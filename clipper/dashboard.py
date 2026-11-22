"""Clip Studio dashboard: manage creators, start video/live clipping jobs, and review clips.

Run: python -m clipper.dashboard   (then open http://127.0.0.1:8765)
"""
import json
import mimetypes
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv

from clipper.cli import CLIPS_DIR
from clipper.clone import load_blueprint
from clipper.create import LOOKS
from clipper.download import ROOT, _node_path
from clipper.jobs import JOB_ID, new_job_id, now, read_json, update_job, write_json
from clipper.live import STOP_FILE, live_status
from clipper import editing, post_scheduler, publishing

HOST = "127.0.0.1"
PORT = int(os.getenv("CLIPPER_DASHBOARD_PORT", "8765"))
WEB_DIR = Path(__file__).resolve().parent / "web"
CREATORS_FILE = CLIPS_DIR / "creators.json"
CLIP_FILE = re.compile(r"^(short|long)_\d{2,3}(?:_edit_\d{2})?\.mp4$")
SOURCE_ENTRY = "__source__"
SOURCE_MEDIA_FILE = re.compile(r"^(?:source\.[a-z0-9]{2,5}|recording\.ts)$", re.IGNORECASE)
UPLOAD_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
REVIEW_STATES = {"pending_review", "approved", "rejected"}
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
AGENT_PORT = int(os.getenv("AGENT_PORT", "3456"))
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
AGENT_LOG = CLIPS_DIR / "agent.log"
INSPIRATION_URL = "https://raw.githubusercontent.com/yihui-dev/awesome-opus5-5-videos/main/data/videos.json"
INSPIRATION_BACKUP = ROOT / "repo" / "awesome-opus5-5-videos-main" / "data" / "videos.json"
STUDIO_ROOT = ROOT / "studio"
STUDIO_GUI = STUDIO_ROOT / "gui.py"
STUDIO_LOG = ROOT / "logs" / "video-studio.log"
STUDIO_PORT = int(os.getenv("AGENTTUBE_STUDIO_PORT", "7070"))
STUDIO_URL = f"http://127.0.0.1:{STUDIO_PORT}"
PEAKCUT_ROOT = ROOT / "peakcut"
PEAKCUT_PORT = int(os.getenv("AGENTTUBE_PEAKCUT_PORT", "8767"))
PEAKCUT_URL = f"http://127.0.0.1:{PEAKCUT_PORT}"
PEAKCUT_LOG = ROOT / "logs" / "peakcut.log"
_processes: dict[str, subprocess.Popen] = {}
_agent: dict[str, subprocess.Popen] = {}
_video_studio: dict[str, subprocess.Popen] = {}
_peakcut: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
ACTIVE = ("queued", "running", "stopping")


class BadRequest(Exception):
    pass


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    # os.kill(pid, 0) would terminate the process on Windows, so ask the kernel instead.
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def _text(body: dict, key: str, limit: int = 300, required: bool = False) -> str:
    value = str(body.get(key) or "").strip()[:limit]
    if required and not value:
        raise BadRequest(f"{key} is required")
    return value


def _url(body: dict, key: str = "url") -> str:
    value = _text(body, key, 500, required=True)
    if urlparse(value).scheme not in ("http", "https") or not urlparse(value).netloc:
        raise BadRequest(f"{key} must be an http(s) link")
    return value


def _int(body: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(body.get(key, default))))
    except (TypeError, ValueError):
        raise BadRequest(f"{key} must be a number")


# ── creators ────────────────────────────────────────────────────────────────

def load_creators() -> list:
    return read_json(CREATORS_FILE, [])


def save_creator(body: dict) -> dict:
    creator = {
        "id": _text(body, "id", 20) or secrets.token_hex(4),
        "name": _text(body, "name", 100, required=True),
        "channel_url": _url(body, "channel_url"),
        "permission": _text(body, "permission", 300, required=True),
        "niche": _text(body, "niche", 100),
    }
    with _lock:
        creators = [c for c in load_creators() if c["id"] != creator["id"]] + [creator]
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        write_json(CREATORS_FILE, creators)
    return creator


def delete_creator(creator_id: str) -> None:
    with _lock:
        write_json(CREATORS_FILE, [c for c in load_creators() if c["id"] != creator_id])


# ── jobs ────────────────────────────────────────────────────────────────────

def _job_dir(job_id: str) -> Path:
    if not JOB_ID.match(job_id or ""):
        raise BadRequest("unknown job")
    path = CLIPS_DIR / job_id
    if not (path / "job.json").exists():
        raise BadRequest("unknown job")
    return path


def _job_summary(job_dir: Path) -> tuple:
    job = read_json(job_dir / "job.json", {})
    manifest = read_json(job_dir / "manifest.json", {}) or {}
    review = read_json(job_dir / "review.json", {}) or {}
    clips = [{**c, "status": review.get(c["file"], c.get("status", "pending_review"))}
             for c in manifest.get("clips", [])]
    editor_clips = read_json(job_dir / "editor_clips.json", []) or []
    clips.extend({**c, "status": review.get(c["file"], c.get("status", "pending_review"))}
                 for c in editor_clips)
    process = _processes.get(job.get("id"))
    starting = process is not None and process.poll() is None and job.get("state") == "queued"
    if job.get("state") in ACTIVE and not starting and not _pid_alive(job.get("pid")):
        job["state"], job["stage"] = "interrupted", "The worker stopped unexpectedly; see the log"
    job["counts"] = {s: sum(1 for c in clips if c["status"] == s) for s in REVIEW_STATES}
    job["counts"]["total"] = len(clips)
    job["title"] = job.get("title") or (manifest.get("source") or {}).get("title")
    job["source_file"] = _source_file(job_dir, job)
    return job, manifest, clips


def list_jobs() -> list:
    if not CLIPS_DIR.exists():
        return []
    jobs = [_job_summary(p)[0] for p in CLIPS_DIR.iterdir() if p.is_dir() and JOB_ID.match(p.name)
            and (p / "job.json").exists()]
    return sorted(jobs, key=lambda j: j.get("id", ""), reverse=True)


def delete_job(job_id: str) -> None:
    with _lock:
        job_dir = _job_dir(job_id)
        job, _, _ = _job_summary(job_dir)
        if job.get("state") not in {"failed", "interrupted", "stopped"}:
            raise BadRequest("Only failed, interrupted, or stopped jobs can be deleted")
        process = _processes.get(job_id)
        worker_alive = process.poll() is None if process is not None else _pid_alive(job.get("pid"))
        if worker_alive:
            raise BadRequest("Wait for the job worker to stop before deleting it")
        try:
            shutil.rmtree(job_dir)
        except OSError as error:
            raise BadRequest(f"Could not delete job files: {error}") from error
        _processes.pop(job_id, None)


def job_detail(job_id: str) -> dict:
    job_dir = _job_dir(job_id)
    job, manifest, clips = _job_summary(job_dir)
    published = read_json(job_dir / "publications.json", {}) or {}
    clips = [{**clip, "publications": published.get(clip["file"], [])} for clip in clips]
    source_file = job.get("source_file")
    source_duration = _source_duration(job_dir, job, manifest) if source_file else None
    log = job_dir / "log.txt"
    tail = log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else ""
    return {"job": job, "source": manifest.get("source"), "permission": manifest.get("permission"),
            "source_file": source_file, "source_duration": source_duration,
            "clips": clips, "blueprint": load_blueprint(job_dir), "log": tail}


def _source_candidates(job_dir: Path, job: dict) -> list[Path]:
    return ([job_dir / "recording.ts"] if job.get("kind") == "live"
            else sorted(job_dir.glob("source.*")))


def _source_file(job_dir: Path, job: dict) -> str | None:
    if not job.get("source_ready") and not (job_dir / "manifest.json").is_file():
        return None
    return next((path.name for path in _source_candidates(job_dir, job) if path.is_file()), None)


def _source_duration(job_dir: Path, job: dict, manifest: dict) -> float | None:
    source = manifest.get("source") or {}
    duration = source.get("duration") or job.get("source_duration")
    try:
        value = float(duration)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    filename = _source_file(job_dir, job)
    if not filename:
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
             str(job_dir / filename)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        value = float(json.loads(result.stdout)["format"]["duration"])
        return value if value > 0 else None
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

def source_frame(job_id: str, filename: str, offset_text: str) -> bytes:
    job_dir = _job_dir(job_id)
    job = read_json(job_dir / "job.json", {}) or {}
    manifest = read_json(job_dir / "manifest.json", {}) or {}
    try:
        offset = float(offset_text)
    except (TypeError, ValueError):
        raise BadRequest("Choose a valid preview time")
    if not (offset >= 0 and offset < float("inf")):
        raise BadRequest("Choose a valid preview time")
    if filename == SOURCE_ENTRY:
        clip = None
        source_time = offset
        duration = _source_duration(job_dir, job, manifest)
        if duration is not None and offset > duration:
            raise BadRequest("Preview time must be inside the source video")
    else:
        if not editing.SOURCE_FILE.fullmatch(filename):
            raise BadRequest("Choose an original clip for layout preview")
        clip = next((item for item in manifest.get("clips", []) if item.get("file") == filename), None)
        if clip is None:
            raise BadRequest("unknown clip")
        try:
            clip_duration = float(clip.get("duration", float(clip.get("end", 0)) - float(clip.get("start", 0))))
        except (TypeError, ValueError):
            raise BadRequest("Choose a valid preview time")
        if offset > clip_duration:
            raise BadRequest("Preview time must be inside the selected clip")
        source_time = float(clip.get("start", 0)) + offset

    source_name = _source_file(job_dir, job)
    source = (job_dir / source_name).resolve() if source_name else None
    if source is None or source.parent != job_dir.resolve():
        raise BadRequest("The original source video is unavailable")
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{source_time:.3f}", "-i", str(source),
             "-frames:v", "1", "-vf", "scale=960:-2:force_original_aspect_ratio=decrease",
             "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BadRequest("FFmpeg could not extract a source preview frame") from error
    if not result.stdout:
        raise BadRequest("No source frame was available at that time")
    return result.stdout


def start_job(body: dict) -> dict:
    mode = body.get("mode")
    if mode not in ("video", "live", "clone", "create"):
        raise BadRequest("mode must be video, live, clone or create")
    if mode == "create":
        return start_create(body)
    url = _url(body)
    permission = _text(body, "permission", 300, required=mode != "clone")
    topic = _text(body, "topic", 300, required=mode == "clone")
    creator, niche = _text(body, "creator", 100), _text(body, "niche", 100)
    model = _text(body, "model", 40)
    variant_mode = _text(body, "variant_mode", 20) or "full"
    if mode == "clone" and variant_mode not in ("full", "hook_test"):
        raise BadRequest("variant_mode must be full or hook_test")
    if model and not re.fullmatch(r"[a-z0-9.\-]+", model):
        raise BadRequest("model must be a whisper size like base or small")

    job_id = new_job_id()
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(parents=True)
    write_json(job_dir / "job.json", {"id": job_id, "created_at": now(), "kind": mode, "source_url": url,
                                       "creator": creator, "state": "queued", "stage": "Starting"})

    command = [sys.executable, "-m", "clipper", url, f"--job-id={job_id}", f"--out={CLIPS_DIR}",
               f"--shorts={_int(body, 'shorts', 5, 0, 20)}", f"--long={_int(body, 'long', 1, 0, 5)}"]
    if permission:
        command.append(f"--permission={permission}")
    if creator:
        command.append(f"--creator={creator}")
    if niche:
        command.append(f"--niche={niche}")
    if model:
        command.append(f"--model={model}")
    if mode == "live":
        command += ["--live", f"--live-shorts={_int(body, 'live_shorts', 1, 0, 5)}"]
    if mode == "clone":
        minimum, maximum = (2, 100) if variant_mode == "hook_test" else (1, 10)
        command += [f"--clone-topic={topic}", f"--variant-mode={variant_mode}",
                    f"--variants={_int(body, 'variants', 3, minimum, maximum)}"]

    return _spawn(job_id, job_dir, command)


def start_upload(handler) -> dict:
    params = parse_qs(urlparse(handler.path).query, keep_blank_values=True)
    body = {key: values[0] for key, values in params.items()}
    mode = body.get("mode", "video")
    if mode not in ("video", "clone"):
        raise BadRequest("Upload is available for finished videos and clone references")
    is_clone = mode == "clone"
    permission = _text(body, "permission", 300, required=not is_clone)
    creator, niche = _text(body, "creator", 100), _text(body, "niche", 100)
    model = _text(body, "model", 40)
    if model and not re.fullmatch(r"[a-z0-9.\-]+", model):
        raise BadRequest("model must be a whisper size like base or small")
    topic = _text(body, "topic", 300, required=is_clone)
    variant_mode = _text(body, "variant_mode", 20) or "full"
    if is_clone and variant_mode not in ("full", "hook_test"):
        raise BadRequest("variant_mode must be full or hook_test")

    filename = _text(body, "filename", 240, required=True)
    filename = Path(filename.replace("\\", "/")).name
    extension = Path(filename).suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise BadRequest("Choose an MP4, MOV, MKV, WebM, M4V or AVI video")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        raise BadRequest("The video upload size is invalid")
    if length <= 0:
        raise BadRequest("The selected video is empty")
    if length > MAX_UPLOAD_BYTES:
        raise BadRequest("Videos must be 4 GB or smaller")

    job_id = new_job_id()
    job_dir = CLIPS_DIR / job_id
    destination = job_dir / f"{'reference' if is_clone else 'source'}{extension}"
    try:
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(CLIPS_DIR).free < length:
            raise BadRequest("There is not enough free disk space for this video")
        job_dir.mkdir()
        remaining = length
        with destination.open("wb") as video:
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise BadRequest("The video upload ended before the file was complete")
                video.write(chunk)
                remaining -= len(chunk)

        if is_clone:
            minimum, maximum = (2, 100) if variant_mode == "hook_test" else (1, 10)
            variants = _int(body, "variants", 3, minimum, maximum)
            job = {"id": job_id, "created_at": now(), "kind": "clone",
                   "source_url": f"Uploaded: {filename}", "title": Path(filename).stem,
                   "state": "queued", "stage": "Starting"}
        else:
            shorts = _int(body, "shorts", 5, 0, 20)
            long = _int(body, "long", 1, 0, 5)
            job = {"id": job_id, "created_at": now(), "kind": "video", "source_url": f"Uploaded: {filename}",
                   "title": Path(filename).stem, "creator": creator, "source_ready": True,
                   "state": "queued", "stage": "Starting"}
        write_json(job_dir / "job.json", job)
        command = [sys.executable, "-m", "clipper", str(destination), f"--job-id={job_id}", f"--out={CLIPS_DIR}"]
        if is_clone:
            command += [f"--clone-topic={topic}", f"--variant-mode={variant_mode}", f"--variants={variants}"]
        else:
            command += [f"--shorts={shorts}", f"--long={long}", f"--permission={permission}"]
        if creator:
            command.append(f"--creator={creator}")
        if niche:
            command.append(f"--niche={niche}")
        if model:
            command.append(f"--model={model}")
        return _spawn(job_id, job_dir, command)
    except OSError as error:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise BadRequest("Could not save the uploaded video. Check free disk space and try again.") from error
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def _spawn(job_id: str, job_dir: Path, command: list) -> dict:
    log = open(job_dir / "log.txt", "ab")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    log.close()
    _processes[job_id] = process
    return {"id": job_id}


def start_create(body: dict) -> dict:
    source = body.get("source")
    if source not in ("script", "idea", "clone"):
        raise BadRequest("source must be script, idea or clone")
    fmt = body.get("format") if body.get("format") in ("short", "long") else "short"
    look = body.get("look") if body.get("look") in LOOKS else "documentary"
    voice = _text(body, "voice", 60) or "en-US-AndrewNeural"
    if not re.fullmatch(r"[\w .-]+", voice):
        raise BadRequest("invalid voice name")
    niche = _text(body, "niche", 100)

    job_id = new_job_id()
    job_dir = CLIPS_DIR / job_id
    variant = 1
    if source == "clone":
        clone_id = _text(body, "clone_job", 40, required=True)
        blueprint_path = CLIPS_DIR / clone_id / "blueprint.json"
        if not JOB_ID.match(clone_id) or not blueprint_path.exists():
            raise BadRequest("unknown clone job")
        blueprint = read_json(blueprint_path) or {}
        variant_count = len(blueprint.get("variants") or [])
        variant = _int(body, "variant", 1, 1, max(variant_count, 1))
        target = clone_id
        label = f"Clone {clone_id} variant {variant}"
    else:
        text = str(body.get("text") or "").strip()[:60_000]
        if len(text) < 20:
            raise BadRequest("write a script or idea of at least a sentence")
        job_dir.mkdir(parents=True)
        (job_dir / "input.txt").write_text(text, encoding="utf-8")
        target = str(job_dir / "input.txt")
        label = text[:80]
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(job_dir / "job.json", {"id": job_id, "created_at": now(), "kind": "create", "source_url": label,
                                       "state": "queued", "stage": "Starting"})
    command = [sys.executable, "-m", "clipper", target, f"--create={source}", f"--job-id={job_id}",
               f"--out={CLIPS_DIR}", f"--format={fmt}", f"--look={look}", f"--voice={voice}",
               f"--variant={variant}"]
    if source == "clone" and str(body.get("match_reference") or "").lower() in ("true", "1", "on"):
        command.append("--match-reference-style")
    if niche:
        command.append(f"--niche={niche}")
    return _spawn(job_id, job_dir, command)


# ── inspiration (awesome-opus5-5-videos prompt library, MIT; prompts belong to their creators) ──

_inspiration: dict = {}


def inspiration(query: str, category: str) -> list:
    if "items" not in _inspiration:
        import requests
        try:
            response = requests.get(INSPIRATION_URL, timeout=30)
            response.raise_for_status()
            items = response.json()
        except requests.RequestException:
            with INSPIRATION_BACKUP.open(encoding="utf-8") as backup:
                items = json.load(backup)
        if not isinstance(items, list):
            raise ValueError("The inspiration catalog must be a JSON list")
        _inspiration["items"] = items
    words = [w for w in query.lower().split() if w]
    results = []
    for item in _inspiration["items"]:
        if category and item.get("category") != category:
            continue
        haystack = f"{item.get('prompt', '')} {item.get('slug', '')}".lower()
        if all(w in haystack for w in words):
            results.append({k: item.get(k) for k in ("slug", "author", "author_url", "post_url", "category",
                                                     "tech_tags", "prompt", "prompt_partial", "skillry_url")})
        if len(results) >= 60:
            break
    return results


def stop_job(job_id: str) -> None:
    job_dir = _job_dir(job_id)
    job = read_json(job_dir / "job.json", {})
    if job.get("kind") == "live":
        # The live worker finishes the recording and still makes the long clips.
        (job_dir / STOP_FILE).write_text(now(), encoding="utf-8")
        update_job(job_dir, state="stopping", stage="Stopping after the current step")
        return
    pid = job.get("pid")
    if _pid_alive(pid):
        os.kill(pid, signal.SIGTERM)
    process = _processes.get(job_id)
    if process is not None and process.poll() is None:
        process.terminate()
    update_job(job_dir, state="stopped", stage="Stopped")


def review_clip(job_id: str, body: dict) -> None:
    job_dir = _job_dir(job_id)
    file, status = _text(body, "file", 40), _text(body, "status", 20)
    if not CLIP_FILE.match(file) or status not in REVIEW_STATES:
        raise BadRequest("invalid review")
    with _lock:
        review = read_json(job_dir / "review.json", {})
        review[file] = status
        write_json(job_dir / "review.json", review)


def publish_clip(job_id: str, body: dict) -> dict:
    job_dir = _job_dir(job_id)
    job, _, clips = _job_summary(job_dir)
    if job.get("state") != "finished":
        raise BadRequest("Wait for clipping to finish before publishing")
    filename = _text(body, "file", 40, required=True)
    if not CLIP_FILE.fullmatch(filename):
        raise BadRequest("unknown clip")
    clip = next((item for item in clips if item.get("file") == filename), None)
    if clip is None:
        raise BadRequest("unknown clip")
    if clip.get("status") != "approved":
        raise BadRequest("Approve this clip before publishing")

    platforms = body.get("platforms")
    allowed = {"youtube", "instagram", "facebook", "x"}
    if not isinstance(platforms, list):
        raise BadRequest("Select at least one platform")
    platforms = list(dict.fromkeys(platform for platform in platforms if platform in allowed))
    if not platforms:
        raise BadRequest("Select at least one supported platform")
    title = _text(body, "title", 100, required=True)
    caption = _text(body, "caption", 5000)
    privacy = _text(body, "privacy", 20) or "private"
    if privacy not in {"private", "unlisted", "public"}:
        raise BadRequest("YouTube privacy must be private, unlisted, or public")

    configured = publishing.publishing_status()
    video = (job_dir / filename).resolve()
    if job_dir.resolve() not in video.parents or not video.is_file():
        raise BadRequest("The selected clip file is missing")

    results = []
    for platform in platforms:
        if not configured[platform]["configured"]:
            results.append({"platform": platform, "ok": False,
                            "error": configured[platform]["message"]})
            continue
        if platform == "facebook" and clip.get("duration", 0) > 90:
            results.append({"platform": platform, "ok": False,
                            "error": "Facebook Reels must be 90 seconds or shorter"})
            continue
        try:
            published = publishing.publish_video(platform, video, title, caption, privacy)
            results.append({"platform": platform, "ok": True, **published})
        except Exception as error:
            results.append({"platform": platform, "ok": False, "error": str(error)[:500]})

    successful = [result for result in results if result["ok"]]
    if successful:
        with _lock:
            history = read_json(job_dir / "publications.json", {}) or {}
            entries = history.setdefault(filename, [])
            entries.extend({"platform": result["platform"], "url": result["url"],
                            "title": title, "created_at": now()} for result in successful)
            write_json(job_dir / "publications.json", history)
    return {"results": results}


def schedule_clip(job_id: str, body: dict) -> dict:
    job_dir = _job_dir(job_id)
    job, _, clips = _job_summary(job_dir)
    if job.get("state") != "finished":
        raise BadRequest("Wait for clipping to finish before scheduling")
    filename = _text(body, "file", 40, required=True)
    if not CLIP_FILE.fullmatch(filename):
        raise BadRequest("unknown clip")
    clip = next((item for item in clips if item.get("file") == filename), None)
    if clip is None:
        raise BadRequest("unknown clip")
    if clip.get("status") != "approved":
        raise BadRequest("Approve this clip before scheduling")
    platforms = body.get("platforms")
    allowed = {"youtube", "instagram", "facebook", "x"}
    if not isinstance(platforms, list) or not platforms:
        raise BadRequest("Select at least one platform")
    if any(not isinstance(platform, str) or platform not in allowed for platform in platforms):
        raise BadRequest("Select supported publishing platforms")
    platforms = list(dict.fromkeys(platforms))
    configured = publishing.publishing_status()
    unavailable = [platform for platform in platforms if not configured[platform]["configured"]]
    if unavailable:
        raise BadRequest("Configure these platforms in Agenttube first: " + ", ".join(unavailable))
    if "facebook" in platforms and clip.get("duration", 0) > 90:
        raise BadRequest("Facebook Reels must be 90 seconds or shorter")
    title = _text(body, "title", 100, required=True)
    caption = _text(body, "caption", 5000)
    privacy = _text(body, "privacy", 20) or "public"
    if privacy not in {"private", "unlisted", "public"}:
        raise BadRequest("YouTube privacy must be private, unlisted, or public")
    scheduled_text = _text(body, "scheduled_at", 50, required=True)
    try:
        scheduled_at = datetime.fromisoformat(scheduled_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BadRequest("Choose a valid publication date and time") from error
    if scheduled_at.tzinfo is None or scheduled_at <= datetime.now(timezone.utc):
        raise BadRequest("Choose a future publication time with a timezone")
    video = (job_dir / filename).resolve()
    if job_dir.resolve() not in video.parents or not video.is_file():
        raise BadRequest("The selected clip file is missing")
    return post_scheduler.schedule_post(
        job_id, job.get("title") or filename, filename, platforms, title, caption, privacy, scheduled_at,
    )


def trypost_post(job_id: str, body: dict) -> dict:
    job_dir = _job_dir(job_id)
    job, _, clips = _job_summary(job_dir)
    if job.get("state") != "finished":
        raise BadRequest("Wait for clipping to finish before sending to TryPost")
    filename = _text(body, "file", 40, required=True)
    if not CLIP_FILE.fullmatch(filename):
        raise BadRequest("unknown clip")
    clip = next((item for item in clips if item.get("file") == filename), None)
    if clip is None:
        raise BadRequest("unknown clip")
    if clip.get("status") != "approved":
        raise BadRequest("Approve this clip before sending it to TryPost")
    media_format = clip.get("format")
    if media_format not in {"short", "long"}:
        raise BadRequest("Unsupported video format")

    account_ids = body.get("social_account_ids")
    if not isinstance(account_ids, list) or not account_ids:
        raise BadRequest("Select at least one TryPost account")
    content_types = body.get("content_types")
    if content_types is not None and not isinstance(content_types, dict):
        raise BadRequest("Invalid TryPost content formats")
    title = _text(body, "title", 100, required=True)
    caption = _text(body, "caption", 5000)
    status = _text(body, "status", 20) or "draft"
    if status not in {"draft", "scheduled", "publishing"}:
        raise BadRequest("Choose draft, scheduled, or publish now")
    scheduled_at = _text(body, "scheduled_at", 50)
    scheduled_times = body.get("scheduled_times")
    if status == "scheduled":
        if isinstance(scheduled_times, dict):
            schedule_by_account = {str(account_id): scheduled_times.get(str(account_id)) for account_id in account_ids}
        elif scheduled_at:
            schedule_by_account = {str(account_id): scheduled_at for account_id in account_ids}
        else:
            raise BadRequest("Choose a publication time for every selected account")
        for value in schedule_by_account.values():
            if not isinstance(value, str):
                raise BadRequest("Choose a publication time for every selected account")
            try:
                scheduled = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise BadRequest("Choose a valid publication date and time") from error
            if scheduled.tzinfo is None or scheduled <= datetime.now(timezone.utc):
                raise BadRequest("Choose a future publication time with a timezone")
        scheduled_times = schedule_by_account
    privacy = _text(body, "privacy", 20) or "public"
    video = (job_dir / filename).resolve()
    if job_dir.resolve() not in video.parents or not video.is_file():
        raise BadRequest("The selected clip file is missing")

    try:
        created = publishing.handoff_to_trypost(
            video, title, caption, account_ids, status=status,
            scheduled_at=scheduled_at or None, privacy=privacy, media_format=media_format,
            scheduled_times=scheduled_times, content_types=content_types,
        )
    except Exception as error:
        raise BadRequest(str(error)[:500]) from error
    with _lock:
        history = read_json(job_dir / "publications.json", {}) or {}
        entries = history.setdefault(filename, [])
        entries.extend({"platform": destination["platform"], "service": "TryPost",
                "url": created["url"], "id": destination["id"],
                        "title": f"{destination['platform'].title()} {status}: {title}",
                        "status": status, "created_at": now()}
                   for destination in created["destinations"])
        write_json(job_dir / "publications.json", history)
    return {"ok": True, **created}


def youtube_post(job_id: str, body: dict) -> dict:
    job_dir = _job_dir(job_id)
    job, _, clips = _job_summary(job_dir)
    if job.get("state") != "finished":
        raise BadRequest("Wait for clipping to finish before sending to YouTube")
    filename = _text(body, "file", 40, required=True)
    if not CLIP_FILE.fullmatch(filename):
        raise BadRequest("unknown clip")
    clip = next((item for item in clips if item.get("file") == filename), None)
    if clip is None:
        raise BadRequest("unknown clip")
    if clip.get("status") != "approved":
        raise BadRequest("Approve this video before sending it to YouTube")
    if clip.get("format") != "long":
        raise BadRequest("Use TryPost for YouTube Shorts")
    configured = publishing.publishing_status()["youtube"]
    if not configured["configured"]:
        raise BadRequest(configured["message"])

    title = _text(body, "title", 100, required=True)
    caption = _text(body, "caption", 5000)
    status = _text(body, "status", 20) or "draft"
    if status not in {"draft", "scheduled", "publishing"}:
        raise BadRequest("Choose draft, scheduled, or publish now")
    scheduled_at = _text(body, "scheduled_at", 50)
    if status == "scheduled":
        try:
            scheduled = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise BadRequest("Choose a valid publication date and time") from error
        if scheduled.tzinfo is None or scheduled <= datetime.now(timezone.utc):
            raise BadRequest("Choose a future publication time with a timezone")
    privacy = _text(body, "privacy", 20) or "private"
    if privacy not in {"private", "unlisted", "public"}:
        raise BadRequest("YouTube privacy must be private, unlisted, or public")
    video = (job_dir / filename).resolve()
    if job_dir.resolve() not in video.parents or not video.is_file():
        raise BadRequest("The selected video file is missing")

    upload_privacy = "private" if status in {"draft", "scheduled"} else privacy
    publish_at = scheduled_at if status == "scheduled" else None
    try:
        uploaded = publishing.publish_video(
            "youtube", video, title, caption, upload_privacy, scheduled_at=publish_at,
        )
    except Exception as error:
        raise BadRequest(str(error)[:500]) from error
    result_status = "uploaded privately (draft)" if status == "draft" else "scheduled" if status == "scheduled" else (
        "published" if privacy == "public" else f"uploaded {privacy}"
    )
    with _lock:
        history = read_json(job_dir / "publications.json", {}) or {}
        entries = history.setdefault(filename, [])
        entries.append({"platform": "youtube", "url": uploaded["url"], "id": uploaded.get("id"),
                       "title": f"{result_status}: {title}", "status": result_status, "created_at": now()})
        write_json(job_dir / "publications.json", history)
    return {"ok": True, **uploaded, "status": result_status}


# ── Automation Agent (the original Agenttube Node server) ─────────────────────────

def agent_status() -> dict:
    process = _agent.get("process")
    status = {"url": AGENT_URL, "running": False, "managed": process is not None and process.poll() is None,
              "ready": (ROOT / "node_modules" / "express").exists()}
    try:
        with urllib.request.urlopen(f"{AGENT_URL}/health", timeout=2) as response:
            status.update(running=True, health=json.loads(response.read()))
    except Exception:
        pass
    if AGENT_LOG.exists():
        status["log"] = AGENT_LOG.read_text(encoding="utf-8", errors="replace")[-3000:]
    return status


def agent_start() -> dict:
    if agent_status()["running"]:
        return agent_status()
    node = _node_path()
    if not node:
        raise BadRequest("Node.js was not found")
    if not (ROOT / "node_modules" / "express").exists():
        raise BadRequest("Run npm install in the agenttube folder first")
    log = open(AGENT_LOG, "ab")
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(AGENT_PORT),
           "NODE_OPTIONS": "--use-system-ca", "FORCE_COLOR": "0"}
    _agent["process"] = subprocess.Popen([node, "index.js"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    log.close()
    return {"starting": True}


def agent_stop() -> None:
    process = _agent.pop("process", None)
    if process is None or process.poll() is not None:
        raise BadRequest("The agent was not started from this dashboard")
    process.terminate()
    process.wait(timeout=15)


def agent_api_request(method: str, path: str, body: dict | None = None) -> tuple[dict, int]:
    allowed = (
        method == "GET" and path == "/api/dashboard"
        or method == "POST" and path in {
            "/generate", "/api/automation/pause", "/api/automation/resume",
        }
        or method == "POST" and re.fullmatch(r"/api/jobs/[\w-]+/(?:cancel|resume)", path)
    )
    if not allowed:
        raise BadRequest("Unsupported Automation Agent action")
    if not agent_status()["running"]:
        raise BadRequest("Automation Agent is not ready yet")

    headers = {"Accept": "application/json"}
    api_key = os.getenv("API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{AGENT_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            content = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        content = error.read()
    except (OSError, urllib.error.URLError) as error:
        raise BadRequest("Could not reach the Automation Agent runtime") from error
    try:
        result = json.loads(content or b"{}")
    except json.JSONDecodeError:
        result = {"error": content.decode("utf-8", errors="replace")[:1000]}
    return result, status


# ── Original Video Studio ──────────────────────────────────────────────────

def video_studio_status() -> dict:
    process = _video_studio.get("process")
    status = {"url": STUDIO_URL, "running": False,
              "managed": process is not None and process.poll() is None,
              "ready": STUDIO_GUI.is_file()}
    try:
        with urllib.request.urlopen(f"{STUDIO_URL}/api/status", timeout=2) as response:
            status.update(running=True, pipeline=json.loads(response.read()))
    except Exception:
        pass
    if STUDIO_LOG.exists():
        status["log"] = STUDIO_LOG.read_text(encoding="utf-8", errors="replace")[-3000:]
    return status


def video_studio_start() -> dict:
    if video_studio_status()["running"]:
        return video_studio_status()
    if not STUDIO_GUI.is_file():
        raise BadRequest("The migrated Video Studio files are missing")
    python = ROOT / ".venv-studio" / "Scripts" / "python.exe"
    if not python.is_file():
        python = ROOT / ".venv-studio" / "bin" / "python"
    if not python.is_file():
        raise BadRequest("Install Video Studio dependencies first; see the Video Studio section in README.md")
    STUDIO_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(STUDIO_LOG, "ab")
    env = {**os.environ, "AGENTTUBE_STUDIO_EMBEDDED": "1",
           "AGENTTUBE_STUDIO_PORT": str(STUDIO_PORT), "PYTHONIOENCODING": "utf-8"}
    _video_studio["process"] = subprocess.Popen(
        [str(python), "gui.py"], cwd=STUDIO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    log.close()
    return {"starting": True, "url": STUDIO_URL}


def video_studio_stop() -> None:
    process = _video_studio.pop("process", None)
    if process is None or process.poll() is not None:
        raise BadRequest("The Video Studio was not started from this dashboard")
    process.terminate()
    process.wait(timeout=15)


# ── PeakCut ────────────────────────────────────────────────────────────────

def peakcut_status() -> dict:
    process = _peakcut.get("process")
    status = {"url": PEAKCUT_URL, "running": False,
              "managed": process is not None and process.poll() is None,
              "ready": (PEAKCUT_ROOT / "studio" / "server.py").is_file()}
    try:
        with urllib.request.urlopen(f"{PEAKCUT_URL}/healthz", timeout=2) as response:
            status.update(running=True, health=json.loads(response.read()))
    except Exception:
        pass
    if PEAKCUT_LOG.exists():
        status["log"] = PEAKCUT_LOG.read_text(encoding="utf-8", errors="replace")[-3000:]
    return status


def peakcut_start() -> dict:
    if peakcut_status()["running"]:
        return peakcut_status()
    if not (PEAKCUT_ROOT / "studio" / "server.py").is_file():
        raise BadRequest("PeakCut source files are missing")
    python = ROOT / ".venv-peakcut" / "Scripts" / "python.exe"
    if not python.is_file():
        python = ROOT / ".venv-peakcut" / "bin" / "python"
    if not python.is_file():
        raise BadRequest("Install PeakCut dependencies first; see the PeakCut section in README.md")
    PEAKCUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(PEAKCUT_LOG, "ab")
    env = {**os.environ, "PEAKCUT_HOST": "127.0.0.1", "PEAKCUT_PORT": str(PEAKCUT_PORT),
           "PEAKCUT_PUBLIC_URL": PEAKCUT_URL, "PEAKCUT_DATA": str(ROOT / "peakcut-data"),
           "PEAKCUT_STORAGE_FILE": str(ROOT / "peakcut-data" / "storage.json"),
           "PEAKCUT_REPO_URL": os.getenv("AGENTTUBE_PEAKCUT_GITHUB_URL", "https://github.com/imrkbhai/Agenttube"),
           "PEAKCUT_X_URL": os.getenv("AGENTTUBE_PEAKCUT_X_URL", "https://x.com/imd_rky"),
           "PEAKCUT_FOLLOW_URL": os.getenv("AGENTTUBE_PEAKCUT_INSTAGRAM_URL", "https://www.instagram.com/imd_rky/"),
           "AGENTTUBE_PEAKCUT_EMBEDDED": "1",
           "AGENTTUBE_DASHBOARD_ORIGINS": f"http://127.0.0.1:{PORT} http://localhost:{PORT}",
           "PYTHONIOENCODING": "utf-8"}
    _peakcut["process"] = subprocess.Popen(
        [str(python), "-m", "studio.server"], cwd=PEAKCUT_ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    log.close()
    return {"starting": True, "url": PEAKCUT_URL}


def peakcut_stop() -> None:
    process = _peakcut.pop("process", None)
    if process is None or process.poll() is not None:
        raise BadRequest("PeakCut was not started from this dashboard")
    process.terminate()
    process.wait(timeout=15)


# ── MoneyPrinterTurbo ──────────────────────────────────────────────────────

# ── HTTP ────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "ClipStudio"

    def log_message(self, *args):
        pass

    def _trusted(self) -> bool:
        # Blocks DNS-rebinding and cross-site requests to this local server.
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise BadRequest("expected JSON")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 200_000:
            raise BadRequest("request too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise BadRequest("expected a JSON object")
        return data

    def do_GET(self):
        if not self._trusted():
            return self._send(403, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        try:
            if path == "/":
                page = (WEB_DIR / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                                              "font-src 'self' https://fonts.gstatic.com; "
                                                              "script-src 'self' 'unsafe-inline'; media-src 'self'; "
                                                              f"frame-src http://127.0.0.1:{AGENT_PORT} http://127.0.0.1:{STUDIO_PORT} "
                                                              f"http://127.0.0.1:{PEAKCUT_PORT}"})
            if path == "/api/agent":
                return self._json(agent_status())
            if path == "/api/agent/dashboard":
                data, status = agent_api_request("GET", "/api/dashboard")
                return self._json(data, status)
            if path == "/api/studio":
                return self._json(video_studio_status())
            if path == "/api/peakcut":
                return self._json(peakcut_status())
            if path == "/api/inspiration":
                params = parse_qs(urlparse(self.path).query)
                try:
                    return self._json(inspiration(params.get("q", [""])[0][:100], params.get("category", [""])[0][:30]))
                except Exception as error:
                    return self._json({"error": f"Could not load the prompt library: {error}"}, 502)
            if path == "/api/creators":
                return self._json(load_creators())
            if path == "/api/publishing/status":
                return self._json(publishing.publishing_status())
            if path == "/api/scheduled-posts":
                return self._json(post_scheduler.list_posts())
            if path == "/api/jobs":
                return self._json(list_jobs())
            match = re.fullmatch(r"/api/jobs/([\w-]+)/frame", path)
            if match:
                params = parse_qs(urlparse(self.path).query)
                filename = params.get("file", [""])[0]
                offset = params.get("time", ["0"])[0]
                return self._send(200, source_frame(match.group(1), filename, offset), "image/jpeg",
                                  {"Cache-Control": "no-store"})
            match = re.fullmatch(r"/api/jobs/([\w-]+)", path)
            if match:
                return self._json(job_detail(match.group(1)))
            match = re.fullmatch(r"/media/([\w-]+)/([\w.]+)", path)
            if match:
                return self._media(match.group(1), match.group(2))
            self._send(404, b"Not found", "text/plain")
        except BadRequest as error:
            self._json({"error": str(error)}, 400)

    do_HEAD = do_GET

    def do_POST(self):
        if not self._trusted() or self.headers.get("Origin") is None:
            return self._send(403, b"Forbidden", "text/plain")
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                return self._json(start_upload(self))
            body = self._body()
            if path == "/api/creators":
                return self._json(save_creator(body))
            if path == "/api/creators/delete":
                delete_creator(_text(body, "id", 20, required=True))
                return self._json({"ok": True})
            if path == "/api/live-status":
                return self._json(live_status(_url(body)))
            if path == "/api/agent/start":
                return self._json(agent_start())
            if path == "/api/agent/stop":
                agent_stop()
                return self._json({"ok": True})
            if path == "/api/agent/generate":
                data, status = agent_api_request("POST", "/generate", body)
                return self._json(data, status)
            match = re.fullmatch(r"/api/agent/automation/(pause|resume)", path)
            if match:
                data, status = agent_api_request("POST", f"/api/automation/{match.group(1)}", body)
                return self._json(data, status)
            match = re.fullmatch(r"/api/agent/jobs/([\w-]+)/(cancel|resume)", path)
            if match:
                target = f"/api/jobs/{match.group(1)}/{match.group(2)}"
                data, status = agent_api_request("POST", target, body)
                return self._json(data, status)
            if path == "/api/studio/start":
                return self._json(video_studio_start())
            if path == "/api/studio/stop":
                video_studio_stop()
                return self._json({"ok": True})
            if path == "/api/peakcut/start":
                return self._json(peakcut_start())
            if path == "/api/peakcut/stop":
                peakcut_stop()
                return self._json({"ok": True})
            if path == "/api/jobs":
                return self._json(start_job(body))
            match = re.fullmatch(r"/api/jobs/([\w-]+)/schedule", path)
            if match:
                return self._json(schedule_clip(match.group(1), body))
            match = re.fullmatch(r"/api/scheduled-posts/([0-9a-f]{16})/cancel", path)
            if match:
                try:
                    return self._json(post_scheduler.cancel_post(match.group(1)))
                except ValueError as error:
                    raise BadRequest(str(error)) from error
            match = re.fullmatch(r"/api/jobs/([\w-]+)/edit", path)
            if match:
                try:
                    return self._json(editing.create_edit(_job_dir(match.group(1)), body))
                except editing.EditingError as error:
                    raise BadRequest(str(error)) from error
            match = re.fullmatch(r"/api/jobs/([\w-]+)/delete", path)
            if match:
                delete_job(match.group(1))
                return self._json({"ok": True})
            match = re.fullmatch(r"/api/jobs/([\w-]+)/publish", path)
            if match:
                return self._json(publish_clip(match.group(1), body))
            match = re.fullmatch(r"/api/jobs/([\w-]+)/(stop|review)", path)
            if match and match.group(2) == "stop":
                stop_job(match.group(1))
                return self._json({"ok": True})
            if match:
                review_clip(match.group(1), body)
                return self._json({"ok": True})
            self._send(404, b"Not found", "text/plain")
        except (BadRequest, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, 400)

    def _media(self, job_id: str, name: str) -> None:
        if not CLIP_FILE.match(name):
            raise BadRequest("unknown file")
        path = (_job_dir(job_id) / name).resolve()
        if CLIPS_DIR.resolve() not in path.parents or not path.is_file():
            raise BadRequest("unknown file")
        size = path.stat().st_size
        start, end = 0, size - 1
        # Range support lets the browser's video player seek.
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            else:
                start = max(0, size - int(match.group(2)))
            end = min(end, size - 1)
            if start > end:
                return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
        length = end - start + 1
        self.send_response(206 if match else 200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as file:
            file.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = file.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except ConnectionError:  # the browser stopped loading the video
                pass


def main() -> None:
    load_dotenv(ROOT / ".env")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    post_scheduler.start_worker()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Clip Studio dashboard: http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        process = _agent.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
        process = _video_studio.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
        post_scheduler.stop_worker()


if __name__ == "__main__":
    main()
