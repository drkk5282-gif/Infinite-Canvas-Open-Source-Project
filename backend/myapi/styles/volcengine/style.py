"""Volcengine Ark style — Seedream / Seedance / Chat + openspeech audio.

Docs: https://ark.volcengine.com/ (方舟)
  Image:  POST {ark}/api/v3/images/generations          (sync)
  Video:  POST {ark}/api/v3/contents/generations/tasks  → GET …/tasks/{id} poll
  Text:   POST {ark}/api/v3/chat/completions
  Voice:  POST {openspeech}/api/v3/tts/create           (sync, X-Api-Key)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.myapi.common import normalize_image_quality
from backend.myapi.styles.base import BaseApiStyle, ImageBuildContext, VideoBuildContext
from backend.myapi.styles.volcengine import params as ve_params
from backend.myapi.styles.volcengine import parse as ve_parse

logger = logging.getLogger(__name__)

DEFAULT_ARK_BASE = "https://ark.cn-beijing.volces.com"
DEFAULT_SPEECH_BASE = "https://openspeech.bytedance.com"


class VolcengineStyle(BaseApiStyle):
    style_id = "volcengine"
    chat_completions_path = "api/v3/chat/completions"

    video_create_path = "api/v3/contents/generations/tasks"
    video_poll_path_template = "api/v3/contents/generations/tasks/{task_id}"
    image_create_path = "api/v3/images/generations"
    image_poll_path_template = "api/v3/contents/generations/tasks/{task_id}"
    voice_create_path = "api/v3/tts/create"

    def normalize_base_url(self, base_url: str) -> str:
        b = (base_url or "").strip().rstrip("/")
        if not b:
            return DEFAULT_ARK_BASE
        low = b.lower()
        # Speech host kept as-is (voice models use openspeech base).
        if "openspeech" in low or "bytedance.com" in low:
            return b
        # Strip trailing /api/v3 so join_url(path) stays stable.
        if low.endswith("/api/v3"):
            b = b[: -len("/api/v3")].rstrip("/")
        return b or DEFAULT_ARK_BASE

    def normalize_voice_base_url(self, base_url: str) -> str:
        b = (base_url or "").strip().rstrip("/")
        if not b:
            return DEFAULT_SPEECH_BASE
        low = b.lower()
        if "openspeech" in low or "bytedance.com" in low:
            if low.endswith("/api/v3"):
                b = b[: -len("/api/v3")].rstrip("/")
            return b or DEFAULT_SPEECH_BASE
        # Accidental Ark base on a voice model → force openspeech.
        if "volces.com" in low or "volcengine" in low:
            return DEFAULT_SPEECH_BASE
        if low.endswith("/api/v3"):
            b = b[: -len("/api/v3")].rstrip("/")
        return b or DEFAULT_SPEECH_BASE

    def voice_auth_headers(self, model_cfg: dict[str, Any]) -> dict[str, str]:
        # Prefer dedicated speech key; Ark Bearer key often fails on openspeech.
        key = str(
            model_cfg.get("speech_api_key")
            or model_cfg.get("api_key")
            or ""
        ).strip()
        app_id = str(model_cfg.get("voice_app_id") or model_cfg.get("app_id") or "").strip()
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        if key:
            h["X-Api-Key"] = key
        if app_id:
            h["X-Api-App-Id"] = app_id
        return h

    def image_create_soft_keys(self) -> tuple[str, ...]:
        return ("size", "watermark", "response_format")

    def image_quality_extras(self, resolution: str | None) -> dict[str, str | None]:
        # Seedream uses size=1K|2K|4K — do not send OpenAI quality/output_format.
        del resolution
        return {"quality": None, "output_format": None, "background": None}

    def image_size_for_ratio(
        self,
        ratio: str,
        model_cfg: dict[str, Any],
        resolution: str | None = None,
    ) -> str | None:
        if not model_cfg.get("send_size", True):
            return None
        return ve_params.seedream_size(resolution, ratio)

    def build_image_body(self, ctx: ImageBuildContext) -> dict[str, Any]:
        return ve_params.build_image_body(ctx)

    def build_video_body(self, ctx: VideoBuildContext) -> dict[str, Any]:
        return ve_params.build_video_body(ctx)

    def extract_task_id(self, data: dict[str, Any]) -> str:
        return ve_parse.extract_task_id(data) or super().extract_task_id(data)

    def extract_status(self, data: dict[str, Any]) -> str:
        return ve_parse.extract_status(data) or super().extract_status(data)

    def extract_task_error(self, data: dict[str, Any]) -> str:
        return ve_parse.extract_task_error(data) or super().extract_task_error(data)

    def extract_video_url(self, data: dict[str, Any]) -> str:
        return ve_parse.extract_video_url(data) or super().extract_video_url(data)

    def extract_image_url(self, data: dict[str, Any]) -> str:
        got = ve_parse.extract_image_url(data)
        return got or super().extract_image_url(data)

    def humanize_image_create_error(self, status: int, err: str) -> str:
        base = (err or "").strip() or f"HTTP {status}"
        if status == 401:
            return f"{base}（API Key 无效，请检查方舟 Authorization: Bearer …）"
        if status == 403:
            return f"{base}（无权限或未开通对应模型）"
        if status == 429:
            return f"{base}（触发频率限制，请稍后重试）"
        return base

    def humanize_video_create_error(self, status: int, err: str) -> str:
        return self.humanize_image_create_error(status, err)

    def humanize_voice_create_error(self, status: int, err: str) -> str:
        base = (err or "").strip() or f"HTTP {status}"
        if status == 401:
            return (
                f"{base}（语音需 openspeech X-Api-Key："
                "控制台 speech/new/setting/apikeys，勿用方舟 Bearer Key）"
            )
        if status == 403:
            return f"{base}（无权限或未开通 Seed-Audio / 语音合成）"
        if status == 429:
            return f"{base}（触发频率限制，请稍后重试）"
        return base

    def clarity_hint(self, resolution: str | None) -> str:
        q = normalize_image_quality(resolution)
        return q.upper() if q else ""


STYLE = VolcengineStyle()
