# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
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
from measurement_sequence import normal_eis_sequence


def _timestamp_dir(prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"{prefix}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


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


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _run_one(bl: BioLogicController, out_dir: Path, bias_v: float, *, f_low_hz: float, n_pts: int, amplitude_mv: float, tag: str) -> dict:
    key = f"{tag}_V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    exp_dir = out_dir / key
    exp_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    error = None
    result = None
    analysis = None
    try:
        result = normal_eis_sequence(
            biologic=bl,
            v_dc=bias_v,
            peis_f_high=1e5,
            peis_f_low=f_low_hz,
            peis_npts=n_pts,
            amplitude_mv=amplitude_mv,
            save_dir=str(exp_dir),
            label=key,
        )
        analysis = analyze_measurement_files(
            None,
            result.peis_path,
            sample_name=key,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    summary = {
        "key": key,
        "bias_v": bias_v,
        "runtime_s": time.time() - started,
        "error": error,
        "parameters": {
            "mode": "normal_only_optimized_candidate",
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": f_low_hz,
            "peis_npts": n_pts,
            "amplitude_mv": amplitude_mv,
        },
        "peis_path": getattr(result, "peis_path", None) if result is not None else None,
        "rows": int(len(result.eis_data)) if result is not None else 0,
        "analysis": _analysis_to_dict(analysis),
    }
    (exp_dir / "experiment_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lf", type=float, default=6.0)
    parser.add_argument("--npts", type=int, default=20)
    parser.add_argument("--amplitude-mv", type=float, default=10.0)
    parser.add_argument("--tag", type=str, default="normal_optimized")
    args = parser.parse_args()

    out_dir = _timestamp_dir(f"{args.tag}_confirmation_matrix")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "strategy": {
            "description": "normal-only PEIS candidate derived from current normal-favored sample",
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": args.lf,
            "peis_npts": args.npts,
            "amplitude_mv": args.amplitude_mv,
            "tag": args.tag,
        },
        "biases_v": [-0.1, 0.0, 0.1],
        "experiments": [],
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = _safe_float(bl.get_ocv())
        for bias_v in summary["biases_v"]:
            exp_summary = _run_one(
                bl,
                out_dir,
                bias_v,
                f_low_hz=args.lf,
                n_pts=args.npts,
                amplitude_mv=args.amplitude_mv,
                tag=args.tag,
            )
            summary["experiments"].append(exp_summary)
            (out_dir / "confirmation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (out_dir / "confirmation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved optimized normal confirmation matrix to: {out_dir}")


if __name__ == "__main__":
    main()
