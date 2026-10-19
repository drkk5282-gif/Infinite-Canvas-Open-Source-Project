"""Per-board asset storage under data/users/{slug}/{board_id}/assets/."""

from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable

from backend.config import DATA_DIR, UPLOADS_DIR
from backend.user_scope import DEFAULT_USER_ID, get_current_user_id, storage_slug, user_data_root

logger = logging.getLogger(__name__)

_board_id: ContextVar[str | None] = ContextVar("active_board_id", default=None)
_workbench_id: ContextVar[str | None] = ContextVar("active_workbench_id", default=None)

_NESTED_UPLOAD_RE = re.compile(r"/uploads/([^/]+)/([^/]+)/([^/?#]+)", re.I)
_WORKBENCH_UPLOAD_RE = re.compile(r"/uploads/([^/]+)/workbenches/([^/]+)/([^/?#]+)", re.I)
_FLAT_UPLOAD_RE = re.compile(r"/uploads/([^/?#]+)", re.I)

_MEDIA_KEYS = ("imageUrl", "videoUrl", "audioUrl", "coverUrl", "thumbUrl", "aiMarkBaseUrl")
_USER_DIR_SKIP = frozenset(
    {"boards", "accounts", "canvas.json", "tasks.json", "accounts.json", "service_config.json", "myapi_models.json"}
)


def set_board_id(board_id: str | None) -> object:
    return _board_id.set((board_id or "").strip() or None)


def reset_board_id(token: object) -> None:
    _board_id.reset(token)


def set_workbench_id(workbench_id: str | None) -> object:
    return _workbench_id.set((workbench_id or "").strip() or None)


def reset_workbench_id(token: object) -> None:
    _workbench_id.reset(token)


def get_workbench_id() -> str | None:
    return _workbench_id.get()


def get_board_id() -> str | None:
    return _board_id.get()


def board_assets_dir(user_id: str | None, board_id: str) -> Path:
    uid = (user_id or get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    bid = (board_id or "").strip()
    return (user_data_root(uid) / bid / "assets").resolve()


def board_root_dir(user_id: str | None, board_id: str) -> Path:
    uid = (user_id or get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    bid = (board_id or "").strip()
    return (user_data_root(uid) / bid).resolve()


def legacy_board_assets_dir(user_id: str | None, board_id: str) -> Path:
    uid = (user_id or get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    bid = (board_id or "").strip()
    return (user_data_root(uid) / "boards" / bid / "assets").resolve()


def board_json_path(user_id: str | None, board_id: str) -> Path:
    return board_root_dir(user_id, board_id) / "board.json"


def public_url(user_id: str | None, board_id: str, filename: str) -> str:
    slug = storage_slug(user_id)
    return f"/uploads/{slug}/{board_id}/{filename}"


def alloc_asset_path(ext: str, *, user_id: str | None = None, board_id: str | None = None) -> tuple[Path, str]:
    """Return (path, public_url). Uses the board folder when id is known."""
    uid = (user_id or get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    bid = (board_id or get_board_id() or "").strip()
    ext = ext if ext.startswith(".") else f".{ext}"
    name = f"{uuid.uuid4().hex}{ext}"
    if bid:
        dest_dir = board_assets_dir(uid, bid)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        return path, public_url(uid, bid, name)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOADS_DIR / name
    return path, f"/uploads/{name}"


def write_bytes(
    data: bytes,
    ext: str,
    *,
    user_id: str | None = None,
    board_id: str | None = None,
) -> dict[str, Any]:
    path, url = alloc_asset_path(ext, user_id=user_id, board_id=board_id)
    path.write_bytes(data)
    return {"url": url, "path": path, "filename": path.name}


def resolve_upload_path(url: str) -> Path | None:
    raw = (url or "").strip()
    if not raw:
        return None
    m = _WORKBENCH_UPLOAD_RE.search(raw)
    if m:
        slug, workbench_id, filename = m.group(1), m.group(2), m.group(3)
        if ".." in filename or "/" in filename or "\\" in filename:
            return None
        path = (
            DATA_DIR / "users" / slug / "workbenches" / workbench_id / "assets" / filename
        ).resolve()
        if path.is_file():
            return path
        found = find_asset_by_filename(filename)
        if found:
            return found
        return None
    m = _NESTED_UPLOAD_RE.search(raw)
    if m:
        slug, board_id, filename = m.group(1), m.group(2), m.group(3)
        if board_id == "workbenches":
            return None
        if ".." in filename or "/" in filename or "\\" in filename:
            return None
        path = (DATA_DIR / "users" / slug / board_id / "assets" / filename).resolve()
        if path.is_file():
            return path
        legacy = (DATA_DIR / "users" / slug / "boards" / board_id / "assets" / filename).resolve()
        if legacy.is_file():
            return legacy
    m = _FLAT_UPLOAD_RE.search(raw)
    if m:
        name = m.group(1)
        if not name or ".." in name or "/" in name or "\\" in name:
            return None
        # Nested board path: /uploads/slug/boardId/file.ext
        if "/" in name:
            return None
        path = (UPLOADS_DIR / name).resolve()
        if path.is_file():
            return path
        found = find_asset_by_filename(name)
        if found:
            return found
    return None


_BOARDS_SLUG_BUG_RE = re.compile(r"/uploads/boards/([^/]+)/([^/?#]+)", re.I)


def _public_url_from_asset_path(path: Path) -> str | None:
    parts = path.parts
    try:
        assets_idx = parts.index("assets")
    except ValueError:
        return None
    if assets_idx < 2:
        return None
    parent = parts[assets_idx - 1]
    grand = parts[assets_idx - 2] if assets_idx >= 2 else ""
    if grand == "workbenches" and assets_idx >= 3:
        slug = parts[assets_idx - 3]
        workbench_id = parent
        return f"/uploads/{slug}/workbenches/{workbench_id}/{path.name}"
    board_id = parent
    slug = parts[assets_idx - 3] if grand == "boards" else grand
    return public_url(slug, board_id, path.name)


def find_asset_by_filename(filename: str) -> Path | None:
    """Locate a board/workbench asset when canvas still references legacy flat /uploads/{name}.

    Prefer nested user paths over the flat uploads pool so ownership checks and URL
    repair keep working when both a flat leftover and the real nested file exist.
    """
    name = (filename or "").strip()
    if not name or ".." in name or "/" in name or "\\" in name:
        return None
    users_root = DATA_DIR / "users"
    if users_root.is_dir():
        for user_dir in users_root.iterdir():
            if not user_dir.is_dir():
                continue
            # Workbench layout: users/{slug}/workbenches/{workbench_id}/assets/
            wb_root = user_dir / "workbenches"
            if wb_root.is_dir():
                for wb_dir in wb_root.iterdir():
                    if not wb_dir.is_dir():
                        continue
                    candidate = (wb_dir / "assets" / name).resolve()
                    if candidate.is_file():
                        return candidate
            # New layout: users/{slug}/{board_id}/assets/
            for child in user_dir.iterdir():
                if not child.is_dir() or child.name in _USER_DIR_SKIP or child.name == "workbenches":
                    continue
                candidate = (child / "assets" / name).resolve()
                if candidate.is_file():
                    return candidate
            # Legacy layout: users/{slug}/boards/{board_id}/assets/
            boards_root = user_dir / "boards"
            if boards_root.is_dir():
                for board_dir in boards_root.iterdir():
                    if not board_dir.is_dir():
                        continue
                    candidate = (board_dir / "assets" / name).resolve()
                    if candidate.is_file():
                        return candidate
    flat = (UPLOADS_DIR / name).resolve()
    if flat.is_file():
        return flat
    return None


def repair_upload_url(url: str) -> str:
    """Rewrite broken or legacy upload URLs to /uploads/{slug}/{boardId}/{file}."""
    raw = (url or "").strip()
    if not raw:
        return url
    m = _BOARDS_SLUG_BUG_RE.search(raw)
    if m:
        filename = m.group(2)
        found = find_asset_by_filename(filename)
        if found:
            fixed = _public_url_from_asset_path(found)
            if fixed:
                return fixed
    return repair_flat_upload_url(raw)


def repair_flat_upload_url(url: str) -> str:
    """Rewrite stale flat /uploads/{file} URLs to nested paths when the file moved."""
    raw = (url or "").strip()
    if not _is_flat_upload_url(raw):
        return url
    m = _FLAT_UPLOAD_RE.search(raw)
    if not m:
        return url
    name = m.group(1)
    found = find_asset_by_filename(name)
    if not found:
        return url
    fixed = _public_url_from_asset_path(found)
    return fixed or url


def repair_media_dict(data: dict) -> bool:
    changed = False
    for key in _MEDIA_KEYS:
        val = str(data.get(key) or "")
        if not val:
            continue
        fixed = repair_upload_url(val)
        if fixed != val:
            data[key] = fixed
            changed = True
    return changed


def repair_board_media(board: dict) -> bool:
    changed = False
    for node in board.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if repair_media_dict(node):
            changed = True
        for entry in node.get("genHistory") or []:
            if isinstance(entry, dict) and repair_media_dict(entry):
                changed = True
    return changed


def upload_ref_from_url(url: str) -> str | None:
    """Normalized ref key for dedup / cleanup (full /uploads/... path tail)."""
    raw = (url or "").strip()
    m = _WORKBENCH_UPLOAD_RE.search(raw)
    if m:
        return f"{m.group(1)}/workbenches/{m.group(2)}/{m.group(3)}"
    m = _NESTED_UPLOAD_RE.search(raw)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    m = _FLAT_UPLOAD_RE.search(raw)
    if m:
        return m.group(1)
    return None


def _collect_history_entry_refs(entry: dict, out: set[str]) -> None:
    if not isinstance(entry, dict):
        return
    for key in _MEDIA_KEYS:
        ref = upload_ref_from_url(str(entry.get(key) or ""))
        if ref:
            out.add(ref)
    for ref_item in entry.get("references") or []:
        if not isinstance(ref_item, dict):
            continue
        ref = upload_ref_from_url(str(ref_item.get("url") or ""))
        if ref:
            out.add(ref)


def _collect_node_live_refs(node: dict, out: set[str]) -> None:
    if not isinstance(node, dict):
        return
    for key in _MEDIA_KEYS:
        ref = upload_ref_from_url(str(node.get(key) or ""))
        if ref:
            out.add(ref)


def _collect_node_refs(node: dict, out: set[str]) -> None:
    """All media URLs on a node: current fields + genHistory (+ history reference inputs)."""
    if not isinstance(node, dict):
        return
    _collect_node_live_refs(node, out)
    for entry in node.get("genHistory") or []:
        _collect_history_entry_refs(entry, out)


def collect_board_live_refs(board: dict) -> set[str]:
    refs: set[str] = set()
    for node in board.get("nodes") or []:
        _collect_node_live_refs(node, refs)
    return refs


def collect_board_history_refs(board: dict) -> set[str]:
    refs: set[str] = set()
    for node in board.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for entry in node.get("genHistory") or []:
            _collect_history_entry_refs(entry, refs)
    return refs


def collect_board_refs(board: dict) -> set[str]:
    refs: set[str] = set()
    for node in board.get("nodes") or []:
        _collect_node_refs(node, refs)
    return refs


def _expand_upload_refs(refs: set[str]) -> set[str]:
    expanded: set[str] = set(refs)
    for ref in refs:
        if "/" not in ref:
            expanded.add(ref)
        else:
            expanded.add(ref.rsplit("/", 1)[-1])
    return expanded


def _is_derivative_asset_filename(filename: str) -> bool:
    stem = Path(filename).stem
    return stem.endswith("_thumb") or stem.endswith("_poster")


def _board_has_content(board: dict) -> bool:
    if not board:
        return False
    if board.get("nodes") or board.get("edges") or board.get("groups"):
        return True
    keys = set(board.keys())
    index_only = {"id", "name", "pinned", "coverNodeId"}
    return not keys.issubset(index_only)


def _load_board_json(path: Path) -> dict | None:
    from backend.json_io import load_json

    if not path.is_file():
        return None
    data = load_json(path, default=None)
    return data if isinstance(data, dict) else None


def _load_board_json_with_backup(path: Path) -> dict | None:
    """Load board.json, falling back to board.json.bak when the primary file is missing or empty."""
    data = _load_board_json(path)
    if data and _board_has_content(data):
        return data
    backup_path = path.with_name(f"{path.stem}.json.bak")
    backup = _load_board_json(backup_path)
    if backup and _board_has_content(backup):
        if not (data and _board_has_content(data)):
            logger.warning("using board backup for refs: %s", path.parent.name[:8])
        return backup
    return data if isinstance(data, dict) else None


def _board_keep_filenames(keep: set[str], *, slug: str, board_id: str) -> set[str]:
    """Filenames in keep that belong to this board folder."""
    prefix = f"{slug}/{board_id}/"
    names: set[str] = set()
    for ref in keep:
        if ref.startswith(prefix):
            names.add(ref.rsplit("/", 1)[-1])
        elif "/" not in ref:
            names.add(ref)
    return names


def _refs_from_tasks_file(path: Path) -> set[str]:
    from backend.json_io import load_json

    refs: set[str] = set()
    if not path.is_file():
        return refs
    raw = load_json(path, default=[])
    if not isinstance(raw, list):
        return refs
    for task in raw:
        if not isinstance(task, dict):
            continue
        for key in ("image_url", "video_url", "cover_url", "thumb_url"):
            ref = upload_ref_from_url(str(task.get(key) or ""))
            if ref:
                refs.add(ref)
    return refs


def _refs_from_workbench_json_files() -> set[str]:
    return set()
    from backend.json_io import load_json

    refs: set[str] = set()
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return refs
    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        wb_root = user_dir / "workbenches"
        if not wb_root.is_dir():
            continue
        for wb_dir in wb_root.iterdir():
            if not wb_dir.is_dir():
                continue
            path = wb_dir / "workbench.json"
            if not path.is_file():
                continue
            data = load_json(path, default={})
            if not isinstance(data, dict):
                continue
            for url in workbench_assets.collect_workbench_refs(data):
                ref = upload_ref_from_url(str(url))
                if ref:
                    refs.add(ref)
    return refs


def _refs_from_workbench_dirs() -> set[str]:
    refs: set[str] = set()
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return refs
    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        wb_root = user_dir / "workbenches"
        if not wb_root.is_dir():
            continue
        for wb_dir in wb_root.iterdir():
            if not wb_dir.is_dir():
                continue
            assets_dir = wb_dir / "assets"
            if not assets_dir.is_dir():
                continue
            wid = wb_dir.name
            for asset in assets_dir.iterdir():
                if asset.is_file() and not asset.name.startswith("."):
                    refs.add(f"{slug}/workbenches/{wid}/{asset.name}")
    return refs


def _refs_from_asset_dirs() -> set[str]:
    refs: set[str] = set()
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return refs
    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        for child in user_dir.iterdir():
            if not child.is_dir() or child.name in _USER_DIR_SKIP or child.name == "workbenches":
                continue
            assets_dir = child / "assets"
            if not assets_dir.is_dir():
                continue
            bid = child.name
            for asset in assets_dir.iterdir():
                if asset.is_file() and not asset.name.startswith("."):
                    refs.add(f"{slug}/{bid}/{asset.name}")
        legacy_boards = user_dir / "boards"
        if legacy_boards.is_dir():
            for board_dir in legacy_boards.iterdir():
                if not board_dir.is_dir():
                    continue
                assets_dir = board_dir / "assets"
                if not assets_dir.is_dir():
                    continue
                bid = board_dir.name
                for asset in assets_dir.iterdir():
                    if asset.is_file() and not asset.name.startswith("."):
                        refs.add(f"{slug}/{bid}/{asset.name}")
    return refs


def iter_all_canvas_boards() -> Iterable[tuple[str, dict]]:
    """Yield (user_slug, board) from board.json files and legacy inline canvas data."""
    from backend.json_io import load_json

    seen_boards: set[tuple[str, str]] = set()

    def _yield_board(slug: str, board: dict) -> Iterable[tuple[str, dict]]:
        bid = str(board.get("id") or "").strip()
        key = (slug, bid)
        if bid and key in seen_boards:
            return
        if bid:
            seen_boards.add(key)
        yield slug, board

    legacy = DATA_DIR / "canvas.json"
    if legacy.is_file():
        data = load_json(legacy, default={})
        for board in data.get("boards") or []:
            if isinstance(board, dict) and _board_has_content(board):
                yield from _yield_board(DEFAULT_USER_ID, board)

    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return

    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        for child in user_dir.iterdir():
            if not child.is_dir() or child.name in _USER_DIR_SKIP:
                continue
            board = _load_board_json_with_backup(child / "board.json")
            if board and _board_has_content(board):
                yield from _yield_board(slug, board)

        canvas_path = user_dir / "canvas.json"
        if not canvas_path.is_file():
            continue
        data = load_json(canvas_path, default={})
        for board in data.get("boards") or []:
            if isinstance(board, dict) and _board_has_content(board):
                yield from _yield_board(slug, board)


def referenced_upload_refs() -> set[str]:
    refs: set[str] = set()
    for _slug, board in iter_all_canvas_boards():
        refs |= collect_board_refs(board)

    users_root = DATA_DIR / "users"
    if users_root.is_dir():
        for user_dir in users_root.iterdir():
            if user_dir.is_dir():
                refs |= _refs_from_tasks_file(user_dir / "tasks.json")
    refs |= _refs_from_tasks_file(DATA_DIR / "tasks.json")
    refs |= _refs_from_asset_dirs()
    refs |= _refs_from_workbench_dirs()
    refs |= _refs_from_workbench_json_files()

    flat_keep: set[str] = set()
    for ref in refs:
        if "/" not in ref:
            flat_keep.add(ref)
        else:
            flat_keep.add(ref.rsplit("/", 1)[-1])
    refs |= flat_keep
    return refs


def delete_board_assets(user_id: str, board_id: str) -> int:
    root = board_root_dir(user_id, board_id)
    if not root.exists():
        return 0
    count = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root, ignore_errors=True)
    if count:
        logger.info("deleted board assets %s/%s (%d file(s))", storage_slug(user_id), board_id[:8], count)
    return count


def _is_flat_upload_url(url: str) -> bool:
    raw = (url or "").strip()
    if _NESTED_UPLOAD_RE.search(raw):
        return False
    m = _FLAT_UPLOAD_RE.search(raw)
    if not m:
        return False
    name = m.group(1)
    return bool(name) and "/" not in name and ".." not in name


def _rewrite_media_dict(
    data: dict,
    *,
    user_id: str,
    board_id: str,
    cache: dict[str, str],
    stats: dict[str, int],
) -> bool:
    changed = False
    for key in _MEDIA_KEYS:
        val = str(data.get(key) or "")
        if not _is_flat_upload_url(val):
            continue
        fname = _FLAT_UPLOAD_RE.search(val).group(1)
        if fname not in cache:
            src = (UPLOADS_DIR / fname).resolve()
            if not src.is_file():
                stats["missing"] += 1
                continue
            dest_dir = board_assets_dir(user_id, board_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / fname
            if not dest.is_file():
                shutil.copy2(src, dest)
                stats["moved"] += 1
            cache[fname] = public_url(user_id, board_id, fname)
        new_url = cache.get(fname)
        if new_url and data.get(key) != new_url:
            data[key] = new_url
            changed = True
            stats["urls"] += 1
    return changed


def _user_id_for_slug(slug: str) -> str:
    from backend import app_users

    for row in app_users.list_users():
        if storage_slug(row["id"]) == slug:
            return row["id"]
    if slug == DEFAULT_USER_ID:
        return DEFAULT_USER_ID
    return slug


def migrate_legacy_uploads_to_boards() -> dict[str, int]:
    """Copy flat data/uploads/* into per-board folders and rewrite canvas URLs."""
    from backend import canvas_store

    stats = {"moved": 0, "missing": 0, "boards": 0, "urls": 0, "users": 0}
    legacy = DATA_DIR / "canvas.json"
    canvas_paths: list[Path] = []
    if legacy.is_file():
        canvas_paths.append(legacy)
    users_root = DATA_DIR / "users"
    if users_root.is_dir():
        for user_dir in users_root.iterdir():
            cpath = user_dir / "canvas.json"
            if cpath.is_file():
                canvas_paths.append(cpath)

    for cpath in canvas_paths:
        slug = DEFAULT_USER_ID if cpath == legacy else cpath.parent.name
        user_id = _user_id_for_slug(slug)
        owned = canvas_store.load_owned(user_id)
        boards = list(owned.get("boards") or [])
        user_changed = False

        for board in boards:
            if not isinstance(board, dict):
                continue
            bid = str(board.get("id") or "").strip()
            if not bid:
                continue
            cache: dict[str, str] = {}
            board_changed = False
            for node in board.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                if _rewrite_media_dict(node, user_id=user_id, board_id=bid, cache=cache, stats=stats):
                    board_changed = True
                for entry in node.get("genHistory") or []:
                    if isinstance(entry, dict) and _rewrite_media_dict(
                        entry, user_id=user_id, board_id=bid, cache=cache, stats=stats
                    ):
                        board_changed = True
            if board_changed:
                user_changed = True
                stats["boards"] += 1

        if user_changed:
            canvas_store.save_owned(user_id, boards)
            stats["users"] += 1

    if any(stats[k] for k in ("moved", "urls", "boards")):
        logger.info("board asset migrate: %s", stats)
    return stats


def canvas_referenced_upload_refs() -> set[str]:
    """Refs from canvas nodes + task queue only (not every file on disk)."""
    refs: set[str] = set()
    for _slug, board in iter_all_canvas_boards():
        refs |= collect_board_refs(board)

    users_root = DATA_DIR / "users"
    if users_root.is_dir():
        for user_dir in users_root.iterdir():
            if user_dir.is_dir():
                refs |= _refs_from_tasks_file(user_dir / "tasks.json")
    refs |= _refs_from_tasks_file(DATA_DIR / "tasks.json")
    refs |= _refs_from_workbench_dirs()
    refs |= _refs_from_workbench_json_files()

    expanded: set[str] = set(refs)
    for ref in refs:
        if "/" not in ref:
            expanded.add(ref)
        else:
            expanded.add(ref.rsplit("/", 1)[-1])
    return expanded


def _stem_keys_from_refs(keep: set[str]) -> set[str]:
    stems: set[str] = set()
    for ref in keep:
        name = ref.rsplit("/", 1)[-1] if "/" in ref else ref
        if not name:
            continue
        stem = Path(name).stem
        stems.add(stem)
        # Collapse derivatives: foo_poster_thumb → foo_poster → foo
        cur = stem
        for _ in range(3):
            if cur.endswith("_poster_thumb"):
                cur = cur[: -len("_poster_thumb")]
            elif cur.endswith("_poster"):
                cur = cur[: -len("_poster")]
            elif cur.endswith("_thumb"):
                cur = cur[: -len("_thumb")]
            else:
                break
            if cur:
                stems.add(cur)
    return stems


# Recently written assets are often still being linked into board.json / thumbUrl.
# Skipping them avoids enter-canvas purge racing ensure-thumb / transcode / task sync.
_PURGE_GRACE_SECONDS = 30 * 60


def _asset_in_purge_grace(path: Path, *, grace_seconds: int = _PURGE_GRACE_SECONDS) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < max(0, int(grace_seconds))


def iter_board_asset_files() -> Iterable[tuple[str, str, Path]]:
    """Yield (user_slug, board_id, file_path) for every board asset file."""
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return

    def _scan_assets_dir(slug: str, bid: str, assets_dir: Path) -> Iterable[tuple[str, str, Path]]:
        if not assets_dir.is_dir():
            return
        for asset in assets_dir.iterdir():
            if asset.is_file() and not asset.name.startswith("."):
                yield slug, bid, asset

    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        for child in user_dir.iterdir():
            # workbenches/{id}/assets are managed separately — never treat as a board folder
            if not child.is_dir() or child.name in _USER_DIR_SKIP or child.name == "workbenches":
                continue
            yield from _scan_assets_dir(slug, child.name, child / "assets")
        legacy_boards = user_dir / "boards"
        if legacy_boards.is_dir():
            for board_dir in legacy_boards.iterdir():
                if board_dir.is_dir():
                    yield from _scan_assets_dir(slug, board_dir.name, board_dir / "assets")


def _asset_is_kept(slug: str, board_id: str, filename: str, keep: set[str], stems: set[str]) -> bool:
    """True if filename (or its base stem) is still referenced anywhere in keep."""
    ref = f"{slug}/{board_id}/{filename}"
    if ref in keep or filename in keep:
        return True
    # Same file may be referenced from another board path: slug/otherBoard/filename
    suffix = f"/{filename}"
    for item in keep:
        text = str(item or "")
        if text == filename or text.endswith(suffix):
            return True
    stem = Path(filename).stem
    if stem in stems:
        return True
    cur = stem
    for _ in range(3):
        if cur.endswith("_poster_thumb"):
            cur = cur[: -len("_poster_thumb")]
        elif cur.endswith("_poster"):
            cur = cur[: -len("_poster")]
        elif cur.endswith("_thumb"):
            cur = cur[: -len("_thumb")]
        else:
            break
        if cur and cur in stems:
            return True
    return False


def _format_bytes(n: int) -> str:
    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def scan_orphan_upload_stats() -> dict[str, int | str]:
    keep = canvas_referenced_upload_refs()
    stems = _stem_keys_from_refs(keep)
    board_orphan_files = 0
    board_orphan_bytes = 0
    board_total_files = 0
    board_total_bytes = 0
    recent_protected_files = 0
    recent_protected_bytes = 0

    for slug, bid, path in iter_board_asset_files() or []:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        board_total_files += 1
        board_total_bytes += size
        if _asset_is_kept(slug, bid, path.name, keep, stems):
            continue
        if _asset_in_purge_grace(path):
            recent_protected_files += 1
            recent_protected_bytes += size
            continue
        board_orphan_files += 1
        board_orphan_bytes += size

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    legacy_orphan_files = 0
    legacy_orphan_bytes = 0
    legacy_total_files = 0
    legacy_total_bytes = 0
    for path in UPLOADS_DIR.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        legacy_total_files += 1
        legacy_total_bytes += size
        if path.name in keep:
            continue
        if _asset_in_purge_grace(path):
            recent_protected_files += 1
            recent_protected_bytes += size
            continue
        legacy_orphan_files += 1
        legacy_orphan_bytes += size

    orphan_files = board_orphan_files + legacy_orphan_files
    orphan_bytes = board_orphan_bytes + legacy_orphan_bytes
    return {
        "orphan_files": orphan_files,
        "orphan_bytes": orphan_bytes,
        "orphan_bytes_label": _format_bytes(orphan_bytes),
        "board_orphan_files": board_orphan_files,
        "board_orphan_bytes": board_orphan_bytes,
        "legacy_orphan_files": legacy_orphan_files,
        "legacy_orphan_bytes": legacy_orphan_bytes,
        "board_total_files": board_total_files,
        "board_total_bytes": board_total_bytes,
        "legacy_total_files": legacy_total_files,
        "legacy_total_bytes": legacy_total_bytes,
        "recent_protected_files": recent_protected_files,
        "recent_protected_bytes": recent_protected_bytes,
        "recent_protected_bytes_label": _format_bytes(recent_protected_bytes),
        "referenced_refs": len(keep),
    }


def purge_orphan_board_assets(*, keep_extra: set[str] | None = None) -> int:
    """Delete board asset files not referenced by any canvas node.

    Per-board safety: skip a board folder when reference stats look wrong
    (empty keep with nodes, or keep count far below file count).
    """
    keep = canvas_referenced_upload_refs()
    if keep_extra:
        keep |= {n for n in keep_extra if n}
    stems = _stem_keys_from_refs(keep)

    from collections import defaultdict

    by_board: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for slug, bid, path in iter_board_asset_files() or []:
        by_board[(slug, bid)].append(path)

    if not keep and by_board:
        total = sum(len(v) for v in by_board.values())
        logger.warning(
            "global board assets purge aborted: no canvas refs but %d file(s) across %d board(s)",
            total,
            len(by_board),
        )
        return 0

    deleted = 0
    skipped_boards = 0
    for (slug, bid), paths in by_board.items():
        keep_files = _board_keep_filenames(keep, slug=slug, board_id=bid)
        file_count = len(paths)
        board_path = DATA_DIR / "users" / slug / bid / "board.json"
        if not board_path.is_file():
            legacy = DATA_DIR / "users" / slug / "boards" / bid / "board.json"
            if legacy.is_file():
                board_path = legacy
        disk = _load_board_json_with_backup(board_path) or {}
        node_count = len(disk.get("nodes") or [])

        if node_count > 0 and file_count > 0 and not keep_files:
            logger.warning(
                "global purge skip board %s/%s: nodes=%d files=%d but no media refs",
                slug,
                bid[:8],
                node_count,
                file_count,
            )
            skipped_boards += 1
            continue
        if file_count >= 8 and keep_files and len(keep_files) < max(3, file_count // 5):
            logger.warning(
                "global purge skip board %s/%s: keep_files=%d scanned=%d looks too aggressive",
                slug,
                bid[:8],
                len(keep_files),
                file_count,
            )
            skipped_boards += 1
            continue

        for path in paths:
            if _asset_is_kept(slug, bid, path.name, keep, stems):
                continue
            if _asset_in_purge_grace(path):
                continue
            try:
                from backend.media.asset_trash import unlink_or_trash

                if unlink_or_trash(path, slug=slug, board_id=bid):
                    deleted += 1
            except OSError as exc:
                logger.debug("board asset purge skip %s: %s", path, exc)

    if deleted or skipped_boards:
        logger.info(
            "board assets purge: deleted=%d skipped_boards=%d",
            deleted,
            skipped_boards,
        )
    return deleted


def _iter_board_asset_dirs(user_id: str, board_id: str) -> list[Path]:
    dirs: list[Path] = []
    for path in (board_assets_dir(user_id, board_id), legacy_board_assets_dir(user_id, board_id)):
        if path.is_dir():
            dirs.append(path)
    return dirs


def _refs_kept_for_board(
    board: dict | None,
    *,
    user_id: str,
    board_id: str,
    trust_client: bool = False,
) -> set[str]:
    """
    Media URLs that must be kept for this board folder.

    Built from on-disk board.json (+ backup): every remaining node's current media,
    genHistory outputs, and genHistory reference inputs. When trust_client is true,
    also union the client snapshot; if the client looks regressed vs disk, prefer disk.
    """
    disk = _load_board_json_with_backup(board_json_path(user_id, board_id))
    disk_refs = collect_board_refs(disk) if disk else set()
    client = board if isinstance(board, dict) else None
    client_refs = collect_board_refs(client) if client and client.get("nodes") is not None else set()

    if trust_client and client is not None and client.get("nodes") is not None:
        refs = set(client_refs) | disk_refs
    else:
        refs = set(disk_refs)

    slug = storage_slug(user_id)
    prefix = f"{slug}/{board_id}/"
    tasks_path = user_data_root(user_id) / "tasks.json"
    if tasks_path.is_file():
        from backend.json_io import load_json

        raw = load_json(tasks_path, default=[])
        if isinstance(raw, list):
            for task in raw:
                if not isinstance(task, dict):
                    continue
                for key in ("image_url", "video_url", "cover_url", "thumb_url"):
                    ref = upload_ref_from_url(str(task.get(key) or ""))
                    if ref and ref.startswith(prefix):
                        refs.add(ref)

    return _expand_upload_refs(refs)


def purge_single_board_orphans(
    user_id: str,
    board_id: str,
    *,
    board: dict | None = None,
    trust_client: bool = False,
) -> dict[str, int]:
    """
    On board enter / after node delete: remove files in this board's assets folder that
    are not referenced by any remaining node (current media + genHistory + history refs).
    Deleted nodes are gone from board.json — their exclusive media becomes eligible.
    """
    uid = (user_id or "").strip() or DEFAULT_USER_ID
    bid = (board_id or "").strip()
    if not bid:
        return {"deleted": 0, "kept": 0, "scanned": 0}

    try:
        from backend.media.asset_trash import purge_expired_trash

        purge_expired_trash()
    except Exception as exc:
        logger.debug("trash TTL on board purge skipped: %s", exc)

    slug = storage_slug(uid)
    disk_board = _load_board_json_with_backup(board_json_path(uid, bid)) or {}
    keep = _refs_kept_for_board(
        board if isinstance(board, dict) and board.get("nodes") is not None else None,
        user_id=uid,
        board_id=bid,
        trust_client=bool(trust_client and isinstance(board, dict) and "nodes" in board),
    )
    history_keep = _expand_upload_refs(collect_board_history_refs(disk_board))
    history_stems = _stem_keys_from_refs(history_keep)
    stems = _stem_keys_from_refs(keep)

    deleted = 0
    scanned = 0
    kept = 0
    keep_files = _board_keep_filenames(keep, slug=slug, board_id=bid)
    file_count = 0
    for assets_dir in _iter_board_asset_dirs(uid, bid):
        try:
            file_count += sum(
                1
                for path in assets_dir.iterdir()
                if path.is_file() and not path.name.startswith(".")
            )
        except OSError:
            pass
    node_count = len(disk_board.get("nodes") or [])
    if node_count > 0 and file_count > 0 and not keep_files:
        logger.warning(
            "board enter purge aborted %s/%s: nodes=%d files=%d but no media refs",
            slug,
            bid[:8],
            node_count,
            file_count,
        )
        return {"deleted": 0, "kept": 0, "scanned": file_count, "aborted": True}
    if file_count >= 8 and keep_files and len(keep_files) < max(3, file_count // 5):
        logger.warning(
            "board enter purge aborted %s/%s: keep_files=%d scanned=%d looks too aggressive",
            slug,
            bid[:8],
            len(keep_files),
            file_count,
        )
        return {"deleted": 0, "kept": 0, "scanned": file_count, "aborted": True}

    for assets_dir in _iter_board_asset_dirs(uid, bid):
        try:
            entries = list(assets_dir.iterdir())
        except OSError:
            continue
        for path in entries:
            if not path.is_file() or path.name.startswith("."):
                continue
            scanned += 1
            if _asset_is_kept(slug, bid, path.name, keep, stems):
                kept += 1
                continue
            # genHistory media (incl. reference inputs) — never drop while still on canvas.
            if _asset_is_kept(slug, bid, path.name, history_keep, history_stems):
                kept += 1
                continue
            # New files often land before board.json / thumbUrl catch up.
            if _asset_in_purge_grace(path):
                kept += 1
                continue
            try:
                from backend.media.asset_trash import unlink_or_trash

                if unlink_or_trash(path, slug=slug, board_id=bid):
                    deleted += 1
            except OSError as exc:
                logger.debug("board enter purge skip %s: %s", path, exc)

        # Drop empty junk subfolders under assets/ (not files).
        for path in entries:
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                if any(path.iterdir()):
                    continue
                path.rmdir()
                deleted += 1
            except OSError:
                pass

    # Empty stray folders under board root (keep board.json + assets).
    root = board_root_dir(uid, bid)
    if root.is_dir():
        for path in list(root.iterdir()):
            if path.name in {"assets", "board.json"} or path.name.startswith("."):
                continue
            if path.is_dir():
                try:
                    # only remove if empty or only contains empty dirs
                    if any(p.is_file() for p in path.rglob("*")):
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                    deleted += 1
                except OSError:
                    pass

    if deleted:
        logger.info(
            "board enter purge %s/%s deleted=%d scanned=%d kept=%d",
            slug,
            bid[:8],
            deleted,
            scanned,
            kept,
        )
    return {"deleted": deleted, "kept": kept, "scanned": scanned}


# After the user deletes a node, its exclusive media is queued on the board
# (pendingAssetDeletes). Only those queued refs are eligible — never a full orphan scan.
PENDING_ASSET_DELETE_SECONDS = 3 * 24 * 60 * 60


def _parse_pending_delete_ts(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return None


def process_pending_asset_deletes(
    user_id: str,
    board_id: str,
    *,
    board: dict | None = None,
    grace_seconds: int = PENDING_ASSET_DELETE_SECONDS,
) -> dict[str, Any]:
    """
    Trash media that was explicitly queued when the user deleted nodes, after
    ``grace_seconds`` (default 3 days). Still-referenced files are skipped.
    Does not scan for unrelated orphans.
    """
    uid = (user_id or "").strip() or DEFAULT_USER_ID
    bid = (board_id or "").strip()
    if not bid:
        return {"deleted": 0, "skipped": 0, "missing": 0, "pending_changed": False}

    path = board_json_path(uid, bid)
    disk = board if isinstance(board, dict) else None
    if disk is None or disk.get("pendingAssetDeletes") is None:
        disk = _load_board_json_with_backup(path) or {}
    pending = disk.get("pendingAssetDeletes")
    if not isinstance(pending, list) or not pending:
        return {"deleted": 0, "skipped": 0, "missing": 0, "pending_changed": False}

    now = time.time()
    grace = max(0, int(grace_seconds))
    keep_entries: list[dict[str, Any]] = []
    due_refs: list[str] = []

    for entry in pending:
        if not isinstance(entry, dict):
            continue
        refs = [str(r).strip() for r in (entry.get("refs") or []) if str(r or "").strip()]
        if not refs:
            continue
        at = _parse_pending_delete_ts(entry.get("at"))
        age_ok = at is None or (now - at) >= grace
        if not age_ok:
            keep_entries.append({"at": entry.get("at"), "refs": refs})
            continue
        due_refs.extend(refs)

    deleted = skipped = missing = 0
    if due_refs:
        result = delete_upload_refs(due_refs, user_id=uid)
        deleted = int(result.get("deleted") or 0)
        skipped = int(result.get("skipped") or 0)
        missing = int(result.get("missing") or 0)

    # Normalize queue: drop due entries after one attempt (still-referenced → skipped).
    before_norm = [
        {"at": e.get("at"), "refs": [str(r).strip() for r in (e.get("refs") or []) if str(r or "").strip()]}
        for e in pending
        if isinstance(e, dict)
    ]
    before_norm = [e for e in before_norm if e["refs"]]
    pending_changed = keep_entries != before_norm
    if pending_changed:
        if keep_entries:
            disk["pendingAssetDeletes"] = keep_entries
        else:
            disk.pop("pendingAssetDeletes", None)
        try:
            from backend.json_io import save_json

            path.parent.mkdir(parents=True, exist_ok=True)
            full = _load_board_json_with_backup(path) or dict(disk)
            if keep_entries:
                full["pendingAssetDeletes"] = keep_entries
            else:
                full.pop("pendingAssetDeletes", None)
            save_json(path, full)
            if isinstance(board, dict):
                if keep_entries:
                    board["pendingAssetDeletes"] = keep_entries
                else:
                    board.pop("pendingAssetDeletes", None)
        except Exception as exc:
            logger.warning("pending asset delete queue save failed %s: %s", bid[:8], exc)

    if deleted or pending_changed:
        logger.info(
            "pending asset deletes %s/%s deleted=%d skipped=%d missing=%d remaining=%d",
            storage_slug(uid),
            bid[:8],
            deleted,
            skipped,
            missing,
            len(keep_entries),
        )
    return {
        "deleted": deleted,
        "skipped": skipped,
        "missing": missing,
        "pending_remaining": len(keep_entries),
        "pending_changed": pending_changed,
        "pendingAssetDeletes": keep_entries,
    }


def delete_unreferenced_legacy_files(*, keep_extra: set[str] | None = None) -> int:
    """Remove flat legacy files in data/uploads/ not referenced by any canvas."""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    keep = canvas_referenced_upload_refs()
    if keep_extra:
        keep |= {n for n in keep_extra if n}
    files = [p for p in UPLOADS_DIR.iterdir() if p.is_file() and not p.name.startswith(".")]
    if not keep and files:
        logger.warning(
            "legacy uploads purge skipped: no referenced files found (%d flat file(s) kept)",
            len(files),
        )
        return 0
    deleted = 0
    for path in list(UPLOADS_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name in keep:
            continue
        if _asset_in_purge_grace(path):
            continue
        try:
            from backend.media.asset_trash import unlink_or_trash

            if unlink_or_trash(path, slug="_legacy", board_id="_uploads"):
                deleted += 1
        except OSError as exc:
            logger.debug("legacy purge skip %s: %s", path.name, exc)
    if deleted:
        logger.info("legacy uploads purge: deleted %d orphan file(s)", deleted)
    return deleted


def _path_owned_by_user(path: Path, user_id: str) -> bool:
    try:
        root = user_data_root(user_id).resolve()
        resolved = path.resolve()
        return resolved == root or root in resolved.parents
    except OSError:
        return False


def _unlink_asset_with_siblings(path: Path, *, slug: str = "_unknown", board_id: str = "_legacy") -> list[str]:
    """Move a media file and matching _poster / _thumb siblings into trash."""
    from backend.media.asset_trash import unlink_or_trash

    removed: list[str] = []
    if not path.is_file():
        return removed
    parent = path.parent
    stem = path.stem
    targets = {path}
    try:
        for sibling in parent.iterdir():
            if not sibling.is_file() or sibling.name.startswith("."):
                continue
            s = sibling.stem
            if s == f"{stem}_poster" or s == f"{stem}_thumb":
                targets.add(sibling)
            elif stem.endswith("_poster") and s == stem[: -len("_poster")]:
                # deleting poster alone — do not remove the main media
                continue
            elif stem.endswith("_thumb") and s == stem[: -len("_thumb")]:
                continue
    except OSError:
        pass
    for target in targets:
        try:
            name = target.name
            if unlink_or_trash(target, slug=slug, board_id=board_id):
                removed.append(name)
        except OSError as exc:
            logger.debug("delete asset skip %s: %s", target, exc)
    return removed


def delete_upload_refs(
    refs: list[str],
    *,
    user_id: str,
    keep_extra: set[str] | None = None,
) -> dict[str, int | list[str]]:
    """Delete specific upload files owned by user if not referenced by any canvas."""
    uid = (user_id or "").strip() or DEFAULT_USER_ID
    slug = storage_slug(uid)
    keep = canvas_referenced_upload_refs()
    if keep_extra:
        keep |= {n for n in keep_extra if n}
    stems = _stem_keys_from_refs(keep)

    deleted = 0
    skipped = 0
    missing = 0
    deleted_names: list[str] = []

    seen: set[str] = set()
    for raw in refs or []:
        text = str(raw or "").strip()
        if not text:
            continue
        url = text if text.startswith("/") else f"/uploads/{text.lstrip('/')}"
        ref = upload_ref_from_url(url)
        if not ref or ref in seen:
            if not ref:
                missing += 1
            continue
        seen.add(ref)

        path = resolve_upload_path(url)
        if path is None or not path.is_file():
            missing += 1
            continue

        # Ownership: nested board assets must belong to this user; flat legacy uploads allowed.
        workbench = _WORKBENCH_UPLOAD_RE.search(url)
        nested = _NESTED_UPLOAD_RE.search(url) if not workbench else None
        if workbench:
            if workbench.group(1) != slug or not _path_owned_by_user(path, uid):
                skipped += 1
                continue
        elif nested:
            if nested.group(1) != slug or not _path_owned_by_user(path, uid):
                skipped += 1
                continue
        elif not _FLAT_UPLOAD_RE.search(url):
            skipped += 1
            continue

        filename = path.name
        board_id = (
            f"workbench-{workbench.group(2)}"
            if workbench
            else (nested.group(2) if nested else "")
        )
        # Keep if still referenced on any board (same path, flat name, or thumb/poster stem).
        if _asset_is_kept(slug, board_id or "_flat", filename, keep, stems):
            skipped += 1
            continue

        removed = _unlink_asset_with_siblings(
            path,
            slug=slug,
            board_id=board_id or "_uploads",
        )
        if removed:
            deleted += len(removed)
            deleted_names.extend(removed)
        else:
            missing += 1

    if deleted:
        logger.info("delete_upload_refs user=%s deleted=%d skipped=%d", slug, deleted, skipped)
    return {
        "deleted": deleted,
        "skipped": skipped,
        "missing": missing,
        "files": deleted_names[:50],
    }


def _should_write_board_json(
    board: dict,
    *,
    board_path: Path,
    canvas_path: Path,
    existing: dict | None,
) -> bool:
    """Never overwrite a full board.json with index-only canvas metadata."""
    if not _board_has_content(board):
        return False
    if existing is None:
        return True
    if not _board_has_content(existing):
        return True
    if board_path.stat().st_mtime >= canvas_path.stat().st_mtime:
        return False
    incoming_nodes = len(board.get("nodes") or [])
    existing_nodes = len(existing.get("nodes") or [])
    return incoming_nodes >= existing_nodes


def migrate_board_directory_layout() -> dict[str, int]:
    """Move users/{slug}/boards/{id}/ → users/{slug}/{id}/ and split canvas into per-board board.json."""
    from backend.json_io import load_json, save_json

    stats = {"users": 0, "boards": 0, "assets_moved": 0, "json_written": 0}
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return stats

    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        user_id = _user_id_for_slug(slug)
        canvas_path = user_dir / "canvas.json"
        if not canvas_path.is_file():
            continue

        data = load_json(canvas_path, default={})
        boards = list(data.get("boards") or [])
        if not boards:
            continue

        user_changed = False
        index_boards: list[dict[str, Any]] = []

        for board in boards:
            if not isinstance(board, dict):
                continue
            bid = str(board.get("id") or "").strip()
            if not bid:
                continue

            dest_assets = board_assets_dir(user_id, bid)
            dest_assets.mkdir(parents=True, exist_ok=True)

            legacy_assets = legacy_board_assets_dir(user_id, bid)
            if legacy_assets.is_dir():
                for src in legacy_assets.iterdir():
                    if not src.is_file():
                        continue
                    dest = dest_assets / src.name
                    if not dest.is_file():
                        shutil.copy2(src, dest)
                        stats["assets_moved"] += 1
                    elif src.stat().st_size != dest.stat().st_size:
                        shutil.copy2(src, dest)
                        stats["assets_moved"] += 1

            board_path = board_json_path(user_id, bid)
            existing = _load_board_json(board_path)
            if _should_write_board_json(
                board,
                board_path=board_path,
                canvas_path=canvas_path,
                existing=existing,
            ):
                save_json(board_path, board)
                stats["json_written"] += 1
                user_changed = True

            meta_source = existing if existing and _board_has_content(existing) else board
            if existing and _board_has_content(existing):
                meta_source = existing
            index_boards.append(
                {
                    "id": bid,
                    "name": meta_source.get("name") or board.get("name"),
                    "pinned": meta_source.get("pinned", board.get("pinned")),
                    "coverNodeId": meta_source.get("coverNodeId", board.get("coverNodeId")),
                }
            )
            stats["boards"] += 1

        for child in user_dir.iterdir():
            if not child.is_dir() or child.name in _USER_DIR_SKIP:
                continue
            bid = child.name
            if any(str(row.get("id") or "") == bid for row in index_boards):
                continue
            board = _load_board_json(child / "board.json")
            if not board:
                continue
            index_boards.append(
                {
                    "id": bid,
                    "name": board.get("name"),
                    "pinned": board.get("pinned"),
                    "coverNodeId": board.get("coverNodeId"),
                }
            )
            stats["boards"] += 1
            user_changed = True

        if user_changed or index_boards:
            save_json(
                canvas_path,
                {
                    "updatedAt": data.get("updatedAt"),
                    "boards": index_boards,
                    "storage": "per-board",
                },
            )
            stats["users"] += 1

        legacy_boards_root = user_dir / "boards"
        if legacy_boards_root.is_dir():
            try:
                shutil.rmtree(legacy_boards_root, ignore_errors=True)
            except OSError as exc:
                logger.debug("legacy boards dir cleanup %s: %s", legacy_boards_root, exc)

    legacy_canvas = DATA_DIR / "canvas.json"
    default_canvas = DATA_DIR / "users" / "default" / "canvas.json"
    if legacy_canvas.is_file() and default_canvas.is_file():
        try:
            legacy_canvas.unlink(missing_ok=True)
            stats["legacy_canvas_removed"] = 1
        except OSError as exc:
            logger.debug("legacy canvas.json remove failed: %s", exc)

    if any(stats.values()):
        logger.info("board directory migrate: %s", stats)
    return stats


def repair_per_board_storage() -> dict[str, int]:
    """Rebuild canvas indexes from board.json on disk; never destructive."""
    from backend import canvas_store
    from backend.json_io import load_json

    stats = {"users": 0, "boards": 0}
    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return stats

    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        user_id = _user_id_for_slug(slug)
        before = load_json(user_dir / "canvas.json", default={}) if (user_dir / "canvas.json").is_file() else {}
        before_ids = {
            str(b.get("id") or "").strip()
            for b in (before.get("boards") or [])
            if isinstance(b, dict) and str(b.get("id") or "").strip()
        }
        owned = canvas_store.load_owned(user_id)
        boards = owned.get("boards") or []
        after_ids = {
            str(b.get("id") or "").strip()
            for b in boards
            if isinstance(b, dict) and str(b.get("id") or "").strip()
        }
        if after_ids != before_ids or before.get("storage") != "per-board":
            stats["users"] += 1
        stats["boards"] += len(boards)

    if stats["users"]:
        logger.info("per-board storage repair: %s", stats)
    return stats
