"""Bump ?v= cache-bust query strings in HTML to current build number."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.json"
HTML_FILES = (
    ROOT / "frontend" / "index.html",
    ROOT / "frontend" / "canvas.html",
    ROOT / "admin" / "index.html",
)

CACHE_RE = re.compile(r"(\?v=)[^\"']+")


def load_build() -> int:
    if not VERSION_FILE.is_file():
        return 0
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        return int(data.get("build") or 0)
    except Exception:
        return 0


def bump_html(path: Path, token: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    updated = CACHE_RE.sub(rf"\1{token}", text)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    build = load_build()
    token = f"b{build}" if build else "dev"
    changed = []
    for path in HTML_FILES:
        if bump_html(path, token):
            changed.append(str(path.relative_to(ROOT)))
    if changed:
        print(f"cache bust OK: {token} -> {', '.join(changed)}")
    else:
        print(f"cache bust skipped (no ?v= in html), token={token}")


if __name__ == "__main__":
    main()
