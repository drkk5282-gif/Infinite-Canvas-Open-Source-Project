"""Volcengine Ark Files API — upload local media when no public URL is available.

POST {ark}/api/v3/files  (multipart purpose=user_data)
GET  {ark}/api/v3/files/{id} until status=active

Docs: 火山文档理解/图片上传理解.txt · 视频上传理解.txt
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.myapi.common import extract_error, guess_mime, join_url

logger = logging.getLogger(__name__)

DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com"
# Hosted Files default storage: 512MB; Seedance ref video also caps ~200MB.
_MAX_BYTES = {
    "image": 100 * 1024 * 1024,
    "video": 200 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
}


def ark_files_base(model_cfg: dict[str, Any] | None) -> str:
    raw = str((model_cfg or {}).get("base_url") or DEFAULT_ARK_BASE).strip().rstrip("/")
    low = raw.lower()
    if "openspeech" in low or "bytedance.com" in low:
        return DEFAULT_ARK_BASE
    if low.endswith("/api/v3"):
        raw = raw[: -len("/api/v3")].rstrip("/")
    return raw or DEFAULT_ARK_BASE


def is_loopback_or_lan_url(url: str) -> bool:
    """True when Volcengine servers cannot fetch this URL from the public internet."""
    u = (url or "").strip()
    if not u:
        return True
    try:
        host = (urlparse(u).hostname or "").strip().lower()
    except Exception:
        return True
    if not host:
        return True
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host.endswith(".local") or host.endswith(".localhost"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if a == 10 or a == 127 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
            return True
    return False


def is_usable_public_media_url(url: str) -> bool:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    return not is_loopback_or_lan_url(u)


async def upload_local_file_to_ark(
    path: Path,
    *,
    model_cfg: dict[str, Any],
    kind: str = "image",
    wait: bool = True,
    timeout_sec: float = 180.0,
) -> str:
    """Upload local file → file_id (waits until active when wait=True)."""
    if not path.is_file():
        raise RuntimeError(f"本地文件不存在：{path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RuntimeError(f"无法读取本地文件：{path}") from exc
    limit = _MAX_BYTES.get(kind, 100 * 1024 * 1024)
    if size > limit:
        raise RuntimeError(
            f"本地参考{'视频' if kind == 'video' else '音频' if kind == 'audio' else '图'}"
            f"过大（{size // (1024 * 1024)}MB），超过方舟 Files 约 {limit // (1024 * 1024)}MB 限制"
        )

    api_key = str(model_cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("火山引擎未配置方舟 API Key，无法上传本地素材到 Files API")

    base = ark_files_base(model_cfg)
    upload_url = join_url(base, "api/v3/files")
    mime = guess_mime(str(path), kind)
    headers = {"Authorization": f"Bearer {api_key}"}
    form: dict[str, Any] = {"purpose": "user_data"}
    # Video understanding examples pass fps preprocess; harmless for Seedance refs.
    if kind == "video":
        form["preprocess_configs[video][fps]"] = "1"

    logger.info(
        "Volcengine Files upload start kind=%s name=%s size=%sMB → %s",
        kind,
        path.name,
        max(1, size // (1024 * 1024)),
        upload_url,
    )
    try:
        payload = path.read_bytes()
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            resp = await client.post(
                upload_url,
                headers=headers,
                data=form,
                files={"file": (path.name, payload, mime)},
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError("火山 Files 上传超时，请缩小文件后重试") from exc
    except Exception as exc:
        raise RuntimeError(f"火山 Files 上传失败：{exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"火山 Files 上传失败：{extract_error(resp)}")

    data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        data = {}
    file_id = str(data.get("id") or data.get("file_id") or "").strip()
    if not file_id:
        raise RuntimeError("火山 Files 上传成功但未返回 file id")

    status = str(data.get("status") or "").strip().lower()
    if wait and status not in ("active", "succeeded", "success"):
        file_id = await wait_file_active(file_id, model_cfg=model_cfg, timeout_sec=timeout_sec)
    logger.info("Volcengine Files upload ok kind=%s id=%s status=%s", kind, file_id, status or "pending")
    return file_id


async def wait_file_active(
    file_id: str,
    *,
    model_cfg: dict[str, Any],
    timeout_sec: float = 180.0,
    poll_interval: float = 1.2,
) -> str:
    api_key = str(model_cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("火山引擎未配置方舟 API Key")
    base = ark_files_base(model_cfg)
    url = join_url(base, f"api/v3/files/{file_id}")
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + max(10.0, timeout_sec)
    last_status = ""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        while True:
            try:
                resp = await client.get(url, headers=headers)
            except Exception as exc:
                logger.warning("Volcengine Files poll error id=%s: %s", file_id, exc)
                await asyncio.sleep(poll_interval)
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"等待火山素材就绪超时（{file_id}）") from exc
                continue
            if resp.status_code >= 400:
                err = extract_error(resp)
                if resp.status_code in (404, 409):
                    await asyncio.sleep(poll_interval)
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"等待火山素材就绪超时（{file_id}）：{err}")
                    continue
                raise RuntimeError(f"查询火山素材失败：{err}")
            data = resp.json() if resp.content else {}
            if not isinstance(data, dict):
                data = {}
            last_status = str(data.get("status") or "").strip().lower()
            if last_status in ("active", "succeeded", "success"):
                return str(data.get("id") or file_id).strip() or file_id
            if last_status in ("failed", "error", "cancelled", "canceled", "deleted"):
                detail = str(data.get("error") or data.get("message") or last_status)
                raise RuntimeError(f"火山素材处理失败：{detail}")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"等待火山素材就绪超时（{file_id}，status={last_status or 'unknown'}）"
                )
            await asyncio.sleep(poll_interval)
