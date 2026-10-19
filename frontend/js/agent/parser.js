/**
 * 解析分镜 / 合集脚本，拆出「视频编号」段落。
 */
(function (global) {
  "use strict";

  function _rules() {
    return global.AgentRules || {};
  }

  /**
   * @param {string} text
   * @returns {{ index: number, title: string, body: string, start: number, end: number }[]}
   */
  /** 多种「视频编号 / 分镜 / 镜头」标题行 */
  const SEGMENT_HEADING_RES = [
    /^#{1,3}\s*视频编号\s*0*(\d+)\s*$/gm,
    /^视频编号\s*0*(\d+)\s*[:：]?\s*$/gm,
    /^【\s*视频编号\s*0*(\d+)\s*】\s*$/gm,
    /^#{1,3}\s*视频\s*0*(\d+)\s*$/gm,
    /^#{0,3}\s*(?:分镜|镜头|镜号)\s*0*(\d+)\s*[:：]?\s*$/gm,
    /^(?:分镜|镜头|镜号)\s*0*(\d+)\s*[:：]\s*/gm,
  ];

  function _collectHeadingHits(raw, re) {
    const hits = [];
    const flags = re.flags.includes("g") ? re.flags : `${re.flags}g`;
    const rx = new RegExp(re.source, flags);
    let m;
    while ((m = rx.exec(raw)) !== null) {
      hits.push({
        index: Number(m[1]),
        title: m[0].replace(/^#+\s*/, "").replace(/[:：]\s*$/, "").trim(),
        start: m.index,
        headingEnd: m.index + m[0].length,
      });
    }
    return hits;
  }

  function _segmentsFromHits(raw, hits) {
    if (!hits.length) return [];
    hits.sort((a, b) => a.start - b.start);
    const segments = [];
    for (let i = 0; i < hits.length; i++) {
      const cur = hits[i];
      const next = hits[i + 1];
      const end = next ? next.start : raw.length;
      let body = raw.slice(cur.start, end).trim();
      if (i === hits.length - 1) {
        const cut = body.search(/\n---\s*\n+##\s*尾帧使用判断/);
        if (cut > 0) body = body.slice(0, cut).trim();
      }
      if (!body) continue;
      segments.push({
        index: cur.index || i + 1,
        title: cur.title || `视频编号${String(cur.index || i + 1).padStart(2, "0")}`,
        body,
        start: cur.start,
        end,
      });
    }
    return segments;
  }

  function parseVideoSegments(text) {
    const raw = String(text || "").replace(/\r\n/g, "\n");
    if (!raw.trim()) return [];

    for (const re of SEGMENT_HEADING_RES) {
      const hits = _collectHeadingHits(raw, re);
      const segs = _segmentsFromHits(raw, hits);
      if (segs.length >= 1) return segs;
    }

    // 无标准标题：按空行切成多段（至少 2 段才算拆分成功）
    const blocks = raw
      .split(/\n{2,}/)
      .map((b) => b.trim())
      .filter((b) => b.length >= 8);
    if (blocks.length >= 2) {
      return blocks.map((body, i) => ({
        index: i + 1,
        title: `视频编号${String(i + 1).padStart(2, "0")}`,
        body,
        start: 0,
        end: body.length,
      }));
    }

    return [];
  }

  /**
   * 从整篇脚本中抽出章节标题（若有）
   */
  function extractScriptMeta(text) {
    const raw = String(text || "");
    const titleM = raw.match(/^#\s+(.+)$/m);
    return {
      title: titleM ? titleM[1].trim() : "",
      segmentCount: parseVideoSegments(raw).length,
    };
  }

  function looksLikeVideoScript(text) {
    const min = _rules().scriptPatterns?.minSegmentsToAutoSplit ?? 2;
    return parseVideoSegments(text).length >= min;
  }

  function clipNodeName(title, maxLen) {
    const lim = maxLen || _rules().textNode?.nameMax || 28;
    const t = String(title || "").replace(/\s+/g, " ").trim();
    if (t.length <= lim) return t;
    return `${t.slice(0, lim - 1)}…`;
  }

  function isGenericNodeName(name) {
    const t = String(name || "")
      .replace(/\s+/g, " ")
      .trim();
    if (!t) return true;
    if (/^(节点|图片|图像|视频|文本|角色图|人物图|场景图|素材|未命名)(\s*\d+)?$/i.test(t)) {
      return true;
    }
    if (/^(image|video|text|node|untitled)(\s*\d+)?$/i.test(t)) return true;
    if (/^视频编号\s*\d+$/i.test(t)) return false;
    // 过长且像整段提示词
    if (t.length > 22 && /[，,。；;、]/.test(t)) return true;
    return false;
  }

  /**
   * 从提示词推断短节点名：人名只留人名，场景只留场景名。
   * @param {string} prompt
   * @param {{ nodeType?: string, fallback?: string, index?: number }} opts
   */
  function inferNodeNameFromPrompt(prompt, opts = {}) {
    const nodeType = String(opts.nodeType || "image").toLowerCase();
    const raw = String(prompt || "").replace(/\r\n/g, "\n").trim();
    const nameMax = _rules().textNode?.nameMax || 28;
    const shortMax = Math.min(16, nameMax);

    if (!raw) {
      const fb = String(opts.fallback || "").trim();
      if (fb && !isGenericNodeName(fb)) return clipNodeName(fb, nameMax);
      if (nodeType === "video") {
        return `视频编号${String((opts.index || 0) + 1).padStart(2, "0")}`;
      }
      return nodeType === "text" ? "文本" : nodeType === "image" ? "图片" : "节点";
    }

    // 视频编号标题
    const vid = raw.match(/^#{0,3}\s*视频编号\s*0*(\d+)/m);
    if (vid) return clipNodeName(`视频编号${String(vid[1]).padStart(2, "0")}`, nameMax);

    const labeled = [
      /(?:^|\n)\s*(?:人物|角色|人名|姓名|主角|配角)\s*[:：]\s*([^\n，,。；;|｜]{1,16})/i,
      /(?:^|\n)\s*(?:场景|地点|环境|背景)\s*[:：]\s*([^\n，,。；;|｜]{1,16})/i,
      /(?:^|\n)\s*(?:标题|名称|名字)\s*[:：]\s*([^\n，,。；;|｜]{1,16})/i,
    ];
    for (const re of labeled) {
      const m = raw.match(re);
      if (m?.[1]) {
        const v = m[1].replace(/[《》【】\[\]「」""'']/g, "").trim();
        if (v) return clipNodeName(v, shortMax);
      }
    }

    // 《书名/标题》
    const book = raw.match(/《([^》]{1,16})》/);
    if (book?.[1]) return clipNodeName(book[1], shortMax);

    // 首行短标题
    const firstLine = raw.split("\n").map((l) => l.trim()).find(Boolean) || "";
    let line = firstLine
      .replace(/^#+\s*/, "")
      .replace(/^(人物|角色|场景|提示词|Prompt)\s*[:：]\s*/i, "")
      .trim();

    // 「沈知晓父亲角色设定…」→ 截到「角色/设定/提示」前
    const cut = line.search(
      /(?:角色设定|人物设定|场景设定|提示词|生成|画像|写真|中年|青年|都市|高清|细节|背景)/
    );
    if (cut > 1 && cut <= 14) line = line.slice(0, cut);

    // 去掉末尾「角色/图/设定」等套话
    line = line
      .replace(/(?:角色图|人物图|场景图|设定图|参考图|图片|图像)$/u, "")
      .replace(/(?:角色|人物|场景|设定)$/u, "")
      .trim();

    // 像「XX的父亲」保留；纯形容词丢掉
    if (line && line.length <= shortMax && !/^[，,。；;\-—]+$/.test(line)) {
      // 若仍像长提示词，取逗号前
      if (line.length > 12 && /[，,]/.test(line)) {
        line = line.split(/[，,]/)[0].trim();
      }
      if (line && !isGenericNodeName(line)) return clipNodeName(line, shortMax);
    }

    // 回退：给定 name 若可用则用
    const fb = String(opts.fallback || "").trim();
    if (fb && !isGenericNodeName(fb)) return clipNodeName(fb, shortMax);

    // 再回退：正文前 8～12 字（去标点）
    const compact = raw.replace(/\s+/g, "").replace(/[《》【】\[\]「」""'']/g, "");
    if (compact.length >= 2) {
      return clipNodeName(compact.slice(0, Math.min(10, shortMax)), shortMax);
    }

    if (nodeType === "video") {
      return `视频编号${String((opts.index || 0) + 1).padStart(2, "0")}`;
    }
    return nodeType === "text" ? "文本" : nodeType === "image" ? "图片" : "节点";
  }

  /**
   * 决定最终节点名：优先可信的 AI name，否则从提示词推断。
   */
  function resolveNodeName(opts = {}) {
    const nameMax = _rules().textNode?.nameMax || 28;
    const given = String(opts.name || opts.title || "").trim();
    const prompt = String(opts.prompt || opts.body || opts.resultText || "");
    const nodeType = opts.nodeType || "image";
    if (given && !isGenericNodeName(given) && given.length <= nameMax) {
      // 仍过长像提示词时改推断
      if (!(given.length > 16 && /[，,。]/.test(given))) {
        return clipNodeName(given, nameMax);
      }
    }
    return inferNodeNameFromPrompt(prompt, {
      nodeType,
      fallback: given,
      index: opts.index,
    });
  }

  global.AgentParser = {
    parseVideoSegments,
    extractScriptMeta,
    looksLikeVideoScript,
    clipNodeName,
    isGenericNodeName,
    inferNodeNameFromPrompt,
    resolveNodeName,
  };
})(typeof window !== "undefined" ? window : globalThis);
