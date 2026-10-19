"""Persisted service preferences (silent mode, browser path, proxy, etc.)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR, app_root
from backend.json_io import load_json

logger = logging.getLogger(__name__)

from backend.user_scope import service_config_file as _service_config_file


def _config_path() -> Path:
    return _service_config_file()

_DEFAULTS: dict[str, Any] = {
    # Deployed on a public server: MyApi reference media uses public_base_url + /uploads/...
    "server_mode": False,
    # e.g. https://canvas.example.com (no trailing slash)
    "public_base_url": "",
    # GPU Worker 外网地址（超分连接链接）；留空则本机 127.0.0.1:8089。服务器打包时写入。
    "upscale_worker_public_url": "",
    # True = silent (headed but minimized; true Playwright headless breaks Doubao UI)
    # False = show browser while generating
    "headless": False,
    # True = launch browser minimized (taskbar); user can restore manually
    "browser_start_minimized": True,
    # Optional absolute path to chrome.exe / msedge.exe; empty = auto detect
    "browser_executable": "",
    # Optional proxy for GPT / Dola Chromium, e.g. http://127.0.0.1:7890
    # Empty = fall back to HTTPS_PROXY / HTTP_PROXY / ALL_PROXY, else direct
    "proxy_server": "",
    # 总开关：关闭后前台隐藏超分入口，接口也拒绝超分任务
    "upscale_enabled": False,
    # AI 超分：无本地 Vulkan/GPU 时走远程 Worker；为 true 时强制远程（即使本机有引擎）
    "upscale_remote_force": False,
    # 远程 Worker 鉴权；留空则不校验（仅内网使用）
    "upscale_worker_token": "",
    # 8089 任务服务（MyApi 生成）回调鉴权；留空则启动时自动生成
    "job_worker_token": "",
    # shared = 公用一台本地 GPU；per_user = 每用户各自挂本地超分
    "upscale_pool_mode": "shared",
    # Per canvas-user max concurrent browser jobs (使用人浏览器线程上限)
    "max_threads": 2,
    # Machine-wide concurrent Chromium windows (all users). Extra jobs wait in queue.
    "max_browsers": 4,
    # When false, canvas users cannot add/edit/delete custom MyApi models (public models still work)
    "allow_frontend_custom_models": True,
    # Master switch: hide points UI and skip reserve/commit on generation
    "points_enabled": True,
    # NTPay online recharge
    "payment_enabled": False,
    "payment_mid": "",
    "payment_key": "",
    "payment_api_base": "https://ntpay.ppbbs.top",
    "payment_wx_enabled": True,
    "payment_alipay_enabled": True,
    "payment_min_amount": "0.01",
    "payment_rate": "1",
    # Doubao: share-link → zacao media-parser (no-watermark) before download
    "doubao_remove_watermark": False,
    "zacao_api_base": "https://video.zacao.top",
    "zacao_api_key": "",
    # MyApi API styles enable map — disabled styles reject generation with 供应商已失效
    "myapi_api_styles_enabled": {
        "generic": True,
    },
    # BenDi 本机 API（仅 127.0.0.1，仅管理员）：xingwan=星晚 / local=dola-pool
    "bendi_mode": "xingwan",
    "bendi_api_base": "http://127.0.0.1:7901",
    "bendi_api_key": "",
    "bendi_owner": "doubao_canvas",
    "bendi_local_api_base": "http://127.0.0.1:18765",
    "bendi_local_api_key": "",
    # Frontend display-name overrides only (never change operational IDs)
    "ui_display": {"channels": {}, "models": {}},
    # Volcengine channel: platform master switch (per-user key/kinds in volcengine.json)
    "volcengine_channel_enabled": True,
}


def _ensure_parent() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_browser_executable(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser())
    except Exception:
        return text


def _normalize_proxy_server(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Accept host:port → assume http
    if "://" not in text and ":" in text:
        text = f"http://{text}"
    return text


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_public_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    return text


def _normalize_myapi_api_styles_enabled(value: Any) -> dict[str, bool]:
    from backend.myapi_store import API_STYLES

    defaults = {sid: True for sid in API_STYLES}
    if not isinstance(value, dict):
        return defaults
    out = dict(defaults)
    for sid in API_STYLES:
        if sid in value:
            out[sid] = bool(value.get(sid))
    return out


def _origin_from_request_headers(request) -> str:
    """Browser-visible origin from Origin / Referer (keeps non-default ports)."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin and "://" in origin:
        return origin
    referer = (request.headers.get("referer") or "").strip()
    if referer:
        try:
            parsed = urllib.parse.urlparse(referer)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        except Exception:
            pass
    return ""


def _origin_from_proxy_headers(request) -> str:
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not host:
        return ""
    if ":" not in host:
        port_hdr = (request.headers.get("x-forwarded-port") or "").split(",")[0].strip()
        if port_hdr.isdigit():
            port = int(port_hdr)
            default = 443 if scheme == "https" else 80
            if port != default:
                host = f"{host}:{port}"
    return f"{scheme}://{host}".rstrip("/")


def enrich_public_url_with_request_port(server_url: str, request) -> str:
    """
    If server_url omits a port but the client request used one (same host),
    copy the port so worker links match the address bar.
    """
    explicit = _normalize_public_base_url(server_url)
    if not explicit or not request:
        return explicit
    detected = _origin_from_request_headers(request) or _origin_from_proxy_headers(request)
    if not detected:
        return explicit
    try:
        src = urllib.parse.urlparse(explicit)
        det = urllib.parse.urlparse(detected)
    except Exception:
        return explicit
    if not src.hostname or not det.hostname:
        return explicit
    if src.hostname.lower() != det.hostname.lower():
        return explicit
    if src.port or not det.port:
        return explicit
    netloc = f"{src.hostname}:{det.port}"
    return urllib.parse.urlunparse((src.scheme, netloc, "", "", "", "")).rstrip("/")


def request_external_base_url(request) -> str:
    """
    Public origin as seen by the browser (scheme + host[:port]).
    Used for upscale worker connect links behind reverse proxies.
    """
    configured = get_public_base_url()
    if configured:
        return enrich_public_url_with_request_port(configured, request)

    from backend.config import INTERNAL_CANVAS_URL

    origin = _origin_from_request_headers(request) or _origin_from_proxy_headers(request)
    if origin:
        return origin
    return INTERNAL_CANVAS_URL.rstrip("/")


def load() -> dict[str, Any]:
    data = dict(_DEFAULTS)
    if not _config_path().is_file():
        return data
    try:
        raw = load_json(_config_path(), default={})
        if isinstance(raw, dict):
            if "headless" in raw:
                data["headless"] = bool(raw["headless"])
            if "browser_start_minimized" in raw:
                data["browser_start_minimized"] = bool(raw["browser_start_minimized"])
            if "browser_executable" in raw:
                data["browser_executable"] = _normalize_browser_executable(
                    raw.get("browser_executable")
                )
            if "proxy_server" in raw:
                data["proxy_server"] = _normalize_proxy_server(raw.get("proxy_server"))
            if "upscale_enabled" in raw:
                data["upscale_enabled"] = bool(raw.get("upscale_enabled"))
            if "upscale_remote_force" in raw:
                data["upscale_remote_force"] = bool(raw.get("upscale_remote_force"))
            if "upscale_worker_token" in raw:
                data["upscale_worker_token"] = str(raw.get("upscale_worker_token") or "").strip()
            if "job_worker_token" in raw:
                data["job_worker_token"] = str(raw.get("job_worker_token") or "").strip()
            if "upscale_pool_mode" in raw:
                mode = str(raw.get("upscale_pool_mode") or "shared").strip().lower()
                data["upscale_pool_mode"] = "per_user" if mode == "per_user" else "shared"
            if "max_threads" in raw:
                try:
                    data["max_threads"] = max(1, min(8, int(raw.get("max_threads"))))
                except (TypeError, ValueError):
                    pass
            if "max_browsers" in raw:
                try:
                    data["max_browsers"] = max(1, min(64, int(raw.get("max_browsers"))))
                except (TypeError, ValueError):
                    pass
            if "server_mode" in raw:
                data["server_mode"] = bool(raw.get("server_mode"))
            if "public_base_url" in raw:
                data["public_base_url"] = _normalize_public_base_url(raw.get("public_base_url"))
            if "upscale_worker_public_url" in raw:
                data["upscale_worker_public_url"] = _normalize_public_base_url(
                    raw.get("upscale_worker_public_url")
                )
            if "allow_frontend_custom_models" in raw:
                data["allow_frontend_custom_models"] = bool(raw.get("allow_frontend_custom_models"))
            if "points_enabled" in raw:
                data["points_enabled"] = bool(raw.get("points_enabled"))
            if "payment_enabled" in raw:
                data["payment_enabled"] = bool(raw.get("payment_enabled"))
            if "payment_mid" in raw:
                data["payment_mid"] = str(raw.get("payment_mid") or "").strip()
            if "payment_key" in raw:
                data["payment_key"] = str(raw.get("payment_key") or "").strip()
            if "payment_api_base" in raw:
                base = str(raw.get("payment_api_base") or "").strip().rstrip("/")
                data["payment_api_base"] = base or "https://ntpay.ppbbs.top"
            if "payment_wx_enabled" in raw:
                data["payment_wx_enabled"] = bool(raw.get("payment_wx_enabled"))
            if "payment_alipay_enabled" in raw:
                data["payment_alipay_enabled"] = bool(raw.get("payment_alipay_enabled"))
            if "payment_min_amount" in raw:
                data["payment_min_amount"] = str(raw.get("payment_min_amount") or "0.01").strip() or "0.01"
            if "payment_rate" in raw:
                data["payment_rate"] = str(raw.get("payment_rate") or "1").strip() or "1"
            if "doubao_remove_watermark" in raw:
                data["doubao_remove_watermark"] = bool(raw.get("doubao_remove_watermark"))
            if "zacao_api_base" in raw:
                base = str(raw.get("zacao_api_base") or "").strip().rstrip("/")
                data["zacao_api_base"] = base or "https://video.zacao.top"
            if "zacao_api_key" in raw:
                data["zacao_api_key"] = str(raw.get("zacao_api_key") or "").strip()
            if "myapi_api_styles_enabled" in raw:
                data["myapi_api_styles_enabled"] = _normalize_myapi_api_styles_enabled(
                    raw.get("myapi_api_styles_enabled")
                )
            if "volcengine_channel_enabled" in raw:
                data["volcengine_channel_enabled"] = bool(raw.get("volcengine_channel_enabled"))
            if "bendi_mode" in raw:
                mode = str(raw.get("bendi_mode") or "").strip().lower()
                data["bendi_mode"] = (
                    "local"
                    if mode in ("local", "dola", "dola_pool", "本机", "bendi_local")
                    else "xingwan"
                )
            if "bendi_api_base" in raw:
                base = str(raw.get("bendi_api_base") or "").strip().rstrip("/")
                data["bendi_api_base"] = base or "http://127.0.0.1:7901"
            if "bendi_api_key" in raw:
                data["bendi_api_key"] = str(raw.get("bendi_api_key") or "").strip()
            if "bendi_owner" in raw:
                data["bendi_owner"] = str(raw.get("bendi_owner") or "").strip() or "doubao_canvas"
            if "bendi_local_api_base" in raw:
                base = str(raw.get("bendi_local_api_base") or "").strip().rstrip("/")
                data["bendi_local_api_base"] = base or "http://127.0.0.1:18765"
            if "bendi_local_api_key" in raw:
                data["bendi_local_api_key"] = str(raw.get("bendi_local_api_key") or "").strip()
            if "ui_display" in raw:
                from backend.ui_display import normalize_ui_display

                data["ui_display"] = normalize_ui_display(raw.get("ui_display"))
    except Exception as exc:
        logger.warning("failed to read service_config: %s", exc)
    return data


def save(data: dict[str, Any]) -> dict[str, Any]:
    current = load()
    merged = dict(_DEFAULTS)
    if "headless" in data:
        merged["headless"] = bool(data["headless"])
    else:
        merged["headless"] = bool(current.get("headless"))
    if "browser_start_minimized" in data:
        merged["browser_start_minimized"] = bool(data["browser_start_minimized"])
    else:
        merged["browser_start_minimized"] = bool(
            current.get("browser_start_minimized", _DEFAULTS["browser_start_minimized"])
        )
    if "browser_executable" in data:
        merged["browser_executable"] = _normalize_browser_executable(
            data.get("browser_executable")
        )
    else:
        merged["browser_executable"] = _normalize_browser_executable(
            current.get("browser_executable")
        )
    if "proxy_server" in data:
        merged["proxy_server"] = _normalize_proxy_server(data.get("proxy_server"))
    else:
        merged["proxy_server"] = _normalize_proxy_server(current.get("proxy_server"))
    if "upscale_enabled" in data:
        merged["upscale_enabled"] = bool(data.get("upscale_enabled"))
    else:
        merged["upscale_enabled"] = bool(current.get("upscale_enabled", True))
    if "upscale_remote_force" in data:
        merged["upscale_remote_force"] = bool(data.get("upscale_remote_force"))
    else:
        merged["upscale_remote_force"] = bool(current.get("upscale_remote_force"))
    if "upscale_worker_token" in data:
        merged["upscale_worker_token"] = str(data.get("upscale_worker_token") or "").strip()
    else:
        merged["upscale_worker_token"] = str(current.get("upscale_worker_token") or "").strip()
    if "job_worker_token" in data:
        merged["job_worker_token"] = str(data.get("job_worker_token") or "").strip()
    else:
        merged["job_worker_token"] = str(current.get("job_worker_token") or "").strip()
    if "upscale_pool_mode" in data:
        mode = str(data.get("upscale_pool_mode") or "shared").strip().lower()
        merged["upscale_pool_mode"] = "per_user" if mode == "per_user" else "shared"
    else:
        mode = str(current.get("upscale_pool_mode") or "shared").strip().lower()
        merged["upscale_pool_mode"] = "per_user" if mode == "per_user" else "shared"
    if "max_threads" in data:
        try:
            merged["max_threads"] = max(1, min(8, int(data.get("max_threads"))))
        except (TypeError, ValueError):
            merged["max_threads"] = int(current.get("max_threads") or 2)
    else:
        merged["max_threads"] = int(current.get("max_threads") or 2)
    if "max_browsers" in data:
        try:
            merged["max_browsers"] = max(1, min(64, int(data.get("max_browsers"))))
        except (TypeError, ValueError):
            merged["max_browsers"] = int(current.get("max_browsers") or _DEFAULTS["max_browsers"])
    else:
        try:
            merged["max_browsers"] = max(
                1, min(64, int(current.get("max_browsers") or _DEFAULTS["max_browsers"]))
            )
        except (TypeError, ValueError):
            merged["max_browsers"] = int(_DEFAULTS["max_browsers"])
    if "server_mode" in data:
        merged["server_mode"] = bool(data.get("server_mode"))
    else:
        merged["server_mode"] = bool(current.get("server_mode"))
    if "public_base_url" in data:
        merged["public_base_url"] = _normalize_public_base_url(data.get("public_base_url"))
    else:
        merged["public_base_url"] = _normalize_public_base_url(current.get("public_base_url"))
    if "upscale_worker_public_url" in data:
        merged["upscale_worker_public_url"] = _normalize_public_base_url(
            data.get("upscale_worker_public_url")
        )
    else:
        merged["upscale_worker_public_url"] = _normalize_public_base_url(
            current.get("upscale_worker_public_url")
        )
    if "allow_frontend_custom_models" in data:
        merged["allow_frontend_custom_models"] = bool(data.get("allow_frontend_custom_models"))
    else:
        merged["allow_frontend_custom_models"] = bool(
            current.get("allow_frontend_custom_models", True)
        )
    if "points_enabled" in data:
        merged["points_enabled"] = bool(data.get("points_enabled"))
    else:
        merged["points_enabled"] = bool(current.get("points_enabled", True))
    if "payment_enabled" in data:
        merged["payment_enabled"] = bool(data.get("payment_enabled"))
    else:
        merged["payment_enabled"] = bool(current.get("payment_enabled"))
    if "payment_mid" in data:
        merged["payment_mid"] = str(data.get("payment_mid") or "").strip()
    else:
        merged["payment_mid"] = str(current.get("payment_mid") or "").strip()
    if "payment_key" in data:
        merged["payment_key"] = str(data.get("payment_key") or "").strip()
    else:
        merged["payment_key"] = str(current.get("payment_key") or "").strip()
    if "payment_api_base" in data:
        base = str(data.get("payment_api_base") or "").strip().rstrip("/")
        merged["payment_api_base"] = base or "https://ntpay.ppbbs.top"
    else:
        base = str(current.get("payment_api_base") or "").strip().rstrip("/")
        merged["payment_api_base"] = base or "https://ntpay.ppbbs.top"
    if "payment_wx_enabled" in data:
        merged["payment_wx_enabled"] = bool(data.get("payment_wx_enabled"))
    else:
        merged["payment_wx_enabled"] = bool(current.get("payment_wx_enabled", True))
    if "payment_alipay_enabled" in data:
        merged["payment_alipay_enabled"] = bool(data.get("payment_alipay_enabled"))
    else:
        merged["payment_alipay_enabled"] = bool(current.get("payment_alipay_enabled", True))
    if "payment_min_amount" in data:
        merged["payment_min_amount"] = str(data.get("payment_min_amount") or "0.01").strip() or "0.01"
    else:
        merged["payment_min_amount"] = str(current.get("payment_min_amount") or "0.01").strip() or "0.01"
    if "payment_rate" in data:
        merged["payment_rate"] = str(data.get("payment_rate") or "1").strip() or "1"
    else:
        merged["payment_rate"] = str(current.get("payment_rate") or "1").strip() or "1"
    if "doubao_remove_watermark" in data:
        merged["doubao_remove_watermark"] = bool(data.get("doubao_remove_watermark"))
    else:
        merged["doubao_remove_watermark"] = bool(current.get("doubao_remove_watermark"))
    if "zacao_api_base" in data:
        base = str(data.get("zacao_api_base") or "").strip().rstrip("/")
        merged["zacao_api_base"] = base or "https://video.zacao.top"
    else:
        base = str(current.get("zacao_api_base") or "").strip().rstrip("/")
        merged["zacao_api_base"] = base or "https://video.zacao.top"
    if "zacao_api_key" in data:
        merged["zacao_api_key"] = str(data.get("zacao_api_key") or "").strip()
    else:
        merged["zacao_api_key"] = str(current.get("zacao_api_key") or "").strip()
    if "myapi_api_styles_enabled" in data:
        merged["myapi_api_styles_enabled"] = _normalize_myapi_api_styles_enabled(
            data.get("myapi_api_styles_enabled")
        )
    else:
        merged["myapi_api_styles_enabled"] = _normalize_myapi_api_styles_enabled(
            current.get("myapi_api_styles_enabled")
        )
    if "volcengine_channel_enabled" in data:
        merged["volcengine_channel_enabled"] = bool(data.get("volcengine_channel_enabled"))
    else:
        merged["volcengine_channel_enabled"] = bool(
            current.get("volcengine_channel_enabled", _DEFAULTS.get("volcengine_channel_enabled", True))
        )
    if "bendi_mode" in data:
        mode = str(data.get("bendi_mode") or "").strip().lower()
        merged["bendi_mode"] = (
            "local"
            if mode in ("local", "dola", "dola_pool", "本机", "bendi_local")
            else "xingwan"
        )
    else:
        mode = str(current.get("bendi_mode") or "xingwan").strip().lower()
        merged["bendi_mode"] = (
            "local"
            if mode in ("local", "dola", "dola_pool", "本机", "bendi_local")
            else "xingwan"
        )
    if "bendi_api_base" in data:
        base = str(data.get("bendi_api_base") or "").strip().rstrip("/")
        merged["bendi_api_base"] = base or "http://127.0.0.1:7901"
    else:
        base = str(current.get("bendi_api_base") or "").strip().rstrip("/")
        merged["bendi_api_base"] = base or "http://127.0.0.1:7901"
    if "bendi_api_key" in data:
        # Empty string from UI means "keep previous" when key already set —
        # callers that want to clear should send a dedicated clear flag.
        incoming = data.get("bendi_api_key")
        if incoming is None or str(incoming).strip() == "":
            merged["bendi_api_key"] = str(current.get("bendi_api_key") or "").strip()
        else:
            merged["bendi_api_key"] = str(incoming).strip()
    else:
        merged["bendi_api_key"] = str(current.get("bendi_api_key") or "").strip()
    if "bendi_owner" in data:
        merged["bendi_owner"] = str(data.get("bendi_owner") or "").strip() or "doubao_canvas"
    else:
        merged["bendi_owner"] = str(current.get("bendi_owner") or "").strip() or "doubao_canvas"
    if "bendi_local_api_base" in data:
        base = str(data.get("bendi_local_api_base") or "").strip().rstrip("/")
        merged["bendi_local_api_base"] = base or "http://127.0.0.1:18765"
    else:
        base = str(current.get("bendi_local_api_base") or "").strip().rstrip("/")
        merged["bendi_local_api_base"] = base or "http://127.0.0.1:18765"
    if "bendi_local_api_key" in data:
        incoming = data.get("bendi_local_api_key")
        if incoming is None or str(incoming).strip() == "":
            merged["bendi_local_api_key"] = str(current.get("bendi_local_api_key") or "").strip()
        else:
            merged["bendi_local_api_key"] = str(incoming).strip()
    else:
        merged["bendi_local_api_key"] = str(current.get("bendi_local_api_key") or "").strip()
    from backend.ui_display import normalize_ui_display

    if "ui_display" in data:
        merged["ui_display"] = normalize_ui_display(data.get("ui_display"))
    else:
        merged["ui_display"] = normalize_ui_display(current.get("ui_display"))
    # server_mode is legacy; public_base_url is optional (used only as upload fallback).
    _ensure_parent()
    _config_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged


def get_headless() -> bool:
    return bool(load().get("headless"))


def set_headless(value: bool) -> bool:
    data = load()
    data["headless"] = bool(value)
    save(data)
    return bool(data["headless"])


def get_browser_start_minimized() -> bool:
    return bool(load().get("browser_start_minimized", _DEFAULTS["browser_start_minimized"]))


def set_browser_start_minimized(value: bool) -> bool:
    data = load()
    data["browser_start_minimized"] = bool(value)
    save(data)
    return bool(data["browser_start_minimized"])


def get_browser_executable() -> str:
    return str(load().get("browser_executable") or "").strip()


def get_proxy_server() -> str:
    return str(load().get("proxy_server") or "").strip()


def get_upscale_worker_token() -> str:
    return str(load().get("upscale_worker_token") or "").strip()


def get_job_worker_token() -> str:
    return str(load().get("job_worker_token") or "").strip()


def ensure_job_worker_token() -> str:
    """Return persisted token, generating one on first use."""
    tok = get_job_worker_token()
    if tok:
        return tok
    tok = secrets.token_urlsafe(24)
    save({**load(), "job_worker_token": tok})
    return tok


def verify_job_worker_token(token: str | None) -> bool:
    expected = get_job_worker_token()
    if not expected:
        return True
    return bool(token) and secrets.compare_digest(str(token), expected)


def get_upscale_enabled() -> bool:
    return False


def get_upscale_pool_mode() -> str:
    mode = str(load().get("upscale_pool_mode") or "shared").strip().lower()
    return "per_user" if mode == "per_user" else "shared"


def get_allow_frontend_custom_models() -> bool:
    return bool(load().get("allow_frontend_custom_models", True))


def get_ui_display() -> dict:
    from backend.ui_display import normalize_ui_display

    return normalize_ui_display(load().get("ui_display"))


def get_points_enabled() -> bool:
    return False


def get_max_threads() -> int:
    try:
        return max(1, min(8, int(load().get("max_threads") or 2)))
    except (TypeError, ValueError):
        return 2


def get_max_browsers() -> int:
    """Global concurrent Chromium windows across all canvas users."""
    try:
        return max(1, min(64, int(load().get("max_browsers") or 4)))
    except (TypeError, ValueError):
        return 4


def get_server_mode() -> bool:
    if _env_truthy("CANVAS_SERVER_MODE"):
        return True
    return bool(load().get("server_mode"))


def get_public_base_url() -> str:
    env = (os.environ.get("CANVAS_PUBLIC_BASE_URL") or "").strip()
    if env:
        return _normalize_public_base_url(env)
    return _normalize_public_base_url(load().get("public_base_url"))


def _read_pack_worker_public_url() -> str:
    """Read worker_public_url.txt beside the exe (server pack default)."""
    path = app_root() / "worker_public_url.txt"
    if not path.is_file():
        return ""
    try:
        return _normalize_public_base_url(path.read_text(encoding="utf-8").strip())
    except OSError:
        return ""


def get_worker_public_base_url() -> str:
    """
    Public URL embedded in GPU upscale worker connect links.
    Falls back to loopback job worker (8089) when unset (local dev).
    """
    env = (os.environ.get("DOUBAO_WORKER_PUBLIC_URL") or "").strip()
    if env:
        return _normalize_public_base_url(env)
    configured = _normalize_public_base_url(load().get("upscale_worker_public_url"))
    if configured:
        return configured
    packed = _read_pack_worker_public_url()
    if packed:
        return packed
    from backend.config import INTERNAL_WORKER_URL

    return INTERNAL_WORKER_URL.rstrip("/")


def resolve_public_media_url(url: str) -> str | None:
    """
    Map canvas /uploads paths to absolute public URLs when public_base_url is set.
    Used as fallback when direct relay upload (/v1/media/uploads) fails.
    No signed ?mt= tokens — /uploads/ is served without download auth.
    """
    base = get_public_base_url()
    if not base:
        return None
    upload_path = upload_path_from_url(url)
    if not upload_path:
        return None
    return f"{base}{upload_path}"


def upload_path_from_url(url: str) -> str | None:
    """Extract /uploads/... path from relative or absolute media URL."""
    raw = (url or "").strip()
    if not raw or raw.startswith("data:"):
        return None
    path = raw
    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            path = urllib.parse.urlparse(raw).path or ""
        except Exception:
            return None
    elif raw.startswith("uploads/"):
        path = "/" + raw
    if not path.startswith("/uploads/"):
        return None
    return path.split("?", 1)[0].split("#", 1)[0]


def resolve_proxy() -> dict[str, str] | None:
    """
    Playwright proxy dict for GPT / Dola browsers.

    Preference: settings.proxy_server → env HTTPS_PROXY/HTTP_PROXY/ALL_PROXY → None.
    """
    configured = get_proxy_server()
    if configured:
        return {"server": configured}

    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return {"server": _normalize_proxy_server(raw)}
    return None


def resolve_proxy_url() -> str | None:
    """Proxy URL string for httpx (or None for direct)."""
    proxy = resolve_proxy()
    if not proxy:
        return None
    server = str(proxy.get("server") or "").strip()
    return server or None


def settings_with_browser() -> dict[str, Any]:
    """Settings payload. Browser simulation / Chromium detect has been removed."""
    data = load()
    proxy = resolve_proxy()
    return {
        **data,
        "browser": {
            "available": False,
            "source": "disabled",
            "path": "",
            "message": "开源版不含浏览器模拟",
        },
        "proxy_resolved": (proxy or {}).get("server") or "",
        "proxy_from": (
            "settings"
            if get_proxy_server()
            else ("env" if proxy else "none")
        ),
    }


def get_doubao_remove_watermark() -> bool:
    # Canvas-only / no-browser packs never enable Doubao watermark stripping.
    try:
        from backend.build_profile import browser_channels_disabled

        if browser_channels_disabled():
            return False
    except Exception:
        pass
    return bool(load().get("doubao_remove_watermark"))


def get_zacao_api_base() -> str:
    base = str(load().get("zacao_api_base") or "").strip().rstrip("/")
    return base or "https://video.zacao.top"


def get_zacao_api_key() -> str:
    return str(load().get("zacao_api_key") or "").strip()


def get_myapi_api_styles_enabled() -> dict[str, bool]:
    return _normalize_myapi_api_styles_enabled(load().get("myapi_api_styles_enabled"))


def get_volcengine_channel_enabled() -> bool:
    return bool(load().get("volcengine_channel_enabled", True))


def is_myapi_api_style_enabled(style_id: str) -> bool:
    sid = str(style_id or "").strip().lower()
    if not sid:
        return True
    enabled = get_myapi_api_styles_enabled()
    # Unknown / legacy ids stay enabled so old configs keep working.
    return bool(enabled.get(sid, True))


_CANVAS_SETTINGS_STRIP = frozenset(
    {
        "upscale_worker_token",
        "job_worker_token",
        "payment_key",
        "payment_mid",
        "zacao_api_key",
        "bendi_api_key",
        "bendi_local_api_key",
    }
)


def get_bendi_mode() -> str:
    mode = str(load().get("bendi_mode") or "xingwan").strip().lower()
    if mode in ("local", "dola", "dola_pool", "本机", "bendi_local"):
        return "local"
    return "xingwan"


def get_bendi_api_base() -> str:
    return str(load().get("bendi_api_base") or "http://127.0.0.1:7901").strip().rstrip("/")


def get_bendi_api_key() -> str:
    return str(load().get("bendi_api_key") or "").strip()


def get_bendi_owner() -> str:
    return str(load().get("bendi_owner") or "doubao_canvas").strip() or "doubao_canvas"


def get_bendi_local_api_base() -> str:
    return str(load().get("bendi_local_api_base") or "http://127.0.0.1:18765").strip().rstrip("/")


def get_bendi_local_api_key() -> str:
    return str(load().get("bendi_local_api_key") or "").strip()


def public_bendi_settings() -> dict[str, Any]:
    """Canvas UI fields for BenDi — mask API keys."""
    mode = get_bendi_mode()
    key = get_bendi_api_key()
    local_key = get_bendi_local_api_key()
    masked = ""
    if key:
        masked = ("*" * max(0, len(key) - 4)) + key[-4:] if len(key) > 4 else "****"
    local_masked = ""
    if local_key:
        local_masked = (
            ("*" * max(0, len(local_key) - 4)) + local_key[-4:]
            if len(local_key) > 4
            else "****"
        )
    return {
        "bendi_mode": mode,
        "bendi_supports_text": mode == "local",
        "bendi_api_base": get_bendi_api_base(),
        "bendi_owner": get_bendi_owner(),
        "bendi_api_key_set": bool(key),
        "bendi_api_key_masked": masked,
        "bendi_local_api_base": get_bendi_local_api_base(),
        "bendi_local_api_key_set": bool(local_key),
        "bendi_local_api_key_masked": local_masked,
    }


def public_canvas_settings() -> dict[str, Any]:
    """Canvas-facing settings without deployment secrets."""
    data = settings_with_browser()
    out = {k: v for k, v in data.items() if k not in _CANVAS_SETTINGS_STRIP}
    out.update(public_bendi_settings())
    return out
