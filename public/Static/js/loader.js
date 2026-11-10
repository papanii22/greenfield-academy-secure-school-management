(() => {
  const progress = document.querySelector(".top-progress");
  const loader = document.querySelector(".branded-loader");
  const confirmDialog = document.querySelector("[data-confirm-dialog]");
  const confirmMessage = confirmDialog?.querySelector("[data-confirm-message]");
  let pendingForm = null;
  let navigating = false;
  const supportsPageTransitions =
    typeof CSS !== "undefined" &&
    CSS.supports("view-transition-name: app-content") &&
    "onpageswap" in window;

  function startProgress() {
    if (!progress) return;
    progress.classList.remove("is-complete");
    progress.classList.add("is-active");
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    if (link.hasAttribute("download")) return;

    const destination = new URL(link.href, window.location.href);
    if (destination.origin !== window.location.origin) return;
    if (destination.href === window.location.href) return;

    startProgress();
    if (!supportsPageTransitions) {
      event.preventDefault();
      if (navigating) return;
      navigating = true;
      document.body.classList.add("is-navigating");
      window.setTimeout(() => window.location.assign(destination.href), 100);
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.confirm && form.dataset.confirmed !== "true") {
      event.preventDefault();
      if (!confirmDialog || typeof confirmDialog.showModal !== "function") {
        if (window.confirm(form.dataset.confirm)) {
          form.dataset.confirmed = "true";
          form.requestSubmit();
        }
        return;
      }
      pendingForm = form;
      if (confirmMessage) confirmMessage.textContent = form.dataset.confirm;
      confirmDialog.showModal();
      return;
    }
    delete form.dataset.confirmed;
    startProgress();
    form.setAttribute("aria-busy", "true");
    const submitter = event.submitter instanceof HTMLButtonElement
      ? event.submitter
      : form.querySelector('button[type="submit"]');
    if (!(submitter instanceof HTMLButtonElement)) return;
    submitter.disabled = true;
    submitter.classList.add("is-submitting");
    const spinner = document.createElement("span");
    spinner.className = "button-spinner";
    spinner.setAttribute("aria-hidden", "true");
    submitter.append(spinner);
  });

  confirmDialog?.querySelectorAll("[data-confirm-cancel]").forEach((button) => {
    button.addEventListener("click", () => confirmDialog.close());
  });
  confirmDialog?.querySelector("[data-confirm-accept]")?.addEventListener("click", () => {
    if (!pendingForm) return;
    const form = pendingForm;
    pendingForm = null;
    form.dataset.confirmed = "true";
    confirmDialog.close();
    form.requestSubmit();
  });
  confirmDialog?.addEventListener("close", () => {
    pendingForm = null;
  });

  window.addEventListener("pageshow", () => {
    progress?.classList.remove("is-active");
    progress?.classList.add("is-complete");
    document.body.classList.remove("is-navigating");
  });

  document.addEventListener("DOMContentLoaded", () => {
    // Only the post-sign-in handoff gets the branded loader. Covering an
    // already-rendered page on every first visit just delays the content.
    if (document.body.dataset.showLoader !== "true") return;
    loader?.classList.add("is-visible");
    const hideLoader = () => loader?.classList.remove("is-visible");
    window.addEventListener("load", hideLoader, { once: true });
    window.setTimeout(hideLoader, 600);
  }, { once: true });
})();
