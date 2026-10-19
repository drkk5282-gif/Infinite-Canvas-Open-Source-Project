"""Canvas reference inputs for UI simulation uploads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReferenceInput:
    kind: str  # image | video | audio
    url: str


def parse_reference_payload(
    references: list[dict] | None,
    reference_images: list[str] | None = None,
) -> list[ReferenceInput]:
    """Merge structured references + legacy reference_images list."""
    out: list[ReferenceInput] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, url: str) -> None:
        u = (url or "").strip()
        if not u:
            return
        key = (kind, u)
        if key in seen:
            return
        seen.add(key)
        out.append(ReferenceInput(kind=kind, url=u))

    for item in references or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "image").lower()
        url = str(item.get("url") or item.get("src") or "").strip()
        add(kind, url)

    for url in reference_images or []:
        add("image", str(url))

    return out
