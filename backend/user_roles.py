"""Canvas user roles and browser-channel access."""

from __future__ import annotations

ROLE_USER = "user"
ROLE_INTERNAL = "internal"
ROLE_ADMIN = "admin"

_ROLE_ALIASES = {
    "user": ROLE_USER,
    "canvas": ROLE_USER,
    "画布用户": ROLE_USER,
    "admin": ROLE_ADMIN,
    "administrator": ROLE_ADMIN,
    "管理员": ROLE_ADMIN,
    "internal": ROLE_INTERNAL,
    "staff": ROLE_INTERNAL,
    "内部": ROLE_INTERNAL,
    "内部人员": ROLE_INTERNAL,
}


def normalize_role(role: str | None) -> str:
    key = (role or "").strip().lower()
    if not key:
        return ROLE_USER
    return _ROLE_ALIASES.get(key, ROLE_USER)


def role_label(role: str | None) -> str:
    r = normalize_role(role)
    if r == ROLE_ADMIN:
        return "管理员"
    if r == ROLE_INTERNAL:
        return "内部人员"
    return "画布用户"


def can_use_browser_channels(role: str | None) -> bool:
    """Doubao browser simulator — admin & internal only."""
    return normalize_role(role) in (ROLE_ADMIN, ROLE_INTERNAL)


def can_use_bendi_channel(role: str | None) -> bool:
    """BenDi / 星晚AI 本机渠道 — admin only."""
    return normalize_role(role) == ROLE_ADMIN


def is_admin_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_ADMIN
