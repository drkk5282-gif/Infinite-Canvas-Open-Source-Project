"""Per-user canvas storage + explicit share by board ID / username."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json
from backend.user_scope import DEFAULT_USER_ID, storage_slug, user_data_root

logger = logging.getLogger(__name__)

_save_locks: dict[str, threading.Lock] = {}
_save_locks_guard = threading.Lock()


def _user_save_lock(user_id: str) -> threading.Lock:
    key = storage_slug(user_id)
    with _save_locks_guard:
        lock = _save_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _save_locks[key] = lock
        return lock

CANVAS_FILE = DATA_DIR / "canvas.json"  # legacy global file
SHARES_FILE = DATA_DIR / "canvas_shares.json"
_META_KEYS = frozenset({"isOwned", "sharedFrom", "shareOwnerId", "shareOwnerUsername"})
_INDEX_ONLY_KEYS = frozenset({"id", "name", "pinned", "coverNodeId", "nodeCount", "coverThumbUrl"})
_MEDIA_KEYS = ("imageUrl", "videoUrl", "audioUrl", "coverUrl", "thumbUrl", "aiMarkBaseUrl")
_USER_DIR_SKIP = frozenset(
    {"boards", "accounts", "canvas.json", "tasks.json", "accounts.json", "service_config.json", "myapi_models.json"}
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _canvas_path(user_id: str) -> Path:
    return user_data_root(user_id) / "canvas.json"


def _load_share_db() -> dict[str, Any]:
    raw = load_json(SHARES_FILE, default={"shares": [], "links": []})
    if not isinstance(raw, dict):
        return {"shares": [], "links": []}
    shares = raw.get("shares")
    links = raw.get("links")
    return {
        "shares": [s for s in shares if isinstance(s, dict)] if isinstance(shares, list) else [],
        "links": [l for l in links if isinstance(l, dict)] if isinstance(links, list) else [],
    }


def _save_share_db(shares: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(SHARES_FILE, {"shares": shares[-500:], "links": links[-200:]})


def _load_shares() -> list[dict[str, Any]]:
    return _load_share_db()["shares"]


def _load_share_links() -> list[dict[str, Any]]:
    return _load_share_db()["links"]


def _save_shares(shares: list[dict[str, Any]]) -> None:
    db = _load_share_db()
    _save_share_db(shares, db["links"])


def migrate_global_canvas() -> None:
    """Move legacy data/canvas.json → users/default/canvas.json once."""
    if not CANVAS_FILE.is_file():
        return
    dest = _canvas_path(DEFAULT_USER_ID)
    if dest.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CANVAS_FILE, dest)
    logger.info("migrated canvas.json → %s", dest)


def _ensure_canvas_file(user_id: str) -> None:
    migrate_global_canvas()
    path = _canvas_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        save_json(path, {"updatedAt": None, "boards": []})


def _strip_meta(board: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in board.items() if k not in _META_KEYS}


def _is_index_only_board(board: dict[str, Any]) -> bool:
    if not board:
        return True
    keys = set(board.keys()) - _META_KEYS
    return keys.issubset(_INDEX_ONLY_KEYS)


def _board_has_content(board: dict[str, Any]) -> bool:
    if not board:
        return False
    if board.get("nodes") or board.get("edges") or board.get("groups"):
        return True
    return not _is_index_only_board(board)


def _discover_board_ids_on_disk(user_id: str) -> list[str]:
    root = user_data_root(user_id)
    if not root.is_dir():
        return []
    ids: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name in _USER_DIR_SKIP:
            continue
        if (child / "board.json").is_file():
            ids.append(child.name)
    return sorted(ids)


def _load_board_file(user_id: str, board_id: str) -> dict[str, Any] | None:
    from backend import board_assets

    path = board_assets.board_json_path(user_id, board_id)
    if not path.is_file():
        return None
    backup_path = path.with_name(f"{path.stem}.json.bak")

    def _read_board_file(target: Path) -> dict[str, Any] | None:
        data = load_json(target, default=None)
        if isinstance(data, dict) and _board_has_content(data):
            return data
        return data if isinstance(data, dict) else None

    try:
        data = _read_board_file(path)
    except json.JSONDecodeError:
        logger.warning("corrupt board json %s for user %s", board_id[:8], storage_slug(user_id))
        data = None
    if data and _board_has_content(data):
        return data
    if backup_path.is_file():
        try:
            backup = _read_board_file(backup_path)
        except json.JSONDecodeError:
            backup = None
        if isinstance(backup, dict) and _board_has_content(backup):
            logger.warning("restored board %s from backup for user %s", board_id[:8], storage_slug(user_id))
            save_json(path, _strip_meta(backup))
            return backup
    return data if isinstance(data, dict) else None


def _board_media_ref_count(board: dict[str, Any]) -> int:
    from backend.board_assets import collect_board_refs

    return len(collect_board_refs(board if isinstance(board, dict) else {}))


def _merge_board_media_from_existing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """When a client snapshot drops media URLs, preserve them from the on-disk board by node id."""
    out = dict(incoming)
    ex_nodes = {
        str(n.get("id")): n
        for n in (existing.get("nodes") or [])
        if isinstance(n, dict) and n.get("id")
    }
    merged_nodes: list[Any] = []
    for node in out.get("nodes") or []:
        if not isinstance(node, dict):
            merged_nodes.append(node)
            continue
        ex = ex_nodes.get(str(node.get("id") or ""))
        if not ex:
            merged_nodes.append(node)
            continue
        row = dict(node)
        for key in _MEDIA_KEYS:
            if not row.get(key) and ex.get(key):
                row[key] = ex[key]
        ex_hist = ex.get("genHistory") or []
        in_hist = row.get("genHistory") or []
        if isinstance(ex_hist, list) and isinstance(in_hist, list) and len(ex_hist) > len(in_hist):
            row["genHistory"] = ex_hist
        merged_nodes.append(row)
    out["nodes"] = merged_nodes
    return out


def _board_index_entry(board: dict[str, Any]) -> dict[str, Any]:
    nodes = board.get("nodes") if isinstance(board, dict) else None
    node_count = (
        len([n for n in nodes if isinstance(n, dict)])
        if isinstance(nodes, list)
        else int(board.get("nodeCount") or 0)
    )
    return {
        "id": board.get("id"),
        "name": board.get("name"),
        "pinned": board.get("pinned"),
        "coverNodeId": board.get("coverNodeId"),
        "nodeCount": node_count,
        "coverThumbUrl": board.get("coverThumbUrl") or None,
    }


def _save_board_file(user_id: str, board: dict[str, Any]) -> None:
    from backend import board_assets

    bid = str(board.get("id") or "").strip()
    if not bid:
        return
    path = board_assets.board_json_path(user_id, bid)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_board_file(user_id, bid)
    if path.is_file() and existing and _board_has_content(existing):
        backup_path = path.with_name(f"{path.stem}.json.bak")
        try:
            shutil.copy2(path, backup_path)
        except OSError as exc:
            logger.debug("board backup skip %s: %s", bid[:8], exc)
    if existing and _board_has_content(existing) and not _board_has_content(board):
        row = dict(existing)
        for key in ("name", "pinned", "coverNodeId"):
            if board.get(key) is not None:
                row[key] = board.get(key)
        save_json(path, _strip_meta(row))
        return
    if existing and _board_has_content(existing) and _board_has_content(board):
        old_refs = _board_media_ref_count(existing)
        new_refs = _board_media_ref_count(board)
        if old_refs >= 4 and new_refs < old_refs * 0.55:
            logger.warning(
                "board save media regression %s: refs %d -> %d, merging from disk",
                bid[:8],
                old_refs,
                new_refs,
            )
            board = _merge_board_media_from_existing(existing, board)
        # Multi-tab: never let an empty editor snapshot wipe stored node prompts.
        old_nodes = {
            str(n.get("id")): n
            for n in (existing.get("nodes") or [])
            if isinstance(n, dict) and n.get("id")
        }
        for node in board.get("nodes") or []:
            if not isinstance(node, dict) or not node.get("id"):
                continue
            old = old_nodes.get(str(node["id"]))
            if not old:
                continue
            if not str(node.get("prompt") or "").strip() and str(old.get("prompt") or "").strip():
                node["prompt"] = old["prompt"]
            if not node.get("genHistory") and old.get("genHistory"):
                node["genHistory"] = old["genHistory"]
    save_json(path, _strip_meta(board))
    try:
        from backend import board_assets

        board_assets.process_pending_asset_deletes(user_id, bid, board=board)
    except Exception as exc:
        logger.debug("pending asset deletes on save skipped %s: %s", bid[:8], exc)


def _merge_boards_from_files(user_id: str, index_boards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for meta in index_boards:
        if not isinstance(meta, dict):
            continue
        bid = str(meta.get("id") or "").strip()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        full = _load_board_file(user_id, bid)
        if full and _board_has_content(full):
            row = dict(full)
            for key in ("name", "pinned", "coverNodeId"):
                if meta.get(key) is not None:
                    row[key] = meta.get(key)
        elif full and not _is_index_only_board(meta):
            row = dict(meta)
            for key, val in full.items():
                if key not in row or row.get(key) in (None, "", []):
                    row[key] = val
        elif _is_index_only_board(meta) and not (full and _board_has_content(full)):
            if full:
                merged.append(dict(full))
            continue
        else:
            row = dict(meta)
        merged.append(row)

    # Index is authoritative for membership. Do NOT auto-promote leftover board.json
    # folders on disk — that resurrects intentionally deleted boards on every load.
    return merged


def load_owned(user_id: str) -> dict[str, Any]:
    _ensure_canvas_file(user_id)
    data = load_json(_canvas_path(user_id), default={"updatedAt": None, "boards": []})
    if not isinstance(data, dict):
        return {"updatedAt": None, "boards": []}
    boards_raw = data.get("boards")
    if not isinstance(boards_raw, list):
        boards_raw = []

    from backend import board_assets

    if data.get("storage") == "per-board" or any(
        isinstance(b, dict) and _is_index_only_board(b) for b in boards_raw
    ):
        boards = _merge_boards_from_files(user_id, boards_raw)
        if not boards and boards_raw and not _is_index_only_board(boards_raw[0] if boards_raw else {}):
            boards = boards_raw
    else:
        boards = boards_raw

    index_ids = {
        str(b.get("id") or "").strip()
        for b in boards_raw
        if isinstance(b, dict) and str(b.get("id") or "").strip()
    }
    merged_ids = {
        str(b.get("id") or "").strip()
        for b in boards
        if isinstance(b, dict) and str(b.get("id") or "").strip()
    }
    needs_index_repair = merged_ids != index_ids

    repaired = False
    for board in boards:
        if isinstance(board, dict) and board_assets.repair_board_media(board):
            repaired = True
    if repaired or needs_index_repair:
        save_owned(user_id, boards)
    return {"updatedAt": data.get("updatedAt"), "boards": boards}


def save_owned(user_id: str, boards: list[dict[str, Any]]) -> dict[str, Any]:
    with _user_save_lock(user_id):
        _ensure_canvas_file(user_id)
        cleaned = [_strip_meta(b) for b in boards if isinstance(b, dict)]
        index_boards = [_board_index_entry(b) for b in cleaned if b.get("id")]
        for board in cleaned:
            _save_board_file(user_id, board)
        data = {
            "updatedAt": _now_iso(),
            "boards": index_boards,
            "storage": "per-board",
        }
        save_json(_canvas_path(user_id), data)
        return data


def get_board(owner_user_id: str, board_id: str) -> dict[str, Any] | None:
    bid = (board_id or "").strip()
    if not bid:
        return None
    full = _load_board_file(owner_user_id, bid)
    if full and _board_has_content(full):
        data = load_json(_canvas_path(owner_user_id), default={"boards": []})
        for meta in data.get("boards") or []:
            if isinstance(meta, dict) and str(meta.get("id") or "") == bid:
                row = dict(full)
                for key in ("name", "pinned", "coverNodeId"):
                    if meta.get(key) is not None:
                        row[key] = meta.get(key)
                return row
        # Owned file without index entry still counts for direct ID lookup.
        return dict(full)
    for board in load_owned(owner_user_id).get("boards") or []:
        if isinstance(board, dict) and str(board.get("id") or "") == bid:
            return board
    data = load_json(_canvas_path(owner_user_id), default={"boards": []})
    for meta in data.get("boards") or []:
        if isinstance(meta, dict) and str(meta.get("id") or "") == bid:
            return full if full else dict(meta)
    return full


def remove_board(owner_user_id: str, board_id: str) -> bool:
    """Remove a board from the user's canvas index and delete its on-disk folder."""
    bid = (board_id or "").strip()
    if not bid:
        return False
    # Read the index file directly — avoid load_owned side effects during delete.
    _ensure_canvas_file(owner_user_id)
    raw = load_json(_canvas_path(owner_user_id), default={"boards": []})
    boards = [b for b in (raw.get("boards") or []) if isinstance(b, dict)]
    next_boards = [b for b in boards if str(b.get("id") or "") != bid]
    if len(next_boards) == len(boards):
        # Still try to wipe leftover folder if index already dropped it.
        from backend import board_assets

        board_assets.delete_board_assets(owner_user_id, board_id)
        return False
    # Drop files first so a later load cannot resurrect this board.json.
    from backend import board_assets

    board_assets.delete_board_assets(owner_user_id, board_id)
    # Rebuild owned list from remaining index entries (full content via files).
    remaining: list[dict[str, Any]] = []
    for meta in next_boards:
        mid = str(meta.get("id") or "").strip()
        if not mid:
            continue
        full = _load_board_file(owner_user_id, mid)
        remaining.append(dict(full) if full else dict(meta))
    save_owned(owner_user_id, remaining)
    return True


def update_board_for_owner(owner_user_id: str, board: dict[str, Any]) -> None:
    bid = str(board.get("id") or "").strip()
    if not bid:
        return
    data = load_owned(owner_user_id)
    boards = list(data.get("boards") or [])
    cleaned = _strip_meta(board)
    found = False
    for i, row in enumerate(boards):
        if isinstance(row, dict) and row.get("id") == bid:
            boards[i] = cleaned
            found = True
            break
    if not found:
        boards.append(cleaned)
    save_owned(owner_user_id, boards)


def shares_for_user(user_id: str) -> list[dict[str, Any]]:
    uid = (user_id or "").strip()
    return [s for s in _load_shares() if str(s.get("shared_with_user_id") or "") == uid]


def shares_by_owner(owner_user_id: str, board_id: str) -> list[dict[str, Any]]:
    oid = (owner_user_id or "").strip()
    bid = (board_id or "").strip()
    return [
        s
        for s in _load_shares()
        if str(s.get("owner_user_id") or "") == oid and str(s.get("board_id") or "") == bid
    ]


def load_for_user(user_id: str) -> dict[str, Any]:
    """Owned boards only (board sharing is disabled)."""
    from backend import app_users

    owned_data = load_owned(user_id)
    owned_boards: list[dict[str, Any]] = []
    for board in owned_data.get("boards") or []:
        if not isinstance(board, dict):
            continue
        row = dict(board)
        row["isOwned"] = True
        owned_boards.append(row)

    user = app_users.get_user(user_id) or {}
    username = str(user.get("username") or "")
    return {
        "updatedAt": owned_data.get("updatedAt"),
        "boards": owned_boards,
        "username": username,
    }


def user_can_edit_shared_board(editor_user_id: str, owner_user_id: str, board_id: str) -> bool:
    return user_has_board_access(editor_user_id, owner_user_id, board_id)


def user_has_board_access(user_id: str, owner_id: str, board_id: str) -> bool:
    uid = (user_id or "").strip()
    oid = (owner_id or "").strip()
    bid = (board_id or "").strip()
    if not uid or not oid or not bid:
        return False
    if uid == oid:
        return True
    for share in shares_for_user(uid):
        if (
            str(share.get("owner_user_id") or "") == oid
            and str(share.get("board_id") or "") == bid
        ):
            return True
    return False


def save_for_user(user_id: str, boards: list[dict[str, Any]]) -> dict[str, Any]:
    owned_out: list[dict[str, Any]] = []
    for board in boards:
        if not isinstance(board, dict):
            continue
        if board.get("isOwned") is False or board.get("shareOwnerId"):
            owner_id = str(board.get("shareOwnerId") or "")
            board_id = str(board.get("id") or "")
            if owner_id and board_id:
                if not user_can_edit_shared_board(user_id, owner_id, board_id):
                    logger.warning(
                        "blocked canvas save: user %s has no share on board %s (owner %s)",
                        user_id,
                        board_id[:8],
                        owner_id[:8],
                    )
                    continue
                update_board_for_owner(owner_id, board)
            continue
        owned_out.append(_strip_meta(board))
    saved = save_owned(user_id, owned_out)
    view = load_for_user(user_id)
    view["updatedAt"] = saved.get("updatedAt")
    return view


def share_board_to_user(
    owner_user_id: str,
    board_id: str,
    target_username: str,
) -> dict[str, Any]:
    from backend import app_users

    owner = app_users.get_user(owner_user_id)
    if not owner:
        raise PermissionError("用户无效")
    target = app_users.get_user_by_username(target_username)
    if not target:
        raise ValueError(f"用户「{target_username}」不存在")
    if target["id"] == owner_user_id:
        raise ValueError("不能共享给自己")
    board = get_board(owner_user_id, board_id)
    if not board:
        raise KeyError("画板不存在")
    shares = _load_shares()
    for row in shares:
        if (
            str(row.get("owner_user_id") or "") == owner_user_id
            and str(row.get("board_id") or "") == board_id
            and str(row.get("shared_with_user_id") or "") == target["id"]
        ):
            return {
                "ok": True,
                "boardId": board_id,
                "sharedWith": target["username"],
                "already": True,
            }
    _add_share_record(
        owner_user_id,
        board_id,
        target,
        owner_username=str(owner.get("username") or ""),
    )
    logger.info(
        "canvas share %s → %s (board %s)",
        owner.get("username"),
        target.get("username"),
        board_id[:8],
    )
    return {
        "ok": True,
        "boardId": board_id,
        "sharedWith": target["username"],
        "already": False,
    }


def join_board_by_id(user_id: str, board_id: str) -> dict[str, Any]:
    """Recipient opens a board by ID — must already be shared with them."""
    bid = (board_id or "").strip()
    if not bid:
        raise ValueError("请填写画板 ID")
    for share in shares_for_user(user_id):
        sid = str(share.get("board_id") or "")
        if sid == bid or sid.startswith(bid) or (len(bid) >= 6 and bid in sid):
            board = get_board(str(share.get("owner_user_id") or ""), sid)
            if board:
                return {"ok": True, **load_for_user(user_id)}
    raise PermissionError("该画板未共享给您，请让所有者先在画板中共享并填写您的账号")


def list_board_shares(owner_user_id: str, board_id: str) -> list[dict[str, Any]]:
    return [
        {
            "username": s.get("shared_with_username"),
            "createdAt": s.get("created_at"),
        }
        for s in shares_by_owner(owner_user_id, board_id)
    ]


def _find_share_link(owner_user_id: str, board_id: str) -> dict[str, Any] | None:
    for row in _load_share_links():
        if (
            str(row.get("owner_user_id") or "") == owner_user_id
            and str(row.get("board_id") or "") == board_id
        ):
            return row
    return None


def _add_share_record(
    owner_user_id: str,
    board_id: str,
    target_user: dict[str, Any],
    *,
    owner_username: str = "",
) -> bool:
    """Add share if missing. Returns True if newly added."""
    if str(target_user.get("id") or "") == owner_user_id:
        return False
    board = get_board(owner_user_id, board_id)
    if not board:
        raise KeyError("画板不存在")
    shares = _load_shares()
    for row in shares:
        if (
            str(row.get("owner_user_id") or "") == owner_user_id
            and str(row.get("board_id") or "") == board_id
            and str(row.get("shared_with_user_id") or "") == target_user["id"]
        ):
            return False
    shares.append(
        {
            "id": uuid.uuid4().hex[:12],
            "board_id": board_id,
            "board_name": str(board.get("name") or ""),
            "owner_user_id": owner_user_id,
            "owner_username": owner_username,
            "shared_with_user_id": target_user["id"],
            "shared_with_username": str(target_user.get("username") or ""),
            "created_at": _now_iso(),
        }
    )
    _save_shares(shares)
    return True


def get_board_share_info(owner_user_id: str, board_id: str) -> dict[str, Any]:
    board = get_board(owner_user_id, board_id)
    if not board:
        raise KeyError("画板不存在")
    link = _find_share_link(owner_user_id, board_id)
    return {
        "boardId": board_id,
        "linkEnabled": bool(link and link.get("enabled")),
        "token": str(link.get("token") or "") if link else "",
        "shares": list_board_shares(owner_user_id, board_id),
    }


def enable_share_link(owner_user_id: str, board_id: str) -> dict[str, Any]:
    from backend import app_users

    owner = app_users.get_user(owner_user_id)
    if not owner:
        raise PermissionError("用户无效")
    board = get_board(owner_user_id, board_id)
    if not board:
        raise KeyError("画板不存在")
    links = _load_share_links()
    link = None
    for row in links:
        if (
            str(row.get("owner_user_id") or "") == owner_user_id
            and str(row.get("board_id") or "") == board_id
        ):
            link = row
            break
    if link:
        link["enabled"] = True
        if not link.get("token"):
            link["token"] = uuid.uuid4().hex[:16]
    else:
        link = {
            "board_id": board_id,
            "owner_user_id": owner_user_id,
            "owner_username": str(owner.get("username") or ""),
            "token": uuid.uuid4().hex[:16],
            "enabled": True,
            "created_at": _now_iso(),
        }
        links.append(link)
    _save_share_db(_load_shares(), links)
    return {
        "ok": True,
        "token": str(link.get("token") or ""),
        "linkEnabled": True,
        "boardId": board_id,
    }


def disable_share_link(owner_user_id: str, board_id: str) -> dict[str, Any]:
    links = _load_share_links()
    found = False
    for row in links:
        if (
            str(row.get("owner_user_id") or "") == owner_user_id
            and str(row.get("board_id") or "") == board_id
        ):
            row["enabled"] = False
            found = True
    if found:
        _save_share_db(_load_shares(), links)
    return {"ok": True, "linkEnabled": False, "boardId": board_id}


def accept_share_link(user_id: str, token: str) -> dict[str, Any]:
    from backend import app_users

    tok = (token or "").strip()
    if not tok:
        raise ValueError("共享链接无效")
    user = app_users.get_user(user_id)
    if not user:
        raise PermissionError("用户无效")
    link = None
    for row in _load_share_links():
        if str(row.get("token") or "") == tok:
            link = row
            break
    if not link or not link.get("enabled"):
        raise PermissionError("共享链接已关闭或不存在")
    owner_id = str(link.get("owner_user_id") or "")
    board_id = str(link.get("board_id") or "")
    if owner_id == user_id:
        return {"ok": True, "already": True, **load_for_user(user_id)}
    owner_username = str(link.get("owner_username") or "")
    target = {"id": user_id, "username": user.get("username")}
    added = _add_share_record(owner_id, board_id, target, owner_username=owner_username)
    return {
        "ok": True,
        "added": added,
        "boardId": board_id,
        **load_for_user(user_id),
    }


def revoke_board_share(owner_user_id: str, board_id: str, target_username: str) -> dict[str, Any]:
    from backend import app_users

    target = app_users.get_user_by_username((target_username or "").strip())
    if not target:
        raise ValueError(f"用户「{target_username}」不存在")
    shares = _load_shares()
    before = len(shares)
    shares = [
        s
        for s in shares
        if not (
            str(s.get("owner_user_id") or "") == owner_user_id
            and str(s.get("board_id") or "") == board_id
            and str(s.get("shared_with_user_id") or "") == target["id"]
        )
    ]
    if len(shares) == before:
        raise ValueError("该用户未共享此画板")
    _save_shares(shares)
    return {"ok": True, "revoked": target.get("username")}
