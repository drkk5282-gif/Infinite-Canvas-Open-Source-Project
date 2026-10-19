const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/app.js'), 'utf8');

function setup() {
  const timers = new Map();
  let seq = 0, token = 'one';
  const context = {AbortController, Map, Promise,
    setTimeout(fn) {timers.set(++seq, fn); return seq;},
    clearTimeout(id) {timers.delete(id);},
    window: {CanvasAuth: {getToken: () => token}},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('const backgroundRefreshJobs'), source.indexOf('function loadAppConfig()')), context);
  return {run: context.runBackgroundRefresh, timers, setToken: value => token = value};
}

test('twenty overlapping refreshes share one job through completion, then allow fresh data', async () => {
  const h = setup();
  let resolve, calls = 0;
  const work = () => {calls++; return new Promise(r => resolve = r);};
  const jobs = Array.from({length: 20}, () => h.run('settings', work));
  await Promise.resolve();
  assert.equal(calls, 1);
  resolve('settings');
  assert.deepEqual(await Promise.all(jobs), Array(20).fill('settings'));
  assert.equal(h.timers.size, 0);
  const next = h.run('settings', work);
  await Promise.resolve(); resolve('updated');
  assert.equal(await next, 'updated'); assert.equal(calls, 2);
});

test('timed out fetch is aborted, releases the job and next refresh can retry', async () => {
  const h = setup();
  const job = h.run('app-config', signal => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error('timeout')), {once: true});
  }));
  await Promise.resolve();
  [...h.timers.values()][0]();
  await assert.rejects(job, /timeout/);
  assert.equal(h.timers.size, 0);
  assert.equal(await h.run('app-config', () => 'recovered'), 'recovered');
});

test('different accounts and resources do not share refresh responses', async () => {
  const h = setup();
  const old = h.run('settings', () => 'old');
  h.setToken('two');
  const current = h.run('settings', () => 'current');
  const config = h.run('app-config', () => 'config');
  assert.deepEqual(await Promise.all([old, current, config]), ['old', 'current', 'config']);
});
