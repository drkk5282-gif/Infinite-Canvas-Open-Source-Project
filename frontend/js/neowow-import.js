/**
 * Paste import: Neowow workflow JSON, multiple media URLs, multi-file clipboard.
 */
(function () {
  const NEOWOW_HOST_RE = /neoai-online\.neodomain\.cn/i;
  const IMAGE_EXT_RE = /\.(jpe?g|png|gif|webp|bmp|svg)(\?|$)/i;
  const VIDEO_EXT_RE = /\.(mp4|webm|mov|m4v|mkv|avi)(\?|$)/i;
  const AUDIO_EXT_RE = /\.(mp3|wav|m4a|aac|ogg|flac)(\?|$)/i;
  const URL_IN_TEXT_RE = /https?:\/\/[^\s<>"')\]]+/gi;

  function mediaKindFromUrl(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const path = raw.split("?")[0].toLowerCase();
    if (path.includes("/workflow/images/") || path.includes("/uploads/image/") || IMAGE_EXT_RE.test(path)) {
      return "image";
    }
    if (path.includes("/workflow/videos/") || path.includes("/video/") || VIDEO_EXT_RE.test(path)) {
      return "video";
    }
    if (AUDIO_EXT_RE.test(path)) return "audio";
    return "";
  }

  function uniqueUrls(list) {
    const out = [];
    const seen = new Set();
    for (const item of list || []) {
      const url = String(item || "").trim().replace(/[),.;]+$/, "");
      if (!url || seen.has(url)) continue;
      seen.add(url);
      out.push(url);
    }
    return out;
  }

  function extractMediaUrls(text) {
    const matches = String(text || "").match(URL_IN_TEXT_RE) || [];
    return uniqueUrls(matches.filter((u) => mediaKindFromUrl(u)));
  }

  function urlToSimpleNode(url) {
    const kind = mediaKindFromUrl(url);
    const name = decodeURIComponent(url.split("/").pop()?.split("?")[0] || "素材");
    if (kind === "image") {
      return {
        type: "image",
        data: { type: "upload", inputImageUrls: [url], fileName: name, label: name },
      };
    }
    if (kind === "video") {
      return {
        type: "video",
        data: { type: "upscale-video", isToolNode: true, inputVideoUrls: [url], label: name },
      };
    }
    if (kind === "audio") {
      return {
        type: "audio",
        data: { type: "upload", connectedAudioUrls: [url], label: name },
      };
    }
    return null;
  }

  function parseClipboardMediaPayload(text) {
    const raw = String(text || "").trim();
    if (!raw) return null;

    if (raw.startsWith("{") && raw.includes('"workflow-nodes"')) {
      try {
        const data = JSON.parse(raw);
        if (data?.type === "workflow-nodes" && Array.isArray(data.nodes) && data.nodes.length) {
          return data;
        }
      } catch {
        /* fall through */
      }
    }

    const urls = extractMediaUrls(raw);
    if (!urls.length) return null;
    const nodes = urls.map(urlToSimpleNode).filter(Boolean);
    if (!nodes.length) return null;
    return { type: "workflow-nodes", nodes };
  }

  function collectUrlList(data, key) {
    const val = data?.[key];
    if (!val) return [];
    return uniqueUrls(Array.isArray(val) ? val : [val]);
  }

  function pickResultUrl(data) {
    const result = data?.result;
    if (typeof result === "string" && result.startsWith("http")) return result;
    if (result && typeof result === "object") {
      const u = result.url || result.videoUrl || result.video_url;
      if (typeof u === "string" && u.startsWith("http")) return u;
    }
    for (const key of ["outputVideoUrl", "videoUrl", "resultVideoUrl", "outputUrl"]) {
      const u = data?.[key];
      if (typeof u === "string" && u.startsWith("http")) return u;
    }
    return "";
  }

  function pickPrimaryVideoUrl(node) {
    const data = node?.data || {};
    if (data.type === "upscale-video" || data.isToolNode) {
      return collectUrlList(data, "inputVideoUrls")[0] || "";
    }
    const result = pickResultUrl(data);
    if (result) return result;
    const inputs = collectUrlList(data, "inputVideoUrls");
    if (inputs.length === 1) return inputs[0];
    if (data.status === "SUCCESS" && inputs[0]) return inputs[0];
    return inputs[0] || "";
  }

  function collectMaterialUrls(data, exclude) {
    const skip = exclude || new Set();
    const nameMap = data?.connectedNameMap || {};
    const items = [];
    const push = (url) => {
      if (!url || skip.has(url) || items.some((x) => x.url === url)) return;
      const kind = mediaKindFromUrl(url);
      if (!kind) return;
      items.push({ url, kind, name: String(nameMap[url] || "").trim() });
    };
    for (const key of [
      "connectedImageUrls",
      "connectedVideoUrls",
      "connectedAudioUrls",
      "inputImageUrls",
    ]) {
      for (const url of collectUrlList(data, key)) push(url);
    }
    return items;
  }

  function classifyNeowowNode(node) {
    const data = node?.data || {};
    const type = String(node?.type || "").toLowerCase();
    const dataType = String(data.type || "").toLowerCase();

    if (type === "audio" || dataType === "upload") {
      const audio = collectUrlList(data, "connectedAudioUrls")[0];
      if (audio) {
        return {
          kind: "ref-audio",
          audioUrl: audio,
          name: String(data.label || data.fileName || "音频").trim() || "音频",
          prompt: "",
          materials: [],
        };
      }
    }

    if (dataType === "upscale-video" || data.isToolNode) {
      const url = pickPrimaryVideoUrl(node);
      return {
        kind: "ref-video",
        videoUrl: url,
        name: String(data.label || data.modelName || "视频").trim() || "视频",
        prompt: "",
        materials: [],
      };
    }

    if (type === "image" || dataType === "upload") {
      const url = collectUrlList(data, "inputImageUrls")[0] || pickResultUrl(data);
      return {
        kind: "ref-image",
        imageUrl: url,
        name: String(data.fileName || data.label || "图片").trim() || "图片",
        prompt: "",
        materials: [],
      };
    }

    if (type === "video") {
      const primary = pickPrimaryVideoUrl(node);
      const exclude = new Set(primary ? [primary] : []);
      const materials = collectMaterialUrls(data, exclude);
      const prompt = String(data.prompt || "").trim();
      return {
        kind: "video-gen",
        videoUrl: primary,
        name: String(data.label || data.modelName || "视频生成").trim() || "视频生成",
        prompt,
        ratio: String(data.universalAspectRatio || data.aspectRatio || "16:9"),
        duration: Number(data.universalDuration || data.duration || 0) || null,
        resolution: String(data.universalResolution || data.resolution || ""),
        materials,
      };
    }

    return null;
  }

  function shouldMirrorUrl(url) {
    const u = String(url || "").trim();
    if (!u.startsWith("http")) return false;
    if (u.startsWith("/uploads/")) return false;
    try {
      const parsed = new URL(u, window.location.origin);
      if (parsed.origin === window.location.origin && parsed.pathname.startsWith("/uploads/")) {
        return false;
      }
    } catch {
      /* ignore */
    }
    return true;
  }

  async function mirrorUrl(url) {
    const boardId = window.CanvasStore?.activeBoardId || "";
    const res = await fetch("/api/import-url", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(window.CanvasAuth?.headers?.() || {}) },
      body: JSON.stringify({ url, board_id: boardId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : "下载素材失败");
    }
    return res.json();
  }

  function placeRefNode(kind, x, y, patch) {
    const node = window.CanvasStore.addNode(kind, x, y);
    if (!node) throw new Error("当前画板不可编辑");
    window.CanvasStore.updateNode(node.id, {
      status: "completed",
      ...patch,
    });
    return node;
  }

  async function createRefFromUrl(kind, url, x, y, name, mirror) {
    let mediaUrl = url;
    let width = 0;
    let height = 0;
    let thumbUrl = "";
    let coverUrl = "";
    if (mirror && shouldMirrorUrl(url)) {
      const saved = await mirrorUrl(url);
      mediaUrl = saved.url;
      width = saved.width || 0;
      height = saved.height || 0;
      thumbUrl = saved.thumb_url || "";
      coverUrl = saved.cover_url || "";
    }
    const patch = {
      customName: name || undefined,
      mediaWidth: width || null,
      mediaHeight: height || null,
      thumbUrl: thumbUrl || null,
      coverUrl: coverUrl || null,
    };
    if (kind === "ref-image") patch.imageUrl = mediaUrl;
    else if (kind === "ref-video") patch.videoUrl = mediaUrl;
    else patch.audioUrl = mediaUrl;
    return placeRefNode(kind, x, y, patch);
  }

  function showToast(msg) {
    if (typeof showCanvasToast === "function") showCanvasToast(msg);
  }

  function gridSlots(count, x, y, cellW, cellH) {
    if (typeof window.layoutImportGridSlots === "function") {
      return window.layoutImportGridSlots(count, x, y, { cellW, cellH, gap: 28 });
    }
    return Array.from({ length: count }, (_, i) => ({ x: x + i * 36, y: y + i * 36 }));
  }

  async function importVideoGeneration(spec, worldX, worldY, withMaterials) {
    const materialNodes = [];
    /** Primary result stays on the video-gen node (prompt + result), not a separate ref. */
    let resultUrl = "";
    let resultMeta = { width: 0, height: 0, thumbUrl: "", coverUrl: "" };

    if (spec.videoUrl) {
      resultUrl = spec.videoUrl;
      if (shouldMirrorUrl(spec.videoUrl)) {
        const saved = await mirrorUrl(spec.videoUrl);
        resultUrl = saved.url;
        resultMeta = {
          width: saved.width || 0,
          height: saved.height || 0,
          thumbUrl: saved.thumb_url || "",
          coverUrl: saved.cover_url || "",
        };
      }
    }

    if (withMaterials && spec.materials?.length) {
      const matSlots = gridSlots(spec.materials.length, worldX - 360, worldY - 120, 280, 180);
      for (let i = 0; i < spec.materials.length; i++) {
        const item = spec.materials[i];
        const slot = matSlots[i] || { x: worldX - 360, y: worldY };
        const kind =
          item.kind === "video" ? "ref-video" : item.kind === "audio" ? "ref-audio" : "ref-image";
        const ref = await createRefFromUrl(
          kind,
          item.url,
          slot.x,
          slot.y,
          item.name || "素材",
          true
        );
        materialNodes.push(ref);
      }
    }

    const videoNode = window.CanvasStore.addNode("video", worldX, worldY);
    if (!videoNode) throw new Error("当前画板不可编辑");
    const patch = {
      customName: spec.name || "视频生成",
      prompt: spec.prompt || "",
      channel: "myapi",
      status: resultUrl ? "completed" : "idle",
    };
    if (resultUrl) {
      patch.videoUrl = resultUrl;
      patch.mediaWidth = resultMeta.width || null;
      patch.mediaHeight = resultMeta.height || null;
      patch.thumbUrl = resultMeta.thumbUrl || null;
      patch.coverUrl = resultMeta.coverUrl || null;
    }
    if (spec.ratio) patch.ratio = spec.ratio;
    if (spec.duration) patch.duration = spec.duration;
    if (spec.resolution && spec.resolution !== "自动") patch.resolution = spec.resolution;
    window.CanvasStore.updateNode(videoNode.id, patch);

    for (const ref of materialNodes) {
      window.CanvasStore.addConnection(ref.id, videoNode.id);
      if (typeof softAddReference === "function") {
        softAddReference(videoNode.id, ref.id, { silent: true });
      }
    }

    return { id: videoNode.id, nodes: [videoNode, ...materialNodes] };
  }

  async function importClassifiedNode(spec, worldX, worldY, withMaterials) {
    if (spec.kind === "ref-image") {
      if (!spec.imageUrl) throw new Error("未找到图片链接");
      return createRefFromUrl("ref-image", spec.imageUrl, worldX, worldY, spec.name, true);
    }
    if (spec.kind === "ref-video") {
      if (!spec.videoUrl) throw new Error("未找到视频链接");
      return createRefFromUrl("ref-video", spec.videoUrl, worldX, worldY, spec.name, true);
    }
    if (spec.kind === "ref-audio") {
      if (!spec.audioUrl) throw new Error("未找到音频链接");
      return createRefFromUrl("ref-audio", spec.audioUrl, worldX, worldY, spec.name, true);
    }
    if (spec.kind === "video-gen") {
      return importVideoGeneration(spec, worldX, worldY, withMaterials);
    }
    return null;
  }

  function getEditableBoard() {
    const store = window.CanvasStore;
    if (!store) return null;
    const board =
      typeof store.activeBoard === "function"
        ? store.activeBoard()
        : store.boards?.find((b) => b.id === store.activeBoardId) || store.boards?.[0];
    if (!board) return null;
    if (board.isOwned === false) return null;
    return board;
  }

  async function tryImportNeowowClipboard(text, worldPos) {
    const payload = parseClipboardMediaPayload(text);
    if (!payload?.nodes?.length) return false;
    if (!getEditableBoard()) {
      showToast("请先打开自己的画板后再粘贴");
      return true;
    }

    const entries = [];
    for (const node of payload.nodes) {
      const spec = classifyNeowowNode(node);
      if (!spec) continue;
      if (!spec.videoUrl && !spec.imageUrl && !spec.audioUrl && spec.kind === "video-gen" && !spec.prompt) {
        continue;
      }
      entries.push({ node, spec });
    }
    if (!entries.length) return false;

    if (typeof CanvasHistory !== "undefined") CanvasHistory.push();

    const anchorX = worldPos?.x ?? 0;
    const anchorY = worldPos?.y ?? 0;

    const materialCount = entries.reduce(
      (sum, e) => sum + (e.spec.kind === "video-gen" ? e.spec.materials?.length || 0 : 0),
      0
    );
    let withMaterials = false;
    if (materialCount > 0) {
      withMaterials = await window.AppDialog.confirm(
        `检测到 ${materialCount} 个关联素材。\n\n是否一并导入并连线到视频节点？\n（会下载保存到本服务器）`,
        {
          title: "导入关联素材",
          okText: "导入素材并连线",
          cancelText: "只要成片+提示词",
        }
      );
    }

    const simpleRefs = entries.filter((e) => e.spec.kind !== "video-gen");
    const videoGens = entries.filter((e) => e.spec.kind === "video-gen");
    const slots = gridSlots(
      entries.length,
      anchorX,
      anchorY,
      videoGens.length ? 520 : 300,
      videoGens.length ? 320 : 220
    );

    try {
      let imported = 0;
      const createdIds = [];
      if (simpleRefs.length > 1) {
        showToast(`正在导入 ${simpleRefs.length} 个素材…`);
      }
      for (let i = 0; i < entries.length; i++) {
        const { spec } = entries[i];
        const slot = slots[i] || { x: anchorX, y: anchorY };
        const result = await importClassifiedNode(
          spec,
          slot.x,
          slot.y,
          spec.kind === "video-gen" ? withMaterials : false
        );
        if (result) {
          imported += 1;
          if (Array.isArray(result.nodes) && result.nodes.length) {
            for (const n of result.nodes) if (n?.id) createdIds.push(n.id);
          } else {
            const id = result.id || result.nodeId || (typeof result === "string" ? result : null);
            if (id) createdIds.push(id);
          }
        }
      }

      await window.CanvasStore.flushSave?.();
      if (typeof renderNodes === "function") renderNodes({ immediate: true, force: true });
      if (typeof scheduleRenderConnections === "function") scheduleRenderConnections(true);
      if (typeof updateNodeCount === "function") updateNodeCount();
      if (typeof updateCanvasHint === "function") updateCanvasHint();
      if (createdIds.length === 1 && typeof window.selectSingle === "function") {
        window.selectSingle(createdIds[0]);
      } else if (createdIds.length > 1 && typeof window.setSelection === "function") {
        window.setSelection(createdIds, createdIds[0]);
      }
      showToast(`已粘贴 ${imported} 项到画布`);
      return true;
    } catch (err) {
      showToast(err?.message || "粘贴导入失败");
      return true;
    }
  }

  window.tryImportNeowowClipboard = tryImportNeowowClipboard;
})();
