"""FastAPI app for local job worker (port 8089)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException

from backend.config import DATA_DIR, WORKER_PORT
from backend.job_worker import jobs as job_store
from backend.job_worker import runner
from backend.job_worker.upscale_routes import router as upscale_router
from backend import service_settings
from backend.user_scope import reset_current_user_id, set_current_user_id

logger = logging.getLogger(__name__)

_TOKEN_HEADER = "X-Job-Worker-Token"
_MYAPI_KINDS = frozenset({"video", "image", "text", "voice"})
_BROWSER_KINDS = frozenset({"browser_video", "browser_image", "browser_text"})
_MEDIA_KINDS = frozenset({"video_tool", "image_tool", "browser_transcode"})
_AGENT_KINDS = frozenset({"agent"})
_ALL_KINDS = _MYAPI_KINDS | _BROWSER_KINDS | _MEDIA_KINDS | _AGENT_KINDS


def _verify_token(token: str | None) -> None:
    if not service_settings.verify_job_worker_token(token):
        raise HTTPException(403, "无效的任务服务令牌")


def _job_age_seconds(job: dict) -> float:
    text = str(job.get("updated_at") or job.get("created_at") or "").strip()
    if not text:
        return 0.0
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(text)).total_seconds())
    except ValueError:
        return 0.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from backend.log_setup import setup_file_logging

    log_path = setup_file_logging(name="worker")
    service_settings.ensure_job_worker_token()
    runner.start_worker_background_loops()
    from backend.channel_gate import browser_channels_enabled

    logger.info(
        "job worker on port %s | data=%s | browser_channels=%s | log=%s",
        WORKER_PORT,
        DATA_DIR,
        browser_channels_enabled(),
        log_path,
    )
    yield
    try:
        from backend.channel_gate import browser_channels_enabled, get_pool

        if browser_channels_enabled():
            await get_pool().close_all()
    except Exception as exc:
        logger.debug("browser pool shutdown skipped: %s", exc)


worker_app = FastAPI(title="Canvas Job Worker", lifespan=lifespan)
worker_app.include_router(upscale_router)


@worker_app.get("/api/health")
def api_health():
    from backend.channel_gate import browser_channels_enabled

    return {
        "ok": True,
        "service": "job-worker",
        "port": WORKER_PORT,
        "browser_channels": browser_channels_enabled(),
    }


def _assert_browser_job_allowed(body: dict, *, kind: str, user_id: str) -> None:
    """Permission gate only (kept for callers that must not lease yet)."""
    from backend import app_users
    from backend.channel_gate import browser_channels_enabled
    from backend.user_roles import can_use_browser_channels

    if not browser_channels_enabled():
        raise HTTPException(
            403,
            {
                "code": "BROWSER_CHANNELS_DISABLED",
                "message": "任务服务仅支持 MyApi HTTP 任务",
            },
        )
    user = app_users.get_user(user_id)
    if not user or not can_use_browser_channels(user.get("role")):
        raise HTTPException(
            403,
            {
                "code": "BROWSER_CHANNELS_FORBIDDEN",
                "message": "Seed 渠道仅限管理员或内部人员使用，请改用 MyApi。",
            },
        )
    del body, kind


def _acquire_browser_account(body: dict, *, kind: str, user_id: str) -> tuple[str, str]:
    """Lease account on the worker process. Returns (account_id, account_name)."""
    from backend import account_manager, app_users
    from backend.channel_gate import browser_channels_enabled
    from backend.user_roles import can_use_browser_channels

    if not browser_channels_enabled():
        raise HTTPException(
            403,
            {
                "code": "BROWSER_CHANNELS_DISABLED",
                "message": "任务服务仅支持 MyApi HTTP 任务",
            },
        )
    user = app_users.get_user(user_id)
    if not user or not can_use_browser_channels(user.get("role")):
        raise HTTPException(
            403,
            {
                "code": "BROWSER_CHANNELS_FORBIDDEN",
                "message": "Seed 渠道仅限管理员或内部人员使用，请改用 MyApi。",
            },
        )

    media_kind = kind.replace("browser_", "", 1)
    provider = str(body.get("provider") or "doubao").strip() or "doubao"
    use_upgrade = bool(body.get("use_upgrade"))
    # Ordinary DouBao/Dola → regular pool; S → upgrade pool (live probe may switch).
    if account_manager.normalize_provider(provider) not in ("doubao", "dola"):
        tier = None
    elif use_upgrade:
        tier = "upgrade"
    else:
        tier = "regular"
    preferred = str(body.get("account_id") or "").strip() or None

    token = set_current_user_id(user_id)
    try:
        account, reason = account_manager.acquire_account(
            preferred,
            kind=media_kind,
            provider=provider,
            doubao_tier=tier,
        )
    finally:
        reset_current_user_id(token)

    if not account:
        code = "NO_ACCOUNT"
        message = "没有可用账号（可能未登录）。请先扫码登录后再试。"
        if reason == "all_busy":
            code = "ACCOUNT_BUSY"
            message = "当前用户并行已满，请稍候（本地浏览器窗口数已达上限）"
        elif reason == "browsers_full":
            code = "BROWSERS_FULL"
            message = "本机浏览器已达上限，正在排队等待空闲窗口"
        elif reason == "video_quota_exhausted":
            code = "VIDEO_QUOTA_EXHAUSTED"
            message = "今日额度已满，请次日尝试"
        elif reason == "no_regular_tier":
            code = "NO_REGULAR_ACCOUNT"
            message = (
                "没有可用的 Seed 普通账号。当前在线账号均为升级套餐（如标准套餐），"
                "请改选 Seed S 再生成。"
            )
        elif reason == "no_upgrade_tier":
            code = "NO_UPGRADE_ACCOUNT"
            message = (
                "没有可用的 Seed S 升级套餐账号。"
                "请改选 Seed，或先检测/登录有套餐的账号。"
            )
        logger.warning(
            "browser job reject user=%s kind=%s provider=%s upgrade=%s reason=%s",
            user_id,
            kind,
            provider,
            use_upgrade,
            reason,
        )
        raise HTTPException(503, {"code": code, "message": message, "reason": reason})
    return account.id, account.name


@worker_app.post("/api/jobs")
async def api_submit_job(
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    token: str | None = Header(default=None, alias=_TOKEN_HEADER),
):
    _verify_token(token)
    task_id = str(body.get("task_id") or body.get("id") or "").strip()
    if not task_id:
        raise HTTPException(400, "task_id required")
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(400, "user_id required")
    kind = str(body.get("kind") or "video").strip().lower()
    if kind not in _ALL_KINDS:
        raise HTTPException(400, "invalid kind")

    existing = job_store.get_job(task_id)
    if existing:
        st = str(existing.get("status") or "")
        age = _job_age_seconds(existing)
        if st == "pending" and age >= 20 and not runner.is_running(task_id):
            background_tasks.add_task(runner.enqueue, task_id)
            logger.info("duplicate submit re-enqueue job=%s age=%.0fs", task_id[:12], age)
        return {"ok": True, "job": job_store.public_view(existing), "duplicate": True}

    if kind in _AGENT_KINDS:
        job = job_store.upsert_job(
            {
                "id": task_id,
                "task_id": task_id,
                "user_id": user_id,
                "board_id": body.get("board_id"),
                "workbench_id": body.get("workbench_id") or body.get("workbenchId"),
                "kind": kind,
                "channel": "agent",
                "model": body.get("model"),
                "payload": body.get("payload") if isinstance(body.get("payload"), dict) else {},
                "needs_callback": False,
                "status": "pending",
                "error": None,
                "result": {},
                "callback_delivered": False,
                "completed_at": None,
            }
        )
    elif kind in _MEDIA_KINDS:
        job = job_store.upsert_job(
            {
                "id": task_id,
                "task_id": task_id,
                "user_id": user_id,
                "board_id": body.get("board_id"),
                "workbench_id": body.get("workbench_id") or body.get("workbenchId"),
                "kind": kind,
                "payload": body.get("payload") if isinstance(body.get("payload"), dict) else {},
                "needs_callback": bool(body.get("needs_callback")),
                "status": "pending",
                "error": None,
                "result": {},
                "callback_delivered": False,
                "completed_at": None,
            }
        )
    elif kind in _BROWSER_KINDS:
        account_id, account_name = _acquire_browser_account(body, kind=kind, user_id=user_id)
        job = job_store.upsert_job(
            {
                "id": task_id,
                "task_id": task_id,
                "user_id": user_id,
                "board_id": body.get("board_id"),
                "workbench_id": body.get("workbench_id") or body.get("workbenchId"),
                "kind": kind,
                "channel": "browser",
                "account_id": account_id,
                "account_name": account_name,
                "provider": body.get("provider") or "doubao",
                "use_upgrade": bool(body.get("use_upgrade")),
                "doubao_tier": body.get("doubao_tier"),
                "model": body.get("model"),
                "prompt": body.get("prompt"),
                "ratio": body.get("ratio") or "自动",
                "duration": body.get("duration"),
                "resolution": body.get("resolution"),
                "style": body.get("style"),
                "references": body.get("references") or [],
                "reference_images": body.get("reference_images") or [],
                "status": "pending",
                "error": None,
                "result": {},
                "callback_delivered": False,
                "completed_at": None,
            }
        )
    else:
        channel = str(body.get("channel") or "myapi").strip().lower() or "myapi"
        job = job_store.upsert_job(
            {
                "id": task_id,
                "task_id": task_id,
                "user_id": user_id,
                "board_id": body.get("board_id"),
                "workbench_id": body.get("workbench_id") or body.get("workbenchId"),
                "kind": kind,
                "channel": channel,
                "model": body.get("model"),
                "prompt": body.get("prompt"),
                "ratio": body.get("ratio") or "自动",
                "duration": body.get("duration"),
                "resolution": body.get("resolution"),
                "style": body.get("style"),
                "references": body.get("references") or [],
                "reference_images": body.get("reference_images") or [],
                "status": "pending",
                "error": None,
                "result": {},
                "callback_delivered": False,
                "completed_at": None,
            }
        )
    # Start browser/MyApi work AFTER HTTP 200 is sent. Video Playwright setup can
    # otherwise block the event loop so canvas ReadTimeouts POST /api/jobs.
    background_tasks.add_task(runner.enqueue, task_id)
    return {"ok": True, "job": job_store.public_view(job)}


@worker_app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str, token: str | None = Header(default=None, alias=_TOKEN_HEADER)):
    _verify_token(token)
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"ok": True, "job": job_store.public_view(job)}


@worker_app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str, token: str | None = Header(default=None, alias=_TOKEN_HEADER)):
    _verify_token(token)
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    st = str(job.get("status") or "")
    if st in ("completed", "failed", "cancelled"):
        return {"ok": True, "already_done": True}
    kind = str(job.get("kind") or "")
    runner.request_cancel(job_id)
    job_store.update_job(job_id, status="cancelled", error="已手动暂停")
    if not job.get("needs_callback") and kind in _MEDIA_KINDS:
        return {"ok": True}
    await runner.deliver_callback(job_id)
    return {"ok": True}


@worker_app.post("/api/jobs/{job_id}/recheck")
async def api_recheck_job(job_id: str, body: dict = Body(default={}), token: str | None = Header(default=None, alias=_TOKEN_HEADER)):
    """Resume wait/download for an existing upstream task (BenDi / MyApi)."""
    _verify_token(token)
    ok = await runner.enqueue_failed_recheck(job_id, poll_attempt=int(body.get("poll_attempt") or 0))
    if not ok:
        raise HTTPException(
            400,
            "无法重新检测：缺少上游任务号，或任务仍在运行。",
        )
    return {"ok": True, "job_id": job_id}
