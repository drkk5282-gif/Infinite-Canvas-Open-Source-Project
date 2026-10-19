"""Shared file logging so worker/canvas runs leave readable traces under data/logs/."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import DATA_DIR

_CONFIGURED = False


def setup_file_logging(*, name: str = "app") -> Path:
    """Attach a rotating file handler to the root logger (idempotent)."""
    global _CONFIGURED
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"

    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    marker = f"doubao-file:{log_path.resolve()}"
    for h in root.handlers:
        if getattr(h, "_doubao_log_marker", "") == marker:
            _CONFIGURED = True
            return log_path

    handler = RotatingFileHandler(
        log_path,
        maxBytes=8 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._doubao_log_marker = marker  # type: ignore[attr-defined]
    root.addHandler(handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        logging.basicConfig(level=logging.INFO)

    _CONFIGURED = True
    logging.getLogger(__name__).info("file logging → %s", log_path)
    return log_path


def write_capture_debug(payload: str, *, filename: str = "last_media_capture.txt") -> Path:
    """Overwrite a small debug dump the user can open without hunting console output."""
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / filename
    path.write_text(payload, encoding="utf-8")
    return path
