"""In-memory recent logs for the canvas log popover (scoped per user + board)."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque

from backend.board_assets import get_board_id
from backend.user_scope import get_current_user_id

_MAX_PER_SCOPE = 200
_SKIP_ACCESS = re.compile(
    r'"(GET|PUT) /(api/(tasks|accounts|canvas)|uploads)/',
    re.I,
)
_SKIP_WORKER = re.compile(r"/api/upscale/worker/", re.I)

_lock = threading.Lock()
_buckets: dict[tuple[str, str], deque[dict]] = defaultdict(
    lambda: deque(maxlen=_MAX_PER_SCOPE)
)
_seq_by_scope: dict[tuple[str, str], int] = defaultdict(int)


def _scope_key(user_id: str | None = None, board_id: str | None = None) -> tuple[str, str] | None:
    uid = (user_id or get_current_user_id() or "").strip()
    if not uid:
        return None
    bid = (board_id if board_id is not None else get_board_id() or "").strip()
    return uid, bid


def _should_keep(record: logging.LogRecord, formatted: str) -> bool:
    if _SKIP_WORKER.search(formatted):
        return False
    if record.levelno >= logging.WARNING:
        return True
    if record.name.startswith("uvicorn.access") and _SKIP_ACCESS.search(formatted):
        return False
    if record.name.startswith(("backend", "uvicorn.error")):
        return True
    if record.name.startswith("httpx") and "POST " in formatted:
        return True
    return False


class BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        if not _should_keep(record, msg):
            return
        key = _scope_key()
        if not key:
            return
        with _lock:
            _seq_by_scope[key] += 1
            seq = _seq_by_scope[key]
            _buckets[key].append(
                {
                    "id": seq,
                    "ts": time.strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name.replace("browser_channels.", "").replace("backend.", ""),
                    "message": msg,
                    "user_id": key[0],
                    "board_id": key[1],
                }
            )


def install() -> None:
    root = logging.getLogger()
    if any(isinstance(h, BufferHandler) for h in root.handlers):
        return
    handler = BufferHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)


def snapshot(
    *,
    user_id: str,
    board_id: str = "",
    since_id: int = 0,
    limit: int = 200,
) -> dict:
    key = _scope_key(user_id, board_id)
    if not key:
        return {"logs": [], "last_id": 0}
    with _lock:
        items = [e for e in _buckets.get(key, ()) if e["id"] > since_id]
        last_id = _seq_by_scope.get(key, 0)
    if len(items) > limit:
        items = items[-limit:]
    return {"logs": items, "last_id": last_id}


def clear(*, user_id: str, board_id: str = "") -> None:
    key = _scope_key(user_id, board_id)
    if not key:
        return
    with _lock:
        if key in _buckets:
            _buckets[key].clear()
        _seq_by_scope[key] = 0
