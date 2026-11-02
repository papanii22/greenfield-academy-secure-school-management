(() => {
  const progress = document.querySelector(".top-progress");
  const loader = document.querySelector(".branded-loader");
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
    startProgress();
    form.setAttribute("aria-busy", "true");
    const submitter = event.submitter;
    if (!(submitter instanceof HTMLButtonElement)) return;
    submitter.disabled = true;
    submitter.classList.add("is-submitting");
    const spinner = document.createElement("span");
    spinner.className = "button-spinner";
    spinner.setAttribute("aria-hidden", "true");
    submitter.append(spinner);
  });

  window.addEventListener("pageshow", () => {
    progress?.classList.remove("is-active");
    progress?.classList.add("is-complete");
    document.body.classList.remove("is-navigating");
  });

  document.addEventListener("DOMContentLoaded", () => {
    const firstPageLoad = !sessionStorage.getItem("greenfield-first-load");
    const afterLogin = document.body.dataset.showLoader === "true";
    sessionStorage.setItem("greenfield-first-load", "true");
    if (!firstPageLoad && !afterLogin) return;
    loader?.classList.add("is-visible");
    const hideLoader = () => loader?.classList.remove("is-visible");
    window.addEventListener("load", hideLoader, { once: true });
    window.setTimeout(hideLoader, 600);
  }, { once: true });
})();
