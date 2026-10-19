"""Client for https://video.zacao.top media-parser (Doubao no-watermark).

Docs: https://video.zacao.top/docs#auth
API: POST /api/parse  body {"text": "<share url>"}
Auth: Authorization: Bearer <zacao_api_key>
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

_IMG_ID_RE = re.compile(r"/rc_gen_image/([a-f0-9]+)\.", re.I)
_VIDEO_HINT_RE = re.compile(
    r"(?:\.mp4(?:\?|$)|mime_type=video_mp4|/video/|video_id=|tos-cn-v-|"
    r"bytevcloud|douyinvod|365yg\.com|video_gen|unwatermarked|/api/video/stream)",
    re.I,
)


def _settings() -> tuple[str, str]:
    from backend import service_settings

    return service_settings.get_zacao_api_base(), service_settings.get_zacao_api_key()


def parse_share_link(
    share_url: str,
    *,
    timeout: float = 90.0,
    retries: int = 3,
    api_base: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Call zacao parse API; return response data dict (raises on failure)."""
    text = str(share_url or "").strip()
    if not text.startswith("http"):
        raise RuntimeError("分享链接无效，无法解析去水印")
    cfg_base, cfg_key = _settings()
    base = str(api_base or "").strip().rstrip("/") or cfg_base
    key = cfg_key if api_key is None else str(api_key or "").strip()
    endpoint = urljoin(base.rstrip("/") + "/", "api/parse")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last_err = "媒体解析失败"
    for attempt in range(max(1, int(retries))):
        try:
            # Bypass local HTTP(S)_PROXY — same as CDN download path
            with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                resp = client.post(endpoint, json={"text": text}, headers=headers)
        except Exception as exc:
            last_err = f"媒体解析服务不可用: {exc}"
            logger.warning("zacao parse attempt %d network: %s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
            continue
        if resp.status_code in (401, 403):
            raise RuntimeError("媒体解析鉴权失败，请检查后台「解析 API Key」")
        try:
            payload = resp.json()
        except Exception:
            last_err = f"媒体解析返回非 JSON (HTTP {resp.status_code})"
            time.sleep(1.2 * (attempt + 1))
            continue

        code = payload.get("code", payload.get("retcode"))
        ok = payload.get("succ") is True or code in (200, "200", 0)
        msg = (
            payload.get("message")
            or payload.get("retdesc")
            or payload.get("msg")
            or ""
        )
        if resp.status_code >= 400 or not ok:
            # Keep API原文 so admin/task errors are diagnosable.
            # Remote often says「未能获取视频内容」even for image shares (not ready yet).
            raw_msg = str(msg or "").strip() or f"HTTP {resp.status_code}"
            last_err = f"媒体解析失败: {raw_msg}"
            if "未能获取" in raw_msg:
                last_err = (
                    f"媒体解析失败: {raw_msg}"
                    "（分享可能尚未就绪——与后台「解析测试」稍后能通是同一原因：时机，不是 Key）"
                )
            # 502 / 未能获取 → retry (image shares commonly need longer settle)
            if (
                resp.status_code in (429, 500, 502, 503, 504)
                or "稍后" in str(msg)
                or "未能" in str(msg)
                or "繁忙" in str(msg)
            ):
                logger.warning(
                    "zacao parse attempt %d retryable: %s", attempt + 1, last_err
                )
                # Longer backoff: Doubao share index often lags 15–60s after copy toast.
                time.sleep(3.5 * (attempt + 1))
                continue
            raise RuntimeError(last_err)

        data = payload.get("data")
        if not isinstance(data, dict):
            last_err = f"媒体解析无有效 data（{msg or 'empty'}）"
            time.sleep(1.5 * (attempt + 1))
            continue
        return data

    raise RuntimeError(last_err)


def _abs_url(base: str, url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/"):
        return urljoin(base.rstrip("/") + "/", u.lstrip("/"))
    return u


def _image_urls(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    seen_ids: set[str] = set()
    for item in data.get("image_list") or []:
        if isinstance(item, str):
            u = item.strip()
        elif isinstance(item, dict):
            u = str(item.get("url") or item.get("image_url") or "").strip()
        else:
            continue
        if not u.startswith("http"):
            continue
        mid = None
        m = _IMG_ID_RE.search(u)
        if m:
            mid = m.group(1).lower()
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _walk_urls(obj: Any, acc: list[str], depth: int = 0) -> None:
    if depth > 8 or obj is None:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http") and _VIDEO_HINT_RE.search(s):
            acc.append(s)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_urls(v, acc, depth + 1)
        return
    if isinstance(obj, list):
        for v in obj:
            _walk_urls(v, acc, depth + 1)


def _video_url(data: dict[str, Any], base: str) -> str:
    # Prefer CDN source (often lr=unwatermarked) over proxy stream path
    for key in ("source_video_url", "video_url", "play_url", "download_url", "url"):
        u = _abs_url(base, str(data.get(key) or ""))
        if u.startswith("http") and (
            _VIDEO_HINT_RE.search(u) or "/api/video/stream" in u or key == "source_video_url"
        ):
            return u
    for item in data.get("video_list") or []:
        if isinstance(item, str):
            u = _abs_url(base, item)
        elif isinstance(item, dict):
            u = _abs_url(
                base,
                str(
                    item.get("url")
                    or item.get("video_url")
                    or item.get("source_video_url")
                    or ""
                ),
            )
        else:
            continue
        if u.startswith("http"):
            return u
    # Deep scan — some payloads nest urls under media / content
    found: list[str] = []
    _walk_urls(data, found)
    for u in found:
        abs_u = _abs_url(base, u)
        if abs_u.startswith("http"):
            return abs_u
    return ""


def _stream_url(data: dict[str, Any], base: str) -> str:
    """zacao /api/video/stream proxy — useful when direct CDN is blocked by local proxy."""
    raw = str(data.get("video_url") or "").strip()
    u = _abs_url(base, raw)
    if "/api/video/stream" in u:
        return u
    return ""


def results_from_parse(
    data: dict[str, Any], *, kind: str, api_base: str | None = None
) -> list[dict[str, Any]]:
    """Map zacao data → chat_ui result dicts (url / video_url)."""
    cfg_base, _ = _settings()
    base = str(api_base or "").strip().rstrip("/") or cfg_base
    kind = (kind or "image").lower()
    if kind == "video":
        vu = _video_url(data, base)
        if not vu:
            keys = ",".join(sorted(str(k) for k in data.keys())[:20])
            logger.warning(
                "zacao video empty fields keys=%s sample=%s",
                keys,
                str(data)[:400],
            )
            raise RuntimeError(
                "媒体解析未返回视频地址"
                f"（data 字段: {keys or '无'}；可能解析服务暂未取到 Seedance 成片，请稍后重试）"
            )
        cover = _abs_url(base, str(data.get("cover_url") or ""))
        stream = _stream_url(data, base)
        item = {
            "video_url": vu,
            "cover_url": cover if cover.startswith("http") else "",
            "width": 0,
            "height": 0,
            "duration": 0.0,
            "clean": True,
            "via": "zacao",
        }
        if stream and stream != vu:
            item["stream_url"] = stream
        return [item]
    images = _image_urls(data)
    if not images:
        # Some responses put single image in cover only
        cover = str(data.get("cover_url") or "").strip()
        if cover.startswith("http") and "image" in cover:
            images = [cover]
    if not images:
        raise RuntimeError("媒体解析未返回图片地址")
    return [
        {"url": u, "width": 0, "height": 0, "via": "zacao"}
        for u in images
    ]


def parse_to_results(
    share_url: str,
    *,
    kind: str,
    api_base: str | None = None,
    api_key: str | None = None,
    retries: int | None = None,
) -> list[dict[str, Any]]:
    # Images need more internal retries — share index lags after「对话链接已复制」.
    # Videos (esp. Seedance) often return metadata first with empty video_url; keep polling.
    kind_l = (kind or "image").lower()
    n = retries if retries is not None else (5 if kind_l == "image" else 6)
    last_err: Exception | None = None
    for attempt in range(max(1, int(n))):
        try:
            # One HTTP call per outer attempt; empty-video is handled below as retryable.
            data = parse_share_link(
                share_url, retries=1, api_base=api_base, api_key=api_key
            )
        except Exception as exc:
            last_err = exc
            logger.warning(
                "zacao parse_to_results attempt %d/%d network/api: %s",
                attempt + 1,
                n,
                exc,
            )
            time.sleep(2.5 * (attempt + 1))
            continue
        try:
            results = results_from_parse(data, kind=kind_l, api_base=api_base)
        except RuntimeError as exc:
            last_err = exc
            msg = str(exc)
            # API often returns 200 + keys but blank urls until CDN/index is ready.
            retryable_video = kind_l == "video" and (
                "未返回视频" in msg or "暂未取到" in msg or "稍后" in msg
            )
            retryable_image = kind_l == "image" and (
                "未返回图片" in msg or "未能获取" in msg or "稍后" in msg
            )
            if (retryable_video or retryable_image) and attempt + 1 < n:
                logger.warning(
                    "zacao parse_to_results attempt %d/%d empty %s — retry: %s",
                    attempt + 1,
                    n,
                    kind_l,
                    msg[:160],
                )
                time.sleep((4.0 if retryable_video else 3.0) + 2.0 * attempt)
                continue
            raise
        logger.info(
            "zacao parse ok kind=%s n=%d attempt=%d share=%s first=%s",
            kind,
            len(results),
            attempt + 1,
            share_url[:80],
            str(
                (results[0].get("url") or results[0].get("video_url") or "")
                if results
                else ""
            )[:120],
        )
        return results
    if last_err:
        raise last_err
    raise RuntimeError("媒体解析失败")


def diagnose_share_link(
    share_url: str,
    *,
    kind: str = "auto",
    api_base: str | None = None,
    api_key: str | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    """Admin test helper: parse once and report structured success/failure."""
    text = str(share_url or "").strip()
    cfg_base, cfg_key = _settings()
    base = str(api_base or "").strip().rstrip("/") or cfg_base
    key = cfg_key if api_key is None else str(api_key or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "share_url": text[:300],
        "api_base": base,
        "has_api_key": bool(key),
        "kind_requested": (kind or "auto").lower(),
        "kind_resolved": None,
        "elapsed_ms": 0,
        "data_keys": [],
        "image_count": 0,
        "has_video": False,
        "results": [],
        "error": None,
    }
    if not text.startswith("http"):
        out["error"] = "请粘贴以 http 开头的分享链接"
        return out
    if not key:
        out["error"] = "未配置解析 API Key"
        return out

    t0 = time.perf_counter()
    try:
        data = parse_share_link(
            text, retries=max(1, int(retries)), api_base=base, api_key=key
        )
    except Exception as exc:
        out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        out["error"] = str(exc)
        return out

    out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    out["data_keys"] = sorted(str(k) for k in data.keys())[:30]
    images = _image_urls(data)
    cover = str(data.get("cover_url") or "").strip()
    if not images and cover.startswith("http") and "image" in cover:
        images = [cover]
    out["image_count"] = len(images)
    out["has_video"] = bool(_video_url(data, base))

    want = (kind or "auto").lower()
    if want == "auto":
        resolved = "image" if images else ("video" if out["has_video"] else "image")
    else:
        resolved = want
    out["kind_resolved"] = resolved

    try:
        results = results_from_parse(data, kind=resolved, api_base=base)
    except Exception as exc:
        out["error"] = str(exc)
        return out

    # Truncate URLs for UI safety / readability
    slim = []
    for item in results[:8]:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k) for k in ("url", "video_url", "cover_url", "stream_url", "via") if item.get(k)}
        slim.append(row)
    out["results"] = slim
    out["ok"] = bool(slim)
    if not slim:
        out["error"] = "解析成功但未得到可用素材地址"
    return out
