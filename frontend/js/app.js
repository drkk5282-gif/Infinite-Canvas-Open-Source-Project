const API = "";
let pollTimer = null;
let pollGeneration = 0;
let qrPollTimer = null;
let qrCountdownTimer = null;
const QR_LOGIN_TIMEOUT_MS = 60_000;

const $ = (sel) => document.querySelector(sel);

function updateVersionBadge() {
  const el = document.getElementById("app-version-badge");
  if (!el) return;
  const onWorkspace = document.getElementById("app-root")?.classList.contains("is-workspace");
  const cfg = window.AppConfig || {};
  const label = cfg.version_label || (cfg.build ? `v${cfg.version || "?"} · build ${cfg.build}` : "");
  if (!label || !onWorkspace) {
    el.classList.add("hidden");
    if (!label) el.textContent = "";
    return;
  }
  el.textContent = /禁止商用/.test(label) ? label : `${label} · 禁止商用`;
  el.classList.remove("hidden");
}
window.updateVersionBadge = updateVersionBadge;

function syncUpscaleUiFromConfig() {
  const enabled = window.AppConfig?.upscale_enabled !== false;
  document.documentElement.classList.toggle("upscale-disabled", !enabled);
  window.CanvasAuth?.refreshUserMenu?.();
  if (!enabled) {
    document.getElementById("video-upscale-modal")?.classList.add("hidden");
    document.getElementById("video-upscale-modal")?.setAttribute("aria-hidden", "true");
    // Belt-and-suspenders: hide any already-rendered toolbar entries immediately
    document.querySelectorAll(".media-tool-upscale").forEach((el) => {
      el.style.display = "none";
    });
  } else {
    document.querySelectorAll(".media-tool-upscale").forEach((el) => {
      if (el.style.display === "none") el.style.display = "";
    });
  }
  const prev = window.__upscaleUiEnabled;
  window.__upscaleUiEnabled = enabled;
  // Rebuild when flag flips, or when disabled but old toolbar DOM is still present
  const staleUpscaleDom = !enabled && !!document.querySelector(".media-tool-upscale");
  if (
    typeof renderNodes === "function" &&
    ((prev !== undefined && prev !== enabled) || staleUpscaleDom)
  ) {
    renderNodes({ force: true });
  }
}
window.syncUpscaleUiFromConfig = syncUpscaleUiFromConfig;

function isPointsEnabled() {
  return false;
}
window.isPointsEnabled = isPointsEnabled;

function syncPointsUiFromConfig() {
  const enabled = isPointsEnabled();
  document.documentElement.classList.toggle("points-disabled", !enabled);
  window.CanvasAuth?.refreshUserMenu?.();
  if (typeof renderNodes === "function" && window.__pointsUiEnabled !== undefined && window.__pointsUiEnabled !== enabled) {
    renderNodes({ force: true });
  }
  window.__pointsUiEnabled = enabled;
}
window.syncPointsUiFromConfig = syncPointsUiFromConfig;

function defaultAppConfigFallback() {
  return {
    disable_browser_channels: true,
    disable_doubao: true,
    browser_channels_allowed: false,
    channels: ["myapi"],
    upscale_enabled: true,
    upscale_pool_mode: "shared",
    allow_frontend_custom_models: true,
    points_enabled: false,
  };
}

const backgroundRefreshJobs = new Map();
function runBackgroundRefresh(key, run) {
  const token = window.CanvasAuth?.getToken?.() || "";
  const jobKey = JSON.stringify([key, token]);
  if (backgroundRefreshJobs.has(jobKey)) return backgroundRefreshJobs.get(jobKey);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  const job = Promise.resolve().then(() => run(controller.signal)).finally(() => {
    clearTimeout(timeout);
    if (backgroundRefreshJobs.get(jobKey) === job) backgroundRefreshJobs.delete(jobKey);
  });
  backgroundRefreshJobs.set(jobKey, job);
  return job;
}

function loadAppConfig() {
  return runBackgroundRefresh("app-config", loadAppConfigImpl);
}
async function loadAppConfigImpl(signal) {
  try {
    const res = await fetch("/api/app-config", { credentials: "same-origin", signal });
    if (res.ok) {
      window.AppConfig = await res.json();
      window.ServerWatch?.markReachable?.();
    } else if (!window.AppConfig) {
      window.AppConfig = defaultAppConfigFallback();
    }
  } catch {
    if (!window.AppConfig) window.AppConfig = defaultAppConfigFallback();
  }
  // Open-source: never expose Doubao / Dola / GPT browser simulation.
  window.AppConfig = window.AppConfig || defaultAppConfigFallback();
  window.AppConfig.disable_browser_channels = true;
  window.AppConfig.disable_doubao = true;
  window.AppConfig.browser_channels_allowed = false;
  window.AppConfig.has_browser_channels = false;
  const keepCh = new Set(["myapi", "bendi", "volcengine"]);
  const channels = Array.isArray(window.AppConfig.channels) ? window.AppConfig.channels : [];
  window.AppConfig.channels = channels.filter((c) => keepCh.has(String(c)));
  if (!window.AppConfig.channels.includes("myapi")) window.AppConfig.channels.unshift("myapi");
  if (typeof refreshBendiModelsForMode === "function") {
    refreshBendiModelsForMode(window.AppConfig?.bendi_mode);
  }
  syncUpscaleUiFromConfig();
  syncPointsUiFromConfig();
  if (typeof syncMyApiApiStyleSelectOptions === "function") syncMyApiApiStyleSelectOptions();
  if (window.CanvasAuth?.updateUserUI && window.__canvasUser) {
    window.CanvasAuth.updateUserUI(window.__canvasUser);
  }
  document.querySelector(".settings-myapi-wrap")?.classList.toggle(
    "hidden",
    window.AppConfig?.allow_frontend_custom_models === false
  );
  document.getElementById("btn-workspace-bendi")?.classList.toggle(
    "hidden",
    !window.AppConfig?.bendi_allowed || !window.CanvasAuth?.isLoggedIn?.()
  );
  // 仅当「允许前台自定义模型」开关变化时刷新列表，避免 8s 轮询反复拉模型/写画布
  const allowCustomNow = window.AppConfig?.allow_frontend_custom_models !== false;
  if (window.__allowFrontendCustomModels !== allowCustomNow) {
    window.__allowFrontendCustomModels = allowCustomNow;
    if (typeof refreshMyApiModels === "function" && window.CanvasAuth?.isLoggedIn?.()) {
      void refreshMyApiModels().then(() => {
        if (typeof refreshMyApiNodeToolbars === "function") refreshMyApiNodeToolbars();
        if (typeof refreshWorkbenchMyApiToolbars === "function") refreshWorkbenchMyApiToolbars();
      });
    }
  }
  const noBrowser = true;
  if (noBrowser) {
    document.body.classList.add("api-only-pack");
    document.querySelectorAll(
      [
        ".settings-accounts-wrap",
        '.account-box[data-provider="doubao"]',
        '.account-box[data-provider="dola"]',
        "#modal-add-account",
        "#modal-qr-login",
        "#modal-browser-login",
      ].join(",")
    ).forEach((el) => el?.classList.add("hidden"));
    // Hide entire「浏览器」settings block (path / headless / proxy).
    document.querySelectorAll("#modal-settings .settings-section").forEach((sec) => {
      const title = sec.querySelector(".settings-section-title")?.textContent || "";
      if (/浏览器|账号管理/.test(title)) sec.classList.add("hidden");
    });
    const sub = document.querySelector(".settings-window-sub");
    if (sub) sub.textContent = "MyApi · 并发 · 数据";
    const threadHint = document.getElementById("thread-recommend-hint");
    if (threadHint) threadHint.textContent = "MyApi 并发上限";
    const threadBadge = document.getElementById("thread-badge");
    if (threadBadge) threadBadge.title = "MyApi 并发";
  } else {
    document.body.classList.remove("api-only-pack");
  }
  // Only migrate/remount when the canvas-only flag flips — the 8s config poll must NOT force-render.
  if (window.__canvasNoBrowser !== noBrowser) {
    const prev = window.__canvasNoBrowser;
    window.__canvasNoBrowser = noBrowser;
    if (noBrowser && typeof migrateDoubaoDisabledNodes === "function") migrateDoubaoDisabledNodes();
    if (typeof migrateDolaSNodes === "function") migrateDolaSNodes();
    if (typeof migrateGptChannelNodes === "function") migrateGptChannelNodes();
    // Skip remount on first paint (prev undefined); bootstrap/render already draws nodes.
    if (prev !== undefined && typeof renderNodes === "function") {
      renderNodes({ force: true });
    }
  }
  updateVersionBadge();
}
window.loadAppConfig = loadAppConfig;

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      headers: { "Content-Type": "application/json", ...opts.headers },
      ...opts,
    });
  } catch (err) {
    window.ServerWatch?.reportFailure?.();
    throw err;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    let msg = res.statusText;
    let code = null;
    if (typeof detail === "string") {
      msg = detail;
    } else if (Array.isArray(detail)) {
      msg = detail.map((d) => d.msg).join("; ");
    } else if (detail && typeof detail === "object") {
      msg = detail.message || detail.msg || msg;
      code = detail.code || null;
    }
    const e = new Error(msg || res.statusText);
    e.status = res.status;
    e.code = code;
    if (err.task_id) e.taskId = err.task_id;
    throw e;
  }
  return res.json();
}
window.api = api;

async function loadAccounts() {
  if (!window.CanvasAuth?.isLoggedIn?.()) return;
  const data = await api("/api/accounts");
  // Guard: concurrent boot / partial responses used to yield undefined → .filter crash on add-account.
  const accounts = Array.isArray(data?.accounts) ? data.accounts : [];
  window.__accounts = accounts;
  renderAccounts(accounts);
  const doubaoList = accounts.filter((a) => (a.provider || "doubao") === "doubao" && a.status === "online");
  const doubaoOnline = doubaoList.length;
  const doubaoSOnline = doubaoList.filter((a) => accountDoubaoTier(a) === "upgrade").length;
  const doubaoRegularOnline = doubaoOnline - doubaoSOnline;
  const badge = $("#online-badge");
  if (badge) {
    badge.textContent = `共 ${doubaoOnline} 个在线（Seed ${doubaoOnline}）`;
  }
  const bd = $("#online-badge-doubao");
  if (bd) {
    bd.textContent = doubaoOnline
      ? `在线 ${doubaoOnline} · S ${doubaoSOnline} · 普通 ${doubaoRegularOnline}`
      : "0 个在线";
  }
  const bdo = $("#online-badge-dola");
  if (bdo) {
    bdo.textContent = "0 个在线";
  }
  refreshNodeAccountSelects();
  await loadServerSettings();
  refreshThreadSettingsUI();
  updateThreadBadge();
}

function refreshNodeAccountSelects() {
  document.querySelectorAll(".channel-btn-label").forEach((label) => {
    const picker = label.closest(".channel-picker");
    const nodeId = picker?.dataset.node || label.closest("[data-node]")?.dataset.node;
    const node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
    if (!node) return;
    const channel = resolveNodeChannel(node);
    label.textContent = getChannelLabel(channel);
    picker?.querySelectorAll(".channel-option").forEach((btn) => {
      const on = btn.dataset.channel === channel;
      btn.classList.toggle("active", on);
      const check = btn.querySelector(".video-model-check");
      if (on && !check) {
        btn.insertAdjacentHTML("beforeend", '<span class="video-model-check">✓</span>');
      } else if (!on && check) {
        check.remove();
      }
    });
  });
  document.querySelectorAll("select.node-account").forEach((sel) => {
    const nodeId = sel.dataset.node;
    const node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
    const channel = resolveNodeChannel(node || { channel: "doubao", type: "image" });
    sel.innerHTML = getAccountOptionsHtml(channel, node?.type || "image");
    sel.value = channel;
  });
}

function statusLabel(s) {
  return { online: "在线", offline: "离线", logging_in: "登录中", error: "异常" }[s] || s;
}

function accountCoolingInfo(a) {
  const now = Date.now();
  const videoUntil = a.video_cooldown_until ? Date.parse(a.video_cooldown_until) : NaN;
  if (Number.isFinite(videoUntil) && videoUntil > now) {
    return { cooling: true, label: "等待次日恢复", kind: "video" };
  }
  const until = a.cooldown_until ? Date.parse(a.cooldown_until) : NaN;
  if (Number.isFinite(until) && until > now) {
    const mins = Math.max(1, Math.ceil((until - now) / 60000));
    return { cooling: true, label: `冷却中约 ${mins} 分钟`, kind: "rate" };
  }
  return { cooling: false, label: "", kind: null };
}

function accountCoolingLabel(a) {
  return accountCoolingInfo(a).label;
}

function accountDoubaoTier(account) {
  const label = String(account?.doubao_plan_label || "").trim();
  if (label) {
    if (label === "标准套餐" || label === "标准") return "upgrade";
    if (/(专业版|升级|旗舰|尊享|高级|\bMax\b|\bPRO\b|\bPro\b)/.test(label)) return "upgrade";
    if (label.includes("套餐") && label !== "无套餐" && label !== "免费套餐") return "upgrade";
  }
  return account?.doubao_tier === "upgrade" ? "upgrade" : "regular";
}

function doubaoTierLabel(tier) {
  return tier === "upgrade" ? "Seed S" : "Seed";
}

function accountPlanDisplay(account) {
  const tier = accountDoubaoTier(account);
  const label = String(account?.doubao_plan_label || "").trim();
  if (label === "未检测到" || label === "未检测") {
    return tier === "upgrade" ? "S · 未确认" : "普通 · 未检测";
  }
  if (tier === "upgrade") {
    if (!label) return "S";
    const short = label.length > 12 ? `${label.slice(0, 12)}…` : label;
    return `S · ${short}`;
  }
  if (label) {
    const short = label.length > 12 ? `${label.slice(0, 12)}…` : label;
    return `普通 · ${short}`;
  }
  return "普通 · 未检测";
}

/** Online DouBao/Dola accounts that can drive S channel (upgrade package). */
function onlineDoubaoUpgradeAccounts(provider = "doubao") {
  const want = provider === "dola" ? "dola" : "doubao";
  return (window.__accounts || []).filter(
    (a) => (a.provider || "doubao") === want && a.status === "online" && accountDoubaoTier(a) === "upgrade"
  );
}

function onlineDoubaoRegularAccounts(provider = "doubao") {
  const want = provider === "dola" ? "dola" : "doubao";
  return (window.__accounts || []).filter(
    (a) => (a.provider || "doubao") === want && a.status === "online" && accountDoubaoTier(a) !== "upgrade"
  );
}

function onlineDoubaoAccounts(provider = "doubao") {
  const want = provider === "dola" ? "dola" : "doubao";
  return (window.__accounts || []).filter(
    (a) => (a.provider || "doubao") === want && a.status === "online"
  );
}

/**
 * Sync channel with live package availability.
 * - DouBao S → DouBao when upgrade packages are gone/expired (avoid empty S pool).
 * - Never DouBao → DouBao S: manual DouBao choice is respected.
 * - Dola 无 S，不参与套餐升降级。
 */
function applyDoubaoSChannelPolicy(opts = {}) {
  if (typeof CanvasStore === "undefined" || !isDoubaoChannelEnabled?.()) {
    return { upgraded: 0, downgraded: 0, canUseS: false };
  }
  let downgraded = 0;
  let canUseS = false;
  const targetId = opts.nodeId || null;

  // 历史 Dola / Dola S → DouBao（Dola 已下线）
  for (const board of CanvasStore.boards || []) {
    for (const node of board.nodes || []) {
      if (!node) continue;
      if (targetId && node.id !== targetId) continue;
      if (node.channel === "dola" || node.channel === "dola_s") {
        node.channel = "doubao";
        node.useUpgrade = false;
        downgraded += 1;
      }
    }
  }

  const provider = "doubao";
  const online = onlineDoubaoAccounts(provider);
  if (online.length) {
    const canUseSProv = onlineDoubaoUpgradeAccounts(provider).length > 0;
    if (canUseSProv) canUseS = true;
    if (!canUseSProv) {
      const sChannel = "doubao_s";
      const plainChannel = "doubao";
      for (const board of CanvasStore.boards || []) {
        for (const node of board.nodes || []) {
          if (!node || !(typeof isGenerativeNode === "function" ? isGenerativeNode(node) : ["image","video","text","voice-text"].includes(node.type))) continue;
          if (targetId && node.id !== targetId) continue;
          const ch =
            typeof normalizeChannel === "function" ? normalizeChannel(node.channel, node.model) : node.channel;
          if (ch !== sChannel) continue;
          const patch = { channel: plainChannel, useUpgrade: false };
          if (node.type === "video") {
            const models =
              typeof videoModelsForChannel === "function" ? videoModelsForChannel(plainChannel) : [];
            const model = models.some((m) => m.id === node.model)
              ? node.model
              : typeof defaultModelForChannel === "function"
                ? defaultModelForChannel(plainChannel, "video")
                : "seedance_v2.0_fast";
            patch.model = model;
            patch.duration = clampVideoDuration(node.duration ?? 10, model, false);
          }
          Object.assign(node, patch);
          downgraded += 1;
        }
      }
    }
  }
  if (downgraded) {
    CanvasStore.save();
    if (typeof renderNodes === "function") renderNodes();
    if (opts.toast !== false && typeof showCanvasToast === "function") {
      showCanvasToast(
        targetId
          ? "升级套餐不可用/已到期，本节点已降级为普通渠道"
          : `升级套餐不可用/已到期，已降级为普通渠道（${downgraded} 个节点）`
      );
    }
  }
  return { upgraded: 0, downgraded, canUseS };
}

window.applyDoubaoSChannelPolicy = applyDoubaoSChannelPolicy;

/** Probe packages then apply expire-downgrade only (never auto-upgrade channel). */
async function syncDoubaoTiersBeforeUse(opts = {}) {
  if (!isDoubaoChannelEnabled?.()) return { upgraded: 0, downgraded: 0, canUseS: false };
  try {
    const res = await api("/api/accounts/sync-doubao-tiers", {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (Array.isArray(res?.accounts)) {
      window.__accounts = res.accounts;
      if (typeof renderAccounts === "function") renderAccounts(res.accounts);
      const doubaoList = res.accounts.filter(
        (a) => (a.provider || "doubao") === "doubao" && a.status === "online"
      );
      const dolaList = res.accounts.filter((a) => a.provider === "dola" && a.status === "online");
      const doubaoOnline = doubaoList.length;
      const doubaoSOnline = doubaoList.filter((a) => accountDoubaoTier(a) === "upgrade").length;
      const doubaoRegularOnline = doubaoOnline - doubaoSOnline;
      const dolaOnline = dolaList.length;
      const bd = document.getElementById("online-badge-doubao");
      if (bd) {
        bd.textContent = doubaoOnline
          ? `在线 ${doubaoOnline} · S ${doubaoSOnline} · 普通 ${doubaoRegularOnline}`
          : "0 个在线";
      }
      const bdo = document.getElementById("online-badge-dola");
      if (bdo) {
        bdo.textContent = dolaOnline ? `在线 ${dolaOnline}` : "0 个在线";
      }
      const badge = document.getElementById("online-badge");
      if (badge) {
        badge.textContent = `共 ${doubaoOnline + dolaOnline} 个在线（Seed ${doubaoOnline}）`;
      }
    }
  } catch (err) {
    console.warn("sync-doubao-tiers failed", err);
  }
  return applyDoubaoSChannelPolicy({
    nodeId: opts.nodeId || null,
    toast: opts.toast !== false,
  });
}

window.syncDoubaoTiersBeforeUse = syncDoubaoTiersBeforeUse;

async function confirmUpgradeOnlyAccounts() {
  // Channel is chosen manually; ordinary DouBao may use any online DouBao account
  // with ordinary model/duration limits — never force-switch to DouBao S.
  return { proceed: true, useUpgrade: false };
}

function renderAccountTable(listEl, accounts, provider) {
  if (!listEl) return;
  if (!accounts.length) {
    const empty =
      provider === "dola"
        ? `还没有 Dola 账号，点「+ 添加」打开浏览器登录`
        : `还没有 Seed 账号，点「+ 添加」扫码登录`;
    listEl.innerHTML = `<div class="account-empty">${empty}</div>`;
    return;
  }
  listEl.innerHTML = `
    <div class="account-row account-row-head" aria-hidden="true">
      <span>账号</span>
      <span>今日用量</span>
      <span>状态</span>
      <span></span>
    </div>
    ${accounts
      .map((a) => {
        const cool = accountCoolingInfo(a);
        const statusText = cool.cooling ? cool.label : statusLabel(a.status);
        const statusClass = cool.cooling ? "cooling" : a.status;
        const usage = `图 ${a.image_count_today || 0} · 视频 ${a.video_count_today || 0}`;
        const tierBadge =
          provider === "doubao"
            ? `<span class="account-tier-badge tier-${accountDoubaoTier(a)}">${esc(accountPlanDisplay(a))}</span>`
            : "";
        return `
    <div class="account-row${cool.cooling ? " is-cooling" : ""}" data-provider="${provider}" title="${esc(a.last_error || "")}">
      <div class="account-row-name">
        <span class="status-dot ${statusClass}"></span>
        <div class="account-row-identity">
          <span class="account-name" title="${esc(a.name)}">${esc(a.name)}</span>
          ${tierBadge}
        </div>
      </div>
      <div class="account-row-usage">${usage}</div>
      <div class="account-row-status status-${statusClass}">${esc(statusText)}</div>
      <div class="account-row-actions">
        <button type="button" class="btn btn-sm btn-ghost btn-login" data-id="${a.id}" data-provider="${provider}">登录</button>
        <button type="button" class="btn btn-sm btn-ghost btn-check" data-id="${a.id}">检测</button>
        <button type="button" class="btn btn-sm btn-ghost btn-delete" data-id="${a.id}">删除</button>
      </div>
    </div>`;
      })
      .join("")}
  `;

  listEl.querySelectorAll(".btn-login").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.dataset.provider === "dola") startDoubaoManualLogin(b.dataset.id);
      else startQrLogin(b.dataset.id);
    })
  );
  listEl.querySelectorAll(".btn-check").forEach((b) => {
    b.addEventListener("click", async () => {
      b.disabled = true;
      const oldText = b.textContent;
      b.textContent = "检测中…";
      try {
        const res = await api(`/api/accounts/${b.dataset.id}/check`, { method: "POST" });
        await loadAccounts();
        if (res.logged_in) {
          const name = res.name || res.account?.name || "";
          const isDola = res.account?.provider === "dola";
          const tier = !isDola ? accountPlanDisplay(res.account) : "";
          const tierLine = tier ? `\n套餐：${tier}` : "";
          await AppDialog.alert(
            name ? `在线 ✓${tierLine}\n名称已更新为「${name}」` : `在线 ✓${tierLine}`,
            { title: "账号状态" }
          );
          if (res.account?.provider === "doubao") {
            applyDoubaoSChannelPolicy({ toast: true });
          }
        } else {
          const isDola = res.account?.provider === "dola";
          await AppDialog.alert(
            isDola ? "未登录，请点「登录」打开浏览器完成登录" : "未登录，请扫码",
            { title: "账号状态" }
          );
        }
      } catch (err) {
        await AppDialog.alert(err.message || "检测失败", { title: "账号状态" });
      } finally {
        b.disabled = false;
        b.textContent = oldText || "检测";
      }
    });
  });
  listEl.querySelectorAll(".btn-delete").forEach((b) => {
    b.addEventListener("click", async () => {
      const ok = await AppDialog.confirm("删除此账号？", {
        title: "删除账号",
        okText: "删除",
        danger: true,
      });
      if (!ok) return;
      await api(`/api/accounts/${b.dataset.id}`, { method: "DELETE" });
      loadAccounts();
    });
  });
}

function renderAccounts(accounts) {
  const doubao = accounts.filter((a) => (a.provider || "doubao") === "doubao");
  renderAccountTable($("#account-list-doubao"), doubao, "doubao");
  const listDola = $("#account-list-dola");
  if (listDola) listDola.innerHTML = "";
}

async function startGptLogin(_accountId) {
  await AppDialog.alert("OpenAI 渠道已下线，请改用 BenDi（本地 ChatGPT）。", {
    title: "OpenAI 已下线",
  });
}

function pollGptLoginStatus(_accountId) {
  /* OpenAI channel retired */
}

async function setQrImageSmooth(imgEl, base64) {
  if (!imgEl || !base64) return;
  const url = `data:image/png;base64,${base64}`;
  imgEl.classList.remove("is-ready");
  // Decode off the live node first so swapping in doesn't hitch the UI thread as hard.
  const probe = new Image();
  probe.decoding = "async";
  probe.src = url;
  try {
    if (typeof probe.decode === "function") await probe.decode();
    else {
      await new Promise((resolve, reject) => {
        probe.onload = resolve;
        probe.onerror = reject;
      });
    }
  } catch {
    /* still assign below */
  }
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  imgEl.src = url;
  imgEl.style.opacity = "";
  requestAnimationFrame(() => imgEl.classList.add("is-ready"));
}

async function startQrLogin(accountId, { deleteOnCancel = false } = {}) {
  dismissOverlay($("#modal-settings"));
  clearQrLoginTimers();
  window.__qrLoginAccountId = accountId;
  window.__qrLoginDeleteOnCancel = !!deleteOnCancel;
  window.__qrLoginHardLock = false;
  window.__qrLoginLocked = false;
  window.__qrLoginForceClosing = false;
  const img = $("#qr-image");
  $("#qr-title").textContent = "扫码登录";
  if (img) {
    img.removeAttribute("src");
    img.classList.remove("is-ready");
  }
  setQrLoginStatus("获取二维码…", { step: "0" });
  updateQrCountdownUi(null);
  setQrCloseLocked(false);
  revealOverlay($("#modal-qr-login"));
  try {
    const res = await api(`/api/accounts/${accountId}/qr-login`, { method: "POST" });
    if (res.qr_image_base64) {
      await setQrImageSmooth(img, res.qr_image_base64);
      setQrLoginStatus(res.message || "① 等待扫码…", { step: "pending" });
    }
    startQrCountdown(accountId);
    pollQrStatus(accountId);
  } catch (err) {
    setQrLoginStatus(err.message || "获取失败", { error: true });
    setQrCloseLocked(false);
    updateQrCountdownUi(null);
  }
}

function clearQrLoginTimers() {
  if (qrPollTimer) {
    clearInterval(qrPollTimer);
    qrPollTimer = null;
  }
  if (qrCountdownTimer) {
    clearInterval(qrCountdownTimer);
    qrCountdownTimer = null;
  }
}

function updateQrCountdownUi(secondsLeft) {
  const el = $("#qr-countdown");
  if (!el) return;
  if (secondsLeft == null) {
    el.textContent = "";
    return;
  }
  el.textContent =
    secondsLeft > 0
      ? `剩余 ${secondsLeft} 秒 · 超时将强制关闭并停止轮询`
      : "已超时，正在关闭…";
}

function startQrCountdown(accountId) {
  window.__qrLoginDeadline = Date.now() + QR_LOGIN_TIMEOUT_MS;
  if (qrCountdownTimer) {
    clearInterval(qrCountdownTimer);
    qrCountdownTimer = null;
  }
  const tick = () => {
    if (window.__qrLoginAccountId !== accountId || window.__qrLoginForceClosing) {
      if (qrCountdownTimer) {
        clearInterval(qrCountdownTimer);
        qrCountdownTimer = null;
      }
      return;
    }
    const left = Math.max(0, Math.ceil((window.__qrLoginDeadline - Date.now()) / 1000));
    updateQrCountdownUi(left);
    if (left <= 0) {
      void forceCloseQrLoginTimeout(accountId);
    }
  };
  tick();
  qrCountdownTimer = setInterval(tick, 250);
}

async function forceCloseQrLoginTimeout(accountId) {
  if (window.__qrLoginForceClosing) return;
  if (window.__qrLoginAccountId !== accountId) return;
  window.__qrLoginForceClosing = true;
  window.__qrLoginHardLock = false;
  window.__qrLoginLocked = false;
  clearQrLoginTimers();
  setQrCloseLocked(false);
  setQrLoginStatus("登录超时（1分钟），已停止轮询", { error: true, step: "timeout" });
  updateQrCountdownUi(0);
  const id = window.__qrLoginAccountId;
  const deleteIncomplete = !!window.__qrLoginDeleteOnCancel;
  window.__qrLoginAccountId = null;
  window.__qrLoginDeleteOnCancel = false;
  try {
    if (id) {
      await api(`/api/accounts/${id}/qr-login/cancel`, {
        method: "POST",
        body: JSON.stringify({ delete_incomplete: deleteIncomplete }),
      });
    }
  } catch {
    /* ignore */
  }
  setTimeout(() => {
    dismissOverlay($("#modal-qr-login"));
    updateQrCountdownUi(null);
    void loadAccounts().catch(() => {});
  }, 900);
}

/** Hard-lock only after phone confirm / finalize. Scanned still allows cancel. */
function setQrCloseLocked(locked, { label } = {}) {
  window.__qrLoginHardLock = !!locked;
  window.__qrLoginLocked = !!locked;
  const btn = $("#btn-close-qr");
  if (!btn) return;
  btn.disabled = !!locked;
  btn.textContent = locked ? "请稍候…" : label || "关闭";
  btn.title = locked
    ? "手机已确认，正在完成登录（超时将自动关闭）"
    : label === "取消登录"
      ? "取消本次扫码登录并停止轮询"
      : "";
}

function setQrLoginStatus(text, { error = false, success = false, step = "" } = {}) {
  const el = $("#qr-status");
  if (el) {
    el.textContent = text || "";
    el.className = "qr-status" + (error ? " error" : success ? " success" : "");
  }
  const hint = $("#qr-step-hint");
  if (hint) {
    const map = {
      "0": "当前：获取二维码",
      pending: "当前：① 等待扫码",
      scanned: "当前：② 已扫码，待手机确认（可取消关闭）",
      confirmed: "当前：③～④ 确认后获取并保存凭证",
      finalizing: "当前：⑤～⑧ 完善昵称并检测套餐（普通/S）",
      success: "当前：登录完成",
      timeout: "当前：已超时关闭",
    };
    hint.textContent = map[step] || (text ? `当前：${text}` : "");
    hint.hidden = !hint.textContent;
  }
}

async function startDoubaoManualLogin(accountId) {
  const id = accountId || window.__qrLoginAccountId;
  if (!id) {
    await AppDialog.alert("请先选择账号", { title: "自主登录" });
    return;
  }
  clearQrLoginTimers();
  updateQrCountdownUi(null);
  dismissOverlay($("#modal-settings"));
  const account = (window.__accounts || []).find((a) => a.id === id);
  $("#qr-title").textContent = `自主登录 · ${account?.name || ""}`;
  if ($("#qr-image")) $("#qr-image").style.opacity = "0.35";
  $("#qr-status").textContent = "正在打开浏览器…";
  $("#qr-status").className = "qr-status";
  revealOverlay($("#modal-qr-login"));
  try {
    const res = await api(`/api/accounts/${id}/browser-login`, { method: "POST" });
    $("#qr-status").textContent = res.message || "请在弹出的浏览器中自行登录";
    pollDoubaoManualLogin(id);
  } catch (err) {
    $("#qr-status").textContent = err.message || "启动失败";
    $("#qr-status").className = "qr-status error";
  }
}

function pollDoubaoManualLogin(accountId) {
  clearInterval(qrPollTimer);
  qrPollTimer = setInterval(async () => {
    try {
      const res = await api(`/api/accounts/${accountId}/browser-login`);
      if (res.message) $("#qr-status").textContent = res.message;
      if (res.status === "success") {
        clearInterval(qrPollTimer);
        $("#qr-status").textContent = res.message || "登录成功！";
        $("#qr-status").className = "qr-status success";
        await loadAccounts();
        applyDoubaoSChannelPolicy({ toast: true });
        setTimeout(() => dismissOverlay($("#modal-qr-login")), 1200);
      } else if (["failed", "timeout"].includes(res.status)) {
        clearInterval(qrPollTimer);
        $("#qr-status").textContent = res.message || "失败";
        $("#qr-status").className = "qr-status error";
        loadAccounts();
      }
    } catch {
      /* ignore */
    }
  }, 2000);
}

function pollQrStatus(accountId) {
  clearInterval(qrPollTimer);
  qrPollTimer = setInterval(async () => {
    try {
      if (window.__qrLoginForceClosing || window.__qrLoginAccountId !== accountId) {
        clearQrLoginTimers();
        return;
      }
      const res = await api(`/api/accounts/${accountId}/qr-login`);
      if (res.qr_image_base64) {
        const img = $("#qr-image");
        const next = `data:image/png;base64,${res.qr_image_base64}`;
        if (img && img.src !== next) {
          void setQrImageSmooth(img, res.qr_image_base64);
        }
      }

      const st = String(res.status || "");
      // Only hard-lock after phone confirm; scanned allows cancel/close.
      if (["confirmed", "finalizing"].includes(st)) {
        setQrCloseLocked(true);
      } else if (st === "scanned") {
        setQrCloseLocked(false, { label: "取消登录" });
      } else if (!["success"].includes(st)) {
        setQrCloseLocked(false);
      }

      if (st === "success") {
        window.__qrLoginDeleteOnCancel = false;
        clearQrLoginTimers();
        updateQrCountdownUi(null);
        setQrCloseLocked(true);
        const name = res.account?.name;
        const plan =
          res.account?.provider !== "dola" ? accountPlanDisplay(res.account) : "";
        setQrLoginStatus(
          res.message ||
            (name ? `登录成功 · ${name}${plan ? ` · ${plan}` : ""}` : "登录成功！"),
          { success: true, step: "success" }
        );
        await loadAccounts();
        window.CanvasAuth?.refreshPoolSummary?.();
        applyDoubaoSChannelPolicy({ toast: true });
        setQrCloseLocked(false);
        setTimeout(() => dismissOverlay($("#modal-qr-login")), 1400);
        return;
      }

      if (st === "finalizing" || st === "confirmed") {
        setQrLoginStatus(res.message || "正在完成登录，请稍候…", {
          step: st === "confirmed" ? "confirmed" : "finalizing",
        });
        return;
      }

      if (st === "scanned") {
        setQrLoginStatus(res.message || "② 已扫码，请在手机上确认登录", {
          step: "scanned",
        });
        return;
      }

      if (["failed", "expired", "timeout"].includes(st)) {
        setQrCloseLocked(false);
        setQrLoginStatus(res.message || "失败", {
          error: true,
          step: st === "timeout" ? "timeout" : "",
        });
        loadAccounts();
        if (res.duplicate) {
          clearQrLoginTimers();
          updateQrCountdownUi(null);
          await AppDialog.alert(res.message || "该 Seed 账号已登录，请勿重复添加", {
            title: "重复登录",
          });
          window.__qrLoginAccountId = null;
          setTimeout(() => dismissOverlay($("#modal-qr-login")), 400);
          return;
        }
        if (st === "timeout") {
          // Backend already stopped polling; cancel/cleanup and force-close UI.
          void forceCloseQrLoginTimeout(accountId);
          return;
        }
        if (st === "failed") {
          clearQrLoginTimers();
          updateQrCountdownUi(null);
          return;
        }
        // expired: stop poll only; keep 1-minute countdown to force-close
        if (qrPollTimer) {
          clearInterval(qrPollTimer);
          qrPollTimer = null;
        }
        return;
      }

      if (res.message) {
        setQrLoginStatus(res.message, { step: st || "pending" });
      }
    } catch {
      /* ignore transient poll errors */
    }
  }, 1200);
}

async function cancelCloseQrLogin() {
  if (window.__qrLoginForceClosing) return;
  if (window.__qrLoginHardLock) {
    setQrLoginStatus("手机已确认，正在完成登录。请稍候，或等待超时自动关闭", {
      step: "finalizing",
    });
    return;
  }
  clearQrLoginTimers();
  updateQrCountdownUi(null);
  const id = window.__qrLoginAccountId;
  const deleteIncomplete = !!window.__qrLoginDeleteOnCancel;
  window.__qrLoginAccountId = null;
  window.__qrLoginDeleteOnCancel = false;
  window.__qrLoginHardLock = false;
  window.__qrLoginLocked = false;
  dismissOverlay($("#modal-qr-login"));
  if (!id) return;
  try {
    await api(`/api/accounts/${id}/qr-login/cancel`, {
      method: "POST",
      body: JSON.stringify({ delete_incomplete: deleteIncomplete }),
    });
  } catch {
    /* ignore — UI already closed */
  }
  try {
    await loadAccounts();
  } catch {
    /* ignore */
  }
}

$("#btn-add-account")?.addEventListener("click", async () => {
  try {
    await window.addDoubaoAccountFromAvatar();
  } catch (err) {
    await AppDialog.alert(err.message || "创建失败", { title: "添加账号" });
  }
});

window.addDoubaoAccountFromAvatar = async function addDoubaoAccountFromAvatar() {
  const role = String(window.__canvasUser?.role || "").toLowerCase();
  if (role !== "admin" && role !== "internal") {
    throw new Error("仅管理员或内部人员可添加账号");
  }
  // Skip name dialog — create with auto name, go straight to QR scan
  const account = await api("/api/accounts", {
    method: "POST",
    body: JSON.stringify({ provider: "doubao" }),
  });
  await loadAccounts();
  startQrLogin(account.id, { deleteOnCancel: true });
};

$("#btn-add-dola-account")?.addEventListener("click", async () => {
  await AppDialog.alert("该渠道已下线（浏览器易频繁登录），请改用 Seed 或 BenDi。", {
    title: "Dola 已下线",
  });
});

$("#btn-add-gpt-account")?.addEventListener("click", async () => {
  await AppDialog.alert("OpenAI 渠道已下线，请改用 BenDi（本地 ChatGPT）。", {
    title: "OpenAI 已下线",
  });
});
$("#btn-cancel-add")?.addEventListener("click", () => dismissOverlay($("#modal-add-account")));
$("#form-add-account")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  dismissOverlay($("#modal-add-account"));
});

window.__genPrepareLocks = window.__genPrepareLocks || new Set();
function isGenPreparing(nodeId) {
  return !!(nodeId && window.__genPrepareLocks?.has(nodeId));
}
function beginGenPrepare(nodeId) {
  if (!nodeId) return false;
  if (window.__genPrepareLocks.has(nodeId)) return false;
  if (window.__genInFlight?.has(nodeId)) return false;
  window.__genPrepareLocks.add(nodeId);
  syncGenPrepareUI(nodeId);
  return true;
}
function endGenPrepare(nodeId) {
  if (!nodeId) return;
  window.__genPrepareLocks?.delete(nodeId);
  syncGenPrepareUI(nodeId);
}
function syncGenPrepareUI(nodeId) {
  const preparing = isGenPreparing(nodeId);
  const node = typeof findNodeAnywhere === "function" ? findNodeAnywhere(nodeId) : null;
  const busy = ["waiting", "pending", "generating"].includes(node?.status);
  document.querySelectorAll(`.btn-generate[data-node="${CSS.escape(String(nodeId))}"]`).forEach((btn) => {
    btn.classList.toggle("is-preparing", preparing);
    btn.setAttribute("aria-busy", String(preparing || busy));
    btn.setAttribute("aria-label", preparing ? "正在准备生成" : "生成");
    btn.title = preparing ? "正在准备生成" : "生成";
    btn.textContent = preparing ? "…" : "↑";
    btn.disabled = preparing || busy;
  });
}

// A status change only needs to repaint its own card. Keep other editors,
// media and controls alive instead of scanning/remounting the full board.
function renderGenerationStart(nodeId) {
  if (typeof isCanvasInteractionActive === "function" && isCanvasInteractionActive()) {
    renderNodes();
    return;
  }
  const node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
  const wrap = document.querySelector(`.node-wrap[data-id="${CSS.escape(String(nodeId))}"]`);
  if (wrap && node && typeof softPatchNodeGenUi === "function" && softPatchNodeGenUi(wrap, node)) {
    syncChannelToolbarIfStale(wrap, node);
    wrap.dataset.structFp = nodeStructuralFingerprint(node);
    wrap.dataset.fp = nodeDomFingerprint(node, wrap.dataset.structFp);
    scheduleRenderConnections(true);
    return;
  }
  renderNodes();
}
window.isGenPreparing = isGenPreparing;
window.beginGenPrepare = beginGenPrepare;
window.endGenPrepare = endGenPrepare;

async function generateForNode(nodeId, options = {}) {
  const requestBoardId = CanvasStore.activeBoard()?.id;
  let node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
  const initialPrompt = node?.prompt;
  const checkRequest = () => {
    if (options.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    const live = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
    if (CanvasStore.activeBoard()?.id !== requestBoardId || !live) throw new Error("画板或目标节点已变化，未提交生成");
    if (live.prompt !== initialPrompt) throw new Error("准备期间提示词已变化，请核对后重新生成");
    return live;
  };
  if (options.signal?.aborted) checkRequest();
  if (!node || !(typeof isGenerativeNode === "function" ? isGenerativeNode(node) : ["video","image","text","voice-text","voice"].includes(node.type))) return;
  if (node.status === "waiting" || node.status === "pending" || node.status === "generating") return;
  if (genQueue.includes(nodeId)) return;
  if (isGenPreparing(nodeId) || isGenInFlight(nodeId)) return;
  if (!beginGenPrepare(nodeId)) return;
  try {
  // Close @ 素材列表 / 预览 so they don't float over generating results.
  if (typeof hideMediaHoverPreviews === "function") hideMediaHoverPreviews();
  else if (typeof MentionUI !== "undefined") MentionUI.hide?.();

  const card = document.querySelector(`.node-wrap[data-id="${nodeId}"]`);
  const promptEl = card?.querySelector(".prompt-editor[data-node]");
  const rawPrompt = options.imageEdit
    ? String(options.imageEdit.instruction || "")
    : promptEl
    ? (typeof PromptEditor !== "undefined" ? PromptEditor.getValue(promptEl) : "")
    : node.prompt;
  const prompt = (rawPrompt || "").trim();
  let channel = normalizeChannel(node.channel, node.model);

  // Generate-time package probe → DouBao ↔ DouBao S（Dola 不检测套餐）
  if (isDoubaoFamilyChannel(channel) && typeof syncDoubaoTiersBeforeUse === "function") {
    await syncDoubaoTiersBeforeUse({ nodeId, toast: true });
    node = checkRequest();
    channel = normalizeChannel(node.channel, node.model);
  }

  if (node.type === "video" && (channel === "gpt" || channel === "openai")) {
    if (typeof showCanvasToast === "function") showCanvasToast("OpenAI 渠道已下线，请改用 BenDi");
    return;
  }
  const textLike = typeof isTextGenNode === "function" ? isTextGenNode(node) : node.type === "text" || node.type === "voice-text";
  const voiceLike = node.type === "voice";
  const audioMode =
    voiceLike && typeof getNodeAudioAppMode === "function" ? getNodeAudioAppMode(node) : "tts";
  if (voiceLike && audioMode === "record") {
    await AppDialog.alert("录音模式请点「开始录音」，不需要点生成", { title: "音频生成" });
    return;
  }
  if (voiceLike && channel !== "volcengine") channel = "myapi";
  let model =
    node.type === "video"
      ? node.model || defaultModelForChannel(channel, "video")
      : voiceLike
        ? node.model || defaultModelForChannel(channel, "voice")
        : textLike
          ? node.model || defaultModelForChannel(channel, "text")
          : node.model || defaultModelForChannel(channel, "image");
  if (channel === "volcengine") {
    if (typeof isVolcengineChannelAvailable === "function" && !isVolcengineChannelAvailable(node.type)) {
      await AppDialog.alert("请先在设置 → 火山引擎 中填写 Key 并开启对应类型开关", { title: "火山引擎" });
      return;
    }
    if (!(typeof isVolcengineModel === "function" && isVolcengineModel(model)) || !model) {
      model = defaultModelForChannel("volcengine", voiceLike ? "voice" : textLike ? "text" : node.type) || "";
    }
    if (!model) {
      await AppDialog.alert("请选择火山引擎模型", { title: "火山引擎" });
      return;
    }
  } else if (channel === "myapi") {
    if (!isMyApiModel(model) || !model) {
      await AppDialog.alert(
        voiceLike ? "请先在设置 → MyApi → 语音 中添加并选择模型" : "请先在设置中添加并选择 MyApi 模型",
        { title: "MyApi" }
      );
      return;
    }
  } else if (channel === "bendi") {
    const textLike = typeof isTextGenNode === "function" ? isTextGenNode(node) : node.type === "text" || node.type === "voice-text";
    if (textLike && !(typeof isBendiTextAllowed === "function" && isBendiTextAllowed())) {
      await AppDialog.alert("BenDi 文本需在设置中切换到「本地」后端", { title: "BenDi" });
      return;
    }
    if (typeof isBendiChannelEnabled === "function" && !isBendiChannelEnabled()) {
      await AppDialog.alert("BenDi 仅限管理员使用", { title: "BenDi" });
      return;
    }
    if (node.type === "video") {
      const models =
        typeof videoModelsForChannel === "function" ? videoModelsForChannel("bendi") : [];
      if (!models.some((m) => m.id === model)) {
        model = defaultModelForChannel("bendi", "video") || models[0]?.id || "";
      }
    } else if (textLike) {
      const models =
        typeof textModelsForChannel === "function" ? textModelsForChannel("bendi") : [];
      if (!models.some((m) => m.id === model)) {
        model = defaultModelForChannel("bendi", "text") || models[0]?.id || "dola-chat";
      }
    } else {
      const models =
        typeof imageModelsForChannel === "function" ? imageModelsForChannel("bendi") : [];
      if (!models.some((m) => m.id === model)) {
        model = defaultModelForChannel("bendi", "image") || models[0]?.id || "bendi_image";
      }
    }
    try {
      const health = await api("/api/bendi/health");
      if (!health?.ok) {
        const local = health?.bendi_mode === "local" || window.AppConfig?.bendi_mode === "local";
        await AppDialog.alert(
          health?.message ||
            (local
              ? "BenDi 本地服务未就绪。请启动本地服务（默认 127.0.0.1:18765）。"
              : "本机星晚AI 未就绪。请启动星晚AI，确认左下角「后台运行正常」，并在设置中填写 API Key。"),
          { title: "BenDi 本机未开启" }
        );
        return;
      }
    } catch (err) {
      const msg = err.message || "";
      const local = window.AppConfig?.bendi_mode === "local";
      await AppDialog.alert(
        /not found/i.test(msg)
          ? "BenDi 接口未生效，请重启画布服务后再试。"
          : msg ||
            (local
              ? "无法检测 BenDi 本地服务。请确认已启动且端口可访问。"
              : "无法检测本机星晚AI。请启动软件并确认 127.0.0.1 端口可访问。"),
        { title: "BenDi 本机未开启" }
      );
      return;
    }
  } else if (node.type === "image") {
    if (isLegacyBrowserGptModel?.(model) || isGptImageModel?.(model) || isMyApiModel(model) || isBendiModel?.(model)) {
      model = DEFAULT_IMAGE_MODEL;
    }
  } else if (textLike) {
    model = "doubao_chat";
  } else if (node.type === "video" && (isMyApiModel(model) || isBendiModel?.(model))) {
    model =
      typeof defaultModelForChannel === "function"
        ? defaultModelForChannel(isDolaFamilyChannel(channel) ? "dola" : "doubao", "video")
        : "seedance_v2.0_fast";
  }
  let useUpgrade = channel === "doubao_s";
  if (node.type === "video" && channel === "doubao") {
    // Ordinary DouBao cannot use Seedance 2.0 / 2.5 — if stuck, force Fast.
    if (typeof isUpgradeDoubaoVideoModel === "function" && isUpgradeDoubaoVideoModel(model)) {
      model =
        typeof defaultModelForChannel === "function"
          ? defaultModelForChannel(channel, "video")
          : "seedance_v2.0_fast";
    }
  }
  // Preflight: DouBao 按套餐分流；Dola 只要有在线账号即可
  if ((node.type === "video" || textLike) && isBytedanceFamilyChannel(channel)) {
    if (isDolaFamilyChannel(channel)) {
      if (!onlineDoubaoAccounts("dola").length) {
        await AppDialog.alert("当前没有可用的账号，请先在后台登录/检测。", {
          title: "无法生成",
        });
        return;
      }
    } else {
      const regularN = onlineDoubaoRegularAccounts("doubao").length;
      const upgradeN = onlineDoubaoUpgradeAccounts("doubao").length;
      if (channel === "doubao" && regularN === 0) {
        const msg = upgradeN
          ? "当前在线账号都是升级套餐（如标准套餐），Seed 普通渠道没有可用账号。请改选 Seed S。"
          : "当前没有可用的 Seed 普通账号，请先在后台登录/检测。";
        await AppDialog.alert(msg, { title: "无法生成" });
        return;
      }
      if (channel === "doubao_s" && upgradeN === 0) {
        const msg = regularN
          ? "当前没有升级套餐账号，Seed S 不可用。请改选 Seed，或先检测有套餐的账号。"
          : "当前没有可用的 Seed S 账号，请先在后台登录/检测。";
        await AppDialog.alert(msg, { title: "无法生成" });
        return;
      }
    }
  }
  const duration =
    node.type === "video"
      ? clampVideoDuration(node.duration ?? 10, model, useUpgrade, channel)
      : null;
  const resolution =
    node.type === "video"
      ? node.resolution || "自动"
      : node.type === "image" && (channel === "myapi" || channel === "volcengine")
        ? node.resolution || (channel === "volcengine" ? "2K" : "默认")
        : null;
  const style =
    node.type === "image" && isBytedanceFamilyChannel(channel) ? (node.style || null) : null;
  const ratio = node.ratio || "自动";
  const fullPromptRaw = typeof resolvePromptWithRefs === "function"
    ? resolvePromptWithRefs(nodeId, prompt)
    : prompt;
  const mediaType = node.type === "video" ? "video" : node.type === "image" ? "image" : null;
  const fullPrompt =
    mediaType && typeof PromptPrefixStore !== "undefined"
      ? PromptPrefixStore.applyPrefix(node.promptPrefixId, mediaType, fullPromptRaw)
      : fullPromptRaw;
  const refLimits = typeof referenceLimitsForNode === "function" ? referenceLimitsForNode({ ...node, channel, model }) : null;
  let refs = typeof collectReferences === "function"
    ? collectReferences(nodeId, prompt, { includeBarRefs: true, limits: refLimits })
    : { all: [], images: [], videos: [], audios: [] };
  const videoMode =
    node.type === "video"
      ? typeof getNodeVideoGenMode === "function"
        ? getNodeVideoGenMode(node)
        : node.videoMode || "omni"
      : null;
  // 纯提示词视频：不带参考素材
  if (videoMode === "text") {
    refs = { all: [], images: [], videos: [], audios: [] };
  }
  // CSS-only AI marks look stamped in the UI but pixels are unmarked — bake before upload (silent).
  if (videoMode !== "text" && typeof ensureReferencedAiMarksBaked === "function") {
    const bakeFailed = await ensureReferencedAiMarksBaked(refs);
    if (bakeFailed.length) {
      const sample = bakeFailed.slice(0, 5).join("、");
      const more = bakeFailed.length > 5 ? ` 等 ${bakeFailed.length} 张` : "";
      await AppDialog.alert(
        `参考图「${sample}」${more}的 AI 标记还没写入像素，上传会丢水印。\n\n请对该图重新点一次「AI标记」后再生成。`,
        { title: "AI 标记未写入" }
      );
      return;
    }
    refs = typeof collectReferences === "function"
      ? collectReferences(nodeId, prompt, { includeBarRefs: true, limits: refLimits })
      : refs;
  }
  if (options.imageEdit) {
    // Snapshot the current original as the first reference. Keep this in the
    // ordinary job payload so retry, refresh and history use the same image.
    const sourceUrl = String(options.imageEdit.sourceUrl || "");
    if (!sourceUrl || node.type !== "image") throw new Error("图片修改缺少原图");
    const original = { kind: "image", url: sourceUrl };
    refs.images = [original, ...refs.images.filter((r) => r.url !== sourceUrl)];
    refs.all = [original, ...refs.all.filter((r) => r.url !== sourceUrl)];
    if (refLimits?.maxImages != null && refs.images.length > refLimits.maxImages) throw new Error("参考图数量超过当前模型上限，或当前模型不支持参考图");
  }
  const referenceImages = refs.images.map((r) => r.url);

  // Require real user/wired content — prefix alone must not start a generation.
  let jobPrompt = fullPrompt;
  if (voiceLike && audioMode === "music") {
    const lyricsEl = card?.querySelector(".audio-lyrics-input");
    const lyrics = String(lyricsEl?.value ?? node.lyrics ?? "").trim();
    if (lyricsEl && lyrics !== String(node.lyrics || "")) {
      CanvasStore.updateNode(nodeId, { lyrics });
    }
    const stylePart = String(fullPromptRaw || "").trim();
    if (!stylePart && !lyrics) {
      await AppDialog.alert("请先输入音乐风格或歌词", { title: "无法生成" });
      card?.querySelector(".prompt-editor")?.focus();
      return;
    }
    jobPrompt = [stylePart, lyrics ? `歌词：\n${lyrics}` : ""].filter(Boolean).join("\n\n");
  } else if (!(fullPromptRaw || "").trim()) {
    const tip =
      node.type === "image"
        ? "请先输入图片描述，或牵线文本节点"
        : node.type === "video"
          ? "请先输入视频描述，或牵线文本节点"
          : voiceLike
            ? "请先输入要合成的文本"
          : (typeof isVoiceTextNode === "function" && isVoiceTextNode(node))
            ? "请先输入音色描述指令，或牵线参考图"
            : "请先输入文本生成指令";
    await AppDialog.alert(tip, { title: "无法生成" });
    card?.querySelector(".prompt-editor")?.focus();
    return;
  }

  if (
    node.type === "video" &&
    typeof videoModeRequiresRefVideo === "function" &&
    videoModeRequiresRefVideo(videoMode)
  ) {
    if (typeof modelSupportsReferenceVideo === "function" && !modelSupportsReferenceVideo(model)) {
      await AppDialog.alert("当前模型不支持参考视频，请切换到支持参考视频的模型", {
        title: "无法生成",
      });
      return;
    }
    if (!(refs.videos || []).length) {
      await AppDialog.alert(
        "该模式需要先添加参考视频",
        { title: "无法生成" }
      );
      return;
    }
  }

  if (typeof collectUnresolvedMentionCodes === "function") {
    const badAts = collectUnresolvedMentionCodes(prompt);
    if (badAts.length) {
      const sample = badAts.slice(0, 8).join("、");
      const more = badAts.length > 8 ? ` 等 ${badAts.length} 处` : "";
      await AppDialog.alert(
        `提示词里仍有未绑定的素材引用：${sample}${more}\n\n复制节点后若只看到缩略图、正文仍是 @代码，请删掉这些 @ 后重新用 @ 选择素材，再生成。`,
        { title: "素材引用未转换" }
      );
      card?.querySelector(".prompt-editor")?.focus();
      return;
    }
  }

  if (typeof findMissingReferenceMedia === "function") {
    const missing = await findMissingReferenceMedia([
      ...(refs.images || []),
      ...(refs.videos || []),
      ...(refs.audios || []),
    ]);
    if (missing.length) {
      missing.forEach((m) => {
        const chip = document.querySelector(
          `.node-card[data-id="${CSS.escape(nodeId)}"] .ref-chip[data-ref-node="${CSS.escape(m.nodeId || "")}"]`
        );
        if (chip && typeof markRefChipBroken === "function") {
          markRefChipBroken(chip, `文件不存在：${m.name}`);
        }
        const thumb = chip?.querySelector?.("img");
        if (thumb && typeof applyMediaMissingFallback === "function") {
          applyMediaMissingFallback(thumb, { markChip: false });
        }
      });
      // One short toast — no modal spam. Chips already show 丢失 + placeholder.
      showCanvasToast?.(
        missing.length === 1
          ? `参考素材「${missing[0].name}」文件不存在`
          : `${missing.length} 个参考素材文件不存在，请移除后重试`
      );
      return;
    }
  }

  // Preflight may have awaited channel health, image baking or reference I/O.
  // Never submit a stale request after navigation, deletion or cancellation.
  checkRequest();
  let editHistory = null;
  if (options.imageEdit) {
    const live = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
    if (CanvasStore.activeBoard()?.id !== requestBoardId || !live || live.imageUrl !== options.imageEdit.sourceUrl) {
      throw new Error("画板或原图已变化，未提交图片修改，请重新核对");
    }
    const original = {
      id: `original-${Date.now().toString(36)}`, imageUrl: live.imageUrl,
      thumbUrl: live.thumbUrl || null, prompt: live.prompt || "", model: live.model,
      ratio: live.ratio, mediaWidth: live.mediaWidth, mediaHeight: live.mediaHeight,
      status: "success", kind: "image", typeLabel: "修改前原图", createdAt: Date.now(),
    };
    editHistory = typeof pushGenHistory === "function" ? pushGenHistory(live, original) : [original, ...(live.genHistory || [])].slice(0, 40);
  }
  CanvasStore.updateNode(nodeId, {
    ...(editHistory ? { genHistory: editHistory } : {}),
    prompt,
    channel,
    accountId: "",
    ratio,
    model,
    duration: node.type === "video" ? duration : null,
    useUpgrade: channel === "doubao_s" || (node.type === "video" ? useUpgrade : false),
    resolution:
      node.type === "video"
        ? resolution
        : node.type === "image" && (channel === "myapi" || channel === "volcengine")
          ? resolution
          : null,
    style: node.type === "image" ? style || null : null,
    // Prefer generating directly when a slot is free — avoid 等待→生成 first paint flicker.
    status: countActiveGenerations() < getMaxThreads() ? "generating" : "waiting",
    error: null,
    isError: false,
    taskId: null,
    generationStopped: false,
    stoppedTaskId: node.taskId || node.stoppedTaskId || null,
    genStartedAt: Date.now(),
    promptSent: false,
    lastGenMs: null,
    autoRetryCount: 0,
    _accountBusyRetries: 0,
    mediaWidth: textLike ? node.mediaWidth : null,
    mediaHeight: textLike ? node.mediaHeight : null,
    _lastRefs: refs.all || [],
    _genPayload: {
      fullPrompt: jobPrompt,
      referenceImages,
      references: refs.all,
      ratio,
      accountId: null,
      channel,
      model,
      duration,
      useUpgrade,
      resolution,
      style,
      type: node.type,
      audioMode: voiceLike ? audioMode : undefined,
    },
    _retryPayload: {
      fullPrompt: jobPrompt,
      referenceImages,
      references: refs.all,
      ratio,
      accountId: null,
      channel,
      model,
      duration,
      useUpgrade,
      resolution,
      style,
      type: node.type,
    },
  });
  if (options.imageEdit && typeof PromptEditor !== "undefined") {
    document.querySelectorAll(".prompt-editor[data-node]").forEach((editor) => {
      if (editor.dataset.node === String(nodeId)) PromptEditor.mount(editor, prompt, { force: true });
    });
  }
  const started = findNodeAnywhere(nodeId);
  if (started?.status === "generating") {
    // Slot already taken by this node; run without waiting→pending hop.
    renderGenerationStart(nodeId);
    updateThreadBadge();
    ensureProgressTicker();
    void runGenerateJob(nodeId);
  } else {
    enqueueGeneration(nodeId);
    renderGenerationStart(nodeId);
  }
  return { submitted: true, nodeId };
  } finally {
    endGenPrepare(nodeId);
  }
}
window.generateForNode = generateForNode;

const genQueue = [];
/** 提交进行中 — 防止 recover 误判重复入队 */
window.__genInFlight = window.__genInFlight || new Set();
const GEN_RECOVER_STALE_MS = 3 * 60 * 1000;

function isGenInFlight(nodeId) {
  return !!(nodeId && window.__genInFlight?.has(nodeId));
}
window.isGenInFlight = isGenInFlight;

function shouldRecoverStuckGeneration(item) {
  if (!item?._genPayload) return false;
  const started = Number(item.genStartedAt) || 0;
  if (!started) return true;
  return Date.now() - started > GEN_RECOVER_STALE_MS;
}
let progressTimer = null;
const AUTO_RETRY_MAX = 3;
window.AUTO_RETRY_MAX = AUTO_RETRY_MAX;
const AUTO_RETRY_DELAY_MS = 1800;
const autoRetryTimers = new Map();

function clearAutoRetryTimer(nodeId) {
  const t = autoRetryTimers.get(nodeId);
  if (t) {
    clearTimeout(t);
    autoRetryTimers.delete(nodeId);
  }
  if (window.__browserQueueTimers?.[nodeId]) {
    clearTimeout(window.__browserQueueTimers[nodeId]);
    delete window.__browserQueueTimers[nodeId];
  }
}

function clearAllAutoRetryTimers() {
  autoRetryTimers.forEach((t) => clearTimeout(t));
  autoRetryTimers.clear();
  if (window.__browserQueueTimers) {
    Object.keys(window.__browserQueueTimers).forEach((id) => {
      clearTimeout(window.__browserQueueTimers[id]);
    });
    window.__browserQueueTimers = Object.create(null);
  }
}

/** @returns {object|null} node patch to queue a retry, or null if exhausted/disabled */
function scheduleAutoRetryPatch(nodeId, errorMsg) {
  const node = findNodeAnywhere(nodeId);
  // Retry only the bound job. A lost submission response must never trigger a
  // second generation POST; refresh/reconciliation restores its task binding.
  if (!node?.autoRetry || !node.taskId || autoRetryTimers.has(nodeId)) return null;
  if (
    (typeof isPausedGenerationError === "function" && isPausedGenerationError(errorMsg)) ||
    String(errorMsg || "").includes("已手动暂停")
  ) return null;
  const used = Number(node.autoRetryCount) || 0;
  if (used >= AUTO_RETRY_MAX) return null;
  const attempt = used + 1;
  const taskId = node.taskId;
  const timer = setTimeout(async () => {
    try {
      const cur = findNodeAnywhere(nodeId);
      if (!cur || !cur.autoRetry || cur.taskId !== taskId || cur.status !== "failed") return;
      await api(`/api/tasks/${encodeURIComponent(taskId)}/recheck`, { method: "POST" });
      const latest = findNodeAnywhere(nodeId);
      if (!latest || latest.taskId !== taskId || latest.status !== "failed") return;
      CanvasStore.updateNode(nodeId, {
        status: "generating", error: null, isError: false, promptSent: true,
        autoRetryCount: attempt,
      });
      void CanvasStore.flushSave();
      renderNodes();
      startPolling();
    } catch (err) {
      const latest = findNodeAnywhere(nodeId);
      if (latest?.taskId === taskId && latest.status === "failed") {
        CanvasStore.updateNode(nodeId, {
          error: err.message || "原任务重新检测暂时失败",
          autoRetryCount: [400, 404, 409].includes(err.status) ? AUTO_RETRY_MAX : attempt,
        });
        renderNodes();
      }
    } finally {
      // A cleared/replaced timer belongs to a different operation.
      if (autoRetryTimers.get(nodeId) === timer) autoRetryTimers.delete(nodeId);
    }
  }, AUTO_RETRY_DELAY_MS);
  autoRetryTimers.set(nodeId, timer);
  const failPatch = {
    status: "failed",
    error: `${errorMsg || "查询失败"} · 即将重新检测原任务 (${attempt}/${AUTO_RETRY_MAX})`,
    autoRetryCount: attempt,
  };
  const live = findNodeAnywhere(nodeId);
  return typeof window.withKeepableGenErrorFlag === "function"
    ? window.withKeepableGenErrorFlag(live || node, failPatch)
    : failPatch;
}

window.scheduleAutoRetryPatch = scheduleAutoRetryPatch;

function getUserThreadLimit() {
  if (typeof CanvasPrefs !== "undefined") {
    CanvasPrefs.load();
    const n = CanvasPrefs.maxThreads;
    return Math.min(64, Math.max(1, Number(n) || 16));
  }
  const n = window.__serverMaxThreads ?? 16;
  return Math.min(64, Math.max(1, Number(n) || 16));
}

/** @deprecated use getUserThreadLimit */
function getThreadsPerAccount() {
  return getUserThreadLimit();
}

function getMaxThreads() {
  return getUserThreadLimit();
}
window.getMaxThreads = getMaxThreads;

function countCanvasActiveGenerations() {
  return CanvasStore.boards.reduce(
    (sum, b) =>
      sum +
      b.nodes.filter((n) => n.status === "pending" || n.status === "generating").length,
    0
  );
}

function countCanvasWaitingGenerations() {
  return CanvasStore.boards.reduce(
    (sum, b) => sum + b.nodes.filter((n) => n.status === "waiting").length,
    0
  );
}

function countActiveGenerations() {
  const wb =
    typeof window.countWorkbenchActiveGenerations === "function"
      ? window.countWorkbenchActiveGenerations()
      : 0;
  return countCanvasActiveGenerations() + wb;
}
window.countActiveGenerations = countActiveGenerations;

function countWaitingGenerations() {
  const wb =
    typeof window.countWorkbenchWaitingGenerations === "function"
      ? window.countWorkbenchWaitingGenerations()
      : 0;
  return countCanvasWaitingGenerations() + wb;
}
window.countWaitingGenerations = countWaitingGenerations;

function updateThreadBadge() {
  const max = getMaxThreads();
  const active = countActiveGenerations();
  const waiting = countWaitingGenerations() || genQueue.length;
  const text = waiting ? `线程 ${active}/${max} · 等待 ${waiting}` : `线程 ${active}/${max}`;
  for (const id of ["thread-badge", "workbench-thread-badge", "workspace-thread-badge"]) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = text;
    el.classList.toggle("is-busy", active > 0);
    el.classList.toggle("is-waiting", waiting > 0);
  }
}

function enqueueGeneration(nodeId) {
  if (!genQueue.includes(nodeId)) genQueue.push(nodeId);
  updateThreadBadge();
  ensureProgressTicker();
  pumpGenQueue();
}
window.enqueueGeneration = enqueueGeneration;

function pumpGenQueue() {
  while (genQueue.length && countActiveGenerations() < getMaxThreads()) {
    const nodeId = genQueue.shift();
    if (!reserveGenerationSlot(nodeId)) continue;
    void runGenerateJob(nodeId);
  }
  updateThreadBadge();
}
window.pumpGenQueue = pumpGenQueue;
window.runGenerateJob = runGenerateJob;
window.updateThreadBadge = updateThreadBadge;

window.onGenerationSlotFreed = function onGenerationSlotFreed() {
  pumpGenQueue();
  updateThreadBadge();
};

window.cancelQueuedGenerationsForBoard = function cancelQueuedGenerationsForBoard(boardId) {
  const board = CanvasStore.boards.find((b) => b.id === boardId);
  const ids = new Set((board?.nodes || []).map((n) => n.id));
  for (let i = genQueue.length - 1; i >= 0; i--) {
    if (ids.has(genQueue[i])) genQueue.splice(i, 1);
  }
  updateThreadBadge();
};

window.cancelQueuedGeneration = function cancelQueuedGeneration(nodeId) {
  clearAutoRetryTimer(nodeId);
  for (let i = genQueue.length - 1; i >= 0; i--) {
    if (genQueue[i] === nodeId) genQueue.splice(i, 1);
  }
  const wbHit = findWorkbenchGenTarget(nodeId);
  if (wbHit?.clip?.status === "waiting") {
    wbHit.clip.status = "idle";
    wbHit.clip._genPayload = null;
    wbHit.clip.genStartedAt = null;
    wbHit.clip.taskId = null;
    WorkbenchStore.save();
    if (typeof refreshWorkbenchClipUi === "function") {
      refreshWorkbenchClipUi(wbHit.wb, wbHit.ep, wbHit.clip);
    }
    updateThreadBadge();
    return;
  }
  if (wbHit?.asset?.status === "waiting") {
    wbHit.asset.status = "idle";
    wbHit.asset._genPayload = null;
    wbHit.asset.genStartedAt = null;
    wbHit.asset.taskId = null;
    if (typeof wbRestoreStudioMediaFromHistory === "function") {
      wbRestoreStudioMediaFromHistory(wbHit.asset);
    }
    WorkbenchStore.save?.();
    WorkbenchAssetStudio?.refreshUi?.();
    updateThreadBadge();
    return;
  }
  const node = findNodeAnywhere(nodeId);
  if (node?.status === "waiting") {
    CanvasStore.updateNode(nodeId, {
      status: "idle", _genPayload: null, genStartedAt: null, taskId: null,
      generationStopped: true, stoppedTaskId: node.taskId || node.stoppedTaskId || null,
    });
  }
  updateThreadBadge();
};

async function pauseGeneration(nodeId) {
  if (nodeId?.startsWith("wbclip-") && typeof pauseWorkbenchClipGeneration === "function") {
    return pauseWorkbenchClipGeneration(nodeId);
  }
  if (nodeId?.startsWith("wbasset-") && typeof pauseWorkbenchStudioGeneration === "function") {
    return pauseWorkbenchStudioGeneration(nodeId);
  }
  const node = findNodeAnywhere(nodeId);
  if (!node) return;
  const busy = ["waiting", "pending", "generating"].includes(node.status);
  if (!busy) return;
  // MyApi has no pause UI / pause action.
  if (typeof isPauseEligibleChannel === "function" && !isPauseEligibleChannel(node)) {
    return;
  }

  const isQueued = node.status === "waiting";
  const channel =
    typeof normalizeChannel === "function"
      ? normalizeChannel(node.channel, node.model)
      : node.channel;
  const browserSim =
    (typeof isBytedanceFamilyChannel === "function"
      ? isBytedanceFamilyChannel(channel)
      : channel === "doubao" ||
        channel === "doubao_s" ||
        channel === "dola" ||
        channel === "dola_s");
  const pauseMsg = isQueued
    ? "确认取消排队？节点将停止等待生成。"
    : "确认停止？将清空未发送的草稿并释放账号。\n（消息一旦发到网页后将无法暂停）";
  const ok = await AppDialog.confirm(pauseMsg, {
    title: isQueued ? "取消排队" : "确认停止",
    okText: isQueued ? "取消排队" : "停止",
    cancelText: "继续等待",
  });
  if (!ok) return;

  if (!isQueued && browserSim && node.promptSent) {
    showCanvasToast("消息已发送，无法暂停");
    return;
  }

  clearAutoRetryTimer(nodeId);

  if (isQueued) {
    cancelQueuedGeneration(nodeId);
    renderNodes();
    showCanvasToast("已取消排队");
    return;
  }

  if (node.taskId) {
    try {
      await api(`/api/tasks/${node.taskId}/cancel`, { method: "POST" });
    } catch (err) {
      showCanvasToast(err.message || "暂停请求失败");
      return;
    }
  }

  CanvasStore.updateNode(nodeId, {
    status: "idle",
    error: null,
    isError: false,
    genStartedAt: null,
    taskId: null,
    generationStopped: true,
    stoppedTaskId: node.taskId || node.stoppedTaskId || null,
    _genPayload: null,
  });
  void window.CanvasAuth?.refreshPointsBalance?.();
  renderNodes();
  pumpGenQueue();
  updateThreadBadge();
  showCanvasToast("已暂停");
}

window.pauseGeneration = pauseGeneration;

function findNodeAnywhere(nodeId) {
  if (nodeId?.startsWith("wbclip-") && typeof parseWbClipNodeId === "function" && typeof clipToNode === "function") {
    const hit = window.findWbClipAnywhere?.(parseWbClipNodeId(nodeId));
    if (hit?.clip) return clipToNode(hit.clip);
  }
  if (
    nodeId?.startsWith("wbasset-") &&
    typeof parseWbAssetNodeId === "function" &&
    typeof assetToNode === "function"
  ) {
    const draft = WorkbenchAssetStudio?.draft;
    if (draft && typeof wbAssetNodeId === "function" && wbAssetNodeId(draft.id) === nodeId) {
      return assetToNode(draft);
    }
  }
  for (const b of CanvasStore.boards) {
    const node = b.nodes.find((n) => n.id === nodeId);
    if (node) return node;
  }
  return null;
}

function findWorkbenchGenTarget(nodeId) {
  if (nodeId?.startsWith("wbclip-") && typeof parseWbClipNodeId === "function") {
    return window.findWbClipAnywhere?.(parseWbClipNodeId(nodeId)) || null;
  }
  if (nodeId?.startsWith("wbasset-") && typeof parseWbAssetNodeId === "function") {
    const draft = WorkbenchAssetStudio?.draft;
    if (draft && typeof wbAssetNodeId === "function" && wbAssetNodeId(draft.id) === nodeId) {
      const wb = WorkbenchStore?.activeWorkbench?.();
      return wb ? { wb, asset: draft } : null;
    }
  }
  return null;
}

function reserveGenerationSlot(nodeId) {
  const wbHit = findWorkbenchGenTarget(nodeId);
  if (wbHit?.clip) {
    const clip = wbHit.clip;
    if (clip.status !== "waiting") return false;
    clip.status = "pending";
    clip.genStartedAt = clip.genStartedAt || Date.now();
    clip.error = null;
    WorkbenchStore.save();
    if (typeof refreshWorkbenchClipUi === "function") {
      refreshWorkbenchClipUi(wbHit.wb, wbHit.ep, clip);
    }
    return true;
  }
  if (wbHit?.asset) {
    const asset = wbHit.asset;
    if (asset.status !== "waiting") return false;
    asset.status = "pending";
    asset.genStartedAt = asset.genStartedAt || Date.now();
    asset.error = null;
    WorkbenchAssetStudio?.refreshUi?.();
    return true;
  }
  const node = findNodeAnywhere(nodeId);
  if (!node || node.status !== "waiting") return false;
  CanvasStore.updateNode(nodeId, {
    status: "pending",
    genStartedAt: node.genStartedAt || Date.now(),
    error: null,
  });
  return true;
}

function findBoardForNode(nodeId) {
  for (const b of CanvasStore.boards) {
    if (b.nodes?.some((n) => n.id === nodeId)) return b;
  }
  return null;
}

async function runGenerateJob(nodeId) {
  if (isGenInFlight(nodeId)) {
    pumpGenQueue();
    return;
  }
  window.__genInFlight.add(nodeId);
  try {
  if (nodeId?.startsWith("wbclip-") || nodeId?.startsWith("wbasset-")) {
    if (typeof window.runWorkbenchGenerateJob === "function") {
      await window.runWorkbenchGenerateJob(nodeId);
    } else {
      pumpGenQueue();
    }
    return;
  }
  const node = findNodeAnywhere(nodeId);
  if (!node || !(typeof isGenerativeNode === "function" ? isGenerativeNode(node) : ["video","image","text","voice-text"].includes(node.type))) {
    pumpGenQueue();
    return;
  }

  const payload = node._genPayload || {};
  const typeRaw = payload.type || node.type;
  const type = typeRaw === "voice-text" ? "text" : typeRaw;
  const board = findBoardForNode(nodeId);
  const boardId = board?.id || CanvasStore.activeBoardId;
  const endpoint =
    type === "image"
      ? "/api/image/generate"
      : type === "text"
        ? "/api/text/generate"
        : type === "voice"
          ? "/api/voice/generate"
          : "/api/video/generate";

  const startedAt = node.genStartedAt || Date.now();
  // Keep a single busy preview; avoid pending→generating extra paint when already generating.
  if (node.status !== "generating") {
    CanvasStore.updateNode(nodeId, {
      status: "generating",
      genStartedAt: startedAt,
      error: null,
      isError: false,
    });
    renderGenerationStart(nodeId);
  }
  updateThreadBadge();
  ensureProgressTicker();
  startPolling();

  try {
    const task = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({
        // Prefer resolved fullPrompt (even if ""); never fall back to raw @ref:… prompt.
        prompt: payload.fullPrompt != null ? payload.fullPrompt : node.prompt,
        ratio: payload.ratio || node.ratio || "自动",
        account_id: null,
        board_id: boardId,
        node_id: nodeId,
        reference_images: payload.referenceImages?.length ? payload.referenceImages : null,
        references: payload.references?.length ? payload.references : null,
        model: payload.model || node.model || null,
        duration: type === "video" ? payload.duration ?? node.duration : null,
        resolution:
          type === "video"
            ? payload.resolution ?? node.resolution ?? "自动"
            : type === "image" &&
                ["myapi", "volcengine"].includes(payload.channel || node.channel)
              ? payload.resolution ??
                node.resolution ??
                ((payload.channel || node.channel) === "volcengine" ? "2K" : "默认")
              : null,
        style: type === "image" ? payload.style || node.style || null : null,
        channel: payload.channel || node.channel || null,
        provider:
          typeof accountProviderForChannel === "function"
            ? accountProviderForChannel(payload.channel || node.channel)
            : null,
        use_upgrade:
          !!(payload.useUpgrade ?? node.useUpgrade) ||
          payload.channel === "doubao_s" ||
          node.channel === "doubao_s",
      }),
    });
    // Pause may have cleared the node while /generate was in flight — cancel orphan + skip revive.
    const cur = findNodeAnywhere(nodeId);
    if (!cur || !["pending", "generating"].includes(cur.status)) {
      if (task?.id) {
        try {
          await api(`/api/tasks/${encodeURIComponent(task.id)}/cancel`, { method: "POST" });
        } catch {
          /* best-effort */
        }
      }
      pumpGenQueue();
      updateThreadBadge();
      return;
    }
    CanvasStore.updateNode(nodeId, {
      taskId: task.id,
      status: task.status || "pending",
      _genPayload: null,
      _accountBusyRetries: 0,
    });
    // Persist the durable task link immediately. If the page refreshes after
    // provider submission, recovery can continue polling this exact task.
    void CanvasStore.flushSave();
    // 预扣已发生，立刻刷新顶栏积分
    void window.CanvasAuth?.refreshPointsBalance?.();
    renderGenerationStart(nodeId);
    loadAccounts();
    startPolling();
  } catch (err) {
    if (err.code === "INSUFFICIENT_POINTS" || err.status === 402) {
      const live = findNodeAnywhere(nodeId) || node;
      CanvasStore.updateNode(
        nodeId,
        typeof window.withKeepableGenErrorFlag === "function"
          ? window.withKeepableGenErrorFlag(live, {
              status: "failed",
              error: err.message || "积分不足",
              genStartedAt: null,
              _genPayload: null,
            })
          : {
              status: "failed",
              error: err.message || "积分不足",
              genStartedAt: null,
              _genPayload: null,
            }
      );
      if (typeof showCanvasToast === "function") showCanvasToast(err.message || "积分不足");
      void window.CanvasAuth?.refreshPointsBalance?.();
      renderNodes();
      pumpGenQueue();
      return;
    }
    // Tier / account pool empty — fail once, do not re-queue (avoids 排队闪烁死循环).
    if (
      err.code === "NO_ACCOUNT" ||
      err.code === "NO_REGULAR_ACCOUNT" ||
      err.code === "NO_UPGRADE_ACCOUNT" ||
      err.code === "VIDEO_QUOTA_EXHAUSTED" ||
      (err.status === 503 && /没有可用|改选 Seed|升级套餐|普通账号|NO_ACCOUNT|接单失败/.test(err.message || ""))
    ) {
      clearAutoRetryTimer(nodeId);
      const live = findNodeAnywhere(nodeId) || node;
      CanvasStore.updateNode(
        nodeId,
        typeof window.withKeepableGenErrorFlag === "function"
          ? window.withKeepableGenErrorFlag(live, {
              status: "failed",
              error: err.message || "没有可用账号",
              genStartedAt: null,
              _genPayload: null,
              taskId: null,
            })
          : {
              status: "failed",
              error: err.message || "没有可用账号",
              genStartedAt: null,
              _genPayload: null,
              taskId: null,
            }
      );
      if (typeof showCanvasToast === "function") showCanvasToast(err.message || "没有可用账号");
      renderNodes();
      pumpGenQueue();
      return;
    }
    // Global browser cap / per-user parallel full — re-queue as waiting (no fail flash).
    if (
      err.code === "BROWSERS_FULL" ||
      err.code === "ACCOUNT_BUSY" ||
      (err.status === 503 &&
        /都在生成|ACCOUNT_BUSY|BROWSERS_FULL|并行已满|浏览器窗口|浏览器空闲|浏览器已达上限/.test(
          err.message || ""
        ))
    ) {
      const retries = (Number(node._browserQueueRetries) || 0) + 1;
      const delay = Math.min(12000, 2500 + retries * 400);
      CanvasStore.updateNode(nodeId, {
        status: "waiting",
        error: err.code === "BROWSERS_FULL" || /浏览器已达上限|浏览器空闲/.test(err.message || "")
          ? "排队中（等待浏览器空闲）"
          : "排队中（等待并行空闲）",
        genStartedAt: null,
        _browserQueueRetries: retries,
        taskId: null,
        // keep _genPayload for retry
      });
      renderNodes();
      updateThreadBadge();
      pumpGenQueue();
      if (!window.__browserQueueTimers) window.__browserQueueTimers = Object.create(null);
      clearTimeout(window.__browserQueueTimers[nodeId]);
      window.__browserQueueTimers[nodeId] = setTimeout(() => {
        delete window.__browserQueueTimers[nodeId];
        const n = findNodeAnywhere(nodeId);
        if (n?.status === "waiting" && n._genPayload) {
          enqueueGeneration(nodeId);
        }
      }, delay);
      return;
    }
    // Do not auto-retry permanent worker reject / 503 noise.
    if (err.status === 503) {
      clearAutoRetryTimer(nodeId);
      const live = findNodeAnywhere(nodeId) || node;
      CanvasStore.updateNode(
        nodeId,
        typeof window.withKeepableGenErrorFlag === "function"
          ? window.withKeepableGenErrorFlag(live, {
              status: "failed",
              error: err.message || "任务服务繁忙，请稍后重试",
              genStartedAt: null,
              _genPayload: null,
              taskId: null,
            })
          : {
              status: "failed",
              error: err.message || "任务服务繁忙，请稍后重试",
              genStartedAt: null,
              _genPayload: null,
              taskId: null,
            }
      );
      if (typeof showCanvasToast === "function") showCanvasToast(err.message || "任务服务繁忙");
      renderNodes();
      pumpGenQueue();
      return;
    }
    // Recoverable node lock: attach the old task and recheck instead of a dead-end fail.
    if (err.status === 409 && err.taskId) {
      clearAutoRetryTimer(nodeId);
      const live = findNodeAnywhere(nodeId) || node;
      CanvasStore.updateNode(
        nodeId,
        typeof window.withKeepableGenErrorFlag === "function"
          ? window.withKeepableGenErrorFlag(live, {
              taskId: err.taskId,
              status: "failed",
              error: err.message || "原任务尚未确认结束",
              genStartedAt: null,
              autoRetry: true,
              autoRetryCount: 0,
              _genPayload: null,
            })
          : {
              taskId: err.taskId,
              status: "failed",
              error: err.message || "原任务尚未确认结束",
              genStartedAt: null,
              autoRetry: true,
              autoRetryCount: 0,
              _genPayload: null,
            }
      );
      void CanvasStore.flushSave();
      renderNodes();
      const retryPatch = scheduleAutoRetryPatch(nodeId, err.message);
      if (retryPatch) CanvasStore.updateNode(nodeId, retryPatch);
      else if (typeof showCanvasToast === "function") {
        showCanvasToast("请打开生成历史，对原任务点「重新检测」");
      }
      pumpGenQueue();
      return;
    }
    const retryPatch = scheduleAutoRetryPatch(nodeId, err.message);
    if (retryPatch) {
      CanvasStore.updateNode(nodeId, retryPatch);
    } else {
      const cur = findNodeAnywhere(nodeId);
      const used = Number(cur?.autoRetryCount) || 0;
      const suffix =
        cur?.autoRetry && used >= AUTO_RETRY_MAX
          ? `（已自动重试 ${AUTO_RETRY_MAX} 次）`
          : "";
      const failPatch = {
        status: "failed",
        error: `${err.message || "生成失败"}${suffix}`,
        genStartedAt: null,
        lastGenMs: cur?.genStartedAt ? Math.max(0, Date.now() - cur.genStartedAt) : cur?.lastGenMs ?? null,
        _genPayload: null,
      };
      CanvasStore.updateNode(
        nodeId,
        typeof window.withKeepableGenErrorFlag === "function"
          ? window.withKeepableGenErrorFlag(cur || node, failPatch)
          : failPatch
      );
    }
    renderNodes();
    pumpGenQueue();
  }
  updateThreadBadge();
  } finally {
    window.__genInFlight.delete(nodeId);
  }
}

function rescheduleCanvasSyncIfNeeded() {
  if (typeof CanvasStore === "undefined" || typeof CanvasStore._rescheduleSync !== "function") return;
  const busy = typeof window.hasActiveGenerationNodes === "function" && window.hasActiveGenerationNodes();
  if (rescheduleCanvasSyncIfNeeded._lastBusy === busy) return;
  rescheduleCanvasSyncIfNeeded._lastBusy = busy;
  CanvasStore._rescheduleSync();
}

function ensureProgressTicker() {
  if (progressTimer) return;
  progressTimer = setInterval(() => {
    if (typeof window.tickGenProgressUI === "function") window.tickGenProgressUI();
    if (typeof window.wbTickGenProgress === "function") window.wbTickGenProgress();
    const busy =
      countActiveGenerations() > 0 ||
      countWaitingGenerations() > 0 ||
      genQueue.length > 0 ||
      (typeof MediaTools !== "undefined" && MediaTools.busy) ||
      (typeof MediaTools !== "undefined" && MediaTools._upscalePollers?.size > 0);
    if (typeof window.reconcileStuckUpscaleNodesThrottled === "function") {
      void window.reconcileStuckUpscaleNodesThrottled();
    }
    rescheduleCanvasSyncIfNeeded();
    // Keep one extra tick after jobs finish so stale「正在生成」DOM can be healed
    if (!busy) {
      if (!ensureProgressTicker._idleOnce) {
        ensureProgressTicker._idleOnce = true;
        updateThreadBadge();
        return;
      }
      clearInterval(progressTimer);
      progressTimer = null;
      ensureProgressTicker._idleOnce = false;
      updateThreadBadge();
      return;
    }
    ensureProgressTicker._idleOnce = false;
    updateThreadBadge();
  }, 1000);
}
window.ensureProgressTicker = ensureProgressTicker;

async function loadTasks() {
  if (!window.CanvasAuth?.isLoggedIn?.()) return;
  const { tasks } = await api("/api/tasks");
  tasks.forEach((t) => syncNodeFromTask(t));
  if (typeof window.syncWorkbenchDraftFromTasks === "function") {
    window.syncWorkbenchDraftFromTasks(tasks);
  }
  reconcileStuckGenerationNodes(tasks);
  if (typeof window.reconcileStuckUpscaleNodes === "function") {
    await window.reconcileStuckUpscaleNodes();
  }
  ensureTaskPolling(tasks);
  pumpGenQueue();
  updateThreadBadge();
}

function reconcileStuckGenerationNodes(tasks) {
  if (!window.CanvasStore) return;
  const byId = new Map((tasks || []).map((t) => [t.id, t]));
  const IMAGE_STUCK_MS = 10 * 60 * 1000;
  const VIDEO_STUCK_MS = 60 * 60 * 1000;
  const recoverIds = [];
  let changed = false;
  CanvasStore.boards.forEach((b) => {
    b.nodes.forEach((n) => {
      if (n.type !== "image" && n.type !== "video") return;
      const busy = ["waiting", "pending", "generating"].includes(n.status);
      if (!busy) return;

      if (n.taskId) {
        const task = byId.get(n.taskId);
        if (task && (task.status === "completed" || task.status === "failed")) {
          // syncNodeFromTask should have applied; ensure unlock
          if (n.status !== task.status) {
            syncNodeFromTask(task);
            changed = true;
          }
          return;
        }
        // The backend task is authoritative. A slow provider must never be
        // changed to failed locally while the server still reports it active.
        if (task && (task.status === "pending" || task.status === "generating")) return;
      }

      const started = Number(n.genStartedAt) || 0;
      const limit = n.type === "video" ? VIDEO_STUCK_MS : IMAGE_STUCK_MS;
      if (started > 0 && Date.now() - started >= limit) {
        const failPatch = {
          status: "failed",
          error: "生成超时未返回结果，请在生成记录中重新检测或重新生成",
          genStartedAt: null,
          lastGenMs: Math.max(0, Date.now() - started),
          _genPayload: null,
        };
        Object.assign(
          n,
          typeof window.withKeepableGenErrorFlag === "function"
            ? window.withKeepableGenErrorFlag(n, failPatch)
            : failPatch
        );
        changed = true;
        return;
      }

      // A paged/temporarily incomplete task list is not proof that the task
      // stopped. Keep its state until the configured generation ceiling.
      if (n.taskId) {
        if (!started) {
          n.genStartedAt = Date.now();
          changed = true;
        }
        return;
      }

      // No taskId yet.  During a live POST, __genInFlight owns the node.  After
      // refresh, a successful /api/tasks response is authoritative: if no active
      // row rebound to this node, safely put the saved payload back in the local
      // queue.  The backend node lock still guarantees exactly one task if the
      // original POST and this recovery race each other.
      if (n._genPayload) {
        if (isGenInFlight(n.id)) return;
        if (n.status !== "waiting") {
          n.status = "waiting";
          n.error = null;
          n.genStartedAt = null;
          changed = true;
        }
        recoverIds.push(n.id);
        return;
      }
      Object.assign(n, {
        status: "idle",
        error: null,
        genStartedAt: null,
        _genPayload: null,
      });
      changed = true;
    });
  });
  if (changed) {
    CanvasStore.save();
    if (typeof renderNodes === "function") renderNodes();
  }
  recoverIds.forEach((nodeId) => enqueueGeneration(nodeId));
}

function hasActiveGenerationNodes() {
  const canvasBusy = CanvasStore.boards.some((b) =>
    b.nodes.some(
      (n) =>
        n.status === "waiting" ||
        ((n.status === "pending" || n.status === "generating") && n.taskId)
    )
  );
  const wbBusy =
    typeof window.hasWorkbenchActiveGeneration === "function" && window.hasWorkbenchActiveGeneration();
  return canvasBusy || wbBusy;
}
window.hasActiveGenerationNodes = hasActiveGenerationNodes;

function ensureTaskPolling(tasks) {
  const list = tasks || [];
  const tasksActive = list.some((t) => t.status === "pending" || t.status === "generating");
  if (tasksActive || hasActiveGenerationNodes()) {
    startPolling();
    ensureProgressTicker();
  }
}

function getTaskPollDelayMs(tasks) {
  const list = tasks || [];
  if (list.some((t) => t.status === "pending")) return 2500;
  const generating = list.filter((t) => t.status === "generating");
  if (!generating.length) return 3000;
  const hasVideo = generating.some((t) => t.kind === "video");
  return hasVideo ? 6500 : 4000;
}

function clearTaskPolling() {
  pollGeneration++;
  if (pollTimer != null) clearTimeout(pollTimer);
  pollTimer = null;
}

function isTaskPollingActive() {
  return pollTimer != null;
}
window.isTaskPollingActive = isTaskPollingActive;

async function syncAllNodesFromTasks() {
  if (!window.CanvasAuth?.isLoggedIn?.()) return [];
  try {
    const { tasks } = await api("/api/tasks");
    tasks.forEach((t) => syncNodeFromTask(t));
    if (typeof window.syncWorkbenchDraftFromTasks === "function") {
      window.syncWorkbenchDraftFromTasks(tasks);
    }
    reconcileStuckGenerationNodes(tasks);
    ensureTaskPolling(tasks);
    if (hasActiveGenerationNodes()) ensureProgressTicker();
    updateThreadBadge();
    return tasks;
  } catch {
    return [];
  }
}

function startPolling() {
  if (!window.CanvasAuth?.isLoggedIn?.()) return;
  ensureProgressTicker();
  if (pollTimer != null) return;
  const generation = ++pollGeneration;
  // Reserve the loop before its first HTTP request resolves. Multiple startup
  // and generation callbacks must share one polling loop even on slow networks.
  pollTimer = -1;
  rescheduleCanvasSyncIfNeeded();
  const poll = async () => {
    if (generation !== pollGeneration) return;
    let tasks = [];
    try {
      const res = await api("/api/tasks");
      if (generation !== pollGeneration) return;
      tasks = res.tasks || [];
      let changed = false;
      tasks.forEach((t) => {
        if (syncNodeFromTask(t)) changed = true;
      });
      if (typeof window.syncWorkbenchDraftFromTasks === "function") {
        window.syncWorkbenchDraftFromTasks(tasks);
      }
      // Unlock nodes stuck as generating when task already finished / gone
      reconcileStuckGenerationNodes(tasks);
      const tasksActive = tasks.some((t) => t.status === "pending" || t.status === "generating");
      if (tasksActive && Date.now() - (startPolling._lastAccountLoad || 0) > 30000) {
        startPolling._lastAccountLoad = Date.now();
        await loadAccounts();
      }
      if (!tasksActive && !hasActiveGenerationNodes()) {
        clearTaskPolling();
        rescheduleCanvasSyncIfNeeded();
        updateThreadBadge();
        return;
      }
      if (changed) updateThreadBadge();
    } catch {
      /* ignore */
    }
    if (generation === pollGeneration) {
      pollTimer = setTimeout(poll, getTaskPollDelayMs(tasks));
    }
  };
  poll();
}
window.startPolling = startPolling;

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}

// Shared by the standalone workbench, which has no canvas node layer.
document.getElementById("nodes-layer")?.addEventListener("click", async (e) => {
  const assetPick = e.target.closest("[data-asset-pick]");
  if (assetPick) {
    e.stopPropagation();
    e.preventDefault();
    const hostId = assetPick.dataset.assetPick;
    if (hostId && typeof AssetPicker !== "undefined") AssetPicker.open(hostId);
    return;
  }

  const gen = e.target.closest(".btn-generate");
  if (gen) {
    e.stopPropagation();
    void generateForNode(gen.dataset.node).catch((err) => {
      if (err?.name !== "AbortError") showCanvasToast(err?.message || "生成准备失败，请重试");
    });
    return;
  }

  const retryToggle = e.target.closest(".gen-auto-retry");
  if (retryToggle) {
    e.stopPropagation();
    e.preventDefault();
    const nodeId = retryToggle.dataset.node;
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const next = !node.autoRetry;
    if (!next) clearAutoRetryTimer(nodeId);
    CanvasStore.updateNode(nodeId, { autoRetry: next, autoRetryCount: next ? node.autoRetryCount || 0 : 0 });
    CanvasStore.save();
    if (typeof window.patchGenActionBtnsUI === "function") {
      window.patchGenActionBtnsUI(CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId));
    }
    if (typeof showCanvasToast === "function") {
      showCanvasToast(next ? "已开启失败自动重试（最多 3 次）" : "已关闭失败自动重试");
    }
    return;
  }

  const pauseBtn = e.target.closest("[data-gen-pause]");
  if (pauseBtn) {
    e.stopPropagation();
    e.preventDefault();
    void pauseGeneration(pauseBtn.dataset.genPause);
    return;
  }

  const paidQuotaBtn = e.target.closest("[data-task-user-action]");
  if (paidQuotaBtn?.dataset?.taskUserAction) {
    e.stopPropagation();
    e.preventDefault();
    if (!paidQuotaBtn.dataset.taskId) {
      if (typeof showCanvasToast === "function") showCanvasToast("任务未绑定，无法确认，请重新生成");
      return;
    }
    if (typeof window.submitTaskUserAction === "function") {
      void window.submitTaskUserAction(paidQuotaBtn.dataset.taskId, paidQuotaBtn.dataset.taskUserAction, {
        nodeId: paidQuotaBtn.dataset.node,
      });
    } else if (typeof showCanvasToast === "function") {
      showCanvasToast("确认组件未就绪，请刷新页面后重试");
    }
    return;
  }

  const channelBtn = e.target.closest(".channel-btn");
  if (channelBtn) {
    e.stopPropagation();
    e.preventDefault();
    const nodeId = channelBtn.dataset.node;
    const menu = document.querySelector(`.channel-menu[data-node="${nodeId}"]`);
    // is-static = only one channel (no menu). Still swallow the click so it doesn't drag the node.
    if (!menu || channelBtn.classList.contains("is-static")) return;
    const willOpen = menu.classList.contains("hidden");
    if (willOpen) {
      closeVideoPickers?.(menu);
      if (typeof showPillPicker === "function") showPillPicker(menu);
      else menu.classList.remove("hidden");
    } else if (typeof hidePillPicker === "function") hidePillPicker(menu);
    else menu.classList.add("hidden");
    return;
  }

  const videoModeTab = e.target.closest(".video-mode-tab[data-video-mode]");
  if (videoModeTab) {
    e.stopPropagation();
    const nodeId = videoModeTab.dataset.node;
    const nextMode =
      typeof normalizeVideoGenMode === "function"
        ? normalizeVideoGenMode(videoModeTab.dataset.videoMode)
        : String(videoModeTab.dataset.videoMode || "omni");
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node || node.type !== "video") return;
    const prevMode =
      typeof getNodeVideoGenMode === "function" ? getNodeVideoGenMode(node) : node.videoMode || "omni";
    if (prevMode === nextMode) return;
    const patch = { videoMode: nextMode };
    const probe = { ...node, videoMode: nextMode };
    const models =
      typeof videoModelsForNode === "function"
        ? videoModelsForNode(probe)
        : typeof videoModelsForChannel === "function"
          ? videoModelsForChannel(normalizeChannel(node.channel, node.model))
          : [];
    if (models.length && !models.some((m) => m.id === node.model)) {
      patch.model = models[0].id;
      const ch = normalizeChannel(node.channel, patch.model);
      if (typeof clampVideoDuration === "function") {
        patch.duration = clampVideoDuration(
          node.duration ?? 10,
          patch.model,
          ch === "doubao_s",
          ch
        );
      }
      if (typeof normalizeVideoSettingsForModel === "function") {
        Object.assign(patch, normalizeVideoSettingsForModel({ ...node, ...patch }, patch.model));
      }
    } else if (
      typeof videoModeRequiresRefVideo === "function" &&
      videoModeRequiresRefVideo(nextMode) &&
      !models.length
    ) {
      if (typeof showCanvasToast === "function") {
        showCanvasToast("当前渠道没有支持参考视频的模型，请切换渠道或配置 MyApi");
      }
    }
    CanvasStore.updateNode(nodeId, patch);
    if (typeof renderNodes === "function") renderNodes({ immediate: true });
    return;
  }

  const audioModeTab = e.target.closest(".audio-mode-tab[data-audio-mode]");
  if (audioModeTab) {
    e.stopPropagation();
    const nodeId = audioModeTab.dataset.node;
    const nextMode =
      typeof normalizeAudioAppMode === "function"
        ? normalizeAudioAppMode(audioModeTab.dataset.audioMode)
        : String(audioModeTab.dataset.audioMode || "tts");
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node || node.type !== "voice") return;
    const prev =
      typeof getNodeAudioAppMode === "function" ? getNodeAudioAppMode(node) : node.audioMode || "tts";
    if (prev === nextMode) return;
    if (prev === "record" && typeof AudioRecordSession !== "undefined" && AudioRecordSession.isRecording(nodeId)) {
      void AudioRecordSession.stop(nodeId);
    }
    CanvasStore.updateNode(nodeId, { audioMode: nextMode });
    if (typeof renderNodes === "function") renderNodes({ immediate: true });
    return;
  }

  const recordBtn = e.target.closest("[data-action='audio-record-toggle']");
  if (recordBtn) {
    e.stopPropagation();
    const nodeId = recordBtn.dataset.node;
    if (nodeId && typeof AudioRecordSession !== "undefined") {
      void AudioRecordSession.toggle(nodeId);
    }
    return;
  }

  const lyricsAiBtn = e.target.closest("[data-action='audio-lyrics-hint']");
  if (lyricsAiBtn) {
    e.stopPropagation();
    const nodeId = lyricsAiBtn.dataset.node;
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node || node.type !== "voice") return;
    const style = String(node.prompt || "").trim();
    if (!style) {
      showCanvasToast?.("请先在上方填写风格描述，再生成歌词草稿");
      return;
    }
    const draft = `[Verse 1]\n（根据风格「${style.slice(0, 80)}」填写主歌）\n\n[Chorus]\n（填写副歌，可重复）\n\n[Bridge]\n（可选桥段）`;
    CanvasStore.updateNode(nodeId, { lyrics: draft });
    if (typeof renderNodes === "function") renderNodes({ immediate: true });
    showCanvasToast?.("已写入歌词草稿，请自行改写");
    return;
  }

  const channelOpt = e.target.closest(".channel-option");
  if (channelOpt) {
    e.stopPropagation();
    const nodeId = channelOpt.dataset.node;
    const raw = channelOpt.dataset.channel;
    const channel =
      raw === "myapi"
        ? "myapi"
        : raw === "bendi"
          ? "bendi"
          : raw === "volcengine"
            ? "volcengine"
          : raw === "doubao_s"
            ? "doubao_s"
            : raw === "dola" || raw === "dola_s"
              ? "dola"
              : "doubao";
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node) return;
    if ((node.type === "text" || node.type === "voice-text") && channel === "bendi") {
      if (!(typeof isBendiTextAllowed === "function" && isBendiTextAllowed())) {
        if (typeof showCanvasToast === "function") {
          showCanvasToast("BenDi 文本需在设置中切换到「本地」后端");
        }
        if (typeof hidePillPicker === "function") hidePillPicker(document.querySelector(`.channel-menu[data-node="${nodeId}"]`));
        else document.querySelector(`.channel-menu[data-node="${nodeId}"]`)?.classList.add("hidden");
        return;
      }
    }
    if (channel === "bendi" && typeof isBendiChannelEnabled === "function" && !isBendiChannelEnabled()) {
      if (typeof showCanvasToast === "function") showCanvasToast("BenDi 仅限管理员使用");
      return;
    }
    if (channel === "volcengine" && typeof isVolcengineChannelAvailable === "function" && !isVolcengineChannelAvailable(node.type)) {
      if (typeof showCanvasToast === "function") showCanvasToast("请先在设置中开启火山引擎对应类型");
      return;
    }
    if (channel === "doubao_s" && !onlineDoubaoUpgradeAccounts("doubao").length) {
      if (typeof showCanvasToast === "function") {
        showCanvasToast("当前没有在线升级套餐账号，Seed S 可能无法生成（可先检测/登录）");
      }
    }
    const patch = { channel };
    if (channel === "doubao_s") patch.useUpgrade = true;
    else if (channel === "doubao" || channel === "dola" || channel === "bendi" || channel === "volcengine") patch.useUpgrade = false;
    if (node.type === "image") {
      patch.model = defaultModelForChannel(channel, "image");
      if (!isBytedanceFamilyChannel(channel)) patch.style = null;
      if (channel === "myapi") {
        patch.resolution = node.resolution || "默认";
        if (!["默认", "1K", "2K", "4K"].includes(patch.resolution)) patch.resolution = "默认";
      } else if (channel === "volcengine") {
        patch.resolution = node.resolution || "2K";
        if (!["1K", "2K", "3K", "4K"].includes(patch.resolution)) patch.resolution = "2K";
      } else {
        patch.resolution = null;
      }
      {
        const ratios =
          typeof imageRatiosForModel === "function"
            ? imageRatiosForModel(patch.model)
            : null;
        if (ratios?.length && (!node.ratio || !ratios.includes(node.ratio) || node.ratio === "自动")) {
          patch.ratio =
            typeof defaultImageRatioForModel === "function"
              ? defaultImageRatioForModel(patch.model)
              : ratios.includes("16:9")
                ? "16:9"
                : ratios.includes("自动")
                  ? "自动"
                  : ratios[0];
        }
      }
    } else if (node.type === "video") {
      const probe = { ...node, channel, videoMode: node.videoMode || "omni" };
      const models =
        typeof videoModelsForNode === "function"
          ? videoModelsForNode(probe)
          : typeof videoModelsForChannel === "function"
            ? videoModelsForChannel(channel)
            : [];
      // BenDi：管理员默认始终 2.5，不沿用节点上旧的 2.0 / 其它渠道模型。
      if (channel === "bendi") {
        patch.model =
          (typeof defaultModelForChannel === "function" && defaultModelForChannel("bendi", "video")) ||
          models.find((m) => /2\.5|2_5/.test(String(m?.id || "")))?.id ||
          models[0]?.id ||
          "seedance-2.5";
      } else {
        const keep = models.some((m) => m.id === node.model) ? node.model : null;
        patch.model =
          keep ||
          models[0]?.id ||
          defaultModelForChannel(channel, "video") ||
          "seedance_v2.0_fast";
      }
      patch.duration = clampVideoDuration(
        node.duration ?? 10,
        patch.model,
        channel === "doubao_s",
        channel
      );
      if (typeof normalizeVideoSettingsForModel === "function") {
        Object.assign(patch, normalizeVideoSettingsForModel({ ...node, ...patch }, patch.model));
      }
      if (
        typeof prefersMaxVideoDefaults === "function" &&
        prefersMaxVideoDefaults(channel, patch.model)
      ) {
        if (typeof defaultVideoDurationForModel === "function") {
          patch.duration = defaultVideoDurationForModel(
            patch.model,
            channel === "doubao_s",
            channel
          );
        }
        if (typeof defaultVideoRatioForChannel === "function") {
          patch.ratio = defaultVideoRatioForChannel(channel, patch.model);
        }
      }
    } else if (typeof isTextGenNode === "function" ? isTextGenNode(node) : node.type === "text" || node.type === "voice-text") {
      patch.model = defaultModelForChannel(channel, "text");
    } else if (node.type === "voice") {
      patch.model = defaultModelForChannel(channel, "voice");
    }
    if ((channel === "myapi" || channel === "volcengine") && !patch.model) {
      if (typeof showCanvasToast === "function") {
        showCanvasToast(channel === "volcengine" ? "请选择火山引擎模型" : "请先在设置中添加 MyApi 模型");
      }
    }
    CanvasStore.updateNode(nodeId, patch);
    const updated = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (typeof refreshNodeMediaToolbar === "function" && updated) {
      refreshNodeMediaToolbar(updated);
    } else if (typeof renderNodes === "function") {
      renderNodes({ immediate: true });
    }
    // Ensure menu closed (toolbar refresh remounts it hidden anyway)
    if (typeof hidePillPicker === "function") hidePillPicker(document.querySelector(`.channel-menu[data-node="${nodeId}"]`));
    else document.querySelector(`.channel-menu[data-node="${nodeId}"]`)?.classList.add("hidden");
    return;
  }

  const modelBtn = e.target.closest(".video-model-btn");
  if (modelBtn) {
    e.stopPropagation();
    const nodeId = modelBtn.dataset.node;
    const menu = document.querySelector(`.video-model-menu[data-node="${nodeId}"]`);
    if (menu) {
      const willOpen = menu.classList.contains("hidden");
      if (willOpen) {
        closeVideoPickers?.(menu);
        if (typeof showPillPicker === "function") showPillPicker(menu);
        else menu.classList.remove("hidden");
      } else if (typeof hidePillPicker === "function") hidePillPicker(menu);
      else menu.classList.add("hidden");
    }
    return;
  }

  const modelOpt = e.target.closest(".video-model-option");
  if (modelOpt) {
    e.stopPropagation();
    const nodeId = modelOpt.dataset.node;
    const nextModel = modelOpt.dataset.model;
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    const prevGpt = typeof isGptImageModel === "function" && isGptImageModel(node?.model);
    const nextGpt = typeof isGptImageModel === "function" && isGptImageModel(nextModel);
    const patch = { model: nextModel };
    if (nextGpt) patch.style = null;
    if (node?.type === "video") {
      const ch = normalizeChannel(node.channel, nextModel);
      if (!isUpgradeDoubaoVideoModel(nextModel) && ch !== "doubao_s") patch.useUpgrade = false;
      if (ch === "doubao_s") patch.useUpgrade = true;
      const useUpgrade =
        ch === "doubao_s" ||
        (!!(patch.useUpgrade ?? node.useUpgrade) && isUpgradeDoubaoVideoModel(nextModel));
      patch.duration = clampVideoDuration(node.duration ?? 10, nextModel, useUpgrade, ch);
      if (typeof normalizeVideoSettingsForModel === "function") {
        Object.assign(patch, normalizeVideoSettingsForModel({ ...node, ...patch }, nextModel));
      }
      if (typeof prefersMaxVideoDefaults === "function" && prefersMaxVideoDefaults(ch, nextModel)) {
        if (typeof defaultVideoDurationForModel === "function") {
          patch.duration = defaultVideoDurationForModel(nextModel, useUpgrade, ch);
        }
        if (typeof defaultVideoRatioForChannel === "function") {
          patch.ratio = defaultVideoRatioForChannel(ch, nextModel);
        }
      }
      CanvasStore.updateNode(nodeId, patch);
      // Prefer toolbar-only refresh — full renderNodes remounts the card and flashes the canvas.
      if (typeof refreshNodeMediaToolbar === "function") {
        const updated = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
        if (updated) refreshNodeMediaToolbar(updated);
      } else if (typeof refreshVideoPickerLabels === "function") {
        refreshVideoPickerLabels(nodeId);
      }
      const menu = document.querySelector(`.video-model-menu[data-node="${nodeId}"]`);
      if (menu) {
        if (typeof hidePillPicker === "function") hidePillPicker(menu);
        else menu.classList.add("hidden");
      }
      return;
    }
    if (node?.type === "image") {
      const ratios =
        typeof imageRatiosForModel === "function" ? imageRatiosForModel(nextModel) : null;
      if (ratios?.length && (!node.ratio || !ratios.includes(node.ratio) || node.ratio === "自动")) {
        patch.ratio =
          typeof defaultImageRatioForModel === "function"
            ? defaultImageRatioForModel(nextModel)
            : ratios.includes("16:9")
              ? "16:9"
              : ratios.includes("自动")
                ? "自动"
                : ratios[0];
      }
    }
    CanvasStore.updateNode(nodeId, patch);
    if (
      node?.type === "image" ||
      (typeof isTextGenNode === "function"
        ? isTextGenNode(node)
        : node?.type === "text" || node?.type === "voice-text")
    ) {
      if (typeof refreshNodeMediaToolbar === "function") {
        const updated = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
        if (updated) refreshNodeMediaToolbar(updated);
        else if (typeof refreshVideoPickerLabels === "function") refreshVideoPickerLabels(nodeId);
      } else if (typeof refreshVideoPickerLabels === "function") {
        refreshVideoPickerLabels(nodeId);
      }
    } else if (prevGpt !== nextGpt && typeof refreshNodeMediaToolbar === "function") {
      const updated = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
      if (updated) refreshNodeMediaToolbar(updated);
      else if (typeof renderNodes === "function") renderNodes();
    } else if (typeof refreshVideoPickerLabels === "function") {
      refreshVideoPickerLabels(nodeId);
    } else if (typeof updateGenCostBadge === "function") {
      updateGenCostBadge(nodeId, { modelId: nextModel });
    }
    const menu = document.querySelector(`.video-model-menu[data-node="${nodeId}"]`);
    if (menu) {
      if (typeof hidePillPicker === "function") hidePillPicker(menu);
      else menu.classList.add("hidden");
    }
    return;
  }

  const settingsBtn = e.target.closest(".video-settings-btn");
  if (settingsBtn) {
    e.stopPropagation();
    const nodeId = settingsBtn.dataset.node;
    const pop = document.querySelector(`.video-settings-pop[data-node="${nodeId}"]`);
    if (pop) {
      const willOpen = pop.classList.contains("hidden");
      if (willOpen) {
        closeVideoPickers?.(pop);
        if (typeof showPillPicker === "function") showPillPicker(pop);
        else pop.classList.remove("hidden");
      } else if (typeof hidePillPicker === "function") hidePillPicker(pop);
      else pop.classList.add("hidden");
    }
    return;
  }

  const styleBtn = e.target.closest(".image-style-btn");
  if (styleBtn) {
    e.stopPropagation();
    const nodeId = styleBtn.dataset.node;
    const menu = document.querySelector(`.image-style-menu[data-node="${nodeId}"]`);
    if (menu) {
      const willOpen = menu.classList.contains("hidden");
      if (willOpen) {
        closeVideoPickers?.(menu);
        if (typeof showPillPicker === "function") showPillPicker(menu);
        else menu.classList.remove("hidden");
      } else if (typeof hidePillPicker === "function") hidePillPicker(menu);
      else menu.classList.add("hidden");
    }
    return;
  }

  const styleOpt = e.target.closest(".image-style-option");
  if (styleOpt) {
    e.stopPropagation();
    const nodeId = styleOpt.dataset.node;
    CanvasStore.updateNode(nodeId, { style: styleOpt.dataset.style || null });
    if (typeof refreshVideoPickerLabels === "function") refreshVideoPickerLabels(nodeId);
    const menu = document.querySelector(`.image-style-menu[data-node="${nodeId}"]`);
    if (menu) {
      if (typeof hidePillPicker === "function") hidePillPicker(menu);
      else menu.classList.add("hidden");
    }
    return;
  }

  const prefixOpt = e.target.closest(".prefix-option");
  if (prefixOpt) {
    e.stopPropagation();
    const nodeId = prefixOpt.dataset.node;
    if (nodeId?.startsWith("wbclip-") && typeof patchWbClip === "function") {
      patchWbClip(parseWbClipNodeId(nodeId), { promptPrefixId: prefixOpt.dataset.prefix || "" });
    } else if (nodeId?.startsWith("wbasset-") && typeof WorkbenchAssetStudio !== "undefined") {
      const itemId = typeof parseWbAssetNodeId === "function" ? parseWbAssetNodeId(nodeId) : null;
      const item =
        (itemId && typeof findWbStudioItem === "function" && findWbStudioItem(itemId)) ||
        WorkbenchAssetStudio.items?.().find((i) => i.id === itemId) ||
        null;
      if (item) {
        item.promptPrefixId = prefixOpt.dataset.prefix || "";
        if (typeof refreshWbStudioPickerLabels === "function") refreshWbStudioPickerLabels(item);
        else WorkbenchAssetStudio.renderToolbar?.(item);
        WorkbenchStore.save?.();
      }
    } else {
      CanvasStore.updateNode(nodeId, { promptPrefixId: prefixOpt.dataset.prefix ?? "" });
      if (typeof refreshVideoPickerLabels === "function") refreshVideoPickerLabels(nodeId);
    }
    const menu =
      prefixOpt.closest(".prefix-picker")?.querySelector(".prefix-menu") ||
      prefixOpt.closest(".prefix-menu") ||
      document.querySelector(`.prefix-menu[data-node="${nodeId}"]`);
    if (menu) {
      if (typeof hidePillPicker === "function") hidePillPicker(menu);
      else menu.classList.add("hidden");
    }
    if (typeof syncPromptExpandPrefixPicker === "function" && PromptExpand?.isOpen?.() && PromptExpand.nodeId === nodeId) {
      syncPromptExpandPrefixPicker(nodeId);
    }
    return;
  }

  const prefixBtn = e.target.closest(".prefix-btn");
  if (prefixBtn) {
    e.stopPropagation();
    const nodeId = prefixBtn.dataset.node;
    // Prefer the picker that was clicked — card + 快速添加 can both exist for one node.
    const menu =
      prefixBtn.closest(".prefix-picker")?.querySelector(".prefix-menu") ||
      document.querySelector(`.prefix-menu[data-node="${nodeId}"]`);
    if (menu) {
      const open = menu.classList.contains("hidden");
      if (open) {
        closeVideoPickers?.(menu);
        if (typeof showPillPicker === "function") showPillPicker(menu);
        else menu.classList.remove("hidden");
      } else if (typeof hidePillPicker === "function") {
        hidePillPicker(menu);
      } else {
        menu.classList.add("hidden");
      }
    }
    return;
  }

  const resBtn = e.target.closest(".video-res-btn");
  if (resBtn) {
    e.stopPropagation();
    const nodeId = resBtn.dataset.node;
    const node = CanvasStore.activeBoard()?.nodes?.find((n) => n.id === nodeId);
    const fallback = node?.type === "image" ? "默认" : "自动";
    const nextRes = resBtn.getAttribute("data-resolution") || resBtn.dataset.resolution || fallback;
    // Resolution is API quality only — do not clear media size or resize the node
    CanvasStore.updateNode(nodeId, {
      resolution: nextRes,
    });
    if (typeof refreshVideoPickerLabels === "function") {
      refreshVideoPickerLabels(nodeId, { resize: false });
    }
    if (typeof updateGenCostBadge === "function") updateGenCostBadge(nodeId, { resolution: nextRes });
    return;
  }

  const upgradeCb = e.target.closest(".video-use-upgrade");
  if (upgradeCb) {
    // Legacy checkbox removed from UI — ignore if any stale DOM remains.
    e.stopPropagation();
    return;
  }

  const videoRatioBtn = e.target.closest(".video-ratio-btn");
  if (videoRatioBtn) {
    e.stopPropagation();
    const nodeId = videoRatioBtn.dataset.node;
    const newRatio = videoRatioBtn.dataset.ratio;
    CanvasStore.updateNode(nodeId, {
      ratio: newRatio,
      mediaWidth: null,
      mediaHeight: null,
    });
    void CanvasStore.flushSave?.();
    if (typeof refreshVideoPickerLabels === "function") refreshVideoPickerLabels(nodeId);
    else if (typeof applyNodePreviewSize === "function") applyNodePreviewSize(nodeId);
    return;
  }

  const ratio = e.target.closest(".ratio-btn[data-node]");
  if (ratio) {
    e.stopPropagation();
    const nodeId = ratio.dataset.node;
    const newRatio = ratio.dataset.ratio;
    CanvasStore.updateNode(nodeId, {
      ratio: newRatio,
      mediaWidth: null,
      mediaHeight: null,
    });
    const wrap = document.querySelector(`.node-wrap[data-id="${nodeId}"]`);
    if (wrap) {
      wrap.querySelectorAll(".ratio-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.ratio === newRatio);
      });
      const badge = wrap.querySelector(".node-ratio");
      if (badge) badge.textContent = newRatio;
      if (typeof applyNodePreviewSize === "function") applyNodePreviewSize(nodeId);
    } else {
      renderNodes();
    }
    return;
  }
  const del = e.target.closest(".node-delete");
  if (del) {
    e.stopPropagation();
    const nodeId = del.dataset.node;
    const ids = typeof getSelectedIds === "function" ? getSelectedIds() : [nodeId];
    if (ids.length > 1 && ids.includes(nodeId) && typeof deleteSelectedNodes === "function") {
      deleteSelectedNodes(true);
      return;
    }
    const ok = await AppDialog.confirm("删除此节点？", {
      title: "删除确认",
      okText: "删除",
      danger: true,
    });
    if (!ok) return;
    if (window.CanvasHistory) CanvasHistory.push();
    if (typeof window.cancelQueuedGeneration === "function") {
      window.cancelQueuedGeneration(nodeId);
    }
    CanvasStore.deleteNode(nodeId);
    ConnectionUI.cancel();
    renderNodes();
  }
});

document.getElementById("nodes-layer")?.addEventListener("input", (e) => {
  const slider = e.target.closest(".video-duration-slider");
  if (slider) {
    const nodeId = slider.dataset.node;
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    const modelId =
      typeof resolveEffectiveVideoModelId === "function" && node
        ? resolveEffectiveVideoModelId(node)
        : node?.model;
    const duration = clampVideoDuration(
      slider.value,
      modelId,
      typeof nodeUsesUpgradeDuration === "function" ? nodeUsesUpgradeDuration(node) : !!node?.useUpgrade,
      typeof resolveNodeChannel === "function" && node
        ? resolveNodeChannel(node, { allowGpt: false })
        : undefined
    );
    CanvasStore.updateNode(nodeId, { duration });
    if (typeof refreshVideoPickerLabels === "function") refreshVideoPickerLabels(nodeId);
    if (typeof updateGenCostBadge === "function") updateGenCostBadge(nodeId);
    return;
  }
  const editor = e.target.closest(".prompt-editor");
  if (editor) {
    CanvasStore.updateNode(editor.dataset.node, { prompt: PromptEditor.getValue(editor) });
    if (typeof updateGenCostBadge === "function") updateGenCostBadge(editor.dataset.node);
  }
  const lyrics = e.target.closest(".audio-lyrics-input");
  if (lyrics) {
    CanvasStore.updateNode(lyrics.dataset.node, { lyrics: lyrics.value || "" });
  }
});

document.addEventListener("click", (e) => {
  if (
    e.target.closest(".video-model-picker") ||
    e.target.closest(".video-settings-picker") ||
    e.target.closest(".video-settings-pop") ||
    e.target.closest(".video-duration-slider") ||
    e.target.closest(".video-duration-tick") ||
    e.target.closest(".image-style-picker") ||
    e.target.closest(".channel-picker") ||
    e.target.closest(".prefix-picker") ||
    e.target.closest(".prefix-menu") ||
    e.target.closest(".media-tool-item")
  ) {
    return;
  }
  if (typeof closeVideoPickers === "function") closeVideoPickers();
  if (typeof FrameCapture !== "undefined") FrameCapture.closeMenus();
});

document.getElementById("nodes-layer")?.addEventListener("change", (e) => {
  if (e.target.classList.contains("node-account")) {
    const nodeId = e.target.dataset.node;
    const raw = e.target.value;
    const channel =
      raw === "myapi" ? "myapi" : raw === "bendi" ? "bendi" : raw === "doubao_s" ? "doubao_s" : "doubao";
    const node = CanvasStore.activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const patch = { channel };
    if (node.type === "image") {
      patch.model = defaultModelForChannel(channel, "image");
      if (channel !== "doubao") patch.style = null;
    } else if (node.type === "video") {
      patch.model = defaultModelForChannel(channel, "video");
      patch.duration = clampVideoDuration(node.duration ?? 10, patch.model);
      if (typeof normalizeVideoSettingsForModel === "function") {
        Object.assign(patch, normalizeVideoSettingsForModel({ ...node, ...patch }, patch.model));
      }
    } else if (typeof isTextGenNode === "function" ? isTextGenNode(node) : node.type === "text" || node.type === "voice-text") {
      patch.model = defaultModelForChannel(channel, "text");
    }
    CanvasStore.updateNode(nodeId, patch);
    if (typeof renderNodes === "function") renderNodes();
  }
});

function loadUpscaleWorkerCapacity() {
  return runBackgroundRefresh("upscale-capacity", loadUpscaleWorkerCapacityImpl);
}
async function loadUpscaleWorkerCapacityImpl(signal) {
  if (!window.CanvasAuth?.isLoggedIn?.()) {
    window.__upscaleWorkerCapacity = 0;
    return;
  }
  try {
    const data = await api("/api/video/upscale/status", { signal });
    const cap = Number(data.worker_capacity ?? data.remote?.worker_capacity ?? 0);
    const workers = data.remote?.workers || [];
    const summed = workers.reduce((s, w) => s + Number(w.max_concurrent || 1), 0);
    window.__upscaleWorkerCapacity = cap > 0 ? cap : summed;
  } catch {
    window.__upscaleWorkerCapacity = 0;
  }
  if (typeof MediaTools !== "undefined") MediaTools.pumpUpscaleQueue?.();
}

function loadServerSettings() {
  return runBackgroundRefresh("server-settings", loadServerSettingsImpl);
}
async function loadServerSettingsImpl(signal) {
  if (!window.CanvasAuth?.isLoggedIn?.()) {
    window.__serverMaxThreads = 2;
    window.__upscaleWorkerCapacity = 0;
    updateThreadBadge();
    return;
  }
  try {
    const data = await api("/api/settings", { signal });
    window.__serverMaxThreads = data.max_threads ?? 2;
    if (typeof CanvasPrefs !== "undefined" && CanvasPrefs.upscaleThreads == null) {
      CanvasPrefs.maxThreads = window.__serverMaxThreads;
    }
  } catch {
    window.__serverMaxThreads = 2;
  }
  await loadUpscaleWorkerCapacity();
  updateThreadBadge();
}

function startServerSettingsSync() {
  if (startServerSettingsSync._started) return;
  startServerSettingsSync._started = true;
  window.addEventListener("focus", () => {
    if (!window.CanvasAuth?.isLoggedIn?.()) return;
    loadServerSettings().catch(() => {});
  });
  setInterval(() => {
    if (document.visibilityState === "visible" && window.CanvasAuth?.isLoggedIn?.()) {
      loadServerSettings().catch(() => {});
    }
  }, 15000);
}

async function bootstrapAuthenticatedApp() {
  if (!window.CanvasAuth?.isLoggedIn?.()) return;
  if (typeof migrateDolaSNodes === "function") migrateDolaSNodes();
  if (typeof migrateGptChannelNodes === "function") migrateGptChannelNodes();
  await loadServerSettings();
  startServerSettingsSync();
  await loadAccounts();
  initMyApiOptionGroups();
  await loadTasks();
  recoverGenerationQueue();
  if (typeof CanvasStore !== "undefined") {
    CanvasStore.startSync?.();
    if (typeof window.syncAllNodesFromTasks === "function") {
      await window.syncAllNodesFromTasks();
    }
  }
  if (typeof applyMyApiModelsToCanvas === "function") {
    await applyMyApiModelsToCanvas();
  } else if (typeof refreshMyApiModels === "function") {
    await refreshMyApiModels();
  }
  void refreshOrphanStorageUI();
}
window.bootstrapAuthenticatedApp = bootstrapAuthenticatedApp;

function openMyApiSettings(opts = {}) {
  const allowCustom = window.AppConfig?.allow_frontend_custom_models !== false;
  const platformVolc = window.AppConfig?.volcengine?.platform_enabled !== false;
  if (!allowCustom && !platformVolc) {
    if (typeof showCanvasToast === "function") {
      showCanvasToast("管理员已关闭前台自定义模型，请使用公共模型");
    }
    return;
  }
  const dlg = $("#modal-settings");
  if (!dlg) return;
  dlg.classList.add("myapi-only");
  document.querySelector(".settings-myapi-wrap")?.classList.remove("hidden");
  document.getElementById("myapi-tab-volcengine")?.classList.toggle("hidden", !platformVolc);
  // Hide MyApi-only tabs when custom models disabled
  document.querySelectorAll('.myapi-tab:not([data-myapi-kind="volcengine"])').forEach((btn) => {
    btn.classList.toggle("hidden", !allowCustom);
  });
  const title = dlg.querySelector(".settings-window-head h3");
  const sub = dlg.querySelector(".settings-window-sub");
  if (title) title.textContent = "渠道配置";
  if (sub) {
    sub.textContent = allowCustom
      ? "火山引擎官方 · MyApi 自定义渠道 · 仅当前登录账号"
      : "火山引擎官方渠道 · 仅当前登录账号";
  }
  const kind = String(opts.kind || opts.myapiKind || "").trim().toLowerCase();
  if (kind === "volcengine" || (!allowCustom && platformVolc)) {
    myapiActiveKind = "volcengine";
  } else if (kind && isMyApiKind(kind)) {
    myapiActiveKind = kind;
  }
  if (allowCustom && typeof refreshMyApiSettingsUI === "function") refreshMyApiSettingsUI();
  else syncMyApiVolcengineTabUI();
  void loadVolcengineSettingsUI();
  revealOverlay(dlg);
  if (allowCustom && kind === "agent") {
    requestAnimationFrame(() => {
      document.querySelector('.myapi-tab[data-myapi-kind="agent"]')?.scrollIntoView?.({
        block: "nearest",
        inline: "nearest",
      });
    });
  }
}

function closeSettingsDialog() {
  const dlg = $("#modal-settings");
  if (!dlg) return;
  const wasMyApi = dlg.classList.contains("myapi-only");
  dlg.classList.remove("myapi-only");
  const title = dlg.querySelector(".settings-window-head h3");
  const sub = dlg.querySelector(".settings-window-sub");
  if (title) title.textContent = "设置";
  if (sub) {
    const noBrowser = !!(
    window.AppConfig?.disable_browser_channels ||
    window.AppConfig?.disable_doubao ||
    window.AppConfig?.browser_channels_allowed === false
  );
    sub.textContent = noBrowser
      ? "MyApi · 并发 · 数据"
      : "Seed / MyApi / BenDi · 并发 · 浏览器 · 代理 · 登录包";
  }
  dismissOverlay(dlg);
  if (wasMyApi && typeof applyMyApiModelsToCanvas === "function") {
    void applyMyApiModelsToCanvas();
  }
}

window.openMyApiSettings = openMyApiSettings;

const VOLCENGINE_KIND_META = [
  { id: "image", title: "图片", hint: "Seedream" },
  { id: "video", title: "视频", hint: "Seedance" },
  { id: "text", title: "文本", hint: "Doubao Seed" },
  { id: "voice", title: "语音", hint: "音频生成 · 真出声" },
];

function volcengineModelsForKind(kind) {
  if (kind === "image") return window.VOLCENGINE_IMAGE_MODELS || [];
  if (kind === "video") return window.VOLCENGINE_VIDEO_MODELS || [];
  if (kind === "text") return window.VOLCENGINE_TEXT_MODELS || [];
  if (kind === "voice") return window.VOLCENGINE_VOICE_MODELS || [];
  return [];
}

function formatVolcengineModelMeta(m) {
  const parts = [];
  if (m.desc) parts.push(m.desc);
  if (Array.isArray(m.ratios) && m.ratios.length) parts.push(`比例 ${m.ratios.join(" ")}`);
  if (Array.isArray(m.resolutions) && m.resolutions.length) parts.push(m.resolutions.join("/"));
  if (m.duration_min != null || m.duration_max != null) {
    parts.push(`时长 ${m.duration_min ?? 4}–${m.duration_max ?? 10}s`);
  }
  if (m.max_images) parts.push(`图×${m.max_images}`);
  if (m.max_videos) parts.push(`视频×${m.max_videos}`);
  if (m.max_audios) parts.push(`音频×${m.max_audios}`);
  if (m.params_note) parts.push(m.params_note);
  if (m.upload_note) parts.push(m.upload_note);
  if (m.canvas === false) parts.push("画布语音节点不调用");
  return parts.join(" · ");
}

function syncMyApiVolcengineTabUI() {
  const isVolc = myapiActiveKind === "volcengine";
  document.querySelectorAll(".myapi-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.myapiKind === myapiActiveKind);
  });
  $("#myapi-model-list")?.classList.toggle("hidden", isVolc);
  $("#volcengine-panel")?.classList.toggle("hidden", !isVolc);
  $("#myapi-panel-foot")?.classList.toggle("hidden", isVolc);
}

function renderVolcengineKindList(data = null) {
  const host = document.getElementById("volcengine-kind-list");
  if (!host) return;
  const cfg = data || window.AppConfig?.volcengine || {};
  const kinds = cfg.kinds_enabled || {};
  const defaults = cfg.default_kinds || {};
  const agentIds = new Set(Array.isArray(cfg.text_agent_ids) ? cfg.text_agent_ids : []);
  host.innerHTML = VOLCENGINE_KIND_META.map(({ id, title, hint }) => {
    const on = !!kinds[id];
    const isDef = !!defaults[id];
    const models = volcengineModelsForKind(id);
    const modelRows = models
      .map((m) => {
        const agentOn = id === "text" && agentIds.has(m.id);
        const agentBtn =
          id === "text" && m.agent_capable !== false
            ? `<button type="button" class="btn btn-ghost btn-sm${agentOn ? " is-default-on" : ""}" data-volc-agent="${escapeAttr(
                m.id
              )}" ${on ? "" : "disabled"} title="${
                agentOn ? "取消：智能体不再使用此模型" : "用于智能体：小庆子模型列表会出现此项"
              }">${agentOn ? "取消智能体" : "用于智能体"}</button>`
            : "";
        return `
      <div class="myapi-item volcengine-model-item">
        <div class="myapi-item-main">
          <div class="myapi-item-name">${escapeHtml(m.label || m.id)}${
            agentOn ? `<span class="myapi-default-badge" title="智能体可用">智能体</span>` : ""
          }</div>
          <div class="myapi-item-meta myapi-item-limits">${escapeHtml(formatVolcengineModelMeta(m))}</div>
        </div>
        <div class="myapi-item-actions">${agentBtn}</div>
      </div>`;
      })
      .join("");
    return `
    <div class="volcengine-kind-block${on ? " is-on" : ""}" data-volc-kind="${escapeAttr(id)}">
      <div class="myapi-item${isDef ? " is-default" : ""}">
        <div class="myapi-item-main">
          <div class="myapi-item-name">${escapeHtml(title)}${
            isDef ? `<span class="myapi-default-badge" title="新建该类型节点默认走火山引擎">默认</span>` : ""
          }</div>
          <div class="myapi-item-meta">${escapeHtml(hint)} · ${models.length} 个内置模型</div>
        </div>
        <div class="myapi-item-actions volcengine-kind-actions">
          <label class="settings-switch" title="开启${title}">
            <input type="checkbox" data-volc-enable="${escapeAttr(id)}" ${on ? "checked" : ""} />
            <span class="settings-switch-ui" aria-hidden="true"></span>
          </label>
          <button type="button" class="btn btn-ghost btn-sm${isDef ? " is-default-on" : ""}" data-volc-default="${escapeAttr(id)}" ${
            on ? "" : "disabled"
          } title="${
            isDef ? "取消默认" : "设为默认：新建该类型节点优先走火山引擎"
          }">${isDef ? "取消默认" : "设为默认"}</button>
        </div>
      </div>
      <div class="volcengine-model-rows">${modelRows}</div>
    </div>`;
  }).join("");
}

async function loadVolcengineSettingsUI() {
  const panel = document.getElementById("volcengine-panel");
  const tab = document.getElementById("myapi-tab-volcengine");
  const platformOn = window.AppConfig?.volcengine?.platform_enabled !== false;
  tab?.classList.toggle("hidden", !platformOn);
  if (!panel) return;
  if (!platformOn) {
    if (myapiActiveKind === "volcengine") {
      myapiActiveKind = "image";
      syncMyApiVolcengineTabUI();
    }
    return;
  }
  try {
    const data = await api("/api/volcengine/settings");
    const keyStatus = $("#volcengine-key-status");
    const keyEl = $("#volcengine-api-key");
    if (keyEl && document.activeElement !== keyEl) keyEl.value = "";
    if (keyStatus) {
      keyStatus.hidden = false;
      if (data.api_key_set) {
        keyStatus.classList.add("is-ok");
        keyStatus.classList.remove("is-bad");
        const masked = String(data.api_key_masked || "");
        const tail = masked.slice(-4) || "****";
        keyStatus.textContent = `已配置 · …${tail}`;
        keyStatus.title = masked ? `已配置 · ${masked}` : "已配置";
      } else {
        keyStatus.classList.add("is-bad");
        keyStatus.classList.remove("is-ok");
        keyStatus.textContent = "未配置";
        keyStatus.title = "尚未配置方舟 API Key";
      }
    }
    const speechStatus = $("#volcengine-speech-key-status");
    const speechEl = $("#volcengine-speech-api-key");
    if (speechEl && document.activeElement !== speechEl) speechEl.value = "";
    if (speechStatus) {
      speechStatus.hidden = false;
      if (data.speech_api_key_set) {
        speechStatus.classList.add("is-ok");
        speechStatus.classList.remove("is-bad");
        const masked = String(data.speech_api_key_masked || "");
        const tail = masked.slice(-4) || "****";
        speechStatus.textContent = `已配置 · …${tail}`;
        speechStatus.title = masked ? `已配置 · ${masked}` : "已配置";
      } else {
        speechStatus.classList.add("is-bad");
        speechStatus.classList.remove("is-ok");
        speechStatus.textContent = "未配置（将回退方舟 Key）";
        speechStatus.title = "未配置 openspeech Key 时，音频生成会尝试用方舟 Key（常会鉴权失败）";
      }
    }
    if (window.AppConfig) {
      window.AppConfig.volcengine = {
        ...(window.AppConfig.volcengine || {}),
        ...data,
        platform_enabled: data.platform_enabled !== false,
      };
      window.AppConfig.volcengine_allowed = !!data.channel_ready;
    }
    renderVolcengineKindList(data);
    syncMyApiVolcengineTabUI();
  } catch (err) {
    const keyStatus = $("#volcengine-key-status");
    if (keyStatus) {
      keyStatus.hidden = false;
      keyStatus.classList.add("is-bad");
      keyStatus.classList.remove("is-ok");
      keyStatus.textContent = err.message || "读取失败";
    }
  }
}

function readVolcengineKindsFromUI() {
  const kinds = { image: false, video: false, text: false, voice: false };
  const defaults = { image: false, video: false, text: false, voice: false };
  document.querySelectorAll("[data-volc-enable]").forEach((el) => {
    const k = el.getAttribute("data-volc-enable");
    if (k && k in kinds) kinds[k] = !!el.checked;
  });
  // defaults from AppConfig + button state on list (is-default class)
  const cfgDefaults = window.AppConfig?.volcengine?.default_kinds || {};
  for (const k of Object.keys(defaults)) {
    defaults[k] = !!cfgDefaults[k] && !!kinds[k];
  }
  document.querySelectorAll("[data-volc-default]").forEach((btn) => {
    const k = btn.getAttribute("data-volc-default");
    if (!k || !(k in defaults)) return;
    defaults[k] = btn.classList.contains("is-default-on") && !!kinds[k];
  });
  const text_agent_ids = [];
  document.querySelectorAll("[data-volc-agent]").forEach((btn) => {
    const mid = btn.getAttribute("data-volc-agent");
    if (mid && btn.classList.contains("is-default-on") && kinds.text) text_agent_ids.push(mid);
  });
  return { kinds_enabled: kinds, default_kinds: defaults, text_agent_ids };
}

async function saveVolcengineSettings(extra = {}) {
  const fromUi = readVolcengineKindsFromUI();
  const body = {
    kinds_enabled: extra.kinds_enabled || fromUi.kinds_enabled,
    default_kinds: extra.default_kinds || fromUi.default_kinds,
    text_agent_ids: extra.text_agent_ids || fromUi.text_agent_ids,
  };
  const key = ($("#volcengine-api-key")?.value || "").trim();
  if (key) body.api_key = key;
  const speechKey = ($("#volcengine-speech-api-key")?.value || "").trim();
  if (speechKey) body.speech_api_key = speechKey;
  try {
    const data = await api("/api/volcengine/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    if (window.AppConfig) {
      window.AppConfig.volcengine = {
        ...(window.AppConfig.volcengine || {}),
        ...data,
        platform_enabled: true,
      };
      window.AppConfig.volcengine_allowed = !!data.channel_ready;
      const chans = Array.isArray(window.AppConfig.channels) ? [...window.AppConfig.channels] : [];
      if (data.channel_ready && !chans.includes("volcengine")) {
        window.AppConfig.channels = [...chans, "volcengine"];
      } else if (!data.channel_ready) {
        window.AppConfig.channels = chans.filter((c) => c !== "volcengine");
      }
    }
    await loadVolcengineSettingsUI();
    if (typeof loadAppConfig === "function") await loadAppConfig();
    if (typeof renderNodes === "function") renderNodes({ force: true });
    if (extra.silent) return data;
    showCanvasToast?.(data.channel_ready ? "火山引擎已保存，渠道可用" : "火山引擎已保存（需 Key + 至少一个开关）");
    return data;
  } catch (err) {
    await AppDialog.alert(err.message || "保存失败", { title: "火山引擎" });
    return null;
  }
}

window.loadVolcengineSettingsUI = loadVolcengineSettingsUI;
window.saveVolcengineSettings = saveVolcengineSettings;

$("#btn-volcengine-save")?.addEventListener("click", () => {
  void saveVolcengineSettings();
});

document.getElementById("volcengine-kind-list")?.addEventListener("click", async (e) => {
  const defBtn = e.target.closest?.("[data-volc-default]");
  if (defBtn) {
    e.preventDefault();
    const kind = defBtn.getAttribute("data-volc-default");
    if (!kind) return;
    const fromUi = readVolcengineKindsFromUI();
    if (!fromUi.kinds_enabled[kind]) {
      showCanvasToast?.("请先开启该类型");
      return;
    }
    const next = !defBtn.classList.contains("is-default-on");
    fromUi.default_kinds[kind] = next;
    await saveVolcengineSettings({
      kinds_enabled: fromUi.kinds_enabled,
      default_kinds: fromUi.default_kinds,
      text_agent_ids: fromUi.text_agent_ids,
      silent: true,
    });
    showCanvasToast?.(
      next ? `新建${VOLCENGINE_KIND_META.find((x) => x.id === kind)?.title || kind}节点将默认走火山引擎` : "已取消火山引擎默认"
    );
    return;
  }
  const agentBtn = e.target.closest?.("[data-volc-agent]");
  if (agentBtn) {
    e.preventDefault();
    const mid = agentBtn.getAttribute("data-volc-agent");
    if (!mid) return;
    const fromUi = readVolcengineKindsFromUI();
    if (!fromUi.kinds_enabled.text) {
      showCanvasToast?.("请先开启文本");
      return;
    }
    const next = !agentBtn.classList.contains("is-default-on");
    const set = new Set(fromUi.text_agent_ids || []);
    if (next) set.add(mid);
    else set.delete(mid);
    await saveVolcengineSettings({
      kinds_enabled: fromUi.kinds_enabled,
      default_kinds: fromUi.default_kinds,
      text_agent_ids: [...set],
      silent: true,
    });
    showCanvasToast?.(next ? "已加入智能体模型列表" : "已从智能体列表移除");
    if (typeof globalThis.AgentApi?.listAgentModels === "function") {
      void globalThis.AgentApi.listAgentModels({ force: true });
    }
    return;
  }
});

document.getElementById("volcengine-kind-list")?.addEventListener("change", async (e) => {
  const enableEl = e.target?.closest?.("[data-volc-enable]");
  if (!enableEl) return;
  const kind = enableEl.getAttribute("data-volc-enable");
  const fromUi = readVolcengineKindsFromUI();
  fromUi.kinds_enabled[kind] = !!enableEl.checked;
  if (!enableEl.checked) fromUi.default_kinds[kind] = false;
  await saveVolcengineSettings({
    kinds_enabled: fromUi.kinds_enabled,
    default_kinds: fromUi.default_kinds,
    text_agent_ids: fromUi.text_agent_ids,
    silent: true,
  });
});

$("#btn-settings")?.addEventListener("click", () => {
  const dlg = $("#modal-settings");
  if (!dlg) return;
  dlg.classList.remove("myapi-only");
  refreshThreadSettingsUI();
  revealOverlay(dlg);
});
$("#btn-close-settings")?.addEventListener("click", closeSettingsDialog);

$("#btn-clear-gen-history")?.addEventListener("click", async () => {
  const ok = await AppDialog.confirm(
    "将清除所有节点的生成历史记录（画板上正在显示的图片/视频不会删除）。确定继续？",
    { title: "清除生成历史", okText: "清除", danger: true }
  );
  if (!ok) return;
  const btn = $("#btn-clear-gen-history");
  const hint = $("#clear-history-hint");
  if (btn) btn.disabled = true;
  try {
    let nodesCleared = 0;
    let entriesCleared = 0;
    if (typeof CanvasStore !== "undefined") {
      for (const board of CanvasStore.boards || []) {
        for (const node of board.nodes || []) {
          const hist = Array.isArray(node.genHistory) ? node.genHistory : [];
          if (!hist.length && !node.activeHistoryId) continue;
          entriesCleared += hist.length;
          nodesCleared += 1;
          node.genHistory = [];
          node.activeHistoryId = null;
        }
      }
      if (typeof CanvasStore.flushSave === "function") {
        await CanvasStore.flushSave();
      } else {
        CanvasStore.save();
      }
    }
    if (typeof NodeHistory !== "undefined" && NodeHistory.close) {
      NodeHistory.close();
    }
    if (hint) {
      hint.textContent = `已清空 ${nodesCleared} 个节点共 ${entriesCleared} 条历史记录`;
    }
    if (typeof showCanvasToast === "function") {
      showCanvasToast(`已清理 ${entriesCleared} 条生成历史`);
    }
    void refreshOrphanStorageUI();
  } finally {
    if (btn) btn.disabled = false;
  }
});

function formatOrphanStorageHint(data) {
  const files = Number(data?.orphan_files) || 0;
  const label = data?.orphan_bytes_label || "0 B";
  if (!files) return "当前没有可清理的未引用上传文件";
  const board = Number(data?.board_orphan_files) || 0;
  const legacy = Number(data?.legacy_orphan_files) || 0;
  const parts = [`约 ${files} 个文件（${label}）可清理`];
  if (board || legacy) parts.push(`画板素材 ${board} · 旧目录 ${legacy}`);
  return parts.join(" · ");
}

async function refreshOrphanStorageUI() {
  const hint = $("#orphan-storage-hint");
  if (!hint) return;
  try {
    const data = await api("/api/uploads/orphan-stats");
    hint.textContent = formatOrphanStorageHint(data);
  } catch {
    hint.textContent = "无法扫描上传缓存（请确认服务已启动）";
  }
}

$("#btn-purge-orphan-uploads")?.addEventListener("click", async () => {
  let preview = "";
  try {
    const stats = await api("/api/uploads/orphan-stats");
    preview = formatOrphanStorageHint(stats);
  } catch {
    preview = "将清理未被任何画板节点引用的上传文件。";
  }
  const ok = await AppDialog.confirm(`${preview}\n\n画板上正在使用的图片/视频不会删除。确定清理？`, {
    title: "清理孤立文件",
    okText: "清理",
    danger: true,
  });
  if (!ok) return;
  const btn = $("#btn-purge-orphan-uploads");
  const hint = $("#orphan-storage-hint");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/uploads/purge-orphans", { method: "POST", body: "{}" });
    const deleted = Number(data?.deleted) || 0;
    const label = data?.orphan_bytes_label || "";
    if (hint) {
      hint.textContent =
        deleted > 0
          ? `已处理 ${deleted} 个未引用文件${label ? `（约 ${label}）` : ""}`
          : "没有需要清理的未引用文件";
    }
    if (typeof showCanvasToast === "function") {
      showCanvasToast(deleted > 0 ? `已清理 ${deleted} 个未引用文件` : "没有可清理的文件");
    }
    void refreshOrphanStorageUI();
  } catch (err) {
    if (hint) hint.textContent = `清理失败：${err.message || err}`;
    if (typeof showCanvasToast === "function") showCanvasToast(`清理失败：${err.message || err}`);
  } finally {
    if (btn) btn.disabled = false;
  }
});

function refreshThreadSettingsUI() {
  if (typeof CanvasPrefs === "undefined") return;
  CanvasPrefs.load();
  const perUser = getUserThreadLimit();
  document.querySelectorAll("#thread-value, #thread-badge-value").forEach((el) => {
    el.textContent = String(perUser);
  });
  const hint = $("#thread-recommend-hint");
  if (hint) {
    const worker = Number(window.__workerConcurrency);
    hint.textContent = `最多同时提交 ${perUser} 个任务${worker > 0 ? `；任务服务同时执行 ${worker} 个，其余排队` : ""}`;
  }
  document.querySelectorAll(".gen-records-thread-hint").forEach((el) => {
    if (el.closest(".gen-records-api-row") || /超时|重试|轮询/.test(el.textContent || "")) return;
    el.textContent = `最多同时提交 ${perUser} 个任务（1–64），超出任务服务执行槽的任务会排队`;
  });
  updateThreadBadge();
}

function applyThreadLimitDelta(delta) {
  if (typeof CanvasPrefs === "undefined") return;
  CanvasPrefs.setMaxThreads(getMaxThreads() + delta);
  refreshThreadSettingsUI();
  pumpGenQueue();
}
window.refreshThreadSettingsUI = refreshThreadSettingsUI;
window.applyThreadLimitDelta = applyThreadLimitDelta;

async function refreshHeadlessSettingsUI() {
  await refreshBrowserSettingsUI();
}

async function refreshRuntimeUI() {
  const el = $("#data-dir-status");
  if (!el) return;
  try {
    const data = await api("/api/runtime");
    el.classList.remove("is-bad");
    el.classList.add("is-ok");
    el.textContent = `data 目录：${data.data_dir || "—"}\n画布文件：${data.canvas_file || "—"}`;
  } catch {
    el.classList.remove("is-ok");
    el.classList.add("is-bad");
    el.textContent = "无法读取数据目录（请确认服务已启动）";
  }
}

function applyBrowserReport(data) {
  const toggle = $("#toggle-headless");
  const hint = $("#headless-hint");
  const statusEl = $("#browser-path-status");
  const input = $("#browser-path-input");
  const pathHint = $("#browser-path-hint");
  const proxyInput = $("#proxy-server-input");
  const proxyStatus = $("#proxy-status");
  const proxyHint = $("#proxy-hint");
  if (toggle && data) toggle.checked = !!data.headless;
  if (hint && data) {
    hint.textContent = data.headless
      ? "已开启：生成时 Chromium 最小化（不用真无头，避免页面卡住）"
      : "已关闭：生成时可看到浏览器操作过程";
  }
  const browser = data?.browser || {};
  const resolved = browser.resolved || {};
  if (input && document.activeElement !== input) {
    input.value = data?.browser_executable || "";
  }
  if (statusEl) {
    statusEl.classList.remove("is-ok", "is-bad");
    if (resolved.ok) {
      statusEl.classList.add("is-ok");
      statusEl.textContent =
        `可用 · ${resolved.label || "已检测"}\n${resolved.path || ""}\n目录：${browser.browsers_dir || "—"}`;
    } else {
      statusEl.classList.add("is-bad");
      statusEl.textContent =
        `${browser.hint || resolved.error || "未找到浏览器"}\n目录：${browser.browsers_dir || "—"}`;
    }
  }
  if (pathHint) {
    pathHint.textContent = resolved.ok
      ? `当前来源：${resolved.label || "自动"}（保存自定义路径后下次生成生效）`
      : "未找到可用浏览器，请点「检测」或「选择」指定 chrome.exe";
  }
  if (proxyInput && document.activeElement !== proxyInput) {
    proxyInput.value = data?.proxy_server || "";
  }
  if (proxyStatus) {
    proxyStatus.classList.remove("is-ok", "is-bad");
    const resolvedProxy = data?.proxy_resolved || "";
    const from = data?.proxy_from || "none";
    if (resolvedProxy) {
      proxyStatus.classList.add("is-ok");
      proxyStatus.textContent =
        from === "settings"
          ? `使用设置代理\n${resolvedProxy}`
          : `使用系统/环境代理\n${resolvedProxy}`;
    } else {
      proxyStatus.textContent = "未配置代理（直连；部分远程浏览器渠道可能需要代理）";
    }
  }
  if (proxyHint) {
    proxyHint.textContent =
      "浏览器渠道需要代理时可填写，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:7890；留空则跟系统/环境代理";
  }
}

async function refreshBrowserSettingsUI() {
  try {
    const data = await api("/api/settings");
    applyBrowserReport(data);
  } catch {
    const hint = $("#headless-hint");
    const statusEl = $("#browser-path-status");
    if (hint) hint.textContent = "无法读取静默设置";
    if (statusEl) {
      statusEl.classList.add("is-bad");
      statusEl.textContent = "无法读取浏览器设置";
    }
  }
}

$("#toggle-headless")?.addEventListener("change", async (e) => {
  const on = !!e.target.checked;
  const hint = $("#headless-hint");
  try {
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ headless: on }),
    });
    e.target.checked = !!data.headless;
    applyBrowserReport(data);
    if (hint) {
      hint.textContent = data.headless
        ? "已开启：下次生成时浏览器会最小化（已关闭旧窗口）"
        : "已关闭：下次生成会弹出浏览器窗口";
    }
    if (typeof showCanvasToast === "function") {
      showCanvasToast(data.headless ? "已开启静默处理" : "已关闭静默处理");
    }
  } catch (err) {
    e.target.checked = !on;
    await AppDialog.alert(err.message || "保存失败", { title: "静默处理" });
  }
});

$("#btn-browser-detect")?.addEventListener("click", async () => {
  const statusEl = $("#browser-path-status");
  if (statusEl) statusEl.textContent = "正在检测…";
  try {
    const data = await api("/api/settings");
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") {
      showCanvasToast(data?.browser?.ok ? "已检测到可用浏览器" : "未检测到浏览器");
    }
  } catch (err) {
    await AppDialog.alert(err.message || "检测失败", { title: "浏览器路径" });
  }
});

$("#btn-browser-browse")?.addEventListener("click", async () => {
  try {
    const res = await api("/api/settings/browser/browse", { method: "POST", body: "{}" });
    if (!res?.path) {
      if (typeof showCanvasToast === "function") showCanvasToast("已取消选择");
      return;
    }
    const input = $("#browser-path-input");
    if (input) input.value = res.path;
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ browser_executable: res.path }),
    });
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") showCanvasToast("已保存浏览器路径");
  } catch (err) {
    await AppDialog.alert(err.message || "选择失败", { title: "浏览器路径" });
  }
});

$("#btn-browser-reset")?.addEventListener("click", async () => {
  try {
    const input = $("#browser-path-input");
    if (input) input.value = "";
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ browser_executable: "" }),
    });
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") showCanvasToast("已恢复自动检测");
  } catch (err) {
    await AppDialog.alert(err.message || "恢复失败", { title: "浏览器路径" });
  }
});

$("#btn-browser-save")?.addEventListener("click", async () => {
  const input = $("#browser-path-input");
  const path = (input?.value || "").trim();
  try {
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ browser_executable: path }),
    });
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") {
      showCanvasToast(path ? "已保存浏览器路径" : "已恢复自动检测");
    }
  } catch (err) {
    await AppDialog.alert(err.message || "保存失败", { title: "浏览器路径" });
  }
});

$("#btn-proxy-save")?.addEventListener("click", async () => {
  const input = $("#proxy-server-input");
  const proxy = (input?.value || "").trim();
  try {
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ proxy_server: proxy }),
    });
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") {
      showCanvasToast(proxy ? "已保存代理" : "已清空代理（跟系统/直连）");
    }
  } catch (err) {
    await AppDialog.alert(err.message || "保存失败", { title: "代理" });
  }
});

$("#btn-proxy-clear")?.addEventListener("click", async () => {
  try {
    const input = $("#proxy-server-input");
    if (input) input.value = "";
    const data = await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ proxy_server: "" }),
    });
    applyBrowserReport(data);
    if (typeof showCanvasToast === "function") showCanvasToast("已清空代理");
  } catch (err) {
    await AppDialog.alert(err.message || "清空失败", { title: "代理" });
  }
});

$("#btn-thread-minus")?.addEventListener("click", () => {
  applyThreadLimitDelta(-1);
});
$("#btn-thread-plus")?.addEventListener("click", () => {
  applyThreadLimitDelta(1);
});
$("#btn-thread-recommend")?.addEventListener("click", () => {
  CanvasPrefs.setMaxThreads(CanvasPrefs.recommendedThreads());
  refreshThreadSettingsUI();
  pumpGenQueue();
});
document.querySelectorAll("#btn-thread-badge-minus").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    applyThreadLimitDelta(-1);
  });
});
document.querySelectorAll("#btn-thread-badge-plus").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    applyThreadLimitDelta(1);
  });
});

$("#btn-close-qr")?.addEventListener("click", () => {
  void cancelCloseQrLogin();
});

$("#modal-qr-login")?.addEventListener("cancel", (e) => {
  e.preventDefault();
  void cancelCloseQrLogin();
});

$("#btn-manual-login")?.addEventListener("click", () => {
  startDoubaoManualLogin(window.__qrLoginAccountId);
});

$("#btn-export-accounts")?.addEventListener("click", async () => {
  try {
    const res = await fetch(API + "/api/accounts/export");
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : res.statusText || "导出失败");
    }
    const blob = await res.blob();
    if (!blob || blob.size < 20) {
      throw new Error("导出内容为空，请确认已有账号");
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/i.exec(cd);
    const name = m?.[1] || `doubao-accounts-${Date.now()}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    if (typeof showCanvasToast === "function") showCanvasToast("登录包已导出");
  } catch (err) {
    await AppDialog.alert(err.message || "导出失败", { title: "导出登录包" });
  }
});

$("#btn-import-accounts")?.addEventListener("click", () => {
  $("#input-import-accounts")?.click();
});

$("#input-import-accounts")?.addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;

  const merge = await AppDialog.confirm(
    "将把登录包合并进本机：已有相同账号 ID 会跳过，只新增包里没有的账号。\n\n若要先清空本机全部账号再导入，请点取消后使用「全部替换」流程。",
    { title: "合并导入", okText: "合并导入" }
  );
  let mode = "merge";
  if (!merge) {
    const replace = await AppDialog.confirm(
      "清空本机全部账号后，再导入此登录包？此操作不可恢复。",
      { title: "全部替换", okText: "全部替换", danger: true }
    );
    if (!replace) return;
    mode = "replace";
  }

  const fd = new FormData();
  fd.append("file", file);
  fd.append("mode", mode);
  try {
    const res = await fetch(API + "/api/accounts/import", { method: "POST", body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : res.statusText);
    }
    const result = await res.json();
    await AppDialog.alert(
      `导入完成：新增 ${result.imported}，跳过 ${result.skipped}。当前共 ${result.total} 个账号。建议点一次「检测」确认在线。`,
      { title: "导入成功" }
    );
    await loadAccounts();
  } catch (err) {
    await AppDialog.alert(err.message || "导入失败", { title: "导入登录包" });
  }
});

/* ── MyApi settings ─────────────────────────────────────── */
let myapiActiveKind = "image";
let myapiCache = [];

const MYAPI_DEFAULTS = {
  max_images: 9,
  max_videos: 3,
  max_audios: 3,
  duration_min: 4,
  duration_max: 10,
  send_duration: true,
  /** 视频接口风格默认「默认/通用」；图片与文字不展示该字段 */
  api_style: "generic",
};

const MYAPI_API_STYLE_LABELS = {
  generic: "默认",
  volcengine: "火山引擎",
};

/** Default Base URL filled when picking an API style (empty = no fixed host). */
const MYAPI_API_STYLE_BASE_URLS = {
  volcengine: "https://ark.cn-beijing.volces.com",
  generic: "",
};

function normalizeMyApiBaseUrlKey(url) {
  return String(url || "")
    .trim()
    .replace(/\/+$/, "")
    .toLowerCase();
}

function knownMyApiStyleBaseUrlKeys() {
  const keys = new Set();
  Object.values(MYAPI_API_STYLE_BASE_URLS).forEach((u) => {
    const n = normalizeMyApiBaseUrlKey(u);
    if (!n) return;
    keys.add(n);
    // Accept the same host with or without /v1
    if (n.endsWith("/v1")) keys.add(n.slice(0, -3).replace(/\/+$/, ""));
    else keys.add(`${n}/v1`);
  });
  return keys;
}

function isKnownMyApiStyleBaseUrl(url) {
  const n = normalizeMyApiBaseUrlKey(url);
  if (!n) return true;
  return knownMyApiStyleBaseUrlKeys().has(n);
}

function applyMyApiStyleBaseUrl(style, { force = false } = {}) {
  const urlEl = $("#myapi-base-url");
  if (!urlEl) return;
  const next = MYAPI_API_STYLE_BASE_URLS[style];
  if (typeof next !== "string") return;
  const cur = (urlEl.value || "").trim();
  // 空白 URL 不自动填写，留给用户自己的接口地址
  if (!force && !cur) return;
  // 用户自定义 URL 不被覆盖
  if (!force && cur && !isKnownMyApiStyleBaseUrl(cur)) return;
  if (urlEl.value !== next) urlEl.value = next;
}

function syncMyApiApiStyleSelectOptions(keepStyle) {
  const sel = $("#myapi-api-style");
  if (!sel) return;
  const enabledMap = window.AppConfig?.myapi_api_styles_enabled || {};
  const keep = keepStyle || sel.value;
  [...sel.options].forEach((opt) => {
    const sid = opt.value;
    const on = enabledMap[sid] !== false || sid === keep;
    opt.hidden = enabledMap[sid] === false && sid !== keep;
    opt.disabled = enabledMap[sid] === false && sid !== keep;
  });
  const visible = [...sel.options].filter((o) => !o.disabled);
  if (visible.length && (sel.selectedOptions[0]?.disabled || !sel.value)) {
    sel.value = visible[0].value;
  }
}

const MYAPI_API_STYLE_HINTS = {
  generic:
    "默认风格：images / videos / audios + duration/seconds + ratio/aspect_ratio + size，兼容多数 OpenAI 风格中转。",
  volcengine:
    "火山方舟：图 POST /api/v3/images/generations（Seedream），视频 POST /api/v3/contents/generations/tasks（Seedance），文本 /api/v3/chat/completions。语音用 openspeech POST /api/v3/tts/create（X-Api-Key）。图/视/文 Base：https://ark.cn-beijing.volces.com ；语音 Base：https://openspeech.bytedance.com",
};

/** POST /v1/videos body field reference (shown in 详情 panel). */
const MYAPI_API_STYLE_SPECS = {
  generic: {
    title: "通用",
    create: "POST {base_url}/v1/videos",
    poll: "GET {base_url}/v1/videos/{task_id}",
    note: "同时发送多种常见字段名，便于兼容未文档化的 OpenAI 风格中转站。",
    fields: [
      { name: "model", type: "string", required: true, desc: "模型 ID" },
      { name: "prompt", type: "string", required: true, desc: "文本提示词" },
      { name: "images", type: "string[]", optional: true, desc: "参考图片 URL 数组" },
      { name: "videos", type: "string[]", optional: true, desc: "参考视频 URL 数组" },
      { name: "audios", type: "string[]", optional: true, desc: "参考音频 URL 数组" },
      { name: "duration", type: "number", optional: true, desc: "时长（秒）" },
      { name: "seconds", type: "number", optional: true, desc: "与 duration 同值，兼容只认 seconds 的上游" },
      { name: "ratio", type: "string", optional: true, desc: "比例，如 16:9" },
      { name: "aspect_ratio", type: "string", optional: true, desc: "与 ratio 同值，兼容不同字段名" },
      {
        name: "size",
        type: "string",
        optional: true,
        desc: "尺寸 WxH，如 1280x720；由节点分辨率/比例换算",
      },
    ],
  },
  volcengine: {
    title: "火山引擎（方舟 / openspeech）",
    create:
      "图 POST {ark}/api/v3/images/generations · 视频 POST {ark}/api/v3/contents/generations/tasks · 语音 POST {speech}/api/v3/tts/create",
    poll: "视频 GET {ark}/api/v3/contents/generations/tasks/{id}；图/语音多为同步返回",
    note: "图/视/文 Base：https://ark.cn-beijing.volces.com（Bearer）。语音 Base：https://openspeech.bytedance.com（X-Api-Key）。模型 ID 需写全，如 doubao-seedream-4-5-… / doubao-seedance-… / seed-audio-1.0。",
    fields: [
      { name: "model", type: "string", required: true, desc: "方舟接入点或模型 ID / seed-audio-1.0" },
      { name: "prompt / text_prompt", type: "string", required: true, desc: "图视文用 prompt；语音用 text_prompt" },
      { name: "content[]", type: "object[]", optional: true, desc: "Seedance：text / image_url / video_url / audio_url" },
      { name: "image", type: "string | string[]", optional: true, desc: "Seedream 参考图 URL 或数组" },
      { name: "size / resolution / ratio", type: "string", optional: true, desc: "图 size=1K|2K|4K；视频 resolution+ratio" },
      { name: "references[]", type: "object[]", optional: true, desc: "语音：speaker / audio_url / image_url" },
      { name: "audio_config", type: "object", optional: true, desc: "语音输出 format / sample_rate 等" },
    ],
  },
};

const MYAPI_RATIO_OPTIONS = ["16:9", "3:4", "4:3", "9:16", "1:1", "21:9"];
const MYAPI_RESOLUTION_OPTIONS = ["自由", "480p", "720p", "1080p"];
const MYAPI_DEFAULT_RATIOS = ["16:9"];
const MYAPI_DEFAULT_RESOLUTIONS = [];
const MYAPI_CLIP_PREFIX = "qinzi-myapi:1:";
const MYAPI_KINDS = ["image", "video", "text", "voice", "vision", "agent"];

function isMyApiKind(kind) {
  return MYAPI_KINDS.includes(String(kind || "").trim());
}

function myapiKindLabel(kind) {
  return (
    {
      image: "图片模型",
      video: "视频模型",
      text: "文本模型",
      voice: "语音模型",
      vision: "视觉模型",
      agent: "智能体模型",
    }[kind] || "模型"
  );
}

function populateMyApiVisionSelect(selectedId) {
  const sel = $("#myapi-vision-model-id");
  if (!sel) return;
  const visions = myapiCache.filter((m) => m.kind === "vision");
  const cur = selectedId != null ? String(selectedId) : String(sel.value || "");
  sel.innerHTML =
    `<option value="">不使用 / 未添加视觉模型</option>` +
    visions
      .map(
        (m) =>
          `<option value="${escapeAttr(m.id)}">${escapeHtml(m.name || m.model || m.id)}${
            m.model ? ` · ${escapeHtml(m.model)}` : ""
          }</option>`
      )
      .join("");
  if (cur && visions.some((m) => m.id === cur)) sel.value = cur;
  else sel.value = "";
  window.UiSelect?.enhance(sel);
  window.UiSelect?.refresh(sel);
}

function syncAgentMultimodalUi() {
  const multi = !!$("#myapi-multimodal")?.checked;
  const wrap = $("#myapi-vision-pick-wrap");
  if (wrap) wrap.classList.toggle("hidden", multi);
  if (multi && $("#myapi-vision-model-id")) {
    $("#myapi-vision-model-id").value = "";
    window.UiSelect?.refresh($("#myapi-vision-model-id"));
  }
}

function syncAgentContextPrefsUi() {
  const p = window.getAgentPreferences?.() || {};
  const detail = $("#agent-context-detail");
  const recent = $("#agent-recent-messages");
  const memory = $("#agent-memory-size");
  const assistant = $("#agent-assistant-size");
  const page = $("#agent-ui-page-size");
  const more = $("#agent-ui-load-more");
  if (detail) detail.value = p.contextDetail || "balanced";
  if (recent) recent.value = String(p.modelRecentMessages || 8);
  if (memory) memory.value = String(p.memoryMaxChars || 2400);
  if (assistant) assistant.value = String(p.recentAssistantMax || 1200);
  if (page) page.value = String(p.uiPageSize || 10);
  if (more) more.value = String(p.uiLoadMore || 10);
  // Must enhance first — refresh alone does nothing on bare native <select>.
  [detail, memory, assistant, page, more].forEach((el) => {
    if (!el) return;
    window.UiSelect?.enhance?.(el);
    window.UiSelect?.refresh?.(el);
  });
}

function bindAgentContextPrefs() {
  const fields = [
    $("#agent-context-detail"), $("#agent-recent-messages"), $("#agent-memory-size"),
    $("#agent-assistant-size"), $("#agent-ui-page-size"), $("#agent-ui-load-more"),
  ].filter(Boolean);
  if (!fields.length || fields.some((el) => el.dataset.agentPrefsBound === "1")) return;
  fields.forEach((el) => {
    el.dataset.agentPrefsBound = "1";
    el.addEventListener("change", () => {
      const id = el.id;
      const value = id === "agent-context-detail" ? el.value : Number(el.value);
      const patch = id === "agent-context-detail" ? { contextDetail: value } :
        id === "agent-recent-messages" ? { modelRecentMessages: value } :
        id === "agent-memory-size" ? { memoryMaxChars: value } :
        id === "agent-assistant-size" ? { recentAssistantMax: value } :
        id === "agent-ui-page-size" ? { uiPageSize: value } : { uiLoadMore: value };
      window.setAgentPreferences?.(patch);
      if (id === "agent-context-detail") {
        const map = { compact: { modelRecentMessages: 4, memoryMaxChars: 800 }, balanced: { modelRecentMessages: 8, memoryMaxChars: 2400 }, full: { modelRecentMessages: 16, memoryMaxChars: 6000 } };
        window.setAgentPreferences?.(map[value] || map.balanced);
        syncAgentContextPrefsUi();
      }
      if (typeof showCanvasToast === "function") showCanvasToast("智能体上下文设置已保存");
    });
  });
  syncAgentContextPrefsUi();
}

function withoutMyApiAutoOptions(list) {
  return (Array.isArray(list) ? list : []).filter(
    (x) => x && x !== "自动" && String(x).toLowerCase() !== "auto"
  );
}

function serializeMyApiFormConfig() {
  const kind = myapiActiveKind;
  const cfg = {
    kind,
    name: ($("#myapi-name")?.value || "").trim(),
    model: ($("#myapi-model-id")?.value || "").trim(),
    base_url: ($("#myapi-base-url")?.value || "").trim(),
    max_images: readMyApiLimitField("#myapi-max-images", MYAPI_DEFAULTS.max_images),
    max_videos: readMyApiLimitField("#myapi-max-videos", MYAPI_DEFAULTS.max_videos),
    max_audios: readMyApiLimitField("#myapi-max-audios", MYAPI_DEFAULTS.max_audios),
    duration_min: readMyApiLimitField("#myapi-duration-min", MYAPI_DEFAULTS.duration_min),
    duration_max: readMyApiLimitField("#myapi-duration-max", MYAPI_DEFAULTS.duration_max),
    send_duration: !!$("#myapi-send-duration")?.checked,
  };
  if (kind === "video" || kind === "image" || kind === "text" || kind === "voice") {
    cfg.api_style = $("#myapi-api-style")?.value || MYAPI_DEFAULTS.api_style;
  }
  if (kind === "agent" || kind === "vision") {
    cfg.api_style = "generic";
  }
  if (kind === "voice") {
    cfg.voice_speaker = ($("#myapi-voice-speaker")?.value || "").trim();
    cfg.voice_app_id = ($("#myapi-voice-app-id")?.value || "").trim();
    cfg.voice_format = $("#myapi-voice-format")?.value || "mp3";
    if (!cfg.api_style || cfg.api_style === "generic") cfg.api_style = "volcengine";
    if (!cfg.base_url) cfg.base_url = "https://openspeech.bytedance.com";
  }
  if (kind === "agent") {
    cfg.multimodal = !!$("#myapi-multimodal")?.checked;
    cfg.vision_model_id = cfg.multimodal ? "" : ($("#myapi-vision-model-id")?.value || "").trim();
    cfg.whisper_model = ""; // 语音走本地 Whisper，不在此配置
  }
  if (kind === "video") {
    cfg.ratios = readMyApiCheckedOptions("#myapi-ratios-group");
    cfg.resolutions = readMyApiCheckedOptions("#myapi-resolutions-group");
  }
  return cfg;
}

function parseMyApiClipText(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  let json = "";
  const prefixIdx = raw.indexOf(MYAPI_CLIP_PREFIX);
  if (prefixIdx >= 0) {
    json = raw.slice(prefixIdx + MYAPI_CLIP_PREFIX.length).trim();
  } else if (raw.startsWith("{")) {
    json = raw;
  } else {
    return null;
  }
  try {
    const data = JSON.parse(json);
    if (!data || typeof data !== "object") return null;
    if (!isMyApiKind(data.kind)) return null;
    return data;
  } catch {
    return null;
  }
}

function applyMyApiFormConfig(data) {
  if (!data) return false;
  initMyApiOptionGroups();
  const editId = ($("#myapi-edit-id")?.value || "").trim();
  const cloneFrom = ($("#myapi-clone-from")?.value || "").trim();
  const keyEl = $("#myapi-api-key");
  const keyValue = keyEl?.value || "";
  const keyPlaceholder = keyEl?.placeholder || "sk-…";

  if (isMyApiKind(data.kind)) {
    myapiActiveKind = data.kind;
  }
  document.querySelectorAll(".myapi-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.myapiKind === myapiActiveKind);
  });

  $("#myapi-name").value = data.name || "";
  $("#myapi-model-id").value = data.model || "";
  $("#myapi-base-url").value = data.base_url || "";
  if ($("#myapi-whisper-model")) $("#myapi-whisper-model").value = data.whisper_model || "";
  if ($("#myapi-multimodal")) $("#myapi-multimodal").checked = !!data.multimodal;
  populateMyApiVisionSelect(data.vision_model_id || "");
  syncAgentMultimodalUi();
  if ($("#myapi-max-images")) {
    $("#myapi-max-images").value = String(data.max_images ?? MYAPI_DEFAULTS.max_images);
  }
  if ($("#myapi-max-videos")) {
    $("#myapi-max-videos").value = String(data.max_videos ?? MYAPI_DEFAULTS.max_videos);
  }
  if ($("#myapi-max-audios")) {
    $("#myapi-max-audios").value = String(data.max_audios ?? MYAPI_DEFAULTS.max_audios);
  }
  if ($("#myapi-duration-min")) {
    $("#myapi-duration-min").value = String(data.duration_min ?? MYAPI_DEFAULTS.duration_min);
  }
  if ($("#myapi-duration-max")) {
    $("#myapi-duration-max").value = String(data.duration_max ?? MYAPI_DEFAULTS.duration_max);
  }
  if ($("#myapi-send-duration")) {
    $("#myapi-send-duration").checked = data.send_duration !== false;
  }
  if ($("#myapi-api-style")) {
    $("#myapi-api-style").value = data.api_style || MYAPI_DEFAULTS.api_style;
    window.UiSelect?.refresh($("#myapi-api-style"));
  }
  if (myapiActiveKind === "video") {
    const ratios = withoutMyApiAutoOptions(data.ratios);
    const resolutions = withoutMyApiAutoOptions(data.resolutions);
    setMyApiCheckedOptions("#myapi-ratios-group", ratios.length ? ratios : MYAPI_DEFAULT_RATIOS);
    setMyApiCheckedOptions("#myapi-resolutions-group", resolutions);
  } else {
    resetMyApiOptionChecks();
  }

  $("#myapi-edit-id").value = editId;
  $("#myapi-clone-from").value = cloneFrom;
  if (keyEl) {
    keyEl.value = keyValue;
    keyEl.placeholder = keyPlaceholder;
  }
  syncMyApiFormKindVisibility();
  syncMyApiApiStyleHint();
  return true;
}

async function copyMyApiConfigToClipboard() {
  const cfg = serializeMyApiFormConfig();
  const text = MYAPI_CLIP_PREFIX + JSON.stringify(cfg);
  try {
    const ok =
      typeof copyTextToSystemClipboard === "function"
        ? await copyTextToSystemClipboard(text)
        : false;
    if (!ok) {
      setMyApiTestStatus("复制失败，请检查浏览器权限或手动全选复制", "bad");
      return;
    }
    setMyApiTestStatus("已复制配置（不含 Key）", "ok");
    if (typeof showCanvasToast === "function") showCanvasToast("MyApi 配置已复制");
  } catch {
    setMyApiTestStatus("复制失败，请检查浏览器权限", "bad");
  }
}

async function pasteMyApiConfigFromClipboard() {
  try {
    let text = "";
    if (navigator.clipboard?.readText && window.isSecureContext) {
      try {
        text = await navigator.clipboard.readText();
      } catch {
        /* permission / insecure — fall through to prompt */
      }
    }
    if (!String(text || "").trim()) {
      text = window.prompt("粘贴 MyApi 配置文本：", "") || "";
    }
    const data = parseMyApiClipText(text);
    if (!data) {
      setMyApiTestStatus("剪贴板中没有有效的 MyApi 配置", "bad");
      return;
    }
    applyMyApiFormConfig(data);
    setMyApiTestStatus("已粘贴配置", "ok");
    if (typeof showCanvasToast === "function") showCanvasToast("MyApi 配置已粘贴");
  } catch {
    setMyApiTestStatus("粘贴失败，请检查浏览器权限", "bad");
  }
}

function handleMyApiConfigPasteEvent(e) {
  const text = e.clipboardData?.getData("text/plain") || "";
  const data = parseMyApiClipText(text);
  if (!data) return;
  e.preventDefault();
  applyMyApiFormConfig(data);
  setMyApiTestStatus("已粘贴配置", "ok");
}

/** Stop drag-select highlights on UI chrome (inputs/editors stay selectable). */
function initPreventUiTextSelection() {
  if (document.documentElement.dataset.uiSelectGuard) return;
  document.documentElement.dataset.uiSelectGuard = "1";
  const allowSel =
    "input, textarea, select, [contenteditable='true'], .prompt-editor, .node-prompt, #prompt-expand-editor, .prompt-expand-editor, .mention-menu, .myapi-style-detail-panel, .myapi-style-detail-table, .log-panel-body, .text-result-inner, .mention-chip, .qm-name, .upscale-link-field, .ui-select, .ui-select-menu, .ui-select-option, .ui-select-trigger";
  // mousedown.preventDefault() cancels HTML5 drag — never block draggable sources.
  const allowDrag =
    '[draggable="true"], .workbench-asset-item, .workbench-asset-card, .workbench-asset-block';
  const guard = (e) => {
    const t = e.target;
    if (!t?.closest) return;
    if (t.closest(allowSel)) return;
    if (e.type === "mousedown" && t.closest(allowDrag)) return;
    e.preventDefault();
  };
  document.addEventListener("mousedown", guard, true);
  document.addEventListener("selectstart", guard, true);
}

function initMyApiOptionGroups() {
  const ratiosEl = $("#myapi-ratios-group");
  const resEl = $("#myapi-resolutions-group");
  if (ratiosEl) delete ratiosEl.dataset.rendered;
  if (resEl) delete resEl.dataset.rendered;
  renderMyApiOptionGroup("#myapi-ratios-group", MYAPI_RATIO_OPTIONS);
  renderMyApiOptionGroup("#myapi-resolutions-group", MYAPI_RESOLUTION_OPTIONS);
}

function renderMyApiOptionGroup(containerSel, options) {
  const el = $(containerSel);
  if (!el || el.dataset.rendered === "1") return;
  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s);
  const escA = typeof escapeAttr === "function" ? escapeAttr : (s) => String(s);
  el.innerHTML = options
    .map(
      (opt) => `
    <label class="myapi-option-check">
      <input type="checkbox" value="${escA(opt)}" />
      <span>${esc(opt)}</span>
    </label>`
    )
    .join("");
  el.dataset.rendered = "1";
}

function readMyApiCheckedOptions(containerSel) {
  const el = $(containerSel);
  if (!el) return [];
  return [...el.querySelectorAll('input[type="checkbox"]:checked')].map((i) => i.value);
}

function setMyApiCheckedOptions(containerSel, selected) {
  const el = $(containerSel);
  if (!el) return;
  const picked = Array.isArray(selected) ? selected : [];
  const set = new Set(picked);
  el.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    cb.checked = set.has(cb.value);
  });
}

function resetMyApiOptionChecks() {
  setMyApiCheckedOptions("#myapi-ratios-group", MYAPI_DEFAULT_RATIOS);
  setMyApiCheckedOptions("#myapi-resolutions-group", MYAPI_DEFAULT_RESOLUTIONS);
}

function syncMyApiApiStyleHint() {
  const sel = $("#myapi-api-style");
  const hint = $("#myapi-api-style-hint");
  if (!sel || !hint) return;
  const style = sel.value || MYAPI_DEFAULTS.api_style;
  hint.textContent = MYAPI_API_STYLE_HINTS[style] || "";
  const durLabel = $("#myapi-send-duration-label");
  if (durLabel) {
    durLabel.textContent = "API 携带 duration";
  }
  const detailOpen = $("#btn-myapi-style-detail")?.getAttribute("aria-expanded") === "true";
  if (detailOpen) renderMyApiStyleDetail(style);
}

function renderMyApiStyleDetail(style) {
  const panel = $("#myapi-api-style-detail");
  if (!panel) return;
  const spec = MYAPI_API_STYLE_SPECS[style] || MYAPI_API_STYLE_SPECS.generic;
  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s);
  const rows = (spec.fields || [])
    .map((f) => {
      const req = f.required ? "必填" : "可选";
      const reqCls = f.required ? "is-req" : "is-opt";
      return `<tr>
        <td><code>${esc(f.name)}</code></td>
        <td><span class="myapi-type-tag">${esc(f.type)}</span></td>
        <td><span class="myapi-req-tag ${reqCls}">${req}</span></td>
        <td>${esc(f.desc)}</td>
      </tr>`;
    })
    .join("");
  panel.innerHTML = `
    <div class="myapi-style-detail-head">
      <strong>${esc(spec.title)}</strong>
      <span class="myapi-style-detail-meta">${esc(spec.create)}</span>
      <span class="myapi-style-detail-meta">${esc(spec.poll)}</span>
    </div>
    <p class="myapi-style-detail-note">${esc(spec.note || "")}</p>
    <div class="myapi-style-detail-table-wrap">
      <table class="myapi-style-detail-table">
        <thead>
          <tr>
            <th>字段</th>
            <th>类型</th>
            <th>必填</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function setMyApiStyleDetailOpen(open) {
  const btn = $("#btn-myapi-style-detail");
  const panel = $("#myapi-api-style-detail");
  if (!btn || !panel) return;
  const show = !!open;
  btn.setAttribute("aria-expanded", show ? "true" : "false");
  btn.classList.toggle("is-open", show);
  btn.textContent = show ? "收起" : "详情";
  panel.classList.toggle("hidden", !show);
  panel.hidden = !show;
  if (show) {
    const style = $("#myapi-api-style")?.value || MYAPI_DEFAULTS.api_style;
    renderMyApiStyleDetail(style);
  }
}

function toggleMyApiStyleDetail() {
  const btn = $("#btn-myapi-style-detail");
  const open = btn?.getAttribute("aria-expanded") !== "true";
  setMyApiStyleDetailOpen(open);
}

function syncMyApiFormKindVisibility() {
  const videoFields = $("#myapi-video-fields");
  const styleWrap = $("#myapi-api-style-wrap");
  const agentFields = $("#myapi-agent-fields");
  const voiceFields = $("#myapi-voice-fields");
  const advanced = $("#myapi-advanced-fields");
  const isVideo = myapiActiveKind === "video";
  const isAgent = myapiActiveKind === "agent";
  const isVision = myapiActiveKind === "vision";
  const isVoice = myapiActiveKind === "voice";
  // 图/视/文/语音可选接口风格（火山引擎等）；智能体 / 视觉走通用 chat
  const showStyle = isVideo || myapiActiveKind === "image" || myapiActiveKind === "text" || isVoice;
  if (videoFields) videoFields.classList.toggle("hidden", !isVideo);
  if (styleWrap) styleWrap.classList.toggle("hidden", !showStyle);
  if (agentFields) agentFields.classList.toggle("hidden", !isAgent);
  if (voiceFields) voiceFields.classList.toggle("hidden", !isVoice);
  if (advanced) advanced.classList.toggle("hidden", isAgent || isVision);
  const whisperField = $("#myapi-whisper-model")?.closest("label.myapi-field");
  if (whisperField) whisperField.classList.add("hidden"); // 语音改本地 Whisper，不在智能体里配
  const kindLabel = $("#myapi-form-kind-label");
  if (kindLabel) kindLabel.textContent = myapiKindLabel(myapiActiveKind);
  if (isAgent) {
    bindAgentContextPrefs();
    syncAgentContextPrefsUi();
    populateMyApiVisionSelect($("#myapi-vision-model-id")?.value || "");
    syncAgentMultimodalUi();
  }
  if (showStyle) syncMyApiApiStyleHint();
  if (isVoice) {
    const style = $("#myapi-api-style")?.value;
    if (!style || style === "generic") {
      if ($("#myapi-api-style")) $("#myapi-api-style").value = "volcengine";
      applyMyApiStyleBaseUrl("volcengine", { force: !($("#myapi-base-url")?.value || "").trim() });
      // Voice defaults to openspeech host
      const urlEl = $("#myapi-base-url");
      if (urlEl && (!(urlEl.value || "").trim() || /ark\.cn-beijing\.volces\.com/i.test(urlEl.value))) {
        urlEl.value = "https://openspeech.bytedance.com";
      }
    }
  }
}

function suggestMyApiCopyName(baseName) {
  const base = String(baseName || "").trim() || "模型";
  const names = new Set(
    myapiCache.filter((m) => m.kind === myapiActiveKind).map((m) => String(m.name || "").trim())
  );
  let candidate = `${base} 副本`;
  if (!names.has(candidate)) return candidate;
  for (let i = 2; i < 100; i += 1) {
    candidate = `${base} ${i}`;
    if (!names.has(candidate)) return candidate;
  }
  return `${base} 副本`;
}

function openMyApiDuplicateModal(source) {
  if (!source) return;
  initMyApiOptionGroups();
  if (source.kind && isMyApiKind(source.kind)) {
    myapiActiveKind = source.kind;
  }
  fillMyApiForm(source);
  $("#myapi-edit-id").value = "";
  $("#myapi-clone-from").value = source.id || "";
  $("#myapi-name").value = suggestMyApiCopyName(source.name || "");
  $("#form-myapi-model")?.classList.remove("is-editing");
  const saveBtn = $("#btn-myapi-save");
  if (saveBtn) saveBtn.textContent = "添加";
  const title = $("#myapi-form-title") || document.querySelector(".myapi-form-title");
  if (title) title.textContent = "添加模型";
  const keyEl = $("#myapi-api-key");
  if (keyEl) {
    keyEl.value = "";
    keyEl.placeholder = source.api_key_set
      ? "将自动沿用原模型的已保存 Key"
      : "sk-…";
  }
  setMyApiTestStatus("", null);
  setMyApiStyleDetailOpen(false);
  $("#myapi-template-wrap")?.classList.remove("hidden");
  const dlg = $("#modal-myapi-model");
  if (dlg) revealOverlay(dlg);
  void loadMyApiTemplatesForKind(myapiActiveKind).then(() => ensureMyApiTemplateSelect());
  requestAnimationFrame(() => {
    const sel = $("#myapi-api-style");
    if (sel) {
      window.UiSelect?.enhance(sel);
      window.UiSelect?.refresh(sel);
    }
    ensureMyApiTemplateSelect();
    const nameEl = $("#myapi-name");
    nameEl?.focus();
    nameEl?.select();
  });
}

function closeMyApiModelModal() {
  setMyApiStyleDetailOpen(false);
  dismissOverlay($("#modal-myapi-model"));
}

function ensureMyApiTemplateSelect() {
  const sel = $("#myapi-template-select");
  if (!sel) return null;
  window.UiSelect?.enhance(sel);
  window.UiSelect?.refresh(sel);
  return sel;
}

function openMyApiModelModal(item = null) {
  syncMyApiApiStyleSelectOptions(item?.api_style || "");
  initMyApiOptionGroups();
  if (item) fillMyApiForm(item);
  else resetMyApiForm();
  setMyApiTestStatus("", null);
  setMyApiStyleDetailOpen(false);
  const tplWrap = $("#myapi-template-wrap");
  if (tplWrap) tplWrap.classList.add("hidden");
  const dlg = $("#modal-myapi-model");
  if (dlg) revealOverlay(dlg);
  void loadMyApiTemplatesForKind(item?.kind || myapiActiveKind).then(() => {
    const sel = ensureMyApiTemplateSelect();
    if (sel) sel.value = "";
    window.UiSelect?.refresh(sel);
    document.querySelectorAll(".ui-select.is-open").forEach((n) => n.classList.remove("is-open"));
  });
  requestAnimationFrame(() => {
    const dlg = $("#modal-myapi-model");
    if (dlg) window.UiSelect?.enhanceAll?.(dlg);
    document.querySelectorAll(".ui-select.is-open").forEach((n) => n.classList.remove("is-open"));
    const styleSel = $("#myapi-api-style");
    if (styleSel) {
      window.UiSelect?.enhance(styleSel);
      window.UiSelect?.refresh(styleSel);
    }
    if (!item) ensureMyApiTemplateSelect();
    syncAgentContextPrefsUi();
  });
}

function readMyApiLimitField(id, fallback) {
  const el = $(id);
  if (!el) return fallback;
  const n = parseInt(el.value, 10);
  return Number.isFinite(n) ? n : fallback;
}

function resetMyApiForm() {
  const idEl = $("#myapi-edit-id");
  const nameEl = $("#myapi-name");
  const modelEl = $("#myapi-model-id");
  const urlEl = $("#myapi-base-url");
  const keyEl = $("#myapi-api-key");
  const saveBtn = $("#btn-myapi-save");
  if (idEl) idEl.value = "";
  if ($("#myapi-clone-from")) $("#myapi-clone-from").value = "";
  if (nameEl) nameEl.value = "";
  if (modelEl) modelEl.value = "";
  if (urlEl) urlEl.value = "";
  if (keyEl) {
    keyEl.value = "";
    keyEl.placeholder = "sk-…";
  }
  if ($("#myapi-max-images")) $("#myapi-max-images").value = String(MYAPI_DEFAULTS.max_images);
  if ($("#myapi-max-videos")) $("#myapi-max-videos").value = String(MYAPI_DEFAULTS.max_videos);
  if ($("#myapi-max-audios")) $("#myapi-max-audios").value = String(MYAPI_DEFAULTS.max_audios);
  if ($("#myapi-duration-min")) $("#myapi-duration-min").value = String(MYAPI_DEFAULTS.duration_min);
  if ($("#myapi-duration-max")) $("#myapi-duration-max").value = String(MYAPI_DEFAULTS.duration_max);
  if ($("#myapi-send-duration")) $("#myapi-send-duration").checked = MYAPI_DEFAULTS.send_duration;
  if ($("#myapi-api-style")) $("#myapi-api-style").value = MYAPI_DEFAULTS.api_style;
  if ($("#myapi-whisper-model")) $("#myapi-whisper-model").value = "";
  if ($("#myapi-multimodal")) $("#myapi-multimodal").checked = false;
  if ($("#myapi-voice-speaker")) $("#myapi-voice-speaker").value = "";
  if ($("#myapi-voice-app-id")) $("#myapi-voice-app-id").value = "";
  if ($("#myapi-voice-format")) $("#myapi-voice-format").value = "mp3";
  populateMyApiVisionSelect("");
  syncAgentMultimodalUi();
  window.UiSelect?.refresh($("#myapi-api-style"));
  window.UiSelect?.refresh($("#myapi-voice-format"));
  setMyApiStyleDetailOpen(false);
  resetMyApiOptionChecks();
  syncMyApiFormKindVisibility();
  // 新建时不默认填入任何供应商 URL（选推荐配置 / 改接口风格时再填）
  if (urlEl) urlEl.value = "";
  if (saveBtn) saveBtn.textContent = "添加";
  $("#form-myapi-model")?.classList.remove("is-editing");
  const title = $("#myapi-form-title") || document.querySelector(".myapi-form-title");
  if (title) title.textContent = "添加模型";
}

function fillMyApiForm(item) {
  if (!item) return resetMyApiForm();
  if (item.kind && isMyApiKind(item.kind)) {
    myapiActiveKind = item.kind;
  }
  $("#myapi-edit-id").value = item.id || "";
  if ($("#myapi-clone-from")) $("#myapi-clone-from").value = "";
  $("#myapi-name").value = item.name || "";
  $("#myapi-model-id").value = item.model || "";
  $("#myapi-base-url").value = item.base_url || "";
  if ($("#myapi-whisper-model")) $("#myapi-whisper-model").value = item.whisper_model || "";
  if ($("#myapi-multimodal")) $("#myapi-multimodal").checked = !!item.multimodal;
  populateMyApiVisionSelect(item.vision_model_id || "");
  syncAgentMultimodalUi();
  if ($("#myapi-max-images")) {
    $("#myapi-max-images").value = String(item.max_images ?? MYAPI_DEFAULTS.max_images);
  }
  if ($("#myapi-max-videos")) {
    $("#myapi-max-videos").value = String(item.max_videos ?? MYAPI_DEFAULTS.max_videos);
  }
  if ($("#myapi-max-audios")) {
    $("#myapi-max-audios").value = String(item.max_audios ?? MYAPI_DEFAULTS.max_audios);
  }
  if ($("#myapi-duration-min")) {
    $("#myapi-duration-min").value = String(item.duration_min ?? MYAPI_DEFAULTS.duration_min);
  }
  if ($("#myapi-duration-max")) {
    $("#myapi-duration-max").value = String(item.duration_max ?? MYAPI_DEFAULTS.duration_max);
  }
  if ($("#myapi-send-duration")) {
    $("#myapi-send-duration").checked = item.send_duration !== false;
  }
  if ($("#myapi-api-style")) {
    $("#myapi-api-style").value = item.api_style || MYAPI_DEFAULTS.api_style;
    window.UiSelect?.refresh($("#myapi-api-style"));
  }
  if ($("#myapi-voice-speaker")) $("#myapi-voice-speaker").value = item.voice_speaker || "";
  if ($("#myapi-voice-app-id")) $("#myapi-voice-app-id").value = item.voice_app_id || "";
  if ($("#myapi-voice-format")) {
    $("#myapi-voice-format").value = item.voice_format || "mp3";
    window.UiSelect?.refresh($("#myapi-voice-format"));
  }
  setMyApiCheckedOptions(
    "#myapi-ratios-group",
    (() => {
      const ratios = withoutMyApiAutoOptions(item.ratios);
      return ratios.length ? ratios : MYAPI_DEFAULT_RATIOS;
    })()
  );
  setMyApiCheckedOptions("#myapi-resolutions-group", withoutMyApiAutoOptions(item.resolutions));
  syncMyApiFormKindVisibility();
  syncMyApiApiStyleHint();
  const keyEl = $("#myapi-api-key");
  if (keyEl) {
    keyEl.value = "";
    keyEl.placeholder = item.api_key_set
      ? `已保存 ${item.api_key_masked || "****"}（留空不改）`
      : "sk-…";
  }
  const saveBtn = $("#btn-myapi-save");
  if (saveBtn) saveBtn.textContent = "保存";
  $("#form-myapi-model")?.classList.add("is-editing");
  const title = $("#myapi-form-title") || document.querySelector(".myapi-form-title");
  if (title) title.textContent = "编辑模型";
}

function formatMyApiItemMeta(m) {
  const parts = [];
  if (m.kind === "video" || m.kind === "image" || m.kind === "text" || m.kind === "agent" || m.kind === "vision") {
    const style = MYAPI_API_STYLE_LABELS[m.api_style] || m.api_style;
    if (style) parts.push(style);
  }
  if (m.kind === "agent") {
    if (m.multimodal) parts.push("多模态");
    else if (m.vision_model_id) {
      const v = myapiCache.find((x) => x.id === m.vision_model_id);
      parts.push(`视觉 ${v?.name || m.vision_model_id}`);
    }
    if (!parts.length) parts.push("对话智能体 · 本地 Whisper");
    return parts.join(" · ");
  }
  if (m.kind === "vision") {
    parts.push("视觉理解");
    return parts.join(" · ");
  }
  if (m.kind === "video") {
    parts.push(`时长 ${m.duration_min ?? 4}–${m.duration_max ?? 10}s`);
    if (Array.isArray(m.ratios) && m.ratios.length) {
      parts.push(`比例 ${m.ratios.join(" ")}`);
    }
    if (Array.isArray(m.resolutions) && m.resolutions.length) {
      parts.push(m.resolutions.join("/"));
    }
  }
  parts.push(`图x${m.max_images ?? 9} · 视频x${m.max_videos ?? 3} · 音频x${m.max_audios ?? 3}`);
  return parts.join(" · ");
}

function renderMyApiList() {
  const list = $("#myapi-model-list");
  if (!list) return;
  const prevScroll = list.scrollTop;
  const items = myapiCache.filter((m) => m.kind === myapiActiveKind);
  if (!items.length) {
    list.innerHTML = `<div class="myapi-empty">还没有模型，在下方填入后点「添加」</div>`;
    return;
  }
  list.innerHTML = items
    .map(
      (m) => `
    <div class="myapi-item${m.is_default ? " is-default" : ""}" data-id="${escapeAttr(m.id)}">
      <div class="myapi-item-main">
        <div class="myapi-item-name">${escapeHtml(m.name)}${
          m.is_default ? `<span class="myapi-default-badge" title="新建节点默认使用此配置">默认</span>` : ""
        }</div>
        <div class="myapi-item-meta">${escapeHtml(m.model || "")} · ${escapeHtml(m.base_url || "")}${
          m.api_key_set ? ` · Key ${escapeHtml(m.api_key_masked || "****")}` : " · 未设密钥"
        }</div>
        <div class="myapi-item-meta myapi-item-limits">${escapeHtml(formatMyApiItemMeta(m))}</div>
        <div class="myapi-item-test-status" data-myapi-test-status="${escapeAttr(m.id)}" aria-live="polite"></div>
      </div>
      <div class="myapi-item-actions">
        <button type="button" class="btn btn-ghost btn-sm${m.is_default ? " is-default-on" : ""}" data-myapi-default="${escapeAttr(m.id)}" title="${
          m.is_default ? "取消默认，新建节点恢复原先通道优先级" : "设为默认：新建图/视频/文本节点优先用此 MyApi 配置（含管理员 BenDi）"
        }">${m.is_default ? "取消默认" : "设为默认"}</button>
        <button type="button" class="btn btn-ghost btn-sm" data-myapi-dup="${escapeAttr(m.id)}" title="以此配置新建模型">复制</button>
        <button type="button" class="btn btn-ghost btn-sm" data-myapi-test="${escapeAttr(m.id)}">测试</button>
        <button type="button" class="btn btn-ghost btn-sm" data-myapi-edit="${escapeAttr(m.id)}">编辑</button>
        <button type="button" class="btn btn-ghost btn-sm" data-myapi-del="${escapeAttr(m.id)}">删除</button>
      </div>
    </div>`
    )
    .join("");
  list.scrollTop = prevScroll;
}

async function refreshMyApiSettingsUI() {
  try {
    const data = await api("/api/myapi/models");
    myapiCache = Array.isArray(data?.models) ? data.models : [];
  } catch {
    myapiCache = [];
  }
  document.querySelectorAll(".myapi-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.myapiKind === myapiActiveKind);
  });
  syncMyApiVolcengineTabUI();
  if (myapiActiveKind === "volcengine") {
    void loadVolcengineSettingsUI();
    return;
  }
  renderMyApiList();
  syncMyApiFormKindVisibility();
  void loadMyApiTemplatesForKind(myapiActiveKind);
  if (typeof applyMyApiModelsToCanvas === "function") {
    await applyMyApiModelsToCanvas();
  } else if (typeof refreshMyApiModels === "function") {
    await refreshMyApiModels();
    if (typeof refreshMyApiNodeToolbars === "function") refreshMyApiNodeToolbars();
    if (typeof refreshWorkbenchMyApiToolbars === "function") refreshWorkbenchMyApiToolbars();
  }
}

let myapiTemplatesCache = [];

async function loadMyApiTemplatesForKind(kind) {
  return;
  const sel = $("#myapi-template-select");
  if (!sel) return;
  try {
    const data = await api(`/api/myapi-templates?kind=${encodeURIComponent(kind || "image")}`);
    myapiTemplatesCache = Array.isArray(data?.templates) ? data.templates : [];
  } catch {
    myapiTemplatesCache = [];
  }
  const cur = sel.value;
  sel.innerHTML =
    `<option value="">手动填写 / 不使用推荐</option>` +
    myapiTemplatesCache
      .map(
        (t) =>
          `<option value="${escapeAttr(t.id)}">${escapeHtml(t.name)}${t.description ? ` · ${escapeHtml(t.description)}` : ""}</option>`
      )
      .join("");
  if (cur && myapiTemplatesCache.some((t) => t.id === cur)) sel.value = cur;
  ensureMyApiTemplateSelect();
}

function applyMyApiTemplate(templateId) {
  const t = myapiTemplatesCache.find((x) => x.id === templateId);
  if (!t) return;
  if (t.kind && isMyApiKind(t.kind)) {
    myapiActiveKind = t.kind;
    document.querySelectorAll(".myapi-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.myapiKind === myapiActiveKind);
    });
  }
  $("#myapi-name").value = t.name || "";
  $("#myapi-model-id").value = t.model || "";
  $("#myapi-base-url").value = t.base_url || "";
  if ($("#myapi-max-images")) $("#myapi-max-images").value = String(t.max_images ?? MYAPI_DEFAULTS.max_images);
  if ($("#myapi-max-videos")) $("#myapi-max-videos").value = String(t.max_videos ?? MYAPI_DEFAULTS.max_videos);
  if ($("#myapi-max-audios")) $("#myapi-max-audios").value = String(t.max_audios ?? MYAPI_DEFAULTS.max_audios);
  if ($("#myapi-duration-min")) $("#myapi-duration-min").value = String(t.duration_min ?? MYAPI_DEFAULTS.duration_min);
  if ($("#myapi-duration-max")) $("#myapi-duration-max").value = String(t.duration_max ?? MYAPI_DEFAULTS.duration_max);
  if ($("#myapi-send-duration")) $("#myapi-send-duration").checked = t.send_duration !== false;
  if ($("#myapi-api-style")) {
    $("#myapi-api-style").value = t.api_style || MYAPI_DEFAULTS.api_style;
    window.UiSelect?.refresh($("#myapi-api-style"));
  }
  setMyApiCheckedOptions(
    "#myapi-ratios-group",
    (() => {
      const ratios = withoutMyApiAutoOptions(t.ratios);
      return ratios.length ? ratios : MYAPI_DEFAULT_RATIOS;
    })()
  );
  setMyApiCheckedOptions("#myapi-resolutions-group", withoutMyApiAutoOptions(t.resolutions));
  syncMyApiFormKindVisibility();
  const keyEl = $("#myapi-api-key");
  if (keyEl) {
    keyEl.value = "";
    keyEl.placeholder = "请填写你的 API Key";
    keyEl.focus();
  }
}

document.querySelectorAll(".myapi-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    myapiActiveKind = btn.dataset.myapiKind || "image";
    closeMyApiModelModal();
    resetMyApiForm();
    document.querySelectorAll(".myapi-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.myapiKind === myapiActiveKind);
    });
    syncMyApiVolcengineTabUI();
    if (myapiActiveKind === "volcengine") {
      void loadVolcengineSettingsUI();
      return;
    }
    syncMyApiFormKindVisibility();
    renderMyApiList();
    void loadMyApiTemplatesForKind(myapiActiveKind);
  });
});

function setMyApiTestStatus(text, state, statusEl) {
  const el = statusEl || $("#myapi-test-status");
  if (!el) return;
  el.textContent = text || "";
  el.classList.remove("is-ok", "is-bad", "is-pending");
  if (state) el.classList.add(`is-${state}`);
}

async function runMyApiTest(opts = {}) {
  const baseUrl = (opts.base_url || $("#myapi-base-url")?.value || "").trim();
  const apiKey = (opts.api_key || $("#myapi-api-key")?.value || "").trim();
  const model =
    (opts.model || $("#myapi-model-id")?.value || $("#myapi-name")?.value || "").trim();
  const editId = (opts.id || $("#myapi-edit-id")?.value || "").trim();
  const cloneFrom = (opts.clone_from || $("#myapi-clone-from")?.value || "").trim();
  const kind = opts.kind || myapiActiveKind;
  const statusEl = opts.statusEl || null;
  const report = (text, state) => {
    setMyApiTestStatus(text, state, statusEl);
    if (opts.toast && text && typeof showCanvasToast === "function") {
      showCanvasToast(text);
    }
  };

  if (!baseUrl && !editId && !cloneFrom) {
    report("请先填写 Base URL", "bad");
    return;
  }
  if (!apiKey && !editId && !cloneFrom) {
    report("请先填写 API Key", "bad");
    return;
  }

  report("正在测试连接…", "pending");
  const btn = opts.button || $("#btn-myapi-test");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/myapi/test", {
      method: "POST",
      body: JSON.stringify({
        id: editId || undefined,
        clone_from: cloneFrom || undefined,
        base_url: baseUrl,
        api_key: apiKey,
        model: model || undefined,
        kind,
        api_style:
          kind === "video" || kind === "image" || kind === "text" || kind === "voice"
            ? opts.api_style || $("#myapi-api-style")?.value || MYAPI_DEFAULTS.api_style
            : undefined,
        voice_speaker: kind === "voice" ? ($("#myapi-voice-speaker")?.value || "").trim() : undefined,
        voice_app_id: kind === "voice" ? ($("#myapi-voice-app-id")?.value || "").trim() : undefined,
        voice_format: kind === "voice" ? $("#myapi-voice-format")?.value || "mp3" : undefined,
        duration_min:
          kind === "video"
            ? opts.duration_min ??
              readMyApiLimitField("#myapi-duration-min", MYAPI_DEFAULTS.duration_min)
            : undefined,
        duration_max:
          kind === "video"
            ? opts.duration_max ??
              readMyApiLimitField("#myapi-duration-max", MYAPI_DEFAULTS.duration_max)
            : undefined,
      }),
    });
    report(data.message || (data.ok ? "连接成功" : "连接失败"), data.ok ? "ok" : "bad");
  } catch (err) {
    let msg = err.message || "测试失败";
    if (err.status === 404 || /^not found$/i.test(String(msg).trim())) {
      msg = "测试接口不可用，请重启后端后再试";
    }
    report(msg, "bad");
  } finally {
    if (btn) btn.disabled = false;
  }
}

$("#btn-myapi-add")?.addEventListener("click", () => {
  openMyApiModelModal(null);
});

$("#myapi-template-select")?.addEventListener("change", (e) => {
  const id = e.target.value;
  if (id) applyMyApiTemplate(id);
});

$("#myapi-api-style")?.addEventListener("change", () => {
  const style = $("#myapi-api-style")?.value || MYAPI_DEFAULTS.api_style;
  // 用户主动切换接口风格时才填入该风格的默认 URL
  applyMyApiStyleBaseUrl(style, { force: true });
  if (style === "volcengine" && myapiActiveKind === "voice") {
    const urlEl = $("#myapi-base-url");
    if (urlEl) urlEl.value = "https://openspeech.bytedance.com";
  }
  syncMyApiApiStyleHint();
});
$("#myapi-multimodal")?.addEventListener("change", syncAgentMultimodalUi);
bindAgentContextPrefs();
$("#btn-myapi-style-detail")?.addEventListener("click", toggleMyApiStyleDetail);

$("#btn-myapi-close")?.addEventListener("click", () => {
  closeMyApiModelModal();
  resetMyApiForm();
  setMyApiTestStatus("", null);
});

$("#btn-myapi-cancel")?.addEventListener("click", () => {
  closeMyApiModelModal();
  resetMyApiForm();
  setMyApiTestStatus("", null);
});

$("#modal-myapi-model")?.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeMyApiModelModal();
  resetMyApiForm();
  setMyApiTestStatus("", null);
});

$("#btn-myapi-test")?.addEventListener("click", () => {
  void runMyApiTest();
});

$("#btn-myapi-copy-config")?.addEventListener("click", () => {
  void copyMyApiConfigToClipboard();
});

$("#btn-myapi-paste-config")?.addEventListener("click", () => {
  void pasteMyApiConfigFromClipboard();
});

$("#form-myapi-model")?.addEventListener("paste", handleMyApiConfigPasteEvent);

$("#form-myapi-model")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  e.stopPropagation();
  const name = ($("#myapi-name")?.value || "").trim();
  const model = ($("#myapi-model-id")?.value || "").trim();
  const baseUrl = ($("#myapi-base-url")?.value || "").trim().replace(/\/+$/, "");
  const apiKey = ($("#myapi-api-key")?.value || "").trim();
  const editId = ($("#myapi-edit-id")?.value || "").trim();
  const cloneFrom = ($("#myapi-clone-from")?.value || "").trim();
  if (!name || !baseUrl) {
    setMyApiTestStatus("请填写显示名称与 Base URL", "bad");
    return;
  }
  let durationMin = readMyApiLimitField("#myapi-duration-min", MYAPI_DEFAULTS.duration_min);
  let durationMax = readMyApiLimitField("#myapi-duration-max", MYAPI_DEFAULTS.duration_max);
  if (durationMax < durationMin) [durationMin, durationMax] = [durationMax, durationMin];
  if (myapiActiveKind === "video") {
    const ratios = readMyApiCheckedOptions("#myapi-ratios-group");
    const resolutions = readMyApiCheckedOptions("#myapi-resolutions-group");
    if (!ratios.length) {
      setMyApiTestStatus("请至少勾选一个比例", "bad");
      return;
    }
    if (!resolutions.length) {
      setMyApiTestStatus("请至少勾选一个分辨率", "bad");
      return;
    }
  }
  const saveBtn = $("#btn-myapi-save");
  if (saveBtn) saveBtn.disabled = true;
  setMyApiTestStatus("保存中…", "pending");
  try {
    const body = {
      id: editId || undefined,
      kind: myapiActiveKind,
      name,
      model: model || name,
      base_url: baseUrl,
      api_key: apiKey,
      max_images: readMyApiLimitField("#myapi-max-images", MYAPI_DEFAULTS.max_images),
      max_videos: readMyApiLimitField("#myapi-max-videos", MYAPI_DEFAULTS.max_videos),
      max_audios: readMyApiLimitField("#myapi-max-audios", MYAPI_DEFAULTS.max_audios),
      duration_min: durationMin,
      duration_max: durationMax,
      send_duration: !!$("#myapi-send-duration")?.checked,
    };
    if (
      myapiActiveKind === "video" ||
      myapiActiveKind === "image" ||
      myapiActiveKind === "text" ||
      myapiActiveKind === "voice"
    ) {
      body.api_style = $("#myapi-api-style")?.value || MYAPI_DEFAULTS.api_style;
    }
    if (myapiActiveKind === "agent" || myapiActiveKind === "vision") {
      body.api_style = "generic";
    }
    if (myapiActiveKind === "voice") {
      body.voice_speaker = ($("#myapi-voice-speaker")?.value || "").trim();
      body.voice_app_id = ($("#myapi-voice-app-id")?.value || "").trim();
      body.voice_format = $("#myapi-voice-format")?.value || "mp3";
      if (!body.api_style || body.api_style === "generic") body.api_style = "volcengine";
    }
    if (myapiActiveKind === "agent") {
      body.multimodal = !!$("#myapi-multimodal")?.checked;
      body.vision_model_id = body.multimodal ? "" : ($("#myapi-vision-model-id")?.value || "").trim();
      body.whisper_model = "";
      body.vision_model = "";
    }
    if (myapiActiveKind === "video") {
      body.ratios = readMyApiCheckedOptions("#myapi-ratios-group");
      body.resolutions = readMyApiCheckedOptions("#myapi-resolutions-group");
    }
    if (cloneFrom && !editId) body.clone_from = cloneFrom;
    await api("/api/myapi/models", {
      method: "POST",
      body: JSON.stringify(body),
    });
    setMyApiTestStatus("", null);
    closeMyApiModelModal();
    resetMyApiForm();
    await refreshMyApiSettingsUI();
    if (typeof window.AgentPanel?.refreshModelSelect === "function") {
      void window.AgentPanel.refreshModelSelect();
    }
    if (typeof showCanvasToast === "function") {
      showCanvasToast(editId ? "已保存 MyApi 模型" : "已添加 MyApi 模型");
    }
  } catch (err) {
    setMyApiTestStatus(err.message || "保存失败", "bad");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
});

$("#myapi-model-list")?.addEventListener("click", async (e) => {
  const defaultId = e.target.closest("[data-myapi-default]")?.dataset?.myapiDefault;
  if (defaultId) {
    e.preventDefault();
    e.stopPropagation();
    const item = myapiCache.find((m) => m.id === defaultId);
    if (!item) return;
    const nextDefault = !item.is_default;
    const btn = e.target.closest("[data-myapi-default]");
    if (btn) btn.disabled = true;
    try {
      await api(`/api/myapi/models/${encodeURIComponent(defaultId)}/default`, {
        method: "POST",
        body: JSON.stringify({ is_default: nextDefault }),
      });
      await refreshMyApiSettingsUI();
      if (typeof showCanvasToast === "function") {
        showCanvasToast(
          nextDefault
            ? `已设为默认：新建${
                item.kind === "video"
                  ? "视频"
                  : item.kind === "text"
                    ? "文本"
                    : item.kind === "agent"
                      ? "智能体"
                      : item.kind === "vision"
                        ? "视觉"
                        : "图片"
              }节点将优先用「${item.name}」`
            : "已取消默认，新建节点恢复原先通道显示"
        );
      }
    } catch (err) {
      await AppDialog.alert(err.message || "设置失败", { title: "MyApi" });
    } finally {
      if (btn) btn.disabled = false;
    }
    return;
  }
  const dupId = e.target.closest("[data-myapi-dup]")?.dataset?.myapiDup;
  if (dupId) {
    e.preventDefault();
    e.stopPropagation();
    const item = myapiCache.find((m) => m.id === dupId);
    if (item) openMyApiDuplicateModal(item);
    return;
  }
  const testBtn = e.target.closest("[data-myapi-test]");
  const testId = testBtn?.dataset?.myapiTest;
  if (testId) {
    e.preventDefault();
    e.stopPropagation();
    const item = myapiCache.find((m) => m.id === testId);
    if (!item) return;
    const statusEl =
      e.currentTarget.querySelector(`[data-myapi-test-status="${CSS.escape(testId)}"]`) || null;
    await runMyApiTest({
      id: item.id,
      base_url: item.base_url,
      model: item.model || item.name,
      kind: item.kind,
      api_style: item.api_style,
      duration_min: item.duration_min,
      duration_max: item.duration_max,
      api_key: "",
      statusEl,
      button: testBtn,
      toast: true,
    });
    return;
  }
  const editId = e.target.closest("[data-myapi-edit]")?.dataset?.myapiEdit;
  if (editId) {
    const item = myapiCache.find((m) => m.id === editId);
    if (!item) return;
    openMyApiModelModal(item);
    return;
  }
  const delId = e.target.closest("[data-myapi-del]")?.dataset?.myapiDel;
  if (!delId) return;
  const item = myapiCache.find((m) => m.id === delId);
  const ok = await AppDialog.confirm(`确定删除模型「${item?.name || delId}」？`, {
    title: "删除 MyApi 模型",
  });
  if (!ok) return;
  try {
    await api(`/api/myapi/models/${encodeURIComponent(delId)}`, { method: "DELETE" });
    if (($("#myapi-edit-id")?.value || "") === delId) {
      closeMyApiModelModal();
      resetMyApiForm();
    }
    setMyApiTestStatus("", null);
    await refreshMyApiSettingsUI();
    if (typeof showCanvasToast === "function") showCanvasToast("已删除");
  } catch (err) {
    await AppDialog.alert(err.message || "删除失败", { title: "MyApi" });
  }
});

loadAppConfig().then(async () => {
  if (window.CanvasAuth) await window.CanvasAuth.bootstrapSession();
  // Non-blocking: account/task bootstrap must not contend with canvas boot / enter-board.
  if (typeof window.bootstrapAuthenticatedApp === "function") {
    void window.bootstrapAuthenticatedApp().catch((err) => {
      console.warn("bootstrapAuthenticatedApp:", err);
    });
  }
});

let _appConfigRefreshAt = 0;
async function refreshAppConfigIfStale(force = false) {
  const now = Date.now();
  if (!force && now - _appConfigRefreshAt < 2500) return;
  _appConfigRefreshAt = now;
  try {
    await loadAppConfig();
  } catch {
    /* ignore */
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void refreshAppConfigIfStale(true);
});
window.addEventListener("focus", () => {
  void refreshAppConfigIfStale(true);
});
// Admin may toggle 超分 while canvas stays open — pick up without full reload
setInterval(() => {
  if (document.visibilityState === "visible") void refreshAppConfigIfStale();
}, 8000);

initPreventUiTextSelection();
initLogPanel();
refreshThreadSettingsUI();
updateThreadBadge();

function recoverGenerationQueue() {
  if (!window.CanvasStore) return;
  CanvasStore.boards.forEach((b) => {
    b.nodes.forEach((n) => {
      if (n.type !== "image" && n.type !== "video") return;
      if (n.status === "waiting") {
        enqueueGeneration(n.id);
      } else if (
        (n.status === "pending" || n.status === "generating") &&
        !n.taskId &&
        n._genPayload &&
        !isGenInFlight(n.id) &&
        shouldRecoverStuckGeneration(n)
      ) {
        CanvasStore.updateNode(n.id, { status: "waiting" });
        enqueueGeneration(n.id);
      }
    });
  });
  window.recoverWorkbenchGenerationQueue?.();
  pumpGenQueue();
  updateThreadBadge();
}

window.syncAllNodesFromTasks = syncAllNodesFromTasks;
window.ensureTaskPolling = ensureTaskPolling;
window.reconcileStuckGenerationNodes = reconcileStuckGenerationNodes;
window.recoverGenerationQueue = recoverGenerationQueue;

function initLogPanel() {
  const btn = $("#btn-logs");
  const panel = $("#log-panel");
  const body = $("#log-body");
  const clearBtn = $("#btn-log-clear");
  const closeBtn = $("#btn-log-close");
  if (!btn || !panel || !body) return;

  let lastId = 0;
  let timer = null;
  let stickBottom = true;
  let activeBoardId = "";

  const boardQuery = () => {
    const bid = window.CanvasStore?.activeBoardId || "";
    return bid ? `&board_id=${encodeURIComponent(bid)}` : "";
  };

  const resetForBoard = (boardId = "") => {
    activeBoardId = boardId || window.CanvasStore?.activeBoardId || "";
    lastId = 0;
    body.textContent = "暂无日志";
    if (isOverlayOpen(panel)) pull(true);
  };
  window.resetLogPanelForBoard = resetForBoard;

  body.addEventListener("scroll", () => {
    stickBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 24;
  });

  const render = (logs, append) => {
    if (!append) body.innerHTML = "";
    if (!logs.length && !body.textContent.trim()) {
      body.textContent = "暂无日志";
      return;
    }
    if (body.textContent === "暂无日志" && logs.length) body.textContent = "";
    const frag = document.createDocumentFragment();
    logs.forEach((row) => {
      const line = document.createElement("div");
      const lv = String(row.level || "").toUpperCase();
      if (lv === "ERROR" || lv === "CRITICAL") line.className = "log-line-error";
      else if (lv === "WARNING") line.className = "log-line-warn";
      line.textContent = `[${row.ts}] ${row.message}`;
      frag.appendChild(line);
    });
    body.appendChild(frag);
    if (stickBottom) body.scrollTop = body.scrollHeight;
  };

  const pull = async (replace = false) => {
    const bid = window.CanvasStore?.activeBoardId || "";
    if (!bid) return;
    if (bid !== activeBoardId) resetForBoard(bid);
    try {
      const data = await api(`/api/logs?since=${lastId}&limit=200${boardQuery()}`);
      const logs = data.logs || [];
      if (logs.length) {
        lastId = data.last_id || logs[logs.length - 1].id;
        render(logs, !replace);
      } else if (replace) {
        body.textContent = "暂无日志";
      }
    } catch {
      /* ignore */
    }
  };

  const open = () => {
    if (typeof window.closeExclusiveChromePopovers === "function") {
      window.closeExclusiveChromePopovers("log");
    }
    resetForBoard();
    revealOverlay(panel);
    btn.setAttribute("aria-pressed", "true");
    pull(true);
    if (!timer) timer = setInterval(() => pull(false), 3000);
  };

  const close = () => {
    dismissOverlay(panel);
    btn.setAttribute("aria-pressed", "false");
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  };
  window.closeLogPanel = close;

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!isOverlayOpen(panel)) open();
    else close();
  });

  panel.addEventListener("click", (e) => e.stopPropagation());

  closeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    close();
  });

  clearBtn?.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await fetch(API + `/api/logs?${boardQuery().slice(1)}`, { method: "DELETE" });
    } catch {
      /* ignore */
    }
    lastId = 0;
    body.textContent = "暂无日志";
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOverlayOpen(panel)) close();
  });

  document.addEventListener("click", (e) => {
    if (!isOverlayOpen(panel)) return;
    if (e.target.closest("#log-fab")) return;
    close();
  });
}
