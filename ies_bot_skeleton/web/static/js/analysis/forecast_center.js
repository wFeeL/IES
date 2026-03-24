(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Неизвестная ошибка');

  const WEATHER_COLORS = {
    generation: '#0f766e',
    consumption: '#3358a5',
    balance: '#b45309',
    balancePositive: '#0f7f5f',
    balanceNegative: '#bf3e50',
    wind: '#0f8b8d',
    windThreshold: '#bf3e50',
    windGen: '#2c7a7b',
    solarImproved: '#e0a100',
    solarSimple: '#c0841a',
    solarEast: '#f59e0b',
    solarWest: '#d97706',
    hospital: '#3358a5',
    factory: '#1f8a70',
    houseA: '#d97706',
    houseB: '#9a3412',
    office: '#4f46e5',
    houseLoad: '#64748b',
    corridor: 'rgba(224, 161, 0, 0.18)',
    grid: 'rgba(148, 163, 184, 0.28)',
    lineMuted: 'rgba(148, 163, 184, 0.7)',
  };

  const WEATHER_LABELS = {
    hospital: 'Больница',
    factory: 'Завод',
    house_a: 'Дом A',
    house_b: 'Дом B',
    office: 'Офис',
    house_load: 'Домовая нагрузка',
    solar_simple: 'Базовая солнечная модель',
    solar_improved: 'Улучшенная солнечная модель',
    sun_east: 'Освещённость востока',
    sun_west: 'Освещённость запада',
    generation: 'Генерация',
    consumption: 'Потребление',
    wind_avg: 'Средний ветер',
    wind_gen: 'Генерация ветра',
  };

  function weatherModeLabel(mode) {
    if (mode === 'full_nto_2024') return 'Полный NTO-анализ';
    if (mode === 'canonical_fallback') return 'Анализ по canonical-данным';
    return 'Частичный анализ';
  }

  function formatSignedNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const formatted = formatNum(number, digits);
    return number > 0 ? `+${formatted}` : formatted;
  }

  function renderWeatherOverview(target, analysis) {
    if (!target) return;
    const availability = analysis?.availability || {};
    const tickCount = Array.isArray(analysis?.series?.tick) ? analysis.series.tick.length : 0;
    const chips = [
      {
        label: availability.has_wind_range ? 'Есть диапазон ветра' : 'Без диапазона ветра',
        tone: availability.has_wind_range ? 'ok' : 'muted',
      },
      {
        label: availability.has_solar_east_west ? 'Есть east/west солнце' : 'Без east/west солнца',
        tone: availability.has_solar_east_west ? 'ok' : 'muted',
      },
      {
        label: availability.has_category_breakdown ? 'Есть категории нагрузки' : 'Без разбивки нагрузки',
        tone: availability.has_category_breakdown ? 'ok' : 'muted',
      },
    ];
    const facts = buildWeatherOverviewFacts(analysis);
    target.innerHTML = `
      <div class="weather-overview">
        <div class="weather-overview-main">
          <span class="weather-mode-badge">${escapeHtml(weatherModeLabel(analysis?.mode))}</span>
          <div class="weather-overview-meta">Горизонт анализа: ${escapeHtml(formatNum(tickCount, 0))} тактов</div>
        </div>
        <div class="weather-chip-row">
          ${chips
            .map(
              (chip) => `
                <span class="weather-chip weather-chip-${escapeHtml(chip.tone)}">${escapeHtml(chip.label)}</span>
              `
            )
            .join('')}
        </div>
        ${
          facts.length
            ? `
              <div class="weather-overview-facts">
                ${facts
                  .map(
                    (fact) => `
                      <article class="weather-overview-fact weather-overview-fact-${escapeHtml(fact.tone || 'base')}">
                        <span class="weather-overview-fact-label">${escapeHtml(fact.label)}</span>
                        <strong>${escapeHtml(fact.value)}</strong>
                      </article>
                    `
                  )
                  .join('')}
              </div>
            `
            : ''
        }
      </div>
    `;
  }

  function formatNum(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    const fixed = number.toFixed(Math.max(0, Number(digits || 0)));
    return fixed.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function asNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  }

  function normalizeSeries(series) {
    if (!Array.isArray(series)) return [];
    return series.map(asNumber);
  }

  function hasSeriesData(series) {
    return normalizeSeries(series).some((value) => value !== null);
  }

  function getSeries(analysis, key) {
    return normalizeSeries(analysis?.series?.[key]);
  }

  function getCategorySeries(analysis) {
    const bucket = analysis?.series?.category_consumption || {};
    return {
      hospital: normalizeSeries(bucket.hospital),
      factory: normalizeSeries(bucket.factory),
      house_a: normalizeSeries(bucket.house_a),
      house_b: normalizeSeries(bucket.house_b),
      office: normalizeSeries(bucket.office),
      house_load: normalizeSeries(bucket.house_load),
    };
  }

  function averageSeries(values) {
    const numbers = collectFiniteValues([values]);
    if (!numbers.length) return null;
    return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
  }

  function formatTickList(values) {
    const items = Array.isArray(values) ? values.filter((item) => item !== null && item !== undefined) : [];
    return items.length ? items.join(', ') : '—';
  }

  function describeBalanceState(avgBalance) {
    const value = Number(avgBalance);
    if (!Number.isFinite(value)) return {value: 'Недостаточно данных', tone: 'base'};
    if (value < -0.1) return {value: 'Средний дефицит', tone: 'warn'};
    if (value > 0.1) return {value: 'Есть запас генерации', tone: 'ok'};
    return {value: 'Почти нейтральный', tone: 'base'};
  }

  function dominantSourceLabel(kpis) {
    const solarShare = Number(kpis?.solar_share_pct);
    const windShare = Number(kpis?.wind_share_pct);
    if (!Number.isFinite(solarShare) || !Number.isFinite(windShare)) {
      return {value: 'Источник не выделяется', tone: 'base'};
    }
    if (solarShare >= windShare + 10) return {value: 'Солнце ведёт профиль', tone: 'sun'};
    if (windShare >= solarShare + 10) return {value: 'Ветер ведёт профиль', tone: 'wind'};
    return {value: 'Смешанная генерация', tone: 'base'};
  }

  function dominantCategoryLabel(categories) {
    const candidates = Object.entries(categories || {})
      .map(([key, values]) => ({key, avg: averageSeries(values)}))
      .filter((item) => item.avg !== null)
      .sort((left, right) => right.avg - left.avg);
    if (!candidates.length) return null;
    return WEATHER_LABELS[candidates[0].key] || candidates[0].key;
  }

  function buildWeatherOverviewFacts(analysis) {
    const kpis = analysis?.kpis || {};
    const balanceState = describeBalanceState(kpis.avg_balance);
    const dominantSource = dominantSourceLabel(kpis);
    const facts = [
      {label: 'Режим баланса', value: balanceState.value, tone: balanceState.tone},
      {label: 'Ведущий источник', value: dominantSource.value, tone: dominantSource.tone},
    ];
    const maxDeficit = kpis.max_deficit || {};
    const maxSurplus = kpis.max_surplus || {};
    if (maxDeficit?.tick !== null && maxDeficit?.tick !== undefined && maxDeficit?.value !== null && maxDeficit?.value !== undefined) {
      facts.push({
        label: 'Пик дефицита',
        value: `такт ${maxDeficit.tick} · ${formatSignedNum(maxDeficit.value, 1)}`,
        tone: 'warn',
      });
    } else if (maxSurplus?.tick !== null && maxSurplus?.tick !== undefined && maxSurplus?.value !== null && maxSurplus?.value !== undefined) {
      facts.push({
        label: 'Пик профицита',
        value: `такт ${maxSurplus.tick} · ${formatSignedNum(maxSurplus.value, 1)}`,
        tone: 'ok',
      });
    }

    const dominantCategory = dominantCategoryLabel(getCategorySeries(analysis));
    if (dominantCategory) {
      facts.push({label: 'Крупнейшая нагрузка', value: dominantCategory, tone: 'base'});
    } else {
      const windOffCount = Number(kpis.wind_off_count);
      facts.push({
        label: 'Риск по ветру',
        value: Number.isFinite(windOffCount) && windOffCount > 0 ? `${formatNum(windOffCount, 0)} отключений` : 'Отключений не видно',
        tone: Number.isFinite(windOffCount) && windOffCount > 0 ? 'warn' : 'wind',
      });
    }
    return facts.filter(Boolean);
  }

  function collectFiniteValues(seriesList) {
    const values = [];
    (seriesList || []).forEach((series) => {
      normalizeSeries(series).forEach((value) => {
        if (value !== null) values.push(value);
      });
    });
    return values;
  }

  function scaleY(value, min, max, height) {
    const range = max - min || 1;
    return height - ((value - min) / range) * height;
  }

  function buildGrid(width, height, min, max, lines) {
    const count = Math.max(2, Number(lines || 4));
    let html = '';
    for (let index = 0; index <= count; index += 1) {
      const ratio = index / count;
      const y = ratio * height;
      const value = max - (max - min) * ratio;
      html += `<line x1="0" y1="${y.toFixed(2)}" x2="${width}" y2="${y.toFixed(2)}" stroke="${WEATHER_COLORS.grid}" stroke-width="1" />`;
      html += `<text x="6" y="${Math.max(12, y - 4).toFixed(2)}" fill="${WEATHER_COLORS.lineMuted}" font-size="10">${escapeHtml(formatNum(value, 1))}</text>`;
    }
    return html;
  }

  function buildPathSegments(values, width, height, min, max) {
    const series = normalizeSeries(values);
    if (!series.length) return [];
    const step = series.length > 1 ? width / (series.length - 1) : width;
    const segments = [];
    let current = [];
    series.forEach((value, index) => {
      if (value === null) {
        if (current.length > 1) segments.push(current);
        current = [];
        return;
      }
      const x = index * step;
      const y = scaleY(value, min, max, height);
      current.push([x, y]);
    });
    if (current.length > 1) segments.push(current);
    return segments;
  }

  function renderLineSeries(values, width, height, min, max, color, options) {
    const segments = buildPathSegments(values, width, height, min, max);
    const dash = options?.dashArray ? ` stroke-dasharray="${options.dashArray}"` : '';
    return segments
      .map((segment) => {
        const d = segment
          .map(([x, y], index) => `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`)
          .join(' ');
        return `<path d="${d}" fill="none" stroke="${color}" stroke-width="${options?.strokeWidth || 2.2}" stroke-linecap="round" stroke-linejoin="round"${dash} />`;
      })
      .join('');
  }

  function buildBandPolygon(lower, upper, width, height, min, max) {
    const low = normalizeSeries(lower);
    const high = normalizeSeries(upper);
    const size = Math.max(low.length, high.length);
    if (!size) return '';
    const step = size > 1 ? width / (size - 1) : width;
    const upperPoints = [];
    const lowerPoints = [];
    for (let index = 0; index < size; index += 1) {
      const lowValue = low[index];
      const highValue = high[index];
      if (lowValue === null || highValue === null) continue;
      const x = index * step;
      upperPoints.push([x, scaleY(highValue, min, max, height)]);
      lowerPoints.push([x, scaleY(lowValue, min, max, height)]);
    }
    if (upperPoints.length < 2 || lowerPoints.length < 2) return '';
    const polygonPoints = upperPoints.concat(lowerPoints.reverse());
    return `<polygon fill="${WEATHER_COLORS.corridor}" points="${polygonPoints
      .map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`)
      .join(' ')}" />`;
  }

  function buildBalanceBars(values, width, height, min, max) {
    const series = normalizeSeries(values);
    if (!series.length) return '';
    const step = width / Math.max(series.length, 1);
    const barWidth = Math.max(3, step * 0.66);
    const zeroY = scaleY(0, min, max, height);
    return series
      .map((value, index) => {
        if (value === null) return '';
        const x = index * step + (step - barWidth) / 2;
        const y = scaleY(value, min, max, height);
        const rectY = Math.min(y, zeroY);
        const rectHeight = Math.max(1, Math.abs(zeroY - y));
        const fill = value >= 0 ? WEATHER_COLORS.balancePositive : WEATHER_COLORS.balanceNegative;
        return `<rect x="${x.toFixed(2)}" y="${rectY.toFixed(2)}" width="${barWidth.toFixed(
          2
        )}" height="${rectHeight.toFixed(2)}" rx="2" fill="${fill}" opacity="0.48" />`;
      })
      .join('');
  }

  function buildStackedBars(stacks, width, height, maxValue) {
    const items = (stacks || []).filter((item) => hasSeriesData(item.values));
    if (!items.length || !maxValue) return '';
    const size = items[0].values.length;
    if (!size) return '';
    const step = width / Math.max(size, 1);
    const barWidth = Math.max(3, step * 0.7);
    let html = '';
    for (let index = 0; index < size; index += 1) {
      let cursor = height;
      items.forEach((item) => {
        const value = asNumber(item.values[index]);
        if (value === null || value <= 0) return;
        const rectHeight = (value / maxValue) * height;
        cursor -= rectHeight;
        const x = index * step + (step - barWidth) / 2;
        html += `<rect x="${x.toFixed(2)}" y="${cursor.toFixed(2)}" width="${barWidth.toFixed(
          2
        )}" height="${rectHeight.toFixed(2)}" rx="2" fill="${item.color}" opacity="0.78" />`;
      });
    }
    return html;
  }

  function buildSvg(width, height, min, max, inner) {
    return `
      <svg viewBox="0 0 ${width} ${height}" class="forecast-svg weather-svg" role="img" aria-label="Weather analysis chart">
        ${buildGrid(width, height, min, max, 4)}
        ${inner}
      </svg>
    `;
  }

  function renderLegend(items) {
    const rows = (items || [])
      .filter((item) => item && item.label)
      .map((item) => `<span class="pill"><span class="weather-swatch" style="background:${item.color}"></span>${escapeHtml(item.label)}</span>`)
      .join('');
    return rows ? `<div class="pill-row mt-3">${rows}</div>` : '';
  }

  function renderFactPills(items, className) {
    const rows = (items || []).filter(Boolean);
    if (!rows.length) return '';
    return `
      <div class="${escapeHtml(className || 'weather-chart-facts mt-3')}">
        ${rows.map((item) => `<span class="pill weather-fact-pill">${escapeHtml(item)}</span>`).join('')}
      </div>
    `;
  }

  function buildUnavailableChart(title, reason, options) {
    const lead = options?.lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(options.lead)}</div>` : '';
    return `
      <article class="card weather-chart-card ${escapeHtml(options?.cardClass || '')}">
        <div class="section-head">
          <div>
            <p class="section-kicker">${escapeHtml(title)}</p>
          </div>
        </div>
        ${lead}
        <div class="empty-state mt-3">${escapeHtml(reason || 'График недоступен для этого формата прогноза.')}</div>
      </article>
    `;
  }

  function buildLineChart(title, seriesItems, options) {
    const active = (seriesItems || []).filter((item) => hasSeriesData(item.values));
    if (!active.length) {
      return buildUnavailableChart(title, options?.reason, options);
    }
    const allValues = collectFiniteValues(active.map((item) => item.values));
    if (!allValues.length) {
      return buildUnavailableChart(title, options?.reason, options);
    }
    const width = 720;
    const height = 240;
    const min = options?.min !== undefined ? Number(options.min) : Math.min(...allValues);
    const max = options?.max !== undefined ? Number(options.max) : Math.max(...allValues);
    const inner = active
      .map((item) => renderLineSeries(item.values, width, height, min, max, item.color, item))
      .join('');
    const svg = buildSvg(width, height, min, max, inner);
    const legend = renderLegend(active.map((item) => ({label: item.label, color: item.color})));
    const lead = options?.lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(options.lead)}</div>` : '';
    const facts = renderFactPills(options?.facts);
    const note = options?.note ? `<div class="muted mt-2">${escapeHtml(options.note)}</div>` : '';
    return `
      <article class="card weather-chart-card ${escapeHtml(options?.cardClass || '')}">
        <div class="section-head">
          <div>
            <p class="section-kicker">${escapeHtml(title)}</p>
          </div>
        </div>
        ${lead}
        <div class="forecast-chart mt-3">${svg}</div>
        ${legend}
        ${facts}
        ${note}
      </article>
    `;
  }

  function buildBalanceChart(title, balance, lower, upper, options) {
    if (!hasSeriesData(balance) || !hasSeriesData(lower) || !hasSeriesData(upper)) {
      return buildUnavailableChart(title, 'Коридор неопределённости нельзя построить для этого прогноза.', options);
    }
    const allValues = collectFiniteValues([balance, lower, upper, [0]]);
    const width = 720;
    const height = 240;
    const min = Math.min(...allValues);
    const max = Math.max(...allValues);
    const band = buildBandPolygon(lower, upper, width, height, min, max);
    const zeroLine = renderLineSeries(
      new Array(normalizeSeries(balance).length).fill(0),
      width,
      height,
      min,
      max,
      WEATHER_COLORS.lineMuted,
      {dashArray: '6 4', strokeWidth: 1.6}
    );
    const bars = buildBalanceBars(balance, width, height, min, max);
    const line = renderLineSeries(balance, width, height, min, max, WEATHER_COLORS.balance, {
      strokeWidth: 2.2,
    });
    const svg = buildSvg(width, height, min, max, `${band}${bars}${zeroLine}${line}`);
    const lead = options?.lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(options.lead)}</div>` : '';
    const facts = renderFactPills(options?.facts);
    return `
      <article class="card weather-chart-card ${escapeHtml(options?.cardClass || '')}">
        <div class="section-head">
          <div>
            <p class="section-kicker">${escapeHtml(title)}</p>
          </div>
        </div>
        ${lead}
        <div class="forecast-chart mt-3">${svg}</div>
        ${renderLegend([
          {label: 'Баланс', color: WEATHER_COLORS.balance},
          {label: 'Коридор неопределённости', color: WEATHER_COLORS.solarImproved},
        ])}
        ${facts}
      </article>
    `;
  }

  function buildStackedChart(title, stacks, overlay, reason, note, options) {
    const activeStacks = (stacks || []).filter((item) => hasSeriesData(item.values));
    const overlayValues = overlay?.values || [];
    if (!activeStacks.length) {
      return buildUnavailableChart(title, reason, options);
    }
    const size = activeStacks[0].values.length;
    const totals = Array.from({length: size}, (_, index) =>
      activeStacks.reduce((sum, item) => sum + (asNumber(item.values[index]) || 0), 0)
    );
    const allValues = collectFiniteValues([totals, overlayValues]);
    if (!allValues.length) return buildUnavailableChart(title, reason, options);
    const maxValue = Math.max(...allValues);
    const width = 720;
    const height = 240;
    const bars = buildStackedBars(activeStacks, width, height, maxValue);
    const overlayLine = hasSeriesData(overlayValues)
      ? renderLineSeries(overlayValues, width, height, 0, maxValue, overlay.color, {
          strokeWidth: 2.3,
        })
      : '';
    const svg = buildSvg(width, height, 0, maxValue, `${bars}${overlayLine}`);
    const legendItems = activeStacks.map((item) => ({label: item.label, color: item.color}));
    if (overlay?.label && hasSeriesData(overlayValues)) {
      legendItems.push({label: overlay.label, color: overlay.color});
    }
    const lead = options?.lead ? `<div class="weather-chart-lead mt-2">${escapeHtml(options.lead)}</div>` : '';
    const facts = renderFactPills(options?.facts);
    return `
      <article class="card weather-chart-card ${escapeHtml(options?.cardClass || '')}">
        <div class="section-head">
          <div>
            <p class="section-kicker">${escapeHtml(title)}</p>
          </div>
        </div>
        ${lead}
        <div class="forecast-chart mt-3">${svg}</div>
        ${renderLegend(legendItems)}
        ${facts}
        ${note ? `<div class="muted mt-2">${escapeHtml(note)}</div>` : ''}
      </article>
    `;
  }

  function renderWeatherKpis(target, analysis) {
    if (!target) return;
    const kpis = analysis?.kpis || {};
    const tickCount = Array.isArray(analysis?.series?.tick) ? analysis.series.tick.length : 0;
    const deficitShare = tickCount ? (Number(kpis.deficit_count || 0) / tickCount) * 100 : null;
    const surplusShare = tickCount ? (Number(kpis.surplus_count || 0) / tickCount) * 100 : null;
    const items = [
      {label: 'Средняя генерация', value: formatNum(kpis.avg_generation, 2), tone: 'ok', caption: 'средняя доступная мощность'},
      {label: 'Среднее потребление', value: formatNum(kpis.avg_consumption, 2), tone: 'base', caption: 'сумма доступных нагрузок'},
      {
        label: 'Средний баланс',
        value: formatSignedNum(kpis.avg_balance, 2),
        tone: Number(kpis.avg_balance) < 0 ? 'warn' : 'ok',
        caption: Number(kpis.avg_balance) < -0.1 ? 'в среднем не хватает энергии' : 'баланс без сильного перекоса',
      },
      {
        label: 'Тактов с дефицитом',
        value: formatNum(kpis.deficit_count, 0),
        tone: 'warn',
        caption: deficitShare === null ? 'доля горизонта неизвестна' : `${formatNum(deficitShare, 0)}% горизонта`,
      },
      {
        label: 'Тактов с профицитом',
        value: formatNum(kpis.surplus_count, 0),
        tone: 'ok',
        caption: surplusShare === null ? 'доля горизонта неизвестна' : `${formatNum(surplusShare, 0)}% горизонта`,
      },
      {
        label: 'Отключений ветряка',
        value: formatNum(kpis.wind_off_count, 0),
        tone: Number(kpis.wind_off_count) > 0 ? 'warn' : 'base',
        caption: Number(kpis.wind_off_count) > 0 ? 'ветер выше порога 7' : 'порог отключения не превышен',
      },
      {label: 'Доля солнца', value: kpis.solar_share_pct === null ? '—' : `${formatNum(kpis.solar_share_pct, 1)}%`, tone: 'sun', caption: 'в общей генерации'},
      {label: 'Доля ветра', value: kpis.wind_share_pct === null ? '—' : `${formatNum(kpis.wind_share_pct, 1)}%`, tone: 'wind', caption: 'в общей генерации'},
    ];
    target.innerHTML = `
      <div>
        <p class="section-kicker">Ключевые показатели</p>
        <div class="weather-kpi-grid mt-2">
          ${items
            .map(
              (item) => `
                <div class="metric-card weather-kpi-card weather-kpi-${escapeHtml(item.tone || 'base')}">
                  <span class="metric-label">${escapeHtml(item.label)}</span>
                  <strong>${escapeHtml(item.value)}</strong>
                  <div class="metric-caption">${escapeHtml(item.caption || '')}</div>
                </div>
              `
            )
            .join('')}
        </div>
      </div>
    `;
  }

  function renderWeatherCharts(target, analysis) {
    if (!target) return;
    const chartMeta = analysis?.charts || {};
    const kpis = analysis?.kpis || {};
    const categories = getCategorySeries(analysis);
    const dominantCategory = dominantCategoryLabel(categories);
    const maxDeficit = kpis.max_deficit || {};
    const maxSurplus = kpis.max_surplus || {};
    const primaryCharts = [];
    const secondaryCharts = [];
    const secondaryNotes = [];

    function pushChart(key, html, unavailableNote) {
      const priority = chartMeta[key]?.priority || 'secondary';
      if (priority === 'primary') {
        primaryCharts.push(html);
        return;
      }
      if (chartMeta[key]?.available) {
        secondaryCharts.push(html);
      } else if (unavailableNote) {
        secondaryNotes.push(`<li>${escapeHtml(unavailableNote)}</li>`);
      }
    }

    pushChart(
      'generation_vs_consumption',
      chartMeta.generation_vs_consumption?.available
        ? buildLineChart(
            chartMeta.generation_vs_consumption.title,
            [
              {label: WEATHER_LABELS.generation, values: getSeries(analysis, 'total_generation'), color: WEATHER_COLORS.generation},
              {label: WEATHER_LABELS.consumption, values: getSeries(analysis, 'total_consumption'), color: WEATHER_COLORS.consumption},
            ],
            {
              reason: chartMeta.generation_vs_consumption.reason,
              cardClass: 'weather-chart-wide',
              lead: 'Сначала смотрите этот график: если потребление держится выше генерации, нужны более устойчивые генерирующие лоты, storage или резерв.',
              facts: [
                `Средняя генерация: ${formatNum(kpis.avg_generation, 1)}`,
                `Среднее потребление: ${formatNum(kpis.avg_consumption, 1)}`,
                `Средний баланс: ${formatSignedNum(kpis.avg_balance, 1)}`,
              ],
            }
          )
        : buildUnavailableChart(
            chartMeta.generation_vs_consumption?.title || 'Генерация и потребление',
            chartMeta.generation_vs_consumption?.reason,
            {cardClass: 'weather-chart-wide'}
          )
    );

    pushChart(
      'balance_uncertainty',
      chartMeta.balance_uncertainty?.available
        ? buildBalanceChart(
            chartMeta.balance_uncertainty.title,
            getSeries(analysis, 'balance'),
            getSeries(analysis, 'balance_min'),
            getSeries(analysis, 'balance_max'),
            {
              cardClass: 'weather-chart-wide',
              lead: 'Ниже нуля система уходит в дефицит. Коридор показывает, насколько этот вывод устойчив к неопределённости прогноза.',
              facts: [
                maxDeficit?.tick === null || maxDeficit?.tick === undefined
                  ? null
                  : `Макс. дефицит: ${formatSignedNum(maxDeficit.value, 1)} на такте ${maxDeficit.tick}`,
                maxSurplus?.tick === null || maxSurplus?.tick === undefined
                  ? null
                  : `Макс. профицит: ${formatSignedNum(maxSurplus.value, 1)} на такте ${maxSurplus.tick}`,
              ],
            }
          )
        : buildUnavailableChart(
            chartMeta.balance_uncertainty?.title || 'Баланс',
            chartMeta.balance_uncertainty?.reason,
            {cardClass: 'weather-chart-wide'}
          )
    );

    pushChart(
      'wind_forecast',
      chartMeta.wind_forecast?.available
        ? buildLineChart(
            chartMeta.wind_forecast.title,
            [
              {label: WEATHER_LABELS.wind_avg, values: getSeries(analysis, 'wind_avg'), color: WEATHER_COLORS.wind},
              {
                label: 'Порог отключения 7',
                values: new Array(getSeries(analysis, 'wind_avg').length).fill(7),
                color: WEATHER_COLORS.windThreshold,
                dashArray: '6 4',
                strokeWidth: 1.7,
              },
              {label: WEATHER_LABELS.wind_gen, values: getSeries(analysis, 'wind_gen'), color: WEATHER_COLORS.windGen},
            ],
            {
              reason: chartMeta.wind_forecast.reason,
              lead: 'Проверяйте этот график перед покупкой wind-лотов: превышение порога 7 выключает ветряк.',
              facts: [
                `Отключений: ${formatNum(kpis.wind_off_count, 0)}`,
                `Полная мощность: ${formatNum(kpis.wind_full_power_count, 0)} тактов`,
              ],
              note: 'При среднем ветре выше 7 ветряк отключается.',
            }
          )
        : buildUnavailableChart(
            chartMeta.wind_forecast?.title || 'Прогноз ветра',
            chartMeta.wind_forecast?.reason
          )
    );

    pushChart(
      'consumption_categories',
      chartMeta.consumption_categories?.available
        ? buildStackedChart(
            chartMeta.consumption_categories.title,
            [
              {label: WEATHER_LABELS.hospital, values: categories.hospital, color: WEATHER_COLORS.hospital},
              {label: WEATHER_LABELS.factory, values: categories.factory, color: WEATHER_COLORS.factory},
              {label: WEATHER_LABELS.house_a, values: categories.house_a, color: WEATHER_COLORS.houseA},
              {label: WEATHER_LABELS.house_b, values: categories.house_b, color: WEATHER_COLORS.houseB},
              {label: WEATHER_LABELS.office, values: categories.office, color: WEATHER_COLORS.office},
              {label: WEATHER_LABELS.house_load, values: categories.house_load, color: WEATHER_COLORS.houseLoad},
            ],
            null,
            chartMeta.consumption_categories.reason,
            null,
            {
              lead: 'Смотрите, какая категория сильнее всего тянет потребление вверх в рискованных тактах.',
              facts: [dominantCategory ? `Крупнейшая нагрузка: ${dominantCategory}` : null],
            }
          )
        : buildUnavailableChart(
            chartMeta.consumption_categories?.title || 'Потребление по категориям',
            chartMeta.consumption_categories?.reason
          )
    );

    pushChart(
      'solar_models',
      chartMeta.solar_models?.available
        ? buildLineChart(
            chartMeta.solar_models.title,
            [
              {label: WEATHER_LABELS.solar_simple, values: getSeries(analysis, 'solar_simple'), color: WEATHER_COLORS.solarSimple},
              {label: WEATHER_LABELS.solar_improved, values: getSeries(analysis, 'solar_improved'), color: WEATHER_COLORS.solarImproved},
            ],
            {
              reason: chartMeta.solar_models.reason,
              facts: ['Нужны раздельные east/west ряды'],
            }
          )
        : buildUnavailableChart(
            chartMeta.solar_models?.title || 'Солнечная генерация: simple vs improved',
            chartMeta.solar_models?.reason,
            {lead: 'Этот график сравнивает грубую и улучшенную солнечную модель.'}
          ),
      chartMeta.solar_models?.reason
    );

    pushChart(
      'solar_activity',
      chartMeta.solar_activity?.available
        ? buildLineChart(
            chartMeta.solar_activity.title,
            [
              {label: WEATHER_LABELS.sun_east, values: getSeries(analysis, 'sun_east'), color: WEATHER_COLORS.solarEast},
              {label: WEATHER_LABELS.sun_west, values: getSeries(analysis, 'sun_west'), color: WEATHER_COLORS.solarWest},
            ],
            {
              reason: chartMeta.solar_activity.reason,
              facts: ['Показывает перекос между восточной и западной стороной'],
            }
          )
        : buildUnavailableChart(
            chartMeta.solar_activity?.title || 'Солнечная активность east/west',
            chartMeta.solar_activity?.reason,
            {lead: 'Доступен только если прогноз хранит отдельные ряды east и west.'}
          ),
      chartMeta.solar_activity?.reason
    );

    pushChart(
      'generation_types',
      chartMeta.generation_types?.available
        ? buildStackedChart(
            chartMeta.generation_types.title,
            [
              {label: 'Солнце', values: getSeries(analysis, 'solar_improved'), color: WEATHER_COLORS.solarImproved},
              {label: 'Ветер', values: getSeries(analysis, 'wind_gen'), color: WEATHER_COLORS.wind},
            ],
            null,
            chartMeta.generation_types.reason,
            null,
            {
              facts: [
                kpis.solar_share_pct === null ? null : `Солнце: ${formatNum(kpis.solar_share_pct, 1)}%`,
                kpis.wind_share_pct === null ? null : `Ветер: ${formatNum(kpis.wind_share_pct, 1)}%`,
              ],
            }
          )
        : buildUnavailableChart(
            chartMeta.generation_types?.title || 'Сравнение источников генерации',
            chartMeta.generation_types?.reason
          ),
      chartMeta.generation_types?.reason
    );

    pushChart(
      'source_mix',
      chartMeta.source_mix?.available
        ? buildStackedChart(
            chartMeta.source_mix.title,
            [
              {label: 'Солнце', values: getSeries(analysis, 'solar_improved'), color: WEATHER_COLORS.solarImproved},
              {label: 'Ветер', values: getSeries(analysis, 'wind_gen'), color: WEATHER_COLORS.wind},
            ],
            {
              label: WEATHER_LABELS.consumption,
              values: getSeries(analysis, 'total_consumption'),
              color: WEATHER_COLORS.consumption,
            },
            chartMeta.source_mix.reason,
            null,
            {
              lead: 'Сопоставляйте суммарную генерацию с потреблением, чтобы понять, нужен ли ещё резерв поверх текущего профиля.',
              facts: [
                `Ключевой риск-такт: ${formatTickList(
                  [maxDeficit?.tick].filter((tick) => tick !== null && tick !== undefined)
                )}`,
              ],
            }
          )
        : buildUnavailableChart(
            chartMeta.source_mix?.title || 'Структура генерации и потребление',
            chartMeta.source_mix?.reason
          ),
      chartMeta.source_mix?.reason
    );

    target.innerHTML = `
      <div>
        <p class="section-kicker">Основные графики</p>
        <div class="weather-chart-lead mt-2">Сначала смотрите на разрыв между генерацией и потреблением, затем проверяйте баланс и только после этого разбирайте ветер и структуру нагрузки.</div>
        <div class="weather-charts-grid mt-2">${primaryCharts.join('')}</div>
        ${
          secondaryCharts.length
            ? `
              <details class="card weather-fold mt-4">
                <summary>Дополнительные графики</summary>
                <div class="weather-charts-grid mt-3">${secondaryCharts.join('')}</div>
              </details>
            `
            : ''
        }
        ${
          secondaryNotes.length
            ? `
              <details class="card weather-fold mt-3">
                <summary>Что недоступно в этом формате</summary>
                <div class="risk-block mt-3">
                  <ul class="stack gap-1">${secondaryNotes.join('')}</ul>
                </div>
              </details>
            `
            : ''
        }
      </div>
    `;
  }

  function renderWeatherDecisionSupport(target, analysis) {
    if (!target) return;
    const decision = analysis?.decision_support || {};
    const cards = Array.isArray(decision.cards) ? decision.cards : [];
    target.innerHTML = `
      <div>
        <p class="section-kicker">Что это значит для покупки</p>
        <div class="risk-block weather-headline-block mt-2"><strong>${escapeHtml(decision.headline || 'Рекомендации пока недоступны.')}</strong></div>
        <div class="weather-decision-grid mt-2">
          ${
            cards.length
              ? cards
                  .map(
                    (card) => `
                      <article class="card weather-decision-card weather-decision-${escapeHtml(card.tone || 'base')}">
                        <p class="section-kicker">${escapeHtml(card.title || 'Рекомендация')}</p>
                        <div class="muted mt-2">${escapeHtml(card.text || '')}</div>
                      </article>
                    `
                  )
                  .join('')
              : '<article class="card"><div class="muted">Пока нет отдельных рекомендаций по покупке.</div></article>'
          }
        </div>
      </div>
    `;
  }

  function buildTableNote(row) {
    const notes = [];
    if (row.positive_count !== undefined) notes.push(`положительных тактов: ${formatNum(row.positive_count, 0)}`);
    if (row.wind_off_count !== undefined) notes.push(`отключений: ${formatNum(row.wind_off_count, 0)}`);
    if (row.full_power_count !== undefined) notes.push(`полная мощность: ${formatNum(row.full_power_count, 0)}`);
    return notes.join(' · ');
  }

  function renderWeatherTable(title, rows) {
    const body = (rows || [])
      .map(
        (row) => `
          <tr>
            <td>${escapeHtml(row.label || row.key || '—')}</td>
            <td>${escapeHtml(formatNum(row.mean, 2))}</td>
            <td>${escapeHtml(formatNum(row.min, 2))}</td>
            <td>${escapeHtml(formatNum(row.max, 2))}</td>
            <td>${escapeHtml(buildTableNote(row) || '—')}</td>
          </tr>
        `
      )
      .join('');
    return `
      <article class="card">
        <p class="section-kicker">${escapeHtml(title)}</p>
        <div class="table-wrap mt-2">
          <table class="table">
            <thead><tr><th>Ряд</th><th>Среднее</th><th>Мин</th><th>Макс</th><th>Комментарий</th></tr></thead>
            <tbody>${body || '<tr><td colspan="5" class="muted">Таблица пока недоступна.</td></tr>'}</tbody>
          </table>
        </div>
      </article>
    `;
  }

  function renderWeatherTables(target, analysis) {
    if (!target) return;
    const tables = analysis?.tables || {};
    target.innerHTML = `
      <div>
        <details class="card weather-fold" open>
          <summary>Таблицы и сводные значения</summary>
          <div class="weather-charts-grid mt-3">
            ${renderWeatherTable('Сводная статистика', tables.main_stats || [])}
            ${renderWeatherTable('Статистика генераторов', tables.generator_stats || [])}
          </div>
        </details>
      </div>
    `;
  }

  function renderWeatherInsights(target, analysis) {
    if (!target) return;
    const insights = Array.isArray(analysis?.insights) ? analysis.insights.filter(Boolean) : [];
    target.innerHTML = `
      <div>
        <p class="section-kicker">Выводы</p>
        <div class="weather-insight-list mt-2">
          ${
            insights.length
              ? insights.map((item) => `<article class="card weather-insight-card">${escapeHtml(item)}</article>`).join('')
              : '<article class="card weather-insight-card">Пока нет выводов по этому прогнозу.</article>'
          }
        </div>
      </div>
    `;
  }

  function renderWeatherAnalysis(target, analysis) {
    if (!target) return;
    if (!analysis || !analysis.series) {
      target.innerHTML = '<div class="empty-state">Weather analysis пока недоступен.</div>';
      return;
    }
    target.innerHTML = `
      <div class="weather-analysis stack gap-4">
        <div data-weather-overview></div>
        <div data-weather-kpis></div>
        <div data-weather-decision></div>
        <div data-weather-charts></div>
        <div data-weather-tables></div>
        <div data-weather-insights></div>
      </div>
    `;
    renderWeatherOverview(target.querySelector('[data-weather-overview]'), analysis);
    renderWeatherKpis(target.querySelector('[data-weather-kpis]'), analysis);
    renderWeatherDecisionSupport(target.querySelector('[data-weather-decision]'), analysis);
    renderWeatherCharts(target.querySelector('[data-weather-charts]'), analysis);
    renderWeatherTables(target.querySelector('[data-weather-tables]'), analysis);
    renderWeatherInsights(target.querySelector('[data-weather-insights]'), analysis);
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
          <td>${escapeHtml(row.label || row.key || '—')}</td>
          <td>${escapeHtml(formatNum((row.stats || {}).min, 3))}</td>
          <td>${escapeHtml(formatNum((row.stats || {}).max, 3))}</td>
          <td>${escapeHtml(formatNum((row.stats || {}).avg, 3))}</td>
          <td>${escapeHtml(formatNum((row.stats || {}).median, 3))}</td>
        </tr>
      `)
      .join('');
    const warnings = (payload.quality?.warnings || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
    const problems = (payload.quality?.problem_columns || []).join(', ');
    const empty = (payload.quality?.empty_columns || []).join(', ');
    const tickRange = payload.mapped_tick_range_label || `${payload.tick_from ?? '—'}–${payload.tick_to ?? '—'}`;
    const rawColumns = Array.isArray(payload.raw_csv_columns) ? payload.raw_csv_columns : [];
    const usedRawColumns = Array.isArray(payload.used_raw_columns) ? payload.used_raw_columns : [];
    const unsupportedColumns = Array.isArray(payload.unsupported_raw_columns) ? payload.unsupported_raw_columns : [];
    const mappings = Array.isArray(payload.column_mapping_rows) ? payload.column_mapping_rows : [];
    const coverageRows = Array.isArray(payload.object_coverage_rows) ? payload.object_coverage_rows : [];
    const mappingBlocks = mappings.length
      ? mappings.map((row) => `<div class="key-value"><span><code>${escapeHtml(row.raw_name)}</code></span><strong>${escapeHtml(row.interpreted_meaning || row.canonical_key || '—')}</strong></div>`).join('')
      : '<div class="muted">Явный mapping не требуется или пока не рассчитан.</div>';
    const coverageHtml = coverageRows.length
      ? coverageRows.map((row) => `<tr><td>${escapeHtml(row.object_type_name || row.object_type_code || '—')}</td><td>${escapeHtml(row.status || '—')}</td><td>${escapeHtml(((row.required_profiles || []).join(', ')) || '—')}</td><td>${escapeHtml(((row.missing_profiles || []).join(', ')) || '—')}</td></tr>`).join('')
      : '<tr><td colspan="4" class="muted">Покрытие по объектам пока недоступно.</td></tr>';

    target.innerHTML = `
      <div class="risk-block">
        <strong>${escapeHtml(title)}</strong>
        <div class="mt-2">Источник: ${escapeHtml(payload.source_kind || '—')}</div>
        <div>Горизонт: ${escapeHtml(tickRange)}</div>
        <div>Средний ветер: ${escapeHtml(formatNum(payload.avg_wind, 2))}</div>
        <div>Средняя освещённость: ${escapeHtml(formatNum(payload.avg_illumination, 2))}</div>
        <div>Средняя цена рынка: ${escapeHtml(formatNum(payload.avg_market_price, 2))}</div>
        <div>Колонки исходного CSV: ${escapeHtml(String(rawColumns.length))}</div>
        <div>Используемые колонки CSV: ${escapeHtml(String(usedRawColumns.length))}</div>
        <div>Лишние колонки CSV: ${escapeHtml(String(unsupportedColumns.length))}</div>
        <div class="mt-3">${escapeHtml(payload.quality?.text || '')}</div>
        ${problems ? `<div class="mt-2">Проблемные ряды: ${escapeHtml(problems)}</div>` : ''}
        ${empty ? `<div class="mt-2">Пустые колонки: ${escapeHtml(empty)}</div>` : ''}
        ${warnings ? `<ul class="stack gap-1 mt-3">${warnings}</ul>` : ''}
      </div>
      <div class="forecast-columns-grid mt-3">
        <article class="card">
          <p class="section-kicker">Исходный CSV</p>
          <div class="muted mt-2">${rawColumns.length ? escapeHtml(rawColumns.join(', ')) : 'Для встроенного прогноза исходные CSV-колонки не используются.'}</div>
        </article>
        <article class="card">
          <p class="section-kicker">Сопоставление</p>
          <div class="mapping-list mt-2">${mappingBlocks}</div>
        </article>
        <article class="card">
          <p class="section-kicker">Лишние колонки CSV</p>
          <div class="muted mt-2">${unsupportedColumns.length ? escapeHtml(unsupportedColumns.join(', ')) : 'Лишних колонок нет.'}</div>
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
          <thead><tr><th>Тип объекта</th><th>Покрытие</th><th>Нужно</th><th>Не хватает</th></tr></thead>
          <tbody>${coverageHtml}</tbody>
        </table>
      </div>
      <div class="mt-4">
        <p class="section-kicker">Анализ прогноза погоды</p>
        <div data-weather-analysis class="mt-2"></div>
      </div>
    `;
  }

  function buildLine(points, width, height, min, max, color) {
    return renderLineSeries(points, width, height, min, max, color, {});
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
        ${buildLine(wind, width, height, min, max, WEATHER_COLORS.wind)}
        ${buildLine(illumination, width, height, min, max, WEATHER_COLORS.solarImproved)}
        ${buildLine(market, width, height, min, max, WEATHER_COLORS.balanceNegative)}
        ${buildLine(load, width, height, min, max, WEATHER_COLORS.consumption)}
      </svg>
      <div class="pill-row mt-3">
        <span class="pill">Бирюзовый: ветер</span>
        <span class="pill">Жёлтый: освещённость</span>
        <span class="pill">Красный: цена рынка</span>
        ${selectedLoad ? `<span class="pill">Синий: ${escapeHtml(selectedLoad)}</span>` : ''}
      </div>
    `;
  }

  function renderForecastDiagnostics(target, title, payload) {
    renderSummary(target, title, payload);
    renderWeatherAnalysis(target.querySelector('[data-weather-analysis]'), payload.weather_analysis || null);
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
      renderForecastDiagnostics(out, 'Диагностика', {quality: {text: 'Сначала выберите прогноз.'}, series_stats: {}});
      return;
    }

    setSelectedForecast(forecastId);
    out.innerHTML = '<div class="risk-block">Собираю диагностику...</div>';
    if (withChart) {
      chart.innerHTML = '<div class="muted">Готовлю график...</div>';
    }
    const data = await loadForecastDetails(forecastId);
    if (!data.ok) {
      renderForecastDiagnostics(out, 'Ошибка', {quality: {text: errorMessage(data)}, series_stats: {}});
      if (withChart) {
        chart.innerHTML = `<div class="muted">${escapeHtml(errorMessage(data))}</div>`;
      }
      return;
    }

    renderForecastDiagnostics(out, `Диагностика прогноза #${forecastId}`, data.item?.summary || {});
    if (withChart) {
      renderChart(chart, data.item?.periods || []);
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    const out = document.getElementById('forecastOut');
    const form = document.getElementById('forecastForm');
    const activeForecastId = Number((window.IES_FORECAST_CENTER || {}).activeForecastId || 0);
    const activeWeatherAnalysis = (window.IES_FORECAST_CENTER || {}).activeWeatherAnalysis || null;

    renderWeatherAnalysis(
      document.getElementById('activeForecastWeatherAnalysis'),
      activeWeatherAnalysis
    );

    if (activeForecastId > 0) {
      setSelectedForecast(activeForecastId);
    }

    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(form);
      out.innerHTML = '<div class="risk-block">Загрузка прогноза...</div>';
      const data = await apiFetchJson('/api/forecast/upload', {method: 'POST', body: formData});
      if (!data.ok) {
        renderForecastDiagnostics(out, 'Ошибка загрузки', {quality: {text: errorMessage(data)}, series_stats: {}});
        return;
      }
      renderForecastDiagnostics(out, 'Прогноз загружен', data.summary || {});
      renderWeatherAnalysis(
        document.getElementById('activeForecastWeatherAnalysis'),
        data.summary?.weather_analysis || null
      );
      setSelectedForecast(Number(data.item?.id || 0));
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

  window.IESForecastCenter = {
    renderWeatherAnalysis,
    renderWeatherOverview,
    renderWeatherKpis,
    renderWeatherDecisionSupport,
    renderWeatherCharts,
    renderWeatherTables,
    renderWeatherInsights,
  };
})();
