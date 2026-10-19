"""Canvas login users (admin manages via admin port)."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json
from backend.user_roles import can_use_browser_channels, normalize_role, role_label
from backend.user_scope import DEFAULT_USER_ID, accounts_index, myapi_file, service_config_file

logger = logging.getLogger(__name__)

_POINTS_QUANT = Decimal("0.001")
_POINTS_ZERO = Decimal("0.000")


def _normalize_points(value: Any) -> str:
    raw = "0.000" if value is None else str(value).strip()
    if not raw:
        raw = "0.000"
    try:
        d = Decimal(raw)
    except (InvalidOperation, ValueError):
        d = _POINTS_ZERO
    if d < _POINTS_ZERO:
        d = _POINTS_ZERO
    return str(d.quantize(_POINTS_QUANT, rounding=ROUND_HALF_UP))

USERS_FILE = DATA_DIR / "app_users.json"
SESSIONS_FILE = DATA_DIR / "app_sessions.json"
SESSION_DAYS = 14

_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex()


def _load_users() -> dict[str, Any]:
    raw = load_json(USERS_FILE, default={"users": []})
    if not isinstance(raw, dict):
        return {"users": []}
    users = raw.get("users")
    if not isinstance(users, list):
        return {"users": []}
    return {"users": users}


def _save_users(users: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(USERS_FILE, {"users": users})


def _load_sessions() -> dict[str, Any]:
    raw = load_json(SESSIONS_FILE, default={"sessions": {}})
    if not isinstance(raw, dict):
        return {"sessions": {}}
    sessions = raw.get("sessions")
    if not isinstance(sessions, dict):
        return {"sessions": {}}
    return {"sessions": sessions}


def _save_sessions(sessions: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(SESSIONS_FILE, {"sessions": sessions})


def _prune_sessions(sessions: dict[str, Any]) -> None:
    now = datetime.now()
    dead = []
    for token, row in sessions.items():
        exp = row.get("expires_at")
        try:
            if exp and datetime.fromisoformat(str(exp)) < now:
                dead.append(token)
        except ValueError:
            dead.append(token)
    for token in dead:
        sessions.pop(token, None)


def _read_users_rows() -> list[dict[str, Any]]:
    raw = load_json(USERS_FILE, default={"users": []})
    users = raw.get("users") if isinstance(raw, dict) else []
    return [u for u in users if isinstance(u, dict)]


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        rows = _read_users_rows()
    out = []
    for row in rows:
        role = normalize_role(row.get("role"))
        out.append(
            {
                "id": row.get("id"),
                "username": row.get("username"),
                "display_name": row.get("display_name") or row.get("username"),
                "role": role,
                "role_label": role_label(role),
                "browser_channels_allowed": can_use_browser_channels(role),
                "enabled": bool(row.get("enabled", True)),
                "points": _normalize_points(row.get("points")),
                "created_at": row.get("created_at"),
                "last_login_at": row.get("last_login_at"),
            }
        )
    return out


def get_user(user_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    for row in _read_users_rows():
        if row.get("id") == uid:
            return _public_user(row)
    return None


def get_user_by_username(username: str) -> dict[str, Any] | None:
    name = (username or "").strip().lower()
    with _LOCK:
        for row in _load_users()["users"]:
            if not isinstance(row, dict):
                continue
            if str(row.get("username") or "").strip().lower() == name:
                return dict(row)
    return None


def _avatar_public_url(row: dict[str, Any]) -> str | None:
    uid = str(row.get("id") or "").strip()
    if not uid:
        return None
    avatar_file = DATA_DIR / "users" / uid / "avatar.jpg"
    if not avatar_file.is_file():
        return None
    try:
        ver = int(avatar_file.stat().st_mtime_ns)
    except OSError:
        ver = 0
    return f"/api/auth/avatar/{uid}?v={ver}"


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    role = normalize_role(row.get("role"))
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "display_name": row.get("display_name") or row.get("username"),
        "avatar_url": _avatar_public_url(row),
        "role": role,
        "role_label": role_label(role),
        "browser_channels_allowed": can_use_browser_channels(role),
        "enabled": bool(row.get("enabled", True)),
        "points": _normalize_points(row.get("points")),
    }


def create_user(
    username: str,
    password: str,
    *,
    display_name: str | None = None,
    role: str = "user",
    points: Any = "0.000",
) -> dict[str, Any]:
    name = (username or "").strip()
    if len(name) < 2:
        raise ValueError("用户名至少 2 个字符")
    if len(password or "") < 4:
        raise ValueError("密码至少 4 个字符")
    if get_user_by_username(name):
        raise ValueError("用户名已存在")
    salt = secrets.token_hex(16)
    row = {
        "id": uuid.uuid4().hex[:12],
        "username": name,
        "display_name": (display_name or name).strip()[:40],
        "role": normalize_role(role),
        "enabled": True,
        "points": _normalize_points(points),
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "created_at": _now_iso(),
        "last_login_at": None,
    }
    with _LOCK:
        data = _load_users()
        data["users"].append(row)
        _save_users(data["users"])
    user_data_root = DATA_DIR / "users" / row["id"]
    user_data_root.mkdir(parents=True, exist_ok=True)
    logger.info("created app user %s (%s)", row["username"], row["id"])
    return _public_user(row)


def update_user(
    user_id: str,
    *,
    display_name: str | None = None,
    enabled: bool | None = None,
    password: str | None = None,
    points: Any = None,
    role: str | None = None,
    avatar_url: str | None = None,
    clear_avatar: bool = False,
) -> dict[str, Any]:
    uid = (user_id or "").strip()
    with _LOCK:
        data = _load_users()
        users = data["users"]
        for row in users:
            if not isinstance(row, dict) or row.get("id") != uid:
                continue
            if display_name is not None:
                row["display_name"] = str(display_name).strip()[:40] or row.get("username")
            if enabled is not None:
                row["enabled"] = bool(enabled)
            if role is not None:
                row["role"] = normalize_role(role)
            if points is not None:
                row["points"] = _normalize_points(points)
            if clear_avatar:
                row.pop("avatar_url", None)
                avatar_path = DATA_DIR / "users" / uid / "avatar.jpg"
                try:
                    if avatar_path.is_file():
                        avatar_path.unlink()
                except OSError:
                    pass
            elif avatar_url is not None:
                row["avatar_url"] = str(avatar_url).strip()[:240]
            if password:
                if len(password) < 4:
                    raise ValueError("密码至少 4 个字符")
                salt = secrets.token_hex(16)
                row["salt"] = salt
                row["password_hash"] = _hash_password(password, salt)
            _save_users(users)
            return _public_user(row)
    raise KeyError(user_id)


def save_user_avatar_bytes(user_id: str, data: bytes, *, content_type: str = "") -> dict[str, Any]:
    """Persist avatar image bytes for a user and return updated public user."""
    import io

    from PIL import Image

    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("无效用户")
    if not data or len(data) > 6 * 1024 * 1024:
        raise ValueError("头像需小于 6MB")
    ctype = (content_type or "").lower()
    if ctype and not ctype.startswith("image/"):
        raise ValueError("请上传图片文件")
    root = DATA_DIR / "users" / uid
    root.mkdir(parents=True, exist_ok=True)
    path = root / "avatar.jpg"
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            w, h = img.size
            # Non-square uploads (legacy / skipped crop UI) → center square.
            if w != h and w > 0 and h > 0:
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
                w = h = side
            max_side = 512
            if w > max_side:
                img = img.resize((max_side, max_side), Image.Resampling.LANCZOS)
            img.save(path, format="JPEG", quality=90, optimize=True)
    except Exception as exc:
        raise ValueError("无法解析头像图片，请换一张 JPG / PNG") from exc
    return update_user(uid, avatar_url=f"/api/auth/avatar/{uid}")


def get_points(user_id: str) -> Decimal:
    uid = (user_id or "").strip()
    with _LOCK:
        for row in _load_users()["users"]:
            if isinstance(row, dict) and row.get("id") == uid:
                return Decimal(_normalize_points(row.get("points")))
    return _POINTS_ZERO


def set_points(user_id: str, value: Any) -> Decimal:
    uid = (user_id or "").strip()
    normalized = _normalize_points(value)
    with _LOCK:
        data = _load_users()
        for row in data["users"]:
            if isinstance(row, dict) and row.get("id") == uid:
                row["points"] = normalized
                _save_users(data["users"])
                return Decimal(normalized)
    raise KeyError(user_id)


def adjust_points(user_id: str, delta: Decimal | Any) -> Decimal:
    uid = (user_id or "").strip()
    try:
        d = Decimal(str(delta))
    except (InvalidOperation, ValueError):
        d = _POINTS_ZERO
    d = d.quantize(_POINTS_QUANT, rounding=ROUND_HALF_UP)
    with _LOCK:
        data = _load_users()
        for row in data["users"]:
            if isinstance(row, dict) and row.get("id") == uid:
                cur = Decimal(_normalize_points(row.get("points")))
                nxt = cur + d
                if nxt < _POINTS_ZERO:
                    nxt = _POINTS_ZERO
                row["points"] = str(nxt.quantize(_POINTS_QUANT, rounding=ROUND_HALF_UP))
                _save_users(data["users"])
                return Decimal(row["points"])
    raise KeyError(user_id)


def delete_user(user_id: str) -> None:
    uid = (user_id or "").strip()
    if uid == DEFAULT_USER_ID:
        raise ValueError("不能删除默认用户")
    with _LOCK:
        data = _load_users()
        before = len(data["users"])
        data["users"] = [u for u in data["users"] if not (isinstance(u, dict) and u.get("id") == uid)]
        if len(data["users"]) == before:
            raise KeyError(user_id)
        _save_users(data["users"])
        sessions = _load_sessions()["sessions"]
        for token, row in list(sessions.items()):
            if row.get("user_id") == uid:
                sessions.pop(token, None)
        _save_sessions(sessions)
    root = DATA_DIR / "users" / uid
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    logger.info("deleted app user %s", uid)


def _find_user_row(user_id: str) -> dict[str, Any] | None:
    key = (user_id or "").strip()
    if not key:
        return None
    for row in _read_users_rows():
        if not isinstance(row, dict):
            continue
        if row.get("id") == key or str(row.get("username") or "").strip() == key:
            return row
    return None


def get_upscale_worker_token(user_id: str) -> str:
    row = _find_user_row(user_id)
    return str(row.get("upscale_worker_token") or "").strip() if row else ""


def regenerate_upscale_token(user_id: str) -> str:
    uid = (user_id or "").strip()
    tok = secrets.token_urlsafe(24)
    with _LOCK:
        data = _load_users()
        found = False
        for row in data["users"]:
            if isinstance(row, dict) and row.get("id") == uid:
                row["upscale_worker_token"] = tok
                found = True
                break
        if not found:
            row = _find_user_row(uid)
            if not row:
                raise KeyError(user_id)
            for r in data["users"]:
                if isinstance(r, dict) and r.get("id") == row.get("id"):
                    r["upscale_worker_token"] = tok
                    found = True
                    break
        if not found:
            raise KeyError(user_id)
        _save_users(data["users"])
    return tok


def verify_password(username: str, password: str) -> dict[str, Any] | None:
    row = get_user_by_username(username)
    if not row or not row.get("enabled", True):
        return None
    salt = str(row.get("salt") or "")
    expected = str(row.get("password_hash") or "")
    if not salt or not expected:
        return None
    if _hash_password(password or "", salt) != expected:
        return None
    return row


def create_session(user_id: str, *, portal: str = "canvas") -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=SESSION_DAYS)
    with _LOCK:
        data = _load_sessions()
        sessions = data["sessions"]
        _prune_sessions(sessions)
        sessions[token] = {
            "user_id": user_id,
            "portal": portal,
            "created_at": _now_iso(),
            "expires_at": expires.isoformat(timespec="seconds"),
        }
        _save_sessions(sessions)
        users = _load_users()["users"]
        for row in users:
            if isinstance(row, dict) and row.get("id") == user_id:
                row["last_login_at"] = _now_iso()
                break
        _save_users(users)
    user = get_user(user_id)
    return {
        "token": token,
        "expires_at": expires.isoformat(timespec="seconds"),
        "user": user,
    }


def validate_session(token: str | None) -> dict[str, Any] | None:
    tok = (token or "").strip()
    if not tok:
        return None
    with _LOCK:
        data = _load_sessions()
        sessions = data["sessions"]
        before_n = len(sessions)
        _prune_sessions(sessions)
        dirty = len(sessions) != before_n
        row = sessions.get(tok)
        if not row:
            if dirty:
                _save_sessions(sessions)
            return None
        exp = row.get("expires_at")
        try:
            if exp and datetime.fromisoformat(str(exp)) < datetime.now():
                sessions.pop(tok, None)
                _save_sessions(sessions)
                return None
        except ValueError:
            sessions.pop(tok, None)
            _save_sessions(sessions)
            return None
        # Valid session: do not rewrite the file every request (Windows lock races).
        if dirty:
            _save_sessions(sessions)
        user = get_user(str(row.get("user_id") or ""))
        if not user or not user.get("enabled", True):
            return None
        return {"token": tok, "user_id": user["id"], "user": user, "portal": row.get("portal")}


def revoke_session(token: str | None) -> None:
    tok = (token or "").strip()
    if not tok:
        return
    with _LOCK:
        data = _load_sessions()
        sessions = data["sessions"]
        sessions.pop(tok, None)
        _save_sessions(sessions)


def users_configured() -> bool:
    if not USERS_FILE.is_file():
        return False
    raw = load_json(USERS_FILE, default={"users": []})
    users = raw.get("users") if isinstance(raw, dict) else []
    return bool(users)


def _migrate_legacy_to_default() -> None:
    """Move global accounts/settings/myapi into users/default/."""
    dest_root = DATA_DIR / "users" / DEFAULT_USER_ID
    dest_root.mkdir(parents=True, exist_ok=True)
    legacy_map = [
        (DATA_DIR / "accounts.json", accounts_index(DEFAULT_USER_ID)),
        (DATA_DIR / "service_config.json", service_config_file(DEFAULT_USER_ID)),
        (DATA_DIR / "myapi_models.json", myapi_file(DEFAULT_USER_ID)),
        (DATA_DIR / "tasks.json", dest_root / "tasks.json"),
        (DATA_DIR / "canvas.json", dest_root / "canvas.json"),
    ]
    for src, dst in legacy_map:
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            logger.info("migrated %s → %s", src.name, dst)


def local_operator() -> dict[str, Any]:
    """The only operator. No login screen; data stays on the user who already has a canvas."""
    ensure_bootstrap()
    users = [u for u in list_users() if u.get("enabled", True) and u.get("id")]
    best: dict[str, Any] | None = None
    best_mtime = -1.0
    for user in users:
        uid = str(user.get("id") or "")
        canvas = DATA_DIR / "users" / uid / "canvas.json"
        try:
            mtime = canvas.stat().st_mtime if canvas.is_file() else 0.0
        except OSError:
            mtime = 0.0
        if best is None or mtime >= best_mtime:
            best = user
            best_mtime = mtime
    if best:
        row = get_user(str(best["id"]))
        if row:
            return row
    created = create_user("local", secrets.token_hex(8), display_name="管理员", role="admin")
    row = get_user(str(created["id"]))
    return row or created


def ensure_bootstrap() -> dict[str, Any] | None:
    """First run: create admin + default user, migrate legacy data."""
    if users_configured():
        _migrate_legacy_to_default()
        return None
    with _LOCK:
        if users_configured():
            _migrate_legacy_to_default()
            return None
    _migrate_legacy_to_default()
    admin_password = (os.getenv("CANVAS_ADMIN_PASSWORD") or "admin123").strip()
    default_password = (os.getenv("CANVAS_DEFAULT_PASSWORD") or "canvas123").strip()
    admin = create_user("admin", admin_password, display_name="管理员", role="admin")
    default_user = create_user("default", default_password, display_name="默认用户", role="user")
    logger.warning(
        "首次启动：已创建管理员 admin 与默认用户 default，请尽快修改密码并在后台创建正式账号"
    )
    return {
        "admin": {"username": admin["username"], "password": admin_password},
        "default": {"username": default_user["username"], "password": default_password},
        "admin_port_hint": "",
    }
