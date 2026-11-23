"""Check local prerequisites for AgentTube's Clip Studio workflows."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _tool_version(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if not path:
        return False, "not found on PATH"
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode:
        return False, (result.stderr or "version check failed").strip().splitlines()[0][:160]
    output = result.stdout or result.stderr
    return True, output.strip().splitlines()[0] if output.strip() else "available"


def _print(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name}: {detail}")


def main() -> int:
    failures = 0
    warnings = 0

    python_ok = sys.version_info >= (3, 10)
    _print("OK" if python_ok else "MISSING", "Python",
           f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (requires 3.10+)")
    failures += not python_ok

    for tool in ("ffmpeg", "ffprobe"):
        available, detail = _tool_version(tool)
        _print("OK" if available else "MISSING", tool, detail)
        failures += not available

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                                     text=True, timeout=8)
            has_ass = filters.returncode == 0 and re.search(r"\bass\s+V->V\b", filters.stdout) is not None
            _print("OK" if has_ass else "MISSING", "FFmpeg subtitles",
                   "libass filter available" if has_ass else "the ass/libass filter is required for captions")
            failures += not has_ass

            encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                      text=True, timeout=8)
            has_h264 = encoders.returncode == 0 and re.search(r"\blibx264\b", encoders.stdout) is not None
            _print("OK" if has_h264 else "MISSING", "FFmpeg H.264 encoder",
                   "libx264 available" if has_h264 else "libx264 is required by the local renderer")
            failures += not has_h264
        except (OSError, subprocess.SubprocessError) as error:
            _print("MISSING", "FFmpeg capabilities", str(error))
            failures += 1

    required_modules = (
        ("yt_dlp", "yt-dlp"),
        ("faster_whisper", "faster-whisper"),
        ("numpy", "NumPy"),
        ("cv2", "OpenCV"),
        ("PIL", "Pillow"),
        ("requests", "requests"),
        ("dotenv", "python-dotenv"),
    )
    for module, label in required_modules:
        available = _module_available(module)
        _print("OK" if available else "MISSING", label, "import available" if available else "not installed in this Python environment")
        failures += not available

    if _module_available("dotenv"):
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except Exception:
            pass

    gemini_ready = bool(os.environ.get("GEMINI_API_KEY")) and _module_available("google.genai")
    if gemini_ready:
        _print("OK", "Gemini", "configured; key value is not displayed")
    else:
        warnings += 1
        _print("WARN", "Gemini", "needed for AI clip selection and clone scripts; configure GEMINI_API_KEY and google-genai")

    voice_ready = _module_available("edge_tts") or _module_available("pyttsx3")
    if voice_ready:
        _print("OK", "Video Generator voice", "at least one voice backend is installed")
    else:
        warnings += 1
        _print("WARN", "Video Generator voice", "install edge-tts or pyttsx3 to create narrated videos")

    _print("SUMMARY", "Result", f"{failures} required check(s) failed; {warnings} optional warning(s)")
    return 1 if failures else 0