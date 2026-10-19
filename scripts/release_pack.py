"""Release manifest + incremental update pack.

Compares the current build folder with the previous manifest and produces
an update zip containing only added/changed files (same relative paths).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Never track browser binary caches or per-machine runtime data.
SKIP_DIR_NAMES = {
    ".playwright-browsers",
    "browser_channels",
    "__pycache__",
    ".git",
    "data",
}
SKIP_FILE_NAMES = {"PACK_MANIFEST.json", "UPDATE.json"}


def _should_skip_rel(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return False


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_release_folder(folder: Path, product: str) -> dict:
    folder = folder.resolve()
    if not folder.is_dir():
        raise SystemExit(f"release folder not found: {folder}")

    files: dict[str, dict] = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if _should_skip_rel(rel):
            continue
        if path.name in SKIP_FILE_NAMES:
            continue
        stat = path.stat()
        files[rel] = {
            "size": stat.st_size,
            "sha256": _file_sha256(path),
        }

    return {
        "product": product,
        "root": folder.name,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "files": files,
    }


def compare_manifests(previous: dict, current: dict) -> dict:
    prev_files = previous.get("files") or {}
    curr_files = current.get("files") or {}

    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []

    for rel, meta in curr_files.items():
        old = prev_files.get(rel)
        if old is None:
            added.append(rel)
        elif old.get("sha256") != meta.get("sha256"):
            changed.append(rel)

    for rel in prev_files:
        if rel not in curr_files:
            removed.append(rel)

    added.sort()
    changed.sort()
    removed.sort()
    return {
        "added": added,
        "changed": changed,
        "removed": removed,
        "update_files": sorted(set(added + changed)),
    }


def _manifest_dir(dist_root: Path) -> Path:
    out = dist_root / "release-manifests"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_update_zip(
    folder: Path,
    rel_files: list[str],
    zip_path: Path,
    changelog: dict,
) -> float:
    folder = folder.resolve()
    zip_path = zip_path.resolve()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.unlink(missing_ok=True)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "UPDATE.json",
            json.dumps(changelog, ensure_ascii=False, indent=2),
        )
        for rel in rel_files:
            src = folder / rel.replace("/", "\\")
            if not src.is_file():
                continue
            zf.write(src, rel)

    return zip_path.stat().st_size / 1024 / 1024


def _load_version_meta(folder: Path) -> dict:
    for name in ("version.json", "build_profile.json"):
        path = folder / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "version": data.get("version"),
                    "build": data.get("build"),
                }
        except Exception:
            continue
    return {}


def finalize_release(folder: Path, product: str, full_zip: Path | None = None) -> dict:
    folder = folder.resolve()
    dist_root = folder.parent
    manifest_dir = _manifest_dir(dist_root)
    latest_path = manifest_dir / f"{product}-latest.json"
    stamped_path = manifest_dir / f"{product}-{_utc_stamp()}.json"

    current = scan_release_folder(folder, product)
    previous = None
    if latest_path.is_file():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception:
            previous = None

    # Save manifest beside the release for reference.
    pack_manifest = {
        "product": product,
        "built_at": current["built_at"],
        "file_count": current["file_count"],
        "previous_built_at": (previous or {}).get("built_at"),
        "full_zip": full_zip.name if full_zip else None,
    }
    _write_json(folder / "PACK_MANIFEST.json", pack_manifest)

    result: dict = {
        "product": product,
        "folder": str(folder),
        "file_count": current["file_count"],
        "manifest_latest": str(latest_path),
        "manifest_stamp": str(stamped_path),
        "update_zip": None,
        "diff": None,
    }

    if previous:
        diff = compare_manifests(previous, current)
        result["diff"] = diff
        update_zip = dist_root / f"{product}-update-{_utc_stamp()}.zip"
        version_meta = _load_version_meta(folder)
        file_hashes = {
            rel: current["files"][rel]["sha256"]
            for rel in diff["update_files"]
            if rel in current["files"]
        }
        exe_in_pack = "CanvasServer.exe" in diff["update_files"]
        changelog = {
            "product": product,
            "type": "incremental_update",
            "version": version_meta.get("version"),
            "build": version_meta.get("build"),
            "from_built_at": previous.get("built_at"),
            "to_built_at": current["built_at"],
            "added": diff["added"],
            "changed": diff["changed"],
            "removed": diff["removed"],
            "file_hashes": file_hashes,
            "exe_in_pack": exe_in_pack,
            "instructions": (
                "1. 关闭正在运行的画布服务器\n"
                "2. 双击「应用更新.bat」并选择本 zip，或解压覆盖到安装目录\n"
                "3. 更新包内列出的所有文件都必须覆盖（含 CanvasServer.exe 若在其中）\n"
                "4. removed 列表中的旧文件会被应用脚本自动删除\n"
                "5. 重新启动服务器，浏览器 Ctrl+F5 强刷\n"
                "6. data/ 目录不会被更新包修改"
            ),
        }
        if diff["update_files"]:
            size_mb = make_update_zip(folder, diff["update_files"], update_zip, changelog)
            result["update_zip"] = str(update_zip)
            result["update_size_mb"] = round(size_mb, 2)
            result["update_file_count"] = len(diff["update_files"])
            print(
                f"update pack OK: {update_zip} "
                f"({result['update_file_count']} files, {size_mb:.1f} MB)"
            )
            print(f"  added={len(diff['added'])} changed={len(diff['changed'])} removed={len(diff['removed'])}")
        else:
            print("update pack skipped: no file changes since last build")
    else:
        print("first release for this product — saved manifest, no update pack yet")

    _write_json(latest_path, current)
    _write_json(stamped_path, current)
    shutil.copy2(latest_path, manifest_dir / f"{product}-previous.json")
    print(f"manifest saved: {latest_path}")
    return result


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: release_pack.py finalize <release_folder> --product <Name> [--full-zip <zip>]"
        )
    if sys.argv[1] != "finalize":
        raise SystemExit("only subcommand supported: finalize")

    folder = Path(sys.argv[2])
    product = "CanvasServer"
    full_zip: Path | None = None
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        if args[i] == "--product" and i + 1 < len(args):
            product = args[i + 1]
            i += 2
            continue
        if args[i] == "--full-zip" and i + 1 < len(args):
            full_zip = Path(args[i + 1])
            i += 2
            continue
        raise SystemExit(f"unknown arg: {args[i]}")

    finalize_release(folder, product, full_zip=full_zip)


if __name__ == "__main__":
    main()
