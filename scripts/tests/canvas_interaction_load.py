"""Interaction stress checks, imported by the isolated canvas route runner."""
import struct
import zlib


def preview_fixture():
    """A synthetic 1024x576 image; no real assets, accounts or paid jobs."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    row = b'\0' + bytes(c for x in range(1024) for c in (x % 256, (x * 7) % 256, 180))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1024, 576, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * 576)) + chunk(b'IEND', b''))


def check_interaction_load(page, verify=True):
    page.evaluate("""() => {
      clearTaskPolling(); CanvasStore.stopSync(); selectSingle('large-0');
      window.__load = {mounts:0, starts:0, staticPaints:0, pathQueries:0, frames:[], moveMs:[], longTasks:[]};
      for (const [name, metric] of [['mountDeferredNode','mounts'], ['renderStaticConnections','staticPaints'],
        ['getPortCenterForConnections','pathQueries']]) {
        const original = window[name]; window[name] = function(...args) { __load[metric]++; return original(...args); };
      }
      const move = applyNodeDragMove;
      applyNodeDragMove = function(e) { const t = performance.now(); const result = move(e);
        __load.moveMs.push(performance.now()-t); return result; };
      window.__loadObserver = new PerformanceObserver(list => {
        for (const e of list.getEntries()) if (CanvasStore.drag || document.querySelector('#viewport.is-interacting'))
          __load.longTasks.push(e.duration);
      }); __loadObserver.observe({type:'longtask'});
    }""")
    page.wait_for_timeout(500)
    box = page.locator('.node-wrap[data-id="large-0"] .node-preview').bounding_box()
    x, y = box['x'] + 80, box['y'] + 60
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 35, y + 20, steps=4)
    page.wait_for_function("CanvasStore.drag?.type === 'nodes'")
    drag = page.evaluate("""async ({x,y}) => {
      const live = CanvasStore.activeBoard().nodes[0];
      const wrap = document.querySelector('.node-wrap[data-id="large-0"]');
      const before = {...__load}; let last = performance.now();
      // A real terminal task response during the gesture must update data now,
      // without replacing the wrapper whose pointer capture and paths are cached.
      live.taskId = 'interaction-job'; live.status = 'running';
      syncNodeFromTask({id:live.taskId, board_id:'large-board', node_id:live.id,
        status:'completed', image_url:'/fixtures/changed-during-drag.png'});
      for (let i=0; i<60; i++) {
        await new Promise(requestAnimationFrame);
        const t = performance.now(); __load.frames.push(t-last); last=t;
        handleNodeDragMove({clientX:x+40+i*2, clientY:y+25+i, buttons:1});
        if (i%3 === 0) {
          scheduleMediaLoad(done => { __load.starts++; done(); });
          renderNodes({immediate:true}); syncNodeViewportCull(); scheduleRenderConnections(true);
        }
      }
      flushNodeDragMove(CanvasStore.drag);
      return {mounts:__load.mounts-before.mounts, starts:__load.starts-before.starts,
        staticPaints:__load.staticPaints-before.staticPaints, pathQueries:__load.pathQueries-before.pathQueries,
        sameWrapper:wrap === document.querySelector('.node-wrap[data-id="large-0"]'),
        updated:live.imageUrl === '/fixtures/changed-during-drag.png', x:live.x, y:live.y};
    }""", {'x': x, 'y': y})
    page.mouse.up()
    page.wait_for_function("!CanvasStore.drag && getMediaLoadPressure().hold === 0 && __load.starts >= 20")
    page.wait_for_function("document.querySelector('.node-wrap[data-id=\"large-0\"] img')?.getAttribute('data-full-src')?.includes('changed-during-drag') || [...document.querySelectorAll('.node-wrap[data-id=\"large-0\"] img')].some(i=>(i.src+i.dataset.src).includes('changed-during-drag'))")
    page.evaluate("""() => {
      const c = CanvasStore.activeBoard().connections[0];
      const a = getPortCenterModel(c.from,'out'), b = getPortCenterModel(c.to,'in');
      if (document.querySelector('.conn-line[data-id="wire-0"]').getAttribute('d') !== connectionPath(a.x,a.y,b.x,b.y))
        throw Error('Final drag connection did not catch up');
    }""")
    # Bring hundreds of previously deferred cards through the warm ring. The
    # pan/zoom lifecycle is shared with native pointer and wheel handlers.
    pan = page.evaluate("""async () => {
      setSelection([], null); const before = {...__load};
      startCanvasPanDrag(700, 450); let last = performance.now();
      for (let i=0; i<60; i++) {
        await new Promise(requestAnimationFrame);
        const t=performance.now(); __load.frames.push(t-last); last=t;
        CanvasStore.viewport = {x:60-i*50, y:90-i*18, scale:1-i/100};
        markViewportInteracting(); applyViewport({lightweight:true});
        if (i%3 === 0) {
          scheduleMediaLoad(done=>{__load.starts++; done();});
          renderNodes({immediate:true}); syncNodeViewportCull();
        }
      }
      const during = {mounts:__load.mounts-before.mounts, starts:__load.starts-before.starts};
      finalizeCanvasDragInteraction({pushHistory:false}); return during;
    }""")
    page.wait_for_function("!document.querySelector('#viewport.is-interacting') && getMediaLoadPressure().hold === 0 && __load.starts >= 40")
    page.wait_for_function("""() => {
      const visible = CanvasStore.activeBoard().nodes.filter(n=>isNodeInViewport(n,0));
      return visible.length > 0 && visible.every(n=>
        document.querySelector(`.node-wrap[data-id="${n.id}"]`)?.dataset.deferredNode !== '1');
    }""")
    bulk = page.evaluate("""() => {
      const before = document.querySelectorAll('.node-preview-col').length;
      setSelection(CanvasStore.activeBoard().nodes.map(n=>n.id), null);
      const result = {newCards:document.querySelectorAll('.node-preview-col').length-before,
        selected:getSelectedIds().length, deferred:document.querySelectorAll('[data-deferred-node="1"]').length};
      setSelection([],null); return result;
    }""")
    # Interruption releases exactly the gesture's hold, preserving a lightbox hold.
    released = page.evaluate("""async () => {
      holdMediaLoadScheduler(); startCanvasPanDrag(10,10);
      window.dispatchEvent(new Event('blur'));
      await new Promise(r=>setTimeout(r,500));
      const nestedHold = getMediaLoadPressure().hold;
      releaseMediaLoadScheduler();
      return {nestedHold, finalHold:getMediaLoadPressure().hold, drag:!!CanvasStore.drag};
    }""")
    metrics = page.evaluate("""() => {
      __loadObserver.disconnect();
      const summary = xs => {const a=[...xs].sort((a,b)=>a-b); return {p95Ms:a[Math.floor(a.length*.95)]||0, maxMs:a.at(-1)||0};};
      return {frames:summary(__load.frames), nodeMove:summary(__load.moveMs), longTasks:__load.longTasks,
        mountedCards:document.querySelectorAll('.node-preview-col').length};
    }""")
    if verify:
        assert drag['starts'] == drag['mounts'] == drag['staticPaints'] == drag['pathQueries'] == 0, drag
        assert drag['sameWrapper'] and drag['updated'] and drag['x'] > 0 and drag['y'] > 0, drag
        assert pan['starts'] == pan['mounts'] == 0, pan
        assert bulk['newCards'] < 30 and bulk['deferred'] > 200 and bulk['selected'] == 600, bulk
        assert released == {'nestedHold': 1, 'finalHold': 0, 'drag': False}, released
    return dict(drag=drag, panZoom=pan, bulkSelection=bulk, interrupted=released, **metrics)
