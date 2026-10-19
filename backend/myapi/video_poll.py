# -*- coding: utf-8 -*-
"""Async video/image task polling using per-style response parsers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from backend.myapi.common import MyApiUpstreamError, extract_error, is_transient_video_error, parse_response_body
from backend.myapi.styles.base import BaseApiStyle

logger = logging.getLogger(__name__)

# Consecutive unreachable / transient failures before giving up.
# Healthy pending responses reset the streak; each task also has a hard deadline.
MAX_UNREACHABLE_RETRIES = 10
MAX_EMPTY_RESPONSES = 10
# Long video queues are normal during provider peaks. Keep a generous absolute
# ceiling while transport failures / malformed responses still fail quickly.
DEFAULT_VIDEO_POLL_TIMEOUT_S = 60 * 60
DEFAULT_IMAGE_POLL_TIMEOUT_S = 10 * 60


def _unreachable_fail(
    kind: str,
    task_id: str,
    detail: str,
    *,
    last: dict[str, Any] | None = None,
) -> MyApiUpstreamError:
    return MyApiUpstreamError(
        f"{kind}任务查询失败：上游连续 {MAX_UNREACHABLE_RETRIES} 次无法访问（{detail}）",
        raw_response=last or {"task_id": task_id, "detail": detail},
    )


async def _emit_progress(callback, data: dict[str, Any], status: str) -> None:
    if not callback:
        return
    try:
        result = callback(data, status)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug("poll progress callback failed: %s", exc)


async def get_video_task_once(
    client: httpx.AsyncClient,
    *,
    style: BaseApiStyle,
    base_url: str,
    headers: dict[str, str],
    task_id: str,
    poll_url: str = "",
    model_cfg: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], str]:
    url = style.video_poll_url(base_url, task_id, poll_url, model_cfg=model_cfg)
    poll_headers = style.video_poll_headers(headers)
    resp = await style.poll_video_once(
        client,
        url=url,
        headers=poll_headers,
        task_id=task_id,
        model_cfg=model_cfg,
    )
    err = extract_error(resp) if resp.status_code >= 400 else ""
    data = parse_response_body(resp)
    if not isinstance(data, dict):
        data = {"http_status": resp.status_code, "body": data}
    return resp.status_code, data, err


async def poll_video_task(
    client: httpx.AsyncClient,
    *,
    style: BaseApiStyle,
    base_url: str,
    headers: dict[str, str],
    task_id: str,
    poll_url: str = "",
    model_cfg: dict[str, Any] | None = None,
    cancel_check=None,
    timeout_s: float | None = None,
    interval_s: float = 10.0,
    on_progress=None,
) -> dict[str, Any]:
    """Poll until done/fail/cancel.

    Healthy pending responses continue until the hard deadline.
    Network / site-down / transient HTTP errors retry up to MAX_UNREACHABLE_RETRIES.
    """
    pending = style.task_pending()
    done = style.task_done()
    fail = style.task_fail()

    effective_timeout = float(timeout_s) if timeout_s and timeout_s > 0 else float(DEFAULT_VIDEO_POLL_TIMEOUT_S)
    deadline = time.monotonic() + effective_timeout
    last: dict[str, Any] = {}
    unreachable_streak = 0
    empty_streak = 0
    while True:
        if cancel_check and cancel_check():
            from backend.task_runtime import TaskCancelledError

            raise TaskCancelledError("已手动暂停")
        if time.monotonic() >= deadline:
            timeout_label = (
                "已超过允许的总等待时间"
                if effective_timeout < 60
                else f"超时（>{int(effective_timeout)}s）"
            )
            raise MyApiUpstreamError(
                f"视频任务{timeout_label}：{task_id}",
                raw_response=last or {"task_id": task_id, "status": "timeout"},
            )
        try:
            status_code, last, err = await asyncio.wait_for(get_video_task_once(
                client,
                style=style,
                base_url=base_url,
                headers=headers,
                task_id=task_id,
                poll_url=poll_url,
                model_cfg=model_cfg,
            ), timeout=max(0.001, deadline - time.monotonic()))
        except (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            unreachable_streak += 1
            detail = type(exc).__name__
            if unreachable_streak >= MAX_UNREACHABLE_RETRIES:
                raise _unreachable_fail("视频", task_id, detail, last=last) from exc
            wait = min(30.0, interval_s * max(1, unreachable_streak))
            logger.warning(
                "MyApi video poll %s network %s retry %d/%d in %.0fs",
                task_id,
                detail,
                unreachable_streak,
                MAX_UNREACHABLE_RETRIES,
                wait,
            )
            await asyncio.sleep(min(wait, max(0.0, deadline - time.monotonic())))
            continue
        if status_code >= 400:
            if is_transient_video_error(status_code, err):
                unreachable_streak += 1
                detail = f"HTTP {status_code}"
                if unreachable_streak >= MAX_UNREACHABLE_RETRIES:
                    raise _unreachable_fail("视频", task_id, f"{detail} {err[:80]}", last=last)
                wait = min(60.0, interval_s * max(1, unreachable_streak))
                logger.warning(
                    "MyApi video poll %s transient %s (%s) retry %d/%d in %.0fs",
                    task_id,
                    detail,
                    err[:80],
                    unreachable_streak,
                    MAX_UNREACHABLE_RETRIES,
                    wait,
                )
                await asyncio.sleep(min(wait, max(0.0, deadline - time.monotonic())))
                continue
            raise MyApiUpstreamError(
                f"查询视频任务失败：{err}",
                raw_response=last if isinstance(last, dict) else {"error": err},
                http_status=status_code,
            )
        unreachable_streak = 0
        status = style.extract_status(last)
        video_url = style.extract_video_url(last)
        await _emit_progress(on_progress, last, status)
        logger.info("MyApi video poll %s status=%s has_url=%s", task_id, status or "?", bool(video_url))
        if status in fail:
            err_msg = style.extract_task_error(last) or "任务失败"
            raise MyApiUpstreamError(
                f"视频任务失败：{err_msg}",
                raw_response=last if isinstance(last, dict) else {"error": err_msg},
                upstream_terminal=True,
            )
        if status in done or (video_url and status not in pending):
            if video_url:
                return last
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_RESPONSES:
                raise MyApiUpstreamError(
                    "视频任务已完成但多次查询仍未返回下载地址，请重新检测原任务", raw_response=last
                )
            await asyncio.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))
            continue
        if video_url and not status:
            return last
        if not status and not video_url:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_RESPONSES:
                raise MyApiUpstreamError(
                    f"视频任务查询连续 {empty_streak} 次返回空数据：{task_id}",
                    raw_response=last or {"task_id": task_id, "empty_responses": empty_streak},
                )
        else:
            empty_streak = 0
        await asyncio.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))


def video_create_client_timeout(ref_count: int) -> httpx.Timeout:
    """Create may upload refs then wait for task id — do not fail just because it is slow."""
    return httpx.Timeout(connect=30.0, read=300.0, write=300.0, pool=30.0)


def image_create_client_timeout(resolution: str | None = None) -> httpx.Timeout:
    """Sync image POST can block a long time — 4K needs ~600s per relay docs."""
    from backend.myapi.common import normalize_image_quality

    tier = normalize_image_quality(resolution)
    read_s = 600.0 if tier == "4k" else 300.0
    return httpx.Timeout(connect=30.0, read=read_s, write=read_s, pool=30.0)


def text_create_client_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=30.0, read=300.0, write=300.0, pool=30.0)


def poll_request_timeout() -> httpx.Timeout:
    """Per poll GET should be quick; network blips are retried in the poll loop."""
    return httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)


async def get_image_task_once(
    client: httpx.AsyncClient,
    *,
    style: BaseApiStyle,
    base_url: str,
    headers: dict[str, str],
    task_id: str,
    poll_url: str = "",
) -> tuple[int, dict[str, Any], str]:
    url = style.image_poll_url(base_url, task_id, poll_url)
    resp = await client.get(url, headers=headers)
    err = extract_error(resp) if resp.status_code >= 400 else ""
    data = parse_response_body(resp)
    if not isinstance(data, dict):
        data = {"http_status": resp.status_code, "body": data}
    return resp.status_code, data, err


async def poll_image_task(
    client: httpx.AsyncClient,
    *,
    style: BaseApiStyle,
    base_url: str,
    headers: dict[str, str],
    task_id: str,
    poll_url: str = "",
    cancel_check=None,
    timeout_s: float | None = None,
    interval_s: float = 10.0,
    on_progress=None,
) -> dict[str, Any]:
    """Poll until done/fail/cancel. Same unreachable retry policy as video."""
    pending = style.task_pending()
    done = style.task_done()
    fail = style.task_fail()
    effective_timeout = float(timeout_s) if timeout_s and timeout_s > 0 else float(DEFAULT_IMAGE_POLL_TIMEOUT_S)
    deadline = time.monotonic() + effective_timeout
    last: dict[str, Any] = {}
    unreachable_streak = 0
    empty_streak = 0
    while True:
        if cancel_check and cancel_check():
            from backend.task_runtime import TaskCancelledError

            raise TaskCancelledError("已手动暂停")
        if time.monotonic() >= deadline:
            timeout_label = (
                "已超过允许的总等待时间"
                if effective_timeout < 60
                else f"超时（>{int(effective_timeout)}s）"
            )
            raise MyApiUpstreamError(
                f"图片任务{timeout_label}：{task_id}",
                raw_response=last or {"task_id": task_id, "status": "timeout"},
            )
        try:
            status_code, last, err = await asyncio.wait_for(get_image_task_once(
                client,
                style=style,
                base_url=base_url,
                headers=headers,
                task_id=task_id,
                poll_url=poll_url,
            ), timeout=max(0.001, deadline - time.monotonic()))
        except (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            unreachable_streak += 1
            detail = type(exc).__name__
            if unreachable_streak >= MAX_UNREACHABLE_RETRIES:
                raise _unreachable_fail("图片", task_id, detail, last=last) from exc
            wait = min(30.0, interval_s * max(1, unreachable_streak))
            logger.warning(
                "MyApi image poll %s network %s retry %d/%d in %.0fs",
                task_id,
                detail,
                unreachable_streak,
                MAX_UNREACHABLE_RETRIES,
                wait,
            )
            await asyncio.sleep(min(wait, max(0.0, deadline - time.monotonic())))
            continue
        if status_code >= 400:
            if is_transient_video_error(status_code, err):
                unreachable_streak += 1
                detail = f"HTTP {status_code}"
                if unreachable_streak >= MAX_UNREACHABLE_RETRIES:
                    raise _unreachable_fail("图片", task_id, f"{detail} {err[:80]}", last=last)
                wait = min(60.0, interval_s * max(1, unreachable_streak))
                logger.warning(
                    "MyApi image poll %s transient %s (%s) retry %d/%d in %.0fs",
                    task_id,
                    detail,
                    err[:80],
                    unreachable_streak,
                    MAX_UNREACHABLE_RETRIES,
                    wait,
                )
                await asyncio.sleep(min(wait, max(0.0, deadline - time.monotonic())))
                continue
            raise MyApiUpstreamError(
                f"查询图片任务失败：{err}",
                raw_response=last if isinstance(last, dict) else {"error": err},
                http_status=status_code,
            )
        unreachable_streak = 0
        status = style.extract_status(last)
        img_url = style.extract_image_url(last)
        await _emit_progress(on_progress, last, status)
        logger.info("MyApi image poll %s status=%s has_url=%s", task_id, status or "?", bool(img_url))
        if status in fail:
            err_msg = style.extract_task_error(last) or "任务失败"
            raise MyApiUpstreamError(
                f"图片任务失败：{err_msg}",
                raw_response=last if isinstance(last, dict) else {"error": err_msg},
                upstream_terminal=True,
            )
        if status in done or (img_url and status not in pending):
            if img_url:
                return last
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_RESPONSES:
                raise MyApiUpstreamError(
                    "图片任务已完成但多次查询仍未返回下载地址，请重新检测原任务", raw_response=last
                )
            await asyncio.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))
            continue
        if img_url and not status:
            return last
        if not status and not img_url:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_RESPONSES:
                raise MyApiUpstreamError(
                    f"图片任务查询连续 {empty_streak} 次返回空数据：{task_id}",
                    raw_response=last or {"task_id": task_id, "empty_responses": empty_streak},
                )
        else:
            empty_streak = 0
        await asyncio.sleep(min(interval_s, max(0.0, deadline - time.monotonic())))
