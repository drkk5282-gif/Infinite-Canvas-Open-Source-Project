/**
 * 小庆子智能体 — 画布操作规则（前端常量）
 * 后端 / Cursor 规则请同步见 .cursor/rules/agent-canvas.mdc
 */
(function (global) {
  "use strict";

  const AGENT_PREFS_KEY = "qinzi-agent-context-prefs-v1";
  const AGENT_CHAT_DEFAULTS = {
    modelRecentMessages: 8,
    memoryMaxChars: 2400,
    recentAssistantMax: 1200,
    uiPageSize: 10,
    uiLoadMore: 10,
    contextDetail: "balanced",
  };

  function loadAgentPreferences() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(AGENT_PREFS_KEY) || "{}"); } catch { /* use defaults */ }
    const p = { ...AGENT_CHAT_DEFAULTS };
    if (["compact", "balanced", "full"].includes(saved.contextDetail)) p.contextDetail = saved.contextDetail;
    for (const key of ["modelRecentMessages", "memoryMaxChars", "recentAssistantMax", "uiPageSize", "uiLoadMore"]) {
      const n = Number(saved[key]);
      if (Number.isFinite(n)) p[key] = Math.max(1, Math.min(key === "memoryMaxChars" ? 12000 : 40, Math.round(n)));
    }
    return p;
  }

  let agentPreferences = loadAgentPreferences();
  function getAgentPreferences() { return { ...agentPreferences }; }
  function setAgentPreferences(patch) {
    agentPreferences = { ...agentPreferences, ...patch };
    try { localStorage.setItem(AGENT_PREFS_KEY, JSON.stringify(agentPreferences)); } catch { /* ignore storage quota */ }
    return getAgentPreferences();
  }

  const AgentRules = Object.freeze({
    /** 默认紧凑竖排：图 → 语音/文本 → 视频；间隔宜小 */
    layout: Object.freeze({
      cellW: 320,
      cellH: 180,
      gapX: 22,
      gapY: 16,
      /** 族与族之间的横向间距（仍要紧凑） */
      clusterGap: 40,
      /** 非竖排时的上限列数 */
      maxCols: 4,
      /** 单列最多节点：超过则另开一列（视频编号尤其重要） */
      videoMaxPerCol: 15,
      imageMaxPerCol: 12,
      textMaxPerCol: 12,
      /** 默认族顺序（左→右） */
      familyOrder: Object.freeze(["image", "text", "video"]),
      /** 相对视口中心的额外偏移 */
      viewportPad: 72,
    }),

    /** 创建文本节点时：正文写入 resultText + prompt，status=completed */
    textNode: Object.freeze({
      type: "text",
      status: "completed",
      nameMax: 28,
    }),

    /** 识别分镜脚本的标题模式 */
    scriptPatterns: Object.freeze({
      segmentHeading: /^#{1,3}\s*视频编号\s*0*(\d+)\s*$/m,
      segmentHeadingGlobal: /^#{1,3}\s*视频编号\s*0*(\d+)\s*$/gm,
      minSegmentsToAutoSplit: 2,
    }),

    /** 智能体消息里 @ 引用画布节点的提示文案 */
    mentionHint: "描述操作或用 @ 引用画布节点…",

    /** 会话展示与模型上下文 */
    chat: Object.freeze({
      /** 面板初始只渲染最近 N 条，上滑再加载更早 */
      uiPageSize: 10,
      /** 每次上滑追加条数 */
      uiLoadMore: 10,
      /** 送给模型的最近原文条数（含 user/assistant，不含卡片） */
      modelRecentMessages: 8,
      /** 更早内容压缩进记忆的总字符上限 */
      memoryMaxChars: 2400,
      /** 普通用户消息上限 */
      recentUserMax: 12000,
      recentAssistantMax: 1200,
      /** 分镜/场景设计长文：仅最近一条尽量完整 */
      scriptUserMax: 100000,
    }),

    /** 快速操作 chips */
    quickActions: Object.freeze([
      { id: "split-script", label: "创建视频编号节点" },
      { id: "layout-selected", label: "整理选中节点" },
      { id: "help", label: "能做什么？" },
    ]),
  });

  global.AgentRules = AgentRules;
  global.getAgentPreferences = getAgentPreferences;
  global.setAgentPreferences = setAgentPreferences;
})(typeof window !== "undefined" ? window : globalThis);
