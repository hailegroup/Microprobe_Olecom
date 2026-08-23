# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.circle_detector import (
    detect_circle_in_roi,
    load_hough_params,
    preprocess_for_circle_search,
)


def _synthetic_circle_frame(center=(100, 100), radius=20, size=200):
    # Anti-aliased edge -- cv2.HoughCircles' internal gradient computation
    # needs some edge softness to work reliably; a hard-edged fill (the
    # default lineType) can confuse it even on a single clean circle.
    frame = np.full((size, size), 40, dtype=np.uint8)
    cv2.circle(frame, center, radius, 220, -1, lineType=cv2.LINE_AA)
    return frame


class DetectCircleInRoiTests(unittest.TestCase):
    def test_recovers_known_circle_center_and_radius(self):
        gray = _synthetic_circle_frame(center=(100, 100), radius=20)

        found = detect_circle_in_roi(gray, center=(96.0, 104.0), expected_radius=20.0)

        self.assertIsNotNone(found)
        x, y, r = found
        self.assertAlmostEqual(x, 100.0, delta=3.0)
        self.assertAlmostEqual(y, 100.0, delta=3.0)
        self.assertAlmostEqual(r, 20.0, delta=4.0)

    def test_returns_none_when_no_circle_present(self):
        gray = np.full((200, 200), 40, dtype=np.uint8)

        found = detect_circle_in_roi(gray, center=(100.0, 100.0), expected_radius=20.0)

        self.assertIsNone(found)

    def test_returns_none_on_degenerate_roi_at_frame_edge(self):
        gray = _synthetic_circle_frame()

        found = detect_circle_in_roi(gray, center=(0.0, 0.0), expected_radius=1.0, search_margin=0.0, radius_tolerance=0.0)

        self.assertIsNone(found)

    def test_picks_circle_nearest_to_expected_center_among_multiple_candidates(self):
        # HoughCircles can return several candidates within the ROI (a
        # tightly spaced neighbouring electrode easily falls inside
        # expected_radius + radius_tolerance + search_margin), ordered by
        # internal accumulator score, not by distance to the clicked point
        # -- naively taking the first result could "snap" to a neighbour
        # instead of the one actually under the click.
        gray = np.full((200, 200), 40, dtype=np.uint8)
        # center=(100, 100), expected_radius=20, default radius_tolerance=6,
        # default search_margin=40 -> half=66 -> ROI top-left (x1, y1) =
        # (34, 34) -> ROI-local expected center = (66, 66).
        far_candidate = [10.0, 12.0, 20.0]  # full-image (44, 46) -- far from (100, 100)
        near_candidate = [64.0, 68.0, 20.0]  # full-image (98, 102) -- near (100, 100)
        fake_raw = np.array([[far_candidate, near_candidate]], dtype=np.float32)

        with mock.patch('vision.circle_detector.cv2.HoughCircles', return_value=fake_raw):
            found = detect_circle_in_roi(gray, center=(100.0, 100.0), expected_radius=20.0)

        self.assertIsNotNone(found)
        x, y, r = found
        self.assertAlmostEqual(x, 98.0)
        self.assertAlmostEqual(y, 102.0)
        self.assertAlmostEqual(r, 20.0)


class PreprocessForCircleSearchTests(unittest.TestCase):
    def test_runs_without_error_and_preserves_shape(self):
        gray = _synthetic_circle_frame()

        processed = preprocess_for_circle_search(gray)

        self.assertEqual(processed.shape, gray.shape)
        self.assertEqual(processed.dtype, gray.dtype)


class LoadHoughParamsTests(unittest.TestCase):
    def test_round_trip_maps_sibling_json_shape(self):
        payload = {
            'preprocessing': {'clahe_clip': 3.0, 'clahe_tile': 6, 'bilateral_d': 7, 'bilateral_sigma': 60.0},
            'hough': {'param1': 45.0, 'param2_roi': 18.0, 'min_radius': 12.0, 'max_radius': 28.0},
        }
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            params = load_hough_params(path)
        finally:
            os.unlink(path)

        self.assertEqual(params['clahe_clip'], 3.0)
        self.assertEqual(params['clahe_tile'], 6)
        self.assertEqual(params['bilateral_d'], 7)
        self.assertEqual(params['bilateral_sigma'], 60.0)
        self.assertEqual(params['param1'], 45.0)
        self.assertEqual(params['param2'], 18.0)
        self.assertEqual(params['expected_radius'], 20.0)
        self.assertEqual(params['radius_tolerance'], 8.0)

    def test_missing_keys_fall_back_to_defaults(self):
        payload = {}
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            params = load_hough_params(path)
        finally:
            os.unlink(path)

        self.assertEqual(params['clahe_clip'], 2.0)
        self.assertEqual(params['param1'], 50.0)

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            load_hough_params('/nonexistent/path/to/params.json')


if __name__ == '__main__':
    unittest.main()
