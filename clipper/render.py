"""Render clips with FFmpeg: 9:16 Shorts (face-centred crop) and 16:9 long clips, with burned-in captions."""
import json
import subprocess
from pathlib import Path

import cv2

SHORT_SIZE = (1080, 1920)
LONG_SIZE = (1920, 1080)
HIGHLIGHT = "&H0000E5FF&"  # yellow, ASS uses BGR order


def _ass_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _header(width: int, height: int, caption_size: int, margin: int) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,{caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{max(caption_size // 12, 3)},2,2,60,60,{margin},1
Style: Hook,Arial,{int(caption_size * 1.05)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,1,0,0,0,100,100,0,0,3,18,0,8,60,60,{int(height * 0.08)},1
Style: Credit,Arial,{max(caption_size // 3, 22)},&HB4FFFFFF,&HB4FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,1,30,30,24,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _word_chunks(words: list, per_chunk: int, max_gap: float = 0.8) -> list:
    chunks, current = [], []
    for word in words:
        if current and (len(current) == per_chunk or word["start"] - current[-1]["end"] > max_gap):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def write_captions(path: Path, words: list, clip_start: float, clip_end: float, size: tuple,
                   hook: str, credit: str, karaoke: bool) -> None:
    width, height = size
    vertical = height > width
    caption_size = 78 if vertical else 54
    margin = int(height * (0.28 if vertical else 0.07))
    lines = [_header(width, height, caption_size, margin)]
    clip_words = [
        {**w, "start": w["start"] - clip_start, "end": w["end"] - clip_start}
        for w in words if w["start"] >= clip_start and w["end"] <= clip_end
    ]

    for chunk in _word_chunks(clip_words, 3 if vertical else 8):
        texts = [_escape(w["word"]).upper() if vertical else _escape(w["word"]) for w in chunk]
        if karaoke:
            for index, word in enumerate(chunk):
                end = chunk[index + 1]["start"] if index + 1 < len(chunk) else word["end"]
                styled = " ".join(
                    f"{{\\c{HIGHLIGHT}}}{t}{{\\c&H00FFFFFF&}}" if i == index else t
                    for i, t in enumerate(texts)
                )
                lines.append(f"Dialogue: 0,{_ass_time(word['start'])},{_ass_time(end)},Caption,,0,0,0,,{styled}")
        else:
            lines.append(
                f"Dialogue: 0,{_ass_time(chunk[0]['start'])},{_ass_time(chunk[-1]['end'])},Caption,,0,0,0,,{' '.join(texts)}"
            )

    duration = clip_end - clip_start
    if hook:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(3.5, duration))},Hook,,0,0,0,,{_escape(hook).upper()}")
    if credit:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},Credit,,0,0,0,,{_escape(credit)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _probe(video_path: str) -> tuple[float, bool]:
    """The file's first timestamp and whether it has an audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=start_time:stream=codec_type",
         "-of", "json", video_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    start = float(info.get("format", {}).get("start_time") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return start, has_audio


def face_center_x(video_path: str, start: float, end: float, samples: int = 12) -> float:
    """Median horizontal face position (0-1) across the clip; 0.5 when no face is found."""
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(video_path)
    centres = []
    try:
        for i in range(samples):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + (end - start) * (i + 0.5) / samples) * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(60, 60))
            if len(faces):
                x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
                centres.append((x + w / 2) / frame.shape[1])
    finally:
        capture.release()
    return float(sorted(centres)[len(centres) // 2]) if centres else 0.5


def render(video_path: str, out_dir: Path, name: str, start: float, end: float, words: list,
           vertical: bool, hook: str, credit: str) -> Path:
    size = SHORT_SIZE if vertical else LONG_SIZE
    captions = out_dir / f"{name}.ass"
    write_captions(captions, words, start, end, size, hook if vertical else "", credit, karaoke=vertical)

    if vertical:
        centre = face_center_x(video_path, start, end)
        crop = (
            "crop=w='min(iw,ih*9/16)':h=ih:"
            f"x='max(0,min(iw-min(iw,ih*9/16),iw*{centre:.4f}-min(iw,ih*9/16)/2))':y=0,"
            f"scale={size[0]}:{size[1]},setsar=1"
        )
    else:
        crop = (
            f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,"
            f"pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )

    output = out_dir / f"{name}.mp4"
    file_start, has_audio = _probe(video_path)
    # Transcript times are relative to the file's first timestamp; live MPEG-TS recordings don't start at 0.
    cut_start, cut_end = file_start + start, file_start + end
    pre_roll = min(start, 15.0)
    graph = (f"[0:v]trim=start={cut_start:.3f}:end={cut_end:.3f},setpts=PTS-STARTPTS,"
             f"{crop},ass={captions.name}[v]")
    maps = ["-map", "[v]"]
    if has_audio:
        graph += f";[0:a]atrim=start={cut_start:.3f}:end={cut_end:.3f},asetpts=PTS-STARTPTS[a]"
        maps += ["-map", "[a]"]
    # Fast-seek just before the clip, keep the original timestamps, then trim to the exact frame.
    # Run inside out_dir so the subtitle filter gets a relative path (Windows drive colons break it).
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{start - pre_roll:.2f}", "-copyts",
            "-i", str(Path(video_path).resolve()),
            "-filter_complex", graph, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            output.name,
        ],
        cwd=out_dir, check=True,
    )
    return output
