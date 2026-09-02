# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.electrode_mapper import (
    annotate_detections,
    detect_live_microscope_electrode_map_rgb,
    filter_live_overlay_detections,
)


def _load_image_rgb(path):
    image_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


class VisionElectrodeMapperTests(unittest.TestCase):
    def setUp(self):
        self.om_dir = PROJECT_DIR.parent / "OM"
        self.photo = self.om_dir / "photo.png"

    def test_detect_photo_map_rgb_finds_candidates(self):
        if not self.photo.exists():
            self.skipTest("photo.png not present in OM/")
        image_rgb = _load_image_rgb(self.photo)
        detections = detect_live_microscope_electrode_map_rgb(image_rgb, sample_side_mm=10.0, size_group="all")
        self.assertGreaterEqual(len(detections), 16)
        annotated = annotate_detections(image_rgb, detections)
        self.assertEqual(annotated.shape, image_rgb.shape)

    def test_live_microscope_preset_detects_candidates_on_saved_swift_snapshot(self):
        snapshot = (
            PROJECT_DIR
            / "results"
            / "camera_probe_20260421_172326"
            / "dshow_index0.png"
        )
        if not snapshot.exists():
            self.skipTest("Saved Swift/OpenCV snapshot not present in results/")

        image_rgb = _load_image_rgb(snapshot)
        detections = detect_live_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        self.assertGreaterEqual(len(detections), 12)
        self.assertGreaterEqual(detections["row_index"].nunique(), 3)

    def test_live_overlay_filter_reduces_saved_swift_snapshot_to_conservative_subset(self):
        snapshot = (
            PROJECT_DIR
            / "results"
            / "camera_probe_20260421_172326"
            / "dshow_index0.png"
        )
        if not snapshot.exists():
            self.skipTest("Saved Swift/OpenCV snapshot not present in results/")

        image_rgb = _load_image_rgb(snapshot)
        raw = detect_live_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        overlay = filter_live_overlay_detections(raw)
        self.assertLess(len(overlay), len(raw))
        self.assertLess(float(overlay["y_px"].max()), 250.0)

    def test_live_overlay_filter_can_honor_user_expected_visible_count(self):
        snapshot = (
            PROJECT_DIR
            / "results"
            / "camera_probe_20260421_172326"
            / "dshow_index0.png"
        )
        if not snapshot.exists():
            self.skipTest("Saved Swift/OpenCV snapshot not present in results/")

        image_rgb = _load_image_rgb(snapshot)
        raw = detect_live_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        overlay = filter_live_overlay_detections(raw, max_candidates=12)
        self.assertLessEqual(len(overlay), 12)

class AnnotateDetectionsColorTests(unittest.TestCase):
    def _detections_row(self, *, x, y, radius, size_group, detected=None):
        row = {
            "x_px": float(x),
            "y_px": float(y),
            "radius_px": float(radius),
            "row_index": 0,
            "col_index": 0,
            "size_group": size_group,
        }
        if detected is not None:
            row["detected"] = detected
        return row

    def test_undetected_row_is_drawn_gray_not_size_group_color(self):
        image_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        detections = pd.DataFrame([
            self._detections_row(x=30, y=50, radius=10, size_group="large", detected=True),
            self._detections_row(x=70, y=50, radius=10, size_group="large", detected=False),
        ])

        annotated = annotate_detections(image_rgb, detections, annotate_labels=False)

        detected_pixel = annotated[50, 30 + 10]
        undetected_pixel = annotated[50, 70 + 10]
        np.testing.assert_array_equal(detected_pixel, [40, 220, 40])
        np.testing.assert_array_equal(undetected_pixel, [160, 160, 160])

    def test_missing_detected_column_defaults_to_size_group_color(self):
        image_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        detections = pd.DataFrame([
            self._detections_row(x=50, y=50, radius=10, size_group="small"),
        ])
        self.assertNotIn("detected", detections.columns)

        annotated = annotate_detections(image_rgb, detections, annotate_labels=False)

        pixel = annotated[50, 50 + 10]
        np.testing.assert_array_equal(pixel, [255, 160, 40])

    def test_occluded_row_is_drawn_magenta_overriding_detected_color(self):
        image_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        row = self._detections_row(x=30, y=50, radius=10, size_group="large", detected=True)
        row["occluded"] = True
        detections = pd.DataFrame([row])

        annotated = annotate_detections(image_rgb, detections, annotate_labels=False)

        pixel = annotated[50, 30 + 10]
        np.testing.assert_array_equal(pixel, [255, 0, 255])

    def test_missing_occluded_column_falls_back_to_detected_color(self):
        image_rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        detections = pd.DataFrame([
            self._detections_row(x=30, y=50, radius=10, size_group="large", detected=True),
        ])
        self.assertNotIn("occluded", detections.columns)

        annotated = annotate_detections(image_rgb, detections, annotate_labels=False)

        pixel = annotated[50, 30 + 10]
        np.testing.assert_array_equal(pixel, [40, 220, 40])


if __name__ == "__main__":
    unittest.main()
