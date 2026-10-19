"""Compress UI text responses without touching media/range downloads."""

from starlette.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers


class TextCompressionMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        text_path = (
            path in ("/", "/canvas", "/workbench", "/canvas.html", "/workbench.html", "/api/canvas", "/api/workbench", "/api/tasks")
            or (path.startswith("/static/") and path.endswith((".js", ".css", ".html", ".svg", ".json")))
        )
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and text_path
            and "range" not in Headers(scope=scope)
        ):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)
