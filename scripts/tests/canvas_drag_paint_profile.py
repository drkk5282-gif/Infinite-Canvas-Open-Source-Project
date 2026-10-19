"""Keep the viewport stationary while dragging through 600 loaded overlapping cards."""
import json
from collections import Counter


def check_drag_paint_profile(page, output, verify=True, diagnose=False, cpu_rate=4):
    page.evaluate("""() => {
      clearTaskPolling(); CanvasStore.stopSync(); clearSelection();
      const board=CanvasStore.activeBoard();
      board.nodes.forEach((n,i)=>{n.x=(i%30)*140;n.y=Math.floor(i/30)*110;});
      CanvasStore.viewport={x:50,y:80,scale:0.3};
      CanvasPrefs.snapAlign=true; applyViewport(); renderNodes({immediate:true});
    }""")
    page.wait_for_function("document.querySelectorAll('[data-deferred-node=\"1\"]').length===0", timeout=60000)
    page.wait_for_function("""() => [...document.querySelectorAll('.node-preview-img')].length>=600 &&
      [...document.querySelectorAll('.node-preview-img')].every(img=>img.complete && img.naturalWidth>0)""", timeout=60000)
    page.wait_for_timeout(1800)
    page.evaluate("""() => {
      window.__dragOriginalNodes=[...document.querySelectorAll('#nodes-layer > .node-wrap')];
      window.__dragOriginalWires=[...document.querySelectorAll('#connections-layer > .conn-group')];
    }""")
    cdp=page.context.new_cdp_session(page)
    cdp.send('Performance.enable')
    # Four-times slower CPU exposes dense-area costs hidden by a fast desktop.
    cdp.send('Emulation.setCPUThrottlingRate', {'rate':cpu_rate})
    def metrics():
        return {m['name']:m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
    traces=[]
    cdp.on('Tracing.dataCollected', lambda event: traces.extend(event['value']))
    box=page.locator('.node-wrap[data-id="large-0"] .node-preview').bounding_box()
    x,y=box['x']+20,box['y']+20
    page.mouse.move(x,y); page.mouse.down(); page.mouse.move(x+15,y+15)
    page.wait_for_function("CanvasStore.drag?.type==='nodes'")
    categories = 'devtools.timeline'
    cdp.send('Tracing.start', {'categories':categories, 'transferMode':'ReportEvents'})
    before=metrics()
    result=page.evaluate("""async ({x,y}) => {
      const frames=[], viewport=JSON.stringify(CanvasStore.viewport); let prev;
      for(let i=0;i<90;i++) {
        const t=await new Promise(requestAnimationFrame); if(prev)frames.push(t-prev);prev=t;
        handleNodeDragMove({clientX:x+20+i*6,clientY:y+40+Math.sin(i/12)*90,buttons:1});
      }
      flushNodeDragMove(CanvasStore.drag); await new Promise(requestAnimationFrame);
      frames.sort((a,b)=>a-b);
      return {nodes:600,loadedImages:document.querySelectorAll('.node-preview-img.is-media-loaded').length,
        viewportStationary:viewport===JSON.stringify(CanvasStore.viewport),
        frames:{medianMs:frames[44],p95Ms:frames[Math.floor(frames.length*.95)],maxMs:frames.at(-1)}};
    }""", {'x':x,'y':y})
    after=metrics()
    result['cpuRate']=cpu_rate
    if diagnose:
        result['diagnostics']={}
        for mode in ['node-only','no-guides','no-wires','no-hit-testing','no-backdrop','hide-ports','contain-cards','no-static-transforms']:
            page.evaluate("""mode => {
              document.getElementById('diagnose-style')?.remove();
              window.__origSnap ||= AlignGuides.snapDragLight;
              window.__origWire ||= updateDragConnectionPaths;
              AlignGuides.snapDragLight=['node-only','no-guides'].includes(mode) ? ((d,dx,dy)=>({dx,dy})) : __origSnap;
              updateDragConnectionPaths=['node-only','no-wires'].includes(mode) ? (()=>{}) : __origWire;
              AlignGuides.clear();
              const css = {
                'no-hit-testing':'body.is-node-dragging #nodes-layer * {pointer-events:none!important}',
                'no-backdrop':'* {backdrop-filter:none!important;-webkit-backdrop-filter:none!important}',
                'hide-ports':'#nodes-layer .node-port,#nodes-layer .node-port-zone {display:none!important}',
                'contain-cards':'#nodes-layer .node-wrap {contain:layout style paint!important}',
                'no-static-transforms':'#nodes-layer * {transform:none!important;will-change:auto!important}'
              }[mode];
              if(css) {const style=document.createElement('style');style.id='diagnose-style';style.textContent=css;document.head.append(style);}
            }""",mode)
            page.wait_for_timeout(700)
            m0=metrics()
            page.evaluate("""async ({x,y})=>{
              for(let i=0;i<18;i++) {
                await new Promise(requestAnimationFrame);
                handleNodeDragMove({clientX:x+20+i*9,clientY:y+40+i*4,buttons:1});
              }
              flushNodeDragMove(CanvasStore.drag);await new Promise(requestAnimationFrame);
            }""",{'x':x,'y':y})
            m1=metrics()
            result['diagnostics'][mode]={key:round((m1[key]-m0[key])*1000,2) for key in ['LayoutDuration','RecalcStyleDuration','TaskDuration']}
    completed=[]
    cdp.on('Tracing.tracingComplete', lambda event:completed.append(True))
    cdp.send('Tracing.end')
    while not completed:
        page.wait_for_timeout(50)
    result['browser']={key:round((after[key]-before[key])*(1000 if key.endswith('Duration') else 1),3)
                       for key in ['LayoutCount','LayoutDuration','RecalcStyleCount','RecalcStyleDuration','TaskDuration']}
    paint_ids=Counter(e.get('args',{}).get('data',{}).get('nodeId') for e in traces if e.get('name')=='Paint')
    result['paintEvents']=sum(paint_ids.values())
    element_counts=sorted(e.get('args',{}).get('elementCount',0) for e in traces if e.get('name')=='UpdateLayoutTree')
    result['styleElements']={'median':element_counts[len(element_counts)//2],
                             'p95':element_counts[int(len(element_counts)*0.95)],'max':max(element_counts)}
    result['paintTargets']=[]
    for node_id,count in paint_ids.most_common(8):
        try:
            node=cdp.send('DOM.describeNode',{'backendNodeId':node_id})['node']
            result['paintTargets'].append({'tag':node['nodeName'],'attrs':node.get('attributes',[]),'count':count})
        except Exception:
            result['paintTargets'].append({'nodeId':node_id,'count':count})
    output.with_suffix('.trace.json').write_text(json.dumps({'traceEvents':traces}),encoding='utf-8')
    page.screenshot(path=str(output.with_suffix('.png')))
    page.evaluate("""() => {
      window.__releaseCalls=[];
      for(const name of ['finishNodeDrag','syncSelectionDOM','syncCanvasLayerBounds','renderStaticConnections','flushCanvasBackgroundWork',
        'ensureNodePanelMounted','renderNodePanel','hydrateNodeWrap','applyChromeCounterScale','applyPanelCompensation','syncMultiSelectBar','autoResizeAllPrompts']) {
        const original=window[name];window[name]=function(...args){const t=performance.now();const r=original(...args);
          __releaseCalls.push({name,ms:performance.now()-t});return r;};
      }
      window.__releaseFrames=[]; let last=performance.now();
      const tick=t=>{__releaseFrames.push(t-last);last=t;if(__releaseFrames.length<25)requestAnimationFrame(tick);};
      requestAnimationFrame(tick);
    }""")
    page.mouse.up()
    page.wait_for_function('__releaseFrames.length>=25')
    result['release']=page.evaluate('({calls:__releaseCalls,maxFrameMs:Math.max(...__releaseFrames)})')
    cdp.send('Emulation.setCPUThrottlingRate',{'rate':1})
    page.wait_for_function('!CanvasStore.drag && getMediaLoadPressure().hold===0')
    if verify:
        assert result['viewportStationary'] and result['loadedImages']==600,result
        page.evaluate("""() => {
          const nodes=[...document.querySelectorAll('#nodes-layer > .node-wrap')];
          const wires=[...document.querySelectorAll('#connections-layer > .conn-group')];
          if(nodes.length!==600 || nodes.some((n,i)=>n!==__dragOriginalNodes[i])) throw Error('Drag changed node order or identity');
          if(wires.some((n,i)=>n!==__dragOriginalWires[i]) || wires.length!==__dragOriginalWires.length) throw Error('Drag changed wire order or identity');
          if(document.querySelector('.drag-nodes-layer,.drag-connections')) throw Error('Drag layer survived release');
        }""")
    cdp.detach()
    if verify:
        result['cancellation']=check_drag_cancellation(page)
    return result


def check_drag_cancellation(page):
    """Cancel via native event handlers and immediately start another gesture."""
    result=page.evaluate("""async () => {
      CanvasPrefs.snapAlign=false;
      const node=CanvasStore.activeBoard().nodes.find(n=>n.id==='large-0');
      const wrap=document.querySelector('.node-wrap[data-id="large-0"]');
      const modes=['pointercancel','blur','overlay','pointercancel','blur','overlay'];
      CanvasHistory.reset();
      const out=[]; let expected={x:node.x,y:node.y};
      const event=(target,type,x,y,buttons=1)=>target.dispatchEvent(new PointerEvent(type,
        {bubbles:true,composed:true,clientX:x,clientY:y,buttons,button:0,pointerId:1,pointerType:'mouse',isPrimary:true}));
      // Consecutive gestures share a turn deliberately: pending release callbacks
      // must not clear, expand panels or resume media in a newer drag.
      for(const mode of modes) {
        const rect=wrap.querySelector('.node-preview').getBoundingClientRect();
        const x=rect.x+10,y=rect.y+10;
        event(wrap.querySelector('.node-preview'),'pointerdown',x,y);
        event(window,'pointermove',x+15,y+15);
        if(CanvasStore.drag?.type!=='nodes') throw Error('Cannot restart after '+mode);
        const d=CanvasStore.drag, start=d.startPositions[node.id], v=d.viewCache;
        const target={clientX:x+45,clientY:y+30,buttons:1};
        handleNodeDragMove(target);
        expected={x:start.x+(target.clientX-d.startX)/v.vs,y:start.y+(target.clientY-d.startY)/v.vs};
        const t=performance.now();
        if(mode==='pointercancel') event(window,'pointercancel',0,0,0);
        else if(mode==='blur') window.dispatchEvent(new Event('blur'));
        else releaseCanvasInteractionState();
        out.push({mode,finishMs:performance.now()-t});
        if(CanvasStore.drag || CanvasStore.isDragging || CanvasStore._primaryPointerDown ||
          document.querySelector('.drag-nodes-layer,.drag-connections') ||
          document.body.classList.contains('is-node-dragging')) throw Error('Stuck state after '+mode);
        if(Math.abs(node.x-expected.x)>.01 || Math.abs(node.y-expected.y)>.01) throw Error('Last queued position lost on '+mode);
        if(wrap.parentElement.id!=='nodes-layer' || wrap.style.transform) throw Error('Node not restored after '+mode);
        if(CanvasHistory.index!==out.length) throw Error('Cancellation lost undo entry');
      }
      await new Promise(r=>setTimeout(r,650));
      if(getMediaLoadPressure().hold!==0) throw Error('Cancelled gestures leaked media hold');
      const c=CanvasStore.activeBoard().connections.find(c=>c.from===node.id);
      const a=getPortCenterModel(c.from,'out'),b=getPortCenterModel(c.to,'in');
      if(document.querySelector(`.conn-line[data-id="${c.id}"]`).getAttribute('d')!==connectionPath(a.x,a.y,b.x,b.y))
        throw Error('Cancelled wire position was not restored');
      if(!wrap.classList.contains('is-selected') || wrap.querySelector('.node-prompt').offsetHeight<64)
        throw Error('Cancelled drag did not restore editor');
      await CanvasStore.flushSave();
      const final={...expected};
      if(!CanvasHistory.undo()) throw Error('Cannot undo cancellation');
      const undone=CanvasStore.activeBoard().nodes.find(n=>n.id===node.id);
      if(undone.x===final.x && undone.y===final.y) throw Error('Undo did not restore pre-drag position');
      if(!CanvasHistory.redo()) throw Error('Cannot redo cancellation');
      const redone=CanvasStore.activeBoard().nodes.find(n=>n.id===node.id);
      if(redone.x!==final.x || redone.y!==final.y) throw Error('Redo lost cancelled position');
      return {gestures:out,finalPosition:expected,mediaHold:getMediaLoadPressure().hold,editorRestored:true};
    }""")
    return result
