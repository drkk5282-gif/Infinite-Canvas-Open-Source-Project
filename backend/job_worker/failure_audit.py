"""Map worker exceptions to customer error + raw audit fields."""

from __future__ import annotations

from typing import Any

from backend.generation_errors import compact_diagnostic


def _format_exc_chain(exc: BaseException, *, max_depth: int = 8) -> str:
    lines: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < max_depth:
        oid = id(cur)
        if oid in seen:
            break
        seen.add(oid)
        msg = str(cur).strip() or type(cur).__name__
        lines.append(f"{type(cur).__name__}: {msg}")
        nxt = cur.__cause__ or cur.__context__
        if nxt is cur.__cause__ and cur.__cause__ is not None:
            lines.append("  ↳ caused by:")
        elif nxt is not None:
            lines.append("  ↳ during handling of:")
        cur = nxt
        depth += 1
    return "\n".join(lines)


def _walk_exc_chain(exc: BaseException, *, max_depth: int = 8):
    seen: set[int] = set()
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < max_depth:
        oid = id(cur)
        if oid in seen:
            break
        seen.add(oid)
        yield cur
        cur = cur.__cause__ or cur.__context__
        depth += 1


def failure_audit_fields(exc: BaseException) -> dict[str, Any]:
    err_msg = str(exc).strip() or type(exc).__name__
    fields: dict[str, Any] = {"error": err_msg, "error_type": type(exc).__name__}

    try:
        from backend.myapi.common import MyApiUpstreamError
    except ImportError:
        MyApiUpstreamError = ()  # type: ignore[misc, assignment]

    raw_response = None
    explicit_raw = ""
    http_status = None
    for cur in _walk_exc_chain(exc):
        if raw_response is None and getattr(cur, "raw_response", None) is not None:
            raw_response = getattr(cur, "raw_response", None)
        r = str(getattr(cur, "raw_error", None) or "").strip()
        if r and not explicit_raw:
            explicit_raw = r
        if http_status is None and getattr(cur, "http_status", None) is not None:
            http_status = getattr(cur, "http_status", None)

    if raw_response is not None:
        fields["raw_response"] = compact_diagnostic(raw_response)

    # Prefer Doubao/page original text over rewritten exception chains.
    if explicit_raw:
        fields["raw_error"] = explicit_raw
    elif isinstance(exc, MyApiUpstreamError) or raw_response is not None or http_status is not None:
        chain = _format_exc_chain(exc)
        if http_status is not None:
            chain = f"HTTP {http_status}\n{chain}".strip()
        fields["raw_error"] = chain
    else:
        fields["raw_error"] = _format_exc_chain(exc)

    return fields
