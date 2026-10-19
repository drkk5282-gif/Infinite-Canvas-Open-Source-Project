/** Themed confirm/alert/prompt dialogs — replaces native window.confirm / alert / prompt */
const AppDialog = {
  _resolve: null,
  _mode: "alert",
  _bound: false,

  _ensureBound() {
    if (this._bound) return;
    this._bound = true;
    const root = document.getElementById("app-dialog");
    root?.addEventListener("click", (e) => {
      const act = e.target.closest("[data-app-dialog]")?.dataset.appDialog;
      if (act === "ok") this._finish(true);
      else if (act === "cancel") this._finish(false);
    });
    root?.addEventListener("cancel", (e) => {
      e.preventDefault();
      this._finish(false);
    });
    const input = document.getElementById("app-dialog-input");
    input?.addEventListener("keydown", (e) => {
      if (!this.isOpen() || this._mode !== "prompt") return;
      if (e.key === "Enter") {
        e.preventDefault();
        e.stopPropagation();
        this._finish(true);
      }
    });
    window.addEventListener(
      "keydown",
      (e) => {
        if (!this.isOpen()) return;
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          this._finish(false);
        } else if (e.key === "Enter" && this._mode !== "prompt") {
          e.preventDefault();
          e.stopPropagation();
          this._finish(true);
        }
      },
      true
    );
  },

  isOpen() {
    const root = document.getElementById("app-dialog");
    return !!(root?.open);
  },

  _finish(ok) {
    const root = document.getElementById("app-dialog");
    if (!root?.open) return;
    const input = document.getElementById("app-dialog-input");
    const mode = this._mode;
    let value = ok;
    if (mode === "prompt") {
      value = ok ? String(input?.value || "") : null;
    }
    if (typeof dismissOverlay === "function") dismissOverlay(root);
    else {
      try {
        root.close();
      } catch {
        /* ignore */
      }
    }
    root.classList.remove("is-alert", "is-danger", "is-prompt");
    if (input) {
      input.value = "";
      input.classList.add("hidden");
    }
    const resolve = this._resolve;
    this._resolve = null;
    this._mode = "alert";
    resolve?.(value);
  },

  _open({ title, message, mode, okText, cancelText, danger, defaultValue, placeholder }) {
    this._ensureBound();
    return new Promise((resolve) => {
      if (this._resolve) this._finish(false);
      this._resolve = resolve;
      this._mode = mode || "alert";
      const root = document.getElementById("app-dialog");
      const titleEl = document.getElementById("app-dialog-title");
      const msgEl = document.getElementById("app-dialog-message");
      const input = document.getElementById("app-dialog-input");
      const okBtn = document.getElementById("app-dialog-ok");
      const cancelBtn = document.getElementById("app-dialog-cancel");
      if (!root) {
        if (mode === "prompt") resolve(null);
        else resolve(mode === "confirm" ? false : true);
        return;
      }
      root.classList.toggle("is-alert", mode === "alert");
      root.classList.toggle("is-danger", Boolean(danger));
      root.classList.toggle("is-prompt", mode === "prompt");
      if (titleEl) titleEl.textContent = title || (mode === "confirm" ? "确认" : mode === "prompt" ? "输入" : "提示");
      if (msgEl) {
        msgEl.textContent = message || "";
        msgEl.classList.toggle("hidden", !message && mode === "prompt");
      }
      if (input) {
        const showInput = mode === "prompt";
        input.classList.toggle("hidden", !showInput);
        if (showInput) {
          input.value = defaultValue != null ? String(defaultValue) : "";
          input.placeholder = placeholder || "";
          input.setAttribute("aria-label", title || "输入");
        }
      }
      if (okBtn) okBtn.textContent = okText || "确定";
      if (cancelBtn) cancelBtn.textContent = cancelText || "取消";
      if (!root.open) {
        if (typeof revealOverlay === "function") revealOverlay(root);
        else {
          try {
            root.showModal();
          } catch {
            /* ignore */
          }
        }
      }
      requestAnimationFrame(() => {
        if (mode === "prompt" && input) {
          input.focus();
          input.select?.();
        } else {
          okBtn?.focus();
        }
      });
    });
  },

  alert(message, opts = {}) {
    return this._open({
      title: opts.title || "提示",
      message,
      mode: "alert",
      okText: opts.okText || "知道了",
      danger: opts.danger,
    }).then(() => undefined);
  },

  confirm(message, opts = {}) {
    return this._open({
      title: opts.title || "确认",
      message,
      mode: "confirm",
      okText: opts.okText || "确定",
      cancelText: opts.cancelText || "取消",
      danger: Boolean(opts.danger),
    });
  },

  confirmDelete(message, opts = {}) {
    return this.confirm(message, {
      title: opts.title || "确认删除",
      okText: opts.okText || "删除",
      cancelText: opts.cancelText || "取消",
      danger: true,
      ...opts,
    });
  },

  /** @returns {Promise<string|null>} trimmed text, or null if cancelled */
  prompt(message, opts = {}) {
    return this._open({
      title: opts.title || "输入",
      message: message || "",
      mode: "prompt",
      okText: opts.okText || "确定",
      cancelText: opts.cancelText || "取消",
      defaultValue: opts.defaultValue ?? "",
      placeholder: opts.placeholder || "",
    }).then((v) => (v == null ? null : String(v)));
  },
};

window.AppDialog = AppDialog;
