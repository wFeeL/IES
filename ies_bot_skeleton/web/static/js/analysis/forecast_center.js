(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Неизвестная ошибка');

  function formatNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const fixed = number.toFixed(Math.max(0, Number(digits || 0)));
    return fixed.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    const statsRows = Array.isArray(payload.mapped_raw_stats_display) && payload.mapped_raw_stats_display.length
      ? payload.mapped_raw_stats_display
      : Array.isArray(payload.series_stats_display)
        ? payload.series_stats_display
        : Object.entries(payload.series_stats || {}).map(([key, stats]) => ({
            key,
            label: key,
            stats,
            group: 'legacy',
          }));
    const seriesRows = statsRows
      .map((row) => `
        <tr>
          <td>${row.label || row.key || '—'}</td>
          <td>${formatNum((row.stats || {}).min, 3)}</td>
          <td>${formatNum((row.stats || {}).max, 3)}</td>
          <td>${formatNum((row.stats || {}).avg, 3)}</td>
          <td>${formatNum((row.stats || {}).median, 3)}</td>
        </tr>
      `)
      .join('');
    const warnings = (payload.quality?.warnings || []).map((item) => `<li>${item}</li>`).join('');
    const problems = (payload.quality?.problem_columns || []).join(', ');
    const empty = (payload.quality?.empty_columns || []).join(', ');
    const tickRange = payload.mapped_tick_range_label || `${payload.tick_from ?? '—'}–${payload.tick_to ?? '—'}`;
    const rawColumns = Array.isArray(payload.raw_csv_columns) ? payload.raw_csv_columns : [];
    const usedRawColumns = Array.isArray(payload.used_raw_columns) ? payload.used_raw_columns : [];
    const unsupportedColumns = Array.isArray(payload.unsupported_raw_columns) ? payload.unsupported_raw_columns : [];
    const mappings = Array.isArray(payload.column_mapping_rows) ? payload.column_mapping_rows : [];
    const coverageRows = Array.isArray(payload.object_coverage_rows) ? payload.object_coverage_rows : [];
    const mappingHtml = mappings.length
      ? mappings.map((row) => `<li><code>${row.raw_name}</code> → ${row.interpreted_meaning || row.canonical_key || '—'}</li>`).join('')
      : '<li class="muted">Явный mapping не требуется или пока не рассчитан.</li>';
    const coverageHtml = coverageRows.length
      ? coverageRows.map((row) => `<tr><td>${row.object_type_name || row.object_type_code || '—'}</td><td>${row.status || '—'}</td><td>${(row.required_profiles || []).join(', ') || '—'}</td><td>${(row.missing_profiles || []).join(', ') || '—'}</td></tr>`).join('')
      : '<tr><td colspan="4" class="muted">Покрытие по объектам пока недоступно.</td></tr>';

    target.innerHTML = `
      <div class="risk-block">
        <strong>${title}</strong>
        <div class="mt-2">Источник: ${payload.source_kind || '—'}</div>
        <div>Такты: ${tickRange}</div>
        <div>Периодов: ${payload.count ?? 0}</div>
        <div>Средний ветер: ${formatNum(payload.avg_wind, 2)}</div>
        <div>Средняя освещённость: ${formatNum(payload.avg_illumination, 2)}</div>
        <div>Средняя цена рынка: ${formatNum(payload.avg_market_price, 2)}</div>
        <div>Raw CSV columns: ${rawColumns.length}</div>
        <div>Used raw columns: ${usedRawColumns.length}</div>
        <div>Лишние колонки CSV: ${unsupportedColumns.length}</div>
        <div class="mt-3">${payload.quality?.text || ''}</div>
        ${problems ? `<div class="mt-2">Проблемные ряды: ${problems}</div>` : ''}
        ${empty ? `<div class="mt-2">Пустые колонки: ${empty}</div>` : ''}
        ${warnings ? `<ul class="stack gap-1 mt-3">${warnings}</ul>` : ''}
      </div>
      <div class="grid cols-3 gap-3">
        <article class="card">
          <p class="section-kicker">Raw CSV</p>
          <div class="muted mt-2">${rawColumns.length ? rawColumns.join(', ') : 'Для встроенного прогноза raw CSV columns не используются.'}</div>
        </article>
        <article class="card">
          <p class="section-kicker">Mapping</p>
          <ul class="stack gap-1 mt-2">${mappingHtml}</ul>
        </article>
        <article class="card">
          <p class="section-kicker">Unsupported</p>
          <div class="muted mt-2">${unsupportedColumns.length ? unsupportedColumns.join(', ') : 'Лишних raw-колонок нет.'}</div>
        </article>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Ряд</th><th>Минимум</th><th>Максимум</th><th>Среднее</th><th>Медиана</th></tr></thead>
          <tbody>${seriesRows || '<tr><td colspan="5" class="muted">Статистика пока недоступна.</td></tr>'}</tbody>
        </table>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Object type</th><th>Coverage</th><th>Required</th><th>Missing</th></tr></thead>
          <tbody>${coverageHtml}</tbody>
        </table>
      </div>
    `;
  }

  function buildLine(points, width, height, min, max, color) {
    if (!points.length) return '';
    const range = max - min || 1;
    const step = points.length > 1 ? width / (points.length - 1) : width;
    const coords = points.map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / range) * height;
      return `${x},${y}`;
    });
    return `<polyline fill="none" stroke="${color}" stroke-width="2" points="${coords.join(' ')}" />`;
  }

  function renderChart(target, periods) {
    if (!target) return;
    if (!Array.isArray(periods) || !periods.length) {
      target.innerHTML = '<div class="muted">Недостаточно данных для графика.</div>';
      return;
    }

    const wind = periods.map((row) => Number(row.wind || 0));
    const illumination = periods.map((row) => Number(row.illumination || 0));
    const market = periods.map((row) => Number(row.market_price || 0));
    const consumptionKeys = Object.keys(periods[0].consumption || {});
    const selectedLoad = consumptionKeys[0] || null;
    const load = selectedLoad ? periods.map((row) => Number((row.consumption || {})[selectedLoad] || 0)) : [];
    const allValues = wind.concat(illumination, market, load).filter((value) => Number.isFinite(value));
    const min = Math.min.apply(null, allValues);
    const max = Math.max.apply(null, allValues);
    const width = 720;
    const height = 240;

    target.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" class="forecast-svg" role="img" aria-label="График прогноза">
        ${buildLine(wind, width, height, min, max, '#0f8b8d')}
        ${buildLine(illumination, width, height, min, max, '#e0a100')}
        ${buildLine(market, width, height, min, max, '#c74a58')}
        ${buildLine(load, width, height, min, max, '#3358a5')}
      </svg>
      <div class="pill-row mt-3">
        <span class="pill">Бирюзовый: ветер</span>
        <span class="pill">Жёлтый: освещённость</span>
        <span class="pill">Красный: цена рынка</span>
        ${selectedLoad ? `<span class="pill">Синий: ${selectedLoad}</span>` : ''}
      </div>
    `;
  }

  function setSelectedForecast(forecastId) {
    document.querySelectorAll('[data-forecast-id]').forEach((node) => {
      const nodeId = Number(node.dataset.forecastId || 0);
      node.classList.toggle('row-selected', nodeId === Number(forecastId || 0));
    });
  }

  async function loadForecastDetails(forecastId) {
    return apiFetchJson(`/api/forecast/${forecastId}`, {
      headers: {'X-CSRFToken': (window.IES_FORECAST_CENTER || {}).csrfToken || window.IES_CSRF_TOKEN || ''},
    });
  }

  async function runDiagnostics(forecastId, withChart) {
    const out = document.getElementById('forecastOut');
    const chart = document.getElementById('forecastChart');
    if (!forecastId) {
      renderSummary(out, 'Диагностика', {quality: {text: 'Сначала выберите прогноз.'}, series_stats: {}});
      return;
    }

    setSelectedForecast(forecastId);
    out.innerHTML = '<div class="risk-block">Собираю диагностику...</div>';
    if (withChart) {
      chart.innerHTML = '<div class="muted">Готовлю график...</div>';
    }
    const data = await loadForecastDetails(forecastId);
    if (!data.ok) {
      renderSummary(out, 'Ошибка', {quality: {text: errorMessage(data)}, series_stats: {}});
      if (withChart) {
        chart.innerHTML = `<div class="muted">${errorMessage(data)}</div>`;
      }
      return;
    }

    renderSummary(out, `Диагностика прогноза #${forecastId}`, data.item?.summary || {});
    if (withChart) {
      renderChart(chart, data.item?.periods || []);
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    const out = document.getElementById('forecastOut');
    const form = document.getElementById('forecastForm');
    const activeForecastId = Number((window.IES_FORECAST_CENTER || {}).activeForecastId || 0);

    if (activeForecastId > 0) {
      setSelectedForecast(activeForecastId);
    }

    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      out.innerHTML = '<div class="risk-block">Загрузка прогноза...</div>';
      const data = await apiFetchJson('/api/forecast/upload', {method: 'POST', body: formData});
      if (!data.ok) {
        renderSummary(out, 'Ошибка загрузки', {quality: {text: errorMessage(data)}, series_stats: {}});
        return;
      }
      renderSummary(out, 'Прогноз загружен', data.summary || {});
      window.setTimeout(() => window.location.reload(), 700);
    });

    document.querySelectorAll('[data-forecast-id]').forEach((row) => {
      row.addEventListener('click', async (event) => {
        if (event.target.closest('button, a, input, form')) {
          return;
        }
        await runDiagnostics(Number(row.dataset.forecastId || 0), false);
      });
    });

    document.querySelectorAll('.analyzeBtn').forEach((button) => {
      button.addEventListener('click', async () => {
        await runDiagnostics(Number(button.dataset.forecastId || 0), false);
      });
    });

    document.querySelectorAll('.chartBtn').forEach((button) => {
      button.addEventListener('click', async () => {
        await runDiagnostics(Number(button.dataset.forecastId || 0), true);
      });
    });
  });
})();
