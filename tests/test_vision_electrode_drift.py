# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import cv2
import numpy as np

from vision.layout_alignment import Circle, LayoutModel
from vision.electrode_drift import (
    detect_and_refit_frame,
    detect_circle_in_roi,
    refit_and_extrapolate,
)


def _grid_layout(rows=3, cols=3, spacing=60.0):
    template = np.array(
        [[x * spacing, y * spacing] for y in range(rows) for x in range(cols)],
        dtype=np.float32,
    )
    return LayoutModel(template)


def _render_circles(shape, positions, radius=15, value=200, background=30):
    frame = np.full(shape + (3,), background, dtype=np.uint8)
    for px, py in positions:
        cv2.circle(frame, (int(px), int(py)), radius, (value, value, value), -1)
    # A touch of blur mimics a real camera's edge softness -- Hough's vote
    # accumulator is tuned for that, not a perfectly hard synthetic edge.
    return cv2.GaussianBlur(frame, (5, 5), 0)


class DetectCircleInRoiTests(unittest.TestCase):
    def test_finds_circle_near_expected_center(self):
        gray = np.full((200, 200), 30, dtype=np.uint8)
        cv2.circle(gray, (100, 100), 20, 220, -1)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        circle = detect_circle_in_roi(gray, (98, 102), 20, search_margin=20, radius_tolerance=6)
        self.assertIsNotNone(circle)
        self.assertAlmostEqual(circle.x, 100.0, delta=3.0)
        self.assertAlmostEqual(circle.y, 100.0, delta=3.0)
        self.assertEqual(circle.confidence, 1.0)
        self.assertTrue(circle.detected)

    def test_returns_none_on_empty_roi(self):
        gray = np.full((200, 200), 30, dtype=np.uint8)
        circle = detect_circle_in_roi(gray, (100, 100), 20, search_margin=20)
        self.assertIsNone(circle)


class RefitAndExtrapolateTests(unittest.TestCase):
    def setUp(self):
        self.layout = _grid_layout()
        self.true_M = np.array([[1.0, 0.0, 50.0], [0.0, 1.0, 50.0]], dtype=np.float32)
        self.layout.transform = self.true_M
        self.true_px = self.layout.project_all()

    def test_detected_circles_keep_measured_position(self):
        tracked = []
        for i, (px, py) in enumerate(self.true_px):
            c = Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            c.detected = True  # simulate a fresh Hough hit this cycle
            tracked.append(c)

        result = refit_and_extrapolate(self.layout, tracked, (400, 400), min_confident=2)
        self.assertTrue(result.refit_performed)
        for i, c in enumerate(tracked):
            self.assertAlmostEqual(c.smoothed_x, float(self.true_px[i][0]), places=3)
            self.assertAlmostEqual(c.smoothed_y, float(self.true_px[i][1]), places=3)

    def test_undetected_circles_hard_snap_not_ema(self):
        tracked = []
        for i, (px, py) in enumerate(self.true_px):
            # Seed every circle at a WRONG position, but mark all but one as
            # detected at the TRUE position so a clean refit is possible.
            c = Circle(x=float(px) + 999, y=float(py) + 999, radius=15.0, layout_index=i, on_image=True)
            tracked.append(c)

        # Mark all but the last electrode as detected at their true (measured) position.
        for i in range(len(tracked) - 1):
            tracked[i].x = tracked[i].smoothed_x = float(self.true_px[i][0])
            tracked[i].y = tracked[i].smoothed_y = float(self.true_px[i][1])
            tracked[i].detected = True

        last = tracked[-1]
        last.detected = False  # stays at the wrong seeded position pre-refit

        result = refit_and_extrapolate(self.layout, tracked, (400, 400), min_confident=2)
        self.assertTrue(result.refit_performed)
        # The undetected electrode should be hard-snapped to the freshly
        # projected geometry, not left at its stale seed nor EMA-blended.
        self.assertAlmostEqual(last.smoothed_x, float(self.true_px[-1][0]), places=1)
        self.assertAlmostEqual(last.smoothed_y, float(self.true_px[-1][1]), places=1)

    def test_refit_skipped_below_min_confident(self):
        tracked = []
        for i, (px, py) in enumerate(self.true_px):
            c = Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            tracked.append(c)
        tracked[0].detected = True  # only 1 confident detection

        result = refit_and_extrapolate(self.layout, tracked, (400, 400), min_confident=4)
        self.assertFalse(result.refit_performed)
        self.assertEqual(result.confident_count, 1)

    def test_inlier_fraction_surfaced_with_outlier(self):
        tracked = []
        for i, (px, py) in enumerate(self.true_px):
            c = Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            c.detected = True
            tracked.append(c)
        # Corrupt one detected point so it becomes a RANSAC outlier.
        tracked[-1].x = tracked[-1].smoothed_x = float(self.true_px[-1][0]) + 500.0

        result = refit_and_extrapolate(self.layout, tracked, (400, 400), min_confident=2)
        self.assertTrue(result.refit_performed)
        self.assertIsNotNone(result.inlier_fraction)
        self.assertLess(result.inlier_fraction, 1.0)


class DetectAndRefitFrameTests(unittest.TestCase):
    def setUp(self):
        self.layout = _grid_layout()
        self.true_M = np.array([[1.0, 0.0, 50.0], [0.0, 1.0, 50.0]], dtype=np.float32)
        self.layout.transform = self.true_M
        self.true_px = self.layout.project_all()
        self.frame = _render_circles((400, 400), self.true_px, radius=15)

    def test_recovers_from_drifted_seed(self):
        stale_M = self.true_M.copy()
        stale_M[0, 2] += 10.0
        self.layout.transform = stale_M
        stale_px = self.layout.project_all()
        tracked = [
            Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            for i, (px, py) in enumerate(stale_px)
        ]

        result = detect_and_refit_frame(
            self.frame, self.layout, tracked, min_confident=2, search_margin=25, param2_roi=15
        )
        self.assertTrue(result.refit_performed)
        # Hough center-finding on a filled synthetic disc has a real, if
        # modest, systematic imprecision -- this checks the pipeline
        # recovers roughly the true drifted-away position, not sub-pixel
        # accuracy (the whole design assumes approximate detection +
        # refit-based correction, not exact centers).
        for i, c in enumerate(tracked):
            self.assertTrue(c.detected)
            self.assertAlmostEqual(c.smoothed_x, float(self.true_px[i][0]), delta=10.0)
            self.assertAlmostEqual(c.smoothed_y, float(self.true_px[i][1]), delta=10.0)

    def test_off_image_circle_is_not_searched(self):
        tracked = [
            Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            for i, (px, py) in enumerate(self.true_px)
        ]
        tracked[0].on_image = False
        result = detect_and_refit_frame(
            self.frame, self.layout, tracked, min_confident=2, search_margin=25, param2_roi=15
        )
        self.assertTrue(result.refit_performed)
        self.assertFalse(tracked[0].detected)

    def test_excluded_layout_index_is_not_searched(self):
        # Mirrors an occluded electrode (e.g. under the probe arm) -- the
        # caller excludes it up front rather than letting a confused Hough
        # search run at all.
        tracked = [
            Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            for i, (px, py) in enumerate(self.true_px)
        ]
        result = detect_and_refit_frame(
            self.frame, self.layout, tracked, min_confident=2, search_margin=25, param2_roi=15,
            excluded_layout_indices={0},
        )
        self.assertTrue(result.refit_performed)
        self.assertFalse(tracked[0].detected)
        # Falls through to the same extrapolation snap an off-image circle
        # would get.
        self.assertAlmostEqual(tracked[0].smoothed_x, float(self.true_px[0][0]), delta=10.0)
        self.assertAlmostEqual(tracked[0].smoothed_y, float(self.true_px[0][1]), delta=10.0)

    def test_rejects_detection_too_far_from_prior_projection(self):
        # The reported bug this guards against: electrode 4's own tracked
        # seed position is corrupted to sit on top of electrode 5's true
        # position (one grid spacing away) -- its ROI search finds
        # electrode 5's real rendered circle instead of its own, exactly
        # mirroring "tracking drifts onto a neighboring electrode."
        tracked = [
            Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            for i, (px, py) in enumerate(self.true_px)
        ]
        tracked[4].x = tracked[4].smoothed_x = float(self.true_px[5][0])
        tracked[4].y = tracked[4].smoothed_y = float(self.true_px[5][1])

        result = detect_and_refit_frame(
            self.frame, self.layout, tracked, min_confident=2, search_margin=25, param2_roi=15,
            max_projected_deviation_radii=2.0,
        )
        self.assertTrue(result.refit_performed)
        self.assertFalse(tracked[4].detected)
        # Rejected -> falls through to the undetected-circle snap, landing
        # back at its OWN true projected position (from the refit, which
        # recovers close to the correct transform from the other 8
        # confidently-detected electrodes), not electrode 5's position.
        self.assertAlmostEqual(tracked[4].smoothed_x, float(self.true_px[4][0]), delta=5.0)
        self.assertAlmostEqual(tracked[4].smoothed_y, float(self.true_px[4][1]), delta=5.0)

    def test_max_projected_deviation_does_not_reject_genuine_drift_recovery(self):
        # A generous tolerance must not interfere with ordinary drift
        # recovery (the same scenario as test_recovers_from_drifted_seed).
        stale_M = self.true_M.copy()
        stale_M[0, 2] += 10.0
        self.layout.transform = stale_M
        stale_px = self.layout.project_all()
        tracked = [
            Circle(x=float(px), y=float(py), radius=15.0, layout_index=i, on_image=True)
            for i, (px, py) in enumerate(stale_px)
        ]

        result = detect_and_refit_frame(
            self.frame, self.layout, tracked, min_confident=2, search_margin=25, param2_roi=15,
            max_projected_deviation_radii=3.0,
        )
        self.assertTrue(result.refit_performed)
        for i, c in enumerate(tracked):
            self.assertTrue(c.detected)
            self.assertAlmostEqual(c.smoothed_x, float(self.true_px[i][0]), delta=10.0)
            self.assertAlmostEqual(c.smoothed_y, float(self.true_px[i][1]), delta=10.0)


if __name__ == "__main__":
    unittest.main()
