from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy as np

from stabilization_policy import StabilizationSettings, analyze_pre_peis_stability

DEFAULT_FFT_READY_BIN_S = 0.1
DEFAULT_FFT_READY_DESPIKE_WINDOW_S = 1.0
DEFAULT_FFT_READY_DESPIKE_SIGMA = 6.0
DEFAULT_FFT_READY_DESPIKE_STEP_GUARD_S = 2.0


@dataclass
class ChunkedCaRun:
    data: np.ndarray
    total_duration_s: float
    chunk_count: int
    last_assessment: Optional[Dict]
    stop_reason: str

    def to_dict(self) -> Dict:
        return asdict(self)


def _concat_ca_segments(existing: np.ndarray, segment: np.ndarray) -> np.ndarray:
    existing = np.asarray(existing, dtype=float)
    segment = np.asarray(segment, dtype=float)
    if existing.size == 0:
        return segment.copy()
    if segment.size == 0:
        return existing.copy()

    merged = segment.copy()
    time_offset = float(existing[-1, 0])
    if merged.shape[0] > 0:
        merged[:, 0] = merged[:, 0] - float(merged[0, 0]) + time_offset
    return np.vstack([existing, merged])


def run_chunked_bias_stabilization(
    biologic,
    *,
    v_dc: float,
    chunk_duration_s: float = 5.0,
    dt_record: float = 0.5,
    max_total_s: float = 60.0,
    settings: Optional[StabilizationSettings] = None,
) -> ChunkedCaRun:
    settings = settings or StabilizationSettings(min_hold_s=10.0, max_hold_s=max_total_s)
    collected = np.empty((0, 3), dtype=float)
    chunk_count = 0
    last_assessment = None

    while True:
        segment = biologic.run_ca_hold(v_dc, chunk_duration_s, dt_record=dt_record)
        collected = _concat_ca_segments(collected, segment)
        chunk_count += 1

        assessment = analyze_pre_peis_stability(
            collected[:, 0],
            collected[:, 2],
            settings=settings,
            peis_already_started=False,
        )
        last_assessment = assessment.to_dict()
        hold_now = float(collected[-1, 0] - collected[0, 0]) if len(collected) else 0.0

        if assessment.should_start_peis:
            return ChunkedCaRun(
                data=collected,
                total_duration_s=hold_now,
                chunk_count=chunk_count,
                last_assessment=last_assessment,
                stop_reason="stabilized",
            )
        if hold_now >= float(max_total_s) - 1e-9 or not assessment.should_continue_hold:
            return ChunkedCaRun(
                data=collected,
                total_duration_s=hold_now,
                chunk_count=chunk_count,
                last_assessment=last_assessment,
                stop_reason="max_total_reached",
            )


def run_chunked_dv_scout(
    biologic,
    *,
    v_dc: float,
    dv: float,
    chunk_duration_s: float = 5.0,
    dt_record: float = 0.01,
    max_total_s: float = 60.0,
):
    # Imported lazily to keep this module light for GUI/runtime use.
    import sys

    compare_dir = Path(__file__).resolve().parent.parent / "Convert_CP_to_EIS 1"
    if str(compare_dir) not in sys.path:
        sys.path.insert(0, str(compare_dir))
    import compare_full_vs_optimized_fit as OPT  # noqa: E402

    collected = np.empty((0, 3), dtype=float)
    chunk_count = 0
    last_assessment = None

    while True:
        segment = biologic.run_ca_perturbation(v_dc, dv, chunk_duration_s, dt_record=dt_record)
        collected = _concat_ca_segments(collected, segment)
        chunk_count += 1
        last_assessment = OPT.assess_cp_saturation(collected[:, 0], collected[:, 2])
        hold_now = float(collected[-1, 0] - collected[0, 0]) if len(collected) else 0.0

        if bool(last_assessment.get("saturation_reached", False)):
            return ChunkedCaRun(
                data=collected,
                total_duration_s=hold_now,
                chunk_count=chunk_count,
                last_assessment=last_assessment,
                stop_reason="cp_saturation_reached",
            )
        if hold_now >= float(max_total_s) - 1e-9:
            return ChunkedCaRun(
                data=collected,
                total_duration_s=hold_now,
                chunk_count=chunk_count,
                last_assessment=last_assessment,
                stop_reason="max_total_reached",
            )


def save_ca_txt(output_path, data: Sequence[Sequence[float]]) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(output_path, np.asarray(data, dtype=float), header="time/s  V/V  I/A", comments="")
    return output_path


def _moving_average(y: np.ndarray, window_points: int) -> np.ndarray:
    values = np.asarray(y, dtype=float).reshape(-1)
    if len(values) == 0:
        return values.copy()
    window = max(1, int(window_points))
    if window <= 1 or len(values) < 3:
        return values.copy()
    if window > len(values):
        window = len(values)
    if window % 2 == 0:
        window = max(1, window - 1)
    if window <= 1:
        return values.copy()
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=float) / float(window)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed[: len(values)]


def _median_time_step(time_values: np.ndarray, fallback: float = DEFAULT_FFT_READY_BIN_S) -> float:
    values = np.asarray(time_values, dtype=float).reshape(-1)
    if len(values) < 2:
        return float(fallback)
    diffs = np.diff(values)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if len(diffs) == 0:
        return float(fallback)
    return float(np.median(diffs))


def _window_points_for_seconds(window_s: float, dt_s: float, min_points: int = 5) -> int:
    dt = max(float(dt_s), 1e-9)
    points = max(int(min_points), int(round(float(window_s) / dt)))
    if points % 2 == 0:
        points += 1
    return points


def _detect_voltage_step_idx(work: np.ndarray) -> int:
    arr = np.asarray(work, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2 or len(arr) < 3:
        return len(arr)
    dv = np.abs(np.diff(arr[:, 1]))
    finite = np.isfinite(dv)
    if not np.any(finite):
        return len(arr)
    max_dv = float(np.nanmax(dv))
    if max_dv <= 1e-12:
        return len(arr)
    return int(np.nanargmax(dv)) + 1


def _hampel_despike(values: np.ndarray, window_points: int, n_sigma: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if len(arr) < 3:
        return arr.copy()

    window = max(3, int(window_points))
    if window % 2 == 0:
        window += 1
    half = window // 2
    cleaned = arr.copy()

    for idx, value in enumerate(arr):
        if not np.isfinite(value):
            continue
        lo = max(0, idx - half)
        hi = min(len(arr), idx + half + 1)
        local = arr[lo:hi]
        local = local[np.isfinite(local)]
        if len(local) < 3:
            continue
        median = float(np.median(local))
        mad = float(np.median(np.abs(local - median)))
        if mad <= 0.0 or not np.isfinite(mad):
            continue
        robust_sigma = 1.4826 * mad
        if abs(float(value) - median) > float(n_sigma) * robust_sigma:
            cleaned[idx] = median

    return cleaned


def _median_bin_segment(segment: np.ndarray, bin_s: Optional[float]) -> np.ndarray:
    seg = np.asarray(segment, dtype=float)
    if bin_s is None or float(bin_s) <= 0.0 or len(seg) < 2:
        return seg.copy()

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
    data: Sequence[Sequence[float]],
    *,
    average_bin_s: Optional[float] = None,
    segmented_smoothing_window_points: Optional[int] = None,
    current_despike: bool = False,
    despike_window_s: float = DEFAULT_FFT_READY_DESPIKE_WINDOW_S,
    despike_sigma: float = DEFAULT_FFT_READY_DESPIKE_SIGMA,
    despike_step_guard_s: float = DEFAULT_FFT_READY_DESPIKE_STEP_GUARD_S,
) -> np.ndarray:
    """
    Convert raw CA into an FFT-ready trace.

    Voltage-step information is preserved, but current-only isolated spikes can
    be removed segment-wise before dI/dt is calculated. Rows are not deleted, so
    FFT timing remains well behaved.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 3 or len(arr) == 0:
        return np.asarray(arr, dtype=float).copy()

    work = arr[:, :3].copy()
    work[:, 0] = work[:, 0] - float(work[0, 0])

    step_idx = _detect_voltage_step_idx(work)
    if step_idx <= 0 or step_idx >= len(work):
        segments = [work]
    else:
        segments = [work[:step_idx], work[step_idx:]]

    processed_segments = []
    for segment_index, segment in enumerate(segments):
        seg = np.asarray(segment, dtype=float).copy()
        if current_despike and len(seg) >= 3:
            dt_values = np.diff(seg[:, 0])
            dt_values = dt_values[np.isfinite(dt_values) & (dt_values > 0)]
            dt_med = float(np.median(dt_values)) if len(dt_values) else 0.1
            window_points = max(3, int(round(float(despike_window_s) / max(dt_med, 1e-9))))
            original_current = seg[:, 2].copy()
            seg[:, 2] = _hampel_despike(seg[:, 2], window_points, n_sigma=float(despike_sigma))
            if len(segments) == 2 and segment_index == 1 and float(despike_step_guard_s) > 0.0:
                guard_mask = seg[:, 0] <= float(seg[0, 0]) + float(despike_step_guard_s)
                seg[guard_mask, 2] = original_current[guard_mask]
        processed_segments.append(_median_bin_segment(seg, average_bin_s))

    if processed_segments:
        work = np.vstack(processed_segments)

    if segmented_smoothing_window_points is not None and int(segmented_smoothing_window_points) > 1 and len(work) >= 3:
        step_idx = _detect_voltage_step_idx(work)

        smoothed = work.copy()
        pre = work[:step_idx]
        post = work[step_idx:]
        if len(pre):
            smoothed[:step_idx, 2] = _moving_average(pre[:, 2], int(segmented_smoothing_window_points))
        if len(post):
            smoothed[step_idx:, 2] = _moving_average(post[:, 2], int(segmented_smoothing_window_points))
        work = smoothed

    return work


def recommend_peis_lf_from_cp_txt(dc_path, *, current_in_mA: bool = False) -> Dict:
    import sys

    compare_dir = Path(__file__).resolve().parent.parent / "Convert_CP_to_EIS 1"
    if str(compare_dir) not in sys.path:
        sys.path.insert(0, str(compare_dir))

    import Convert_CP_to_EIS as DC_EIS  # noqa: E402
    import Load_CP_Data as LD  # noqa: E402
    import Smooth_and_Interpolate as SI  # noqa: E402
    import compare_full_vs_optimized_fit as OPT  # noqa: E402

    dc_data = LD.load_dc_from_path(
        str(dc_path),
        current_in_mA=current_in_mA,
        CA_step_only=False,
        trim_start_time=None,
        current_scale_factor=1.0,
        auto_trim=True,
        auto_trim_kwargs={
            "voltage_step_sigma": 8.0,
            "smooth_window_points": 11,
            "stability_window_s": 2.0,
            "sustain_window_s": 3.0,
            "std_factor": 2.5,
        },
    )
    fft_ready = build_fft_ready_ca_trace(
        dc_data,
        average_bin_s=DEFAULT_FFT_READY_BIN_S,
        segmented_smoothing_window_points=None,
        current_despike=True,
    )
    positive_time = np.asarray(fft_ready[:, 0], dtype=float)
    if len(positive_time) < 2:
        raise ValueError("Need at least two processed CA points to estimate a CP-only LF seed.")
    fft_dt = _median_time_step(positive_time, fallback=DEFAULT_FFT_READY_BIN_S)
    time_arr = np.arange(
        float(positive_time.min()),
        float(positive_time.max()) + 0.5 * float(fft_dt),
        float(fft_dt),
    )
    if len(time_arr) < 2:
        time_arr = positive_time.copy()
    volt = np.interp(time_arr, positive_time, np.asarray(fft_ready[:, 1], dtype=float))
    current = np.interp(time_arr, positive_time, np.asarray(fft_ready[:, 2], dtype=float))
    positive_time = np.asarray(time_arr, dtype=float)
    dt_est = _median_time_step(positive_time, fallback=fft_dt)
    fft_window = _window_points_for_seconds(0.5, dt_est)
    nyquist_hz = 0.5 / max(dt_est, 1e-12)
    f_max_limit = float(OPT.F_MAX) if getattr(OPT, "F_MAX", None) is not None else float(nyquist_hz)
    synthetic_ref_hz = float(min(max(nyquist_hz, 1e-3), f_max_limit))
    synthetic_eis = np.array([[synthetic_ref_hz, 0.0, 0.0]], dtype=float)

    fft_s = DC_EIS.FFT_EIS(
        time_arr,
        volt,
        current,
        "Raw",
        dt_est,
        fft_window,
        OPT.F_MAX,
        OPT.FAST_ZERO_PAD_FACTOR,
        OPT.N_LOG_POINTS,
        eis_data=synthetic_eis,
        make_plots=False,
    )
    ft_f = fft_s[3]
    ft_v = fft_s[4]
    ft_i = fft_s[5]
    analysis_max_f = DC_EIS.resolve_fft_max_f(OPT.F_MAX, eis_data=synthetic_eis, ft_freq=ft_f)
    analysis_f, analysis_z = DC_EIS.extract_log_spaced_impedance(
        ft_f,
        ft_v,
        ft_i,
        max_f=analysis_max_f,
        n_log_points=max(OPT.N_LOG_POINTS, 120),
    )
    recommendation = DC_EIS.recommend_peis_lowest_frequency(analysis_f, analysis_z)
    cp_saturation = OPT.assess_cp_saturation(time_arr, current)
    return {
        "dc_path": str(dc_path),
        "synthetic_reference_hz": synthetic_ref_hz,
        "analysis_f": np.asarray(analysis_f, dtype=float),
        "analysis_z": np.asarray(analysis_z, dtype=complex),
        "recommendation": recommendation,
        "cp_saturation": cp_saturation,
        "smoothed_duration_s": float(time_arr[-1] - time_arr[0]) if len(time_arr) else 0.0,
        "fft_ready_trace": np.asarray(fft_ready, dtype=float),
        "fft_ready_average_bin_s": DEFAULT_FFT_READY_BIN_S,
        "fft_ready_current_despike": True,
        "fft_ready_despike_window_s": DEFAULT_FFT_READY_DESPIKE_WINDOW_S,
        "fft_ready_despike_sigma": DEFAULT_FFT_READY_DESPIKE_SIGMA,
        "fft_ready_despike_step_guard_s": DEFAULT_FFT_READY_DESPIKE_STEP_GUARD_S,
        "fft_dt_s": dt_est,
        "fft_window_points": int(fft_window),
        "fft_ready_segmented_smoothing_window_points": None,
    }
