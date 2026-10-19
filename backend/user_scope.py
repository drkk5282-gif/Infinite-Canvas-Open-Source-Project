"""Per-login-user data paths (canvas stays global)."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from backend.config import DATA_DIR

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
DEFAULT_USER_ID = "default"


def set_current_user_id(user_id: str | None) -> object:
    return _current_user_id.set((user_id or "").strip() or None)


def reset_current_user_id(token: object) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> str | None:
    return _current_user_id.get()


def require_current_user_id() -> str:
    uid = get_current_user_id()
    if not uid:
        raise PermissionError("请先登录")
    return uid


def storage_slug(user_id: str | None = None) -> str:
    """Folder name under data/users/ — prefer username (default, admin)."""
    uid = (user_id or get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID
    if uid == DEFAULT_USER_ID:
        return DEFAULT_USER_ID
    try:
        from backend import app_users

        row = app_users.get_user(uid)
        if row and row.get("username"):
            return str(row["username"]).strip()[:64] or uid
    except Exception:
        pass
    return uid


def user_data_root(user_id: str | None = None) -> Path:
    return (DATA_DIR / "users" / storage_slug(user_id)).resolve()


def accounts_index(user_id: str | None = None) -> Path:
    return (DATA_DIR / "accounts.json").resolve()


def service_config_file(user_id: str | None = None) -> Path:
    return (DATA_DIR / "service_config.json").resolve()


def myapi_file(user_id: str | None = None) -> Path:
    return user_data_root(user_id) / "myapi_models.json"


def volcengine_file(user_id: str | None = None) -> Path:
    return user_data_root(user_id) / "volcengine.json"


def tasks_file(user_id: str | None = None) -> Path:
    return user_data_root(user_id) / "tasks.json"
