"""Run local media jobs on the 8089 worker."""

from __future__ import annotations

import inspect
from typing import Any, Callable


def _kwargs_for(fn: Callable[..., Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only keyword args accepted by ``fn`` (skip None / unknown keys)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {k: v for k, v in payload.items() if v is not None and k not in {"op", "url", "board_id"}}

    out: dict[str, Any] = {}
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    for key, value in payload.items():
        if key in {"op", "url", "board_id"}:
            continue
        if value is None:
            continue
        if accepts_var_kw or key in params:
            out[key] = value
    return out


async def run_media_job(job: dict[str, Any]) -> dict[str, Any]:
    kind = str(job.get("kind") or "").strip()
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    board_id = str(job.get("board_id") or "").strip() or None
    board_token = None

    if kind == "video_tool":
        from backend import board_assets
        from backend.media.video_tools import run_video_tool

        op = str(payload.get("op") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not op or not url:
            raise ValueError("video_tool 缺少 op 或 url")
        kwargs = _kwargs_for(run_video_tool, payload)
        workbench_id = str(job.get("workbench_id") or "").strip() or None
        wb_token = board_assets.set_workbench_id(workbench_id)
        board_token = board_assets.set_board_id(board_id)
        try:
            return await run_video_tool(op, url, **kwargs)
        finally:
            board_assets.reset_board_id(board_token)
            board_assets.reset_workbench_id(wb_token)

    if kind == "image_tool":
        from backend import board_assets
        from backend.media.image_tools import run_image_tool

        op = str(payload.get("op") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not op or not url:
            raise ValueError("image_tool 缺少 op 或 url")
        kwargs = _kwargs_for(run_image_tool, payload)
        workbench_id = str(job.get("workbench_id") or "").strip() or None
        wb_token = board_assets.set_workbench_id(workbench_id)
        board_token = board_assets.set_board_id(board_id)
        try:
            return await run_image_tool(op, url, **kwargs)
        finally:
            board_assets.reset_board_id(board_token)
            board_assets.reset_workbench_id(wb_token)

    if kind == "browser_transcode":
        from backend import board_assets
        from backend.media.video_tools import ensure_upload_browser_video

        workbench_id = str(job.get("workbench_id") or "").strip() or None
        wb_token = board_assets.set_workbench_id(workbench_id)
        board_token = board_assets.set_board_id(board_id)
        try:
            url = str(payload.get("url") or "")
            force = bool(payload.get("force"))
            return await ensure_upload_browser_video(url, force=force)
        finally:
            board_assets.reset_board_id(board_token)
            board_assets.reset_workbench_id(wb_token)

    raise RuntimeError(f"不支持的媒体任务类型: {kind}")
