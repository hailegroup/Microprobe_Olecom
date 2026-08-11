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


def _save_array(path: Path, data, header: str):
    arr = np.asarray(data)
    if arr.size == 0:
        return
    np.savetxt(path, arr, header=header, comments="")


def _analysis_to_dict(result):
    if result is None:
        return None
    payload = {}
    for key, value in result.__dict__.items():
        if isinstance(value, np.generic):
            payload[key] = value.item()
        elif isinstance(value, (list, dict, str, int, float, bool)) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


def _run_cp_first_then_peis(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float) -> dict:
    pre = bl.run_ca_hold(bias_v, 5.0, dt_record=0.02, read_interval=0.1)
    peis = bl.run_peis(bias_v, 1e5, 1.0, 15, amplitude_mv=10.0, read_interval=1.0)
    ts = time.strftime("%Y%m%d_%H%M%S")
    pre_path = exp_dir / f"{label}_preCA_{ts}.txt"
    peis_path = exp_dir / f"{label}_PEIS_{ts}.txt"
    _save_array(pre_path, pre, "time/s  V/V  I/A")
    _save_array(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")
    analysis = analyze_measurement_files(str(pre_path), str(peis_path), sample_name=label)
    return {
        "pre_ca_path": str(pre_path),
        "peis_path": str(peis_path),
        "post_ca_path": None,
        "analysis": _analysis_to_dict(analysis),
        "rows": {"pre_ca": int(len(pre)), "peis": int(len(peis))},
    }


def _run_peis_then_post(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float) -> dict:
    peis = bl.run_peis(bias_v, 1e5, 1.0, 15, amplitude_mv=10.0, read_interval=1.0)
    post = bl.run_ca_hold(bias_v + 0.03, 5.0, dt_record=0.02, read_interval=0.1)
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
        "rows": {"post_ca": int(len(post)), "peis": int(len(peis))},
    }


def _run_one(bl: BioLogicController, out_dir: Path, architecture: str, bias_v: float) -> dict:
    label = f"{architecture}_V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    exp_dir = out_dir / label
    exp_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    summary = {
        "label": label,
        "architecture": architecture,
        "bias_v": bias_v,
    }
    try:
        if architecture == "normal":
            result = normal_eis_sequence(
                biologic=bl,
                v_dc=bias_v,
                peis_f_high=1e5,
                peis_f_low=1.0,
                peis_npts=15,
                amplitude_mv=10.0,
                save_dir=str(exp_dir),
                label=label,
            )
            analysis = analyze_measurement_files(None, result.peis_path, sample_name=label)
            summary.update(
                {
                    "pre_ca_path": None,
                    "peis_path": result.peis_path,
                    "post_ca_path": None,
                    "analysis": _analysis_to_dict(analysis),
                    "rows": {"peis": int(len(result.eis_data))},
                }
            )
        elif architecture == "rapid":
            result = rapid_eis_sequence(
                biologic=bl,
                v_dc=bias_v,
                dv=0.03,
                hold_time=3.0,
                post_peis_hold_time=2.0,
                peis_f_high=1e5,
                peis_f_low=1.0,
                peis_npts=15,
                ca_duration=5.0,
                ca_dt=0.02,
                save_dir=str(exp_dir),
                label=label,
            )
            analysis = analyze_measurement_files(result.post_ca_path, result.peis_path, sample_name=label)
            summary.update(
                {
                    "pre_ca_path": result.pre_ca_path,
                    "peis_path": result.peis_path,
                    "post_ca_path": result.post_ca_path,
                    "analysis": _analysis_to_dict(analysis),
                    "rows": {
                        "pre_ca": int(len(result.pre_ca_data)),
                        "post_ca": int(len(result.ca_data)),
                        "peis": int(len(result.eis_data)),
                    },
                }
            )
        elif architecture == "cp_first":
            summary.update(_run_cp_first_then_peis(bl, exp_dir, label, bias_v))
        elif architecture == "peis_first":
            summary.update(_run_peis_then_post(bl, exp_dir, label, bias_v))
        else:
            raise ValueError(f"Unknown architecture: {architecture}")
        summary["error"] = None
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    summary["runtime_s"] = time.time() - started
    (exp_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    out_dir = _timestamp_dir("sequence_voltage_smoke_matrix")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "biases_v": [-0.1, 0.0, 0.1],
        "architectures": ["normal", "rapid", "cp_first", "peis_first"],
        "experiments": [],
    }
    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = float(bl.get_ocv())
        for bias_v in summary["biases_v"]:
            for architecture in summary["architectures"]:
                result = _run_one(bl, out_dir, architecture, bias_v)
                summary["experiments"].append(result)
                (out_dir / "matrix_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "matrix_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved sequence/voltage smoke matrix to: {out_dir}")


if __name__ == "__main__":
    main()
