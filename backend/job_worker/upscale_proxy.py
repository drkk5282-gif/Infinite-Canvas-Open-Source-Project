"""Proxy /api/upscale/worker/* from canvas (10027) to job worker (8089)."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import Response

from backend.config import INTERNAL_WORKER_URL

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-encoding",
        "content-length",
    }
)


async def forward_upscale_worker_request(request: Request, path: str) -> Response:
    base = INTERNAL_WORKER_URL.rstrip("/")
    suffix = (path or "").lstrip("/")
    url = f"{base}/api/upscale/worker/{suffix}" if suffix else f"{base}/api/upscale/worker"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in ("host", "content-length")
    }
    body = await request.body()
    timeout = httpx.Timeout(900.0, connect=30.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        upstream = await client.request(
            request.method,
            url,
            content=body,
            headers=headers,
        )

    resp_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
