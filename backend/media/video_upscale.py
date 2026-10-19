"""Video super-resolution via Real-ESRGAN-ncnn-vulkan (same engine stack as xmsr).

Pipeline: ffmpeg extract frames → realesrgan-ncnn-vulkan → optional fps boost → remux.
Also supports a fast “basic HD” path (ffmpeg lanczos only).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx

from backend.config import BASE_DIR, DATA_DIR
from backend.media.video_tools import get_ffmpeg_exe, hidden_subprocess_kwargs, probe_duration, probe_size

logger = logging.getLogger(__name__)

ENGINE_DIRNAME = "realesrgan-ncnn-vulkan"
ENGINE_EXE = "realesrgan-ncnn-vulkan.exe"
ENGINE_ZIP_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
    "v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
)
VULKAN_DLL = "vulkan-1.dll"
VULKAN_MISSING_HINT = (
    "服务器缺少 Vulkan 运行库（vulkan-1.dll），AI 视频超分无法启动。\n"
    "请任选其一：\n"
    "① 安装显卡驱动（NVIDIA / AMD / Intel，推荐）\n"
    "② 安装 LunarG Vulkan Runtime：https://vulkan.lunarg.com/sdk/home\n"
    "③ 或执行：winget install --id KhronosGroup.VulkanRT -e\n"
    "装好后重启 CanvasServer 再试。无独显/核显的纯 CPU 服务器只能用「常规高清」，不能用 AI 超分。"
)

DEFAULT_MODEL = "realesr-animevideov3"
ALLOWED_MODELS = frozenset(
    {
        "realesr-animevideov3",
        "realesrgan-x4plus",
        "realesrgan-x4plus-anime",
        "realesrnet-x4plus",
    }
)
ALLOWED_SCALES = frozenset({2, 3, 4})
FIXED_SCALE_MODELS = {
    "realesrgan-x4plus": 4,
    "realesrgan-x4plus-anime": 4,
    "realesrnet-x4plus": 4,
}

# Target short-side / height presets (landscape uses height; portrait uses width)
RES_PRESETS = {
    "720": 720,
    "1080": 1080,
    "2k": 1440,
    "4k": 2160,
}


def engines_root() -> Path:
    env = (os.getenv("DOUBAO_ENGINES_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Portable UpscaleWorker.exe: engines/ or realesrgan-ncnn-vulkan/ beside the exe
    if getattr(sys, "frozen", False):
        for alt in (BASE_DIR / "engines", BASE_DIR / "tools" / "engines", BASE_DIR):
            if (alt / ENGINE_DIRNAME / ENGINE_EXE).is_file():
                return alt.resolve()
    return (BASE_DIR / "tools" / "engines").resolve()


def _temp_work_parent() -> Path:
    """Directory for doubao_sr_* frame workdirs (must exist before mkdtemp)."""
    from backend.config import resolve_data_dir

    try:
        data = resolve_data_dir()
        data.mkdir(parents=True, exist_ok=True)
        return data
    except OSError:
        pass
    fallback = Path(tempfile.gettempdir()) / "doubao-upscale-worker"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def resolve_realesrgan() -> Path | None:
    candidates: list[Path] = []
    root = engines_root()
    candidates.append(root / ENGINE_DIRNAME / ENGINE_EXE)
    candidates.append(root / ENGINE_EXE)
    env_exe = (os.getenv("DOUBAO_REALESRGAN") or "").strip()
    if env_exe:
        candidates.insert(0, Path(env_exe).expanduser().resolve())
    appdata = os.environ.get("APPDATA") or ""
    local = os.environ.get("LOCALAPPDATA") or ""
    for base in (appdata, local):
        if not base:
            continue
        candidates.append(
            Path(base) / "星梦超分工具" / "engines" / ENGINE_DIRNAME / ENGINE_EXE
        )
    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def _system_vulkan_dll() -> Path | None:
    if os.name != "nt":
        return None
    windir = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    for name in ("System32", "SysWOW64"):
        path = windir / name / VULKAN_DLL
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def resolve_vulkan_dll(*, engine_dir: Path | None = None) -> Path | None:
    """Locate vulkan-1.dll beside the engine or in System32."""
    dirs: list[Path] = []
    if engine_dir is not None:
        dirs.append(engine_dir)
    exe = resolve_realesrgan()
    if exe is not None:
        dirs.append(exe.parent)
    dirs.append(engines_root() / ENGINE_DIRNAME)
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        path = d / VULKAN_DLL
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return _system_vulkan_dll()


def ensure_vulkan_loader(*, engine_dir: Path | None = None) -> Path:
    """Make sure vulkan-1.dll is findable next to the engine (Windows).

    Copies from System32 when possible so packaged servers without a full
    driver install still load the Vulkan loader. GPU ICD (driver) is still required.
    """
    existing = resolve_vulkan_dll(engine_dir=engine_dir)
    exe = resolve_realesrgan()
    dest_dir = engine_dir or (exe.parent if exe else engines_root() / ENGINE_DIRNAME)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / VULKAN_DLL

    if existing and existing.resolve() == dest.resolve():
        return dest
    if dest.is_file():
        return dest

    src = existing or _system_vulkan_dll()
    if src and src.is_file():
        try:
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
                logger.info("copied %s → %s", src, dest)
            return dest
        except OSError as exc:
            logger.warning("copy vulkan loader failed: %s", exc)
            return src

    raise RuntimeError(VULKAN_MISSING_HINT)


def vulkan_runtime_ok() -> bool:
    try:
        return resolve_vulkan_dll() is not None or _system_vulkan_dll() is not None
    except OSError:
        return False


def humanize_upscale_error(err: str | Exception | None) -> str:
    text = str(err or "")
    lower = text.lower()
    if (
        "vulkan-1.dll" in lower
        or "vulkan-1" in lower
        or ("找不到" in text and "vulkan" in lower)
        or ("not found" in lower and "vulkan" in lower)
    ):
        return VULKAN_MISSING_HINT
    if "vkcreateinstance" in lower or "no device" in lower or "no vulkan" in lower:
        return (
            "已找到 vulkan-1.dll，但未检测到可用的 Vulkan 显卡设备。\n"
            "请安装/更新 NVIDIA、AMD 或 Intel 显卡驱动后重试。"
            "纯 CPU 云主机无法使用 AI 超分，请改用「常规高清」。"
        )
    return text


def engine_status() -> dict:
    exe = resolve_realesrgan()
    vulkan = resolve_vulkan_dll(engine_dir=exe.parent if exe else None)
    return {
        "available": bool(exe) and bool(vulkan or os.name != "nt"),
        "exe": str(exe) if exe else None,
        "vulkan": bool(vulkan),
        "vulkan_dll": str(vulkan) if vulkan else None,
        "engines_dir": str(engines_root()),
        "default_model": DEFAULT_MODEL,
        "scales": sorted(ALLOWED_SCALES),
        "models": sorted(ALLOWED_MODELS),
        "resolutions": list(RES_PRESETS.keys()),
        "hint": None if (exe and (vulkan or os.name != "nt")) else (
            VULKAN_MISSING_HINT if exe and not vulkan else
            "未找到 Real-ESRGAN 引擎，请运行 scripts/setup_upscale_engine.py"
        ),
    }


async def ensure_realesrgan(*, download: bool = True) -> Path:
    existing = resolve_realesrgan()
    if existing:
        return existing
    if not download:
        raise RuntimeError(
            "未找到 Real-ESRGAN 引擎。请运行 scripts/setup_upscale_engine.py，"
            f"或将 {ENGINE_EXE} 放到 {engines_root() / ENGINE_DIRNAME}/"
        )
    if os.name != "nt":
        raise RuntimeError("自动下载仅支持 Windows；请手动安装 realesrgan-ncnn-vulkan")

    dest_dir = engines_root() / ENGINE_DIRNAME
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "_download.zip"
    logger.info("downloading Real-ESRGAN engine → %s", dest_dir)

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", ENGINE_ZIP_URL) as resp:
                resp.raise_for_status()
                with zip_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 256):
                        f.write(chunk)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"下载超分引擎失败：{exc}") from exc

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("超分引擎压缩包损坏，请重试下载") from exc
    finally:
        zip_path.unlink(missing_ok=True)

    exe = dest_dir / ENGINE_EXE
    if not exe.is_file():
        for found in dest_dir.rglob(ENGINE_EXE):
            if found.is_file():
                if found.parent.resolve() != dest_dir.resolve():
                    for item in found.parent.iterdir():
                        target = dest_dir / item.name
                        if target.exists():
                            continue
                        shutil.move(str(item), str(target))
                break
    exe = resolve_realesrgan()
    if not exe:
        raise RuntimeError(f"引擎已下载但未找到 {ENGINE_EXE}，请检查 {dest_dir}")
    try:
        ensure_vulkan_loader(engine_dir=exe.parent)
    except RuntimeError as exc:
        logger.warning("Vulkan loader not available after engine download: %s", exc)
    return exe


async def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 3600,
    label: str = "cmd",
) -> None:
    logger.info("%s: %s", label, " ".join(cmd[:16]) + ("…" if len(cmd) > 16 else ""))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{label} 超时") from None
    if proc.returncode != 0:
        msg = (err or b"").decode("utf-8", errors="ignore")[-800:]
        raw = msg.strip() or f"{label} 失败 (code={proc.returncode})"
        raise RuntimeError(humanize_upscale_error(raw) if label == "超分" else raw)


async def _probe_fps_and_audio(path: Path) -> tuple[float, bool]:
    ffmpeg = get_ffmpeg_exe()
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-i",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **hidden_subprocess_kwargs(),
    )
    _out, err = await proc.communicate()
    text = (err or b"").decode("utf-8", errors="ignore")
    fps = 0.0
    m = re.search(r"([\d.]+)\s*fps", text)
    if m:
        fps = float(m.group(1))
    else:
        m = re.search(r"([\d.]+)\s*tbr", text)
        if m:
            fps = float(m.group(1))
    if fps <= 0:
        fps = 24.0
    has_audio = bool(re.search(r"Stream\s+#\d+:\d+.*?Audio:", text, re.I))
    return fps, has_audio


def _even(n: int) -> int:
    return max(2, int(n) // 2 * 2)


def _target_size(sw: int, sh: int, target_res: str) -> tuple[int, int]:
    key = (target_res or "1080").strip().lower()
    if key not in RES_PRESETS:
        raise ValueError("目标分辨率仅支持 720 / 1080 / 2k / 4k")
    short = RES_PRESETS[key]
    # Fit so the shorter side reaches `short` (or longer side for landscape height)
    if sh <= sw:
        # landscape / square — target height
        th = short
        tw = _even(int(round(sw * th / sh)))
        th = _even(th)
    else:
        # portrait — target width
        tw = short
        th = _even(int(round(sh * tw / sw)))
        tw = _even(tw)
    return tw, th


def _pick_sr_scale(sw: int, sh: int, tw: int, th: int) -> int:
    """Smallest AI scale (2/3/4) that covers target; 1 means no AI needed."""
    need = max(tw / sw, th / sh)
    if need <= 1.05:
        return 1
    for s in (2, 3, 4):
        if s + 0.01 >= need:
            return s
    return 4


def _resolve_model(algo: str, engine_tier: str) -> str:
    a = (algo or "v3").strip().lower()
    tier = (engine_tier or "pro").strip().lower()
    if a == "v2":
        return "realesr-animevideov3"
    # v3 效果超分
    if tier == "pro":
        return "realesrgan-x4plus-anime"
    return "realesr-animevideov3"


def _crf_and_threads(engine_tier: str) -> tuple[str, str, str]:
    tier = (engine_tier or "pro").strip().lower()
    if tier == "pro":
        return "16", "slow", "2:2:2"
    return "20", "medium", "1:2:2"


async def _basic_enhance(
    src: Path,
    out: Path,
    *,
    tw: int,
    th: int,
    src_fps: float,
    target_fps: float | None,
    has_audio: bool,
    crf: str,
    preset: str,
) -> tuple[int, int]:
    ffmpeg = get_ffmpeg_exe()
    out_fps = float(target_fps) if target_fps and target_fps > 0 else 0.0
    vf_parts = [f"scale={tw}:{th}:flags=lanczos"]
    if out_fps > src_fps + 0.5:
        # Soft motion blend upsample (no extra RIFE engine)
        vf_parts.append(f"minterpolate=fps={out_fps:.3f}:mi_mode=blend")
    elif out_fps > 0 and out_fps < src_fps - 0.5:
        vf_parts.append(f"fps={out_fps:.3f}")
    vf = ",".join(vf_parts)
    cmd = [ffmpeg, "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-crf", crf, "-preset", preset]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    await _run_cmd(cmd, timeout=900, label="常规高清")
    ow, oh = await probe_size(out)
    return ow or tw, oh or th


async def upscale_video(
    src: Path,
    out: Path,
    *,
    scale: int | None = None,
    model: str | None = None,
    mode: str = "ai",
    algo: str = "v3",
    engine_tier: str = "pro",
    target_res: str = "1080",
    fps: float | None = None,
    download_engine: bool = True,
) -> tuple[int, int]:
    """Enhance a local video. Returns (width, height) of output."""
    sw, sh = await probe_size(src)
    if not sw or not sh:
        raise RuntimeError("无法读取视频尺寸")
    src_fps, has_audio = await _probe_fps_and_audio(src)
    duration = await probe_duration(src) or 0.0
    if duration > 180:
        raise ValueError("超分暂仅支持 3 分钟以内的视频（画布短片场景）")

    res_key = (target_res or "").strip().lower()
    if res_key in RES_PRESETS:
        tw, th = _target_size(sw, sh, res_key)
        sr_scale = _pick_sr_scale(sw, sh, tw, th)
    elif scale is not None and int(scale) in ALLOWED_SCALES:
        tw, th = _even(sw * int(scale)), _even(sh * int(scale))
        sr_scale = int(scale)
    else:
        tw, th = _target_size(sw, sh, "1080")
        sr_scale = _pick_sr_scale(sw, sh, tw, th)

    crf, preset, threads = _crf_and_threads(engine_tier)
    target_fps = float(fps) if fps and fps > 0 else None
    mode_l = (mode or "ai").strip().lower()

    if mode_l == "basic" or sr_scale == 1:
        return await _basic_enhance(
            src,
            out,
            tw=tw,
            th=th,
            src_fps=src_fps,
            target_fps=target_fps,
            has_audio=has_audio,
            crf=crf,
            preset=preset,
        )

    use_model = (model or "").strip() or _resolve_model(algo, engine_tier)
    # Fixed 4x models: if we only need 2x/3x, prefer animevideov3
    fixed = FIXED_SCALE_MODELS.get(use_model)
    if fixed is not None and sr_scale != fixed:
        if sr_scale < 4:
            use_model = "realesr-animevideov3"
        else:
            sr_scale = fixed
    if use_model not in ALLOWED_MODELS:
        raise ValueError(f"不支持的模型：{use_model}")
    if sr_scale not in ALLOWED_SCALES:
        raise ValueError("超分倍率仅支持 2 / 3 / 4")

    exe = await ensure_realesrgan(download=download_engine)
    try:
        ensure_vulkan_loader(engine_dir=exe.parent)
    except RuntimeError as exc:
        raise RuntimeError(humanize_upscale_error(exc)) from exc
    ffmpeg = get_ffmpeg_exe()
    work = Path(tempfile.mkdtemp(prefix="doubao_sr_", dir=str(_temp_work_parent())))
    src_frames = work / "src"
    sr_frames = work / "sr"
    src_frames.mkdir(parents=True, exist_ok=True)
    sr_frames.mkdir(parents=True, exist_ok=True)

    try:
        await _run_cmd(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-vsync",
                "0",
                "-q:v",
                "2",
                str(src_frames / "%08d.jpg"),
            ],
            timeout=600,
            label="拆帧",
        )
        frames = sorted(src_frames.glob("*.jpg"))
        if not frames:
            raise RuntimeError("拆帧失败：未得到任何帧")

        await _run_cmd(
            [
                str(exe),
                "-i",
                str(src_frames),
                "-o",
                str(sr_frames),
                "-n",
                use_model,
                "-s",
                str(sr_scale),
                "-f",
                "jpg",
                "-j",
                threads,
            ],
            cwd=exe.parent,
            timeout=max(1800.0, len(frames) * 10.0),
            label="超分",
        )
        out_frames = sorted(sr_frames.glob("*.jpg"))
        if len(out_frames) < len(frames):
            raise RuntimeError(
                f"超分不完整：输入 {len(frames)} 帧，输出 {len(out_frames)} 帧"
                "（可能是显存不足，请降低目标分辨率或关闭其他占 GPU 的程序）"
            )

        final_dir = work / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(out_frames, start=1):
            frame.replace(final_dir / f"{i:08d}.jpg")

        out_fps = target_fps if target_fps and target_fps > 0 else src_fps
        fps_str = f"{round(out_fps * 1000) / 1000:.3f}".rstrip("0").rstrip(".")

        # After AI upscale, fit exactly to target (may downscale from 2x/3x/4x)
        vf = f"scale={tw}:{th}:flags=lanczos"
        if target_fps and target_fps > src_fps + 0.5:
            # Compose at source fps first into temp, then interpolate — simpler: scale only here
            # and use minterpolate on the scaled sequence via -vf after image2
            vf = f"scale={tw}:{th}:flags=lanczos,minterpolate=fps={out_fps:.3f}:mi_mode=blend"
            fps_in = f"{round(src_fps * 1000) / 1000:.3f}".rstrip("0").rstrip(".")
        else:
            fps_in = fps_str

        compose = [
            ffmpeg,
            "-y",
            "-framerate",
            fps_in,
            "-i",
            str(final_dir / "%08d.jpg"),
            "-i",
            str(src),
            "-vf",
            vf,
            "-map",
            "0:v:0",
        ]
        if has_audio:
            compose += ["-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k"]
        compose += [
            "-c:v",
            "libx264",
            "-crf",
            crf,
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ]
        await _run_cmd(compose, timeout=1200, label="合成")

        ow, oh = await probe_size(out)
        return ow or tw, oh or th
    finally:
        shutil.rmtree(work, ignore_errors=True)
