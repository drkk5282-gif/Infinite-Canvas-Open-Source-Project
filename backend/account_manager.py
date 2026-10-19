import json
import logging
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from backend.user_scope import accounts_index as _accounts_index


def _index_path() -> Path:
    return _accounts_index()
from backend.json_io import load_json, save_json
from backend.models import Account, AccountStatus

logger = logging.getLogger(__name__)

# UI messaging rarely needs long cooldowns; keep short if page itself rate-limits
RATE_LIMIT_COOLDOWN_SECONDS = 120
# Daily / plan quota fallback when reset time is not in the error text
QUOTA_COOLDOWN_SECONDS = 6 * 60 * 60

_LIMIT_RESET_MINUTES_RE = re.compile(
    r"(?:resets?\s+in|in)\s+(\d+)\s*minutes?"
    r"|约\s*(\d+)\s*分钟后恢复"
    r"|(\d+)\s*分钟(?:后|内)?(?:恢复|重置|可用)?",
    re.I,
)

# In-flight generation leases: local Chromium windows per user + per Doubao account.
_lease_lock = threading.Lock()
_account_leases: dict[str, int] = {}  # account_id -> active lease count
_user_leases: dict[str, int] = {}  # canvas user_id -> active lease count


def _ensure_dirs() -> None:
    _index_path().parent.mkdir(parents=True, exist_ok=True)
    try:
        from browser_channels.paths import channel_data_root

        for prov in ("doubao", "dola"):
            (channel_data_root(prov)).mkdir(parents=True, exist_ok=True)
    except ImportError:
        pass


def _provider_for_account(account_id: str) -> str:
    account = get_account(account_id)
    return normalize_provider(account.provider if account else "doubao")


def session_path(account_id: str, provider: str | None = None) -> Path:
    try:
        from browser_channels.paths import session_path as _session_path

        return _session_path(account_id, provider or _provider_for_account(account_id))
    except ImportError:
        from backend.config import DATA_DIR

        return DATA_DIR / "accounts" / account_id / "session.json"


def browser_data_path(account_id: str, provider: str | None = None) -> Path:
    try:
        from browser_channels.paths import browser_data_path as _browser_data_path

        return _browser_data_path(account_id, provider or _provider_for_account(account_id))
    except ImportError:
        from backend.config import DATA_DIR

        return DATA_DIR / "accounts" / account_id / "browser"


def _load_index() -> list[dict]:
    _ensure_dirs()
    data = load_json(_index_path(), default=[])
    return data if isinstance(data, list) else []


def _save_index(items: list[dict]) -> None:
    _ensure_dirs()
    save_json(_index_path(), items)


def _reset_daily_counts_if_needed(items: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    now = datetime.now()
    changed = False
    for item in items:
        if item.get("count_date") != today:
            item["count_date"] = today
            item["video_count_today"] = 0
            item["image_count_today"] = 0
            # New calendar day → clear next-day video wait
            if item.get("video_cooldown_until"):
                item["video_cooldown_until"] = None
                if "等待次日" in str(item.get("last_error") or "") or "视频" in str(
                    item.get("last_error") or ""
                ):
                    item["last_error"] = None
            changed = True
        # Drop expired cooldowns so UI yellow light clears without waiting for a task
        for key in ("cooldown_until", "video_cooldown_until"):
            raw = (item.get(key) or "").strip()
            if not raw:
                continue
            try:
                if datetime.fromisoformat(raw) <= now:
                    item[key] = None
                    changed = True
            except ValueError:
                item[key] = None
                changed = True
    if changed:
        _save_index(items)
    return items


def list_accounts() -> list[Account]:
    from backend.account_ownership import apply_owner_defaults

    items = _reset_daily_counts_if_needed(_load_index())
    out: list[Account] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("provider"):
            item["provider"] = "doubao"
        # In-memory defaults only — never write on list (avoids Windows file lock stalls
        # when /api/accounts and /api/accounts/pool-summary run together at login).
        apply_owner_defaults(item)
        try:
            out.append(Account(**item))
        except Exception as exc:
            logger.warning("skip bad account row: %s", exc)
    return out


def get_account(account_id: str) -> Optional[Account]:
    for account in list_accounts():
        if account.id == account_id:
            return account
    return None


def _norm_name(name: str) -> str:
    return (name or "").strip().lower()


_KNOWN_PROVIDERS = frozenset({"doubao", "dola"})
_RETIRED_GPT_PROVIDERS = frozenset({"gpt", "openai"})


def normalize_provider(provider: str | None) -> str:
    p = (provider or "doubao").strip().lower()
    # Retired browser OpenAI channel — keep id for soft-reject / legacy accounts.
    if p in _RETIRED_GPT_PROVIDERS:
        return "gpt"
    if p in _KNOWN_PROVIDERS:
        return p
    return "doubao"


def is_bytedance_provider(provider: str | None) -> bool:
    return normalize_provider(provider) in ("doubao", "dola")


def account_doubao_tier(account: Account) -> str:
    if not is_bytedance_provider(account.provider):
        return "regular"
    # Prefer re-derive from stored sidebar label so rule changes apply immediately.
    label = str(getattr(account, "doubao_plan_label", None) or "").strip()
    if label:
        try:
            from browser_channels.doubao.profile import tier_from_plan_label

            return "upgrade" if tier_from_plan_label(label) == "upgrade" else "regular"
        except Exception:
            pass
    t = str(getattr(account, "doubao_tier", None) or "regular").strip().lower()
    return "upgrade" if t == "upgrade" else "regular"


def only_upgrade_accounts_available(
    *,
    exclude_ids: set[str] | None = None,
    kind: str = "video",
) -> bool:
    """True when no regular-tier Doubao account is available but upgrade-tier is."""
    regular = pick_account(
        None,
        exclude_ids=exclude_ids,
        kind=kind,
        provider="doubao",
        doubao_tier="regular",
    )
    if regular is not None:
        return False
    upgrade = pick_account(
        None,
        exclude_ids=exclude_ids,
        kind=kind,
        provider="doubao",
        doubao_tier="upgrade",
    )
    return upgrade is not None


def is_gpt_model(model: str | None) -> bool:
    """Browser OpenAI models retired — always False (BenDi uses hyphen ids)."""
    return False


def provider_for_image_model(model: str | None, *, channel: str | None = None) -> str:
    ch = (channel or "").strip().lower()
    # Dola 已下线 → 走豆包账号
    if ch in ("dola", "dola_s"):
        return "doubao"
    return "doubao"


def provider_for_text_model(model: str | None, *, channel: str | None = None) -> str:
    """doubao_chat → doubao. Browser OpenAI / gpt_* retired (use BenDi chatgpt)."""
    return "doubao"


def provider_for_video_channel(channel: str | None = None, *, provider: str | None = None) -> str:
    """Map UI channel / explicit provider to account provider for video jobs."""
    if provider:
        p = normalize_provider(provider)
        if p == "dola":
            return "doubao"
        if p == "gpt":
            raise ValueError("OpenAI 渠道已下线，请改用 BenDi")
        return p
    ch = (channel or "").strip().lower()
    if ch in ("dola", "dola_s"):
        return "doubao"
    if ch == "gpt":
        raise ValueError("OpenAI 渠道已下线，请改用 BenDi")
    return "doubao"


def suggest_account_name(*, provider: str = "doubao") -> str:
    """Next free label like Seed1 …"""
    provider = normalize_provider(provider)
    if provider == "gpt":
        prefix = "OpenAI"
    elif provider == "dola":
        prefix = "Seed"
    else:
        prefix = "Seed"
    taken = {_norm_name(a.name) for a in list_accounts()}
    n = 1
    while True:
        candidate = f"{prefix}{n}"
        if _norm_name(candidate) not in taken:
            return candidate
        n += 1


def is_name_taken(name: str, *, exclude_id: Optional[str] = None) -> bool:
    key = _norm_name(name)
    if not key:
        return False
    for account in list_accounts():
        if exclude_id and account.id == exclude_id:
            continue
        if _norm_name(account.name) == key:
            return True
    return False


def unique_display_name(base: str, *, exclude_id: Optional[str] = None) -> str:
    base = (base or "").strip() or "Seed 用户"
    base = base[:40]
    if not is_name_taken(base, exclude_id=exclude_id):
        return base
    n = 2
    while True:
        candidate = f"{base}·{n}"
        if not is_name_taken(candidate, exclude_id=exclude_id):
            return candidate
        n += 1


def create_account(
    name: str | None = None,
    *,
    provider: str = "doubao",
    owner_mode: str | None = None,
    owner_user_id: str | None = None,
) -> Account:
    from backend.account_ownership import (
        OWNER_MODE_ADMIN,
        OWNER_MODE_USER,
        normalize_owner_mode,
    )
    from backend.user_roles import can_use_browser_channels, normalize_role, ROLE_ADMIN
    from backend.user_scope import get_current_user_id

    provider = normalize_provider(provider)
    if provider == "dola":
        raise ValueError("该渠道已下线（浏览器易频繁要求登录），请改用 Seed 或 BenDi")
    if provider == "gpt":
        raise ValueError("OpenAI 渠道已下线，请改用 BenDi")
    label = (name or "").strip() or suggest_account_name(provider=provider)
    if is_name_taken(label):
        raise ValueError(f"名称「{label}」已存在，请换一个")

    # Resolve ownership: explicit args win; else bind to current internal/admin user.
    mode = normalize_owner_mode(owner_mode) if owner_mode else None
    oid = str(owner_user_id or "").strip() or None
    if mode is None:
        uid = (get_current_user_id() or "").strip() or None
        if uid:
            try:
                from backend import app_users

                row = app_users.get_user(uid)
            except Exception:
                row = None
            if row and can_use_browser_channels(row.get("role")):
                if normalize_role(row.get("role")) == ROLE_ADMIN and not owner_user_id:
                    # Admin-created accounts default to admin pool unless explicitly personal.
                    mode = OWNER_MODE_ADMIN
                    oid = None
                else:
                    mode = OWNER_MODE_USER
                    oid = uid
        if mode is None:
            mode = OWNER_MODE_ADMIN
            oid = None
    elif mode == OWNER_MODE_USER:
        if not oid:
            oid = (get_current_user_id() or "").strip() or None
        if not oid:
            raise ValueError("归属用户无效")
    else:
        oid = None

    account_id = uuid.uuid4().hex[:12]
    try:
        from browser_channels.paths import ensure_account_dirs

        ensure_account_dirs(account_id, provider)
    except ImportError:
        from backend.config import DATA_DIR

        (DATA_DIR / "accounts" / account_id / "browser").mkdir(parents=True, exist_ok=True)

    account = Account(
        id=account_id,
        name=label,
        status=AccountStatus.offline,
        created_at=datetime.now().isoformat(),
        provider=provider,
        owner_mode=mode,
        owner_user_id=oid,
    )
    items = _load_index()
    items.append(account.model_dump())
    _save_index(items)
    return account


def clear_session_files(account_id: str) -> None:
    path = session_path(account_id)
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning("clear_session_files failed: %s", exc)


def get_session_cookie_value(account_id: str, cookie_name: str = "sessionid") -> Optional[str]:
    path = session_path(account_id)
    if not path.exists():
        return None
    try:
        cookies = load_json(path, default=[])
    except Exception:
        return None
    if not isinstance(cookies, list):
        return None
    for c in cookies:
        if isinstance(c, dict) and c.get("name") == cookie_name and c.get("value"):
            return str(c["value"])
    return None


def session_fingerprint(account_id: str) -> Optional[str]:
    import hashlib

    sid = get_session_cookie_value(account_id, "sessionid")
    if not sid:
        return None
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:20]


def find_account_by_session_fp(
    fingerprint: str,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[Account]:
    if not fingerprint:
        return None
    for account in list_accounts():
        if exclude_id and account.id == exclude_id:
            continue
        fp = account.session_fp or session_fingerprint(account.id)
        if fp and fp == fingerprint:
            return account
    return None


def bind_session_identity(
    account_id: str,
    *,
    doubao_uid: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[Account]:
    """After login: store fingerprint/uid and optionally rename from Doubao nickname."""
    fp = session_fingerprint(account_id)
    fields: dict = {}
    if fp:
        fields["session_fp"] = fp
    if doubao_uid:
        fields["doubao_uid"] = str(doubao_uid)
    if display_name:
        fields["name"] = unique_display_name(display_name, exclude_id=account_id)
    if not fields:
        return get_account(account_id)
    return update_account(account_id, **fields)


def update_account(account_id: str, **fields) -> Optional[Account]:
    from backend.account_ownership import apply_owner_defaults

    items = _load_index()
    for item in items:
        if item["id"] == account_id:
            for key, value in fields.items():
                if value is not None or key in (
                    "last_error",
                    "cooldown_until",
                    "video_cooldown_until",
                    "note",
                    "doubao_plan_label",
                    "owner_user_id",
                    "owner_mode",
                    "shared_with_user_ids",
                ):
                    item[key] = value
            apply_owner_defaults(item)
            _save_index(items)
            return Account(**item)
    return None


def delete_account(account_id: str) -> bool:
    items = _load_index()
    new_items = [item for item in items if item["id"] != account_id]
    if len(new_items) == len(items):
        return False
    account_row = next((i for i in items if i.get("id") == account_id), None)
    _save_index(new_items)

    prov = normalize_provider(
        account_row.get("provider") if isinstance(account_row, dict) else "doubao"
    )
    try:
        from browser_channels.paths import remove_account_data

        remove_account_data(account_id, prov)
    except ImportError:
        import shutil
        from backend.config import DATA_DIR

        legacy = DATA_DIR / "accounts" / account_id
        if legacy.exists():
            shutil.rmtree(legacy, ignore_errors=True)
    return True


def set_account_status(
    account_id: str,
    status: AccountStatus,
    *,
    last_error: Optional[str] = None,
) -> Optional[Account]:
    fields: dict = {"status": status.value}
    if last_error is not None:
        fields["last_error"] = last_error
    if status == AccountStatus.online:
        fields["last_error"] = None
    return update_account(account_id, **fields)


def mark_account_used(account_id: str, *, increment_video: bool = False, increment_image: bool = False) -> None:
    items = _load_index()
    today = date.today().isoformat()
    for item in items:
        if item["id"] == account_id:
            item["last_used_at"] = datetime.now().isoformat()
            if item.get("count_date") != today:
                item["count_date"] = today
                item["video_count_today"] = 0
                item["image_count_today"] = 0
                item["video_cooldown_until"] = None
            if increment_video:
                item["video_count_today"] = int(item.get("video_count_today", 0)) + 1
            if increment_image:
                item["image_count_today"] = int(item.get("image_count_today", 0)) + 1
            break
    _save_index(items)


def _account_provider(account_id: str) -> str:
    for item in _load_index():
        if isinstance(item, dict) and item.get("id") == account_id:
            return normalize_provider(item.get("provider"))
    return "doubao"


def has_valid_session(account_id: str) -> bool:
    from backend.channel_gate import browser_channels_installed

    if not browser_channels_installed():
        return False
    from browser_channels.doubao.qr_login import qr_login_manager

    provider = _account_provider(account_id)
    if provider == "gpt":
        # Browser OpenAI channel retired — never treat legacy GPT sessions as online.
        return False
    cookies = qr_login_manager.cookies_for_playwright(account_id)
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)


def reconcile_gpt_online_status() -> int:
    """Downgrade legacy GPT accounts marked online (channel retired)."""
    fixed = 0
    items = _load_index()
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if normalize_provider(item.get("provider")) != "gpt":
            continue
        if item.get("status") != AccountStatus.online.value:
            continue
        item["status"] = AccountStatus.offline.value
        item["last_error"] = "OpenAI 渠道已下线，请改用 BenDi"
        changed = True
        fixed += 1
    if changed:
        _save_index(items)
    return fixed


def _has_gpt_session_cookies(cookies: list[dict]) -> bool:
    """Stub: browser OpenAI channel retired."""
    return False


def is_rate_limit_error(err: str | Exception | None) -> bool:
    text = str(err or "")
    lower = text.lower()
    return (
        "710022004" in text
        or "rate limited" in lower
        or "shark_admin" in lower
        or "限流" in text
        or "安全验证" in text
        or "额度不足" in text
        or "已达到限额" in text
        or "usage limit" in lower
        or "hit the limit" in lower
        or "hit your limit" in lower
        or "free plan limit" in lower
        or "limit resets in" in lower
        or "daily limit" in lower
        or "too many requests" in lower
        or "次数用完" in text
        or "今日视频生成" in text
    )


def is_policy_reject_error(err: str | Exception | None) -> bool:
    """Copyright / audit reject — stop task immediately (switching accounts won't help)."""
    text = str(err or "")
    return any(
        m in text
        for m in (
            "版权相关限制",
            "版权限制",
            "暂时无法创作",
            "无法创作对应的内容",
            "换其他主题试试",
            "换个主题再试试",
            "换个主题试试",
            "疑似包含侵权",
            "侵权内容",
            "违规内容",
            "无法返回该内容",
            "审核未通过",
            "未通过审核",
            "内容违规",
            "敏感内容",
            "无法生成该内容",
            "不符合社区",
            "content policy",
            "against our usage policies",
        )
    )


def is_video_quota_exhausted_error(err: str | Exception | None) -> bool:
    """Doubao daily free video quota — image/text may still work on same account."""
    text = str(err or "")
    # User chose「跳过」on paid-quota confirm — treat as free video quota done, switch account.
    if "PAID_QUOTA_USER_SKIP" in text:
        return True
    if "今日视频生成" in text and ("用完" in text or "满了" in text):
        return True
    if "视频生成免费次数" in text and "用完" in text:
        return True
    if "免费次数用完了" in text and "视频" in text:
        return True
    if "明天再来" in text and "视频" in text:
        return True
    # Doubao UI:「额度紧张 / 开通专业版」on video = daily video quota for this account.
    if "额度紧张" in text or "开通豆包专业版" in text:
        return True
    return False


def is_model_select_error(err: str | Exception | None) -> bool:
    """Doubao UI could not pick the requested model — often per-account; try another account first."""
    text = str(err or "")
    if is_model_unavailable_error(text):
        return False
    return (
        "无法选择模型" in text
        or "无法使用模型" in text
        or "账号无法使用模型" in text
    )


def is_model_unavailable_error(err: str | Exception | None) -> bool:
    """Model removed from Doubao menu — switching accounts will not help."""
    text = str(err or "")
    return "在豆包网页中已不可用" in text or "在页面上找不到该模型" in text


class DoubaoTierMismatchError(RuntimeError):
    """Live package does not match DouBao / DouBao S channel — switch account."""

    def __init__(self, message: str, *, tier: str = "regular", plan_label: str | None = None):
        super().__init__(message)
        self.tier = tier
        self.plan_label = plan_label


def is_tier_mismatch_error(err: str | Exception | None) -> bool:
    if isinstance(err, DoubaoTierMismatchError):
        return True
    text = str(err or "")
    return "套餐与渠道不符" in text or "TIER_MISMATCH" in text


def is_quota_exhausted_error(err: str | Exception | None) -> bool:
    """Daily / plan quota (prefer longer cooldown) vs short shark rate-limit."""
    text = str(err or "")
    lower = text.lower()
    if is_video_quota_exhausted_error(text):
        return True
    return (
        "额度不足" in text
        or "额度紧张" in text
        or "开通豆包专业版" in text
        or "创作额度包" in text
        or "没有剩余" in text
        or "已达上限" in text
        or "额度已用完" in text
        or "已达到限额" in text
        or "usage limit" in lower
        or "hit the limit" in lower
        or "hit your limit" in lower
        or "free plan limit" in lower
        or "limit resets in" in lower
        or "daily limit" in lower
        or "次数用完" in text
        or "今日次数" in text
        or "生成次数已用完" in text
    )


def parse_limit_reset_seconds(err: str | Exception | None) -> int | None:
    """Parse「resets in N minutes」→ seconds; None if not found."""
    text = str(err or "")
    m = _LIMIT_RESET_MINUTES_RE.search(text)
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    if not raw:
        return None
    try:
        minutes = max(1, int(raw))
    except (TypeError, ValueError):
        return None
    # Small buffer so we don't retry a minute early
    return minutes * 60 + 30


def cooldown_seconds_for_error(
    err: str | Exception | None,
    *,
    default: int = QUOTA_COOLDOWN_SECONDS,
) -> int:
    parsed = parse_limit_reset_seconds(err)
    if parsed is not None:
        return parsed
    return max(30, int(default))


def _cooldown_active(until_raw: str | None, *, now: datetime) -> bool:
    raw = (until_raw or "").strip()
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw) > now
    except ValueError:
        return False


def _cooldown_is_short_rate_limit(until_raw: str | None, *, now: datetime) -> bool:
    """True for shark/rate-limit cooldowns — not daily quota marks (until midnight)."""
    raw = (until_raw or "").strip()
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if until <= now:
        return False
    return (until - now).total_seconds() < 3 * 3600


def is_account_cooling(account: Account, *, kind: str = "image", now: datetime | None = None) -> bool:
    ts = now or datetime.now()
    if _cooldown_active(account.cooldown_until, now=ts):
        return True
    if kind == "video" and _cooldown_active(account.video_cooldown_until, now=ts):
        return True
    return False


def should_close_browser_until_next_day(
    account: Account | None, *, now: datetime | None = None
) -> bool:
    """True when account waits until next day — not short rate-limit cooldown."""
    if not account:
        return False
    ts = now or datetime.now()
    if _cooldown_active(account.video_cooldown_until, now=ts):
        return True
    if _cooldown_active(account.cooldown_until, now=ts):
        return not _cooldown_is_short_rate_limit(account.cooldown_until, now=ts)
    return False


def should_close_browser_after_job(
    account: Account | None, *, kind: str = "image", now: datetime | None = None
) -> bool:
    """Whether to tear down browser after a UI job completes.

    - Full-day quota (cooldown_until until midnight): always close.
    - Video-only 次日 (video_cooldown_until): close after video jobs only;
      keep warm for image/text reuse on the same account.
    """
    if not account:
        return False
    ts = now or datetime.now()
    if _cooldown_active(account.cooldown_until, now=ts):
        return not _cooldown_is_short_rate_limit(account.cooldown_until, now=ts)
    if kind == "video" and _cooldown_active(account.video_cooldown_until, now=ts):
        return True
    return False


def seconds_until_local_midnight() -> int:
    return _seconds_until_local_midnight()


def _seconds_until_local_midnight() -> int:
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(3600, int((tomorrow - now).total_seconds()))


def clear_account_video_quota_exhausted(account_id: str) -> Optional[Account]:
    """Clear video quota mark so the account can be scheduled for video again."""
    return update_account(account_id, video_cooldown_until=None)


def mark_account_video_quota_exhausted(
    account_id: str,
    *,
    reason: str | None = None,
) -> Optional[Account]:
    until = (datetime.now() + timedelta(seconds=_seconds_until_local_midnight())).isoformat(
        timespec="seconds"
    )
    return update_account(
        account_id,
        video_cooldown_until=until,
        last_error=reason
        or "Seed 今日视频额度已用完，等待次日恢复（图片/文本仍可用）",
    )


def maybe_mark_doubao_video_quota_after_success(
    account_id: str,
    *,
    model: str | None = None,
    provider: str = "doubao",
) -> bool:
    """Auto mark 次日 when daily model limit is reached after a successful video."""
    if normalize_provider(provider) != "doubao":
        return False
    account = get_account(account_id)
    if not account:
        return False
    if is_account_cooling(account, kind="video"):
        return True
    try:
        from browser_channels.doubao.media_models import (
            doubao_daily_video_limit,
            model_label,
            video_models_for_provider,
        )
    except ImportError:
        return False
    limit = doubao_daily_video_limit(model)
    count = int(account.video_count_today or 0)
    if count < limit:
        return False
    label = model_label(video_models_for_provider(provider), model, "Seedance")
    if limit == 1:
        reason = f"Seed {label} 今日额度已用完（1 次/日），等待次日恢复"
    else:
        reason = f"Seed 今日视频额度已用完（{count}/{limit} 次），等待次日恢复"
    mark_account_video_quota_exhausted(account_id, reason=reason)
    logger.info(
        "doubao video quota auto-marked account=%s model=%s count=%d limit=%d",
        account_id[:8],
        model or "—",
        count,
        limit,
    )
    return True


def mark_account_daily_quota_exhausted(
    account_id: str,
    *,
    reason: str | None = None,
) -> Optional[Account]:
    """Mark Doubao account daily quota used — skip until local midnight."""
    until = (datetime.now() + timedelta(seconds=_seconds_until_local_midnight())).isoformat(
        timespec="seconds"
    )
    return update_account(
        account_id,
        cooldown_until=until,
        last_error=reason or "Seed 今日额度已用完，等待次日恢复",
    )


def doubao_quota_max_switches() -> int:
    """How many account switches to attempt before giving up on daily quota."""
    n = len(_online_doubao_accounts())
    return max(1, n - 1) if n > 1 else 0


def mark_account_gpt_image_quota_exhausted(
    account_id: str,
    err: str | Exception | None = None,
    *,
    seconds: int | None = None,
    reason: str | None = None,
) -> Optional[Account]:
    """Stub: browser OpenAI channel retired — map to generic cooldown if called."""
    secs = int(seconds) if seconds is not None else cooldown_seconds_for_error(err)
    mins = max(1, int(round(secs / 60)))
    msg = reason or f"账号额度已用完，约 {mins} 分钟后恢复"
    return mark_account_rate_limited(account_id, seconds=secs, reason=msg)


def mark_account_rate_limited(
    account_id: str,
    *,
    seconds: int = RATE_LIMIT_COOLDOWN_SECONDS,
    reason: str | None = None,
) -> Optional[Account]:
    until = (datetime.now() + timedelta(seconds=max(30, int(seconds)))).isoformat(
        timespec="seconds"
    )
    return update_account(
        account_id,
        cooldown_until=until,
        last_error=reason
        or f"限流冷却至 {until[11:19]}，已自动跳过该账号",
    )


def clear_account_cooldown(account_id: str) -> Optional[Account]:
    return update_account(account_id, cooldown_until=None, last_error=None)


def clear_all_cooldowns() -> int:
    """Clear rate-limit cooldowns on all accounts (UI path is not blocked like API)."""
    cleared = 0
    for account in list_accounts():
        if (account.cooldown_until or "").strip():
            clear_account_cooldown(account.id)
            cleared += 1
    return cleared


def _online_doubao_accounts(*, exclude_ids: set[str] | None = None) -> list[Account]:
    exclude = exclude_ids or set()
    return [
        a
        for a in list_accounts()
        if a.id not in exclude
        and a.status == AccountStatus.online
        and has_valid_session(a.id)
        and normalize_provider(a.provider) == "doubao"
    ]


def has_video_quota_account(
    *,
    exclude_ids: set[str] | None = None,
    provider: str | None = "doubao",
) -> bool:
    """True when at least one online account can still run video today."""
    want = normalize_provider(provider) if provider is not None else None
    now = datetime.now()
    for account in list_accounts():
        if exclude_ids and account.id in exclude_ids:
            continue
        if account.status != AccountStatus.online or not has_valid_session(account.id):
            continue
        if want is not None and normalize_provider(account.provider) != want:
            continue
        if not is_account_cooling(account, kind="video", now=now):
            return True
    return False


def all_doubao_video_quota_exhausted(*, exclude_ids: set[str] | None = None) -> bool:
    """All online Doubao accounts are on daily video cooldown."""
    online = _online_doubao_accounts(exclude_ids=exclude_ids)
    if not online:
        return False
    now = datetime.now()
    return all(is_account_cooling(a, kind="video", now=now) for a in online)


def pick_account(
    preferred_id: Optional[str] = None,
    *,
    exclude_ids: set[str] | None = None,
    kind: str = "image",
    respect_cooldown: bool = True,
    provider: str | None = "doubao",
    doubao_tier: str | None = None,
    for_user_id: str | None = None,
    ignore_ownership: bool = False,
) -> Optional[Account]:
    from backend.account_ownership import account_pick_priority, user_can_use_account

    exclude = exclude_ids or set()
    now = datetime.now()
    want = normalize_provider(provider) if provider is not None else None
    tier_want = None
    if doubao_tier and want != "gpt":
        t = str(doubao_tier).strip().lower()
        tier_want = "upgrade" if t == "upgrade" else "regular"
    scope_uid = for_user_id if for_user_id is not None else (
        None if ignore_ownership else _current_user_key()
    )

    def _candidates() -> list[Account]:
        out: list[Account] = []
        for a in list_accounts():
            if a.id in exclude:
                continue
            if a.status != AccountStatus.online or not has_valid_session(a.id):
                continue
            if want is not None and normalize_provider(a.provider) != want:
                continue
            if tier_want is not None and account_doubao_tier(a) != tier_want:
                continue
            if respect_cooldown and is_account_cooling(a, kind=kind, now=now):
                continue
            if not ignore_ownership and not user_can_use_account(a, scope_uid):
                continue
            out.append(a)
        return out

    accounts = _candidates()
    # API shark cooldowns must not block UI — only clear short rate-limit marks, not daily quota.
    if not accounts and respect_cooldown:
        cooled = [
            a
            for a in list_accounts()
            if a.id not in exclude
            and a.status == AccountStatus.online
            and has_valid_session(a.id)
            and (want is None or normalize_provider(a.provider) == want)
            and (tier_want is None or account_doubao_tier(a) == tier_want)
            and (ignore_ownership or user_can_use_account(a, scope_uid))
            and _cooldown_is_short_rate_limit(a.cooldown_until, now=now)
        ]
        if cooled:
            for a in cooled:
                clear_account_cooldown(a.id)
            logger.info(
                "cleared %d short rate-limit cooldown(s) so UI generation can proceed",
                len(cooled),
            )
            accounts = _candidates()
    if not accounts:
        return None
    if preferred_id:
        for account in accounts:
            if account.id == preferred_id:
                return account

    def _last_used_key(a: Account) -> str:
        return (a.last_used_at or "").strip() or "0"

    # Own pool first — only fall back to shared-in / shared_all when no own candidate.
    if scope_uid and not ignore_ownership:
        own = [a for a in accounts if account_pick_priority(a, scope_uid) == 0]
        if own:
            accounts = own
        else:
            # Prefer「所有共享」over one-way shared-in when both are available.
            shared_all = [a for a in accounts if account_pick_priority(a, scope_uid) == 1]
            if shared_all:
                accounts = shared_all

    # Text/image: prefer accounts that still have an open browser (within the chosen pool).
    if kind in ("image", "text") and want in ("doubao", "dola"):
        try:
            from backend.channel_gate import browser_channels_enabled, get_pool

            if browser_channels_enabled():
                warm_ids = set(get_pool().open_doubao_account_ids())
                warmed = [a for a in accounts if a.id in warm_ids]
                if warmed:
                    accounts = warmed
        except Exception:
            pass

    # Video: prefer accounts with remaining daily quota; avoid touching Playwright
    # here (get_pool) so submit/lease never blocks on browser import.
    if kind == "video":
        return max(
            accounts,
            key=lambda a: (int(a.video_count_today or 0), _last_used_key(a), a.id),
        )
    return min(
        accounts,
        key=lambda a: (a.image_count_today, a.video_count_today, _last_used_key(a), a.id),
    )


def _max_leases_per_account() -> int:
    # One Playwright persistent context (one browser window) per account.
    return 1


def _max_leases_per_user() -> int:
    from backend import service_settings

    return service_settings.get_max_threads()


def _max_browsers_global() -> int:
    from backend import service_settings

    return service_settings.get_max_browsers()


def _current_user_key() -> str:
    from backend.user_scope import DEFAULT_USER_ID, get_current_user_id

    return (get_current_user_id() or DEFAULT_USER_ID).strip() or DEFAULT_USER_ID


def _user_at_capacity(user_id: str | None = None) -> bool:
    uid = (user_id or _current_user_key()).strip()
    cap = _max_leases_per_user()
    with _lease_lock:
        return int(_user_leases.get(uid, 0)) >= cap


def _accounts_at_capacity() -> set[str]:
    cap = _max_leases_per_account()
    with _lease_lock:
        return {aid for aid, n in _account_leases.items() if n >= cap}


def busy_account_ids() -> set[str]:
    return _accounts_at_capacity()


def lease_snapshot() -> dict[str, int]:
    with _lease_lock:
        return dict(_account_leases)


def release_account(account_id: str | None, *, user_id: str | None = None) -> None:
    """Drop one generation lease for an account (idempotent)."""
    uid = (user_id or _current_user_key()).strip()
    with _lease_lock:
        if account_id:
            n = int(_account_leases.get(account_id, 0)) - 1
            if n <= 0:
                _account_leases.pop(account_id, None)
            else:
                _account_leases[account_id] = n
            logger.info(
                "account lease release %s → %d",
                account_id[:8],
                max(0, n),
            )
        if uid:
            un = int(_user_leases.get(uid, 0)) - 1
            if un <= 0:
                _user_leases.pop(uid, None)
            else:
                _user_leases[uid] = un
            logger.info("user lease release %s → %d", uid[:8], max(0, un))


def acquire_account(
    preferred_id: Optional[str] = None,
    *,
    kind: str = "image",
    exclude_ids: set[str] | None = None,
    allow_queue_on_preferred: bool = True,
    provider: str | None = "doubao",
    doubao_tier: str | None = None,
) -> tuple[Optional[Account], str]:
    """
    Lease an online account for one UI-generation job.

    Returns (account, reason) where reason is '' | 'no_account' | 'all_busy' | 'browsers_full'.

    - Auto-select: prefer accounts without an active lease (parallel = different accounts).
      A leased account is never handed to another user (includes shared-pool accounts).
    - Ownership: pick_account prefers the caller's own pool; shared-in only when own is empty/busy.
    - Global max_browsers: total leases across all users; excess jobs should queue (browsers_full).
    - Preferred id: use it when free; if busy, try another online account before queueing.
    """
    exclude = set(exclude_ids or ())
    want = normalize_provider(provider) if provider is not None else None
    user_id = _current_user_key()
    cap = _max_leases_per_account()
    browser_cap = _max_browsers_global()
    with _lease_lock:
        if int(_user_leases.get(user_id, 0)) >= _max_leases_per_user():
            return None, "all_busy"
        total_leases = sum(int(n) for n in _account_leases.values())
        if total_leases >= browser_cap:
            return None, "browsers_full"
        at_capacity = {aid for aid, n in _account_leases.items() if n >= cap}

        if preferred_id and preferred_id not in exclude:
            preferred = pick_account(
                preferred_id,
                exclude_ids=exclude,
                kind=kind,
                provider=want,
                doubao_tier=doubao_tier,
            )
            if preferred and preferred.id == preferred_id:
                cur = int(_account_leases.get(preferred.id, 0))
                if cur >= cap:
                    alt = pick_account(
                        None,
                        exclude_ids=exclude | at_capacity | {preferred.id},
                        kind=kind,
                        provider=want,
                        doubao_tier=doubao_tier,
                    )
                    if alt:
                        preferred = alt
                    else:
                        return None, "all_busy"
                _account_leases[preferred.id] = int(_account_leases.get(preferred.id, 0)) + 1
                _user_leases[user_id] = int(_user_leases.get(user_id, 0)) + 1
                logger.info(
                    "account lease acquire preferred %s (%s/%s) count=%d user=%d",
                    preferred.id[:8],
                    preferred.name,
                    want or preferred.provider,
                    _account_leases[preferred.id],
                    _user_leases[user_id],
                )
                # Touch last_used so the next auto-pick rotates away from this account
                mark_account_used(preferred.id)
                return preferred, ""

        # Auto: spread jobs across accounts (skip ones already holding the browser).
        leased_ids = {aid for aid, n in _account_leases.items() if n > 0}
        auto_exclude = exclude | at_capacity
        account = None
        if leased_ids:
            account = pick_account(
                None,
                exclude_ids=auto_exclude | leased_ids,
                kind=kind,
                provider=want,
                doubao_tier=doubao_tier,
            )
        if not account:
            account = pick_account(
                None,
                exclude_ids=auto_exclude,
                kind=kind,
                provider=want,
                doubao_tier=doubao_tier,
            )
        if account:
            _account_leases[account.id] = int(_account_leases.get(account.id, 0)) + 1
            _user_leases[user_id] = int(_user_leases.get(user_id, 0)) + 1
            logger.info(
                "account lease acquire auto %s (%s/%s) count=%d user=%d cap=%d busy_was=%s",
                account.id[:8],
                account.name,
                want or account.provider,
                _account_leases[account.id],
                _user_leases[user_id],
                cap,
                sorted(x[:8] for x in at_capacity),
            )
            mark_account_used(account.id)
            return account, ""

        # Diagnose: any online at all?
        any_online = pick_account(
            None,
            exclude_ids=exclude,
            kind=kind,
            respect_cooldown=False,
            provider=want,
            doubao_tier=doubao_tier,
        )
        if any_online is None:
            any_online = pick_account(
                None,
                exclude_ids=exclude,
                kind=kind,
                provider=want,
                doubao_tier=doubao_tier,
            )
        if any_online is None:
            # Same-tier pool empty: see if the other DouBao tier still has online accounts.
            if want in ("doubao", "dola") and doubao_tier in ("regular", "upgrade"):
                other = "upgrade" if doubao_tier == "regular" else "regular"
                other_online = pick_account(
                    None,
                    exclude_ids=exclude,
                    kind=kind,
                    respect_cooldown=False,
                    provider=want,
                    doubao_tier=other,
                )
                if other_online is None:
                    other_online = pick_account(
                        None,
                        exclude_ids=exclude,
                        kind=kind,
                        provider=want,
                        doubao_tier=other,
                    )
                if other_online is not None:
                    return None, (
                        "no_regular_tier" if doubao_tier == "regular" else "no_upgrade_tier"
                    )
            return None, "no_account"
        if (
            kind == "video"
            and want != "gpt"
            and all_doubao_video_quota_exhausted(exclude_ids=exclude)
        ):
            return None, "video_quota_exhausted"
        return None, "all_busy"
