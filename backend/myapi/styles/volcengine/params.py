"""Volcengine Ark request bodies: Seedream image / Seedance video / openspeech audio."""

from __future__ import annotations

from typing import Any

from backend.myapi.common import (
    normalize_image_quality,
    normalize_ratio,
    ratio_is_auto,
    resolution_is_free,
    resolve_video_resolution,
)
from backend.myapi.media import parse_ark_file_id
from backend.myapi.styles.base import ImageBuildContext, VideoBuildContext


def _ref_media(ref: dict[str, Any]) -> str:
    return str(ref.get("_media") or ref.get("url") or "").strip()


def _ref_file_id(ref: dict[str, Any]) -> str:
    fid = str(ref.get("_file_id") or "").strip()
    if fid:
        return fid
    return parse_ark_file_id(_ref_media(ref))


def _split_refs(
    resolved_refs: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (media, file_id) pairs per kind. media may be empty when file_id is set."""
    images: list[tuple[str, str]] = []
    videos: list[tuple[str, str]] = []
    audios: list[tuple[str, str]] = []
    for ref in resolved_refs or []:
        if not isinstance(ref, dict):
            continue
        file_id = _ref_file_id(ref)
        media = _ref_media(ref)
        if media.startswith("arkfile:"):
            media = ""
        if not media and not file_id:
            continue
        kind = str(ref.get("kind") or "image").strip().lower()
        pair = (media, file_id)
        if kind == "video":
            videos.append(pair)
        elif kind == "audio":
            audios.append(pair)
        else:
            images.append(pair)
    return images, videos, audios


def _url_or_file_obj(media: str, file_id: str) -> dict[str, str]:
    if file_id:
        return {"file_id": file_id}
    return {"url": media}


def _seedream_image_value(media: str, file_id: str) -> str:
    """Seedream `image` field: URL / Base64 / 素材 ID string."""
    if file_id:
        return file_id
    return media


def seedream_size(resolution: str | None, ratio: str | None) -> str | None:
    """Map canvas clarity to Seedream size (1K|1.5K|2K|3K|4K)."""
    raw = str(resolution or "").strip().upper().replace("Ｋ", "K")
    if raw in ("1K", "1.5K", "2K", "3K", "4K"):
        return raw
    q = normalize_image_quality(resolution)
    if q == "1k":
        return "1K"
    if q == "1.5k":
        return "1.5K"
    if q == "3k":
        return "3K"
    if q == "4k":
        return "4K"
    if q == "2k":
        return "2K"
    r = normalize_ratio(ratio)
    if r and not ratio_is_auto(r):
        return None
    return "2K" if q else None


def build_image_body(ctx: ImageBuildContext) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": ctx.model_name,
        "prompt": ctx.prompt,
        "response_format": "url",
        "watermark": bool(ctx.model_cfg.get("watermark", False)),
    }
    size = ctx.size or seedream_size(ctx.resolution, ctx.ratio)
    if size:
        body["size"] = size
    images, _, _ = _split_refs(ctx.resolved_refs)
    values = [_seedream_image_value(m, fid) for m, fid in images if m or fid]
    if len(values) == 1:
        body["image"] = values[0]
    elif len(values) > 1:
        body["image"] = values
    return body


def _volc_seedance_content(ctx: VideoBuildContext) -> list[dict[str, Any]]:
    """Seedance 2.x content[]：全能参考用 reference_*；支持 URL / Base64 / file_id。"""
    content: list[dict[str, Any]] = []
    text = (ctx.text or "").strip()
    if text:
        content.append({"type": "text", "text": text})
    images, videos, audios = _split_refs(ctx.resolved_refs)
    frame_mode = str(ctx.model_cfg.get("video_frame_mode") or "").strip().lower()

    def _image_block(media: str, file_id: str, role: str) -> dict[str, Any]:
        return {
            "type": "image_url",
            "image_url": _url_or_file_obj(media, file_id),
            "role": role,
        }

    if frame_mode in ("first_last", "fl", "first-last") and len(images) >= 2:
        content.append(_image_block(images[0][0], images[0][1], "first_frame"))
        content.append(_image_block(images[1][0], images[1][1], "last_frame"))
        for media, file_id in images[2:]:
            content.append(_image_block(media, file_id, "reference_image"))
    else:
        for media, file_id in images:
            role = (
                "first_frame"
                if len(images) == 1 and frame_mode in ("first", "i2v")
                else "reference_image"
            )
            content.append(_image_block(media, file_id, role))

    for media, file_id in videos:
        if not file_id and str(media).startswith("data:"):
            raise RuntimeError(
                "参考视频不能使用 Base64（Seedance 仅支持公网 URL 或素材 ID）。"
                "本地视频应自动走方舟 Files API；若仍失败请检查 API Key。"
            )
        if not file_id and not media:
            continue
        content.append(
            {
                "type": "video_url",
                "video_url": _url_or_file_obj(media, file_id),
                "role": "reference_video",
            }
        )
    for media, file_id in audios:
        if not file_id and not media:
            continue
        content.append(
            {
                "type": "audio_url",
                "audio_url": _url_or_file_obj(media, file_id),
                "role": "reference_audio",
            }
        )
    return content


def _seedance_body(ctx: VideoBuildContext) -> dict[str, Any]:
    body: dict[str, Any] = {"model": ctx.model_name}
    if ctx.model_cfg.get("send_duration", True):
        body["duration"] = ctx.dur
    ratio = normalize_ratio(ctx.ratio)
    if ratio and not ratio_is_auto(ratio):
        body["aspect_ratio"] = ratio
        body["ratio"] = ratio
    return body


def build_video_body(ctx: VideoBuildContext) -> dict[str, Any]:
    """Native Ark Seedance: POST /api/v3/contents/generations/tasks."""
    body = _seedance_body(ctx)
    body["content"] = _volc_seedance_content(ctx)
    if body.get("aspect_ratio") and not body.get("ratio"):
        body["ratio"] = body["aspect_ratio"]
    if not body.get("ratio"):
        r = normalize_ratio(ctx.ratio)
        body["ratio"] = "adaptive" if (not r or ratio_is_auto(r)) else r
    if ctx.model_cfg.get("send_size", True) and not resolution_is_free(ctx.resolution):
        resolved = resolve_video_resolution(ctx.resolution, ctx.model_cfg)
        label = str(resolved or ctx.resolution or "").strip().lower()
        if label.endswith("p"):
            body["resolution"] = label
        elif label in ("480p", "720p", "1080p", "4k"):
            body["resolution"] = label
    if "generate_audio" not in body and "generate_audio" in ctx.model_cfg:
        body["generate_audio"] = bool(ctx.model_cfg.get("generate_audio"))
    elif "generate_audio" not in body:
        body["generate_audio"] = True
    if "watermark" not in body:
        body["watermark"] = bool(ctx.model_cfg.get("watermark", False))
    return body


def _data_url_b64(media: str) -> str:
    """Strip data:*;base64, prefix → raw base64 for openspeech audio_data / image_data."""
    u = (media or "").strip()
    if not u.startswith("data:") or "," not in u:
        return ""
    return u.partition(",")[2].strip()


def _voice_audio_ref(media: str, file_id: str = "") -> dict[str, str]:
    # openspeech: audio_data / audio_url（无 file_id）；Files 素材转不成 openspeech 引用时退回 URL 字段尝试。
    b64 = _data_url_b64(media)
    if b64:
        return {"audio_data": b64}
    if file_id:
        # 方舟 file_id 不能直接给 openspeech；提示应走 Base64（小文件）或公网 URL。
        raise RuntimeError(
            "语音参考音频过大且无法使用方舟 Files ID（openspeech 不认 file_id）。"
            "请压缩到约 6MB 以内，或配置公网 HTTPS 音频地址。"
        )
    return {"audio_url": media}


def _voice_image_ref(media: str, file_id: str = "") -> dict[str, str]:
    b64 = _data_url_b64(media)
    if b64:
        return {"image_data": b64}
    if file_id:
        raise RuntimeError(
            "语音参考图过大且无法使用方舟 Files ID（openspeech 不认 file_id）。"
            "请压缩到约 10MB 以内，或配置公网 HTTPS 图片地址。"
        )
    return {"image_url": media}


def build_voice_body(
    *,
    model_name: str,
    prompt: str,
    model_cfg: dict[str, Any],
    resolved_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Seed-Audio 1.0: POST openspeech /api/v3/tts/create (sync; not Ark content_generation)."""
    fmt = str(model_cfg.get("voice_format") or model_cfg.get("audio_format") or "mp3").strip().lower()
    if fmt not in ("mp3", "wav", "pcm", "ogg_opus"):
        fmt = "mp3"
    try:
        sample_rate = int(model_cfg.get("voice_sample_rate") or 24000)
    except (TypeError, ValueError):
        sample_rate = 24000
    if sample_rate not in (8000, 16000, 24000, 32000, 44100, 48000):
        sample_rate = 24000
    audio_config: dict[str, Any] = {
        "format": fmt,
        "sample_rate": sample_rate,
    }
    for key, cfg_key, lo, hi in (
        ("speech_rate", "voice_speech_rate", -50, 100),
        ("loudness_rate", "voice_loudness_rate", -50, 100),
        ("pitch_rate", "voice_pitch_rate", -12, 12),
    ):
        raw = model_cfg.get(cfg_key)
        if raw is None:
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        audio_config[key] = max(lo, min(hi, val))
    if model_cfg.get("voice_enable_subtitle"):
        audio_config["enable_subtitle"] = True

    body: dict[str, Any] = {
        "model": model_name or "seed-audio-1.0",
        "text_prompt": (prompt or "").strip()[:2048],
        "audio_config": audio_config,
    }
    speaker = str(model_cfg.get("voice_speaker") or model_cfg.get("speaker") or "").strip()
    images, _, audios = _split_refs(resolved_refs or [])
    refs: list[dict[str, Any]] = []
    if audios:
        for media, file_id in audios[:3]:
            refs.append(_voice_audio_ref(media, file_id))
    elif images:
        media, file_id = images[0]
        refs.append(_voice_image_ref(media, file_id))
    elif speaker:
        refs.append({"speaker": speaker})
    if refs:
        body["references"] = refs
    return body
