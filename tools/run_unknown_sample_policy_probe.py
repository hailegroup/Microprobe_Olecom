# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from analysis_adapter import analyze_measurement_files
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence, rapid_eis_sequence


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


def _run_normal_probe(bl: BioLogicController, out_dir: Path, bias_v: float) -> dict:
    started = time.time()
    probe_dir = out_dir / "stage1_normal_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1e5,
        peis_f_low=1.0,
        peis_npts=20,
        amplitude_mv=10.0,
        save_dir=str(probe_dir),
        label="stage1_normal_probe",
    )
    analysis = analyze_measurement_files(None, result.peis_path, sample_name="unknown_stage1_probe")
    return {
        "runtime_s": time.time() - started,
        "peis_path": result.peis_path,
        "rows": int(len(result.eis_data)),
        "analysis": _analysis_to_dict(analysis),
    }


def _run_hybrid_followup(bl: BioLogicController, out_dir: Path, bias_v: float) -> dict:
    started = time.time()
    followup_dir = out_dir / "stage2_hybrid_followup"
    followup_dir.mkdir(parents=True, exist_ok=True)
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
        save_dir=str(followup_dir),
        label="stage2_hybrid_followup",
    )
    analysis = analyze_measurement_files(result.post_ca_path, result.peis_path, sample_name="unknown_stage2_followup")
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
    recommended_normal_lf = _safe_float(analysis.get("recommended_normal_peis_lowest_freq_hz"))
    current_lowest = 1.0
    if peis_only_sufficient and recommended_normal_lf is not None and recommended_normal_lf >= current_lowest:
        return {
            "decision": "stop_after_stage1_normal",
            "reason": "stage1 normal probe already looks sufficient and does not ask for a lower LF than what was just measured",
        }
    return {
        "decision": "continue_to_stage2_hybrid",
        "reason": "stage1 normal probe did not prove sufficiency for an unknown sample",
    }


def main():
    out_dir = _timestamp_dir("unknown_sample_policy_probe")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "policy": {
            "stage1": "normal_only_peis_to_1Hz_20pts_10mV",
            "stage2_if_needed": "rapid_followup_pre5s_post3s_ca20s_lf0p1",
        },
        "bias_v": 0.0,
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())
        stage1 = _run_normal_probe(bl, out_dir, bias_v=0.0)
        summary["stage1"] = stage1
        decision = _decide_stage1(stage1["analysis"] or {})
        summary["decision"] = decision
        if decision["decision"] == "continue_to_stage2_hybrid":
            summary["stage2"] = _run_hybrid_followup(bl, out_dir, bias_v=0.0)
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
    summary_path = out_dir / "unknown_sample_policy_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved unknown-sample policy probe to: {summary_path}")


if __name__ == "__main__":
    main()
