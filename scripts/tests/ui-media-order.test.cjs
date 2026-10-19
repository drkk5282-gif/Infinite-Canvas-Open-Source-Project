const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..', '..');

function loadWorkbenchStore() {
  const noop = () => {};
  const document = {
    addEventListener: noop,
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    body: { classList: { toggle: noop } },
  };
  const context = {
    console,
    document,
    location: { hash: '', origin: 'http://test' },
    history: { replaceState: noop },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: noop,
    requestAnimationFrame: noop,
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    CSS: { escape: String },
    FormData: class {},
    confirm: () => true,
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/js/workbench.js'), 'utf8'), context);
  return context.WorkbenchStore;
}

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

test('moving a clip keeps the requested array order and rewrites order/name', () => {
  const store = loadWorkbenchStore();
  const ep = {
    clips: [
      { id: 'a', order: 0, name: '第1片段' },
      { id: 'b', order: 1, name: '第2片段' },
      { id: 'c', order: 2, name: '第3片段' },
    ],
  };
  assert.equal(store.moveClip(ep, 'b', -1), true);
  assert.deepEqual(Array.from(ep.clips, (clip) => clip.id), ['b', 'a', 'c']);
  assert.deepEqual(Array.from(ep.clips, (clip) => clip.order), [0, 1, 2]);
  assert.deepEqual(Array.from(ep.clips, (clip) => clip.name), ['第1片段', '第2片段', '第3片段']);
});

test('reindex normalizes the backing array as well as display indexes', () => {
  const store = loadWorkbenchStore();
  const ep = { clips: [{ id: 'third', order: 2 }, { id: 'first', order: 0 }, { id: 'second', order: 1 }] };
  store.reindexClips(ep);
  assert.deepEqual(Array.from(ep.clips, (clip) => clip.id), ['first', 'second', 'third']);
  assert.deepEqual(Array.from(ep.clips, (clip) => clip.order), [0, 1, 2]);
});

test('a hung media request releases its scheduler slot', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const timers = new Map();
  let timerId = 0;
  const context = {
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.createContext(context);
  vm.runInContext(`
    let mediaLoadActive = 0;
    let mediaLoadHoldDepth = 0;
    const mediaLoadQueue = [];
    const MEDIA_LOAD_MAX = 4;
    const MEDIA_PRIO_NORMAL = 1;
    const MEDIA_LOAD_SLOT_TIMEOUT_MS = 15000;
    ${extractFunction(source, 'enqueueMediaLoad')}
    ${extractFunction(source, 'pumpMediaLoadQueue')}
    ${extractFunction(source, 'scheduleMediaLoad')}
    ${extractFunction(source, 'runMediaLoadWithTimeout')}
    this.scheduleMediaLoad = scheduleMediaLoad;
    this.runMediaLoadWithTimeout = runMediaLoadWithTimeout;
  `, context);
  const started = [];
  // The image/video loader owns its watchdog; the scheduler owns the slot.
  // Do not impose a scheduler timeout on browser transfers still in progress.
  for (let i = 0; i < 5; i++) context.scheduleMediaLoad((done) => {
    context.runMediaLoadWithTimeout(() => started.push(i), () => {}, done);
  });
  assert.deepEqual(started, [0, 1, 2, 3]);
  const firstTimer = timers.entries().next().value;
  timers.delete(firstTimer[0]);
  firstTimer[1]();
  assert.deepEqual(started, [0, 1, 2, 3, 4]);
});

test('video normalize replacement explicitly finishes the failed attempt', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  assert.match(source, /ensureLazyVideoEl\(video\);\s*\/\/ The replacement load[\s\S]*?onDone\(false\);/);
});

test('an active backend task rebinds a canvas node after taskId was lost on refresh', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const node = { id: 'node-a', type: 'video', status: 'idle', taskId: null };
  let saves = 0;
  let flushes = 0;
  const context = {
    CanvasStore: {
      boards: [{ id: 'board-a', nodes: [node] }],
      activeBoardId: 'board-a',
      save() { saves++; },
      flushSave() { flushes++; return Promise.resolve(); },
    },
    window: { ensureProgressTicker() {} },
    isTextGenNode: () => false,
    normalizeUploadUrlKey: (value) => value,
  };
  vm.createContext(context);
  vm.runInContext(`
    ${extractFunction(source, 'findCanvasTaskBinding')}
    ${extractFunction(source, 'syncNodeFromTask')}
    this.syncNodeFromTask = syncNodeFromTask;
  `, context);

  const changed = context.syncNodeFromTask({
    id: 'task-a', kind: 'video', status: 'generating', board_id: 'board-a', node_id: 'node-a',
    ratio: '16:9', created_at: '2026-09-04T12:00:00',
  });
  assert.equal(changed, true);
  assert.equal(node.taskId, 'task-a');
  assert.equal(node.status, 'generating');
  assert.ok(node.genStartedAt > 0);
  assert.equal(saves, 1);
  assert.equal(flushes, 1);
});

test('an old terminal task does not revive an intentionally idle canvas node', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const node = { id: 'node-a', type: 'video', status: 'idle', taskId: null };
  const context = {
    CanvasStore: { boards: [{ id: 'board-a', nodes: [node] }] },
    isTextGenNode: () => false,
  };
  vm.createContext(context);
  vm.runInContext(`
    ${extractFunction(source, 'findCanvasTaskBinding')}
    this.findCanvasTaskBinding = findCanvasTaskBinding;
  `, context);
  assert.equal(context.findCanvasTaskBinding({
    id: 'old-task', kind: 'video', status: 'failed', board_id: 'board-a', node_id: 'node-a',
  }), null);
});

test('recovery respects stop markers, history, board identity and existing task bindings', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const node = { id: 'node-a', type: 'video', status: 'idle', taskId: null };
  const context = {
    CanvasStore: { boards: [{ id: 'board-a', nodes: [node] }] },
    isTextGenNode: () => false,
  };
  vm.createContext(context);
  vm.runInContext(extractFunction(source, 'isPausedGenerationError') + '\n' + extractFunction(source, 'findCanvasTaskBinding'), context);
  const task = { id: 'task-a', kind: 'video', status: 'generating', board_id: 'board-a', node_id: 'node-a' };
  for (const patch of [
    { generationStopped: true }, { stoppedTaskId: 'task-a' },
    { activeHistoryId: 'selected-result' }, { status: 'completed' },
    { taskId: 'other-task' }, { error: '已手动暂停' },
  ]) {
    Object.assign(node, patch);
    assert.equal(context.findCanvasTaskBinding(task), null, JSON.stringify(patch));
    for (const key of Object.keys(patch)) delete node[key];
    Object.assign(node, { status: 'idle', taskId: null });
  }
  for (const patch of [{ board_id: null }, { board_id: 'other-board' }, { kind: 'image' }, { workbench_id: 'wb' }]) {
    assert.equal(context.findCanvasTaskBinding({ ...task, ...patch }), null);
  }
  // A subsequent explicit generate can recover its own task, never the stopped one.
  Object.assign(node, { generationStopped: false, stoppedTaskId: 'task-a', status: 'generating' });
  assert.equal(context.findCanvasTaskBinding(task), null);
  assert.equal(context.findCanvasTaskBinding({ ...task, id: 'new-task' }).node, node);
});

test('manual stop and queue cancellation stay stopped after serialization and stale polling', async () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/canvas.js'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
  for (const status of ['waiting', 'pending', 'generating']) {
    const node = { id: 'node-a', type: 'video', channel: 'bendi', status, taskId: status === 'waiting' ? null : 'task-a' };
    const requests = [];
    const context = {
      CanvasStore: {
        boards: [{ id: 'board-a', nodes: [node] }],
        updateNode(id, patch) { Object.assign(node, patch); },
      },
      genQueue: ['node-a'], findNodeAnywhere: () => node, findWorkbenchGenTarget: () => null,
      AppDialog: { confirm: async () => true }, api: async (url) => { requests.push(url); },
      clearAutoRetryTimer() {}, updateThreadBadge() {}, renderNodes() {}, pumpGenQueue() {}, showCanvasToast() {},
      isTextGenNode: () => false,
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext([
      extractFunction(source, 'findCanvasTaskBinding'),
      extractFunction(app, 'cancelQueuedGeneration'),
      'async ' + extractFunction(app, 'pauseGeneration'),
    ].join('\n'), context);
    await context.pauseGeneration(node.id);
    assert.equal(node.status, 'idle');
    assert.equal(node.taskId, null);
    assert.equal(node.generationStopped, true);
    assert.deepEqual(requests, status === 'waiting' ? [] : ['/api/tasks/task-a/cancel']);
    context.CanvasStore.boards[0].nodes = [JSON.parse(JSON.stringify(node))];
    assert.equal(context.findCanvasTaskBinding({
      id: 'task-a', kind: 'video', status: 'generating', board_id: 'board-a', node_id: 'node-a',
    }), null);
  }
});

test('active backend generation wins over local timeout and missing lists get a grace window', () => {
  const source = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
  let saves = 0;
  const video = {
    id: 'video-a', type: 'video', status: 'generating', taskId: 'task-a',
    genStartedAt: Date.now() - 2 * 60 * 60 * 1000,
  };
  const context = {
    CanvasStore: { boards: [{ nodes: [video] }], save() { saves++; } },
    syncNodeFromTask() {},
    renderNodes() {},
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(`
    ${extractFunction(source, 'reconcileStuckGenerationNodes')}
    this.reconcileStuckGenerationNodes = reconcileStuckGenerationNodes;
  `, context);

  context.reconcileStuckGenerationNodes([{ id: 'task-a', status: 'generating' }]);
  assert.equal(video.status, 'generating');
  assert.equal(saves, 0);

  video.genStartedAt = Date.now() - 59 * 60 * 1000;
  context.reconcileStuckGenerationNodes([]);
  assert.equal(video.status, 'generating');

  const image = {
    id: 'image-a', type: 'image', status: 'generating', taskId: 'missing-image',
    genStartedAt: Date.now() - 11 * 60 * 1000,
  };
  context.CanvasStore.boards[0].nodes.push(image);
  context.reconcileStuckGenerationNodes([]);
  assert.equal(image.status, 'failed');
  assert.match(image.error, /重新检测或重新生成/);
});
