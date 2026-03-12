(function () {
  function errorMessage(data) {
    return data?.error?.message || data?.error || "Неизвестная ошибка";
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    const wrap = document.createElement("div");
    wrap.className = "risk-block";
    wrap.innerHTML = `
      <strong>${title}</strong>
      <div class="mt-2">Периоды: ${payload.count ?? 0}</div>
      <div>Диапазон: ${payload.tick_from ?? "—"}–${payload.tick_to ?? "—"}</div>
      <div>Средний ветер: ${payload.avg_wind ?? "n/a"}</div>
      <div>Средняя освещенность: ${payload.avg_illumination ?? "n/a"}</div>
      <div>Ряды нагрузки: ${(payload.load_series || []).join(", ") || "не найдены"}</div>
    `;
    target.prepend(wrap);
  }

  window.addEventListener("DOMContentLoaded", () => {
    const out = document.getElementById("forecastOut");
    const form = document.getElementById("forecastForm");
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const formData = new FormData(form);
      out.innerHTML = '<div class="risk-block">Загрузка прогноза...</div>';
      const res = await fetch("/api/forecast/upload", {method: "POST", body: formData});
      const data = await res.json();
      out.innerHTML = "";
      if (!data.ok) {
        renderSummary(out, "Ошибка загрузки", {
          count: 0,
          load_series: [],
          tick_from: "—",
          tick_to: "—",
          avg_wind: errorMessage(data),
        });
        return;
      }
      renderSummary(out, "Прогноз загружен", data.summary || {});
      window.setTimeout(() => window.location.reload(), 600);
    });

    document.querySelectorAll(".analyzeBtn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        out.innerHTML = '<div class="risk-block">Собираю диагностику...</div>';
        const res = await fetch(`/api/forecast/${btn.dataset.forecastId}/analyze`, {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-CSRFToken": (window.IES_FORECAST_CENTER || {}).csrfToken || ""},
          body: JSON.stringify({})
        });
        const data = await res.json();
        out.innerHTML = "";
        if (!data.ok) {
          renderSummary(out, "Ошибка", {avg_wind: errorMessage(data), load_series: []});
          return;
        }
        renderSummary(out, `Диагностика прогноза #${btn.dataset.forecastId}`, data.item || {});
      });
    });
  });
})();
