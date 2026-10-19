import os
import sys
from pathlib import Path


def app_root() -> Path:
    """Portable root: frozen exe folder or project root (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resolve_data_dir() -> Path:
    """All user data (canvas, accounts, uploads) lives under app run directory."""
    override = (os.environ.get("DOUBAO_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (app_root() / "data").resolve()


BASE_DIR = app_root()
DATA_DIR = resolve_data_dir()
UPLOADS_DIR = DATA_DIR / "uploads"
ACCOUNTS_INDEX = DATA_DIR / "accounts.json"
VIDEO_TASKS_FILE = DATA_DIR / "video_tasks.json"
TASKS_FILE = DATA_DIR / "tasks.json"
GEN_RECORDS_FILE = DATA_DIR / "gen_records.json"
SERVICE_CONFIG_FILE = DATA_DIR / "service_config.json"

def _read_pack_port() -> int | None:
    """Read port from port.txt beside the exe (portable pack)."""
    for name in ("port.txt", "server.port"):
        path = app_root() / name
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                port = int(raw)
                if 1 <= port <= 65535:
                    return port
        except OSError:
            pass
    return None


# 0.0.0.0 = 局域网内其他设备可访问；仅本机用 127.0.0.1
HOST = os.getenv("DOUBAO_HOST", "0.0.0.0")
_port_env = (os.getenv("DOUBAO_PORT") or "").strip()
PORT = int(_port_env) if _port_env.isdigit() else (_read_pack_port() or 10027)
ADMIN_PORT = int(os.getenv("DOUBAO_ADMIN_PORT", "8090"))
WORKER_PORT = int(os.getenv("DOUBAO_WORKER_PORT", "8089"))

# 同机进程互连（管理后台、Upscale Worker → 画布 API）默认走回环，最快且不依赖外网 IP
INTERNAL_CANVAS_URL = (
    os.getenv("DOUBAO_INTERNAL_CANVAS_URL") or f"http://127.0.0.1:{PORT}"
).rstrip("/")
INTERNAL_WORKER_URL = (
    os.getenv("DOUBAO_INTERNAL_WORKER_URL") or f"http://127.0.0.1:{WORKER_PORT}"
).rstrip("/")

RATIO_MAP = {
    "1:1": "1:1",
    "16:9": "16:9",
    "9:16": "9:16",
    "4:3": "4:3",
    "3:4": "3:4",
}
