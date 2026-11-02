(() => {
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const menuClose = document.querySelector("[data-menu-close]");
  const userMenuToggle = document.querySelector("[data-user-menu-toggle]");
  const userMenu = document.querySelector("[data-user-menu]");

  function closeMenu() {
    document.body.classList.remove("menu-open");
    menuToggle?.setAttribute("aria-expanded", "false");
  }

  menuToggle?.addEventListener("click", () => {
    const isOpen = document.body.classList.toggle("menu-open");
    menuToggle.setAttribute("aria-expanded", String(isOpen));
  });
  menuClose?.addEventListener("click", closeMenu);
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
      userMenu?.querySelector("button")?.focus();
    } else {
      closeUserMenu();
    }
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element) || !event.target.closest(".user-menu")) {
      closeUserMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu();
      closeUserMenu(true);
    }
  });
  document.querySelectorAll(".side-nav [data-nav-link]").forEach((link) => {
    link.addEventListener("click", closeMenu);
  });

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

  document.querySelectorAll("select[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => select.form?.requestSubmit());
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

  const activeLink = document.querySelector(".nav-link.is-active");
  const indicator = document.querySelector(".nav-indicator");
  function positionIndicator() {
    if (!activeLink || !indicator) return;
    indicator.style.height = `${activeLink.offsetHeight}px`;
    indicator.style.transform = `translateY(${activeLink.offsetTop}px)`;
  }
  positionIndicator();
  window.addEventListener("resize", positionIndicator);
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("pointerenter", () => {
      if (!indicator) return;
      indicator.style.height = `${link.offsetHeight}px`;
      indicator.style.transform = `translateY(${link.offsetTop}px)`;
    });
    link.addEventListener("pointerleave", positionIndicator);
  });
})();
