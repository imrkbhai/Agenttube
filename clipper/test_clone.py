import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from clipper import clone, create


class FakeCapture:
    def __init__(self):
        self.frames = [
            np.full((72, 128, 3), (0, 0, 255), dtype=np.uint8),
            np.full((72, 128, 3), (0, 0, 255), dtype=np.uint8),
            np.full((72, 128, 3), (255, 0, 0), dtype=np.uint8),
            np.full((72, 128, 3), (255, 0, 0), dtype=np.uint8),
        ]
        self.frame_index = 0
        self.released = False

    def get(self, prop):
        return {
            clone.cv2.CAP_PROP_FPS: 2,
            clone.cv2.CAP_PROP_FRAME_COUNT: len(self.frames),
            clone.cv2.CAP_PROP_FRAME_WIDTH: 128,
            clone.cv2.CAP_PROP_FRAME_HEIGHT: 72,
        }.get(prop, 0)

    def set(self, _prop, value):
        self.frame_index = int(value)

    def read(self):
        if self.frame_index >= len(self.frames):
            return False, None
        return True, self.frames[self.frame_index]

    def release(self):
        self.released = True


class CloneStyleAnalysisTests(unittest.TestCase):
    def test_measures_reference_format_pacing_and_releases_video(self):
        capture = FakeCapture()
        with patch.object(clone.cv2, "VideoCapture", return_value=capture):
            profile = clone.analyze_visual_style("reference.mp4")

        self.assertEqual(profile["duration_s"], 2.0)
        self.assertEqual(profile["aspect"], "landscape")
        self.assertEqual(profile["estimated_shot_count"], 2)
        self.assertEqual(profile["average_color"], "#800080")
        self.assertEqual(profile["sampled_frames"], 4)
        self.assertTrue(capture.released)

    def test_rejects_video_with_unavailable_metadata(self):
        capture = FakeCapture()
        capture.get = lambda _prop: 0
        with patch.object(clone.cv2, "VideoCapture", return_value=capture):
            with self.assertRaisesRegex(RuntimeError, "dimensions or duration"):
                clone.analyze_visual_style("broken.mp4")

        self.assertTrue(capture.released)


class HookTestVariantTests(unittest.TestCase):
    def test_expands_hook_candidates_with_the_same_body_and_cta(self):
        shared_script = [{
            "beat": "setup", "seconds": 5, "narration": "The common explanation.",
            "on_screen_text": "The fact", "visual": "A simple chart",
        }]
        result = {
            "shared_script": shared_script,
            "shared_cta": "Follow for more.",
            "variants": [
                {"title": "First", "hook": "A surprising question"},
                {"title": "Second", "hook": "A bold claim"},
            ],
        }

        normalized = clone._expand_hook_test_result(result)

        self.assertEqual(normalized["variants"][0]["script"][1:], shared_script)
        self.assertEqual(normalized["variants"][1]["script"][1:], shared_script)
        self.assertEqual(normalized["variants"][0]["cta"], normalized["variants"][1]["cta"])
        self.assertNotEqual(normalized["variants"][0]["script"][0]["narration"],
                            normalized["variants"][1]["script"][0]["narration"])

    def test_rejects_hook_variants_without_a_shared_body(self):
        with self.assertRaisesRegex(RuntimeError, "shared hook-test script body"):
            clone._expand_hook_test_result({"shared_script": [], "variants": []})


class LocalReferenceTests(unittest.TestCase):
    def test_local_reference_does_not_use_youtube_downloader(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.mp4"
            reference.touch()

            with patch.object(clone, "check_url", side_effect=AssertionError("URL validation was called")):
                result = clone._fetch_reference(str(reference), Path(directory))

        self.assertEqual(result["path"], str(reference.resolve()))
        self.assertEqual(result["title"], "reference")
        self.assertEqual(result["url"], "Uploaded: reference.mp4")


class ReferenceRenderProfileTests(unittest.TestCase):
    def test_builds_render_settings_from_reference_style(self):
        profile = create.reference_render_profile({
            "aspect": "landscape", "average_color": "#204060",
            "motion_level": "low", "edit_pace": "slow",
        })

        self.assertEqual(profile["format"], "long")
        self.assertEqual(profile["look"]["box"], "&H00604020")
        self.assertEqual(profile["average_color"], "#204060")
        self.assertAlmostEqual(profile["camera_strength"], 0.045)

    def test_uses_safe_defaults_for_incomplete_style_data(self):
        profile = create.reference_render_profile({})

        self.assertEqual(profile["format"], "short")
        self.assertEqual(profile["average_color"], "#808080")
        self.assertGreater(profile["camera_strength"], 0)


if __name__ == "__main__":
    unittest.main()