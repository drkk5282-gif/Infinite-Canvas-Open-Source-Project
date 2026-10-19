"""Strip Doubao / OpenAI browser / watermark UI from a Canvas Server pack.

Mutates frontend/admin HTML under the release folder so the shipped UI only
surfaces MyApi / public models — no channel names or browser-login chrome.
Also drops stray draft copies (e.g. ``index copy.html``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _strip_between(html: str, start_pat: str, end_pat: str) -> str:
    """Remove inclusive block from first start_pat match through end_pat."""
    start = re.search(start_pat, html, flags=re.I)
    if not start:
        return html
    end = re.search(end_pat, html[start.start() :], flags=re.I)
    if not end:
        return html
    cut_end = start.start() + end.end()
    return html[: start.start()] + html[cut_end:]


def _mark_api_only_pack(html: str) -> str:
    """Tag root so frontend JS can keep MyApi-only fallbacks if /api/app-config fails."""
    if re.search(r"<html[^>]*\bdata-pack\s*=", html, flags=re.I):
        return re.sub(
            r'(<html[^>]*\bdata-pack\s*=\s*)(["\'])[^"\']*\2',
            r'\1\2api-only\2',
            html,
            count=1,
            flags=re.I,
        )
    return re.sub(r"<html\b", '<html data-pack="api-only"', html, count=1, flags=re.I)


def _neutralize_openai_compat(html: str) -> str:
    return re.sub(r"OpenAI 兼容中转", "HTTP API 中转", html)


def sanitize_frontend(html: str) -> str:
    html = _mark_api_only_pack(html)
    # Settings: browser section (静默 / 路径 / OpenAI 代理)
    html = _strip_between(
        html,
        r'<section class="settings-section">\s*<div class="settings-section-title">浏览器</div>',
        r"</section>",
    )
    # Settings: Doubao / OpenAI account boxes
    html = _strip_between(
        html,
        r'<section class="settings-section settings-accounts-wrap">',
        r"</section>",
    )
    # Account / QR login dialogs
    for start, end in (
        (r'<dialog id="modal-add-account">', r"</dialog>"),
        (r'<dialog id="modal-qr-login">', r"</dialog>"),
        (r'<dialog id="modal-browser-login"[^>]*>', r"</dialog>"),
        (r'<dialog id="modal-gpt-login"[^>]*>', r"</dialog>"),
    ):
        html = _strip_between(html, start, end)

    html = html.replace(
        "DouBao / OpenAI / MyApi · 并发 · 浏览器 · 代理 · 登录包",
        "MyApi · 并发 · 数据",
    )
    html = html.replace(
        'title="本地浏览器并行"',
        'title="MyApi 并发"',
    )
    html = html.replace(
        "本地浏览器并行上限（在管理后台修改）",
        "MyApi 并发上限（在管理后台修改）",
    )
    html = _neutralize_openai_compat(html)
    # Neutralize leftover browser-install wording if any block remains.
    html = html.replace("自动检测内置 Chromium；也可手动选择本机 Chrome / Edge", "—")
    html = html.replace("自动检测内置 Chromium", "—")
    html = html.replace("本地浏览器并行", "MyApi 并发")
    return html


def sanitize_admin(html: str) -> str:
    html = _mark_api_only_pack(html)
    # Prefer class=hidden blocks — remove entirely from API-only pack.
    for start, end in (
        (
            r'<section class="settings-section[^"]*" id="settings-browser-section"[^>]*>',
            r"</section>",
        ),
        (
            r'<section class="settings-section[^"]*" id="settings-doubao-wm-section"[^>]*>',
            r"</section>",
        ),
        (
            r'<section class="settings-section[^"]*" id="settings-accounts-section"[^>]*>',
            r"</section>",
        ),
        (r'<dialog id="modal-qr-login"[^>]*>', r"</dialog>"),
        (r'<dialog id="modal-add-account"[^>]*>', r"</dialog>"),
        (r'<dialog id="modal-browser-login"[^>]*>', r"</dialog>"),
    ):
        html = _strip_between(html, start, end)

    html = re.sub(
        r"当前为 <strong>纯 API 部署</strong>，仅支持 <strong>MyApi / 公共模型</strong> HTTP 渠道（无浏览器模拟、无豆包 / OpenAI 网页登录）。",
        "当前为 <strong>纯 API 部署</strong>，仅支持 <strong>MyApi / 公共模型</strong> HTTP 渠道。",
        html,
    )
    html = html.replace(
        "创建可登录画布的本地用户（MyApi / 公共模型）",
        "创建可登录画布的本地用户",
    )
    html = _neutralize_openai_compat(html)
    return html


def _drop_stray_frontend_copies(root: Path) -> list[str]:
    removed: list[str] = []
    fe = root / "frontend"
    if not fe.is_dir():
        return removed
    for path in fe.glob("index*.html"):
        if path.name.lower() == "index.html":
            continue
        path.unlink(missing_ok=True)
        removed.append(str(path.relative_to(root)).replace("\\", "/"))
    return removed


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: sanitize_api_only_ui.py <pack_root>")
    root = Path(sys.argv[1]).resolve()
    changed: list[str] = []

    fe = root / "frontend" / "index.html"
    ad = root / "admin" / "index.html"
    user_myapi = root / "admin" / "user-myapi.html"

    if fe.is_file():
        text = fe.read_text(encoding="utf-8")
        out = sanitize_frontend(text)
        if out != text:
            fe.write_text(out, encoding="utf-8")
            changed.append("frontend/index.html")
    if ad.is_file():
        text = ad.read_text(encoding="utf-8")
        out = sanitize_admin(text)
        if out != text:
            ad.write_text(out, encoding="utf-8")
            changed.append("admin/index.html")
    if user_myapi.is_file():
        text = user_myapi.read_text(encoding="utf-8")
        out = _neutralize_openai_compat(text)
        if out != text:
            user_myapi.write_text(out, encoding="utf-8")
            changed.append("admin/user-myapi.html")

    changed.extend(f"removed {p}" for p in _drop_stray_frontend_copies(root))
    print(
        "sanitize api-only UI OK: " + (", ".join(changed) if changed else "no changes")
    )


if __name__ == "__main__":
    main()
