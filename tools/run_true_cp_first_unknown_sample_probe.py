# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from analysis_adapter import analyze_measurement_files, analyze_measurement_files_with_visuals
from cp_first_policy import (
    recommend_peis_lf_from_cp_txt,
    run_chunked_bias_stabilization,
    run_chunked_dv_scout,
    save_ca_txt,
)
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence
from stabilization_policy import StabilizationSettings


def _timestamp_dir(prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"{prefix}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _analysis_to_dict(result):
    if result is None:
        return None
    payload = {}
    for key, value in result.__dict__.items():
        if isinstance(value, Path):
            payload[key] = str(value)
        elif isinstance(value, (list, dict, str, int, float, bool)) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


def _visuals_to_dict(visuals):
    payload = {}
    for key, value in visuals.items():
        if isinstance(value, np.ndarray):
            if np.iscomplexobj(value):
                payload[key] = [
                    [float(v.real), float(v.imag)] for v in value.tolist()
                ]
            else:
                payload[key] = np.asarray(value).tolist()
        else:
            payload[key] = value
    return payload


def main():
    out_dir = _timestamp_dir("true_cp_first_unknown_sample_probe")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "policy": {
            "description": (
                "True CP-first probe: Vdc stabilization -> dV scout CP -> CP-only FFT LF seed -> PEIS"
            ),
            "bias_v": 0.0,
            "dv_v": 0.03,
            "stabilization_chunk_s": 5.0,
            "stabilization_max_total_s": 30.0,
            "scout_chunk_s": 5.0,
            "scout_max_total_s": 30.0,
            "peis_high_hz": 1.0e5,
            "peis_points": 20,
            "peis_amplitude_mv": 10.0,
        },
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())

        stabilization = run_chunked_bias_stabilization(
            bl,
            v_dc=0.0,
            chunk_duration_s=5.0,
            dt_record=0.5,
            max_total_s=30.0,
            settings=StabilizationSettings(
                min_hold_s=10.0,
                max_hold_s=30.0,
                extend_step_s=5.0,
                live_window_s=10.0,
                min_points_in_window=10,
                required_stable_windows=1,
            ),
        )
        stabilization_path = save_ca_txt(out_dir / "stage1_vdc_stabilization_CA.txt", stabilization.data)
        summary["stage1_vdc_stabilization"] = {
            "path": str(stabilization_path),
            **stabilization.to_dict(),
        }

        scout = run_chunked_dv_scout(
            bl,
            v_dc=0.0,
            dv=0.03,
            chunk_duration_s=5.0,
            dt_record=0.01,
            max_total_s=30.0,
        )
        scout_path = save_ca_txt(out_dir / "stage2_dv_scout_CA.txt", scout.data)
        summary["stage2_dv_scout"] = {
            "path": str(scout_path),
            **scout.to_dict(),
        }

        cp_recommendation = recommend_peis_lf_from_cp_txt(scout_path, current_in_mA=False)
        seed_lf = _safe_float(cp_recommendation["recommendation"].get("recommended_peis_lowest_freq_hz"))
        if seed_lf is None or not np.isfinite(seed_lf) or seed_lf <= 0:
            seed_lf = 1.0
        seed_lf = max(min(seed_lf, 1.0e5), 0.1)
        summary["stage3_cp_only_fft_seed"] = {
            "recommended_peis_lowest_freq_hz": seed_lf,
            "cp_saturation": cp_recommendation["cp_saturation"],
            "smoothed_duration_s": cp_recommendation["smoothed_duration_s"],
            "analysis_point_count": int(len(cp_recommendation["analysis_f"])),
        }

        peis_dir = out_dir / "stage4_peis_from_cp_seed"
        peis_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        peis_result = normal_eis_sequence(
            biologic=bl,
            v_dc=0.0,
            peis_f_high=1.0e5,
            peis_f_low=seed_lf,
            peis_npts=20,
            amplitude_mv=10.0,
            save_dir=str(peis_dir),
            label="cp_first_unknown_sample",
        )
        analysis, visuals = analyze_measurement_files_with_visuals(
            str(scout_path),
            peis_result.peis_path,
            sample_name="true_cp_first_unknown_sample",
        )
        summary["stage4_peis"] = {
            "runtime_s": time.time() - started,
            "peis_path": peis_result.peis_path,
            "rows": int(len(peis_result.eis_data)),
            "analysis": _analysis_to_dict(analysis),
            "visuals": _visuals_to_dict(visuals),
        }

        peis_only = analyze_measurement_files(
            None,
            peis_result.peis_path,
            sample_name="true_cp_first_unknown_sample_peis_only",
        )
        summary["peis_only_crosscheck"] = _analysis_to_dict(peis_only)

    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (out_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "true_cp_first_unknown_sample_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved true CP-first unknown-sample probe to: {summary_path}")


if __name__ == "__main__":
    main()
