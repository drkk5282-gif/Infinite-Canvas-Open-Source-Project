import asyncio
import io
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from backend import account_manager, canvas_store, log_buffer, task_manager, task_runtime
from backend.build_profile import browser_channels_disabled, doubao_disabled, public_config
from backend.channel_gate import (
    browser_channels_enabled,
    doubao_media_models,
    get_pool,
    reject_browser_channels_http,
)
from backend.config import BASE_DIR, DATA_DIR, HOST, INTERNAL_CANVAS_URL, PORT, UPLOADS_DIR
from backend.auth_deps import apply_user_scope, is_auth_exempt
from backend.user_scope import reset_current_user_id, set_current_user_id
from backend.media.refs import parse_reference_payload
from backend.models import (
    AccountCreate,
    AccountStatus,
    AccountUpdate,
    EnsureBrowserVideoRequest,
    GenerateRequest,
    ImageGenerateRequest,
    ImageToolRequest,
    QRLoginResponse,
    TaskStatus,
    TextGenerateRequest,
    VideoGenerateRequest,
    VideoToolRequest,
    VoiceGenerateRequest,
)

logger = logging.getLogger(__name__)


class _DropJianyingRelayLogs(logging.Filter):
    """This build has no Jianying relay. Ignore those polls in the console."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            return True
        return "/api/jianying/" not in text


logging.getLogger("uvicorn.access").addFilter(_DropJianyingRelayLogs())

FRONTEND_DIR = BASE_DIR / "frontend"
_CONFIG_ADMIN_MSG = "该配置已关闭"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

from backend.media.video_tools import UPLOAD_VIDEO_EXTENSIONS

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
ALLOWED_VIDEO_EXT = UPLOAD_VIDEO_EXTENSIONS
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("uvicorn.access").addFilter(_DropJianyingRelayLogs())
    log_buffer.install()
    # Windows Proactor: Playwright/page close often raises ConnectionResetError in a callback
    try:
        loop = asyncio.get_running_loop()
        prev = loop.get_exception_handler()

        def _handler(loop, context):
            exc = context.get("exception")
            if isinstance(exc, ConnectionResetError):
                logger.debug("ignored ConnectionResetError during transport close")
                return
            if prev:
                prev(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_handler)
    except Exception:
        pass
    if browser_channels_disabled():
        logger.info("build profile: canvas-only (MyApi / public HTTP channels; no browser simulation)")
    else:
        logger.info("build profile: full (browser_channels + MyApi)")
    logger.info(
        "data dir: %s (canvas=%s, accounts=%s, uploads=%s)",
        DATA_DIR,
        DATA_DIR / "canvas.json",
        DATA_DIR / "accounts.json",
        UPLOADS_DIR,
    )
    try:
        from backend import app_users

        app_users.ensure_bootstrap()
    except Exception as exc:
        logger.warning("user bootstrap failed: %s", exc)
    try:
        from backend.board_assets import migrate_legacy_uploads_to_boards

        migrate_legacy_uploads_to_boards()
    except Exception as exc:
        logger.warning("board asset migrate failed: %s", exc)
    try:
        from backend.board_assets import migrate_board_directory_layout

        migrate_board_directory_layout()
    except Exception as exc:
        logger.warning("board directory migrate failed: %s", exc)
    try:
        from backend.board_assets import repair_per_board_storage

        repair_per_board_storage()
    except Exception as exc:
        logger.warning("per-board storage repair failed: %s", exc)
    # Do not auto-run cleanup_uploads() on startup — unreferenced legacy files
    # stay until the user explicitly purges from Settings. Only expire items
    # already soft-deleted into data/trash/ past TTL.
    try:
        from backend.media.asset_trash import purge_expired_trash

        trash_stats = purge_expired_trash()
        if trash_stats.get("deleted"):
            logger.info("startup trash TTL: %s", trash_stats)
    except Exception as exc:
        logger.warning("startup trash TTL cleanup failed: %s", exc)
    try:
        from browser_channels.paths import migrate_all_accounts
        from backend import account_manager

        n = migrate_all_accounts(
            [a.model_dump() for a in account_manager.list_accounts()]
        )
        if n:
            logger.info("migrated %d legacy account browser dirs → browser_channels/", n)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("browser_channels data migrate failed: %s", exc)
    try:
        from backend.job_worker.reconcile import sync_myapi_tasks_from_worker

        await sync_myapi_tasks_from_worker()
    except Exception as exc:
        logger.warning("startup job worker reconcile failed: %s", exc)
    try:
        n = task_manager.fail_interrupted_tasks()
        if n:
            logger.info("cleared %d interrupted task(s) after restart", n)
    except Exception as exc:
        logger.warning("startup task cleanup failed: %s", exc)
    yield
    try:
        if browser_channels_enabled():
            await get_pool().close_all()
    except Exception as exc:
        logger.debug("browser pool shutdown skipped: %s", exc)


app = FastAPI(title="无限画布", lifespan=lifespan)

from backend.share_routes import router as workspace_package_router

app.include_router(workspace_package_router)


@app.exception_handler(task_manager.ExistingNodeTask)
async def existing_node_task_response(request, exc):
    task = exc.task
    if task.status in (TaskStatus.pending, TaskStatus.generating) and not request.url.path.endswith("/recheck"):
        # A duplicate click reattaches to the existing task; no reserve or submit.
        return JSONResponse(content=task.model_dump(mode="json"))
    detail = "此节点原任务尚未确认结束，请在生成历史中重新检测原任务，避免重复生成"
    err = str(getattr(task, "error", None) or "")
    if "等待本机任务" in err and "超时" in err:
        detail = (
            "原任务本地查询已超时，但上游任务号仍保留。"
            "请先在生成历史中对原任务点「重新检测」；"
            "若确认已失败，再点生成发起新任务。"
        )
    return JSONResponse(status_code=409, content={
        "detail": detail,
        "task_id": task.id,
    })

from backend.text_compression import TextCompressionMiddleware

app.add_middleware(TextCompressionMiddleware, minimum_size=1024, compresslevel=4)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def canvas_auth_middleware(request, call_next):
    scope_token = set_current_user_id(None)
    try:
        user_id = await apply_user_scope(request)
        path = request.url.path
        if path.startswith("/api/") and not is_auth_exempt(path) and not user_id:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        return await call_next(request)
    finally:
        reset_current_user_id(scope_token)


# ── Login (canvas port) ────────────────────────────────────


@app.get("/api/auth/status")
def api_auth_status():
    from backend import app_users

    return {"configured": True, "require_login": False}


@app.post("/api/auth/login")
def api_auth_login(body: dict = Body(...)):
    from backend import app_users

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    row = app_users.verify_password(username, password)
    if not row:
        raise HTTPException(401, "用户名或密码错误")
    if not row.get("enabled", True):
        raise HTTPException(403, "账号已禁用")
    sess = app_users.create_session(row["id"], portal="canvas")
    resp = JSONResponse(content=sess)
    from backend.auth_deps import attach_session_cookie

    attach_session_cookie(resp, sess.get("token"))
    return resp


@app.post("/api/auth/logout")
def api_auth_logout(request: Request):
    from backend import app_users
    from backend.auth_deps import _extract_token, clear_session_cookie

    app_users.revoke_session(_extract_token(request))
    resp = JSONResponse(content={"ok": True})
    clear_session_cookie(resp)
    return resp


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    from backend.auth_deps import _extract_token, attach_session_cookie, require_login_user

    user = require_login_user()
    resp = JSONResponse(content={"user": user})
    attach_session_cookie(resp, _extract_token(request))
    return resp


@app.patch("/api/auth/profile")
def api_auth_profile(request: Request, body: dict = Body(default={})):
    from backend import app_users
    from backend.auth_deps import _extract_token, attach_session_cookie, require_login_user

    user = require_login_user()
    uid = str(user.get("id") or "")
    display_name = body.get("display_name")
    clear_avatar = bool(body.get("clear_avatar"))
    try:
        updated = app_users.update_user(
            uid,
            display_name=None if display_name is None else str(display_name),
            clear_avatar=clear_avatar,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "用户不存在") from exc
    resp = JSONResponse(content={"ok": True, "user": updated})
    attach_session_cookie(resp, _extract_token(request))
    return resp


@app.post("/api/auth/avatar")
async def api_auth_avatar(request: Request):
    from backend import app_users
    from backend.auth_deps import _extract_token, attach_session_cookie, require_login_user

    user = require_login_user()
    uid = str(user.get("id") or "")
    form = await request.form()
    upload = form.get("file") or form.get("avatar")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "请选择头像图片")
    data = await upload.read()
    content_type = getattr(upload, "content_type", "") or ""
    try:
        updated = app_users.save_user_avatar_bytes(uid, data, content_type=str(content_type))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "用户不存在") from exc
    resp = JSONResponse(content={"ok": True, "user": updated})
    attach_session_cookie(resp, _extract_token(request))
    return resp


@app.get("/api/auth/avatar/{user_id}")
def api_auth_avatar_file(user_id: str):
    uid = (user_id or "").strip()
    path = DATA_DIR / "users" / uid / "avatar.jpg"
    if not path.is_file():
        raise HTTPException(404, "头像不存在")
    resp = FileResponse(path, media_type="image/jpeg", filename="avatar.jpg")
    resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return resp


@app.get("/api/points/history")
def api_points_history(
    category: str = "all",
    limit: int = 50,
    offset: int = 0,
):
    from backend import points
    from backend import service_settings
    from backend.auth_deps import require_login_user

    if not service_settings.get_points_enabled():
        raise HTTPException(404, "积分功能未启用")
    user = require_login_user()
    return points.list_history(
        str(user.get("id") or ""),
        category=category,
        limit=limit,
        offset=offset,
    )


@app.get("/api/pay/config")
def api_pay_config():
    from backend import payment

    return payment.public_payment_info()


@app.post("/api/pay/create")
def api_pay_create(request: Request, body: dict = Body(...)):
    from backend import payment
    from backend.auth_deps import require_login_user

    user = require_login_user()
    pay_type = body.get("type") or body.get("pay_type")
    amount = body.get("amount") or body.get("price")
    try:
        return payment.create_order(
            user_id=str(user.get("id") or ""),
            amount=amount,
            pay_type=int(pay_type),
            request=request,
            is_test=False,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/pay/status")
def api_pay_status(pay_id: str = Query(...)):
    from backend import payment
    from backend.auth_deps import require_login_user

    user = require_login_user()
    try:
        data = payment.poll_order_status(pay_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    order = payment.get_local_order(pay_id)
    if order and str(order.get("user_id") or "") != str(user.get("id") or ""):
        raise HTTPException(403, "无权查看该订单")
    return data


@app.post("/api/pay/close")
def api_pay_close(body: dict = Body(...)):
    from backend import payment
    from backend.auth_deps import require_login_user

    user = require_login_user()
    pay_id = str(body.get("pay_id") or "").strip()
    order = payment.get_local_order(pay_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if str(order.get("user_id") or "") != str(user.get("id") or ""):
        raise HTTPException(403, "无权操作该订单")
    try:
        return payment.close_order(pay_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.api_route("/api/pay/notify", methods=["GET", "POST"])
async def api_pay_notify(request: Request):
    from backend import payment
    from fastapi.responses import PlainTextResponse

    params: dict = {}
    params.update({k: str(v) for k, v in request.query_params.multi_items()})
    if request.method == "POST":
        try:
            form = await request.form()
            for k, v in form.multi_items():
                params[str(k)] = str(v)
        except Exception:
            pass
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                params.update({str(k): str(v) for k, v in raw.items()})
        except Exception:
            pass
    result = payment.apply_notify(params)
    return PlainTextResponse(result)


@app.api_route("/api/pay/return", methods=["GET", "POST"])
async def api_pay_return(request: Request):
    """Sync return URL after payment — credit if needed, then show a simple success page."""
    from backend import payment
    from fastapi.responses import HTMLResponse

    params: dict = {k: str(v) for k, v in request.query_params.multi_items()}
    if request.method == "POST":
        try:
            form = await request.form()
            for k, v in form.multi_items():
                params[str(k)] = str(v)
        except Exception:
            pass
    result = "ok"
    if params.get("sign"):
        result = payment.apply_notify(params)
    paid = result == "success"
    title = "充值成功" if paid else "支付处理中"
    tip = "积分已到账，请返回画布查看。" if paid else "若已付款请稍候，积分将自动到账。"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:system-ui,sans-serif;background:#111318;color:#ececf2}}
.card{{padding:28px 32px;border-radius:16px;background:#1c1e26;border:1px solid rgba(255,255,255,.1);
text-align:center;max-width:360px}}
h1{{margin:0 0 8px;font-size:20px}} p{{margin:0;color:#a8abb8;font-size:14px;line-height:1.5}}
</style></head><body><div class="card"><h1>{title}</h1><p>{tip}</p></div>
<script>try{{if(window.opener)setTimeout(function(){{window.close()}},1200)}}catch(e){{}}</script>
</body></html>"""
    return HTMLResponse(html)


# ── Service settings ───────────────────────────────────────


@app.get("/api/runtime")
def api_runtime():
    """Where this instance stores canvas/accounts (for portable green builds)."""
    import os
    import sys

    from backend import canvas_store

    return {
        "app_root": str(BASE_DIR),
        "data_dir": str(DATA_DIR),
        "canvas_file": str(canvas_store.CANVAS_FILE),
        "uploads_dir": str(UPLOADS_DIR),
        "cwd": os.getcwd(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


@app.get("/api/ping")
def api_ping():
    """Lightweight liveness for ServerWatch — avoid competing with /api/app-config under media load."""
    return {"ok": True}


@app.get("/api/app-config")
def api_app_config(request: Request):
    """Deploy profile; browser channels filtered by logged-in user role."""
    from backend import app_users
    from backend.auth_deps import user_id_from_request

    user = None
    uid = user_id_from_request(request)
    if uid:
        user = app_users.get_user(uid)
    return public_config(user=user)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from backend.branding import favicon_response

    return favicon_response()


@app.get("/api/upscale/my-link")
def api_upscale_my_link():
    """Per-user upscale worker link for the logged-in canvas account."""
    from backend.auth_deps import require_login_user
    from backend import service_settings
    from backend.upscale_connect import build_link_for_user, pool_mode

    user = require_login_user()
    if not service_settings.get_upscale_enabled():
        return {"pool_mode": pool_mode(), "link": "", "upscale_enabled": False}
    mode = pool_mode()
    if mode != "per_user":
        return {"pool_mode": mode, "link": "", "upscale_enabled": True}
    link = build_link_for_user(
        str(user.get("id") or ""),
        service_settings.get_worker_public_base_url(),
    )
    return {"pool_mode": mode, "link": link, "upscale_enabled": True}


def _reject_doubao_http() -> None:
    from backend.channel_gate import assert_user_browser_channels

    assert_user_browser_channels()


@app.get("/api/uploads/orphan-stats")
def api_orphan_upload_stats():
    """Scan upload folders for files not referenced by any canvas node."""
    from backend.board_assets import scan_orphan_upload_stats

    return scan_orphan_upload_stats()


@app.post("/api/uploads/purge-orphans")
def api_purge_orphan_uploads():
    """Delete upload files not referenced by current canvas node media URLs."""
    from backend.media.uploads_cleanup import purge_orphan_uploads

    return purge_orphan_uploads()


@app.post("/api/uploads/delete-refs")
def api_delete_upload_refs(body: dict = Body(default_factory=dict)):
    """Delete specific upload refs after canvas node removal (skips still-referenced files)."""
    from backend.auth_deps import require_login_user
    from backend import board_assets

    user = require_login_user()
    refs = body.get("refs") if isinstance(body, dict) else None
    if not isinstance(refs, list):
        raise HTTPException(400, "refs 必须是数组")
    cleaned = [str(r).strip() for r in refs if str(r or "").strip()]
    if len(cleaned) > 500:
        raise HTTPException(400, "一次最多删除 500 个文件引用")
    return board_assets.delete_upload_refs(cleaned, user_id=user["id"])


@app.get("/api/settings")
def api_get_settings():
    from backend import service_settings

    return service_settings.public_canvas_settings()


@app.get("/api/http-policy")
def api_get_http_policy():
    """Current upstream timeout / retry / poll interval."""
    from backend.auth_deps import require_login_user
    from backend import http_policy, http_policy_store

    require_login_user()
    return http_policy_store.load_into_runtime() or http_policy.get_policy()


@app.put("/api/http-policy")
def api_put_http_policy(body: dict = Body(default_factory=dict)):
    """Update upstream timeout / retry / poll interval and persist for this server."""
    from backend.auth_deps import require_login_user
    from backend import http_policy, http_policy_store

    require_login_user()
    raw = body if isinstance(body, dict) else {}
    http_policy.set_policy(
        hard_timeout_hours=raw.get("hard_timeout_hours"),
        network_retries=raw.get("network_retries"),
        poll_interval_sec=raw.get("poll_interval_sec"),
    )
    return http_policy_store.save_from_runtime()


# ── MyApi custom channel ───────────────────────────────────


def _assert_custom_myapi_writable():
    from backend import service_settings

    if not service_settings.get_allow_frontend_custom_models():
        raise HTTPException(403, "管理员已关闭前台自定义模型，请使用公共模型")


def _bendi_removed() -> None:
    raise HTTPException(410, "BenDi 已移除，请改用 MyApi")


@app.get("/api/bendi/health")
@app.get("/api/bendi/models")
@app.get("/api/bendi/settings")
@app.put("/api/bendi/settings")
def api_bendi_removed():
    _bendi_removed()


@app.get("/api/volcengine/settings")
def api_volcengine_get_settings():
    from backend.auth_deps import require_login_user
    from backend import service_settings
    from backend.volcengine import settings as ve_settings

    require_login_user()
    view = ve_settings.public_view()
    view["platform_enabled"] = bool(service_settings.get_volcengine_channel_enabled())
    return view


@app.put("/api/volcengine/settings")
def api_volcengine_put_settings(payload: dict = Body(...)):
    from backend.auth_deps import require_login_user
    from backend import service_settings
    from backend.volcengine import settings as ve_settings

    require_login_user()
    if not service_settings.get_volcengine_channel_enabled():
        raise HTTPException(403, "管理员已关闭火山引擎渠道")
    body = payload if isinstance(payload, dict) else {}
    view = ve_settings.save(body)
    view["platform_enabled"] = True
    return view


@app.get("/api/myapi/models")
def api_myapi_list(kind: str | None = None):
    from backend import myapi_store

    k = (kind or "").strip().lower() or None
    if k and k not in myapi_store.KINDS:
        raise HTTPException(400, "kind 须为 image|video|text|agent|vision")
    return {"models": myapi_store.public_list(k)}


@app.post("/api/myapi/models")
def api_myapi_upsert(payload: dict = Body(...)):
    _assert_custom_myapi_writable()
    from backend import myapi_store

    kind = str(payload.get("kind") or "").strip().lower()
    try:
        saved = myapi_store.upsert(kind, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    pub = next((m for m in myapi_store.public_list(kind) if m["id"] == saved["id"]), None)
    return {"ok": True, "model": pub or {"id": saved["id"], "name": saved["name"], "kind": kind}}


@app.delete("/api/myapi/models/{model_id}")
def api_myapi_delete(model_id: str):
    _assert_custom_myapi_writable()
    from backend import myapi_store

    if not myapi_store.delete(model_id):
        raise HTTPException(404, "模型不存在")
    return {"ok": True}


@app.post("/api/myapi/models/{model_id}/default")
def api_myapi_set_default(model_id: str, payload: dict | None = Body(None)):
    """Set or clear the per-kind default MyApi model used for new nodes."""
    _assert_custom_myapi_writable()
    from backend import myapi_store

    body = payload if isinstance(payload, dict) else {}
    enabled = body.get("is_default", body.get("default", True))
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off", "")
    else:
        enabled = bool(enabled)
    saved = myapi_store.set_default(model_id, enabled)
    if not saved:
        raise HTTPException(404, "模型不存在")
    pub = next((m for m in myapi_store.public_list(saved.get("kind")) if m["id"] == saved["id"]), None)
    return {"ok": True, "model": pub or {"id": saved["id"], "kind": saved.get("kind"), "is_default": enabled}}


@app.get("/api/myapi-templates")
def api_myapi_templates(kind: str | None = None):
    from backend import myapi_store, myapi_templates_store

    k = (kind or "").strip().lower() or None
    if k and k not in myapi_store.KINDS:
        raise HTTPException(400, "kind 须为 image|video|text|agent|vision")
    return {"templates": myapi_templates_store.public_list(k)}


@app.post("/api/agent/chat")
async def api_agent_chat(request: Request, payload: dict = Body(...)):
    """小庆子智能体：提交到任务服务（8089），前端轮询结果。"""
    import uuid

    from backend import myapi_store, public_models_store
    from backend.job_worker import client as worker_client
    from backend.user_scope import get_current_user_id

    model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
    message = str(payload.get("message") or payload.get("prompt") or "").strip()
    if not message:
        raise HTTPException(400, "message 不能为空")

    def _as_agent_cfg(raw: dict | None) -> dict | None:
        if not raw or raw.get("kind") != "agent":
            return None
        if raw.get("enabled") is False:
            return None
        return raw

    cfg = None
    if model_id:
        cfg = _as_agent_cfg(myapi_store.get_model(model_id))
        if not cfg:
            cfg = _as_agent_cfg(public_models_store.get_model(model_id, require_enabled=True))
        if not cfg:
            try:
                from backend.volcengine import settings as ve_settings

                cfg = ve_settings.build_agent_cfg(model_id)
            except Exception:
                cfg = None
        if not cfg:
            raise HTTPException(400, "所选智能体模型已删除或停用，请刷新模型列表后重新选择")
    if not cfg:
        agents = [
            m
            for m in myapi_store.list_models("agent")
            if m.get("enabled", True)
        ]
        pub_agents = public_models_store.list_models("agent", enabled_only=True)
        cfg = next((m for m in agents if m.get("is_default")), None) or (agents[0] if agents else None)
        if not cfg:
            cfg = pub_agents[0] if pub_agents else None
    if not cfg:
        try:
            from backend.volcengine import settings as ve_settings

            volc_agents = ve_settings.list_agent_picker()
            if volc_agents:
                cfg = ve_settings.build_agent_cfg(str(volc_agents[0].get("id") or ""))
        except Exception:
            cfg = None
    if not cfg:
        raise HTTPException(400, "请先在 MyApi → 智能体 添加模型，或开启火山引擎文本并勾选「用于智能体」")

    ok, detail = await worker_client.is_available_retry()
    if not ok:
        raise HTTPException(503, detail or "任务服务（8089）未启动，请先运行「启动任务服务.bat」")

    user_id = str(get_current_user_id() or "").strip()
    if not user_id:
        raise HTTPException(401, "请先登录")

    board_id = str(payload.get("board_id") or "").strip() or None
    job_id = f"agent-{uuid.uuid4().hex[:16]}"
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    memory = str(payload.get("memory") or "").strip()
    canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else None
    light_raw = payload.get("light")
    light_flag = None if light_raw is None else bool(light_raw)

    try:
        await worker_client.submit_job(
            task_id=job_id,
            user_id=user_id,
            board_id=board_id,
            kind="agent",
            model=str(cfg.get("id") or model_id or ""),
            channel="agent",
            needs_callback=False,
            payload={
                "model_id": str(cfg.get("id") or model_id or ""),
                "message": message,
                "history": history,
                "memory": memory,
                "canvas": canvas,
                "user_intent": str(payload.get("user_intent") or message),
                "role_mode": str(payload.get("role_mode") or "agent"),
                "exec_mode": str(payload.get("exec_mode") or "confirm"),
                "light": light_flag,
            },
        )
    except worker_client.WorkerJobRejected as exc:
        raise HTTPException(exc.status_code if 400 <= int(exc.status_code) < 600 else 502, str(exc.detail)) from exc
    except Exception as exc:
        logger.exception("agent job submit failed: %s", exc)
        raise HTTPException(502, str(exc) or "提交智能体任务失败") from exc

    return {"ok": True, "async": True, "job_id": job_id, "model_id": cfg.get("id")}


@app.get("/api/agent/chat/{job_id}")
async def api_agent_chat_status(job_id: str):
    """轮询 8089 上的智能体任务状态。"""
    from backend.job_worker import client as worker_client
    from backend.user_scope import get_current_user_id

    jid = str(job_id or "").strip()
    if not jid.startswith("agent-"):
        raise HTTPException(400, "无效的智能体任务 id")
    user_id = str(get_current_user_id() or "").strip()
    if not user_id:
        raise HTTPException(401, "请先登录")
    try:
        job = await worker_client.get_job(jid)
    except worker_client.WorkerQueryError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not job:
        raise HTTPException(404, "智能体任务不存在或已过期")
    if str(job.get("user_id") or "") != user_id:
        raise HTTPException(403, "无权查看该智能体任务")
    status = str(job.get("status") or "pending")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    out = {
        "ok": True,
        "job_id": jid,
        "status": status,
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "completed_at": job.get("completed_at"),
    }
    if status == "completed":
        out.update(
            {
                "thinking": result.get("thinking") or "",
                "reply": result.get("reply") or "",
                "actions": result.get("actions") or [],
                "choices": result.get("choices") or [],
                "decision": result.get("decision") or "",
                "diagnostics": result.get("diagnostics") or {},
                "model_id": result.get("model_id") or job.get("model") or "",
                "multimodal": bool(result.get("multimodal")),
                "vision_model_id": result.get("vision_model_id") or "",
                "vision_model": result.get("vision_model") or "",
                "public": bool(result.get("public")),
            }
        )
    return out


@app.post("/api/agent/chat/{job_id}/cancel")
async def api_agent_chat_cancel(job_id: str):
    """取消进行中的智能体任务。"""
    from backend.job_worker import client as worker_client
    from backend.user_scope import get_current_user_id

    jid = str(job_id or "").strip()
    if not jid.startswith("agent-"):
        raise HTTPException(400, "无效的智能体任务 id")
    user_id = str(get_current_user_id() or "").strip()
    if not user_id:
        raise HTTPException(401, "请先登录")
    try:
        job = await worker_client.get_job(jid)
    except worker_client.WorkerQueryError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not job:
        return {"ok": True, "already_done": True}
    if str(job.get("user_id") or "") != user_id:
        raise HTTPException(403, "无权取消该智能体任务")
    await worker_client.cancel_job(jid)
    return {"ok": True}


@app.post("/api/myapi/test")
async def api_myapi_test(payload: dict = Body(...)):
    from backend import myapi_store
    from backend.myapi import client as myapi_client

    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    kind = str(payload.get("kind") or "text").strip().lower() or "text"
    edit_id = str(payload.get("id") or "").strip()
    clone_from = str(payload.get("clone_from") or "").strip()
    api_style = str(payload.get("api_style") or "").strip().lower() or None
    duration_min = payload.get("duration_min")
    duration_max = payload.get("duration_max")
    if not api_key or not api_style or duration_min is None or duration_max is None:
        for mid in (edit_id, clone_from):
            if not mid:
                continue
            saved = myapi_store.get_model(mid)
            if not saved:
                continue
            if not api_key:
                api_key = str(saved.get("api_key") or "")
            if not base_url:
                base_url = str(saved.get("base_url") or "")
            if not model:
                model = str(saved.get("model") or saved.get("name") or "")
            if not api_style:
                api_style = str(saved.get("api_style") or "").strip().lower() or None
            if duration_min is None and saved.get("duration_min") is not None:
                duration_min = saved.get("duration_min")
            if duration_max is None and saved.get("duration_max") is not None:
                duration_max = saved.get("duration_max")
            if api_key and api_style and duration_min is not None and duration_max is not None:
                break

    def _opt_int(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return await myapi_client.test_connection(
        base_url=base_url,
        api_key=api_key,
        model=model or None,
        kind=kind,
        api_style=api_style,
        duration_min=_opt_int(duration_min),
        duration_max=_opt_int(duration_max),
    )


@app.get("/api/settings/browser")
def api_get_browser_settings():
    """Detect bundled / system / custom Chromium paths."""
    from backend import service_settings

    return service_settings.settings_with_browser().get("browser") or {}


@app.post("/api/settings/browser/browse")
def api_browse_browser_exe():
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.patch("/api/settings")
async def api_patch_settings(payload: dict = Body(...)):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


# ── Accounts ──────────────────────────────────────────────


@app.get("/api/accounts")
def api_list_accounts():
    from backend.auth_deps import require_login_user
    from backend.account_ownership import enrich_account_payload, filter_accounts_for_user
    from backend.user_roles import can_use_browser_channels, is_admin_role

    user = require_login_user()
    if not can_use_browser_channels(user.get("role")):
        return {"accounts": []}
    # Skip reconcile for retired GPT accounts on every list (admin still reconciles).
    accounts = account_manager.list_accounts()
    if not is_admin_role(user.get("role")):
        accounts = filter_accounts_for_user(accounts, user.get("id"))
    return {"accounts": [enrich_account_payload(a) for a in accounts]}


@app.get("/api/accounts/pool-summary")
def api_accounts_pool_summary():
    """Avatar stats + pool share list for admin/internal users."""
    from backend.auth_deps import require_login_user
    from backend.account_ownership import summarize_accounts_for_user
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    summary = summarize_accounts_for_user(account_manager.list_accounts(), str(user.get("id") or ""))
    return {"ok": True, **summary}


@app.get("/api/accounts/pool-peers")
def api_accounts_pool_peers():
    from backend.auth_deps import require_login_user
    from backend.account_ownership import list_pool_peers
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    return {"users": list_pool_peers(exclude_user_id=str(user.get("id") or ""))}


@app.post("/api/accounts/pool-share")
def api_add_pool_share(body: dict = Body(...)):
    from backend.auth_deps import require_login_user
    from backend import app_users
    from backend.account_ownership import add_pool_share, summarize_accounts_for_user
    from backend.channel_gate import assert_user_browser_channels
    from backend.user_roles import can_use_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    uid = str(user.get("id") or "")
    target_id = str(body.get("user_id") or "").strip()
    username = str(body.get("username") or "").strip()
    if not target_id and username:
        row = app_users.get_user_by_username(username)
        if not row:
            # Also allow display_name match for convenience
            key = username.strip().lower()
            for u in app_users.list_users():
                dn = str(u.get("display_name") or "").strip().lower()
                un = str(u.get("username") or "").strip().lower()
                if key and key in (dn, un):
                    row = u
                    break
        if not row:
            raise HTTPException(
                404,
                f"找不到画布用户「{username}」。请填写对方的登录用户名（不是 Seed 号/手机号）",
            )
        if not can_use_browser_channels(row.get("role")):
            raise HTTPException(400, "只能共享给内部人员或管理员账号")
        target_id = str(row.get("id") or "")
    if not target_id:
        raise HTTPException(400, "请填写要共享的内部账号用户名")
    try:
        share = add_pool_share(uid, target_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    summary = summarize_accounts_for_user(account_manager.list_accounts(), uid)
    return {"ok": True, "share": share, **summary}


@app.delete("/api/accounts/pool-share/{target_user_id}")
def api_remove_pool_share(target_user_id: str):
    from backend.auth_deps import require_login_user
    from backend.account_ownership import remove_pool_share, summarize_accounts_for_user
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    uid = str(user.get("id") or "")
    ok = remove_pool_share(uid, target_user_id)
    if not ok:
        raise HTTPException(404, "未找到该共享")
    summary = summarize_accounts_for_user(account_manager.list_accounts(), uid)
    return {"ok": True, **summary}


_tier_sync_at: dict[str, float] = {}
_TIER_SYNC_TTL_SEC = 90.0


@app.post("/api/accounts/sync-doubao-tiers")
async def api_sync_doubao_tiers(force: bool = False):
    """
    Refresh Doubao package tiers for online accounts (HTTP cookie probe).
    Called before DouBao/Dola / S generate so channel can auto up/downgrade.
    """
    from backend.auth_deps import require_login_user
    from backend.channel_gate import assert_user_browser_channels

    assert_user_browser_channels(require_login_user())
    import time

    from browser_channels.doubao.profile import apply_profile_tier_to_account

    now = time.time()
    refreshed: list[dict] = []
    skipped = 0
    errors = 0
    for acc in account_manager.list_accounts():
        # Dola 无套餐分流，跳过
        if account_manager.normalize_provider(acc.provider) != "doubao":
            continue
        if str(getattr(acc, "status", "") or "") != "online":
            continue
        last = _tier_sync_at.get(acc.id) or 0.0
        if not force and (now - last) < _TIER_SYNC_TTL_SEC:
            skipped += 1
            continue
        try:
            row = await apply_profile_tier_to_account(acc.id, allow_browser=False)
            _tier_sync_at[acc.id] = now
            refreshed.append(
                {
                    "account_id": acc.id,
                    "name": acc.name,
                    "tier": row.get("tier"),
                    "plan_label": row.get("plan_label"),
                    "provider": account_manager.normalize_provider(acc.provider),
                }
            )
        except Exception as exc:
            errors += 1
            logger.debug("sync bytedance tier %s failed: %s", acc.id[:8], exc)

    upgrade_online = sum(
        1
        for a in account_manager.list_accounts()
        if account_manager.is_bytedance_provider(a.provider)
        and str(getattr(a, "status", "") or "") == "online"
        and account_manager.account_doubao_tier(a) == "upgrade"
    )
    return {
        "ok": True,
        "refreshed": refreshed,
        "skipped": skipped,
        "errors": errors,
        "upgrade_online": upgrade_online,
        "can_use_s": upgrade_online > 0,
        "accounts": [a.model_dump() for a in account_manager.list_accounts()],
    }


@app.get("/api/accounts/export")
def api_export_accounts(ids: str | None = None):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.post("/api/accounts/import")
async def api_import_accounts(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.post("/api/accounts")
def api_create_account(body: AccountCreate):
    """Admin / internal may add Doubao accounts from the canvas avatar (QR only)."""
    from backend.auth_deps import require_login_user
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    provider = account_manager.normalize_provider(body.provider)
    if provider != "doubao":
        raise HTTPException(403, "前台仅支持扫码添加 Seed 账号")
    try:
        account = account_manager.create_account(body.name, provider="doubao")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return account.model_dump()


@app.get("/api/accounts/suggest-name")
def api_suggest_account_name(provider: str = "doubao"):
    return {"name": account_manager.suggest_account_name(provider=provider)}


@app.patch("/api/accounts/{account_id}")
def api_update_account(account_id: str, body: AccountUpdate):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: str):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.post("/api/accounts/{account_id}/check")
async def api_check_account(account_id: str):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.post("/api/accounts/{account_id}/gpt-login")
async def api_start_gpt_login(account_id: str):
    raise HTTPException(410, "OpenAI 渠道已下线，请改用 BenDi")


@app.get("/api/accounts/{account_id}/gpt-login")
async def api_gpt_login_status(account_id: str):
    raise HTTPException(410, "OpenAI 渠道已下线，请改用 BenDi")


@app.post("/api/accounts/{account_id}/qr-login")
async def api_start_qr_login(account_id: str):
    """Admin / internal: start Doubao QR login from canvas."""
    from backend.auth_deps import require_login_user
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    _reject_doubao_http()
    account = account_manager.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    if account_manager.normalize_provider(account.provider) != "doubao":
        raise HTTPException(400, "仅 Seed 账号支持扫码登录")
    try:
        session = await get_pool().start_qr_login(account_id)
        return QRLoginResponse(
            status=session.status,
            qr_image_base64=session.qr_base64,
            message="请扫码登录",
        )
    except Exception as exc:
        logger.exception("QR login start failed")
        raise HTTPException(502, f"获取二维码失败: {exc}") from exc


@app.post("/api/accounts/{account_id}/qr-login/cancel")
async def api_cancel_qr_login(account_id: str, body: dict = Body(default_factory=dict)):
    """Stop QR/manual login poll; delete account if never logged in."""
    from backend.auth_deps import require_login_user
    from backend.channel_gate import assert_user_browser_channels

    user = require_login_user()
    assert_user_browser_channels(user)
    _reject_doubao_http()
    delete_incomplete = body.get("delete_incomplete", True)
    if isinstance(delete_incomplete, str):
        delete_incomplete = delete_incomplete.strip().lower() not in {"0", "false", "no"}
    return await get_pool().cancel_login_flow(
        account_id,
        delete_incomplete=bool(delete_incomplete),
    )


@app.post("/api/accounts/{account_id}/browser-login")
async def api_start_doubao_browser_login(account_id: str):
    raise HTTPException(403, _CONFIG_ADMIN_MSG)


@app.get("/api/accounts/{account_id}/browser-login")
async def api_doubao_browser_login_status(account_id: str):
    _reject_doubao_http()
    from browser_channels.doubao import login as doubao_login

    job = doubao_login.get_login_job(account_id)
    account = account_manager.get_account(account_id)
    if not job:
        ok = account_manager.has_valid_session(account_id) if account else False
        return {
            "status": "success" if ok else "idle",
            "message": "已登录" if ok else "尚未开始登录",
            "account": account.model_dump() if account else None,
        }
    return {**job, "account": account.model_dump() if account else None}


@app.get("/api/accounts/{account_id}/qr-login")
async def api_qr_login_status(account_id: str):
    _reject_doubao_http()
    session = get_pool().get_qr_session(account_id)
    if not session:
        raise HTTPException(404, "未找到扫码会话，请先发起登录")

    if session.status == "success":
        result = await get_pool().on_qr_success(account_id)
        if result.get("pending"):
            return {
                "status": "finalizing",
                "message": result.get("message") or "正在完善账号信息，请稍候…",
                "account": result.get("account"),
            }
        if result.get("duplicate"):
            return {
                "status": "failed",
                "message": result.get("message") or "重复登录",
                "duplicate": True,
                "account": result.get("account"),
            }
        return {
            "status": "success",
            "message": result.get("message") or "登录成功",
            "account": result.get("account"),
        }

    step_msg = session.error or (
        "① 等待扫码…"
        if session.status == "pending"
        else "② 已扫码，请在手机上确认登录"
        if session.status == "scanned"
        else "③ 手机已确认，正在获取登录凭证…"
        if session.status == "confirmed"
        else "登录成功"
        if session.status == "success"
        else None
    )
    return QRLoginResponse(
        status=session.status,
        qr_image_base64=session.qr_base64 or None,
        message=step_msg,
    )


# ── Video ─────────────────────────────────────────────────


def _sanitize_myapi_task_error(err: str, *, kind: str = "image") -> str:
    """Plain user-facing copy for MyApi / relay failures (no channel brand prefix)."""
    text = (err or "").strip()
    for prefix in (
        "MyApi 创建视频任务失败：",
        "MyApi 生图失败：",
        "MyApi 文本失败：",
        "MyApi 视频未返回可下载地址。",
        "MyApi 图片失败：",
        "MyApi 视频失败：",
        "MyApi 文本失败：",
        "MyApi ",
        "RuntimeError: ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    # Drop leftover "图片失败：" style wrappers after stripping MyApi
    for prefix in ("图片失败：", "视频失败：", "文本失败：", "任务失败："):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    kind_label = {"image": "图片", "video": "视频", "text": "文本"}.get(kind, "任务")
    if not text:
        return f"{kind_label}生成失败，请稍后重试"
    if "ReadTimeout" in text or "创建视频任务时等待上游响应超时" in text:
        return (
            "创建任务等待超时，可能已在平台侧生成。"
            "请稍后查看结果，勿连点多开。"
        )
    if "暂时繁忙" in text or ("稍后重试" in text and ("拥堵" in text or "限流" in text or "繁忙" in text)):
        return "服务暂时繁忙，请 1～2 分钟后再试，避免连点或多开任务。"
    if "额度不足" in text or "余额不足" in text:
        return text if len(text) <= 160 else text[:160] + "…"
    if "积分" in text and ("不足" in text or "不够" in text):
        return text if len(text) <= 160 else text[:160] + "…"
    if len(text) > 280:
        return text[:280] + "…"
    return text


def _sanitize_task_error(err: str, *, kind: str = "image", source: str = "browser") -> str:
    text = (err or "").strip()
    if source == "myapi":
        return _sanitize_myapi_task_error(text, kind=kind)
    if "Page.evaluate: Error: " in text:
        text = text.split("Page.evaluate: Error: ", 1)[-1].split("\n    at ")[0]
    if "Request URL is missing an 'http://' or 'https://' protocol" in text:
        return "参考图地址无效或文件已丢失，请重新添加引用图片后重试"
    if account_manager.is_video_quota_exhausted_error(text):
        return "今日额度已满，请次日尝试"
    if account_manager.is_quota_exhausted_error(text):
        return "账号额度已用完。已自动冷却该账号；若有其他账号会自动换号重试。"
    if account_manager.is_rate_limit_error(text):
        return "Seed 触发限流/安全验证，请等待 8～10 分钟后再试（期间勿连点、勿多开任务）"
    if "710020702" in text:
        return (
            f"{'图片' if kind == 'image' else '视频'}生成失败（Seed 系统错误）。"
            "请尝试：① 简化提示词 ② 换小一点的参考图 ③ 稍后再试"
        )
    if "ReadTimeout" in text or "等待上游响应超时" in text or "创建视频任务时等待上游响应超时" in text:
        return (
            f"{'图片' if kind == 'image' else '视频'}请求等待超时，请稍后再试；"
            "勿连点或多开任务。"
        )
    # Doubao UI often ends with「请稍后重试」— do NOT relabel as llmtoken/MyApi.
    if "无法下载为可播放文件" in text or "CDN 403" in text:
        return text[:240]
    if "服务过载" in text or "暂时繁忙" in text:
        return "Seed 视频服务暂时繁忙或过载，请 1～2 分钟后再试，避免连点或多开任务。"
    if "额度紧张" in text or "开通豆包专业版" in text:
        return "Seed 额度紧张（需专业版或次日额度），请换账号或稍后再试。"
    if text.startswith("视频任务失败："):
        return text
    # Unwrap RuntimeError / poll wrappers before classifying.
    text = re.split(r"\n\s*↳\s*caused by:\s*", text, maxsplit=1)[0].strip()
    text = re.sub(r"^(?:RuntimeError|GenerationPollError):\s*", "", text, flags=re.I).strip()
    # Soft Doubao banner often arrives glued to the force-prompt body.
    if "AI 生成可能有误" in text or "AI生成可能有误" in text or (
        "注意核实" in text and "生成" in text
    ):
        return "Seed 提示生成可能有误，请换提示词或参考图后重试"
    if any(
        m in text
        for m in (
            "版权相关限制",
            "版权限制",
            "疑似包含侵权",
            "侵权内容",
            "内容违规",
            "审核未通过",
            "无法生成该内容",
            "暂时无法创作",
            "无法创作对应的内容",
        )
    ):
        return "Seed 因版权/安全策略拒绝生成，请换提示词或参考图后重试"
    label = "图片" if kind == "image" else "视频" if kind == "video" else "内容"
    generic = f"{label}生成失败，请稍后重试"
    if text.startswith("{") and ("error_detail" in text or "error_code" in text or len(text) > 80):
        return generic
    if re.match(r"^generate_\w+ error:", text[:48], re.I):
        return generic
    # Keep zacao / share diagnostics (API原文 + 分享链接) readable
    if "媒体解析" in text or "分享链接=" in text or "去水印" in text:
        if len(text) > 480:
            return text[:480] + "…"
        return text
    if len(text) > 240:
        return text[:240] + "…"
    return text


def _prefer_sticky_doubao_account(
    preferred_id: str | None,
    *,
    kind: str,
    provider: str,
) -> str | None:
    """Text/image: prefer an account whose Doubao browser is still open."""
    if kind == "video":
        return preferred_id
    if account_manager.normalize_provider(provider) not in ("doubao", "dola"):
        return preferred_id
    if not browser_channels_enabled():
        return preferred_id
    try:
        warm = get_pool().idle_doubao_context_account()
        if warm:
            return warm
    except Exception:
        pass
    return preferred_id


async def _on_doubao_video_quota_exhausted(account_id: str | None = None) -> None:
    if not browser_channels_enabled():
        return
    try:
        pool = get_pool()
        if account_id:
            await pool.close_account_if_next_day_cooldown(account_id)
        await pool.maybe_close_all_doubao_on_video_quota_exhausted()
    except Exception as exc:
        logger.debug("close browsers after video quota: %s", exc)


def _paused_task_message(*, myapi: bool = False) -> str:
    if myapi:
        return "已手动暂停"
    return "已手动暂停（未发送则可立即再生成；已发送可稍后在渠道网页查看进度）"


def _record_generation_start(
    task,
    *,
    user_id: str,
    username: str | None = None,
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
    kind: str,
    channel: str | None = None,
    model: str | None = None,
    prompt: str = "",
) -> None:
    from backend import gen_records

    # Persist binding on the task itself so /api/tasks can reattach UI after refresh.
    bind: dict = {}
    if board_id:
        bind["board_id"] = board_id
    if workbench_id:
        bind["workbench_id"] = workbench_id
    if node_id:
        bind["node_id"] = node_id
    if bind:
        try:
            task_manager.update_task(task.id, **bind)
            for key, val in bind.items():
                setattr(task, key, val)
        except Exception as exc:
            logger.debug("task bind patch failed task=%s: %s", task.id, exc)

    try:
        gen_records.upsert_record(
            task.id,
            user_id=user_id,
            username=username or "",
            board_id=board_id or "",
            node_id=node_id or "",
            account_id=task.account_id,
            account_name=task.account_name,
            kind=kind,
            channel=channel or "",
            model=model or "",
            prompt=prompt or task.prompt or "",
            status=TaskStatus.pending.value,
            created_at=task.created_at,
        )
    except Exception as exc:
        logger.debug("gen record start failed task=%s: %s", task.id, exc)


def _record_generation_patch(task_id: str, **fields) -> None:
    from backend import gen_records

    try:
        with task_manager.transaction():
            task = task_manager.get_task(task_id)
            target = getattr(fields.get("status"), "value", fields.get("status"))
            if task and target and target != task.status.value:
                return
            gen_records.upsert_record(task_id, **fields)
    except Exception as exc:
        logger.debug("gen record patch failed task=%s: %s", task_id, exc)


def _mark_task_paused(task_id: str, *, myapi: bool = False) -> None:
    msg = _paused_task_message(myapi=myapi)
    now = __import__("datetime").datetime.now().isoformat()
    task_manager.update_task(
        task_id,
        status=TaskStatus.failed,
        error=msg,
        completed_at=now,
    )
    _record_generation_patch(
        task_id,
        status=TaskStatus.failed.value,
        error=msg,
        raw_error=msg,
        completed_at=now,
    )


def _guard_body_media_refs(body) -> None:
    from backend.auth_deps import require_login_user
    from backend.media_access import assert_media_refs

    user = require_login_user()
    refs = [r.model_dump() for r in (getattr(body, "references", None) or [])]
    imgs = getattr(body, "reference_images", None) or []
    assert_media_refs(user["id"], refs, imgs)


def _guard_media_url(
    url: str,
    *,
    workbench_id: str | None = None,
    board_id: str | None = None,
) -> None:
    from backend.auth_deps import require_login_user
    from backend.media_access import assert_media_access

    user = require_login_user()
    assert_media_access(
        user["id"],
        url,
        workbench_id=workbench_id,
        board_id=board_id,
    )


def _body_use_upgrade(body) -> bool:
    """DouBao S channel must lease upgrade-tier accounts (text/image had been missing this)."""
    if bool(getattr(body, "use_upgrade", False)):
        return True
    return str(getattr(body, "channel", None) or "").strip() == "doubao_s"


async def _dispatch_media_to_worker(
    *,
    user_id: str,
    board_id: str | None,
    kind: str,
    payload: dict,
    timeout: float = 900.0,
    workbench_id: str | None = None,
) -> dict:
    """Prefer local worker (8089); fall back to in-process ffmpeg when worker is down."""
    from backend.job_worker import client as worker_client

    try:
        return await worker_client.run_media_job_sync(
            user_id=user_id,
            board_id=board_id,
            kind=kind,
            payload=payload,
            timeout=timeout,
            workbench_id=workbench_id,
        )
    except worker_client.WorkerUnavailableError:
        logger.warning(
            "job worker unavailable — running %s locally on canvas process",
            kind,
        )
        from backend.job_worker.media_runner import run_media_job

        return await run_media_job(
            {
                "user_id": user_id,
                "board_id": board_id,
                "workbench_id": workbench_id,
                "kind": kind,
                "payload": payload,
            }
        )

def _launch_generation(task_id: str, coro, *, board_id: str | None = None, workbench_id: str | None = None) -> None:
    async def _with_board() -> None:
        from backend import board_assets

        board_token = board_assets.set_board_id(board_id)
        wb_token = board_assets.set_workbench_id(workbench_id)
        try:
            await coro
        finally:
            board_assets.reset_workbench_id(wb_token)
            board_assets.reset_board_id(board_token)

    task_runtime.launch(task_id, _with_board())


async def _run_with_account_failover(
    task_id: str,
    *,
    kind: str,
    start_account_id: str,
    runner,
    provider: str = "doubao",
    doubao_tier: str | None = None,
):
    """Run generation; on rate-limit/quota, try another account. Policy reject stops immediately."""
    tried: set[str] = set()
    account_id = start_account_id
    last_err: Exception | None = None
    switches = 0
    want = account_manager.normalize_provider(provider)
    if want == "gpt":
        raise RuntimeError("OpenAI 渠道已下线，请改用 BenDi")
    # Doubao rate-limit/shark: avoid burning every account (1 switch).
    max_switches_rate = 1
    leased_id = start_account_id
    model_select_streak = 0

    try:
        while account_id:
            if task_runtime.is_cancelled(task_id):
                raise task_runtime.TaskCancelledError("已手动暂停")
            tried.add(account_id)
            account = account_manager.get_account(account_id)
            if account:
                task_manager.update_task(
                    task_id,
                    account_id=account.id,
                    account_name=account.name,
                    status=TaskStatus.generating,
                    error=None,
                )
            try:
                await runner(account_id)
                return
            except Exception as exc:
                last_err = exc
                if account_manager.is_policy_reject_error(exc):
                    logger.warning(
                        "task %s: policy/copyright reject on %s, stop",
                        task_id,
                        account_id,
                    )
                    raise

                if account_manager.is_model_unavailable_error(exc):
                    raise

                if account_manager.is_model_select_error(exc):
                    model_select_streak += 1
                    try:
                        pool = get_pool()
                        await pool.close_context(account_id)
                    except Exception as close_exc:
                        logger.debug(
                            "task %s: close browser after model select fail: %s",
                            task_id,
                            close_exc,
                        )
                    if model_select_streak >= 2:
                        logger.warning(
                            "task %s: model select failed on %d account(s) in a row (tried=%s)",
                            task_id,
                            model_select_streak,
                            sorted(tried),
                        )
                        raise RuntimeError(
                            f"无法选择模型：已连续 {model_select_streak} 个账号在页面上点不到该模型。"
                            f"详情：{exc}"
                            "。请先在网页手动确认模型是否可用，或换 Seedream 4.0 / 5.0 Lite 后重试。"
                        ) from exc
                    nxt, reason_code = account_manager.acquire_account(
                        None,
                        kind=kind,
                        exclude_ids=tried,
                        allow_queue_on_preferred=False,
                        provider=want,
                        doubao_tier=doubao_tier,
                    )
                    if not nxt:
                        raise RuntimeError(
                            "当前账号无法选择该模型，且没有其他可用账号。"
                            f"（{reason_code}）"
                        ) from exc
                    logger.warning(
                        "task %s: model select failed on %s (streak=%d) → switch to %s (%s)",
                        task_id,
                        account_id,
                        model_select_streak,
                        nxt.id,
                        nxt.name,
                    )
                    account_manager.release_account(leased_id)
                    leased_id = nxt.id
                    await asyncio.sleep(5)
                    switches += 1
                    account_id = nxt.id
                    continue

                # Live package ≠ DouBao / DouBao S: account tier already persisted; switch.
                if account_manager.is_tier_mismatch_error(exc):
                    model_select_streak = 0
                    try:
                        pool = get_pool()
                        await pool.close_context(account_id)
                    except Exception as close_exc:
                        logger.debug(
                            "task %s: close browser after tier mismatch: %s",
                            task_id,
                            close_exc,
                        )
                    nxt, reason_code = account_manager.acquire_account(
                        None,
                        kind=kind,
                        exclude_ids=tried,
                        allow_queue_on_preferred=False,
                        provider=want,
                        doubao_tier=doubao_tier,
                    )
                    if not nxt:
                        want_label = (
                            "Seed S（升级套餐）"
                            if doubao_tier == "upgrade"
                            else "Seed（普通）"
                        )
                        raise RuntimeError(
                            f"套餐与渠道不符，且没有其他可用的 {want_label} 账号。"
                            f"（{reason_code}）详情：{exc}"
                        ) from exc
                    logger.warning(
                        "task %s: tier mismatch on %s → switch to %s (%s)",
                        task_id,
                        account_id,
                        nxt.id,
                        nxt.name,
                    )
                    account_manager.release_account(leased_id)
                    leased_id = nxt.id
                    await asyncio.sleep(1)
                    switches += 1
                    account_id = nxt.id
                    continue

                model_select_streak = 0

                rate = (
                    account_manager.is_rate_limit_error(exc)
                    or account_manager.is_quota_exhausted_error(exc)
                    or account_manager.is_video_quota_exhausted_error(exc)
                )
                if not rate:
                    raise

                video_quota = account_manager.is_video_quota_exhausted_error(exc)
                quota = account_manager.is_quota_exhausted_error(exc)
                parsed_reset = account_manager.parse_limit_reset_seconds(exc)
                brand = "Seed"
                bytedance = want in ("doubao", "dola")
                if video_quota and kind == "video":
                    fail_kind = "video-quota"
                    reason = "今日视频额度已满，已标记该账号次日再用"
                    max_switches = (
                        account_manager.doubao_quota_max_switches()
                        if bytedance
                        else 5
                    )
                    cool_secs = account_manager.RATE_LIMIT_COOLDOWN_SECONDS
                elif quota:
                    cool_secs = account_manager.cooldown_seconds_for_error(
                        exc,
                        default=account_manager.QUOTA_COOLDOWN_SECONDS,
                    )
                    reason = f"{brand} 额度已用完，已标记该账号（自动换号）"
                    if bytedance:
                        max_switches = account_manager.doubao_quota_max_switches()
                    else:
                        max_switches = 5 if kind == "video" else max_switches_rate
                    fail_kind = "quota"
                else:
                    if parsed_reset is not None:
                        cool_secs = parsed_reset
                        mins = max(1, int(round(cool_secs / 60)))
                        reason = f"{brand} 限流/额度，约 {mins} 分钟后恢复"
                    else:
                        cool_secs = account_manager.RATE_LIMIT_COOLDOWN_SECONDS
                        reason = f"{brand} 触发限流/安全验证，冷却中"
                    max_switches = max_switches_rate
                    fail_kind = "rate-limited"

                if bytedance and fail_kind in ("quota", "video-quota") and kind == "video":
                    account_manager.mark_account_video_quota_exhausted(
                        account_id,
                        reason=reason,
                    )
                    await _on_doubao_video_quota_exhausted(account_id)
                elif bytedance and fail_kind == "quota":
                    account_manager.mark_account_daily_quota_exhausted(
                        account_id,
                        reason=reason,
                    )
                    await _on_doubao_video_quota_exhausted(account_id)
                else:
                    account_manager.mark_account_rate_limited(
                        account_id,
                        seconds=cool_secs,
                        reason=reason,
                    )
                if switches >= max_switches:
                    logger.warning(
                        "task %s: %s, stop failover after %d switch(es) (tried=%s)",
                        task_id,
                        fail_kind,
                        switches,
                        sorted(tried),
                    )
                    label = "Seed"
                    if fail_kind == "video-quota":
                        raise RuntimeError(
                            f"所有{label}账号今日视频额度已用完，请次日再试"
                        ) from exc
                    if fail_kind == "quota" and want in ("doubao", "dola"):
                        raise RuntimeError(
                            f"所有{label}账号今日额度已用完，请次日再试"
                        ) from exc
                    raise RuntimeError(
                        f"{label}触发限流/安全验证。请稍等 1～2 分钟再试；"
                        "期间不要连点生成。"
                    ) from exc
                nxt, reason_code = account_manager.acquire_account(
                    None,
                    kind=kind,
                    exclude_ids=tried,
                    allow_queue_on_preferred=False,
                    provider=want,
                    doubao_tier=doubao_tier,
                )
                if not nxt:
                    logger.warning(
                        "task %s: %s on %s, no free account (%s, tried=%s)",
                        task_id,
                        fail_kind,
                        account_id,
                        reason_code,
                        sorted(tried),
                    )
                    if fail_kind == "video-quota":
                        raise RuntimeError(
                            "所有 Seed 账号今日视频额度已用完，请次日再试"
                        ) from exc
                    if fail_kind == "quota":
                        raise RuntimeError(
                            "所有 Seed 账号今日额度已用完，请次日再试"
                        ) from exc
                    raise RuntimeError(
                        "当前账号已触发限流/安全验证。"
                        "请稍等 1～2 分钟，或登录另一个账号后再试。"
                    ) from exc
                logger.warning(
                    "task %s: account %s %s → switch to %s (%s)",
                    task_id,
                    account_id,
                    fail_kind,
                    nxt.id,
                    nxt.name,
                )
                account_manager.release_account(leased_id)
                leased_id = nxt.id
                await asyncio.sleep(12)
                switches += 1
                account_id = nxt.id

        if last_err:
            raise last_err
        raise RuntimeError("没有可用账号")
    finally:
        account_manager.release_account(leased_id)


def _is_myapi_model(model: str | None) -> bool:
    from backend import myapi_store

    return myapi_store.is_myapi_model(model)


def _is_bendi_model(model: str | None) -> bool:
    s = str(model or "").strip().lower()
    return s.startswith("bendi_") or s.startswith("bendi:")


def _is_volcengine_model(model: str | None) -> bool:
    from backend.volcengine.models import is_volcengine_model

    return is_volcengine_model(model)


def _is_volcengine_request(channel: str | None, model: str | None) -> bool:
    ch = str(channel or "").strip().lower()
    if ch == "volcengine":
        return True
    if ch in {
        "doubao",
        "doubao_s",
        "dola",
        "dola_s",
        "myapi",
        "bendi",
        "gpt",
        "relay",
        "public",
        "browser",
    }:
        return False
    return _is_volcengine_model(model)


def _is_bendi_request(channel: str | None, model: str | None) -> bool:
    """Only BenDi channel (or legacy unset + BenDi-native model) routes to local Xingwan/Dola.

    Explicit browser/API channels (doubao / doubao_s / myapi / …) must never be
    hijacked just because a Seedance model id shares a display name with BenDi.
    """
    ch = str(channel or "").strip().lower()
    if ch == "bendi":
        return True
    # Explicit non-BenDi channels always keep their own pipeline.
    if ch in {
        "doubao",
        "doubao_s",
        "dola",
        "dola_s",
        "myapi",
        "volcengine",
        "gpt",  # retired browser OpenAI — do not hijack to BenDi
        "relay",
        "public",
        "browser",
    }:
        return False
    # Legacy / missing channel: infer only from BenDi-native model ids (hyphen / bendi_*).
    return _is_bendi_model(model)


def _is_public_model(model: str | None) -> bool:
    from backend import public_models_store

    return public_models_store.is_public_model(model)


def _is_relay_model(model: str | None) -> bool:
    return _is_public_model(model) or _is_myapi_model(model)


def _resolve_relay_model(model: str | None, kind: str) -> tuple[dict, str]:
    """Return (cfg, source) where source is 'public' | 'myapi'."""
    from backend import myapi_store, public_models_store

    mid = str(model or "").strip()
    if not mid:
        raise HTTPException(400, "未指定模型")

    if mid.startswith("public_") or public_models_store.get_model(mid):
        raw = public_models_store.get_model(mid)
        if not raw:
            raise HTTPException(400, "公共模型不存在")
        if not raw.get("enabled", True):
            raise HTTPException(400, f"公共模型「{raw.get('name')}」已停用")
        if raw.get("kind") != kind:
            raise HTTPException(400, f"公共模型类型不匹配（需要{kind}）")
        if not raw.get("api_key"):
            raise HTTPException(400, f"公共模型「{raw.get('name')}」未配置密钥")
        return raw, "public"

    if myapi_store.is_myapi_model(mid):
        cfg = myapi_store.get_model(mid)
        if not cfg or cfg.get("kind") != kind:
            raise HTTPException(400, f"MyApi {kind}模型不存在，请在设置中添加")
        if not cfg.get("api_key"):
            raise HTTPException(400, f"MyApi 模型「{cfg.get('name')}」未配置密钥")
        return cfg, "myapi"

    raise HTTPException(400, "模型不存在")


async def _start_relay_generation(
    *,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
):
    from backend.auth_deps import require_login_user
    from backend import points
    from backend import service_settings
    from backend.points import InsufficientPointsError
    from backend.prompt_refs import sanitize_generation_prompt

    # Never persist / forward workbench soft-ref codes (@ref:wbasset-…) to MyApi.
    prompt = sanitize_generation_prompt(prompt)
    cfg, source = _resolve_relay_model(model, kind)
    user = require_login_user()
    cost = points.ZERO
    if source == "public" and service_settings.get_points_enabled():
        est_tokens = points.estimate_prompt_tokens(prompt or "") if kind == "text" else None
        cost = points.compute_cost(
            cfg,
            prompt=prompt or "",
            duration=duration,
            resolution=resolution,
            prompt_tokens=est_tokens,
        )

    account_label = "公共模型" if source == "public" else "MyApi"
    task = task_manager.create_task(
        "myapi",
        cfg.get("name") or account_label,
        prompt,
        ratio or "自动",
        kind=kind,
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
    )
    _record_generation_start(
        task,
        user_id=user["id"],
        username=user.get("username") or user.get("display_name") or "",
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
        kind=kind,
        channel="myapi",
        model=str(model),
        prompt=prompt,
    )

    if cost > points.ZERO:
        try:
            points.reserve(
                user["id"],
                cost,
                task.id,
                meta={
                    "model": str(model),
                    "model_name": str(cfg.get("name") or ""),
                    "kind": kind,
                    "source": source,
                    "billing_mode": cfg.get("billing_mode"),
                    "points_price": cfg.get("points_price"),
                    "resolution": resolution or "",
                    "duration": duration,
                    "ratio": ratio or "",
                },
            )
        except InsufficientPointsError as exc:
            task_manager.update_task(
                task.id,
                status=TaskStatus.failed,
                error=str(exc),
                completed_at=__import__("datetime").datetime.now().isoformat(),
            )
            _record_generation_patch(
                task.id,
                status=TaskStatus.failed.value,
                error=str(exc),
                raw_error=str(exc),
                completed_at=__import__("datetime").datetime.now().isoformat(),
            )
            raise HTTPException(
                402,
                {
                    "code": "INSUFFICIENT_POINTS",
                    "message": str(exc),
                    "balance": points.format_points(exc.balance),
                    "need": points.format_points(exc.need),
                },
            ) from exc

    logger.info(
        "%s task %s: %s model=%s cost=%s → job worker",
        kind,
        task.id,
        source,
        model,
        points.format_points(cost),
    )
    await _dispatch_myapi_to_worker(
        task.id,
        user_id=user["id"],
        board_id=board_id,
        workbench_id=workbench_id,
        kind=kind,
        model=str(model),
        prompt=prompt,
        ratio=ratio or "自动",
        duration=duration,
        resolution=resolution,
        style=style,
        references=references,
        reference_images=reference_images,
    )
    return task


async def _start_bendi_generation(
    *,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
):
    """BenDi was removed."""
    raise HTTPException(410, "BenDi 已移除，请改用 MyApi")



async def _dispatch_bendi_to_worker(
    task_id: str,
    *,
    user_id: str,
    board_id: str | None,
    workbench_id: str | None = None,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
) -> None:
    from backend.job_worker import client as worker_client

    ok, detail = await worker_client.is_available_retry()
    if not ok:
        now = __import__("datetime").datetime.now().isoformat()
        err = detail or "任务服务（8089）未启动，请联系管理员"
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=err,
            completed_at=now,
        )
        return
    try:
        await worker_client.submit_job(
            task_id=task_id,
            user_id=user_id,
            board_id=board_id,
            workbench_id=workbench_id,
            kind=kind,
            model=model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            references=references,
            reference_images=reference_images,
            channel="bendi",
        )
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value)
    except worker_client.WorkerSubmitUncertain as exc:
        # The worker may already be generating. Keep this exact task binding.
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value,
                                 raw_error=str(exc), error="接单响应未确认，正在同步原任务")
    except Exception as exc:
        logger.exception("dispatch BenDi task %s to worker failed", task_id)
        err = str(exc) or "提交本机任务失败"
        now = __import__("datetime").datetime.now().isoformat()
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=err,
            completed_at=now,
        )


async def _dispatch_myapi_to_worker(
    task_id: str,
    *,
    user_id: str,
    board_id: str | None,
    workbench_id: str | None = None,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
) -> None:
    """Submit MyApi generation to local job worker (8089)."""
    from backend.job_worker import client as worker_client
    from backend import points

    ok, detail = await worker_client.is_available_retry()
    if not ok:
        points.refund(task_id, reason="worker_unavailable")
        now = __import__("datetime").datetime.now().isoformat()
        err = detail or "任务服务（8089）未启动，请联系管理员"
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=err,
            completed_at=now,
        )
        return
    try:
        await worker_client.submit_job(
            task_id=task_id,
            user_id=user_id,
            board_id=board_id,
            workbench_id=workbench_id,
            kind=kind,
            model=model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            style=style,
            references=references,
            reference_images=reference_images,
        )
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value)
    except worker_client.WorkerSubmitUncertain as exc:
        # The worker may already be generating. Keep this exact task binding.
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value,
                                 raw_error=str(exc), error="接单响应未确认，正在同步原任务")
    except Exception as exc:
        logger.exception("dispatch MyApi task %s to worker failed", task_id)
        points.refund(task_id, reason="dispatch_failed")
        err = _sanitize_task_error(str(exc), kind=kind, source="myapi")
        now = __import__("datetime").datetime.now().isoformat()
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=str(exc),
            completed_at=now,
        )


async def _start_volcengine_generation(
    *,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
):
    """Volcengine / Ark official channel — separate from MyApi."""
    from backend.auth_deps import require_login_user
    from backend import service_settings
    from backend.prompt_refs import sanitize_generation_prompt
    from backend.volcengine import models as ve_models
    from backend.volcengine import settings as ve_settings

    user = require_login_user()
    if not service_settings.get_volcengine_channel_enabled():
        raise HTTPException(403, "管理员已关闭火山引擎渠道")
    prompt = sanitize_generation_prompt(prompt)
    k = (kind or "").strip().lower()
    try:
        ve_settings.require_ready(k)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    mid = str(model or "").strip()
    catalog = ve_models.get_model(mid)
    if not catalog or catalog.get("kind") != k:
        raise HTTPException(400, f"请选择火山引擎{k}模型")

    task = task_manager.create_task(
        "volcengine",
        catalog.get("label") or "火山引擎",
        prompt,
        ratio or "自动",
        kind=kind,
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
    )
    _record_generation_start(
        task,
        user_id=user["id"],
        username=user.get("username") or user.get("display_name") or "",
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
        kind=kind,
        channel="volcengine",
        model=mid,
        prompt=prompt,
    )
    await _dispatch_volcengine_to_worker(
        task.id,
        user_id=user["id"],
        board_id=board_id,
        workbench_id=workbench_id,
        kind=kind,
        model=mid,
        prompt=prompt,
        ratio=ratio or "自动",
        duration=duration,
        resolution=resolution,
        references=references,
        reference_images=reference_images,
    )
    return task


async def _dispatch_volcengine_to_worker(
    task_id: str,
    *,
    user_id: str,
    board_id: str | None,
    workbench_id: str | None = None,
    kind: str,
    model: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
) -> None:
    from backend.job_worker import client as worker_client

    ok, detail = await worker_client.is_available_retry()
    if not ok:
        now = __import__("datetime").datetime.now().isoformat()
        err = detail or "任务服务（8089）未启动，请联系管理员"
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=err,
            completed_at=now,
        )
        return
    try:
        await worker_client.submit_job(
            task_id=task_id,
            user_id=user_id,
            board_id=board_id,
            workbench_id=workbench_id,
            kind=kind,
            model=model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            channel="volcengine",
        )
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value)
    except worker_client.WorkerSubmitUncertain as exc:
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(
            task_id,
            status=TaskStatus.generating.value,
            raw_error=str(exc),
            error="接单响应未确认，正在同步原任务",
        )
    except Exception as exc:
        logger.exception("dispatch Volcengine task %s to worker failed", task_id)
        err = str(exc) or "提交火山引擎任务失败"
        now = __import__("datetime").datetime.now().isoformat()
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=err,
            completed_at=now,
        )


async def _require_browser_worker() -> None:
    """Ensure 8089 is up and has browser_channels (Doubao sim)."""
    from backend.job_worker import client as worker_client

    ok, detail = await worker_client.is_available_retry()
    if not ok:
        raise HTTPException(
            503,
            {
                "code": "WORKER_UNAVAILABLE",
                "message": detail
                or "任务服务（8089）未启动。请先运行「启动任务服务」后再用 Seed。",
            },
        )
    if not await worker_client.worker_supports_browser():
        raise HTTPException(
            503,
            {
                "code": "BROWSER_CHANNELS_DISABLED",
                "message": "当前任务服务不支持该请求，请使用 MyApi 渠道。",
            },
        )


async def _dispatch_browser_to_worker(
    task_id: str,
    *,
    user_id: str,
    board_id: str | None,
    workbench_id: str | None = None,
    kind: str,
    prompt: str,
    ratio: str = "自动",
    model: str | None = None,
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    account_id: str | None = None,
    provider: str = "doubao",
    use_upgrade: bool = False,
    doubao_tier: str | None = None,
) -> None:
    """Submit Doubao browser-sim generation to local job worker (8089)."""
    from backend.job_worker import client as worker_client
    from backend.job_worker.client import WorkerJobRejected

    worker_kind = f"browser_{kind}"
    try:
        data = await worker_client.submit_job(
            task_id=task_id,
            user_id=user_id,
            board_id=board_id,
            workbench_id=workbench_id,
            kind=worker_kind,
            model=model,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            style=style,
            references=references,
            reference_images=reference_images,
            account_id=account_id,
            provider=provider,
            use_upgrade=use_upgrade,
            doubao_tier=doubao_tier,
        )
        job = data.get("job") if isinstance(data, dict) else None
        fields: dict = {"status": TaskStatus.generating}
        if isinstance(job, dict):
            if job.get("account_id"):
                fields["account_id"] = job["account_id"]
            if job.get("account_name"):
                fields["account_name"] = job["account_name"]
        task_manager.update_task(task_id, **fields)
        patch = {"status": TaskStatus.generating.value}
        if fields.get("account_id"):
            patch["account_id"] = fields["account_id"]
        if fields.get("account_name"):
            patch["account_name"] = fields["account_name"]
        _record_generation_patch(task_id, **patch)
    except WorkerJobRejected as exc:
        detail = exc.detail
        code = "WORKER_REJECTED"
        message = getattr(exc, "message", None) or str(exc) or "任务服务拒绝接单"
        if isinstance(detail, dict):
            code = str(detail.get("code") or code)
            message = str(detail.get("message") or detail.get("msg") or message)
        if not str(message).strip():
            message = f"任务服务拒绝接单（HTTP {exc.status_code}）"
        now = __import__("datetime").datetime.now().isoformat()
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=message,
            completed_at=now,
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=message,
            raw_error=f"HTTP {exc.status_code}" if exc.status_code else None,
            raw_response=detail if isinstance(detail, dict) else {"message": message},
            completed_at=now,
        )
        raise HTTPException(
            exc.status_code if exc.status_code >= 400 else 503,
            {"code": code, "message": message},
        ) from exc
    except worker_client.WorkerSubmitUncertain as exc:
        # The worker may already be generating. Keep this exact task binding.
        task_manager.update_task(task_id, status=TaskStatus.generating)
        _record_generation_patch(task_id, status=TaskStatus.generating.value,
                                 raw_error=str(exc), error="接单响应未确认，正在同步原任务")
    except Exception as exc:
        logger.exception("dispatch browser task %s to worker failed", task_id)
        raw = str(exc).strip() or repr(exc)
        err = _sanitize_task_error(raw, kind=kind, source="browser")
        if not err:
            err = "任务服务接单失败，请查看任务服务窗口日志"
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
        _record_generation_patch(
            task_id,
            status=TaskStatus.failed.value,
            error=err,
            raw_error=raw,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
        raise HTTPException(503, {"code": "WORKER_SUBMIT_FAILED", "message": err}) from exc


async def _start_browser_generation(
    *,
    kind: str,
    prompt: str,
    ratio: str = "自动",
    model: str | None = None,
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
    preferred_account_id: str | None = None,
    provider: str = "doubao",
    use_upgrade: bool = False,
    channel: str | None = None,
):
    """Create canvas task and hand Doubao work to 8089."""
    from backend.auth_deps import require_login_user
    from backend.prompt_refs import sanitize_generation_prompt

    # Same scrub as MyApi: browser jobs must not carry @ref: soft-ref codes.
    prompt = sanitize_generation_prompt(prompt)

    ch_raw = str(channel or "").strip().lower()
    prov_raw = account_manager.normalize_provider(provider)
    if ch_raw == "gpt" or prov_raw == "gpt":
        raise HTTPException(410, "OpenAI 渠道已下线，请改用 BenDi")

    _reject_doubao_http()
    await _require_browser_worker()
    user = require_login_user()

    tier_filter = "upgrade" if use_upgrade else "regular"
    # Ordinary DouBao → regular-tier pool; DouBao S → upgrade.
    # Dola 不按套餐分流。

    task = task_manager.create_task(
        preferred_account_id or "browser",
        "浏览器模拟",
        prompt,
        ratio or "自动",
        kind=kind,
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
    )
    ch = channel or provider or "doubao"
    if use_upgrade and provider == "doubao":
        ch = "doubao_s"
    _record_generation_start(
        task,
        user_id=user["id"],
        username=user.get("username") or user.get("display_name") or "",
        board_id=board_id,
        workbench_id=workbench_id,
        node_id=node_id,
        kind=kind,
        channel=ch,
        model=model or "",
        prompt=prompt,
    )
    logger.info(
        "%s browser task %s → job worker (preferred=%s provider=%s)",
        kind,
        task.id,
        (preferred_account_id or "-")[:8],
        provider,
    )
    await _dispatch_browser_to_worker(
        task.id,
        user_id=user["id"],
        board_id=board_id,
        workbench_id=workbench_id,
        kind=kind,
        prompt=prompt,
        ratio=ratio or "自动",
        model=model,
        duration=duration,
        resolution=resolution,
        style=style,
        references=references,
        reference_images=reference_images,
        account_id=preferred_account_id,
        provider=provider,
        use_upgrade=use_upgrade if provider == "doubao" else False,
        doubao_tier=tier_filter if provider == "doubao" else None,
    )
    return task


async def _run_myapi_video_task(
    task_id: str,
    prompt: str,
    ratio: str,
    model: str,
    duration: int | None = None,
    resolution: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
):
    from backend.myapi import client as myapi_client

    task_manager.update_task(task_id, status=TaskStatus.generating)
    check = task_runtime.cancel_check(task_id)
    try:
        video = await myapi_client.run_for_model(
            model,
            kind="video",
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            cancel_check=check,
            client_task_id=task_id,
        )
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            video_url=video.get("video_url"),
            cover_url=video.get("cover_url") or "",
            duration=video.get("duration") or 0,
            width=video.get("width") or None,
            height=video.get("height") or None,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
    except task_runtime.TaskCancelledError:
        _mark_task_paused(task_id, myapi=True)
    except asyncio.CancelledError:
        _mark_task_paused(task_id, myapi=True)
        raise
    except Exception as exc:
        logger.exception("MyApi video failed")
        err = _sanitize_task_error(str(exc), kind="video", source="myapi")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )


async def _run_myapi_image_task(
    task_id: str,
    prompt: str,
    ratio: str,
    model: str,
    style: str | None = None,
    resolution: str | None = None,
):
    from backend.myapi import client as myapi_client

    task_manager.update_task(task_id, status=TaskStatus.generating)
    try:
        img = await myapi_client.run_for_model(
            model,
            kind="image",
            prompt=prompt,
            ratio=ratio,
            resolution=resolution,
            style=style,
        )
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            image_url=img.get("url"),
            width=img.get("width") or None,
            height=img.get("height") or None,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
    except Exception as exc:
        logger.exception("MyApi image failed")
        err = _sanitize_task_error(str(exc), kind="image", source="myapi")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )


async def _run_myapi_text_task(
    task_id: str,
    prompt: str,
    model: str,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
):
    from backend.myapi import client as myapi_client

    task_manager.update_task(task_id, status=TaskStatus.generating)
    try:
        result = await myapi_client.run_for_model(
            model,
            kind="text",
            prompt=prompt,
            references=references,
            reference_images=reference_images,
        )
        text = result.get("text") if isinstance(result, dict) else str(result or "")
        usage = result.get("usage") if isinstance(result, dict) else {}
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            text=text,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
        try:
            from backend import points
            from backend import public_models_store

            cfg = public_models_store.get_model(model)
            mode = str((cfg or {}).get("billing_mode") or "").lower()
            if mode == "per_char":
                mode = "per_token"
            actual = None
            settle_meta = {}
            if isinstance(usage, dict):
                if usage.get("prompt_tokens") is not None:
                    settle_meta["prompt_tokens"] = int(usage["prompt_tokens"])
                if usage.get("completion_tokens") is not None:
                    settle_meta["completion_tokens"] = int(usage["completion_tokens"])
                if usage.get("total_tokens") is not None:
                    settle_meta["total_tokens"] = int(usage["total_tokens"])
            if cfg and cfg.get("kind") == "text" and mode == "per_token" and cfg.get("points_required"):
                actual = points.compute_cost(
                    cfg,
                    prompt_tokens=settle_meta.get("prompt_tokens"),
                    completion_tokens=settle_meta.get("completion_tokens"),
                )
            points.commit(task_id, actual_amount=actual, meta=settle_meta or None)
        except Exception:
            pass
    except Exception as exc:
        logger.exception("MyApi text failed")
        err = _sanitize_task_error(str(exc), kind="text", source="myapi")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )
        try:
            from backend import points

            points.refund(task_id, reason="failed")
        except Exception:
            pass


async def _run_video_task(
    task_id: str,
    account_id: str,
    prompt: str,
    ratio: str,
    model: str | None = None,
    duration: int | None = None,
    resolution: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    *,
    use_upgrade: bool = False,
):
    if _is_myapi_model(model):
        await _run_myapi_video_task(
            task_id,
            prompt,
            ratio,
            str(model),
            duration,
            resolution,
            references,
            reference_images,
        )
        return

    if doubao_disabled():
        raise RuntimeError(
            "本部署仅支持 MyApi 渠道，请配置视频模型后重试。"
        )

    task_manager.update_task(task_id, status=TaskStatus.generating)
    check = task_runtime.cancel_check(task_id)
    start_acc = account_manager.get_account(account_id)
    start_prov = account_manager.normalize_provider(
        start_acc.provider if start_acc else "doubao"
    )
    use_upgrade_eff = bool(use_upgrade) if start_prov == "doubao" else False
    tier_filter = (
        ("upgrade" if use_upgrade_eff else "regular") if start_prov == "doubao" else None
    )

    async def _once(aid: str) -> None:
        async def _on_paid_quota_confirm() -> str:
            from backend.job_worker.browser_runner import _await_paid_quota_user_decision

            return await _await_paid_quota_user_decision(task_id, cancel_check=check)

        videos = await get_pool().generate_video(
            aid,
            prompt,
            ratio,
            model=model,
            duration=duration,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            cancel_check=check,
            use_upgrade=use_upgrade_eff,
            on_paid_quota_confirm=_on_paid_quota_confirm,
        )
        video = videos[0]
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            video_url=video.video_url,
            cover_url=video.cover_url,
            duration=video.duration,
            width=video.width or None,
            height=video.height or None,
            share_link=getattr(video, "share_link", None) or None,
            completed_at=__import__("datetime").datetime.now().isoformat(),
            user_prompt_kind=None,
            user_action=None,
            user_prompt_message=None,
        )

    try:
        await _run_with_account_failover(
            task_id,
            kind="video",
            start_account_id=account_id,
            runner=_once,
            doubao_tier=tier_filter,
        )
    except task_runtime.TaskCancelledError:
        _mark_task_paused(task_id)
    except asyncio.CancelledError:
        _mark_task_paused(task_id)
        raise
    except Exception as exc:
        logger.exception("Video generation failed")
        err = _sanitize_task_error(str(exc), kind="video")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )


async def _run_image_task(
    task_id: str,
    account_id: str,
    prompt: str,
    ratio: str,
    reference_images: list[str] | None = None,
    references: list[dict] | None = None,
    model: str | None = None,
    style: str | None = None,
    use_upgrade: bool = False,
):
    if _is_myapi_model(model):
        await _run_myapi_image_task(task_id, prompt, ratio, str(model), style)
        return

    provider = account_manager.provider_for_image_model(model)
    if doubao_disabled() and provider in ("doubao", "dola"):
        raise RuntimeError(
            "本部署仅支持 MyApi 渠道，请配置图片模型后重试。"
        )

    task_manager.update_task(task_id, status=TaskStatus.generating)
    check = task_runtime.cancel_check(task_id)
    start_acc = account_manager.get_account(account_id)
    start_prov = account_manager.normalize_provider(
        start_acc.provider if start_acc else provider
    )
    use_upgrade_eff = bool(use_upgrade) if start_prov == "doubao" else False
    tier_filter = (
        ("upgrade" if use_upgrade_eff else "regular") if start_prov == "doubao" else None
    )

    async def _once(aid: str) -> None:
        images = await get_pool().generate_image(
            aid,
            prompt,
            ratio,
            reference_images=reference_images,
            references=references,
            model=model,
            style=style,
            cancel_check=check,
            use_upgrade=use_upgrade_eff,
        )
        img = images[0]
        logger.info(
            "task %s: kept image %dx%d %s",
            task_id,
            int(img.width or 0),
            int(img.height or 0),
            str(img.url)[:80],
        )
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            image_url=img.url,
            width=img.width or None,
            height=img.height or None,
            share_link=getattr(img, "share_link", None) or None,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )

    try:
        await _run_with_account_failover(
            task_id,
            kind="image",
            start_account_id=account_id,
            runner=_once,
            provider=provider,
            doubao_tier=tier_filter,
        )
    except task_runtime.TaskCancelledError:
        _mark_task_paused(task_id)
    except asyncio.CancelledError:
        _mark_task_paused(task_id)
        raise
    except Exception as exc:
        logger.exception("Image generation failed")
        err = _sanitize_task_error(str(exc), kind="image")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )


async def _run_text_task(
    task_id: str,
    account_id: str,
    prompt: str,
    reference_images: list[str] | None = None,
    references: list[dict] | None = None,
    model: str | None = None,
):
    if _is_myapi_model(model):
        await _run_myapi_text_task(
            task_id,
            prompt,
            str(model),
            references=references,
            reference_images=reference_images,
        )
        return

    provider = account_manager.provider_for_text_model(model)
    if doubao_disabled() and provider in ("doubao", "dola"):
        raise RuntimeError(
            "本部署仅支持 MyApi 渠道，请配置文本模型后重试。"
        )

    task_manager.update_task(task_id, status=TaskStatus.generating)
    check = task_runtime.cancel_check(task_id)

    async def _once(aid: str) -> None:
        text = await get_pool().generate_text(
            aid,
            prompt,
            reference_images=reference_images,
            references=references,
            model=model,
            cancel_check=check,
        )
        logger.info("task %s: kept text (%d chars)", task_id, len(text or ""))
        task_manager.update_task(
            task_id,
            status=TaskStatus.completed,
            text=text,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )

    try:
        await _run_with_account_failover(
            task_id,
            kind="image",  # share image lease pool (text uses same browser chat)
            start_account_id=account_id,
            runner=_once,
            provider=provider,
        )
    except task_runtime.TaskCancelledError:
        _mark_task_paused(task_id)
    except asyncio.CancelledError:
        _mark_task_paused(task_id)
        raise
    except Exception as exc:
        logger.exception("Text generation failed")
        err = _sanitize_task_error(str(exc), kind="image")
        task_manager.update_task(
            task_id,
            status=TaskStatus.failed,
            error=err,
            completed_at=__import__("datetime").datetime.now().isoformat(),
        )


@app.post("/api/video/generate")
async def api_generate_video(body: VideoGenerateRequest, background_tasks: BackgroundTasks):
    _guard_body_media_refs(body)
    if _is_volcengine_request(getattr(body, "channel", None), body.model):
        task = await _start_volcengine_generation(
            kind="video",
            model=str(body.model or ""),
            prompt=body.prompt,
            ratio=body.ratio,
            duration=body.duration,
            resolution=body.resolution,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_bendi_request(getattr(body, "channel", None), body.model):
        task = await _start_bendi_generation(
            kind="video",
            model=str(body.model or ""),
            prompt=body.prompt,
            ratio=body.ratio,
            duration=body.duration,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_relay_model(body.model):
        task = await _start_relay_generation(
            kind="video",
            model=str(body.model),
            prompt=body.prompt,
            ratio=body.ratio,
            duration=body.duration,
            resolution=body.resolution,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()

    try:
        video_provider = account_manager.provider_for_video_channel(
            getattr(body, "channel", None),
            provider=getattr(body, "provider", None),
        )
    except ValueError as exc:
        raise HTTPException(410, str(exc)) from exc
    task = await _start_browser_generation(
        kind="video",
        prompt=body.prompt,
        ratio=body.ratio,
        model=body.model,
        duration=body.duration,
        resolution=body.resolution,
        references=[r.model_dump() for r in (body.references or [])],
        reference_images=body.reference_images,
        board_id=body.board_id,
        workbench_id=body.workbench_id,
        node_id=body.node_id,
        preferred_account_id=body.account_id,
        provider=video_provider,
        use_upgrade=bool(body.use_upgrade),
        channel=getattr(body, "channel", None),
    )
    return task.model_dump()


@app.post("/api/image/generate")
async def api_generate_image(body: ImageGenerateRequest, background_tasks: BackgroundTasks):
    _guard_body_media_refs(body)
    if _is_volcengine_request(getattr(body, "channel", None), body.model):
        task = await _start_volcengine_generation(
            kind="image",
            model=str(body.model or ""),
            prompt=body.prompt,
            ratio=body.ratio,
            resolution=body.resolution,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_bendi_request(getattr(body, "channel", None), body.model):
        task = await _start_bendi_generation(
            kind="image",
            model=str(body.model or "bendi_image"),
            prompt=body.prompt,
            ratio=body.ratio,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_relay_model(body.model):
        task = await _start_relay_generation(
            kind="image",
            model=str(body.model),
            prompt=body.prompt,
            ratio=body.ratio,
            resolution=body.resolution,
            style=body.style,
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()

    provider = None
    if getattr(body, "provider", None):
        provider = account_manager.normalize_provider(body.provider)
    else:
        provider = account_manager.provider_for_image_model(
            body.model, channel=getattr(body, "channel", None)
        )
    if provider == "gpt" or str(getattr(body, "channel", None) or "").strip().lower() == "gpt":
        raise HTTPException(410, "OpenAI 渠道已下线，请改用 BenDi")
    if doubao_disabled() and provider in ("doubao", "dola"):
        _reject_doubao_http()
    task = await _start_browser_generation(
        kind="image",
        prompt=body.prompt,
        ratio=body.ratio,
        model=body.model,
        style=body.style,
        references=[r.model_dump() for r in (body.references or [])],
        reference_images=body.reference_images,
        board_id=body.board_id,
        workbench_id=body.workbench_id,
        node_id=body.node_id,
        preferred_account_id=_prefer_sticky_doubao_account(
            body.account_id, kind="image", provider=provider
        ),
        provider=provider,
        use_upgrade=_body_use_upgrade(body),
        channel=getattr(body, "channel", None),
    )
    return task.model_dump()


@app.post("/api/text/generate")
async def api_generate_text(body: TextGenerateRequest, background_tasks: BackgroundTasks):
    _guard_body_media_refs(body)
    if _is_volcengine_request(getattr(body, "channel", None), body.model):
        task = await _start_volcengine_generation(
            kind="text",
            model=str(body.model or ""),
            prompt=body.prompt,
            ratio=body.ratio or "自动",
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_bendi_request(getattr(body, "channel", None), body.model):
        task = await _start_bendi_generation(
            kind="text",
            model=str(body.model or "dola-chat"),
            prompt=body.prompt,
            ratio=body.ratio or "自动",
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if _is_relay_model(body.model):
        task = await _start_relay_generation(
            kind="text",
            model=str(body.model),
            prompt=body.prompt,
            ratio=body.ratio or "自动",
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()

    if getattr(body, "provider", None):
        provider = account_manager.normalize_provider(body.provider)
    else:
        provider = account_manager.provider_for_text_model(
            body.model, channel=getattr(body, "channel", None)
        )
    if provider == "gpt" or str(getattr(body, "channel", None) or "").strip().lower() == "gpt":
        raise HTTPException(410, "OpenAI 渠道已下线，请改用 BenDi")
    if doubao_disabled() and provider in ("doubao", "dola"):
        _reject_doubao_http()
    task = await _start_browser_generation(
        kind="text",
        prompt=body.prompt,
        ratio=body.ratio or "自动",
        model=body.model,
        references=[r.model_dump() for r in (body.references or [])],
        reference_images=body.reference_images,
        board_id=body.board_id,
        workbench_id=body.workbench_id,
        node_id=body.node_id,
        preferred_account_id=_prefer_sticky_doubao_account(
            body.account_id, kind="text", provider=provider
        ),
        provider=provider,
        use_upgrade=_body_use_upgrade(body),
        channel=getattr(body, "channel", None),
    )
    return task.model_dump()


@app.post("/api/voice/generate")
async def api_generate_voice(body: VoiceGenerateRequest, background_tasks: BackgroundTasks):
    """Voice via Volcengine channel or MyApi / public relay."""
    del background_tasks
    _guard_body_media_refs(body)
    if _is_volcengine_request(getattr(body, "channel", None), body.model):
        task = await _start_volcengine_generation(
            kind="voice",
            model=str(body.model or ""),
            prompt=body.prompt,
            ratio="自动",
            references=[r.model_dump() for r in (body.references or [])],
            reference_images=body.reference_images,
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            node_id=body.node_id,
        )
        return task.model_dump()
    if not _is_relay_model(body.model):
        raise HTTPException(400, "语音生成请先在 MyApi / 公共模型 / 火山引擎中配置语音模型")
    task = await _start_relay_generation(
        kind="voice",
        model=str(body.model),
        prompt=body.prompt,
        ratio="自动",
        references=[r.model_dump() for r in (body.references or [])],
        reference_images=body.reference_images,
        board_id=body.board_id,
        workbench_id=body.workbench_id,
        node_id=body.node_id,
    )
    return task.model_dump()


@app.get("/api/logs")
def api_logs(since: int = 0, limit: int = 200, board_id: str = Query("")):
    from backend.auth_deps import require_login_user

    user = require_login_user()
    return log_buffer.snapshot(
        user_id=str(user.get("id") or ""),
        board_id=board_id.strip(),
        since_id=since,
        limit=min(max(limit, 1), 400),
    )


@app.delete("/api/logs")
def api_clear_logs(board_id: str = Query("")):
    from backend.auth_deps import require_login_user

    user = require_login_user()
    log_buffer.clear(user_id=str(user.get("id") or ""), board_id=board_id.strip())
    return {"ok": True}


@app.get("/api/media-models")
def api_media_models():
    from backend import myapi_store, public_models_store
    from backend import service_settings

    bendi_video: list = []
    bendi_image: list = []

    volc_image: list = []
    volc_video: list = []
    volc_text: list = []
    volc_voice: list = []
    volc_agent: list = []
    volc_view: dict = {}
    try:
        if service_settings.get_volcengine_channel_enabled():
            from backend.volcengine import models as ve_models
            from backend.volcengine import settings as ve_settings

            volc_view = ve_settings.public_view()
            if volc_view.get("api_key_set"):
                kinds = volc_view.get("kinds_enabled") or {}
                if kinds.get("image"):
                    volc_image = [ve_models.picker_entry(m) for m in ve_models.VOLCENGINE_IMAGE_MODELS]
                if kinds.get("video"):
                    volc_video = [ve_models.picker_entry(m) for m in ve_models.VOLCENGINE_VIDEO_MODELS]
                if kinds.get("text"):
                    volc_text = [ve_models.picker_entry(m) for m in ve_models.VOLCENGINE_TEXT_MODELS]
                if kinds.get("voice"):
                    volc_voice = [
                        ve_models.picker_entry(m)
                        for m in ve_models.VOLCENGINE_VOICE_MODELS
                        if m.get("canvas", True)
                    ]
                volc_agent = ve_settings.list_agent_picker()
    except Exception:
        pass

    myapi = myapi_store.public_list()
    public = public_models_store.public_list(enabled_only=True)
    myapi_image = [
        {"id": m["id"], "label": m["name"], "myapi": True}
        for m in myapi
        if m.get("kind") == "image"
    ]
    myapi_video = [
        {"id": m["id"], "label": m["name"], "myapi": True}
        for m in myapi
        if m.get("kind") == "video"
    ]
    myapi_text = [
        {"id": m["id"], "label": m["name"], "myapi": True}
        for m in myapi
        if m.get("kind") == "text"
    ]
    myapi_voice = [
        {"id": m["id"], "label": m["name"], "myapi": True, "kind": "voice"}
        for m in myapi
        if m.get("kind") == "voice"
    ]
    public_image = [
        {
            "id": m["id"],
            "label": m["name"],
            "public": True,
            "points_required": m.get("points_required"),
            "billing_mode": m.get("billing_mode"),
            "points_price": m.get("points_price"),
            "billing_label": m.get("billing_label") or "",
        }
        for m in public
        if m.get("kind") == "image"
    ]
    public_video = [
        {
            "id": m["id"],
            "label": m["name"],
            "name": m["name"],
            "kind": "video",
            "public": True,
            "points_required": m.get("points_required"),
            "billing_mode": m.get("billing_mode"),
            "points_price": m.get("points_price"),
            "billing_label": m.get("billing_label") or "",
            "resolution_prices": dict(m.get("resolution_prices") or {}),
            "resolutions": list(m.get("resolutions") or []),
        }
        for m in public
        if m.get("kind") == "video"
    ]
    public_text = [
        {
            "id": m["id"],
            "label": m["name"],
            "public": True,
            "points_required": m.get("points_required"),
            "billing_mode": m.get("billing_mode"),
            "points_price": m.get("points_price"),
            "billing_label": m.get("billing_label") or "",
        }
        for m in public
        if m.get("kind") == "text"
    ]
    public_voice = [
        {
            "id": m["id"],
            "label": m["name"],
            "public": True,
            "kind": "voice",
            "points_required": m.get("points_required"),
            "billing_mode": m.get("billing_mode"),
            "points_price": m.get("points_price"),
            "billing_label": m.get("billing_label") or "",
        }
        for m in public
        if m.get("kind") == "voice"
    ]
    public_agent = [
        {
            "id": m["id"],
            "label": m["name"],
            "name": m["name"],
            "kind": "agent",
            "public": True,
            "points_required": m.get("points_required"),
            "billing_mode": m.get("billing_mode"),
            "points_price": m.get("points_price"),
            "billing_label": m.get("billing_label") or "",
        }
        for m in public
        if m.get("kind") == "agent"
    ]
    myapi_agent = [
        {"id": m["id"], "label": m["name"], "myapi": True, "kind": "agent"}
        for m in myapi
        if m.get("kind") == "agent"
    ]
    dm = doubao_media_models()
    return {
        "image": list(dm["IMAGE_MODELS"]) + public_image + myapi_image + bendi_image + volc_image,
        "video": list(dm["VIDEO_MODELS"]) + public_video + myapi_video + bendi_video + volc_video,
        "text": public_text + myapi_text + volc_text,
        "voice": public_voice + myapi_voice + volc_voice,
        "agent": public_agent + myapi_agent + volc_agent,
        "myapi": myapi,
        "bendi": {"video": bendi_video, "image": bendi_image},
        "volcengine": {
            "image": volc_image,
            "video": volc_video,
            "text": volc_text,
            "voice": volc_voice,
            "agent": volc_agent,
            "settings": volc_view,
        },
        "public": public,
        "image_ratios": dm["IMAGE_RATIOS"],
        "image_styles": dm["IMAGE_STYLES"],
        "video_ratios": dm["VIDEO_RATIOS"],
        "video_duration_min": dm["VIDEO_DURATION_MIN"],
        "video_duration_max": dm["VIDEO_DURATION_MAX"],
        "video_duration_ticks": dm["VIDEO_DURATION_TICKS"],
    }


@app.get("/api/tasks")
async def api_list_tasks(kind: str | None = None):
    from backend.job_worker.reconcile import reconcile_user_myapi_tasks
    from backend.user_scope import get_current_user_id
    from backend import gen_records

    uid = get_current_user_id()
    if uid:
        try:
            await reconcile_user_myapi_tasks(uid)
        except Exception as exc:
            logger.warning("MyApi task reconcile on poll failed: %s", exc)
    out = []
    for t in task_manager.list_tasks(kind=kind):
        data = t.model_dump()
        # Older tasks may lack node_id on the task row — fill from gen_records for rebind.
        if uid and not data.get("node_id"):
            try:
                rec = gen_records.get_record(t.id)
                if rec and str(rec.get("user_id") or "") == str(uid):
                    if rec.get("node_id"):
                        data["node_id"] = rec.get("node_id")
                    if not data.get("board_id") and rec.get("board_id"):
                        data["board_id"] = rec.get("board_id")
            except Exception:
                pass
        out.append(data)
    return {"tasks": out}


@app.get("/api/video/tasks")
def api_list_video_tasks():
    return {"tasks": [t.model_dump() for t in task_manager.list_tasks(kind="video")]}


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    data = task.model_dump()
    try:
        from backend import board_assets

        for key in ("image_url", "thumb_url", "cover_url", "video_url"):
            raw = str(data.get(key) or "").strip()
            if not raw.startswith("/uploads/"):
                continue
            fixed = board_assets.repair_flat_upload_url(raw)
            if fixed != raw:
                data[key] = fixed
    except Exception:
        pass
    return data


@app.get("/api/gen-records")
def api_list_gen_records(
    q: str = Query(""),
    status: str = Query(""),
    account_id: str = Query(""),
    user_id: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    limit: int = Query(20),
    offset: int = Query(0),
):
    """Generation audit list for local thread records panel."""
    from backend import gen_records
    from backend.auth_deps import require_login_user

    require_login_user()
    rows, total, stats = gen_records.list_records(
        q=q or None,
        status=status or None,
        account_id=account_id or None,
        user_id=user_id or None,
        date_from=date_from or None,
        date_to=date_to or None,
        limit=limit,
        offset=offset,
    )
    page_size = max(1, min(500, int(limit or 20)))
    page = (max(0, int(offset or 0)) // page_size) + 1 if total else 1
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return {
        "records": rows,
        "total": total,
        "stats": stats,
        "limit": page_size,
        "offset": max(0, int(offset or 0)),
        "page": page,
        "total_pages": total_pages,
    }


@app.post("/api/gen-records/purge")
def api_purge_gen_records(body: dict = Body(default_factory=dict)):
    from backend import gen_records
    from backend.auth_deps import require_login_user

    require_login_user()
    mode = str((body or {}).get("mode") or "").strip().lower()
    keep = (body or {}).get("keep")
    try:
        result = gen_records.purge_records(mode=mode, keep=int(keep or 0))
    except ValueError as exc:
        raise HTTPException(400, str(exc) or "参数无效") from exc
    return {"ok": True, **result}


@app.get("/api/gen-records/{record_id}")
def api_get_gen_record(record_id: str):
    """Return generation audit detail (incl. raw upstream error)."""
    from backend import gen_records
    from backend.auth_deps import require_login_user

    require_login_user()
    row = gen_records.detail_view(record_id)
    if not row:
        raise HTTPException(404, "记录不存在")
    return row


@app.post("/api/gen-records/{record_id}/force-stop")
async def api_force_stop_gen_record(record_id: str):
    """Force a live generation into a local terminal state and stop its worker job."""
    from datetime import datetime

    from backend import gen_records, task_manager
    from backend.auth_deps import require_login_user
    from backend.job_worker import client as worker_client
    from backend.job_worker import poll_ledger
    from backend.models import TaskStatus
    from backend.user_scope import reset_current_user_id, set_current_user_id

    actor = require_login_user()
    row = gen_records.get_record(record_id)
    if not row:
        raise HTTPException(404, "记录不存在")

    record_status = str(row.get("status") or "").strip().lower()
    if record_status not in ("pending", "generating"):
        return {
            "ok": True,
            "task_id": record_id,
            "already_done": True,
            "status": record_status,
        }

    owner_id = str(row.get("user_id") or "").strip()
    now = datetime.now().isoformat()
    reason = "本地强制中止"
    task_status = ""

    if owner_id:
        scope_token = set_current_user_id(owner_id)
        try:
            task = task_manager.get_task(record_id)
            if task:
                task_status = task.status.value
                if task.status == TaskStatus.completed:
                    gen_records.upsert_record(
                        record_id,
                        status=TaskStatus.completed.value,
                        error="",
                        completed_at=task.completed_at or now,
                    )
                    return {
                        "ok": True,
                        "task_id": record_id,
                        "already_done": True,
                        "status": TaskStatus.completed.value,
                    }
                updated = task_manager.update_task(
                    record_id,
                    status=TaskStatus.failed,
                    error=reason,
                    completed_at=now,
                    user_prompt_kind=None,
                    user_action=None,
                    user_prompt_message=None,
                )
                if updated:
                    task_status = updated.status.value
        finally:
            reset_current_user_id(scope_token)

    gen_records.upsert_record(
        record_id,
        status=TaskStatus.failed.value,
        error=reason,
        completed_at=now,
        stopped_at=now,
        stopped_by=str(actor.get("username") or actor.get("id") or "local"),
    )
    poll_ledger.update_record(
        record_id,
        state="cancelled",
        upstream_stage="local_force_stop",
    )
    worker_cancelled = await worker_client.cancel_job(record_id)
    return {
        "ok": True,
        "task_id": record_id,
        "status": task_status or TaskStatus.failed.value,
        "worker_cancelled": bool(worker_cancelled),
    }


@app.post("/api/tasks/{task_id}/recheck")
async def api_recheck_task(task_id: str):
    """Re-poll a recoverable MyApi upstream task without re-submitting it."""
    from backend.auth_deps import require_login_user
    from backend.job_worker import client as worker_client
    from backend.job_worker import poll_ledger
    from backend.models import TaskStatus

    require_login_user()
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status not in (TaskStatus.failed,):
        if task.status in (TaskStatus.pending, TaskStatus.generating):
            return {"ok": True, "already_running": True, "task_id": task_id}
        raise HTTPException(400, "仅失败任务可重新检测")

    channel = str(getattr(task, "channel", None) or task.account_id or "").strip().lower()
    is_bendi = channel == "bendi" or task.account_id == "bendi"
    is_myapi = channel == "myapi" or task.account_id == "myapi"
    if is_bendi:
        raise HTTPException(410, "BenDi 已移除，请改用 MyApi")
    if not is_myapi:
        raise HTTPException(400, "重新检测仅支持保留了上游任务号的 MyApi 任务")

    rec = poll_ledger.get_record(task_id)
    upstream = str((rec or {}).get("upstream_task_id") or "").strip()
    if not upstream:
        raise HTTPException(
            400,
            "该 MyApi 任务没有可恢复的上游任务号，需要重新生成",
        )

    if str((rec or {}).get("state") or "") == "upstream_failed":
        raise HTTPException(400, "服务商已明确返回失败，请主动发起下一次生成")
    try:
        await worker_client.require_worker()
    except worker_client.WorkerUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc

    claimed, started = task_manager.begin_recheck(task_id)
    if not started:
        return {"ok": True, "already_running": True, "task_id": task_id}

    try:
        await worker_client.recheck_job(task_id, poll_attempt=claimed.poll_attempt)
    except worker_client.WorkerUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, worker_client.WorkerJobRejected):
            task_manager.update_task(task_id, status=TaskStatus.failed, error=str(exc), poll_attempt=task.poll_attempt)
        # Unknown delivery outcome stays attached to this task; polling reconciles it.
        raise HTTPException(502, str(exc) or "重新检测响应未确认，请等待原任务状态同步") from exc

    return {"ok": True, "task_id": task_id, "upstream_task_id": upstream}


@app.post("/api/tasks/{task_id}/user-action")
async def api_task_user_action(task_id: str, body: dict = Body(...)):
    """Respond to an in-flight browser prompt (e.g. paid-quota 继续 / 跳过)."""
    from backend.auth_deps import require_login_user

    require_login_user()
    action = str((body or {}).get("action") or "").strip().lower()
    if action not in ("continue", "skip"):
        raise HTTPException(400, "action 须为 continue 或 skip")
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status not in (TaskStatus.pending, TaskStatus.generating):
        raise HTTPException(400, "任务已结束")
    kind = str(getattr(task, "user_prompt_kind", None) or "").strip()
    existing = str(getattr(task, "user_action", None) or "").strip().lower()
    if kind != "paid_quota_confirm":
        # Idempotent: worker may have already consumed the prompt after our first write.
        if existing == action:
            return {"ok": True, "task_id": task_id, "action": action, "already": True}
        raise HTTPException(400, "当前任务无需此操作")
    # Keep user_action for the worker waiter; clear kind immediately so poll cannot
    # resurrect the confirm UI after the client optimistically dismissed it.
    task_manager.update_task(
        task_id,
        user_action=action,
        user_prompt_kind=None,
        user_prompt_message=None,
    )
    return {"ok": True, "task_id": task_id, "action": action}


@app.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status not in (TaskStatus.pending, TaskStatus.generating):
        return {"ok": True, "task_id": task_id, "already_done": True}
    # Browser Doubao: after webpage send, generation cannot be aborted.
    if getattr(task, "prompt_sent", False) and task.account_id not in ("myapi", "bendi"):
        try:
            from backend import account_manager as _am
            from browser_channels.bytedance_site import is_bytedance_provider

            acc = _am.get_account(task.account_id)
            prov = _am.normalize_provider(acc.provider if acc else "doubao")
            if is_bytedance_provider(prov):
                raise HTTPException(
                    400,
                    "消息已发送到渠道网页，无法暂停（网页侧会继续生成）",
                )
        except HTTPException:
            raise
        except Exception:
            pass
    # MyApi + browser-sim both run on 8089 now.
    from backend.job_worker import client as worker_client
    from backend import points

    await worker_client.cancel_job(task_id)
    # Always stop the canvas task once past the prompt_sent guard — even if the
    # worker cancel RPC fails, leave the task terminal so polling cannot revive UI.
    await task_runtime.cancel(task_id)
    points.refund(task_id, reason="cancelled")
    _mark_task_paused(task_id, myapi=task.account_id in ("myapi", "bendi"))
    return {"ok": True, "task_id": task_id, "was_running": True}


@app.post("/api/internal/job-worker/callback")
async def api_job_worker_callback(
    body: dict = Body(...),
    token: str | None = Header(default=None, alias="X-Job-Worker-Token"),
):
    from backend import service_settings
    from backend.job_worker.callback import apply_worker_callback

    if not service_settings.verify_job_worker_token(token):
        raise HTTPException(403, "无效的任务服务令牌")
    return apply_worker_callback(body)


@app.get("/api/video/tasks/{task_id}")
def api_get_video_task(task_id: str):
    return api_get_task(task_id)


@app.get("/api/image/tasks/{task_id}")
def api_get_image_task(task_id: str):
    return api_get_task(task_id)


# ── Canvas (per-user + explicit share) ────────────────────


@app.get("/api/canvas")
def api_get_canvas():
    from backend.auth_deps import require_login_user
    from backend import canvas_store
    from backend.storage_epoch import with_storage_epoch

    user = require_login_user()
    return with_storage_epoch(canvas_store.load_for_user(user["id"]), user["id"])


@app.put("/api/canvas")
def api_save_canvas(body: dict):
    from backend.auth_deps import require_login_user
    from backend import canvas_store
    from backend.storage_epoch import with_storage_epoch

    user = require_login_user()
    boards = body.get("boards")
    if not isinstance(boards, list):
        raise HTTPException(400, "boards 必须是数组")
    return with_storage_epoch(canvas_store.save_for_user(user["id"], boards), user["id"])


@app.patch("/api/canvas")
def api_save_canvas_changes(body: dict):
    from backend.auth_deps import require_login_user
    from backend.document_updates import save_changed
    from backend.storage_epoch import with_storage_epoch

    user = require_login_user()
    rows = body.get("boards")
    if not isinstance(rows, list):
        raise HTTPException(400, "boards 必须是数组")
    try:
        return with_storage_epoch(save_changed(user["id"], rows, kind="canvas"), user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/canvas/boards/{board_id}")
def api_delete_canvas_board(board_id: str):
    from backend.auth_deps import require_login_user

    user = require_login_user()
    if not canvas_store.remove_board(user["id"], board_id):
        raise HTTPException(404, "画板不存在")
    return {"ok": True, "boardId": board_id}


@app.post("/api/canvas/boards/{board_id}/purge-orphans")
def api_purge_board_orphans(board_id: str, body: dict = Body(default_factory=dict)):
    """Process queued node-delete media (3-day grace). Does not scan unrelated orphans."""
    from backend.auth_deps import require_login_user
    from backend import board_assets, canvas_store

    user = require_login_user()
    bid = (board_id or "").strip()
    if not bid:
        raise HTTPException(400, "board_id 无效")
    owned = canvas_store.load_for_user(user["id"])
    boards = owned.get("boards") if isinstance(owned, dict) else []
    meta = next((b for b in (boards or []) if isinstance(b, dict) and str(b.get("id")) == bid), None)
    if not meta:
        raise HTTPException(404, "画板不存在")
    if meta.get("isOwned") is False:
        raise HTTPException(403, "共享画板不可清理存储")
    board_payload = body.get("board") if isinstance(body, dict) else None
    if not isinstance(board_payload, dict) or str(board_payload.get("id") or "") != bid:
        board_payload = canvas_store.get_board(user["id"], bid)
    result = board_assets.process_pending_asset_deletes(
        user["id"], bid, board=board_payload if isinstance(board_payload, dict) else None
    )
    return {"ok": True, "boardId": bid, **result}

def _canvas_share_disabled():
    raise HTTPException(410, "共享画板已停用，请使用导入/导出")


@app.post("/api/canvas/boards/{board_id}/share")
def api_share_canvas_board(board_id: str, body: dict = Body(...)):
    del board_id, body
    _canvas_share_disabled()


@app.get("/api/canvas/boards/{board_id}/shares")
def api_list_canvas_board_shares(board_id: str):
    del board_id
    _canvas_share_disabled()


@app.post("/api/canvas/boards/{board_id}/share-link")
def api_enable_canvas_share_link(board_id: str):
    del board_id
    _canvas_share_disabled()


@app.delete("/api/canvas/boards/{board_id}/share-link")
def api_disable_canvas_share_link(board_id: str):
    del board_id
    _canvas_share_disabled()


@app.delete("/api/canvas/boards/{board_id}/shares/{username}")
def api_revoke_canvas_board_share(board_id: str, username: str):
    del board_id, username
    _canvas_share_disabled()


@app.post("/api/canvas/share-link/{token}/accept")
def api_accept_canvas_share_link(token: str):
    del token
    _canvas_share_disabled()


@app.post("/api/canvas/join")
def api_join_shared_canvas(body: dict = Body(...)):
    del body
    _canvas_share_disabled()


@app.get("/api/video/upscale/status")
async def api_upscale_status():
    """Whether Real-ESRGAN-ncnn-vulkan is available for video super-res."""
    from backend.auth_deps import require_login_user
    from backend.media.upscale_queue import worker_capacity_for_user, worker_status
    from backend.media.video_upscale import engine_status

    user = require_login_user()
    local = engine_status()
    remote = worker_status()
    capacity = worker_capacity_for_user(user["id"])
    return {
        **local,
        "remote": remote,
        "remote_enabled": bool(remote.get("remote_enabled")),
        "workers_online": int(remote.get("workers_online") or 0),
        "worker_capacity": capacity,
    }


@app.get("/api/upscale/jobs/lookup")
async def api_upscale_job_lookup(url: str = Query(...)):
    """Find latest upscale job for a source video URL (canvas recovery)."""
    from backend.auth_deps import require_login_user
    from backend.media.upscale_queue import find_job_for_source, job_public_view

    user = require_login_user()
    job = find_job_for_source(url, user_id=user["id"])
    if not job:
        raise HTTPException(404, "未找到对应超分任务")
    return job_public_view(job)


@app.get("/api/upscale/jobs/{job_id}")
async def api_upscale_job_status(job_id: str):
    """Poll remote upscale job status (canvas UI)."""
    from backend.auth_deps import require_login_user
    from backend.media.upscale_queue import get_job_for_user, job_public_view

    user = require_login_user()
    job = get_job_for_user(job_id, user["id"])
    if not job:
        raise HTTPException(404, "超分任务不存在")
    return job_public_view(job)


@app.api_route(
    "/api/upscale/worker/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def api_upscale_worker_proxy(path: str, request: Request):
    """Forward GPU worker API to local job worker (8089); keeps 10027 entry for legacy links."""
    from backend.job_worker.upscale_proxy import forward_upscale_worker_request

    return await forward_upscale_worker_request(request, path)


@app.post("/api/video/upscale/setup")
async def api_upscale_setup():
    """Download Real-ESRGAN portable engine if missing (~45MB)."""
    from backend.media.video_upscale import ensure_realesrgan, engine_status

    try:
        await ensure_realesrgan(download=True)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)[:300]) from exc
    return engine_status()


@app.post("/api/video/tools")
async def api_video_tools(body: VideoToolRequest):
    """Local non-AI video utilities (ffmpeg + optional Real-ESRGAN upscale)."""
    from backend.auth_deps import require_login_user

    _guard_media_url(body.url, workbench_id=body.workbench_id, board_id=body.board_id)
    user = require_login_user()
    from backend import board_assets

    board_token = board_assets.set_board_id(body.board_id)
    wb_token = board_assets.set_workbench_id(body.workbench_id)
    try:
        return await _dispatch_media_to_worker(
            user_id=str(user["id"]),
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            kind="video_tool",
            payload=body.tool_payload(),
            timeout=900.0,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc)
        if "不存在" in msg or "已丢失" in msg:
            raise HTTPException(404, msg[:300]) from exc
        raise HTTPException(500, msg[:300]) from exc
    finally:
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)


@app.post("/api/media/ensure-thumb")
async def api_ensure_thumb(body: dict = Body(...)):
    """Generate or return preview thumb / video poster for a local /uploads asset."""
    from backend import board_assets
    from backend.media.thumbnails import ensure_media_thumb

    url = str(body.get("url") or "").strip()
    kind = str(body.get("kind") or "image").strip().lower()
    if not url:
        raise HTTPException(400, "缺少 url")
    board_id = body.get("board_id")
    workbench_id = body.get("workbench_id") or body.get("workbenchId")
    _guard_media_url(url, workbench_id=workbench_id, board_id=board_id)
    board_token = board_assets.set_board_id(board_id)
    wb_token = board_assets.set_workbench_id(workbench_id)
    try:
        meta = await ensure_media_thumb(url, kind)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)[:300]) from exc
    finally:
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)
    return meta


@app.post("/api/media/ensure-browser-video")
async def api_ensure_browser_video(body: EnsureBrowserVideoRequest):
    """Normalize any local /uploads video to browser-playable H.264 MP4 when needed."""
    from backend.auth_deps import require_login_user

    _guard_media_url(body.url, workbench_id=body.workbench_id, board_id=body.board_id)
    user = require_login_user()
    try:
        return await _dispatch_media_to_worker(
            user_id=str(user["id"]),
            board_id=body.board_id,
            workbench_id=body.workbench_id,
            kind="browser_transcode",
            payload={"url": body.url, "force": body.force},
            timeout=900.0,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        msg = str(exc)
        if "不存在" in msg or "已丢失" in msg:
            raise HTTPException(404, msg[:300]) from exc
        raise HTTPException(500, msg[:300]) from exc


@app.post("/api/image/tools")
async def api_image_tools(body: ImageToolRequest):
    """Local non-AI image utilities (Pillow): crop / compress / mirror / rotate / AI mark."""
    from backend import board_assets
    from backend.auth_deps import require_login_user
    from backend.job_worker.media_runner import run_media_job

    _guard_media_url(body.url, workbench_id=body.workbench_id, board_id=body.board_id)
    user = require_login_user()
    board_token = board_assets.set_board_id(body.board_id)
    wb_token = board_assets.set_workbench_id(body.workbench_id)
    try:
        # Pillow ops are lightweight — run in-process to avoid 8089 queue / stale-job sweeps.
        return await run_media_job(
            {
                "user_id": str(user["id"]),
                "board_id": body.board_id,
                "workbench_id": body.workbench_id,
                "kind": "image_tool",
                "payload": body.tool_payload(),
            }
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)[:300]) from exc
    finally:
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)


@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),
    width: int | None = Form(None),
    height: int | None = Form(None),
    board_id: str | None = Form(None),
    workbench_id: str | None = Form(None),
):
    from backend import board_assets

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件过大（最大 200MB）")

    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()

    if content_type.startswith("image/") or ext in ALLOWED_IMAGE_EXT:
        kind = "image"
        default_ext = ".jpg"
    elif content_type.startswith("video/") or ext in ALLOWED_VIDEO_EXT:
        kind = "video"
        default_ext = ".mp4"
    elif content_type.startswith("audio/") or ext in ALLOWED_AUDIO_EXT:
        kind = "audio"
        default_ext = ".mp3"
    else:
        raise HTTPException(400, "仅支持图片、视频或音频文件")

    if kind == "image" and (not width or not height):
        try:
            img = Image.open(io.BytesIO(content))
            width, height = img.size
        except Exception:
            width = width or None
            height = height or None

    bid = (board_id or "").strip() or None
    token = board_assets.set_board_id(bid)
    try:
        out_path, result_url = board_assets.alloc_asset_path(ext or default_ext, board_id=bid)
    finally:
        board_assets.reset_board_id(token)
    out_path.write_bytes(content)

    if kind == "video":
        from backend.media.video_tools import finalize_saved_video

        saved = await finalize_saved_video(out_path, result_url)
        result_url = str(saved.get("video_url") or result_url)
        if saved.get("width"):
            width = saved["width"]
        if saved.get("height"):
            height = saved["height"]
        out_path = board_assets.resolve_upload_path(result_url) or out_path

    from backend.media.thumbnails import enrich_upload

    thumb_meta = await enrich_upload(kind, out_path)

    payload = {
        "url": result_url,
        "kind": kind,
        "width": width,
        "height": height,
        "filename": file.filename,
    }
    if thumb_meta.get("thumb_url"):
        payload["thumb_url"] = thumb_meta["thumb_url"]
    if thumb_meta.get("cover_url"):
        payload["cover_url"] = thumb_meta["cover_url"]
    return payload


@app.post("/api/import-url")
async def api_import_url(payload: dict = Body(...)):
    """Download a remote media URL into board uploads (Neowow paste / URL import)."""
    from backend import board_assets
    from backend.myapi.common import download_to_uploads

    url = str(payload.get("url") or "").strip()
    board_id = str(payload.get("board_id") or "").strip() or None
    workbench_id = str(payload.get("workbench_id") or payload.get("workbenchId") or "").strip() or None
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "无效 URL")
    board_token = board_assets.set_board_id(board_id)
    wb_token = board_assets.set_workbench_id(workbench_id)
    try:
        saved = await download_to_uploads(url, prefix="import")
        out_path = board_assets.resolve_upload_path(str(saved.get("url") or ""))
        kind = "image"
        url_l = url.lower().split("?")[0]
        if url_l.endswith((".mp4", ".webm", ".mov", ".mkv", ".m4v")) or "/video/" in url_l:
            kind = "video"
        elif url_l.endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")):
            kind = "audio"
        if out_path and out_path.is_file() and kind == "image":
            from backend.media.thumbnails import enrich_upload

            thumb_meta = await enrich_upload("image", out_path)
            saved.update({k: v for k, v in thumb_meta.items() if v})
        elif out_path and out_path.is_file() and kind == "video":
            from backend.media.thumbnails import enrich_upload

            thumb_meta = await enrich_upload("video", out_path)
            saved.update({k: v for k, v in thumb_meta.items() if v})
        saved["kind"] = kind
        return saved
    except Exception as exc:
        msg = str(exc)[:300]
        if "HTTP" in type(exc).__name__ or "Connect" in type(exc).__name__:
            raise HTTPException(502, f"下载失败: {msg}") from exc
        raise HTTPException(400, msg) from exc
    finally:
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)


# ── Static ────────────────────────────────────────────────


@app.api_route("/uploads/{path:path}", methods=["GET", "HEAD"])
def serve_upload(path: str, request: Request, mt: str | None = Query(None)):
    from backend import board_assets
    from backend.media_access import assert_upload_serve_allowed

    full_url = f"/uploads/{path}"
    resolved = board_assets.resolve_upload_path(full_url)
    if not resolved or not resolved.is_file():
        raise HTTPException(404, "文件不存在")
    assert_upload_serve_allowed(request, full_url, resolved, mt)
    from backend.media.range_response import media_file_response
    return media_file_response(resolved, request)


@app.get("/")
def index():
    return _frontend_page("index.html")


def _frontend_page(name: str):
    return FileResponse(
        FRONTEND_DIR / name,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/canvas")
@app.get("/canvas.html")
def canvas_page():
    return _frontend_page("canvas.html")


@app.get("/workbench")
@app.get("/workbench.html")
def workbench_page():
    return RedirectResponse("/", status_code=302)


if FRONTEND_DIR.exists():
    class _VersionedStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)
            if path.endswith((".js", ".css", ".woff2", ".woff", ".ico")):
                # Never immutable-cache JS/CSS: hotfixes (e.g. DnD) must apply after refresh.
                # Query ?v= still helps browsers pick a new URL after each build bump.
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            return response

    app.mount("/static", _VersionedStaticFiles(directory=FRONTEND_DIR), name="static")
