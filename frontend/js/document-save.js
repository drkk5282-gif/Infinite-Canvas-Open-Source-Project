/* Debounced, asynchronous document persistence. Existing server payloads and
 * feature handlers stay intact; no global click/input snapshot scans. */
(function (global) {
  const controllers = new Set();
  const pause = () => new Promise((resolve) => setTimeout(resolve, 0));
  let worker;
  let codecSequence = 0;
  const codecRequests = new Map();
  function codec(operation, value) {
    if (!worker) {
      try {
        worker = new Worker('/static/js/document-codec.js?v=save-20260904');
        worker.onmessage = ({ data }) => {
          const request = codecRequests.get(data.id);
          codecRequests.delete(data.id);
          if (request) data.error ? request.reject(new Error(data.error)) : request.resolve(data.result);
        };
        worker.onerror = () => {
          for (const request of codecRequests.values()) request.reject(new Error('保存线程不可用'));
          codecRequests.clear();
          worker.terminate();
          worker = null;
        };
      } catch { /* Older browsers use the yielded fallback below. */ }
    }
    if (!worker) return pause().then(() => operation === 'encode' ? JSON.stringify(value) : JSON.parse(value));
    return new Promise((resolve, reject) => {
      const id = ++codecSequence;
      codecRequests.set(id, { resolve, reject });
      try { worker.postMessage({ id, operation, value }); }
      catch (error) {
        codecRequests.delete(id);
        // Match JSON's previous behavior for optional runtime functions/toJSON
        // values that cannot be structured-cloned. This is not the normal path.
        if (operation === 'encode' && error.name === 'DataCloneError') {
          pause().then(() => JSON.stringify(value)).then(resolve, reject);
        } else reject(error);
      }
    });
  }

  let dbPromise;
  function database() {
    if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open('canvas-documents', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('snapshots');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    }).catch((error) => { dbPromise = null; throw error; });
    return dbPromise;
  }
  const storage = {
    async read(key) {
      const db = await database();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('snapshots', 'readonly');
        const store = tx.objectStore('snapshots');
        let meta = null;
        const rows = new Map();
        const request = store.get(key);
        request.onsuccess = () => {
          meta = request.result || null;
          for (const id of meta?.ids || []) {
            const item = store.get([key, id]);
            item.onsuccess = () => { if (item.result) rows.set(id, item.result); };
          }
        };
        tx.oncomplete = () => resolve(meta ? { ...meta, rows } : null);
        tx.onerror = tx.onabort = () => reject(tx.error || new Error('读取缓存失败'));
      });
    },
    async write(key, meta, rows, removed = []) {
      const db = await database();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('snapshots', 'readwrite');
        const store = tx.objectStore('snapshots');
        store.put(meta, key);
        for (const [id, value] of rows) store.put(value, [key, id]);
        for (const id of removed) store.delete([key, id]);
        tx.oncomplete = resolve;
        tx.onerror = tx.onabort = () => reject(tx.error || new Error('写入缓存失败'));
      });
    },
  };

  function create(options, dependencies = {}) {
    const io = dependencies.storage || storage;
    const convert = dependencies.codec || codec;
    const token = options.token;
    const key = options.key;
    const journalKey = `document-save-journal:${key}`;
    let encoded = new Map();
    let acknowledged = new Map();
    let dirty = false, initialized = false, disposed = false;
    let revision = 0;
    let timer = null, maxTimer = null, retryTimer = null;
    let inFlight = null, loading = null;
    let lastLoadAt = 0, retryDelay = 1000, warning = '';
    let storageEpoch = null;
    let remoteUnverified = false;
    // null = upload any differing doc (legacy/full). Set = only docs this tab edited.
    let touchedIds = null;
    const allowed = () => !disposed && options.canSave(token);
    const isTouched = (id) => !(touchedIds instanceof Set) || touchedIds.has(id);
    const differs = () => {
      for (const id of acknowledged.keys()) if (!encoded.has(id)) return true;
      return [...encoded].some(([id, row]) => acknowledged.get(id) !== row && isTouched(id));
    };
    const report = (message) => {
      if (warning !== message) options.warn?.(message);
      warning = message;
    };
    function clearTimers() {
      clearTimeout(timer); clearTimeout(maxTimer); clearTimeout(retryTimer);
      timer = maxTimer = retryTimer = null;
    }
    function meta() {
      return {
        version: 1, ids: [...encoded.keys()],
        pending: [...encoded].filter(([id, row]) => acknowledged.get(id) !== row && isTouched(id)).map(([id]) => id),
        removed: [...acknowledged.keys()].filter((id) => !encoded.has(id)),
        ui: options.ui(), updatedAt: options.updatedAt(), storageEpoch,
      };
    }
    async function encodeRows(rows) {
      const map = new Map();
      // One document per worker message bounds structured-clone work on the UI
      // thread. The next document starts after a task boundary, allowing paint.
      for (const row of rows) {
        if (!row?.id) continue;
        map.set(row.id, await convert('encode', row));
        if (disposed) break;
      }
      return map;
    }
    function schedule() {
      if (!allowed()) return;
      clearTimeout(timer);
      timer = setTimeout(() => void flush(), 350);
      if (!maxTimer) maxTimer = setTimeout(() => void flush(), 2000);
    }
    function noteTouched(docIds) {
      if (docIds === undefined) {
        touchedIds = null;
        return;
      }
      // A later scoped edit must not narrow an outstanding full-document edit.
      if (!(touchedIds instanceof Set)) return;
      const list = Array.isArray(docIds) ? docIds : [docIds];
      for (const id of list) if (id) touchedIds.add(id);
    }
    function changed(docIds) {
      if (!allowed()) return;
      // No stringify, IndexedDB clone or fetch on the input/pointer hot path.
      // Pass a board/workbench id so multi-tab only uploads docs this window edited.
      noteTouched(docIds);
      dirty = true;
      revision++;
      schedule();
    }
    function emergency() {
      if (!allowed() || (!dirty && !differs())) return;
      // Only lifecycle shutdown uses synchronous storage. Normal edits use IDB.
      try {
        options.prepare?.();
        const rows = new Map(options.rows().filter((r) => r?.id).map((r) => [r.id, JSON.stringify(r)]));
        const changedRows = [...rows].filter(([id, value]) => acknowledged.get(id) !== value && isTouched(id));
        const removed = [...acknowledged.keys()].filter((id) => !rows.has(id));
        if (!changedRows.length && !removed.length) return;
        localStorage.setItem(journalKey, JSON.stringify({ rows: changedRows, removed, ui: options.ui(), storageEpoch }));
      } catch {
        report('本机应急缓存写入失败，请保持页面打开，等待保存完成');
      }
    }
    async function cacheSnapshot() {
      options.prepare?.();
      const captureRevision = revision;
      const snapshot = await encodeRows(options.rows());
      if (!allowed()) return false;
      const changedRows = new Map([...snapshot].filter(([id, row]) => encoded.get(id) !== row));
      const removed = [...encoded.keys()].filter((id) => !snapshot.has(id));
      encoded = snapshot;
      try {
        await io.write(key, meta(), changedRows, removed);
        if (!allowed()) return false;
        dirty = revision !== captureRevision;
        if (!dirty) localStorage.removeItem(journalKey);
        return true;
      } catch {
        emergency();
        report('本机缓存暂时不可用，正在同步服务器，请保持页面打开');
        dirty = revision !== captureRevision;
        return true; // Still attempt the server write with this snapshot.
      }
    }
    function retry() {
      if (retryTimer || !allowed()) return;
      retryTimer = setTimeout(() => { retryTimer = null; void flush(); }, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    }
    async function drain() {
      // An offline boot has no current server membership/epoch. Reconcile before
      // uploading so reconnect cannot resurrect deleted documents or old storage.
      if (remoteUnverified) {
        await load({ force: true });
        if (remoteUnverified) { retry(); return false; }
      }
      do {
        const captureRevision = revision;
        if (!(await cacheSnapshot())) return false;
        // Membership deletes use dedicated DELETE APIs — drop local ack only.
        for (const id of [...acknowledged.keys()]) {
          if (!encoded.has(id)) acknowledged.delete(id);
        }
        if (!differs()) continue;
        const ctrl = new AbortController();
        const timeout = setTimeout(() => ctrl.abort(), 15000);
        try {
          // Joining pre-encoded strings avoids a second full JSON stringify.
          const outgoing = options.select ? options.select(encoded) : encoded;
          const changed = [...outgoing].filter(([id, row]) => acknowledged.get(id) !== row && isTouched(id));
          if (!changed.length) continue;
          // PUT fallback must still send the full owned set; PATCH only ships touched diffs.
          const body = () => `{"${options.field}":[${[...outgoing.values()].join(',')}]}`;
          const partialBody = `{"${options.field}":[${changed.map(([, row]) => row).join(',')}]}`;
          const result = await options.send(body, { signal: ctrl.signal, partialBody });
          if (!allowed()) return false;
          if (result?.storageEpoch) storageEpoch = result.storageEpoch;
          const saved = Array.isArray(result?.savedIds) ? new Set(result.savedIds) : null;
          // PATCH returns savedIds for only the touched docs. Full PUT responses
          // omit savedIds — the whole owned set was written, so ack all outgoing.
          const acked = saved ? [...outgoing].filter(([id]) => saved.has(id)) : [...outgoing];
          for (const [id, row] of acked) {
            acknowledged.set(id, row);
            // This response acknowledges the captured snapshot, not edits made
            // while encoding, writing the cache, or waiting for the request.
            if (revision === captureRevision && touchedIds instanceof Set) touchedIds.delete(id);
          }
          options.committed(result);
          try { await io.write(key, meta(), new Map()); }
          catch { /* Pending IDB entries replay safely after a refresh. */ }
          retryDelay = 1000; warning = '';
        } catch (error) {
          if (!allowed()) return false;
          report('同步服务器失败，正在自动重试；请勿清理浏览器数据');
          retry();
          return false;
        } finally { clearTimeout(timeout); }
      } while (dirty && allowed());
      if (!dirty && !differs()) touchedIds = new Set();
      return true;
    }
    function flush() {
      if (!allowed()) return Promise.resolve(false);
      clearTimers();
      if (inFlight) return inFlight;
      if (!dirty && !differs()) return Promise.resolve(true);
      inFlight = drain().catch(() => { emergency(); retry(); return false; }).finally(() => { inFlight = null; });
      return inFlight;
    }
    async function loadOnce(force) {
      if (!allowed()) return;
      // Re-entering a view never waits for an unrelated slow save or reloads over
      // edits in memory. Sync resumes once the pending snapshot is acknowledged.
      if (initialized && (dirty || differs()) && !remoteUnverified) { void flush(); return; }
      if (initialized && !force && Date.now() - lastLoadAt < 4000) return;
      const loadRevision = revision;
      let cached = null, journal = null;
      if (initialized && remoteUnverified) {
        if (!(await cacheSnapshot()) || revision !== loadRevision) return;
        cached = { ...meta(), rows: new Map(encoded) };
      } else if (!initialized) {
        try { cached = await io.read(key); } catch { /* Server remains available. */ }
        try { journal = JSON.parse(localStorage.getItem(journalKey) || 'null'); } catch { /* ignore invalid journal */ }
      }
      let pendingIds = new Set(cached?.pending || []);
      let removedIds = new Set(cached?.removed || []);
      let local = cached?.rows || new Map();
      for (const [id, value] of journal?.rows || []) { local.set(id, value); pendingIds.add(id); removedIds.delete(id); }
      for (const id of journal?.removed || []) { local.delete(id); pendingIds.delete(id); removedIds.add(id); }
      let response;
      try { response = await options.fetch(); }
      catch {
        if (initialized) return;
        if (!cached && !journal) {
          const legacy = options.legacy();
          if (legacy.length) {
            options.adopt(legacy);
            initialized = true;
            changed();
          }
          report('暂时无法加载服务器数据，联网后重试');
          return;
        }
      }
      if (!allowed() || revision !== loadRevision) return;
      let rows = response ? response[options.field] : [];
      if (!Array.isArray(rows)) throw new Error('Invalid document response');
      const responseEpoch = response?.storageEpoch || null;
      if (responseEpoch) {
        const knownEpochs = [cached?.storageEpoch, journal?.storageEpoch].filter(Boolean);
        const replacedStorage = knownEpochs.some((value) => value !== responseEpoch);
        const resetWithoutEpoch = !knownEpochs.length && rows.length === 0 && response.updatedAt == null;
        if (replacedStorage || resetWithoutEpoch) {
          cached = null; journal = null;
          pendingIds = new Set(); removedIds = new Set(); local = new Map();
          localStorage.removeItem(journalKey);
          options.discardLegacy?.();
        }
        storageEpoch = responseEpoch;
      }
      if (!allowed() || revision !== loadRevision) return;
      const replayPending = [];
      if (!response) {
        storageEpoch = journal?.storageEpoch || cached?.storageEpoch || null;
        rows = [];
        for (const [id, value] of local) {
          rows.push(await convert('decode', value));
          if (pendingIds.has(id)) replayPending.push(id);
        }
      } else if (cached || journal) {
        const byId = new Map(rows.filter((r) => !removedIds.has(r.id)).map((r) => [r.id, r]));
        for (const id of pendingIds) {
          if (!local.has(id)) continue;
          // When the server answered, membership comes from the server. Do not
          // re-insert local-only pending rows (resurrects deleted boards/workbenches).
          if (!byId.has(id)) continue;
          let row = await convert('decode', local.get(id));
          const remote = byId.get(id);
          if (remote && options.mergePending) row = options.mergePending(remote, row) || row;
          // Server-supplied sharing permissions remain authoritative.
          if (remote) for (const k of ['isOwned', 'shareOwnerId', 'shareOwnerUsername', 'sharedFrom']) {
            if (Object.hasOwn(remote, k)) row[k] = remote[k]; else delete row[k];
          }
          byId.set(id, row);
          replayPending.push(id);
        }
        rows = [...byId.values()];
      } else if (!initialized && !(responseEpoch && rows.length === 0 && response.updatedAt == null)) {
        // One-time compatibility with the old browser cache recovery rules.
        rows = options.reconcile(rows, options.legacy());
      }
      if (!allowed() || revision !== loadRevision) return;
      options.adopt(rows, journal?.ui || cached?.ui);
      if (response) options.committed(response);
      // Baseline after adopt (ensureMeta etc.) so idle tabs do not re-upload the
      // whole library — only docs this tab later marks via changed(id).
      const baseline = await encodeRows(options.rows());
      if (!allowed() || revision !== loadRevision) return;
      acknowledged = new Map(baseline);
      encoded = new Map(baseline);
      // Restored edits are local snapshots, not server acknowledgements. Keep
      // them pending in IDB before removing the journal, including on re-refresh.
      touchedIds = new Set(replayPending);
      for (const id of replayPending) acknowledged.delete(id);
      initialized = true;
      remoteUnverified = !response;
      lastLoadAt = response ? Date.now() : 0;
      dirty = replayPending.length > 0;
      try {
        await io.write(key, meta(), baseline, []);
        if (allowed() && revision === loadRevision) localStorage.removeItem(journalKey);
      } catch { /* Server remains authoritative; IDB can catch up on next edit. */ }
      if (replayPending.length) schedule();
    }
    function load({ force = false } = {}) {
      if (loading) return loading;
      loading = loadOnce(force).finally(() => { loading = null; });
      return loading;
    }
    const api = {
      changed, flush, load, emergency,
      async acceptRemote(rows, accept) {
        if (!allowed() || dirty || differs()) return false;
        const before = revision;
        const next = await encodeRows(rows);
        if (!allowed() || revision !== before || dirty || differs()) return false;
        accept();
        acknowledged = next;
        encoded = new Map(next);
        touchedIds = new Set();
        dirty = false;
        try { await io.write(key, meta(), next, []); } catch { /* ignore */ }
        return true;
      },
      get pending() { return dirty || !!inFlight || differs(); },
      get revision() { return revision; },
      get ready() { return initialized; },
      dispose() { disposed = true; clearTimers(); controllers.delete(api); },
    };
    controllers.add(api);
    return api;
  }
  function suspend() {
    for (const controller of controllers) { controller.emergency(); void controller.flush(); }
  }
  global.addEventListener('pagehide', suspend);
  global.addEventListener('beforeunload', suspend);
  global.addEventListener('online', () => {
    for (const controller of controllers) controller.pending ? void controller.flush() : void controller.load({ force: true });
  });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') suspend(); });
  global.DocumentSave = { create, readResponse: async (response) => codec('decode', await response.text()) };
})(window);
