"""Exact, unambiguous canvas commands. Everything else stays with the model."""
from __future__ import annotations

import re


def _plan(reply: str, actions: list | None = None) -> dict:
    return {"thinking": "", "reply": reply, "actions": actions or [], "choices": []}


def exact_plan(message: str, canvas: dict | None, *, role_mode: str = "agent") -> dict | None:
    if not isinstance(canvas, dict):
        return None
    # A pasted document, reference expansion or compound command must be read
    # by the planner. Never classify an instruction inside source material.
    text = str(message or "").strip()
    if not text or len(text) > 140 or "\n" in text:
        return None
    index = canvas.get("node_index") or canvas.get("nodes") or []
    nodes = [n for n in index if isinstance(n, dict) and n.get("id")]
    ids = {str(n["id"]) for n in nodes}
    selected = list(dict.fromkeys(str(i) for i in canvas.get("selected_ids", []) if str(i) in ids))
    text = re.sub(r"[。.!！?？]+$", "", text)
    text = re.sub(r"^(?:请|帮我|请帮我)\s*", "", text)
    count = re.fullmatch(r"(?:当前|这个)?画布(?:上|里)?(?:一共|总共)?有(?:多少|几)个?节点", text)
    total = (canvas.get("stats") or {}).get("total", len(nodes))
    if count and isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return _plan(f"当前画布共有 {total} 个节点。")
    if role_mode == "ask":
        return None
    layout = re.fullmatch(r"(?:把|将)?(?P<scope>全部|所有|选中(?:的)?)节点(?:按)?"
                          r"(?P<mode>竖着|横着|竖向|横向|垂直|水平)(?:排列|排版|排好)", text)
    if layout:
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            return None
        scope = "selected" if layout["scope"].startswith("选中") else "all"
        if scope == "selected" and not selected:
            return None
        if (len(selected) if scope == "selected" else total) < 2:
            return _plan("节点少于两个，无需修改布局。")
        mode = "col" if layout["mode"] in ("竖着", "竖向", "垂直") else "row"
        action = {"type": "layout_nodes", "scope": scope, "mode": mode}
        if scope == "selected":
            action["node_ids"] = selected
        return _plan("准备将" + ("选中" if scope == "selected" else "所有") + "节点" +
                     ("竖向" if mode == "col" else "横向") + "排列。", [action])
    rename = re.fullmatch(r"(?:把|将)?(?P<target>[^，,。；;！？!?\n]{1,64}?)(?:改名为|重命名为)"
                          r"(?P<name>[^，,。；;！？!?\n]{1,60}?)(?:[，,](?:其它|其他)内容(?:保持)?不变)?", text)
    if not rename:
        return None
    name = rename["name"].strip().strip('"“”「」')
    target = rename["target"].strip().strip('"“”「」')
    if not name or re.search(r"然后|并|同时|再|之后|接着|不要|别|改名", name):
        return None
    if target in ("选中节点", "选中的节点", "这个节点", "当前节点"):
        matches = [n for n in nodes if str(n["id"]) in selected] if len(selected) == 1 else []
    else:
        selected_only = target.startswith(("选中的", "选中"))
        candidate = re.sub(r"^选中(?:的)?", "", target) if selected_only else target
        names = {candidate, candidate.removesuffix("节点")}
        matches = [n for n in nodes if n.get("name") in names and
                   (not selected_only or str(n["id"]) in selected)]
    if len(matches) != 1:
        return None
    node = matches[0]
    if name == node.get("name"):
        return _plan(f"该节点已经叫「{name}」，无需修改。")
    return _plan(f"准备把「{node.get('name') or '选中节点'}」改名为「{name}」。",
                 [{"type": "update_node", "id": str(node["id"]), "customName": name}])
