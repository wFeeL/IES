(function () {
  function parseInitial(raw) {
    try {
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function toInt(value, fallback) {
    const out = Number(value);
    if (!Number.isFinite(out)) return fallback;
    return Math.floor(out);
  }

  function setupLotEditor() {
    const cfg = window.IES_LOT_EDITOR || {};
    const rowsEl = document.getElementById("lotEditorRows");
    const addBtn = document.getElementById("lotRowAddBtn");
    const summaryEl = document.getElementById("lotEditorSummary");
    const hiddenEl = document.getElementById("itemsStateJson");
    const rawEl = document.getElementById("items_json");

    if (!rowsEl || !addBtn || !hiddenEl) return;

    const objectTypes = Array.isArray(cfg.objectTypes) ? cfg.objectTypes : [];
    const defaultTypeId = objectTypes.length ? Number(objectTypes[0].id) : 0;
    const typeOptions = objectTypes
      .map((row) => `<option value="${row.id}">${row.code} / ${row.name}</option>`)
      .join("");

    function renderSummary(items) {
      if (!summaryEl) return;
      if (!items.length) {
        summaryEl.textContent = "Добавьте минимум одну строку состава лота.";
        return;
      }

      const counts = {consumer: 0, generator: 0, storage: 0, infrastructure: 0};
      let total = 0;
      items.forEach((item) => {
        total += Number(item.quantity || 0);
        const type = objectTypes.find((row) => Number(row.id) === Number(item.object_type_id));
        const category = type ? type.category : "other";
        counts[category] = (counts[category] || 0) + Number(item.quantity || 0);
      });
      summaryEl.textContent = `Суммарно объектов: ${total}. Потребители: ${counts.consumer || 0}, генераторы: ${counts.generator || 0}, накопители: ${counts.storage || 0}, инфраструктура: ${counts.infrastructure || 0}.`;
    }

    function collectRows() {
      const lines = Array.from(rowsEl.querySelectorAll("tr"))
        .map((tr) => {
          const typeId = toInt(tr.querySelector(".le-type")?.value || 0, 0);
          const quantity = Math.max(1, toInt(tr.querySelector(".le-qty")?.value || 1, 1));
          if (!typeId) return null;
          const typeRow = objectTypes.find((x) => Number(x.id) === Number(typeId));
          return {
            object_type_id: typeId,
            object_type_code: typeRow ? typeRow.code : null,
            quantity,
            overrides: {},
          };
        })
        .filter(Boolean);

      hiddenEl.value = JSON.stringify(lines);
      if (rawEl) rawEl.value = JSON.stringify(lines, null, 2);
      renderSummary(lines);
      return lines;
    }

    function addRow(item) {
      const tr = document.createElement("tr");
      const selectedType = item.object_type_id ? String(item.object_type_id) : String(defaultTypeId || "");
      tr.innerHTML = `
        <td><select class="input le-type">${typeOptions}</select></td>
        <td><input class="input le-qty" type="number" min="1" value="${Math.max(1, toInt(item.quantity || 1, 1))}" /></td>
        <td class="le-category muted">—</td>
        <td><button type="button" class="btn btn-secondary le-remove">Удалить</button></td>
      `;
      const sel = tr.querySelector(".le-type");
      function syncCategory() {
        const typeId = Number(sel?.value || 0);
        const typeRow = objectTypes.find((x) => Number(x.id) === typeId);
        const categoryEl = tr.querySelector(".le-category");
        if (categoryEl) categoryEl.textContent = typeRow ? typeRow.category : "—";
      }
      if (sel) sel.value = selectedType;
      syncCategory();
      tr.querySelector(".le-remove")?.addEventListener("click", function () {
        tr.remove();
        collectRows();
      });
      tr.querySelectorAll("input,select").forEach((el) => {
        el.addEventListener("input", () => {
          syncCategory();
          collectRows();
        });
      });
      rowsEl.appendChild(tr);
      collectRows();
    }

    const initialRows = parseInitial(cfg.initialItemsRaw);
    if (initialRows.length > 0) {
      initialRows.forEach((row) => addRow(row));
    } else {
      addRow({});
    }

    addBtn.addEventListener("click", function () {
      addRow({});
    });

    document.getElementById("lotEditorForm")?.addEventListener("submit", function () {
      collectRows();
    });
  }

  window.addEventListener("DOMContentLoaded", setupLotEditor);
})();
