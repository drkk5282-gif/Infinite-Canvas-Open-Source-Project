"""NTPay online recharge: create order, notify, credit points."""

from __future__ import annotations

import hashlib
import io
import logging
import base64
import secrets
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import httpx

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

ORDERS_FILE = DATA_DIR / "payment_orders.json"
_LOCK = threading.Lock()
_DEFAULT_API_BASE = "https://ntpay.ppbbs.top"
_MIN_DEFAULT = Decimal("0.01")
_MONEY_Q = Decimal("0.01")
_POINTS_Q = Decimal("0.001")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _money_str(value: Any) -> str:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        d = Decimal("0")
    if d < 0:
        d = Decimal("0")
    return str(d.quantize(_MONEY_Q, rounding=ROUND_HALF_UP))


def _points_from_money(money: Decimal, rate: Decimal) -> Decimal:
    pts = (money * rate).quantize(_POINTS_Q, rounding=ROUND_HALF_UP)
    return pts if pts > 0 else Decimal("0.000")


def get_config() -> dict[str, Any]:
    from backend import service_settings

    cfg = service_settings.load()
    mid = str(cfg.get("payment_mid") or "").strip()
    key = str(cfg.get("payment_key") or "").strip()
    api_base = str(cfg.get("payment_api_base") or _DEFAULT_API_BASE).strip().rstrip("/")
    if not api_base:
        api_base = _DEFAULT_API_BASE
    try:
        min_amount = Decimal(_money_str(cfg.get("payment_min_amount") or _MIN_DEFAULT))
    except Exception:
        min_amount = _MIN_DEFAULT
    if min_amount < Decimal("0.01"):
        min_amount = Decimal("0.01")
    # Fixed 1 yuan = 1 point
    rate = Decimal("1")
    enabled = bool(cfg.get("payment_enabled"))
    wx = bool(cfg.get("payment_wx_enabled", True))
    alipay = bool(cfg.get("payment_alipay_enabled", True))
    ready = enabled and bool(mid) and bool(key) and (wx or alipay)
    return {
        "enabled": enabled,
        "ready": ready,
        "mid": mid,
        "key": key,
        "api_base": api_base,
        "wx_enabled": wx,
        "alipay_enabled": alipay,
        "min_amount": min_amount,
        "rate": rate,
    }


def public_payment_info() -> dict[str, Any]:
    from backend import service_settings

    if not service_settings.get_points_enabled():
        return {
            "payment_enabled": False,
            "payment_wx_enabled": False,
            "payment_alipay_enabled": False,
            "payment_min_amount": "0.01",
            "payment_rate": "1",
        }
    cfg = get_config()
    return {
        "payment_enabled": bool(cfg["ready"]),
        "payment_wx_enabled": bool(cfg["wx_enabled"] and cfg["ready"]),
        "payment_alipay_enabled": bool(cfg["alipay_enabled"] and cfg["ready"]),
        "payment_min_amount": _money_str(cfg["min_amount"]),
        "payment_rate": "1",
    }


def _sign_create(mid: str, pay_id: str, param: str, pay_type: int, price: str, key: str) -> str:
    raw = f"{mid}{pay_id}{param}{pay_type}{price}{key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _sign_notify(
    mid: str,
    pay_id: str,
    param: str,
    pay_type: str | int,
    price: str,
    really_price: str,
    key: str,
) -> str:
    raw = f"{mid}{pay_id}{param}{pay_type}{price}{really_price}{key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _load_orders() -> dict[str, Any]:
    raw = load_json(ORDERS_FILE, default={"orders": {}})
    if not isinstance(raw, dict):
        return {"orders": {}}
    orders = raw.get("orders")
    if not isinstance(orders, dict):
        orders = {}
    return {"orders": orders}


def _save_orders(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    orders = data.get("orders") or {}
    if isinstance(orders, dict) and len(orders) > 2000:
        # Keep newest ~1500 by created_at
        items = sorted(
            orders.items(),
            key=lambda kv: str((kv[1] or {}).get("created_at") or ""),
            reverse=True,
        )
        data["orders"] = dict(items[:1500])
    save_json(ORDERS_FILE, data)


def _qr_data_url(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    try:
        import qrcode

        img = qrcode.make(text)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.warning("qr encode failed: %s", exc)
        return ""


def _resolve_base_url(request=None) -> str:
    from backend import service_settings

    configured = service_settings.get_public_base_url()
    if configured:
        if request:
            return service_settings.enrich_public_url_with_request_port(configured, request)
        return configured
    if request:
        return service_settings.request_external_base_url(request)
    return ""


def create_order(
    *,
    user_id: str,
    amount: Any,
    pay_type: int,
    request=None,
    is_test: bool = False,
) -> dict[str, Any]:
    cfg = get_config()
    if not cfg["ready"]:
        raise ValueError("支付系统未开启或未配置完成")
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("用户无效")
    try:
        money = Decimal(_money_str(amount))
    except Exception as exc:
        raise ValueError("金额无效") from exc
    if money < cfg["min_amount"] and not is_test:
        raise ValueError(f"最低充值 {_money_str(cfg['min_amount'])} 积分")
    if is_test and money < Decimal("0.01"):
        raise ValueError("测试金额最低 0.01")
    ptype = int(pay_type)
    if ptype not in (1, 2):
        raise ValueError("支付渠道无效")
    if ptype == 1 and not cfg["wx_enabled"]:
        raise ValueError("微信支付未开启")
    if ptype == 2 and not cfg["alipay_enabled"]:
        raise ValueError("支付宝未开启")

    price = _money_str(money)
    pay_id = f"{int(time.time() * 1000)}{secrets.token_hex(3)}"
    param = uid
    mid = cfg["mid"]
    key = cfg["key"]
    sign = _sign_create(mid, pay_id, param, ptype, price, key)
    base = _resolve_base_url(request)
    notify_url = f"{base}/api/pay/notify" if base else ""
    return_url = f"{base}/api/pay/return" if base else ""

    payload = {
        "mid": mid,
        "payId": pay_id,
        "type": str(ptype),
        "price": price,
        "sign": sign,
        "param": param,
        "isHtml": "0",
    }
    if notify_url:
        payload["notifyUrl"] = notify_url
    if return_url:
        payload["returnUrl"] = return_url

    url = f"{cfg['api_base']}/createOrder"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            # NTPay accepts GET or POST; GET with query is most compatible
            resp = client.get(url, params=payload)
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.warning("createOrder failed: %s", exc)
        raise ValueError(f"创建支付订单失败：{exc}") from exc

    if not isinstance(body, dict) or int(body.get("code") or -1) != 1:
        msg = (body.get("msg") if isinstance(body, dict) else None) or "创建订单失败"
        raise ValueError(str(msg))

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    order_id = str(data.get("orderId") or "").strip()
    pay_url = str(data.get("payUrl") or "").strip()
    really_price = _money_str(data.get("reallyPrice") or price)
    points = _points_from_money(Decimal(price), cfg["rate"])

    order = {
        "pay_id": pay_id,
        "order_id": order_id,
        "user_id": uid,
        "pay_type": ptype,
        "price": price,
        "really_price": really_price,
        "points": str(points),
        "state": "pending",
        "credited": False,
        "is_test": bool(is_test),
        "pay_url": pay_url,
        "created_at": _now_iso(),
        "paid_at": "",
        "ntpay": {
            "isAuto": data.get("isAuto"),
            "timeOut": data.get("timeOut"),
            "date": data.get("date"),
        },
    }
    with _LOCK:
        store = _load_orders()
        store["orders"][pay_id] = order
        _save_orders(store)

    return {
        "pay_id": pay_id,
        "order_id": order_id,
        "pay_type": ptype,
        "price": price,
        "really_price": really_price,
        "points": str(points),
        "pay_url": pay_url,
        "qr_data_url": _qr_data_url(pay_url),
        "is_auto": int(data.get("isAuto") or 0),
        "time_out": int(data.get("timeOut") or 5),
        "state": "pending",
    }


def get_local_order(pay_id: str) -> dict[str, Any] | None:
    pid = str(pay_id or "").strip()
    if not pid:
        return None
    with _LOCK:
        store = _load_orders()
        row = store["orders"].get(pid)
        return dict(row) if isinstance(row, dict) else None


def _credit_order(order: dict[str, Any]) -> dict[str, Any]:
    """Idempotent credit. Caller must hold _LOCK and persist after."""
    if order.get("credited"):
        order["state"] = "paid"
        return order
    from backend import points

    uid = str(order.get("user_id") or "").strip()
    pts = order.get("points")
    if not uid:
        raise ValueError("订单缺少用户")
    pay_type = int(order.get("pay_type") or 0)
    channel = {1: "微信", 2: "支付宝"}.get(pay_type, "在线支付")
    bal = points.adjust_balance(
        uid,
        pts,
        reason="recharge",
        meta={
            "channel": channel,
            "name": f"{channel}充值",
            "pay_id": str(order.get("pay_id") or ""),
            "order_id": str(order.get("order_id") or ""),
            "pay_type": pay_type,
            "price": str(order.get("price") or ""),
            "really_price": str(order.get("really_price") or ""),
        },
    )
    order["credited"] = True
    order["state"] = "paid"
    order["paid_at"] = _now_iso()
    order["balance_after"] = points.format_points(bal)
    return order


def apply_notify(params: dict[str, Any]) -> str:
    """Process NTPay async/sync callback. Returns plain text response body."""
    cfg = get_config()
    if not cfg["key"] or not cfg["mid"]:
        return "error_config"
    mid = str(params.get("mid") or "").strip()
    pay_id = str(params.get("payId") or "").strip()
    param = str(params.get("param") or "")
    pay_type = str(params.get("type") or "").strip()
    price = str(params.get("price") or "").strip()
    really_price = str(params.get("reallyPrice") or "").strip()
    sign = str(params.get("sign") or "").strip().lower()
    if not pay_id or not sign:
        return "error_param"
    expect = _sign_notify(mid, pay_id, param, pay_type, price, really_price, cfg["key"]).lower()
    if expect != sign:
        logger.warning("payment notify bad sign payId=%s", pay_id)
        return "error_sign"
    if mid and mid != cfg["mid"]:
        return "error_mid"

    with _LOCK:
        store = _load_orders()
        order = store["orders"].get(pay_id)
        if not isinstance(order, dict):
            # Still accept: create a synthetic order from callback (rare race)
            try:
                money = Decimal(_money_str(really_price or price))
            except Exception:
                money = Decimal("0")
            order = {
                "pay_id": pay_id,
                "order_id": "",
                "user_id": str(param or "").strip(),
                "pay_type": int(pay_type) if str(pay_type).isdigit() else 0,
                "price": _money_str(price or money),
                "really_price": _money_str(really_price or price or money),
                "points": str(_points_from_money(money, cfg["rate"])),
                "state": "pending",
                "credited": False,
                "is_test": False,
                "created_at": _now_iso(),
                "paid_at": "",
            }
            store["orders"][pay_id] = order
        if order.get("credited"):
            return "success"
        if really_price:
            order["really_price"] = _money_str(really_price)
        try:
            _credit_order(order)
        except Exception as exc:
            logger.exception("credit failed payId=%s: %s", pay_id, exc)
            return "error_credit"
        store["orders"][pay_id] = order
        _save_orders(store)
    return "success"


def poll_order_status(pay_id: str) -> dict[str, Any]:
    """Return local status; if still pending, query NTPay checkOrder / getOrder."""
    order = get_local_order(pay_id)
    if not order:
        raise ValueError("订单不存在")
    if order.get("credited") or order.get("state") == "paid":
        return _public_order(order)

    cfg = get_config()
    order_id = str(order.get("order_id") or "").strip()
    if order_id and cfg["api_base"]:
        # Prefer getOrder for state field
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                resp = client.get(f"{cfg['api_base']}/getOrder", params={"orderId": order_id})
                body = resp.json()
            if isinstance(body, dict) and int(body.get("code") or -1) == 1:
                data = body.get("data") if isinstance(body.get("data"), dict) else {}
                state = int(data.get("state") or 0)
                if state in (1, 2):
                    # Paid — credit via notify-equivalent
                    with _LOCK:
                        store = _load_orders()
                        row = store["orders"].get(pay_id)
                        if isinstance(row, dict) and not row.get("credited"):
                            if data.get("reallyPrice") is not None:
                                row["really_price"] = _money_str(data.get("reallyPrice"))
                            _credit_order(row)
                            store["orders"][pay_id] = row
                            _save_orders(store)
                            order = row
                elif state == -1:
                    with _LOCK:
                        store = _load_orders()
                        row = store["orders"].get(pay_id)
                        if isinstance(row, dict) and row.get("state") == "pending":
                            row["state"] = "expired"
                            store["orders"][pay_id] = row
                            _save_orders(store)
                            order = row
        except Exception as exc:
            logger.debug("getOrder poll failed: %s", exc)

        # Also try checkOrder which returns jump URL when paid
        if not order.get("credited"):
            try:
                with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                    resp = client.get(f"{cfg['api_base']}/checkOrder", params={"orderId": order_id})
                    body = resp.json()
                if isinstance(body, dict) and int(body.get("code") or -1) == 1:
                    jump = str(body.get("data") or "")
                    # Parse callback params from jump URL if present
                    from urllib.parse import urlparse, parse_qs

                    qs = parse_qs(urlparse(jump).query)
                    flat = {k: (v[0] if v else "") for k, v in qs.items()}
                    if flat.get("sign"):
                        result = apply_notify(flat)
                        if result == "success":
                            order = get_local_order(pay_id) or order
            except Exception as exc:
                logger.debug("checkOrder poll failed: %s", exc)

    return _public_order(order or get_local_order(pay_id) or {})


def _public_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "pay_id": order.get("pay_id") or "",
        "order_id": order.get("order_id") or "",
        "pay_type": order.get("pay_type"),
        "price": order.get("price"),
        "really_price": order.get("really_price"),
        "points": order.get("points"),
        "state": "paid" if order.get("credited") else (order.get("state") or "pending"),
        "credited": bool(order.get("credited")),
        "balance_after": order.get("balance_after") or "",
        "pay_url": order.get("pay_url") or "",
        "qr_data_url": _qr_data_url(str(order.get("pay_url") or "")),
        "time_out": (order.get("ntpay") or {}).get("timeOut") if isinstance(order.get("ntpay"), dict) else 5,
    }


def close_order(pay_id: str) -> dict[str, Any]:
    cfg = get_config()
    order = get_local_order(pay_id)
    if not order:
        raise ValueError("订单不存在")
    if order.get("credited"):
        raise ValueError("订单已支付，无法关闭")
    order_id = str(order.get("order_id") or "").strip()
    if order_id and cfg["key"]:
        sign = hashlib.md5(f"{order_id}{cfg['key']}".encode("utf-8")).hexdigest()
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True) as client:
                client.get(
                    f"{cfg['api_base']}/closeOrder",
                    params={"orderId": order_id, "sign": sign},
                )
        except Exception as exc:
            logger.debug("closeOrder remote failed: %s", exc)
    with _LOCK:
        store = _load_orders()
        row = store["orders"].get(pay_id)
        if isinstance(row, dict) and not row.get("credited"):
            row["state"] = "closed"
            store["orders"][pay_id] = row
            _save_orders(store)
            order = row
    return _public_order(order)


def list_orders(
    *,
    state: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Admin listing of local recharge orders (newest first)."""
    st = str(state or "all").strip().lower()
    if st not in ("all", "paid", "pending", "closed", "expired"):
        st = "all"
    try:
        lim = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        lim = 100
    try:
        off = max(0, int(offset))
    except (TypeError, ValueError):
        off = 0

    with _LOCK:
        store = _load_orders()
        rows = [dict(v) for v in (store.get("orders") or {}).values() if isinstance(v, dict)]

    def _state_of(row: dict[str, Any]) -> str:
        if row.get("credited") or row.get("state") == "paid":
            return "paid"
        return str(row.get("state") or "pending")

    if st != "all":
        rows = [r for r in rows if _state_of(r) == st]

    rows.sort(
        key=lambda r: str(r.get("paid_at") or r.get("created_at") or ""),
        reverse=True,
    )
    total = len(rows)
    page = rows[off : off + lim]

    # Enrich with username
    user_cache: dict[str, str] = {}
    try:
        from backend import app_users

        for u in app_users.list_users():
            user_cache[str(u.get("id") or "")] = str(u.get("username") or u.get("display_name") or "")
    except Exception:
        pass

    out: list[dict[str, Any]] = []
    for r in page:
        uid = str(r.get("user_id") or "")
        pay_type = int(r.get("pay_type") or 0)
        out.append(
            {
                "pay_id": r.get("pay_id") or "",
                "order_id": r.get("order_id") or "",
                "user_id": uid,
                "username": user_cache.get(uid) or uid or "—",
                "pay_type": pay_type,
                "channel": {1: "微信", 2: "支付宝"}.get(pay_type, "—"),
                "price": r.get("price") or "",
                "really_price": r.get("really_price") or "",
                "points": r.get("points") or "",
                "state": _state_of(r),
                "credited": bool(r.get("credited")),
                "is_test": bool(r.get("is_test")),
                "created_at": r.get("created_at") or "",
                "paid_at": r.get("paid_at") or "",
                "balance_after": r.get("balance_after") or "",
            }
        )

    paid_sum = Decimal("0")
    paid_count = 0
    for r in rows:
        if _state_of(r) == "paid":
            paid_count += 1
            try:
                paid_sum += Decimal(str(r.get("points") or "0"))
            except (InvalidOperation, ValueError):
                pass

    return {
        "orders": out,
        "total": total,
        "limit": lim,
        "offset": off,
        "paid_count": paid_count,
        "paid_points": str(paid_sum.quantize(_POINTS_Q, rounding=ROUND_HALF_UP)),
    }

