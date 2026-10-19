/**
 * Global UI themes: default | black | white | pink | custom
 * Persisted in qingzi_canvas_prefs.theme (+ themeCustom).
 */
(function (global) {
  "use strict";

  const PREFS_KEY = "qingzi_canvas_prefs";
  const LEGACY_PREFS_KEY = "doubao_canvas_prefs";
  const THEME_IDS = ["default", "black", "white", "pink", "custom"];
  const CUSTOM_VARS = [
    { key: "--bg", label: "背景" },
    { key: "--bg-panel", label: "面板" },
    { key: "--bg-node", label: "节点" },
    { key: "--text", label: "文字" },
    { key: "--text-muted", label: "次要文字" },
    { key: "--accent", label: "强调色" },
    { key: "--border", label: "边框" },
  ];
  const CUSTOM_DEFAULTS = {
    "--bg": "#1a1020",
    "--bg-panel": "#2a1a30",
    "--bg-node": "#322038",
    "--text": "#f5eef8",
    "--text-muted": "#b09ab8",
    "--accent": "#e879f9",
    "--border": "#4a3554",
  };

  function readPrefs() {
    try {
      let raw = localStorage.getItem(PREFS_KEY);
      if (!raw) {
        raw = localStorage.getItem(LEGACY_PREFS_KEY);
        if (raw) {
          localStorage.setItem(PREFS_KEY, raw);
          localStorage.removeItem(LEGACY_PREFS_KEY);
        }
      }
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  function writePrefs(patch) {
    const data = { ...readPrefs(), ...patch };
    localStorage.setItem(PREFS_KEY, JSON.stringify(data));
    try {
      localStorage.removeItem(LEGACY_PREFS_KEY);
    } catch {
      /* ignore */
    }
    if (global.CanvasPrefs) {
      if (patch.theme != null) global.CanvasPrefs.theme = patch.theme;
      if (patch.themeCustom != null) global.CanvasPrefs.themeCustom = patch.themeCustom;
    }
    return data;
  }

  function normalizeTheme(id) {
    const t = String(id || "default").toLowerCase();
    return THEME_IDS.includes(t) ? t : "default";
  }

  function clearCustomInline() {
    const root = document.documentElement;
    CUSTOM_VARS.forEach(({ key }) => root.style.removeProperty(key));
    [
      "--bg-elevated",
      "--surface",
      "--surface-glass",
      "--surface-glass-soft",
      "--surface-inset",
      "--border-inset",
      "--primary",
      "--dot",
      "--canvas-grid-dot-color",
      "--hover-fill",
      "--hover-fill-strong",
      "--scrollbar-thumb",
      "--scrollbar-thumb-hover",
    ].forEach((k) => root.style.removeProperty(k));
  }

  function hexToRgb(hex) {
    const h = String(hex || "").replace("#", "").trim();
    if (h.length === 3) {
      return {
        r: parseInt(h[0] + h[0], 16),
        g: parseInt(h[1] + h[1], 16),
        b: parseInt(h[2] + h[2], 16),
      };
    }
    if (h.length !== 6) return null;
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    };
  }

  function clamp01(n) {
    return Math.min(1, Math.max(0, n));
  }

  function hslToHex(h, s, l) {
    const hh = ((h % 360) + 360) % 360;
    const ss = clamp01(s);
    const ll = clamp01(l);
    const c = (1 - Math.abs(2 * ll - 1)) * ss;
    const x = c * (1 - Math.abs(((hh / 60) % 2) - 1));
    const m = ll - c / 2;
    let r = 0;
    let g = 0;
    let b = 0;
    if (hh < 60) [r, g, b] = [c, x, 0];
    else if (hh < 120) [r, g, b] = [x, c, 0];
    else if (hh < 180) [r, g, b] = [0, c, x];
    else if (hh < 240) [r, g, b] = [0, x, c];
    else if (hh < 300) [r, g, b] = [x, 0, c];
    else [r, g, b] = [c, 0, x];
    const to = (v) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
    return `#${to(r)}${to(g)}${to(b)}`;
  }

  /** Coherent random palette — dark only (avoid washed-out white backgrounds). */
  function randomCustomPalette() {
    const hue = Math.floor(Math.random() * 360);
    const accentHue = (hue + 24 + Math.floor(Math.random() * 56)) % 360;
    const sat = 0.32 + Math.random() * 0.34;
    // Keep surfaces clearly dark: bg ≈ 6–14%, panel ≈ 12–20%, node ≈ 16–24%.
    const bgL = 0.06 + Math.random() * 0.08;
    const panelL = Math.min(0.22, bgL + 0.06 + Math.random() * 0.05);
    const nodeL = Math.min(0.26, panelL + 0.03 + Math.random() * 0.04);
    return {
      "--bg": hslToHex(hue, sat * 0.85, bgL),
      "--bg-panel": hslToHex(hue, sat, panelL),
      "--bg-node": hslToHex(hue, sat * 0.92, nodeL),
      "--text": hslToHex(hue, 0.12 + Math.random() * 0.12, 0.92 + Math.random() * 0.04),
      "--text-muted": hslToHex(hue, 0.18 + Math.random() * 0.12, 0.62 + Math.random() * 0.1),
      "--accent": hslToHex(accentHue, 0.58 + Math.random() * 0.28, 0.52 + Math.random() * 0.16),
      "--border": hslToHex(hue, sat * 0.75, Math.min(0.36, nodeL + 0.08)),
    };
  }

  function applyCustomTokens(custom) {
    const root = document.documentElement;
    const src = { ...CUSTOM_DEFAULTS, ...(custom || {}) };
    CUSTOM_VARS.forEach(({ key }) => {
      if (src[key]) root.style.setProperty(key, src[key]);
    });
    const panel = src["--bg-panel"] || CUSTOM_DEFAULTS["--bg-panel"];
    const text = src["--text"] || CUSTOM_DEFAULTS["--text"];
    const bg = src["--bg"] || CUSTOM_DEFAULTS["--bg"];
    const accent = src["--accent"] || CUSTOM_DEFAULTS["--accent"];
    root.style.setProperty("--bg-elevated", src["--bg-node"] || panel);
    root.style.setProperty("--surface", panel);
    root.style.setProperty("--surface-inset", src["--bg-node"] || panel);
    root.style.setProperty("--primary", text);
    const rgb = hexToRgb(panel);
    if (rgb) {
      root.style.setProperty("--surface-glass", `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.94)`);
      root.style.setProperty("--surface-glass-soft", `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.76)`);
    }
    const ar = hexToRgb(accent);
    if (ar) {
      root.style.setProperty("--border-inset", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.14)`);
      root.style.setProperty("--hover-fill", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.08)`);
      root.style.setProperty("--hover-fill-strong", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.14)`);
      root.style.setProperty("--scrollbar-thumb", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.3)`);
      root.style.setProperty("--scrollbar-thumb-hover", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.45)`);
      root.style.setProperty("--canvas-grid-dot-color", `rgba(${ar.r}, ${ar.g}, ${ar.b}, 0.18)`);
    }
    const br = hexToRgb(bg);
    if (br) {
      const light = (br.r + br.g + br.b) / 3 > 140;
      root.style.setProperty("color-scheme", light ? "light" : "dark");
      root.style.setProperty("--dot", light ? "#d0d0d6" : "#333038");
    }
  }

  function apply(themeId, custom, { persist = false } = {}) {
    const id = normalizeTheme(themeId);
    const root = document.documentElement;
    if (id === "default") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", id);

    if (id === "custom") {
      applyCustomTokens(custom || readPrefs().themeCustom || CUSTOM_DEFAULTS);
    } else {
      clearCustomInline();
    }

    const mode =
      id === "white"
        ? "light"
        : id === "custom" &&
          (() => {
            const bg = root.style.getPropertyValue("--bg") || CUSTOM_DEFAULTS["--bg"];
            const rgb = hexToRgb(bg);
            return rgb && (rgb.r + rgb.g + rgb.b) / 3 > 140;
          })()
        ? "light"
        : "dark";
    root.setAttribute("data-theme-mode", mode);
    root.style.setProperty("color-scheme", mode);

    if (persist) {
      const patch = { theme: id };
      if (id === "custom" && custom) patch.themeCustom = { ...CUSTOM_DEFAULTS, ...custom };
      writePrefs(patch);
    }
    syncMenu();
    try {
      global.dispatchEvent(new CustomEvent("app-theme-change", { detail: { theme: id } }));
    } catch {
      /* ignore */
    }
    return id;
  }

  function current() {
    return normalizeTheme(readPrefs().theme || document.documentElement.getAttribute("data-theme") || "default");
  }

  function bootFromStorage() {
    const data = readPrefs();
    apply(data.theme || "default", data.themeCustom, { persist: false });
  }

  function syncMenu() {
    const id = current();
    document.querySelectorAll("[data-app-theme]").forEach((btn) => {
      const on = btn.getAttribute("data-app-theme") === id;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function ensureCustomDialog() {
    let dlg = document.getElementById("modal-theme-custom");
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.id = "modal-theme-custom";
    dlg.className = "theme-custom-dialog";
    dlg.innerHTML = `
      <form method="dialog" class="theme-custom-card" id="theme-custom-form">
        <div class="theme-custom-head">
          <h3>自定义主题</h3>
          <button type="button" class="theme-custom-close" id="btn-theme-custom-close" aria-label="关闭">×</button>
        </div>
        <p class="theme-custom-hint">调整后立即预览，点保存写入本机。</p>
        <div class="theme-custom-grid" id="theme-custom-grid"></div>
        <div class="theme-custom-actions">
          <button type="button" class="theme-custom-btn ghost" id="btn-theme-custom-random">随机配色</button>
          <button type="button" class="theme-custom-btn ghost" id="btn-theme-custom-reset">恢复预设</button>
          <button type="submit" class="theme-custom-btn primary" value="save">保存</button>
        </div>
      </form>
    `;
    document.body.appendChild(dlg);
    const grid = dlg.querySelector("#theme-custom-grid");
    CUSTOM_VARS.forEach(({ key, label }) => {
      const row = document.createElement("label");
      row.className = "theme-custom-row";
      row.innerHTML = `
        <span>${label}</span>
        <input type="color" data-theme-var="${key}" />
        <input type="text" class="theme-custom-hex" data-theme-hex="${key}" maxlength="7" spellcheck="false" />
      `;
      grid.appendChild(row);
    });

    function readFormCustom() {
      const out = {};
      dlg.querySelectorAll("input[data-theme-var]").forEach((inp) => {
        out[inp.getAttribute("data-theme-var")] = inp.value;
      });
      return out;
    }

    function fillForm(custom) {
      const src = { ...CUSTOM_DEFAULTS, ...(custom || {}) };
      dlg.querySelectorAll("input[data-theme-var]").forEach((inp) => {
        const key = inp.getAttribute("data-theme-var");
        const val = src[key] || "#000000";
        inp.value = /^#[0-9a-fA-F]{6}$/.test(val) ? val : "#888888";
        const hex = dlg.querySelector(`input[data-theme-hex="${key}"]`);
        if (hex) hex.value = inp.value;
      });
    }

    dlg.querySelector("#theme-custom-grid")?.addEventListener("input", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLInputElement)) return;
      if (t.dataset.themeVar) {
        const hex = dlg.querySelector(`input[data-theme-hex="${t.dataset.themeVar}"]`);
        if (hex) hex.value = t.value;
        apply("custom", readFormCustom(), { persist: false });
      } else if (t.dataset.themeHex) {
        let v = t.value.trim();
        if (!v.startsWith("#")) v = `#${v}`;
        if (/^#[0-9a-fA-F]{6}$/.test(v)) {
          const color = dlg.querySelector(`input[data-theme-var="${t.dataset.themeHex}"]`);
          if (color) color.value = v;
          apply("custom", readFormCustom(), { persist: false });
        }
      }
    });

    dlg.querySelector("#btn-theme-custom-close")?.addEventListener("click", () => {
      bootFromStorage();
      closeCustomEditor(dlg);
    });
    dlg.querySelector("#btn-theme-custom-random")?.addEventListener("click", () => {
      const next = randomCustomPalette();
      fillForm(next);
      apply("custom", next, { persist: false });
    });
    dlg.querySelector("#btn-theme-custom-reset")?.addEventListener("click", () => {
      fillForm(CUSTOM_DEFAULTS);
      apply("custom", CUSTOM_DEFAULTS, { persist: false });
    });
    dlg.querySelector("#theme-custom-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      const custom = readFormCustom();
      apply("custom", custom, { persist: true });
      closeCustomEditor(dlg);
      if (typeof global.showCanvasToast === "function") global.showCanvasToast("已保存自定义主题");
    });
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      bootFromStorage();
      closeCustomEditor(dlg);
    });

    dlg._fillForm = fillForm;
    return dlg;
  }

  function closeCustomEditor(dlg) {
    const el = dlg || document.getElementById("modal-theme-custom");
    if (!el) return;
    if (typeof global.OverlayAnim?.dismiss === "function") global.OverlayAnim.dismiss(el);
    else {
      try {
        el.close();
      } catch {
        /* ignore */
      }
    }
  }

  function openCustomEditor() {
    const dlg = ensureCustomDialog();
    const saved = readPrefs().themeCustom || CUSTOM_DEFAULTS;
    dlg._fillForm(saved);
    apply("custom", saved, { persist: false });
    if (typeof global.CanvasAuth?.closeAllAccountPops === "function") {
      try {
        global.CanvasAuth.closeAllAccountPops();
      } catch {
        /* ignore */
      }
    }
    if (typeof global.OverlayAnim?.reveal === "function") global.OverlayAnim.reveal(dlg);
    else if (typeof dlg.showModal === "function") {
      dlg.showModal();
      dlg.classList.add("is-overlay-open");
    } else {
      dlg.setAttribute("open", "");
      dlg.classList.add("is-overlay-open");
    }
  }

  function setTheme(themeId) {
    const id = normalizeTheme(themeId);
    if (id === "custom") {
      openCustomEditor();
      return id;
    }
    apply(id, null, { persist: true });
    if (typeof global.showCanvasToast === "function") {
      const labels = { default: "默认", black: "黑色", white: "白色", pink: "粉色" };
      global.showCanvasToast(`已切换为${labels[id] || id}主题`);
    }
    return id;
  }

  function bindMenu() {
    document.getElementById("workspace-theme-swatches")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-app-theme]");
      if (!btn) return;
      e.stopPropagation();
      setTheme(btn.getAttribute("data-app-theme"));
    });
    syncMenu();
  }

  global.AppTheme = {
    THEME_IDS,
    CUSTOM_DEFAULTS,
    bootFromStorage,
    apply,
    setTheme,
    current,
    syncMenu,
    openCustomEditor,
    bindMenu,
    readPrefs,
  };

  try {
    bootFromStorage();
  } catch {
    /* ignore */
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindMenu);
  } else {
    bindMenu();
  }
})(window);
