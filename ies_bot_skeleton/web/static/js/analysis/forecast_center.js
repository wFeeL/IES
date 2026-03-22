(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage =
    api.errorMessage || ((data) => data?.error?.message || "Неизвестная ошибка");

  const chartStore = new Map();

  const COLORS = {
    generation: "#2563eb",
    consumption: "#dc2626",
    balance: "#0f766e",
    wind: "#7c3aed",
    windGen: "#0891b2",
    threshold: "#f59e0b",
    zero: "#94a3b8",
    factory: "#2563eb",
    office: "#7c3aed",
    house: "#dc2626",
    hospital: "#0891b2",
    solar: "#f59e0b",
  };

  function formatNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const fixed = number.toFixed(Math.max(0, Number(digits || 0)));
    return fixed.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function normalizeSeries(series) {
    if (!Array.isArray(series)) return [];
    return series.map((value) => {
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    });
  }

  function buildLabels(length, ticks) {
    if (Array.isArray(ticks) && ticks.length === length) {
      return ticks.map((value, index) => {
        const n = Number(value);
        return Number.isFinite(n) ? String(n) : String(index + 1);
      });
    }
    return Array.from({ length }, (_, index) => String(index + 1));
  }

  function destroyChart(container) {
    if (!container) return;
    const prev = chartStore.get(container);
    if (prev) {
      prev.destroy();
      chartStore.delete(container);
    }
  }

  function renderChartCard(target, title, subtitle = "") {
    if (!target) return null;
    target.innerHTML = `
      <article class="card weather-chart-card">
        <p class="section-kicker">${escapeHtml(title)}</p>
        ${subtitle ? `<div class="muted mt-2">${escapeHtml(subtitle)}</div>` : ""}
        <div class="weather-chart-canvas-wrap mt-3">
          <canvas></canvas>
        </div>
      </article>
    `;
    return target.querySelector("canvas");
  }

  function baseChartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            boxWidth: 12,
            usePointStyle: true,
          },
        },
        tooltip: {
          callbacks: {
            label(context) {
              const label = context.dataset?.label || "";
              const value = context.parsed?.y;
              return `${label}: ${formatNum(value, 2)}`;
            },
          },
        },
      },
      elements: {
        point: {
          radius: 0,
          hoverRadius: 3,
        },
        line: {
          tension: 0.28,
          borderWidth: 2.25,
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          title: {
            display: true,
            text: "Такт",
          },
        },
        y: {
          beginAtZero: false,
          ticks: {
            callback(value) {
              return formatNum(value, 1);
            },
          },
        },
      },
    };
  }

  function makeDataset(label, data, color, extra = {}) {
    return {
      label,
      data,
      borderColor: color,
      backgroundColor: color + "22",
      spanGaps: true,
      fill: false,
      ...extra,
    };
  }

  function drawLineChart(container, config) {
    if (!container || typeof Chart === "undefined") return;
    destroyChart(container);

    const labels = buildLabels(config.length, config.ticks);
    const canvas = renderChartCard(container, config.title, config.subtitle);
    if (!canvas) return;

    const chart = new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: config.datasets,
      },
      options: {
        ...baseChartOptions(),
        ...config.options,
      },
    });

    chartStore.set(container, chart);
  }

  function renderGenerationChart(container, analysis) {
    const generation = normalizeSeries(analysis?.series?.total_generation || []);
    const consumption = normalizeSeries(analysis?.series?.total_consumption || []);
    if (!generation.length && !consumption.length) {
      container.innerHTML = `<div class="empty-state">Недостаточно данных для графика генерации и потребления.</div>`;
      return;
    }

    drawLineChart(container, {
      title: "Генерация и потребление",
      subtitle: "Главный график для чтения прогноза",
      length: Math.max(generation.length, consumption.length),
      ticks: analysis?.series?.tick || [],
      datasets: [
        makeDataset("Генерация", generation, COLORS.generation, {
          fill: true,
          backgroundColor: "rgba(37,99,235,0.10)",
        }),
        makeDataset("Потребление", consumption, COLORS.consumption, {
          fill: false,
        }),
      ],
      options: {
        scales: {
          ...baseChartOptions().scales,
          y: {
            ...baseChartOptions().scales.y,
            beginAtZero: true,
            title: {
              display: true,
              text: "Мощность / нагрузка",
            },
          },
        },
      },
    });
  }

  function renderBalanceChart(container, analysis) {
    const balance = normalizeSeries(analysis?.series?.balance || []);
    if (!balance.length) {
      container.innerHTML = `<div class="empty-state">Недостаточно данных для графика баланса.</div>`;
      return;
    }

    const zeroLine = new Array(balance.length).fill(0);

    drawLineChart(container, {
      title: "Баланс",
      subtitle: "Выше нуля — профицит, ниже нуля — дефицит",
      length: balance.length,
      ticks: analysis?.series?.tick || [],
      datasets: [
        makeDataset("Баланс", balance, COLORS.balance, {
          fill: {
            target: "origin",
            above: "rgba(15,118,110,0.12)",
            below: "rgba(220,38,38,0.10)",
          },
        }),
        makeDataset("Нулевая линия", zeroLine, COLORS.zero, {
          borderDash: [6, 6],
          borderWidth: 1.5,
        }),
      ],
      options: {
        scales: {
          ...baseChartOptions().scales,
          y: {
            ...baseChartOptions().scales.y,
            title: {
              display: true,
              text: "Баланс",
            },
          },
        },
      },
    });
  }

  function renderWindChart(container, analysis) {
    const windAvg = normalizeSeries(analysis?.series?.wind_avg || []);
    const windGen = normalizeSeries(analysis?.series?.wind_gen || []);
    if (!windAvg.length && !windGen.length) {
      container.innerHTML = `<div class="empty-state">Недостаточно данных для графика ветра.</div>`;
      return;
    }

    const threshold = new Array(Math.max(windAvg.length, windGen.length)).fill(7);

    drawLineChart(container, {
      title: "Ветер",
      subtitle: "С порогом отключения ветряка",
      length: Math.max(windAvg.length, windGen.length),
      ticks: analysis?.series?.tick || [],
      datasets: [
        makeDataset("Средний ветер", windAvg, COLORS.wind),
        makeDataset("Генерация ветра", windGen, COLORS.windGen),
        makeDataset("Порог отключения", threshold, COLORS.threshold, {
          borderDash: [6, 6],
          borderWidth: 1.75,
        }),
      ],
      options: {
        scales: {
          ...baseChartOptions().scales,
          y: {
            ...baseChartOptions().scales.y,
            beginAtZero: true,
            title: {
              display: true,
              text: "Ветер / генерация",
            },
          },
        },
      },
    });
  }

  function renderConsumptionChart(container, analysis) {
    const total = normalizeSeries(analysis?.series?.total_consumption || []);
    const bucket = analysis?.series?.category_consumption || {};

    const factory = normalizeSeries(bucket.factory || []);
    const office = normalizeSeries(bucket.office || []);
    const house = normalizeSeries(bucket.house_load || bucket.house || []);
    const hospital = normalizeSeries(bucket.hospital || []);

    const hasBreakdown =
      factory.length || office.length || house.length || hospital.length;

    if (!total.length && !hasBreakdown) {
      container.innerHTML = `<div class="empty-state">Недостаточно данных для графика потребления.</div>`;
      return;
    }

    const datasets = [];
    if (factory.length) datasets.push(makeDataset("Factory", factory, COLORS.factory));
    if (office.length) datasets.push(makeDataset("Office", office, COLORS.office));
    if (house.length) datasets.push(makeDataset("House", house, COLORS.house));
    if (hospital.length) datasets.push(makeDataset("Hospital", hospital, COLORS.hospital));

    datasets.push(
      makeDataset("Суммарное потребление", total, COLORS.consumption, {
        borderWidth: 3,
      })
    );

    drawLineChart(container, {
      title: "Потребление",
      subtitle: hasBreakdown
        ? "С разбивкой по доступным категориям"
        : "Только суммарное canonical-потребление",
      length: Math.max(
        total.length,
        factory.length,
        office.length,
        house.length,
        hospital.length
      ),
      ticks: analysis?.series?.tick || [],
      datasets,
      options: {
        scales: {
          ...baseChartOptions().scales,
          y: {
            ...baseChartOptions().scales.y,
            beginAtZero: true,
            title: {
              display: true,
              text: "Потребление",
            },
          },
        },
      },
    });
  }

  function renderWeatherAnalysis(target, analysis) {
    if (!target) return;
    if (!analysis || !analysis.series) {
      target.innerHTML = `<div class="empty-state">Weather analysis пока недоступен.</div>`;
      return;
    }

    const kpis = analysis.kpis || {};
    target.innerHTML = `
      <div class="stack gap-4">
        <div class="weather-kpi-grid">
          <div class="metric-card">
            <span class="metric-label">Средняя генерация</span>
            <strong>${escapeHtml(formatNum(kpis.avg_generation, 2))}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">Среднее потребление</span>
            <strong>${escapeHtml(formatNum(kpis.avg_consumption, 2))}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">Средний баланс</span>
            <strong>${escapeHtml(formatNum(kpis.avg_balance, 2))}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">Тактов с дефицитом</span>
            <strong>${escapeHtml(formatNum(kpis.deficit_count, 0))}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">Тактов с профицитом</span>
            <strong>${escapeHtml(formatNum(kpis.surplus_count, 0))}</strong>
          </div>
          <div class="metric-card">
            <span class="metric-label">Отключений ветряка</span>
            <strong>${escapeHtml(formatNum(kpis.wind_off_count, 0))}</strong>
          </div>
        </div>

        <div class="weather-charts-grid">
          <div data-chart-generation></div>
          <div data-chart-balance></div>
          <div data-chart-wind></div>
          <div data-chart-consumption></div>
        </div>

        <div class="weather-insight-list">
          ${
            (Array.isArray(analysis.insights) ? analysis.insights : []).length
              ? analysis.insights
                  .map(
                    (item) =>
                      `<article class="card weather-insight-card">${escapeHtml(item)}</article>`
                  )
                  .join("")
              : `<article class="card weather-insight-card">Пока нет выводов по этому прогнозу.</article>`
          }
        </div>
      </div>
    `;

    renderGenerationChart(target.querySelector("[data-chart-generation]"), analysis);
    renderBalanceChart(target.querySelector("[data-chart-balance]"), analysis);
    renderWindChart(target.querySelector("[data-chart-wind]"), analysis);
    renderConsumptionChart(target.querySelector("[data-chart-consumption]"), analysis);
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    target.innerHTML = `
      <div class="risk-block">
        <strong>${escapeHtml(title)}</strong>
        <div class="mt-2">${escapeHtml(payload.quality?.text || "")}</div>
      </div>
      <div class="mt-4">
        <p class="section-kicker">Анализ прогноза погоды</p>
        <div data-weather-analysis class="mt-2"></div>
      </div>
    `;
    renderWeatherAnalysis(
      target.querySelector("[data-weather-analysis]"),
      payload.weather_analysis || null
    );
  }

  async function loadForecastDetails(forecastId) {
    return apiFetchJson(`/api/forecast/${forecastId}`, {
      headers: {
        "X-CSRFToken":
          (window.IES_FORECAST_CENTER || {}).csrfToken ||
          window.IES_CSRF_TOKEN ||
          "",
      },
    });
  }

  async function runDiagnostics(forecastId, withChart) {
    const out = document.getElementById("forecastOut");
    const chart = document.getElementById("forecastChart");

    if (!forecastId) {
      renderSummary(out, "Диагностика", {
        quality: { text: "Сначала выберите прогноз." },
      });
      return;
    }

    out.innerHTML = `<div class="risk-block">Собираю диагностику...</div>`;
    if (withChart) chart.innerHTML = `<div class="muted">Готовлю график...</div>`;

    const data = await loadForecastDetails(forecastId);
    if (!data.ok) {
      renderSummary(out, "Ошибка", { quality: { text: errorMessage(data) } });
      if (withChart) {
        chart.innerHTML = `<div class="muted">${escapeHtml(errorMessage(data))}</div>`;
      }
      return;
    }

    renderSummary(out, `Диагностика прогноза #${forecastId}`, data.item?.summary || {});

    if (withChart) {
      const periods = Array.isArray(data.item?.periods) ? data.item.periods : [];
      const wind = periods.map((row) => {
        const n = Number(row.wind);
        return Number.isFinite(n) ? n : null;
      });

      drawLineChart(chart, {
        title: "Предпросмотр: ветер",
        subtitle: "Быстрый просмотр загруженного прогноза",
        length: wind.length,
        ticks: periods.map((row, index) => {
          const n = Number(row.tick);
          return Number.isFinite(n) ? n : index + 1;
        }),
        datasets: [makeDataset("Wind", wind, COLORS.wind)],
        options: {
          scales: {
            ...baseChartOptions().scales,
            y: {
              ...baseChartOptions().scales.y,
              beginAtZero: true,
            },
          },
        },
      });
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const out = document.getElementById("forecastOut");
    const form = document.getElementById("forecastForm");
    const activeWeatherAnalysis =
      (window.IES_FORECAST_CENTER || {}).activeWeatherAnalysis || null;

    renderWeatherAnalysis(
      document.getElementById("activeForecastWeatherAnalysis"),
      activeWeatherAnalysis
    );

    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      out.innerHTML = `<div class="risk-block">Загрузка прогноза...</div>`;

      const data = await apiFetchJson("/api/forecast/upload", {
        method: "POST",
        body: formData,
      });

      if (!data.ok) {
        renderSummary(out, "Ошибка загрузки", {
          quality: { text: errorMessage(data) },
        });
        return;
      }

      renderSummary(out, "Прогноз загружен", data.summary || {});
      window.setTimeout(() => window.location.reload(), 700);
    });

    document.querySelectorAll(".analyzeBtn").forEach((button) => {
      button.addEventListener("click", async () => {
        await runDiagnostics(Number(button.dataset.forecastId || 0), false);
      });
    });

    document.querySelectorAll(".chartBtn").forEach((button) => {
      button.addEventListener("click", async () => {
        await runDiagnostics(Number(button.dataset.forecastId || 0), true);
      });
    });
  });
})();