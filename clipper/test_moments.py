import unittest

from clipper.moments import _clean, _fallback_title


class TitleSuggestionTests(unittest.TestCase):
    source_title = "Kai Cenat's First Time Playing GTA 6!"

    def test_profanity_fallback_uses_clip_context(self):
        title = _fallback_title(
            "ass the fuck out of here come here bitch all right let me pick you up",
            source_title=self.source_title,
            creator="KaiCenatGaming",
        )

        self.assertEqual(title, "Kai Cenat's GTA 6: Rescue Mission Goes Wrong")

    def test_short_transcript_uses_source_context(self):
        title = _fallback_title("Jump.", source_title=self.source_title, creator="KaiCenatGaming")

        self.assertNotEqual(title, "Jump.")
        self.assertIn("GTA 6", title)

    def test_clean_model_title_is_preserved_and_profanity_is_replaced(self):
        segments = [{"start": 0.0, "end": 30.0, "text": "let me pick you up and take you and your friend"}]
        clips = _clean([{
            "start": 0.0, "end": 30.0,
            "title": "ass the fuck out of here bitch",
        }], segments, 20.0, 58.0, 1, [], self.source_title, "KaiCenatGaming")

        self.assertEqual(clips[0]["title"], "Kai Cenat's GTA 6: Rescue Mission Goes Wrong")
        self.assertEqual(len(clips[0]["title_variants"]), 2)


if __name__ == "__main__":
    unittest.main()