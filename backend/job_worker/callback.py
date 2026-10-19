"""Apply worker callbacks to canvas task records (8088)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend import gen_records, task_manager
from backend.models import TaskStatus

logger = logging.getLogger(__name__)


def _sync_gen_record(task_id: str, **fields: Any) -> None:
    try:
        gen_records.upsert_record(task_id, **fields)
    except Exception as exc:
        logger.debug("gen record sync failed task=%s: %s", task_id, exc)


def apply_worker_callback(payload: dict[str, Any]) -> dict[str, Any]:
    """Update a MediaTask from 8089 callback payload."""
    from backend.user_scope import reset_current_user_id, set_current_user_id

    task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
    user_id = str(payload.get("user_id") or "").strip()
    if not task_id:
        raise ValueError("task_id required")

    token = set_current_user_id(user_id or None)
    try:
        with task_manager.transaction():
            task = task_manager.get_task(task_id)
            if not task:
                # Permanent miss: caller should stop retrying (task deleted / cleaned up).
                logger.warning("worker callback: task %s not found (user=%s)", task_id, user_id)
                return {"ok": False, "reason": "task_not_found"}

            if int(payload.get("poll_attempt") or 0) != task.poll_attempt:
                return {"ok": True, "task_id": task_id, "applied": "ignored_stale_attempt"}
            status = str(payload.get("status") or "").strip().lower()
            # Reject stale terminal callbacks before changing records or billing.
            if task.status not in (TaskStatus.pending, TaskStatus.generating):
                return {"ok": True, "task_id": task_id, "applied": "ignored_terminal"}
            now = datetime.now().isoformat()
            if status == "cancelled":
                raw_err = str(payload.get("raw_error") or payload.get("error") or "已手动暂停")
                err = raw_err
                if task.status in (TaskStatus.pending, TaskStatus.generating):
                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.failed,
                        error=err,
                        completed_at=now,
                    )
                _sync_gen_record(
                    task_id,
                    status=TaskStatus.failed.value,
                    error=err,
                    raw_error=raw_err,
                    raw_response=payload.get("raw_response"),
                    completed_at=now,
                )
                try:
                    from backend import points

                    points.refund(task_id, reason="cancelled")
                except Exception:
                    pass
                return {"ok": True, "task_id": task_id, "applied": "cancelled"}

            if status == "completed":
                # Manual pause / fail already terminal — ignore late child completion.
                if task.status not in (TaskStatus.pending, TaskStatus.generating):
                    return {"ok": True, "task_id": task_id, "applied": "ignored_terminal"}
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                kind = str(payload.get("kind") or task.kind or "video")
                required_value = ""
                if kind == "video":
                    required_value = str(result.get("video_url") or "").strip()
                elif kind == "image":
                    required_value = str(result.get("url") or result.get("image_url") or "").strip()
                elif kind == "text":
                    required_value = str(result.get("text") or result.get("content") or "").strip()
                elif kind == "voice":
                    required_value = str(result.get("audio_url") or result.get("url") or "").strip()
                invalid_reason = ""
                if kind in ("video", "image", "text", "voice") and not required_value:
                    invalid_reason = f"任务服务返回 completed，但{kind}结果为空"
                elif kind in ("video", "image", "voice") and required_value.startswith("/uploads/"):
                    try:
                        from backend import board_assets

                        path = board_assets.resolve_upload_path(required_value)
                        if not path or not path.is_file():
                            invalid_reason = f"任务服务返回 completed，但本地结果文件不存在：{required_value}"
                    except Exception as exc:
                        invalid_reason = f"任务服务返回 completed，但无法校验结果文件：{exc}"
                if invalid_reason:
                    logger.error("invalid completed callback task=%s: %s", task_id, invalid_reason)
                    return apply_worker_callback(
                        {
                            **payload,
                            "status": "failed",
                            "invalid_completed": True,
                            "error": invalid_reason,
                            "raw_error": invalid_reason,
                            "raw_response": payload.get("raw_response") or result,
                            "result": {},
                        }
                    )
                fields: dict[str, Any] = {
                    "status": TaskStatus.completed,
                    "completed_at": now,
                    "error": None,
                }
                if kind == "video":
                    fields["video_url"] = result.get("video_url")
                    fields["cover_url"] = result.get("cover_url") or ""
                    fields["duration"] = result.get("duration") or 0
                    fields["width"] = result.get("width")
                    fields["height"] = result.get("height")
                    if result.get("share_link"):
                        fields["share_link"] = result.get("share_link")
                elif kind == "image":
                    img_url = result.get("url") or result.get("image_url")
                    if img_url:
                        try:
                            from backend import board_assets

                            img_url = board_assets.repair_flat_upload_url(str(img_url))
                            path = board_assets.resolve_upload_path(str(img_url))
                            if not path or not path.is_file():
                                logger.warning(
                                    "image file missing at callback task=%s url=%s",
                                    task_id,
                                    img_url,
                                )
                        except Exception as exc:
                            logger.debug("image url repair skipped task=%s: %s", task_id, exc)
                    fields["image_url"] = img_url
                    fields["width"] = result.get("width")
                    fields["height"] = result.get("height")
                    if result.get("thumb_url"):
                        fields["thumb_url"] = result.get("thumb_url")
                    elif img_url and str(img_url).startswith("/uploads/"):
                        try:
                            from backend import board_assets
                            from backend.media.thumbnails import thumb_urls_for_image

                            path = board_assets.resolve_upload_path(str(img_url))
                            if path and path.is_file():
                                fields.update({k: v for k, v in thumb_urls_for_image(path).items() if v})
                        except Exception as exc:
                            logger.debug("image thumb enrich skipped task=%s: %s", task_id, exc)
                    if result.get("share_link"):
                        fields["share_link"] = result.get("share_link")
                elif kind == "text":
                    fields["text"] = result.get("text") or result.get("content") or ""
                elif kind == "voice":
                    fields["audio_url"] = result.get("audio_url") or result.get("url") or ""
                    fields["duration"] = result.get("duration") or 0
                task_manager.update_task(task_id, **fields)
                _sync_gen_record(
                    task_id,
                    status=TaskStatus.completed.value,
                    error="",  # clear prior false-timeout text (upsert skips None)
                    raw_error="",
                    raw_response=payload.get("raw_response") or result,
                    completed_at=now,
                    account_id=task.account_id,
                    account_name=task.account_name,
                    error_type=payload.get("error_type"),
                    upstream_task_id=payload.get("upstream_task_id"),
                    upstream_status=payload.get("upstream_status"),
                    upstream_stage=payload.get("upstream_stage"),
                    last_poll_at=payload.get("last_poll_at"),
                )
                try:
                    from backend import points
                    from backend import public_models_store

                    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                    pin = usage.get("prompt_tokens")
                    cout = usage.get("completion_tokens")
                    settle_meta: dict[str, Any] = {}
                    if pin is not None:
                        settle_meta["prompt_tokens"] = int(pin)
                    if cout is not None:
                        settle_meta["completion_tokens"] = int(cout)
                    if usage.get("total_tokens") is not None:
                        settle_meta["total_tokens"] = int(usage.get("total_tokens"))

                    actual = None
                    row = points.get_reserve(task_id) or {}
                    reserve_meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
                    mid = str((reserve_meta or {}).get("model") or "").strip()
                    cfg = public_models_store.get_model(mid) if mid else None
                    mode = str((cfg or {}).get("billing_mode") or "").lower()
                    if mode == "per_char":
                        mode = "per_token"
                    if cfg and cfg.get("kind") == "text" and mode == "per_token" and cfg.get("points_required"):
                        actual = points.compute_cost(
                            cfg,
                            prompt_tokens=int(pin) if pin is not None else None,
                            completion_tokens=int(cout) if cout is not None else None,
                        )
                    points.commit(task_id, actual_amount=actual, meta=settle_meta or None)
                except Exception:
                    pass
                return {"ok": True, "task_id": task_id, "applied": "completed"}

            if status == "failed":
                err_raw = payload.get("error")
                raw_err = payload.get("raw_error")
                raw_response = payload.get("raw_response")
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                # False timeout: media already present — treat as success.
                has_media = not payload.get("invalid_completed") and bool(
                    result.get("video_url")
                    or result.get("url")
                    or result.get("image_url")
                    or (isinstance(result.get("text"), str) and result.get("text").strip())
                    or (isinstance(raw_response, dict) and (
                        raw_response.get("video_url")
                        or raw_response.get("url")
                        or raw_response.get("image_url")
                    ))
                )
                if has_media:
                    media_result = result or (raw_response if isinstance(raw_response, dict) else {})
                    return apply_worker_callback(
                        {
                            **payload,
                            "status": "completed",
                            "result": media_result,
                            "error": None,
                            "raw_error": None,
                            "raw_response": media_result,
                        }
                    )
                err = str(err_raw or raw_err or "生成失败")
                try:
                    from backend.main import _sanitize_task_error

                    kind = str(payload.get("kind") or task.kind or "video")
                    channel = str(payload.get("channel") or "").strip().lower()
                    source = (
                        "browser"
                        if channel in ("browser", "doubao", "doubao_s", "dola", "dola_s", "gpt")
                        else "myapi"
                    )
                    # Browser jobs rewrite kind to video|image|text before callback; channel may be absent.
                    if source == "myapi" and task.account_id and task.account_id not in ("myapi", "bendi"):
                        source = "browser"
                    err = _sanitize_task_error(str(err_raw or raw_err or "生成失败"), kind=kind, source=source)
                except Exception:
                    pass
                if not raw_err and err_raw and str(err_raw).strip() != str(err).strip():
                    raw_err = err_raw
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.failed,
                    error=err,
                    completed_at=now,
                )
                _sync_gen_record(
                    task_id,
                    status=TaskStatus.failed.value,
                    error=err,
                    raw_error=raw_err if raw_err is not None else None,
                    raw_response=raw_response,
                    completed_at=now,
                    account_id=task.account_id,
                    account_name=task.account_name,
                    error_type=payload.get("error_type"),
                    upstream_task_id=payload.get("upstream_task_id"),
                    upstream_status=payload.get("upstream_status"),
                    upstream_stage=payload.get("upstream_stage"),
                    last_poll_at=payload.get("last_poll_at"),
                )
                try:
                    from backend import points

                    points.refund(task_id, reason="failed")
                except Exception:
                    pass
                return {"ok": True, "task_id": task_id, "applied": "failed"}

            if status in ("pending", "processing", "generating"):
                if task.status not in (TaskStatus.pending, TaskStatus.generating):
                    return {"ok": True, "task_id": task_id, "applied": "ignored_terminal"}
                fields: dict[str, Any] = {}
                if task.status == TaskStatus.pending:
                    fields["status"] = TaskStatus.generating
                if payload.get("prompt_sent") is True:
                    fields["prompt_sent"] = True
                if fields:
                    task_manager.update_task(task_id, **fields)
                patch: dict[str, Any] = {"status": TaskStatus.generating.value}
                if payload.get("account_id"):
                    patch["account_id"] = payload.get("account_id")
                if payload.get("account_name"):
                    patch["account_name"] = payload.get("account_name")
                for key in (
                    "upstream_task_id",
                    "upstream_status",
                    "upstream_stage",
                    "last_poll_at",
                ):
                    if payload.get(key) is not None:
                        patch[key] = payload.get(key)
                _sync_gen_record(task_id, **patch)
                return {"ok": True, "task_id": task_id, "applied": "progress"}

            return {"ok": False, "reason": f"unknown_status:{status}"}
    finally:
        reset_current_user_id(token)
