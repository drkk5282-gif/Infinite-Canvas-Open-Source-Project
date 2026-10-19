"""Upscale worker connection links (paste into local UpscaleWorker app)."""

from __future__ import annotations

import json
import secrets
import urllib.parse
from typing import Any

from backend import app_users, service_settings


def pool_mode() -> str:
    mode = str(service_settings.load().get("upscale_pool_mode") or "shared").strip().lower()
    return "per_user" if mode == "per_user" else "shared"


def ensure_shared_token() -> str:
    data = service_settings.load()
    tok = str(data.get("upscale_worker_token") or "").strip()
    if tok:
        return tok
    tok = secrets.token_urlsafe(24)
    data["upscale_worker_token"] = tok
    service_settings.save(data)
    return tok


def ensure_user_token(user_id: str) -> str:
    user = app_users.get_user(user_id) or app_users.get_user_by_username(user_id)
    if not user:
        raise KeyError("用户不存在")
    uid = str(user.get("id") or "")
    tok = app_users.get_upscale_worker_token(uid)
    if tok:
        return tok
    return app_users.regenerate_upscale_token(uid)


def build_link(*, server_url: str, token: str, username: str = "") -> str:
    base = (server_url or "").strip().rstrip("/")
    params: dict[str, str] = {"s": base, "t": token}
    if username:
        params["u"] = username
    q = urllib.parse.urlencode(params)
    return f"qingzi-upscale://connect?{q}"


def build_link_for_user(user_id: str, server_url: str) -> str:
    mode = pool_mode()
    if mode == "per_user":
        user = app_users.get_user(user_id)
        if not user:
            raise KeyError("用户不存在")
        token = ensure_user_token(user_id)
        return build_link(server_url=server_url, token=token, username=str(user.get("username") or ""))
    token = ensure_shared_token()
    return build_link(server_url=server_url, token=token)


def parse_link(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("连接链接为空")
    if text.startswith("qingzi-upscale://") or text.startswith("doubao-upscale://"):
        parsed = urllib.parse.urlparse(text)
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        server = (q.get("s") or q.get("server") or [""])[0]
        token = (q.get("t") or q.get("token") or [""])[0]
        user = (q.get("u") or q.get("user") or [""])[0]
        return {
            "server_url": urllib.parse.unquote(server).rstrip("/"),
            "token": token,
            "username": user,
        }
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON 链接格式无效")
        return {
            "server_url": str(data.get("server_url") or data.get("server") or "").rstrip("/"),
            "token": str(data.get("token") or ""),
            "username": str(data.get("username") or data.get("user") or ""),
        }
    raise ValueError("无法识别的连接链接，请粘贴完整链接")


def verify_worker_token(token: str | None, *, user_scope: str | None = None) -> bool:
    supplied = str(token or "").strip()
    mode = pool_mode()
    scope = str(user_scope or "").strip()
    if mode == "per_user" and scope:
        user = app_users.get_user(scope) or app_users.get_user_by_username(scope)
        if not user:
            return False
        expected = app_users.get_upscale_worker_token(str(user.get("id") or scope))
        if not expected:
            return False
        return supplied == expected
    expected = str(service_settings.get_upscale_worker_token() or "").strip()
    if not expected:
        return True
    return supplied == expected


def admin_status(server_url: str) -> dict[str, Any]:
    from backend.media.upscale_queue import worker_status

    mode = pool_mode()
    cfg = service_settings.load()
    shared_token = ensure_shared_token() if mode == "shared" else str(cfg.get("upscale_worker_token") or "")
    users = []
    for row in app_users.list_users():
        uid = str(row.get("id") or "")
        uname = str(row.get("username") or "")
        utok = app_users.get_upscale_worker_token(uid)
        link = ""
        if mode == "per_user" and uid:
            if not utok:
                utok = ensure_user_token(uid)
            link = build_link(server_url=server_url, token=utok, username=uname)
        users.append(
            {
                "id": uid,
                "username": uname,
                "display_name": row.get("display_name"),
                "token": utok,
                "link": link,
            }
        )
    remote = worker_status()
    return {
        "pool_mode": mode,
        "upscale_enabled": bool(cfg.get("upscale_enabled", True)),
        "upscale_remote_force": bool(cfg.get("upscale_remote_force")),
        "shared_token": shared_token,
        "shared_link": build_link(server_url=server_url, token=shared_token) if mode == "shared" else "",
        "users": users,
        "workers_online": int(remote.get("workers_online") or 0),
        "queue_pending": int(remote.get("queue_pending") or 0),
        "queue_active": int(remote.get("queue_active") or 0),
        "workers": remote.get("workers") or [],
    }


def test_worker_link(*, server_url: str, user_id: str | None = None) -> dict[str, Any]:
    """
    Validate an upscale connect link: parse, token check, optional HTTP probe.
    When user_id is omitted in per_user mode, pick a random canvas user.
    """
    import random

    import httpx

    mode = pool_mode()
    base = (server_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("服务器地址为空")

    picked_user: dict[str, Any] | None = None
    if mode == "per_user":
        users = [u for u in app_users.list_users() if str(u.get("id") or "").strip()]
        if not users:
            raise ValueError("没有画布用户")
        if user_id:
            picked_user = next((u for u in users if str(u.get("id")) == str(user_id)), None)
            if not picked_user:
                raise ValueError("用户不存在")
        else:
            picked_user = random.choice(users)
        uid = str(picked_user.get("id") or "")
        uname = str(picked_user.get("username") or "")
        token = ensure_user_token(uid)
        link = build_link(server_url=base, token=token, username=uname)
        scope = uname
    else:
        token = ensure_shared_token()
        link = build_link(server_url=base, token=token)
        scope = ""

    parsed = parse_link(link)
    token_ok = verify_worker_token(parsed.get("token"), user_scope=scope or None)
    probe_base = (parsed.get("server_url") or base).rstrip("/")
    http_ok = False
    http_detail = ""
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            probe_params: dict[str, str] = {"token": parsed.get("token") or token}
            if scope:
                probe_params["user_scope"] = scope
            resp = client.get(
                f"{probe_base}/api/upscale/worker/status",
                params=probe_params,
            )
            http_ok = resp.status_code < 400
            http_detail = f"HTTP {resp.status_code}"
    except Exception as exc:
        http_detail = str(exc)[:160]

    ok = bool(token_ok and http_ok)
    label = ""
    if picked_user:
        label = str(picked_user.get("display_name") or picked_user.get("username") or "")
    elif mode == "shared":
        label = "公用链接"

    message_parts = []
    if not token_ok:
        message_parts.append("Token 校验失败")
    if not http_ok:
        message_parts.append(f"服务器不可达（{http_detail}）")
    if ok:
        message_parts.append("链接格式与鉴权正常，Worker 接口可访问")

    return {
        "ok": ok,
        "pool_mode": mode,
        "link": link,
        "user_id": str(picked_user.get("id") or "") if picked_user else "",
        "username": str(picked_user.get("username") or "") if picked_user else "",
        "display_name": label,
        "token_valid": token_ok,
        "server_reachable": http_ok,
        "server_url": probe_base,
        "detail": http_detail,
        "message": " · ".join(message_parts) if message_parts else "测试完成",
    }
