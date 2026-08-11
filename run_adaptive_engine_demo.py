# -*- coding: utf-8 -*-
"""
Headless dry-run simulation for the adaptive measurement engine.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from adaptive_engine import AdaptiveMeasurementEngine
from adaptive_types import MeasurementExecution, MeasurementFiles, PointMetadata
from stabilization_policy import StabilizationSettings


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "result" / "adaptive_engine_demo"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _fake_analysis_factory(result, delay_s):
    def _fake_analyzer(*, dc_path, eis_path, sample_name):
        time.sleep(delay_s)
        return result

    return _fake_analyzer


def _fake_full_processor_factory(delay_s):
    def _fake_processor(*, dc_path, eis_path, sample_name):
        time.sleep(delay_s)
        return {
            "status": "completed",
            "sample_name": sample_name,
            "excel_path": f"demo_output/{sample_name}.xlsx",
            "output_dir": f"demo_output/{sample_name}",
            "processing_latency_s": delay_s,
            "fit_summary": {"demo": "ok"},
            "notes": ["demo full processing finished in the background"],
        }

    return _fake_processor


def main():
    from adaptive_types import AnalysisResult

    engine = AdaptiveMeasurementEngine(
        analyzer=_fake_analysis_factory(
            AnalysisResult(
                recommended_peis_lowest_freq_hz=0.08,
                recommended_peis_conservative_cp_time_s=55.0,
                data_sufficient=True,
                peis_only_sufficient=True,
                confidence=0.82,
                reason="demo analysis completed quickly enough for the immediate next point",
                notes=["demo result"],
            ),
            delay_s=0.4,
        ),
        full_processor=_fake_full_processor_factory(1.5),
        stabilization_settings=StabilizationSettings(min_points_in_window=4, required_stable_windows=2, live_window_s=40.0),
    )

    p1 = PointMetadata(
        point_id="pt001",
        label="T300_GA10_GB10_E1_V0.00",
        electrode_id=1,
        temperature_c=300,
        gas_a_sccm=10,
        gas_b_sccm=10,
        voltage_v=0.0,
        sequence_index=1,
    )
    m1 = MeasurementExecution(
        measurement_mode="rapid_eis",
        peis_lowest_freq_hz=0.10,
        cp_duration_s=60.0,
        planned_pre_peis_hold_s=80.0,
        planned_post_peis_hold_s=20.0,
        actual_peis_high_freq_hz=1e5,
    )
    f1 = MeasurementFiles(
        pre_ca_path="demo_pre_ca.txt",
        peis_path="demo_peis.txt",
        post_ca_path="demo_post_ca.txt",
    )
    engine.register_point(p1, m1, f1)
    engine.start_post_measurement_pipeline("pt001")
    finalize_1 = engine.finalize_completed_point("pt001", timeout_s=1.0)

    p2 = PointMetadata(
        point_id="pt002",
        label="T300_GA10_GB10_E2_V0.00",
        electrode_id=2,
        temperature_c=300,
        gas_a_sccm=10,
        gas_b_sccm=10,
        voltage_v=0.0,
        sequence_index=2,
    )
    next_point_recommendation = engine.recommend_next_point(p2)
    stability_time = [0, 20, 40, 60, 80, 100, 120]
    stability_current = [8.0e-6, 4.0e-6, 2.1e-6, 1.3e-6, 1.0e-6, 0.98e-6, 0.97e-6]
    stability_assessment = engine.assess_pre_peis_stabilization(
        "pt001",
        time_s=stability_time,
        current_a=stability_current,
        peis_already_started=False,
    )
    sweep_order = engine.recommend_bias_sweep_order(
        bias_values_v=[0.3, 0.25, 0.2, 0.15, 0.1, 0.05, 0.0, -0.05],
        condition_context={
            "electrode_id": 2,
            "temperature_c": 300,
            "gas_a_sccm": 10,
            "gas_b_sccm": 10,
        },
    )
    time.sleep(1.7)
    engine.poll_ready_full_processing()

    delayed_engine = AdaptiveMeasurementEngine(
        analyzer=_fake_analysis_factory(
            AnalysisResult(
                recommended_peis_lowest_freq_hz=0.006,
                recommended_peis_conservative_cp_time_s=110.0,
                data_sufficient=False,
                peis_only_sufficient=False,
                confidence=0.44,
                reason="demo analysis finished late and suggests a retry",
                notes=["late analysis demo result"],
            ),
            delay_s=2.5,
        ),
        full_processor=_fake_full_processor_factory(3.0),
        stabilization_settings=StabilizationSettings(min_points_in_window=4, required_stable_windows=2, live_window_s=40.0),
    )
    delayed_engine.register_point(p1, m1, f1)
    delayed_engine.start_post_measurement_pipeline("pt001")
    finalize_2 = delayed_engine.finalize_completed_point("pt001", timeout_s=0.2)
    fallback_recommendation = delayed_engine.recommend_next_point(p2)
    time.sleep(2.7)
    delayed_engine.poll_ready_analyses()
    delayed_engine.poll_ready_full_processing()
    late_remeasurements = [req.to_dict() for req in delayed_engine.collect_pending_remeasurements()]

    summary = {
        "fast_analysis_case": {
            "finalize": finalize_1,
            "next_point_recommendation": next_point_recommendation.to_dict(),
            "stability_assessment": stability_assessment.to_dict(),
            "sweep_order": sweep_order,
            "queue_status": engine.get_background_queue_status(),
            "engine_state": engine.export_state(),
        },
        "delayed_analysis_case": {
            "finalize": finalize_2,
            "fallback_recommendation": fallback_recommendation.to_dict(),
            "late_remeasurements": late_remeasurements,
            "queue_status": delayed_engine.get_background_queue_status(),
            "engine_state": delayed_engine.export_state(),
        },
    }

    output_path = OUTPUT_DIR / "adaptive_engine_demo_summary.json"
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
