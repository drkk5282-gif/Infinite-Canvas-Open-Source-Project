"""Convert Windows 11 concept cursor .cur (1bpp XOR+AND) to web PNG + manifest."""
from __future__ import annotations

import io
import json
import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "frontend" / "cursors" / "win11-v2"
OUT_DIR = ROOT / "frontend" / "cursors" / "win11"

# Prefer 32px art for web (96px looks huge in browser).
PREFERRED_SIZES = (32, 48, 64, 96)

MAP = {
    "default": "pointer",
    "pointer": "link",
    "grab": "move",
    "grabbing": "move",
    "crosshair": "precision",
    "not-allowed": "unavailable",
    "text": "beam",
    "ew-resize": "horz",
    "ns-resize": "vert",
    "nwse-resize": "dgn1",
    "nesw-resize": "dgn2",
}


def read_1bpp_rows(data: bytes, offset: int, width: int, height: int, row_bytes: int) -> list[list[int]]:
    rows: list[list[int]] = []
    for y in range(height):
        row: list[int] = []
        base = offset + y * row_bytes
        for x in range(width):
            bit = 7 - (x % 8)
            row.append((data[base + x // 8] >> bit) & 1)
        rows.append(row)
    return rows


def cursor_pixel(xor_bit: int, and_bit: int) -> tuple[int, int, int, int]:
    """Map Windows XOR/AND to pure-white cursor on transparent bg."""
    if and_bit == 1 and xor_bit == 0:
        return (0, 0, 0, 0)
    # Visible stroke/fill → pure white (user asked for 纯白 on dark canvas).
    return (255, 255, 255, 255)


def parse_bgra_dib(data: bytes, offset: int, size: int, ent: dict) -> tuple[Image.Image, int, int]:
    bi_size, bi_width, bi_height, _planes, bi_bit_count = struct.unpack_from("<IiIHH", data, offset)
    width = bi_width
    xor_height = bi_height // 2

    if bi_bit_count == 32:
        row_bytes = width * 4
        xor_off = offset + bi_size
        im = Image.new("RGBA", (width, xor_height), (0, 0, 0, 0))
        px = im.load()
        for y in range(xor_height):
            src_y = xor_height - 1 - y
            row_base = xor_off + src_y * row_bytes
            for x in range(width):
                b, g, r, a = struct.unpack_from("BBBB", data, row_base + x * 4)
                if a == 0:
                    px[x, y] = (0, 0, 0, 0)
                else:
                    # Normalize to pure white stroke on dark canvas.
                    px[x, y] = (255, 255, 255, a)
        return im, ent["xhot"], ent["yhot"]

    if bi_bit_count == 1:
        row_bytes = ((width + 31) // 32) * 4
        xor_off = offset + bi_size
        and_off = xor_off + row_bytes * xor_height
        xor_rows = read_1bpp_rows(data, xor_off, width, xor_height, row_bytes)
        and_rows = read_1bpp_rows(data, and_off, width, xor_height, row_bytes)
        im = Image.new("RGBA", (width, xor_height), (0, 0, 0, 0))
        px = im.load()
        for y in range(xor_height):
            src_y = xor_height - 1 - y
            for x in range(width):
                px[x, y] = cursor_pixel(xor_rows[src_y][x], and_rows[src_y][x])
        return im, ent["xhot"], ent["yhot"]

    img_data = data[offset : offset + size]
    im = Image.open(io.BytesIO(img_data)).convert("RGBA")
    return im, ent["xhot"], ent["yhot"]


def parse_cur_entry(data: bytes, ent: dict) -> Image.Image:
    im, _x, _y = parse_bgra_dib(data, ent["offset"], ent["size"], ent)
    return im


def parse_cur(path: Path) -> tuple[Image.Image, int, int]:
    data = path.read_bytes()
    if data[:2] != b"\x00\x00" or data[2:4] not in (b"\x02\x00", b"\x01\x00"):
        raise ValueError(f"Unsupported cursor format: {path}")
    count = struct.unpack_from("<H", data, 4)[0]
    entries = []
    offset = 6
    for _ in range(count):
        w, h, _colors, _reserved, xhot, yhot, size, imgoff = struct.unpack_from(
            "<BBBBHHII", data, offset
        )
        entries.append(
            {
                "width": 256 if w == 0 else w,
                "height": 256 if h == 0 else h,
                "xhot": xhot,
                "yhot": yhot,
                "size": size,
                "offset": imgoff,
            }
        )
        offset += 16

    for prefer in PREFERRED_SIZES:
        match = next((e for e in entries if e["width"] == prefer), None)
        if match:
            ent = match
            break
    else:
        ent = max(entries, key=lambda e: e["width"] * e["height"])

    return parse_cur_entry(data, ent), ent["xhot"], ent["yhot"]


def find_dark_default_base() -> Path:
    for base in sorted(SRC_ROOT.rglob("base")):
        rel = base.relative_to(SRC_ROOT).parts
        if len(rel) >= 4 and rel[1] == "深色" and rel[2] == "默认":
            return base
    bases = sorted({p.parent for p in SRC_ROOT.rglob("pointer.cur") if p.parent.name == "base"})
    if len(bases) >= 4:
        return bases[3]
    raise FileNotFoundError("Dark/default/base cursor folder not found")


def main() -> None:
    base = find_dark_default_base()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, int | str]] = {}

    for out_name, src_name in MAP.items():
        src = base / f"{src_name}.cur"
        if not src.exists():
            print(f"skip missing {src_name}")
            continue
        im, xhot, yhot = parse_cur(src)
        out_png = OUT_DIR / f"{out_name}.png"
        im.save(out_png, optimize=True)
        manifest[out_name] = {"file": out_png.name, "x": xhot, "y": yhot, "w": im.width, "h": im.height}
        print(f"{out_name} <- {src_name}.cur  {im.size}  hotspot {xhot},{yhot}")

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {len(manifest)} cursors to {OUT_DIR}")


if __name__ == "__main__":
    main()
