"""Frontend display-name overrides for channels / built-in models.

Only affects UI labels and model remark (desc). Operational IDs and routing
are never changed. MyApi model names are NOT overridden here (edit MyApi /
public models instead); only the MyApi channel label is editable.
"""

from __future__ import annotations

from typing import Any

CHANNEL_DEFAULTS: list[dict[str, str]] = [
    {"id": "doubao", "default_label": "Seed"},
    {"id": "doubao_s", "default_label": "Seed S"},
    {"id": "bendi", "default_label": "BenDi"},
    {"id": "myapi", "default_label": "MyApi"},
    {"id": "volcengine", "default_label": "火山引擎"},
]

# Built-in catalogs only. Never list myapi_* / public_* here.
MODEL_DEFAULTS: list[dict[str, str]] = [
    # Seed video
    {
        "id": "seedance_v2.5",
        "kind": "video",
        "group": "Seed 视频",
        "default_label": "Seedance 2.5",
        "default_desc": "旗舰视频标杆 · 5 倍消耗",
    },
    {
        "id": "seedance_v2.0",
        "kind": "video",
        "group": "Seed 视频",
        "default_label": "Seedance 2.0",
        "default_desc": "进阶画面表现 · 2 倍消耗",
    },
    {
        "id": "seedance_v2.0_fast",
        "kind": "video",
        "group": "Seed 视频",
        "default_label": "Seedance 2.0 Fast",
        "default_desc": "快速出片选择",
    },
    {
        "id": "seedance_v2.0_mini",
        "kind": "video",
        "group": "Seed 视频",
        "default_label": "Seedance 2.0 Mini",
        "default_desc": "日常生成使用",
    },
    {
        "id": "seedance_v2.0_mini_chat",
        "kind": "video",
        "group": "Seed 视频",
        "default_label": "Seedance 2.0 Mini Chat",
        "default_desc": "聊天框生成 · 4–15 秒 · 尺寸时长写入提示词",
    },
    # Seed image
    {
        "id": "seedream_v5.0_pro",
        "kind": "image",
        "group": "Seed 图片",
        "default_label": "Seedream 5.0 Pro",
        "default_desc": "专业出图 · 4 倍消耗",
    },
    {
        "id": "seedream_v5.0_lite",
        "kind": "image",
        "group": "Seed 图片",
        "default_label": "Seedream 5.0 Lite",
        "default_desc": "进阶效果 · 3 倍消耗",
    },
    {
        "id": "seedream_v4.5",
        "kind": "image",
        "group": "Seed 图片",
        "default_label": "Seedream 4.5",
        "default_desc": "日常生成",
    },
    {
        "id": "seedream_v4.0",
        "kind": "image",
        "group": "Seed 图片",
        "default_label": "Seedream 4.0",
        "default_desc": "基础生图",
    },
    # BenDi video
    {
        "id": "seedance-2.0-fast",
        "kind": "video",
        "group": "BenDi 视频",
        "default_label": "Seedance 2.0 Fast",
        "default_desc": "本机 · 5/10 秒",
    },
    {
        "id": "seedance-2.0",
        "kind": "video",
        "group": "BenDi 视频",
        "default_label": "Seedance 2.0",
        "default_desc": "本机 · 15 秒",
    },
    {
        "id": "seedance-2.0-mini",
        "kind": "video",
        "group": "BenDi 视频",
        "default_label": "Seedance 2.0 Mini",
        "default_desc": "本机 · 5/10/15 秒",
    },
    {
        "id": "seedance-2.5",
        "kind": "video",
        "group": "BenDi 视频",
        "default_label": "Seedance 2.5",
        "default_desc": "本机 · 30 秒",
    },
    # BenDi image
    {
        "id": "bendi_image",
        "kind": "image",
        "group": "BenDi 图片",
        "default_label": "BenDi 图片",
        "default_desc": "本机文生图 / 参考图",
    },
    {
        "id": "seedream-4.5",
        "kind": "image",
        "group": "BenDi 图片",
        "default_label": "Seedream 4.5",
        "default_desc": "本地文生图 / 参考图",
    },
    {
        "id": "seedream-5.0-pro",
        "kind": "image",
        "group": "BenDi 图片",
        "default_label": "Seedream 5.0 Pro",
        "default_desc": "本地文生图 / 参考图",
    },
    {
        "id": "gpt-image",
        "kind": "image",
        "group": "BenDi 图片",
        "default_label": "GPT Image",
        "default_desc": "本地 · ChatGPT 网页生图",
    },
    # Built-in text (non-MyApi)
    {
        "id": "doubao_chat",
        "kind": "text",
        "group": "文字",
        "default_label": "Seed Chat",
        "default_desc": "",
    },
    {
        "id": "dola-chat",
        "kind": "text",
        "group": "BenDi 文字",
        "default_label": "BenDi Chat",
        "default_desc": "本地普通对话 / 带图提问",
    },
    {
        "id": "chatgpt",
        "kind": "text",
        "group": "BenDi 文字",
        "default_label": "ChatGPT",
        "default_desc": "本地 · ChatGPT 对话 / 图生图",
    },
]

_ALLOWED_CHANNEL_IDS = {c["id"] for c in CHANNEL_DEFAULTS}
_ALLOWED_MODEL_IDS = {m["id"] for m in MODEL_DEFAULTS}


def empty_ui_display() -> dict[str, Any]:
    return {"channels": {}, "models": {}}


def catalog() -> dict[str, Any]:
    return {
        "channels": [dict(c) for c in CHANNEL_DEFAULTS],
        "models": [dict(m) for m in MODEL_DEFAULTS],
    }


def _trim(value: Any, max_len: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def normalize_ui_display(raw: Any) -> dict[str, Any]:
    """Keep only known ids; strip empty overrides; never touch operational IDs."""
    out = empty_ui_display()
    if not isinstance(raw, dict):
        return out

    channels = raw.get("channels")
    if isinstance(channels, dict):
        for cid, entry in channels.items():
            key = str(cid or "").strip()
            if key not in _ALLOWED_CHANNEL_IDS:
                continue
            if isinstance(entry, str):
                label = _trim(entry)
                if label:
                    out["channels"][key] = {"label": label}
                continue
            if not isinstance(entry, dict):
                continue
            label = _trim(entry.get("label"))
            if label:
                out["channels"][key] = {"label": label}

    models = raw.get("models")
    if isinstance(models, dict):
        for mid, entry in models.items():
            key = str(mid or "").strip()
            if key not in _ALLOWED_MODEL_IDS:
                continue
            # Safety: never allow MyApi/public model id overrides via this map.
            if key.startswith("myapi_") or key.startswith("public_"):
                continue
            if not isinstance(entry, dict):
                continue
            label = _trim(entry.get("label"))
            desc = _trim(entry.get("desc"), max_len=160)
            patch: dict[str, str] = {}
            if label:
                patch["label"] = label
            if desc:
                patch["desc"] = desc
            if patch:
                out["models"][key] = patch

    return out


def public_ui_display() -> dict[str, Any]:
    from backend import service_settings

    return normalize_ui_display(service_settings.load().get("ui_display"))
