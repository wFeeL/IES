(function () {
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

  function errorMessage(data) {
    return data?.error?.message || data?.error || 'Неизвестная ошибка';
  }

  function netProfit(row) {
    return Number((row.financial_breakdown || {}).result?.net_profit || row.net_profit || 0);
  }

  function riskValue(row) {
    return Number((row.financial_breakdown || {}).losses_and_risks?.risk_total || row.risk || 0);
  }

  function hardBid(row) {
    return Number((row.decision_summary || {}).hard_bid || row.recommended_bid_hard || 0);
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
    const reasons = Array.isArray(item.reasons) ? item.reasons.slice(0, 3).join('; ') : '';
    $('qaCommentary').textContent = reasons || item.risk_commentary || item.explanation || 'Нет комментария.';
    $('currentLotId').value = String(item.lot_id || '');
    setCurrentLinks(item.lot_id || 0);
  }

  async function persistCurrentBid(lotId) {
    const bidRaw = $('currentBid')?.value || '';
    if (!bidRaw) return true;
    const bid = Number(bidRaw);
    if (!Number.isFinite(bid) || bid < 0) {
      setStatus('Текущая ставка должна быть числом не меньше нуля.');
      return false;
    }
    const res = await fetch(`/api/lots/${lotId}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': cfg().csrfToken || '',
      },
      body: JSON.stringify({current_bid: bid}),
    });
    const data = await res.json();
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
    const bidSaved = await persistCurrentBid(id);
    if (!bidSaved) return;

    const res = await fetch(`/api/lots/${id}/evaluate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': cfg().csrfToken || '',
      },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(errorMessage(data));
      return;
    }

    updateDecisionPanel(data.item);
    setStatus(`Лот ${id} пересчитан. Полезность ${Number(data.item.summary_score || 0).toFixed(2)}, рабочая ставка ${hardBid(data.item).toFixed(1)}.`);
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
      });
    });
  }

  async function refreshRanking() {
    const params = new URLSearchParams();
    params.set('status', 'available');
    params.set('sort', $('qaSort')?.value || 'utility_desc');
    if ($('qaMinUtility')?.value) params.set('utility_min', $('qaMinUtility').value);
    if ($('qaMaxRisk')?.value) params.set('risk_max', $('qaMaxRisk').value);

    const res = await fetch(`/api/sessions/${cfg().sessionId}/lots/analytics?${params.toString()}`, {
      headers: {'X-CSRFToken': cfg().csrfToken || ''},
    });
    const data = await res.json();
    if (!data.ok) {
      setStatus(errorMessage(data));
      return;
    }
    state.ranking = Array.isArray(data.items) ? data.items : [];
    renderRanking(state.ranking);
    if (state.ranking[0]) updateDecisionPanel(state.ranking[0]);
    setStatus(`Получено лотов: ${state.ranking.length}.`);
  }

  async function evalCurrent() {
    const lotId = Number($('currentLotId')?.value || 0);
    if (!lotId) return;
    await evalLot(lotId);
    await refreshRanking();
  }

  window.addEventListener('DOMContentLoaded', () => {
    restoreState();
    state.ranking = Array.isArray(cfg().initialRanking) ? cfg().initialRanking : [];
    renderRanking(state.ranking);
    if (state.ranking[0]) updateDecisionPanel(state.ranking[0]);

    document.querySelectorAll('.auction-item').forEach((item, index) => {
      item.addEventListener('click', () => {
        document.querySelectorAll('.auction-item').forEach((row) => row.classList.remove('active'));
        item.classList.add('active');
        $('currentLotId').value = item.dataset.lotId;
        syncCurrentBid(item.dataset.lotId);
        persistState();
      });
      item.dataset.hotkey = String(index + 1);
    });

    $('evalCurrent')?.addEventListener('click', evalCurrent);
    $('refreshRanking')?.addEventListener('click', async () => {
      await refreshRanking();
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

    const initialLotId = $('currentLotId')?.value;
    if (initialLotId) syncCurrentBid(initialLotId);
    if (!initialLotId && document.querySelector('.auction-item')) {
      document.querySelector('.auction-item').click();
    }

    document.addEventListener('keydown', async (event) => {
      if (/^[1-9]$/.test(event.key)) {
        const index = Number(event.key) - 1;
        const rows = document.querySelectorAll('.auction-item');
        if (rows[index]) rows[index].click();
      }
      if (event.key.toLowerCase() === 'e') {
        await evalCurrent();
      }
      if (event.key.toLowerCase() === 'r') {
        await refreshRanking();
      }
    });
  });
})();
