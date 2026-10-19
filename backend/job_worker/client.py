"""8088-side client for the local 8089 job worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.config import INTERNAL_WORKER_URL
from backend import service_settings

logger = logging.getLogger(__name__)

_TOKEN_HEADER = "X-Job-Worker-Token"
# Busy worker can delay /api/health while still accepting jobs (large jobs.json I/O).
_HEALTH_TIMEOUT = 8.0
# Submit only registers the job; browser work runs after the response.
# Keep generous headroom so a busy event loop does not false-fail as「未启动」.
_SUBMIT_TIMEOUT = 60.0
_QUERY_TIMEOUT = 10.0


def _headers() -> dict[str, str]:
    tok = service_settings.ensure_job_worker_token()
    return {_TOKEN_HEADER: tok, "Content-Type": "application/json"}


def worker_base_url() -> str:
    return INTERNAL_WORKER_URL.rstrip("/")


def _worker_host_port() -> tuple[str, int]:
    parsed = urlparse(worker_base_url())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8089
    return host, int(port)


async def _tcp_port_open(*, timeout: float = 1.0) -> bool:
    """True if something accepts TCP on the worker port (process up even if HTTP is slow)."""
    host, port = _worker_host_port()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    return True


def _submit_failure_message(exc: BaseException) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return (
            "无法连接任务服务（8089）。请先运行「启动任务服务.bat」，"
            "且与画布使用同一安装目录。"
        )
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return (
            "任务服务（8089）接单超时：多任务并行时服务曾被浏览器模拟占满。"
            "请确认已更新任务服务后重试；若仍出现，重启「启动任务服务.bat」，"
            "并在后台适当提高并行数或错开提交。"
        )
    detail = str(exc).strip()
    if detail:
        return f"任务服务（8089）异常：{detail}"
    return "任务服务（8089）请求失败，请查看任务服务窗口日志。"


async def probe_worker(*, timeout: float = _HEALTH_TIMEOUT) -> tuple[bool, str]:
    """Return (ok, detail). detail is empty when ok; otherwise a short Chinese reason.

    HTTP timeout alone does NOT mean the worker is dead — a busy event loop
    (e.g. rewriting a large job_worker_jobs.json) can delay /api/health while
    the port still accepts connections. In that case we treat it as available.
    """
    url = f"{worker_base_url()}/api/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
    except httpx.ConnectError:
        return False, f"无法连接 {url}（连接被拒绝）。请确认「启动任务服务」窗口仍在运行"
    except httpx.TimeoutException:
        if await _tcp_port_open():
            logger.warning(
                "job worker health HTTP timeout (>%ss) but port open — treating as available",
                timeout,
            )
            return True, ""
        return False, f"探测 {url} 超时（>{timeout:.0f}s）且端口无响应。请重启「启动任务服务」"
    except Exception as exc:
        return False, f"探测 {url} 失败：{type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return False, f"任务服务健康检查返回 HTTP {resp.status_code}（{url}）"
    try:
        data = resp.json()
    except Exception:
        return False, f"任务服务健康检查返回非 JSON（{url}）"
    if not isinstance(data, dict) or not data.get("ok"):
        return False, f"任务服务健康检查 ok≠true（{url}）：{data!r}"[:240]
    return True, ""


async def is_available(*, timeout: float = _HEALTH_TIMEOUT) -> bool:
    ok, detail = await probe_worker(timeout=timeout)
    if not ok and detail:
        logger.warning("job worker unavailable: %s", detail)
    return ok


async def is_available_retry(*, timeout: float = _HEALTH_TIMEOUT, retries: int = 2) -> tuple[bool, str]:
    """Health probe with retries; HTTP timeout + open port counts as available."""
    last = "任务服务（8089）未启动"
    attempts = max(1, int(retries) + 1)
    for i in range(attempts):
        ok, detail = await probe_worker(timeout=timeout)
        if ok:
            return True, ""
        last = detail or last
        # Connection refused: no point waiting long; still retry once briefly.
        if i + 1 < attempts:
            await asyncio.sleep(0.25 if "连接被拒绝" in last else 0.5)
    logger.warning("job worker unavailable after retry: %s", last)
    return False, last


async def submit_job(
    *,
    task_id: str,
    user_id: str,
    board_id: str | None,
    workbench_id: str | None = None,
    kind: str,
    model: str | None = None,
    prompt: str = "",
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    account_id: str | None = None,
    provider: str | None = None,
    use_upgrade: bool = False,
    doubao_tier: str | None = None,
    channel: str | None = None,
    payload: dict[str, Any] | None = None,
    needs_callback: bool | None = None,
) -> dict[str, Any]:
    body = {
        "id": task_id,
        "task_id": task_id,
        "user_id": user_id,
        "board_id": board_id,
        "workbench_id": workbench_id,
        "kind": kind,
        "channel": channel,
        "model": model,
        "prompt": prompt,
        "ratio": ratio,
        "duration": duration,
        "resolution": resolution,
        "style": style,
        "references": references or [],
        "reference_images": reference_images or [],
        "account_id": account_id,
        "provider": provider,
        "use_upgrade": use_upgrade,
        "doubao_tier": doubao_tier,
    }
    if isinstance(payload, dict):
        body["payload"] = payload
    if needs_callback is not None:
        body["needs_callback"] = bool(needs_callback)
    url = f"{worker_base_url()}/api/jobs"
    try:
        async with httpx.AsyncClient(timeout=_SUBMIT_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=_headers())
    except httpx.HTTPError as exc:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            raise RuntimeError(_submit_failure_message(exc)) from exc
        raise WorkerSubmitUncertain(_submit_failure_message(exc)) from exc
    if resp.status_code >= 400:
        detail: Any = (resp.text or "")[:300] or f"HTTP {resp.status_code}"
        try:
            detail = resp.json().get("detail") or detail
        except Exception:
            pass
        raise WorkerJobRejected(resp.status_code, detail)
    try:
        data = resp.json()
    except Exception as exc:
        raise WorkerSubmitUncertain(f"任务服务接单响应无法解析：{exc}") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        raise WorkerSubmitUncertain("任务服务接单结果未确认，请等待原任务状态同步")
    return data


class WorkerJobRejected(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        if isinstance(detail, dict):
            msg = str(detail.get("message") or detail.get("msg") or detail.get("code") or detail)
        else:
            msg = str(detail or "").strip() or f"HTTP {status_code}"
        self.message = msg
        super().__init__(msg)


async def worker_supports_browser(*, timeout: float = _HEALTH_TIMEOUT) -> bool:
    url = f"{worker_base_url()}/api/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return False
            data = resp.json()
            return bool(data.get("ok")) and bool(data.get("browser_channels"))
    except Exception:
        return False


class WorkerSubmitUncertain(RuntimeError):
    """POST may have been accepted; retain the task and reconcile, never re-submit."""


class WorkerQueryError(RuntimeError):
    """GET failed; this is not evidence of a missing job."""


async def get_job(job_id: str) -> dict[str, Any] | None:
    url = f"{worker_base_url()}/api/jobs/{job_id}"
    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            job = data.get("job") if isinstance(data, dict) else None
            if not isinstance(job, dict) or str(job.get("id") or "") != job_id:
                raise ValueError("任务服务返回无效任务数据")
            return job
    except Exception as exc:
        logger.warning("job worker get %s failed: %s", job_id, exc)
        raise WorkerQueryError(f"任务状态暂时查询失败：{type(exc).__name__}: {exc}") from exc


async def cancel_job(job_id: str) -> bool:
    url = f"{worker_base_url()}/api/jobs/{job_id}/cancel"
    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT) as client:
            resp = await client.post(url, headers=_headers())
            return resp.status_code < 400
    except Exception as exc:
        logger.warning("job worker cancel %s failed: %s", job_id, exc)
        return False


async def recheck_job(job_id: str, *, poll_attempt: int = 0) -> dict[str, Any]:
    """Ask 8089 to resume an upstream poll for a failed job (no re-submit)."""
    url = f"{worker_base_url()}/api/jobs/{job_id}/recheck"
    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT) as client:
            resp = await client.post(url, headers=_headers(), json={"poll_attempt": poll_attempt})
            if resp.status_code >= 400:
                detail = ""
                try:
                    data = resp.json()
                    detail = str(data.get("detail") or data.get("error") or "")
                except Exception:
                    detail = (resp.text or "")[:200]
                raise WorkerJobRejected(resp.status_code, detail or "重新检测被拒绝")
            data = resp.json() if resp.content else {}
            return data if isinstance(data, dict) else {"ok": True}
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("job worker recheck %s failed: %s", job_id, exc)
        raise RuntimeError(f"任务服务不可用：{exc}") from exc


class WorkerUnavailableError(RuntimeError):
    pass


async def require_worker() -> None:
    if not await is_available():
        raise WorkerUnavailableError("任务服务未启动，请先运行 start-worker.bat（端口 8089）")


async def submit_media_job(
    *,
    user_id: str,
    board_id: str | None,
    kind: str,
    payload: dict[str, Any],
    job_id: str | None = None,
    needs_callback: bool = False,
    workbench_id: str | None = None,
) -> str:
    import uuid

    jid = (job_id or uuid.uuid4().hex).strip()
    body = {
        "id": jid,
        "task_id": jid,
        "user_id": user_id,
        "board_id": board_id,
        "workbench_id": workbench_id,
        "kind": kind,
        "payload": payload,
        "needs_callback": needs_callback,
    }
    url = f"{worker_base_url()}/api/jobs"
    async with httpx.AsyncClient(timeout=_SUBMIT_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers())
        if resp.status_code >= 400:
            detail = resp.text[:300]
            try:
                detail = resp.json().get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(f"任务服务拒绝接单：{detail}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(str(data.get("error") or "任务服务接单失败"))
    return jid


async def wait_for_job(job_id: str, *, timeout: float = 900.0, interval: float = 1.0) -> dict[str, Any]:
    import asyncio
    import time

    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            job = await get_job(job_id)
        except WorkerQueryError:
            job = None
        if job:
            st = str(job.get("status") or "")
            if st == "completed":
                result = job.get("result")
                return result if isinstance(result, dict) else {}
            if st in ("failed", "cancelled"):
                err = str(job.get("error") or "任务失败")
                if "不存在" in err or "已丢失" in err:
                    raise FileNotFoundError(err)
                raise RuntimeError(err)
        await asyncio.sleep(interval)
    raise RuntimeError("任务超时，请稍后重试")


async def run_media_job_sync(
    *,
    user_id: str,
    board_id: str | None,
    kind: str,
    payload: dict[str, Any],
    timeout: float = 900.0,
    workbench_id: str | None = None,
) -> dict[str, Any]:
    await require_worker()
    jid = await submit_media_job(
        user_id=user_id,
        board_id=board_id,
        kind=kind,
        payload=payload,
        workbench_id=workbench_id,
    )
    return await wait_for_job(jid, timeout=timeout)
