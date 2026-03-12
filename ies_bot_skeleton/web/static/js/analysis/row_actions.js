(function () {
  function focusableItems(menu) {
    return Array.prototype.slice.call(
      menu.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    );
  }

  function setOpen(wrapper, open) {
    var toggle = wrapper.querySelector("[data-actions-toggle]");
    var menu = wrapper.querySelector("[data-actions-menu]");
    if (!toggle || !menu) return;
    wrapper.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    menu.hidden = !open;
  }

  function closeAll(root, except) {
    root.querySelectorAll("[data-row-actions].is-open").forEach(function (wrapper) {
      if (except && wrapper === except) return;
      setOpen(wrapper, false);
    });
  }

  window.addEventListener("DOMContentLoaded", function () {
    var root = document;

    root.querySelectorAll("[data-row-actions]").forEach(function (wrapper, index) {
      var toggle = wrapper.querySelector("[data-actions-toggle]");
      var menu = wrapper.querySelector("[data-actions-menu]");
      if (!toggle || !menu) return;

      if (!menu.id) {
        menu.id = "row-actions-menu-" + String(index + 1);
      }
      toggle.setAttribute("aria-controls", menu.id);
      toggle.setAttribute("aria-haspopup", "menu");
      toggle.setAttribute("aria-expanded", "false");
      menu.setAttribute("role", menu.getAttribute("role") || "menu");
      menu.hidden = true;

      menu.querySelectorAll("a, button").forEach(function (item) {
        if (!item.getAttribute("role")) {
          item.setAttribute("role", "menuitem");
        }
      });

      toggle.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var shouldOpen = !wrapper.classList.contains("is-open");
        closeAll(root);
        if (shouldOpen) setOpen(wrapper, true);
      });

      toggle.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowDown" && event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        closeAll(root);
        setOpen(wrapper, true);
        var items = focusableItems(menu);
        if (items.length > 0) items[0].focus();
      });

      menu.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        event.preventDefault();
        setOpen(wrapper, false);
        toggle.focus();
      });

      menu.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (target.closest("a, button")) {
          setOpen(wrapper, false);
        }
      });
    });

    document.addEventListener("click", function (event) {
      var target = event.target;
      if (!(target instanceof Node)) return;
      var inside = target instanceof Element ? target.closest("[data-row-actions]") : null;
      if (inside) return;
      closeAll(root);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      closeAll(root);
    });
  });
})();
