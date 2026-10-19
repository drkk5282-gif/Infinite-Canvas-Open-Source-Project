"""Read/write JSON files — tolerate UTF-8 BOM from PowerShell / Windows editors."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Interrupted writes can leave a valid object plus trailing garbage.
        if "Extra data" not in str(exc):
            raise
        obj, end = json.JSONDecoder().raw_decode(text)
        logger.warning("JSON extra data ignored after char %s", end)
        return obj


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return default
    return _parse_json_text(text)


def save_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if indent is None:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=indent)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        last_exc: OSError | None = None
        for attempt in range(8):
            try:
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
                return
            except OSError as exc:
                last_exc = exc
                winerr = getattr(exc, "winerror", None)
                if winerr not in (5, 32) or attempt >= 7:
                    raise
                time.sleep(0.04 * (attempt + 1))
        if last_exc:
            raise last_exc
    finally:
        tmp.unlink(missing_ok=True)


def loads_json_bytes(raw: bytes, default: Any = None) -> Any:
    if not raw:
        return default
    text = raw.decode("utf-8-sig")
    if not text.strip():
        return default
    return _parse_json_text(text)
