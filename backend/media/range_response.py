"""Single-range media reads, including deployments with pre-Range Starlette."""
import re
from pathlib import Path

import anyio
from starlette.responses import FileResponse, Response, StreamingResponse


def media_file_response(path: Path, request):
    stat = path.stat()
    full = FileResponse(path, stat_result=stat, headers={
        "Cache-Control": "private, max-age=604800", "Accept-Ranges": "bytes",
    })
    raw = request.headers.get("range", "")
    if request.method != "GET" or not raw:
        return full
    validator = request.headers.get("if-range")
    if validator and validator not in (full.headers.get("etag"), full.headers.get("last-modified")):
        return full
    # Unsupported/malformed/multiple ranges are ignored with a full response.
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw.strip())
    if not match or not any(match.groups()):
        return full
    left, right = match.groups()
    if len(left) > 20 or len(right) > 20:
        return full
    size = stat.st_size
    if left:
        start = int(left)
        end = min(int(right), size - 1) if right else size - 1
    else:
        start, end = max(0, size - int(right)), size - 1
    headers = dict(full.headers)
    if start >= size or end < start:
        headers["content-range"] = f"bytes */{size}"
        headers["content-length"] = "0"
        return Response(status_code=416, headers=headers)
    headers["content-range"] = f"bytes {start}-{end}/{size}"
    headers["content-length"] = str(end - start + 1)

    async def chunks():
        # Closing/cancelling a browser request also closes the file context.
        async with await anyio.open_file(path, "rb") as source:
            await source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = await source.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(chunks(), status_code=206, headers=headers)
