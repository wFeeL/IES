(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage =
    api.errorMessage || ((data) => data?.error?.message || "Неизвестная ошибка");

  const COLORS = {
    generation: "#1d4ed8",
    generationFill: "rgba(29,78,216,0.12)",
    consumption: "#dc2626",
    consumptionFill: "rgba(220,38,38,0.1)",
    balance: "#0f766e",
    balanceFill: "rgba(15,118,110,0.12)",
    band: "rgba(15,118,110,0.08)",
    wind: "#7c3aed",
    windGen: "#0891b2",
    threshold: "#f59e0b",
    zero: "#94a3b8",
    factory: "#2563eb",
    office: "#8b5cf6",
    house: "#dc2626",
    hospital: "#0891b2",
    solarImproved: "#f59e0b",
    solarSimple: "#f97316",
    solarEast: "#facc15",
    solarWest: "#fb7185",
    total: "#334155",
    illuminationEast: "#facc15",
    illuminationWest: "#f97316",
  };

  const MODE_META = {
    full_nto_2024: {
      label: "Полный NTO-профиль",
      description:
        "Учтены отдельные солнечные направления, диапазон ветра и разбивка нагрузки по категориям.",
    },
    canonical_fallback: {
      label: "Canonical fallback",
      description:
        "Прогноз собран по canonical-факторам и доступным типовым рядам нагрузки.",
    },
    partial: {
      label: "Частичный анализ",
      description:
        "Часть исходных рядов отсутствует, поэтому выводы строятся только по доступным данным.",
    },
  };

  function formatNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const fixed = number.toFixed(Math.max(0, Number(digits || 0)));
    return fixed.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  }

  function formatPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return `${formatNum(number, 1)}%`;
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
        const number = Number(value);
        return Number.isFinite(number) ? String(number) : String(index + 1);
      });
    }
    return Array.from({ length }, (_, index) => String(index + 1));
  }

  function seriesHasValues(series) {
    return normalizeSeries(series).some((value) => value !== null);
  }

  function maxLength(...seriesList) {
    return Math.max(
      0,
      ...seriesList.map((series) => (Array.isArray(series) ? series.length : 0))
    );
  }

  function finiteValues(series) {
    return normalizeSeries(series).filter((value) => value !== null);
  }

  function sampleItems(items, limit) {
    if (!Array.isArray(items) || !items.length) return [];
    return items.filter(Boolean).slice(0, limit);
  }

  function joinList(items) {
    const clean = sampleItems(items, 20);
    return clean.length ? clean.join(", ") : "—";
  }

  function modeMeta(mode) {
    return MODE_META[mode] || MODE_META.partial;
  }

  function toneClass(tone) {
    if (tone === "buy") return "weather-decision-buy";
    if (tone === "watch") return "weather-decision-watch";
    if (tone === "timing") return "weather-decision-timing";
    return "";
  }

  function buildFactPills(items) {
    const clean = sampleItems(items, 6);
    if (!clean.length) return "";
    return `
      <div class="weather-chart-facts mt-3">
        ${clean
          .map(
            (item) =>
              `<span class="weather-chip weather-fact-pill">${escapeHtml(item)}</span>`
          )
          .join("")}
      </div>
    `;
  }

  function buildLegend(items) {
    const clean = sampleItems(items, 8);
    if (!clean.length) return "";
    return `
      <div class="weather-chart-facts mt-3">
        ${clean
          .map(
            (item) => `
              <span class="weather-chip weather-fact-pill">
                <span class="weather-swatch" style="background:${escapeHtml(item.color)}"></span>
                ${escapeHtml(item.label)}
              </span>
            `
          )
          .join("")}
      </div>
    `;
  }

  function buildContinuousSegments(data, project) {
    const segments = [];
    let current = [];
    data.forEach((value, index) => {
      if (value === null) {
        if (current.length) {
          segments.push(current);
          current = [];
        }
        return;
      }
      current.push(project(value, index));
    });
    if (current.length) segments.push(current);
    return segments;
  }

  function svgPathFromPoints(points) {
    if (!points.length) return "";
    return points
      .map((point, index) =>
        `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`
      )
      .join(" ");
  }

  function svgAreaFromPoints(points, baselineY) {
    if (!points.length) return "";
    const path = svgPathFromPoints(points);
    const first = points[0];
    const last = points[points.length - 1];
    return `${path} L${last.x.toFixed(2)},${baselineY.toFixed(2)} L${first.x.toFixed(
      2
    )},${baselineY.toFixed(2)} Z`;
  }

  function svgBandFromSeries(indexes, upper, lower, project) {
    const points = [];
    indexes.forEach((index) => {
      const upperValue = upper[index];
      const lowerValue = lower[index];
      if (upperValue === null || lowerValue === null) return;
      points.push({
        upper: project(upperValue, index),
        lower: project(lowerValue, index),
      });
    });
    if (points.length < 2) return "";
    const upperPath = points
      .map((point, index) =>
        `${index === 0 ? "M" : "L"}${point.upper.x.toFixed(2)},${point.upper.y.toFixed(2)}`
      )
      .join(" ");
    const lowerPath = points
      .slice()
      .reverse()
      .map((point) => `L${point.lower.x.toFixed(2)},${point.lower.y.toFixed(2)}`)
      .join(" ");
    return `${upperPath} ${lowerPath} Z`;
  }

  function numericTicks(min, max, count = 4) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (Math.abs(max - min) < 1e-9) {
      const value = min;
      return [value - 1, value, value + 1];
    }
    return Array.from({ length: count + 1 }, (_, index) => min + ((max - min) * index) / count);
  }

  function chartExtent(datasets, bands, includeZero) {
    const values = [];
    datasets.forEach((dataset) => {
      normalizeSeries(dataset.data).forEach((value) => {
        if (value !== null) values.push(value);
      });
    });
    bands.forEach((band) => {
      normalizeSeries(band.lower).forEach((value) => {
        if (value !== null) values.push(value);
      });
      normalizeSeries(band.upper).forEach((value) => {
        if (value !== null) values.push(value);
      });
    });
    if (!values.length) return { min: 0, max: 1 };
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (includeZero) {
      min = Math.min(min, 0);
      max = Math.max(max, 0);
    }
    if (Math.abs(max - min) < 1e-9) {
      const pad = Math.max(Math.abs(max) * 0.2, 1);
      min -= pad;
      max += pad;
    } else {
      const pad = (max - min) * 0.12;
      min -= pad;
      max += pad;
    }
    return { min, max };
  }

  function chartEmptyCard(title, lead, reason, wide) {
    return `
      <article class="card weather-chart-card ${wide ? "weather-chart-wide" : ""}">
        <p class="section-kicker">${escapeHtml(title)}</p>
        ${lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(lead)}</div>` : ""}
        <div class="empty-state mt-3">${escapeHtml(reason)}</div>
      </article>
    `;
  }

  function renderSvgChart(target, config) {
    if (!target) return;

    const datasets = (config.datasets || [])
      .map((dataset) => ({
        ...dataset,
        data: normalizeSeries(dataset.data || []),
      }))
      .filter((dataset) => seriesHasValues(dataset.data));
    const bands = (config.bands || [])
      .map((band) => ({
        ...band,
        lower: normalizeSeries(band.lower || []),
        upper: normalizeSeries(band.upper || []),
      }))
      .filter(
        (band) => seriesHasValues(band.lower) && seriesHasValues(band.upper)
      );
    const length =
      config.length ||
      maxLength(
        ...datasets.map((dataset) => dataset.data),
        ...bands.map((band) => band.lower),
        ...bands.map((band) => band.upper)
      );

    if (!length || (!datasets.length && !bands.length)) {
      target.innerHTML = chartEmptyCard(
        config.title,
        config.lead,
        config.emptyText || "Недостаточно данных для построения графика.",
        Boolean(config.wide)
      );
      return;
    }

    const labels = buildLabels(length, config.ticks || []);
    const width = 760;
    const height = 300;
    const padding = { top: 18, right: 20, bottom: 38, left: 58 };
    const extent = chartExtent(datasets, bands, Boolean(config.includeZero));
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    const safeLength = Math.max(length - 1, 1);

    const project = (value, index) => {
      const x = padding.left + (index / safeLength) * chartWidth;
      const y =
        padding.top +
        ((extent.max - value) / Math.max(extent.max - extent.min, 1e-9)) * chartHeight;
      return { x, y };
    };

    const gridTicks = numericTicks(extent.min, extent.max, 4);
    const gridMarkup = gridTicks
      .map((tick) => {
        const point = project(tick, 0);
        return `
          <line
            x1="${padding.left}"
            y1="${point.y.toFixed(2)}"
            x2="${width - padding.right}"
            y2="${point.y.toFixed(2)}"
            stroke="rgba(148,163,184,0.18)"
            stroke-width="1"
          ></line>
          <text
            x="${padding.left - 12}"
            y="${(point.y + 4).toFixed(2)}"
            text-anchor="end"
            fill="var(--text-muted)"
            font-size="11"
          >${escapeHtml(formatNum(tick, 1))}</text>
        `;
      })
      .join("");

    const xLabelIndexes = Array.from(
      new Set(
        Array.from({ length: Math.min(6, length) }, (_, index) =>
          Math.round((index * safeLength) / Math.max(Math.min(6, length) - 1, 1))
        )
      )
    );

    const xMarkup = xLabelIndexes
      .map((index) => {
        const point = project(extent.min, index);
        return `
          <text
            x="${point.x.toFixed(2)}"
            y="${height - 10}"
            text-anchor="middle"
            fill="var(--text-muted)"
            font-size="11"
          >${escapeHtml(labels[index] || String(index + 1))}</text>
        `;
      })
      .join("");

    const bandMarkup = bands
      .map((band) => {
        const indexes = Array.from({ length }, (_, index) => index).filter(
          (index) => band.lower[index] !== null && band.upper[index] !== null
        );
        const path = svgBandFromSeries(indexes, band.upper, band.lower, project);
        if (!path) return "";
        return `<path d="${path}" fill="${escapeHtml(
          band.fillColor || COLORS.band
        )}" stroke="none"></path>`;
      })
      .join("");

    const lineMarkup = datasets
      .map((dataset) => {
        const segments = buildContinuousSegments(dataset.data, project);
        const lines = segments
          .map(
            (segment) => `
              <path
                d="${svgPathFromPoints(segment)}"
                fill="none"
                stroke="${escapeHtml(dataset.color)}"
                stroke-width="${escapeHtml(String(dataset.width || 2.5))}"
                stroke-linecap="round"
                stroke-linejoin="round"
                ${dataset.dashed ? 'stroke-dasharray="6 6"' : ""}
              ></path>
            `
          )
          .join("");
        const areas = dataset.fill
          ? segments
              .map(
                (segment) => `
                  <path
                    d="${svgAreaFromPoints(
                      segment,
                      project(config.includeZero ? 0 : extent.min, 0).y
                    )}"
                    fill="${escapeHtml(dataset.fillColor || dataset.color)}"
                    stroke="none"
                  ></path>
                `
              )
              .join("")
          : "";
        return `${areas}${lines}`;
      })
      .join("");

    const legendItems = datasets.map((dataset) => ({
      label: dataset.label,
      color: dataset.color,
    }));

    target.innerHTML = `
      <article class="card weather-chart-card ${config.wide ? "weather-chart-wide" : ""}">
        <p class="section-kicker">${escapeHtml(config.title)}</p>
        ${config.lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(config.lead)}</div>` : ""}
        ${buildFactPills(config.facts)}
        <div class="forecast-chart mt-3">
          <svg class="forecast-svg weather-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(
            config.title
          )}">
            <rect
              x="${padding.left}"
              y="${padding.top}"
              width="${chartWidth}"
              height="${chartHeight}"
              rx="18"
              fill="rgba(255,255,255,0.68)"
            ></rect>
            ${gridMarkup}
            ${bandMarkup}
            ${lineMarkup}
            ${xMarkup}
          </svg>
        </div>
        ${buildLegend(legendItems)}
      </article>
    `;
  }

  function renderWeatherOverview(target, analysis) {
    if (!target) return;
    const meta = modeMeta(analysis?.mode);
    const availability = analysis?.availability || {};
    const kpis = analysis?.kpis || {};
    const maxDeficit = kpis.max_deficit || {};
    const maxSurplus = kpis.max_surplus || {};

    target.innerHTML = `
      <article class="weather-overview">
        <div class="weather-overview-main">
          <span class="weather-mode-badge">${escapeHtml(meta.label)}</span>
          <strong>${escapeHtml(
            analysis?.decision_support?.headline || "Погодный профиль ещё анализируется."
          )}</strong>
        </div>
        <div class="weather-overview-meta">${escapeHtml(meta.description)}</div>
        <div class="weather-chip-row">
          <span class="weather-chip ${
            availability.has_wind_range ? "weather-chip-ok" : "weather-chip-muted"
          }">
            ${availability.has_wind_range ? "Есть диапазон ветра" : "Без диапазона ветра"}
          </span>
          <span class="weather-chip ${
            availability.has_solar_east_west ? "weather-chip-ok" : "weather-chip-muted"
          }">
            ${
              availability.has_solar_east_west
                ? "Есть east/west по солнцу"
                : "Нет east/west по солнцу"
            }
          </span>
          <span class="weather-chip ${
            availability.has_category_breakdown ? "weather-chip-ok" : "weather-chip-muted"
          }">
            ${
              availability.has_category_breakdown
                ? "Есть разбиение потребления"
                : "Потребление агрегировано"
            }
          </span>
        </div>
        <div class="weather-overview-facts">
          <div class="weather-overview-fact weather-overview-fact-ok">
            <span class="weather-overview-fact-label">Средний баланс</span>
            <strong>${escapeHtml(formatNum(kpis.avg_balance, 2))}</strong>
          </div>
          <div class="weather-overview-fact weather-overview-fact-sun">
            <span class="weather-overview-fact-label">Доля солнечной генерации</span>
            <strong>${escapeHtml(formatPercent(kpis.solar_share_pct))}</strong>
          </div>
          <div class="weather-overview-fact weather-overview-fact-wind">
            <span class="weather-overview-fact-label">Доля ветровой генерации</span>
            <strong>${escapeHtml(formatPercent(kpis.wind_share_pct))}</strong>
          </div>
          <div class="weather-overview-fact weather-overview-fact-warn">
            <span class="weather-overview-fact-label">Пиковый риск</span>
            <strong>${escapeHtml(
              maxDeficit.tick !== null && maxDeficit.tick !== undefined
                ? `Дефицит на такте ${maxDeficit.tick}`
                : maxSurplus.tick !== null && maxSurplus.tick !== undefined
                  ? `Профицит на такте ${maxSurplus.tick}`
                  : "Нет выраженного экстремума"
            )}</strong>
          </div>
        </div>
      </article>
    `;
  }

  function renderWeatherKpis(target, analysis) {
    if (!target) return;
    const kpis = analysis?.kpis || {};
    const metrics = [
      {
        label: "Средняя генерация",
        value: formatNum(kpis.avg_generation, 2),
        caption: "Средняя доступная мощность по всем источникам.",
        klass: "weather-kpi-base",
      },
      {
        label: "Среднее потребление",
        value: formatNum(kpis.avg_consumption, 2),
        caption: "Средняя нагрузка энергосистемы по горизонту.",
        klass: "weather-kpi-base",
      },
      {
        label: "Средний баланс",
        value: formatNum(kpis.avg_balance, 2),
        caption: "Плюс означает профицит, минус означает дефицит.",
        klass:
          Number(kpis.avg_balance) >= 0 ? "weather-kpi-ok" : "weather-kpi-warn",
      },
      {
        label: "Тактов с дефицитом",
        value: formatNum(kpis.deficit_count, 0),
        caption: "Сколько периодов система не покрывает спрос.",
        klass: "weather-kpi-warn",
      },
      {
        label: "Тактов с профицитом",
        value: formatNum(kpis.surplus_count, 0),
        caption: "Периоды с запасом мощности.",
        klass: "weather-kpi-ok",
      },
      {
        label: "Отключений ветряка",
        value: formatNum(kpis.wind_off_count, 0),
        caption: "Число тактов с отключением из-за сильного ветра.",
        klass: "weather-kpi-wind",
      },
      {
        label: "Доля солнца",
        value: formatPercent(kpis.solar_share_pct),
        caption: "Часть всей генерации, которая приходится на солнце.",
        klass: "weather-kpi-sun",
      },
      {
        label: "Доля ветра",
        value: formatPercent(kpis.wind_share_pct),
        caption: "Часть всей генерации, которая приходится на ветер.",
        klass: "weather-kpi-wind",
      },
    ];

    target.innerHTML = `
      <div class="weather-kpi-grid">
        ${metrics
          .map(
            (metric) => `
              <article class="card weather-kpi-card ${escapeHtml(metric.klass)}">
                <span class="metric-label">${escapeHtml(metric.label)}</span>
                <strong>${escapeHtml(metric.value)}</strong>
                <div class="metric-caption">${escapeHtml(metric.caption)}</div>
              </article>
            `
          )
          .join("")}
      </div>
    `;
  }

  function renderWeatherDecisionSupport(target, analysis) {
    if (!target) return;
    const cards = Array.isArray(analysis?.decision_support?.cards)
      ? analysis.decision_support.cards
      : [];

    target.innerHTML = `
      <div class="stack gap-3">
        <article class="card weather-headline-block">
          <p class="section-kicker">Как читать прогноз</p>
          <strong>${escapeHtml(
            analysis?.decision_support?.headline ||
              "Пока нет рекомендации по этому прогнозу."
          )}</strong>
        </article>
        <div class="weather-decision-grid">
          ${cards
            .map(
              (card) => `
                <article class="card weather-decision-card ${toneClass(card?.tone)}">
                  <p class="section-kicker">${escapeHtml(card?.title || "Рекомендация")}</p>
                  <div class="mt-2">${escapeHtml(card?.text || "—")}</div>
                </article>
              `
            )
            .join("")}
        </div>
      </div>
    `;
  }

  function renderWeatherTables(target, analysis) {
    if (!target) return;

    function extrasText(row) {
      const extras = [];
      if (row.positive_count !== undefined) {
        extras.push(`положительных тактов: ${formatNum(row.positive_count, 0)}`);
      }
      if (row.wind_off_count !== undefined) {
        extras.push(`отключений: ${formatNum(row.wind_off_count, 0)}`);
      }
      if (row.full_power_count !== undefined) {
        extras.push(`полная мощность: ${formatNum(row.full_power_count, 0)}`);
      }
      return extras.length ? extras.join("; ") : "—";
    }

    function tableMarkup(title, rows) {
      if (!rows.length) {
        return `
          <article class="card weather-fold">
            <p class="section-kicker">${escapeHtml(title)}</p>
            <div class="empty-state mt-3">Для этой таблицы пока нет данных.</div>
          </article>
        `;
      }
      return `
        <article class="card weather-fold">
          <p class="section-kicker">${escapeHtml(title)}</p>
          <div class="table-wrap mt-3">
            <table class="table table-forecast-meta">
              <thead>
                <tr>
                  <th class="col-text">Ряд</th>
                  <th>Среднее</th>
                  <th>Мин</th>
                  <th>Макс</th>
                  <th class="col-text">Примечание</th>
                </tr>
              </thead>
              <tbody>
                ${rows
                  .map(
                    (row) => `
                      <tr>
                        <td class="col-text">${escapeHtml(row.label || row.key || "—")}</td>
                        <td>${escapeHtml(formatNum(row.mean, 2))}</td>
                        <td>${escapeHtml(formatNum(row.min, 2))}</td>
                        <td>${escapeHtml(formatNum(row.max, 2))}</td>
                        <td class="col-text">${escapeHtml(extrasText(row))}</td>
                      </tr>
                    `
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </article>
      `;
    }

    const tables = analysis?.tables || {};
    const mainStats = Array.isArray(tables.main_stats) ? tables.main_stats : [];
    const generatorStats = Array.isArray(tables.generator_stats)
      ? tables.generator_stats
      : [];

    target.innerHTML = `
      <div class="page-grid">
        ${tableMarkup("Ключевые погодные ряды", mainStats)}
        ${tableMarkup("Статистика по генераторам", generatorStats)}
      </div>
    `;
  }

  function renderWeatherInsights(target, analysis) {
    if (!target) return;
    const insights = Array.isArray(analysis?.insights) ? analysis.insights : [];
    target.innerHTML = `
      <div class="weather-insight-list">
        ${
          insights.length
            ? insights
                .map(
                  (item) =>
                    `<article class="card weather-insight-card">${escapeHtml(item)}</article>`
                )
                .join("")
            : `<article class="card weather-insight-card">Пока нет выводов по этому прогнозу.</article>`
        }
      </div>
    `;
  }

  function renderWeatherCharts(target, analysis) {
    if (!target) return;
    const series = analysis?.series || {};
    const chartMeta = analysis?.charts || {};
    const generation = normalizeSeries(series.total_generation || []);
    const consumption = normalizeSeries(series.total_consumption || []);
    const balance = normalizeSeries(series.balance || []);
    const balanceMin = normalizeSeries(series.balance_min || []);
    const balanceMax = normalizeSeries(series.balance_max || []);
    const windAvg = normalizeSeries(series.wind_avg || []);
    const windGen = normalizeSeries(series.wind_gen || []);
    const factory = normalizeSeries(series.category_consumption?.factory || []);
    const office = normalizeSeries(series.category_consumption?.office || []);
    const house = normalizeSeries(
      series.category_consumption?.house_load || series.category_consumption?.house || []
    );
    const hospital = normalizeSeries(series.category_consumption?.hospital || []);
    const solarImproved = normalizeSeries(series.solar_improved || []);
    const solarSimple = normalizeSeries(series.solar_simple || []);
    const solarEast = normalizeSeries(series.solar_east_gen || []);
    const solarWest = normalizeSeries(series.solar_west_gen || []);
    const sunEast = normalizeSeries(series.sun_east || []);
    const sunWest = normalizeSeries(series.sun_west || []);
    const ticks = series.tick || [];

    target.innerHTML = `
      <div class="weather-charts-grid">
        <div data-weather-chart="generation"></div>
        <div data-weather-chart="balance"></div>
        <div data-weather-chart="wind"></div>
        <div data-weather-chart="consumption"></div>
        <div data-weather-chart="solar-models"></div>
        <div data-weather-chart="solar-activity"></div>
        <div data-weather-chart="sources"></div>
      </div>
    `;

    const generationNode = target.querySelector('[data-weather-chart="generation"]');
    const balanceNode = target.querySelector('[data-weather-chart="balance"]');
    const windNode = target.querySelector('[data-weather-chart="wind"]');
    const consumptionNode = target.querySelector('[data-weather-chart="consumption"]');
    const solarModelsNode = target.querySelector('[data-weather-chart="solar-models"]');
    const solarActivityNode = target.querySelector('[data-weather-chart="solar-activity"]');
    const sourcesNode = target.querySelector('[data-weather-chart="sources"]');

    renderSvgChart(generationNode, {
      title:
        chartMeta.generation_vs_consumption?.title || "Генерация и потребление",
      lead: "Базовый график, который показывает запас мощности по горизонту.",
      facts: [
        `средняя генерация ${formatNum(analysis?.kpis?.avg_generation, 1)}`,
        `среднее потребление ${formatNum(analysis?.kpis?.avg_consumption, 1)}`,
      ],
      ticks,
      length: maxLength(generation, consumption),
      includeZero: true,
      wide: true,
      emptyText:
        chartMeta.generation_vs_consumption?.reason ||
        "Недостаточно данных для сравнения генерации и потребления.",
      datasets: [
        {
          label: "Генерация",
          data: generation,
          color: COLORS.generation,
          fill: true,
          fillColor: COLORS.generationFill,
        },
        {
          label: "Потребление",
          data: consumption,
          color: COLORS.consumption,
          width: 2.8,
        },
      ],
    });

    renderSvgChart(balanceNode, {
      title:
        chartMeta.balance_uncertainty?.title ||
        "Баланс с коридором неопределённости",
      lead: "Коридор показывает возможный разброс, центральная линия показывает ожидаемый баланс.",
      facts: [
        `дефицитных тактов ${formatNum(analysis?.kpis?.deficit_count, 0)}`,
        `профицитных тактов ${formatNum(analysis?.kpis?.surplus_count, 0)}`,
      ],
      ticks,
      length: maxLength(balance, balanceMin, balanceMax),
      wide: true,
      emptyText:
        chartMeta.balance_uncertainty?.reason ||
        "Не удалось построить коридор баланса.",
      datasets: [
        {
          label: "Баланс",
          data: balance,
          color: COLORS.balance,
          width: 2.8,
        },
        {
          label: "Нулевая линия",
          data: new Array(Math.max(balance.length, balanceMin.length, balanceMax.length)).fill(0),
          color: COLORS.zero,
          width: 1.5,
          dashed: true,
        },
      ],
      bands: [
        {
          lower: balanceMin,
          upper: balanceMax,
          fillColor: COLORS.band,
        },
      ],
    });

    renderSvgChart(windNode, {
      title: chartMeta.wind_forecast?.title || "Прогноз ветра и генерации",
      lead: "Полезен для быстрой оценки риска отключения ветряка и вклада ветровых лотов.",
      facts: [
        `отключений ${formatNum(analysis?.kpis?.wind_off_count, 0)}`,
        `полная мощность ${formatNum(analysis?.kpis?.wind_full_power_count, 0)}`,
      ],
      ticks,
      length: maxLength(windAvg, windGen),
      includeZero: true,
      emptyText:
        chartMeta.wind_forecast?.reason || "В прогнозе нет пригодного ветрового ряда.",
      datasets: [
        {
          label: "Средний ветер",
          data: windAvg,
          color: COLORS.wind,
        },
        {
          label: "Генерация ветра",
          data: windGen,
          color: COLORS.windGen,
        },
        {
          label: "Порог отключения",
          data: new Array(Math.max(windAvg.length, windGen.length)).fill(7),
          color: COLORS.threshold,
          width: 1.8,
          dashed: true,
        },
      ],
    });

    renderSvgChart(consumptionNode, {
      title:
        chartMeta.consumption_categories?.title || "Потребление по категориям",
      lead: "Показывает, какие типы нагрузки сильнее двигают общий спрос.",
      facts: [
        `домашняя нагрузка ${house.length ? "есть" : "нет"}`,
        `hospital ${hospital.length ? "есть" : "нет"}`,
      ],
      ticks,
      length: maxLength(factory, office, house, hospital, consumption),
      includeZero: true,
      emptyText:
        chartMeta.consumption_categories?.reason ||
        "Нет данных для разбивки потребления по категориям.",
      datasets: [
        { label: "Factory", data: factory, color: COLORS.factory },
        { label: "Office", data: office, color: COLORS.office },
        { label: "House", data: house, color: COLORS.house },
        { label: "Hospital", data: hospital, color: COLORS.hospital },
        { label: "Суммарное потребление", data: consumption, color: COLORS.total, width: 3.2 },
      ],
    });

    renderSvgChart(solarModelsNode, {
      title:
        chartMeta.solar_models?.title ||
        "Солнечная генерация: simple vs improved",
      lead: "Сравнивает базовую модель солнца с улучшенной и отдельными восточным/западным плечами.",
      facts: [
        `доля солнца ${formatPercent(analysis?.kpis?.solar_share_pct)}`,
        `east ${solarEast.length ? "есть" : "нет"}, west ${solarWest.length ? "есть" : "нет"}`,
      ],
      ticks,
      length: maxLength(solarImproved, solarSimple, solarEast, solarWest),
      includeZero: true,
      emptyText:
        chartMeta.solar_models?.reason ||
        "Не хватает рядов для сравнения солнечных моделей.",
      datasets: [
        {
          label: "Solar improved",
          data: solarImproved,
          color: COLORS.solarImproved,
          width: 2.8,
        },
        {
          label: "Solar simple",
          data: solarSimple,
          color: COLORS.solarSimple,
        },
        { label: "East generation", data: solarEast, color: COLORS.solarEast },
        { label: "West generation", data: solarWest, color: COLORS.solarWest },
      ],
    });

    renderSvgChart(solarActivityNode, {
      title:
        chartMeta.solar_activity?.title || "Солнечная активность east/west",
      lead: "Помогает увидеть асимметрию между восточным и западным солнцем.",
      facts: [
        `пиковый east ${formatNum(Math.max(...finiteValues(sunEast), 0), 1)}`,
        `пиковый west ${formatNum(Math.max(...finiteValues(sunWest), 0), 1)}`,
      ],
      ticks,
      length: maxLength(sunEast, sunWest),
      includeZero: true,
      emptyText:
        chartMeta.solar_activity?.reason ||
        "Для этого графика нужны отдельные ряды east/west.",
      datasets: [
        { label: "Sun east", data: sunEast, color: COLORS.illuminationEast },
        { label: "Sun west", data: sunWest, color: COLORS.illuminationWest },
      ],
    });

    renderSvgChart(sourcesNode, {
      title:
        chartMeta.generation_types?.title || "Сравнение источников генерации",
      lead: "Сопоставляет вклад солнца и ветра с общей генерацией.",
      facts: [
        `ветер ${formatPercent(analysis?.kpis?.wind_share_pct)}`,
        `солнце ${formatPercent(analysis?.kpis?.solar_share_pct)}`,
      ],
      ticks,
      length: maxLength(solarImproved, windGen, generation),
      includeZero: true,
      emptyText:
        chartMeta.generation_types?.reason ||
        "Недостаточно данных по типам генерации.",
      datasets: [
        { label: "Solar", data: solarImproved, color: COLORS.solarImproved },
        { label: "Wind", data: windGen, color: COLORS.windGen },
        { label: "Total generation", data: generation, color: COLORS.generation, width: 3 },
      ],
    });
  }

  function renderWeatherAnalysis(target, analysis) {
    if (!target) return;
    if (!analysis || !analysis.series) {
      target.innerHTML = `<div class="empty-state">Анализ прогноза погоды пока недоступен.</div>`;
      return;
    }

    target.innerHTML = `
      <div class="weather-analysis stack gap-4">
        <div data-weather-overview></div>
        <div data-weather-kpis></div>
        <div data-weather-decision-support></div>
        <div data-weather-charts></div>
        <div data-weather-tables></div>
        <div data-weather-insights></div>
      </div>
    `;

    renderWeatherOverview(target.querySelector("[data-weather-overview]"), analysis);
    renderWeatherKpis(target.querySelector("[data-weather-kpis]"), analysis);
    renderWeatherDecisionSupport(
      target.querySelector("[data-weather-decision-support]"),
      analysis
    );
    renderWeatherCharts(target.querySelector("[data-weather-charts]"), analysis);
    renderWeatherTables(target.querySelector("[data-weather-tables]"), analysis);
    renderWeatherInsights(target.querySelector("[data-weather-insights]"), analysis);
  }

  function renderPreviewChart(target, periods) {
    if (!target) return;
    const rows = Array.isArray(periods) ? periods : [];
    const wind = rows.map((row) => {
      const number = Number(row.wind);
      return Number.isFinite(number) ? number : null;
    });
    const illumination = rows.map((row) => {
      const number = Number(row.illumination);
      return Number.isFinite(number) ? number : null;
    });

    renderSvgChart(target, {
      title: "Предпросмотр загруженного прогноза",
      lead: "Быстрая проверка, что ключевые погодные ряды читаются корректно.",
      facts: [`тактов ${rows.length}`, `wind ${seriesHasValues(wind) ? "есть" : "нет"}`],
      ticks: rows.map((row, index) => {
        const number = Number(row.tick);
        return Number.isFinite(number) ? number : index + 1;
      }),
      length: maxLength(wind, illumination),
      includeZero: true,
      emptyText: "В загруженном прогнозе нет рядов для предварительного графика.",
      datasets: [
        { label: "Wind", data: wind, color: COLORS.wind, width: 2.8 },
        { label: "Illumination", data: illumination, color: COLORS.solarImproved },
      ],
    });
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    target.innerHTML = `
      <div class="risk-block">
        <strong>${escapeHtml(title)}</strong>
        <div class="mt-2">${escapeHtml(payload?.quality?.text || "")}</div>
      </div>
      <div class="mt-4">
        <p class="section-kicker">Анализ прогноза погоды</p>
        <div data-weather-analysis class="mt-2"></div>
      </div>
    `;
    renderWeatherAnalysis(
      target.querySelector("[data-weather-analysis]"),
      payload?.weather_analysis || null
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
    if (withChart) {
      chart.innerHTML = `<div class="muted">Готовлю график...</div>`;
    }

    const data = await loadForecastDetails(forecastId);
    if (!data.ok) {
      renderSummary(out, "Ошибка", { quality: { text: errorMessage(data) } });
      if (withChart) {
        chart.innerHTML = `<div class="empty-state">${escapeHtml(errorMessage(data))}</div>`;
      }
      return;
    }

    renderSummary(out, `Диагностика прогноза #${forecastId}`, data.item?.summary || {});

    if (withChart) {
      renderPreviewChart(chart, data.item?.periods || []);
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
