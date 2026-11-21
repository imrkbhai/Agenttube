"""Persist and publish scheduled social posts inside the Agenttube dashboard process."""
import threading
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from clipper import publishing
from clipper.cli import CLIPS_DIR
from clipper.jobs import now, read_json, write_json

SCHEDULE_FILE = CLIPS_DIR / "scheduled-posts.json"
_lock = threading.Lock()
_stop = threading.Event()
_worker: threading.Thread | None = None


def _posts() -> list[dict]:
    value = read_json(SCHEDULE_FILE, [])
    return value if isinstance(value, list) else []


def list_posts() -> list[dict]:
    with _lock:
        return sorted(_posts(), key=lambda post: post.get("scheduled_at", ""), reverse=True)


def schedule_post(job_id: str, job_title: str, filename: str, platforms: list[str], title: str,
                  caption: str, privacy: str, scheduled_at: datetime) -> dict:
    post = {
        "id": token_hex(8), "job_id": job_id, "job_title": job_title,
        "file": filename, "platforms": platforms, "title": title, "caption": caption,
        "privacy": privacy,
        "scheduled_at": scheduled_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "status": "scheduled", "created_at": now(),
    }
    with _lock:
        posts = _posts()
        posts.append(post)
        write_json(SCHEDULE_FILE, posts)
    return post


def cancel_post(post_id: str) -> dict:
    with _lock:
        posts = _posts()
        post = next((item for item in posts if item.get("id") == post_id), None)
        if post is None:
            raise ValueError("Scheduled post not found")
        if post.get("status") != "scheduled":
            raise ValueError("Only queued posts can be cancelled")
        post["status"] = "cancelled"
        post["completed_at"] = now()
        write_json(SCHEDULE_FILE, posts)
        return post


def run_due(at: datetime | None = None) -> list[dict]:
    current = at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    with _lock:
        posts = _posts()
        due = [post for post in posts if post.get("status") == "scheduled"
               and datetime.fromisoformat(post["scheduled_at"]) <= current]
        for post in due:
            post["status"] = "publishing"
        if due:
            write_json(SCHEDULE_FILE, posts)

    completed = []
    for post in due:
        job_dir = (CLIPS_DIR / post["job_id"]).resolve()
        video = (job_dir / post["file"]).resolve()
        results = []
        if video.parent != job_dir or not video.is_file():
            results = [{"platform": platform, "ok": False, "error": "Scheduled video file is missing"}
                       for platform in post["platforms"]]
        else:
            for platform in post["platforms"]:
                try:
                    result = publishing.publish_video(
                        platform, video, post["title"], post["caption"], post["privacy"]
                    )
                    results.append({"platform": platform, "ok": True, **result})
                except Exception as error:
                    results.append({"platform": platform, "ok": False, "error": str(error)[:500]})

        successful = [result for result in results if result["ok"]]
        post["results"] = results
        post["status"] = "published" if len(successful) == len(results) else "partial" if successful else "failed"
        post["completed_at"] = now()
        with _lock:
            posts = _posts()
            for index, saved in enumerate(posts):
                if saved.get("id") == post["id"]:
                    posts[index] = post
                    break
            write_json(SCHEDULE_FILE, posts)
            if successful:
                history_path = job_dir / "publications.json"
                history = read_json(history_path, {}) or {}
                entries = history.setdefault(post["file"], [])
                entries.extend({"platform": result["platform"], "url": result["url"],
                                "title": post["title"], "created_at": post["completed_at"]}
                               for result in successful)
                write_json(history_path, history)
        completed.append(post)
    return completed


def recover_interrupted() -> None:
    with _lock:
        posts = _posts()
        changed = False
        for post in posts:
            if post.get("status") == "publishing":
                post["status"] = "failed"
                post["error"] = "Dashboard stopped during publishing; verify platform status before retrying."
                post["completed_at"] = now()
                changed = True
        if changed:
            write_json(SCHEDULE_FILE, posts)


def _run() -> None:
    while not _stop.is_set():
        try:
            run_due()
        except Exception as error:
            print(f"Scheduled publishing check failed: {error}")
        _stop.wait(5)


def start_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    recover_interrupted()
    _stop.clear()
    _worker = threading.Thread(target=_run, name="agenttube-post-scheduler", daemon=True)
    _worker.start()


def stop_worker() -> None:
    _stop.set()
    if _worker is not None:
        _worker.join(timeout=1)