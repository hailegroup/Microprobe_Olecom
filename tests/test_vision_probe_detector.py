# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import cv2
import numpy as np

from vision.probe_detector import ProbeDetector, ProbeTip, load_probe_params


def _wedge_probe_image(*, disc_value, background_value, notch_apex=(100, 75)):
    """A bright/dark disc with a wedge (the 'probe') cut out of the top,
    pointing down toward notch_apex -- the classic occlusion scenario the
    convexity-defect strategy targets."""
    img = np.full((200, 200), background_value, dtype=np.uint8)
    cv2.circle(img, (100, 100), 50, disc_value, -1)
    pts = np.array([[80, 40], [120, 40], notch_apex], dtype=np.int32)
    cv2.fillPoly(img, [pts], background_value)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


class ProbeDetectorTests(unittest.TestCase):
    def test_probe_tip_has_no_confidence_field(self):
        tip = ProbeTip(x=1.0, y=2.0)
        self.assertFalse(hasattr(tip, "confidence"))

    def test_detects_tip_bright_disc_dark_probe(self):
        frame = _wedge_probe_image(disc_value=220, background_value=40)
        det = ProbeDetector(clahe_clip=4.0)
        tip = det.detect(frame)
        self.assertIsNotNone(tip)
        self.assertTrue(tip.detected)
        self.assertAlmostEqual(tip.x, 100.0, delta=5.0)
        self.assertAlmostEqual(tip.y, 75.0, delta=5.0)

    def test_detects_tip_dark_disc_bright_probe(self):
        frame = _wedge_probe_image(disc_value=40, background_value=220)
        det = ProbeDetector(clahe_clip=4.0)
        tip = det.detect(frame)
        self.assertIsNotNone(tip)
        self.assertTrue(tip.detected)
        self.assertAlmostEqual(tip.x, 100.0, delta=5.0)
        self.assertAlmostEqual(tip.y, 75.0, delta=5.0)

    def test_no_detection_on_blank_frame_returns_none_on_first_call(self):
        frame = np.full((200, 200, 3), 128, dtype=np.uint8)
        det = ProbeDetector()
        tip = det.detect(frame)
        self.assertIsNone(tip)

    def test_lost_detection_after_prior_success_keeps_last_position(self):
        frame = _wedge_probe_image(disc_value=220, background_value=40)
        det = ProbeDetector(clahe_clip=4.0)
        tip = det.detect(frame)
        self.assertTrue(tip.detected)
        last_x, last_y = tip.x, tip.y

        blank = np.full((200, 200, 3), 128, dtype=np.uint8)
        tip2 = det.detect(blank)
        self.assertIsNotNone(tip2)
        self.assertFalse(tip2.detected)
        self.assertEqual(tip2.x, last_x)
        self.assertEqual(tip2.y, last_y)

class LoadProbeParamsTests(unittest.TestCase):
    def test_round_trip_maps_saved_probe_tuner_shape(self):
        # Shape saved by vision/tuning_gui/probe_gui.py's 's' key -- ROI and
        # lines are intentionally not read (see load_probe_params's
        # docstring), only preprocessing.* and probe.invert.
        payload = {
            'source_image': 'probe.png',
            'preprocessing': {'clahe_clip': 3.5, 'clahe_tile': 6, 'bilateral_d': 7, 'bilateral_sigma': 60.0},
            'probe': {'invert': True, 'roi': [1, 2, 3, 4], 'lines': None},
        }
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            params = load_probe_params(path)
        finally:
            os.unlink(path)

        self.assertEqual(params, {
            'clahe_clip': 3.5, 'clahe_tile': 6, 'bilateral_d': 7,
            'bilateral_sigma': 60.0, 'invert': True,
        })

    def test_missing_keys_fall_back_to_probe_detector_defaults(self):
        payload = {}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            params = load_probe_params(path)
        finally:
            os.unlink(path)

        self.assertEqual(params, {
            'clahe_clip': 4.0, 'clahe_tile': 4, 'bilateral_d': 9,
            'bilateral_sigma': 75.0, 'invert': False,
        })

    def test_result_constructs_a_working_probe_detector(self):
        payload = {'preprocessing': {'clahe_clip': 4.0}, 'probe': {'invert': False}}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            params = load_probe_params(path)
        finally:
            os.unlink(path)

        detector = ProbeDetector(**params)
        frame = _wedge_probe_image(disc_value=220, background_value=40)
        tip = detector.detect(frame)
        self.assertTrue(tip.detected)

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            load_probe_params('/nonexistent/path/to/params.json')


if __name__ == "__main__":
    unittest.main()
