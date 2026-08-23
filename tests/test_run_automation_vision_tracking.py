# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import numpy as np
import pandas as pd

import run_automation
from measurement_sequence import SequenceResult
from vision_stage_mapper import PixelStageReference, ProbeZParallaxCalibration, pixel_to_stage_xy, solve_stage_affine_calibration
from vision.electrode_z_seed import ElectrodeZCalibrationStore
from vision.layout_alignment import Circle, LayoutModel


class _StubDevice:
    def connect(self):
        return None

    def disconnect(self):
        return None


class _StubMotor(_StubDevice):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.pos = {"X": x, "Y": y, "Z": z}
        self.moves_log = []

    def get_position(self, axis):
        return self.pos[axis]

    def move_abs_wait(self, axis, pos_mm, **kwargs):
        self.pos[axis] = pos_mm

    def move_xyz_safe(self, *, x_mm, y_mm, z_mm, current_positions=None, log_fn=print, **kwargs):
        moves = {}
        if x_mm is not None:
            self.pos["X"] = x_mm
            moves["X"] = x_mm
        if y_mm is not None:
            self.pos["Y"] = y_mm
            moves["Y"] = y_mm
        if z_mm is not None:
            self.pos["Z"] = z_mm
            moves["Z"] = z_mm
        self.moves_log.append(moves)
        return moves


def _fake_rapid_sequence(**kwargs):
    return SequenceResult(
        measurement_mode="rapid_eis",
        eis_data=np.array([[1.0, 10.0, 2.0]]),
        ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
        pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
    )


def _make_row(label, v_dc, x_mm, y_mm, auto_track_xy=1):
    return {
        "Label": label,
        "V_dc": v_dc,
        "X_mm": x_mm,
        "Y_mm": y_mm,
        "Z_mm": 0.0,
        "AutoTrackXY": auto_track_xy,
        "dV": 0.03,
        "HoldTime_s": 1,
        "PostPEIS_HoldTime_s": 1,
        "PEIS_fHigh": 1e5,
        "PEIS_fLow": 0.1,
        "PEIS_nPts": 10,
        "CA_duration_s": 1,
        "CA_dt": 0.1,
        "Skip": 0,
    }


class ExtractElectrodeIdTests(unittest.TestCase):
    def test_extracts_number(self):
        self.assertEqual(run_automation._extract_electrode_id("600C_E3_pO2-0p2_0p3V"), 3)
        self.assertEqual(run_automation._extract_electrode_id("E12"), 12)

    def test_returns_none_when_absent(self):
        self.assertIsNone(run_automation._extract_electrode_id("no_electrode_here"))


class RunConditionsAutoTrackXYTests(unittest.TestCase):
    def _run(self, df, vision_runtime, motor=None):
        motor = motor or _StubMotor()
        with tempfile.TemporaryDirectory() as tmpdir:
            run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                motor=motor,
                rapid_sequence=_fake_rapid_sequence,
                log_fn=lambda *a, **k: None,
                vision_runtime=vision_runtime,
            )
        return motor

    def test_vision_runtime_none_is_complete_noop(self):
        df = pd.DataFrame([_make_row("E1_test", 0.1, 1.0, 2.0)])
        motor = self._run(df, vision_runtime=None)
        self.assertEqual(motor.moves_log[0]["X"], 1.0)
        self.assertEqual(motor.moves_log[0]["Y"], 2.0)

    def test_auto_track_xy_zero_is_noop_even_with_runtime(self):
        df = pd.DataFrame([_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {0: (99.0, 99.0)},
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertEqual(motor.moves_log[0]["X"], 1.0)
        self.assertEqual(motor.moves_log[0]["Y"], 2.0)

    def test_empty_cache_falls_back_to_csv_until_correction_lands(self):
        df = pd.DataFrame([_make_row("E1_test", 0.1, 1.0, 2.0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {},
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertEqual(motor.moves_log[0]["X"], 1.0)
        self.assertEqual(motor.moves_log[0]["Y"], 2.0)

    def test_within_tolerance_correction_is_applied(self):
        df = pd.DataFrame([_make_row("E1_test", 0.1, 1.0, 2.0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {0: (1.05, 2.05)},  # layout_index = E1 - 1 = 0
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor.moves_log[0]["X"], 1.05)
        self.assertAlmostEqual(motor.moves_log[0]["Y"], 2.05)
        self.assertEqual(vision_runtime["trusted_electrode_positions"][0], (1.05, 2.05))

    def test_out_of_tolerance_correction_falls_back_to_csv(self):
        df = pd.DataFrame([_make_row("E2_test", 0.1, 5.0, 6.0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {1: (50.0, 60.0)},  # layout_index = E2 - 1 = 1, way off
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor.moves_log[0]["X"], 5.0)
        self.assertAlmostEqual(motor.moves_log[0]["Y"], 6.0)
        self.assertNotIn(1, vision_runtime["trusted_electrode_positions"])

    def test_no_electrode_id_in_label_falls_back_to_csv(self):
        df = pd.DataFrame([_make_row("no_electrode_here", 0.1, 9.0, 9.0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {0: (1.0, 1.0)},
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor.moves_log[0]["X"], 9.0)
        self.assertAlmostEqual(motor.moves_log[0]["Y"], 9.0)

    def test_second_touch_beyond_csv_bound_but_within_trusted_bound_is_applied(self):
        # First row establishes a trusted position 0.3 mm from CSV (within
        # the 0.5 mm bound, gated against CSV since nothing is trusted yet).
        df = pd.DataFrame([
            _make_row("E1_r1", 0.1, 1.0, 2.0),
            _make_row("E1_r2", 0.1, 1.0, 2.0),
        ])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {0: (1.3, 2.0)},
            "trusted_electrode_positions": {},
        }
        motor = self._run(df, vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor.moves_log[0]["X"], 1.3)
        self.assertEqual(vision_runtime["trusted_electrode_positions"][0], (1.3, 2.0))

        # Second row drifts a further 0.3 mm (0.6 mm total from the
        # original CSV value -- would fail a CSV-anchored gate -- but only
        # 0.3 mm from the just-trusted position, so it's accepted.
        vision_runtime["electrode_stage_cache"][0] = (1.6, 2.0)
        motor2 = self._run(df.iloc[[1]].reset_index(drop=True), vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor2.moves_log[0]["X"], 1.6)
        self.assertEqual(vision_runtime["trusted_electrode_positions"][0], (1.6, 2.0))

    def test_second_touch_glitch_near_csv_but_far_from_trusted_falls_back(self):
        df = pd.DataFrame([_make_row("E1_r1", 0.1, 1.0, 2.0)])
        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": None,
            "last_check_row_idx": 999999, "last_check_time": 1e18,
            "electrode_stage_cache": {0: (1.1, 2.0)},
            "trusted_electrode_positions": {},
        }
        self._run(df, vision_runtime=vision_runtime)
        self.assertEqual(vision_runtime["trusted_electrode_positions"][0], (1.1, 2.0))

        # A bad detection lands close to the ORIGINAL CSV value again but
        # far from the position just trusted -- must be rejected, and the
        # previously-trusted position must survive.
        df2 = pd.DataFrame([_make_row("E1_r2", 0.1, 1.0, 2.0)])
        vision_runtime["electrode_stage_cache"][0] = (0.95, 2.6)
        motor2 = self._run(df2, vision_runtime=vision_runtime)
        self.assertAlmostEqual(motor2.moves_log[0]["X"], 1.0)
        self.assertAlmostEqual(motor2.moves_log[0]["Y"], 2.0)
        self.assertEqual(vision_runtime["trusted_electrode_positions"][0], (1.1, 2.0))


class DriftCorrectionCadenceTests(unittest.TestCase):
    def test_refresh_skipped_when_not_due_by_row_or_time(self):
        calls = []

        class _FakeCap:
            def read(self):
                calls.append("read")
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": _FakeCap(),
            "last_check_row_idx": 0, "last_check_time": time.time(),
            "electrode_stage_cache": {},
        }
        # row 1 is within VISION_DRIFT_RECHECK_EVERY_N_ROWS of row 0 and time
        # just elapsed -- should NOT trigger a frame read.
        run_automation._maybe_refresh_vision_drift_correction(
            vision_runtime, row_idx=1, log_fn=lambda *a, **k: None
        )
        self.assertEqual(calls, [])

    def test_refresh_fires_on_first_call(self):
        calls = []

        class _FakeCap:
            def read(self):
                calls.append("read")
                return False, None  # fail fast, we're only checking that it was attempted

        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": _FakeCap(),
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {},
        }
        run_automation._maybe_refresh_vision_drift_correction(
            vision_runtime, row_idx=0, log_fn=lambda *a, **k: None
        )
        self.assertEqual(calls, ["read"])
        self.assertEqual(vision_runtime["last_check_row_idx"], 0)

    def test_refresh_fires_after_row_threshold(self):
        calls = []

        class _FakeCap:
            def read(self):
                calls.append("read")
                return False, None

        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": _FakeCap(),
            "last_check_row_idx": 0, "last_check_time": time.time(),
            "electrode_stage_cache": {},
        }
        # Default VISION_DRIFT_RECHECK_EVERY_N_ROWS is 5; row 5 should trigger.
        run_automation._maybe_refresh_vision_drift_correction(
            vision_runtime, row_idx=5, log_fn=lambda *a, **k: None
        )
        self.assertEqual(calls, ["read"])

    def test_refresh_does_not_exclude_probe_occluded_electrodes_from_detect_and_refit_frame(self):
        # _probe_occluded_layout_indices used to be wired in as
        # excluded_layout_indices, hard-skipping the Hough search entirely
        # for electrodes near the probe's current stage X -- that gate has
        # been removed (search is attempted everywhere every cycle now),
        # though the helper itself is kept for any future diagnostic use.
        class _FakeCap:
            def read(self):
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        vision_runtime = {
            "calibration": None, "layout": SimpleNamespace(n=30), "tracked": [], "cap": _FakeCap(),
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {0: (0.0, 0.0), 1: (100.0, 0.0)},
        }
        motor = mock.Mock()
        motor.get_position.return_value = 50.0  # would have excluded electrode 0 (x=0) under the old gate

        fake_result = mock.Mock(refit_performed=False, inlier_fraction=None, notes=[])
        with mock.patch(
            "vision.electrode_drift.detect_and_refit_frame", return_value=fake_result
        ) as mocked_detect:
            run_automation._maybe_refresh_vision_drift_correction(
                vision_runtime, row_idx=0, motor=motor, log_fn=lambda *a, **k: None
            )

        mocked_detect.assert_called_once()
        kwargs = mocked_detect.call_args.kwargs
        self.assertNotIn("excluded_layout_indices", kwargs)
        self.assertEqual(kwargs["ema_alpha"], run_automation.VISION_DRIFT_EMA_ALPHA)
        self.assertEqual(
            kwargs["max_projected_deviation_radii"],
            run_automation.VISION_DRIFT_MAX_PROJECTED_DEVIATION_RADII,
        )
        # 30 electrodes * 0.2 fraction = 6, which exceeds the flat floor (4)
        # -- confirms the proportional threshold actually takes over for a
        # large-enough layout instead of always just returning the floor.
        self.assertEqual(kwargs["min_confident"], 6)

    def test_refresh_min_confident_never_drops_below_flat_floor_for_small_layout(self):
        class _FakeCap:
            def read(self):
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        vision_runtime = {
            "calibration": None, "layout": SimpleNamespace(n=5), "tracked": [], "cap": _FakeCap(),
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {},
        }
        fake_result = mock.Mock(refit_performed=False, inlier_fraction=None, notes=[])
        with mock.patch(
            "vision.electrode_drift.detect_and_refit_frame", return_value=fake_result
        ) as mocked_detect:
            run_automation._maybe_refresh_vision_drift_correction(
                vision_runtime, row_idx=0, log_fn=lambda *a, **k: None
            )

        # 5 electrodes * 0.2 = 1, well below the flat floor of 4 -- must
        # still require the floor, not the smaller proportional value.
        self.assertEqual(mocked_detect.call_args.kwargs["min_confident"], 4)

    def test_camera_failure_does_not_raise_and_keeps_stale_cache(self):
        class _FailingCap:
            def read(self):
                raise RuntimeError("camera disconnected")

        vision_runtime = {
            "calibration": None, "layout": None, "tracked": [], "cap": _FailingCap(),
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {0: (1.0, 2.0)},
        }
        # Should not raise despite the camera erroring.
        run_automation._maybe_refresh_vision_drift_correction(
            vision_runtime, row_idx=0, log_fn=lambda *a, **k: None
        )
        self.assertEqual(vision_runtime["electrode_stage_cache"], {0: (1.0, 2.0)})


class ProbeOccludedLayoutIndicesTests(unittest.TestCase):
    def test_none_without_motor(self):
        vision_runtime = {"electrode_stage_cache": {0: (0.0, 0.0)}}
        self.assertIsNone(run_automation._probe_occluded_layout_indices(vision_runtime, None))

    def test_none_on_motor_failure(self):
        vision_runtime = {"electrode_stage_cache": {0: (0.0, 0.0)}}
        motor = mock.Mock()
        motor.get_position.side_effect = RuntimeError("disconnected")
        self.assertIsNone(run_automation._probe_occluded_layout_indices(vision_runtime, motor))

    def test_excludes_electrodes_left_of_probe_by_margin(self):
        vision_runtime = {
            "electrode_stage_cache": {0: (0.0, 0.0), 1: (10.0, 0.0), 2: (20.0, 0.0)},
        }
        motor = mock.Mock()
        motor.get_position.return_value = 15.0
        # Default margin 2.0mm -> threshold 13.0: electrodes 0 (x=0) and
        # 1 (x=10) fall below it and are excluded; electrode 2 (x=20) is not.
        excluded = run_automation._probe_occluded_layout_indices(vision_runtime, motor)
        self.assertEqual(excluded, {0, 1})

    def test_empty_cache_yields_empty_set(self):
        vision_runtime = {"electrode_stage_cache": {}}
        motor = mock.Mock()
        motor.get_position.return_value = 15.0
        self.assertEqual(run_automation._probe_occluded_layout_indices(vision_runtime, motor), set())


class DriftCorrectionZSeedCorrectionTests(unittest.TestCase):
    def _run_with_fake_detect(self, vision_runtime, result):
        class _FakeCap:
            def read(self):
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        vision_runtime["cap"] = _FakeCap()
        with mock.patch("vision.electrode_drift.detect_and_refit_frame", return_value=result):
            run_automation._maybe_refresh_vision_drift_correction(
                vision_runtime, row_idx=0, log_fn=lambda *a, **k: None
            )

    def _make_calibration(self):
        return solve_stage_affine_calibration([
            PixelStageReference(0, 0, 0.0, 0.0),
            PixelStageReference(100, 0, 10.0, 0.0),
            PixelStageReference(0, 100, 0.0, 10.0),
        ])

    def test_seed_correction_applied_when_available(self):
        circle = Circle(x=50.0, y=50.0, radius=8.0, layout_index=0)
        circle.smoothed_x, circle.smoothed_y = 50.0, 60.0
        calibration = self._make_calibration()
        z_store = ElectrodeZCalibrationStore()
        z_store.record_contact(0, 10.30, source="auto_contact")
        z_store.z_parallax = ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        vision_runtime = {
            "calibration": calibration, "z_ref_mm": 10.0, "layout": SimpleNamespace(n=1),
            "tracked": [circle], "cap": None,
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {}, "z_store": z_store,
        }
        result = SimpleNamespace(notes=["ok"], refit_performed=True, inlier_fraction=1.0)

        self._run_with_fake_detect(vision_runtime, result)

        delta_z = 10.30 - 10.0
        expected = pixel_to_stage_xy(calibration, 50.0 - 4.0 * delta_z, 60.0 - (-2.0) * delta_z)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][0][0], expected[0], places=6)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][0][1], expected[1], places=6)
        naive = pixel_to_stage_xy(calibration, 50.0, 60.0)
        self.assertFalse(
            abs(vision_runtime["electrode_stage_cache"][0][0] - naive[0]) < 1e-6
            and abs(vision_runtime["electrode_stage_cache"][0][1] - naive[1]) < 1e-6
        )

    def test_seed_correction_not_applied_without_seed(self):
        circle = Circle(x=50.0, y=50.0, radius=8.0, layout_index=0)
        circle.smoothed_x, circle.smoothed_y = 50.0, 60.0
        calibration = self._make_calibration()
        z_store = ElectrodeZCalibrationStore()
        z_store.z_parallax = ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)  # no seed for electrode 0
        vision_runtime = {
            "calibration": calibration, "z_ref_mm": 10.0, "layout": SimpleNamespace(n=1),
            "tracked": [circle], "cap": None,
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {}, "z_store": z_store,
        }
        result = SimpleNamespace(notes=["ok"], refit_performed=True, inlier_fraction=1.0)

        self._run_with_fake_detect(vision_runtime, result)

        expected = pixel_to_stage_xy(calibration, 50.0, 60.0)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][0][0], expected[0], places=6)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][0][1], expected[1], places=6)

    def test_z_plane_estimate_applied_for_unseeded_electrode(self):
        # Square layout: electrodes 0..3 at (0,0), (60,0), (0,60), (60,60).
        # Seed 3 of the 4 with a known plane Z = 10.0 + 0.01*u; electrode 3
        # (60, 60) is tracked but never individually contact-measured.
        layout = LayoutModel(
            np.array([[0, 0], [60, 0], [0, 60], [60, 60]], dtype=np.float32),
            np.array([8, 8, 8, 8], dtype=np.float32),
        )
        circle = Circle(x=50.0, y=50.0, radius=8.0, layout_index=3)
        circle.smoothed_x, circle.smoothed_y = 50.0, 60.0
        calibration = self._make_calibration()
        z_store = ElectrodeZCalibrationStore()
        z_store.record_contact(0, 10.0, source="auto_contact", xy_mm=(0.0, 0.0))
        z_store.record_contact(1, 10.6, source="auto_contact", xy_mm=(60.0, 0.0))
        z_store.record_contact(2, 10.0, source="auto_contact", xy_mm=(0.0, 60.0))
        self.assertIsNotNone(z_store.z_plane)
        z_store.z_parallax = ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        vision_runtime = {
            "calibration": calibration, "z_ref_mm": 10.0, "layout": layout,
            "tracked": [circle], "cap": None,
            "last_check_row_idx": None, "last_check_time": None,
            "electrode_stage_cache": {}, "z_store": z_store,
        }
        result = SimpleNamespace(notes=["ok"], refit_performed=True, inlier_fraction=1.0)

        self._run_with_fake_detect(vision_runtime, result)

        estimated_z = z_store.z_plane.evaluate((60.0, 60.0))
        self.assertAlmostEqual(estimated_z, 10.6, places=6)
        delta_z = estimated_z - 10.0
        expected = pixel_to_stage_xy(calibration, 50.0 - 4.0 * delta_z, 60.0 - (-2.0) * delta_z)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][3][0], expected[0], places=6)
        self.assertAlmostEqual(vision_runtime["electrode_stage_cache"][3][1], expected[1], places=6)


class VisionProbePixelSampleFnTests(unittest.TestCase):
    def test_none_vision_runtime_returns_none(self):
        self.assertIsNone(run_automation._vision_probe_pixel_sample_fn(None))

    def test_missing_cap_or_detector_returns_none(self):
        self.assertIsNone(run_automation._vision_probe_pixel_sample_fn({"cap": None, "probe_detector": object()}))
        self.assertIsNone(run_automation._vision_probe_pixel_sample_fn({"cap": object(), "probe_detector": None}))

    def test_sample_returns_detected_pixel(self):
        class _FakeTip:
            detected = True
            x = 12.5
            y = 34.5

        class _FakeDetector:
            def detect(self, frame_bgr):
                return _FakeTip()

        class _FakeCap:
            def read(self):
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        sample_fn = run_automation._vision_probe_pixel_sample_fn(
            {"cap": _FakeCap(), "probe_detector": _FakeDetector()}
        )
        self.assertEqual(sample_fn(), (12.5, 34.5))

    def test_sample_returns_none_on_detection_failure(self):
        class _FakeTip:
            detected = False

        class _FakeDetector:
            def detect(self, frame_bgr):
                return _FakeTip()

        class _FakeCap:
            def read(self):
                return True, np.zeros((10, 10, 3), dtype=np.uint8)

        sample_fn = run_automation._vision_probe_pixel_sample_fn(
            {"cap": _FakeCap(), "probe_detector": _FakeDetector()}
        )
        self.assertIsNone(sample_fn())


class RecordElectrodeZContactTests(unittest.TestCase):
    def test_none_store_is_noop(self):
        run_automation._record_electrode_z_contact(
            None, {"Label": "E1_x"}, 12.0, None, log_fn=lambda *a, **k: None
        )

    def test_updates_seed_for_row_with_electrode_id(self):
        z_store = ElectrodeZCalibrationStore()
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "E3_test"}, 12.34, None, log_fn=lambda *a, **k: None
            )
        self.assertAlmostEqual(z_store.get_seed(2), 12.34, places=6)

    def test_passes_layout_xy_mm_when_layout_given(self):
        layout = LayoutModel(
            np.array([[0, 0], [60, 0], [0, 60], [60, 60]], dtype=np.float32),
            np.array([8, 8, 8, 8], dtype=np.float32),
        )
        z_store = ElectrodeZCalibrationStore()
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "E2_test"}, 12.34, None, layout=layout, log_fn=lambda *a, **k: None
            )
        # E2 -> layout_index 1 -> template position (60, 0)
        self.assertEqual(z_store.electrode_z_seeds[1].xy_mm, (60.0, 0.0))

    def test_no_layout_leaves_xy_mm_none(self):
        z_store = ElectrodeZCalibrationStore()
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "E1_test"}, 12.34, None, log_fn=lambda *a, **k: None
            )
        self.assertIsNone(z_store.electrode_z_seeds[0].xy_mm)

    def test_skips_row_without_electrode_id(self):
        z_store = ElectrodeZCalibrationStore()
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "no_electrode"}, 12.34, None, log_fn=lambda *a, **k: None
            )
        self.assertEqual(z_store.electrode_z_seeds, {})

    def test_appends_parallax_sample_when_provided(self):
        z_store = ElectrodeZCalibrationStore()
        parallax_sample = (12.0, (0.0, 0.0), 12.3, (6.0, -3.0))
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "E1_test"}, 12.35, parallax_sample, log_fn=lambda *a, **k: None
            )
        self.assertEqual(len(z_store.parallax_samples), 1)

    def test_log_message_is_1_indexed(self):
        # E3 -> layout_index=2 (0-based, internal) -- the log must read
        # "electrode #3", not the raw 0-based "#2".
        z_store = ElectrodeZCalibrationStore()
        logged = []
        with mock.patch.object(z_store, "save"):
            run_automation._record_electrode_z_contact(
                z_store, {"Label": "E3_test"}, 12.34, None, log_fn=logged.append
            )
        self.assertTrue(any("electrode #3" in msg for msg in logged), logged)


class RunConditionsAutoContactZSeedTests(unittest.TestCase):
    def test_auto_contact_z_seeds_persisted_store(self):
        df = pd.DataFrame([{**_make_row("E4_test", 0.1, 1.0, 2.0, auto_track_xy=0), "AutoContactZ": 1}])
        motor = _StubMotor()
        fake_parallax_sample = (12.0, (0.0, 0.0), 12.3, (6.0, -3.0))
        with tempfile.TemporaryDirectory() as tmpdir:
            z_seed_path = os.path.join(tmpdir, "z_seed.json")
            with mock.patch.object(run_automation, "find_contact_z", return_value=(12.3, fake_parallax_sample)), \
                 mock.patch.object(run_automation, "VISION_ELECTRODE_Z_SEED_PATH", z_seed_path):
                run_automation._run_conditions(
                    df,
                    result_root=tmpdir,
                    bl=_StubDevice(),
                    motor=motor,
                    rapid_sequence=_fake_rapid_sequence,
                    log_fn=lambda *a, **k: None,
                    vision_runtime=None,
                )
            self.assertTrue(os.path.exists(z_seed_path))
            loaded = ElectrodeZCalibrationStore.load(z_seed_path)
        # E4 -> layout_index 3
        self.assertAlmostEqual(loaded.get_seed(3), 12.3, places=6)
        self.assertEqual(len(loaded.parallax_samples), 1)

    def test_auto_contact_z_collect_anyway_on_unconfirmed_contact(self):
        # Contact search never confirms OCV -- PEIS must still run (at the
        # search's last-probed Z), the row must be flagged
        # ContactConfirmed=False, and the Z-seed/parallax store must NOT
        # be updated with an unconfirmed height.
        df = pd.DataFrame([{**_make_row("E4_test", 0.1, 1.0, 2.0, auto_track_xy=0), "AutoContactZ": 1}])
        motor = _StubMotor()
        with tempfile.TemporaryDirectory() as tmpdir:
            z_seed_path = os.path.join(tmpdir, "z_seed.json")
            with mock.patch.object(
                run_automation, "find_contact_z",
                side_effect=run_automation.ContactNotConfirmedError(
                    "AutoContactZ did not find a valid OCV threshold", last_z=11.7,
                ),
            ), mock.patch.object(run_automation, "VISION_ELECTRODE_Z_SEED_PATH", z_seed_path):
                results = run_automation._run_conditions(
                    df,
                    result_root=tmpdir,
                    bl=_StubDevice(),
                    motor=motor,
                    rapid_sequence=_fake_rapid_sequence,
                    log_fn=lambda *a, **k: None,
                    vision_runtime=None,
                )
            # PEIS still ran -- a result row was produced, not skipped/aborted.
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["Status"], "OK")
            self.assertEqual(results[0]["ContactConfirmed"], False)
            # No Z seed was persisted for the unconfirmed contact.
            self.assertFalse(os.path.exists(z_seed_path))


class AutoContactZPreMoveTests(unittest.TestCase):
    """
    The row-loop's initial move-to-electrode must target the AutoContactZ
    search's own start height (seed +/- ContactStartOffset_mm), never the
    raw seed Z directly -- moving to the seed first would send the tip
    down to/through the electrode surface at full move speed before
    find_contact_z's careful step-wise approach even begins.
    """

    def test_initial_move_targets_search_start_not_raw_seed(self):
        # positive_z_is_up=False in this project's config.py -> approach_sign
        # = +1, so start_z = base_z - ContactStartOffset_mm (default 0.200).
        df = pd.DataFrame([{**_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0), "Z_mm": 10.0, "AutoContactZ": 1}])
        motor = _StubMotor()
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(run_automation, "find_contact_z", return_value=(10.05, None)):
            run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                motor=motor,
                rapid_sequence=_fake_rapid_sequence,
                log_fn=lambda *a, **k: None,
                vision_runtime=None,
            )
        self.assertEqual(len(motor.moves_log), 1)
        self.assertAlmostEqual(motor.moves_log[0]["Z"], 9.800, places=6)

    def test_exact_cache_hit_moves_directly_to_cached_contact_z(self):
        # Row 1 and row 3 share an XY (and no temp/gas) -- an exact-contact
        # cache key. Row 2 sits at a different XY in between, so returning
        # to row 3 is a genuine move (the plain seed-cache substitution
        # alone would otherwise make row 3 look like "position unchanged"
        # vs. row 1 and skip the move block entirely, defeating the test).
        # Row 3's initial move must go straight to row 1's confirmed
        # contact height, not hover at the search-start offset with no
        # search left to run and bring it down.
        row1 = {**_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0), "Z_mm": 10.0, "AutoContactZ": 1, "Label": "E1_a"}
        row2 = {**_make_row("E2_test", 0.1, 5.0, 6.0, auto_track_xy=0), "Z_mm": 3.0, "AutoContactZ": 0, "Label": "E2_spacer"}
        row3 = {**_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0), "Z_mm": 10.0, "AutoContactZ": 1, "Label": "E1_b"}
        df = pd.DataFrame([row1, row2, row3])
        motor = _StubMotor()
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(run_automation, "find_contact_z", return_value=(10.03, None)) as mocked_search:
            run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                motor=motor,
                rapid_sequence=_fake_rapid_sequence,
                log_fn=lambda *a, **k: None,
                vision_runtime=None,
            )
        self.assertEqual(mocked_search.call_count, 1)
        self.assertEqual(len(motor.moves_log), 3)
        self.assertAlmostEqual(motor.moves_log[0]["Z"], 9.800, places=6)  # row 1: real search-start
        self.assertAlmostEqual(motor.moves_log[1]["Z"], 3.0, places=6)  # row 2: spacer, no AutoContactZ
        self.assertAlmostEqual(motor.moves_log[2]["Z"], 10.03, places=6)  # row 3: cached contact Z

    def test_contact_skip_memory_bypasses_the_exact_cache(self):
        # Same setup as the exact-cache-hit test above, but row 3 sets
        # ContactSkipMemory=1 -- it must run a real search again (moving
        # to the search-start height) instead of reusing row 1's cached
        # exact-conditions contact.
        row1 = {**_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0), "Z_mm": 10.0, "AutoContactZ": 1, "Label": "E1_a"}
        row2 = {**_make_row("E2_test", 0.1, 5.0, 6.0, auto_track_xy=0), "Z_mm": 3.0, "AutoContactZ": 0, "Label": "E2_spacer"}
        row3 = {
            **_make_row("E1_test", 0.1, 1.0, 2.0, auto_track_xy=0), "Z_mm": 10.0,
            "AutoContactZ": 1, "ContactSkipMemory": 1, "Label": "E1_b",
        }
        df = pd.DataFrame([row1, row2, row3])
        motor = _StubMotor()
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(run_automation, "find_contact_z", return_value=(10.03, None)) as mocked_search:
            run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                motor=motor,
                rapid_sequence=_fake_rapid_sequence,
                log_fn=lambda *a, **k: None,
                vision_runtime=None,
            )
        self.assertEqual(mocked_search.call_count, 2)  # row 1 AND row 3 both really searched
        self.assertEqual(len(motor.moves_log), 3)
        # row 3's seed is pre-improved to row 1's measured 10.03 by the
        # (unrelated, always-on) plain XY-only cache before the
        # search-start offset is applied: 10.03 - 0.200 = 9.83. This is a
        # real search-start move (not the exact-cache's 10.03 itself), so
        # it's distinguishable from the skip-memory-off behavior verified
        # in test_exact_cache_hit_moves_directly_to_cached_contact_z.
        self.assertAlmostEqual(motor.moves_log[2]["Z"], 9.830, places=6)


class _LoggingMotor(_StubMotor):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        super().__init__(x, y, z)
        self.z_log = []

    def move_abs_wait(self, axis, pos_mm, **kwargs):
        super().move_abs_wait(axis, pos_mm, **kwargs)
        if axis == 'Z':
            self.z_log.append(pos_mm)


class _SequencedOCVBiologic(_StubDevice):
    """Returns OCV values from a fixed sequence, one per get_ocv() call,
    repeating the last value once exhausted."""

    def __init__(self, ocv_sequence):
        self._values = list(ocv_sequence)
        self._idx = 0

    def get_ocv(self, channel=1):
        value = self._values[min(self._idx, len(self._values) - 1)]
        self._idx += 1
        return value


class FindContactZTests(unittest.TestCase):
    """
    find_contact_z has no other direct (unmocked) test coverage -- every
    other test mocks it wholesale, since a real successful search normally
    blocks for CONTACT_CONFIRM_DURATION_S (10s) in _confirm_contact_candidate.
    These tests patch that confirmation step to be instantaneous so the
    actual step-loop/safety-lock logic (recently reworked to drop
    ContactMaxDrop_mm and derive the step budget from ContactMaxBeyondSeed_mm
    instead) gets real coverage.
    """

    def test_missing_columns_use_new_safety_focused_defaults(self):
        # positive_z_is_up=False in config.py -> approach_sign=+1, so Z
        # increases by step_mm each iteration. start_z = base_z - 0.200
        # (ContactStartOffset_mm default). Contact found at idx=5 with the
        # new ContactStep_mm default (0.005 mm, not the old 0.010/0.020).
        row = {"Label": "E1_test", "Z_mm": 10.0}
        motor = _LoggingMotor(z=10.0)
        biologic = _SequencedOCVBiologic([0.5, 0.5, 0.5, 0.5, 0.5, 0.05])
        with mock.patch.object(run_automation, "_confirm_contact_candidate", return_value=True):
            measure_z, parallax_sample = run_automation.find_contact_z(
                motor, biologic, row, log_fn=lambda *a, **k: None,
            )
        # start move + 6 step-loop moves (idx=0..5) + engage move.
        self.assertEqual(len(motor.z_log), 8)
        self.assertAlmostEqual(motor.z_log[0], 9.800, places=6)  # start_z
        step_positions = motor.z_log[1:7]
        expected = [9.800 + i * 0.005 for i in range(6)]
        for actual, want in zip(step_positions, expected):
            self.assertAlmostEqual(actual, want, places=6)
        # ContactEngage_mm defaults to 0.01, so the engage move lands
        # 0.01 mm past the confirmed contact height.
        self.assertAlmostEqual(measure_z, 9.835, places=6)
        self.assertAlmostEqual(motor.z_log[-1], 9.835, places=6)

    def test_exhausts_and_raises_without_contact_max_drop_column(self):
        # No ContactMaxDrop_mm anywhere -- the search budget now comes
        # entirely from ContactStartOffset_mm + ContactMaxBeyondSeed_mm.
        # OCV never drops below threshold, so the search must exhaust its
        # step budget and raise, never finding contact. Raises the more
        # specific ContactNotConfirmedError (a RuntimeError subclass) so
        # callers can catch just this failure mode and collect anyway.
        row = {
            "Label": "E1_test", "Z_mm": 10.0,
            "ContactStep_mm": 0.005, "ContactMaxBeyondSeed_mm": 0.05,
        }
        motor = _LoggingMotor(z=10.0)
        biologic = _SequencedOCVBiologic([0.5] * 100)
        with self.assertRaisesRegex(
            run_automation.ContactNotConfirmedError, "did not find a valid OCV threshold"
        ) as ctx:
            run_automation.find_contact_z(motor, biologic, row, log_fn=lambda *a, **k: None)
        # last_z carries the search's final (deepest) probed position, for
        # a caller that wants to collect a measurement there anyway.
        self.assertAlmostEqual(ctx.exception.last_z, motor.z_log[-1], places=6)
        # Never moved past the safety bound even while exhausting the budget.
        max_beyond = max(motor.z_log) - 10.0
        self.assertLessEqual(max_beyond, 0.05 + 1e-9)

    def _run_with_pixel_samples(self, pixel_samples):
        row = {"Label": "E1_test", "Z_mm": 10.0}
        motor = _LoggingMotor(z=10.0)
        biologic = _SequencedOCVBiologic([0.5, 0.5, 0.5, 0.5, 0.5, 0.05])
        sample_fn = mock.Mock(side_effect=pixel_samples)
        with mock.patch.object(run_automation, "_confirm_contact_candidate", return_value=True):
            return run_automation.find_contact_z(
                motor, biologic, row, log_fn=lambda *a, **k: None,
                probe_pixel_sample_fn=sample_fn,
            )

    def test_parallax_sample_rejected_when_displacement_too_small(self):
        # Both criteria eligible (direction is fine), but the magnitude
        # (0.5px) is below the threshold -- treated as a probe-tip
        # detection failure for this sample, not a valid tiny measurement.
        # Pinned rather than relying on the live config.py value -- this
        # threshold is meant to be user-tunable per-instrument (it's
        # explicitly UNMEASURED/provisional), so the test fixes it to a
        # known value instead of depending on whatever it's currently set
        # to on this machine. run_automation imports this as a bound name
        # (from config import ...), so it must be patched on run_automation
        # itself, not on the config module.
        with mock.patch.object(run_automation, 'VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX', 2.0):
            measure_z, parallax_sample = self._run_with_pixel_samples(
                [(100.0, 100.0), (99.5, 99.5)]
            )
        self.assertIsNone(parallax_sample)
        self.assertAlmostEqual(measure_z, 9.835, places=6)

    def test_parallax_sample_rejected_when_wrong_direction(self):
        # Magnitude passes (10px), but delta_px is positive -- as the probe
        # moves from search-start height to contact its detected pixel
        # position should always move up-and-left (both deltas <= 0).
        measure_z, parallax_sample = self._run_with_pixel_samples(
            [(100.0, 100.0), (110.0, 90.0)]
        )
        self.assertIsNone(parallax_sample)
        self.assertAlmostEqual(measure_z, 9.835, places=6)

    def test_parallax_sample_accepted_when_plausible(self):
        measure_z, parallax_sample = self._run_with_pixel_samples(
            [(100.0, 100.0), (90.0, 80.0)]
        )
        self.assertIsNotNone(parallax_sample)
        start_z, start_pixel, contact_z, contact_pixel = parallax_sample
        self.assertEqual(start_pixel, (100.0, 100.0))
        self.assertEqual(contact_pixel, (90.0, 80.0))
        self.assertAlmostEqual(measure_z, 9.835, places=6)

    def test_safety_lock_fires_before_overshooting_the_seed(self):
        # start_offset=0.2, max_beyond_seed=0.05 -> budget 0.25 mm; with
        # step_mm=0.07, 0.25/0.07 = 3.571..., which rounds UP to 4 steps.
        # At idx=4, z_here = start_z + 4*0.07 = (base_z-0.2) + 0.28
        #   -> beyond_seed = 0.08 mm > the 0.05 mm limit -- the safety lock
        # must fire on that step rather than silently overshooting it.
        row = {
            "Label": "E1_test", "Z_mm": 10.0,
            "ContactStep_mm": 0.07, "ContactMaxBeyondSeed_mm": 0.05,
        }
        motor = _LoggingMotor(z=10.0)
        biologic = _SequencedOCVBiologic([0.5] * 100)
        with self.assertRaisesRegex(RuntimeError, "AutoContactZ safety lock"):
            run_automation.find_contact_z(motor, biologic, row, log_fn=lambda *a, **k: None)
        # The offending position must never actually have been commanded.
        max_beyond = max(motor.z_log) - 10.0
        self.assertLessEqual(max_beyond, 0.05 + 1e-9)


if __name__ == "__main__":
    unittest.main()
