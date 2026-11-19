"""Job folders: state (job.json), clip manifest (manifest.json) and review decisions (review.json)."""
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from clipper.render import render

JOB_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_job_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    # Write then rename, so the dashboard never reads a half-written file.
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def update_job(job_dir: Path, **fields) -> None:
    state = read_json(job_dir / "job.json", {})
    state.update(fields, updated_at=now())
    write_json(job_dir / "job.json", state)


def new_manifest(job_dir: Path, source: dict, permission: str) -> dict:
    manifest = {
        "created_at": now(),
        "source": {k: v for k, v in source.items() if k != "path"},
        "permission": permission,
        "clips": [],
    }
    write_json(job_dir / "manifest.json", manifest)
    return manifest


def _description(clip: dict, source: dict, permission: str) -> str:
    credit = f"Clip from \"{source.get('title')}\" by {source.get('creator')}"
    if source.get("url"):
        credit += f": {source['url']}"
    return f"{clip.get('description', '').strip()}\n\n{credit}\nUsed with permission ({permission})."


def render_clip(job_dir: Path, manifest: dict, source: dict, video_path: str, words: list,
                clip: dict, vertical: bool, selector: str) -> dict:
    kind = "short" if vertical else "long"
    index = sum(1 for c in manifest["clips"] if c["format"] == kind) + 1
    name = f"{kind}_{index:02d}"
    print(f"   {name}: {clip['start']:.1f}s - {clip['end']:.1f}s  {clip.get('title', '')[:60]}")
    path = render(video_path, job_dir, name, clip["start"], clip["end"], words, vertical,
                  clip.get("hook", ""), f"via {source.get('creator')}")
    entry = {
        "file": path.name,
        "format": kind,
        "start": clip["start"],
        "end": clip["end"],
        "duration": round(clip["end"] - clip["start"], 2),
        "title": re.sub(r"\s+", " ", clip.get("title", "")).strip()[:100],
        "title_variants": [re.sub(r"\s+", " ", str(t)).strip()[:100] for t in (clip.get("title_variants") or [])[:3]],
        "description": _description(clip, source, manifest["permission"]),
        "tags": clip.get("tags", []),
        "scores": clip.get("scores"),
        "reason": clip.get("reason"),
        "selector": selector,
        "status": "pending_review",
    }
    manifest["clips"].append(entry)
    write_json(job_dir / "manifest.json", manifest)
    return entry
