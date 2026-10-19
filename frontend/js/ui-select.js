/**
 * Dark-theme custom select — replaces native OS dropdown list styling.
 * Keeps a hidden native <select> for value / change events / programmatic updates.
 * Open menus are portaled into the nearest dialog (or body) with fixed positioning
 * so parent overflow:hidden / overflow:auto cannot clip or block clicks.
 */
(function (global) {
  const SELECTOR = "select.admin-select, #myapi-api-style, #myapi-template-select";
  const OPEN_MENUS = new Set();

  function chevronSvg() {
    return (
      '<svg class="ui-select-chevron" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">' +
      '<path fill="currentColor" d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' +
      "</svg>"
    );
  }

  function getMeta(select) {
    return select && select._uiSelectMeta;
  }

  function readLabel(select) {
    const opt = select.options[select.selectedIndex];
    return opt ? opt.textContent.trim() : "";
  }

  function portalHost(el) {
    return el.closest("dialog[open]") || el.closest("dialog") || document.body;
  }

  function rebuildMenu(meta) {
    const { select, menu } = meta;
    menu.innerHTML = "";
    Array.from(select.options).forEach((opt, idx) => {
      if (opt.hidden) return;
      if (opt.disabled && !opt.value) return;
      const li = document.createElement("li");
      li.className = "ui-select-option";
      li.setAttribute("role", "option");
      li.dataset.index = String(idx);
      li.textContent = opt.textContent.trim();
      if (opt.disabled) li.classList.add("is-disabled");
      if (idx === select.selectedIndex) li.classList.add("is-selected");
      if (opt.value === select.value) li.classList.add("is-active");
      menu.appendChild(li);
    });
  }

  function syncTrigger(meta) {
    const label = readLabel(meta.select);
    meta.labelEl.textContent = label || "请选择";
    meta.trigger.setAttribute("aria-label", label);
    meta.menu.querySelectorAll(".ui-select-option").forEach((el) => {
      const idx = Number(el.dataset.index);
      el.classList.toggle("is-selected", idx === meta.select.selectedIndex);
      el.classList.toggle("is-active", meta.select.options[idx]?.value === meta.select.value);
    });
  }

  function clearMenuPosition(menu) {
    menu.classList.remove("is-ported");
    menu.style.position = "";
    menu.style.top = "";
    menu.style.left = "";
    menu.style.width = "";
    menu.style.minWidth = "";
    menu.style.maxWidth = "";
    menu.style.right = "";
    menu.style.zIndex = "";
    menu.style.maxHeight = "";
  }

  function positionMenu(meta) {
    const { trigger, menu } = meta;
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const pad = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    menu.classList.add("is-ported");
    menu.style.position = "fixed";
    menu.style.right = "auto";
    menu.style.zIndex = "10050";
    menu.style.minWidth = `${Math.ceil(rect.width)}px`;
    menu.style.width = "max-content";
    menu.style.maxWidth = `${Math.min(360, vw - pad * 2)}px`;
    menu.style.top = "0px";
    menu.style.left = "0px";
    menu.style.maxHeight = "";

    const menuH = Math.min(menu.scrollHeight || 0, Math.floor(vh * 0.52), 280) || 160;
    const spaceBelow = vh - rect.bottom - gap - pad;
    const spaceAbove = rect.top - gap - pad;
    let placeAbove = false;
    let maxH = Math.min(280, Math.floor(vh * 0.52));
    if (spaceBelow < Math.min(menuH, 120) && spaceAbove > spaceBelow) {
      placeAbove = true;
      maxH = Math.max(96, Math.min(maxH, spaceAbove));
    } else {
      maxH = Math.max(96, Math.min(maxH, spaceBelow > 0 ? spaceBelow : maxH));
    }
    menu.style.maxHeight = `${maxH}px`;

    const width = Math.min(
      Math.max(rect.width, menu.offsetWidth || rect.width),
      Math.min(360, vw - pad * 2)
    );
    let left = rect.left;
    left = Math.min(Math.max(pad, left), vw - width - pad);
    let top = placeAbove ? rect.top - gap - Math.min(menu.offsetHeight || maxH, maxH) : rect.bottom + gap;
    top = Math.min(Math.max(pad, top), vh - pad - 40);

    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
    menu.style.width = `${Math.round(width)}px`;
  }

  function unbindReposition(meta) {
    if (!meta._onReposition) return;
    window.removeEventListener("resize", meta._onReposition);
    document.removeEventListener("scroll", meta._onReposition, true);
    meta._onReposition = null;
  }

  function bindReposition(meta) {
    unbindReposition(meta);
    meta._onReposition = () => {
      if (!meta.isOpen) return;
      const rect = meta.trigger.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        close(meta);
        return;
      }
      positionMenu(meta);
    };
    window.addEventListener("resize", meta._onReposition);
    document.addEventListener("scroll", meta._onReposition, true);
  }

  function close(meta) {
    if (!meta.isOpen) return;
    meta.isOpen = false;
    meta.wrap.classList.remove("is-open");
    meta.menu.classList.add("hidden");
    meta.trigger.setAttribute("aria-expanded", "false");
    unbindReposition(meta);
    OPEN_MENUS.delete(meta);
    if (meta.menu.parentNode && meta.menu.parentNode !== meta.wrap) {
      meta.wrap.appendChild(meta.menu);
    }
    clearMenuPosition(meta.menu);
  }

  function open(meta) {
    if (meta.select.disabled) return;
    document.querySelectorAll(".ui-select.is-open").forEach((node) => {
      const other = node.querySelector("select")?._uiSelectMeta;
      if (other && other !== meta) close(other);
    });
    meta.isOpen = true;
    meta.wrap.classList.add("is-open");
    meta.menu.classList.remove("hidden");
    meta.trigger.setAttribute("aria-expanded", "true");

    const host = portalHost(meta.wrap);
    if (meta.menu.parentNode !== host) host.appendChild(meta.menu);
    positionMenu(meta);
    bindReposition(meta);
    OPEN_MENUS.add(meta);

    const active = meta.menu.querySelector(".ui-select-option.is-selected");
    if (active) {
      try {
        active.scrollIntoView({ block: "nearest" });
      } catch {
        /* ignore */
      }
    }
  }

  function pickIndex(meta, index) {
    const opt = meta.select.options[index];
    if (!opt || opt.disabled) return;
    if (meta.select.selectedIndex !== index) {
      meta.select.selectedIndex = index;
      meta.select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncTrigger(meta);
    close(meta);
  }

  function closeAll() {
    document.querySelectorAll(".ui-select.is-open").forEach((node) => {
      const m = node.querySelector("select")?._uiSelectMeta;
      if (m) close(m);
    });
    OPEN_MENUS.forEach((m) => close(m));
  }

  function enhanceSelect(select) {
    if (!select) return select;
    if (select._uiSelectMeta) {
      if (select._uiSelectMeta.wrap?.isConnected) {
        refresh(select);
        return select;
      }
      select._uiSelectMeta = null;
      select.classList.remove("ui-select-native");
    }

    const wrap = document.createElement("div");
    wrap.className = "ui-select";
    if (select.classList.contains("admin-select")) wrap.classList.add("ui-select--admin");
    if (
      select.id === "myapi-api-style" ||
      select.id === "myapi-template-select" ||
      select.classList.contains("myapi-template-select")
    ) {
      wrap.classList.add("ui-select--myapi");
    }
    if (select.id === "agent-model-select" || select.classList.contains("agent-model-select")) {
      wrap.classList.add("ui-select--agent");
    }

    select.classList.add("ui-select-native");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "ui-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");

    const labelEl = document.createElement("span");
    labelEl.className = "ui-select-label";
    trigger.appendChild(labelEl);
    trigger.insertAdjacentHTML("beforeend", chevronSvg());

    const menu = document.createElement("ul");
    menu.className = "ui-select-menu hidden";
    menu.setAttribute("role", "listbox");

    wrap.appendChild(trigger);
    wrap.appendChild(menu);

    const meta = {
      select,
      wrap,
      trigger,
      labelEl,
      menu,
      isOpen: false,
    };
    select._uiSelectMeta = meta;

    rebuildMenu(meta);
    syncTrigger(meta);

    // Stop <label> from also activating the hidden native <select>.
    trigger.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
    trigger.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (meta.isOpen) close(meta);
      else open(meta);
    });

    // Prefer pointerdown: global mousedown.preventDefault() can swallow click.
    const onPick = (e) => {
      if (!meta.isOpen) return;
      const item = e.target.closest?.(".ui-select-option");
      if (!item || !menu.contains(item) || item.classList.contains("is-disabled")) return;
      e.preventDefault();
      e.stopPropagation();
      pickIndex(meta, Number(item.dataset.index));
    };
    menu.addEventListener("pointerdown", onPick);
    menu.addEventListener("mousedown", onPick);
    menu.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
    });

    select.addEventListener("change", () => syncTrigger(meta));

    if (!global._uiSelectDocBound) {
      global._uiSelectDocBound = true;
      const onDocPointer = (e) => {
        if (e.target.closest?.(".ui-select") || e.target.closest?.(".ui-select-menu")) return;
        closeAll();
      };
      document.addEventListener("pointerdown", onDocPointer, true);
      document.addEventListener("mousedown", onDocPointer, true);
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeAll();
      });
    }

    return select;
  }

  function refresh(select) {
    const meta = getMeta(select);
    if (!meta) return;
    rebuildMenu(meta);
    syncTrigger(meta);
    meta.wrap.classList.toggle("is-disabled", !!select.disabled);
    meta.trigger.disabled = !!select.disabled;
    meta.trigger.setAttribute("aria-disabled", select.disabled ? "true" : "false");
    if (meta.isOpen) positionMenu(meta);
  }

  function enhanceAll(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(SELECTOR).forEach((el) => enhanceSelect(el));
  }

  global.UiSelect = {
    enhance: enhanceSelect,
    refresh,
    enhanceAll,
  };
})(window);
