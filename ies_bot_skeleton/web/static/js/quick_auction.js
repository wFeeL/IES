(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function persistState() {
    const state = {
      currentLotId: $("currentLotId")?.value || "",
      currentBid: $("currentBid")?.value || "",
    };
    localStorage.setItem("ies-quick-auction", JSON.stringify(state));
  }

  function restoreState() {
    try {
      const raw = localStorage.getItem("ies-quick-auction");
      if (!raw) return;
      const state = JSON.parse(raw);
      if ($("currentLotId")) $("currentLotId").value = state.currentLotId || "";
      if ($("currentBid")) $("currentBid").value = state.currentBid || "";
    } catch (_) {}
  }

  async function evalCurrent() {
    const lotId = Number($("currentLotId")?.value || 0);
    if (!lotId) return;
    const res = await fetch(`/api/lots/${lotId}/evaluate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": (window.IES_QUICK_AUCTION || {}).csrfToken || "",
      },
      body: JSON.stringify({ mode: "forecast" }),
    });
    const data = await res.json();
    $("quickOut").textContent = JSON.stringify(data, null, 2);
  }

  async function refreshRanking() {
    const payload = {
      session_id: (window.IES_QUICK_AUCTION || {}).sessionId,
      lot_ids: Array.from(document.querySelectorAll(".auction-item"))
        .map((el) => Number(el.dataset.lotId || 0))
        .filter(Boolean),
      mode: "forecast",
    };

    const res = await fetch("/api/lots/compare", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": (window.IES_QUICK_AUCTION || {}).csrfToken || "",
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    $("quickOut").textContent = JSON.stringify(data, null, 2);
  }

  window.addEventListener("DOMContentLoaded", () => {
    restoreState();

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

    $("currentLotId")?.addEventListener("input", persistState);
    $("currentBid")?.addEventListener("input", persistState);

    setInterval(persistState, 10_000);

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
