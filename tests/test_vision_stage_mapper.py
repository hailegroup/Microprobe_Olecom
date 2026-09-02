# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from vision_stage_mapper import (
    PixelStageReference,
    ProbeXYBiasCalibration,
    ProbeZParallaxCalibration,
    correct_pixel_for_xy_bias,
    correct_pixel_for_z_parallax,
    generate_bilinear_grid_pixels,
    map_pixels_to_stage_xy,
    parallax_within_trusted_range,
    pixel_to_stage_xy,
    solve_parallax_slope_from_samples,
    solve_stage_affine_calibration,
    solve_xy_bias_from_samples,
    stage_to_pixel_xy,
)


class VisionStageMapperTests(unittest.TestCase):
    def test_affine_calibration_maps_pixels_to_stage(self):
        refs = [
            PixelStageReference(0, 0, 10, 20),
            PixelStageReference(100, 0, 20, 20),
            PixelStageReference(0, 200, 10, 40),
        ]
        calibration = solve_stage_affine_calibration(refs)
        stage_x, stage_y = pixel_to_stage_xy(calibration, 50, 100)
        self.assertAlmostEqual(stage_x, 15.0, places=6)
        self.assertAlmostEqual(stage_y, 30.0, places=6)

    def test_bilinear_grid_generation(self):
        pixels = generate_bilinear_grid_pixels(
            top_left=(0, 0),
            top_right=(10, 0),
            bottom_left=(0, 20),
            bottom_right=(10, 20),
            rows=3,
            cols=2,
        )
        self.assertEqual(
            pixels,
            [
                (0.0, 0.0),
                (10.0, 0.0),
                (0.0, 10.0),
                (10.0, 10.0),
                (0.0, 20.0),
                (10.0, 20.0),
            ],
        )

    def test_map_pixels_to_stage_batch(self):
        refs = [
            PixelStageReference(0, 0, 0, 0),
            PixelStageReference(10, 0, 1, 0),
            PixelStageReference(0, 10, 0, 2),
        ]
        calibration = solve_stage_affine_calibration(refs)
        mapped = map_pixels_to_stage_xy(calibration, [(0, 0), (10, 10)])
        self.assertEqual(len(mapped), 2)
        self.assertAlmostEqual(mapped[1][0], 1.0, places=6)
        self.assertAlmostEqual(mapped[1][1], 2.0, places=6)


class ParallaxSlopeTests(unittest.TestCase):
    def test_solve_recovers_known_slope(self):
        true_du = 3.2
        true_dv = -1.1
        samples = [
            (0.10, 0.10 * true_du, 0.10 * true_dv),
            (0.25, 0.25 * true_du, 0.25 * true_dv),
            (0.40, 0.40 * true_du, 0.40 * true_dv),
            (0.55, 0.55 * true_du, 0.55 * true_dv),
        ]
        parallax = solve_parallax_slope_from_samples(samples)
        self.assertAlmostEqual(parallax.du_per_mm, true_du, places=6)
        self.assertAlmostEqual(parallax.dv_per_mm, true_dv, places=6)

    def test_solve_raises_with_fewer_than_three_samples(self):
        with self.assertRaises(ValueError):
            solve_parallax_slope_from_samples([(0.1, 0.3, -0.1), (0.2, 0.6, -0.2)])

    def test_solve_is_stable_on_tightly_clustered_delta_z(self):
        # Real-world case that broke the old intercept-fitted approach:
        # delta_z values clustered within a 0.02mm range produced an
        # implausible >100px/mm slope. The origin-constrained fit should
        # instead recover a small, physically plausible slope, since its
        # precision depends on how far delta_z sits from zero, not on the
        # spread across samples.
        samples = [
            (0.57, -9.0, -4.0),
            (0.55, -10.0, -2.0),
            (0.55, -7.0, -1.0),
            (0.56, -6.0, -3.0),
        ]
        parallax = solve_parallax_slope_from_samples(samples)
        self.assertLess(abs(parallax.du_per_mm), 20.0)
        self.assertLess(abs(parallax.dv_per_mm), 20.0)

    def test_solve_forces_fit_through_the_origin(self):
        # A constant pixel offset unrelated to Z (i.e. a nonzero intercept
        # in an unconstrained fit) must NOT leak into the slope -- the fit
        # is defined to pass through (0, 0).
        true_du = 3.2
        true_dv = -1.1
        offset_px = 50.0
        samples = [
            (0.10, 0.10 * true_du + offset_px, 0.10 * true_dv),
            (0.25, 0.25 * true_du + offset_px, 0.25 * true_dv),
            (0.40, 0.40 * true_du + offset_px, 0.40 * true_dv),
        ]
        parallax = solve_parallax_slope_from_samples(samples)
        # With the constant offset present, the origin-constrained slope
        # is pulled away from true_du (expected -- the model has no
        # intercept term to absorb it), but dv (unaffected) still recovers
        # exactly.
        self.assertAlmostEqual(parallax.dv_per_mm, true_dv, places=6)
        self.assertNotAlmostEqual(parallax.du_per_mm, true_du, places=2)

    def test_solve_raises_when_all_delta_z_are_zero(self):
        samples = [(0.0, 3.0, -1.0), (0.0, 4.0, -2.0), (0.0, 5.0, -3.0)]
        with self.assertRaises(ValueError):
            solve_parallax_slope_from_samples(samples)

    def test_correct_pixel_is_identity_when_parallax_none(self):
        x, y = correct_pixel_for_z_parallax(None, 100.0, 200.0, 0.5)
        self.assertEqual((x, y), (100.0, 200.0))

    def test_correct_pixel_is_identity_when_delta_z_none(self):
        parallax = ProbeZParallaxCalibration(du_per_mm=3.0, dv_per_mm=-2.0)
        x, y = correct_pixel_for_z_parallax(parallax, 100.0, 200.0, None)
        self.assertEqual((x, y), (100.0, 200.0))

    def test_correct_pixel_subtracts_slope_times_delta_z(self):
        parallax = ProbeZParallaxCalibration(du_per_mm=3.0, dv_per_mm=-2.0)
        x, y = correct_pixel_for_z_parallax(parallax, 100.0, 200.0, 0.5)
        self.assertAlmostEqual(x, 100.0 - 3.0 * 0.5, places=6)
        self.assertAlmostEqual(y, 200.0 - (-2.0) * 0.5, places=6)

    def test_within_trusted_range_true_when_inputs_none(self):
        parallax = ProbeZParallaxCalibration(du_per_mm=3.0, dv_per_mm=-2.0)
        self.assertTrue(parallax_within_trusted_range(None, 5.0, 1.0))
        self.assertTrue(parallax_within_trusted_range(parallax, None, 1.0))
        self.assertTrue(parallax_within_trusted_range(parallax, 5.0, None))

    def test_within_trusted_range_gates_on_magnitude(self):
        parallax = ProbeZParallaxCalibration(du_per_mm=3.0, dv_per_mm=-2.0)
        self.assertTrue(parallax_within_trusted_range(parallax, 0.9, 1.0))
        self.assertTrue(parallax_within_trusted_range(parallax, -1.0, 1.0))
        self.assertFalse(parallax_within_trusted_range(parallax, 1.1, 1.0))
        self.assertFalse(parallax_within_trusted_range(parallax, -1.1, 1.0))


class XYBiasTests(unittest.TestCase):
    def test_solve_recovers_constant_bias(self):
        samples = [(0.5, -0.3), (0.4, -0.2), (0.6, -0.4)]
        bias = solve_xy_bias_from_samples(samples)
        self.assertAlmostEqual(bias.bias_x_px, 0.5, places=6)
        self.assertAlmostEqual(bias.bias_y_px, -0.3, places=6)

    def test_solve_uses_median_not_mean_for_outlier_robustness(self):
        # A single wild outlier (e.g. one bad ROI detection) should barely
        # move the fit, unlike a mean which would be dragged toward it.
        samples = [(1.0, 1.0), (1.1, 0.9), (1.05, 1.05), (50.0, -50.0)]
        bias = solve_xy_bias_from_samples(samples)
        self.assertAlmostEqual(bias.bias_x_px, 1.075, places=6)
        self.assertAlmostEqual(bias.bias_y_px, 0.95, places=6)

    def test_solve_raises_with_fewer_than_three_samples(self):
        with self.assertRaises(ValueError):
            solve_xy_bias_from_samples([(0.1, 0.2), (0.3, 0.4)])

    def test_correct_pixel_is_identity_when_bias_none(self):
        x, y = correct_pixel_for_xy_bias(None, 100.0, 200.0)
        self.assertEqual((x, y), (100.0, 200.0))

    def test_correct_pixel_subtracts_bias(self):
        # Same sign convention as correct_pixel_for_z_parallax (which also
        # subtracts) -- see solve_xy_bias_from_samples/
        # correct_pixel_for_xy_bias docstrings for why: bias is how far
        # the probe's true position has drifted (probe-arm thermal
        # expansion) from where the stale calibration would aim it, so a
        # future aim must be shifted the opposite way to cancel that
        # drift, not compound it.
        bias = ProbeXYBiasCalibration(bias_x_px=0.5, bias_y_px=-0.3)
        x, y = correct_pixel_for_xy_bias(bias, 100.0, 200.0)
        self.assertAlmostEqual(x, 99.5, places=6)
        self.assertAlmostEqual(y, 200.3, places=6)

    def test_bias_measured_at_touch_corrects_a_later_tracked_pixel_to_the_true_location(self):
        """
        End-to-end sign-convention check against a synthetic calibration.
        The calibration is fit from PROBE pixel<->stage pairs at a
        reference temperature, but the probe arm has since thermally
        expanded: at a fresh AutoContactZ touch, the probe's true current
        pixel position (what its own ROI detector finds) no longer
        matches what the (now-stale) calibration would predict for the
        stage position it was actually commanded to. That measured
        (observed - expected) pixel delta is the bias. Applying it to an
        unrelated electrode's own tracked pixel (electrodes don't move,
        so their tracked position stays correct) and projecting through
        the same stale calibration must shift the resulting stage target
        by exactly the *opposite* of the bias -- pre-compensating for the
        same drift the probe would otherwise suffer, so the probe's true
        landing position ends up at the electrode, not offset from it.
        """
        # Calibration: probe pixel (u, v) -> stage (u, v) directly (identity),
        # but the probe arm has thermally drifted 0.2px right / 0.1px up
        # out of what the calibration still assumes.
        calibration = solve_stage_affine_calibration([
            PixelStageReference(0, 0, 0, 0),
            PixelStageReference(1, 0, 1, 0),
            PixelStageReference(0, 1, 0, 1),
        ])
        true_touch_stage_xy = (5.0, 3.0)
        expected_px = stage_to_pixel_xy(calibration, *true_touch_stage_xy)  # (5.0, 3.0)
        observed_probe_px = (expected_px[0] + 0.2, expected_px[1] - 0.1)  # what the ROI search actually finds

        bias_sample = (
            observed_probe_px[0] - expected_px[0],
            observed_probe_px[1] - expected_px[1],
        )
        bias = solve_xy_bias_from_samples([bias_sample, bias_sample, bias_sample])
        self.assertAlmostEqual(bias.bias_x_px, 0.2, places=6)
        self.assertAlmostEqual(bias.bias_y_px, -0.1, places=6)

        # A different electrode, tracked at some other pixel, should have
        # its projected stage target shifted by exactly the opposite of
        # the bias -- pre-compensating for the same drift the probe would
        # otherwise suffer if aimed there uncorrected.
        electrode_tracked_px = (10.0, 20.0)
        corrected_px = correct_pixel_for_xy_bias(bias, *electrode_tracked_px)
        stage_xy = pixel_to_stage_xy(calibration, *corrected_px)
        self.assertAlmostEqual(stage_xy[0], electrode_tracked_px[0] - 0.2, places=6)
        self.assertAlmostEqual(stage_xy[1], electrode_tracked_px[1] + 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
