"""Render non-destructive edits of finished clips using the local FFmpeg install."""
import json
import math
import re
import subprocess
import threading
from pathlib import Path

from clipper.jobs import read_json, write_json

SOURCE_FILE = re.compile(r"^(short|long)_\d{2,3}\.mp4$")
EDIT_FILE = re.compile(r"^(short|long)_\d{2,3}_edit_\d{2}\.mp4$")
REGION_FIELDS = ("x", "y", "width", "height")
_edit_lock = threading.Lock()


class EditingError(ValueError):
    pass


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditingError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise EditingError(f"{name} must be finite")
    return number


def _region(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise EditingError(f"Draw a {name} region on the source frame")
    region = {field: _number(value.get(field), f"{name} {field}") for field in REGION_FIELDS}
    if (region["x"] < 0 or region["y"] < 0 or region["width"] < 0.02 or region["height"] < 0.02
            or region["x"] + region["width"] > 1.000001
            or region["y"] + region["height"] > 1.000001):
        raise EditingError(f"The {name} region must stay inside the source frame")
    return region


def _region_branch(input_label: str, output_label: str, region: dict, height: int, speed: float) -> str:
    x, y = region["x"], region["y"]
    width, region_height = region["width"], region["height"]
    return (
        f"[{input_label}]crop=w='trunc(iw*{width:.6f}/2)*2':h='trunc(ih*{region_height:.6f}/2)*2':"
        f"x='min(trunc(iw*{x:.6f}/2)*2,iw-ow)':y='min(trunc(ih*{y:.6f}/2)*2,ih-oh)',"
        f"scale=1080:{height}:force_original_aspect_ratio=increase,crop=1080:{height},setsar=1,"
        f"setpts=(PTS-STARTPTS)/{speed:.3f}[{output_label}]"
    )


def _layout_filter(first: dict, second: dict, first_height: int, second_height: int, speed: float) -> str:
    branches = [
        "[0:v]split=2[v0][v1]",
        _region_branch("v0", "panel0", first, first_height, speed),
        _region_branch("v1", "panel1", second, second_height, speed),
        "[panel0][panel1]vstack=inputs=2,setsar=1[v]",
    ]
    return ";".join(branches)


def create_edit(job_dir: Path, body: dict) -> dict:
    job = read_json(job_dir / "job.json", {}) or {}
    filename = body.get("file")
    manifest_path = job_dir / "manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    source_edit = filename == "__source__"
    if source_edit:
        if not job.get("source_ready") and not manifest_path.is_file():
            raise EditingError("The source download is still in progress")
        candidates = ([job_dir / "recording.ts"] if job.get("kind") == "live"
                      else sorted(job_dir.glob("source.*")))
        input_path = next((path.resolve() for path in candidates if path.is_file()), None)
        if input_path is None or input_path.parent != job_dir.resolve():
            raise EditingError("The downloaded source video is not available yet")
        source = manifest.get("source") or {}
        original = {
            "file": input_path.name,
            "format": "",
            "start": 0,
            "end": 0,
            "title": source.get("title") or job.get("title") or input_path.stem,
            "description": "",
        }
        source_start = 0
    else:
        if job.get("state") != "finished":
            raise EditingError("Wait for clipping to finish before editing a rendered clip")
        if not isinstance(filename, str) or not SOURCE_FILE.fullmatch(filename):
            raise EditingError("Choose an original clip to edit")
        original = next((clip for clip in manifest.get("clips", []) if clip.get("file") == filename), None)
        if original is None:
            raise EditingError("The selected clip is not in this job")
        source_start = float(original.get("start", 0))

    layout = body.get("layout", "standard")
    if not isinstance(layout, str) or layout not in {"standard", "gaming", "podcast"}:
        raise EditingError("Unsupported layout")

    if source_edit:
        pass
    elif layout == "standard":
        input_path = (job_dir / filename).resolve()
        if input_path.parent != job_dir.resolve() or not input_path.is_file():
            raise EditingError("The selected clip is missing")
    else:
        job_kind = job.get("kind")
        candidates = ([job_dir / "recording.ts"] if job_kind == "live"
                      else sorted(job_dir.glob("source.*")))
        input_path = next((path.resolve() for path in candidates if path.is_file()), None)
        if input_path is None or input_path.parent != job_dir.resolve():
            raise EditingError("The original source video is unavailable for this layout")

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(input_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise EditingError("Could not read the selected clip with ffprobe") from error
    if duration <= 0:
        raise EditingError("The selected clip has no usable duration")

    clip_duration = duration if layout == "standard" or source_edit else _number(
        original.get("duration", float(original.get("end", 0)) - source_start), "Clip duration",
    )
    start = _number(body.get("start", 0), "Start")
    end = _number(body.get("end", clip_duration), "End")
    if start < 0 or end > clip_duration or end - start < 0.5:
        raise EditingError("Choose a trim range of at least 0.5 seconds inside the clip")

    aspect = body.get("aspect", "original")
    if not isinstance(aspect, str) or aspect not in {"original", "portrait", "square", "landscape"}:
        raise EditingError("Unsupported output format")
    crop = body.get("crop", "center")
    if not isinstance(crop, str) or crop not in {"left", "center", "right"}:
        raise EditingError("Unsupported crop position")
    volume = _number(body.get("volume", 1), "Volume")
    speed = _number(body.get("speed", 1), "Speed")
    if not 0 <= volume <= 2 or not 0.5 <= speed <= 2:
        raise EditingError("Volume must be 0–2 and speed must be 0.5–2")

    layout_regions = None
    top = body.get("top", "first")
    camera_share = 38
    if layout == "gaming":
        layout_regions = {
            "webcam": _region(body.get("webcam_box"), "webcam"),
            "game": _region(body.get("game_box"), "game"),
        }
        if top not in {"webcam", "game"}:
            raise EditingError("Choose whether the webcam or game appears on top")
        camera_share = _number(body.get("camera_share", 38), "Camera share")
        if not 25 <= camera_share <= 50:
            raise EditingError("Camera share must be between 25 and 50 percent")
    elif layout == "podcast":
        layout_regions = {
            "speaker_a": _region(body.get("speaker_a_box"), "Speaker A"),
            "speaker_b": _region(body.get("speaker_b_box"), "Speaker B"),
        }
        if top not in {"speaker_a", "speaker_b"}:
            raise EditingError("Choose whether Speaker A or Speaker B appears on top")

    filters = {"original": None, "portrait": (1080, 1920), "square": (1080, 1080),
               "landscape": (1920, 1080)}
    filter_graph = None
    video_filter = "setsar=1"
    if layout != "standard":
        aspect = "portrait"
        first_name, second_name = tuple(layout_regions)
        first_height, second_height = (960, 960)
        if layout == "gaming":
            camera_height = int(round(1920 * camera_share / 100 / 2) * 2)
            game_height = 1920 - camera_height
            first_height, second_height = ((camera_height, game_height) if top == "webcam"
                                           else (game_height, camera_height))
        if top == second_name:
            first_name, second_name = second_name, first_name
        filter_graph = _layout_filter(layout_regions[first_name], layout_regions[second_name],
                                      first_height, second_height, speed)
    elif filters[aspect]:
        width, height = filters[aspect]
        x_position = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[crop]
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{x_position}:(ih-oh)/2,setsar=1"
        )
    video_filter += f",setpts=(PTS-STARTPTS)/{speed:.3f}"
    audio_filter = f"asetpts=PTS-STARTPTS,volume={volume:.3f},atempo={speed:.3f}"
    edited_duration = round((end - start) / speed, 2)
    output_format = "long" if (
        aspect == "landscape" or edited_duration > 60
        or (aspect == "original" and original.get("format") == "long")
    ) else "short"

    with _edit_lock:
        if source_edit:
            output_index = 1
            while output_index < 1000:
                output_name = f"{output_format}_{output_index:02d}_edit_01.mp4"
                if not (job_dir / output_name).exists():
                    break
                output_index += 1
            else:
                raise EditingError("This source already has too many edited clips")
        else:
            edit_index = next((index for index in range(1, 100)
                               if not (job_dir / f"{Path(filename).stem}_edit_{index:02d}.mp4").exists()), None)
            if edit_index is None:
                raise EditingError("This clip already has 99 edited versions")
            output_name = f"{Path(filename).stem}_edit_{edit_index:02d}.mp4"
        output_path = job_dir / output_name
        command = [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{(source_start + start) if layout != 'standard' else start:.3f}",
            "-i", str(input_path), "-t", f"{(end - start) / speed:.3f}",
        ]
        if filter_graph:
            command += ["-filter_complex", filter_graph, "-map", "[v]"]
        else:
            command += ["-vf", video_filter, "-map", "0:v:0"]
        command += [
            "-af", audio_filter, "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(output_path),
        ]
        try:
            subprocess.run(command, cwd=job_dir, capture_output=True, check=True)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise EditingError("FFmpeg did not create an output clip")
        except (OSError, subprocess.SubprocessError, EditingError) as error:
            output_path.unlink(missing_ok=True)
            if isinstance(error, EditingError):
                raise
            raise EditingError("FFmpeg could not render this edit") from error

        edited = {
            **original,
            "file": output_name,
            "format": output_format,
            "start": round(source_start + start, 2),
            "end": round(source_start + end, 2),
            "duration": edited_duration,
            "title": f"{original.get('title') or Path(filename).stem} (edited)"[:100],
            "status": "pending_review",
            "edit_of": "source" if source_edit else filename,
            **({"source_edit": True, "source_file": input_path.name} if source_edit else {}),
            "edit_options": {"start": start, "end": end, "aspect": aspect,
                             "crop": crop, "volume": volume, "speed": speed,
                             "layout": layout, **({"regions": layout_regions, "top": top,
                                                   "camera_share": camera_share} if layout == "gaming"
                                                  else {"regions": layout_regions, "top": top}
                                                  if layout == "podcast" else {})},
        }
        if source_edit:
            editor_path = job_dir / "editor_clips.json"
            editor_clips = read_json(editor_path, []) or []
            editor_clips.append(edited)
            write_json(editor_path, editor_clips)
        else:
            manifest.setdefault("clips", []).append(edited)
            write_json(manifest_path, manifest)
    return edited