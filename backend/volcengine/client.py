"""Run Volcengine channel generations via existing Ark style helpers."""

from __future__ import annotations

import logging
from typing import Any

from backend.volcengine import models as ve_models
from backend.volcengine import settings as ve_settings

logger = logging.getLogger(__name__)


async def run_for_model(
    model_id: str,
    *,
    kind: str,
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    style: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    cancel_check=None,
    client_task_id: str = "",
) -> Any:
    del style
    k = str(kind or "").strip().lower()
    user_cfg = ve_settings.require_ready(k)
    catalog = ve_models.get_model(model_id)
    if not catalog or catalog.get("kind") != k:
        raise RuntimeError(f"火山引擎模型不存在或不匹配类型：{model_id}")
    if k == "voice" and catalog.get("canvas") is False:
        raise RuntimeError(f"{catalog.get('label') or model_id} 不是画布语音生成模型（识别/实时/音色设计不走此节点）")
    model_cfg = ve_settings.build_model_cfg(catalog, user_cfg, kind=k)

    from backend.myapi import client as myapi_client

    if k == "image":
        return await myapi_client.generate_image(
            model_cfg=model_cfg,
            prompt=prompt,
            ratio=ratio,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    if k == "video":
        return await myapi_client.generate_video(
            model_cfg=model_cfg,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    if k == "text":
        return await myapi_client.generate_text(
            model_cfg=model_cfg,
            prompt=prompt,
            references=references,
            reference_images=reference_images,
        )
    if k == "voice":
        return await myapi_client.generate_voice(
            model_cfg=model_cfg,
            prompt=prompt,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    raise RuntimeError(f"火山引擎不支持 kind={kind}")
