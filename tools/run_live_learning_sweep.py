# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
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


def _run_normal_only(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=spec["peis_lf_hz"],
        peis_npts=spec["peis_npts"],
        amplitude_mv=spec["amplitude_mv"],
        save_dir=str(exp_dir),
        label=spec["name"],
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        None,
        result.peis_path,
        sample_name=spec["name"],
    )
    return {
        "peis_path": result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {"peis_rows": int(len(result.eis_data))},
    }


def _run_rapid(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        dv=spec["dv_v"],
        hold_time=spec["pre_hold_s"],
        post_peis_hold_time=spec["post_hold_s"],
        peis_f_high=1.0e5,
        peis_f_low=spec["peis_lf_hz"],
        peis_npts=spec["peis_npts"],
        ca_duration=spec["ca_duration_s"],
        ca_dt=spec["ca_dt_s"],
        save_dir=str(exp_dir),
        label=spec["name"],
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        result.post_ca_path,
        result.peis_path,
        sample_name=spec["name"],
    )
    return {
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


def _run_peis_then_post_ca(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=spec["peis_lf_hz"],
        peis_npts=spec["peis_npts"],
        amplitude_mv=spec["amplitude_mv"],
        save_dir=str(exp_dir),
        label=spec["name"],
    )
    post = bl.run_ca_hold(spec["bias_v"] + spec["dv_v"], spec["ca_duration_s"], dt_record=spec["ca_dt_s"], read_interval=0.1)
    ts = time.strftime("%Y%m%d_%H%M%S")
    post_path = exp_dir / f"{spec['name']}_postCA_{ts}.txt"
    np.savetxt(post_path, np.asarray(post), header="time/s  V/V  I/A", comments="")
    analysis, visuals = analyze_measurement_files_with_visuals(
        str(post_path),
        peis_result.peis_path,
        sample_name=spec["name"],
    )
    return {
        "peis_path": peis_result.peis_path,
        "post_ca_path": str(post_path),
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "peis_rows": int(len(peis_result.eis_data)),
            "post_ca_rows": int(len(post)),
        },
    }


def _run_true_ca_first_seeded(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    stabilization = run_chunked_bias_stabilization(
        bl,
        v_dc=spec["bias_v"],
        chunk_duration_s=5.0,
        dt_record=0.5,
        max_total_s=spec["stabilization_max_s"],
        settings=StabilizationSettings(
            min_hold_s=10.0,
            max_hold_s=spec["stabilization_max_s"],
            extend_step_s=5.0,
            live_window_s=10.0,
            min_points_in_window=10,
            required_stable_windows=1,
        ),
    )
    stabilization_path = save_ca_txt(exp_dir / "stage1_vdc_stabilization_CA.txt", stabilization.data)

    scout = run_chunked_dv_scout(
        bl,
        v_dc=spec["bias_v"],
        dv=spec["dv_v"],
        chunk_duration_s=5.0,
        dt_record=spec["ca_dt_s"],
        max_total_s=spec["scout_max_s"],
    )
    scout_path = save_ca_txt(exp_dir / "stage2_dv_scout_CA.txt", scout.data)

    cp_recommendation = recommend_peis_lf_from_cp_txt(scout_path, current_in_mA=False)
    seed_lf = _safe_float(cp_recommendation["recommendation"].get("recommended_peis_lowest_freq_hz"))
    if seed_lf is None or not np.isfinite(seed_lf) or seed_lf <= 0:
        seed_lf = spec["peis_lf_hz"]
    seed_lf = max(min(seed_lf, 1.0e5), spec["seed_floor_hz"])

    peis_dir = exp_dir / "seeded_peis"
    peis_dir.mkdir(parents=True, exist_ok=True)
    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=seed_lf,
        peis_npts=spec["peis_npts"],
        amplitude_mv=spec["amplitude_mv"],
        save_dir=str(peis_dir),
        label=spec["name"],
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        str(scout_path),
        peis_result.peis_path,
        sample_name=spec["name"],
    )
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
        "rows": {
            "stage1_rows": int(len(stabilization.data)),
            "stage2_rows": int(len(scout.data)),
            "peis_rows": int(len(peis_result.eis_data)),
        },
    }


def _run_one(bl: BioLogicController, out_dir: Path, spec: dict) -> dict:
    exp_dir = out_dir / spec["name"]
    exp_dir.mkdir(parents=True, exist_ok=True)
    ocv_before = _safe_ocv(bl)
    live_before = _safe_live(bl)
    started = time.time()
    error = None
    payload = {}
    try:
        _ensure_connected(bl)
        if spec["architecture"] == "normal_only":
            payload = _run_normal_only(bl, exp_dir, spec)
        elif spec["architecture"] == "rapid_pre_peis_post":
            payload = _run_rapid(bl, exp_dir, spec)
        elif spec["architecture"] == "peis_then_post_ca":
            payload = _run_peis_then_post_ca(bl, exp_dir, spec)
        elif spec["architecture"] == "true_ca_first_seeded":
            payload = _run_true_ca_first_seeded(bl, exp_dir, spec)
        else:
            raise ValueError(f"Unknown architecture: {spec['architecture']}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    summary = {
        "name": spec["name"],
        "architecture": spec["architecture"],
        "parameters": spec,
        "ocv_before_v": ocv_before,
        "ocv_after_v": _safe_ocv(bl),
        "live_before": live_before,
        "live_after": _safe_live(bl),
        "runtime_s": time.time() - started,
        "error": error,
        **payload,
    }
    (exp_dir / "experiment_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    return summary


def _build_base_experiments_for_bias(bias_v: float):
    tag = f"V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    return [
        {
            "name": f"normal_only_lf1_amp10_{tag}",
            "architecture": "normal_only",
            "bias_v": bias_v,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "name": f"normal_only_lf0p3_amp10_{tag}",
            "architecture": "normal_only",
            "bias_v": bias_v,
            "peis_lf_hz": 0.3,
            "peis_npts": 22,
            "amplitude_mv": 10.0,
        },
        {
            "name": f"normal_only_lf0p1_amp20_{tag}",
            "architecture": "normal_only",
            "bias_v": bias_v,
            "peis_lf_hz": 0.1,
            "peis_npts": 24,
            "amplitude_mv": 20.0,
        },
        {
            "name": f"rapid_pre_post_dv30_{tag}",
            "architecture": "rapid_pre_peis_post",
            "bias_v": bias_v,
            "dv_v": 0.03,
            "pre_hold_s": 5.0,
            "post_hold_s": 3.0,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
        },
        {
            "name": f"rapid_pre_post_dv50_{tag}",
            "architecture": "rapid_pre_peis_post",
            "bias_v": bias_v,
            "dv_v": 0.05,
            "pre_hold_s": 5.0,
            "post_hold_s": 3.0,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 0.3,
            "peis_npts": 22,
        },
        {
            "name": f"rapid_pre_post_dv100_{tag}",
            "architecture": "rapid_pre_peis_post",
            "bias_v": bias_v,
            "dv_v": 0.1,
            "pre_hold_s": 5.0,
            "post_hold_s": 3.0,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 0.3,
            "peis_npts": 22,
        },
        {
            "name": f"peis_then_postca_dv30_{tag}",
            "architecture": "peis_then_post_ca",
            "bias_v": bias_v,
            "dv_v": 0.03,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "name": f"peis_then_postca_dv100_{tag}",
            "architecture": "peis_then_post_ca",
            "bias_v": bias_v,
            "dv_v": 0.1,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 0.3,
            "peis_npts": 22,
            "amplitude_mv": 20.0,
        },
        {
            "name": f"true_ca_first_dv30_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "dv_v": 0.03,
            "stabilization_max_s": 20.0,
            "scout_max_s": 20.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "name": f"true_ca_first_dv50_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "dv_v": 0.05,
            "stabilization_max_s": 20.0,
            "scout_max_s": 20.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
        {
            "name": f"true_ca_first_dv100_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "dv_v": 0.1,
            "stabilization_max_s": 20.0,
            "scout_max_s": 20.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
            "amplitude_mv": 10.0,
        },
    ]


def _extract_recommendation(exp_summary: dict):
    analysis = exp_summary.get("analysis") or {}
    rec_lf = _safe_float(analysis.get("recommended_peis_lowest_freq_hz"))
    rec_normal_lf = _safe_float(analysis.get("recommended_normal_peis_lowest_freq_hz"))
    rec_cp = _safe_float(analysis.get("recommended_peis_conservative_cp_time_s"))
    peis_only_sufficient = bool(analysis.get("peis_only_sufficient", False))
    data_sufficient = bool(analysis.get("data_sufficient", False))
    confidence = _safe_float(analysis.get("confidence"))
    return {
        "recommended_peis_lowest_freq_hz": rec_lf,
        "recommended_normal_peis_lowest_freq_hz": rec_normal_lf,
        "recommended_peis_conservative_cp_time_s": rec_cp,
        "peis_only_sufficient": peis_only_sufficient,
        "data_sufficient": data_sufficient,
        "confidence": confidence if confidence is not None else -1.0,
    }


def _build_followup_for_bias(bias_v: float, bias_results: list[dict]):
    tag = f"V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    valid = [res for res in bias_results if not res.get("error")]
    if not valid:
        return None

    enriched = []
    for res in valid:
        rec = _extract_recommendation(res)
        enriched.append((res, rec))

    hybrid_candidates = [item for item in enriched if not item[1]["peis_only_sufficient"]]
    if hybrid_candidates:
        chosen_res, chosen = min(
            hybrid_candidates,
            key=lambda item: (
                0 if item[1]["data_sufficient"] else 1,
                item[1]["recommended_peis_lowest_freq_hz"] if item[1]["recommended_peis_lowest_freq_hz"] is not None else 1.0e9,
                -(item[1]["confidence"]),
            ),
        )
        rec_lf = chosen["recommended_peis_lowest_freq_hz"] or 0.1
        rec_cp = chosen["recommended_peis_conservative_cp_time_s"] or 30.0
        return {
            "kind": "optimized_hybrid_followup",
            "name": f"optimized_followup_hybrid_{tag}",
            "architecture": "rapid_pre_peis_post",
            "bias_v": bias_v,
            "dv_v": 0.03,
            "pre_hold_s": 5.0,
            "post_hold_s": 3.0,
            "ca_duration_s": float(min(max(rec_cp, 10.0), 60.0)),
            "ca_dt_s": 0.02,
            "peis_lf_hz": float(max(min(rec_lf, 1.0e5), 0.1)),
            "peis_npts": 24,
            "optimized_from": chosen_res["name"],
        }

    normal_candidates = [item for item in enriched if item[1]["peis_only_sufficient"]]
    chosen_res, chosen = max(
        normal_candidates,
        key=lambda item: (
            item[1]["data_sufficient"],
            item[1]["recommended_normal_peis_lowest_freq_hz"] if item[1]["recommended_normal_peis_lowest_freq_hz"] is not None else -1.0,
            item[1]["confidence"],
        ),
    )
    rec_normal_lf = chosen["recommended_normal_peis_lowest_freq_hz"] or chosen["recommended_peis_lowest_freq_hz"] or 1.0
    return {
        "kind": "optimized_normal_followup",
        "name": f"optimized_followup_normal_{tag}",
        "architecture": "normal_only",
        "bias_v": bias_v,
        "peis_lf_hz": float(max(min(rec_normal_lf, 1.0e5), 0.1)),
        "peis_npts": 24,
        "amplitude_mv": 10.0,
        "optimized_from": chosen_res["name"],
    }


def _build_carryover_followup_for_bias(bias_v: float, previous_followup: dict | None):
    if not previous_followup:
        return None
    tag = f"V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    kind = previous_followup.get("kind")
    if kind is None:
        name = str(previous_followup.get("name", "")).lower()
        arch = str(previous_followup.get("architecture", "")).lower()
        if "hybrid" in name or arch == "rapid_pre_peis_post":
            kind = "optimized_hybrid_followup"
        else:
            kind = "optimized_normal_followup"
    if kind == "optimized_hybrid_followup":
        return {
            "name": f"carryover_hybrid_from_prev_{tag}",
            "architecture": "rapid_pre_peis_post",
            "bias_v": bias_v,
            "dv_v": float(previous_followup.get("dv_v", 0.03)),
            "pre_hold_s": 5.0,
            "post_hold_s": 3.0,
            "ca_duration_s": float(previous_followup.get("cp_s", 30.0)),
            "ca_dt_s": 0.02,
            "peis_lf_hz": float(previous_followup.get("lf_hz", 0.1)),
            "peis_npts": 24,
            "carried_from_bias_v": previous_followup.get("bias_v"),
            "carried_from_name": previous_followup.get("optimized_from"),
        }
    return {
        "name": f"carryover_normal_from_prev_{tag}",
        "architecture": "normal_only",
        "bias_v": bias_v,
        "peis_lf_hz": float(previous_followup.get("lf_hz", 1.0)),
        "peis_npts": 24,
        "amplitude_mv": 10.0,
        "carried_from_bias_v": previous_followup.get("bias_v"),
        "carried_from_name": previous_followup.get("optimized_from"),
    }


def _reconstruct_previous_followup(summary: dict):
    experiments = summary.get("experiments", [])
    for exp in reversed(experiments):
        name = str(exp.get("name", ""))
        params = exp.get("parameters") or {}
        if name.startswith("optimized_followup_"):
            kind = "optimized_hybrid_followup" if "hybrid" in name else "optimized_normal_followup"
            if kind == "optimized_hybrid_followup":
                return {
                    "kind": kind,
                    "name": name,
                    "architecture": params.get("architecture", "rapid_pre_peis_post"),
                    "bias_v": params.get("bias_v", exp.get("bias_v")),
                    "dv_v": params.get("dv_v", 0.03),
                    "lf_hz": params.get("peis_lf_hz", 0.1),
                    "cp_s": params.get("ca_duration_s", 30.0),
                    "optimized_from": params.get("optimized_from"),
                }
            return {
                "kind": kind,
                "name": name,
                "architecture": params.get("architecture", "normal_only"),
                "bias_v": params.get("bias_v", exp.get("bias_v")),
                "lf_hz": params.get("peis_lf_hz", 1.0),
                "optimized_from": params.get("optimized_from"),
            }
    return None


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-summary", type=str, default=None)
    parser.add_argument("--start-bias", type=float, default=None)
    return parser.parse_args()


def main():
    args = _parse_args()
    biases = [0.3, 0.25, 0.2, 0.1, 0.0, -0.1, -0.2, -0.25, -0.3]
    if args.resume_summary:
        summary_path = Path(args.resume_summary)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        out_dir = summary_path.parent
    else:
        out_dir = _timestamp_dir("live_learning_sweep")
        summary = {
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(out_dir),
            "biases_v": biases,
            "bias_strategy": "descending ladder from +0.3 V to -0.3 V to probe history-aware stabilization and sequence dependence",
            "experiments": [],
        }
    bl = BioLogicController()
    try:
        _ensure_connected(bl)
        summary.setdefault("initial_ocv_v", _safe_ocv(bl))
        summary.setdefault("initial_live", _safe_live(bl))
        total_counter = len(summary.get("experiments", []))
        previous_followup = _reconstruct_previous_followup(summary)
        for bias_v in biases:
            if args.start_bias is not None and bias_v > float(args.start_bias) + 1e-12:
                continue
            if any(abs(float(exp.get("bias_v", 999)) - bias_v) < 1e-12 for exp in summary.get("experiments", []) if str(exp.get("name", "")).startswith("optimized_followup_")) and args.start_bias is None:
                continue
            bias_results = []
            carryover = _build_carryover_followup_for_bias(bias_v, previous_followup)
            if carryover is not None:
                total_counter += 1
                print(f"\n=== [{total_counter}] {carryover['name']} ===")
                carryover_result = _run_one(bl, out_dir, carryover)
                bias_results.append(carryover_result)
                summary["experiments"].append(carryover_result)
                (out_dir / "sweep_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")

            base_experiments = _build_base_experiments_for_bias(bias_v)
            summary.setdefault("base_experiment_count", 0)
            summary["base_experiment_count"] += len(base_experiments)
            for spec in base_experiments:
                total_counter += 1
                print(f"\n=== [{total_counter}] {spec['name']} ===")
                result = _run_one(bl, out_dir, spec)
                bias_results.append(result)
                summary["experiments"].append(result)
                (out_dir / "sweep_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")

            followup = _build_followup_for_bias(bias_v, bias_results)
            if followup is not None:
                total_counter += 1
                print(f"\n=== [{total_counter}] {followup['name']} ===")
                followup_result = _run_one(bl, out_dir, followup)
                summary["experiments"].append(followup_result)
                (out_dir / "sweep_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
                previous_followup = followup
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass
    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "sweep_summary.json"
    summary_path.write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    print(json.dumps(_json_safe(summary), indent=2))
    print(f"Saved live learning sweep to: {summary_path}")


if __name__ == "__main__":
    main()
