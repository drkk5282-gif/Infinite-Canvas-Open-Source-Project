/**
 * 智能体 API：读取 MyApi agent 配置，调用 /api/agent/chat（结构化计划）
 */
(function (global) {
  "use strict";

  let _cache = [];
  let _cacheAt = 0;

  const LIGHT_BLOCK_RE =
    /创建|新建|生成|出图|提示词|排版|连线|牵线|艾特|删除|更新|修改|改成|分镜|视频编号|提取|列出|检查画布|@\[|补齐|写入|整理|移动节点/;

  function isLightChat(text) {
    const t = String(text || "")
      .split("\n\n---\n引用内容：")[0]
      .trim();
    if (!t || t.length > 80) return false;
    if (LIGHT_BLOCK_RE.test(t)) return false;
    if (
      /^(你好|您好|嗨|哈喽|在吗|早上好|中午好|下午好|晚上好|晚安|谢谢|感谢|拜拜|再见|你好啊|你好呀|hi|hello|hey|thanks|thank you)[\s?？!！。.]*$/i.test(
        t
      )
    ) {
      return true;
    }
    if (
      /^(?:请问|请|你好[，,]?\s*)?(?:你是谁|你叫什么|你是什么模型|你用的是什么模型|什么模型|哪个模型|介绍下自己|介绍一下你自己|who are you|what model)[\s?？!！。.]*$/i.test(
        t
      )
    ) {
      return true;
    }
    if (
      /(你会|你能|你可以).{0,8}(什么|啥|哪些)|^(有什么|什么).{0,4}(功能|本事|能力|技能)|能做(些)?什么|会做(些)?什么|what can you do/i.test(
        t
      )
    ) {
      return true;
    }
    return false;
  }

  function isAbortError(err, signal) {
    if (signal?.aborted) return true;
    if (!err) return false;
    if (err.name === "AbortError" || err.code === 20) return true;
    const msg = String(err.message || err || "");
    return /aborted|AbortError|The user aborted|signal is aborted/i.test(msg);
  }

  async function _api(path, opts) {
    if (typeof global.api === "function") return global.api(path, opts);
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts?.headers || {}) },
      ...opts,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try {
        const j = await res.json();
        msg = j.detail || j.message || msg;
      } catch {
        /* ignore */
      }
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return res.json();
  }

  async function listAgentModels({ force, signal } = {}) {
    const now = Date.now();
    if (!force && _cacheAt && now - _cacheAt < 15000) return _cache;
    const fallback = (value) => (err) => {
      if (isAbortError(err, signal)) throw err;
      return value;
    };
    try {
      const [data, media] = await Promise.all([
        _api("/api/myapi/models?kind=agent", { signal }).catch(fallback({ models: [] })),
        _api("/api/media-models", { signal }).catch(fallback({})),
      ]);
      const mine = Array.isArray(data?.models) ? data.models : [];
      const pubSrc = Array.isArray(media?.agent)
        ? media.agent.filter((m) => m.public)
        : [];
      const volcSrc = Array.isArray(media?.volcengine?.agent)
        ? media.volcengine.agent
        : Array.isArray(media?.agent)
          ? media.agent.filter((m) => m.volcengine)
          : [];
      const pub = pubSrc.map((m) => ({
        id: m.id,
        name: m.label || m.name,
        kind: "agent",
        public: true,
        is_default: false,
      }));
      const volc = volcSrc.map((m) => ({
        id: m.id,
        name: m.label || m.name,
        kind: "agent",
        volcengine: true,
        public: false,
        is_default: false,
      }));
      const byId = new Map();
      for (const m of pub) if (m?.id) byId.set(m.id, m);
      for (const m of volc) if (m?.id) byId.set(m.id, m);
      for (const m of mine) if (m?.id) byId.set(m.id, { ...m, public: !!m.public });
      _cache = [...byId.values()];
      _cacheAt = now;
    } catch (err) {
      if (isAbortError(err, signal)) throw err;
      _cache = [];
      _cacheAt = now;
    }
    return _cache;
  }

  function pickDefaultModel(models) {
    const list = Array.isArray(models) ? models : [];
    return list.find((m) => m.is_default) || list[0] || null;
  }

  function _looksLikePlanJson(text) {
    const s = String(text || "").trim();
    if (!s.startsWith("{") || !s.includes('"actions"')) return false;
    try {
      const data = JSON.parse(s);
      return !!(data && typeof data === "object" && ("thinking" in data || "reply" in data || "actions" in data));
    } catch {
      return false;
    }
  }

  function _sanitizeReply(reply, thinking) {
    let s = String(reply || "").trim();
    s = s
      .replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, "")
      .replace(/<tool_call>\s*\{[\s\S]*?\}\s*/gi, "")
      .replace(/<\/?tool_call>/gi, "")
      .trim();
    if (/^\{\s*\}$/.test(s)) s = "";
    if (_looksLikePlanJson(s)) {
      try {
        const data = JSON.parse(s);
        s = String(data.reply || data.message || "").trim();
      } catch {
        s = "";
      }
    }
    const looksInternal = (text) =>
      /mention_ref|host_id|ref_id|into_prompt|disconnect_nodes|update_node|generate_nodes|create_nodes|输出\s*JSON|执行 mention|\{"type"|我应使用|我最终决定|意图：|关于负面提示词/i.test(
        String(text || "")
      ) && String(text || "").length > 80;

    if (looksInternal(s)) s = "";

    const think = String(thinking || "").trim();
    const thinkPlaceholder =
      !think ||
      think === "（模型未提供思考过程）" ||
      think === "模型未返回结构化 JSON，已作为普通回复展示。" ||
      think === "模型输出格式异常。" ||
      think === "（详见回复）";
    if ((!s || s === "（无回复）") && think && !thinkPlaceholder && !looksInternal(think) && think.length < 160) {
      s = think;
    }
    return s || "（无回复）";
  }

  function _normalizePlanData(data, fallbackModelId) {
    const thinking = String(data?.thinking || "").trim();
    let reply = _sanitizeReply(data?.reply || data?.text || "", thinking);
    let thinkOut = thinking;
    const blankThink =
      !thinkOut ||
      /^(（模型未提供思考过程）|（详见回复）|模型未返回|模型输出格式异常)/.test(thinkOut);
    if (blankThink) thinkOut = "";
    if (
      reply &&
      thinkOut &&
      (reply === thinkOut ||
        (thinkOut.length > 200 && reply.length > 200 && reply.includes(thinkOut.slice(0, 80))))
    ) {
      thinkOut = "";
    }
    return {
      thinking: thinkOut,
      reply,
      actions: Array.isArray(data?.actions) ? data.actions : [],
      choices: Array.isArray(data?.choices) ? data.choices : [],
      decision: data?.decision || "",
      diagnostics: data?.diagnostics || {},
      model_id: data?.model_id || fallbackModelId || "",
      light: !!data?.light,
      job_id: data?.job_id || "",
    };
  }

  function _sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
        return;
      }
      const t = setTimeout(() => {
        signal?.removeEventListener?.("abort", onAbort);
        resolve();
      }, ms);
      const onAbort = () => {
        clearTimeout(t);
        signal?.removeEventListener?.("abort", onAbort);
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      };
      signal?.addEventListener?.("abort", onAbort, { once: true });
    });
  }

  async function cancelJob(jobId) {
    const id = String(jobId || "").trim();
    if (!id) return false;
    try {
      await _api(`/api/agent/chat/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      return true;
    } catch {
      return false;
    }
  }

  async function pollJob(jobId, opts = {}) {
    const id = String(jobId || "").trim();
    if (!id) throw new Error("缺少智能体任务 id");
    const signal = opts.signal;
    let delay = 700;
    while (true) {
      if (isAbortError(null, signal) || signal?.aborted) {
        await cancelJob(id);
        const e = new Error("aborted");
        e.name = "AbortError";
        throw e;
      }
      let data;
      try {
        data = await _api(`/api/agent/chat/${encodeURIComponent(id)}`, { signal });
      } catch (err) {
        if (isAbortError(err, signal)) {
          await cancelJob(id);
          const e = new Error("aborted");
          e.name = "AbortError";
          throw e;
        }
        // Transient query blips: keep waiting a bit.
        await _sleep(Math.min(delay, 2000), signal);
        delay = Math.min(delay + 200, 2500);
        continue;
      }
      if (signal?.aborted) {
        await cancelJob(id);
        throw Object.assign(new Error("aborted"), { name: "AbortError" });
      }
      const status = String(data?.status || "").toLowerCase();
      if (status === "completed") {
        return _normalizePlanData(data, data?.model_id);
      }
      if (status === "failed" || status === "cancelled") {
        const err = new Error(
          String(data?.error || (status === "cancelled" ? "已停止" : "智能体任务失败")).trim() ||
            "智能体任务失败"
        );
        if (status === "cancelled") err.name = "AbortError";
        throw err;
      }
      if (typeof opts.onStatus === "function") {
        try {
          opts.onStatus(data);
        } catch {
          /* ignore */
        }
      }
      await _sleep(delay, signal);
      delay = Math.min(delay + 150, 2000);
    }
  }

  /**
   * @returns {Promise<{ thinking: string, reply: string, actions: any[] }>}
   */
  function compactCanvasForRequest(canvas) {
    if (!canvas) return canvas;
    return {
      ...canvas,
      nodes: (canvas.nodes || []).map((node) => {
        const out = { ...node };
        if (out.body === out.prompt || out.body === out.result_text) delete out.body;
        return out;
      }),
      node_index: (canvas.node_index || []).map(({ image_url, ...node }) => node),
    };
  }

  async function plan(userText, opts = {}) {
    const intentText = String(opts.userIntent || userText || "").trim();
    const light =
      opts.light === true || (opts.light !== false && isLightChat(intentText || userText));

    // The selected ID is validated by the backend; no catalogue round trip is
    // needed on every send. Resolve the catalogue only when choosing a default.
    if (opts.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    const models = opts.modelId ? [{ id: opts.modelId }] : await listAgentModels({ signal: opts.signal });
    const model = opts.modelId
      ? models.find((m) => m.id === opts.modelId)
      : pickDefaultModel(models);
    if (!model) {
      throw new Error("尚未配置智能体模型（点 ⚙ 或底部模型名 → MyApi → 智能体）");
    }

    const canvas = light
      ? null
      : opts.canvas ||
        (typeof global.AgentRunner?.buildCanvasContext === "function"
          ? global.AgentRunner.buildCanvasContext()
          : null);

    let data;
    try {
      data = await _api("/api/agent/chat", {
        method: "POST",
        signal: opts.signal,
        body: JSON.stringify({
          model_id: model.id,
          message: String(userText || ""),
          history: light ? (opts.history || []).slice(-4) : opts.history || [],
          memory: light ? "" : opts.memory || "",
          user_intent: intentText || String(userText || ""),
          role_mode: opts.roleMode || "agent",
          exec_mode: opts.execMode || "confirm",
          light: !!light,
          board_id: opts.boardId || global.CanvasStore?.activeBoardId || "",
          canvas: compactCanvasForRequest(canvas),
        }),
      });
    } catch (err) {
      if (isAbortError(err, opts.signal)) {
        const e = new Error("aborted");
        e.name = "AbortError";
        throw e;
      }
      throw err;
    }

    if (opts.signal?.aborted) {
      if (data?.job_id) await cancelJob(data.job_id);
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }
    // Sync legacy response (in case an older server still returns the plan inline).
    if (!data?.async && !data?.job_id && (data?.reply != null || data?.thinking != null || data?.actions)) {
      return _normalizePlanData(data, model.id);
    }

    const jobId = String(data?.job_id || "").trim();
    if (!jobId) throw new Error("智能体任务未创建（任务服务未返回 job_id）");
    if (typeof opts.onJobId === "function") {
      try {
        opts.onJobId(jobId);
      } catch {
        /* ignore */
      }
    }
    return pollJob(jobId, { signal: opts.signal, onStatus: opts.onStatus });
  }

  /** @deprecated 兼容旧调用：只返回 reply 文本 */
  async function chat(userText, opts = {}) {
    const out = await plan(userText, opts);
    return out.reply;
  }

  global.AgentApi = {
    listAgentModels,
    pickDefaultModel,
    isLightChat,
    isAbortError,
    plan,
    pollJob,
    cancelJob,
    chat,
  };
})(typeof window !== "undefined" ? window : globalThis);
