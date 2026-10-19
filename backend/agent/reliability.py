"""Validate complete plans before any canvas operation can reach the browser."""
from __future__ import annotations

import re
from typing import Any


def request_text(message: str) -> str:
    # Reference bodies are material, never the current instruction. Long pasted
    # documents commonly end with a short task; do not classify their headings.
    text = str(message or "").split("\n\n---\n引用内容：", 1)[0].strip()
    if len(text) > 400 and "\n" in text:
        tail = text.splitlines()[-1].strip()
        if 0 < len(tail) < 240 and not tail.startswith(("#", "-", "|")):
            return tail
    return text


def affirmative_request(message: str) -> str:
    text = request_text(message)
    # Include compact negations like 不出图 / 不新建 (not only 不要出图).
    text = re.sub(
        r"(?:不要|不用|别|无需|不需要|先不|暂不|不)\s*(?:再)?\s*(?:创建|新建|生成|出图|修改|改画布|建节点)",
        "",
        text,
    )
    return text.replace("生成提示词", "提示词")


def read_only(message: str) -> bool:
    text = request_text(message)
    if re.search(r"(为什么|为啥|怎么).{0,12}(没创建|创建失败|没成功|失败了|报错)", text) and not re.search(r"(重新|再|帮我).{0,6}(创建|生成|修复)", text):
        return True
    # '不要新建，更新已有提示词' is still an edit. Remove only the negated verb.
    affirmative = affirmative_request(text)
    if re.search(r"创建|新建|补齐|补上|更新|修改|改提示词|改成|改为|换成|替换|生成|出图|牵线|连线|艾特|排版|删除|写入|放到画布", affirmative):
        return False
    return bool(re.search(r"提取|列出|列举|检查|看看|有哪些|有什么|缺哪些|缺什么|只回答|只分析|仅分析|只告诉", text))


def plan_issues(data: dict | None, plan: dict, content: str, finish_reason: str,
                user_intent: str, canvas: dict | None, role_mode: str) -> list[str]:
    issues: list[str] = []
    if finish_reason in ("length", "max_tokens", "content_filter"):
        issues.append("模型输出被截断或未完整返回，必须重新给出完整计划，不能恢复部分操作")
    if not isinstance(data, dict) or not any(k in data for k in ("reply", "message", "actions", "choices")):
        issues.append("没有完整的结构化回复")
    if isinstance(data, dict) and (not isinstance(data.get("actions", []), list) or not isinstance(data.get("choices", []), list)):
        issues.append("actions 和 choices 必须是数组")
    reply = str(plan.get("reply") or "").strip()
    actions = plan.get("actions") or []
    choices = plan.get("choices") or []
    clarifying = plan.get("decision") == "clarify"
    if clarifying and (actions or (data or {}).get("actions") or any(c.get("actions") for c in choices)):
        issues.append("正在澄清缺失信息时不能同时执行操作，请先等用户回答")
    if isinstance(data, dict) and isinstance(data.get("actions"), list) and not choices and len(data["actions"]) != len(actions):
        issues.append("部分操作缺少必需字段，不能静默丢弃后交付部分计划")
    raw_reply = str((data or {}).get("reply") or (data or {}).get("message") or "").strip()
    if not raw_reply or raw_reply.endswith(("：", ":", "，", ",", "、")) and not actions and not choices:
        issues.append("回复为空或停在未完成的句子/列表开头")
    if choices and len(choices) < 2:
        issues.append("需要澄清时给出至少两个具体可选回答")
    if not choices and not clarifying and re.search(r"请选|选一下|选一个|下面选|你想|您想|哪一种|哪种|不确定.{0,20}(意思|指的|哪个)|没理解|不太明白", reply):
        issues.append("在询问用户但没有提供具体选项，请围绕原话和材料给出 2～4 个选择")
    readonly = role_mode == "ask" or read_only(user_intent)
    affirmative = affirmative_request(user_intent)
    if actions and not choices:
        types = {a.get("type") for a in actions}
        if re.search(r"(?:不要|不用|别|无需|不需要|先不|暂不|不).{0,6}(生成|出图)", request_text(user_intent)) and types & {"generate_nodes", "edit_image"}:
            issues.append("用户明确要求不生成，只能准备内容，不能触发出图")
        if not readonly and re.search(r"(?<![不没别])出图|(?<![不没别])(?:生成)(?:图片|视频|一下|图像|图|$)", affirmative) and not types & {"generate_nodes", "edit_image"}:
            issues.append("用户要求生成媒体，但操作计划缺少生成步骤；目标不清时先给选项")
    vague_scope = re.fullmatch(
        r"(?:帮我|请|麻烦)?\s*(?:提取|处理|整理)(?:一下)?\s*(?:里面的|其中的|这个|这些|这份文档的|文档里的)?(?:内容|东西)?[。！!？?]*",
        request_text(user_intent),
    )
    if vague_scope and not choices and not clarifying:
        issues.append("用户只说提取/处理内容，没有指定提取对象或结果形式；请结合文档实际栏目给出具体范围选项，不能擅自用摘要代替提取")
    if readonly and (actions or any(c.get("actions") for c in choices)):
        issues.append("本轮仅问答/提取，不能写画布；请给出实际提取结果或只读澄清选项")
    if not actions and not choices and not clarifying:
        if re.search(r"(准备|将|会|正在|已经|已把|按文档).{0,100}(创建|补上|补齐|更新|改|生成|写入|牵线)", reply, re.S):
            issues.append("只承诺了画布操作却没有可执行 actions，也没有澄清选项")
        no_change = bool(re.search(r"无需修改|无需补|无需创建|不需要新增|均已完整|没有缺失|未发现缺失", reply))
        if not readonly and not no_change and re.search(r"创建|新建|补齐|补上|更新|改提示词|改成|改为|换成|替换|生成|出图|写入|牵线|连线|艾特|排版|删除", affirmative):
            issues.append("用户要求执行操作；必须给出 actions，信息不足则给出具体澄清选项")
        if reply in ("好的。", "我看过了。若还需要具体结论，请再问一次或换个问法。"):
            issues.append("没有回答实际问题")
    index = (canvas or {}).get("node_index") or (canvas or {}).get("nodes") or []
    ids = {str(n.get("id")) for n in index if isinstance(n, dict)}
    supported = {"create_nodes", "create_from_script", "split_script", "update_node", "generate_nodes", "replace_node_text", "edit_image",
                 "connect_nodes", "disconnect_nodes", "mention_ref", "layout_nodes", "layout", "move_nodes",
                 "select_nodes", "set_node_channel", "set_nodes_channel", "set_model", "set_nodes_model",
                 "set_node_prefix", "extract_frame", "media_tool", "delete_nodes"}
    created = False
    last_sequence = -1
    sequences = [actions, *(c.get("actions", []) for c in choices)]
    for sequence_id, action in ((i, a) for i, seq in enumerate(sequences) for a in seq):
        if sequence_id != last_sequence:
            created = False
            last_sequence = sequence_id
        typ = action.get("type")
        if typ not in supported:
            issues.append("包含不支持的操作类型")
        refs = [action[k] for k in ("id", "node_id", "host_id", "ref_id", "from", "to") if action.get(k)]
        refs += action.get("node_ids", []) or action.get("ids", [])
        new_aliases = {"$last_created", "__last__", "$last", "{{last_created}}"} if created else set()
        if any(str(ref) not in ids and str(ref) not in new_aliases for ref in refs):
            issues.append("操作引用了画布索引中不存在的节点，请用真实 id；新建后的生成用 target:created")
        if typ == "update_node" and not (action.get("id") or action.get("node_id")):
            issues.append("更新节点缺少目标 id")
        if typ == "update_node":
            fill_doc = action.get("fill_from_document") is True or (
                isinstance(action.get("patch"), dict) and action["patch"].get("fill_from_document") is True
            )
            has_body = any(
                action.get(k) not in (None, "")
                or (isinstance(action.get("patch"), dict) and action["patch"].get(k) not in (None, ""))
                for k in ("prompt", "resultText", "customName")
            )
            if not fill_doc and not has_body and not any(
                isinstance(action.get("patch"), dict) and action["patch"].get(k) is not None
                for k in ("channel", "model", "x", "y", "status", "ratio", "style", "duration", "resolution")
            ):
                issues.append("更新节点缺少可写字段；长提示词请用 fill_from_document:true + source_name，勿把全文塞进 JSON")
        if typ in ("replace_node_text", "edit_image"):
            node = next((n for n in (canvas or {}).get("nodes", []) if isinstance(n, dict) and str(n.get("id")) == str(action.get("id"))), None)
            if not node:
                issues.append("缺少目标节点详情，请先明确节点，不能根据名字猜正文或原图")
            elif typ == "replace_node_text":
                key = {"prompt": "prompt", "resultText": "result_text", "customName": "name"}.get(action.get("field"), "prompt")
                body = str(node.get(key) or (node.get("body") if key == "result_text" else "") or "")
                count = body.count(action["find"])
                if not count or (count > 1 and action.get("all") is not True):
                    issues.append("局部替换原文没有唯一匹配，请引用更完整的原句；只有用户要求全部替换才 all:true")
            elif not node.get("image_url") or node.get("type") not in ("image", "ref-image"):
                issues.append("目标没有可编辑的原图，不能用改提示词冒充改图")
            elif action.get("source_url") and action["source_url"] != node["image_url"]:
                issues.append("原图地址与目标节点不一致")
        if typ == "generate_nodes" and not refs and action.get("target") != "created":
            issues.append("生成操作缺少明确目标；新建后生成必须用 target:created")
        if typ == "generate_nodes" and action.get("target") == "created" and not created:
            issues.append("生成新节点之前缺少创建操作")
        if typ in ("create_nodes", "create_from_script", "split_script"):
            created = True
        if typ == "create_nodes":
            for item in action.get("items", []):
                name = str(item.get("name") or "").strip()
                if name and any(str(n.get("name") or "").strip() == name and not n.get("has_media") for n in index if isinstance(n, dict)):
                    issues.append("同名空节点已存在，请更新该节点而非重复创建")
    return list(dict.fromkeys(issues))


def recovery_plan(user_intent: str, issues: list[str] | None = None) -> dict[str, Any]:
    why = ""
    if issues:
        # Keep user-facing text short; surface the real gate reason so retries aren't blind.
        short = "；".join(str(i).strip() for i in issues[:3] if str(i).strip())
        if short:
            why = f"原因：{short}。"
            if any("截断" in str(i) for i in issues):
                why += "若是按文档改多个长提示词，应改为「只点名节点、由前端从文档回填」，不要让模型把全文再抄进回复。"
    return {
        "thinking": "",
        "reply": (
            "这次没有得到完整、可执行的结果，自动修复后仍未通过检查，未执行画布操作。"
            + (why or "你可以重试原请求，或先核对我理解的任务范围。")
        ),
        "actions": [],
        "choices": [
            {"id": "retry_request", "label": "重试这次请求", "message": user_intent, "actions": []},
            {"id": "clarify_request", "label": "先核对理解和范围", "message": "先不要改动画布。结合以下原请求和原文，说明你理解的目标、已知信息与缺失信息；如有歧义，给出具体选项：\n" + user_intent, "actions": []},
        ],
    }
