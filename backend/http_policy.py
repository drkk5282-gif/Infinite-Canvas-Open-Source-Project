"""Shared upstream HTTP policy: long hard deadlines, limited network retries.

Policy (all generation / agent / poll APIs):
- Hard ceiling: up to N hours of waiting while the upstream is still working (default 4h).
- Network / site-down / transient 5xx / 429: retry up to N times (default 10), then fail.
- Clear client errors (404, 401, 403, most 4xx) and upstream terminal failures: fail fast.
- Poll interval between healthy status checks (default 10s for video).
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

# Absolute wait while a task is still pending / generating.
API_HARD_TIMEOUT_S = 4 * 60 * 60
# Single HTTP call may block a long time (sync create / LLM). Still below hard ceiling.
API_REQUEST_READ_S = 60 * 60
API_REQUEST_CONNECT_S = 30.0
# Consecutive unreachable / transport / transient failures before giving up.
API_NETWORK_RETRIES = 10
# Healthy poll tick for video/upstream status (seconds).
API_POLL_INTERVAL_S = 10.0

_state: dict[str, float | int] = {
    "hard_timeout_s": float(API_HARD_TIMEOUT_S),
    "network_retries": int(API_NETWORK_RETRIES),
    "poll_interval_s": float(API_POLL_INTERVAL_S),
}

T = TypeVar("T")


def hard_timeout_s() -> float:
    return float(_state["hard_timeout_s"])


def network_retries() -> int:
    return int(_state["network_retries"])


def poll_interval_s() -> float:
    return float(_state["poll_interval_s"])


def get_policy() -> dict[str, Any]:
    hours = hard_timeout_s() / 3600.0
    return {
        "hard_timeout_s": hard_timeout_s(),
        "hard_timeout_hours": round(hours, 2) if hours < 1 else int(round(hours)),
        "network_retries": network_retries(),
        "poll_interval_sec": poll_interval_s(),
        "request_connect_s": API_REQUEST_CONNECT_S,
        "request_read_s": API_REQUEST_READ_S,
    }


def set_policy(
    *,
    hard_timeout_hours: float | int | None = None,
    hard_timeout_s: float | int | None = None,
    network_retries: int | None = None,
    poll_interval_sec: float | int | None = None,
) -> dict[str, Any]:
    global API_HARD_TIMEOUT_S, API_NETWORK_RETRIES, API_POLL_INTERVAL_S
    # Params share getter names — never call hard_timeout_s()/network_retries() in this scope.
    if hard_timeout_s is not None:
        _state["hard_timeout_s"] = float(max(60.0, min(24 * 3600.0, float(hard_timeout_s))))
    elif hard_timeout_hours is not None:
        hours = max(1.0, min(24.0, float(hard_timeout_hours)))
        _state["hard_timeout_s"] = hours * 3600.0
    if network_retries is not None:
        _state["network_retries"] = int(max(0, min(30, int(network_retries))))
    if poll_interval_sec is not None:
        _state["poll_interval_s"] = float(max(1.0, min(60.0, float(poll_interval_sec))))
    API_HARD_TIMEOUT_S = int(_state["hard_timeout_s"])
    API_NETWORK_RETRIES = int(_state["network_retries"])
    API_POLL_INTERVAL_S = float(_state["poll_interval_s"])
    return get_policy()


def long_client_timeout(*, read_s: float | None = None, write_s: float | None = None) -> httpx.Timeout:
    read = float(read_s if read_s is not None else API_REQUEST_READ_S)
    write = float(write_s if write_s is not None else read)
    return httpx.Timeout(
        connect=API_REQUEST_CONNECT_S,
        read=read,
        write=write,
        pool=API_REQUEST_CONNECT_S,
    )


def poll_get_timeout() -> httpx.Timeout:
    """One poll GET should return soon; the loop owns the hard deadline."""
    return httpx.Timeout(connect=API_REQUEST_CONNECT_S, read=120.0, write=60.0, pool=30.0)


def is_fail_fast_http(status: int) -> bool:
    """True when the response is a definitive client/business error — do not retry."""
    if status in (404, 400, 401, 403, 409, 410, 422):
        return True
    if status == 429:
        return False
    return 400 <= status < 500


def is_upstream_missing_http(status: int, err: str = "") -> bool:
    """True when the provider no longer has this job — recheck is useless; allow a fresh generate."""
    if status in (404, 410):
        return True
    text = (err or "").lower()
    return any(
        marker in text
        for marker in (
            "job not found",
            "task not found",
            "任务不存在",
            "not found or does not belong",
        )
    )


def is_transient_http(status: int, err: str = "") -> bool:
    if status in (429, 502, 503, 504):
        return True
    if is_fail_fast_http(status):
        return False
    text = (err or "").lower()
    return any(
        k in text
        for k in (
            "繁忙",
            "稍后重试",
            "try again",
            "rate limit",
            "too many",
            "overloaded",
            "temporarily",
            "timeout",
            "timed out",
            "gateway",
            "bad gateway",
            "service unavailable",
            "connection reset",
            "connection refused",
            "name or service not known",
            "nodename nor servname",
            "network is unreachable",
            "temporary failure",
            "无法访问",
            "网页无法",
        )
    )


def is_network_exc(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (httpx.TimeoutException, httpx.TransportError, asyncio.TimeoutError, ConnectionError, TimeoutError),
    ):
        return True
    text = str(exc or "").lower()
    return any(
        k in text
        for k in (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "name or service not known",
            "nodename nor servname",
            "network is unreachable",
            "server disconnected",
            "remote protocol",
            "ssl",
            "无法访问",
            "网页无法",
        )
    )


def retry_backoff_s(attempt: int, *, base: float = 1.5, cap: float = 30.0) -> float:
    """attempt is 1-based streak count."""
    wait = min(cap, base * max(1, attempt))
    return wait + random.uniform(0.0, min(1.0, wait * 0.1))


async def call_with_network_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int | None = None,
    label: str = "upstream",
    is_retryable: Callable[[BaseException], bool] | None = None,
) -> T:
    """Retry only network / transport failures. Caller must fail-fast on HTTP 4xx themselves."""
    limit = max(1, int(retries if retries is not None else network_retries()))
    check = is_retryable or is_network_exc
    last: BaseException | None = None
    for attempt in range(1, limit + 1):
        try:
            return await operation()
        except Exception as exc:
            last = exc
            if not check(exc) or attempt >= limit:
                raise
            wait = retry_backoff_s(attempt)
            logger.warning(
                "%s network error %s — retry %d/%d in %.1fs",
                label,
                type(exc).__name__,
                attempt,
                limit,
                wait,
            )
            await asyncio.sleep(wait)
    assert last is not None
    raise last
