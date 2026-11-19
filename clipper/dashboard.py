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
import urllib.request
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

HOST = "127.0.0.1"
PORT = int(os.getenv("CLIPPER_DASHBOARD_PORT", "8765"))
WEB_DIR = Path(__file__).resolve().parent / "web"
CREATORS_FILE = CLIPS_DIR / "creators.json"
CLIP_FILE = re.compile(r"^(short|long)_\d{2,3}\.mp4$")
UPLOAD_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
REVIEW_STATES = {"pending_review", "approved", "rejected"}
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
AGENT_PORT = int(os.getenv("AGENT_PORT", "3456"))
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
AGENT_LOG = CLIPS_DIR / "agent.log"
INSPIRATION_URL = "https://raw.githubusercontent.com/yihui-dev/awesome-opus5-5-videos/main/data/videos.json"
STUDIO_ROOT = ROOT / "studio"
STUDIO_GUI = STUDIO_ROOT / "gui.py"
STUDIO_LOG = ROOT / "logs" / "video-studio.log"
STUDIO_PORT = int(os.getenv("AGENTTUBE_STUDIO_PORT", "7070"))
STUDIO_URL = f"http://127.0.0.1:{STUDIO_PORT}"
PEAKCUT_ROOT = ROOT / "peakcut"
PEAKCUT_PORT = int(os.getenv("AGENTTUBE_PEAKCUT_PORT", "8767"))
PEAKCUT_URL = f"http://127.0.0.1:{PEAKCUT_PORT}"
PEAKCUT_LOG = ROOT / "logs" / "peakcut.log"
MPT_ROOT = ROOT / "moneyprinterturbo"
MPT_PORT = int(os.getenv("AGENTTUBE_MPT_PORT", "8768"))
MPT_URL = f"http://127.0.0.1:{MPT_PORT}"
MPT_LOG = ROOT / "logs" / "moneyprinterturbo.log"

_processes: dict[str, subprocess.Popen] = {}
_agent: dict[str, subprocess.Popen] = {}
_video_studio: dict[str, subprocess.Popen] = {}
_peakcut: dict[str, subprocess.Popen] = {}
_moneyprinterturbo: dict[str, subprocess.Popen] = {}
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
    process = _processes.get(job.get("id"))
    starting = process is not None and process.poll() is None and job.get("state") == "queued"
    if job.get("state") in ACTIVE and not starting and not _pid_alive(job.get("pid")):
        job["state"], job["stage"] = "interrupted", "The worker stopped unexpectedly; see the log"
    job["counts"] = {s: sum(1 for c in clips if c["status"] == s) for s in REVIEW_STATES}
    job["counts"]["total"] = len(clips)
    job["title"] = job.get("title") or (manifest.get("source") or {}).get("title")
    return job, manifest, clips


def list_jobs() -> list:
    if not CLIPS_DIR.exists():
        return []
    jobs = [_job_summary(p)[0] for p in CLIPS_DIR.iterdir() if p.is_dir() and JOB_ID.match(p.name)
            and (p / "job.json").exists()]
    return sorted(jobs, key=lambda j: j.get("id", ""), reverse=True)


def job_detail(job_id: str) -> dict:
    job_dir = _job_dir(job_id)
    job, manifest, clips = _job_summary(job_dir)
    log = job_dir / "log.txt"
    tail = log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else ""
    return {"job": job, "source": manifest.get("source"), "permission": manifest.get("permission"),
            "clips": clips, "blueprint": load_blueprint(job_dir), "log": tail}


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
        command += [f"--clone-topic={topic}", f"--variants={_int(body, 'variants', 3, 1, 10)}"]

    return _spawn(job_id, job_dir, command)


def start_upload(handler) -> dict:
    params = parse_qs(urlparse(handler.path).query, keep_blank_values=True)
    body = {key: values[0] for key, values in params.items()}
    if body.get("mode", "video") != "video":
        raise BadRequest("Upload is available for finished videos, not live streams")
    permission = _text(body, "permission", 300, required=True)
    creator, niche = _text(body, "creator", 100), _text(body, "niche", 100)
    model = _text(body, "model", 40)
    if model and not re.fullmatch(r"[a-z0-9.\-]+", model):
        raise BadRequest("model must be a whisper size like base or small")

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
    destination = job_dir / f"source{extension}"
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

        shorts = _int(body, "shorts", 5, 0, 20)
        long = _int(body, "long", 1, 0, 5)
        job = {"id": job_id, "created_at": now(), "kind": "video", "source_url": f"Uploaded: {filename}",
               "title": Path(filename).stem, "creator": creator, "state": "queued", "stage": "Starting"}
        write_json(job_dir / "job.json", job)
        command = [sys.executable, "-m", "clipper", str(destination), f"--job-id={job_id}", f"--out={CLIPS_DIR}",
                   f"--shorts={shorts}", f"--long={long}", f"--permission={permission}"]
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
    if source == "clone":
        clone_id = _text(body, "clone_job", 40, required=True)
        if not JOB_ID.match(clone_id) or not (CLIPS_DIR / clone_id / "blueprint.json").exists():
            raise BadRequest("unknown clone job")
        target = clone_id
        label = f"Clone {clone_id} variant {_int(body, 'variant', 1, 1, 10)}"
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
               f"--variant={_int(body, 'variant', 1, 1, 10)}"]
    if niche:
        command.append(f"--niche={niche}")
    return _spawn(job_id, job_dir, command)


# ── inspiration (awesome-opus5-5-videos prompt library, MIT; prompts belong to their creators) ──

_inspiration: dict = {}


def inspiration(query: str, category: str) -> list:
    if "items" not in _inspiration:
        import requests
        response = requests.get(INSPIRATION_URL, timeout=30)
        response.raise_for_status()
        _inspiration["items"] = response.json()
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

def moneyprinterturbo_status() -> dict:
    process = _moneyprinterturbo.get("process")
    python = MPT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = MPT_ROOT / ".venv" / "bin" / "python"
    streamlit = MPT_ROOT / ".venv" / "Scripts" / "streamlit.exe"
    if not streamlit.is_file():
        streamlit = MPT_ROOT / ".venv" / "bin" / "streamlit"
    source_ready = (MPT_ROOT / "webui" / "Main.py").is_file()
    status = {"url": MPT_URL, "running": False,
              "managed": process is not None and process.poll() is None,
              "ready": source_ready and python.is_file() and streamlit.is_file(),
              "source": source_ready}
    try:
        with urllib.request.urlopen(f"{MPT_URL}/_stcore/health", timeout=2) as response:
            status.update(running=response.status == 200)
    except Exception:
        pass
    if MPT_LOG.exists():
        status["log"] = MPT_LOG.read_text(encoding="utf-8", errors="replace")[-3000:]
    return status


def moneyprinterturbo_start() -> dict:
    if moneyprinterturbo_status()["running"]:
        return moneyprinterturbo_status()
    if not (MPT_ROOT / "webui" / "Main.py").is_file():
        raise BadRequest("MoneyPrinterTurbo source is missing; see its setup instructions in README.md")
    python = MPT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = MPT_ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        raise BadRequest("Install MoneyPrinterTurbo dependencies first; see its setup instructions in README.md")
    streamlit = MPT_ROOT / ".venv" / "Scripts" / "streamlit.exe"
    if not streamlit.is_file():
        streamlit = MPT_ROOT / ".venv" / "bin" / "streamlit"
    if not streamlit.is_file():
        raise BadRequest("Install MoneyPrinterTurbo dependencies first; see its setup instructions in README.md")
    MPT_LOG.parent.mkdir(parents=True, exist_ok=True)
    data_dir = ROOT / "moneyprinterturbo-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log = open(MPT_LOG, "ab")
    env = {**os.environ, "MPT_HOME": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    _moneyprinterturbo["process"] = subprocess.Popen(
        [str(python), "-m", "streamlit", "run", "webui/Main.py",
         "--server.address=127.0.0.1", f"--server.port={MPT_PORT}",
         "--server.headless=true", "--browser.gatherUsageStats=false",
         "--client.toolbarMode=minimal", "--logger.hideWelcomeMessage=true",
         "--server.showEmailPrompt=false"],
        cwd=MPT_ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    log.close()
    return {"starting": True, "url": MPT_URL}


def moneyprinterturbo_stop() -> None:
    process = _moneyprinterturbo.pop("process", None)
    if process is None or process.poll() is not None:
        raise BadRequest("MoneyPrinterTurbo was not started from this dashboard")
    process.terminate()
    process.wait(timeout=15)


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
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                              "script-src 'self' 'unsafe-inline'; media-src 'self'; "
                                                              f"frame-src http://127.0.0.1:{AGENT_PORT} http://127.0.0.1:{STUDIO_PORT} "
                                                              f"http://127.0.0.1:{PEAKCUT_PORT} http://127.0.0.1:{MPT_PORT}"})
            if path == "/api/agent":
                return self._json(agent_status())
            if path == "/api/studio":
                return self._json(video_studio_status())
            if path == "/api/peakcut":
                return self._json(peakcut_status())
            if path == "/api/moneyprinterturbo":
                return self._json(moneyprinterturbo_status())
            if path == "/api/inspiration":
                params = parse_qs(urlparse(self.path).query)
                try:
                    return self._json(inspiration(params.get("q", [""])[0][:100], params.get("category", [""])[0][:30]))
                except Exception as error:
                    return self._json({"error": f"Could not load the prompt library: {error}"}, 502)
            if path == "/api/creators":
                return self._json(load_creators())
            if path == "/api/jobs":
                return self._json(list_jobs())
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
            if path == "/api/moneyprinterturbo/start":
                return self._json(moneyprinterturbo_start())
            if path == "/api/moneyprinterturbo/stop":
                moneyprinterturbo_stop()
                return self._json({"ok": True})
            if path == "/api/jobs":
                return self._json(start_job(body))
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
        process = _moneyprinterturbo.get("process")
        if process is not None and process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
