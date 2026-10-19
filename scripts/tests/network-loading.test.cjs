const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../..');
const settle = () => new Promise(resolve => setImmediate(resolve));

test('slow first task response starts only one loop; stopped responses cannot restart it', async () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
  const pending = [];
  const timers = new Map();
  let nextId = 0;
  let applied = 0;
  const context = {
    CanvasAuth: { isLoggedIn: () => true },
    api: () => new Promise(resolve => pending.push(resolve)),
    ensureProgressTicker() {}, rescheduleCanvasSyncIfNeeded() {},
    syncNodeFromTask() { applied++; }, reconcileStuckGenerationNodes() {},
    hasActiveGenerationNodes: () => true, loadAccounts: async () => {},
    updateThreadBadge() {}, getTaskPollDelayMs: () => 6500,
    setTimeout(fn) { timers.set(++nextId, fn); return nextId; },
    clearTimeout(id) { timers.delete(id); },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext('let pollTimer = null; let pollGeneration = 0;\n' +
    source.slice(source.indexOf('function clearTaskPolling()'), source.indexOf('async function syncAllNodesFromTasks()')) +
    source.slice(source.indexOf('function startPolling()'), source.indexOf('window.startPolling = startPolling;')), context);
  for (let i = 0; i < 20; i++) context.startPolling();
  assert.equal(pending.length, 1);
  assert.equal(context.isTaskPollingActive(), true);
  context.clearTaskPolling();
  context.startPolling();
  pending[0]({ tasks: [{id: 'stale', status: 'generating'}] });
  await settle();
  assert.equal(applied, 0);
  assert.equal(timers.size, 0);
  pending[1]({ tasks: [{id: 'live', status: 'generating'}] });
  await settle();
  assert.equal(applied, 1);
  assert.equal(timers.size, 1);
  context.clearTaskPolling();
  assert.equal(timers.size, 0);
});

test('slow canvas sync does not overlap and a failed request releases the guard', async () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const pending = [];
  const context = {
    CanvasAuth: {getToken: () => 'test'}, shouldPauseCanvasSync: () => false,
    fetch: () => new Promise((resolve, reject) => pending.push({resolve, reject})),
    ServerWatch: {reportFailure() {}},
  };
  context.window = context;
  vm.createContext(context);
  const method = source.slice(source.indexOf('  async syncFromServer(options = {})'), source.indexOf('  startSync()'));
  vm.runInContext('this.store = {' + method + '};', context);
  const first = context.store.syncFromServer();
  for (let i = 0; i < 10; i++) await context.store.syncFromServer();
  assert.equal(pending.length, 1);
  pending[0].reject(new Error('offline'));
  await first;
  const second = context.store.syncFromServer();
  assert.equal(pending.length, 2);
  pending[1].resolve({ok: false});
  await second;
  assert.equal(context.store._syncInFlight, false);
});
