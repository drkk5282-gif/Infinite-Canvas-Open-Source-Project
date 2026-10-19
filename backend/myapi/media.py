"""Reference media resolution: relay upload, public URL fallback, base64, Volcengine Files."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from backend.board_assets import resolve_upload_path
from backend.myapi.common import extract_error, guess_mime, join_url, normalize_data_url_mime

logger = logging.getLogger(__name__)

ARK_FILE_PREFIX = "arkfile:"


def local_upload_path(url: str) -> Path | None:
    u = (url or "").strip()
    if not u:
        return None
    p = Path(u)
    if p.is_file():
        return p
    return resolve_upload_path(u if u.startswith("/uploads/") else f"/uploads/{u.lstrip('/')}")


def to_data_url(path: Path, kind: str) -> str:
    raw = path.read_bytes()
    mime = guess_mime(str(path), kind, raw=raw)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def ark_file_ref(file_id: str) -> str:
    fid = (file_id or "").strip()
    return f"{ARK_FILE_PREFIX}{fid}" if fid else ""


def parse_ark_file_id(media: str) -> str:
    u = (media or "").strip()
    if u.startswith(ARK_FILE_PREFIX):
        return u[len(ARK_FILE_PREFIX) :].strip()
    return ""


def resolve_media_url_for_api(url: str, kind: str = "image") -> str:
    """Sync fallback when model_cfg is unavailable."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith(ARK_FILE_PREFIX):
        return u
    if u.startswith("data:"):
        return normalize_data_url_mime(u, kind)
    if u.startswith("http://") or u.startswith("https://"):
        return u
    from backend import service_settings

    upload_ref = u if u.startswith("/uploads/") else f"/uploads/{u.lstrip('/')}"
    public = service_settings.resolve_public_media_url(upload_ref)
    if public:
        logger.info("MyApi media ref kind=%s → public url %s", kind, public[:120])
        return public
    local = local_upload_path(u)
    if local is not None:
        return to_data_url(local, kind)
    local2 = resolve_upload_path(upload_ref)
    if local2 is not None:
        return to_data_url(local2, kind)
    return u


async def upload_file_to_relay(
    path: Path,
    *,
    base_url: str,
    api_key: str,
    kind: str = "image",
    upload_path: str = "v1/media/uploads",
    upload_field: str = "file",
    response_url_key: str = "url",
) -> str | None:
    base = (base_url or "").strip()
    if not base:
        return None
    upload_url = join_url(base, upload_path)
    headers: dict[str, str] = {}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    mime = guess_mime(str(path), kind)
    try:
        payload = path.read_bytes()
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.post(
                upload_url,
                headers=headers,
                files={upload_field: (path.name, payload, mime)},
            )
        if resp.status_code >= 400:
            logger.warning(
                "MyApi relay media upload HTTP %s %s: %s",
                resp.status_code,
                path.name,
                extract_error(resp)[:200],
            )
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        uploaded = str(data.get(response_url_key) or "").strip()
        if uploaded:
            logger.info("MyApi relay media upload ok %s → %s", path.name, uploaded[:120])
            return uploaded
    except Exception as exc:
        logger.warning("MyApi relay media upload failed %s: %s", path.name, exc)
    return None


async def _volcengine_files_fallback(
    local: Path,
    *,
    kind: str,
    model_cfg: dict[str, Any],
) -> str:
    from backend.myapi.styles.volcengine import files as ve_files

    file_id = await ve_files.upload_local_file_to_ark(local, model_cfg=model_cfg, kind=kind)
    return ark_file_ref(file_id)


def _volc_public_ok(url: str) -> bool:
    from backend.myapi.styles.volcengine import files as ve_files

    return ve_files.is_usable_public_media_url(url)


async def _volc_resolve_local(
    local: Path,
    *,
    kind: str,
    model_cfg: dict[str, Any],
) -> str:
    """Volcengine local refs: prefer Ark Files API for image/video/audio.

    例外：openspeech「语音生成」节点（Seed-Audio）用 audio_data/image_data，
    不走 Chat Completions 的 input_audio.file_id；故 voice kind 仍用 Base64。
    文本多模态的音频理解请用文本节点 + Files（见官方 curl）。
    """
    try:
        size = local.stat().st_size
    except OSError:
        size = 0

    gen_kind = str(model_cfg.get("kind") or "").strip().lower()
    if gen_kind == "voice":
        if kind == "video":
            raise RuntimeError("语音生成不支持参考视频")
        if kind == "audio" and size > 6 * 1024 * 1024:
            raise RuntimeError(
                f"本地参考音频过大（{size // (1024 * 1024)}MB）。"
                "openspeech 不支持方舟 Files ID，请压缩到约 6MB 内或使用公网 HTTPS 音频。"
            )
        if kind == "image" and size > 10 * 1024 * 1024:
            raise RuntimeError(
                f"本地参考图过大（{size // (1024 * 1024)}MB）。"
                "openspeech 不支持方舟 Files ID，请压缩到约 10MB 内或使用公网 HTTPS 图片。"
            )
        return to_data_url(local, kind)

    # 图 / 视 / 文 / Seedance 音频参考：统一优先 Files API（官方推荐本地上传路径）。
    return await _volcengine_files_fallback(local, kind=kind, model_cfg=model_cfg)


async def resolve_media_url_for_api_async(
    url: str,
    kind: str = "image",
    model_cfg: dict[str, Any] | None = None,
    *,
    upload_path: str = "v1/media/uploads",
    upload_field: str = "file",
    response_url_key: str = "url",
) -> str:
    from backend import service_settings

    u = (url or "").strip()
    if not u:
        return ""
    style = str((model_cfg or {}).get("api_style") or "").strip().lower()
    # 方舟没有 /v1/media/uploads：本地 /uploads 转 data URL、公网 URL，或 Files API 素材 ID。
    skip_relay = style in ("volcengine",)
    is_volc = skip_relay or bool((model_cfg or {}).get("volcengine"))

    if u.startswith(ARK_FILE_PREFIX):
        return u

    if u.startswith("data:"):
        if is_volc and kind == "video":
            raise RuntimeError(
                "参考视频不能使用 Base64。请使用画布本地文件（将自动走方舟 Files API），"
                "或提供公网 HTTPS 视频地址。"
            )
        return normalize_data_url_mime(u, kind)

    # Absolute http(s) that still point at /uploads/...
    upload_from_abs = service_settings.upload_path_from_url(u)
    if upload_from_abs and (u.startswith("http://") or u.startswith("https://")):
        local = local_upload_path(upload_from_abs) or resolve_upload_path(upload_from_abs)
        # 火山：本地 /uploads 优先 Files，不依赖 public_base_url。
        if is_volc and local is not None and model_cfg:
            return await _volc_resolve_local(local, kind=kind, model_cfg=model_cfg)
        public = service_settings.resolve_public_media_url(upload_from_abs)
        if public:
            if local is not None and model_cfg and not skip_relay:
                uploaded = await upload_file_to_relay(
                    local,
                    base_url=str(model_cfg.get("base_url") or ""),
                    api_key=str(model_cfg.get("api_key") or ""),
                    kind=kind,
                    upload_path=upload_path,
                    upload_field=upload_field,
                    response_url_key=response_url_key,
                )
                if uploaded:
                    return uploaded
            logger.info("MyApi media ref kind=%s → public url (rewrite) %s", kind, public[:120])
            return public
        u = upload_from_abs

    if u.startswith("http://") or u.startswith("https://"):
        if is_volc and not _volc_public_ok(u):
            local_guess = local_upload_path(u) or (
                local_upload_path(upload_from_abs) if upload_from_abs else None
            )
            if local_guess is not None and model_cfg:
                return await _volc_resolve_local(local_guess, kind=kind, model_cfg=model_cfg)
            raise RuntimeError(
                "参考素材地址是内网/本机地址，火山方舟无法访问。"
                "请使用画布本地素材（自动 Files 上传），或改用公网 HTTPS 地址。"
            )
        # 已是外网直链：直接用 URL，不必再上传 Files。
        return u

    upload_ref = u if u.startswith("/uploads/") else f"/uploads/{u.lstrip('/')}"
    local = local_upload_path(u)
    if local is None:
        local = resolve_upload_path(upload_ref)

    # 火山本地文件：优先 Files（跳过 public_base_url / Base64）。
    if local is not None and model_cfg and is_volc:
        return await _volc_resolve_local(local, kind=kind, model_cfg=model_cfg)

    if local is not None and model_cfg and not skip_relay:
        uploaded = await upload_file_to_relay(
            local,
            base_url=str(model_cfg.get("base_url") or ""),
            api_key=str(model_cfg.get("api_key") or ""),
            kind=kind,
            upload_path=upload_path,
            upload_field=upload_field,
            response_url_key=response_url_key,
        )
        if uploaded:
            return uploaded

    public = service_settings.resolve_public_media_url(upload_ref)
    if public:
        logger.info("MyApi media ref kind=%s → public url (fallback) %s", kind, public[:120])
        return public

    if local is not None:
        return to_data_url(local, kind)
    return u


async def resolve_references_async(
    refs: list[dict[str, Any]],
    model_cfg: dict[str, Any],
    *,
    upload_path: str = "v1/media/uploads",
    upload_field: str = "file",
    response_url_key: str = "url",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "image").lower()
        ref_url = str(item.get("url") or item.get("src") or "").strip()
        if not ref_url:
            logger.warning("MyApi ref skipped kind=%s: empty url", kind)
            continue
        media = await resolve_media_url_for_api_async(
            ref_url,
            kind,
            model_cfg,
            upload_path=upload_path,
            upload_field=upload_field,
            response_url_key=response_url_key,
        )
        if not media:
            logger.warning("MyApi ref skipped kind=%s: unresolved %s", kind, ref_url[:96])
            continue
        file_id = parse_ark_file_id(media)
        if file_id:
            logger.info("MyApi ref ok kind=%s src=arkfile id=%s", kind, file_id)
            out.append(
                {
                    **item,
                    "kind": kind,
                    "url": ref_url,
                    "_media": media,
                    "_file_id": file_id,
                }
            )
            continue
        if media.startswith("data:"):
            mime = media.split(";", 1)[0][5:]
            logger.info("MyApi ref ok kind=%s src=data mime=%s bytes~=%d", kind, mime, len(media))
        else:
            logger.info("MyApi ref ok kind=%s src=url %s", kind, media[:96])
        out.append({**item, "kind": kind, "url": ref_url, "_media": media})
    return out
