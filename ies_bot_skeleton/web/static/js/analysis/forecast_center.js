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

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderSimpleChart(target, title, series) {
    if (!target) return;
    const points = Array.isArray(series) ? series.map((value, index) => ({index, value: Number(value)})).filter((row) => Number.isFinite(row.value)) : [];
    if (!points.length) {
      target.innerHTML = `<div class="empty-state">${escapeHtml(title)}: недостаточно данных.</div>`;
      return;
    }
    const width = 720;
    const height = 240;
    const min = Math.min(...points.map((row) => row.value));
    const max = Math.max(...points.map((row) => row.value));
    const range = max - min || 1;
    const step = points.length > 1 ? width / (points.length - 1) : width;
    const path = points.map((row, idx) => {
      const x = idx * step;
      const y = height - ((row.value - min) / range) * height;
      return `${idx === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');
    target.innerHTML = `<article class="card weather-chart-card"><p class="section-kicker">${escapeHtml(title)}</p><svg viewBox="0 0 ${width} ${height}" class="forecast-svg weather-svg"><path d="${path}" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"></path></svg></article>`;
  }

  function renderWeatherAnalysis(target, analysis) {
    if (!target) return;
    if (!analysis || !analysis.series) {
      target.innerHTML = '<div class="empty-state">Weather analysis пока недоступен.</div>';
      return;
    }
    const kpis = analysis.kpis || {};
    target.innerHTML = `
      <div class="stack gap-4">
        <div class="weather-kpi-grid">
          <div class="metric-card"><span class="metric-label">Средняя генерация</span><strong>${escapeHtml(formatNum(kpis.avg_generation, 2))}</strong></div>
          <div class="metric-card"><span class="metric-label">Среднее потребление</span><strong>${escapeHtml(formatNum(kpis.avg_consumption, 2))}</strong></div>
          <div class="metric-card"><span class="metric-label">Средний баланс</span><strong>${escapeHtml(formatNum(kpis.avg_balance, 2))}</strong></div>
          <div class="metric-card"><span class="metric-label">Тактов с дефицитом</span><strong>${escapeHtml(formatNum(kpis.deficit_count, 0))}</strong></div>
          <div class="metric-card"><span class="metric-label">Тактов с профицитом</span><strong>${escapeHtml(formatNum(kpis.surplus_count, 0))}</strong></div>
          <div class="metric-card"><span class="metric-label">Отключений ветряка</span><strong>${escapeHtml(formatNum(kpis.wind_off_count, 0))}</strong></div>
        </div>
        <div class="weather-charts-grid">
          <div data-chart-generation></div>
          <div data-chart-balance></div>
          <div data-chart-wind></div>
          <div data-chart-consumption></div>
        </div>
        <div class="weather-insight-list">
          ${(Array.isArray(analysis.insights) ? analysis.insights : []).map((item) => `<article class="card weather-insight-card">${escapeHtml(item)}</article>`).join('') || '<article class="card weather-insight-card">Пока нет выводов по этому прогнозу.</article>'}
        </div>
      </div>
    `;
    renderSimpleChart(target.querySelector('[data-chart-generation]'), 'Генерация', analysis.series.total_generation || []);
    renderSimpleChart(target.querySelector('[data-chart-balance]'), 'Баланс', analysis.series.balance || []);
    renderSimpleChart(target.querySelector('[data-chart-wind]'), 'Ветер', analysis.series.wind_avg || []);
    renderSimpleChart(target.querySelector('[data-chart-consumption]'), 'Потребление', analysis.series.total_consumption || []);
  }

  function renderSummary(target, title, payload) {
    if (!target) return;
    target.innerHTML = `<div class="risk-block"><strong>${escapeHtml(title)}</strong><div class="mt-2">${escapeHtml(payload.quality?.text || '')}</div></div><div class="mt-4"><p class="section-kicker">Анализ прогноза погоды</p><div data-weather-analysis class="mt-2"></div></div>`;
    renderWeatherAnalysis(target.querySelector('[data-weather-analysis]'), payload.weather_analysis || null);
  }

  async function loadForecastDetails(forecastId) {
    return apiFetchJson(`/api/forecast/${forecastId}`, {headers: {'X-CSRFToken': (window.IES_FORECAST_CENTER || {}).csrfToken || window.IES_CSRF_TOKEN || ''}});
  }

  async function runDiagnostics(forecastId, withChart) {
    const out = document.getElementById('forecastOut');
    const chart = document.getElementById('forecastChart');
    if (!forecastId) {
      renderSummary(out, 'Диагностика', {quality: {text: 'Сначала выберите прогноз.'}});
      return;
    }
    out.innerHTML = '<div class="risk-block">Собираю диагностику...</div>';
    if (withChart) chart.innerHTML = '<div class="muted">Готовлю график...</div>';
    const data = await loadForecastDetails(forecastId);
    if (!data.ok) {
      renderSummary(out, 'Ошибка', {quality: {text: errorMessage(data)}});
      if (withChart) chart.innerHTML = `<div class="muted">${escapeHtml(errorMessage(data))}</div>`;
      return;
    }
    renderSummary(out, `Диагностика прогноза #${forecastId}`, data.item?.summary || {});
    if (withChart) renderSimpleChart(chart, 'Предпросмотр: ветер', (data.item?.periods || []).map((row) => Number(row.wind || 0)));
  }

  window.addEventListener('DOMContentLoaded', () => {
    const out = document.getElementById('forecastOut');
    const form = document.getElementById('forecastForm');
    const activeWeatherAnalysis = (window.IES_FORECAST_CENTER || {}).activeWeatherAnalysis || null;
    renderWeatherAnalysis(document.getElementById('activeForecastWeatherAnalysis'), activeWeatherAnalysis);

    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      out.innerHTML = '<div class="risk-block">Загрузка прогноза...</div>';
      const data = await apiFetchJson('/api/forecast/upload', {method: 'POST', body: formData});
      if (!data.ok) {
        renderSummary(out, 'Ошибка загрузки', {quality: {text: errorMessage(data)}});
        return;
      }
      renderSummary(out, 'Прогноз загружен', data.summary || {});
      window.setTimeout(() => window.location.reload(), 700);
    });

    document.querySelectorAll('.analyzeBtn').forEach((button) => {
      button.addEventListener('click', async () => { await runDiagnostics(Number(button.dataset.forecastId || 0), false); });
    });
    document.querySelectorAll('.chartBtn').forEach((button) => {
      button.addEventListener('click', async () => { await runDiagnostics(Number(button.dataset.forecastId || 0), true); });
    });
  });
})();