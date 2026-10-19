const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(nodes = [], responses = []) {
  const board = { id: 'board', name: '测试画布', nodes, connections: [] };
  const calls = [], updates = [], generated = [], created = [];
  const context = vm.createContext({ console, setTimeout, clearTimeout, Map, Set });
  Object.assign(context, {
    CanvasStore: { activeBoard: () => board, selectedIds: [] },
    AgentApi: { async plan(text, options) { calls.push({ text, options }); return responses.shift(); } },
    AgentOps: {
      updateNode(id, patch) { updates.push({ id, patch }); },
      createNodesFromItems(items, nodeType) {
        created.push(...items);
        const ids = items.map((item, i) => `created-${i}`);
        board.nodes.push(...items.map((item, i) => ({ id: ids[i], customName: item.name, prompt: item.prompt, type: nodeType })));
        return { ids, count: ids.length, nodeType };
      },
      async generateNodes(ids) { generated.push(...ids); return { count: ids.length }; },
    },
  });
  context.window = context;
  for (const name of ['runner', 'intent', 'panel']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, `../../frontend/js/agent/${name}.js`), 'utf8'), context);
  }
  const run = (message, opts = {}) => context.AgentIntent.runIntent({ payload: { text: message, displayText: message } }, opts);
  return { context, run, board, calls, updates, generated, created };
}

test('focused context sends target details and all identities without unrelated bodies', () => {
  const nodes = Array.from({length: 100}, (_, i) => ({id: `n-${i}`, customName: `场景${i}号`, type: 'image',
    prompt: `正文${i}：` + '长提示词'.repeat(300)}));
  const h = harness(nodes);
  h.context.CanvasStore.selectedIds = ['n-99'];
  const packed = h.context.AgentRunner.buildCanvasContext({query: '把选中节点改名为餐厅'});
  assert.equal(packed.node_index.length, 100);
  assert.equal(packed.nodes.length, 1);
  assert.equal(packed.nodes[0].id, 'n-99');
  assert.equal(packed.nodes[0].prompt, nodes[99].prompt);
  assert.equal(packed.omitted_detail_ids.length, 99);
  const broad = h.context.AgentRunner.buildCanvasContext({query: '提取全部场景里的提示词'});
  assert.ok(broad.nodes.length > 1, 'explicit full-board questions must not become selected-only');
});

test('aborting while a model reply arrives prevents auto actions', async () => {
  const h = harness([{id:'r', type:'image', customName:'餐厅'}]);
  const ac = new AbortController();
  h.context.AgentApi.plan = async () => {
    ac.abort();
    return {reply:'准备修改。', actions:[{type:'update_node', id:'r', prompt:'新提示词'}]};
  };
  await assert.rejects(h.run('修改餐厅提示词', {execMode:'auto', signal:ac.signal}), {name:'AbortError'});
  assert.equal(h.updates.length, 0);
});

test('aborting a generation batch stops subsequent submissions', async () => {
  const h = harness([{id:'a', type:'image'}, {id:'b', type:'image'}]);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js/agent/ops.js'), 'utf8'), h.context);
  const calls = [], ac = new AbortController();
  h.context.generateForNode = async (id) => { calls.push(id); ac.abort(); return {submitted:true}; };
  await assert.rejects(h.context.AgentOps.generateNodes(['a','b'], {signal:ac.signal}), {name:'AbortError'});
  assert.deepEqual(calls, ['a']);
});

test('repeated targets in a batch are submitted once', async () => {
  const h = harness([{id:'a', type:'image'}]);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js/agent/ops.js'), 'utf8'), h.context);
  const calls = [];
  h.context.generateForNode = async (id) => { calls.push(id); return {submitted:true}; };
  const result = await h.context.AgentOps.generateNodes(['a','a']);
  assert.equal(result.count, 1);
  assert.deepEqual(calls, ['a']);
});

test('read-only extraction preserves specific clarification choices and full answer text', async () => {
  const h = harness([], [{ reply: '要提取哪部分？', actions: [], choices: [
    { id: 'people', label: '剧情人物', message: '提取全文剧情里的人物，包含临时出场人物，只用文字列出。' },
    { id: 'reference', label: '参考图人物', message: '只提取需要单独制作参考图的人物。' },
  ] }]);
  const out = await h.run('提取人物');
  assert.equal(out.pendingChoices.length, 3);
  assert.equal(out.pendingChoices[0].message, '提取全文剧情里的人物，包含临时出场人物，只用文字列出。');
  assert.equal(out.actions.length, 0);
});

test('ambiguous missing-media request never guesses the first three empty nodes', async () => {
  const h = harness(['客厅', '茶水间', '合作餐厅'].map((customName, i) => ({ id: `${i}`, customName, type: 'image' })), [{
    reply: '是哪个场景没图？', actions: [], choices: [{ label: '合作餐厅' }, { label: '茶水间' }],
  }]);
  const out = await h.run('还是没有图', { execMode: 'auto' });
  assert.equal(h.generated.length, 0);
  assert.equal(out.pendingChoices.length, 3);
});

test('compound extraction plus creation survives local read-only guard', async () => {
  const h = harness([], [{ reply: '准备创建场景并填提示词。', actions: [{ type: 'create_nodes', node_type: 'image', items: [{ name: '餐厅', prompt: '提示词' }] }], choices: [] }]);
  const out = await h.run('提取一下场景并创建对应节点，里面填上提示词');
  assert.equal(out.pendingActions.length, 1);
  assert.equal(h.created.length, 0);
});

test('negative create instruction does not block requested update', async () => {
  const h = harness([{ id: 'r', customName: '餐厅', type: 'image' }], [{ reply: '准备更新餐厅。', actions: [{ type: 'update_node', id: 'r', prompt: '新的提示词' }], choices: [] }]);
  const out = await h.run('不要新建，更新已有节点提示词');
  assert.equal(out.pendingActions[0].type, 'update_node');
});

test('Ask mode strips mutations while keeping useful options', async () => {
  const h = harness([], [{ reply: '想看哪种提取结果？', actions: [], choices: [{ label: '场景列表', actions: [{ type: 'create_nodes', items: [] }] }, { label: '人物列表' }] }]);
  const out = await h.run('提取内容', { roleMode: 'ask' });
  assert.equal(out.pendingChoices.length, 3);
  assert.ok(out.pendingChoices.every((c) => c.actions.length === 0));
});

test('canvas context includes nodes after 80 and complete document endings', () => {
  const nodes = Array.from({ length: 100 }, (_, i) => ({ id: `${i}`, customName: `场景${i}`, type: 'video', prompt: '开头' + '正文'.repeat(6000) + `结尾${i}` }));
  const h = harness(nodes);
  const packed = h.context.AgentRunner.buildCanvasContext();
  assert.equal(packed.node_index.length, 100);
  assert.equal(packed.nodes.length, 100);
  assert.ok(packed.nodes[99].body.endsWith('结尾99'));
});

test('old document and multiple document versions remain intact in model history', () => {
  const h = harness();
  const doc1 = '# 前一章\n' + '内容'.repeat(10000) + '关键尾注：不单独制作参考图';
  const doc2 = '# 当前章\n' + '内容'.repeat(1000) + '连续性更正';
  const rows = [{ role: 'user', content: doc1 }, { role: 'user', content: doc2 }, ...Array.from({ length: 12 }, () => ({ role: 'assistant', content: '收到' })), { role: 'user', content: '按照全文提取' }];
  const packed = h.context.AgentPanel.buildCompactModelContext(rows);
  assert.equal(packed.history[0].content, doc1);
  assert.equal(packed.history[1].content, doc2);
  assert.ok(!packed.history.some((row) => row.content === '按照全文提取'));
});

test('reference documents stay separate from raw user intent and are not truncated', async () => {
  const h = harness([{ id: 'doc', customName: '场景文档', type: 'text', resultText: '正文'.repeat(6000) + '禁止给服务员单独制图' }], [{ reply: '人物只有姜眠。', actions: [], choices: [] }]);
  const message = h.context.AgentIntent.expandMentionsIntoMessage('提取人物 @[场景文档](doc)', [{ id: 'doc' }]);
  assert.ok(message.endsWith('禁止给服务员单独制图'));
  await h.context.AgentIntent.runIntent({ payload: { text: message, displayText: '提取人物' } });
  assert.equal(h.calls[0].options.userIntent, '提取人物');
  assert.equal(h.calls[0].options.roleMode, 'agent');
  assert.ok(!h.calls[0].text.includes('[模式:'));
});

test('explicit short prompt and negative prompt are never replaced with full source document', async () => {
  const h = harness([{ id: 'r', customName: '合作餐厅', type: 'image' }]);
  h.context.AgentPanel.getUserScriptSources = () => [{ id: 'doc', body: '# 场景设计提示词\n' + '整篇原文'.repeat(500) }];
  const prompt = '安静餐厅，无人物。\n\n负面提示词：禁止多余餐桌。';
  await h.context.AgentRunner.runActions([{ type: 'update_node', id: 'r', prompt }, { type: 'create_nodes', node_type: 'image', items: [{ name: '办公室', prompt: '办公室空景' }] }]);
  assert.equal(h.updates[0].patch.prompt, prompt);
  assert.equal(h.created[0].prompt, '办公室空景');
});

test('new-node generation uses only explicitly created targets', async () => {
  const h = harness([{ id: 'old', type: 'image', customName: '旧场景' }]);
  await h.context.AgentRunner.runActions([{ type: 'create_nodes', node_type: 'image', items: [{ name: '新场景', prompt: '场景提示词' }] }, { type: 'generate_nodes', target: 'created' }], {generationConfirmed:true});
  assert.deepEqual(h.generated, ['created-0']);
});

test('missing generation targets fail visibly without generating arbitrary nodes', async () => {
  const h = harness([{ id: 'old', type: 'image' }]);
  const out = await h.context.AgentRunner.runActions([{ type: 'generate_nodes', node_ids: [] }]);
  assert.equal(h.generated.length, 0);
  assert.ok(out.logs.some((line) => line.includes('没有明确的生成目标')));
});

test('documents survive persisted history cap and choices keep full semantics on reload', () => {
  const h = harness();
  const content = '原始文档'.repeat(500);
  const history = [{ role: 'user', content }, ...Array.from({ length: 100 }, () => ({ role: 'assistant', content: '短回复' })),
    { role: 'assistant', kind: 'plan', items: ['提取范围'], pendingChoices: [{ id: 'a', label: '人物', message: '提取所有人物，包括不需要参考图的人物。', actions: [] }] }];
  const stored = JSON.parse(JSON.stringify(h.context.AgentPanel.retainSourceHistory(history)));
  const reloaded = h.context.AgentPanel.retainSourceHistory(stored);
  assert.equal(reloaded[0].content, content);
  assert.equal(reloaded.at(-1).pendingChoices[0].message, '提取所有人物，包括不需要参考图的人物。');
});

test('failed prompt update stops dependent generation', async () => {
  const h = harness([{ id: 'r', type: 'image' }]);
  h.context.AgentOps.updateNode = () => { throw new Error('节点已删除'); };
  const out = await h.context.AgentRunner.runActions([{ type: 'update_node', id: 'r', prompt: '新提示词' }, { type: 'generate_nodes', node_ids: ['r'] }]);
  assert.equal(h.generated.length, 0);
  assert.ok(out.logs.some((line) => line.includes('已停止后续操作')));
});

test('generation submission errors are returned instead of swallowed', async () => {
  const h = harness([{ id: 'r', type: 'image' }]);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js/agent/ops.js'), 'utf8'), h.context);
  h.context.generateForNode = async (id) => { if (id === 'bad') throw new Error('模型未配置'); return { submitted: true }; };
  const out = await h.context.AgentOps.generateNodes(['r', 'bad']);
  assert.equal(out.count, 1);
  assert.equal(out.failed[0].id, 'bad');
  assert.equal(out.failed[0].error, '模型未配置');
});

function realOps(nodes) {
  const h = harness(nodes);
  h.context.CanvasStore.addNode = () => {};
  h.context.CanvasStore.updateNode = (id, patch) => Object.assign(h.board.nodes.find(n => n.id === id), patch);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js/agent/ops.js'), 'utf8'), h.context);
  return h;
}

test('precise replacement preserves paragraphs, references and literal replacement characters', () => {
  const body = '第一行 春天\n\n角色@ref:person 保持原文';
  const h = realOps([{ id:'text', type:'text', resultText:body, prompt:body }]);
  h.context.AgentOps.replaceNodeText('text', {field:'resultText', find:'春天', replace:'夏天 $&'});
  assert.equal(h.board.nodes[0].resultText, '第一行 夏天 $&\n\n角色@ref:person 保持原文');
  assert.equal(h.board.nodes[0].prompt, h.board.nodes[0].resultText);
});

test('ambiguous or stale replacement is rejected without modifying the node', () => {
  const h = realOps([{id:'n', type:'image', prompt:'春天\n春天'}]);
  assert.throws(() => h.context.AgentOps.replaceNodeText('n', {find:'春天', replace:'夏天'}), /多次/);
  assert.throws(() => h.context.AgentOps.replaceNodeText('n', {find:'秋天', replace:'夏天'}), /未找到/);
  assert.equal(h.board.nodes[0].prompt, '春天\n春天');
  h.context.AgentOps.replaceNodeText('n', {find:'春天', replace:'夏天', all:true});
  assert.equal(h.board.nodes[0].prompt, '夏天\n夏天');
});

test('local edits preserve literal markup and voice descriptions stay separate from output', () => {
  const h = realOps([{id:'n', type:'image', prompt:String.raw`保留\n和<br> 春天`},
    {id:'voice', type:'voice-text', prompt:'音色生成指令', resultText:'春天的声音'}]);
  h.context.AgentOps.replaceNodeText('n', {find:'春天', replace:'夏天'});
  assert.equal(h.board.nodes[0].prompt, String.raw`保留\n和<br> 夏天`);
  h.context.AgentOps.replaceNodeText('voice', {field:'resultText', find:'春天', replace:'夏天'});
  assert.equal(h.board.nodes[1].prompt, '音色生成指令');
  assert.equal(h.board.nodes[1].resultText, '夏天的声音');
});

test('image edits preserve exact source and target, and reject changed originals', async () => {
  const h = realOps([{id:'img', type:'image', imageUrl:'/original.png', status:'completed'}]);
  const calls = [];
  h.context.generateForNode = async (...args) => { calls.push(args); return {submitted:true}; };
  await h.context.AgentOps.editImage('img', {instruction:'把上衣改成红色', source_url:'/original.png'});
  assert.equal(calls[0][0], 'img');
  assert.equal(calls[0][1].imageEdit.sourceUrl, '/original.png');
  assert.equal(h.board.nodes[0].imageUrl, '/original.png');
  await assert.rejects(h.context.AgentOps.editImage('img', {instruction:'改色', source_url:'/old.png'}), /图片已变化/);
  assert.equal(calls.length, 1);
});

test('unsent image edits restore reference node type and report failure', async () => {
  const h = realOps([{id:'img', type:'ref-image', imageUrl:'/original.png', status:'completed'}]);
  h.context.generateForNode = async () => undefined;
  await assert.rejects(h.context.AgentOps.editImage('img', {instruction:'修改招牌'}), /未提交/);
  assert.equal(h.board.nodes[0].type, 'ref-image');
  assert.equal(h.board.nodes[0].imageUrl, '/original.png');
});

test('planned edits cannot overwrite intervening edits or operate on another board', async () => {
  const h = harness([{id:'n', type:'image', prompt:'旧提示词'}], [{reply:'准备修改。', actions:[{type:'update_node', id:'n', prompt:'模型修改'}]}]);
  const plan = await h.run('修改提示词');
  h.board.nodes[0].prompt = '用户新编辑';
  let result = await h.context.AgentRunner.runActions(plan.pendingActions);
  assert.match(result.logs[0], /发生了变化/);
  assert.equal(h.updates.length, 0);
  h.board.id = 'other';
  result = await h.context.AgentRunner.runActions(plan.pendingActions);
  assert.match(result.logs[0], /画板已切换/);
});

test('large context keeps full index and targeted document ending without sending unrelated megabytes', () => {
  const h = harness(Array.from({length:600}, (_, i) => ({id:`n${i}`, type:'text', customName:`材料【${i}】`, resultText:'原文'.repeat(2000)+`尾注${i}`})));
  const packed = h.context.AgentRunner.buildCanvasContext({query:'修改材料【599】里的文字', mentionedIds:['n599']});
  assert.equal(packed.node_index.length, 600);
  assert.ok(packed.nodes[0].body.endsWith('尾注599'));
  assert.ok(packed.omitted_detail_ids.length > 500);
  assert.ok(JSON.stringify(packed).length < 700000);
  assert.equal(packed.mentioned_ids[0], 'n599');
});

test('look then edit is an edit and AI free-form questions remain free-form', async () => {
  const h = harness([{id:'n', type:'image'}], [
    {reply:'准备替换文字。', actions:[{type:'replace_node_text', id:'n', field:'prompt', find:'春天', replace:'夏天'}]},
    {reply:'招牌要换成哪几个字？请给出准确文字。', decision:'clarify', actions:[], choices:[]},
  ]);
  const plan = await h.run('看看这个，把春天改成夏天');
  assert.equal(plan.pendingActions[0].type, 'replace_node_text');
  const question = await h.run('换招牌');
  assert.equal(question.reply, '招牌要换成哪几个字？请给出准确文字。');
  assert.equal(question.choices.length, 0);
});

test('auto prepares ordinary actions and pauses before generating; confirmation resumes without duplicate creation', async () => {
  const h = harness([], [{reply:'准备建立场景并生成。', actions:[
    {type:'create_nodes', node_type:'image', items:[{name:'夏日', prompt:'夏日场景'}]},
    {type:'generate_nodes', target:'created'},
    {type:'update_node', id:'$last_created', prompt:'后续文案'}
  ]}]);
  const out = await h.run('创建场景并生成图片', {execMode:'auto'});
  assert.equal(h.created.length,1);
  assert.equal(h.generated.length,0);
  assert.equal(out.pendingActions.length,2);
  assert.ok(out.planSummary[0].includes('夏日'));
  const pending = JSON.parse(JSON.stringify(out.pendingActions)); // survives refresh
  const blocked = await h.context.AgentIntent.executePendingActions(pending);
  assert.equal(blocked.requiresConfirmation,true);
  assert.equal(h.generated.length,0);
  await h.context.AgentIntent.executePendingActions(pending,{confirmed:true});
  assert.equal(h.created.length,1);
  assert.deepEqual(h.generated,['created-0']);
  assert.equal(h.updates.at(-1).id,'created-0');
});

test('generation aliases and image edits require confirmation at the runner boundary', async () => {
  for (const action of [{type:'generate', node_id:'a'}, {type:'generate_nodes', id:'a'}, {type:'edit_image', id:'a', instruction:'改背景',source_url:'original.png'}]) {
    const h=harness([{id:'a',type:'image',customName:'场景甲',imageUrl:'original.png'}]);
    let edits=0; h.context.AgentOps.editImage=async()=>{edits++};
    const out=await h.context.AgentRunner.runActions([action]);
    assert.equal(out.requiresConfirmation,true);
    assert.equal(h.generated.length,0); assert.equal(edits,0);
    await h.context.AgentIntent.executePendingActions(out.pendingActions,{confirmed:true});
    assert.equal(h.generated.length+edits,1);
  }
});

test('changed generation prompt, model, reference or board invalidates a pending confirmation',async()=>{
  for (const change of [h=>h.board.nodes[0].prompt='改后的文案', h=>h.board.nodes[0].model='different',
    h=>h.board.nodes[1].imageUrl='changed.png', h=>h.board.id='different-board']) {
    const h=harness([{id:'a',type:'image',prompt:'原文',softRefIds:['ref']},{id:'ref',type:'ref-image',imageUrl:'original.png'}]);
    const out=await h.context.AgentRunner.runActions([{type:'generate_nodes',node_ids:['a']}]);
    change(h);
    const result=await h.context.AgentIntent.executePendingActions(out.pendingActions,{confirmed:true});
    assert.equal(h.generated.length,0); assert.match(result.logs.join(' '),/变化|切换/);
  }
});

test('ordinary auto edits still execute and confirm mode holds the full plan',async()=>{
  const plan=()=>({reply:'准备修改。',actions:[{type:'update_node',id:'a',prompt:'新文案'}]});
  const h=harness([{id:'a',type:'image',prompt:'原文'}],[plan(),plan()]);
  const auto=await h.run('改提示词',{execMode:'auto'});
  assert.equal(h.updates.length,1);assert.equal(auto.pendingActions,null);
  const confirm=await h.run('改提示词',{execMode:'confirm'});
  assert.equal(h.updates.length,1);assert.equal(confirm.pendingActions.length,1);
});

test('confirmation covers later generation steps and confirm-mode targets', async () => {
  for (const execMode of ['auto','confirm']) {
    const h=harness([{id:'a',type:'image',prompt:'甲'},{id:'b',type:'video',prompt:'乙'}], [
      {reply:'准备生成。', actions:[{type:'generate_nodes',node_ids:['a']},{type:'generate_nodes',node_ids:['b']}]}
    ]);
    const out=await h.run('生成这两个节点',{execMode});
    h.board.nodes[1].prompt='等待确认时修改了第二个目标';
    const result=await h.context.AgentIntent.executePendingActions(out.pendingActions,{confirmed:true});
    assert.equal(h.generated.length,0);
    assert.match(result.logs.join(' '),/变化/);
  }
});

test('changed inline references and prompt prefix invalidate generation confirmation', async () => {
  for (const kind of ['reference','prefix']) {
    const h=harness([{id:'a',type:'image',prompt:'参考 @ref:r'},{id:'r',type:'ref-image',imageUrl:'original.png'}]);
    h.context.collectReferences=()=>({all:[{nodeId:'r'}]});
    let prefix='原前缀'; h.context.PromptPrefixStore={applyPrefix:()=>prefix};
    const pending=await h.context.AgentRunner.runActions([{type:'generate_nodes',node_ids:['a']}]);
    if(kind==='prefix') prefix='新前缀'; else h.board.nodes[1].imageUrl='changed.png';
    await h.context.AgentIntent.executePendingActions(pending.pendingActions,{confirmed:true});
    assert.equal(h.generated.length,0);
  }
});
