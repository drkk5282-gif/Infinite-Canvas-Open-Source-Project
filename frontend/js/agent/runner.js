/**
 * 执行智能体返回的 canvas actions，并汇总结果。
 * 创建 / 移动 / 排布后平滑镜头跟随（不闪跳）。
 */
(function (global) {
  "use strict";

  function collectFocusIds(action, createdIds) {
    const type = String(action?.type || "");
    const ids = [];
    if (Array.isArray(action?.node_ids)) ids.push(...action.node_ids);
    if (Array.isArray(action?.ids)) ids.push(...action.ids);
    if (action?.node_id) ids.push(action.node_id);
    if (action?.host_id) ids.push(action.host_id);
    if (action?.to) ids.push(action.to);
    if (Array.isArray(action?.items)) {
      for (const it of action.items) {
        if (it?.node_id) ids.push(it.node_id);
        if (it?.id) ids.push(it.id);
      }
    }
    if (
      type === "create_nodes" ||
      type === "create_from_script" ||
      type === "split_script" ||
      type === "layout_nodes" ||
      type === "layout" ||
      type === "layout_selected" ||
      type === "arrange_selected" ||
      type === "move_nodes" ||
      type === "move"
    ) {
      if (!ids.length && createdIds?.length) ids.push(...createdIds);
    }
    return [...new Set(ids.filter(Boolean))];
  }

  async function focusAfterOps(focusIds) {
    const ids = (focusIds || []).filter(Boolean);
    if (!ids.length) return;
    if (typeof global.focusNodesOnCanvasSmooth === "function") {
      try {
        await global.focusNodesOnCanvasSmooth(ids, { durationMs: 580 });
      } catch (err) {
        console.warn("[agent] focus smooth failed", err);
      }
    }
  }

  function collapseWs(s) {
    return String(s || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function looksLikeScriptContent(text) {
    const s = String(text || "");
    if (!s.trim()) return false;
    if (/视频编号|分镜脚本|##\s*视频/.test(s)) return true;
    if ((s.match(/\|/g) || []).length >= 8 && /镜号|对白|画面/.test(s)) return true;
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

  /** 从聊天长文里按场景/节点名抠提示词段落 */
  function extractPromptByNameFromSources(name, sources) {
    const key = String(name || "").trim();
    if (!key || key.length < 2) return "";
    const docs = (sources || [])
      .map((s) => String(s.body || s || ""))
      .filter((b) => b.length >= 200)
      .sort((a, b) => b.length - a.length);
    const esc = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const patterns = [
      new RegExp(
        `(?:#{1,3}\\s*)?(?:场景\\s*\\d+[^\\n]{0,20})?${esc}[^\\n]{0,40}\\n+([\\s\\S]{40,2500}?)(?=\\n#{1,3}\\s|\\n场景\\s*\\d+|\\n人物|\\n第\\s*\\d+\\s*章|$)`,
        "i"
      ),
      new RegExp(
        `${esc}[^\\n]{0,30}(?:场景生成)?提示词[:：]?\\s*\\n+([\\s\\S]{40,2000}?)(?=\\n#{1,3}|\\n负面提示词|\\n场景|$)`,
        "i"
      ),
      new RegExp(
        `(?:场景生成)?提示词[:：]?\\s*\\n+([\\s\\S]{20,1500}?${esc}[\\s\\S]{20,1500}?)(?=\\n负面提示词|\\n#{1,3}|$)`,
        "i"
      ),
    ];
    for (const doc of docs) {
      if (!doc.includes(key)) continue;
      for (const re of patterns) {
        const m = doc.match(re);
        if (m?.[1] && m[1].trim().length >= 40) {
          let body = m[1].trim();
          // 若段落后紧跟负面提示词，拼上
          const neg = doc.slice(doc.indexOf(m[1]) + m[1].length).match(
            /^\s*\n+#{0,3}\s*负面提示词[:：]?\s*\n+([\s\S]{10,800}?)(?=\n#{1,3}|\n场景|$)/
          );
          if (neg?.[1]) {
            body = `${body}\n\n负面提示词：${neg[1].trim()}`;
          }
          return body;
        }
      }
    }
    return "";
  }

  function nodeBody(node) {
    if (!node) return "";
    if (typeof global.getTextNodeBody === "function") {
      const b = global.getTextNodeBody(node);
      if (b) return String(b);
    }
    return String(node.resultText || node.prompt || "");
  }

  /** 收集可作「原文」的节点正文（选中优先）+ 聊天里的完整分镜。 */
  function collectSourceBodies(extraIds) {
    const Store = global.CanvasStore;
    const board = Store?.activeBoard?.();
    const out = [];
    if (board) {
      const ids = new Set([
        ...(Array.isArray(Store.selectedIds) ? Store.selectedIds : []),
        ...(Array.isArray(extraIds) ? extraIds : []),
      ]);
      for (const id of ids) {
        const n = board.nodes.find((x) => x.id === id);
        const body = nodeBody(n);
        if (body && (body.includes("\n") || body.length >= 80)) out.push({ id, body });
      }
      if (!out.length) {
        for (const n of board.nodes || []) {
          if (n.type !== "text" && n.type !== "text_gen") continue;
          const body = nodeBody(n);
          if (body && body.includes("\n")) out.push({ id: n.id, body });
          if (out.length >= 8) break;
        }
      }
    }
    // 聊天历史里的完整用户脚本（解决模型省略 / 历史被截断后写残稿）
    try {
      const chatSrc =
        typeof global.AgentPanel?.getUserScriptSources === "function"
          ? global.AgentPanel.getUserScriptSources()
          : [];
      for (const s of chatSrc || []) {
        if (s?.body) out.push({ id: s.id || `chat-${out.length}`, body: String(s.body) });
      }
    } catch {
      /* ignore */
    }
    return out;
  }

  function scriptHasOmission(text) {
    const s = String(text || "");
    if (!s) return true;
    // 对白/表格被 … 截断，或行尾残缺
    if (/…|\.\.\.|……/.test(s)) return true;
    if (/\|[^|\n]{0,40}$/.test(s.trim()) && /\|/.test(s) && !/\|\s*$/.test(s.trim())) {
      /* table may end without trailing | — weak signal */
    }
    // markdown 表行明显半截（最后一行列数远少于表头）
    const lines = s.split("\n").map((l) => l.trim()).filter(Boolean);
    const tableLines = lines.filter((l) => l.startsWith("|") && l.includes("|"));
    if (tableLines.length >= 2) {
      const cols = (line) => (line.match(/\|/g) || []).length;
      const headerCols = cols(tableLines[0]);
      const lastCols = cols(tableLines[tableLines.length - 1]);
      if (headerCols >= 4 && lastCols > 0 && lastCols < headerCols - 1) return true;
    }
    return false;
  }

  /** 用更长的聊天/节点原文替换模型省略稿 */
  function preferFullScript(text, sources) {
    let best = String(text ?? "");
    const restored = restoreTextFromSources(best, sources);
    if (restored && restored.length >= best.length) best = restored;

    const candidates = (sources || [])
      .map((s) => String(s.body || s || ""))
      .filter((b) => b && looksLikeScriptContent(b))
      .sort((a, b) => b.length - a.length);

    if (!candidates.length) return best;

    const omit = scriptHasOmission(best);
    const head = collapseWs(best).slice(0, 48);
    for (const cand of candidates) {
      if (cand.length <= best.length && !omit) continue;
      const ch = collapseWs(cand);
      const bh = collapseWs(best);
      // 同源：开头相近，或短稿是长稿子串，或同含多个视频编号
      const related =
        (head && ch.includes(head) && head.length >= 20) ||
        (bh.length >= 40 && ch.includes(bh)) ||
        (bh.length >= 40 && bh.includes(ch.slice(0, 80))) ||
        (/视频编号/.test(best) && /视频编号/.test(cand) && omit && cand.length > best.length * 1.2);
      if (related && cand.length > best.length) {
        best = cand;
        break;
      }
    }

    // 模型几乎没给正文时：直接用最长分镜 / 最长聊天素材
    if ((!best || best.length < 80 || omit) && candidates[0] && candidates[0].length > best.length) {
      if (!best || omit || /视频编号/.test(candidates[0])) best = candidates[0];
    }
    // 仍空：不挑分镜特征，取聊天/节点里最长正文（≥400），避免 script:"" 空跑
    if (!best || best.length < 40) {
      const anyLong = (sources || [])
        .map((s) => String(s.body || s || ""))
        .filter((b) => b.length >= 400)
        .sort((a, b) => b.length - a.length);
      if (anyLong[0]) best = anyLong[0];
    }
    return best;
  }

  function restoreTextFromSources(text, sources) {
    const raw = String(text ?? "");
    if (!raw || !sources?.length) return raw;
    // 已有换行的也可能是残稿，仍尝试用更长原文替换
    const collapsed = collapseWs(raw);
    if (!collapsed) return raw;
    for (const src of sources) {
      const body = src.body || src;
      if (collapseWs(body) === collapsed) return body;
    }
    for (const src of sources) {
      const body = src.body || src;
      if (collapseWs(body).includes(collapsed) && collapsed.length >= 40) return body;
    }
    return raw;
  }

  function mapNodeType(raw, fallback = "") {
    const s = String(raw || "").trim();
    if (!s) return fallback;
    if (/参考图|场景|人物|角色|图片|立绘/.test(s)) return "image";
    if (/视频编号|分镜/.test(s)) return "video";
    const low = s.toLowerCase().replace(/[\s\-－]+/g, "_");
    const aliases = {
      image: "image",
      img: "image",
      pic: "image",
      picture: "image",
      ref_image: "image",
      refimage: "image",
      reference: "image",
      reference_image: "image",
      ref: "image",
      video: "video",
      vid: "video",
      text: "text",
      txt: "text",
      voice: "text",
      voice_text: "text",
      voicetext: "text",
    };
    return aliases[low] || fallback;
  }

  function normalizeCreateNodeItem(it) {
    if (typeof it === "string" && it.trim()) return { name: "", prompt: it.trim() };
    if (!it || typeof it !== "object") return null;
    const name = String(it.name || it.title || it.label || it.customName || "").trim();
    const prompt = String(
      it.prompt || it.content || it.body || it.text || it.resultText || it.result_text || ""
    ).trim();
    if (!name && !prompt) return null;
    const item = { name, prompt };
    const nt = mapNodeType(it.node_type || it.nodeType || it.type);
    if (nt) item.node_type = nt;
    for (const key of ["channel", "model", "promptPrefixId", "from_node_id", "source_id"]) {
      if (it[key] != null) item[key] = it[key];
    }
    if (it.resultText != null || it.result_text != null) {
      item.resultText = String(it.resultText || it.result_text || prompt);
    }
    return item;
  }

  function normalizeCreateNodesAction(action) {
    if (!action || typeof action !== "object") return action;
    const next = { ...action };
    let rawItems = next.items;
    if (!Array.isArray(rawItems) || !rawItems.length) {
      rawItems = next.nodes || next.list || next.data || [];
    }
    if (!Array.isArray(rawItems)) rawItems = [];
    if (!rawItems.length) {
      const singlePrompt = String(
        next.prompt || next.content || next.body || next.text || ""
      ).trim();
      const singleName = String(next.name || next.title || "").trim();
      if (
        singleName &&
        !["create_nodes", "create_from_script", "split_script"].includes(singleName)
      ) {
        rawItems = [{ name: singleName, prompt: singlePrompt, node_type: next.node_type || next.nodeType }];
      } else if (singlePrompt) {
        rawItems = [{ name: "", prompt: singlePrompt, node_type: next.node_type || next.nodeType }];
      }
    }
    const items = rawItems.map(normalizeCreateNodeItem).filter(Boolean);
    next.items = items;
    let nodeType = mapNodeType(next.node_type || next.nodeType);
    if (!nodeType) {
      for (const it of items) {
        nodeType = mapNodeType(it.node_type);
        if (nodeType) break;
        if (/视频编号/.test(String(it.name || ""))) {
          nodeType = "video";
          break;
        }
      }
    }
    next.node_type = nodeType || "image";
    delete next.nodes;
    delete next.list;
    delete next.data;
    return next;
  }

  function hydrateActionTexts(actions) {
    const list = Array.isArray(actions) ? actions : [];
    const extraIds = [];
    for (const a of list) {
      if (a?.from_node_id) extraIds.push(a.from_node_id);
      if (a?.source_id) extraIds.push(a.source_id);
      if (a?.copy_from) extraIds.push(a.copy_from);
      if (a?.node_id) extraIds.push(a.node_id);
      if (Array.isArray(a?.items)) {
        for (const it of a.items) {
          if (it?.from_node_id) extraIds.push(it.from_node_id);
          if (it?.source_id) extraIds.push(it.source_id);
        }
      }
      if (Array.isArray(a?.nodes)) {
        for (const it of a.nodes) {
          if (it?.from_node_id) extraIds.push(it.from_node_id);
          if (it?.source_id) extraIds.push(it.source_id);
        }
      }
    }
    const sources = collectSourceBodies(extraIds);
    return list.map((action) => {
      if (!action || typeof action !== "object") return action;
      const type = String(action.type || "");
      const next = { ...action };
      if (type === "create_from_script" || type === "split_script") {
        if (next.script) next.script = preferFullScript(next.script, sources);
        else next.script = preferFullScript("", sources);
        if (next.text) next.text = preferFullScript(next.text, sources);
      }
      if (type === "create_nodes") {
        Object.assign(next, normalizeCreateNodesAction(next));
        if (Array.isArray(next.items)) {
          next.items = next.items.map((it) => {
            const item = { ...it };
            const fromId = item.from_node_id || item.source_id || next.from_node_id || next.source_id;
            if (fromId) {
              const hit = sources.find((s) => s.id === fromId);
              if (hit?.body) {
                item.prompt = hit.body;
                if (item.resultText != null) item.resultText = hit.body;
                return item;
              }
            }
            const fullScript = preferFullScript("", sources);
            const name = String(item.name || item.title || "");
            const vid =
              name.match(/视频编号\s*0*(\d+)/) ||
              String(item.prompt || "").match(/#{0,3}\s*视频编号\s*0*(\d+)/);
            if (vid && fullScript && typeof global.AgentParser?.parseVideoSegments === "function") {
              const segs = global.AgentParser.parseVideoSegments(fullScript) || [];
              const hitSeg = segs.find((s) => Number(s.index) === Number(vid[1]));
              if (hitSeg?.body) {
                item.prompt = hitSeg.body;
                if (item.resultText != null) item.resultText = hitSeg.body;
                return item;
              }
            }
            // Explicit content is intentional. A short prompt must never be
            // replaced with the longest source document or an older version.
            const promptNow = String(item.prompt || item.body || "").trim();
            if (name && !promptNow) {
              const fromDoc = extractPromptByNameFromSources(name, sources);
              if (fromDoc) item.prompt = fromDoc;
            }
            return item;
          });
        }
      }
      if (type === "update_node") {
        const patch = { ...(next.patch || {}) };
        if (next.prompt != null && patch.prompt == null) patch.prompt = next.prompt;
        if (next.content != null && patch.prompt == null) patch.prompt = next.content;
        if (next.body != null && patch.prompt == null) patch.prompt = next.body;
        if (next.resultText != null && patch.resultText == null) patch.resultText = next.resultText;
        if (next.customName != null && patch.customName == null) patch.customName = next.customName;
        const fillFromDoc =
          next.fill_from_document === true || patch.fill_from_document === true;
        const id = next.node_id || next.id;
        const board = global.CanvasStore?.activeBoard?.();
        const node = board?.nodes?.find((n) => n.id === id);
        const nameHint =
          next.source_name ||
          patch.source_name ||
          next.fill_name ||
          patch.fill_name ||
          patch.customName ||
          next.customName ||
          node?.customName ||
          node?.name ||
          "";
        if (fillFromDoc) {
          next.fill_from_document = true;
          if (nameHint) {
            const fromDoc = extractPromptByNameFromSources(nameHint, sources);
            if (fromDoc) {
              patch.prompt = fromDoc;
            }
          }
          // Avoid wiping an existing prompt if document lookup failed.
          if (!String(patch.prompt || "").trim()) {
            delete patch.prompt;
          }
          delete patch.fill_from_document;
          delete patch.source_name;
          delete patch.fill_name;
        }
        next.patch = patch;
      }
      return next;
    });
  }

  function isGenerationAction(action) {
    return ["generate_nodes", "generate", "edit_image"].includes(String(action?.type || "").trim().toLowerCase());
  }

  function generationSnapshot(board, id) {
    const node = board?.nodes?.find((n) => n.id === id);
    if (!node) return null;
    const fields = ["type", "prompt", "resultText", "customName", "imageUrl", "videoUrl", "audioUrl",
      "channel", "model", "ratio", "resolution", "duration", "style", "videoMode", "audioMode", "lyrics", "promptPrefixId", "softRefIds"];
    const inputs = (board.connections || []).filter((c) => c.to === id).map((c) => c.from);
    // Include inline @ references as well as wires and the reference bar.
    const collected = global.collectReferences?.(id, node.prompt || "", { includeBarRefs: true });
    const mentioned = [...(collected?.all || []), ...(collected?.texts || [])].map((r) => r.nodeId);
    const refs = [...new Set([...inputs, ...(node.softRefIds || []), ...mentioned].filter(Boolean))].map((refId) => {
      const n = board.nodes.find((r) => r.id === refId);
      return [refId, n?.prompt, n?.resultText, n?.imageUrl, n?.videoUrl, n?.audioUrl];
    });
    const prefix = ["image", "video"].includes(node.type)
      ? global.PromptPrefixStore?.applyPrefix?.(node.promptPrefixId, node.type, "") : null;
    return JSON.stringify([fields.map((k) => node[k] ?? null), refs, prefix ?? null]);
  }

  function prepareConfirmation(actions, board = global.CanvasStore?.activeBoard?.(), createdIds = []) {
    const list = (actions || []).map((a) => ({ ...a, _board_id: board?.id }));
    if (!list.length) return list;
    const snapshots = {};
    for (const action of list) {
      if (!isGenerationAction(action)) continue;
      const ids = action.target === "created" ? createdIds :
        action.node_id || action.id ? [action.node_id || action.id] : action.node_ids || action.ids || [];
      for (const id of ids) {
        const snapshot = generationSnapshot(board, id);
        // New nodes are created by this approved plan. Existing targets in
        // every generation step must still match what the user reviewed.
        if (snapshot != null) snapshots[id] = snapshot;
      }
    }
    if (Object.keys(snapshots).length) list[0]._generation_snapshot = snapshots;
    return list;
  }

  async function runActions(actions, { signal, generationConfirmed = false } = {}) {
    const checkAborted = () => {
      if (signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    };
    checkAborted();
    const list = hydrateActionTexts(Array.isArray(actions) ? actions : []);
    const logs = [];
    const createdIds = [];
    // A confirmation resumes exactly where this plan paused, including aliases
    // for nodes already created. No preceding edits are replayed.
    const allCreatedIds = (list[0]?._resume_created_ids || []).slice();
    const focusIds = [];
    let pendingActions = null;
    const startBoard = global.CanvasStore?.activeBoard?.();
    for (const action of list) {
      if (action?._board_id && action._board_id !== startBoard?.id) {
        return { logs: ["画板已切换，未执行旧画板的计划，请返回原画板后重试。"], createdIds, focusIds };
      }
      if (action?._generation_snapshot && Object.entries(action._generation_snapshot)
          .some(([id, snapshot]) => generationSnapshot(startBoard, id) !== snapshot)) {
        return { logs: ["生成目标或引用在确认前发生了变化，未执行，请重新核对生成要求。"], createdIds, focusIds };
      }
      if (action?._expected) {
        const node = startBoard?.nodes?.find((n) => n.id === (action.node_id || action.id));
        if (!node || Object.entries(action._expected).some(([key, value]) => (node[key] ?? null) !== value)) {
          return { logs: ["目标节点在计划后发生了变化，未执行操作，请重新发送修改要求。"], createdIds, focusIds };
        }
      }
    }

    function resolveId(raw) {
      const s = String(raw || "").trim();
      if (!s) return s;
      if (
        s === "$last_created" ||
        s === "__last__" ||
        s === "$last" ||
        s === "{{last_created}}"
      ) {
        return allCreatedIds[allCreatedIds.length - 1] || s;
      }
      return s;
    }

    for (const [actionIndex, rawAction] of list.entries()) {
      checkAborted();
      if (!rawAction || typeof rawAction !== "object") continue;
      const action = { ...rawAction };
      for (const key of [
        "host_id",
        "host",
        "ref_id",
        "ref",
        "from",
        "to",
        "node_id",
        "id",
        "from_id",
        "to_id",
        "source",
        "target",
      ]) {
        if (action[key] != null) action[key] = resolveId(action[key]);
      }
      if (Array.isArray(action.node_ids)) action.node_ids = action.node_ids.map(resolveId);
      if (Array.isArray(action.ids)) action.ids = action.ids.map(resolveId);
      const type = String(action.type || "").trim().toLowerCase();
      try {
        if (global.CanvasStore?.activeBoard?.()?.id !== startBoard?.id) throw new Error("画板已切换，已停止旧计划");
        if (isGenerationAction(action) && !generationConfirmed) {
          const ids = type === "edit_image" ? [action.node_id || action.id]
            : action.target === "created" ? allCreatedIds
            : action.node_id || action.id ? [action.node_id || action.id] : action.node_ids || action.ids || [];
          if (!ids.length) throw new Error("没有明确的生成目标，未触发生成");
          if (ids.some((id) => !startBoard?.nodes?.some((n) => n.id === id))) throw new Error("生成目标不存在，请重新指定");
          pendingActions = list.slice(actionIndex).map((a) => {
            const next = { ...a, _board_id: startBoard?.id };
            const live = startBoard?.nodes?.find((n) => n.id === resolveId(a.node_id || a.id));
            if (a._expected && live) next._expected = Object.fromEntries(Object.keys(a._expected).map((k) => [k, live[k] ?? null]));
            return next;
          });
          pendingActions[0] = { ...pendingActions[0], _resume_created_ids: allCreatedIds.slice() };
          // The first generation's aliases are now real IDs, making the
          // confirmation summary show the actual targets.
          if (type !== "edit_image") {
            pendingActions[0].node_ids = ids.slice();
            delete pendingActions[0].target;
          } else {
            pendingActions[0].id = ids[0];
            pendingActions[0].node_id = ids[0];
          }
          pendingActions = prepareConfirmation(pendingActions, startBoard, allCreatedIds);
          break;
        }
        if (type === "create_nodes") {
          const normalized = normalizeCreateNodesAction(action);
          let items = normalized.items;
          if ((!items || !items.length) && (normalized.script || action.script)) {
            const segs =
              global.AgentParser?.parseVideoSegments?.(normalized.script || action.script) || [];
            items = segs.map((s) => ({ name: s.title, prompt: s.body }));
          }
          if (!items?.length) {
            throw new Error(
              "create_nodes 缺少 items（需要 name/prompt）。请用 {type,node_type,items:[{name,prompt}]}，不要用 nodes/content"
            );
          }
          const nodeType = mapNodeType(
            normalized.node_type || normalized.nodeType,
            "image"
          );
          const layout = normalized.layout || normalized.mode || action.layout || action.mode || "col";
          const result = global.AgentOps.createNodesFromItems(items, nodeType, { layout });
          createdIds.push(...(result.ids || []));
          allCreatedIds.push(...(result.ids || []));
          focusIds.push(...(result.ids || []));
          logs.push(`已创建 ${result.count} 个 ${result.nodeType} 节点`);
          continue;
        }
        if (type === "split_script" || type === "create_from_script") {
          const text = action.text || action.script || "";
          if (!String(text || "").trim()) {
            throw new Error("脚本内容为空（会话里没找到可回填的分镜原文，请先粘贴完整分镜再创建）");
          }
          const nodeType = String(action.node_type || action.nodeType || "video").toLowerCase();
          const layout = action.layout || action.mode || "col";
          const result = global.AgentOps.createNodesFromScript(text, { nodeType, layout });
          createdIds.push(...(result.ids || []));
          allCreatedIds.push(...(result.ids || []));
          focusIds.push(...(result.ids || []));
          logs.push(`已按脚本创建 ${result.count} 个 ${result.nodeType} 节点`);
          continue;
        }
        if (type === "update_node") {
          const id = resolveId(action.node_id || action.id);
          if (!id) throw new Error("update_node 缺少节点 id");
          const patch = action.patch || action;
          if (
            action.fill_from_document === true &&
            !String(patch.prompt || "").trim() &&
            patch.resultText == null &&
            patch.customName == null
          ) {
            const hint = action.source_name || action.fill_name || id;
            throw new Error(
              `未能从文档回填「${hint}」的提示词。请确认会话里仍有完整场景文档，且 source_name 与文档标题一致（如 场景02）。`
            );
          }
          global.AgentOps.updateNode(id, patch);
          focusIds.push(id);
          logs.push(`已更新节点 ${id}`);
          continue;
        }
        if (type === "replace_node_text") {
          const id = action.node_id || action.id;
          const result = global.AgentOps.replaceNodeText(id, action);
          focusIds.push(id);
          logs.push(`已局部修改 ${result.count} 处文字`);
          continue;
        }
        if (type === "edit_image") {
          const id = action.node_id || action.id;
          await global.AgentOps.editImage(id, action, { signal });
          focusIds.push(id);
          logs.push("已提交原图修改任务，完成后会更新对应图片节点");
          continue;
        }
        if (
          type === "set_node_channel" ||
          type === "set_model" ||
          type === "set_nodes_channel" ||
          type === "set_nodes_model"
        ) {
          const bulk = global.AgentOps.setNodesChannelOrModel({
            nodeIds: action.node_ids || action.ids || (action.node_id || action.id ? [action.node_id || action.id] : null),
            nodeType: action.node_type || action.nodeType || action.media_type || null,
            channel: type === "set_model" || type === "set_nodes_model" ? "" : action.channel,
            model: action.model,
            scope: action.scope || (action.all ? "all" : null),
          });
          focusIds.push(...(bulk.ids || []));
          if (bulk.logs?.length) logs.push(...bulk.logs.slice(0, 12));
          else logs.push(`已更新 ${bulk.count || 0} 个节点渠道/模型`);
          if (bulk.failed) logs.push(`另有 ${bulk.failed} 个失败`);
          continue;
        }
        if (
          type === "set_node_prefix" ||
          type === "set_prefix" ||
          type === "switch_prefix"
        ) {
          const id = action.node_id || action.id;
          const spec =
            action.prefix_id ??
            action.prefixId ??
            action.prefix ??
            action.name ??
            action.label ??
            "";
          const result = global.AgentOps.setNodePrefix(id, spec);
          focusIds.push(id);
          logs.push(`已切换前缀 → ${result.label}（${id}）`);
          continue;
        }
        if (type === "select_nodes" || type === "select") {
          const ids = action.node_ids || action.ids || [];
          const ok = global.AgentOps.selectNodes(ids);
          focusIds.push(...ids);
          logs.push(ok ? `已选中 ${ids.length} 个节点` : "选中失败（无有效 id）");
          continue;
        }
        if (
          type === "layout_selected" ||
          type === "arrange_selected" ||
          type === "layout_nodes" ||
          type === "layout" ||
          type === "layout_all" ||
          type === "tidy_layout"
        ) {
          const mode = action.mode || action.layout || "col";
          const scopeRaw = String(action.scope || "").toLowerCase();
          const scope =
            type === "layout_all" || action.all === true || scopeRaw === "all"
              ? "all"
              : scopeRaw === "selected"
                ? "selected"
                : "";
          const ids = action.node_ids || action.ids || null;
          const result = global.AgentOps.arrangeNodes(mode, ids, {
            scope: scope || undefined,
            smart: action.smart !== false,
            // 未点名「仅选中」时，漏传 id 就整理整张画布，避免空跑
            allowBoardFallback: scope !== "selected" && type !== "layout_selected",
          });
          const used =
            result.ok && result.count
              ? ids?.length && scope !== "all"
                ? ids
                : (global.CanvasStore?.activeBoard?.()?.nodes || [])
                    .map((n) => n.id)
                    .slice(0, result.count)
              : [];
          focusIds.push(...used);
          logs.push(
            result.ok
              ? `已整理 ${result.count} 个节点（${result.mode}）`
              : "没有足够的节点可整理（至少需要 2 个）"
          );
          continue;
        }
        if (type === "move_nodes" || type === "move") {
          const result = global.AgentOps.moveNodes(action);
          focusIds.push(...collectFocusIds(action, createdIds));
          logs.push(result.count ? `已移动 ${result.count} 个节点` : "未移动任何节点");
          continue;
        }
        if (type === "connect_nodes" || type === "connect") {
          const from =
            action.from ||
            action.from_id ||
            action.source ||
            action.ref_id ||
            action.ref ||
            action.source_id;
          const to =
            action.to ||
            action.to_id ||
            action.target ||
            action.host_id ||
            action.host ||
            action.target_id;
          if (!from || !to) {
            throw new Error("连线缺少 from/to（或 source/target、ref/host）节点 id");
          }
          const result = global.AgentOps.connectNodes(from, to);
          focusIds.push(to, from);
          logs.push(result.ok ? `已连接 ${from} → ${to}` : `连线未变化 ${from} → ${to}`);
          continue;
        }
        if (
          type === "disconnect_nodes" ||
          type === "disconnect" ||
          type === "remove_connection" ||
          type === "delete_connection"
        ) {
          const result = global.AgentOps.disconnectNodes(action);
          if (action.from || action.to) {
            focusIds.push(action.from || action.from_id, action.to || action.to_id);
          }
          logs.push(
            result.count
              ? result.scope === "between_images" ||
                result.scope === "image_to_image" ||
                result.scope === "images"
                ? `已删除 ${result.count} 条图↔图连线`
                : `已断开 ${result.count} 条连线`
              : "未找到可删除的连线"
          );
          continue;
        }
        if (type === "mention_ref" || type === "mention" || type === "soft_ref" || type === "wire_mention") {
          const host =
            action.host_id ||
            action.host ||
            action.to ||
            action.to_id ||
            action.target ||
            action.target_id;
          const ref =
            action.ref_id ||
            action.ref ||
            action.from ||
            action.from_id ||
            action.source ||
            action.source_id;
          if (!host || !ref) {
            throw new Error("@ 引用缺少 host/ref（视频节点与素材节点 id）");
          }
          const softOnly =
            type === "soft_ref" ||
            action.soft_only === true ||
            action.softOnly === true ||
            action.into_prompt === false ||
            action.intoPrompt === false;
          const result = global.AgentOps.mentionRef(host, ref, {
            into_prompt: !softOnly,
            soft_only: softOnly,
            connect: action.connect !== false && type !== "soft_ref",
            replace_name: action.replace_name || action.replaceName || action.name || "",
            voice_id: action.voice_id || action.voiceId || "",
            append: action.append === true || action.force_append === true,
          });
          focusIds.push(host);
          if (result.intoPrompt && result.mode === "name" && result.replaced) {
            logs.push(`已在 ${host} 提示词中 @ 引用 ${ref}（替换 ${result.replaced} 处）`);
          } else if (result.intoPrompt && (result.mode === "append" || result.mode === "append_named")) {
            logs.push(`已将 ${ref} @ 追加写入 ${host} 提示词`);
          } else if (result.intoPrompt && result.mode === "exists") {
            logs.push(`已为 ${host} 处理 @ 引用 ${ref}（提示词中已存在）`);
          } else if (result.intoPrompt && result.mode === "no_name_match") {
            logs.push(
              `跳过 ${ref} → ${host}：提示词中无同名，未改文案、未牵线（避免乱加内容）`
            );
          } else if (result.intoPrompt) {
            logs.push(`已为 ${host} 处理 @ 引用 ${ref}`);
          } else {
            logs.push(`已为 ${host} 添加素材引用 ${ref}`);
          }
          continue;
        }
        if (type === "extract_frame" || type === "capture_frame") {
          const id = action.node_id || action.id;
          const mode = action.mode || action.frame || "current";
          await global.AgentOps.extractFrame(id, mode);
          focusIds.push(id);
          logs.push(`已截取帧（${mode}）← ${id}`);
          continue;
        }
        if (type === "media_tool" || type === "run_media") {
          const id = action.node_id || action.id;
          const tool = action.tool || action.name;
          await global.AgentOps.runMediaTool(id, tool);
          focusIds.push(id);
          logs.push(`已执行媒体工具 ${tool} ← ${id}`);
          continue;
        }
        if (type === "delete_nodes" || type === "delete") {
          const ids = action.node_ids || action.ids || [];
          if (action.node_id) ids.push(action.node_id);
          const result = global.AgentOps.deleteNodes(ids);
          logs.push(`已删除 ${result.count} 个节点`);
          continue;
        }
        if (type === "generate_nodes" || type === "generate") {
          let ids = action.node_ids || action.ids || [];
          if (action.target === "created") ids = allCreatedIds.slice();
          else if (action.node_id || action.id) ids = [action.node_id || action.id];
          if (!ids?.length) throw new Error("没有明确的生成目标，未触发生成");
          const result = await global.AgentOps.generateNodes(ids, { signal });
          focusIds.push(...ids);
          logs.push(`已触发生成 ${result.count} 个节点`);
          if (result.failed?.length) {
            for (const failure of result.failed) logs.push(`生成失败：${failure.name || failure.id}，${failure.error}`);
            logs.push("生成未全部提交，已停止后续操作。");
            break;
          }
          continue;
        }
        logs.push(`未知操作：${type}`);
      } catch (err) {
        if (err?.name === "AbortError") throw err;
        logs.push(`${type || "action"} 失败：${err?.message || err}`);
        logs.push("已停止后续操作，请检查失败原因后重试。");
        break;
      }
    }

    const uniqueFocus = [...new Set(focusIds.filter(Boolean))];
    checkAborted();
    if (uniqueFocus.length) {
      // Report the completed operation immediately while the existing smooth
      // camera animation finishes independently.
      void focusAfterOps(uniqueFocus);
    }

    return { logs, createdIds, focusIds: uniqueFocus, pendingActions, requiresConfirmation: !!pendingActions };
  }

  function _kindHintForNode(n, name, bodyOrPrompt) {
    const blob = `${name}\n${bodyOrPrompt || ""}`.toLowerCase();
    let kindHint = "other";
    if (
      n.type === "image" ||
      n.type === "ref-image" ||
      /人物|角色|演员|女主|男主|配角/.test(blob)
    ) {
      if (/场景|地点|环境|室内|室外|街道|客厅|卧室|背景/.test(blob) && !/人物|角色|演员/.test(name)) {
        kindHint = "scene";
      } else if (
        /人物|角色|演员|女主|男主|配角/.test(blob) ||
        (n.type === "image" && name && name.length <= 12)
      ) {
        kindHint = /场景|地点|环境|街道|客厅/.test(name) ? "scene" : "character";
      }
    } else if (/场景|地点|环境/.test(blob)) {
      kindHint = "scene";
    }
    return kindHint;
  }

  function _summarizeNode(n, selected, { withBody }) {
    const soft = Array.isArray(n.softRefIds) ? n.softRefIds.slice(0, 12) : [];
    const hasMedia = !!(n.videoUrl || n.imageUrl || n.audioUrl || n.coverUrl);
    const name = n.customName || global.getNodeDisplayName?.(n, global.CanvasStore?.activeBoard?.()) || "";
    let body = "";
    let prompt = "";
    if (withBody) {
      const bodyRaw =
        n.resultText != null ? String(n.resultText) : typeof global.getTextNodeBody === "function"
          ? global.getTextNodeBody(n)
          : String(n.resultText || "");
      const promptRaw = String(n.prompt || "");
      body = String(bodyRaw || "");
      prompt = String(promptRaw || "");
    }
    const kindHint = _kindHintForNode(n, name, body || prompt || name);
    let prefixLabel = "";
    try {
      const mt = n.type === "video" ? "video" : "image";
      prefixLabel =
        typeof global.PromptPrefixStore?.getEffectiveLabel === "function"
          ? global.PromptPrefixStore.getEffectiveLabel(n.promptPrefixId, mt)
          : "";
    } catch {
      /* ignore */
    }
    const resolvedCh =
      typeof global.resolveNodeChannel === "function"
        ? global.resolveNodeChannel(n)
        : n.channel || "";
    const out = {
      id: n.id,
      type: n.type,
      name,
      kind_hint: kindHint,
      x: Math.round(n.x || 0),
      y: Math.round(n.y || 0),
      has_media: hasMedia,
      prompt_empty: !(String(n.prompt || n.resultText || "").trim()),
      prompt_chars: String(n.prompt || n.resultText || "").trim().length,
      soft_refs: soft,
      ai_marked: !!n.aiMarked,
      prefix: prefixLabel || "无",
      prefix_id: n.promptPrefixId ?? "",
      status: n.status || "",
      ratio: n.ratio || "",
      channel: resolvedCh || n.channel || "",
      model: n.model || "",
      selected: selected.has(String(n.id)),
      image_url: n.imageUrl || "",
    };
    if (withBody) {
      out.prompt = prompt;
      out.body = body || prompt;
      out.result_text = body;
    }
    return out;
  }

  function buildCanvasContext(options = {}) {
    const Store = global.CanvasStore;
    const board = Store?.activeBoard?.();
    if (!board) {
      return {
        board_id: null,
        nodes: [],
        node_index: [],
        selected_ids: [],
        stats: { total: 0, by_type: {}, with_media: 0, connections: 0 },
      };
    }

    const selected = new Set(
      Array.isArray(Store.selectedIds) ? Store.selectedIds.map(String) : []
    );
    const allNodes = Array.isArray(board.nodes) ? board.nodes : [];
    const allConns = Array.isArray(board.connections) ? board.connections : [];

    const byType = {};
    let withMedia = 0;
    for (const n of allNodes) {
      const t = String(n?.type || "unknown");
      byType[t] = (byType[t] || 0) + 1;
      if (n?.videoUrl || n?.imageUrl || n?.audioUrl || n?.coverUrl) withMedia += 1;
    }
    const stats = {
      total: allNodes.length,
      by_type: byType,
      with_media: withMedia,
      connections: allConns.length,
    };

    // Index every node so an existing item beyond position 80 is never recreated.
    const nodeIndex = allNodes.map((n) => _summarizeNode(n, selected, { withBody: false }));
    const selectedNodes = allNodes.filter((n) => selected.has(String(n.id)));
    const otherNodes = allNodes.filter((n) => !selected.has(String(n.id)));
    const query = String(options.query || "");
    const mentioned = new Set(options.mentionedIds || []);
    const targeted = new Set(allNodes.filter((n) => mentioned.has(n.id) ||
      (n.customName && query.includes(n.customName))).map((n) => n.id));
    const related = new Set();
    for (const c of allConns) {
      if (selected.has(c.from) || targeted.has(c.from)) related.add(c.to);
      if (selected.has(c.to) || targeted.has(c.to)) related.add(c.from);
    }
    const visible = new Set(allNodes.filter((n) => global.isNodeInViewport?.(n, 0)).map((n) => n.id));
    const score = (n) => (targeted.has(n.id) ? 100 : 0) + (selected.has(n.id) ? 80 : 0) +
      (related.has(n.id) ? 40 : 0) + (visible.has(n.id) ? 20 : 0);
    const ranked = [...selectedNodes, ...otherNodes].sort((a, b) => score(b) - score(a));
    const broadScope = /所有|全部|全文|整张|整个|各个|每个|批量|哪些|有什么|缺/.test(query);
    const focused = !broadScope && (targeted.size > 0 ||
      (selected.size > 0 && /选中|这个|这张|当前节点/.test(query)));
    const needsSources = /文档|原文|脚本|材料/.test(query);
    // Full index stays available; detail bodies are never cut mid-document.
    let chars = 0;
    const detailed = options.query == null ? ranked : ranked.filter((n) => {
      const size = String(n.prompt || "").length + String(n.resultText || "").length;
      const explicit = selected.has(String(n.id)) || targeted.has(n.id);
      if (focused && !explicit && !related.has(n.id) &&
          !(needsSources && (n.type === "text" || n.type === "text_gen"))) return false;
      if (chars + size > (focused ? 24000 : 100000) && !explicit) return false;
      chars += size;
      return true;
    });
    const detailedIds = new Set(detailed.map((n) => n.id));
    const nodes = detailed.map((n) => ({
      ..._summarizeNode(n, selected, { withBody: true }),
      selected: selected.has(String(n.id)),
      visible: visible.has(n.id),
      mentioned: mentioned.has(n.id),
    }));

    const connections = allConns.map((c) => ({
      from: c.from,
      to: c.to,
    }));

    let prefixes = [];
    try {
      const pm = global.PromptPrefixStore || global.PrefixStore;
      if (pm?.list) {
        const img = pm.list("image").map((p) => ({ id: p.id, name: p.name, media: "image" }));
        const vid = pm.list("video").map((p) => ({ id: p.id, name: p.name, media: "video" }));
        prefixes = [...img, ...vid].slice(0, 40);
      }
    } catch {
      /* ignore */
    }

    const modelsHint = [];
    const modelCatalog = { image: {}, video: {}, text: {} };
    let allowedChannels = [];
    try {
      const probe = { type: "image" };
      if (typeof global.getChannelOptions === "function") {
        allowedChannels = (global.getChannelOptions(probe) || []).map((o) => ({
          id: o.id,
          label: o.label || o.id,
        }));
      }
      const channelIds = allowedChannels.length
        ? allowedChannels.map((c) => c.id)
        : ["myapi"];
      for (const ch of channelIds) {
        const img =
          typeof global.imageModelsForChannel === "function"
            ? global.imageModelsForChannel(ch)
            : [];
        const vid =
          typeof global.videoModelsForChannel === "function"
            ? global.videoModelsForChannel(ch)
            : [];
        const txt =
          typeof global.textModelsForChannel === "function"
            ? global.textModelsForChannel(ch)
            : [];
        modelCatalog.image[ch] = img.slice(0, 12).map((m) => ({
          id: m.id,
          label: m.label || m.id,
        }));
        modelCatalog.video[ch] = vid.slice(0, 12).map((m) => ({
          id: m.id,
          label: m.label || m.id,
        }));
        modelCatalog.text[ch] = txt.slice(0, 8).map((m) => ({
          id: m.id,
          label: m.label || m.id,
        }));
      }
      for (const ch of channelIds) {
        for (const m of modelCatalog.image[ch] || []) {
          modelsHint.push(`image/${ch}:${m.label}`);
        }
        for (const m of modelCatalog.video[ch] || []) {
          modelsHint.push(`video/${ch}:${m.label}`);
        }
        for (const m of modelCatalog.text[ch] || []) {
          modelsHint.push(`text/${ch}:${m.label}`);
        }
      }
    } catch {
      /* ignore */
    }

    const byChannel = {};
    for (const n of allNodes) {
      if (!(n?.type === "image" || n?.type === "video" || n?.type === "text" || n?.type === "voice-text")) {
        continue;
      }
      const ch =
        typeof global.resolveNodeChannel === "function"
          ? global.resolveNodeChannel(n)
          : n.channel || "";
      const key = `${n.type}|${ch || "?"}`;
      byChannel[key] = (byChannel[key] || 0) + 1;
    }
    stats.by_channel_type = byChannel;
    stats.generative = {
      image: allNodes.filter((n) => n?.type === "image").length,
      video: allNodes.filter((n) => n?.type === "video").length,
      text: allNodes.filter((n) => n?.type === "text" || n?.type === "voice-text").length,
    };

    const userRole = String(
      global.__canvasUser?.role || global.AppConfig?.user_role || ""
    ).toLowerCase();
    const canChangeChannel = userRole === "admin" || userRole === "internal";

    return {
      board_id: board.id,
      board_name: board.name || "",
      selected_ids: Array.isArray(Store.selectedIds) ? Store.selectedIds.slice() : [],
      mentioned_ids: [...mentioned],
      visible_ids: [...visible],
      viewport: Store.viewport ? { ...Store.viewport } : null,
      omitted_detail_ids: allNodes.filter((n) => !detailedIds.has(n.id)).map((n) => n.id),
      stats,
      node_index: nodeIndex,
      nodes,
      connections,
      prefixes,
      models_hint: modelsHint.slice(0, 40),
      model_catalog: modelCatalog,
      allowed_channels: allowedChannels,
      user_role: userRole || "user",
      can_change_channel: canChangeChannel,
      permissions: {
        change_channel: canChangeChannel,
        change_model: true,
        note: canChangeChannel
          ? "管理员/内部可切换渠道与模型"
          : "平台用户只能换模型（通常仅 MyApi），不能切换 Seed/BenDi 渠道",
      },
    };
  }

  global.AgentRunner = {
    runActions,
    prepareConfirmation,
    buildCanvasContext,
    focusAfterOps,
    restoreReplyNewlines(text) {
      return restoreTextFromSources(text, collectSourceBodies());
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
