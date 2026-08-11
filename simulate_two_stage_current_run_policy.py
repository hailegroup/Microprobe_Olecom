import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONVERT_ROOT = PROJECT_ROOT / "Convert_CP_to_EIS 1"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from adaptive_engine import AdaptiveMeasurementEngine  # noqa: E402
from adaptive_types import AnalysisResult  # noqa: E402


def _build_analysis_result(case_row):
    return AnalysisResult(
        recommended_peis_lowest_freq_hz=float(case_row["general_target_lf_hz"]),
        recommended_peis_conservative_cp_time_s=None,
        data_sufficient=True,
        peis_only_sufficient=bool(case_row["final_mode"] == "normal"),
        recommended_normal_peis_lowest_freq_hz=float(case_row["normal_target_lf_hz"]),
        peis_only_selection_reason=str(case_row.get("selection_reason") or ""),
        notes=[f"convert_case={case_row['label']}"],
    )


def main():
    source_path = (
        CONVERT_ROOT
        / "result"
        / "two_stage_current_run_policy"
        / "two_stage_current_run_policy_summary.json"
    )
    source_summary = json.loads(source_path.read_text(encoding="utf-8"))

    engine = AdaptiveMeasurementEngine()
    try:
        cases = []
        for row in source_summary["cases"]:
            decision = engine.plan_two_stage_current_run_policy(
                exploratory_seed_lf_hz=float(row["exploratory_seed_lf_hz"]),
                analysis_result=_build_analysis_result(row),
            )
            cases.append(
                {
                    "label": row["label"],
                    "expected_mode": row["expected_mode"],
                    "convert_two_stage_action": row["two_stage_action"],
                    "convert_runtime_lf_hz": float(row["runtime_lf_hz"]),
                    "microprobe": decision.to_dict(),
                    "matches_action": bool(decision.action == row["two_stage_action"]),
                    "matches_measurement_mode": bool(decision.measurement_mode == row["final_mode"] + "_eis"
                                                    if row["final_mode"] in {"rapid", "normal"} else False),
                    "runtime_lf_abs_diff_hz": abs(float(row["runtime_lf_hz"]) - float(decision.runtime_lf_hz)),
                    "runtime_gap_abs_diff": abs(
                        float(row["runtime_vs_target_log10_gap"])
                        - float(decision.runtime_vs_target_log10_gap)
                    ),
                }
            )
    finally:
        engine.shutdown()

    out_dir = SCRIPT_DIR / "results" / "two_stage_current_run_policy"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "source_summary": str(source_path),
        "cases": cases,
        "aggregate": {
            "case_count": len(cases),
            "all_actions_match": all(row["matches_action"] for row in cases),
            "all_runtime_lf_match": all(row["runtime_lf_abs_diff_hz"] < 1e-12 for row in cases),
            "max_runtime_gap_abs_diff": max(
                (float(row["runtime_gap_abs_diff"]) for row in cases),
                default=0.0,
            ),
        },
        "notes": {
            "policy": (
                "Microprobe planner mirrors the saved Convert two-stage artifact: "
                "keep the exploratory CP seed for rapid/hybrid, but hand off to the "
                "PEIS-based normal LF once PEIS-only sufficiency is established."
            ),
        },
    }

    out_path = out_dir / "two_stage_current_run_policy_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved Microprobe two-stage current-run policy summary to: {out_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
