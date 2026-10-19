"""Partial autosave using the existing per-document files and authorization.

Omitted documents are untouched. Deletion still uses the dedicated DELETE APIs.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from backend.json_io import load_json, save_json


def save_changed(user_id: str, rows: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    if kind == "canvas":
        from backend import canvas_store as store

        field, storage = "boards", "per-board"
        ensure, index_path = store._ensure_canvas_file, store._canvas_path
        write_row = store._save_board_file
        can_share = store.user_can_edit_shared_board
        cover = "coverNodeId"
    else:
        raise ValueError("Unknown document kind")

    owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("保存内容必须是对象")
        doc_id = str(row.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", doc_id):
            raise ValueError("无效的文档 ID")
        owner = user_id
        if row.get("isOwned") is False or row.get("shareOwnerId"):
            owner = str(row.get("shareOwnerId") or "")
            if not owner or not can_share(user_id, owner, doc_id):
                raise PermissionError("没有此共享文档的编辑权限")
        if (owner, doc_id) in seen:
            raise ValueError("重复的文档 ID")
        seen.add((owner, doc_id))
        owners[owner].append(store._strip_meta(row))

    updated_at = None
    for owner, changed in owners.items():
        with store._user_save_lock(owner):
            ensure(owner)
            path = index_path(owner)
            index = load_json(path, default={field: []})
            if not isinstance(index, dict) or not isinstance(index.get(field), list):
                raise ValueError("文档索引异常，请先恢复索引")
            previous = [r for r in index[field] if isinstance(r, dict) and r.get("id")]
            if index.get("storage") != storage:
                # Migrate a legacy inline index once; retain untouched content.
                for old in previous:
                    if any(key not in {"id", "name", "pinned", cover} for key in old):
                        write_row(owner, old)
            positions = {r["id"]: i for i, r in enumerate(previous)}
            for row in changed:
                write_row(owner, row)
                if row["id"] in positions:
                    previous[positions[row["id"]]] = row
                else:
                    positions[row["id"]] = len(previous)
                    previous.append(row)
            stamp = datetime.now().isoformat(timespec="microseconds")
            save_json(path, {
                "updatedAt": stamp, "storage": storage,
                field: [{k: row.get(k) for k in ("id", "name", "pinned", cover)} for row in previous],
            })
            if owner == user_id:
                updated_at = stamp
    return {"ok": True, "updatedAt": updated_at, "savedIds": [row["id"] for row in rows]}
