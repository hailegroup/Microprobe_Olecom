# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.electrode_mapper import load_image_rgb
from vision.roi_verifier import extract_square_roi, verify_roi_revisit


class ROIVerifierTests(unittest.TestCase):
    def setUp(self):
        self.om_dir = PROJECT_DIR.parent / "OM"
        self.photo = load_image_rgb(self.om_dir / "photo.png")
        self.monitoring = load_image_rgb(self.om_dir / "monitoring during measurement.png")

    def test_extract_square_roi_returns_nonempty_crop(self):
        roi = extract_square_roi(
            self.monitoring,
            center_x_px=500,
            center_y_px=250,
            half_size_px=40,
        )
        self.assertEqual(roi.shape[0], 80)
        self.assertEqual(roi.shape[1], 80)
        self.assertGreater(float(np.std(roi)), 0.0)

    def test_verify_roi_revisit_self_match_stays_near_zero_offset(self):
        result = verify_roi_revisit(
            self.monitoring,
            self.monitoring,
            expected_center_x_px=500,
            expected_center_y_px=250,
            half_size_px=40,
            search_radius_px=16,
        )
        self.assertGreater(result.score, 0.99)
        self.assertAlmostEqual(result.dx_px, 0.0, delta=1.0)
        self.assertAlmostEqual(result.dy_px, 0.0, delta=1.0)

    def test_verify_roi_revisit_recovers_small_stage_like_shift(self):
        dx, dy = 8, -6
        transform = np.float32([[1, 0, dx], [0, 1, dy]])
        shifted = cv2.warpAffine(
            cv2.cvtColor(self.monitoring, cv2.COLOR_RGB2BGR),
            transform,
            (self.monitoring.shape[1], self.monitoring.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        shifted_rgb = cv2.cvtColor(shifted, cv2.COLOR_BGR2RGB)
        result = verify_roi_revisit(
            self.monitoring,
            shifted_rgb,
            expected_center_x_px=500,
            expected_center_y_px=250,
            half_size_px=40,
            search_radius_px=16,
        )
        self.assertGreater(result.score, 0.85)
        self.assertAlmostEqual(result.dx_px, dx, delta=1.5)
        self.assertAlmostEqual(result.dy_px, dy, delta=1.5)
