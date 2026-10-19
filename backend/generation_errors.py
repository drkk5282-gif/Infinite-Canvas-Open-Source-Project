"""Generation exceptions that keep provider diagnostics for admin audit."""

from __future__ import annotations

from datetime import datetime
from typing import Any


_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "secret",
    "access_token",
    "refresh_token",
}


def compact_diagnostic(value: Any, *, _depth: int = 0) -> Any:
    """Keep provider diagnostics useful without persisting secrets or huge blobs."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if _depth >= 6:
        return "<nested value omitted>"
    if isinstance(value, str):
        text = value
        if text.lower().startswith("data:") and ";base64," in text[:160].lower():
            return f"<data URL omitted; {len(text)} chars>"
        if len(text) > 4000:
            return text[:4000] + f"\n…(truncated, total {len(text)} chars)"
        return text
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            name = str(key)
            lowered = name.lower()
            if lowered in _SECRET_KEYS or lowered.endswith("_api_key"):
                out[name] = "<redacted>"
            else:
                out[name] = compact_diagnostic(item, _depth=_depth + 1)
        if len(value) > 80:
            out["<omitted_keys>"] = len(value) - 80
        return out
    if isinstance(value, (list, tuple)):
        items = [compact_diagnostic(item, _depth=_depth + 1) for item in value[:50]]
        if len(value) > 50:
            items.append(f"<omitted {len(value) - 50} items>")
        return items
    return compact_diagnostic(str(value), _depth=_depth + 1)


def remaining_poll_seconds(created_at: Any, limit_s: float) -> float:
    """Return a persisted poll budget so service restarts do not reset deadlines."""
    text = str(created_at or "").strip()
    if not text:
        return float(limit_s)
    try:
        created = datetime.fromisoformat(text)
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.now()
        elapsed = max(0.0, (now - created).total_seconds())
        return max(0.001, float(limit_s) - elapsed)
    except (TypeError, ValueError):
        return float(limit_s)


class GenerationPollError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        raw_response: Any = None,
        raw_error: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = compact_diagnostic(raw_response)
        self.raw_error = raw_error
        self.http_status = http_status
