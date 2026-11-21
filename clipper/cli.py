"""Clip Studio CLI.

Usage:
  python -m clipper <url-or-file> --permission "how you got permission" [--shorts 5] [--long 1]
  python -m clipper <live-or-channel-url> --live --permission "..." [--live-shorts 1]
"""
import argparse
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

from clipper.clone import run_clone
from clipper.create import LOOKS, run_create
from clipper.download import ROOT, fetch
from clipper.jobs import JOB_ID, new_job_id, new_manifest, now, read_json, render_clip, update_job, write_json
from clipper.live import run_live
from clipper.moments import select_moments
from clipper.transcribe import transcribe

CLIPS_DIR = ROOT / "data" / "clips"


def _step(job_dir: Path, text: str) -> None:
    print(f"\n== {text}", flush=True)
    update_job(job_dir, stage=text)


def run_video(args, job_dir: Path) -> None:
    _step(job_dir, "Getting the video")
    local = Path(args.source)
    if local.is_file():
        saved_job = read_json(job_dir / "job.json", {})
        source = {"path": str(local.resolve()), "title": saved_job.get("title") or local.stem,
                  "creator": args.creator or saved_job.get("creator") or "unknown",
                  "url": None, "channel_url": None, "license": None, "id": None}
    else:
        source = fetch(args.source, job_dir)
        source["creator"] = args.creator or source.get("creator") or "unknown"
    print(f"   {source['title']} by {source['creator']}")
    update_job(job_dir, title=source["title"], creator=source["creator"])
    manifest = new_manifest(job_dir, source, args.permission)

    _step(job_dir, f"Transcribing on this computer (whisper '{args.model}')")
    transcript = transcribe(source["path"], args.model, args.language)
    write_json(job_dir / "transcript.json", transcript)
    print(f"   {len(transcript['words'])} words, language: {transcript['language']}")
    if not transcript["segments"]:
        raise RuntimeError("No speech found in the video, so there is nothing to clip.")

    _step(job_dir, "Picking moments")
    plan = select_moments(source["path"], transcript, args.shorts, args.long, args.niche,
                          source_title=source["title"], creator=source["creator"])
    print(f"   {len(plan['shorts'])} Shorts and {len(plan['long'])} long clips (picked by {plan['selector']})")

    _step(job_dir, "Rendering")
    for kind, vertical in (("shorts", True), ("long", False)):
        for clip in plan[kind]:
            render_clip(job_dir, manifest, source, source["path"], transcript["words"], clip, vertical, plan["selector"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a long video or live stream into review-ready clips.")
    parser.add_argument("source", help="Video URL, live stream / channel URL (with --live), or a local video file")
    parser.add_argument("--permission", default="",
                        help='Record of the creator\'s permission, e.g. "email from @creator 2026-11-19" or "CC BY licence"')
    parser.add_argument("--creator", help="Creator name, if it can't be read from the source")
    parser.add_argument("--shorts", type=int, default=5, help="Number of 9:16 Shorts (under 60 s)")
    parser.add_argument("--long", type=int, default=1, help="Number of 16:9 clips (7-10 min)")
    parser.add_argument("--niche", default="", help="Audience to optimise for, e.g. 'trading'")
    parser.add_argument("--model", default=None, help="Whisper model size or local folder (default: small, base for live)")
    parser.add_argument("--language", default=None, help="Spoken language code; auto-detected if omitted")
    parser.add_argument("--live", action="store_true", help="Record a live stream and clip while it plays")
    parser.add_argument("--live-shorts", type=int, default=1,
                        help="Shorts to cut per window during the stream; 0 = only clip after it ends")
    parser.add_argument("--window", type=float, default=300, help="Seconds of stream per live analysis window")
    parser.add_argument("--clone-topic", default="",
                        help="Viral Clone: analyse the video's structure and write variants about this topic")
    parser.add_argument("--variants", type=int, default=3, help="Number of script variants for --clone-topic")
    parser.add_argument("--create", choices=("script", "idea", "clone"),
                        help="Create an original video: source is a text file (script/idea) or a clone job id")
    parser.add_argument("--variant", type=int, default=1, help="Which clone variant to render (1-based)")
    parser.add_argument("--format", choices=("short", "long"), default="short", help="9:16 Short or 16:9 video")
    parser.add_argument("--look", choices=sorted(LOOKS), default="documentary", help="Visual look")
    parser.add_argument("--voice", default="en-US-AndrewNeural",
                        help="Edge voice (e.g. en-US-AriaNeural) or offline Windows voice name (David, Zira)")
    parser.add_argument("--out", default=str(CLIPS_DIR), help="Output folder")
    parser.add_argument("--job-id", help=argparse.SUPPRESS)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    args.permission = args.permission.strip()
    args.clone_topic = args.clone_topic.strip()
    # Clips reuse the creator's footage; clones and created videos reuse nothing.
    if not args.permission and not args.clone_topic and not args.create:
        parser.error("--permission must describe how you have the right to clip this video.")
    # The smaller model keeps transcription close to real time on a CPU.
    args.model = args.model or ("base" if args.live else "small")

    job_id = args.job_id or new_job_id()
    job_dir = Path(args.out) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    saved_job = read_json(job_dir / "job.json", {})
    if not saved_job:
        write_json(job_dir / "job.json", {"id": job_id, "created_at": now()})
    kind = "create" if args.create else "clone" if args.clone_topic else "live" if args.live else "video"
    update_job(job_dir, kind=kind, source_url=saved_job.get("source_url") or args.source, creator=args.creator,
               permission=args.permission, niche=args.niche, state="running", pid=os.getpid(),
               options={"shorts": args.shorts, "long": args.long, "live_shorts": args.live_shorts,
                        "model": args.model, "window": args.window})

    try:
        if args.create:
            clone_job, text = None, ""
            if args.create == "clone":
                clone_job = Path(args.out) / args.source
                if not JOB_ID.match(args.source) or not (clone_job / "blueprint.json").exists():
                    raise RuntimeError("Unknown clone job.")
            else:
                text = Path(args.source).read_text(encoding="utf-8-sig").strip()
            run_create(job_dir, args.create, text, args.format, args.look, args.voice, args.niche,
                       args.language, clone_job, args.variant - 1)
            update_job(job_dir, state="finished")
            print(f"\nDone. Video saved for review in:\n   {job_dir}")
            return
        if args.clone_topic:
            run_clone(args.source, job_dir, args.clone_topic, args.niche, max(1, min(args.variants, 10)),
                      args.model, args.language)
            update_job(job_dir, state="finished")
            print(f"\nDone. Blueprint and variants saved in:\n   {job_dir / 'blueprint.json'}")
            return
        if args.live:
            run_live(args.source, job_dir, args.permission, args.creator, args.live_shorts, args.shorts,
                     args.long, args.niche, args.model, args.language, args.window)
        else:
            run_video(args, job_dir)
    except Exception as error:
        traceback.print_exc()
        update_job(job_dir, state="failed", stage="Failed", error=str(error)[:500])
        sys.exit(1)

    clips = len(read_json(job_dir / "manifest.json", {}).get("clips", []))
    update_job(job_dir, state="finished", stage=f"Done: {clips} clips ready for review")
    print(f"\nDone. {clips} clips saved for review in:\n   {job_dir}")
    print("Nothing was uploaded. Review the clips before publishing.")
