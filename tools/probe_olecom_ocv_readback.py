from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_olecom_pre_scout_post_hybrid import OleComController  # noqa: E402


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_float(value, default=None):
    try:
        out = float(value)
    except Exception:
        return default
    if out != out:
        return default
    return out


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Read OLE-COM MeasureStatus repeatedly without starting/stopping a technique. "
            "Use this to validate whether Ewe/Eoc can drive OCV contact detection."
        )
    )
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1, help="User-visible 1-based channel.")
    parser.add_argument("--duration-s", type=float, default=10.0)
    parser.add_argument("--interval-s", type=float, default=0.25)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--create-if-missing", action="store_true")
    parser.add_argument("--disconnect-on-exit", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_DIR / "results" / f"olecom_ocv_readback_probe_{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "ocv_readback_samples.csv"
    summary_path = out_dir / "ocv_readback_summary.json"

    ctrl = OleComController(
        ip=args.ip,
        channel=int(args.channel),
        create_if_missing=bool(args.create_if_missing),
        trust_test_connection=True,
    )

    rows = []
    errors = []
    t0 = time.perf_counter()
    try:
        ctrl.connect()
        next_t = time.perf_counter()
        while time.perf_counter() - t0 < float(args.duration_s):
            now = time.perf_counter()
            if now < next_t:
                time.sleep(max(0.0, next_t - now))
            poll_t = time.perf_counter()
            wall = datetime.now().isoformat(timespec="milliseconds")
            try:
                status = ctrl.status_dict()
                row = {
                    "wall_time": wall,
                    "elapsed_wall_s": poll_t - t0,
                    "status": status.get("Status"),
                    "technique_number": status.get("Technique number"),
                    "sequence_number": status.get("Sequence number"),
                    "buffer_size": status.get("Buffer size"),
                    "ec_lab_time_s": status.get("Time"),
                    "ewe_v": status.get("Ewe"),
                    "eoc_v": status.get("Eoc"),
                    "ece_v": status.get("Ece"),
                    "current_a": status.get("I"),
                    "irange": status.get("Irange"),
                    "point_index": status.get("Current point index"),
                    "total_point_index": status.get("Total point index"),
                    "connection": status.get("Connection"),
                    "result_code": status.get("Result code"),
                    "raw": json.dumps(status.get("_raw"), default=str),
                    "error": "",
                }
            except Exception as exc:
                row = {
                    "wall_time": wall,
                    "elapsed_wall_s": poll_t - t0,
                    "status": "",
                    "technique_number": "",
                    "sequence_number": "",
                    "buffer_size": "",
                    "ec_lab_time_s": "",
                    "ewe_v": "",
                    "eoc_v": "",
                    "ece_v": "",
                    "current_a": "",
                    "irange": "",
                    "point_index": "",
                    "total_point_index": "",
                    "connection": "",
                    "result_code": "",
                    "raw": "",
                    "error": repr(exc),
                }
                errors.append(repr(exc))
            rows.append(row)
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            next_t = poll_t + float(args.interval_s)
    finally:
        if bool(args.disconnect_on_exit):
            try:
                ctrl.disconnect()
            except Exception:
                pass

    elapsed = [float(r["elapsed_wall_s"]) for r in rows]
    intervals = np.diff(elapsed) if len(elapsed) > 1 else np.asarray([], dtype=float)
    ewe = np.asarray([_safe_float(r.get("ewe_v")) for r in rows if _safe_float(r.get("ewe_v")) is not None], dtype=float)
    eoc = np.asarray([_safe_float(r.get("eoc_v")) for r in rows if _safe_float(r.get("eoc_v")) is not None], dtype=float)
    summary = {
        "output_dir": str(out_dir),
        "csv_path": str(csv_path),
        "samples": len(rows),
        "errors": errors[-10:],
        "requested_interval_s": float(args.interval_s),
        "duration_s": float(args.duration_s),
        "median_actual_interval_s": float(np.nanmedian(intervals)) if intervals.size else None,
        "p95_actual_interval_s": float(np.nanpercentile(intervals, 95)) if intervals.size else None,
        "min_actual_interval_s": float(np.nanmin(intervals)) if intervals.size else None,
        "max_actual_interval_s": float(np.nanmax(intervals)) if intervals.size else None,
        "ewe_mean_v": float(np.nanmean(ewe)) if ewe.size else None,
        "ewe_std_v": float(np.nanstd(ewe)) if ewe.size else None,
        "eoc_mean_v": float(np.nanmean(eoc)) if eoc.size else None,
        "eoc_std_v": float(np.nanstd(eoc)) if eoc.size else None,
        "last_row": rows[-1] if rows else None,
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
