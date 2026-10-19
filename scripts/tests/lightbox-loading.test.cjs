const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/canvas.js'), 'utf8');

function harness() {
  let now = 1000, nextTimer = 0, open = true;
  const timers = new Map(), listeners = new Map();
  const video = {
    src: '', dataset: {}, readyState: 0, error: null, loads: 0,
    getAttribute: () => video.src,
    removeAttribute: () => {video.src = '';},
    load: () => {video.loads++;}, pause() {},
    addEventListener(type, fn) {if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn);},
    removeEventListener(type, fn) {listeners.get(type)?.delete(fn);},
  };
  const context = {
    URL, Date: {now: () => now}, location: {origin: 'http://test'},
    setTimeout(fn, ms) {const id = ++nextTimer; timers.set(id, {fn, at: now + ms}); return id;},
    clearTimeout: id => timers.delete(id), queueMicrotask,
    uploadMediaPath: url => url.startsWith('/uploads/') ? url : '',
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function lightboxVideoMatchesUrl('), source.indexOf('function warmVideoLightboxNode(')) +
    source.slice(source.indexOf('const VideoLightbox = {'), source.indexOf('function getBoardVideoNodes(')), context);
  const box = context.VideoLightbox;
  let retries = 0;
  Object.assign(box, {video: () => video, isOpen: () => open, retryVideoLoad: () => retries++,
    currentNode: () => ({id: 'test'}), applyShellAspect() {}, setLoading() {}, syncMeta() {},
    syncPlayUi() {}, syncTimeUi() {}, playOnShow() {}});
  const advance = ms => {
    const end = now + ms;
    while (true) {
      const next = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].fn();
    }
    now = end;
  };
  return {box, video, timers, advance, retries: () => retries,
    close: () => {open = false;}, emit: type => [...(listeners.get(type) || [])].forEach(fn => fn())};
}

test('progressing download survives the initial timeout, but idle download retries', () => {
  const h = harness();
  h.box.mountVideo('/uploads/test.mp4');
  h.advance(11000); h.emit('progress'); h.advance(11000);
  assert.equal(h.retries(), 0);
  h.advance(1001);
  assert.equal(h.retries(), 1);
  assert.equal(h.timers.size, 0);
});

test('progress events cannot postpone recovery forever', () => {
  const h = harness();
  h.box.mountVideo('/uploads/test.mp4');
  for (let i = 0; i < 11; i++) {h.advance(5000); h.emit('progress');}
  assert.equal(h.retries(), 0);
  h.advance(5000);
  assert.equal(h.retries(), 1);
});

test('source changes do not turn intentional aborts into failed retries', () => {
  const h = harness();
  h.box.mountVideo('/uploads/first.mp4');
  h.box.beginLoadCycle();
  h.box.mountVideo('/uploads/second.mp4');
  h.emit('abort');
  assert.equal(h.retries(), 0);
  assert.equal(h.video.loads, 2);
  h.video.readyState = 1; h.emit('loadedmetadata'); h.advance(60000);
  assert.equal(h.retries(), 0);
  assert.equal(h.box._loadReady, true);
});

test('first retry preserves the URL; repeated local failure can bypass cache; signed remote URLs stay intact', () => {
  const h = harness();
  h.box.mountVideo('/uploads/test.mp4');
  h.box.mountVideo('/uploads/test.mp4');
  assert.equal(h.video.src, '/uploads/test.mp4');
  h.box.mountVideo('/uploads/test.mp4');
  assert.match(h.video.src, /_vl_retry=/);
  const signed = 'https://media.test/clip.mp4?signature=unchanged';
  h.box.mountVideo(signed);
  assert.equal(h.video.src, signed);
});

test('closed lightbox releases retained video after 30 seconds; reopening cancels release', () => {
  const h = harness();
  h.video.src = '/uploads/test.mp4'; h.close();
  h.box.retainVideoBriefly(); h.advance(29999);
  assert.equal(h.video.src, '/uploads/test.mp4');
  h.advance(1); assert.equal(h.video.src, '');
  h.video.src = '/uploads/reopen.mp4'; h.box.retainVideoBriefly();
  h.box.beginLoadCycle(); h.advance(30000);
  assert.equal(h.video.src, '/uploads/reopen.mp4');
});
