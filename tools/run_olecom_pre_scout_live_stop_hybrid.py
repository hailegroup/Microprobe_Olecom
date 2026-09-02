from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from cp_first_policy import recommend_peis_lf_from_cp_txt  # noqa: E402
from run_olecom_pre_scout_post_hybrid import (  # noqa: E402
    OleComController,
    _ca_sequence_continuity_metrics,
    _clamp_peis_lf,
    _cleanup_eclab_processes,
    _fit_and_plot,
    _fmt_duration,
    _mps_row,
    _parse_mpr_dc,
    _parse_mpr_eis,
    _plot_ca_sequence_continuity,
    _plot_live_ca_snapshot,
    _plot_onepage,
    _records_to_array,
    _require_mps_fields,
    _save_ca_txt,
    _save_fft_ready_trace_from_recommendation,
    _select_pre_tail_plus_scout,
    _safe_point_count,
    _wait_for_point_count_stable,
    _wait_for_run_completion,
    _write_live_status,
    _write_peis_mps,
)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_pre_scout_ca_mps(
    path: Path,
    *,
    bias_v: float,
    dv_v: float,
    pre_s: float,
    scout_s: float,
    dt_s: float,
    bandwidth: int,
    i_range: str = "Auto",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    v0 = f"{float(bias_v):.3f}"
    v1 = f"{float(bias_v + dv_v):.3f}"
    text = [
        "EC-LAB SETTING FILE\n\n",
        "Number of linked techniques : 1\n\n",
        "EC-LAB for windows v11.61 (software)\n",
        "Internet server v11.61 (firmware)\n",
        "Command interpretor v11.61 (firmware)\n\n",
        f"Filename : {path}\n\n",
        "Device : SP-200\n",
        "Electrode connection : standard\n",
        "Potential control : Ewe\n",
        "Ewe ctrl range : min = -2.50 V, max = 2.50 V\n",
        "Ewe,I filtering : 50 kHz\n",
        "Safety Limits :\n",
        "\tDo not start on E overload\n",
        "Channel : Grounded\n",
        "Cable : standard\n",
        "Electrode surface area : 0.001 cm2\n",
        "Characteristic mass : 0.001 g\n",
        "Equivalent Weight : 0.000 g/eq.\n",
        "Density : 0.000 g/cm3\n",
        "Volume (V) : 0.001 cm3\n",
        "Cycle Definition : Charge/Discharge alternance\n",
        "Do not turn to OCV between techniques\n\n",
        "Technique : 1\n",
        "Chronoamperometry / Chronocoulometry\n",
        _mps_row("Ns", [0, 1]),
        _mps_row("Ei (V)", [v0, v1]),
        _mps_row("vs.", ["Ref", "Ref"]),
        _mps_row("ti (h:m:s)", [_fmt_duration(pre_s), _fmt_duration(scout_s)]),
        _mps_row("Imax", ["pass", "pass"]),
        _mps_row("unit Imax", ["mA", "mA"]),
        _mps_row("Imin", ["pass", "pass"]),
        _mps_row("unit Imin", ["mA", "mA"]),
        _mps_row("dQM", ["0.000", "0.000"]),
        _mps_row("unit dQM", ["mA.h", "mA.h"]),
        _mps_row("record", ["<I>", "<I>"]),
        _mps_row("dI", ["5.000", "5.000"]),
        _mps_row("unit dI", ["\xb5A", "\xb5A"]),
        _mps_row("dQ", ["0.000", "0.000"]),
        _mps_row("unit dQ", ["mA.h", "mA.h"]),
        _mps_row("dt (s)", [f"{dt_s:.4f}", f"{dt_s:.4f}"]),
        _mps_row("dta (s)", [f"{dt_s:.4f}", f"{dt_s:.4f}"]),
        _mps_row("E range min (V)", ["-2.500", "-2.500"]),
        _mps_row("E range max (V)", ["2.500", "2.500"]),
        _mps_row("I Range", [i_range, i_range]),
        _mps_row("I Range min", ["Unset", "Unset"]),
        _mps_row("I Range max", ["Unset", "Unset"]),
        _mps_row("I Range init", ["Unset", "Unset"]),
        _mps_row("Bandwidth", [bandwidth, bandwidth]),
        _mps_row("goto Ns'", [0, 0]),
        _mps_row("nc cycles", [0, 0]),
    ]
    path.write_text("".join(text), encoding="latin1")
    return path


def _write_single_hold_ca_mps(
    path: Path,
    *,
    potential_v: float,
    duration_s: float,
    dt_s: float,
    bandwidth: int,
    i_range: str = "Auto",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    v = f"{float(potential_v):.3f}"
    text = [
        "EC-LAB SETTING FILE\n\n",
        "Number of linked techniques : 1\n\n",
        "EC-LAB for windows v11.61 (software)\n",
        "Internet server v11.61 (firmware)\n",
        "Command interpretor v11.61 (firmware)\n\n",
        f"Filename : {path}\n\n",
        "Device : SP-200\n",
        "Electrode connection : standard\n",
        "Potential control : Ewe\n",
        "Ewe ctrl range : min = -2.50 V, max = 2.50 V\n",
        "Ewe,I filtering : 50 kHz\n",
        "Safety Limits :\n",
        "\tDo not start on E overload\n",
        "Channel : Grounded\n",
        "Cable : standard\n",
        "Electrode surface area : 0.001 cm2\n",
        "Characteristic mass : 0.001 g\n",
        "Equivalent Weight : 0.000 g/eq.\n",
        "Density : 0.000 g/cm3\n",
        "Volume (V) : 0.001 cm3\n",
        "Cycle Definition : Charge/Discharge alternance\n",
        "Do not turn to OCV between techniques\n\n",
        "Technique : 1\n",
        "Chronoamperometry / Chronocoulometry\n",
        "Comments : OLE-COM separate post bias hold\n",
        _mps_row("Ns", [2]),
        _mps_row("Ei (V)", [v]),
        _mps_row("vs.", ["Ref"]),
        _mps_row("ti (h:m:s)", [_fmt_duration(duration_s)]),
        _mps_row("Imax", ["pass"]),
        _mps_row("unit Imax", ["mA"]),
        _mps_row("Imin", ["pass"]),
        _mps_row("unit Imin", ["mA"]),
        _mps_row("dQM", ["0.000"]),
        _mps_row("unit dQM", ["mA.h"]),
        _mps_row("record", ["<I>"]),
        _mps_row("dI", ["5.000"]),
        _mps_row("unit dI", ["uA"]),
        _mps_row("dQ", ["0.000"]),
        _mps_row("unit dQ", ["mA.h"]),
        _mps_row("dt (s)", [f"{dt_s:.4f}"]),
        _mps_row("dta (s)", [f"{dt_s:.4f}"]),
        _mps_row("E range min (V)", ["-2.500"]),
        _mps_row("E range max (V)", ["2.500"]),
        _mps_row("I Range", [i_range]),
        _mps_row("I Range min", ["Unset"]),
        _mps_row("I Range max", ["Unset"]),
        _mps_row("I Range init", ["Unset"]),
        _mps_row("Bandwidth", [bandwidth]),
        _mps_row("goto Ns'", [0]),
        _mps_row("nc cycles", [0]),
    ]
    path.write_text("".join(text), encoding="latin1")
    return path


def _append_post_for_display(pre_scout: np.ndarray, post: np.ndarray) -> np.ndarray:
    left = np.asarray(pre_scout, dtype=float)
    right = np.asarray(post, dtype=float)
    if left.ndim != 2 or left.size == 0:
        return right
    if right.ndim != 2 or right.size == 0:
        return left
    out = right.copy()
    dt = float(np.nanmedian(np.diff(left[:, 0]))) if len(left) > 1 else 0.1
    if not np.isfinite(dt) or dt <= 0:
        dt = 0.1
    out[:, 0] = out[:, 0] - out[0, 0] + left[-1, 0] + dt
    out[:, 3] = 2.0
    return np.vstack([left, out])


def _robust_tail_metrics(segment: np.ndarray, *, window_s: float, scale_a: float) -> dict:
    seg = np.asarray(segment, dtype=float)
    if seg.ndim != 2 or seg.shape[0] < 6 or seg.shape[1] < 3:
        return {
            "valid": False,
            "reason": "insufficient_points",
        }
    t = np.asarray(seg[:, 0], dtype=float)
    i = np.asarray(seg[:, 2], dtype=float)
    finite = np.isfinite(t) & np.isfinite(i)
    t = t[finite]
    i = i[finite]
    if len(t) < 6:
        return {
            "valid": False,
            "reason": "insufficient_finite_points",
        }
    t = t - float(t[0])
    duration = float(t[-1] - t[0])
    win = min(float(window_s), max(duration / 3.0, 1.0))
    if duration < max(2.0 * win, 3.0):
        return {
            "valid": False,
            "reason": "duration_shorter_than_two_windows",
            "duration_s": duration,
            "tail_window_s": win,
        }
    tail_mask = t >= duration - win
    prev_mask = (t >= duration - 2.0 * win) & (t < duration - win)
    tail = i[tail_mask]
    prev = i[prev_mask]
    tt = t[tail_mask]
    if len(tail) < 3 or len(prev) < 3 or len(tt) < 3:
        return {
            "valid": False,
            "reason": "tail_window_insufficient_points",
            "duration_s": duration,
            "tail_window_s": win,
        }
    scale = float(scale_a)
    if not np.isfinite(scale) or scale <= 0:
        p95, p05 = np.nanpercentile(i, [95, 5])
        scale = float(max(abs(np.nanmedian(tail)), abs(p95 - p05), 1e-12))
    slope = float(np.polyfit(tt - float(tt[0]), tail, 1)[0]) if len(tt) >= 3 else 0.0
    tail_median = float(np.nanmedian(tail))
    prev_median = float(np.nanmedian(prev))
    tail_mean_shift = float(abs(tail_median - prev_median))
    tail_mean_shift_rel = float(tail_mean_shift / max(scale, 1e-12))
    tail_rel_slope_per_s = float(abs(slope) / max(scale, 1e-12))
    tail_rel_change_window = float(abs(slope) * win / max(scale, 1e-12))
    return {
        "valid": True,
        "duration_s": duration,
        "tail_window_s": win,
        "tail_scale_a": scale,
        "tail_current_a": tail_median,
        "prev_tail_current_a": prev_median,
        "tail_mean_shift_a": tail_mean_shift,
        "tail_mean_shift_rel": tail_mean_shift_rel,
        "tail_slope_a_per_s": slope,
        "tail_rel_slope_per_s": tail_rel_slope_per_s,
        "tail_rel_change_window": tail_rel_change_window,
    }


def _estimate_pre_stabilization(ca_all: np.ndarray, *, window_s: float = 10.0, rel_limit: float = 0.02) -> dict:
    arr = np.asarray(ca_all, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 6 or arr.shape[1] < 3:
        return {"valid": False, "reason": "insufficient_points"}
    if arr.shape[1] >= 4:
        pre = arr[np.isclose(arr[:, 3], 0)]
    else:
        pre = arr
    if len(pre) < 6:
        return {"valid": False, "reason": "no_pre_segment"}
    pre = np.asarray(pre, dtype=float)
    t = pre[:, 0] - float(pre[0, 0])
    i = pre[:, 2]
    finite = np.isfinite(t) & np.isfinite(i)
    t = t[finite]
    i = i[finite]
    if len(t) < 6:
        return {"valid": False, "reason": "insufficient_finite_points"}
    duration = float(t[-1] - t[0])
    p95, p05 = np.nanpercentile(i, [95, 5])
    scale = float(max(abs(np.nanmedian(i[-max(3, len(i) // 10):])), abs(p95 - p05), 1e-12))
    win = min(float(window_s), max(duration / 3.0, 1.0))
    stable_time = None
    stable_shift_rel = None
    stable_rel_change = None
    # Learn the earliest time where both the preceding and current windows are quiet.
    for t_end in t:
        if t_end < 2.0 * win:
            continue
        cur_mask = (t >= t_end - win) & (t <= t_end)
        prev_mask = (t >= t_end - 2.0 * win) & (t < t_end - win)
        if np.sum(cur_mask) < 3 or np.sum(prev_mask) < 3:
            continue
        cur = i[cur_mask]
        prev = i[prev_mask]
        tt = t[cur_mask]
        shift_rel = float(abs(np.nanmedian(cur) - np.nanmedian(prev)) / scale)
        slope = float(np.polyfit(tt - float(tt[0]), cur, 1)[0])
        rel_change = float(abs(slope) * win / scale)
        if shift_rel <= float(rel_limit) and rel_change <= float(rel_limit):
            stable_time = float(t_end)
            stable_shift_rel = shift_rel
            stable_rel_change = rel_change
            break
    tail = _robust_tail_metrics(pre[:, :3], window_s=window_s, scale_a=scale)
    return {
        "valid": True,
        "duration_s": duration,
        "stable_detected": stable_time is not None,
        "stable_time_s": stable_time,
        "stable_shift_rel": stable_shift_rel,
        "stable_rel_change_window": stable_rel_change,
        "rel_limit": float(rel_limit),
        "tail_metrics": tail,
    }


def _scout_live_guard(
    ca_all: np.ndarray,
    fft_summary: dict,
    *,
    min_fft_duration_s: float,
    tail_window_s: float,
    rel_shift_limit: float,
    rel_slope_limit: float,
) -> dict:
    arr = np.asarray(ca_all, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 6 or arr.shape[1] < 4:
        return {"ready": False, "reason": "invalid_ca_shape"}
    scout = arr[np.isclose(arr[:, 3], 1)]
    if len(scout) < 6:
        return {"ready": False, "reason": "no_scout_segment"}
    sat = dict(fft_summary.get("cp_saturation") or {})
    step_amp = sat.get("step_amplitude_a")
    try:
        scale = abs(float(step_amp))
    except Exception:
        scale = float("nan")
    metrics = _robust_tail_metrics(scout[:, :3], window_s=tail_window_s, scale_a=scale)
    fft_duration = fft_summary.get("fft_ready_duration_s")
    try:
        fft_duration = float(fft_duration)
    except Exception:
        fft_duration = float("nan")
    if not np.isfinite(fft_duration) or fft_duration < float(min_fft_duration_s):
        return {
            "ready": False,
            "reason": "fft_duration_too_short",
            "fft_ready_duration_s": fft_duration,
            "min_fft_duration_s": float(min_fft_duration_s),
            "tail_metrics": metrics,
        }
    if not metrics.get("valid"):
        return {
            "ready": False,
            "reason": metrics.get("reason", "tail_metrics_invalid"),
            "fft_ready_duration_s": fft_duration,
            "min_fft_duration_s": float(min_fft_duration_s),
            "tail_metrics": metrics,
        }
    shift_ok = float(metrics.get("tail_mean_shift_rel", float("inf"))) <= float(rel_shift_limit)
    slope_ok = float(metrics.get("tail_rel_change_window", float("inf"))) <= float(rel_slope_limit)
    ready = bool(shift_ok and slope_ok)
    reason = "tail_guard_clear" if ready else "tail_still_drifting"
    return {
        "ready": ready,
        "reason": reason,
        "fft_ready_duration_s": fft_duration,
        "min_fft_duration_s": float(min_fft_duration_s),
        "rel_shift_limit": float(rel_shift_limit),
        "rel_slope_limit": float(rel_slope_limit),
        "shift_ok": shift_ok,
        "slope_ok": slope_ok,
        "tail_metrics": metrics,
    }


def _analyze_fft(out_dir: Path, ca_all: np.ndarray, *, pre_tail_s: float, deep_limit: float, overlap_limit: float) -> tuple[dict, np.ndarray]:
    ca_fft, trim_source = _select_pre_tail_plus_scout(ca_all, pre_tail_s=pre_tail_s)
    raw_txt = _save_ca_txt(out_dir / "ca_for_fft_pre_plus_scout_only.txt", ca_fft)
    rec = recommend_peis_lf_from_cp_txt(raw_txt, current_in_mA=False)
    ca_fft_txt, ca_fft_plot, ca_fft_source = _save_fft_ready_trace_from_recommendation(
        out_dir=out_dir,
        rec=rec,
        raw_ca_txt=raw_txt,
        fallback_ca=ca_fft,
    )
    raw_lf = float(rec["recommendation"]["recommended_peis_lowest_freq_hz"])
    applied_lf = _clamp_peis_lf(raw_lf, deep_limit=deep_limit, overlap_limit=overlap_limit)
    summary = {
        "raw_recommended_lf_hz": raw_lf,
        "applied_peis_lf_hz": applied_lf,
        "fft_ready_duration_s": float(rec.get("smoothed_duration_s", np.nan)),
        "fft_ready_average_bin_s": rec.get("fft_ready_average_bin_s"),
        "fft_ready_current_despike": rec.get("fft_ready_current_despike"),
        "fft_ready_segmented_smoothing_window_points": rec.get("fft_ready_segmented_smoothing_window_points"),
        "fft_ready_segmented_smoothing_window_s": rec.get("fft_ready_segmented_smoothing_window_s"),
        "fft_ready_segmented_smoothing_step_guard_s": rec.get("fft_ready_segmented_smoothing_step_guard_s"),
        "fft_ready_segmented_smoothing_blend_s": rec.get("fft_ready_segmented_smoothing_blend_s"),
        "cp_saturation": rec.get("cp_saturation", {}),
        "ca_fft_raw_txt": str(raw_txt),
        "ca_fft_txt": str(ca_fft_txt),
        "ca_fft_source": ca_fft_source,
        "ca_fft_trim_source": trim_source,
        "ca_fft_pre_tail_s": float(pre_tail_s),
    }
    return summary, ca_fft_plot


def _estimate_peis_points(f_high_hz: float, f_low_hz: float, points_per_decade: int) -> int:
    if not np.isfinite(f_high_hz) or not np.isfinite(f_low_hz) or f_high_hz <= f_low_hz or f_low_hz <= 0:
        return 1
    return max(1, int(round(np.log10(float(f_high_hz) / float(f_low_hz)) * int(points_per_decade))))


def _safe_min_eis_freq(mpr_path: Path) -> float:
    try:
        data = _parse_mpr_eis(Path(mpr_path))
        if len(data) == 0:
            return float("nan")
        return float(np.nanmin(data[:, 0]))
    except Exception:
        return float("nan")


def _wait_peis_completion_or_stable(
    ctrl: OleComController,
    mpr_path: Path,
    *,
    timeout_s: float,
    startup_grace_s: float,
    expected_points: int,
    target_low_hz: float,
    stable_s: float,
    poll_s: float,
    monitor_callback=None,
) -> int:
    """PEIS can finish writing while OLE-COM MeasureStatus remains RUN."""
    def _emit(event, **payload):
        if monitor_callback:
            try:
                monitor_callback(event, **payload)
            except Exception:
                pass

    t0 = time.perf_counter()
    seen_points = False
    last_count = -1
    last_emitted_count = 0
    stable_since = time.perf_counter()
    rve_path = Path(mpr_path).with_suffix(".rve")
    while True:
        elapsed = time.perf_counter() - t0
        if elapsed > float(timeout_s):
            ctrl.stop()
            raise TimeoutError(f"PEIS exceeded timeout ({timeout_s:g} s)")
        running = ctrl.is_running()
        count = _safe_point_count(ctrl, mpr_path)
        seen_points = seen_points or count > 0
        if count != last_count:
            last_count = count
            stable_since = time.perf_counter()
        stable_for = time.perf_counter() - stable_since
        # EC-Lab/OLE-COM sometimes leaves MeasureStatus in RUN after the MPR
        # has stopped changing.  Do not use the RVE file alone as completion:
        # it can appear before the requested low-frequency end is reached.
        # Parsed once per poll cycle (only when the point count actually
        # changed) and reused for both the completion check and the live
        # monitor emission below, instead of parsing the growing .mpr twice.
        eis_live = None
        min_freq = float("nan")
        if count > 0 and count != last_emitted_count:
            try:
                eis_live = _parse_mpr_eis(mpr_path)
            except Exception:
                eis_live = None
            if eis_live is not None and len(eis_live):
                min_freq = float(np.nanmin(eis_live[:, 0]))
                _emit('eis_data', step='peis', data=eis_live)
                last_emitted_count = count
        elif count > 0:
            min_freq = _safe_min_eis_freq(mpr_path)
        enough_frequency = np.isfinite(min_freq) and min_freq <= max(float(target_low_hz) * 1.25, float(target_low_hz) + 1e-12)
        rve_done = rve_path.exists() and enough_frequency
        if not running and (seen_points or elapsed >= float(startup_grace_s)):
            time.sleep(0.5)
            return max(count, _safe_point_count(ctrl, mpr_path))
        if (rve_done or enough_frequency) and stable_for >= float(stable_s):
            # This avoids 30 min hangs when EC-Lab wrote the full PEIS MPR but
            # OLE-COM status never left RUN. StopChannel is harmless if already stopped.
            ctrl.stop()
            ctrl.wait_until_stopped(timeout_s=10.0)
            return max(count, _safe_point_count(ctrl, mpr_path))
        if elapsed >= float(startup_grace_s) and not seen_points:
            raise RuntimeError(f"PEIS never produced data points within {startup_grace_s:g} s")
        time.sleep(float(poll_s))


def run_once(args, ctrl=None, monitor_callback=None) -> dict:
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_DIR / "results" / f"olecom_pre_scout_live_stop_{_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "settings.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")

    external_ctrl = ctrl is not None
    if bool(args.auto_clean_eclab) and not external_ctrl:
        killed = _cleanup_eclab_processes(include_visible=bool(args.auto_clean_visible_eclab))
    else:
        killed = []

    ca_bandwidth = int(getattr(args, "ca_bandwidth", None) or args.bandwidth)
    ca_i_range = str(getattr(args, "ca_i_range", None) or "Auto")
    pre_scout_mps = _write_pre_scout_ca_mps(
        out_dir / "olecom_pre_scout_ca.mps",
        bias_v=args.bias,
        dv_v=args.dv,
        pre_s=args.pre_s,
        scout_s=args.scout_max_s,
        dt_s=args.dt,
        bandwidth=ca_bandwidth,
        i_range=ca_i_range,
    )
    generated_pre_scout_mps = out_dir / "olecom_pre_scout_ca.generated_before_load.mps"
    generated_pre_scout_mps.write_bytes(pre_scout_mps.read_bytes())
    _require_mps_fields(
        pre_scout_mps,
        [
            ("CA Ns 0/1", "Ns                  0                   1"),
            ("CA bias", f"{float(args.bias):.3f}"),
            ("CA scout", f"{float(args.bias + args.dv):.3f}"),
            ("CA I range", f"I Range             {ca_i_range}"),
            ("CA bandwidth", f"Bandwidth           {int(ca_bandwidth)}"),
            (
                "CA pre/scout duration",
                f"{'ti (h:m:s)':<20}{_fmt_duration(args.pre_s):<20}{_fmt_duration(args.scout_max_s):<20}",
            ),
        ],
    )
    post_mps = _write_single_hold_ca_mps(
        out_dir / "olecom_separate_post_hold.mps",
        potential_v=args.bias,
        duration_s=args.post_s,
        dt_s=args.dt,
        bandwidth=ca_bandwidth,
        i_range=ca_i_range,
    )
    _require_mps_fields(
        post_mps,
        [
            ("Post-hold bias", f"{float(args.bias):.3f}"),
            ("Post-hold I range", f"I Range             {ca_i_range}"),
            ("Post-hold bandwidth", f"Bandwidth           {int(ca_bandwidth)}"),
            (
                "Post-hold duration",
                f"{'ti (h:m:s)':<20}{_fmt_duration(args.post_s):<20}",
            ),
        ],
    )
    peis_mps = out_dir / "olecom_seeded_peis.mps"

    if ctrl is None:
        ctrl = OleComController(
            ip=args.ip,
            channel=args.channel,
            create_if_missing=args.allow_create_eclab,
            trust_test_connection=args.trust_test_connection,
        )
    summary: dict = {
        "output_dir": str(out_dir),
        "bias_v": float(args.bias),
        "dv_v": float(args.dv),
        "pre_s": float(args.pre_s),
        "scout_max_s": float(args.scout_max_s),
        "scout_min_s": float(args.scout_min_s),
        "post_s": float(args.post_s),
        "bandwidth": int(args.bandwidth),
        "ca_bandwidth": ca_bandwidth,
        "ca_i_range": ca_i_range,
        "peis_high_hz": float(args.peis_high),
        "auto_clean_eclab_killed_pids": killed,
        "architecture": "continuous_pre_scout_then_separate_post",
    }
    def _emit(event, **payload):
        if monitor_callback:
            try:
                monitor_callback(event, **payload)
            except Exception:
                pass

    records: list[dict] = []
    last_idx = 0
    live_ca_png = out_dir / "live_ca_status.png"
    last_live_plot_t = 0.0
    last_live_status_t = 0.0
    raw_lf = None
    applied_lf = None
    ca_fft_for_plot = None

    try:
        if not external_ctrl:
            ctrl.connect()
        pre_scout_run = ctrl.load_and_run(pre_scout_mps, out_dir / "olecom_pre_scout_ca")
        loaded_mps_text = pre_scout_mps.read_text(encoding="latin1", errors="replace")
        if "Ns                  0                   1" not in loaded_mps_text or f"{float(args.bias + args.dv):.3f}" not in loaded_mps_text:
            ctrl.stop()
            raise RuntimeError(
                "EC-Lab LoadSettings rewrote pre+scout MPS into a single-step CA; "
                "aborting before invalid hybrid analysis. Compare "
                f"{generated_pre_scout_mps.name} with {pre_scout_mps.name}."
            )
        summary["pre_scout_ca_mpr"] = str(pre_scout_run.mpr_path)
        print(f"[OLE-COM] pre+scout CA running: {pre_scout_run.mpr_path}", flush=True)
        _emit('step', step='ca_pre_scout', message='OLE-COM pre+scout CA started')
        ctrl.wait_until_started(pre_scout_run.mpr_path, startup_grace_s=args.ca_startup_grace_s, label="pre+scout CA")
        t0 = time.perf_counter()
        scout_stop_reason = "scout_max_or_sequence_finished"
        fft_summary = None
        last_fft_eval_t = 0.0
        while True:
            if pre_scout_run.mpr_path.exists():
                try:
                    n = ctrl.point_count(pre_scout_run.mpr_path)
                except Exception:
                    n = last_idx
                points_before = last_idx
                for idx in range(last_idx, n):
                    try:
                        records.append(ctrl.dc_value(pre_scout_run.mpr_path, idx))
                    except Exception:
                        break
                last_idx = max(last_idx, n)

                if records:
                    now = time.perf_counter()
                    rec_arr = _records_to_array(records)
                    if last_idx > points_before:
                        _emit('dc_data', step='ca_pre_scout', data=rec_arr[:, :3])
                    scout_mask = np.isclose(rec_arr[:, 3], 1)
                    should_write_status = now - last_live_status_t >= float(args.live_status_interval_s)
                    if float(args.live_plot_interval_s) > 0 and now - last_live_plot_t >= float(args.live_plot_interval_s):
                        if not bool(args.disable_live_ca_png):
                            _plot_live_ca_snapshot(
                                out_png=live_ca_png,
                                records=records,
                                summary={**summary, "raw_recommended_lf_hz": raw_lf, "applied_peis_lf_hz": applied_lf},
                                status_text=f"OLE-COM pre+scout live | points={len(records)} | t={rec_arr[-1, 0]:.1f}s",
                            )
                            should_write_status = True
                        last_live_plot_t = now
                    if should_write_status:
                        _write_live_status(
                            out_dir,
                            {
                                "stage": "pre_scout_running",
                                "output_dir": str(out_dir),
                                "pre_scout_ca_mpr": str(pre_scout_run.mpr_path),
                                "points": len(records),
                                "last_time_s": float(rec_arr[-1, 0]),
                                "last_ns": int(round(float(rec_arr[-1, 3]))),
                                "live_ca_png": str(live_ca_png) if live_ca_png.exists() else "",
                                "live_ca_png_enabled": bool(float(args.live_plot_interval_s) > 0 and not bool(args.disable_live_ca_png)),
                                "updated": datetime.now().isoformat(timespec="seconds"),
                            },
                        )
                        last_live_status_t = now

                    if np.any(scout_mask):
                        scout = rec_arr[scout_mask]
                        scout_elapsed = float(scout[-1, 0] - scout[0, 0]) if len(scout) > 1 else 0.0
                        if (
                            scout_elapsed >= float(args.scout_min_s)
                            and now - last_fft_eval_t >= float(args.fft_eval_interval_s)
                        ):
                            last_fft_eval_t = now
                            fft_summary, ca_fft_for_plot = _analyze_fft(
                                out_dir,
                                rec_arr,
                                pre_tail_s=args.fft_pre_tail_s,
                                deep_limit=args.peis_deep_limit,
                                overlap_limit=args.peis_overlap_limit,
                            )
                            raw_lf = float(fft_summary["raw_recommended_lf_hz"])
                            applied_lf = float(fft_summary["applied_peis_lf_hz"])
                            sat = fft_summary.get("cp_saturation", {})
                            reached = bool(sat.get("saturation_reached"))
                            guard = _scout_live_guard(
                                rec_arr,
                                fft_summary,
                                min_fft_duration_s=args.min_fft_duration_s,
                                tail_window_s=args.scout_tail_window_s,
                                rel_shift_limit=args.scout_tail_rel_shift_limit,
                                rel_slope_limit=args.scout_tail_rel_slope_limit,
                            )
                            fft_summary["scout_live_guard"] = guard
                            if reached and bool(guard.get("ready")):
                                scout_stop_reason = f"live_saturation_{sat.get('selection_reason', 'reached')}"
                                ctrl.stop()
                                print(
                                    f"[OLE-COM] scout live stop: raw LF={raw_lf:.5g}, PEIS LF={applied_lf:.5g}",
                                    flush=True,
                                )
                                break
                            if reached:
                                print(
                                    "[OLE-COM] scout hold: saturation flag set but tail guard is not clear "
                                    f"({guard.get('reason')}); raw LF={raw_lf:.5g}, PEIS LF={applied_lf:.5g}",
                                    flush=True,
                                )
                            if scout_elapsed >= float(args.scout_max_s):
                                scout_stop_reason = (
                                    "scout_max_reached_after_guard_hold"
                                    if reached
                                    else "scout_max_reached_without_saturation"
                                )
                                ctrl.stop()
                                break

            if not ctrl.is_running():
                break
            if time.perf_counter() - t0 > float(args.pre_s + args.scout_max_s + 90):
                ctrl.stop()
                raise TimeoutError("pre+scout CA exceeded expected duration")
            time.sleep(float(args.poll_s))

        _wait_for_point_count_stable(
            ctrl,
            pre_scout_run.mpr_path,
            stable_s=args.buffer_stable_s,
            timeout_s=args.buffer_timeout_s,
            poll_s=args.poll_s,
            label="pre+scout CA flush",
            verify_row_count_fn=lambda p: len(_parse_mpr_dc(p)),
        )
        ca_pre_scout = _parse_mpr_dc(pre_scout_run.mpr_path)
        _emit('dc_data', step='ca_pre_scout_done', data=ca_pre_scout[:, :3])
        continuity = _ca_sequence_continuity_metrics(ca_pre_scout)
        pre_stability = _estimate_pre_stabilization(ca_pre_scout)
        continuity_png = out_dir / "pre_scout_continuity_audit.png"
        if fft_summary is None:
            fft_summary, ca_fft_for_plot = _analyze_fft(
                out_dir,
                ca_pre_scout,
                pre_tail_s=args.fft_pre_tail_s,
                deep_limit=args.peis_deep_limit,
                overlap_limit=args.peis_overlap_limit,
            )
            raw_lf = float(fft_summary["raw_recommended_lf_hz"])
            applied_lf = float(fft_summary["applied_peis_lf_hz"])
            fft_summary["scout_live_guard"] = _scout_live_guard(
                ca_pre_scout,
                fft_summary,
                min_fft_duration_s=args.min_fft_duration_s,
                tail_window_s=args.scout_tail_window_s,
                rel_shift_limit=args.scout_tail_rel_shift_limit,
                rel_slope_limit=args.scout_tail_rel_slope_limit,
            )

        summary.update(fft_summary)
        summary.update(
            {
                "scout_stop_reason": scout_stop_reason,
                "ca_sequence_continuity": continuity,
                "ca_sequence_continuity_png": str(continuity_png),
                "pre_stability": pre_stability,
            }
        )

        peis_n_average = int(getattr(args, "peis_n_average", 1) or 1)
        _write_peis_mps(
            peis_mps,
            bias_v=args.bias,
            f_high_hz=args.peis_high,
            f_low_hz=float(applied_lf),
            points_per_decade=args.peis_points_per_decade,
            amplitude_mv=abs(args.dv) * 1000.0,
            bandwidth=args.bandwidth,
            n_average=peis_n_average,
        )
        _require_mps_fields(
            peis_mps,
            [
                ("PEIS bias", f"E (V)               {float(args.bias):.4f}"),
                ("PEIS high", f"fi                  {float(args.peis_high):.3f}"),
                ("PEIS low", f"ff                  {float(applied_lf):.5f}"),
                ("PEIS amplitude", f"Va (mV)             {abs(float(args.dv)) * 1000.0:.1f}"),
                ("PEIS I range", "I Range             Auto"),
                ("PEIS bandwidth", f"Bandwidth           {int(args.bandwidth)}"),
                ("PEIS N average", f"Na                  {peis_n_average}"),
            ],
        )

        post_run = ctrl.load_and_run(post_mps, out_dir / "olecom_separate_post_hold")
        print(f"[OLE-COM] separate post hold running: {post_run.mpr_path}", flush=True)
        _emit('step', step='ca_post_hold', message='OLE-COM post hold started')
        ctrl.wait_until_started(post_run.mpr_path, startup_grace_s=args.ca_startup_grace_s, label="post hold")
        _wait_for_run_completion(
            ctrl,
            post_run.mpr_path,
            timeout_s=float(args.post_s + 120),
            # wait_until_started() already handled the startup transient.  Using
            # the full CA startup grace here made short post holds wait ~30 s
            # before PEIS, which is exactly the gap this hold is meant to avoid.
            startup_grace_s=min(float(args.ca_startup_grace_s), max(1.0, float(args.post_s) * 0.2)),
            label="post hold",
        )
        _wait_for_point_count_stable(
            ctrl,
            post_run.mpr_path,
            stable_s=args.buffer_stable_s,
            timeout_s=args.buffer_timeout_s,
            poll_s=args.poll_s,
            label="post hold flush",
            verify_row_count_fn=lambda p: len(_parse_mpr_dc(p)),
        )
        post_ca = _parse_mpr_dc(post_run.mpr_path)
        _emit('dc_data', step='ca_post_hold_done', data=post_ca[:, :3])
        ca_all_display = _append_post_for_display(ca_pre_scout, post_ca)
        summary["post_ca_mpr"] = str(post_run.mpr_path)

        peis_run = ctrl.load_and_run(peis_mps, out_dir / "olecom_seeded_peis")
        print(f"[OLE-COM] PEIS running: {peis_run.mpr_path}", flush=True)
        _emit('step', step='peis', message='OLE-COM PEIS started')
        expected_peis_points = _estimate_peis_points(
            float(args.peis_high),
            float(applied_lf),
            int(args.peis_points_per_decade),
        )
        peis_points = _wait_peis_completion_or_stable(
            ctrl,
            peis_run.mpr_path,
            timeout_s=args.peis_timeout_s,
            startup_grace_s=args.peis_startup_grace_s,
            expected_points=expected_peis_points,
            target_low_hz=applied_lf,
            stable_s=args.buffer_stable_s,
            poll_s=args.poll_s,
            monitor_callback=monitor_callback,
        )
        if peis_points <= 0:
            raise RuntimeError("PEIS completed but produced zero points")
        peis = _parse_mpr_eis(peis_run.mpr_path)
        if len(peis) == 0:
            raise RuntimeError(f"PEIS MPR has zero parseable impedance rows: {peis_run.mpr_path}")
        _emit('eis_data', step='peis_done', data=peis)
        peis_min_hz = float(np.nanmin(peis[:, 0]))
        if peis_min_hz > max(float(applied_lf) * 1.25, float(applied_lf) + 1e-12):
            raise RuntimeError(
                f"PEIS stopped before requested LF overlap: min={peis_min_hz:.6g} Hz, "
                f"target={float(applied_lf):.6g} Hz"
            )
        peis_txt = out_dir / "measured_peis.txt"
        np.savetxt(peis_txt, peis, delimiter="\t", header="freq_Hz\tReZ_ohm\tnegImZ_ohm", comments="")
        fit_png = out_dir / "peis_fit_onepage.png"
        onepage = out_dir / "olecom_hybrid_onepage.png"
        fit = {}
        if not bool(args.defer_postprocess):
            _plot_ca_sequence_continuity(ca_pre_scout, continuity_png, continuity)
            fit = _fit_and_plot(peis, fit_png, f"OLE-COM pre-scout live stop PEIS, bias={args.bias:+.3f} V")
        summary.update(
            {
                "ca_mpr": str(pre_scout_run.mpr_path),
                "peis_mpr": str(peis_run.mpr_path),
                "peis_txt": str(peis_txt),
                "peis_points": int(len(peis)),
                "peis_expected_points": int(expected_peis_points),
                "peis_min_hz": peis_min_hz,
                "peis_max_hz": float(np.nanmax(peis[:, 0])),
                "fit_png": str(fit_png) if fit else "",
                "fit_model": fit.get("Equivalent circuit") or fit.get("Fit model"),
                "fit_score": fit.get("Fit quality score"),
                "fit_cost": fit.get("Fit cost"),
                "fit": fit,
                "postprocess_deferred": bool(args.defer_postprocess),
            }
        )
        if not bool(args.defer_postprocess):
            _plot_onepage(out_png=onepage, ca_all=ca_all_display, ca_fft=ca_fft_for_plot, peis=peis, summary=summary)
            summary["onepage_png"] = str(onepage)
            (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            full_arc_stdout = out_dir / "full_arc_stdout.log"
            full_arc_stderr = out_dir / "full_arc_stderr.log"
            full_arc_cmd = [
                sys.executable,
                str(SCRIPT_DIR / "make_olecom_full_arc_from_run.py"),
                str(out_dir),
                "--fit-model",
                "RRQRQ",
            ]
            with full_arc_stdout.open("w", encoding="utf-8") as fout, full_arc_stderr.open("w", encoding="utf-8") as ferr:
                completed = subprocess.run(
                    full_arc_cmd,
                    cwd=str(PROJECT_DIR),
                    stdout=fout,
                    stderr=ferr,
                    timeout=180.0,
                )
            if completed.returncode == 0 and (out_dir / "full_arc_summary.json").exists():
                try:
                    full_arc_summary = json.loads((out_dir / "full_arc_summary.json").read_text(encoding="utf-8"))
                    summary["full_arc_summary_json"] = str(out_dir / "full_arc_summary.json")
                    summary["full_arc_png"] = full_arc_summary.get("recommended_full_arc_png") or full_arc_summary.get("full_arc_png")
                    summary["full_arc_fit_model"] = full_arc_summary.get("fit_model")
                    summary["full_arc_fit_score"] = full_arc_summary.get("fit_score")
                except Exception as exc:
                    summary["full_arc_postprocess_warning"] = str(exc)
            else:
                summary["full_arc_postprocess_warning"] = (
                    f"make_olecom_full_arc_from_run failed with return code {completed.returncode}; "
                    f"see {full_arc_stderr}"
                )
        else:
            summary["onepage_png"] = ""
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        _write_live_status(
            out_dir,
            {
                "stage": "complete",
                "output_dir": str(out_dir),
                "summary_json": str(out_dir / "summary.json"),
                "onepage_png": str(onepage) if not bool(args.defer_postprocess) else "",
                "fit_png": str(fit_png) if fit else "",
                "fit_score": fit.get("Fit quality score"),
                "postprocess_deferred": bool(args.defer_postprocess),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        print(json.dumps(summary, indent=2, default=str), flush=True)
        return summary
    except Exception as exc:
        _write_live_status(
            out_dir,
            {
                "stage": "error",
                "output_dir": str(out_dir),
                "error": f"{type(exc).__name__}: {exc}",
                "live_ca_png": str(live_ca_png),
                "updated": datetime.now().isoformat(timespec="seconds"),
            },
        )
        try:
            if ctrl.obj is not None and ctrl.is_running():
                ctrl.stop()
                ctrl.wait_until_stopped(timeout_s=10.0)
        except Exception:
            pass
        raise
    finally:
        if bool(getattr(args, "disconnect_on_exit", False)) and not external_ctrl:
            ctrl.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="OLE-COM hybrid: continuous pre+scout, live stop, separate post, PEIS.")
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--bias", type=float, default=0.0)
    parser.add_argument("--dv", type=float, default=0.03)
    parser.add_argument("--pre-s", type=float, default=60.0)
    parser.add_argument("--scout-min-s", type=float, default=90.0)
    parser.add_argument("--scout-max-s", type=float, default=300.0)
    parser.add_argument("--post-s", type=float, default=10.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--bandwidth", type=int, default=4, help="PEIS bandwidth")
    parser.add_argument("--peis-n-average", type=int, default=1, help="PEIS N average (repeats per point)")
    parser.add_argument("--ca-bandwidth", type=int, default=None, help="CA bandwidth (defaults to --bandwidth)")
    parser.add_argument("--ca-i-range", default="Auto", help="CA I Range (e.g. Auto, 100mA, 10mA, ...)")
    parser.add_argument("--poll-s", type=float, default=0.5)
    parser.add_argument("--live-status-interval-s", type=float, default=5.0)
    parser.add_argument("--live-plot-interval-s", type=float, default=0.0)
    parser.add_argument("--disable-live-ca-png", action="store_true", default=False)
    parser.add_argument("--buffer-stable-s", type=float, default=5.0)
    parser.add_argument("--buffer-timeout-s", type=float, default=180.0)
    parser.add_argument("--ca-startup-grace-s", type=float, default=30.0)
    parser.add_argument("--peis-high", type=float, default=100.0)
    parser.add_argument("--peis-deep-limit", type=float, default=0.1)
    parser.add_argument(
        "--peis-overlap-limit",
        type=float,
        default=0.5,
        help=(
            "Highest PEIS lowest-frequency cutoff allowed from FFT recommendation. "
            "If FFT recommends a higher cutoff, still measure PEIS down to this value "
            "to keep a practical overlap/bridge region."
        ),
    )
    parser.add_argument("--peis-points-per-decade", type=int, default=10)
    parser.add_argument("--peis-timeout-s", type=float, default=1800.0)
    parser.add_argument("--peis-startup-grace-s", type=float, default=30.0)
    parser.add_argument("--fft-pre-tail-s", type=float, default=10.0)
    parser.add_argument("--fft-eval-interval-s", type=float, default=5.0)
    parser.add_argument("--min-fft-duration-s", type=float, default=100.0)
    parser.add_argument("--scout-tail-window-s", type=float, default=20.0)
    parser.add_argument("--scout-tail-rel-shift-limit", type=float, default=0.03)
    parser.add_argument("--scout-tail-rel-slope-limit", type=float, default=0.03)
    parser.add_argument(
        "--defer-postprocess",
        action="store_true",
        help="Skip noncritical plots/fits so the queue can move to the next technique/condition immediately.",
    )
    parser.add_argument("--allow-create-eclab", action="store_true")
    parser.add_argument("--trust-test-connection", action="store_true")
    parser.add_argument("--auto-clean-eclab", action="store_true")
    parser.add_argument("--auto-clean-visible-eclab", action="store_true")
    parser.add_argument("--disconnect-on-exit", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    run_once(args)


if __name__ == "__main__":
    main()
