"""Generate canvas preview thumbnails and video poster frames."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PIL import Image

from backend.board_assets import resolve_upload_path
from backend.config import DATA_DIR, UPLOADS_DIR
from backend.media.video_tools import _run_ffmpeg

logger = logging.getLogger(__name__)

THUMB_MAX_WIDTH = 640
THUMB_QUALITY = 82
POSTER_MAX_WIDTH = 960
_poster_locks: dict[str, asyncio.Lock] = {}


def thumb_filename_for(source_name: str) -> str:
    stem = Path(source_name).stem
    return f"{stem}_thumb.webp"


def poster_filename_for(source_name: str) -> str:
    stem = Path(source_name).stem
    return f"{stem}_poster.jpg"


def public_url_for_path(path: Path) -> str:
    path = path.resolve()
    parts = path.parts
    if "workbenches" in parts:
        wi = parts.index("workbenches")
        if wi + 3 < len(parts) and parts[wi + 2] == "assets":
            slug = parts[wi - 1]
            workbench_id = parts[wi + 1]
            filename = parts[wi + 3]
            return f"/uploads/{slug}/workbenches/{workbench_id}/{filename}"
    if "assets" in parts:
        ai = parts.index("assets")
        if ai >= 2 and ai + 1 < len(parts):
            slug = parts[ai - 2]
            board_id = parts[ai - 1]
            filename = parts[ai + 1]
            return f"/uploads/{slug}/{board_id}/{filename}"
    try:
        rel = path.relative_to(UPLOADS_DIR.resolve())
        if len(rel.parts) == 1:
            return f"/uploads/{rel.name}"
    except ValueError:
        pass
    return ""


def _is_fresh(target: Path, source: Path) -> bool:
    if not target.is_file():
        return False
    try:
        return target.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def generate_image_thumbnail(source: Path) -> Path | None:
    if not source.is_file():
        return None
    thumb_path = source.parent / thumb_filename_for(source.name)
    if _is_fresh(thumb_path, source):
        return thumb_path
    try:
        with Image.open(source) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            w, h = im.size
            if w > THUMB_MAX_WIDTH:
                nh = max(1, int(h * THUMB_MAX_WIDTH / w))
                im = im.resize((THUMB_MAX_WIDTH, nh), Image.Resampling.LANCZOS)
            if im.mode == "RGBA":
                im = im.convert("RGB")
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            im.save(thumb_path, "WEBP", quality=THUMB_QUALITY, method=4)
    except Exception as exc:
        logger.warning("image thumb failed for %s: %s", source, exc)
        return None
    return thumb_path if thumb_path.is_file() else None


async def generate_video_poster(source: Path) -> Path | None:
    if not source.is_file():
        return None
    key = str(source.resolve())
    lock = _poster_locks.setdefault(key, asyncio.Lock())
    async with lock:
        poster_path = source.parent / poster_filename_for(source.name)
        if _is_fresh(poster_path, source):
            return poster_path
        try:
            await _run_ffmpeg(
                [
                    "-ss",
                    "0.1",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    "-y",
                    str(poster_path),
                ],
                timeout=180,
            )
        except Exception as exc:
            logger.warning("video poster failed for %s: %s", source, exc)
            return None
        if not poster_path.is_file() or poster_path.stat().st_size < 400:
            try:
                poster_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        await asyncio.to_thread(generate_image_thumbnail, poster_path)
        return poster_path


def thumb_urls_for_image(source: Path) -> dict[str, str]:
    thumb = generate_image_thumbnail(source)
    if not thumb:
        return {}
    url = public_url_for_path(thumb)
    return {"thumb_url": url} if url else {}


async def thumb_urls_for_video(source: Path) -> dict[str, str]:
    poster = await generate_video_poster(source)
    if not poster:
        return {}
    cover_url = public_url_for_path(poster)
    out: dict[str, str] = {}
    if cover_url:
        out["cover_url"] = cover_url
    thumb = await asyncio.to_thread(generate_image_thumbnail, poster)
    if thumb:
        thumb_url = public_url_for_path(thumb)
        if thumb_url:
            out["thumb_url"] = thumb_url
    elif cover_url:
        out["thumb_url"] = cover_url
    return out


async def enrich_upload(kind: str, path: Path) -> dict[str, str]:
    if kind == "image":
        return await asyncio.to_thread(thumb_urls_for_image, path)
    if kind == "video":
        return await thumb_urls_for_video(path)
    return {}


async def ensure_media_thumb(url: str, kind: str = "image") -> dict[str, str]:
    path = resolve_upload_path(url)
    if not path or not path.is_file():
        raise FileNotFoundError("文件不存在")
    kind = (kind or "image").lower()
    if kind == "video":
        result = await thumb_urls_for_video(path)
    else:
        # PIL resize must not block the uvicorn event loop — otherwise /uploads
        # and /api/ping stall and every canvas preview spins.
        result = await asyncio.to_thread(thumb_urls_for_image, path)
    if not result:
        raise RuntimeError("缩略图生成失败，请稍后重试")
    return result


def ensure_media_thumb_sync(url: str, kind: str = "image") -> dict[str, str]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(ensure_media_thumb(url, kind))
    return asyncio.run_coroutine_threadsafe(ensure_media_thumb(url, kind), loop).result()
