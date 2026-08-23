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

import numpy as np

from vision.layout_alignment import (
    Circle,
    LayoutModel,
    apply_manual_nudge,
    estimate_proj_radius,
    fit_layout_affine,
    project_all_circles,
)


class LayoutModelJsonTests(unittest.TestCase):
    def test_from_json_round_trip(self):
        data = {
            "positions": [[0, 0], [60, 0], [0, 60], [60, 60]],
            "radii": [8, 8, 8, 8],
            "count": 4,
            "notes": "test",
        }
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump(data, f)
            layout = LayoutModel.from_json(path)
        finally:
            os.remove(path)
        self.assertEqual(layout.n, 4)
        np.testing.assert_allclose(layout.template, np.array(data["positions"], dtype=np.float32))
        np.testing.assert_allclose(layout.template_radii, np.array(data["radii"], dtype=np.float32))

    def test_from_json_without_radii(self):
        data = {"positions": [[0, 0], [10, 0]], "count": 2}
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump(data, f)
            layout = LayoutModel.from_json(path)
        finally:
            os.remove(path)
        self.assertIsNone(layout.template_radii)


class FitLayoutAffineTests(unittest.TestCase):
    def setUp(self):
        self.template = np.array([[0, 0], [60, 0], [0, 60], [60, 60]], dtype=np.float32)
        self.layout = LayoutModel(self.template)

    def test_recovers_known_affine_transform(self):
        known_M = np.array([[0.0, -2.0, 100.0], [2.0, 0.0, 50.0]], dtype=np.float32)
        self.layout.transform = known_M
        img_pts = self.layout.project_all()
        self.layout.transform = None

        result = fit_layout_affine(self.layout, img_pts, list(range(4)), ransac_reproj_threshold=2.0)
        self.assertIsNotNone(result)
        matrix, _inliers = result
        np.testing.assert_allclose(matrix, known_M, atol=1e-3)
        # fit_layout_affine sets layout.transform as a side effect
        np.testing.assert_allclose(self.layout.transform, known_M, atol=1e-3)

    def test_fails_with_fewer_than_three_points(self):
        # fit_layout_affine now fits a full 6-DOF affine (shear +
        # independent x/y scale), which needs >=3 non-collinear
        # correspondences -- 2 points (4 equations) is no longer enough,
        # unlike the old 4-DOF similarity-only fit.
        result = fit_layout_affine(self.layout, [(0.0, 0.0), (1.0, 1.0)], [0, 1])
        self.assertIsNone(result)

    def test_recovers_known_shear_and_nonuniform_scale(self):
        known_M = np.array([[1.4, 0.35, 20.0], [-0.1, 0.8, -10.0]], dtype=np.float32)
        self.layout.transform = known_M
        img_pts = self.layout.project_all()
        self.layout.transform = None

        result = fit_layout_affine(self.layout, img_pts, list(range(4)), ransac_reproj_threshold=2.0)
        self.assertIsNotNone(result)
        matrix, _inliers = result
        np.testing.assert_allclose(matrix, known_M, atol=1e-3)

    def test_mismatched_lengths_fail(self):
        result = fit_layout_affine(self.layout, [(0.0, 0.0), (1.0, 1.0)], [0])
        self.assertIsNone(result)

    def test_fit_indexed_wraps_fit_layout_affine(self):
        known_M = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -3.0]], dtype=np.float32)
        self.layout.transform = known_M
        img_pts = self.layout.project_all()
        self.layout.transform = None
        ok = self.layout.fit_indexed(img_pts, [0, 1, 2, 3])
        self.assertTrue(ok)
        np.testing.assert_allclose(self.layout.transform, known_M, atol=1e-3)

    def test_inlier_mask_flags_outlier(self):
        # A full 6-DOF affine fit has much less redundancy per point than
        # the old 4-DOF similarity fit -- a 4-point set (barely more
        # equations than unknowns) lets RANSAC "explain away" a single bad
        # point by warping the fit to include it. Use a bigger point set (a
        # 3x3 grid) so there's enough redundancy for outlier rejection to
        # behave the same way it did before this function fit shear/
        # non-uniform scale too.
        template = np.array(
            [[x, y] for y in (0, 60, 120) for x in (0, 60, 120)], dtype=np.float32
        )
        layout = LayoutModel(template)
        known_M = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, -3.0]], dtype=np.float32)
        layout.transform = known_M
        img_pts = layout.project_all().copy()
        layout.transform = None
        # Corrupt one point badly so RANSAC should reject it as an outlier.
        img_pts[3] = img_pts[3] + np.array([500.0, 500.0], dtype=np.float32)

        result = fit_layout_affine(layout, img_pts, list(range(len(template))), ransac_reproj_threshold=2.0)
        self.assertIsNotNone(result)
        _matrix, inliers = result
        self.assertIsNotNone(inliers)
        self.assertEqual(int(inliers.ravel()[3]), 0)


class ManualNudgeTests(unittest.TestCase):
    def test_zero_nudge_equals_base_transform(self):
        base = np.array([[0.0, -2.0, 100.0], [2.0, 0.0, 50.0]], dtype=np.float32)
        nudged = apply_manual_nudge(
            base, scale_x_permille=0, scale_y_permille=0, angle_deg_x10=0, translate_x_px=0, translate_y_px=0, center=(100, 100),
        )
        np.testing.assert_allclose(nudged, base, atol=1e-4)

    def test_translate_only_nudge_shifts_offsets_exactly(self):
        base = np.array([[0.0, -2.0, 100.0], [2.0, 0.0, 50.0]], dtype=np.float32)
        nudged = apply_manual_nudge(
            base, scale_x_permille=0, scale_y_permille=0, angle_deg_x10=0, translate_x_px=15, translate_y_px=-7, center=(100, 100),
        )
        np.testing.assert_allclose(nudged[:, :2], base[:, :2], atol=1e-4)
        self.assertAlmostEqual(float(nudged[0, 2]), float(base[0, 2]) + 15, places=3)
        self.assertAlmostEqual(float(nudged[1, 2]), float(base[1, 2]) - 7, places=3)

    def test_nudge_does_not_mutate_base(self):
        base = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        base_copy = base.copy()
        apply_manual_nudge(
            base, scale_x_permille=25, scale_y_permille=25, angle_deg_x10=50, translate_x_px=5, translate_y_px=5, center=(0, 0),
        )
        np.testing.assert_array_equal(base, base_copy)

    def test_independent_x_y_scale_produces_anisotropic_result(self):
        # scale_x_permille != scale_y_permille must scale each axis
        # independently (angle=0 keeps the expected matrix easy to verify
        # directly): +10% in x, -10% in y, pivoting about the origin.
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        nudged = apply_manual_nudge(
            identity, scale_x_permille=100, scale_y_permille=-100, angle_deg_x10=0,
            translate_x_px=0, translate_y_px=0, center=(0, 0),
        )
        expected = np.array([[1.1, 0.0, 0.0], [0.0, 0.9, 0.0]], dtype=np.float32)
        np.testing.assert_allclose(nudged, expected, atol=1e-5)

    def test_zero_shear_matches_pre_shear_behavior(self):
        # Explicit shear_x_permille=0/shear_y_permille=0 must reproduce the
        # exact same output as calls that omit them entirely (the defaults),
        # for a nudge that also exercises scale/angle/translation together.
        base = np.array([[0.0, -2.0, 100.0], [2.0, 0.0, 50.0]], dtype=np.float32)
        without_shear_args = apply_manual_nudge(
            base, scale_x_permille=25, scale_y_permille=25, angle_deg_x10=50, translate_x_px=5, translate_y_px=5, center=(10, 20),
        )
        with_explicit_zero_shear = apply_manual_nudge(
            base, scale_x_permille=25, scale_y_permille=25, angle_deg_x10=50, translate_x_px=5, translate_y_px=5,
            shear_x_permille=0, shear_y_permille=0, center=(10, 20),
        )
        np.testing.assert_allclose(with_explicit_zero_shear, without_shear_args, atol=1e-5)

    def test_shear_applies_expected_matrix(self):
        # With an identity base transform, zero scale/rotation/translation,
        # and the pivot at the origin, the nudge should reduce to exactly
        # the shear matrix derived from shear_x_permille/shear_y_permille:
        # [[1, shear_x, 0], [shear_y, 1, 0]].
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        nudged = apply_manual_nudge(
            identity, scale_x_permille=0, scale_y_permille=0, angle_deg_x10=0, translate_x_px=0, translate_y_px=0,
            shear_x_permille=100, shear_y_permille=-50, center=(0, 0),
        )
        expected = np.array([[1.0, 0.1, 0.0], [-0.05, 1.0, 0.0]], dtype=np.float32)
        np.testing.assert_allclose(nudged, expected, atol=1e-5)

    def test_shear_pivots_about_center_leaving_it_fixed(self):
        # A point exactly at the pivot center should be unmoved by a
        # shear-only nudge (shear, like scale/rotation, pivots about center).
        identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
        center = (50.0, 50.0)
        nudged = apply_manual_nudge(
            identity, scale_x_permille=0, scale_y_permille=0, angle_deg_x10=0, translate_x_px=0, translate_y_px=0,
            shear_x_permille=100, shear_y_permille=100, center=center,
        )
        px = nudged[0, 0] * center[0] + nudged[0, 1] * center[1] + nudged[0, 2]
        py = nudged[1, 0] * center[0] + nudged[1, 1] * center[1] + nudged[1, 2]
        self.assertAlmostEqual(float(px), center[0], places=4)
        self.assertAlmostEqual(float(py), center[1], places=4)


class ProjectAllCirclesTests(unittest.TestCase):
    def test_projects_expected_count_and_center(self):
        template = np.array([[0, 0], [60, 0]], dtype=np.float32)
        radii = np.array([8.0, 8.0], dtype=np.float32)
        layout = LayoutModel(template, radii)
        layout.transform = np.array([[1.0, 0.0, 10.0], [0.0, 1.0, 20.0]], dtype=np.float32)

        circles = project_all_circles(layout)
        self.assertEqual(len(circles), 2)
        self.assertAlmostEqual(circles[0]["center"][0], 10.0, places=3)
        self.assertAlmostEqual(circles[0]["center"][1], 20.0, places=3)
        self.assertAlmostEqual(circles[1]["center"][0], 70.0, places=3)

    def test_raises_without_transform(self):
        layout = LayoutModel(np.array([[0, 0]], dtype=np.float32))
        with self.assertRaises(RuntimeError):
            project_all_circles(layout)

    def test_radius_scales_with_transform(self):
        template = np.array([[0, 0], [10, 0]], dtype=np.float32)
        radii = np.array([5.0, 5.0], dtype=np.float32)
        layout = LayoutModel(template, radii)
        layout.transform = np.array([[3.0, 0.0, 0.0], [0.0, 3.0, 0.0]], dtype=np.float32)
        r = estimate_proj_radius(layout, 0, layout.transform)
        self.assertEqual(r, 15)


class CircleDataclassTests(unittest.TestCase):
    def test_smoothed_initializes_to_raw_position(self):
        c = Circle(x=10.0, y=20.0, radius=5.0)
        self.assertEqual(c.smoothed_x, 10.0)
        self.assertEqual(c.smoothed_y, 20.0)
        self.assertEqual(c.center, (10.0, 20.0))

    def test_update_smoothed_applies_ema(self):
        c = Circle(x=0.0, y=0.0, radius=5.0)
        c.update_smoothed(10.0, 10.0, alpha=0.5)
        self.assertAlmostEqual(c.smoothed_x, 5.0)
        self.assertAlmostEqual(c.smoothed_y, 5.0)


if __name__ == "__main__":
    unittest.main()
