"""Manual check using the configured agent; synthetic materials, no canvas execution."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import myapi_store, user_scope
from backend.agent.chat import chat_completion


async def main():
    user_scope.set_current_user_id("admin")
    cfg = next(m for m in myapi_store.list_models("agent") if m.get("model") == "agnes-3.0-flash")
    document = """# 第21章参考图设定（验证素材）
## 场景13 合作餐厅
场景生成提示词：
现代城市安静餐厅，入口在左侧，靠窗桌在右侧，通道位于中央，固定两把椅子，无人物，暖色灯光。
负面提示词：
禁止人物，禁止改变入口、通道和桌椅位置，禁止额外餐具。
## 人物
姜眠、陆司年是本章主要角色；林夏在茶水间短暂出现。
服务员是同一名短暂人物，但不单独制作参考图。
## 场景连续性
场景03只作为凌晨回忆插入，结束后必须返回场景05原座位。
不要把回忆里的公寓客厅误当成公司茶水间。
"""
    nodes = [{"id": "restaurant", "type": "image", "name": "合作餐厅", "has_media": False, "prompt_empty": True, "prompt": ""},
             {"id": "pantry", "type": "image", "name": "公司茶水间", "has_media": True, "prompt_empty": False, "prompt": "公司茶水间场景"}]
    canvas = {"stats": {"total": 2}, "node_index": nodes, "nodes": nodes}
    cases = [("fill-existing", "看看合作餐厅有没有，已有就补上文档里的完整场景生成提示词和负面提示词，不要出图。", False),
             ("ambiguous-extraction", "帮我提取里面的内容", True)]
    results = []
    for case, request, ambiguous in cases:
        plan = await chat_completion(model_cfg=cfg, message=request,
                                     history=[{"role": "user", "content": document}], canvas=canvas)
        result = {"case": case, "reply": plan["reply"], "actions": plan["actions"], "choices": plan["choices"]}
        if not ambiguous:
            updates = [a for a in plan["actions"] if a["type"] == "update_node" and a.get("id") == "restaurant"]
            result["passed"] = bool(updates) and "禁止额外餐具" in updates[0].get("prompt", "") and not any(a["type"] in ("create_nodes", "generate_nodes") for a in plan["actions"])
        else:
            result["passed"] = not plan["actions"] and len(plan["choices"]) >= 2 and all(not c.get("actions") for c in plan["choices"])
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    target = Path(__file__).resolve().parents[1] / "artifacts/agent-reliability-qa/live-results.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__": asyncio.run(main())
