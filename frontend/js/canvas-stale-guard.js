/**
 * Stale canvas session guard — when another tab/device saved this board and this
 * view has been idle 5+ minutes with local edits, block canvas ops and prompt refresh.
 */
(function (global) {
  const OVERLAY_ID = "canvas-stale-overlay";
  const STALE_IDLE_MS = 5 * 60 * 1000;
  const OWN_SAVE_GRACE_MS = 3000;

  let lastActivityAt = 0;
  let hasLocalActivity = false;
  let staleBlocked = false;
  let lastOwnSaveAt = 0;
  let lastOwnSaveUpdatedAt = null;
  let activityBound = false;

  function isInCanvasView() {
    return !document.getElementById("app-root")?.classList.contains("is-workspace");
  }

  function ensureOverlay() {
    let el = document.getElementById(OVERLAY_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.className = "canvas-stale-overlay server-restart-overlay";
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "assertive");
    el.setAttribute("aria-labelledby", "canvas-stale-msg");
    el.innerHTML =
      '<div class="server-restart-card">' +
      '<span class="server-restart-msg" id="canvas-stale-msg">界面已在其他窗口更新，请</span>' +
      '<button type="button" class="server-restart-btn" id="canvas-stale-btn">刷新画布</button>' +
      "</div>";
    document.body.appendChild(el);
    el.querySelector("#canvas-stale-btn")?.addEventListener("click", () => {
      void applyRefresh();
    });
    return el;
  }

  function setBlocked(on) {
    staleBlocked = !!on;
    document.getElementById("app-root")?.classList.toggle("is-canvas-stale-blocked", staleBlocked);
    ensureOverlay().classList.toggle("is-visible", staleBlocked);
    document.body.classList.toggle("is-canvas-stale-blocked", staleBlocked);
  }

  function blockStaleInput(e) {
    if (!staleBlocked) return;
    if (e.target?.closest?.(`#${OVERLAY_ID}`)) return;
    e.stopPropagation();
    e.preventDefault();
  }

  function hideOverlay() {
    setBlocked(false);
  }

  function showOverlay() {
    if (staleBlocked) return;
    setBlocked(true);
  }

  function markActivity() {
    if (!isInCanvasView()) return;
    lastActivityAt = Date.now();
    hasLocalActivity = true;
  }

  function bindActivityListeners() {
    if (activityBound) return;
    const world = document.getElementById("canvas-world");
    if (!world) return;
    activityBound = true;
    const mark = () => markActivity();
    world.addEventListener("pointerdown", mark, { capture: true, passive: true });
    world.addEventListener("wheel", mark, { capture: true, passive: true });
    document.addEventListener(
      "keydown",
      (e) => {
        if (!isInCanvasView() || staleBlocked) return;
        if (e.key === "Shift" || e.key === "Control" || e.key === "Alt" || e.key === "Meta") return;
        mark();
      },
      { capture: true }
    );
    document.addEventListener("pointerdown", blockStaleInput, true);
    document.addEventListener("keydown", blockStaleInput, true);
    document.addEventListener("wheel", blockStaleInput, { capture: true, passive: false });
  }

  function onEnterCanvas() {
    hideOverlay();
    lastActivityAt = Date.now();
    hasLocalActivity = false;
    bindActivityListeners();
  }

  function onLeaveCanvas() {
    hideOverlay();
    hasLocalActivity = false;
  }

  function notifyOwnSave(updatedAt) {
    lastOwnSaveAt = Date.now();
    lastOwnSaveUpdatedAt = updatedAt || null;
  }

  function isOwnSave(serverUpdatedAt) {
    if (lastOwnSaveUpdatedAt && serverUpdatedAt === lastOwnSaveUpdatedAt) return true;
    return Date.now() - lastOwnSaveAt < OWN_SAVE_GRACE_MS;
  }

  /**
   * Returns true when sync apply should be skipped (stale overlay shown).
   */
  function handleRemoteUpdate(serverUpdatedAt) {
    if (staleBlocked) return true;
    if (!isInCanvasView()) return false;
    if (isOwnSave(serverUpdatedAt)) return false;
    // Never touched this session — silently accept server (single-tab idle view).
    if (!hasLocalActivity) return false;
    const idleMs = Date.now() - lastActivityAt;
    if (idleMs < STALE_IDLE_MS) return false;
    showOverlay();
    return true;
  }

  async function applyRefresh() {
    hideOverlay();
    lastActivityAt = Date.now();
    hasLocalActivity = false;
    try {
      if (typeof global.refreshCanvasView === "function") {
        await global.refreshCanvasView({ fromStaleGuard: true });
      } else if (global.CanvasStore?.syncFromServer) {
        await global.CanvasStore.syncFromServer({ force: true });
      }
    } catch (err) {
      console.warn("[canvas-stale-guard] refresh failed", err);
      if (typeof global.showCanvasToast === "function") {
        global.showCanvasToast(err?.message || "刷新失败");
      }
    }
  }

  global.CanvasStaleGuard = {
    markActivity,
    onEnterCanvas,
    onLeaveCanvas,
    notifyOwnSave,
    handleRemoteUpdate,
    isBlocked: () => staleBlocked,
    isInCanvasView,
  };
})(window);
