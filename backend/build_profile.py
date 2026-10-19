"""Runtime build / deploy profile (canvas-only server vs full desktop)."""

from __future__ import annotations

import importlib.util
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.config import ADMIN_PORT, INTERNAL_CANVAS_URL, PORT, app_root

_PROFILE_FILE = app_root() / "build_profile.json"
_VERSION_FILE = app_root() / "version.json"
_POOL_FILE = app_root() / "browser_channels" / "pool.py"


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_bool(val: Any, default: bool = False) -> bool:
    """JSON / env / form bools — never treat the string 'false' as True."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        s = val.strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off", ""}:
            return False
    return default


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    data: dict[str, Any] = {
        "disable_browser_channels": False,
        "disable_doubao": False,  # legacy alias
        "product_label": "无限画布",
        "skip_playwright_pack": False,
    }
    if _PROFILE_FILE.is_file():
        try:
            raw = json.loads(_PROFILE_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    if _env_truthy("CANVAS_DISABLE_BROWSER_CHANNELS") or _env_truthy("CANVAS_DISABLE_DOUBAO"):
        data["disable_browser_channels"] = True
        data["disable_doubao"] = True
    if _env_truthy("CANVAS_SKIP_PLAYWRIGHT"):
        data["skip_playwright_pack"] = True
    # Browser simulation (Doubao/GPT Playwright) has been removed.
    data["disable_browser_channels"] = True
    data["disable_doubao"] = True
    data["skip_playwright_pack"] = _as_bool(data.get("skip_playwright_pack"))
    return data


def browser_channels_disabled() -> bool:
    return bool(load().get("disable_browser_channels"))


def doubao_disabled() -> bool:
    """Legacy name — same as ``browser_channels_disabled``."""
    return browser_channels_disabled()


def browser_channels_pool_path() -> Path:
    return _POOL_FILE


def browser_channels_code_present() -> bool:
    """True when Doubao/GPT browser sim package is on disk / importable."""
    # Prefer filesystem — portable packs sometimes fail find_spec under -m launches.
    if _POOL_FILE.is_file():
        return True
    cwd_pool = Path.cwd() / "browser_channels" / "pool.py"
    if cwd_pool.is_file():
        return True
    try:
        if importlib.util.find_spec("browser_channels.pool") is not None:
            return True
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    return False


def pack_kind() -> str:
    return str(load().get("pack_kind") or "").strip().lower()


def is_full_pack_profile() -> bool:
    kind = pack_kind()
    return kind.startswith("full") or kind in {"desktop", "doubao", "doubaocanvas"}


def deploy_diag() -> dict[str, Any]:
    """Why admin shows full vs 纯 API — for on-screen troubleshooting."""
    prof = load()
    disabled = browser_channels_disabled()
    pool_ok = browser_channels_code_present()
    full_profile = is_full_pack_profile()
    env_disable = _env_truthy("CANVAS_DISABLE_BROWSER_CHANNELS") or _env_truthy(
        "CANVAS_DISABLE_DOUBAO"
    )
    has = (not disabled) and (pool_ok or full_profile)
    reasons: list[str] = []
    if disabled:
        reasons.append("build_profile.json 里 disable_browser_channels=true（或环境变量禁用）")
    if env_disable:
        reasons.append("环境变量 CANVAS_DISABLE_BROWSER_CHANNELS / CANVAS_DISABLE_DOUBAO 已开启")
    if not pool_ok:
        reasons.append(f"未找到 {_POOL_FILE}")
    if full_profile and not disabled:
        reasons.append("pack_kind 为全功能，已按全功能启用界面")
    if has and not reasons:
        reasons.append("全功能检测通过")
    if not has and not reasons:
        reasons.append("未检测到全功能包特征")
    return {
        "app_root": str(app_root()),
        "profile_file": str(_PROFILE_FILE),
        "profile_exists": _PROFILE_FILE.is_file(),
        "pack_kind": str(prof.get("pack_kind") or "") or "(空)",
        "disable_browser_channels": disabled,
        "pool_py": str(_POOL_FILE),
        "pool_py_exists": _POOL_FILE.is_file(),
        "cwd_pool_exists": (Path.cwd() / "browser_channels" / "pool.py").is_file(),
        "has_browser_channels": has,
        "reasons": reasons,
        "reason_text": "；".join(reasons),
    }


def enabled_channels() -> list[str]:
    channels = ["myapi"]
    if browser_channels_disabled():
        return channels
    if browser_channels_code_present() or is_full_pack_profile():
        # Dola / 浏览器 OpenAI 已下线；保留豆包 + MyApi（ChatGPT 走 BenDi）
        channels = ["doubao", "myapi"]
    return channels


def app_version() -> dict[str, Any]:
    prof = load()
    version = str(prof.get("version") or "").strip()
    build = int(prof.get("build") or 0)
    built_at = str(prof.get("built_at") or "").strip()
    if not version and not build and _VERSION_FILE.is_file():
        try:
            raw = json.loads(_VERSION_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                version = str(raw.get("version") or "").strip()
                build = int(raw.get("build") or 0)
                built_at = str(raw.get("built_at") or "").strip()
        except Exception:
            pass
    if not version:
        version = "dev"
    label = f"v{version} · build {build}" if build else f"v{version}"
    return {
        "version": version,
        "build": build,
        "built_at": built_at,
        "version_label": label,
    }


def public_config(user: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend import service_settings
    from backend.config import WORKER_PORT
    from backend.user_roles import can_use_browser_channels, normalize_role

    prof = load()
    disabled = browser_channels_disabled()
    # Full pack profile → treat as having browser channels even if import glitches.
    has_bc = (not disabled) and (
        browser_channels_code_present() or is_full_pack_profile()
    )
    role = normalize_role(user.get("role") if user else None)
    user_allowed = bool(user and can_use_browser_channels(role))
    browser_ok = has_bc and not disabled and user_allowed
    channels = enabled_channels() if browser_ok else ["myapi"]
    if browser_ok and "myapi" not in channels:
        channels = [*channels, "myapi"]

    volcengine_platform_on = bool(service_settings.get_volcengine_channel_enabled())
    volcengine_view: dict[str, Any] = {
        "platform_enabled": volcengine_platform_on,
        "channel_ready": False,
        "kinds_enabled": {"image": False, "video": False, "text": False, "voice": False},
        "api_key_set": False,
    }
    if user and volcengine_platform_on:
        try:
            from backend.volcengine import settings as ve_settings

            volcengine_view = {**ve_settings.public_view(), "platform_enabled": True}
        except Exception:
            pass
    if volcengine_view.get("channel_ready") and "volcengine" not in channels:
        channels = [*channels, "volcengine"]

    kind = str(prof.get("pack_kind") or "").strip() or (
        "api" if disabled or not has_bc else "full"
    )
    diag = deploy_diag()
    return {
        "disable_browser_channels": not browser_ok,
        "disable_doubao": not browser_ok,
        "browser_channels_allowed": browser_ok,
        "bendi_allowed": False,
        "bendi_mode": "",
        "bendi_supports_text": False,
        "user_role": role if user else None,
        "channels": channels,
        "volcengine": volcengine_view,
        "volcengine_allowed": bool(volcengine_view.get("channel_ready")),
        "product_label": str(prof.get("product_label") or "无限画布"),
        "has_browser_channels": has_bc,
        "pack_kind": kind,
        "app_root": str(app_root()),
        "deploy_diag": diag,
        "browser_sim_on_worker": True,
        "admin_port": ADMIN_PORT,
        "canvas_port": PORT,
        "worker_port": WORKER_PORT,
        "internal_canvas_url": INTERNAL_CANVAS_URL,
        "upscale_enabled": service_settings.get_upscale_enabled(),
        "upscale_pool_mode": service_settings.get_upscale_pool_mode(),
        "allow_frontend_custom_models": service_settings.get_allow_frontend_custom_models(),
        "points_enabled": service_settings.get_points_enabled(),
        "myapi_api_styles_enabled": service_settings.get_myapi_api_styles_enabled(),
        "ui_display": service_settings.get_ui_display(),
        **__payment_public(),
        **app_version(),
    }


def __payment_public() -> dict[str, Any]:
    try:
        from backend import payment

        return payment.public_payment_info()
    except Exception:
        return {
            "payment_enabled": False,
            "payment_wx_enabled": False,
            "payment_alipay_enabled": False,
            "payment_min_amount": "5.00",
            "payment_rate": "1",
        }


def assert_doubao_allowed() -> None:
    if browser_channels_disabled():
        raise RuntimeError("本部署仅支持 MyApi 渠道。")
