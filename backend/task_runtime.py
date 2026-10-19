"""Track in-flight generation tasks and support manual cancel (pause)."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

CancelCheck = Callable[[], bool]


class TaskCancelledError(Exception):
    """Raised when the user manually pauses a generation task."""


_cancelled: set[str] = set()
_tasks: dict[str, asyncio.Task] = {}


def register(task_id: str, task: asyncio.Task) -> None:
    _tasks[task_id] = task


def clear(task_id: str) -> None:
    _cancelled.discard(task_id)
    _tasks.pop(task_id, None)


def is_cancelled(task_id: str) -> bool:
    return task_id in _cancelled


def cancel_check(task_id: str) -> CancelCheck:
    return lambda: is_cancelled(task_id)


def _raise_if_cancelled(task_id: str) -> None:
    if is_cancelled(task_id):
        raise TaskCancelledError("已手动暂停")


async def cancel(task_id: str) -> dict:
    """Request cancel for a running or pending task."""
    task = _tasks.get(task_id)
    existing = task is not None and not task.done()
    _cancelled.add(task_id)
    if task and not task.done():
        task.cancel()
        logger.info("task %s: manual cancel requested", task_id)
        return {"ok": True, "task_id": task_id, "was_running": True}
    logger.info("task %s: cancel flag set (not running locally)", task_id)
    return {"ok": True, "task_id": task_id, "was_running": existing}


def launch(task_id: str, coro: Awaitable[None]) -> asyncio.Task:
    async def _wrapped() -> None:
        try:
            await coro
        finally:
            clear(task_id)

    task = asyncio.create_task(_wrapped(), name=f"gen-{task_id}")
    register(task_id, task)
    return task
