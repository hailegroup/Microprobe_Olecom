# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision.electrode_mapper import (
    annotate_detections,
    compute_ecc_affine_registration,
    detect_electrode_map,
    detect_electrode_map_rgb,
    detect_live_microscope_electrode_map_rgb,
    detect_markup_electrode_map_rgb,
    detect_reference_microscope_electrode_map_rgb,
    filter_live_overlay_detections,
    load_image_rgb,
    save_detection_outputs,
    scale_detection_table,
    strip_red_markup_from_rgb,
    transform_detection_table,
)


class VisionElectrodeMapperTests(unittest.TestCase):
    def setUp(self):
        self.om_dir = PROJECT_DIR.parent / "OM"
        self.design = self.om_dir / "Design.png"
        self.photo = self.om_dir / "photo.png"

    def test_detect_design_map_finds_expected_grid(self):
        detections = detect_electrode_map(self.design, sample_side_mm=10.0, size_group="all")
        self.assertGreaterEqual(len(detections), 30)
        self.assertGreaterEqual(detections["row_index"].nunique(), 4)
        self.assertIn("large", set(detections["size_group"]))
        self.assertIn("small", set(detections["size_group"]))

    def test_detect_photo_map_finds_candidates_and_can_save_outputs(self):
        detections = detect_electrode_map(self.photo, sample_side_mm=10.0, size_group="all")
        self.assertGreaterEqual(len(detections), 16)
        annotated = annotate_detections(load_image_rgb(self.photo), detections)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path, overlay_path = save_detection_outputs(tmpdir, self.photo, detections, annotated)
            self.assertTrue(csv_path.exists())
            self.assertTrue(overlay_path.exists())
            loaded = cv2.imread(str(overlay_path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(loaded)

    def test_detect_photo_map_from_rgb_matches_path_detector_shape(self):
        image_rgb = load_image_rgb(self.photo)
        by_path = detect_electrode_map(self.photo, sample_side_mm=10.0, size_group="all")
        by_rgb = detect_electrode_map_rgb(image_rgb, sample_side_mm=10.0, size_group="all")
        self.assertGreaterEqual(len(by_rgb), 16)
        self.assertEqual(set(by_path.columns), set(by_rgb.columns))

    def test_live_microscope_preset_detects_candidates_on_saved_swift_snapshot(self):
        snapshot = (
            PROJECT_DIR
            / "results"
            / "camera_probe_20260421_172326"
            / "dshow_index0.png"
        )
        if not snapshot.exists():
            self.skipTest("Saved Swift/OpenCV snapshot not present in results/")

        image_rgb = load_image_rgb(snapshot)
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

        image_rgb = load_image_rgb(snapshot)
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

        image_rgb = load_image_rgb(snapshot)
        raw = detect_live_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        overlay = filter_live_overlay_detections(raw, max_candidates=12)
        self.assertLessEqual(len(overlay), 12)

    def test_reference_microscope_preset_reduces_monitoring_image_overdetection(self):
        monitoring = self.om_dir / "monitoring during measurement.png"
        if not monitoring.exists():
            self.skipTest("monitoring during measurement image not present in OM/")

        image_rgb = load_image_rgb(monitoring)
        live = detect_live_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        reference = detect_reference_microscope_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        self.assertGreaterEqual(len(reference), 10)
        self.assertLess(len(reference), len(live))
        self.assertGreaterEqual(reference["row_index"].nunique(), 2)
        self.assertLess(reference["row_index"].nunique(), 8)
        self.assertGreater(float(reference["y_px"].min()), image_rgb.shape[0] * 0.2)

    def test_manual_markup_snapshot_parses_all_18_marked_electrodes(self):
        markup = self.om_dir / "Swift_snapshot_markup.png"
        if not markup.exists():
            self.skipTest("Manual markup snapshot not present in OM/")

        image_rgb = load_image_rgb(markup)
        detections = detect_markup_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        self.assertEqual(len(detections), 18)
        self.assertEqual(detections["row_index"].nunique(), 3)
        self.assertGreaterEqual(detections["col_index"].max(), 6)

    def test_markup_cleanup_and_scaling_preserve_tracking_seed_structure(self):
        markup = self.om_dir / "Swift_snapshot_markup.png"
        if not markup.exists():
            self.skipTest("Manual markup snapshot not present in OM/")

        image_rgb = load_image_rgb(markup)
        detections = detect_markup_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        cleaned = strip_red_markup_from_rgb(image_rgb)
        self.assertEqual(cleaned.shape, image_rgb.shape)
        self.assertGreater(float(np.mean(np.abs(cleaned.astype(np.float32) - image_rgb.astype(np.float32)))), 1.0)

        scaled = scale_detection_table(
            detections,
            source_shape=image_rgb.shape[:2],
            target_shape=(image_rgb.shape[0] // 2, image_rgb.shape[1] // 2),
        )
        self.assertEqual(len(scaled), len(detections))
        self.assertAlmostEqual(
            float(scaled["x_px"].iloc[0]),
            float(detections["x_px"].iloc[0]) * 0.5,
            delta=1.5,
        )
        self.assertAlmostEqual(
            float(scaled["radius_px"].median()),
            float(detections["radius_px"].median()) * 0.5,
            delta=1.5,
        )

    def test_affine_registration_tracks_shifted_markup_layout(self):
        markup = self.om_dir / "Swift_snapshot_markup.png"
        if not markup.exists():
            self.skipTest("Manual markup snapshot not present in OM/")

        image_rgb = load_image_rgb(markup)
        detections = detect_markup_electrode_map_rgb(
            image_rgb,
            sample_side_mm=10.0,
            size_group="all",
        )
        shifted = cv2.warpAffine(
            cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR),
            np.array([[1.0, 0.0, 12.0], [0.0, 1.0, -9.0]], dtype=np.float32),
            (image_rgb.shape[1], image_rgb.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        shifted_rgb = cv2.cvtColor(shifted, cv2.COLOR_BGR2RGB)

        transform, cc = compute_ecc_affine_registration(image_rgb, shifted_rgb)
        projected = transform_detection_table(detections, transform)
        self.assertGreater(cc, 0.85)
        delta_x = projected["x_px"].to_numpy() - detections["x_px"].to_numpy()
        delta_y = projected["y_px"].to_numpy() - detections["y_px"].to_numpy()
        self.assertAlmostEqual(float(np.median(delta_x)), 12.0, delta=3.0)
        self.assertAlmostEqual(float(np.median(delta_y)), -9.0, delta=3.0)
