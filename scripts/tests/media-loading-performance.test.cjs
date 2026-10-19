const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/canvas.js'), 'utf8');

function harness(overrides = {}) {
  const queue = [];
  let now = 1000;
  const timers = [];
  const context = {
    Date: {
      now: () => now,
    },
    setTimeout(fn, ms = 0) {
      const id = timers.length + 1;
      timers.push({ id, fn, at: now + ms, done: false });
      return id;
    },
    clearTimeout(id) {
      const hit = timers.find((t) => t.id === id);
      if (hit) hit.done = true;
    },
    MEDIA_LOAD_SLOT_TIMEOUT_MS: 20000,
    mediaLoadPriorityForEl: () => 1,
    scheduleMediaLoad: (run) => queue.push(run),
    runMediaLoadWithTimeout: (run, clear, release) => run(release || (() => {})),
    syncVideoPreviewPendingState() {},
    shouldShowNodePreviewVideoEl: () => true,
    shouldNodeVideoAutoplay: () => false,
    markLazyVideoVisible() {},
    probeNodeVideoDimensions() {},
    ...overrides,
  };
  vm.createContext(context);
  vm.runInContext(
    source.slice(
      source.indexOf('const previewVideoTransfers ='),
      source.indexOf('function getNodeMediaLazyObserver(')
    ),
    context
  );
  const listeners = new Map();
  const video = {
    dataset: { src: '/slow.mp4' },
    readyState: 0,
    videoWidth: 0,
    isConnected: true,
    currentSrc: '',
    src: '',
    resets: 0,
    loads: 0,
    classList: { contains: () => false, remove() {} },
    closest: () => null,
    getAttribute() {
      return this.src;
    },
    removeAttribute() {
      this.src = '';
      this.resets++;
    },
    load() {
      this.loads++;
    },
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) {
      listeners.get(name)?.delete(fn);
    },
  };
  const advance = (ms) => {
    const end = now + ms;
    now = end;
    let guard = 0;
    while (guard++ < 200) {
      const due = timers.filter((t) => !t.done && t.at <= now).sort((a, b) => a.at - b.at);
      if (!due.length) break;
      for (const t of due) {
        t.done = true;
        t.fn();
      }
    }
  };
  return { context, queue, video, listeners, advance, getNow: () => now, setNow: (v) => { now = v; } };
}

test('twenty hydrate calls share a queued video load and preserve an active slow request', () => {
  const { context, queue, video } = harness();
  for (let i = 0; i < 20; i++) context.ensureLazyVideoEl(video);
  assert.equal(queue.length, 1);
  queue.shift()(() => {});
  for (let i = 0; i < 20; i++) context.ensureLazyVideoEl(video);
  assert.equal(queue.length, 0);
  assert.equal(video.resets, 0);
  assert.equal(video.loads, 1);
});

test('an expired queued attempt is skipped; retry and changed URLs still load', () => {
  const { context, queue, video, getNow } = harness();
  context.ensureLazyVideoEl(video);
  video.dataset.mediaLoadQueuedAt = String(getNow() - 21000);
  context.ensureLazyVideoEl(video);
  let released = 0;
  queue.shift()(() => released++);
  assert.equal(released, 1);
  assert.equal(video.src, '');
  queue.shift()(() => {});
  assert.equal(video.src, '/slow.mp4');
  video.dataset.src = '/replacement.mp4';
  context.ensureLazyVideoEl(video);
  queue.shift()(() => {});
  assert.equal(video.src, '/replacement.mp4');
});

test('successful video loads remove all attempt listeners and release once', () => {
  const { context, queue, video, listeners } = harness();
  context.ensureLazyVideoEl(video);
  let released = 0;
  queue.shift()(() => released++);
  video.readyState = 2;
  for (const fn of [...listeners.get('loadeddata')]) fn();
  assert.equal(released, 1);
  assert.equal(listeners.get('error').size, 0);
  assert.equal(listeners.get('loadeddata').size, 0);
  // One progress and one metadata listener belong to the shared transfer guard.
  assert.equal([...listeners.values()].reduce((n, set) => n + set.size, 0), 2);
});

test('workbench frame recovery preserves slow downloads but retries expired ones', () => {
  const { context, video, getNow } = harness();
  const workbench = fs.readFileSync(path.join(__dirname, '../../frontend/js/workbench-extras.js'), 'utf8');
  vm.runInContext(
    workbench.slice(
      workbench.indexOf('function wbForceClipVideoSrc('),
      workbench.indexOf('/**\n * Keep kicking')
    ),
    context
  );
  const preview = { querySelector: () => video };
  for (let i = 0; i < 20; i++) context.wbForceClipVideoSrc(preview);
  assert.equal(video.loads, 1);
  video.dataset.wbForceLoadAt = String(getNow() - 21000);
  context.testVideo = video;
  vm.runInContext(
    'previewVideoTransfers.get(testVideo).started -= 31000; previewVideoTransfers.get(testVideo).progress -= 31000;',
    context
  );
  context.wbForceClipVideoSrc(preview);
  assert.equal(video.loads, 2);
  video.dataset.src = '/new.mp4';
  context.wbForceClipVideoSrc(preview);
  assert.equal(video.src, '/new.mp4');
  assert.equal(video.loads, 3);
});

test('scheduler timeout does not restart a live browser transfer; failed retries back off', () => {
  const { context, queue, video, advance } = harness();
  context.ensureLazyVideoEl(video);
  queue.shift()(() => {});
  // Claim markers can clear when the slot watchdog fires, but the browser transfer remains.
  advance(20000);
  video.dataset.loaded = '';
  delete video.dataset.loadedSrc;
  context.ensureLazyVideoEl(video);
  assert.equal(queue.length, 0);
  assert.equal(video.loads, 1);
  advance(11000);
  context.ensureLazyVideoEl(video);
  queue.shift()(() => {});
  const loadsAfterRetry = video.loads;
  advance(31000);
  video.dataset.loaded = '';
  delete video.dataset.loadedSrc;
  context.ensureLazyVideoEl(video);
  assert.equal(video.loads, loadsAfterRetry);
  assert.equal(queue.length, 0);
  advance(30000);
  context.ensureLazyVideoEl(video);
  assert.equal(queue.length, 1);
});

test('download progress preserves a slow transfer; changed URL and offscreen release remain reloadable', () => {
  const { context, queue, video, listeners, advance } = harness();
  context.ensureLazyVideoEl(video);
  queue.shift()(() => {});
  for (let i = 0; i < 5; i++) {
    advance(25000);
    for (const fn of listeners.get('progress')) fn();
    video.dataset.loaded = '';
    context.ensureLazyVideoEl(video);
    assert.equal(queue.length, 0);
  }
  video.removeAttribute('src');
  assert.equal(context.previewVideoTransferPending(video, '/slow.mp4'), false);
  video.dataset.src = '/different.mp4';
  context.ensureLazyVideoEl(video);
  queue.shift()(() => {});
  assert.equal(video.src, '/different.mp4');
});

test('frame-preview videos still go through the shared media queue', () => {
  const { context, queue, video } = harness();
  video.classList = {
    contains: (name) => name === 'node-preview-vid-el',
    remove() {},
  };
  video.closest = (sel) => {
    if (String(sel).includes('.node-preview')) {
      return { classList: { contains: (name) => name === 'is-frame-preview' } };
    }
    return null;
  };
  context.mediaLoadPriorityForEl = () => 0; // MEDIA_PRIO_HIGH
  context.ensureLazyVideoEl(video);
  assert.equal(queue.length, 1);
});

test('an imported video decode failure updates its preview and releases its load slot', async () => {
  const previews = [];
  const { context, queue, video, listeners, advance } = harness({
    uploadMediaPath: () => '',
    syncVideoPreviewPendingState: preview => previews.push(preview),
  });
  let released = 0;
  context.ensureLazyVideoEl(video);
  queue.shift()(() => released++);
  for (const fn of [...listeners.get('error')]) fn();
  await new Promise(resolve => setImmediate(resolve));
  advance(31000); // Existing transfer backoff must still release the scheduler slot.
  assert.equal(previews.length, 1);
  assert.equal(previews[0], null);
  assert.equal(released, 1);
});

test('scheduler keeps the concurrency slot while a browser transfer is pending', () => {
  const { context, queue, video, advance } = harness({
    runMediaLoadWithTimeout: (run, onTimeout, onRelease) => {
      run(() => {});
      onTimeout?.();
      onRelease?.();
    },
  });
  let released = 0;
  context.ensureLazyVideoEl(video);
  queue.shift()(() => released++);
  assert.equal(video.loads, 1);
  assert.equal(released, 0);
  advance(1000);
  assert.equal(released, 0);
  advance(30000);
  assert.equal(released, 1);
});
