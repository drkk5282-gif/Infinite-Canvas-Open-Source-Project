"""Download remote Doubao CDN images into local /uploads."""

from __future__ import annotations

import io
import logging
import re
import uuid

import httpx
from PIL import Image

from backend.board_assets import alloc_asset_path, write_bytes

logger = logging.getLogger(__name__)


def _to_original_image_url(url: str) -> str:
    if not url:
        return url
    u = (url or "").strip()
    query = ""
    if "?" in u:
        path, query = u.split("?", 1)
    else:
        path = u
    path = re.sub(r"~tplv[^?]*", "", path)
    if query:
        parts = []
        for part in query.split("&"):
            low = part.lower()
            if low.startswith(("watermark=", "logo=", "wm=")):
                continue
            if part:
                parts.append(part)
        query = "&".join(parts)
    if "watermark=1" in query:
        query = query.replace("watermark=1", "watermark=0")
    return f"{path}?{query}" if query else path


def _to_clean_image_url(url: str) -> str:
    u = _to_original_image_url(url)
    if not u.startswith("http"):
        return u
    if "lr=" in u and "no_watermark" not in u.lower():
        u = re.sub(r"lr=[^&]+", "lr=image_gen_no_watermark", u, count=1)
    return u


async def persist_remote_image(
    page,
    url: str,
    *,
    keep_extra: set[str] | None = None,
) -> dict[str, int | str] | None:
    """Save byteimg CDN URL to /uploads (browser cookies optional)."""
    if not url or not str(url).startswith("http"):
        return None

    cookie_str = ""
    if page is not None:
        try:
            cookies = await page.context.cookies()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies if c.get("name")
            )
        except Exception:
            cookie_str = ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.doubao.com/",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str

    # Clean / stripped first — watermarked display URL last
    candidates = [_to_clean_image_url(url), _to_original_image_url(url), url]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            async with httpx.AsyncClient(
                timeout=120, follow_redirects=True, trust_env=False
            ) as client:
                resp = await client.get(candidate, headers=headers)
                resp.raise_for_status()
                data = resp.content
            if len(data) < 2048:
                continue
            ext = ".jpg"
            ct = (resp.headers.get("content-type") or "").lower()
            if "png" in ct:
                ext = ".png"
            elif "webp" in ct:
                ext = ".webp"
            path, url = alloc_asset_path(ext)
            path.write_bytes(data)
            w = h = 0
            try:
                im = Image.open(io.BytesIO(data))
                w, h = im.size
            except Exception:
                pass
            logger.info(
                "saved generated image to %s (%dx%d)%s",
                url,
                w,
                h,
                " [clean]" if candidate != url else "",
            )
            meta = {"url": url, "width": w, "height": h}
            try:
                import asyncio
                from backend.media.thumbnails import thumb_urls_for_image

                meta.update(await asyncio.to_thread(thumb_urls_for_image, path))
            except Exception as thumb_exc:
                logger.debug("persist image thumb skipped: %s", thumb_exc)
            return meta
        except Exception as exc:
            logger.debug("persist remote image failed for %s: %s", candidate[:100], exc)
    return None


_VIDEO_URL_HINT = re.compile(
    r"(?:\.mp4(?:\?|$)|\.m3u8(?:\?|$)|mime_type=video_mp4|video_gen|douyinvod|"
    r"bytevcloud|vlabvod|volcvod|/video/|video_id=|bytevid|tos-cn-v-|mediacloud)",
    re.I,
)


def looks_like_video_url(url: str) -> bool:
    if not url or not str(url).startswith("http"):
        return False
    if re.search(r"\.(?:png|jpe?g|webp|gif|svg)(?:~|\?|$)", url, re.I):
        return False
    if "doubao.com/legal" in url or "community_guidelines" in url:
        return False
    return bool(_VIDEO_URL_HINT.search(url))


def _to_clean_video_url(url: str) -> str:
    """Rewrite lr=/logo_type= watermark markers (CDN may ignore — verify separately)."""
    u = (url or "").strip()
    if not u.startswith("http"):
        return u
    # Already clean Doubao/zacao marker — do not rewrite (unwatermarked contains "watermark")
    if re.search(r"lr=unwatermarked\b", u, re.I):
        return u
    u = re.sub(
        r"lr=video_gen_watermark(?:_dyn)?",
        "lr=video_gen_no_watermark",
        u,
        flags=re.I,
    )
    if (
        "lr=" in u
        and "no_watermark" not in u.lower()
        and "unwatermarked" not in u.lower()
        and re.search(r"lr=[^&]*watermark", u, re.I)
    ):
        u = re.sub(r"lr=[^&]+", "lr=video_gen_no_watermark", u, count=1)
    u = re.sub(
        r"logo_type=video_gen_watermark(?:_dyn)?",
        "logo_type=video_gen_no_watermark",
        u,
        flags=re.I,
    )
    if "watermark=1" in u:
        u = u.replace("watermark=1", "watermark=0")
    u = re.sub(r"~tplv-[^.?&]*watermark[^.?&]*", "", u, flags=re.I)
    u = re.sub(r"([&?])logo=[^&]*", r"\1", u)
    u = re.sub(r"\?&+", "?", u)
    u = re.sub(r"&&+", "&", u)
    return u.rstrip("?&")


def _url_has_baked_watermark(url: str) -> bool:
    """True when this CDN asset is watermarked (incl. lr= rewrite of watermark_dyn)."""
    lower = (url or "").lower()
    if not lower.startswith("http"):
        return False
    if re.search(
        r"video_gen_watermark(?:_dyn)?|watermark_dyn|watermark=1|"
        r"logo_type=[^&]*watermark",
        lower,
    ):
        return True
    if "lr=video_gen_no_watermark" in lower and re.search(
        r"videoweb\.doubao\.com|douyin\.com|douyinvod|bytevcloud|vlabvod|volcvod|snssdk\.com",
        lower,
    ):
        return True
    return False


def _is_bytedance_cdn_url(url: str) -> bool:
    """ByteDance video CDN hosts — often 403 if Cookie/extra headers look 'dirty'."""
    u = (url or "").lower()
    return any(
        h in u
        for h in (
            "365yg.com",
            "bytevcloud",
            "douyinvod",
            "vlabvod",
            "volcvod",
            "tos-cn-v-",
            "pstatp.com",
            "byteimg.com",
            "snssdk.com",
            "douyin.com",
            "iesdouyin.com",
            "videoweb.doubao.com",
        )
    )


def _video_download_headers(url: str, *, cookie_str: str = "") -> dict[str, str]:
    """Build request headers for video download.

    字节 CDN（365yg 等）会校验请求头：浏览器地址栏打开通常很干净；
    若顺手带上豆包整站 Cookie / 多余 Authorization，会被判「协议头不干净」→ 403。
    手动复制链接能下、程序下不了，多半就是这个原因。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if "video.zacao.top" in (url or "") or "/api/video/stream" in (url or ""):
        headers["Referer"] = "https://www.doubao.com/"
        try:
            from backend import service_settings

            key = service_settings.get_zacao_api_key()
            if key:
                headers["Authorization"] = f"Bearer {key}"
            base = service_settings.get_zacao_api_base()
            if base:
                headers["Referer"] = base.rstrip("/") + "/"
        except Exception:
            pass
        return headers

    if _is_bytedance_cdn_url(url):
        # 干净头：仅 UA + 豆包 Referer，不带 Cookie
        headers["Referer"] = "https://www.doubao.com/"
        return headers

    headers["Referer"] = "https://www.doubao.com/"
    if cookie_str:
        headers["Cookie"] = cookie_str
    return headers


async def persist_remote_video(page, url: str) -> dict[str, int | float | str] | None:
    """Download a remote Doubao video into /uploads for reliable local playback."""
    if not looks_like_video_url(url):
        return None

    cookie_str = ""
    if page is not None and not _is_bytedance_cdn_url(url):
        try:
            cookies = await page.context.cookies()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies if c.get("name")
            )
        except Exception:
            cookie_str = ""

    candidates: list[str] = []
    clean = _to_clean_video_url(url)
    for u in (url, clean):
        if u and u not in candidates:
            candidates.append(u)

    last_exc: Exception | None = None
    for candidate in candidates:
        header_variants: list[dict[str, str]] = [
            _video_download_headers(candidate, cookie_str=cookie_str),
        ]
        if _is_bytedance_cdn_url(candidate):
            # 地址栏风格：几乎无 Referer，部分 CDN 更吃这套
            bare = {
                "User-Agent": header_variants[0]["User-Agent"],
                "Accept": "*/*",
            }
            header_variants.append(bare)
        for headers in header_variants:
            try:
                # Bypass system HTTP(S)_PROXY — CDN often 403s via local clash/v2ray.
                async with httpx.AsyncClient(
                    timeout=180, follow_redirects=True, trust_env=False
                ) as client:
                    head = await client.head(candidate, headers=headers)
                    ct = (head.headers.get("content-type") or "").lower()
                    if head.status_code >= 400:
                        ct = ""
                    if ct and not any(
                        x in ct for x in ("video/", "mpegurl", "octet-stream", "mp4")
                    ):
                        if "text/html" in ct or "image/" in ct or "json" in ct:
                            logger.debug(
                                "skip non-video content-type %s for %s",
                                ct,
                                candidate[:100],
                            )
                            continue

                    resp = await client.get(candidate, headers=headers)
                    resp.raise_for_status()
                    data = resp.content
                    ct = (resp.headers.get("content-type") or ct or "").lower()
                if len(data) < 50_000:
                    logger.debug(
                        "skip tiny video payload (%d bytes) %s",
                        len(data),
                        candidate[:100],
                    )
                    continue
                head_bytes = data[:200].lstrip().lower()
                if head_bytes.startswith(b"<!doctype") or head_bytes.startswith(b"<html"):
                    continue
                if ct and not any(
                    x in ct for x in ("video/", "mpegurl", "octet-stream", "mp4")
                ):
                    if "text/html" in ct or "image/" in ct:
                        continue

                ext = ".mp4"
                if "webm" in ct:
                    ext = ".webm"
                elif "m3u8" in ct or candidate.lower().endswith(".m3u8"):
                    logger.debug("skip m3u8 (not locally playable): %s", candidate[:100])
                    continue
                else:
                    from backend.media.video_tools import guess_video_extension

                    ext = guess_video_extension(url=candidate, content_type=ct, head=data)

                path, video_url = alloc_asset_path(ext)
                path.write_bytes(data)
                logger.info(
                    "saved generated video to %s (%d bytes)%s",
                    video_url,
                    len(data),
                    " [clean]" if candidate != url else "",
                )
                from backend.media.video_tools import finalize_saved_video

                return await finalize_saved_video(path, video_url)
            except Exception as exc:
                last_exc = exc
                logger.debug(
                    "persist remote video failed for %s headers=%s: %s",
                    candidate[:100],
                    ",".join(sorted(headers.keys())),
                    exc,
                )
                continue
    if last_exc:
        logger.debug("persist remote video exhausted candidates: %s", last_exc)
    return None
