"""Local (non-AI) image utilities via Pillow."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from backend.board_assets import alloc_asset_path
from backend.config import BASE_DIR
from backend.media.video_tools import RATIO_CROP, ensure_local_video

logger = logging.getLogger(__name__)

IMAGE_OPS = frozenset(
    {
        "image_crop",
        "image_compress",
        "image_mirror",
        "image_rotate",
        "image_ai_mark",
    }
)

AI_MARK_WATERMARK_PATH = BASE_DIR / "frontend" / "assets" / "ai-mark-watermark.png"

# Max pixels for smart compress (~2MP)
IMAGE_COMPRESS_MAX_PIXELS = 2_088_876
IMAGE_SCALE_PRESETS = {
    "2048": 2048,
    "1080": 1080,
    "720": 720,
    "540": 540,
}


def _out_path(ext: str) -> tuple[Path, str]:
    return alloc_asset_path(ext)


def _fit_scale(sw: int, sh: int, *, max_edge: int | None = None, max_pixels: int | None = None) -> tuple[int, int]:
    w, h = sw, sh
    if max_edge and max(w, h) > max_edge:
        if w >= h:
            h = max(1, round(h * max_edge / w))
            w = max_edge
        else:
            w = max(1, round(w * max_edge / h))
            h = max_edge
    if max_pixels and w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        w = max(1, round(w * scale))
        h = max(1, round(h * scale))
    return w, h


def _save_rgb(img: Image.Image, path: Path, *, quality: int = 92) -> None:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        out = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        out.save(path, format="JPEG", quality=quality, optimize=True)
    elif ext == ".webp":
        img.save(path, format="WEBP", quality=quality, method=4)
    else:
        if img.mode == "P":
            img = img.convert("RGBA")
        # optimize=True on large PNGs can take several seconds with little benefit.
        img.save(path, format="PNG", optimize=False, compress_level=3)


def _load_image(path: Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img


def _watermark_target_width(img_w: int, img_h: int) -> int:
    """Scale badge to ~34% of image width, clamped for legibility."""
    target = round(img_w * 0.34)
    min_w = max(112, round(img_w * 0.22))
    max_w = round(img_w * 0.46)
    return max(min_w, min(max_w, target))


def _watermark_padding(sw: int, sh: int) -> int:
    return max(10, min(40, round(min(sw, sh) * 0.028)))


def _watermark_has_alpha(mark: Image.Image) -> bool:
    if mark.mode not in ("RGBA", "LA"):
        return False
    alpha = mark.getchannel("A")
    lo, hi = alpha.getextrema()
    return lo < 250


def _prepare_watermark_mark(mark: Image.Image) -> Image.Image:
    """Keep PNG alpha; for RGB assets preserve glyphs and nearby dark stroke."""
    rgba = mark.convert("RGBA")
    if _watermark_has_alpha(rgba):
        return rgba
    return _rgb_watermark_to_rgba(rgba)


def _rgb_watermark_to_rgba(rgba: Image.Image) -> Image.Image:
    """Turn RGB watermark mats into transparency without eating black text outlines."""
    w, h = rgba.size
    px = rgba.load()
    stroke_radius = 5
    keep: set[tuple[int, int]] = set()
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if r > 160 and g > 160 and b > 160:
                for dy in range(-stroke_radius, stroke_radius + 1):
                    for dx in range(-stroke_radius, stroke_radius + 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            keep.add((nx, ny))
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if (x, y) in keep:
                px[x, y] = (r, g, b, 255)
            else:
                px[x, y] = (r, g, b, 0)
    return rgba


def _apply_ai_watermark(img: Image.Image, mark: Image.Image) -> Image.Image:
    """Composite the transparent AI badge onto the bottom-right corner."""
    sw, sh = img.size
    mark_rgba = _prepare_watermark_mark(mark)
    mw, mh = mark_rgba.size
    tw = _watermark_target_width(sw, sh)
    th = max(1, round(mh * tw / mw))
    mark_resized = mark_rgba.resize((tw, th), Image.Resampling.LANCZOS)
    pad = _watermark_padding(sw, sh)

    base = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    base.alpha_composite(mark_resized, (sw - tw - pad, sh - th - pad))
    return base


async def run_image_tool(
    op: str,
    url: str,
    *,
    ratio: str | None = None,
    scale_mode: str | None = None,
    quality: str | None = None,
    direction: str | None = None,
    degrees: int | None = None,
    crop_x: int | None = None,
    crop_y: int | None = None,
    crop_w: int | None = None,
    crop_h: int | None = None,
) -> dict:
    if op not in IMAGE_OPS:
        raise ValueError(f"不支持的图片工具：{op}")

    # Reuse download/cache helper (works for any uploads URL)
    src = await ensure_local_video(url)
    img = await asyncio.to_thread(_load_image, src)
    sw, sh = img.size

    if op == "image_mirror":
        d = (direction or "h").strip().lower()
        if d in ("h", "horizontal", "x"):
            out_img = ImageOps.mirror(img)
            label = "水平镜像"
        elif d in ("v", "vertical", "y"):
            out_img = ImageOps.flip(img)
            label = "垂直镜像"
        else:
            raise ValueError("direction 应为 h 或 v")
        out, out_url = _out_path(".png" if img.mode in ("RGBA", "P") else ".jpg")
        await asyncio.to_thread(_save_rgb, out_img, out)
        return {
            "url": out_url,
            "kind": "image",
            "width": out_img.width,
            "height": out_img.height,
            "label": label,
        }

    if op == "image_rotate":
        deg = int(degrees if degrees is not None else 90)
        if deg not in (90, 180, 270, -90):
            raise ValueError("仅支持旋转 90 / 180 / 270 度")
        # PIL rotate is counter-clockwise; UI "顺时针 90" → -90
        out_img = img.rotate(-deg if deg > 0 else deg, expand=True)
        if deg == -90:
            out_img = img.rotate(90, expand=True)
        out, out_url = _out_path(".png" if img.mode in ("RGBA", "P") else ".jpg")
        await asyncio.to_thread(_save_rgb, out_img, out)
        return {
            "url": out_url,
            "kind": "image",
            "width": out_img.width,
            "height": out_img.height,
            "label": f"旋转 {abs(deg)}°",
        }

    if op == "image_compress":
        mode = (scale_mode or "smart").strip().lower()
        q = 85
        if quality:
            try:
                q = max(40, min(95, int(float(quality))))
            except (TypeError, ValueError):
                q = 85
        if mode == "smart":
            tw, th = _fit_scale(sw, sh, max_pixels=IMAGE_COMPRESS_MAX_PIXELS)
        elif mode in IMAGE_SCALE_PRESETS:
            tw, th = _fit_scale(sw, sh, max_edge=IMAGE_SCALE_PRESETS[mode], max_pixels=IMAGE_COMPRESS_MAX_PIXELS)
        else:
            tw, th = _fit_scale(sw, sh, max_pixels=IMAGE_COMPRESS_MAX_PIXELS)
        out_img = img if (tw, th) == (sw, sh) else img.resize((tw, th), Image.Resampling.LANCZOS)
        out, out_url = _out_path(".jpg")
        await asyncio.to_thread(_save_rgb, out_img.convert("RGB"), out, quality=q)
        return {
            "url": out_url,
            "kind": "image",
            "width": out_img.width,
            "height": out_img.height,
            "label": "图片压缩",
        }

    if op == "image_ai_mark":
        if not AI_MARK_WATERMARK_PATH.is_file():
            raise FileNotFoundError(f"AI 标记水印文件不存在：{AI_MARK_WATERMARK_PATH}")
        mark = await asyncio.to_thread(_load_image, AI_MARK_WATERMARK_PATH)
        out_img = await asyncio.to_thread(_apply_ai_watermark, img, mark)
        src_ext = src.suffix.lower()
        use_png = src_ext == ".png" or img.mode in ("RGBA", "P")
        out, out_url = _out_path(".png" if use_png else ".jpg")
        if use_png:
            await asyncio.to_thread(_save_rgb, out_img, out)
        else:
            await asyncio.to_thread(_save_rgb, out_img.convert("RGB"), out)
        return {
            "url": out_url,
            "kind": "image",
            "width": out_img.width,
            "height": out_img.height,
            "label": "AI标记",
        }

    if op == "image_crop":
        if crop_w and crop_h and crop_x is not None and crop_y is not None:
            new_w = max(1, int(crop_w))
            new_h = max(1, int(crop_h))
            x = max(0, min(int(crop_x), max(0, sw - 1)))
            y = max(0, min(int(crop_y), max(0, sh - 1)))
            new_w = min(new_w, sw - x)
            new_h = min(new_h, sh - y)
        else:
            key = (ratio or "1:1").strip()
            if key == "origin":
                new_w, new_h, x, y = sw, sh, 0, 0
            else:
                if key not in RATIO_CROP:
                    raise ValueError("不支持的裁剪比例")
                rw, rh = RATIO_CROP[key]
                target = rw / rh
                src_aspect = sw / sh
                if src_aspect > target:
                    new_h = sh
                    new_w = max(1, int(sh * rw / rh))
                    x = max(0, (sw - new_w) // 2)
                    y = 0
                else:
                    new_w = sw
                    new_h = max(1, int(sw * rh / rw))
                    x = 0
                    y = max(0, (sh - new_h) // 2)
        box = (x, y, x + new_w, y + new_h)
        out_img = img.crop(box)
        out, out_url = _out_path(".png" if img.mode in ("RGBA", "P") else ".jpg")
        await asyncio.to_thread(_save_rgb, out_img, out)
        return {
            "url": out_url,
            "kind": "image",
            "width": out_img.width,
            "height": out_img.height,
            "label": "图片裁剪",
        }

    raise ValueError(f"未实现：{op}")
