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

from analysis_adapter import analyze_measurement_files
from driver_biologic import BioLogicController
from measurement_sequence import rapid_eis_sequence


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


def _run_stage1_cp_first(bl: BioLogicController, out_dir: Path, bias_v: float) -> dict:
    stage_dir = out_dir / "stage1_cp_first_probe"
    stage_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        dv=0.03,
        hold_time=5.0,
        post_peis_hold_time=3.0,
        peis_f_high=1e5,
        peis_f_low=1.0,
        peis_npts=20,
        ca_duration=5.0,
        ca_dt=0.01,
        save_dir=str(stage_dir),
        label="stage1_cp_first_probe",
    )
    analysis = analyze_measurement_files(result.post_ca_path, result.peis_path, sample_name="unknown_stage1_cp_first")
    return {
        "runtime_s": time.time() - started,
        "pre_ca_path": result.pre_ca_path,
        "peis_path": result.peis_path,
        "post_ca_path": result.post_ca_path,
        "rows": {
            "pre_ca": int(len(result.pre_ca_data)),
            "peis": int(len(result.eis_data)),
            "post_ca": int(len(result.ca_data)),
        },
        "analysis": _analysis_to_dict(analysis),
    }


def _run_stage2_hybrid(bl: BioLogicController, out_dir: Path, bias_v: float) -> dict:
    stage_dir = out_dir / "stage2_hybrid_followup"
    stage_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        dv=0.03,
        hold_time=5.0,
        post_peis_hold_time=3.0,
        peis_f_high=1e5,
        peis_f_low=0.1,
        peis_npts=30,
        ca_duration=20.0,
        ca_dt=0.01,
        save_dir=str(stage_dir),
        label="stage2_hybrid_followup",
    )
    analysis = analyze_measurement_files(result.post_ca_path, result.peis_path, sample_name="unknown_stage2_hybrid")
    return {
        "runtime_s": time.time() - started,
        "pre_ca_path": result.pre_ca_path,
        "peis_path": result.peis_path,
        "post_ca_path": result.post_ca_path,
        "rows": {
            "pre_ca": int(len(result.pre_ca_data)),
            "peis": int(len(result.eis_data)),
            "post_ca": int(len(result.ca_data)),
        },
        "analysis": _analysis_to_dict(analysis),
    }


def _decide_stage1(analysis: dict) -> dict:
    peis_only_sufficient = bool(analysis.get("peis_only_sufficient", False))
    recommended_peis_lf = _safe_float(analysis.get("recommended_peis_lowest_freq_hz"))
    cp_saturation_reached = analysis.get("cp_saturation_reached")
    if peis_only_sufficient and recommended_peis_lf is not None and recommended_peis_lf >= 1.0:
        return {
            "decision": "stop_after_stage1_cp_first",
            "reason": "CP-first scout already says PEIS-only is sufficient and does not require a lower LF than the scout measured",
            "cp_saturation_reached": cp_saturation_reached,
        }
    return {
        "decision": "continue_to_stage2_hybrid",
        "reason": "CP-first scout did not prove sufficiency for an unknown sample",
        "cp_saturation_reached": cp_saturation_reached,
    }


def main():
    out_dir = _timestamp_dir("unknown_sample_cp_first_policy_probe")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "policy": {
            "stage1": "cp_first_scout_pre5s_post3s_ca5s_peis_to_1Hz_20pts",
            "stage2_if_needed": "hybrid_followup_pre5s_post3s_ca20s_peis_to_0p1Hz_30pts",
        },
        "bias_v": 0.0,
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())
        stage1 = _run_stage1_cp_first(bl, out_dir, 0.0)
        summary["stage1"] = stage1
        decision = _decide_stage1(stage1["analysis"] or {})
        summary["decision"] = decision
        if decision["decision"] == "continue_to_stage2_hybrid":
            summary["stage2"] = _run_stage2_hybrid(bl, out_dir, 0.0)
        else:
            summary["stage2"] = None
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (out_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "unknown_sample_cp_first_policy_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved unknown-sample CP-first policy probe to: {summary_path}")


if __name__ == "__main__":
    main()
