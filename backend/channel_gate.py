"""Gateway for optional browser simulation channels (Doubao).

Canvas-only / server builds omit the ``browser_channels`` package and set
``disable_browser_channels`` in ``build_profile.json``.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException

from backend.build_profile import browser_channels_disabled


def browser_channels_installed() -> bool:
    """True when the ``browser_channels`` package is present on disk."""
    if browser_channels_disabled():
        return False
    from backend.build_profile import browser_channels_code_present, is_full_pack_profile

    return browser_channels_code_present() or is_full_pack_profile()


def browser_channels_enabled() -> bool:
    if browser_channels_disabled():
        return False
    return browser_channels_installed()


@lru_cache(maxsize=1)
def get_pool():
    if not browser_channels_enabled():
        # Prefer 403 over 500 when pack omits browser_channels.
        raise HTTPException(403, "本部署仅支持 MyApi 渠道。")
    from browser_channels.pool import browser_pool

    return browser_pool


def reject_browser_channels_http() -> None:
    if not browser_channels_enabled():
        raise HTTPException(
            403,
            "本部署仅支持 MyApi 渠道。",
        )


def assert_user_browser_channels(user: dict | None = None) -> dict:
    """Deploy must have browser channels; user must be admin or internal."""
    from backend.auth_deps import require_login_user
    from backend.user_roles import can_use_browser_channels

    reject_browser_channels_http()
    row = user or require_login_user()
    if not can_use_browser_channels(row.get("role")):
        raise HTTPException(
            403,
            "Seed 渠道仅限管理员或内部人员使用，请改用 MyApi 或 BenDi。",
        )
    return row


def _empty_doubao_media_models() -> dict:
    return {
        "IMAGE_MODELS": [],
        "IMAGE_RATIOS": [],
        "IMAGE_STYLES": [],
        "VIDEO_MODELS": [],
        "VIDEO_RATIOS": [],
        "DOLA_VIDEO_MODELS": [],
        "DOLA_VIDEO_RATIOS": [],
        "VIDEO_DURATION_MIN": 5,
        "VIDEO_DURATION_MAX": 15,
        "VIDEO_DURATION_TICKS": [5, 10, 15],
    }


def doubao_media_models():
    """Doubao model catalogs; empty lists when browser channels are off."""
    if browser_channels_disabled():
        return _empty_doubao_media_models()
    if not browser_channels_enabled():
        return _empty_doubao_media_models()
    try:
        from browser_channels.doubao import media_models as mm
    except (ImportError, ModuleNotFoundError):
        return _empty_doubao_media_models()

    return {
        "IMAGE_MODELS": mm.IMAGE_MODELS,
        "IMAGE_RATIOS": mm.IMAGE_RATIOS,
        "IMAGE_STYLES": mm.IMAGE_STYLES,
        "VIDEO_MODELS": mm.VIDEO_MODELS,
        "VIDEO_RATIOS": mm.VIDEO_RATIOS,
        "DOLA_VIDEO_MODELS": mm.DOLA_VIDEO_MODELS,
        "DOLA_VIDEO_RATIOS": mm.DOLA_VIDEO_RATIOS,
        "VIDEO_DURATION_MIN": mm.VIDEO_DURATION_MIN,
        "VIDEO_DURATION_MAX": mm.VIDEO_DURATION_MAX,
        "VIDEO_DURATION_TICKS": mm.VIDEO_DURATION_TICKS,
    }
