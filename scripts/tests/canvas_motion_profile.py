"""Gesture costs on separated regions, with synthetic media and isolated APIs."""
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parents[2] / 'artifacts/canvas-drag-qa'


def check_motion_profile(page, verify=True):
    page.evaluate("""() => {
      clearTaskPolling(); CanvasStore.stopSync(); clearSelection();
      const board = CanvasStore.activeBoard();
      board.nodes.forEach((n,i) => {
        const region = Math.floor(i / 200), local = i % 200;
        n.x = region * 8000 + (local % 20) * 250;
        n.y = Math.floor(local / 20) * 200;
      });
      board.nodes[599].x = 6300; board.nodes[599].y = 200;
      window.__motionCalls = {bounds:0, culls:0, moveMs:[], cullMs:[]};
      const bounds = nodeBounds, cull = syncNodeViewportCull, move = applyNodeDragMove;
      nodeBounds = function(...args) { __motionCalls.bounds++; return bounds(...args); };
      syncNodeViewportCull = function(...args) { const t=performance.now(); __motionCalls.culls++;
        const r=cull(...args); __motionCalls.cullMs.push(performance.now()-t); return r; };
      applyNodeDragMove = function(...args) { const t=performance.now(); const r=move(...args);
        __motionCalls.moveMs.push(performance.now()-t); return r; };
    }""")
    cdp = page.context.new_cdp_session(page)
    cdp.send('Performance.enable')

    def perf():
        return {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}

    results = {}
    if verify:
        page.evaluate("""() => {
          const vp = getViewportEl(), grid=vp.querySelector('.canvas-grid');
          if(!grid || getComputedStyle(grid).pointerEvents !== 'none') throw Error('Grid intercepts input');
          const saved = {...CanvasStore.viewport};
          for(const scale of [0.3,1,2,4]) {
            CanvasStore.viewport={x:-137,y:83,scale}; applyViewportGrid();
            const step=24*scale, m=new DOMMatrixReadOnly(getComputedStyle(grid).transform);
            if(Math.abs(m.m41-((-137%step+step)%step))>0.001 || Math.abs(m.m42-((83%step+step)%step))>0.001)
              throw Error('Grid phase lost');
            if(Math.abs(parseFloat(getComputedStyle(grid).backgroundSize)-step)>0.001) throw Error('Grid scale lost');
          }
          CanvasPrefs.showGrid=false; applyViewportGrid();
          if(getComputedStyle(grid).display !== 'none') throw Error('Grid toggle broken');
          CanvasPrefs.showGrid=true; CanvasStore.viewport=saved; applyViewport();
          AlignGuides.render([{axis:'v',pos:120}]);
          const svg=document.getElementById('align-guides-layer'), line=svg.firstElementChild;
          const bounds=svg.getBoundingClientRect(), view=vp.getBoundingClientRect();
          if(bounds.width>view.width+1 || bounds.height>view.height+1) throw Error('Board-sized guide surface');
          AlignGuides.render([{axis:'v',pos:130}]);
          if(svg.firstElementChild!==line || line.getAttribute('x1')!=='130') throw Error('Guide remounted or stale');
          const mutations=new MutationObserver(()=>{}); mutations.observe(svg,{childList:true,subtree:true});
          AlignGuides.clear();
          AlignGuides.render([{axis:'h',pos:140},{axis:'v',pos:150}]);
          AlignGuides.clear();
          if(mutations.takeRecords().length || svg.firstElementChild!==line) throw Error('Guide changes invalidate surrounding nodes');
          mutations.disconnect();
          setSelection(['large-0','large-1'],null);
          startCanvasPanDrag(0,0);
          const bar=document.getElementById('multi-select-bar');
          if(getComputedStyle(bar).visibility!=='hidden') throw Error('Moving toolbar not deferred');
          finalizeCanvasDragInteraction({pushHistory:false});
          if(getComputedStyle(bar).visibility==='hidden') throw Error('Toolbar did not return');
          clearSelection();
        }""")
    for name, node_id, scale, pan in [
        ('denseDrag', 'large-0', 0.5, False),
        ('emptyDrag', 'large-599', 1, False),
        ('regionPan', 'large-0', 0.6, True),
    ]:
        page.evaluate("""({id,scale}) => {
          clearSelection(); const n=CanvasStore.activeBoard().nodes.find(n=>n.id===id);
          CanvasStore.viewport={x:100-n.x*scale, y:100-n.y*scale, scale};
          applyViewport(); renderNodes({immediate:true});
        }""", {'id': node_id, 'scale': scale})
        page.wait_for_function("""() => CanvasStore.activeBoard().nodes.filter(n=>isNodeInViewport(n,0))
          .every(n=>document.querySelector(`.node-wrap[data-id="${n.id}"]`)?.dataset.deferredNode !== '1')""")
        page.wait_for_timeout(700)
        box = page.locator(f'.node-wrap[data-id="{node_id}"] .node-preview').bounding_box()
        x, y = box['x'] + 40, box['y'] + 35
        page.mouse.move(x, y)
        page.mouse.down(button='middle' if pan else 'left')
        page.mouse.move(x + 12, y + 12)
        page.wait_for_function("CanvasStore.drag?.type === '" + ('pan' if pan else 'nodes') + "'")
        before = perf()
        result = page.evaluate("""async ({x,y,pan}) => {
          const calls=__motionCalls; calls.bounds=0; calls.culls=0; calls.moveMs=[]; calls.cullMs=[];
          const frames=[]; let prev; let guideMutations=0;
          const observer=new MutationObserver(records=>guideMutations+=records.length);
          observer.observe(document.getElementById('align-guides-layer'), {childList:true,subtree:true,attributes:true});
          const vx=CanvasStore.viewport.x, vy=CanvasStore.viewport.y;
          for(let i=0;i<90;i++) {
            const t=await new Promise(requestAnimationFrame); if(prev) frames.push(t-prev); prev=t;
            if(pan) {
              CanvasStore.viewport.x=vx-i*65;
              CanvasStore.viewport.y=vy+Math.sin(i/15)*30;
              applyViewport({lightweight:true});
            } else {
              handleNodeDragMove({clientX:x+20+i*3,clientY:y+20+Math.sin(i/10)*60,buttons:1});
            }
          }
          if(!pan) flushNodeDragMove(CanvasStore.drag);
          await new Promise(requestAnimationFrame); observer.disconnect();
          const summary=xs=>{const a=[...xs].sort((a,b)=>a-b);return {
            medianMs:a[Math.floor(a.length*.5)]||0,p95Ms:a[Math.floor(a.length*.95)]||0,maxMs:a.at(-1)||0};};
          return {frames:summary(frames),move:summary(calls.moveMs),cull:summary(calls.cullMs),
            bounds:calls.bounds,culls:calls.culls,guideMutations,
            deferred:document.querySelectorAll('[data-deferred-node="1"]').length};
        }""", {'x': x, 'y': y, 'pan': pan})
        after = perf()
        result['browser'] = {key: round((after[key] - before[key]) * (1000 if key.endswith('Duration') else 1), 3)
                             for key in ['LayoutCount', 'LayoutDuration', 'RecalcStyleCount', 'RecalcStyleDuration', 'TaskDuration']}
        page.mouse.up(button='middle' if pan else 'left')
        page.wait_for_function('!CanvasStore.drag && getMediaLoadPressure().hold === 0')
        page.wait_for_timeout(350)
        if verify:
            assert page.evaluate("!document.querySelector('.node-wrap.is-dragging')"), name
            if pan:
                assert result['bounds'] < 600 * 5, result
            else:
                assert result['bounds'] == 0, result
        if name == 'denseDrag':
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / 'dense.png'))
        results[name] = result
    page.wait_for_function("""() => CanvasStore.activeBoard().nodes.filter(n=>isNodeInViewport(n,0))
      .every(n=>document.querySelector(`.node-wrap[data-id="${n.id}"]`)?.dataset.deferredNode !== '1')""")
    page.screenshot(path=str(ARTIFACTS / 'motion.png'))
    cdp.detach()
    return results
