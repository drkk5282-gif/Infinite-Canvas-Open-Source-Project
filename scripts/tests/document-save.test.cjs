const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../frontend/js/document-save.js'), 'utf8');
const copy = (x) => structuredClone(x);
const settle = async () => { for (let i = 0; i < 100; i++) await Promise.resolve(); };
function harness({ disk = new Map(), local = new Map(), initial = [{ id: 'a', text: 'original' }], epoch = 'epoch-1', legacy = [], mergePending = null } = {}) {
  const timers = new Map(); let next = 0, now = 0;
  const listeners = new Map();
  const h = { disk, local, rows: [], server: copy(initial), epoch, legacy: copy(legacy), sends: [], writes: [], reads: 0, encodes: 0, allowed: true, warnings: [], discarded: 0 };
  const listen = (event, fn) => { if (!listeners.has(event)) listeners.set(event, []); listeners.get(event).push(fn); };
  const context = vm.createContext({
    console, AbortController, Blob, document: { addEventListener: listen }, addEventListener: listen,
    setTimeout(fn, ms) { const id = ++next; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    localStorage: { getItem: (key) => local.get(key) || null, setItem: (key, value) => local.set(key, value), removeItem: (key) => local.delete(key) },
  });
  context.window = context;
  vm.runInContext(code, context);
  h.controller = context.DocumentSave.create({
    token: 'test', key: 'test', field: 'boards', canSave: () => h.allowed,
    rows: () => h.rows, ui: () => ({ active: 'a' }), updatedAt: () => 'v1',
    legacy: () => copy(h.legacy), reconcile: (remote, old) => [...remote, ...old],
    mergePending,
    discardLegacy: () => { h.legacy = []; h.discarded++; }, adopt: (rows) => { h.rows = rows; },
    committed() {}, warn: (message) => h.warnings.push(message),
    fetch: async () => { h.reads++; if (h.get) return h.get(); return { boards: copy(h.server), updatedAt: h.server.length ? 'server' : null, storageEpoch: h.epoch }; },
    send: async (body, options) => {
      const data = JSON.parse(typeof body === 'function' ? body() : body); h.sends.push({ ...data, options });
      if (h.put) return h.put(data);
      h.server = data.boards; return { updatedAt: 'saved', storageEpoch: h.epoch };
    },
  }, {
    codec: async (operation, value) => { h.encodes++; return operation === 'encode' ? JSON.stringify(value) : JSON.parse(value); },
    storage: {
      read: async (key) => disk.has(key) ? copy(disk.get(key)) : null,
      write: async (key, meta, changed, removed) => {
        if (h.failCache) throw new Error('quota');
        const old = disk.get(key)?.rows || new Map();
        const rows = new Map(old);
        for (const [id, row] of changed) rows.set(id, row);
        for (const id of removed || []) rows.delete(id);
        disk.set(key, copy({ ...meta, rows }));
        h.writes.push([...changed.keys()]);
      },
    },
  });
  h.tick = async (ms) => {
    const target = now + ms;
    for (;;) {
      const due = [...timers].filter(([, x]) => x.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]); now = due[1].at; due[1].fn(); await settle();
    }
    now = target; await settle();
  };
  h.emit = async (event) => { for (const fn of listeners.get(event) || []) fn(); await settle(); };
  h.boot = async () => { await h.controller.load(); await h.tick(350); h.encodes = 0; h.writes.length = 0; };
  return h;
}

test('100 input changes do no serialization or storage on the hot path', async () => {
  const h = harness(); await h.boot();
  for (let i = 0; i < 100; i++) { h.rows[0].text = `${i}`; h.controller.changed(); }
  assert.equal(h.encodes, 0); assert.equal(h.writes.length, 0); assert.equal(h.sends.length, 0);
  await h.tick(350);
  assert.equal(h.sends.length, 1); assert.equal(h.server[0].text, '99');
});
test('ordinary clicks and keyup do not scan documents', async () => {
  const h = harness(); await h.boot();
  await h.emit('click'); await h.emit('keyup'); await h.tick(2500);
  assert.equal(h.encodes, 0); assert.equal(h.sends.length, 0);
});
test('only changed document rows are written to IndexedDB', async () => {
  const h = harness({ initial: Array.from({ length: 30 }, (_, i) => ({ id: `${i}`, text: 'same' })) });
  await h.boot(); h.rows[12].text = 'edited'; h.controller.changed(); await h.controller.flush();
  assert.deepEqual(h.writes.filter((ids) => ids.length), [['12']]);
});
test('slow saves serialize; edits during a request are not acknowledged early', async () => {
  const h = harness(); await h.boot(); let release;
  h.put = () => new Promise((resolve) => { release = resolve; });
  h.rows[0].text = 'first'; h.controller.changed(); const promise = h.controller.flush(); await settle();
  h.rows[0].text = 'latest'; h.controller.changed(); await h.tick(500);
  assert.equal(h.sends.length, 1); assert.equal(h.controller.pending, true);
  h.put = null; release({ updatedAt: 'first' }); await promise;
  assert.deepEqual(h.sends.map((s) => s.boards[0].text), ['first', 'latest']);
  assert.equal(h.controller.pending, false);
});

test('scoped edits during a PATCH remain pending until the newest snapshot is saved', async () => {
  const h = harness(); await h.boot(); let release;
  h.put = () => new Promise((resolve) => { release = resolve; });
  h.rows[0].text = 'first'; h.controller.changed('a');
  const saved = h.controller.flush(); await settle();
  h.rows[0].text = 'latest'; h.controller.changed('a');
  await h.tick(500);
  h.put = null; release({ updatedAt: 'first', savedIds: ['a'] }); await saved;
  assert.deepEqual(h.sends.map((s) => JSON.parse(s.options.partialBody).boards[0].text), ['first', 'latest']);
  assert.equal(h.server[0].text, 'latest');
  assert.equal(h.controller.pending, false);
});

test('a scoped edit cannot narrow earlier unscoped pending edits', async () => {
  const h = harness({ initial: [{ id: 'a', text: 'old' }, { id: 'b', text: 'old' }] }); await h.boot();
  h.rows[0].text = 'all docs'; h.controller.changed();
  h.rows[1].text = 'scoped'; h.controller.changed('b');
  await h.controller.flush();
  assert.deepEqual(JSON.parse(h.sends[0].options.partialBody).boards.map((r) => r.id), ['a', 'b']);
});
test('HTTP/network errors retry with the most recent snapshot', async () => {
  const h = harness(); await h.boot(); h.put = async () => { throw new Error('503'); };
  h.rows[0].text = 'offline'; h.controller.changed(); assert.equal(await h.controller.flush(), false);
  assert.equal(h.disk.get('test').pending[0], 'a');
  h.rows[0].text = 'updated offline'; h.controller.changed(); h.put = null; await h.tick(1000);
  assert.equal(h.server[0].text, 'updated offline'); assert.equal(h.controller.pending, false);
});
test('pagehide journal preserves edits made immediately before refresh', async () => {
  const h = harness(); await h.boot(); h.rows[0].text = 'last keystroke'; h.controller.changed();
  h.controller.emergency(); h.controller.dispose();
  const next = harness({ disk: h.disk, local: h.local }); await next.boot();
  assert.equal(next.rows[0].text, 'last keystroke'); assert.equal(next.server[0].text, 'last keystroke');
});

test('two immediate refreshes retain the pending journal until server acknowledgement', async () => {
  const h = harness(); await h.boot();
  h.rows[0].text = 'last keystroke'; h.controller.changed('a');
  h.controller.emergency(); h.controller.dispose();
  const next = harness({ disk: h.disk, local: h.local });
  await next.controller.load();
  assert.equal(next.controller.pending, true);
  assert.deepEqual(next.disk.get('test').pending, ['a']);
  next.controller.dispose(); // Refresh again before the debounce can send.
  const final = harness({ disk: h.disk, local: h.local }); await final.boot();
  assert.equal(final.server[0].text, 'last keystroke');
  assert.deepEqual(final.disk.get('test').pending, []);
});

test('an offline boot keeps edits pending and uploads the latest edit on reconnect', async () => {
  const h = harness(); await h.boot();
  h.rows[0].text = 'before refresh'; h.controller.changed('a');
  h.controller.emergency(); h.controller.dispose();
  const next = harness({ disk: h.disk, local: h.local });
  next.get = async () => { throw new Error('offline'); };
  next.put = async () => { throw new Error('offline'); };
  await next.boot();
  assert.equal(next.rows[0].text, 'before refresh');
  assert.equal(next.controller.pending, true);
  assert.equal(next.disk.get('test').storageEpoch, 'epoch-1');
  assert.deepEqual(next.disk.get('test').pending, ['a']);
  next.rows[0].text = 'edited while offline'; next.controller.changed('a');
  next.get = null; next.put = null; await next.emit('online');
  assert.equal(next.server[0].text, 'edited while offline');
  assert.equal(next.controller.pending, false);
});

test('reconnect validates membership and storage epoch before replaying an offline boot', async () => {
  for (const epoch of ['epoch-1', 'epoch-2']) {
    const h = harness(); await h.boot();
    h.rows[0].text = 'obsolete'; h.controller.changed('a');
    h.controller.emergency(); h.controller.dispose();
    const next = harness({ disk: h.disk, local: h.local, initial: [], epoch });
    next.get = async () => { throw new Error('offline'); };
    await next.boot();
    assert.equal(next.rows[0].text, 'obsolete');
    next.get = null; await next.emit('online');
    assert.equal(next.rows.length, 0);
    assert.equal(next.sends.length, 0);
    assert.equal(next.controller.pending, false);
    assert.equal(next.disk.get('test').storageEpoch, epoch);
  }
});
test('pending deletions survive refresh without richer-cache resurrection', async () => {
  const h = harness({ initial: [{ id: 'a', clips: [1, 2, 3] }] }); await h.boot();
  h.rows[0].clips = [1]; h.controller.changed(); h.put = async () => { throw new Error('offline'); };
  await h.controller.flush(); h.controller.dispose();
  const next = harness({ disk: h.disk, local: h.local, initial: [{ id: 'a', clips: [1, 2, 3] }] }); await next.boot();
  assert.deepEqual(next.rows[0].clips, [1]);
});
test('synced cache cannot restore an item deleted on another device', async () => {
  const h = harness(); await h.boot(); h.controller.dispose();
  const next = harness({ disk: h.disk, initial: [] }); await next.boot();
  assert.equal(next.rows.length, 0); assert.equal(next.sends.length, 0);
});
test('recreated server storage rejects pending snapshots from the previous epoch', async () => {
  const h = harness(); await h.boot();
  h.rows[0].text = 'offline edit'; h.controller.changed(); h.put = async () => { throw new Error('offline'); };
  await h.controller.flush(); h.controller.dispose();
  const next = harness({ disk: h.disk, local: h.local, initial: [], epoch: 'epoch-2' }); await next.boot();
  assert.deepEqual(next.rows, []); assert.equal(next.sends.length, 0);
  assert.equal(next.disk.get('test').storageEpoch, 'epoch-2');
});
test('fresh empty storage does not resurrect pre-epoch localStorage data', async () => {
  const h = harness({ initial: [], legacy: [{ id: 'old', text: 'deleted' }] }); await h.boot();
  assert.deepEqual(h.rows, []); assert.equal(h.sends.length, 0); assert.equal(h.discarded, 1);
});
test('pending row merges without overwriting unrelated remote changes', async () => {
  const h = harness({ initial: [{ id: 'a', text: 'old' }, { id: 'b', text: 'old' }] }); await h.boot();
  h.rows[0].text = 'local'; h.controller.changed(); h.put = async () => { throw new Error('offline'); };
  await h.controller.flush(); h.controller.dispose();
  const next = harness({ disk: h.disk, initial: [{ id: 'a', text: 'old' }, { id: 'b', text: 'remote' }] }); await next.boot();
  assert.deepEqual(Array.from(next.rows, (r) => r.text), ['local', 'remote']);
});
test('document adapter can protect rich server fields from an ambiguous empty pending cache', async () => {
  const protectPrompt = (remote, pending) => {
    if (!pending.prompt && remote.prompt) pending.prompt = remote.prompt;
    return pending;
  };
  const h = harness({ initial: [{ id: 'a', prompt: 'important' }] }); await h.boot();
  h.rows[0].prompt = ''; h.controller.changed(); h.put = async () => { throw new Error('offline'); };
  await h.controller.flush(); h.controller.dispose();
  const next = harness({ disk: h.disk, initial: [{ id: 'a', prompt: 'important' }], mergePending: protectPrompt }); await next.boot();
  assert.equal(next.rows[0].prompt, 'important');
});
test('deduplicates concurrent loads and immediate re-entry', async () => {
  const h = harness(); await Promise.all([h.controller.load(), h.controller.load(), h.controller.load()]);
  await h.tick(350); await h.controller.load(); assert.equal(h.reads, 1);
});
test('delayed initial GET cannot replace newly created local content', async () => {
  const h = harness(); let release;
  h.get = () => new Promise((resolve) => { release = resolve; });
  const loaded = h.controller.load(); await settle();
  h.rows.push({ id: 'new', text: 'new project' }); h.controller.changed();
  release({ boards: [{ id: 'old' }] }); await loaded;
  assert.equal(h.rows[0].id, 'new');
});
test('old account request completion cannot write to a new account', async () => {
  const h = harness(); await h.boot(); let release;
  h.put = () => new Promise((resolve) => { release = resolve; });
  h.rows[0].text = 'alice'; h.controller.changed(); const saved = h.controller.flush(); await settle();
  h.allowed = false; h.rows = []; release({ updatedAt: 'old session' }); await saved;
  assert.deepEqual(h.disk.get('test').pending, ['a']);
});
test('large saves never opt into keepalive quota', async () => {
  const h = harness(); await h.boot(); h.rows[0].text = '中'.repeat(50000); h.controller.changed();
  await h.controller.flush(); assert.equal(h.sends[0].options.keepalive, undefined);
});
test('continuous typing is checkpointed within two seconds', async () => {
  const h = harness(); await h.boot();
  for (let i = 0; i < 11; i++) { h.rows[0].text = `${i}`; h.controller.changed(); await h.tick(200); }
  assert.ok(h.sends.length >= 1);
});
test('remote refresh adopts once and does not upload unchanged remote data', async () => {
  const h = harness(); await h.boot();
  const rows = [{ id: 'a', text: 'remote' }];
  assert.equal(await h.controller.acceptRemote(rows, () => { h.rows = rows; }), true);
  await h.tick(350);
  assert.equal(h.sends.length, 0); assert.equal(h.rows[0].text, 'remote');
});
test('partial payload contains only edited rows and leaves full fallback lazy', async () => {
  const h = harness({ initial: [{ id: 'a', text: 'old' }, { id: 'b', text: 'unchanged' }] }); await h.boot();
  h.rows[0].text = 'edit'; h.controller.changed(); await h.controller.flush();
  assert.deepEqual(JSON.parse(h.sends[0].options.partialBody).boards.map((r) => r.id), ['a']);
});
