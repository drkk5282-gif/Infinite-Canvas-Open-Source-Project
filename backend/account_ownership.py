"""Doubao/GPT channel account ownership and one-way pool sharing."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Optional

from backend.json_io import load_json, save_json
from backend.user_roles import ROLE_ADMIN, can_use_browser_channels, normalize_role

logger = logging.getLogger(__name__)

OWNER_MODE_ADMIN = "admin"
OWNER_MODE_SHARED_ALL = "shared_all"
OWNER_MODE_USER = "user"

_VALID_OWNER_MODES = frozenset({OWNER_MODE_ADMIN, OWNER_MODE_SHARED_ALL, OWNER_MODE_USER})

_shares_lock = threading.Lock()


def _shares_path():
    from backend.config import DATA_DIR as _DATA_DIR

    return (_DATA_DIR / "account_pool_shares.json").resolve()


def normalize_owner_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower()
    if m in ("all", "shared", "share_all", "everyone"):
        return OWNER_MODE_SHARED_ALL
    if m in _VALID_OWNER_MODES:
        return m
    return OWNER_MODE_ADMIN


def account_used_today(account: Any) -> bool:
    """True when the account has generation counts today (soft signal)."""
    return int(getattr(account, "image_count_today", 0) or 0) > 0 or int(
        getattr(account, "video_count_today", 0) or 0
    ) > 0


def account_unavailable_today(account: Any) -> bool:
    """True when the account is spent for today (次日 / daily quota), matching pool UI."""
    from backend import account_manager

    if account_manager.should_close_browser_until_next_day(account):
        return True
    return account_used_today(account)


def apply_owner_defaults(item: dict) -> dict:
    """Normalize ownership fields on a raw accounts.json row (in-place + return)."""
    if not isinstance(item, dict):
        return item
    mode = normalize_owner_mode(item.get("owner_mode"))
    item["owner_mode"] = mode
    uid = str(item.get("owner_user_id") or "").strip() or None
    if mode != OWNER_MODE_USER:
        item["owner_user_id"] = None
    else:
        item["owner_user_id"] = uid
    shares = item.get("shared_with_user_ids")
    if not isinstance(shares, list):
        item["shared_with_user_ids"] = []
    else:
        item["shared_with_user_ids"] = [
            str(x).strip() for x in shares if str(x or "").strip()
        ]
    return item


def owner_label_for_account(account: Any, *, users_by_id: dict[str, dict] | None = None) -> str:
    mode = normalize_owner_mode(getattr(account, "owner_mode", None))
    if mode == OWNER_MODE_SHARED_ALL:
        return "所有共享"
    if mode == OWNER_MODE_ADMIN:
        return "管理员号池"
    uid = str(getattr(account, "owner_user_id", None) or "").strip()
    if not uid:
        return "管理员号池"
    row = None
    if users_by_id is not None:
        row = users_by_id.get(uid)
    else:
        try:
            from backend import app_users

            row = app_users.get_user(uid)
        except Exception:
            row = None
    if not row:
        return f"用户 {uid[:8]}"
    return str(row.get("display_name") or row.get("username") or uid[:8])


def _load_shares_raw() -> list[dict]:
    path = _shares_path()
    data = load_json(path, default={"shares": []})
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    shares = data.get("shares") if isinstance(data, dict) else []
    return [s for s in shares if isinstance(s, dict)]


def _save_shares_raw(shares: list[dict]) -> None:
    path = _shares_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, {"shares": shares})


def list_pool_shares() -> list[dict[str, Any]]:
    with _shares_lock:
        return list(_load_shares_raw())


def shares_from_user(from_user_id: str) -> list[dict[str, Any]]:
    fid = (from_user_id or "").strip()
    return [s for s in list_pool_shares() if str(s.get("from_user_id") or "") == fid]


def shares_to_user(to_user_id: str) -> list[dict[str, Any]]:
    tid = (to_user_id or "").strip()
    return [s for s in list_pool_shares() if str(s.get("to_user_id") or "") == tid]


def has_pool_share(from_user_id: str, to_user_id: str) -> bool:
    fid = (from_user_id or "").strip()
    tid = (to_user_id or "").strip()
    if not fid or not tid or fid == tid:
        return False
    return any(
        str(s.get("from_user_id") or "") == fid and str(s.get("to_user_id") or "") == tid
        for s in list_pool_shares()
    )


def add_pool_share(from_user_id: str, to_user_id: str) -> dict[str, Any]:
    from backend import app_users

    fid = (from_user_id or "").strip()
    tid = (to_user_id or "").strip()
    if not fid or not tid:
        raise ValueError("缺少用户")
    if fid == tid:
        raise ValueError("不能共享给自己")
    donor = app_users.get_user(fid)
    target = app_users.get_user(tid)
    if not donor or not target:
        raise ValueError("用户不存在")
    if not can_use_browser_channels(donor.get("role")):
        raise ValueError("仅内部人员/管理员可共享号池")
    if not can_use_browser_channels(target.get("role")):
        raise ValueError("只能共享给内部人员或管理员")
    if not bool(target.get("enabled", True)):
        raise ValueError("目标账号已禁用")

    with _shares_lock:
        shares = _load_shares_raw()
        for s in shares:
            if str(s.get("from_user_id") or "") == fid and str(s.get("to_user_id") or "") == tid:
                return s
        row = {
            "from_user_id": fid,
            "from_username": str(donor.get("username") or ""),
            "from_display_name": str(donor.get("display_name") or donor.get("username") or ""),
            "to_user_id": tid,
            "to_username": str(target.get("username") or ""),
            "to_display_name": str(target.get("display_name") or target.get("username") or ""),
            "created_at": datetime.now().isoformat(),
        }
        shares.append(row)
        _save_shares_raw(shares)
        return row


def remove_pool_share(from_user_id: str, to_user_id: str) -> bool:
    fid = (from_user_id or "").strip()
    tid = (to_user_id or "").strip()
    with _shares_lock:
        shares = _load_shares_raw()
        next_shares = [
            s
            for s in shares
            if not (
                str(s.get("from_user_id") or "") == fid
                and str(s.get("to_user_id") or "") == tid
            )
        ]
        if len(next_shares) == len(shares):
            return False
        _save_shares_raw(next_shares)
        return True


_admin_cache: dict[str, bool] = {}
_admin_cache_at = 0.0


def _user_is_admin(user_id: str) -> bool:
    import time

    global _admin_cache_at
    uid = (user_id or "").strip()
    if not uid:
        return False
    now = time.monotonic()
    if now - _admin_cache_at > 30:
        _admin_cache.clear()
        _admin_cache_at = now
    cached = _admin_cache.get(uid)
    if cached is not None:
        return cached
    try:
        from backend import app_users
        from backend.user_scope import DEFAULT_USER_ID

        if uid == DEFAULT_USER_ID:
            _admin_cache[uid] = False
            return False
        row = app_users.get_user(uid)
        ok = bool(row and normalize_role(row.get("role")) == ROLE_ADMIN)
        _admin_cache[uid] = ok
        return ok
    except Exception:
        return False


def _has_share_from_admin(to_user_id: str) -> bool:
    """True when any admin user has one-way pool-shared to this user."""
    tid = (to_user_id or "").strip()
    if not tid:
        return False
    for s in shares_to_user(tid):
        donor = str(s.get("from_user_id") or "").strip()
        if donor and _user_is_admin(donor):
            return True
    return False


def user_owns_account(account: Any, user_id: str) -> bool:
    """True when this user is the assigned owner (admin pool / personal)."""
    uid = (user_id or "").strip()
    mode = normalize_owner_mode(getattr(account, "owner_mode", None))
    if mode == OWNER_MODE_SHARED_ALL:
        return False
    if mode == OWNER_MODE_ADMIN:
        return _user_is_admin(uid)
    owner_id = str(getattr(account, "owner_user_id", None) or "").strip()
    if not owner_id:
        # Legacy unset → admin pool
        return _user_is_admin(uid)
    return bool(owner_id and owner_id == uid)


def account_pick_priority(account: Any, user_id: str | None) -> int:
    """
    Lower is better for auto-pick.
    0 = own pool (personal / admin-pool for admins)
    1 = shared_all
    2 = shared-in from another user (one-way pool share)
    """
    uid = (user_id or "").strip()
    if not uid:
        return 1
    if user_owns_account(account, uid):
        return 0
    mode = normalize_owner_mode(getattr(account, "owner_mode", None))
    if mode == OWNER_MODE_SHARED_ALL:
        return 1
    return 2


def user_can_use_account(account: Any, user_id: str | None) -> bool:
    """
    Whether user_id may lease this channel account.

    - shared_all: any browser-channel user
    - admin pool: admins always; recipients of an admin's pool-share may use unused-today
    - user: owner always; share recipients only for accounts unused today
    """
    uid = (user_id or "").strip()
    if not uid:
        return normalize_owner_mode(getattr(account, "owner_mode", None)) == OWNER_MODE_SHARED_ALL

    mode = normalize_owner_mode(getattr(account, "owner_mode", None))
    if mode == OWNER_MODE_SHARED_ALL:
        return True
    if mode == OWNER_MODE_ADMIN:
        if _user_is_admin(uid):
            return True
        # Admin shared their pool → recipient can use unused admin-pool accounts today
        return _has_share_from_admin(uid) and not account_used_today(account)

    owner_id = str(getattr(account, "owner_user_id", None) or "").strip()
    if not owner_id:
        # Legacy / unset owner → treat as admin pool
        if _user_is_admin(uid):
            return True
        return _has_share_from_admin(uid) and not account_used_today(account)
    if owner_id == uid:
        return True
    if not has_pool_share(owner_id, uid):
        return False
    # One-way share: only unused-today accounts from the donor's pool
    return not account_used_today(account)


def filter_accounts_for_user(accounts: list, user_id: str | None) -> list:
    return [a for a in accounts if user_can_use_account(a, user_id)]


def owned_accounts_for_user(accounts: list, user_id: str) -> list:
    uid = (user_id or "").strip()
    out = []
    for a in accounts:
        mode = normalize_owner_mode(getattr(a, "owner_mode", None))
        if mode == OWNER_MODE_USER and str(getattr(a, "owner_user_id", None) or "") == uid:
            out.append(a)
        elif mode == OWNER_MODE_ADMIN and _user_is_admin(uid):
            # Admin "owns" the admin pool for stats — count only when listing as admin owner view
            # For personal avatar stats we only want personal ownership for internal users.
            # Admins see admin-pool accounts as theirs.
            out.append(a)
    return out


def personal_owned_accounts(accounts: list, user_id: str) -> list:
    """Accounts assigned to this user (owner_mode=user). Admin pool excluded."""
    uid = (user_id or "").strip()
    return [
        a
        for a in accounts
        if normalize_owner_mode(getattr(a, "owner_mode", None)) == OWNER_MODE_USER
        and str(getattr(a, "owner_user_id", None) or "") == uid
    ]


def admin_pool_accounts(accounts: list) -> list:
    return [
        a
        for a in accounts
        if normalize_owner_mode(getattr(a, "owner_mode", None)) == OWNER_MODE_ADMIN
        or (
            normalize_owner_mode(getattr(a, "owner_mode", None)) == OWNER_MODE_USER
            and not str(getattr(a, "owner_user_id", None) or "").strip()
        )
    ]


def summarize_accounts_for_user(accounts: list, user_id: str) -> dict[str, Any]:
    """Avatar stats: owned pool + usable shared-in unused accounts."""
    from backend import account_manager

    uid = (user_id or "").strip()
    is_admin = _user_is_admin(uid)
    owned = []
    for a in accounts:
        if account_manager.normalize_provider(getattr(a, "provider", None)) != "doubao":
            continue
        mode = normalize_owner_mode(getattr(a, "owner_mode", None))
        if mode == OWNER_MODE_USER and str(getattr(a, "owner_user_id", None) or "") == uid:
            owned.append(a)
        elif mode == OWNER_MODE_ADMIN and is_admin:
            owned.append(a)

    owned_ids = {a.id for a in owned}

    # Usable accounts from one-way pool shares (unused today only).
    shared_in_accounts = []
    seen_shared = set()
    for s in shares_to_user(uid):
        donor_id = str(s.get("from_user_id") or "")
        if not donor_id:
            continue
        donor_owned = personal_owned_accounts(accounts, donor_id)
        if _user_is_admin(donor_id):
            # Admin donor may also share admin-pool unused accounts
            donor_owned = list(donor_owned) + [
                a
                for a in accounts
                if account_manager.normalize_provider(getattr(a, "provider", None)) == "doubao"
                and normalize_owner_mode(getattr(a, "owner_mode", None)) == OWNER_MODE_ADMIN
                and a.id not in {x.id for x in donor_owned}
            ]
        for a in donor_owned:
            if a.id in owned_ids or a.id in seen_shared:
                continue
            if account_manager.normalize_provider(getattr(a, "provider", None)) != "doubao":
                continue
            if account_unavailable_today(a):
                continue
            if not user_can_use_account(a, uid):
                continue
            seen_shared.add(a.id)
            shared_in_accounts.append(a)

    owned_s = sum(1 for a in owned if account_manager.account_doubao_tier(a) == "upgrade")
    owned_regular = len(owned) - owned_s
    shared_s = sum(1 for a in shared_in_accounts if account_manager.account_doubao_tier(a) == "upgrade")
    shared_regular = len(shared_in_accounts) - shared_s
    shared_count = len(shared_in_accounts)

    # 今日消耗 = 已进入次日等待期的自有账号数（非今日 merely 用过）
    used_today = sum(
        1 for a in owned if account_manager.should_close_browser_until_next_day(a)
    )
    # 可用 = 未进次日/未消耗的自有 + 共享进来仍可用的
    available_owned = [a for a in owned if not account_unavailable_today(a)]
    available_s_owned = sum(
        1 for a in available_owned if account_manager.account_doubao_tier(a) == "upgrade"
    )
    available_regular_owned = len(available_owned) - available_s_owned
    available_total = len(available_owned) + shared_count
    available_s = available_s_owned + shared_s
    available_regular = available_regular_owned + shared_regular

    shares_out = []
    for s in shares_from_user(uid):
        shares_out.append(
            {
                "user_id": s.get("to_user_id"),
                "username": s.get("to_username"),
                "display_name": s.get("to_display_name") or s.get("to_username"),
                "created_at": s.get("created_at"),
            }
        )
    shares_in = []
    for s in shares_to_user(uid):
        donor_id = str(s.get("from_user_id") or "")
        donor_pool = personal_owned_accounts(accounts, donor_id)
        if _user_is_admin(donor_id):
            donor_pool = list(donor_pool) + [
                a
                for a in accounts
                if account_manager.normalize_provider(getattr(a, "provider", None)) == "doubao"
                and normalize_owner_mode(getattr(a, "owner_mode", None)) == OWNER_MODE_ADMIN
            ]
        unused_from_donor = sum(
            1
            for a in donor_pool
            if account_manager.normalize_provider(a.provider) == "doubao"
            and not account_unavailable_today(a)
            and a.id not in owned_ids
        )
        shares_in.append(
            {
                "user_id": donor_id,
                "username": s.get("from_username"),
                "display_name": s.get("from_display_name") or s.get("from_username"),
                "unused_available": unused_from_donor,
                "created_at": s.get("created_at"),
            }
        )

    return {
        "owned_total": len(owned),
        "owned_s_count": owned_s,
        "owned_regular_count": owned_regular,
        "shared_count": shared_count,
        "shared_s_count": shared_s,
        "shared_regular_count": shared_regular,
        # Main totals include usable shared-in accounts
        "total": len(owned) + shared_count,
        "s_count": owned_s + shared_s,
        "regular_count": owned_regular + shared_regular,
        "used_today_count": used_today,
        "unused_count": available_total,
        "unused_s_count": available_s,
        "unused_regular_count": available_regular,
        "shares_out": shares_out,
        "shares_in": shares_in,
    }


def list_pool_peers(exclude_user_id: str | None = None) -> list[dict[str, Any]]:
    """Internal + admin users that can receive/send pool shares."""
    from backend import app_users

    exclude = (exclude_user_id or "").strip()
    out = []
    for u in app_users.list_users():
        if not bool(u.get("enabled", True)):
            continue
        if not can_use_browser_channels(u.get("role")):
            continue
        if str(u.get("id") or "") == exclude:
            continue
        out.append(
            {
                "id": u.get("id"),
                "username": u.get("username"),
                "display_name": u.get("display_name") or u.get("username"),
                "role": u.get("role"),
                "role_label": u.get("role_label"),
            }
        )
    return out


def set_account_owner(
    account_id: str,
    *,
    owner_mode: str,
    owner_user_id: str | None = None,
) -> Any:
    from backend import account_manager, app_users

    mode = normalize_owner_mode(owner_mode)
    uid = str(owner_user_id or "").strip() or None
    if mode == OWNER_MODE_USER:
        if not uid:
            raise ValueError("请选择归属的内部账号")
        user = app_users.get_user(uid)
        if not user:
            raise ValueError("用户不存在")
        if not can_use_browser_channels(user.get("role")):
            raise ValueError("只能归属给内部人员或管理员")
    else:
        uid = None

    account = account_manager.update_account(
        account_id,
        owner_mode=mode,
        owner_user_id=uid,
    )
    if not account:
        raise ValueError("账号不存在")
    return account


def enrich_account_payload(account: Any, *, users_by_id: dict[str, dict] | None = None) -> dict:
    data = account.model_dump() if hasattr(account, "model_dump") else dict(account)
    apply_owner_defaults(data)
    data["owner_label"] = owner_label_for_account(account, users_by_id=users_by_id)
    owner_id = str(data.get("owner_user_id") or "").strip()
    if owner_id and normalize_owner_mode(data.get("owner_mode")) == OWNER_MODE_USER:
        data["owner_shares_out"] = [
            {
                "user_id": s.get("to_user_id"),
                "username": s.get("to_username"),
                "display_name": s.get("to_display_name") or s.get("to_username"),
            }
            for s in shares_from_user(owner_id)
        ]
    else:
        data["owner_shares_out"] = []
    data["used_today"] = account_used_today(account)
    return data
