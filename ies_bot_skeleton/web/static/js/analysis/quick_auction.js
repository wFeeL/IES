(function () {
  const api = window.IESApi || {};
  const apiFetchJson = api.apiFetchJson;
  const errorMessage = api.errorMessage || ((data) => data?.error?.message || 'Неизвестная ошибка');
  if (typeof apiFetchJson !== 'function') {
    return;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function cfg() {
    return window.IES_QUICK_AUCTION || {};
  }

  const state = {
    ranking: [],
    visibleRanking: [],
    shortlistSuggested: [],
    strategy: null,
  };

  function csrfToken() {
    return cfg().csrfToken || window.IES_CSRF_TOKEN || '';
  }

  function storageKey() {
    return `ies-quick-auction-${cfg().sessionId || 0}`;
  }

  function shortlistKey() {
    return `ies-shortlist-${cfg().sessionId || 0}`;
  }

  function formatNumber(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return '0';
    }
    const fixed = number.toFixed(Math.max(0, Number(digits || 0)));
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

  function toNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function getShortlist() {
    try {
      return JSON.parse(localStorage.getItem(shortlistKey()) || '[]').map((value) => Number(value));
    } catch (_) {
      return [];
    }
  }

  function setShortlist(ids) {
    const normalized = Array.from(
      new Set((ids || []).map((value) => Number(value)).filter((value) => Number.isFinite(value) && value > 0))
    );
    localStorage.setItem(shortlistKey(), JSON.stringify(normalized));
    return normalized;
  }

  function updateHeroBudget(snapshot) {
    const budgetTotal = Number(snapshot?.budget_total);
    const spentTotal = Number(snapshot?.spent_total);
    const remainingBudget = Number(snapshot?.remaining_budget);
    const boughtLotsCount = Number(snapshot?.bought_lots_count);
    const totalNode = document.querySelector('[data-session-budget-total]');
    const spentNode = document.querySelector('[data-session-spent-total]');
    const remainingNode = document.querySelector('[data-session-remaining-budget]');
    const boughtNode = document.querySelector('[data-session-bought-count]');
    if (totalNode && Number.isFinite(budgetTotal)) {
      totalNode.textContent = formatNumber(budgetTotal, 1);
    }
    if (spentNode && Number.isFinite(spentTotal)) {
      spentNode.textContent = formatNumber(spentTotal, 1);
    }
    if (remainingNode && Number.isFinite(remainingBudget)) {
      remainingNode.textContent = formatNumber(remainingBudget, 1);
    }
    if (boughtNode && Number.isFinite(boughtLotsCount)) {
      boughtNode.textContent = String(Math.max(0, Math.trunc(boughtLotsCount)));
    }
  }

  function persistState() {
    const payload = {
      currentLotId: $('currentLotId')?.value || '',
      purchasePriceInput: $('purchasePriceInput')?.value || '',
      sort: $('qaSort')?.value || 'utility_desc',
      minUtility: $('qaMinUtility')?.value || '',
      maxRisk: $('qaMaxRisk')?.value || '',
      shortlistOnly: $('qaShortlistOnly')?.value || '0',
    };
    localStorage.setItem(storageKey(), JSON.stringify(payload));
  }

  function restoreState() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return;
      const payload = JSON.parse(raw);
      if ($('currentLotId')) $('currentLotId').value = payload.currentLotId || '';
      if ($('purchasePriceInput')) $('purchasePriceInput').value = payload.purchasePriceInput || '';
      if ($('qaSort')) $('qaSort').value = payload.sort || 'utility_desc';
      if ($('qaMinUtility')) $('qaMinUtility').value = payload.minUtility || '';
      if ($('qaMaxRisk')) $('qaMaxRisk').value = payload.maxRisk || '';
      if ($('qaShortlistOnly')) $('qaShortlistOnly').value = payload.shortlistOnly || '0';
    } catch (_) {}
  }

  function setStatus(text) {
    if ($('qaStatus')) $('qaStatus').textContent = text;
  }

  const initialDisabled = {
    evalCurrent: Boolean($('evalCurrent')?.disabled),
    refreshRanking: Boolean($('refreshRanking')?.disabled),
    qaApplyFilters: Boolean($('qaApplyFilters')?.disabled),
    buyCurrentLot: Boolean($('buyCurrentLot')?.disabled),
  };

  function setBusy(busy) {
    const controls = ['evalCurrent', 'refreshRanking', 'qaApplyFilters', 'buyCurrentLot'];
    controls.forEach((id) => {
      const node = $(id);
      if (!node) {
        return;
      }
      node.disabled = Boolean(busy || initialDisabled[id]);
    });
  }

  function netProfit(row) {
    return Number((row.financial_breakdown || {}).result?.net_profit || row.net_profit || 0);
  }

  function grossProfitBeforeBid(row) {
    return Number(
      (row.decision_summary || {}).gross_expected_profit_before_bid ||
      (row.financial_breakdown || {}).result?.gross_profit_before_bid ||
      row.gross_profit_before_bid ||
      0
    );
  }

  function remainingBudget(row) {
    return Number(
      (row.decision_summary || {}).budget_remaining ||
      (row.portfolio_context || {}).remaining_budget ||
      0
    );
  }

  function riskValue(row) {
    return Number((row.financial_breakdown || {}).losses_and_risks?.risk_total || row.risk || 0);
  }

  function workingBid(row) {
    return Number(
      row.recommended_bid ||
      (row.decision_summary || {}).recommended_bid ||
      row.working_bid ||
      (row.decision_summary || {}).working_bid ||
      0
    );
  }

  function workingBidReason(row) {
    return (
      row.recommended_bid_reason ||
      (row.decision_summary || {}).recommended_bid_reason ||
      row.working_bid_short_reason ||
      row.working_bid_reason ||
      (row.decision_summary || {}).working_bid_reason ||
      row.risk_commentary ||
      row.explanation ||
      ''
    );
  }

  function marketBid(row) {
    return Number(row?.price || row?.current_bid || 0);
  }

  function compactReason(reason) {
    const text = String(reason || '').trim();
    if (!text) {
      return '';
    }
    if (text.length <= 96) {
      return text;
    }
    return `${text.slice(0, 93)}...`;
  }

  function fullReason(row) {
    const reasons = Array.isArray(row?.reasons) ? row.reasons.slice(0, 2).join('; ') : '';
    return String(reasons || workingBidReason(row) || '').trim();
  }

  function shortReason(row) {
    const shortLabel = String(row?.working_bid_short_reason || '').trim();
    if (shortLabel) {
      return shortLabel;
    }
    return compactReason(fullReason(row));
  }

  function budgetAdjustedBid(row) {
    return Number(
      (row.decision_summary || {}).max_bid ||
      row.max_bid ||
      (row.decision_summary || {}).budget_adjusted_bid ||
      row.budget_adjusted_bid ||
      0
    );
  }

  function profitAfterBid(row, bid) {
    return grossProfitBeforeBid(row) - Math.max(0, Number(bid || 0));
  }

  function budgetLeftAfterBid(row, bid) {
    return Math.max(0, remainingBudget(row) - Math.max(0, Number(bid || 0)));
  }

  function structureText(row) {
    const items = Array.isArray(row?.structure_items) ? row.structure_items : [];
    const labels = items
      .map((item) => String(item?.label || '').trim())
      .filter(Boolean);
    if (labels.length) {
      return labels.join(', ');
    }
    return String(row?.structure || 'Пустой лот');
  }

  function structureCellHtml(row) {
    const items = Array.isArray(row?.structure_items) ? row.structure_items : [];
    const fullText = structureText(row);
    if (items.length) {
      const chips = items
        .map((item) => `<span class="lot-chip">${escapeHtml(item?.label || '—')}</span>`)
        .join('');
      return `<div class="lot-chip-wrap" title="${escapeHtml(fullText)}">${chips}</div>`;
    }
    const safeText = escapeHtml(fullText);
    return `<span class="text-clamp-2" title="${safeText}">${safeText}</span>`;
  }

  function strategyNames(row) {
    if (row?.display_title) return row.display_title;
    const labels = Array.isArray(row?.lot_labels) ? row.lot_labels : [];
    if (labels.length) return labels.join(' + ');
    return '—';
  }

  function strategyLotsCount(row) {
    return Array.isArray(row?.lot_ids) ? row.lot_ids.length : 0;
  }

  function strategyRowPrice(row) {
    return Number(row?.working_bid || row?.total_price || 0);
  }

  function strategyRowProfit(row) {
    return Number(row?.total_profit || row?.net_profit_base || 0);
  }

  function strategyMetricsLine(row) {
    if (strategyLotsCount(row) <= 1) {
      return `цена: ${formatNumber(strategyRowPrice(row), 1)} · прибыль: ${formatNumber(strategyRowProfit(row), 2)}`;
    }
    return (
      `общая цена: ${formatNumber(strategyRowPrice(row), 1)} · ` +
      `общая прибыль: ${formatNumber(strategyRowProfit(row), 2)} · ` +
      `синергия: ${formatNumber(row?.synergy_score || 0, 2)}`
    );
  }

  function renderStrategyBreakdown(row) {
    const breakdown = Array.isArray(row?.lot_bid_breakdown) ? row.lot_bid_breakdown : [];
    if (!breakdown.length) {
      return '';
    }
    const lines = breakdown
      .map((item) => {
        const label = item?.lot_label || `${item?.lot_name || 'Лот'} (${Number(item?.lot_id || 0)})`;
        const price = Number(
          item?.recommended_bid ||
          item?.allocated_working_bid ||
          item?.budget_adjusted_bid ||
          item?.allocated_target_bid ||
          item?.price ||
          0
        );
        const profit = Number(item?.profit || item?.allocated_net_profit || 0);
        return `<li>${escapeHtml(label)} — цена: ${formatNumber(price, 1)}, прибыль: ${formatNumber(profit, 2)}</li>`;
      })
      .join('');
    return `<ul class="strategy-lot-list mt-2">${lines}</ul>`;
  }

  function renderStrategyRow(row, title) {
    if (!row) {
      return `<div class="muted">${title}: нет доступной альтернативы.</div>`;
    }
    return `
      <article class="card">
        <p class="section-kicker">${title}</p>
        <strong>${strategyNames(row)}</strong>
        <div class="muted mt-2">${strategyMetricsLine(row)}</div>
        ${renderStrategyBreakdown(row)}
      </article>
    `;
  }

  function renderStrategySnapshot(snapshot) {
    const content = $('qaStrategyContent');
    const status = $('qaStrategyStatus');
    if (!content) return;
    if (!snapshot) {
      content.hidden = true;
      if (status) status.textContent = 'Стратегия пока недоступна.';
      return;
    }
    const scenarios = snapshot.scenarios || {};
    const blocks = [
      {title: 'Текущий лучший ход', row: scenarios.full_budget?.best_combination || snapshot.best_combination},
      {title: 'Plan B', row: scenarios.full_budget?.plan_b || snapshot.plan_b},
      {title: 'Plan C', row: scenarios.full_budget?.plan_c || snapshot.plan_c},
      {title: 'После покупки', row: scenarios.after_purchase?.best_combination},
      {title: 'Если лучший лот ушёл', row: scenarios.after_loss?.best_combination},
    ];
    content.innerHTML = blocks.map((block) => renderStrategyRow(block.row, block.title)).join('');
    content.hidden = false;
    if (status) status.hidden = true;
  }

  async function loadStrategySnapshot() {
    const url = cfg().strategyUrl;
    if (!url) return;
    const data = await apiFetchJson(url, {method: 'GET', headers: {'X-CSRFToken': csrfToken()}});
    if (!data.ok) {
      if ($('qaStrategyStatus')) $('qaStrategyStatus').textContent = errorMessage(data);
      return;
    }
    state.strategy = data.item || null;
    renderStrategySnapshot(state.strategy);
  }

  function bySort(a, b, key) {
    if (key === 'profit_desc') {
      return netProfit(b) - netProfit(a);
    }
    if (key === 'risk_asc') {
      return riskValue(a) - riskValue(b);
    }
    if (key === 'bid_desc') {
      return workingBid(b) - workingBid(a);
    }
    return Number(b.summary_score || 0) - Number(a.summary_score || 0);
  }

  function filterAndSort(rows) {
    const minUtilityRaw = $('qaMinUtility')?.value || '';
    const maxRiskRaw = $('qaMaxRisk')?.value || '';
    const sort = $('qaSort')?.value || 'utility_desc';
    const shortlistOnly = $('qaShortlistOnly')?.value === '1';
    const shortlist = getShortlist();
    const minUtility = Number(minUtilityRaw);
    const maxRisk = Number(maxRiskRaw);

    return (rows || [])
      .filter((row) => {
        const lotId = Number(row.lot_id || 0);
        if (shortlistOnly && !shortlist.includes(lotId)) return false;
        if (minUtilityRaw && Number(row.summary_score || 0) < minUtility) return false;
        if (maxRiskRaw && riskValue(row) > maxRisk) return false;
        return true;
      })
      .slice()
      .sort((a, b) => bySort(a, b, sort));
  }

  function setCurrentLinks(lotId) {
    const lotLink = $('openCurrentLot');
    if (lotLink) lotLink.href = lotId ? `/lots/item/${lotId}` : '#';
  }

  function setActiveAuctionItem(lotId) {
    const targetId = Number(lotId || 0);
    document.querySelectorAll('.auction-item').forEach((item) => {
      const rowId = Number(item.dataset.lotId || 0);
      item.classList.toggle('active', targetId > 0 && rowId === targetId);
    });
  }

  function rankingItemByLotId(lotId) {
    const targetId = Number(lotId || 0);
    if (!targetId) return null;
    return state.ranking.find((row) => Number(row.lot_id || 0) === targetId) || null;
  }

  function selectedDecisionItem() {
    return rankingItemByLotId(Number($('currentLotId')?.value || 0));
  }

  function syncPurchasePriceInput(lotId, force = false) {
    const input = $('purchasePriceInput');
    if (!input) return;
    const ranked = rankingItemByLotId(lotId);
    const fallback = document.querySelector(`.auction-item[data-lot-id="${lotId}"]`)?.dataset.currentBid;
    const nextValue = ranked ? workingBid(ranked) : toNumber(fallback);
    if (force || !input.value) {
      input.value = formatNumber(nextValue, 1);
    }
    setCurrentLinks(lotId);
  }

  function updateLiveBidMetrics(item) {
    if (!item) {
      return;
    }
    const input = $('purchasePriceInput');
    const rawValue = input?.value || '';
    const enteredBid = rawValue ? toNumber(rawValue) : workingBid(item);
    const recommended = workingBid(item);
    const grossProfit = grossProfitBeforeBid(item);
    const profit = profitAfterBid(item, enteredBid);
    const budgetLeft = budgetLeftAfterBid(item, enteredBid);
    const profitDelta = profitAfterBid(item, recommended) - profit;
    const budgetDelta = budgetLeftAfterBid(item, recommended) - budgetLeft;
    if ($('qaBidProfit')) $('qaBidProfit').textContent = formatNumber(profit, 2);
    if ($('qaBudgetLeft')) $('qaBudgetLeft').textContent = formatNumber(budgetLeft, 1);

    let advice = String(
      item?.budget_preservation_note ||
      (item?.decision_summary || {}).budget_preservation_note ||
      'Оставшийся бюджет можно использовать позже.'
    ).trim();
    if (enteredBid > recommended + 1e-9) {
      advice +=
        ` По сравнению с рекомендуемой ставкой вы теряете ` +
        `${formatNumber(profitDelta, 2)} прибыли и ${formatNumber(Math.max(0, budgetDelta), 1)} бюджета.`;
    }
    if (enteredBid < recommended - 1e-9 && enteredBid > 0) {
      advice +=
        ` Вы оставляете ещё ${formatNumber(Math.max(0, budgetLeft - budgetLeftAfterBid(item, recommended)), 1)} бюджета в резерве.`;
    }
    if ($('qaBidAdvice')) $('qaBidAdvice').textContent = advice;

    let warning = '';
    if (enteredBid <= 0) {
      warning = 'Ставка должна быть больше нуля, иначе купить лот нельзя.';
    } else if (profit <= 0) {
      warning = 'Предупреждение: после введённой ставки ожидаемая прибыль становится неположительной.';
    } else if (enteredBid > budgetAdjustedBid(item) + 1e-9) {
      warning = `Предупреждение: введённая цена выше максимальной ставки ${formatNumber(budgetAdjustedBid(item), 1)}.`;
    }
    if ($('qaBidWarning')) $('qaBidWarning').textContent = warning;
    if ($('qaBidWarning')) $('qaBidWarning').hidden = !warning;
    if (grossProfit <= 0 && !warning && $('qaBidWarning')) {
      $('qaBidWarning').textContent = '';
    }
  }

  function updateDecisionPanel(item) {
    if (!item) return;
    $('qaScore').textContent = formatNumber(item.summary_score, 2);
    $('qaRisk').textContent = formatNumber(riskValue(item), 2);
    $('qaBid').textContent = formatNumber(workingBid(item), 1);
    if ($('qaBudgetBid')) $('qaBudgetBid').textContent = formatNumber(budgetAdjustedBid(item), 1);
    const reasons = Array.isArray(item.reasons) ? item.reasons.slice(0, 3).join('; ') : '';
    const systemMessage = String((item.system_check || {}).message || '');
    $('qaCommentary').textContent = reasons || workingBidReason(item) || systemMessage || 'Нет комментария.';
    if (workingBid(item) <= 0) {
      $('qaCommentary').textContent = workingBidReason(item) || systemMessage || 'Рекомендуемая ставка недоступна для текущего лота.';
    }
    const lotId = Number(item.lot_id || 0);
    $('currentLotId').value = String(lotId || '');
    setCurrentLinks(lotId);
    setActiveAuctionItem(lotId);
    syncPurchasePriceInput(lotId, true);
    updateLiveBidMetrics(item);
  }

  async function evalLot(lotId) {
    const id = Number(lotId || 0);
    if (!id) return;
    setBusy(true);
    setStatus(`Оцениваю лот ${id}...`);
    try {
      const data = await apiFetchJson(`/api/lots/${id}/evaluate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({}),
      });
      if (!data.ok) {
        setStatus(errorMessage(data));
        return;
      }

      updateDecisionPanel(data.item);
      setStatus(
        `Лот ${id} пересчитан. Полезность ${formatNumber(data.item.summary_score, 2)}, ` +
        `рекомендуемая ставка ${formatNumber(workingBid(data.item), 1)}.`
      );
    } catch (_) {
      setStatus('Не удалось пересчитать лот из-за сетевой ошибки.');
    } finally {
      setBusy(false);
    }
  }

  function rowActionsHtml(lotId) {
    return `
      <div class="qa-row-actions">
        <button class="btn btn-secondary quickEvalRow" data-lot-id="${lotId}" type="button">Оценить</button>
        <button class="btn btn-secondary quickBuyRow" data-lot-id="${lotId}" type="button">Купить</button>
        <a class="btn btn-secondary" href="/lots/item/${lotId}">Открыть лот</a>
      </div>
    `;
  }

  function syncAuctionListWithRanking(rows) {
    const list = $('auctionLots');
    if (!list) return;
    const rankingRows = Array.isArray(rows) ? rows : [];
    const byId = new Map();
    list.querySelectorAll('.auction-item').forEach((item) => {
      const lotId = Number(item.dataset.lotId || 0);
      if (lotId > 0) {
        byId.set(lotId, item);
      }
    });

    const fragment = document.createDocumentFragment();
    rankingRows.forEach((row, index) => {
      const lotId = Number(row?.lot_id || 0);
      if (!lotId) {
        return;
      }
      const existing = byId.get(lotId);
      const item = existing || document.createElement('li');
      item.className = 'auction-item';
      item.dataset.lotId = String(lotId);
      item.dataset.hotkey = String(index + 1);
      item.dataset.currentBid = String(marketBid(row));
      item.innerHTML = `
        <strong>${index + 1}.</strong>
        <span class="text-clamp-2" title="${escapeHtml(row?.name || `Лот ${lotId}`)}">${escapeHtml(row?.name || `Лот ${lotId}`)}</span>
        <span class="muted auction-item-bid">(${formatNumber(marketBid(row), 1)})</span>
      `;
      fragment.appendChild(item);
    });

    list.innerHTML = '';
    if (fragment.childNodes.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'muted';
      empty.textContent = 'Нет доступных лотов.';
      list.appendChild(empty);
      if ($('qaBidWarning')) {
        $('qaBidWarning').textContent = '';
        $('qaBidWarning').hidden = true;
      }
      setActiveAuctionItem(0);
      return;
    }
    list.appendChild(fragment);

    const selectedLotId = Number($('currentLotId')?.value || 0);
    const stillVisible = rankingRows.some((row) => Number(row?.lot_id || 0) === selectedLotId);
    setActiveAuctionItem(stillVisible ? selectedLotId : 0);
  }

  function renderRanking(rows) {
    const body = $('rankingTableBody');
    if (!body) return;
    const filtered = filterAndSort(rows);
    body.innerHTML = '';

    state.visibleRanking = filtered;
    syncAuctionListWithRanking(filtered);

    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="9" class="muted">Нет строк после фильтрации.</td></tr>';
      $('currentLotId').value = '';
      if ($('purchasePriceInput')) $('purchasePriceInput').value = '';
      if ($('qaBidProfit')) $('qaBidProfit').textContent = '—';
      if ($('qaBudgetLeft')) $('qaBudgetLeft').textContent = '—';
      if ($('qaBidAdvice')) $('qaBidAdvice').textContent = 'Оставшийся бюджет можно использовать позже.';
      if ($('qaBidWarning')) {
        $('qaBidWarning').textContent = '';
        $('qaBidWarning').hidden = true;
      }
      setCurrentLinks(0);
      setStatus('После фильтрации подходящих лотов не осталось.');
      return;
    }

    filtered.forEach((row, index) => {
      const tr = document.createElement('tr');
      const lotId = Number(row?.lot_id || 0);
      const bid = workingBid(row);
      const budgetBid = budgetAdjustedBid(row);
      const fullReasonText = fullReason(row);
      const shortReasonText = shortReason(row);
      const lotName = escapeHtml(row?.name || `Лот ${lotId || '—'}`);
      const fitStatus = String(row?.connection_fit_status || '').trim();
      const fitBadge = fitStatus && fitStatus !== 'neutral'
        ? `<span class=\"lot-bid-meta\">fit ${escapeHtml(fitStatus)}</span>`
        : '';
      tr.dataset.lotId = String(row.lot_id || '');
      tr.innerHTML = `
        <td class="num">${index + 1}</td>
        <td class="col-text qa-name-cell">
          <strong class="text-clamp-2" title="${lotName}">${lotName}</strong>
          <div class="table-secondary">Лот #${lotId || '—'}</div>
        </td>
        <td class="col-text qa-structure-cell">
          ${structureCellHtml(row)}
        </td>
        <td class="num">${formatNumber(row.summary_score, 2)}</td>
        <td class="num">${formatNumber(netProfit(row), 2)}</td>
        <td class="num">${formatNumber(riskValue(row), 2)}</td>
        <td class="lot-bid-cell">
          <div class="lot-bid-stack">
            <strong>${formatNumber(bid, 1)}</strong>
            ${
              bid > 0
                ? `<span class="lot-bid-meta" title="${escapeHtml(fullReasonText || `Максимальная ставка: ${formatNumber(budgetBid, 1)}`)}">max ${formatNumber(budgetBid, 1)}</span>${fitBadge}`
                : `<span class="lot-bid-reason text-clamp-2" title="${escapeHtml(fullReasonText)}">${escapeHtml(shortReasonText || 'Нет рекомендуемой ставки')}</span>`
            }
          </div>
        </td>
        <td class="col-text qa-reason-cell"><span class="text-clamp-2" title="${escapeHtml(fullReasonText)}">${escapeHtml(shortReasonText || '—')}</span></td>
        <td class="actions-col qa-actions-cell">${rowActionsHtml(row.lot_id)}</td>
      `;
      body.appendChild(tr);
    });

    const selectedLotId = Number($('currentLotId')?.value || 0);
    const selectedVisible = filtered.some((row) => Number(row?.lot_id || 0) === selectedLotId);
    if (!selectedVisible && filtered.length) {
      updateDecisionPanel(filtered[0]);
    } else if (selectedVisible) {
      setActiveAuctionItem(selectedLotId);
    }

    body.querySelectorAll('.quickEvalRow').forEach((button) => {
      button.addEventListener('click', async () => {
        $('currentLotId').value = button.dataset.lotId;
        syncPurchasePriceInput(button.dataset.lotId, true);
        await evalLot(button.dataset.lotId);
        await refreshRanking(Number(button.dataset.lotId || 0));
      });
    });

    body.querySelectorAll('.quickBuyRow').forEach((button) => {
      button.addEventListener('click', async () => {
        $('currentLotId').value = button.dataset.lotId;
        syncPurchasePriceInput(button.dataset.lotId, true);
        await buyCurrentLot();
      });
    });
  }

  async function refreshRanking(preferredLotId) {
    setBusy(true);
    setStatus('Обновляю shortlist...');
    try {
      const params = new URLSearchParams();
      params.set('status', 'available');
      params.set('sort', $('qaSort')?.value || 'utility_desc');
      if ($('qaMinUtility')?.value) params.set('utility_min', $('qaMinUtility').value);
      if ($('qaMaxRisk')?.value) params.set('risk_max', $('qaMaxRisk').value);

      const data = await apiFetchJson(`/api/sessions/${cfg().sessionId}/lots/analytics?${params.toString()}`, {
        headers: {'X-CSRFToken': csrfToken()},
      });
      if (!data.ok) {
        setStatus(errorMessage(data));
        return;
      }
      state.ranking = Array.isArray(data.items) ? data.items : [];
      if (state.shortlistSuggested.length) {
        const available = new Set(state.ranking.map((row) => Number(row.lot_id || 0)));
        setShortlist(state.shortlistSuggested.filter((id) => available.has(Number(id))));
        state.shortlistSuggested = [];
      } else {
        const available = new Set(state.ranking.map((row) => Number(row.lot_id || 0)));
        setShortlist(getShortlist().filter((id) => available.has(Number(id))));
      }
      renderRanking(state.ranking);
      const selectedLotId = Number(preferredLotId ?? $('currentLotId')?.value ?? 0);
      const selected = rankingItemByLotId(selectedLotId) || state.ranking[0] || null;
      if (selected) {
        updateDecisionPanel(selected);
      } else {
        $('currentLotId').value = '';
        if ($('purchasePriceInput')) $('purchasePriceInput').value = '';
        if ($('qaBidProfit')) $('qaBidProfit').textContent = '—';
        if ($('qaBudgetLeft')) $('qaBudgetLeft').textContent = '—';
        if ($('qaBidAdvice')) $('qaBidAdvice').textContent = 'Оставшийся бюджет можно использовать позже.';
        if ($('qaBidWarning')) {
          $('qaBidWarning').textContent = '';
          $('qaBidWarning').hidden = true;
        }
        setCurrentLinks(0);
        setActiveAuctionItem(0);
      }
      setStatus(`Получено лотов: ${state.ranking.length}.`);
    } catch (_) {
      setStatus('Не удалось обновить shortlist из-за сетевой ошибки.');
    } finally {
      setBusy(false);
    }
  }

  async function recalculateAllLots(preferredLotId) {
    setBusy(true);
    setStatus('Пересчитываю все лоты...');
    try {
      const recalc = await apiFetchJson(`/api/sessions/${cfg().sessionId}/recalculate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({}),
      });
      if (!recalc.ok) {
        setStatus(errorMessage(recalc));
        return;
      }
      state.shortlistSuggested = Array.isArray(recalc?.meta?.shortlist_suggested_ids)
        ? recalc.meta.shortlist_suggested_ids.map((value) => Number(value || 0)).filter((value) => value > 0)
        : [];
      updateHeroBudget(recalc?.meta || {});
      if (recalc?.strategy) {
        state.strategy = recalc.strategy;
        renderStrategySnapshot(state.strategy);
      }
      await refreshRanking(preferredLotId);
      setStatus(`Пересчитано лотов: ${Number(recalc?.meta?.count || 0)}.`);
    } catch (_) {
      setStatus('Не удалось пересчитать лоты из-за сетевой ошибки.');
    } finally {
      setBusy(false);
    }
  }

  async function buyCurrentLot() {
    const lotId = Number($('currentLotId')?.value || 0);
    if (!lotId) {
      setStatus('Сначала выберите лот для покупки.');
      return;
    }
    const priceRaw = $('purchasePriceInput')?.value || '';
    const purchasePrice = Number(priceRaw);
    if (!Number.isFinite(purchasePrice) || purchasePrice <= 0) {
      setStatus('Цена покупки должна быть числом больше нуля.');
      return;
    }

    setBusy(true);
    setStatus(`Покупаю лот ${lotId} по цене ${formatNumber(purchasePrice, 1)}...`);
    try {
      const data = await apiFetchJson(`/api/lots/${lotId}/buy`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({purchase_price: purchasePrice}),
      });
      if (!data.ok) {
        setStatus(errorMessage(data));
        return;
      }
      updateHeroBudget(data?.item || {});
      const remaining = formatNumber(data?.item?.remaining_budget, 1);
      const boughtLotId = Number(data?.item?.lot_id || lotId);
      setShortlist(getShortlist().filter((id) => Number(id) !== boughtLotId));
      const refresh = data?.refresh || null;
      if (refresh?.meta) {
        updateHeroBudget(refresh.meta);
        state.shortlistSuggested = Array.isArray(refresh?.meta?.shortlist_suggested_ids)
          ? refresh.meta.shortlist_suggested_ids.map((value) => Number(value || 0)).filter((value) => value > 0)
          : [];
      }
      if (refresh?.strategy) {
        state.strategy = refresh.strategy;
        renderStrategySnapshot(state.strategy);
      }
      await refreshRanking(0);
      const refreshedCount = Number(refresh?.meta?.count || state.ranking.length || 0);
      setStatus(
        `Лот ${lotId} куплен. Остаток бюджета: ${remaining}. Пересчитано лотов: ${refreshedCount}.`
      );
    } catch (_) {
      setStatus('Не удалось выполнить покупку из-за сетевой ошибки.');
    } finally {
      setBusy(false);
    }
  }

  async function evalCurrent() {
    const lotId = Number($('currentLotId')?.value || 0);
    if (!lotId) return;
    await evalLot(lotId);
    await refreshRanking(lotId);
  }

  function isHotkeyTarget(event) {
    const target = event?.target;
    if (!target || !(target instanceof Element)) {
      return false;
    }
    if (target.closest('[contenteditable=""], [contenteditable="true"]')) {
      return true;
    }
    const tag = String(target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      return true;
    }
    return false;
  }

  window.addEventListener('DOMContentLoaded', () => {
    restoreState();
    state.ranking = Array.isArray(cfg().initialRanking) ? cfg().initialRanking : [];
    renderRanking(state.ranking);
    loadStrategySnapshot();
    const initialLotId = Number($('currentLotId')?.value || 0);
    const selected =
      rankingItemByLotId(initialLotId) ||
      rankingItemByLotId(Number(state.visibleRanking[0]?.lot_id || 0)) ||
      null;
    if (selected) {
      updateDecisionPanel(selected);
    }

    $('auctionLots')?.addEventListener('click', (event) => {
      const target = event?.target;
      if (!(target instanceof Element)) {
        return;
      }
      const item = target.closest('.auction-item');
      if (!item) {
        return;
      }
      const lotId = Number(item.dataset.lotId || 0);
      if (!lotId) {
        return;
      }
      const ranked = rankingItemByLotId(lotId);
      if (ranked) {
        updateDecisionPanel(ranked);
      } else {
        $('currentLotId').value = String(lotId);
        setActiveAuctionItem(lotId);
        setCurrentLinks(lotId);
        syncPurchasePriceInput(lotId, true);
      }
      persistState();
    });

    $('evalCurrent')?.addEventListener('click', evalCurrent);
    $('refreshRanking')?.addEventListener('click', async () => {
      await recalculateAllLots(Number($('currentLotId')?.value || 0));
      persistState();
    });
    $('buyCurrentLot')?.addEventListener('click', async () => {
      await buyCurrentLot();
      persistState();
    });
    $('qaApplyFilters')?.addEventListener('click', () => {
      renderRanking(state.ranking);
      persistState();
    });

    $('currentLotId')?.addEventListener('input', () => {
      const item = selectedDecisionItem();
      if (item) {
        setActiveAuctionItem(Number(item.lot_id || 0));
        setCurrentLinks(Number(item.lot_id || 0));
        syncPurchasePriceInput(Number(item.lot_id || 0), false);
        updateLiveBidMetrics(item);
      }
      persistState();
    });
    $('purchasePriceInput')?.addEventListener('input', () => {
      const item = selectedDecisionItem();
      if (item) {
        updateLiveBidMetrics(item);
      }
      persistState();
    });
    $('qaSort')?.addEventListener('change', persistState);
    $('qaMinUtility')?.addEventListener('input', persistState);
    $('qaMaxRisk')?.addEventListener('input', persistState);
    $('qaShortlistOnly')?.addEventListener('change', persistState);

    const initialRawLotId = $('currentLotId')?.value;
    if (initialRawLotId) {
      syncPurchasePriceInput(Number(initialRawLotId || 0), true);
      setActiveAuctionItem(Number(initialRawLotId || 0));
    }
    if (!initialRawLotId && state.visibleRanking.length) {
      const firstId = Number(state.visibleRanking[0]?.lot_id || 0);
      if (firstId > 0) {
        $('currentLotId').value = String(firstId);
        const ranked = rankingItemByLotId(firstId);
        if (ranked) {
          updateDecisionPanel(ranked);
        }
      }
    }

    document.addEventListener('keydown', async (event) => {
      if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) {
        return;
      }
      if (isHotkeyTarget(event)) {
        return;
      }

      if (/^[1-9]$/.test(event.key)) {
        const index = Number(event.key) - 1;
        const row = state.visibleRanking[index];
        const lotId = Number(row?.lot_id || 0);
        if (lotId > 0) {
          event.preventDefault();
          const ranked = rankingItemByLotId(lotId);
          if (ranked) {
            updateDecisionPanel(ranked);
          }
          setStatus(`Выбран лот по горячей клавише ${event.key}.`);
          persistState();
        }
        return;
      }
      if (event.key.toLowerCase() === 'e') {
        event.preventDefault();
        await evalCurrent();
        return;
      }
      if (event.key.toLowerCase() === 'r') {
        event.preventDefault();
        await recalculateAllLots(Number($('currentLotId')?.value || 0));
        return;
      }
      if (event.key.toLowerCase() === 'b') {
        event.preventDefault();
        await buyCurrentLot();
      }
    });
  });
})();
