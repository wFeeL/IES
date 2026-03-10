(function () {
  const KEY = "ies-theme";

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
  }

  const saved = localStorage.getItem(KEY);
  if (saved) {
    apply(saved);
  }

  window.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("themeToggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const now = document.documentElement.getAttribute("data-theme") || "light";
      const next = now === "dark" ? "light" : "dark";
      apply(next);
      localStorage.setItem(KEY, next);
    });
  });
})();
