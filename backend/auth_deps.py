"""Auth helpers for canvas and admin APIs."""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend import app_users
from backend.user_scope import set_current_user_id

AUTH_EXEMPT_PREFIXES = (
    "/api/auth/",
    "/api/app-config",
    "/api/ping",
    "/api/runtime",
    "/api/upscale/worker/",
    "/api/internal/job-worker/",
    "/api/pay/notify",
    "/api/pay/return",
)

AUTH_EXEMPT_EXACT = frozenset({"/api/auth/login", "/api/auth/status", "/api/ping"})


def _extract_token(request: Request) -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = (request.cookies.get("canvas_token") or "").strip()
    return cookie or None


def is_auth_exempt(path: str) -> bool:
    if path in AUTH_EXEMPT_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES)


async def apply_user_scope(request: Request) -> str | None:
    """Single local operator. Login tokens are ignored."""
    del request
    user = app_users.local_operator()
    uid = str(user.get("id") or "").strip()
    set_current_user_id(uid or None)
    return uid or None


def require_login_user() -> dict:
    token_user = None
    from backend.user_scope import get_current_user_id

    uid = get_current_user_id()
    if not uid:
        raise HTTPException(401, "请先登录")
    user = app_users.get_user(uid)
    if not user or not user.get("enabled", True):
        raise HTTPException(401, "账号已禁用或不存在")
    return user


def require_admin_user() -> dict:
    user = require_login_user()
    if (user.get("role") or "").lower() != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def user_id_from_request(request: Request) -> str | None:
    token = _extract_token(request)
    sess = app_users.validate_session(token)
    return str(sess["user_id"]) if sess else None


def attach_session_cookie(response, token: str | None) -> None:
    tok = (token or "").strip()
    if not tok:
        return
    response.set_cookie(
        key="canvas_token",
        value=tok,
        httponly=True,
        samesite="lax",
        max_age=app_users.SESSION_DAYS * 86400,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key="canvas_token", path="/")
