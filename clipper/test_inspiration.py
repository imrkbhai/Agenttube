import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from clipper import dashboard


class InspirationFallbackTests(unittest.TestCase):
    def test_uses_local_snapshot_when_remote_request_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backup = Path(temp_dir) / "videos.json"
            backup.write_text(json.dumps([{
                "slug": "webgl-demo",
                "author": "example",
                "category": "3d",
                "prompt": "Create a WebGL animation",
            }]), encoding="utf-8")

            with patch.object(dashboard, "INSPIRATION_BACKUP", backup):
                with patch("requests.get", side_effect=requests.ConnectionError("offline")):
                    with patch.dict(dashboard._inspiration, {}, clear=True):
                        results = dashboard.inspiration("webgl", "3d")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "webgl-demo")


if __name__ == "__main__":
    unittest.main()