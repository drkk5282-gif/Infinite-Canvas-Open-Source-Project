/**
 * Detect prolonged server disconnect and show a restart overlay.
 * Only the periodic /api/ping counts toward failure — transient
 * canvas/sync errors must not stack into a false "server updated" banner.
 *
 * LAN note: browsers cap ~6 connections per host. Heavy /uploads media over
 * Wi‑Fi can starve the heartbeat and false-trigger this overlay even though
 * the Python process is fine. Prefer a tiny ping + longer LAN timeouts, and
 * ignore ping failures while the media loader is under pressure.
 */
(function (global) {
  const OVERLAY_ID = "server-restart-overlay";
  const PING_URL = "/api/ping";
  const PING_FALLBACK_URL = "/api/app-config";
  const host = String(global.location?.hostname || "");
  const isLoopback = host === "127.0.0.1" || host === "localhost" || host === "::1";
  // LAN / remote: Wi‑Fi + many images easily delay a ping past 8s.
  const INTERVAL_MS = isLoopback ? 4000 : 8000;
  const FAIL_THRESHOLD = isLoopback ? 6 : 10; // ~24s local / ~80s LAN
  const TIMEOUT_MS = isLoopback ? 8000 : 25000;

  let reachable = false;
  let failCount = 0;
  let shown = false;
  let timer = null;
  let inflight = null;

  function mediaUnderPressure() {
    try {
      const p = typeof global.getMediaLoadPressure === "function" ? global.getMediaLoadPressure() : null;
      if (!p) return false;
      return p.active + p.queued >= Math.max(2, (p.max || 4) - 1) || p.hold > 0;
    } catch {
      return false;
    }
  }

  function ensureOverlay() {
    let el = document.getElementById(OVERLAY_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = OVERLAY_ID;
    el.className = "server-restart-overlay";
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "assertive");
    el.setAttribute("aria-labelledby", "server-restart-msg");
    const tip = isLoopback
      ? '<div class="server-restart-tip">若素材很多：可能是浏览器连接被占满，不一定是服务挂了 — 可先等几秒或刷新</div>'
      : '<div class="server-restart-tip">内网图多时 Wi‑Fi 会慢，心跳易被占满；本机请优先用 127.0.0.1</div>';
    el.innerHTML =
      '<div class="server-restart-card">' +
      '<span class="server-restart-msg" id="server-restart-msg">连接异常，请</span>' +
      '<button type="button" class="server-restart-btn" id="server-restart-btn">刷新</button>' +
      tip +
      "</div>";
    document.body.appendChild(el);
    el.querySelector("#server-restart-btn")?.addEventListener("click", () => {
      global.location.reload();
    });
    return el;
  }

  function hideRestartOverlay() {
    shown = false;
    document.getElementById(OVERLAY_ID)?.classList.remove("is-visible");
  }

  function showRestartOverlay() {
    if (shown) return;
    shown = true;
    const el = ensureOverlay();
    el.classList.add("is-visible");
  }

  function markReachable() {
    reachable = true;
    failCount = 0;
    if (shown) hideRestartOverlay();
  }

  function reportPingFailure() {
    if (shown) return;
    // Media storms fill Chromium's 6 connections/host — a late ping is not offline.
    if (mediaUnderPressure()) return;
    failCount += 1;
    if (failCount >= FAIL_THRESHOLD) showRestartOverlay();
  }

  async function fetchPing(url, signal) {
    const opts = { cache: "no-store", signal, credentials: "same-origin" };
    // Chromium: prefer heartbeat over queued media when supported.
    try {
      opts.priority = "high";
    } catch {
      /* ignore */
    }
    return fetch(url, opts);
  }

  async function ping() {
    if (inflight) return;
    // Skip heartbeat while uploads are saturating the host — avoids false overlay.
    if (mediaUnderPressure()) return;
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    inflight = (async () => {
      try {
        let res;
        try {
          res = await fetchPing(PING_URL, ctrl.signal);
        } catch (err) {
          if (ctrl.signal.aborted) throw err;
          res = await fetchPing(PING_FALLBACK_URL, ctrl.signal);
        }
        if (!res.ok) throw new Error(String(res.status));
        markReachable();
      } catch {
        if (!reachable) return;
        reportPingFailure();
      } finally {
        clearTimeout(tid);
        inflight = null;
      }
    })();
    await inflight;
  }

  function onOffline() {
    // Wait for ping failures — brief offline blips should not force refresh.
    if (reachable && !shown) void ping();
  }

  function onVisible() {
    if (document.visibilityState === "visible" && !shown) void ping();
  }

  function start() {
    if (timer) return;
    void ping();
    timer = setInterval(() => void ping(), INTERVAL_MS);
    global.addEventListener("offline", onOffline);
    document.addEventListener("visibilitychange", onVisible);
  }

  global.ServerWatch = {
    markReachable,
    reportFailure() {
      /* no-op: canvas/sync errors must not trigger the restart banner */
    },
    showRestartOverlay,
    start,
    isLoopback,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})(window);
