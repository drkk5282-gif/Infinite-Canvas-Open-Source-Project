"""Persistent job store for the 8089 task worker."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.config import DATA_DIR
from backend.file_transaction import file_transaction
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)

JOBS_FILE = DATA_DIR / "job_worker_jobs.json"
# Cap history so sync load/save cannot stall the uvicorn event loop for seconds.
_MAX_JOBS = 400
_KEEP_OPEN = 80


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict[str, Any]:
    raw = load_json(JOBS_FILE, default={"jobs": []})
    if not isinstance(raw, dict):
        return {"jobs": []}
    jobs = raw.get("jobs")
    if not isinstance(jobs, list):
        return {"jobs": []}
    return {"jobs": jobs}


def _prune_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(jobs) <= _MAX_JOBS:
        return jobs
    open_jobs: list[dict[str, Any]] = []
    done_jobs: list[dict[str, Any]] = []
    for job in jobs:
        st = str(job.get("status") or "")
        if st in ("pending", "processing"):
            open_jobs.append(job)
        else:
            done_jobs.append(job)
    keep_open = open_jobs[:_KEEP_OPEN]
    room = max(0, _MAX_JOBS - len(keep_open))
    keep_done = done_jobs[:room]
    pruned = keep_open + keep_done
    dropped = len(jobs) - len(pruned)
    if dropped > 0:
        logger.info("pruned %s old jobs from %s (kept %s)", dropped, JOBS_FILE.name, len(pruned))
    return pruned


def _save(jobs: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Compact JSON: indented multi-MB history blocked the 8089 event loop and
    # made canvas health probes time out even though the process was alive.
    save_json(JOBS_FILE, {"jobs": _prune_jobs(jobs)}, indent=None)


def get_job(job_id: str) -> dict[str, Any] | None:
    jid = (job_id or "").strip()
    if not jid:
        return None
    with file_transaction(JOBS_FILE):
        for job in _load()["jobs"]:
            if str(job.get("id") or "") == jid:
                return dict(job)
    return None


def upsert_job(job: dict[str, Any]) -> dict[str, Any]:
    jid = str(job.get("id") or "").strip()
    if not jid:
        raise ValueError("job id required")
    now = _now_iso()
    job = dict(job)
    job.setdefault("created_at", now)
    job["updated_at"] = now
    with file_transaction(JOBS_FILE):
        data = _load()
        jobs: list[dict[str, Any]] = data["jobs"]
        for i, row in enumerate(jobs):
            if str(row.get("id") or "") == jid:
                job["created_at"] = row.get("created_at") or now
                jobs[i] = job
                _save(jobs)
                return dict(job)
        jobs.insert(0, job)
        _save(jobs)
        return dict(job)


def update_job(job_id: str, *, allow_recheck: bool = False, expected_poll_attempt: int | None = None, **fields: Any) -> dict[str, Any] | None:
    jid = (job_id or "").strip()
    if not jid:
        return None
    with file_transaction(JOBS_FILE):
        data = _load()
        jobs: list[dict[str, Any]] = data["jobs"]
        for i, row in enumerate(jobs):
            if str(row.get("id") or "") != jid:
                continue
            if expected_poll_attempt is not None and int(row.get("poll_attempt") or 0) != expected_poll_attempt:
                return dict(row)
            target = fields.get("status")
            if row.get("status") in ("completed", "cancelled") and target and target != row.get("status") and not (allow_recheck and row.get("status") == "cancelled" and target == "processing"):
                return dict(row)
            merged = dict(row)
            for key, value in fields.items():
                if value is not None or key in ("error", "completed_at"):
                    merged[key] = value
            merged["updated_at"] = _now_iso()
            jobs[i] = merged
            _save(jobs)
            return dict(merged)
    return None


def list_open_jobs(limit: int = 500) -> list[dict[str, Any]]:
    with file_transaction(JOBS_FILE):
        out: list[dict[str, Any]] = []
        for job in _load()["jobs"]:
            st = str(job.get("status") or "")
            if st in ("pending", "processing"):
                out.append(dict(job))
            if len(out) >= limit:
                break
        return out


def list_pending_callbacks(limit: int = 200) -> list[dict[str, Any]]:
    with file_transaction(JOBS_FILE):
        out: list[dict[str, Any]] = []
        for job in _load()["jobs"]:
            if job.get("callback_delivered"):
                continue
            st = str(job.get("status") or "")
            if st not in ("completed", "failed", "cancelled"):
                continue
            out.append(dict(job))
            if len(out) >= limit:
                break
        return out


def public_view(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    use_upgrade = bool(job.get("use_upgrade"))
    tier = str(job.get("doubao_tier") or "").strip() or ("upgrade" if use_upgrade else "")
    channel = job.get("channel")
    provider = str(job.get("provider") or "").strip().lower()
    # Surface S channel in worker job views when upgrade package is used (DouBao only).
    if (use_upgrade or tier == "upgrade") and provider != "dola" and channel not in ("dola", "dola_s"):
        channel = "doubao_s"
    return {
        "id": job.get("id"),
        "task_id": job.get("task_id"),
        "user_id": job.get("user_id"),
        "kind": job.get("kind"),
        "channel": channel,
        "use_upgrade": use_upgrade,
        "doubao_tier": tier or None,
        "account_id": job.get("account_id"),
        "account_name": job.get("account_name"),
        "status": job.get("status"),
        "poll_attempt": int(job.get("poll_attempt") or 0),
        "error": job.get("error"),
        "raw_error": job.get("raw_error"),
        "raw_response": job.get("raw_response"),
        "error_type": job.get("error_type"),
        "upstream_task_id": job.get("upstream_task_id"),
        "upstream_status": job.get("upstream_status"),
        "upstream_stage": job.get("upstream_stage"),
        "last_poll_at": job.get("last_poll_at"),
        "result": job.get("result") or {},
        "callback_delivered": bool(job.get("callback_delivered")),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "completed_at": job.get("completed_at"),
    }
