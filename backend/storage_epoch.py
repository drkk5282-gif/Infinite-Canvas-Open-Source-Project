"""Detect replacement of a user's server-side data directory.

The marker deliberately lives inside the user data folder.  If that folder is
removed and later recreated, browsers receive a different value and can reject
snapshots that belonged to the previous storage generation.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from backend.user_scope import user_data_root

_VALID_EPOCH = re.compile(r"^[0-9a-f]{32}$")


def epoch_path(user_id: str) -> Path:
    return user_data_root(user_id) / ".storage_epoch"


def storage_epoch(user_id: str) -> str:
    path = epoch_path(user_id)
    try:
        current = path.read_text(encoding="ascii").strip()
        if _VALID_EPOCH.fullmatch(current):
            return current
    except (FileNotFoundError, OSError, UnicodeError):
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        value = secrets.token_hex(16)
        try:
            with path.open("x", encoding="ascii", newline="\n") as handle:
                handle.write(value)
            return value
        except FileExistsError:
            try:
                current = path.read_text(encoding="ascii").strip()
                if _VALID_EPOCH.fullmatch(current):
                    return current
            except (FileNotFoundError, OSError, UnicodeError):
                pass

    # Invalid marker files are replaced atomically. This path is exceptional;
    # normal requests only perform one tiny file read.
    value = secrets.token_hex(16)
    temporary = path.with_name(f"{path.name}.{value}.tmp")
    temporary.write_text(value, encoding="ascii")
    temporary.replace(path)
    return value


def with_storage_epoch(payload: dict, user_id: str) -> dict:
    return {**payload, "storageEpoch": storage_epoch(user_id)}
