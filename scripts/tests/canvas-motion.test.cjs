const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../frontend/js/canvas.js'), 'utf8');
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\nfunction ', start + 1));
}

test('indexed snapping preserves nearest edge, original tie order and coincident guides', () => {
  const c = vm.createContext({});
  vm.runInContext(['buildSnapAxisIndex', 'snapAxisLowerBound', 'pickIndexedSnap', 'addIndexedCoincidentGuides'].map(extract).join('\n'), c);
  let seed = 42;
  const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const targets = Array.from({length: 1200}, () => Math.round(random() * 4000 - 2000) / 4);
  targets.push(10, 8, 10, -50, 50);
  const index = c.buildSnapAxisIndex(targets);
  for (let i=0; i<600; i++) {
    const sources = Array.from({length: 3}, () => Math.round(random() * 4000 - 2000) / 4);
    const threshold = [4, 8, 8/0.3][i%3];
    let expected = null;
    for (const s of sources) for (const t of targets) {
      const dist = Math.abs(s-t);
      if (dist <= threshold && (!expected || dist < expected.dist)) expected = {dist, delta:t-s, guide:t};
    }
    const actual = c.pickIndexedSnap(sources, index, threshold);
    assert.deepEqual(actual && {dist:actual.dist, delta:actual.delta, guide:actual.guide}, expected);
    const guides = new Set();
    c.addIndexedCoincidentGuides(guides, sources, index, 0.6);
    assert.deepEqual([...guides].sort((a,b)=>a-b), [...new Set(targets.filter(t=>sources.some(s=>Math.abs(s-t)<0.6)))].sort((a,b)=>a-b));
  }
  assert.equal(c.pickIndexedSnap([9], c.buildSnapAxisIndex([10,8,10]), 1).guide, 10);
  assert.equal(c.pickIndexedSnap([0], [], 8), null);
});

test('snap lookup does not scan distant regions on a very large board', () => {
  const c = vm.createContext({});
  vm.runInContext(['buildSnapAxisIndex', 'snapAxisLowerBound', 'pickIndexedSnap'].map(extract).join('\n'), c);
  let reads = 0;
  const index = new Proxy(c.buildSnapAxisIndex(Array.from({length:100000}, (_,i)=>i*10)), {
    get(target,key) { if (/^\d+$/.test(String(key))) reads++; return target[key]; }
  });
  assert.equal(c.pickIndexedSnap([920001,920101,920201], index, 4).delta, -1);
  assert.ok(reads < 100, reads);
});

test('gesture culling matches geometry across regions and reuses bounds until board or selection changes', () => {
  const cls = (...initial) => { const set = new Set(initial); return {
    contains: name=>set.has(name), add: name=>set.add(name), remove: name=>set.delete(name)
  }; };
  let reads=0;
  const nodes = Array.from({length:600}, (_,i)=>({id:String(i),x:(i%30)*500-4000,y:Math.floor(i/30)*350-2000,w:220,h:180}));
  const wraps = nodes.map((n,i)=>({dataset:{id:n.id,...(i===599?{deferredNode:'1'}:{})},classList:cls('is-offscreen')}));
  wraps[0].classList.add('is-selected');
  const layer={children:wraps,childElementCount:wraps.length};
  const board={nodes};
  const c=vm.createContext({CanvasStore:{activeBoard:()=>board},document:{getElementById:()=>layer},
    nodeBounds:n=>{reads++; return {...n};},getViewportWorldRect:()=>c.viewport,
    scheduleCanvasBackgroundWork(){},cancelScheduledMediaRelease(){}, VIEWPORT_PRELOAD_MARGIN:980,INTERACT_CULL_CELL:800,
    _interactCullCache:null});
  vm.runInContext(['isNodeRectInViewport','buildInteractingCullCache','syncInteractingNodeCull'].map(extract).join('\n'),c);
  for(const [x,y,scale] of [[0,0,1],[8000,1000,0.3],[-4200,-2400,4],[18000,18000,1],[0,0,1]]) {
    c.viewport={x,y,w:1440/scale,h:900/scale}; c.syncInteractingNodeCull();
    for(let i=0;i<599;i++) {
      const expected=i===0 || c.isNodeRectInViewport(nodes[i],980,c.viewport);
      assert.equal(!wraps[i].classList.contains('is-offscreen'),expected,`node ${i} at ${x},${y}`);
    }
    assert.equal(wraps[599].dataset.deferredNode,'1');
  }
  assert.equal(reads,599);
  board.nodes=nodes.map(n=>({...n,x:n.x+50000}));
  c.syncInteractingNodeCull(); assert.equal(reads,1198);
  wraps[0].classList.remove('is-selected'); c._interactCullCache=null;
  c.syncInteractingNodeCull(); assert.equal(wraps[0].classList.contains('is-offscreen'),true);
});
