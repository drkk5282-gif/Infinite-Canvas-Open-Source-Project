"""Base API style: default upload, response parsing, and reference resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.myapi import media as media_api
from backend.myapi.common import (
    TASK_DONE,
    TASK_FAIL,
    TASK_PENDING,
    extract_error,
    image_quality_level,
    image_quality_to_size,
    join_url,
    limit_references,
    normalize_image_quality,
    ratio_to_size,
    split_media_arrays,
)


@dataclass
class VideoBuildContext:
    model_cfg: dict[str, Any]
    model_name: str
    text: str
    dur: int
    ratio: str
    resolution: str | None
    resolved_refs: list[dict[str, Any]]
    client_task_id: str = ""


@dataclass
class ImageBuildContext:
    model_cfg: dict[str, Any]
    model_name: str
    prompt: str
    ratio: str
    size: str | None
    resolved_refs: list[dict[str, Any]]
    resolution: str | None = None
    quality: str | None = None
    output_format: str | None = None
    background: str | None = None
    client_task_id: str = ""


class BaseApiStyle:
    """Override build_* and parse_* in subclasses; upload defaults are shared."""

    style_id: str = ""
    # Open Canvas-style single /generations endpoint (e.g. shanhai).
    uses_generations_api: bool = False

    # Upload endpoint config (override per relay if needed)
    upload_path: str = "v1/media/uploads"
    upload_field: str = "file"
    upload_response_key: str = "url"

    video_create_path: str = "v1/videos"
    video_poll_path_template: str = "v1/videos/{task_id}"
    image_create_path: str = "v1/images/generations"
    # Some relays (incl. slow youkou models) return a task id first; poll like video.
    image_poll_path_template: str = "v1/images/generations/{task_id}"

    def normalize_base_url(self, base_url: str) -> str:
        """Normalize relay root before joining create/poll/upload paths."""
        return (base_url or "").strip().rstrip("/")

    async def resolve_references(
        self,
        refs: list[dict[str, Any]],
        model_cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await media_api.resolve_references_async(
            refs,
            model_cfg,
            upload_path=self.upload_path,
            upload_field=self.upload_field,
            response_url_key=self.upload_response_key,
        )

    def limit_references(
        self,
        refs: list[dict[str, Any]],
        model_cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return limit_references(refs, model_cfg)

    def split_media_arrays(
        self,
        resolved_refs: list[dict[str, Any]],
    ) -> tuple[list[str], list[str], list[str]]:
        return split_media_arrays(resolved_refs)

    def build_video_body(self, ctx: VideoBuildContext) -> dict[str, Any]:
        from backend.myapi.common import (
            normalize_ratio,
            ratio_is_auto,
            resolution_is_free,
            video_resolution_to_size,
        )

        images, videos, audios = self.split_media_arrays(ctx.resolved_refs)
        body: dict[str, Any] = {"model": ctx.model_name, "prompt": ctx.text}
        if images:
            body["images"] = images
        if videos:
            body["videos"] = videos
        if audios:
            body["audios"] = audios
        if ctx.model_cfg.get("send_duration", True):
            body["duration"] = ctx.dur
            body["seconds"] = ctx.dur
        ratio = normalize_ratio(ctx.ratio)
        if not ratio_is_auto(ratio):
            body["ratio"] = ratio
            body["aspect_ratio"] = ratio
        if ctx.model_cfg.get("send_size", True) and not resolution_is_free(ctx.resolution):
            size = video_resolution_to_size(
                str(ctx.resolution or "").strip() or None,
                ratio if not ratio_is_auto(ratio) else None,
            )
            if size:
                body["size"] = size
        return body

    async def post_video_create(
        self,
        client: Any,
        *,
        create_url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        ctx: VideoBuildContext,
    ) -> Any:
        """Default: JSON POST. Styles may override (e.g. fanke multipart)."""
        return await client.post(create_url, headers=headers, json=body)

    def video_poll_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return headers

    def build_image_body(self, ctx: ImageBuildContext) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": ctx.model_name,
            "prompt": ctx.prompt,
            "n": 1,
            "response_format": "url",
        }
        if ctx.size:
            body["size"] = ctx.size
        # OpenAI / aiooapi-compatible extras — only when a clarity tier is chosen.
        if ctx.quality:
            body["quality"] = ctx.quality
        if ctx.output_format:
            body["output_format"] = ctx.output_format
        if ctx.background:
            body["background"] = ctx.background
        # Common relay reference-image field (URL or data-URL list).
        images, _, _ = self.split_media_arrays(ctx.resolved_refs)
        if images:
            body["images"] = images
        return body

    def image_create_soft_keys(self) -> tuple[str, ...]:
        """Top-level keys that may be stripped on soft retry after 400/404/422."""
        return ("size", "response_format", "quality", "output_format", "background")

    def resolve_image_create_path(self, ctx: ImageBuildContext) -> str:
        """Override when path depends on refs (e.g. generations vs edits)."""
        del ctx
        return self.image_create_path

    async def post_image_create(
        self,
        client: Any,
        *,
        create_url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        ctx: ImageBuildContext,
    ) -> Any:
        """Default: JSON POST. Styles may override (e.g. 4stoken multipart edits)."""
        del ctx
        return await client.post(create_url, headers=headers, json=body)

    def humanize_image_create_error(self, status: int, err: str) -> str:
        return (err or "").strip() or f"HTTP {status}"

    def image_size_for_ratio(
        self,
        ratio: str,
        model_cfg: dict[str, Any],
        resolution: str | None = None,
    ) -> str | None:
        if not model_cfg.get("send_size", True):
            return None
        tier_size = image_quality_to_size(resolution, ratio)
        if tier_size:
            return tier_size
        return ratio_to_size(ratio)

    def image_quality_extras(self, resolution: str | None) -> dict[str, str | None]:
        """quality / output_format / background when 1K|2K|4K is selected."""
        if not normalize_image_quality(resolution):
            return {"quality": None, "output_format": None, "background": None}
        return {
            "quality": image_quality_level(resolution) or "high",
            "output_format": "png",
            "background": "opaque",
        }

    def image_poll_url(self, base_url: str, task_id: str, poll_url: str = "") -> str:
        if poll_url:
            if poll_url.startswith("/"):
                return join_url(base_url, poll_url.lstrip("/"))
            return poll_url
        return join_url(base_url, self.image_poll_path_template.format(task_id=task_id))

    def extract_image_url(self, data: dict[str, Any]) -> str:
        items = self.parse_image_response(data)
        if items:
            first = items[0]
            for key in ("url", "image_url"):
                v = first.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            b64 = first.get("b64_json")
            if isinstance(b64, str) and b64.strip():
                raw = b64.strip()
                return raw if raw.startswith("data:") else f"data:image/png;base64,{raw}"
        if isinstance(data, dict):
            for key in ("url", "image_url", "output_url", "download_url"):
                v = data.get(key)
                if isinstance(v, str) and v.strip().startswith(("http://", "https://", "data:")):
                    return v.strip()
        return ""

    # ── Response parsing ─────────────────────────────────────────────

    def extract_task_id(self, data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        for key in ("task_id", "id"):
            v = data.get(key)
            if v:
                return str(v).strip()
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("task_id", "id"):
                v = nested.get(key)
                if v:
                    return str(v).strip()
        return ""

    def extract_status(self, data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        for key in ("status", "state"):
            v = data.get(key)
            if v:
                return str(v).strip().lower()
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("status", "state"):
                v = nested.get(key)
                if v:
                    return str(v).strip().lower()
        return ""

    def extract_poll_url(self, data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        for key in ("poll_url", "pollUrl"):
            v = data.get(key)
            if isinstance(v, str) and v.strip().startswith(("http://", "https://", "/")):
                return v.strip()
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("poll_url", "pollUrl"):
                v = nested.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    def extract_task_error(self, data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""
        err = data.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("msg") or "").strip()
            if msg:
                return msg
        for key in ("message", "error", "fail_reason", "failure_reason"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        nested = data.get("data")
        if isinstance(nested, dict):
            return self.extract_task_error(nested)
        return ""

    def extract_video_url(self, data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return ""

        def from_obj(obj: Any) -> str:
            if not isinstance(obj, dict):
                return ""
            for key in ("url", "video_url", "download_url", "output_url"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip().startswith(("http://", "https://", "data:")):
                    return v.strip()
            for nest_key in ("metadata", "output", "result", "video", "data"):
                nested = obj.get(nest_key)
                if isinstance(nested, dict):
                    got = from_obj(nested)
                    if got:
                        return got
                if isinstance(nested, list) and nested:
                    got = from_obj(nested[0])
                    if got:
                        return got
                if isinstance(nested, str) and nested.strip().startswith(("http://", "https://")):
                    return nested.strip()
            return ""

        return from_obj(data)

    def parse_image_response(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        items = data.get("data")
        if isinstance(items, list) and items:
            return [x for x in items if isinstance(x, dict)]
        if data.get("url"):
            return [{"url": data["url"]}]
        if isinstance(data.get("images"), list):
            return [x for x in data["images"] if isinstance(x, dict)]
        return []

    def task_pending(self) -> set[str]:
        return TASK_PENDING

    def task_done(self) -> set[str]:
        return TASK_DONE

    def task_fail(self) -> set[str]:
        return TASK_FAIL

    def resolve_video_create_path(self, ctx: VideoBuildContext) -> str:
        """Override when create path depends on model / protocol."""
        del ctx
        return self.video_create_path

    def default_video_poll_url(self, base_url: str, task_id: str, ctx: VideoBuildContext) -> str:
        """Optional absolute poll URL to persist after create (e.g. Gemini operation fetch)."""
        del ctx
        return self.video_poll_url(base_url, task_id)

    def video_create_should_fallback(self, status: int) -> bool:
        return status in (404, 405)

    def humanize_video_create_error(self, status: int, err: str) -> str:
        return (err or "").strip() or f"HTTP {status}"

    def video_poll_url(
        self,
        base_url: str,
        task_id: str,
        poll_url: str = "",
        *,
        model_cfg: dict[str, Any] | None = None,
    ) -> str:
        del model_cfg
        if poll_url:
            if poll_url.startswith("/"):
                return join_url(base_url, poll_url.lstrip("/"))
            return poll_url
        return join_url(base_url, self.video_poll_path_template.format(task_id=task_id))

    async def poll_video_once(
        self,
        client: Any,
        *,
        url: str,
        headers: dict[str, str],
        task_id: str,
        model_cfg: dict[str, Any] | None = None,
    ) -> Any:
        """Default video poll is GET. Styles may POST (e.g. Gemini fetchPredictOperation)."""
        del task_id, model_cfg
        return await client.get(url, headers=headers)

    async def recover_video_after_create_timeout(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        client_task_id: str,
        cancel_check=None,
    ) -> dict[str, Any] | None:
        return None

    def media_download_headers(self, headers: dict[str, str], url: str) -> dict[str, str] | None:
        """Return auth headers when the result URL requires the same API Key (e.g. content_url)."""
        del headers, url
        return None

    async def resolve_download_url(
        self,
        client: Any,
        *,
        base_url: str,
        headers: dict[str, str],
        task_id: str,
        data: dict[str, Any],
    ) -> str:
        """Optional post-poll download URL resolution (e.g. signed_url)."""
        del client, base_url, headers, task_id
        return self.extract_video_url(data)
