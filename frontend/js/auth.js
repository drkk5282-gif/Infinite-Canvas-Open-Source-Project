(() => {
  const TOKEN_KEY = "canvas_session_token";
  const LOCAL_TOKEN = "local-operator";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || LOCAL_TOKEN;
  }

  function setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.setItem(TOKEN_KEY, LOCAL_TOKEN);
  }

  function isLoggedIn() {
    return true;
  }

  function getUsername() {
    return String(
      window.__canvasUser?.display_name || window.__canvasUser?.username || "本机"
    ).trim();
  }

  function getUserCacheKey() {
    const uid = window.__canvasUser?.id || window.__canvasUser?.user_id;
    if (uid) return `u_${uid}`;
    const name = getUsername();
    return name || "local";
  }

  let _authBootstrapping = false;

  function avatarLetter(user) {
    const name = String(user?.display_name || user?.username || "").trim();
    if (!name) return "—";
    return [...name][0] || "—";
  }

  let userPopAnchor = null;
  let internalPopAnchor = null;

  function closeUserPop() {
    const pop = document.getElementById("workspace-user-pop");
    if (pop) window.OverlayAnim?.dismiss(pop);
    document.querySelectorAll("#btn-workspace-user, #btn-canvas-user, #btn-workbench-user").forEach((btn) => {
      btn.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    });
    userPopAnchor = null;
  }

  function closeInternalPop() {
    const pop = document.getElementById("workspace-internal-pop");
    if (pop) window.OverlayAnim?.dismiss(pop);
    document.querySelectorAll("#btn-workspace-internal, #btn-canvas-internal, #btn-workbench-internal").forEach((btn) => {
      btn.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
    });
    internalPopAnchor = null;
  }

  function closeAllAccountPops() {
    closeUserPop();
    closeInternalPop();
  }

  /** Square avatar crop: default center; user may drag/resize before confirm. */
  const AvatarCrop = {
    iw: 0,
    ih: 0,
    x: 0,
    y: 0,
    side: 0,
    _bound: false,
    _drag: null,
    _srcUrl: "",
    _resolve: null,
    _objectUrl: "",

    ensureDom() {
      let root = document.getElementById("avatar-crop-studio");
      // Must be <dialog> so it stacks above「编辑资料」native top-layer dialog.
      if (root && root.tagName !== "DIALOG") {
        root.remove();
        root = null;
        this._bound = false;
      }
      if (root) return root;
      root = document.createElement("dialog");
      root.id = "avatar-crop-studio";
      root.className = "avatar-crop-studio hidden";
      root.setAttribute("aria-labelledby", "avatar-crop-title");
      root.innerHTML = `
        <div class="avatar-crop-backdrop" id="avatar-crop-backdrop"></div>
        <div class="avatar-crop-panel">
          <div class="avatar-crop-head">
            <div>
              <h3 id="avatar-crop-title">裁剪头像</h3>
              <p>拖动或缩放选区；不调整则默认居中裁剪</p>
            </div>
            <button type="button" class="icon-btn" id="avatar-crop-close" title="关闭" aria-label="关闭">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            </button>
          </div>
          <div class="avatar-crop-stage" id="avatar-crop-stage">
            <img id="avatar-crop-image" alt="" draggable="false" />
            <div class="avatar-crop-box" id="avatar-crop-box">
              <span class="avatar-crop-handle" data-corner="nw"></span>
              <span class="avatar-crop-handle" data-corner="ne"></span>
              <span class="avatar-crop-handle" data-corner="sw"></span>
              <span class="avatar-crop-handle" data-corner="se"></span>
            </div>
          </div>
          <div class="avatar-crop-foot">
            <button type="button" class="btn btn-ghost" id="avatar-crop-cancel">取消</button>
            <button type="button" class="btn btn-primary" id="avatar-crop-confirm">使用此区域</button>
          </div>
        </div>`;
      document.body.appendChild(root);
      return root;
    },

    bind() {
      if (this._bound) return;
      this._bound = true;
      const root = this.ensureDom();
      const finish = (blob) => this._finish(blob);
      root.querySelector("#avatar-crop-close")?.addEventListener("click", () => finish(null));
      root.querySelector("#avatar-crop-cancel")?.addEventListener("click", () => finish(null));
      root.querySelector("#avatar-crop-backdrop")?.addEventListener("click", () => finish(null));
      root.addEventListener("cancel", (e) => {
        e.preventDefault();
        finish(null);
      });
      root.querySelector("#avatar-crop-confirm")?.addEventListener("click", () => {
        void this.exportBlob().then((blob) => finish(blob));
      });
      window.addEventListener("resize", () => {
        if (this.isOpen()) this.layoutBox();
      });
      const box = root.querySelector("#avatar-crop-box");
      box?.addEventListener("pointerdown", (e) => {
        if (!this.isOpen()) return;
        const corner = e.target.closest("[data-corner]")?.dataset.corner || "move";
        const disp = this.getDisplayRect();
        if (!disp) return;
        e.preventDefault();
        e.stopPropagation();
        box.setPointerCapture?.(e.pointerId);
        this._drag = {
          mode: corner,
          startX: e.clientX,
          startY: e.clientY,
          ox: this.x,
          oy: this.y,
          os: this.side,
          scale: disp.scale,
        };
      });
      window.addEventListener("pointermove", (e) => {
        if (!this._drag || !this.isOpen()) return;
        const dx = (e.clientX - this._drag.startX) / this._drag.scale;
        const dy = (e.clientY - this._drag.startY) / this._drag.scale;
        const mode = this._drag.mode;
        const minSide = Math.min(this.iw, this.ih, 32);
        if (mode === "move") {
          this.x = this._drag.ox + dx;
          this.y = this._drag.oy + dy;
        } else {
          const ox = this._drag.ox;
          const oy = this._drag.oy;
          const os = this._drag.os;
          let nx = ox;
          let ny = oy;
          let ns = os;
          if (mode === "se") {
            ns = os + Math.max(dx, dy);
            nx = ox;
            ny = oy;
          } else if (mode === "nw") {
            ns = os + Math.max(-dx, -dy);
            nx = ox + os - ns;
            ny = oy + os - ns;
          } else if (mode === "ne") {
            ns = os + Math.max(dx, -dy);
            nx = ox;
            ny = oy + os - ns;
          } else if (mode === "sw") {
            ns = os + Math.max(-dx, dy);
            nx = ox + os - ns;
            ny = oy;
          }
          ns = Math.max(minSide, ns);
          this.x = nx;
          this.y = ny;
          this.side = ns;
        }
        this.clamp();
        this.layoutBox();
      });
      window.addEventListener("pointerup", () => {
        this._drag = null;
      });
      window.addEventListener("pointercancel", () => {
        this._drag = null;
      });
    },

    isOpen() {
      const root = document.getElementById("avatar-crop-studio");
      if (!root || root.classList.contains("hidden")) return false;
      if (root.tagName === "DIALOG") return !!root.open;
      return true;
    },

    resetCenter() {
      const side = Math.min(this.iw, this.ih);
      this.side = side;
      this.x = (this.iw - side) / 2;
      this.y = (this.ih - side) / 2;
    },

    clamp() {
      const minSide = Math.min(this.iw, this.ih, 32);
      this.side = Math.max(minSide, Math.min(this.side, this.iw, this.ih));
      this.x = Math.max(0, Math.min(this.x, this.iw - this.side));
      this.y = Math.max(0, Math.min(this.y, this.ih - this.side));
    },

    fitImage() {
      const stage = document.getElementById("avatar-crop-stage");
      const img = document.getElementById("avatar-crop-image");
      if (!stage || !img || !(this.iw > 0 && this.ih > 0)) return null;
      const sw = stage.clientWidth;
      const sh = stage.clientHeight;
      if (!(sw > 0 && sh > 0)) return null;
      const scale = Math.min(sw / this.iw, sh / this.ih);
      const dw = this.iw * scale;
      const dh = this.ih * scale;
      img.style.width = `${dw}px`;
      img.style.height = `${dh}px`;
      return { scale, dw, dh };
    },

    getDisplayRect() {
      const stage = document.getElementById("avatar-crop-stage");
      const img = document.getElementById("avatar-crop-image");
      if (!stage || !img || !(this.iw > 0 && this.ih > 0)) return null;
      const fitted = this.fitImage();
      if (!fitted) return null;
      // Prefer real rendered box so crop ring tracks the image (not theoretical flex math).
      const sr = stage.getBoundingClientRect();
      const ir = img.getBoundingClientRect();
      if (ir.width > 1 && ir.height > 1) {
        return {
          left: ir.left - sr.left,
          top: ir.top - sr.top,
          scale: ir.width / this.iw,
          dw: ir.width,
          dh: ir.height,
        };
      }
      const left = (stage.clientWidth - fitted.dw) / 2;
      const top = (stage.clientHeight - fitted.dh) / 2;
      return { left, top, scale: fitted.scale, dw: fitted.dw, dh: fitted.dh };
    },

    layoutBox() {
      const box = document.getElementById("avatar-crop-box");
      const disp = this.getDisplayRect();
      if (!box || !disp) return;
      box.style.left = `${disp.left + this.x * disp.scale}px`;
      box.style.top = `${disp.top + this.y * disp.scale}px`;
      box.style.width = `${this.side * disp.scale}px`;
      box.style.height = `${this.side * disp.scale}px`;
    },

    scheduleLayout() {
      requestAnimationFrame(() => {
        this.layoutBox();
        requestAnimationFrame(() => this.layoutBox());
      });
    },

    exportBlob() {
      return new Promise((resolve) => {
        const img = document.getElementById("avatar-crop-image");
        if (!img?.complete) {
          resolve(null);
          return;
        }
        this.clamp();
        const out = Math.min(512, Math.max(64, Math.round(this.side)));
        const canvas = document.createElement("canvas");
        canvas.width = out;
        canvas.height = out;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          resolve(null);
          return;
        }
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(
          img,
          this.x,
          this.y,
          this.side,
          this.side,
          0,
          0,
          out,
          out
        );
        canvas.toBlob(
          (blob) => resolve(blob || null),
          "image/jpeg",
          0.92
        );
      });
    },

    _cleanupSrc() {
      const img = document.getElementById("avatar-crop-image");
      if (img) {
        img.onload = null;
        img.removeAttribute("src");
        img.style.width = "";
        img.style.height = "";
      }
      if (this._objectUrl) {
        URL.revokeObjectURL(this._objectUrl);
        this._objectUrl = "";
      }
      this._srcUrl = "";
    },

    _finish(blob) {
      const root = document.getElementById("avatar-crop-studio");
      if (root) {
        // Close immediately. Delayed OverlayAnim leave + display:flex on closed
        // <dialog> left a black unclickable crop ghost on screen.
        if (window.OverlayAnim?.dismiss) {
          window.OverlayAnim.dismiss(root, { immediate: true });
        } else if (root.tagName === "DIALOG" && root.open) {
          try {
            root.close();
          } catch {
            /* ignore */
          }
        }
        root.classList.remove("is-overlay-open");
        root.classList.add("hidden");
      }
      this._cleanupSrc();
      this._drag = null;
      const resolve = this._resolve;
      this._resolve = null;
      resolve?.(blob);
    },

    open(file) {
      this.bind();
      const root = this.ensureDom();
      this._cleanupSrc();
      this._objectUrl = URL.createObjectURL(file);
      this._srcUrl = this._objectUrl;
      const img = document.getElementById("avatar-crop-image");
      return new Promise((resolve) => {
        this._resolve = resolve;
        if (!img) {
          resolve(null);
          return;
        }
        img.onload = () => {
          this.iw = img.naturalWidth || 0;
          this.ih = img.naturalHeight || 0;
          this.resetCenter();
          this.scheduleLayout();
        };
        img.src = this._srcUrl;
        root.classList.remove("hidden");
        if (window.OverlayAnim?.reveal) window.OverlayAnim.reveal(root);
        else {
          try {
            if (root.tagName === "DIALOG" && !root.open) root.showModal();
          } catch {
            /* ignore */
          }
          requestAnimationFrame(() => root.classList.add("is-overlay-open"));
        }
        this.scheduleLayout();
      });
    },
  };

  function placePopover(pop, anchorBtn) {
    const rect = anchorBtn.getBoundingClientRect();
    pop.style.position = "fixed";
    pop.style.top = `${rect.bottom + 8}px`;
    pop.style.right = "auto";
    pop.classList.remove("hidden", "is-overlay-open");
    const menuW = pop.offsetWidth || 168;
    let left = rect.right - menuW;
    left = Math.max(8, Math.min(left, window.innerWidth - menuW - 8));
    pop.style.left = `${left}px`;
  }

  function toggleUserPop(anchorBtn) {
    const pop = document.getElementById("workspace-user-pop");
    if (!pop || !anchorBtn) return;
    const reopen = window.OverlayAnim?.isOpen?.(pop) && userPopAnchor === anchorBtn;
    closeAllAccountPops();
    if (typeof window.closeExclusiveChromePopovers === "function") {
      window.closeExclusiveChromePopovers("user");
    } else {
      window.AgentPanel?.close?.();
      window.NodeListPanel?.close?.();
    }
    if (reopen) return;
    userPopAnchor = anchorBtn;
    placePopover(pop, anchorBtn);
    if (typeof window.syncConnFlowAnimMenuButton === "function") {
      window.syncConnFlowAnimMenuButton();
    }
    if (typeof window.AppTheme?.syncMenu === "function") window.AppTheme.syncMenu();
    window.OverlayAnim?.reveal(pop);
    anchorBtn.classList.add("is-open");
    anchorBtn.setAttribute("aria-expanded", "true");
  }

  function toggleInternalPop(anchorBtn) {
    const pop = document.getElementById("workspace-internal-pop");
    if (!pop || !anchorBtn) return;
    const reopen = window.OverlayAnim?.isOpen?.(pop) && internalPopAnchor === anchorBtn;
    closeAllAccountPops();
    if (typeof window.closeExclusiveChromePopovers === "function") {
      window.closeExclusiveChromePopovers("internal");
    } else {
      window.AgentPanel?.close?.();
      window.NodeListPanel?.close?.();
    }
    if (reopen) return;
    internalPopAnchor = anchorBtn;
    placePopover(pop, anchorBtn);
    void refreshPoolSummary();
    window.OverlayAnim?.reveal(pop);
    anchorBtn.classList.add("is-open");
    anchorBtn.setAttribute("aria-expanded", "true");
  }

  function formatPointsDisplay(value) {
    const raw = value != null ? String(value) : "0.000";
    const n = Number(raw);
    if (!Number.isFinite(n)) return raw;
    return n.toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 3,
    });
  }

  function parsePointsValue(value) {
    const n = Number(value != null ? String(value) : "0");
    return Number.isFinite(n) ? n : 0;
  }

  /** Odometer-style digit reel for points badge. */
  function setRollingPointsValue(el, nextValue, { animate = true } = {}) {
    if (!el) return;
    const text = formatPointsDisplay(nextValue);
    const prev = el.dataset.value != null ? String(el.dataset.value) : "";
    el.dataset.value = text;
    if (!animate || prev === text || !prev) {
      el.textContent = text;
      el.classList.remove("is-rolling");
      return;
    }
    const chars = Array.from(text);
    el.classList.add("is-rolling");
    el.innerHTML = chars
      .map((ch) => {
        if (!/\d/.test(ch)) {
          return `<span class="points-roll-sep">${ch}</span>`;
        }
        const digit = Number(ch);
        const strip = Array.from({ length: 10 }, (_, i) => `<span>${i}</span>`).join("");
        return `<span class="points-roll-digit"><span class="points-roll-strip" style="--digit:${digit}">${strip}</span></span>`;
      })
      .join("");
    requestAnimationFrame(() => {
      el.querySelectorAll(".points-roll-strip").forEach((strip) => {
        const d = Number(strip.style.getPropertyValue("--digit")) || 0;
        strip.style.transform = `translateY(${-d * 10}%)`;
      });
    });
    window.setTimeout(() => {
      if (el.dataset.value === text) {
        el.textContent = text;
        el.classList.remove("is-rolling");
      }
    }, 720);
  }

  function updatePointsBadges(user) {
    const guest = !user;
    const pointsOn = window.AppConfig?.points_enabled !== false;
    const pts = user?.points;
    const inWorkbench = document.documentElement.classList.contains("route-workbench");
    const workspaceBadge = document.getElementById("workspace-points-badge");
    const workspaceValue = document.getElementById("workspace-points-value");
    const canvasBadge = document.getElementById("canvas-points-badge");
    const canvasValue = document.getElementById("canvas-points-value");
    const workbenchBadge = document.getElementById("workbench-points-badge");
    const workbenchValue = document.getElementById("workbench-points-value");
    const animate = pointsOn && !guest && !!workspaceBadge && !workspaceBadge.classList.contains("hidden");
    setRollingPointsValue(workspaceValue, pts, { animate });
    setRollingPointsValue(canvasValue, pts, { animate });
    setRollingPointsValue(workbenchValue, pts, { animate });
    if (workspaceBadge) workspaceBadge.classList.toggle("hidden", guest || !pointsOn || inWorkbench);
    if (workbenchBadge) workbenchBadge.classList.toggle("hidden", guest || !pointsOn || !inWorkbench);
    if (canvasBadge) {
      const inCanvas = !document.getElementById("app-root")?.classList.contains("is-workspace");
      canvasBadge.classList.toggle("hidden", guest || !inCanvas || !pointsOn);
    }
  }

  function applyAvatarFace(letterEl, imgEl, user, { bust = false } = {}) {
    let url = String(user?.avatar_url || "").trim();
    const letter = user ? avatarLetter(user) : "?";
    if (letterEl) letterEl.textContent = letter;
    if (!imgEl) return;
    if (url) {
      if (bust) {
        const join = url.includes("?") ? "&" : "?";
        url = `${url}${join}_=${Date.now()}`;
      }
      const onErr = () => {
        imgEl.removeAttribute("src");
        imgEl.classList.add("hidden");
        letterEl?.classList.remove("hidden");
        imgEl.removeEventListener("error", onErr);
      };
      imgEl.addEventListener("error", onErr);
      // Force reload even when path is unchanged (browser cache / same ?v= second).
      imgEl.removeAttribute("src");
      imgEl.src = url;
      imgEl.classList.remove("hidden");
      letterEl?.classList.add("hidden");
    } else {
      imgEl.removeAttribute("src");
      imgEl.classList.add("hidden");
      letterEl?.classList.remove("hidden");
    }
  }

  function updateUserUI(user, opts = {}) {
    const resolved = user || window.__canvasUser || { username: "本机", display_name: "本机", role: "admin" };
    const guest = false;
    const name = resolved.display_name || resolved.username || "本机";
    const nameEl = document.getElementById("workspace-user-name");
    const idRow = document.getElementById("workspace-user-id-row");
    const idValueEl = document.getElementById("workspace-user-id-value");
    if (nameEl) nameEl.textContent = name;
    const uid = String(resolved.id || resolved.user_id || "").trim();
    if (idValueEl) idValueEl.textContent = uid;
    if (idRow) {
      idRow.classList.toggle("hidden", !uid);
      idRow.dataset.userId = uid;
    }
    user = resolved;
    const faceOpts = { bust: !!opts.bustAvatar };
    applyAvatarFace(
      document.getElementById("workspace-user-avatar"),
      document.getElementById("workspace-user-avatar-img"),
      user,
      faceOpts
    );
    applyAvatarFace(
      document.getElementById("canvas-user-avatar"),
      document.getElementById("canvas-user-avatar-img"),
      user,
      faceOpts
    );
    applyAvatarFace(
      document.getElementById("workbench-user-avatar"),
      document.getElementById("workbench-user-avatar-img"),
      user,
      faceOpts
    );
    applyAvatarFace(
      document.getElementById("profile-edit-avatar-letter"),
      document.getElementById("profile-edit-avatar-img"),
      user,
      faceOpts
    );
    updatePointsBadges(user);
    const loginBtn = document.getElementById("btn-workspace-login");
    const logoutBtn = document.getElementById("btn-workspace-logout");
    const myapiBtn = document.getElementById("btn-workspace-myapi");
    const pointsBtn = document.getElementById("btn-workspace-points");
    const editProfileBtn = document.getElementById("btn-workspace-edit-profile");
    const pointsOn = window.AppConfig?.points_enabled !== false;
    if (loginBtn) loginBtn.classList.add("hidden");
    if (logoutBtn) logoutBtn.classList.add("hidden");
    if (editProfileBtn) editProfileBtn.classList.add("hidden");
    if (pointsBtn) pointsBtn.classList.toggle("hidden", guest || !pointsOn);
    const allowCustom = window.AppConfig?.allow_frontend_custom_models !== false;
    if (myapiBtn) myapiBtn.classList.toggle("hidden", guest || !allowCustom);
    const role = String(user?.role || "").toLowerCase();
    const isStaff = !guest && (role === "admin" || role === "internal");
    document.querySelectorAll("#btn-workspace-internal, #btn-canvas-internal, #btn-workbench-internal").forEach((btn) => {
      let visible = isStaff;
      if (btn.id === "btn-canvas-internal") {
        const inCanvas = !document.getElementById("app-root")?.classList.contains("is-workspace");
        visible = isStaff && inCanvas;
      } else if (btn.id === "btn-workbench-internal") {
        const inWorkbench = document.documentElement.classList.contains("route-workbench");
        visible = isStaff && inWorkbench;
      }
      btn.classList.toggle("hidden", !visible);
    });
    const bendiBtn = document.getElementById("btn-workspace-bendi");
    if (bendiBtn) {
      const bendiOk = isStaff && !!window.AppConfig?.bendi_allowed;
      bendiBtn.classList.toggle("hidden", !bendiOk);
    }
    const adminBtn = document.getElementById("btn-workspace-admin");
    if (adminBtn) {
      const isAdmin = !guest && role === "admin";
      adminBtn.classList.toggle("hidden", !isAdmin);
      const port = Number(window.AppConfig?.admin_port) || 8090;
      adminBtn.textContent = `管理后台（${port}）`;
    }
    const canPool =
      isStaff &&
      !(
        window.AppConfig?.disable_browser_channels ||
        window.AppConfig?.disable_doubao ||
        window.AppConfig?.browser_channels_allowed === false
      );
    const addDoubaoBtn = document.getElementById("btn-workspace-add-doubao");
    if (addDoubaoBtn) addDoubaoBtn.classList.toggle("hidden", !canPool);
    const poolShareBtn = document.getElementById("btn-workspace-pool-share");
    if (poolShareBtn) poolShareBtn.classList.toggle("hidden", !canPool);
    const poolStats = document.getElementById("workspace-pool-stats");
    if (poolStats) {
      poolStats.classList.toggle("hidden", !canPool);
      if (!canPool) clearPoolStats();
    }
    const upscaleBtn = document.getElementById("btn-workspace-upscale-link");
    if (upscaleBtn) {
      const upscaleOn = window.AppConfig?.upscale_enabled !== false;
      const perUser = window.AppConfig?.upscale_pool_mode === "per_user";
      upscaleBtn.classList.toggle("hidden", !isStaff || !upscaleOn || !perUser);
    }
    window.updateCanvasTopChrome?.();
    window.updateWorkbenchTopChrome?.();
    window.PromptPrefixStore?.invalidate?.();
    window.refreshAllPrefixPickers?.();
  }

  function clearPoolStats() {
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    set("workspace-pool-total", "0");
    set("workspace-pool-s", "0");
    set("workspace-pool-regular", "0");
    set("workspace-pool-used-n", "0");
  }

  function formatPoolCount(total, shared, available) {
    const n = Number(total) || 0;
    const s = Number(shared) || 0;
    const availRaw = Number(available);
    const avail = Number.isFinite(availRaw) ? Math.max(0, availRaw) : n;
    let out = String(n);
    if (s > 0) out += `（享${s}）`;
    out += `（可${avail}）`;
    return out;
  }

  function applyPoolSummary(data) {
    if (!data) return;
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(v ?? 0);
    };
    set(
      "workspace-pool-total",
      formatPoolCount(data.total, data.shared_count, data.unused_count)
    );
    set(
      "workspace-pool-s",
      formatPoolCount(data.s_count, data.shared_s_count, data.unused_s_count)
    );
    set(
      "workspace-pool-regular",
      formatPoolCount(
        data.regular_count,
        data.shared_regular_count,
        data.unused_regular_count
      )
    );
    set("workspace-pool-used-n", data.used_today_count ?? 0);
    window.__poolSummary = data;
  }

  async function refreshPoolSummary() {
    const role = String(window.__canvasUser?.role || "").toLowerCase();
    if (role !== "admin" && role !== "internal") return null;
    try {
      const t = getToken();
      const res = await fetch("/api/accounts/pool-summary", {
        headers: t ? { Authorization: `Bearer ${t}` } : {},
      });
      if (!res.ok) return null;
      const data = await res.json();
      applyPoolSummary(data);
      return data;
    } catch {
      return null;
    }
  }

  function closePoolShareDialog() {
    window.OverlayAnim?.dismiss(document.getElementById("modal-pool-share"));
  }

  function renderPoolShareLists(summary) {
    const outList = document.getElementById("pool-share-recipients-list");
    const emptyHint = document.getElementById("pool-share-empty-hint");
    const inWrap = document.getElementById("pool-share-incoming-wrap");
    const inList = document.getElementById("pool-share-incoming-list");
    const sharesOut = Array.isArray(summary?.shares_out) ? summary.shares_out : [];
    const sharesIn = Array.isArray(summary?.shares_in) ? summary.shares_in : [];
    if (outList) {
      outList.innerHTML = sharesOut
        .map((s) => {
          const label = s.display_name || s.username || s.user_id || "—";
          const uid = s.user_id || "";
          return `<li class="share-recipient-item"><span>${escapeHtml(label)}</span><button type="button" class="btn btn-ghost btn-sm pool-share-revoke" data-user-id="${escapeHtml(uid)}">取消</button></li>`;
        })
        .join("");
      outList.querySelectorAll(".pool-share-revoke").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.userId;
          if (!id) return;
          btn.disabled = true;
          try {
            const t = getToken();
            const res = await fetch(`/api/accounts/pool-share/${encodeURIComponent(id)}`, {
              method: "DELETE",
              headers: t ? { Authorization: `Bearer ${t}` } : {},
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "取消失败");
            applyPoolSummary(data);
            renderPoolShareLists(data);
          } catch (err) {
            if (typeof AppDialog?.alert === "function") {
              await AppDialog.alert(err.message || "取消失败", { title: "共享额度" });
            }
          } finally {
            btn.disabled = false;
          }
        });
      });
    }
    if (emptyHint) emptyHint.hidden = sharesOut.length > 0;
    if (inWrap && inList) {
      inWrap.hidden = sharesIn.length === 0;
      inList.innerHTML = sharesIn
        .map((s) => {
          const label = s.display_name || s.username || "—";
          const n = Number(s.unused_available) || 0;
          return `<li class="share-recipient-item"><span>${escapeHtml(label)}</span><span class="share-recipient-meta">可用未用 ${n}</span></li>`;
        })
        .join("");
    }
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function openPoolShareDialog() {
    const dlg = document.getElementById("modal-pool-share");
    if (!dlg) return;
    const summary = (await refreshPoolSummary()) || window.__poolSummary || {};
    renderPoolShareLists(summary);
    const input = document.getElementById("input-pool-share-username");
    const errEl = document.getElementById("pool-share-err");
    const okEl = document.getElementById("pool-share-ok");
    if (input) input.value = "";
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
    if (okEl) {
      okEl.textContent = "";
      okEl.classList.add("hidden");
    }
    if (typeof window.OverlayAnim?.reveal === "function") window.OverlayAnim.reveal(dlg);
    else if (typeof dlg.showModal === "function") dlg.showModal();
    requestAnimationFrame(() => input?.focus?.());
  }

  function setPoolShareFeedback(kind, message) {
    const errEl = document.getElementById("pool-share-err");
    const okEl = document.getElementById("pool-share-ok");
    if (errEl) {
      errEl.textContent = kind === "err" ? message || "" : "";
      errEl.classList.toggle("hidden", kind !== "err" || !message);
    }
    if (okEl) {
      okEl.textContent = kind === "ok" ? message || "" : "";
      okEl.classList.toggle("hidden", kind !== "ok" || !message);
    }
  }

  async function refreshPointsBalance() {
    if (!isLoggedIn()) return;
    try {
      const t = getToken();
      const res = await fetch("/api/auth/me", {
        headers: t ? { Authorization: `Bearer ${t}` } : {},
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data?.user) {
        window.__canvasUser = { ...(window.__canvasUser || {}), ...data.user };
        updateUserUI(window.__canvasUser);
      }
    } catch {
      /* ignore */
    }
  }

  function closeUpscaleLinkDialog() {
    window.OverlayAnim?.dismiss(document.getElementById("upscale-link-dialog"));
  }

  const pointsHistoryState = {
    category: "all",
    offset: 0,
    limit: 40,
    total: 0,
    loading: false,
  };

  const rechargeState = {
    paying: false,
    payId: "",
    pollTimer: null,
  };

  function escapePointsHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatPointsHistoryTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso).replace("T", " ");
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function formatSignedPoints(signed) {
    const n = typeof signed === "number" ? signed : parsePointsValue(signed);
    const label = formatPointsDisplay(Math.abs(n));
    if (n > 0) return `+${label}`;
    if (n < 0) return `-${label}`;
    return label;
  }

  function closePointsHistoryDialog() {
    window.OverlayAnim?.dismiss(document.getElementById("points-history-dialog"));
  }

  function getPaymentConfig() {
    const cfg = window.AppConfig || {};
    return {
      enabled: !!cfg.payment_enabled,
      wx: !!cfg.payment_wx_enabled,
      alipay: !!cfg.payment_alipay_enabled,
      min: Math.max(0.01, parseFloat(cfg.payment_min_amount) || 5),
      rate: Math.max(0.001, parseFloat(cfg.payment_rate) || 1),
    };
  }

  function syncRechargePanel() {
    const pay = getPaymentConfig();
    const wrap = document.getElementById("points-history-recharge");
    const sub = document.querySelector(".points-history-sub");
    const wxBtn = document.getElementById("btn-recharge-wx");
    const aliBtn = document.getElementById("btn-recharge-alipay");
    const amountInput = document.getElementById("points-recharge-amount-input");
    if (wrap) wrap.classList.toggle("hidden", !pay.enabled);
    if (sub) {
      sub.textContent = pay.enabled
        ? "后台发放 · 生成消耗 · 在线充值"
        : "后台发放 · 生成消耗 · 失败退款";
    }
    wxBtn?.classList.toggle("hidden", !pay.enabled || !pay.wx);
    aliBtn?.classList.toggle("hidden", !pay.enabled || !pay.alipay);
    if (amountInput) {
      amountInput.min = String(pay.min);
      amountInput.step = pay.min < 1 ? "0.01" : "0.01";
      amountInput.placeholder = `最低 ${pay.min}`;
      if (!amountInput.value) amountInput.value = String(pay.min);
    }
  }

  function selectedRechargeAmount() {
    const n = parseFloat(document.getElementById("points-recharge-amount-input")?.value);
    return Number.isFinite(n) ? n : NaN;
  }

  function stopPayPoll() {
    if (rechargeState.pollTimer) {
      clearInterval(rechargeState.pollTimer);
      rechargeState.pollTimer = null;
    }
  }

  function closePointsPayDialog() {
    stopPayPoll();
    rechargeState.paying = false;
    rechargeState.payId = "";
    document.querySelectorAll(".points-recharge-channel").forEach((b) => {
      b.disabled = false;
    });
    window.OverlayAnim?.dismiss(document.getElementById("points-pay-dialog"));
  }

  async function onPaySuccess(order) {
    stopPayPoll();
    const statusEl = document.getElementById("points-pay-status");
    if (statusEl) statusEl.textContent = `支付成功，已到账 ${order.points || ""} 积分`;
    await refreshPointsBalance();
    pointsHistoryState.offset = 0;
    await fetchPointsHistory({ append: false });
    setTimeout(() => closePointsPayDialog(), 900);
  }

  async function startPayPoll(payId) {
    stopPayPoll();
    let ticks = 0;
    rechargeState.pollTimer = setInterval(async () => {
      ticks += 1;
      if (ticks > 120) {
        stopPayPoll();
        const statusEl = document.getElementById("points-pay-status");
        if (statusEl) statusEl.textContent = "超时未检测到支付，可关闭后重试";
        return;
      }
      try {
        const res = await fetch(`/api/pay/status?pay_id=${encodeURIComponent(payId)}`, {
          headers: { ...window.CanvasAuth?.headers?.() },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.credited || data.state === "paid") {
          await onPaySuccess(data);
        } else if (data.state === "expired" || data.state === "closed") {
          stopPayPoll();
          const statusEl = document.getElementById("points-pay-status");
          if (statusEl) statusEl.textContent = "订单已关闭或过期";
        }
      } catch {
        /* ignore */
      }
    }, 2200);
  }

  async function createRechargeOrder(payType) {
    const pay = getPaymentConfig();
    if (!pay.enabled || rechargeState.paying) return;
    const amount = selectedRechargeAmount();
    if (!Number.isFinite(amount) || amount < pay.min) {
      window.AppDialog?.alert?.(`最低充值 ${pay.min} 积分`) || alert(`最低充值 ${pay.min} 积分`);
      return;
    }
    rechargeState.paying = true;
    document.querySelectorAll(".points-recharge-channel").forEach((b) => {
      b.disabled = true;
    });
    try {
      const res = await fetch("/api/pay/create", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...window.CanvasAuth?.headers?.(),
        },
        body: JSON.stringify({ type: payType, amount }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.msg || "创建订单失败");
      rechargeState.payId = data.pay_id || "";
      const dialog = document.getElementById("points-pay-dialog");
      const qr = document.getElementById("points-pay-qr");
      const title = document.getElementById("points-pay-title");
      const amountEl = document.getElementById("points-pay-amount");
      const statusEl = document.getElementById("points-pay-status");
      const hint = document.getElementById("points-pay-hint");
      if (qr) qr.src = data.qr_data_url || "";
      if (title) title.textContent = payType === 1 ? "微信扫码支付" : "支付宝扫码支付";
      if (amountEl) {
        amountEl.textContent = `¥${data.price} → ${data.points} 积分`;
      }
      if (statusEl) statusEl.textContent = "等待扫码支付…";
      if (hint) {
        hint.textContent = data.is_auto
          ? "请按二维码提示手动输入金额"
          : `订单 ${data.pay_id || ""}`;
      }
      if (dialog) window.OverlayAnim?.reveal(dialog);
      await startPayPoll(data.pay_id);
    } catch (err) {
      rechargeState.paying = false;
      document.querySelectorAll(".points-recharge-channel").forEach((b) => {
        b.disabled = false;
      });
      window.AppDialog?.alert?.(err.message || "支付失败") || alert(err.message || "支付失败");
    }
  }

  async function cancelCurrentPayOrder() {
    const payId = rechargeState.payId;
    if (payId) {
      try {
        await fetch("/api/pay/close", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...window.CanvasAuth?.headers?.(),
          },
          body: JSON.stringify({ pay_id: payId }),
        });
      } catch {
        /* ignore */
      }
    }
    closePointsPayDialog();
  }

  function renderPointsHistoryRows(entries, { append = false } = {}) {
    const list = document.getElementById("points-history-list");
    if (!list) return;
    if (!append) list.innerHTML = "";
    if (!entries.length && !append) {
      list.innerHTML = `<div class="points-history-empty">暂无积分记录</div>`;
      return;
    }
    // Newest-first list: older row is at i+1. Infer reserve delta when signed_amount was clamped to 0.
    const resolved = entries.map((row, i) => {
      let signed = parsePointsValue(row.signed_amount);
      const reason = String(row.reason || "");
      if (
        signed === 0 &&
        (reason === "reserve" || row.title === "生成消耗")
      ) {
        const older = entries[i + 1];
        if (older) {
          const bal = parsePointsValue(row.balance_after);
          const prev = parsePointsValue(older.balance_after);
          const inferred = bal - prev;
          if (Number.isFinite(inferred) && inferred !== 0) signed = inferred;
        }
      }
      return { row, signed };
    });
    const html = resolved
      .map(({ row, signed }) => {
        const amountCls = signed > 0 ? "is-earn" : signed < 0 ? "is-spend" : "";
        const tags = Array.isArray(row.tags)
          ? row.tags
              .filter(Boolean)
              .map((t) => `<span class="points-history-tag">${escapePointsHtml(t)}</span>`)
              .join("")
          : "";
        return `<article class="points-history-row" role="listitem">
          <div class="points-history-row-main">
            <div class="points-history-row-title">${escapePointsHtml(row.title || "积分变动")}</div>
            <div class="points-history-row-meta">
              <time>${escapePointsHtml(formatPointsHistoryTime(row.at))}</time>
              ${tags ? `<span class="points-history-tags">${tags}</span>` : ""}
            </div>
          </div>
          <div class="points-history-row-side">
            <span class="points-history-amount ${amountCls}">${escapePointsHtml(formatSignedPoints(signed))}</span>
            <span class="points-history-bal">余额 ${escapePointsHtml(formatPointsDisplay(row.balance_after))}</span>
          </div>
        </article>`;
      })
      .join("");
    list.insertAdjacentHTML("beforeend", html);
  }

  async function fetchPointsHistory({ append = false } = {}) {
    if (pointsHistoryState.loading || !isLoggedIn()) return;
    pointsHistoryState.loading = true;
    const moreBtn = document.getElementById("btn-points-history-more");
    const countEl = document.getElementById("points-history-count");
    const balEl = document.getElementById("points-history-balance");
    try {
      const offset = append ? pointsHistoryState.offset : 0;
      const q = new URLSearchParams({
        category: pointsHistoryState.category,
        limit: String(pointsHistoryState.limit),
        offset: String(offset),
      });
      const res = await fetch(`/api/points/history?${q}`, {
        headers: { ...window.CanvasAuth?.headers?.() },
      });
      if (!res.ok) throw new Error("加载失败");
      const data = await res.json();
      const entries = Array.isArray(data.entries) ? data.entries : [];
      pointsHistoryState.total = Number(data.total) || 0;
      pointsHistoryState.offset = offset + entries.length;
      if (balEl) balEl.textContent = formatPointsDisplay(data.balance ?? window.__canvasUser?.points);
      if (countEl) countEl.textContent = `共 ${pointsHistoryState.total} 条`;
      renderPointsHistoryRows(entries, { append });
      const hasMore = pointsHistoryState.offset < pointsHistoryState.total;
      moreBtn?.classList.toggle("hidden", !hasMore);
    } catch (err) {
      if (!append) {
        const list = document.getElementById("points-history-list");
        if (list) list.innerHTML = `<div class="points-history-empty">${escapePointsHtml(err.message || "加载失败")}</div>`;
      }
    } finally {
      pointsHistoryState.loading = false;
    }
  }

  async function openPointsHistoryDialog(category = "all") {
    if (window.AppConfig?.points_enabled === false) return;
    if (!isLoggedIn()) {
      try {
        await window.CanvasAuth.ensureLogin();
      } catch {
        return;
      }
    }
    closeAllAccountPops();
    pointsHistoryState.category = category || "all";
    pointsHistoryState.offset = 0;
    document.querySelectorAll("#points-history-tabs .points-history-tab").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.pointsCat === pointsHistoryState.category);
    });
    try {
      const res = await fetch("/api/pay/config");
      if (res.ok) {
        const payCfg = await res.json();
        window.AppConfig = { ...(window.AppConfig || {}), ...payCfg };
      }
    } catch {
      /* ignore */
    }
    syncRechargePanel();
    const dialog = document.getElementById("points-history-dialog");
    if (!dialog) return;
    window.OverlayAnim?.reveal(dialog);
    await fetchPointsHistory({ append: false });
  }

  function showUpscaleLinkDialog(link) {
    const dialog = document.getElementById("upscale-link-dialog");
    const field = document.getElementById("upscale-link-field");
    if (!dialog || !field) return;
    field.value = link;
    window.OverlayAnim?.reveal(dialog);
    requestAnimationFrame(() => {
      field.focus();
      field.select();
    });
  }

  async function openUpscaleLinkDialog() {
    try {
      const res = await nativeFetch("/api/upscale/my-link", { headers: { ...window.CanvasAuth.headers() } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "获取链接失败");
      }
      const data = await res.json();
      const link = String(data.link || "").trim();
      if (!link) throw new Error("当前未启用每用户超分");
      showUpscaleLinkDialog(link);
    } catch (err) {
      const msg = err?.message || "获取链接失败";
      if (typeof showCanvasToast === "function") showCanvasToast(msg);
      else alert(msg);
    }
  }

  async function tryCopyUpscaleLinkField() {
    const field = document.getElementById("upscale-link-field");
    if (!field) return;
    const link = String(field.value || "").trim();
    if (!link) return;
    field.focus();
    field.select();
    try {
      await navigator.clipboard.writeText(link);
      if (typeof showCanvasToast === "function") showCanvasToast("已复制到剪贴板");
      else alert("已复制到剪贴板");
      return;
    } catch {}
    try {
      const ok = document.execCommand("copy");
      if (ok) {
        if (typeof showCanvasToast === "function") showCanvasToast("已复制到剪贴板");
        else alert("已复制到剪贴板");
        return;
      }
    } catch {}
    if (typeof showCanvasToast === "function") showCanvasToast("请手动全选复制");
    else alert("请手动全选复制");
  }

  function adminPanelUrl() {
    const port = Number(window.AppConfig?.admin_port) || 8090;
    return `${location.protocol}//${location.hostname}:${port}/#settings`;
  }

  let _loginWait = null;

  function revealAppAfterLogin() {
    hideLoginDialog();
    if (window.isAppCanvasPage) return;
    if (window.isAppWorkbenchPage) {
      if (typeof syncWorkbenchShellMode === "function") syncWorkbenchShellMode(true);
      return;
    }
    if (typeof syncAppShellMode === "function") syncAppShellMode("workspace");
    else {
      document.documentElement.classList.add("route-workspace");
      const app = document.getElementById("app-root");
      app?.classList.add("is-workspace");
      document.getElementById("workspace-home")?.classList.remove("hidden");
    }
  }

  function showLoginDialog() {
    /* Local-operator build: no account login UI. */
    void window.CanvasAuth?.bootstrapSession?.();
  }

  function hideLoginDialog() {
    /* no-op */
  }

  function rejectLoginWait(err) {
    const reject = window.__canvasAuthReject;
    _loginWait = null;
    window.__canvasAuthResolve = null;
    window.__canvasAuthReject = null;
    reject?.(err || new Error("已取消登录"));
  }

  async function logout() {
    // Preserve the last debounced edit before the session token is cleared.
    const pendingStores = [
      typeof CanvasStore !== 'undefined' ? CanvasStore._persistence : null,
      typeof WorkbenchStore !== 'undefined' ? WorkbenchStore._persistence : null,
    ].filter(Boolean);
    for (const store of pendingStores) store.emergency();
    await Promise.race([
      Promise.all(pendingStores.map((store) => store.flush())),
      new Promise((resolve) => setTimeout(resolve, 2000)),
    ]);
    try {
      await nativeFetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
        body: "{}",
      });
    } catch {}
    setToken("");
    window.__canvasUser = null;
    closeAllAccountPops();
    updateUserUI(null);
    if (typeof CanvasStore !== "undefined") {
      CanvasStore.stopSync?.();
      CanvasStore.boards = [];
      CanvasStore.activeBoardId = null;
      try {
        localStorage.removeItem("doubao_canvas_v2");
        Object.keys(localStorage).forEach((k) => {
          if (k.startsWith("doubao_canvas_v3_")) localStorage.removeItem(k);
        });
      } catch {}
      await CanvasStore.loadFromServer?.();
    }
    if (typeof clearTaskPolling === "function") clearTaskPolling();
    showWorkspaceHome?.();
  }

  async function requestLogout() {
    let ok = false;
    ok = await AppDialog.confirm("确定退出当前账号？", {
      title: "退出登录",
      okText: "退出",
      danger: true,
    });
    if (ok) await logout();
  }

  window.CanvasAuth = {
    getToken,
    setToken,
    isLoggedIn,
    getUsername,
    getUserCacheKey,
    updateUserUI,
    updatePointsBadges,
    refreshPointsBalance,
    refreshPoolSummary,
    openPointsHistoryDialog,
    openPoolShareDialog,
    closeUserPop,
    closeInternalPop,
    closeAllAccountPops,
    logout,
    requestLogout,
    revealAppAfterLogin,
    showLoginDialog,
    hideLoginDialog,
    headers() {
      const t = getToken();
      return t ? { Authorization: `Bearer ${t}` } : {};
    },
    async bootstrapSession() {
      setToken(LOCAL_TOKEN);
      _authBootstrapping = true;
      try {
        const res = await nativeFetch("/api/auth/me", {
          credentials: "same-origin",
          headers: { ...this.headers() },
        });
        if (res.ok) {
          const data = await res.json();
          window.__canvasUser = data.user || { username: "本机", display_name: "本机", role: "admin" };
          updateUserUI(window.__canvasUser);
          if (typeof window.loadAppConfig === "function") {
            await window.loadAppConfig();
          }
          return window.__canvasUser;
        }
        window.__canvasUser = window.__canvasUser || { username: "本机", display_name: "本机", role: "admin" };
        updateUserUI(window.__canvasUser);
        return window.__canvasUser;
      } catch {
        window.__canvasUser = window.__canvasUser || { username: "本机", display_name: "本机", role: "admin" };
        updateUserUI(window.__canvasUser);
        return window.__canvasUser;
      } finally {
        _authBootstrapping = false;
      }
    },
    refreshUserMenu() {
      updateUserUI(window.__canvasUser || { username: "本机", display_name: "本机", role: "admin" });
    },
    async ensureLogin() {
      return this.bootstrapSession();
    },
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function patchedFetch(input, init) {
    const url = typeof input === "string" ? input : input?.url || "";
    const path = url.startsWith("http") ? new URL(url).pathname : url;
    if (path.startsWith("/api/") && !path.startsWith("/api/auth/") && !path.startsWith("/api/app-config")) {
      init = { ...(init || {}) };
      const headers = new Headers(init.headers || {});
      const t = getToken();
      if (t) headers.set("Authorization", `Bearer ${t}`);
      init.headers = headers;
    }
    return nativeFetch(input, init).then((res) => {
      if (
        res.status === 401 &&
        path.startsWith("/api/") &&
        !path.startsWith("/api/auth/") &&
        !_authBootstrapping
      ) {
        // Local-operator build: ignore 401 — never clear boards or force login.
        setToken(LOCAL_TOKEN);
        if (!window.__canvasUser) {
          window.__canvasUser = { username: "本机", display_name: "本机", role: "admin" };
          updateUserUI(window.__canvasUser);
        }
      }
      return res;
    });
  };

  document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.getElementById("login-gate-dialog");
    const form = document.getElementById("login-gate-form");
    const errEl = document.getElementById("login-gate-err");

    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      errEl?.classList.add("hidden");
      try {
        const res = await nativeFetch("/api/auth/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: document.getElementById("login-gate-user")?.value?.trim(),
            password: document.getElementById("login-gate-pass")?.value,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || "登录失败");
        }
        const data = await res.json();
        setToken(data.token);
        window.__canvasUser = data.user;
        updateUserUI(data.user);
        if (typeof window.loadAppConfig === "function") {
          await window.loadAppConfig();
        }
        if (typeof CanvasStore !== "undefined") {
          await CanvasStore.loadFromServer?.();
        }
        if (typeof reloadWorkbenchHome === "function") {
          await reloadWorkbenchHome();
        } else {
          WorkbenchHome?.render?.();
        }
        revealAppAfterLogin();
        if (typeof WorkspaceHome !== "undefined") WorkspaceHome.render?.();
        window.__canvasAuthResolve?.(data.user);
        // Non-blocking: account/task bootstrap must not freeze the workspace UI.
        if (typeof window.bootstrapAuthenticatedApp === "function") {
          void window.bootstrapAuthenticatedApp().catch((err) => {
            console.warn("bootstrapAuthenticatedApp:", err);
          });
        }
        void refreshPoolSummary();
      } catch (err) {
        if (errEl) {
          errEl.textContent = err.message || "登录失败";
          errEl.classList.remove("hidden");
        }
        if (err.message !== "已取消登录") {
          window.__canvasAuthReject?.(err);
          window.__canvasAuthReject = null;
        }
      }
    });

    document.getElementById("btn-close-login-gate")?.addEventListener("click", () => {
      hideLoginDialog();
      rejectLoginWait(new Error("已取消登录"));
    });
    dialog?.addEventListener("cancel", (e) => {
      e.preventDefault();
      hideLoginDialog();
      rejectLoginWait(new Error("已取消登录"));
    });

    document.getElementById("btn-workspace-user")?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleUserPop(e.currentTarget);
    });

    document.getElementById("btn-canvas-user")?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleUserPop(e.currentTarget);
    });

    document.getElementById("btn-workbench-user")?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleUserPop(e.currentTarget);
    });

    ["btn-workspace-internal", "btn-canvas-internal", "btn-workbench-internal"].forEach((id) => {
      document.getElementById(id)?.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleInternalPop(e.currentTarget);
      });
    });

    async function authFetch(url, opts = {}) {
      const headers = { ...(opts.headers || {}), ...window.CanvasAuth.headers() };
      return fetch(url, { ...opts, headers, credentials: "same-origin" });
    }

    function closeEditProfileDialog() {
      const dlg = document.getElementById("modal-edit-profile");
      if (!dlg) return;
      if (window.OverlayAnim?.dismiss) window.OverlayAnim.dismiss(dlg);
      else {
        try {
          dlg.close();
        } catch {}
      }
    }

    function openEditProfileDialog() {
      const dlg = document.getElementById("modal-edit-profile");
      if (!dlg) return;
      const user = window.__canvasUser;
      const nameInput = document.getElementById("input-profile-display-name");
      if (nameInput) {
        nameInput.value = String(user?.display_name || user?.username || "").trim();
      }
      applyAvatarFace(
        document.getElementById("profile-edit-avatar-letter"),
        document.getElementById("profile-edit-avatar-img"),
        user
      );
      if (window.OverlayAnim?.reveal) window.OverlayAnim.reveal(dlg);
      else {
        try {
          dlg.showModal();
        } catch {}
      }
      requestAnimationFrame(() => {
        nameInput?.focus();
        nameInput?.select?.();
      });
    }

    async function copyUserId() {
      const uid = String(
        document.getElementById("workspace-user-id-row")?.dataset.userId ||
          window.__canvasUser?.id ||
          window.__canvasUser?.user_id ||
          ""
      ).trim();
      if (!uid) return;
      try {
        await navigator.clipboard.writeText(uid);
        if (typeof showCanvasToast === "function") showCanvasToast("已复制用户 ID");
        else await AppDialog.alert("已复制用户 ID");
        return;
      } catch {}
      try {
        const ta = document.createElement("textarea");
        ta.value = uid;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        if (typeof showCanvasToast === "function") showCanvasToast("已复制用户 ID");
      } catch {
        await AppDialog.alert(uid, { title: "用户 ID" });
      }
    }

    document.getElementById("btn-workspace-copy-id")?.addEventListener("click", (e) => {
      e.stopPropagation();
      void copyUserId();
    });

    document.getElementById("btn-workspace-edit-profile")?.addEventListener("click", (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      openEditProfileDialog();
    });

    document.getElementById("btn-close-edit-profile")?.addEventListener("click", () => closeEditProfileDialog());
    document.getElementById("btn-cancel-edit-profile")?.addEventListener("click", () => closeEditProfileDialog());
    document.getElementById("modal-edit-profile")?.addEventListener("cancel", (e) => {
      e.preventDefault();
      closeEditProfileDialog();
    });
    document.getElementById("modal-edit-profile")?.addEventListener("click", (e) => {
      if (e.target?.id === "modal-edit-profile") closeEditProfileDialog();
    });

    document.getElementById("btn-profile-pick-avatar")?.addEventListener("click", (e) => {
      e.preventDefault();
      document.getElementById("input-workspace-avatar")?.click();
    });
    document.getElementById("btn-profile-pick-avatar-text")?.addEventListener("click", (e) => {
      e.preventDefault();
      document.getElementById("input-workspace-avatar")?.click();
    });

    document.getElementById("form-edit-profile")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const nameInput = document.getElementById("input-profile-display-name");
      const name = String(nameInput?.value || "").trim().slice(0, 40);
      if (!name) {
        await AppDialog.alert("昵称不能为空", { title: "编辑资料" });
        nameInput?.focus();
        return;
      }
      const saveBtn = document.getElementById("btn-save-edit-profile");
      if (saveBtn) saveBtn.disabled = true;
      try {
        const res = await authFetch("/api/auth/profile", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: name }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.message || "保存失败");
        window.__canvasUser = data.user || { ...window.__canvasUser, display_name: name };
        updateUserUI(window.__canvasUser);
        closeEditProfileDialog();
        if (typeof showCanvasToast === "function") showCanvasToast("资料已更新");
      } catch (err) {
        await AppDialog.alert(String(err?.message || err || "保存失败"), { title: "编辑资料" });
      } finally {
        if (saveBtn) saveBtn.disabled = false;
      }
    });

    document.getElementById("input-workspace-avatar")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      if (!String(file.type || "").startsWith("image/")) {
        await AppDialog.alert("请选择 JPG / PNG 图片", { title: "更换头像" });
        return;
      }
      if (file.size > 6 * 1024 * 1024) {
        await AppDialog.alert("头像需小于 6MB", { title: "更换头像" });
        return;
      }
      closeAllAccountPops();
      let blob = null;
      try {
        blob = await AvatarCrop.open(file);
      } catch {
        blob = null;
      }
      if (!blob) return;
      const fd = new FormData();
      fd.append("file", blob, "avatar.jpg");
      try {
        const res = await authFetch("/api/auth/avatar", { method: "POST", body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.message || "上传失败");
        window.__canvasUser = data.user || window.__canvasUser;
        updateUserUI(window.__canvasUser, { bustAvatar: true });
        if (typeof showCanvasToast === "function") showCanvasToast("头像已更新");
      } catch (err) {
        await AppDialog.alert(String(err?.message || err || "上传失败"), { title: "更换头像" });
      }
    });

    document.getElementById("btn-workspace-login")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      try {
        await window.CanvasAuth.ensureLogin();
      } catch {}
    });

    document.getElementById("btn-workspace-logout")?.addEventListener("click", (e) => {
      e.stopPropagation();
      requestLogout();
    });

    document.getElementById("btn-workspace-myapi")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      try {
        await window.CanvasAuth.ensureLogin();
        if (typeof window.openMyApiSettings === "function") window.openMyApiSettings();
      } catch {}
    });

    document.getElementById("btn-workspace-conn-flow")?.addEventListener("click", (e) => {
      e.stopPropagation();
      if (typeof window.setConnFlowAnimEnabled === "function" && typeof window.isConnFlowAnimEnabled === "function") {
        window.setConnFlowAnimEnabled(!window.isConnFlowAnimEnabled());
        return;
      }
      // Fallback when canvas engine isn't on this page yet.
      try {
        let raw = localStorage.getItem("qingzi_canvas_prefs");
        if (!raw) raw = localStorage.getItem("doubao_canvas_prefs");
        const data = raw ? JSON.parse(raw) : {};
        const next = data.connFlowAnim === false;
        data.connFlowAnim = next;
        localStorage.setItem("qingzi_canvas_prefs", JSON.stringify(data));
        try {
          localStorage.removeItem("doubao_canvas_prefs");
        } catch {}
        const btn = e.currentTarget;
        btn.classList.toggle("is-on", next);
        btn.setAttribute("aria-checked", next ? "true" : "false");
        const state = btn.querySelector(".workspace-menu-switch-state");
        if (state) state.textContent = next ? "开" : "关";
        document.body.classList.toggle("conn-flow-off", !next);
      } catch {}
    });

    document.getElementById("btn-workspace-points")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      await openPointsHistoryDialog("all");
    });

    ["workspace-points-badge", "canvas-points-badge", "workbench-points-badge"].forEach((id) => {
      document.getElementById(id)?.addEventListener("click", async (e) => {
        e.stopPropagation();
        await openPointsHistoryDialog("all");
      });
    });

    document.getElementById("btn-close-points-history")?.addEventListener("click", (e) => {
      e.preventDefault();
      closePointsHistoryDialog();
    });

    document.getElementById("points-history-tabs")?.addEventListener("click", async (e) => {
      const tab = e.target.closest("[data-points-cat]");
      if (!tab) return;
      const cat = tab.dataset.pointsCat || "all";
      if (cat === pointsHistoryState.category) return;
      pointsHistoryState.category = cat;
      pointsHistoryState.offset = 0;
      document.querySelectorAll("#points-history-tabs .points-history-tab").forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.pointsCat === cat);
      });
      await fetchPointsHistory({ append: false });
    });

    document.getElementById("btn-points-history-more")?.addEventListener("click", async () => {
      await fetchPointsHistory({ append: true });
    });

    document.getElementById("points-history-dialog")?.addEventListener("click", (e) => {
      if (e.target?.id === "points-history-dialog") closePointsHistoryDialog();
    });

    document.getElementById("btn-recharge-wx")?.addEventListener("click", () => {
      void createRechargeOrder(1);
    });
    document.getElementById("btn-recharge-alipay")?.addEventListener("click", () => {
      void createRechargeOrder(2);
    });

    document.getElementById("btn-close-points-pay")?.addEventListener("click", (e) => {
      e.preventDefault();
      void cancelCurrentPayOrder();
    });
    document.getElementById("btn-points-pay-cancel")?.addEventListener("click", () => {
      void cancelCurrentPayOrder();
    });
    document.getElementById("btn-points-pay-done")?.addEventListener("click", () => {
      closePointsPayDialog();
      void refreshPointsBalance();
      pointsHistoryState.offset = 0;
      void fetchPointsHistory({ append: false });
    });
    document.getElementById("points-pay-dialog")?.addEventListener("click", (e) => {
      if (e.target?.id === "points-pay-dialog") void cancelCurrentPayOrder();
    });

    document.getElementById("btn-workspace-admin")?.addEventListener("click", (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      window.open(adminPanelUrl(), "_blank", "noopener,noreferrer");
    });

    document.getElementById("btn-workspace-add-doubao")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      try {
        if (typeof window.addDoubaoAccountFromAvatar === "function") {
          await window.addDoubaoAccountFromAvatar();
        }
        void refreshPoolSummary();
      } catch (err) {
        if (typeof AppDialog?.alert === "function") {
          await AppDialog.alert(err.message || "创建失败", { title: "添加账号" });
        } else {
          window.alert(err.message || "创建失败");
        }
      }
    });

    document.getElementById("btn-workspace-pool-share")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      try {
        await openPoolShareDialog();
      } catch (err) {
        if (typeof AppDialog?.alert === "function") {
          await AppDialog.alert(err.message || "打开失败", { title: "共享额度" });
        }
      }
    });

    document.getElementById("btn-cancel-pool-share")?.addEventListener("click", () => closePoolShareDialog());
    document.getElementById("modal-pool-share")?.addEventListener("cancel", (e) => {
      e.preventDefault();
      closePoolShareDialog();
    });
    document.getElementById("form-pool-share")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const input = document.getElementById("input-pool-share-username");
      const submitBtn = document.getElementById("btn-pool-share-submit");
      const username = String(input?.value || "").trim();
      setPoolShareFeedback("", "");
      if (!username) {
        setPoolShareFeedback("err", "请填写对方的画布登录用户名");
        input?.focus?.();
        return;
      }
      if (submitBtn) submitBtn.disabled = true;
      try {
        const t = getToken();
        const res = await fetch("/api/accounts/pool-share", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(t ? { Authorization: `Bearer ${t}` } : {}),
          },
          body: JSON.stringify({ username }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail =
            typeof data.detail === "string"
              ? data.detail
              : Array.isArray(data.detail)
                ? data.detail.map((d) => d?.msg || d).filter(Boolean).join("；")
                : "添加失败";
          throw new Error(detail);
        }
        applyPoolSummary(data);
        renderPoolShareLists(data);
        const name =
          data.share?.to_display_name || data.share?.to_username || username;
        setPoolShareFeedback("ok", `已共享给 ${name}`);
        if (input) input.value = "";
        input?.focus?.();
      } catch (err) {
        setPoolShareFeedback("err", err.message || "添加失败");
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });

    document.getElementById("btn-workspace-upscale-link")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeAllAccountPops();
      try {
        await window.CanvasAuth.ensureLogin();
        await openUpscaleLinkDialog();
      } catch {}
    });

    document.getElementById("btn-close-upscale-link")?.addEventListener("click", () => closeUpscaleLinkDialog());
    document.getElementById("btn-upscale-link-done")?.addEventListener("click", () => closeUpscaleLinkDialog());
    document.getElementById("btn-upscale-link-copy")?.addEventListener("click", () => {
      void tryCopyUpscaleLinkField();
    });
    document.getElementById("upscale-link-dialog")?.addEventListener("cancel", (e) => {
      e.preventDefault();
      closeUpscaleLinkDialog();
    });

    document.addEventListener("click", (e) => {
      if (
        !e.target.closest(
          "#workspace-user-wrap, #canvas-user-wrap, #workbench-user-wrap, #workspace-user-pop, #btn-workspace-internal, #btn-canvas-internal, #btn-workbench-internal, #workspace-internal-pop"
        )
      ) {
        closeAllAccountPops();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeAllAccountPops();
    });
  });
})();
