"""Copy launcher/readme into release folder with correct UTF-8 Chinese filenames."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
TPL = SCRIPTS / "pack_templates"
# ASCII-only source: avoid Windows cp936 mis-decoding UTF-8 literals in .py files
LAUNCHER_NAME = "\u542f\u52a8\u65e0\u9650\u753b\u5e03.bat"
README_NAME = "\u4f7f\u7528\u8bf4\u660e.txt"
LAUNCHER_SERVER = "\u542f\u52a8\u753b\u5e03\u670d\u52a1\u5668.bat"
LAUNCHER_JOB_WORKER = "\u542f\u52a8\u4efb\u52a1\u670d\u52a1.bat"
README_SERVER = "\u670d\u52a1\u5668\u90e8\u7f72\u8bf4\u660e.txt"


def _write_bat(src: Path, dst: Path) -> None:
    """Copy .bat as ASCII/ANSI — never UTF-8 BOM (breaks @echo off on Windows cmd)."""
    raw = src.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    dst.write_bytes(raw)


def stage(out_dir: Path, variant: str = "full") -> None:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Remove old launcher/readme (including mojibake names from broken PowerShell copies)
    keep_txt = {"requirements.txt", "requirements-server.txt", "port.txt", "host.txt", "external_port.txt", "worker_public_url.txt"}
    for item in out_dir.iterdir():
        if not item.is_file():
            continue
        name = item.name.lower()
        if item.suffix.lower() == ".bat":
            item.unlink(missing_ok=True)
        elif item.suffix.lower() == ".txt" and name not in keep_txt:
            item.unlink(missing_ok=True)

    if variant in {"server", "server-python", "full-python"}:
        if variant == "server-python":
            launcher_src = TPL / "launcher-server-python.bat"
            worker_src = TPL / "launcher-job-worker-python.bat"
            readme_src = TPL / "readme-server-python.txt"
        elif variant == "full-python":
            launcher_src = TPL / "launcher-full-python.bat"
            worker_src = TPL / "launcher-job-worker-full-python.bat"
            readme_src = TPL / "readme-full-python.txt"
        else:
            launcher_src = TPL / "launcher-server.bat"
            worker_src = TPL / "launcher-job-worker.bat"
            readme_src = TPL / "readme-server.txt"
        launcher_dst = out_dir / LAUNCHER_SERVER
        readme_dst = out_dir / README_SERVER
        worker_dst = out_dir / LAUNCHER_JOB_WORKER
    else:
        launcher_src = TPL / "launcher.bat"
        readme_src = TPL / "readme.txt"
        launcher_dst = out_dir / LAUNCHER_NAME
        readme_dst = out_dir / README_NAME

    _write_bat(launcher_src, launcher_dst)

    if variant in {"server", "server-python", "full-python"}:
        _write_bat(worker_src, worker_dst)

    readme_dst.write_text(readme_src.read_text(encoding="utf-8"), encoding="utf-8")

    port_tpl = TPL / "port.txt"
    if port_tpl.is_file():
        (out_dir / "port.txt").write_text(port_tpl.read_text(encoding="utf-8"), encoding="utf-8")

    if variant in {"server", "server-python", "full-python"}:
        host_tpl = TPL / "host.txt"
        if host_tpl.is_file() and not (out_dir / "host.txt").is_file():
            (out_dir / "host.txt").write_text(host_tpl.read_text(encoding="utf-8"), encoding="utf-8")
        ext_tpl = TPL / "external_port.txt"
        if ext_tpl.is_file() and not (out_dir / "external_port.txt").is_file():
            (out_dir / "external_port.txt").write_text(ext_tpl.read_text(encoding="utf-8"), encoding="utf-8")

    if variant in {"server", "server-python", "full-python"}:
        # Chinese filename via Unicode escapes (avoid mojibake in pack scripts)
        vk_src = TPL / "install-vulkan-runtime.bat"
        vk_dst = out_dir / "\u5b89\u88c5Vulkan\u8fd0\u884c\u5e93.bat"
        if vk_src.is_file():
            _write_bat(vk_src, vk_dst)

    if variant in {"server", "server-python", "full-python"}:
        stage_caddy(out_dir)
        stage_update_tools(out_dir)
        if variant in {"server-python", "full-python"}:
            stage_python_deps_helper(out_dir)
        worker_url_tpl = TPL / "worker_public_url.txt"
        if worker_url_tpl.is_file():
            (out_dir / "worker_public_url.txt").write_text(
                worker_url_tpl.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    print(f"stage pack extras OK: {launcher_dst.name}, {readme_dst.name}")
    if variant in {"server", "server-python", "full-python"}:
        print(f"  + {worker_dst.name}")


def stage_update_tools(out_dir: Path) -> None:
    """Bundle update apply scripts into release."""
    scripts_dst = out_dir / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    for name in ("apply_update.py", "apply_update.ps1"):
        src = SCRIPTS / name
        if src.is_file():
            shutil.copy2(src, scripts_dst / name)

    apply_bat_tpl = TPL / "apply-update.bat"
    apply_bat_dst = out_dir / "\u5e94\u7528\u66f4\u65b0.bat"
    if apply_bat_tpl.is_file():
        _write_bat(apply_bat_tpl, apply_bat_dst)
        print("stage update tools OK: apply-update.bat")


def stage_python_deps_helper(out_dir: Path) -> None:
    """BAT that creates .venv + pip installs requirements on first launch."""
    src = TPL / "ensure-python-deps.bat"
    if not src.is_file():
        return
    scripts_dst = out_dir / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    _write_bat(src, scripts_dst / "ensure-python-deps.bat")
    print("stage python deps helper OK: scripts/ensure-python-deps.bat")


def stage_caddy(out_dir: Path) -> None:
    """Bundle HTTPS helper scripts (+ optional caddy.exe) for server deploy."""
    scripts_dst = out_dir / "scripts"
    caddy_dst = out_dir / "caddy"
    vendor_src = SCRIPTS / "caddy" / "vendor"
    example = SCRIPTS / "caddy" / "Caddyfile.example"

    scripts_dst.mkdir(parents=True, exist_ok=True)
    caddy_dst.mkdir(parents=True, exist_ok=True)
    (caddy_dst / "ssl").mkdir(parents=True, exist_ok=True)

    for name in (
        "setup_caddy.ps1",
        "setup_caddy.bat",
        "start_caddy.bat",
        "install_caddy_offline.bat",
    ):
        src = SCRIPTS / name
        if src.is_file():
            shutil.copy2(src, scripts_dst / name)

    caddy_scripts = SCRIPTS / "caddy"
    if caddy_scripts.is_dir():
        dst_caddy_scripts = scripts_dst / "caddy"
        dst_caddy_scripts.mkdir(parents=True, exist_ok=True)
        for rel in (
            "Caddyfile.example",
            "Caddyfile.template",
            "domain.txt.example",
            "generate_caddyfile.ps1",
            "run_caddy.bat",
            "diagnose_caddy.bat",
            "vendor/README.txt",
        ):
            src = caddy_scripts / rel
            if src.is_file():
                target = dst_caddy_scripts / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)

    if example.is_file() and not (caddy_dst / "Caddyfile").is_file():
        shutil.copy2(example, caddy_dst / "Caddyfile")

    run_bat = SCRIPTS / "caddy" / "run_caddy.bat"
    gen_ps1 = SCRIPTS / "caddy" / "generate_caddyfile.ps1"
    tpl = SCRIPTS / "caddy" / "Caddyfile.template"
    domain_example = SCRIPTS / "caddy" / "domain.txt.example"
    if run_bat.is_file():
        shutil.copy2(run_bat, caddy_dst / "run_caddy.bat")
    if gen_ps1.is_file():
        shutil.copy2(gen_ps1, caddy_dst / "generate_caddyfile.ps1")
    if tpl.is_file():
        shutil.copy2(tpl, caddy_dst / "Caddyfile.template")
    if domain_example.is_file() and not (caddy_dst / "domain.txt").is_file():
        shutil.copy2(domain_example, caddy_dst / "domain.txt")

    vendor_exe = vendor_src / "caddy.exe"
    vendor_zip = vendor_src / "caddy.zip"
    if vendor_exe.is_file():
        shutil.copy2(vendor_exe, caddy_dst / "caddy.exe")
        print("stage caddy OK: bundled caddy.exe (offline-ready)")
    elif vendor_zip.is_file():
        shutil.copy2(vendor_zip, caddy_dst / "caddy.zip")
        print("stage caddy OK: bundled caddy.zip (run scripts\\install_caddy_offline.bat)")
    else:
        print("stage caddy OK: scripts only (place caddy.exe in scripts/caddy/vendor before build)")

    # Root launcher: double-click to start HTTPS proxy
    https_bat = out_dir / "\u542f\u52a8HTTPS.bat"
    https_bat.write_text(
        "@echo off\r\n"
        'cd /d "%~dp0"\r\n'
        "echo.\r\n"
        "echo Do NOT double-click caddy.exe\r\n"
        "echo.\r\n"
        'if exist "%~dp0caddy\\run_caddy.bat" (\r\n'
        '  call "%~dp0caddy\\run_caddy.bat"\r\n'
        "  exit /b %errorlevel%\r\n"
        ")\r\n"
        'call "%~dp0scripts\\start_caddy.bat"\r\n',
        encoding="ascii",
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: stage_pack_extras.py <out_dir> [full|server|server-python|full-python]")
    variant = sys.argv[2] if len(sys.argv) > 2 else "full"
    stage(Path(sys.argv[1]), variant=variant)


if __name__ == "__main__":
    main()
