"""Upscale queue + GPU worker API on port 8089."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile

from backend.config import UPLOADS_DIR
from backend.media.video_tools import UPLOAD_VIDEO_EXTENSIONS

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
ALLOWED_VIDEO_EXT = UPLOAD_VIDEO_EXTENSIONS

router = APIRouter()


@router.get("/api/upscale/worker/status")
def api_upscale_worker_status(
    token: str | None = Query(None),
    user_scope: str | None = Query(None),
):
    from backend.media.upscale_queue import verify_worker_token, worker_status

    if not verify_worker_token(token, user_scope=user_scope):
        raise HTTPException(403, "Worker token 无效")
    return worker_status()


@router.post("/api/upscale/worker/heartbeat")
async def api_upscale_worker_heartbeat(body: dict = Body(...)):
    from backend.media.upscale_queue import register_worker, worker_status

    token = body.get("token")
    worker_id = str(body.get("worker_id") or "worker")
    user_scope = str(body.get("user_scope") or body.get("username") or "").strip() or None
    max_concurrent = int(body.get("max_concurrent") or 1)
    running = int(body.get("running") or 0)
    try:
        register_worker(
            worker_id,
            max_concurrent=max_concurrent,
            running=running,
            token=token,
            user_scope=user_scope,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    snap = worker_status()
    return {
        "ok": True,
        "queue_pending": int(snap.get("queue_pending") or 0),
        "queue_active": int(snap.get("queue_active") or 0),
    }


@router.post("/api/upscale/worker/claim")
async def api_upscale_worker_claim(body: dict = Body(...)):
    from backend.media.upscale_queue import claim_job

    token = body.get("token")
    worker_id = str(body.get("worker_id") or "worker")
    user_scope = str(body.get("user_scope") or body.get("username") or "").strip() or None
    max_concurrent = int(body.get("max_concurrent") or 1)
    running = int(body.get("running") or 0)
    try:
        job = claim_job(
            worker_id,
            max_concurrent=max_concurrent,
            running=running,
            token=token,
            user_scope=user_scope,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not job:
        return {"job": None}
    return {"job": job}


@router.get("/api/upscale/worker/jobs/{job_id}/source")
async def api_upscale_worker_job_source(
    job_id: str,
    worker_id: str = Query(...),
    token: str | None = Query(None),
    user_scope: str | None = Query(None),
):
    from fastapi.responses import FileResponse

    from backend.media.upscale_queue import (
        get_job,
        resolve_job_source_path,
        verify_job_worker,
        verify_worker_token,
    )

    if not verify_worker_token(token, user_scope=user_scope):
        raise HTTPException(403, "Worker token 无效")
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.get("status") not in ("claimed", "processing"):
        raise HTTPException(409, "任务状态不可下载")
    try:
        verify_job_worker(job, worker_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    try:
        path = await resolve_job_source_path(job)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)[:300]) from exc
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/api/upscale/worker/jobs/{job_id}/processing")
async def api_upscale_worker_job_processing(job_id: str, body: dict = Body(...)):
    from backend.media.upscale_queue import mark_processing

    token = body.get("token")
    worker_id = str(body.get("worker_id") or "worker")
    user_scope = str(body.get("user_scope") or body.get("username") or "").strip() or None
    try:
        mark_processing(job_id, worker_id, token=token, user_scope=user_scope)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    return {"ok": True}


@router.post("/api/upscale/worker/jobs/{job_id}/complete")
async def api_upscale_worker_job_complete(
    job_id: str,
    worker_id: str = Form(...),
    width: int = Form(0),
    height: int = Form(0),
    token: str | None = Form(None),
    user_scope: str | None = Form(None),
    file: UploadFile = File(...),
):
    from backend.media.upscale_queue import complete_job, job_public_view, verify_worker_token

    scope = str(user_scope or "").strip() or None
    if not verify_worker_token(token, user_scope=scope):
        raise HTTPException(403, "Worker token 无效")
    content = await file.read()
    if len(content) < 8_000:
        raise HTTPException(400, "结果视频过小")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "结果视频过大")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_VIDEO_EXT:
        ext = ".mp4"
    filename = f"{uuid.uuid4().hex}{ext}"
    out_path = UPLOADS_DIR / filename
    out_path.write_bytes(content)
    result_url = f"/uploads/{filename}"
    try:
        from backend.media.video_tools import finalize_saved_video

        saved = await finalize_saved_video(out_path, result_url)
        result_url = str(saved.get("video_url") or result_url)
        if saved.get("width"):
            width = int(saved["width"])
        if saved.get("height"):
            height = int(saved["height"])
        job = complete_job(
            job_id,
            worker_id,
            result_url=result_url,
            width=int(width or 0),
            height=int(height or 0),
            token=token,
            user_scope=scope,
        )
    except PermissionError as exc:
        out_path.unlink(missing_ok=True)
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        out_path.unlink(missing_ok=True)
        raise HTTPException(404, "任务不存在") from exc
    return job_public_view(job)


@router.post("/api/upscale/worker/jobs/{job_id}/fail")
async def api_upscale_worker_job_fail(job_id: str, body: dict = Body(...)):
    from backend.media.upscale_queue import fail_job, job_public_view

    token = body.get("token")
    worker_id = str(body.get("worker_id") or "worker")
    user_scope = str(body.get("user_scope") or body.get("username") or "").strip() or None
    error = str(body.get("error") or "超分失败")
    try:
        job = fail_job(job_id, worker_id, error=error, token=token, user_scope=user_scope)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "任务不存在") from exc
    return job_public_view(job)
