"""Agent regressions with mocked model transport; no accounts or media jobs."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.agent.chat import chat_completion, _compact_history, _build_context_block
from backend.agent.reliability import read_only


CANVAS = {"stats": {"total": 1}, "node_index": [{"id": "restaurant", "name": "合作餐厅", "type": "image", "has_media": False}],
          "nodes": [{"id": "restaurant", "name": "合作餐厅", "type": "image", "prompt": "旧提示词"}]}


def response(plan, finish="stop"):
    return {"choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False) if isinstance(plan, dict) else plan}, "finish_reason": finish}]}


class AgentReliabilityTest(unittest.IsolatedAsyncioTestCase):
    async def call_model(self, responses, message="检查哪些没有，补齐对应提示词", **kwargs):
        sent = []
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                sent.append(kwargs["json"])
                item = responses[len(sent) - 1]
                if isinstance(item, Exception): raise item
                class Response:
                    status_code = 200
                    def json(self): return item
                return Response()
        with patch("backend.agent.chat.httpx.AsyncClient", Client):
            result = await chat_completion(model_cfg=kwargs.pop("model_cfg", {"base_url": "http://model.test", "model": "test"}), message=message,
                                           canvas=kwargs.pop("canvas", CANVAS), **kwargs)
        return result, sent

    async def test_screenshot_half_reply_gets_repaired_before_execution(self):
        broken = {"reply": "画布上已经有合作餐厅，按文档补上，并生成图片：", "actions": [], "choices": []}
        fixed = {"reply": "准备补全合作餐厅的提示词并生成图片。", "actions": [
            {"type": "update_node", "id": "restaurant", "prompt": "场景原文\n\n负面提示词：禁止人物"},
            {"type": "generate_nodes", "node_ids": ["restaurant"]}], "choices": []}
        result, sent = await self.call_model([response(broken), response(fixed)], "补上合作餐厅提示词并生成图片")
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(result["actions"]), 2)
        self.assertEqual(result["actions"][0]["patch"]["prompt"], "场景原文\n\n负面提示词：禁止人物")

    async def test_truncated_valid_json_is_not_executable(self):
        plan = {"reply": "准备更新合作餐厅。", "actions": [{"type": "update_node", "id": "restaurant", "prompt": "不完整"}], "choices": []}
        result, sent = await self.call_model([response(plan, "length"), response(plan, "length")])
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(result["choices"]), 2)
        self.assertIn("未执行", result["reply"])

    async def test_plain_half_reply_repair_failure_does_not_leak_partial_actions(self):
        result, sent = await self.call_model([response("我准备把提示词补上："), RuntimeError("offline")])
        self.assertEqual(result["actions"], [])
        self.assertTrue(result["choices"][0]["message"])

    async def test_ambiguous_extraction_returns_readonly_choices(self):
        bad = {"reply": "你想提取哪种内容？", "actions": [], "choices": []}
        good = {"reply": "这份文档有剧情和参考图设定，提取哪部分？", "actions": [], "choices": [
            {"label": "剧情里的人物与场景", "message": "从全文提取剧情人物和场景，只文字列出。"},
            {"label": "参考图提示词", "message": "提取每个参考图的完整正面及负面提示词，只文字列出。"}]}
        result, sent = await self.call_model([response(bad), response(good)], "提取内容")
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(result["choices"]), 2)
        self.assertEqual(result["actions"], [])

    async def test_vague_extraction_cannot_silently_become_a_summary(self):
        summary = {"reply": "文档核心内容如下：人物是姜眠，场景是合作餐厅。", "actions": [], "choices": []}
        fixed = {"reply": "提取哪部分？", "actions": [], "choices": [{"label": "人物设定"}, {"label": "场景提示词"}]}
        result, sent = await self.call_model([response(summary), response(fixed)], "帮我提取里面的内容")
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(result["choices"]), 2)

    async def test_readonly_plan_cannot_mutate_even_via_choice(self):
        bad = {"reply": "选择怎么提取？", "actions": [], "choices": [
            {"label": "提取人物", "actions": [{"type": "delete_nodes", "node_ids": ["restaurant"]}]}, {"label": "提取场景"}]}
        result, sent = await self.call_model([response(bad), response(bad)], "提取内容", role_mode="ask")
        self.assertEqual(result["actions"], [])
        self.assertTrue(all(not c["actions"] for c in result["choices"]))

    async def test_missing_or_unknown_node_triggers_repair(self):
        bad = {"reply": "准备更新。", "actions": [{"type": "update_node", "id": "invented", "prompt": "test"}], "choices": []}
        result, sent = await self.call_model([response(bad), response(bad)])
        self.assertEqual(len(sent), 2)
        self.assertFalse(result["actions"])

    async def test_existing_empty_node_not_recreated(self):
        bad = {"reply": "准备创建。", "actions": [{"type": "create_nodes", "node_type": "image", "items": [{"name": "合作餐厅", "prompt": "test"}]}], "choices": []}
        result, sent = await self.call_model([response(bad), response(bad)])
        self.assertFalse(result["actions"])

    async def test_new_nodes_generation_keeps_explicit_target(self):
        plan = {"reply": "准备新建办公室并生成。", "actions": [
            {"type": "create_nodes", "node_type": "image", "items": [{"name": "办公室", "prompt": "无人物"}]},
            {"type": "generate_nodes", "target": "created"}], "choices": []}
        result, sent = await self.call_model([response(plan)], "创建办公室并出图")
        self.assertEqual(result["actions"][1]["target"], "created")
        self.assertEqual(len(sent), 1)

    async def test_source_tail_and_older_documents_survive(self):
        doc = "# 文档\n" + "重要内容。" * 10000 + "\n末尾：服务员不单独制作参考图。"
        history = [{"role": "user", "content": doc}] + [{"role": "assistant", "content": "收到"}] * 12
        self.assertEqual(_compact_history(history)[0]["content"], doc)
        plan = {"reply": "服务员不单独制作参考图，依据是文档末尾的明确要求。", "actions": [], "choices": []}
        result, sent = await self.call_model([response(plan)], "从整个文档提取人物", history=history)
        self.assertTrue(any(m["content"] == doc for m in sent[0]["messages"]))
        result, sent = await self.call_model([response(plan)], doc + "\n提取人物")
        self.assertTrue(sent[0]["messages"][-1]["content"].endswith("提取人物"))

    def test_context_keeps_late_node_and_full_body(self):
        nodes = [{"id": str(i), "body": "正文" * 5000 + f"末尾{i}"} for i in range(30)]
        context = _build_context_block({"nodes": nodes, "stats": {"total": 30}})
        self.assertIn("末尾29", context)
        self.assertIn("末尾0", context)

    def test_compound_instruction_is_not_mistaken_for_readonly(self):
        self.assertFalse(read_only("提取一下场景并创建对应节点，里面填上提示词"))
        self.assertFalse(read_only("不要新建，更新已有节点提示词"))
        self.assertTrue(read_only("提取人物，不要创建"))
        self.assertTrue(read_only("为什么没创建"))
        self.assertTrue(read_only("提取人物\n\n---\n引用内容：\n文档写着生成图片"))
        self.assertTrue(read_only("提取场景生成提示词"))
        self.assertTrue(read_only("只提取，不要生成图片"))

    async def test_extract_generation_prompts_is_readonly(self):
        plan = {"reply": "场景生成提示词：现代餐厅，无人物。", "actions": [], "choices": []}
        result, sent = await self.call_model([response(plan)], "提取场景生成提示词")
        self.assertEqual(len(sent), 1)
        self.assertFalse(result["actions"])


    async def test_precise_text_replacement_and_read_then_edit(self):
        plan = {"reply": "准备把旧字改成新字，保留其它内容。", "actions": [
            {"type": "replace_node_text", "id": "restaurant", "field": "prompt", "find": "旧", "replace": "新"}]}
        result, sent = await self.call_model([response(plan)], "看看餐厅，把旧提示词改成新提示词")
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["actions"][0]["find"], "旧")

    async def test_document_specific_free_question_does_not_require_fixed_options(self):
        plan = {"reply": "这段剧本的结尾有两版重逢。要沿用码头重逢，还是车站重逢？", "decision": "clarify", "actions": [], "choices": []}
        result, sent = await self.call_model([response(plan)], "剧本材料。" * 150 + "\n结尾：码头与车站版本待定。")
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["reply"], plan["reply"])
        self.assertEqual(result["choices"], [])

    async def test_clarification_cannot_run_edits_at_the_same_time(self):
        bad = {"reply": "招牌想换成什么文字？", "decision": "clarify", "actions": [{"type":"update_node", "id":"restaurant", "prompt":"猜测"}]}
        result, sent = await self.call_model([response(bad), response(bad)], "修改招牌")
        self.assertFalse(result["actions"])

    async def test_actual_image_blocks_reach_multimodal_model_with_node_mapping(self):
        canvas = {"selected_ids": ["restaurant"], "nodes": [{"id":"restaurant", "name":"餐厅", "type":"image", "image_url":"https://images.test/original.png"}], "node_index": CANVAS["node_index"]}
        plan = {"reply":"准备修改招牌，保持其它部分。", "actions":[{"type":"edit_image", "id":"restaurant", "instruction":"招牌改成夏天", "source_url":"https://images.test/original.png"}]}
        style = type("Style", (), {"resolve_references": AsyncMock(return_value=[{"kind":"image", "_media":"https://images.test/original.png"}])})()
        with patch("backend.myapi.styles.get_style", return_value=style):
            result, sent = await self.call_model([response(plan)], "把选中图片的招牌改成夏天", canvas=canvas,
                model_cfg={"base_url":"http://model.test", "model":"vision", "multimodal":True})
        self.assertEqual(len(sent), 1)
        blocks = sent[0]["messages"][-1]["content"]
        self.assertEqual(blocks[-1]["image_url"]["url"], "https://images.test/original.png")
        self.assertIn("restaurant", blocks[1]["text"])
        self.assertEqual(result["actions"][0]["type"], "edit_image")

    async def test_external_vision_observation_is_supplied_to_planner(self):
        canvas = {"selected_ids":["restaurant"], "nodes":[{"id":"restaurant", "type":"image", "image_url":"https://images.test/original.png"}]}
        observation = AsyncMock(return_value={"text":"招牌左侧为春天，字体为白色。"})
        with patch("backend.myapi_store.get_model", return_value={"kind":"vision", "enabled":True}), patch("backend.myapi.client.generate_text", observation):
            result, sent = await self.call_model([response({"reply":"招牌上是春天。", "actions":[]})], "看看选中图片的招牌", canvas=canvas,
                model_cfg={"base_url":"http://model.test", "model":"text", "vision_model_id":"vision-id"})
        self.assertEqual(observation.await_count, 1)
        self.assertTrue(any("招牌左侧为春天" in str(m["content"]) for m in sent[0]["messages"]))
        self.assertIsInstance(sent[0]["messages"][-1]["content"], str)

    async def test_vision_failure_does_not_block_dynamic_clarification(self):
        with patch("backend.agent.chat._visual_input", AsyncMock(side_effect=TimeoutError())):
            canvas = {**CANVAS, "selected_ids": ["restaurant"], "nodes": [
                {**CANVAS["nodes"][0], "image_url": "https://images.test/original.png"}]}
            result, sent = await self.call_model([response({"reply":"这次没能读到图片。招牌要改成哪几个字？", "decision":"clarify", "actions":[]})], "修改招牌", canvas=canvas)
        self.assertFalse(result["actions"])
        self.assertTrue(any("图片读取失败" in str(m["content"]) for m in sent[0]["messages"]))

    async def test_image_edit_rejects_missing_original(self):
        bad = {"reply":"准备改图。", "actions":[{"type":"edit_image", "id":"restaurant", "instruction":"背景改成蓝色"}]}
        result, sent = await self.call_model([response(bad), response(bad)], "背景改成蓝色")
        self.assertFalse(result["actions"])

    async def test_new_node_can_reference_existing_material_after_creation(self):
        plan = {"reply":"准备新建节点并连接原素材。", "actions":[
            {"type":"create_nodes", "node_type":"image", "items":[{"name":"新场景", "prompt":"新场景提示词"}]},
            {"type":"connect_nodes", "from":"restaurant", "to":"$last_created"}]}
        result, sent = await self.call_model([response(plan)], "新建场景节点并连接餐厅素材")
        self.assertEqual(len(sent), 1)
        self.assertEqual(result["actions"][1]["to"], "$last_created")


if __name__ == "__main__": unittest.main()
