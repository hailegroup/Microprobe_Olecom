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
    run_chunked_dv_scout,
    save_ca_txt,
)
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence, rapid_eis_sequence
from stabilization_policy import StabilizationAssessment, StabilizationSettings, analyze_pre_peis_stability


BIAS_LADDER = [0.3, 0.25, 0.2, 0.1, 0.0, -0.1, -0.2, -0.25, -0.3]
DV_V = 0.03
PEIS_AMPLITUDE_MV = 30.0
HARD_RUN_CAP_S = 600.0


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


def _detrend_current_linear(time_s: np.ndarray, current_a: np.ndarray) -> np.ndarray:
    time_arr = np.asarray(time_s, dtype=float)
    current_arr = np.asarray(current_a, dtype=float)
    finite = np.isfinite(time_arr) & np.isfinite(current_arr)
    time_arr = time_arr[finite]
    current_arr = current_arr[finite]
    if len(time_arr) < 3:
        return current_arr
    x = time_arr - time_arr[0]
    coeff = np.polyfit(x, current_arr, deg=1)
    trend = np.polyval(coeff, x)
    return current_arr - trend


def _analyze_stability_mode(
    time_s: np.ndarray,
    current_a: np.ndarray,
    *,
    mode: str,
    settings: StabilizationSettings,
) -> StabilizationAssessment:
    if mode == "raw":
        return analyze_pre_peis_stability(time_s, current_a, settings=settings, peis_already_started=False)
    if mode == "detrended":
        detrended = _detrend_current_linear(time_s, current_a)
        assessment = analyze_pre_peis_stability(time_s, detrended, settings=settings, peis_already_started=False)
        assessment.notes.append("stability assessed after linear detrend of the CA background")
        return assessment
    raise ValueError(f"Unknown stabilization mode: {mode}")


def _concat_ca_segments(existing: np.ndarray, segment: np.ndarray) -> np.ndarray:
    existing = np.asarray(existing, dtype=float)
    segment = np.asarray(segment, dtype=float)
    if existing.size == 0:
        return segment.copy()
    if segment.size == 0:
        return existing.copy()
    merged = segment.copy()
    time_offset = float(existing[-1, 0])
    merged[:, 0] = merged[:, 0] - float(merged[0, 0]) + time_offset
    return np.vstack([existing, merged])


def _run_chunked_bias_stabilization_mode(
    biologic,
    *,
    v_dc: float,
    mode: str,
    chunk_duration_s: float = 5.0,
    dt_record: float = 0.5,
    max_total_s: float = 40.0,
):
    settings = StabilizationSettings(
        min_hold_s=10.0,
        max_hold_s=max_total_s,
        extend_step_s=5.0,
        live_window_s=10.0,
        min_points_in_window=10,
        required_stable_windows=1,
    )
    collected = np.empty((0, 3), dtype=float)
    chunk_count = 0
    last_assessment = None
    while True:
        segment = biologic.run_ca_hold(v_dc, chunk_duration_s, dt_record=dt_record)
        collected = _concat_ca_segments(collected, segment)
        chunk_count += 1
        assessment = _analyze_stability_mode(collected[:, 0], collected[:, 2], mode=mode, settings=settings)
        last_assessment = assessment.to_dict()
        hold_now = float(collected[-1, 0] - collected[0, 0]) if len(collected) else 0.0
        if assessment.should_start_peis:
            return {
                "data": collected,
                "total_duration_s": hold_now,
                "chunk_count": chunk_count,
                "last_assessment": last_assessment,
                "stop_reason": "stabilized",
                "mode": mode,
            }
        if hold_now >= float(max_total_s) - 1e-9 or not assessment.should_continue_hold:
            return {
                "data": collected,
                "total_duration_s": hold_now,
                "chunk_count": chunk_count,
                "last_assessment": last_assessment,
                "stop_reason": "max_total_reached",
                "mode": mode,
            }


def _run_tiny_peis_scout(bl: BioLogicController, exp_dir: Path, spec: dict) -> tuple[str, int]:
    result = normal_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=1.0e3,
        peis_npts=8,
        amplitude_mv=PEIS_AMPLITUDE_MV,
        save_dir=str(exp_dir),
        label=f"{spec['name']}_tiny_scout",
    )
    return result.peis_path, int(len(result.eis_data))


def _run_rapid_realtime(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    pre = _run_chunked_bias_stabilization_mode(
        bl,
        v_dc=spec["bias_v"],
        mode=spec["stabilization_mode"],
        chunk_duration_s=5.0,
        dt_record=0.5,
        max_total_s=spec["pre_max_s"],
    )
    pre_path = save_ca_txt(exp_dir / "realtime_preCA.txt", pre["data"])

    peis_result = normal_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        peis_f_high=1.0e5,
        peis_f_low=spec["peis_lf_hz"],
        peis_npts=spec["peis_npts"],
        amplitude_mv=PEIS_AMPLITUDE_MV,
        save_dir=str(exp_dir),
        label=spec["name"],
    )

    post = run_chunked_dv_scout(
        bl,
        v_dc=spec["bias_v"],
        dv=DV_V,
        chunk_duration_s=5.0,
        dt_record=spec["ca_dt_s"],
        max_total_s=spec["post_max_s"],
    )
    post_path = save_ca_txt(exp_dir / "realtime_postCA.txt", post.data)

    analysis, visuals = analyze_measurement_files_with_visuals(
        str(post_path),
        peis_result.peis_path,
        sample_name=spec["name"],
    )
    return {
        "pre_ca_path": str(pre_path),
        "post_ca_path": str(post_path),
        "peis_path": peis_result.peis_path,
        "pre_ca": _json_safe({k: v for k, v in pre.items() if k != "data"}),
        "post_ca": _json_safe({k: v for k, v in post.to_dict().items() if k != "data"}),
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "pre_ca_rows": int(len(pre["data"])),
            "peis_rows": int(len(peis_result.eis_data)),
            "post_ca_rows": int(len(post.data)),
        },
    }


def _run_rapid_static(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    result = rapid_eis_sequence(
        biologic=bl,
        v_dc=spec["bias_v"],
        dv=DV_V,
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
        "post_ca_path": result.post_ca_path,
        "peis_path": result.peis_path,
        "analysis": _analysis_to_dict(analysis),
        "visuals": _visuals_to_dict(visuals),
        "rows": {
            "pre_ca_rows": int(len(result.pre_ca_data)),
            "peis_rows": int(len(result.eis_data)),
            "post_ca_rows": int(len(result.ca_data)),
        },
    }


def _run_true_ca_first_variant(bl: BioLogicController, exp_dir: Path, spec: dict) -> dict:
    tiny_path = None
    tiny_rows = 0
    if spec.get("tiny_peis_scout", False):
        tiny_path, tiny_rows = _run_tiny_peis_scout(bl, exp_dir, spec)

    stabilization = _run_chunked_bias_stabilization_mode(
        bl,
        v_dc=spec["bias_v"],
        mode=spec["stabilization_mode"],
        chunk_duration_s=5.0,
        dt_record=0.5,
        max_total_s=spec["stabilization_max_s"],
    )
    stabilization_path = save_ca_txt(exp_dir / "stage1_vdc_stabilization_CA.txt", stabilization["data"])

    scout = run_chunked_dv_scout(
        bl,
        v_dc=spec["bias_v"],
        dv=DV_V,
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
        amplitude_mv=PEIS_AMPLITUDE_MV,
        save_dir=str(peis_dir),
        label=spec["name"],
    )
    analysis, visuals = analyze_measurement_files_with_visuals(
        str(scout_path),
        peis_result.peis_path,
        sample_name=spec["name"],
    )
    return {
        "tiny_peis_scout_path": tiny_path,
        "stage1_vdc_stabilization_path": str(stabilization_path),
        "stage2_dv_scout_path": str(scout_path),
        "stage1_vdc_stabilization": _json_safe({k: v for k, v in stabilization.items() if k != "data"}),
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
            "tiny_peis_rows": tiny_rows,
            "stage1_rows": int(len(stabilization["data"])),
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
        if spec["architecture"] == "rapid_pre_peis_post_static":
            payload = _run_rapid_static(bl, exp_dir, spec)
        elif spec["architecture"] == "rapid_pre_peis_post_realtime":
            payload = _run_rapid_realtime(bl, exp_dir, spec)
        elif spec["architecture"] == "true_ca_first_seeded":
            payload = _run_true_ca_first_variant(bl, exp_dir, spec)
        else:
            raise ValueError(f"Unknown architecture: {spec['architecture']}")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        (exp_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
    summary = {
        "name": spec["name"],
        "architecture": spec["architecture"],
        "parameters": spec,
        "bias_v": spec["bias_v"],
        "hard_run_cap_s": HARD_RUN_CAP_S,
        "ocv_before_v": ocv_before,
        "ocv_after_v": _safe_ocv(bl),
        "live_before": live_before,
        "live_after": _safe_live(bl),
        "runtime_s": time.time() - started,
        "error": error,
        **payload,
    }
    analysis = summary.get("analysis") or {}
    runtime_s = _safe_float(summary.get("runtime_s")) or 0.0
    summary["discard_candidate"] = bool(
        runtime_s >= HARD_RUN_CAP_S
        or (
            runtime_s >= HARD_RUN_CAP_S * 0.95
            and (
                not bool(analysis.get("data_sufficient", False))
                or not bool(analysis.get("peis_only_sufficient", False))
            )
        )
    )
    if summary["discard_candidate"] and not summary.get("error"):
        summary["discard_reason"] = (
            "run approached or exceeded the 10 min hard cap without producing a clearly sufficient result"
        )
    (exp_dir / "experiment_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    return summary


def _extract_recommendation(exp_summary: dict):
    analysis = exp_summary.get("analysis") or {}
    return {
        "recommended_peis_lowest_freq_hz": _safe_float(analysis.get("recommended_peis_lowest_freq_hz")),
        "recommended_normal_peis_lowest_freq_hz": _safe_float(analysis.get("recommended_normal_peis_lowest_freq_hz")),
        "recommended_peis_conservative_cp_time_s": _safe_float(analysis.get("recommended_peis_conservative_cp_time_s")),
        "peis_only_sufficient": bool(analysis.get("peis_only_sufficient", False)),
        "data_sufficient": bool(analysis.get("data_sufficient", False)),
        "confidence": _safe_float(analysis.get("confidence")) or -1.0,
    }


def _build_base_experiments_for_bias(bias_v: float):
    tag = f"V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    return [
        {
            "name": f"rapid_static30_{tag}",
            "architecture": "rapid_pre_peis_post_static",
            "bias_v": bias_v,
            "pre_hold_s": 60.0,
            "post_hold_s": 60.0,
            "ca_duration_s": 10.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
        },
        {
            "name": f"rapid_realtime30_raw_{tag}",
            "architecture": "rapid_pre_peis_post_realtime",
            "bias_v": bias_v,
            "stabilization_mode": "raw",
            "pre_max_s": 120.0,
            "post_max_s": 120.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
        },
        {
            "name": f"rapid_realtime30_detrended_{tag}",
            "architecture": "rapid_pre_peis_post_realtime",
            "bias_v": bias_v,
            "stabilization_mode": "detrended",
            "pre_max_s": 120.0,
            "post_max_s": 120.0,
            "ca_dt_s": 0.02,
            "peis_lf_hz": 1.0,
            "peis_npts": 20,
        },
        {
            "name": f"true_ca_first30_raw_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "stabilization_mode": "raw",
            "tiny_peis_scout": False,
            "stabilization_max_s": 120.0,
            "scout_max_s": 120.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
        },
        {
            "name": f"true_ca_first30_detrended_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "stabilization_mode": "detrended",
            "tiny_peis_scout": False,
            "stabilization_max_s": 120.0,
            "scout_max_s": 120.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
        },
        {
            "name": f"true_ca_first30_tiny_raw_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "stabilization_mode": "raw",
            "tiny_peis_scout": True,
            "stabilization_max_s": 120.0,
            "scout_max_s": 120.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
        },
        {
            "name": f"true_ca_first30_tiny_detrended_{tag}",
            "architecture": "true_ca_first_seeded",
            "bias_v": bias_v,
            "stabilization_mode": "detrended",
            "tiny_peis_scout": True,
            "stabilization_max_s": 120.0,
            "scout_max_s": 120.0,
            "ca_dt_s": 0.01,
            "peis_lf_hz": 1.0,
            "seed_floor_hz": 0.1,
            "peis_npts": 20,
        },
    ]


def _build_followups_for_bias(bias_v: float, bias_results: list[dict]):
    tag = f"V{bias_v:+0.3f}".replace("+", "p").replace("-", "m")
    valid = [res for res in bias_results if not res.get("error")]
    if not valid:
        return []
    enriched = [(res, _extract_recommendation(res)) for res in valid]
    hybrid_candidates = [item for item in enriched if not item[1]["peis_only_sufficient"]]
    if not hybrid_candidates:
        return []
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
    return [
        {
            "name": f"optimized_static30_{tag}",
            "architecture": "rapid_pre_peis_post_static",
            "bias_v": bias_v,
            "pre_hold_s": 60.0,
            "post_hold_s": 60.0,
            "ca_duration_s": float(min(max(rec_cp, 10.0), 60.0)),
            "ca_dt_s": 0.02,
            "peis_lf_hz": float(max(min(rec_lf, 1.0e5), 0.1)),
            "peis_npts": 24,
            "optimized_from": chosen_res["name"],
        },
        {
            "name": f"optimized_realtime30_{tag}",
            "architecture": "rapid_pre_peis_post_realtime",
            "bias_v": bias_v,
            "stabilization_mode": "detrended",
            "pre_max_s": float(min(max(rec_cp * 0.5, 60.0), 120.0)),
            "post_max_s": float(min(max(rec_cp, 60.0), 120.0)),
            "ca_dt_s": 0.02,
            "peis_lf_hz": float(max(min(rec_lf, 1.0e5), 0.1)),
            "peis_npts": 24,
            "optimized_from": chosen_res["name"],
        },
    ]


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-summary", type=str, default=None)
    parser.add_argument("--start-bias", type=float, default=None)
    parser.add_argument("--biases", type=str, default=None, help="Comma-separated bias list, e.g. 0.2,0.1,0,-0.1,-0.2")
    return parser.parse_args()


def main():
    args = _parse_args()
    biases = BIAS_LADDER
    if args.biases:
        biases = [float(token.strip()) for token in args.biases.split(",") if token.strip()]
    if args.resume_summary:
        summary_path = Path(args.resume_summary)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        out_dir = summary_path.parent
    else:
        out_dir = _timestamp_dir("live_realtime_protocol_comparison")
        summary = {
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(out_dir),
            "hard_run_cap_s": HARD_RUN_CAP_S,
            "biases_v": biases,
            "bias_strategy": "descending ladder from +0.3 V to -0.3 V with 30 mV perturbations and realtime/detrended/tiny-PEIS protocol comparison",
            "experiments": [],
        }

    bl = BioLogicController()
    try:
        _ensure_connected(bl)
        summary.setdefault("initial_ocv_v", _safe_ocv(bl))
        summary.setdefault("initial_live", _safe_live(bl))
        total_counter = len(summary.get("experiments", []))
        for bias_v in biases:
            if args.start_bias is not None and bias_v > float(args.start_bias) + 1e-12:
                continue
            done_names = {str(exp.get("name", "")) for exp in summary.get("experiments", [])}
            bias_results = []
            for spec in _build_base_experiments_for_bias(bias_v):
                if spec["name"] in done_names:
                    continue
                total_counter += 1
                print(f"\n=== [{total_counter}] {spec['name']} ===")
                result = _run_one(bl, out_dir, spec)
                bias_results.append(result)
                summary["experiments"].append(result)
                (out_dir / "comparison_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")

            for spec in _build_followups_for_bias(bias_v, bias_results):
                if spec["name"] in done_names:
                    continue
                total_counter += 1
                print(f"\n=== [{total_counter}] {spec['name']} ===")
                result = _run_one(bl, out_dir, spec)
                summary["experiments"].append(result)
                (out_dir / "comparison_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    finally:
        try:
            bl.disconnect()
        except Exception:
            pass

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary_path = out_dir / "comparison_summary.json"
    summary_path.write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    print(json.dumps(_json_safe(summary), indent=2))
    print(f"Saved live realtime protocol comparison to: {summary_path}")


if __name__ == "__main__":
    main()
