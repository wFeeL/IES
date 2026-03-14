(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Не удалось загрузить стратегию.');

  if (typeof apiFetchJson !== 'function') {
    return;
  }

  function formatNumber(value, digits) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric)) {
      return Number(0).toFixed(digits);
    }
    return numeric.toFixed(digits);
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
    const labels = Array.isArray(row?.lot_names) ? row.lot_names : [];
    return labels.length ? labels.join(' + ') : '—';
  }

  function renderLotBidBreakdown(row) {
    const breakdown = Array.isArray(row?.lot_bid_breakdown) ? row.lot_bid_breakdown : [];
    if (!breakdown.length) {
      return '';
    }
    return breakdown
      .map((item) => {
        const lotName = item?.lot_name ? escapeHtml(item.lot_name) : `Лот ${Number(item?.lot_id || 0)}`;
        return `${lotName}: ${formatNumber(item?.allocated_target_bid, 1)} ` +
          `(лимит ${formatNumber(item?.allocated_hard_ceiling_bid, 1)}, ` +
          `синергия ${formatNumber(item?.synergy_allocated, 1)})`;
      })
      .join('<br/>');
  }

  function renderSessionList(rows, emptyMessage, detailBuilder) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return `<li class="muted">${escapeHtml(emptyMessage)}</li>`;
    }
    return rows
      .map((row) => {
        const breakdownHtml = renderLotBidBreakdown(row);
        return (
          `<li><strong>${escapeHtml(names(row))}</strong><br/>` +
          `<span class="muted">${detailBuilder(row)}</span>` +
          (breakdownHtml ? `<div class="muted mt-1">${breakdownHtml}</div>` : '') +
          `</li>`
        );
      })
      .join('');
  }

  function renderSessionStrategy(item, remainingBudget) {
    const best = item?.best_combination;
    if (!best) {
      return '<div class="muted mt-3">Недостаточно доступных лотов для расчёта стратегии.</div>';
    }

    const warning =
      Number(best.target_bid || 0) > Number(remainingBudget || 0)
        ? '<div class="flash flash-warning mt-3">Экономически оправдано, но не помещается в текущий бюджет.</div>'
        : '';

    return `
      <div class="insight-grid mt-3">
        <div class="metric-card"><span class="metric-label">Лучшая комбинация</span><strong>${escapeHtml(names(best))}</strong></div>
        <div class="metric-card"><span class="metric-label">Риск-скорректированная прибыль</span><strong>${formatNumber(best.risk_adjusted_net_profit, 2)}</strong></div>
        <div class="metric-card"><span class="metric-label">Чистая прибыль</span><strong>${formatNumber(best.net_profit_base, 2)}</strong></div>
        <div class="metric-card"><span class="metric-label">Синергия</span><strong>${formatNumber(best.synergy_score, 2)}</strong></div>
        <div class="metric-card"><span class="metric-label">Рабочая ставка</span><strong>${formatNumber(best.target_bid, 1)}</strong></div>
        <div class="metric-card"><span class="metric-label">Ставка с учетом бюджета</span><strong>${formatNumber(best.budget_limited_bid, 1)}</strong></div>
      </div>
      ${warning}
      <div class="grid cols-3 gap-4 mt-4">
        <article class="card">
          <p class="section-kicker">Лучшие одиночные</p>
          <ul class="stack gap-2 mt-2">
            ${renderSessionList(
              item.best_singles,
              'Нет доступных одиночных рекомендаций.',
              (row) => `Прибыль ${formatNumber(row.net_profit_base, 2)}, риск ${formatNumber(row.risk, 2)}`
            )}
          </ul>
        </article>
        <article class="card">
          <p class="section-kicker">Лучшие пары</p>
          <ul class="stack gap-2 mt-2">
            ${renderSessionList(
              item.best_pairs,
              'Пары в бюджете не найдены.',
              (row) => `Синергия ${formatNumber(row.synergy_score, 2)}, прибыль ${formatNumber(row.net_profit_base, 2)}`
            )}
          </ul>
        </article>
        <article class="card">
          <p class="section-kicker">Лучшие группы</p>
          <ul class="stack gap-2 mt-2">
            ${renderSessionList(
              item.best_groups,
              'Группы в бюджете не найдены.',
              (row) => `Цена ${formatNumber(row.total_price, 1)}, прибыль ${formatNumber(row.net_profit_base, 2)}`
            )}
          </ul>
        </article>
      </div>
    `;
  }

  function renderLotPairs(rows) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return '<tr><td colspan="6" class="muted">Лучшие пары не рассчитаны или отсутствуют в бюджете.</td></tr>';
    }
    return rows
      .map((row) => {
        const breakdown = renderLotBidBreakdown(row);
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
