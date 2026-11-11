// Form handling shared by every page: the confirmation dialog for
// destructive actions, and a guard against sending a form twice.
(() => {
  const confirmDialog = document.querySelector("[data-confirm-dialog]");
  const confirmMessage = confirmDialog?.querySelector("[data-confirm-message]");
  let pendingForm = null;

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
    form.setAttribute("aria-busy", "true");
    // A second click while the first submission is on its way would send it again.
    const submitter = event.submitter instanceof HTMLButtonElement
      ? event.submitter
      : form.querySelector('button[type="submit"]');
    if (submitter instanceof HTMLButtonElement) submitter.disabled = true;
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

  // Returning with the back button restores the page as it was left:
  // re-enable anything disabled during a submission.
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.querySelectorAll("form[aria-busy='true']").forEach((form) => {
      form.removeAttribute("aria-busy");
      form.querySelectorAll("button[disabled]").forEach((button) => {
        button.disabled = false;
      });
    });
  });
})();
