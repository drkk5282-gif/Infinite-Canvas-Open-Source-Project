"""Admin-managed MyApi recommended templates (no API key; users fill their own)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json
from backend.myapi_store import (
    API_STYLES,
    DEFAULT_API_STYLE,
    DEFAULT_DURATION_MAX,
    DEFAULT_DURATION_MIN,
    DEFAULT_IMAGE_RATIOS,
    DEFAULT_MAX_AUDIOS,
    DEFAULT_MAX_IMAGES,
    DEFAULT_MAX_VIDEOS,
    DEFAULT_SELECTED_VIDEO_RATIOS,
    DEFAULT_STOKEN_VIDEO_PROTOCOL,
    KINDS,
    STOKEN_VIDEO_PROTOCOLS,
    _int_field,
    _list_field,
    filter_api_ratio_options,
    filter_api_resolution_options,
)

logger = logging.getLogger(__name__)

TEMPLATES_FILE = DATA_DIR / "myapi_templates.json"


def _path():
    return TEMPLATES_FILE


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {"image": [], "video": [], "text": [], "agent": [], "vision": [], "voice": []}


def _normalize_entry(raw: dict[str, Any], kind: str) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    base_url = str(raw.get("base_url") or raw.get("url") or "").strip().rstrip("/")
    model = str(raw.get("model") or raw.get("model_id") or name).strip()
    if not name or not base_url:
        return None
    eid = str(raw.get("id") or "").strip()
    if not eid.startswith("tpl_"):
        eid = f"tpl_{kind}_{uuid.uuid4().hex[:10]}"
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
        kind=kind,
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
    try:
        sort = int(raw.get("sort", 0))
    except (TypeError, ValueError):
        sort = 0
    return {
        "id": eid,
        "kind": kind,
        "name": name[:80],
        "model": model[:120] or name[:80],
        "base_url": base_url,
        "send_size": bool(raw.get("send_size", True)),
        "send_duration": bool(raw.get("send_duration", True)),
        "duration_min": duration_min,
        "duration_max": duration_max,
        "max_images": _int_field(raw, "max_images", DEFAULT_MAX_IMAGES, lo=0, hi=50),
        "max_videos": _int_field(raw, "max_videos", DEFAULT_MAX_VIDEOS, lo=0, hi=50),
        "max_audios": _int_field(raw, "max_audios", DEFAULT_MAX_AUDIOS, lo=0, hi=50),
        "ratios": ratios,
        "resolutions": resolutions,
        "api_style": api_style if kind in ("video", "image", "text", "voice") else DEFAULT_API_STYLE,
        "video_protocol": video_protocol if kind == "video" and api_style == "4stoken" else DEFAULT_STOKEN_VIDEO_PROTOCOL,
        "enabled": bool(raw.get("enabled", True)),
        "sort": sort,
        "description": str(raw.get("description") or "").strip()[:200],
        "voice_speaker": str(raw.get("voice_speaker") or "").strip()[:120] if kind == "voice" else "",
        "voice_app_id": str(raw.get("voice_app_id") or "").strip()[:80] if kind == "voice" else "",
        "voice_format": (
            str(raw.get("voice_format") or "mp3").strip().lower()[:20] if kind == "voice" else "mp3"
        ),
    }


def load() -> dict[str, list[dict[str, Any]]]:
    data = _empty()
    raw = load_json(_path(), default={})
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
        out.sort(key=lambda x: (int(x.get("sort") or 0), x.get("name") or ""))
        data[kind] = out
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
        cleaned[kind].sort(key=lambda x: (int(x.get("sort") or 0), x.get("name") or ""))
    save_json(_path(), cleaned)
    return cleaned


def list_templates(kind: str | None = None, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    data = load()
    items: list[dict[str, Any]] = []
    if kind in KINDS:
        items = list(data[kind])
    else:
        for k in KINDS:
            items.extend(data[k])
    if enabled_only:
        items = [m for m in items if m.get("enabled", True)]
    return items


def get_template(template_id: str | None) -> dict[str, Any] | None:
    if not template_id:
        return None
    tid = str(template_id).strip()
    for item in list_templates():
        if item.get("id") == tid:
            return item
    return None


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
    if idx >= 0:
        items[idx] = norm
    else:
        items.append(norm)
    data[kind] = items
    save(data)
    return norm


def delete(template_id: str) -> bool:
    data = load()
    found = False
    for kind in KINDS:
        before = len(data[kind])
        data[kind] = [x for x in data[kind] if x.get("id") != template_id]
        if len(data[kind]) < before:
            found = True
    if found:
        save(data)
    return found


def public_list(kind: str | None = None) -> list[dict[str, Any]]:
    """Canvas-safe list of enabled templates (no secrets)."""
    return list_templates(kind, enabled_only=True)
