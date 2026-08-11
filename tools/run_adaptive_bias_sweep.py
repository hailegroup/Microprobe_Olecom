# -*- coding: utf-8 -*-
"""
Run a real adaptive bias sweep against the connected BioLogic instrument.

This helper is meant for lab validation of the current adaptive-runtime logic.
It keeps the first point conservative, then follows adaptive recommendations
while optionally forcing periodic rapid/hybrid refresh points so that both
normal-EIS and hybrid paths are exercised in the same sweep.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from adaptive_engine import AdaptiveEngineSettings, AdaptiveMeasurementEngine
from adaptive_types import MeasurementExecution, MeasurementFiles, PointMetadata
from driver_biologic import BioLogicController
from measurement_sequence import normal_eis_sequence, rapid_eis_sequence


def _bias_label(v):
    return f"{v:+0.3f}".replace("+", "p").replace("-", "m")


def _result_dir(root: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = root / f"adaptive_bias_sweep_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _point_metadata(index: int, bias_v: float) -> PointMetadata:
    label = f"ADAPT_E1_V{_bias_label(bias_v)}"
    return PointMetadata(
        point_id=f"row{index:04d}_{label}",
        label=label,
        electrode_id=1,
        temperature_c=None,
        gas_a_sccm=None,
        gas_b_sccm=None,
        voltage_v=float(bias_v),
        sequence_index=index,
    )


def _write_summary(path: Path, payload: dict):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _backfill_pending_summary(engine, summary: dict, *, timeout_s: float = 15.0) -> int:
    """
    Refresh per-point finalize snapshots after the live loop.

    Some rows can finish measurement before the background analysis thread
    completes, so the first written summary snapshot may still show
    `analysis_pending`. This helper waits briefly at the end of the sweep and
    rewrites those per-point finalize payloads from the current engine state.
    """
    updated = 0
    point_summaries = summary.get("points", [])
    if not point_summaries:
        return updated

    deadline = time.time() + max(float(timeout_s), 0.0)
    for point_summary in point_summaries:
        point_id = point_summary.get("point_id")
        if not point_id:
            continue
        previous_finalize = point_summary.get("analysis_finalize") or {}
        remaining = max(0.0, deadline - time.time())
        refreshed_finalize = engine.finalize_completed_point(point_id, timeout_s=remaining)
        point_summary["analysis_finalize"] = refreshed_finalize
        if previous_finalize != refreshed_finalize:
            updated += 1
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--biases",
        default="-0.3,-0.2,-0.1,0.0,0.1,0.2,0.3",
        help="Comma-separated bias list in volts.",
    )
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--peis-f-high", type=float, default=1e5)
    parser.add_argument("--peis-f-low", type=float, default=1.0)
    parser.add_argument("--peis-npts", type=int, default=20)
    parser.add_argument("--hold-time", type=float, default=2.0)
    parser.add_argument("--post-peis-hold", type=float, default=2.0)
    parser.add_argument("--ca-duration", type=float, default=6.0)
    parser.add_argument("--ca-dt", type=float, default=0.1)
    parser.add_argument("--normal-eis-floor-hz", type=float, default=0.01)
    parser.add_argument(
        "--force-rapid-every",
        type=int,
        default=2,
        help="Force a rapid/hybrid refresh on every Nth point after the first. 0 disables forcing.",
    )
    parser.add_argument(
        "--result-root",
        default=str(PROJECT_DIR / "results"),
    )
    parser.add_argument(
        "--final-backfill-timeout",
        type=float,
        default=15.0,
        help="Extra end-of-run wait used to refresh pending adaptive-analysis summary entries.",
    )
    args = parser.parse_args()

    biases = [float(token.strip()) for token in args.biases.split(",") if token.strip()]
    result_root = Path(args.result_root)
    sweep_dir = _result_dir(result_root)
    summary_path = sweep_dir / "adaptive_bias_sweep_summary.json"

    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "biases_v": biases,
        "settings": {
            "dv_v": args.dv,
            "peis_f_high_hz": args.peis_f_high,
            "peis_f_low_hz": args.peis_f_low,
            "peis_npts": args.peis_npts,
            "hold_time_s": args.hold_time,
            "post_peis_hold_time_s": args.post_peis_hold,
            "ca_duration_s": args.ca_duration,
            "ca_dt_s": args.ca_dt,
            "normal_eis_floor_hz": args.normal_eis_floor_hz,
            "force_rapid_every": args.force_rapid_every,
        },
        "points": [],
        "mode_counts": {},
    }
    _write_summary(summary_path, summary)

    engine = AdaptiveMeasurementEngine(
        engine_settings=AdaptiveEngineSettings(
            normal_eis_floor_hz=args.normal_eis_floor_hz,
        )
    )
    last_rapid_settings = {
        "peis_low_hz": float(args.peis_f_low),
        "cp_duration_s": float(args.ca_duration),
        "hold_time_s": float(args.hold_time),
        "post_hold_time_s": float(args.post_peis_hold),
    }
    biologic = BioLogicController()
    biologic.connect()
    print(f"[SWEEP] Connected to BioLogic. Results -> {sweep_dir}")

    try:
        for idx, bias_v in enumerate(biases, start=1):
            point = _point_metadata(idx, bias_v)

            if idx == 1:
                recommendation = None
                measurement_mode = "rapid_eis"
                peis_low = float(args.peis_f_low)
                cp_duration = float(args.ca_duration)
                hold_time = float(args.hold_time)
                post_hold = float(args.post_peis_hold)
                forced_rapid = False
                reason = "first point stays conservative"
            else:
                recommendation = engine.recommend_next_point(point)
                measurement_mode = recommendation.measurement_mode
                peis_low = float(recommendation.peis_lowest_freq_hz)
                cp_duration = float(recommendation.cp_duration_s)
                hold_time = float(recommendation.planned_pre_peis_hold_s)
                post_hold = float(recommendation.planned_post_peis_hold_s)
                forced_rapid = bool(args.force_rapid_every and idx > 1 and ((idx - 1) % args.force_rapid_every == 0))
                reason = "adaptive recommendation"
                if forced_rapid:
                    measurement_mode = "rapid_eis"
                    if cp_duration <= 0:
                        cp_duration = float(last_rapid_settings["cp_duration_s"])
                    if hold_time <= 0:
                        hold_time = float(last_rapid_settings["hold_time_s"])
                    if post_hold <= 0:
                        post_hold = float(last_rapid_settings["post_hold_time_s"])
                    reason = "forced rapid refresh"

            print(
                f"[SWEEP] {idx}/{len(biases)} {point.label}: "
                f"mode={measurement_mode}, Vdc={bias_v:+.3f} V, "
                f"f_low={peis_low:.4g} Hz, hold={hold_time:.2f}s, post={post_hold:.2f}s, "
                f"CA={cp_duration:.2f}s ({reason})"
            )

            point_dir = sweep_dir / f"{idx:02d}_{point.label}"
            point_dir.mkdir(parents=True, exist_ok=True)
            started_at = time.time()

            if measurement_mode == "normal_eis":
                sequence_result = normal_eis_sequence(
                    biologic=biologic,
                    v_dc=bias_v,
                    peis_f_high=args.peis_f_high,
                    peis_f_low=peis_low,
                    peis_npts=args.peis_npts,
                    amplitude_mv=args.dv * 1000.0,
                    save_dir=str(point_dir),
                    label=point.label,
                )
            else:
                sequence_result = rapid_eis_sequence(
                    biologic=biologic,
                    v_dc=bias_v,
                    dv=args.dv,
                    hold_time=hold_time,
                    post_peis_hold_time=post_hold,
                    peis_f_high=args.peis_f_high,
                    peis_f_low=peis_low,
                    peis_npts=args.peis_npts,
                    ca_duration=cp_duration,
                    ca_dt=args.ca_dt,
                    save_dir=str(point_dir),
                    label=point.label,
                )

            measurement = MeasurementExecution(
                measurement_mode=measurement_mode,
                peis_lowest_freq_hz=peis_low,
                cp_duration_s=None if measurement_mode == "normal_eis" else cp_duration,
                planned_pre_peis_hold_s=hold_time,
                planned_post_peis_hold_s=post_hold,
                actual_peis_high_freq_hz=args.peis_f_high,
                actual_peis_npts=args.peis_npts,
                dv_v=args.dv,
                pre_hold_time_s=hold_time,
                post_peis_hold_time_s=post_hold,
            )
            files = MeasurementFiles(
                pre_ca_path=getattr(sequence_result, "pre_ca_path", None),
                peis_path=getattr(sequence_result, "peis_path", None),
                post_ca_path=getattr(sequence_result, "post_ca_path", None),
            )
            engine.register_point(point, measurement, files)

            finalize = None
            if files.peis_path:
                engine.start_post_measurement_pipeline(
                    point.point_id,
                    start_full_processing=(measurement_mode == "rapid_eis" and bool(files.post_ca_path or files.pre_ca_path)),
                )
                finalize = engine.finalize_completed_point(point.point_id, timeout_s=10.0)
            else:
                finalize = {
                    "analysis_ready_within_wait": False,
                    "analysis_result": None,
                    "state": "missing_peis_file",
                    "full_processing_state": None,
                    "remeasurement_request": None,
                }

            next_recommendation = None
            if idx < len(biases):
                next_point = _point_metadata(idx + 1, biases[idx])
                next_recommendation = engine.recommend_next_point(next_point).to_dict()

            point_summary = {
                "index": idx,
                "point_id": point.point_id,
                "label": point.label,
                "bias_v": bias_v,
                "measurement_mode": measurement_mode,
                "forced_rapid_refresh": forced_rapid,
                "reason": reason,
                "used_parameters": {
                    "peis_f_low_hz": peis_low,
                    "hold_time_s": hold_time,
                    "post_peis_hold_time_s": post_hold,
                    "ca_duration_s": None if measurement_mode == "normal_eis" else cp_duration,
                },
                "recommendation_used": None if recommendation is None else recommendation.to_dict(),
                "analysis_finalize": finalize,
                "next_recommendation": next_recommendation,
                "measurement_elapsed_s": round(time.time() - started_at, 3),
                "files": asdict(files),
            }
            summary["points"].append(point_summary)
            summary["mode_counts"][measurement_mode] = summary["mode_counts"].get(measurement_mode, 0) + 1
            if measurement_mode == "rapid_eis":
                last_rapid_settings = {
                    "peis_low_hz": peis_low,
                    "cp_duration_s": cp_duration,
                    "hold_time_s": hold_time,
                    "post_hold_time_s": post_hold,
                }
            _write_summary(summary_path, summary)

            analysis_result = (finalize or {}).get("analysis_result") or {}
            if analysis_result:
                print(
                    f"  -> analysis: peis_only={analysis_result.get('peis_only_sufficient')}, "
                    f"reason={analysis_result.get('peis_only_selection_reason')}, "
                    f"agreement={analysis_result.get('agreement_rel_err')}, "
                    f"cp_sat={analysis_result.get('cp_saturation_reached')}"
                )
            if next_recommendation:
                print(
                    f"  -> next: mode={next_recommendation.get('measurement_mode')}, "
                    f"f_low={next_recommendation.get('peis_lowest_freq_hz')}, "
                    f"CA={next_recommendation.get('cp_duration_s')}"
                )

    finally:
        refreshed = _backfill_pending_summary(
            engine,
            summary,
            timeout_s=args.final_backfill_timeout,
        )
        if refreshed:
            print(f"[SWEEP] Backfilled {refreshed} point summary entr{'y' if refreshed == 1 else 'ies'} after pending analysis completed.")
        engine.shutdown()
        biologic.disconnect()
        summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_summary(summary_path, summary)
        print(f"[SWEEP] Finished. Summary -> {summary_path}")


if __name__ == "__main__":
    main()
