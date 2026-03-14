(function () {
  const KEY = "ies-theme";

  function preferredTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    return theme;
  }

  function syncA11y(btn, theme) {
    if (!btn) return;
    const isDark = theme === "dark";
    btn.setAttribute("aria-pressed", isDark ? "true" : "false");
    btn.setAttribute("aria-label", isDark ? "Переключить на светлую тему" : "Переключить на тёмную тему");
    btn.title = isDark ? "Сейчас тёмная тема" : "Сейчас светлая тема";
    btn.dataset.themeState = theme;
  }

  const saved = localStorage.getItem(KEY);
  const initialTheme = saved === "dark" || saved === "light" ? saved : preferredTheme();
  let currentTheme = apply(initialTheme);

  window.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("themeToggle");
    syncA11y(btn, currentTheme);
    if (!btn) return;
    btn.addEventListener("click", () => {
      const now = document.documentElement.getAttribute("data-theme") || "light";
      const next = now === "dark" ? "light" : "dark";
      currentTheme = apply(next);
      localStorage.setItem(KEY, next);
      syncA11y(btn, currentTheme);
    });
  });
})();
