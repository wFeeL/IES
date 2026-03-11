(function () {
  function $(id) {
    return document.getElementById(id);
  }

  const state = {
    ranking: [],
    shortlistOnly: false,
  };

  function storageKey() {
    return `ies-quick-auction-${(window.IES_QUICK_AUCTION || {}).sessionId || 0}`;
  }

  function shortlistKey() {
    return `ies-shortlist-${(window.IES_QUICK_AUCTION || {}).sessionId || 0}`;
  }

  function getShortlist() {
    try {
      return JSON.parse(localStorage.getItem(shortlistKey()) || "[]");
    } catch (_) {
      return [];
    }
  }

  function persistState() {
    const payload = {
      currentLotId: $("currentLotId")?.value || "",
      currentBid: $("currentBid")?.value || "",
      sort: $("qaSort")?.value || "score_desc",
      minScore: $("qaMinScore")?.value || "",
      maxRisk: $("qaMaxRisk")?.value || "",
      shortlistOnly: $("qaShortlistOnly")?.value || "0",
    };
    localStorage.setItem(storageKey(), JSON.stringify(payload));
  }

  function restoreState() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return;
      const payload = JSON.parse(raw);
      if ($("currentLotId")) $("currentLotId").value = payload.currentLotId || "";
      if ($("currentBid")) $("currentBid").value = payload.currentBid || "";
      if ($("qaSort")) $("qaSort").value = payload.sort || "score_desc";
      if ($("qaMinScore")) $("qaMinScore").value = payload.minScore || "";
      if ($("qaMaxRisk")) $("qaMaxRisk").value = payload.maxRisk || "";
      if ($("qaShortlistOnly")) $("qaShortlistOnly").value = payload.shortlistOnly || "0";
    } catch (_) {}
  }

  function bySort(a, b, key) {
    if (key === "risk_asc") {
      return Number((a.metrics || {}).delta_risk || 0) - Number((b.metrics || {}).delta_risk || 0);
    }
    if (key === "bid_desc") {
      return Number(b.recommended_bid_hard || 0) - Number(a.recommended_bid_hard || 0);
    }
    return Number(b.summary_score || 0) - Number(a.summary_score || 0);
  }

  function filterAndSort(rows) {
    const minScore = Number($("qaMinScore")?.value || "");
    const maxRisk = Number($("qaMaxRisk")?.value || "");
    const sort = $("qaSort")?.value || "score_desc";
    const shortlistOnly = $("qaShortlistOnly")?.value === "1";
    const shortlist = getShortlist().map((x) => Number(x));

    return rows
      .filter((row) => {
        const score = Number(row.summary_score || 0);
        const risk = Number((row.metrics || {}).delta_risk || 0);
        const lotId = Number(row.lot_id || 0);
        if (Number.isFinite(minScore) && $("qaMinScore")?.value && score < minScore) return false;
        if (Number.isFinite(maxRisk) && $("qaMaxRisk")?.value && risk > maxRisk) return false;
        if (shortlistOnly && !shortlist.includes(lotId)) return false;
        return true;
      })
      .slice()
      .sort((a, b) => bySort(a, b, sort));
  }

  function updateDecisionPanel(item) {
    if (!item) return;
    $("qaScore").textContent = Number(item.summary_score || 0).toFixed(2);
    $("qaRisk").textContent = Number((item.metrics || {}).delta_risk || 0).toFixed(2);
    $("qaBid").textContent = Number(item.recommended_bid_hard || 0).toFixed(1);
    $("qaCommentary").textContent = item.risk_commentary || item.explanation || "Нет комментария.";
  }

  async function evalLot(lotId) {
    const id = Number(lotId || 0);
    if (!id) return;
    const cfg = window.IES_QUICK_AUCTION || {};
    const payload = {
      mode: cfg.mode || "no_forecast",
      corridor_override: cfg.corridorOverride || null,
    };
    if (cfg.forecastId) payload.forecast_id = cfg.forecastId;
    const res = await fetch(`/api/lots/${id}/evaluate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    $("quickOut").textContent = JSON.stringify(data, null, 2);
    if (data.ok) updateDecisionPanel(data.item);
  }

  function rowActionsHtml(lotId, sessionId) {
    return `
      <div class="actions">
        <button class="btn btn-secondary quickEvalRow" data-lot-id="${lotId}" type="button">Оценить</button>
        <a class="btn btn-secondary" href="/strategy-fit/${lotId}">Стратегии</a>
        <a class="btn btn-secondary" href="/lots/${sessionId}">Открыть лот</a>
      </div>
    `;
  }

  function renderRanking(rows) {
    const body = $("rankingTableBody");
    if (!body) return;

    const sessionId = Number((window.IES_QUICK_AUCTION || {}).sessionId || 0);
    const filtered = filterAndSort(rows || []);

    body.innerHTML = "";
    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="8" class="muted">Нет строк после фильтрации.</td></tr>';
      return;
    }

    filtered.forEach((row, idx) => {
      const tr = document.createElement("tr");
      const risk = Number((row.metrics || {}).delta_risk || 0);
      tr.dataset.lotId = String(row.lot_id || "");
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td>${row.lot_id}</td>
        <td>${Number(row.summary_score || 0).toFixed(2)}</td>
        <td>${risk.toFixed(2)}</td>
        <td>${Number(row.recommended_bid_hard || 0).toFixed(1)}</td>
        <td>${row.strategy_fit_text || "-"}</td>
        <td>${row.risk_commentary || row.explanation || ""}</td>
        <td>${rowActionsHtml(row.lot_id, sessionId)}</td>
      `;
      body.appendChild(tr);
    });

    body.querySelectorAll(".quickEvalRow").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await evalLot(btn.dataset.lotId);
      });
    });
  }

  async function refreshRanking() {
    const cfg = window.IES_QUICK_AUCTION || {};
    const payload = {
      session_id: cfg.sessionId,
      lot_ids: Array.from(document.querySelectorAll(".auction-item"))
        .map((el) => Number(el.dataset.lotId || 0))
        .filter(Boolean),
      mode: cfg.mode || "no_forecast",
      corridor_override: cfg.corridorOverride || null,
    };

    if (cfg.forecastId) payload.forecast_id = cfg.forecastId;
    if (!payload.session_id || payload.lot_ids.length < 2) {
      $("quickOut").textContent = "Нужно минимум 2 лота для сравнения.";
      return;
    }

    const res = await fetch("/api/lots/compare", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    $("quickOut").textContent = JSON.stringify(data, null, 2);
    if (data.ok && Array.isArray(data.items)) {
      state.ranking = data.items;
      renderRanking(state.ranking);
      if (data.items[0]) updateDecisionPanel(data.items[0]);
    }
  }

  async function evalCurrent() {
    const lotId = Number($("currentLotId")?.value || 0);
    if (!lotId) return;
    await evalLot(lotId);
  }

  window.addEventListener("DOMContentLoaded", () => {
    restoreState();
    state.ranking = (window.IES_QUICK_AUCTION || {}).initialRanking || [];
    renderRanking(state.ranking);
    if (state.ranking[0]) updateDecisionPanel(state.ranking[0]);

    document.querySelectorAll(".auction-item").forEach((el, idx) => {
      el.addEventListener("click", () => {
        document.querySelectorAll(".auction-item").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        $("currentLotId").value = el.dataset.lotId;
        persistState();
      });
      el.dataset.hotkey = String(idx + 1);
    });

    $("evalCurrent")?.addEventListener("click", evalCurrent);
    $("refreshRanking")?.addEventListener("click", refreshRanking);
    $("qaApplyFilters")?.addEventListener("click", () => {
      renderRanking(state.ranking);
      persistState();
    });

    $("currentLotId")?.addEventListener("input", persistState);
    $("currentBid")?.addEventListener("input", persistState);
    $("qaSort")?.addEventListener("change", () => renderRanking(state.ranking));
    $("qaShortlistOnly")?.addEventListener("change", () => renderRanking(state.ranking));

    document.addEventListener("keydown", async (e) => {
      if (/^[1-9]$/.test(e.key)) {
        const idx = Number(e.key) - 1;
        const rows = document.querySelectorAll(".auction-item");
        if (rows[idx]) rows[idx].click();
      }
      if (e.key.toLowerCase() === "e") {
        await evalCurrent();
      }
      if (e.key.toLowerCase() === "r") {
        await refreshRanking();
      }
    });
  });
})();
