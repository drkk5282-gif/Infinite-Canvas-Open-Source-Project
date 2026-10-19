"""Job worker entry: python -m backend.run_worker"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    os.chdir(ROOT)
except OSError:
    pass
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT))

_data = ROOT / "data"
_data.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DOUBAO_DATA_DIR", str(_data.resolve()))

import uvicorn

from backend.config import HOST, WORKER_PORT
from backend.job_worker.app import worker_app
from backend.log_setup import setup_file_logging

setup_file_logging(name="worker")

if __name__ == "__main__":
    uvicorn.run(worker_app, host=HOST, port=WORKER_PORT, reload=False, log_level="info")
