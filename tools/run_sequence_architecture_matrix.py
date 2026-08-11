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


def _to_path_str(value):
    return None if value is None else str(value)


def _analysis_to_dict(result):
    if result is None:
        return None
    payload = {}
    for key, value in result.__dict__.items():
        if isinstance(value, Path):
            payload[key] = str(value)
        elif isinstance(value, np.generic):
            payload[key] = value.item()
        elif isinstance(value, (list, dict, str, int, float, bool)) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


def _save_array(path: Path, data, header: str):
    if data is None:
        return
    arr = np.asarray(data)
    if arr.size == 0:
        return
    np.savetxt(path, arr, header=header, comments="")


def _run_cp_first_then_peis(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    label = spec["key"]
    pre = bl.run_ca_hold(
        spec["v_dc"],
        spec["cp_first_duration_s"],
        dt_record=spec["ca_dt_s"],
        read_interval=0.1,
    )
    peis = bl.run_peis(
        spec["v_dc"],
        spec["peis_f_high_hz"],
        spec["peis_f_low_hz"],
        spec["peis_npts"],
        amplitude_mv=spec["amplitude_mv"],
        read_interval=1.0,
    )
    ts = time.strftime("%Y%m%d_%H%M%S")
    pre_path = exp_dir / f"{label}_cpfirst_CA_{ts}.txt"
    peis_path = exp_dir / f"{label}_PEIS_{ts}.txt"
    _save_array(pre_path, pre, "time/s  V/V  I/A")
    _save_array(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")
    analysis = analyze_measurement_files(str(pre_path), str(peis_path), sample_name=label)
    return {
        "pre_ca_path": str(pre_path),
        "peis_path": str(peis_path),
        "post_ca_path": None,
        "analysis": _analysis_to_dict(analysis),
        "rows": {
            "pre_ca_rows": int(len(pre)),
            "peis_rows": int(len(peis)),
        },
    }


def _run_peis_then_cp(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    label = spec["key"]
    peis = bl.run_peis(
        spec["v_dc"],
        spec["peis_f_high_hz"],
        spec["peis_f_low_hz"],
        spec["peis_npts"],
        amplitude_mv=spec["amplitude_mv"],
        read_interval=1.0,
    )
    post = bl.run_ca_hold(
        spec["v_dc"] + spec.get("post_cp_dv_v", 0.03),
        spec["post_cp_duration_s"],
        dt_record=spec["ca_dt_s"],
        read_interval=0.1,
    )
    ts = time.strftime("%Y%m%d_%H%M%S")
    peis_path = exp_dir / f"{label}_PEIS_{ts}.txt"
    post_path = exp_dir / f"{label}_postCA_{ts}.txt"
    _save_array(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")
    _save_array(post_path, post, "time/s  V/V  I/A")
    analysis = analyze_measurement_files(str(post_path), str(peis_path), sample_name=label)
    return {
        "pre_ca_path": None,
        "peis_path": str(peis_path),
        "post_ca_path": str(post_path),
        "analysis": _analysis_to_dict(analysis),
        "rows": {
            "post_ca_rows": int(len(post)),
            "peis_rows": int(len(peis)),
        },
    }


def _run_case(bl: BioLogicController, out_dir: Path, spec: dict) -> dict:
    exp_dir = out_dir / spec["key"]
    exp_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    error = None
    result_payload = None

    try:
        if spec["architecture"] == "normal_only":
            result = normal_eis_sequence(
                biologic=bl,
                v_dc=spec["v_dc"],
                peis_f_high=spec["peis_f_high_hz"],
                peis_f_low=spec["peis_f_low_hz"],
                peis_npts=spec["peis_npts"],
                amplitude_mv=spec["amplitude_mv"],
                save_dir=str(exp_dir),
                label=spec["key"],
            )
            analysis = analyze_measurement_files(None, result.peis_path, sample_name=spec["key"])
            result_payload = {
                "pre_ca_path": None,
                "peis_path": _to_path_str(result.peis_path),
                "post_ca_path": None,
                "analysis": _analysis_to_dict(analysis),
                "rows": {"peis_rows": int(len(result.eis_data))},
            }
        elif spec["architecture"] == "rapid_pre_peis_post":
            result = rapid_eis_sequence(
                biologic=bl,
                v_dc=spec["v_dc"],
                dv=spec["dv_v"],
                hold_time=spec["hold_time_s"],
                post_peis_hold_time=spec["post_hold_time_s"],
                peis_f_high=spec["peis_f_high_hz"],
                peis_f_low=spec["peis_f_low_hz"],
                peis_npts=spec["peis_npts"],
                ca_duration=spec["ca_duration_s"],
                ca_dt=spec["ca_dt_s"],
                save_dir=str(exp_dir),
                label=spec["key"],
            )
            analysis = analyze_measurement_files(result.post_ca_path, result.peis_path, sample_name=spec["key"])
            result_payload = {
                "pre_ca_path": _to_path_str(result.pre_ca_path),
                "peis_path": _to_path_str(result.peis_path),
                "post_ca_path": _to_path_str(result.post_ca_path),
                "analysis": _analysis_to_dict(analysis),
                "rows": {
                    "pre_ca_rows": int(len(result.pre_ca_data)),
                    "post_ca_rows": int(len(result.ca_data)),
                    "peis_rows": int(len(result.eis_data)),
                },
            }
        elif spec["architecture"] == "cp_first_then_peis":
            result_payload = _run_cp_first_then_peis(bl, exp_dir, spec)
        elif spec["architecture"] == "peis_then_post_cp":
            result_payload = _run_peis_then_cp(bl, exp_dir, spec)
        else:
            raise ValueError(f"Unknown architecture: {spec['architecture']}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")

    finished = time.time()
    summary = {
        "key": spec["key"],
        "architecture": spec["architecture"],
        "parameters": spec,
        "runtime_s": finished - started,
        "error": error,
    }
    if result_payload:
        summary.update(result_payload)
    (exp_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    out_dir = _timestamp_dir("sequence_architecture_matrix")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "ocv_v": None,
        "experiments": [],
    }

    experiments = [
        {
            "key": "normal_only_lf1p0",
            "architecture": "normal_only",
            "v_dc": 0.0,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "key": "normal_only_lf0p1",
            "architecture": "normal_only",
            "v_dc": 0.0,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 0.1,
            "peis_npts": 30,
            "amplitude_mv": 10.0,
        },
        {
            "key": "rapid_pre_peis_post_lf1p0",
            "architecture": "rapid_pre_peis_post",
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
            "key": "rapid_pre_peis_post_lf0p1",
            "architecture": "rapid_pre_peis_post",
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
        {
            "key": "cp_first_then_peis_lf1p0",
            "architecture": "cp_first_then_peis",
            "v_dc": 0.0,
            "cp_first_duration_s": 23.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "key": "cp_first_then_peis_lf0p1",
            "architecture": "cp_first_then_peis",
            "v_dc": 0.0,
            "cp_first_duration_s": 23.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 0.1,
            "peis_npts": 30,
            "amplitude_mv": 10.0,
        },
        {
            "key": "peis_then_post_cp_lf1p0",
            "architecture": "peis_then_post_cp",
            "v_dc": 0.0,
            "post_cp_dv_v": 0.03,
            "post_cp_duration_s": 23.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "key": "peis_then_post_cp_lf0p1",
            "architecture": "peis_then_post_cp",
            "v_dc": 0.0,
            "post_cp_dv_v": 0.03,
            "post_cp_duration_s": 23.0,
            "ca_dt_s": 0.01,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 0.1,
            "peis_npts": 30,
            "amplitude_mv": 10.0,
        },
    ]

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())
        for experiment in experiments:
            exp_summary = _run_case(bl, out_dir, experiment)
            summary["experiments"].append(exp_summary)
            (out_dir / "matrix_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "matrix_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved sequence architecture matrix to: {out_dir}")


if __name__ == "__main__":
    main()
