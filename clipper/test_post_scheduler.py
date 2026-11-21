"""Tests for the in-process scheduled post queue."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from clipper import post_scheduler


class ScheduledPostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clips_dir = self.root / "clips"
        self.clips_dir.mkdir()
        self.schedule_file = self.root / "scheduled-posts.json"
        self.patches = [
            patch.object(post_scheduler, "CLIPS_DIR", self.clips_dir),
            patch.object(post_scheduler, "SCHEDULE_FILE", self.schedule_file),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    def test_schedules_and_cancels_post(self):
        post = post_scheduler.schedule_post(
            "20261120-120000-ab12", "A video", "short_01.mp4", ["instagram"],
            "A title", "A caption", "public", datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.assertEqual(post["status"], "scheduled")
        cancelled = post_scheduler.cancel_post(post["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(post_scheduler.list_posts()[0]["status"], "cancelled")

    @patch.object(post_scheduler.publishing, "publish_video", return_value={"url": "https://example.test/post"})
    def test_due_post_publishes_and_records_history(self, publish_video):
        job_dir = self.clips_dir / "20261120-120000-ab12"
        job_dir.mkdir()
        (job_dir / "short_01.mp4").write_bytes(b"video")
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        post = post_scheduler.schedule_post(
            job_dir.name, "A video", "short_01.mp4", ["instagram"],
            "A title", "A caption", "public", due,
        )

        completed = post_scheduler.run_due()

        self.assertEqual([item["id"] for item in completed], [post["id"]])
        self.assertEqual(completed[0]["status"], "published")
        publish_video.assert_called_once_with("instagram", job_dir / "short_01.mp4", "A title", "A caption", "public")
        history = json.loads((job_dir / "publications.json").read_text(encoding="utf-8"))
        self.assertEqual(history["short_01.mp4"][0]["url"], "https://example.test/post")

    def test_recover_interrupted_posts_without_republishing(self):
        self.schedule_file.write_text(json.dumps([{"id": "post-1", "status": "publishing"}]))
        post_scheduler.recover_interrupted()
        saved = json.loads(self.schedule_file.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["status"], "failed")
        self.assertIn("verify platform status", saved[0]["error"])


if __name__ == "__main__":
    unittest.main()