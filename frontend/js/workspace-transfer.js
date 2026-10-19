/* Share a project with its media, or import several portable ZIPs. */
(() => {
  const API = "/api/workspace/packages";
  const label = (kind) => kind === "canvas" ? "画布" : "工作台";
  const bytes = (value) => {
    let n = Number(value) || 0;
    const units = ["B", "KB", "MB", "GB"];
    let unit = 0;
    while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit += 1; }
    return `${n.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };
  async function request(path, options = {}) {
    const headers = new Headers(options.headers);
    const token = window.CanvasAuth?.getToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${API}${path}`, { ...options, headers, credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`);
    return data;
  }
  const post = (path, body) => request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  function makeDialog(id, title, content, confirmText) {
    const dialog = document.createElement("dialog");
    dialog.id = id;
    dialog.className = "modal-dialog workspace-transfer-dialog";
    dialog.setAttribute("aria-labelledby", `${id}-title`);
    dialog.innerHTML = `<div class="workspace-transfer-card">
      <div class="workspace-transfer-head"><h3 id="${id}-title">${title}</h3><button type="button" class="icon-btn" data-transfer-close aria-label="关闭">×</button></div>
      ${content}
      <p class="workspace-transfer-status" role="status" aria-live="polite"></p>
      <div class="modal-actions"><button type="button" class="btn btn-ghost" data-transfer-close>取消</button><button type="button" class="btn btn-primary" data-transfer-confirm disabled>${confirmText}</button></div>
    </div>`;
    document.body.append(dialog);
    return dialog;
  }
  function open(dialog) {
    if (typeof revealOverlay === "function") revealOverlay(dialog);
    else if (!dialog.open) dialog.showModal();
  }
  function close(dialog) {
    if (typeof dismissOverlay === "function") dismissOverlay(dialog);
    else dialog.close();
  }
  function setBusy(dialog, busy) {
    dialog.dataset.busy = busy ? "1" : "0";
    dialog.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  }
  function message(dialog, text, error = false) {
    const status = dialog.querySelector(".workspace-transfer-status");
    status.textContent = text;
    status.classList.toggle("is-error", error);
  }
  const Transfer = {
    exportDialog: null,
    importDialog: null,
    exportTarget: null,
    importToken: "",
    importFiles: [],
    init() {
      if (this.importDialog) return;
      this.exportDialog = makeDialog("modal-share-workspace", "分享", '<p class="workspace-transfer-intro">将项目内容和素材打包为 ZIP，可在其他电脑导入继续编辑。</p><div class="workspace-transfer-summary"></div>', "导出压缩包");
      this.importDialog = makeDialog("modal-import-workspace", "导入数据", `<p class="workspace-transfer-intro">请选择 ZIP 分享包（与小庆子导出格式相同）。</p>
        <div class="workspace-transfer-pickers"><button type="button" class="btn btn-ghost" data-pick-files>请选择文件（可多选）</button><button type="button" class="btn btn-ghost" data-pick-directory>请选择目录</button></div>
        <input type="file" accept=".zip,application/zip" multiple hidden data-transfer-files />
        <input type="file" webkitdirectory multiple hidden data-transfer-directory />
        <p class="workspace-transfer-count">尚未选择文件</p><div class="workspace-transfer-list"></div>
        <p class="workspace-transfer-intro">导入后添加为新画板，保留现有项目。</p>`, "确认导入");
      for (const dialog of [this.exportDialog, this.importDialog]) {
        const dismiss = () => {
          if (dialog.dataset.busy === "1") return;
          if (dialog === this.importDialog) void this.discard();
          close(dialog);
        };
        dialog.querySelectorAll("[data-transfer-close]").forEach((button) => button.addEventListener("click", dismiss));
        dialog.addEventListener("cancel", (event) => { event.preventDefault(); dismiss(); });
      }
      this.exportDialog.querySelector("[data-transfer-confirm]").addEventListener("click", () => void this.export());
      this.importDialog.querySelector("[data-transfer-confirm]").addEventListener("click", () => void this.import());
      const pick = (selector) => this.importDialog.querySelector(selector).click();
      this.importDialog.querySelector("[data-pick-files]").addEventListener("click", () => pick("[data-transfer-files]"));
      this.importDialog.querySelector("[data-pick-directory]").addEventListener("click", () => pick("[data-transfer-directory]"));
      for (const selector of ["[data-transfer-files]", "[data-transfer-directory]"]) {
        this.importDialog.querySelector(selector).addEventListener("change", (event) => {
          const files = [...event.target.files];
          event.target.value = "";
          if (files.length) void this.select(files.filter((file) => /\.zip$/i.test(file.name)));
        });
      }
      document.getElementById("btn-workspace-import-data")?.addEventListener("click", () => {
        this.importDialog.querySelector("[data-transfer-confirm]").disabled = !this.importToken;
        open(this.importDialog);
      });
      document.getElementById("btn-workspace-export-data")?.addEventListener("click", () => void this.pickExport());
    },
    async pickExport() {
      this.init();
      const boards = (window.CanvasStore?.sortedBoards?.() || window.CanvasStore?.boards || [])
        .filter((board) => board && board.id && board.isOwned !== false);
      if (!boards.length) {
        if (typeof showCanvasToast === "function") showCanvasToast("还没有可导出的画板");
        return;
      }
      if (boards.length === 1) {
        await this.share("canvas", boards[0].id);
        return;
      }
      const dialog = this.exportDialog;
      if (!dialog || dialog.dataset.busy === "1") return;
      this.exportTarget = null;
      dialog.querySelector("h3").textContent = "导出画布";
      const summary = dialog.querySelector(".workspace-transfer-summary");
      summary.replaceChildren();
      const intro = document.createElement("p");
      intro.textContent = "选择要导出的画板，格式与小庆子分享包相同。";
      summary.append(intro);
      for (const board of boards) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-ghost workspace-transfer-board";
        button.textContent = board.name || "未命名画板";
        button.addEventListener("click", () => void this.share("canvas", board.id));
        summary.append(button);
      }
      dialog.querySelector("[data-transfer-confirm]").disabled = true;
      message(dialog, "");
      open(dialog);
    },
    async share(kind, id) {
      this.init();
      const dialog = this.exportDialog;
      if (!dialog || dialog.dataset.busy === "1") return;
      this.exportTarget = { kind, id };
      dialog.querySelector("h3").textContent = `分享${label(kind)}`;
      dialog.querySelector(".workspace-transfer-summary").replaceChildren();
      open(dialog);
      setBusy(dialog, true);
      message(dialog, "正在统计项目数据…");
      try {
        const store = kind === "canvas" ? window.CanvasStore : window.WorkbenchStore;
        await store?.persistence?.().flush?.();
        const summary = await request(`/export-preview?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`);
        const rows = [summary.name,
          kind === "canvas" ? `${summary.node_count} 个节点` : `${summary.asset_count} 个公共资产 · ${summary.episode_count} 集 · ${summary.clip_count} 个片段`,
          `${summary.file_count} 个素材文件 · 本地素材 ${bytes(summary.local_bytes)}`,
          `预计压缩包大小：${summary.remote_count ? "至少 " : "约 "}${bytes(summary.estimated_zip_bytes)}`];
        if (summary.remote_count) rows.push(`${summary.remote_count} 个云端素材将在导出时下载，最终大小会增加。`);
        for (const text of rows) {
          const row = document.createElement("p"); row.textContent = text;
          dialog.querySelector(".workspace-transfer-summary").append(row);
        }
        const missing = summary.missing_files?.length || 0;
        message(dialog, missing ? `有 ${missing} 个素材丢失，请先恢复后再导出。` : "已保存的布局、提示词、引用和历史记录将随素材一起导出。", !!missing);
        setBusy(dialog, false);
        dialog.querySelector("[data-transfer-confirm]").disabled = !!missing;
      } catch (error) {
        setBusy(dialog, false);
        dialog.querySelector("[data-transfer-confirm]").disabled = true;
        message(dialog, error.message, true);
      }
    },
    async export() {
      const dialog = this.exportDialog;
      if (dialog.dataset.busy === "1" || !this.exportTarget) return;
      setBusy(dialog, true);
      message(dialog, "正在打包素材…云端素材较多时需要等待下载完成。");
      try {
        const result = await post("/export", this.exportTarget);
        // The browser downloads the file directly; large ZIPs never become a
        // multi-gigabyte JS Blob in the page's memory.
        const link = document.createElement("a");
        link.href = result.download_url;
        link.download = result.filename;
        document.body.append(link); link.click(); link.remove();
        message(dialog, `压缩包已生成（${bytes(result.bytes)}），已开始下载。`);
      } catch (error) { message(dialog, error.message, true); }
      finally { setBusy(dialog, false); }
    },
    async discard() {
      const token = this.importToken;
      this.importToken = "";
      this.importFiles = [];
      this.importDialog?.querySelector(".workspace-transfer-list").replaceChildren();
      if (this.importDialog) {
        this.importDialog.querySelector(".workspace-transfer-count").textContent = "尚未选择文件";
        this.importDialog.querySelector("[data-transfer-confirm]").disabled = true;
        message(this.importDialog, "");
      }
      if (token) await request(`/import-preview/${encodeURIComponent(token)}`, { method: "DELETE" }).catch(() => {});
    },
    async select(files) {
      const dialog = this.importDialog;
      if (dialog.dataset.busy === "1") return;
      setBusy(dialog, true);
      await this.discard();
      if (!files.length || files.length > 20) {
        message(dialog, files.length ? "每批最多选择 20 个 ZIP 分享包。" : "所选目录中没有 ZIP 分享包。", true);
        setBusy(dialog, false);
        dialog.querySelector("[data-transfer-confirm]").disabled = true;
        return;
      }
      const count = dialog.querySelector(".workspace-transfer-count");
      count.textContent = `已选择 ${files.length} 个文件 · ${bytes(files.reduce((sum, file) => sum + file.size, 0))}`;
      message(dialog, "正在读取并检查分享包…");
      const form = new FormData();
      for (const file of files) form.append("files", file, file.name);
      try {
        const result = await request("/import-preview", { method: "POST", body: form });
        this.importToken = result.token;
        this.importFiles = result.files;
        for (const file of result.files) {
          const row = document.createElement("div"); row.className = "workspace-transfer-file";
          const title = document.createElement("strong"); title.textContent = `${label(file.kind)} · ${file.name}`;
          const detail = document.createElement("span"); detail.textContent = `${file.filename} · ${file.file_count} 个素材 · ${bytes(file.bytes)}`;
          row.append(title, detail); dialog.querySelector(".workspace-transfer-list").append(row);
        }
        const boards = result.files.filter((file) => file.kind === "canvas").length;
        const benches = result.files.length - boards;
        message(dialog, benches
          ? `已选择 ${result.files.length} 个文件，是否导入？将添加 ${boards} 个画布、${benches} 个工作台。`
          : `已选择 ${result.files.length} 个文件，是否导入？将添加 ${boards} 个画布。`);
      } catch (error) { message(dialog, error.message, true); }
      finally {
        setBusy(dialog, false);
        dialog.querySelector("[data-transfer-confirm]").disabled = !this.importToken;
      }
    },
    async import() {
      const dialog = this.importDialog;
      if (dialog.dataset.busy === "1" || !this.importToken) return;
      const token = this.importToken;
      setBusy(dialog, true);
      message(dialog, "正在校验素材并导入…");
      try {
        // Persist any pending edits before reloading the indexes.
        await Promise.all([window.CanvasStore?.persistence?.().flush?.(), window.WorkbenchStore?.persistence?.().flush?.()]);
        const result = await post("/import", { token });
        this.importToken = "";
        await Promise.all([window.CanvasStore?.loadFromServer?.({ force: true }), window.WorkbenchStore?.loadFromServer?.({ force: true })]);
        if (typeof WorkspaceHome !== "undefined") WorkspaceHome.render();
        if (typeof WorkbenchHome !== "undefined") WorkbenchHome.render();
        void window.WorkspaceStorageStats?.refresh?.({ force: true });
        const done = result.workbenches
          ? `导入完成：${result.boards} 个画布、${result.workbenches} 个工作台。`
          : `导入完成：${result.boards} 个画布。`;
        message(dialog, done);
        if (typeof showCanvasToast === "function") {
          showCanvasToast(result.workbenches
            ? `已导入 ${result.boards} 个画布、${result.workbenches} 个工作台`
            : `已导入 ${result.boards} 个画布`);
        }
      } catch (error) {
        this.importToken = "";
        message(dialog, `${error.message}；请重新选择文件后重试。`, true);
      } finally {
        setBusy(dialog, false);
        dialog.querySelector("[data-transfer-confirm]").disabled = !this.importToken;
      }
    },
  };
  window.WorkspaceTransfer = Transfer;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => Transfer.init());
  else Transfer.init();
})();
