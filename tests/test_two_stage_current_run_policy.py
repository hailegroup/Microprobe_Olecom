import json
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_DIR.parent
CONVERT_ROOT = REPO_ROOT / "Convert_CP_to_EIS 1"

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from adaptive_engine import AdaptiveMeasurementEngine
from adaptive_types import AnalysisResult


class TwoStageCurrentRunPolicyTests(unittest.TestCase):
    def test_hybrid_case_keeps_exploratory_seed(self):
        engine = AdaptiveMeasurementEngine()
        try:
            decision = engine.plan_two_stage_current_run_policy(
                exploratory_seed_lf_hz=0.19,
                analysis_result=AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.25,
                    recommended_peis_conservative_cp_time_s=210.0,
                    data_sufficient=True,
                    peis_only_sufficient=False,
                    recommended_normal_peis_lowest_freq_hz=1.0,
                    peis_only_selection_reason="hybrid_still_helpful",
                ),
            )
        finally:
            engine.shutdown()

        self.assertEqual(decision.action, "keep_exploratory_seed_for_hybrid")
        self.assertEqual(decision.measurement_mode, "rapid_eis")
        self.assertAlmostEqual(decision.runtime_lf_hz, 0.19)
        self.assertAlmostEqual(decision.runtime_target_lf_hz, 0.25)
        self.assertEqual(decision.runtime_vs_target_relation, "conservative_or_equal")
        self.assertLess(decision.runtime_vs_target_log10_gap, 0.2)

    def test_normal_case_hands_off_to_normal_lf(self):
        engine = AdaptiveMeasurementEngine()
        try:
            decision = engine.plan_two_stage_current_run_policy(
                exploratory_seed_lf_hz=0.10,
                analysis_result=AnalysisResult(
                    recommended_peis_lowest_freq_hz=0.10,
                    recommended_peis_conservative_cp_time_s=None,
                    data_sufficient=True,
                    peis_only_sufficient=True,
                    recommended_normal_peis_lowest_freq_hz=1.0,
                    peis_only_selection_reason="peis_reaches_saturation",
                ),
            )
        finally:
            engine.shutdown()

        self.assertEqual(decision.action, "handoff_to_normal_lf")
        self.assertEqual(decision.measurement_mode, "normal_eis")
        self.assertAlmostEqual(decision.runtime_lf_hz, 1.0)
        self.assertAlmostEqual(decision.runtime_target_lf_hz, 1.0)
        self.assertEqual(decision.runtime_vs_target_relation, "conservative_or_equal")
        self.assertAlmostEqual(decision.runtime_vs_target_log10_gap, 0.0)

    def test_microprobe_summary_matches_convert_artifact(self):
        script_path = PROJECT_DIR / "simulate_two_stage_current_run_policy.py"
        subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_DIR),
            check=True,
        )

        source_summary = json.loads(
            (
                CONVERT_ROOT
                / "result"
                / "two_stage_current_run_policy"
                / "two_stage_current_run_policy_summary.json"
            ).read_text(encoding="utf-8")
        )
        microprobe_summary = json.loads(
            (
                PROJECT_DIR
                / "results"
                / "two_stage_current_run_policy"
                / "two_stage_current_run_policy_summary.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(len(microprobe_summary["cases"]), len(source_summary["cases"]))
        self.assertTrue(microprobe_summary["aggregate"]["all_actions_match"])
        self.assertTrue(microprobe_summary["aggregate"]["all_runtime_lf_match"])
        self.assertLess(microprobe_summary["aggregate"]["max_runtime_gap_abs_diff"], 1e-12)


if __name__ == "__main__":
    unittest.main()
