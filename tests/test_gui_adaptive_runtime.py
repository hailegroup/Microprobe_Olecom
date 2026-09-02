# -*- coding: utf-8 -*-
import os
import sys
import shutil
import tempfile
import threading
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
        self.app = gui.MicroprobGUI(
            enable_background_polls=False, enable_file_logging=False, enable_xy_correction_csv_log=False,
        )
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

    def _flaky_normal_sequence(self, fail_times, message=None):
        # Simulates the exact failure mode seen in practice: EC-Lab OLE-COM
        # LoadSettings throwing RPC_E_SERVERFAULT (-2147417851) partway
        # through a run, which _is_olecom_transient_error must recognize
        # (it matches on 'loadsettings' as a substring) so _run_worker's
        # row-level retry kicks in instead of stopping the whole run.
        message = message or (
            "LoadSettings failed (-2147417851) for .../olecom_quick_peis.mps; "
            "last MeasureStatus=(...). EC-Lab may still be flushing a "
            "previous buffer or holding a stale COM channel."
        )
        state = {"calls": 0}

        def fake(**kwargs):
            state["calls"] += 1
            if state["calls"] <= fail_times:
                raise RuntimeError(message)
            return SequenceResult(
                measurement_mode="normal_eis",
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis_only.txt"),
            )

        return fake, state

    def test_run_worker_retries_row_after_transient_olecom_error_then_succeeds(self):
        # SkipCA routes straight to normal_sequence (measurement_sequence.py
        # normal_eis_sequence -> driver_biologic_olecom.py::run_peis ->
        # load_and_run), the exact path a real LoadSettings failure was
        # observed on -- unlike the OLE-COM hybrid branch
        # (_run_olecom_hybrid_sequence), this path previously had no
        # transient-error recovery at all, so one failure stopped the run.
        fake_normal, state = self._flaky_normal_sequence(fail_times=1)
        gui.MODS["normal_sequence"] = fake_normal
        with mock.patch.object(
            gui.MicroprobGUI, "_recover_olecom_after_error"
        ) as recover_mock:
            with tempfile.TemporaryDirectory() as tmpdir:
                self.app._result_dir.set(tmpdir)
                row = self._make_row("SKIPCA_T300_E1_V+0.000", 0.0)
                row["SkipCA"] = 1
                self.app.condition_df = pd.DataFrame([row])
                self.app._run_worker()
            recover_mock.assert_called_once()
        self.assertEqual(state["calls"], 2)
        self.assertEqual(self.app._status_var.get(), "Done")

    def test_run_worker_stops_run_after_exhausting_olecom_retries(self):
        fake_normal, state = self._flaky_normal_sequence(fail_times=99)
        gui.MODS["normal_sequence"] = fake_normal
        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            row = self._make_row("SKIPCA_T300_E1_V+0.000", 0.0)
            row["SkipCA"] = 1
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()
        # max_row_attempts is 2 -- the row must not be retried forever.
        self.assertEqual(state["calls"], 2)
        self.assertEqual(self.app._status_var.get(), "Error - stopped")

    def test_run_worker_does_not_retry_non_transient_row_error(self):
        fake_normal, state = self._flaky_normal_sequence(
            fail_times=99, message="unrelated ValueError, not an OLE-COM issue"
        )
        gui.MODS["normal_sequence"] = fake_normal
        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            row = self._make_row("SKIPCA_T300_E1_V+0.000", 0.0)
            row["SkipCA"] = 1
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()
        self.assertEqual(state["calls"], 1)
        self.assertEqual(self.app._status_var.get(), "Error - stopped")

    def test_ramp_down_furnace_after_run_skipped_when_toggle_off(self):
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(False)
        self.app._ramp_down_furnace_after_run('run completed')
        self.app.tc.set_temperature.assert_not_called()
        self.app.tc.set_ramp_rate.assert_not_called()

    def test_ramp_down_furnace_after_run_skipped_without_controller(self):
        self.app.tc = None
        self.app._run_ramp_down_on_done_var.set(True)
        # Must not raise even though there is no controller to command.
        self.app._ramp_down_furnace_after_run('run completed')

    def test_ramp_down_furnace_after_run_commands_configured_setpoint(self):
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(True)
        self.app._run_ramp_down_end_temp_c_var.set('25.0')
        self.app._run_ramp_down_end_ramp_rate_var.set('7.5')

        self.app._ramp_down_furnace_after_run('run completed')

        self.app.tc.set_ramp_rate.assert_called_once_with(7.5)
        self.app.tc.set_temperature.assert_called_once_with(25.0)
        self.assertEqual(self.app._active_temperature_target_c, 25.0)

    def test_ramp_down_furnace_after_run_invalid_fields_do_not_raise_or_command(self):
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(True)
        self.app._run_ramp_down_end_temp_c_var.set('not-a-number')
        self.app._ramp_down_furnace_after_run('run completed')
        self.app.tc.set_temperature.assert_not_called()

    def test_ramp_down_furnace_after_run_controller_failure_does_not_raise(self):
        self.app.tc = mock.Mock()
        self.app.tc.set_ramp_rate.side_effect = RuntimeError("serial port busy")
        self.app._run_ramp_down_on_done_var.set(True)
        # Must not propagate -- a ramp-down failure can never be allowed to
        # crash the worker thread or mask the run's real outcome.
        self.app._ramp_down_furnace_after_run('run completed')

    def test_run_worker_ramps_down_after_successful_run(self):
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(True)
        self.app._run_ramp_down_end_temp_c_var.set('25.0')
        self.app._run_ramp_down_end_ramp_rate_var.set('5.0')
        gui.MODS["normal_sequence"] = self._flaky_normal_sequence(fail_times=0)[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            row = self._make_row("RAMP_T300_E1_V+0.000", 0.0)
            row["SkipCA"] = 1
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()
        self.assertEqual(self.app._status_var.get(), "Done")
        self.app.tc.set_temperature.assert_called_once_with(25.0)
        self.assertFalse(self.app.running)

    def test_run_worker_ramps_down_even_when_row_fails(self):
        # Mirrors test_run_worker_does_not_retry_non_transient_row_error's
        # failure setup, but asserts the furnace still gets commanded down
        # -- the whole point of this feature is that it must not depend on
        # the run having succeeded.
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(True)
        self.app._run_ramp_down_end_temp_c_var.set('25.0')
        fake_normal, state = self._flaky_normal_sequence(
            fail_times=99, message="unrelated ValueError, not an OLE-COM issue"
        )
        gui.MODS["normal_sequence"] = fake_normal
        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            row = self._make_row("RAMPFAIL_T300_E1_V+0.000", 0.0)
            row["SkipCA"] = 1
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()
        self.assertEqual(self.app._status_var.get(), "Error - stopped")
        self.app.tc.set_temperature.assert_called_once_with(25.0)
        self.assertFalse(self.app.running)

    def test_run_worker_ramps_down_even_on_unexpected_exception_in_setup(self):
        # The core safety guarantee: even a completely uncaught exception
        # (not just a per-row failure _run_worker_impl already catches
        # itself) must still reach the furnace ramp-down and re-enable the
        # Start/Stop buttons, not silently kill the worker thread.
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(True)
        self.app._run_ramp_down_end_temp_c_var.set('25.0')
        self.app.running = True

        def _boom():
            raise RuntimeError("simulated unexpected failure before the per-row loop")

        self.app._run_worker_impl = _boom

        self.app._run_worker()

        self.app.tc.set_temperature.assert_called_once_with(25.0)
        self.assertFalse(self.app.running)
        self.assertEqual(self.app._status_var.get(), "Error - stopped")

    def test_run_worker_does_not_ramp_down_when_toggle_off_even_on_failure(self):
        self.app.tc = mock.Mock()
        self.app._run_ramp_down_on_done_var.set(False)

        def _boom():
            raise RuntimeError("simulated unexpected failure")

        self.app._run_worker_impl = _boom
        self.app._run_worker()

        self.app.tc.set_temperature.assert_not_called()
        self.assertFalse(self.app.running)

    def test_ramp_down_settings_survive_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = os.path.join(tmpdir, 'gui_last_session.json')
            with mock.patch.object(gui._config, 'GUI_LAST_SESSION_PATH', session_path):
                self.app._run_ramp_down_on_done_var.set(False)
                self.app._run_ramp_down_end_temp_c_var.set('30.0')
                self.app._run_ramp_down_end_ramp_rate_var.set('2.5')
                self.app._save_condition_generator_settings()

                self.app._run_ramp_down_on_done_var.set(True)
                self.app._run_ramp_down_end_temp_c_var.set('25.0')
                self.app._run_ramp_down_end_ramp_rate_var.set('5.0')

                self.app._load_condition_generator_settings()

        self.assertFalse(self.app._run_ramp_down_on_done_var.get())
        self.assertEqual(self.app._run_ramp_down_end_temp_c_var.get(), '30.0')
        self.assertEqual(self.app._run_ramp_down_end_ramp_rate_var.get(), '2.5')

    def test_run_worker_raises_tip_before_every_temperature_change(self):
        # Previously the tip only retracted at the very end of a run
        # (checkbox-gated _retract_tip_after_successful_run). This proves
        # the tip is also raised, unconditionally, right before each row
        # that actually changes the furnace setpoint -- not just once at
        # the end -- and that the raise happens BEFORE set_temperature is
        # called, not after.
        events = []

        class _FakeMotor:
            def __init__(self):
                self.z = 10.0

            def get_position(self, axis):
                return self.z

            def move_abs_wait(self, axis, target, **kwargs):
                events.append(('motor_move', axis, target))
                if axis == 'Z':
                    self.z = target

        class _FakeTempController:
            def set_ramp_rate(self, rate):
                events.append(('set_ramp_rate', rate))

            def set_temperature(self, target):
                events.append(('set_temperature', target))

            def wait_stable(self, *args, **kwargs):
                events.append(('wait_stable',))

            def get_temperature(self):
                return 300.0

        self.app.motor = _FakeMotor()
        self.app.tc = _FakeTempController()
        self.app._run_retract_tip_mm_var.set('1.000')

        def fake_normal(**kwargs):
            return SequenceResult(
                measurement_mode="normal_eis",
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis_only.txt"),
            )

        gui.MODS["normal_sequence"] = fake_normal
        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            row = self._make_row("SKIPCA_T300_E1_V+0.000", 0.0)
            row["SkipCA"] = 1
            row["Temperature_C"] = 300.0
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()

        retract_idx = events.index(('motor_move', 'Z', 9.0))
        set_temp_idx = events.index(('set_temperature', 300.0))
        self.assertLess(retract_idx, set_temp_idx)

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

    def test_image_monitor_expected_count_parser(self):
        self.app._image_expected_count_var.set("")
        self.assertIsNone(self.app._parse_image_expected_count())
        self.app._image_expected_count_var.set("12")
        self.assertEqual(self.app._parse_image_expected_count(), 12)
        self.app._image_expected_count_var.set("0")
        self.assertIsNone(self.app._parse_image_expected_count())
        self.app._image_expected_count_var.set("abc")
        self.assertIsNone(self.app._parse_image_expected_count())

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
        self.assertIsNotNone(self.app._image_seed_map)
        self.assertEqual(len(self.app._image_seed_map), 1)
        self.assertIsNotNone(self.app._image_seed_layout)
        self.assertIsNotNone(self.app._image_seed_tracked)
        self.assertEqual(len(self.app._image_seed_tracked), 1)
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

    def test_selecting_target_fills_manual_target_xy_when_calibration_solved(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        self.app._image_tracking_map = pd.DataFrame(
            [{
                "x_px": 12.5, "y_px": 7.25, "radius_px": 8.0,
                "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all",
                "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed",
            }]
        )
        self.app._manual_target['x'].set('-1.0')
        self.app._manual_target['y'].set('-1.0')

        self.assertTrue(self.app._image_select_target_from_frame_xy(12.5, 7.25))

        self.assertAlmostEqual(float(self.app._manual_target['x'].get()), 12.5, places=3)
        self.assertAlmostEqual(float(self.app._manual_target['y'].get()), 7.25, places=3)

    def test_selecting_target_leaves_manual_target_xy_unchanged_without_calibration(self):
        self.app._image_pixel_stage_affine_calibration = None
        self.app._image_tracking_map = pd.DataFrame(
            [{
                "x_px": 12.5, "y_px": 7.25, "radius_px": 8.0,
                "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "all",
                "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "seed",
            }]
        )
        self.app._manual_target['x'].set('-1.0')
        self.app._manual_target['y'].set('-1.0')

        self.assertTrue(self.app._image_select_target_from_frame_xy(12.5, 7.25))

        self.assertEqual(self.app._manual_target['x'].get(), '-1.0')
        self.assertEqual(self.app._manual_target['y'].get(), '-1.0')

    def test_selecting_dxf_layout_target_fills_manual_target_z_from_seed(self):
        layout = self._make_square_layout()
        layout.transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        self.app._image_layout_model = layout
        self.app._image_electrode_z_store.record_contact(0, 3.75, source='auto_contact')
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        self.app._image_tracking_map = pd.DataFrame(
            [{
                # layout_index=0 -> col_index=1, source='dxf_layout' so
                # _image_layout_index_for_target resolves it.
                "x_px": 5.0, "y_px": 5.0, "radius_px": 8.0,
                "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "dxf_layout",
                "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "dxf_layout",
            }]
        )
        self.app._manual_target['z'].set('-1.0')
        # Lock Z defaults to True (see
        # test_selecting_dxf_layout_target_leaves_manual_target_z_untouched_when_locked
        # for that default) -- explicitly unlock it here to exercise the
        # auto-fill-from-seed path itself.
        self.app._image_lock_z_var.set(False)

        self.assertTrue(self.app._image_select_target_from_frame_xy(5.0, 5.0))

        self.assertAlmostEqual(float(self.app._manual_target['z'].get()), 3.75, places=3)

    def test_selecting_dxf_layout_target_leaves_manual_target_z_untouched_when_locked(self):
        layout = self._make_square_layout()
        layout.transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        self.app._image_layout_model = layout
        self.app._image_electrode_z_store.record_contact(0, 3.75, source='auto_contact')
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        self.app._image_tracking_map = pd.DataFrame(
            [{
                "x_px": 5.0, "y_px": 5.0, "radius_px": 8.0,
                "row_index": 1, "col_index": 1, "ordinal": 1, "size_group": "dxf_layout",
                "sample_x_mm": None, "sample_y_mm": None, "projected": False, "source": "dxf_layout",
            }]
        )
        self.app._manual_target['z'].set('-1.0')
        self.assertTrue(self.app._image_lock_z_var.get())  # default

        self.assertTrue(self.app._image_select_target_from_frame_xy(5.0, 5.0))

        # Z untouched; X/Y still fill in as usual (X/Y auto-fill is
        # unaffected by Lock Z -- covered by the sibling unlocked test).
        self.assertAlmostEqual(float(self.app._manual_target['z'].get()), -1.0, places=3)
        self.assertAlmostEqual(float(self.app._manual_target['x'].get()), 5.0, places=3)

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

    def test_image_monitor_tick_prefers_frozen_seed_tracking_when_available(self):
        frame_rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        self.app._image_monitor_last_frame_rgb = frame_rgb.copy()
        self.app._image_tracking_map = pd.DataFrame(
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
        self.app._image_freeze_current_seed()
        self.assertIsNotNone(self.app._image_seed_tracked)

        self.app._image_monitor_running = True
        self.app._image_monitor_frame_idx = 0
        self.app._image_monitor_last_overlay_bgr = None

        class FakeCap:
            def read(self_inner):
                return True, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        self.app._image_monitor_cap = FakeCap()
        self.app.after = lambda *args, **kwargs: None
        self.app._render_image_monitor_frame = lambda frame_bgr: None

        def fake_refit(frame, layout, tracked, **kwargs):
            for c in tracked:
                c.detected = True
            return SimpleNamespace(confident_count=len(tracked), refit_performed=False, notes=[])

        with mock.patch.object(
            gui, "detect_and_refit_frame", side_effect=fake_refit
        ) as mocked_refit:
            self.app._image_monitor_tick()

        mocked_refit.assert_called_once()
        self.assertEqual(len(self.app._image_tracking_map), 1)
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

    # ══════════════════════════════════════════════════════════════════
    # DXF-layout alignment: simplified single-click fit + live-tracking seed
    # ══════════════════════════════════════════════════════════════════

    def _make_square_layout(self):
        template = np.array([[0, 0], [60, 0], [0, 60], [60, 60]], dtype=np.float32)
        radii = np.array([8, 8, 8, 8], dtype=np.float32)
        return gui.LayoutModel(template, radii)

    def test_layout_diameter_um_for_layout_index_converts_mm_radius_to_micron_diameter(self):
        self.app._image_layout_model = self._make_square_layout()
        # Fixture radii are 8 (mm, per LayoutModel's own docstring) -> diameter 16 mm = 16000 um.
        self.assertAlmostEqual(
            self.app._image_layout_diameter_um_for_layout_index(0), 16000.0, places=6,
        )

    def test_layout_diameter_um_returns_none_without_layout(self):
        self.app._image_layout_model = None
        self.assertIsNone(self.app._image_layout_diameter_um_for_layout_index(0))

    def test_layout_diameter_um_returns_none_without_radii(self):
        template = np.array([[0, 0], [60, 0]], dtype=np.float32)
        self.app._image_layout_model = gui.LayoutModel(template, None)
        self.assertIsNone(self.app._image_layout_diameter_um_for_layout_index(0))

    def test_layout_diameter_um_returns_none_for_out_of_range_index(self):
        self.app._image_layout_model = self._make_square_layout()
        self.assertIsNone(self.app._image_layout_diameter_um_for_layout_index(99))
        self.assertIsNone(self.app._image_layout_diameter_um_for_layout_index(None))

    def _write_fake_row_artifacts(self, tmpdir, txt_name='raw_peis.txt', mpr_name='raw_peis.mpr'):
        txt_path = os.path.join(tmpdir, txt_name)
        with open(txt_path, 'w', encoding='utf-8') as fh:
            fh.write('1.0 2.0 3.0\n')
        mpr_path = os.path.join(tmpdir, mpr_name)
        with open(mpr_path, 'wb') as fh:
            fh.write(b'FAKE MPR BYTES')
        return txt_path, mpr_path

    def test_curate_row_result_artifacts_plain_sequence_result(self):
        self.app._image_layout_model = self._make_square_layout()
        row_tmpdir = tempfile.mkdtemp()
        result_tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, row_tmpdir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, result_tmpdir, ignore_errors=True)
        txt_path, mpr_path = self._write_fake_row_artifacts(row_tmpdir)
        sequence_result = SimpleNamespace(peis_path=txt_path, peis_mpr_path=mpr_path)

        curated_txt, curated_mpr, curated_json = self.app._curate_row_result_artifacts(
            row={'Label': 'E1_test'}, row_index=1, label='E1_test',
            sequence_result=sequence_result, result_root=result_tmpdir,
        )

        self.assertTrue(os.path.exists(curated_txt))
        self.assertTrue(os.path.exists(curated_mpr))
        self.assertTrue(os.path.exists(curated_json))
        base_txt = os.path.splitext(os.path.basename(curated_txt))[0]
        base_mpr = os.path.splitext(os.path.basename(curated_mpr))[0]
        base_json = os.path.splitext(os.path.basename(curated_json))[0]
        self.assertEqual(base_txt, base_mpr)
        self.assertEqual(base_txt, base_json)
        # E1 -> layout_index 0 -> fixture radius 8 (mm) -> diameter 16000 um.
        self.assertIn('16000um', base_txt)
        with open(curated_json, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
        self.assertEqual(payload['electrode_diameter_um'], 16000.0)
        self.assertEqual(payload['curated_peis_txt'], curated_txt)
        self.assertEqual(payload['curated_peis_mpr'], curated_mpr)
        # Originals in the per-row working folder are untouched, not moved.
        self.assertTrue(os.path.exists(txt_path))
        self.assertTrue(os.path.exists(mpr_path))

    def test_curate_row_result_artifacts_hybrid_summary_result(self):
        # OleComSequenceResult exposes paths via .summary, not attributes.
        row_tmpdir = tempfile.mkdtemp()
        result_tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, row_tmpdir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, result_tmpdir, ignore_errors=True)
        txt_path, mpr_path = self._write_fake_row_artifacts(row_tmpdir, 'measured_peis.txt', 'seeded_peis_C01.mpr')
        sequence_result = SimpleNamespace(summary={'peis_txt': txt_path, 'peis_mpr': mpr_path})

        curated_txt, curated_mpr, _curated_json = self.app._curate_row_result_artifacts(
            row={'Label': 'E2_test'}, row_index=2, label='E2_test',
            sequence_result=sequence_result, result_root=result_tmpdir,
        )

        self.assertTrue(os.path.exists(curated_txt))
        self.assertTrue(os.path.exists(curated_mpr))

    def test_curate_row_result_artifacts_missing_mpr_still_curates_txt(self):
        # Legacy (non-OLE-COM) backend never produces a .mpr at all.
        row_tmpdir = tempfile.mkdtemp()
        result_tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, row_tmpdir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, result_tmpdir, ignore_errors=True)
        txt_path = os.path.join(row_tmpdir, 'raw_peis.txt')
        with open(txt_path, 'w', encoding='utf-8') as fh:
            fh.write('1.0 2.0 3.0\n')
        sequence_result = SimpleNamespace(peis_path=txt_path, peis_mpr_path=None)

        curated_txt, curated_mpr, curated_json = self.app._curate_row_result_artifacts(
            row={'Label': 'E3_test'}, row_index=3, label='E3_test',
            sequence_result=sequence_result, result_root=result_tmpdir,
        )

        self.assertTrue(os.path.exists(curated_txt))
        self.assertIsNone(curated_mpr)
        self.assertTrue(os.path.exists(curated_json))

    def test_curate_row_result_artifacts_no_layout_omits_diameter_from_name(self):
        self.app._image_layout_model = None
        row_tmpdir = tempfile.mkdtemp()
        result_tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, row_tmpdir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, result_tmpdir, ignore_errors=True)
        txt_path, mpr_path = self._write_fake_row_artifacts(row_tmpdir)
        sequence_result = SimpleNamespace(peis_path=txt_path, peis_mpr_path=mpr_path)

        curated_txt, _curated_mpr, curated_json = self.app._curate_row_result_artifacts(
            row={'Label': 'E1_test'}, row_index=1, label='E1_test',
            sequence_result=sequence_result, result_root=result_tmpdir,
        )

        self.assertNotIn('um', os.path.basename(curated_txt))
        with open(curated_json, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
        self.assertIsNone(payload['electrode_diameter_um'])

    def _make_mixed_diameter_layout(self):
        # 2 electrodes at radius 8 (diameter 16000 um), 2 at radius 4 (diameter 8000 um).
        template = np.array([[0, 0], [60, 0], [0, 60], [60, 60]], dtype=np.float32)
        radii = np.array([8, 8, 4, 4], dtype=np.float32)
        return gui.LayoutModel(template, radii)

    def test_refresh_exclude_diameter_checkboxes_builds_one_per_distinct_diameter(self):
        self.app._image_layout_model = self._make_mixed_diameter_layout()
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.assertEqual(set(self.app._semi_auto_exclude_diameter_vars.keys()), {8000.0, 16000.0})
        self.assertEqual(len(self.app._semi_auto_exclude_diameter_frame.winfo_children()), 2)

    def test_refresh_exclude_diameter_checkboxes_no_layout_shows_placeholder(self):
        self.app._image_layout_model = None
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.assertEqual(self.app._semi_auto_exclude_diameter_vars, {})
        self.assertEqual(len(self.app._semi_auto_exclude_diameter_frame.winfo_children()), 1)

    def test_layout_indices_for_excluded_diameters_matches_checked_size_class(self):
        self.app._image_layout_model = self._make_mixed_diameter_layout()
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.app._semi_auto_exclude_diameter_vars[8000.0].set(True)
        self.assertEqual(self.app._image_layout_indices_for_excluded_diameters(), {2, 3})

    def test_layout_indices_for_excluded_diameters_empty_when_none_checked(self):
        self.app._image_layout_model = self._make_mixed_diameter_layout()
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.assertEqual(self.app._image_layout_indices_for_excluded_diameters(), set())

    def test_layout_indices_for_excluded_diameters_empty_without_layout(self):
        self.app._image_layout_model = None
        self.assertEqual(self.app._image_layout_indices_for_excluded_diameters(), set())

    def test_layout_click_places_circle_with_no_radius_adjustment(self):
        self.app._image_layout_model = self._make_square_layout()
        self.app._image_render_shape = (400, 400)
        self.app._image_render_size = (400, 400)
        self.app._image_render_offset = (0, 0)
        self.app._image_toggle_layout_draw_mode()
        self.assertTrue(self.app._image_layout_draw_mode)

        event = SimpleNamespace(x=50, y=60)
        self.app._image_monitor_click_select_target(event)

        self.assertEqual(len(self.app._image_layout_drawn_circles), 1)
        drawn = self.app._image_layout_drawn_circles[0]
        self.assertEqual(drawn["center"], [50.0, 60.0])
        self.assertEqual(drawn["radius"], gui._IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX)

    def test_layout_click_does_not_snap_to_nearby_circle(self):
        # Regression: placing an alignment-circle center must use the raw
        # click position and never re-detect/snap to whatever circle is
        # nearby -- Hough-based snapping was leftover from an earlier
        # rubber-band (two-click) placement process and could jump to a
        # neighbouring electrode's circle instead of the one clicked. No
        # frame grab should happen at all for this click anymore.
        self.app._image_layout_model = self._make_square_layout()
        self.app._image_render_shape = (400, 400)
        self.app._image_render_size = (400, 400)
        self.app._image_render_offset = (0, 0)
        self.app._image_toggle_layout_draw_mode()
        self.app._image_current_frame_bgr = mock.Mock(return_value=None)

        event = SimpleNamespace(x=50, y=60)
        self.app._image_monitor_click_select_target(event)

        self.assertEqual(len(self.app._image_layout_drawn_circles), 1)
        self.assertEqual(self.app._image_layout_drawn_circles[0]["center"], [50.0, 60.0])
        self.app._image_current_frame_bgr.assert_not_called()

    def test_fit_layout_alignment_uses_raw_clicked_centers_no_refinement(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        known_transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        layout.transform = known_transform
        true_pixels = layout.project_all()
        layout.transform = None

        self.app._image_layout_drawn_circles = [
            {"center": [float(x), float(y)], "radius": gui._IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX}
            for x, y in true_pixels
        ]
        self.app._image_layout_alignment_pairs = [(0, 0), (1, 1), (2, 2), (3, 3)]
        # No live frame present -- confirms the fit does not depend on a
        # Hough-refinement search against a camera frame.
        self.app._image_monitor_last_frame_rgb = None

        self.app._image_fit_layout_alignment()

        self.assertIsNotNone(self.app._image_layout_model.transform)
        np.testing.assert_allclose(
            self.app._image_layout_model.project_all(), true_pixels, atol=1e-3
        )

    def test_accept_layout_alignment_seeds_live_tracking(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        layout.transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        frame = np.full((400, 400, 3), 30, dtype=np.uint8)
        self.app._image_monitor_last_frame_rgb = frame

        self.app._image_accept_layout_alignment()

        self.assertIsNotNone(self.app._image_layout_seed_map)
        self.assertEqual(len(self.app._image_layout_seed_map), 4)
        self.assertListEqual(
            list(self.app._image_layout_seed_map["col_index"]), [1, 2, 3, 4]
        )
        self.assertTrue(
            {"x_px", "y_px", "radius_px", "row_index", "col_index", "size_group", "source"}
            <= set(self.app._image_layout_seed_map.columns)
        )
        self.assertIsNotNone(self.app._image_layout_tracked)
        self.assertEqual(len(self.app._image_layout_tracked), 4)
        self.assertListEqual(
            [c.layout_index for c in self.app._image_layout_tracked], [0, 1, 2, 3]
        )
        self.assertIn("seeded for live tracking", self.app._image_layout_alignment_status_var.get())

    def test_accept_layout_alignment_clears_drawn_circles_and_pairs(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        layout.transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        frame = np.full((400, 400, 3), 30, dtype=np.uint8)
        self.app._image_monitor_last_frame_rgb = frame
        self.app._image_layout_drawn_circles = [
            {"center": [10.0, 10.0], "radius": gui._IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX}
        ]
        self.app._image_layout_alignment_pairs = [(0, 0)]

        self.app._image_accept_layout_alignment()

        self.assertEqual(self.app._image_layout_drawn_circles, [])
        self.assertEqual(self.app._image_layout_alignment_pairs, [])

    def test_accept_layout_alignment_without_transform_does_not_seed(self):
        self.app._image_layout_model = self._make_square_layout()
        self.app._image_monitor_last_frame_rgb = np.full((10, 10, 3), 1, dtype=np.uint8)

        self.app._image_accept_layout_alignment()

        self.assertIsNone(self.app._image_layout_seed_map)
        self.assertIsNone(self.app._image_layout_tracked)
        self.assertIn("Accept failed", self.app._image_layout_alignment_status_var.get())

    def test_image_monitor_tick_prefers_dxf_layout_seed_tracking_when_loaded(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        layout.transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        true_pixels = layout.project_all()

        frame_rgb = np.full((400, 400, 3), 30, dtype=np.uint8)
        for px, py in true_pixels:
            cv2.circle(frame_rgb, (int(px), int(py)), 15, (200, 200, 200), -1)
        frame_rgb = cv2.GaussianBlur(frame_rgb, (5, 5), 0)
        self.app._image_monitor_last_frame_rgb = frame_rgb

        self.app._image_accept_layout_alignment()
        self.assertIsNotNone(self.app._image_layout_seed_map)

        class _FakeCap:
            def read(self):
                return True, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        self.app._image_monitor_cap = _FakeCap()
        self.app._image_monitor_running = True
        orig_after = self.app.after
        self.app.after = lambda *a, **k: None
        try:
            self.app._image_monitor_tick()
        finally:
            self.app.after = orig_after

        self.assertIsNotNone(self.app._image_tracking_map)
        self.assertEqual(len(self.app._image_tracking_map), 4)
        self.assertIn("DXF layout seed", self.app._image_detection_var.get())
        self.assertIn("DXF-layout-guided tracking is active", self.app._image_hint_var.get())

    def test_image_monitor_tick_min_confident_scales_with_layout_size(self):
        # 30-electrode layout: 30 * VISION_DRIFT_MIN_CONFIDENT_ELECTRODES_FRACTION
        # (0.2) = 6, which exceeds the flat floor (VISION_DRIFT_MIN_CONFIDENT_ELECTRODES,
        # 4) -- confirms the proportional threshold actually takes over for
        # a large-enough layout instead of always just returning the floor.
        layout = gui.LayoutModel(
            np.array([[i * 10.0, 0.0] for i in range(30)], dtype=np.float32),
            np.array([8.0] * 30, dtype=np.float32),
        )
        layout.transform = np.array([[1.0, 0.0, 50.0], [0.0, 1.0, 50.0]], dtype=np.float32)
        self.app._image_layout_model = layout
        self.app._image_layout_tracked = [
            gui.Circle(x=float(i * 10.0 + 50), y=50.0, radius=8.0, layout_index=i, on_image=True)
            for i in range(30)
        ]
        frame_rgb = np.zeros((400, 400, 3), dtype=np.uint8)
        self.app._image_monitor_last_frame_rgb = frame_rgb

        class _FakeCap:
            def read(self):
                return True, frame_rgb.copy()

        self.app._image_monitor_cap = _FakeCap()
        self.app._image_monitor_running = True
        fake_result = mock.Mock(refit_performed=False, confident_count=0, notes=[])
        orig_after = self.app.after
        self.app.after = lambda *a, **k: None
        try:
            with mock.patch.object(gui, 'detect_and_refit_frame', return_value=fake_result) as mocked:
                self.app._image_monitor_tick()
        finally:
            self.app.after = orig_after

        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs['min_confident'], 6)

    def test_image_monitor_tick_min_confident_floor_for_small_layout(self):
        layout = self._make_square_layout()  # 4 electrodes
        layout.transform = np.array([[1.0, 0.0, 50.0], [0.0, 1.0, 50.0]], dtype=np.float32)
        self.app._image_layout_model = layout
        self.app._image_layout_tracked = [
            gui.Circle(x=50.0, y=50.0, radius=8.0, layout_index=i, on_image=True) for i in range(4)
        ]
        frame_rgb = np.zeros((400, 400, 3), dtype=np.uint8)
        self.app._image_monitor_last_frame_rgb = frame_rgb

        class _FakeCap:
            def read(self):
                return True, frame_rgb.copy()

        self.app._image_monitor_cap = _FakeCap()
        self.app._image_monitor_running = True
        fake_result = mock.Mock(refit_performed=False, confident_count=0, notes=[])
        orig_after = self.app.after
        self.app.after = lambda *a, **k: None
        try:
            with mock.patch.object(gui, 'detect_and_refit_frame', return_value=fake_result) as mocked:
                self.app._image_monitor_tick()
        finally:
            self.app.after = orig_after

        # 4 electrodes * 0.2 = 0.8 -> ceil 1, well below the flat floor of
        # 4 -- must still require the floor.
        self.assertEqual(mocked.call_args.kwargs['min_confident'], 4)

    def test_image_monitor_tick_does_not_mention_occlusion_in_status(self):
        # Occlusion exclusion itself stays functional (see
        # _image_probe_occluded_layout_indices's own dedicated tests and
        # detect_and_refit_frame's excluded_layout_indices tests) -- only
        # the status-text callout was removed, per user request.
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        layout.transform = np.array([[1.0, 0.0, 50.0], [0.0, 1.0, 50.0]], dtype=np.float32)
        true_pixels = layout.project_all()

        frame_rgb = np.full((400, 400, 3), 30, dtype=np.uint8)
        for px, py in true_pixels:
            cv2.circle(frame_rgb, (int(px), int(py)), 15, (200, 200, 200), -1)
        frame_rgb = cv2.GaussianBlur(frame_rgb, (5, 5), 0)
        self.app._image_monitor_last_frame_rgb = frame_rgb

        self.app._image_accept_layout_alignment()
        self.assertIsNotNone(self.app._image_layout_seed_map)

        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 60.0

        class _FakeCap:
            def read(self):
                return True, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

        self.app._image_monitor_cap = _FakeCap()
        self.app._image_monitor_running = True
        orig_after = self.app.after
        self.app.after = lambda *a, **k: None
        try:
            self.app._image_monitor_tick()
        finally:
            self.app.after = orig_after

        self.assertNotIn('occluded', self.app._image_detection_var.get())

    # ══════════════════════════════════════════════════════════════════
    # Run-worker live-tracked positioning: gate against last-trusted
    # position (not the static CSV value) so gradual, genuine drift keeps
    # being trusted over a long run.
    # ══════════════════════════════════════════════════════════════════

    def _set_up_identity_tracked_electrode(self, layout_index=0, px=10.0, py=20.0, radius=8.0):
        """Identity pixel<->stage calibration (stage mm == pixel px) plus a
        single tracked Circle at (px, py) for layout_index, so live-tracked
        target math is trivial to reason about in assertions."""
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        circles = [None] * (layout_index + 1)
        circles[layout_index] = gui.Circle(x=px, y=py, radius=radius, layout_index=layout_index, on_image=True)
        self.app._image_layout_tracked = circles
        self.app._image_layout_model = self._make_square_layout()

    def test_resolve_live_tracked_xy_falls_back_to_csv_without_electrode_id(self):
        self._set_up_identity_tracked_electrode()
        row = {'Label': 'no_electrode_here', 'X_mm': 1.0, 'Y_mm': 2.0}
        x, y, source = self.app._image_resolve_live_tracked_xy(row, 1.0, 2.0)
        self.assertEqual((x, y, source), (1.0, 2.0, 'csv'))

    def test_resolve_live_tracked_xy_falls_back_to_csv_without_tracking(self):
        self.app._image_layout_tracked = None
        row = {'Label': 'E1', 'X_mm': 1.0, 'Y_mm': 2.0}
        x, y, source = self.app._image_resolve_live_tracked_xy(row, 1.0, 2.0)
        self.assertEqual((x, y, source), (1.0, 2.0, 'csv'))

    def test_resolve_live_tracked_xy_first_touch_within_tolerance_of_csv_is_accepted(self):
        self._set_up_identity_tracked_electrode(px=10.1, py=20.1)  # tracked (10.1, 20.1) mm
        row = {'Label': 'E1', 'X_mm': 10.0, 'Y_mm': 20.0}
        x, y, source = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual(source, 'live-tracked')
        self.assertAlmostEqual(x, 10.1, places=6)
        self.assertAlmostEqual(y, 20.1, places=6)
        self.assertEqual(self.app._image_run_trusted_positions[0], (x, y))

    def test_resolve_live_tracked_xy_first_touch_out_of_tolerance_of_csv_falls_back(self):
        self._set_up_identity_tracked_electrode(px=11.0, py=20.0)  # 1.0 mm from CSV, > 0.5 mm bound
        row = {'Label': 'E1', 'X_mm': 10.0, 'Y_mm': 20.0}
        x, y, source = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual((x, y, source), (10.0, 20.0, 'csv'))
        self.assertNotIn(0, self.app._image_run_trusted_positions)

    def test_resolve_live_tracked_xy_accepts_drift_beyond_csv_bound_but_within_trusted_bound(self):
        # First touch establishes a trusted position 0.3 mm from CSV.
        self._set_up_identity_tracked_electrode(px=10.3, py=20.0)
        row = {'Label': 'E1', 'X_mm': 10.0, 'Y_mm': 20.0}
        x1, y1, source1 = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual(source1, 'live-tracked')

        # Second touch drifts another 0.3 mm further (0.6 mm total from the
        # original CSV value -- would fail a CSV-anchored gate -- but only
        # 0.3 mm from the just-trusted position, so it should be accepted.
        self.app._image_layout_tracked[0].smoothed_x = 10.6
        x2, y2, source2 = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual(source2, 'live-tracked')
        self.assertAlmostEqual(x2, 10.6, places=6)
        self.assertEqual(self.app._image_run_trusted_positions[0], (x2, y2))

    def test_resolve_live_tracked_xy_rejects_glitch_near_csv_but_far_from_trusted(self):
        # First touch establishes a trusted position close to CSV.
        self._set_up_identity_tracked_electrode(px=10.1, py=20.0)
        row = {'Label': 'E1', 'X_mm': 10.0, 'Y_mm': 20.0}
        x1, y1, source1 = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual(source1, 'live-tracked')

        # A bad detection jumps close to the ORIGINAL CSV value again but
        # far from the position we just trusted -- must be rejected.
        self.app._image_layout_tracked[0].smoothed_x = 9.95
        self.app._image_layout_tracked[0].smoothed_y = 21.0  # > 0.5 mm from (10.1, 20.0)
        x2, y2, source2 = self.app._image_resolve_live_tracked_xy(row, 10.0, 20.0)
        self.assertEqual((x2, y2, source2), (10.0, 20.0, 'csv'))
        # The trusted position from the first touch must survive the rejection.
        self.assertEqual(self.app._image_run_trusted_positions[0], (x1, y1))

    # ══════════════════════════════════════════════════════════════════
    # Generating CSV rows from Image Monitor seeded electrodes
    # ══════════════════════════════════════════════════════════════════

    def _set_up_seeded_layout(self, n=2):
        """Identity pixel<->stage calibration + an N-electrode layout with
        both tracked pixel positions and exact Z seeds for every electrode."""
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        template = np.array([[i * 10.0, 0.0] for i in range(n)], dtype=np.float32)
        radii = np.array([8.0] * n, dtype=np.float32)
        self.app._image_layout_model = gui.LayoutModel(template, radii)
        self.app._image_layout_tracked = [
            gui.Circle(x=float(i * 10.0), y=0.0, radius=8.0, layout_index=i, on_image=True)
            for i in range(n)
        ]
        for i in range(n):
            self.app._image_electrode_z_store.record_contact(i, 5.0 + i, source='auto_contact')

    def test_seeded_electrode_rows_source_requires_layout(self):
        with self.assertRaises(RuntimeError):
            self.app._image_seeded_electrode_rows_source()

    def test_seeded_electrode_rows_source_requires_calibration(self):
        self.app._image_layout_model = self._make_square_layout()
        with self.assertRaises(RuntimeError):
            self.app._image_seeded_electrode_rows_source()

    def test_seeded_electrode_rows_source_requires_at_least_one_seed(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        self.app._image_layout_model = self._make_square_layout()
        with self.assertRaises(RuntimeError):
            self.app._image_seeded_electrode_rows_source()

    def test_seeded_electrode_rows_source_returns_seeded_electrodes(self):
        self._set_up_seeded_layout(n=2)
        rows = self.app._image_seeded_electrode_rows_source()
        self.assertEqual(len(rows), 2)
        for (layout_index, x, y, z), expected in zip(rows, [(0, 0.0, 0.0, 5.0), (1, 10.0, 0.0, 6.0)]):
            self.assertEqual(layout_index, expected[0])
            self.assertAlmostEqual(x, expected[1], places=6)
            self.assertAlmostEqual(y, expected[2], places=6)
            self.assertAlmostEqual(z, expected[3], places=6)

    def test_seeded_electrode_rows_source_skips_electrodes_without_a_seed(self):
        self._set_up_seeded_layout(n=3)
        # Electrode 1 (layout_index=1) was seeded by the helper -- clear the
        # store and only seed electrodes 0 and 2, with no plane (only 2
        # points), so electrode 1 has neither an exact seed nor an estimate.
        self.app._image_electrode_z_store = gui.ElectrodeZCalibrationStore()
        self.app._image_electrode_z_store.record_contact(0, 5.0, source='auto_contact')
        self.app._image_electrode_z_store.record_contact(2, 7.0, source='auto_contact')
        rows = self.app._image_seeded_electrode_rows_source()
        self.assertEqual({r[0] for r in rows}, {0, 2})

    def test_seeded_electrode_rows_source_omit_layout_indices_filters(self):
        self._set_up_seeded_layout(n=3)
        rows = self.app._image_seeded_electrode_rows_source(omit_layout_indices={1})
        self.assertEqual({r[0] for r in rows}, {0, 2})

    def test_seeded_electrode_rows_source_omit_layout_indices_all_raises(self):
        # Omitting every seeded electrode still raises the "no electrodes
        # have a Z seed yet" error -- same as the whole-layout case, just
        # scoped to what's left after the omit filter (see
        # _image_seeded_electrode_rows_source docstring).
        self._set_up_seeded_layout(n=2)
        with self.assertRaises(RuntimeError):
            self.app._image_seeded_electrode_rows_source(omit_layout_indices={0, 1})

    def test_parse_int_ranges_comma_list(self):
        self.assertEqual(self.app._parse_int_ranges('1, 3, 5'), {1, 3, 5})

    def test_parse_int_ranges_expands_hyphen_range(self):
        self.assertEqual(self.app._parse_int_ranges('1,3,5-8'), {1, 3, 5, 6, 7, 8})

    def test_parse_int_ranges_handles_newline_separators(self):
        self.assertEqual(self.app._parse_int_ranges('1\n2\n3'), {1, 2, 3})

    def test_parse_int_ranges_blank_input_returns_empty_set(self):
        self.assertEqual(self.app._parse_int_ranges(''), set())
        self.assertEqual(self.app._parse_int_ranges('   '), set())

    def test_parse_int_ranges_malformed_range_raises(self):
        with self.assertRaises(ValueError):
            self.app._parse_int_ranges('8-5')
        with self.assertRaises(ValueError):
            self.app._parse_int_ranges('abc')

    def test_generate_semi_auto_conditions_image_monitor_mode_honors_electrode_omit_list(self):
        self._set_up_seeded_layout(n=3)
        self.app._semi_auto_image_monitor_omit_electrodes_var.set('2')  # 1-based -> layout 1
        self._generate_conditions_for_seeded_layout(adaptive=False)
        df = self.app.condition_df
        self.assertEqual(len(df), 2)
        self.assertListEqual(list(df['X_mm']), [0.0, 20.0])

    def test_generate_semi_auto_conditions_image_monitor_mode_honors_diameter_exclusion(self):
        self._set_up_seeded_layout(n=3)
        # radii -> diameters: [16000, 8000, 8000] um. Excluding 8000 drops
        # layout indices 1 and 2, leaving only index 0 (X=0.0).
        self.app._image_layout_model.template_radii = np.array([8.0, 4.0, 4.0], dtype=np.float32)
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.app._semi_auto_exclude_diameter_vars[8000.0].set(True)
        self._generate_conditions_for_seeded_layout(adaptive=False)
        df = self.app.condition_df
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(float(df['X_mm'].iloc[0]), 0.0, places=6)

    def test_generate_semi_auto_conditions_image_monitor_mode_combines_omit_list_and_diameter_exclusion(self):
        self._set_up_seeded_layout(n=4)
        # radii -> diameters: [16000, 8000, 16000, 8000] um. Excluding 8000
        # drops layout indices 1 and 3; the omit-list additionally drops
        # index 0 (1-based electrode '1') -- only index 2 (X=20.0) survives.
        self.app._image_layout_model.template_radii = np.array([8.0, 4.0, 8.0, 4.0], dtype=np.float32)
        self.app._image_refresh_semi_auto_exclude_diameter_checkboxes()
        self.app._semi_auto_exclude_diameter_vars[8000.0].set(True)
        self.app._semi_auto_image_monitor_omit_electrodes_var.set('1')
        self._generate_conditions_for_seeded_layout(adaptive=False)
        df = self.app.condition_df
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(float(df['X_mm'].iloc[0]), 20.0, places=6)

    def test_generate_full_auto_conditions_image_monitor_mode_honors_electrode_omit_list(self):
        self._set_up_seeded_layout(n=3)
        self.app._full_auto_image_monitor_omit_electrodes_var.set('1,3')  # 1-based -> layout 0, 2
        self._generate_conditions_for_seeded_layout(adaptive=True)
        df = self.app.condition_df
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(float(df['X_mm'].iloc[0]), 10.0, places=6)

    def test_generate_semi_auto_conditions_image_monitor_mode_reports_error_on_malformed_electrode_field(self):
        self._set_up_seeded_layout(n=2)
        self.app._semi_auto_image_monitor_omit_electrodes_var.set('abc')
        with mock.patch.object(gui.messagebox, 'showerror') as mocked_error:
            self._generate_conditions_for_seeded_layout(adaptive=False)
        mocked_error.assert_called_once()

    def _generate_conditions_for_seeded_layout(self, *, adaptive):
        use = self.app._full_auto_use if adaptive else self.app._semi_auto_use
        fields = self.app._full_auto if adaptive else self.app._semi_auto
        source_var = (
            self.app._full_auto_tip_source_var if adaptive
            else self.app._semi_auto_tip_source_var
        )
        use['temperature'].set(False)
        use['gas'].set(False)
        use['tip'].set(True)
        source_var.set('image_monitor')
        fields['voltages'].set('0.0')
        fields['auto_contact_z'].set('0')  # must be ignored/forced to 1 in this mode
        generate = (
            self.app._generate_full_auto_conditions if adaptive
            else self.app._generate_semi_auto_conditions
        )
        generate()

    def test_generate_semi_auto_conditions_image_monitor_mode_uses_seeded_electrodes(self):
        self._set_up_seeded_layout(n=2)
        self._generate_conditions_for_seeded_layout(adaptive=False)
        df = self.app.condition_df
        self.assertEqual(len(df), 2)
        self.assertListEqual(list(df['X_mm']), [0.0, 10.0])
        self.assertListEqual(list(df['Z_mm']), [5.0, 6.0])
        self.assertTrue((df['AutoContactZ'] == 1).all())

    def test_generate_semi_auto_conditions_image_monitor_mode_reports_error_without_seeds(self):
        self.app._image_layout_model = self._make_square_layout()
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        with mock.patch.object(gui.messagebox, 'showerror') as mocked_error:
            self._generate_conditions_for_seeded_layout(adaptive=False)
        mocked_error.assert_called_once()

    def test_generate_full_auto_conditions_image_monitor_mode_uses_seeded_electrodes(self):
        self._set_up_seeded_layout(n=2)
        self._generate_conditions_for_seeded_layout(adaptive=True)
        df = self.app.condition_df
        self.assertEqual(len(df), 2)
        self.assertListEqual(list(df['X_mm']), [0.0, 10.0])
        self.assertListEqual(list(df['Z_mm']), [5.0, 6.0])
        self.assertTrue((df['AutoContactZ'] == 1).all())
        self.assertTrue(all(str(label).startswith('ADAPT') for label in df['Label']))

    def test_generate_semi_auto_conditions_manual_mode_is_unaffected(self):
        # tip_source defaults to 'manual' -- confirms the restructuring
        # didn't change default (pre-existing) generator behavior.
        self.app._semi_auto_use['temperature'].set(False)
        self.app._semi_auto_use['gas'].set(False)
        self.app._semi_auto['voltages'].set('0.0')
        self.app._semi_auto['electrode_start'].set('1')
        self.app._semi_auto['electrode_end'].set('2')
        self.app._semi_auto['x1'].set('0.0')
        self.app._semi_auto['xn'].set('10.0')
        self.app._semi_auto['y1'].set('0.0')
        self.app._semi_auto['yn'].set('0.0')
        self.app._semi_auto['z1'].set('1.0')
        self.app._semi_auto['zn'].set('2.0')
        self.app._generate_semi_auto_conditions()
        df = self.app.condition_df
        self.assertEqual(len(df), 2)
        self.assertListEqual(list(df['X_mm']), [0.0, 10.0])
        self.assertListEqual(list(df['Z_mm']), [1.0, 2.0])

    def _make_stateful_motor(self, x=0.0, y=0.0, z=0.0):
        """A motor mock whose get_position reflects the last position
        move_xyz_safe actually commanded -- needed now that Search Z's
        precondition is a live stage-position check
        (_image_target_within_move_tolerance), not a flag set once at
        Move Tip time."""
        positions = {'X': x, 'Y': y, 'Z': z}

        def get_position(axis):
            return positions[axis]

        def move_xyz_safe(x_mm=None, y_mm=None, z_mm=None, **kwargs):
            if x_mm is not None:
                positions['X'] = x_mm
            if y_mm is not None:
                positions['Y'] = y_mm
            if z_mm is not None:
                positions['Z'] = z_mm
            return {}

        motor = mock.Mock()
        motor.get_position.side_effect = get_position
        motor.move_xyz_safe.side_effect = move_xyz_safe
        return motor

    def test_manual_move_stage_does_not_arm_target_without_calibration(self):
        motor = self._make_stateful_motor()
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_tracking_map = pd.DataFrame(
            [{"x_px": 50.0, "y_px": 50.0, "radius_px": 8.0, "row_index": 1, "col_index": 1,
              "ordinal": 1, "size_group": "dxf_layout", "sample_x_mm": None, "sample_y_mm": None,
              "projected": False, "source": "dxf_layout"}]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_pixel_stage_affine_calibration = None

        self.app._manual_move_stage()

        motor.move_xyz_safe.assert_called_once()
        self.assertFalse(self.app._image_target_within_move_tolerance())
        self.assertIsNotNone(self.app._image_selected_target)

    def test_manual_move_stage_uses_pixel_probe_projection_synced_at_lock_time(self):
        motor = self._make_stateful_motor()
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=100.0, stage_y_mm=200.0),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=101.0, stage_y_mm=200.0),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=100.0, stage_y_mm=201.0),
        ])
        self.app._image_tracking_map = pd.DataFrame(
            [{"x_px": 50.0, "y_px": 50.0, "radius_px": 8.0, "row_index": 1, "col_index": 1,
              "ordinal": 1, "size_group": "dxf_layout", "sample_x_mm": None, "sample_y_mm": None,
              "projected": False, "source": "dxf_layout"}]
        )
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))

        self.app._manual_move_stage()

        motor.move_xyz_safe.assert_called_once()
        _args, kwargs = motor.move_xyz_safe.call_args
        expected_x, expected_y = gui.pixel_to_stage_xy(
            self.app._image_pixel_stage_affine_calibration, 50.0, 50.0
        )
        self.assertAlmostEqual(kwargs["x_mm"], expected_x)
        self.assertAlmostEqual(kwargs["y_mm"], expected_y)
        self.assertTrue(self.app._image_target_within_move_tolerance())

    # ══════════════════════════════════════════════════════════════════
    # Z-seed readiness: Search Z must only run once the stage is
    # currently within tolerance of the locked target's expected XY --
    # checked live (_image_target_within_move_tolerance), not gated on
    # having clicked Move Tip specifically.
    # ══════════════════════════════════════════════════════════════════

    def _set_up_locked_target(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=100.0, stage_y_mm=200.0),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=101.0, stage_y_mm=200.0),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=100.0, stage_y_mm=201.0),
        ])
        self.app._image_tracking_map = pd.DataFrame(
            [{"x_px": 50.0, "y_px": 50.0, "radius_px": 8.0, "row_index": 1, "col_index": 1,
              "ordinal": 1, "size_group": "dxf_layout", "sample_x_mm": None, "sample_y_mm": None,
              "projected": False, "source": "dxf_layout"}]
        )

    def test_image_select_target_resets_move_readiness_and_z_seed_status(self):
        self.app.motor = self._make_stateful_motor(x=999.0, y=999.0)  # far from any target
        self._set_up_locked_target()

        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))

        self.assertFalse(self.app._image_target_within_move_tolerance())
        self.assertIn('move the stage within tolerance', self.app._image_z_seed_status_var.get())

    def test_manual_move_stage_sets_move_readiness_and_z_seed_status(self):
        motor = self._make_stateful_motor()
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.assertFalse(self.app._image_target_within_move_tolerance())

        self.app._manual_move_stage()

        self.assertTrue(self.app._image_target_within_move_tolerance())
        self.assertIn('ready', self.app._image_z_seed_status_var.get())

    def test_image_seed_z_from_contact_requires_target_within_tolerance(self):
        self.app.motor = self._make_stateful_motor(x=999.0, y=999.0)  # far from the target
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.assertFalse(self.app._image_target_within_move_tolerance())
        self.app._execute_contact_z_search = mock.Mock()

        self.app._image_seed_z_from_contact()

        self.app._execute_contact_z_search.assert_not_called()
        self.assertIn('not within tolerance', self.app._image_z_seed_status_var.get())

    def test_image_seed_z_from_contact_runs_without_move_tip_if_already_within_tolerance(self):
        # The point of removing the Move Tip requirement: any means of
        # getting the stage within tolerance of the target counts, not
        # just having clicked Move Tip -- here the stage simply starts out
        # already there.
        self._set_up_locked_target()
        expected_x, expected_y = gui.pixel_to_stage_xy(
            self.app._image_pixel_stage_affine_calibration, 50.0, 50.0
        )
        self.app.motor = self._make_stateful_motor(x=expected_x, y=expected_y, z=3.0)
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._execute_contact_z_search = mock.Mock(return_value=(3.0, 3.0, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)

        self.app._image_seed_z_from_contact()

        self.app._execute_contact_z_search.assert_called_once()

    def test_manual_move_stage_clears_target_when_fields_dont_match(self):
        motor = self._make_stateful_motor(x=1.0, y=2.0, z=3.0)
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._manual_move_stage()
        self.assertTrue(self.app._image_target_within_move_tolerance())

        # Manually editing the Move to: fields away from the locked target's
        # position means this move is no longer "to" that target -- the
        # target lock should clear automatically instead of needing a
        # separate Clear Target button.
        self.app._manual_target['x'].set('5.0')
        self.app._manual_target['y'].set('6.0')
        self.app._manual_target['z'].set('7.0')
        self.app._manual_move_stage()

        self.assertIsNone(self.app._image_selected_target)
        self.assertIn('lock a target', self.app._image_z_seed_status_var.get())

    def test_image_clear_target_resets_move_readiness(self):
        self._set_up_locked_target()
        self.app.motor = self._make_stateful_motor()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))

        self.app._image_clear_target()

        self.assertFalse(self.app._image_target_within_move_tolerance())
        self.assertIn('lock a target', self.app._image_z_seed_status_var.get())

    # ══════════════════════════════════════════════════════════════════
    # Electrode Z-seed: single-Z calibration enforcement + corrected
    # electrode-pixel-to-stage projection
    # ══════════════════════════════════════════════════════════════════

    def test_image_monitor_solve_probe_calibration_rejects_inconsistent_z(self):
        self.app._image_pixel_stage_calibration_refs = [
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0.0, stage_y_mm=0.0, z_mm=10.00),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=10.0, stage_y_mm=0.0, z_mm=10.02),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=0.0, stage_y_mm=10.0, z_mm=10.20),
        ]

        self.app._image_solve_pixel_stage_affine_calibration()

        self.assertIsNone(self.app._image_pixel_stage_affine_calibration)
        self.assertIsNone(self.app._image_pixel_stage_z_ref_mm)
        self.assertIn("Z varied by", self.app._image_pixel_stage_affine_status_var.get())

    def test_image_monitor_solve_probe_calibration_rejects_missing_z(self):
        self.app._image_pixel_stage_calibration_refs = [
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0.0, stage_y_mm=0.0, z_mm=10.00),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=10.0, stage_y_mm=0.0, z_mm=None),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=0.0, stage_y_mm=10.0, z_mm=10.01),
        ]

        self.app._image_solve_pixel_stage_affine_calibration()

        self.assertIsNone(self.app._image_pixel_stage_affine_calibration)
        self.assertIn("valid Z readback", self.app._image_pixel_stage_affine_status_var.get())

    def test_image_monitor_solve_probe_calibration_accepts_consistent_z(self):
        self.app._image_pixel_stage_calibration_refs = [
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0.0, stage_y_mm=0.0, z_mm=10.00),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=10.0, stage_y_mm=0.0, z_mm=10.01),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=0.0, stage_y_mm=10.0, z_mm=10.02),
        ]

        self.app._image_solve_pixel_stage_affine_calibration()

        self.assertIsNotNone(self.app._image_pixel_stage_affine_calibration)
        self.assertAlmostEqual(self.app._image_pixel_stage_z_ref_mm, 10.01, places=6)

    def _set_up_z_corrected_calibration(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0.0, stage_y_mm=0.0),
            gui.PixelStageReference(pixel_x=100, pixel_y=0, stage_x_mm=10.0, stage_y_mm=0.0),
            gui.PixelStageReference(pixel_x=0, pixel_y=100, stage_x_mm=0.0, stage_y_mm=10.0),
        ])
        self.app._image_pixel_stage_z_ref_mm = 10.0

    def test_image_monitor_project_pixel_to_stage_xy_applies_electrode_seed_correction(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.record_contact(0, 10.30, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}

        stage_xy = self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target)

        delta_z = 10.30 - 10.0
        expected = gui.pixel_to_stage_xy(
            self.app._image_pixel_stage_affine_calibration,
            50.0 - 4.0 * delta_z, 60.0 - (-2.0) * delta_z,
        )
        self.assertAlmostEqual(stage_xy[0], expected[0], places=6)
        self.assertAlmostEqual(stage_xy[1], expected[1], places=6)
        naive = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 50.0, 60.0)
        self.assertFalse(abs(stage_xy[0] - naive[0]) < 1e-6 and abs(stage_xy[1] - naive[1]) < 1e-6)

    def test_image_monitor_project_pixel_to_stage_xy_uses_temperature_bucketed_xy_bias(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0.0, stage_y_mm=0.0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1.0, stage_y_mm=0.0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0.0, stage_y_mm=1.0),
        ])
        # Two separate setpoint buckets, each with its own distinct bias --
        # record_xy_bias_sample is called directly on the store here (not
        # through the gui.py wrapper), so no disk write happens and .save()
        # doesn't need mocking.
        for _ in range(3):
            self.app._image_electrode_z_store.record_xy_bias_sample(
                observed_px=10.5, observed_py=9.7, expected_px=10.0, expected_py=10.0,
                temperature_c=300.0,
            )
        for _ in range(3):
            self.app._image_electrode_z_store.record_xy_bias_sample(
                observed_px=10.2, observed_py=9.9, expected_px=10.0, expected_py=10.0,
                temperature_c=20.0,
            )

        self.app._active_temperature_target_c = 300.0
        stage_xy_hot = self.app._image_project_pixel_to_stage_xy(50.0, 60.0)
        expected_hot = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 49.5, 60.3)
        self.assertAlmostEqual(stage_xy_hot[0], expected_hot[0], places=6)
        self.assertAlmostEqual(stage_xy_hot[1], expected_hot[1], places=6)

        # A cooler active target must give a different, own-bucket
        # correction, not the same constant every time -- proves
        # temperature is actually wired through to get_xy_bias, not just
        # accepted and ignored.
        self.app._active_temperature_target_c = 20.0
        stage_xy_cool = self.app._image_project_pixel_to_stage_xy(50.0, 60.0)
        expected_cool = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 49.8, 60.1)
        self.assertAlmostEqual(stage_xy_cool[0], expected_cool[0], places=6)
        self.assertAlmostEqual(stage_xy_cool[1], expected_cool[1], places=6)
        self.assertNotAlmostEqual(stage_xy_hot[0], stage_xy_cool[0], places=3)

    def test_image_monitor_project_pixel_to_stage_xy_falls_back_without_seed(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}  # layout_index 0 has no seed

        stage_xy = self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target)

        expected = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 50.0, 60.0)
        self.assertAlmostEqual(stage_xy[0], expected[0], places=6)
        self.assertAlmostEqual(stage_xy[1], expected[1], places=6)

    def test_image_monitor_project_pixel_to_stage_xy_falls_back_for_non_dxf_target(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.record_contact(0, 10.30, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "live_detection"}

        stage_xy = self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target)

        expected = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 50.0, 60.0)
        self.assertAlmostEqual(stage_xy[0], expected[0], places=6)
        self.assertAlmostEqual(stage_xy[1], expected[1], places=6)

    def test_image_monitor_project_pixel_to_stage_xy_skips_correction_beyond_extrapolation_range(self):
        self._set_up_z_corrected_calibration()
        # Default VISION_Z_PARALLAX_MAX_EXTRAPOLATION_MM is 1.0 mm; this
        # seed is 3.0 mm from z_ref_mm, well beyond the trusted range.
        self.app._image_electrode_z_store.record_contact(0, 13.0, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}

        stage_xy = self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target)

        expected = gui.pixel_to_stage_xy(self.app._image_pixel_stage_affine_calibration, 50.0, 60.0)
        self.assertAlmostEqual(stage_xy[0], expected[0], places=6)
        self.assertAlmostEqual(stage_xy[1], expected[1], places=6)

    def test_image_monitor_project_pixel_to_stage_xy_uses_z_plane_estimate_for_unseeded_electrode(self):
        self._set_up_z_corrected_calibration()
        self.app._image_layout_model = self._make_square_layout()
        # Square layout: electrodes 0..3 at (0,0), (60,0), (0,60), (60,60).
        # Seed 3 of the 4 with a known plane Z = 10.0 + 0.01*u; electrode 3
        # (60, 60) is deliberately left unmeasured.
        self.app._image_electrode_z_store.record_contact(0, 10.0, source='auto_contact', xy_mm=(0.0, 0.0))
        self.app._image_electrode_z_store.record_contact(1, 10.6, source='auto_contact', xy_mm=(60.0, 0.0))
        self.app._image_electrode_z_store.record_contact(2, 10.0, source='auto_contact', xy_mm=(0.0, 60.0))
        self.assertIsNotNone(self.app._image_electrode_z_store.z_plane)
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 4, "source": "dxf_layout"}  # layout_index 3, unseeded

        stage_xy = self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target)

        estimated_z = self.app._image_electrode_z_store.z_plane.evaluate((60.0, 60.0))
        self.assertAlmostEqual(estimated_z, 10.6, places=6)
        delta_z = estimated_z - 10.0
        expected = gui.pixel_to_stage_xy(
            self.app._image_pixel_stage_affine_calibration,
            50.0 - 4.0 * delta_z, 60.0 - (-2.0) * delta_z,
        )
        self.assertAlmostEqual(stage_xy[0], expected[0], places=6)
        self.assertAlmostEqual(stage_xy[1], expected[1], places=6)

    # ══════════════════════════════════════════════════════════════════
    # _image_project_pixel_to_stage_xy's debug_out diagnostic -- every gate
    # below silently falls back to the uncorrected projection with no
    # other visible signal, which made a real "parallax isn't doing
    # anything" report impossible to diagnose without this.
    # ══════════════════════════════════════════════════════════════════

    def test_project_pixel_debug_out_reports_applied_with_delta_and_shift(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.record_contact(0, 10.30, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('applied', debug_out['message'])
        self.assertIn('Δz=+0.300mm', debug_out['message'])

    def test_project_pixel_debug_out_reports_no_dxf_layout_electrode(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "live_detection"}
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('no DXF-layout electrode', debug_out['message'])

    def test_project_pixel_debug_out_reports_no_parallax_calibration_yet(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.record_contact(0, 10.30, source='auto_contact')
        self.assertIsNone(self.app._image_electrode_z_store.z_parallax)
        target = {"col_index": 1, "source": "dxf_layout"}
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('no Z-parallax calibration yet', debug_out['message'])

    def test_project_pixel_debug_out_reports_pixel_stage_calibration_not_solved(self):
        self._set_up_z_corrected_calibration()
        self.app._image_pixel_stage_z_ref_mm = None  # e.g. loaded, never re-solved this session
        self.app._image_electrode_z_store.record_contact(0, 10.30, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('pixel-stage calibration not solved', debug_out['message'])

    def test_project_pixel_debug_out_reports_no_seed_for_electrode(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}  # layout_index 0 has no seed
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('no Z seed/estimate for this electrode', debug_out['message'])

    def test_project_pixel_debug_out_reports_extrapolation_too_far(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.record_contact(0, 13.0, source='auto_contact')
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        target = {"col_index": 1, "source": "dxf_layout"}
        debug_out = {}

        self.app._image_project_pixel_to_stage_xy(50.0, 60.0, target=target, debug_out=debug_out)

        self.assertIn('extrapolation too far', debug_out['message'])
        self.assertIn('Δz=+3.000mm', debug_out['message'])

    def test_image_update_target_status_appends_parallax_diagnostic(self):
        self._set_up_z_corrected_calibration()
        self.app._image_electrode_z_store.z_parallax = gui.ProbeZParallaxCalibration(du_per_mm=4.0, dv_per_mm=-2.0)
        self.app._image_selected_target = pd.Series({
            "x_px": 50.0, "y_px": 60.0, "row_index": 1, "col_index": 1, "source": "dxf_layout",
        })

        self.app._image_update_target_status()

        self.assertIn('no Z seed/estimate for this electrode', self.app._image_target_status_var.get())

    def test_image_record_electrode_z_contact_passes_layout_xy_mm(self):
        self.app._image_layout_model = self._make_square_layout()
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            self.app._image_record_electrode_z_contact_for_layout_index(1, 12.5, None)
        self.assertEqual(self.app._image_electrode_z_store.electrode_z_seeds[1].xy_mm, (60.0, 0.0))

    def test_image_record_electrode_z_contact_status_is_1_indexed(self):
        # layout_index=1 (0-based, internal) is DXF-layout electrode E2 --
        # the status text must read "E2", not the raw 0-based "E1".
        self.app._image_layout_model = self._make_square_layout()
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            status = self.app._image_record_electrode_z_contact_for_layout_index(1, 12.5, None)
        self.assertIn('E2', status)

    def test_image_save_z_seed_store_browses_for_a_file(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, 'chosen_z_seed.json')
        with mock.patch.object(gui.filedialog, 'asksaveasfilename', return_value=path) as mocked_dialog, \
             mock.patch.object(self.app._image_electrode_z_store, 'save') as mocked_save:
            result = self.app._image_save_z_seed_store()
        mocked_dialog.assert_called_once()
        mocked_save.assert_called_once_with(path)
        self.assertTrue(result)
        self.assertIn('saved', self.app._image_z_seed_status_var.get())
        self.assertIn('chosen_z_seed.json', self.app._image_z_seed_status_var.get())

    def test_image_save_z_seed_store_explicit_path_skips_dialog(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, 'explicit_z_seed.json')
        with mock.patch.object(gui.filedialog, 'asksaveasfilename') as mocked_dialog, \
             mock.patch.object(self.app._image_electrode_z_store, 'save') as mocked_save:
            result = self.app._image_save_z_seed_store(path)
        mocked_dialog.assert_not_called()
        mocked_save.assert_called_once_with(path)
        self.assertTrue(result)

    def test_image_save_z_seed_store_cancelled_dialog_is_a_noop(self):
        with mock.patch.object(gui.filedialog, 'asksaveasfilename', return_value=''), \
             mock.patch.object(self.app._image_electrode_z_store, 'save') as mocked_save:
            result = self.app._image_save_z_seed_store()
        mocked_save.assert_not_called()
        self.assertFalse(result)

    def _write_z_seed_file(self, payload):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        path = os.path.join(tmpdir, 'electrode_z_seed.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)
        return path

    def test_image_load_z_seed_store_browses_for_a_file(self):
        # Matches every other "Load ..." button in this tab (Load DXF
        # Layout, Load Circle Params, Load Alignment, Load probe pixel<->
        # stage calibration) -- must not silently fall back to the fixed
        # default config path.
        path = self._write_z_seed_file({'electrode_z_seeds': {}, 'parallax_samples': [], 'xy_bias_samples': []})
        original = self.app._image_electrode_z_store
        with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=path) as mocked_dialog:
            result = self.app._image_load_z_seed_store()
        mocked_dialog.assert_called_once()
        self.assertTrue(result)
        self.assertIsNot(self.app._image_electrode_z_store, original)
        self.assertIn('loaded', self.app._image_z_seed_status_var.get())

    def test_image_load_z_seed_store_reports_parallax_sample_count(self):
        # The actual bug report: after loading, the status looked like it
        # still said "no parallax samples" -- because the user was reading
        # the unrelated live-search ROI panel var, not this one, which
        # never mentioned parallax at all. Now it does.
        payload = {
            'electrode_z_seeds': {
                '0': {'z_mm': -0.92, 'source': 'manual', 'updated_at': '', 'xy_mm': [0.0, 0.0]},
                '1': {'z_mm': -0.93, 'source': 'manual', 'updated_at': '', 'xy_mm': [1.0, 0.0]},
                '2': {'z_mm': -0.94, 'source': 'manual', 'updated_at': '', 'xy_mm': [0.0, 1.0]},
            },
            'parallax_samples': [
                {'delta_z_mm': 0.50, 'delta_pixel_x': -8.0, 'delta_pixel_y': -3.0, 'recorded_at': ''},
                {'delta_z_mm': 0.55, 'delta_pixel_x': -9.0, 'delta_pixel_y': -2.0, 'recorded_at': ''},
                {'delta_z_mm': 0.60, 'delta_pixel_x': -7.0, 'delta_pixel_y': -1.0, 'recorded_at': ''},
            ],
            'xy_bias_samples': [],
        }
        path = self._write_z_seed_file(payload)

        with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=path):
            self.app._image_load_z_seed_store()

        self.assertIsNotNone(self.app._image_electrode_z_store.z_parallax)
        status = self.app._image_z_seed_status_var.get()
        self.assertIn('3 parallax sample(s)', status)
        self.assertIn('slope fit', status)

    def test_image_load_z_seed_store_reports_no_parallax_samples(self):
        path = self._write_z_seed_file({'electrode_z_seeds': {}, 'parallax_samples': [], 'xy_bias_samples': []})

        with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=path):
            self.app._image_load_z_seed_store()

        self.assertIn('no parallax samples', self.app._image_z_seed_status_var.get())

    def test_image_load_z_seed_store_cancelled_dialog_is_a_noop(self):
        original = self.app._image_electrode_z_store
        with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=''):
            result = self.app._image_load_z_seed_store()
        self.assertFalse(result)
        self.assertIs(self.app._image_electrode_z_store, original)

    def test_image_load_z_seed_store_bad_file_reports_failure_and_keeps_current_store(self):
        original = self.app._image_electrode_z_store
        original.record_contact(0, 5.0, source='auto_contact')

        result = self.app._image_load_z_seed_store(path='/nonexistent/path/electrode_z_seed.json')

        self.assertFalse(result)
        self.assertIs(self.app._image_electrode_z_store, original)
        self.assertIn('load failed', self.app._image_z_seed_status_var.get())

    def test_image_clear_z_seed_store_requires_confirmation(self):
        self.app._image_electrode_z_store.record_contact(0, 5.0, source='auto_contact')
        with mock.patch.object(gui.messagebox, 'askyesno', return_value=False):
            self.app._image_clear_z_seed_store()
        self.assertEqual(len(self.app._image_electrode_z_store.electrode_z_seeds), 1)

    def test_image_clear_z_seed_store_clears_and_saves_when_confirmed(self):
        self.app._image_electrode_z_store.record_contact(0, 5.0, source='auto_contact')
        with mock.patch.object(gui.messagebox, 'askyesno', return_value=True), \
             mock.patch.object(self.app._image_electrode_z_store, 'save') as mocked_save:
            self.app._image_clear_z_seed_store()
        self.assertEqual(self.app._image_electrode_z_store.electrode_z_seeds, {})
        mocked_save.assert_called_once()
        self.assertIn('cleared', self.app._image_z_seed_status_var.get())

    def test_image_refit_z_surface_reports_coefficients_on_success(self):
        self.app._image_layout_model = self._make_square_layout()
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            self.app._image_electrode_z_store.record_contact(
                0, 1.0, source='auto_contact', xy_mm=(0.0, 0.0),
            )
            self.app._image_electrode_z_store.record_contact(
                1, 2.0, source='auto_contact', xy_mm=(1.0, 0.0),
            )
            self.app._image_electrode_z_store.record_contact(
                2, 3.0, source='auto_contact', xy_mm=(0.0, 1.0),
            )
            self.app._image_refit_z_surface()
        text = self.app._image_z_seed_status_var.get()
        self.assertIn('Z surface fit:', text)
        self.assertIn('a=', text)
        self.assertIn('from 3 electrode(s)', text)

    def test_image_refit_z_surface_reports_insufficient_electrodes(self):
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            self.app._image_refit_z_surface()
        self.assertIn('not enough', self.app._image_z_seed_status_var.get())

    def test_image_layout_index_for_target_dxf_layout(self):
        self.assertEqual(self.app._image_layout_index_for_target({"col_index": 4, "source": "dxf_layout"}), 3)

    def test_image_layout_index_for_target_non_dxf(self):
        self.assertIsNone(self.app._image_layout_index_for_target({"col_index": 4, "source": "live"}))

    def test_image_layout_index_for_target_none(self):
        self.assertIsNone(self.app._image_layout_index_for_target(None))

    def test_image_record_electrode_z_contact_updates_seed_from_row_label(self):
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            self.app._image_record_electrode_z_contact({"Label": "E3_test"}, 12.34, None)
        self.assertAlmostEqual(self.app._image_electrode_z_store.get_seed(2), 12.34, places=6)

    def test_image_record_electrode_z_contact_skips_row_without_electrode_id(self):
        with mock.patch.object(self.app._image_electrode_z_store, 'save'):
            self.app._image_record_electrode_z_contact({"Label": "no_electrode_here"}, 12.34, None)
        self.assertEqual(self.app._image_electrode_z_store.electrode_z_seeds, {})

    # ══════════════════════════════════════════════════════════════════
    # Camera/layout setup simplification: one-click probe-cal capture,
    # live-motor-Z-sourced Z-seed contact searches
    # ══════════════════════════════════════════════════════════════════

    def test_image_add_pixel_stage_calibration_point_uses_displayed_candidate(self):
        # Must capture whatever tip location is already on screen, not
        # re-detect -- a fresh detection on a newer live frame could land
        # somewhere other than the crosshair the user is looking at.
        self.app._image_probe_tip_candidate = (12.0, 34.0)
        self.app._image_detect_probe_tip = mock.Mock()
        self.app._manual_current['x'].set('1.0')
        self.app._manual_current['y'].set('2.0')
        self.app._manual_current['z'].set('3.0')

        self.app._image_add_pixel_stage_calibration_point()

        self.app._image_detect_probe_tip.assert_not_called()
        self.assertEqual(len(self.app._image_pixel_stage_calibration_refs), 1)
        ref = self.app._image_pixel_stage_calibration_refs[0]
        self.assertEqual((ref.pixel_x, ref.pixel_y), (12.0, 34.0))
        self.assertEqual((ref.stage_x_mm, ref.stage_y_mm), (1.0, 2.0))
        self.assertEqual(ref.z_mm, 3.0)

    def test_image_add_pixel_stage_calibration_point_fails_without_prior_detection(self):
        self.app._image_probe_tip_candidate = None
        self.app._image_detect_probe_tip = mock.Mock()

        self.app._image_add_pixel_stage_calibration_point()

        self.app._image_detect_probe_tip.assert_not_called()
        self.assertEqual(self.app._image_pixel_stage_calibration_refs, [])
        self.assertIn('detect the probe tip first', self.app._image_pixel_stage_affine_status_var.get())

    def test_image_seed_z_from_contact_uses_live_motor_z_not_manual_target(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app._manual_target['z'].set('2.0')  # stale -- must not be used
        self.app._image_selected_target = {'col_index': 1, 'source': 'dxf_layout'}
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)

        self.app._image_seed_z_from_contact()

        self.app._execute_contact_z_search.assert_called_once()
        kwargs = self.app._execute_contact_z_search.call_args.kwargs
        self.assertEqual(kwargs['base_z'], 7.5)

    def test_image_seed_z_from_contact_probe_pixel_sample_fn_uses_target_layout_index(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app._image_selected_target = {'col_index': 1, 'source': 'dxf_layout'}  # layout_index 0
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)
        self.app._image_probe_tip_pixel_now = mock.Mock(return_value=(11.0, 22.0))

        self.app._image_seed_z_from_contact()

        kwargs = self.app._execute_contact_z_search.call_args.kwargs
        result = kwargs['probe_pixel_sample_fn']()
        self.assertEqual(result, (11.0, 22.0))
        self.app._image_probe_tip_pixel_now.assert_called_once_with(layout_index=0)

    def test_image_seed_z_from_contact_also_recalibrates_xy_bias(self):
        # Manual "Search Z" is a full contact-search success path just
        # like AutoContactZ (_auto_contact_z_for_row makes the same call
        # after a confirmed contact) -- must build up the same XY-bias
        # calibration, not just Z-seed/parallax.
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app._image_selected_target = {'col_index': 1, 'source': 'dxf_layout'}  # layout_index 0
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)

        self.app._image_seed_z_from_contact()

        self.app._image_recalibrate_xy_bias_from_contact.assert_called_once_with(0)

    def test_image_seed_z_from_contact_skips_xy_bias_when_target_not_a_layout_electrode(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app._image_selected_target = {'col_index': 1, 'source': 'live_detection'}  # not DXF-layout
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)

        self.app._image_seed_z_from_contact()

        self.app._image_recalibrate_xy_bias_from_contact.assert_not_called()

    def test_auto_contact_z_for_row_probe_pixel_sample_fn_uses_row_layout_index(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app.bl = mock.Mock()
        row = {'AutoContactZ': '1', 'Label': 'E3_600C', 'Z_mm': None}
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)
        self.app._image_probe_tip_pixel_now = mock.Mock(return_value=(11.0, 22.0))

        self.app._auto_contact_z_for_row(row, 'E3_600C')

        kwargs = self.app._execute_contact_z_search.call_args.kwargs
        result = kwargs['probe_pixel_sample_fn']()
        self.assertEqual(result, (11.0, 22.0))
        # "E3" -> electrode_id 3 -> layout_index 2 (0-based).
        self.app._image_probe_tip_pixel_now.assert_called_once_with(layout_index=2)

    def test_auto_contact_z_for_row_not_enabled_returns_none_none(self):
        row = {'AutoContactZ': '0', 'Label': 'E3_600C', 'Z_mm': None}

        result = self.app._auto_contact_z_for_row(row, 'E3_600C')

        self.assertEqual(result, (None, None))

    def test_auto_contact_z_for_row_confirmed_contact_returns_true(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app.bl = mock.Mock()
        row = {'AutoContactZ': '1', 'Label': 'E3_600C', 'Z_mm': None}
        self.app._execute_contact_z_search = mock.Mock(return_value=(7.5, 7.6, None))
        self.app._image_record_electrode_z_contact = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)

        result = self.app._auto_contact_z_for_row(row, 'E3_600C')

        self.assertEqual(result, (7.6, True))
        self.app._image_record_electrode_z_contact.assert_called_once()
        self.app._image_recalibrate_xy_bias_from_contact.assert_called_once()

    def test_auto_contact_z_for_row_collect_anyway_returns_last_z_and_false(self):
        # Default policy ('collect_anyway'): an unconfirmed contact search
        # still returns a usable Z (the search's last-probed position, not
        # a bare failure) and must NOT touch the Z-seed/parallax/XY-bias
        # calibration stores or the same-XY contact cache with that
        # unconfirmed height.
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app.bl = mock.Mock()
        self.assertEqual(self.app._run_contact_fail_policy_var.get(), 'collect_anyway')
        row = {'AutoContactZ': '1', 'Label': 'E3_600C', 'Z_mm': None}
        self.app._execute_contact_z_search = mock.Mock(
            side_effect=gui.ContactNotConfirmedError(
                'Contact search did not find a valid OCV threshold', last_z=8.123,
            )
        )
        self.app._image_record_electrode_z_contact = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)
        contact_z_cache = {}

        result = self.app._auto_contact_z_for_row(row, 'E3_600C', contact_z_cache)

        self.assertEqual(result, (8.123, False))
        self.app._image_record_electrode_z_contact.assert_not_called()
        self.app._image_recalibrate_xy_bias_from_contact.assert_not_called()
        self.assertEqual(contact_z_cache, {})

    def test_auto_contact_z_for_row_stop_policy_reraises_on_unconfirmed_contact(self):
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 7.5
        self.app.bl = mock.Mock()
        self.app._run_contact_fail_policy_var.set('stop')
        row = {'AutoContactZ': '1', 'Label': 'E3_600C', 'Z_mm': None}
        self.app._execute_contact_z_search = mock.Mock(
            side_effect=gui.ContactNotConfirmedError(
                'Contact search did not find a valid OCV threshold', last_z=8.123,
            )
        )

        with self.assertRaises(gui.ContactNotConfirmedError):
            self.app._auto_contact_z_for_row(row, 'E3_600C')

    def test_run_worker_auto_contact_z_initial_move_targets_search_start_not_seed(self):
        # The row-loop's initial move-to-electrode must target the AutoContactZ
        # search's own start height (seed +/- ContactStartOffset_mm), never the
        # raw seed Z directly -- moving to the seed first would send the tip
        # down to/through the electrode surface at full move speed before
        # _execute_contact_z_search's careful step-wise approach even begins.
        # positive_z_is_up=False in this project's config.py -> approach_sign
        # = +1, so start_z = seed - ContactStartOffset_mm (default 0.200).
        motor = self._make_stateful_motor(x=1.0, y=2.0, z=10.0)
        self.app.motor = motor
        self.app.bl = mock.Mock()
        self.app._execute_contact_z_search = mock.Mock(return_value=(10.03, 10.03, None))
        self.app._image_record_electrode_z_contact = mock.Mock(return_value=None)
        self.app._image_recalibrate_xy_bias_from_contact = mock.Mock(return_value=None)

        def fake_rapid_sequence(**kwargs):
            return SequenceResult(
                measurement_mode='rapid_eis',
                eis_data=np.array([[1.0, 10.0, 2.0]]),
                ca_data=np.array([[0.0, kwargs['v_dc'], 1e-6]]),
                pre_ca_data=np.array([[0.0, kwargs['v_dc'], 1e-6]]),
                pre_ca_path=os.path.join(kwargs['save_dir'], f"{kwargs['label']}_pre.txt"),
                peis_path=os.path.join(kwargs['save_dir'], f"{kwargs['label']}_peis.txt"),
                post_ca_path=os.path.join(kwargs['save_dir'], f"{kwargs['label']}_post.txt"),
            )
        gui.MODS['sequence'] = fake_rapid_sequence
        gui.MODS['rapid_sequence'] = fake_rapid_sequence

        row = self._make_row('E1_test', 0.1)
        row['X_mm'] = 1.0
        row['Y_mm'] = 2.0
        row['Z_mm'] = 10.0
        row['AutoContactZ'] = 1

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()

        motor.move_xyz_safe.assert_called_once()
        _args, kwargs = motor.move_xyz_safe.call_args
        self.assertAlmostEqual(kwargs['z_mm'], 9.800, places=6)

    def test_run_olecom_hybrid_sequence_dynamic_lf_honors_row_peis_f_low_as_deep_limit(self):
        # Regression: the row's own PEIS_fLow used to be silently discarded
        # whenever dynamic_lf=True (i.e. on every real Semi-auto/Full-auto
        # OLE-COM row), replaced by a fixed 0.1 Hz deep_limit -- so a user
        # asking for lower frequencies via the PEIS f low field had no
        # effect at all. peis_deep_limit passed to run_hybrid_live_stop
        # must now reflect the row's PEIS_fLow; peis_overlap_limit (the
        # guaranteed-overlap ceiling) is unchanged, still the config default.
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        captured = {}

        def fake_run_hybrid_live_stop(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(summary={}, output_dir='out')

        self.app.bl.run_hybrid_live_stop = fake_run_hybrid_live_stop

        row = self._make_row('E1_test', 0.1)
        row['PEIS_fLow'] = 0.02

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._run_olecom_hybrid_sequence(
                row=row, label='E1_test', save_dir=tmpdir, channel=1,
                stop_event=threading.Event(), dynamic_lf=True,
            )

        self.assertAlmostEqual(captured['peis_deep_limit'], 0.02, places=9)
        self.assertAlmostEqual(
            captured['peis_overlap_limit'], gui.BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ, places=9
        )

    def test_run_olecom_hybrid_sequence_dynamic_lf_defaults_to_config_deep_limit_when_row_omits_peis_f_low(self):
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        captured = {}

        def fake_run_hybrid_live_stop(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(summary={}, output_dir='out')

        self.app.bl.run_hybrid_live_stop = fake_run_hybrid_live_stop

        row = self._make_row('E1_test', 0.1)
        del row['PEIS_fLow']

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._run_olecom_hybrid_sequence(
                row=row, label='E1_test', save_dir=tmpdir, channel=1,
                stop_event=threading.Event(), dynamic_lf=True,
            )

        self.assertAlmostEqual(
            captured['peis_deep_limit'], gui.BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ, places=9
        )

    def test_run_olecom_hybrid_sequence_honors_row_peis_bandwidth_and_n_average(self):
        # Regression: PEIS bandwidth was hardcoded to BIOLOGIC_OLECOM_BANDWIDTH
        # regardless of the row (unlike CA bandwidth, which correctly read
        # row.get('CA_Bandwidth')), and PEIS N-average was never threaded
        # into run_hybrid_live_stop at all.
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        captured = {}

        def fake_run_hybrid_live_stop(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(summary={}, output_dir='out')

        self.app.bl.run_hybrid_live_stop = fake_run_hybrid_live_stop

        row = self._make_row('E1_test', 0.1)
        row['PEIS_Bandwidth'] = 8
        row['PEIS_NAverage'] = 3

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._run_olecom_hybrid_sequence(
                row=row, label='E1_test', save_dir=tmpdir, channel=1,
                stop_event=threading.Event(), dynamic_lf=True,
            )

        self.assertEqual(captured['bandwidth'], 8)
        self.assertEqual(captured['peis_n_average'], 3)

    def test_run_olecom_hybrid_sequence_defaults_peis_bandwidth_and_n_average_when_row_omits_them(self):
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        captured = {}

        def fake_run_hybrid_live_stop(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(summary={}, output_dir='out')

        self.app.bl.run_hybrid_live_stop = fake_run_hybrid_live_stop

        row = self._make_row('E1_test', 0.1)

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._run_olecom_hybrid_sequence(
                row=row, label='E1_test', save_dir=tmpdir, channel=1,
                stop_event=threading.Event(), dynamic_lf=True,
            )

        self.assertEqual(captured['bandwidth'], gui.BIOLOGIC_OLECOM_BANDWIDTH)
        self.assertEqual(captured['peis_n_average'], 1)

    def test_run_worker_uses_fixed_lf_policy_for_non_adaptive_row_and_adaptive_for_adapt_row(self):
        # The actual root-cause fix: _run_worker's OLE-COM dispatch used to
        # hardcode dynamic_lf=True for every row regardless of origin,
        # silently overriding Semi-auto's own CA_duration_s/PEIS_fLow with
        # the fixed adaptive scout window (100-300s) and floor/ceiling
        # (0.1-0.5 Hz). Semi-auto rows (no ADAPT label prefix) must now get
        # the fixed/manual policy; Full-auto (ADAPT-prefixed) rows must keep
        # the adaptive policy unchanged. defer_postprocess must stay True
        # for both regardless (decoupled from dynamic_lf on purpose).
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        calls = []

        def fake_run_hybrid_live_stop(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(summary={}, output_dir='out')

        self.app.bl.run_hybrid_live_stop = fake_run_hybrid_live_stop

        semi_auto_row = self._make_row('T600_GA0_GB20_E1_X1_Y2_Z3_V+0', 0.0)
        semi_auto_row['CA_duration_s'] = 900.0
        semi_auto_row['PEIS_fLow'] = 0.02
        adapt_row = self._make_row('ADAPT_T600_GA0_GB20_E1_X1_Y2_Z3_V+0', 0.0)
        adapt_row['CA_duration_s'] = 900.0
        adapt_row['PEIS_fLow'] = 0.02

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([semi_auto_row, adapt_row])
            self.app._run_worker()

        self.assertEqual(len(calls), 2)
        semi_auto_kwargs, adapt_kwargs = calls

        # Semi-auto: fixed policy -- scout window follows the 900s
        # CA_duration_s directly, and PEIS LF is pinned exactly to 0.02 Hz
        # (both deep_limit and overlap_limit equal the requested value).
        self.assertAlmostEqual(semi_auto_kwargs['scout_min_s'], 900.0, places=6)
        self.assertAlmostEqual(semi_auto_kwargs['scout_max_s'], 900.0, places=6)
        self.assertAlmostEqual(semi_auto_kwargs['peis_deep_limit'], 0.02, places=9)
        self.assertAlmostEqual(semi_auto_kwargs['peis_overlap_limit'], 0.02, places=9)
        self.assertTrue(semi_auto_kwargs['defer_postprocess'])

        # Full-auto/adaptive: fixed 100-300s scout window and the
        # config-default overlap ceiling, unaffected by CA_duration_s/PEIS_fLow.
        self.assertAlmostEqual(
            adapt_kwargs['scout_min_s'], float(gui.BIOLOGIC_OLECOM_MIN_FFT_DURATION_S), places=6
        )
        self.assertAlmostEqual(
            adapt_kwargs['scout_max_s'], float(gui.BIOLOGIC_OLECOM_SCOUT_MAX_S), places=6
        )
        self.assertAlmostEqual(adapt_kwargs['peis_deep_limit'], 0.02, places=9)
        self.assertAlmostEqual(
            adapt_kwargs['peis_overlap_limit'], gui.BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ, places=9
        )
        self.assertTrue(adapt_kwargs['defer_postprocess'])

    def test_run_worker_skip_ca_bypasses_olecom_hybrid_protocol(self):
        # SkipCA=1 must be an absolute guarantee of no CA call -- even when
        # connected to the OLE-COM backend, whose hybrid live-stop protocol
        # is fundamentally CA/FFT-seeded and would otherwise always run
        # regardless of SkipCA.
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'olecom'
        self.app.bl.run_hybrid_live_stop = mock.Mock(
            side_effect=AssertionError('run_hybrid_live_stop must not be called when SkipCA=1')
        )
        calls = []

        def fake_normal_sequence(**kwargs):
            calls.append(kwargs['label'])
            return SequenceResult(
                measurement_mode='normal_eis',
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs['save_dir'], f"{kwargs['label']}_peis_only.txt"),
            )
        gui.MODS['normal_sequence'] = fake_normal_sequence

        row = self._make_row('E1_test', 0.1)
        row['SkipCA'] = 1

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()

        self.app.bl.run_hybrid_live_stop.assert_not_called()
        self.assertEqual(calls, ['E1_test'])

    def test_run_worker_skip_ca_bypasses_adaptive_live_seeded_protocol(self):
        # Same guarantee, for the (non-OLE-COM) adaptive live-seeded path.
        self.app.bl = mock.Mock()
        self.app.bl.backend_name = 'legacy'
        calls = []

        def fake_rapid_sequence(**kwargs):
            calls.append(('rapid', kwargs['label']))
            raise AssertionError('rapid_sequence must not be called when SkipCA=1')

        def fake_normal_sequence(**kwargs):
            calls.append(('normal', kwargs['label']))
            return SequenceResult(
                measurement_mode='normal_eis',
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs['save_dir'], f"{kwargs['label']}_peis_only.txt"),
            )
        gui.MODS['sequence'] = fake_rapid_sequence
        gui.MODS['rapid_sequence'] = fake_rapid_sequence
        gui.MODS['normal_sequence'] = fake_normal_sequence

        row = self._make_row('ADAPT_T300_E1_V+0.000', 0.0)
        row['SkipCA'] = 1

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch('live_seeded_sequence.live_seeded_rapid_eis_sequence',
                        side_effect=AssertionError('live_seeded_rapid_eis_sequence must not be called when SkipCA=1')):
            self.app._result_dir.set(tmpdir)
            self.app.condition_df = pd.DataFrame([row])
            self.app._run_worker()

        self.assertEqual(calls, [('normal', 'ADAPT_T300_E1_V+0.000')])

    def test_contact_fail_policy_dropdown_defaults_to_collect_anyway_and_offers_it(self):
        self.assertEqual(self.app._run_contact_fail_policy_var.get(), 'collect_anyway')
        combo = self._find_widget_by_class_and_textvariable(
            self.app, gui.ttk.Combobox, self.app._run_contact_fail_policy_var,
        )
        self.assertIsNotNone(combo)
        self.assertIn('collect_anyway', combo.cget('values'))
        self.assertIn('stop', combo.cget('values'))
        self.assertIn('skip_row', combo.cget('values'))

    def _find_widget_by_class_and_textvariable(self, widget, cls, var):
        for child in widget.winfo_children():
            if isinstance(child, cls):
                try:
                    if child.cget('textvariable') == str(var):
                        return child
                except Exception:
                    pass
            found = self._find_widget_by_class_and_textvariable(child, cls, var)
            if found is not None:
                return found
        return None

    def _find_button_by_text(self, widget, text):
        for child in widget.winfo_children():
            if isinstance(child, (gui.ttk.Button, gui.tk.Button)) and child.cget('text') == text:
                return child
            found = self._find_button_by_text(child, text)
            if found is not None:
                return found
        return None

    def _find_checkbutton_by_text(self, widget, text):
        for child in widget.winfo_children():
            if isinstance(child, gui.ttk.Checkbutton) and child.cget('text') == text:
                return child
            found = self._find_checkbutton_by_text(child, text)
            if found is not None:
                return found
        return None

    def _canvas_text(self, canvas):
        """Concatenated text of every text-type item drawn on canvas --
        used to assert on placeholder/error text drawn via create_text
        (e.g. self._image_parallax_panel_canvas), same idea as
        _find_button_by_text but for Canvas-drawn text instead of widgets."""
        parts = []
        for item in canvas.find_all():
            if canvas.type(item) == 'text':
                parts.append(canvas.itemcget(item, 'text'))
        return ' '.join(parts)

    def test_image_search_z_button_runs_via_run_manual_action(self):
        # Must be backgrounded like every other long-running manual action
        # ("Find Contact Z" already is), so it doesn't freeze the GUI
        # thread during a contact search. "Search Z All" and its underlying
        # _image_seed_z_for_paired_electrodes method were both removed per
        # user request -- only "Search Z" remains.
        self.app._run_manual_action = mock.Mock()

        from_contact_btn = self._find_button_by_text(self.app, 'Search Z')
        self.assertIsNotNone(from_contact_btn)
        self.assertIsNone(self._find_button_by_text(self.app, 'Search Z All'))

        from_contact_btn.invoke()

        self.assertEqual(self.app._run_manual_action.call_count, 1)
        called_actions = [call_args.args[0] for call_args in self.app._run_manual_action.call_args_list]
        self.assertIn(self.app._image_seed_z_from_contact, called_actions)

    def test_stop_tip_button_removed_from_both_tabs(self):
        # No reliable way to interrupt a move once started was ever found
        # (SL 0 doesn't preempt an in-progress MA/MR; no STOP mnemonic
        # exists in this motor's command set) -- the button was removed
        # outright rather than leave a control that gives false confidence.
        self.assertIsNone(self._find_button_by_text(self.app, 'Stop Tip'))

    def test_move_tip_button_style_active_during_move_and_reverts_on_failure(self):
        captured_styles = {}

        def fake_move_xyz_safe(**kwargs):
            captured_styles['manual'] = self.app._manual_move_tip_button.cget('style')
            captured_styles['image'] = self.app._image_move_tip_button.cget('style')
            raise RuntimeError('stop before a real move runs')

        self.app.motor = mock.Mock()
        self.app.motor.move_xyz_safe.side_effect = fake_move_xyz_safe

        with self.assertRaises(RuntimeError):
            self.app._manual_move_stage()

        self.assertEqual(captured_styles.get('manual'), 'ImageModeActive.TButton')
        self.assertEqual(captured_styles.get('image'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._manual_move_tip_button.cget('style'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._image_move_tip_button.cget('style'), 'ImageModeActive.TButton')

    def test_move_tip_button_style_reverts_after_successful_move(self):
        self.app.motor = mock.Mock()

        self.app._manual_move_stage()

        self.assertNotEqual(self.app._manual_move_tip_button.cget('style'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._image_move_tip_button.cget('style'), 'ImageModeActive.TButton')

    def test_move_tip_button_style_unchanged_when_motor_not_connected(self):
        # The early-return path (nothing actually moves) shouldn't flash
        # the active style at all.
        self.app.motor = None

        self.app._manual_move_stage()

        self.assertNotEqual(self.app._manual_move_tip_button.cget('style'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._image_move_tip_button.cget('style'), 'ImageModeActive.TButton')

    def test_camera_stage_section_has_lock_z_checkbox_defaulting_checked(self):
        checkbox = self._find_checkbutton_by_text(self.app, 'Lock Z')
        self.assertIsNotNone(checkbox)
        self.assertTrue(self.app._image_lock_z_var.get())

    def test_z_seed_store_has_save_load_clear_fit_buttons(self):
        for text in ('Save Z Seed', 'Load Z Seed', 'Clear Z Seed', 'Z Surface Fit'):
            self.assertIsNotNone(self._find_button_by_text(self.app, text), text)

    # ══════════════════════════════════════════════════════════════════
    # Round 3: mode/process buttons recolor while active
    # ══════════════════════════════════════════════════════════════════

    def test_probe_roi_button_style_reflects_select_mode(self):
        button = self.app._image_probe_roi_button
        self.assertNotEqual(button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_toggle_probe_roi_select_mode()
        self.assertEqual(button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_toggle_probe_roi_select_mode()
        self.assertNotEqual(button.cget('style'), 'ImageModeActive.TButton')

    def test_clear_probe_roi_resets_button_style(self):
        self.app._image_toggle_probe_roi_select_mode()
        self.assertEqual(self.app._image_probe_roi_button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_clear_probe_roi()

        self.assertNotEqual(self.app._image_probe_roi_button.cget('style'), 'ImageModeActive.TButton')

    def test_draw_circles_button_style_reflects_draw_mode(self):
        self.app._image_layout_model = gui.LayoutModel(
            np.array([[0, 0], [10, 0]], dtype=np.float32)
        )
        button = self.app._image_draw_circles_button
        self.assertNotEqual(button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_toggle_layout_draw_mode()
        self.assertEqual(button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_toggle_layout_draw_mode()
        self.assertNotEqual(button.cget('style'), 'ImageModeActive.TButton')

    def test_image_monitor_stop_resets_mode_button_styles(self):
        self.app._image_layout_model = gui.LayoutModel(
            np.array([[0, 0], [10, 0]], dtype=np.float32)
        )
        self.app._image_toggle_probe_roi_select_mode()
        self.app._image_toggle_layout_draw_mode()
        self.assertEqual(self.app._image_probe_roi_button.cget('style'), 'ImageModeActive.TButton')
        self.assertEqual(self.app._image_draw_circles_button.cget('style'), 'ImageModeActive.TButton')

        self.app._image_monitor_stop()

        self.assertNotEqual(self.app._image_probe_roi_button.cget('style'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._image_draw_circles_button.cget('style'), 'ImageModeActive.TButton')

    def test_search_z_button_style_active_during_search_and_reverts_on_failure(self):
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 3.0
        captured_style = {}

        def fake_search(**kwargs):
            captured_style['during'] = self.app._image_search_z_button.cget('style')
            raise RuntimeError('stop before a real search runs')

        self.app._execute_contact_z_search = mock.Mock(side_effect=fake_search)

        self.app._image_seed_z_from_contact()

        self.assertEqual(captured_style.get('during'), 'ImageModeActive.TButton')
        self.assertNotEqual(self.app._image_search_z_button.cget('style'), 'ImageModeActive.TButton')

    def test_search_z_button_style_reverts_after_successful_search(self):
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 3.0
        self.app._execute_contact_z_search = mock.Mock(return_value=(3.0, 3.0, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)

        self.app._image_seed_z_from_contact()

        self.assertNotEqual(self.app._image_search_z_button.cget('style'), 'ImageModeActive.TButton')

    # ══════════════════════════════════════════════════════════════════
    # Camera-control simplification, concurrent Manual Control strip,
    # probe-tip ROI selection, DXF-alignment Hough snap
    # ══════════════════════════════════════════════════════════════════

    def test_image_monitor_camera_controls_default_without_widgets(self):
        self.assertEqual(self.app._image_backend_var.get(), 'any')
        self.assertEqual(self.app._image_index_var.get(), '0')
        self.assertFalse(hasattr(self.app, '_image_detector_var'))
        removed_labels = {'Backend', 'Index', 'Detector', 'Expected visible electrodes'}
        found_labels = {
            widget.cget('text') for widget in self._collect_widgets(self.app.tab_image, gui.tk.Label)
        }
        self.assertEqual(removed_labels & found_labels, set())

    def _collect_widgets(self, widget, kind):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, kind):
                found.append(child)
            found.extend(self._collect_widgets(child, kind))
        return found

    def _collect_textvariable_names(self, widget, kind):
        names = []
        for child in self._collect_widgets(widget, kind):
            try:
                names.append(str(child.cget('textvariable')))
            except Exception:
                pass
        return names

    def test_image_monitor_stage_strip_shares_manual_control_variables(self):
        target_names = self._collect_textvariable_names(self.app.tab_image, gui.tk.Entry)
        for key in ('x', 'y', 'z'):
            self.assertIn(str(self.app._manual_target[key]), target_names)

        current_names = self._collect_textvariable_names(self.app.tab_image, gui.tk.Label)
        for key in ('x', 'y', 'z'):
            self.assertIn(str(self.app._manual_current[key]), current_names)

    def test_image_monitor_stage_strip_buttons_run_shared_manual_callbacks(self):
        self.app._run_manual_action = mock.Mock()
        move_btn = self._find_button_by_text(self.app.tab_image, 'Move Tip')
        refresh_btn = self._find_button_by_text(self.app.tab_image, 'Refresh Current State')
        self.assertIsNotNone(move_btn)
        self.assertIsNotNone(refresh_btn)

        move_btn.invoke()
        refresh_btn.invoke()

        called_actions = [call_args.args[0] for call_args in self.app._run_manual_action.call_args_list]
        self.assertIn(self.app._manual_move_stage, called_actions)
        self.assertIn(self.app._manual_refresh_state, called_actions)

    def test_manual_move_stage_uses_safe_xyz_move(self):
        # "Move Tip" (Manual Control, and the shared Camera & Stage section
        # in Image Monitor -- the sole move path for both) must lift Z to a
        # clearance height before translating X/Y and lower it after, not a
        # raw per-axis move that could drag the tip across the sample.
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 1.0, "Y": 2.0, "Z": 3.0}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self.app._manual_target['x'].set('10.000')
        self.app._manual_target['y'].set('20.000')
        self.app._manual_target['z'].set('4.000')

        self.app._manual_move_stage()

        motor.move_abs_wait.assert_not_called()
        motor.move_xyz_safe.assert_called_once()
        _args, kwargs = motor.move_xyz_safe.call_args
        self.assertAlmostEqual(kwargs['x_mm'], 10.0)
        self.assertAlmostEqual(kwargs['y_mm'], 20.0)
        self.assertAlmostEqual(kwargs['z_mm'], 4.0)
        self.assertEqual(kwargs['current_positions'], {"X": 1.0, "Y": 2.0, "Z": 3.0})

    def test_image_load_dxf_layout_auto_saves_layout_json(self):
        # "Save Layout JSON" was removed as a manual button; run_automation.py's
        # headless AutoTrackXY hard-requires a layout JSON at
        # config.VISION_ELECTRODE_LAYOUT_PATH, so loading a DXF must now
        # write that file automatically instead.
        fake_circles = [
            {'x': 0.0, 'y': 0.0, 'radius': 5.0},
            {'x': 10.0, 'y': 0.0, 'radius': 5.0},
            {'x': 0.0, 'y': 10.0, 'radius': 5.0},
        ]
        with mock.patch.object(gui, 'extract_circles_from_dxf', return_value=fake_circles), \
             mock.patch.object(gui, 'shift_to_origin', side_effect=lambda c: c), \
             mock.patch.object(gui, 'warn_if_spacing_implausible', return_value=None), \
             mock.patch.object(gui, 'write_layout_json') as write_mock, \
             mock.patch.object(gui, 'VISION_DXF_LAYOUT_AVAILABLE', True):
            self.app._image_load_dxf_layout(path='fake.dxf')

        write_mock.assert_called_once()
        args, _kwargs = write_mock.call_args
        written_circles, written_path = args
        self.assertEqual(len(written_circles), 3)
        self.assertEqual(written_path, gui._config.VISION_ELECTRODE_LAYOUT_PATH)
        self.assertNotIn('auto-save failed', self.app._image_layout_status_var.get())

    def test_image_load_dxf_layout_reports_auto_save_failure_without_failing_load(self):
        fake_circles = [
            {'x': 0.0, 'y': 0.0, 'radius': 5.0},
            {'x': 10.0, 'y': 0.0, 'radius': 5.0},
            {'x': 0.0, 'y': 10.0, 'radius': 5.0},
        ]
        with mock.patch.object(gui, 'extract_circles_from_dxf', return_value=fake_circles), \
             mock.patch.object(gui, 'shift_to_origin', side_effect=lambda c: c), \
             mock.patch.object(gui, 'warn_if_spacing_implausible', return_value=None), \
             mock.patch.object(gui, 'write_layout_json', side_effect=OSError('disk full')), \
             mock.patch.object(gui, 'VISION_DXF_LAYOUT_AVAILABLE', True):
            self.app._image_load_dxf_layout(path='fake.dxf')

        self.assertIsNotNone(self.app._image_layout_model)
        self.assertIn('auto-save failed', self.app._image_layout_status_var.get())
        self.assertIn('disk full', self.app._image_layout_status_var.get())

    def _set_up_rendered_frame(self, frame_w=160, frame_h=120):
        self.app._image_render_shape = (frame_h, frame_w)
        self.app._image_render_size = (frame_w, frame_h)
        self.app._image_render_offset = (0, 0)

    def test_image_detect_probe_tip_passes_roi_to_detector(self):
        self.app._image_probe_roi = (10, 20, 30, 40)
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        self.app._image_detect_probe_tip()

        fake_detector.detect.assert_called_once()
        _args, kwargs = fake_detector.detect.call_args
        self.assertEqual(kwargs.get('roi'), (10, 20, 30, 40))

    def test_image_probe_tip_pixel_now_passes_roi_to_detector(self):
        self.app._image_probe_roi = (1, 2, 3, 4)
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        self.app._image_probe_tip_pixel_now()

        fake_detector.detect.assert_called_once()
        _args, kwargs = fake_detector.detect.call_args
        self.assertEqual(kwargs.get('roi'), (1, 2, 3, 4))

    def test_image_probe_tip_pixel_now_uses_tight_roi_for_tracked_electrode(self):
        # Same idea as _image_recalibrate_xy_bias_from_contact's ROI: a box
        # centered on the electrode's own tracked pixel, sized to its
        # tracked radius -- not the generic user-drawn _image_probe_roi.
        self.app._image_probe_roi = (1, 2, 3, 4)
        self.app._image_layout_tracked = [
            gui.Circle(x=50.0, y=60.0, radius=8.0, layout_index=0, on_image=True),
        ]
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        self.app._image_probe_tip_pixel_now(layout_index=0)

        fake_detector.detect.assert_called_once()
        _args, kwargs = fake_detector.detect.call_args
        self.assertEqual(kwargs.get('roi'), (42, 52, 58, 68))

    def test_image_probe_tip_pixel_now_blocks_while_tracked_lock_is_held(self):
        # _image_layout_tracked's Circle objects are mutated in place by
        # _image_monitor_tick (main thread) every tick while this method
        # reads them from a background thread during AutoContactZ -- a
        # real, previously-unguarded data race (see _image_tracked_lock's
        # definition comment in gui.py) that a faulthandler crash dump
        # from the real Win7 instrument implicated. Proves the lock
        # actually synchronizes -- not just decorative -- by holding it
        # here and confirming a background reader blocks until released.
        self.app._image_layout_tracked = [
            gui.Circle(x=50.0, y=60.0, radius=8.0, layout_index=0, on_image=True),
        ]
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        result_holder = []
        self.app._image_tracked_lock.acquire()
        try:
            thread = threading.Thread(
                target=lambda: result_holder.append(self.app._image_probe_tip_pixel_now(layout_index=0))
            )
            thread.start()
            thread.join(timeout=0.3)
            self.assertTrue(thread.is_alive())
            self.assertEqual(result_holder, [])
        finally:
            self.app._image_tracked_lock.release()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result_holder), 1)

    def test_image_probe_tip_pixel_now_falls_back_to_generic_roi_when_not_tracked(self):
        self.app._image_probe_roi = (1, 2, 3, 4)
        self.app._image_layout_tracked = None
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        self.app._image_probe_tip_pixel_now(layout_index=0)

        fake_detector.detect.assert_called_once()
        _args, kwargs = fake_detector.detect.call_args
        self.assertEqual(kwargs.get('roi'), (1, 2, 3, 4))

    def _set_up_xy_bias_recalibration(self, tip_x, tip_y):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(0, 0, 0, 0),
            gui.PixelStageReference(1, 0, 1, 0),
            gui.PixelStageReference(0, 1, 0, 1),
        ])
        self.app._image_layout_tracked = [
            gui.Circle(x=50.0, y=60.0, radius=8.0, layout_index=0, on_image=True),
        ]
        self.app._image_electrode_z_store = gui.ElectrodeZCalibrationStore()
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_tip = SimpleNamespace(x=tip_x, y=tip_y, detected=True)
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = fake_tip
        self.app._image_probe_detector = fake_detector
        # _image_recalibrate_xy_bias_from_contact saves to
        # config.VISION_ELECTRODE_Z_SEED_PATH (the real, relative
        # vision_calibration/electrode_z_seed.json by default) -- must not
        # touch that real file from a test, same pattern as
        # test_image_clear_z_seed_store_clears_and_saves_when_confirmed.
        save_patcher = mock.patch.object(self.app._image_electrode_z_store, 'save')
        save_patcher.start()
        self.addCleanup(save_patcher.stop)

    def test_image_recalibrate_xy_bias_from_contact_compares_to_electrode_pixel_not_stage_position(self):
        # The correction is supposed to be a pure probe-vs-electrode pixel
        # offset (both observed in the same frame, same actual Z) -- not
        # observed-pixel-vs-calibration-predicted-from-stage-position,
        # which implicitly (and wrongly) assumes the electrode's contact Z
        # matches whatever Z the affine calibration was solved at.
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        # No motor connected at all -- must not be required now that the
        # comparison no longer needs a motor position readback.
        self.app.motor = None

        self.app._image_recalibrate_xy_bias_from_contact(0)

        samples = self.app._image_electrode_z_store.xy_bias_samples
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0].delta_x_px, 5.0, places=6)
        self.assertAlmostEqual(samples[0].delta_y_px, 5.0, places=6)

    def test_image_recalibrate_xy_bias_from_contact_backs_out_already_applied_correction(self):
        # Once a bias fit is active, the target this electrode was aimed at
        # was already shifted by it -- so the raw (tip - electrode) delta
        # measured here is a post-correction RESIDUAL, not the same
        # targeting-independent quantity the original (uncorrected)
        # samples measured. Recording that residual as-is would drag a
        # converged fit's own median back toward zero over time even
        # though the true underlying bias hasn't changed (see
        # conversation history). The stored sample must instead be the
        # raw delta with the already-applied correction backed back out:
        # raw(5, 5) - applied(3, -2) = (2, 7).
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._image_last_applied_correction[0] = {'xy_bias_px': (3.0, -2.0)}

        self.app._image_recalibrate_xy_bias_from_contact(0)

        samples = self.app._image_electrode_z_store.xy_bias_samples
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0].delta_x_px, 2.0, places=6)
        self.assertAlmostEqual(samples[0].delta_y_px, 7.0, places=6)

    def test_image_recalibrate_xy_bias_from_contact_rejects_implausibly_far_detection(self):
        # Electrode tracked at (50, 60) -> default max distance is a fixed
        # 20px, not scaled by the electrode's own radius. A "detection"
        # ~64px away (e.g. ProbeDetector locking onto a reflection, a
        # known limitation) must be rejected, not recorded as a sample.
        self._set_up_xy_bias_recalibration(tip_x=100.0, tip_y=100.0)
        self.app.motor = None

        status = self.app._image_recalibrate_xy_bias_from_contact(0)

        self.assertEqual(self.app._image_electrode_z_store.xy_bias_samples, [])
        self.assertIn('rejected', status)

    def test_image_recalibrate_xy_bias_from_contact_accepts_plausible_detection(self):
        # Same setup, but the detection is well within the fixed 20px
        # threshold (5px offset here) -- must NOT be rejected.
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None

        self.app._image_recalibrate_xy_bias_from_contact(0)

        self.assertEqual(len(self.app._image_electrode_z_store.xy_bias_samples), 1)

    def test_image_recalibrate_xy_bias_from_contact_rejection_disabled_when_threshold_none(self):
        self._set_up_xy_bias_recalibration(tip_x=100.0, tip_y=100.0)
        self.app.motor = None

        with mock.patch.object(gui._config, 'VISION_XY_BIAS_MAX_SAMPLE_PIXEL_DELTA_PX', None):
            self.app._image_recalibrate_xy_bias_from_contact(0)

        self.assertEqual(len(self.app._image_electrode_z_store.xy_bias_samples), 1)

    def test_image_recalibrate_xy_bias_from_contact_tags_sample_with_active_temperature_target(self):
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._active_temperature_target_c = 350.0

        self.app._image_recalibrate_xy_bias_from_contact(0)

        sample = self.app._image_electrode_z_store.xy_bias_samples[0]
        self.assertEqual(sample.temperature_c, 350.0)

    def test_image_recalibrate_xy_bias_from_contact_falls_back_to_last_safety_pv_without_active_target(self):
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = 340.0

        self.app._image_recalibrate_xy_bias_from_contact(0)

        sample = self.app._image_electrode_z_store.xy_bias_samples[0]
        self.assertEqual(sample.temperature_c, 340.0)

    def test_image_recalibrate_xy_bias_from_contact_tags_none_with_no_temperature_source(self):
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = None

        self.app._image_recalibrate_xy_bias_from_contact(0)

        sample = self.app._image_electrode_z_store.xy_bias_samples[0]
        self.assertIsNone(sample.temperature_c)

    def test_image_recalibrate_xy_bias_from_contact_falls_back_to_live_read_outside_a_run(self):
        # Manual "Search Z" used outside of an automated run -- neither
        # _active_temperature_target_c nor _temp_safety_last_pv_c is ever
        # populated (both are automated-run-only), so without a live read
        # fallback every manually-collected sample would be tagged
        # temperature_c=None regardless of the furnace's actual reading.
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = None
        self.app.tc = mock.Mock()
        self.app.tc.get_temperature.return_value = 275.0

        self.app._image_recalibrate_xy_bias_from_contact(0)

        sample = self.app._image_electrode_z_store.xy_bias_samples[0]
        self.assertEqual(sample.temperature_c, 275.0)

    def test_image_recalibrate_xy_bias_from_contact_no_live_read_when_tc_disconnected(self):
        self._set_up_xy_bias_recalibration(tip_x=55.0, tip_y=65.0)
        self.app.motor = None
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = None
        self.app.tc = None

        self.app._image_recalibrate_xy_bias_from_contact(0)

        sample = self.app._image_electrode_z_store.xy_bias_samples[0]
        self.assertIsNone(sample.temperature_c)

    def test_image_current_temperature_c_for_bias_no_live_read_by_default(self):
        # _image_project_pixel_to_stage_xy calls this with the default
        # (allow_live_read=False) from a per-camera-tick hot path
        # (_image_probe_occluded_layout_indices) -- must never touch the
        # temperature controller's serial port unless explicitly opted in.
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = None
        self.app.tc = mock.Mock()
        self.app.tc.get_temperature.return_value = 275.0

        result = self.app._image_current_temperature_c_for_bias()

        self.assertIsNone(result)
        self.app.tc.get_temperature.assert_not_called()

    def test_image_current_temperature_c_for_bias_live_read_failure_returns_none(self):
        self.app._active_temperature_target_c = None
        self.app._temp_safety_last_pv_c = None
        self.app.tc = mock.Mock()
        self.app.tc.get_temperature.side_effect = RuntimeError('serial timeout')

        result = self.app._image_current_temperature_c_for_bias(allow_live_read=True)

        self.assertIsNone(result)

    def test_image_current_temperature_c_for_bias_prefers_active_target_over_safety_pv(self):
        self.app._active_temperature_target_c = 400.0
        self.app._temp_safety_last_pv_c = 340.0
        self.assertEqual(self.app._image_current_temperature_c_for_bias(), 400.0)

    def test_image_probe_tip_pixel_now_stashes_roi_debug_on_detection(self):
        self.app._image_probe_roi = (10, 20, 90, 100)
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_tip = SimpleNamespace(x=30.0, y=45.0, detected=True)
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = fake_tip
        self.app._image_probe_detector = fake_detector

        result = self.app._image_probe_tip_pixel_now()

        self.assertEqual(result, (30.0, 45.0))
        debug = self.app._image_last_probe_roi_debug
        self.assertIsNotNone(debug)
        self.assertEqual(debug['crop_bgr'].shape[:2], (80, 80))  # (100-20, 90-10)
        self.assertEqual(debug['tip_local'], (20.0, 25.0))  # (30-10, 45-20)

    def test_image_probe_tip_pixel_now_stashes_roi_debug_on_failed_detection(self):
        self.app._image_probe_roi = (10, 20, 90, 100)
        self.app._image_current_frame_bgr = mock.Mock(return_value=np.zeros((120, 160, 3), dtype=np.uint8))
        fake_detector = mock.Mock()
        fake_detector.detect.return_value = None
        self.app._image_probe_detector = fake_detector

        result = self.app._image_probe_tip_pixel_now()

        self.assertIsNone(result)
        debug = self.app._image_last_probe_roi_debug
        self.assertIsNotNone(debug)
        self.assertIsNone(debug['tip_local'])

    def test_image_stash_probe_roi_debug_rejects_zero_width_crop(self):
        # An roi clamped entirely off-frame (or otherwise degenerate)
        # produces a zero-width/height crop -- must be treated as "no
        # crop" rather than stashed, since _make_panel's cell/w scale math
        # would divide by zero on it (this was the actual cause of "status
        # updates but no image" on a real rejected search: the exception
        # was being silently swallowed downstream).
        frame_bgr = np.zeros((120, 160, 3), dtype=np.uint8)
        self.app._image_stash_probe_roi_debug(frame_bgr, (200, 20, 250, 100), mock.Mock(detected=False))
        self.assertIsNone(self.app._image_last_probe_roi_debug)

    def test_image_probe_tip_pixel_now_clears_stale_debug_when_no_frame(self):
        self.app._image_last_probe_roi_debug = {'crop_bgr': np.zeros((5, 5, 3), dtype=np.uint8), 'tip_local': (1, 1)}
        self.app._image_current_frame_bgr = mock.Mock(return_value=None)

        result = self.app._image_probe_tip_pixel_now()

        self.assertIsNone(result)
        self.assertIsNone(self.app._image_last_probe_roi_debug)

    def test_image_update_parallax_debug_display_formats_accepted_sample(self):
        self.app._image_last_parallax_debug = {
            'start_roi': None, 'contact_roi': None,
            'start_pixel': (100.0, 100.0), 'contact_pixel': (90.0, 80.0),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': True, 'reject_reason': None,
        }
        self.app._image_update_parallax_debug_display()
        text = self.app._image_parallax_debug_var.get()
        self.assertIn('accepted', text)
        self.assertIn('-10.00', text)
        self.assertIn('-20.00', text)

    def test_image_update_parallax_debug_display_formats_rejected_sample(self):
        self.app._image_last_parallax_debug = {
            'start_roi': None, 'contact_roi': None,
            'start_pixel': (100.0, 100.0), 'contact_pixel': (99.5, 99.5),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': False, 'reject_reason': 'displacement 0.71px < 2.00px',
        }
        self.app._image_update_parallax_debug_display()
        text = self.app._image_parallax_debug_var.get()
        self.assertIn('rejected', text)
        self.assertIn('displacement 0.71px < 2.00px', text)

    def test_image_update_parallax_debug_display_handles_no_sample_yet(self):
        self.app._image_last_parallax_debug = None
        # Must not raise even with no sample captured yet (e.g. right
        # after Stop Camera resets the debug state).
        self.app._image_update_parallax_debug_display()

    def test_image_update_parallax_debug_display_renders_crosshair_panel(self):
        crop = np.zeros((40, 40, 3), dtype=np.uint8)
        self.app._image_last_parallax_debug = {
            'start_roi': {'crop_bgr': crop, 'tip_local': (10.0, 12.0)},
            'contact_roi': {'crop_bgr': crop, 'tip_local': None},
            'start_pixel': (100.0, 100.0), 'contact_pixel': (90.0, 80.0),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': True, 'reject_reason': None,
        }
        self.app._image_update_parallax_debug_display()
        self.assertIsNotNone(self.app._image_parallax_panel_photo)

    def test_image_update_parallax_debug_display_upscales_small_crop_to_fill_cell(self):
        # ProbeTuningWindow._make_panel alone only ever shrinks to fit a
        # cell (its own scale is capped at 1.0) -- a real ROI crop is
        # typically much smaller than the display cell, so without our own
        # upscaling step it would stay tiny instead of filling the space.
        crop = np.zeros((40, 40, 3), dtype=np.uint8)
        self.app._image_last_parallax_debug = {
            'start_roi': {'crop_bgr': crop, 'tip_local': (20.0, 20.0)},
            'contact_roi': {'crop_bgr': crop, 'tip_local': (20.0, 20.0)},
            'start_pixel': (100.0, 100.0), 'contact_pixel': (90.0, 80.0),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': True, 'reject_reason': None,
        }

        self.app._image_update_parallax_debug_display()

        photo = self.app._image_parallax_panel_photo
        self.assertIsNotNone(photo)
        cell = self.app._PARALLAX_PANEL_CELL_PX
        # Two 40x40 crops upscaled to fill a 150x150 cell each compose to
        # ~2*cell wide; without upscaling this would be ~2*40=80, well
        # under a single cell's width.
        self.assertGreater(photo.width(), cell)

    def test_image_update_parallax_debug_display_renders_on_rejected_sample_too(self):
        # The whole point of this panel is to show what the detector saw
        # even (especially) when the sample was rejected -- must not be
        # gated on debug['accepted'].
        crop = np.zeros((40, 40, 3), dtype=np.uint8)
        self.app._image_last_parallax_debug = {
            'start_roi': {'crop_bgr': crop, 'tip_local': (5.0, 5.0)},
            'contact_roi': {'crop_bgr': crop, 'tip_local': (6.0, 6.0)},
            'start_pixel': (100.0, 100.0), 'contact_pixel': (99.5, 99.5),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': False, 'reject_reason': 'displacement 0.71px < 2.00px',
        }
        self.app._image_update_parallax_debug_display()
        self.assertIsNotNone(self.app._image_parallax_panel_photo)

    def test_image_update_parallax_debug_display_surfaces_render_failure_instead_of_swallowing_it(self):
        # Regression coverage for the actual bug: a real panel-build
        # exception (e.g. a degenerate crop reaching _make_panel) must be
        # visible in the label, not silently discarded -- a diagnostic
        # panel that fails silently defeats its own purpose.
        crop = np.zeros((40, 40, 3), dtype=np.uint8)
        self.app._image_last_parallax_debug = {
            'start_roi': {'crop_bgr': crop, 'tip_local': None},
            'contact_roi': {'crop_bgr': crop, 'tip_local': None},
            'start_pixel': (100.0, 100.0), 'contact_pixel': (99.5, 99.5),
            'start_z': 9.8, 'contact_z': 9.83,
            'accepted': False, 'reject_reason': 'displacement 0.71px < 2.00px',
        }
        with mock.patch.object(
            gui.ProbeTuningWindow, '_make_panel', side_effect=ValueError('boom'),
        ):
            self.app._image_update_parallax_debug_display()
        self.assertIsNone(self.app._image_parallax_panel_photo)
        text = self._canvas_text(self.app._image_parallax_panel_canvas)
        self.assertIn('Panel render failed', text)
        self.assertIn('boom', text)

    def test_image_update_parallax_debug_display_shows_placeholder_when_no_sample_yet(self):
        # Same bordered-box-with-centered-text style as
        # self._image_dxf_layout_preview_canvas's "No layout loaded" --
        # not a two-cell panel of blank gray boxes.
        self.app._image_last_parallax_debug = None

        self.app._image_update_parallax_debug_display()

        self.assertIsNone(self.app._image_parallax_panel_photo)
        self.assertIn('No parallax sample yet', self._canvas_text(self.app._image_parallax_panel_canvas))

    def test_image_update_parallax_debug_display_placeholder_when_neither_roi_present(self):
        # debug dict exists (e.g. a rejected sample) but neither ROI crop
        # was ever captured -- still the single placeholder, not two blank
        # "no sample" cells.
        self.app._image_last_parallax_debug = {
            'start_roi': None, 'contact_roi': None,
            'start_pixel': None, 'contact_pixel': None,
            'start_z': 9.8, 'contact_z': None,
            'accepted': False, 'reject_reason': 'probe tip not detected at start and/or contact',
        }

        self.app._image_update_parallax_debug_display()

        self.assertIn('No parallax sample yet', self._canvas_text(self.app._image_parallax_panel_canvas))

    def _run_contact_z_search_with_pixel_samples(self, pixel_samples, ocv_sequence=(0.9, 0.05), **kwargs):
        # _manual_refresh_state (called both right after the initial move
        # to the search-start height, before the step loop, and again
        # after the post-contact engage move) reads OCV too, on top of the
        # step loop's own read -- three calls total for a single-step
        # search. Repeat the sequence's last value for any calls beyond
        # what's explicitly given, so only the values that matter
        # (confirming contact) need to be spelled out.
        self.app.motor = mock.Mock()
        self.app.bl = object()
        ocv_iter = iter(ocv_sequence)
        last_ocv = [ocv_sequence[-1]]

        def _next_ocv():
            try:
                last_ocv[0] = next(ocv_iter)
            except StopIteration:
                pass
            return last_ocv[0]

        self.app._manual_update_ocv = mock.Mock(side_effect=_next_ocv)
        self.app._confirm_contact_candidate = mock.Mock(return_value=True)
        sample_fn = mock.Mock(side_effect=pixel_samples)
        return self.app._execute_contact_z_search(
            base_z=10.0, start_offset=0.2, step_mm=0.05, max_beyond_seed_mm=0.2,
            ocv_threshold=0.2, settle_s=0.0, engage_mm=0.01,
            probe_pixel_sample_fn=sample_fn,
            **kwargs,
        )

    def test_execute_contact_z_search_rejects_too_small_parallax_sample(self):
        # Pinned rather than relying on the live config.py value -- this
        # threshold is meant to be user-tunable per-instrument (it's
        # explicitly UNMEASURED/provisional), so the test fixes it to a
        # known value instead of depending on whatever it's currently set
        # to on this machine.
        with mock.patch.object(gui._config, 'VISION_PARALLAX_MIN_SAMPLE_PIXEL_DELTA_PX', 2.0):
            found_contact, measure_z, parallax_sample = self._run_contact_z_search_with_pixel_samples(
                [(100.0, 100.0), (99.5, 99.5)]
            )
        self.assertIsNone(parallax_sample)
        debug = self.app._image_last_parallax_debug
        self.assertFalse(debug['accepted'])
        self.assertIn('displacement', debug['reject_reason'])

    def test_execute_contact_z_search_accepts_sample_regardless_of_direction(self):
        # No hardcoded expected sign: which way the tracked pixel shifts
        # with Z depends on camera orientation (e.g. flips under a
        # 180-degree camera rotation), so a sufficiently large displacement
        # in ANY direction must be accepted -- only magnitude is gated here.
        found_contact, measure_z, parallax_sample = self._run_contact_z_search_with_pixel_samples(
            [(100.0, 100.0), (110.0, 90.0)]
        )
        self.assertIsNotNone(parallax_sample)
        debug = self.app._image_last_parallax_debug
        self.assertTrue(debug['accepted'])
        self.assertIsNone(debug['reject_reason'])

    def test_execute_contact_z_search_accepts_plausible_parallax_sample(self):
        found_contact, measure_z, parallax_sample = self._run_contact_z_search_with_pixel_samples(
            [(100.0, 100.0), (90.0, 80.0)]
        )
        self.assertIsNotNone(parallax_sample)
        debug = self.app._image_last_parallax_debug
        self.assertTrue(debug['accepted'])
        self.assertIsNone(debug['reject_reason'])
        self.assertEqual(debug['start_pixel'], (100.0, 100.0))
        self.assertEqual(debug['contact_pixel'], (90.0, 80.0))

    def test_execute_contact_z_search_updates_manual_target_z_by_default(self):
        # Matches Manual Control's "Find Contact Z", which never passes
        # track_manual_target_z -- the field must keep reflecting the
        # search's own start/measurement heights as before.
        self.app._manual_target['z'].set('-1.0')
        self._run_contact_z_search_with_pixel_samples([(100.0, 100.0), (90.0, 80.0)])
        self.assertNotEqual(self.app._manual_target['z'].get(), '-1.0')

    def test_execute_contact_z_search_leaves_manual_target_z_untouched_when_tracking_disabled(self):
        # The Image Monitor tab's "Search Z" passes
        # track_manual_target_z=False when its "Lock Z" checkbox is on --
        # confirms the field genuinely never changes throughout the whole
        # search (start-height move included), not just at the end.
        self.app._manual_target['z'].set('-1.0')
        self._run_contact_z_search_with_pixel_samples(
            [(100.0, 100.0), (90.0, 80.0)], track_manual_target_z=False,
        )
        self.assertEqual(self.app._manual_target['z'].get(), '-1.0')

    def test_image_seed_z_from_contact_disables_manual_target_z_tracking_when_locked(self):
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 3.0
        self.app._execute_contact_z_search = mock.Mock(return_value=(3.0, 3.0, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)
        self.app._image_lock_z_var.set(True)

        self.app._image_seed_z_from_contact()

        kwargs = self.app._execute_contact_z_search.call_args.kwargs
        self.assertFalse(kwargs['track_manual_target_z'])

    def test_image_seed_z_from_contact_enables_manual_target_z_tracking_when_unlocked(self):
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 3.0
        self.app._execute_contact_z_search = mock.Mock(return_value=(3.0, 3.0, None))
        self.app._image_record_electrode_z_contact_for_target = mock.Mock(return_value=None)
        self.app._image_lock_z_var.set(False)

        self.app._image_seed_z_from_contact()

        kwargs = self.app._execute_contact_z_search.call_args.kwargs
        self.assertTrue(kwargs['track_manual_target_z'])

    def test_execute_contact_z_search_captures_start_sample_when_contact_not_confirmed(self):
        self.app.motor = mock.Mock()
        self.app.bl = object()
        self.app._manual_update_ocv = mock.Mock(return_value=0.9)  # never confirms
        sample_fn = mock.Mock(return_value=(100.0, 100.0))
        with self.assertRaises(gui.ContactNotConfirmedError):
            self.app._execute_contact_z_search(
                base_z=10.0, start_offset=0.2, step_mm=0.1, max_beyond_seed_mm=0.1,
                ocv_threshold=0.2, settle_s=0.0, engage_mm=0.01,
                probe_pixel_sample_fn=sample_fn,
            )
        debug = self.app._image_last_parallax_debug
        self.assertIsNotNone(debug)
        self.assertFalse(debug['accepted'])
        self.assertEqual(debug['reject_reason'], 'contact not confirmed')
        self.assertIsNone(debug['contact_pixel'])
        self.assertEqual(debug['start_pixel'], (100.0, 100.0))

    def test_execute_contact_z_search_schedules_parallax_display_via_after_not_direct_call(self):
        # _execute_contact_z_search runs on a background worker thread (via
        # _run_manual_action) when triggered from Search Z -- calling
        # _image_update_parallax_debug_display directly from there means
        # its ImageTk.PhotoImage creation happens off the main Tkinter
        # thread and silently fails to render (the plain StringVar status
        # text still updates fine, which is why only the image looked
        # broken). Must go through self.after instead, same as every other
        # cross-thread UI update in this file.
        self.app._image_update_parallax_debug_display = mock.Mock()
        scheduled = []
        orig_after = self.app.after
        self.app.after = lambda delay, fn: scheduled.append(fn)
        try:
            self._run_contact_z_search_with_pixel_samples([(100.0, 100.0), (90.0, 80.0)])
        finally:
            self.app.after = orig_after

        self.app._image_update_parallax_debug_display.assert_not_called()
        self.assertIn(self.app._image_update_parallax_debug_display, scheduled)

    def test_execute_contact_z_search_schedules_parallax_display_via_after_on_contact_not_confirmed(self):
        self.app._image_update_parallax_debug_display = mock.Mock()
        scheduled = []
        orig_after = self.app.after
        self.app.after = lambda delay, fn: scheduled.append(fn)
        self.app.motor = mock.Mock()
        self.app.bl = object()
        self.app._manual_update_ocv = mock.Mock(return_value=0.9)  # never confirms
        sample_fn = mock.Mock(return_value=(100.0, 100.0))
        try:
            with self.assertRaises(gui.ContactNotConfirmedError):
                self.app._execute_contact_z_search(
                    base_z=10.0, start_offset=0.2, step_mm=0.1, max_beyond_seed_mm=0.1,
                    ocv_threshold=0.2, settle_s=0.0, engage_mm=0.01,
                    probe_pixel_sample_fn=sample_fn,
                )
        finally:
            self.app.after = orig_after

        self.app._image_update_parallax_debug_display.assert_not_called()
        self.assertIn(self.app._image_update_parallax_debug_display, scheduled)

    def test_image_monitor_stop_resets_parallax_debug_state(self):
        self.app._image_last_probe_roi_debug = {'crop_bgr': np.zeros((5, 5, 3), dtype=np.uint8), 'tip_local': None}
        self.app._image_last_parallax_debug = {'accepted': True}
        self.app._image_parallax_debug_var.set('stale text')

        self.app._image_monitor_stop()

        self.assertIsNone(self.app._image_last_probe_roi_debug)
        self.assertIsNone(self.app._image_last_parallax_debug)
        self.assertEqual(self.app._image_parallax_debug_var.get(), 'Last parallax sample: none yet')

    def test_image_toggle_probe_roi_select_mode_toggles_and_reports_status(self):
        self.assertFalse(self.app._image_probe_roi_select_mode)
        self.app._image_toggle_probe_roi_select_mode()
        self.assertTrue(self.app._image_probe_roi_select_mode)
        self.assertIn('drag a rectangle', self.app._image_probe_status_var.get())
        self.app._image_toggle_probe_roi_select_mode()
        self.assertFalse(self.app._image_probe_roi_select_mode)

    def test_image_clear_probe_roi_resets_state(self):
        self.app._image_probe_roi = (1, 2, 3, 4)
        self.app._image_probe_roi_select_mode = True
        self.app._image_probe_roi_drag_start = (1, 1)
        self.app._image_probe_roi_drag_current = (2, 2)
        self.app._image_probe_tip_candidate = (5.0, 6.0)

        self.app._image_clear_probe_roi()

        self.assertIsNone(self.app._image_probe_roi)
        self.assertFalse(self.app._image_probe_roi_select_mode)
        self.assertIsNone(self.app._image_probe_roi_drag_start)
        self.assertIsNone(self.app._image_probe_roi_drag_current)
        # Otherwise a stale crosshair from a previous "Detect Probe Tip"
        # keeps being redrawn every frame after the ROI is cleared.
        self.assertIsNone(self.app._image_probe_tip_candidate)

    def _tracked_circles_at_stage_x(self, xs):
        # Identity pixel<->stage calibration, so a Circle's pixel x IS its
        # stage x -- makes the exclusion math trivial to reason about.
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        return [
            gui.Circle(x=float(x), y=0.0, radius=8.0, layout_index=i, on_image=True)
            for i, x in enumerate(xs)
        ]

    def test_image_probe_occluded_layout_indices_excludes_electrodes_left_of_probe(self):
        tracked = self._tracked_circles_at_stage_x([0.0, 10.0, 20.0])
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 15.0
        # Default margin 2.0mm -> threshold 13.0: electrodes 0 (x=0) and
        # 1 (x=10) fall below it and are excluded; electrode 2 (x=20) is not.
        excluded = self.app._image_probe_occluded_layout_indices(tracked)
        self.assertEqual(excluded, {0, 1})

    def test_image_probe_occluded_layout_indices_none_without_motor(self):
        tracked = self._tracked_circles_at_stage_x([0.0, 10.0])
        self.app.motor = None
        self.assertIsNone(self.app._image_probe_occluded_layout_indices(tracked))

    def test_image_probe_occluded_layout_indices_none_on_motor_failure(self):
        tracked = self._tracked_circles_at_stage_x([0.0, 10.0])
        self.app.motor = mock.Mock()
        self.app.motor.get_position.side_effect = RuntimeError('disconnected')
        self.assertIsNone(self.app._image_probe_occluded_layout_indices(tracked))

    def test_image_monitor_press_drag_release_sets_probe_roi(self):
        self._set_up_rendered_frame()
        self.app._image_probe_roi_select_mode = True

        self.app._image_monitor_press(SimpleNamespace(x=20, y=30))
        self.app._image_monitor_drag(SimpleNamespace(x=60, y=80))
        self.app._image_monitor_release(SimpleNamespace(x=60, y=80))

        self.assertEqual(self.app._image_probe_roi, (20, 30, 60, 80))
        self.assertIsNone(self.app._image_probe_roi_drag_start)

    def test_image_monitor_press_delegates_to_click_select_when_not_in_roi_mode(self):
        self._set_up_rendered_frame()
        self.app._image_probe_roi_select_mode = False
        self.app._image_monitor_click_select_target = mock.Mock()

        event = SimpleNamespace(x=20, y=30)
        self.app._image_monitor_press(event)

        self.app._image_monitor_click_select_target.assert_called_once_with(event)

    def test_image_load_circle_params_file_sets_state(self):
        payload = {
            'preprocessing': {'clahe_clip': 3.0, 'clahe_tile': 6, 'bilateral_d': 7, 'bilateral_sigma': 60.0},
            'hough': {'param1': 45.0, 'param2_roi': 18.0, 'min_radius': 12.0, 'max_radius': 28.0},
        }
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=path):
                self.app._image_load_circle_params_file()
        finally:
            os.unlink(path)

        self.assertIsNotNone(self.app._image_circle_params)
        self.assertAlmostEqual(self.app._image_circle_params['clahe_clip'], 3.0)
        self.assertAlmostEqual(self.app._image_circle_params['expected_radius'], 20.0)

    def test_resolve_live_detector_params_returns_real_ints_for_radius(self):
        # Regression test: expected_radius/radius_tolerance are always
        # floats (vision/circle_detector.py::load_hough_params), and
        # cv2.HoughCircles requires minRadius/maxRadius to be actual
        # Python int -- passing a float (even a whole number like 30.0)
        # raises "Argument 'minRadius' is required to be an integer".
        self.app._image_circle_params = {
            'clahe_clip': 2.0, 'clahe_tile': 8, 'bilateral_d': 9, 'bilateral_sigma': 75.0,
            'param1': 50.0, 'param2': 20.0,
            'expected_radius': 20.0, 'radius_tolerance': 10.0,
        }

        params = self.app._image_resolve_live_detector_params()

        self.assertIsInstance(params['min_radius_px'], int)
        self.assertIsInstance(params['max_radius_px'], int)
        self.assertEqual(params['min_radius_px'], 10)
        self.assertEqual(params['max_radius_px'], 30)

    # ══════════════════════════════════════════════════════════════════
    # Round 2: configurable target XY tolerance for the Search Z gate
    # ══════════════════════════════════════════════════════════════════

    def test_target_xy_tolerance_field_defaults_to_module_constant(self):
        self.assertAlmostEqual(
            float(self.app._contact_search['target_xy_tolerance_mm'].get()),
            gui.IMAGE_TARGET_MOVE_MATCH_TOL_MM,
        )

    def test_manual_move_stage_arms_target_with_widened_xy_tolerance(self):
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 0.0, "Y": 0.0, "Z": 3.0}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        # Nudge the synced fields by 0.05 mm -- outside the default 0.01 mm
        # tolerance, but within a widened one (needed when the tip is far
        # from the electrode plane and can't land pixel-perfect).
        x = float(self.app._manual_target['x'].get()) + 0.05
        y = float(self.app._manual_target['y'].get()) + 0.05
        self.app._manual_target['x'].set(f'{x:.3f}')
        self.app._manual_target['y'].set(f'{y:.3f}')
        self.app._contact_search['target_xy_tolerance_mm'].set('0.1')

        self.app._manual_move_stage()

        # Matched within the widened tolerance -- the lock was not
        # auto-cleared.
        self.assertIsNotNone(self.app._image_selected_target)

    def test_manual_move_stage_rejects_same_mismatch_with_default_tolerance(self):
        motor = mock.Mock()
        motor.get_position.side_effect = lambda axis: {"X": 0.0, "Y": 0.0, "Z": 3.0}[axis]
        self.app.motor = motor
        self.app._manual_refresh_state = mock.Mock()
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        x = float(self.app._manual_target['x'].get()) + 0.05
        y = float(self.app._manual_target['y'].get()) + 0.05
        self.app._manual_target['x'].set(f'{x:.3f}')
        self.app._manual_target['y'].set(f'{y:.3f}')
        # target_xy_tolerance_mm left at its default (0.01 mm).

        self.app._manual_move_stage()

        self.assertIsNone(self.app._image_selected_target)

    # ══════════════════════════════════════════════════════════════════
    # Round 2: show/hide electrode circles toggle
    # ══════════════════════════════════════════════════════════════════

    def test_show_electrode_circles_checkbutton_exists_and_defaults_true(self):
        self.assertTrue(self.app._image_show_circles_var.get())
        checkbox = self._find_checkbutton_by_text(self.app.tab_image, 'Show Electrode Circles')
        self.assertIsNotNone(checkbox)

    def test_render_image_monitor_frame_skips_layout_overlay_when_circles_hidden(self):
        # "Show Electrode Circles" off hides every circle overlay, including
        # the drawn/projected alignment overlay (user-drawn click circles +
        # live fit-transform preview), not just the Hough-detected circles.
        self.app._image_show_circles_var.set(False)
        self.app._image_draw_layout_alignment_overlay = mock.Mock(side_effect=lambda f: f)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        self.app._render_image_monitor_frame(frame)

        self.app._image_draw_layout_alignment_overlay.assert_not_called()

    def test_render_image_monitor_frame_calls_layout_overlay_when_circles_shown(self):
        self.app._image_show_circles_var.set(True)
        self.app._image_draw_layout_alignment_overlay = mock.Mock(side_effect=lambda f: f)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        self.app._render_image_monitor_frame(frame)

        self.app._image_draw_layout_alignment_overlay.assert_called_once()

    def test_tracked_circles_drawn_after_alignment_overlay_so_colors_stay_visible(self):
        # Detected/extrapolated circles (annotate_detections) must be
        # drawn last -- on top of the alignment overlay's amber projected
        # circles -- so the detected (orange/green) vs. extrapolated
        # (gray) color-coding stays visible instead of being covered by
        # the alignment overlay drawn earlier in the pipeline. The
        # annotate_detections call sits after the tab-visibility guard in
        # _render_image_monitor_frame (skipped when not the active tab, to
        # avoid wasted work), so winfo_ismapped must be forced True here --
        # the withdrawn test window otherwise reports unmapped.
        self.app._image_show_circles_var.set(True)
        self.app._image_tracking_map = pd.DataFrame([{
            'x_px': 5.0, 'y_px': 5.0, 'radius_px': 2.0,
            'row_index': 0, 'col_index': 0, 'size_group': 'large', 'source': 'seed',
        }])
        call_order = []
        self.app._image_draw_layout_alignment_overlay = mock.Mock(
            side_effect=lambda f: call_order.append('alignment') or f
        )
        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=True), \
             mock.patch.object(
                 gui, 'annotate_detections',
                 side_effect=lambda f, df, **kw: call_order.append('detections') or f,
             ) as mocked_annotate:
            frame = np.zeros((10, 10, 3), dtype=np.uint8)
            self.app._render_image_monitor_frame(frame)

        mocked_annotate.assert_called_once()
        self.assertEqual(call_order, ['alignment', 'detections'])

    def test_tracked_circles_to_table_marks_occluded_electrodes(self):
        seed_meta = pd.DataFrame([
            {'row_index': 0, 'col_index': 1, 'size_group': 'large', 'source': 'dxf_layout'},
            {'row_index': 0, 'col_index': 2, 'size_group': 'large', 'source': 'dxf_layout'},
        ])
        tracked = [
            gui.Circle(x=1.0, y=1.0, radius=8.0, layout_index=0, on_image=True),
            gui.Circle(x=2.0, y=2.0, radius=8.0, layout_index=1, on_image=True),
        ]

        df = self.app._image_tracked_circles_to_table(tracked, seed_meta, occluded_layout_indices={0})

        self.assertTrue(bool(df.iloc[0]['occluded']))
        self.assertFalse(bool(df.iloc[1]['occluded']))

    def test_tracked_circles_to_table_defaults_occluded_false_without_arg(self):
        seed_meta = pd.DataFrame([{'row_index': 0, 'col_index': 1, 'size_group': 'large', 'source': 'dxf_layout'}])
        tracked = [gui.Circle(x=1.0, y=1.0, radius=8.0, layout_index=0, on_image=True)]

        df = self.app._image_tracked_circles_to_table(tracked, seed_meta)

        self.assertFalse(bool(df.iloc[0]['occluded']))

    def test_tracked_circles_not_drawn_when_circles_toggle_off(self):
        self.app._image_show_circles_var.set(False)
        self.app._image_tracking_map = pd.DataFrame([{
            'x_px': 5.0, 'y_px': 5.0, 'radius_px': 2.0,
            'row_index': 0, 'col_index': 0, 'size_group': 'large', 'source': 'seed',
        }])
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=True), \
             mock.patch.object(gui, 'annotate_detections') as mocked_annotate:
            self.app._render_image_monitor_frame(frame)

        mocked_annotate.assert_not_called()

    def test_tracked_circles_not_drawn_when_tracking_map_empty(self):
        self.app._image_show_circles_var.set(True)
        self.app._image_tracking_map = pd.DataFrame([])
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=True), \
             mock.patch.object(gui, 'annotate_detections') as mocked_annotate:
            self.app._render_image_monitor_frame(frame)

        mocked_annotate.assert_not_called()

    def test_render_image_monitor_frame_skips_label_update_when_tab_unmapped(self):
        # Regression test for stray camera-frame artifacts bleeding onto
        # other tabs (e.g. Manual Control): while the Image Monitor tab
        # isn't the currently-selected/mapped Notebook tab, the Label
        # bitmap must not be reconfigured, even though detection/overlay
        # drawing above still runs.
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=False):
            self.app._image_monitor_photo = None
            self.app._render_image_monitor_frame(frame)
        self.assertIsNone(self.app._image_monitor_photo)

    def test_render_image_monitor_frame_updates_label_when_tab_mapped(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=True):
            self.app._image_monitor_photo = None
            self.app._render_image_monitor_frame(frame)
        self.assertIsNotNone(self.app._image_monitor_photo)

    def test_render_image_monitor_frame_respects_actual_label_size_below_default(self):
        # Regression test: max(actual, 640/480) used to force the render to
        # at least 640x480 even when the label's real laid-out size (e.g.
        # a non-maximized window) was smaller than that, so the rendered
        # image ended up bigger than what was actually visible -- which is
        # exactly what made the Draw ROI cursor mapping drift unless the
        # window was maximized/fullscreen (where the label naturally
        # exceeds 640x480 and the mismatch disappears). The fix only uses
        # the 640/480 default before the label has been laid out at all
        # (winfo_width/height ~1); once genuinely laid out smaller than
        # that, the real (smaller) size must be respected.
        frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
        with mock.patch.object(type(self.app.tab_image), 'winfo_ismapped', return_value=True), \
             mock.patch.object(type(self.app._image_monitor_label), 'winfo_width', return_value=300), \
             mock.patch.object(type(self.app._image_monitor_label), 'winfo_height', return_value=200):
            self.app._render_image_monitor_frame(frame)
        self.assertLessEqual(self.app._image_render_size[0], 300)
        self.assertLessEqual(self.app._image_render_size[1], 200)

    def test_axis_arrows_overlay_draws_nothing_without_calibration(self):
        self.app._image_pixel_stage_affine_calibration = None
        frame = np.zeros((400, 400, 3), dtype=np.uint8)

        overlay = self.app._image_draw_axis_arrows_overlay(frame)

        self.assertIs(overlay, frame)

    def test_axis_arrows_overlay_draws_red_x_and_green_y_arrows(self):
        self.app._image_pixel_stage_affine_calibration = gui.solve_stage_affine_calibration([
            gui.PixelStageReference(pixel_x=0, pixel_y=0, stage_x_mm=0, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=1, pixel_y=0, stage_x_mm=1, stage_y_mm=0),
            gui.PixelStageReference(pixel_x=0, pixel_y=1, stage_x_mm=0, stage_y_mm=1),
        ])
        frame = np.zeros((400, 400, 3), dtype=np.uint8)

        overlay = self.app._image_draw_axis_arrows_overlay(frame)

        self.assertFalse(np.array_equal(overlay, frame))
        self.assertTrue(np.any(np.all(overlay == [0, 0, 255], axis=-1)))  # +X, red (BGR)
        self.assertTrue(np.any(np.all(overlay == [0, 220, 0], axis=-1)))  # +Y, green (BGR)

    def test_probe_tip_overlay_draws_red_marker(self):
        self.app._image_probe_tip_candidate = (5.0, 5.0)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        overlay = self.app._image_draw_probe_tip_overlay(frame)

        self.assertTrue(np.any(np.all(overlay == [0, 0, 255], axis=-1)))
        self.assertFalse(np.any(np.all(overlay == [0, 255, 180], axis=-1)))

    # ══════════════════════════════════════════════════════════════════
    # Round 2: DXF layout schematic preview plot
    # ══════════════════════════════════════════════════════════════════

    def test_dxf_layout_preview_shows_placeholder_without_layout(self):
        self.app._image_layout_model = None

        self.app._image_draw_dxf_layout_preview()

        items = self.app._image_dxf_layout_preview_canvas.find_all()
        self.assertEqual(len(items), 1)

    def test_dxf_layout_preview_draws_circle_per_electrode(self):
        template = np.array([[0, 0], [10, 0], [0, 10]], dtype=np.float32)
        radii = np.array([2.0, 2.0, 2.0], dtype=np.float32)
        self.app._image_layout_model = gui.LayoutModel(template, radii)

        self.app._image_draw_dxf_layout_preview()

        canvas = self.app._image_dxf_layout_preview_canvas
        ovals = [item for item in canvas.find_all() if canvas.type(item) == 'oval']
        self.assertEqual(len(ovals), 3)

    def test_dxf_layout_preview_does_not_re_flip_y(self):
        # extract_circles_from_dxf() already negates Y at load time (DXF is
        # Y-up, image/pixel space is Y-down), so layout.template's Y is
        # already in image convention -- a larger template Y must render
        # further DOWN the canvas (larger canvas Y), not flipped back up.
        template = np.array([[0.0, 0.0], [0.0, 100.0]], dtype=np.float32)
        radii = np.array([2.0, 2.0], dtype=np.float32)
        self.app._image_layout_model = gui.LayoutModel(template, radii)

        self.app._image_draw_dxf_layout_preview()

        canvas = self.app._image_dxf_layout_preview_canvas
        ovals = [item for item in canvas.find_all() if canvas.type(item) == 'oval']
        self.assertEqual(len(ovals), 2)
        centers_y = []
        for item in ovals:
            y0, y1 = canvas.coords(item)[1], canvas.coords(item)[3]
            centers_y.append((y0 + y1) / 2.0)
        # ovals are created in template order: index 0 (y=0), index 1 (y=100).
        self.assertGreater(centers_y[1], centers_y[0])

    def test_dxf_layout_preview_labels_every_circle_even_a_tiny_one(self):
        # Many electrodes spread over a large layout scale down to a tiny
        # on-screen radius -- the number must still be drawn (just outside
        # the circle rather than squeezed inside it), not skipped.
        template = np.array([[0, 0], [1000, 0], [0, 1000]], dtype=np.float32)
        radii = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        self.app._image_layout_model = gui.LayoutModel(template, radii)

        self.app._image_draw_dxf_layout_preview()

        canvas = self.app._image_dxf_layout_preview_canvas
        texts = [canvas.itemcget(item, 'text') for item in canvas.find_all() if canvas.type(item) == 'text']
        self.assertEqual(sorted(texts), ['1', '2', '3'])

    # ══════════════════════════════════════════════════════════════════
    # Round 2: Fit Transform preview uses real per-electrode radius
    # ══════════════════════════════════════════════════════════════════

    def test_layout_alignment_overlay_uses_project_all_circles_for_per_electrode_radius(self):
        layout = mock.Mock()
        layout.transform = np.eye(2, 3, dtype=np.float32)
        self.app._image_layout_model = layout
        self.app._image_layout_drawn_circles = []
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        fake_circles = [
            {'center': (30.0, 30.0), 'radius': 4},
            {'center': (70.0, 70.0), 'radius': 12},
        ]

        with mock.patch.object(gui, 'project_all_circles', return_value=fake_circles) as mocked:
            out = self.app._image_draw_layout_alignment_overlay(frame)

        mocked.assert_called_once_with(layout)
        # Rings should appear at each electrode's OWN radius (4 and 12),
        # not at the old fixed 8px every electrode used to share.
        self.assertTrue(np.any(out[30, 34] != 0), "expected a ring at radius 4 around (30, 30)")
        self.assertFalse(np.any(out[30, 38] != 0), "old fixed radius-8 ring should be gone")
        self.assertTrue(np.any(out[70, 82] != 0), "expected a ring at radius 12 around (70, 70)")
        self.assertFalse(np.any(out[70, 78] != 0), "old fixed radius-8 ring should be gone")

    # ══════════════════════════════════════════════════════════════════
    # Round 2: X/Y shear nudge controls
    # ══════════════════════════════════════════════════════════════════

    def test_shear_nudge_vars_forwarded_to_apply_manual_nudge(self):
        self.app._image_layout_base_transform = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
        )
        self.app._image_layout_model = mock.Mock()
        self.app._image_render_shape = None
        self.app._image_layout_nudge_shear_x_var.set(40)
        self.app._image_layout_nudge_shear_y_var.set(-20)

        with mock.patch.object(
            gui, 'apply_manual_nudge', return_value=np.eye(2, 3, dtype=np.float32)
        ) as mocked:
            self.app._image_apply_layout_nudge()

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['shear_x_permille'], 40)
        self.assertEqual(kwargs['shear_y_permille'], -20)

    def test_reset_layout_alignment_state_resets_shear_nudge_vars(self):
        self.app._image_layout_nudge_shear_x_var.set(40)
        self.app._image_layout_nudge_shear_y_var.set(-20)

        self.app._image_reset_layout_alignment_state()

        self.assertEqual(self.app._image_layout_nudge_shear_x_var.get(), 0)
        self.assertEqual(self.app._image_layout_nudge_shear_y_var.get(), 0)

    def test_fit_layout_alignment_resets_shear_nudge_vars(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        known_transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        layout.transform = known_transform
        true_pixels = layout.project_all()
        layout.transform = None
        self.app._image_layout_drawn_circles = [
            {"center": [float(x), float(y)], "radius": gui._IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX}
            for x, y in true_pixels
        ]
        self.app._image_layout_alignment_pairs = [(0, 0), (1, 1), (2, 2), (3, 3)]
        self.app._image_monitor_last_frame_rgb = None
        self.app._image_layout_nudge_shear_x_var.set(40)
        self.app._image_layout_nudge_shear_y_var.set(-20)

        self.app._image_fit_layout_alignment()

        self.assertEqual(self.app._image_layout_nudge_shear_x_var.get(), 0)
        self.assertEqual(self.app._image_layout_nudge_shear_y_var.get(), 0)

    # ══════════════════════════════════════════════════════════════════
    # Round 5: independent X/Y scale nudge controls
    # ══════════════════════════════════════════════════════════════════

    def test_scale_nudge_vars_forwarded_independently_to_apply_manual_nudge(self):
        self.app._image_layout_base_transform = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
        )
        self.app._image_layout_model = mock.Mock()
        self.app._image_render_shape = None
        self.app._image_layout_nudge_scale_x_var.set(30)
        self.app._image_layout_nudge_scale_y_var.set(-15)

        with mock.patch.object(
            gui, 'apply_manual_nudge', return_value=np.eye(2, 3, dtype=np.float32)
        ) as mocked:
            self.app._image_apply_layout_nudge()

        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs['scale_x_permille'], 30)
        self.assertEqual(kwargs['scale_y_permille'], -15)

    def test_reset_layout_alignment_state_resets_scale_nudge_vars(self):
        self.app._image_layout_nudge_scale_x_var.set(30)
        self.app._image_layout_nudge_scale_y_var.set(-15)

        self.app._image_reset_layout_alignment_state()

        self.assertEqual(self.app._image_layout_nudge_scale_x_var.get(), 0)
        self.assertEqual(self.app._image_layout_nudge_scale_y_var.get(), 0)

    def test_fit_layout_alignment_resets_scale_nudge_vars(self):
        layout = self._make_square_layout()
        self.app._image_layout_model = layout
        known_transform = np.array([[2.0, 0.0, 50.0], [0.0, 2.0, 50.0]], dtype=np.float32)
        layout.transform = known_transform
        true_pixels = layout.project_all()
        layout.transform = None
        self.app._image_layout_drawn_circles = [
            {"center": [float(x), float(y)], "radius": gui._IMAGE_LAYOUT_DRAWN_MARKER_RADIUS_PX}
            for x, y in true_pixels
        ]
        self.app._image_layout_alignment_pairs = [(0, 0), (1, 1), (2, 2), (3, 3)]
        self.app._image_monitor_last_frame_rgb = None
        self.app._image_layout_nudge_scale_x_var.set(30)
        self.app._image_layout_nudge_scale_y_var.set(-15)

        self.app._image_fit_layout_alignment()

        self.assertEqual(self.app._image_layout_nudge_scale_x_var.get(), 0)
        self.assertEqual(self.app._image_layout_nudge_scale_y_var.get(), 0)

    # ══════════════════════════════════════════════════════════════════
    # Round 2: live "Searching."/OCV status during Z-contact search
    # ══════════════════════════════════════════════════════════════════

    def test_image_seed_z_from_contact_forwards_ocv_status_var_and_sets_searching(self):
        self._set_up_locked_target()
        self.assertTrue(self.app._image_select_target_from_frame_xy(50.0, 50.0))
        self.app._image_target_within_move_tolerance = mock.Mock(return_value=True)
        self.app.motor = mock.Mock()
        self.app.motor.get_position.return_value = 3.0
        captured = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            captured['status_at_search_start'] = self.app._image_z_seed_status_var.get()
            raise RuntimeError('stop before a real search runs')

        self.app._execute_contact_z_search = mock.Mock(side_effect=fake_search)

        self.app._image_seed_z_from_contact()

        self.assertIs(captured.get('ocv_status_var'), self.app._image_z_seed_ocv_var)
        self.assertIn('searching', captured.get('status_at_search_start', ''))

    # ══════════════════════════════════════════════════════════════════
    # Round 4: 1-based indexing for the layout dropdown and drawn circles
    # ══════════════════════════════════════════════════════════════════

    def test_layout_pair_combo_values_are_one_based(self):
        self.app._image_layout_model = gui.LayoutModel(
            np.array([[0, 0], [10, 0]], dtype=np.float32)
        )

        self.app._image_populate_layout_pair_combo()

        values = list(self.app._image_layout_pair_combo.cget('values'))
        self.assertEqual(values[0].split(' ', 1)[0], '1')
        self.assertEqual(values[1].split(' ', 1)[0], '2')

    def test_confirm_layout_pair_converts_one_based_input_to_zero_based_pair(self):
        self.app._image_layout_model = gui.LayoutModel(
            np.array([[0, 0], [10, 0]], dtype=np.float32)
        )
        self.app._image_layout_drawn_circles = [
            {'center': (5.0, 5.0), 'radius': 8.0},
            {'center': (15.0, 15.0), 'radius': 8.0},
        ]
        self.app._image_populate_layout_pair_combo()
        self.app._image_layout_pair_drawn_var.set('2')  # second drawn circle
        combo_values = self.app._image_layout_pair_combo.cget('values')
        self.app._image_layout_pair_combo.set(combo_values[0])  # "1 (0.0, 0.0)"

        self.app._image_confirm_layout_pair()

        self.assertEqual(self.app._image_layout_alignment_pairs, [(1, 0)])

    def test_confirm_layout_pair_out_of_range_error_shows_the_number_typed(self):
        self.app._image_layout_model = gui.LayoutModel(
            np.array([[0, 0], [10, 0]], dtype=np.float32)
        )
        self.app._image_layout_drawn_circles = [{'center': (5.0, 5.0), 'radius': 8.0}]
        self.app._image_populate_layout_pair_combo()
        self.app._image_layout_pair_drawn_var.set('5')  # only one drawn circle exists
        self.app._image_layout_pair_combo.set(self.app._image_layout_pair_combo.cget('values')[0])

        self.app._image_confirm_layout_pair()

        self.assertIn('drawn circle #5', self.app._image_layout_alignment_status_var.get())

    def test_layout_alignment_overlay_labels_drawn_circles_starting_at_one(self):
        self.app._image_layout_drawn_circles = [{'center': (10.0, 10.0), 'radius': 5.0}]
        self.app._image_layout_model = None
        frame = np.zeros((50, 50, 3), dtype=np.uint8)

        with mock.patch.object(gui.cv2, 'putText') as mocked_put_text:
            self.app._image_draw_layout_alignment_overlay(frame)

        mocked_put_text.assert_called_once()
        args, _kwargs = mocked_put_text.call_args
        self.assertEqual(args[1], '#1')

    # ══════════════════════════════════════════════════════════════════
    # Round 5: in-process Tkinter tuning windows
    # ══════════════════════════════════════════════════════════════════

    def test_open_circle_tuning_window_reports_error_without_camera_frame(self):
        self.app._image_current_frame_bgr = mock.Mock(return_value=None)

        with mock.patch.object(gui, 'CircleTuningWindow') as mocked_window:
            self.app._image_open_circle_tuning_window()

        mocked_window.assert_not_called()
        self.assertIn('start the camera first', self.app._image_layout_status_var.get())

    def test_open_probe_tuning_window_reports_error_without_camera_frame(self):
        self.app._image_current_frame_bgr = mock.Mock(return_value=None)

        with mock.patch.object(gui, 'ProbeTuningWindow') as mocked_window:
            self.app._image_open_probe_tuning_window()

        mocked_window.assert_not_called()
        self.assertIn('start the camera first', self.app._image_probe_status_var.get())

    def test_open_circle_tuning_window_saves_snapshot_and_opens_window(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.app._image_current_frame_bgr = mock.Mock(return_value=frame)

        with mock.patch.object(gui.cv2, 'imwrite', return_value=True) as mocked_imwrite, \
             mock.patch.object(gui, 'CircleTuningWindow') as mocked_window:
            self.app._image_open_circle_tuning_window()

        mocked_imwrite.assert_called_once()
        written_path = mocked_imwrite.call_args.args[0]
        self.assertTrue(written_path.endswith('circle.png'))
        self.assertIn('vision_calibration', written_path)
        self.assertEqual(
            os.path.basename(os.path.dirname(written_path)), 'vision_calibration'
        )

        mocked_window.assert_called_once()
        args, kwargs = mocked_window.call_args
        self.assertEqual(args[0], self.app)
        self.assertIs(args[1], frame)
        self.assertEqual(kwargs['image_path'], written_path)
        self.assertIs(kwargs['status_var'], self.app._image_layout_status_var)

    def test_open_probe_tuning_window_saves_snapshot_and_opens_window(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.app._image_current_frame_bgr = mock.Mock(return_value=frame)

        with mock.patch.object(gui.cv2, 'imwrite', return_value=True), \
             mock.patch.object(gui, 'ProbeTuningWindow') as mocked_window:
            self.app._image_open_probe_tuning_window()

        mocked_window.assert_called_once()
        args, kwargs = mocked_window.call_args
        self.assertTrue(kwargs['image_path'].endswith('probe.png'))
        self.assertIs(kwargs['status_var'], self.app._image_probe_status_var)

    def test_open_circle_tuning_window_reports_snapshot_write_failure(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.app._image_current_frame_bgr = mock.Mock(return_value=frame)

        with mock.patch.object(gui.cv2, 'imwrite', return_value=False), \
             mock.patch.object(gui, 'CircleTuningWindow') as mocked_window:
            self.app._image_open_circle_tuning_window()

        mocked_window.assert_not_called()
        self.assertIn('Tuning GUI failed', self.app._image_layout_status_var.get())

    def test_image_load_probe_params_file_sets_state(self):
        payload = {
            'preprocessing': {'clahe_clip': 3.5, 'clahe_tile': 6, 'bilateral_d': 7, 'bilateral_sigma': 60.0},
            'probe': {'invert': True},
        }
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(payload, f)
            path = f.name
        try:
            with mock.patch.object(gui.filedialog, 'askopenfilename', return_value=path):
                self.app._image_load_probe_params_file()
        finally:
            os.unlink(path)

        self.assertIsInstance(self.app._image_probe_detector, gui.ProbeDetector)
        self.assertEqual(self.app._image_probe_detector._clahe_clip, 3.5)
        self.assertEqual(self.app._image_probe_detector.invert, True)
        self.assertIn('Probe: params loaded', self.app._image_probe_status_var.get())

    # ══════════════════════════════════════════════════════════════════
    # Cross-thread Tk safety: root cause of a real, reproducible-across-
    # Python-3.7/3.8 Win7 production crash (0xc0000005 inside the
    # interpreter core) -- Tkinter/Tcl is not thread-safe, and this file's
    # AutoContactZ/automated-run code calls .set() on Tk Variables and
    # mutates widgets directly from background worker threads. Fixed via
    # _ThreadSafeVariableMixin (applied to every StringVar/BooleanVar/
    # IntVar/DoubleVar in gui.py) and _run_on_main_thread (for the
    # non-Variable widget mutations: Button.configure, Canvas draws).
    # ══════════════════════════════════════════════════════════════════

    def _capture_after_calls(self):
        """Replaces self.app.after with a recorder instead of letting a
        background thread touch the real Tk interpreter -- Python 3.12's
        tkinter raises "main thread is not in main loop" if .after() is
        called off-thread while no real mainloop() is running, which is a
        test-harness artifact (mainloop() is always running in the real
        app) rather than anything wrong with the fix under test. Matches
        the existing test_execute_contact_z_search_schedules_parallax_display_via_after_not_direct_call
        pattern already established in this file for the same reason."""
        # Mirrors tkinter.Misc.after's real signature -- (self, ms,
        # func=None, *args), no **kwargs -- so a call site that forwards
        # keyword args straight to self.after() (rather than binding them
        # into func first, e.g. via functools.partial) fails here the
        # same way it would against the real Tk after().
        scheduled = []
        orig_after = self.app.after
        self.app.after = lambda delay, fn, *a: scheduled.append((fn, a))
        self.addCleanup(lambda: setattr(self.app, 'after', orig_after))
        return scheduled

    def test_thread_safe_stringvar_set_from_main_thread_is_immediate(self):
        var = gui._ThreadSafeStringVar(master=self.app, value='before')
        var.set('after')
        self.assertEqual(var.get(), 'after')

    def test_thread_safe_stringvar_set_from_background_thread_marshals(self):
        var = gui._ThreadSafeStringVar(master=self.app, value='before')
        scheduled = self._capture_after_calls()
        thread = threading.Thread(target=lambda: var.set('from_bg'))
        thread.start()
        thread.join(timeout=5)
        # Not applied directly -- only queued via self._root.after(0, ...).
        self.assertEqual(var.get(), 'before')
        self.assertEqual(len(scheduled), 1)
        fn, args = scheduled[0]
        fn(*args)
        self.assertEqual(var.get(), 'from_bg')

    def test_thread_safe_boolean_var_set_from_background_thread_marshals(self):
        var = gui._ThreadSafeBooleanVar(master=self.app, value=False)
        scheduled = self._capture_after_calls()
        thread = threading.Thread(target=lambda: var.set(True))
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(var.get())
        self.assertEqual(len(scheduled), 1)
        fn, args = scheduled[0]
        fn(*args)
        self.assertTrue(var.get())

    def test_run_on_main_thread_calls_immediately_from_main_thread(self):
        called = []
        self.app._run_on_main_thread(lambda: called.append(1))
        self.assertEqual(called, [1])

    def test_run_on_main_thread_marshals_from_background_thread(self):
        called = []
        scheduled = self._capture_after_calls()
        thread = threading.Thread(
            target=lambda: self.app._run_on_main_thread(lambda: called.append(1))
        )
        thread.start()
        thread.join(timeout=5)
        self.assertEqual(called, [])
        self.assertEqual(len(scheduled), 1)
        fn, args = scheduled[0]
        fn(*args)
        self.assertEqual(called, [1])

    def test_image_set_mode_button_active_from_background_thread_marshals(self):
        button = gui.ttk.Button(self.app)
        scheduled = self._capture_after_calls()
        thread = threading.Thread(
            target=lambda: self.app._image_set_mode_button_active(button, True)
        )
        thread.start()
        thread.join(timeout=5)
        self.assertNotEqual(str(button.cget('style')), 'ImageModeActive.TButton')
        self.assertEqual(len(scheduled), 1)
        fn, args = scheduled[0]
        fn(*args)
        self.assertEqual(str(button.cget('style')), 'ImageModeActive.TButton')

    def test_image_set_mode_button_active_from_main_thread_is_immediate(self):
        button = gui.ttk.Button(self.app)
        self.app._image_set_mode_button_active(button, True)
        self.assertEqual(str(button.cget('style')), 'ImageModeActive.TButton')

    def test_redraw_monitor_from_background_thread_marshals(self):
        scheduled = self._capture_after_calls()
        with mock.patch.object(self.app, '_redraw_monitor_now') as mocked_now:
            thread = threading.Thread(target=self.app._redraw_monitor)
            thread.start()
            thread.join(timeout=5)
            mocked_now.assert_not_called()
            self.assertEqual(len(scheduled), 1)
            fn, args = scheduled[0]
            fn(*args)
            mocked_now.assert_called_once()

    def test_redraw_monitor_from_main_thread_calls_immediately(self):
        with mock.patch.object(self.app, '_redraw_monitor_now') as mocked_now:
            self.app._redraw_monitor()
            mocked_now.assert_called_once()

    def test_run_worker_progress_label_and_button_config_marshal_from_background_thread(self):
        # Found by a full-file audit of every .config(/.configure( call
        # site in gui.py (beyond the two non-Variable helpers already
        # covered by dedicated tests above) -- _run_worker itself calls
        # self._progress_lbl.config(...)/self._btn_start.config(...)/
        # self._btn_stop.config(...) directly, missed in the first pass of
        # this fix. Exercises the exact call shape used at those 3 sites
        # (self._run_on_main_thread(widget.config, **kwargs)) directly,
        # rather than running the whole _run_worker on a real thread --
        # that function also does plain cross-thread StringVar.get() reads
        # (e.g. self._result_dir.get()) elsewhere, which is a separate,
        # lower-severity, explicitly out-of-scope hazard for this fix (see
        # the plan) and which this Python/tkinter version raises
        # RuntimeError("main thread is not in main loop") for in a test
        # harness with no real mainloop running -- not something this test
        # is trying to cover.
        scheduled = self._capture_after_calls()

        def call_from_background():
            self.app._run_on_main_thread(self.app._progress_lbl.config, text='3 / 5')
            self.app._run_on_main_thread(self.app._btn_start.config, state='normal')
            self.app._run_on_main_thread(self.app._btn_stop.config, state='disabled')

        thread = threading.Thread(target=call_from_background)
        thread.start()
        thread.join(timeout=5)

        self.assertNotEqual(self.app._progress_lbl.cget('text'), '3 / 5')
        self.assertEqual(len(scheduled), 3)
        for fn, args in scheduled:
            fn(*args)

        self.assertEqual(self.app._progress_lbl.cget('text'), '3 / 5')
        self.assertEqual(str(self.app._btn_start.cget('state')), 'normal')
        self.assertEqual(str(self.app._btn_stop.cget('state')), 'disabled')
