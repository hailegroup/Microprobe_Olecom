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

import easy_biologic.base_programs as ebp

from driver_biologic import BioLogicController


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


def _infer_median_dt(time_values):
    arr = np.asarray(time_values, dtype=float)
    if arr.size < 3:
        return None
    diffs = np.diff(arr)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return None
    return float(np.median(diffs))


def _summarize_dc(parsed):
    if parsed is None or len(parsed) == 0:
        return {"rows": 0}
    parsed = np.asarray(parsed, dtype=float)
    first_window = parsed[parsed[:, 0] <= 10.0]
    second_window = parsed[parsed[:, 0] > 10.0]
    return {
        "rows": int(len(parsed)),
        "first_window_rows": int(len(first_window)),
        "second_window_rows": int(len(second_window)),
        "first_window_median_dt_s": _infer_median_dt(first_window[:, 0]) if len(first_window) else None,
        "second_window_median_dt_s": _infer_median_dt(second_window[:, 0]) if len(second_window) else None,
        "overall_median_dt_s": _infer_median_dt(parsed[:, 0]),
    }


def main():
    out_dir = _timestamp_dir("variable_dt_ca_probe")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "requested": {
            "voltages": [0.0, 0.0],
            "durations_s": [10.0, 10.0],
            "dt_s": [0.01, 0.1],
        },
        "attempts": [],
    }

    bl = BioLogicController()
    bl.connect()
    try:
        ch_idx = 0
        def run_low_level_attempt(name, time_interval):
            attempt = {"name": name, "time_interval": time_interval}
            flat_params = {
                "voltages": [0.0, 0.0],
                "durations": [10.0, 10.0],
                "vs_initial": False,
                "time_interval": time_interval,
                "current_interval": 0.001,
            }
            started = time.time()
            try:
                prog = ebp.CA(bl.dev, flat_params, channels=[ch_idx], stop_event=None)
                if not hasattr(prog, "channel"):
                    prog.channel = ch_idx
                technique, low_level_params = bl._build_low_level_params(ebp.CA, prog.params)
                prog._run(
                    technique,
                    low_level_params,
                    read_interval=0.1,
                    retrieve_data=True,
                )
                raw = prog.data.get(ch_idx, [])
                parsed = bl._parse_dc(raw)
                out_path = out_dir / f"{name}.txt"
                _save_array(out_path, parsed, "time/s  V/V  I/A")
                attempt["runtime_s"] = time.time() - started
                attempt["data_path"] = str(out_path)
                attempt["observed"] = _summarize_dc(parsed)
                attempt["error"] = None
            except Exception as exc:
                attempt["runtime_s"] = time.time() - started
                attempt["error"] = f"{type(exc).__name__}: {exc}"
                (out_dir / f"{name}_exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
            summary["attempts"].append(attempt)

        run_low_level_attempt("single_program_variable_dt", [0.01, 0.1])
        run_low_level_attempt("single_program_scalar_dt_0p01", 0.01)

        split_attempt = {"name": "split_programs_variable_dt"}
        started = time.time()
        try:
            first = bl.run_ca_hold(0.0, 10.0, dt_record=0.01, read_interval=0.1)
            second = bl.run_ca_hold(0.0, 10.0, dt_record=0.1, read_interval=0.1)
            stitched = []
            if len(first):
                stitched.append(np.asarray(first, dtype=float))
            if len(second):
                second_arr = np.asarray(second, dtype=float).copy()
                second_arr[:, 0] += 10.0
                stitched.append(second_arr)
            stitched_arr = np.vstack(stitched) if stitched else np.empty((0, 3))
            out_path = out_dir / "split_programs_variable_dt.txt"
            _save_array(out_path, stitched_arr, "time/s  V/V  I/A")
            split_attempt["runtime_s"] = time.time() - started
            split_attempt["data_path"] = str(out_path)
            split_attempt["observed"] = _summarize_dc(stitched_arr)
            split_attempt["error"] = None
        except Exception as exc:
            split_attempt["runtime_s"] = time.time() - started
            split_attempt["error"] = f"{type(exc).__name__}: {exc}"
            (out_dir / "split_programs_variable_dt_exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        summary["attempts"].append(split_attempt)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (out_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "variable_dt_ca_probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved variable-dt CA probe to: {summary_path}")


if __name__ == "__main__":
    main()
