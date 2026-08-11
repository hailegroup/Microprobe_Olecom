# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from analysis_adapter import analyze_measurement_files
from driver_biologic import BioLogicController
from stabilization_policy import StabilizationSettings, analyze_pre_peis_stability

ANALYSIS_DIR = PROJECT_DIR / "Analysis_Convert_CP_to_EIS"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import compare_full_vs_optimized_fit as OPT


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


def _run_live_guided_ca_hold(
    bl: BioLogicController,
    *,
    v_dc: float,
    max_duration_s: float,
    dt_record_s: float,
    read_interval_s: float,
    settings: StabilizationSettings,
) -> dict:
    stop_event = threading.Event()
    latest = {"assessment": None}
    rows: list[np.ndarray] = []

    def _on_segment(data, _segment):
        if data is None:
            return
        arr = np.asarray(data, dtype=float)
        if arr.size == 0:
            return
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        rows.append(arr)
        combined = np.vstack(rows)
        assessment = analyze_pre_peis_stability(
            combined[:, 0],
            combined[:, 2],
            settings=settings,
        )
        latest["assessment"] = assessment.to_dict()
        if assessment.should_start_peis:
            stop_event.set()

    started = time.time()
    data = bl.run_ca_hold(
        v_dc=v_dc,
        duration=max_duration_s,
        dt_record=dt_record_s,
        on_segment=_on_segment,
        read_interval=read_interval_s,
        stop_event=stop_event,
    )
    runtime = time.time() - started
    return {
        "data": data,
        "runtime_s": runtime,
        "stopped_by_stability": bool(stop_event.is_set()),
        "assessment": latest["assessment"],
    }


def _run_live_guided_post_cp(
    bl: BioLogicController,
    *,
    v_dc: float,
    dv_v: float,
    max_duration_s: float,
    dt_record_s: float,
    read_interval_s: float,
    settings: StabilizationSettings,
) -> dict:
    stop_event = threading.Event()
    latest = {"assessment": None}
    rows: list[np.ndarray] = []

    def _on_segment(data, _segment):
        if data is None:
            return
        arr = np.asarray(data, dtype=float)
        if arr.size == 0:
            return
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        rows.append(arr)
        combined = np.vstack(rows)
        assessment = analyze_pre_peis_stability(
            combined[:, 0],
            combined[:, 2],
            settings=settings,
        )
        latest["assessment"] = assessment.to_dict()
        if assessment.should_start_peis:
            stop_event.set()

    started = time.time()
    data = bl.run_ca_hold(
        v_dc=v_dc + dv_v,
        duration=max_duration_s,
        dt_record=dt_record_s,
        on_segment=_on_segment,
        read_interval=read_interval_s,
        stop_event=stop_event,
    )
    runtime = time.time() - started
    return {
        "data": data,
        "runtime_s": runtime,
        "stopped_by_stability": bool(stop_event.is_set()),
        "assessment": latest["assessment"],
    }


def _run_live_guided_peis(
    bl: BioLogicController,
    *,
    v_dc: float,
    f_high_hz: float,
    f_low_hz: float,
    n_pts: int,
    amplitude_mv: float,
) -> dict:
    stop_event = threading.Event()
    rows: list[np.ndarray] = []
    live_decisions: list[dict] = []

    def _on_segment(data, _segment):
        arr = np.asarray(data, dtype=float)
        if arr.size == 0:
            return
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        rows.append(arr)
        combined = np.vstack(rows)
        if len(combined) < 6:
            return
        try:
            peis_only = OPT.assess_peis_only_sufficiency(
                combined,
                np.array([], dtype=float),
                np.array([], dtype=complex),
            )
        except Exception:
            return
        current_lowest = float(np.min(combined[:, 0]))
        recommended = float(peis_only.get("recommended_peis_lowest_freq_hz") or current_lowest)
        sufficient = bool(peis_only.get("peis_only_sufficient", False))
        selection_reason = str(peis_only.get("selection_reason", "") or "")
        decision = {
            "current_lowest_freq_hz": current_lowest,
            "recommended_peis_lowest_freq_hz": recommended,
            "peis_only_sufficient": sufficient,
            "selection_reason": selection_reason,
        }
        live_decisions.append(decision)
        if sufficient and current_lowest <= (recommended * 1.05):
            stop_event.set()

    started = time.time()
    peis = bl.run_peis(
        v_dc=v_dc,
        f_high=f_high_hz,
        f_low=f_low_hz,
        n_pts=n_pts,
        amplitude_mv=amplitude_mv,
        on_segment=_on_segment,
        read_interval=1.0,
        stop_event=stop_event,
    )
    runtime = time.time() - started
    final_partial = np.vstack(rows) if rows else np.empty((0, 3))
    final_partial_assessment = None
    if len(final_partial):
        try:
            final_partial_assessment = OPT.assess_peis_only_sufficiency(
                final_partial,
                np.array([], dtype=float),
                np.array([], dtype=complex),
            )
        except Exception:
            final_partial_assessment = None
    return {
        "data": peis,
        "runtime_s": runtime,
        "stopped_by_live_normal_sufficiency": bool(stop_event.is_set()),
        "live_decisions": live_decisions,
        "final_partial_assessment": final_partial_assessment,
    }


def main():
    out_dir = _timestamp_dir("live_saturation_guided_sequence")
    settings = StabilizationSettings(
        min_hold_s=5.0,
        min_post_peis_hold_s=5.0,
        max_hold_s=120.0,
        extend_step_s=5.0,
        live_window_s=5.0,
        min_points_in_window=20,
        required_stable_windows=2,
    )
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "settings": {
            "v_dc": 0.0,
            "dv_v": 0.03,
            "peis_f_high_hz": 1e5,
            "peis_f_low_hz": 0.1,
            "peis_npts": 30,
            "amplitude_mv": 10.0,
            "max_pre_cp_s": 120.0,
            "max_post_cp_s": 120.0,
            "dt_record_s": 0.01,
            "read_interval_s": 0.1,
        },
    }

    bl = BioLogicController()
    bl.connect()
    try:
        summary["ocv_v"] = float(bl.get_ocv())

        pre = _run_live_guided_ca_hold(
            bl,
            v_dc=0.0,
            max_duration_s=120.0,
            dt_record_s=0.01,
            read_interval_s=0.1,
            settings=settings,
        )
        pre_path = out_dir / "pre_cp_live_guided.txt"
        _save_array(pre_path, pre["data"], "time/s  V/V  I/A")

        peis = _run_live_guided_peis(
            bl,
            v_dc=0.0,
            f_high_hz=1e5,
            f_low_hz=0.1,
            n_pts=30,
            amplitude_mv=10.0,
        )
        peis_path = out_dir / "guided_sequence_PEIS.txt"
        _save_array(peis_path, peis["data"], "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")

        post = _run_live_guided_post_cp(
            bl,
            v_dc=0.0,
            dv_v=0.03,
            max_duration_s=120.0,
            dt_record_s=0.01,
            read_interval_s=0.1,
            settings=settings,
        )
        post_path = out_dir / "post_cp_live_guided.txt"
        _save_array(post_path, post["data"], "time/s  V/V  I/A")

        analysis = analyze_measurement_files(str(post_path), str(peis_path), sample_name="live_saturation_guided")
        summary["pre_cp"] = {
            "runtime_s": pre["runtime_s"],
            "rows": int(len(pre["data"])),
            "stopped_by_stability": pre["stopped_by_stability"],
            "assessment": pre["assessment"],
            "path": str(pre_path),
        }
        summary["peis"] = {
            "runtime_s": peis["runtime_s"],
            "rows": int(len(peis["data"])),
            "path": str(peis_path),
            "stopped_by_live_normal_sufficiency": peis["stopped_by_live_normal_sufficiency"],
            "live_decisions": peis["live_decisions"],
            "final_partial_assessment": peis["final_partial_assessment"],
        }
        summary["post_cp"] = {
            "runtime_s": post["runtime_s"],
            "rows": int(len(post["data"])),
            "stopped_by_stability": post["stopped_by_stability"],
            "assessment": post["assessment"],
            "path": str(post_path),
        }
        summary["analysis"] = _analysis_to_dict(analysis)
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "live_saturation_guided_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved live saturation-guided sequence to: {summary_path}")


if __name__ == "__main__":
    main()
