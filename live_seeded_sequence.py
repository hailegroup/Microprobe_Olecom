# -*- coding: utf-8 -*-
"""Live-stopped CP/CA FFT seeded PEIS sequence for GUI/CSV automation.

This is the production wrapper around the optimized debugging protocol:
stable pre-bias CA -> live-stopped dV scout CA -> FFT LF recommendation ->
PEIS using the recommended LF with measured-PEIS overlap preferred.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from measurement_sequence import SequenceResult


PROJECT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = PROJECT_DIR / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_continuous_prepost_seeded_reference as seeded  # type: ignore  # noqa: E402


def _safe_label(label: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._+-" else "_" for ch in str(label).strip())
    return safe or "live_seeded"


def _emit(callback, *, event: str, label: str, **payload) -> None:
    if callback is not None:
        callback(event=event, label=label, **payload)


def live_seeded_rapid_eis_sequence(
    *,
    biologic,
    v_dc: float,
    dv: float = 0.03,
    pre_min: float = 60.0,
    pre_max: float = 900.0,
    scout_min: float = 120.0,
    scout_max: float = 1200.0,
    ca_dt: float = 0.1,
    channel: int = 1,
    peis_f_high: float = 1.0e6,
    peis_floor: float = 0.5,
    peis_deep_limit: float = 0.05,
    peis_npts: int = 60,
    peis_amplitude_mv: float = 10.0,
    post_stable_buffer: float = 20.0,
    bandwidth: str | None = "BW4",
    current_range: str | None = "AUTO",
    fit_model: str = "RQRQRQ",
    save_dir: str | os.PathLike | None = None,
    label: str = "",
    monitor_callback=None,
    stop_event=None,
) -> SequenceResult:
    """Run the optimized live-FFT seeded rapid-EIS measurement.

    ``peis_floor`` is the overlap policy: measure PEIS at least this low unless
    FFT asks for deeper, bounded by ``peis_deep_limit``.
    """

    label = str(label or "live_seeded")
    root = Path(save_dir) if save_dir else PROJECT_DIR / "results"
    out_dir = root / f"{_safe_label(label)}_live_seeded_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    bandwidth_override = seeded._resolve_bandwidth(bandwidth)
    if bandwidth and bandwidth_override is None:
        raise ValueError(f"Unknown BioLogic bandwidth setting: {bandwidth!r}")
    current_range_override = seeded._resolve_current_range(current_range)
    if current_range and current_range_override is None:
        raise ValueError(f"Unknown BioLogic current range setting: {current_range!r}")

    summary = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "output_dir": str(out_dir),
        "protocol": "live_seeded_rapid_eis",
        "settings": {
            "bias_v": float(v_dc),
            "dv_v": float(dv),
            "channel": int(channel),
            "pre_min_s": float(pre_min),
            "pre_max_s": float(pre_max),
            "scout_min_s": float(scout_min),
            "scout_max_s": float(scout_max),
            "ca_dt_s": float(ca_dt),
            "peis_high_hz": float(peis_f_high),
            "peis_floor_hz": float(peis_floor),
            "peis_deep_limit_hz": float(peis_deep_limit),
            "peis_npts": int(peis_npts),
            "peis_amplitude_mv": float(peis_amplitude_mv),
            "post_stable_buffer_s": float(post_stable_buffer),
            "bandwidth": None if bandwidth is None else str(bandwidth).upper(),
            "current_range": None if current_range is None else str(current_range),
            "fit_model": str(fit_model),
        },
    }

    def _check_stop() -> None:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Live seeded measurement stopped")

    try:
        _emit(monitor_callback, event="step", label=label, step="live_pre_ca", message="Live pre-CA stabilization")
        seeded._write_stage_marker(out_dir, "starting_pre")
        pre = seeded._run_continuous_pre_hold(
            biologic,
            v_dc=float(v_dc),
            min_duration_s=float(pre_min),
            max_duration_s=float(pre_max),
            dt_record_s=float(ca_dt),
            read_interval_s=0.2,
            channel=int(channel),
            bandwidth=bandwidth_override,
            current_range=current_range_override,
            post_stable_buffer_s=float(post_stable_buffer),
        )
        pre_path = out_dir / "pre_ca_continuous.txt"
        seeded._save_txt(pre_path, pre["data"], "time/s  V/V  I/A")
        seeded._write_stage_marker(out_dir, "pre_saved", rows=int(len(pre["data"])), runtime_s=float(pre["runtime_s"]))
        _emit(monitor_callback, event="dc_data", label=label, step="live_pre_ca_done", data=pre["data"])
        _check_stop()

        _emit(monitor_callback, event="step", label=label, step="live_dv_scout", message="Live dV scout CA")
        scout = seeded._run_continuous_dv_scout(
            biologic,
            v_dc=float(v_dc),
            dv_v=float(dv),
            min_duration_s=float(scout_min),
            max_duration_s=float(scout_max),
            dt_record_s=float(ca_dt),
            read_interval_s=0.2,
            channel=int(channel),
            bandwidth=bandwidth_override,
            current_range=current_range_override,
        )
        scout_path = out_dir / "dv_scout_continuous.txt"
        seeded._save_txt(scout_path, scout["data"], "time/s  V/V  I/A")
        seeded._write_stage_marker(out_dir, "scout_saved", rows=int(len(scout["data"])), runtime_s=float(scout["runtime_s"]))
        _emit(monitor_callback, event="dc_data", label=label, step="live_dv_scout_done", data=scout["data"])
        _check_stop()

        try:
            biologic.stop_measurement(channel=int(channel))
            summary["post_scout_stop_request"] = {"performed": True, "error": None}
        except Exception as exc:
            summary["post_scout_stop_request"] = {"performed": False, "error": str(exc)}
        time.sleep(0.5)

        combined_raw = seeded._concat_pre_post(pre["data"], scout["data"])
        combined_raw_path = out_dir / "combined_prepost_raw.txt"
        seeded._save_txt(combined_raw_path, combined_raw, "time/s  V/V  I/A")

        combined_trimmed, trim_start_s, trim_method = seeded._trim_stable_pretail_plus_post(
            pre["data"],
            scout["data"],
            stable_detected_hold_s=pre.get("stable_detected_hold_s"),
        )
        combined_trimmed_path = out_dir / "combined_prepost_trimmed.txt"
        seeded._save_txt(combined_trimmed_path, combined_trimmed, "time/s  V/V  I/A")

        fft_ready = seeded.build_fft_ready_ca_trace(
            combined_trimmed,
            average_bin_s=None,
            segmented_smoothing_window_s=None,
        )
        fft_ready_path = out_dir / "combined_prepost_fft_ready.txt"
        seeded._save_txt(fft_ready_path, fft_ready, "time/s  V/V  I/A")
        seeded._write_stage_marker(out_dir, "fft_ready_saved", rows=int(len(fft_ready)), trim_method=trim_method, trim_start_s=trim_start_s)

        seed_rec = seeded.recommend_peis_lf_from_cp_txt(str(fft_ready_path), current_in_mA=False)
        raw_seed_lf = float(seed_rec["recommendation"]["recommended_peis_lowest_freq_hz"])
        if not np.isfinite(raw_seed_lf):
            applied_peis_lf = float(peis_floor)
        else:
            applied_peis_lf = float(max(min(raw_seed_lf, float(peis_floor)), float(peis_deep_limit)))

        summary["seed_recommendation"] = {
            "raw_recommended_lf_hz": raw_seed_lf,
            "applied_peis_lf_hz": applied_peis_lf,
            "peis_minimum_depth_hz": float(peis_floor),
            "peis_deep_limit_hz": float(peis_deep_limit),
            "cp_saturation": seeded._json_safe(seed_rec["cp_saturation"]),
            "smoothed_duration_s": float(seed_rec["smoothed_duration_s"]),
        }
        seeded._write_stage_marker(out_dir, "seed_recommendation_done", **summary["seed_recommendation"])
        _check_stop()

        handoff = seeded._force_biologic_idle_for_handoff(
            biologic,
            channel=int(channel),
            initial_timeout_s=5.0,
            reconnect_timeout_s=20.0,
        )
        summary["pre_peis_handoff"] = seeded._json_safe(handoff)
        seeded._write_stage_marker(out_dir, "starting_peis", seed_lf_hz=applied_peis_lf, high_hz=float(peis_f_high))
        _emit(
            monitor_callback,
            event="step",
            label=label,
            step="seeded_peis",
            message=f"PEIS from {float(peis_f_high):.3g} Hz to {applied_peis_lf:.3g} Hz",
        )
        peis_data = biologic.run_peis(
            v_dc=float(v_dc),
            f_high=float(peis_f_high),
            f_low=float(applied_peis_lf),
            n_pts=int(peis_npts),
            amplitude_mv=float(peis_amplitude_mv),
            channel=int(channel),
            bandwidth=bandwidth_override,
            current_range=current_range_override,
        )
        peis_path = out_dir / "seeded_peis.txt"
        seeded._save_txt(peis_path, peis_data, "freq/Hz  Re(Z)/Ohm  -Im(Z)/Ohm")
        seeded._write_stage_marker(out_dir, "peis_saved", rows=int(len(peis_data)))
        _emit(monitor_callback, event="eis_data", label=label, step="seeded_peis_done", data=peis_data)

        analysis_result, visuals = seeded.analyze_measurement_files_with_visuals(
            str(fft_ready_path),
            str(peis_path),
            sample_name=f"{label}_live_seeded",
        )
        peis_loaded = seeded._load_eis(peis_path)
        merged_cutoff = seeded._resolve_overlap_cutoff(
            peis_loaded,
            analysis_result.recommended_peis_lowest_freq_hz,
        )
        merged_f, merged_z = seeded._merge_peis_and_fft(
            peis_loaded,
            np.asarray(seed_rec["analysis_f"], dtype=float),
            np.asarray(seed_rec["analysis_z"], dtype=complex),
            cutoff_hz=merged_cutoff,
        )
        if fit_model == "auto":
            merged_fit = seeded.EF.fit_equivalent_circuit_auto(
                merged_f,
                np.real(merged_z),
                np.imag(merged_z),
            )
        else:
            merged_fit = seeded.EF.fit_circuit_model_stable(
                merged_f,
                np.real(merged_z),
                np.imag(merged_z),
                model=str(fit_model),
            )

        full_fit_path = out_dir / "live_seeded_full_fit.png"
        seeded._plot_full_fit(merged_f, merged_z, merged_fit, full_fit_path, title=f"{label} bias={float(v_dc):+.3f} V")
        measured_peis_path = out_dir / "live_seeded_measured_peis.png"
        seeded._plot_measured_peis(peis_loaded, measured_peis_path, title=f"{label} measured PEIS")
        fft_input_segment_path = out_dir / "live_seeded_fft_input_segment.png"
        seeded._plot_fft_input_segment(combined_raw, combined_trimmed, fft_input_segment_path, title=f"{label} FFT input")

        summary.update({
            "pre": {
                "runtime_s": pre["runtime_s"],
                "rows": int(len(pre["data"])),
                "stopped_by_stability": pre["stopped_by_stability"],
                "stable_detected_hold_s": pre.get("stable_detected_hold_s"),
                "assessment": seeded._json_safe(pre["assessment"]),
                "path": str(pre_path),
            },
            "scout": {
                "runtime_s": scout["runtime_s"],
                "rows": int(len(scout["data"])),
                "stopped_by_cp_saturation": scout["stopped_by_cp_saturation"],
                "assessment": seeded._json_safe(scout["assessment"]),
                "path": str(scout_path),
            },
            "combined": {
                "raw_path": str(combined_raw_path),
                "trimmed_path": str(combined_trimmed_path),
                "fft_ready_path": str(fft_ready_path),
                "trim_start_s": trim_start_s,
                "trim_method": trim_method,
                "trimmed_rows": int(len(combined_trimmed)),
                "fft_ready_rows": int(len(fft_ready)),
            },
            "peis": {
                "rows": int(len(peis_data)),
                "path": str(peis_path),
                "f_low_hz": applied_peis_lf,
                "f_high_hz": float(peis_f_high),
            },
            "analysis_result": seeded._json_safe(analysis_result.__dict__),
            "visuals": seeded._json_safe(visuals),
            "merged_fit": seeded._json_safe(merged_fit),
            "merged_cutoff_hz": float(merged_cutoff),
            "plots": {
                "full_fit": str(full_fit_path),
                "measured_peis": str(measured_peis_path),
                "fft_input_segment": str(fft_input_segment_path),
            },
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as exc:
        summary["error"] = repr(exc)
        summary["failed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        raise
    finally:
        summary_path = out_dir / "live_seeded_summary.json"
        summary_path.write_text(json.dumps(seeded._json_safe(summary), indent=2), encoding="utf-8")

    result = SequenceResult(
        measurement_mode="rapid_eis",
        eis_data=peis_data,
        ca_data=fft_ready,
        pre_ca_data=pre["data"],
        pre_ca_path=str(pre_path),
        peis_path=str(peis_path),
        post_ca_path=str(fft_ready_path),
    )
    result.output_dir = str(out_dir)
    result.summary_path = str(summary_path)
    result.applied_peis_lf_hz = float(applied_peis_lf)
    result.raw_recommended_lf_hz = float(raw_seed_lf)
    result.full_fit_path = str(full_fit_path)
    return result
