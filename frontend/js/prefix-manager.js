/**
 * Prompt prefix management — image/video prefixes prepended to generation prompts.
 * Format: [prefix content]\n\n[user content]
 */
const PROMPT_PREFIX_STORAGE_KEY = "doubao_prompt_prefixes";

const PREFIX_ICO_VIEW = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.6"/><path d="M16.5 16.5L20 20" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
const PREFIX_ICO_EDIT = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 19h3.8L19 8.8a1.8 1.8 0 0 0 0-2.6L17.8 5a1.8 1.8 0 0 0-2.6 0L5 15.2V19z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
const PREFIX_ICO_DEL = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6.2 6.2h11.6M9.5 6.2V4.8h5v1.4M8.1 6.2l.6 12.2h6.6l.6-12.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const PrefixEditState = { mode: "add", mediaType: "image", id: null };

const PromptPrefixStore = {
  _cache: null,
  _listeners: [],

  _userKey() {
    const u = String(window.__canvasUser?.username || window.__canvasUser?.display_name || "guest").trim();
    return `${PROMPT_PREFIX_STORAGE_KEY}_${u || "guest"}`;
  },

  _normalizeMediaType(mediaType) {
    return mediaType === "video" ? "video" : "image";
  },

  _listKey(mediaType) {
    return this._normalizeMediaType(mediaType) === "video" ? "videoPrefixes" : "imagePrefixes";
  },

  load() {
    if (this._cache) return this._cache;
    try {
      const raw = localStorage.getItem(this._userKey());
      this._cache = raw ? JSON.parse(raw) : null;
    } catch {
      this._cache = null;
    }
    if (!this._cache || typeof this._cache !== "object") {
      this._cache = { imagePrefixes: [], videoPrefixes: [], defaultImage: null, defaultVideo: null };
    }
    if (Array.isArray(this._cache.prefixes) && !this._cache.imagePrefixes) {
      this._cache.imagePrefixes = this._cache.prefixes.slice();
      this._cache.videoPrefixes = [];
      delete this._cache.prefixes;
    }
    if (!Array.isArray(this._cache.imagePrefixes)) this._cache.imagePrefixes = [];
    if (!Array.isArray(this._cache.videoPrefixes)) this._cache.videoPrefixes = [];
    return this._cache;
  },

  save() {
    localStorage.setItem(this._userKey(), JSON.stringify(this._cache));
    this._notifyChange();
  },

  invalidate() {
    this._cache = null;
  },

  onChange(fn) {
    if (typeof fn === "function") this._listeners.push(fn);
  },

  _notifyChange() {
    for (const fn of this._listeners) {
      try {
        fn();
      } catch {}
    }
    if (typeof window.refreshAllPrefixPickers === "function") window.refreshAllPrefixPickers();
  },

  list(mediaType) {
    const data = this.load();
    const key = this._listKey(mediaType);
    return (data[key] || []).slice();
  },

  hasAny(mediaType) {
    return this.list(mediaType).length > 0;
  },

  getById(id, mediaType) {
    if (!id) return null;
    return this.list(mediaType).find((p) => p.id === id) || null;
  },

  getDefaultId(mediaType) {
    const data = this.load();
    const key = this._normalizeMediaType(mediaType) === "video" ? "defaultVideo" : "defaultImage";
    const id = data[key];
    return id && this.getById(id, mediaType) ? id : null;
  },

  getEffectivePrefixId(storedId, mediaType) {
    if (storedId === "") return null;
    if (storedId && this.getById(storedId, mediaType)) return storedId;
    if (storedId !== undefined && storedId !== null) return null;
    return this.getDefaultId(mediaType);
  },

  getEffectiveLabel(storedId, mediaType) {
    const id = this.getEffectivePrefixId(storedId, mediaType);
    if (!id) return "无";
    return this.getById(id, mediaType)?.name || "无";
  },

  applyPrefix(storedId, mediaType, content) {
    const text = String(content || "").replace(/^\s+/, "").replace(/\s+$/, "");
    const id = this.getEffectivePrefixId(storedId, mediaType);
    if (!id) return text;
    const prefix = this.getById(id, mediaType);
    // Trim ends only — keep internal newlines inside the prefix body.
    const prefixText = String(prefix?.content || "").replace(/^\s+/, "").replace(/\s+$/, "");
    if (!prefixText) return text;
    if (!text) return prefixText;
    // Always insert a blank line so the first content line never glues onto the
    // last prefix line (e.g. …】视频编号 → …】\n\n视频编号).
    return `${prefixText}\n\n${text}`;
  },

  add(name, content, mediaType) {
    const data = this.load();
    const key = this._listKey(mediaType);
    const item = {
      id: typeof uid === "function" ? uid() : `pfx_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name: String(name || "").trim() || (mediaType === "video" ? "新视频前缀" : "新图片前缀"),
      content: String(content || "").trim(),
    };
    data[key].push(item);
    this.save();
    return item;
  },

  update(id, mediaType, patch) {
    const data = this.load();
    const key = this._listKey(mediaType);
    const item = data[key].find((p) => p.id === id);
    if (!item) return false;
    if (patch.name != null) item.name = String(patch.name).trim() || item.name;
    if (patch.content != null) item.content = String(patch.content).trim();
    this.save();
    return true;
  },

  remove(id, mediaType) {
    const data = this.load();
    const key = this._listKey(mediaType);
    const before = data[key].length;
    data[key] = data[key].filter((p) => p.id !== id);
    const defaultKey = this._normalizeMediaType(mediaType) === "video" ? "defaultVideo" : "defaultImage";
    if (data[defaultKey] === id) data[defaultKey] = null;
    if (data[key].length !== before) this.save();
  },

  setDefault(mediaType, id) {
    const data = this.load();
    const defaultKey = this._normalizeMediaType(mediaType) === "video" ? "defaultVideo" : "defaultImage";
    data[defaultKey] = id && this.getById(id, mediaType) ? id : null;
    this.save();
  },
};

function prefixContentSnippet(content, maxLen = 28) {
  const text = String(content || "").replace(/\s+/g, " ").trim();
  if (!text) return "（空）";
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

function renderPrefixPicker(opts = {}) {
  const mediaType = opts.mediaType === "video" ? "video" : "image";
  if (!PromptPrefixStore.hasAny(mediaType)) return "";
  const nodeId = opts.nodeId || "";
  const storedId = opts.prefixId;
  const effectiveId = PromptPrefixStore.getEffectivePrefixId(storedId, mediaType);
  const label = PromptPrefixStore.getEffectiveLabel(storedId, mediaType);
  const displayLabel = label.length > 8 ? `${label.slice(0, 7)}…` : label;
  const prefixes = PromptPrefixStore.list(mediaType);

  const noneBtn = `
    <button type="button" class="prefix-option${!effectiveId ? " active" : ""}" data-node="${escapeAttr(nodeId)}" data-prefix="" data-media="${mediaType}">
      <span class="prefix-option-label">无</span>
      ${!effectiveId ? '<span class="video-model-check">✓</span>' : ""}
    </button>`;
  const items = prefixes
    .map(
      (p) => `
    <button type="button" class="prefix-option${effectiveId === p.id ? " active" : ""}" data-node="${escapeAttr(nodeId)}" data-prefix="${escapeAttr(p.id)}" data-media="${mediaType}">
      <span class="prefix-option-label">${escapeHtml(p.name)}</span>
      ${effectiveId === p.id ? '<span class="video-model-check">✓</span>' : ""}
    </button>`
    )
    .join("");

  return `
    <div class="prefix-picker" data-node="${escapeAttr(nodeId)}" data-media="${mediaType}">
      <button type="button" class="prefix-btn pill-select" data-node="${escapeAttr(nodeId)}" data-media="${mediaType}" title="前缀">
        <span class="prefix-btn-label">${escapeHtml(displayLabel)}</span>
        <span class="video-model-btn-caret">▾</span>
      </button>
      <div class="prefix-menu hidden" data-node="${escapeAttr(nodeId)}" data-media="${mediaType}">${noneBtn}${items}</div>
    </div>`;
}

function renderPrefixPickerCompact(opts = {}) {
  return renderPrefixPicker(opts);
}

/** Same node can host a prefix picker on the card and in 快速添加 — always resolve locally. */
function resolvePrefixMenuEl(fromEl) {
  if (!fromEl?.closest) return null;
  return (
    fromEl.closest(".prefix-picker")?.querySelector(".prefix-menu") ||
    fromEl.closest(".prefix-menu") ||
    null
  );
}

function renderDefaultPrefixPicker(mediaType) {
  const prefixes = PromptPrefixStore.list(mediaType);
  const selectedId = PromptPrefixStore.getDefaultId(mediaType);
  const label = selectedId ? PromptPrefixStore.getById(selectedId, mediaType)?.name || "无" : "无";
  const displayLabel = label.length > 10 ? `${label.slice(0, 9)}…` : label;
  const noneBtn = `
    <button type="button" class="prefix-default-option${!selectedId ? " active" : ""}" data-default-prefix="" data-default-media="${mediaType}">
      <span class="prefix-option-label">无</span>
      ${!selectedId ? '<span class="video-model-check">✓</span>' : ""}
    </button>`;
  const items = prefixes
    .map(
      (p) => `
    <button type="button" class="prefix-default-option${selectedId === p.id ? " active" : ""}" data-default-prefix="${escapeAttr(p.id)}" data-default-media="${mediaType}">
      <span class="prefix-option-label">${escapeHtml(p.name)}</span>
      ${selectedId === p.id ? '<span class="video-model-check">✓</span>' : ""}
    </button>`
    )
    .join("");
  return `
    <div class="prefix-default-picker" data-default-media="${mediaType}">
      <button type="button" class="prefix-default-btn pill-select" data-default-media="${mediaType}" title="选择默认前缀">
        <span class="prefix-default-btn-value">${escapeHtml(displayLabel)}</span>
        <span class="video-model-btn-caret">▾</span>
      </button>
      <div class="prefix-default-menu hidden" data-default-media="${mediaType}">${noneBtn}${items}</div>
    </div>`;
}

function closePrefixManagerMenus(exceptMedia) {
  document.querySelectorAll(".prefix-default-menu").forEach((el) => {
    if (exceptMedia && el.dataset.defaultMedia === exceptMedia) return;
    if (typeof hidePillPicker === "function") hidePillPicker(el);
    else el.classList.add("hidden");
  });
}

function syncPromptExpandPrefixPicker(nodeId) {
  const host = document.getElementById("prompt-expand-qm-prefix");
  if (!host) return;
  const node = CanvasStore?.activeBoard()?.nodes?.find((n) => n.id === nodeId);
  if (!node || (node.type !== "image" && node.type !== "video")) {
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  const mediaType = node.type === "video" ? "video" : "image";
  const html = renderPrefixPickerCompact({
    nodeId,
    mediaType,
    prefixId: node.promptPrefixId,
  });
  if (!html) {
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  host.hidden = false;
  host.innerHTML = html;
}

function refreshAllPrefixPickers() {
  document.querySelectorAll(".prefix-picker[data-node]").forEach((picker) => {
    const nodeId = picker.dataset.node;
    const mediaType = picker.dataset.media === "video" ? "video" : "image";
    let storedId;
    if (nodeId?.startsWith("wbclip-") && typeof findWbClip === "function") {
      const clip = findWbClip(parseWbClipNodeId?.(nodeId));
      storedId = clip?.promptPrefixId;
    } else if (nodeId?.startsWith("wbasset-")) {
      // Must resolve THIS row — never reuse active draft (that made every row show one prefix).
      const itemId = typeof parseWbAssetNodeId === "function" ? parseWbAssetNodeId(nodeId) : null;
      const item =
        (itemId && typeof findWbStudioItem === "function" && findWbStudioItem(itemId)) ||
        (itemId && WorkbenchAssetStudio?.items?.().find((i) => i.id === itemId)) ||
        null;
      storedId = item?.promptPrefixId;
    } else if (nodeId && typeof CanvasStore !== "undefined") {
      const node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
      storedId = node?.promptPrefixId;
    }
    // Rebuild only the prefix control so option lists stay in sync after manager edits.
    const html = renderPrefixPicker({ nodeId, mediaType, prefixId: storedId });
    if (!html) {
      picker.remove();
      return;
    }
    const wrap = document.createElement("div");
    wrap.innerHTML = html.trim();
    const next = wrap.firstElementChild;
    if (next) picker.replaceWith(next);
  });
  if (typeof PromptExpand !== "undefined" && PromptExpand.isOpen?.() && PromptExpand.nodeId) {
    syncPromptExpandPrefixPicker(PromptExpand.nodeId);
  }
  // Do not rebuild full toolbars / all canvas nodes — that flashed every studio row.
}

function renderPrefixManagerItems(prefixes, mediaType) {
  if (!prefixes.length) {
    return `<p class="prefix-manager-empty">暂无${mediaType === "video" ? "视频" : "图片"}前缀</p>`;
  }
  return prefixes
    .map((p) => {
      const snippet = prefixContentSnippet(p.content);
      return `
      <div class="prefix-manager-row" data-prefix-id="${escapeAttr(p.id)}" data-prefix-kind="${mediaType}">
        <div class="prefix-manager-row-main" title="${escapeAttr(p.name + " - " + (p.content || ""))}">
          <span class="prefix-manager-row-name">${escapeHtml(p.name)}</span>
          <span class="prefix-manager-row-sep">-</span>
          <span class="prefix-manager-row-snippet">${escapeHtml(snippet)}</span>
        </div>
        <div class="prefix-manager-row-actions">
          <button type="button" class="prefix-manager-ico" data-prefix-view="${escapeAttr(p.id)}" data-prefix-kind="${mediaType}" title="查看">${PREFIX_ICO_VIEW}</button>
          <button type="button" class="prefix-manager-ico" data-prefix-edit="${escapeAttr(p.id)}" data-prefix-kind="${mediaType}" title="编辑">${PREFIX_ICO_EDIT}</button>
          <button type="button" class="prefix-manager-ico is-danger" data-prefix-del="${escapeAttr(p.id)}" data-prefix-kind="${mediaType}" title="删除">${PREFIX_ICO_DEL}</button>
        </div>
      </div>`;
    })
    .join("");
}

function renderPrefixManagerList() {
  const listImage = document.getElementById("prefix-manager-list-image");
  const listVideo = document.getElementById("prefix-manager-list-video");
  const imgDefault = document.getElementById("prefix-default-image");
  const vidDefault = document.getElementById("prefix-default-video");
  const data = PromptPrefixStore.load();

  if (listImage) listImage.innerHTML = renderPrefixManagerItems(data.imagePrefixes, "image");
  if (listVideo) listVideo.innerHTML = renderPrefixManagerItems(data.videoPrefixes, "video");
  if (imgDefault) imgDefault.innerHTML = renderDefaultPrefixPicker("image");
  if (vidDefault) vidDefault.innerHTML = renderDefaultPrefixPicker("video");
}

function openPrefixManagerDialog() {
  const dlg = document.getElementById("modal-prefix-manager");
  if (!dlg) return;
  renderPrefixManagerList();
  if (typeof revealOverlay === "function") revealOverlay(dlg);
  else dlg.showModal?.();
}

function closePrefixManagerDialog() {
  closePrefixManagerMenus(null);
  const dlg = document.getElementById("modal-prefix-manager");
  if (!dlg) return;
  if (typeof dismissOverlay === "function") dismissOverlay(dlg);
  else dlg.close?.();
}

function openPrefixViewModal(id, mediaType) {
  const item = PromptPrefixStore.getById(id, mediaType);
  if (!item) return;
  const dlg = document.getElementById("modal-prefix-view");
  const title = document.getElementById("prefix-view-title");
  const content = document.getElementById("prefix-view-content");
  if (title) title.textContent = item.name || "查看前缀";
  if (content) content.textContent = item.content || "（空）";
  if (typeof revealOverlay === "function") revealOverlay(dlg);
  else dlg?.showModal?.();
}

function closePrefixViewModal() {
  const dlg = document.getElementById("modal-prefix-view");
  if (!dlg) return;
  if (typeof dismissOverlay === "function") dismissOverlay(dlg);
  else dlg.close?.();
}

function openPrefixEditModal(mode, mediaType, id) {
  PrefixEditState.mode = mode;
  PrefixEditState.mediaType = mediaType;
  PrefixEditState.id = id || null;
  const dlg = document.getElementById("modal-prefix-edit");
  const title = document.getElementById("prefix-edit-title");
  const nameInput = document.getElementById("input-prefix-edit-name");
  const contentInput = document.getElementById("input-prefix-edit-content");
  const saveBtn = document.getElementById("btn-prefix-edit-save");
  const item = id ? PromptPrefixStore.getById(id, mediaType) : null;
  const isView = mode === "view";

  if (title) {
    title.textContent =
      mode === "edit" ? "编辑前缀" : mediaType === "video" ? "添加视频前缀" : "添加图片前缀";
  }
  if (nameInput) {
    nameInput.value = item?.name || "";
    nameInput.readOnly = isView;
  }
  if (contentInput) {
    contentInput.value = item?.content || "";
    contentInput.readOnly = isView;
  }
  if (saveBtn) saveBtn.classList.toggle("hidden", isView);

  if (typeof revealOverlay === "function") revealOverlay(dlg);
  else dlg?.showModal?.();
  if (!isView) setTimeout(() => nameInput?.focus(), 40);
}

function closePrefixEditModal() {
  const dlg = document.getElementById("modal-prefix-edit");
  if (!dlg) return;
  if (typeof dismissOverlay === "function") dismissOverlay(dlg);
  else dlg.close?.();
  PrefixEditState.mode = "add";
  PrefixEditState.id = null;
}

async function confirmDeletePrefix(id, mediaType) {
  const item = PromptPrefixStore.getById(id, mediaType);
  if (!item) return;
  const ok = await AppDialog.confirmDelete(`确定删除前缀「${item.name}」？`, { title: "删除前缀" });
  if (!ok) return;
  PromptPrefixStore.remove(id, mediaType);
  renderPrefixManagerList();
  if (typeof showCanvasToast === "function") showCanvasToast("已删除");
}

function initPrefixManager() {
  document.getElementById("btn-workspace-prefix-manager")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    window.CanvasAuth?.closeAllAccountPops?.();
    try {
      await window.CanvasAuth?.ensureLogin?.();
      openPrefixManagerDialog();
    } catch {}
  });

  document.getElementById("btn-close-prefix-manager")?.addEventListener("click", () => closePrefixManagerDialog());

  document.getElementById("btn-prefix-manager-add-image")?.addEventListener("click", () => {
    openPrefixEditModal("add", "image");
  });
  document.getElementById("btn-prefix-manager-add-video")?.addEventListener("click", () => {
    openPrefixEditModal("add", "video");
  });

  document.getElementById("form-prefix-edit")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = document.getElementById("input-prefix-edit-name")?.value?.trim();
    const content = document.getElementById("input-prefix-edit-content")?.value?.trim() || "";
    if (!name) {
      document.getElementById("input-prefix-edit-name")?.focus();
      return;
    }
    if (PrefixEditState.mode === "edit" && PrefixEditState.id) {
      PromptPrefixStore.update(PrefixEditState.id, PrefixEditState.mediaType, { name, content });
    } else {
      PromptPrefixStore.add(name, content, PrefixEditState.mediaType);
    }
    closePrefixEditModal();
    renderPrefixManagerList();
    if (typeof showCanvasToast === "function") showCanvasToast("已保存");
  });

  document.getElementById("btn-prefix-edit-cancel")?.addEventListener("click", () => closePrefixEditModal());
  document.getElementById("btn-prefix-view-close")?.addEventListener("click", () => closePrefixViewModal());
  document.getElementById("btn-prefix-view-done")?.addEventListener("click", () => closePrefixViewModal());
  document.getElementById("modal-prefix-view")?.addEventListener("click", (e) => {
    if (e.target?.id === "modal-prefix-view") closePrefixViewModal();
  });
  document.getElementById("modal-prefix-edit")?.addEventListener("click", (e) => {
    if (e.target?.id === "modal-prefix-edit") closePrefixEditModal();
  });

  document.getElementById("modal-prefix-manager")?.addEventListener("click", (e) => {
    const viewBtn = e.target.closest("[data-prefix-view]");
    if (viewBtn) {
      openPrefixViewModal(viewBtn.dataset.prefixView, viewBtn.dataset.prefixKind);
      return;
    }
    const editBtn = e.target.closest("[data-prefix-edit]");
    if (editBtn) {
      openPrefixEditModal("edit", editBtn.dataset.prefixKind, editBtn.dataset.prefixEdit);
      return;
    }
    const delBtn = e.target.closest("[data-prefix-del]");
    if (delBtn) {
      void confirmDeletePrefix(delBtn.dataset.prefixDel, delBtn.dataset.prefixKind);
      return;
    }

    const defaultOpt = e.target.closest(".prefix-default-option");
    if (defaultOpt) {
      e.stopPropagation();
      const mediaType = defaultOpt.dataset.defaultMedia;
      PromptPrefixStore.setDefault(mediaType, defaultOpt.dataset.defaultPrefix || null);
      renderPrefixManagerList();
      closePrefixManagerMenus(null);
      return;
    }

    const defaultBtn = e.target.closest(".prefix-default-btn");
    if (defaultBtn) {
      e.stopPropagation();
      const mediaType = defaultBtn.dataset.defaultMedia;
      const menu = document.querySelector(`.prefix-default-menu[data-default-media="${mediaType}"]`);
      if (menu) {
        const willOpen = menu.classList.contains("hidden");
        closePrefixManagerMenus(willOpen ? mediaType : null);
        if (willOpen) {
          if (typeof showPillPicker === "function") showPillPicker(menu);
          else menu.classList.remove("hidden");
        } else if (typeof hidePillPicker === "function") hidePillPicker(menu);
        else menu.classList.add("hidden");
      }
      return;
    }

    if (e.target?.id === "modal-prefix-manager") closePrefixManagerDialog();
  });

  document.getElementById("prompt-expand-qm")?.addEventListener(
    "click",
    (e) => {
      const prefixOpt = e.target.closest(".prefix-option");
      if (prefixOpt) {
        e.stopPropagation();
        const nodeId = prefixOpt.dataset.node;
        if (!nodeId || typeof CanvasStore === "undefined") return;
        CanvasStore.updateNode(nodeId, { promptPrefixId: prefixOpt.dataset.prefix ?? "" });
        refreshVideoPickerLabels?.(nodeId);
        // Prefer the menu that owns this option — same node may also have a picker on the card.
        hidePillPicker?.(resolvePrefixMenuEl(prefixOpt));
        syncPromptExpandPrefixPicker(nodeId);
        return;
      }
      const prefixBtn = e.target.closest(".prefix-btn");
      if (prefixBtn) {
        e.stopPropagation();
        const menu = resolvePrefixMenuEl(prefixBtn);
        if (menu) {
          const willOpen = menu.classList.contains("hidden");
          closeVideoPickers?.(willOpen ? menu : null);
          if (willOpen) showPillPicker?.(menu);
          else hidePillPicker?.(menu);
        }
      }
    },
    true
  );
}

window.PromptPrefixStore = PromptPrefixStore;
window.renderPrefixPicker = renderPrefixPicker;
window.renderPrefixPickerCompact = renderPrefixPickerCompact;
window.syncPromptExpandPrefixPicker = syncPromptExpandPrefixPicker;
window.refreshAllPrefixPickers = refreshAllPrefixPickers;
window.openPrefixManagerDialog = openPrefixManagerDialog;

document.addEventListener("DOMContentLoaded", initPrefixManager);
