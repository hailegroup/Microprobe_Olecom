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

from analysis_adapter import analyze_measurement_files, analyze_measurement_files_with_visuals
from cp_first_policy import (
    recommend_peis_lf_from_cp_txt,
    run_chunked_bias_stabilization,
    run_chunked_dv_scout,
    save_ca_txt,
)
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence, rapid_eis_sequence
from stabilization_policy import StabilizationSettings


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
        elif isinstance(value, np.generic):
            payload[key] = value.item()
        elif isinstance(value, (list, dict, str, int, float, bool)) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


def _visuals_to_dict(visuals):
    payload = {}
    for key, value in visuals.items():
        if isinstance(value, np.ndarray):
            if np.iscomplexobj(value):
                payload[key] = [[float(v.real), float(v.imag)] for v in value.tolist()]
            else:
                payload[key] = np.asarray(value).tolist()
        else:
            payload[key] = value
    return payload


def _json_safe(value):
    if isinstance(value, np.ndarray):
        if np.iscomplexobj(value):
            return [[float(v.real), float(v.imag)] for v in value.tolist()]
        return np.asarray(value).tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _ensure_connected(bl: BioLogicController) -> None:
    try:
        if bl.dev is not None:
            bl.get_live_values()
            return
    except Exception:
        try:
            bl.disconnect()
        except Exception:
            pass
    bl.connect()


def _safe_ocv(bl: BioLogicController):
    try:
        _ensure_connected(bl)
        return _safe_float(bl.get_ocv())
    except Exception:
        return None


def _safe_live(bl: BioLogicController):
    try:
        _ensure_connected(bl)
        return bl.get_live_values()
    except Exception:
        return None


def _run_normal_only(bl: BioLogicController, exp_dir: Path, bias_v: float) -> dict:
    label = "normal_only"
    started = time.time()
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1.0e5,
        peis_f_low=1.0,
        peis_npts=20,
        amplitude_mv=10.0,
        save_dir=str(exp_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        None,
        result.peis_path,
        sample_name=f"{label}_{bias_v:+0.3f}V",
    )
    return {
        "protocol": label,
        "runtime_s": time.time() - started,
        "peis_path": result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {"peis_rows": int(len(result.eis_data))},
    }


def _run_rapid_pre_peis_post(bl: BioLogicController, exp_dir: Path, bias_v: float) -> dict:
    label = "rapid_pre_peis_post"
    started = time.time()
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        dv=0.03,
        hold_time=5.0,
        post_peis_hold_time=3.0,
        peis_f_high=1.0e5,
        peis_f_low=1.0,
        peis_npts=20,
        ca_duration=10.0,
        ca_dt=0.02,
        save_dir=str(exp_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        result.post_ca_path,
        result.peis_path,
        sample_name=f"{label}_{bias_v:+0.3f}V",
    )
    return {
        "protocol": label,
        "runtime_s": time.time() - started,
        "pre_ca_path": result.pre_ca_path,
        "peis_path": result.peis_path,
        "post_ca_path": result.post_ca_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "pre_ca_rows": int(len(result.pre_ca_data)),
            "peis_rows": int(len(result.eis_data)),
            "post_ca_rows": int(len(result.ca_data)),
        },
    }


def _run_peis_then_post_ca(bl: BioLogicController, exp_dir: Path, bias_v: float) -> dict:
    label = "peis_then_post_ca"
    started = time.time()
    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1.0e5,
        peis_f_low=1.0,
        peis_npts=20,
        amplitude_mv=10.0,
        save_dir=str(exp_dir),
        label=label,
    )
    post = bl.run_ca_hold(bias_v + 0.03, 10.0, dt_record=0.02, read_interval=0.1)
    ts = time.strftime("%Y%m%d_%H%M%S")
    post_path = exp_dir / f"{label}_postCA_{ts}.txt"
    np.savetxt(post_path, np.asarray(post), header="time/s  V/V  I/A", comments="")
    analysis, visuals = analyze_measurement_files_with_visuals(
        str(post_path),
        peis_result.peis_path,
        sample_name=f"{label}_{bias_v:+0.3f}V",
    )
    return {
        "protocol": label,
        "runtime_s": time.time() - started,
        "peis_path": peis_result.peis_path,
        "post_ca_path": str(post_path),
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "peis_rows": int(len(peis_result.eis_data)),
            "post_ca_rows": int(len(post)),
        },
    }


def _run_true_ca_first_seeded(bl: BioLogicController, exp_dir: Path, bias_v: float) -> dict:
    label = "true_ca_first_seeded"
    started = time.time()

    stabilization = run_chunked_bias_stabilization(
        bl,
        v_dc=bias_v,
        chunk_duration_s=5.0,
        dt_record=0.5,
        max_total_s=20.0,
        settings=StabilizationSettings(
            min_hold_s=10.0,
            max_hold_s=20.0,
            extend_step_s=5.0,
            live_window_s=10.0,
            min_points_in_window=10,
            required_stable_windows=1,
        ),
    )
    stabilization_path = save_ca_txt(exp_dir / "stage1_vdc_stabilization_CA.txt", stabilization.data)

    scout = run_chunked_dv_scout(
        bl,
        v_dc=bias_v,
        dv=0.03,
        chunk_duration_s=5.0,
        dt_record=0.01,
        max_total_s=20.0,
    )
    scout_path = save_ca_txt(exp_dir / "stage2_dv_scout_CA.txt", scout.data)

    cp_recommendation = recommend_peis_lf_from_cp_txt(scout_path, current_in_mA=False)
    seed_lf = _safe_float(cp_recommendation["recommendation"].get("recommended_peis_lowest_freq_hz"))
    if seed_lf is None or not np.isfinite(seed_lf) or seed_lf <= 0:
        seed_lf = 1.0
    seed_lf = max(min(seed_lf, 1.0e5), 0.1)

    peis_dir = exp_dir / "seeded_peis"
    peis_dir.mkdir(parents=True, exist_ok=True)
    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1.0e5,
        peis_f_low=seed_lf,
        peis_npts=20,
        amplitude_mv=10.0,
        save_dir=str(peis_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        str(scout_path),
        peis_result.peis_path,
        sample_name=f"{label}_{bias_v:+0.3f}V",
    )
    return {
        "protocol": label,
        "runtime_s": time.time() - started,
        "stage1_vdc_stabilization_path": str(stabilization_path),
        "stage2_dv_scout_path": str(scout_path),
        "stage1_vdc_stabilization": _json_safe({k: v for k, v in stabilization.to_dict().items() if k != "data"}),
        "stage2_dv_scout": _json_safe({k: v for k, v in scout.to_dict().items() if k != "data"}),
        "cp_seed_recommendation": {
            "recommended_peis_lowest_freq_hz": seed_lf,
            "cp_saturation": _json_safe(cp_recommendation["cp_saturation"]),
            "smoothed_duration_s": cp_recommendation["smoothed_duration_s"],
            "analysis_point_count": int(len(cp_recommendation["analysis_f"])),
        },
        "peis_path": peis_result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "stage1_rows": int(len(stabilization.data)),
            "stage2_rows": int(len(scout.data)),
            "peis_rows": int(len(peis_result.eis_data)),
        },
    }


def _run_one_protocol(bl: BioLogicController, out_dir: Path, bias_v: float, protocol_name: str) -> dict:
    exp_dir = out_dir / f"{protocol_name}_V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    exp_dir.mkdir(parents=True, exist_ok=True)
    ocv_before = _safe_ocv(bl)
    live_before = _safe_live(bl)
    try:
        _ensure_connected(bl)
        if protocol_name == "normal_only":
            payload = _run_normal_only(bl, exp_dir, bias_v)
        elif protocol_name == "rapid_pre_peis_post":
            payload = _run_rapid_pre_peis_post(bl, exp_dir, bias_v)
        elif protocol_name == "peis_then_post_ca":
            payload = _run_peis_then_post_ca(bl, exp_dir, bias_v)
        elif protocol_name == "true_ca_first_seeded":
            payload = _run_true_ca_first_seeded(bl, exp_dir, bias_v)
        else:
            raise ValueError(f"Unknown protocol: {protocol_name}")
        error = None
    except Exception as exc:
        payload = {}
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    ocv_after = _safe_ocv(bl)
    live_after = _safe_live(bl)
    summary = {
        "bias_v": bias_v,
        "protocol": protocol_name,
        "ocv_before_v": ocv_before,
        "ocv_after_v": ocv_after,
        "live_before": live_before,
        "live_after": live_after,
        "error": error,
        **payload,
    }
    (exp_dir / "experiment_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    return summary


def main():
    out_dir = _timestamp_dir("live_protocol_voltage_matrix")
    biases_v = [-0.1, 0.0, 0.1]
    protocols = [
        "normal_only",
        "rapid_pre_peis_post",
        "peis_then_post_ca",
        "true_ca_first_seeded",
    ]
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "biases_v": biases_v,
        "protocols": protocols,
        "experiments": [],
    }

    bl = BioLogicController()
    try:
        _ensure_connected(bl)
        summary["initial_ocv_v"] = _safe_ocv(bl)
        summary["initial_live"] = _safe_live(bl)
        for bias_v in biases_v:
            for protocol_name in protocols:
                result = _run_one_protocol(bl, out_dir, bias_v, protocol_name)
                summary["experiments"].append(result)
                (out_dir / "matrix_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "matrix_summary.json"
    summary_path.write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    print(json.dumps(_json_safe(summary), indent=2))
    print(f"Saved live protocol voltage matrix to: {summary_path}")


if __name__ == "__main__":
    main()
