# -*- coding: utf-8 -*-
"""
Generic full-vs-optimized comparison utilities for the mpr-only workflow.
"""

from __future__ import annotations

from pathlib import Path
import json
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)
plt.rcParams["figure.max_open_warning"] = 0
import numpy as np

import Convert_CP_to_EIS as DC_EIS
import EIS_Fitting as EISFIT
import Load_CP_Data as LD
import Plotting_Functions as PF
import Smooth_and_Interpolate as SI
import export_dict_to_excel as Export
import export_origin_friendly as OriginExport


BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "Input data" / "260417-8"
OUTPUT_DIR = BASE_DIR / "result" / "vs optimized"

DEFAULT_TRIM_START_TIME = 242.25
DEFAULT_CP_DURATIONS = [60, 90, 120, 150, 180, 210, 240, 270, 300]

DT = 0.01
WINDOW = 51
ZERO_PAD_FACTOR = 50
FAST_ZERO_PAD_FACTOR = 50
N_LOG_POINTS = 100
F_MAX = None
RECOMMENDATION_EXTENSION_FACTOR = 8.0
DEFAULT_FFT_PRE_TAIL_S = 10.0


def _median_time_step(time_values, fallback=DT):
    time_values = np.asarray(time_values, dtype=float)
    if time_values.size < 2:
        return float(fallback)
    diffs = np.diff(time_values)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return float(fallback)
    return float(np.median(diffs))


def _window_points_for_seconds(window_s, dt, min_points=5):
    dt = max(float(dt), 1e-9)
    points = int(round(float(window_s) / dt))
    points = max(int(min_points), points)
    if points % 2 == 0:
        points += 1
    return points


def _moving_average(values, window):
    values = np.asarray(values, dtype=float)
    if window is None or int(window) <= 1 or len(values) <= 2:
        return values.copy()
    window = int(min(max(1, int(window)), len(values)))
    if window <= 1:
        return values.copy()
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=float) / float(window)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed[: len(values)]


def _hampel_despike(values, window_points, n_sigma=6.0):
    values = np.asarray(values, dtype=float)
    if values.size < 3:
        return values.copy()
    window_points = int(max(3, window_points))
    if window_points % 2 == 0:
        window_points += 1
    half = window_points // 2
    filtered = values.copy()
    for idx in range(values.size):
        lo = max(0, idx - half)
        hi = min(values.size, idx + half + 1)
        window = values[lo:hi]
        finite = window[np.isfinite(window)]
        if finite.size < 3 or not np.isfinite(values[idx]):
            continue
        median = float(np.median(finite))
        mad = float(np.median(np.abs(finite - median)))
        if mad <= 0:
            continue
        robust_sigma = 1.4826 * mad
        if abs(float(values[idx]) - median) > float(n_sigma) * robust_sigma:
            filtered[idx] = median
    return filtered


def _detect_voltage_step_idx(work):
    if len(work) < 3:
        return len(work)
    dv = np.abs(np.diff(work[:, 1]))
    if len(dv) == 0 or not np.isfinite(dv).any() or float(np.nanmax(dv)) <= 1e-12:
        return len(work)
    return int(np.nanargmax(dv)) + 1


def _detect_voltage_step_indices(work, rel_threshold=0.30):
    arr = np.asarray(work, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or len(arr) < 3:
        return []
    dv = np.abs(np.diff(arr[:, 1]))
    finite = np.isfinite(dv)
    if not np.any(finite):
        return []
    max_dv = float(np.nanmax(dv[finite]))
    if max_dv <= 1e-12:
        return []
    threshold = max(max_dv * float(rel_threshold), 1e-5)
    candidate = np.where(finite & (dv >= threshold))[0] + 1
    if candidate.size == 0:
        return [int(np.nanargmax(dv)) + 1]
    steps = [int(candidate[0])]
    for idx in candidate[1:]:
        idx = int(idx)
        if idx - steps[-1] > 2:
            steps.append(idx)
    return steps


def _select_pre_tail_plus_scout_for_fft(data, *, cp_duration_s=None, pre_tail_s=DEFAULT_FFT_PRE_TAIL_S):
    """Return only stable pre-tail plus the dV scout segment for FFT recovery.

    The FFT excitation is the voltage step. Feeding the full pre-stabilization
    drift into the FFT creates a second low-frequency artifact and can flip or
    distort the recovered arc. Candidate CP durations are interpreted as scout
    time after the dV step; the retained pre-tail is extra baseline.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 3 or len(arr) < 3:
        return arr.copy(), {
            "method": "invalid_or_short_input",
            "fft_pre_tail_s": float(pre_tail_s),
        }

    work = arr[:, :3].copy()
    steps = _detect_voltage_step_indices(work)
    if not steps:
        out = work.copy()
        out[:, 0] -= float(out[0, 0])
        return out, {
            "method": "no_voltage_step_detected_full_trace",
            "fft_pre_tail_s": float(pre_tail_s),
            "selected_duration_s": float(out[-1, 0] - out[0, 0]) if len(out) else 0.0,
        }

    first_step_idx = int(np.clip(steps[0], 0, len(work) - 1))
    first_step_time = float(work[first_step_idx, 0])
    start_time = first_step_time - max(0.0, float(pre_tail_s))

    if cp_duration_s is not None and np.isfinite(cp_duration_s):
        end_time = first_step_time + max(0.0, float(cp_duration_s))
        end_reason = "requested_scout_duration"
    elif len(steps) > 1:
        end_time = float(work[int(np.clip(steps[1], 0, len(work) - 1)), 0])
        end_reason = "next_voltage_step"
    else:
        end_time = float(work[-1, 0])
        end_reason = "trace_end"

    if len(steps) > 1:
        second_step_time = float(work[int(np.clip(steps[1], 0, len(work) - 1)), 0])
        end_time = min(end_time, second_step_time)
    else:
        second_step_time = None

    mask = (work[:, 0] >= start_time) & (work[:, 0] <= end_time)
    if np.count_nonzero(mask) < 3:
        pre_idx = np.where(work[:, 0] < first_step_time)[0]
        if len(pre_idx):
            keep_pre = pre_idx[-min(len(pre_idx), 5):]
            scout_idx = np.where(work[:, 0] >= first_step_time)[0]
            if cp_duration_s is not None and np.isfinite(cp_duration_s):
                scout_idx = scout_idx[work[scout_idx, 0] <= first_step_time + float(cp_duration_s)]
            keep = np.r_[keep_pre, scout_idx]
            mask = np.zeros(len(work), dtype=bool)
            mask[keep] = True

    selected = work[mask].copy()
    if len(selected):
        selected[:, 0] -= float(selected[0, 0])

    selected_step_time = first_step_time - float(work[mask][0, 0]) if np.any(mask) else np.nan
    scout_available_s = max(0.0, float(end_time - first_step_time))
    return selected, {
        "method": "pre_tail_plus_scout",
        "fft_pre_tail_s": float(pre_tail_s),
        "voltage_step_time_original_s": first_step_time,
        "voltage_step_time_selected_s": float(selected_step_time),
        "second_voltage_step_time_original_s": second_step_time,
        "selected_start_original_s": float(start_time),
        "selected_end_original_s": float(end_time),
        "selected_duration_s": float(selected[-1, 0] - selected[0, 0]) if len(selected) else 0.0,
        "scout_duration_available_s": float(scout_available_s),
        "end_reason": end_reason,
        "input_points": int(len(work)),
        "selected_points": int(len(selected)),
    }


def _robust_bin_segment(segment, bin_s):
    if bin_s is None or float(bin_s) <= 0.0 or len(segment) < 2:
        return np.asarray(segment, dtype=float).copy()
    seg = np.asarray(segment, dtype=float)
    start = float(seg[0, 0])
    stop = float(seg[-1, 0])
    if not np.isfinite(start) or not np.isfinite(stop) or stop <= start:
        return seg.copy()
    edges = np.arange(start, stop + float(bin_s) + 1e-12, float(bin_s))
    if len(edges) < 2:
        return seg.copy()
    rows = []
    for idx in range(len(edges) - 1):
        lo = float(edges[idx])
        hi = float(edges[idx + 1])
        if idx < len(edges) - 2:
            mask = (seg[:, 0] >= lo) & (seg[:, 0] < hi)
        else:
            mask = (seg[:, 0] >= lo) & (seg[:, 0] <= hi)
        if not np.any(mask):
            continue
        chunk = seg[mask]
        rows.append(
            [
                float(np.mean(chunk[:, 0])),
                float(np.median(chunk[:, 1])),
                float(np.median(chunk[:, 2])),
            ]
        )
    if len(rows) < 2:
        return seg.copy()
    return np.asarray(rows, dtype=float)


def build_fft_ready_ca_trace(
    data,
    *,
    average_bin_s=0.1,
    segmented_smoothing_window_points=None,
    despike=True,
    despike_window_s=1.0,
    despike_sigma=6.0,
):
    """
    Convert raw CA into an FFT-ready trace.

    The default path preserves the voltage-step boundary, then removes obvious
    within-segment current spikes and bins to 0.1 s using medians. Voltage is
    kept untouched because the dV step is the FFT excitation.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 3 or len(arr) == 0:
        return np.asarray(arr, dtype=float).copy()

    work = arr[:, :3].copy()
    work[:, 0] = work[:, 0] - float(work[0, 0])

    step_idx = _detect_voltage_step_idx(work)
    segments = [work] if step_idx <= 0 or step_idx >= len(work) else [work[:step_idx], work[step_idx:]]
    processed_segments = []
    for segment in segments:
        seg = np.asarray(segment, dtype=float).copy()
        if despike and len(seg) >= 3:
            dt_values = np.diff(seg[:, 0])
            dt_values = dt_values[np.isfinite(dt_values) & (dt_values > 0)]
            dt_med = float(np.median(dt_values)) if dt_values.size else 0.1
            window_points = max(3, int(round(float(despike_window_s) / max(dt_med, 1e-9))))
            seg[:, 2] = _hampel_despike(seg[:, 2], window_points, n_sigma=despike_sigma)
        processed_segments.append(_robust_bin_segment(seg, average_bin_s))
    if processed_segments:
        work = np.vstack(processed_segments)

    if (
        segmented_smoothing_window_points is not None
        and int(segmented_smoothing_window_points) > 1
        and len(work) >= 3
    ):
        step_idx = _detect_voltage_step_idx(work)

        smoothed = work.copy()
        pre = work[:step_idx]
        post = work[step_idx:]
        if len(pre):
            smoothed[:step_idx, 2] = _moving_average(
                pre[:, 2], int(segmented_smoothing_window_points)
            )
        if len(post):
            smoothed[step_idx:, 2] = _moving_average(
                post[:, 2], int(segmented_smoothing_window_points)
            )
        work = smoothed

    return work


def _legacy_smooth_fft_trace(data):
    """Return the trusted legacy FFT input used by the original optimized backend."""
    arr = np.asarray(data, dtype=float)
    return SI.smooth_and_interpolate(
        arr[:, 0],
        arr[:, 1],
        arr[:, 2],
        DT,
        WINDOW,
    )


def get_fit_value(fit_result, prefix):
    for key, value in fit_result.items():
        if key.startswith(prefix):
            return value
    return None


def json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def _text_header_declares_minus_imag(eis_path) -> bool:
    """Return True when a text EIS file explicitly stores the third column as -Im."""
    try:
        with open(eis_path, "r", encoding="latin1") as handle:
            for _ in range(12):
                line = handle.readline()
                if not line:
                    break
                lowered = line.strip().lower().replace(" ", "")
                if "-im" in lowered or "minusim" in lowered:
                    return True
                if "im(z" in lowered and "-im" not in lowered:
                    return False
    except Exception:
        return False
    return False


def _ensure_eis_imaginary_convention(eis_data, eis_path=None):
    """
    Internal fitting convention is [freq, Re(Z), Im(Z)].

    Some copied Win7/manual paths have produced text PEIS files whose header says
    "-Im(Z)" but whose third column reached full processing without being flipped
    to Im(Z). Only correct that case when the source header explicitly declares
    -Im and the lowest-frequency tail still looks like positive imaginary data.
    """
    data = np.asarray(eis_data, dtype=float)
    if data.ndim != 2 or data.shape[1] < 3 or len(data) < 3:
        return eis_data
    if eis_path is None or not _text_header_declares_minus_imag(eis_path):
        return eis_data

    finite = np.isfinite(data[:, 0]) & np.isfinite(data[:, 2]) & (data[:, 0] > 0)
    if np.sum(finite) < 3:
        return eis_data
    finite_data = data[finite]
    order = np.argsort(finite_data[:, 0])
    low_n = max(3, min(10, int(np.ceil(0.2 * len(order)))))
    low_im = finite_data[order[:low_n], 2]
    if float(np.nanmedian(low_im)) > 0:
        corrected = data.copy()
        corrected[:, 2] *= -1.0
        print(
            "[Analysis] Corrected EIS imaginary sign: source header declares -Im(Z), "
            "but loaded low-frequency imaginary values were positive."
        )
        return corrected
    return eis_data


def load_mpr_pair(
    dc_path,
    eis_path,
    trim_start_time,
    current_in_mA=True,
    current_scale_factor=1.0,
    auto_trim=False,
    auto_trim_kwargs=None,
):
    """Load an mpr PEIS/CP pair and return raw data with load timing."""
    t0 = time.perf_counter()
    dc_data = LD.load_dc_from_path(
        dc_path,
        current_in_mA=current_in_mA,
        CA_step_only=False,
        trim_start_time=trim_start_time,
        current_scale_factor=current_scale_factor,
        auto_trim=auto_trim,
        auto_trim_kwargs=auto_trim_kwargs,
    )
    eis_data = LD.load_eis_from_path(eis_path)
    eis_data = _ensure_eis_imaginary_convention(eis_data, eis_path=eis_path)
    load_time_s = time.perf_counter() - t0
    return {
        "dc_path": str(dc_path),
        "eis_path": str(eis_path),
        "dc_data": dc_data,
        "eis_data": eis_data,
        "timing": {"load_time_s": float(load_time_s)},
    }


def recover_from_dc(
    dc_path,
    eis_path,
    current_in_mA,
    current_scale_factor,
    trim_start_time,
    cp_duration_s=None,
    make_figures=True,
    recovered_upper_bound_hz=None,
    auto_trim=False,
    auto_trim_kwargs=None,
):
    loaded = load_mpr_pair(
        dc_path=dc_path,
        eis_path=eis_path,
        trim_start_time=trim_start_time,
        current_in_mA=current_in_mA,
        current_scale_factor=current_scale_factor,
        auto_trim=auto_trim,
        auto_trim_kwargs=auto_trim_kwargs,
    )
    dc_data = loaded["dc_data"]
    fft_dc_data, fft_selection = _select_pre_tail_plus_scout_for_fft(
        dc_data,
        cp_duration_s=cp_duration_s,
        pre_tail_s=DEFAULT_FFT_PRE_TAIL_S,
    )

    eis_data = loaded["eis_data"]

    fft_ready_data = build_fft_ready_ca_trace(fft_dc_data, average_bin_s=0.1, despike=True)
    fft_selection.update(
        {
            "fft_ready_points": int(len(fft_ready_data)),
            "fft_ready_bin_s": 0.1,
            "fft_ready_despike": True,
        }
    )

    fig_raw = PF.plot_DC(dc_data[:, 0], dc_data[:, 1], dc_data[:, 2], "Raw") if make_figures else None
    t_pre = time.perf_counter()
    time_arr, volt, current = _legacy_smooth_fft_trace(fft_ready_data)
    fig_treated = (
        PF.plot_DC_comp(dc_data[:, 0], dc_data[:, 1], dc_data[:, 2], time_arr, volt, current)
        if make_figures
        else None
    )

    (
        dif_time,
        dif_v,
        dif_i,
        ft_f,
        ft_v,
        ft_i,
        filtered_f,
        recovered_z,
        _,
        fig_dif,
        fig_fft,
        fig_recovered,
        fig_ref_comp,
    ) = DC_EIS.FFT_EIS(
        time_arr,
        volt,
        current,
        "Smooth",
        DT,
        WINDOW,
        recovered_upper_bound_hz if recovered_upper_bound_hz is not None else F_MAX,
        ZERO_PAD_FACTOR,
        N_LOG_POINTS,
        eis_data=eis_data,
        make_plots=make_figures,
    )
    preprocessing_time_s = time.perf_counter() - t_pre

    target_f_max = DC_EIS.resolve_fft_max_f(
        recovered_upper_bound_hz if recovered_upper_bound_hz is not None else F_MAX,
        eis_data=eis_data,
        ft_freq=ft_f,
    )
    analysis_max_f = min(
        float(np.max(ft_f[ft_f > 0])),
        float(target_f_max) * float(RECOMMENDATION_EXTENSION_FACTOR),
    )
    analysis_f, analysis_z = DC_EIS.extract_log_spaced_impedance(
        ft_f,
        ft_v,
        ft_i,
        max_f=analysis_max_f,
        n_log_points=max(N_LOG_POINTS * 2, 160),
    )
    recommendation = DC_EIS.recommend_peis_lowest_frequency(analysis_f, analysis_z)
    recommendation_fig = (
        DC_EIS.plot_peis_cutoff_recommendation(
            analysis_f,
            analysis_z,
            recommendation,
            title=Path(dc_path).stem,
        )
        if make_figures
        else None
    )

    ft_mask = (ft_f > 0) & (ft_f <= target_f_max)
    ft_f_e = ft_f[ft_mask]
    ft_v_e = ft_v[ft_mask]
    ft_i_e = ft_i[ft_mask]
    ft_z_e = ft_v_e / ft_i_e

    return {
        "dc_path": str(dc_path),
        "eis_path": str(eis_path),
        "dc_data": dc_data,
        "fft_dc_data": fft_dc_data,
        "fft_ready_data": fft_ready_data,
        "fft_selection": fft_selection,
        "eis_data": eis_data,
        "time": time_arr,
        "volt": volt,
        "current": current,
        "dif_time": dif_time,
        "dif_v": dif_v,
        "dif_i": dif_i,
        "ft_f": ft_f,
        "ft_v": ft_v,
        "ft_i": ft_i,
        "ft_f_e": ft_f_e,
        "ft_v_e": ft_v_e,
        "ft_i_e": ft_i_e,
        "ft_z_e": ft_z_e,
        "filtered_f": filtered_f,
        "recovered_z": recovered_z,
        "target_f_max": target_f_max,
        "analysis_f": analysis_f,
        "analysis_z": analysis_z,
        "analysis_max_f": analysis_max_f,
        "recommendation": recommendation,
        "timing": {
            **loaded["timing"],
            "preprocessing_time_s": float(preprocessing_time_s),
            "fft_dt_s": float(DT),
            "fft_window_points": int(WINDOW),
        },
        "figures": {
            "raw": fig_raw,
            "treated": fig_treated,
            "dif": fig_dif,
            "fft": fig_fft,
            "recovered": fig_recovered,
            "ref_comp": fig_ref_comp,
            "recommendation": recommendation_fig,
        },
    }


def fit_combined_data(
    eis_data,
    filtered_f,
    recovered_z,
    peis_lowest_freq_hz=None,
    previous_fit_seed=None,
    reference_full_fit_seed=None,
    make_figure=True,
):
    if peis_lowest_freq_hz is None:
        eis_mask = np.ones(len(eis_data), dtype=bool)
        rec_mask = np.ones(len(filtered_f), dtype=bool)
    else:
        eis_mask = eis_data[:, 0] >= float(peis_lowest_freq_hz)
        rec_mask = np.asarray(filtered_f, dtype=float) < float(peis_lowest_freq_hz) * (1.0 - 1e-9)

    fit_freq = np.concatenate([eis_data[eis_mask, 0], np.asarray(filtered_f)[rec_mask]])
    fit_re = np.concatenate([eis_data[eis_mask, 1], np.asarray(recovered_z.real)[rec_mask]])
    fit_im = np.concatenate([eis_data[eis_mask, 2], np.asarray(recovered_z.imag)[rec_mask]])
    sort_idx = np.argsort(fit_freq)[::-1]
    fit_freq = fit_freq[sort_idx]
    fit_re = fit_re[sort_idx]
    fit_im = fit_im[sort_idx]

    t_fit = time.perf_counter()
    fit_result = EISFIT.fit_RQRQRQ_stable(
        fit_freq,
        fit_re,
        fit_im,
        previous_fit_seed=previous_fit_seed,
        reference_full_fit_seed=reference_full_fit_seed,
        freq_range=None,
    )
    fit_time_s = time.perf_counter() - t_fit
    fit_fig = (
        EISFIT.plot_fit(
            fit_freq,
            fit_re,
            fit_im,
            fit_result,
            title="full fit" if peis_lowest_freq_hz is None else "optimized fit",
        )
        if make_figure
        else None
    )
    return {
        "freq": fit_freq,
        "re": fit_re,
        "im": fit_im,
        "fit": fit_result,
        "fit_fig": fit_fig,
        "peis_lowest_freq_hz": peis_lowest_freq_hz,
        "fit_time_s": float(fit_time_s),
    }


def build_hybrid_dataset(eis_data, filtered_f, recovered_z, peis_lowest_freq_hz=None):
    """Build the hybrid PEIS + recovered dataset used for fitting."""
    if peis_lowest_freq_hz is None:
        eis_mask = np.ones(len(eis_data), dtype=bool)
        rec_mask = np.ones(len(filtered_f), dtype=bool)
    else:
        eis_mask = eis_data[:, 0] >= float(peis_lowest_freq_hz)
        rec_mask = np.asarray(filtered_f, dtype=float) < float(peis_lowest_freq_hz) * (1.0 - 1e-9)
    freq = np.concatenate([eis_data[eis_mask, 0], np.asarray(filtered_f)[rec_mask]])
    rez = np.concatenate([eis_data[eis_mask, 1], np.asarray(recovered_z.real)[rec_mask]])
    imz = np.concatenate([eis_data[eis_mask, 2], np.asarray(recovered_z.imag)[rec_mask]])
    sort_idx = np.argsort(freq)[::-1]
    return {
        "freq": freq[sort_idx],
        "re": rez[sort_idx],
        "im": imz[sort_idx],
        "used_peis_mask": eis_mask,
        "used_recovered_mask": rec_mask,
    }


def fit_hybrid_dataset(
    hybrid_dataset,
    previous_fit_seed=None,
    reference_full_fit_seed=None,
    make_figure=False,
):
    """Fit the prepared hybrid dataset with timing and seed metadata."""
    t_fit = time.perf_counter()
    fit_result = EISFIT.fit_RQRQRQ_stable(
        hybrid_dataset["freq"],
        hybrid_dataset["re"],
        hybrid_dataset["im"],
        previous_fit_seed=previous_fit_seed,
        reference_full_fit_seed=reference_full_fit_seed,
        freq_range=None,
    )
    fit_time_s = time.perf_counter() - t_fit
    fit_fig = (
        EISFIT.plot_fit(
            hybrid_dataset["freq"],
            hybrid_dataset["re"],
            hybrid_dataset["im"],
            fit_result,
            title="hybrid fit",
        )
        if make_figure
        else None
    )
    return {
        "freq": hybrid_dataset["freq"],
        "re": hybrid_dataset["re"],
        "im": hybrid_dataset["im"],
        "fit": fit_result,
        "fit_fig": fit_fig,
        "fit_time_s": float(fit_time_s),
        "fit_quality_score": float(fit_result.get("Fit quality score", np.nan)),
        "fit_seed_source": fit_result.get("Fit seed source"),
        "fit_pass_used": fit_result.get("Fit pass used"),
    }


def model_curve_from_fit(fit_result, freq):
    params = EISFIT.fit_result_to_seed(fit_result)
    return EISFIT.Z_3RQ(params, freq)


def summarize_fit(fit_result):
    summary = {key: json_safe(fit_result.get(key)) for key in EISFIT.FIT_RESULT_KEYS}
    summary.update(
        {
            "Fit cost": json_safe(fit_result.get("Fit cost")),
            "Fit success": bool(fit_result.get("Fit success", False)),
            "Fit quality score": json_safe(fit_result.get("Fit quality score")),
            "Fit seed source": fit_result.get("Fit seed source"),
            "Fit pass used": fit_result.get("Fit pass used"),
        }
    )
    return summary


def compare_fit_bundles(reference_fit_bundle, candidate_fit_bundle):
    full_fit = reference_fit_bundle["fit"]
    cand_fit = candidate_fit_bundle["fit"]

    common_freq = np.logspace(
        np.log10(max(min(np.min(reference_fit_bundle["freq"]), np.min(candidate_fit_bundle["freq"])), 1e-6)),
        np.log10(max(np.max(reference_fit_bundle["freq"]), np.max(candidate_fit_bundle["freq"]))),
        500,
    )
    z_ref = model_curve_from_fit(full_fit, common_freq)
    z_cand = model_curve_from_fit(cand_fit, common_freq)

    mag_rel = np.abs(np.abs(z_ref) - np.abs(z_cand)) / np.maximum(np.abs(z_ref), 1e-8)
    phase_diff = np.angle(z_ref / z_cand, deg=True)
    low_count = max(30, int(common_freq.size * 0.2))
    low_idx = np.arange(common_freq.size - low_count, common_freq.size)
    re_scale = max(np.ptp(z_ref.real), 1.0)
    im_scale = max(np.ptp(-z_ref.imag), 1.0)
    nyquist_low_rmse = float(
        np.sqrt(
            np.mean(
                np.square((z_ref.real[low_idx] - z_cand.real[low_idx]) / re_scale)
                + np.square((z_ref.imag[low_idx] - z_cand.imag[low_idx]) / im_scale)
            )
        )
    )

    metrics = EISFIT.compare_fit_outputs(full_fit, cand_fit)
    metrics.update(
        {
            "model_mag_rel_mean": float(np.mean(mag_rel)),
            "model_mag_rel_max": float(np.max(mag_rel)),
            "model_phase_rmse_deg": float(np.sqrt(np.mean(np.square(phase_diff)))),
            "model_nyquist_low_rmse": nyquist_low_rmse,
            "candidate_quality_score": float(cand_fit.get("Fit quality score", np.nan)),
            "reference_quality_score": float(full_fit.get("Fit quality score", np.nan)),
        }
    )
    metrics["composite_score"] = (
        metrics["model_mag_rel_mean"] * 2.0
        + metrics["model_nyquist_low_rmse"] * 3.0
        + metrics["model_phase_rmse_deg"] / 20.0
        + (metrics["parameter_rel_err_mean"] if np.isfinite(metrics["parameter_rel_err_mean"]) else 1.0)
    )
    return metrics


def passes_optimized_quality(metrics):
    return (
        metrics["model_mag_rel_mean"] <= 0.06
        and metrics["model_nyquist_low_rmse"] <= 0.10
        and metrics["model_phase_rmse_deg"] <= 8.0
        and metrics["parameter_rel_err_mean"] <= 0.18
        and metrics["parameter_rel_err_max"] <= 0.40
    )


def passes_final_duration_guard(metrics):
    r2_rel_err = metrics.get("R2 (Ohm) rel err", np.nan)
    return (
        metrics["model_mag_rel_mean"] <= 0.04
        and metrics["model_nyquist_low_rmse"] <= 0.06
        and metrics["model_phase_rmse_deg"] <= 5.0
        and (not np.isfinite(r2_rel_err) or r2_rel_err <= 0.05)
    )


def assess_cp_saturation(time_arr, current_arr, settle_fraction=0.01, safety_factor=1.10):
    time_arr = np.asarray(time_arr, dtype=float)
    current_arr = np.asarray(current_arr, dtype=float)
    if len(time_arr) < 50 or len(current_arr) != len(time_arr):
        return {
            "step_time_s": np.nan,
            "cp_duration_s": np.nan,
            "plateau_start_time_s": None,
            "plateau_duration_s": None,
            "recommended_duration_s": None,
            "step_amplitude_a": np.nan,
            "tail_current_a": np.nan,
            "saturation_reached": False,
            "selection_reason": "insufficient_points",
        }

    di = np.gradient(current_arr, time_arr)
    step_idx = int(np.argmax(np.abs(di)))
    tail_n = max(20, int(0.15 * len(current_arr)))
    tail_current = float(np.median(current_arr[-tail_n:]))
    early_end = min(len(current_arr), step_idx + max(20, int(0.03 * (len(current_arr) - step_idx))))
    step_amplitude = float(np.max(np.abs(current_arr[step_idx:early_end] - tail_current)))
    if not np.isfinite(step_amplitude) or step_amplitude <= 0:
        return {
            "step_time_s": float(time_arr[step_idx]),
            "cp_duration_s": float(time_arr[-1] - time_arr[0]),
            "plateau_start_time_s": None,
            "plateau_duration_s": None,
            "recommended_duration_s": None,
            "step_amplitude_a": step_amplitude,
            "tail_current_a": tail_current,
            "saturation_reached": False,
            "selection_reason": "zero_step_amplitude",
        }

    allowed_band = settle_fraction * step_amplitude
    within = np.abs(current_arr - tail_current) <= allowed_band
    plateau_idx = None
    for idx in range(step_idx, len(current_arr)):
        if np.all(within[idx:]):
            plateau_idx = idx
            break

    step_time = float(time_arr[step_idx])
    cp_duration = float(time_arr[-1] - time_arr[0])
    if plateau_idx is None:
        recommended_duration = max(cp_duration * 1.25, cp_duration + 30.0)
        return {
            "step_time_s": step_time,
            "cp_duration_s": cp_duration,
            "plateau_start_time_s": None,
            "plateau_duration_s": None,
            "recommended_duration_s": float(recommended_duration),
            "step_amplitude_a": step_amplitude,
            "tail_current_a": tail_current,
            "saturation_reached": False,
            "selection_reason": "tail_not_stabilized",
        }

    plateau_start_time = float(time_arr[plateau_idx])
    plateau_duration = float(plateau_start_time - step_time)
    recommended_duration = float(max(plateau_duration * safety_factor, plateau_duration + 5.0))
    return {
        "step_time_s": step_time,
        "cp_duration_s": cp_duration,
        "plateau_start_time_s": plateau_start_time,
        "plateau_duration_s": plateau_duration,
        "recommended_duration_s": recommended_duration,
        "step_amplitude_a": step_amplitude,
        "tail_current_a": tail_current,
        "saturation_reached": True,
        "selection_reason": "tail_plateau_detected",
    }


def _is_duration_plateau(duration_entries, index):
    if index + 2 >= len(duration_entries):
        return False
    current = duration_entries[index]
    next_one = duration_entries[index + 1]
    next_two = duration_entries[index + 2]
    change_one = EISFIT.compare_fit_outputs(current["fit"], next_one["fit"])
    change_two = EISFIT.compare_fit_outputs(current["fit"], next_two["fit"])
    mean_one = change_one["parameter_rel_err_mean"]
    mean_two = change_two["parameter_rel_err_mean"]
    max_one = change_one["parameter_rel_err_max"]
    max_two = change_two["parameter_rel_err_max"]
    return (
        np.isfinite(mean_one)
        and np.isfinite(mean_two)
        and np.isfinite(max_one)
        and np.isfinite(max_two)
        and mean_one <= 0.03
        and mean_two <= 0.05
        and max_one <= 0.10
        and max_two <= 0.16
    )


def extract_optimized_parameters_fast(
    dc_path,
    eis_path,
    trim_start_time,
    current_in_mA=True,
    current_scale_factor=1.0,
    cp_duration_candidates=None,
    previous_fit_seed=None,
    auto_trim=False,
    auto_trim_kwargs=None,
):
    """
    Fast Stage A: estimate recommended PEIS LF and CP duration before any export.
    """
    t_stage = time.perf_counter()
    loaded = load_mpr_pair(
        dc_path=dc_path,
        eis_path=eis_path,
        trim_start_time=trim_start_time,
        current_in_mA=current_in_mA,
        current_scale_factor=current_scale_factor,
        auto_trim=auto_trim,
        auto_trim_kwargs=auto_trim_kwargs,
    )
    dc_data = loaded["dc_data"]
    eis_data = loaded["eis_data"]

    t_pre = time.perf_counter()
    fft_dc_data, fft_selection = _select_pre_tail_plus_scout_for_fft(
        dc_data,
        cp_duration_s=None,
        pre_tail_s=DEFAULT_FFT_PRE_TAIL_S,
    )
    fft_ready_data = build_fft_ready_ca_trace(fft_dc_data, average_bin_s=0.1, despike=True)
    fft_selection.update(
        {
            "fft_ready_points": int(len(fft_ready_data)),
            "fft_ready_bin_s": 0.1,
            "fft_ready_despike": True,
        }
    )
    smoothed_time, smoothed_voltage, smoothed_current = _legacy_smooth_fft_trace(fft_ready_data)
    preprocessing_time_s = time.perf_counter() - t_pre
    cp_saturation = assess_cp_saturation(smoothed_time, smoothed_current)

    t_fft = time.perf_counter()
    full_fft = _recover_fft_points_fast(
        smoothed_time,
        smoothed_voltage,
        smoothed_current,
        eis_data,
        cp_duration_s=None,
    )
    filtered_f_full = full_fft["filtered_f"]
    recovered_z_full = full_fft["recovered_z"]
    ft_f = full_fft["ft_f"]
    ft_v = full_fft["ft_v"]
    ft_i = full_fft["ft_i"]
    target_f_max = DC_EIS.resolve_fft_max_f(F_MAX, eis_data=eis_data, ft_freq=ft_f)
    analysis_max_f = min(
        float(np.max(ft_f[ft_f > 0])),
        float(target_f_max) * float(RECOMMENDATION_EXTENSION_FACTOR),
    )
    analysis_f, analysis_z = DC_EIS.extract_log_spaced_impedance(
        ft_f,
        ft_v,
        ft_i,
        max_f=analysis_max_f,
        n_log_points=max(N_LOG_POINTS, 120),
    )
    recommendation = DC_EIS.recommend_peis_lowest_frequency(analysis_f, analysis_z)
    fft_time_s = time.perf_counter() - t_fft
    peis_only_assessment = assess_peis_only_sufficiency(eis_data, filtered_f_full, recovered_z_full)

    if peis_only_assessment["peis_only_sufficient"]:
        parameter_extraction_time_s = time.perf_counter() - t_stage
        return {
            "recommended_peis_lowest_freq_hz": float(peis_only_assessment["recommended_peis_lowest_freq_hz"]),
            "recommended_cp_duration_s": None,
            "minimum_valid_cp_duration_s": None,
            "saturation_recommended_cp_duration_s": None,
            "cp_saturation": cp_saturation,
            "parameter_extraction_time_s": float(parameter_extraction_time_s),
            "quality_margin": 0.0,
            "selection_reason": f"peis_only:{peis_only_assessment['selection_reason']}",
            "timing": {
                "load_time_s": float(loaded["timing"]["load_time_s"]),
                "preprocessing_time_s": float(preprocessing_time_s),
                "fft_time_s": float(fft_time_s),
                "full_fit_time_s": 0.0,
                "parameter_extraction_time_s": float(parameter_extraction_time_s),
            },
            "full_fit_result": None,
            "full_filtered_f": filtered_f_full,
            "full_recovered_z": recovered_z_full,
            "fft_selection": fft_selection,
            "analysis_recommendation": recommendation,
            "sweep_results": [],
            "best_by_duration": [],
            "peis_only_assessment": peis_only_assessment,
        }

    full_hybrid = build_hybrid_dataset(eis_data, filtered_f_full, recovered_z_full, peis_lowest_freq_hz=None)
    full_fit_bundle = fit_hybrid_dataset(
        full_hybrid,
        previous_fit_seed=previous_fit_seed,
        make_figure=False,
    )

    cp_highest_freq = recommendation.get("recommended_cp_highest_freq_hz")
    if cp_highest_freq is not None and np.isfinite(cp_highest_freq):
        coarse_cutoff = choose_overlap_peis_cutoff(eis_data, float(cp_highest_freq))
    else:
        coarse_cutoff = float(recommendation["recommended_peis_lowest_freq_hz"])
    peis_min_freq = float(np.min(np.asarray(eis_data[:, 0], dtype=float)))
    if (
        not peis_only_assessment["peis_only_sufficient"]
        and np.isfinite(coarse_cutoff)
        and coarse_cutoff <= peis_min_freq * 1.05
    ):
        coarse_cutoff = min(
            float(np.max(np.asarray(eis_data[:, 0], dtype=float))),
            peis_min_freq * RECOMMENDATION_EXTENSION_FACTOR,
        )
    cutoff_candidates = build_cutoff_candidates(
        eis_data,
        float(coarse_cutoff),
    )
    duration_candidates = _select_duration_candidates(
        float(smoothed_time[-1] - smoothed_time[0]),
        requested=cp_duration_candidates,
    )
    cp_saturation_floor = None
    if cp_saturation.get("recommended_duration_s") is not None:
        for candidate in duration_candidates:
            if float(candidate) >= float(cp_saturation["recommended_duration_s"]) - 1e-9:
                cp_saturation_floor = float(candidate)
                break

    sweep_results = []
    previous_duration_fit = full_fit_bundle["fit"]
    best_by_duration = []

    for duration_s in duration_candidates:
        duration_fft = _recover_fft_points_fast(
            smoothed_time,
            smoothed_voltage,
            smoothed_current,
            eis_data,
            cp_duration_s=duration_s,
        )
        hybrid = build_hybrid_dataset(
            eis_data,
            duration_fft["filtered_f"],
            duration_fft["recovered_z"],
            peis_lowest_freq_hz=coarse_cutoff,
        )
        fit_bundle = fit_hybrid_dataset(
            hybrid,
            previous_fit_seed=previous_duration_fit,
            reference_full_fit_seed=full_fit_bundle["fit"],
            make_figure=False,
        )
        metrics = compare_fit_bundles(full_fit_bundle, fit_bundle)
        entry = {
            "duration_s": float(duration_s),
            "peis_lowest_freq_hz": float(coarse_cutoff),
            "metrics": metrics,
            "fit": fit_bundle["fit"],
        }
        sweep_results.append(entry)
        best_by_duration.append(entry)
        previous_duration_fit = fit_bundle["fit"]

        if (
            cp_saturation_floor is not None
            and float(duration_s) >= cp_saturation_floor
            and len(best_by_duration) >= 3
            and passes_final_duration_guard(metrics)
            and all(passes_optimized_quality(item["metrics"]) for item in best_by_duration[-3:])
        ):
            break

    chosen_duration_entry, duration_reason, saturation_duration_entry = _pick_duration_choice(best_by_duration)

    cutoff_entries = []
    previous_cutoff_fit = chosen_duration_entry["fit"]
    chosen_duration_fft = _recover_fft_points_fast(
        smoothed_time,
        smoothed_voltage,
        smoothed_current,
        eis_data,
        cp_duration_s=chosen_duration_entry["duration_s"],
    )
    for cutoff_f in cutoff_candidates[:3]:
        hybrid = build_hybrid_dataset(
            eis_data,
            chosen_duration_fft["filtered_f"],
            chosen_duration_fft["recovered_z"],
            peis_lowest_freq_hz=cutoff_f,
        )
        fit_bundle = fit_hybrid_dataset(
            hybrid,
            previous_fit_seed=previous_cutoff_fit,
            reference_full_fit_seed=full_fit_bundle["fit"],
            make_figure=False,
        )
        metrics = compare_fit_bundles(full_fit_bundle, fit_bundle)
        entry = {
            "duration_s": float(chosen_duration_entry["duration_s"]),
            "peis_lowest_freq_hz": float(cutoff_f),
            "metrics": metrics,
            "fit": fit_bundle["fit"],
        }
        sweep_results.append(entry)
        cutoff_entries.append(entry)
        previous_cutoff_fit = fit_bundle["fit"]

    valid_cutoffs = [entry for entry in cutoff_entries if passes_optimized_quality(entry["metrics"])]
    if valid_cutoffs:
        best_score = min(entry["metrics"]["composite_score"] for entry in valid_cutoffs)
        tolerant = [
            entry
            for entry in valid_cutoffs
            if entry["metrics"]["composite_score"] <= best_score * 1.15 + 1e-9
        ]
        chosen_cutoff_entry = min(tolerant, key=lambda item: item["peis_lowest_freq_hz"])
        cutoff_reason = "quality_margin_cutoff"
    else:
        chosen_cutoff_entry = min(cutoff_entries, key=lambda item: item["metrics"]["composite_score"])
        cutoff_reason = "global_best_cutoff"

    chosen = {
        "duration_s": float(chosen_duration_entry["duration_s"]),
        "peis_lowest_freq_hz": float(chosen_cutoff_entry["peis_lowest_freq_hz"]),
        "metrics": chosen_cutoff_entry["metrics"],
        "fit": chosen_cutoff_entry["fit"],
    }

    parameter_extraction_time_s = time.perf_counter() - t_stage
    quality_margin = max(0.0, 0.18 - float(chosen["metrics"]["composite_score"]))
    selected_duration_s = (
        float(saturation_duration_entry["duration_s"])
        if saturation_duration_entry is not None
        else float(chosen_duration_entry["duration_s"])
    )
    selection_reason = f"{duration_reason}+{cutoff_reason}"

    return {
        "recommended_peis_lowest_freq_hz": float(chosen["peis_lowest_freq_hz"]),
        "recommended_cp_duration_s": selected_duration_s,
        "minimum_valid_cp_duration_s": float(chosen_duration_entry["duration_s"]),
        "saturation_recommended_cp_duration_s": (
            float(saturation_duration_entry["duration_s"]) if saturation_duration_entry is not None else None
        ),
        "cp_saturation": cp_saturation,
        "parameter_extraction_time_s": float(parameter_extraction_time_s),
        "quality_margin": float(quality_margin),
        "selection_reason": selection_reason,
        "timing": {
            "load_time_s": float(loaded["timing"]["load_time_s"]),
            "preprocessing_time_s": float(preprocessing_time_s),
            "fft_time_s": float(fft_time_s),
            "full_fit_time_s": float(full_fit_bundle["fit_time_s"]),
            "parameter_extraction_time_s": float(parameter_extraction_time_s),
        },
        "full_fit_result": full_fit_bundle["fit"],
        "full_filtered_f": filtered_f_full,
        "full_recovered_z": recovered_z_full,
        "fft_selection": fft_selection,
        "analysis_recommendation": recommendation,
        "sweep_results": sweep_results,
        "best_by_duration": best_by_duration,
    }


def build_cutoff_candidates(eis_data, recommended_cutoff):
    eis_freqs = np.sort(np.unique(np.asarray(eis_data[:, 0], dtype=float)))
    peis_lowest = float(np.min(eis_freqs))
    aggressive = max(float(recommended_cutoff), peis_lowest)
    targets = [aggressive, aggressive * 0.85, aggressive * 0.70, aggressive * 0.55, aggressive * 0.40, peis_lowest]

    actual = []
    for target in targets:
        usable = eis_freqs[eis_freqs >= target]
        if usable.size:
            actual.append(float(np.min(usable)))
        else:
            actual.append(peis_lowest)

    unique = sorted(set(actual), reverse=True)
    return unique


def choose_overlap_peis_cutoff(eis_data, cp_upper_bound_hz):
    """Return the PEIS/FFT merge cutoff with PEIS owning measured overlap.

    Once PEIS has been measured, any frequency at or above the measured PEIS
    low end should come from PEIS. FFT is only used below that PEIS low end.
    The CP upper bound is still accepted for API compatibility, but it should
    not move the merge cutoff upward and discard valid low-frequency PEIS data.
    """
    eis_freqs = np.sort(np.unique(np.asarray(eis_data[:, 0], dtype=float)))
    return float(np.min(eis_freqs))


def assess_peis_only_sufficiency(
    eis_data,
    recovered_f,
    recovered_z,
    agreement_tolerance=0.12,
    plateau_tolerance=0.08,
    min_points=3,
    conservative_freq_cap_ratio=10.0,
    strong_agreement_tolerance=0.03,
    relaxed_plateau_factor=2.0,
):
    """Decide whether PEIS alone already reaches the saturated LF resistance."""
    eis = np.asarray(eis_data, dtype=float)
    if eis.ndim != 2 or eis.shape[1] < 3:
        return {
            "peis_only_sufficient": False,
            "recommended_peis_lowest_freq_hz": float(np.min(eis[:, 0])) if eis.size else np.nan,
            "saturation_resistance_ohm": np.nan,
            "peis_tail_resistance_ohm": np.nan,
            "cp_tail_resistance_ohm": np.nan,
            "agreement_rel_err": np.nan,
            "selection_reason": "invalid_eis_shape",
        }

    order = np.argsort(eis[:, 0])
    freq_asc = eis[order, 0]
    rez_asc = eis[order, 1]
    n_tail = min(5, len(freq_asc))
    peis_tail = rez_asc[:n_tail]
    peis_tail_res = float(np.median(peis_tail))
    peis_tail_band = float((np.max(peis_tail) - np.min(peis_tail)) / max(abs(peis_tail_res), 1e-9))

    recovered_f = np.asarray(recovered_f, dtype=float).reshape(-1)
    recovered_z = np.asarray(recovered_z).reshape(-1)
    if recovered_f.size and recovered_z.size:
        rec_order = np.argsort(recovered_f)
        rec_tail = recovered_z.real[rec_order][: min(5, recovered_f.size)]
        cp_tail_res = float(np.median(rec_tail))
        saturation_res = cp_tail_res
    else:
        cp_tail_res = np.nan
        saturation_res = peis_tail_res

    agreement_rel_err = abs(peis_tail_res - saturation_res) / max(abs(saturation_res), 1e-9)
    rel_err = np.abs(rez_asc - saturation_res) / max(abs(saturation_res), 1e-9)

    recommended_freq = float(freq_asc[0])
    found = False
    for idx in range(min_points - 1, len(freq_asc)):
        tail_rel = rel_err[: idx + 1]
        tail_re = rez_asc[: idx + 1]
        tail_band = (np.max(tail_re) - np.min(tail_re)) / max(abs(saturation_res), 1e-9)
        if np.all(tail_rel <= agreement_tolerance) and tail_band <= plateau_tolerance:
            recommended_freq = float(freq_asc[idx])
            found = True

    peis_plateau_found = bool(np.isfinite(peis_tail_band) and peis_tail_band <= plateau_tolerance)
    cp_does_not_exceed_peis = bool(
        np.isfinite(saturation_res)
        and np.isfinite(peis_tail_res)
        and saturation_res <= peis_tail_res * (1.0 + agreement_tolerance)
    )
    strong_agreement_relaxed_plateau = bool(
        not peis_plateau_found
        and np.isfinite(agreement_rel_err)
        and agreement_rel_err <= strong_agreement_tolerance
        and cp_does_not_exceed_peis
        and np.isfinite(peis_tail_band)
        and peis_tail_band <= plateau_tolerance * relaxed_plateau_factor
    )
    peis_only_sufficient = bool(
        (
            peis_plateau_found
            or strong_agreement_relaxed_plateau
        )
        and (
            (np.isfinite(agreement_rel_err) and agreement_rel_err <= agreement_tolerance)
            or cp_does_not_exceed_peis
        )
    )
    peis_lowest_freq = float(freq_asc[0])
    if peis_only_sufficient:
        recommended_freq = min(recommended_freq, peis_lowest_freq * conservative_freq_cap_ratio)

    if strong_agreement_relaxed_plateau:
        reason = "peis_reaches_saturation_via_strong_agreement"
    elif peis_only_sufficient and cp_does_not_exceed_peis and agreement_rel_err > agreement_tolerance:
        reason = "peis_plateau_exceeds_cp_saturation"
    else:
        reason = "peis_reaches_saturation" if peis_only_sufficient else "hybrid_still_helpful"
    return {
        "peis_only_sufficient": peis_only_sufficient,
        "peis_plateau_found": peis_plateau_found,
        "strong_agreement_relaxed_plateau": strong_agreement_relaxed_plateau,
        "cp_does_not_exceed_peis": cp_does_not_exceed_peis,
        "recommended_peis_lowest_freq_hz": recommended_freq,
        "saturation_resistance_ohm": float(saturation_res),
        "peis_tail_resistance_ohm": peis_tail_res,
        "peis_tail_band": peis_tail_band,
        "cp_tail_resistance_ohm": cp_tail_res,
        "agreement_rel_err": float(agreement_rel_err),
        "selection_reason": reason,
    }


def build_duration_candidates(full_recovery, cp_duration_candidates=None):
    full_duration = float(full_recovery["time"][-1] - full_recovery["time"][0])
    if cp_duration_candidates is None:
        candidates = [float(value) for value in DEFAULT_CP_DURATIONS if value <= full_duration + 1]
        if not candidates or candidates[-1] < full_duration:
            candidates.append(full_duration)
        return sorted(set(candidates))
    values = [float(value) for value in cp_duration_candidates if value <= full_duration + 1]
    if not values or values[-1] < full_duration:
        values.append(full_duration)
    return sorted(set(values))


def _recover_fft_points_fast(smoothed_time, smoothed_voltage, smoothed_current, eis_data, cp_duration_s=None):
    time_use = smoothed_time
    voltage_use = smoothed_voltage
    current_use = smoothed_current
    if cp_duration_s is not None:
        selected = np.column_stack([smoothed_time, smoothed_voltage, smoothed_current])
        step_indices = _detect_voltage_step_indices(selected)
        if step_indices:
            duration_start = float(smoothed_time[int(step_indices[0])])
        else:
            duration_start = float(smoothed_time[0])
        mask = smoothed_time <= duration_start + float(cp_duration_s)
        time_use = smoothed_time[mask]
        voltage_use = smoothed_voltage[mask]
        current_use = smoothed_current[mask]

    fft_dt = _median_time_step(time_use, DT)
    fft_window = _window_points_for_seconds(0.5, fft_dt)
    fft_s = DC_EIS.FFT_EIS(
        time_use,
        voltage_use,
        current_use,
        "Raw",
        fft_dt,
        fft_window,
        F_MAX,
        FAST_ZERO_PAD_FACTOR,
        N_LOG_POINTS,
        eis_data=eis_data,
        make_plots=False,
    )
    return {
        "filtered_f": fft_s[6],
        "recovered_z": fft_s[7],
        "ft_f": fft_s[3],
        "ft_v": fft_s[4],
        "ft_i": fft_s[5],
    }


def _select_duration_candidates(full_duration, requested=None):
    if requested is None:
        base = [float(value) for value in DEFAULT_CP_DURATIONS]
        if full_duration not in base:
            base.append(full_duration)
    else:
        base = [float(value) for value in requested if float(value) <= full_duration + 1.0]
        if full_duration not in base:
            base.append(full_duration)
    filtered = [value for value in base if value <= full_duration + 1.0]
    return sorted(set(filtered))


def _pick_duration_choice(best_by_duration):
    quality_candidates = [entry for entry in best_by_duration if passes_optimized_quality(entry["metrics"])]
    minimum_valid = (
        min(quality_candidates, key=lambda item: (item["duration_s"], item["metrics"]["composite_score"]))
        if quality_candidates
        else None
    )

    plateau_candidates = []
    for index, entry in enumerate(best_by_duration):
        if (
            passes_optimized_quality(entry["metrics"])
            and _is_duration_plateau(best_by_duration, index)
        ):
            plateau_candidates.append(entry)
    saturation_recommended = (
        min(plateau_candidates, key=lambda item: (item["duration_s"], item["metrics"]["composite_score"]))
        if plateau_candidates
        else minimum_valid
    )

    if minimum_valid is not None:
        if saturation_recommended is not None and saturation_recommended["duration_s"] > minimum_valid["duration_s"]:
            reason = "minimum_valid_with_later_plateau"
        else:
            reason = "minimum_valid"
        return minimum_valid, reason, saturation_recommended

    return min(best_by_duration, key=lambda item: item["metrics"]["composite_score"]), "global_best", None


def recommend_optimized_configuration(
    dc_path,
    eis_path,
    current_in_mA,
    current_scale_factor,
    trim_start_time,
    cp_duration_candidates=None,
    previous_fit_seed=None,
    auto_trim=False,
    auto_trim_kwargs=None,
):
    fast = extract_optimized_parameters_fast(
        dc_path=dc_path,
        eis_path=eis_path,
        trim_start_time=trim_start_time,
        current_in_mA=current_in_mA,
        current_scale_factor=current_scale_factor,
        cp_duration_candidates=cp_duration_candidates,
        previous_fit_seed=previous_fit_seed,
        auto_trim=auto_trim,
        auto_trim_kwargs=auto_trim_kwargs,
    )

    full_recovery = recover_from_dc(
        dc_path=dc_path,
        eis_path=eis_path,
        current_in_mA=current_in_mA,
        current_scale_factor=current_scale_factor,
        trim_start_time=trim_start_time,
        cp_duration_s=None,
        auto_trim=auto_trim,
        auto_trim_kwargs=auto_trim_kwargs,
    )
    full_fit_bundle = fit_combined_data(
        full_recovery["eis_data"],
        full_recovery["filtered_f"],
        full_recovery["recovered_z"],
        peis_lowest_freq_hz=None,
        previous_fit_seed=previous_fit_seed,
        reference_full_fit_seed=fast["full_fit_result"],
    )

    peis_only = assess_peis_only_sufficiency(
        full_recovery["eis_data"],
        full_recovery["filtered_f"],
        full_recovery["recovered_z"],
    )
    peis_only_normal_lf = assess_peis_only_sufficiency(
        full_recovery["eis_data"],
        np.array([], dtype=float),
        np.array([], dtype=complex),
    )

    t_stage_b = time.perf_counter()
    peis_min_freq = float(np.min(full_recovery["eis_data"][:, 0]))
    cp_saturation_info = fast.get("cp_saturation", {})
    duration_candidates_full = [float(v) for v in build_duration_candidates(full_recovery, None)]

    def _round_up_duration(target):
        if target is None or not np.isfinite(target):
            return None
        for duration_value in duration_candidates_full:
            if duration_value >= float(target) - 1e-9:
                return float(duration_value)
        return float(target)

    heuristic_tail_band = peis_only.get("peis_tail_band", np.nan)
    heuristic_tail_is_plateau_like = bool(
        peis_only.get("peis_plateau_found", False)
        or (
            np.isfinite(heuristic_tail_band)
            and float(heuristic_tail_band) <= 0.15
        )
    )
    if (
        not peis_only["peis_only_sufficient"]
        and heuristic_tail_is_plateau_like
        and float(fast["recommended_peis_lowest_freq_hz"]) >= peis_min_freq * 0.95
        and np.isfinite(peis_only.get("agreement_rel_err", np.nan))
        and float(peis_only.get("agreement_rel_err")) <= 0.60
    ):
        peis_only["peis_only_sufficient"] = True
        peis_only["selection_reason"] = "peis_plateau_and_no_hybrid_gain"

    if peis_only["peis_only_sufficient"]:
        optimized_cp_upper_bound_hz = None
        optimized_peis_lowest_freq_hz = float(peis_only["recommended_peis_lowest_freq_hz"])
        recovery = dict(full_recovery)
        recovery["filtered_f"] = np.array([], dtype=float)
        recovery["recovered_z"] = np.array([], dtype=complex)
        fit_bundle = fit_combined_data(
            recovery["eis_data"],
            recovery["filtered_f"],
            recovery["recovered_z"],
            peis_lowest_freq_hz=optimized_peis_lowest_freq_hz,
            previous_fit_seed=fast["full_fit_result"],
            reference_full_fit_seed=full_fit_bundle["fit"],
        )
    else:
        initial_cp_duration_s = float(fast["recommended_cp_duration_s"])
        cp_saturation_duration_s = _round_up_duration(cp_saturation_info.get("recommended_duration_s"))
        if cp_saturation_duration_s is not None:
            initial_cp_duration_s = max(initial_cp_duration_s, cp_saturation_duration_s)
        optimized_cp_upper_bound_hz = float(fast["recommended_peis_lowest_freq_hz"])
        optimized_peis_lowest_freq_hz = choose_overlap_peis_cutoff(
            full_recovery["eis_data"],
            optimized_cp_upper_bound_hz,
        )
        recovery = recover_from_dc(
            dc_path=dc_path,
            eis_path=eis_path,
            current_in_mA=current_in_mA,
            current_scale_factor=current_scale_factor,
            trim_start_time=trim_start_time,
            cp_duration_s=initial_cp_duration_s,
            recovered_upper_bound_hz=optimized_cp_upper_bound_hz,
            auto_trim=auto_trim,
            auto_trim_kwargs=auto_trim_kwargs,
        )
        fit_bundle = fit_combined_data(
            recovery["eis_data"],
            recovery["filtered_f"],
            recovery["recovered_z"],
            peis_lowest_freq_hz=optimized_peis_lowest_freq_hz,
            previous_fit_seed=fast["full_fit_result"],
            reference_full_fit_seed=full_fit_bundle["fit"],
        )
    metrics = compare_fit_bundles(full_fit_bundle, fit_bundle)

    selected_cp_duration_s = None if peis_only["peis_only_sufficient"] else float(initial_cp_duration_s)
    postcheck_reason = None
    if not peis_only["peis_only_sufficient"] and not passes_final_duration_guard(metrics):
        longer_candidates = [
            float(duration)
            for duration in duration_candidates_full
            if float(duration) > float(initial_cp_duration_s)
        ]
        best_bundle = fit_bundle
        best_recovery = recovery
        best_metrics = metrics
        best_duration = float(initial_cp_duration_s)
        for duration_s in longer_candidates:
            candidate_recovery = recover_from_dc(
                dc_path=dc_path,
                eis_path=eis_path,
                current_in_mA=current_in_mA,
                current_scale_factor=current_scale_factor,
                trim_start_time=trim_start_time,
                cp_duration_s=duration_s,
                recovered_upper_bound_hz=optimized_cp_upper_bound_hz,
                auto_trim=auto_trim,
                auto_trim_kwargs=auto_trim_kwargs,
            )
            candidate_fit = fit_combined_data(
                candidate_recovery["eis_data"],
                candidate_recovery["filtered_f"],
                candidate_recovery["recovered_z"],
                peis_lowest_freq_hz=optimized_peis_lowest_freq_hz,
                previous_fit_seed=best_bundle["fit"],
                reference_full_fit_seed=full_fit_bundle["fit"],
            )
            candidate_metrics = compare_fit_bundles(full_fit_bundle, candidate_fit)
            if candidate_metrics["composite_score"] < best_metrics["composite_score"]:
                best_bundle = candidate_fit
                best_recovery = candidate_recovery
                best_metrics = candidate_metrics
                best_duration = float(duration_s)
            if passes_final_duration_guard(candidate_metrics):
                best_bundle = candidate_fit
                best_recovery = candidate_recovery
                best_metrics = candidate_metrics
                best_duration = float(duration_s)
                postcheck_reason = "duration_guard_extended"
                break
        recovery = best_recovery
        fit_bundle = best_bundle
        metrics = best_metrics
        selected_cp_duration_s = best_duration
        if postcheck_reason is None and best_duration > float(initial_cp_duration_s):
            postcheck_reason = "duration_guard_best_available"
    elif (
        not peis_only["peis_only_sufficient"]
        and cp_saturation_info.get("recommended_duration_s") is not None
        and float(selected_cp_duration_s) > float(fast["recommended_cp_duration_s"])
    ):
        postcheck_reason = (
            "cp_tail_not_saturated"
            if not cp_saturation_info.get("saturation_reached", False)
            else "cp_tail_saturation_guard"
        )

    export_ready_time_s = time.perf_counter() - t_stage_b

    return {
        "full_recovery": full_recovery,
        "full_fit_bundle": full_fit_bundle,
        "optimized_recovery": recovery,
        "optimized_fit_bundle": fit_bundle,
        "recommended_peis_lowest_freq_hz": float(optimized_peis_lowest_freq_hz),
        "recommended_min_cp_duration_s": selected_cp_duration_s,
        "recommended_cp_highest_freq_hz": (
            None if optimized_cp_upper_bound_hz is None else float(optimized_cp_upper_bound_hz)
        ),
        "optimized_metrics": metrics,
        "sweep_results": fast["sweep_results"],
        "fast_parameter_summary": fast,
        "peis_only_assessment": peis_only,
        "recommended_normal_peis_lowest_freq_hz": float(
            peis_only_normal_lf["recommended_peis_lowest_freq_hz"]
        ),
        "cp_saturation": cp_saturation_info,
        "postcheck_reason": postcheck_reason,
        "timing": {
            **fast["timing"],
            "optimized_final_fit_time_s": float(fit_bundle["fit_time_s"]),
            "stage_b_pre_export_time_s": float(export_ready_time_s),
        },
    }


def export_pipeline_variant(
    variant_label,
    variant_dir,
    recovery_bundle,
    fit_bundle,
    peis_lowest_freq_hz=None,
):
    variant_dir.mkdir(parents=True, exist_ok=True)

    eis_data = recovery_bundle["eis_data"]
    if peis_lowest_freq_hz is None:
        eis_mask = np.ones(len(eis_data), dtype=bool)
    else:
        eis_mask = eis_data[:, 0] >= float(peis_lowest_freq_hz)
    eis_used = eis_data[eis_mask]

    ref_sort = np.argsort(eis_used[:, 0])[::-1]
    rec_sort = np.argsort(recovery_bundle["filtered_f"])[::-1]

    ref_fig = PF.plot_reference_peis(
        eis_used[ref_sort, 0],
        eis_used[ref_sort, 1] + 1j * eis_used[ref_sort, 2],
        title=f"Reference PEIS - {variant_label}",
    )

    figures = [
        ref_fig,
        recovery_bundle["figures"]["ref_comp"],
        recovery_bundle["figures"]["raw"],
        recovery_bundle["figures"]["treated"],
        recovery_bundle["figures"]["dif"],
        recovery_bundle["figures"]["fft"],
        recovery_bundle["figures"]["recommendation"],
        fit_bundle["fit_fig"],
    ]

    summary_dict = {
        "Label": variant_label,
        "Selected freq range": recovery_bundle["filtered_f"],
        "Recovered ZRe": recovery_bundle["recovered_z"].real,
        "Recovered ZIm": recovery_bundle["recovered_z"].imag,
        "Recovered Zabs": np.abs(recovery_bundle["recovered_z"]),
        "Recovered Phase": np.angle(recovery_bundle["recovered_z"], deg=True),
        "Used PEIS lowest freq (Hz)": float(np.min(eis_used[:, 0])) if len(eis_used) else np.nan,
        "Used PEIS highest freq (Hz)": float(np.max(eis_used[:, 0])) if len(eis_used) else np.nan,
        "Used PEIS point count": int(len(eis_used)),
        "Recovered point count": int(len(recovery_bundle["filtered_f"])),
        "DC data filepath": recovery_bundle["dc_path"],
        "EIS data filepath": recovery_bundle["eis_path"],
    }
    summary_dict.update({k: str(v) for k, v in fit_bundle["fit"].items()})

    raw_dict = {
        "Imported time(s), V(V), I(A)": recovery_bundle["dc_data"],
        "Treated Data time": recovery_bundle["time"],
        "Treated Data Voltage": recovery_bundle["volt"],
        "Treated Data Current": recovery_bundle["current"],
        "Differentiated time": recovery_bundle["dif_time"],
        "dV/dt": recovery_bundle["dif_v"],
        "dI/dt": recovery_bundle["dif_i"],
        "FFT frequencies": recovery_bundle["ft_f_e"],
        "FFT Voltage real": recovery_bundle["ft_v_e"].real,
        "FFT Voltage imag": recovery_bundle["ft_v_e"].imag,
        "FFT Current real": recovery_bundle["ft_i_e"].real,
        "FFT Current imag": recovery_bundle["ft_i_e"].imag,
        "All recovered ZRe": recovery_bundle["ft_z_e"].real,
        "All recovered ZImag": recovery_bundle["ft_z_e"].imag,
        "Used PEIS freq": eis_used[:, 0],
        "Used PEIS ReZ": eis_used[:, 1],
        "Used PEIS ImZ": eis_used[:, 2],
        "Fit input freq": fit_bundle["freq"],
        "Fit input Re": fit_bundle["re"],
        "Fit input Im": fit_bundle["im"],
    }

    total_arc_data = {
        "ref": {
            "freq": eis_used[ref_sort, 0],
            "ReZ": eis_used[ref_sort, 1],
            "ImZ": eis_used[ref_sort, 2],
        },
        "recovered": {
            "freq": recovery_bundle["filtered_f"][rec_sort],
            "ReZ": recovery_bundle["recovered_z"][rec_sort].real,
            "ImZ": recovery_bundle["recovered_z"][rec_sort].imag,
        },
    }

    excel_path = variant_dir / f"{variant_label}.xlsx"
    Export.export_to_excel(
        summary_dict,
        raw_dict,
        figures,
        save_path=str(excel_path),
        img_dir=str(variant_dir),
        total_arc_data=total_arc_data,
    )
    OriginExport.export_origin_bundle(
        variant_dir,
        variant_label,
        dc_data=recovery_bundle["dc_data"],
        treated_time=recovery_bundle["time"],
        treated_voltage=recovery_bundle["volt"],
        treated_current=recovery_bundle["current"],
        diff_time=recovery_bundle["dif_time"],
        diff_voltage=recovery_bundle["dif_v"],
        diff_current=recovery_bundle["dif_i"],
        reference_freq=eis_used[ref_sort, 0],
        reference_re=eis_used[ref_sort, 1],
        reference_im=eis_used[ref_sort, 2],
        recovered_freq=recovery_bundle["filtered_f"][rec_sort],
        recovered_re=recovery_bundle["recovered_z"][rec_sort].real,
        recovered_im=recovery_bundle["recovered_z"][rec_sort].imag,
        fit_freq=fit_bundle["freq"],
        fit_re=fit_bundle["re"],
        fit_im=fit_bundle["im"],
        fft_analysis_freq=recovery_bundle["analysis_f"],
        fft_analysis_re=recovery_bundle["analysis_z"].real,
        fft_analysis_im=recovery_bundle["analysis_z"].imag,
        stable_mask=recovery_bundle["recommendation"]["stable_mask"],
    )
    return excel_path


def make_comparison_plot(label, eis_data, full_fit_bundle, optimized_fit_bundle):
    min_measured_freq = min(np.min(full_fit_bundle["freq"]), np.min(optimized_fit_bundle["freq"]))
    min_plot_freq = max(min(float(min_measured_freq) / 100.0, 1e-5), 1e-6)
    freq_plot = np.logspace(
        np.log10(min_plot_freq),
        np.log10(max(np.max(full_fit_bundle["freq"]), np.max(optimized_fit_bundle["freq"]))),
        600,
    )
    z_full_fit = model_curve_from_fit(full_fit_bundle["fit"], freq_plot)
    z_opt_fit = model_curve_from_fit(optimized_fit_bundle["fit"], freq_plot)

    fig, axs = plt.subplot_mosaic(
        [["nyquist", "mag"], ["nyquist", "phase"]],
        figsize=(12, 7),
        layout="constrained",
    )

    ref_z = eis_data[:, 1] + 1j * eis_data[:, 2]
    axs["nyquist"].plot(eis_data[:, 1], -eis_data[:, 2], color="0.85", linewidth=2.5, label="Reference PEIS")
    axs["nyquist"].scatter(full_fit_bundle["re"], -full_fit_bundle["im"], s=18, color="#1d3557", alpha=0.7, label="Full data used")
    axs["nyquist"].scatter(optimized_fit_bundle["re"], -optimized_fit_bundle["im"], s=18, color="#e76f51", alpha=0.7, label="Optimized data used")
    axs["nyquist"].plot(z_full_fit.real, -z_full_fit.imag, color="#1d3557", linewidth=2.0, label="Full fit")
    axs["nyquist"].plot(z_opt_fit.real, -z_opt_fit.imag, color="#e76f51", linewidth=2.0, linestyle="--", label="Optimized fit")
    axs["nyquist"].set_xlabel("Re(Z) / Ohm")
    axs["nyquist"].set_ylabel("-Im(Z) / Ohm")
    axs["nyquist"].set_title(f"Full vs Optimized Fit - {label}")
    axs["nyquist"].grid(True, alpha=0.25)
    axs["nyquist"].legend(loc="best", fontsize=8)
    x_all = np.concatenate([eis_data[:, 1], full_fit_bundle["re"], optimized_fit_bundle["re"], z_full_fit.real, z_opt_fit.real])
    y_all = np.concatenate([-eis_data[:, 2], -full_fit_bundle["im"], -optimized_fit_bundle["im"], -z_full_fit.imag, -z_opt_fit.imag])
    x_min = float(np.nanmin(x_all))
    x_max = float(np.nanmax(x_all))
    y_min = float(np.nanmin(y_all))
    y_max = float(np.nanmax(y_all))
    span = max(x_max - x_min, y_max - y_min, 1e-9) * 1.05
    x_center = 0.5 * (x_min + x_max)
    y_center = 0.5 * (y_min + y_max)
    axs["nyquist"].set_xlim(x_center - 0.5 * span, x_center + 0.5 * span)
    axs["nyquist"].set_ylim(y_center - 0.5 * span, y_center + 0.5 * span)
    axs["nyquist"].set_aspect("equal", adjustable="box")

    axs["mag"].plot(np.log10(eis_data[:, 0]), np.log10(np.abs(ref_z)), color="0.85", linewidth=2.5)
    axs["mag"].plot(np.log10(freq_plot), np.log10(np.abs(z_full_fit)), color="#1d3557", linewidth=2.0)
    axs["mag"].plot(np.log10(freq_plot), np.log10(np.abs(z_opt_fit)), color="#e76f51", linewidth=2.0, linestyle="--")
    axs["mag"].set_ylabel("log10(|Z| / Ohm)")
    axs["mag"].set_title("Magnitude")
    axs["mag"].grid(True, alpha=0.25)

    axs["phase"].plot(np.log10(eis_data[:, 0]), np.angle(ref_z, deg=True), color="0.85", linewidth=2.5)
    axs["phase"].plot(np.log10(freq_plot), np.angle(z_full_fit, deg=True), color="#1d3557", linewidth=2.0)
    axs["phase"].plot(np.log10(freq_plot), np.angle(z_opt_fit, deg=True), color="#e76f51", linewidth=2.0, linestyle="--")
    axs["phase"].set_xlabel("log10(f / Hz)")
    axs["phase"].set_ylabel("Phase / deg")
    axs["phase"].set_title("Phase")
    axs["phase"].grid(True, alpha=0.25)

    return fig


def make_sweep_plot(label, sweep_results, save_path):
    durations = [entry["duration_s"] for entry in sweep_results]
    param_err = [entry["metrics"]["parameter_rel_err_mean"] for entry in sweep_results]
    mag_err = [entry["metrics"]["model_mag_rel_mean"] for entry in sweep_results]
    nyq_err = [entry["metrics"]["model_nyquist_low_rmse"] for entry in sweep_results]
    cutoffs = [entry["peis_lowest_freq_hz"] for entry in sweep_results]

    fig, axs = plt.subplots(2, 2, figsize=(12, 7), layout="constrained")
    axs[0, 0].plot(durations, param_err, marker="o")
    axs[0, 0].set_title("Parameter Mean Relative Error")
    axs[0, 0].set_xlabel("Used CP duration / s")
    axs[0, 0].grid(True, alpha=0.25)

    axs[0, 1].plot(durations, mag_err, marker="o", color="#e76f51")
    axs[0, 1].set_title("Model Magnitude Relative Error")
    axs[0, 1].set_xlabel("Used CP duration / s")
    axs[0, 1].grid(True, alpha=0.25)

    axs[1, 0].plot(durations, nyq_err, marker="o", color="#1d3557")
    axs[1, 0].set_title("LF Nyquist Mismatch")
    axs[1, 0].set_xlabel("Used CP duration / s")
    axs[1, 0].grid(True, alpha=0.25)

    axs[1, 1].plot(durations, cutoffs, marker="o", color="#2a9d8f")
    axs[1, 1].set_title("Selected PEIS Lowest Frequency")
    axs[1, 1].set_xlabel("Used CP duration / s")
    axs[1, 1].grid(True, alpha=0.25)

    fig.suptitle(f"Optimization Sweep - {label}")
    fig.savefig(save_path, dpi=160)
    return fig


def export_analysis_outputs(
    label,
    output_dir,
    figures,
    summary_dict,
    raw_dict,
    total_arc_data,
    full_recovery,
    full_fit_bundle,
    optimized_recovery,
    optimized_fit_bundle,
    recommended_peis_lowest_freq_hz,
):
    """Stage B export wrapper with explicit timing."""
    output_dir = Path(output_dir)
    t_excel = time.perf_counter()
    excel_path = output_dir / f"{label}.xlsx"
    Export.export_to_excel(
        summary_dict,
        raw_dict,
        figures,
        save_path=str(excel_path),
        img_dir=str(output_dir),
        total_arc_data=total_arc_data,
    )
    excel_time_s = time.perf_counter() - t_excel

    full_variant_dir = output_dir / "full"
    optimized_variant_dir = output_dir / "optimized"
    full_excel_path = export_pipeline_variant(
        variant_label=f"{label}-full",
        variant_dir=full_variant_dir,
        recovery_bundle=full_recovery,
        fit_bundle=full_fit_bundle,
        peis_lowest_freq_hz=None,
    )
    optimized_excel_path = export_pipeline_variant(
        variant_label=f"{label}-optimized",
        variant_dir=optimized_variant_dir,
        recovery_bundle=optimized_recovery,
        fit_bundle=optimized_fit_bundle,
        peis_lowest_freq_hz=recommended_peis_lowest_freq_hz,
    )

    return {
        "excel_path": str(excel_path),
        "full_pipeline_excel_path": str(full_excel_path),
        "optimized_pipeline_excel_path": str(optimized_excel_path),
        "full_pipeline_dir": str(full_variant_dir),
        "optimized_pipeline_dir": str(optimized_variant_dir),
        "excel_save_time_s": float(excel_time_s),
    }


def analyze_case(
    label,
    dc_path,
    eis_path,
    output_dir,
    trim_start_time=DEFAULT_TRIM_START_TIME,
    current_in_mA=True,
    current_scale_factor=1.0,
    cp_duration_candidates=None,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    t_case = time.perf_counter()
    analysis = recommend_optimized_configuration(
        dc_path=dc_path,
        eis_path=eis_path,
        current_in_mA=current_in_mA,
        current_scale_factor=current_scale_factor,
        trim_start_time=trim_start_time,
        cp_duration_candidates=cp_duration_candidates,
    )

    full_recovery = analysis["full_recovery"]
    full_fit_bundle = analysis["full_fit_bundle"]
    optimized_recovery = analysis["optimized_recovery"]
    optimized_fit_bundle = analysis["optimized_fit_bundle"]

    comparison_fig = make_comparison_plot(
        label,
        full_recovery["eis_data"],
        full_fit_bundle,
        optimized_fit_bundle,
    )

    ref_sort = np.argsort(full_recovery["eis_data"][:, 0])[::-1]
    ref_fig = PF.plot_reference_peis(
        full_recovery["eis_data"][ref_sort, 0],
        full_recovery["eis_data"][ref_sort, 1] + 1j * full_recovery["eis_data"][ref_sort, 2],
        title=f"Reference PEIS - {label}",
    )

    sweep_plot_path = output_dir / f"{label}_sweep.png"
    sweep_fig = make_sweep_plot(label, analysis["sweep_results"], sweep_plot_path)

    figures = [
        ref_fig,
        comparison_fig,
        full_recovery["figures"]["ref_comp"],
        full_recovery["figures"]["raw"],
        full_recovery["figures"]["treated"],
        full_recovery["figures"]["dif"],
        full_recovery["figures"]["fft"],
        full_recovery["figures"]["recommendation"],
        full_fit_bundle["fit_fig"],
        optimized_fit_bundle["fit_fig"],
        sweep_fig,
    ]

    summary_dict = {
        "Label": label,
        "Trim start time (s)": trim_start_time,
        "Recommended PEIS lowest freq (Hz)": analysis["recommended_peis_lowest_freq_hz"],
        "Recommended CP highest freq (Hz)": analysis["recommended_cp_highest_freq_hz"],
        "Recommended minimum CP duration (s)": analysis["recommended_min_cp_duration_s"],
        "Minimum valid CP duration (s)": analysis["fast_parameter_summary"]["minimum_valid_cp_duration_s"],
        "Saturation recommended CP duration (s)": analysis["fast_parameter_summary"]["saturation_recommended_cp_duration_s"],
        "PEIS-only sufficient": analysis["peis_only_assessment"]["peis_only_sufficient"],
        "PEIS-only selection reason": analysis["peis_only_assessment"]["selection_reason"],
        "PEIS saturation resistance (Ohm)": analysis["peis_only_assessment"]["saturation_resistance_ohm"],
        "PEIS/CP saturation agreement": analysis["peis_only_assessment"]["agreement_rel_err"],
        "Optimization selection reason": analysis["fast_parameter_summary"]["selection_reason"],
        "Optimization quality margin": analysis["fast_parameter_summary"]["quality_margin"],
        "Parameter extraction time (s)": analysis["timing"]["parameter_extraction_time_s"],
        "Optimized composite score": analysis["optimized_metrics"]["composite_score"],
        "Optimized parameter mean relative error": analysis["optimized_metrics"]["parameter_rel_err_mean"],
        "Optimized model magnitude relative error": analysis["optimized_metrics"]["model_mag_rel_mean"],
        "Optimized model phase RMSE (deg)": analysis["optimized_metrics"]["model_phase_rmse_deg"],
        "Optimized LF Nyquist mismatch": analysis["optimized_metrics"]["model_nyquist_low_rmse"],
        "Selected freq range": full_recovery["filtered_f"],
        "Recovered ZRe": full_recovery["recovered_z"].real,
        "Recovered ZIm": full_recovery["recovered_z"].imag,
    }
    summary_dict.update({f"Full {k}": v for k, v in summarize_fit(full_fit_bundle["fit"]).items()})
    summary_dict.update({f"Optimized {k}": v for k, v in summarize_fit(optimized_fit_bundle["fit"]).items()})

    raw_dict = {
        "Imported time(s), V(V), I(A)": full_recovery["dc_data"],
        "Treated Data time": full_recovery["time"],
        "Treated Data Voltage": full_recovery["volt"],
        "Treated Data Current": full_recovery["current"],
        "Differentiated time": full_recovery["dif_time"],
        "dV/dt": full_recovery["dif_v"],
        "dI/dt": full_recovery["dif_i"],
        "FFT frequencies": full_recovery["ft_f_e"],
        "FFT Voltage real": full_recovery["ft_v_e"].real,
        "FFT Voltage imag": full_recovery["ft_v_e"].imag,
        "FFT Current real": full_recovery["ft_i_e"].real,
        "FFT Current imag": full_recovery["ft_i_e"].imag,
        "All recovered ZRe": full_recovery["ft_z_e"].real,
        "All recovered ZImag": full_recovery["ft_z_e"].imag,
        "Optimized recovered freq": optimized_recovery["filtered_f"],
        "Optimized recovered ZRe": optimized_recovery["recovered_z"].real,
        "Optimized recovered ZIm": optimized_recovery["recovered_z"].imag,
        "Full fit freq": full_fit_bundle["freq"],
        "Full fit Re": full_fit_bundle["re"],
        "Full fit Im": full_fit_bundle["im"],
        "Optimized fit freq": optimized_fit_bundle["freq"],
        "Optimized fit Re": optimized_fit_bundle["re"],
        "Optimized fit Im": optimized_fit_bundle["im"],
    }

    rec_sort = np.argsort(optimized_recovery["filtered_f"])[::-1]
    total_arc_data = {
        "ref": {
            "freq": full_recovery["eis_data"][ref_sort, 0],
            "ReZ": full_recovery["eis_data"][ref_sort, 1],
            "ImZ": full_recovery["eis_data"][ref_sort, 2],
        },
        "recovered": {
            "freq": optimized_recovery["filtered_f"][rec_sort],
            "ReZ": optimized_recovery["recovered_z"][rec_sort].real,
            "ImZ": optimized_recovery["recovered_z"][rec_sort].imag,
        },
    }

    export_info = export_analysis_outputs(
        label=label,
        output_dir=output_dir,
        figures=figures,
        summary_dict=summary_dict,
        raw_dict=raw_dict,
        total_arc_data=total_arc_data,
        full_recovery=full_recovery,
        full_fit_bundle=full_fit_bundle,
        optimized_recovery=optimized_recovery,
        optimized_fit_bundle=optimized_fit_bundle,
        recommended_peis_lowest_freq_hz=analysis["recommended_peis_lowest_freq_hz"],
    )
    comparison_path = output_dir / f"{label}_comparison.png"
    t_fig = time.perf_counter()
    comparison_fig.savefig(comparison_path, dpi=160)
    comparison_save_time_s = time.perf_counter() - t_fig
    total_analysis_time_s = time.perf_counter() - t_case

    result = {
        "label": label,
        "full_cp_duration_s": float(full_recovery["time"][-1] - full_recovery["time"][0]),
        "recommended_peis_lowest_freq_hz": analysis["recommended_peis_lowest_freq_hz"],
        "recommended_cp_highest_freq_hz": analysis["recommended_cp_highest_freq_hz"],
        "recommended_min_cp_duration_s": analysis["recommended_min_cp_duration_s"],
        "minimum_valid_cp_duration_s": analysis["fast_parameter_summary"]["minimum_valid_cp_duration_s"],
        "saturation_recommended_cp_duration_s": analysis["fast_parameter_summary"]["saturation_recommended_cp_duration_s"],
        "peis_only_assessment": analysis["peis_only_assessment"],
        "optimization_selection_reason": analysis["fast_parameter_summary"]["selection_reason"],
        "optimization_quality_margin": analysis["fast_parameter_summary"]["quality_margin"],
        "full_fit": summarize_fit(full_fit_bundle["fit"]),
        "optimized_fit": summarize_fit(optimized_fit_bundle["fit"]),
        "optimized_metrics": analysis["optimized_metrics"],
        "timing": {
            **analysis["timing"],
            "comparison_save_time_s": float(comparison_save_time_s),
            "excel_save_time_s": float(export_info["excel_save_time_s"]),
            "total_analysis_time_s": float(total_analysis_time_s),
        },
        "sweep_results": [
            {
                "duration_s": entry["duration_s"],
                "peis_lowest_freq_hz": entry["peis_lowest_freq_hz"],
                "metrics": entry["metrics"],
                "fit": summarize_fit(entry["fit"]),
            }
            for entry in analysis["sweep_results"]
        ],
        "excel_path": export_info["excel_path"],
        "comparison_path": str(comparison_path),
        "sweep_path": str(sweep_plot_path),
        "full_pipeline_excel_path": export_info["full_pipeline_excel_path"],
        "optimized_pipeline_excel_path": export_info["optimized_pipeline_excel_path"],
        "full_pipeline_dir": export_info["full_pipeline_dir"],
        "optimized_pipeline_dir": export_info["optimized_pipeline_dir"],
    }
    plt.close("all")
    return result


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = analyze_case(
        label="260417-8-mpr",
        dc_path=INPUT_DIR / "300 rapid measurement_02_CA_C02.mpr",
        eis_path=INPUT_DIR / "300 rapid measurement_01_PEIS_C02.mpr",
        output_dir=OUTPUT_DIR / "260417-8-mpr",
        trim_start_time=DEFAULT_TRIM_START_TIME,
        current_in_mA=True,
        current_scale_factor=1.0,
    )
    summary_path = OUTPUT_DIR / "full_vs_optimized_summary.json"
    summary_path.write_text(json.dumps(json_safe(result), indent=2), encoding="utf-8")
    print(json.dumps(json_safe(result), indent=2))
    print(f"Saved: {summary_path}")


if __name__ == "__main__":
    main()
