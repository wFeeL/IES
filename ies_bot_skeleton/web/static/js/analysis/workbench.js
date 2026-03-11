(function () {
  function syncModePanels() {
    const select = document.getElementById("analysisModeSelect");
    if (!select) return;
    const mode = String(select.value || "no_forecast");
    document.querySelectorAll("[data-analysis-panel]").forEach((el) => {
      el.classList.toggle("is-hidden", el.getAttribute("data-analysis-panel") !== mode);
    });
  }

  window.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("analysisModeSelect");
    syncModePanels();
    select?.addEventListener("change", syncModePanels);
  });
})();
