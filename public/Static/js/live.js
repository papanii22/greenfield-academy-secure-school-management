// Live background refresh.
//
// Polls /live/state (every 5 seconds; every second on Messages, set by the
// page's data-live-interval) for unread counts and a version string that changes
// whenever data the signed-in user can see changes. Badges update at once.
// When the version changes, the current page is fetched in the background and
// every element marked data-live-region="<name>" is swapped for its fresh
// copy, so nobody has to reload.
//
// A region is left alone while someone is using it: focus inside it, text
// typed into one of its fields, or a menu (<details>) open in it. It is
// swapped on a later poll instead.
//
// Background requests carry X-Greenfield-Live: 1, which the server uses to
// leave the session expiry untouched, so an unattended screen still signs out
// after the idle timeout.
(() => {
  const body = document.body;
  const stateUrl = body.dataset.liveUrl;
  if (!stateUrl || !window.fetch || !window.DOMParser) return;

  const HEADERS = { "X-Greenfield-Live": "1", Accept: "application/json" };
  // Seconds between checks, set per page; never faster than once a second.
  const baseInterval = Math.max(1, Number(body.dataset.liveInterval) || 5) * 1000;
  const maxInterval = 5 * 60 * 1000;
  let interval = baseInterval;
  let version = null;
  let timer = null;
  let busy = false;
  let stopped = false;

  function setCount(name, value) {
    document.querySelectorAll(`[data-live-count="${name}"]`).forEach((badge) => {
      const count = Number(value) || 0;
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = count === 0;
      const host = badge.closest("[data-live-label]");
      if (host) {
        const label = host.dataset.liveLabel;
        const suffix = host.dataset.liveSuffix || "unread";
        host.setAttribute("aria-label", count ? `${label}, ${count} ${suffix}` : label);
      }
    });
  }

  function isInUse(region) {
    if (region.contains(document.activeElement) && document.activeElement !== document.body) {
      return true;
    }
    if (region.querySelector("details[open]")) return true;
    return [...region.querySelectorAll("input, textarea, select")].some((field) => {
      if (field.type === "hidden") return false;
      if (field instanceof HTMLSelectElement) {
        return [...field.options].some((option) => option.selected !== option.defaultSelected);
      }
      if (field.type === "checkbox" || field.type === "radio") {
        return field.checked !== field.defaultChecked;
      }
      return field.value !== field.defaultValue;
    });
  }

  function nearBottom(element) {
    return element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  }

  // Ask the page to start over: the session ended or access changed.
  function reloadForSignIn() {
    stopped = true;
    window.location.reload();
  }

  async function refreshRegions() {
    const response = await fetch(window.location.href, {
      headers: { "X-Greenfield-Live": "1", Accept: "text/html" },
      credentials: "same-origin",
      cache: "no-store",
      redirect: "follow",
    });
    if (response.redirected || !response.ok) {
      // Signed out, or this page is gone: let a real navigation decide.
      if (response.redirected || response.status === 401 || response.status === 403) {
        reloadForSignIn();
      }
      return false;
    }
    const fresh = new DOMParser().parseFromString(await response.text(), "text/html");
    let pending = false;
    const changed = [];
    document.querySelectorAll("[data-live-region]").forEach((region) => {
      const name = region.dataset.liveRegion;
      const replacement = fresh.querySelector(`[data-live-region="${CSS.escape(name)}"]`);
      if (!replacement || replacement.innerHTML === region.innerHTML) return;
      if (isInUse(region)) {
        pending = true;
        return;
      }
      // Keep a scrolled list where the reader left it, and a chat at its end.
      const scroller = region.querySelector("[data-chat-thread]") || region;
      const stickToEnd = scroller.matches("[data-chat-thread]") && nearBottom(scroller);
      const scrollTop = scroller.scrollTop;
      const imported = document.importNode(replacement, true);
      region.replaceWith(imported);
      const newScroller = imported.querySelector("[data-chat-thread]") || imported;
      newScroller.scrollTop = stickToEnd ? newScroller.scrollHeight : scrollTop;
      changed.push(imported);
    });
    if (changed.length) {
      document.dispatchEvent(new CustomEvent("greenfield:live-updated", {
        detail: { regions: changed },
      }));
    }
    // A region that was in use is retried on the next poll.
    return !pending;
  }

  async function poll() {
    if (busy || stopped || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(stateUrl, {
        headers: HEADERS,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "manual",
      });
      // A redirect here means the session ended (redirects go to the login page).
      if (response.type === "opaqueredirect" || response.status === 401) {
        reloadForSignIn();
        return;
      }
      if (response.status === 429 || !response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      setCount("messages", state.messages);
      setCount("notices", state.notices);
      if (state.version && version !== null && state.version !== version) {
        const settled = await refreshRegions();
        if (settled) version = state.version;
      } else if (state.version) {
        version = state.version;
      }
      interval = baseInterval;
    } catch {
      // Network trouble or rate limited: back off, then try again.
      interval = Math.min(interval * 2, maxInterval);
    } finally {
      busy = false;
      schedule();
    }
  }

  function schedule() {
    window.clearTimeout(timer);
    if (!stopped && !document.hidden) timer = window.setTimeout(poll, interval);
  }

  // Check straight away when the tab comes back; pause while it is hidden.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(timer);
    } else {
      interval = baseInterval;
      poll();
    }
  });
  window.addEventListener("pagehide", () => {
    stopped = true;
    window.clearTimeout(timer);
  });

  // First call only records the version the page was rendered at.
  poll();
})();
