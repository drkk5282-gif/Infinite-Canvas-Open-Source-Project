"""Authenticated API for sharing and batch-importing projects."""
import asyncio
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.auth_deps import require_login_user
from backend import share_archive as packages

router = APIRouter(prefix="/api/workspace/packages")


@router.get("/export-preview")
def export_preview(kind: str, id: str):
    user = require_login_user()
    try:
        return packages.export_plan(user["id"], kind, id)["summary"]
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/export")
async def export_project(body: dict):
    user = require_login_user()
    try:
        return await asyncio.to_thread(
            packages.prepare_export, user["id"], str(body.get("kind", "")), str(body.get("id", ""))
        )
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"分享导出失败：{exc}") from exc


@router.get("/export/{token}")
def download_export(token: str, background_tasks: BackgroundTasks):
    try:
        package = packages.take_export(require_login_user()["id"], token)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    background_tasks.add_task(package["temp"].cleanup)
    return FileResponse(package["path"], filename=package["filename"],
                        media_type="application/zip", background=background_tasks)


@router.post("/import-preview")
async def import_preview(files: list[UploadFile] = File(...)):
    user = require_login_user()
    temp = tempfile.TemporaryDirectory(prefix="qingzi-import-")
    try:
        if not 1 <= len(files) <= packages.MAX_PACKAGES:
            raise ValueError("每批请选择 1–20 个分享压缩包")
        paths = []
        total = 0
        for index, upload in enumerate(files):
            filename = upload.filename or ""
            if not filename.lower().endswith(".zip"):
                raise ValueError("请选择 .zip 分享压缩包")
            path = Path(temp.name) / f"{index}.zip"
            size = 0
            with path.open("wb") as dest:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > packages.MAX_ARCHIVE_BYTES or total > packages.MAX_BATCH_BYTES:
                        raise ValueError("文件过大，请分批导入（单包最多 8GB，单批最多 16GB）")
                    await asyncio.to_thread(dest.write, chunk)
            paths.append((Path(filename).name, path))
        return await asyncio.to_thread(packages.stage_preview, user["id"], temp, paths)
    except (ValueError, OSError) as exc:
        temp.cleanup()
        raise HTTPException(400, f"导入预览失败：{exc}") from exc
    except BaseException:
        temp.cleanup()
        raise
    finally:
        for upload in files:
            await upload.close()


@router.post("/import")
async def import_projects(body: dict):
    user = require_login_user()
    try:
        return await asyncio.to_thread(packages.import_preview, user["id"], str(body.get("token", "")))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f"导入失败：{exc}") from exc


@router.delete("/import-preview/{token}")
def discard_import_preview(token: str):
    packages.discard_preview(require_login_user()["id"], token)
    return {"ok": True}
