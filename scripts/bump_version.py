"""Bump and record release version before packaging."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.json"


def load_version() -> dict:
    default = {
        "version": "1.0.0",
        "build": 0,
        "product": "CanvasServer",
        "built_at": "",
        "history": [],
    }
    if not VERSION_FILE.is_file():
        return default
    try:
        raw = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {**default, **raw}
    except Exception:
        pass
    return default


def bump(product: str = "CanvasServer") -> dict:
    data = load_version()
    data["product"] = product
    data["build"] = int(data.get("build") or 0) + 1
    built_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    data["built_at"] = built_at
    history = list(data.get("history") or [])
    history.append({"build": data["build"], "built_at": built_at, "product": product})
    data["history"] = history[-50:]
    VERSION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return data


if __name__ == "__main__":
    prod = sys.argv[1] if len(sys.argv) > 1 else "CanvasServer"
    print(json.dumps(bump(prod), ensure_ascii=False))
