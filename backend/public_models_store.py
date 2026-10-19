"""Admin-managed public relay models (shared keys, optional points billing)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

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
    STOKEN_VIDEO_PROTOCOLS,
    _int_field,
    _list_field,
    filter_api_ratio_options,
    filter_api_resolution_options,
)
from backend.points import billing_label, format_points, parse_points

logger = logging.getLogger(__name__)

MODELS_FILE = DATA_DIR / "public_models.json"
# 公共模型独立 kind：不含 vision（视觉仍走用户 MyApi）
PUBLIC_KINDS: tuple[str, ...] = ("image", "video", "text", "agent", "voice")
Kind = Literal["image", "video", "text", "agent", "voice"]

BILLING_MODES = {
    "image": ("per_use",),
    "video": ("per_use", "per_second"),
    "text": ("per_use", "per_token", "per_char"),  # per_char kept as alias of per_token
    "agent": ("per_use", "per_token", "per_char"),
    "voice": ("per_use",),
}


def _path():
    return MODELS_FILE


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {k: [] for k in PUBLIC_KINDS}


def _default_billing_mode(kind: str) -> str:
    return BILLING_MODES.get(kind, ("per_use",))[0]


def _normalize_entry(raw: dict[str, Any], kind: str) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    base_url = str(raw.get("base_url") or raw.get("url") or "").strip().rstrip("/")
    api_key = str(raw.get("api_key") or raw.get("key") or "").strip()
    model = str(raw.get("model") or raw.get("model_id") or name).strip()
    if not name or not base_url:
        return None
    eid = str(raw.get("id") or "").strip()
    if not eid.startswith("public_"):
        eid = f"public_{kind}_{uuid.uuid4().hex[:10]}"
    duration_min = _int_field(raw, "duration_min", DEFAULT_DURATION_MIN, lo=1, hi=600)
    duration_max = _int_field(raw, "duration_max", DEFAULT_DURATION_MAX, lo=1, hi=600)
    if duration_max < duration_min:
        duration_min, duration_max = duration_max, duration_min
    ratios = (
        filter_api_ratio_options(
            _list_field(
                raw,
                "ratios",
                DEFAULT_SELECTED_VIDEO_RATIOS if kind == "video" else DEFAULT_IMAGE_RATIOS,
            ),
            kind=kind,
        )
        if kind in ("image", "video")
        else []
    )
    resolutions = filter_api_resolution_options(
        _list_field(
            raw,
            "resolutions",
            [],
        )
    ) if kind == "video" else []
    api_style = str(raw.get("api_style") or DEFAULT_API_STYLE).strip().lower()
    if kind == "agent":
        api_style = "generic"
    elif api_style not in API_STYLES:
        api_style = DEFAULT_API_STYLE
    video_protocol = str(raw.get("video_protocol") or DEFAULT_STOKEN_VIDEO_PROTOCOL).strip().lower()
    if video_protocol not in STOKEN_VIDEO_PROTOCOLS:
        video_protocol = DEFAULT_STOKEN_VIDEO_PROTOCOL
    allowed = BILLING_MODES.get(kind, ("per_use",))
    billing_mode = str(raw.get("billing_mode") or _default_billing_mode(kind)).strip().lower()
    if billing_mode == "per_char":
        billing_mode = "per_token"
    if billing_mode not in allowed:
        billing_mode = allowed[0]
    points_price = format_points(parse_points(raw.get("points_price"), default="0.000"))
    resolution_prices = _normalize_resolution_prices(raw.get("resolution_prices"), points_price, resolutions if kind == "video" else [])
    return {
        "id": eid,
        "kind": kind,
        "name": name[:80],
        "model": model[:120] or name[:80],
        "base_url": base_url,
        "api_key": api_key,
        "send_size": bool(raw.get("send_size", True)),
        "send_duration": bool(raw.get("send_duration", True)),
        "duration_min": duration_min,
        "duration_max": duration_max,
        "max_images": _int_field(raw, "max_images", DEFAULT_MAX_IMAGES, lo=0, hi=50),
        "max_videos": _int_field(raw, "max_videos", DEFAULT_MAX_VIDEOS, lo=0, hi=50),
        "max_audios": _int_field(raw, "max_audios", DEFAULT_MAX_AUDIOS, lo=0, hi=50),
        "ratios": ratios if kind in ("image", "video") else [],
        "resolutions": resolutions,
        "api_style": api_style if kind in ("video", "image", "text", "voice") else "generic",
        "video_protocol": video_protocol if kind == "video" and api_style == "4stoken" else DEFAULT_STOKEN_VIDEO_PROTOCOL,
        "enabled": bool(raw.get("enabled", True)),
        "points_required": bool(raw.get("points_required", False)),
        "billing_mode": billing_mode,
        "points_price": points_price,
        "resolution_prices": resolution_prices if kind == "video" else {},
        "multimodal": bool(raw.get("multimodal", False)) if kind == "agent" else False,
        "vision_model": str(raw.get("vision_model") or "").strip()[:120] if kind == "agent" else "",
        "vision_model_id": str(raw.get("vision_model_id") or "").strip()[:80] if kind == "agent" else "",
        "voice_speaker": str(raw.get("voice_speaker") or raw.get("speaker") or "").strip()[:120] if kind == "voice" else "",
        "voice_app_id": str(raw.get("voice_app_id") or raw.get("app_id") or "").strip()[:80] if kind == "voice" else "",
        "voice_format": (
            (
                vf
                if (vf := str(raw.get("voice_format") or raw.get("audio_format") or "mp3").strip().lower()[:20])
                in ("mp3", "wav", "pcm", "ogg_opus")
                else "mp3"
            )
            if kind == "voice"
            else "mp3"
        ),
    }


def _normalize_resolution_prices(raw: Any, fallback: str, resolutions: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    src = raw if isinstance(raw, dict) else {}
    keys = list(resolutions) if resolutions else [str(k) for k in src.keys()]
    for key in keys:
        k = str(key).strip()
        if not k:
            continue
        val = src.get(k, fallback)
        out[k] = format_points(parse_points(val, default=fallback or "0.000"))
    return out


def load() -> dict[str, list[dict[str, Any]]]:
    data = _empty()
    raw = load_json(_path(), default={})
    if not isinstance(raw, dict):
        return data
    for kind in PUBLIC_KINDS:
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
        data[kind] = out
    return data


def save(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = _empty()
    for kind in PUBLIC_KINDS:
        items = data.get(kind) or []
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            norm = _normalize_entry(it, kind)
            if norm:
                cleaned[kind].append(norm)
    save_json(_path(), cleaned)
    return cleaned


def list_models(kind: str | None = None, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    data = load()
    items: list[dict[str, Any]] = []
    if kind in PUBLIC_KINDS:
        items = list(data[kind])
    else:
        for k in PUBLIC_KINDS:
            items.extend(data[k])
    if enabled_only:
        items = [m for m in items if m.get("enabled", True)]
    return items


def get_model(model_id: str | None, *, require_enabled: bool = False) -> dict[str, Any] | None:
    if not model_id:
        return None
    mid = str(model_id).strip()
    for item in list_models():
        if item.get("id") == mid:
            if require_enabled and not item.get("enabled", True):
                return None
            return item
    return None


def is_public_model(model_id: str | None) -> bool:
    s = str(model_id or "").strip().lower()
    return s.startswith("public_") or bool(get_model(model_id))


def public_list(kind: str | None = None, *, enabled_only: bool = True, mask_key: bool = True) -> list[dict[str, Any]]:
    out = []
    for it in list_models(kind, enabled_only=enabled_only):
        key = str(it.get("api_key") or "")
        masked = (("*" * max(0, len(key) - 4)) + key[-4:]) if key else ""
        row = {
            "id": it["id"],
            "kind": it["kind"],
            "name": it["name"],
            "model": it["model"],
            "base_url": it["base_url"],
            "public": True,
            "enabled": bool(it.get("enabled", True)),
            "points_required": bool(it.get("points_required")),
            "billing_mode": it.get("billing_mode") or "per_use",
            "points_price": it.get("points_price") or "0.000",
            "resolution_prices": it.get("resolution_prices") or {},
            "billing_label": billing_label(it),
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
            "vision_model": it.get("vision_model") or "",
            "vision_model_id": it.get("vision_model_id") or "",
            "voice_speaker": it.get("voice_speaker") or "",
            "voice_app_id": it.get("voice_app_id") or "",
            "voice_format": it.get("voice_format") or "mp3",
        }
        if mask_key:
            row["api_key_set"] = bool(key)
            row["api_key_masked"] = masked
        else:
            row["api_key"] = key
            row["api_key_set"] = bool(key)
            row["api_key_masked"] = masked
        out.append(row)
    return out


def upsert(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in PUBLIC_KINDS:
        raise ValueError("kind must be image|video|text|agent|voice")
    data = load()
    if kind not in data:
        data[kind] = []
    norm = _normalize_entry({**body, "kind": kind}, kind)
    if not norm:
        raise ValueError("name 与 URL 必填")
    if kind == "video" and not (norm.get("resolutions") or []):
        raise ValueError("请至少勾选一个分辨率")
    items = data[kind]
    idx = next((i for i, x in enumerate(items) if x["id"] == norm["id"]), -1)
    if idx >= 0 and not norm.get("api_key") and items[idx].get("api_key"):
        norm["api_key"] = items[idx]["api_key"]
    if idx >= 0:
        items[idx] = norm
    else:
        items.append(norm)
    data[kind] = items
    save(data)
    return norm


def delete(model_id: str) -> bool:
    data = load()
    found = False
    for kind in PUBLIC_KINDS:
        before = len(data[kind])
        data[kind] = [x for x in data[kind] if x.get("id") != model_id]
        if len(data[kind]) < before:
            found = True
    if found:
        save(data)
    return found
