"""Download a source video with yt-dlp."""
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent.parent
BUNDLED_NODE = ROOT / ".tools" / "node-v24.19.0-win-x64" / "node.exe"


def _node_path() -> str | None:
    if os.getenv("CLIPPER_NODE"):
        return os.getenv("CLIPPER_NODE")
    if BUNDLED_NODE.exists():
        return str(BUNDLED_NODE)
    return shutil.which("node")


def check_url(url: str) -> None:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Only http(s) video links are supported.")


def ydl_options(**extra) -> dict:
    options = {"noplaylist": True, "quiet": True, "no_warnings": True, **extra}
    node = _node_path()
    if node:
        # YouTube needs a JavaScript runtime to resolve its stream URLs.
        options["js_runtimes"] = {"node": {"path": node}}
    return options


def extract(url: str, fmt: str | None = None) -> dict:
    check_url(url)
    with YoutubeDL(ydl_options(**({"format": fmt} if fmt else {}))) as ydl:
        return ydl.extract_info(url, download=False)


def source_info(info: dict, url: str) -> dict:
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "creator": info.get("uploader") or info.get("channel"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "url": info.get("webpage_url") or url,
        "license": info.get("license"),
    }


def fetch(url: str, job_dir: Path) -> dict:
    check_url(url)
    options = ydl_options(
        outtmpl=str(job_dir / "source.%(ext)s"),
        format="bv*[height<=1080]+ba/b[height<=1080]/b",
        merge_output_format="mp4",
    )
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    return {"path": info["requested_downloads"][0]["filepath"], **source_info(info, url)}
