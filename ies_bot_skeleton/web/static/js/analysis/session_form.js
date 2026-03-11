(function () {
  function setupStrategySummary() {
    const select = document.getElementById("sessionStrategySelect");
    const cfg = Array.isArray(window.IES_SESSION_STRATEGIES) ? window.IES_SESSION_STRATEGIES : [];
    const labelEl = document.getElementById("strategySummaryLabel");
    const textEl = document.getElementById("strategySummaryText");
    const hintEl = document.getElementById("strategySummaryHint");

    if (!select || !labelEl || !textEl || !hintEl) return;

    function sync() {
      const current = cfg.find((row) => row.code === select.value) || cfg[0];
      if (!current) return;
      labelEl.textContent = current.label || select.value;
      textEl.textContent = current.summary || "";
      hintEl.textContent = current.when_to_use || "";
    }

    sync();
    select.addEventListener("change", sync);
  }

  window.addEventListener("DOMContentLoaded", setupStrategySummary);
})();
