import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.user_scope import tasks_file as _tasks_file
from backend.config import VIDEO_TASKS_FILE
from backend.json_io import load_json, save_json
from backend.models import MediaTask, TaskStatus
from backend.file_transaction import file_transaction, locked_file


def _tasks_path() -> Path:
    return _tasks_file()


class ExistingNodeTask(RuntimeError):
    def __init__(self, task):
        self.task = task
        super().__init__("此节点已有任务，请继续等待或重新检测原任务")


def transaction():
    return file_transaction(_tasks_path())


def _node_key(item):
    node = str(item.get("node_id") or "").strip()
    wb = str(item.get("workbench_id") or "").strip()
    board = str(item.get("board_id") or "").strip()
    return ("workbench", wb, node) if wb and node else (("board", board, node) if board and node else None)


def _upstream_is_definitively_missing(item) -> bool:
    """True when a failed query proves the provider no longer owns the task."""
    error = str(item.get("error") or "").lower()
    return any(
        marker in error
        for marker in (
            "任务不存在或不属于当前本地账户",
            "任务不存在或不属于当前账户",
            "task not found or does not belong",
        )
    )


def _is_local_wait_timeout(item) -> bool:
    """Local poll ceiling hit — upstream may still exist, but the node must not stay locked forever."""
    error = str(item.get("error") or "")
    return "等待本机任务" in error and "超时" in error


def _check_node_binding(items, candidate, *, exclude_id=None):
    key = _node_key(candidate)
    if key is None:
        return
    from backend.job_worker import poll_ledger
    for row in items:
        if row.get("id") == exclude_id or _node_key(row) != key:
            continue
        if row.get("status") in ("pending", "generating"):
            raise ExistingNodeTask(MediaTask(**row))
        # An unanswered query is not evidence that the upstream generation ended.
        if row.get("status") == "failed":
            rec = poll_ledger.get_record(row["id"])
            if rec and rec.get("upstream_task_id") and rec.get("state") not in ("upstream_failed", "cancelled"):
                if _upstream_is_definitively_missing(row):
                    # Heal ledgers written by older builds that left a 404/410
                    # provider result in ``polling`` forever.
                    poll_ledger.update_record(
                        row["id"],
                        state="upstream_failed",
                        upstream_status="missing",
                    )
                    continue
                if _is_local_wait_timeout(row):
                    # Local wait already gave up (e.g. >3600s). Keep the ledger so
                    # history「重新检测」can still recover a late upstream success,
                    # but do not block a fresh generate on this node forever.
                    if rec.get("state") == "polling":
                        poll_ledger.update_record(row["id"], state="query_failed")
                    continue
                raise ExistingNodeTask(MediaTask(**row))


def _ensure_file() -> None:
    path = _tasks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    # migrate legacy video-only tasks
    if VIDEO_TASKS_FILE.exists():
        items = load_json(VIDEO_TASKS_FILE, default=[])
        if not isinstance(items, list):
            items = []
        for item in items:
            item.setdefault("kind", "video")
            item.setdefault("image_url", None)
        save_json(path, items)
    else:
        path.write_text("[]", encoding="utf-8")


def _load() -> list[dict]:
    _ensure_file()
    data = load_json(_tasks_path(), default=[])
    return data if isinstance(data, list) else []


def _save(items: list[dict]) -> None:
    _ensure_file()
    save_json(_tasks_path(), items)


@locked_file(_tasks_path)
def list_tasks(limit: int = 50, kind: Optional[str] = None) -> list[MediaTask]:
    items = _load()
    if kind:
        items = [i for i in items if i.get("kind", "video") == kind]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    # A long-running video can be older than the newest 50 rows. Always keep
    # every active task in the polling response, then fill the remaining room
    # with recent terminal history. Otherwise refresh makes live work vanish.
    active_statuses = {TaskStatus.pending.value, TaskStatus.generating.value}
    active = [item for item in items if item.get("status") in active_statuses]
    terminal = [item for item in items if item.get("status") not in active_statuses]
    if limit is None or limit <= 0:
        selected = active
    else:
        selected = active + terminal[: max(0, limit - len(active))]
    selected.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return [MediaTask(**item) for item in selected]


@locked_file(_tasks_path)
def get_task(task_id: str) -> Optional[MediaTask]:
    for item in _load():
        if item["id"] == task_id:
            return MediaTask(**item)
    return None


@locked_file(_tasks_path)
def create_task(
    account_id: str,
    account_name: str,
    prompt: str,
    ratio: str,
    *,
    kind: str = "video",
    board_id: str | None = None,
    workbench_id: str | None = None,
    node_id: str | None = None,
) -> MediaTask:
    task = MediaTask(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        account_id=account_id,
        account_name=account_name,
        prompt=prompt,
        ratio=ratio,
        status=TaskStatus.pending,
        created_at=datetime.now().isoformat(),
        board_id=(board_id or None),
        workbench_id=(workbench_id or None),
        node_id=(node_id or None),
    )
    items = _load()
    _check_node_binding(items, task.model_dump())
    items.insert(0, task.model_dump())
    _save(items)
    return task


@locked_file(_tasks_path)
def update_task(task_id: str, *, allow_recheck: bool = False, **fields) -> Optional[MediaTask]:
    items = _load()
    for item in items:
        if item["id"] == task_id:
            target = getattr(fields.get("status"), "value", fields.get("status"))
            if target in ("pending", "generating") and item.get("status") not in ("pending", "generating"):
                if not allow_recheck:
                    return MediaTask(**item)
                _check_node_binding(items, item, exclude_id=task_id)
            # Success cannot be undone by a delayed timeout/cancel/progress writer.
            if item.get("status") == "completed" and target and target != "completed":
                return MediaTask(**item)
            for key, value in fields.items():
                # Allow clearing optional string fields with "" (None still skipped).
                if value is None and key not in (
                    "user_prompt_kind",
                    "user_action",
                    "user_prompt_message",
                    "error",
                ):
                    continue
                if value is None:
                    item[key] = None
                elif hasattr(value, "value"):
                    item[key] = value.value
                else:
                    item[key] = value
            _save(items)
            return MediaTask(**item)
    return None


@locked_file(_tasks_path)
def fail_interrupted_tasks(
    reason: str = "服务已重启，任务已中断，请重新点生成",
) -> int:
    """Mark pending/generating tasks as failed after process restart (BackgroundTasks die)."""
    items = _load()
    changed = 0
    now = datetime.now().isoformat()
    for item in items:
        if item.get("account_id") == "myapi":
            continue
        status = item.get("status")
        if status in (TaskStatus.pending.value, TaskStatus.generating.value, "pending", "generating"):
            item["status"] = TaskStatus.failed.value
            item["error"] = reason
            item["completed_at"] = now
            changed += 1
    if changed:
        _save(items)
    return changed


@locked_file(_tasks_path)
def begin_recheck(task_id):
    task = get_task(task_id)
    if not task or task.status not in (TaskStatus.failed,):
        return task, False
    return update_task(task_id, allow_recheck=True, status=TaskStatus.generating,
                       poll_attempt=task.poll_attempt + 1, error=None, prompt_sent=True), True
