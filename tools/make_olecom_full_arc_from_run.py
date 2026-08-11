from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS") not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS"))

from Analysis_Convert_CP_to_EIS import Convert_CP_to_EIS as DC_EIS  # noqa: E402
from scipy.optimize import least_squares  # noqa: E402

from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF  # noqa: E402


def _run_file(run_dir: Path, candidate: object, fallback_name: str) -> Path:
    """Resolve copied run folders whose summary.json has another PC's paths."""
    if candidate:
        path = Path(str(candidate))
        if path.exists():
            return path
        local = run_dir / path.name
        if local.exists():
            return local
    return run_dir / fallback_name


def _load_ca_txt(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    data = np.loadtxt(path, skiprows=1)
    return data[:, 0], data[:, 1], data[:, 2]


def _uniform_ca_grid(t_raw: np.ndarray, v_raw: np.ndarray, i_raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    t_raw = np.asarray(t_raw, dtype=float)
    v_raw = np.asarray(v_raw, dtype=float)
    i_raw = np.asarray(i_raw, dtype=float)
    finite = np.isfinite(t_raw) & np.isfinite(v_raw) & np.isfinite(i_raw)
    t_raw = t_raw[finite]
    v_raw = v_raw[finite]
    i_raw = i_raw[finite]
    if len(t_raw) < 2:
        return t_raw, v_raw, i_raw, 0.1
    order = np.argsort(t_raw)
    t_raw = t_raw[order]
    v_raw = v_raw[order]
    i_raw = i_raw[order]
    t_raw, unique_idx = np.unique(t_raw, return_index=True)
    v_raw = v_raw[unique_idx]
    i_raw = i_raw[unique_idx]
    if len(t_raw) < 2:
        return t_raw, v_raw, i_raw, 0.1
    dt_values = np.diff(t_raw)
    dt_values = dt_values[np.isfinite(dt_values) & (dt_values > 0)]
    dt = float(np.median(dt_values)) if len(dt_values) else 0.1
    if not np.isfinite(dt) or dt <= 0:
        dt = 0.1
    t = np.arange(float(t_raw[0]), float(t_raw[-1]) + 0.5 * dt, dt)
    v = np.interp(t, t_raw, v_raw)
    i = np.interp(t, t_raw, i_raw)
    return t, v, i, dt


def _load_peis_txt(path: Path) -> np.ndarray:
    data = np.loadtxt(path, skiprows=1)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    valid = np.isfinite(data).all(axis=1) & (data[:, 0] > 0)
    return data[valid]


def _average_duplicate_freq(peis: np.ndarray, ndigits: int = 8) -> np.ndarray:
    keys = np.round(np.log10(peis[:, 0]), ndigits)
    out = []
    for key in np.unique(keys):
        group = peis[keys == key]
        out.append([float(np.median(group[:, 0])), float(np.mean(group[:, 1])), float(np.mean(group[:, 2]))])
    arr = np.asarray(out, dtype=float)
    return arr[np.argsort(arr[:, 0])]


def _window_points_for_seconds(seconds: float, dt: float) -> int:
    points = max(3, int(round(float(seconds) / max(float(dt), 1e-12))))
    if points % 2 == 0:
        points += 1
    return points


def _z_rrq(params: np.ndarray, freq: np.ndarray) -> np.ndarray:
    rs, r, q, alpha = np.asarray(params, dtype=float)
    w = 2.0 * np.pi * np.asarray(freq, dtype=float)
    r = max(float(r), 1e-18)
    q = max(float(q), 1e-18)
    alpha = float(np.clip(alpha, 0.0, 1.0))
    return max(float(rs), 0.0) + 1.0 / (1.0 / r + q * (1j * w) ** alpha)


def _score_model(freq: np.ndarray, z_data: np.ndarray, z_model: np.ndarray, cost: float, n_params: int) -> dict:
    scale = np.maximum(np.abs(z_data), 1e-8)
    rel_rmse = float(np.sqrt(np.mean(np.square(np.abs(z_data - z_model) / scale))))
    re_scale = max(float(np.ptp(z_data.real)), 1.0)
    im_scale = max(float(np.ptp(-z_data.imag)), 1.0)
    nyquist_rmse = float(
        np.sqrt(
            np.mean(
                np.square((z_data.real - z_model.real) / re_scale)
                + np.square((z_data.imag - z_model.imag) / im_scale)
            )
        )
    )
    n_obs = max(int(freq.size * 2), 1)
    rss = float(np.sum(np.square((z_data.real - z_model.real) / scale)) + np.sum(np.square((z_data.imag - z_model.imag) / scale)))
    bic = float(n_obs * np.log(max(rss / n_obs, 1e-300)) + int(n_params) * np.log(n_obs))
    return {
        "Fit cost": float(cost),
        "Fit quality score": rel_rmse,
        "Common rel RMSE": rel_rmse,
        "Common Nyquist RMSE": nyquist_rmse,
        "Common BIC": bic,
    }


def _fit_rrq(freq: np.ndarray, re_z: np.ndarray, im_z: np.ndarray) -> dict:
    freq = np.asarray(freq, dtype=float)
    z_data = np.asarray(re_z, dtype=float) + 1j * np.asarray(im_z, dtype=float)
    order = np.argsort(freq)[::-1]
    freq = freq[order]
    z_data = z_data[order]
    finite = np.isfinite(freq) & np.isfinite(z_data.real) & np.isfinite(z_data.imag) & (freq > 0)
    freq = freq[finite]
    z_data = z_data[finite]
    if freq.size < 4:
        return {"Fit model": "RRQ", "Equivalent circuit": "RRQ", "Fit success": False}

    high_count = max(3, min(freq.size, freq.size // 8))
    rs_guess = max(float(np.nanmedian(z_data.real[:high_count])), 0.0)
    span = max(float(np.nanmax(z_data.real) - rs_guess), 1.0)
    peak_idx = int(np.nanargmax(-z_data.imag)) if np.isfinite(-z_data.imag).any() else freq.size // 2
    f_peak = max(float(freq[peak_idx]), float(np.nanmedian(freq)))

    seeds = [
        np.array([rs_guess, span, 1.0 / max(span * 2.0 * np.pi * f_peak, 1e-18), 0.85], dtype=float),
        np.array([0.0, max(float(np.ptp(z_data.real)), 1.0), 1.0 / max(span * 2.0 * np.pi * f_peak, 1e-18), 0.95], dtype=float),
        np.array([rs_guess * 0.5, span * 1.5, 1.0 / max(span * 2.0 * np.pi * max(f_peak * 0.2, 1e-9), 1e-18), 0.75], dtype=float),
    ]
    lower = np.array([0.0, 1e-6, 1e-14, 0.0], dtype=float)
    upper = np.array([np.inf, np.inf, np.inf, 1.0], dtype=float)

    best = None
    for seed in seeds:
        seed = np.clip(seed, lower, upper)

        def residuals(params):
            z_model = _z_rrq(params, freq)
            scale = np.maximum(np.abs(z_data), 1e-8)
            return np.concatenate([(z_data.real - z_model.real) / scale, (z_data.imag - z_model.imag) / scale])

        try:
            result = least_squares(residuals, seed, bounds=(lower, upper), max_nfev=20000)
        except Exception:
            continue
        if not result.success or not np.isfinite(result.cost):
            continue
        params = result.x
        z_model = _z_rrq(params, freq)
        metrics = _score_model(freq, z_data, z_model, result.cost, 4)
        candidate = {
            "Equivalent circuit": "RRQ",
            "Fit model": "RRQ",
            "Rs (Ohm)": float(params[0]),
            "R0 (Ohm)": float(params[1]),
            "Q0 (F·s^(a-1))": float(params[2]),
            "a0": float(params[3]),
            "Fit success": True,
            "Fit seed source": "rrq_local_multiseed",
            "Fit pass used": "pass1",
            "Model parameter count": 4,
            "RQ0 fchar (Hz)": float((params[1] * params[2]) ** (-1.0 / max(params[3], 1e-9)) / (2.0 * np.pi)),
            **metrics,
        }
        if best is None or candidate["Common BIC"] < best["Common BIC"]:
            best = candidate
    return best or {"Fit model": "RRQ", "Equivalent circuit": "RRQ", "Fit success": False}


def _fit_model(freq: np.ndarray, re_z: np.ndarray, im_z: np.ndarray, model: str = "RRQ") -> tuple[dict, np.ndarray, np.ndarray]:
    model = str(model or "RRQ").upper()
    if model == "RRQ":
        fit = _fit_rrq(freq, re_z, im_z)
        params = np.array([fit.get("Rs (Ohm)"), fit.get("R0 (Ohm)"), fit.get("Q0 (F·s^(a-1))"), fit.get("a0")], dtype=float)
        f_fit = np.logspace(np.log10(np.nanmin(freq)), np.log10(np.nanmax(freq)), 600)
        z_fit = _z_rrq(params, f_fit) if bool(fit.get("Fit success", False)) and np.all(np.isfinite(params)) else np.full_like(f_fit, np.nan, dtype=complex)
        return fit, f_fit, z_fit
    fit = EF.fit_circuit_model_stable(freq, re_z, im_z, model=model)
    params = EF.fit_result_to_model_params(fit)
    f_fit = np.logspace(np.log10(np.nanmin(freq)), np.log10(np.nanmax(freq)), 600)
    z_fit = EF.Z_model(model, params, f_fit) if params is not None else np.full_like(f_fit, np.nan, dtype=complex)
    return fit, f_fit, z_fit


def _rrqrq_component_summary(fit: dict):
    if str(fit.get("Fit model") or fit.get("Equivalent circuit") or "").upper() != "RRQRQ":
        return None
    try:
        rs = float(fit.get("RRQRQ Rs (Ohm)", fit.get("Rs (Ohm)")))
        rq1_r = float(fit.get("RRQRQ RQ1 R (Ohm)", fit.get("R0 (Ohm)")))
        rq1_q = float(fit.get("RRQRQ RQ1 Q (F*s^(a-1))", fit.get("Q0 (F·s^(a-1))", fit.get("Q0 (FÂ·s^(a-1))"))))
        rq1_a = float(fit.get("RRQRQ RQ1 alpha", fit.get("a0")))
        rq1_f = float(fit.get("RRQRQ RQ1 fchar (Hz)", fit.get("RQ0 fchar (Hz)")))
        rq2_r = float(fit.get("RRQRQ RQ2 R (Ohm)", fit.get("R1 (Ohm)")))
        rq2_q = float(fit.get("RRQRQ RQ2 Q (F*s^(a-1))", fit.get("Q1 (F·s^(a-1))", fit.get("Q1 (FÂ·s^(a-1))"))))
        rq2_a = float(fit.get("RRQRQ RQ2 alpha", fit.get("a1")))
        rq2_f = float(fit.get("RRQRQ RQ2 fchar (Hz)", fit.get("RQ1 fchar (Hz)")))
    except Exception:
        return None
    rp = max(rq1_r + rq2_r, 1e-18)
    return {
        "interpretation": "R_s + RQ1(faster/higher-f) + RQ2(slower/lower-f)",
        "Rs_ohm": rs,
        "RQ1_R_ohm": rq1_r,
        "RQ1_Q_F_s_alpha_minus_1": rq1_q,
        "RQ1_alpha": rq1_a,
        "RQ1_fchar_Hz": rq1_f,
        "RQ1_resistance_fraction": float(rq1_r / rp),
        "RQ2_R_ohm": rq2_r,
        "RQ2_Q_F_s_alpha_minus_1": rq2_q,
        "RQ2_alpha": rq2_a,
        "RQ2_fchar_Hz": rq2_f,
        "RQ2_resistance_fraction": float(rq2_r / rp),
        "polarization_R_total_ohm": float(rp),
    }


def _set_equal_nyquist_limits(ax, x_values: np.ndarray, y_values: np.ndarray) -> None:
    x_values = np.asarray(x_values, dtype=float)
    y_values = np.asarray(y_values, dtype=float)
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    if not np.any(finite):
        ax.set_aspect("equal", adjustable="box")
        return
    x_min = float(np.nanmin(x_values[finite]))
    x_max = float(np.nanmax(x_values[finite]))
    y_min = float(np.nanmin(y_values[finite]))
    y_max = float(np.nanmax(y_values[finite]))
    x_span = max(x_max - x_min, 1.0)
    y_span = max(y_max - y_min, 1.0)
    pad = 0.05 * max(x_span, y_span)
    ax.set_xlim(x_min - pad, x_max + pad)
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_aspect("equal", adjustable="box")


def _nyquist_peak_completion_metrics(
    freq: np.ndarray,
    neg_im: np.ndarray,
    *,
    required_drop_fraction: float = 0.10,
    drop_tolerance_fraction: float = 0.02,
) -> dict:
    """Estimate whether the LF FFT arc has passed the Nyquist -Zim peak.

    Sorting by decreasing frequency follows the physical sweep direction from
    the PEIS boundary toward lower-frequency FFT points. If the lowest-frequency
    end has dropped by >= required_drop_fraction from the observed -Zim peak,
    the unpadded/duration-guarded arc is visually complete enough to use as the
    representative plot.
    """
    freq = np.asarray(freq, dtype=float)
    neg_im = np.asarray(neg_im, dtype=float)
    finite = np.isfinite(freq) & np.isfinite(neg_im) & (freq > 0)
    freq = freq[finite]
    neg_im = neg_im[finite]
    effective_drop_fraction = max(0.0, float(required_drop_fraction) - float(drop_tolerance_fraction))
    if len(freq) < 4:
        return {
            "nyquist_peak_check_points": int(len(freq)),
            "nyquist_peak_reached": False,
            "nyquist_peak_drop_fraction": float("nan"),
            "nyquist_peak_required_drop_fraction": float(required_drop_fraction),
            "nyquist_peak_effective_drop_fraction": float(effective_drop_fraction),
            "nyquist_peak_reason": "too_few_fft_points",
        }
    order = np.argsort(freq)[::-1]
    freq = freq[order]
    neg_im = neg_im[order]
    peak_idx = int(np.nanargmax(neg_im))
    peak_value = float(neg_im[peak_idx])
    low_value = float(neg_im[-1])
    if not np.isfinite(peak_value) or abs(peak_value) <= 0:
        drop_fraction = float("nan")
    else:
        drop_fraction = float((peak_value - low_value) / abs(peak_value))
    peak_has_lf_side = peak_idx < len(freq) - 1
    reached = bool(peak_has_lf_side and np.isfinite(drop_fraction) and drop_fraction >= effective_drop_fraction)
    if reached:
        reason = "lowest_frequency_side_dropped_past_peak_threshold"
    elif not peak_has_lf_side:
        reason = "peak_at_lowest_frequency_edge"
    else:
        reason = "post_peak_drop_below_threshold"
    return {
        "nyquist_peak_check_points": int(len(freq)),
        "nyquist_peak_reached": reached,
        "nyquist_peak_drop_fraction": drop_fraction,
        "nyquist_peak_required_drop_fraction": float(required_drop_fraction),
        "nyquist_peak_effective_drop_fraction": float(effective_drop_fraction),
        "nyquist_peak_reason": reason,
        "nyquist_peak_frequency_hz": float(freq[peak_idx]),
        "nyquist_peak_neg_im_ohm": peak_value,
        "nyquist_lowest_frequency_hz": float(freq[-1]),
        "nyquist_lowest_frequency_neg_im_ohm": low_value,
        "nyquist_peak_index_from_hf": peak_idx,
    }


def _copy_recommended_artifacts(run_dir: Path, selected: dict, variant: str) -> dict:
    copied: dict[str, str] = {}
    for key, stem in (
        ("full_arc_png", "full_arc_merged_fft_lf_peis_hf_duration_gated_recommended.png"),
        ("full_arc_txt", "full_arc_merged_fft_lf_peis_hf_duration_gated_recommended.txt"),
        ("nyquist_equal_aspect_png", f"nyquist_equal_aspect_{str(selected.get('fit_model') or 'fit').lower()}_recommended.png"),
    ):
        src = selected.get(key)
        if not src:
            continue
        src_path = Path(str(src))
        dst_path = run_dir / stem
        try:
            if src_path.exists():
                shutil.copyfile(src_path, dst_path)
                copied[f"recommended_{key}"] = str(dst_path)
        except Exception as exc:
            copied[f"recommended_{key}_copy_error"] = repr(exc)
            copied[f"recommended_{key}"] = str(src_path)
    copied["recommended_full_arc_variant"] = variant
    return copied


def make_full_arc(
    run_dir: Path,
    *,
    zero_pad_factor: int = 30,
    n_log_points: int = 100,
    duration_guard_periods: float = 0.0,
    peis_anchor_gap_decades: float = 0.0,
    output_suffix: str = "",
    fit_model: str = "RRQRQ",
) -> dict:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    ca_txt = _run_file(run_dir, summary.get("ca_fft_txt"), "ca_for_fft_pre_plus_scout_only.txt")
    peis_txt = _run_file(run_dir, summary.get("peis_txt"), "measured_peis.txt")
    # OLE export file stores the display convention (-ImZ). The legacy backend
    # and fitter expect the physical complex convention ImZ internally.
    peis = _average_duplicate_freq(_load_peis_txt(peis_txt))
    peis_im = -peis[:, 2]
    peis_lf = float(np.nanmin(peis[:, 0]))

    t_raw, v_raw, i_raw = _load_ca_txt(ca_txt)
    raw_dt_values = np.diff(np.asarray(t_raw, dtype=float))
    raw_dt_values = raw_dt_values[np.isfinite(raw_dt_values) & (raw_dt_values > 0)]
    raw_dt = float(np.median(raw_dt_values)) if len(raw_dt_values) else float("nan")
    t, v, i, dt = _uniform_ca_grid(t_raw, v_raw, i_raw)
    duration = float(np.nanmax(t) - np.nanmin(t))
    fft_min_reliable_hz = 1.0 / max(duration, dt)
    fft_window_points = _window_points_for_seconds(0.5, dt)
    eis_ref = np.column_stack([peis[:, 0], peis[:, 1], peis_im])
    (
        dif_t,
        dif_v,
        dif_i,
        ft_f_e,
        ft_v_e,
        ft_i_e,
        filtered_f,
        recovered_z,
        _z_full_ref,
        *_plots,
    ) = DC_EIS.FFT_EIS(
        t,
        v,
        i,
        method="Raw",
        dt=dt,
        window=fft_window_points,
        max_f=peis_lf,
        zero_pad_factor=int(zero_pad_factor),
        n_log_points=int(n_log_points),
        eis_data=eis_ref,
        make_plots=False,
    )
    fft_valid = np.isfinite(filtered_f) & np.isfinite(recovered_z.real) & np.isfinite(recovered_z.imag)
    # Zero padding densifies the frequency grid. A positive duration guard keeps
    # only frequencies supported by at least the requested number of full
    # time-domain periods; set it to 0 for legacy/exploratory zero-padded LF.
    peis_anchor_high_hz = peis_lf / (10.0 ** peis_anchor_gap_decades)
    duration_guard_hz = float(duration_guard_periods) / max(duration, dt) if float(duration_guard_periods) > 0 else 0.0
    fft_low = fft_valid & (filtered_f < peis_anchor_high_hz) & (filtered_f >= duration_guard_hz)
    fft_freq = filtered_f[fft_low]
    fft_re = recovered_z.real[fft_low]
    fft_im = recovered_z.imag[fft_low]
    fft_neg_im = -fft_im
    fft_min_used_hz = float(np.nanmin(fft_freq)) if len(fft_freq) else float("nan")
    peak_completion = _nyquist_peak_completion_metrics(
        fft_freq,
        fft_neg_im,
        required_drop_fraction=0.10,
    )

    full_freq = np.concatenate([fft_freq, peis[:, 0]])
    full_re = np.concatenate([fft_re, peis[:, 1]])
    full_im = np.concatenate([fft_im, peis_im])
    order = np.argsort(full_freq)
    full_freq = full_freq[order]
    full_re = full_re[order]
    full_im = full_im[order]
    full_neg_im = -full_im

    fit_model = str(fit_model or "RRQRQ").upper()
    fit, f_fit, z_fit = _fit_model(full_freq, full_re, full_im, model=fit_model)
    rrqrq_components = _rrqrq_component_summary(fit)

    suffix = str(output_suffix or "")
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    full_txt = run_dir / f"full_arc_merged_fft_lf_peis_hf_duration_gated{suffix}.txt"
    np.savetxt(
        full_txt,
        np.column_stack([full_freq, full_re, full_im, full_neg_im]),
        delimiter="\t",
        header="freq_Hz\tReZ_ohm\tImZ_ohm\tnegImZ_ohm",
        comments="",
    )

    out_png = run_dir / f"full_arc_merged_fft_lf_peis_hf_duration_gated{suffix}.png"
    fig, axs = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    ax = axs[0, 0]
    ax.scatter(peis[:, 1], peis[:, 2], c="#1f77b4", s=28, label=f"PEIS >= {peis_lf:.3g} Hz")
    if len(fft_freq):
        sc = ax.scatter(fft_re, fft_neg_im, c=np.log10(fft_freq), cmap="plasma_r", s=34, label="CA FFT LF")
        fig.colorbar(sc, ax=ax, label="log10(FFT f / Hz)")
    if np.isfinite(z_fit.real).any():
        ax.plot(z_fit.real, -z_fit.imag, color="black", lw=1.6, label=f"{fit.get('Fit model')} fit")
    ax.set_title("Full Arc: FFT LF + Measured PEIS HF")
    ax.set_xlabel("Zre / Ohm")
    ax.set_ylabel("-Zim / Ohm")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    z_full = full_re - 1j * full_neg_im
    ax = axs[0, 1]
    ax.scatter(np.log10(full_freq), np.log10(np.abs(z_full)), c=np.log10(full_freq), cmap="viridis", s=25)
    if np.isfinite(z_fit.real).any():
        ax.plot(np.log10(f_fit), np.log10(np.abs(z_fit)), color="black", lw=1.4)
    ax.axvline(np.log10(peis_lf), color="gray", ls="--", lw=1)
    ax.set_title("|Z|")
    ax.set_xlabel("log10(f / Hz)")
    ax.set_ylabel("log10(|Z| / Ohm)")
    ax.grid(alpha=0.25)

    ax = axs[1, 1]
    ax.scatter(np.log10(full_freq), np.degrees(np.angle(z_full)), c=np.log10(full_freq), cmap="viridis", s=25)
    if np.isfinite(z_fit.real).any():
        ax.plot(np.log10(f_fit), np.degrees(np.angle(z_fit)), color="black", lw=1.4)
    ax.axvline(np.log10(peis_lf), color="gray", ls="--", lw=1)
    ax.set_title("Phase")
    ax.set_xlabel("log10(f / Hz)")
    ax.set_ylabel("Phase / deg")
    ax.grid(alpha=0.25)

    ax = axs[1, 0]
    ax.plot(t, i * 1e9, color="#d62728", lw=1.0)
    smooth_points = summary.get("fft_ready_segmented_smoothing_window_points")
    smooth_s = summary.get("fft_ready_segmented_smoothing_window_s")
    if smooth_points:
        smooth_label = f"FFT-ready, segmented tail smoothing {smooth_s or smooth_points}"
    else:
        smooth_label = "FFT-ready"
    ax.set_title(f"CA FFT Input ({smooth_label})")
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / nA")
    ax.grid(alpha=0.25)

    guard_label = "duration-gated" if duration_guard_hz > 0 else f"ZP{int(zero_pad_factor)} no-duration-guard"
    fig.suptitle(
        f"OLE-COM {guard_label} full arc | raw LF={summary.get('raw_recommended_lf_hz')} Hz, "
        f"PEIS LF={peis_lf:.4g} Hz, FFT used min={fft_min_used_hz:.4g} Hz, "
        f"score={fit.get('Fit quality score'):.4g}"
    )
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close(fig)

    nyquist_png = run_dir / f"nyquist_equal_aspect_{fit_model.lower()}{suffix}.png"
    fig_ny, ax_ny = plt.subplots(figsize=(9, 7), constrained_layout=True)
    ax_ny.scatter(peis[:, 1], peis[:, 2], c="#1f77b4", s=38, label=f"PEIS >= {peis_lf:.3g} Hz")
    if len(fft_freq):
        sc_ny = ax_ny.scatter(fft_re, fft_neg_im, c=np.log10(fft_freq), cmap="plasma_r", s=44, label="CA FFT LF")
        fig_ny.colorbar(sc_ny, ax=ax_ny, label="log10(FFT f / Hz)")
    if np.isfinite(z_fit.real).any():
        ax_ny.plot(z_fit.real, -z_fit.imag, color="black", lw=1.8, label=f"{fit.get('Fit model')} fit")
    x_for_limits = full_re
    y_for_limits = full_neg_im
    if np.isfinite(z_fit.real).any():
        x_for_limits = np.concatenate([x_for_limits, z_fit.real])
        y_for_limits = np.concatenate([y_for_limits, -z_fit.imag])
    _set_equal_nyquist_limits(ax_ny, x_for_limits, y_for_limits)
    ax_ny.set_title(
        f"Nyquist equal aspect | PEIS LF={peis_lf:.4g} Hz | score={fit.get('Fit quality score'):.4g}"
    )
    ax_ny.set_xlabel("Zre / Ohm")
    ax_ny.set_ylabel("-Zim / Ohm")
    ax_ny.grid(alpha=0.25)
    ax_ny.legend(fontsize=9)
    fig_ny.savefig(nyquist_png, dpi=200, bbox_inches="tight")
    plt.close(fig_ny)

    out_summary = {
        "run_dir": str(run_dir),
        "full_arc_png": str(out_png),
        "nyquist_equal_aspect_png": str(nyquist_png),
        "full_arc_txt": str(full_txt),
        "peis_lf_hz": peis_lf,
        "fft_min_reliable_hz": fft_min_reliable_hz,
        "fft_min_used_hz": fft_min_used_hz,
        "fft_duration_guard_hz": duration_guard_hz,
        "fft_duration_guard_periods": float(duration_guard_periods),
        "fft_peis_anchor_high_hz": float(peis_anchor_high_hz),
        "fft_peis_anchor_gap_decades": float(peis_anchor_gap_decades),
        "fft_points_used": int(len(fft_freq)),
        **peak_completion,
        "peis_points_averaged": int(len(peis)),
        "ca_fft_processing": "uniform_grid_from_fft_ready_trace",
        "ca_fft_dt_s": float(dt),
        "ca_fft_window_points": int(fft_window_points),
        "fit_model": fit.get("Fit model"),
        "fit_score": fit.get("Fit quality score"),
        "fit_cost": fit.get("Fit cost"),
        "rrqrq_components": rrqrq_components,
        "fit": {k: (float(v) if isinstance(v, np.generic) else v) for k, v in fit.items() if k != "Auto fit candidate results"},
        "ca_duration_s": duration,
        "ca_dt_s": dt,
        "ca_raw_dt_s": raw_dt,
        "fft_method": "Raw",
        "fft_pre_smooth_interpolate_dt_s": None,
        "fft_pre_smooth_interpolate_window_points": 0,
        "fft_smoothing_window_s": 0.0,
        "fft_window_points": int(fft_window_points),
        "zero_pad_factor": int(zero_pad_factor),
        "n_log_points": int(n_log_points),
    }
    (run_dir / f"full_arc_summary{suffix}.json").write_text(json.dumps(out_summary, indent=2, default=str), encoding="utf-8")
    return out_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build merged full arc from an OLE-COM run folder.")
    parser.add_argument("run_dir")
    parser.add_argument("--zero-pad-factor", type=int, default=30)
    parser.add_argument("--n-log-points", type=int, default=100)
    parser.add_argument(
        "--fit-model",
        choices=["RRQ", "RQRQ", "RRQRQ", "RQRQRQ"],
        default="RRQRQ",
        help="Equivalent-circuit model used for full-arc fitting. Default is RRQRQ.",
    )
    parser.add_argument(
        "--duration-guard-periods",
        type=float,
        default=0.0,
        help="Minimum observed periods required for FFT LF points. Use 0 for legacy zero-padded LF.",
    )
    parser.add_argument(
        "--peis-anchor-gap-decades",
        type=float,
        default=0.0,
        help="Optional gap below PEIS LF before accepting FFT points. Default 0 uses FFT up to PEIS LF.",
    )
    parser.add_argument("--output-suffix", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if str(args.output_suffix or "").strip():
        result = make_full_arc(
            run_dir,
            zero_pad_factor=args.zero_pad_factor,
            n_log_points=args.n_log_points,
            duration_guard_periods=args.duration_guard_periods,
            peis_anchor_gap_decades=args.peis_anchor_gap_decades,
            output_suffix=args.output_suffix,
            fit_model=args.fit_model,
        )
        print(json.dumps(result, indent=2, default=str))
        return

    # Standard automated post-processing:
    # 1. hot: conservative ZP30 grid with a one-observed-period LF guard.
    # 2. relaxed10x30: still ZP30 grid, but allows LF 10x below the guard.
    # 3. zp30: exploratory full ZP30 LF extension with no duration guard.
    # The recommended view walks that order so we only use more aggressive LF
    # recovery when the safer Nyquist arc has not yet passed the peak.
    hot = make_full_arc(
        run_dir,
        zero_pad_factor=30,
        n_log_points=args.n_log_points,
        duration_guard_periods=1.0,
        peis_anchor_gap_decades=args.peis_anchor_gap_decades,
        output_suffix="hot",
        fit_model=args.fit_model,
    )
    relaxed10x30 = make_full_arc(
        run_dir,
        zero_pad_factor=30,
        n_log_points=args.n_log_points,
        duration_guard_periods=0.1,
        peis_anchor_gap_decades=args.peis_anchor_gap_decades,
        output_suffix="relaxed10x30",
        fit_model=args.fit_model,
    )
    zp30 = make_full_arc(
        run_dir,
        zero_pad_factor=30,
        n_log_points=args.n_log_points,
        duration_guard_periods=0.0,
        peis_anchor_gap_decades=args.peis_anchor_gap_decades,
        output_suffix="zp30",
        fit_model=args.fit_model,
    )
    hot_peak_complete = bool(hot.get("nyquist_peak_reached", False))
    if hot_peak_complete:
        recommended = hot
        recommended_variant = "hot_one_period_guard"
        recommended_reason = (
            "hot/ZP30 one-period-guard LF arc passed the Nyquist peak and the lowest-frequency side "
            f"dropped by about {float(hot.get('nyquist_peak_required_drop_fraction', 0.10)):.0%} "
            f"(effective cutoff {float(hot.get('nyquist_peak_effective_drop_fraction', 0.08)):.0%})"
        )
    elif bool(relaxed10x30.get("nyquist_peak_reached", False)):
        recommended = relaxed10x30
        recommended_variant = "relaxed10x30_guard"
        recommended_reason = (
            "hot/ZP30 one-period-guard LF arc did not drop >=10% after the Nyquist peak; "
            "using the 10x-relaxed duration guard on the same ZP30 FFT grid"
        )
    else:
        recommended = zp30
        recommended_variant = "zp30_no_duration_guard"
        recommended_reason = (
            "neither hot nor 10x-relaxed ZP30 LF arc dropped >=10% after the Nyquist peak; "
            "using full ZP30 exploratory LF extension as representative"
        )
    recommended_copies = _copy_recommended_artifacts(run_dir, recommended, recommended_variant)

    combined = dict(recommended)
    combined.update(
        {
            "full_arc_png": recommended_copies.get("recommended_full_arc_png", recommended.get("full_arc_png")),
            "full_arc_txt": recommended_copies.get("recommended_full_arc_txt", recommended.get("full_arc_txt")),
            "nyquist_equal_aspect_png": recommended_copies.get(
                "recommended_nyquist_equal_aspect_png",
                recommended.get("nyquist_equal_aspect_png"),
            ),
            "fit_score": recommended.get("fit_score"),
            "fit_cost": recommended.get("fit_cost"),
            "fit_model": recommended.get("fit_model"),
            "standard_full_arc_variant": "recommended_auto",
            "recommended_full_arc_variant": recommended_variant,
            "recommended_full_arc_reason": recommended_reason,
            "recommended_full_arc_png": recommended_copies.get("recommended_full_arc_png", recommended.get("full_arc_png")),
            "recommended_full_arc_txt": recommended_copies.get("recommended_full_arc_txt", recommended.get("full_arc_txt")),
            "recommended_nyquist_equal_aspect_png": recommended_copies.get(
                "recommended_nyquist_equal_aspect_png",
                recommended.get("nyquist_equal_aspect_png"),
            ),
            "recommended_fit_score": recommended.get("fit_score"),
            "recommended_fit_cost": recommended.get("fit_cost"),
            "recommended_nyquist_peak_reached": recommended.get("nyquist_peak_reached"),
            "recommended_nyquist_peak_drop_fraction": recommended.get("nyquist_peak_drop_fraction"),
            "hot_full_arc_variant": "hot_one_period_guard",
            "hot_full_arc_png": hot.get("full_arc_png"),
            "hot_full_arc_txt": hot.get("full_arc_txt"),
            "hot_nyquist_equal_aspect_png": hot.get("nyquist_equal_aspect_png"),
            "hot_fit_score": hot.get("fit_score"),
            "hot_fit_cost": hot.get("fit_cost"),
            "hot_nyquist_peak_reached": hot.get("nyquist_peak_reached"),
            "hot_nyquist_peak_drop_fraction": hot.get("nyquist_peak_drop_fraction"),
            "safe_full_arc_variant": "hot_one_period_guard",
            "safe_full_arc_png": hot.get("full_arc_png"),
            "safe_full_arc_txt": hot.get("full_arc_txt"),
            "safe_nyquist_equal_aspect_png": hot.get("nyquist_equal_aspect_png"),
            "safe_fit_score": hot.get("fit_score"),
            "safe_fit_cost": hot.get("fit_cost"),
            "relaxed10x30_full_arc_variant": "relaxed10x30_guard",
            "relaxed10x30_full_arc_png": relaxed10x30.get("full_arc_png"),
            "relaxed10x30_full_arc_txt": relaxed10x30.get("full_arc_txt"),
            "relaxed10x30_nyquist_equal_aspect_png": relaxed10x30.get("nyquist_equal_aspect_png"),
            "relaxed10x30_fit_score": relaxed10x30.get("fit_score"),
            "relaxed10x30_fit_cost": relaxed10x30.get("fit_cost"),
            "relaxed10x30_nyquist_peak_reached": relaxed10x30.get("nyquist_peak_reached"),
            "relaxed10x30_nyquist_peak_drop_fraction": relaxed10x30.get("nyquist_peak_drop_fraction"),
            # Backward-friendly alias for the "ZP10" discussion: this is not
            # 10x zero padding; it is a 10x lower duration guard on a ZP30 grid.
            "zp10_relaxed_full_arc_variant": "relaxed10x30_guard",
            "zp10_relaxed_full_arc_png": relaxed10x30.get("full_arc_png"),
            "zp10_relaxed_full_arc_txt": relaxed10x30.get("full_arc_txt"),
            "zp10_relaxed_nyquist_equal_aspect_png": relaxed10x30.get("nyquist_equal_aspect_png"),
            "zp10_relaxed_fit_score": relaxed10x30.get("fit_score"),
            "zp10_relaxed_fit_cost": relaxed10x30.get("fit_cost"),
            "zp30_full_arc_png": zp30.get("full_arc_png"),
            "zp30_full_arc_txt": zp30.get("full_arc_txt"),
            "zp30_nyquist_equal_aspect_png": zp30.get("nyquist_equal_aspect_png"),
            "zp30_fit_score": zp30.get("fit_score"),
            "zp30_fit_cost": zp30.get("fit_cost"),
            "zp30_nyquist_peak_reached": zp30.get("nyquist_peak_reached"),
            "zp30_nyquist_peak_drop_fraction": zp30.get("nyquist_peak_drop_fraction"),
        }
    )
    (run_dir / "full_arc_summary.json").write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")
    print(json.dumps(combined, indent=2, default=str))


if __name__ == "__main__":
    main()
