(() => {
  const toggles = document.querySelectorAll("[data-theme-toggle]");

  function updateToggles(theme) {
    toggles.forEach((toggle) => {
      toggle.setAttribute("aria-pressed", String(theme === "light"));
      // Icon-only toggles need a name; labelled buttons keep their own text.
      if (!toggle.textContent.trim()) {
        toggle.setAttribute(
          "aria-label",
          theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
        );
      }
    });
  }

  function setTheme(theme, save = true) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "light" ? "#f7f8fa" : "#14161c");
    updateToggles(theme);
    if (save) {
      try {
        localStorage.setItem("greenfield-theme", theme);
      } catch {
        // Storage can be blocked; the theme still applies for this page.
      }
    }
    document.dispatchEvent(new CustomEvent("greenfield:theme-change", { detail: { theme } }));
  }

  const currentTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  updateToggles(currentTheme);
  toggles.forEach((toggle) => {
    toggle.addEventListener("click", () => {
      const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      setTheme(nextTheme);
    });
  });
})();
