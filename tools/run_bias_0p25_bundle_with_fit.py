# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

CONVERT_DIR = PROJECT_DIR.parent / "Convert_CP_to_EIS 1"
if str(CONVERT_DIR) not in sys.path:
    sys.path.insert(0, str(CONVERT_DIR))

from analysis_adapter import analyze_measurement_files_with_visuals
from cp_first_policy import (
    recommend_peis_lf_from_cp_txt,
    run_chunked_bias_stabilization,
    run_chunked_dv_scout,
    save_ca_txt,
)
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence, rapid_eis_sequence
from stabilization_policy import StabilizationSettings

import EIS_Fitting as EISFIT


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


def _analysis_to_dict(result):
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


def _fit_peis_rqrqrq(peis_path: str, out_dir: Path, title: str):
    data = np.loadtxt(peis_path, skiprows=1)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    freq = data[:, 0]
    re_z = data[:, 1]
    im_z = -data[:, 2]

    fit_result = EISFIT.fit_RQRQRQ_stable(freq, re_z, im_z)
    fit_metrics = EISFIT.compare_fit_to_data(freq, re_z + 1j * im_z, fit_result)
    fig = EISFIT.plot_fit(freq, re_z, im_z, fit_result, title=title)
    plot_path = out_dir / f"{title}_rqrqrq_fit.png"
    fig.savefig(plot_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return {
        "fit_result": _json_safe(fit_result),
        "fit_metrics": _json_safe(fit_metrics),
        "plot_path": str(plot_path),
    }


def _run_normal(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float, lf_hz: float, amp_mv: float, n_pts: int):
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1.0e5,
        peis_f_low=lf_hz,
        peis_npts=n_pts,
        amplitude_mv=amp_mv,
        save_dir=str(exp_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(None, result.peis_path, sample_name=label)
    fit = _fit_peis_rqrqrq(result.peis_path, exp_dir, label)
    return {
        "peis_path": result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "fit_rqrqrq": fit,
        "rows": {"peis_rows": int(len(result.eis_data))},
    }


def _run_rapid(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float, dv_v: float, lf_hz: float):
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        dv=dv_v,
        hold_time=5.0,
        post_peis_hold_time=3.0,
        peis_f_high=1.0e5,
        peis_f_low=lf_hz,
        peis_npts=22,
        ca_duration=10.0,
        ca_dt=0.02,
        save_dir=str(exp_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(result.post_ca_path, result.peis_path, sample_name=label)
    fit = _fit_peis_rqrqrq(result.peis_path, exp_dir, label)
    return {
        "pre_ca_path": result.pre_ca_path,
        "peis_path": result.peis_path,
        "post_ca_path": result.post_ca_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "fit_rqrqrq": fit,
        "rows": {
            "pre_ca_rows": int(len(result.pre_ca_data)),
            "peis_rows": int(len(result.eis_data)),
            "post_ca_rows": int(len(result.ca_data)),
        },
    }


def _run_peis_then_post(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float, dv_v: float, lf_hz: float, amp_mv: float):
    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=bias_v,
        peis_f_high=1.0e5,
        peis_f_low=lf_hz,
        peis_npts=22,
        amplitude_mv=amp_mv,
        save_dir=str(exp_dir),
        label=label,
    )
    post = bl.run_ca_hold(bias_v + dv_v, 10.0, dt_record=0.02, read_interval=0.1)
    post_path = exp_dir / f"{label}_postCA_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    np.savetxt(post_path, np.asarray(post), header="time/s  V/V  I/A", comments="")
    analysis, visuals = analyze_measurement_files_with_visuals(str(post_path), peis_result.peis_path, sample_name=label)
    fit = _fit_peis_rqrqrq(peis_result.peis_path, exp_dir, label)
    return {
        "peis_path": peis_result.peis_path,
        "post_ca_path": str(post_path),
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "fit_rqrqrq": fit,
        "rows": {
            "peis_rows": int(len(peis_result.eis_data)),
            "post_ca_rows": int(len(post)),
        },
    }


def _run_true_ca_first(bl: BioLogicController, exp_dir: Path, label: str, bias_v: float, dv_v: float):
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
        dv=dv_v,
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
    analysis, visuals = analyze_measurement_files_with_visuals(str(scout_path), peis_result.peis_path, sample_name=label)
    fit = _fit_peis_rqrqrq(peis_result.peis_path, exp_dir, label)
    return {
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
        "fit_rqrqrq": fit,
        "rows": {
            "stage1_rows": int(len(stabilization.data)),
            "stage2_rows": int(len(scout.data)),
            "peis_rows": int(len(peis_result.eis_data)),
        },
    }


def _build_followup_from_results(results: list[dict], bias_v: float):
    valid = [res for res in results if not res.get("error")]
    if not valid:
        return None
    hybrid = [res for res in valid if not bool((res.get("analysis") or {}).get("peis_only_sufficient", False))]
    if hybrid:
        chosen = min(
            hybrid,
            key=lambda res: _safe_float((res.get("analysis") or {}).get("recommended_peis_lowest_freq_hz")) or 1.0e9,
        )
        rec_lf = _safe_float((chosen.get("analysis") or {}).get("recommended_peis_lowest_freq_hz")) or 0.1
        rec_cp = _safe_float((chosen.get("analysis") or {}).get("recommended_peis_conservative_cp_time_s")) or 30.0
        return {
            "kind": "optimized_hybrid_followup",
            "optimized_from": chosen["name"],
            "bias_v": bias_v,
            "dv_v": 0.03,
            "lf_hz": max(min(rec_lf, 1.0e5), 0.1),
            "cp_s": min(max(rec_cp, 10.0), 60.0),
        }
    chosen = max(
        valid,
        key=lambda res: _safe_float((res.get("analysis") or {}).get("confidence")) or -1.0,
    )
    rec_lf = _safe_float((chosen.get("analysis") or {}).get("recommended_normal_peis_lowest_freq_hz")) or 1.0
    return {
        "kind": "optimized_normal_followup",
        "optimized_from": chosen["name"],
        "bias_v": bias_v,
        "lf_hz": max(min(rec_lf, 1.0e5), 0.1),
    }


def _run_followup(bl: BioLogicController, exp_dir: Path, followup: dict):
    label = followup["kind"]
    if followup["kind"] == "optimized_hybrid_followup":
        result = rapid_eis_sequence(
            biologic=bl,
            v_dc=followup["bias_v"],
            dv=followup["dv_v"],
            hold_time=5.0,
            post_peis_hold_time=3.0,
            peis_f_high=1.0e5,
            peis_f_low=followup["lf_hz"],
            peis_npts=24,
            ca_duration=followup["cp_s"],
            ca_dt=0.02,
            save_dir=str(exp_dir),
            label=label,
        )
        analysis, visuals = analyze_measurement_files_with_visuals(result.post_ca_path, result.peis_path, sample_name=label)
        fit = _fit_peis_rqrqrq(result.peis_path, exp_dir, label)
        return {
            "pre_ca_path": result.pre_ca_path,
            "peis_path": result.peis_path,
            "post_ca_path": result.post_ca_path,
            "analysis": _analysis_to_dict(analysis),
            "visuals": _visuals_to_dict(visuals),
            "fit_rqrqrq": fit,
            "rows": {
                "pre_ca_rows": int(len(result.pre_ca_data)),
                "peis_rows": int(len(result.eis_data)),
                "post_ca_rows": int(len(result.ca_data)),
            },
        }
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=followup["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=followup["lf_hz"],
        peis_npts=24,
        amplitude_mv=10.0,
        save_dir=str(exp_dir),
        label=label,
    )
    analysis, visuals = analyze_measurement_files_with_visuals(None, result.peis_path, sample_name=label)
    fit = _fit_peis_rqrqrq(result.peis_path, exp_dir, label)
    return {
        "peis_path": result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "fit_rqrqrq": fit,
        "rows": {"peis_rows": int(len(result.eis_data))},
    }


def main():
    out_dir = _timestamp_dir("bias_0p25_bundle_with_fit")
    bias_v = 0.25
    bundle = [
        ("normal_only_lf1", lambda bl, d: _run_normal(bl, d, "normal_only_lf1", bias_v, 1.0, 10.0, 20)),
        ("normal_only_lf0p3", lambda bl, d: _run_normal(bl, d, "normal_only_lf0p3", bias_v, 0.3, 10.0, 22)),
        ("normal_only_lf0p1", lambda bl, d: _run_normal(bl, d, "normal_only_lf0p1", bias_v, 0.1, 20.0, 24)),
        ("rapid_dv30", lambda bl, d: _run_rapid(bl, d, "rapid_dv30", bias_v, 0.03, 1.0)),
        ("rapid_dv100", lambda bl, d: _run_rapid(bl, d, "rapid_dv100", bias_v, 0.1, 0.3)),
        ("peis_then_post_dv30", lambda bl, d: _run_peis_then_post(bl, d, "peis_then_post_dv30", bias_v, 0.03, 1.0, 10.0)),
        ("true_ca_first_dv30", lambda bl, d: _run_true_ca_first(bl, d, "true_ca_first_dv30", bias_v, 0.03)),
        ("true_ca_first_dv100", lambda bl, d: _run_true_ca_first(bl, d, "true_ca_first_dv100", bias_v, 0.1)),
    ]
    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "bias_v": bias_v,
        "experiments": [],
    }
    bl = BioLogicController()
    try:
        _ensure_connected(bl)
        summary["initial_ocv_v"] = _safe_float(bl.get_ocv())
        summary["initial_live"] = bl.get_live_values()
        for name, runner in bundle:
            print(f"\n=== Running {name} at {bias_v:+0.3f} V ===")
            exp_dir = out_dir / name
            exp_dir.mkdir(parents=True, exist_ok=True)
            started = time.time()
            try:
                payload = runner(bl, exp_dir)
                error = None
            except Exception as exc:
                payload = {}
                error = f"{type(exc).__name__}: {exc}"
                (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
            exp_summary = {
                "name": name,
                "bias_v": bias_v,
                "runtime_s": time.time() - started,
                "ocv_before_v": _safe_float(summary["initial_ocv_v"]) if not summary["experiments"] else _safe_float(bl.get_ocv()),
                "ocv_after_v": _safe_float(bl.get_ocv()),
                "error": error,
                **payload,
            }
            summary["experiments"].append(exp_summary)
            (out_dir / "bundle_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")

        followup = _build_followup_from_results(summary["experiments"], bias_v)
        if followup is not None:
            print(f"\n=== Running {followup['kind']} from {followup['optimized_from']} ===")
            exp_dir = out_dir / followup["kind"]
            exp_dir.mkdir(parents=True, exist_ok=True)
            started = time.time()
            try:
                payload = _run_followup(bl, exp_dir, followup)
                error = None
            except Exception as exc:
                payload = {}
                error = f"{type(exc).__name__}: {exc}"
                (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
            summary["optimized_followup"] = {
                **followup,
                "runtime_s": time.time() - started,
                "error": error,
                **payload,
            }
            (out_dir / "bundle_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "bundle_summary.json"
    summary_path.write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    print(json.dumps(_json_safe(summary), indent=2))
    print(f"Saved 0.25V bundle with fit to: {summary_path}")


if __name__ == "__main__":
    main()
