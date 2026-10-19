"""Entry point: python run.py  /  QingZiCanvas.exe"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _app_root()
try:
    os.chdir(ROOT)
except OSError:
    pass
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT))

_data = ROOT / "data"
_data.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DOUBAO_DATA_DIR", str(_data.resolve()))

try:
    import certifi

    _ca = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
except Exception:
    pass

import uvicorn

from backend.config import HOST, PORT, WORKER_PORT

_CANVAS_ROLE_ENV = "CANVAS_ROLE"


def _is_worker_child() -> bool:
    return os.environ.get(_CANVAS_ROLE_ENV) == "worker"


def _run_worker_server() -> None:
    from backend.job_worker.app import worker_app
    from backend.log_setup import setup_file_logging

    setup_file_logging(name="worker")
    uvicorn.run(worker_app, host=HOST, port=WORKER_PORT, reload=False, log_level="info")


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return ips


def _startup_log(msg: str) -> None:
    try:
        line = msg.rstrip() + "\n"
        sys.stdout.write(line)
    except Exception:
        pass
    try:
        with (_data / "startup.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _is_server_pack() -> bool:
    try:
        from backend.build_profile import browser_channels_disabled

        return browser_channels_disabled()
    except Exception:
        return False


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    if _is_worker_child():
        _run_worker_server()
        raise SystemExit(0)

    local = f"http://127.0.0.1:{PORT}"
    worker_local = f"http://127.0.0.1:{WORKER_PORT}"
    server_pack = getattr(sys, "frozen", False) and _is_server_pack()
    if server_pack:
        _startup_log(f"Canvas {local}  |  Worker {worker_local}")
    else:
        _startup_log("=" * 56)
        _startup_log("  无限画布")
        _startup_log(f"  画布（前台）  → {local}")
        _startup_log(f"  任务服务 8089 → {worker_local}（start.bat 会另开窗口）")
        for ip in _lan_ips():
            _startup_log(f"  局域网画布    → http://{ip}:{PORT}")
        _startup_log("  若局域网进不去、本机 127.0.0.1 仍可：多为 Windows 防火墙/公用网络")
        _startup_log("  → 把 Wi-Fi 设为专用网络，或以管理员运行 ensure-lan-access.bat")
        _startup_log("  日志文件      → " + str(_data / "startup.log"))
        _startup_log(f"  数据目录      → {_data}")
        _startup_log("=" * 56)

    log_level = "warning" if server_pack else "info"
    if getattr(sys, "frozen", False):
        from backend.main import app

        uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level=log_level)
    else:
        uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=False)
