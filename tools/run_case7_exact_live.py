from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONVERT_DIR = PROJECT_DIR.parent / "Convert_CP_to_EIS 1"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(CONVERT_DIR) not in sys.path:
    sys.path.insert(0, str(CONVERT_DIR))

from driver_biologic import BioLogicController
from analysis_adapter import analyze_measurement_files_with_visuals


def _timestamp_dir(prefix: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT_DIR / "results" / f"{prefix}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _save_txt(path: Path, data, header: str) -> None:
    arr = np.asarray(data, dtype=float)
    np.savetxt(path, arr, header=header, comments="")


def main() -> None:
    out_dir = _timestamp_dir("case7_exact_live")
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "settings": {
            "ca_voltages_v": [0.100, 0.120],
            "ca_durations_s": [120.0, 300.0],
            "ca_dt_s": 0.1,
            "peis_f_high_hz": 7.0e6,
            "peis_f_low_hz": 0.05,
            "peis_npts": 160,
            "peis_amplitude_mv": 20.0,
        },
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_before_v"] = float(bl.get_ocv())

        ca = bl.run_ca_sequence(
            [0.100, 0.120],
            [120.0, 300.0],
            dt_record=0.1,
            channel=1,
            read_interval=0.1,
        )
        ca_path = out_dir / "case7_exact_live_CA.txt"
        _save_txt(ca_path, ca, "time/s  V/V  I/A")

        peis = bl.run_peis(
            0.100,
            f_high=7.0e6,
            f_low=0.05,
            n_pts=160,
            amplitude_mv=20.0,
            channel=1,
            read_interval=1.0,
        )
        peis_path = out_dir / "case7_exact_live_PEIS.txt"
        _save_txt(peis_path, peis, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")

        try:
            analysis_result, visuals = analyze_measurement_files_with_visuals(
                str(ca_path),
                str(peis_path),
                sample_name="case7_exact_live",
            )
            summary["analysis_result"] = dict(analysis_result.__dict__)
            summary["visuals"] = dict(visuals)
        except Exception as exc:
            summary["analysis_error"] = f"{type(exc).__name__}: {exc}"

        summary["rows"] = {
            "ca_rows": int(len(np.asarray(ca))),
            "peis_rows": int(len(np.asarray(peis))),
        }

        try:
            summary["ocv_after_v"] = float(bl.get_ocv())
        except Exception:
            summary["ocv_after_v"] = None
    finally:
        bl.disconnect()

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "case7_exact_live_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(str(summary_path))


if __name__ == "__main__":
    main()
