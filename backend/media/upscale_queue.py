"""Remote video upscale job queue (server has no GPU → local worker processes)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from backend.config import DATA_DIR, UPLOADS_DIR
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

JOBS_FILE = DATA_DIR / "upscale_jobs.json"
WORKERS_FILE = DATA_DIR / "upscale_workers.json"

_LOCK = threading.Lock()
_LEASE_SECONDS = 45 * 60  # reclaim stuck jobs after 45 min without worker heartbeat
_MAX_RECLAIM_ATTEMPTS = 10
_STALE_WORKER_SECONDS = 90


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _load_jobs() -> dict[str, Any]:
    raw = load_json(JOBS_FILE, default={"jobs": []})
    if not isinstance(raw, dict):
        return {"jobs": []}
    jobs = raw.get("jobs")
    if not isinstance(jobs, list):
        return {"jobs": []}
    return {"jobs": jobs}


def _save_jobs(jobs: list[dict[str, Any]]) -> None:
    save_json(JOBS_FILE, {"jobs": jobs})


def _load_workers() -> dict[str, Any]:
    raw = load_json(WORKERS_FILE, default={"workers": {}})
    if not isinstance(raw, dict):
        return {"workers": {}}
    workers = raw.get("workers")
    if not isinstance(workers, dict):
        return {"workers": {}}
    return {"workers": workers}


def _save_workers(workers: dict[str, Any]) -> None:
    save_json(WORKERS_FILE, {"workers": workers})


def _touch_worker_jobs(jobs: list[dict[str, Any]], worker_id: str) -> bool:
    """Refresh lease for jobs actively owned by a heartbeating worker."""
    wid = (worker_id or "").strip()
    if not wid:
        return False
    now = _now_iso()
    changed = False
    for job in jobs:
        if str(job.get("worker_id") or "").strip() != wid:
            continue
        if str(job.get("status") or "") not in ("claimed", "processing"):
            continue
        job["updated_at"] = now
        changed = True
    return changed


def _reclaim_orphaned_for_worker(
    jobs: list[dict[str, Any]], worker_id: str, *, running: int
) -> bool:
    """Worker reports idle but server still has claimed/processing jobs for it (e.g. after restart)."""
    if int(running) > 0:
        return False
    wid = (worker_id or "").strip()
    if not wid:
        return False
    now = _now_iso()
    changed = False
    for job in jobs:
        if str(job.get("worker_id") or "").strip() != wid:
            continue
        if str(job.get("status") or "") not in ("claimed", "processing"):
            continue
        logger.warning(
            "reclaim orphaned upscale job %s from idle worker %s",
            job.get("id"),
            wid[:16],
        )
        job["status"] = "pending"
        job["worker_id"] = None
        job["claimed_at"] = None
        job["updated_at"] = now
        job["error"] = "Worker 已重启或中断，任务已重新排队"
        changed = True
    return changed


def _reclaim_stale(jobs: list[dict[str, Any]]) -> bool:
    now = datetime.now()
    changed = False
    for job in jobs:
        st = str(job.get("status") or "")
        if st not in ("claimed", "processing"):
            continue
        ref = _parse_iso(job.get("claimed_at") or job.get("updated_at"))
        if not ref or (now - ref).total_seconds() <= _LEASE_SECONDS:
            continue
        reclaim_count = int(job.get("reclaim_count") or 0) + 1
        job["reclaim_count"] = reclaim_count
        if reclaim_count >= _MAX_RECLAIM_ATTEMPTS:
            logger.warning(
                "upscale job %s failed after %d reclaim attempts",
                job.get("id"),
                reclaim_count,
            )
            job["status"] = "failed"
            job["worker_id"] = None
            job["claimed_at"] = None
            job["completed_at"] = _now_iso()
            job["updated_at"] = _now_iso()
            job["error"] = f"Worker 多次超时未响应（已回收 {reclaim_count} 次）"
        else:
            logger.warning(
                "reclaim stale upscale job %s (%d/%d)",
                job.get("id"),
                reclaim_count,
                _MAX_RECLAIM_ATTEMPTS,
            )
            job["status"] = "pending"
            job["worker_id"] = None
            job["claimed_at"] = None
            job["error"] = f"Worker 超时，已重新排队（{reclaim_count}/{_MAX_RECLAIM_ATTEMPTS}）"
            job["updated_at"] = _now_iso()
        changed = True
    return changed


def _prune_workers(workers: dict[str, Any]) -> None:
    now = datetime.now()
    dead = []
    for wid, row in workers.items():
        ref = _parse_iso(row.get("last_seen"))
        if ref and (now - ref).total_seconds() > _STALE_WORKER_SECONDS:
            dead.append(wid)
    for wid in dead:
        workers.pop(wid, None)


def verify_worker_token(token: str | None, *, user_scope: str | None = None) -> bool:
    from backend.upscale_connect import verify_worker_token as _verify

    return _verify(token, user_scope=user_scope)


def _online_worker_count() -> int:
    with _LOCK:
        wdata = _load_workers()
        _prune_workers(wdata["workers"])
        return len(wdata["workers"])


def should_use_remote_upscale(*, mode: str | None) -> bool:
    """Queue for remote UpscaleWorker when forced, workers online, or no local GPU."""
    if (mode or "ai").strip().lower() == "basic":
        return False
    from backend import service_settings

    cfg = service_settings.load()
    if bool(cfg.get("upscale_remote_force")):
        return True
    if _online_worker_count() > 0:
        return True
    from backend.media.video_upscale import engine_status

    return not bool(engine_status().get("available"))


def create_job(
    source_url: str,
    *,
    user_id: str | None = None,
    mode: str | None = "ai",
    algo: str | None = "v3",
    engine_tier: str | None = "pro",
    target_res: str | None = "1080",
    fps: float | None = None,
    scale: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    source = str(source_url or "").strip()
    uid = str(user_id or "").strip() or None
    job = {
        "id": uuid.uuid4().hex[:16],
        "status": "pending",
        "user_id": uid,
        "source_url": source,
        "params": {
            "mode": mode or "ai",
            "algo": algo or "v3",
            "engine_tier": engine_tier or "pro",
            "target_res": target_res or "1080",
            "fps": fps,
            "scale": scale,
            "model": model,
        },
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "claimed_at": None,
        "completed_at": None,
        "worker_id": None,
        "result_url": None,
        "result_width": None,
        "result_height": None,
        "error": None,
        "reclaim_count": 0,
    }
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        if _reclaim_stale(jobs):
            _save_jobs(jobs)
        for existing in reversed(jobs):
            if str(existing.get("source_url") or "").strip() != source:
                continue
            if (existing.get("user_id") or None) != uid:
                continue
            st = str(existing.get("status") or "")
            if st in ("pending", "claimed", "processing"):
                logger.info("reuse existing upscale job %s → %s", existing.get("id"), source[:80])
                return dict(existing)
        jobs.append(job)
        _save_jobs(jobs[-200:])
    logger.info("upscale job queued %s → %s", job["id"], job["source_url"][:80])
    return dict(job)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        if _reclaim_stale(jobs):
            _save_jobs(jobs)
        for job in jobs:
            if job.get("id") == job_id:
                return dict(job)
    return None


def find_job_for_source(source_url: str, *, user_id: str | None = None) -> dict[str, Any] | None:
    """Latest upscale job for a source video URL (canvas recovery after refresh)."""
    needle = str(source_url or "").strip()
    if not needle:
        return None
    uid = str(user_id or "").strip() or None
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        if _reclaim_stale(jobs):
            _save_jobs(jobs)
        for job in reversed(jobs):
            if str(job.get("source_url") or "").strip() != needle:
                continue
            if uid is not None and (job.get("user_id") or None) != uid:
                continue
            return dict(job)
    return None


def get_job_for_user(job_id: str, user_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job:
        return None
    if str(job.get("user_id") or "") != str(user_id or ""):
        return None
    return job


def find_job_by_result_url(result_url: str) -> dict[str, Any] | None:
    needle = str(result_url or "").strip()
    if not needle:
        return None
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        if _reclaim_stale(jobs):
            _save_jobs(jobs)
        for job in reversed(jobs):
            if str(job.get("result_url") or "").strip() == needle:
                return dict(job)
    return None


def job_public_view(job: dict[str, Any]) -> dict[str, Any]:
    st = str(job.get("status") or "")
    out: dict[str, Any] = {
        "id": job.get("id"),
        "status": st,
        "remote": True,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }
    if st == "completed":
        out["url"] = job.get("result_url")
        out["width"] = job.get("result_width")
        out["height"] = job.get("result_height")
        out["kind"] = "video"
    return out


def worker_capacity_for_user(user_id: str | None = None) -> int:
    """Sum max_concurrent for workers visible to this user (per_user pool filters by scope)."""
    from backend.upscale_connect import pool_mode
    from backend.user_scope import get_current_user_id

    uid = str(user_id or get_current_user_id() or "").strip()
    remote = worker_status()
    workers = remote.get("workers") or []
    if pool_mode() != "per_user":
        return int(remote.get("worker_capacity") or 0)
    total = 0
    for row in workers:
        if str(row.get("user_scope") or "") == uid:
            total += int(row.get("max_concurrent") or 1)
    return total


def worker_status() -> dict[str, Any]:
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        if _reclaim_stale(jobs):
            _save_jobs(jobs)
        wdata = _load_workers()
        _prune_workers(wdata["workers"])
        _save_workers(wdata["workers"])
        pending = sum(1 for j in jobs if j.get("status") == "pending")
        active = sum(1 for j in jobs if j.get("status") in ("claimed", "processing"))
        workers = []
        for wid, row in wdata["workers"].items():
            workers.append(
                {
                    "id": wid,
                    "max_concurrent": int(row.get("max_concurrent") or 1),
                    "running": int(row.get("running") or 0),
                    "last_seen": row.get("last_seen"),
                    "user_scope": row.get("user_scope"),
                    "user_scope_label": row.get("user_scope_label"),
                }
            )
    from backend import service_settings
    from backend.media.video_upscale import engine_status

    local = engine_status()
    force_remote = bool(service_settings.load().get("upscale_remote_force"))
    return {
        "remote_enabled": force_remote or bool(workers) or not bool(local.get("available")),
        "local_engine": local,
        "queue_pending": pending,
        "queue_active": active,
        "workers": workers,
        "workers_online": len(workers),
        "worker_capacity": sum(int(w.get("max_concurrent") or 1) for w in workers),
    }


def _resolve_scope_user_id(user_scope: str | None) -> str | None:
    scope = str(user_scope or "").strip()
    if not scope:
        return None
    from backend import app_users

    user = app_users.get_user(scope) or app_users.get_user_by_username(scope)
    return str(user.get("id") or "") if user else None


def _job_matches_worker(job: dict[str, Any], worker_scope_id: str | None) -> bool:
    from backend.upscale_connect import pool_mode

    job_uid = str(job.get("user_id") or "").strip()
    if pool_mode() == "per_user":
        if not worker_scope_id:
            return False
        return job_uid == worker_scope_id
    return True


def register_worker(
    worker_id: str,
    *,
    max_concurrent: int,
    running: int,
    token: str | None = None,
    user_scope: str | None = None,
) -> None:
    scope_id = _resolve_scope_user_id(user_scope)
    if not verify_worker_token(token, user_scope=user_scope or scope_id):
        raise PermissionError("Worker token 无效")
    wid = (worker_id or "worker").strip()[:64] or "worker"
    with _LOCK:
        jdata = _load_jobs()
        jobs = jdata["jobs"]
        touched = _touch_worker_jobs(jobs, wid)
        if _reclaim_orphaned_for_worker(jobs, wid, running=running):
            touched = True
        if _reclaim_stale(jobs):
            touched = True
        if touched:
            _save_jobs(jobs)
        wdata = _load_workers()
        workers = wdata["workers"]
        workers[wid] = {
            "max_concurrent": max(1, min(16, int(max_concurrent))),
            "running": max(0, int(running)),
            "last_seen": _now_iso(),
            "user_scope": scope_id,
            "user_scope_label": str(user_scope or "").strip() or None,
        }
        _save_workers(workers)


def claim_job(
    worker_id: str,
    *,
    max_concurrent: int,
    running: int,
    token: str | None = None,
    user_scope: str | None = None,
) -> dict[str, Any] | None:
    scope_id = _resolve_scope_user_id(user_scope)
    if not verify_worker_token(token, user_scope=user_scope or scope_id):
        raise PermissionError("Worker token 无效")
    if int(running) >= int(max_concurrent):
        return None
    wid = (worker_id or "worker").strip()[:64] or "worker"
    with _LOCK:
        data = _load_jobs()
        jobs = data["jobs"]
        reclaimed = _reclaim_stale(jobs)
        orphaned = _reclaim_orphaned_for_worker(jobs, wid, running=running)
        touched = _touch_worker_jobs(jobs, wid) or reclaimed or orphaned
        picked = None
        for job in jobs:
            if job.get("status") != "pending":
                continue
            if not _job_matches_worker(job, scope_id):
                continue
            job["status"] = "claimed"
            job["worker_id"] = wid
            job["claimed_at"] = _now_iso()
            job["updated_at"] = _now_iso()
            picked = dict(job)
            break
        if picked or touched:
            _save_jobs(jobs)
        wdata = _load_workers()
        workers = wdata["workers"]
        workers[wid] = {
            "max_concurrent": max(1, min(16, int(max_concurrent))),
            "running": max(0, int(running)),
            "last_seen": _now_iso(),
            "user_scope": scope_id,
            "user_scope_label": str(user_scope or "").strip() or None,
        }
        _save_workers(workers)
    if picked:
        logger.info("upscale job %s claimed by %s", picked["id"], wid[:16])
    return picked


def mark_processing(job_id: str, worker_id: str, *, token: str | None = None, user_scope: str | None = None) -> None:
    if not verify_worker_token(token, user_scope=user_scope):
        raise PermissionError("Worker token 无效")
    with _LOCK:
        data = _load_jobs()
        for job in data["jobs"]:
            if job.get("id") == job_id and job.get("worker_id") == worker_id:
                job["status"] = "processing"
                job["updated_at"] = _now_iso()
                job["reclaim_count"] = 0
                _save_jobs(data["jobs"])
                return
    raise KeyError(job_id)


def complete_job(
    job_id: str,
    worker_id: str,
    *,
    result_url: str,
    width: int,
    height: int,
    token: str | None = None,
    user_scope: str | None = None,
) -> dict[str, Any]:
    if not verify_worker_token(token, user_scope=user_scope):
        raise PermissionError("Worker token 无效")
    with _LOCK:
        data = _load_jobs()
        for job in data["jobs"]:
            if job.get("id") != job_id:
                continue
            if job.get("worker_id") != worker_id:
                raise PermissionError("非本 Worker 的任务")
            job["status"] = "completed"
            job["result_url"] = result_url
            job["result_width"] = int(width or 0)
            job["result_height"] = int(height or 0)
            job["completed_at"] = _now_iso()
            job["updated_at"] = _now_iso()
            job["error"] = None
            _save_jobs(data["jobs"])
            logger.info("upscale job %s completed → %s", job_id, result_url)
            return dict(job)
    raise KeyError(job_id)


def fail_job(
    job_id: str,
    worker_id: str,
    *,
    error: str,
    token: str | None = None,
    user_scope: str | None = None,
) -> dict[str, Any]:
    if not verify_worker_token(token, user_scope=user_scope):
        raise PermissionError("Worker token 无效")
    with _LOCK:
        data = _load_jobs()
        for job in data["jobs"]:
            if job.get("id") != job_id:
                continue
            if job.get("worker_id") not in (worker_id, None) and job.get("status") != "pending":
                if job.get("worker_id") != worker_id:
                    raise PermissionError("非本 Worker 的任务")
            job["status"] = "failed"
            job["error"] = (error or "超分失败")[:500]
            job["completed_at"] = _now_iso()
            job["updated_at"] = _now_iso()
            _save_jobs(data["jobs"])
            logger.warning("upscale job %s failed: %s", job_id, job["error"][:120])
            return dict(job)
    raise KeyError(job_id)


async def resolve_job_source_path(job: dict[str, Any]) -> "Path":
    """Local path for a queued job's source video (for worker download)."""
    from pathlib import Path

    from backend.media.video_tools import ensure_local_video

    url = str(job.get("source_url") or "").strip()
    if not url:
        raise FileNotFoundError("任务缺少源视频地址")
    path = await ensure_local_video(url)
    if not isinstance(path, Path):
        path = Path(path)
    return path


def verify_job_worker(job: dict[str, Any], worker_id: str) -> None:
    wid = (worker_id or "").strip()
    owner = str(job.get("worker_id") or "").strip()
    if owner and owner != wid:
        raise PermissionError("非本 Worker 的任务")


async def wait_for_job(job_id: str, *, timeout: float = 7200, poll: float = 2.0) -> dict[str, Any]:
    """Block until job completes (for tests / sync callers)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if not job:
            raise KeyError(job_id)
        st = job.get("status")
        if st == "completed":
            return job_public_view(job)
        if st == "failed":
            raise RuntimeError(job.get("error") or "远程超分失败")
        await asyncio.sleep(poll)
    raise TimeoutError("远程超分超时")
