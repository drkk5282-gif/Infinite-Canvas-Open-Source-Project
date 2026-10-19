/**
 * 小庆子 FAB 入口
 * 刷新 / 新会话：会话窗默认关闭，不自动打开。
 * 进行中的 8089 任务会后台续接，FAB 显示「思考中」。
 */
(function (global) {
  "use strict";

  function forcePanelClosed() {
    const fab = document.getElementById("btn-agent-fab");
    if (fab) fab.setAttribute("aria-pressed", "false");
    if (global.AgentPanel?.isOpen?.()) {
      global.AgentPanel.close();
      return;
    }
    const root = document.getElementById("agent-panel");
    if (!root) return;
    root.classList.remove("is-overlay-open");
    root.classList.add("hidden");
    root.setAttribute("aria-hidden", "true");
  }

  function initAgentFab() {
    const btn = document.getElementById("btn-agent-fab");
    if (!btn) return;
    if (btn.dataset.bound !== "1") {
      btn.dataset.bound = "1";
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        global.AgentPanel?.toggle?.();
      });
    }
    // Keep panel closed, but hydrate so in-flight jobs can resume + FAB badge.
    forcePanelClosed();
    try {
      global.AgentPanel?.ensureDom?.();
    } catch {
      /* ignore */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAgentFab);
  } else {
    initAgentFab();
  }

  // bfcache / 返回前进：仍保持默认关闭
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) forcePanelClosed();
  });

  global.AgentApp = { init: initAgentFab };
})(typeof window !== "undefined" ? window : globalThis);
