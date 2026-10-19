"""Shared HTTP, MIME, sizing, and asset helpers for MyApi relays."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from backend.board_assets import alloc_asset_path

logger = logging.getLogger(__name__)

RATIO_SIZE = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "4:3": "1536x1152",
    "3:4": "1152x1536",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
}

# gpt-image / aiooapi-style quality tiers (1K / 2K / 4K).
# Chart: STANDARD 1K, DETAIL 2K, ULTRA 4K — landscape/portrait/square buckets.
IMAGE_QUALITY_SIZE = {
    "1k": {
        "1:1": "1024x1024",
        "16:9": "1536x1024",
        "3:2": "1536x1024",
        "4:3": "1536x1024",
        "9:16": "1024x1536",
        "2:3": "1024x1536",
        "3:4": "1024x1536",
        "_square": "1024x1024",
        "_landscape": "1536x1024",
        "_portrait": "1024x1536",
    },
    "2k": {
        "1:1": "2048x2048",
        "16:9": "2048x1152",
        "3:2": "2048x1152",
        "4:3": "2048x1152",
        "9:16": "1152x2048",
        "2:3": "1152x2048",
        "3:4": "1152x2048",
        "_square": "2048x2048",
        "_landscape": "2048x1152",
        "_portrait": "1152x2048",
    },
    "4k": {
        # Chart has no 4K square; use short-side square. Landscape/portrait match ULTRA.
        "1:1": "2160x2160",
        "16:9": "3840x2160",
        "3:2": "3840x2160",
        "4:3": "3840x2160",
        "9:16": "2160x3840",
        "2:3": "2160x3840",
        "3:4": "2160x3840",
        "_square": "2160x2160",
        "_landscape": "3840x2160",
        "_portrait": "2160x3840",
    },
}

IMAGE_QUALITY_LEVEL = {
    "1k": "medium",
    "2k": "high",
    "4k": "high",
}

VIDEO_RATIO_SIZE = {
    "1:1": "720x720",
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "960x720",
    "3:4": "720x960",
    "3:2": "1080x720",
    "2:3": "720x1080",
    "21:9": "1680x720",
}

VIDEO_RESOLUTION_SIZE = {
    "480p": "854x480",
    "720p": "1280x720",
    "1080p": "1920x1080",
    "2k": "2560x1440",
    "4k": "3840x2160",
}

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}

AUDIO_MIME_CANONICAL = {
    "audio/wave": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/mp3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg",
}

TASK_PENDING = {"pending", "processing", "running", "queued", "in_progress", "submitted", "created"}
TASK_DONE = {"succeeded", "completed", "success", "done", "finished"}
TASK_FAIL = {"failed", "error", "cancelled", "canceled", "timeout"}


def sniff_audio_mime(raw: bytes) -> str | None:
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "audio/wav"
    if len(raw) >= 3 and raw[:3] == b"ID3":
        return "audio/mpeg"
    if len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None


def canonical_audio_mime(mime: str) -> str:
    m = (mime or "").split(";", 1)[0].strip().lower()
    return AUDIO_MIME_CANONICAL.get(m, m)


def guess_mime(path_or_url: str, kind: str = "image", raw: bytes | None = None) -> str:
    ext = Path(str(path_or_url).split("?", 1)[0]).suffix.lower()
    if ext in (".wav", ".wave"):
        return "audio/wav"
    if ext in (".mp3", ".mpeg", ".mpga"):
        return "audio/mpeg"
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    if kind == "audio" and raw:
        sniffed = sniff_audio_mime(raw)
        if sniffed:
            return sniffed
    if kind == "video":
        return "video/mp4"
    if kind == "audio":
        return "audio/mpeg"
    return "image/png"


def normalize_data_url_mime(data_url: str, kind: str = "image") -> str:
    u = (data_url or "").strip()
    if not u.startswith("data:") or ";base64," not in u:
        return u
    head, payload = u.split(";base64,", 1)
    mime = head[5:]
    if kind != "audio" and not mime.lower().startswith("audio/"):
        return u
    canon = canonical_audio_mime(mime)
    if not canon.startswith("audio/"):
        return u
    if canon in ("audio/wav", "audio/mpeg"):
        fixed = canon
    elif kind == "audio":
        try:
            raw = base64.b64decode(payload[:256] + "==", validate=False)
            fixed = sniff_audio_mime(raw) or canon
        except Exception:
            fixed = canon
    else:
        fixed = canon
    if fixed == mime:
        return u
    return f"data:{fixed};base64,{payload}"


def join_url(base: str, path: str) -> str:
    b = (base or "").rstrip("/") + "/"
    p = (path or "").lstrip("/")
    if p.startswith("v1/") and b.rstrip("/").endswith("/v1"):
        p = p[3:]
    return urljoin(b, p)


def auth_headers(api_key: str) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def ratio_to_size(ratio: str | None) -> str | None:
    r = str(ratio or "").strip().replace("：", ":")
    if not r or r in ("自动", "auto", "Auto"):
        return None
    return RATIO_SIZE.get(r) or None


def normalize_image_quality(resolution: str | None) -> str:
    """Return 1k|1.5k|2k|3k|4k or empty when unset / 默认."""
    raw = str(resolution or "").strip().lower().replace("ｋ", "k")
    if not raw or raw in ("自动", "auto", "默认", "default"):
        return ""
    if raw in ("1k", "1.5k", "2k", "3k", "4k"):
        return raw
    if raw in ("1.5", "1536"):
        return "1.5k"
    if raw in ("3", "3072"):
        return "3k"
    if raw in ("1", "1024", "standard"):
        return "1k"
    if raw in ("2", "2048", "detail"):
        return "2k"
    if raw in ("4", "3840", "ultra"):
        return "4k"
    return ""


def _ratio_orient_bucket(ratio: str | None) -> str:
    r = normalize_ratio(ratio)
    if not r or ratio_is_auto(r):
        return "_square"
    if r == "1:1":
        return "_square"
    parts = r.split(":")
    try:
        w, h = float(parts[0]), float(parts[1])
    except (TypeError, ValueError, IndexError):
        return "_square"
    if abs(w - h) < 1e-6:
        return "_square"
    return "_landscape" if w > h else "_portrait"


def image_quality_to_size(resolution: str | None, ratio: str | None = None) -> str | None:
    """Map MyAPI image clarity (1K/2K/4K) × aspect ratio → WxH. Empty resolution → None."""
    tier = normalize_image_quality(resolution)
    if not tier:
        return None
    table = IMAGE_QUALITY_SIZE.get(tier) or {}
    r = normalize_ratio(ratio)
    if r and not ratio_is_auto(r) and r in table:
        return table[r]
    return table.get(_ratio_orient_bucket(r)) or table.get("_square")


def image_quality_level(resolution: str | None) -> str | None:
    tier = normalize_image_quality(resolution)
    return IMAGE_QUALITY_LEVEL.get(tier) if tier else None


def video_ratio_to_size(ratio: str | None) -> str | None:
    r = str(ratio or "").strip().replace("：", ":")
    if not r or r in ("自动", "auto", "Auto"):
        return None
    return VIDEO_RATIO_SIZE.get(r) or ratio_to_size(r)


def video_resolution_to_size(resolution: str | None, ratio: str | None = None) -> str | None:
    raw = str(resolution or "").strip().lower()
    # 「自由」= omit size/resolution entirely (upstream default).
    if raw in ("自由", "free"):
        return None
    if not raw or raw in ("自动", "auto"):
        return video_ratio_to_size(ratio)
    if raw in VIDEO_RESOLUTION_SIZE:
        return VIDEO_RESOLUTION_SIZE[raw]
    if "x" in raw and all(p.isdigit() for p in raw.split("x", 1)):
        return raw
    return None


def normalize_ratio(ratio: str | None) -> str:
    return str(ratio or "").strip().replace("：", ":")


def ratio_is_auto(r: str) -> bool:
    return not r or r in ("自动", "auto", "Auto")


def resolution_is_free(resolution: str | None) -> bool:
    """MyAPI「自由」: do not send resolution/size; let upstream choose."""
    raw = str(resolution or "").strip().lower()
    return raw in ("自由", "free")


def resolution_is_auto(resolution: str | None) -> bool:
    raw = str(resolution or "").strip().lower()
    return not raw or raw in ("自动", "auto")


def resolve_video_resolution(resolution: str | None, model_cfg: dict[str, Any]) -> str:
    """Concrete resolution to send, or empty string when free / omit."""
    raw = str(resolution or "").strip()
    if resolution_is_free(raw):
        return ""
    if raw and not resolution_is_auto(raw):
        return raw
    for item in model_cfg.get("resolutions") or []:
        candidate = str(item or "").strip()
        if not candidate or resolution_is_auto(candidate) or resolution_is_free(candidate):
            continue
        return candidate
    # Model only offers 自由 (or empty) → omit.
    configured = [str(x or "").strip() for x in (model_cfg.get("resolutions") or [])]
    if configured and all(resolution_is_free(x) or resolution_is_auto(x) or not x for x in configured):
        return ""
    return "480p"


def split_media_arrays(
    resolved_refs: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    images: list[str] = []
    videos: list[str] = []
    audios: list[str] = []
    for ref in resolved_refs:
        kind = (ref.get("kind") or "image").lower()
        media = str(ref.get("_media") or "")
        if not media:
            continue
        if kind == "video":
            videos.append(media)
        elif kind == "audio":
            audios.append(media)
        else:
            images.append(media)
    return images, videos, audios


def model_ref_max(model_cfg: dict[str, Any], key: str, default: int) -> int:
    if key not in model_cfg or model_cfg.get(key) is None:
        return default
    try:
        return int(model_cfg[key])
    except (TypeError, ValueError):
        return default


def limit_references(refs: list[dict[str, Any]], model_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    max_images = model_ref_max(model_cfg, "max_images", 9)
    max_videos = model_ref_max(model_cfg, "max_videos", 3)
    max_audios = model_ref_max(model_cfg, "max_audios", 3)
    out: list[dict[str, Any]] = []
    n_img = n_vid = n_aud = 0
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "image").lower()
        if kind == "video":
            if n_vid >= max_videos:
                continue
            n_vid += 1
        elif kind == "audio":
            if n_aud >= max_audios:
                continue
            n_aud += 1
        else:
            if n_img >= max_images:
                continue
            n_img += 1
        out.append(item)
    return out


def bake_prompt_extras(
    prompt: str,
    *,
    ratio: str | None = None,
    duration: int | None = None,
    style: str | None = None,
) -> str:
    from backend.prompt_refs import sanitize_generation_prompt

    parts = [sanitize_generation_prompt(prompt)]
    extras: list[str] = []
    r = str(ratio or "").strip()
    if r and r not in ("自动", "auto", "Auto"):
        extras.append(f"画面比例 {r}")
    if duration and int(duration) > 0:
        extras.append(f"视频时长 {int(duration)} 秒")
    if style and str(style).strip():
        extras.append(f"风格 {str(style).strip()}")
    if extras:
        parts.append("（" + "，".join(extras) + "）")
    return "\n".join(p for p in parts if p).strip()


def extract_error(resp: httpx.Response) -> str:
    try:
        j = resp.json()
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(err, str):
                return err
            for key in ("errorMessage", "error_message", "message"):
                v = j.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return (resp.text or "")[:400] or f"HTTP {resp.status_code}"


class MyApiUpstreamError(RuntimeError):
    """MyApi HTTP / poll failure with upstream JSON body for audit logs."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: Any = None,
        http_status: int | None = None,
        upstream_terminal: bool = False,
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.http_status = http_status
        self.upstream_terminal = upstream_terminal


def parse_response_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        text = (resp.text or "").strip()
        if text:
            return {"http_status": resp.status_code, "body": text[:8192]}
        return {"http_status": resp.status_code, "body": ""}


def raise_http_error(prefix: str, resp: httpx.Response) -> None:
    body = parse_response_body(resp)
    msg = extract_error(resp)
    detail = f"{prefix}{msg}" if msg else prefix.rstrip("：")
    raise MyApiUpstreamError(detail, raw_response=body, http_status=resp.status_code)


def raise_payload_error(prefix: str, data: dict[str, Any], err_msg: str | None = None) -> None:
    msg = (err_msg or "").strip() or "任务失败"
    detail = f"{prefix}{msg}" if prefix.endswith("：") else f"{prefix}：{msg}"
    raise MyApiUpstreamError(detail, raw_response=data)


def save_b64_image(b64: str, *, prefix: str = "myapi") -> dict[str, Any]:
    raw = b64
    if "," in raw and raw.strip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    data = base64.b64decode(raw)
    path, url = alloc_asset_path(".png")
    path.write_bytes(data)
    return {"url": url, "width": 0, "height": 0, "path": str(path)}


async def download_to_uploads(
    url: str,
    *,
    prefix: str = "myapi",
    headers: dict[str, str] | None = None,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    if (url or "").startswith("data:"):
        head, _, b64 = url.partition(",")
        mime = "application/octet-stream"
        if head.startswith("data:") and ";" in head:
            mime = head[5:].split(";", 1)[0] or mime
        data = base64.b64decode(b64)
        _validate_download_payload(
            data,
            content_type=mime,
            url=url[:120],
            expected_kind=expected_kind,
        )
        ext = ".bin"
        if "mp4" in mime:
            ext = ".mp4"
        elif "webm" in mime:
            ext = ".webm"
        elif "png" in mime:
            ext = ".png"
        elif "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        path, public = alloc_asset_path(ext)
        path.write_bytes(data)
        return {"url": public, "width": 0, "height": 0, "path": str(path), "bytes": len(data)}

    req_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "content-type"}
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=req_headers or None)
        if resp.status_code >= 400:
            raise_http_error("下载生成结果失败：", resp)
        data = resp.content
    ctype = (resp.headers.get("content-type") or "").lower()
    _validate_download_payload(
        data,
        content_type=ctype,
        url=str(resp.url),
        expected_kind=expected_kind,
        http_status=resp.status_code,
    )
    ext = ".png"
    if "jpeg" in ctype or "jpg" in ctype:
        ext = ".jpg"
    elif "webp" in ctype:
        ext = ".webp"
    elif "png" in ctype:
        ext = ".png"
    elif "mp4" in ctype:
        ext = ".mp4"
    elif "webm" in ctype:
        ext = ".webm"
    elif "wav" in ctype or url.lower().split("?")[0].endswith(".wav"):
        ext = ".wav"
    elif "mpeg" in ctype or url.lower().split("?")[0].endswith(".mp3"):
        ext = ".mp3"
    elif "m4a" in ctype or url.lower().split("?")[0].endswith(".m4a"):
        ext = ".m4a"
    elif ctype.startswith("audio/"):
        ext = ".mp3"
    elif ctype.startswith("video/"):
        from backend.media.video_tools import guess_video_extension

        ext = guess_video_extension(url=url, content_type=ctype, head=data)
    else:
        from backend.media.video_tools import UPLOAD_VIDEO_EXTENSIONS, guess_video_extension

        url_l = url.lower().split("?")[0]
        if any(url_l.endswith(suffix) for suffix in UPLOAD_VIDEO_EXTENSIONS):
            ext = guess_video_extension(url=url, content_type=ctype, head=data)
    path, public = alloc_asset_path(ext)
    path.write_bytes(data)
    out = {"url": public, "width": 0, "height": 0, "path": str(path), "bytes": len(data)}
    from backend.media.video_tools import UPLOAD_VIDEO_EXTENSIONS, finalize_saved_video

    if ext in UPLOAD_VIDEO_EXTENSIONS or ctype.startswith("video/"):
        saved = await finalize_saved_video(path, public)
        out["url"] = saved["video_url"]
        out["width"] = saved.get("width") or 0
        out["height"] = saved.get("height") or 0
    return out


def _validate_download_payload(
    data: bytes,
    *,
    content_type: str,
    url: str,
    expected_kind: str | None,
    http_status: int | None = None,
) -> None:
    """Reject HTTP 200 error pages and truncated media before recording success."""
    kind = str(expected_kind or "").strip().lower()
    if not kind:
        return
    ctype = str(content_type or "").split(";", 1)[0].strip().lower()
    preview = data[:2048].decode("utf-8", errors="replace").strip()
    diagnostic = {
        "http_status": http_status,
        "content_type": ctype,
        "bytes": len(data),
        "url": url[:1000],
        "body_preview": preview[:1200] if ctype.startswith(("text/", "application/json")) else "",
    }
    if ctype.startswith("text/") or ctype in {"application/json", "application/problem+json"}:
        raise MyApiUpstreamError(
            f"生成结果下载返回了{ctype or '文本'}，不是{kind}文件",
            raw_response=diagnostic,
            http_status=http_status,
        )
    if kind == "video":
        if len(data) < 10_000:
            raise MyApiUpstreamError(
                f"生成视频下载内容过小（{len(data)} 字节）",
                raw_response=diagnostic,
                http_status=http_status,
            )
        if ctype.startswith("image/"):
            raise MyApiUpstreamError(
                "生成视频下载地址实际返回了图片",
                raw_response=diagnostic,
                http_status=http_status,
            )
    elif kind == "image":
        if len(data) < 128:
            raise MyApiUpstreamError(
                f"生成图片下载内容过小（{len(data)} 字节）",
                raw_response=diagnostic,
                http_status=http_status,
            )
        try:
            import io
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise MyApiUpstreamError(
                "生成图片下载内容无法识别为有效图片",
                raw_response=diagnostic,
                http_status=http_status,
            ) from exc


def is_transient_video_error(status: int, err: str) -> bool:
    if status in (429, 502, 503, 504):
        return True
    text = (err or "").lower()
    return any(
        k in text
        for k in (
            "繁忙",
            "稍后重试",
            "try again",
            "rate limit",
            "too many",
            "overloaded",
            "temporarily",
        )
    )
