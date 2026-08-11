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


def _run_experiment(bl: BioLogicController, out_dir: Path, experiment: dict) -> dict:
    exp_dir = out_dir / experiment["key"]
    exp_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = None
    analysis = None
    error = None

    try:
        if experiment["mode"] == "normal":
            result = normal_eis_sequence(
                biologic=bl,
                v_dc=experiment["v_dc"],
                peis_f_high=experiment["peis_f_high_hz"],
                peis_f_low=experiment["peis_f_low_hz"],
                peis_npts=experiment["peis_npts"],
                amplitude_mv=experiment["amplitude_mv"],
                save_dir=str(exp_dir),
                label=experiment["key"],
            )
            if result.peis_path:
                analysis = analyze_measurement_files(
                    None,
                    result.peis_path,
                    sample_name=experiment["key"],
                )
        else:
            result = rapid_eis_sequence(
                biologic=bl,
                v_dc=experiment["v_dc"],
                dv=experiment["dv_v"],
                hold_time=experiment["hold_time_s"],
                post_peis_hold_time=experiment["post_hold_time_s"],
                peis_f_high=experiment["peis_f_high_hz"],
                peis_f_low=experiment["peis_f_low_hz"],
                peis_npts=experiment["peis_npts"],
                ca_duration=experiment["ca_duration_s"],
                ca_dt=experiment["ca_dt_s"],
                save_dir=str(exp_dir),
                label=experiment["key"],
            )
            if result.peis_path and result.post_ca_path:
                analysis = analyze_measurement_files(
                    result.post_ca_path,
                    result.peis_path,
                    sample_name=experiment["key"],
                )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")

    finished = time.time()
    summary = {
        "key": experiment["key"],
        "mode": experiment["mode"],
        "parameters": experiment,
        "runtime_s": finished - started,
        "error": error,
        "files": {
            "pre_ca_path": getattr(result, "pre_ca_path", None) if result is not None else None,
            "peis_path": getattr(result, "peis_path", None) if result is not None else None,
            "post_ca_path": getattr(result, "post_ca_path", None) if result is not None else None,
        },
        "analysis": _analysis_to_dict(analysis),
    }
    (exp_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    out_dir = _timestamp_dir("normal_candidate_baseline_bundle")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "ocv_v": None,
        "experiments": [],
    }

    experiments = [
        {
            "key": "normal_lf1p0_v0p000",
            "mode": "normal",
            "v_dc": 0.0,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "key": "rapid_lf1p0_shortcp_v0p000",
            "mode": "rapid",
            "v_dc": 0.0,
            "dv_v": 0.03,
            "hold_time_s": 5.0,
            "post_hold_time_s": 3.0,
            "ca_duration_s": 20.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 1.0,
            "peis_npts": 20,
        },
        {
            "key": "rapid_lf0p1_shortcp_v0p000",
            "mode": "rapid",
            "v_dc": 0.0,
            "dv_v": 0.03,
            "hold_time_s": 5.0,
            "post_hold_time_s": 3.0,
            "ca_duration_s": 20.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 0.1,
            "peis_npts": 30,
        },
    ]

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())
        for experiment in experiments:
            exp_summary = _run_experiment(bl, out_dir, experiment)
            summary["experiments"].append(exp_summary)
            (out_dir / "bundle_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "bundle_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved baseline bundle to: {out_dir}")


if __name__ == "__main__":
    main()
