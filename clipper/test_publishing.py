import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipper import dashboard
from clipper import publishing


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class PublishClipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp.name)
        (self.job_dir / "short_01.mp4").write_bytes(b"video")
        (self.job_dir / "job.json").write_text(json.dumps({"id": "20261120-120000-ab12", "state": "finished"}))
        (self.job_dir / "manifest.json").write_text(json.dumps({"clips": [{
            "file": "short_01.mp4", "format": "short", "duration": 45, "title": "Test clip",
        }]}))
        (self.job_dir / "review.json").write_text(json.dumps({"short_01.mp4": "approved"}))

    def tearDown(self):
        self.temp.cleanup()

    def test_source_frame_extracts_jpeg_from_original_clip_time(self):
        source = self.job_dir / "source.mp4"
        source.write_bytes(b"wide source")
        manifest = json.loads((self.job_dir / "manifest.json").read_text())
        manifest["clips"][0].update(start=20, end=65, duration=45)
        (self.job_dir / "manifest.json").write_text(json.dumps(manifest))

        with patch.object(dashboard, "_job_dir", return_value=self.job_dir):
            with patch.object(dashboard.subprocess, "run", return_value=SimpleNamespace(stdout=b"\xff\xd8jpeg")) as run:
                frame = dashboard.source_frame("20261120-120000-ab12", "short_01.mp4", "3")

        self.assertEqual(frame[:2], b"\xff\xd8")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "23.000")
        self.assertEqual(Path(command[command.index("-i") + 1]), source)

    def test_source_frame_rejects_time_outside_clip(self):
        with patch.object(dashboard, "_job_dir", return_value=self.job_dir):
            with patch.object(dashboard.subprocess, "run") as run:
                with self.assertRaisesRegex(dashboard.BadRequest, "inside the selected clip"):
                    dashboard.source_frame("20261120-120000-ab12", "short_01.mp4", "46")

        run.assert_not_called()

    def test_source_frame_extracts_from_downloaded_video_time(self):
        source = self.job_dir / "source.mp4"
        source.write_bytes(b"downloaded source")
        (self.job_dir / "manifest.json").write_text(json.dumps({
            "source": {"duration": 100}, "clips": [],
        }))

        with patch.object(dashboard, "_job_dir", return_value=self.job_dir):
            with patch.object(dashboard.subprocess, "run", return_value=SimpleNamespace(stdout=b"\xff\xd8jpeg")) as run:
                frame = dashboard.source_frame(
                    "20261120-120000-ab12", dashboard.SOURCE_ENTRY, "73.5",
                )

        self.assertEqual(frame[:2], b"\xff\xd8")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "73.500")
        self.assertEqual(Path(command[command.index("-i") + 1]), source)

    def test_source_frame_rejects_time_beyond_downloaded_video(self):
        (self.job_dir / "source.mp4").write_bytes(b"downloaded source")
        (self.job_dir / "manifest.json").write_text(json.dumps({
            "source": {"duration": 100}, "clips": [],
        }))

        with patch.object(dashboard, "_job_dir", return_value=self.job_dir):
            with patch.object(dashboard.subprocess, "run") as run:
                with self.assertRaisesRegex(dashboard.BadRequest, "inside the source video"):
                    dashboard.source_frame(
                        "20261120-120000-ab12", dashboard.SOURCE_ENTRY, "101",
                    )

        run.assert_not_called()

    def test_deletes_failed_interrupted_and_stopped_job_folders(self):
        job_id = "20261120-120000-ab12"
        for state in ("failed", "interrupted", "stopped"):
            with self.subTest(state=state):
                job_dir = self.job_dir / state
                job_dir.mkdir()
                (job_dir / "job.json").write_text(json.dumps({"id": job_id, "state": state}))
                with patch.object(dashboard, "_job_dir", return_value=job_dir):
                    dashboard.delete_job(job_id)
                self.assertFalse(job_dir.exists())

    def test_rejects_deleting_finished_job(self):
        job_id = "20261120-120000-ab12"
        job_dir = self.job_dir / "finished"
        job_dir.mkdir()
        (job_dir / "job.json").write_text(json.dumps({"id": job_id, "state": "finished"}))

        with patch.object(dashboard, "_job_dir", return_value=job_dir):
            with self.assertRaisesRegex(dashboard.BadRequest, "Only failed, interrupted, or stopped"):
                dashboard.delete_job(job_id)

        self.assertTrue(job_dir.exists())

    def test_does_not_delete_stopped_job_while_worker_is_alive(self):
        job_id = "20261120-120000-ab12"
        job_dir = self.job_dir / "stopped"
        job_dir.mkdir()
        (job_dir / "job.json").write_text(json.dumps({"id": job_id, "state": "stopped", "pid": 1234}))
        process = unittest.mock.Mock()
        process.poll.return_value = None

        with patch.object(dashboard, "_job_dir", return_value=job_dir):
            with patch.dict(dashboard._processes, {job_id: process}):
                with self.assertRaisesRegex(dashboard.BadRequest, "Wait for the job worker to stop"):
                    dashboard.delete_job(job_id)

        self.assertTrue(job_dir.exists())

    @patch.object(dashboard, "_job_dir")
    @patch.object(dashboard.publishing, "publishing_status")
    @patch.object(dashboard.publishing, "publish_video")
    def test_publishes_selected_platforms_and_saves_links(self, publish_video, publishing_status, job_dir):
        job_dir.return_value = self.job_dir
        publishing_status.return_value = {name: {"configured": True, "message": "Ready"}
                                          for name in ("youtube", "instagram", "facebook", "x")}
        publish_video.side_effect = lambda platform, *args: {"url": f"https://{platform}.example/post/1", "id": "1"}

        result = dashboard.publish_clip("20261120-120000-ab12", {
            "file": "short_01.mp4", "platforms": ["youtube", "x"], "title": "Test clip",
        })

        self.assertEqual([item["platform"] for item in result["results"]], ["youtube", "x"])
        saved = json.loads((self.job_dir / "publications.json").read_text())
        self.assertEqual(saved["short_01.mp4"][0]["url"], "https://youtube.example/post/1")
        self.assertEqual(saved["short_01.mp4"][1]["platform"], "x")

    @patch.object(dashboard, "_job_dir")
    @patch.object(dashboard.publishing, "handoff_to_trypost")
    def test_creates_trypost_scheduled_post_for_approved_short(self, handoff, job_dir):
        job_dir.return_value = self.job_dir
        handoff.return_value = {
            "url": "https://trypost.example", "ids": ["post-1"],
            "destinations": [{"id": "post-1", "account_id": "youtube-account-1", "platform": "youtube"}],
            "status": "scheduled", "accounts": ["youtube-account-1"],
        }

        result = dashboard.trypost_post("20261120-120000-ab12", {
            "file": "short_01.mp4", "social_account_ids": ["youtube-account-1"],
            "title": "Test clip", "status": "scheduled",
            "scheduled_times": {"youtube-account-1": "2026-12-01T10:00:00Z"}, "privacy": "public",
        })

        self.assertTrue(result["ok"])
        handoff.assert_called_once_with(
            self.job_dir / "short_01.mp4", "Test clip", "", ["youtube-account-1"],
            status="scheduled", scheduled_at=None, privacy="public", media_format="short",
            scheduled_times={"youtube-account-1": "2026-12-01T10:00:00Z"}, content_types=None,
        )
        saved = json.loads((self.job_dir / "publications.json").read_text())
        self.assertEqual(saved["short_01.mp4"][0]["platform"], "youtube")
        self.assertEqual(saved["short_01.mp4"][0]["service"], "TryPost")
        self.assertEqual(saved["short_01.mp4"][0]["status"], "scheduled")

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[{"id": "youtube-1", "platform": "youtube"}])
    @patch.object(publishing.requests, "post")
    def test_trypost_rejects_long_video_for_youtube_before_upload(self, post, accounts):
        with self.assertRaisesRegex(RuntimeError, "remove the YouTube account"):
            publishing.handoff_to_trypost(
                self.job_dir / "short_01.mp4", "Title", "Caption", ["youtube-1"], media_format="long",
            )

        post.assert_not_called()

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[{"id": "instagram-1", "platform": "instagram"}])
    @patch.object(publishing.requests, "post")
    def test_trypost_rejects_unsupported_account_format_before_upload(self, post, accounts):
        with self.assertRaisesRegex(RuntimeError, "Unsupported TryPost video format for instagram"):
            publishing.handoff_to_trypost(
                self.job_dir / "short_01.mp4", "Title", "Caption", ["instagram-1"],
                content_types={"instagram-1": "youtube_short"},
            )

        post.assert_not_called()

    @patch.object(dashboard, "_job_dir")
    @patch.object(dashboard.publishing, "handoff_to_trypost")
    def test_routes_long_video_to_trypost_for_non_youtube_targets(self, handoff, job_dir):
        job_dir.return_value = self.job_dir
        (self.job_dir / "long_01.mp4").write_bytes(b"video")
        manifest = json.loads((self.job_dir / "manifest.json").read_text())
        manifest["clips"].append({
            "file": "long_01.mp4", "format": "long", "duration": 600, "title": "Long video",
        })
        (self.job_dir / "manifest.json").write_text(json.dumps(manifest))
        (self.job_dir / "review.json").write_text(json.dumps({"short_01.mp4": "approved", "long_01.mp4": "approved"}))
        handoff.return_value = {
            "url": "https://trypost.example", "ids": ["post-1"], "status": "draft",
            "accounts": ["instagram-1"],
            "destinations": [{"id": "post-1", "account_id": "instagram-1", "platform": "instagram"}],
        }

        result = dashboard.trypost_post("20261120-120000-ab12", {
            "file": "long_01.mp4", "social_account_ids": ["instagram-1"],
            "title": "Long video", "status": "draft",
        })

        self.assertTrue(result["ok"])
        handoff.assert_called_once_with(
            self.job_dir / "long_01.mp4", "Long video", "", ["instagram-1"],
            status="draft", scheduled_at=None, privacy="public", media_format="long", scheduled_times=None,
            content_types=None,
        )

    @patch.object(dashboard, "_job_dir")
    @patch.object(dashboard.publishing, "publishing_status")
    @patch.object(dashboard.publishing, "publish_video")
    def test_schedules_approved_long_video_with_youtube_api(self, publish_video, publishing_status, job_dir):
        job_dir.return_value = self.job_dir
        (self.job_dir / "long_01.mp4").write_bytes(b"video")
        manifest = json.loads((self.job_dir / "manifest.json").read_text())
        manifest["clips"].append({
            "file": "long_01.mp4", "format": "long", "duration": 600, "title": "Long video",
        })
        (self.job_dir / "manifest.json").write_text(json.dumps(manifest))
        (self.job_dir / "review.json").write_text(json.dumps({"short_01.mp4": "approved", "long_01.mp4": "approved"}))
        publishing_status.return_value = {"youtube": {"configured": True, "message": "Ready"}}
        publish_video.return_value = {"url": "https://youtube.example/watch?v=video-1", "id": "video-1"}

        result = dashboard.youtube_post("20261120-120000-ab12", {
            "file": "long_01.mp4", "title": "Long video", "caption": "Description",
            "status": "scheduled", "scheduled_at": "2026-12-01T10:00:00Z", "privacy": "public",
        })

        self.assertEqual(result["status"], "scheduled")
        publish_video.assert_called_once_with(
            "youtube", self.job_dir / "long_01.mp4", "Long video", "Description", "private",
            scheduled_at="2026-12-01T10:00:00Z",
        )
        saved = json.loads((self.job_dir / "publications.json").read_text())
        self.assertEqual(saved["long_01.mp4"][0]["status"], "scheduled")

    def test_youtube_schedule_stays_private_until_publish_at(self):
        self.assertEqual(publishing._youtube_upload_status(
            "public", "2026-12-01T10:00:00Z",
        ), {
            "privacyStatus": "private", "selfDeclaredMadeForKids": False,
            "publishAt": "2026-12-01T10:00:00Z",
        })

    def test_youtube_draft_uses_private_visibility(self):
        self.assertEqual(publishing._youtube_upload_status("private"), {
            "privacyStatus": "private", "selfDeclaredMadeForKids": False,
        })

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[{
        "id": "youtube-account-1", "platform": "youtube", "display_name": "My channel",
    }])
    @patch.object(publishing.requests, "post")
    def test_trypost_uploads_once_and_schedules_youtube_short(self, post, accounts):
        post.side_effect = [
            FakeResponse({"data": {"upload_token": "upload-token-1"}}),
            FakeResponse({"posts": [{"id": "post-1"}]}),
        ]

        result = publishing.handoff_to_trypost(
            self.job_dir / "short_01.mp4", "Title", "Description", ["youtube-account-1"],
            status="scheduled", scheduled_at="2026-12-01T10:00:00Z", privacy="public",
        )

        self.assertEqual(result, {
            "url": "https://trypost.example", "ids": ["post-1"], "status": "scheduled",
            "accounts": ["youtube-account-1"], "failures": [],
            "destinations": [{"id": "post-1", "account_id": "youtube-account-1", "platform": "youtube",
                              "content_type": "youtube_short", "scheduled_at": "2026-12-01T10:00:00Z"}],
        })
        accounts.assert_called_once_with()
        upload_call, batch_call = post.call_args_list
        self.assertEqual(upload_call.args[0], "https://trypost.example/api/uploads")
        self.assertTrue(upload_call.kwargs["headers"]["Content-Type"].startswith("multipart/form-data;"))
        self.assertEqual(upload_call.kwargs["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(batch_call.args[0], "https://trypost.example/api/posts/batch")
        self.assertEqual(batch_call.kwargs["json"]["status"], "scheduled")
        self.assertEqual(batch_call.kwargs["json"]["scheduled_at"], "2026-12-01T10:00:00Z")
        self.assertEqual(batch_call.kwargs["json"]["media"], [{"upload_token": "upload-token-1"}])
        self.assertEqual(batch_call.kwargs["json"]["destinations"], [{
            "social_account_id": "youtube-account-1", "content_type": "youtube_short",
            "content": "Description",
            "meta": {"title": "Title", "description": "Description", "privacy_status": "public"},
        }])

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[
        {"id": "youtube-1", "platform": "youtube"},
        {"id": "instagram-1", "platform": "instagram"},
        {"id": "facebook-1", "platform": "facebook"},
    ])
    @patch.object(publishing.requests, "post")
    def test_trypost_fans_out_one_upload_to_meta_and_youtube(self, post, accounts):
        post.side_effect = [
            FakeResponse({"data": {"upload_token": "upload-token-1"}}),
            FakeResponse({"posts": [{"id": "yt-post"}, {"id": "ig-post"}, {"id": "fb-post"}]}),
        ]

        result = publishing.handoff_to_trypost(
            self.job_dir / "short_01.mp4", "Title", "Caption",
            ["youtube-1", "instagram-1", "facebook-1"], status="draft",
            content_types={"instagram-1": "instagram_feed", "facebook-1": "facebook_post"},
        )

        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["ids"], ["yt-post", "ig-post", "fb-post"])
        self.assertEqual([destination["platform"] for destination in result["destinations"]],
                         ["youtube", "instagram", "facebook"])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["destinations"], [
            {"social_account_id": "youtube-1", "content_type": "youtube_short", "content": "Caption",
             "meta": {"title": "Title", "description": "Caption", "privacy_status": "public"}},
            {"social_account_id": "instagram-1", "content_type": "instagram_feed", "content": "Title\n\nCaption"},
            {"social_account_id": "facebook-1", "content_type": "facebook_post", "content": "Title\n\nCaption"},
        ])

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[
        {"id": "instagram-1", "platform": "instagram"},
        {"id": "facebook-1", "platform": "facebook"},
    ])
    @patch.object(publishing.requests, "post")
    def test_trypost_schedules_accounts_at_different_times_after_one_upload(self, post, accounts):
        post.side_effect = [
            FakeResponse({"data": {"upload_token": "upload-token-1"}}),
            FakeResponse({"posts": [{"id": "ig-post", "media": [{"id": "shared-media-1"}]}]}),
            FakeResponse({"posts": [{"id": "fb-post", "media": [{"id": "shared-media-2"}]}]}),
        ]

        result = publishing.handoff_to_trypost(
            self.job_dir / "short_01.mp4", "Title", "Caption", ["instagram-1", "facebook-1"],
            status="scheduled", scheduled_times={
                "instagram-1": "2026-12-01T10:00:00Z",
                "facebook-1": "2026-12-02T10:00:00Z",
            },
        )

        self.assertEqual(post.call_count, 3)
        first_batch, second_batch = post.call_args_list[1:]
        self.assertEqual(first_batch.kwargs["json"]["media"], [{"upload_token": "upload-token-1"}])
        self.assertEqual(first_batch.kwargs["json"]["scheduled_at"], "2026-12-01T10:00:00Z")
        self.assertEqual(second_batch.kwargs["json"]["media"], [{"id": "shared-media-1"}])
        self.assertEqual(second_batch.kwargs["json"]["scheduled_at"], "2026-12-02T10:00:00Z")
        self.assertEqual([destination["scheduled_at"] for destination in result["destinations"]], [
            "2026-12-01T10:00:00Z", "2026-12-02T10:00:00Z",
        ])

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing, "trypost_accounts", return_value=[{"id": "x-account-1", "platform": "x"}])
    @patch.object(publishing.requests, "post")
    def test_trypost_creates_x_video_post(self, post, accounts):
        post.side_effect = [
            FakeResponse({"data": {"upload_token": "upload-token-1"}}),
            FakeResponse({"posts": [{"id": "x-post-1"}]}),
        ]

        result = publishing.handoff_to_trypost(
            self.job_dir / "short_01.mp4", "Title", "Caption", ["x-account-1"], status="draft",
        )

        self.assertEqual(result["destinations"], [{
            "id": "x-post-1", "account_id": "x-account-1", "platform": "x",
            "content_type": "x_post", "scheduled_at": None,
        }])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["destinations"], [{
            "social_account_id": "x-account-1", "content_type": "x_post", "content": "Title\n\nCaption",
        }])

    @patch.dict("os.environ", {
        "CLIPPER_TRYPOST_URL": "https://trypost.example",
        "CLIPPER_TRYPOST_API_KEY": "test-api-key",
    })
    @patch.object(publishing.requests, "post")
    def test_trypost_surfaces_upload_api_errors(self, post):
        post.return_value = FakeResponse({"message": "Invalid API key"})
        post.return_value.ok = False
        post.return_value.status_code = 401

        with patch.object(publishing, "trypost_accounts", return_value=[{
            "id": "youtube-account-1", "platform": "youtube",
        }]):
            with self.assertRaisesRegex(RuntimeError, "TryPost HTTP 401: Invalid API key"):
                publishing.handoff_to_trypost(
                    self.job_dir / "short_01.mp4", "Title", "Description", ["youtube-account-1"],
                )

        post.assert_called_once()

    @patch.object(dashboard, "_job_dir")
    def test_rejects_clip_until_approved(self, job_dir):
        job_dir.return_value = self.job_dir
        (self.job_dir / "review.json").write_text("{}")

        with self.assertRaises(dashboard.BadRequest):
            dashboard.publish_clip("20261120-120000-ab12", {
                "file": "short_01.mp4", "platforms": ["youtube"], "title": "Test clip",
            })

    @patch.object(dashboard, "_job_dir")
    @patch.object(dashboard.publishing, "publishing_status")
    @patch.object(dashboard.publishing, "publish_video")
    def test_reports_unconfigured_platform_without_uploading(self, publish_video, publishing_status, job_dir):
        job_dir.return_value = self.job_dir
        publishing_status.return_value = {name: {"configured": name == "youtube", "message": "Configure first"}
                                          for name in ("youtube", "instagram", "facebook", "x")}
        publish_video.return_value = {"url": "https://youtube.example/post/1", "id": "1"}

        result = dashboard.publish_clip("20261120-120000-ab12", {
            "file": "short_01.mp4", "platforms": ["youtube", "instagram"], "title": "Test clip",
        })

        self.assertTrue(result["results"][0]["ok"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertEqual(publish_video.call_count, 1)

    @patch.dict("os.environ", {"CLIPPER_X_USER_ACCESS_TOKEN": "test-token"})
    @patch.object(publishing.requests, "post")
    def test_x_uses_chunked_upload_then_creates_post(self, post):
        post.side_effect = [
            FakeResponse({"data": {"id": "media-1"}}),
            FakeResponse({}),
            FakeResponse({"data": {"processing_info": {"state": "succeeded"}}}),
            FakeResponse({"data": {"id": "post-1"}}),
        ]

        result = publishing._publish_x(self.job_dir / "short_01.mp4", "Title", "Caption")

        self.assertEqual(result["url"], "https://x.com/i/status/post-1")
        self.assertEqual(post.call_count, 4)
        self.assertEqual(post.call_args.kwargs["json"], {"text": "Title\n\nCaption", "media": {"media_ids": ["media-1"]}})

    @patch.dict("os.environ", {"CLIPPER_META_ACCESS_TOKEN": "test-token", "CLIPPER_INSTAGRAM_USER_ID": "ig-1"})
    @patch.object(publishing, "_meta_request")
    @patch.object(publishing.requests, "post")
    def test_instagram_uploads_reel_then_publishes_container(self, post, meta_request):
        post.return_value = FakeResponse({"success": True})
        meta_request.side_effect = [
            {"id": "container-1"}, {"status_code": "FINISHED"},
            {"id": "media-1"}, {"permalink": "https://instagram.com/reel/media-1/"},
        ]

        result = publishing._publish_instagram(self.job_dir / "short_01.mp4", "Title", "Caption")

        self.assertEqual(result["url"], "https://instagram.com/reel/media-1/")
        self.assertEqual(meta_request.call_count, 4)
        self.assertEqual(meta_request.call_args_list[2].args[1], "ig-1/media_publish")

    @patch.dict("os.environ", {"CLIPPER_META_ACCESS_TOKEN": "test-token", "CLIPPER_FACEBOOK_PAGE_ID": "page-1"})
    @patch.object(publishing, "_meta_request")
    @patch.object(publishing.requests, "post")
    def test_facebook_uploads_and_finishes_page_reel(self, post, meta_request):
        post.return_value = FakeResponse({"success": True})
        meta_request.side_effect = [
            {"video_id": "video-1", "upload_url": "https://rupload.facebook.com/video-upload/video-1"},
            {"success": True},
        ]

        result = publishing._publish_facebook(self.job_dir / "short_01.mp4", "Title", "Caption")

        self.assertEqual(result["url"], "https://www.facebook.com/reel/video-1")
        self.assertEqual(meta_request.call_args.args[1], "page-1/video_reels")
        self.assertEqual(meta_request.call_args.kwargs["data"]["upload_phase"], "finish")


if __name__ == "__main__":
    unittest.main()