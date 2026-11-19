"""
YouTube Uploader
Authenticates with the YouTube Data API v3 and uploads the final video.
"""
import os
import pickle
import re
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
import config


TOKEN_FILE = "youtube_token.pickle"
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
MAX_TAGS_LENGTH = 450


def _clean_text(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f<>]", " ", str(value or ""))).strip()


def _normalize_tags(tags) -> list:
    values = tags if isinstance(tags, list) else str(tags or "").split(",")
    unique, seen, total = [], set(), 0
    for value in values:
        tag = _clean_text(value).lstrip("#").replace('"', "")[:100].strip()
        if not tag or tag.lower() in seen:
            continue
        total += len(tag) + (1 if unique else 0)
        if total > MAX_TAGS_LENGTH or len(unique) >= 30:
            break
        unique.append(tag)
        seen.add(tag.lower())
    return unique


def _get_service():
    """Return an authenticated YouTube service object."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
        except RefreshError:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(config.YOUTUBE_CLIENT_SECRET):
            raise FileNotFoundError(
                f"YouTube OAuth file not found: {config.YOUTUBE_CLIENT_SECRET}\n"
                "Follow the setup guide to create it in Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            config.YOUTUBE_CLIENT_SECRET, config.YOUTUBE_SCOPES
        )
        creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def ensure_authenticated() -> None:
    """Preflight: authorize YouTube before spending time on rendering."""
    _get_service()


def upload_to_youtube(script: dict, video_path: str, thumbnail_path: str | None = None) -> str:
    """
    Uploads the video with auto-generated title, description, and tags.
    Returns the public YouTube URL.
    """
    youtube = _get_service()
    tags = _normalize_tags(script.get("tags", []))
    title = _clean_text(script.get("title", ""))[:MAX_TITLE_LENGTH]
    if not title:
        raise ValueError("YouTube metadata is invalid: a video title is required.")

    description = (
        _clean_text(script.get("description", "")) + "\n\n"
        + "Educational content only, not financial advice. Trading involves risk.\n"
        + "─────────────────────────────\n"
        + f"Subscribe to {config.CHANNEL_NAME} for new explainers every week!\n"
        + "─────────────────────────────\n"
        + " ".join(f"#{t.replace(' ', '')}" for t in tags[:3])
    )[:MAX_DESCRIPTION_LENGTH]

    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  config.VIDEO_CATEGORY_ID,
        },
        "status": {
            "privacyStatus":            config.VIDEO_PRIVACY,
            "selfDeclaredMadeForKids":  False,
            "containsSyntheticMedia":   config.CONTAINS_SYNTHETIC_MEDIA,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=5 * 1024 * 1024,   # 5 MB chunks
    )

    insert_req = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    print("   → Uploading…")
    response = None
    while response is None:
        status, response = insert_req.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"   → {pct}% uploaded", end="\r")

    print()
    video_id = response["id"]
    if thumbnail_path and os.path.exists(thumbnail_path):
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg"),
        ).execute()
        print("   → Thumbnail uploaded")
    return f"https://www.youtube.com/watch?v={video_id}"
