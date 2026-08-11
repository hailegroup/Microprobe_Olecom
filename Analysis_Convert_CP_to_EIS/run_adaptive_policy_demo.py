# -*- coding: utf-8 -*-
"""
Small non-GUI driver for the adaptive measurement policy prototype.

This script exists only inside the analysis project so the recommendation logic
can be tested without changing the Microprobe GUI/runtime yet.
"""

from pathlib import Path
import csv
import json
import sys

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from adaptive_measurement_policy import (
    PolicySettings,
    export_policy_settings,
    propose_measurement_action,
)

OUTPUT_DIR = BASE_DIR / "result" / "adaptive_policy_demo"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_history_from_csv(csv_path: Path):
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    settings = PolicySettings()

    history = [
        {
            "electrode_id": 1,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 10,
            "optimized_peis_lowest_freq_hz": 0.12,
            "optimized_cp_duration_s": 42,
            "data_sufficient": True,
        },
        {
            "electrode_id": 2,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 10,
            "optimized_peis_lowest_freq_hz": 0.10,
            "optimized_cp_duration_s": 50,
            "data_sufficient": True,
        },
        {
            "electrode_id": 2,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 30,
            "optimized_peis_lowest_freq_hz": 0.07,
            "optimized_cp_duration_s": 65,
            "data_sufficient": True,
        },
    ]

    current_point = {
        "electrode_id": 2,
        "temperature_c": 300,
        "gas_a_sccm": 10,
        "gas_b_sccm": 20,
    }

    analysis_recommendation = {
        "recommended_peis_lowest_freq_hz": 0.10,
        "recommended_peis_conservative_cp_time_s": 45,
        "peis_only_sufficient": False,
    }

    next_point = propose_measurement_action(
        current_point=current_point,
        history=history,
        analysis_recommendation=analysis_recommendation,
        settings=settings,
    )

    remeasure = propose_measurement_action(
        current_point=current_point,
        history=history,
        analysis_recommendation=analysis_recommendation,
        current_measurement={
            "measurement_mode": "rapid_eis",
            "peis_lowest_freq_hz": 0.09,
            "cp_duration_s": 30,
            "data_sufficient": False,
        },
        settings=settings,
    )

    summary = {
        "settings": export_policy_settings(settings),
        "history": history,
        "current_point": current_point,
        "analysis_recommendation": analysis_recommendation,
        "next_point_plan": next_point,
        "remeasurement_plan": remeasure,
    }

    summary_path = OUTPUT_DIR / "adaptive_policy_demo_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Saved:")
    print(summary_path)
    print("\nNext-point plan:")
    print(json.dumps(next_point, indent=2, ensure_ascii=False))
    print("\nRemeasurement plan:")
    print(json.dumps(remeasure, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
