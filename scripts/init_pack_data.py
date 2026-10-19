"""Write fresh data/*.json for portable release (UTF-8, no BOM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
TPL = SCRIPTS / "pack_templates"


def _server_worker_public_url() -> str:
    path = TPL / "worker_public_url.txt"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip().rstrip("/")


def main() -> None:
    data_dir = Path(sys.argv[1]).resolve()
    profile = (sys.argv[2] if len(sys.argv) > 2 else "default").strip().lower()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)
    (data_dir / "accounts").mkdir(exist_ok=True)

    service_config: dict = {"headless": False, "browser_executable": ""}
    if profile == "server":
        # Canvas-only: no Doubao browser sim, no watermark / zacao keys.
        service_config.update(
            {
                "server_mode": True,
                "allow_frontend_custom_models": True,
                "doubao_remove_watermark": False,
                "zacao_api_base": "",
                "zacao_api_key": "",
                "browser_executable": "",
            }
        )
        worker_url = _server_worker_public_url()
        if worker_url:
            service_config["upscale_worker_public_url"] = worker_url

    files = {
        "accounts.json": [],
        "tasks.json": [],
        "canvas.json": {"boards": [], "activeBoardId": None},
        "service_config.json": service_config,
    }
    for name, payload in files.items():
        path = data_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"init pack data OK: {data_dir} (profile={profile})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: init_pack_data.py <data_dir> [default|server]")
    main()
