/**
 * 智能体画布操作：创建 / 更新 / 排布 / 移动 / 连线 / @引用 / 截帧 / 媒体工具 / 触发生成。
 */
(function (global) {
  "use strict";

  function _refreshCanvas() {
    if (typeof global.renderNodes === "function") global.renderNodes({ immediate: true });
    if (typeof global.updateNodeCount === "function") global.updateNodeCount();
    if (typeof global.updateCanvasHint === "function") global.updateCanvasHint();
    if (typeof global.scheduleRenderConnections === "function") {
      global.scheduleRenderConnections(true);
    }
  }

  function _selectIds(ids) {
    if (!ids?.length) return;
    if (ids.length === 1 && typeof global.selectSingle === "function") {
      global.selectSingle(ids[0]);
    } else if (typeof global.setSelection === "function") {
      // 多选不设 primary，避免展开编辑面板
      global.setSelection(ids, null);
    }
  }

  function _activeBoard() {
    const Store = global.CanvasStore;
    if (!Store || typeof Store.addNode !== "function") throw new Error("画布未就绪");
    const board = Store.activeBoard?.();
    if (!board || board.isOwned === false) throw new Error("当前画布不可编辑");
    return board;
  }

  /** 还原模型常把换行写成字面 \\n / <br> 的情况；已有真实换行则不动 */
  function normalizeMultiline(text) {
    let t = String(text ?? "");
    if (!t) return "";
    t = t.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    if (!t.includes("\n")) {
      if (/\\n/.test(t)) t = t.replace(/\\n/g, "\n");
      if (/\\t/.test(t)) t = t.replace(/\\t/g, "\t");
      if (/<br\s*\/?>/i.test(t)) t = t.replace(/<br\s*\/?>/gi, "\n");
    }
    return t;
  }

  function _layoutOpts() {
    const L = global.AgentRules?.layout || {};
    return {
      cellW: L.cellW ?? 320,
      cellH: L.cellH ?? 180,
      gapX: L.gapX ?? 22,
      gapY: L.gapY ?? 16,
      maxCols: L.maxCols ?? 4,
      clusterGap: L.clusterGap ?? 40,
      videoMaxPerCol: L.videoMaxPerCol ?? 15,
      imageMaxPerCol: L.imageMaxPerCol ?? 12,
      textMaxPerCol: L.textMaxPerCol ?? 12,
      familyOrder: Array.isArray(L.familyOrder) ? L.familyOrder.slice() : ["image", "text", "video"],
    };
  }

  function _nodeFamily(type) {
    const t = String(type || "");
    if (t === "video") return "video";
    if (t === "image" || t === "ref-image") return "image";
    // 文本 / 音色描述 / 音频生成 同属中间一列
    return "text";
  }

  function _maxPerCol(family) {
    const L = _layoutOpts();
    if (family === "video") return L.videoMaxPerCol;
    if (family === "image") return L.imageMaxPerCol;
    return L.textMaxPerCol;
  }

  function _nodeBox(n) {
    const { cellW, cellH } = _layoutOpts();
    const w =
      typeof global.getNodeWidth === "function"
        ? global.getNodeWidth(n)
        : Number(n.width) || cellW;
    const h =
      typeof global.getNodeFrameHeight === "function"
        ? global.getNodeFrameHeight(n)
        : Number(n.height) || cellH;
    return {
      x: Number(n.x) || 0,
      y: Number(n.y) || 0,
      w,
      h,
      r: (Number(n.x) || 0) + w,
      b: (Number(n.y) || 0) + h,
    };
  }

  function _clusterBounds(nodes) {
    if (!nodes?.length) return null;
    const boxes = nodes.map(_nodeBox);
    return {
      minX: Math.min(...boxes.map((b) => b.x)),
      minY: Math.min(...boxes.map((b) => b.y)),
      maxX: Math.max(...boxes.map((b) => b.r)),
      maxY: Math.max(...boxes.map((b) => b.b)),
    };
  }

  function _isSceneNode(n) {
    if (!n || (n.type !== "image" && n.type !== "ref-image")) return false;
    const name = String(n.customName || "");
    const blob = `${name}\n${n.prompt || n.resultText || ""}`;
    if (/场景|地点|环境|室内|室外|街道|客厅|卧室|背景|布景/.test(blob)) return true;
    try {
      const label =
        typeof global.PromptPrefixStore?.getEffectiveLabel === "function"
          ? global.PromptPrefixStore.getEffectiveLabel(n.promptPrefixId, "image")
          : "";
      if (/场景/.test(String(label || ""))) return true;
    } catch {
      /* ignore */
    }
    return false;
  }

  /** 默认竖排；过长才多列，视频编号单列尽量 ≤15 */
  function _defaultFamilyLayout(family, count) {
    const n = Math.max(1, count || 1);
    const maxPer = _maxPerCol(family);
    if (n <= maxPer) return "col";
    return "col-stack";
  }

  function _colsForCount(family, count, layout) {
    const n = Math.max(1, count || 1);
    const mode = _normalizeLayoutMode(layout || _defaultFamilyLayout(family, n));
    if (mode === "col") return 1;
    if (mode === "row") return n;
    if (mode === "grid-2") return Math.min(2, n);
    if (mode === "col-stack") return Math.ceil(n / _maxPerCol(family));
    return Math.min(n, Math.max(1, Math.ceil(Math.sqrt(n))), _layoutOpts().maxCols);
  }

  function _anchorWorld() {
    const pad = global.AgentRules?.layout?.viewportPad ?? 72;
    let pos = null;
    if (typeof global.getPasteWorldPos === "function") {
      try {
        pos = global.getPasteWorldPos();
      } catch {
        pos = null;
      }
    }
    if (pos && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
      return { x: pos.x + pad, y: pos.y };
    }
    return { x: 0, y: 0 };
  }

  /**
   * 新节点落点：往外接，不挤进已有簇缝隙。
   * - 有同类：优先接在同类列下方；列满则在该类最右侧再开一列
   * - 无同类：放到整板最右侧外侧（不插进图/文/视频之间）
   * - 若与任意已有节点重叠，继续向下/向右推开
   */
  function _originForFamily(family, count, mode) {
    const board = _activeBoard();
    const { cellW, cellH, gapX, gapY, clusterGap } = _layoutOpts();
    const layout = _normalizeLayoutMode(mode || _defaultFamilyLayout(family, count));
    const cols = _colsForCount(family, count, layout);
    const maxPer = _maxPerCol(family);
    const same = (board.nodes || []).filter((n) => _nodeFamily(n.type) === family);
    const view = _anchorWorld();

    let ox;
    let oy;

    if (same.length) {
      const colBuckets = [];
      for (const n of same) {
        const bx = _nodeBox(n);
        let hit = colBuckets.find((c) => Math.abs(c.x - bx.x) < Math.max(40, gapX * 2));
        if (!hit) {
          hit = { x: bx.x, nodes: [], maxY: bx.b, minY: bx.y };
          colBuckets.push(hit);
        }
        hit.nodes.push(n);
        hit.maxY = Math.max(hit.maxY, bx.b);
        hit.minY = Math.min(hit.minY, bx.y);
      }
      colBuckets.sort((a, b) => a.x - b.x);
      const joinable = colBuckets.find((c) => c.nodes.length + count <= maxPer);
      if (joinable) {
        ox = joinable.x;
        oy = joinable.maxY + gapY;
      } else {
        const last = colBuckets[colBuckets.length - 1];
        const lastW = Math.max(...last.nodes.map((n) => _nodeBox(n).w), cellW);
        ox = last.x + lastW + gapX;
        oy = Math.min(...colBuckets.map((c) => c.minY));
      }
    } else {
      // 无同类：一律放到整板外侧，避免插进已有族缝隙
      const all = _clusterBounds(board.nodes || []);
      if (all) {
        ox = all.maxX + clusterGap;
        oy = all.minY;
      } else {
        ox = view.x - (cols * cellW + Math.max(0, cols - 1) * gapX) / 2;
        oy = view.y - cellH / 2;
      }
    }

    return _nudgeOriginOutside(ox, oy, count, layout, { cols });
  }

  /** 若新块与已有节点重叠，整体向下/向右推到空位（不重排旧节点） */
  function _nudgeOriginOutside(ox, oy, count, layout, extra = {}) {
    const board = _activeBoard();
    const { cellW, cellH, gapX, gapY, clusterGap } = _layoutOpts();
    const nodes = board.nodes || [];
    let x = ox;
    let y = oy;
    const startY = oy;

    const blockHits = (sx, sy) => {
      const slots = layoutSlots(count, { x: sx, y: sy, topLeft: true }, layout);
      const pad = Math.max(8, Math.min(gapX, gapY) / 2);
      for (const slot of slots) {
        const l = slot.x - cellW / 2;
        const t = slot.y - cellH / 2;
        const r = l + cellW;
        const b = t + cellH;
        for (const n of nodes) {
          const nb = _nodeBox(n);
          if (!(r + pad <= nb.x || nb.r + pad <= l || b + pad <= nb.y || nb.b + pad <= t)) {
            return true;
          }
        }
      }
      return false;
    };

    for (let attempt = 0; attempt < 80; attempt++) {
      if (!blockHits(x, y)) {
        return { x, y, cols: extra.cols || 1, layout };
      }
      // 先向下接；连撞几次再整列右移
      if (attempt > 0 && attempt % 12 === 0) {
        x += cellW + gapX;
        y = startY;
      } else {
        y += cellH + gapY;
      }
    }

    const all = _clusterBounds(nodes);
    return {
      x: (all?.maxX ?? x) + clusterGap,
      y: all?.minY ?? y,
      cols: extra.cols || 1,
      layout,
    };
  }

  function _normalizeLayoutMode(mode) {
    const m = String(mode || "col").toLowerCase().trim();
    if (["col", "column", "vertical", "竖", "竖排", "竖着", "默认", "default"].includes(m)) return "col";
    if (["row", "horizontal", "横", "横排", "横着"].includes(m)) return "row";
    if (["grid-2", "2col", "两列"].includes(m)) return "grid-2";
    if (["col-stack", "stack", "多列竖排"].includes(m)) return "col-stack";
    if (["by-links", "links", "按连线"].includes(m)) return "by-links";
    if (["auto", "auto-grid", "grid"].includes(m)) return "col";
    return m || "col";
  }

  function layoutSlots(count, anchor, mode = "col") {
    const n = Math.max(0, count | 0);
    const { cellW, cellH, gapX, gapY } = _layoutOpts();
    const layout = _normalizeLayoutMode(mode);
    let cols = 1;
    if (layout === "row") cols = Math.max(1, n);
    else if (layout === "grid-2") cols = Math.min(2, n);
    else if (layout === "col-stack")
      cols = Math.max(1, Math.ceil(n / (_layoutOpts().videoMaxPerCol || 15)));
    else cols = 1;

    const maxPer =
      layout === "col-stack"
        ? Math.ceil(n / cols)
        : layout === "col"
          ? n
          : Math.ceil(n / Math.max(1, cols));
    const ax = Number(anchor?.x) || 0;
    const ay = Number(anchor?.y) || 0;
    const topLeft = !!anchor?.topLeft;
    const slots = [];
    for (let i = 0; i < n; i++) {
      let col;
      let row;
      if (layout === "col-stack" || layout === "col") {
        col = Math.floor(i / maxPer);
        row = i % maxPer;
        if (layout === "col") {
          col = 0;
          row = i;
        }
      } else {
        col = i % cols;
        row = Math.floor(i / cols);
      }
      if (topLeft) {
        slots.push({
          x: ax + col * (cellW + gapX) + cellW / 2,
          y: ay + row * (cellH + gapY) + cellH / 2,
        });
      } else {
        const totalW = cols * cellW + Math.max(0, cols - 1) * gapX;
        const rows = layout === "col" ? n : Math.ceil(n / cols);
        const totalH = rows * cellH + Math.max(0, rows - 1) * gapY;
        slots.push({
          x: ax - totalW / 2 + col * (cellW + gapX) + cellW / 2,
          y: ay - totalH / 2 + row * (cellH + gapY) + cellH / 2,
        });
      }
    }
    return slots;
  }

  /**
   * 按真实节点宽高紧凑排布（静默，不弹 toast）。
   * col / col-stack：先竖后横；row：横排。
   */
  function packNodesBySize(nodeIds, mode = "col", { keepOrigin = true, originX, originY } = {}) {
    const Store = global.CanvasStore;
    const board = Store?.activeBoard?.();
    if (!board || !nodeIds?.length) return false;
    const layout = _normalizeLayoutMode(mode);
    const { gapX, gapY } = _layoutOpts();
    const nodes = nodeIds.map((id) => board.nodes.find((n) => n.id === id)).filter(Boolean);
    if (nodes.length < 1) return false;

    const boxes = nodes.map((n) => ({
      node: n,
      w:
        typeof global.getNodeWidth === "function"
          ? global.getNodeWidth(n)
          : Number(n.width) || _layoutOpts().cellW,
      h:
        typeof global.getNodeFrameHeight === "function"
          ? global.getNodeFrameHeight(n)
          : Number(n.height) || _layoutOpts().cellH,
    }));
    const ordered = [...boxes].sort((a, b) => {
      const na = String(a.node.customName || "");
      const nb = String(b.node.customName || "");
      const ma = na.match(/视频编号\s*0*(\d+)/);
      const mb = nb.match(/视频编号\s*0*(\d+)/);
      if (ma && mb) return Number(ma[1]) - Number(mb[1]);
      return a.node.y - b.node.y || a.node.x - b.node.x;
    });
    const n = ordered.length;
    const family = _nodeFamily(ordered[0]?.node?.type);
    const maxPer = _maxPerCol(family);
    let cols = 1;
    let perCol = n;
    if (layout === "row") {
      cols = n;
      perCol = 1;
    } else if (layout === "grid-2") {
      cols = Math.min(2, n);
      perCol = Math.ceil(n / cols);
    } else if (layout === "col-stack" || (layout === "col" && n > maxPer)) {
      cols = Math.ceil(n / maxPer);
      perCol = maxPer;
    } else {
      cols = 1;
      perCol = n;
    }

    const colW = [];
    const rowHByCol = [];
    for (let i = 0; i < n; i++) {
      const c = Math.floor(i / perCol);
      const r = i % perCol;
      colW[c] = Math.max(colW[c] || 0, ordered[i].w);
      if (!rowHByCol[c]) rowHByCol[c] = [];
      rowHByCol[c][r] = Math.max(rowHByCol[c][r] || 0, ordered[i].h);
    }
    const colX = [];
    let xAcc = 0;
    for (let c = 0; c < cols; c++) {
      colX[c] = xAcc;
      xAcc += (colW[c] || 0) + gapX;
    }

    const minX =
      originX != null
        ? originX
        : keepOrigin
          ? Math.min(...ordered.map((b) => b.node.x))
          : ordered[0].node.x;
    const minY =
      originY != null
        ? originY
        : keepOrigin
          ? Math.min(...ordered.map((b) => b.node.y))
          : ordered[0].node.y;

    ordered.forEach((b, i) => {
      const c = Math.floor(i / perCol);
      const r = i % perCol;
      let y = minY;
      const rh = rowHByCol[c] || [];
      for (let rr = 0; rr < r; rr++) y += (rh[rr] || 0) + gapY;
      Store.updateNode(b.node.id, {
        x: Math.round(minX + (colX[c] || 0)),
        y: Math.round(y),
      });
    });
    return true;
  }

  /** 同族竖排收齐；横向顺序 图→文本/语音→视频；场景图可顶上横排 */
  function tidyFamilyClusters(preferFamily) {
    const Store = global.CanvasStore;
    const board = Store?.activeBoard?.();
    if (!board?.nodes?.length) return;
    const { clusterGap, gapX, gapY, familyOrder } = _layoutOpts();
    const order = familyOrder.length ? familyOrder : ["image", "text", "video"];

    // 场景图：顶上横排（不混进人物竖列）
    const scenes = board.nodes.filter((n) => _isSceneNode(n));
    const sceneIds = new Set(scenes.map((n) => n.id));
    if (scenes.length) {
      const base = _clusterBounds(scenes) || { minX: 0, minY: 0 };
      packNodesBySize(
        scenes.map((n) => n.id),
        "row",
        { keepOrigin: false, originX: base.minX, originY: base.minY }
      );
    }

    for (const f of order) {
      const nodes = board.nodes.filter(
        (n) => _nodeFamily(n.type) === f && !sceneIds.has(n.id)
      );
      if (nodes.length < 1) continue;
      packNodesBySize(
        nodes.map((n) => n.id),
        _defaultFamilyLayout(f, nodes.length),
        { keepOrigin: true }
      );
    }

    const snap = () =>
      order
        .map((f) => {
          const nodes = board.nodes.filter(
            (n) => _nodeFamily(n.type) === f && !sceneIds.has(n.id)
          );
          return nodes.length ? { family: f, nodes, bounds: _clusterBounds(nodes) } : null;
        })
        .filter(Boolean);

    // 强制左→右：image → text → video，间距紧凑
    let clusters = snap();
    let cursorX = null;
    let topY = null;
    for (const c of clusters) {
      if (!c.bounds) continue;
      if (topY == null) topY = c.bounds.minY;
      else topY = Math.min(topY, c.bounds.minY);
    }
    // 场景在最上时，主列从场景下方开始
    if (scenes.length) {
      const sb = _clusterBounds(scenes);
      if (sb) topY = sb.maxY + gapY;
    }
    for (const c of clusters) {
      if (!c.bounds) continue;
      const targetX = cursorX == null ? c.bounds.minX : cursorX;
      const targetY = topY == null ? c.bounds.minY : topY;
      const dx = targetX - c.bounds.minX;
      const dy = targetY - c.bounds.minY;
      if (dx || dy) {
        for (const n of c.nodes) {
          Store.updateNode(n.id, {
            x: Math.round((n.x || 0) + dx),
            y: Math.round((n.y || 0) + dy),
          });
        }
        c.bounds = _clusterBounds(c.nodes);
      }
      cursorX = (c.bounds?.maxX || targetX) + clusterGap;
    }

    // 再消一次重叠
    clusters = snap();
    for (let i = 1; i < clusters.length; i++) {
      const prev = clusters[i - 1];
      const cur = clusters[i];
      if (!prev.bounds || !cur.bounds) continue;
      if (cur.bounds.minX < prev.bounds.maxX + gapX) {
        const shift = prev.bounds.maxX + clusterGap - cur.bounds.minX;
        if (shift > 0) {
          for (const n of cur.nodes) {
            Store.updateNode(n.id, { x: Math.round((n.x || 0) + shift) });
          }
          cur.bounds = _clusterBounds(cur.nodes);
        }
      }
    }
    void preferFamily;
  }

  function _formatVideoNo(n) {
    return `视频编号${String(Math.max(1, Number(n) || 1)).padStart(2, "0")}`;
  }

  function _extractVideoNo(text) {
    const m = String(text || "").match(/视频编号\s*0*(\d+)/);
    return m ? Number(m[1]) : null;
  }

  function _existingNodeNames(board) {
    const set = new Set();
    for (const n of board?.nodes || []) {
      const name = String(n.customName || "").trim();
      if (name) set.add(name);
    }
    return set;
  }

  function _maxExistingVideoNo(board) {
    let max = 0;
    for (const n of board?.nodes || []) {
      const v = _extractVideoNo(n.customName);
      if (v != null) max = Math.max(max, v);
    }
    return max;
  }

  function _rewriteVideoHeading(body, videoNo) {
    const label = _formatVideoNo(videoNo);
    const raw = String(body || "");
    if (!raw.trim()) return raw;
    if (/^#{0,3}\s*视频编号\s*0*\d+/m.test(raw)) {
      return raw.replace(/^(#{0,3}\s*)视频编号\s*0*\d+/m, `$1${label}`);
    }
    if (/^视频编号\s*0*\d+\s*[:：]?/m.test(raw)) {
      return raw.replace(/^视频编号\s*0*\d+/m, label);
    }
    return raw;
  }

  /**
   * Avoid duplicate customNames. Video batches like 视频编号01-10 continue from
   * the board max (next ten → 11-20) when any name already exists.
   */
  function _allocateUniqueCreateItems(items, board, nodeType) {
    const list = (Array.isArray(items) ? items : []).map((it) => ({ ...(it || {}) }));
    if (!list.length) return list;
    const used = _existingNodeNames(board);
    const type = ["video", "image", "text"].includes(nodeType) ? nodeType : "video";

    const videoNums = list.map((it) => {
      const fromName = _extractVideoNo(it.name || it.title);
      if (fromName != null) return fromName;
      return _extractVideoNo(it.prompt || it.body || it.content || it.text || "");
    });
    const videoBatch = videoNums.length > 0 && videoNums.every((n) => n != null);

    if (videoBatch) {
      const maxExist = _maxExistingVideoNo(board);
      const conflict = videoNums.some((n) => used.has(_formatVideoNo(n)));
      if (conflict) {
        const order = videoNums
          .map((n, i) => ({ n, i }))
          .sort((a, b) => a.n - b.n || a.i - b.i);
        const remap = new Map();
        let next = maxExist + 1;
        for (const { n } of order) {
          if (!remap.has(n)) remap.set(n, next++);
        }
        for (let i = 0; i < list.length; i++) {
          const newNo = remap.get(videoNums[i]);
          list[i].name = _formatVideoNo(newNo);
          const body = normalizeMultiline(
            list[i].prompt || list[i].content || list[i].body || list[i].text || list[i].resultText || ""
          );
          const rewritten = _rewriteVideoHeading(body, newNo);
          list[i].prompt = rewritten;
          if (list[i].body != null) list[i].body = rewritten;
          if (list[i].content != null) list[i].content = rewritten;
          if (list[i].resultText != null) list[i].resultText = rewritten;
          used.add(list[i].name);
        }
        return list;
      }
      for (const n of videoNums) used.add(_formatVideoNo(n));
      return list;
    }

    for (let i = 0; i < list.length; i++) {
      let base = String(list[i].name || list[i].title || "").trim();
      if (!base) {
        base =
          type === "video"
            ? _formatVideoNo(_maxExistingVideoNo(board) + i + 1)
            : type === "text"
              ? `文本${i + 1}`
              : `图片${i + 1}`;
      }
      let name = base;
      if (used.has(name)) {
        let k = 2;
        while (used.has(`${base}${k}`) && k < 10000) k += 1;
        name = `${base}${k}`;
      }
      list[i].name = name;
      used.add(name);
    }
    return list;
  }

  /**
   * @param {{ name?: string, prompt?: string, resultText?: string }[]} items
   * @param {"video"|"image"|"text"} nodeType
   */
  function createNodesFromItems(items, nodeType = "video", opts = {}) {
    const Store = global.CanvasStore;
    const board = _activeBoard();

    const type = ["video", "image", "text"].includes(nodeType) ? nodeType : "video";
    const family = _nodeFamily(type);
    let list = (Array.isArray(items) ? items : []).filter((it) => {
      const p = normalizeMultiline(
        it?.prompt || it?.content || it?.body || it?.text || it?.resultText || ""
      ).trim();
      const n = String(it?.name || it?.title || "").trim();
      return p || n;
    });
    if (!list.length) {
      throw new Error("没有可创建的节点内容（items 需要 name 或 prompt）");
    }
    list = _allocateUniqueCreateItems(list, board, type);

    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();

    const rawLayout = opts.layout || opts.mode;
    const layoutMode =
      !rawLayout ||
      ["auto", "auto-grid", "grid", "默认"].includes(String(rawLayout).toLowerCase().trim())
        ? _defaultFamilyLayout(family, list.length)
        : rawLayout;
    const origin = _originForFamily(family, list.length, layoutMode);
    const slots = layoutSlots(list.length, { x: origin.x, y: origin.y, topLeft: true }, layoutMode);
    const nameMax = global.AgentRules?.textNode?.nameMax || 28;
    const ids = [];

    for (let i = 0; i < list.length; i++) {
      const it = list[i];
      const slot = slots[i] || { x: origin.x, y: origin.y };
      const node = Store.addNode(type, slot.x, slot.y);
      if (!node) continue;
      const body = normalizeMultiline(
        it.prompt || it.content || it.body || it.text || it.resultText || ""
      ).trim();
      const title = global.AgentParser?.resolveNodeName
        ? global.AgentParser.resolveNodeName({
            name: it.name || it.title,
            prompt: body,
            body,
            resultText: it.resultText,
            nodeType: type,
            index: i,
          })
        : String(it.name || it.title || "").trim() ||
          (type === "video" ? _formatVideoNo(i + 1) : `节点${i + 1}`);
      const preferred = String(it.name || it.title || "").trim();
      const resolved =
        preferred && (/^视频编号\s*\d+$/i.test(preferred) || preferred === title)
          ? preferred
          : title;
      const customName =
        typeof global.AgentParser?.clipNodeName === "function"
          ? global.AgentParser.clipNodeName(resolved, nameMax)
          : resolved.slice(0, nameMax);

      const patch = { customName, prompt: body };
      if (type === "text") {
        patch.resultText = normalizeMultiline(it.resultText || body).trim() || body;
        patch.status = "completed";
      } else {
        patch.status = "idle";
      }
      if (it.channel) patch.channel = String(it.channel);
      if (it.model) patch.model = String(it.model);
      if (it.promptPrefixId) patch.promptPrefixId = String(it.promptPrefixId);

      Store.updateNode(node.id, patch);
      ids.push(node.id);
    }

    _refreshCanvas();
    _selectIds(ids);
    return { ids, count: ids.length, nodeType: type };
  }

  function createTextNodesFromSegments(segments, opts = {}) {
    const items = (segments || []).map((seg) => ({
      name: seg.title,
      prompt: seg.body,
      resultText: seg.body,
    }));
    return createNodesFromItems(items, opts.nodeType || "text", opts);
  }

  function createNodesFromScript(text, opts = {}) {
    const parser = global.AgentParser;
    if (!parser) throw new Error("解析器未加载");
    const raw = String(text || "").trim();
    if (!raw) throw new Error("脚本内容为空");
    let segments = parser.parseVideoSegments(raw) || [];
    if (segments.length < 1) {
      segments = [{ index: 1, title: _formatVideoNo(1), body: raw }];
    }
    const nodeType = opts.nodeType || "video";
    const items = segments.map((seg) => ({
      name: seg.title || _formatVideoNo(seg.index),
      prompt: seg.body,
    }));
    return createNodesFromItems(items, nodeType, opts);
  }

  function updateNode(nodeId, patch = {}, options = {}) {
    const Store = global.CanvasStore;
    const board = _activeBoard();
    const node = board.nodes.find((n) => n.id === nodeId);
    if (!node) throw new Error(`节点不存在：${nodeId}`);
    const src = patch && typeof patch === "object" ? patch : {};
    const next = {};
    const promptVal =
      src.prompt != null
        ? src.prompt
        : src.content != null
          ? src.content
          : src.body != null
            ? src.body
            : null;
    if (src.customName != null) next.customName = String(src.customName);
    if (promptVal != null) next.prompt = options.exactText ? String(promptVal) : normalizeMultiline(promptVal);
    if (src.resultText != null) next.resultText = options.exactText ? String(src.resultText) : normalizeMultiline(src.resultText);
    if (src.channel != null) next.channel = String(src.channel);
    if (src.model != null) next.model = String(src.model);
    if (src.promptPrefixId != null) next.promptPrefixId = String(src.promptPrefixId);
    if (src.status != null) next.status = String(src.status);
    if (src.useUpgrade != null) next.useUpgrade = !!src.useUpgrade;
    if (src.resolution !== undefined) next.resolution = src.resolution;
    if (src.duration != null) next.duration = src.duration;
    if (src.ratio != null) next.ratio = src.ratio;
    if (src.style !== undefined) next.style = src.style;
    if (src.x != null && Number.isFinite(Number(src.x))) next.x = Number(src.x);
    if (src.y != null && Number.isFinite(Number(src.y))) next.y = Number(src.y);
    const textNode = node.type === "text";
    if (textNode && next.resultText != null) next.prompt = next.resultText;
    if (!Object.keys(next).length) {
      throw new Error("update_node 没有可应用字段（请用 prompt / resultText / customName）");
    }
    Store.updateNode(nodeId, next);
    // Generation reads the visible editor: update it before another action can run.
    if (next.prompt != null && global.document && global.PromptEditor?.mount) {
      global.document.querySelectorAll(".prompt-editor[data-node]").forEach((editor) => {
        if (editor.dataset.node === String(nodeId)) global.PromptEditor.mount(editor, next.prompt, { force: true });
      });
    }
    const updated = Store.activeBoard()?.nodes?.find((n) => n.id === nodeId) || { ...node, ...next };
    if (typeof global.refreshNodeMediaToolbar === "function") {
      try {
        global.refreshNodeMediaToolbar(updated);
      } catch {
        // The render below also rebuilds stale toolbar structure.
      }
    }
    _refreshCanvas();
    return { id: nodeId, ok: true };
  }

  function replaceNodeText(nodeId, spec) {
    const node = _activeBoard().nodes.find((n) => n.id === nodeId);
    if (!node) throw new Error("目标节点已删除，请重新定位");
    const field = spec.field || (node.type === "text" ? "resultText" : "prompt");
    if (!["prompt", "resultText", "customName"].includes(field)) throw new Error("只能修改正文、提示词或名称");
    const old = String((field === "resultText" ? node.resultText || node.prompt : node[field]) ?? "");
    const find = String(spec.find || "");
    if (!find || typeof spec.replace !== "string") throw new Error("局部修改缺少原文或替换内容");
    const hits = old.split(find).length - 1;
    if (!hits) throw new Error("未找到指定原文，节点可能已被修改，请重新核对");
    if (hits > 1 && spec.all !== true) throw new Error("原文出现多次，请指定更完整的句子或明确全部替换");
    const next = spec.all === true ? old.split(find).join(spec.replace) : old.replace(find, () => spec.replace);
    updateNode(nodeId, { [field]: next }, { exactText: true });
    return { id: nodeId, count: spec.all === true ? hits : 1 };
  }

  async function editImage(nodeId, spec, { signal } = {}) {
    const board = _activeBoard();
    const node = board.nodes.find((n) => n.id === nodeId);
    if (!node?.imageUrl || !["image", "ref-image"].includes(node.type)) throw new Error("目标节点没有可编辑的原图");
    if (["waiting", "pending", "generating"].includes(node.status)) throw new Error("该图片已有生成任务，请等完成后再修改");
    if (!String(spec.instruction || "").trim()) throw new Error("缺少图片修改要求");
    if (spec.source_url && spec.source_url !== node.imageUrl) throw new Error("图片已变化，请重新查看后再修改");
    if (!global.generateForNode) throw new Error("生成接口未就绪");
    const originalType = node.type;
    if (originalType === "ref-image") global.CanvasStore.updateNode(nodeId, { type: "image" });
    try {
      const result = await global.generateForNode(nodeId, { signal, imageEdit: { sourceUrl: node.imageUrl, instruction: spec.instruction } });
      if (!result?.submitted) {
        throw new Error("图片修改未提交，请检查图片模型和生成提示");
      }
    } catch (err) {
      if (originalType === "ref-image" && global.CanvasStore.activeBoard()?.id === board.id) {
        global.CanvasStore.updateNode(nodeId, { type: originalType });
      }
      throw err;
    }
    return { id: nodeId, ok: true };
  }

  function _userRole() {
    return String(global.__canvasUser?.role || global.AppConfig?.user_role || "").toLowerCase();
  }

  function _normalizeChannelId(raw) {
    const s = String(raw || "").trim().toLowerCase();
    if (!s) return "";
    if (
      s === "myapi" ||
      s === "api" ||
      s === "api渠道" ||
      s === "渠道api" ||
      s === "qudaoapi" ||
      s === "求道api" ||
      /api/.test(s) && /渠道|channel|myapi|qudao/.test(s)
    ) {
      return "myapi";
    }
    if (s === "bendi" || s === "本地" || s === "星晚" || s === "bendì") return "bendi";
    if (
      s === "doubao_s" ||
      s === "seed_s" ||
      s === "seeds" ||
      s === "seed s" ||
      s === "seed s求道" ||
      s === "求道" ||
      s === "seed求道" ||
      s === "dola_s"
    ) {
      return "doubao_s";
    }
    if (s === "doubao" || s === "seed" || s === "dola") return "doubao";
    if (typeof global.normalizeChannel === "function") {
      return global.normalizeChannel(s, "") || s;
    }
    return s;
  }

  function _channelAllowed(channel, node) {
    const ch = _normalizeChannelId(channel);
    if (!ch) return false;
    if (typeof global.getChannelOptions === "function") {
      const opts = global.getChannelOptions(node || { type: "image" }) || [];
      return opts.some((o) => o.id === ch);
    }
    const role = _userRole();
    if (ch === "bendi") return role === "admin";
    if (ch === "doubao" || ch === "doubao_s") return role === "admin" || role === "internal";
    if (ch === "myapi") return true;
    return false;
  }

  function _mediaKind(node) {
    if (!node) return "image";
    if (node.type === "video") return "video";
    if (node.type === "text" || node.type === "voice-text") return "text";
    return "image";
  }

  function _isGenerativeNode(node) {
    if (!node) return false;
    const t = node.type;
    return t === "image" || t === "video" || t === "text" || t === "voice-text";
  }

  function _buildChannelPatch(node, channel, model) {
    const ch = _normalizeChannelId(channel);
    if (!ch) throw new Error("未指定渠道");
    if (!_channelAllowed(ch, node)) {
      const role = _userRole();
      if (role === "user" || !role) {
        throw new Error("平台用户只能使用 MyApi 渠道并更换模型，不能切换 Seed / BenDi");
      }
      throw new Error(`当前账号无权使用渠道「${ch}」`);
    }
    if ((node.type === "text" || node.type === "voice-text") && ch === "bendi") {
      if (!(typeof global.isBendiTextAllowed === "function" && global.isBendiTextAllowed())) {
        throw new Error("BenDi 文本需在设置中切换到「本地」后端");
      }
    }

    const patch = { channel: ch };
    if (ch === "doubao_s") patch.useUpgrade = true;
    else if (ch === "doubao" || ch === "bendi") patch.useUpgrade = false;

    const kind = _mediaKind(node);
    const requestedModel = model != null && String(model).trim() ? String(model).trim() : "";
    const defaultModel =
      typeof global.defaultModelForChannel === "function"
        ? global.defaultModelForChannel(ch, kind)
        : "";

    if (kind === "image") {
      patch.model = requestedModel || defaultModel;
      if (typeof global.isBytedanceFamilyChannel === "function" && !global.isBytedanceFamilyChannel(ch)) {
        patch.style = null;
      }
      if (ch === "myapi") {
        patch.resolution = node.resolution || "默认";
        if (!["默认", "1K", "2K", "4K"].includes(patch.resolution)) patch.resolution = "默认";
      } else {
        patch.resolution = null;
      }
    } else if (kind === "video") {
      const models =
        typeof global.videoModelsForChannel === "function" ? global.videoModelsForChannel(ch) : [];
      if (requestedModel) {
        patch.model = requestedModel;
      } else if (ch === "bendi") {
        patch.model =
          defaultModel ||
          models.find((m) => /2\.5|2_5/.test(String(m?.id || "")))?.id ||
          models[0]?.id ||
          "seedance-2.5";
      } else {
        const keep = models.some((m) => m.id === node.model) ? node.model : null;
        patch.model = keep || defaultModel || models[0]?.id || "";
      }
      if (typeof global.clampVideoDuration === "function") {
        patch.duration = global.clampVideoDuration(
          node.duration ?? 10,
          patch.model,
          ch === "doubao_s",
          ch
        );
      }
      if (typeof global.normalizeVideoSettingsForModel === "function") {
        Object.assign(patch, global.normalizeVideoSettingsForModel({ ...node, ...patch }, patch.model));
      }
    } else {
      patch.model = requestedModel || defaultModel || "";
    }

    if (ch === "myapi" && !patch.model) {
      throw new Error("请先在 MyApi 中添加对应类型的模型");
    }
    return patch;
  }

  function setNodeChannel(nodeId, channel, model) {
    const board = _activeBoard();
    const node = board.nodes.find((n) => n.id === nodeId);
    if (!node) throw new Error(`节点不存在：${nodeId}`);
    if (!_isGenerativeNode(node)) {
      throw new Error(`节点 ${nodeId} 不是可切换渠道/模型的生成节点`);
    }
    const onlyModel = channel == null || String(channel).trim() === "";
    if (onlyModel) {
      const patch = { model: String(model || "").trim() };
      if (!patch.model) throw new Error("未指定模型");
      const ch =
        typeof global.resolveNodeChannel === "function"
          ? global.resolveNodeChannel(node)
          : node.channel;
      if (node.type === "video" && typeof global.normalizeVideoSettingsForModel === "function") {
        Object.assign(
          patch,
          global.normalizeVideoSettingsForModel({ ...node, channel: ch, ...patch }, patch.model)
        );
      }
      return updateNode(nodeId, patch);
    }
    const patch = _buildChannelPatch(node, channel, model);
    return updateNode(nodeId, patch);
  }

  function listGenerativeNodes(filterType) {
    const board = _activeBoard();
    const nodes = (board?.nodes || []).filter(_isGenerativeNode);
    if (!filterType) return nodes;
    const t = String(filterType).toLowerCase();
    if (t === "text") return nodes.filter((n) => n.type === "text" || n.type === "voice-text");
    return nodes.filter((n) => n.type === t);
  }

  function setNodesChannelOrModel({ nodeIds, nodeType, channel, model, scope } = {}) {
    let nodes = [];
    if (Array.isArray(nodeIds) && nodeIds.length) {
      const board = _activeBoard();
      const set = new Set(nodeIds.map(String));
      nodes = (board?.nodes || []).filter((n) => set.has(String(n.id)) && _isGenerativeNode(n));
    } else if (nodeType) {
      nodes = listGenerativeNodes(nodeType);
    } else if (scope === "selected") {
      const sel = new Set((global.CanvasStore?.selectedIds || []).map(String));
      nodes = listGenerativeNodes().filter((n) => sel.has(String(n.id)));
    } else {
      nodes = listGenerativeNodes();
    }
    if (!nodes.length) return { count: 0, ids: [], failed: 0, logs: ["没有可切换的生成节点"] };

    const ids = [];
    const logs = [];
    let failed = 0;
    for (const n of nodes) {
      try {
        setNodeChannel(n.id, channel, model);
        ids.push(n.id);
        const applied = global.CanvasStore.activeBoard()?.nodes?.find((x) => x.id === n.id) || n;
        const chId =
          channel != null && String(channel).trim()
            ? _normalizeChannelId(channel)
            : typeof global.resolveNodeChannel === "function"
              ? global.resolveNodeChannel(applied)
              : applied.channel;
        const chLabel =
          typeof global.getChannelLabel === "function" ? global.getChannelLabel(chId) : chId;
        logs.push(`已设置 ${n.id} → ${chLabel}${applied.model ? ` / ${applied.model}` : ""}`);
      } catch (err) {
        failed += 1;
        logs.push(`${n.id} 失败：${err?.message || err}`);
      }
    }
    return { count: ids.length, ids, failed, logs };
  }

  /** 按名称或 id 设置提示词前缀（人物 / 场景 / 真人…） */
  function setNodePrefix(nodeId, prefixSpec) {
    const board = _activeBoard();
    const node = board.nodes.find((n) => n.id === nodeId);
    if (!node) throw new Error(`节点不存在：${nodeId}`);
    const mediaType = node.type === "video" ? "video" : "image";
    const raw = prefixSpec == null ? "" : String(prefixSpec).trim();
    let prefixId = "";
    if (raw && raw !== "无" && raw.toLowerCase() !== "none") {
      const store = global.PromptPrefixStore;
      if (store?.getById?.(raw, mediaType)) {
        prefixId = raw;
      } else if (store?.list) {
        const list = store.list(mediaType);
        const exact = list.find((p) => String(p.name || "").trim() === raw);
        const fuzzy =
          exact ||
          list.find((p) => {
            const nm = String(p.name || "").trim();
            return nm.includes(raw) || raw.includes(nm);
          });
        if (fuzzy) prefixId = fuzzy.id;
        else throw new Error(`未找到前缀「${raw}」，请先在前缀管理中添加`);
      }
    }
    global.CanvasStore.updateNode(nodeId, { promptPrefixId: prefixId });
    if (typeof global.refreshVideoPickerLabels === "function") {
      global.refreshVideoPickerLabels(nodeId);
    }
    if (typeof global.syncPromptExpandPrefixPicker === "function") {
      global.syncPromptExpandPrefixPicker(nodeId);
    }
    _refreshCanvas();
    const label =
      typeof global.PromptPrefixStore?.getEffectiveLabel === "function"
        ? global.PromptPrefixStore.getEffectiveLabel(prefixId, mediaType)
        : prefixId || "无";
    return { id: nodeId, prefixId, label, ok: true };
  }

  function selectNodes(nodeIds) {
    const ids = (Array.isArray(nodeIds) ? nodeIds : []).filter(Boolean);
    if (!ids.length) return false;
    _selectIds(ids);
    return true;
  }

  /**
   * @param {string} mode
   * @param {string[]|"all"|null} [nodeIds]
   * @param {{ scope?: string, all?: boolean, smart?: boolean, allowBoardFallback?: boolean }} [opts]
   * @returns {{ ok: boolean, count: number, mode: string }}
   */
  function arrangeNodes(mode = "col", nodeIds, opts = {}) {
    const layout = _normalizeLayoutMode(mode);
    const Store = global.CanvasStore;
    const board = Store?.activeBoard?.();
    if (!board) return { ok: false, count: 0, mode: layout };

    const scope = String(opts.scope || "").toLowerCase();
    const wantAll =
      scope === "all" ||
      opts.all === true ||
      nodeIds === "all" ||
      (Array.isArray(nodeIds) &&
        nodeIds.length === 1 &&
        String(nodeIds[0]).toLowerCase() === "all");

    let ids = Array.isArray(nodeIds)
      ? nodeIds.map(String).filter((id) => id && id.toLowerCase() !== "all")
      : [];

    if (wantAll) {
      ids = (board.nodes || []).map((n) => n.id).filter(Boolean);
    } else if (!ids.length) {
      ids = Array.isArray(Store.selectedIds) ? Store.selectedIds.slice() : [];
    }

    // 智能体常漏传 node_ids：选中不足时回退整画布（可用 opts.allowBoardFallback 关闭）
    if (
      ids.length < 2 &&
      (board.nodes || []).length >= 2 &&
      opts.allowBoardFallback
    ) {
      ids = board.nodes.map((n) => n.id).filter(Boolean);
    }

    if (ids.length < 2) return { ok: false, count: 0, mode: layout };

    const coveringAll =
      wantAll || ids.length >= Math.max(2, (board.nodes || []).length);
    const useSmart =
      opts.smart === true ||
      (opts.smart !== false &&
        coveringAll &&
        (layout === "col" || layout === "col-stack" || layout === "auto"));

    if (useSmart) {
      if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();
      tidyFamilyClusters();
      _refreshCanvas();
      _selectIds(ids);
      return { ok: true, count: ids.length, mode: "family-col" };
    }

    _selectIds(ids);
    if (typeof global.arrangeSelectedNodes === "function") {
      global.arrangeSelectedNodes(layout, {
        silent: true,
        gapX: _layoutOpts().gapX,
        gapY: _layoutOpts().gapY,
      });
      return { ok: true, count: ids.length, mode: layout };
    }

    // 无全局 arrange 时：至少用分族排版兜底
    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();
    packNodesBySize(ids, layout, { keepOrigin: true });
    _refreshCanvas();
    return { ok: true, count: ids.length, mode: layout };
  }

  function arrangeSelectedRegular(mode) {
    const r = arrangeNodes(mode || "col");
    return !!r.ok;
  }

  function moveNodes(spec) {
    const Store = global.CanvasStore;
    const board = _activeBoard();
    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();

    let moved = 0;
    if (Array.isArray(spec?.items) && spec.items.length) {
      for (const it of spec.items) {
        const id = it.node_id || it.id;
        const node = board.nodes.find((n) => n.id === id);
        if (!node) continue;
        const patch = {};
        if (it.x != null && Number.isFinite(Number(it.x))) patch.x = Number(it.x);
        if (it.y != null && Number.isFinite(Number(it.y))) patch.y = Number(it.y);
        if (it.dx != null && Number.isFinite(Number(it.dx))) patch.x = (node.x || 0) + Number(it.dx);
        if (it.dy != null && Number.isFinite(Number(it.dy))) patch.y = (node.y || 0) + Number(it.dy);
        if (Object.keys(patch).length) {
          Store.updateNode(id, patch);
          moved += 1;
        }
      }
    } else {
      const ids = (spec?.node_ids || spec?.ids || []).filter(Boolean);
      const dx = Number(spec?.dx) || 0;
      const dy = Number(spec?.dy) || 0;
      if (!ids.length || (!dx && !dy)) return { count: 0 };
      for (const id of ids) {
        const node = board.nodes.find((n) => n.id === id);
        if (!node) continue;
        Store.updateNode(id, { x: (node.x || 0) + dx, y: (node.y || 0) + dy });
        moved += 1;
      }
    }
    _refreshCanvas();
    return { count: moved };
  }

  function connectNodes(fromId, toId) {
    const Store = global.CanvasStore;
    _activeBoard();
    if (!fromId || !toId || fromId === toId) throw new Error("连线需要不同的 from / to");
    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();
    const ok = Store.addConnection(fromId, toId);
    if (typeof global.refreshNodeInputRefs === "function") {
      global.refreshNodeInputRefs(toId);
    }
    _refreshCanvas();
    return { ok: !!ok, from: fromId, to: toId };
  }

  function _nodeIsImageLike(n) {
    if (!n) return false;
    const t = String(n.type || n.mediaType || n.media_type || "").toLowerCase();
    return t === "image" || t === "img" || t === "ref-image" || t === "reference";
  }

  /**
   * 删除连线。支持：
   * - from + to：删这一对（默认双向匹配）
   * - scope:"between_images"：删所有图↔图互相牵线
   * - pairs:[{from,to},...]
   */
  function disconnectNodes(spec = {}) {
    const Store = global.CanvasStore;
    const board = _activeBoard();
    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();

    const scope = String(spec.scope || spec.mode || "").toLowerCase().replace(/-/g, "_");
    const either =
      spec.either_direction !== false &&
      spec.eitherDirection !== false &&
      spec.bidirectional !== false;
    const conns = [...(board.connections || [])];
    const touchHosts = new Set();
    let removed = 0;

    const matchesPair = (c, fromId, toId) => {
      const a = c.from || c.source;
      const b = c.to || c.target;
      if (a === fromId && b === toId) return true;
      if (either && a === toId && b === fromId) return true;
      return false;
    };

    const drop = (c) => {
      if (!c?.id || typeof Store.removeConnection !== "function") return;
      const host = c.to || c.target;
      Store.removeConnection(c.id, false);
      removed += 1;
      if (host) touchHosts.add(host);
    };

    if (scope === "between_images" || scope === "image_to_image" || scope === "images") {
      const imageIds = new Set((board.nodes || []).filter(_nodeIsImageLike).map((n) => n.id));
      for (const c of conns) {
        const a = c.from || c.source;
        const b = c.to || c.target;
        if (imageIds.has(a) && imageIds.has(b)) drop(c);
      }
    } else {
      const pairs = [];
      if (Array.isArray(spec.pairs) && spec.pairs.length) {
        for (const p of spec.pairs) {
          const from = p?.from || p?.from_id || p?.source || p?.ref_id || p?.ref;
          const to = p?.to || p?.to_id || p?.target || p?.host_id || p?.host;
          if (from && to) pairs.push([String(from), String(to)]);
        }
      } else {
        const from =
          spec.from || spec.from_id || spec.source || spec.ref_id || spec.ref || spec.source_id;
        const to = spec.to || spec.to_id || spec.target || spec.host_id || spec.host || spec.target_id;
        if (from && to) pairs.push([String(from), String(to)]);
      }
      if (!pairs.length) throw new Error("断线需要 from/to，或 scope:between_images");
      for (const c of conns) {
        for (const [fromId, toId] of pairs) {
          if (matchesPair(c, fromId, toId)) {
            drop(c);
            break;
          }
        }
      }
    }

    Store.save?.();
    if (typeof global.refreshNodeInputRefs === "function") {
      for (const id of touchHosts) global.refreshNodeInputRefs(id);
    }
    _refreshCanvas();
    return { ok: removed > 0, count: removed, scope: scope || "pair" };
  }

  /**
   * @ 引用：默认只把提示词里**已有名字**替换成 名@ref:id（不改文案、不追加）。
   * soft_only / into_prompt:false 时仅 soft 引用。
   * 仅当 opts.append===true 时才允许找不到名字时追加到文末。
   * into_prompt 且提示词无同名 → 不牵线、不 soft、不改文案（避免乱绑无关场景）。
   */
  function mentionRef(hostId, refId, opts = {}) {
    const board = _activeBoard();
    if (!hostId || !refId || hostId === refId) throw new Error("@ 引用需要 host 与 ref");
    const softOnly =
      opts.soft_only === true ||
      opts.softOnly === true ||
      opts.into_prompt === false ||
      opts.intoPrompt === false;
    const intoPrompt = !softOnly;
    const allowAppend = opts.append === true || opts.force_append === true;
    const wantConnect = opts.connect !== false;

    const Store = global.CanvasStore;
    const host = board.nodes.find((n) => n.id === hostId);
    const ref = board.nodes.find((n) => n.id === refId);
    if (!host) throw new Error(`找不到 host 节点 ${hostId}`);
    if (!ref) throw new Error(`找不到引用节点 ${refId}`);

    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();

    const replaceName = String(
      opts.replace_name ||
        opts.replaceName ||
        opts.name ||
        (typeof global.getNodeDisplayName === "function"
          ? global.getNodeDisplayName(ref, board)
          : "") ||
        ref.customName ||
        ""
    ).trim();
    const voiceId = opts.voice_id || opts.voiceId || "";
    const token = `@ref:${refId}`;
    let nextPrompt = String(host.prompt || "");
    const already = nextPrompt.includes(token) || new RegExp(`@ref:${refId}\\b`).test(nextPrompt);

    let replaced = 0;
    let mode = softOnly ? "soft" : "no_name_match";

    if (intoPrompt) {
      if (replaceName && typeof global.applyQuickNameMentions === "function") {
        const r = global.applyQuickNameMentions(nextPrompt, replaceName, refId, voiceId);
        if (r?.count > 0) {
          nextPrompt = r.prompt;
          replaced = r.count;
          mode = "name";
        }
      }
      if (!replaced && !already && allowAppend) {
        const piece = replaceName ? `${replaceName}${token}` : token;
        nextPrompt = (nextPrompt.trim() ? nextPrompt.trimEnd() + "\n" : "") + piece;
        replaced = 1;
        mode = replaceName ? "append_named" : "append";
      } else if (!replaced && already) {
        mode = "exists";
      } else if (!replaced) {
        mode = "no_name_match";
      }
    }

    // 提示词里没有该名时：禁止顺手牵线/ soft，否则会把公寓客厅绑进公司茶水间
    const bindOk = softOnly || replaced > 0 || already || allowAppend || mode === "exists";

    if (wantConnect && bindOk) {
      try {
        const conns = board.connections || [];
        const linked = conns.some(
          (c) =>
            (c.from === refId && c.to === hostId) ||
            (c.source === refId && c.target === hostId)
        );
        if (!linked && typeof Store.addConnection === "function") {
          Store.addConnection(refId, hostId);
        }
      } catch {
        /* ignore */
      }
    }

    let softOk = false;
    if (bindOk && typeof global.softAddReference === "function") {
      softOk = !!global.softAddReference(hostId, refId, { silent: true });
    }

    if (intoPrompt && nextPrompt !== String(host.prompt || "")) {
      Store.updateNode(hostId, { prompt: nextPrompt });
      if (typeof global.syncPromptEditorsForNode === "function") {
        global.syncPromptEditorsForNode(hostId, nextPrompt);
      }
      if (
        replaceName &&
        typeof global.appendQuickMentionRecord === "function" &&
        mode === "name"
      ) {
        try {
          global.appendQuickMentionRecord(hostId, {
            name: replaceName,
            materialId: refId,
            voiceId: voiceId || "",
          });
        } catch {
          /* optional */
        }
      }
    }

    if (intoPrompt && bindOk && typeof global.insertMention === "function") {
      try {
        global.insertMention(refId, hostId);
      } catch {
        /* DOM 可能未就绪 */
      }
    }

    if (intoPrompt || softOk) {
      _refreshCanvas();
    } else if (typeof global.refreshNodeInputRefs === "function") {
      global.refreshNodeInputRefs(hostId);
    }

    return {
      ok: bindOk || softOnly,
      host: hostId,
      ref: refId,
      intoPrompt,
      soft: softOk,
      replaced,
      mode,
      bound: bindOk,
    };
  }

  async function extractFrame(nodeId, mode = "current") {
    const m = String(mode || "current").toLowerCase();
    const normalized = m === "first" || m === "首帧" ? "first" : m === "last" || m === "尾帧" ? "last" : "current";
    const FC = global.FrameCapture;
    if (!FC || typeof FC.run !== "function") throw new Error("截帧工具未就绪");
    await FC.run(nodeId, normalized);
    return { ok: true, nodeId, mode: normalized };
  }

  async function runMediaTool(nodeId, tool) {
    const t = String(tool || "").toLowerCase();
    const MT = global.MediaTools;
    if (!MT) throw new Error("媒体工具未就绪");

    if (t === "ai_mark" || t === "ai-mark" || t === "toggle_ai_mark") {
      if (typeof MT.toggleAiMark !== "function") throw new Error("AI 标记不可用");
      await MT.toggleAiMark(nodeId);
      return { ok: true, tool: "ai_mark" };
    }
    if (t === "ai_pass" || t === "ai-pass" || t === "run_ai_pass") {
      if (typeof MT.runAiPass !== "function") throw new Error("AI 处理不可用");
      await MT.runAiPass(nodeId);
      return { ok: true, tool: "ai_pass" };
    }
    if (t === "extract_audio" || t === "extract_video" || t === "reverse") {
      if (typeof MT.run !== "function") throw new Error("媒体工具不可用");
      await MT.run(nodeId, t);
      return { ok: true, tool: t };
    }
    if (t === "download") {
      if (typeof MT.download !== "function") throw new Error("下载不可用");
      await MT.download(nodeId);
      return { ok: true, tool: "download" };
    }
    throw new Error(`不支持的媒体工具：${tool}`);
  }

  function deleteNodes(nodeIds) {
    const Store = global.CanvasStore;
    _activeBoard();
    const ids = (Array.isArray(nodeIds) ? nodeIds : []).filter(Boolean);
    if (!ids.length) return { count: 0 };
    if (typeof global.CanvasHistory?.push === "function") global.CanvasHistory.push();
    let n = 0;
    for (const id of ids) {
      if (typeof Store.deleteNode === "function") {
        Store.deleteNode(id, false);
        n += 1;
      }
    }
    Store.save?.();
    _refreshCanvas();
    return { count: n };
  }

  async function generateNodes(nodeIds, { signal } = {}) {
    const ids = Array.isArray(nodeIds) ? [...new Set(nodeIds.filter(Boolean))] : [];
    if (!ids.length) return { count: 0 };
    const gen = typeof global.generateForNode === "function" ? global.generateForNode : null;
    if (!gen) throw new Error("生成接口未就绪");
    let ok = 0;
    const failed = [];
    const boardId = global.CanvasStore?.activeBoard?.()?.id;
    for (const id of ids) {
      if (signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      try {
        if (boardId !== global.CanvasStore?.activeBoard?.()?.id) throw new Error("画板已切换，未继续提交生成");
        const result = await gen(id, { signal });
        if (!result?.submitted) throw new Error("未提交生成，请检查目标节点是否忙碌、提示词和模型配置");
        ok += 1;
      } catch (err) {
        if (err?.name === "AbortError" || signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
        console.warn("[agent] generate failed", id, err);
        failed.push({ id, error: String(err?.message || err) });
        break;
      }
    }
    return { count: ok, failed };
  }

  global.AgentOps = {
    layoutSlots,
    packNodesBySize,
    tidyFamilyClusters,
    createNodesFromItems,
    createTextNodesFromSegments,
    createNodesFromScript,
    updateNode,
    replaceNodeText,
    editImage,
    setNodeChannel,
    setNodesChannelOrModel,
    listGenerativeNodes,
    normalizeChannelId: _normalizeChannelId,
    channelAllowed: _channelAllowed,
    userRole: _userRole,
    setNodePrefix,
    selectNodes,
    arrangeNodes,
    arrangeSelectedRegular,
    moveNodes,
    connectNodes,
    disconnectNodes,
    mentionRef,
    extractFrame,
    runMediaTool,
    deleteNodes,
    generateNodes,
    getAnchorWorld: _anchorWorld,
    normalizeLayoutMode: _normalizeLayoutMode,
  };
})(typeof window !== "undefined" ? window : globalThis);
