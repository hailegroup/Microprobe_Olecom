# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from live_sample_validation import build_default_validation_suite, write_validation_suite


class LiveSampleValidationTests(unittest.TestCase):
    def test_default_suite_contains_normal_and_hybrid_requests(self):
        suite = build_default_validation_suite()
        self.assertEqual(suite.normal_sample.key, "normal_candidate")
        self.assertEqual(suite.hybrid_sample.key, "hybrid_candidate")
        experiment_keys = {exp.key for exp in suite.experiments}
        self.assertIn("buffered_peis_probe", experiment_keys)
        self.assertIn("dt_sweep_probe", experiment_keys)

    def test_write_validation_suite_creates_manifest_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = Path(tmp_dir)
            paths = write_validation_suite(out)
            self.assertTrue(paths["manifest"].exists())
            self.assertTrue(paths["markdown"].exists())
            manifest_text = paths["manifest"].read_text(encoding="utf-8")
            markdown_text = paths["markdown"].read_text(encoding="utf-8")
            self.assertIn("normal_candidate", manifest_text)
            self.assertIn("hybrid_candidate", manifest_text)
            self.assertIn("Live Sample Validation Suite", markdown_text)
            self.assertIn("Connect the sample you believe should be well served by normal EIS.", markdown_text)


if __name__ == "__main__":
    unittest.main()
