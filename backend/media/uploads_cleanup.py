"""Upload cleanup — board-scoped assets are deleted with the board; only legacy flat files are purged here."""

from __future__ import annotations

import logging

from backend.board_assets import (
    _asset_in_purge_grace,
    delete_unreferenced_legacy_files,
    referenced_upload_refs,
)
from backend.media.asset_trash import unlink_or_trash

logger = logging.getLogger(__name__)

# Legacy flat pool soft cap (board assets are NOT subject to this).
MAX_UPLOAD_FILES = 500


def referenced_upload_names(boards: list | None = None) -> set[str]:
    if boards is not None:
        from backend.board_assets import collect_board_refs

        names: set[str] = set()
        for board in boards:
            names |= collect_board_refs(board)
        return names
    return referenced_upload_refs()


def cleanup_uploads(
    *,
    keep_extra: set[str] | None = None,
    max_files: int = MAX_UPLOAD_FILES,
) -> int:
    """Purge unreferenced legacy flat files; board assets are managed per-board."""
    from backend.config import UPLOADS_DIR

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    keep = referenced_upload_names()
    if keep_extra:
        keep |= {n for n in keep_extra if n}

    files = [p for p in UPLOADS_DIR.iterdir() if p.is_file() and not p.name.startswith(".")]
    if len(files) <= max_files:
        return delete_unreferenced_legacy_files(keep_extra=keep_extra)

    protected = [p for p in files if p.name in keep]
    disposable = sorted(
        (p for p in files if p.name not in keep and not _asset_in_purge_grace(p)),
        key=lambda p: p.stat().st_mtime,
    )
    budget = max(0, max_files - len(protected))
    to_delete = disposable[:-budget] if budget else disposable
    deleted = 0
    for path in to_delete:
        try:
            if not unlink_or_trash(path, slug="_legacy", board_id="_uploads"):
                continue
            deleted += 1
        except OSError as exc:
            logger.debug("cleanup skip %s: %s", path.name, exc)
    if deleted:
        logger.info("legacy uploads cleanup: moved %d file(s) to trash", deleted)
    return deleted


def purge_orphan_uploads(*, keep_extra: set[str] | None = None) -> dict:
    from backend.board_assets import purge_orphan_board_assets, scan_orphan_upload_stats
    from backend.media.asset_trash import purge_expired_trash

    trash_expired = purge_expired_trash()
    before = scan_orphan_upload_stats()
    board_deleted = purge_orphan_board_assets(keep_extra=keep_extra)
    legacy_deleted = delete_unreferenced_legacy_files(keep_extra=keep_extra)
    stats = scan_orphan_upload_stats()
    return {
        "ok": True,
        "deleted": board_deleted + legacy_deleted,
        "board_deleted": board_deleted,
        "legacy_deleted": legacy_deleted,
        "trash_expired": trash_expired.get("deleted", 0),
        "trash_dir": "data/trash",
        "orphan_files_before": before.get("orphan_files"),
        **stats,
    }
