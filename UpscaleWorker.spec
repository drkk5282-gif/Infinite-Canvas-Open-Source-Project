# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — UpscaleWorker GUI (local GPU)."""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()

hiddenimports = [
    "httpx",
    "worker",
    "backend.media.video_upscale",
    "backend.upscale_connect",
    "backend.service_settings",
    "backend.json_io",
    "backend.config",
    "tkinter",
]

a = Analysis(
    [str(ROOT / "upscale-worker" / "worker_gui.py")],
    pathex=[str(ROOT), str(ROOT / "upscale-worker")],
    binaries=[],
    datas=[(str(ROOT / "upscale-worker" / "config.ini.example"), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QingZiUpscale",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="QingZiUpscale",
)
