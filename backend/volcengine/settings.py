"""Per-user Volcengine channel settings (Ark key + kind toggles)."""

from __future__ import annotations

import logging
from typing import Any

from backend.json_io import load_json, save_json
from backend.user_scope import volcengine_file

logger = logging.getLogger(__name__)

KINDS = ("image", "video", "text", "voice")
DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com"
DEFAULT_SPEECH_BASE = "https://openspeech.bytedance.com"


def _path(user_id: str | None = None):
    return volcengine_file(user_id)


def _empty() -> dict[str, Any]:
    return {
        "api_key": "",
        # openspeech Seed-Audio; falls back to api_key when empty.
        "speech_api_key": "",
        "base_url": DEFAULT_ARK_BASE,
        "speech_base_url": DEFAULT_SPEECH_BASE,
        "voice_app_id": "",
        "voice_speaker": "",
        "kinds_enabled": {k: False for k in KINDS},
        # When true for a kind, new nodes of that kind prefer channel=volcengine.
        "default_kinds": {k: False for k in KINDS},
        # Text catalog ids that appear in 智能体 model picker.
        "text_agent_ids": [],
    }


def _normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = _empty()
    src = raw if isinstance(raw, dict) else {}
    data["api_key"] = str(src.get("api_key") or src.get("key") or "").strip()
    data["speech_api_key"] = str(src.get("speech_api_key") or src.get("voice_api_key") or "").strip()
    base = str(src.get("base_url") or src.get("url") or DEFAULT_ARK_BASE).strip().rstrip("/")
    data["base_url"] = base or DEFAULT_ARK_BASE
    speech = str(src.get("speech_base_url") or DEFAULT_SPEECH_BASE).strip().rstrip("/")
    data["speech_base_url"] = speech or DEFAULT_SPEECH_BASE
    data["voice_app_id"] = str(src.get("voice_app_id") or src.get("app_id") or "").strip()[:80]
    data["voice_speaker"] = str(src.get("voice_speaker") or src.get("speaker") or "").strip()[:120]
    kinds_raw = src.get("kinds_enabled") if isinstance(src.get("kinds_enabled"), dict) else {}
    defaults_raw = src.get("default_kinds") if isinstance(src.get("default_kinds"), dict) else {}
    # Also accept flat enable_image / enable_video …
    for k in KINDS:
        if f"enable_{k}" in src:
            data["kinds_enabled"][k] = bool(src.get(f"enable_{k}"))
        elif k in kinds_raw:
            data["kinds_enabled"][k] = bool(kinds_raw.get(k))
        if f"default_{k}" in src:
            data["default_kinds"][k] = bool(src.get(f"default_{k}"))
        elif k in defaults_raw:
            data["default_kinds"][k] = bool(defaults_raw.get(k))
        # Default only meaningful when kind is enabled.
        if data["default_kinds"][k] and not data["kinds_enabled"][k]:
            data["default_kinds"][k] = False
    raw_ids = src.get("text_agent_ids")
    ids: list[str] = []
    if isinstance(raw_ids, list):
        for item in raw_ids:
            mid = str(item or "").strip()
            if mid and mid not in ids:
                ids.append(mid)
    data["text_agent_ids"] = ids
    return data


def load(user_id: str | None = None) -> dict[str, Any]:
    raw = load_json(_path(user_id), default={})
    return _normalize(raw if isinstance(raw, dict) else {})


def save(patch: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    cur = load(user_id)
    if not isinstance(patch, dict):
        return public_view(cur)
    merged = dict(cur)
    if "api_key" in patch:
        key = str(patch.get("api_key") or "").strip()
        # Empty string on update keeps existing key (like MyApi edit).
        if key or not cur.get("api_key"):
            merged["api_key"] = key
    if "speech_api_key" in patch:
        skey = str(patch.get("speech_api_key") or "").strip()
        if skey or not cur.get("speech_api_key"):
            merged["speech_api_key"] = skey
    if "base_url" in patch:
        merged["base_url"] = str(patch.get("base_url") or DEFAULT_ARK_BASE).strip().rstrip("/") or DEFAULT_ARK_BASE
    if "speech_base_url" in patch:
        merged["speech_base_url"] = (
            str(patch.get("speech_base_url") or DEFAULT_SPEECH_BASE).strip().rstrip("/") or DEFAULT_SPEECH_BASE
        )
    if "voice_app_id" in patch:
        merged["voice_app_id"] = str(patch.get("voice_app_id") or "").strip()[:80]
    if "voice_speaker" in patch:
        merged["voice_speaker"] = str(patch.get("voice_speaker") or "").strip()[:120]
    if isinstance(patch.get("kinds_enabled"), dict):
        for k in KINDS:
            if k in patch["kinds_enabled"]:
                merged["kinds_enabled"][k] = bool(patch["kinds_enabled"][k])
    if isinstance(patch.get("default_kinds"), dict):
        for k in KINDS:
            if k in patch["default_kinds"]:
                merged["default_kinds"][k] = bool(patch["default_kinds"][k])
    for k in KINDS:
        fk = f"enable_{k}"
        if fk in patch:
            merged["kinds_enabled"][k] = bool(patch.get(fk))
        dk = f"default_{k}"
        if dk in patch:
            merged["default_kinds"][k] = bool(patch.get(dk))
    if "text_agent_ids" in patch:
        merged["text_agent_ids"] = patch.get("text_agent_ids")
    cleaned = _normalize(merged)
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, cleaned)
    return public_view(cleaned)


def public_view(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    c = cfg if isinstance(cfg, dict) else load()
    key = str(c.get("api_key") or "")
    speech_key = str(c.get("speech_api_key") or "")
    masked = (("*" * max(0, len(key) - 4)) + key[-4:]) if key else ""
    speech_masked = (("*" * max(0, len(speech_key) - 4)) + speech_key[-4:]) if speech_key else ""
    kinds = {k: bool((c.get("kinds_enabled") or {}).get(k)) for k in KINDS}
    defaults = {k: bool((c.get("default_kinds") or {}).get(k)) for k in KINDS}
    any_kind = any(kinds.values())
    return {
        "api_key_set": bool(key),
        "api_key_masked": masked,
        "speech_api_key_set": bool(speech_key),
        "speech_api_key_masked": speech_masked,
        "base_url": c.get("base_url") or DEFAULT_ARK_BASE,
        "speech_base_url": c.get("speech_base_url") or DEFAULT_SPEECH_BASE,
        "voice_app_id": c.get("voice_app_id") or "",
        "voice_speaker": c.get("voice_speaker") or "",
        "kinds_enabled": kinds,
        "default_kinds": defaults,
        "enable_image": kinds["image"],
        "enable_video": kinds["video"],
        "enable_text": kinds["text"],
        "enable_voice": kinds["voice"],
        "default_image": defaults["image"],
        "default_video": defaults["video"],
        "default_text": defaults["text"],
        "default_voice": defaults["voice"],
        "text_agent_ids": list(c.get("text_agent_ids") or []),
        # Channel usable when Ark key present and at least one kind on.
        "channel_ready": bool(key) and any_kind,
    }


def is_kind_enabled(kind: str, user_id: str | None = None) -> bool:
    cfg = load(user_id)
    k = str(kind or "").strip().lower()
    return bool((cfg.get("kinds_enabled") or {}).get(k))


def require_ready(kind: str, user_id: str | None = None) -> dict[str, Any]:
    cfg = load(user_id)
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        raise RuntimeError(f"不支持的类型：{kind}")
    if k == "voice":
        if not (cfg.get("speech_api_key") or cfg.get("api_key")):
            raise RuntimeError("请先在设置 → 火山引擎 填写语音 Key（openspeech）或方舟 API Key")
    elif not cfg.get("api_key"):
        raise RuntimeError("请先在设置 → 火山引擎 中填写方舟 API Key")
    if not (cfg.get("kinds_enabled") or {}).get(k):
        raise RuntimeError(f"火山引擎未开启「{k}」，请在设置中打开开关")
    return cfg


def build_model_cfg(catalog: dict[str, Any], user_cfg: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Merge catalog entry + user credentials into a cfg for VolcengineStyle / myapi client."""
    use_speech = kind == "voice"
    return {
        "id": catalog.get("id"),
        "kind": kind,
        "name": catalog.get("label") or catalog.get("name") or catalog.get("id"),
        "model": catalog.get("model") or catalog.get("id"),
        "base_url": (
            user_cfg.get("speech_base_url") if use_speech else user_cfg.get("base_url")
        )
        or (DEFAULT_SPEECH_BASE if use_speech else DEFAULT_ARK_BASE),
        "speech_base_url": user_cfg.get("speech_base_url") or DEFAULT_SPEECH_BASE,
        "api_key": user_cfg.get("api_key") or "",
        "speech_api_key": user_cfg.get("speech_api_key") or "",
        "api_style": "volcengine",
        "send_size": True,
        "send_duration": True,
        "duration_min": catalog.get("duration_min") or 4,
        "duration_max": catalog.get("duration_max") or 10,
        "max_images": catalog.get("max_images", 9 if kind != "voice" else 1),
        "max_videos": catalog.get("max_videos", 3 if kind == "video" else 0),
        "max_audios": catalog.get("max_audios", 3 if kind in ("video", "voice") else 0),
        "ratios": list(catalog.get("ratios") or []),
        "resolutions": list(catalog.get("resolutions") or []),
        "voice_app_id": user_cfg.get("voice_app_id") or "",
        "voice_speaker": user_cfg.get("voice_speaker") or "",
        "voice_format": "mp3",
        "volcengine": True,
        "multimodal": bool(catalog.get("multimodal")),
        "chat_completions_path": "api/v3/chat/completions",
        # Seedance 2.x：默认生成有声视频（官方示例 generate_audio=true）
        "generate_audio": bool(catalog.get("generate_audio", kind == "video")),
        "watermark": False,
    }


def build_agent_cfg(model_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Text catalog entry enabled for 智能体 → cfg chat_completion can use."""
    from backend.volcengine import models as ve_models

    catalog = ve_models.get_model(model_id)
    if not catalog or catalog.get("kind") != "text" or not catalog.get("agent_capable", True):
        return None
    cfg = load(user_id)
    if not cfg.get("api_key"):
        return None
    if not (cfg.get("kinds_enabled") or {}).get("text"):
        return None
    allowed = {str(x) for x in (cfg.get("text_agent_ids") or [])}
    if str(catalog.get("id") or "") not in allowed:
        return None
    built = build_model_cfg(catalog, cfg, kind="text")
    built["kind"] = "agent"
    built["enabled"] = True
    built["multimodal"] = bool(catalog.get("multimodal"))
    return built


def list_agent_picker(user_id: str | None = None) -> list[dict[str, Any]]:
    from backend.volcengine import models as ve_models

    cfg = load(user_id)
    if not cfg.get("api_key") or not (cfg.get("kinds_enabled") or {}).get("text"):
        return []
    allowed = {str(x) for x in (cfg.get("text_agent_ids") or [])}
    out: list[dict[str, Any]] = []
    for m in ve_models.VOLCENGINE_TEXT_MODELS:
        if not m.get("agent_capable", True):
            continue
        if str(m.get("id") or "") not in allowed:
            continue
        entry = ve_models.picker_entry(m)
        entry["kind"] = "agent"
        entry["volcengine"] = True
        out.append(entry)
    return out
