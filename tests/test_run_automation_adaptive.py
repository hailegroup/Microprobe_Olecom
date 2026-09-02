import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import run_automation
from adaptive_engine import AdaptiveEngineSettings, AdaptiveMeasurementEngine
from adaptive_types import AnalysisResult
from measurement_sequence import SequenceResult


class _StubDevice:
    def connect(self):
        return None

    def disconnect(self):
        return None


def _fake_full_processor(*, dc_path, eis_path, sample_name):
    return {
        "status": "completed",
        "sample_name": sample_name,
        "excel_path": f"{sample_name}.xlsx",
        "output_dir": f"processed/{sample_name}",
        "notes": ["test full processing completed"],
    }


class RunAutomationAdaptiveTests(unittest.TestCase):
    def _make_row(self, label, v_dc):
        return {
            "Label": label,
            "Temperature_C": "None",
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

    def test_run_conditions_supports_adaptive_mode_switch_and_summary(self):
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

        def immediate_analyzer(*, dc_path, eis_path, sample_name):
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.2,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                recommended_normal_peis_lowest_freq_hz=1.0,
                peis_only_selection_reason="peis_reaches_saturation",
                cp_saturation_reached=True,
                agreement_rel_err=0.01,
            )

        engine_factory = lambda normal_eis_floor_hz=0.01: AdaptiveMeasurementEngine(
            analyzer=immediate_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.01),
        )

        df = pd.DataFrame([
            self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
            self._make_row("ADAPT_T300_E1_V+0.100", 0.1),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            results = run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                rapid_sequence=fake_rapid_sequence,
                normal_sequence=fake_normal_sequence,
                adaptive_engine_factory=engine_factory,
                log_fn=lambda *args, **kwargs: None,
            )

            summary_path = os.path.join(tmpdir, "adaptive_runtime_summary.json")
            self.assertTrue(os.path.exists(summary_path))
            with open(summary_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)

        self.assertEqual(calls[0][0], "rapid")
        self.assertEqual(calls[1][0], "normal")
        self.assertEqual(results[1]["MeasurementMode"], "normal_eis")
        self.assertEqual(len(payload["points"]), 2)
        self.assertEqual(payload["mode_counts"]["rapid_eis"], 1)
        self.assertEqual(payload["mode_counts"]["normal_eis"], 1)
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
        self.assertIn("finished_at", payload)

    def test_skip_ca_bypasses_adaptive_live_seeded_protocol(self):
        # An ADAPT-labeled row would normally route to
        # live_seeded_rapid_eis_sequence (a CA/FFT-seeded protocol) -- but
        # SkipCA=1 must be an absolute guarantee of no CA call, so it has to
        # override that routing entirely and run PEIS-only instead.
        calls = []

        def fake_normal_sequence(**kwargs):
            calls.append(("normal", kwargs["label"]))
            return SequenceResult(
                measurement_mode="normal_eis",
                eis_data=np.array([[1.0, 9.0, 1.5]]),
                peis_path=os.path.join(kwargs["save_dir"], f"{kwargs['label']}_peis_only.txt"),
            )

        def fake_rapid_sequence(**kwargs):
            calls.append(("rapid", kwargs["label"]))
            raise AssertionError("rapid_sequence must not be called when SkipCA=1")

        row = self._make_row("ADAPT_T300_E1_V+0.000", 0.0)
        row["SkipCA"] = 1
        df = pd.DataFrame([row])

        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch.object(
                 run_automation, "live_seeded_rapid_eis_sequence",
                 side_effect=AssertionError("live_seeded_rapid_eis_sequence must not be called when SkipCA=1"),
             ):
            results = run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                rapid_sequence=fake_rapid_sequence,
                normal_sequence=fake_normal_sequence,
                log_fn=lambda *args, **kwargs: None,
            )

        self.assertEqual(calls, [("normal", "ADAPT_T300_E1_V+0.000")])
        self.assertEqual(results[0]["MeasurementMode"], "normal_eis")

    def test_run_conditions_backfills_delayed_analysis_summary(self):
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

        def delayed_analyzer(*, dc_path, eis_path, sample_name):
            time.sleep(0.05)
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.2,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                recommended_normal_peis_lowest_freq_hz=1.0,
                peis_only_selection_reason="peis_reaches_saturation",
                cp_saturation_reached=True,
                agreement_rel_err=0.01,
            )

        engine_factory = lambda normal_eis_floor_hz=0.01: AdaptiveMeasurementEngine(
            analyzer=delayed_analyzer,
            full_processor=_fake_full_processor,
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.01),
        )

        df = pd.DataFrame([
            self._make_row("ADAPT_T300_E1_V+0.000", 0.0),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            results = run_automation._run_conditions(
                df,
                result_root=tmpdir,
                bl=_StubDevice(),
                rapid_sequence=fake_rapid_sequence,
                normal_sequence=None,
                adaptive_engine_factory=engine_factory,
                log_fn=lambda *args, **kwargs: None,
            )

            summary_path = os.path.join(tmpdir, "adaptive_runtime_summary.json")
            self.assertTrue(os.path.exists(summary_path))
            with open(summary_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["MeasurementMode"], "rapid_eis")
        finalize = payload["points"][0]["analysis_finalize"]
        self.assertTrue(finalize["analysis_ready_within_wait"])
        self.assertEqual(finalize["state"], "recommendation_ready")
        self.assertIsNotNone(finalize["analysis_result"])
        completed = payload["points"][0]["completed_point_current_run_policy"]
        self.assertEqual(completed["action"], "handoff_to_normal_lf")
        self.assertEqual(completed["measurement_mode"], "normal_eis")
        completed_text = payload["points"][0]["completed_point_current_run_policy_text"]
        self.assertIn("completed-point policy stored a normal handoff", completed_text)


if __name__ == "__main__":
    unittest.main()
