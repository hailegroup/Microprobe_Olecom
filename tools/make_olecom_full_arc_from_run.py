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
from mpl_toolkits.axes_grid1 import make_axes_locatable

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS") not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR / "Analysis_Convert_CP_to_EIS"))

from Analysis_Convert_CP_to_EIS import Convert_CP_to_EIS as DC_EIS  # noqa: E402
from Analysis_Convert_CP_to_EIS import Load_CP_Data as LD  # noqa: E402
from scipy.optimize import least_squares  # noqa: E402

from Analysis_Convert_CP_to_EIS import EIS_Fitting as EF  # noqa: E402
from cp_first_policy import build_fft_ready_ca_trace  # noqa: E402
from extract_full_arc_results_to_csv import _cpe_capacitance  # noqa: E402


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


def _uniform_ca_grid(
    t_raw: np.ndarray,
    v_raw: np.ndarray,
    i_raw: np.ndarray,
    *,
    max_gap_multiple: float = 5.0,
    max_gap_floor_s: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Resample onto a uniform time grid at the data's own median spacing.

    A CA recording with a long, nearly-flat pre-hold next to a fast-changing
    scout can be wildly non-uniform in density -- e.g. a handful of real
    samples spread across 120s of pre-hold, then thousands of samples
    across the following 100s of scout. The median dt is set by whichever
    region has more points (scout), so naively `np.interp`-ing the WHOLE
    span onto that fine grid fabricates a smooth, densely-sampled ramp
    across the sparse region that the recording never actually supported --
    confirmed on a real run where this produced ~2.2x as many "processed"
    points as real raw ones, and blurred the true voltage step badly
    enough that step-detection locked onto an unrelated noise blip 70s
    away instead. Grid points whose nearest real-sample gap is much wider
    than the typical spacing are dropped instead of interpolated, so a
    sparse region shows up as sparse (real gap in the output) rather than
    a fabricated dense ramp.
    """
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

    gaps = np.diff(t_raw)
    max_gap = max(float(max_gap_multiple) * dt, float(max_gap_floor_s))
    if len(gaps) and np.any(gaps > max_gap):
        # Which real-sample interval does each candidate grid point fall
        # into? searchsorted gives the insertion index into t_raw; -1
        # converts that to "index of the real sample just at/before t".
        interval_idx = np.clip(np.searchsorted(t_raw, t, side="right") - 1, 0, len(gaps) - 1)
        local_gap = gaps[interval_idx]
        keep = local_gap <= max_gap
        t = t[keep]

    v = np.interp(t, t_raw, v_raw)
    i = np.interp(t, t_raw, i_raw)
    return t, v, i, dt


def _load_and_smooth_raw_ca(
    raw_ca_txt: Path,
    *,
    average_bin_s: float | None,
    despike_sigma: float,
    despike_window_s: float,
    despike_step_guard_s: float,
    segmented_smoothing_window_s: float | None,
    auto_trim_kwargs: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Raw saved CA trace -> smoothed, uniform-gridded (t, v, i).

    Mirrors the same auto-trim + despike/median-bin steps
    `cp_first_policy.recommend_peis_lf_from_cp_txt` applies during a live
    run, but with every knob exposed to the caller instead of hardcoded --
    lets `tools/ca_fft_tuning_gui.py` re-derive a differently-smoothed
    trace from the raw data any saved run already has on disk
    (`ca_for_fft_pre_plus_scout_only.txt` / `summary["ca_fft_raw_txt"]`),
    without needing a real CA/PEIS re-measurement to try new values.
    `average_bin_s=None` auto-detects bin width from the raw trace's own
    median sample spacing, same as the live pipeline.
    """
    dc_data = LD.load_dc_from_path(
        str(raw_ca_txt),
        current_in_mA=False,
        CA_step_only=False,
        trim_start_time=None,
        current_scale_factor=1.0,
        auto_trim=True,
        auto_trim_kwargs=auto_trim_kwargs,
    )
    raw_time = np.asarray(dc_data, dtype=float)[:, 0] if np.asarray(dc_data).ndim == 2 else np.asarray([])
    bin_s = average_bin_s
    if bin_s is None:
        diffs = np.diff(raw_time)
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        bin_s = float(np.median(diffs)) if len(diffs) else 0.1
    # build_fft_ready_ca_trace rebases its output to start at t=0 relative
    # to whatever sample survived auto-trim first -- restore the original
    # absolute time reference afterward so this trace stays directly
    # comparable (same t axis) to the untouched raw file it came from,
    # instead of silently landing on a different, auto-trim-dependent
    # zero point.
    time_offset = float(raw_time[0]) if len(raw_time) else 0.0
    smoothed = build_fft_ready_ca_trace(
        dc_data,
        average_bin_s=bin_s,
        segmented_smoothing_window_s=segmented_smoothing_window_s,
        current_despike=True,
        despike_window_s=despike_window_s,
        despike_sigma=despike_sigma,
        despike_step_guard_s=despike_step_guard_s,
    )
    smoothed = np.asarray(smoothed, dtype=float).copy()
    if len(smoothed):
        smoothed[:, 0] += time_offset
    t, v, i, _dt = _uniform_ca_grid(smoothed[:, 0], smoothed[:, 1], smoothed[:, 2])
    return t, v, i


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


def _score_model(
    freq: np.ndarray, z_data: np.ndarray, z_model: np.ndarray, cost: float, n_params: int,
    relative_error: bool = True,
) -> dict:
    scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
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


def _fit_rrq(freq: np.ndarray, re_z: np.ndarray, im_z: np.ndarray, relative_error: bool = True) -> dict:
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
            scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
            return np.concatenate([(z_data.real - z_model.real) / scale, (z_data.imag - z_model.imag) / scale])

        try:
            result = least_squares(residuals, seed, bounds=(lower, upper), max_nfev=20000)
        except Exception:
            continue
        if not result.success or not np.isfinite(result.cost):
            continue
        params = result.x
        perr, condition_number, ill_conditioned = EF.param_std_errors(result, len(seed))
        z_model = _z_rrq(params, freq)
        metrics = _score_model(freq, z_data, z_model, result.cost, 4, relative_error=relative_error)
        candidate = {
            "Equivalent circuit": "RRQ",
            "Fit model": "RRQ",
            "Rs (Ohm)": float(params[0]),
            "Rs (Ohm) error": float(perr[0]),
            "R0 (Ohm)": float(params[1]),
            "R0 (Ohm) error": float(perr[1]),
            "Q0 (F·s^(a-1))": float(params[2]),
            "Q0 (F·s^(a-1)) error": float(perr[2]),
            "a0": float(params[3]),
            "a0 error": float(perr[3]),
            "Fit condition number": float(condition_number),
            "Fit errors ill-conditioned": bool(ill_conditioned),
            "Fit relative error": bool(relative_error),
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


def _fit_model(
    freq: np.ndarray, re_z: np.ndarray, im_z: np.ndarray, model: str = "RRQ", relative_error: bool = True,
) -> tuple[dict, np.ndarray, np.ndarray]:
    model = str(model or "RRQ").upper()
    if model == "RRQ":
        fit = _fit_rrq(freq, re_z, im_z, relative_error=relative_error)
        params = np.array([fit.get("Rs (Ohm)"), fit.get("R0 (Ohm)"), fit.get("Q0 (F·s^(a-1))"), fit.get("a0")], dtype=float)
        f_fit = np.logspace(np.log10(np.nanmin(freq)), np.log10(np.nanmax(freq)), 600)
        z_fit = _z_rrq(params, f_fit) if bool(fit.get("Fit success", False)) and np.all(np.isfinite(params)) else np.full_like(f_fit, np.nan, dtype=complex)
        return fit, f_fit, z_fit
    fit = EF.fit_circuit_model_stable(freq, re_z, im_z, model=model, relative_error=relative_error)
    params = EF.fit_result_to_model_params(fit)
    f_fit = np.logspace(np.log10(np.nanmin(freq)), np.log10(np.nanmax(freq)), 600)
    z_fit = EF.Z_model(model, params, f_fit) if params is not None else np.full_like(f_fit, np.nan, dtype=complex)
    return fit, f_fit, z_fit


def _rrqrq_component_summary(fit: dict):
    if str(fit.get("Fit model") or fit.get("Equivalent circuit") or "").upper() != "RRQRQ":
        return None
    # Error keys are never duplicated under the "RRQRQ ..." interpretation
    # names the way values are above -- they only ever exist under the base
    # fit keys (e.g. "R0 (Ohm) error"), so read those directly. Missing (older
    # JSON, pre-dates this feature) or non-finite values fall back to NaN
    # rather than raising, since a fit can succeed without a usable Jacobian.
    def _err(key):
        try:
            return float(fit.get(key, float("nan")))
        except (TypeError, ValueError):
            return float("nan")

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
    rs_err = _err("Rs (Ohm) error")
    rq1_r_err = _err("R0 (Ohm) error")
    rq1_q_err = _err("Q0 (F·s^(a-1)) error")
    rq1_a_err = _err("a0 error")
    rq2_r_err = _err("R1 (Ohm) error")
    rq2_q_err = _err("Q1 (F·s^(a-1)) error")
    rq2_a_err = _err("a1 error")
    rp = max(rq1_r + rq2_r, 1e-18)
    # Independent-sum propagation (sqrt(err1^2 + err2^2)) -- the covariance
    # between RQ1_R and RQ2_R is not tracked past the diagonal, so this
    # ignores any cross-correlation between the two branches' resistances.
    rp_err = (
        float(np.sqrt(rq1_r_err**2 + rq2_r_err**2))
        if np.isfinite(rq1_r_err) and np.isfinite(rq2_r_err)
        else float("nan")
    )
    return {
        "interpretation": "R_s + RQ1(faster/higher-f) + RQ2(slower/lower-f)",
        "Rs_ohm": rs,
        "Rs_err_ohm": rs_err,
        "RQ1_R_ohm": rq1_r,
        "RQ1_R_err_ohm": rq1_r_err,
        "RQ1_Q_F_s_alpha_minus_1": rq1_q,
        "RQ1_Q_err_F_s_alpha_minus_1": rq1_q_err,
        "RQ1_alpha": rq1_a,
        "RQ1_alpha_err": rq1_a_err,
        "RQ1_fchar_Hz": rq1_f,
        "RQ1_resistance_fraction": float(rq1_r / rp),
        "RQ2_R_ohm": rq2_r,
        "RQ2_R_err_ohm": rq2_r_err,
        "RQ2_Q_F_s_alpha_minus_1": rq2_q,
        "RQ2_Q_err_F_s_alpha_minus_1": rq2_q_err,
        "RQ2_alpha": rq2_a,
        "RQ2_alpha_err": rq2_a_err,
        "RQ2_fchar_Hz": rq2_f,
        "RQ2_resistance_fraction": float(rq2_r / rp),
        # Named after what it literally is (RQ1_R_ohm + RQ2_R_ohm) --
        # previously also duplicated as a separately-computed "R_reaction_ohm"
        # downstream in extract_full_arc_results_to_csv.py; the two were
        # always identical for this 2-element model, so that duplicate
        # column was dropped rather than kept in sync with two names.
        "R1+R2_ohm": float(rp),
        "R1+R2_err_ohm": rp_err,
    }


# Default iteration count for monte_carlo_param_errors -- a pragmatic middle
# ground for an interactive, explicitly-triggered GUI action (each iteration
# is a full multi-seed refit); literature bootstrap practice ranges from
# ~20-50 for a quick estimate up to hundreds for a rigorous one.
DEFAULT_MC_ITERATIONS = 30
# Fewer successful refits than this and the resulting std is too noisy to
# report as a meaningful estimate -- monte_carlo_param_errors returns None
# instead of a misleadingly-precise number from too few samples.
MC_MIN_SUCCESSFUL_REFITS = 5

# Explicit mapping from _rrqrq_component_summary's VALUE keys to the
# corresponding Monte Carlo std key monte_carlo_param_errors reports --
# deliberately excludes that same dict's own "*_err_ohm"/"*_alpha_err" keys
# (those are already Jacobian-based uncertainty estimates, not values to take
# a std across bootstrap refits of). Kept as an explicit table rather than a
# string-suffix transform so a future new key in _rrqrq_component_summary
# fails safe (silently not MC-tracked) instead of getting a mangled name.
_RRQRQ_COMPONENT_VALUE_KEY_TO_MC_KEY = {
    "Rs_ohm": "Rs_mc_err_ohm",
    "RQ1_R_ohm": "RQ1_R_mc_err_ohm",
    "RQ1_Q_F_s_alpha_minus_1": "RQ1_Q_mc_err_F_s_alpha_minus_1",
    "RQ1_alpha": "RQ1_alpha_mc_err",
    "RQ1_fchar_Hz": "RQ1_fchar_mc_err_Hz",
    "RQ1_resistance_fraction": "RQ1_resistance_fraction_mc_err",
    "RQ2_R_ohm": "RQ2_R_mc_err_ohm",
    "RQ2_Q_F_s_alpha_minus_1": "RQ2_Q_mc_err_F_s_alpha_minus_1",
    "RQ2_alpha": "RQ2_alpha_mc_err",
    "RQ2_fchar_Hz": "RQ2_fchar_mc_err_Hz",
    "RQ2_resistance_fraction": "RQ2_resistance_fraction_mc_err",
    "R1+R2_ohm": "R1+R2_mc_err_ohm",
    "RQ1_C_F": "RQ1_C_mc_err_F",
    "RQ2_C_F": "RQ2_C_mc_err_F",
}


def _param_vector_for_model(fit: dict, model: str) -> np.ndarray | None:
    """A fit-result dict's parameter vector, in the same key order _z_rrq
    (RRQ) or EF.Z_model (RQRQ/RRQRQ/RQRQRQ) expect."""
    model = str(model or "RRQ").upper()
    if model == "RRQ":
        try:
            params = np.array(
                [fit["Rs (Ohm)"], fit["R0 (Ohm)"], fit["Q0 (F·s^(a-1))"], fit["a0"]],
                dtype=float,
            )
        except Exception:
            return None
        return params if np.all(np.isfinite(params)) else None
    return EF.fit_result_to_model_params(fit)


def _eval_model_for_mc(model: str, params: np.ndarray, freq: np.ndarray) -> np.ndarray:
    model = str(model or "RRQ").upper()
    if model == "RRQ":
        return _z_rrq(params, freq)
    return EF.Z_model(model, params, freq)


def _freq_bin_membership(freq: np.ndarray, n_freq_bins: int) -> list[np.ndarray]:
    """Split freq's indices into n_freq_bins equal-*count* bins by sorted
    frequency (not equal log-width, so every bin has enough points to
    resample from even though FFT-recovered low-frequency points are much
    sparser than the densely log-spaced PEIS points). n_freq_bins<=1 ->
    one bin containing every index, i.e. global pooling. Every original
    index appears in exactly one returned bin."""
    n = int(np.asarray(freq).size)
    n_freq_bins = max(1, int(n_freq_bins))
    if n_freq_bins <= 1:
        return [np.arange(n)]
    order = np.argsort(freq)
    return [order[idxs] for idxs in np.array_split(np.arange(n), n_freq_bins) if len(idxs)]


def monte_carlo_param_errors(
    freq: np.ndarray,
    re_z: np.ndarray,
    im_z: np.ndarray,
    fit: dict,
    model: str,
    *,
    n_iterations: int = DEFAULT_MC_ITERATIONS,
    seed: int = 0,
    relative_error: bool = True,
    n_freq_bins: int = 1,
) -> dict | None:
    """Residual-resampling ("adapted Monte-Carlo", inspired by Alper & Gelb
    1990 -- see https://www.biologic.net/topics/zfit-confidence-with-std-err/
    for BioLogic's description of ZFit's version of this, which this follows
    more closely than the 1990 paper's own literal algorithm: that paper
    injects fresh Gaussian noise of an assumed/known magnitude onto the
    original data, whereas this -- like BioLogic's description -- resamples
    the fit's own empirical residuals, needing no assumed noise model)
    parameter uncertainty, as an alternative to param_std_errors' linearized
    Jacobian/covariance estimate (EIS_Fitting.py).

    Computes the best-fit model curve at `fit`'s own parameters, then for
    each of n_iterations: resamples the observed (data - model) residuals
    with replacement, refits the resulting synthetic dataset with the SAME
    _fit_model(...) dispatcher used for the real data (full multi-seed/
    2-pass machinery, for correctness -- this is why this function is meant
    to be triggered explicitly on one row at a time, never automatically
    across a batch), and collects each successful refit's raw parameter
    vector. Unlike the Jacobian estimate, this makes no local-linearization
    assumption, so it naturally reflects genuine model misfit in the
    residual structure, not just measurement noise around a locally-linear
    model.

    relative_error controls BOTH how residuals are normalized before
    resampling AND how each bootstrap refit weights its own objective (via
    _fit_model) -- the same single setting throughout, for internal
    consistency, matching whatever weighting was actually used to produce
    `fit` in the first place (the caller is responsible for passing the same
    value that produced `fit`, e.g. reading it back from that fit's own
    summary rather than a possibly-since-changed UI control). True (default):
    residuals are resampled in the same |Z_data|-normalized (relative) units
    the fit minimizes, not raw ohms, re-scaled by each synthetic point's OWN
    magnitude before being added back onto the model curve -- this matters
    because a fit's residual magnitude naturally scales with |Z| under
    relative weighting, so resampling *raw* ohms would let a residual
    "sized for" the large-|Z| end of the sweep land on a small-|Z| point (or
    vice versa), over- or under-perturbing it relative to what's actually
    representative there. False: residuals are resampled and refit as raw,
    unweighted ohms throughout, matching a relative_error=False fit.

    n_freq_bins (default 1, i.e. off) splits the n frequency points into
    that many equal-*count* bins by sorted frequency, and restricts each
    destination point's resampled residual to come only from other points
    in its own bin -- addressing the fact that this pipeline mixes directly
    -measured PEIS points (high frequency) with FFT-recovered points from a
    CA transient (low frequency), which plausibly have different noise
    character; pooling every point's residual globally (n_freq_bins=1)
    blends that away. n_freq_bins=2 approximates that natural split.

    For RRQRQ specifically, also re-derives _rrqrq_component_summary(fit_i)
    for every successful bootstrap refit and takes the std of each of ITS
    keys across refits -- the statistically correct way to Monte-Carlo a
    nonlinear function (fchar, resistance_fraction, R1+R2_ohm) of the fitted
    parameters, rather than propagating an error formula through the raw
    parameters' own std. Other models only get the raw per-parameter std
    (see module-level scope note in the project's implementation notes for
    why the RRQRQ-shaped rollup isn't generalized yet).

    Returns None if fewer than MC_MIN_SUCCESSFUL_REFITS bootstrap refits
    converge. Otherwise a dict with "<key> Monte Carlo error" for every one
    of `fit`'s own model parameter keys, plus (RRQRQ only) "RQ1_R_mc_err_ohm"
    etc. for every rrqrq_components key, plus "mc_n_iterations" and
    "mc_n_success".
    """
    freq = np.asarray(freq, dtype=float)
    z_data = np.asarray(re_z, dtype=float) + 1j * np.asarray(im_z, dtype=float)
    model = str(model or "RRQ").upper()

    params0 = _param_vector_for_model(fit, model)
    if params0 is None:
        return None
    z_model = _eval_model_for_mc(model, params0, freq)
    if not np.all(np.isfinite(z_model)):
        return None
    # Same normalization convention as the fit's own residual
    # (_fit_rrq/_fit_model_once/EF._fit_model_once all use max(|z_data|, 1e-8)
    # when relative_error, else unweighted) -- must match whatever weighting
    # produced `fit`, or the resampled perturbation sizes and each bootstrap
    # refit's own objective would be internally inconsistent with it.
    scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
    norm_residuals = (z_data.real - z_model.real) / scale + 1j * (z_data.imag - z_model.imag) / scale
    n = int(freq.size)
    if n < 4:
        return None

    bin_members = _freq_bin_membership(freq, n_freq_bins)

    param_keys = ["Rs (Ohm)", "R0 (Ohm)", "Q0 (F·s^(a-1))", "a0"] if model == "RRQ" else list(EF.MODEL_PARAM_KEYS[model])
    rng = np.random.default_rng(seed)
    param_samples: list[np.ndarray] = []
    component_samples: list[dict] = []
    for _ in range(max(1, int(n_iterations))):
        # Resample the (optionally normalized) residual, then re-scale by
        # the DESTINATION point's own magnitude (`scale`, indexed by
        # position, not by the resampled source index) -- so a residual
        # that was "2% of |Z|" wherever it came from gets applied as 2% of
        # |Z| wherever it lands, not as a fixed number of ohms carried over
        # unchanged. Restricted to same-bin members when n_freq_bins > 1 --
        # each bin resamples only from itself, never across bins.
        resample_idx = np.empty(n, dtype=int)
        for members in bin_members:
            resample_idx[members] = members[rng.integers(0, len(members), size=len(members))]
        z_synth = z_model + norm_residuals[resample_idx] * scale
        fit_i, _f_fit, _z_fit = _fit_model(freq, z_synth.real, z_synth.imag, model=model, relative_error=relative_error)
        if not bool(fit_i.get("Fit success", False)):
            continue
        p_i = _param_vector_for_model(fit_i, model)
        if p_i is None:
            continue
        param_samples.append(p_i)
        if model == "RRQRQ":
            comp_i = _rrqrq_component_summary(fit_i)
            if comp_i:
                # Capacitance isn't one of _rrqrq_component_summary's own
                # keys (it's normally derived downstream, in
                # extract_full_arc_results_to_csv.py, from the *final* fit's
                # R/Q/alpha only) -- inject it here, per bootstrap sample,
                # via the same Brug formula, so its spread can be taken
                # across samples like every other derived quantity.
                comp_i["RQ1_C_F"] = _cpe_capacitance(
                    comp_i.get("RQ1_Q_F_s_alpha_minus_1"), comp_i.get("RQ1_R_ohm"), comp_i.get("RQ1_alpha")
                )
                comp_i["RQ2_C_F"] = _cpe_capacitance(
                    comp_i.get("RQ2_Q_F_s_alpha_minus_1"), comp_i.get("RQ2_R_ohm"), comp_i.get("RQ2_alpha")
                )
                component_samples.append(comp_i)

    if len(param_samples) < MC_MIN_SUCCESSFUL_REFITS:
        return None

    param_array = np.array(param_samples, dtype=float)
    param_std = np.std(param_array, axis=0, ddof=1)
    out: dict = {f"{key} Monte Carlo error": float(value) for key, value in zip(param_keys, param_std)}

    if component_samples:
        for value_key, mc_key in _RRQRQ_COMPONENT_VALUE_KEY_TO_MC_KEY.items():
            values = [
                comp[value_key] for comp in component_samples
                if isinstance(comp.get(value_key), (int, float)) and np.isfinite(comp[value_key])
            ]
            if len(values) >= MC_MIN_SUCCESSFUL_REFITS:
                out[mc_key] = float(np.std(values, ddof=1))

    out["mc_n_iterations"] = int(n_iterations)
    out["mc_n_success"] = int(len(param_samples))
    out["mc_relative_error"] = bool(relative_error)
    out["mc_n_freq_bins"] = int(n_freq_bins)
    return out


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
    # Equalize spans (not just pad each independently) so the axes box comes
    # out square while keeping true 1:1 Ohm-per-inch scaling -- padding each
    # axis by its own 5% left the box's shape following whatever Re/-Im
    # ratio the data happened to have, which for a Nyquist arc is usually
    # wide, not square.
    span = max(x_span, y_span)
    pad = 0.05 * span
    x_center = 0.5 * (x_min + x_max)
    y_center = 0.5 * (y_min + y_max)
    half = 0.5 * span + pad
    ax.set_xlim(x_center - half, x_center + half)
    ax.set_ylim(y_center - half, y_center + half)
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
    relative_error: bool = True,
) -> dict:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    ca_txt = _run_file(run_dir, summary.get("ca_fft_txt"), "ca_for_fft_pre_plus_scout_only.txt")
    peis_txt = _run_file(run_dir, summary.get("peis_txt"), "measured_peis.txt")
    # OLE export file stores the display convention (-ImZ). The legacy backend
    # and fitter expect the physical complex convention ImZ internally.
    peis = _average_duplicate_freq(_load_peis_txt(peis_txt))

    t_raw, v_raw, i_raw = _load_ca_txt(ca_txt)
    raw_dt_values = np.diff(np.asarray(t_raw, dtype=float))
    raw_dt_values = raw_dt_values[np.isfinite(raw_dt_values) & (raw_dt_values > 0)]
    raw_dt = float(np.median(raw_dt_values)) if len(raw_dt_values) else float("nan")
    t, v, i, _dt = _uniform_ca_grid(t_raw, v_raw, i_raw)

    # "_points" is the legacy key name (pre-dates expressing this window in
    # seconds); kept as a fallback so older summary.json files still label
    # correctly.
    smooth_s = summary.get("fft_ready_segmented_smoothing_window_s") or summary.get(
        "fft_ready_segmented_smoothing_window_points"
    )
    if smooth_s:
        ca_fft_input_label = f"FFT-ready, segmented tail smoothing {smooth_s}s"
    else:
        ca_fft_input_label = "FFT-ready"

    return _build_arc_and_plots(
        run_dir,
        t,
        v,
        i,
        peis,
        zero_pad_factor=zero_pad_factor,
        n_log_points=n_log_points,
        duration_guard_periods=duration_guard_periods,
        peis_anchor_gap_decades=peis_anchor_gap_decades,
        output_suffix=output_suffix,
        fit_model=fit_model,
        relative_error=relative_error,
        ca_fft_input_label=ca_fft_input_label,
        raw_recommended_lf_hz=summary.get("raw_recommended_lf_hz"),
        extra_summary_fields={
            "ca_fft_processing": "uniform_grid_from_fft_ready_trace",
            "ca_raw_dt_s": raw_dt,
        },
    )


def _build_arc_and_plots(
    run_dir: Path,
    t: np.ndarray,
    v: np.ndarray,
    i: np.ndarray,
    peis: np.ndarray,
    *,
    zero_pad_factor: int = 30,
    n_log_points: int = 100,
    duration_guard_periods: float = 0.0,
    peis_anchor_gap_decades: float = 0.0,
    output_suffix: str = "",
    fit_model: str = "RRQRQ",
    relative_error: bool = True,
    extra_summary_fields: dict | None = None,
    ca_fft_input_label: str | None = None,
    raw_recommended_lf_hz: float | None = None,
) -> dict:
    """Shared FFT + merge + fit + render + save core.

    Takes an already-loaded, already-uniform-gridded CA trace (t, v, i) and
    an already-loaded, duplicate-averaged PEIS array, so callers can supply
    differently-smoothed CA data (e.g. a re-despiked/re-binned raw trace)
    without duplicating the FFT/merge/fit/plot logic below. `make_full_arc`
    is the file-loading wrapper used by the live automated pipeline;
    `tools/ca_fft_tuning_gui.py` is the other caller, supplying its own
    reprocessed (t, v, i) from the raw saved CA trace.
    """
    peis_im = -peis[:, 2]
    peis_lf = float(np.nanmin(peis[:, 0]))
    peis_hf = float(np.nanmax(peis[:, 0]))
    dt_values = np.diff(np.asarray(t, dtype=float))
    dt_values = dt_values[np.isfinite(dt_values) & (dt_values > 0)]
    dt = float(np.median(dt_values)) if len(dt_values) else 0.1
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
    fft_max_used_hz = float(np.nanmax(fft_freq)) if len(fft_freq) else float("nan")
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
    fit, f_fit, z_fit = _fit_model(full_freq, full_re, full_im, model=fit_model, relative_error=relative_error)
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

    # Bode + CA-input overview (the Nyquist-with-fit panel that used to live
    # here was dropped -- it duplicated the dedicated nyquist_equal_aspect_*
    # plot below pixel-for-pixel, just without equal-aspect framing).
    out_png = run_dir / f"full_arc_merged_fft_lf_peis_hf_duration_gated{suffix}.png"
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.5), constrained_layout=True)

    z_full = full_re - 1j * full_neg_im
    ax = axs[0]
    ax.scatter(np.log10(full_freq), np.log10(np.abs(z_full)), c=np.log10(full_freq), cmap="viridis", s=25)
    if np.isfinite(z_fit.real).any():
        ax.plot(np.log10(f_fit), np.log10(np.abs(z_fit)), color="black", lw=1.4)
    ax.axvline(np.log10(peis_lf), color="gray", ls="--", lw=1)
    ax.set_title("|Z|")
    ax.set_xlabel("log10(f / Hz)")
    ax.set_ylabel("log10(|Z| / Ohm)")
    ax.grid(alpha=0.25)

    ax = axs[1]
    ax.scatter(np.log10(full_freq), np.degrees(np.angle(z_full)), c=np.log10(full_freq), cmap="viridis", s=25)
    if np.isfinite(z_fit.real).any():
        ax.plot(np.log10(f_fit), np.degrees(np.angle(z_fit)), color="black", lw=1.4)
    ax.axvline(np.log10(peis_lf), color="gray", ls="--", lw=1)
    ax.set_title("Phase")
    ax.set_xlabel("log10(f / Hz)")
    ax.set_ylabel("Phase / deg")
    ax.grid(alpha=0.25)

    ax = axs[2]
    ax.plot(t, i * 1e9, color="#d62728", lw=1.0)
    ax.set_title(f"CA FFT Input ({ca_fft_input_label or 'FFT-ready'})")
    ax.set_xlabel("time / s")
    ax.set_ylabel("current / nA")
    ax.grid(alpha=0.25)

    guard_label = "duration-gated" if duration_guard_hz > 0 else f"ZP{int(zero_pad_factor)} no-duration-guard"
    fig.suptitle(
        f"OLE-COM {guard_label} full arc | raw LF={raw_recommended_lf_hz} Hz, "
        f"PEIS LF={peis_lf:.4g} Hz, FFT used min={fft_min_used_hz:.4g} Hz, "
        f"score={fit.get('Fit quality score'):.4g}"
    )
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close(fig)

    # Nyquist plot from PEIS + CA with fit
    nyquist_png = run_dir / f"nyquist_equal_aspect_{fit_model.lower()}{suffix}.png"
    fig_ny, ax_ny = plt.subplots(figsize=(6, 6), constrained_layout=True)
    log_freq_all = np.log10(full_freq[full_freq > 0])
    vmin = float(np.nanmin(log_freq_all)) if log_freq_all.size else None
    vmax = float(np.nanmax(log_freq_all)) if log_freq_all.size else None
    sc_peis = ax_ny.scatter(
        peis[:, 1], peis[:, 2], c=np.log10(peis[:, 0]), cmap="plasma_r", vmin=vmin, vmax=vmax,
        s=38, marker="o", label=rf"{peis_lf:.3g} $\leq$ PEIS f $\leq$ {peis_hf:.3g} Hz",
    )
    sc_fft = None
    if len(fft_freq):
        sc_fft = ax_ny.scatter(
            fft_re, fft_neg_im, c=np.log10(fft_freq), cmap="plasma_r", vmin=vmin, vmax=vmax,
            s=44, marker="^", label=rf"{fft_min_used_hz:.3g} $\leq$ CA f $\leq$ {fft_max_used_hz:.3g} Hz",
        )
    if np.isfinite(z_fit.real).any():
        ax_ny.plot(z_fit.real, -z_fit.imag, color="black", lw=1.8, label=f"{fit.get('Fit model')} fit")
    x_for_limits = full_re
    y_for_limits = full_neg_im
    if np.isfinite(z_fit.real).any():
        x_for_limits = np.concatenate([x_for_limits, z_fit.real])
        y_for_limits = np.concatenate([y_for_limits, -z_fit.imag])
    _set_equal_nyquist_limits(ax_ny, x_for_limits, y_for_limits)

    divider = make_axes_locatable(ax_ny)
    cax = divider.append_axes("right", size="5%", pad=0.1)
    cbar = fig_ny.colorbar(sc_fft if sc_fft is not None else sc_peis, cax=cax)

    ax_ny.set_title("Hybrid EIS Nyquist Plot", fontsize=18)
    ax_ny.set_xlabel("Z$_{re}$ [Ohm]", fontsize=18)
    ax_ny.set_ylabel("-Z$_{im}$ [Ohm]", fontsize=18)
    # matplotlib decides sci-notation per axis from the actual tick values,
    # not the display range -- even with equal x/y spans (_set_equal_nyquist_
    # limits) the two can disagree if their centers differ. Force both axes
    # to the same style so -Zim always matches Zre instead of only sometimes.
    ax_ny.ticklabel_format(axis="both", style="sci", scilimits=(0, 0))
    ax_ny.tick_params(axis='both', which='major', labelsize=14)
    cbar.ax.tick_params(labelsize=14)
    cbar.set_label('log(f [Hz])', size=18)

    # ax_ny.grid(alpha=0.25)
    ax_ny.legend(fontsize=16, frameon=False)
    fig_ny.savefig(nyquist_png, dpi=200, bbox_inches="tight")
    plt.close(fig_ny)

    out_summary = {
        "run_dir": str(run_dir),
        "full_arc_png": str(out_png),
        "nyquist_equal_aspect_png": str(nyquist_png),
        "full_arc_txt": str(full_txt),
        "peis_lf_hz": peis_lf,
        "peis_hf_hz": peis_hf,
        "fft_min_reliable_hz": fft_min_reliable_hz,
        "fft_min_used_hz": fft_min_used_hz,
        "fft_max_used_hz": fft_max_used_hz,
        "fft_duration_guard_hz": duration_guard_hz,
        "fft_duration_guard_periods": float(duration_guard_periods),
        "fft_peis_anchor_high_hz": float(peis_anchor_high_hz),
        "fft_peis_anchor_gap_decades": float(peis_anchor_gap_decades),
        "fft_points_used": int(len(fft_freq)),
        **peak_completion,
        "peis_points_averaged": int(len(peis)),
        "ca_fft_processing": "uniform_grid",
        "ca_fft_dt_s": float(dt),
        "ca_fft_window_points": int(fft_window_points),
        "fit_model": fit.get("Fit model"),
        "fit_score": fit.get("Fit quality score"),
        "fit_cost": fit.get("Fit cost"),
        "fit_condition_number": fit.get("Fit condition number"),
        "fit_errors_ill_conditioned": fit.get("Fit errors ill-conditioned"),
        "fit_relative_error": bool(relative_error),
        "rrqrq_components": rrqrq_components,
        "fit": {k: (float(v) if isinstance(v, np.generic) else v) for k, v in fit.items() if k != "Auto fit candidate results"},
        "ca_duration_s": duration,
        "ca_dt_s": dt,
        "ca_raw_dt_s": None,
        "fft_method": "Raw",
        "fft_pre_smooth_interpolate_dt_s": None,
        "fft_pre_smooth_interpolate_window_points": 0,
        "fft_smoothing_window_s": 0.0,
        "fft_window_points": int(fft_window_points),
        "zero_pad_factor": int(zero_pad_factor),
        "n_log_points": int(n_log_points),
    }
    if extra_summary_fields:
        out_summary.update(extra_summary_fields)
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
    parser.add_argument(
        "--absolute-error",
        action="store_true",
        help=(
            "Fit with raw, unweighted (data-model) residuals instead of the "
            "default |Z_data|-normalized (relative) weighting. Changes the "
            "actual fitted parameters, not just their reported uncertainty -- "
            "off by default everywhere; must be explicitly requested."
        ),
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    relative_error = not args.absolute_error
    if str(args.output_suffix or "").strip():
        result = make_full_arc(
            run_dir,
            zero_pad_factor=args.zero_pad_factor,
            n_log_points=args.n_log_points,
            duration_guard_periods=args.duration_guard_periods,
            peis_anchor_gap_decades=args.peis_anchor_gap_decades,
            output_suffix=args.output_suffix,
            fit_model=args.fit_model,
            relative_error=relative_error,
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
        relative_error=relative_error,
    )
    relaxed10x30 = make_full_arc(
        run_dir,
        zero_pad_factor=30,
        n_log_points=args.n_log_points,
        duration_guard_periods=0.1,
        peis_anchor_gap_decades=args.peis_anchor_gap_decades,
        output_suffix="relaxed10x30",
        fit_model=args.fit_model,
        relative_error=relative_error,
    )
    zp30 = make_full_arc(
        run_dir,
        zero_pad_factor=30,
        n_log_points=args.n_log_points,
        duration_guard_periods=0.0,
        peis_anchor_gap_decades=args.peis_anchor_gap_decades,
        output_suffix="zp30",
        fit_model=args.fit_model,
        relative_error=relative_error,
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
            "recommended_fit_condition_number": recommended.get("fit_condition_number"),
            "recommended_fit_errors_ill_conditioned": recommended.get("fit_errors_ill_conditioned"),
            "recommended_nyquist_peak_reached": recommended.get("nyquist_peak_reached"),
            "recommended_nyquist_peak_drop_fraction": recommended.get("nyquist_peak_drop_fraction"),
            "hot_full_arc_variant": "hot_one_period_guard",
            "hot_full_arc_png": hot.get("full_arc_png"),
            "hot_full_arc_txt": hot.get("full_arc_txt"),
            "hot_nyquist_equal_aspect_png": hot.get("nyquist_equal_aspect_png"),
            "hot_fit_score": hot.get("fit_score"),
            "hot_fit_cost": hot.get("fit_cost"),
            "hot_fit_condition_number": hot.get("fit_condition_number"),
            "hot_fit_errors_ill_conditioned": hot.get("fit_errors_ill_conditioned"),
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
            "relaxed10x30_fit_condition_number": relaxed10x30.get("fit_condition_number"),
            "relaxed10x30_fit_errors_ill_conditioned": relaxed10x30.get("fit_errors_ill_conditioned"),
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
            "zp30_fit_condition_number": zp30.get("fit_condition_number"),
            "zp30_fit_errors_ill_conditioned": zp30.get("fit_errors_ill_conditioned"),
            "zp30_nyquist_peak_reached": zp30.get("nyquist_peak_reached"),
            "zp30_nyquist_peak_drop_fraction": zp30.get("nyquist_peak_drop_fraction"),
        }
    )
    (run_dir / "full_arc_summary.json").write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")
    print(json.dumps(combined, indent=2, default=str))


if __name__ == "__main__":
    main()
