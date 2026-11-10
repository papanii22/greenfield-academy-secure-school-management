(() => {
  const toggle = document.querySelector("[data-theme-toggle]");

  function updateToggle(theme) {
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", String(theme === "light"));
    toggle.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
    );
  }

  function setTheme(theme, save = true) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "light" ? "#f2f8f8" : "#0b1417");
    updateToggle(theme);
    if (save) localStorage.setItem("greenfield-theme", theme);
    document.dispatchEvent(new CustomEvent("greenfield:theme-change", { detail: { theme } }));
  }

  const currentTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  updateToggle(currentTheme);
  // Follow the operating system until the person makes an explicit choice.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
    if (localStorage.getItem("greenfield-theme")) return;
    setTheme(event.matches ? "dark" : "light", false);
  });
  toggle?.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
  });
})();
