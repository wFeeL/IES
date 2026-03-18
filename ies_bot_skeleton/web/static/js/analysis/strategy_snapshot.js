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
    after_purchase: 'После покупки',
    after_loss: 'После потери лучшего лота',
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

  function compactText(value, maxLength) {
    const text = String(value ?? '').trim();
    if (!text) {
      return '';
    }
    const limit = Math.max(8, Number(maxLength || 120));
    if (text.length <= limit) {
      return text;
    }
    return `${text.slice(0, limit - 1)}...`;
  }

  function rowLotsCount(row) {
    return Array.isArray(row?.lot_ids) ? row.lot_ids.length : 0;
  }

  function rowTitle(row) {
    if (row?.display_title) {
      return String(row.display_title);
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

  function rowPrice(row) {
    return Number(row?.working_bid || row?.total_price || 0);
  }

  function rowProfit(row) {
    return Number(row?.total_profit ?? row?.net_profit_base ?? 0);
  }

  function rowSynergy(row) {
    return Number(row?.synergy_score ?? row?.synergy ?? 0);
  }

  function rowMetrics(row) {
    if (rowLotsCount(row) <= 1) {
      return `цена: ${formatNumber(rowPrice(row), 1)} · прибыль: ${formatNumber(rowProfit(row), 2)}`;
    }
    return (
      `общая цена: ${formatNumber(rowPrice(row), 1)} · ` +
      `общая прибыль: ${formatNumber(rowProfit(row), 2)} · ` +
      `синергия: ${formatNumber(rowSynergy(row), 2)}`
    );
  }

  function renderLotBreakdown(row) {
    const breakdown = Array.isArray(row?.lot_bid_breakdown) ? row.lot_bid_breakdown : [];
    if (!breakdown.length) {
      return '';
    }
    const items = breakdown
      .map((item) => {
        const label = item?.lot_label
          ? escapeHtml(item.lot_label)
          : `${escapeHtml(item?.lot_name || 'Лот')} (${Number(item?.lot_id || 0)})`;
        const price = Number(
          item?.recommended_bid ||
          item?.allocated_working_bid ||
          item?.budget_adjusted_bid ||
          item?.allocated_target_bid ||
          item?.price ||
          0
        );
        const profit = Number(item?.profit ?? item?.allocated_net_profit ?? 0);
        return `<li>${label} — цена: ${formatNumber(price, 1)}, прибыль: ${formatNumber(profit, 2)}</li>`;
      })
      .join('');
    return `<ul class="strategy-lot-list mt-2">${items}</ul>`;
  }

  function renderSessionList(rows, emptyMessage) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return `<li class="muted">${escapeHtml(emptyMessage)}</li>`;
    }
    return rows
      .map((row) => {
        return (
          `<li class="strategy-list-item">` +
          `<strong>${escapeHtml(rowTitle(row))}</strong>` +
          `<span class="muted">${escapeHtml(rowMetrics(row))}</span>` +
          `${renderLotBreakdown(row)}` +
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
      Number(rowPrice(best)) > Number(remainingBudget || 0)
        ? '<div class="flash flash-warning mt-3">Экономически оправдано, но не помещается в текущий бюджет.</div>'
        : '';

    const alternatives = [scenario?.plan_b, scenario?.plan_c].filter(Boolean);
    const alternativesHtml = alternatives.length
      ? `<ul class="stack gap-1 mt-3">${alternatives
          .map((row, index) => `<li><strong>${PLAN_TITLES[index] || `Plan ${index + 2}`}:</strong> ${escapeHtml(rowTitle(row))} · ${escapeHtml(rowMetrics(row))}</li>`)
          .join('')}</ul>`
      : '';

    return `
      <article class="card">
        <p class="section-kicker">${escapeHtml(title)}</p>
        <div class="muted mt-2">${escapeHtml(scenario?.note || '')}</div>
        <div class="mt-3 strategy-list-item">
          <strong>${escapeHtml(rowTitle(best))}</strong>
          <span class="muted">${escapeHtml(rowMetrics(best))}</span>
          ${renderLotBreakdown(best)}
        </div>
        ${warning}
        ${alternativesHtml}
        <div class="grid cols-3 gap-4 mt-4">
          <article class="card">
            <p class="section-kicker">Одиночные</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(scenario.best_singles, 'Нет доступных одиночных рекомендаций.')}
            </ul>
          </article>
          <article class="card">
            <p class="section-kicker">Пары</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(scenario.best_pairs, 'Пары в бюджете не найдены.')}
            </ul>
          </article>
          <article class="card">
            <p class="section-kicker">Группы</p>
            <ul class="stack gap-2 mt-2">
              ${renderSessionList(scenario.best_groups, 'Группы в бюджете не найдены.')}
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
        const reason = compactText(String(row?.reason || ''), 120) || '—';
        return `
          <tr>
            <td class="col-text">${escapeHtml(rowTitle(row))}</td>
            <td class="num">${formatNumber(rowSynergy(row), 2)}</td>
            <td class="num">${formatNumber(rowProfit(row), 2)}</td>
            <td class="num">${formatNumber(rowPrice(row), 1)}</td>
            <td class="col-text">${renderLotBreakdown(row) || '—'}</td>
            <td class="col-text"><span class="text-clamp-2" title="${escapeHtml(String(row?.reason || ''))}">${escapeHtml(reason)}</span></td>
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
        const leftProfit = Number(left?.net_profit_base ?? left?.total_profit ?? 0);
        const rightProfit = Number(right?.net_profit_base ?? right?.total_profit ?? 0);
        if (rightProfit !== leftProfit) {
          return rightProfit - leftProfit;
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
