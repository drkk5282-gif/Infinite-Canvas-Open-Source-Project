/**
 * 小庆子聊天面板：支持 @ 引用画布节点、发送分镜脚本。
 * 会话按画布隔离；新消息自动滚底，向上翻时显示「回到底部」。
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "qinzi-agent-chats-v1";
  const PREFS_KEY = "qinzi-agent-prefs-v1";
  const RUNS_KEY = "qinzi-agent-runs-v1";
  const BOTTOM_GAP = 56;

  const STATE = {
    open: false,
    sending: false,
    /** @type {AbortController|null} */
    abortController: null,
    /** @type {string} */
    activeJobId: "",
    mentions: [],
    history: [],
    boardId: null,
    stickToBottom: true,
    /** 已渲染历史的起始下标（更早的需上滑加载） */
    uiStart: 0,
    /** agent=自动化 · ask=仅问答 */
    roleMode: "agent",
    /** confirm=询问 · auto=直接操作 */
    execMode: "confirm",
    pendingActions: null,
    pendingChoices: null,
  };

  /** @type {Map<string, { history: {role:string,content:string}[], draft: string }>} */
  const chatsByBoard = new Map();
  /** @type {Map<string, { jobId: string, startedAt: number }>} */
  const runsByBoard = new Map();
  let loadedRunsScope = "";

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function currentBoardId() {
    const id = global.CanvasStore?.activeBoardId;
    return id ? String(id) : "_default";
  }

  function storageScope(userOverride) {
    const user =
      userOverride ||
      global.CanvasAuth?.getUser?.()?.id ||
      global.__canvasUser?.id ||
      global.__canvasUser?.username ||
      "anon";
    return `${STORAGE_KEY}:${user}`;
  }

  function normalizeHistoryEntry(h) {
    if (!h || (h.role !== "user" && h.role !== "assistant")) return null;
    if (h.kind === "result" || h.kind === "plan") {
      const items = Array.isArray(h.items) ? h.items.map((x) => String(x || "").trim()).filter(Boolean) : [];
      const title = String(h.title || (h.kind === "plan" ? "拟执行" : "已执行"));
      if (!items.length && !String(h.content || "").trim()) return null;
      const out = {
        role: "assistant",
        kind: h.kind,
        title,
        items: items.length
          ? items
          : String(h.content || "")
              .split(/\n/)
              .map((l) => l.replace(/^[·•\-]\s*/, "").trim())
              .filter(Boolean),
        content: String(h.content || ""),
        thinking: "",
      };
      if (h.kind === "plan") {
        if (Array.isArray(h.pendingActions) && h.pendingActions.length) {
          out.pendingActions = h.pendingActions.slice();
        }
        if (Array.isArray(h.pendingChoices) && h.pendingChoices.length) {
          out.pendingChoices = h.pendingChoices
            .filter((c) => c && typeof c === "object")
            .map((c) => ({
              id: c.id,
              label: c.label,
            message: c.message || c.label,
              actions: Array.isArray(c.actions) ? c.actions.slice() : [],
              _askModelsChannel: c._askModelsChannel,
              _askModelsTypes: Array.isArray(c._askModelsTypes)
                ? c._askModelsTypes.slice()
                : undefined,
            }));
        }
      }
      return out;
    }
    const content = String(h.content || "");
    const thinking = String(h.thinking || "");
    if (!content && !thinking) return null;
    return { role: h.role, content, thinking };
  }

  function copyText(text) {
    const val = String(text || "");
    if (!val) return;
    const done = () => {
      if (typeof global.showCanvasToast === "function") global.showCanvasToast("已复制");
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(val).then(done).catch(() => {
        try {
          const ta = document.createElement("textarea");
          ta.value = val;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
          done();
        } catch {
          /* ignore */
        }
      });
      return;
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = val;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      done();
    } catch {
      /* ignore */
    }
  }

  function copyBtnHtml() {
    return `<button type="button" class="agent-msg-copy" title="复制" aria-label="复制">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M4 14V6a2 2 0 0 1 2-2h8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
    </button>`;
  }

  function buildConfirmBarHtml() {
    return `<button type="button" class="agent-confirm-btn agent-confirm-yes" data-agent-confirm="yes">同意并执行</button>
      <button type="button" class="agent-confirm-btn agent-confirm-no" data-agent-confirm="no">跳过</button>`;
  }

  function buildChoiceBarHtml(choices) {
    return (Array.isArray(choices) ? choices : [])
      .map((c, i) => {
        const label = escapeHtml(c.label || c.id || `选项${i + 1}`);
        const primary = i === 0 && !c._customSay ? " agent-confirm-yes" : "";
        const custom = c._customSay || c.id === "custom_say" ? " agent-choice-custom" : "";
        return `<button type="button" class="agent-confirm-btn${primary}${custom}" data-agent-choice="${i}">${label}</button>`;
      })
      .join("");
  }

  function hydratePlanCardPending(card, entry) {
    if (!card || entry?.kind !== "plan") return;
    const bar = card.querySelector(".agent-confirm-bar");
    if (!bar) return;
    if (entry.pendingChoices?.length) {
      bar._pendingChoices = entry.pendingChoices.map((c) => ({
        id: c.id,
        label: c.label,
            message: c.message || c.label,
        actions: Array.isArray(c.actions) ? c.actions.slice() : [],
        _askModelsChannel: c._askModelsChannel,
        _askModelsTypes: Array.isArray(c._askModelsTypes) ? c._askModelsTypes.slice() : undefined,
        _customSay: !!c._customSay || c.id === "custom_say",
      }));
    } else if (entry.pendingActions?.length) {
      bar._pendingActions = entry.pendingActions.slice();
    }
  }

  function clearLatestPlanPending() {
    for (let i = STATE.history.length - 1; i >= 0; i--) {
      const h = STATE.history[i];
      if (h?.kind !== "plan") continue;
      if (!h.pendingActions && !h.pendingChoices) continue;
      delete h.pendingActions;
      delete h.pendingChoices;
      break;
    }
    if (STATE.boardId) {
      const chat = ensureBoardChat(STATE.boardId);
      chat.history = STATE.history.slice();
      persistChats();
    }
  }

  /** 选完/确认后：去掉「请选择/拟执行」卡，避免和结果并排往上堆 */
  function dismissInteractivePlan(bar) {
    clearLatestPlanPending();
    for (let i = STATE.history.length - 1; i >= 0; i--) {
      const h = STATE.history[i];
      if (h?.kind !== "plan") continue;
      if (h.pendingActions || h.pendingChoices) continue;
      STATE.history.splice(i, 1);
      break;
    }
    if (STATE.boardId) {
      const chat = ensureBoardChat(STATE.boardId);
      chat.history = STATE.history.slice();
      persistChats();
    }
    const card = bar?.closest?.(".agent-result-card") || null;
    const row = card?.closest?.(".agent-result-row") || bar?.closest?.(".agent-result-row");
    const wrap = row?.closest?.(".agent-msg-card") || card?.closest?.(".agent-msg-card");
    bar?.remove();
    card?.remove();
    if (row && !row.querySelector(".agent-result-card")) {
      wrap?.remove();
    } else if (row && !row.querySelector(".agent-result-plan") && row.querySelector(".agent-result-result")) {
      // 只剩结果时保留行
    }
  }

  function resultCardInnerHtml(entry, opts = {}) {
    const kind = entry.kind === "plan" ? "plan" : "result";
    const title = escapeHtml(entry.title || (kind === "plan" ? "拟执行" : "已执行"));
    const items = Array.isArray(entry.items) ? entry.items : [];
    const previewN = 3;
    const head = items.slice(0, previewN);
    const rest = items.slice(previewN);
    const headList = head.map((it) => `<li>${escapeHtml(it)}</li>`).join("");
    const restList = rest.map((it) => `<li>${escapeHtml(it)}</li>`).join("");
    const isChoice =
      kind === "plan" &&
      (!!opts.choiceBar || !!(entry.pendingChoices && entry.pendingChoices.length));
    const note =
      kind === "plan"
        ? `<p class="agent-result-note">${
            isChoice ? "点选以明确本次处理范围，也可以自己补充" : "确认后才会改动画布"
          }</p>`
        : "";
    const more =
      rest.length > 0
        ? `<ul class="agent-result-list agent-result-list-more hidden">${restList}</ul>
           <button type="button" class="agent-result-toggle" data-agent-result-toggle aria-expanded="false">
             展开全部（${items.length}）
           </button>`
        : "";
    let confirmBar = opts.confirmBar;
    let choiceBar = opts.choiceBar;
    if (kind === "plan" && !confirmBar && !choiceBar) {
      if (entry.pendingChoices?.length) {
        choiceBar = buildChoiceBarHtml(entry.pendingChoices);
      } else if (entry.pendingActions?.length) {
        confirmBar = buildConfirmBarHtml();
      }
    }
    const actions = confirmBar
      ? `<div class="agent-confirm-bar">${confirmBar}</div>`
      : choiceBar
        ? `<div class="agent-confirm-bar agent-choice-bar">${choiceBar}</div>`
        : "";
    return `<div class="agent-result-card agent-result-${kind}" data-agent-kind="${kind}">
      <div class="agent-result-head">
        <span class="agent-result-title">${title}${
          items.length > 1 ? `<span class="agent-result-count">${items.length}</span>` : ""
        }</span>
        ${copyBtnHtml()}
      </div>
      ${note}
      <ul class="agent-result-list">${headList}</ul>
      ${more}
      ${actions}
    </div>`;
  }

  function resultCardHtml(entry, opts = {}) {
    return `<div class="agent-msg agent-msg-assistant agent-msg-card" data-agent-kind="${
      entry.kind === "plan" ? "plan" : "result"
    }">${resultCardInnerHtml(entry, opts)}</div>`;
  }

  function createResultRow() {
    const row = document.createElement("div");
    row.className = "agent-result-row";
    return row;
  }

  /** 把「已执行」挂到最近的「拟执行」同一行 */
  function pairResultBesidePlan(cardEl, box) {
    if (!cardEl || !box) {
      box?.appendChild(cardEl);
      return;
    }
    const kids = [...box.children];
    for (let i = kids.length - 1; i >= 0; i--) {
      const el = kids[i];
      const row = el.classList?.contains("agent-result-row")
        ? el
        : el.querySelector?.(":scope > .agent-result-row, .agent-result-row");
      if (!row) continue;
      const hasPlan = !!row.querySelector(".agent-result-plan");
      const hasResult = !!row.querySelector(".agent-result-result");
      if (hasPlan && !hasResult) {
        row.appendChild(cardEl);
        return;
      }
      if (hasPlan && hasResult) break;
    }
    const row = createResultRow();
    row.appendChild(cardEl);
    const wrap = document.createElement("div");
    wrap.className = "agent-msg agent-msg-assistant agent-msg-card";
    wrap.appendChild(row);
    box.appendChild(wrap);
  }

  function setResultCardExpanded(card, expanded) {
    if (!card) return;
    const more = card.querySelector(".agent-result-list-more");
    const toggle = card.querySelector("[data-agent-result-toggle]");
    if (!more || !toggle) return;
    more.classList.toggle("hidden", !expanded);
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    const total = card.querySelectorAll(".agent-result-list li").length;
    toggle.textContent = expanded ? "收起" : `展开全部（${total}）`;
  }

  function mountResultCardFromEntry(entry, opts = {}) {
    const tmp = document.createElement("div");
    tmp.innerHTML = resultCardInnerHtml(entry, opts);
    const card = tmp.firstElementChild;
    hydratePlanCardPending(card, entry);
    return card;
  }

  function appendResultCard(kind, title, items) {
    const list = (Array.isArray(items) ? items : []).map((x) => String(x || "").trim()).filter(Boolean);
    if (!list.length) return null;
    if (!STATE.boardId) STATE.boardId = currentBoardId();
    const welcome = $("#agent-welcome");
    const box = $("#agent-messages");
    if (welcome) welcome.hidden = true;
    if (box) box.hidden = false;
    const entry = {
      role: "assistant",
      kind: kind === "plan" ? "plan" : "result",
      title: title || (kind === "plan" ? "拟执行" : "已执行"),
      items: list,
      content: list.map((x) => `· ${x}`).join("\n"),
      thinking: "",
    };
    STATE.history.push(entry);
    const chat = ensureBoardChat(STATE.boardId);
    chat.history = STATE.history.slice();
    persistChats();
    const tmp = document.createElement("div");
    tmp.innerHTML = resultCardInnerHtml(entry);
    const card = tmp.firstElementChild;
    hydratePlanCardPending(card, entry);
    if (entry.kind === "result") pairResultBesidePlan(card, box);
    else {
      const wrap = document.createElement("div");
      wrap.className = "agent-msg agent-msg-assistant agent-msg-card";
      const row = createResultRow();
      row.appendChild(card);
      wrap.appendChild(row);
      box.appendChild(wrap);
    }
    scrollToBottom({ force: STATE.stickToBottom });
    return entry;
  }

  function retainSourceHistory(history) {
    const rows = (history || []).map(normalizeHistoryEntry).filter(Boolean);
    return rows.filter((h, i) => i >= rows.length - 80 ||
      (h.role === "user" && (h.content.length >= 400 || looksLikeScriptContent(h.content))));
  }

  function mergeBoardEntry(bid, entry) {
    if (!entry || !Array.isArray(entry.history)) return;
    const id = String(bid);
    const existing = chatsByBoard.get(id);
    const incoming = retainSourceHistory(entry.history);
    const draft = String(entry.draft || "");
    if (!existing) {
      chatsByBoard.set(id, { history: incoming, draft });
      return;
    }
    // Prefer the longer history (don't wipe newer in-memory with shorter disk)
    if (incoming.length >= (existing.history?.length || 0)) {
      existing.history = incoming;
    }
    if (draft && !existing.draft) existing.draft = draft;
  }

  function loadPersistedChats() {
    try {
      const keys = [storageScope()];
      const anonKey = storageScope("anon");
      if (!keys.includes(anonKey)) keys.push(anonKey);
      for (const key of keys) {
        const raw = localStorage.getItem(key);
        if (!raw) continue;
        const data = JSON.parse(raw);
        if (!data || typeof data !== "object") continue;
        for (const [bid, entry] of Object.entries(data)) {
          mergeBoardEntry(bid, entry);
        }
      }
    } catch {
      /* ignore */
    }
  }

  function persistChats() {
    try {
      const out = {};
      for (const [bid, entry] of chatsByBoard.entries()) {
        if (!entry?.history?.length && !entry?.draft) continue;
        out[bid] = {
          history: retainSourceHistory(entry.history),
          draft: String(entry.draft || "").slice(0, 20000),
        };
      }
      localStorage.setItem(storageScope(), JSON.stringify(out));
      // Keep anon mirror when logged in so refresh-before-auth still sees chats
      const anonKey = storageScope("anon");
      if (storageScope() !== anonKey) {
        localStorage.setItem(anonKey, JSON.stringify(out));
      }
    } catch {
      global.showCanvasToast?.("对话保存失败，当前原文仍在本次会话中；刷新前请复制保留。");
    }
  }

  function ensureBoardChat(boardId) {
    const id = String(boardId || "_default");
    if (!chatsByBoard.has(id)) {
      chatsByBoard.set(id, { history: [], draft: "" });
    }
    return chatsByBoard.get(id);
  }

  function snapshotCurrentBoard() {
    if (!STATE.boardId) return;
    const chat = ensureBoardChat(STATE.boardId);
    chat.history = STATE.history.slice();
    chat.draft = getComposeText($("#agent-input"));
    persistChats();
  }

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data?.roleMode === "ask" || data?.roleMode === "agent") STATE.roleMode = data.roleMode;
      if (data?.execMode === "auto" || data?.execMode === "confirm") STATE.execMode = data.execMode;
    } catch {
      /* ignore */
    }
  }

  function persistPrefs() {
    try {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({ roleMode: STATE.roleMode, execMode: STATE.execMode })
      );
    } catch {
      /* ignore */
    }
  }

  function runsScope(userOverride) {
    const user =
      userOverride ||
      global.CanvasAuth?.getUser?.()?.id ||
      global.__canvasUser?.id ||
      global.__canvasUser?.username ||
      "anon";
    return `${RUNS_KEY}:${user}`;
  }

  function persistRuns() {
    try {
      const out = {};
      for (const [bid, run] of runsByBoard.entries()) {
        if (!run?.jobId) continue;
        out[bid] = { jobId: String(run.jobId), startedAt: Number(run.startedAt) || Date.now() };
      }
      localStorage.setItem(runsScope(), JSON.stringify(out));
    } catch {
      /* ignore */
    }
  }

  function loadPersistedRuns() {
    const scope = runsScope();
    if (scope === loadedRunsScope) return;
    loadedRunsScope = scope;
    runsByBoard.clear();
    try {
      const raw = localStorage.getItem(scope);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!data || typeof data !== "object") return;
      const now = Date.now();
      for (const [bid, run] of Object.entries(data)) {
        const jobId = String(run?.jobId || "").trim();
        const startedAt = Number(run?.startedAt) || 0;
        // Drop stale runs older than 30 minutes.
        if (!jobId || (startedAt && now - startedAt > 30 * 60 * 1000)) continue;
        runsByBoard.set(String(bid), { jobId, startedAt: startedAt || now });
      }
    } catch {
      /* ignore */
    }
  }

  function setActiveRun(boardId, jobId) {
    loadPersistedRuns();
    const bid = String(boardId || currentBoardId() || "_default");
    const id = String(jobId || "").trim();
    STATE.activeJobId = id;
    if (!id) {
      runsByBoard.delete(bid);
    } else {
      runsByBoard.set(bid, { jobId: id, startedAt: Date.now() });
    }
    persistRuns();
    syncFabBusy();
  }

  function clearActiveRun(boardId) {
    loadPersistedRuns();
    const bid = String(boardId || STATE.boardId || currentBoardId() || "_default");
    if (STATE.activeJobId && runsByBoard.get(bid)?.jobId === STATE.activeJobId) {
      STATE.activeJobId = "";
    }
    runsByBoard.delete(bid);
    persistRuns();
    syncFabBusy();
  }

  function getPersistedRun(boardId) {
    // Hidden panel may boot before authentication resolves. Rehydrate when
    // the real user becomes available instead of retaining the empty anon map.
    loadPersistedRuns();
    return runsByBoard.get(String(boardId || currentBoardId() || "_default")) || null;
  }

  function syncFabBusy() {
    const fab = $("#btn-agent-fab");
    const wrap = $("#agent-fab");
    const busy = !!STATE.sending || runsByBoard.size > 0;
    fab?.classList.toggle("is-thinking", busy);
    wrap?.classList.toggle("is-thinking", busy);
    if (fab) {
      fab.title = busy ? "小庆子思考中（点击查看）" : "小庆子智能体";
      fab.setAttribute("aria-label", busy ? "小庆子思考中" : "打开小庆子");
    }
  }

  function restoreBusyUi() {
    setSendBusy(STATE.sending);
    if (STATE.sending && STATE.open && !$("#agent-msg-pending")) {
      const welcome = $("#agent-welcome");
      const box = $("#agent-messages");
      if (welcome) welcome.hidden = true;
      if (box) box.hidden = false;
      beginThinkingShell();
    }
    syncFabBusy();
  }

  function jobBindOpts(extra = {}) {
    const boardId = extra.boardId || STATE.boardId || currentBoardId();
    return {
      ...extra,
      boardId,
      onJobId: (jobId) => {
        setActiveRun(boardId, jobId);
        if (typeof extra.onJobId === "function") extra.onJobId(jobId);
      },
    };
  }

  function isDisplayableThinking(text) {
    const t = String(text || "").trim();
    if (!t) return false;
    if (
      /^(（模型未提供思考过程）|（详见回复）|模型未返回结构化 JSON|模型输出格式异常|已从破损输出中恢复)/.test(
        t
      )
    ) {
      return false;
    }
    // DeepSeek 长 CoT / 协议草稿：不外露
    if (t.length > 220) return false;
    if ((t.match(/\n/g) || []).length >= 4) return false;
    if (
      /update_node|generate_nodes|create_nodes|我应使用|我最终决定|意图：|关于负面|host_id|into_prompt|\{"type"/i.test(
        t
      )
    ) {
      return false;
    }
    return true;
  }

  function messageBubbleHtml(h, opts = {}) {
    if (h?.kind === "result" || h?.kind === "plan") return resultCardHtml(h);
    const role = h.role === "user" ? "user" : "assistant";
    const thinking = isDisplayableThinking(h.thinking) ? String(h.thinking || "").trim() : "";
    const content = String(h.content || "");
    const thinkingOpen = opts.thinkingOpen === true;
    let inner = "";
    if (thinking) {
      inner += `<details class="agent-thinking"${thinkingOpen ? " open" : ""}>
        <summary>思考</summary>
        <div class="agent-thinking-body">${escapeHtml(thinking).replace(/\n/g, "<br>")}</div>
      </details>`;
    }
    if (content) {
      const bodyHtml =
        role === "user"
          ? formatRichMentionHtml(content)
          : escapeHtml(content).replace(/\n/g, "<br>");
      inner += `<div class="agent-msg-bubble-wrap">
        <div class="agent-msg-bubble">${bodyHtml}</div>
        ${copyBtnHtml()}
      </div>`;
    }
    return `<div class="agent-msg agent-msg-${role}">${inner}</div>`;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  /** 立刻展示回复；思考默认收起，不做打字动画。 */
  async function appendAssistantTyped(text, thinking, extra = {}) {
    if (!STATE.boardId) STATE.boardId = currentBoardId();
    const welcome = $("#agent-welcome");
    const box = $("#agent-messages");
    if (welcome) welcome.hidden = true;
    if (box) box.hidden = false;

    const entry = {
      role: "assistant",
      content: String(text || ""),
      thinking: String(thinking || ""),
    };
    STATE.history.push(entry);
    const chat = ensureBoardChat(STATE.boardId);
    chat.history = STATE.history.slice();
    persistChats();

    const wrap = document.createElement("div");
    wrap.className = "agent-msg agent-msg-assistant";
    const thinkingText = isDisplayableThinking(thinking) ? String(thinking || "").trim() : "";
    if (thinkingText) {
      const details = document.createElement("details");
      details.className = "agent-thinking";
      details.open = false;
      details.innerHTML = `<summary>思考</summary><div class="agent-thinking-body"></div>`;
      details.querySelector(".agent-thinking-body").textContent = thinkingText;
      wrap.appendChild(details);
    }
    const bubbleText = String(entry.content || "").trim();
    if (bubbleText) {
      const bubbleWrap = document.createElement("div");
      bubbleWrap.className = "agent-msg-bubble-wrap";
      const bubble = document.createElement("div");
      bubble.className = "agent-msg-bubble";
      bubble.innerHTML = escapeHtml(bubbleText).replace(/\n/g, "<br>");
      bubbleWrap.appendChild(bubble);
      const copyMount = document.createElement("div");
      copyMount.innerHTML = copyBtnHtml();
      const copyBtn = copyMount.firstElementChild;
      if (copyBtn) bubbleWrap.appendChild(copyBtn);
      wrap.appendChild(bubbleWrap);
    }

    const confirmBarHtml = extra.pendingActions?.length ? buildConfirmBarHtml() : "";
    const choiceBarHtml = extra.pendingChoices?.length
      ? buildChoiceBarHtml(extra.pendingChoices)
      : "";
    const needsPlanCard =
      !!extra.planSummary?.length ||
      !!extra.pendingActions?.length ||
      !!extra.pendingChoices?.length;

    if (needsPlanCard || extra.execLogs?.length) {
      const row = createResultRow();
      if (needsPlanCard) {
        let planItems = (extra.planSummary || []).map((x) => String(x)).filter(Boolean);
        if (!planItems.length && extra.pendingChoices?.length) {
          planItems = extra.pendingChoices
            .map((c) => String(c.label || c.id || "").trim())
            .filter(Boolean);
        }
        if (!planItems.length && extra.pendingActions?.length) {
          planItems =
            typeof global.AgentIntent?.summarizeActions === "function"
              ? global.AgentIntent.summarizeActions(extra.pendingActions)
              : extra.pendingActions.map((a) => String(a?.op || a?.type || "操作"));
        }
        if (!planItems.length) planItems = ["待确认操作"];
        const planEntry = {
          role: "assistant",
          kind: "plan",
          title: extra.pendingChoices?.length ? "请选择" : "拟执行",
          items: planItems,
          content: "",
          thinking: "",
        };
        planEntry.content = planEntry.items.map((x) => `· ${x}`).join("\n");
        if (extra.pendingActions?.length) {
          planEntry.pendingActions = extra.pendingActions.slice();
        }
        if (extra.pendingChoices?.length) {
          planEntry.pendingChoices = extra.pendingChoices.map((c) => ({
            id: c.id,
            label: c.label,
            message: c.message || c.label,
            actions: Array.isArray(c.actions) ? c.actions.slice() : [],
            _askModelsChannel: c._askModelsChannel,
            _askModelsTypes: Array.isArray(c._askModelsTypes) ? c._askModelsTypes.slice() : undefined,
            _customSay: !!c._customSay || c.id === "custom_say",
          }));
        }
        STATE.history.push(planEntry);
        const chatPlan = ensureBoardChat(STATE.boardId);
        chatPlan.history = STATE.history.slice();
        persistChats();
        const card = mountResultCardFromEntry(planEntry, {
          confirmBar: confirmBarHtml || undefined,
          choiceBar: !confirmBarHtml ? choiceBarHtml || undefined : undefined,
        });
        if (card) row.appendChild(card);
      }
      if (extra.execLogs?.length) {
        const execEntry = {
          role: "assistant",
          kind: "result",
          title: "已执行",
          items: extra.execLogs.map((x) => String(x)).filter(Boolean),
          content: "",
          thinking: "",
        };
        execEntry.content = execEntry.items.map((x) => `· ${x}`).join("\n");
        STATE.history.push(execEntry);
        const chatExec = ensureBoardChat(STATE.boardId);
        chatExec.history = STATE.history.slice();
        persistChats();
        const card = mountResultCardFromEntry(execEntry);
        if (card) row.appendChild(card);
      }
      if (row.childNodes.length) wrap.appendChild(row);
    }

    box.appendChild(wrap);
    scrollToBottom({ force: STATE.stickToBottom });
  }

  function setSendBusy(busy) {
    const sendBtn = $("#agent-btn-send");
    if (sendBtn) {
      if (busy) {
        sendBtn.classList.add("is-stop");
        sendBtn.disabled = false;
        sendBtn.title = "停止";
        sendBtn.setAttribute("aria-label", "停止生成");
        sendBtn.textContent = "■";
      } else {
        sendBtn.classList.remove("is-stop");
        sendBtn.disabled = false;
        sendBtn.title = "发送";
        sendBtn.setAttribute("aria-label", "发送");
        sendBtn.textContent = "↑";
      }
    }
    syncFabBusy();
  }

  function abortCurrent() {
    const jobId = STATE.activeJobId || getPersistedRun(STATE.boardId)?.jobId || "";
    if (jobId) void global.AgentApi?.cancelJob?.(jobId);
    if (!STATE.abortController) return;
    try {
      STATE.abortController.abort();
    } catch {
      /* ignore */
    }
  }

  /** 用户手动发新消息时，作废上方未点的「请选择 / 拟执行」 */
  function invalidateOpenPlans() {
    STATE.pendingActions = null;
    STATE.pendingChoices = null;
    document.querySelectorAll("#agent-messages .agent-result-plan").forEach((card) => {
      const row = card.closest(".agent-result-row");
      const wrap = card.closest(".agent-msg-card");
      card.remove();
      if (row && !row.querySelector(".agent-result-card")) {
        wrap?.remove();
      }
    });
    for (let i = STATE.history.length - 1; i >= 0; i--) {
      const h = STATE.history[i];
      if (h?.kind !== "plan") continue;
      if (h.pendingActions || h.pendingChoices) {
        STATE.history.splice(i, 1);
      }
    }
    if (STATE.boardId) {
      const chat = ensureBoardChat(STATE.boardId);
      chat.history = STATE.history.slice();
      persistChats();
    }
  }

  function beginThinkingShell() {
    const welcome = $("#agent-welcome");
    const box = $("#agent-messages");
    if (welcome) welcome.hidden = true;
    if (box) box.hidden = false;
    const el = document.createElement("div");
    el.className = "agent-msg agent-msg-assistant agent-msg-pending";
    el.id = "agent-msg-pending";
    el.innerHTML = `<button type="button" class="agent-thinking agent-thinking-pending" id="agent-btn-stop-think" title="停止生成" aria-label="思考中，点击停止">
      <span class="agent-think-spinner" aria-hidden="true"></span>
      <span class="agent-thinking-pending-label">思考中</span>
      <span class="agent-thinking-stop-hint">停止</span>
    </button>`;
    box.appendChild(el);
    $("#agent-btn-stop-think", el)?.addEventListener("click", (e) => {
      e.preventDefault();
      abortCurrent();
    });
    scrollToBottom({ force: true });
    return el;
  }

  function removePending() {
    $("#agent-msg-pending")?.remove();
  }

  function syncModeUi(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-agent-role]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.agentRole === STATE.roleMode);
    });
    scope.querySelectorAll("[data-agent-exec]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.agentExec === STATE.execMode);
      btn.disabled = STATE.roleMode === "ask";
    });
    const execGroup = $("#agent-exec-mode", scope);
    if (execGroup) execGroup.classList.toggle("is-disabled", STATE.roleMode === "ask");
  }

  function chatCfg() {
    return { ...(global.AgentRules?.chat || {}), ...(global.getAgentPreferences?.() || {}) };
  }

  function looksLikeScriptContent(text) {
    const s = String(text || "");
    if (!s.trim()) return false;
    if (/视频编号|分镜脚本|##\s*视频/.test(s)) return true;
    if ((s.match(/\|/g) || []).length >= 8 && /镜号|对白|画面/.test(s)) return true;
    // 场景设计 / 提示词文档：跟进「改提示词」时必须还能看见全文
    if (
      s.length >= 400 &&
      /(场景(设计|生成)?提示词|负面提示词|人物设定|场景\s*\d+|第\s*\d+\s*章)/.test(s)
    ) {
      return true;
    }
    if (s.length >= 800 && /(空场景|无人物|16\s*[:：]\s*9|镜头|光影)/.test(s)) {
      return true;
    }
    return false;
  }

  function clipText(text, max) {
    const s = String(text || "").trim();
    const n = Math.max(0, Number(max) || 0);
    if (!n || s.length <= n) return s;
    return s.slice(0, n - 1) + "…";
  }

  function entryBrief(h) {
    if (!h) return "";
    if (h.kind === "plan" || h.kind === "result") {
      const items = Array.isArray(h.items) ? h.items.slice(0, 3) : [];
      const body = items.length ? items.join("；") : String(h.content || "").replace(/\s+/g, " ");
      return clipText(`${h.title || h.kind}：${body}`, 140);
    }
    // 分镜原文：摘要里也尽量留头尾线索，避免完全丢掉
    const raw = String(h.content || "");
    if (looksLikeScriptContent(raw)) {
      return clipText(raw.replace(/\s+/g, " "), 800);
    }
    return clipText(raw.replace(/\s+/g, " "), 220);
  }

  /**
   * 模型上下文：更早轮次压成 memory，只带最近几条原文。
   * @param {any[]} history 已含当前用户句时，excludeLast=true 去掉最后一条
   */
  function buildCompactModelContext(history, { excludeLast = true } = {}) {
    const cfg = chatCfg();
    const recentN = Math.max(2, Number(cfg.modelRecentMessages) || 8);
    const memMax = Math.max(200, Number(cfg.memoryMaxChars) || 1600);
    const asstMax = Math.max(200, Number(cfg.recentAssistantMax) || 2000);

    let list = (Array.isArray(history) ? history : []).filter(
      (h) => h && (h.role === "user" || h.role === "assistant")
    );
    if (excludeLast && list.length) list = list.slice(0, -1);

    const recentSrc = list.slice(-recentN);
    const olderSrc = list.slice(0, Math.max(0, list.length - recentSrc.length));

    // Source documents remain available outside the recent-message window.
    const sourceRows = olderSrc.filter((h) => h.role === "user" &&
      (String(h.content || "").length >= 400 || looksLikeScriptContent(h.content)));
    const historyOut = [...sourceRows, ...recentSrc].map((h) => {
      if (h.kind === "plan" || h.kind === "result") {
        return { role: "assistant", content: clipText(entryBrief(h), asstMax) };
      }
      const content = h.role === "user" ? String(h.content || "") : clipText(h.content, asstMax);
      return content ? { role: h.role, content } : null;
    }).filter(Boolean);

    let memory = "";
    if (olderSrc.length) {
      const lines = olderSrc.map((h) => {
        const who = h.role === "user" ? "用户" : "助手";
        return `${who}：${entryBrief(h)}`;
      });
      memory = lines.join("\n");
      if (memory.length > memMax) {
        const head = Math.floor(memMax * 0.45);
        const tail = memMax - head - 5;
        memory = memory.slice(0, head) + "\n…\n" + memory.slice(-tail);
      }
    }
    return { history: historyOut, memory };
  }

  function historyMoreHtml(remaining) {
    if (remaining <= 0) return "";
    return `<div class="agent-history-more" id="agent-history-more" role="status">向上滑动加载更早消息 · 还有 ${remaining} 条</div>`;
  }

  function updateHistoryMoreBanner() {
    const box = $("#agent-messages");
    if (!box) return;
    const rem = STATE.uiStart;
    let el = $("#agent-history-more", box);
    if (rem <= 0) {
      el?.remove();
      return;
    }
    if (!el) {
      box.insertAdjacentHTML("afterbegin", historyMoreHtml(rem));
      return;
    }
    el.textContent = `向上滑动加载更早消息 · 还有 ${rem} 条`;
  }

  function loadOlderMessages() {
    if (STATE.uiStart <= 0) return false;
    const body = bodyEl();
    const box = $("#agent-messages");
    if (!box || !body) return false;
    const cfg = chatCfg();
    const step = Math.max(1, Number(cfg.uiLoadMore) || 10);
    const prevHeight = body.scrollHeight;
    const prevTop = body.scrollTop;
    const newStart = Math.max(0, STATE.uiStart - step);
    const chunk = STATE.history.slice(newStart, STATE.uiStart);
    STATE.uiStart = newStart;
    const frag = document.createDocumentFragment();
    const holder = document.createElement("div");
    appendHistoryEntries(holder, chunk);
    while (holder.firstChild) frag.appendChild(holder.firstChild);
    const more = $("#agent-history-more", box);
    const anchor = more ? more.nextSibling : box.firstChild;
    box.insertBefore(frag, anchor);
    updateHistoryMoreBanner();
    body.scrollTop = body.scrollHeight - prevHeight + prevTop;
    return true;
  }

  function appendHistoryEntries(box, entries) {
    let i = 0;
    while (i < entries.length) {
      const h = entries[i];
      const next = entries[i + 1];
      if (h?.kind === "plan" && next?.kind === "result") {
        const wrap = document.createElement("div");
        wrap.className = "agent-msg agent-msg-assistant agent-msg-card";
        const row = createResultRow();
        const planView = { ...h };
        delete planView.pendingActions;
        delete planView.pendingChoices;
        const planCard = mountResultCardFromEntry(planView);
        const resultCard = mountResultCardFromEntry(next);
        if (planCard) row.appendChild(planCard);
        if (resultCard) row.appendChild(resultCard);
        wrap.appendChild(row);
        box.appendChild(wrap);
        i += 2;
        continue;
      }
      if (h?.kind === "plan" || h?.kind === "result") {
        const wrap = document.createElement("div");
        wrap.className = "agent-msg agent-msg-assistant agent-msg-card";
        const row = createResultRow();
        const card = mountResultCardFromEntry(h);
        if (card) row.appendChild(card);
        wrap.appendChild(row);
        box.appendChild(wrap);
        i += 1;
        continue;
      }
      box.insertAdjacentHTML("beforeend", messageBubbleHtml(h));
      i += 1;
    }
    if (typeof global.hydrateMediaThumbs === "function") {
      global.hydrateMediaThumbs(box);
    }
  }

  function renderHistory(history) {
    const welcome = $("#agent-welcome");
    const box = $("#agent-messages");
    if (!box) return;
    box.innerHTML = "";
    const list = Array.isArray(history) ? history : [];
    if (!list.length) {
      STATE.uiStart = 0;
      if (welcome) welcome.hidden = false;
      box.hidden = true;
      return;
    }
    if (welcome) welcome.hidden = true;
    box.hidden = false;
    const page = Math.max(1, Number(chatCfg().uiPageSize) || 10);
    STATE.uiStart = Math.max(0, list.length - page);
    if (STATE.uiStart > 0) {
      box.insertAdjacentHTML("beforeend", historyMoreHtml(STATE.uiStart));
    }
    appendHistoryEntries(box, list.slice(STATE.uiStart));
  }

  function syncBoardUi(boardId, { keepStick } = {}) {
    const id = String(boardId || currentBoardId());
    if (STATE.boardId && STATE.boardId !== id) snapshotCurrentBoard();
    STATE.boardId = id;
    const chat = ensureBoardChat(id);
    STATE.history = chat.history.slice();
    STATE.mentions = [];
    STATE.pendingActions = null;
    STATE.pendingChoices = null;
    for (let i = STATE.history.length - 1; i >= 0; i--) {
      const h = STATE.history[i];
      if (h?.kind !== "plan") continue;
      const followedByResult = STATE.history[i + 1]?.kind === "result";
      if (followedByResult) break;
      if (h.pendingActions?.length) STATE.pendingActions = h.pendingActions.slice();
      if (h.pendingChoices?.length) {
        STATE.pendingChoices = h.pendingChoices.map((c) => ({
          id: c.id,
          label: c.label,
            message: c.message || c.label,
          actions: Array.isArray(c.actions) ? c.actions.slice() : [],
          _askModelsChannel: c._askModelsChannel,
          _askModelsTypes: Array.isArray(c._askModelsTypes)
            ? c._askModelsTypes.slice()
            : undefined,
        }));
      }
      break;
    }
    renderHistory(STATE.history);
    const input = $("#agent-input");
    if (input) setComposeText(input, chat.draft || "");
    if (!keepStick) STATE.stickToBottom = true;
    restoreBusyUi();
    requestAnimationFrame(() => scrollToBottom({ force: true }));
  }

  function onBoardChange(boardId) {
    const id = String(boardId || currentBoardId());
    if (getPersistedRun(id)?.jobId) {
      setTimeout(() => {
        if (currentBoardId() === id) void resumePendingJob();
      }, 0);
    }
    // Closed chat may contain long documents; render it only when opened.
    if (!STATE.open) {
      if (STATE.boardId && STATE.boardId !== String(boardId)) snapshotCurrentBoard();
      STATE.boardId = null;
      return;
    }
    ensureDom();
    syncBoardUi(boardId || currentBoardId());
  }

  function bodyEl() {
    return $("#agent-panel-body");
  }

  function isNearBottom(el) {
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_GAP;
  }

  function updateJumpBtn() {
    const btn = $("#agent-jump-bottom");
    const body = bodyEl();
    if (!btn || !body) return;
    const overflow = body.scrollHeight > body.clientHeight + 8;
    const show = overflow && !STATE.stickToBottom && !isNearBottom(body);
    btn.classList.toggle("hidden", !show);
    btn.setAttribute("aria-hidden", show ? "false" : "true");
  }

  function scrollToBottom({ force } = {}) {
    const body = bodyEl();
    if (!body) return;
    if (!force && !STATE.stickToBottom) {
      updateJumpBtn();
      return;
    }
    STATE.stickToBottom = true;
    body.scrollTop = body.scrollHeight;
    updateJumpBtn();
  }

  function nodeDisplayName(node) {
    if (!node) return "";
    if (node.customName) return String(node.customName);
    if (typeof global.getNodeDisplayName === "function") {
      try {
        return global.getNodeDisplayName(node) || node.id;
      } catch {
        /* fallthrough */
      }
    }
    const body = String(node.resultText || node.prompt || "").trim();
    if (body) return body.slice(0, 20) + (body.length > 20 ? "…" : "");
    return node.type || node.id;
  }

  function listMentionCandidates(query) {
    const board = global.CanvasStore?.activeBoard?.();
    if (!board?.nodes?.length) return [];
    const q = String(query || "").trim().toLowerCase();
    const out = [];
    for (const n of board.nodes) {
      const name = nodeDisplayName(n);
      const hay = `${name} ${n.id} ${n.type || ""}`.toLowerCase();
      if (q && !hay.includes(q)) continue;
      out.push({ id: n.id, name, node: n });
      if (out.length >= 16) break;
    }
    return out;
  }

  function thumbHtml(node) {
    if (typeof global.renderMediaThumbHtml === "function") {
      return global.renderMediaThumbHtml(node);
    }
    if (node?.imageUrl || node?.thumbUrl || node?.coverUrl) {
      const src = node.thumbUrl || node.coverUrl || node.imageUrl;
      return `<img src="${escapeHtml(src)}" alt="" draggable="false" />`;
    }
    if (node?.type === "text" || node?.type === "voice-text") {
      return `<span class="ref-txt" title="文本">T</span>`;
    }
    if (node?.audioUrl || node?.type === "ref-audio") {
      return `<span class="ref-aud" title="音频">♪</span>`;
    }
    return `<span class="ref-icon">◇</span>`;
  }

  function buildAgentMentionChipHtml(ref) {
    const board = global.CanvasStore?.activeBoard?.();
    const id = typeof ref === "string" ? ref : ref?.id;
    const node =
      typeof ref === "object" && ref?.id
        ? ref
        : board?.nodes?.find((n) => String(n.id) === String(id));
    if (!node) {
      return `<span class="mention-chip is-broken" contenteditable="false" data-ref-id="${escapeHtml(
        String(id || "")
      )}" aria-label="素材已失效"><span class="mention-chip-label">素材失效</span></span>`;
    }
    const name = nodeDisplayName(node);
    const thumb = thumbHtml(node);
    return `<span class="mention-chip" contenteditable="false" data-ref-id="${escapeHtml(
      String(node.id)
    )}" aria-label="${escapeHtml(name)}"><span class="mention-chip-thumb">${thumb}</span><span class="mention-chip-label">${escapeHtml(
      name
    )}</span></span>`;
  }

  function formatRichMentionHtml(text) {
    const raw = String(text || "");
    if (!raw) return "";
    const board = global.CanvasStore?.activeBoard?.();
    const byId = new Map((board?.nodes || []).map((n) => [String(n.id), n]));
    let html = "";
    let last = 0;
    const re = /@\[([^\]]*)\]\(([^)]+)\)|@ref:([A-Za-z0-9_-]+)/g;
    let m;
    while ((m = re.exec(raw)) !== null) {
      if (m.index > last) {
        html += escapeHtml(raw.slice(last, m.index)).replace(/\n/g, "<br>");
      }
      const id = m[2] || m[3] || "";
      const node = byId.get(String(id));
      html += buildAgentMentionChipHtml(node || id);
      last = m.index + m[0].length;
    }
    if (last < raw.length) {
      html += escapeHtml(raw.slice(last)).replace(/\n/g, "<br>");
    }
    return html;
  }

  function serializeComposeRoot(root) {
    let out = "";
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.parentElement?.closest?.(".mention-chip")) return;
        out += String(node.textContent || "").replace(/\r\n?/g, "\n");
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.classList?.contains("mention-chip")) {
        if (node.classList.contains("is-broken")) return;
        const id = node.dataset.refId || "";
        if (!id) return;
        const board = global.CanvasStore?.activeBoard?.();
        const n = board?.nodes?.find((x) => String(x.id) === String(id));
        const name = n ? nodeDisplayName(n) : node.querySelector(".mention-chip-label")?.textContent || id;
        out += `@[${name}](${id})`;
        return;
      }
      if (node.tagName === "BR") {
        out += "\n";
        return;
      }
      if (node.tagName === "DIV" || node.tagName === "P") {
        if (out.length && !out.endsWith("\n")) out += "\n";
        [...node.childNodes].forEach(walk);
        if (out.length && !out.endsWith("\n")) out += "\n";
        return;
      }
      [...node.childNodes].forEach(walk);
    };
    [...(root?.childNodes || [])].forEach(walk);
    return out.replace(/\n{3,}/g, "\n\n");
  }

  function getComposeText(el) {
    if (!el) return "";
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return String(el.value || "");
    return serializeComposeRoot(el);
  }

  function syncComposePlaceholder(el) {
    if (!el || el.tagName === "TEXTAREA" || el.tagName === "INPUT") return;
    const text = getComposeText(el).trim();
    const hasChip = !!el.querySelector?.(".mention-chip");
    el.classList.toggle("is-empty", !text && !hasChip);
    el.setAttribute("data-empty", !text && !hasChip ? "1" : "0");
  }

  function setComposeText(el, text) {
    if (!el) return;
    const val = String(text || "");
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      el.value = val;
      return;
    }
    el.innerHTML = formatRichMentionHtml(val);
    if (typeof global.hydrateMediaThumbs === "function") {
      global.hydrateMediaThumbs(el);
    }
    syncComposePlaceholder(el);
  }

  function clearCompose(el) {
    if (!el) return;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      el.value = "";
      return;
    }
    el.innerHTML = "";
    syncComposePlaceholder(el);
  }

  function getTextBeforeCaret(editor) {
    if (!editor) return "";
    if (editor.tagName === "TEXTAREA" || editor.tagName === "INPUT") {
      const val = editor.value || "";
      const pos = editor.selectionStart ?? val.length;
      return val.slice(0, pos);
    }
    const sel = global.getSelection?.();
    if (!sel || !sel.rangeCount || !editor.contains(sel.anchorNode)) {
      return getComposeText(editor);
    }
    const pre = document.createRange();
    pre.selectNodeContents(editor);
    pre.setEnd(sel.anchorNode, sel.anchorOffset);
    const holder = document.createElement("div");
    holder.appendChild(pre.cloneContents());
    return serializeComposeRoot(holder);
  }

  function placeCaretAfter(node) {
    const sel = global.getSelection?.();
    if (!sel || !node) return;
    const range = document.createRange();
    range.setStartAfter(node);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function deleteAtTokenBeforeCaret(editor) {
    const sel = global.getSelection?.();
    if (!sel || !sel.rangeCount) return false;
    const range = sel.getRangeAt(0);
    let node = range.startContainer;
    let offset = range.startOffset;
    if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node.childNodes[Math.max(0, offset - 1)] || node.childNodes[offset];
      if (child?.nodeType === Node.TEXT_NODE) {
        node = child;
        offset = child.textContent?.length || 0;
      } else {
        return false;
      }
    }
    if (node.nodeType !== Node.TEXT_NODE) return false;
    const text = node.textContent || "";
    const before = text.slice(0, offset);
    const m = before.match(/@([^\s@[\]]*)$/);
    if (!m) return false;
    const start = offset - m[0].length;
    node.textContent = before.slice(0, start) + text.slice(offset);
    const next = document.createRange();
    next.setStart(node, start);
    next.collapse(true);
    sel.removeAllRanges();
    sel.addRange(next);
    return true;
  }

  function upgradeComposeEditors(root) {
    const scope = root || document;
    const pick = (sel) => {
      if (scope === document) return $(sel);
      return scope.querySelector?.(sel) || $(sel, scope) || $(sel);
    };
    [
      ["#agent-input", "agent-input", global.AgentRules?.mentionHint || "描述操作或用 @ 引用…"],
      ["#agent-compose-input", "agent-compose-input", global.AgentRules?.mentionHint || "描述操作或用 @ 引用…"],
    ].forEach(([sel, cls, ph]) => {
      const el = pick(sel);
      if (!el) return;
      if (el.isContentEditable || el.getAttribute?.("contenteditable") === "true") {
        el.dataset.placeholder = ph;
        syncComposePlaceholder(el);
        return;
      }
      if (el.tagName !== "TEXTAREA" && el.tagName !== "INPUT") return;
      const draft = el.value || "";
      const div = document.createElement("div");
      div.id = el.id;
      div.className = cls;
      div.setAttribute("contenteditable", "true");
      div.setAttribute("role", "textbox");
      div.setAttribute("aria-multiline", "true");
      div.dataset.placeholder = ph;
      el.replaceWith(div);
      setComposeText(div, draft);
    });
  }

  function openAgentSettings() {
    if (typeof global.openMyApiSettings === "function") {
      global.openMyApiSettings({ kind: "agent" });
      return;
    }
    if (typeof global.showCanvasToast === "function") {
      global.showCanvasToast("请打开头像菜单 → MyApi 配置 → 智能体");
    }
  }

  function ensureDom() {
    if (!STATE._storageBooted) {
      STATE._storageBooted = true;
      loadPrefs();
      loadPersistedChats();
      loadPersistedRuns();
      syncFabBusy();
      if (!STATE._resumeBooted) {
        STATE._resumeBooted = true;
        setTimeout(() => {
          if (STATE.sending) return;
          if (getPersistedRun(currentBoardId())?.jobId) void resumePendingJob();
        }, 900);
      }
    }
    let root = $("#agent-panel");
    if (root) {
      if (!$("#agent-jump-bottom", root)) {
        const wrap = $("#agent-panel-body-wrap", root) || $("#agent-panel-body", root)?.parentElement;
        const body = $("#agent-panel-body", root);
        if (body && !body.parentElement?.classList?.contains("agent-panel-body-wrap")) {
          const shell = document.createElement("div");
          shell.className = "agent-panel-body-wrap";
          body.replaceWith(shell);
          shell.appendChild(body);
          const jump = document.createElement("button");
          jump.type = "button";
          jump.className = "agent-jump-bottom hidden";
          jump.id = "agent-jump-bottom";
          jump.title = "回到底部";
          jump.setAttribute("aria-label", "回到底部");
          jump.innerHTML = "<span aria-hidden=\"true\">↓</span>";
          shell.appendChild(jump);
        }
      }
      // 旧面板补齐扩大编辑入口
      if (!$("#agent-btn-expand", root)) {
        const foot = $(".agent-panel-foot", root);
        const input = $("#agent-input", root);
        if (foot && input && !input.parentElement?.classList?.contains("agent-input-wrap")) {
          const wrap = document.createElement("div");
          wrap.className = "agent-input-wrap";
          input.replaceWith(wrap);
          wrap.appendChild(input);
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "agent-input-expand";
          btn.id = "agent-btn-expand";
          btn.title = "扩大编辑";
          btn.setAttribute("aria-label", "扩大编辑");
          btn.textContent = "⤢";
          wrap.appendChild(btn);
        }
      }
      if (!$("#agent-compose-overlay", root)) {
        const overlay = document.createElement("div");
        overlay.className = "agent-compose-overlay hidden";
        overlay.id = "agent-compose-overlay";
        overlay.setAttribute("aria-hidden", "true");
        overlay.innerHTML = `
          <div class="agent-compose-card" role="dialog" aria-label="扩大编辑">
            <div class="agent-compose-head">
              <span>编辑消息</span>
              <button type="button" class="agent-icon-btn" id="agent-compose-close" title="收起" aria-label="收起">×</button>
            </div>
            <div class="agent-compose-body">
              <div id="agent-compose-input" class="agent-compose-input" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="描述操作或用 @ 引用…"></div>
              <div class="agent-mention-menu mention-menu hidden" id="agent-compose-mention-menu" role="listbox">
                <div class="mention-list" id="agent-compose-mention-list"></div>
              </div>
            </div>
            <div class="agent-compose-actions">
              <button type="button" class="agent-confirm-btn" id="agent-compose-cancel">收起</button>
              <button type="button" class="agent-confirm-btn agent-confirm-yes" id="agent-compose-send">发送</button>
            </div>
          </div>`;
        root.appendChild(overlay);
      }
      upgradeComposeEditors(root);
      const hi = $(".agent-welcome-hi", root);
      if (hi && !hi.textContent.includes("(｡･∀･)")) {
        hi.textContent = "(｡･∀･)ﾉﾞ嗨，我是小庆子";
      }
      wireCompose(root);
      return root;
    }

    root = document.createElement("aside");
    root.id = "agent-panel";
    root.className = "agent-panel hidden";
    root.setAttribute("aria-hidden", "true");
    root.innerHTML = `
      <div class="agent-panel-card">
        <header class="agent-panel-head">
          <div class="agent-panel-title-wrap">
            <button type="button" class="agent-panel-avatar" id="agent-btn-avatar" title="收起会话窗" aria-label="收起会话窗">庆</button>
            <div>
              <div class="agent-panel-title">小庆子</div>
              <div class="agent-panel-sub">智能体 · 画布助手</div>
            </div>
          </div>
          <div class="agent-panel-head-actions">
            <button type="button" class="agent-icon-btn" id="agent-btn-settings" title="配置智能体模型" aria-label="设置">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" stroke="currentColor" stroke-width="1.7"/><path d="M19.4 13a7.8 7.8 0 0 0 .1-2l2-1.2-2-3.4-2.3.7a8 8 0 0 0-1.7-1L15 3.5h-6l-.5 2.6a8 8 0 0 0-1.7 1L4.5 6.4l-2 3.4 2 1.2a7.8 7.8 0 0 0 0 2l-2 1.2 2 3.4 2.3-.7a8 8 0 0 0 1.7 1l.5 2.6h6l.5-2.6a8 8 0 0 0 1.7-1l2.3.7 2-3.4-2-1.2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
            </button>
            <button type="button" class="agent-icon-btn agent-btn-clear" id="agent-btn-clear" title="清除对话记录" aria-label="清除对话">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7h12Z" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 11v5M14 11v5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
            </button>
            <button type="button" class="agent-icon-btn" id="agent-btn-close" title="关闭" aria-label="关闭">×</button>
          </div>
        </header>
        <div class="agent-panel-body-wrap">
          <div class="agent-panel-body" id="agent-panel-body">
            <div class="agent-welcome" id="agent-welcome">
              <p class="agent-welcome-hi">(｡･∀･)ﾉﾞ嗨，我是小庆子</p>
              <p class="agent-welcome-desc">我会先思考再操作画布：创建节点、排列移动、连线与 @ 引用、截帧、AI 标记、触发生成。每个画布的对话互不影响。</p>
              <div class="agent-chips" id="agent-chips"></div>
            </div>
            <div class="agent-messages" id="agent-messages" hidden></div>
          </div>
          <button type="button" class="agent-jump-bottom hidden" id="agent-jump-bottom" title="回到底部" aria-label="回到底部" aria-hidden="true">
            <span aria-hidden="true">↓</span>
          </button>
        </div>
        <div class="agent-mention-menu mention-menu hidden" id="agent-mention-menu" role="listbox">
          <div class="mention-list" id="agent-mention-list"></div>
        </div>
        <footer class="agent-panel-foot">
          <div class="agent-input-wrap">
            <div id="agent-input" class="agent-input is-empty" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="${escapeHtml(
              global.AgentRules?.mentionHint || "描述操作或用 @ 引用…"
            )}"></div>
            <button type="button" class="agent-input-expand" id="agent-btn-expand" title="扩大编辑" aria-label="扩大编辑">⤢</button>
          </div>
          <div class="agent-foot-bar">
            <select id="agent-model-select" class="agent-model-select" title="切换智能体模型"></select>
            <div class="agent-mode-group" id="agent-role-mode" title="Agent 自动化 · Ask 仅问答" role="group" aria-label="对话模式">
              <button type="button" class="agent-mode-btn" data-agent-role="agent">Agent</button>
              <button type="button" class="agent-mode-btn" data-agent-role="ask">Ask</button>
            </div>
            <div class="agent-mode-group" id="agent-exec-mode" title="询问先确认 · 操作直接执行普通操作，生成始终确认" role="group" aria-label="执行方式">
              <button type="button" class="agent-mode-btn" data-agent-exec="confirm">询问</button>
              <button type="button" class="agent-mode-btn" data-agent-exec="auto">操作</button>
            </div>
            <button type="button" class="agent-send-btn" id="agent-btn-send" title="发送">↑</button>
          </div>
        </footer>
      </div>
      <div class="agent-compose-overlay hidden" id="agent-compose-overlay" aria-hidden="true">
        <div class="agent-compose-card" role="dialog" aria-label="扩大编辑">
          <div class="agent-compose-head">
            <span>编辑消息</span>
            <button type="button" class="agent-icon-btn" id="agent-compose-close" title="收起" aria-label="收起">×</button>
          </div>
          <div class="agent-compose-body">
            <div id="agent-compose-input" class="agent-compose-input is-empty" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="${escapeHtml(
              global.AgentRules?.mentionHint || "描述操作或用 @ 引用…"
            )}"></div>
            <div class="agent-mention-menu mention-menu hidden" id="agent-compose-mention-menu" role="listbox">
              <div class="mention-list" id="agent-compose-mention-list"></div>
            </div>
          </div>
          <div class="agent-compose-actions">
            <button type="button" class="agent-confirm-btn" id="agent-compose-cancel">收起</button>
            <button type="button" class="agent-confirm-btn agent-confirm-yes" id="agent-compose-send">发送</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(root);
    wire(root);
    renderChips();
    void refreshModelSelect();
    syncBoardUi(currentBoardId());
    return root;
  }

  function renderChips() {
    const host = $("#agent-chips");
    if (!host) return;
    const actions = global.AgentRules?.quickActions || [];
    host.innerHTML = actions
      .map(
        (a) =>
          `<button type="button" class="agent-chip" data-agent-chip="${escapeHtml(a.id)}">${escapeHtml(
            a.label
          )}</button>`
      )
      .join("");
  }

  async function refreshModelSelect() {
    const sel = $("#agent-model-select");
    if (!sel) return;
    const models = (await global.AgentApi?.listAgentModels?.({ force: true })) || [];
    // 仅智能体 kind；视觉模型不出现在此列表
    const agents = models.filter((m) => !m.kind || m.kind === "agent");
    const prev = sel.value;
    if (!agents.length) {
      sel.innerHTML = `<option value="">未配置智能体</option><option value="__settings__">＋ 去添加智能体模型…</option>`;
    } else {
      sel.innerHTML =
        agents
          .map((m) => {
            const label = m.public
              ? `公共 · ${m.name || m.id}`
              : m.volcengine
                ? `火山 · ${m.name || m.id}`
                : m.name || m.id;
            return `<option value="${escapeHtml(m.id)}"${m.is_default ? " selected" : ""}>${escapeHtml(
              label
            )}</option>`;
          })
          .join("") + `<option value="__settings__">＋ 管理智能体模型…</option>`;
      if (prev && prev !== "__settings__" && agents.some((m) => m.id === prev)) {
        sel.value = prev;
      } else {
        const def = agents.find((m) => m.is_default) || agents[0];
        if (def) sel.value = def.id;
      }
    }
    if (global.UiSelect?.enhance) {
      global.UiSelect.enhance(sel);
      global.UiSelect.refresh?.(sel);
    }
  }

  async function confirmClearChat() {
    if (!STATE.history.length && !getComposeText($("#agent-input")).trim()) {
      clearChat();
      return;
    }
    let ok = true;
    if (typeof global.AppDialog?.confirmDelete === "function") {
      ok = await global.AppDialog.confirmDelete("清除当前画布的对话记录？此操作不可撤销。", {
        title: "清除对话",
        confirmText: "清除",
      });
    } else if (typeof global.AppDialog?.confirm === "function") {
      ok = await global.AppDialog.confirm("清除当前画布的对话记录？", { title: "清除对话" });
    } else {
      ok = window.confirm("清除当前画布的对话记录？");
    }
    if (ok) clearChat();
  }

  function appendMessage(role, text, extra = {}) {
    if (!STATE.boardId) STATE.boardId = currentBoardId();
    const welcome = $("#agent-welcome");
    const box = $("#agent-messages");
    if (welcome) welcome.hidden = true;
    if (box) box.hidden = false;
    const entry = {
      role,
      content: String(text || ""),
      thinking: String(extra.thinking || ""),
    };
    box.insertAdjacentHTML("beforeend", messageBubbleHtml(entry));
    if (role === "user" && typeof global.hydrateMediaThumbs === "function") {
      const last = box.lastElementChild;
      if (last) global.hydrateMediaThumbs(last);
    }
    STATE.history.push(entry);
    const chat = ensureBoardChat(STATE.boardId);
    chat.history = STATE.history.slice();
    persistChats();
    scrollToBottom({ force: STATE.stickToBottom });
  }

  function clearChat() {
    STATE.history = [];
    STATE.mentions = [];
    STATE.stickToBottom = true;
    STATE.uiStart = 0;
    if (STATE.boardId) {
      const chat = ensureBoardChat(STATE.boardId);
      chat.history = [];
      chat.draft = "";
      persistChats();
    }
    const box = $("#agent-messages");
    if (box) {
      box.innerHTML = "";
      box.hidden = true;
    }
    const welcome = $("#agent-welcome");
    if (welcome) welcome.hidden = false;
    const input = $("#agent-input");
    if (input) clearCompose(input);
    updateJumpBtn();
  }

  function activeComposeInput() {
    const overlay = $("#agent-compose-overlay");
    if (overlay && !overlay.classList.contains("hidden")) {
      return $("#agent-compose-input");
    }
    return $("#agent-input");
  }

  function mentionMenuEls() {
    const overlay = $("#agent-compose-overlay");
    if (overlay && !overlay.classList.contains("hidden")) {
      return {
        menu: $("#agent-compose-mention-menu"),
        list: $("#agent-compose-mention-list"),
      };
    }
    return {
      menu: $("#agent-mention-menu"),
      list: $("#agent-mention-list"),
    };
  }

  function hideMentionMenu() {
    ["#agent-mention-menu", "#agent-compose-mention-menu"].forEach((sel) => {
      const menu = $(sel);
      if (menu) menu.classList.add("hidden");
    });
    ["#agent-mention-list", "#agent-compose-mention-list"].forEach((sel) => {
      const list = $(sel);
      if (list) list.innerHTML = "";
    });
  }

  function showMentionMenu(query) {
    const { menu, list: listEl } = mentionMenuEls();
    let list = listEl;
    if (!menu) return;
    if (!list) {
      list = document.createElement("div");
      list.className = "mention-list";
      list.id = menu.id === "agent-compose-mention-menu" ? "agent-compose-mention-list" : "agent-mention-list";
      menu.appendChild(list);
    }
    const items = listMentionCandidates(query);
    if (!items.length) {
      hideMentionMenu();
      return;
    }
    list.innerHTML = items
      .map((it) => {
        const name = it.name || it.id;
        return `<button type="button" class="mention-item" data-id="${escapeHtml(
          it.id
        )}" data-name="${escapeHtml(name)}" role="option" title="${escapeHtml(name)}">
          <span class="mention-item-thumb">${thumbHtml(it.node)}</span>
          <span class="mention-item-label">${escapeHtml(name)}</span>
        </button>`;
      })
      .join("");
    menu.classList.remove("hidden");
    if (typeof global.hydrateMediaThumbs === "function") {
      global.hydrateMediaThumbs(list);
    }
    const first = list.querySelector(".mention-item");
    if (first) first.classList.add("is-active");
  }

  function insertMention(id, name) {
    const input = activeComposeInput();
    if (!input) return;
    const board = global.CanvasStore?.activeBoard?.();
    const node = board?.nodes?.find((n) => String(n.id) === String(id));
    const label = name || (node ? nodeDisplayName(node) : id);

    if (input.isContentEditable || input.getAttribute?.("contenteditable") === "true") {
      input.focus();
      deleteAtTokenBeforeCaret(input);
      const mount = document.createElement("div");
      mount.innerHTML = buildAgentMentionChipHtml(node || id);
      const chip = mount.firstElementChild;
      const space = document.createTextNode("\u00a0");
      const sel = global.getSelection?.();
      if (chip && sel && sel.rangeCount && input.contains(sel.anchorNode)) {
        const range = sel.getRangeAt(0);
        range.deleteContents();
        range.insertNode(space);
        range.insertNode(chip);
        placeCaretAfter(space);
      } else if (chip) {
        input.appendChild(chip);
        input.appendChild(space);
        placeCaretAfter(space);
      }
      if (typeof global.hydrateMediaThumbs === "function") {
        global.hydrateMediaThumbs(input);
      }
      syncComposePlaceholder(input);
    } else {
      const val = input.value || "";
      const pos = input.selectionStart ?? val.length;
      const before = val.slice(0, pos);
      const after = val.slice(pos);
      const at = before.lastIndexOf("@");
      if (at < 0) return;
      const token = `@[${label}](${id}) `;
      input.value = before.slice(0, at) + token + after;
      const next = at + token.length;
      input.setSelectionRange(next, next);
      input.focus();
    }

    STATE.mentions.push({ id, name: label });
    hideMentionMenu();
    const main = $("#agent-input");
    if (main && input !== main) setComposeText(main, getComposeText(input));
    snapshotCurrentBoard();
  }

  function openComposeExpand() {
    const overlay = $("#agent-compose-overlay");
    const main = $("#agent-input");
    const big = $("#agent-compose-input");
    if (!overlay || !big) return;
    setComposeText(big, getComposeText(main));
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => {
      big.focus();
      const sel = global.getSelection?.();
      if (!sel) return;
      const range = document.createRange();
      range.selectNodeContents(big);
      range.collapse(false);
      sel.removeAllRanges();
      sel.addRange(range);
    });
  }

  function closeComposeExpand({ sync = true } = {}) {
    const overlay = $("#agent-compose-overlay");
    const main = $("#agent-input");
    const big = $("#agent-compose-input");
    if (sync && main && big) setComposeText(main, getComposeText(big));
    hideMentionMenu();
    if (overlay) {
      overlay.classList.add("hidden");
      overlay.setAttribute("aria-hidden", "true");
    }
    if (STATE.boardId && main) {
      ensureBoardChat(STATE.boardId).draft = getComposeText(main);
      persistChats();
    }
    main?.focus();
  }

  function bindInputMention(input) {
    if (!input || input.dataset.mentionBound === "1") return;
    input.dataset.mentionBound = "1";
    input.addEventListener("keydown", (e) => {
      const { menu, list } = mentionMenuEls();
      const openMenu = menu && !menu.classList.contains("hidden");
      if (openMenu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const items = [...(list?.querySelectorAll(".mention-item") || [])];
        if (!items.length) return;
        let idx = items.findIndex((el) => el.classList.contains("is-active"));
        if (e.key === "ArrowDown") idx = idx < 0 ? 0 : (idx + 1) % items.length;
        else idx = idx <= 0 ? items.length - 1 : idx - 1;
        items.forEach((el, i) => el.classList.toggle("is-active", i === idx));
        items[idx]?.scrollIntoView?.({ block: "nearest" });
        return;
      }
      if (openMenu && e.key === "Enter" && !e.shiftKey) {
        const active = list?.querySelector(".mention-item.is-active");
        if (active) {
          e.preventDefault();
          insertMention(active.dataset.id, active.dataset.name);
          return;
        }
      }
      if (e.key === "Enter" && !e.shiftKey && input.id === "agent-input") {
        e.preventDefault();
        void sendCurrent();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        if (STATE.sending) {
          abortCurrent();
          return;
        }
        if (openMenu) hideMentionMenu();
        else if (input.id === "agent-compose-input") closeComposeExpand();
        else {
          // Blur so canvas shortcuts (WASD…) take over again; close via × / avatar.
          input.blur?.();
        }
      }
    });
    input.addEventListener("input", () => {
      syncComposePlaceholder(input);
      const before = getTextBeforeCaret(input);
      const m = before.match(/@([^\s@[\]]*)$/);
      if (m) showMentionMenu(m[1]);
      else hideMentionMenu();
      const main = $("#agent-input");
      const draft = getComposeText(input.id === "agent-compose-input" ? input : main || input);
      if (input.id === "agent-compose-input" && main) {
        // 扩大编辑时草稿以大框为准，收起时再同步视觉
      }
      if (STATE.boardId) {
        ensureBoardChat(STATE.boardId).draft = draft;
        persistChats();
      }
    });
    input.addEventListener("blur", () => syncComposePlaceholder(input));
    input.addEventListener("focus", () => syncComposePlaceholder(input));
    syncComposePlaceholder(input);
  }

  function parseMentionsFromInput(text) {
    const re = /@\[([^\]]+)\]\(([^)]+)\)/g;
    const out = [];
    let m;
    while ((m = re.exec(String(text || ""))) !== null) {
      out.push({ name: m[1], id: m[2] });
    }
    return out;
  }

  async function sendCurrent() {
    if (STATE.sending) {
      abortCurrent();
      return;
    }
    if (!STATE.boardId) STATE.boardId = currentBoardId();
    const input = $("#agent-input");
    const compose = $("#agent-compose-overlay");
    const active =
      compose && !compose.classList.contains("hidden") ? $("#agent-compose-input") : input;
    const text = getComposeText(active).trim();
    if (!text) return;

    const ac = new AbortController();
    STATE.abortController = ac;
    STATE.sending = true;
    STATE.stickToBottom = true;
    setSendBusy(true);
    invalidateOpenPlans();

    appendMessage("user", text);
    clearCompose(input);
    if (active && active !== input) clearCompose(active);
    if (compose && !compose.classList.contains("hidden")) {
      closeComposeExpand({ sync: false });
    }
    snapshotCurrentBoard();
    hideMentionMenu();
    const requestBoardId = currentBoardId();

    const mentions = parseMentionsFromInput(text);
    const intent = global.AgentIntent.detectLocalIntent(text, { mentions });
    const modelSel = $("#agent-model-select");
    const modelId = modelSel?.value || "";
    const modelLabel = modelSel?.selectedOptions?.[0]?.textContent?.trim() || "";
    const light = global.AgentApi?.isLightChat?.(text) === true;
    beginThinkingShell();

    try {
      const packed = buildCompactModelContext(STATE.history, { excludeLast: true });

      const result = await global.AgentIntent.runIntent(intent, jobBindOpts({
        modelId: modelId || undefined,
        modelLabel,
        history: packed.history,
        memory: packed.memory,
        roleMode: STATE.roleMode,
        execMode: STATE.execMode,
        signal: ac.signal,
        light,
        boardId: requestBoardId,
      }));
      removePending();
      clearActiveRun(requestBoardId);
      if (currentBoardId() !== requestBoardId) {
        ensureBoardChat(requestBoardId).history.push({ role: "assistant", content: result.reply || "画板已切换，未执行操作。" });
        persistChats();
        return;
      }
      STATE.pendingActions = result.pendingActions || null;
      STATE.pendingChoices = result.pendingChoices || null;
      await appendAssistantTyped(
        result.reply || (result.ok ? "完成" : "失败"),
        result.thinking || "",
        {
          pendingActions: result.pendingActions,
          pendingChoices: result.pendingChoices,
          planSummary: result.planSummary,
          execLogs: result.execLogs,
        }
      );
      const created = result.result?.createdIds?.length || 0;
      if (typeof global.showCanvasToast === "function" && result.ok && created) {
        global.showCanvasToast(`小庆子：已创建 ${created} 个节点`);
      }
    } catch (err) {
      removePending();
      clearActiveRun(requestBoardId);
      if (ac.signal.aborted || global.AgentApi?.isAbortError?.(err, ac.signal) || err?.name === "AbortError") {
        await appendAssistantTyped("已停止。", "");
        return;
      }
      const raw = String(err?.message || err || "").trim() || "出错了";
      const status = err?.status ? `HTTP ${err.status}` : "";
      let kind = "未知原因";
      if (/超时|timeout|timed out/i.test(raw)) kind = "超时";
      else if (/过长|context|token|too long|too large|413/i.test(raw)) kind = "内容过长";
      else if (/鉴权|api key|401|403|unauthorized/i.test(raw)) kind = "鉴权/配置";
      else if (/连不上|network|fetch|failed to fetch|Base URL|任务服务|8089/i.test(raw)) kind = "网络/地址";
      else if (/429|限流|rate limit/i.test(raw)) kind = "限流";
      else if (/502|500|上游/i.test(raw) || status) kind = "上游/服务错误";
      await appendAssistantTyped(
        raw,
        `调用失败分类：${kind}${status ? `（${status}）` : ""}。\n原始错误：${raw}`
      );
    } finally {
      STATE.sending = false;
      STATE.abortController = null;
      STATE.activeJobId = "";
      setSendBusy(false);
    }
  }

  async function presentFollowupAsk(ask) {
    if (!ask?.choices?.length && !ask?.pendingChoices?.length) return;
    let choices = ask.pendingChoices || ask.choices || [];
    if (!choices.some((c) => c._customSay || c.id === "custom_say" || /自己说/.test(c.label || ""))) {
      choices = choices.concat([{ id: "custom_say", label: "自己说", actions: [], _customSay: true }]);
    }
    await appendAssistantTyped(ask.reply || "请选择：", ask.thinking || "", {
      pendingChoices: choices,
    });
  }

  async function presentPendingExecution(exec) {
    if (!exec?.pendingActions?.length) return false;
    STATE.pendingActions = exec.pendingActions;
    STATE.pendingChoices = null;
    await appendAssistantTyped("请核对以下生成目标，确认后开始生成。", "", {
      pendingActions: exec.pendingActions,
      planSummary: global.AgentIntent.summarizeActions(exec.pendingActions),
      execLogs: exec.logs || [],
    });
    return true;
  }

  async function runPanelActions(actions, confirmed) {
    const ac = new AbortController();
    STATE.abortController = ac;
    STATE.sending = true;
    setSendBusy(true);
    try {
      return await global.AgentIntent.executePendingActions(actions, { confirmed, signal: ac.signal });
    } catch (err) {
      return { logs: [err?.name === "AbortError" ? "已停止后续操作。" : `执行失败：${err?.message || err}`], createdIds: [] };
    } finally {
      STATE.sending = false;
      STATE.abortController = null;
      setSendBusy(false);
    }
  }

  async function handleConfirmActions(bar, approved) {
    if (STATE.sending || bar?._consumed) return;
    if (bar) bar._consumed = true;
    const actions = bar?._pendingActions || STATE.pendingActions;
    dismissInteractivePlan(bar);
    STATE.pendingActions = null;
    STATE.pendingChoices = null;
    if (!approved || !actions?.length) {
      if (typeof global.showCanvasToast === "function") {
        global.showCanvasToast(approved ? "没有可执行操作" : "已跳过操作");
      }
      return;
    }
    const exec = await runPanelActions(actions, true);
    const logs = exec?.logs || [];
    if (logs.length) appendResultCard("result", "执行结果", logs);
    else {
      appendMessage(
        "assistant",
        "确认了，但没有实际改画布（操作引擎未就绪或动作为空）。请刷新页面后再试一次。"
      );
    }
    const failed = logs.some((l) => /失败/.test(String(l)));
    const created = exec?.createdIds?.length || 0;
    if (created && typeof global.showCanvasToast === "function") {
      global.showCanvasToast(`小庆子：已创建 ${created} 个节点`);
    } else if (failed && typeof global.showCanvasToast === "function") {
      global.showCanvasToast("小庆子：执行失败，见聊天记录");
    }
    const ask =
      typeof global.AgentIntent?.buildNextModelAsk === "function"
        ? global.AgentIntent.buildNextModelAsk()
        : null;
    if (ask) await presentFollowupAsk(ask);
  }

  async function handleChoiceActions(bar, index) {
    if (STATE.sending) {
      abortCurrent();
      return;
    }
    const choices = bar?._pendingChoices || STATE.pendingChoices || [];
    const choice = choices[Number(index)];
    dismissInteractivePlan(bar);
    STATE.pendingChoices = null;
    STATE.pendingActions = null;
    if (!choice) return;

    // 「自己说」：收起选项，聚焦输入框让用户自由表达
    if (choice._customSay || choice.id === "custom_say") {
      appendMessage("assistant", "好的，请直接打字告诉我你的想法。");
      const input = $("#agent-input") || $("#agent-compose-input");
      if (input) {
        input.focus?.();
        try {
          const range = document.createRange();
          range.selectNodeContents(input);
          range.collapse(false);
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(range);
        } catch {
          /* ignore */
        }
      }
      if (typeof global.showCanvasToast === "function") {
        global.showCanvasToast("请输入你的想法后发送");
      }
      return;
    }

    const actions = Array.isArray(choice.actions) ? choice.actions : [];
    // 无现成 actions：把选项当作用户下一句，再走思考
    if (!actions.length) {
      await sendChoiceFollowUp(String(choice.message || choice.label || "").trim() || "继续", choice.label);
      return;
    }
    const exec = await runPanelActions(actions, false);
    if (await presentPendingExecution(exec)) return;
    const logs = exec?.logs || [];
    if (logs.length) {
      appendResultCard("result", `已按「${choice.label}」执行`, logs);
    } else {
      appendMessage("assistant", `已按「${choice.label}」执行。`);
    }
    const created = exec?.createdIds?.length || 0;
    if (created && typeof global.showCanvasToast === "function") {
      global.showCanvasToast(`小庆子：已创建 ${created} 个节点`);
    }
    let ask = null;
    if (typeof global.AgentIntent?.continueModelAskAfterChoice === "function") {
      ask = global.AgentIntent.continueModelAskAfterChoice(choice);
    } else if (typeof global.AgentIntent?.buildNextModelAsk === "function") {
      ask = global.AgentIntent.buildNextModelAsk();
    }
    if (ask) await presentFollowupAsk(ask);
  }

  /** 点选无 actions 的选项：当作新用户消息再思考一轮 */
  async function sendChoiceFollowUp(text, displayLabel) {
    const label = String(text || "").trim();
    if (!label) return;

    // 找回上一句真实用户指令，避免点到无关固定项时模型另起炉灶
    let lastUser = "";
    for (let i = STATE.history.length - 1; i >= 0; i -= 1) {
      const h = STATE.history[i];
      if (h?.role !== "user") continue;
      const c = String(h.content || "").trim();
      if (!c) continue;
      // 跳过上一轮也是点选短标签的情况，再往前找
      if (
        /^(创建相关节点|整理排版|检查画布情况|只分析不改画布|先跳过|自己说)$/.test(c)
      ) {
        continue;
      }
      lastUser = c;
      break;
    }

    let msg = label;
    if (lastUser && lastUser !== label) {
      msg =
        `用户点选了选项「${label}」。` +
        `这是对你上一问的回答，请结合用户更早的原话执行：\n「${lastUser}」\n` +
        `禁止改成无关操作（例如用户在说牵线/@ 时不要去「创建相关节点」）。` +
        `若选项本身已是完整指令，就按选项执行。`;
    } else {
      msg =
        `用户点选了「${label}」。请按该选项作为本轮指令执行；` +
        `选项须落实为画布 actions，不要再反问一套无关菜单。`;
    }

    // 把画布上已有人物/图节点名塞进跟进句，避免模型再喊「未检测到节点」
    try {
      const canvas =
        typeof global.AgentRunner?.buildCanvasContext === "function"
          ? global.AgentRunner.buildCanvasContext()
          : null;
      const total = Number(canvas?.stats?.total || 0);
      if (total > 0 && /牵线|换衣服|更换|艾特|@|引用|删|连线|创建|节点|排版|整理/.test(msg)) {
        const names = (canvas.node_index || canvas.nodes || [])
          .slice(0, 40)
          .map((n) => String(n.name || n.customName || n.id || "").trim())
          .filter(Boolean);
        const hint = names.length
          ? `画布已有 ${total} 个节点（含：${names.slice(0, 16).join("、")}）。请直接用这些节点 id/名称执行，禁止再说未检测到。`
          : `画布已有 ${total} 个节点，请直接操作，禁止再说未检测到。`;
        msg = `${msg}\n${hint}`;
      }
      // 仅当选项本身在说「按视频编号创建」时才暗示 create_from_script
      if (/按视频编号/.test(label) || (/分镜/.test(label) && /创建/.test(label))) {
        msg +=
          "\n请返回 create_from_script（script 为空字符串），前端会从会话回填完整分镜；不要再追问。";
      }
    } catch {
      /* ignore */
    }

    STATE.stickToBottom = true;
    appendMessage("user", label);
    const ac = new AbortController();
    STATE.abortController = ac;
    STATE.sending = true;
    setSendBusy(true);
    beginThinkingShell();
    try {
      const intent = global.AgentIntent.detectLocalIntent(msg, {});
      // 展示用短文案（选项原文），喂模型用增强后的 msg
      if (intent?.payload) {
        intent.payload.text = msg;
        intent.payload.displayText = label;
      }
      const packed = buildCompactModelContext(STATE.history, { excludeLast: true });
      const result = await global.AgentIntent.runIntent(intent, jobBindOpts({
        roleMode: STATE.roleMode === "ask" ? "ask" : "agent",
        execMode: STATE.roleMode === "ask" ? "confirm" : STATE.execMode,
        modelId: $("#agent-model-select")?.value || undefined,
        history: packed.history,
        memory: packed.memory,
        signal: ac.signal,
      }));
      removePending();
      clearActiveRun(STATE.boardId);
      STATE.pendingActions = result.pendingActions || null;
      STATE.pendingChoices = result.pendingChoices || null;
      await appendAssistantTyped(result.reply || (result.ok ? "完成" : "失败"), result.thinking || "", {
        pendingActions: result.pendingActions,
        pendingChoices: result.pendingChoices,
        planSummary: result.planSummary,
        execLogs: result.execLogs,
      });
    } catch (err) {
      removePending();
      clearActiveRun(STATE.boardId);
      if (global.AgentApi?.isAbortError?.(err) || err?.name === "AbortError") {
        await appendAssistantTyped("已停止。", "");
      } else {
        await appendAssistantTyped(err?.message || "失败", "");
      }
    } finally {
      STATE.sending = false;
      STATE.abortController = null;
      STATE.activeJobId = "";
      setSendBusy(false);
    }
  }

  function wire(root) {
    $("#agent-btn-close", root)?.addEventListener("click", () => close());
    $("#agent-btn-avatar", root)?.addEventListener("click", () => close());
    $("#agent-btn-clear", root)?.addEventListener("click", () => void confirmClearChat());
    $("#agent-btn-new", root)?.addEventListener("click", () => void confirmClearChat());
    $("#agent-btn-settings", root)?.addEventListener("click", () => openAgentSettings());
    $("#agent-btn-send", root)?.addEventListener("click", () => {
      if (STATE.sending) abortCurrent();
      else void sendCurrent();
    });
    $("#agent-jump-bottom", root)?.addEventListener("click", () => scrollToBottom({ force: true }));

    root.querySelectorAll("[data-agent-role]").forEach((btn) => {
      btn.addEventListener("click", () => {
        STATE.roleMode = btn.dataset.agentRole === "ask" ? "ask" : "agent";
        persistPrefs();
        syncModeUi(root);
      });
    });
    root.querySelectorAll("[data-agent-exec]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (STATE.roleMode === "ask") return;
        STATE.execMode = btn.dataset.agentExec === "auto" ? "auto" : "confirm";
        persistPrefs();
        syncModeUi(root);
      });
    });
    syncModeUi(root);

    const modelSel = $("#agent-model-select", root);
    modelSel?.addEventListener("change", () => {
      if (modelSel.value === "__settings__") {
        openAgentSettings();
        const firstReal = [...modelSel.options].find((o) => o.value && o.value !== "__settings__");
        modelSel.value = firstReal?.value || "";
        global.UiSelect?.refresh?.(modelSel);
      }
    });
    if (global.UiSelect?.enhance && modelSel) {
      global.UiSelect.enhance(modelSel);
    }
    const body = $("#agent-panel-body", root);
    body?.addEventListener(
      "scroll",
      () => {
        if (body.scrollTop < 48) {
          loadOlderMessages();
        }
        if (isNearBottom(body)) {
          STATE.stickToBottom = true;
        } else {
          STATE.stickToBottom = false;
        }
        updateJumpBtn();
      },
      { passive: true }
    );

    root.addEventListener("click", (e) => {
      const toggle = e.target.closest("[data-agent-result-toggle]");
      if (toggle) {
        e.preventDefault();
        e.stopPropagation();
        const card = toggle.closest(".agent-result-card");
        const more = card?.querySelector(".agent-result-list-more");
        if (!more) return;
        const willExpand = more.classList.contains("hidden");
        setResultCardExpanded(card, willExpand);
        return;
      }
      const copyBtn = e.target.closest(".agent-msg-copy");
      if (copyBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = copyBtn.closest(".agent-result-card");
        if (card) {
          const items = [...card.querySelectorAll(".agent-result-list li")].map((li) => li.textContent || "");
          const title = card.querySelector(".agent-result-title")?.textContent || "";
          copyText([title, ...items.map((x) => `· ${x}`)].filter(Boolean).join("\n"));
          return;
        }
        const bubble = copyBtn.closest(".agent-msg-bubble-wrap")?.querySelector(".agent-msg-bubble");
        copyText(bubble?.innerText || bubble?.textContent || "");
        return;
      }
      const choiceBtn = e.target.closest("[data-agent-choice]");
      if (choiceBtn) {
        const bar = choiceBtn.closest(".agent-confirm-bar");
        void handleChoiceActions(bar, choiceBtn.dataset.agentChoice);
        return;
      }
      const confirmBtn = e.target.closest("[data-agent-confirm]");
      if (confirmBtn) {
        const bar = confirmBtn.closest(".agent-confirm-bar");
        void handleConfirmActions(bar, confirmBtn.dataset.agentConfirm === "yes");
        return;
      }
      const chip = e.target.closest("[data-agent-chip]");
      if (chip) {
        const id = chip.dataset.agentChip;
        if (id === "split-script") {
          const input = $("#agent-input");
          if (input) {
            setComposeText(
              input,
              "请先思考，再把下面分镜脚本创建成视频节点（node_type=video，不要建成文本节点），按视频编号拆分并规律排布：\n\n"
            );
            input.focus();
          }
        } else if (id === "layout-selected") {
          void (async () => {
            if (STATE.sending) {
              abortCurrent();
              return;
            }
            STATE.stickToBottom = true;
            const welcome = $("#agent-welcome");
            const box = $("#agent-messages");
            if (welcome) welcome.hidden = true;
            if (box) box.hidden = false;
            appendMessage("user", "整理选中节点");
            const ac = new AbortController();
            STATE.abortController = ac;
            STATE.sending = true;
            setSendBusy(true);
            beginThinkingShell();
            try {
              const intent = global.AgentIntent.detectLocalIntent("整理选中节点", {});
              const packed = buildCompactModelContext(STATE.history, { excludeLast: true });
              const remote = await global.AgentIntent.runIntent(intent, jobBindOpts({
                roleMode: STATE.roleMode,
                execMode: STATE.execMode,
                modelId: $("#agent-model-select")?.value || undefined,
                history: packed.history,
                memory: packed.memory,
                signal: ac.signal,
              }));
              removePending();
              clearActiveRun(STATE.boardId);
              await appendAssistantTyped(remote.reply, remote.thinking || "", {
                pendingActions: remote.pendingActions,
                pendingChoices: remote.pendingChoices,
                planSummary: remote.planSummary,
                execLogs: remote.execLogs,
              });
            } catch (err) {
              removePending();
              clearActiveRun(STATE.boardId);
              if (global.AgentApi?.isAbortError?.(err) || err?.name === "AbortError") {
                await appendAssistantTyped("已停止。", "");
              } else {
                await appendAssistantTyped(err?.message || "失败", "");
              }
            } finally {
              STATE.sending = false;
              STATE.abortController = null;
              STATE.activeJobId = "";
              setSendBusy(false);
            }
          })();
        } else if (id === "help") {
          void (async () => {
            if (STATE.sending) {
              abortCurrent();
              return;
            }
            STATE.stickToBottom = true;
            appendMessage("user", "你会什么");
            const ac = new AbortController();
            STATE.abortController = ac;
            STATE.sending = true;
            setSendBusy(true);
            beginThinkingShell();
            try {
              const intent = global.AgentIntent.detectLocalIntent("你会什么", {});
              const packed = buildCompactModelContext(STATE.history, { excludeLast: true });
              const result = await global.AgentIntent.runIntent(intent, jobBindOpts({
                roleMode: STATE.roleMode,
                execMode: STATE.execMode,
                modelId: $("#agent-model-select")?.value || undefined,
                history: packed.history,
                memory: packed.memory,
                signal: ac.signal,
                light: true,
              }));
              removePending();
              clearActiveRun(STATE.boardId);
              await appendAssistantTyped(result.reply, result.thinking || "", {
                pendingActions: result.pendingActions,
                pendingChoices: result.pendingChoices,
                planSummary: result.planSummary,
                execLogs: result.execLogs,
              });
            } catch (err) {
              removePending();
              clearActiveRun(STATE.boardId);
              if (global.AgentApi?.isAbortError?.(err) || err?.name === "AbortError") {
                await appendAssistantTyped("已停止。", "");
              } else {
                await appendAssistantTyped(err?.message || "失败", "");
              }
            } finally {
              STATE.sending = false;
              STATE.abortController = null;
              STATE.activeJobId = "";
              setSendBusy(false);
            }
          })();
        }
        return;
      }
    });

    root.addEventListener("mousedown", (e) => {
      const item = e.target.closest(
        "#agent-mention-menu .mention-item, #agent-compose-mention-menu .mention-item"
      );
      if (!item) return;
      e.preventDefault();
      e.stopPropagation();
      insertMention(item.dataset.id, item.dataset.name);
    });

    root.addEventListener("mouseover", (e) => {
      const item = e.target.closest(
        "#agent-mention-menu .mention-item, #agent-compose-mention-menu .mention-item"
      );
      if (!item) return;
      const list = item.closest(".mention-list");
      list
        ?.querySelectorAll(".mention-item")
        .forEach((b) => b.classList.toggle("is-active", b === item));
    });

    wireCompose(root);
  }

  function isAgentComposeTarget(el) {
    if (!(el instanceof Element)) return false;
    return !!el.closest(
      "#agent-input, #agent-compose-input, #agent-compose-overlay, .agent-input-wrap, " +
        ".agent-compose-card, .agent-mention-menu, .agent-panel-foot, #agent-model-select, " +
        ".ui-select-menu, .agent-mode-group"
    );
  }

  function blurAgentCompose() {
    const ae = document.activeElement;
    if (!(ae instanceof Element)) return;
    if (!ae.closest?.("#agent-input, #agent-compose-input")) return;
    try {
      ae.blur();
    } catch {
      /* ignore */
    }
  }

  function bindAgentShortcutPriority(root) {
    if (!root || root.dataset.shortcutPriorityBound === "1") return;
    root.dataset.shortcutPriorityBound = "1";
    // Clicking chat history / chrome (not compose) releases input focus → canvas keys win.
    root.addEventListener(
      "pointerdown",
      (e) => {
        if (!(e.target instanceof Element)) return;
        if (isAgentComposeTarget(e.target)) return;
        blurAgentCompose();
      },
      true
    );
    // Clicking the canvas / elsewhere while panel is open also releases compose focus.
    if (!window.__agentComposeBlurBound) {
      window.__agentComposeBlurBound = true;
      document.addEventListener(
        "pointerdown",
        (e) => {
          if (!STATE.open) return;
          if (!(e.target instanceof Element)) return;
          if (e.target.closest?.("#agent-panel")) return;
          blurAgentCompose();
        },
        true
      );
    }
  }

  function wireCompose(root) {
    upgradeComposeEditors(root);
    bindAgentShortcutPriority(root);
    if (!root || root.dataset.composeWired === "1") {
      bindInputMention($("#agent-input", root));
      bindInputMention($("#agent-compose-input", root));
      return;
    }
    root.dataset.composeWired = "1";
    $("#agent-btn-expand", root)?.addEventListener("click", () => openComposeExpand());
    $("#agent-compose-close", root)?.addEventListener("click", () => closeComposeExpand());
    $("#agent-compose-cancel", root)?.addEventListener("click", () => closeComposeExpand());
    $("#agent-compose-send", root)?.addEventListener("click", () => {
      closeComposeExpand({ sync: true });
      void sendCurrent();
    });
    $("#agent-compose-overlay", root)?.addEventListener("click", (e) => {
      if (e.target?.id === "agent-compose-overlay") closeComposeExpand();
    });
    bindInputMention($("#agent-input", root));
    bindInputMention($("#agent-compose-input", root));
  }

  async function resumePendingJob() {
    if (STATE.sending) return;
    const boardId = STATE.boardId || currentBoardId();
    const run = getPersistedRun(boardId);
    const jobId = String(run?.jobId || "").trim();
    if (!jobId || typeof global.AgentApi?.pollJob !== "function") return;

    // Load this board's history before appending a background result. A closed
    // panel may still hold the previous board's messages after navigation.
    if (STATE.boardId !== boardId) syncBoardUi(boardId, { keepStick: true });

    const ac = new AbortController();
    STATE.abortController = ac;
    STATE.sending = true;
    STATE.activeJobId = jobId;
    STATE.stickToBottom = true;
    setSendBusy(true);
    beginThinkingShell();

    try {
      const data = await global.AgentApi.pollJob(jobId, { signal: ac.signal });
      if (ac.signal.aborted || currentBoardId() !== boardId) return;
      removePending();
      clearActiveRun(boardId);
      // Map raw plan into the same shape Intent would produce for display.
      const reply = data?.reply || "完成";
      const thinking = data?.thinking || "";
      const stamp = (a) => ({ ...a, _board_id: boardId });
      const actions = Array.isArray(data?.actions) ? data.actions.map(stamp) : [];
      const choices = (Array.isArray(data?.choices) ? data.choices : []).map((c) => ({
        ...c, actions: STATE.roleMode === "ask" ? [] : (c.actions || []).map(stamp),
      }));
      const roleMode = STATE.roleMode === "ask" ? "ask" : "agent";
      const execMode = roleMode === "ask" ? "confirm" : STATE.execMode;
      if (roleMode === "ask" || !actions.length) {
        STATE.pendingActions = null;
        STATE.pendingChoices = choices.length ? choices : null;
        await appendAssistantTyped(reply, thinking, {
          pendingChoices: choices.length ? choices : null,
        });
        return;
      }
      if (execMode === "confirm") {
        const pending = global.AgentRunner.prepareConfirmation(actions);
        STATE.pendingActions = pending;
        STATE.pendingChoices = null;
        await appendAssistantTyped(reply, thinking, {
          pendingActions: pending,
          planSummary: global.AgentIntent.summarizeActions(pending),
        });
        return;
      }
      // Run ordinary preparation immediately; the runner pauses before generation.
      const exec = await global.AgentIntent.executePendingActions(actions, { signal: ac.signal });
      if (await presentPendingExecution(exec)) return;
      STATE.pendingActions = null;
      STATE.pendingChoices = null;
      await appendAssistantTyped(reply, thinking, {
        execLogs: exec?.logs || [],
      });
    } catch (err) {
      removePending();
      clearActiveRun(boardId);
      if (ac.signal.aborted || global.AgentApi?.isAbortError?.(err, ac.signal) || err?.name === "AbortError") {
        await appendAssistantTyped("已停止。", "");
        return;
      }
      const raw = String(err?.message || err || "").trim() || "智能体任务失败";
      await appendAssistantTyped(raw, "");
    } finally {
      STATE.sending = false;
      STATE.abortController = null;
      STATE.activeJobId = "";
      setSendBusy(false);
    }
  }

  function open() {
    const root = ensureDom();
    if (typeof global.closeExclusiveChromePopovers === "function") {
      global.closeExclusiveChromePopovers("agent");
    } else {
      global.NodeListPanel?.close?.();
      global.CanvasAuth?.closeAllAccountPops?.();
    }
    syncBoardUi(currentBoardId(), { keepStick: true });
    STATE.open = true;
    document.body.classList.add("is-agent-overlay-open");
    root.setAttribute("aria-hidden", "false");
    const fab = $("#btn-agent-fab");
    if (fab) fab.setAttribute("aria-pressed", "true");
    if (typeof global.revealOverlay === "function") {
      global.revealOverlay(root);
    } else {
      root.classList.remove("hidden");
      requestAnimationFrame(() => root.classList.add("is-overlay-open"));
    }
    void refreshModelSelect();
    // Reattach in-flight UI, or resume a job that survived refresh.
    restoreBusyUi();
    if (!STATE.sending && getPersistedRun(STATE.boardId)?.jobId) {
      void resumePendingJob();
    }
    const delay = global.OverlayAnim?.MS || 280;
    setTimeout(() => {
      scrollToBottom({ force: true });
      // Do not autofocus the compose box — canvas WASD/EQ stay primary until
      // the user deliberately clicks into the input to chat.
    }, Math.min(delay, 180));
  }

  function close() {
    document.body.classList.remove("is-agent-overlay-open");
    snapshotCurrentBoard();
    const root = $("#agent-panel");
    if (!root) {
      STATE.open = false;
      syncFabBusy();
      return;
    }
    closeComposeExpand({ sync: true });
    hideMentionMenu();
    STATE.open = false;
    root.setAttribute("aria-hidden", "true");
    const fab = $("#btn-agent-fab");
    if (fab) fab.setAttribute("aria-pressed", "false");
    if (typeof global.dismissOverlay === "function") {
      global.dismissOverlay(root);
    } else {
      root.classList.remove("is-overlay-open");
      setTimeout(() => root.classList.add("hidden"), global.OverlayAnim?.MS || 260);
    }
    // Keep request alive; FAB shows thinking while closed.
    syncFabBusy();
  }

  function toggle() {
    if (STATE.open || $("#agent-panel")?.classList.contains("is-overlay-open")) close();
    else open();
  }

  function isOpen() {
    return STATE.open || !!$("#agent-panel")?.classList.contains("is-overlay-open");
  }

  global.AgentPanel = {
    open,
    close,
    toggle,
    isOpen,
    refreshModelSelect,
    ensureDom,
    onBoardChange,
    buildCompactModelContext,
    retainSourceHistory,
    /** 供 hydrate 取聊天里的完整分镜原文（不截断） */
    getUserScriptSources() {
      return (STATE.history || [])
        .filter((h) => h && h.role === "user" && String(h.content || "").trim())
        .map((h, i) => ({ id: `chat-user-${i}`, body: String(h.content || "") }))
        .filter((s) => looksLikeScriptContent(s.body) || s.body.length >= 400);
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
