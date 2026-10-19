# -*- coding: utf-8 -*-
"""Restore shell HTML from packaging-era backup. Python UTF-8 write only."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(r"e:\Desktop\AI\doubao-canvas")
FRONT = ROOT / "frontend"
BAK = ROOT / "backups" / "before-interaction-scheduling-20260914" / "frontend"
CORRUPT_SUFFIX = ".corrupt-b209-encoding-bak"

PERSONAL_POP = """<div class="workspace-user-pop overlay-popover hidden" id="workspace-user-pop" role="menu">
      <div class="workspace-user-pop-head">
        <p class="workspace-user-pop-name" id="workspace-user-name">—</p>
        <div class="workspace-user-id-row hidden" id="workspace-user-id-row">
          <span class="workspace-user-id-text">ID：<span id="workspace-user-id-value"></span></span>
          <button type="button" class="workspace-user-id-copy" id="btn-workspace-copy-id" title="复制用户 ID">复制</button>
        </div>
      </div>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-login" role="menuitem">登录</button>
      <button type="button" class="workspace-user-menu-item" id="btn-workspace-edit-profile" role="menuitem">编辑资料</button>
      <input type="file" id="input-workspace-avatar" accept="image/*" hidden />
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-points" role="menuitem">积分使用记录</button>
      <button type="button" class="workspace-user-menu-item" id="btn-workspace-prefix-manager" role="menuitem">前缀管理</button>
      <button type="button" class="workspace-user-menu-item" id="btn-workspace-myapi" role="menuitem">MyApi 配置</button>
      <button type="button" class="workspace-user-menu-item workspace-user-menu-toggle is-on" id="btn-workspace-conn-flow" role="menuitemcheckbox" aria-checked="true" title="生成中时，输入连线显示传输动画">
        <span class="workspace-menu-toggle-label">连线传输动画</span>
        <span class="workspace-menu-switch" aria-hidden="true"><span class="workspace-menu-switch-knob"></span></span>
        <span class="workspace-menu-switch-state">开</span>
      </button>
      <div class="workspace-theme-block" aria-label="界面主题">
        <div class="workspace-theme-label">界面主题</div>
        <div class="workspace-theme-swatches" id="workspace-theme-swatches" role="radiogroup">
          <button type="button" class="theme-swatch is-active" data-app-theme="default" title="默认" aria-label="默认主题" aria-checked="true" role="radio"></button>
          <button type="button" class="theme-swatch" data-app-theme="black" title="黑色" aria-label="黑色主题" aria-checked="false" role="radio"></button>
          <button type="button" class="theme-swatch" data-app-theme="white" title="白色" aria-label="白色主题" aria-checked="false" role="radio"></button>
          <button type="button" class="theme-swatch" data-app-theme="pink" title="粉色" aria-label="粉色主题" aria-checked="false" role="radio"></button>
          <button type="button" class="theme-swatch" data-app-theme="custom" title="自定义" aria-label="自定义主题" aria-checked="false" role="radio"></button>
        </div>
      </div>
      <button type="button" class="workspace-user-logout" id="btn-workspace-logout" role="menuitem">退出登录</button>
    </div>

<div class="workspace-user-pop overlay-popover hidden" id="workspace-internal-pop" role="menu">
      <p class="workspace-user-pop-name">内部</p>
      <p class="workspace-user-pop-sub">仅管理员 / 内部可见</p>
      <div class="workspace-pool-stats hidden" id="workspace-pool-stats" aria-label="号池概况">
        <div class="workspace-pool-stats-row" data-kind="total">
          <span>总账号</span><strong id="workspace-pool-total">0</strong>
        </div>
        <div class="workspace-pool-stats-row" data-kind="s">
          <span>S账号</span><strong id="workspace-pool-s">0</strong>
        </div>
        <div class="workspace-pool-stats-row" data-kind="regular">
          <span>普通账号</span><strong id="workspace-pool-regular">0</strong>
        </div>
        <div class="workspace-pool-stats-row" data-kind="used">
          <span>今日消耗</span><strong id="workspace-pool-used"><span id="workspace-pool-used-n">0</span> 个号</strong>
        </div>
      </div>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-add-doubao" role="menuitem">添加账号</button>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-pool-share" role="menuitem">共享额度</button>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-bendi" role="menuitem">BenDi 本机</button>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-upscale-link" role="menuitem">超分链接</button>
      <button type="button" class="workspace-user-menu-item hidden" id="btn-workspace-admin" role="menuitem">管理后台（8090）</button>
    </div>
"""

EDIT_PROFILE_DIALOG = """
<dialog id="modal-edit-profile" class="modal-dialog profile-edit-dialog">
  <form method="dialog" id="form-edit-profile" autocomplete="off">
    <div class="profile-edit-card">
      <div class="profile-edit-head">
        <h3>编辑资料</h3>
        <button type="button" class="icon-btn" id="btn-close-edit-profile" title="关闭" aria-label="关闭">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div class="profile-edit-body">
        <div class="profile-edit-avatar-block">
          <button type="button" class="profile-edit-avatar-btn" id="btn-profile-pick-avatar" title="更换头像" aria-label="更换头像">
            <span class="user-avatar-letter" id="profile-edit-avatar-letter">—</span>
            <img class="user-avatar-img hidden" id="profile-edit-avatar-img" alt="" />
          </button>
          <div class="profile-edit-avatar-meta">
            <strong>头像</strong>
            <p>点击左侧更换图片，支持 JPG / PNG，最大 3MB</p>
            <button type="button" class="btn btn-ghost btn-sm" id="btn-profile-pick-avatar-text">更换头像</button>
          </div>
        </div>
        <label class="profile-edit-field">
          <span>昵称</span>
          <input type="text" id="input-profile-display-name" maxlength="40" placeholder="显示名称" required autocomplete="off" spellcheck="false" />
        </label>
      </div>
      <div class="profile-edit-actions">
        <button type="button" class="btn btn-ghost" id="btn-cancel-edit-profile">取消</button>
        <button type="submit" class="btn btn-primary" id="btn-save-edit-profile">保存</button>
      </div>
    </div>
  </form>
</dialog>
"""

THEME_BOOT = """  <script>
    (function () {
      try {
        var d = JSON.parse(localStorage.getItem("doubao_canvas_prefs") || "{}");
        var t = d.theme || "default";
        if (t && t !== "default") document.documentElement.setAttribute("data-theme", t);
      } catch (e) {}
    })();
  </script>
"""

HEAD_LINKS = {
    "index.html": [
        '<link rel="stylesheet" href="/static/css/style.css?v=b208-white-vis" />',
        '<link rel="stylesheet" href="/static/css/theme-adapt.css?v=b209-bottom-clear" />',
        '<link rel="stylesheet" href="/static/css/workbench.css?v=b208-white-vis" />',
    ],
    "canvas.html": [
        '<link rel="stylesheet" href="/static/css/style.css?v=b208-white-vis" />',
        '<link rel="stylesheet" href="/static/css/theme-adapt.css?v=b209-bottom-clear" />',
    ],
    "workbench.html": [
        '<link rel="stylesheet" href="/static/css/style.css?v=b208-white-vis" />',
        '<link rel="stylesheet" href="/static/css/theme-adapt.css?v=b209-bottom-clear" />',
        '<link rel="stylesheet" href="/static/css/workbench.css?v=b208-white-vis" />',
    ],
}


def count_bad(text: str) -> int:
    return text.count("\ufffd")


def find_div_open_before(html: str, id_marker: str) -> int:
    pos = html.find(id_marker)
    if pos < 0:
        raise RuntimeError(f"missing {id_marker}")
    open_pos = html.rfind("<div", 0, pos)
    if open_pos < 0:
        raise RuntimeError(f"no div open before {id_marker}")
    return open_pos


def find_matching_div_end(html: str, open_pos: int) -> int:
    """Return index just after the closing </div> that matches div at open_pos."""
    i = open_pos
    depth = 0
    n = len(html)
    while i < n:
        if html.startswith("<div", i) and (i + 4 == n or html[i + 4] in " \t\r\n>"):
            depth += 1
            i += 4
            continue
        if html.startswith("</div>", i):
            depth -= 1
            i += 6
            if depth == 0:
                return i
            continue
        i += 1
    raise RuntimeError("unbalanced div")


def remove_element_by_id(html: str, elem_id: str) -> str:
    marker = f'id="{elem_id}"'
    if marker not in html:
        return html
    open_pos = find_div_open_before(html, marker)
    end = find_matching_div_end(html, open_pos)
    # also trim surrounding blank lines
    left = html[:open_pos].rstrip("\n")
    right = html[end:].lstrip("\n")
    return left + "\n\n" + right


def replace_user_pops(html: str) -> str:
    html = remove_element_by_id(html, "workspace-internal-pop")
    open_pos = find_div_open_before(html, 'id="workspace-user-pop"')
    end = find_matching_div_end(html, open_pos)
    return html[:open_pos] + PERSONAL_POP + html[end:]


def ensure_edit_profile_dialog(html: str) -> str:
    if "modal-edit-profile" in html:
        # remove existing dialog element carefully
        marker = 'id="modal-edit-profile"'
        pos = html.find(marker)
        open_pos = html.rfind("<dialog", 0, pos)
        close = html.find("</dialog>", pos)
        if open_pos >= 0 and close > pos:
            html = html[:open_pos] + html[close + len("</dialog>") :]
    if 'id="app-dialog"' in html:
        return html.replace(
            '<dialog id="app-dialog"',
            EDIT_PROFILE_DIALOG + "\n<dialog id=\"app-dialog\"",
            1,
        )
    if 'id="login-gate-dialog"' in html:
        return html.replace(
            '<dialog id="login-gate-dialog"',
            EDIT_PROFILE_DIALOG + "\n<dialog id=\"login-gate-dialog\"",
            1,
        )
    return html.replace("</body>", EDIT_PROFILE_DIALOG + "\n</body>", 1)


def ensure_theme_boot(html: str) -> str:
    if "doubao_canvas_prefs" in html:
        return html
    if 'rel="icon"' in html:
        return html.replace('<link rel="icon"', THEME_BOOT + '<link rel="icon"', 1)
    return html.replace("<head>", "<head>\n" + THEME_BOOT, 1)


def ensure_css_links(html: str, name: str) -> str:
    html = re.sub(
        r'\s*<link rel="stylesheet" href="/static/css/(?:style|theme-adapt|workbench)\.css\?v=[^"]+"\s*/?>\s*',
        "\n",
        html,
    )
    block = "\n  ".join(HEAD_LINKS[name]) + "\n"
    if 'rel="icon"' in html:
        return re.sub(r'(<link rel="icon"[^>]*>\s*)', r"\1" + block, html, count=1)
    return html.replace("</head>", "  " + block + "</head>", 1)


def ensure_script_versions(html: str) -> str:
    for script, ver in {
        "theme.js": "b204-theme",
        "auth.js": "b205-profile",
        "canvas.js": "b208-white-vis",
    }.items():
        html = re.sub(
            rf'(/static/js/{re.escape(script)})\?v=[^"\s>]+',
            rf"\1?v={ver}",
            html,
        )
    if "/static/js/theme.js" not in html and "/static/js/auth.js" in html:
        html = re.sub(
            r'(<script src="/static/js/auth\.js\?v=[^"]+"></script>)',
            '<script src="/static/js/theme.js?v=b204-theme"></script>\n  \\1',
            html,
            count=1,
        )
    return html


def ensure_internal_btn(html: str, btn_id: str, avatar_img_id: str) -> str:
    """Insert 内 as a sibling BEFORE workspace-user-wrap (never inside it)."""
    if btn_id not in html:
        btn = (
            f'        <button type="button" class="internal-entry-btn hidden" id="{btn_id}" '
            f'title="内部功能" aria-label="内部" aria-expanded="false" aria-haspopup="menu">内</button>\n'
        )
        # Prefer insert immediately before user wrap
        m = re.search(
            r'(<div class="workspace-user-wrap[^"]*" id="(?:workspace|canvas|workbench)-user-wrap"[^>]*>)',
            html,
        )
        if m:
            html = html[: m.start()] + btn + html[m.start() :]
        else:
            for pat in [
                r'(<button type="button" class="user-avatar-btn"[^>]*>)',
                r'(<div class="user-points-badge"[^>]*>)',
            ]:
                if re.search(pat, html):
                    html = re.sub(pat, btn + r"\1", html, count=1)
                    break
    else:
        # If wrongly nested inside wrap, move it out
        nested = re.search(
            rf'(<div class="workspace-user-wrap[^"]*"[^>]*>\s*)'
            rf'(<button type="button" class="internal-entry-btn[^"]*" id="{re.escape(btn_id)}"[^>]*>.*?</button>\s*)',
            html,
            flags=re.S,
        )
        if nested:
            wrap_open, btn_html = nested.group(1), nested.group(2)
            html = html[: nested.start()] + btn_html + wrap_open + html[nested.end() :]

    if avatar_img_id not in html:
        html = re.sub(
            r'(<span class="user-avatar-letter" id="[^"]*user-avatar"[^>]*>.*?</span>)',
            rf'\1\n            <img class="user-avatar-img hidden" id="{avatar_img_id}" alt="" />',
            html,
            count=1,
            flags=re.S,
        )
    return html


def ensure_share_menu(html: str) -> str:
    if 'data-action="share"' not in html and 'data-action="rename"' in html:
        html = html.replace(
            '<button type="button" class="board-menu-item" data-action="rename">重命名</button>',
            '<button type="button" class="board-menu-item" data-action="rename">重命名</button>\n'
            '              <button type="button" class="board-menu-item" data-action="share">共享画板</button>',
            1,
        )
    return html


def restore(name: str, internal_btn: str, avatar_img: str) -> str:
    src = BAK / name
    dst = FRONT / name
    safety = FRONT / f"{name}{CORRUPT_SUFFIX}"
    # Prefer original corrupt safety if present; else keep current as secondary safety
    if dst.exists() and not safety.exists():
        shutil.copy2(dst, safety)

    html = src.read_text(encoding="utf-8")
    assert count_bad(html) == 0

    html = replace_user_pops(html)
    html = ensure_edit_profile_dialog(html)
    html = ensure_theme_boot(html)
    html = ensure_css_links(html, name)
    html = ensure_script_versions(html)
    html = ensure_internal_btn(html, internal_btn, avatar_img)
    if name == "canvas.html":
        html = ensure_share_menu(html)

    html = html.replace(
        "html,body{background:#0a0a0a;margin:0;}",
        "html,body{background:var(--bg,#0a0a0a);margin:0;color:var(--text,#ececf0);}",
        1,
    )

    dst.write_text(html, encoding="utf-8", newline="\n")
    out = dst.read_text(encoding="utf-8")
    bak_len = len(src.read_text(encoding="utf-8"))
    return (
        f"{name}: bytes={dst.stat().st_size} chars={len(out)} bak_chars={bak_len} "
        f"bad={count_bad(out)} dialogs={out.count('<dialog')} "
        f"edit={('modal-edit-profile' in out)} internal={('workspace-internal-pop' in out)} "
        f"theme_adapt={('theme-adapt.css?v=b209-bottom-clear' in out)} "
        f"adopt={('采纳到节点' in out)} rename={('重命名画布' in out or '重命名' in out)}"
    )


def main() -> None:
    lines = [
        restore("index.html", "btn-workspace-internal", "workspace-user-avatar-img"),
        restore("canvas.html", "btn-canvas-internal", "canvas-user-avatar-img"),
        restore("workbench.html", "btn-workbench-internal", "workbench-user-avatar-img"),
    ]
    (ROOT / "scripts" / "_restore_html_encoding_report.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
