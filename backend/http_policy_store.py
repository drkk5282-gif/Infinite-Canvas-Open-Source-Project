"""Persist / restore http_policy overrides under data/http_policy.json."""
from __future__ import annotations

import logging
from typing import Any

from backend.config import DATA_DIR
from backend.json_io import load_json, save_json

logger = logging.getLogger(__name__)


def _path():
    return DATA_DIR / "http_policy.json"


def load_into_runtime() -> dict[str, Any]:
    from backend import http_policy

    raw = load_json(_path(), default={})
    if not isinstance(raw, dict):
        return http_policy.get_policy()
    try:
        return http_policy.set_policy(
            hard_timeout_hours=raw.get("hard_timeout_hours"),
            network_retries=raw.get("network_retries"),
            poll_interval_sec=raw.get("poll_interval_sec"),
        )
    except Exception as exc:
        logger.warning("failed to apply http_policy.json: %s", exc)
        return http_policy.get_policy()


def save_from_runtime() -> dict[str, Any]:
    from backend import http_policy

    policy = http_policy.get_policy()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(
        _path(),
        {
            "hard_timeout_hours": policy["hard_timeout_hours"],
            "network_retries": policy["network_retries"],
            "poll_interval_sec": policy["poll_interval_sec"],
        },
    )
    return policy
