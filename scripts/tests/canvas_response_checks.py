"""Wire latency and generation confirmation checks on the fictional 600-node board."""


def check_responsiveness(page, verify=True):
    page.evaluate("""() => {
      clearTaskPolling(); CanvasStore.stopSync();
      CanvasStore.viewport = {x:60,y:90,scale:1}; applyViewport();
      setSelection(['large-0','large-1']); renderNodes({immediate:true});
    }""")
    page.wait_for_timeout(450)
    wire = page.evaluate("""async () => {
      const frame = () => new Promise(requestAnimationFrame);
      const original = getPortCenter;
      let reads = 0;
      getPortCenter = (...args) => { reads++; return original(...args); };
      try {
        ConnectionUI.startDragFrom('large-0', 300, 200);
        await frame(); await frame();
        const startReads = reads;
        for (let i=0;i<120;i++) window.dispatchEvent(new PointerEvent('pointermove', {
          clientX:500+i, clientY:400, buttons:1, bubbles:true
        }));
        let frames = 0;
        const point = screenToWorld(619,400);
        const anchor = ConnectionUI.dragAnchor;
        const expected = connectionPath(anchor.x,anchor.y,point.x,point.y);
        do { await frame(); frames++; } while (frames < 5 &&
          document.querySelector('.conn-pending')?.getAttribute('d') !== expected);
        const result = {samples:120, framesToLatestPoint:frames, extraAnchorReads:reads-startReads,
          pathCount:document.querySelectorAll('.conn-pending').length};
        ConnectionUI.cancel(); await frame(); await frame();
        connStaticDirty = false;
        schedulePendingLine();
        CanvasStore.activeBoard().connections.push({id:'test-refresh',from:'large-0',to:'large-2'});
        scheduleRenderConnections(true); await frame(); await frame();
        result.staticRefreshPreserved = !!document.querySelector('.conn-group[data-id="test-refresh"]');
        CanvasStore.activeBoard().connections = CanvasStore.activeBoard().connections.filter(c=>c.id!=='test-refresh');
        const count = CanvasStore.activeBoard().connections.length;
        const target = worldToScreen(...Object.values(original('large-2','in')));
        ConnectionUI.startDragFrom('large-0',300,200);
        window.dispatchEvent(new PointerEvent('pointercancel',{clientX:target.x,clientY:target.y,button:0}));
        result.cancelDidNotConnect = !ConnectionUI.dragging && CanvasStore.activeBoard().connections.length===count;
        ConnectionUI.startDragFrom('large-0',300,200);
        window.dispatchEvent(new Event('blur'));
        result.blurCleared = !ConnectionUI.dragging;
        ConnectionUI.cancel(); return result;
      } finally { getPortCenter = original; }
    }""")
    if verify:
        assert wire['framesToLatestPoint'] == 1, wire
        assert wire['extraAnchorReads'] == 0, wire
        assert wire['pathCount'] == 2, wire
        assert wire['staticRefreshPreserved'] and wire['cancelDidNotConnect'] and wire['blurCleared'], wire

    page.evaluate("""() => {
      clearSelection(); selectSingle('large-0');
      CanvasStore.updateNode('large-0',{status:'idle',channel:'myapi',model:'myapi_image',prompt:'测试描述'});
      renderNodes({immediate:true});
      window.__originalBake = ensureReferencedAiMarksBaked;
      ensureReferencedAiMarksBaked = () => new Promise(resolve => window.__finishPreflight=resolve);
      window.__genOriginal = generateForNode;
      generateForNode = (...args) => {
        window.__clickedAt=performance.now();
        window.__testSubmit = __genOriginal(...args).catch(e=>({error:e.message}));
        return __testSubmit;
      };
    }""")
    page.wait_for_timeout(450)
    page.locator('.btn-generate[data-node="large-0"]').click()
    page.wait_for_function("typeof __finishPreflight === 'function'")
    prepare = page.evaluate("""async () => {
      const button=()=>document.querySelector('.btn-generate[data-node="large-0"]');
      const result={immediateBusy:button().disabled && button().getAttribute('aria-busy')==='true'};
      renderNodes({immediate:true});
      result.remountKeepsBusy=button().disabled;
      const board=CanvasStore.activeBoard();
      const before=board.nodes[0].status;
      const activeId=CanvasStore.activeBoardId;
      CanvasStore.boards.push({id:'other-test',nodes:[],connections:[]});
      CanvasStore.activeBoardId='other-test';
      __finishPreflight([]); const submit=await __testSubmit;
      result.staleRequestRejected=!!submit?.error && board.nodes[0].status===before;
      CanvasStore.activeBoardId=activeId;
      CanvasStore.boards=CanvasStore.boards.filter(b=>b.id!=='other-test');
      result.lockReleased=!isGenPreparing('large-0');
      generateForNode=__genOriginal; ensureReferencedAiMarksBaked=__originalBake;
      renderNodes({immediate:true});
      return result;
    }""")
    if verify:
        assert all(prepare.values()), prepare
    page.evaluate("""() => {
      selectSingle('large-2');
      CanvasStore.updateNode('large-2',{status:'idle',prompt:'响应测试',channel:'myapi',model:'myapi_image'});
      renderNodes({immediate:true});
    }""")
    page.wait_for_timeout(450)
    submit_paint = page.evaluate("""async () => {
      const oldRun=runGenerateJob, oldTicker=ensureProgressTicker, oldBake=ensureReferencedAiMarksBaked;
      const originalRender=renderNodesImpl;
      let fullBoardRenders=0, renderMs=0;
      runGenerateJob=async()=>{}; ensureProgressTicker=()=>{}; ensureReferencedAiMarksBaked=async()=>[];
      renderNodesImpl=(opts)=>{const t=performance.now(); const r=originalRender(opts); fullBoardRenders++; renderMs+=performance.now()-t; return r;};
      try {
        const result=await generateForNode('large-2');
        await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
        return {submitted:!!result?.submitted,fullBoardRenders,fullBoardRenderMs:renderMs,
          busyButton:document.querySelector('.btn-generate[data-node="large-2"]').disabled};
      } finally {
        runGenerateJob=oldRun; ensureProgressTicker=oldTicker; ensureReferencedAiMarksBaked=oldBake; renderNodesImpl=originalRender;
        CanvasStore.updateNode('large-2',{status:'idle',_genPayload:null,genStartedAt:null});
        renderNodes({immediate:true});
      }
    }""")
    if verify:
        assert submit_paint['submitted'] and submit_paint['busyButton'], submit_paint
        assert submit_paint['fullBoardRenders'] == 0, submit_paint
    if not verify:
        return dict(wire=wire, prepare=prepare, submitPaint=submit_paint)

    # Exercise real panel buttons; generation itself is stubbed to avoid spending anything.
    page.evaluate("""() => {
      AgentPanel.open(); window.__agentGenerated=[];
      window.__oldPlan=AgentApi.plan; window.__oldAgentGen=AgentOps.generateNodes;
      AgentOps.generateNodes=async(ids)=>{__agentGenerated.push(...ids);return {count:ids.length}};
      AgentApi.plan=async()=>({reply:'准备改名并生成。',actions:[
        {type:'update_node',id:'large-0',customName:'确认目标'},
        {type:'generate_nodes',node_ids:['large-0']}
      ]});
    }""")
    page.locator('[data-agent-exec="auto"]').click()
    page.locator('#agent-input').fill('把这个节点改名为确认目标，并生成图片')
    page.locator('#agent-btn-send').click()
    page.locator('[data-agent-confirm="yes"]').last.wait_for(state='visible')
    assert page.evaluate("__agentGenerated.length") == 0
    assert page.evaluate("CanvasStore.activeBoard().nodes[0].customName") == '确认目标'
    page.locator('[data-agent-confirm="no"]').last.click()
    assert page.evaluate("__agentGenerated.length") == 0
    page.evaluate("""() => { AgentApi.plan=async()=>({reply:'准备生成。',actions:[{type:'generate_nodes',node_id:'large-0'}]}); }""")
    page.locator('#agent-input').fill('生成这个节点')
    page.locator('#agent-btn-send').click()
    page.locator('[data-agent-confirm="yes"]').last.wait_for(state='visible')
    page.locator('[data-agent-confirm="yes"]').last.click()
    page.wait_for_function('__agentGenerated.length===1')

    # A suggested choice selects a plan; it cannot silently authorize generation.
    page.evaluate("""() => { AgentApi.plan=async()=>({reply:'请选择生成目标。',actions:[],choices:[
      {id:'target',label:'生成确认目标',actions:[{type:'generate_nodes',node_ids:['large-0']}]}
    ]}); }""")
    page.locator('#agent-input').fill('帮我选择要生成的节点')
    page.locator('#agent-btn-send').click()
    page.locator('[data-agent-choice="0"]').last.click()
    page.locator('[data-agent-confirm="yes"]').last.wait_for(state='visible')
    assert page.evaluate('__agentGenerated.length') == 1
    page.evaluate("CanvasStore.updateNode('large-0',{prompt:'确认前重新编辑'});")
    page.locator('[data-agent-confirm="yes"]').last.click()
    page.wait_for_function("document.querySelector('#agent-panel').textContent.includes('生成目标或引用在确认前发生了变化')")
    assert page.evaluate('__agentGenerated.length') == 1
    page.evaluate("""() => {
      AgentApi.plan=__oldPlan; AgentOps.generateNodes=__oldAgentGen; AgentPanel.close();
      clearSelection(); CanvasStore.save();
    }""")

    # A durable agent job resumes after reload and still waits for confirmation.
    page.evaluate("""async () => {
      await CanvasStore.flushSave();
    }""")
    page.add_init_script("""
      localStorage.setItem('qinzi-agent-runs-v1:entry-test',JSON.stringify({
        'large-board':{jobId:'mock-resumed-agent',startedAt:Date.now()}
      }));
      document.addEventListener('DOMContentLoaded', () => {
        window.__resumedGenerated=[];
        AgentOps.generateNodes=async(ids)=>{__resumedGenerated.push(...ids);return {count:ids.length}};
        AgentApi.pollJob=async()=>{window.__resumedPolled=(window.__resumedPolled||0)+1;return {reply:'准备生成。',actions:[{type:'generate_nodes',node_ids:['large-0']}]}};
      },{once:true});
    """)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function("CanvasStore.activeBoard()?.nodes.length===600 && !document.documentElement.classList.contains('is-canvas-booting')")
    page.wait_for_function('window.__resumedPolled===1')
    assert page.evaluate('!AgentPanel.isOpen()'), 'Background resume should preserve closed panel'
    page.evaluate('AgentPanel.open(); clearTaskPolling(); CanvasStore.stopSync();')
    assert page.evaluate("localStorage.getItem('qinzi-agent-chats-v1:entry-test').includes('把这个节点改名为确认目标')"), 'Resume lost prior chat history'
    page.locator('[data-agent-confirm="yes"]').last.wait_for(state='visible')
    assert page.evaluate('__resumedGenerated.length') == 0
    page.locator('[data-agent-confirm="yes"]').last.click()
    page.wait_for_function('__resumedGenerated.length===1')
    page.evaluate('AgentPanel.close();')
    page.evaluate("""() => {
      setAgentPreferences({contextDetail:'compact', modelRecentMessages:4, memoryMaxChars:800});
      openMyApiSettings({kind:'agent'});
    }""")
    page.wait_for_function("document.querySelector('#agent-context-detail')?.value === 'compact'")
    assert page.locator('#agent-recent-messages').input_value() == '4'
    page.evaluate("document.getElementById('btn-myapi-cancel')?.click();")
    return dict(wire=wire, prepare=prepare, submitPaint=submit_paint,
                agent={'ordinaryAutoEdit':True,'skipSubmitsNothing':True,'confirmedSubmissions':1,
                       'choiceRequiresConfirmation':True,'changedTargetRejected':True,'resumedJobRequiresConfirmation':True,
                       'contextMemorySettings':True})
