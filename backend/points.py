"""Per-user points ledger (3 decimal places) with reserve / commit / refund."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

LEDGER_FILE = DATA_DIR / "points_ledger.json"
_LOCK = threading.Lock()
_QUANT = Decimal("0.001")
ZERO = Decimal("0.000")


class InsufficientPointsError(Exception):
    def __init__(self, balance: Decimal, need: Decimal):
        self.balance = balance
        self.need = need
        super().__init__(f"积分不足（余额 {format_points(balance)}，需要 {format_points(need)}）")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_points(value: Any, *, default: str = "0.000") -> Decimal:
    raw = default if value is None else str(value).strip()
    if not raw:
        raw = default
    try:
        d = Decimal(raw)
    except (InvalidOperation, ValueError):
        d = Decimal(default)
    if d < ZERO:
        d = ZERO
    return d.quantize(_QUANT, rounding=ROUND_HALF_UP)


def format_points(value: Decimal | Any) -> str:
    return str(parse_points(value))


def format_delta(value: Decimal | Any) -> str:
    """Format a signed ledger delta (may be negative). Do not use format_points — it clamps <0 to 0."""
    return str(parse_delta(value))


def _load_ledger() -> dict[str, Any]:
    raw = load_json(LEDGER_FILE, default={"entries": [], "reserves": {}})
    if not isinstance(raw, dict):
        return {"entries": [], "reserves": {}}
    entries = raw.get("entries")
    reserves = raw.get("reserves")
    if not isinstance(entries, list):
        entries = []
    if not isinstance(reserves, dict):
        reserves = {}
    return {"entries": entries, "reserves": reserves}


def _save_ledger(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(LEDGER_FILE, data)


def _append_entry(
    data: dict[str, Any],
    *,
    user_id: str,
    amount: Decimal,
    balance_after: Decimal,
    reason: str,
    task_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    data["entries"].append(
        {
            "at": _now_iso(),
            "user_id": user_id,
            "amount": format_delta(amount),
            "balance_after": format_points(balance_after),
            "reason": reason,
            "task_id": task_id or "",
            "meta": meta or {},
        }
    )
    # Keep ledger bounded
    if len(data["entries"]) > 5000:
        data["entries"] = data["entries"][-4000:]


def get_balance(user_id: str) -> Decimal:
    from backend import app_users

    return app_users.get_points(user_id)


def set_balance(user_id: str, value: Any, *, reason: str = "admin_set") -> Decimal:
    from backend import app_users

    amount = parse_points(value)
    with _LOCK:
        bal = app_users.set_points(user_id, amount)
        data = _load_ledger()
        _append_entry(
            data,
            user_id=user_id,
            amount=ZERO,
            balance_after=bal,
            reason=reason,
            meta={"set_to": format_points(amount)},
        )
        _save_ledger(data)
        return bal


def list_history(
    user_id: str,
    *,
    category: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return newest-first ledger rows for a user (skips zero-change commit markers)."""
    uid = str(user_id or "").strip()
    cat = str(category or "all").strip().lower()
    if cat not in ("all", "spend", "earn"):
        cat = "all"
    try:
        lim = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        lim = 50
    try:
        off = max(0, int(offset))
    except (TypeError, ValueError):
        off = 0

    with _LOCK:
        data = _load_ledger()
        raw_rows = [e for e in data.get("entries") or [] if str(e.get("user_id") or "") == uid]

    enriched: list[dict[str, Any]] = []
    prev_bal: Decimal | None = None
    for e in raw_rows:
        if not isinstance(e, dict):
            continue
        reason = str(e.get("reason") or "")
        if reason == "commit":
            # Confirm-only marker; balance already changed at reserve time.
            prev_bal = parse_points(e.get("balance_after"))
            continue
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        amount = parse_delta(e.get("amount"))
        bal_after = parse_points(e.get("balance_after"))
        if reason == "admin_set" and amount == ZERO:
            set_to = parse_points(meta.get("set_to"), default=format_points(bal_after))
            amount = (set_to - prev_bal) if prev_bal is not None else (set_to - ZERO)
        elif amount == ZERO and reason in ("reserve", "admin_adjust"):
            # Legacy bug: format_points() clamped negatives to 0 — recover from balance delta / reserve.
            if prev_bal is not None:
                inferred = (bal_after - prev_bal).quantize(_QUANT, rounding=ROUND_HALF_UP)
                if inferred != ZERO:
                    amount = inferred
            if amount == ZERO and reason == "reserve":
                tid = str(e.get("task_id") or "")
                row = (data.get("reserves") or {}).get(tid) if tid else None
                if isinstance(row, dict):
                    reserved = parse_points(row.get("amount"))
                    if reserved > ZERO:
                        amount = -reserved
        row_cat = "spend" if amount < ZERO else "earn" if amount > ZERO else "other"
        if reason == "reserve":
            row_cat = "spend"
        elif reason in ("refund", "failed", "cancelled"):
            row_cat = "earn"
        elif reason.startswith("admin"):
            row_cat = "earn" if amount >= ZERO else "spend"

        title, detail_tags = _history_labels(reason, meta, amount)
        enriched.append(
            {
                "at": e.get("at") or "",
                "reason": reason,
                "category": row_cat,
                "amount": format_points(amount) if amount >= ZERO else format_points(-amount),
                "signed_amount": str(amount.quantize(_QUANT, rounding=ROUND_HALF_UP)),
                "balance_after": format_points(bal_after),
                "task_id": e.get("task_id") or "",
                "title": title,
                "tags": detail_tags,
                "meta": meta,
            }
        )
        prev_bal = bal_after

    if cat in ("spend", "earn"):
        enriched = [r for r in enriched if r.get("category") == cat]
    enriched.reverse()  # newest first
    total = len(enriched)
    page = enriched[off : off + lim]
    return {
        "entries": page,
        "total": total,
        "limit": lim,
        "offset": off,
        "balance": format_points(get_balance(uid)) if uid else format_points(ZERO),
    }


def _history_labels(reason: str, meta: dict[str, Any], amount: Decimal) -> tuple[str, list[str]]:
    tags: list[str] = []
    kind = str(meta.get("kind") or "").strip()
    name = str(meta.get("model_name") or meta.get("name") or "").strip()
    model = str(meta.get("model") or "").strip()
    resolution = str(meta.get("resolution") or "").strip()
    duration = meta.get("duration")
    if name:
        tags.append(name)
    elif model:
        tags.append(model)
    if kind:
        tags.append({"image": "图片", "video": "视频", "text": "文本"}.get(kind, kind))
    if resolution:
        tags.append(resolution)
    if duration not in (None, ""):
        tags.append(f"{duration}s")
    pin = meta.get("prompt_tokens")
    cout = meta.get("completion_tokens")
    if pin is not None and str(pin) != "":
        try:
            tags.append(f"输入 {int(pin):,} token")
        except (TypeError, ValueError):
            tags.append(f"输入 {pin} token")
    if cout is not None and str(cout) != "":
        try:
            tags.append(f"输出 {int(cout):,} token")
        except (TypeError, ValueError):
            tags.append(f"输出 {cout} token")

    if reason == "admin_set":
        return "后台设置积分", tags
    if reason == "admin_adjust":
        return "后台调整积分", tags
    if reason == "recharge":
        channel = str(meta.get("channel") or meta.get("name") or "").strip()
        if channel and channel not in tags:
            tags.insert(0, channel)
        return "在线充值", tags
    if reason == "reserve":
        return "生成消耗", tags
    if reason in ("refund", "failed"):
        return "失败退款", tags
    if reason == "cancelled":
        return "取消退款", tags
    if amount < ZERO:
        return "积分消耗", tags
    if amount > ZERO:
        return "积分到账", tags
    return reason or "积分变动", tags


def parse_delta(value: Any) -> Decimal:
    """Parse a signed delta (may be negative)."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return ZERO
    return d.quantize(_QUANT, rounding=ROUND_HALF_UP)


def adjust_balance(
    user_id: str,
    delta: Any,
    *,
    reason: str = "admin_adjust",
    meta: dict[str, Any] | None = None,
) -> Decimal:
    from backend import app_users

    d = parse_delta(delta)
    with _LOCK:
        bal = app_users.adjust_points(user_id, d)
        data = _load_ledger()
        _append_entry(
            data,
            user_id=user_id,
            amount=d,
            balance_after=bal,
            reason=reason,
            meta=meta,
        )
        _save_ledger(data)
        return bal


def compute_cost(
    model_cfg: dict[str, Any] | None,
    *,
    prompt: str = "",
    duration: int | float | None = None,
    resolution: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> Decimal:
    """Return points to charge. 0 if model does not require points."""
    if not model_cfg or not model_cfg.get("points_required"):
        return ZERO
    kind = str(model_cfg.get("kind") or "").strip().lower()
    mode = str(model_cfg.get("billing_mode") or "per_use").strip().lower()
    if mode == "per_char":
        mode = "per_token"
    price = _unit_price_for_resolution(model_cfg, resolution)
    if price <= ZERO:
        return ZERO
    if kind == "video" and mode == "per_second":
        try:
            secs = max(1, int(duration or 1))
        except (TypeError, ValueError):
            secs = 1
        return (price * Decimal(secs)).quantize(_QUANT, rounding=ROUND_HALF_UP)
    if kind == "text" and mode == "per_token":
        total = _total_tokens(prompt_tokens, completion_tokens, prompt=prompt)
        return (price * Decimal(total)).quantize(_QUANT, rounding=ROUND_HALF_UP)
    return price


def estimate_prompt_tokens(prompt: str) -> int:
    """Rough token estimate for reserve/preview when API usage is unknown."""
    n = len(prompt or "")
    # Mixed CJK/Latin heuristic ≈ 1 token / 2 chars, min 1.
    return max(1, (n + 1) // 2)


def _total_tokens(
    prompt_tokens: int | None,
    completion_tokens: int | None,
    *,
    prompt: str = "",
) -> int:
    pin = int(prompt_tokens) if prompt_tokens is not None else None
    cout = int(completion_tokens) if completion_tokens is not None else None
    if pin is not None and cout is not None:
        return max(1, pin + cout)
    if pin is not None:
        # Reserve/preview without output yet: input + modest output buffer.
        return max(1, pin + max(64, pin // 2))
    return max(1, estimate_prompt_tokens(prompt) + max(64, estimate_prompt_tokens(prompt) // 2))


def billing_label(model_cfg: dict[str, Any] | None) -> str:
    if not model_cfg or not model_cfg.get("points_required"):
        return ""
    kind = str(model_cfg.get("kind") or "").strip().lower()
    mode = str(model_cfg.get("billing_mode") or "per_use").strip().lower()
    if mode == "per_char":
        mode = "per_token"
    suffix = (
        "/秒"
        if kind == "video" and mode == "per_second"
        else "/token"
        if kind == "text" and mode == "per_token"
        else "/次"
    )
    prices = model_cfg.get("resolution_prices")
    if kind == "video" and isinstance(prices, dict) and prices:
        vals = sorted({parse_points(v) for v in prices.values() if parse_points(v) > ZERO})
        if not vals:
            return ""
        if len(vals) == 1:
            return f"{format_points(vals[0])}{suffix}"
        return f"{format_points(vals[0])}–{format_points(vals[-1])}{suffix}"
    price = format_points(model_cfg.get("points_price"))
    return f"{price}{suffix}"


def _unit_price_for_resolution(model_cfg: dict[str, Any], resolution: str | None) -> Decimal:
    """Per-use or per-second unit price for a resolution. Falls back to points_price."""
    key = str(resolution or "自动").strip() or "自动"
    prices = model_cfg.get("resolution_prices")
    if isinstance(prices, dict) and prices:
        if key in prices:
            return parse_points(prices.get(key), default="0.000")
        want = key.lower()
        for pk, pv in prices.items():
            if str(pk).strip().lower() == want:
                return parse_points(pv, default="0.000")
        # Only fall back to 自动 when the requested resolution is 自动 itself
        if want in ("自动", "auto") and "自动" in prices:
            return parse_points(prices.get("自动"), default="0.000")
    return parse_points(model_cfg.get("points_price"), default="0.000")


def reserve(
    user_id: str,
    amount: Decimal | Any,
    task_id: str,
    *,
    meta: dict[str, Any] | None = None,
) -> Decimal:
    """Deduct now; keep a reserve record for later commit/refund."""
    from backend import app_users

    uid = (user_id or "").strip()
    tid = (task_id or "").strip()
    need = parse_points(amount)
    if not uid or not tid:
        raise ValueError("user_id and task_id required")
    if need <= ZERO:
        return get_balance(uid)

    with _LOCK:
        data = _load_ledger()
        if tid in data["reserves"]:
            # Already reserved for this task
            return app_users.get_points(uid)
        bal = app_users.get_points(uid)
        if bal < need:
            raise InsufficientPointsError(bal, need)
        new_bal = app_users.adjust_points(uid, -need)
        data["reserves"][tid] = {
            "user_id": uid,
            "amount": format_points(need),
            "status": "reserved",
            "at": _now_iso(),
            "meta": meta or {},
        }
        _append_entry(
            data,
            user_id=uid,
            amount=-need,
            balance_after=new_bal,
            reason="reserve",
            task_id=tid,
            meta=meta,
        )
        _save_ledger(data)
        logger.info("points reserve user=%s task=%s amount=%s bal=%s", uid, tid, need, new_bal)
        return new_bal


def get_reserve(task_id: str) -> dict[str, Any] | None:
    tid = str(task_id or "").strip()
    if not tid:
        return None
    with _LOCK:
        data = _load_ledger()
        row = data["reserves"].get(tid)
        return dict(row) if isinstance(row, dict) else None


def commit(
    task_id: str,
    *,
    actual_amount: Decimal | Any | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Finalize a reserve. Optionally settle to actual_amount (token billing true-up)."""
    from backend import app_users

    tid = (task_id or "").strip()
    if not tid:
        return False
    with _LOCK:
        data = _load_ledger()
        row = data["reserves"].get(tid)
        if not row or row.get("status") != "reserved":
            return False
        uid = str(row.get("user_id") or "")
        reserved = parse_points(row.get("amount"))
        extra_meta = meta if isinstance(meta, dict) else {}
        if actual_amount is not None:
            actual = parse_points(actual_amount)
            delta = actual - reserved
            if delta > ZERO:
                bal_now = app_users.get_points(uid) if uid else ZERO
                # Deduct remaining; if short, take all remaining balance.
                take = delta if bal_now >= delta else bal_now
                if take > ZERO and uid:
                    app_users.adjust_points(uid, -take)
                row["amount"] = format_points(reserved + take)
            elif delta < ZERO:
                refund_amt = -delta
                if uid:
                    app_users.adjust_points(uid, refund_amt)
                row["amount"] = format_points(actual)
            # Patch the original reserve ledger row for history display.
            for ent in reversed(data.get("entries") or []):
                if (
                    isinstance(ent, dict)
                    and ent.get("task_id") == tid
                    and ent.get("reason") == "reserve"
                    and str(ent.get("user_id") or "") == uid
                ):
                    final_amt = parse_points(row.get("amount"))
                    ent["amount"] = format_delta(-final_amt)
                    ent["balance_after"] = format_points(app_users.get_points(uid)) if uid else ent.get("balance_after")
                    old_meta = ent.get("meta") if isinstance(ent.get("meta"), dict) else {}
                    ent["meta"] = {**old_meta, **extra_meta, "settled": True}
                    break
            row_meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            row["meta"] = {**row_meta, **extra_meta, "settled": True}
        row["status"] = "committed"
        row["committed_at"] = _now_iso()
        _append_entry(
            data,
            user_id=uid,
            amount=ZERO,
            balance_after=app_users.get_points(uid) if uid else ZERO,
            reason="commit",
            task_id=tid,
            meta=extra_meta or None,
        )
        _save_ledger(data)
        return True


def refund(task_id: str, *, reason: str = "refund") -> bool:
    from backend import app_users

    tid = (task_id or "").strip()
    if not tid:
        return False
    with _LOCK:
        data = _load_ledger()
        row = data["reserves"].get(tid)
        if not row or row.get("status") != "reserved":
            return False
        uid = str(row.get("user_id") or "")
        amt = parse_points(row.get("amount"))
        row["status"] = "refunded"
        row["refunded_at"] = _now_iso()
        new_bal = app_users.adjust_points(uid, amt) if uid else ZERO
        _append_entry(
            data,
            user_id=uid,
            amount=amt,
            balance_after=new_bal,
            reason=reason,
            task_id=tid,
        )
        _save_ledger(data)
        logger.info("points refund user=%s task=%s amount=%s bal=%s", uid, tid, amt, new_bal)
        return True
