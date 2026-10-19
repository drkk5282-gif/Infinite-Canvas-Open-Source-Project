"""Reconcile canvas MediaTask rows with 8089 worker jobs."""

from __future__ import annotations

import logging
from datetime import datetime

from backend import task_manager
from backend.job_worker import callback as worker_callback
from backend.job_worker import client as worker_client
from backend.job_worker import poll_ledger
from backend.models import MediaTask, TaskStatus

logger = logging.getLogger(__name__)

_WORKER_TIMEOUT_S = 10.0
_JOB_MISSING_GRACE_S = 45


def _parse_iso_age_seconds(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
        return max(0.0, (datetime.now() - dt).total_seconds())
    except ValueError:
        return None


def _job_result_has_media(job: dict | None) -> bool:
    if not isinstance(job, dict):
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("video_url") or result.get("url") or result.get("image_url"):
        return True
    text = result.get("text")
    return isinstance(text, str) and bool(text.strip())


def _fail_task(task: MediaTask, message: str, *, raw_error: str | None = None) -> None:
    from backend.user_scope import get_current_user_id
    worker_callback.apply_worker_callback({
        "task_id": task.id, "user_id": get_current_user_id(), "kind": task.kind,
        "poll_attempt": task.poll_attempt, "status": "failed",
        "error": message, "raw_error": raw_error or message,
    })


def _apply_worker_terminal(user_id: str, task: MediaTask, job: dict) -> None:
    st = str(job.get("status") or "")
    # Stale-fail race: job may be marked failed while result already has media.
    if st == "failed" and _job_result_has_media(job):
        st = "completed"
        job = {**job, "status": "completed", "error": None}
    worker_callback.apply_worker_callback(
        {
            "task_id": task.id,
            "poll_attempt": int(job.get("poll_attempt") or 0),
            "user_id": user_id,
            "kind": job.get("kind") or task.kind,
            "channel": job.get("channel") or "myapi",
            "status": st,
            "result": job.get("result") or {},
            "error": job.get("error") if st != "completed" else None,
            "raw_error": job.get("raw_error") if st != "completed" else None,
            "raw_response": job.get("raw_response") or job.get("result"),
            "error_type": job.get("error_type"),
            "upstream_task_id": job.get("upstream_task_id"),
            "upstream_status": job.get("upstream_status"),
            "upstream_stage": job.get("upstream_stage"),
            "last_poll_at": job.get("last_poll_at"),
        }
    )


async def reconcile_myapi_task(
    task: MediaTask,
    *,
    user_id: str,
    worker_available: bool,
) -> bool:
    """Sync one in-flight MyApi task. Returns True if canvas task changed."""
    if task.status not in (TaskStatus.pending, TaskStatus.generating):
        return False

    age_s = _parse_iso_age_seconds(task.created_at) or 0.0

    if not worker_available:
        # Unreachable worker != failed generation (including browser jobs, which
        # do not have an API poll ledger). Preserve binding until it is reachable.
        return False

    try:
        job = await worker_client.get_job(task.id)
    except worker_client.WorkerQueryError:
        # Transport/HTTP/decoding failures only defer this check.
        return False
    if not job:
        ledger = poll_ledger.get_record(task.id)
        if ledger and ledger.get("upstream_task_id"):
            if ledger.get("state") in ("upstream_failed", "cancelled"):
                _fail_task(task, str(ledger.get("last_error") or "原任务已结束"))
                return True
            try:
                # Restore the worker shell and GET/download the same upstream ID.
                await worker_client.recheck_job(task.id, poll_attempt=task.poll_attempt)
            except Exception as exc:
                logger.warning("restore worker query job=%s: %s", task.id, exc)
            return False
        if age_s >= _JOB_MISSING_GRACE_S:
            msg = "任务未成功提交到任务服务，请重新生成"
            _fail_task(task, msg)
            return True
        return False

    if int(job.get("poll_attempt") or 0) < task.poll_attempt:
        # The recheck POST/response may have been lost. Re-deliver the same
        # query attempt; this endpoint never submits a generation upstream.
        try:
            await worker_client.recheck_job(task.id, poll_attempt=task.poll_attempt)
        except Exception as exc:
            logger.warning("recheck delivery pending task=%s: %s", task.id, exc)
        return False

    st = str(job.get("status") or "")
    if st in ("completed", "failed", "cancelled"):
        _apply_worker_terminal(user_id, task, job)
        return True

    # Execution and deadlines belong to the worker. The canvas cannot see its
    # active subprocesses and must not turn a healthy processing reply into failure.
    if st in ("pending", "processing") and task.status == TaskStatus.pending:
        task_manager.update_task(task.id, status=TaskStatus.generating)
        return True
    return False


async def reconcile_user_myapi_tasks(user_id: str) -> int:
    """Poll-time reconcile for the logged-in user's MyApi tasks."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    available = await worker_client.is_available(timeout=3.0)
    changed = 0
    for task in task_manager.list_tasks(limit=200):
        if await reconcile_myapi_task(task, user_id=uid, worker_available=available):
            changed += 1
    return changed


async def sync_myapi_tasks_from_worker() -> int:
    """On canvas startup: align in-flight MyApi tasks with worker state."""
    from backend.user_scope import reset_current_user_id, set_current_user_id

    available = await worker_client.is_available(timeout=_WORKER_TIMEOUT_S)
    changed = 0

    from backend import app_users
    from backend.config import DATA_DIR

    users_dir = DATA_DIR / "users"
    if not users_dir.is_dir():
        return 0

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        tasks_path = user_dir / "tasks.json"
        if not tasks_path.is_file():
            continue
        slug = user_dir.name
        row = app_users.get_user_by_username(slug) or app_users.get_user(slug)
        user_id = str((row or {}).get("id") or slug)
        token = set_current_user_id(user_id)
        try:
            for task in task_manager.list_tasks(limit=500):
                if await reconcile_myapi_task(task, user_id=user_id, worker_available=available):
                    changed += 1
        finally:
            reset_current_user_id(token)

    if changed:
        logger.info("reconciled %d MyApi task(s) with job worker", changed)
    return changed
