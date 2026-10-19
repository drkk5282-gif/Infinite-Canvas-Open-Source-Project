/**
 * 智能体意图：问题一律交给模型；按 roleMode / execMode 决定是否执行画布操作。
 * roleMode: agent=可自动化操作画布；ask=仅问答
 * execMode: confirm=询问后执行；auto=直接操作
 */
(function (global) {
  "use strict";

  function stripMentionMarkers(text) {
    return String(text || "")
      .replace(/@\[[^\]]+\]\([^)]+\)/g, "")
      .replace(/@[^\s@]+/g, "")
      .trim();
  }

  function describeMentionNode(node) {
    if (!node) return "";
    const parts = [
      `id=${node.id}`,
      `type=${node.type}`,
      `name=${node.customName || ""}`,
    ];
    if (node.videoUrl) parts.push(`videoUrl=${node.videoUrl}`);
    if (node.imageUrl) parts.push(`imageUrl=${node.imageUrl}`);
    if (node.audioUrl) parts.push(`audioUrl=${node.audioUrl}`);
    if (node.coverUrl) parts.push(`coverUrl=${node.coverUrl}`);
    const body =
      typeof global.getTextNodeBody === "function"
        ? global.getTextNodeBody(node)
        : String(node.resultText || node.prompt || "");
    const text = String(body || node.prompt || "");
    const head = `[引用节点 ${parts.join(" ")}]`;
    const fidelityNote = text.includes("\n")
      ? "\n（以下为节点原文，含换行；写入 prompt/resultText 时必须原样复制，禁止压成一行）\n"
      : "\n";
    if (node.videoUrl || node.audioUrl) {
      return (
        `${head}` +
        fidelityNote +
        `（含音视频；若用户要求听内容 / 语音分析 / 转写，请说明需本地 Whisper，勿要求配置 Whisper 模型 ID。）\n` +
        (text ? `${text}` : "")
      );
    }
    return text ? `${head}${fidelityNote}${text}` : head;
  }

  function expandMentionsIntoMessage(userText, mentions) {
    let msg = String(userText || "");
    const board = global.CanvasStore?.activeBoard?.();
    const list = Array.isArray(mentions) ? mentions : [];
    if (!board || !list.length) return stripMentionMarkers(msg);

    const chunks = [];
    for (const m of list) {
      const node = board.nodes.find((n) => n.id === m.id);
      if (!node) continue;
      chunks.push(describeMentionNode(node));
    }
    const plain = stripMentionMarkers(msg);
    if (!chunks.length) return plain;
    return `${plain}\n\n---\n引用内容：\n${chunks.join("\n\n")}`;
  }

  function modeDirective(roleMode, execMode) {
    const role = roleMode === "ask" ? "ask" : "agent";
    const exec = execMode === "auto" ? "auto" : "confirm";
    const priority =
      "\n本轮意图优先：只以用户最新一条消息为准。" +
      "意图明确（创建节点/改提示词/出图/排版/牵线等）→ 直接给 actions，禁止再追问。" +
      "画布统计 total>0 时禁止说未检测到节点；用节点索引里的 id 操作。" +
      "索引有同名但 empty ≠ 没有：抱怨没图 → generate_nodes；改提示词 → update_node；禁止再 create 同名。" +
      "update_node：短文用 id+prompt；按文档改长提示词用 fill_from_document:true + source_name + prompt:\"\"（前端回填），禁止 content/body/negative。" +
      "create_nodes 只建空壳不出图；要出图必须另给 generate_nodes。" +
      "reply 有 actions 时用「准备…」，禁止「已经/正在创建」装成完成；actions=[] 时禁止声称已操作。";
    const thinkFirst =
      "\n先在 thinking 判断意图。仅当真缺信息时才用 choices；" +
      "choices 必须灵活理解本轮用户原话并动态生成（可当指令点选），" +
      "严禁任何固定套话菜单（创建相关节点/整理排版/检查画布/请继续等）。" +
      "前端只加「自己说」。意图已清 → 直接 actions，不要硬问。" +
      "thinking 禁止纠结 update_node 字段名——按协议直接写。";
    const userVoice =
      "\nreply 写给普通用户：简单白话即可。" +
      "禁止 JSON/字段名/action 名/规则编号/节点 id。思考过程只放 thinking，不要写进 reply。";
    const fidelity =
      "\n内容铁律：分镜用 create_from_script 且 script=\"\"（前端回填全文）；禁止把长分镜塞进 JSON。" +
      "按文档批量改场景/人物长提示词：update_node + fill_from_document:true + source_name，prompt 留空；禁止把全文抄进 actions。" +
      "action 用扁平 type，禁止 tool/args。" +
      "牵线+艾特：只连「本段视频提示词里真正出现的人物/场景名」对应的素材图；" +
      "connect_nodes(from=素材,to=视频) + mention_ref(host=视频,ref=素材,into_prompt:true)。" +
      "艾特=把提示词里已有名字变成@芯片，禁止改写/摘要/追加无关文案；提示词没有该名就不要硬塞。" +
      "画布有图但本段没用到的场景（如公寓客厅 vs 公司茶水间）→ 不牵线不艾特。" +
      "相似场景（餐厅/客厅/客餐厅）按提示词原文精确匹配，禁止乱绑。" +
      "删图↔图互相牵线：disconnect_nodes scope:\"between_images\"；删指定线：from+to。" +
      "from/to/host/ref 必须填画布真实 id，禁止空字段。";
    const noOverAct =
      "\n纯检查/列举/提取/问答（本轮没说创建/改/生成）→ 只 reply，actions=[]；" +
      "同一句还要求创建/补齐/改提示词/出图 → 必须 actions。";
    if (role === "ask") {
      return (
        "\n\n[模式: Ask 仅问答] 只回答问题，actions 必须 []。" +
        priority +
        thinkFirst +
        userVoice
      );
    }
    if (exec === "confirm") {
      return (
        "\n\n[模式: Agent · 询问] 意图明确就给 actions（前端会确认后才执行）。" +
        "reply 必须用「准备…请确认」，禁止「已经创建/已经更新」。" +
        priority +
        thinkFirst +
        userVoice +
        noOverAct +
        fidelity
      );
    }
    return (
      "\n\n[模式: Agent · 操作] 意图明确立即 actions 执行；不要反复询问。" +
      "普通操作立即执行，生成及修改原图必须等前端确认。reply 使用「准备…」，不能提前声称完成。" +
      priority +
      thinkFirst +
      userVoice +
      noOverAct +
      fidelity
    );
  }

  /** 检查/列举/提取等：只回答，不改画布 */
  function isReadOnlyUserText(userText) {
    let t = String(userText || "").split("\n\n---\n引用内容：")[0].trim();
    if (t.length > 400 && t.includes("\n")) {
      const tail = t.split("\n").pop().trim();
      if (tail.length > 0 && tail.length < 240 && !/^[#|\-]/.test(tail)) t = tail;
    }
    if (/(为什么|为啥|怎么).{0,12}(没创建|创建失败|没成功|失败了|报错)/.test(t) &&
        !/(重新|再|帮我).{0,6}(创建|生成|修复)/.test(t)) return true;
    const affirmative = t.replace(/(?:不要|不用|别|无需|不需要|先不|暂不)\s*(?:再)?\s*(?:创建|新建|生成|出图|修改|改画布|建节点)/g, "").replace(/生成提示词/g, "提示词");
    if (/创建|新建|补齐|补上|更新|修改|改提示词|改成|改为|换成|替换|生成|出图|牵线|连线|艾特|排版|删除|写入|放到画布/.test(affirmative)) return false;
    return /提取|列出|列举|检查|看看|有哪些|有什么|缺哪些|缺什么|只回答|只分析|仅分析|只告诉/.test(t);
  }

  function isRetryOnlyUserText(t) {
    const s = String(t || "").trim();
    if (!s) return false;
    return (
      /^(好的[，,]?)?(请)?继续[.。!！…]*$/i.test(s) ||
      /^(请)?(重新|重试|再试|再来|再发)(一次|一遍)?[.。!！…]*$/i.test(s) ||
      /^(重新|重试|再试一下|再来一次)$/i.test(s)
    );
  }

  /** 「继续/重新」时找回上一句真实用户意图，禁止根据助手旧建议改写成创建 */
  function resolveContinueToLastUserIntent(plain, history) {
    const t = String(plain || "").trim();
    if (!isRetryOnlyUserText(t)) {
      return { text: plain, fromContinue: false };
    }
    const hist = Array.isArray(history) ? history : [];
    for (let i = hist.length - 1; i >= 0; i -= 1) {
      const h = hist[i];
      if (h?.role !== "user") continue;
      const content = String(h.content || "").trim();
      if (!content || isRetryOnlyUserText(content)) continue;
      return {
        text:
          content +
          "\n\n（用户点了继续/重新：必须严格按上面这句用户原文执行；" +
          "若是提取/列出人物场景 → 只文字回答，actions=[]；" +
          "禁止改成创建节点，禁止再问「你想让我做什么」。）",
        fromContinue: true,
        originalUser: content,
      };
    }
    return {
      text:
        "用户说重新/继续，但找不到上一句具体需求。请用 choices 问清要做什么（具体操作名），actions=[]。",
      fromContinue: true,
      originalUser: "",
    };
  }

  const MUTATING_ACTION_TYPES = new Set([
    "create_nodes",
    "create_from_script",
    "split_script",
    "update_node",
    "replace_node_text",
    "edit_image",
    "delete_nodes",
    "layout_nodes",
    "layout",
    "layout_selected",
    "layout_all",
    "tidy_layout",
    "move_nodes",
    "move",
    "connect_nodes",
    "connect",
    "disconnect_nodes",
    "disconnect",
    "remove_connection",
    "delete_connection",
    "mention_ref",
    "mention",
    "soft_ref",
    "wire_mention",
    "generate_nodes",
    "set_node_channel",
    "set_nodes_channel",
    "set_model",
    "set_nodes_model",
    "set_node_prefix",
    "extract_frame",
    "media_tool",
  ]);

  function stripMutatingActions(actions) {
    return (Array.isArray(actions) ? actions : []).filter((a) => {
      const t = String(a?.type || "").trim();
      return t && !MUTATING_ACTION_TYPES.has(t);
    });
  }

  function detectLocalIntent(userText, ctx = {}) {
    const raw = String(userText || "");
    const plain = stripMentionMarkers(raw);
    return {
      action: "agent",
      confidence: 1,
      payload: {
        text: expandMentionsIntoMessage(raw, ctx.mentions),
        displayText: plain,
        mentionedIds: (ctx.mentions || []).map((m) => m.id),
      },
    };
  }

  function nodeActionLabel(id) {
    const raw = String(id || "").trim();
    if (!raw) return "?";
    const board = global.CanvasStore?.activeBoard?.();
    const n = board?.nodes?.find((x) => String(x.id) === raw);
    if (!n) return raw.length > 10 ? raw.slice(0, 8) : raw;
    const name =
      (typeof global.getNodeDisplayName === "function" && global.getNodeDisplayName(n, board)) ||
      n.customName ||
      "";
    return String(name || raw).trim() || raw;
  }

  function connectEnds(a) {
    const from =
      a?.from || a?.from_id || a?.source || a?.ref_id || a?.ref || a?.source_id || "";
    const to =
      a?.to || a?.to_id || a?.target || a?.host_id || a?.host || a?.target_id || "";
    return { from, to };
  }

  function mentionEnds(a) {
    const host =
      a?.host_id || a?.host || a?.to || a?.to_id || a?.target || a?.target_id || "";
    const ref =
      a?.ref_id || a?.ref || a?.from || a?.from_id || a?.source || a?.source_id || "";
    return { host, ref };
  }

  function summarizeActions(actions) {
    return (actions || [])
      .map((a) => {
        const t = a?.type || "?";
        if (t === "create_nodes") {
          const n = (a.items || a.nodes || []).length || "?";
          return `创建 ${n} 个节点`;
        }
        if (t === "layout_nodes" || t === "layout" || t === "layout_selected")
          return `整理布局（${a.mode || a.layout || "auto"}）`;
        if (t === "move_nodes" || t === "move") return "移动节点";
        if (t === "connect_nodes" || t === "connect") {
          const { from, to } = connectEnds(a);
          return `连接 ${nodeActionLabel(from)} → ${nodeActionLabel(to)}`;
        }
        if (
          t === "disconnect_nodes" ||
          t === "disconnect" ||
          t === "remove_connection" ||
          t === "delete_connection"
        ) {
          const scope = String(a.scope || a.mode || "").toLowerCase();
          if (scope === "between_images" || scope === "image_to_image" || scope === "images") {
            return "删除图↔图互相连线";
          }
          const { from, to } = connectEnds(a);
          return `断开 ${nodeActionLabel(from)} ↔ ${nodeActionLabel(to)}`;
        }
        if (t === "mention_ref" || t === "mention" || t === "soft_ref" || t === "wire_mention") {
          const { host, ref } = mentionEnds(a);
          const soft =
            t === "soft_ref" || a.soft_only === true || a.into_prompt === false;
          return soft
            ? `素材引用 ${nodeActionLabel(ref)} → ${nodeActionLabel(host)}`
            : `@ 写入提示词 ${nodeActionLabel(ref)} → ${nodeActionLabel(host)}`;
        }
        if (t === "generate_nodes" || t === "generate") {
          const ids = [...new Set(a.node_ids || a.ids || (a.node_id || a.id ? [a.node_id || a.id] : []))];
          if (a.target === "created") return "生成本次新建的节点";
          return `生成 ${ids.length} 个节点：${ids.slice(0, 5).map(nodeActionLabel).join("、")}${ids.length > 5 ? " 等" : ""}`;
        }
        if (t === "replace_node_text") return `局部修改 ${nodeActionLabel(a.id || a.node_id)} 的文字`;
        if (t === "edit_image") return `基于原图修改 ${nodeActionLabel(a.id || a.node_id)}`;
        if (t === "update_node") {
          const id = a.node_id || a.id || "";
          return id ? `更新节点 ${nodeActionLabel(id)}` : "更新节点";
        }
        if (
          t === "set_node_channel" ||
          t === "set_nodes_channel" ||
          t === "set_model" ||
          t === "set_nodes_model"
        ) {
          const ch = a.channel || "";
          const model = a.model || "";
          const scope =
            a.node_type || a.nodeType
              ? `${a.node_type || a.nodeType}节点`
              : a.node_ids?.length || a.ids?.length
                ? `${(a.node_ids || a.ids).length} 个节点`
                : "全部生成节点";
          if (ch && model) return `切换渠道/模型（${scope} → ${ch} / ${model}）`;
          if (ch) return `切换渠道（${scope} → ${ch}）`;
          if (model) return `切换模型（${scope} → ${model}）`;
          return `切换渠道/模型（${scope}）`;
        }
        return t;
      })
      .filter(Boolean);
  }

  function softenPendingReply(reply, actions) {
    const summary = summarizeActions(actions);
    const fallback = summary.length
      ? `准备：${summary.slice(0, 4).join("；")}。请确认后执行。`
      : "准备改动画布，请确认。";
    const r = String(reply || "").trim();
    if (!r) return fallback;
    if (
      /(已经|已为|我已经|正在).{0,28}(创建|更新|改|生成|牵线|加上|建好|完成|触发)/.test(r) ||
      /已(把|将|帮你|为你).{0,24}(创建|更新|改|生成|换成)/.test(r)
    ) {
      return fallback;
    }
    return r;
  }

  const MODEL_ASK_QUEUE = { types: [], channel: "", scope: "all" };

  function _typeLabel(t) {
    if (t === "image") return "图片";
    if (t === "video") return "视频";
    if (t === "text") return "文字";
    return t;
  }

  function _presentGenerativeTypes() {
    const nodes =
      typeof global.AgentOps?.listGenerativeNodes === "function"
        ? global.AgentOps.listGenerativeNodes()
        : (global.CanvasStore?.activeBoard?.()?.nodes || []).filter((n) =>
            ["image", "video", "text", "voice-text"].includes(n?.type)
          );
    const present = [];
    if (nodes.some((n) => n.type === "image")) present.push("image");
    if (nodes.some((n) => n.type === "video")) present.push("video");
    if (nodes.some((n) => n.type === "text" || n.type === "voice-text")) present.push("text");
    return present;
  }

  function _modelsFor(type, channel) {
    const ch =
      channel ||
      (typeof global.resolveNodeChannel === "function"
        ? global.resolveNodeChannel({ type, channel: "myapi" })
        : "myapi");
    let list = [];
    if (type === "image" && typeof global.imageModelsForChannel === "function") {
      list = global.imageModelsForChannel(ch) || [];
    } else if (type === "video" && typeof global.videoModelsForChannel === "function") {
      list = global.videoModelsForChannel(ch) || [];
    } else if (type === "text" && typeof global.textModelsForChannel === "function") {
      list = global.textModelsForChannel(ch) || [];
    }
    return list.slice(0, 12);
  }

  function _buildModelChoices({ type, channel }) {
    const models = _modelsFor(type, channel);
    return models.map((m, i) => ({
      id: `model-${type}-${m.id || i}`,
      label: `${_typeLabel(type)} · ${m.label || m.id}`,
      actions: [
        {
          type: "set_nodes_model",
          node_type: type,
          model: m.id,
          scope: "all",
        },
      ],
    }));
  }

  function buildNextModelAsk() {
    const nextType = MODEL_ASK_QUEUE.types.shift();
    if (!nextType) return null;
    const channel = MODEL_ASK_QUEUE.channel || "";
    const ch =
      channel ||
      (() => {
        const nodes = global.AgentOps?.listGenerativeNodes?.(nextType) || [];
        const n = nodes[0];
        return n && typeof global.resolveNodeChannel === "function"
          ? global.resolveNodeChannel(n)
          : n?.channel || "myapi";
      })();
    const choices = _buildModelChoices({
      type: nextType,
      channel: ch,
    });
    if (!choices.length) {
      return buildNextModelAsk();
    }
    const remain = MODEL_ASK_QUEUE.types.length;
    const chLabel =
      typeof global.getChannelLabel === "function" ? global.getChannelLabel(ch) : ch;
    const reply = `请选择「${_typeLabel(nextType)}」节点模型（当前渠道：${chLabel}）${
      remain ? `；选完后还会问另外 ${remain} 类` : ""
    }。`;
    return {
      ok: true,
      thinking: "按画布实际节点类型逐项询问模型。",
      reply,
      actions: [],
      choices,
      pendingActions: null,
      pendingChoices: choices,
      planSummary: [],
      result: { logs: [], createdIds: [] },
      _modelAskContinue: true,
    };
  }

  function tryLocalChannelModelPlan(userText) {
    const raw = stripMentionMarkers(userText);
    const text = String(raw || "").trim();
    if (!text) return null;

    const wantChannel =
      /换.*渠道|改.*渠道|切换.*渠道|渠道|qudao|bendi|seed\s*s?|myapi|api\s*渠道|渠道\s*api/i.test(
        text
      );
    const wantModel = /换.*模型|改.*模型|切换.*模型|模型换|全部.*模型|所有.*模型/i.test(text);
    if (!wantChannel && !wantModel) return null;

    const role = String(
      global.__canvasUser?.role || global.AppConfig?.user_role || global.AgentOps?.userRole?.() || ""
    ).toLowerCase();
    const canChangeChannel = role === "admin" || role === "internal";

    let targetChannel = "";
    if (/myapi|api\s*渠道|渠道\s*api|qudao\s*api|求道?\s*api/i.test(text)) targetChannel = "myapi";
    else if (/bendi|本地|星晚/i.test(text)) targetChannel = "bendi";
    else if (/seed\s*s|doubao_s|求道|升级/i.test(text)) targetChannel = "doubao_s";
    else if (/seed|doubao|豆包/i.test(text)) targetChannel = "doubao";

    if (typeof global.AgentOps?.normalizeChannelId === "function" && targetChannel) {
      targetChannel = global.AgentOps.normalizeChannelId(targetChannel);
    }

    const present = _presentGenerativeTypes();
    if (!present.length) {
      return {
        ok: true,
        thinking: "画布没有可切换渠道/模型的生成节点。",
        reply: "当前画布没有图片/视频/文字生成节点，没法换渠道或模型。",
        actions: [],
        choices: [],
        pendingActions: null,
        pendingChoices: null,
        result: { logs: [], createdIds: [] },
      };
    }

    // 明确换渠道
    if (wantChannel && targetChannel) {
      if (!canChangeChannel && targetChannel !== "myapi") {
        MODEL_ASK_QUEUE.types = present.slice();
        MODEL_ASK_QUEUE.channel = "";
        const ask = buildNextModelAsk();
        return {
          ...(ask || {}),
          thinking: "平台用户无权切换 Seed/BenDi，改为询问 MyApi 模型。",
          reply:
            `平台用户不能切换到「${
              typeof global.getChannelLabel === "function"
                ? global.getChannelLabel(targetChannel)
                : targetChannel
            }」渠道，只能在 MyApi 下换模型。` + (ask ? `\n${ask.reply}` : ""),
          choices: ask?.choices || [],
          pendingChoices: ask?.choices || [],
        };
      }
      if (typeof global.AgentOps?.channelAllowed === "function") {
        const probeType = present[0] === "text" ? "text" : present[0];
        if (!global.AgentOps.channelAllowed(targetChannel, { type: probeType })) {
          return {
            ok: true,
            thinking: "目标渠道对当前账号不可用。",
            reply: `当前账号用不了这个渠道。可用渠道请看节点上的渠道菜单。`,
            actions: [],
            choices: [],
            pendingActions: null,
            pendingChoices: null,
            result: { logs: [], createdIds: [] },
          };
        }
      }

      // 先切渠道（用默认模型），再逐类问模型
      const switchActions = present.map((t) => ({
        type: "set_nodes_channel",
        node_type: t,
        channel: targetChannel,
        scope: "all",
      }));
      MODEL_ASK_QUEUE.types = present.slice();
      MODEL_ASK_QUEUE.channel = targetChannel;
      const chLabel =
        typeof global.getChannelLabel === "function"
          ? global.getChannelLabel(targetChannel)
          : targetChannel;
      return {
        ok: true,
        thinking: `先切换到 ${targetChannel}，再询问各类模型。`,
        reply: `准备把画布上的生成节点切到「${chLabel}」。确认后我会再分别问图片/视频/文字要哪个模型（没有的类型会跳过）。`,
        actions: switchActions,
        choices: [],
        pendingActions: switchActions,
        pendingChoices: null,
        planSummary: summarizeActions(switchActions),
        result: { logs: [], createdIds: [] },
        _afterConfirmAskModels: true,
      };
    }

    // 只说换渠道但没说哪个 → 问渠道（仅管理员/内部）
    if (wantChannel && !targetChannel && !wantModel) {
      if (!canChangeChannel) {
        MODEL_ASK_QUEUE.types = present.slice();
        MODEL_ASK_QUEUE.channel = "";
        const ask = buildNextModelAsk();
        return {
          ...(ask || {
            ok: true,
            reply: "平台用户只能换模型，不能换渠道。",
            actions: [],
            choices: [],
          }),
          thinking: "平台用户无换渠道权限。",
          reply:
            "平台用户不能切换渠道（Seed / BenDi），只能换模型。" +
            (ask ? `\n${ask.reply}` : ""),
        };
      }
      const opts =
        typeof global.getChannelOptions === "function"
          ? global.getChannelOptions({ type: present[0] || "image" }) || []
          : [];
      if (!opts.length) {
        return {
          ok: true,
          thinking: "",
          reply: "当前没有可选渠道。",
          actions: [],
          choices: [],
          result: { logs: [], createdIds: [] },
        };
      }
      const choices = opts.map((o) => ({
        id: `ch-${o.id}`,
        label: o.label || o.id,
        actions: present.map((t) => ({
          type: "set_nodes_channel",
          node_type: t,
          channel: o.id,
          scope: "all",
        })),
        _askModelsChannel: o.id,
        _askModelsTypes: present.slice(),
      }));
      return {
        ok: true,
        thinking: "先让用户选择渠道。",
        reply: "要换到哪个渠道？选完后我会再问各类模型。",
        actions: [],
        choices,
        pendingActions: null,
        pendingChoices: choices,
        result: { logs: [], createdIds: [] },
      };
    }

    // 换模型 / 全部换模型
    if (wantModel || (wantChannel && !targetChannel)) {
      MODEL_ASK_QUEUE.types = present.slice();
      MODEL_ASK_QUEUE.channel = "";
      return buildNextModelAsk();
    }

    return null;
  }

  function ensureCustomSayChoice(choices) {
    const list = (Array.isArray(choices) ? choices : [])
      .filter((c) => c && String(c.label || "").trim())
      .map((c) => ({
        id: String(c.id || c.label),
        label: String(c.label || c.id).trim(),
        message: String(c.message || c.label || c.id),
        actions: Array.isArray(c.actions) ? c.actions : [],
        _askModelsChannel: c._askModelsChannel,
        _askModelsTypes: c._askModelsTypes,
        _customSay: !!c._customSay,
      }));
    const hasCustom = list.some(
      (c) =>
        c._customSay ||
        c.id === "custom_say" ||
        /^(自己说|我来说|其他|以上都不对)/.test(c.label)
    );
    if (!hasCustom) {
      list.push({
        id: "custom_say",
        label: "自己说",
        actions: [],
        _customSay: true,
      });
    }
    return list;
  }

  function isVagueChoiceLabel(label) {
    const t = String(label || "").trim();
    if (!t) return true;
    return (
      /^(好的[，,]?)?(请)?继续[.。!！…]*$/i.test(t) ||
      /^(先)?不用(了|吧)?[.。!！]*$/i.test(t) ||
      /^(暂时)?(跳过|取消)[.。!！]*$/i.test(t) ||
      /^(好的|是的|对|嗯|随便|都行|确认|否|不了)$/i.test(t)
    );
  }

  function choicesAreMostlyVague(choices) {
    const real = (Array.isArray(choices) ? choices : []).filter(
      (c) => c && !c._customSay && c.id !== "custom_say" && !/自己说/.test(c.label || "")
    );
    if (!real.length) return true;
    const vagueN = real.filter((c) => isVagueChoiceLabel(c.label)).length;
    return vagueN >= Math.ceil(real.length * 0.6);
  }

  // Generation spends points / external resources, so it always gets an
  // explicit confirmation, even when the Agent is in "操作" (auto) mode.
  function requiresGenerationConfirmation(actions) {
    return (Array.isArray(actions) ? actions : []).some((a) => {
      const type = String(a?.type || a?.op || "").trim().toLowerCase();
      return type === "generate" || type === "generate_nodes" || type === "edit_image";
    });
  }

  /**
   * 选择题：只用模型按用户指令动态生成的 choices。
   * 前端绝不本地编菜单；糊选项/固定套话直接丢弃。
   * 若 reply 明确让用户「选」但选项被滤光，按用户原话补一组最小可点选项，避免「请选择」却没按钮。
   */
  function synthesizeAskChoices(reply, actions, choices, userText = "") {
    if (Array.isArray(actions) && actions.length) return [];
    const r = String(reply || "").trim();
    if (!r) return [];

    const real = (Array.isArray(choices) ? choices : []).filter(
      (c) => c && String(c.label || "").trim() && !c._customSay && c.id !== "custom_say"
    );
    return real.length ? ensureCustomSayChoice(real) : [];
  }

  /** 本地/模型套话菜单，一律无效 */
  function isBoilerplateChoiceLabel(label) {
    const t = String(label || "").trim();
    return (
      /^(创建相关节点|按视频编号创建节点|先检查还缺什么|只整理排版|全部重新整理|只整理选中|创建新图|牵线换衣服\s*\/\s*更换|先跳过|检查画布情况|整理排版|只分析不改画布|按我原话完整执行|先说明刚才为什么失败|先不操作了)$/.test(
        t
      ) || /^(好的，)?重新执行$/.test(t)
    );
  }

  async function runIntent(intent, opts = {}) {
    const checkAborted = () => {
      if (opts.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
    };
    checkAborted();
    if (!global.AgentApi?.plan) {
      return {
        ok: false,
        thinking: "",
        reply: "智能体模块未加载，请刷新页面。",
        actions: [],
      };
    }

    const roleMode = opts.roleMode === "ask" ? "ask" : "agent";
    const execMode = opts.execMode === "auto" ? "auto" : "confirm";
    const rawDisplay = String(intent?.payload?.displayText || intent?.payload?.text || "").trim();
    const resolved = resolveContinueToLastUserIntent(rawDisplay, opts.history);
    let baseText = resolved.fromContinue
      ? resolved.text
      : intent?.payload?.text || rawDisplay;
    const text = baseText;
    const board = global.CanvasStore?.activeBoard?.();
    const snapshots = new Map((board?.nodes || []).map((n) => [n.id,
      Object.fromEntries(["type", "prompt", "resultText", "customName", "imageUrl"].map((k) => [k, n[k] ?? null]))]));

    const light =
      opts.light === true ||
      (opts.light !== false &&
        typeof global.AgentApi?.isLightChat === "function" &&
        global.AgentApi.isLightChat(resolved.originalUser || rawDisplay));
    const plan = await global.AgentApi.plan(text, {
      modelId: opts.modelId,
      modelLabel: opts.modelLabel,
      userIntent: resolved.originalUser || rawDisplay,
      roleMode,
      execMode,
      history: opts.history,
      memory: opts.memory,
      signal: opts.signal,
      light,
      boardId: opts.boardId || board?.id,
      onJobId: opts.onJobId,
      onStatus: opts.onStatus,
      canvas: light
        ? null
        : opts.canvas ||
          global.AgentRunner?.buildCanvasContext?.({
            query: resolved.originalUser || rawDisplay,
            mentionedIds: intent?.payload?.mentionedIds,
          }),
    });
    checkAborted();
    if (board?.id !== global.CanvasStore?.activeBoard?.()?.id) {
      return { ok: false, reply: "画板已切换，未执行刚才的计划。请在目标画板重新发送。", actions: [] };
    }

    const stamp = (a) => {
      const action = { ...a, _board_id: board?.id };
      const snapshot = snapshots.get(a.id || a.node_id);
      if (snapshot && ["update_node", "replace_node_text", "edit_image"].includes(a.type)) action._expected = snapshot;
      return action;
    };
    let actions = Array.isArray(plan.actions) ? plan.actions.map(stamp) : [];
    let choices = Array.isArray(plan.choices)
      ? plan.choices
          .filter((c) => c && typeof c === "object" && String(c.label || "").trim())
          .map((c) => ({
            id: String(c.id || c.label),
            label: String(c.label || c.id).trim(),
        message: String(c.message || c.label || c.id),
            actions: Array.isArray(c.actions) ? c.actions.map(stamp) : [],
            _askModelsChannel: c._askModelsChannel,
            _askModelsTypes: c._askModelsTypes,
          }))
      : [];

    // Ask 模式：不改画布，但保留选择题（点选后再用选项文案继续对话）
    if (roleMode === "ask") {
      actions = [];
      choices = choices.map((c) => ({ ...c, actions: [] }));
    }

    // 只读意图：用「用户真实原话」判断（继续时用上一句，不用被改写后的 baseText）
    const userPlain = String(resolved.originalUser || rawDisplay || "").trim();
    if (roleMode === "agent" && isReadOnlyUserText(userPlain)) {
      const before = actions.length;
      actions = stripMutatingActions(actions);
      choices = choices.map((c) => ({
        ...c,
        actions: stripMutatingActions(c.actions),
      }));
      if (before > 0 && actions.length === 0) {
        let cleaned = String(plan.reply || "")
          .replace(/好的[，,]?\s*我将为你创建[\s\S]*$/m, "")
          .replace(/我将为您创建[\s\S]*$/m, "")
          .replace(/我会为你创建[\s\S]*$/m, "")
          .trim();
        if (/将为|创建.{0,12}(节点|参考图)|已按脚本创建/.test(String(plan.reply || ""))) {
          plan.reply =
            (cleaned && !/创建/.test(cleaned) ? `${cleaned}\n\n` : "") +
            "你要的是提取/说明，我没有改画布。下面只回答人物与场景（若上文已分析则保留分析）。若要建参考图节点，请明确说「帮我创建」。";
        } else if (!cleaned) {
          plan.reply =
            "你要的是提取/说明，不是创建节点。请稍等我根据分镜提示词列出人物与场景；若刚才没列出，请再发一次「提取人物和场景」。";
        }
      }
    }

    // 回复里若把原文换行压扁，尽量用画布原文还原（聊天展示不丢段）
    let reply = String(plan.reply || "");
    if (reply && !reply.includes("\n") && typeof global.AgentRunner?.restoreReplyNewlines === "function") {
      reply = global.AgentRunner.restoreReplyNewlines(reply);
    } else if (reply && !reply.includes("\n")) {
      const board = global.CanvasStore?.activeBoard?.();
      const sel = global.CanvasStore?.selectedIds || [];
      for (const id of sel) {
        const n = board?.nodes?.find((x) => x.id === id);
        const body =
          typeof global.getTextNodeBody === "function"
            ? global.getTextNodeBody(n)
            : String(n?.resultText || "");
        if (body && body.includes("\n")) {
          const flat = (s) => String(s || "").replace(/\s+/g, " ").trim();
          const fr = flat(reply);
          const fb = flat(body);
          if (fr && (fb === fr || (fr.length >= 40 && fb.includes(fr)))) {
            reply = body;
            break;
          }
        }
      }
    }

    // Read-only questions still need clarification choices. Never guess empty
    // nodes or override the model's question with an unsolicited generation.
    choices = synthesizeAskChoices(reply, actions, choices, userPlain);
    if (choices.length) actions = [];

    // 人物/场景冲突选择题：无论询问/操作模式都先问用户
    if (choices.length) {
      return {
        ok: true,
        thinking: plan.thinking || "",
        reply: reply || "请选择一种处理方式：",
        actions: [],
        choices,
        pendingActions: null,
        pendingChoices: choices,
        result: { logs: [], createdIds: [] },
      };
    }

    // Ask 模式或无操作：直接返回
    if (!actions.length) {
      let emptyReply = reply || "（无回复）";
      if (
        /(已经|正在).{0,20}(创建|更新|生成|改)|已(把|帮你|为你).{0,16}(创建|更新)/.test(emptyReply)
      ) {
        emptyReply =
          "这轮没有可执行的画布操作。若要改提示词请明确节点；若节点在但没图，请说「生成」或点生成。";
      }
      return {
        ok: true,
        thinking: plan.thinking || "",
        reply: emptyReply,
        actions: [],
        choices: [],
        result: { logs: [], createdIds: [] },
        pendingActions: null,
        pendingChoices: null,
      };
    }

    // 询问模式先确认整份计划；操作模式由执行器在生成前暂停。
    if (execMode === "confirm") {
      actions = global.AgentRunner.prepareConfirmation(actions, board);
      const summary = summarizeActions(actions);
      return {
        ok: true,
        thinking: plan.thinking || "",
        reply: softenPendingReply(reply, actions),
        actions,
        choices: [],
        pendingActions: actions,
        pendingChoices: null,
        planSummary: summary,
        result: { logs: [], createdIds: [] },
      };
    }

    // Agent + 操作：立即执行（结果用卡片展示）
    let exec = { logs: [], createdIds: [] };
    if (global.AgentRunner?.runActions) {
      exec = await global.AgentRunner.runActions(actions, { signal: opts.signal });
    }
    return {
      ok: true,
      thinking: plan.thinking || "",
      reply: exec.pendingActions?.length ? (exec.logs?.length ? "准备操作已处理，请核对生成目标后确认执行。" : "请核对生成目标，确认后开始生成。") : reply || "已处理。",
      actions,
      choices: [],
      pendingActions: exec.pendingActions || null,
      planSummary: exec.pendingActions?.length ? summarizeActions(exec.pendingActions) : null,
      pendingChoices: null,
      result: exec,
      execLogs: exec.logs || [],
    };
  }

  async function executePendingActions(actions, { confirmed = false, signal } = {}) {
    if (!actions?.length || !global.AgentRunner?.runActions) {
      return { logs: [], createdIds: [] };
    }
    return global.AgentRunner.runActions(actions, { signal, generationConfirmed: confirmed });
  }

  function continueModelAskAfterChoice(choice) {
    if (choice?._askModelsTypes?.length) {
      MODEL_ASK_QUEUE.types = choice._askModelsTypes.slice();
      MODEL_ASK_QUEUE.channel = choice._askModelsChannel || "";
    }
    return buildNextModelAsk();
  }

  global.AgentIntent = {
    detectLocalIntent,
    runIntent,
    executePendingActions,
    stripMentionMarkers,
    expandMentionsIntoMessage,
    summarizeActions,
    continueModelAskAfterChoice,
    buildNextModelAsk,
    requiresGenerationConfirmation,
  };
})(typeof window !== "undefined" ? window : globalThis);
