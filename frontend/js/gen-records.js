/**
 * Frontend generation audit panel (relocated from admin 8090).
 * Opened from topbar 「线程」badge / account menu「线程记录」.
 */
(function () {
  const state = {
    page: 1,
    pageSize: 20,
    total: 0,
    totalPages: 1,
    q: "",
    status: "",
    loading: false,
  };

  const ICON_VIEW =
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><circle cx="7" cy="7" r="4" stroke="currentColor" stroke-width="1.2"/><path d="M10.2 10.2L13.5 13.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
  const ICON_COPY =
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.2" stroke="currentColor" stroke-width="1.2"/><path d="M4 10.5H3.2A1.2 1.2 0 0 1 2 9.3V3.2A1.2 1.2 0 0 1 3.2 2h6.1A1.2 1.2 0 0 1 10.5 3.2V4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>';
  const ICON_STOP =
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><rect x="3.5" y="3.5" width="9" height="9" rx="1.4" stroke="currentColor" stroke-width="1.3"/></svg>';

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(msg) {
    if (typeof showCanvasToast === "function") showCanvasToast(msg);
    else console.log(msg);
  }

  function statusLabel(status) {
    return (
      {
        pending: "排队中",
        generating: "生成中",
        completed: "成功",
        failed: "失败",
        cancelled: "已取消",
      }[status] ||
      status ||
      "—"
    );
  }

  function statusClass(status) {
    if (status === "completed") return "on";
    if (status === "generating" || status === "pending") return "is-busy";
    if (status === "failed") return "off";
    return "";
  }

  function isActive(status) {
    return status === "pending" || status === "generating";
  }

  function formatWhen(iso) {
    const s = String(iso || "").trim();
    if (!s) return "—";
    try {
      const d = new Date(s);
      if (Number.isNaN(d.getTime())) return s.slice(0, 19).replace("T", " ");
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return s.slice(0, 19).replace("T", " ");
    }
  }

  function formatModel(r) {
    const modelId = String(r.model || "").trim();
    const accountName = String(r.account_name || "").trim();
    const kind = String(r.kind || "").toLowerCase();
    const channel = String(r.channel || "").toLowerCase();

    let label = "";
    try {
      if (kind === "image" && typeof getImageModelLabel === "function") {
        label = String(getImageModelLabel(modelId) || "").trim();
      } else if (kind === "video" && typeof getVideoModelLabel === "function") {
        label = String(getVideoModelLabel(modelId, channel || undefined) || "").trim();
      } else if ((kind === "text" || kind === "voice-text") && typeof getTextModelLabel === "function") {
        label = String(getTextModelLabel(modelId) || "").trim();
      } else if (kind === "voice" && typeof getVoiceModelLabel === "function") {
        label = String(getVoiceModelLabel(modelId) || "").trim();
      } else if (kind && typeof getNodeModelLabel === "function") {
        label = String(getNodeModelLabel(kind, modelId) || "").trim();
      }
    } catch {
      label = "";
    }
    if (label && label !== "无" && label !== modelId) return label;

    if (typeof getMyApiModelMeta === "function" && modelId) {
      try {
        const meta = getMyApiModelMeta(modelId);
        const n = String(meta?.name || meta?.label || "").trim();
        if (n) return n;
      } catch {
        /* ignore */
      }
    }

    if (accountName && accountName !== modelId && !/^myapi_[a-z0-9]+_/i.test(accountName)) {
      return accountName;
    }
    if (/^myapi_/i.test(modelId)) {
      return accountName && accountName !== modelId ? accountName : "MyApi（模型已删）";
    }
    return modelId || accountName || "—";
  }

  function formatDuration(r) {
    const start = Date.parse(String(r.created_at || ""));
    if (!start || Number.isNaN(start)) return "—";
    if (isActive(r.status)) return "…";
    const endRaw = r.completed_at || r.updated_at;
    const end = Date.parse(String(endRaw || ""));
    if (!end || Number.isNaN(end) || end < start) return "—";
    const sec = Math.max(0, Math.round((end - start) / 1000));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm ? `${h}h ${rm}m` : `${h}h`;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(String(text || ""));
      return true;
    } catch {
      return false;
    }
  }

  function updatePager(data) {
    const total = Number(data?.total) || 0;
    const pageSize = Math.max(1, Number(data?.limit) || state.pageSize);
    const totalPages = Math.max(1, Number(data?.total_pages) || Math.ceil(total / pageSize) || 1);
    const page = total ? Math.min(Math.max(1, Number(data?.page) || 1), totalPages) : 1;
    state.total = total;
    state.totalPages = totalPages;
    state.page = page;
    state.pageSize = pageSize;
    const info = $("gen-records-page-info");
    const prev = $("gen-records-prev");
    const next = $("gen-records-next");
    const pager = $("gen-records-pager");
    if (pager) pager.classList.toggle("hidden", totalPages <= 1);
    if (info) info.textContent = total ? `第 ${page} / ${totalPages} 页` : "第 0 / 0 页";
    if (prev) prev.disabled = page <= 1;
    if (next) next.disabled = page >= totalPages;
  }

  function formatOutboundRequest(req) {
    if (!req || typeof req !== "object") return "";
    const method = String(req.method || "POST").toUpperCase();
    const url = String(req.url || "").trim();
    const lines = [];
    if (url) lines.push(`${method} ${url}`);
    if (req.json != null) {
      try {
        lines.push(JSON.stringify(req.json, null, 2));
      } catch {
        lines.push(String(req.json));
      }
    } else if (req.form && typeof req.form === "object") {
      try {
        lines.push(JSON.stringify(req.form, null, 2));
      } catch {
        lines.push(String(req.form));
      }
    }
    if (Array.isArray(req.files) && req.files.length) {
      lines.push("files: " + JSON.stringify(req.files, null, 2));
    }
    return lines.join("\n").trim();
  }

  function formatPayload(customerError, rawError, rawResponse, record) {
    const parts = [];
    const poll = record
      ? [
          record.upstream_task_id ? `上游任务号: ${record.upstream_task_id}` : "",
          record.upstream_status ? `上游状态: ${record.upstream_status}` : "",
          record.upstream_stage ? `上游阶段: ${record.upstream_stage}` : "",
          record.last_poll_at ? `最后查询: ${record.last_poll_at}` : "",
        ].filter(Boolean)
      : [];
    if (poll.length) parts.push("【轮询状态】\n" + poll.join("\n"));
    const aiOriginal =
      rawResponse && typeof rawResponse === "object"
        ? String(rawResponse.ai_original || rawResponse.last_text || rawResponse.fail_text || "").trim()
        : "";
    if (aiOriginal) parts.push("【AI原文】\n" + aiOriginal);
    const userMsg = String(customerError || "").trim();
    if (userMsg) parts.push("【提示】\n" + userMsg);
    let body = "";
    if (rawResponse != null && rawResponse !== "") {
      try {
        body = typeof rawResponse === "object" ? JSON.stringify(rawResponse, null, 2) : String(rawResponse);
      } catch {
        body = String(rawResponse);
      }
    }
    const raw = String(rawError || "").trim();
    if (body.trim()) parts.push("【原始返回】\n" + body.trim());
    if (raw && raw !== userMsg) parts.push("【原始错误】\n" + raw);
    const outbound = formatOutboundRequest(record?.outbound_request);
    if (outbound) parts.push("【发给服务商的请求】\n" + outbound);
    return parts.join("\n\n").trim() || "（暂无详情）";
  }

  async function openDetail(recordId) {
    const id = String(recordId || "").trim();
    if (!id) return;
    try {
      const res = await fetch(`/api/gen-records/${encodeURIComponent(id)}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "加载失败");
      }
      const data = await res.json();
      const title = $("gen-record-detail-title");
      const meta = $("gen-record-detail-meta");
      const body = $("gen-record-detail-body");
      const viewBtn = $("btn-gen-record-view-request");
      const detailText = formatPayload(data.error, data.raw_error, data.raw_response, data);
      const requestText = formatOutboundRequest(data.outbound_request);
      if (title) title.textContent = `工作编号 ${id}`;
      if (meta) {
        meta.textContent = [
          statusLabel(data.status),
          formatModel(data),
          data.kind || "",
          formatDuration(data),
        ]
          .filter(Boolean)
          .join(" · ");
      }
      const dlg = $("modal-gen-record-detail");
      if (dlg) {
        dlg._detailBodyText = detailText;
        dlg._outboundRequestText = requestText;
      }
      if (body) {
        const preferReq =
          requestText &&
          (data.status === "generating" ||
            data.status === "pending" ||
            data.status === "completed" ||
            !String(detailText || "").trim() ||
            detailText === "（暂无详情）");
        body.textContent = preferReq ? requestText : detailText;
        body.dataset.view = preferReq ? "request" : "detail";
      }
      if (viewBtn) {
        viewBtn.hidden = false;
        viewBtn.disabled = false;
        viewBtn.textContent = body?.dataset.view === "request" ? "查看详情" : "查看请求";
      }
      if (dlg) {
        if (typeof window.revealOverlay === "function") window.revealOverlay(dlg);
        else {
          dlg.showModal();
          dlg.classList.add("is-overlay-open");
        }
      }
    } catch (err) {
      toast(err.message || "加载失败");
    }
  }

  async function forceStop(recordId, btn) {
    const id = String(recordId || "").trim();
    if (!id) return;
    const ok = await AppDialog.confirm(`确定强制中止任务 ${id}？本地任务会立即结束；已提交给服务商的生成可能仍会继续。`, {
      title: "强制中止任务",
      okText: "强制中止",
      danger: true,
    });
    if (!ok) return;
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/gen-records/${encodeURIComponent(id)}/force-stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(result.detail || "强制中止失败");
      if (result.already_done) toast("任务已经结束");
      else if (result.worker_cancelled === false) toast("本地任务已中止，任务服务未返回确认");
      else toast("任务已强制中止");
      await load();
    } catch (err) {
      toast(err.message || "强制中止失败");
      if (btn) btn.disabled = false;
    }
  }

  async function purgeRecords(mode, keep) {
    const tips = {
      failed: { title: "删除失败记录", msg: "确定删除全部「失败」记录？此操作不可撤销。", ok: "删除失败" },
      all: {
        title: "清空全部记录",
        msg: "确定清空全部线程记录？生成中和排队中的任务会保留。此操作不可撤销。",
        ok: "清空全部",
      },
      keep: {
        title: "保留最近记录",
        msg: `确定只保留最近 ${keep} 条记录，删除其余？生成中和排队中的任务会保留。此操作不可撤销。`,
        ok: `保留最近 ${keep}`,
      },
    };
    const tip = tips[mode] || tips.keep;
    const ok = await AppDialog.confirm(tip.msg, {
      title: tip.title,
      okText: tip.ok,
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await fetch("/api/gen-records/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, keep: keep || 0 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "清理失败");
      const deleted = Number(data.deleted) || 0;
      const keptLive = Number(data.kept_live) || 0;
      if (!deleted) {
        toast(keptLive ? "没有可清理的记录（生成中已保留）" : "没有可清理的记录");
      } else {
        toast(
          keptLive
            ? `已清理 ${deleted} 条，剩余 ${data.remaining || 0} 条（含生成中 ${keptLive}）`
            : `已清理 ${deleted} 条，剩余 ${data.remaining || 0} 条`
        );
      }
      state.page = 1;
      await load();
    } catch (err) {
      toast(err.message || "清理失败");
    }
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    const tbody = $("gen-records-tbody");
    const summary = $("gen-records-summary");
    try {
      const offset = (Math.max(1, state.page) - 1) * state.pageSize;
      const q = new URLSearchParams({
        q: state.q || "",
        status: state.status || "",
        limit: String(state.pageSize),
        offset: String(offset),
      });
      const res = await fetch(`/api/gen-records?${q}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      const records = Array.isArray(data.records) ? data.records : [];
      if (!records.length && (data.total || 0) > 0 && state.page > 1) {
        state.page = 1;
        state.loading = false;
        return load();
      }
      const stats = data.stats || {};
      updatePager(data);
      if (summary) {
        const from = offset + 1;
        const to = offset + records.length;
        const range = records.length && data.total ? ` · 当前 ${from}-${to}` : "";
        summary.textContent = `共 ${data.total || 0} 条${range} · 生成中 ${stats.generating || 0} · 成功 ${stats.completed || 0} · 失败 ${stats.failed || 0}`;
      }
      if (!tbody) return;
      if (!records.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="muted-cell gen-records-empty">暂无线程记录</td></tr>`;
        return;
      }
      tbody.innerHTML = records
        .map((r) => {
          const id = String(r.id || "");
          const err = String(r.error || "").trim();
          const hint = err || (r.has_raw ? "有原始返回" : "—");
          const model = formatModel(r);
          const stopBtn = isActive(r.status)
            ? `<button type="button" class="gen-records-icon-btn" data-stop="${esc(id)}" title="强制中止">${ICON_STOP}</button>`
            : "";
          return `<tr>
            <td class="muted-cell">${esc(formatWhen(r.created_at))}</td>
            <td class="muted-cell"><code title="${esc(id)}">${esc(id ? id.slice(0, 12) : "—")}</code></td>
            <td class="gen-records-model" title="${esc(model)}">${esc(model)}</td>
            <td>${esc(r.kind || "—")}</td>
            <td><span class="tag gen-records-status ${statusClass(r.status)}">${esc(statusLabel(r.status))}</span></td>
            <td class="muted-cell gen-records-duration">${esc(formatDuration(r))}</td>
            <td class="muted-cell gen-records-hint"><span title="${esc(hint)}">${esc(hint.length > 36 ? hint.slice(0, 36) + "…" : hint)}</span></td>
            <td>
              <div class="gen-records-actions">
                ${stopBtn}
                <button type="button" class="gen-records-icon-btn" data-view="${esc(id)}" title="查看详情">${ICON_VIEW}</button>
                <button type="button" class="gen-records-icon-btn" data-copy="${esc(id)}" title="复制编号">${ICON_COPY}</button>
              </div>
            </td>
          </tr>`;
        })
        .join("");
      tbody.querySelectorAll("[data-stop]").forEach((btn) => {
        btn.addEventListener("click", () => void forceStop(btn.dataset.stop, btn));
      });
      tbody.querySelectorAll("[data-view]").forEach((btn) => {
        btn.addEventListener("click", () => void openDetail(btn.dataset.view));
      });
      tbody.querySelectorAll("[data-copy]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          toast((await copyText(btn.dataset.copy || "")) ? "已复制编号" : "复制失败");
        });
      });
    } catch (err) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="muted-cell">${esc(err.message || "加载失败")}</td></tr>`;
      if (summary) summary.textContent = err.message || "加载失败";
    } finally {
      state.loading = false;
    }
  }

  function open() {
    const dlg = $("modal-gen-records");
    if (!dlg) {
      toast("线程记录面板未就绪");
      return;
    }
    window.CanvasAuth?.closeAllAccountPops?.();
    if (typeof window.refreshThreadSettingsUI === "function") {
      try {
        window.refreshThreadSettingsUI();
      } catch {
        /* ignore */
      }
    }
    if (typeof window.WorkspaceApiPolicy !== "undefined") {
      try {
        window.WorkspaceApiPolicy.syncUi();
        void window.WorkspaceApiPolicy.loadFromServer();
      } catch {
        /* ignore */
      }
    }
    if (typeof window.revealOverlay === "function") window.revealOverlay(dlg);
    else {
      dlg.showModal();
      dlg.classList.add("is-overlay-open");
    }
    void load();
  }

  function close() {
    const dlg = $("modal-gen-records");
    if (!dlg) return;
    if (typeof window.dismissOverlay === "function") window.dismissOverlay(dlg);
    else {
      dlg.close();
      dlg.classList.remove("is-overlay-open");
    }
  }

  function bind() {
    $("btn-gen-records-refresh")?.addEventListener("click", () => void load());
    $("btn-gen-records-close")?.addEventListener("click", () => close());
    $("gen-records-prev")?.addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        void load();
      }
    });
    $("gen-records-next")?.addEventListener("click", () => {
      if (state.page < state.totalPages) {
        state.page += 1;
        void load();
      }
    });
    $("gen-records-search")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        state.q = e.target.value.trim();
        state.page = 1;
        void load();
      }
    });
    let searchTimer = 0;
    $("gen-records-search")?.addEventListener("input", (e) => {
      const next = e.target.value.trim();
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        if (state.q === next) return;
        state.q = next;
        state.page = 1;
        void load();
      }, 280);
    });
    $("gen-records-status-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-gen-status]");
      if (!btn) return;
      $("gen-records-status-tabs")
        ?.querySelectorAll("[data-gen-status]")
        .forEach((b) => b.classList.toggle("is-active", b === btn));
      state.status = btn.getAttribute("data-gen-status") || "";
      state.page = 1;
      void load();
    });
    document.querySelector(".gen-records-clear-bar")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-purge], [data-purge-keep]");
      if (!btn) return;
      const keep = Number(btn.getAttribute("data-purge-keep") || 0);
      if (keep > 0) {
        void purgeRecords("keep", keep);
        return;
      }
      const mode = btn.getAttribute("data-purge") || "";
      if (mode) void purgeRecords(mode, 0);
    });
    document.querySelectorAll(".js-open-gen-records, #btn-workspace-gen-records").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        window.CanvasAuth?.closeAllAccountPops?.();
        requestAnimationFrame(() => {
          try {
            open();
          } catch (err) {
            console.warn("openGenRecords", err);
            if (typeof showCanvasToast === "function") showCanvasToast("无法打开线程记录");
          }
        });
      });
    });
    const closeDetail = () => {
      const dlg = $("modal-gen-record-detail");
      if (!dlg) return;
      if (typeof window.dismissOverlay === "function") window.dismissOverlay(dlg);
      else dlg.close();
    };
    $("btn-gen-record-detail-close")?.addEventListener("click", closeDetail);
    $("btn-gen-record-detail-close-foot")?.addEventListener("click", closeDetail);
    $("btn-gen-record-view-request")?.addEventListener("click", () => {
      const dlg = $("modal-gen-record-detail");
      const body = $("gen-record-detail-body");
      const btn = $("btn-gen-record-view-request");
      if (!dlg || !body) return;
      const req = String(dlg._outboundRequestText || "").trim();
      if (!req) {
        toast("本次没有保存到发出去的请求（旧任务或未走到上游）");
        return;
      }
      const showingReq = body.dataset.view === "request";
      if (showingReq) {
        body.textContent = dlg._detailBodyText || "";
        body.dataset.view = "detail";
        if (btn) btn.textContent = "查看请求";
      } else {
        body.textContent = req;
        body.dataset.view = "request";
        if (btn) btn.textContent = "查看详情";
      }
    });
  }

  window.GenRecordsPanel = { open, close, refresh: load };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
