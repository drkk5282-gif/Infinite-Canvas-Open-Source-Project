"""Portable, self-contained canvas/workbench ZIP packages.

Media is streamed from disk; imports use new document IDs and verify the entire
batch before publishing indexes. No task or provider configuration is imported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from backend import board_assets, canvas_store
from backend.json_io import load_json, save_json
from backend.user_scope import user_data_root

FORMAT = "qingzi-workspace"
VERSION = 1
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_BATCH_BYTES = 16 * 1024**3
MAX_JSON_BYTES = 32 * 1024**2
MAX_FILES = 20000
MAX_PACKAGES = 20
PREVIEW_TTL = 30 * 60
MEDIA_KEYS = frozenset({
    "imageUrl", "videoUrl", "audioUrl", "coverUrl", "thumbUrl", "coverThumbUrl",
    "fileUrl", "tailFrameUrl", "refImageUrl", "aiMarkBaseUrl", "aiMarkBakedUrl",
    "url", "src", "_media", "local_url", "oss_url", "originalUrl", "previewUrl",
    "image_url", "video_url", "audio_url", "cover_url", "thumb_url", "canvasBgUrl",
    "worldModelUrl", "worldThumbnailUrl", "worldPanoUrl", "worldSplatUrl",
    "model_url", "mesh_url", "glb_url", "collider_mesh_url", "full_res_mesh_url", "hq_mesh_url",
    "pano_url", "panorama_url", "panoUrl", "splat_url", "spz_url", "preview_image_url",
})
PRIVATE_KEYS = frozenset({
    "api_key", "apiKey", "authorization", "password", "access_token", "refresh_token",
    "shareOwnerId", "shareOwnerUsername", "sharedFrom", "isOwned", "nodesHydrated", "_lite",
    "taskId", "task_id", "_genPayload", "_genBatchId", "pendingAssetDeletes",
})
SECRET_KEYS = frozenset({"api_key", "apikey", "authorization", "cookie", "set-cookie",
                         "password", "secret", "access_token", "refresh_token", "x-api-key"})
_LOCAL_REF = re.compile(r'(?:https?://[^\s"\'<>]+)?/uploads/[^\s"\'<>]+')
_PACKAGE_REF = re.compile(r"qingzi-asset:assets/[A-Za-z0-9_-]+\.[a-z0-9]{1,10}")
_staged: dict[str, dict[str, Any]] = {}
_exports: dict[str, dict[str, Any]] = {}
_stage_lock = threading.Lock()


def _expire_transfers() -> None:
    for registry in (_staged, _exports):
        expired = [k for k, v in registry.items() if time.monotonic() - v["created"] > PREVIEW_TTL]
        for key in expired:
            registry.pop(key)["temp"].cleanup()


def prepare_export(user_id: str, kind: str, doc_id: str) -> dict:
    path, temp, filename = export_archive(user_id, kind, doc_id)
    token = uuid.uuid4().hex
    with _stage_lock:
        _expire_transfers()
        _exports[token] = {"user": user_id, "temp": temp, "path": path,
                           "filename": filename, "created": time.monotonic()}
    return {"download_url": f"/api/workspace/packages/export/{token}",
            "filename": filename, "bytes": path.stat().st_size}


def take_export(user_id: str, token: str) -> dict:
    with _stage_lock:
        _expire_transfers()
        package = _exports.get(token)
        if not package or package["user"] != user_id:
            raise ValueError("分享下载已过期，请重新导出")
        return _exports.pop(token)


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 100:
        raise ValueError("项目数据嵌套过深")
    if isinstance(value, dict):
        out = {k: _clean(v, depth + 1) for k, v in value.items()
               if k not in PRIVATE_KEYS and k.lower() not in SECRET_KEYS
               and not k.lower().endswith("_api_key") and not k.startswith("_runtime")}
        if out.get("status") in ("pending", "generating", "waiting", "uploading", "running"):
            out["status"] = "idle"
        return out
    if isinstance(value, list):
        return [_clean(v, depth + 1) for v in value]
    return value


def _validate_document(doc: dict) -> None:
    row_lists = {"nodes", "edges", "connections", "groups", "assets", "episodes", "clips", "studioItems", "genHistory", "editHistory"}
    def validate(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in row_lists and (not isinstance(item, list) or any(not isinstance(row, dict) for row in item)):
                    raise ValueError(f"项目中的 {key} 格式错误")
                validate(item)
        elif isinstance(value, list):
            for item in value: validate(item)
    validate(doc)


def _strings(value: Any, key: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, key)
    elif isinstance(value, str):
        # Embedded local thumbnails in rich-text prompts also need relocation.
        for match in _LOCAL_REF.finditer(value):
            yield match.group(0)
        if key in MEDIA_KEYS and value.startswith(("https://", "http://")):
            yield value


def _rewrite(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, mapping) for v in value]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        # Only replace complete references, not URL prefixes in unrelated text.
        value = _LOCAL_REF.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
        return _PACKAGE_REF.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
    return value


def _document(user_id: str, kind: str, doc_id: str) -> dict:
    if kind != "canvas":
        raise ValueError("请选择画布")
    owned = any(
        isinstance(r, dict) and str(r.get("id") or "") == doc_id
        for r in (canvas_store.load_owned(user_id).get("boards") or [])
    )
    if not owned:
        raise ValueError("项目不存在或无权分享")
    row = canvas_store.get_board(user_id, doc_id)
    if row is None:
        raise ValueError("项目不存在或无权分享")
    return _clean(copy.deepcopy(row))


def export_plan(user_id: str, kind: str, doc_id: str) -> dict:
    doc = _document(user_id, kind, doc_id)
    _validate_document(doc)
    assets: dict[str, dict] = {}
    identities: dict[str, dict] = {}
    missing: list[str] = []
    local_bytes = 0
    root = user_data_root(user_id).resolve()
    for source in sorted(set(_strings(doc))):
        upload_ref = board_assets.upload_ref_from_url(source)
        path = board_assets.resolve_upload_path(f"/uploads/{upload_ref}") if upload_ref else None
        if not upload_ref:
            from backend.myapi.media import local_upload_path

            path = local_upload_path(source)
        if path:
            # Shared references to an owned document are allowed; unrelated local
            # configuration files can never be resolved as media by this plan.
            path = path.resolve()
            if not path.is_relative_to(root) and not path.is_relative_to(board_assets.UPLOADS_DIR.resolve()):
                raise ValueError("素材不属于当前用户")
            size = path.stat().st_size
        elif upload_ref:
            missing.append(Path(upload_ref).name)
            continue
        else:
            size = None
        suffix = Path(source.split("?", 1)[0]).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        identity = str(path) if path else source
        if identity not in identities:
            if path: local_bytes += size
            name = hashlib.sha256(identity.encode()).hexdigest()[:24] + suffix
            identities[identity] = {"path": path, "size": size, "name": f"assets/{name}", "source": source}
        assets[source] = identities[identity]
    metadata_bytes = len(_json_bytes(doc))
    if metadata_bytes > MAX_JSON_BYTES:
        raise ValueError("项目元数据超过 32MB，请先减少历史记录")
    if len(identities) + 2 > MAX_FILES:
        raise ValueError("项目素材文件过多，请拆分项目")
    if local_bytes + metadata_bytes + len(identities) * 250 + 2048 > MAX_ARCHIVE_BYTES:
        raise ValueError("分享包超过 8GB，请拆分项目")
    return {"kind": kind, "document": doc, "assets": assets, "summary": {
        "kind": kind, "id": doc_id, "name": str(doc.get("name") or "未命名"),
        "node_count": len(doc.get("nodes") or []),
        "asset_count": len(doc.get("assets") or []),
        "episode_count": len(doc.get("episodes") or []),
        "clip_count": sum(len(e.get("clips") or []) for e in doc.get("episodes", []) if isinstance(e, dict)),
        "file_count": len(identities), "local_bytes": local_bytes,
        "remote_count": sum(a["path"] is None for a in identities.values()),
        "estimated_zip_bytes": local_bytes + metadata_bytes + len(identities) * 250 + 2048,
        "missing_files": missing,
    }}


def export_archive(user_id: str, kind: str, doc_id: str) -> tuple[Path, tempfile.TemporaryDirectory, str]:
    plan = export_plan(user_id, kind, doc_id)
    if plan["summary"]["missing_files"]:
        raise ValueError("有素材文件丢失，请先恢复素材后再分享")
    temp = tempfile.TemporaryDirectory(prefix="qingzi-share-")
    try:
        archive_path = Path(temp.name) / "project.zip"
        mapping = {src: f"qingzi-asset:{a['name']}" for src, a in plan["assets"].items()}
        doc = _rewrite(plan["document"], mapping)
        document_bytes = _json_bytes(doc)
        metadata_budget = len(document_bytes) + len({a["name"] for a in plan["assets"].values()}) * 250 + 2048
        entries = []
        total = 0
        with zipfile.ZipFile(archive_path, "w", allowZip64=True) as archive, \
                httpx.Client(timeout=httpx.Timeout(120, connect=30), follow_redirects=True) as client:
            unique_assets = {asset["name"]: asset for asset in plan["assets"].values()}
            for asset in unique_assets.values():
                source = asset["source"]
                digest = hashlib.sha256()
                size = 0
                with archive.open(asset["name"], "w", force_zip64=True) as dest:
                    if asset["path"]:
                        with asset["path"].open("rb") as src:
                            while chunk := src.read(1024 * 1024):
                                size += len(chunk)
                                if total + size + metadata_budget > MAX_ARCHIVE_BYTES:
                                    raise ValueError("分享包超过 8GB，请拆分项目")
                                dest.write(chunk)
                                digest.update(chunk)
                    else:
                        from backend.myapi.media import _sign_oss_url
                        url = _sign_oss_url(source) or source
                        with client.stream("GET", url) as response:
                            response.raise_for_status()
                            for chunk in response.iter_bytes(1024 * 1024):
                                size += len(chunk)
                                if total + size + metadata_budget > MAX_ARCHIVE_BYTES:
                                    raise ValueError("分享包超过 8GB，请拆分项目")
                                dest.write(chunk)
                                digest.update(chunk)
                total += size
                entries.append({"path": asset["name"], "size": size, "sha256": digest.hexdigest()})
            archive.writestr("document.json", document_bytes, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("manifest.json", _json_bytes({
                "format": FORMAT, "version": VERSION, "kind": kind,
                "name": plan["summary"]["name"], "assets": entries,
            }), compress_type=zipfile.ZIP_DEFLATED)
        label = "画布" if kind == "canvas" else "工作台"
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", plan["summary"]["name"])[:80]
        return archive_path, temp, f"{safe_name}-{label}.zip"
    except BaseException:
        temp.cleanup()
        raise


def inspect_archive(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_FILES or len(set(names)) != len(names):
                raise ValueError("压缩包包含过多文件或重复路径")
            if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError("压缩包解压后超过 8GB")
            for info in infos:
                member = PurePosixPath(info.filename)
                if member.is_absolute() or ".." in member.parts or "\\" in info.filename or ":" in info.filename:
                    raise ValueError("压缩包包含不安全的文件路径")
                if (info.external_attr >> 16) & 0o170000 == 0o120000 or info.flag_bits & 1:
                    raise ValueError("不支持链接或加密压缩包")
            def read_json(name):
                info = archive.getinfo(name)
                if info.file_size > MAX_JSON_BYTES:
                    raise ValueError("压缩包元数据超过 32MB")
                return json.loads(archive.read(info))
            manifest, doc = read_json("manifest.json"), read_json("document.json")
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise ValueError("不是支持的小庆子分享包")
            kind = manifest.get("kind")
            if kind != "canvas" or not isinstance(doc, dict):
                raise ValueError("分享包类型或项目数据无效")
            field = "nodes" if kind == "canvas" else "assets"
            if not isinstance(doc.get(field), list) or (kind == "workbench" and not isinstance(doc.get("episodes", []), list)):
                raise ValueError("分享包缺少项目内容")
            for row_field in ("nodes", "edges", "groups") if kind == "canvas" else ("assets", "episodes"):
                rows = doc.get(row_field, [])
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("分享包项目内容格式错误")
            entries = manifest.get("assets")
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                raise ValueError("分享包素材列表无效")
            paths = set()
            for entry in entries:
                name = entry.get("path", "")
                if not re.fullmatch(r"assets/[A-Za-z0-9_-]+\.[a-z0-9]{1,10}", name) or name in paths:
                    raise ValueError("分享包素材路径无效或重复")
                info = archive.getinfo(name)
                if info.file_size != entry.get("size") or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("sha256", ""))):
                    raise ValueError("分享包素材大小或校验信息无效")
                paths.add(name)
            if set(names) != paths | {"document.json", "manifest.json"}:
                raise ValueError("分享包包含未登记文件")
            clean = _clean(doc)
            _validate_document(clean)
            if any(_strings(clean)):
                raise ValueError("分享包包含未打包的素材地址，请重新导出")
            def check_links(value):
                if isinstance(value, dict):
                    for v in value.values(): check_links(v)
                elif isinstance(value, list):
                    for v in value: check_links(v)
                elif isinstance(value, str):
                    for match in _PACKAGE_REF.finditer(value):
                        if match.group(0)[13:] not in paths:
                            raise ValueError("分享包引用了未包含的素材")
            check_links(clean)
            return {"manifest": manifest, "document": clean, "kind": kind,
                    "name": str(clean.get("name") or manifest.get("name") or "未命名"),
                    "file_count": len(paths), "bytes": path.stat().st_size,
                    "unpacked_bytes": sum(i.file_size for i in infos)}
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("分享包已损坏或缺少必要文件") from exc


def stage_preview(user_id: str, temp: tempfile.TemporaryDirectory, files: list[tuple[str, Path]]) -> dict:
    previews = []
    total = 0
    for filename, path in files:
        inspected = inspect_archive(path)
        total += inspected["unpacked_bytes"]
        if total > MAX_BATCH_BYTES:
            raise ValueError("本批文件解压后超过 16GB，请分批导入")
        previews.append({k: inspected[k] for k in ("kind", "name", "file_count", "bytes")}
                              | {"filename": filename})
    token = uuid.uuid4().hex
    with _stage_lock:
        _expire_transfers()
        _staged[token] = {"user": user_id, "temp": temp, "files": files, "created": time.monotonic()}
    return {"token": token, "files": previews}


def discard_preview(user_id: str, token: str) -> None:
    with _stage_lock:
        staged = _staged.get(token)
        if staged and staged["user"] == user_id:
            _staged.pop(token)["temp"].cleanup()


def import_preview(user_id: str, token: str) -> dict:
    with _stage_lock:
        staged = _staged.get(token)
        if not staged or staged["user"] != user_id:
            raise ValueError("导入预览已失效，请重新选择文件")
        _staged.pop(token)
    try:
        if time.monotonic() - staged["created"] > PREVIEW_TTL:
            raise ValueError("导入预览已过期，请重新选择文件")
        rows: dict[str, list[dict]] = {"canvas": []}
        folders = []
        for _, path in staged["files"]:
            package = inspect_archive(path)
            kind = package["kind"]
            doc_id = uuid.uuid4().hex[:16]
            doc = package["document"]
            original_id = doc.get("id")
            doc["id"] = doc_id
            stage_dir = Path(staged["temp"].name) / doc_id / "assets"
            stage_dir.mkdir(parents=True)
            if kind != "canvas":
                raise ValueError("工作台分享包已停用")
            media = board_assets
            mapping = {}
            with zipfile.ZipFile(path) as archive:
                for entry in package["manifest"]["assets"]:
                    filename = PurePosixPath(entry["path"]).name
                    digest = hashlib.sha256()
                    with archive.open(entry["path"]) as src, (stage_dir / filename).open("wb") as dest:
                        while chunk := src.read(1024 * 1024):
                            dest.write(chunk)
                            digest.update(chunk)
                    if digest.hexdigest() != entry["sha256"]:
                        raise ValueError(f"素材校验失败：{filename}")
                    mapping[f"qingzi-asset:{entry['path']}"] = media.public_url(user_id, doc_id, filename)
            doc = _rewrite(doc, mapping)
            def relocate(value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        if k in ("boardId", "board_id", "workbenchId", "workbench_id") and v == original_id:
                            value[k] = doc_id
                        else: relocate(v)
                elif isinstance(value, list):
                    for v in value: relocate(v)
            relocate(doc)
            rows[kind].append(doc)
            target = media.board_root_dir(user_id, doc_id)
            target = target.resolve()
            if not target.is_relative_to(user_data_root(user_id).resolve()) or target.exists():
                raise ValueError("导入目标目录无效")
            folders.append((stage_dir.parent, target))
        # All archives and file hashes are valid before any user files change.
        moved = []
        snapshots = []
        with ExitStack() as locks:
            locks.enter_context(canvas_store._user_save_lock(user_id))
            try:
                for kind, store, field, storage, cover in (
                    ("canvas", canvas_store, "boards", "per-board", "coverNodeId"),
                ):
                    if not rows[kind]: continue
                    index_path = store._canvas_path(user_id) if kind == "canvas" else store._index_path(user_id)
                    old = load_json(index_path, default=None)
                    snapshots.append((index_path, old))
                    if old is not None and (not isinstance(old, dict) or not isinstance(old.get(field), list)):
                        raise ValueError("项目索引异常，请先恢复索引")
                    index_rows = (old or {}).get(field, [])
                    # Preserve existing index rows exactly; importing one project
                    # must not rewrite or repair unrelated documents.
                    existing = index_rows
                    if not old or old.get("storage") != storage:
                        for row in existing:
                            if kind == "canvas": store._save_board_file(user_id, row)
                            else: store._save_workbench_file(user_id, row)
                    for stage_dir, target in folders:
                        if target.name in {r["id"] for r in rows[kind]}:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(stage_dir), str(target))
                            moved.append(target)
                    for row in rows[kind]:
                        if kind == "canvas": store._save_board_file(user_id, row)
                        else: store._save_workbench_file(user_id, row)
                    meta_keys = ("id", "name", "pinned", cover, "nodeCount", "coverThumbUrl") if kind == "canvas" else ("id", "name", "pinned", cover)
                    def metadata(row):
                        return store._board_index_entry(row) if kind == "canvas" else {k: row.get(k) for k in meta_keys}
                    index_rows = ((existing if old and old.get("storage") == storage else [metadata(r) for r in existing])
                                  + [metadata(r) for r in rows[kind]])
                    save_json(index_path, {"updatedAt": datetime.now().isoformat(timespec="microseconds"), "storage": storage, field: index_rows})
            except BaseException:
                for index_path, old in snapshots:
                    if old is None: index_path.unlink(missing_ok=True)
                    else: save_json(index_path, old)
                for target in moved:
                    if target.resolve().is_relative_to(user_data_root(user_id).resolve()):
                        shutil.rmtree(target)
                raise
        return {"ok": True, "boards": len(rows["canvas"]), "workbenches": 0,
                "items": [{"kind": k, "id": r["id"], "name": r.get("name")} for k in rows for r in rows[k]]}
    finally:
        staged["temp"].cleanup()
