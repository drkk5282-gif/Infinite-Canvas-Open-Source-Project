"""Soft-delete vault for purged board / upload assets.

Orphan cleanup moves files into data/trash/ instead of unlinking.
Entries older than TRASH_TTL_DAYS are removed permanently (startup + on move).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import DATA_DIR

logger = logging.getLogger(__name__)

TRASH_TTL_DAYS = 7
TRASH_ROOT = DATA_DIR / "trash"
_README_NAME = "回收站说明.txt"
_META_SUFFIX = ".trashmeta.json"


def trash_root() -> Path:
    return TRASH_ROOT


def ensure_trash_layout() -> Path:
    TRASH_ROOT.mkdir(parents=True, exist_ok=True)
    readme = TRASH_ROOT / _README_NAME
    if not readme.is_file():
        readme.write_text(
            "素材回收站\n"
            "============\n"
            "\n"
            "画布清理「无人引用」的本地文件时，不会立刻永久删除，\n"
            "而是先移到本目录（按账号 / 画板分子文件夹）。\n"
            "\n"
            f"保留时间：{TRASH_TTL_DAYS} 天。超时后由程序自动永久删除。\n"
            "\n"
            "若误删后节点打不开：\n"
            "1. 在本目录按时间找到对应文件；\n"
            "2. 复制回原画板 assets 文件夹：\n"
            "   data/users/<账号>/<画板ID>/assets/\n"
            "3. 文件名需与节点里的 URL 尾部一致（可看同目录的 .trashmeta.json）。\n"
            "\n"
            f"本说明生成于 {datetime.now().isoformat(timespec='seconds')}\n",
            encoding="utf-8",
        )
    return TRASH_ROOT


def _safe_segment(value: str, fallback: str) -> str:
    raw = (value or "").strip() or fallback
    out = []
    for ch in raw:
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        else:
            out.append("_")
    text = "".join(out).strip("._") or fallback
    return text[:80]


def move_to_trash(
    src: Path,
    *,
    slug: str = "_unknown",
    board_id: str = "_legacy",
) -> Path | None:
    """Move a file into the trash vault. Returns destination path, or None on failure."""
    if not src or not src.is_file():
        return None
    ensure_trash_layout()
    slug_s = _safe_segment(slug, "_unknown")
    board_s = _safe_segment(board_id, "_legacy")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest_dir = TRASH_ROOT / slug_s / board_s
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{stamp}_{uuid.uuid4().hex[:8]}_{src.name}"
    dest = dest_dir / dest_name
    try:
        shutil.move(str(src), str(dest))
        # Trash retention starts now, not at the source file's old modification time.
        dest.touch()
    except OSError as exc:
        logger.warning("trash move failed %s → %s: %s", src, dest, exc)
        return None
    meta = {
        "original_name": src.name,
        "original_path": str(src),
        "slug": slug_s,
        "board_id": board_s,
        "moved_at": datetime.now(timezone.utc).isoformat(),
        "ttl_days": TRASH_TTL_DAYS,
    }
    try:
        (dest_dir / f"{dest_name}{_META_SUFFIX}").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return dest


def unlink_or_trash(
    path: Path,
    *,
    slug: str = "_unknown",
    board_id: str = "_legacy",
) -> bool:
    """Move a file to trash without ever falling back to permanent deletion."""
    if move_to_trash(path, slug=slug, board_id=board_id):
        return True
    logger.warning("asset cleanup kept file because trash move failed: %s", path)
    return False


def _entry_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def purge_expired_trash(*, max_age_days: int = TRASH_TTL_DAYS) -> dict[str, int]:
    """Permanently remove trash files older than max_age_days."""
    ensure_trash_layout()
    max_age = max(1, int(max_age_days)) * 86400
    deleted = 0
    scanned = 0
    if not TRASH_ROOT.is_dir():
        return {"deleted": 0, "scanned": 0}

    for path in TRASH_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.name == _README_NAME:
            continue
        scanned += 1
        age = _entry_age_seconds(path)
        if age is None or age < max_age:
            continue
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError as exc:
            logger.debug("trash expire skip %s: %s", path, exc)

    # Drop empty date/board folders (keep trash root + readme).
    for path in sorted(TRASH_ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir() or path == TRASH_ROOT:
            continue
        try:
            if any(path.iterdir()):
                continue
            path.rmdir()
        except OSError:
            pass

    if deleted:
        logger.info(
            "trash TTL purge: deleted=%d scanned=%d max_age_days=%d",
            deleted,
            scanned,
            max_age_days,
        )
    return {"deleted": deleted, "scanned": scanned}
