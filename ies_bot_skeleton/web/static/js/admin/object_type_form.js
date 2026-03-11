(function () {
  function setupObjectTypeForm() {
    const cfg = window.IES_OBJECT_TYPE_FORM || {};
    const categoryEl = document.getElementById(cfg.categoryId || "objectTypeCategory");
    const subtypeEl = document.getElementById(cfg.subtypeId || "objectTypeSubtype");
    if (!categoryEl || !subtypeEl) return;

    function syncSections() {
      const category = String(categoryEl.value || "");
      const subtype = String(subtypeEl.value || "");
      document.querySelectorAll(".object-type-section").forEach((section) => {
        const categories = String(section.getAttribute("data-object-categories") || "")
          .split(",")
          .filter(Boolean);
        const subtypes = String(section.getAttribute("data-object-subtypes") || "")
          .split(",")
          .filter(Boolean);
        const categoryOk = categories.includes(category);
        const subtypeOk = !subtypes.length || subtypes.includes(subtype);
        section.classList.toggle("is-hidden", !(categoryOk && subtypeOk));
      });
    }

    syncSections();
    categoryEl.addEventListener("change", syncSections);
    subtypeEl.addEventListener("change", syncSections);
  }

  window.addEventListener("DOMContentLoaded", setupObjectTypeForm);
})();
