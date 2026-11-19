"""Live clipping: record a stream, transcribe it in windows as it plays, and clip highlights.

When the stream ends (or Stop is pressed) the full recording gets the regular pass for long clips.
"""
import re
import subprocess
import threading
import time
from pathlib import Path

from clipper.download import extract, source_info
from clipper.jobs import new_manifest, render_clip, update_job, write_json
from clipper.moments import select_moments
from clipper.transcribe import transcribe

STOP_FILE = "STOP"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
CHANNEL_PAGE = re.compile(r"^https?://(www\.|m\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?$")


def live_url(url: str) -> str:
    """A YouTube channel link points at its current stream via /live."""
    return url.rstrip("/") + "/live" if CHANNEL_PAGE.match(url) else url


def live_status(url: str) -> dict:
    try:
        info = extract(live_url(url))
    except Exception as error:
        return {"live": False, "status": "not_live", "message": ANSI.sub("", str(error)).splitlines()[0][:200]}
    return {
        "live": bool(info.get("is_live")),
        "status": info.get("live_status") or ("is_live" if info.get("is_live") else "not_live"),
        "title": info.get("title"),
        "url": info.get("webpage_url"),
    }


class Recorder:
    """FFmpeg copies the live stream to an MPEG-TS file, which stays readable while it grows."""

    def __init__(self, stream_url: str, headers: dict, path: Path, log_path: Path):
        header_text = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        self.seconds = 0.0
        self._log = open(log_path, "ab")
        self.process = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1",
             *(["-headers", header_text] if header_text else []),
             "-i", stream_url, "-map", "0", "-c", "copy", "-f", "mpegts", str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
        )
        threading.Thread(target=self._read_progress, daemon=True).start()

    def _read_progress(self) -> None:
        for line in self.process.stdout:
            key, _, value = line.decode(errors="ignore").strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                self.seconds = int(value) / 1_000_000

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        if self.running:
            try:
                # "q" makes FFmpeg finish the file cleanly.
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
                self.process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=10)
        self._log.close()


def _clock(seconds: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def run_live(url: str, job_dir: Path, permission: str, creator: str | None, live_shorts: int,
             shorts: int, longs: int, niche: str, model: str, language: str | None, window: float) -> None:
    update_job(job_dir, stage="Finding the live stream")
    # Live streams come as combined audio+video HLS, which FFmpeg can record directly.
    info = extract(live_url(url), "b[height<=1080]/b")
    if not info.get("is_live"):
        raise RuntimeError(f"Not live right now (status: {info.get('live_status') or 'unknown'}).")
    stream_url = info["url"]
    source = {**source_info(info, url), "creator": creator or info.get("uploader") or info.get("channel") or "unknown"}
    manifest = new_manifest(job_dir, source, permission)
    recording = job_dir / "recording.ts"

    recorder = Recorder(stream_url, info.get("http_headers") or {}, recording, job_dir / "recorder.log")
    print(f"Recording {source['title']} by {source['creator']}")
    transcript = {"language": None, "segments": [], "words": []}
    processed = 0.0

    while True:
        if (job_dir / STOP_FILE).exists() and recorder.running:
            update_job(job_dir, state="stopping", stage="Stopping the recording")
            recorder.stop()
        ended = not recorder.running
        available = recorder.seconds - processed
        if available >= window + (0 if ended else 15):
            length = window
        elif ended and available > 5:
            length = available
        elif ended:
            break
        else:
            update_job(job_dir, stage=f"Live: {_clock(recorder.seconds)} recorded, {len(manifest['clips'])} clips")
            time.sleep(5)
            continue

        update_job(job_dir, stage=f"Transcribing {_clock(processed)}-{_clock(processed + length)}")
        part = transcribe(str(recording), model, language, processed, length)
        transcript["language"] = transcript["language"] or part["language"]
        transcript["segments"] += part["segments"]
        transcript["words"] += part["words"]
        write_json(job_dir / "transcript.json", transcript)

        if live_shorts > 0 and part["segments"]:
            update_job(job_dir, stage=f"Clipping {_clock(processed)}-{_clock(processed + length)}")
            plan = select_moments(str(recording), part, live_shorts, 0, niche, processed, length, manifest["clips"])
            for clip in plan["shorts"]:
                render_clip(job_dir, manifest, source, str(recording), transcript["words"], clip, True, plan["selector"])
        processed += length

    recorder.stop()
    if processed == 0:
        raise RuntimeError("Nothing was recorded from the stream; see recorder.log in the job folder.")
    update_job(job_dir, state="running", stage="Stream over: making clips from the full recording")
    print(f"Recorded {_clock(processed)}. Making clips from the full recording.")
    plan = select_moments(str(recording), transcript, 0 if live_shorts > 0 else shorts, longs, niche)
    for kind, vertical in (("shorts", True), ("long", False)):
        for clip in plan[kind]:
            render_clip(job_dir, manifest, source, str(recording), transcript["words"], clip, vertical, plan["selector"])
