(function () {
  const PREFIX = "ies-dismissed-alert:";

  function storageKey(alertId) {
    return `${PREFIX}${alertId}`;
  }

  function isDismissed(alertId) {
    try {
      return localStorage.getItem(storageKey(alertId)) === "1";
    } catch (_) {
      return false;
    }
  }

  function dismiss(alertId) {
    try {
      localStorage.setItem(storageKey(alertId), "1");
    } catch (_) {
      // no-op when storage is unavailable
    }
  }

  function boot() {
    const alerts = document.querySelectorAll("[data-dismissible-alert]");
    alerts.forEach((node) => {
      const alertId = String(node.getAttribute("data-alert-id") || "").trim();
      if (!alertId) {
        return;
      }
      if (isDismissed(alertId)) {
        node.hidden = true;
        return;
      }
      const btn = node.querySelector("[data-dismiss-alert]");
      if (!btn) {
        return;
      }
      btn.addEventListener("click", () => {
        dismiss(alertId);
        node.hidden = true;
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
