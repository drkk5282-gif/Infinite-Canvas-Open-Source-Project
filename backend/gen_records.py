"""Persistent generation audit log (data/gen_records.json).

Each node generation gets a work id (= task id). Stores status and raw upstream
errors/responses for admin debugging. Only records created after this feature
ship are kept; older generations are not backfilled.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from backend.config import GEN_RECORDS_FILE
from backend.generation_errors import compact_diagnostic
from backend.json_io import load_json, save_json

_LOCK = threading.Lock()
_MAX_RECORDS = 5000
_MAX_TEXT = 65536
_MAX_PROMPT = 800


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _truncate_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(truncated, total {len(text)} chars)"


def _truncate_prompt(value: Any) -> str:
    return _truncate_text(value, limit=_MAX_PROMPT)


def _normalize_raw_response(value: Any) -> Any:
    return compact_diagnostic(value)


def _ensure_file() -> None:
    GEN_RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not GEN_RECORDS_FILE.exists():
        save_json(GEN_RECORDS_FILE, {"records": []})


def _load_records() -> list[dict[str, Any]]:
    _ensure_file()
    raw = load_json(GEN_RECORDS_FILE, default={"records": []})
    if not isinstance(raw, dict):
        return []
    rows = raw.get("records")
    return list(rows) if isinstance(rows, list) else []


def _save_records(records: list[dict[str, Any]]) -> None:
    _ensure_file()
    save_json(GEN_RECORDS_FILE, {"records": records})


def _public_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "username": row.get("username"),
        "board_id": row.get("board_id"),
        "node_id": row.get("node_id"),
        "account_id": row.get("account_id"),
        "account_name": row.get("account_name"),
        "kind": row.get("kind"),
        "channel": row.get("channel"),
        "model": row.get("model"),
        "status": row.get("status"),
        "error": row.get("error"),
        "prompt": row.get("prompt"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "completed_at": row.get("completed_at"),
        "error_type": row.get("error_type"),
        "upstream_task_id": row.get("upstream_task_id"),
        "upstream_status": row.get("upstream_status"),
        "upstream_stage": row.get("upstream_stage"),
        "last_poll_at": row.get("last_poll_at"),
        "has_raw": bool(row.get("raw_error") or row.get("raw_response")),
    }


def upsert_record(record_id: str, **fields: Any) -> dict[str, Any]:
    """Insert or merge a generation record by task/work id."""
    rid = (record_id or "").strip()
    if not rid:
        raise ValueError("record id required")

    now = _now_iso()
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "prompt":
            clean[key] = _truncate_prompt(value)
        elif key == "raw_error":
            clean[key] = _truncate_text(value)
        elif key == "raw_response":
            clean[key] = _normalize_raw_response(value)
        elif key == "error":
            clean[key] = _truncate_text(value, limit=4096)
        else:
            clean[key] = value

    with _LOCK:
        records = _load_records()
        idx = next((i for i, row in enumerate(records) if str(row.get("id") or "") == rid), -1)
        if idx >= 0:
            merged = dict(records[idx])
            merged.update(clean)
            merged["id"] = rid
            merged.setdefault("created_at", now)
            merged["updated_at"] = now
            records[idx] = merged
            out = merged
        else:
            row = {"id": rid, "created_at": now, "updated_at": now, **clean}
            records.insert(0, row)
            out = row
        if len(records) > _MAX_RECORDS:
            records = records[:_MAX_RECORDS]
        _save_records(records)
        return dict(out)


def get_record(record_id: str) -> Optional[dict[str, Any]]:
    rid = (record_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        for row in _load_records():
            if str(row.get("id") or "") == rid:
                return dict(row)
    return None


def _parse_date_bound(value: str | None, *, end: bool = False) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text}T{'23:59:59.999999' if end else '00:00:00'}"
    return text


def list_records(
    *,
    q: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
    user_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    needle = (q or "").strip().lower()
    want_status = (status or "").strip().lower()
    want_account = (account_id or "").strip()
    want_user = (user_id or "").strip()
    bound_from = _parse_date_bound(date_from, end=False)
    bound_to = _parse_date_bound(date_to, end=True)
    limit = max(1, min(500, int(limit or 20)))
    offset = max(0, int(offset or 0))

    with _LOCK:
        rows = _load_records()

    filtered: list[dict[str, Any]] = []
    for row in rows:
        if want_status and str(row.get("status") or "").lower() != want_status:
            continue
        if want_account and str(row.get("account_id") or "") != want_account:
            continue
        if want_user and str(row.get("user_id") or "") != want_user:
            continue
        created = str(row.get("created_at") or "")
        if bound_from and created and created < bound_from:
            continue
        if bound_to and created and created > bound_to:
            continue
        if needle:
            hay = " ".join(
                str(row.get(k) or "")
                for k in (
                    "id",
                    "username",
                    "user_id",
                    "account_id",
                    "account_name",
                    "node_id",
                    "board_id",
                    "prompt",
                    "error",
                    "raw_error",
                )
            ).lower()
            if needle not in hay:
                continue
        filtered.append(row)

    total = len(filtered)
    stats = {
        "generating": sum(
            1 for r in filtered if str(r.get("status") or "").lower() in ("pending", "generating")
        ),
        "completed": sum(1 for r in filtered if str(r.get("status") or "").lower() == "completed"),
        "failed": sum(1 for r in filtered if str(r.get("status") or "").lower() == "failed"),
    }
    page = filtered[offset : offset + limit]
    return [_public_summary(r) for r in page], total, stats


def detail_view(record_id: str) -> Optional[dict[str, Any]]:
    row = get_record(record_id)
    if not row:
        return None
    out = _public_summary(row)
    out["raw_error"] = row.get("raw_error")
    out["raw_response"] = row.get("raw_response")
    out["outbound_request"] = row.get("outbound_request")
    return out


_LIVE_STATUSES = frozenset({"pending", "generating"})


def _is_live_record(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() in _LIVE_STATUSES


def purge_records(*, mode: str, keep: int = 0) -> dict[str, int]:
    """Delete audit rows. mode: failed | all | keep (keep newest N).

    Generating / queued rows are never removed.
    """
    action = (mode or "").strip().lower()
    keep_n = max(0, min(5000, int(keep or 0)))
    with _LOCK:
        records = _load_records()
        before = len(records)
        live = [r for r in records if _is_live_record(r)]
        done = [r for r in records if not _is_live_record(r)]
        if action == "failed":
            records = live + [r for r in done if str(r.get("status") or "").lower() != "failed"]
        elif action == "all":
            records = live
        elif action == "keep":
            if keep_n <= 0:
                raise ValueError("keep 须为正整数")
            newest = sorted(
                records,
                key=lambda r: str(r.get("created_at") or ""),
                reverse=True,
            )[:keep_n]
            kept_ids = {str(r.get("id") or "") for r in newest}
            extras = [r for r in live if str(r.get("id") or "") not in kept_ids]
            records = extras + newest
        else:
            raise ValueError("mode 须为 failed / all / keep")
        _save_records(records)
        return {
            "deleted": before - len(records),
            "remaining": len(records),
            "kept_live": len(live),
        }
