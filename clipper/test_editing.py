"""Tests for native clip editing."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clipper.editing import EditingError, create_edit
from clipper.jobs import write_json


class CreateEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.job_dir = Path(self.temp.name)
        write_json(self.job_dir / "job.json", {"state": "finished"})
        write_json(self.job_dir / "manifest.json", {"clips": [{
            "file": "short_01.mp4", "format": "short", "start": 20, "end": 30,
            "duration": 10, "title": "A highlight", "description": "A clip",
            "status": "approved",
        }]})
        (self.job_dir / "short_01.mp4").write_bytes(b"source")

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_creates_reviewable_variant_without_replacing_source(self):
        def fake_run(command, **_kwargs):
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout=json.dumps({"format": {"duration": "10"}}))
            Path(command[-1]).write_bytes(b"edited")
            return SimpleNamespace(stdout="")

        with patch("clipper.editing.subprocess.run", side_effect=fake_run):
            edited = create_edit(self.job_dir, {
                "file": "short_01.mp4", "start": 1, "end": 9,
                "aspect": "portrait", "crop": "center", "volume": 0, "speed": 2,
            })

        self.assertEqual(edited["file"], "short_01_edit_01.mp4")
        self.assertEqual(edited["duration"], 4)
        self.assertEqual(edited["status"], "pending_review")
        self.assertEqual(edited["edit_of"], "short_01.mp4")
        self.assertEqual((self.job_dir / "short_01.mp4").read_bytes(), b"source")
        manifest = json.loads((self.job_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["clips"]), 2)

    def test_rejects_trim_outside_clip(self):
        with patch("clipper.editing.subprocess.run", return_value=SimpleNamespace(
                stdout=json.dumps({"format": {"duration": "10"}}))) as run:
            with self.assertRaisesRegex(EditingError, "trim range"):
                create_edit(self.job_dir, {"file": "short_01.mp4", "start": 2, "end": 11})
        self.assertEqual(run.call_count, 1)

    def test_rejects_unlisted_source_file(self):
        (self.job_dir / "short_02.mp4").write_bytes(b"not listed")
        with self.assertRaisesRegex(EditingError, "not in this job"):
            create_edit(self.job_dir, {"file": "short_02.mp4"})

    def test_long_portrait_edit_remains_a_long_video(self):
        (self.job_dir / "long_01.mp4").write_bytes(b"source")
        write_json(self.job_dir / "manifest.json", {"clips": [{
            "file": "long_01.mp4", "format": "long", "start": 0, "end": 80,
            "duration": 80, "title": "Long clip",
        }]})

        def fake_run(command, **_kwargs):
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout=json.dumps({"format": {"duration": "80"}}))
            Path(command[-1]).write_bytes(b"edited")
            return SimpleNamespace(stdout="")

        with patch("clipper.editing.subprocess.run", side_effect=fake_run):
            edited = create_edit(self.job_dir, {
                "file": "long_01.mp4", "start": 0, "end": 80, "aspect": "portrait",
            })

        self.assertEqual(edited["format"], "long")

    def test_gaming_layout_uses_original_source_and_stacks_drawn_regions(self):
        source = self.job_dir / "source.mp4"
        source.write_bytes(b"wide source")
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout=json.dumps({"format": {"duration": "100"}}))
            Path(command[-1]).write_bytes(b"edited")
            return SimpleNamespace(stdout="")

        with patch("clipper.editing.subprocess.run", side_effect=fake_run):
            edited = create_edit(self.job_dir, {
                "file": "short_01.mp4", "start": 1, "end": 9, "layout": "gaming",
                "webcam_box": {"x": 0.05, "y": 0.2, "width": 0.25, "height": 0.3},
                "game_box": {"x": 0.3, "y": 0.05, "width": 0.65, "height": 0.85},
                "top": "webcam", "camera_share": 38,
            })

        command = commands[-1]
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(Path(command[command.index("-i") + 1]), source)
        self.assertEqual(command[command.index("-ss") + 1], "21.000")
        self.assertIn("vstack=inputs=2", graph)
        self.assertIn("scale=1080:730", graph)
        self.assertEqual(edited["edit_options"]["layout"], "gaming")
        self.assertEqual(edited["format"], "short")

    def test_active_job_source_can_render_a_reviewable_gaming_clip(self):
        source = self.job_dir / "source.mp4"
        source.write_bytes(b"downloaded source")
        write_json(self.job_dir / "job.json", {"state": "running", "title": "Stream VOD"})
        write_json(self.job_dir / "manifest.json", {"source": {"title": "Stream VOD"}, "clips": []})
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout=json.dumps({"format": {"duration": "120"}}))
            Path(command[-1]).write_bytes(b"edited")
            return SimpleNamespace(stdout="")

        with patch("clipper.editing.subprocess.run", side_effect=fake_run):
            edited = create_edit(self.job_dir, {
                "file": "__source__", "start": 12, "end": 32, "layout": "gaming",
                "webcam_box": {"x": 0.05, "y": 0.2, "width": 0.25, "height": 0.3},
                "game_box": {"x": 0.3, "y": 0.05, "width": 0.65, "height": 0.85},
                "top": "webcam", "camera_share": 38,
            })

        render_command = commands[-1]
        self.assertEqual(Path(render_command[render_command.index("-i") + 1]), source)
        self.assertEqual(render_command[render_command.index("-ss") + 1], "12.000")
        self.assertEqual(edited["file"], "short_01_edit_01.mp4")
        self.assertTrue(edited["source_edit"])
        self.assertEqual(edited["status"], "pending_review")
        self.assertEqual(json.loads((self.job_dir / "manifest.json").read_text())["clips"], [])
        editor_clips = json.loads((self.job_dir / "editor_clips.json").read_text())
        self.assertEqual(editor_clips[0]["file"], edited["file"])

    def test_source_edit_waits_until_download_is_ready(self):
        (self.job_dir / "source.mp4").write_bytes(b"partial download")
        (self.job_dir / "manifest.json").unlink()
        write_json(self.job_dir / "job.json", {"state": "running"})

        with patch("clipper.editing.subprocess.run") as run:
            with self.assertRaisesRegex(EditingError, "download is still in progress"):
                create_edit(self.job_dir, {"file": "__source__"})

        run.assert_not_called()

    def test_podcast_layout_requires_both_regions_inside_source_frame(self):
        (self.job_dir / "source.mp4").write_bytes(b"wide source")
        with patch("clipper.editing.subprocess.run", return_value=SimpleNamespace(
                stdout=json.dumps({"format": {"duration": "100"}}))):
            with self.assertRaisesRegex(EditingError, "inside the source frame"):
                create_edit(self.job_dir, {
                    "file": "short_01.mp4", "layout": "podcast",
                    "speaker_a_box": {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.5},
                    "speaker_b_box": {"x": 0.8, "y": 0.2, "width": 0.4, "height": 0.5},
                })

    def test_podcast_layout_renders_two_speakers_in_selected_order(self):
        (self.job_dir / "source.mp4").write_bytes(b"wide source")
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout=json.dumps({"format": {"duration": "100"}}))
            Path(command[-1]).write_bytes(b"edited")
            return SimpleNamespace(stdout="")

        with patch("clipper.editing.subprocess.run", side_effect=fake_run):
            edited = create_edit(self.job_dir, {
                "file": "short_01.mp4", "start": 1, "end": 9, "layout": "podcast",
                "speaker_a_box": {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.5},
                "speaker_b_box": {"x": 0.6, "y": 0.2, "width": 0.3, "height": 0.5},
                "top": "speaker_b",
            })

        graph = commands[-1][commands[-1].index("-filter_complex") + 1]
        self.assertIn("[v0]crop=w='trunc(iw*0.300000/2)*2'", graph)
        self.assertIn("[v1]crop=w='trunc(iw*0.250000/2)*2'", graph)
        self.assertEqual(edited["edit_options"]["top"], "speaker_b")
        self.assertEqual(edited["edit_options"]["layout"], "podcast")


if __name__ == "__main__":
    unittest.main()