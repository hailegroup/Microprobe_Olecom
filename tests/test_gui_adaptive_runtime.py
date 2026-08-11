# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from pathlib import Path
import json
import time
import queue
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import cv2

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import gui
import measurement_sequence
from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings
from adaptive_types import AnalysisResult, RecommendationPayload
from measurement_sequence import SequenceResult


def _fake_full_processor(*, dc_path, eis_path, sample_name):
    return {
        "status": "completed",
        "sample_name": sample_name,
        "excel_path": f"{sample_name}.xlsx",
        "output_dir": f"processed/{sample_name}",
        "notes": ["test full processing completed"],
    }


class GUIAdaptiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.orig_mods = {
            "sequence": gui.MODS.get("sequence"),
            "rapid_sequence": gui.MODS.get("rapid_sequence"),
            "normal_sequence": gui.MODS.get("normal_sequence"),
        }
        self.app = gui.MicroprobGUI(enable_background_polls=False)
        self.app.withdraw()
        self.app.bl = object()
        self.app.tc = None
        self.app.mfc = None
        self.app.motor = None
        self._orig_measurement_sequence_rapid = measurement_sequence.rapid_eis_sequence

    def tearDown(self):
        for key, value in self.orig_mods.items():
            gui.MODS[key] = value
        measurement_sequence.rapid_eis_sequence = self._orig_measurement_sequence_rapid
        try:
            self.app.destroy()
        except Exception:
            pass

    def _drain_monitor_events(self):
        events = []
        while True:
            try:
                events.append(self.app.monitor_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _make_row(self, label, v_dc):
        return {
            "Label": label,
            "Temperature_C": "None",
            "RampRate_C_per_min": "None",
            "GasA_sccm": "None",
            "GasB_sccm": "None",
            "X_mm": "None",
            "Y_mm": "None",
            "Z_mm": "None",
            "V_dc": v_dc,
            "dV": 0.03,
            "HoldTime_s": 60,
            "PostPEIS_HoldTime_s": 30,
            "PEIS_fHigh": 100000,
            "PEIS_fLow": 0.1,
            "PEIS_nPts": 60,
            "CA_duration_s": 200,
            "CA_dt": 0.01,
            "StableTime_s": "None",
            "GasStableTime_s": "None",
            "Skip": 0,
        }

    def test_second_adaptive_row_can_switch_to_normal_eis(self):
        calls = []

        def fake_rapid_sequence(**kwargs):
            calls.append(("rapid", kwargs["label"], kwargs["peis_f_low"], kwargs["ca_duration"]))
            return SequenceResult(
                measurement_mode="rapid_eis",
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_post.txt"),
            )

        def fake_normal_sequence(**kwargs):
            calls.append(("normal", kwargs["label"], kwargs["peis_f_low"], None))
            return SequenceResult(
                measurement_mode="normal_eis",
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis_only.txt"),
            )

        def fake_analyzer(*, dc_path, eis_path, sample_name):
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.2,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                peis_only_selection_reason="peis_reaches_saturation",
                cp_saturation_reached=True,
                agreement_rel_err=0.02,
            )

        engine = AdaptiveMeasurementEngine(
            analyzer=fake_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.1),
        )
        self.app._adaptive_engine_factory = lambda normal_eis_floor_hz=0.01: engine
        gui.MODS["sequence"] = fake_rapid_sequence
        gui.MODS["rapid_sequence"] = fake_rapid_sequence
        gui.MODS["normal_sequence"] = fake_normal_sequence

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([
                self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
                self._make_row("ADAPT_T300_E1_V+0.100", 0.1),
            ])
            self.app._run_worker()

        self.assertEqual(calls[0][0], "rapid")
        self.assertEqual(calls[1][0], "normal")
        self.assertGreater(calls[1][2], calls[0][2])
        engine.shutdown()

    def test_temperature_safety_trip_detects_implausibly_low_hot_furnace_read(self):
        reason = self.app._evaluate_temperature_safety_trip(
            pv_c=18.0,
            previous_pv_c=612.0,
            active_target_c=600.0,
        )
        self.assertIn("dropped to 18.0 C", reason)
        self.assertIn("bad TC/contact read", reason)

    def test_manual_ocv_text_formats_value_or_dash(self):
        self.assertEqual(self.app._format_manual_ocv_text(None), "OCV: -")
        self.assertEqual(self.app._format_manual_ocv_text(0.123456), "OCV: +0.1235 V")

    def test_manual_ocv_poll_complete_updates_display_and_clears_inflight(self):
        self.app._manual_ocv_poll_inflight = True
        self.app._manual_ocv_poll_complete(0.01234)
        self.assertFalse(self.app._manual_ocv_poll_inflight)
        self.assertEqual(self.app._manual_ocv_var.get(), "OCV: +0.0123 V")
        self.app._manual_ocv_poll_inflight = True
        self.app._manual_ocv_poll_complete(None)
        self.assertFalse(self.app._manual_ocv_poll_inflight)
        self.assertEqual(self.app._manual_ocv_var.get(), "OCV: -")

    def test_temperature_safety_trip_detects_sudden_large_drop(self):
        reason = self.app._evaluate_temperature_safety_trip(
            pv_c=420.0,
            previous_pv_c=540.0,
            active_target_c=600.0,
        )
        self.assertIn("dropped by 120.0 C", reason)
        self.assertIn("TC/contact fault", reason)

    def test_trigger_temperature_safety_trip_stops_run_and_writes_record(self):
        self.app.running = True
        self.app.stop_flag.clear()
        self.app._active_temperature_target_c = 600.0
        self.app.bl = SimpleNamespace(stop_measurement=mock.Mock())
        self.app.tc = SimpleNamespace(safe_shutdown=mock.Mock())

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(gui.messagebox, "showerror") as showerror:
            self.app._active_run_result_root = tmpdir
            self.app._result_dir.set(tmpdir)
            self.app._trigger_temperature_safety_trip(
                "Temperature safety trip: furnace PV dropped to 18.0 C while target was 600.0 C.",
                18.0,
                612.0,
            )
            self.app.update()

            self.assertTrue(self.app.stop_flag.is_set())
            self.app.bl.stop_measurement.assert_called_once()
            self.app.tc.safe_shutdown.assert_called_once_with(gui.TEMP_SAFETY_SHUTDOWN_SETPOINT_C)
            showerror.assert_called_once()
            self.assertEqual("Temperature safety trip", self.app._status_var.get())

            trip_path = os.path.join(tmpdir, "temperature_safety_trip.json")
            self.assertTrue(os.path.exists(trip_path))
            with open(trip_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["pv_c"], 18.0)
            self.assertEqual(payload["previous_pv_c"], 612.0)
            self.assertEqual(payload["target_c"], 600.0)

    def test_second_adaptive_row_replans_rapid_parameters_while_preserving_exploratory_seed(self):
        calls = []

        def fake_rapid_sequence(**kwargs):
            calls.append((
                "rapid",
                kwargs["label"],
                kwargs["peis_f_low"],
                kwargs["ca_duration"],
                kwargs["hold_time"],
                kwargs["post_peis_hold_time"],
            ))
            return SequenceResult(
                measurement_mode="rapid_eis",
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_post.txt"),
            )

        def fake_analyzer(*, dc_path, eis_path, sample_name):
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.03,
                recommended_peis_conservative_cp_time_s=120.0,
                data_sufficient=True,
                peis_only_sufficient=False,
                cp_saturation_reached=False,
            )

        engine = AdaptiveMeasurementEngine(
            analyzer=fake_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.1),
        )
        self.app._adaptive_engine_factory = lambda normal_eis_floor_hz=0.01: engine
        gui.MODS["sequence"] = fake_rapid_sequence
        gui.MODS["rapid_sequence"] = fake_rapid_sequence
        gui.MODS["normal_sequence"] = None

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([
                self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
                self._make_row("ADAPT_T300_E1_V+0.100", 0.1),
            ])
            self.app._run_worker()

        self.assertEqual(calls[0][0], "rapid")
        self.assertEqual(calls[1][0], "rapid")
        self.assertAlmostEqual(calls[1][2], calls[0][2], places=9)
        self.assertNotEqual(calls[1][3], calls[0][3])
        self.assertNotEqual(calls[1][4], calls[0][4])
        self.assertNotEqual(calls[1][5], calls[0][5])
        engine.shutdown()

    def test_run_worker_writes_and_backfills_adaptive_runtime_summary(self):
        def fake_rapid_sequence(**kwargs):
            return SequenceResult(
                measurement_mode="rapid_eis",
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_post.txt"),
            )

        def delayed_analyzer(*, dc_path, eis_path, sample_name):
            time.sleep(0.15)
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.2,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                peis_only_selection_reason="peis_reaches_saturation",
                cp_saturation_reached=True,
                agreement_rel_err=0.02,
            )

        engine = AdaptiveMeasurementEngine(
            analyzer=delayed_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.01),
        )
        self.app._adaptive_engine_factory = lambda normal_eis_floor_hz=0.01: engine
        gui.MODS["sequence"] = fake_rapid_sequence
        gui.MODS["rapid_sequence"] = fake_rapid_sequence
        gui.MODS["normal_sequence"] = None

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([
                self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
            ])
            self.app._run_worker()

            summary_path = os.path.join(tmpdir, "adaptive_runtime_summary.json")
            self.assertTrue(os.path.exists(summary_path))
            with open(summary_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)

        self.assertEqual(len(payload["points"]), 1)
        finalize = payload["points"][0]["analysis_finalize"]
        self.assertTrue(finalize["analysis_ready_within_wait"])
        self.assertEqual(finalize["state"], "recommendation_ready")
        self.assertIsNotNone(finalize["analysis_result"])
        self.assertEqual(payload["mode_counts"]["rapid_eis"], 1)
        self.assertIn("finished_at", payload)
        engine.shutdown()

    def test_adaptive_runtime_summary_records_consumed_two_stage_policy_action(self):
        def fake_rapid_sequence(**kwargs):
            return SequenceResult(
                measurement_mode="rapid_eis",
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_post.txt"),
            )

        def fake_normal_sequence(**kwargs):
            return SequenceResult(
                measurement_mode="normal_eis",
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis_only.txt"),
            )

        def fake_analyzer(*, dc_path, eis_path, sample_name):
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.2,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                recommended_normal_peis_lowest_freq_hz=1.0,
                peis_only_selection_reason="peis_reaches_saturation",
                cp_saturation_reached=True,
                agreement_rel_err=0.02,
            )

        engine = AdaptiveMeasurementEngine(
            analyzer=fake_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.1),
        )
        self.app._adaptive_engine_factory = lambda normal_eis_floor_hz=0.01: engine
        gui.MODS["sequence"] = fake_rapid_sequence
        gui.MODS["rapid_sequence"] = fake_rapid_sequence
        gui.MODS["normal_sequence"] = fake_normal_sequence

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([
                self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
                self._make_row("ADAPT_T300_E1_V+0.100", 0.1),
            ])
            self.app._run_worker()

            summary_path = os.path.join(tmpdir, "adaptive_runtime_summary.json")
            with open(summary_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)

        second = payload["points"][1]["recommendation_used"]
        self.assertEqual(second["current_run_policy_action"], "handoff_to_normal_lf")
        self.assertTrue(second["current_run_policy_consumed"])
        self.assertEqual(second["current_run_policy_analysis_point_id"], "row0001_ADAPT_T300_E1_V+0.000")
        self.assertAlmostEqual(second["current_run_policy_runtime_lf_hz"], 1.0, places=9)
        consumed = payload["points"][1]["consumed_current_run_policy"]
        self.assertEqual(consumed["action"], "handoff_to_normal_lf")
        self.assertTrue(consumed["consumed"])
        self.assertEqual(consumed["analysis_point_id"], "row0001_ADAPT_T300_E1_V+0.000")
        self.assertAlmostEqual(consumed["runtime_lf_hz"], 1.0, places=9)
        consumed_text = payload["points"][1]["consumed_current_run_policy_text"]
        self.assertIn("adaptive handoff to normal EIS", consumed_text)
        self.assertIn("1 Hz", consumed_text)
        completed = payload["points"][0]["completed_point_current_run_policy"]
        self.assertEqual(completed["action"], "handoff_to_normal_lf")
        self.assertEqual(completed["measurement_mode"], "normal_eis")
        self.assertAlmostEqual(completed["runtime_lf_hz"], 1.0, places=9)
        completed_text = payload["points"][0]["completed_point_current_run_policy_text"]
        self.assertIn("completed-point policy stored a normal handoff", completed_text)
        self.assertIn("1 Hz", completed_text)
        engine.shutdown()

    def test_manual_quick_eis_emits_live_device_events(self):
        class FakeBioLogic:
            def __init__(self):
                self.live_calls = 0

            def get_live_values(self):
                self.live_calls += 1
                return {
                    "elapsed_s": 0.1 * self.live_calls,
                    "frequency_hz": 1000.0 / self.live_calls,
                    "ewe_v": 0.0,
                    "current_a": 1e-6 * self.live_calls,
                }

            def run_peis(self, **kwargs):
                time.sleep(0.16)
                return np.array([[1000.0, 10.0, 1.0], [100.0, 10.5, 0.8]])

        self.app.bl = FakeBioLogic()

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app._manual_run_quick_eis()
            events = self._drain_monitor_events()

        device_live_events = [event for event, payload in events if event == "device_live"]
        self.assertGreaterEqual(len(device_live_events), 1)
        self.assertGreaterEqual(self.app.bl.live_calls, 1)

    def test_live_poll_recovers_from_transient_get_live_values_failure(self):
        class FlakyBioLogic:
            def __init__(self):
                self.live_calls = 0

            def get_live_values(self):
                self.live_calls += 1
                if self.live_calls == 1:
                    raise RuntimeError("temporary PEIS transition glitch")
                return {
                    "elapsed_s": 0.2 * self.live_calls,
                    "frequency_hz": 100.0,
                    "ewe_v": 0.3,
                    "current_a": 2e-6,
                }

        self.app.bl = FlakyBioLogic()
        stop_event = self.app._start_biologic_live_poll("manual_eis", interval_s=0.02)
        try:
            time.sleep(0.12)
            events = self._drain_monitor_events()
        finally:
            stop_event.set()

        device_live_events = [event for event, payload in events if event == "device_live"]
        self.assertGreaterEqual(len(device_live_events), 1)
        self.assertGreaterEqual(self.app.bl.live_calls, 2)

    def test_manual_quick_frequency_defaults_match_previous_fixed_values(self):
        self.assertEqual(self.app._quick_eis["peis_f_high"].get(), f"{gui.MANUAL_QUICK_F_HIGH_HZ:.0f}")
        self.assertEqual(self.app._quick_eis["peis_f_low"].get(), f"{gui.MANUAL_QUICK_F_LOW_HZ:.1f}")
        self.assertEqual(self.app._quick_rapid["peis_f_high"].get(), f"{gui.MANUAL_QUICK_F_HIGH_HZ:.0f}")
        self.assertEqual(self.app._quick_rapid["peis_f_low"].get(), f"{gui.MANUAL_QUICK_F_LOW_HZ:.1f}")

    def test_image_monitor_can_load_manual_markup_layout(self):
        markup = PROJECT_DIR.parent / "OM" / "Swift_snapshot_markup.png"
        if not markup.exists():
            self.skipTest("Manual markup snapshot not present in OM/")
        self.app._image_markup_path_var.set(str(markup))
        self.app._image_load_markup()
        self.assertIsNotNone(self.app._image_markup_map)
        self.assertIsNotNone(self.app._image_markup_reference_rgb)
        self.assertEqual(len(self.app._image_markup_map), 18)
        self.assertIn("18 electrodes", self.app._image_markup_status_var.get())

    def test_image_monitor_load_design_resets_stale_high_override_opt_in(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        fake_design = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_design_path_var.set("fake_design.png")
        with mock.patch.object(gui, "detect_electrode_map", return_value=fake_design):
            self.app._image_load_design()

        self.assertIsNotNone(self.app._image_design_map)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_clearing_design_resets_stale_high_override_opt_in(self):
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_design_path_var.set("")
        self.app._image_load_design()

        self.assertIsNone(self.app._image_design_map)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_load_markup_resets_stale_high_override_opt_in(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        fake_markup = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "markup"},
            ]
        )
        fake_rgb = np.zeros((12, 12, 3), dtype=np.uint8)
        self.app._image_markup_path_var.set("fake_markup.png")
        with mock.patch.object(gui.cv2, "imread", return_value=np.zeros((12, 12, 3), dtype=np.uint8)), \
             mock.patch.object(gui.cv2, "cvtColor", return_value=fake_rgb), \
             mock.patch.object(gui, "detect_markup_electrode_map_rgb", return_value=fake_markup), \
             mock.patch.object(gui, "strip_red_markup_from_rgb", return_value=fake_rgb.copy()):
            self.app._image_load_markup()

        self.assertIsNotNone(self.app._image_markup_map)
        self.assertIsNotNone(self.app._image_markup_reference_rgb)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_clearing_markup_resets_stale_high_override_opt_in(self):
        self.app._image_markup_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "markup"},
            ]
        )
        self.app._image_markup_reference_rgb = np.zeros((8, 8, 3), dtype=np.uint8)
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_markup_path_var.set("")
        self.app._image_load_markup()

        self.assertIsNone(self.app._image_markup_map)
        self.assertIsNone(self.app._image_markup_reference_rgb)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_design_load_failure_resets_stale_high_override_opt_in(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_design_path_var.set("broken_design.png")
        with mock.patch.object(gui, "detect_electrode_map", side_effect=RuntimeError("bad design")):
            self.app._image_load_design()

        self.assertIsNone(self.app._image_design_map)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("Design load failed", self.app._image_design_status_var.get())

    def test_image_monitor_markup_load_failure_resets_stale_high_override_opt_in(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_markup_path_var.set("broken_markup.png")
        with mock.patch.object(gui.cv2, "imread", return_value=np.zeros((12, 12, 3), dtype=np.uint8)), \
             mock.patch.object(gui.cv2, "cvtColor", return_value=np.zeros((12, 12, 3), dtype=np.uint8)), \
             mock.patch.object(gui, "detect_markup_electrode_map_rgb", side_effect=RuntimeError("bad markup")):
            self.app._image_load_markup()

        self.assertIsNone(self.app._image_markup_map)
        self.assertIsNone(self.app._image_markup_reference_rgb)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("Markup load failed", self.app._image_markup_status_var.get())

    def test_device_live_updates_current_proxy_but_keeps_nyquist_canvas_reserved(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event("step", {"label": "manual_eis", "step": "manual_peis", "message": "Quick EIS running"})
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_eis",
                "values": {
                    "elapsed_s": 1.2,
                    "frequency_hz": 12.5,
                    "ewe_v": 0.2,
                    "current_a": 3.0e-6,
                },
            },
        )

        self.assertEqual(self.app._monitor_dc_title, "PEIS Live Current Monitor")
        self.assertEqual(self.app._monitor_dc_axes, ("Time (s)", "Current (A)"))
        self.assertEqual(len(self.app._monitor_dc_points), 1)
        self.assertEqual(self.app._monitor_dc_points[0][0], 0.0)
        self.assertEqual(self.app._monitor_eis_title, "Impedance Monitor")
        self.assertEqual(self.app._monitor_eis_axes, ("Re(Z) (Ohm)", "-Im(Z) (Ohm)"))
        self.assertEqual(len(self.app._monitor_eis_points), 0)

    def test_device_live_normalizes_elapsed_time_to_first_sample(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event("step", {"label": "manual_eis", "step": "manual_peis", "message": "Quick EIS running"})
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_eis",
                "values": {
                    "elapsed_s": 32.5,
                    "frequency_hz": 0.5,
                    "ewe_v": 0.3,
                    "current_a": 2.8e-5,
                },
            },
        )
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_eis",
                "values": {
                    "elapsed_s": 33.0,
                    "frequency_hz": 0.45,
                    "ewe_v": 0.3,
                    "current_a": 2.9e-5,
                },
            },
        )

        self.assertEqual(len(self.app._monitor_dc_points), 2)
        self.assertAlmostEqual(self.app._monitor_dc_points[0][0], 0.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[1][0], 0.5, places=6)

    def test_device_live_appends_after_pre_hold_trace_during_peis(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event(
            "dc_data",
            {
                "label": "manual_rapid",
                "step": "ca_hold_done",
                "data": np.array([[0.0, 0.3, 1.0e-5], [5.0, 0.3, 1.1e-5]]),
            },
        )
        self.assertEqual(self.app._monitor_dc_title, "Pre-PEIS Hold Current")
        self.assertEqual(len(self.app._monitor_dc_points), 2)

        self.app._apply_monitor_event(
            "step",
            {"label": "manual_rapid", "step": "peis", "message": "PEIS started"},
        )
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_rapid",
                "values": {
                    "elapsed_s": 9.0,
                    "frequency_hz": 10.82,
                    "ewe_v": 0.2949,
                    "current_a": 2.7e-5,
                },
            },
        )

        self.assertEqual(self.app._monitor_dc_title, "Measurement Current Timeline")
        self.assertEqual(self.app._monitor_dc_series, "device_live_current")
        self.assertEqual(len(self.app._monitor_dc_points), 3)
        self.assertAlmostEqual(self.app._monitor_dc_points[0][0], 0.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[1][0], 5.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[2][0], 5.0, places=6)

    def test_post_peis_ca_appends_after_live_current_timeline(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event(
            "dc_data",
            {
                "label": "manual_rapid",
                "step": "ca_hold_done",
                "data": np.array([[0.0, 0.3, 1.0e-5], [5.0, 0.3, 1.1e-5]]),
            },
        )
        self.app._apply_monitor_event(
            "step",
            {"label": "manual_rapid", "step": "peis", "message": "PEIS started"},
        )
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_rapid",
                "values": {
                    "elapsed_s": 9.0,
                    "frequency_hz": 10.82,
                    "ewe_v": 0.2949,
                    "current_a": 2.7e-5,
                },
            },
        )
        self.app._apply_monitor_event(
            "dc_data",
            {
                "label": "manual_rapid",
                "step": "post_peis_ca_sequence",
                "data": np.array([[0.0, 0.31, 1.2e-5], [2.0, 0.33, 1.3e-5]]),
            },
        )

        self.assertEqual(self.app._monitor_dc_title, "Measurement Current Timeline")
        self.assertEqual(self.app._monitor_dc_series, "post_peis_ca_sequence")
        self.assertEqual(len(self.app._monitor_dc_points), 5)
        self.assertAlmostEqual(self.app._monitor_dc_points[2][0], 5.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[3][0], 5.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[4][0], 7.0, places=6)

    def test_post_peis_ca_appends_even_without_peis_live_current(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event(
            "dc_data",
            {
                "label": "manual_rapid",
                "step": "ca_hold_done",
                "data": np.array([[0.0, 0.3, 1.0e-5], [5.0, 0.3, 1.1e-5]]),
            },
        )
        self.app._apply_monitor_event(
            "dc_data",
            {
                "label": "manual_rapid",
                "step": "post_peis_ca_sequence_done",
                "data": np.array([[0.0, 0.31, 1.2e-5], [2.0, 0.33, 1.3e-5]]),
            },
        )

        self.assertEqual(self.app._monitor_dc_title, "Measurement Current Timeline")
        self.assertEqual(self.app._monitor_dc_series, "post_peis_ca_sequence_done")
        self.assertEqual(len(self.app._monitor_dc_points), 4)
        self.assertAlmostEqual(self.app._monitor_dc_points[0][0], 0.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[1][0], 5.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[2][0], 5.0, places=6)
        self.assertAlmostEqual(self.app._monitor_dc_points[3][0], 7.0, places=6)

    def test_compute_plot_bounds_clamps_nonnegative_time_axis_to_zero(self):
        bounds = self.app._compute_plot_bounds(
            [(5.0, 1.0), (10.0, 2.0)],
            x_label="Time (s)",
            y_label="Current (A)",
        )
        self.assertEqual(bounds[0], 0.0)
        self.assertGreater(bounds[1], 10.0)

    def test_manual_recommendation_prefers_higher_normal_lf_message(self):
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=0.01,
            recommended_normal_peis_lowest_freq_hz=0.3,
            recommended_peis_conservative_cp_time_s=None,
            data_sufficient=True,
            peis_only_sufficient=True,
        )
        text = self.app._format_manual_recommendation_text(analysis)
        self.assertIn("higher LF", text)

    def test_manual_recommendation_is_mirrored_to_live_monitor(self):
        self.app._manual_set_recommendation("Recommendation: normal EIS is sufficient.")
        self.assertEqual(
            self.app._monitor_recommendation_var.get(),
            "Recommendation: normal EIS is sufficient.",
        )

    def test_manual_recommendation_autofills_quick_controls(self):
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=0.02,
            recommended_normal_peis_lowest_freq_hz=0.2,
            recommended_peis_conservative_cp_time_s=123.0,
            minimum_valid_cp_duration_s=90.0,
            saturation_recommended_cp_duration_s=150.0,
            data_sufficient=True,
            peis_only_sufficient=True,
        )
        self.app._apply_manual_recommendation_to_controls(
            analysis,
            measured_settings={
                "v_dc": 0.315,
                "amp_mv": 15.0,
                "dv_mv": 12.0,
                "n_pts": 70,
                "peis_f_high": 200000.0,
                "hold_time": 7.0,
                "post_hold_time": 4.0,
            },
        )

        self.assertEqual(self.app._quick_eis["v_dc"].get(), "0.315")
        self.assertEqual(self.app._quick_rapid["v_dc"].get(), "0.315")
        self.assertEqual(self.app._quick_eis["amp_mv"].get(), "15.0")
        self.assertEqual(self.app._quick_rapid["dv_mv"].get(), "12.0")
        self.assertEqual(self.app._quick_eis["n_pts"].get(), "70")
        self.assertEqual(self.app._quick_rapid["n_pts"].get(), "70")
        self.assertEqual(self.app._quick_eis["peis_f_high"].get(), "200000")
        self.assertEqual(self.app._quick_rapid["peis_f_high"].get(), "200000")
        self.assertEqual(self.app._quick_eis["peis_f_low"].get(), "0.200")
        self.assertEqual(self.app._quick_rapid["peis_f_low"].get(), "0.020")
        self.assertEqual(self.app._quick_rapid["hold_time"].get(), "7.000")
        self.assertEqual(self.app._quick_rapid["post_hold_time"].get(), "4.000")
        self.assertEqual(self.app._quick_rapid["ca_duration"].get(), "123.000")

    def test_manual_rapid_recommendation_adds_fft_overlay_points(self):
        sequence_result = SequenceResult(
            measurement_mode="rapid_eis",
            eis_data=np.array([[9.0, 1000.0, 200.0]]),
            peis_path="manual_peis.txt",
            post_ca_path="manual_post.txt",
        )
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=0.05,
            recommended_peis_conservative_cp_time_s=120.0,
            recommended_normal_peis_lowest_freq_hz=0.05,
            data_sufficient=True,
            peis_only_sufficient=False,
        )
        visuals = {
            "recovered_fft_nyquist_points": [(100.0, 20.0), (110.0, 22.0)],
        }

        with mock.patch(
            "analysis_adapter.analyze_measurement_files_with_visuals",
            return_value=(analysis, visuals),
        ):
            self.app._analyze_manual_sequence_result(
                sequence_result,
                current_low_hz=9.0,
                measured_settings={"v_dc": 0.3, "peis_f_low": 9.0},
            )

        self.assertEqual(
            self.app._monitor_eis_overlay_points,
            visuals["recovered_fft_nyquist_points"],
        )

    def test_manual_normal_recommendation_clears_fft_overlay_points(self):
        self.app._monitor_eis_overlay_points = [(1.0, 2.0)]
        sequence_result = SequenceResult(
            measurement_mode="rapid_eis",
            eis_data=np.array([[9.0, 1000.0, 200.0]]),
            peis_path="manual_peis.txt",
            post_ca_path="manual_post.txt",
        )
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=0.5,
            recommended_peis_conservative_cp_time_s=None,
            recommended_normal_peis_lowest_freq_hz=0.5,
            data_sufficient=True,
            peis_only_sufficient=True,
        )

        with mock.patch(
            "analysis_adapter.analyze_measurement_files_with_visuals",
            return_value=(analysis, {"recovered_fft_nyquist_points": [(10.0, 3.0)]}),
        ):
            self.app._analyze_manual_sequence_result(sequence_result, current_low_hz=0.5)

        self.assertEqual(self.app._monitor_eis_overlay_points, [])

    def test_time_series_plot_keeps_dense_live_history_without_downsampling(self):
        points = [(float(i) * 0.01, np.sin(i * 0.01)) for i in range(1500)]
        kept = self.app._plot_points_for_canvas(
            points,
            max_points=gui.MONITOR_TIME_SERIES_MAX_POINTS,
        )
        self.assertEqual(len(kept), len(points))
        self.assertEqual(kept[0], points[0])
        self.assertEqual(kept[-1], points[-1])

    def test_nyquist_plot_still_downsamples_large_series(self):
        points = [(float(i), float(i % 17)) for i in range(2000)]
        kept = self.app._plot_points_for_canvas(
            points,
            max_points=gui.MONITOR_NYQUIST_MAX_POINTS,
        )
        self.assertLess(len(kept), len(points))
        self.assertEqual(kept[0], points[0])
        self.assertEqual(kept[-1], points[-1])

    def test_manual_recommendation_lf_is_capped_to_one_hz(self):
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=9.74,
            recommended_normal_peis_lowest_freq_hz=9.74,
            recommended_peis_conservative_cp_time_s=None,
            data_sufficient=True,
            peis_only_sufficient=False,
            source="peis_only_followup",
        )
        text = self.app._format_manual_recommendation_text(analysis, current_low_hz=0.1)
        self.assertIn("rapid EIS", text)

        self.app._apply_manual_recommendation_to_controls(analysis, measured_settings={})
        self.assertEqual(self.app._quick_eis["peis_f_low"].get(), "1.000")
        self.assertEqual(self.app._quick_rapid["peis_f_low"].get(), "1.000")

    def test_manual_recommendation_can_suggest_rapid_or_lower_lf_normal(self):
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=0.03,
            recommended_peis_conservative_cp_time_s=120.0,
            data_sufficient=True,
            peis_only_sufficient=False,
        )
        text = self.app._format_manual_recommendation_text(analysis)
        self.assertIn("rapid EIS or lower-LF normal EIS", text)

    def test_manual_recommendation_marks_nearby_peis_only_lf_as_close_normal(self):
        analysis = AnalysisResult(
            recommended_peis_lowest_freq_hz=9.74,
            recommended_normal_peis_lowest_freq_hz=9.74,
            recommended_peis_conservative_cp_time_s=None,
            data_sufficient=True,
            peis_only_sufficient=False,
            source="peis_only_followup",
        )
        text = self.app._format_manual_recommendation_text(analysis, current_low_hz=9.0)
        self.assertIn("normal EIS is close", text)
        self.assertNotIn("rapid EIS.", text)

    def test_adaptive_policy_text_describes_normal_handoff(self):
        recommendation = RecommendationPayload(
            action="use_analysis_recommendation",
            measurement_mode="normal_eis",
            peis_lowest_freq_hz=1.0,
            cp_duration_s=0.0,
            analysis_source="post_measurement_analysis",
            current_run_policy_action="handoff_to_normal_lf",
            current_run_policy_consumed=True,
            current_run_policy_analysis_point_id="row0001_demo",
            current_run_policy_runtime_lf_hz=1.0,
        )
        text = self.app._format_adaptive_policy_text(recommendation=recommendation)
        self.assertIn("adaptive handoff to normal EIS", text)
        self.assertIn("1 Hz", text)

    def test_prepare_adaptive_row_updates_monitor_recommendation_from_policy(self):
        recommendation = RecommendationPayload(
            action="use_analysis_recommendation",
            measurement_mode="rapid_eis",
            peis_lowest_freq_hz=0.19,
            cp_duration_s=120.0,
            analysis_source="post_measurement_analysis",
            current_run_policy_action="keep_exploratory_seed_for_hybrid",
            current_run_policy_consumed=True,
            current_run_policy_analysis_point_id="row0001_demo",
            current_run_policy_runtime_lf_hz=0.19,
        )

        class FakeEngine:
            def recommend_next_point(self, point):
                return recommendation

        runtime = {
            "engine": FakeEngine(),
            "regime_counts": {self.app._adaptive_regime_key(self._make_row("ADAPT_T300_E1_V+0.100", 0.1)): 1},
        }
        row = self._make_row("ADAPT_T300_E1_V+0.100", 0.1)
        self.app._prepare_adaptive_row(runtime, row, 1)
        text = self.app._monitor_recommendation_var.get()
        self.assertIn("exploratory LF seed", text)
        self.assertIn("0.19 Hz", text)

    def test_manual_stop_measurement_button_is_shared_outside_rapid_panel(self):
        self.assertEqual(self.app._manual_stop_measurement_btn["text"], "Stop Measurement")
        parent_name = str(self.app._manual_stop_measurement_btn.master)
        self.assertNotIn("quick_rapid", parent_name.lower())
        ancestor = self.app._manual_stop_measurement_btn.master
        found_tab_manual = False
        while ancestor is not None:
            if ancestor == self.app.tab_manual:
                found_tab_manual = True
                break
            ancestor = getattr(ancestor, "master", None)
        self.assertTrue(found_tab_manual)

    def test_manual_tab_uses_scrollable_canvas_container(self):
        self.assertTrue(hasattr(self.app, "_manual_scroll_canvas"))
        self.assertTrue(hasattr(self.app, "_manual_scroll_window"))

    def test_image_monitor_tab_and_status_vars_exist(self):
        self.assertTrue(hasattr(self.app, "tab_image"))
        self.assertEqual(self.app._image_status_var.get(), "Camera idle")
        self.assertEqual(self.app._image_detection_var.get(), "Electrodes: -")
        self.app._image_detector_var.set("reference")
        self.assertEqual(self.app._image_detector_var.get(), "reference")

    def test_image_monitor_expected_count_parser(self):
        self.app._image_expected_count_var.set("")
        self.assertIsNone(self.app._parse_image_expected_count())
        self.app._image_expected_count_var.set("12")
        self.assertEqual(self.app._parse_image_expected_count(), 12)
        self.app._image_expected_count_var.set("0")
        self.assertIsNone(self.app._parse_image_expected_count())
        self.app._image_expected_count_var.set("abc")
        self.assertIsNone(self.app._parse_image_expected_count())

    def test_image_monitor_tick_prefers_markup_seed_tracking_when_loaded(self):
        markup = PROJECT_DIR.parent / "OM" / "Swift_snapshot_markup.png"
        if not markup.exists():
            self.skipTest("Manual markup snapshot not present in OM/")

        image_rgb = cv2.cvtColor(cv2.imread(str(markup), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        self.app._image_markup_path_var.set(str(markup))
        self.app._image_load_markup()
        self.app._image_detector_var.set("live")
        self.app._image_monitor_running = True
        self.app._image_monitor_frame_idx = 0
        self.app._image_monitor_last_overlay_bgr = None

        class FakeCap:
            def read(self_inner):
                return True, cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)

        self.app._image_monitor_cap = FakeCap()
        self.app.after = lambda *args, **kwargs: None
        self.app._render_image_monitor_frame = lambda frame_bgr: None

        with mock.patch.object(
            gui,
            "compute_ecc_affine_registration",
            return_value=(np.eye(3, dtype=np.float32), 0.95),
        ) as mocked_ecc, mock.patch.object(
            gui,
            "refine_circular_electrode_map_rgb",
            side_effect=lambda frame_rgb, seed_detections, **kwargs: seed_detections.copy(),
        ) as mocked_refine:
            self.app._image_monitor_tick()

        mocked_ecc.assert_called_once()
        mocked_refine.assert_called_once()
        self.assertIsNotNone(self.app._image_tracking_map)
        self.assertEqual(len(self.app._image_tracking_map), 18)
        self.assertIn("markup seed", self.app._image_detection_var.get())

    def test_image_monitor_can_freeze_current_overlay_as_seed(self):
        self.app._image_monitor_last_frame_rgb = np.zeros((40, 60, 3), dtype=np.uint8)
        self.app._image_tracking_map = pd.DataFrame(
            [
                {
                    "x_px": 10.0,
                    "y_px": 12.0,
                    "radius_px": 5.0,
                    "row_index": 1,
                    "col_index": 1,
                    "ordinal": 1,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "seed",
                }
            ]
        )
        self.app._image_freeze_current_seed()
        self.assertIsNotNone(self.app._image_seed_reference_rgb)
        self.assertIsNotNone(self.app._image_seed_map)
        self.assertEqual(len(self.app._image_seed_map), 1)
        self.assertIn("Frozen current frame", self.app._image_status_var.get())

    def test_image_monitor_can_lock_target_from_current_overlay(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {
                    "x_px": 20.0,
                    "y_px": 25.0,
                    "radius_px": 8.0,
                    "row_index": 1,
                    "col_index": 1,
                    "ordinal": 1,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "seed",
                },
                {
                    "x_px": 80.0,
                    "y_px": 90.0,
                    "radius_px": 10.0,
                    "row_index": 2,
                    "col_index": 3,
                    "ordinal": 2,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "seed",
                },
            ]
        )
        ok = self.app._image_select_target_from_frame_xy(84.0, 86.0)
        self.assertTrue(ok)
        self.assertIsNotNone(self.app._image_selected_target)
        self.assertEqual(int(self.app._image_selected_target["row_index"]), 2)
        self.assertEqual(int(self.app._image_selected_target["col_index"]), 3)
        self.assertIn("R2C3", self.app._image_target_status_var.get())

    def test_image_monitor_target_lock_reports_design_match_when_available(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {
                    "x_px": 80.0,
                    "y_px": 90.0,
                    "radius_px": 10.0,
                    "row_index": 2,
                    "col_index": 3,
                    "ordinal": 2,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "seed",
                },
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {
                    "x_px": 120.0,
                    "y_px": 150.0,
                    "radius_px": 11.0,
                    "row_index": 2,
                    "col_index": 3,
                    "ordinal": 7,
                    "size_group": "all",
                    "sample_x_mm": 4.5,
                    "sample_y_mm": 6.25,
                    "projected": False,
                    "source": "design",
                }
            ]
        )
        ok = self.app._image_select_target_from_frame_xy(79.0, 92.0)
        self.assertTrue(ok)
        self.assertIsNotNone(self.app._image_selected_design_target)
        self.assertIn("Design R2C3", self.app._image_target_status_var.get())
        self.assertIn("4.50, 6.25", self.app._image_target_status_var.get())

    def test_image_monitor_updates_locked_target_from_new_overlay(self):
        self.app._image_selected_target = pd.Series(
            {
                "x_px": 50.0,
                "y_px": 60.0,
                "radius_px": 9.0,
                "row_index": 1,
                "col_index": 2,
            }
        )
        self.app._image_tracking_map = pd.DataFrame(
            [
                {
                    "x_px": 61.0,
                    "y_px": 54.0,
                    "radius_px": 9.0,
                    "row_index": 1,
                    "col_index": 2,
                    "ordinal": 1,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "tracking",
                },
                {
                    "x_px": 140.0,
                    "y_px": 120.0,
                    "radius_px": 11.0,
                    "row_index": 2,
                    "col_index": 4,
                    "ordinal": 2,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "tracking",
                },
            ]
        )
        self.app._image_update_selected_target_from_overlay()
        self.assertAlmostEqual(float(self.app._image_selected_target["x_px"]), 61.0, delta=0.1)
        self.assertAlmostEqual(float(self.app._image_selected_target["y_px"]), 54.0, delta=0.1)
        self.assertIn("R1C2", self.app._image_target_status_var.get())

    def test_image_monitor_target_lock_falls_back_to_normalized_design_match(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed"},
                {"x_px": 60.0, "y_px": 15.0, "radius_px": 5.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed"},
                {"x_px": 12.0, "y_px": 60.0, "radius_px": 5.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed"},
                {"x_px": 62.0, "y_px": 65.0, "radius_px": 5.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": 2.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 300.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 3, "ordinal": 3, "size_group": "all", "sample_x_mm": 3.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 1, "ordinal": 4, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 2.0, "projected": False, "source": "design"},
                {"x_px": 300.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 5, "size_group": "all", "sample_x_mm": 3.0, "sample_y_mm": 2.0, "projected": False, "source": "design"},
            ]
        )
        ok = self.app._image_select_target_from_frame_xy(63.0, 62.0)
        self.assertTrue(ok)
        self.assertIsNotNone(self.app._image_selected_design_target)
        self.assertEqual(int(self.app._image_selected_design_target["row_index"]), 2)
        self.assertEqual(int(self.app._image_selected_design_target["col_index"]), 3)
        self.assertIn("Design R2C3", self.app._image_target_status_var.get())

    def test_image_monitor_stage_anchor_uses_current_xy_for_selected_design_target(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
            ]
        )
        self.app._manual_current['x'].set('12.300')
        self.app._manual_current['y'].set('-1.250')

        ok = self.app._image_select_target_from_frame_xy(79.0, 92.0)
        self.assertTrue(ok)
        self.app._image_set_stage_anchor_from_current_xy()

        self.assertIsNotNone(self.app._image_stage_anchor)
        self.assertIn('R2C3', self.app._image_stage_anchor_status_var.get())
        self.assertIn('12.300, -1.250', self.app._image_stage_anchor_status_var.get())

    def test_image_monitor_target_status_includes_projected_stage_xy_after_anchor(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._manual_current['x'].set('12.300')
        self.app._manual_current['y'].set('-1.250')

        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_set_stage_anchor_from_current_xy()
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        text = self.app._image_target_status_var.get()
        self.assertIn('Design R2C4', text)
        self.assertIn('sample (~5.00, 7.00) mm', text)
        self.assertIn('Stage (~12.800, -0.500) mm', text)

    def test_image_monitor_stage_projection_supports_swap_and_invert(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._manual_current['x'].set('12.300')
        self.app._manual_current['y'].set('-1.250')

        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_set_stage_anchor_from_current_xy()
        self.app._image_stage_swap_xy_var.set(True)
        self.app._image_stage_invert_x_var.set(True)
        self.app._image_refresh_stage_projection_status()
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        text = self.app._image_target_status_var.get()
        self.assertIn('Stage (~11.550, -0.750) mm', text)
        self.assertIn('[swap xy, invert x]', self.app._image_stage_anchor_status_var.get())

    def test_image_monitor_stage_affine_calibration_projects_targets(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
            ]
        )

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._manual_current['x'].set('12.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-2.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 20.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._image_solve_stage_affine_calibration()
        self.assertIn('affine solved from 3 points', self.app._image_stage_affine_status_var.get())

        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 20.0))
        text = self.app._image_target_status_var.get()
        self.assertIn('Design R2C2', text)
        self.assertIn('Stage (~12.000, -2.000) mm', text)

    def test_image_monitor_affine_solve_with_insufficient_refs_clears_old_calibration(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_stage_affine_calibration = SimpleNamespace(matrix=np.eye(3))
        self.app._image_stage_calibration_refs = [
            gui.SampleStageReference(0.0, 0.0, 10.0, -5.0),
            gui.SampleStageReference(1.0, 0.0, 12.0, -5.0),
        ]

        self.app._image_solve_stage_affine_calibration()

        self.assertIsNone(self.app._image_stage_affine_calibration)
        self.assertIn('need at least 3 reference points', self.app._image_stage_affine_status_var.get())
        self.assertNotIn('Stage (~', self.app._image_target_status_var.get())

    def test_image_monitor_affine_solve_failure_clears_old_calibration(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_stage_affine_calibration = SimpleNamespace(matrix=np.eye(3))
        self.app._image_stage_calibration_refs = [
            gui.SampleStageReference(0.0, 0.0, 10.0, -5.0),
            gui.SampleStageReference(1.0, 0.0, 12.0, -5.0),
            gui.SampleStageReference(0.0, 1.0, 10.0, -2.0),
        ]

        with mock.patch.object(gui, 'solve_sample_to_stage_affine_calibration', side_effect=RuntimeError('bad affine')):
            self.app._image_solve_stage_affine_calibration()

        self.assertIsNone(self.app._image_stage_affine_calibration)
        self.assertIn('Stage calibration solve failed: bad affine', self.app._image_stage_affine_status_var.get())
        self.assertNotIn('Stage (~', self.app._image_target_status_var.get())

    def test_image_monitor_affine_load_failure_clears_old_calibration(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_stage_affine_calibration = SimpleNamespace(matrix=np.eye(3))

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as tmp:
            tmp.write("{ invalid json")
            bad_path = tmp.name
        try:
            self.assertFalse(self.app._image_load_stage_affine_calibration(path=bad_path))
        finally:
            os.unlink(bad_path)

        self.assertIsNone(self.app._image_stage_affine_calibration)
        self.assertIn('Stage calibration load failed', self.app._image_stage_affine_status_var.get())
        self.assertNotIn('Stage (~', self.app._image_target_status_var.get())

    def test_image_monitor_affine_load_failure_does_not_partially_apply_new_payload(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_stage_calibration_refs = [
            gui.SampleStageReference(0.0, 0.0, 10.0, -5.0),
        ]
        self.app._image_stage_anchor = {
            "stage_x_mm": 12.3,
            "stage_y_mm": -1.25,
            "sample_x_mm": 0.0,
            "sample_y_mm": 0.0,
            "row_index": 1,
            "col_index": 1,
        }
        self.app._image_stage_swap_xy_var.set(True)
        self.app._image_stage_invert_x_var.set(True)
        self.app._image_stage_invert_y_var.set(False)
        self.app._image_stage_affine_calibration = SimpleNamespace(matrix=np.eye(3))

        bad_payload = {
            "refs": [
                {"sample_x_mm": 5.0, "sample_y_mm": 6.0, "stage_x_mm": 20.0, "stage_y_mm": -2.0},
                {"sample_x_mm": 6.0, "sample_y_mm": 6.0, "stage_x_mm": 21.0, "stage_y_mm": -2.0},
                {"sample_x_mm": 5.0, "sample_y_mm": 7.0, "stage_x_mm": 20.0, "stage_y_mm": -1.0},
            ],
            "swap_xy": False,
            "invert_x": False,
            "invert_y": True,
            "anchor": {
                "stage_x_mm": 20.0,
                "stage_y_mm": -2.0,
                "sample_x_mm": 5.0,
                "sample_y_mm": 6.0,
                "row_index": "bad",
                "col_index": 3,
            },
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as tmp:
            json.dump(bad_payload, tmp)
            bad_path = tmp.name
        try:
            self.assertFalse(self.app._image_load_stage_affine_calibration(path=bad_path))
        finally:
            os.unlink(bad_path)

        self.assertIsNone(self.app._image_stage_affine_calibration)
        self.assertEqual(1, len(self.app._image_stage_calibration_refs))
        self.assertEqual(12.3, self.app._image_stage_anchor["stage_x_mm"])
        self.assertTrue(self.app._image_stage_swap_xy_var.get())
        self.assertTrue(self.app._image_stage_invert_x_var.get())
        self.assertFalse(self.app._image_stage_invert_y_var.get())
        self.assertIn('Stage calibration load failed', self.app._image_stage_affine_status_var.get())

    def test_image_monitor_can_copy_projected_anchor_stage_xy_to_manual_target(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._manual_current['x'].set('12.300')
        self.app._manual_current['y'].set('-1.250')

        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_set_stage_anchor_from_current_xy()
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_apply_projected_stage_xy_to_manual_target()

        self.assertEqual(self.app._manual_target['x'].get(), '12.800')
        self.assertEqual(self.app._manual_target['y'].get(), '-0.500')
        self.assertIn('copied to target', self.app._image_target_status_var.get())
        self.assertIn('X=12.800 mm, Y=-0.500 mm', self.app._manual_status_var.get())

    def test_image_monitor_can_copy_projected_affine_stage_xy_to_manual_target(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
            ]
        )

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._manual_current['x'].set('12.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-2.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 20.0))
        self.app._image_add_stage_calibration_point_from_current_target()

        self.app._image_solve_stage_affine_calibration()
        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 20.0))
        self.app._image_apply_projected_stage_xy_to_manual_target()

        self.assertEqual(self.app._manual_target['x'].get(), '12.000')
        self.assertEqual(self.app._manual_target['y'].get(), '-2.000')
        self.assertIn('copied to target', self.app._image_target_status_var.get())
        self.assertIn('X=12.000 mm, Y=-2.000 mm', self.app._manual_status_var.get())

    def test_image_monitor_can_save_and_load_stage_calibration_roundtrip(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
            ]
        )

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._manual_current['x'].set('12.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-2.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 20.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._image_stage_swap_xy_var.set(True)
        self.app._image_stage_invert_x_var.set(True)
        self.app._image_set_stage_anchor_from_current_xy()
        self.app._image_solve_stage_affine_calibration()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            path = tmp.name
        try:
            self.assertTrue(self.app._image_save_stage_affine_calibration(path))
            self.app._image_clear_stage_affine_calibration()
            self.app._image_clear_stage_anchor()
            self.app._image_stage_swap_xy_var.set(False)
            self.app._image_stage_invert_x_var.set(False)
            self.app._image_stage_invert_y_var.set(False)

            self.assertTrue(self.app._image_load_stage_affine_calibration(path))
            self.assertIsNotNone(self.app._image_stage_affine_calibration)
            self.assertTrue(self.app._image_stage_swap_xy_var.get())
            self.assertTrue(self.app._image_stage_invert_x_var.get())
            self.assertIsNotNone(self.app._image_stage_anchor)
            self.assertIn('loaded affine from 3 refs', self.app._image_stage_affine_status_var.get())

            self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 20.0))
            text = self.app._image_target_status_var.get()
            self.assertIn('Stage (~12.000, -2.000) mm', text)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_image_monitor_safe_move_uses_anchor_projected_xy_and_preserves_current_z(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 12.3, "Y": -1.25, "Z": 0.75}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 128, dtype=np.uint8)

        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_move_target_safe()

        motor.move_xyz_safe.assert_called_once()
        kwargs = motor.move_xyz_safe.call_args.kwargs
        self.assertAlmostEqual(kwargs["x_mm"], 12.8, places=6)
        self.assertAlmostEqual(kwargs["y_mm"], -0.5, places=6)
        self.assertAlmostEqual(kwargs["z_mm"], 0.75, places=6)
        self.assertEqual(kwargs["current_positions"], {"X": 12.3, "Y": -1.25, "Z": 0.75})
        self.assertEqual(self.app._manual_target["x"].get(), "12.800")
        self.assertEqual(self.app._manual_target["y"].get(), "-0.500")
        self.assertEqual(self.app._manual_target["z"].get(), "0.750")
        self.assertIsNotNone(self.app._image_stage_anchor)
        self.assertAlmostEqual(self.app._image_stage_anchor["stage_x_mm"], 12.8, places=6)
        self.assertAlmostEqual(self.app._image_stage_anchor["stage_y_mm"], -0.5, places=6)
        self.assertEqual(self.app._image_stage_anchor["row_index"], 2)
        self.assertEqual(self.app._image_stage_anchor["col_index"], 4)
        self.assertIn("safe move sent; anchor updated", self.app._image_target_status_var.get())
        self.assertIn("Safe target move complete", self.app._manual_status_var.get())
        self.assertIsNotNone(self.app._image_pending_roi_verification)
        self.assertEqual(self.app._image_pending_roi_verification["row_index"], 2)
        self.assertEqual(self.app._image_pending_roi_verification["col_index"], 4)

    def test_image_monitor_safe_move_uses_affine_projected_xy(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 10.0, "radius_px": 5.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 10.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 20.0, "y_px": 20.0, "radius_px": 5.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 1, "col_index": 2, "ordinal": 2, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 0.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 1, "ordinal": 3, "size_group": "all", "sample_x_mm": 0.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
                {"x_px": 200.0, "y_px": 200.0, "radius_px": 8.0, "row_index": 2, "col_index": 2, "ordinal": 4, "size_group": "all", "sample_x_mm": 1.0, "sample_y_mm": 1.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 10.0, "Y": -5.0, "Z": 0.4}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 140, dtype=np.uint8)

        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._manual_current['x'].set('12.000')
        self.app._manual_current['y'].set('-5.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 10.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._manual_current['x'].set('10.000')
        self.app._manual_current['y'].set('-2.000')
        self.assertTrue(self.app._image_select_target_from_frame_xy(10.0, 20.0))
        self.app._image_add_stage_calibration_point_from_current_target()
        self.app._image_solve_stage_affine_calibration()

        self.assertTrue(self.app._image_select_target_from_frame_xy(20.0, 20.0))
        self.app._image_move_target_safe()

        kwargs = motor.move_xyz_safe.call_args.kwargs
        self.assertAlmostEqual(kwargs["x_mm"], 12.0, places=6)
        self.assertAlmostEqual(kwargs["y_mm"], -2.0, places=6)
        self.assertAlmostEqual(kwargs["z_mm"], 0.4, places=6)
        self.assertEqual(self.app._manual_target["x"].get(), "12.000")
        self.assertEqual(self.app._manual_target["y"].get(), "-2.000")
        self.assertEqual(self.app._manual_target["z"].get(), "0.400")
        self.assertIsNotNone(self.app._image_stage_anchor)
        self.assertAlmostEqual(self.app._image_stage_anchor["stage_x_mm"], 12.0, places=6)
        self.assertAlmostEqual(self.app._image_stage_anchor["stage_y_mm"], -2.0, places=6)
        self.assertEqual(self.app._image_stage_anchor["row_index"], 2)
        self.assertEqual(self.app._image_stage_anchor["col_index"], 2)
        self.assertIn("safe move sent; anchor updated", self.app._image_target_status_var.get())
        self.assertIn("Safe target move complete", self.app._manual_status_var.get())
        self.assertIsNotNone(self.app._image_pending_roi_verification)
        self.assertEqual(self.app._image_pending_roi_verification["row_index"], 2)
        self.assertEqual(self.app._image_pending_roi_verification["col_index"], 2)

    def test_image_monitor_process_pending_roi_verification_updates_status(self):
        frame_rgb = np.full((120, 160, 3), 150, dtype=np.uint8)
        target = pd.Series(
            {
                "x_px": 40.0,
                "y_px": 50.0,
                "radius_px": 12.0,
                "row_index": 2,
                "col_index": 4,
            }
        )
        self.assertTrue(self.app._image_arm_post_move_roi_verification(frame_rgb, target, wait_frames=0))
        with mock.patch.object(
            gui,
            "verify_roi_revisit",
            return_value=mock.Mock(
                score=0.91,
                matched_center_x_px=41.0,
                matched_center_y_px=49.0,
                dx_px=1.0,
                dy_px=-1.0,
                template_size_px=48,
                search_radius_px=24,
            ),
        ):
            self.assertTrue(self.app._image_process_pending_roi_verification(frame_rgb))

        self.assertIsNone(self.app._image_pending_roi_verification)
        self.assertIn("ROI verify: OK for R2C4", self.app._image_roi_verify_status_var.get())
        self.assertIn("score=0.910", self.app._image_roi_verify_status_var.get())
        self.assertIn("post-move ROI verification matched", self.app._image_hint_var.get())
        self.assertTrue(self.app._image_pending_roi_seed_promotion)

    def test_image_monitor_process_pending_roi_verification_weak_blocks_future_safe_moves(self):
        frame_rgb = np.full((120, 160, 3), 150, dtype=np.uint8)
        target = pd.Series(
            {
                "x_px": 40.0,
                "y_px": 50.0,
                "radius_px": 12.0,
                "row_index": 2,
                "col_index": 4,
            }
        )
        self.assertTrue(self.app._image_arm_post_move_roi_verification(frame_rgb, target, wait_frames=0))
        with mock.patch.object(
            gui,
            "verify_roi_revisit",
            return_value=mock.Mock(
                score=0.12,
                matched_center_x_px=60.0,
                matched_center_y_px=80.0,
                dx_px=20.0,
                dy_px=30.0,
                template_size_px=48,
                search_radius_px=24,
            ),
        ):
            self.assertTrue(self.app._image_process_pending_roi_verification(frame_rgb))

        self.assertIsNone(self.app._image_pending_roi_verification)
        self.assertFalse(self.app._image_pending_roi_seed_promotion)
        self.assertTrue(self.app._image_move_requires_review_after_weak_roi)
        self.assertIn("ROI verify: weak for R2C4", self.app._image_roi_verify_status_var.get())
        self.assertIn("further safe moves are blocked", self.app._image_hint_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual(gui.CLR_RED, self.app._image_move_gate_label.cget("fg"))
        self.assertEqual("disabled", str(self.app._image_move_target_button.cget("state")))
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertEqual("weak", self.app._image_last_roi_verify_result["state"])
        self.assertEqual(2, self.app._image_last_roi_verify_result["row_index"])
        self.assertEqual(4, self.app._image_last_roi_verify_result["col_index"])
        self.assertAlmostEqual(0.12, self.app._image_last_roi_verify_result["score"], places=6)
        self.assertAlmostEqual(20.0, self.app._image_last_roi_verify_result["dx_px"], places=6)
        self.assertAlmostEqual(30.0, self.app._image_last_roi_verify_result["dy_px"], places=6)

    def test_image_monitor_apply_pending_roi_seed_promotion_promotes_current_overlay(self):
        self.app._image_monitor_last_frame_rgb = np.full((100, 120, 3), 90, dtype=np.uint8)
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 40.0, "y_px": 50.0, "radius_px": 12.0, "row_index": 2, "col_index": 4, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 70.0, "y_px": 50.0, "radius_px": 12.0, "row_index": 2, "col_index": 5, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_pending_roi_seed_promotion = True
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_allow_stale_high_override_var.set(True)

        self.assertTrue(self.app._image_apply_pending_roi_seed_promotion())

        self.assertFalse(self.app._image_pending_roi_seed_promotion)
        self.assertFalse(self.app._image_move_requires_review_after_weak_roi)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertIsNotNone(self.app._image_seed_reference_rgb)
        self.assertEqual(self.app._image_seed_reference_rgb.shape, (100, 120, 3))
        self.assertIsNotNone(self.app._image_seed_map)
        self.assertEqual(len(self.app._image_seed_map), 2)
        self.assertIn("ROI-verified frame promoted to tracking seed", self.app._image_status_var.get())
        self.assertIn("promoted to the new frozen seed", self.app._image_hint_var.get())
        self.assertEqual("Move gate: clear", self.app._image_move_gate_status_var.get())
        self.assertEqual(gui.CLR_GREEN, self.app._image_move_gate_label.cget("fg"))

    def test_image_monitor_safe_move_is_blocked_after_weak_roi_until_seed_refresh(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        self.app.motor = motor
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 128, dtype=np.uint8)
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        self.app._image_move_target_safe()

        motor.move_xyz_safe.assert_not_called()
        self.assertIn("Safe move blocked", self.app._manual_status_var.get())
        self.assertIn("blocks the next safe move", self.app._image_hint_var.get())

    def test_image_monitor_override_weak_roi_block_allows_next_safe_move(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 12.3, "Y": -1.25, "Z": 0.75}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 128, dtype=np.uint8)
        self.app._image_monitor_last_frame_time_s = 112.5
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.217,
            "dx_px": 6.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 100.0,
        }

        with mock.patch.object(gui.messagebox, "askyesno", return_value=True) as askyesno, \
             mock.patch.object(gui.time, "time", return_value=112.5):
            self.app._image_override_weak_roi_block()
        self.assertTrue(self.app._image_move_requires_review_after_weak_roi)
        self.assertTrue(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertIn("Weak ROI block overridden", self.app._manual_status_var.get())
        self.assertIn("one safe move only", self.app._image_hint_var.get())
        override_message = askyesno.call_args.args[1]
        self.assertIn("Current move gate: Move gate: weak ROI blocked.", override_message)
        self.assertIn("Current live frame: 1970-01-01 00:01:52 UTC.", override_message)
        self.assertIn("Frame vs weak ROI verify gap: 12.5 s.", override_message)
        self.assertIn("Frame freshness bucket: aging (review with caution).", override_message)
        self.assertIn("Override risk: moderate (overlay may still be usable, but operator review matters).", override_message)
        self.assertIn("Recommended action: inspect the overlay carefully before overriding.", override_message)
        self.assertIn("Locked target: R2C4", override_message)
        self.assertIn("Design sample: (5.00, 7.00) mm", override_message)
        self.assertIn("Projected stage XY: (12.800, -0.500) mm", override_message)
        self.assertIn("Projection source: stage anchor mapping", override_message)
        self.assertIn("R2C4", override_message)
        self.assertIn("12.5 s ago", override_message)
        self.assertIn("1970-01-01 00:01:40 UTC", override_message)
        self.assertIn("score=0.217", override_message)
        self.assertIn("dx=+6.0", override_message)
        self.assertIn("dy=-4.0", override_message)
        self.assertEqual(
            "Move gate: weak ROI blocked (one-shot override armed)",
            self.app._image_move_gate_status_var.get(),
        )
        self.assertEqual(gui.CLR_ORANGE, self.app._image_move_gate_label.cget("fg"))
        self.assertEqual("normal", str(self.app._image_move_target_button.cget("state")))
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.app._image_move_target_safe()

        self.assertTrue(self.app._image_move_requires_review_after_weak_roi)
        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertIn("Safe target move complete", self.app._manual_status_var.get())
        motor.move_xyz_safe.assert_called_once()
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual(gui.CLR_RED, self.app._image_move_gate_label.cget("fg"))
        self.assertEqual("disabled", str(self.app._image_move_target_button.cget("state")))
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_successful_safe_move_resets_stale_high_override_opt_in(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 12.3, "Y": -1.25, "Z": 0.75}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 128, dtype=np.uint8)
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_allow_one_safe_move_after_weak_roi_override = True
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.121,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_refresh_move_gate_status()

        self.app._image_move_target_safe()

        motor.move_xyz_safe.assert_called_once()
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("Safe target move complete", self.app._manual_status_var.get())

    def test_image_monitor_override_weak_roi_block_is_noop_when_not_active(self):
        self.app._image_move_requires_review_after_weak_roi = False
        self.app._image_allow_one_safe_move_after_weak_roi_override = False

        self.app._image_override_weak_roi_block()

        self.assertIn("not active", self.app._manual_status_var.get())
        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)

    def test_image_monitor_override_weak_roi_block_is_one_shot(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 12.3, "Y": -1.25, "Z": 0.75}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_monitor_last_frame_rgb = np.full((120, 160, 3), 128, dtype=np.uint8)
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
            self.app._image_override_weak_roi_block()
        self.app._image_move_target_safe()
        self.app._image_move_target_safe()

        motor.move_xyz_safe.assert_called_once()
        self.assertIn("Safe move blocked", self.app._manual_status_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())

    def test_image_monitor_override_weak_roi_block_can_be_cancelled(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_allow_one_safe_move_after_weak_roi_override = False
        self.app._image_monitor_last_frame_time_s = 131.5
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.205,
            "dx_px": 4.0,
            "dy_px": -3.0,
            "search_radius_px": 18,
            "verify_time_s": 120.0,
        }
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_refresh_move_gate_status()

        with mock.patch.object(gui.messagebox, "askyesno", return_value=False) as askyesno, \
             mock.patch.object(gui.time, "time", return_value=131.5):
            self.app._image_override_weak_roi_block()

        self.assertTrue(self.app._image_move_requires_review_after_weak_roi)
        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertIn("override cancelled", self.app._manual_status_var.get())
        cancel_message = askyesno.call_args.args[1]
        self.assertIn("Current move gate: Move gate: weak ROI blocked.", cancel_message)
        self.assertIn("Current live frame: 1970-01-01 00:02:11 UTC.", cancel_message)
        self.assertIn("Frame vs weak ROI verify gap: 11.5 s.", cancel_message)
        self.assertIn("Frame freshness bucket: aging (review with caution).", cancel_message)
        self.assertIn("Override risk: moderate (overlay may still be usable, but operator review matters).", cancel_message)
        self.assertIn("Recommended action: inspect the overlay carefully before overriding.", cancel_message)
        self.assertIn("Locked target: R2C4", cancel_message)
        self.assertIn("Design sample: (5.00, 7.00) mm", cancel_message)
        self.assertIn("Projected stage XY: (12.800, -0.500) mm", cancel_message)
        self.assertIn("Projection source: stage anchor mapping", cancel_message)
        self.assertIn("R2C4", cancel_message)
        self.assertIn("11.5 s ago", cancel_message)
        self.assertIn("1970-01-01 00:02:00 UTC", cancel_message)
        self.assertIn("score=0.205", cancel_message)

    def test_image_monitor_override_weak_roi_block_dialog_shows_affine_projection_source(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
                {"x_px": 100.0, "y_px": 140.0, "radius_px": 8.0, "row_index": 3, "col_index": 3, "ordinal": 3, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 209.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.205,
            "dx_px": 4.0,
            "dy_px": -3.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_stage_affine_calibration = gui.solve_sample_to_stage_affine_calibration(
            [
                SimpleNamespace(sample_x_mm=4.5, sample_y_mm=6.25, stage_x_mm=12.3, stage_y_mm=-1.25),
                SimpleNamespace(sample_x_mm=5.0, sample_y_mm=6.25, stage_x_mm=12.8, stage_y_mm=-1.25),
                SimpleNamespace(sample_x_mm=4.5, sample_y_mm=7.0, stage_x_mm=12.3, stage_y_mm=-0.5),
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        with mock.patch.object(gui.messagebox, "askyesno", return_value=False) as askyesno, \
             mock.patch.object(gui.time, "time", return_value=209.0):
            self.app._image_override_weak_roi_block()

        affine_message = askyesno.call_args.args[1]
        self.assertIn("Current move gate: Move gate: weak ROI blocked.", affine_message)
        self.assertIn("Current live frame: 1970-01-01 00:03:29 UTC.", affine_message)
        self.assertIn("Frame vs weak ROI verify gap: 9.0 s.", affine_message)
        self.assertIn("Frame freshness bucket: fresh (good for visual comparison).", affine_message)
        self.assertIn("Override risk: low (live frame and weak verify are still closely aligned).", affine_message)
        self.assertIn("Recommended action: override is reasonable if the locked target still looks right by eye.", affine_message)
        self.assertIn("Locked target: R2C4", affine_message)
        self.assertIn("Design sample: (5.00, 7.00) mm", affine_message)
        self.assertIn("Projected stage XY: (12.800, -0.500) mm", affine_message)
        self.assertIn("Projection source: affine calibration", affine_message)
        self.assertIn("9.0 s ago", affine_message)
        self.assertIn("1970-01-01 00:03:20 UTC", affine_message)

    def test_image_monitor_stop_resets_move_gate_status(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_allow_one_safe_move_after_weak_roi_override = True
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()

        self.app._image_monitor_stop()

        self.assertEqual("Move gate: clear", self.app._image_move_gate_status_var.get())
        self.assertEqual(gui.CLR_GREEN, self.app._image_move_gate_label.cget("fg"))
        self.assertEqual("normal", str(self.app._image_move_target_button.cget("state")))
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())

    def test_image_monitor_clear_seed_resets_stale_high_override_opt_in(self):
        self.app._image_seed_reference_rgb = np.full((20, 30, 3), 90, dtype=np.uint8)
        self.app._image_seed_map = pd.DataFrame(
            [
                {"x_px": 10.0, "y_px": 11.0, "radius_px": 4.0, "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 1,
            "col_index": 1,
            "score": 0.191,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)

        self.app._image_clear_seed()

        self.assertIsNone(self.app._image_seed_reference_rgb)
        self.assertIsNone(self.app._image_seed_map)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("Cleared frozen tracking seed", self.app._image_status_var.get())

    def test_image_monitor_clear_target_resets_stale_high_override_opt_in(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_clear_target()

        self.assertIsNone(self.app._image_selected_target)
        self.assertIsNone(self.app._image_selected_design_target)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Target: not locked", self.app._image_target_status_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_selecting_new_target_resets_stale_high_override_opt_in(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))

        self.assertEqual(int(self.app._image_selected_target["row_index"]), 2)
        self.assertEqual(int(self.app._image_selected_target["col_index"]), 4)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("R2C4", self.app._image_target_status_var.get())

    def test_image_monitor_setting_stage_anchor_resets_stale_high_override_opt_in(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
            ]
        )
        self.app._manual_current['x'].set('12.300')
        self.app._manual_current['y'].set('-1.250')
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(79.0, 92.0))
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_set_stage_anchor_from_current_xy()

        self.assertIsNotNone(self.app._image_stage_anchor)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_clearing_stage_anchor_resets_stale_high_override_opt_in(self):
        self.app._image_selected_target = {"row_index": 2, "col_index": 3, "x_px": 79.0, "y_px": 92.0, "radius_px": 9.0}
        self.app._image_stage_anchor = {
            "stage_x_mm": 12.3,
            "stage_y_mm": -1.25,
            "sample_x_mm": 4.5,
            "sample_y_mm": 6.25,
            "row_index": 2,
            "col_index": 3,
        }
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_clear_stage_anchor()

        self.assertIsNone(self.app._image_stage_anchor)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_stage_projection_toggle_resets_stale_high_override_opt_in(self):
        self.app._image_selected_target = {"row_index": 2, "col_index": 3, "x_px": 79.0, "y_px": 92.0, "radius_px": 9.0}
        self.app._image_stage_anchor = {
            "stage_x_mm": 12.3,
            "stage_y_mm": -1.25,
            "sample_x_mm": 4.5,
            "sample_y_mm": 6.25,
            "row_index": 2,
            "col_index": 3,
        }
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 3,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_stage_swap_xy_var.set(True)
        self.app._image_on_stage_projection_controls_changed()

        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))
        self.assertIn("[swap xy]", self.app._image_stage_anchor_status_var.get())

    def test_image_monitor_solving_affine_resets_stale_high_override_opt_in(self):
        self.app._image_selected_target = {"row_index": 2, "col_index": 2, "x_px": 20.0, "y_px": 20.0, "radius_px": 5.0}
        self.app._image_stage_calibration_refs = [
            gui.SampleStageReference(0.0, 0.0, 10.0, -5.0),
            gui.SampleStageReference(1.0, 0.0, 12.0, -5.0),
            gui.SampleStageReference(0.0, 1.0, 10.0, -2.0),
        ]
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 2,
            "score": 0.412,
            "dx_px": 5.0,
            "dy_px": -4.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()
        self.assertEqual("normal", str(self.app._image_override_weak_roi_button.cget("state")))

        self.app._image_solve_stage_affine_calibration()

        self.assertIsNotNone(self.app._image_stage_affine_calibration)
        self.assertFalse(self.app._image_allow_stale_high_override_var.get())
        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_override_weak_roi_block_dialog_shows_high_risk_recommendation(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.191,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()

        with mock.patch.object(gui.messagebox, "askyesno", return_value=False) as askyesno, \
             mock.patch.object(gui.time, "time", return_value=240.0):
            self.app._image_override_weak_roi_block()

        stale_message = askyesno.call_args.args[1]
        self.assertIn(
            "WARNING: the current weak-ROI evidence is stale enough that refreshing the seed is strongly preferred before overriding.",
            stale_message,
        )
        self.assertIn("Frame vs weak ROI verify gap: 40.0 s.", stale_message)
        self.assertIn("Frame freshness bucket: stale (visual lock may be too old).", stale_message)
        self.assertIn("Override risk: high (reacquire the seed unless the live overlay is unmistakably correct).", stale_message)
        self.assertIn("Recommended action: refresh the seed first unless the current overlay is obviously correct.", stale_message)

    def test_image_monitor_override_weak_roi_block_requires_second_confirm_for_high_risk(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.191,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_allow_stale_high_override_var.set(True)
        self.app._image_refresh_move_gate_status()

        with mock.patch.object(gui.messagebox, "askyesno", side_effect=[True, False]) as askyesno, \
             mock.patch.object(gui.time, "time", return_value=240.0):
            self.app._image_override_weak_roi_block()

        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertIn("stale high-risk confirmation", self.app._manual_status_var.get())
        self.assertEqual(2, askyesno.call_count)
        second_message = askyesno.call_args_list[1].args[1]
        self.assertIn("stale/high risk", second_message)
        self.assertIn("one-shot override", second_message)

    def test_image_monitor_refresh_move_gate_disables_override_for_stale_high_without_opt_in(self):
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_allow_one_safe_move_after_weak_roi_override = False
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.191,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_allow_stale_high_override_var.set(False)

        self.app._image_refresh_move_gate_status()

        self.assertEqual("Move gate: weak ROI blocked", self.app._image_move_gate_status_var.get())
        self.assertEqual("disabled", str(self.app._image_move_target_button.cget("state")))
        self.assertEqual("disabled", str(self.app._image_override_weak_roi_button.cget("state")))

    def test_image_monitor_override_weak_roi_block_requires_opt_in_for_stale_high(self):
        self.app._image_tracking_map = pd.DataFrame(
            [
                {"x_px": 79.0, "y_px": 92.0, "radius_px": 9.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
                {"x_px": 95.0, "y_px": 104.0, "radius_px": 9.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "tracking"},
            ]
        )
        self.app._image_design_map = pd.DataFrame(
            [
                {"x_px": 100.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 3, "ordinal": 1, "size_group": "all", "sample_x_mm": 4.5, "sample_y_mm": 6.25, "projected": False, "source": "design"},
                {"x_px": 140.0, "y_px": 100.0, "radius_px": 8.0, "row_index": 2, "col_index": 4, "ordinal": 2, "size_group": "all", "sample_x_mm": 5.0, "sample_y_mm": 7.0, "projected": False, "source": "design"},
            ]
        )
        self.app._image_move_requires_review_after_weak_roi = True
        self.app._image_monitor_last_frame_time_s = 240.0
        self.app._image_last_roi_verify_result = {
            "state": "weak",
            "row_index": 2,
            "col_index": 4,
            "score": 0.191,
            "dx_px": 8.0,
            "dy_px": -6.0,
            "search_radius_px": 18,
            "verify_time_s": 200.0,
        }
        self.app._image_stage_anchor = {
            'stage_x_mm': 12.3,
            'stage_y_mm': -1.25,
            'sample_x_mm': 4.5,
            'sample_y_mm': 6.25,
            'row_index': 2,
            'col_index': 3,
        }
        self.assertTrue(self.app._image_select_target_from_frame_xy(95.0, 104.0))
        self.app._image_allow_stale_high_override_var.set(False)
        self.app._image_refresh_move_gate_status()

        with mock.patch.object(gui.messagebox, "askyesno") as askyesno:
            self.app._image_override_weak_roi_block()

        askyesno.assert_not_called()
        self.assertFalse(self.app._image_allow_one_safe_move_after_weak_roi_override)
        self.assertIn("disabled until Allow stale/high override is enabled", self.app._manual_status_var.get())
        self.assertIn("blocked by default", self.app._image_hint_var.get())

    def test_image_monitor_tick_prefers_frozen_seed_tracking_when_available(self):
        frame_rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        self.app._image_detector_var.set("live")
        self.app._image_monitor_running = True
        self.app._image_monitor_frame_idx = 0
        self.app._image_monitor_last_overlay_bgr = None
        self.app._image_seed_reference_rgb = frame_rgb.copy()
        self.app._image_seed_map = pd.DataFrame(
            [
                {
                    "x_px": 40.0,
                    "y_px": 50.0,
                    "radius_px": 12.0,
                    "row_index": 1,
                    "col_index": 1,
                    "ordinal": 1,
                    "size_group": "all",
                    "sample_x_mm": None,
                    "sample_y_mm": None,
                    "projected": False,
                    "source": "seed",
                }
            ]
        )

        class FakeCap:
            def read(self_inner):
                return True, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        self.app._image_monitor_cap = FakeCap()
        self.app.after = lambda *args, **kwargs: None
        self.app._render_image_monitor_frame = lambda frame_bgr: None

        with mock.patch.object(
            gui,
            "compute_ecc_affine_registration",
            return_value=(np.eye(3, dtype=np.float32), 0.97),
        ) as mocked_ecc, mock.patch.object(
            gui,
            "refine_circular_electrode_map_rgb",
            side_effect=lambda frame_rgb, seed_detections, **kwargs: seed_detections.copy(),
        ) as mocked_refine:
            self.app._image_monitor_tick()

        mocked_ecc.assert_called_once()
        mocked_refine.assert_called_once()
        self.assertIn("frozen seed", self.app._image_detection_var.get())
        self.assertIn("frozen-seed tracking", self.app._image_hint_var.get())

    def test_monitor_eis_placeholder_text_explains_peis_buffering(self):
        self.app._monitor_reset()
        self.app._apply_monitor_event(
            "step",
            {"label": "manual_rapid", "step": "peis", "message": "PEIS started"},
        )
        self.app._apply_monitor_event(
            "device_live",
            {
                "label": "manual_rapid",
                "values": {
                    "elapsed_s": 9.0,
                    "frequency_hz": 10.82,
                    "buffer_bytes": 56,
                    "ewe_v": 0.2949,
                    "current_a": 2.7e-5,
                },
            },
        )

        text = self.app._monitor_eis_placeholder_text()
        self.assertIn("buffering PEIS data", text)
        self.assertIn("Nyquist points will appear", text)
        self.assertIn("10.8", text)

    def test_manual_quick_eis_uses_configured_frequency_bounds(self):
        captured = {}

        class FakeBioLogic:
            def get_live_values(self):
                return {
                    "elapsed_s": 0.1,
                    "frequency_hz": 10.0,
                    "ewe_v": 0.3,
                    "current_a": 1e-6,
                }

            def run_peis(self, **kwargs):
                captured.update(kwargs)
                return np.array([[1000.0, 10.0, 1.0], [100.0, 10.5, 0.8]])

        self.app.bl = FakeBioLogic()
        self.app._quick_eis["peis_f_high"].set("200000")
        self.app._quick_eis["peis_f_low"].set("0.5")

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app._analyze_manual_sequence_result = lambda *args, **kwargs: None
            self.app._manual_run_quick_eis()

        self.assertEqual(captured["f_high"], 200000.0)
        self.assertEqual(captured["f_low"], 0.5)

    def test_manual_quick_rapid_eis_uses_configured_frequency_bounds(self):
        captured = {}

        class FakeBioLogic:
            def get_live_values(self):
                return {
                    "elapsed_s": 0.1,
                    "frequency_hz": 10.0,
                    "ewe_v": 0.3,
                    "current_a": 1e-6,
                }

        def fake_rapid_eis_sequence(**kwargs):
            captured.update(kwargs)
            return SequenceResult(
                measurement_mode="rapid_eis",
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs["v_dc"], 1e-6]]),
                pre_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_post.txt"),
            )

        self.app.bl = FakeBioLogic()
        self.app._quick_rapid["peis_f_high"].set("250000")
        self.app._quick_rapid["peis_f_low"].set("0.25")
        measurement_sequence.rapid_eis_sequence = fake_rapid_eis_sequence

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app._analyze_manual_sequence_result = lambda *args, **kwargs: None
            self.app._manual_run_quick_rapid_eis()

        self.assertEqual(captured["peis_f_high"], 250000.0)
        self.assertEqual(captured["peis_f_low"], 0.25)

    def test_draw_line_plot_renders_single_nyquist_point(self):
        self.app.update_idletasks()
        self.app._draw_line_plot(
            self.app._eis_canvas,
            [(1500.0, 250.0)],
            title="Impedance Monitor",
            x_label="Re(Z) (Ohm)",
            y_label="-Im(Z) (Ohm)",
            line_color=gui.CLR_GOLD,
        )
        items = self.app._eis_canvas.find_all()
        item_types = [self.app._eis_canvas.type(item) for item in items]
        texts = [
            self.app._eis_canvas.itemcget(item, "text")
            for item in items
            if self.app._eis_canvas.type(item) == "text"
        ]
        self.assertIn("oval", item_types)
        self.assertNotIn("Waiting for live data...", texts)

    def test_sanitize_plot_points_filters_non_finite_and_extreme_nyquist_points(self):
        sanitized, dropped = self.app._sanitize_plot_points(
            [
                (1500.0, 250.0),
                (float("nan"), 20.0),
                (30.0, float("inf")),
                (1.0e20, 5.0),
            ]
        )
        self.assertEqual(dropped, 3)
        self.assertEqual(sanitized, [(1500.0, 250.0)])
