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
      if (!items.length) {
        summaryEl.textContent = "Добавьте минимум одну строку состава лота.";
        return;
      }

      const counts = {};
      for (const item of items) {
        const key = item.object_type_code || String(item.object_type_id);
        counts[key] = (counts[key] || 0) + Number(item.quantity || 0);
      }
      const parts = Object.keys(counts)
        .sort()
        .map((key) => `${key} x${counts[key]}`);
      summaryEl.textContent = `Строк: ${items.length}. Суммарно: ${parts.join(", ")}`;
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
        <td><input class="input le-parent" type="text" placeholder="optional" value="" disabled /></td>
        <td><button type="button" class="btn btn-secondary le-remove">Remove</button></td>
      `;
      const sel = tr.querySelector(".le-type");
      if (sel) {
        sel.value = selectedType;
      }
      tr.querySelector(".le-remove")?.addEventListener("click", function () {
        tr.remove();
        collectRows();
      });
      tr.querySelectorAll("input,select").forEach((el) => {
        el.addEventListener("input", collectRows);
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
