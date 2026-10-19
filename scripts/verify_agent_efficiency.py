"""Compare agent request sizes; --live uses the default model on fictional data.

No returned actions are executed and no media generation is submitted.
"""
import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.agent import chat as current


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", choices=("readonly-document", "rename-selected"))
    parser.add_argument("--only-after", action="store_true")
    parser.add_argument("--baseline", type=Path, default=ROOT / "backups/before-agent-efficiency-20260915-143935/backend/agent/chat.py")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/agent-efficiency-qa/request-comparison.json")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("backend.agent._efficiency_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    cfg = {"base_url": "https://model.test", "model": "test"}
    if args.live:
        from backend import myapi_store, user_scope
        user_scope.set_current_user_id("admin")
        models = [m for m in myapi_store.list_models("agent") if m.get("enabled", True)]
        cfg = next((m for m in models if m.get("is_default")), None) or models[0]
    document = ("# 测试剧本\n人物：姜眠、陆司年。临时出场：林夏。\n"
                + "场景说明：合作餐厅入口在左侧，靠窗桌在右侧，通道在中央。\n" * 180
                + "\n关键尾注：服务员不单独制作参考图；不要创建或生成媒体。")
    nodes = [{"id": "doc", "name": "测试剧本", "type": "text", "body": document, "prompt": document},
             {"id": "restaurant", "name": "合作餐厅", "type": "image", "prompt": "现代餐厅，无人物。", "has_media": False}]
    canvas = {"board_id": "fictional", "nodes": nodes, "node_index": [
        {k: n[k] for k in ("id", "name", "type")} for n in nodes], "selected_ids": ["restaurant"], "stats": {"total": 2}}
    cases = [("readonly-document", "从测试剧本提取人物，并说明服务员是否需要单独制作参考图。只文字回答。",
              {"reply": "人物是姜眠、陆司年和林夏；尾注明确服务员不单独制作参考图。", "actions": [], "choices": []}),
             ("rename-selected", "把选中的合作餐厅节点改名为夏日餐厅，其它内容保持不变。",
              {"reply": "准备把合作餐厅改名为夏日餐厅。", "actions": [
                  {"type": "update_node", "id": "restaurant", "customName": "夏日餐厅"}], "choices": []})]
    results = []
    for case, message, fake_plan in cases:
        if args.case and case != args.case:
            continue
        for label, module in (("before", baseline), ("after", current)):
            if args.only_after and label == "before":
                continue
            calls = []
            original = module._post_chat_completion
            async def tracked(**kwargs):
                messages = kwargs["payload"]["messages"]
                calls.append({"input_chars": sum(len(str(m["content"])) for m in messages)})
                if args.live:
                    return await original(**kwargs)
                return {"choices": [{"message": {"content": json.dumps(fake_plan, ensure_ascii=False)}, "finish_reason": "stop"}]}
            started = time.perf_counter()
            try:
                with patch.object(module, "_post_chat_completion", tracked):
                    plan = await module.chat_completion(model_cfg=cfg, message=message, canvas=canvas,
                        history=[{"role": "user", "content": document}])
                actions = plan.get("actions", [])
                if case == "readonly-document":
                    passed = not actions and all(name in plan["reply"] for name in ("姜眠", "陆司年", "林夏", "服务员"))
                else:
                    passed = len(actions) == 1 and actions[0].get("id") == "restaurant" and (
                        actions[0].get("patch", {}).get("customName") == "夏日餐厅" or
                        actions[0].get("customName") == "夏日餐厅")
                result = {"case": case, "version": label, "live": args.live,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000), "requests": calls,
                          "passed": passed, "reply": plan["reply"], "actions": actions}
            except Exception as exc:
                result = {"case": case, "version": label, "live": args.live,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000), "requests": calls,
                          "passed": False, "error_type": type(exc).__name__}
            results.append(result)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=True), flush=True)
    if not all(r["passed"] for r in results if r["version"] == "after"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
