# -*- coding: utf-8 -*-
import json
import tempfile
import time
import unittest
from pathlib import Path
import sys

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from adaptive_engine import AdaptiveMeasurementEngine, AdaptiveEngineSettings
from adaptive_types import AnalysisResult, MeasurementExecution, MeasurementFiles, PointMetadata
from analysis_adapter import (
    AnalysisBackendSettings,
    _infer_current_in_mA_from_path,
    analyze_measurement_files,
)
from stabilization_policy import StabilizationSettings


def _make_fake_analyzer(result: AnalysisResult, delay_s: float = 0.0):
    def _fake_analyzer(*, dc_path, eis_path, sample_name):
        if delay_s > 0:
            time.sleep(delay_s)
        return result

    return _fake_analyzer


class AdaptiveEngineTests(unittest.TestCase):
    def _point(self, point_id, electrode, temp, gas_a, gas_b, seq):
        return PointMetadata(
            point_id=point_id,
            label=point_id,
            electrode_id=electrode,
            temperature_c=temp,
            gas_a_sccm=gas_a,
            gas_b_sccm=gas_b,
            voltage_v=0.0,
            sequence_index=seq,
        )

    def _measurement(self, lf, cp):
        return MeasurementExecution(
            measurement_mode="rapid_eis",
            peis_lowest_freq_hz=lf,
            cp_duration_s=cp,
            actual_peis_high_freq_hz=1e5,
        )

    def _files(self):
        return MeasurementFiles(
            pre_ca_path="demo_pre_ca.txt",
            peis_path="demo_peis.txt",
            post_ca_path="demo_post_ca.txt",
        )

    def _full_processor(self, delay_s=0.0):
        def _processor(*, dc_path, eis_path, sample_name):
            if delay_s > 0:
                time.sleep(delay_s)
            return {
                "status": "completed",
                "sample_name": sample_name,
                "excel_path": f"{sample_name}.xlsx",
                "output_dir": f"processed/{sample_name}",
                "processing_latency_s": delay_s,
                "fit_summary": {"demo": "ok"},
                "notes": ["background full processing completed"],
            }
        return _processor

    def test_same_electrode_history_is_preferred(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.09,
                    recommended_peis_conservative_cp_time_s=50.0,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                )
            )
        )

        p1 = self._point("p1", 2, 280, 10, 10, 1)
        m1 = self._measurement(0.10, 52.0)
        engine.register_point(p1, m1, self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)

        p2 = self._point("p2", 1, 300, 10, 10, 2)
        m2 = self._measurement(0.14, 40.0)
        engine.register_point(p2, m2, self._files())
        engine.start_analysis("p2")
        engine.finalize_completed_point("p2", timeout_s=0.5)

        target = self._point("target", 2, 300, 10, 10, 3)
        rec = engine.recommend_next_point(target)
        self.assertEqual(rec.seed_source, "same_electrode_previous_condition")
        self.assertLessEqual(rec.peis_lowest_freq_hz, 0.09)
        self.assertGreaterEqual(rec.cp_duration_s, 50.0)
        self.assertGreaterEqual(rec.planned_pre_peis_hold_s, 20.0)
        self.assertGreaterEqual(rec.planned_post_peis_hold_s, 20.0)
        engine.shutdown()

    def test_slower_trend_applies_more_strongly_than_speedup(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.09,
                    recommended_peis_conservative_cp_time_s=45.0,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                )
            )
        )
        for point_id, electrode, cp, lf in [
            ("p1", 1, 40.0, 0.12),
            ("p2", 2, 60.0, 0.08),
        ]:
            engine.register_point(self._point(point_id, electrode, 300, 10, 10, electrode), self._measurement(lf, cp), self._files())
            engine.start_analysis(point_id)
            engine.finalize_completed_point(point_id, timeout_s=0.5)

        target = self._point("p3", 3, 300, 10, 10, 3)
        rec = engine.recommend_next_point(target)
        self.assertGreater(rec.cp_duration_s, 45.0)
        self.assertLess(rec.peis_lowest_freq_hz, 0.09)
        engine.shutdown()

    def test_first_pass_fallback_without_history(self):
        engine = AdaptiveMeasurementEngine(analyzer=_make_fake_analyzer(
            AnalysisResult(
                recommended_peis_lowest_freq_hz=None,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=False,
                peis_only_sufficient=False,
            )
        ))
        target = self._point("p1", 1, 300, 10, 10, 1)
        rec = engine.recommend_next_point(target)
        self.assertTrue(rec.used_fallback)
        self.assertGreaterEqual(rec.cp_duration_s, 100.0)
        self.assertLessEqual(rec.peis_lowest_freq_hz, 0.05)
        self.assertGreaterEqual(rec.planned_pre_peis_hold_s, 20.0)
        self.assertGreaterEqual(rec.planned_post_peis_hold_s, 20.0)
        engine.shutdown()

    def test_remeasurement_request_is_generated_for_insufficient_data(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.04,
                    recommended_peis_conservative_cp_time_s=120.0,
                    data_sufficient=False,
                    peis_only_sufficient=False,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        m1 = self._measurement(0.08, 40.0)
        engine.register_point(p1, m1, self._files())
        engine.start_analysis("p1")
        finalize = engine.finalize_completed_point("p1", timeout_s=0.5)
        self.assertEqual(finalize["state"], "remeasure_required")
        pending = engine.collect_pending_remeasurements()
        self.assertEqual(len(pending), 1)
        self.assertGreater(pending[0].cp_duration_s, 40.0)
        self.assertLess(pending[0].peis_lowest_freq_hz, 0.08)
        self.assertGreaterEqual(pending[0].planned_pre_peis_hold_s, 20.0)
        self.assertGreaterEqual(pending[0].planned_post_peis_hold_s, 20.0)
        engine.shutdown()

    def test_mode_choice_uses_trusted_peis_only_signal(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.20,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_reaches_saturation",
                    cp_saturation_reached=True,
                    agreement_rel_err=0.02,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 60.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "normal_eis")
        engine.shutdown()

    def test_normal_mode_prefers_peis_only_lf_over_cp_comparison_lf(self):
        from Analysis_Convert_CP_to_EIS.adaptive_measurement_policy import suggest_next_parameters

        plan = suggest_next_parameters(
            current_point=self._point("p2", 1, 300, 10, 10, 2).to_policy_dict(),
            history=[],
            analysis_recommendation={
                "peis_only_sufficient": True,
                "recommended_peis_lowest_freq_hz": 0.05,
                "recommended_normal_peis_lowest_freq_hz": 1.0,
                "recommended_peis_conservative_cp_time_s": None,
            },
        )
        self.assertEqual(plan["measurement_mode"], "normal_eis")
        self.assertAlmostEqual(plan["peis_lowest_freq_hz"], 0.85, places=9)

    def test_strong_peis_only_signal_can_bypass_cp_saturation_guard(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.20,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_reaches_saturation_via_strong_agreement",
                    cp_saturation_reached=False,
                    agreement_rel_err=0.02,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 60.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "normal_eis")
        engine.shutdown()

    def test_plateau_signal_can_switch_to_normal_even_if_cp_saturation_is_false(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.20,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_reaches_saturation",
                    cp_saturation_reached=False,
                    agreement_rel_err=0.06,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 60.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "normal_eis")
        self.assertTrue(rec.hybrid_unnecessary)
        engine.shutdown()

    def test_normal_eis_followup_can_be_analyzed_without_dc_file(self):
        seen = {}

        def _analyzer(*, dc_path, eis_path, sample_name):
            seen["dc_path"] = dc_path
            seen["eis_path"] = eis_path
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.12,
                recommended_peis_conservative_cp_time_s=None,
                data_sufficient=True,
                peis_only_sufficient=True,
                peis_only_selection_reason="peis_reaches_saturation",
                agreement_rel_err=0.01,
            )

        engine = AdaptiveMeasurementEngine(analyzer=_analyzer)
        point = self._point("p1", 1, 300, 10, 10, 1)
        measurement = MeasurementExecution(
            measurement_mode="normal_eis",
            peis_lowest_freq_hz=0.20,
            cp_duration_s=None,
            actual_peis_high_freq_hz=1e5,
        )
        files = MeasurementFiles(peis_path="demo_peis_only.txt")
        engine.register_point(point, measurement, files)
        engine.start_post_measurement_pipeline("p1", start_full_processing=False)
        finalize = engine.finalize_completed_point("p1", timeout_s=0.5)
        self.assertTrue(finalize["analysis_ready_within_wait"])
        self.assertIsNone(seen["dc_path"])
        self.assertEqual(seen["eis_path"], "demo_peis_only.txt")
        engine.shutdown()

    def test_normal_eis_insufficient_followup_requests_rapid_remeasure(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.04,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=False,
                    peis_only_sufficient=False,
                    peis_only_selection_reason="hybrid_still_helpful",
                )
            )
        )
        point = self._point("p1", 1, 300, 10, 10, 1)
        measurement = MeasurementExecution(
            measurement_mode="normal_eis",
            peis_lowest_freq_hz=0.20,
            cp_duration_s=None,
            actual_peis_high_freq_hz=1e5,
        )
        files = MeasurementFiles(peis_path="demo_peis_only.txt")
        engine.register_point(point, measurement, files)
        engine.start_post_measurement_pipeline("p1", start_full_processing=False)
        finalize = engine.finalize_completed_point("p1", timeout_s=0.5)
        self.assertEqual(finalize["state"], "remeasure_required")
        pending = engine.collect_pending_remeasurements()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].measurement_mode, "rapid_eis")
        self.assertGreater(pending[0].cp_duration_s, 0.0)

    def test_finalize_completed_point_exposes_two_stage_current_run_policy(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.25,
                    recommended_peis_conservative_cp_time_s=210.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                    recommended_normal_peis_lowest_freq_hz=0.10,
                    peis_only_selection_reason="hybrid_still_helpful",
                )
            )
        )
        point = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(point, self._measurement(0.19, 120.0), self._files())
        engine.start_analysis("p1")
        finalize = engine.finalize_completed_point("p1", timeout_s=0.5)
        policy = finalize["current_run_policy_decision"]
        self.assertIsNotNone(policy)
        self.assertEqual(policy["action"], "keep_exploratory_seed_for_hybrid")
        self.assertEqual(policy["measurement_mode"], "rapid_eis")
        self.assertAlmostEqual(policy["exploratory_seed_lf_hz"], 0.19, places=9)
        self.assertAlmostEqual(policy["runtime_target_lf_hz"], 0.25, places=9)
        self.assertEqual(
            engine.get_point_record("p1").current_run_policy_decision.action,
            "keep_exploratory_seed_for_hybrid",
        )
        engine.shutdown()

    def test_finalize_completed_point_normal_case_hands_off_to_normal_lf(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.10,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    recommended_normal_peis_lowest_freq_hz=1.0,
                    peis_only_selection_reason="peis_reaches_saturation",
                )
            )
        )
        point = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(point, self._measurement(0.10, 30.0), self._files())
        engine.start_analysis("p1")
        finalize = engine.finalize_completed_point("p1", timeout_s=0.5)
        policy = finalize["current_run_policy_decision"]
        self.assertIsNotNone(policy)
        self.assertEqual(policy["action"], "handoff_to_normal_lf")
        self.assertEqual(policy["measurement_mode"], "normal_eis")
        self.assertAlmostEqual(policy["runtime_lf_hz"], 1.0, places=9)
        self.assertAlmostEqual(policy["runtime_target_lf_hz"], 1.0, places=9)
        self.assertEqual(
            engine.get_point_record("p1").current_run_policy_decision.action,
            "handoff_to_normal_lf",
        )
        engine.shutdown()
        engine.shutdown()

    def test_recommend_next_point_consumes_stored_two_stage_normal_handoff(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.10,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    recommended_normal_peis_lowest_freq_hz=1.0,
                    peis_only_selection_reason="peis_reaches_saturation",
                    agreement_rel_err=0.01,
                    cp_saturation_reached=True,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 30.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)

        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "normal_eis")
        self.assertAlmostEqual(rec.peis_lowest_freq_hz, 1.0, places=9)
        self.assertEqual(rec.current_run_policy_action, "handoff_to_normal_lf")
        self.assertTrue(rec.current_run_policy_consumed)
        self.assertEqual(rec.current_run_policy_analysis_point_id, "p1")
        self.assertAlmostEqual(rec.current_run_policy_runtime_lf_hz, 1.0, places=9)
        self.assertTrue(
            any("current_run_policy_decision=handoff_to_normal_lf" in note for note in rec.notes)
        )
        engine.shutdown()

    def test_recommend_next_point_consumes_stored_two_stage_hybrid_seed(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.25,
                    recommended_peis_conservative_cp_time_s=210.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                    recommended_normal_peis_lowest_freq_hz=0.10,
                    peis_only_selection_reason="hybrid_still_helpful",
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.19, 120.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)

        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "rapid_eis")
        self.assertAlmostEqual(rec.peis_lowest_freq_hz, 0.19, places=9)
        self.assertEqual(rec.current_run_policy_action, "keep_exploratory_seed_for_hybrid")
        self.assertTrue(rec.current_run_policy_consumed)
        self.assertEqual(rec.current_run_policy_analysis_point_id, "p1")
        self.assertAlmostEqual(rec.current_run_policy_runtime_lf_hz, 0.19, places=9)
        self.assertTrue(
            any("current_run_policy_decision=keep_exploratory_seed_for_hybrid" in note for note in rec.notes)
        )
        engine.shutdown()

    def test_periodic_rapid_refresh_breaks_consecutive_normal_chain(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.18,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_reaches_saturation",
                    agreement_rel_err=0.01,
                    cp_saturation_reached=True,
                )
            ),
            engine_settings=AdaptiveEngineSettings(max_consecutive_normal_eis_points=2),
        )

        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 60.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)

        for point_id, seq in [("p2", 2), ("p3", 3)]:
            point = self._point(point_id, 1, 300, 10, 10, seq)
            measurement = MeasurementExecution(
                measurement_mode="normal_eis",
                peis_lowest_freq_hz=0.05,
                cp_duration_s=None,
                actual_peis_high_freq_hz=1e5,
            )
            files = MeasurementFiles(peis_path=f"{point_id}_peis.txt")
            engine.register_point(point, measurement, files)
            engine.start_post_measurement_pipeline(point_id, start_full_processing=False)
            engine.finalize_completed_point(point_id, timeout_s=0.5)

        rec = engine.recommend_next_point(self._point("p4", 1, 300, 10, 10, 4))
        self.assertEqual(rec.measurement_mode, "rapid_eis")
        self.assertGreater(rec.cp_duration_s, 0.0)
        self.assertTrue(any("forced periodic rapid refresh" in note for note in rec.notes))
        engine.shutdown()

    def test_new_regime_keeps_rapid_even_if_raw_analysis_says_peis_only(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.20,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_reaches_saturation",
                    cp_saturation_reached=True,
                    agreement_rel_err=0.02,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.10, 60.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 400, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "rapid_eis")
        self.assertFalse(rec.hybrid_unnecessary)
        self.assertTrue(any("new temperature/gas/electrode regime" in note for note in rec.notes))
        engine.shutdown()

    def test_heuristic_peis_only_reason_keeps_rapid_and_preserves_cp_seed(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.05,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_plateau_and_no_hybrid_gain",
                    cp_saturation_reached=True,
                    agreement_rel_err=0.95,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.05, 300.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "rapid_eis")
        self.assertGreater(rec.cp_duration_s, 300.0)
        self.assertFalse(rec.hybrid_unnecessary)
        self.assertTrue(any("agreement is too weak to trust" in note for note in rec.notes))
        engine.shutdown()

    def test_plateau_and_no_hybrid_gain_can_switch_to_normal_when_agreement_is_reasonable(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.05,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    peis_only_selection_reason="peis_plateau_and_no_hybrid_gain",
                    cp_saturation_reached=False,
                    agreement_rel_err=0.25,
                )
            )
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.05, 300.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertEqual(rec.measurement_mode, "normal_eis")
        self.assertTrue(rec.hybrid_unnecessary)
        engine.shutdown()

    def test_hybrid_wait_keeps_fallback_then_applies_late_result(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.005,
                    recommended_peis_conservative_cp_time_s=140.0,
                    data_sufficient=False,
                    peis_only_sufficient=False,
                ),
                delay_s=0.6,
            ),
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.05),
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.08, 50.0), self._files())
        engine.start_analysis("p1")
        finalize = engine.finalize_completed_point("p1", timeout_s=0.05)
        self.assertFalse(finalize["analysis_ready_within_wait"])
        rec = engine.recommend_next_point(self._point("p2", 2, 300, 10, 10, 2))
        self.assertTrue(rec.used_fallback)
        time.sleep(0.8)
        engine.poll_ready_analyses()
        late = engine.collect_pending_remeasurements()
        self.assertEqual(len(late), 1)
        engine.shutdown()

    def test_full_processing_can_run_in_background_while_fast_analysis_drives_next_point(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.03,
                    recommended_peis_conservative_cp_time_s=60.0,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                ),
                delay_s=0.05,
            ),
            full_processor=self._full_processor(delay_s=0.5),
            engine_settings=AdaptiveEngineSettings(analysis_wait_timeout_s=0.2),
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(p1, self._measurement(0.08, 50.0), self._files())
        engine.start_post_measurement_pipeline("p1")
        finalize = engine.finalize_completed_point("p1", timeout_s=0.2)
        self.assertTrue(finalize["analysis_ready_within_wait"])
        self.assertEqual(finalize["full_processing_state"], "processing")
        rec = engine.recommend_next_point(self._point("p2", 2, 300, 10, 10, 2))
        self.assertFalse(rec.used_fallback)
        time.sleep(0.7)
        engine.poll_ready_full_processing()
        record = engine.get_point_record("p1")
        self.assertEqual(record.full_processing_state, "completed")
        self.assertIsNotNone(record.full_processing_result)
        engine.shutdown()

    def test_pre_peis_stability_assessment_allows_peis_when_drift_is_small(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.02,
                    recommended_peis_conservative_cp_time_s=60.0,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                )
            ),
            stabilization_settings=StabilizationSettings(min_points_in_window=4, required_stable_windows=2, live_window_s=40.0),
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        m1 = self._measurement(0.08, 50.0)
        m1.planned_pre_peis_hold_s = 100.0
        engine.register_point(p1, m1, self._files())
        time_axis = list(range(0, 181, 10))
        current = [
            4.0e-6, 2.8e-6, 1.9e-6, 1.4e-6, 1.1e-6, 1.02e-6, 0.995e-6,
            0.985e-6, 0.980e-6, 0.978e-6, 0.976e-6, 0.975e-6, 0.9745e-6,
            0.9740e-6, 0.9738e-6, 0.9736e-6, 0.9735e-6, 0.9735e-6, 0.9734e-6,
        ]
        assessment = engine.assess_pre_peis_stabilization("p1", time_s=time_axis, current_a=current)
        self.assertTrue(assessment.stable)
        self.assertTrue(assessment.should_start_peis)
        engine.shutdown()

    def test_pre_peis_instability_can_abort_recent_peis_and_request_retry(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.02,
                    recommended_peis_conservative_cp_time_s=60.0,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                )
            ),
            stabilization_settings=StabilizationSettings(min_points_in_window=4, required_stable_windows=2, live_window_s=40.0),
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        m1 = self._measurement(0.08, 50.0)
        engine.register_point(p1, m1, self._files())
        time_axis = [0, 10, 20, 30, 40, 50, 60]
        current = [1.0e-6, 3.0e-6, 2.4e-6, 3.3e-6, 2.7e-6, 3.5e-6, 2.9e-6]
        assessment = engine.assess_pre_peis_stabilization(
            "p1",
            time_s=time_axis,
            current_a=current,
            peis_already_started=True,
        )
        self.assertFalse(assessment.stable)
        self.assertTrue(assessment.should_abort_recent_peis)
        pending = engine.collect_pending_remeasurements()
        self.assertEqual(len(pending), 1)
        self.assertIsNotNone(pending[0].planned_pre_peis_hold_s)
        self.assertGreaterEqual(pending[0].planned_post_peis_hold_s, 20.0)
        engine.shutdown()

    def test_bias_sweep_order_returns_a_valid_order(self):
        engine = AdaptiveMeasurementEngine(analyzer=_make_fake_analyzer(
            AnalysisResult(
                recommended_peis_lowest_freq_hz=0.05,
                recommended_peis_conservative_cp_time_s=70.0,
                data_sufficient=True,
                peis_only_sufficient=False,
            )
        ))
        for idx, voltage in enumerate([0.30, 0.25, 0.20], start=1):
            point = self._point(f"p{idx}", 1, 300, 10, 10, idx)
            point.voltage_v = voltage
            meas = self._measurement(0.08, 50.0)
            meas.stabilization_hold_s = 120.0 - 20.0 * idx
            engine.register_point(point, meas, self._files())
        result = engine.recommend_bias_sweep_order(
            bias_values_v=[0.3, 0.25, 0.2, 0.15, 0.1, 0.05, 0.0],
            condition_context={"electrode_id": 1, "temperature_c": 300, "gas_a_sccm": 10, "gas_b_sccm": 10},
        )
        self.assertEqual(sorted(result["ordered_biases_v"]), sorted([0.3, 0.25, 0.2, 0.15, 0.1, 0.05, 0.0]))
        self.assertIn(result["strategy"], {"ascending", "descending"})
        engine.shutdown()

    def test_post_peis_hold_floor_is_kept_at_least_20_seconds(self):
        engine = AdaptiveMeasurementEngine(analyzer=_make_fake_analyzer(
            AnalysisResult(
                recommended_peis_lowest_freq_hz=0.05,
                recommended_peis_conservative_cp_time_s=45.0,
                data_sufficient=True,
                peis_only_sufficient=False,
            )
        ))
        rec = engine.recommend_next_point(self._point("p1", 1, 300, 10, 10, 1))
        self.assertGreaterEqual(rec.planned_post_peis_hold_s, 20.0)
        engine.shutdown()

    def test_post_ca_file_is_preferred_for_analysis_over_pre_stabilization_file(self):
        captured = {}

        def _capturing_analyzer(*, dc_path, eis_path, sample_name):
            captured["dc_path"] = dc_path
            captured["eis_path"] = eis_path
            return AnalysisResult(
                recommended_peis_lowest_freq_hz=0.05,
                recommended_peis_conservative_cp_time_s=45.0,
                data_sufficient=True,
                peis_only_sufficient=False,
            )

        engine = AdaptiveMeasurementEngine(analyzer=_capturing_analyzer)
        point = self._point("p1", 1, 300, 10, 10, 1)
        engine.register_point(point, self._measurement(0.08, 50.0), self._files())
        engine.start_analysis("p1")
        engine.finalize_completed_point("p1", timeout_s=0.5)
        self.assertEqual(captured["dc_path"], "demo_post_ca.txt")
        self.assertEqual(captured["eis_path"], "demo_peis.txt")
        engine.shutdown()

    def test_completed_pre_peis_hold_can_recommend_shorter_next_time(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.05,
                    recommended_peis_conservative_cp_time_s=45.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                )
            ),
            stabilization_settings=StabilizationSettings(
                min_points_in_window=4,
                live_window_s=20.0,
                required_stable_windows=2,
                min_hold_s=20.0,
            ),
        )
        point = self._point("p1", 1, 300, 10, 10, 1)
        point.voltage_v = 0.0
        engine.register_point(point, self._measurement(0.08, 60.0), self._files())
        time_axis = list(range(0, 121, 5))
        current = [
            6.0e-6, 4.5e-6, 3.2e-6, 2.1e-6, 1.5e-6,
            1.1e-6, 1.02e-6, 1.005e-6, 0.999e-6, 0.997e-6,
            0.996e-6, 0.9955e-6, 0.9950e-6, 0.9948e-6, 0.9947e-6,
            0.9946e-6, 0.9946e-6, 0.9945e-6, 0.9945e-6, 0.9945e-6,
            0.9945e-6, 0.9945e-6, 0.9945e-6, 0.9945e-6, 0.9945e-6,
        ]
        assessment = engine.assess_completed_pre_peis_hold("p1", time_s=time_axis, current_a=current)
        self.assertTrue(assessment.saturated_enough)
        self.assertTrue(assessment.can_reduce_next_time)
        self.assertLess(assessment.recommended_next_hold_s, assessment.current_hold_s)
        self.assertIsNotNone(engine.get_point_record("p1").measurement.optimized_pre_peis_hold_s)
        engine.shutdown()

    def test_completed_pre_peis_hold_requests_extension_when_not_stable(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.05,
                    recommended_peis_conservative_cp_time_s=45.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                )
            ),
            stabilization_settings=StabilizationSettings(
                min_points_in_window=4,
                live_window_s=20.0,
                required_stable_windows=2,
                min_hold_s=20.0,
            ),
        )
        point = self._point("p1", 1, 300, 10, 10, 1)
        point.voltage_v = 0.0
        engine.register_point(point, self._measurement(0.08, 60.0), self._files())
        time_axis = list(range(0, 61, 5))
        current = [1.0e-6, 2.1e-6, 1.4e-6, 2.4e-6, 1.5e-6, 2.3e-6, 1.6e-6, 2.5e-6, 1.5e-6, 2.2e-6, 1.4e-6, 2.4e-6, 1.3e-6]
        assessment = engine.assess_completed_pre_peis_hold("p1", time_s=time_axis, current_a=current)
        self.assertFalse(assessment.saturated_enough)
        self.assertTrue(assessment.should_extend_now)
        self.assertGreater(assessment.recommended_retry_hold_s, assessment.current_hold_s)
        engine.shutdown()

    def test_completed_post_peis_hold_updates_next_recommendation(self):
        engine = AdaptiveMeasurementEngine(
            analyzer=_make_fake_analyzer(
                AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.05,
                    recommended_peis_conservative_cp_time_s=45.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                )
            ),
            stabilization_settings=StabilizationSettings(
                min_points_in_window=4,
                live_window_s=20.0,
                required_stable_windows=2,
                min_post_peis_hold_s=20.0,
            ),
        )
        p1 = self._point("p1", 1, 300, 10, 10, 1)
        p1.voltage_v = 0.0
        m1 = self._measurement(0.08, 60.0)
        engine.register_point(p1, m1, self._files())
        time_axis = list(range(0, 81, 5))
        current = [
            4.0e-6, 2.8e-6, 1.8e-6, 1.2e-6, 1.05e-6,
            1.01e-6, 1.000e-6, 0.999e-6, 0.999e-6, 0.9988e-6,
            0.9987e-6, 0.9987e-6, 0.9986e-6, 0.9986e-6, 0.9986e-6,
            0.9986e-6, 0.9986e-6,
        ]
        assessment = engine.assess_completed_post_peis_hold("p1", time_s=time_axis, current_a=current)
        self.assertTrue(assessment.saturated_enough)
        self.assertLess(assessment.recommended_next_hold_s, assessment.current_hold_s)

        rec = engine.recommend_next_point(self._point("p2", 1, 300, 10, 10, 2))
        self.assertLess(rec.planned_post_peis_hold_s, 40.0)
        engine.shutdown()


class AnalysisAdapterIntegrationTests(unittest.TestCase):
    def test_analysis_backend_settings_default_to_trusted_auto_trim(self):
        self.assertTrue(AnalysisBackendSettings().auto_trim)
        self.assertIsNone(AnalysisBackendSettings().current_in_mA)

    def test_current_unit_inference_uses_text_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            txt_path = tmp / "demo_ca.txt"
            txt_path.write_text("time/s  V/V  I/A\n0 0 0\n1 0.1 0.01\n", encoding="utf-8")
            self.assertFalse(_infer_current_in_mA_from_path(txt_path, None))

            mpr_path = tmp / "demo_ca.mpr"
            mpr_path.write_bytes(b"BIO")
            self.assertTrue(_infer_current_in_mA_from_path(mpr_path, None))

    def test_saved_measurement_files_turn_into_machine_readable_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            t = np.arange(0.0, 200.0, 0.1)
            voltage = 0.3 + 0.015 * np.sin(2 * np.pi * 0.02 * t) + 0.007 * np.sin(2 * np.pi * 0.2 * t)
            current = 0.0012 * np.sin(2 * np.pi * 0.02 * t - 0.4) + 0.0005 * np.sin(2 * np.pi * 0.2 * t - 0.2)
            dc_data = np.column_stack([t, voltage, current])
            dc_path = tmp / "demo_pre_ca.txt"
            np.savetxt(dc_path, dc_data)

            freq = np.logspace(-2, 3, 90)
            rez = 10 + 2 / np.sqrt(freq)
            imz = -1.2 / np.sqrt(freq)
            eis_data = np.column_stack([freq, rez, -imz])
            eis_path = tmp / "demo_peis.txt"
            np.savetxt(eis_path, eis_data)

            result = analyze_measurement_files(
                dc_path,
                eis_path,
                settings=AnalysisBackendSettings(current_in_mA=False),
                sample_name="synthetic_demo",
            )
            self.assertIn("provisional", json.dumps(result.to_dict()))
            self.assertIsNotNone(result.recommended_peis_lowest_freq_hz)
            self.assertIsNotNone(result.recommended_peis_conservative_cp_time_s)
            self.assertIsInstance(result.data_sufficient, bool)


if __name__ == "__main__":
    unittest.main()
