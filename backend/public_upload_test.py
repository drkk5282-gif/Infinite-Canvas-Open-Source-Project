"""Probe public /uploads/ reachability for admin settings."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import httpx

from backend import service_settings
from backend.board_assets import public_url
from backend.config import DATA_DIR, UPLOADS_DIR


def _iter_upload_public_paths() -> list[str]:
    """Collect /uploads/... paths for files on disk."""
    paths: list[str] = []
    if UPLOADS_DIR.is_dir():
        for item in UPLOADS_DIR.iterdir():
            if item.is_file() and not item.name.startswith("."):
                paths.append(f"/uploads/{item.name}")

    users_root = DATA_DIR / "users"
    if not users_root.is_dir():
        return paths

    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        slug = user_dir.name
        for child in user_dir.iterdir():
            if not child.is_dir() or child.name in ("boards", "accounts"):
                continue
            assets = child / "assets"
            if assets.is_dir():
                for asset in assets.iterdir():
                    if asset.is_file() and not asset.name.startswith("."):
                        paths.append(public_url(slug, child.name, asset.name))
        boards_root = user_dir / "boards"
        if boards_root.is_dir():
            for board_dir in boards_root.iterdir():
                assets = board_dir / "assets"
                if not assets.is_dir():
                    continue
                for asset in assets.iterdir():
                    if asset.is_file() and not asset.name.startswith("."):
                        paths.append(public_url(slug, board_dir.name, asset.name))
    return paths


def _pick_random_upload_path() -> str | None:
    paths = _iter_upload_public_paths()
    if not paths:
        return None
    return random.choice(paths)


def test_public_upload_link(*, server_url: str, request=None) -> dict[str, Any]:
    """
    Build a random /uploads/ public URL and HTTP-probe it.
    Used by admin to verify public_base_url without a manual toggle.
    """
    base = (server_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("公网访问地址为空，请先填写并保存")

    # enrich port from request when omitted
    if request is not None:
        base = service_settings.enrich_public_url_with_request_port(base, request)

    upload_path = _pick_random_upload_path()
    if not upload_path:
        raise ValueError("暂无上传文件，请先在画布上传一张图片或视频后再测试")

    public_url_full = f"{base}{upload_path}"
    http_ok = False
    http_detail = ""
    content_type = ""
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            resp = client.head(public_url_full)
            if resp.status_code in (405, 501):
                resp = client.get(public_url_full)
            http_ok = resp.status_code < 400
            http_detail = f"HTTP {resp.status_code}"
            content_type = resp.headers.get("content-type", "")
    except Exception as exc:
        http_detail = str(exc)[:160]

    ok = bool(http_ok)
    message_parts: list[str] = []
    if ok:
        message_parts.append("公网可访问")
        if content_type:
            message_parts.append(content_type.split(";", 1)[0])
        # Self-probe ≠ Agnes/overseas fetchers can reach nonstandard ports.
        try:
            from urllib.parse import urlparse

            port = urlparse(public_url_full).port
            if port and port not in (80, 443):
                message_parts.append(
                    f"注意:端口{port}本机可通，海外上游(如Agnes)仍可能拉不到，建议443反代"
                )
        except Exception:
            pass
    else:
        message_parts.append(f"无法访问（{http_detail}）")
        message_parts.append("请检查反向代理、防火墙或 public_base_url 是否与浏览器地址栏一致")

    return {
        "ok": ok,
        "public_base_url": base,
        "upload_path": upload_path,
        "test_url": public_url_full,
        "http_detail": http_detail,
        "content_type": content_type,
        "message": " · ".join(message_parts),
    }
