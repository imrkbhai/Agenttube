"""Clip Studio CLI.

Usage:
  python -m clipper <url-or-file> --permission "how you got permission" [--shorts 5] [--long 1]
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from clipper.download import ROOT, fetch
from clipper.moments import select_moments
from clipper.render import render
from clipper.transcribe import transcribe


def _step(text: str) -> None:
    print(f"\n== {text}")


def _description(clip: dict, source: dict, permission: str) -> str:
    credit = f"Clip from \"{source['title']}\" by {source['creator']}"
    if source.get("url"):
        credit += f": {source['url']}"
    return f"{clip.get('description', '').strip()}\n\n{credit}\nUsed with permission ({permission})."


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a long video into review-ready clips.")
    parser.add_argument("source", help="Video URL (YouTube, Twitch, etc.) or a local video file")
    parser.add_argument("--permission", required=True,
                        help='Record of the creator\'s permission, e.g. "email from @creator 2026-11-19" or "CC BY licence"')
    parser.add_argument("--creator", help="Creator name, if it can't be read from the source")
    parser.add_argument("--shorts", type=int, default=5, help="Number of 9:16 Shorts (under 60 s)")
    parser.add_argument("--long", type=int, default=1, help="Number of 16:9 clips (7-10 min)")
    parser.add_argument("--niche", default="", help="Audience to optimise for, e.g. 'trading'")
    parser.add_argument("--model", default="small", help="Whisper model size or local model folder")
    parser.add_argument("--language", default=None, help="Spoken language code; auto-detected if omitted")
    parser.add_argument("--out", default=str(ROOT / "data" / "clips"), help="Output folder")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not args.permission.strip():
        parser.error("--permission must describe how you have the right to clip this video.")

    job_dir = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    job_dir.mkdir(parents=True, exist_ok=True)

    _step("1/4 Getting the video")
    local = Path(args.source)
    if local.is_file():
        source = {"path": str(local.resolve()), "title": local.stem, "creator": args.creator or "unknown",
                  "url": None, "channel_url": None, "license": None, "id": None}
    else:
        source = fetch(args.source, job_dir)
        source["creator"] = args.creator or source.get("creator") or "unknown"
    print(f"   {source['title']} by {source['creator']}")

    _step(f"2/4 Transcribing on this computer (whisper '{args.model}')")
    transcript = transcribe(source["path"], args.model, args.language)
    (job_dir / "transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"   {len(transcript['words'])} words, language: {transcript['language']}")

    _step("3/4 Picking moments")
    plan = select_moments(source["path"], transcript, args.shorts, args.long, args.niche)
    print(f"   {len(plan['shorts'])} Shorts and {len(plan['long'])} long clips (picked by {plan['selector']})")

    _step("4/4 Rendering")
    credit = f"via {source['creator']}"
    clips = []
    for kind, vertical in (("shorts", True), ("long", False)):
        for index, clip in enumerate(plan[kind], start=1):
            name = f"{'short' if vertical else 'long'}_{index:02d}"
            print(f"   {name}: {clip['start']:.1f}s - {clip['end']:.1f}s  {clip.get('title', '')[:60]}")
            path = render(source["path"], job_dir, name, clip["start"], clip["end"],
                          transcript["words"], vertical, clip.get("hook", ""), credit)
            clips.append({
                "file": path.name,
                "format": "short" if vertical else "long",
                "start": clip["start"],
                "end": clip["end"],
                "duration": round(clip["end"] - clip["start"], 2),
                "title": re.sub(r"\s+", " ", clip.get("title", "")).strip()[:100],
                "description": _description(clip, source, args.permission.strip()),
                "tags": clip.get("tags", []),
                "scores": clip.get("scores"),
                "reason": clip.get("reason"),
                "status": "pending_review",
            })

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {k: v for k, v in source.items() if k != "path"},
        "permission": args.permission.strip(),
        "selector": plan["selector"],
        "clips": clips,
    }
    (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nDone. {len(clips)} clips saved for review in:\n   {job_dir}")
    print("Nothing was uploaded. Review the clips and manifest.json before publishing.")
