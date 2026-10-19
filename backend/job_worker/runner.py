"""Execute MyApi / browser-sim jobs on 8089 and deliver callbacks to canvas (8088)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Any

import httpx

from backend.job_worker.failure_audit import failure_audit_fields
from backend.config import BASE_DIR, DATA_DIR, INTERNAL_CANVAS_URL
from backend.job_worker import jobs as job_store
from backend.job_worker import poll_ledger
from backend import board_assets, service_settings
from backend.user_scope import reset_current_user_id, set_current_user_id

logger = logging.getLogger(__name__)

_running: set[str] = set()
_cancelled: set[str] = set()
_retry_task: asyncio.Task | None = None

_TOKEN_HEADER = "X-Job-Worker-Token"
_CALLBACK_RETRY_INTERVAL_S = 8.0
_POLL_RECLAIM_EVERY_N = 2  # every N callback ticks ≈ 16s, plus startup
_BROWSER_KINDS = frozenset({"browser_video", "browser_image", "browser_text"})
_MEDIA_KINDS = frozenset({"video_tool", "image_tool", "browser_transcode"})
_AGENT_KINDS = frozenset({"agent"})


def is_browser_sim_job(job: dict[str, Any] | None) -> bool:
    """Doubao/GPT Playwright jobs — kind may be rewritten to video|image|text while running."""
    if not job:
        return False
    kind = str(job.get("kind") or "").strip().lower()
    if kind in _BROWSER_KINDS or kind.startswith("browser_"):
        return True
    return str(job.get("channel") or "").strip().lower() == "browser"


def is_local_worker_job(job: dict[str, Any] | None) -> bool:
    """Jobs that wait on browser automation or local ffmpeg — no MyApi upstream poll."""
    if not job:
        return False
    if is_browser_sim_job(job):
        return True
    return str(job.get("kind") or "") in _MEDIA_KINDS


def _keep_failed_poll_ledger(job_id: str, channel: str) -> bool:
    """Keep tasks that can be rechecked without charging for a second submit."""
    rec = poll_ledger.get_record(job_id)
    if not rec or not str(rec.get("upstream_task_id") or "").strip():
        return False
    return channel in ("bendi", "myapi", "volcengine") or str(rec.get("state") or "") in ("result_ready", "query_failed", "upstream_failed")


# Browser/media can run long (video often 5–10+ min). Still fail orphans / hard ceilings
# so a closed Chromium or dead worker cannot leave nodes "生成中" forever.
_LOCAL_PENDING_ORPHAN_S = 90
_LOCAL_PROCESSING_ORPHAN_S = 180  # processing but not in _running (worker lost the task)
_LOCAL_IMAGE_HARD_CEILING_S = 10 * 60
_LOCAL_VIDEO_HARD_CEILING_S = 60 * 60
_LOCAL_MEDIA_HARD_CEILING_S = 30 * 60


def local_worker_hard_ceiling_s(job: dict[str, Any] | None) -> float:
    """Absolute max age for a browser/media job before forced failure."""
    if not job:
        return float(_LOCAL_IMAGE_HARD_CEILING_S)
    kind = str(job.get("kind") or "").strip().lower()
    if "video" in kind:
        return float(_LOCAL_VIDEO_HARD_CEILING_S)
    if kind in _MEDIA_KINDS or "tool" in kind or "transcode" in kind:
        return float(_LOCAL_MEDIA_HARD_CEILING_S)
    return float(_LOCAL_IMAGE_HARD_CEILING_S)


def cancel_check(job_id: str):
    def _check() -> bool:
        if job_id in _cancelled:
            return True
        job = job_store.get_job(job_id)
        st = str(job.get("status") or "") if job else ""
        return st in ("cancelled", "failed")

    return _check


def request_cancel(job_id: str) -> None:
    _cancelled.add(job_id)
    job_store.update_job(job_id, status="cancelled", error="已手动暂停")


def is_running(job_id: str) -> bool:
    return (job_id or "").strip() in _running


def fail_stale_job(
    job_id: str,
    error: str,
    *,
    raw_error: str | None = None,
    force: bool = False,
) -> None:
    """Mark a worker job failed and push callback (used by stale reconcilers).

    By default skips browser/media jobs still actively running. Pass force=True for
    orphan reclaim or hard ceiling (closed browser / hung Playwright).
    """
    jid = (job_id or "").strip()
    if not jid:
        return
    job = job_store.get_job(jid)
    if not job:
        return
    st = str(job.get("status") or "")
    if st in ("completed", "failed", "cancelled"):
        return
    # Never timeout-fail a job that already has usable media in result.
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if (
        result.get("video_url")
        or result.get("url")
        or result.get("image_url")
        or (isinstance(result.get("text"), str) and result.get("text").strip())
    ):
        job_store.update_job(
            jid,
            status="completed",
            error=None,
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
        )
        poll_ledger.delete_record(jid)
        return
    if not force:
        if is_local_worker_job(job):
            return
        if jid in _running:
            return
    still_running = jid in _running
    if force and still_running:
        # Signal in-flight runner/child to stop; leave _running for its finally cleanup.
        _cancelled.add(jid)
    audit = {"error": error}
    if raw_error:
        audit["raw_error"] = raw_error
    job_store.update_job(
        jid,
        status="failed",
        completed_at=datetime.now().isoformat(),
        callback_delivered=False,
        **audit,
    )
    poll_ledger.delete_record(jid)
    if still_running and force:
        return
    _running.discard(jid)
    _cancelled.discard(jid)


async def enqueue(job_id: str) -> None:
    """Accept a job without blocking the 8089 event loop."""
    jid = (job_id or "").strip()
    if not jid:
        return
    if jid in _running:
        job = job_store.get_job(jid)
        st = str(job.get("status") or "") if job else ""
        if st in ("pending", "processing"):
            logger.debug("job worker enqueue skip (already running) job=%s", jid[:12])
        return
    _running.add(jid)

    async def _guarded() -> None:
        try:
            await _run_job(jid)
        except Exception as exc:
            logger.exception("job worker enqueue crashed job=%s", jid)
            job = job_store.get_job(jid)
            st = str(job.get("status") or "") if job else ""
            if job and st in ("pending", "processing"):
                audit = failure_audit_fields(exc)
                err_msg = audit.pop("error", str(exc) or "任务执行异常")
                job_store.update_job(
                    jid,
                    status="failed",
                    error=err_msg,
                    completed_at=datetime.now().isoformat(),
                    callback_delivered=False,
                    **audit,
                )
                poll_ledger.delete_record(jid)
                try:
                    await deliver_callback(jid)
                except Exception:
                    logger.exception("callback after enqueue crash job=%s", jid)
            _running.discard(jid)

    asyncio.create_task(_guarded(), name=f"job-worker-{jid}")


async def enqueue_poll_resume(job_id: str) -> None:
    """Resume an open poll ledger without re-POSTing create to upstream."""
    jid = (job_id or "").strip()
    if not jid or jid in _running:
        return
    if not poll_ledger.get_record(jid):
        return
    _running.add(jid)
    asyncio.create_task(_run_poll_resume(jid), name=f"job-worker-poll-resume-{jid}")


async def enqueue_failed_recheck(job_id: str, *, poll_attempt: int = 0) -> bool:
    """Reset a failed BenDi/MyApi job and resume upstream poll (no re-submit)."""
    jid = (job_id or "").strip()
    if not jid:
        return False
    rec = poll_ledger.get_record(jid)
    if not rec or not str(rec.get("upstream_task_id") or "").strip():
        return False
    job = job_store.get_job(jid)
    if not job:
        # Recreate a minimal job shell from the poll ledger.
        job_store.upsert_job(
            {
                "id": jid,
                "task_id": jid,
                "user_id": str(rec.get("user_id") or ""),
                "board_id": str(rec.get("board_id") or "") or None,
                "workbench_id": str(rec.get("workbench_id") or "") or None,
                "kind": str(rec.get("kind") or "video"),
                "channel": str(rec.get("channel") or rec.get("style_id") or "bendi"),
                "model": str(rec.get("model") or ""),
                "status": "processing",
                "poll_attempt": poll_attempt,
                "error": None,
                "result": {},
                "needs_callback": True,
                "callback_delivered": False,
                "completed_at": None,
            }
        )
    else:
        st = str(job.get("status") or "")
        current_attempt = int(job.get("poll_attempt") or 0)
        if poll_attempt < current_attempt:
            return False
        if poll_attempt == current_attempt and st in ("pending", "processing") and jid in _running:
            return True
        if st not in ("failed", "cancelled", "processing", "pending"):
            return False
        if jid in _running:
            return False
        job_store.update_job(
            jid,
            status="processing",
            allow_recheck=True,
            poll_attempt=poll_attempt,
            error=None,
            completed_at=None,
            callback_delivered=False,
            result={},
        )
    if jid in _running:
        return False
    poll_ledger.update_record(jid, manual_recheck=True)
    await enqueue_poll_resume(jid)
    return True


async def _release_browser_lease(job: dict[str, Any], *, close_browser: bool = False) -> None:
    try:
        from backend import account_manager
        from backend.channel_gate import browser_channels_enabled, get_pool

        aid = str(job.get("account_id") or "").strip()
        if aid:
            account_manager.release_account(aid)
        if close_browser and browser_channels_enabled() and aid:
            try:
                await get_pool().close_context(aid)
            except Exception:
                pass
    except Exception as cleanup_exc:
        logger.warning("browser cleanup after job: %s", cleanup_exc)


def _should_close_browser_after_fail(job: dict[str, Any]) -> bool:
    blob = f"{job.get('error') or ''}\n{job.get('raw_error') or ''}".lower()
    if not blob.strip():
        return False
    if any(
        n in blob
        for n in (
            "has been closed",
            "target closed",
            "browser has been closed",
            "browser closed",
            "登录态同步失败",
            "未登录",
            "session 已失效",
        )
    ):
        return True
    try:
        from backend import account_manager

        full = f"{job.get('error') or ''}\n{job.get('raw_error') or ''}"
        if account_manager.is_video_quota_exhausted_error(full):
            return True
        if account_manager.is_quota_exhausted_error(full):
            return True
    except Exception:
        pass
    return False


async def _run_browser_via_subprocess(job_id: str, job: dict[str, Any]) -> None:
    """Browser simulation was removed; reject leftover browser jobs."""
    del job
    raise RuntimeError("浏览器模拟已移除，请改用 MyApi。")




async def _run_agent_job(job_id: str, job: dict[str, Any]) -> None:
    """Run 小庆子 chat_completion on 8089 so canvas can poll across refresh."""
    from backend import myapi_store, public_models_store
    from backend.agent import chat_completion
    from backend.agent.chat import CHAT_JOB_BUDGET_SECONDS

    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    model_id = str(payload.get("model_id") or job.get("model") or "").strip()

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
            raise RuntimeError("所选智能体模型已删除或停用，请刷新模型列表后重新选择")
    if not cfg:
        agents = [m for m in myapi_store.list_models("agent") if m.get("enabled", True)]
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
        raise RuntimeError("请先在 MyApi → 智能体 添加模型，或开启火山引擎文本并勾选「用于智能体」")

    message = str(payload.get("message") or "").strip()
    if not message:
        raise RuntimeError("message 不能为空")
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    memory = str(payload.get("memory") or "").strip()
    canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else None
    light_raw = payload.get("light")
    light_flag = None if light_raw is None else bool(light_raw)

    if cancel_check(job_id)():
        raise _Cancelled("已手动暂停")

    plan = await chat_completion(
        model_cfg=cfg,
        message=message,
        history=history,
        memory=memory,
        canvas=canvas,
        user_intent=str(payload.get("user_intent") or message),
        role_mode=str(payload.get("role_mode") or "agent"),
        exec_mode=str(payload.get("exec_mode") or "confirm"),
        light=light_flag,
        budget_seconds=CHAT_JOB_BUDGET_SECONDS,
    )
    if cancel_check(job_id)():
        raise _Cancelled("已手动暂停")

    result = {
        "thinking": plan.get("thinking") or "",
        "reply": plan.get("reply") or "",
        "actions": plan.get("actions") or [],
        "choices": plan.get("choices") or [],
        "decision": plan.get("decision") or "",
        "diagnostics": plan.get("diagnostics") or {},
        "model_id": cfg.get("id"),
        "multimodal": bool(cfg.get("multimodal")),
        "vision_model_id": cfg.get("vision_model_id") or "",
        "vision_model": cfg.get("vision_model") or "",
        "public": bool(str(cfg.get("id") or "").startswith("public_")),
    }
    job_store.update_job(
        job_id,
        status="completed",
        result=result,
        error=None,
        completed_at=datetime.now().isoformat(),
        callback_delivered=True,
    )


async def _run_job(job_id: str) -> None:
    # If a poll ledger already exists (e.g. duplicate submit after restart), resume poll only.
    if poll_ledger.get_record(job_id):
        await _run_poll_resume(job_id)
        return

    job = job_store.get_job(job_id)
    if not job:
        _running.discard(job_id)
        return
    job_store.update_job(job_id, status="processing")
    kind = str(job.get("kind") or "video")
    if kind not in _MEDIA_KINDS and kind not in _AGENT_KINDS:
        await _deliver_progress(job)

    user_id = str(job.get("user_id") or "").strip()
    board_id = str(job.get("board_id") or "").strip() or None
    workbench_id = str(job.get("workbench_id") or "").strip() or None
    user_token = set_current_user_id(user_id or None)
    board_token = board_assets.set_board_id(board_id)
    wb_token = board_assets.set_workbench_id(workbench_id)
    kind = str(job.get("kind") or "video")
    try:
        if job_id in _cancelled:
            if kind in _BROWSER_KINDS:
                aid = str(job.get("account_id") or "").strip()
                if aid:
                    from backend import account_manager

                    account_manager.release_account(aid)
            raise _Cancelled("已手动暂停")

        if kind in _AGENT_KINDS:
            try:
                await _run_agent_job(job_id, job)
            except _Cancelled:
                raise
            except Exception as exc:
                from backend.agent.chat import format_agent_call_error

                err = format_agent_call_error(exc)
                logger.exception("agent job failed job=%s: %s", job_id[:12], err)
                job_store.update_job(
                    job_id,
                    status="failed",
                    error=err,
                    completed_at=datetime.now().isoformat(),
                    callback_delivered=True,
                )
            return

        if kind in _MEDIA_KINDS:
            from backend.job_worker.media_runner import run_media_job

            result = await run_media_job(job)
            if job_id in _cancelled:
                raise _Cancelled("已手动暂停")
            job_store.update_job(
                job_id,
                status="completed",
                result=result if isinstance(result, dict) else {"value": result},
                error=None,
                completed_at=datetime.now().isoformat(),
                callback_delivered=False,
            )
            if job.get("needs_callback"):
                await deliver_callback(job_id)
            return

        if kind in _BROWSER_KINDS:
            # Isolate Playwright in a child process — keeps 8089 free for concurrent submits.
            await _run_browser_via_subprocess(job_id, job)
            return

        from backend.prompt_refs import sanitize_generation_prompt

        prompt = sanitize_generation_prompt(str(job.get("prompt") or ""))
        channel = str(job.get("channel") or "myapi").strip().lower()

        if channel == "bendi":
            raise RuntimeError("BenDi 已移除，请改用 MyApi")
        elif channel == "volcengine":
            from backend.volcengine.client import run_for_model as volc_run

            if kind == "video":
                result = await volc_run(
                    str(job.get("model") or ""),
                    kind="video",
                    prompt=prompt,
                    ratio=str(job.get("ratio") or "自动"),
                    duration=job.get("duration"),
                    resolution=job.get("resolution"),
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "video_url": result.get("video_url"),
                    "cover_url": result.get("cover_url") or "",
                    "duration": result.get("duration") or 0,
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            elif kind == "image":
                result = await volc_run(
                    str(job.get("model") or ""),
                    kind="image",
                    prompt=prompt,
                    ratio=str(job.get("ratio") or "自动"),
                    resolution=job.get("resolution"),
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "url": result.get("url"),
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            elif kind == "text":
                result = await volc_run(
                    str(job.get("model") or ""),
                    kind="text",
                    prompt=prompt,
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                if isinstance(result, dict):
                    payload_result = {
                        "text": result.get("text") or "",
                        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
                    }
                else:
                    payload_result = {"text": str(result or ""), "usage": {}}
            elif kind == "voice":
                result = await volc_run(
                    str(job.get("model") or ""),
                    kind="voice",
                    prompt=prompt,
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "audio_url": result.get("audio_url") or result.get("url") or "",
                    "duration": result.get("duration") or 0,
                }
            else:
                raise RuntimeError(f"火山引擎不支持 kind={kind}")
        else:
            from backend.myapi import client as myapi_client

            if kind == "video":
                result = await myapi_client.run_for_model(
                    str(job.get("model") or ""),
                    kind="video",
                    prompt=prompt,
                    ratio=str(job.get("ratio") or "自动"),
                    duration=job.get("duration"),
                    resolution=job.get("resolution"),
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "video_url": result.get("video_url"),
                    "cover_url": result.get("cover_url") or "",
                    "duration": result.get("duration") or 0,
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            elif kind == "image":
                result = await myapi_client.run_for_model(
                    str(job.get("model") or ""),
                    kind="image",
                    prompt=prompt,
                    ratio=str(job.get("ratio") or "自动"),
                    resolution=job.get("resolution"),
                    style=job.get("style"),
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "url": result.get("url"),
                    "width": result.get("width"),
                    "height": result.get("height"),
                }
            elif kind == "text":
                result = await myapi_client.run_for_model(
                    str(job.get("model") or ""),
                    kind="text",
                    prompt=prompt,
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                )
                if isinstance(result, dict):
                    payload_result = {
                        "text": result.get("text") or "",
                        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
                    }
                else:
                    payload_result = {"text": str(result or ""), "usage": {}}
            elif kind == "voice":
                result = await myapi_client.run_for_model(
                    str(job.get("model") or ""),
                    kind="voice",
                    prompt=prompt,
                    references=job.get("references"),
                    reference_images=job.get("reference_images"),
                    cancel_check=cancel_check(job_id),
                    client_task_id=job_id,
                )
                payload_result = {
                    "audio_url": result.get("audio_url") or result.get("url") or "",
                    "duration": result.get("duration") or 0,
                }
            else:
                raise RuntimeError(f"不支持的 kind: {kind}")

        if job_id in _cancelled:
            raise _Cancelled("已手动暂停")

        job_store.update_job(
            job_id,
            status="completed",
            result=payload_result,
            error=None,
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
        )
        poll_ledger.delete_record(job_id)
        await _maybe_deliver_callback(job_id)
    except _Cancelled as exc:
        # Ensure callback kind is canvas media kind for browser jobs.
        media_kind = kind.replace("browser_", "", 1) if kind in _BROWSER_KINDS else kind
        job_store.update_job(
            job_id,
            status="cancelled",
            kind=media_kind,
            error=str(exc),
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
        )
        poll_ledger.delete_record(job_id)
        if kind in _BROWSER_KINDS:
            await _release_browser_lease(job, close_browser=False)
        await _maybe_deliver_callback(job_id)
    except asyncio.CancelledError:
        # Keep job + poll ledger so restart can reclaim.
        raise
    except Exception as exc:
        logger.exception("job worker %s failed", job_id)
        if getattr(exc, "upstream_terminal", False):
            poll_ledger.update_record(job_id, state="upstream_failed")
        audit = failure_audit_fields(exc)
        err_msg = audit.pop("error")
        media_kind = kind.replace("browser_", "", 1) if kind in _BROWSER_KINDS else kind
        job_store.update_job(
            job_id,
            status="failed",
            kind=media_kind,
            error=err_msg,
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
            **audit,
        )
        channel = str(job.get("channel") or "").strip().lower()
        # BenDi and completed-upstream/download-failed jobs can be rechecked
        # without submitting (and charging) a second generation.
        if not _keep_failed_poll_ledger(job_id, channel):
            poll_ledger.delete_record(job_id)
        await _maybe_deliver_callback(job_id)
        if kind in _BROWSER_KINDS:
            try:
                refreshed = job_store.get_job(job_id) or job
                await _release_browser_lease(
                    refreshed,
                    close_browser=_should_close_browser_after_fail(refreshed),
                )
            except Exception as cleanup_exc:
                logger.warning("browser cleanup after job fail: %s", cleanup_exc)
    finally:
        reset_current_user_id(user_token)
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)
        _running.discard(job_id)
        _cancelled.discard(job_id)


async def _run_poll_resume(job_id: str) -> None:
    """Finish an upstream poll that was interrupted by 8089 restart."""
    rec = poll_ledger.get_record(job_id)
    job = job_store.get_job(job_id)
    if not rec:
        _running.discard(job_id)
        return
    if not job:
        logger.warning("poll resume missing job=%s — keep upstream identity for recheck", job_id[:12])
        _running.discard(job_id)
        return

    st = str(job.get("status") or "")
    if st in ("completed", "failed", "cancelled"):
        # Failed BenDi keeps ledger for manual「重新检测」.
        channel = str(job.get("channel") or rec.get("channel") or rec.get("style_id") or "").lower()
        if st == "failed" and _keep_failed_poll_ledger(job_id, channel):
            _running.discard(job_id)
            return
        poll_ledger.delete_record(job_id)
        if not job.get("callback_delivered"):
            await deliver_callback(job_id)
        _running.discard(job_id)
        return

    poll_ledger.mark_reclaiming(job_id)
    poll_ledger.update_record(job_id, manual_recheck=False)
    job_store.update_job(job_id, status="processing", error=None)
    await _deliver_progress(job)

    user_id = str(job.get("user_id") or rec.get("user_id") or "").strip()
    board_id = str(job.get("board_id") or rec.get("board_id") or "").strip() or None
    workbench_id = str(job.get("workbench_id") or rec.get("workbench_id") or "").strip() or None
    user_token = set_current_user_id(user_id or None)
    board_token = board_assets.set_board_id(board_id)
    wb_token = board_assets.set_workbench_id(workbench_id)
    kind = str(rec.get("kind") or job.get("kind") or "video").strip().lower()
    channel = str(job.get("channel") or rec.get("channel") or rec.get("style_id") or "").strip().lower()
    try:
        if job_id in _cancelled:
            raise _Cancelled("已手动暂停")

        if channel == "bendi" or str(rec.get("style_id") or "") == "bendi":
            raise RuntimeError("BenDi 已移除，请改用 MyApi")
        else:
            from backend.myapi import client as myapi_client

            result = await myapi_client.resume_media_poll(rec, cancel_check=cancel_check(job_id))
        if job_id in _cancelled:
            raise _Cancelled("已手动暂停")

        if kind == "video":
            payload_result = {
                "video_url": result.get("video_url"),
                "cover_url": result.get("cover_url") or "",
                "duration": result.get("duration") or 0,
                "width": result.get("width"),
                "height": result.get("height"),
            }
        elif kind == "text":
            payload_result = {
                "text": result.get("text") or result.get("reply") or "",
                "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
            }
        else:
            payload_result = {
                "url": result.get("url"),
                "width": result.get("width"),
                "height": result.get("height"),
            }

        job_store.update_job(
            job_id,
            status="completed",
            result=payload_result,
            error=None,
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
        )
        poll_ledger.delete_record(job_id)
        await _maybe_deliver_callback(job_id)
        logger.info(
            "poll resume completed job=%s node=%s",
            job_id[:12],
            str(rec.get("node_id") or "-")[:12],
        )
    except _Cancelled as exc:
        job_store.update_job(
            job_id,
            status="cancelled",
            error=str(exc),
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
        )
        poll_ledger.delete_record(job_id)
        await _maybe_deliver_callback(job_id)
    except asyncio.CancelledError:
        # Keep ledger for next reclaim.
        raise
    except Exception as exc:
        logger.exception("poll resume %s failed", job_id)
        if getattr(exc, "upstream_terminal", False):
            poll_ledger.update_record(job_id, state="upstream_failed")
        audit = failure_audit_fields(exc)
        err_msg = audit.pop("error")
        job_store.update_job(
            job_id,
            status="failed",
            error=err_msg,
            completed_at=datetime.now().isoformat(),
            callback_delivered=False,
            **audit,
        )
        # Keep recoverable ledger for another「重新检测」.
        if not _keep_failed_poll_ledger(job_id, channel):
            poll_ledger.delete_record(job_id)
        await _maybe_deliver_callback(job_id)
    finally:
        reset_current_user_id(user_token)
        board_assets.reset_workbench_id(wb_token)
        board_assets.reset_board_id(board_token)
        _running.discard(job_id)


async def reclaim_open_polls() -> int:
    """Scan poll ledger and re-queue unfinished polls. Returns number enqueued."""
    enqueued = 0
    for rec in poll_ledger.list_records():
        job_id = str(rec.get("job_id") or "").strip()
        if not job_id:
            continue
        if job_id in _running:
            continue
        job = job_store.get_job(job_id)
        if not job:
            logger.info("poll ledger missing job=%s — keep for canvas recheck", job_id[:12])
            continue
        st = str(job.get("status") or "")
        if st in ("completed", "failed", "cancelled"):
            channel = str(job.get("channel") or rec.get("channel") or rec.get("style_id") or "").lower()
            # Keep recoverable ledgers for manual recheck.
            if st == "failed" and _keep_failed_poll_ledger(job_id, channel):
                continue
            poll_ledger.delete_record(job_id)
            if not job.get("callback_delivered"):
                await deliver_callback(job_id)
            continue
        # pending / processing with open ledger → resume poll (do not re-create)
        logger.info(
            "poll reclaim job=%s kind=%s upstream=%s node=%s state=%s",
            job_id[:12],
            rec.get("kind"),
            str(rec.get("upstream_task_id") or "")[:16],
            str(rec.get("node_id") or "-")[:12],
            rec.get("state"),
        )
        await enqueue_poll_resume(job_id)
        enqueued += 1
    return enqueued


class _Cancelled(Exception):
    pass


async def _deliver_progress(job: dict[str, Any], *, prompt_sent: bool = False) -> None:
    payload: dict[str, Any] = {
        "task_id": job.get("task_id") or job.get("id"),
        "poll_attempt": int(job.get("poll_attempt") or 0),
        "user_id": job.get("user_id"),
        "kind": job.get("kind"),
        "status": "generating",
    }
    if prompt_sent:
        payload["prompt_sent"] = True
    await _post_callback(payload)


async def notify_prompt_sent(job: dict[str, Any]) -> None:
    """Tell canvas the Doubao/Dola webpage send already happened (hide pause)."""
    try:
        await _deliver_progress(job, prompt_sent=True)
    except Exception as exc:
        logger.debug("notify_prompt_sent failed: %s", exc)


async def _maybe_deliver_callback(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if not job:
        return
    kind = str(job.get("kind") or "")
    if kind in ("video_tool", "image_tool", "browser_transcode") and not job.get("needs_callback"):
        return
    await deliver_callback(job_id)


async def deliver_callback(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if not job:
        return
    st = str(job.get("status") or "")
    if st == "cancelled":
        status = "cancelled"
    elif st == "completed":
        status = "completed"
    elif st == "failed":
        status = "failed"
    else:
        return
    outcome = await _post_callback(
        {
            "task_id": job.get("task_id") or job.get("id"),
            "poll_attempt": int(job.get("poll_attempt") or 0),
            "user_id": job.get("user_id"),
            "kind": job.get("kind"),
            "channel": job.get("channel"),
            "status": status,
            "result": job.get("result") or {},
            "error": job.get("error"),
            "raw_error": job.get("raw_error"),
            "raw_response": job.get("raw_response"),
            "error_type": job.get("error_type"),
            "upstream_task_id": job.get("upstream_task_id"),
            "upstream_status": job.get("upstream_status"),
            "upstream_stage": job.get("upstream_stage"),
            "last_poll_at": job.get("last_poll_at"),
            "account_id": job.get("account_id"),
            "account_name": job.get("account_name"),
        }
    )
    # ok = applied; give_up = canvas task gone — stop retrying either way
    if outcome in ("ok", "give_up"):
        job_store.update_job(job_id, callback_delivered=True, expected_poll_attempt=int(job.get("poll_attempt") or 0))


async def _post_callback(payload: dict[str, Any]) -> str:
    """Return 'ok' | 'retry' | 'give_up'."""
    url = f"{INTERNAL_CANVAS_URL.rstrip('/')}/api/internal/job-worker/callback"
    headers = {_TOKEN_HEADER: service_settings.ensure_job_worker_token()}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "job worker callback %s HTTP %s: %s",
                    payload.get("task_id"),
                    resp.status_code,
                    resp.text[:200],
                )
                return "retry"
            data = resp.json() if resp.content else {}
            if data.get("ok", True):
                return "ok"
            reason = str(data.get("reason") or "").strip()
            # Permanent: MediaTask was deleted / never existed — don't spin forever
            if reason in ("task_not_found",):
                logger.info(
                    "job worker callback %s abandoned (%s)",
                    payload.get("task_id"),
                    reason,
                )
                return "give_up"
            return "retry"
    except Exception as exc:
        logger.warning("job worker callback %s failed: %s", payload.get("task_id"), exc)
        return "retry"


async def _sweep_stale_jobs() -> None:
    """Re-enqueue orphaned pending jobs; fail jobs stuck too long."""
    from datetime import datetime

    now = datetime.now()

    def _age_sec(iso: str | None) -> float:
        text = str(iso or "").strip()
        if not text:
            return 0.0
        try:
            return max(0.0, (now - datetime.fromisoformat(text)).total_seconds())
        except ValueError:
            return 0.0

    jobs = job_store.list_open_jobs(limit=500)

    for row in jobs:
        jid = str(row.get("id") or "").strip()
        if not jid:
            continue
        st = str(row.get("status") or "")
        if st not in ("pending", "processing"):
            continue
        age = _age_sec(str(row.get("updated_at") or row.get("created_at") or ""))
        has_poll = bool(poll_ledger.get_record(jid))

        if st == "pending":
            if is_local_worker_job(row):
                if jid in _running:
                    continue
                if age >= _LOCAL_PENDING_ORPHAN_S:
                    msg = "任务排队超时（浏览器/本地任务可能已中断），请重新生成"
                    logger.info("stale pending browser/media job=%s age=%.0fs — fail", jid[:12], age)
                    fail_stale_job(jid, msg, raw_error=f"local pending orphan {int(age)}s", force=True)
                    await deliver_callback(jid)
                elif age >= 20:
                    logger.info("stale pending browser/media job=%s age=%.0fs — re-enqueue", jid[:12], age)
                    await enqueue(jid)
                continue
            if age >= 120:
                msg = "任务排队超时，未能提交到服务商"
                fail_stale_job(jid, msg)
                await deliver_callback(jid)
            elif age >= 20 and jid not in _running:
                logger.info("stale pending job=%s age=%.0fs — re-enqueue", jid[:12], age)
                await enqueue(jid)
            continue

        if st == "processing" and is_local_worker_job(row):
            ceiling = local_worker_hard_ceiling_s(row)
            total_age = _age_sec(str(row.get("created_at") or row.get("updated_at") or ""))
            if total_age >= ceiling:
                msg = (
                    "任务超时未返回结果（常见于自动化浏览器被关闭或进程中断），请重新生成"
                )
                logger.info(
                    "stale processing browser/media job=%s age=%.0fs ceiling=%.0fs — fail",
                    jid[:12],
                    age,
                    ceiling,
                )
                fail_stale_job(
                    jid,
                    msg,
                    raw_error=f"local processing hard ceiling {int(total_age)}s",
                    force=True,
                )
                await deliver_callback(jid)
            elif jid not in _running and age >= _LOCAL_PROCESSING_ORPHAN_S:
                msg = "任务执行中断（工作进程丢失），请重新生成"
                logger.info(
                    "stale processing browser/media job=%s age=%.0fs — orphan fail",
                    jid[:12],
                    age,
                )
                fail_stale_job(
                    jid,
                    msg,
                    raw_error=f"local processing orphan {int(age)}s not running",
                    force=True,
                )
                await deliver_callback(jid)
            continue

        if st == "processing" and not has_poll and age >= 180:
            # MyApi HTTP jobs without poll ledger — provider unreachable.
            if jid in _running:
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if (
                result.get("video_url")
                or result.get("url")
                or result.get("image_url")
                or (isinstance(result.get("text"), str) and str(result.get("text")).strip())
            ):
                job_store.update_job(
                    jid,
                    status="completed",
                    error=None,
                    completed_at=datetime.now().isoformat(),
                    callback_delivered=False,
                )
                poll_ledger.delete_record(jid)
                await deliver_callback(jid)
                continue
            kind = str(row.get("kind") or "").lower()
            # Match canvas reconcile: video download after poll can exceed 3 minutes.
            if "video" in kind and age < 20 * 60:
                continue
            msg = "任务执行超时，可能未提交到服务商"
            fail_stale_job(jid, msg, raw_error=f"processing {int(age)}s without upstream poll")
            await deliver_callback(jid)


async def _retry_loop() -> None:
    tick = 0
    # Startup reclaim as soon as the loop starts.
    try:
        n = await reclaim_open_polls()
        if n:
            logger.info("poll reclaim startup enqueued=%s", n)
    except Exception as exc:
        logger.warning("poll reclaim startup failed: %s", exc)

    while True:
        try:
            for job in job_store.list_pending_callbacks():
                jid = str(job.get("id") or "")
                if jid:
                    await deliver_callback(jid)
        except Exception as exc:
            logger.warning("job worker callback retry loop: %s", exc)

        tick += 1
        if tick % 2 == 0:
            try:
                await _sweep_stale_jobs()
            except Exception as exc:
                logger.warning("job worker stale sweep: %s", exc)
        if tick % _POLL_RECLAIM_EVERY_N == 0:
            try:
                n = await reclaim_open_polls()
                if n:
                    logger.info("poll reclaim periodic enqueued=%s", n)
            except Exception as exc:
                logger.warning("poll reclaim periodic failed: %s", exc)

        await asyncio.sleep(_CALLBACK_RETRY_INTERVAL_S)


def start_callback_retry_loop() -> None:
    global _retry_task
    if _retry_task and not _retry_task.done():
        return
    _retry_task = asyncio.create_task(_retry_loop(), name="job-worker-callback-retry")


def start_worker_background_loops() -> None:
    """Start callback retry + poll reclaim (call from worker lifespan)."""
    start_callback_retry_loop()
    # Also kick an immediate reclaim task (before first sleep of retry loop).
    asyncio.create_task(_startup_reclaim(), name="job-worker-poll-reclaim-startup")


async def _startup_reclaim() -> None:
    try:
        await asyncio.sleep(0.5)
        n = await reclaim_open_polls()
        if n:
            logger.info("poll reclaim immediate enqueued=%s", n)
    except Exception as exc:
        logger.warning("poll reclaim immediate failed: %s", exc)
