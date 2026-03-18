(function () {
  const KEY = "ies-theme";
  const LEGACY_KEYS = ["theme", "ies-ui-theme", "ies_theme"];

  function normalizeTheme(value) {
    return value === "dark" || value === "light" ? value : null;
  }

  function readSavedTheme() {
    const current = normalizeTheme(localStorage.getItem(KEY));
    if (current) {
      return current;
    }
    for (const legacyKey of LEGACY_KEYS) {
      const legacy = normalizeTheme(localStorage.getItem(legacyKey));
      if (!legacy) {
        continue;
      }
      localStorage.setItem(KEY, legacy);
      return legacy;
    }
    return null;
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

  const saved = readSavedTheme();
  const initialTheme = saved || "light";
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
