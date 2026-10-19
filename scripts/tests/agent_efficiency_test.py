"""Agent latency/cancellation regressions; model transport is entirely mocked."""
import asyncio
import ast
import json
import sys
import time
import unittest
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import httpx
import agent_reliability_test as reliability
from backend.agent import chat as agent


def endpoint_app():
    # Run the production endpoint with ASGI, without application startup/data writes.
    from fastapi import Body, FastAPI, HTTPException, Request
    tree = ast.parse((Path(__file__).resolve().parents[2] / "backend/main.py").read_text(encoding="utf-8"))
    endpoints = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in
                 {"api_agent_chat", "api_agent_chat_status", "api_agent_chat_cancel"}]
    env = {"app": FastAPI(), "Body": Body, "HTTPException": HTTPException, "Request": Request,
           "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=endpoints, type_ignores=[]), "backend/main.py", "exec"), env)
    return env["app"]


class AgentEfficiencyTest(unittest.IsolatedAsyncioTestCase):
    call_model = reliability.AgentReliabilityTest.call_model

    async def test_transport_timeout_never_resubmits_with_thinking_enabled(self):
        calls = []
        async def send(request):
            calls.append(request)
            raise httpx.ReadTimeout("timed out", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            with self.assertRaises(RuntimeError):
                await agent._post_chat_completion(url="https://model.test/chat", headers={},
                    payload={"model": "deepseek-flash"}, timeout=httpx.Timeout(1),
                    try_disable_thinking=True, client=client)
        self.assertEqual(len(calls), 1)

    async def test_explicit_parameter_rejection_falls_back_once_for_entire_turn(self):
        bodies, state = [], {}
        async def send(request):
            body = json.loads(request.content)
            bodies.append(body)
            if "thinking" in body:
                return httpx.Response(400, json={"error": "unknown field thinking"})
            return httpx.Response(200, json={"choices": []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            for _ in range(2):
                await agent._post_chat_completion(url="https://model.test/chat", headers={},
                    payload={"model": "deepseek-flash"}, timeout=httpx.Timeout(1),
                    try_disable_thinking=True, client=client, request_state=state)
        self.assertEqual(len(bodies), 3)
        self.assertTrue(state["thinking_rejected"])
        self.assertNotIn("thinking", bodies[-1])

    async def test_auth_errors_never_retry(self):
        calls = []
        async def send(request):
            calls.append(request)
            return httpx.Response(401, text="invalid API key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            with self.assertRaises(RuntimeError):
                await agent._post_chat_completion(url="https://model.test/chat", headers={},
                    payload={}, timeout=httpx.Timeout(1), try_disable_thinking=True, client=client)
        self.assertEqual(len(calls), 1)

    async def test_one_deadline_covers_initial_request_and_repair(self):
        calls, cancelled = [], asyncio.Event()
        async def send(request):
            calls.append(request)
            if len(calls) == 1:
                await asyncio.sleep(0.04)
                return httpx.Response(200, json=reliability.response({"reply": "准备更新。", "actions": []}))
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            with patch.object(agent, "CHAT_BUDGET_SECONDS", 0.12):
                started = time.perf_counter()
                with self.assertRaisesRegex(RuntimeError, "本轮处理超时"):
                    await agent.chat_completion(model_cfg={"base_url": "https://model.test", "model": "test"},
                        message="更新合作餐厅提示词", canvas=reliability.CANVAS, client=client)
                self.assertLess(time.perf_counter() - started, 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(cancelled.is_set())

    async def test_disconnect_cancels_inflight_upstream(self):
        cancelled = asyncio.Event()
        async def operation():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with self.assertRaises(agent.AgentClientDisconnected):
            await agent.wait_while_connected(operation(), AsyncMock(return_value={"type": "http.disconnect"}))
        self.assertTrue(cancelled.is_set())

    async def test_connected_request_returns_result_and_propagates_errors(self):
        self.assertEqual(await agent.wait_while_connected(AsyncMock(return_value=3)(),
            asyncio.Event().wait), 3)
        with self.assertRaisesRegex(ValueError, "example"):
            await agent.wait_while_connected(AsyncMock(side_effect=ValueError("example"))(),
                asyncio.Event().wait)

    async def test_rename_selected_image_does_not_call_vision(self):
        canvas = {**reliability.CANVAS, "selected_ids": ["restaurant"], "nodes": [
            {**reliability.CANVAS["nodes"][0], "image_url": "https://images.test/source.png"}]}
        vision = AsyncMock(side_effect=AssertionError("unnecessary vision call"))
        with patch.object(agent, "_visual_input", vision):
            result, sent = await self.call_model([reliability.response({"reply": "准备改名。", "actions": [
                {"type": "update_node", "id": "restaurant", "customName": "夏天"}]})],
                "把选中节点改名为夏天", canvas=canvas)
        self.assertEqual(vision.await_count, 0)
        self.assertEqual(len(sent), 0)
        self.assertEqual(result["diagnostics"]["route"], "local")

    def test_visual_questions_and_edits_still_use_pixels(self):
        canvas = {"selected_ids": ["r"], "nodes": [{"id": "r", "image_url": "https://images.test/a.png"}]}
        for message in ("看看图里是什么", "把图片背景改成蓝色", "根据这张图写提示词", "图片和提示词一致吗", "这个怎么样"):
            self.assertTrue(agent._needs_visual_input(canvas, message), message)
        for message in ("把选中节点竖排", "修改这个节点提示词", "删除这个节点", "给选中节点重新生成"):
            self.assertFalse(agent._needs_visual_input(canvas, message), message)

    async def test_complete_plain_readonly_answer_needs_one_call(self):
        reply = "人物是姜眠；依据是文档人物表中写着姜眠。"
        result, sent = await self.call_model([reliability.response(reply)], "提取人物，只文字列出")
        self.assertEqual(result["reply"], reply)
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(sent), 1)
        self.assertNotIn("## actions", sent[0]["messages"][0]["content"])

    async def test_truncated_readonly_answer_is_not_delivered_as_complete(self):
        result, sent = await self.call_model([reliability.response("人物有姜眠", "length")] * 2,
            "提取人物，只文字列出")
        self.assertEqual(len(sent), 2)
        self.assertIn("未执行", result["reply"])

    async def test_exact_source_is_sent_once_but_changed_versions_survive(self):
        doc = "# 人物文档\n" + "人物是姜眠。\n" * 300 + "关键尾注：不单独制图。"
        changed = doc + "\n更正：新增林夏。"
        canvas = {"nodes": [{"id": "doc", "type": "text", "body": doc}]}
        result, sent = await self.call_model([reliability.response({"reply": "人物为姜眠和林夏。", "actions": []})],
            "提取文档人物", canvas=canvas, history=[{"role": "user", "content": s} for s in (doc, doc, changed)])
        contents = [m["content"] for m in sent[0]["messages"]]
        self.assertEqual(contents.count(doc), 1)
        self.assertIn(changed, contents)
        context = next(c for c in contents if c.startswith("画布上下文"))
        self.assertNotIn(doc, context)
        self.assertIn("全文见该材料", context)

    def test_distinct_node_prompt_and_result_are_both_supplied(self):
        context = agent._build_context_block({"nodes": [{"id": "t", "prompt": "原始指令", "result_text": "生成的文章"}]})
        self.assertIn("原始指令", context)
        self.assertIn("生成的文章", context)

    def test_node_model_question_is_not_assistant_identity(self):
        self.assertFalse(agent.is_light_chat("选中节点用的是什么模型？"))
        self.assertFalse(agent.is_light_chat("这个图片使用什么模型？"))
        self.assertTrue(agent.is_light_chat("你是什么模型？"))

    async def test_repair_keeps_thinking_disabled_and_gets_failed_output(self):
        bad = {"reply": "准备更新。", "actions": []}
        good = {"reply": "准备更新合作餐厅。", "actions": [{"type": "update_node", "id": "restaurant", "prompt": "新提示词"}]}
        result, sent = await self.call_model([reliability.response(bad), reliability.response(good)],
            "更新合作餐厅提示词", model_cfg={"base_url": "https://model.test", "model": "deepseek-flash"})
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(s["thinking"] == {"type": "disabled"} for s in sent))
        self.assertIn("上次未执行的输出", sent[1]["messages"][-1]["content"])
        self.assertEqual(result["diagnostics"]["upstream_calls"], 2)

    async def test_exact_count_layout_and_rename_use_no_model(self):
        canvas = {"selected_ids": ["a"], "node_index": [
            {"id": "a", "name": "餐厅", "type": "image"}, {"id": "b", "name": "厨房", "type": "image"}],
            "stats": {"total": 2}}
        for request in ("画布上有多少个节点？", "把所有节点竖着排列", "把选中的餐厅节点改名为夏日餐厅，其它内容保持不变。"):
            result, sent = await self.call_model([], request, canvas=canvas)
            self.assertEqual(len(sent), 0)
            self.assertEqual(result["diagnostics"]["route"], "local")
        self.assertEqual(result["actions"][0]["patch"]["customName"], "夏日餐厅")

    def test_ambiguous_compound_source_and_ask_edits_never_use_local_path(self):
        from backend.agent.fast_path import exact_plan
        canvas = {"selected_ids": ["a", "b"], "node_index": [
            {"id": "a", "name": "餐厅"}, {"id": "b", "name": "餐厅"}]}
        for request in ("把选中节点改名为夏天", "把餐厅改名为夏天", "把选中节点改名为夏天，然后生成", "文档\n把所有节点竖着排列", "能把所有节点竖着排列吗"):
            self.assertIsNone(exact_plan(request, canvas), request)
        self.assertIsNone(exact_plan("把所有节点竖着排列", canvas, role_mode="ask"))

    async def test_http_endpoint_submits_selected_model_then_polls_plan_metadata(self):
        app = endpoint_app()
        cfg = {"id": "chosen", "kind": "agent", "model": "test", "base_url": "https://model.test"}
        submit = AsyncMock()
        result = {"user_id": "test-user", "status": "completed", "model": "chosen",
                  "result": {"reply": "共12个节点。", "diagnostics": {"route": "local"}, "actions": []}}
        with patch("backend.myapi_store.get_model", return_value=cfg), \
             patch("backend.user_scope.get_current_user_id", return_value="test-user"), \
             patch("backend.job_worker.client.is_available_retry", AsyncMock(return_value=(True, ""))), \
             patch("backend.job_worker.client.submit_job", submit), \
             patch("backend.job_worker.client.get_job", AsyncMock(return_value=result)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
                response = await client.post("/api/agent/chat", json={"model_id": "chosen", "message": "画布上有多少个节点？",
                    "board_id": "board", "exec_mode": "auto", "canvas": {"stats": {"total": 12}, "nodes": []}})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["async"])
                self.assertEqual(response.json()["model_id"], "chosen")
                self.assertEqual(submit.await_count, 1)
                sent = submit.await_args.kwargs
                self.assertEqual(sent["board_id"], "board")
                self.assertEqual(sent["payload"]["exec_mode"], "auto")
                polled = await client.get("/api/agent/chat/" + response.json()["job_id"])
        self.assertEqual(polled.status_code, 200)
        self.assertIn("12", polled.json()["reply"])
        self.assertEqual(polled.json()["diagnostics"]["route"], "local")

    async def test_http_endpoint_does_not_silently_switch_a_missing_selected_model(self):
        app = endpoint_app()
        model = AsyncMock()
        with patch("backend.myapi_store.get_model", return_value=None), patch("backend.public_models_store.get_model", return_value=None), patch("backend.agent.chat_completion", model):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
                response = await client.post("/api/agent/chat", json={"model_id": "removed", "message": "你好"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(model.await_count, 0)

    async def test_explicit_cancel_checks_job_ownership_and_cancels_once(self):
        # Jobs intentionally survive page disconnect/reload. Only the explicit
        # cancellation endpoint stops a worker, and only for its owner.
        app = endpoint_app()
        cancel = AsyncMock()
        with patch("backend.user_scope.get_current_user_id", return_value="test-user"), \
             patch("backend.job_worker.client.get_job", AsyncMock(return_value={"user_id": "other-user"})) as get_job, \
             patch("backend.job_worker.client.cancel_job", cancel):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
                response = await client.post("/api/agent/chat/agent-test/cancel")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(cancel.await_count, 0)
                get_job.return_value = {"user_id": "test-user", "status": "running"}
                response = await client.post("/api/agent/chat/agent-test/cancel")
                self.assertEqual(response.status_code, 200)
                cancel.assert_awaited_once_with("agent-test")


if __name__ == "__main__":
    unittest.main()
