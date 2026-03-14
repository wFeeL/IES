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

  const state = {
    ranking: [],
  };

  function cfg() {
    return window.IES_QUICK_AUCTION || {};
  }

  function storageKey() {
    return `ies-quick-auction-${cfg().sessionId || 0}`;
  }

  function shortlistKey() {
    return `ies-shortlist-${cfg().sessionId || 0}`;
  }

  function getShortlist() {
    try {
      return JSON.parse(localStorage.getItem(shortlistKey()) || '[]').map((value) => Number(value));
    } catch (_) {
      return [];
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
  };

  function setBusy(busy) {
    const controls = ['evalCurrent', 'refreshRanking', 'qaApplyFilters'];
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

  function hardBid(row) {
    return Number(
      (row.decision_summary || {}).target_bid ||
      (row.decision_summary || {}).hard_bid ||
      row.target_bid ||
      row.recommended_bid_hard ||
      0
    );
  }

  function budgetLimitedBid(row) {
    return Number(
      (row.decision_summary || {}).budget_limited_bid ||
      row.budget_limited_bid ||
      0
    );
  }

  function bySort(a, b, key) {
    if (key === 'profit_desc') {
      return netProfit(b) - netProfit(a);
    }
    if (key === 'risk_asc') {
      return riskValue(a) - riskValue(b);
    }
    if (key === 'bid_desc') {
      return hardBid(b) - hardBid(a);
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
    const buyLink = $('buyCurrentLot');
    if (lotLink) lotLink.href = lotId ? `/lots/item/${lotId}` : '#';
    if (buyLink) buyLink.href = lotId ? `/lots/item/${lotId}/buy` : '#';
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

  function syncCurrentBid(lotId) {
    const row = document.querySelector(`.auction-item[data-lot-id="${lotId}"]`);
    if (!row || !$('currentBid')) return;
    $('currentBid').value = row.dataset.currentBid || '';
    setCurrentLinks(lotId);
  }

  function updateDecisionPanel(item) {
    if (!item) return;
    $('qaScore').textContent = Number(item.summary_score || 0).toFixed(2);
    $('qaRisk').textContent = riskValue(item).toFixed(2);
    $('qaBid').textContent = hardBid(item).toFixed(1);
    if ($('qaBudgetBid')) $('qaBudgetBid').textContent = budgetLimitedBid(item).toFixed(1);
    const reasons = Array.isArray(item.reasons) ? item.reasons.slice(0, 3).join('; ') : '';
    $('qaCommentary').textContent = reasons || item.risk_commentary || item.explanation || 'Нет комментария.';
    const lotId = Number(item.lot_id || 0);
    $('currentLotId').value = String(lotId || '');
    setCurrentLinks(lotId);
    setActiveAuctionItem(lotId);
  }

  async function persistCurrentBid(lotId) {
    const bidRaw = $('currentBid')?.value || '';
    if (!bidRaw) return true;
    const bid = Number(bidRaw);
    if (!Number.isFinite(bid) || bid < 0) {
      setStatus('Текущая ставка должна быть числом не меньше нуля.');
      return false;
    }
    const data = await apiFetchJson(`/api/lots/${lotId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': cfg().csrfToken || window.IES_CSRF_TOKEN || '',
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
      if (label) label.textContent = `(${bid.toFixed(1)})`;
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
          'X-CSRFToken': cfg().csrfToken || window.IES_CSRF_TOKEN || '',
        },
        body: JSON.stringify({}),
      });
      if (!data.ok) {
        setStatus(errorMessage(data));
        return;
      }

      updateDecisionPanel(data.item);
      setStatus(
        `Лот ${id} пересчитан. Полезность ${Number(data.item.summary_score || 0).toFixed(2)}, ` +
        `рабочая ставка ${hardBid(data.item).toFixed(1)}.`
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
        <a class="btn btn-secondary" href="/lots/item/${lotId}/buy">Купить</a>
      </div>
    `;
  }

  function renderRanking(rows) {
    const body = $('rankingTableBody');
    if (!body) return;
    const filtered = filterAndSort(rows);
    body.innerHTML = '';

    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="8" class="muted">Нет строк после фильтрации.</td></tr>';
      setStatus('После фильтрации подходящих лотов не осталось.');
      return;
    }

    filtered.forEach((row, index) => {
      const tr = document.createElement('tr');
      const reasons = Array.isArray(row.reasons) ? row.reasons.slice(0, 2).join('; ') : '';
      tr.dataset.lotId = String(row.lot_id || '');
      tr.innerHTML = `
        <td>${index + 1}</td>
        <td>${row.lot_id}</td>
        <td>${Number(row.summary_score || 0).toFixed(2)}</td>
        <td>${netProfit(row).toFixed(2)}</td>
        <td>${riskValue(row).toFixed(2)}</td>
        <td>${hardBid(row).toFixed(1)}</td>
        <td>${reasons || row.risk_commentary || row.explanation || ''}</td>
        <td>${rowActionsHtml(row.lot_id)}</td>
      `;
      body.appendChild(tr);
    });

    body.querySelectorAll('.quickEvalRow').forEach((button) => {
      button.addEventListener('click', async () => {
        $('currentLotId').value = button.dataset.lotId;
        syncCurrentBid(button.dataset.lotId);
        await evalLot(button.dataset.lotId);
        await refreshRanking(Number(button.dataset.lotId || 0));
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
        headers: {'X-CSRFToken': cfg().csrfToken || window.IES_CSRF_TOKEN || ''},
      });
      if (!data.ok) {
        setStatus(errorMessage(data));
        return;
      }
      state.ranking = Array.isArray(data.items) ? data.items : [];
      renderRanking(state.ranking);
      const selectedLotId = Number(preferredLotId ?? $('currentLotId')?.value ?? 0);
      const selected = rankingItemByLotId(selectedLotId) || state.ranking[0] || null;
      if (selected) {
        updateDecisionPanel(selected);
        syncCurrentBid(selected.lot_id);
      }
      setStatus(`Получено лотов: ${state.ranking.length}.`);
    } catch (_) {
      setStatus('Не удалось обновить shortlist из-за сетевой ошибки.');
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
    const initialLotId = Number($('currentLotId')?.value || 0);
    const selected = rankingItemByLotId(initialLotId) || state.ranking[0] || null;
    if (selected) {
      updateDecisionPanel(selected);
      syncCurrentBid(selected.lot_id);
    }

    document.querySelectorAll('.auction-item').forEach((item, index) => {
      item.addEventListener('click', () => {
        const lotId = Number(item.dataset.lotId || 0);
        setActiveAuctionItem(lotId);
        $('currentLotId').value = String(lotId || '');
        syncCurrentBid(lotId);
        const ranked = rankingItemByLotId(lotId);
        if (ranked) {
          updateDecisionPanel(ranked);
        }
        persistState();
      });
      item.dataset.hotkey = String(index + 1);
    });

    $('evalCurrent')?.addEventListener('click', evalCurrent);
    $('refreshRanking')?.addEventListener('click', async () => {
      await refreshRanking(Number($('currentLotId')?.value || 0));
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
      syncCurrentBid(Number(initialRawLotId || 0));
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
        await refreshRanking();
      }
    });
  });
})();
