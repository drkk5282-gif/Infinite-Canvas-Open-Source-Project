const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.join(__dirname, '..', '..');

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${name} must exist`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`Could not extract ${name}`);
}

test('a saved submit without a server task is immediately returned to the queue', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
  const node = {
    id: 'node',
    type: 'video',
    status: 'generating',
    taskId: null,
    genStartedAt: Date.now(),
    _genPayload: { type: 'video' },
  };
  const queued = [];
  const context = {
    window: {},
    CanvasStore: { boards: [{ id: 'board', nodes: [node] }], save() {} },
    isGenInFlight: () => false,
    enqueueGeneration: (id) => queued.push(id),
    renderNodes() {},
    syncNodeFromTask() {},
    Date,
    Map,
  };
  context.window.CanvasStore = context.CanvasStore;
  vm.createContext(context);
  vm.runInContext(extractFunction(source, 'reconcileStuckGenerationNodes'), context);

  context.reconcileStuckGenerationNodes([]);

  assert.equal(node.status, 'waiting');
  assert.equal(node.genStartedAt, null);
  assert.deepEqual(queued, ['node']);
});

test('an in-flight submit is not queued a second time', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
  const node = {
    id: 'node', type: 'video', status: 'generating', taskId: null,
    genStartedAt: Date.now(), _genPayload: { type: 'video' },
  };
  const queued = [];
  const context = {
    window: {},
    CanvasStore: { boards: [{ id: 'board', nodes: [node] }], save() {} },
    isGenInFlight: () => true,
    enqueueGeneration: (id) => queued.push(id),
    renderNodes() {},
    Date,
    Map,
  };
  context.window.CanvasStore = context.CanvasStore;
  vm.createContext(context);
  vm.runInContext(extractFunction(source, 'reconcileStuckGenerationNodes'), context);

  context.reconcileStuckGenerationNodes([]);

  assert.equal(node.status, 'generating');
  assert.deepEqual(queued, []);
});

test('preview distinguishes submitting from provider generation', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const context = {
    isTextGenNode: () => false,
    isVoiceTextNode: () => false,
  };
  vm.createContext(context);
  vm.runInContext(extractFunction(source, 'progressLabelForNode'), context);

  assert.equal(
    context.progressLabelForNode({ type: 'video', taskId: null, _genPayload: {} }),
    '正在提交视频任务…'
  );
  assert.equal(
    context.progressLabelForNode({ type: 'video', taskId: 'task', _genPayload: null }),
    '视频生成中…'
  );
});
