"""Volcengine / openspeech response helpers."""

from __future__ import annotations

from typing import Any


def extract_task_id(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("id", "task_id", "taskId"):
        v = data.get(key)
        if v and str(v).strip():
            return str(v).strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("id", "task_id", "taskId"):
            v = nested.get(key)
            if v and str(v).strip():
                return str(v).strip()
    return ""


def extract_status(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("status", "state"):
        v = data.get(key)
        if v:
            return str(v).strip().lower()
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("status", "state"):
            v = nested.get(key)
            if v:
                return str(v).strip().lower()
    return ""


def extract_task_error(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("msg") or err.get("code") or "").strip()
        if msg:
            return msg
    if isinstance(err, str) and err.strip():
        return err.strip()
    for key in ("message", "msg"):
        v = data.get(key)
        if isinstance(v, str) and v.strip() and str(data.get("code") or "") not in ("0", "200", ""):
            # openspeech uses code=0 for success with message
            if key == "message" and int(data.get("code") or 0) == 0:
                continue
            return v.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return extract_task_error(nested)
    return ""


def extract_video_url(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if isinstance(content, dict):
        for key in ("video_url", "url"):
            v = content.get(key)
            if isinstance(v, str) and v.strip().startswith(("http://", "https://")):
                return v.strip()
    for key in ("video_url", "url"):
        v = data.get(key)
        if isinstance(v, str) and v.strip().startswith(("http://", "https://")):
            return v.strip()
    nested = data.get("data")
    if isinstance(nested, dict):
        return extract_video_url(nested)
    return ""


def extract_image_url(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        return ""
    items = data.get("data")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("url", "image_url"):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            b64 = item.get("b64_json")
            if isinstance(b64, str) and b64.strip():
                raw = b64.strip()
                return raw if raw.startswith("data:") else f"data:image/png;base64,{raw}"
    for key in ("url", "image_url"):
        v = data.get(key)
        if isinstance(v, str) and v.strip().startswith(("http://", "https://", "data:")):
            return v.strip()
    return ""


def extract_audio_result(data: dict[str, Any]) -> tuple[str, float]:
    """Return (url_or_data_url, duration_seconds) from openspeech Seed-Audio sync response."""
    if not isinstance(data, dict):
        return "", 0.0
    code = data.get("code")
    if code is not None:
        try:
            if int(code) not in (0, 200):
                return "", 0.0
        except (TypeError, ValueError):
            pass
    try:
        duration = float(data.get("duration") or data.get("original_duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    for key in ("url", "audio_url"):
        url = str(data.get(key) or "").strip()
        if url.startswith(("http://", "https://", "data:")):
            return url, duration

    audio_b64 = str(data.get("audio") or data.get("audio_data") or "").strip()
    if audio_b64:
        if audio_b64.startswith("data:"):
            return audio_b64, duration
        # Default container for Seed-Audio when format unknown in response.
        return f"data:audio/mpeg;base64,{audio_b64}", duration

    content = data.get("content")
    if isinstance(content, dict):
        got, dur = extract_audio_result(content)
        if got:
            return got, dur or duration

    nested = data.get("data")
    if isinstance(nested, dict):
        return extract_audio_result(nested)
    return "", duration
