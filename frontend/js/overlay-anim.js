/** Smooth enter/exit for modals, lightboxes, drawers, and popovers */
(() => {
  const OVERLAY_ANIM_MS = 260;
  /** Stack above fixed panels (agent ~280, history ~1200, studios ~1260). */
  let overlayZ = 1800;

  function isDialog(el) {
    return el && el.tagName === "DIALOG";
  }

  function isOpen(el) {
    if (!el) return false;
    if (isDialog(el)) return !!(el.open && el.classList.contains("is-overlay-open"));
    return !el.classList.contains("hidden") && el.classList.contains("is-overlay-open");
  }

  function bringToFront(el) {
    if (!el || isDialog(el)) return;
    // Native <dialog showModal> already lives in the browser top layer.
    overlayZ += 1;
    el.style.zIndex = String(overlayZ);
  }

  function finishHide(el) {
    if (!el) return;
    el.classList.remove("is-overlay-open");
    if (isDialog(el)) {
      if (el.open) {
        try {
          el.close();
        } catch {
          /* ignore */
        }
      }
    } else {
      el.classList.add("hidden");
    }
  }

  function reveal(el, { immediate = false } = {}) {
    if (!el) return;
    clearTimeout(el._overlayHideTimer);
    el._overlayHideTimer = null;
    el.classList.remove("hidden", "is-overlay-open");
    bringToFront(el);
    if (isDialog(el) && !el.open) {
      try {
        el.showModal();
      } catch {
        return;
      }
    }
    if (immediate) {
      el.classList.add("is-overlay-open");
      return;
    }
    void el.offsetWidth;
    requestAnimationFrame(() => {
      el.classList.add("is-overlay-open");
    });
  }

  function dismiss(el, { immediate = false } = {}) {
    if (!el) return;
    clearTimeout(el._overlayHideTimer);
    if (!isOpen(el) && el.classList.contains("hidden")) return;
    el.classList.remove("is-overlay-open");
    if (immediate) {
      finishHide(el);
      return;
    }
    el._overlayHideTimer = setTimeout(() => {
      finishHide(el);
      el._overlayHideTimer = null;
    }, OVERLAY_ANIM_MS);
  }

  // Clicking an already-open fixed overlay raises it above siblings.
  document.addEventListener(
    "pointerdown",
    (e) => {
      const t = e.target;
      if (!(t instanceof Element)) return;
      const host = t.closest(
        ".is-overlay-open:not(dialog), .agent-panel.is-overlay-open, .history-drawer.is-overlay-open, .gen-history-drawer.is-overlay-open"
      );
      if (host && !isDialog(host)) bringToFront(host);
    },
    true
  );

  window.OverlayAnim = {
    MS: OVERLAY_ANIM_MS,
    isOpen,
    reveal,
    dismiss,
    bringToFront,
    show: reveal,
    hide: dismiss,
    showDialog: reveal,
    closeDialog: dismiss,
  };

  // Global helpers — single definition shared by canvas.js / app.js / auth.js
  window.revealOverlay = reveal;
  window.dismissOverlay = dismiss;
  window.isOverlayOpen = isOpen;

  /** White grain click burst — uses Popover top layer so it shows above canvas AND <dialog>. */
  function spawnChromeClickSpark(clientX, clientY, fromEl) {
    void fromEl;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
    if (document.querySelectorAll(".click-particle-burst, .click-bubble-burst").length >= 12) return;

    const host = document.createElement("span");
    host.className = "click-particle-burst chrome-click-spark";
    host.style.transform = `translate(${clientX}px, ${clientY}px)`;
    // Popover enters the browser top layer (same as showModal dialogs), so sparks
    // stay visible on canvas, workbench, admin, and open settings windows.
    try {
      host.setAttribute("popover", "manual");
    } catch {
      /* older engines */
    }

    const count = 14 + Math.floor(Math.random() * 5);
    for (let i = 0; i < count; i++) {
      const grain = document.createElement("i");
      grain.className = "cbb-grain";
      const ang = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.55;
      const dist = 18 + Math.random() * 34;
      const size =
        Math.random() < 0.22
          ? 8 + Math.random() * 5
          : Math.random() < 0.5
            ? 5 + Math.random() * 3.5
            : 2.5 + Math.random() * 2.5;
      grain.style.setProperty("--dx", `${Math.cos(ang) * dist}px`);
      grain.style.setProperty("--dy", `${Math.sin(ang) * dist}px`);
      grain.style.setProperty("--s", `${size}px`);
      grain.style.setProperty("--delay", `${Math.floor(Math.random() * 30)}ms`);
      host.appendChild(grain);
    }

    const mount = document.body || document.documentElement;
    try {
      mount.appendChild(host);
    } catch {
      return;
    }
    try {
      if (typeof host.showPopover === "function") host.showPopover();
    } catch {
      /* ignore — still visible as fixed on body when no dialog top-layer */
    }
    window.setTimeout(() => {
      try {
        if (typeof host.hidePopover === "function" && host.matches?.(":popover-open")) {
          host.hidePopover();
        }
      } catch {
        /* ignore */
      }
      host.remove();
    }, 720);
  }

  function shouldSpawnClickBubble(e) {
    if (e.button !== 0) return false;
    if (e.pointerType === "mouse" && e.isPrimary === false) return false;
    if (document.documentElement.classList.contains("is-os-file-dragging")) return false;
    if (document.body?.classList.contains("is-node-dragging")) return false;
    const vp = document.getElementById("viewport");
    if (vp?.classList.contains("is-panning")) return false;
    return true;
  }

  function bindClickBubbleBurst() {
    if (window.__clickBubbleBurstBound) return;
    window.__clickBubbleBurstBound = true;
    let lastAt = 0;
    const onDown = (e) => {
      if (!shouldSpawnClickBubble(e)) return;
      const now = typeof performance !== "undefined" ? performance.now() : Date.now();
      if (now - lastAt < 20) return;
      lastAt = now;
      spawnChromeClickSpark(e.clientX, e.clientY, e.target);
    };
    window.addEventListener("pointerdown", onDown, { capture: true, passive: true });
  }

  window.spawnChromeClickSpark = spawnChromeClickSpark;
  window.bindClickBubbleBurst = bindClickBubbleBurst;
  window.__clickSparkUsesPopover = true;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bindClickBubbleBurst());
  } else {
    bindClickBubbleBurst();
  }
})();
