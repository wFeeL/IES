(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Неизвестная ошибка');

  function formatNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toFixed(digits);
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    const seriesRows = Object.entries(payload.series_stats || {})
      .map(([key, stats]) => `
        <tr>
          <td>${key}</td>
          <td>${formatNum(stats.min, 3)}</td>
          <td>${formatNum(stats.max, 3)}</td>
          <td>${formatNum(stats.avg, 3)}</td>
          <td>${formatNum(stats.median, 3)}</td>
        </tr>
      `)
      .join('');
    const loads = Object.entries(payload.consumer_averages || {})
      .map(([key, value]) => `<span class="pill">${key}: ${formatNum(value, 2)}</span>`)
      .join('');
    const warnings = (payload.quality?.warnings || []).map((item) => `<li>${item}</li>`).join('');
    const problems = (payload.quality?.problem_columns || []).join(', ');
    const empty = (payload.quality?.empty_columns || []).join(', ');

    target.innerHTML = `
      <div class="risk-block">
        <strong>${title}</strong>
        <div class="mt-2">Источник: ${payload.source_kind || '—'}</div>
        <div>Такты: ${payload.tick_from ?? '—'}–${payload.tick_to ?? '—'}</div>
        <div>Периодов: ${payload.count ?? 0}</div>
        <div>Средний ветер: ${formatNum(payload.avg_wind, 2)}</div>
        <div>Средняя освещённость: ${formatNum(payload.avg_illumination, 2)}</div>
        <div>Средняя цена рынка: ${formatNum(payload.avg_market_price, 2)}</div>
        <div class="pill-row mt-3">${loads || '<span class="muted">Ряды нагрузки не найдены.</span>'}</div>
        <div class="mt-3">${payload.quality?.text || ''}</div>
        ${problems ? `<div class="mt-2">Проблемные ряды: ${problems}</div>` : ''}
        ${empty ? `<div class="mt-2">Пустые колонки: ${empty}</div>` : ''}
        ${warnings ? `<ul class="stack gap-1 mt-3">${warnings}</ul>` : ''}
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>Ряд</th><th>Минимум</th><th>Максимум</th><th>Среднее</th><th>Медиана</th></tr></thead>
          <tbody>${seriesRows || '<tr><td colspan="5" class="muted">Статистика пока недоступна.</td></tr>'}</tbody>
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

  async function loadForecastDetails(forecastId) {
    return apiFetchJson(`/api/forecast/${forecastId}`, {
      headers: {'X-CSRFToken': (window.IES_FORECAST_CENTER || {}).csrfToken || window.IES_CSRF_TOKEN || ''},
    });
  }

  window.addEventListener('DOMContentLoaded', () => {
    const out = document.getElementById('forecastOut');
    const chart = document.getElementById('forecastChart');
    const form = document.getElementById('forecastForm');

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

    document.querySelectorAll('.analyzeBtn').forEach((button) => {
      button.addEventListener('click', async () => {
        out.innerHTML = '<div class="risk-block">Собираю диагностику...</div>';
        const data = await apiFetchJson(`/api/forecast/${button.dataset.forecastId}/analyze`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': (window.IES_FORECAST_CENTER || {}).csrfToken || window.IES_CSRF_TOKEN || '',
          },
          body: JSON.stringify({}),
        });
        if (!data.ok) {
          renderSummary(out, 'Ошибка', {quality: {text: errorMessage(data)}, series_stats: {}});
          return;
        }
        renderSummary(out, `Диагностика прогноза #${button.dataset.forecastId}`, data.item || {});
      });
    });

    document.querySelectorAll('.chartBtn').forEach((button) => {
      button.addEventListener('click', async () => {
        chart.innerHTML = '<div class="muted">Готовлю график...</div>';
        const data = await loadForecastDetails(button.dataset.forecastId);
        if (!data.ok) {
          chart.innerHTML = `<div class="muted">${errorMessage(data)}</div>`;
          return;
        }
        renderChart(chart, data.item?.periods || []);
        renderSummary(out, `Диагностика прогноза #${button.dataset.forecastId}`, data.item?.summary || {});
      });
    });
  });
})();
