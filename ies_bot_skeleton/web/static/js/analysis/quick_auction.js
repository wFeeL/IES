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
    const totalNode = document.querySelector('[data-session-budget-total]');
    const spentNode = document.querySelector('[data-session-spent-total]');
    const remainingNode = document.querySelector('[data-session-remaining-budget]');
    if (totalNode && Number.isFinite(budgetTotal)) {
      totalNode.textContent = formatNumber(budgetTotal, 1);
    }
    if (spentNode && Number.isFinite(spentTotal)) {
      spentNode.textContent = formatNumber(spentTotal, 1);
    }
    if (remainingNode && Number.isFinite(remainingBudget)) {
      remainingNode.textContent = formatNumber(remainingBudget, 1);
    }
  }

  function persistState() {
    const payload = {
      currentLotId: $('currentLotId')?.value || '',
      currentBid: $('currentBid')?.value || '',
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
      if ($('currentBid')) $('currentBid').value = payload.currentBid || '';
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

  function riskValue(row) {
    return Number((row.financial_breakdown || {}).losses_and_risks?.risk_total || row.risk || 0);
  }

  function workingBid(row) {
    return Number(
      row.working_bid ||
      (row.decision_summary || {}).working_bid ||
      0
    );
  }

  function workingBidReason(row) {
    return (
      row.working_bid_reason ||
      (row.decision_summary || {}).working_bid_reason ||
      row.risk_commentary ||
      row.explanation ||
      ''
    );
  }

  function budgetAdjustedBid(row) {
    return Number(
      (row.decision_summary || {}).budget_adjusted_bid ||
      row.budget_adjusted_bid ||
      0
    );
  }

  function strategyNames(row) {
    if (row?.display_title) return row.display_title;
    const labels = Array.isArray(row?.lot_labels) ? row.lot_labels : [];
    if (labels.length) return labels.join(' + ');
    return '—';
  }

  function renderStrategyRow(row, title) {
    if (!row) {
      return `<div class="muted">${title}: нет доступной альтернативы.</div>`;
    }
    const breakdown = Array.isArray(row?.lot_bid_breakdown) ? row.lot_bid_breakdown : [];
    const lines = breakdown
      .map((item) => `${item.lot_label} — цена: ${formatNumber(item.price, 1)}, прибыль: ${formatNumber(item.profit, 2)}`)
      .join('<br/>');
    return `
      <article class="card">
        <p class="section-kicker">${title}</p>
        <strong>${strategyNames(row)}</strong>
        <div class="muted mt-2">total price: ${formatNumber(row.working_bid || row.total_price, 1)} · total profit: ${formatNumber(row.total_profit || row.net_profit_base, 2)}</div>
        <div class="muted">synergy: ${formatNumber(row.synergy_score, 2)} · utility: ${formatNumber(row.utility_score || row.utility, 2)} · budget fit: ${row?.budget_fit?.is_affordable ? 'fit' : 'tight'}</div>
        <div class="muted mt-2">${row.explanation || row.reason || row.working_bid_reason || ''}</div>
        ${lines ? `<div class="muted mt-2">${lines}</div>` : ''}
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
      {title: 'Current best', row: scenarios.full_budget?.best_combination || snapshot.best_combination},
      {title: 'Plan B', row: scenarios.full_budget?.plan_b || snapshot.plan_b},
      {title: 'Plan C', row: scenarios.full_budget?.plan_c || snapshot.plan_c},
      {title: 'After purchase', row: scenarios.after_purchase?.best_combination},
      {title: 'After loss', row: scenarios.after_loss?.best_combination},
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

  function syncCurrentBid(lotId, force = false) {
    const input = $('currentBid');
    if (!input) return;
    const ranked = rankingItemByLotId(lotId);
    const fallback = document.querySelector(`.auction-item[data-lot-id="${lotId}"]`)?.dataset.currentBid;
    const nextValue = ranked ? workingBid(ranked) : toNumber(fallback);
    if (force || !input.value) {
      input.value = formatNumber(nextValue, 1);
    }
    setCurrentLinks(lotId);
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
      $('qaCommentary').textContent = workingBidReason(item) || systemMessage || 'Рабочая цена недоступна для текущего лота.';
    }
    const lotId = Number(item.lot_id || 0);
    $('currentLotId').value = String(lotId || '');
    setCurrentLinks(lotId);
    setActiveAuctionItem(lotId);
    syncCurrentBid(lotId, true);
  }

  async function persistCurrentBid(lotId) {
    const bidRaw = $('currentBid')?.value || '';
    if (!bidRaw) return true;
    const bid = Number(bidRaw);
    if (!Number.isFinite(bid) || bid <= 0) {
      setStatus('Цена покупки должна быть числом больше нуля.');
      return false;
    }
    const data = await apiFetchJson(`/api/lots/${lotId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({current_bid: bid}),
    });
    if (!data.ok) {
      setStatus(errorMessage(data));
      return false;
    }
    const item = document.querySelector(`.auction-item[data-lot-id="${lotId}"]`);
    if (item) {
      item.dataset.currentBid = String(bid);
      const label = item.querySelector('.auction-item-bid');
      if (label) label.textContent = `(${formatNumber(bid, 1)})`;
    }
    return true;
  }

  async function evalLot(lotId) {
    const id = Number(lotId || 0);
    if (!id) return;
    setBusy(true);
    setStatus(`Оцениваю лот ${id}...`);
    try {
      const bidSaved = await persistCurrentBid(id);
      if (!bidSaved) {
        return;
      }

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
        `рабочая ставка ${formatNumber(workingBid(data.item), 1)}.`
      );
    } catch (_) {
      setStatus('Не удалось пересчитать лот из-за сетевой ошибки.');
    } finally {
      setBusy(false);
    }
  }

  function rowActionsHtml(lotId) {
    return `
      <div class="actions">
        <button class="btn btn-secondary quickEvalRow" data-lot-id="${lotId}" type="button">Оценить</button>
        <a class="btn btn-secondary" href="/lots/item/${lotId}">Открыть лот</a>
        <button class="btn btn-secondary quickBuyRow" data-lot-id="${lotId}" type="button">Купить</button>
      </div>
    `;
  }

  function syncAuctionListWithRanking() {
    const ids = new Set(state.ranking.map((row) => Number(row.lot_id || 0)));
    document.querySelectorAll('.auction-item').forEach((item) => {
      const lotId = Number(item.dataset.lotId || 0);
      const ranked = rankingItemByLotId(lotId);
      if (!ids.has(lotId)) {
        item.remove();
        return;
      }
      if (!ranked) {
        return;
      }
      const nextBid = workingBid(ranked);
      item.dataset.currentBid = String(nextBid);
      const label = item.querySelector('.auction-item-bid');
      if (label) {
        label.textContent = `(${formatNumber(nextBid, 1)})`;
      }
    });

    document.querySelectorAll('.auction-item').forEach((item, index) => {
      item.dataset.hotkey = String(index + 1);
    });
  }

  function renderRanking(rows) {
    const body = $('rankingTableBody');
    if (!body) return;
    const filtered = filterAndSort(rows);
    body.innerHTML = '';

    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="9" class="muted">Нет строк после фильтрации.</td></tr>';
      setStatus('После фильтрации подходящих лотов не осталось.');
      return;
    }

    filtered.forEach((row, index) => {
      const tr = document.createElement('tr');
      const reasons = Array.isArray(row.reasons) ? row.reasons.slice(0, 2).join('; ') : '';
      tr.dataset.lotId = String(row.lot_id || '');
      tr.innerHTML = `
        <td>${index + 1}</td>
        <td>${row.name || row.lot_id}</td>
        <td>${row.structure || '—'}</td>
        <td>${formatNumber(row.summary_score, 2)}</td>
        <td>${formatNumber(netProfit(row), 2)}</td>
        <td>${formatNumber(riskValue(row), 2)}</td>
        <td>${formatNumber(workingBid(row), 1)}</td>
        <td>${reasons || workingBidReason(row)}</td>
        <td>${rowActionsHtml(row.lot_id)}</td>
      `;
      body.appendChild(tr);
    });

    body.querySelectorAll('.quickEvalRow').forEach((button) => {
      button.addEventListener('click', async () => {
        $('currentLotId').value = button.dataset.lotId;
        syncCurrentBid(button.dataset.lotId, true);
        await evalLot(button.dataset.lotId);
        await refreshRanking(Number(button.dataset.lotId || 0));
      });
    });

    body.querySelectorAll('.quickBuyRow').forEach((button) => {
      button.addEventListener('click', async () => {
        $('currentLotId').value = button.dataset.lotId;
        syncCurrentBid(button.dataset.lotId, true);
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
      syncAuctionListWithRanking();
      renderRanking(state.ranking);
      const selectedLotId = Number(preferredLotId ?? $('currentLotId')?.value ?? 0);
      const selected = rankingItemByLotId(selectedLotId) || state.ranking[0] || null;
      if (selected) {
        updateDecisionPanel(selected);
      } else {
        $('currentLotId').value = '';
        if ($('currentBid')) $('currentBid').value = '';
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
      updateHeroBudget({
        remaining_budget: recalc?.meta?.remaining_budget,
      });
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
    const priceRaw = $('currentBid')?.value || '';
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
      setStatus(`Лот ${lotId} куплен. Остаток бюджета: ${remaining}.`);
      await recalculateAllLots(0);
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
    syncAuctionListWithRanking();
    renderRanking(state.ranking);
    loadStrategySnapshot();
    const initialLotId = Number($('currentLotId')?.value || 0);
    const selected = rankingItemByLotId(initialLotId) || state.ranking[0] || null;
    if (selected) {
      updateDecisionPanel(selected);
    }

    document.querySelectorAll('.auction-item').forEach((item) => {
      item.addEventListener('click', () => {
        const lotId = Number(item.dataset.lotId || 0);
        setActiveAuctionItem(lotId);
        $('currentLotId').value = String(lotId || '');
        syncCurrentBid(lotId, true);
        const ranked = rankingItemByLotId(lotId);
        if (ranked) {
          updateDecisionPanel(ranked);
        }
        persistState();
      });
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

    $('currentLotId')?.addEventListener('input', persistState);
    $('currentBid')?.addEventListener('input', persistState);
    $('qaSort')?.addEventListener('change', persistState);
    $('qaMinUtility')?.addEventListener('input', persistState);
    $('qaMaxRisk')?.addEventListener('input', persistState);
    $('qaShortlistOnly')?.addEventListener('change', persistState);

    const initialRawLotId = $('currentLotId')?.value;
    if (initialRawLotId) {
      syncCurrentBid(Number(initialRawLotId || 0), true);
      setActiveAuctionItem(Number(initialRawLotId || 0));
    }
    if (!initialRawLotId && document.querySelector('.auction-item')) {
      document.querySelector('.auction-item').click();
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
        const rows = document.querySelectorAll('.auction-item');
        if (rows[index]) {
          event.preventDefault();
          rows[index].click();
          setStatus(`Выбран лот по горячей клавише ${event.key}.`);
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
