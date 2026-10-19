"""HTTP client for custom MyApi (OpenAI-compatible relay) channels."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx

from backend.generation_errors import compact_diagnostic, remaining_poll_seconds
from backend.media.refs import parse_reference_payload
from backend.myapi import common
from backend.myapi.styles import get_style
from backend.myapi.styles.base import BaseApiStyle, ImageBuildContext, VideoBuildContext
from backend.myapi.video_poll import (
    DEFAULT_IMAGE_POLL_TIMEOUT_S,
    DEFAULT_VIDEO_POLL_TIMEOUT_S,
    image_create_client_timeout,
    poll_image_task,
    poll_request_timeout,
    poll_video_task,
    text_create_client_timeout,
    video_create_client_timeout,
)
from backend.myapi_store import get_model as get_user_model


def get_model(model_id: str | None):
    """Resolve public models first, then per-user MyApi models."""
    mid = str(model_id or "").strip()
    if mid.startswith("public_"):
        from backend import public_models_store

        return public_models_store.get_model(mid)
    cfg = get_user_model(mid)
    if cfg:
        return cfg
    if mid.startswith("myapi_"):
        return None
    from backend import public_models_store

    return public_models_store.get_model(mid)

logger = logging.getLogger(__name__)

# Re-export for callers that imported from client
bake_prompt_extras = common.bake_prompt_extras


def _model_ledger_id(model_cfg: dict[str, Any]) -> str:
    return str(model_cfg.get("id") or model_cfg.get("name") or model_cfg.get("model") or "").strip()


async def _poll_with_ledger(
    *,
    kind: str,
    client_task_id: str,
    model_cfg: dict[str, Any],
    style: BaseApiStyle,
    base_url: str,
    headers: dict[str, str],
    upstream_task_id: str,
    poll_url: str,
    cancel_check,
    poll_coro_factory: Callable[[httpx.AsyncClient, Callable], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Keep upstream identity through polling and download; runner removes it on success."""
    job_id = (client_task_id or "").strip()
    if job_id:
        try:
            from backend.board_assets import get_board_id, get_workbench_id
            from backend.job_worker import poll_ledger
            from backend.user_scope import get_current_user_id

            resolved = (
                style.video_poll_url(base_url, upstream_task_id, poll_url, model_cfg=model_cfg)
                if kind == "video"
                else style.image_poll_url(base_url, upstream_task_id, poll_url)
            )
            poll_ledger.write_record(
                job_id=job_id,
                kind=kind,
                model=_model_ledger_id(model_cfg),
                upstream_task_id=upstream_task_id,
                poll_url=poll_url or "",
                base_url=base_url,
                style_id=str(getattr(style, "style_id", "") or ""),
                resolved_poll_url=resolved,
                user_id=str(get_current_user_id() or ""),
                board_id=str(get_board_id() or ""),
                extra={"workbench_id": str(get_workbench_id() or "")},
            )
        except Exception as exc:
            logger.warning("poll ledger write skipped for %s: %s", job_id[:12], exc)

    keep_ledger = False
    last_progress_at = 0.0
    last_progress_status = ""

    def _progress(data: dict[str, Any], status: str) -> None:
        nonlocal last_progress_at, last_progress_status
        now = time.monotonic()
        status_s = str(status or "").strip().lower()
        if status_s == last_progress_status and now - last_progress_at < 30.0:
            return
        last_progress_at = now
        last_progress_status = status_s
        try:
            from backend.job_worker import jobs as job_store, poll_ledger

            stamp = datetime.now().isoformat(timespec="seconds")
            poll_ledger.update_record(
                job_id,
                upstream_status=status_s or "unknown",
                last_poll_at=stamp,
            )
            job_store.update_job(
                job_id,
                upstream_task_id=upstream_task_id,
                upstream_status=status_s or "unknown",
                last_poll_at=stamp,
            )
        except Exception as exc:
            logger.debug("MyApi poll progress persist failed: %s", exc)

    poll_client = httpx.AsyncClient(timeout=poll_request_timeout(), follow_redirects=True)
    try:
        out = await poll_coro_factory(poll_client, _progress)
        # Success: keep ledger through post-poll download + job complete.
        # Deleting here opened a window where canvas reconcile saw
        # processing + no poll and falsely marked「长时间无响应」while the
        # worker was still downloading the mp4.
        keep_ledger = True
        if job_id:
            try:
                from backend.job_worker import jobs as job_store, poll_ledger

                poll_ledger.update_record(
                    job_id,
                    state="result_ready",
                    upstream_status=str(style.extract_status(out) or "completed"),
                )
                job_store.update_job(
                    job_id,
                    raw_response=compact_diagnostic(out),
                    upstream_status=str(style.extract_status(out) or "completed"),
                )
            except Exception as exc:
                logger.debug("MyApi terminal poll response persist failed: %s", exc)
        return out
    except Exception as exc:
        # Preserve identity even when querying fails. Never turn GET trouble into
        # a second paid POST; the same upstream ID remains available for recheck.
        keep_ledger = True
        if job_id:
            from backend.job_worker import poll_ledger
            from backend.task_runtime import TaskCancelledError
            state = "cancelled" if isinstance(exc, TaskCancelledError) else (
                "upstream_failed" if getattr(exc, "upstream_terminal", False) else "query_failed"
            )
            poll_ledger.update_record(job_id, state=state,
                                      last_error=str(exc),
                                      raw_response=compact_diagnostic(getattr(exc, "raw_response", None)))
        raise
    except asyncio.CancelledError:
        # Worker/process shutdown — keep record so 8089 can reclaim after restart.
        keep_ledger = True
        raise
    finally:
        await poll_client.aclose()
        if job_id and not keep_ledger:
            try:
                from backend.job_worker import poll_ledger

                poll_ledger.delete_record(job_id)
            except Exception as exc:
                logger.warning("poll ledger delete skipped for %s: %s", job_id[:12], exc)


async def generate_video(
    *,
    model_cfg: dict[str, Any],
    prompt: str,
    ratio: str = "自动",
    duration: int | None = None,
    resolution: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    cancel_check=None,
    client_task_id: str = "",
) -> dict[str, Any]:
    style = get_style(model_cfg)
    from backend import service_settings

    if model_cfg.get("volcengine"):
        if not service_settings.get_volcengine_channel_enabled():
            raise RuntimeError("管理员已关闭火山引擎渠道")
    elif not service_settings.is_myapi_api_style_enabled(style.style_id):
        raise RuntimeError("供应商已失效，请去添加新的")
    base_url = style.normalize_base_url(str(model_cfg.get("base_url") or ""))
    # Keep upload/create/poll on the same normalized root (e.g. strip trailing /v1 for fanke).
    model_cfg = {**model_cfg, "base_url": base_url}

    dur_min = int(model_cfg.get("duration_min") or 4)
    dur_max = int(model_cfg.get("duration_max") or 10)
    if dur_max < dur_min:
        dur_min, dur_max = dur_max, dur_min
    dur = int(duration) if duration else dur_min
    if dur <= 0:
        dur = dur_min
    dur = max(dur_min, min(dur_max, dur))

    text = (prompt or "").strip()
    if not model_cfg.get("send_duration", True):
        text = common.bake_prompt_extras(text, duration=dur)
    else:
        # Even when duration is sent as a field, scrub internal @ref: codes for upstream API.
        from backend.prompt_refs import sanitize_generation_prompt

        text = sanitize_generation_prompt(text)
    from backend.prompt_refs import append_video_copyright_disclaimer

    text = append_video_copyright_disclaimer(text)

    refs = [r.__dict__ for r in parse_reference_payload(references, reference_images)]
    refs = style.limit_references(refs, model_cfg)
    resolved_refs = await style.resolve_references(refs, model_cfg)
    skipped = max(0, len(refs) - len(resolved_refs))
    if skipped:
        logger.warning("MyApi video: %d reference(s) could not be resolved locally", skipped)
    if style.style_id == "fanke" and refs and not resolved_refs:
        raise RuntimeError(
            f"Fanke 参考素材均未能提交（共 {len(refs)} 个）。"
            "请确认画布已连接本地上传的图片/视频文件；"
            "Fanke 不接受 data URL，本地文件将以 multipart 直传。"
        )

    model_name = str(model_cfg.get("model") or model_cfg.get("name") or "")
    ctx = VideoBuildContext(
        model_cfg=model_cfg,
        model_name=model_name,
        text=text,
        dur=dur,
        ratio=ratio,
        resolution=resolution,
        resolved_refs=resolved_refs,
        client_task_id=client_task_id,
    )
    body = style.build_video_body(ctx)

    headers = common.auth_headers(str(model_cfg.get("api_key") or ""))
    create_path = style.resolve_video_create_path(ctx)
    create_url = common.join_url(base_url, create_path)
    logger.info(
        "MyApi video POST %s style=%s protocol=%s model=%s dur=%s refs=%d",
        create_url,
        style.style_id,
        getattr(style, "resolve_video_protocol", lambda *_a, **_k: "")(model_cfg, model_name),
        body.get("model") or model_name,
        dur,
        len(resolved_refs),
    )

    ref_count = len(resolved_refs)
    create_timeout = video_create_client_timeout(ref_count)

    data: dict[str, Any] = {}
    video_url = ""
    task_id = ""
    poll_url = ""

    async with httpx.AsyncClient(timeout=create_timeout, follow_redirects=True) as client:
        try:
            resp = await style.post_video_create(
                client,
                create_url=create_url,
                headers=headers,
                body=body,
                ctx=ctx,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "MyApi video create timeout read=%ss write=%ss refs=%d: %s",
                create_timeout.read,
                create_timeout.write,
                ref_count,
                exc,
            )
            recovered = await style.recover_video_after_create_timeout(
                base_url=base_url,
                headers=headers,
                client_task_id=client_task_id,
                cancel_check=cancel_check,
            )
            if recovered:
                data = recovered
                video_url = style.extract_video_url(data)
            else:
                raise RuntimeError(
                    "创建视频任务时等待上游响应超时；"
                    f"已解析参考素材 {ref_count} 个"
                    f"{f'（{skipped} 个未能读取本地文件）' if skipped else ''}。"
                    "画布任务 ID 与 llmtoken 任务 ID 不同，请到 llmtoken 控制台查看；"
                    "若提示「暂时繁忙」多为平台限流/通道拥堵，等 1～2 分钟再试，勿连点。"
                ) from exc
        else:
            if resp.status_code >= 400:
                primary_err = style.humanize_video_create_error(
                    resp.status_code, common.extract_error(resp)
                )
                # Surface the exact media URLs we submitted (Agnes download failures).
                if style.style_id == "agnes":
                    bits = []
                    for key in ("first_frame", "last_frame"):
                        val = body.get(key)
                        if val:
                            bits.append(f"{key}={val}")
                    imgs = body.get("images")
                    if isinstance(imgs, list) and imgs:
                        bits.append("images=" + ",".join(str(x) for x in imgs[:3]))
                    if bits:
                        primary_err = f"{primary_err}｜已提交：{'；'.join(bits)}"
                llmtoken = getattr(style, "is_llmtoken_base", lambda _b: False)(base_url)
                if llmtoken or not style.video_create_should_fallback(resp.status_code):
                    logger.warning(
                        "MyApi video create failed %s status=%s err=%s",
                        create_url,
                        resp.status_code,
                        common.extract_error(resp),
                    )
                    raise common.MyApiUpstreamError(
                        f"MyApi 创建视频任务失败：{primary_err}",
                        raw_response=common.parse_response_body(resp),
                        http_status=resp.status_code,
                    )
                last_err = primary_err
                alt_ok = False
                for path in ("v1/videos/generations",):
                    alt = common.join_url(base_url, path)
                    try:
                        logger.info("MyApi video retry %s (primary was %s)", alt, resp.status_code)
                        resp2 = await client.post(alt, headers=headers, json=body)
                        if resp2.status_code < 400:
                            resp = resp2
                            alt_ok = True
                            break
                        if not style.video_create_should_fallback(resp2.status_code):
                            last_err = style.humanize_video_create_error(
                                resp2.status_code, common.extract_error(resp2)
                            )
                            break
                        last_err = style.humanize_video_create_error(
                            resp2.status_code, common.extract_error(resp2)
                        )
                    except Exception as exc:
                        last_err = str(exc)
                if not alt_ok:
                    raise common.MyApiUpstreamError(
                        f"MyApi 创建视频任务失败：{last_err}",
                        raw_response=common.parse_response_body(resp),
                        http_status=resp.status_code,
                    )

            data = resp.json() if resp.content else {}
            if not isinstance(data, dict):
                data = {}

            video_url = style.extract_video_url(data)
            task_id = style.extract_task_id(data)
            status = style.extract_status(data)
            poll_url = style.extract_poll_url(data)
            if not poll_url and task_id:
                try:
                    poll_url = style.default_video_poll_url(base_url, task_id, ctx) or ""
                except Exception:
                    poll_url = ""

            # Relative content_url is not a finished public asset — still need poll / auth download.
            if video_url and not video_url.startswith(("http://", "https://", "data:")):
                video_url = ""

            if status in style.task_fail():
                err = style.extract_task_error(data) or "任务失败"
                common.raise_payload_error("视频任务失败", data, err)

            if not video_url and task_id:
                logger.info("MyApi video task created id=%s poll=%s", task_id, poll_url or "-")
                data = await _poll_with_ledger(
                    kind="video",
                    client_task_id=client_task_id,
                    model_cfg=model_cfg,
                    style=style,
                    base_url=base_url,
                    headers=headers,
                    upstream_task_id=task_id,
                    poll_url=poll_url,
                    cancel_check=cancel_check,
                    poll_coro_factory=lambda c, progress, _tid=task_id, _purl=poll_url: poll_video_task(
                        c,
                        style=style,
                        base_url=base_url,
                        headers=headers,
                        task_id=_tid,
                        poll_url=_purl,
                        model_cfg=model_cfg,
                        cancel_check=cancel_check,
                        timeout_s=None,
                        on_progress=progress,
                    ),
                )
                video_url = style.extract_video_url(data)

    need_resolve = bool(task_id) or (
        bool(video_url) and not str(video_url).startswith(("http://", "https://", "data:"))
    )
    if need_resolve:
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                resolved = await style.resolve_download_url(
                    dl_client,
                    base_url=base_url,
                    headers=headers,
                    task_id=task_id,
                    data=data if isinstance(data, dict) else {},
                )
            if resolved:
                video_url = resolved
        except Exception as exc:
            logger.warning("MyApi resolve_download_url failed: %s", exc)

    if not video_url:
        raise common.MyApiUpstreamError(
            "MyApi 视频未返回可下载地址。"
            "请确认中转站与所选协议匹配（4stoken：NewAPI / Gateway / Seedance / Gemini）。",
            raw_response=compact_diagnostic(data),
        )

    dl_headers = style.media_download_headers(headers, str(video_url))
    if (
        dl_headers is None
        and style.style_id == "yimi"
        and "/v1/videos/" in str(video_url)
        and str(video_url).rstrip("/").endswith("/file")
    ):
        dl_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    saved = await common.download_to_uploads(
        video_url, prefix="myapi_vid", headers=dl_headers, expected_kind="video"
    )
    return {
        "video_url": saved["url"],
        "cover_url": saved.get("cover_url") or "",
        "duration": float(dur),
        "width": 0,
        "height": 0,
    }


async def generate_image(
    *,
    model_cfg: dict[str, Any],
    prompt: str,
    ratio: str = "自动",
    resolution: str | None = None,
    style_name: str | None = None,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    cancel_check=None,
    client_task_id: str = "",
) -> dict[str, Any]:
    style = get_style(model_cfg)
    from backend import service_settings

    if model_cfg.get("volcengine"):
        if not service_settings.get_volcengine_channel_enabled():
            raise RuntimeError("管理员已关闭火山引擎渠道")
    elif not service_settings.is_myapi_api_style_enabled(style.style_id):
        raise RuntimeError("供应商已失效，请去添加新的")
    base_url = style.normalize_base_url(str(model_cfg.get("base_url") or ""))
    # Keep upload/create/poll on the same normalized root (e.g. youkou legacy → Open API).
    model_cfg = {**model_cfg, "base_url": base_url}

    full = common.bake_prompt_extras(prompt, ratio=ratio, style=style_name)
    size = style.image_size_for_ratio(ratio, model_cfg, resolution=resolution)
    extras = style.image_quality_extras(resolution)

    refs = [r.__dict__ for r in parse_reference_payload(references, reference_images)]
    refs = style.limit_references(refs, model_cfg)
    resolved_refs = await style.resolve_references(refs, model_cfg) if refs else []

    ctx = ImageBuildContext(
        model_cfg=model_cfg,
        model_name=str(model_cfg.get("model") or model_cfg.get("name") or ""),
        prompt=full,
        ratio=ratio,
        size=size,
        resolved_refs=resolved_refs,
        resolution=resolution,
        quality=extras.get("quality"),
        output_format=extras.get("output_format"),
        background=extras.get("background"),
        client_task_id=client_task_id,
    )
    body = style.build_image_body(ctx)

    create_path = style.resolve_image_create_path(ctx)
    url = common.join_url(base_url, create_path)
    headers = common.auth_headers(str(model_cfg.get("api_key") or ""))
    create_timeout = image_create_client_timeout(resolution=resolution)
    logger.info(
        "MyApi image POST %s style=%s model=%s size=%s quality=%s res=%s refs=%d",
        url,
        style.style_id,
        body.get("model") or ctx.model_name,
        size,
        body.get("quality"),
        resolution or "默认",
        len(resolved_refs),
    )

    soft_keys = style.image_create_soft_keys()
    task_id = ""
    async with httpx.AsyncClient(timeout=create_timeout, follow_redirects=True) as client:
        try:
            resp = await style.post_image_create(
                client,
                create_url=url,
                headers=headers,
                body=body,
                ctx=ctx,
            )
            if resp.status_code >= 400:
                # Soft retry only for JSON generations (multipart edits keep files).
                is_multipart_edits = create_path.rstrip("/").endswith("/images/edits")
                if (
                    not is_multipart_edits
                    and soft_keys
                    and resp.status_code in (400, 404, 422)
                    and any(k in body for k in soft_keys)
                ):
                    body2 = {k: v for k, v in body.items() if k not in soft_keys}
                    resp = await style.post_image_create(
                        client,
                        create_url=url,
                        headers=headers,
                        body=body2,
                        ctx=ctx,
                    )
                if resp.status_code >= 400:
                    err = style.humanize_image_create_error(
                        resp.status_code, common.extract_error(resp)
                    )
                    raise common.MyApiUpstreamError(
                        f"MyApi 生图失败：{err}",
                        raw_response=common.parse_response_body(resp),
                        http_status=resp.status_code,
                    )
            data = resp.json() if resp.content else {}
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "MyApi 生图请求中断（网络超时）。若上游仍在生成，请到中转站任务中心查看或重试。"
            ) from exc

        if not isinstance(data, dict):
            data = {}

        img_url = style.extract_image_url(data)
        status = style.extract_status(data)
        task_id = style.extract_task_id(data)
        poll_url = style.extract_poll_url(data)

        # Relative content_url is not a finished public asset — still need poll / auth download.
        if img_url and not img_url.startswith(("http://", "https://", "data:")):
            img_url = ""

        # Async-shaped response: task id without image url yet → poll like video.
        if not img_url and task_id:
            if status in style.task_fail():
                common.raise_payload_error(
                    "MyApi 生图失败",
                    data,
                    style.extract_task_error(data) or status,
                )
            logger.info("MyApi image task created id=%s status=%s — polling", task_id, status or "?")
            data = await _poll_with_ledger(
                kind="image",
                client_task_id=client_task_id,
                model_cfg=model_cfg,
                style=style,
                base_url=base_url,
                headers=headers,
                upstream_task_id=task_id,
                poll_url=poll_url,
                cancel_check=cancel_check,
                poll_coro_factory=lambda c, progress, _tid=task_id, _purl=poll_url: poll_image_task(
                    c,
                    style=style,
                    base_url=base_url,
                    headers=headers,
                    task_id=_tid,
                    poll_url=_purl,
                    cancel_check=cancel_check,
                    timeout_s=None,
                    on_progress=progress,
                ),
            )
            img_url = style.extract_image_url(data)

    need_resolve = bool(task_id) or (
        bool(img_url) and not str(img_url).startswith(("http://", "https://", "data:"))
    )
    if need_resolve:
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                resolved = await style.resolve_download_url(
                    dl_client,
                    base_url=base_url,
                    headers=headers,
                    task_id=task_id,
                    data=data if isinstance(data, dict) else {},
                )
            if resolved:
                img_url = resolved
        except Exception as exc:
            logger.warning("MyApi image resolve_download_url failed: %s", exc)

    if not img_url:
        # Fall back to legacy list parse for clearer errors
        items = style.parse_image_response(data)
        if not items:
            raise common.MyApiUpstreamError(
                "MyApi 生图未返回图片数据", raw_response=compact_diagnostic(data)
            )
        first = items[0]
        if first.get("b64_json"):
            return common.save_b64_image(str(first["b64_json"]))
        img_url = str(first.get("url") or first.get("image_url") or "").strip()
    if not img_url:
        raise common.MyApiUpstreamError(
            "MyApi 生图结果缺少 url", raw_response=compact_diagnostic(data)
        )
    if img_url.startswith("data:") or (len(img_url) > 200 and not img_url.startswith("http")):
        # bare b64 from extract_image_url data: prefix, or long b64 without prefix
        return common.save_b64_image(img_url)
    dl_headers = style.media_download_headers(headers, str(img_url))
    return await common.download_to_uploads(
        img_url, prefix="myapi_img", headers=dl_headers, expected_kind="image"
    )


def _text_allows_data_image_urls(model_cfg: dict[str, Any] | None) -> bool:
    """Volcengine Ark Chat Completions accept data:image/...;base64,... (see 图片上传理解).

    Agnes and similar relays still need a publicly fetchable HTTPS URL.
    """
    cfg = model_cfg or {}
    if cfg.get("volcengine"):
        return True
    return str(cfg.get("api_style") or "").strip().lower() == "volcengine"


def _text_allows_av_refs(model_cfg: dict[str, Any] | None) -> bool:
    """Ark Chat Completions：图/视/音均可 Files file_id（见音频/视频上传理解）。"""
    cfg = model_cfg or {}
    if not (cfg.get("volcengine") or str(cfg.get("api_style") or "").lower() == "volcengine"):
        return False
    # 目录里显式声明了音/视频额度，或默认多模态 Seed 系。
    if int(cfg.get("max_videos") or 0) > 0 or int(cfg.get("max_audios") or 0) > 0:
        return True
    return bool(cfg.get("multimodal"))


def _audio_format_from_media(media: str) -> str:
    u = (media or "").lower()
    if "wav" in u[:64] or u.startswith("data:audio/wav") or u.startswith("data:audio/x-wav"):
        return "wav"
    if "ogg" in u[:64]:
        return "ogg"
    return "mp3"


def _build_text_message_content(
    prompt: str,
    resolved_refs: list[dict[str, Any]],
    *,
    allow_data_urls: bool = False,
    allow_av_refs: bool = False,
) -> str | list[dict[str, Any]]:
    """Chat Completions multimodal content blocks.

    - 默认（Agnes 等）：仅公网图片 URL
    - 火山方舟：图片 URL/Base64/file_id；音视频用 Files file_id
      （audio → type=input_audio；video → type=video_url）
    """
    from backend.myapi.media import parse_ark_file_id

    blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    media_blocks = 0

    for item in resolved_refs or []:
        kind = str(item.get("kind") or item.get("type") or "image").lower()
        file_id = str(item.get("_file_id") or "").strip() or parse_ark_file_id(
            str(item.get("_media") or "")
        )
        media = str(item.get("_media") or item.get("url") or item.get("src") or "").strip()
        if media.startswith("arkfile:"):
            media = ""

        if kind in ("image", "img", "picture"):
            if file_id:
                blocks.append({"type": "image_url", "image_url": {"file_id": file_id}})
                media_blocks += 1
                continue
            if not media:
                continue
            if media.startswith("data:"):
                if not allow_data_urls:
                    raise RuntimeError(
                        "文本多模态需要公网可访问的图片 URL（如 Agnes 无法读取本地文件/Base64）。"
                        "请在服务设置中配置可外网访问的 public_base_url，或改用已公开的 HTTPS 图片地址。"
                    )
                if not media.startswith("data:image/"):
                    raise RuntimeError(
                        f"参考图 Base64 格式无效（需 data:image/...;base64,...）：{media[:48]}"
                    )
                blocks.append({"type": "image_url", "image_url": {"url": media}})
                media_blocks += 1
                continue
            if not (media.startswith("http://") or media.startswith("https://")):
                raise RuntimeError(f"参考图未能解析为公网 URL：{media[:96]}")
            blocks.append({"type": "image_url", "image_url": {"url": media}})
            media_blocks += 1
            continue

        if kind == "video":
            if not allow_av_refs:
                logger.info("MyApi text skip video ref (style does not allow AV)")
                continue
            if file_id:
                blocks.append({"type": "video_url", "video_url": {"file_id": file_id}})
                media_blocks += 1
                continue
            if media.startswith("http://") or media.startswith("https://"):
                blocks.append({"type": "video_url", "video_url": {"url": media}})
                media_blocks += 1
                continue
            raise RuntimeError(
                "文本参考视频需要方舟 Files file_id 或公网 HTTPS。"
                "请使用画布本地视频（自动 Files 上传）。"
            )

        if kind == "audio":
            if not allow_av_refs:
                logger.info("MyApi text skip audio ref (style does not allow AV)")
                continue
            if file_id:
                # 官方 Chat：type=input_audio + input_audio.file_id
                blocks.append({"type": "input_audio", "input_audio": {"file_id": file_id}})
                media_blocks += 1
                continue
            if media.startswith("data:") and "," in media:
                raw = media.partition(",")[2].strip()
                blocks.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": raw,
                            "format": _audio_format_from_media(media),
                        },
                    }
                )
                media_blocks += 1
                continue
            if media.startswith("http://") or media.startswith("https://"):
                # 部分模型接受 URL；优先仍建议 Files。这里用同结构的 url 字段兜底。
                blocks.append({"type": "input_audio", "input_audio": {"url": media}})
                media_blocks += 1
                continue
            raise RuntimeError(
                "文本参考音频需要方舟 Files file_id 或公网 HTTPS。"
                "请使用画布本地音频（自动 Files 上传）。"
            )

        logger.info("MyApi text skip unsupported ref kind=%s", kind)

    if media_blocks == 0:
        return prompt
    return blocks


async def generate_text(
    *,
    model_cfg: dict[str, Any],
    prompt: str,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    from backend.prompt_refs import sanitize_generation_prompt
    from backend import service_settings

    style = get_style(model_cfg)
    if model_cfg.get("volcengine"):
        if not service_settings.get_volcengine_channel_enabled():
            raise RuntimeError("管理员已关闭火山引擎渠道")
    elif not service_settings.is_myapi_api_style_enabled(style.style_id):
        raise RuntimeError("供应商已失效，请去添加新的")
    base_url = style.normalize_base_url(str(model_cfg.get("base_url") or ""))
    model_cfg = {**model_cfg, "base_url": base_url}

    prompt = sanitize_generation_prompt(prompt)
    refs = [r.__dict__ for r in parse_reference_payload(references, reference_images)]
    resolved: list[dict[str, Any]] = []
    allow_av = _text_allows_av_refs(model_cfg)
    if refs:
        for item in refs:
            rk = str(item.get("kind") or item.get("type") or "image").lower()
            if rk in ("video", "audio") and not allow_av:
                raise RuntimeError(
                    "当前文本模型只支持参考图片，不支持参考视频或音频。请去掉音视频参考后重试。"
                )
        limit_cfg = {
            **model_cfg,
            "kind": "text",
        }
        if not allow_av:
            limit_cfg["max_videos"] = 0
            limit_cfg["max_audios"] = 0
        refs = style.limit_references(refs, limit_cfg)
        resolved = await style.resolve_references(refs, model_cfg) if refs else []

    headers = common.auth_headers(str(model_cfg.get("api_key") or ""))

    # Open Canvas / Shanhai: POST /generations (not OpenAI chat completions).
    if getattr(style, "uses_generations_api", False):
        body = style.build_text_body(
            model_name=str(model_cfg.get("model") or model_cfg.get("name") or ""),
            prompt=prompt,
            resolved_refs=resolved,
        )
        create_path = str(getattr(style, "text_create_path", None) or "generations")
        url = common.join_url(base_url, create_path)
        logger.info(
            "MyApi text POST %s style=%s model=%s refs=%d",
            url,
            style.style_id,
            body.get("model"),
            len(resolved),
        )
        async with httpx.AsyncClient(timeout=text_create_client_timeout(), follow_redirects=True) as client:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                raise RuntimeError("MyApi 文本请求中断（网络超时），请重试") from exc
            if resp.status_code >= 400:
                err = style.humanize_image_create_error(resp.status_code, common.extract_error(resp))
                raise common.MyApiUpstreamError(
                    f"MyApi 文本失败：{err}",
                    raw_response=common.parse_response_body(resp),
                    http_status=resp.status_code,
                )
            data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        status = style.extract_status(data)
        if status in style.task_fail():
            common.raise_payload_error(
                "MyApi 文本失败",
                data,
                style.extract_task_error(data) or status,
            )
        text = str(style.extract_text_result(data) or "").strip()
        if not text:
            raise common.MyApiUpstreamError(
                "MyApi 文本未返回有效内容",
                raw_response=compact_diagnostic(data),
            )
        usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        credits = usage_raw.get("credits")
        usage = _extract_chat_usage(data, prompt=prompt, completion=text)
        if credits is not None:
            try:
                usage["credits"] = float(credits)
            except (TypeError, ValueError):
                pass
        return {"text": text, "usage": usage}

    url = common.join_url(
        base_url,
        getattr(style, "chat_completions_path", None) or "v1/chat/completions",
    )
    message_content: str | list[dict[str, Any]] = prompt
    if resolved:
        message_content = _build_text_message_content(
            prompt,
            resolved,
            allow_data_urls=_text_allows_data_image_urls(model_cfg),
            allow_av_refs=allow_av,
        )

    body = {
        "model": model_cfg.get("model") or model_cfg.get("name"),
        "messages": [{"role": "user", "content": message_content}],
        "temperature": 0.7,
    }
    # 画布文本节点默认关闭深度思考，降低时延与费用（官方文档：thinking.type=disabled）
    if model_cfg.get("volcengine") or str(model_cfg.get("api_style") or "").lower() == "volcengine":
        body["thinking"] = {"type": "disabled"}
    n_media = (
        sum(
            1
            for b in message_content
            if isinstance(b, dict) and b.get("type") in ("image_url", "video_url", "input_audio")
        )
        if isinstance(message_content, list)
        else 0
    )
    logger.info("MyApi text POST %s model=%s media=%s", url, body.get("model"), n_media)

    async with httpx.AsyncClient(timeout=text_create_client_timeout(), follow_redirects=True) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise RuntimeError("MyApi 文本请求中断（网络超时），请重试") from exc
        if resp.status_code >= 400:
            common.raise_http_error("MyApi 文本失败：", resp)
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError("MyApi 文本未返回有效内容") from exc
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        text = "".join(parts).strip()
    else:
        text = str(content or "").strip()
    if not text:
        raise RuntimeError("MyApi 文本结果为空")
    usage = _extract_chat_usage(data, prompt=prompt, completion=text)
    return {"text": text, "usage": usage}


def _extract_chat_usage(data: dict[str, Any], *, prompt: str, completion: str) -> dict[str, int]:
    """Normalize OpenAI-style usage; fall back to char heuristics."""
    raw = data.get("usage") if isinstance(data, dict) else None
    pin = cout = total = None
    if isinstance(raw, dict):
        try:
            if raw.get("prompt_tokens") is not None:
                pin = max(0, int(raw.get("prompt_tokens")))
            if raw.get("completion_tokens") is not None:
                cout = max(0, int(raw.get("completion_tokens")))
            if raw.get("total_tokens") is not None:
                total = max(0, int(raw.get("total_tokens")))
        except (TypeError, ValueError):
            pin = cout = total = None
    if pin is None:
        pin = max(1, (len(prompt or "") + 1) // 2)
    if cout is None:
        cout = max(1, (len(completion or "") + 1) // 2)
    if total is None:
        total = pin + cout
    return {
        "prompt_tokens": int(pin),
        "completion_tokens": int(cout),
        "total_tokens": int(total),
    }


async def generate_voice(
    *,
    model_cfg: dict[str, Any],
    prompt: str,
    references: list[dict] | None = None,
    reference_images: list[str] | None = None,
    cancel_check=None,
    client_task_id: str = "",
) -> dict[str, Any]:
    """Seed-Audio via openspeech POST /api/v3/tts/create (sync).

    Unlike Seedance video (create → poll), Seed-Audio returns audio/url in one shot.
    """
    del client_task_id
    from backend.prompt_refs import sanitize_generation_prompt
    from backend import service_settings
    from backend.myapi.styles.volcengine import params as ve_params
    from backend.myapi.styles.volcengine import parse as ve_parse
    from backend.myapi.styles.volcengine.style import DEFAULT_SPEECH_BASE, VolcengineStyle

    style = get_style(model_cfg)
    is_ve_channel = bool(model_cfg.get("volcengine"))
    if is_ve_channel:
        if not service_settings.get_volcengine_channel_enabled():
            raise RuntimeError("管理员已关闭火山引擎渠道")
    elif not service_settings.is_myapi_api_style_enabled(style.style_id):
        raise RuntimeError("供应商已失效，请去添加新的")
    if not isinstance(style, VolcengineStyle) and style.style_id != "volcengine":
        style = get_style({**model_cfg, "api_style": "volcengine"})
        if not isinstance(style, VolcengineStyle):
            raise RuntimeError("语音生成需要火山引擎 / openspeech 风格")

    if cancel_check and cancel_check():
        raise RuntimeError("已手动暂停")

    prompt = sanitize_generation_prompt(prompt)
    if not prompt.strip():
        raise RuntimeError("语音生成需要提示词或朗读文本")

    base_url = style.normalize_voice_base_url(
        str(model_cfg.get("speech_base_url") or model_cfg.get("base_url") or DEFAULT_SPEECH_BASE)
    )
    model_cfg = {**model_cfg, "base_url": base_url}

    refs = [r.__dict__ for r in parse_reference_payload(references, reference_images)]
    limit_cfg = {**model_cfg, "kind": "voice", "max_videos": 0}
    refs = style.limit_references(refs, limit_cfg) if refs else []
    resolved_refs = await style.resolve_references(refs, model_cfg) if refs else []

    body = ve_params.build_voice_body(
        model_name=str(model_cfg.get("model") or model_cfg.get("name") or "seed-audio-1.0"),
        prompt=prompt,
        model_cfg=model_cfg,
        resolved_refs=resolved_refs,
    )
    headers = style.voice_auth_headers(model_cfg)
    if not headers.get("X-Api-Key"):
        raise RuntimeError("语音未配置 API Key（openspeech 使用 X-Api-Key，勿填方舟 Bearer）")

    url = common.join_url(base_url, style.voice_create_path)
    logger.info(
        "MyApi voice POST %s style=%s model=%s refs=%d req=%s",
        url,
        style.style_id,
        body.get("model"),
        len(body.get("references") or []),
        headers.get("X-Api-Request-Id", "")[:8],
    )

    # Seed-Audio can take tens of seconds for director-style prompts (≤120s audio).
    timeout = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise RuntimeError("语音请求超时（Seed-Audio 同步生成），请缩短提示词后重试") from exc
        if resp.status_code >= 400:
            humanize = getattr(style, "humanize_voice_create_error", None) or style.humanize_image_create_error
            err = humanize(resp.status_code, common.extract_error(resp))
            raise common.MyApiUpstreamError(
                f"语音生成失败：{err}",
                raw_response=common.parse_response_body(resp),
                http_status=resp.status_code,
            )
        data = resp.json() if resp.content else {}

    if cancel_check and cancel_check():
        raise RuntimeError("已手动暂停")

    if not isinstance(data, dict):
        data = {}
    code = data.get("code")
    if code is not None:
        try:
            code_i = int(code)
        except (TypeError, ValueError):
            code_i = -1
        if code_i not in (0, 200):
            msg = ve_parse.extract_task_error(data) or str(data.get("message") or f"code={code}")
            raise common.MyApiUpstreamError(f"语音生成失败：{msg}", raw_response=data)

    audio_url, duration = ve_parse.extract_audio_result(data)
    if not audio_url:
        raise common.MyApiUpstreamError(
            "语音未返回音频（需 sync 响应含 audio base64 或 url）",
            raw_response=compact_diagnostic(data),
        )

    saved = await common.download_to_uploads(
        audio_url, prefix="myapi_aud", headers=None, expected_kind="audio"
    )
    return {
        "audio_url": saved["url"],
        "duration": float(duration or 0),
        "url": saved["url"],
    }


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
    cfg = get_model(model_id)
    if not cfg:
        raise RuntimeError(f"未找到 MyApi 模型：{model_id}")
    if not cfg.get("api_key"):
        raise RuntimeError(f"MyApi 模型「{cfg.get('name')}」未配置密钥")
    if kind == "image":
        return await generate_image(
            model_cfg=cfg,
            prompt=prompt,
            ratio=ratio,
            resolution=resolution,
            style_name=style,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    if kind == "text":
        return await generate_text(
            model_cfg=cfg,
            prompt=prompt,
            references=references,
            reference_images=reference_images,
        )
    if kind == "voice":
        return await generate_voice(
            model_cfg=cfg,
            prompt=prompt,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    if kind == "video":
        return await generate_video(
            model_cfg=cfg,
            prompt=prompt,
            ratio=ratio,
            duration=duration,
            resolution=resolution,
            references=references,
            reference_images=reference_images,
            cancel_check=cancel_check,
            client_task_id=client_task_id,
        )
    raise RuntimeError(f"不支持的 MyApi kind: {kind}")


async def resume_media_poll(
    record: dict[str, Any],
    *,
    cancel_check=None,
) -> dict[str, Any]:
    """Resume an interrupted upstream poll from a poll_ledger record (no re-create)."""
    kind = str(record.get("kind") or "video").strip().lower()
    if kind not in ("video", "image"):
        raise RuntimeError(f"不支持恢复的 kind: {kind}")
    model_id = str(record.get("model") or "").strip()
    cfg = get_model(model_id)
    if not cfg:
        raise RuntimeError(f"未找到 MyApi 模型：{model_id}")
    if not cfg.get("api_key"):
        raise RuntimeError(f"MyApi 模型「{cfg.get('name')}」未配置密钥")
    style = get_style(cfg)
    from backend import service_settings

    if not service_settings.is_myapi_api_style_enabled(style.style_id):
        raise RuntimeError("供应商已失效，请去添加新的")
    base_url = style.normalize_base_url(str(record.get("base_url") or cfg.get("base_url") or ""))
    if not base_url:
        raise RuntimeError("轮询记录缺少 base_url")
    upstream = str(record.get("upstream_task_id") or "").strip()
    if not upstream:
        raise RuntimeError("轮询记录缺少 upstream_task_id")
    poll_url = str(record.get("poll_url") or "").strip()
    headers = common.auth_headers(str(cfg.get("api_key") or ""))
    job_id = str(record.get("job_id") or record.get("task_id") or "").strip()
    logger.info(
        "MyApi resume poll job=%s kind=%s upstream=%s url=%s",
        job_id[:12],
        kind,
        upstream[:16],
        (record.get("resolved_poll_url") or poll_url or "-")[:80],
    )
    remaining_s = remaining_poll_seconds(
        record.get("created_at"),
        DEFAULT_VIDEO_POLL_TIMEOUT_S if kind == "video" else DEFAULT_IMAGE_POLL_TIMEOUT_S,
    )
    if record.get("manual_recheck"):
        # Explicit recheck may inspect a late result, but never restarts an hour
        # of automatic waiting or creates a new upstream generation.
        remaining_s = max(remaining_s, 60.0)
    data = await _poll_with_ledger(
        kind=kind,
        client_task_id=job_id,
        model_cfg=cfg,
        style=style,
        base_url=base_url,
        headers=headers,
        upstream_task_id=upstream,
        poll_url=poll_url,
        cancel_check=cancel_check,
        poll_coro_factory=lambda c, progress: (
            poll_video_task(
                c,
                style=style,
                base_url=base_url,
                headers=headers,
                task_id=upstream,
                poll_url=poll_url,
                model_cfg=cfg,
                cancel_check=cancel_check,
                timeout_s=remaining_s,
                on_progress=progress,
            )
            if kind == "video"
            else poll_image_task(
                c,
                style=style,
                base_url=base_url,
                headers=headers,
                task_id=upstream,
                poll_url=poll_url,
                cancel_check=cancel_check,
                timeout_s=remaining_s,
                on_progress=progress,
            )
        ),
    )
    if kind == "video":
        video_url = style.extract_video_url(data)
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                resolved = await style.resolve_download_url(
                    dl_client,
                    base_url=base_url,
                    headers=headers,
                    task_id=upstream,
                    data=data if isinstance(data, dict) else {},
                )
            if resolved:
                video_url = resolved
        except Exception as exc:
            logger.warning("MyApi resume resolve_download_url failed: %s", exc)
        if not video_url:
            raise RuntimeError("恢复轮询完成但未返回视频地址")
        saved = await common.download_to_uploads(
            video_url, prefix="myapi_vid", expected_kind="video"
        )
        return {
            "video_url": saved["url"],
            "cover_url": saved.get("cover_url") or "",
            "duration": 0,
            "width": 0,
            "height": 0,
        }
    img_url = style.extract_image_url(data)
    if not img_url:
        raise RuntimeError("恢复轮询完成但未返回图片地址")
    if img_url.startswith("data:") or (len(img_url) > 200 and not img_url.startswith("http")):
        return common.save_b64_image(img_url)
    return await common.download_to_uploads(img_url, prefix="myapi_img", expected_kind="image")


async def test_connection(
    *,
    base_url: str,
    api_key: str,
    model: str | None = None,
    kind: str = "text",
    api_style: str | None = None,
    duration_min: int | None = None,
    duration_max: int | None = None,
) -> dict[str, Any]:
    """Probe OpenAI-compatible relay without generating billable media."""
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base:
        return {"ok": False, "message": "请填写 Base URL"}
    if not key:
        return {"ok": False, "message": "请填写 API Key（编辑已保存模型时可留空复用）"}

    headers = common.auth_headers(key)
    model_name = (model or "").strip() or "gpt-3.5-turbo"
    kind = (kind or "text").strip().lower()
    style_id = str(api_style or "").strip().lower()
    notes: list[str] = []

    def _auth_fail(status: int, err: str) -> dict[str, Any]:
        return {
            "ok": False,
            "message": f"密钥无效或无权限（HTTP {status}）{(' · ' + err) if err else ''}",
            "status": status,
        }

    def _auth_ok(status: int, detail: str = "") -> dict[str, Any]:
        msg = f"连接成功（HTTP {status}）"
        if detail:
            msg += f" · {detail}"
        return {"ok": True, "message": msg, "status": status}

    def _business_err_markers(err: str) -> bool:
        low = (err or "").lower()
        markers = (
            "model",
            "prompt",
            "invalid",
            "missing",
            "required",
            "parameter",
            "不存在",
            "未知",
            "unsupported",
            "not supported",
            "supported values",
            "too short",
            "duration",
            "seconds",
            "ratio",
            "resolution",
            "size",
            "image",
            "video",
            "audio",
            "不支持",
            "秒",
            "余额",
            "quota",
            "balance",
            "credit",
        )
        return any(m in low for m in markers)

    def _looks_like_auth_ok_but_bad_request(status: int, err: str) -> bool:
        # Fanke often wraps parameter validation as 502 from the gateway.
        allowed = (400, 402, 404, 405, 415, 422, 500, 502) if style_id == "fanke" else (400, 404, 405, 415, 422)
        if status not in allowed:
            return False
        low = (err or "").lower()
        if low in ("not found", "404", "404 not found") and status == 404:
            return False
        if status == 402:
            return True
        return _business_err_markers(err)

    def _response_err(resp: httpx.Response) -> str:
        err = common.extract_error(resp) if resp.status_code >= 400 else ""
        if err:
            return err
        try:
            data = resp.json()
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        if data.get("success") is False:
            for key in ("errorMessage", "error_message", "error", "message"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ""

    if style_id == "fanke":
        from backend.myapi.styles import get_style as _get_style

        base = _get_style({"api_style": "fanke"}).normalize_base_url(base)

    # Prefer configured duration for fanke probe; fall back to a common value.
    try:
        dmin = int(duration_min) if duration_min is not None else 0
    except (TypeError, ValueError):
        dmin = 0
    try:
        dmax = int(duration_max) if duration_max is not None else 0
    except (TypeError, ValueError):
        dmax = 0
    if dmin <= 0 and dmax <= 0:
        probe_dur = 5
    elif dmax > 0:
        probe_dur = dmax
    else:
        probe_dur = dmin

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        if style_id != "fanke":
            models_url = common.join_url(base, "v1/models")
            try:
                resp = await client.get(models_url, headers=headers)
                err = common.extract_error(resp) if resp.status_code >= 400 else ""
                if resp.status_code in (401, 403):
                    return _auth_fail(resp.status_code, err)
                if resp.status_code < 400:
                    detail = ""
                    try:
                        data = resp.json()
                        items = data.get("data") if isinstance(data, dict) else None
                        if isinstance(items, list) and items:
                            ids = [
                                str(x.get("id") or "")
                                for x in items[:5]
                                if isinstance(x, dict) and x.get("id")
                            ]
                            if model_name and any(i == model_name for i in ids):
                                detail = f"已找到模型 {model_name}"
                            elif ids:
                                detail = f"可见 {', '.join(ids[:3])}"
                                if len(items) > 3:
                                    detail += "…"
                                if model_name and model_name not in ids:
                                    detail += f"；列表未直接包含「{model_name}」（仍可能可用）"
                    except Exception:
                        pass
                    return _auth_ok(resp.status_code, detail)
                notes.append(f"GET /models → {resp.status_code}")
            except httpx.TimeoutException:
                return {"ok": False, "message": "连接超时，请检查 URL 或网络"}
            except httpx.RequestError as exc:
                return {"ok": False, "message": f"无法连接：{exc.__class__.__name__}"}

        probes: list[tuple[str, str, dict[str, Any]]] = []
        if kind == "video":
            fanke_probe = (
                "POST /api/open/v1/video/generate",
                common.join_url(base, "api/open/v1/video/generate"),
                {
                    "model": model_name,
                    "prompt": "ping",
                    "ratio": "9:16",
                    "duration": probe_dur,
                    "imageUrls": [],
                    "videoUrls": [],
                    "audioUrls": [],
                },
            )
            if style_id == "fanke":
                probes = [fanke_probe]
            else:
                probes = [
                    ("POST /videos", common.join_url(base, "v1/videos"), {"model": model_name, "prompt": "ping"}),
                    (
                        "POST /videos/generations",
                        common.join_url(base, "v1/videos/generations"),
                        {"model": model_name, "prompt": "ping"},
                    ),
                    (
                        "POST /video/generations",
                        common.join_url(base, "v1/video/generations"),
                        {"model": model_name, "prompt": "ping"},
                    ),
                    fanke_probe,
                ]
        elif kind == "image":
            probes = [
                (
                    "POST /images/generations",
                    common.join_url(base, "v1/images/generations"),
                    {"model": model_name, "prompt": "ping", "n": 1},
                ),
            ]
        else:
            probes = [
                (
                    "POST /chat/completions",
                    common.join_url(base, "v1/chat/completions"),
                    {
                        "model": model_name,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                ),
            ]

        if kind != "text" and style_id != "fanke":
            probes.append(
                (
                    "POST /chat/completions",
                    common.join_url(base, "v1/chat/completions"),
                    {
                        "model": model_name,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                )
            )

        last_err = ""
        for label, url, body in probes:
            try:
                resp = await client.post(url, headers=headers, json=body)
                err = _response_err(resp)
                if resp.status_code in (401, 403):
                    return _auth_fail(resp.status_code, err)
                # HTTP 200 but Fanke success:false with a parameter complaint still proves auth/route.
                if resp.status_code < 400:
                    try:
                        data = resp.json() if resp.content else {}
                    except Exception:
                        data = {}
                    if isinstance(data, dict) and data.get("success") is False:
                        biz = err or str(data.get("errorMessage") or data.get("error") or "业务错误")
                        if _looks_like_auth_ok_but_bad_request(400, biz):
                            return _auth_ok(
                                resp.status_code,
                                f"密钥有效（{label} 返回业务错误：{biz[:100]}）",
                            )
                        notes.append(f"{label} → success=false")
                        last_err = biz
                        continue
                    return _auth_ok(resp.status_code, f"{label} 可用")
                if _looks_like_auth_ok_but_bad_request(resp.status_code, err):
                    return _auth_ok(
                        resp.status_code,
                        f"密钥有效（{label} 返回业务错误：{err[:100]}）",
                    )
                notes.append(f"{label} → {resp.status_code}")
                last_err = err or f"HTTP {resp.status_code}"
            except httpx.TimeoutException:
                notes.append(f"{label} → 超时")
                last_err = "超时"
            except httpx.RequestError as exc:
                notes.append(f"{label} → {exc.__class__.__name__}")
                last_err = exc.__class__.__name__

        hint = "；".join(notes[-3:]) if notes else ""
        if style_id == "fanke":
            tip = "请确认 Base URL（如 https://ai.fanke2026.xyz，带不带 /v1 均可）与密钥正确；探测不会真正计费成功，参数报错也通常表示已连通。"
        else:
            tip = "请确认 Base URL 以 /v1 结尾、密钥正确；视频站若无 /models 也可能仍能生成。"
        return {
            "ok": False,
            "message": (
                f"探测未通过：{last_err or '未知错误'}"
                + (f"（{hint}）" if hint else "")
                + f"。{tip}"
            ),
        }
