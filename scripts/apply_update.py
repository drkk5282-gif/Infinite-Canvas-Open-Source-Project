"""Apply CanvasServer incremental update zip with overwrite + verify."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

SKIP_NAMES = {"UPDATE.json"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_update(install_dir: Path, update_zip: Path) -> dict:
    install_dir = install_dir.resolve()
    update_zip = update_zip.resolve()
    if not install_dir.is_dir():
        raise SystemExit(f"install dir not found: {install_dir}")
    if not update_zip.is_file():
        raise SystemExit(f"update zip not found: {update_zip}")

    with zipfile.ZipFile(update_zip) as zf:
        if "UPDATE.json" not in zf.namelist():
            raise SystemExit("invalid update zip: UPDATE.json missing")
        changelog = json.loads(zf.read("UPDATE.json").decode("utf-8"))
        names = [n for n in zf.namelist() if n not in SKIP_NAMES and not n.endswith("/")]
        extracted: list[str] = []
        for rel in names:
            dest = install_dir / rel.replace("/", "\\")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(rel) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            extracted.append(rel)

    removed: list[str] = []
    for rel in changelog.get("removed") or []:
        target = install_dir / rel.replace("/", "\\")
        if target.is_file():
            target.unlink()
            removed.append(rel)

    failed: list[str] = []
    hashes = changelog.get("file_hashes") or {}
    for rel, expected in hashes.items():
        target = install_dir / rel.replace("/", "\\")
        if not target.is_file():
            failed.append(f"{rel} (missing)")
            continue
        actual = _sha256(target)
        if actual != expected:
            failed.append(f"{rel} (hash mismatch)")

    return {
        "product": changelog.get("product"),
        "from_built_at": changelog.get("from_built_at"),
        "to_built_at": changelog.get("to_built_at"),
        "version": changelog.get("version"),
        "build": changelog.get("build"),
        "extracted": extracted,
        "removed": removed,
        "failed": failed,
        "exe_updated": any(
            p.replace("\\", "/") == "CanvasServer.exe" or p.endswith("/CanvasServer.exe")
            for p in extracted
        ),
    }


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: apply_update.py <install_dir> <CanvasServer-update-*.zip>"
        )
    install_dir = Path(sys.argv[1])
    update_zip = Path(sys.argv[2])
    result = apply_update(install_dir, update_zip)

    print(f"product: {result.get('product')}")
    if result.get("version"):
        print(f"version: v{result['version']} build {result.get('build')}")
    print(f"extracted: {len(result['extracted'])} files")
    if result["removed"]:
        print(f"removed: {len(result['removed'])} files")
    if result["exe_updated"]:
        print("CanvasServer.exe updated — restart required")
    else:
        print("CanvasServer.exe not in this pack — restart still recommended")

    if result["failed"]:
        print("\nVERIFY FAILED:")
        for line in result["failed"]:
            print(f"  - {line}")
        raise SystemExit(1)

    print("\nVERIFY OK — all files match update manifest")
    print("Next: stop launcher, start again, then Ctrl+F5 in browser")


if __name__ == "__main__":
    main()
