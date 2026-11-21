"""Publish approved Clip Studio videos through official platform APIs."""
import os
import re
import time
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests

from clipper.cli import CLIPS_DIR
from clipper.download import ROOT

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_TOKEN = CLIPS_DIR / ".publishing" / "youtube-token.json"
REQUEST_TIMEOUT = (20, 180)
POLL_TIMEOUT = 300
X_CHUNK_SIZE = 4 * 1024 * 1024
TRYPOST_TIMEOUT = (20, 180)


def _youtube_secret() -> Path:
    configured = Path(os.getenv("CLIPPER_YOUTUBE_CLIENT_SECRET", str(ROOT / "studio" / "client_secret.local.json")))
    return configured if configured.is_absolute() else ROOT / configured


def _meta_version() -> str:
    version = os.getenv("CLIPPER_META_GRAPH_VERSION", "v27.0")
    return version if re.fullmatch(r"v\d+\.\d+", version) else "v27.0"


def publishing_status() -> dict:
    meta_token = bool(os.getenv("CLIPPER_META_ACCESS_TOKEN"))
    youtube_secret = _youtube_secret()
    return {
        "youtube": {
            "configured": youtube_secret.is_file(),
            "message": "OAuth ready; first publish opens Google sign-in" if youtube_secret.is_file()
            else "Set CLIPPER_YOUTUBE_CLIENT_SECRET to a Google Desktop OAuth JSON file",
        },
        "instagram": {
            "configured": meta_token and bool(os.getenv("CLIPPER_INSTAGRAM_USER_ID")),
            "message": "Ready" if meta_token and os.getenv("CLIPPER_INSTAGRAM_USER_ID")
            else "Needs a Meta access token and Instagram professional account ID",
        },
        "facebook": {
            "configured": meta_token and bool(os.getenv("CLIPPER_FACEBOOK_PAGE_ID")),
            "message": "Ready" if meta_token and os.getenv("CLIPPER_FACEBOOK_PAGE_ID")
            else "Needs a Meta Page access token and Facebook Page ID",
        },
        "x": {
            "configured": bool(os.getenv("CLIPPER_X_USER_ACCESS_TOKEN")),
            "message": "Ready" if os.getenv("CLIPPER_X_USER_ACCESS_TOKEN")
            else "Needs an X user access token with media.write and tweet.write",
        },
    }


def _trypost_settings() -> tuple[str, str, str]:
    base_url = os.getenv("CLIPPER_TRYPOST_URL", "").strip().rstrip("/")
    api_url = os.getenv("CLIPPER_TRYPOST_API_URL", "").strip().rstrip("/")
    api_key = os.getenv("CLIPPER_TRYPOST_API_KEY", "").strip()
    parsed = urlparse(base_url)
    if not base_url or not api_key:
        raise RuntimeError("Set CLIPPER_TRYPOST_URL and CLIPPER_TRYPOST_API_KEY in .env")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeError("CLIPPER_TRYPOST_URL must be an http(s) URL without credentials")
    if not api_url:
        api_url = f"{base_url}/api"
    parsed_api = urlparse(api_url)
    if (parsed_api.scheme not in ("http", "https") or not parsed_api.netloc
            or parsed_api.username or parsed_api.password or parsed_api.query or parsed_api.fragment):
        raise RuntimeError("CLIPPER_TRYPOST_API_URL must be an http(s) API URL without credentials or query parameters")
    return base_url, api_key, api_url


def _trypost_response(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.ok:
        message = data.get("message") if isinstance(data, dict) else None
        errors = data.get("errors") if isinstance(data, dict) else None
        if not message and isinstance(errors, dict):
            message = next((str(value[0]) for value in errors.values()
                            if isinstance(value, list) and value), None)
        raise RuntimeError(f"TryPost HTTP {response.status_code}: {message or response.text[:400] or 'Request failed'}")
    if not isinstance(data, dict):
        raise RuntimeError("TryPost returned an unexpected response")
    return data


def trypost_accounts() -> list[dict]:
    _, api_key, api_url = _trypost_settings()
    headers = {"Authorization": f"Bearer {api_key}"}
    accounts = []
    page = 1
    while page <= 50:
        response = requests.get(f"{api_url}/social-accounts", headers=headers,
                                params={"page": page}, timeout=TRYPOST_TIMEOUT)
        data = _trypost_response(response)
        page_data = data.get("data", [])
        if not isinstance(page_data, list):
            raise RuntimeError("TryPost returned an unexpected social accounts response")
        accounts.extend(account for account in page_data
                        if isinstance(account, dict) and account.get("id"))
        meta = data.get("meta")
        last_page = meta.get("last_page", page) if isinstance(meta, dict) else page
        if page >= int(last_page):
            break
        page += 1
    return accounts


def trypost_status() -> dict:
    base_url = os.getenv("CLIPPER_TRYPOST_URL", "").strip().rstrip("/")
    if not os.getenv("CLIPPER_TRYPOST_API_KEY", "").strip() or not base_url:
        return {"configured": False, "connected": False, "url": base_url,
                "message": "Set CLIPPER_TRYPOST_URL and CLIPPER_TRYPOST_API_KEY in .env", "accounts": []}
    try:
        supported = {"youtube", "instagram", "facebook", "x"}
        accounts = [account for account in trypost_accounts()
                    if str(account.get("platform", "")).lower() in supported]
        return {"configured": True, "connected": True, "url": base_url,
                "message": f"{len(accounts)} supported account(s) connected" if accounts
                else "TryPost is connected; connect YouTube, Instagram, Facebook, or X accounts in TryPost",
                "accounts": accounts}
    except Exception as error:
        return {"configured": True, "connected": False, "url": base_url,
                "message": str(error)[:400], "accounts": []}


def handoff_to_trypost(video: Path, title: str, caption: str, social_account_ids: list[str],
                       status: str = "draft", scheduled_at: str | None = None,
                       privacy: str = "public", media_format: str = "short",
                       scheduled_times: dict[str, str] | None = None,
                       content_types: dict[str, str] | None = None) -> dict:
    base_url, api_key, api_url = _trypost_settings()
    if status not in {"draft", "scheduled", "publishing"}:
        raise RuntimeError("TryPost status must be draft, scheduled, or publishing")
    if privacy not in {"private", "unlisted", "public"}:
        raise RuntimeError("YouTube privacy must be private, unlisted, or public")

    default_content_types = {
        "youtube": "youtube_short",
        "instagram": "instagram_reel",
        "facebook": "facebook_reel",
        "x": "x_post",
    }
    platform_content_types = {
        "youtube": {"youtube_short"},
        "instagram": {"instagram_reel", "instagram_feed"},
        "facebook": {"facebook_reel", "facebook_post"},
        "x": {"x_post"},
    }
    accounts = {str(item["id"]): item for item in trypost_accounts()
                if str(item.get("platform", "")).lower() in default_content_types}
    selected = list(dict.fromkeys(str(item) for item in social_account_ids if str(item) in accounts))
    if not selected:
        raise RuntimeError("Select at least one connected TryPost account")
    if len(selected) != len(set(str(item) for item in social_account_ids)):
        raise RuntimeError("One or more selected TryPost accounts are no longer connected")
    if media_format == "long" and any(accounts[item].get("platform") == "youtube" for item in selected):
        raise RuntimeError("TryPost currently supports YouTube Shorts only; remove the YouTube account or use direct YouTube publishing")
    selected_content_types = {}
    for account_id in selected:
        platform = str(accounts[account_id].get("platform", "")).lower()
        content_type = (content_types or {}).get(account_id, default_content_types[platform])
        if content_type not in platform_content_types[platform]:
            raise RuntimeError(f"Unsupported TryPost video format for {platform}")
        selected_content_types[account_id] = content_type
    account_schedules = {}
    if status == "scheduled":
        account_schedules = {
            account_id: (scheduled_times or {}).get(account_id) or scheduled_at
            for account_id in selected
        }
        if any(not value for value in account_schedules.values()):
            raise RuntimeError("Choose a publication time for every selected account")
    if not video.is_file():
        raise RuntimeError("The selected video file is missing")

    try:
        from requests_toolbelt.multipart.encoder import MultipartEncoder
    except ImportError as error:
        raise RuntimeError("Install Clip Studio requirements to upload videos to TryPost") from error

    headers = {"Authorization": f"Bearer {api_key}"}
    media_type = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    with video.open("rb") as media:
        upload = MultipartEncoder(fields={"media": (video.name, media, media_type)})
        response = requests.post(f"{api_url}/uploads", data=upload,
                                 headers={**headers, "Content-Type": upload.content_type}, timeout=TRYPOST_TIMEOUT)
        uploaded = _trypost_response(response)
    upload_data = uploaded.get("data", uploaded)
    upload_token = upload_data.get("upload_token") if isinstance(upload_data, dict) else None
    if not upload_token:
        raise RuntimeError("TryPost did not return a usable upload token")

    grouped_accounts: dict[str | None, list[str]] = {}
    for account_id in selected:
        group_key = account_schedules.get(account_id) if status == "scheduled" else None
        grouped_accounts.setdefault(group_key, []).append(account_id)

    destinations = []
    failures = []
    media_id = None
    groups = list(grouped_accounts.items())
    for group_index, (group_schedule, account_group) in enumerate(groups):
        body = {
            "status": status,
            "content": caption,
            "media": [{"id": media_id}] if media_id else [{"upload_token": upload_token}],
            "destinations": [],
        }
        for account_id in account_group:
            platform = str(accounts[account_id].get("platform", "")).lower()
            content_type = selected_content_types[account_id]
            destination = {
                "social_account_id": account_id,
                "content_type": content_type,
                "content": caption if platform == "youtube" else "\n\n".join(
                    part for part in (title, caption) if part
                ),
            }
            if platform == "youtube":
                destination["meta"] = {"title": title, "description": caption, "privacy_status": privacy}
            body["destinations"].append(destination)
        if status == "scheduled":
            body["scheduled_at"] = group_schedule
        try:
            response = requests.post(f"{api_url}/posts/batch", json=body,
                                     headers=headers, timeout=TRYPOST_TIMEOUT)
            created = _trypost_response(response)
            post_data = created.get("posts", created.get("data", []))
            if isinstance(post_data, dict):
                post_data = post_data.get("posts", [post_data])
            if not isinstance(post_data, list) or len(post_data) != len(account_group):
                raise RuntimeError("TryPost returned an unexpected batch response")
        except Exception as error:
            message = str(error)[:400]
            failures.extend({"account_id": account_id,
                             "platform": str(accounts[account_id].get("platform", "")).lower(),
                             "error": message}
                            for account_id in account_group)
            error_match = re.search(r"TryPost HTTP (\d{3})", message)
            if not error_match or int(error_match.group(1)) >= 500:
                for _, remaining in groups[group_index + 1:]:
                    failures.extend({"account_id": account_id,
                                     "platform": str(accounts[account_id].get("platform", "")).lower(),
                                     "error": "Stopped after an uncertain TryPost response; review TryPost before retrying."}
                                    for account_id in remaining)
                break
            continue

        for index, post in enumerate(post_data):
            account_id = account_group[index]
            platform = str(accounts[account_id].get("platform", "")).lower()
            destinations.append({
                "id": str(post["id"]),
                "account_id": account_id,
                "platform": platform,
                "content_type": body["destinations"][index]["content_type"],
                "scheduled_at": group_schedule,
            })
        if media_id is None and group_index < len(groups) - 1:
            first_post_media = post_data[0].get("media", [])
            if first_post_media and isinstance(first_post_media[0], dict):
                media_id = first_post_media[0].get("id")
            if not media_id:
                for _, remaining in groups[group_index + 1:]:
                    failures.extend({"account_id": account_id,
                                     "platform": str(accounts[account_id].get("platform", "")).lower(),
                                     "error": "TryPost did not return a reusable media ID; this destination was not submitted."}
                                    for account_id in remaining)
                break

    post_ids = [post["id"] for post in destinations]
    if not post_ids:
        details = "; ".join(dict.fromkeys(item["error"] for item in failures))
        raise RuntimeError(f"TryPost could not create any posts: {details or 'No post records returned'}")
    return {"url": base_url, "ids": post_ids, "destinations": destinations,
            "failures": failures, "status": status, "accounts": selected}


def _json_response(response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not response.ok:
        error = data.get("error") or data
        message = error.get("message") or error.get("detail") or response.text[:500] or "Request failed"
        raise RuntimeError(f"HTTP {response.status_code}: {message}")
    return data


def _meta_url(path: str) -> str:
    return f"https://graph.facebook.com/{_meta_version()}/{path.lstrip('/')}"


def _meta_request(method: str, path: str, token: str, **kwargs) -> dict:
    headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
    response = requests.request(method, _meta_url(path), headers=headers,
                                timeout=REQUEST_TIMEOUT, **kwargs)
    return _json_response(response)


def _wait_for_instagram(container_id: str, token: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        data = _meta_request("GET", container_id, token, params={"fields": "status_code"})
        state = data.get("status_code")
        if state == "FINISHED":
            return
        if state in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram could not process this Reel ({state.lower()})")
        time.sleep(5)
    raise RuntimeError("Instagram Reel processing timed out; check the account and try again")


def _publish_instagram(video: Path, title: str, caption: str) -> dict:
    token = os.environ["CLIPPER_META_ACCESS_TOKEN"]
    account_id = os.environ["CLIPPER_INSTAGRAM_USER_ID"]
    text = "\n\n".join(part for part in (title, caption) if part).strip()
    container = _meta_request("POST", f"{account_id}/media", token, data={
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": text,
    })
    container_id = container.get("id")
    if not container_id:
        raise RuntimeError("Instagram did not return a Reel upload ID")

    upload_url = f"https://rupload.facebook.com/ig-api-upload/{_meta_version()}/{container_id}"
    with video.open("rb") as media:
        response = requests.post(upload_url, headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(video.stat().st_size),
            "Content-Type": "application/octet-stream",
        }, data=media, timeout=REQUEST_TIMEOUT)
    _json_response(response)
    _wait_for_instagram(container_id, token)
    result = _meta_request("POST", f"{account_id}/media_publish", token,
                           data={"creation_id": container_id})
    media_id = result.get("id")
    if not media_id:
        raise RuntimeError("Instagram did not return a published Reel ID")
    try:
        permalink = _meta_request("GET", media_id, token, params={"fields": "permalink"}).get("permalink")
    except RuntimeError:
        permalink = None
    return {"url": permalink or f"https://www.instagram.com/reels/{media_id}/", "id": media_id}


def _publish_facebook(video: Path, title: str, caption: str) -> dict:
    token = os.environ["CLIPPER_META_ACCESS_TOKEN"]
    page_id = os.environ["CLIPPER_FACEBOOK_PAGE_ID"]
    session = _meta_request("POST", f"{page_id}/video_reels", token,
                            data={"upload_phase": "start"})
    video_id, upload_url = session.get("video_id"), session.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError("Facebook did not return a Reel upload session")
    upload_target = urlparse(upload_url)
    if upload_target.scheme != "https" or upload_target.hostname != "rupload.facebook.com":
        raise RuntimeError("Facebook returned an unexpected upload host")

    with video.open("rb") as media:
        response = requests.post(upload_url, headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(video.stat().st_size),
            "Content-Type": "application/octet-stream",
        }, data=media, timeout=REQUEST_TIMEOUT)
    _json_response(response)

    fields = {"video_id": video_id, "upload_phase": "finish", "video_state": "PUBLISHED"}
    if title:
        fields["title"] = title
    if caption:
        fields["description"] = caption
    _meta_request("POST", f"{page_id}/video_reels", token, data=fields)
    return {"url": f"https://www.facebook.com/reel/{video_id}", "id": video_id}


def _publish_x(video: Path, title: str, caption: str) -> dict:
    token = os.environ["CLIPPER_X_USER_ACCESS_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    size = video.stat().st_size
    initialized = _json_response(requests.post(
        "https://api.x.com/2/media/upload/initialize", headers=headers, json={
            "media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video",
        }, timeout=REQUEST_TIMEOUT,
    ))
    media_id = str((initialized.get("data") or {}).get("id") or "")
    if not media_id:
        raise RuntimeError("X did not return a video upload ID")

    with video.open("rb") as media:
        segment_index = 0
        while chunk := media.read(X_CHUNK_SIZE):
            _json_response(requests.post(
                f"https://api.x.com/2/media/upload/{media_id}/append",
                headers=headers, data={"segment_index": str(segment_index)},
                files={"media": (video.name, chunk, "application/octet-stream")},
                timeout=REQUEST_TIMEOUT,
            ))
            segment_index += 1

    finalized = _json_response(requests.post(
        f"https://api.x.com/2/media/upload/{media_id}/finalize",
        headers=headers, timeout=REQUEST_TIMEOUT,
    ))
    info = (finalized.get("data") or {}).get("processing_info") or {}
    deadline = time.monotonic() + POLL_TIMEOUT
    while info and info.get("state") not in ("succeeded", "failed"):
        if time.monotonic() >= deadline:
            raise RuntimeError("X video processing timed out; try again")
        time.sleep(max(1, min(int(info.get("check_after_secs", 2)), 10)))
        status = _json_response(requests.get(
            "https://api.x.com/2/media/upload", headers=headers,
            params={"command": "STATUS", "media_id": media_id}, timeout=REQUEST_TIMEOUT,
        ))
        info = (status.get("data") or {}).get("processing_info") or {}
    if info.get("state") == "failed":
        raise RuntimeError("X could not process this video")

    text = "\n\n".join(part for part in (title, caption) if part).strip()
    posted = _json_response(requests.post(
        "https://api.x.com/2/tweets", headers={**headers, "Content-Type": "application/json"},
        json={"text": text, "media": {"media_ids": [media_id]}}, timeout=REQUEST_TIMEOUT,
    ))
    post_id = str((posted.get("data") or {}).get("id") or "")
    if not post_id:
        raise RuntimeError("X did not return a published post ID")
    return {"url": f"https://x.com/i/status/{post_id}", "id": post_id}


def _youtube_upload_status(privacy: str, scheduled_at: str | None = None) -> dict:
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if scheduled_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = scheduled_at
    return status


def _publish_youtube(video: Path, title: str, caption: str, privacy: str,
                     scheduled_at: str | None = None) -> dict:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as error:
        raise RuntimeError("Install Clip Studio requirements to enable YouTube publishing") from error

    YOUTUBE_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    credentials = None
    if YOUTUBE_TOKEN.is_file():
        credentials = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN), YOUTUBE_SCOPES)
    if credentials and not credentials.valid and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            credentials = None
    if not credentials or not credentials.valid:
        youtube_secret = _youtube_secret()
        if not youtube_secret.is_file():
            raise RuntimeError("Set CLIPPER_YOUTUBE_CLIENT_SECRET to a Google Desktop OAuth JSON file")
        flow = InstalledAppFlow.from_client_secrets_file(str(youtube_secret), YOUTUBE_SCOPES)
        credentials = flow.run_local_server(port=0, open_browser=True)
    YOUTUBE_TOKEN.write_text(credentials.to_json(), encoding="utf-8")

    youtube = build("youtube", "v3", credentials=credentials)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": caption, "categoryId": "22"},
            "status": _youtube_upload_status(privacy, scheduled_at),
        },
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True,
                                   chunksize=5 * 1024 * 1024),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube did not return an uploaded video ID")
    return {"url": f"https://www.youtube.com/watch?v={video_id}", "id": video_id}


def publish_video(platform: str, video: Path, title: str, caption: str,
                  privacy: str = "private", scheduled_at: str | None = None) -> dict:
    if platform == "youtube":
        return _publish_youtube(video, title, caption, privacy, scheduled_at)
    if platform == "instagram":
        return _publish_instagram(video, title, caption)
    if platform == "facebook":
        return _publish_facebook(video, title, caption)
    if platform == "x":
        return _publish_x(video, title, caption)
    raise ValueError("unsupported publishing platform")