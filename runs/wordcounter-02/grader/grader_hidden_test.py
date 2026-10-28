"""Coordinator-owned acceptance oracle. Not mounted into worker runs."""

import json
import subprocess
import tempfile
from pathlib import Path
import unittest


class Acceptance(unittest.TestCase):
    def invoke(self, payload=None, missing=False, args=None):
        with tempfile.TemporaryDirectory() as td:
            incoming = Path(td) / "input.json"
            outgoing = Path(td) / "output.json"
            if not missing:
                incoming.write_text(json.dumps(payload) if not isinstance(payload, bytes)
                                    else payload.decode("utf-8"))
            supplied = args if args is not None else [str(incoming), str(outgoing)]
            run = subprocess.run(["python3", str(Path.cwd() / "wordfreq.py"), *supplied], capture_output=True, text=True)
            output = json.loads(outgoing.read_text()) if outgoing.exists() else None
            return run, output

    def test_aggregation_and_sort(self):
        run, result = self.invoke(["b", "A", "b", "a", "A", "☃"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(result, [
            {"word": "A", "count": 2}, {"word": "a", "count": 1},
            {"word": "b", "count": 2}, {"word": "☃", "count": 1}])

    def test_empty(self):
        run, result = self.invoke([])
        self.assertEqual((run.returncode, result), (0, []))

    def test_non_string_rejected(self):
        for bad in (["a", 1], {"a": 1}, None):
            run, result = self.invoke(bad)
            self.assertEqual(run.returncode, 2)
            self.assertIsNone(result)

    def test_missing_rejected(self):
        run, result = self.invoke(missing=True)
        self.assertEqual(run.returncode, 2)
        self.assertIsNone(result)

    def test_argument_count(self):
        run, result = self.invoke(args=[])
        self.assertEqual(run.returncode, 2)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
