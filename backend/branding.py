"""App branding: logo letter and favicon."""

from __future__ import annotations

from fastapi.responses import Response

APP_LOGO_LETTER = "\u5e86"
APP_LOGO_BG = "#000000"


def favicon_svg() -> str:
    font = (
        'system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif'
    )
    # Numeric entity avoids encoding issues when serving SVG as bytes.
    letter = "&#x5E86;"
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        f"<rect width='32' height='32' rx='8' fill='{APP_LOGO_BG}'/>"
        f"<text x='16' y='22.5' text-anchor='middle' font-size='17' "
        f"font-weight='700' fill='#ffffff' font-family='{font}'>"
        f"{letter}</text></svg>"
    )


def favicon_response() -> Response:
    return Response(
        content=favicon_svg().encode("utf-8"),
        media_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )
