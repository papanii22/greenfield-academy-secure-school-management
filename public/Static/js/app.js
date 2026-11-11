(() => {
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const menuClose = document.querySelector("[data-menu-close]");
  const userMenuToggle = document.querySelector("[data-user-menu-toggle]");
  const userMenu = document.querySelector("[data-user-menu]");

  const sidebar = document.querySelector("#app-sidebar");
  const drawerQuery = window.matchMedia("(max-width: 640px)");

  function closeMenu(restoreFocus = false) {
    const wasOpen = document.body.classList.contains("menu-open");
    document.body.classList.remove("menu-open");
    menuToggle?.setAttribute("aria-expanded", "false");
    menuToggle?.setAttribute("aria-label", "Open navigation");
    if (wasOpen && restoreFocus) menuToggle?.focus();
  }

  menuToggle?.addEventListener("click", () => {
    const isOpen = document.body.classList.toggle("menu-open");
    menuToggle.setAttribute("aria-expanded", String(isOpen));
    menuToggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
    if (isOpen) {
      (sidebar?.querySelector(".nav-link.is-active") || sidebar?.querySelector(".nav-link"))?.focus();
    }
  });
  menuClose?.addEventListener("click", () => closeMenu(true));
  drawerQuery.addEventListener("change", (event) => {
    if (!event.matches) closeMenu();
  });
  // Keep keyboard focus inside the open drawer.
  sidebar?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || !document.body.classList.contains("menu-open")) return;
    const focusable = [...sidebar.querySelectorAll("a[href], button:not([disabled])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  function closeUserMenu(restoreFocus = false) {
    userMenu?.setAttribute("hidden", "");
    userMenuToggle?.setAttribute("aria-expanded", "false");
    if (restoreFocus) userMenuToggle?.focus();
  }

  userMenuToggle?.addEventListener("click", () => {
    const shouldOpen = userMenuToggle.getAttribute("aria-expanded") !== "true";
    userMenuToggle.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) {
      userMenu?.removeAttribute("hidden");
      userMenu?.querySelector('[role="menuitem"]')?.focus();
    } else {
      closeUserMenu();
    }
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest(".user-menu")) {
      closeUserMenu();
    }
  });
  userMenu?.addEventListener("keydown", (event) => {
    const items = [...userMenu.querySelectorAll('[role="menuitem"]')];
    const index = items.indexOf(document.activeElement);
    let next = null;
    if (event.key === "ArrowDown") next = items[(index + 1) % items.length];
    else if (event.key === "ArrowUp") next = items[(index - 1 + items.length) % items.length];
    else if (event.key === "Home") next = items[0];
    else if (event.key === "End") next = items[items.length - 1];
    if (!next) return;
    event.preventDefault();
    next.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu(true);
      if (userMenuToggle?.getAttribute("aria-expanded") === "true") closeUserMenu(true);
    }
  });
  document.querySelectorAll(".side-nav [data-nav-link]").forEach((link) => {
    link.addEventListener("click", () => closeMenu());
  });

  // Tie each server-side validation message to its control for screen readers.
  document.querySelectorAll(".form-field").forEach((field, fieldIndex) => {
    const errors = field.querySelectorAll(".field-error");
    const control = field.querySelector("input, select, textarea");
    if (!errors.length || !control) return;
    const ids = [...errors].map((error, errorIndex) => {
      if (!error.id) error.id = "field-error-" + fieldIndex + "-" + errorIndex;
      return error.id;
    });
    control.setAttribute("aria-invalid", "true");
    control.setAttribute("aria-describedby", ids.join(" "));
  });

  // Wide tables scroll sideways on small screens; make that reachable by keyboard.
  function markScrollableTables() {
    document.querySelectorAll(".table-wrap").forEach((wrap) => {
      if (wrap.scrollWidth > wrap.clientWidth + 1) {
        wrap.setAttribute("tabindex", "0");
        wrap.setAttribute("role", "region");
        if (!wrap.hasAttribute("aria-label")) {
          wrap.setAttribute("aria-label", "Scrollable table");
        }
      } else {
        wrap.removeAttribute("tabindex");
      }
    });
  }
  markScrollableTables();
  window.addEventListener("resize", markScrollableTables, { passive: true });
  // "toggle" does not bubble; listening in the capture phase catches every <details>.
  document.addEventListener("toggle", markScrollableTables, true);
  document.addEventListener("greenfield:live-updated", markScrollableTables);

  const accountRole = document.querySelector("#account-role");
  function updateAccountFields() {
    const role = accountRole?.value;
    const studentFields = document.querySelector("[data-student-field]");
    const teacherFields = document.querySelector("[data-teacher-field]");
    if (studentFields) studentFields.hidden = role !== "student";
    if (teacherFields) teacherFields.hidden = role !== "teacher";
  }
  accountRole?.addEventListener("change", updateAccountFields);
  updateAccountFields();

  // Delegated, so controls swapped in by the live refresh work too.
  document.addEventListener("change", (event) => {
    const control = event.target;
    if (control instanceof Element && control.matches("select[data-auto-submit], input[data-auto-submit]")) {
      control.form?.requestSubmit();
    }
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.closest(".password-control")?.querySelector("input");
      if (!input) return;
      const showPassword = input.type === "password";
      input.type = showPassword ? "text" : "password";
      button.setAttribute("aria-pressed", String(showPassword));
      button.setAttribute("aria-label", showPassword ? "Hide password" : "Show password");
      input.focus();
    });
  });

  // Chat: newest message in view, Enter sends and Shift+Enter adds a line.
  document.querySelectorAll("[data-chat-thread]").forEach((thread) => {
    thread.scrollTop = thread.scrollHeight;
  });
  document.addEventListener("keydown", (event) => {
    const area = event.target;
    if (!(area instanceof HTMLTextAreaElement) || !area.matches("[data-enter-submit]")) return;
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (area.value.trim()) area.form?.requestSubmit();
  });

  // Small pop-over forms built on <details> close on outside click or Escape.
  const openPopovers = () => document.querySelectorAll("details.row-popover[open]");
  document.addEventListener("click", (event) => {
    openPopovers().forEach((popover) => {
      if (!popover.contains(event.target)) popover.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    openPopovers().forEach((popover) => {
      if (popover.open) {
        popover.open = false;
        popover.querySelector("summary")?.focus();
      }
    });
  });

  const activeLink = document.querySelector(".nav-link.is-active");
  const indicator = document.querySelector(".nav-indicator");
  function positionIndicator() {
    if (!indicator) return;
    indicator.hidden = !activeLink;
    if (!activeLink) return;
    indicator.style.height = `${activeLink.offsetHeight}px`;
    indicator.style.transform = `translateY(${activeLink.offsetTop}px)`;
  }
  positionIndicator();
  window.addEventListener("resize", positionIndicator);
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("pointerenter", () => {
      if (!indicator) return;
      indicator.hidden = false;
      indicator.style.height = `${link.offsetHeight}px`;
      indicator.style.transform = `translateY(${link.offsetTop}px)`;
    });
    link.addEventListener("pointerleave", positionIndicator);
  });
})();
