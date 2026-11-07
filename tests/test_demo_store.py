"""Run: .venv\\Scripts\\python -m unittest discover -s tests -v"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.demo_store import load_run, save_run  # noqa: E402


class DemoStore(unittest.TestCase):
    def test_round_trip_keeps_bytes_and_tuples(self):
        state = {"jpeg": b"\xff\xd8\x00binary", "terms": "linen pants", "n": [1, 2.5, None, True], "nested": {"a": (1, "b")}}
        rfq_result = {"key": ("linen pants", ("Add lining",), 500, 60, True), "result": {"mode": "agents"}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.json"
            save_run(state, {"keyword": "x"}, rfq_result, path)
            got = load_run(path)
        self.assertEqual(got["state"], state)
        self.assertEqual(got["rfq_result"]["key"], rfq_result["key"])
        self.assertIsInstance(got["rfq_result"]["key"], tuple)
        self.assertEqual(got["rfq_context"], {"keyword": "x"})

    def test_file_is_plain_json_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.json"
            save_run({"a": 1}, None, None, path)
            self.assertTrue(path.read_text(encoding="utf-8").lstrip().startswith("{"))


if __name__ == "__main__":
    unittest.main()
