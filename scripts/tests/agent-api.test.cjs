const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(api) {
  const ctx = vm.createContext({ console, api });
  ctx.window = ctx;
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js/agent/api.js'), 'utf8'), ctx);
  return ctx.AgentApi;
}

test('selected model sends one request and losslessly packs duplicate canvas fields', async () => {
  const calls = [];
  const api = harness(async (url, options) => {
    calls.push({url, data:JSON.parse(options.body)});
    return {reply:'准备更新。', actions:[], decision:'clarify', diagnostics:{upstream_calls:1}};
  });
  const canvas = {nodes:[{id:'doc', prompt:'原始指令', body:'完整正文\n关键尾注', result_text:'完整正文\n关键尾注'}],
    node_index:[{id:'doc', name:'文档', image_url:'unused-index-url'}]};
  const result = await api.plan('更新提示词', {modelId:'chosen', canvas});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/agent/chat');
  assert.equal(calls[0].data.model_id, 'chosen');
  assert.equal(calls[0].data.canvas.nodes[0].body, undefined);
  assert.equal(calls[0].data.canvas.nodes[0].result_text, '完整正文\n关键尾注');
  assert.equal(calls[0].data.canvas.nodes[0].prompt, '原始指令');
  assert.equal(calls[0].data.canvas.node_index[0].image_url, undefined);
  assert.equal(canvas.nodes[0].body, '完整正文\n关键尾注', 'must not mutate caller context');
  assert.equal(result.decision, 'clarify');
});

test('missing selection resolves default once and preserves cached empty lists', async () => {
  const calls = [];
  const api = harness(async (url) => { calls.push(url); return {models:[]}; });
  await assert.rejects(api.plan('检查画布'), /尚未配置/);
  await assert.rejects(api.plan('检查画布'), /尚未配置/);
  assert.equal(calls.length, 2);
});

test('model catalogue abort propagates without caching a false empty list', async () => {
  let abort = true;
  const api = harness(async () => {
    if (abort) throw Object.assign(new Error('aborted'), {name:'AbortError'});
    return {models:[{id:'valid', is_default:true}]};
  });
  await assert.rejects(api.listAgentModels(), {name:'AbortError'});
  abort = false;
  const models = await api.listAgentModels();
  assert.equal(models[0].id, 'valid');
});

test('node model question keeps canvas context', async () => {
  let sent;
  const api = harness(async (url, opts) => { sent=JSON.parse(opts.body); return {reply:'模型A'}; });
  assert.equal(api.isLightChat('你是什么模型？'), true);
  assert.equal(api.isLightChat('这个节点用的是什么模型？'), false);
  await api.plan('这个节点用的是什么模型？', {modelId:'a', canvas:{nodes:[], node_index:[]}});
  assert.equal(sent.light, false);
  assert.ok(sent.canvas);
});

test('cancelled transport result is not returned as a runnable plan', async () => {
  const ac = new AbortController();
  const api = harness(async () => { ac.abort(); return {reply:'准备生成。', actions:[{type:'generate_nodes', node_ids:['a']}]}; });
  await assert.rejects(api.plan('生成图片', {modelId:'a', signal:ac.signal}), {name:'AbortError'});
});
