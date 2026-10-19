"""Local (non-AI) video utilities via ffmpeg."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

from backend.board_assets import alloc_asset_path, resolve_upload_path as _resolve_board_asset, upload_ref_from_url

logger = logging.getLogger(__name__)


def hidden_subprocess_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


SAFE_OPS = frozenset(
    {
        "extract_audio",
        "extract_video",
        "extract_frame",
        "trim",
        "trim_audio",
        "convert_audio",
        "crop",
        "compress",
        "reverse",
        "to_gif",
        "upscale",
    }
)

RATIO_CROP = {
    "1:1": (1, 1),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "4:3": (4, 3),
    "3:4": (3, 4),
}

# Max output pixels for compress smart-fit (≈2.09M like Jimeng)
COMPRESS_MAX_PIXELS = 2_088_876

SCALE_PRESETS = {
    "1080": 1080,
    "720": 720,
    "540": 540,
    "480": 480,
    "360": 360,
}

GIF_QUALITY = {
    "high": 256,
    "standard": 128,
    "smooth": 64,
}


def get_ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise RuntimeError("未找到 ffmpeg，请安装 imageio-ffmpeg 或将 ffmpeg 加入 PATH")


def resolve_upload_path(url: str) -> Path:
    path = _resolve_board_asset(url)
    if path and path.is_file():
        return path
    raise FileNotFoundError("视频文件不存在，请先确保已下载到本地")


async def ensure_local_video(url: str) -> Path:
    """Return a local Path for the given video URL (download if remote)."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("缺少视频地址")

    # Absolute same-origin /uploads URLs must stay local — never HTTP-fetch them
    # (missing files would otherwise become opaque 500 download errors).
    upload_idx = raw.find("/uploads/")
    if upload_idx >= 0:
        local_url = raw[upload_idx:].split("?", 1)[0].split("#", 1)[0]
        path = _resolve_board_asset(local_url)
        if path and path.is_file():
            return path
        found = None
        try:
            from backend.board_assets import find_asset_by_filename

            found = find_asset_by_filename(Path(local_url).name)
        except Exception:
            found = None
        if found and found.is_file():
            return found
        ref = upload_ref_from_url(local_url) or Path(local_url).name
        raise FileNotFoundError(
            f"本地视频已丢失（{ref}）。请重新生成或上传后再使用工具。"
        )

    path = _resolve_board_asset(raw)
    if path and path.is_file():
        return path

    if raw.startswith("/") and "://" not in raw:
        ref = upload_ref_from_url(raw) or Path(raw).name
        raise FileNotFoundError(
            f"本地视频已丢失（{ref}）。请重新生成或上传后再使用工具。"
        )

    if not raw.startswith("http://") and not raw.startswith("https://"):
        raise FileNotFoundError("视频地址无效，请使用已生成或已上传的视频")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    # 字节 CDN：不要带 Cookie；Referer 用豆包即可（协议头保持干净）
    from backend.media.persist import _is_bytedance_cdn_url

    if _is_bytedance_cdn_url(raw):
        headers["Referer"] = "https://www.doubao.com/"
    else:
        headers["Referer"] = "https://www.doubao.com/"
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True, trust_env=False) as client:
            resp = await client.get(raw, headers=headers)
            resp.raise_for_status()
            data = resp.content
            ct = (resp.headers.get("content-type") or "").lower()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        raise RuntimeError(f"下载远程视频失败（HTTP {code}）") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"下载远程视频失败：{exc}") from exc

    if len(data) < 8_000:
        raise RuntimeError("下载的视频过小，可能不是有效文件")
    head = data[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise RuntimeError("下载失败：返回了网页而非视频")

    ext = ".mp4"
    if "webm" in ct or raw.lower().endswith(".webm"):
        ext = ".webm"
    elif "quicktime" in ct or raw.lower().endswith(".mov"):
        ext = ".mov"

    out, public_url = alloc_asset_path(ext)
    out.write_bytes(data)
    logger.info("cached remote video → %s (%d bytes)", public_url, len(data))
    return out


async def _run_ffmpeg(args: list[str], timeout: float = 900) -> None:
    ffmpeg = get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", *args]
    logger.info("ffmpeg: %s", " ".join(cmd[:14]) + ("…" if len(cmd) > 14 else ""))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("处理超时") from None
    if proc.returncode != 0:
        msg = (err or b"").decode("utf-8", errors="ignore")[-500:]
        raise RuntimeError(msg.strip() or f"ffmpeg 失败 (code={proc.returncode})")


def _out_path(ext: str) -> tuple[Path, str]:
    return alloc_asset_path(ext)


_NON_BROWSER_FTYP_BRANDS = frozenset({"f4v", "f4a", "flv", "flx", "f4p"})
_BROWSER_H264_CODECS = frozenset({"h264", "avc1", "avc3"})
_BROWSER_WEBM_CODECS = frozenset({"vp8", "vp9", "av1"})
_BROWSER_SAFE_PIX_FMT = frozenset({"yuv420p", "yuvj420p"})
# Containers that *may* play natively after codec check (everything else → transcode).
_BROWSER_NATIVE_EXT = frozenset({".mp4", ".m4v", ".webm", ".mov"})
# Accepted on upload / import from any platform; non-native types are always normalized.
UPLOAD_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".webm",
        ".mov",
        ".avi",
        ".mkv",
        ".f4v",
        ".flv",
        ".wmv",
        ".mpeg",
        ".mpg",
        ".3gp",
        ".3g2",
        ".ts",
        ".m2ts",
        ".mts",
        ".ogv",
        ".mxf",
        ".asf",
        ".rm",
        ".rmvb",
        ".vob",
        ".ogm",
        ".divx",
    }
)


def _read_container_brand_from_bytes(head: bytes) -> str:
    if head[:3] == b"FLV":
        return "flv"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return head[8:12].decode("ascii", errors="ignore").strip().lower()
    return ""


def _read_container_brand(path: Path) -> str:
    """Detect FLV container or ISO-BMFF ftyp brand (e.g. f4v saved as .mp4)."""
    try:
        head = path.read_bytes()[:32]
    except OSError:
        return ""
    return _read_container_brand_from_bytes(head)


def _container_needs_transcode(path: Path) -> bool:
    brand = _read_container_brand(path)
    if brand == "flv":
        return True
    return brand in _NON_BROWSER_FTYP_BRANDS


def _is_browser_safe_stream(ext: str, info: dict[str, str | int]) -> bool:
    codec = str(info.get("codec") or "").lower()
    pix_fmt = str(info.get("pix_fmt") or "").lower()
    if not codec:
        return False
    if ext in (".mp4", ".m4v", ".mov"):
        if codec not in _BROWSER_H264_CODECS:
            return False
        return not pix_fmt or pix_fmt in _BROWSER_SAFE_PIX_FMT
    if ext == ".webm":
        return codec in _BROWSER_WEBM_CODECS
    return False


def guess_video_extension(*, url: str = "", content_type: str = "", head: bytes = b"") -> str:
    """Best-effort extension when saving a remote/imported video from any provider."""
    url_l = (url or "").lower().split("?")[0]
    for ext in sorted(UPLOAD_VIDEO_EXTENSIONS, key=len, reverse=True):
        if url_l.endswith(ext):
            return ext
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    if "f4v" in ct:
        return ".f4v"
    if "flv" in ct:
        return ".flv"
    if "matroska" in ct or "mkv" in ct:
        return ".mkv"
    if "x-msvideo" in ct or "avi" in ct:
        return ".avi"
    if "mp4" in ct or "mpeg" in ct:
        return ".mp4"
    brand = _read_container_brand_from_bytes(head[:32] if head else b"")
    if brand == "flv":
        return ".flv"
    if brand in _NON_BROWSER_FTYP_BRANDS:
        return ".f4v"
    return ".mp4"


async def probe_video_stream(path: Path) -> dict[str, str | int]:
    """Return codec/geometry for the first video stream (empty dict when unknown)."""
    result: dict[str, str | int] = {}
    ffmpeg = get_ffmpeg_exe()
    probe = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if not probe.is_file():
        return result
    proc = await asyncio.create_subprocess_exec(
        str(probe),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,codec_tag_string,width,height,pix_fmt",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    out, _ = await proc.communicate()
    try:
        payload = json.loads((out or b"{}").decode("utf-8", errors="ignore") or "{}")
    except json.JSONDecodeError:
        return result
    streams = payload.get("streams") or []
    if not streams:
        return result
    stream = streams[0]
    codec = str(stream.get("codec_name") or stream.get("codec_tag_string") or "").strip().lower()
    if codec:
        result["codec"] = codec
    for key in ("width", "height"):
        try:
            val = int(stream.get(key) or 0)
        except (TypeError, ValueError):
            val = 0
        if val > 0:
            result[key] = val
    pix = str(stream.get("pix_fmt") or "").strip().lower()
    if pix:
        result["pix_fmt"] = pix
    return result


async def needs_browser_video_transcode(path: Path, *, force: bool = False) -> bool:
    """True unless file is verified browser-playable (any source / any container).

    ``force`` is kept for API compatibility but must NOT mean "always re-encode".
    Re-encoding deletes/replaces bytes and historically wiped playable videos when
    the canvas still pointed at the old path.
    """
    del force  # probe always; never blindly re-transcode

    ext = path.suffix.lower()
    if not ext or ext not in _BROWSER_NATIVE_EXT:
        return True
    if _container_needs_transcode(path):
        return True
    try:
        head = path.read_bytes()[:12]
    except OSError:
        head = b""
    if head[:3] == b"FLV":
        return True

    info = await probe_video_stream(path)
    if not info.get("codec"):
        return True
    return not _is_browser_safe_stream(ext, info)


_BROWSER_MP4_ARGS = [
    "-c:v",
    "libx264",
    "-preset",
    "fast",
    "-crf",
    "20",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "128k",
    "-movflags",
    "+faststart",
]


async def _transcode_file_to_mp4(src: Path) -> tuple[Path, str]:
    """Transcode any input (f4v/flv/hevc mp4/…) to browser-safe H.264 MP4."""
    out_path, out_url = _out_path(".mp4")
    ext = src.suffix.lower()
    brand = _read_container_brand(src)
    attempts: list[list[str]] = [["-i", str(src), *_BROWSER_MP4_ARGS, str(out_path)]]
    if brand == "flv" or ext in (".f4v", ".flv"):
        attempts.append(["-f", "flv", "-i", str(src), *_BROWSER_MP4_ARGS, str(out_path)])

    last_exc: RuntimeError | None = None
    for args in attempts:
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        try:
            await _run_ffmpeg(args)
            return out_path, out_url
        except RuntimeError as exc:
            last_exc = exc
    if out_path.exists():
        out_path.unlink(missing_ok=True)
    raise last_exc or RuntimeError("视频转码失败")


def _bind_scope_from_upload_url(video_url: str) -> tuple[object | None, object | None]:
    """Keep transcoded output in the same workbench/board folder as the source URL."""
    from backend import board_assets

    raw = (video_url or "").strip()
    m = re.search(r"/uploads/[^/]+/workbenches/([^/?#]+)/", raw, re.I)
    if m:
        return board_assets.set_workbench_id(m.group(1)), None
    m = re.search(r"/uploads/[^/]+/([^/?#]+)/[^/?#]+$", raw, re.I)
    if m and m.group(1).lower() != "workbenches":
        return None, board_assets.set_board_id(m.group(1))
    return None, None


async def normalize_video_for_browser(
    *, path: Path, video_url: str, force: bool = False
) -> dict[str, int | float | str]:
    """Transcode to H.264/AAC MP4 when needed; return updated public URL."""
    from backend import board_assets

    base = {"video_url": video_url, "width": 0, "height": 0, "duration": 0.0}
    if not path.is_file():
        return base
    try:
        if path.stat().st_size < 1000:
            return base
    except OSError:
        return base

    if not await needs_browser_video_transcode(path, force=force):
        w, h = await probe_size(path)
        info = await probe_video_stream(path)
        if not w and info.get("width"):
            w = int(info["width"])
        if not h and info.get("height"):
            h = int(info["height"])
        return {**base, "width": w or 0, "height": h or 0}

    wb_token, board_token = _bind_scope_from_upload_url(video_url)
    try:
        out_path, out_url = await _transcode_file_to_mp4(path)
    except RuntimeError as exc:
        logger.warning("video mp4 transcode failed for %s: %s", video_url, exc)
        w, h = await probe_size(path)
        return {**base, "width": w or 0, "height": h or 0}
    finally:
        if board_token is not None:
            board_assets.reset_board_id(board_token)
        if wb_token is not None:
            board_assets.reset_workbench_id(wb_token)

    try:
        if out_path.stat().st_size < 1000:
            out_path.unlink(missing_ok=True)
            logger.warning("video mp4 transcode too small for %s", video_url)
            return base
    except OSError:
        return base

    # Keep the source file. Deleting it here used to leave the canvas pointing at a
    # dead URL when the client failed to rewrite videoUrl to out_url (or raced).
    # Unreferenced leftovers can be cleaned later via orphan purge.
    if path.resolve() != out_path.resolve():
        logger.info(
            "kept source video after transcode: %s (playable=%s)",
            path.name,
            out_path.name,
        )

    w, h = await probe_size(out_path)
    logger.info("transcoded video to browser mp4: %s -> %s (%dx%d)", video_url, out_url, w or 0, h or 0)
    return {"video_url": out_url, "width": w or 0, "height": h or 0, "duration": 0.0}


async def finalize_saved_video(path: Path, video_url: str) -> dict[str, int | float | str]:
    """Post-save hook: ensure canvas/browser can play the file."""
    norm = await normalize_video_for_browser(path=path, video_url=video_url)
    return {
        "video_url": norm["video_url"],
        "cover_url": "",
        "width": int(norm.get("width") or 0),
        "height": int(norm.get("height") or 0),
        "duration": float(norm.get("duration") or 0.0),
    }


async def ensure_upload_browser_video(
    video_url: str, *, force: bool = False
) -> dict[str, int | float | str | bool]:
    """On-demand: transcode an existing /uploads video for HTML5 playback."""
    raw = (video_url or "").strip()
    if not raw:
        raise ValueError("缺少视频地址")
    rel = raw.split("?", 1)[0]
    if not rel.startswith("/uploads/"):
        raise ValueError("仅支持本地 /uploads 视频")
    path = _resolve_board_asset(rel)
    if not path or not path.is_file():
        raise FileNotFoundError("视频文件不存在")
    before = rel
    norm = await normalize_video_for_browser(path=path, video_url=rel, force=force)
    after = str(norm.get("video_url") or before)
    return {
        "url": after,
        "converted": after != before,
        "width": int(norm.get("width") or 0),
        "height": int(norm.get("height") or 0),
    }


async def probe_size(path: Path) -> tuple[int | None, int | None]:
    ffmpeg = get_ffmpeg_exe()
    probe = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if probe.is_file():
        proc = await asyncio.create_subprocess_exec(
            str(probe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode().strip()
        if "x" in text:
            try:
                w, h = text.split("x", 1)
                return int(w), int(h)
            except ValueError:
                pass
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-i",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    _out, err = await proc.communicate()
    text = (err or b"").decode("utf-8", errors="ignore")
    m = re.search(r"(\d{2,5})x(\d{2,5})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _probe_image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            return int(w), int(h)
    except Exception:
        return None, None


def _frame_file_ok(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= 32
    except OSError:
        return False


async def _ffmpeg_extract_one_frame(src: Path, out: Path, *, ss: float | None = None, sseof: float | None = None) -> bool:
    """Run ffmpeg to write one PNG frame. Returns True if a usable file was produced."""
    if out.exists():
        out.unlink(missing_ok=True)
    args: list[str] = []
    if sseof is not None:
        args.extend(["-sseof", f"{float(sseof):.3f}"])
    if ss is not None:
        args.extend(["-ss", f"{max(0.0, float(ss)):.3f}"])
    args.extend(["-i", str(src)])
    if sseof is not None:
        args.extend(["-update", "1"])
    args.extend(["-frames:v", "1", str(out)])
    try:
        await _run_ffmpeg(args, timeout=120)
    except RuntimeError:
        if out.exists():
            out.unlink(missing_ok=True)
        return False
    if _frame_file_ok(out):
        return True
    if out.exists():
        out.unlink(missing_ok=True)
    return False


async def _extract_near_end(src: Path, out: Path, duration: float) -> bool:
    """Seek slightly before EOF — imageio ffmpeg often writes nothing at exact duration / -sseof."""
    if not duration or duration <= 0:
        return False
    for delta in (0.05, 0.08, 0.2, 0.5, 1.0):
        t = max(0.0, float(duration) - delta)
        if await _ffmpeg_extract_one_frame(src, out, ss=t):
            return True
    return False


async def _extract_video_frame(src: Path, *, start: float | None) -> tuple[Path, str]:
    """Extract one PNG frame. start<0 → last frame; start>0 → at time; else first frame."""
    out, out_url = _out_path(".png")
    when_last = start is not None and float(start) < 0
    when_at = start is not None and float(start) > 0 and not when_last

    ok = False
    if when_last:
        # Prefer duration seek: -sseof often exits 0 with empty output on imageio builds.
        dur = await probe_duration(src)
        if dur and dur > 0:
            ok = await _extract_near_end(src, out, dur)
        if not ok:
            ok = await _ffmpeg_extract_one_frame(src, out, sseof=-0.08)
        if not ok and not (dur and dur > 0):
            # Last resort if duration probe failed earlier
            dur = await probe_duration(src)
            if dur and dur > 0:
                ok = await _extract_near_end(src, out, dur)
    elif when_at:
        t = max(0.0, float(start))
        dur = await probe_duration(src)
        if dur and dur > 0 and t >= max(0.0, float(dur) - 0.04):
            ok = await _extract_near_end(src, out, dur)
        else:
            ok = await _ffmpeg_extract_one_frame(src, out, ss=t)
            if not ok and dur and dur > 0 and t >= float(dur) * 0.9:
                ok = await _extract_near_end(src, out, dur)
    else:
        ok = await _ffmpeg_extract_one_frame(src, out)

    if not ok or not _frame_file_ok(out):
        raise RuntimeError("截帧失败")
    return out, out_url


async def probe_duration(path: Path) -> float | None:
    ffmpeg = get_ffmpeg_exe()
    probe = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if probe.is_file():
        proc = await asyncio.create_subprocess_exec(
            str(probe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **hidden_subprocess_kwargs(),
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode().strip()
        try:
            return float(text)
        except ValueError:
            pass

    # imageio_ffmpeg ships ffmpeg only — parse Duration from `ffmpeg -i` stderr.
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-i",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    _out, err = await proc.communicate()
    text = (err or b"").decode("utf-8", errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + sec


def _even(n: int) -> int:
    return max(2, int(n) // 2 * 2)


def _fit_scale(sw: int, sh: int, *, max_h: int | None = None, max_pixels: int | None = None) -> tuple[int, int]:
    w, h = sw, sh
    if max_h and h > max_h:
        w = int(round(w * max_h / h))
        h = max_h
    if max_pixels and w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        w = int(round(w * scale))
        h = int(round(h * scale))
    return _even(w), _even(h)


async def run_video_tool(
    op: str,
    url: str,
    *,
    start: float | None = None,
    end: float | None = None,
    ratio: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    format: str | None = None,
    quality: str | None = None,
    scale_mode: str | None = None,
    crop_x: int | None = None,
    crop_y: int | None = None,
    crop_w: int | None = None,
    crop_h: int | None = None,
    scale: int | None = None,
    model: str | None = None,
    mode: str | None = None,
    algo: str | None = None,
    engine_tier: str | None = None,
    target_res: str | None = None,
) -> dict:
    if op not in SAFE_OPS:
        raise ValueError(f"不支持的操作: {op}")
    src = await ensure_local_video(url)

    if op == "upscale":
        from backend import service_settings
        from backend.media.upscale_queue import create_job, job_public_view, should_use_remote_upscale

        if not service_settings.get_upscale_enabled():
            raise ValueError("超分功能已在后台关闭")

        mode_l = (mode or "ai").strip().lower()
        if should_use_remote_upscale(mode=mode_l):
            from backend.user_scope import get_current_user_id

            job = create_job(
                url,
                user_id=get_current_user_id(),
                mode=mode_l,
                algo=algo,
                engine_tier=engine_tier,
                target_res=target_res,
                fps=fps,
                scale=int(scale) if scale else None,
                model=model,
            )
            view = job_public_view(job)
            view["op"] = "upscale"
            return view

        from backend.media.video_upscale import humanize_upscale_error, upscale_video

        out, out_url = _out_path(".mp4")
        try:
            tw, th = await upscale_video(
                src,
                out,
                scale=int(scale) if scale else None,
                model=model,
                mode=(mode or "ai"),
                algo=(algo or "v3"),
                engine_tier=(engine_tier or "pro"),
                target_res=(target_res or "1080"),
                fps=fps,
            )
        except Exception as exc:
            if out.exists():
                out.unlink(missing_ok=True)
            raise RuntimeError(humanize_upscale_error(exc)) from exc
        return {"url": out_url, "kind": "video", "width": tw, "height": th}

    if op == "extract_frame":
        out, out_url = await _extract_video_frame(src, start=start)
        w, h = _probe_image_size(out)
        if not w or not h:
            w, h = await probe_size(src)
        return {
            "url": out_url,
            "kind": "image",
            "width": w,
            "height": h,
            "op": op,
        }

    if op == "extract_audio":
        out, out_url = _out_path(".mp3")
        await _run_ffmpeg(
            ["-i", str(src), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(out)]
        )
        return {"url": out_url, "kind": "audio", "width": None, "height": None}

    if op == "extract_video":
        out, out_url = _out_path(".mp4")
        try:
            await _run_ffmpeg(
                ["-i", str(src), "-an", "-c:v", "copy", "-movflags", "+faststart", str(out)]
            )
        except RuntimeError:
            if out.exists():
                out.unlink(missing_ok=True)
            await _run_ffmpeg(
                [
                    "-i",
                    str(src),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-movflags",
                    "+faststart",
                    str(out),
                ]
            )
        w, h = await probe_size(out)
        return {"url": out_url, "kind": "video", "width": w, "height": h}

    if op == "trim":
        if start is None or end is None:
            raise ValueError("长度裁剪需要开始与结束时间")
        s = max(0.0, float(start))
        e = max(s + 0.05, float(end))
        duration = e - s
        out, out_url = _out_path(".mp4")
        await _run_ffmpeg(
            [
                "-ss",
                f"{s:.3f}",
                "-i",
                str(src),
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        w, h = await probe_size(out)
        return {"url": out_url, "kind": "video", "width": w, "height": h}

    if op == "trim_audio":
        if start is None or end is None:
            raise ValueError("音频裁剪需要开始与结束时间")
        s = max(0.0, float(start))
        e = max(s + 0.05, float(end))
        duration = e - s
        fmt = (format or "mp3").lower().strip()
        if fmt not in ("mp3", "wav", "m4a", "aac"):
            fmt = "mp3"
        out, out_url = _out_path(f".{fmt}")
        if fmt == "wav":
            codec_args = ["-vn", "-acodec", "pcm_s16le"]
        elif fmt == "m4a" or fmt == "aac":
            codec_args = ["-vn", "-acodec", "aac", "-b:a", "192k"]
        else:
            codec_args = ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]
        await _run_ffmpeg(
            [
                "-ss",
                f"{s:.3f}",
                "-i",
                str(src),
                "-t",
                f"{duration:.3f}",
                *codec_args,
                str(out),
            ]
        )
        return {"url": out_url, "kind": "audio", "width": None, "height": None}

    if op == "convert_audio":
        fmt = (format or "mp3").lower().strip()
        if fmt not in ("mp3", "wav", "m4a", "aac"):
            raise ValueError("仅支持 mp3 / wav / m4a")
        out, out_url = _out_path(f".{fmt}")
        if fmt == "wav":
            codec_args = ["-vn", "-acodec", "pcm_s16le"]
        elif fmt in ("m4a", "aac"):
            codec_args = ["-vn", "-acodec", "aac", "-b:a", "192k"]
        else:
            codec_args = ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]
        await _run_ffmpeg(["-i", str(src), *codec_args, str(out)])
        return {"url": out_url, "kind": "audio", "width": None, "height": None}

    if op == "crop":
        sw, sh = await probe_size(src)
        if not sw or not sh:
            raise RuntimeError("无法读取视频尺寸")

        if crop_w and crop_h and crop_x is not None and crop_y is not None:
            new_w = _even(max(2, int(crop_w)))
            new_h = _even(max(2, int(crop_h)))
            x = max(0, min(int(crop_x), max(0, sw - new_w)))
            y = max(0, min(int(crop_y), max(0, sh - new_h)))
            new_w = min(new_w, _even(sw - x))
            new_h = min(new_h, _even(sh - y))
        else:
            key = (ratio or "1:1").strip()
            if key == "origin":
                new_w, new_h, x, y = _even(sw), _even(sh), 0, 0
            else:
                if key not in RATIO_CROP:
                    raise ValueError("不支持的裁剪比例")
                rw, rh = RATIO_CROP[key]
                target = rw / rh
                src_aspect = sw / sh
                if src_aspect > target:
                    new_w = max(2, int(sh * rw / rh) // 2 * 2)
                    new_h = sh // 2 * 2
                    x = max(0, (sw - new_w) // 2)
                    y = 0
                else:
                    new_w = sw // 2 * 2
                    new_h = max(2, int(sw * rh / rw) // 2 * 2)
                    x = 0
                    y = max(0, (sh - new_h) // 2)

        out, out_url = _out_path(".mp4")
        await _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vf",
                f"crop={new_w}:{new_h}:{x}:{y}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        return {"url": out_url, "kind": "video", "width": new_w, "height": new_h}

    if op == "compress":
        sw, sh = await probe_size(src)
        if not sw or not sh:
            raise RuntimeError("无法读取视频尺寸")
        mode = (scale_mode or "smart").strip().lower()
        if mode == "smart":
            tw, th = _fit_scale(sw, sh, max_pixels=COMPRESS_MAX_PIXELS)
        elif mode in SCALE_PRESETS:
            tw, th = _fit_scale(sw, sh, max_h=SCALE_PRESETS[mode], max_pixels=COMPRESS_MAX_PIXELS)
        elif width and height:
            tw, th = _even(width), _even(height)
        else:
            tw, th = _fit_scale(sw, sh, max_pixels=COMPRESS_MAX_PIXELS)

        out_fps = float(fps) if fps and fps > 0 else 0
        vf_parts = [f"scale={tw}:{th}:flags=lanczos"]
        if out_fps > 0:
            vf_parts.append(f"fps={out_fps:.3f}")
        vf = ",".join(vf_parts)
        out, out_url = _out_path(".mp4")
        await _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        w, h = await probe_size(out)
        return {"url": out_url, "kind": "video", "width": w or tw, "height": h or th}

    if op == "reverse":
        out, out_url = _out_path(".mp4")
        try:
            await _run_ffmpeg(
                [
                    "-i",
                    str(src),
                    "-vf",
                    "reverse",
                    "-af",
                    "areverse",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(out),
                ]
            )
        except RuntimeError:
            if out.exists():
                out.unlink(missing_ok=True)
            await _run_ffmpeg(
                [
                    "-i",
                    str(src),
                    "-an",
                    "-vf",
                    "reverse",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "20",
                    "-movflags",
                    "+faststart",
                    str(out),
                ]
            )
        w, h = await probe_size(out)
        return {"url": out_url, "kind": "video", "width": w, "height": h}

    if op == "to_gif":
        fmt = (format or "gif").strip().lower()
        if fmt not in ("gif", "webp"):
            raise ValueError("格式仅支持 gif / webp")
        out_w = int(width or 480)
        out_w = max(64, min(1920, out_w))
        out_fps = float(fps or 10)
        out_fps = max(4.0, min(30.0, out_fps))
        colors = GIF_QUALITY.get((quality or "standard").strip().lower(), 128)

        s = max(0.0, float(start or 0))
        if end is not None:
            e = max(s + 0.1, float(end))
            duration = e - s
        else:
            duration = 5.0

        sw, sh = await probe_size(src)
        out_h = None
        if sw and sh:
            out_h = _even(int(round(sh * out_w / sw)))
            out_w = _even(out_w)

        if fmt == "webp":
            out, out_url = _out_path(".webp")
            vf = f"fps={out_fps:.3f},scale={out_w}:-1:flags=lanczos"
            args = [
                "-ss",
                f"{s:.3f}",
                "-i",
                str(src),
                "-t",
                f"{duration:.3f}",
                "-vf",
                vf,
                "-loop",
                "0",
                "-an",
                str(out),
            ]
            await _run_ffmpeg(args)
            return {
                "url": out_url,
                "kind": "image",
                "width": out_w,
                "height": out_h,
            }

        out, out_url = _out_path(".gif")
        vf = (
            f"fps={out_fps:.3f},scale={out_w}:-1:flags=lanczos,split[s0][s1];"
            f"[s0]palettegen=max_colors={colors}[p];[s1][p]paletteuse"
        )
        await _run_ffmpeg(
            [
                "-ss",
                f"{s:.3f}",
                "-i",
                str(src),
                "-t",
                f"{duration:.3f}",
                "-vf",
                vf,
                "-loop",
                "0",
                str(out),
            ]
        )
        return {
            "url": out_url,
            "kind": "image",
            "width": out_w,
            "height": out_h,
        }

    raise ValueError(f"未实现: {op}")
