const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/canvas.js'), 'utf8');

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf('\nfunction ', start + 1);
  return source.slice(start, end < 0 ? undefined : end);
}
function classes() {
  const values = new Set();
  return { contains: s => values.has(s), add: (...xs) => xs.forEach(x => values.add(x)),
    remove: (...xs) => xs.forEach(x => values.delete(x)),
    toggle(s, on) { if (on) values.add(s); else values.delete(s); } };
}

test('120 pointer samples perform one node/connection update and commit the last position', () => {
  const frames = new Map();
  let seq = 0, pathUpdates = 0;
  const node = {x: 10, y: 20};
  const drag = {type: 'nodes', viewCache: {}, anchorId: 'n', offsetX: 0, offsetY: 0,
    nodeIds: ['n'], startPositions: {n: {x: 10, y: 20}}, nodeRefs: {n: node}, wraps: {n: {style: {}}}};
  const context = vm.createContext({CanvasStore: {drag},
    requestAnimationFrame(fn) { frames.set(++seq, fn); return seq; },
    cancelAnimationFrame: id => frames.delete(id),
    screenToWorldCached: (x, y) => ({x, y}),
    AlignGuides: {snapDragLight: (_, dx, dy) => ({dx, dy})},
    updateDragConnectionPaths() { pathUpdates++; },
  });
  vm.runInContext(['handleNodeDragMove', 'flushNodeDragMove', 'applyNodeDragMove'].map(extract).join('\n'), context);
  for (let i = 1; i <= 120; i++) context.handleNodeDragMove({clientX: i, clientY: i * 2, buttons: 1});
  assert.equal(frames.size, 1);
  assert.equal(pathUpdates, 0);
  context.flushNodeDragMove(drag); // pointerup/blur must flush before history/save
  assert.equal(frames.size, 0);
  assert.equal(pathUpdates, 1);
  assert.deepEqual(node, {x: 120, y: 240});
  context.flushNodeDragMove(drag);
  assert.equal(pathUpdates, 1);
});

test('dragging updates cached wire endpoints without DOM or node lookups, including multi-selection', () => {
  const values = [];
  const paths = [0, 1, 2, 3, 4].map(index => ({setAttribute: (name, value) => values[index] = value}));
  const context = vm.createContext({
    getPortCenterForConnections() { throw Error('Drag queried live geometry'); },
    connectionPath: (...points) => points.join(','),
  });
  vm.runInContext(extract('updateDragConnectionPaths'), context);
  const entry = {startFrom:{x:10,y:20}, startTo:{x:200,y:300}, moveFrom:true, moveTo:false, paths};
  const drag = {connEntries:[entry], _lastDx:40, _lastDy:-10};
  context.updateDragConnectionPaths(drag);
  assert.deepEqual(values, Array(5).fill('50,10,200,300'));
  entry.moveTo = true;
  context.updateDragConnectionPaths(drag);
  assert.deepEqual(values, Array(5).fill('50,10,240,290'));
  drag._lastDx = 0; drag._lastDy = 0;
  context.updateDragConnectionPaths(drag);
  assert.deepEqual(values, Array(5).fill('10,20,200,300'));
});

function imageHarness() {
  const queue = [], timers = [], listeners = new Map();
  let releases = 0;
  const img = { dataset: {src: 'https://media.test/image.jpg?signature=original'},
    classList: classes(), isConnected: true, complete: false, naturalWidth: 0,
    closest: () => null,
    addEventListener(event, fn) { if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event).add(fn); },
    removeEventListener(event, fn) { listeners.get(event)?.delete(fn); },
  };
  const context = vm.createContext({Date, MEDIA_LOAD_SLOT_TIMEOUT_MS: 1000, MEDIA_PRIO_HIGH: 0,
    mediaLoadPriorityForEl: () => 1, isMediaMissingUrl: () => false, isKnownMediaUrl404: () => false,
    scheduleMediaLoad: fn => queue.push(fn),
    runMediaLoadWithTimeout(run, onTimeout, release) {
      context.expire = () => {onTimeout(); release?.();};
      run(() => release?.());
    },
    setTimeout(fn) { timers.push(fn); },
  });
  vm.runInContext(extract('ensureLazyImage'), context);
  return {context, img, queue, timers, listeners,
    start: () => queue.shift()(() => releases++),
    emit: event => [...(listeners.get(event) || [])].forEach(fn => fn()),
    count: () => [...listeners.values()].reduce((n, set) => n + set.size, 0),
    releases: () => releases};
}

test('image hydration deduplicates requests and removes both listeners on success', () => {
  const h = imageHarness();
  for (let i = 0; i < 20; i++) h.context.ensureLazyImage(h.img);
  assert.equal(h.queue.length, 1);
  h.start();
  assert.equal(h.count(), 2);
  h.emit('load');
  assert.equal(h.count(), 0);
  assert.equal(h.releases(), 1);
  h.emit('error');
  assert.equal(h.timers.length, 0);
});

test('image timeout cleans listeners, and a network retry preserves a signed URL', () => {
  const h = imageHarness();
  h.context.ensureLazyImage(h.img); h.start();
  h.context.expire();
  assert.equal(h.count(), 0);
  h.context.ensureLazyImage(h.img); h.start();
  h.emit('error');
  assert.equal(h.count(), 0);
  assert.equal(h.timers.length, 1);
  h.timers.shift()(); h.start();
  assert.equal(h.img.src, 'https://media.test/image.jpg?signature=original');
  h.emit('load');
  assert.equal(h.count(), 0);
});

test('an image replaced while queued never starts the obsolete URL', () => {
  const h = imageHarness();
  h.context.ensureLazyImage(h.img);
  h.img.dataset.src = '/replacement.jpg';
  h.start();
  assert.equal(h.img.src, undefined);
  assert.equal(h.queue.length, 1);
  h.start();
  assert.equal(h.img.src, '/replacement.jpg');
});

test('playback respects hidden, offscreen, dragging and manually paused states', async () => {
  let blocked = false, resolvePlay, plays = 0;
  const preview = {isConnected: true, dataset: {keepPlaying: '1'}, classList: classes(),
    closest: selector => selector.includes('is-offscreen') && blocked ? {} : null};
  const context = vm.createContext({document: {visibilityState: 'visible'}, _viewportInteracting: false,
    CanvasStore: {isDragging: false}, VideoLightbox: {isOpen: () => false}, VideoUpscaleModal: {isOpen: () => false},
    elementNearViewport: () => true, Promise});
  vm.runInContext(['nodeVideoPlaybackBlocked', 'shouldNodeVideoAutoplay', 'requestNodeVideoPlay'].map(extract).join('\n'), context);
  assert.equal(context.shouldNodeVideoAutoplay(preview), true);
  for (const state of ['hidden', 'dragging', 'interacting', 'offscreen']) {
    context.document.visibilityState = state === 'hidden' ? 'hidden' : 'visible';
    context.CanvasStore.isDragging = state === 'dragging';
    context._viewportInteracting = state === 'interacting';
    blocked = state === 'offscreen';
    assert.equal(context.shouldNodeVideoAutoplay(preview), false, state);
  }
  blocked = false;
  const video = {paused: true, play() { plays++; return new Promise(resolve => {resolvePlay = resolve;}); },
    pause() { this.paused = true; }};
  for (let i = 0; i < 20; i++) context.requestNodeVideoPlay(preview, video);
  assert.equal(plays, 1);
  preview.dataset.userPaused = '1';
  video.paused = false;
  resolvePlay();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(video.paused, true);
  assert.equal(preview.classList.contains('is-playing'), false);
  assert.equal(preview._playRequest, null);
});
