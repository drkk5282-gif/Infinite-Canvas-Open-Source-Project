"""Registry for MyApi relay styles.

Open-source builds only ship the generic HTTP style and the Volcengine channel.
"""

from __future__ import annotations

from typing import Any

from backend.myapi.styles.base import BaseApiStyle
from backend.myapi.styles.volcengine import STYLE as VOLCENGINE_STYLE


class GenericStyle(BaseApiStyle):
    style_id = "generic"


GENERIC_STYLE = GenericStyle()

_STYLES: dict[str, BaseApiStyle] = {
    GENERIC_STYLE.style_id: GENERIC_STYLE,
    VOLCENGINE_STYLE.style_id: VOLCENGINE_STYLE,
}

DEFAULT_STYLE_ID = GENERIC_STYLE.style_id


def _looks_like_volcengine(model_cfg: dict[str, Any] | None) -> bool:
    cfg = model_cfg or {}
    base = str(cfg.get("base_url") or "").strip().lower()
    model = str(cfg.get("model") or cfg.get("name") or "").strip().lower()
    if "ark.cn-beijing.volces.com" in base or "volces.com" in base:
        return True
    if "openspeech.bytedance.com" in base:
        return True
    if any(k in model for k in ("doubao-seedance", "doubao-seedream", "seed-audio", "doubao-seed-")):
        return True
    return False


def get_style(model_cfg: dict[str, Any] | None) -> BaseApiStyle:
    style_id = str((model_cfg or {}).get("api_style") or "").strip().lower()
    if style_id == VOLCENGINE_STYLE.style_id:
        return _STYLES[VOLCENGINE_STYLE.style_id]
    if style_id in ("", "generic") and _looks_like_volcengine(model_cfg):
        base = str((model_cfg or {}).get("base_url") or "").strip().lower()
        if "volces.com" in base or "openspeech" in base or "bytedance.com" in base:
            return _STYLES[VOLCENGINE_STYLE.style_id]
    if style_id in _STYLES:
        return _STYLES[style_id]
    return _STYLES[DEFAULT_STYLE_ID]


def registered_style_ids() -> tuple[str, ...]:
    return tuple(_STYLES.keys())
