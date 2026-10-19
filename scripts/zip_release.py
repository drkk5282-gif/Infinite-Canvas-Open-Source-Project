"""Zip a release folder (avoids PowerShell Compress-Archive file-lock issues)."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

# Runtime data — never ship in deploy zips.
DEFAULT_SKIP_DIRS = {"data"}


def _should_skip_zip_rel(rel: str, skip_dirs: set[str]) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if parts and parts[0] in skip_dirs:
        return True
    if len(parts) >= 3 and parts[0] == "browser_channels" and parts[2] == "data":
        return True
    return False


def zip_folder(folder: Path, zip_path: Path, *, skip_dirs: set[str] | None = None) -> float:
    folder = folder.resolve()
    zip_path = zip_path.resolve()
    if not folder.is_dir():
        raise SystemExit(f"folder not found: {folder}")
    skip = set(skip_dirs or set())

    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(folder).as_posix()
            if _should_skip_zip_rel(rel, skip):
                continue
            zf.write(path, f"{folder.name}/{rel}")

    if not zip_path.is_file():
        raise SystemExit(f"zip failed: {zip_path}")
    return zip_path.stat().st_size / 1024 / 1024


def main() -> None:
    parser = argparse.ArgumentParser(description="Zip a release folder")
    parser.add_argument("folder", type=Path)
    parser.add_argument("zip_path", type=Path)
    parser.add_argument(
        "--exclude-data",
        action="store_true",
        help="omit data/ from zip (safe server deploy — keeps existing runtime data)",
    )
    parser.add_argument(
        "--exclude-playwright-browsers",
        action="store_true",
        help="omit .playwright-browsers/ (Chromium is downloaded on first start)",
    )
    args = parser.parse_args()
    skip = set()
    if args.exclude_data:
        skip |= set(DEFAULT_SKIP_DIRS)
    if args.exclude_playwright_browsers:
        skip.add(".playwright-browsers")
    size_mb = zip_folder(args.folder, args.zip_path, skip_dirs=skip)
    skipped = f" (excluded: {', '.join(sorted(skip))})" if skip else ""
    print(f"zip OK: {args.zip_path.resolve()} ({size_mb:.1f} MB){skipped}")


if __name__ == "__main__":
    main()
