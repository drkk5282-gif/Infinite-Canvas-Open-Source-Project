"""Custom MyApi channel models (image / video / text / agent relay endpoints)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

from backend.user_scope import myapi_file as _myapi_file


def _models_path():
    return _myapi_file()
Kind = Literal["image", "video", "text", "agent", "vision", "voice"]
KINDS: tuple[str, ...] = ("image", "video", "text", "agent", "vision", "voice")
KIND_LABELS: dict[str, str] = {
    "image": "图片",
    "video": "视频",
    "text": "文本",
    "agent": "智能体",
    "vision": "视觉",
    "voice": "语音",
}
API_STYLES: tuple[str, ...] = ("generic",)
DEFAULT_API_STYLE = "generic"
STOKEN_VIDEO_PROTOCOLS: tuple[str, ...] = ("auto", "newapi", "gateway", "seedance", "gemini")
DEFAULT_STOKEN_VIDEO_PROTOCOL = "auto"

# Display labels for admin toggles / UI
API_STYLE_LABELS: dict[str, str] = {
    "generic": "通用",
    "volcengine": "火山引擎",
}
STOKEN_VIDEO_PROTOCOL_LABELS: dict[str, str] = {
    "auto": "自动（按模型识别）",
    "newapi": "NewAPI /v1/video/generations",
    "gateway": "Gateway /v1/videos",
    "seedance": "Seedance /api/v3/…/tasks",
    "gemini": "Gemini Veo :predict",
}

DEFAULT_MAX_IMAGES = 9
DEFAULT_MAX_VIDEOS = 3
DEFAULT_MAX_AUDIOS = 3
DEFAULT_DURATION_MIN = 4
DEFAULT_DURATION_MAX = 10
DEFAULT_VIDEO_RATIOS = ["16:9", "3:4", "4:3", "9:16", "1:1", "21:9"]
DEFAULT_SELECTED_VIDEO_RATIOS = ["16:9"]
DEFAULT_VIDEO_RESOLUTIONS = ["480p", "720p", "1080p"]
DEFAULT_IMAGE_RATIOS = ["自动", "16:9", "9:16", "1:1"]
_AUTO_RATIO_RES = frozenset({"自动", "auto", "Auto"})


def filter_api_ratio_options(items: list[str], *, kind: str = "video") -> list[str]:
    """Video APIs reject 自动; image ratio is baked into the prompt and 自动 = omit it."""
    raw = [str(x).strip() for x in items if str(x).strip()]
    if kind == "image":
        concrete: list[str] = []
        seen: set[str] = set()
        for x in raw:
            if x in _AUTO_RATIO_RES or x in seen:
                continue
            seen.add(x)
            concrete.append(x)
        if not concrete:
            concrete = [x for x in DEFAULT_IMAGE_RATIOS if x not in _AUTO_RATIO_RES]
        return ["自动", *concrete]
    out = [x for x in raw if x not in _AUTO_RATIO_RES]
    if out:
        return out
    return list(DEFAULT_SELECTED_VIDEO_RATIOS)


def filter_api_resolution_options(items: list[str]) -> list[str]:
    return [x for x in items if str(x).strip() and str(x).strip() not in _AUTO_RATIO_RES]


def _int_field(raw: dict[str, Any], key: str, default: int, *, lo: int = 0, hi: int = 999) -> int:
    try:
        val = int(raw.get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _list_field(raw: dict[str, Any], key: str, default: list[str]) -> list[str]:
    val = raw.get(key)
    if isinstance(val, list):
        items = [str(x).strip() for x in val if str(x).strip()]
        return items or list(default)
    if isinstance(val, str):
        items = [x.strip() for x in val.replace("，", ",").split(",") if x.strip()]
        return items or list(default)
    return list(default)


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {"image": [], "video": [], "text": [], "agent": [], "vision": [], "voice": []}


def _normalize_entry(raw: dict[str, Any], kind: str) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    base_url = str(raw.get("base_url") or raw.get("url") or "").strip().rstrip("/")
    api_key = str(raw.get("api_key") or raw.get("key") or "").strip()
    model = str(raw.get("model") or raw.get("model_id") or name).strip()
    if not name or not base_url:
        return None
    eid = str(raw.get("id") or "").strip()
    if not eid.startswith("myapi_"):
        eid = f"myapi_{kind}_{uuid.uuid4().hex[:10]}"
    duration_min = _int_field(raw, "duration_min", DEFAULT_DURATION_MIN, lo=1, hi=600)
    duration_max = _int_field(raw, "duration_max", DEFAULT_DURATION_MAX, lo=1, hi=600)
    if duration_max < duration_min:
        duration_min, duration_max = duration_max, duration_min
    ratios = filter_api_ratio_options(
        _list_field(
            raw,
            "ratios",
            DEFAULT_SELECTED_VIDEO_RATIOS if kind == "video" else DEFAULT_IMAGE_RATIOS,
        ),
        kind=kind if kind in ("image", "video") else "image",
    )
    resolutions = filter_api_resolution_options(
        _list_field(
            raw,
            "resolutions",
            [],
        )
    ) if kind == "video" else []
    api_style = str(raw.get("api_style") or DEFAULT_API_STYLE).strip().lower()
    if api_style not in API_STYLES:
        api_style = DEFAULT_API_STYLE
    video_protocol = str(raw.get("video_protocol") or DEFAULT_STOKEN_VIDEO_PROTOCOL).strip().lower()
    if video_protocol not in STOKEN_VIDEO_PROTOCOLS:
        video_protocol = DEFAULT_STOKEN_VIDEO_PROTOCOL
    multimodal = bool(raw.get("multimodal") or raw.get("vision_builtin"))
    vision_model_id = str(raw.get("vision_model_id") or "").strip()[:80]
    # Legacy free-text vision_model kept for backward compat display only
    vision_model = str(raw.get("vision_model") or "").strip()[:120]
    whisper_model = str(raw.get("whisper_model") or raw.get("stt_model") or "").strip()[:120]
    voice_speaker = str(raw.get("voice_speaker") or raw.get("speaker") or "").strip()[:120]
    voice_app_id = str(raw.get("voice_app_id") or raw.get("app_id") or "").strip()[:80]
    voice_format = str(raw.get("voice_format") or raw.get("audio_format") or "mp3").strip().lower()[:20]
    if voice_format not in ("mp3", "wav", "pcm", "ogg_opus"):
        voice_format = "mp3"
    if kind != "agent":
        multimodal = False
        vision_model_id = ""
        vision_model = ""
        whisper_model = ""
    if kind != "voice":
        voice_speaker = ""
        voice_app_id = ""
        voice_format = "mp3"
    if multimodal:
        vision_model_id = ""
        vision_model = ""
    # Voice models default to volcengine / openspeech when unset.
    if kind == "voice" and api_style == DEFAULT_API_STYLE and "openspeech" in base_url.lower():
        api_style = "volcengine"
    return {
        "id": eid,
        "kind": kind,
        "name": name[:80],
        "model": model[:120] or name[:80],
        "base_url": base_url,
        "api_key": api_key,
        # Prefer sending size/duration as API fields when possible; always also bake into prompt as fallback
        "send_size": bool(raw.get("send_size", True)),
        "send_duration": bool(raw.get("send_duration", True)),
        "duration_min": duration_min,
        "duration_max": duration_max,
        "max_images": _int_field(raw, "max_images", DEFAULT_MAX_IMAGES if kind != "voice" else 1, lo=0, hi=50),
        "max_videos": _int_field(raw, "max_videos", DEFAULT_MAX_VIDEOS if kind != "voice" else 0, lo=0, hi=50),
        "max_audios": _int_field(raw, "max_audios", DEFAULT_MAX_AUDIOS if kind != "voice" else 3, lo=0, hi=50),
        "ratios": ratios if kind in ("image", "video") else [],
        "resolutions": resolutions,
        "api_style": api_style if kind in ("video", "image", "text", "agent", "vision", "voice") else DEFAULT_API_STYLE,
        "video_protocol": video_protocol if kind == "video" and api_style == "4stoken" else DEFAULT_STOKEN_VIDEO_PROTOCOL,
        "multimodal": multimodal,
        "vision_model_id": vision_model_id,
        "vision_model": vision_model,
        "whisper_model": whisper_model,
        "voice_speaker": voice_speaker,
        "voice_app_id": voice_app_id,
        "voice_format": voice_format,
        "is_default": bool(raw.get("is_default")),
    }


def _enforce_single_default(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most one is_default per kind; keep the first marked default if multiple sneaked in."""
    seen = False
    for it in items:
        if not it.get("is_default"):
            it["is_default"] = False
            continue
        if seen:
            it["is_default"] = False
        else:
            it["is_default"] = True
            seen = True
    return items


def load() -> dict[str, list[dict[str, Any]]]:
    data = _empty()
    raw = load_json(_models_path(), default={})
    if not isinstance(raw, dict):
        return data
    for kind in KINDS:
        items = raw.get(kind) or []
        if not isinstance(items, list):
            continue
        out: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            norm = _normalize_entry(it, kind)
            if norm:
                out.append(norm)
        data[kind] = _enforce_single_default(out)
    return data


def save(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = _empty()
    for kind in KINDS:
        items = data.get(kind) or []
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            norm = _normalize_entry(it, kind)
            if norm:
                cleaned[kind].append(norm)
        cleaned[kind] = _enforce_single_default(cleaned[kind])
    save_json(_models_path(), cleaned)
    return cleaned


def list_models(kind: str | None = None) -> list[dict[str, Any]]:
    data = load()
    if kind in KINDS:
        return list(data[kind])
    out: list[dict[str, Any]] = []
    for k in KINDS:
        out.extend(data[k])
    return out


def get_model(model_id: str | None) -> dict[str, Any] | None:
    if not model_id:
        return None
    mid = str(model_id).strip()
    for item in list_models():
        if item.get("id") == mid:
            return item
    return None


def has_models(kind: str) -> bool:
    return bool(kind in KINDS and load().get(kind))


def public_list(kind: str | None = None) -> list[dict[str, Any]]:
    """Frontend-safe list (api_key masked)."""
    out = []
    for it in list_models(kind):
        key = str(it.get("api_key") or "")
        masked = (("*" * max(0, len(key) - 4)) + key[-4:]) if key else ""
        out.append(
            {
                "id": it["id"],
                "kind": it["kind"],
                "name": it["name"],
                "model": it["model"],
                "base_url": it["base_url"],
                "api_key_set": bool(key),
                "api_key_masked": masked,
                "send_size": it.get("send_size", True),
                "send_duration": it.get("send_duration", True),
                "duration_min": it.get("duration_min", DEFAULT_DURATION_MIN),
                "duration_max": it.get("duration_max", DEFAULT_DURATION_MAX),
                "max_images": it.get("max_images", DEFAULT_MAX_IMAGES),
                "max_videos": it.get("max_videos", DEFAULT_MAX_VIDEOS),
                "max_audios": it.get("max_audios", DEFAULT_MAX_AUDIOS),
                "ratios": it.get("ratios") or (
                    DEFAULT_SELECTED_VIDEO_RATIOS if it.get("kind") == "video" else DEFAULT_IMAGE_RATIOS
                ),
                "resolutions": list(it.get("resolutions") or []),
                "api_style": it.get("api_style") or DEFAULT_API_STYLE,
                "video_protocol": it.get("video_protocol") or DEFAULT_STOKEN_VIDEO_PROTOCOL,
                "multimodal": bool(it.get("multimodal")),
                "vision_model_id": it.get("vision_model_id") or "",
                "vision_model": it.get("vision_model") or "",
                "whisper_model": it.get("whisper_model") or "",
                "voice_speaker": it.get("voice_speaker") or "",
                "voice_app_id": it.get("voice_app_id") or "",
                "voice_format": it.get("voice_format") or "mp3",
                "is_default": bool(it.get("is_default")),
            }
        )
    return out


def upsert(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("kind must be image|video|text|agent|vision")
    data = load()
    norm = _normalize_entry({**body, "kind": kind}, kind)
    if not norm:
        raise ValueError("name 与 URL 必填")
    if kind == "video" and not (norm.get("resolutions") or []):
        raise ValueError("请至少勾选一个分辨率")
    items = data[kind]
    idx = next((i for i, x in enumerate(items) if x["id"] == norm["id"]), -1)
    # Keep existing key if client sent empty (edit without retyping)
    if idx >= 0 and not norm.get("api_key") and items[idx].get("api_key"):
        norm["api_key"] = items[idx]["api_key"]
    # Preserve default unless the client explicitly sends is_default
    if idx >= 0 and "is_default" not in body:
        norm["is_default"] = bool(items[idx].get("is_default"))
    # New / duplicate entries are never default unless explicitly requested
    if idx < 0 and "is_default" not in body:
        norm["is_default"] = False
    # Duplicate: copy key from source model when creating new entry
    if idx < 0 and not norm.get("api_key"):
        clone_from = str(body.get("clone_from") or "").strip()
        if clone_from:
            src = get_model(clone_from)
            if src and src.get("api_key"):
                norm["api_key"] = str(src["api_key"])
    if idx >= 0:
        items[idx] = norm
    else:
        items.append(norm)
    if norm.get("is_default"):
        for it in items:
            if it["id"] != norm["id"]:
                it["is_default"] = False
    data[kind] = items
    save(data)
    return norm


def set_default(model_id: str, is_default: bool = True) -> dict[str, Any] | None:
    """Toggle default for one model; at most one default per kind."""
    mid = str(model_id or "").strip()
    if not mid:
        return None
    data = load()
    target: dict[str, Any] | None = None
    target_kind: str | None = None
    for kind in KINDS:
        for it in data[kind]:
            if it.get("id") == mid:
                target = it
                target_kind = kind
                break
        if target:
            break
    if not target or not target_kind:
        return None
    if is_default:
        for it in data[target_kind]:
            it["is_default"] = it.get("id") == mid
    else:
        target["is_default"] = False
    save(data)
    return get_model(mid)


def delete(model_id: str) -> bool:
    data = load()
    found = False
    for kind in KINDS:
        before = len(data[kind])
        data[kind] = [x for x in data[kind] if x.get("id") != model_id]
        if len(data[kind]) < before:
            found = True
    if found:
        save(data)
    return found


def is_myapi_model(model_id: str | None) -> bool:
    s = str(model_id or "").strip().lower()
    return s.startswith("myapi_") or bool(get_model(model_id))
