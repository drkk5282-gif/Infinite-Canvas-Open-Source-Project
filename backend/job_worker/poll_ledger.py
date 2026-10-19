# -*- coding: utf-8 -*-
"""Persist in-flight MyApi poll metadata so 8089 can resume after restart.

Each open poll is one UTF-8 text/JSON file under data/poll_ledger/{job_id}.json.
Written when upstream poll starts; retained for failed queries/downloads until resolved.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR
from backend.file_transaction import file_transaction
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

LEDGER_DIR = DATA_DIR / "poll_ledger"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path(job_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (job_id or "").strip())
    if not safe:
        raise ValueError("job_id required")
    return LEDGER_DIR / f"{safe}.json"


def _lookup_node_id(user_id: str, board_id: str, task_id: str) -> str:
    """Best-effort: find canvas node whose taskId matches the job."""
    uid = (user_id or "").strip()
    bid = (board_id or "").strip()
    tid = (task_id or "").strip()
    if not uid or not bid or not tid:
        return ""
    try:
        from backend import canvas_store

        board = canvas_store.get_board(uid, bid)
        if not board:
            return ""
        for node in board.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            if str(node.get("taskId") or "").strip() == tid:
                return str(node.get("id") or "").strip()
    except Exception as exc:
        logger.debug("poll ledger node lookup failed: %s", exc)
    return ""


def write_record(
    *,
    job_id: str,
    kind: str,
    model: str,
    upstream_task_id: str,
    poll_url: str = "",
    base_url: str = "",
    style_id: str = "",
    resolved_poll_url: str = "",
    user_id: str = "",
    board_id: str = "",
    node_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/overwrite the poll record when a poll starts."""
    jid = (job_id or "").strip()
    if not jid:
        raise ValueError("job_id required")
    uid = (user_id or "").strip()
    bid = (board_id or "").strip()
    nid = (node_id or "").strip() or _lookup_node_id(uid, bid, jid)
    now = _now_iso()
    record: dict[str, Any] = {
        "job_id": jid,
        "task_id": jid,
        "user_id": uid,
        "board_id": bid,
        "node_id": nid,
        "kind": (kind or "video").strip().lower(),
        "model": (model or "").strip(),
        "upstream_task_id": (upstream_task_id or "").strip(),
        "poll_url": (poll_url or "").strip(),
        "base_url": (base_url or "").strip(),
        "style_id": (style_id or "").strip(),
        "resolved_poll_url": (resolved_poll_url or "").strip(),
        "state": "polling",
        "created_at": now,
        "updated_at": now,
        "reclaim_count": 0,
    }
    if extra and isinstance(extra, dict):
        for k, v in extra.items():
            if k in record or v is None:
                continue
            record[k] = v
    with file_transaction(LEDGER_DIR / "ledger"):
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(jid)
        # Preserve created_at / reclaim_count if reclaiming the same job
        if path.is_file():
            prev = load_json(path, default=None)
            if isinstance(prev, dict):
                record["created_at"] = prev.get("created_at") or now
                try:
                    record["reclaim_count"] = int(prev.get("reclaim_count") or 0)
                except (TypeError, ValueError):
                    record["reclaim_count"] = 0
                if not record["node_id"] and prev.get("node_id"):
                    record["node_id"] = str(prev.get("node_id") or "")
        save_json(path, record)
    logger.info(
        "poll ledger write job=%s kind=%s upstream=%s node=%s",
        jid[:12],
        record["kind"],
        record["upstream_task_id"][:16],
        record["node_id"][:12] or "-",
    )
    return record


def mark_reclaiming(job_id: str) -> dict[str, Any] | None:
    """Mark a record as being reclaimed by the worker resume loop."""
    jid = (job_id or "").strip()
    if not jid:
        return None
    with file_transaction(LEDGER_DIR / "ledger"):
        path = _path(jid)
        if not path.is_file():
            return None
        row = load_json(path, default=None)
        if not isinstance(row, dict):
            return None
        row = dict(row)
        row["state"] = "reclaiming"
        row["updated_at"] = _now_iso()
        try:
            row["reclaim_count"] = int(row.get("reclaim_count") or 0) + 1
        except (TypeError, ValueError):
            row["reclaim_count"] = 1
        save_json(path, row)
        return row


def update_record(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """Patch poll progress without replacing identity or original creation time."""
    jid = (job_id or "").strip()
    if not jid:
        return None
    with file_transaction(LEDGER_DIR / "ledger"):
        path = _path(jid)
        if not path.is_file():
            return None
        row = load_json(path, default=None)
        if not isinstance(row, dict):
            return None
        out = dict(row)
        for key, value in fields.items():
            if value is not None and key not in {"job_id", "task_id", "created_at"}:
                out[key] = value
        out["updated_at"] = _now_iso()
        save_json(path, out)
        return out


def get_record(job_id: str) -> dict[str, Any] | None:
    jid = (job_id or "").strip()
    if not jid:
        return None
    with file_transaction(LEDGER_DIR / "ledger"):
        path = _path(jid)
        if not path.is_file():
            return None
        row = load_json(path, default=None)
        return dict(row) if isinstance(row, dict) else None


def delete_record(job_id: str) -> bool:
    """Remove the poll record after success or failure."""
    jid = (job_id or "").strip()
    if not jid:
        return False
    with file_transaction(LEDGER_DIR / "ledger"):
        path = _path(jid)
        if not path.is_file():
            return False
        try:
            path.unlink()
            logger.info("poll ledger delete job=%s", jid[:12])
            return True
        except OSError as exc:
            logger.warning("poll ledger delete failed %s: %s", jid[:12], exc)
            return False


def list_records(limit: int = 500) -> list[dict[str, Any]]:
    with file_transaction(LEDGER_DIR / "ledger"):
        if not LEDGER_DIR.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(LEDGER_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            if path.name.endswith(".tmp"):
                continue
            row = load_json(path, default=None)
            if isinstance(row, dict) and row.get("job_id"):
                out.append(dict(row))
                if len(out) >= limit:
                    break
        return out
