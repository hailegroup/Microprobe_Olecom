# -*- coding: utf-8 -*-
"""
RQRQRQ equivalent-circuit fitting utilities.

Project-facing convention:
    Z = Zrq1 + Zrq2 + Zrq3
    Zrq = 1 / (1/R + Q * (j 2 pi f)^a)

This module now provides a stable fitting wrapper that:
  - tries multiple seed families,
  - re-fits the best result in a second pass,
  - scores candidates using shape and parameter plausibility,
  - can reuse the previous accepted fit and the full-data fit as seed sources.
"""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib_compat import install_pyplot_compat
install_pyplot_compat(plt)
plt.ioff()
from scipy.optimize import least_squares
from scipy.signal import find_peaks


TRUSTED_TEMPLATE = np.array(
    [
        30205.387763508486,
        2.7813618075356317e-10,
        0.9076782481853904,
        17755.392480470335,
        1.5947068628041627e-4,
        0.7389117087745343,
        321266.5906708489,
        7.71945097111093e-4,
        0.9876418976750849,
    ],
    dtype=float,
)

FIT_RESULT_KEYS = [
    "R0 (Ohm)",
    "Q0 (F·s^(a-1))",
    "a0",
    "R1 (Ohm)",
    "Q1 (F·s^(a-1))",
    "a1",
    "R2 (Ohm)",
    "Q2 (F·s^(a-1))",
    "a2",
]

MODEL_PARAM_KEYS = {
    "RQRQ": FIT_RESULT_KEYS[:6],
    "RRQRQ": ["Rs (Ohm)", *FIT_RESULT_KEYS[:6]],
    "RQRQRQ": FIT_RESULT_KEYS,
}

MODEL_PARAMETER_COUNT = {name: len(keys) for name, keys in MODEL_PARAM_KEYS.items()}


# Empirically calibrated, not a pure numerical-analysis cutoff: ordinary RQ/CPE
# fits on real EIS data routinely land in the ~1e8-1e9 range on their own
# (e.g. Rs/R and Q/alpha are naturally correlated for a single well-resolved
# arc) without the covariance estimate itself being unreliable -- a plain
# "half of float64's digits are gone" threshold (~sqrt(1/eps) =~ 6.7e7) fires
# on essentially every fit and is useless as a signal. Measured directly: a
# clean, well-determined single-arc fit sits around 2e8; a genuinely
# non-identifiable fit (e.g. a 2-branch model forced onto single-arc data,
# where the two branches can freely trade resistance between them) jumps to
# ~2e9+; a fully degenerate case (two branches seeded identically) reaches
# ~1e14-1e15, near float64's actual precision ceiling. 1e9 sits in the gap
# between "ordinary parameter correlation" and "genuine non-identifiability".
ILL_CONDITIONED_THRESHOLD = 1e9


def param_std_errors(result, n_params: int):
    """Asymptotic 1-sigma parameter uncertainty from a converged least_squares fit.

    Standard linearized (Gauss-Newton) estimate: cov = s^2 * (J^T J)^-1, where
    J is the residual Jacobian at the solution and s^2 = RSS / dof is the
    reduced chi-square. Every fit in this module normalizes residuals by
    max(|Z_data|, 1e-8) rather than a calibrated per-point measurement sigma,
    so s^2 self-consistently rescales that ad hoc relative weighting to match
    the fit's actual residual spread -- the same thing scipy.optimize.curve_fit
    does by default (absolute_sigma=False).

    Computed via SVD of J directly (mirroring scipy.optimize.curve_fit's own
    reference implementation), NOT via np.linalg.pinv(J.T @ J): forming J^T J
    explicitly squares J's condition number before inverting, which routinely
    pushes an already ill-conditioned (but still informative) Jacobian past
    float64's precision ceiling and silently corrupts the result -- observed
    directly on a poorly-fit RQ branch where J itself had condition number
    ~1.85e8, but J^T J's was ~3.4e16, beyond float64's ~4.5e15 precision
    limit, producing implausibly tiny "confident" errors on a fit that was
    actually barely constrained in that direction.

    Returns (perr, condition_number, ill_conditioned): perr is per-parameter
    1-sigma uncertainty; condition_number is J's own (not J^T J's) condition
    number; ill_conditioned flags condition_number > ILL_CONDITIONED_THRESHOLD,
    for callers to surface as a "don't trust these error bars" signal even
    after this numerically-stabler computation.
    """
    jac = np.asarray(result.jac, dtype=float)
    n_obs = jac.shape[0]
    dof = max(n_obs - int(n_params), 1)
    rss = 2.0 * float(result.cost)
    s_sq = rss / dof
    try:
        _u, s, vt = np.linalg.svd(jac, full_matrices=False)
        condition_number = float(s[0] / s[-1]) if s[-1] > 0 else float("inf")
        threshold = np.finfo(float).eps * max(jac.shape) * s[0]
        keep = s > threshold
        s_inv2 = np.where(keep, 1.0 / np.square(np.where(keep, s, 1.0)), 0.0)
        cov = s_sq * (vt.T * s_inv2) @ vt
        perr = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    except Exception:
        return np.full(int(n_params), np.nan), float("nan"), True
    ill_conditioned = bool(not np.isfinite(condition_number) or condition_number > ILL_CONDITIONED_THRESHOLD)
    return perr, condition_number, ill_conditioned


def Z_3RQ(p, freq):
    """Return complex impedance for the project RQRQRQ model."""
    r0, q0, a0, r1, q1, a1, r2, q2, a2 = p
    w = 2.0 * np.pi * np.asarray(freq, dtype=float)
    jw = 1j * w

    def _zrq(r, q, a):
        r = max(float(r), 1e-18)
        q = max(float(q), 1e-18)
        a = float(np.clip(a, 0.0, 1.0))
        return 1.0 / (1.0 / r + q * jw**a)

    return _zrq(r0, q0, a0) + _zrq(r1, q1, a1) + _zrq(r2, q2, a2)


def _zrq_branch(r, q, a, freq):
    w = 2.0 * np.pi * np.asarray(freq, dtype=float)
    jw = 1j * w
    r = max(float(r), 1e-18)
    q = max(float(q), 1e-18)
    a = float(np.clip(a, 0.0, 1.0))
    return 1.0 / (1.0 / r + q * jw**a)


def Z_model(model, params, freq):
    """Return complex impedance for one supported equivalent-circuit model."""
    model = str(model or "RQRQRQ").upper()
    p = np.asarray(params, dtype=float)
    if model == "RQRQ":
        r0, q0, a0, r1, q1, a1 = p
        return _zrq_branch(r0, q0, a0, freq) + _zrq_branch(r1, q1, a1, freq)
    if model == "RRQRQ":
        rs, r0, q0, a0, r1, q1, a1 = p
        return float(max(rs, 0.0)) + _zrq_branch(r0, q0, a0, freq) + _zrq_branch(r1, q1, a1, freq)
    if model == "RQRQRQ":
        return Z_3RQ(p, freq)
    raise ValueError(f"Unsupported EIS model: {model}")


def _fit_result_model(fit_result):
    return str(
        fit_result.get("Equivalent circuit")
        or fit_result.get("Fit model")
        or fit_result.get("Circuit model")
        or "RQRQRQ"
    ).upper()


def fit_result_to_model_params(fit_result):
    """Convert a model-aware fit-result dict into its compact parameter vector."""
    if fit_result is None:
        return None
    model = _fit_result_model(fit_result)
    keys = MODEL_PARAM_KEYS.get(model)
    if keys is None:
        return None
    try:
        params = np.array([float(fit_result[key]) for key in keys], dtype=float)
    except Exception:
        return None
    if params.size != len(keys) or not np.all(np.isfinite(params)):
        return None
    return params


def Z_from_fit_result(fit_result, freq):
    """Evaluate the model stored in a fit-result dict."""
    params = fit_result_to_model_params(fit_result)
    if params is None:
        seed = fit_result_to_seed(fit_result)
        if seed is None:
            return np.full_like(np.asarray(freq, dtype=float), np.nan, dtype=complex)
        return Z_3RQ(seed, freq)
    return Z_model(_fit_result_model(fit_result), params, freq)


def _nan_fit():
    result = {key: np.nan for key in FIT_RESULT_KEYS}
    result.update(
        {
            "Fit cost": np.nan,
            "Fit success": False,
            "Fit quality score": np.nan,
            "Fit seed source": "none",
            "Fit pass used": "none",
            "Fit pass1 quality score": np.nan,
            "Fit pass2 quality score": np.nan,
        }
    )
    return result


def fit_result_to_seed(fit_result):
    """Convert a fit-result dict into a seed vector if possible."""
    if fit_result is None:
        return None
    try:
        seed = np.array([float(fit_result[key]) for key in FIT_RESULT_KEYS], dtype=float)
    except Exception:
        return None
    if seed.size != 9 or not np.all(np.isfinite(seed)):
        return None
    return seed


def _moving_average(values, window):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    if window <= 1 or values.size < 3:
        return values.copy()
    if window % 2 == 0:
        window += 1
    window = max(3, min(int(window), values.size if values.size % 2 == 1 else values.size - 1))
    if window <= 1:
        return values.copy()
    kernel = np.ones(window, dtype=float) / window
    padded = np.pad(values, (window // 2, window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _nearest_valley_index(signal, peak_idx, side="left"):
    signal = np.asarray(signal, dtype=float)
    peak_idx = int(np.clip(peak_idx, 0, len(signal) - 1))

    if side == "left":
        if peak_idx == 0:
            return 0
        sub = signal[: peak_idx + 1]
        valley_candidates, _ = find_peaks(-sub)
        if valley_candidates.size:
            return int(valley_candidates[-1])
        return int(np.argmin(sub))

    if peak_idx >= len(signal) - 1:
        return len(signal) - 1
    sub = signal[peak_idx:]
    valley_candidates, _ = find_peaks(-sub)
    if valley_candidates.size:
        return int(peak_idx + valley_candidates[0])
    return int(peak_idx + np.argmin(sub))


def _arc_resistance_from_boundaries(re_s, left_idx, right_idx, min_r):
    left_idx = int(np.clip(left_idx, 0, len(re_s) - 1))
    right_idx = int(np.clip(right_idx, 0, len(re_s) - 1))
    if right_idx < left_idx:
        left_idx, right_idx = right_idx, left_idx
    width = float(re_s[right_idx] - re_s[left_idx])
    return max(abs(width), float(min_r))


def _safe_q_estimate(r_est, f_char, alpha_init=0.85):
    r_est = max(float(r_est), 1e-12)
    f_char = max(float(f_char), 1e-9)
    omega = 2.0 * np.pi * f_char
    return max(1.0 / (r_est * omega**alpha_init), 1e-12)


def _characteristic_frequency(r, q, a):
    r = max(float(r), 1e-18)
    q = max(float(q), 1e-18)
    a = max(float(a), 1e-6)
    log_term = -np.log(r * q) / a - np.log(2.0 * np.pi)
    log_term = float(np.clip(log_term, -700.0, 700.0))
    return float(np.exp(log_term))


def _trusted_template_seed(re_z):
    re_z = np.asarray(re_z, dtype=float)
    span = max(float(np.nanmax(re_z) - np.nanmin(re_z)), 1e-6)
    template_total = np.sum(TRUSTED_TEMPLATE[[0, 3, 6]])
    scale = max(span / template_total, 0.35)
    seed = TRUSTED_TEMPLATE.copy()
    seed[[0, 3, 6]] *= scale
    seed[[1, 4, 7]] /= max(scale, 1e-6)
    return seed


def _conservative_default_seed(re_z, freq):
    re_z = np.asarray(re_z, dtype=float)
    freq = np.asarray(freq, dtype=float)
    span = max(float(np.nanmax(re_z) - np.nanmin(re_z)), 1.0)
    f_sorted = np.sort(freq[freq > 0])[::-1]
    if f_sorted.size == 0:
        f_sorted = np.array([1.0, 0.1, 0.01], dtype=float)

    f0 = float(np.median(f_sorted[: max(1, f_sorted.size // 5)]))
    f1 = float(np.median(f_sorted[max(1, f_sorted.size // 5): max(2, (2 * f_sorted.size) // 3)]))
    f2 = float(np.median(f_sorted[max(2, (2 * f_sorted.size) // 3):]))
    r0 = max(span * 0.08, 50.0)
    r1 = max(span * 0.05, 20.0)
    r2 = max(span * 0.82, max(r0, r1) * 2.0)
    a0, a1, a2 = 0.93, 0.82, 0.92
    return np.array(
        [
            r0,
            _safe_q_estimate(r0, f0, a0),
            a0,
            r1,
            _safe_q_estimate(r1, f1, a1),
            a1,
            r2,
            _safe_q_estimate(r2, f2, a2),
            a2,
        ],
        dtype=float,
    )


def _auto_p0(freq, ReZ, ImZ):
    freq = np.asarray(freq, dtype=float)
    re_z = np.asarray(ReZ, dtype=float)
    im_z = np.asarray(ImZ, dtype=float)

    finite = np.isfinite(freq) & np.isfinite(re_z) & np.isfinite(im_z) & (freq > 0)
    freq = freq[finite]
    re_z = re_z[finite]
    im_z = im_z[finite]

    fallback = _trusted_template_seed(re_z).tolist()
    if freq.size < 6:
        print("  Auto p0 -> fallback (too few finite points)")
        return fallback

    idx_sort = np.argsort(freq)[::-1]
    f_s = freq[idx_sort]
    re_s = re_z[idx_sort]
    neg_im_s = -im_z[idx_sort]

    re_q10, re_q90 = np.percentile(re_s, [10, 90])
    r_span_robust = max(float(re_q90 - re_q10), 10.0)
    min_r = max(r_span_robust * 1e-4, 1e-3)

    smooth_window = max(5, min(21, (f_s.size // 12) * 2 + 1))
    neg_im_sm = _moving_average(neg_im_s, smooth_window)
    prominence = max(
        float(np.ptp(neg_im_sm)) * 0.03,
        float(np.max(neg_im_sm)) * 0.01 if np.max(neg_im_sm) > 0 else 0.0,
        1e-9,
    )
    distance = max(f_s.size // 18, 3)
    peaks, props = find_peaks(neg_im_sm, prominence=prominence, distance=distance)

    arc_entries = []
    if peaks.size:
        scores = props["prominences"] * np.maximum(neg_im_sm[peaks], 1e-12)
        selected = peaks[np.argsort(scores)[-3:]]
        selected = np.sort(selected)
        for peak_idx in selected:
            left_idx = _nearest_valley_index(neg_im_sm, peak_idx, side="left")
            right_idx = _nearest_valley_index(neg_im_sm, peak_idx, side="right")
            r_est = _arc_resistance_from_boundaries(re_s, left_idx, right_idx, min_r)
            f_char = max(float(f_s[peak_idx]), 1e-9)
            arc_entries.append((f_char, r_est))

    arc_entries = sorted(arc_entries, key=lambda item: item[0], reverse=True)
    default_entries = [
        (float(np.median(f_s[: max(1, f_s.size // 10)])), max(0.08 * r_span_robust, 10.0 * min_r)),
        (float(np.median(f_s[max(1, f_s.size // 10): max(2, f_s.size // 2)])), max(0.05 * r_span_robust, 10.0 * min_r)),
        (float(np.median(f_s[max(2, f_s.size // 2):])), max(0.87 * r_span_robust, 50.0 * min_r)),
    ]
    while len(arc_entries) < 3:
        arc_entries.append(default_entries[len(arc_entries)])

    arc_entries = arc_entries[:3]
    (f0_est, r0_est), (f1_est, r1_est), (f2_est, r2_est) = arc_entries
    r0_est = max(float(r0_est), min_r)
    r1_est = max(float(r1_est), min_r)
    r2_est = max(float(r2_est), max(2.0 * max(r0_est, r1_est), min_r))

    alpha0 = 0.95
    alpha1 = 0.85
    alpha2 = 0.80
    q0_est = _safe_q_estimate(r0_est, f0_est, alpha0)
    q1_est = _safe_q_estimate(r1_est, f1_est, alpha1)
    q2_est = _safe_q_estimate(r2_est, f2_est, alpha2)

    print(
        "  Auto p0 -> "
        f"R0={r0_est:.4g} (f={f0_est:.4g} Hz), "
        f"R1={r1_est:.4g} (f={f1_est:.4g} Hz), "
        f"R2={r2_est:.4g} (f={f2_est:.4g} Hz)"
    )

    return [r0_est, q0_est, alpha0, r1_est, q1_est, alpha1, r2_est, q2_est, alpha2]


def _prepare_data(freq, re_z, im_z, freq_range=None):
    freq = np.asarray(freq, dtype=float)
    re_z = np.asarray(re_z, dtype=float)
    im_z = np.asarray(im_z, dtype=float)

    finite = np.isfinite(freq) & np.isfinite(re_z) & np.isfinite(im_z) & (freq > 0)
    freq = freq[finite]
    z_data = re_z[finite] + 1j * im_z[finite]

    if freq_range is not None:
        f_min, f_max = freq_range
        mask = (freq >= f_min) & (freq <= f_max)
        freq = freq[mask]
        z_data = z_data[mask]

    if freq.size < 4:
        return np.array([]), np.array([], dtype=complex)

    idx = np.argsort(freq)[::-1]
    return freq[idx], z_data[idx]


def _normalize_seed(seed):
    seed = np.asarray(seed, dtype=float)
    if seed.size != 9 or not np.all(np.isfinite(seed)):
        return None
    norm = np.array(
        [
            max(float(seed[0]), 1e-6),
            max(float(seed[1]), 1e-12),
            float(np.clip(seed[2], 0.3, 1.0)),
            max(float(seed[3]), 1e-6),
            max(float(seed[4]), 1e-12),
            float(np.clip(seed[5], 0.3, 1.0)),
            max(float(seed[6]), 1e-6),
            max(float(seed[7]), 1e-12),
            float(np.clip(seed[8], 0.3, 1.0)),
        ],
        dtype=float,
    )
    return norm


def _dedupe_seed_candidates(seed_candidates):
    deduped = []
    signatures = set()
    for name, seed in seed_candidates:
        norm = _normalize_seed(seed)
        if norm is None:
            continue
        sig = tuple(np.round(np.log10(np.maximum(np.abs(norm), 1e-18)), 4))
        if sig in signatures:
            continue
        signatures.add(sig)
        deduped.append((name, norm))
    return deduped


def _sort_branch_params(params, perr=None):
    """Sort the 3 RQ branches by descending characteristic frequency.

    When `perr` (a parallel per-parameter uncertainty vector) is given, it is
    permuted by the same branch order and returned alongside params -- keeping
    each error matched to the parameter it belongs to after reordering.
    """
    params = np.asarray(params, dtype=float)
    branches = [params[0:3], params[3:6], params[6:9]]
    order = sorted(
        range(3),
        key=lambda i: _characteristic_frequency(branches[i][0], branches[i][1], branches[i][2]),
        reverse=True,
    )
    sorted_params = np.concatenate([branches[i] for i in order])
    if perr is None:
        return sorted_params
    perr = np.asarray(perr, dtype=float)
    perr_branches = [perr[0:3], perr[3:6], perr[6:9]]
    sorted_perr = np.concatenate([perr_branches[i] for i in order])
    return sorted_params, sorted_perr


def _fit_once(freq, z_data, seed, relative_error=True):
    lower = np.array([0.0, 1e-12, 0.0, 0.0, 1e-12, 0.0, 0.0, 1e-12, 0.0], dtype=float)
    upper = np.array([np.inf, np.inf, 1.0, np.inf, np.inf, 1.0, np.inf, np.inf, 1.0], dtype=float)

    def _residuals(params):
        z_model = Z_3RQ(params, freq)
        scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
        res_re = (z_data.real - z_model.real) / scale
        res_im = (z_data.imag - z_model.imag) / scale
        return np.concatenate([res_re, res_im])

    try:
        result = least_squares(
            _residuals,
            seed,
            bounds=(lower, upper),
            max_nfev=12000,
        )
    except Exception:
        return None

    if not result.success or not np.isfinite(result.cost):
        return None

    perr_raw, condition_number, ill_conditioned = param_std_errors(result, len(seed))
    params, perr = _sort_branch_params(result.x, perr_raw)
    return {
        "params": params,
        "perr": perr,
        "condition_number": condition_number,
        "ill_conditioned": ill_conditioned,
        "cost": float(result.cost),
        "success": True,
    }


def _evaluate_fit_quality(freq, z_data, params, cost):
    z_model = Z_3RQ(params, freq)
    abs_scale = np.maximum(np.abs(z_data), 1e-8)
    re_scale = max(np.ptp(z_data.real), 1.0)
    im_scale = max(np.ptp(-z_data.imag), 1.0)

    mag_res = (np.abs(z_data) - np.abs(z_model)) / abs_scale
    mag_rmse = float(np.sqrt(np.mean(np.square(mag_res))))

    phase_data = np.unwrap(np.angle(z_data))
    phase_model = np.unwrap(np.angle(z_model))
    phase_res_deg = np.rad2deg(phase_data - phase_model)
    phase_rmse_deg = float(np.sqrt(np.mean(np.square(phase_res_deg))))

    low_count = max(6, int(np.ceil(freq.size * 0.2)))
    low_idx = np.argsort(freq)[:low_count]
    nyquist_low = float(
        np.sqrt(
            np.mean(
                np.square((z_data.real[low_idx] - z_model.real[low_idx]) / re_scale)
                + np.square((z_data.imag[low_idx] - z_model.imag[low_idx]) / im_scale)
            )
        )
    )

    total_span = max(np.ptp(z_data.real), 1.0)
    r0, _, _, r1, _, _, r2, _, _ = params
    r1_ratio = float(r1 / total_span)
    r2_ratio = float(r2 / total_span)
    branch_penalty = 0.0
    if r1_ratio < 0.01 or r1_ratio > 0.30:
        branch_penalty += min(abs(r1_ratio - 0.08) * 4.0, 2.0)
    if r2_ratio < 0.35:
        branch_penalty += min((0.35 - r2_ratio) * 3.0, 2.0)
    if r2 < r1:
        branch_penalty += 1.0

    score = (
        float(cost) / max(freq.size, 1)
        + 3.0 * nyquist_low
        + 1.8 * mag_rmse
        + 0.02 * phase_rmse_deg
        + branch_penalty
    )

    return {
        "quality_score": float(score),
        "mag_rmse": mag_rmse,
        "phase_rmse_deg": phase_rmse_deg,
        "nyquist_low_rmse": nyquist_low,
        "branch_penalty": float(branch_penalty),
    }


def _result_from_params(params, cost, metrics, seed_source, pass_used, perr=None, condition_number=None, ill_conditioned=None):
    result = {key: float(value) for key, value in zip(FIT_RESULT_KEYS, params)}
    if perr is not None:
        for key, value in zip(FIT_RESULT_KEYS, perr):
            result[f"{key} error"] = float(value)
    if condition_number is not None:
        result["Fit condition number"] = float(condition_number)
    if ill_conditioned is not None:
        result["Fit errors ill-conditioned"] = bool(ill_conditioned)
    result.update(
        {
            "Fit cost": float(cost),
            "Fit success": True,
            "Fit quality score": float(metrics["quality_score"]),
            "Fit seed source": seed_source,
            "Fit pass used": pass_used,
        }
    )
    return result


def fit_RQRQRQ_stable(
    freq,
    ReZ,
    ImZ,
    p0=None,
    previous_fit_seed=None,
    reference_full_fit_seed=None,
    freq_range=None,
    return_details=False,
    relative_error=True,
):
    """
    Stable 3RQ fitting with multi-seed, two-pass refinement, and quality scoring.
    """
    freq, z_data = _prepare_data(freq, ReZ, ImZ, freq_range=freq_range)
    if freq.size < 4:
        result = _nan_fit()
        return (result, {"candidates": []}) if return_details else result

    auto_seed = np.asarray(_auto_p0(freq, z_data.real, z_data.imag), dtype=float)
    template_seed = _trusted_template_seed(z_data.real)
    default_seed = _conservative_default_seed(z_data.real, freq)

    seed_candidates = [
        ("auto", auto_seed),
        ("trusted_template", template_seed),
        ("conservative_default", default_seed),
    ]

    if p0 is not None:
        seed_candidates.append(("provided", p0))
    if previous_fit_seed is not None:
        prev_seed = fit_result_to_seed(previous_fit_seed)
        seed_candidates.append(("previous_fit", prev_seed if prev_seed is not None else previous_fit_seed))
    if reference_full_fit_seed is not None:
        ref_seed = fit_result_to_seed(reference_full_fit_seed)
        seed_candidates.append(("reference_full_fit", ref_seed if ref_seed is not None else reference_full_fit_seed))

    template_scaled = template_seed.copy()
    span_ratio = max(float(np.ptp(z_data.real)) / max(np.sum(template_seed[[0, 3, 6]]), 1e-6), 0.35)
    template_scaled[[0, 3, 6]] *= span_ratio
    template_scaled[[1, 4, 7]] /= max(span_ratio, 1e-6)
    seed_candidates.append(("trusted_template_scaled", template_scaled))
    seed_candidates = _dedupe_seed_candidates(seed_candidates)

    best_result = None
    best_score = np.inf
    candidate_logs = []

    for seed_name, seed in seed_candidates:
        pass1 = _fit_once(freq, z_data, seed, relative_error=relative_error)
        if pass1 is None:
            candidate_logs.append({"seed": seed_name, "pass1": None, "pass2": None})
            continue

        pass1_metrics = _evaluate_fit_quality(freq, z_data, pass1["params"], pass1["cost"])
        pass1_result = _result_from_params(
            pass1["params"], pass1["cost"], pass1_metrics, seed_name, "pass1",
            perr=pass1["perr"], condition_number=pass1["condition_number"], ill_conditioned=pass1["ill_conditioned"],
        )

        pass2 = _fit_once(freq, z_data, pass1["params"], relative_error=relative_error)
        pass2_result = None
        pass2_metrics = None
        if pass2 is not None:
            pass2_metrics = _evaluate_fit_quality(freq, z_data, pass2["params"], pass2["cost"])
            pass2_result = _result_from_params(
                pass2["params"], pass2["cost"], pass2_metrics, seed_name, "pass2",
                perr=pass2["perr"], condition_number=pass2["condition_number"], ill_conditioned=pass2["ill_conditioned"],
            )

        chosen = pass1_result
        if pass2_result is not None and pass2_result["Fit quality score"] <= pass1_result["Fit quality score"]:
            chosen = pass2_result

        chosen["Fit pass1 quality score"] = float(pass1_result["Fit quality score"])
        chosen["Fit pass2 quality score"] = float(pass2_result["Fit quality score"]) if pass2_result is not None else np.nan

        candidate_logs.append(
            {
                "seed": seed_name,
                "pass1": pass1_result,
                "pass2": pass2_result,
                "selected": chosen["Fit pass used"],
            }
        )

        if chosen["Fit quality score"] < best_score:
            best_score = chosen["Fit quality score"]
            best_result = chosen

    if best_result is None:
        result = _nan_fit()
        return (result, {"candidates": candidate_logs}) if return_details else result

    return (best_result, {"candidates": candidate_logs}) if return_details else best_result


def fit_RQRQRQ(freq, ReZ, ImZ, p0=None, freq_range=None):
    """Backward-compatible entry point."""
    return fit_RQRQRQ_stable(freq, ReZ, ImZ, p0=p0, freq_range=freq_range)


def _normalize_model_seed(model, seed):
    model = str(model).upper()
    seed = np.asarray(seed, dtype=float)
    if model == "RQRQ":
        if seed.size != 6 or not np.all(np.isfinite(seed)):
            return None
        return np.array(
            [
                max(float(seed[0]), 1e-6),
                max(float(seed[1]), 1e-12),
                float(np.clip(seed[2], 0.0, 1.0)),
                max(float(seed[3]), 1e-6),
                max(float(seed[4]), 1e-12),
                float(np.clip(seed[5], 0.0, 1.0)),
            ],
            dtype=float,
        )
    if model == "RRQRQ":
        if seed.size != 7 or not np.all(np.isfinite(seed)):
            return None
        return np.array(
            [
                max(float(seed[0]), 0.0),
                max(float(seed[1]), 1e-6),
                max(float(seed[2]), 1e-12),
                float(np.clip(seed[3], 0.0, 1.0)),
                max(float(seed[4]), 1e-6),
                max(float(seed[5]), 1e-12),
                float(np.clip(seed[6], 0.0, 1.0)),
            ],
            dtype=float,
        )
    return _normalize_seed(seed)


def _sort_model_params(model, params, perr=None):
    """Sort a model's RQ branches by descending characteristic frequency.

    See `_sort_branch_params` -- `perr` (if given) is permuted the same way so
    each parameter keeps its own uncertainty after reordering.
    """
    model = str(model).upper()
    params = np.asarray(params, dtype=float)
    perr = None if perr is None else np.asarray(perr, dtype=float)
    if model == "RQRQ":
        branches = [params[0:3], params[3:6]]
        order = sorted(
            range(2),
            key=lambda i: _characteristic_frequency(branches[i][0], branches[i][1], branches[i][2]),
            reverse=True,
        )
        sorted_params = np.concatenate([branches[i] for i in order])
        if perr is None:
            return sorted_params
        perr_branches = [perr[0:3], perr[3:6]]
        return sorted_params, np.concatenate([perr_branches[i] for i in order])
    if model == "RRQRQ":
        rs = float(params[0])
        branches = [params[1:4], params[4:7]]
        order = sorted(
            range(2),
            key=lambda i: _characteristic_frequency(branches[i][0], branches[i][1], branches[i][2]),
            reverse=True,
        )
        sorted_params = np.array([rs, *np.concatenate([branches[i] for i in order])], dtype=float)
        if perr is None:
            return sorted_params
        rs_err = float(perr[0])
        perr_branches = [perr[1:4], perr[4:7]]
        sorted_perr = np.array([rs_err, *np.concatenate([perr_branches[i] for i in order])], dtype=float)
        return sorted_params, sorted_perr
    return _sort_branch_params(params, perr)


def _model_seed_candidates(model, freq, z_data, p0=None):
    model = str(model).upper()
    re_z = z_data.real
    auto3 = np.asarray(_auto_p0(freq, re_z, z_data.imag), dtype=float)
    template3 = _trusted_template_seed(re_z)
    default3 = _conservative_default_seed(re_z, freq)
    three_rq_seeds = [
        ("auto", auto3),
        ("trusted_template", template3),
        ("conservative_default", default3),
    ]
    if p0 is not None:
        three_rq_seeds.append(("provided", p0))

    seed_candidates = []
    if model == "RQRQRQ":
        return _dedupe_seed_candidates(three_rq_seeds)

    if model == "RQRQ":
        for name, seed3 in three_rq_seeds:
            norm3 = _normalize_seed(seed3)
            if norm3 is None:
                continue
            triplets = [norm3[0:3], norm3[3:6], norm3[6:9]]
            for pair_name, pair in [("hf_lf", (0, 2)), ("hf_mf", (0, 1)), ("mf_lf", (1, 2))]:
                seed_candidates.append((f"{name}_{pair_name}", np.concatenate([triplets[pair[0]], triplets[pair[1]]])))
        span = max(float(np.ptp(re_z)), 1.0)
        f_hi = float(np.nanpercentile(freq, 85))
        f_lo = float(np.nanpercentile(freq, 15))
        r_hi = max(span * 0.06, 10.0)
        r_lo = max(span * 0.88, r_hi * 3.0)
        seed_candidates.append(
            (
                "two_branch_default",
                np.array(
                    [
                        r_hi,
                        _safe_q_estimate(r_hi, f_hi, 0.82),
                        0.82,
                        r_lo,
                        _safe_q_estimate(r_lo, f_lo, 0.92),
                        0.92,
                    ],
                    dtype=float,
                ),
            )
        )

    if model == "RRQRQ":
        high_count = max(3, min(len(freq), len(freq) // 8))
        rs_hf = max(float(np.nanmedian(re_z[:high_count])), 0.0)
        span = max(float(np.ptp(re_z)), 1.0)
        for name, seed3 in three_rq_seeds:
            norm3 = _normalize_seed(seed3)
            if norm3 is None:
                continue
            triplets = [norm3[0:3], norm3[3:6], norm3[6:9]]
            for rs_name, rs0 in [("rs0", 0.0), ("rshf", rs_hf)]:
                for pair_name, pair in [("hf_lf", (0, 2)), ("mf_lf", (1, 2))]:
                    seed_candidates.append((f"{name}_{rs_name}_{pair_name}", np.array([rs0, *triplets[pair[0]], *triplets[pair[1]]], dtype=float)))
        f_mid = float(np.nanpercentile(freq, 55))
        f_lo = float(np.nanpercentile(freq, 15))
        r_mid = max(span * 0.08, 10.0)
        r_lo = max(span * 0.85, r_mid * 3.0)
        seed_candidates.append(
            (
                "series_r_default",
                np.array(
                    [
                        rs_hf,
                        r_mid,
                        _safe_q_estimate(r_mid, f_mid, 0.82),
                        0.82,
                        r_lo,
                        _safe_q_estimate(r_lo, f_lo, 0.92),
                        0.92,
                    ],
                    dtype=float,
                ),
            )
        )

    deduped = []
    signatures = set()
    for name, seed in seed_candidates:
        norm = _normalize_model_seed(model, seed)
        if norm is None:
            continue
        sig = tuple(np.round(np.log10(np.maximum(np.abs(norm), 1e-18)), 4))
        if sig in signatures:
            continue
        signatures.add(sig)
        deduped.append((name, norm))
    return deduped


def _fit_model_once(model, freq, z_data, seed, relative_error=True):
    model = str(model).upper()
    seed = _normalize_model_seed(model, seed)
    if seed is None:
        return None
    if model == "RQRQ":
        lower = np.array([0.0, 1e-12, 0.0, 0.0, 1e-12, 0.0], dtype=float)
        upper = np.array([np.inf, np.inf, 1.0, np.inf, np.inf, 1.0], dtype=float)
    elif model == "RRQRQ":
        lower = np.array([0.0, 0.0, 1e-12, 0.0, 0.0, 1e-12, 0.0], dtype=float)
        upper = np.array([np.inf, np.inf, np.inf, 1.0, np.inf, np.inf, 1.0], dtype=float)
    else:
        return _fit_once(freq, z_data, seed, relative_error=relative_error)

    def _residuals(params):
        z_model = Z_model(model, params, freq)
        scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
        res_re = (z_data.real - z_model.real) / scale
        res_im = (z_data.imag - z_model.imag) / scale
        return np.concatenate([res_re, res_im])

    try:
        result = least_squares(_residuals, seed, bounds=(lower, upper), max_nfev=16000)
    except Exception:
        return None
    if not result.success or not np.isfinite(result.cost):
        return None
    perr_raw, condition_number, ill_conditioned = param_std_errors(result, len(seed))
    params, perr = _sort_model_params(model, result.x, perr_raw)
    return {
        "params": params,
        "perr": perr,
        "condition_number": condition_number,
        "ill_conditioned": ill_conditioned,
        "cost": float(result.cost),
        "success": True,
    }


def _evaluate_common_model_metrics(model, freq, z_data, params, cost, relative_error=True):
    z_model = Z_model(model, params, freq)
    scale = np.maximum(np.abs(z_data), 1e-8) if relative_error else np.ones_like(z_data, dtype=float)
    res_re = (z_data.real - z_model.real) / scale
    res_im = (z_data.imag - z_model.imag) / scale
    rss = float(np.sum(np.square(res_re)) + np.sum(np.square(res_im)))
    n_obs = max(int(res_re.size + res_im.size), 1)
    k = MODEL_PARAMETER_COUNT.get(str(model).upper(), len(params))
    rel_rmse = float(np.sqrt(np.mean(np.square(np.abs(z_data - z_model) / scale))))
    re_scale = max(np.ptp(z_data.real), 1.0)
    im_scale = max(np.ptp(-z_data.imag), 1.0)
    nyquist_rmse = float(np.sqrt(np.mean(np.square((z_data.real - z_model.real) / re_scale) + np.square((z_data.imag - z_model.imag) / im_scale))))
    low_count = max(6, int(np.ceil(freq.size * 0.2)))
    low_idx = np.argsort(freq)[:low_count]
    low_nyquist_rmse = float(
        np.sqrt(
            np.mean(
                np.square((z_data.real[low_idx] - z_model.real[low_idx]) / re_scale)
                + np.square((z_data.imag[low_idx] - z_model.imag[low_idx]) / im_scale)
            )
        )
    )
    bic = float(n_obs * np.log(max(rss / n_obs, 1e-300)) + k * np.log(n_obs))
    return {
        "common_rel_rmse": rel_rmse,
        "common_nyquist_rmse": nyquist_rmse,
        "common_low_nyquist_rmse": low_nyquist_rmse,
        "common_rss": rss,
        "common_bic": bic,
        "parameter_count": int(k),
        "Fit cost": float(cost),
    }


def _result_from_model_params(model, params, cost, metrics, seed_source, pass_used, perr=None, condition_number=None, ill_conditioned=None):
    model = str(model).upper()
    result = {key: np.nan for key in FIT_RESULT_KEYS}
    result["Equivalent circuit"] = model
    result["Fit model"] = model
    for key, value in zip(MODEL_PARAM_KEYS[model], params):
        result[key] = float(value)
    if perr is not None:
        for key, value in zip(MODEL_PARAM_KEYS[model], perr):
            result[f"{key} error"] = float(value)
    if condition_number is not None:
        result["Fit condition number"] = float(condition_number)
    if ill_conditioned is not None:
        result["Fit errors ill-conditioned"] = bool(ill_conditioned)
    result.update(
        {
            "Fit cost": float(cost),
            "Fit success": True,
            "Fit quality score": float(metrics["common_rel_rmse"]),
            "Fit seed source": seed_source,
            "Fit pass used": pass_used,
            "Model parameter count": int(metrics["parameter_count"]),
            "Common rel RMSE": float(metrics["common_rel_rmse"]),
            "Common Nyquist RMSE": float(metrics["common_nyquist_rmse"]),
            "Common low-frequency Nyquist RMSE": float(metrics["common_low_nyquist_rmse"]),
            "Common BIC": float(metrics["common_bic"]),
        }
    )
    offset = 1 if model == "RRQRQ" else 0
    branch_count = (len(params) - offset) // 3
    for idx in range(branch_count):
        base = offset + idx * 3
        result[f"RQ{idx} fchar (Hz)"] = _characteristic_frequency(params[base], params[base + 1], params[base + 2])
    if model == "RRQRQ":
        rs = float(params[0])
        r_a, q_a, alpha_a = (float(params[1]), float(params[2]), float(params[3]))
        r_b, q_b, alpha_b = (float(params[4]), float(params[5]), float(params[6]))
        f_a = _characteristic_frequency(r_a, q_a, alpha_a)
        f_b = _characteristic_frequency(r_b, q_b, alpha_b)
        rp = max(r_a + r_b, 1e-18)
        # RRQRQ params are sorted by characteristic frequency: RQ1 is the faster
        # branch and RQ2 is the slower branch.  Keep the legacy R0/R1 keys above,
        # but add explicit interpretation keys for summaries and papers.
        result.update(
            {
                "RRQRQ Rs (Ohm)": rs,
                "RRQRQ RQ1 R (Ohm)": r_a,
                "RRQRQ RQ1 Q (F*s^(a-1))": q_a,
                "RRQRQ RQ1 alpha": alpha_a,
                "RRQRQ RQ1 fchar (Hz)": f_a,
                "RRQRQ RQ1 resistance fraction": float(r_a / rp),
                "RRQRQ RQ2 R (Ohm)": r_b,
                "RRQRQ RQ2 Q (F*s^(a-1))": q_b,
                "RRQRQ RQ2 alpha": alpha_b,
                "RRQRQ RQ2 fchar (Hz)": f_b,
                "RRQRQ RQ2 resistance fraction": float(r_b / rp),
                "RRQRQ polarization R total (Ohm)": float(r_a + r_b),
            }
        )
    return result


def fit_circuit_model_stable(freq, ReZ, ImZ, model="RQRQ", p0=None, freq_range=None, return_details=False, relative_error=True):
    """Fit one supported model using the same normalized residual convention.

    relative_error=True (default) weights each residual by max(|Z_data|, 1e-8)
    -- every point contributes comparably in *percentage* terms, which is why
    every existing caller of this function gets identical results to before
    this parameter existed. relative_error=False minimizes raw, unweighted
    (data-model) residuals instead, letting the largest-|Z| points dominate
    the sum of squares -- this changes the actual fitted parameters, not just
    their reported uncertainty, so it is not a default anywhere in this
    codebase; it must be explicitly requested by every caller that wants it.
    """
    model = str(model).upper()
    if model == "RQRQRQ":
        result, details = fit_RQRQRQ_stable(
            freq, ReZ, ImZ, p0=p0, freq_range=freq_range, return_details=True, relative_error=relative_error,
        )
        freq_p, z_data = _prepare_data(freq, ReZ, ImZ, freq_range=freq_range)
        params = fit_result_to_seed(result)
        if params is not None and freq_p.size:
            metrics = _evaluate_common_model_metrics(
                model, freq_p, z_data, params, float(result.get("Fit cost", np.nan)), relative_error=relative_error,
            )
            result.update(_result_from_model_params(model, params, float(result.get("Fit cost", np.nan)), metrics, result.get("Fit seed source", "unknown"), result.get("Fit pass used", "unknown")))
        return (result, details) if return_details else result

    freq_p, z_data = _prepare_data(freq, ReZ, ImZ, freq_range=freq_range)
    if freq_p.size < 4:
        result = _nan_fit()
        result.update({"Equivalent circuit": model, "Fit model": model})
        return (result, {"candidates": []}) if return_details else result

    best_result = None
    best_score = np.inf
    candidate_logs = []
    for seed_name, seed in _model_seed_candidates(model, freq_p, z_data, p0=p0):
        pass1 = _fit_model_once(model, freq_p, z_data, seed, relative_error=relative_error)
        if pass1 is None:
            candidate_logs.append({"seed": seed_name, "pass1": None, "pass2": None})
            continue
        pass1_metrics = _evaluate_common_model_metrics(
            model, freq_p, z_data, pass1["params"], pass1["cost"], relative_error=relative_error,
        )
        pass1_result = _result_from_model_params(
            model, pass1["params"], pass1["cost"], pass1_metrics, seed_name, "pass1",
            perr=pass1["perr"], condition_number=pass1["condition_number"], ill_conditioned=pass1["ill_conditioned"],
        )

        pass2 = _fit_model_once(model, freq_p, z_data, pass1["params"], relative_error=relative_error)
        pass2_result = None
        if pass2 is not None:
            pass2_metrics = _evaluate_common_model_metrics(
                model, freq_p, z_data, pass2["params"], pass2["cost"], relative_error=relative_error,
            )
            pass2_result = _result_from_model_params(
                model, pass2["params"], pass2["cost"], pass2_metrics, seed_name, "pass2",
                perr=pass2["perr"], condition_number=pass2["condition_number"], ill_conditioned=pass2["ill_conditioned"],
            )

        chosen = pass1_result
        if pass2_result is not None and pass2_result["Common BIC"] <= pass1_result["Common BIC"]:
            chosen = pass2_result

        candidate_logs.append({"seed": seed_name, "pass1": pass1_result, "pass2": pass2_result, "selected": chosen["Fit pass used"]})
        if chosen["Common BIC"] < best_score:
            best_score = chosen["Common BIC"]
            best_result = chosen

    if best_result is None:
        result = _nan_fit()
        result.update({"Equivalent circuit": model, "Fit model": model})
        return (result, {"candidates": candidate_logs}) if return_details else result
    return (best_result, {"candidates": candidate_logs}) if return_details else best_result


def fit_equivalent_circuit_auto(
    freq,
    ReZ,
    ImZ,
    models=("RQRQ", "RRQRQ", "RQRQRQ"),
    p0=None,
    freq_range=None,
    simplicity_rel_margin=0.08,
    simplicity_abs_margin=0.005,
    return_details=False,
):
    """
    Fit candidate circuits and select the simplest model that is statistically close.

    The selector intentionally avoids overfitting: if RQRQ is within the configured
    margin of the best residual, it wins over RRQRQ/RQRQRQ. More complex models are
    selected only when they materially improve both total and low-frequency errors.
    """
    candidate_results = []
    details_by_model = {}
    for model in models:
        model = str(model).upper()
        result, details = fit_circuit_model_stable(freq, ReZ, ImZ, model=model, p0=p0, freq_range=freq_range, return_details=True)
        details_by_model[model] = details
        if bool(result.get("Fit success", False)) and np.isfinite(float(result.get("Common rel RMSE", np.nan))):
            candidate_results.append(result)

    if not candidate_results:
        result = _nan_fit()
        result.update({"Equivalent circuit": "none", "Fit model": "none", "Auto fit candidate results": []})
        return (result, {"models": details_by_model}) if return_details else result

    best_rel = min(float(r["Common rel RMSE"]) for r in candidate_results)
    best_low = min(float(r["Common low-frequency Nyquist RMSE"]) for r in candidate_results)
    eligible = [
        r
        for r in sorted(candidate_results, key=lambda item: (int(item["Model parameter count"]), float(item["Common BIC"])))
        if float(r["Common rel RMSE"]) <= best_rel * (1.0 + simplicity_rel_margin) + simplicity_abs_margin
        and float(r["Common low-frequency Nyquist RMSE"]) <= best_low * 1.20 + 2.0 * simplicity_abs_margin
    ]
    selected = eligible[0] if eligible else min(candidate_results, key=lambda item: float(item["Common BIC"]))
    selected = dict(selected)
    selected["Auto fit selected"] = True
    selected["Auto selection reason"] = (
        "simplest_within_margin" if eligible else "lowest_bic_no_simple_candidate"
    )
    selected["Auto fit candidate results"] = [
        {
            "model": r["Equivalent circuit"],
            "parameter_count": int(r["Model parameter count"]),
            "common_rel_rmse": float(r["Common rel RMSE"]),
            "common_low_nyquist_rmse": float(r["Common low-frequency Nyquist RMSE"]),
            "common_bic": float(r["Common BIC"]),
            "fit_cost": float(r["Fit cost"]),
        }
        for r in sorted(candidate_results, key=lambda item: int(item["Model parameter count"]))
    ]
    return (selected, {"models": details_by_model, "selected": selected}) if return_details else selected


def compare_fit_outputs(reference_fit, candidate_fit):
    """Parameter-space comparison between two fit dictionaries."""
    metrics = {}
    for key in FIT_RESULT_KEYS:
        ref_val = float(reference_fit.get(key, np.nan))
        cand_val = float(candidate_fit.get(key, np.nan))
        rel_key = f"{key} rel err"
        if np.isfinite(ref_val) and abs(ref_val) > 1e-12 and np.isfinite(cand_val):
            metrics[rel_key] = abs(cand_val - ref_val) / abs(ref_val)
        else:
            metrics[rel_key] = np.nan
    valid = [value for value in metrics.values() if np.isfinite(value)]
    metrics["parameter_rel_err_mean"] = float(np.mean(valid)) if valid else np.nan
    metrics["parameter_rel_err_max"] = float(np.max(valid)) if valid else np.nan
    return metrics


def compare_fit_to_data(freq_reference, z_reference, fit_result):
    """Data-space comparison between measured data and fit result."""
    freq_reference = np.asarray(freq_reference, dtype=float)
    z_reference = np.asarray(z_reference, dtype=complex)
    params = fit_result_to_model_params(fit_result)
    if params is None or freq_reference.size == 0:
        return {
            "nyquist_low_rmse": np.nan,
            "mag_rmse": np.nan,
            "phase_rmse_deg": np.nan,
            "quality_score": np.nan,
        }
    common = _evaluate_common_model_metrics(
        _fit_result_model(fit_result),
        freq_reference,
        z_reference,
        params,
        float(fit_result.get("Fit cost", np.nan)),
    )
    return {
        "nyquist_low_rmse": common["common_low_nyquist_rmse"],
        "mag_rmse": common["common_rel_rmse"],
        "phase_rmse_deg": np.nan,
        "quality_score": common["common_rel_rmse"],
        **common,
    }


def plot_fit(freq, ReZ, ImZ, fit_params, title=""):
    """Plot measured data and the fitted RQRQRQ response."""
    freq = np.asarray(freq, dtype=float)
    re_z = np.asarray(ReZ, dtype=float)
    im_z = np.asarray(ImZ, dtype=float)
    z_meas = re_z + 1j * im_z

    p = [
        fit_params["R0 (Ohm)"],
        fit_params["Q0 (F·s^(a-1))"],
        fit_params["a0"],
        fit_params["R1 (Ohm)"],
        fit_params["Q1 (F·s^(a-1))"],
        fit_params["a1"],
        fit_params["R2 (Ohm)"],
        fit_params["Q2 (F·s^(a-1))"],
        fit_params["a2"],
    ]

    finite = np.isfinite(freq) & np.isfinite(re_z) & np.isfinite(im_z) & (freq > 0)
    freq = freq[finite]
    z_meas = z_meas[finite]

    if freq.size == 0:
        fig, _ = plt.subplots(figsize=(6, 4))
        return fig

    min_plot_freq = max(min(float(np.min(freq)) / 100.0, 1e-5), 1e-6)
    f_fit = np.logspace(np.log10(min_plot_freq), np.log10(np.max(freq)), 500)
    z_fit = Z_from_fit_result(fit_params, f_fit)
    z_all = np.concatenate([z_meas, z_fit])
    x_all = z_all.real
    y_all = -z_all.imag

    x_min = float(np.nanmin(x_all))
    x_max = float(np.nanmax(x_all))
    y_min = float(np.nanmin(y_all))
    y_max = float(np.nanmax(y_all))
    x_pad = max((x_max - x_min) * 0.05, 1e-9)
    y_pad = max((y_max - y_min) * 0.05, 1e-9)

    logf_all = np.concatenate([np.log10(freq), np.log10(f_fit)])
    mag_all = np.concatenate([np.log10(np.abs(z_meas)), np.log10(np.abs(z_fit))])
    phase_all = np.concatenate([np.angle(z_meas, deg=True), np.angle(z_fit, deg=True)])
    logf_min = float(np.nanmin(logf_all))
    logf_max = float(np.nanmax(logf_all))
    logf_pad = max((logf_max - logf_min) * 0.03, 1e-9)
    mag_min = float(np.nanmin(mag_all))
    mag_max = float(np.nanmax(mag_all))
    mag_pad = max((mag_max - mag_min) * 0.05, 1e-9)
    phase_min = float(np.nanmin(phase_all))
    phase_max = float(np.nanmax(phase_all))
    phase_pad = max((phase_max - phase_min) * 0.05, 1e-9)

    fig, axs = plt.subplot_mosaic(
        [["nyquist", "mag"], ["nyquist", "phase"]],
        figsize=(10, 5.5),
        layout="constrained",
    )

    model_name = _fit_result_model(fit_params)
    axs["nyquist"].plot(z_fit.real, -z_fit.imag, color="tab:red", linewidth=2.0, label=f"{model_name} fit")
    axs["nyquist"].scatter(z_meas.real, -z_meas.imag, s=12, color="tab:blue", label="Data")
    axs["nyquist"].set_xlabel("Re(Z) / Ohm")
    axs["nyquist"].set_ylabel("-Im(Z) / Ohm")
    axs["nyquist"].set_title(f"Nyquist - {title}" if title else "Nyquist")
    axs["nyquist"].grid(True, alpha=0.25)
    axs["nyquist"].legend(loc="best", fontsize=9)
    x_center = 0.5 * ((x_min - x_pad) + (x_max + x_pad))
    y_center = 0.5 * ((y_min - y_pad) + (y_max + y_pad))
    span = max((x_max - x_min) + 2.0 * x_pad, (y_max - y_min) + 2.0 * y_pad, 1e-9)
    half = 0.5 * span
    axs["nyquist"].set_xlim(x_center - half, x_center + half)
    axs["nyquist"].set_ylim(y_center - half, y_center + half)
    axs["nyquist"].set_aspect("equal", adjustable="box")

    axs["mag"].plot(np.log10(f_fit), np.log10(np.abs(z_fit)), color="tab:red", linewidth=2.0)
    axs["mag"].scatter(np.log10(freq), np.log10(np.abs(z_meas)), s=12, color="tab:blue")
    axs["mag"].set_ylabel("log10(|Z| / Ohm)")
    axs["mag"].set_title("Magnitude")
    axs["mag"].grid(True, alpha=0.25)
    axs["mag"].set_xlim(logf_min - logf_pad, logf_max + logf_pad)
    axs["mag"].set_ylim(mag_min - mag_pad, mag_max + mag_pad)

    axs["phase"].plot(np.log10(f_fit), np.angle(z_fit, deg=True), color="tab:red", linewidth=2.0)
    axs["phase"].scatter(np.log10(freq), np.angle(z_meas, deg=True), s=12, color="tab:blue")
    axs["phase"].set_xlabel("log10(f / Hz)")
    axs["phase"].set_ylabel("Phase / deg")
    axs["phase"].set_title("Phase")
    axs["phase"].grid(True, alpha=0.25)
    axs["phase"].set_xlim(logf_min - logf_pad, logf_max + logf_pad)
    axs["phase"].set_ylim(phase_min - phase_pad, phase_max + phase_pad)

    return fig
