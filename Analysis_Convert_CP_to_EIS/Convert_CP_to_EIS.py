# -*- coding: utf-8 -*-
"""
FFT-based conversion from DC transient data to recovered impedance.
"""

import matplotlib.pyplot as plt
from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)
import numpy as np

import Load_CP_Data as LD
import Plotting_Functions as PF
import Smooth_and_Interpolate as SI


def _moving_average(values, window):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    window = max(int(window), 1)
    if window <= 1:
        return values.copy()
    if window % 2 == 0:
        window += 1
    if window > values.size:
        window = values.size if values.size % 2 == 1 else max(values.size - 1, 1)
    if window <= 1:
        return values.copy()
    kernel = np.ones(window, dtype=float) / window
    padded = np.pad(values, (window // 2, window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def resolve_fft_max_f(max_f, eis_data=None, ft_freq=None):
    """
    Resolve the target recovered-frequency upper bound.

    If `max_f` is None and reference PEIS data is available, use the lowest
    measured PEIS frequency as the recovered-data upper limit.
    """
    if max_f is not None:
        return float(max_f)

    if eis_data is not None and len(eis_data) > 0:
        lowest_ref_f = float(np.min(np.asarray(eis_data)[:, 0]))
        print(f"Recovered-frequency upper bound set from reference PEIS lowest f: {lowest_ref_f:.4f} Hz")
        return lowest_ref_f

    if ft_freq is not None:
        positive = np.asarray(ft_freq)
        positive = positive[np.isfinite(positive) & (positive > 0)]
        if positive.size:
            fallback = float(np.max(positive))
            print(f"Recovered-frequency upper bound fallback: {fallback:.4f} Hz")
            return fallback

    raise ValueError("Could not resolve FFT recovered-frequency upper bound.")


def extract_log_spaced_impedance(FT_f, FT_V, FT_I, max_f, n_log_points=100, min_f=None):
    """
    Extract log-spaced impedance points from one-sided FFT spectra.
    """
    pos_mask = (FT_f > 0) & (FT_f <= float(max_f))
    if min_f is not None:
        pos_mask &= FT_f >= float(min_f)

    pos_f = FT_f[pos_mask]
    pos_FT_V = FT_V[pos_mask]
    pos_FT_I = FT_I[pos_mask]

    if pos_f.size == 0:
        return np.array([]), np.array([], dtype=complex)

    f_min_avail = float(np.min(pos_f))
    f_max_avail = float(np.max(pos_f))
    n_points = max(int(n_log_points), 2)
    log_f_targets = np.logspace(np.log10(f_min_avail), np.log10(f_max_avail), n_points)

    recovered_Z = np.array([], dtype=complex)
    filtered_f = np.array([], dtype=float)
    seen_idx = set()

    for f_target in log_f_targets:
        idx = int(np.argmin(np.abs(pos_f - f_target)))
        if idx not in seen_idx:
            seen_idx.add(idx)
            filtered_f = np.append(filtered_f, pos_f[idx])
            recovered_Z = np.append(recovered_Z, pos_FT_V[idx] / pos_FT_I[idx])

    return filtered_f, recovered_Z


def recommend_peis_lowest_frequency(
    freq,
    recovered_z,
    stable_mag_rel_tol=0.10,
    stable_phase_tol_deg=8.0,
    smooth_window_points=7,
    max_consecutive_unstable=3,
):
    """
    Recommend the lowest conventional PEIS frequency needed to cover the noisy
    high-frequency side of the FFT-recovered spectrum.

    Interpretation:
    - frequencies below the returned cutoff are smooth enough to trust from FFT
    - frequencies above the returned cutoff are better covered by conventional PEIS
    """
    freq = np.asarray(freq, dtype=float)
    recovered_z = np.asarray(recovered_z, dtype=complex)

    finite = np.isfinite(freq) & np.isfinite(recovered_z.real) & np.isfinite(recovered_z.imag) & (freq > 0)
    freq = freq[finite]
    recovered_z = recovered_z[finite]

    if freq.size < 8:
        return {
            "recommended_peis_lowest_freq_hz": None,
            "analysis_freq": freq,
            "stable_mask": np.ones(freq.shape, dtype=bool),
            "mag_rel_residual": np.zeros(freq.shape, dtype=float),
            "phase_residual_deg": np.zeros(freq.shape, dtype=float),
        }

    idx = np.argsort(freq)
    freq = freq[idx]
    recovered_z = recovered_z[idx]

    z_abs = np.abs(recovered_z)
    phase_deg = np.angle(recovered_z, deg=True)

    smooth_abs = _moving_average(z_abs, smooth_window_points)
    smooth_phase = _moving_average(phase_deg, smooth_window_points)

    mag_rel_residual = np.abs(z_abs - smooth_abs) / np.maximum(smooth_abs, 1e-12)
    phase_residual_deg = np.abs(phase_deg - smooth_phase)
    stable_mask = (mag_rel_residual <= stable_mag_rel_tol) & (phase_residual_deg <= stable_phase_tol_deg)

    cutoff_idx = len(freq) - 1
    consecutive_unstable = 0
    for idx_point in range(len(freq)):
        consecutive_unstable = consecutive_unstable + 1 if not stable_mask[idx_point] else 0
        if consecutive_unstable >= max_consecutive_unstable:
            cutoff_idx = max(0, idx_point - max_consecutive_unstable)
            break

    return {
        "recommended_peis_lowest_freq_hz": float(freq[cutoff_idx]),
        "analysis_freq": freq,
        "stable_mask": stable_mask,
        "mag_rel_residual": mag_rel_residual,
        "phase_residual_deg": phase_residual_deg,
    }


def summarize_cp_duration_requirements(
    time,
    target_lowest_freq_hz,
    recommended_peis_lowest_freq_hz=None,
    periods_required=1.0,
    conservative_factor=3.0,
):
    """
    Summarize how long the CP/CA transient should be to support a target low
    frequency in FFT-based recovery.

    Notes
    -----
    Zero-padding can densify the FFT grid, but it does not extend the true
    low-frequency information content. The usable low-frequency limit still
    scales with the original time duration.
    """
    time = np.asarray(time, dtype=float)
    finite = np.isfinite(time)
    time = time[finite]

    if time.size < 2:
        actual_duration_s = None
    else:
        actual_duration_s = float(np.max(time) - np.min(time))

    def _duration_for_freq(freq_hz):
        if freq_hz is None or not np.isfinite(freq_hz) or freq_hz <= 0:
            return None
        return float(periods_required / float(freq_hz))

    def _conservative_duration_for_freq(freq_hz):
        base = _duration_for_freq(freq_hz)
        if base is None:
            return None
        return float(conservative_factor * base)

    target_min_cp_time_s = _duration_for_freq(target_lowest_freq_hz)
    target_conservative_cp_time_s = _conservative_duration_for_freq(target_lowest_freq_hz)

    peis_crossover_min_cp_time_s = _duration_for_freq(recommended_peis_lowest_freq_hz)
    peis_crossover_conservative_cp_time_s = _conservative_duration_for_freq(
        recommended_peis_lowest_freq_hz
    )

    return {
        "actual_cp_duration_s": actual_duration_s,
        "duration_limited_lowest_freq_hz": (
            None if actual_duration_s is None or actual_duration_s <= 0 else float(1.0 / actual_duration_s)
        ),
        "target_lowest_freq_hz": (
            None if target_lowest_freq_hz is None or not np.isfinite(target_lowest_freq_hz) else float(target_lowest_freq_hz)
        ),
        "target_min_cp_time_s": target_min_cp_time_s,
        "target_conservative_cp_time_s": target_conservative_cp_time_s,
        "recommended_peis_lowest_freq_hz": (
            None
            if recommended_peis_lowest_freq_hz is None or not np.isfinite(recommended_peis_lowest_freq_hz)
            else float(recommended_peis_lowest_freq_hz)
        ),
        "recommended_peis_min_cp_time_s": peis_crossover_min_cp_time_s,
        "recommended_peis_conservative_cp_time_s": peis_crossover_conservative_cp_time_s,
        "target_duration_margin": (
            None
            if actual_duration_s is None or target_min_cp_time_s is None or target_min_cp_time_s <= 0
            else float(actual_duration_s / target_min_cp_time_s)
        ),
    }


def plot_peis_cutoff_recommendation(freq, recovered_z, recommendation, title=""):
    """
    Visualize FFT scatter and the recommended conventional-PEIS crossover.
    """
    freq = np.asarray(freq, dtype=float)
    recovered_z = np.asarray(recovered_z, dtype=complex)
    rec = recommendation["recommended_peis_lowest_freq_hz"]
    stable_mask = np.asarray(recommendation["stable_mask"], dtype=bool)

    fig, axs = plt.subplot_mosaic(
        [["nyquist", "mag"], ["nyquist", "phase"]],
        figsize=(11, 6),
        layout="constrained",
    )

    axs["nyquist"].scatter(
        recovered_z.real[stable_mask],
        -recovered_z.imag[stable_mask],
        s=22,
        color="#2a9d8f",
        label="FFT stable",
    )
    axs["nyquist"].scatter(
        recovered_z.real[~stable_mask],
        -recovered_z.imag[~stable_mask],
        s=22,
        color="#e76f51",
        label="FFT noisy",
    )
    axs["nyquist"].set_xlabel("Re(Z) / Ohm")
    axs["nyquist"].set_ylabel("-Im(Z) / Ohm")
    axs["nyquist"].set_title("FFT Reliability Map" if not title else f"FFT Reliability Map - {title}")
    axs["nyquist"].grid(True, alpha=0.25)
    axs["nyquist"].legend(loc="best", fontsize=9)

    axs["mag"].scatter(np.log10(freq), np.log10(np.abs(recovered_z)), s=18, c=np.where(stable_mask, "#2a9d8f", "#e76f51"))
    if rec is not None:
        axs["mag"].axvline(np.log10(rec), color="black", linestyle="--", linewidth=1.2, label=f"Recommend PEIS >= {rec:.4g} Hz")
        axs["mag"].legend(loc="best", fontsize=8)
    axs["mag"].set_ylabel("log10(|Z| / Ohm)")
    axs["mag"].set_title("Magnitude")
    axs["mag"].grid(True, alpha=0.25)

    axs["phase"].scatter(np.log10(freq), np.angle(recovered_z, deg=True), s=18, c=np.where(stable_mask, "#2a9d8f", "#e76f51"))
    if rec is not None:
        axs["phase"].axvline(np.log10(rec), color="black", linestyle="--", linewidth=1.2)
    axs["phase"].set_xlabel("log10(f / Hz)")
    axs["phase"].set_ylabel("Phase / deg")
    axs["phase"].set_title("Phase")
    axs["phase"].grid(True, alpha=0.25)

    return fig


def FFT_EIS(
    time,
    Volt,
    Current,
    method,
    dt,
    window,
    max_f,
    zero_pad_factor=50,
    n_log_points=100,
    eis_data=None,
    make_plots=True,
):
    """
    Recover impedance points from DC transient data using FFT.

    `max_f` is the highest recovered frequency to keep. If `None`, the lowest
    PEIS frequency is used when reference EIS data is available.
    """
    dif_V = np.gradient(Volt, time)
    dif_I = np.gradient(Current, time)

    if method == "Smooth":
        time, dif_V, dif_I = SI.smooth_and_interpolate(time, dif_V, dif_I, dt, window)

    print("Differentiated Data:")
    dif_plot = PF.plot_DC(time, dif_V, dif_I, "d " + method + "/dt") if make_plots else None

    # Zero-padding increases frequency-grid density for smoother log extraction.
    N_original = len(dif_I)
    N_fft = N_original * zero_pad_factor
    print(f"FFT: original N={N_original}, zero-padded N={N_fft} (factor={zero_pad_factor})")

    FT_V = np.fft.fft(dif_V, n=N_fft)
    FT_I = np.fft.fft(dif_I, n=N_fft)
    FT_f = np.fft.fftfreq(N_fft, dt)

    fig = None
    if make_plots:
        fig, ax1 = plt.subplots(figsize=(8, 5))
        ax1.scatter(FT_f, np.abs(FT_V), label="Fourier Transform of dV/dt", c=np.angle(FT_V, deg=True), cmap="Blues", s=10)
        ax1.set_xscale("log")
        ax1.set_title("Fourier Transforms")
        ax1.set_ylabel("FFT(dV/dt)")
        ax2 = ax1.twinx()
        ax2.scatter(FT_f, np.abs(FT_I), label="Fourier Transform of dI/dt", c=np.angle(FT_I, deg=True), cmap="Greens", s=10)
        ax2.set_ylabel("FFT(dI/dt)")
        handles = []
        labels = []
        for ax in (ax1, ax2):
            ax_handles, ax_labels = ax.get_legend_handles_labels()
            handles.extend(ax_handles)
            labels.extend(ax_labels)
        if handles:
            ax1.legend(handles, labels)

    target_max_f = resolve_fft_max_f(max_f, eis_data=eis_data, ft_freq=FT_f)
    filtered_f, recovered_Z = extract_log_spaced_impedance(
        FT_f,
        FT_V,
        FT_I,
        max_f=target_max_f,
        n_log_points=n_log_points,
    )

    if filtered_f.size == 0:
        raise ValueError("No recovered FFT impedance points were found in the requested frequency range.")

    print(
        f"Recovered {len(filtered_f)} log-spaced impedance points "
        f"from {filtered_f.min():.4f} Hz to {filtered_f.max():.4f} Hz"
    )

    Z_DC_plot = PF.plot_Z(recovered_Z, filtered_f) if make_plots else None

    if eis_data is not None:
        EIS_data_full = eis_data
    else:
        EIS_data_full, _ = LD.load_txt_EIS("Select full Impedance data file")

    ref_f = EIS_data_full[:, 0]
    Z_full = np.array([EIS_data_full[:, 1] + 1j * EIS_data_full[:, 2]]).T

    Z_comp_plot = PF.plot_Z_comp(Z_full, ref_f, recovered_Z, filtered_f) if make_plots else None

    return (
        time,
        dif_V,
        dif_I,
        FT_f,
        FT_V,
        FT_I,
        filtered_f,
        recovered_Z,
        Z_full.T,
        dif_plot,
        fig,
        Z_DC_plot,
        Z_comp_plot,
    )
