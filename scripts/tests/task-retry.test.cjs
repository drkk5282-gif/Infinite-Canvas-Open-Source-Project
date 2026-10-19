const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');
const start = source.indexOf('function scheduleAutoRetryPatch(');
const code = source.slice(start, source.indexOf('window.scheduleAutoRetryPatch', start));

function setup(node, apiImpl = async () => ({ ok: true })) {
  let timer;
  const calls = [];
  const context = {
    node, AUTO_RETRY_MAX: 3, AUTO_RETRY_DELAY_MS: 1800,
    autoRetryTimers: new Map(), findNodeAnywhere: () => context.node,
    setTimeout(fn) { timer = fn; return 123; },
    api: async (...args) => { calls.push(args); return apiImpl(...args); },
    CanvasStore: {
      updateNode(id, patch) { Object.assign(context.node, patch); },
      flushSave() {},
    },
    renderNodes() {}, startPolling() {},
    enqueueGeneration() { throw new Error('must not create another generation'); },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, calls, runTimer: () => timer?.() };
}

test('a bound task is rechecked without clearing ID or resubmitting', async () => {
  const node = { id: 'node', taskId: 'original', autoRetry: true, status: 'failed' };
  const env = setup(node);
  const patch = env.context.scheduleAutoRetryPatch('node', 'query failed');
  Object.assign(node, patch);
  assert.equal(node.taskId, 'original');
  assert.equal(env.context.scheduleAutoRetryPatch('node', 'duplicate poll'), null);
  await env.runTimer();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0][0], '/api/tasks/original/recheck');
  assert.equal(node.status, 'generating');
  assert.equal(node.taskId, 'original');
});

test('unknown submission outcome and manual pause never auto-submit', () => {
  for (const [taskId, error] of [[null, 'network failed'], ['original', '已手动暂停']]) {
    const env = setup({ taskId, autoRetry: true, status: 'failed' });
    assert.equal(env.context.scheduleAutoRetryPatch('node', error), null);
    assert.equal(env.context.autoRetryTimers.size, 0);
  }
});

test('switching history before retry does not query or modify the new binding', async () => {
  const node = { taskId: 'original', autoRetry: true, status: 'failed' };
  const env = setup(node);
  env.context.scheduleAutoRetryPatch('node', 'network failed');
  node.taskId = 'different';
  await env.runTimer();
  assert.equal(env.calls.length, 0);
  assert.equal(node.status, 'failed');
});

test('permanent recheck rejection stops retrying without creating a task', async () => {
  const node = { taskId: 'original', autoRetry: true, status: 'failed' };
  const env = setup(node, async () => { throw Object.assign(new Error('no recoverable ID'), { status: 400 }); });
  Object.assign(node, env.context.scheduleAutoRetryPatch('node', 'failed'));
  await env.runTimer();
  assert.equal(node.taskId, 'original');
  assert.equal(node.autoRetryCount, 3);
  assert.equal(env.context.scheduleAutoRetryPatch('node', 'failed'), null);
});
