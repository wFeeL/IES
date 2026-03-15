(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Не удалось загрузить стратегию.');

  if (typeof apiFetchJson !== 'function') {
    return;
  }

  const PLAN_TITLES = ['Plan B', 'Plan C'];
  const FALLBACK_SCENARIO_TITLES = {
    full_budget: 'Полный бюджет',
    after_purchase: 'After purchase',
    after_loss: 'After loss',
  };

  function formatNumber(value, digits) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return '0';
    }
    const fixed = numeric.toFixed(Math.max(0, Number(digits || 0)));
    return fixed.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function names(row) {
    if (row?.display_title) {
      return row.display_title;
    }
    const lotLabels = Array.isArray(row?.lot_labels) ? row.lot_labels : [];
    if (lotLabels.length) {
      return lotLabels.join(' + ');
    }
    const lotNames = Array.isArray(row?.lot_names) ? row.lot_names : [];
    const lotIds = Array.isArray(row?.lot_ids) ? row.lot_ids.map((value) => Number(value || 0)) : [];
    if (!lotNames.length) {
      return '—';
    }
    return lotNames.map((label, index) => `${label} (${lotIds[index] || 0})`).join(' + ');
  }

  function renderLotBidBreakdown(row, withProfit) {
    const breakdown = Array.isArray(row?.lot_bid_breakdown) ? row.lot_bid_breakdown : [];
    if (!breakdown.length) {
      return '';
    }
    return breakdown
      .map((item) => {
        const lotLabel = item?.lot_label
          ? escapeHtml(item.lot_label)
          : `${escapeHtml(item?.lot_name || 'Лот')} (${Number(item?.lot_id || 0)})`;
        const purchasePrice = Number(item?.budget_adjusted_bid || item?.allocated_target_bid || 0);
        const profitPart = withProfit
          ? `, прибыль: ${formatNumber(item?.allocated_net_profit, 2)}`
          : '';
        return `${lotLabel} — цена: ${formatNumber(purchasePrice, 1)}${profitPart}`;
      })
      .join('<br/>');
  }

  function renderSessionList(rows, emptyMessage, detailBuilder) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return `<li class="muted">${escapeHtml(emptyMessage)}</li>`;
    }
    return rows
      .map((row) => {
        const breakdownHtml = renderLotBidBreakdown(row, true);
        return (
          `<li><strong>${escapeHtml(names(row))}</strong><br/>` +
          `<span class="muted">${detailBuilder(row)}</span>` +
          `<div class="muted mt-1">Рабочая цена: ${formatNumber(row?.working_bid, 1)} · ` +
          `Осторожная цена: ${formatNumber(row?.cautious_bid, 1)} · ` +
          `Предельная цена: ${formatNumber(row?.hard_ceiling_bid, 1)}</div>` +
          (breakdownHtml ? `<div class="muted mt-1">${breakdownHtml}</div>` : '') +
          `</li>`
        );
      })
      .join('');
  }

  function renderScenarioBlock(scenario, remainingBudget, scenarioKey) {
    const best = scenario?.best_combination;
    const title = scenario?.title || FALLBACK_SCENARIO_TITLES[scenarioKey] || 'Сценарий';
    if (!best) {
      return `
        <article class="card">
          <p class="section-kicker">${escapeHtml(title)}</p>
          <div class="muted mt-2">${escapeHtml(scenario?.note || 'Недостаточно данных для расчёта.')}</div>
        </article>
      `;
    }

    const warning =
      Number(best.working_bid || 0) > Number(remainingBudget || 0)
        ? '<div class="flash flash-warning mt-3">Экономически оправдано, но не помещается в текущий бюджет.</div>'
        : '';

    const alternatives = [scenario?.plan_b, scenario?.plan_c].filter(Boolean);
    const alternativesHtml = alternatives.length
      ? `<ul class="stack gap-1 mt-3">${alternatives
          .map((row, index) => `<li><strong>${PLAN_TITLES[index] || `Plan ${index + 2}`}:</strong> ${escapeHtml(names(row))} · рабочая цена ${formatNumber(row.working_bid, 1)} · прибыль ${formatNumber(row.total_profit ?? row.net_profit_base, 2)}</li>`)
          .join('')}</ul>`
      : '';

    return `
      <article class="card">
        <p class="section-kicker">${escapeHtml(title)}</p>
        <div class="muted mt-2">${escapeHtml(scenario?.note || '')}</div>
        <div class="insight-grid mt-3">
          <div class="metric-card"><span class="metric-label">Лучшая комбинация</span><strong>${escapeHtml(names(best))}</strong></div>
          <div class="metric-card"><span class="metric-label">Риск-скорректированная прибыль</span><strong>${formatNumber(best.risk_adjusted_net_profit, 2)}</strong></div>
          <div class="metric-card"><span class="metric-label">Чистая прибыль</span><strong>${formatNumber(best.total_profit ?? best.net_profit_base, 2)}</strong></div>
          <div class="metric-card"><span class="metric-label">Синергия</span><strong>${formatNumber(best.synergy_score, 2)}</strong></div>
          <div class="metric-card"><span class="metric-label">Рабочая ставка</span><strong>${formatNumber(best.working_bid, 1)}</strong></div>
          <div class="metric-card"><span class="metric-label">Ставка с учетом бюджета</span><strong>${formatNumber(best.budget_adjusted_bid, 1)}</strong></div>
        </div>
        ${warning}
        ${alternativesHtml}
        <div class="grid cols-3 gap-4 mt-4">
          <article class="card">
            <p class="section-kicker">Лучшие одиночные</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(
                scenario.best_singles,
                'Нет доступных одиночных рекомендаций.',
                (row) =>
                  `Название группы - ${escapeHtml(names(row))}: ` +
                  `цена: ${formatNumber(row.working_bid, 1)}, прибыль: ${formatNumber(row.total_profit ?? row.net_profit_base, 2)}`
              )}
            </ul>
          </article>
          <article class="card">
            <p class="section-kicker">Лучшие пары</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(
                scenario.best_pairs,
                'Пары в бюджете не найдены.',
                (row) =>
                  `Название группы - ${escapeHtml(names(row))}: ` +
                  `цена: ${formatNumber(row.working_bid, 1)}, ` +
                  `прибыль: ${formatNumber(row.total_profit ?? row.net_profit_base, 2)}, ` +
                  `синергия: ${formatNumber(row.synergy_score, 2)}`
              )}
            </ul>
          </article>
          <article class="card">
            <p class="section-kicker">Лучшие группы</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(
                scenario.best_groups,
                'Группы в бюджете не найдены.',
                (row) =>
                  `Название группы - ${escapeHtml(names(row))}: ` +
                  `цена: ${formatNumber(row.working_bid || row.total_price, 1)}, ` +
                  `прибыль: ${formatNumber(row.total_profit ?? row.net_profit_base, 2)}`
              )}
            </ul>
          </article>
        </div>
      </article>
    `;
  }

  function renderSessionStrategy(item, remainingBudget) {
    const scenarios = item?.scenarios || {};
    const blocks = [
      {key: 'full_budget', scenario: scenarios.full_budget},
      {key: 'after_purchase', scenario: scenarios.after_purchase},
      {key: 'after_loss', scenario: scenarios.after_loss},
    ].filter((entry) => entry.scenario);
    if (!blocks.length) {
      const best = item?.best_combination;
      if (!best) {
        return '<div class="muted mt-3">Недостаточно доступных лотов для расчёта стратегии.</div>';
      }
      blocks.push({
        key: 'full_budget',
        scenario: {
          title: FALLBACK_SCENARIO_TITLES.full_budget,
          note: 'Стратегия по текущему бюджету.',
          best_singles: item.best_singles || [],
          best_pairs: item.best_pairs || [],
          best_groups: item.best_groups || [],
          best_combination: best,
          plan_b: item.plan_b || null,
          plan_c: item.plan_c || null,
        },
      });
    }
    return `<div class="stack gap-4 mt-3">${blocks
      .map((entry) => renderScenarioBlock(entry.scenario, remainingBudget, entry.key))
      .join('')}</div>`;
  }

  function renderLotPairs(rows) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return '<tr><td colspan="6" class="muted">Лучшие пары не рассчитаны или отсутствуют в бюджете.</td></tr>';
    }
    return rows
      .map((row) => {
        const breakdown = renderLotBidBreakdown(row, true);
        return `
          <tr>
            <td class="col-text">${escapeHtml(names(row))}</td>
            <td class="num">${formatNumber(row.synergy_score, 2)}</td>
            <td class="num">${formatNumber(row.net_profit_base, 2)}</td>
            <td class="num">${formatNumber(row.target_bid, 1)}</td>
            <td class="col-text">${breakdown || '—'}</td>
            <td class="col-text">${escapeHtml(row.reason || '—')}</td>
          </tr>
        `;
      })
      .join('');
  }

  async function loadSessionSnapshot() {
    const card = document.getElementById('strategySnapshotCard');
    if (!card) {
      return;
    }
    const url = card.dataset.strategyUrl;
    if (!url) {
      return;
    }
    const status = document.getElementById('strategySnapshotStatus');
    const content = document.getElementById('strategySnapshotContent');
    const data = await apiFetchJson(url, {method: 'GET'});
    if (!data.ok) {
      if (status) {
        status.textContent = errorMessage(data);
      }
      return;
    }
    if (!content) {
      return;
    }
    content.innerHTML = renderSessionStrategy(data.item || {}, Number(card.dataset.remainingBudget || 0));
    content.hidden = false;
    if (status) {
      status.hidden = true;
    }
  }

  async function loadLotPairs() {
    const card = document.getElementById('lotPairsCard');
    if (!card) {
      return;
    }
    const url = card.dataset.strategyUrl;
    const lotId = Number(card.dataset.lotId || 0);
    if (!url || !lotId) {
      return;
    }
    const body = document.getElementById('lotBestPairsBody');
    if (!body) {
      return;
    }
    const data = await apiFetchJson(url, {method: 'GET'});
    if (!data.ok) {
      body.innerHTML = `<tr><td colspan="6" class="muted">${escapeHtml(errorMessage(data))}</td></tr>`;
      return;
    }
    const rows = Array.isArray(data.item?.best_pairs) ? data.item.best_pairs : [];
    const filtered = rows
      .filter((row) => Array.isArray(row?.lot_ids) && row.lot_ids.map(Number).includes(lotId))
      .sort((left, right) => {
        const leftSynergy = Number(left?.synergy_score || 0);
        const rightSynergy = Number(right?.synergy_score || 0);
        if (rightSynergy !== leftSynergy) {
          return rightSynergy - leftSynergy;
        }
        return Number(right?.risk_adjusted_net_profit || 0) - Number(left?.risk_adjusted_net_profit || 0);
      })
      .slice(0, 5);
    body.innerHTML = renderLotPairs(filtered);
  }

  function boot() {
    loadSessionSnapshot();
    loadLotPairs();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, {once: true});
  } else {
    boot();
  }
})();
