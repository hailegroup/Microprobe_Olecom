# -*- coding: utf-8 -*-
"""
Full vs Optimized measurement comparison — 2-arc model fitting.

Model: Z = R0 + 1/(1/R1 + Q1*(jw)^a1) + 1/(1/R2 + Q2*(jw)^a2)
  No series CPE: the original RQRQRQ model had a series CPE that
  absorbed low-freq behavior and made R2 too small (~11 kOhm instead of ~330 kOhm).

Optimization logic:
  1. Full measurement: entire PEIS (to lowest f) + entire CP duration.
  2. Optimized: PEIS only ABOVE the recommended cutoff frequency (cutoff_f),
     combined with recovered EIS from a shortened CP run (whose upper bound
     is cutoff_f).  Both dimensions are optimized simultaneously.
  3. Sweep: vary CP duration while using the optimized PEIS range to find
     the minimum CP duration where R2 (and LF |Z|) converge within tolerance.
"""

import matplotlib
matplotlib.use("Agg")

import json, os, sys
import matplotlib.pyplot as plt
import numpy as np
from lmfit import Parameters, minimize as lm_minimize
from scipy.signal import find_peaks

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import Convert_CP_to_EIS as DC_EIS
import Load_CP_Data as LD
import Smooth_and_Interpolate as SI


# ── settings ────────────────────────────────────────────────────────────────
DATASETS = [
    {
        "label": "260417-7",
        "ca_path": "Input data/260417-7/300 rapid measurement_02_CA_C02.txt",
        "eis_path": "Input data/260417-7/300 rapid measurement_01_PEIS_C02.txt",
    },
    {
        "label": "260417-8",
        "ca_path": "Input data/260417-8/300 rapid measurement_02_CA_C02.txt",
        "eis_path": "Input data/260417-8/300 rapid measurement_01_PEIS_C02.txt",
    },
]

DT = 0.01
WINDOW = 51
ZERO_PAD = 50
N_LOG = 100
MIN_PERIODS_FULL  = 1.0  # full run: at least 1 full period (reliable Nyquist limit)
MIN_PERIODS_SWEEP = 1.0  # sweep: at least 1 period — zero-padded data at short T is noise
CONVERGENCE_TOL = 0.10   # R2 and LF|Z| within 10% of full-run value

SWEEP_DURATIONS = [10, 20, 30, 45, 60, 90, 120, 150, 180, 210, 240, 270, 300, 320]

OUT_DIR = os.path.join("result", "vs_optimized")
os.makedirs(OUT_DIR, exist_ok=True)


# ── 2-arc impedance model ────────────────────────────────────────────────────

def Z_2arc(p, freq):
    """Z = R0 + (R1||CPE1) + (R2||CPE2)  — no series CPE element."""
    r0, r1, q1, a1, r2, q2, a2 = p
    w = 2.0 * np.pi * np.asarray(freq, dtype=float)
    jw = 1j * w
    z1 = 1.0 / (1.0 / r1 + q1 * jw ** a1)
    z2 = 1.0 / (1.0 / r2 + q2 * jw ** a2)
    return r0 + z1 + z2


def _nan_fit():
    return {
        "R0 (Ohm)": np.nan, "R1 (Ohm)": np.nan,
        "Q1 (F*s^(a-1))": np.nan, "a1": np.nan,
        "R2 (Ohm)": np.nan, "Q2 (F*s^(a-1))": np.nan, "a2": np.nan,
        "Fit cost": np.nan, "Fit success": False,
    }


def _ma(values, window):
    v = np.asarray(values, dtype=float)
    if v.size < 3 or window <= 1:
        return v.copy()
    w = max(3, min(int(window) | 1, v.size if v.size % 2 == 1 else v.size - 1))
    return np.convolve(np.pad(v, (w // 2, w // 2), mode="edge"), np.ones(w) / w, mode="valid")


def _auto_p0_2arc(freq, rez, imz):
    """Initial guess for R0 + RQ1 + RQ2. freq must be sorted descending."""
    n_hf = max(3, min(10, len(freq) // 5))
    r0 = max(float(np.median(rez[:n_hf])), 1.0)

    re_span = max(float(np.percentile(rez, 90) - r0), r0 * 0.1, 10.0)
    r1_init = re_span * 0.25
    r2_init = re_span * 5.0   # R2 is expected to be the large low-freq arc

    neg_im = -np.asarray(imz, dtype=float)
    sw = max(5, min(21, len(neg_im) // 10 * 2 + 1))
    sm = _ma(neg_im, sw)
    prom = max(np.ptp(sm) * 0.03, sm.max() * 0.01 if sm.max() > 0 else 1e-9, 1e-9)
    peaks, _ = find_peaks(sm, prominence=prom, distance=max(len(freq) // 15, 3))

    if peaks.size >= 1:
        p1 = peaks[0]
        f1 = max(float(freq[p1]), 1e-9)
        r1_init = max(float(rez[p1]) - r0, r1_init)
    else:
        f1 = max(float(np.median(freq[:max(len(freq) // 4, 1)])), 1e-9)

    if peaks.size >= 2:
        p2 = peaks[-1]
        f2 = max(float(freq[p2]), 1e-9)
        r2_init = max(float(rez[p2]) - r0 - r1_init, r1_init * 2, r2_init)
    else:
        f2 = max(float(np.median(freq[max(len(freq) // 2, 0):])), 1e-9)
        r2_init = max(re_span * 5.0, r1_init * 3)

    alpha = 0.85
    q1 = max(1.0 / (r1_init * (2 * np.pi * f1) ** alpha), 1e-12)
    q2 = max(1.0 / (r2_init * (2 * np.pi * f2) ** alpha), 1e-12)

    print(f"  p0_2arc: R0={r0:.4g}  R1={r1_init:.4g}(f={f1:.4g}Hz)  R2={r2_init:.4g}(f={f2:.4g}Hz)")
    return [r0, r1_init, q1, alpha, r2_init, q2, alpha]


def fit_2arc(freq_arr, rez_arr, imz_arr, p0=None, r0_fix=None):
    """
    Fit Z = R0 + RQ1 + RQ2 with modulus weighting.

    r0_fix: PEIS high-frequency Re(Z) estimate.  R0 is constrained to
            r0_fix ± 15% so the optimizer cannot collapse the ohmic term.
    Multi-start on R2 with a wide absolute range to find the large low-freq arc.
    """
    freq_arr = np.asarray(freq_arr, dtype=float)
    z_data = np.asarray(rez_arr, dtype=float) + 1j * np.asarray(imz_arr, dtype=float)
    ok = np.isfinite(freq_arr) & np.isfinite(z_data.real) & np.isfinite(z_data.imag) & (freq_arr > 0)
    freq_arr, z_data = freq_arr[ok], z_data[ok]

    if freq_arr.size < 4:
        return _nan_fit()

    idx = np.argsort(freq_arr)[::-1]
    freq_arr, z_data = freq_arr[idx], z_data[idx]

    base = _auto_p0_2arc(freq_arr, z_data.real, z_data.imag) if p0 is None else list(p0)

    # R0 is anchored to PEIS high-frequency Re(Z) — known and reliable.
    r0_anchor = r0_fix if r0_fix is not None else base[0]
    r0_lo = r0_anchor * 0.80
    r0_hi = r0_anchor * 1.20
    base[0] = r0_anchor  # override auto-p0 R0

    def _try_fit(p0_try):
        def residuals(params):
            p = [params[k].value for k in ["R0","R1","Q1","a1","R2","Q2","a2"]]
            zm = Z_2arc(p, freq_arr)
            sc = np.maximum(np.abs(z_data), 1e-12)
            return np.concatenate([(z_data.real - zm.real) / sc, (z_data.imag - zm.imag) / sc])

        pars = Parameters()
        pars.add("R0", value=float(np.clip(p0_try[0], r0_lo, r0_hi)), min=r0_lo, max=r0_hi)
        pars.add("R1", value=max(p0_try[1], 1e-9), min=0.0)
        pars.add("Q1", value=max(p0_try[2], 1e-12), min=1e-12)
        pars.add("a1", value=float(np.clip(p0_try[3], 0.3, 1.0)), min=0.0, max=1.0)
        pars.add("R2", value=max(p0_try[4], 1e-9), min=0.0)
        pars.add("Q2", value=max(p0_try[5], 1e-12), min=1e-12)
        pars.add("a2", value=float(np.clip(p0_try[6], 0.3, 1.0)), min=0.0, max=1.0)
        try:
            res = lm_minimize(residuals, pars, method="least_squares", max_nfev=20000)
            cost = float(np.sum(np.square(res.residual))) / 2.0
            return res.params, cost
        except Exception:
            return None, np.inf

    # Multi-start: (R2, f_char2) pairs spanning physically relevant range.
    # For SOFC/SOEC electrodes: f_char2 in 0.0001–0.05 Hz, R2 in 10 kΩ–3 MΩ.
    alpha_init = 0.85
    r2_f2_seeds = [
        (1e3,  0.1),  (1e4,  0.05), (5e4,  0.01),
        (1e5,  0.005),(3e5,  0.001),(5e5,  0.001),
        (1e6,  3e-4), (3e6,  1e-4),
    ]
    best_params, best_cost = None, np.inf
    for r2_seed, f2_seed in r2_f2_seeds:
        p0_try = list(base)
        p0_try[4] = float(r2_seed)
        p0_try[5] = max(1.0 / (r2_seed * (2 * np.pi * f2_seed) ** alpha_init), 1e-12)
        params, cost = _try_fit(p0_try)
        if params is not None and np.isfinite(cost):
            if cost < best_cost:
                best_cost = cost
                best_params = params

    if best_params is None:
        return _nan_fit()

    return {
        "R0 (Ohm)": best_params["R0"].value,
        "R1 (Ohm)": best_params["R1"].value,
        "Q1 (F*s^(a-1))": best_params["Q1"].value,
        "a1": best_params["a1"].value,
        "R2 (Ohm)": best_params["R2"].value,
        "Q2 (F*s^(a-1))": best_params["Q2"].value,
        "a2": best_params["a2"].value,
        "Fit cost": best_cost,
        "Fit success": True,
    }


# ── data loading and FFT helpers ─────────────────────────────────────────────

def load_and_smooth(ca_path, eis_path):
    dc_raw = LD.load_dc_from_path(
        ca_path, current_in_mA=True, current_scale_factor=1.0, CA_step_only=False
    )
    eis = LD.load_eis_from_path(eis_path)
    time, volt, curr = SI.smooth_and_interpolate(
        dc_raw[:, 0], dc_raw[:, 1], dc_raw[:, 2], DT, WINDOW
    )
    return dc_raw, eis, time, volt, curr


def run_fft(time, volt, curr, eis, f_max_override=None, min_periods=MIN_PERIODS_FULL):
    """
    FFT recovery with optional min_f = min_periods/duration guard.
    min_periods=0 uses all FFT data; min_periods=1 requires at least 1 full period.
    Returns (filtered_f, recovered_z, ft_f, ft_v, ft_i, min_f).
    """
    duration = float(np.max(time) - np.min(time))
    min_f = (min_periods / max(duration, 1e-6)) if min_periods > 0 else None

    res = DC_EIS.FFT_EIS(
        time, volt, curr, "Smooth", DT, WINDOW,
        f_max_override, ZERO_PAD, N_LOG, eis_data=eis,
    )
    ft_f, ft_v, ft_i = res[3], res[4], res[5]

    peis_lowest_f = float(np.min(eis[:, 0]))
    target_max_f = f_max_override if f_max_override is not None else peis_lowest_f

    filtered_f, recovered_z = DC_EIS.extract_log_spaced_impedance(
        ft_f, ft_v, ft_i, max_f=target_max_f, n_log_points=N_LOG, min_f=min_f
    )
    return filtered_f, recovered_z, ft_f, ft_v, ft_i, min_f


def fit_combined(eis, filtered_f, recovered_z, peis_f_min=None):
    """
    Combine PEIS (above peis_f_min) + recovered EIS and fit 2-arc model.
    peis_f_min = cutoff_f for optimized scenario, None for full scenario.
    """
    eis_use = eis[eis[:, 0] >= peis_f_min] if peis_f_min is not None else eis.copy()

    if len(filtered_f) == 0:
        return np.array([]), np.array([]), np.array([]), _nan_fit()

    freq_all = np.concatenate([eis_use[:, 0], filtered_f])
    rez_all  = np.concatenate([eis_use[:, 1], recovered_z.real])
    imz_all  = np.concatenate([eis_use[:, 2], recovered_z.imag])
    idx = np.argsort(freq_all)[::-1]
    freq_all, rez_all, imz_all = freq_all[idx], rez_all[idx], imz_all[idx]

    # R0 anchor from PEIS high-frequency Re(Z) — the most reliable constraint.
    eis_hf = eis[np.argsort(eis[:, 0])[::-1]]
    n_hf = max(3, min(10, len(eis_hf) // 5))
    r0_hf = max(float(np.median(eis_hf[:n_hf, 1])), 1.0)

    fit = fit_2arc(freq_all, rez_all, imz_all, r0_fix=r0_hf)
    return freq_all, rez_all, imz_all, fit


def lf_z_mag(eis, filtered_f, recovered_z):
    all_f = np.concatenate([eis[:, 0], filtered_f])
    all_z = np.concatenate([eis[:, 1] + 1j * eis[:, 2], recovered_z])
    return float(np.abs(all_z[np.argmin(all_f)]))


# ── main analysis ────────────────────────────────────────────────────────────

def analyze_one(label, ca_path, eis_path):
    print(f"\n{'='*58}\n{label}\n{'='*58}")

    dc_raw, eis, time_full, volt_full, curr_full = load_and_smooth(ca_path, eis_path)
    full_duration = float(np.max(time_full) - np.min(time_full))
    peis_lowest_f = float(np.min(eis[:, 0]))

    print(f"Full CA duration : {full_duration:.1f} s")
    print(f"PEIS lowest f    : {peis_lowest_f:.4f} Hz")

    # ── Full run FFT ─────────────────────────────────────────────
    print("\n[Full] FFT recovery ...")
    filtered_f_full, rec_z_full, ft_f, ft_v, ft_i, min_f_full = run_fft(
        time_full, volt_full, curr_full, eis, min_periods=MIN_PERIODS_FULL
    )
    min_f_str = f"{min_f_full:.5f}" if min_f_full is not None else "none"
    print(f"  Recovered {len(filtered_f_full)} pts "
          f"{filtered_f_full.min():.5f}-{filtered_f_full.max():.4f} Hz  "
          f"(min_f={min_f_str})")

    # FFT stability analysis → recommended PEIS cutoff frequency
    analysis_max_f = min(float(np.max(ft_f[ft_f > 0])), peis_lowest_f * 8.0)
    a_f, a_z = DC_EIS.extract_log_spaced_impedance(
        ft_f, ft_v, ft_i, max_f=analysis_max_f, n_log_points=200, min_f=min_f_full
    )
    rec = DC_EIS.recommend_peis_lowest_frequency(a_f, a_z)
    cutoff_f = rec["recommended_peis_lowest_freq_hz"]
    cp_info = DC_EIS.summarize_cp_duration_requirements(
        time_full, peis_lowest_f,
        recommended_peis_lowest_freq_hz=cutoff_f,
        periods_required=1.0, conservative_factor=3.0,
    )
    print(f"  Recommended PEIS cutoff : {cutoff_f:.4f} Hz")

    # PEIS-only fit — ground-truth reference R2 from conventional measurement.
    # The arc curvature at PEIS lowest frequency constrains R2 extrapolation.
    print("[Full] PEIS-only 2-arc fit (reference) ...")
    eis_hf_s = eis[np.argsort(eis[:, 0])[::-1]]
    n_hf_r = max(3, min(10, len(eis_hf_s) // 5))
    r0_peis = max(float(np.median(eis_hf_s[:n_hf_r, 1])), 1.0)
    fit_peis_ref = fit_2arc(eis[:, 0], eis[:, 1], eis[:, 2], r0_fix=r0_peis)
    r2_ref = fit_peis_ref["R2 (Ohm)"]
    print(f"  PEIS ref: R0={fit_peis_ref['R0 (Ohm)']:.2f}  R1={fit_peis_ref['R1 (Ohm)']:.1f}  "
          f"R2={r2_ref:.1f}  cost={fit_peis_ref['Fit cost']:.4g}")

    # Full hybrid fit: all PEIS + recovered EIS below PEIS LF (for display).
    print("[Full] Hybrid 2-arc fit (full CP + full PEIS) ...")
    freq_full, rez_full, imz_full, fit_full = fit_combined(
        eis, filtered_f_full, rec_z_full, peis_f_min=None
    )
    # LF|Z| reference: |Z| at PEIS lowest frequency (fixed, measurable)
    peis_lf_idx = np.argmin(eis[:, 0])
    lf_ref = float(np.abs(eis[peis_lf_idx, 1] + 1j * eis[peis_lf_idx, 2]))
    print(f"  Full hybrid: R0={fit_full['R0 (Ohm)']:.2f}  R1={fit_full['R1 (Ohm)']:.1f}  "
          f"R2={fit_full['R2 (Ohm)']:.1f}  cost={fit_full['Fit cost']:.4g}")

    # ── Duration sweep ────────────────────────────────────────────
    # Each sweep point represents the OPTIMIZED scenario:
    #   - PEIS from cutoff_f to HF (not measured below cutoff_f)
    #   - Recovered EIS from shortened CP, upper bound = cutoff_f
    print(f"\n[Sweep] Optimized PEIS (>={cutoff_f:.4f} Hz) + shortened CP ...")
    sweep = []
    for dur in SWEEP_DURATIONS:
        if dur > full_duration + 5:
            continue
        cap = min(float(dur), full_duration)
        t_mask = (time_full - time_full[0]) <= cap
        t_s, v_s, c_s = time_full[t_mask], volt_full[t_mask], curr_full[t_mask]
        if len(t_s) < 50:
            continue
        try:
            rf_s, rz_s, _, _, _, _ = run_fft(
                t_s, v_s, c_s, eis,
                f_max_override=cutoff_f, min_periods=MIN_PERIODS_SWEEP,
            )
            if len(rf_s) == 0:
                continue
            _, _, _, fit_s = fit_combined(eis, rf_s, rz_s, peis_f_min=cutoff_f)
            lf_s = lf_z_mag(eis, rf_s, rz_s)
            r2_s = fit_s["R2 (Ohm)"]
            sweep.append({
                "duration_s": cap,
                "R0": fit_s["R0 (Ohm)"], "R1": fit_s["R1 (Ohm)"], "R2": r2_s,
                "cost": fit_s["Fit cost"], "lf_z": lf_s,
                "filtered_f": rf_s, "rec_z": rz_s,
                "fit": fit_s,
            })
            print(f"  {cap:5.0f}s  R0={fit_s['R0 (Ohm)']:.2f}  "
                  f"R1={fit_s['R1 (Ohm)']:.1f}  R2={r2_s:.1f}  "
                  f"LF|Z|={lf_s:.1f}  cost={fit_s['Fit cost']:.4g}")
        except Exception as e:
            print(f"  {cap:5.0f}s  ERROR: {e}")

    # ── Find convergence ──────────────────────────────────────────
    converged_dur = full_duration
    converged_entry = None
    if sweep and np.isfinite(r2_ref) and r2_ref > 0:
        for entry in sweep:
            r2_err  = abs(entry["R2"]    - r2_ref) / r2_ref
            lf_err  = abs(entry["lf_z"]  - lf_ref) / max(lf_ref, 1e-12)
            if r2_err <= CONVERGENCE_TOL and lf_err <= CONVERGENCE_TOL:
                converged_dur = entry["duration_s"]
                converged_entry = entry
                break
    if converged_entry is None and sweep:
        converged_entry = sweep[-1]
        converged_dur = sweep[-1]["duration_s"]

    saving = full_duration / max(converged_dur, 1)
    print(f"\n  Convergence at : {converged_dur:.1f} s  ({saving:.1f}x saving vs {full_duration:.0f} s)")
    print(f"  PEIS LF savings: {peis_lowest_f:.4f} Hz  ->  {cutoff_f:.4f} Hz  "
          f"(factor {cutoff_f/peis_lowest_f:.1f}x higher)")

    # Optimized fit (PEIS above cutoff_f + recovered EIS from converged CP)
    if converged_entry is not None:
        opt_rf  = converged_entry["filtered_f"]
        opt_rz  = converged_entry["rec_z"]
        fit_opt = converged_entry["fit"]
    else:
        opt_rf, opt_rz, fit_opt = filtered_f_full, rec_z_full, fit_full

    return {
        "label": label,
        "peis_lowest_f": peis_lowest_f,
        "cutoff_f": cutoff_f,
        "cp_info": cp_info,
        "full_duration_s": full_duration,
        "converged_duration_s": converged_dur,
        "eis": eis,
        "filtered_f_full": filtered_f_full, "rec_z_full": rec_z_full,
        "freq_full": freq_full, "rez_full": rez_full, "imz_full": imz_full,
        "fit_full": fit_full,
        "filtered_f_opt": opt_rf,  "rec_z_opt": opt_rz,
        "fit_opt": fit_opt,
        "sweep": sweep,
        "a_f": a_f, "a_z": a_z, "rec": rec,
        "r2_ref": r2_ref, "lf_ref": lf_ref,
    }


# ── plotting ─────────────────────────────────────────────────────────────────

def _fit_line(fit, f_lo, f_hi, n=800):
    """Model prediction from f_lo to f_hi using the 2-arc model."""
    p = [fit[k] for k in [
        "R0 (Ohm)", "R1 (Ohm)", "Q1 (F*s^(a-1))", "a1",
        "R2 (Ohm)", "Q2 (F*s^(a-1))", "a2",
    ]]
    ff = np.logspace(np.log10(max(f_lo, 1e-7)), np.log10(max(f_hi, f_lo * 10)), n)
    return ff, Z_2arc(p, ff)


def plot_sweep(results, out_dir):
    n = len(results)
    fig, axes = plt.subplots(2, n, figsize=(7 * n, 10))
    if n == 1:
        axes = axes.reshape(2, 1)

    for col, r in enumerate(results):
        sweep = r["sweep"]
        if not sweep:
            continue
        durs = np.array([s["duration_s"] for s in sweep])
        R0s  = np.array([s["R0"] for s in sweep])
        R1s  = np.array([s["R1"] for s in sweep])
        R2s  = np.array([s["R2"] for s in sweep])
        LFs  = np.array([s["lf_z"] for s in sweep])

        ax_r  = axes[0, col]
        ax_lf = axes[1, col]

        ax_r.plot(durs, R0s, "o-", color="#4e79a7", ms=6, label="R0")
        ax_r.plot(durs, R1s, "s-", color="#f28e2b", ms=6, label="R1")
        ax_r.plot(durs, R2s, "^-", color="#e15759", ms=6, label="R2")
        for val, col_ref in [
            (r["fit_full"]["R0 (Ohm)"], "#4e79a7"),
            (r["fit_full"]["R1 (Ohm)"], "#f28e2b"),
            (r["fit_full"]["R2 (Ohm)"], "#e15759"),
        ]:
            ax_r.axhline(val, color=col_ref, ls="--", lw=1)
        ax_r.axvline(r["converged_duration_s"], color="k", ls=":", lw=1.5,
                     label=f"Converged {r['converged_duration_s']:.0f} s")
        ax_r.set_yscale("log")
        ax_r.set_xlabel("CP duration (s)")
        ax_r.set_ylabel("Resistance (Ohm)")
        ax_r.set_title(f"{r['label']}  R0/R1/R2 vs CP duration\n"
                       f"PEIS cutoff={r['cutoff_f']:.4f} Hz  (full={r['peis_lowest_f']:.4f} Hz)")
        ax_r.legend(fontsize=8); ax_r.grid(alpha=0.3)

        ax_lf.plot(durs, LFs, "D-", color="#76b7b2", ms=6)
        ax_lf.axhline(r["lf_ref"], color="#76b7b2", ls="--", lw=1, label="Full-run LF|Z|")
        ax_lf.axvline(r["converged_duration_s"], color="k", ls=":", lw=1.5,
                      label=f"Converged {r['converged_duration_s']:.0f} s")
        ax_lf.set_yscale("log")
        ax_lf.set_xlabel("CP duration (s)")
        ax_lf.set_ylabel("LF |Z| (Ohm)")
        ax_lf.set_title(f"{r['label']}  Low-freq |Z| vs CP duration")
        ax_lf.legend(fontsize=8); ax_lf.grid(alpha=0.3)

    fig.suptitle(
        "R0 / R1 / R2 convergence vs CP duration  (optimized PEIS range)\n"
        "Dashed = full-run reference  |  Dotted = convergence point",
        fontsize=11,
    )
    fig.tight_layout()
    path = os.path.join(out_dir, "sweep_convergence.png")
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_comparison(results, out_dir):
    """
    3-row Nyquist / Bode for each sample.
    Model prediction extends to 1e-5 Hz so the full arc is visible.
    """
    n = len(results)
    fig, axes = plt.subplots(3, n, figsize=(7 * n, 14))
    if n == 1:
        axes = axes.reshape(3, 1)

    COL_FULL = "#1b9e77"
    COL_OPT  = "#d95f02"
    COL_PEIS = "0.78"

    for col, r in enumerate(results):
        ax_ny  = axes[0, col]
        ax_mag = axes[1, col]
        ax_ph  = axes[2, col]

        eis = r["eis"]
        eis_s = eis[np.argsort(eis[:, 0])]   # ascending for plotting

        fit_f = r["fit_full"]
        fit_o = r["fit_opt"]

        rf_full = r["filtered_f_full"]; rz_full = r["rec_z_full"]
        rf_opt  = r["filtered_f_opt"];  rz_opt  = r["rec_z_opt"]

        all_f = np.concatenate([eis[:, 0], rf_full])
        f_hi_plot = float(all_f.max())
        # Extend Nyquist arc well below measurement range to show full arc closure
        f_lo_arc  = 1e-5

        # ── Nyquist ──
        ax_ny.plot(eis_s[:, 1], -eis_s[:, 2], color=COL_PEIS, lw=2.5, label="PEIS ref")
        ax_ny.scatter(rz_full.real, -rz_full.imag, s=22, color=COL_FULL, zorder=4,
                      label=f"Full CP ({r['full_duration_s']:.0f} s)")
        ax_ny.scatter(rz_opt.real,  -rz_opt.imag,  s=22, marker="x", color=COL_OPT, zorder=5,
                      label=f"Opt CP ({r['converged_duration_s']:.0f} s)")

        # Full model arc extended to DC
        ff_arc, zf_arc = _fit_line(fit_f, f_lo_arc, f_hi_plot)
        ax_ny.plot(zf_arc.real, -zf_arc.imag, "--", color=COL_FULL, lw=1.5, label="Fit (full)")

        if np.isfinite(fit_o.get("R0 (Ohm)", np.nan)):
            ff2_arc, zf2_arc = _fit_line(fit_o, f_lo_arc, f_hi_plot)
            ax_ny.plot(zf2_arc.real, -zf2_arc.imag, "--", color=COL_OPT, lw=1.5, label="Fit (opt)")

        ax_ny.set_xlabel("Re(Z) / Ohm"); ax_ny.set_ylabel("-Im(Z) / Ohm")
        ax_ny.set_title(
            f"{r['label']}  Nyquist\n"
            f"R2_full={fit_f['R2 (Ohm)']:.0f} Ohm  "
            f"R2_opt={fit_o.get('R2 (Ohm)', float('nan')):.0f} Ohm",
            fontsize=9,
        )
        ax_ny.legend(fontsize=8, ncol=2); ax_ny.grid(alpha=0.25)

        # ── Bode Magnitude ──
        ax_mag.scatter(np.log10(rf_full), np.log10(np.abs(rz_full)), s=18, color=COL_FULL,
                       label=f"Full CP ({r['full_duration_s']:.0f} s)")
        ax_mag.scatter(np.log10(rf_opt), np.log10(np.abs(rz_opt)), s=18, marker="x", color=COL_OPT,
                       label=f"Opt CP ({r['converged_duration_s']:.0f} s)")
        ax_mag.plot(np.log10(eis_s[:, 0]),
                    np.log10(np.abs(eis_s[:, 1] + 1j * eis_s[:, 2])),
                    color=COL_PEIS, lw=2.5, label="PEIS ref")
        ff, zf = _fit_line(fit_f, f_lo_arc, f_hi_plot)
        ax_mag.plot(np.log10(ff), np.log10(np.abs(zf)), "--", color=COL_FULL, lw=1.5)
        if np.isfinite(fit_o.get("R0 (Ohm)", np.nan)):
            ff2, zf2 = _fit_line(fit_o, f_lo_arc, f_hi_plot)
            ax_mag.plot(np.log10(ff2), np.log10(np.abs(zf2)), "--", color=COL_OPT, lw=1.5)
        ax_mag.axvline(np.log10(r["cutoff_f"]), color="k", lw=1, ls=":",
                       label=f"PEIS cutoff {r['cutoff_f']:.3f} Hz")
        ax_mag.axvline(np.log10(r["peis_lowest_f"]), color="gray", lw=1, ls="--",
                       label=f"PEIS full LF {r['peis_lowest_f']:.3f} Hz")
        ax_mag.set_ylabel("log10(|Z| / Ohm)"); ax_mag.set_title("Bode Magnitude", fontsize=10)
        ax_mag.legend(fontsize=7); ax_mag.grid(alpha=0.25)

        # ── Bode Phase ──
        ax_ph.scatter(np.log10(rf_full), np.angle(rz_full, deg=True), s=18, color=COL_FULL)
        ax_ph.scatter(np.log10(rf_opt),  np.angle(rz_opt, deg=True),  s=18, marker="x", color=COL_OPT)
        ax_ph.plot(np.log10(eis_s[:, 0]),
                   np.angle(eis_s[:, 1] + 1j * eis_s[:, 2], deg=True),
                   color=COL_PEIS, lw=2.5)
        ff, zf = _fit_line(fit_f, f_lo_arc, f_hi_plot)
        ax_ph.plot(np.log10(ff), np.angle(zf, deg=True), "--", color=COL_FULL, lw=1.5)
        if np.isfinite(fit_o.get("R0 (Ohm)", np.nan)):
            ff2, zf2 = _fit_line(fit_o, f_lo_arc, f_hi_plot)
            ax_ph.plot(np.log10(ff2), np.angle(zf2, deg=True), "--", color=COL_OPT, lw=1.5)
        ax_ph.axvline(np.log10(r["cutoff_f"]), color="k", lw=1, ls=":")
        ax_ph.axvline(np.log10(r["peis_lowest_f"]), color="gray", lw=1, ls="--")
        ax_ph.set_xlabel("log10(f / Hz)"); ax_ph.set_ylabel("Phase / deg")
        ax_ph.set_title("Bode Phase", fontsize=10); ax_ph.grid(alpha=0.25)

    fig.suptitle(
        "Full vs Optimized measurement\n"
        "green = full measurement  |  orange = optimized (PEIS+CP both shortened)  |  dashed = 2-arc fit",
        fontsize=11,
    )
    fig.tight_layout()
    path = os.path.join(out_dir, "full_vs_optimized.png")
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def print_table(results):
    hdr = (f"{'Sample':<14} {'Scenario':<13} {'R0':>9} {'R1':>11} {'R2':>13} "
           f"{'Cost':>11} {'CP(s)':>7} {'PEIS LF(Hz)':>12}")
    print("\n" + "=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        ff = r["fit_full"]
        fo = r["fit_opt"]
        _pct = lambda a, b: (
            f"{abs(a-b)/max(abs(b),1e-12)*100:.1f}%" if np.isfinite(a) and np.isfinite(b) else "N/A"
        )
        print(f"{r['label']:<14} {'Full':<13} "
              f"{ff['R0 (Ohm)']:>9.1f} {ff['R1 (Ohm)']:>11.1f} {ff['R2 (Ohm)']:>13.1f} "
              f"{ff['Fit cost']:>11.4g} {r['full_duration_s']:>7.1f} {r['peis_lowest_f']:>12.4f}")
        if fo and np.isfinite(fo.get("R2 (Ohm)", np.nan)):
            print(f"{'':14} {'Optimized':<13} "
                  f"{fo['R0 (Ohm)']:>9.1f} {fo['R1 (Ohm)']:>11.1f} {fo['R2 (Ohm)']:>13.1f} "
                  f"{fo['Fit cost']:>11.4g} {r['converged_duration_s']:>7.1f} {r['cutoff_f']:>12.4f}")
            print(f"{'':14} {'Diff %':<13} "
                  f"{_pct(fo['R0 (Ohm)'],ff['R0 (Ohm)']):>9} "
                  f"{_pct(fo['R1 (Ohm)'],ff['R1 (Ohm)']):>11} "
                  f"{_pct(fo['R2 (Ohm)'],ff['R2 (Ohm)']):>13}")
        print()
    print("=" * len(hdr))


def main():
    all_results = []
    for ds in DATASETS:
        try:
            r = analyze_one(ds["label"], ds["ca_path"], ds["eis_path"])
            all_results.append(r)
        except Exception as e:
            print(f"[ERROR] {ds['label']}: {e}")
            import traceback; traceback.print_exc()

    print_table(all_results)

    p1 = plot_sweep(all_results, OUT_DIR)
    p2 = plot_comparison(all_results, OUT_DIR)
    print(f"\nSaved:\n  {p1}\n  {p2}")

    # JSON summary
    summary = []
    for r in all_results:
        fo = r["fit_opt"] or {}
        summary.append({
            "label": r["label"],
            "peis_full_lf_hz": r["peis_lowest_f"],
            "peis_optimized_cutoff_hz": r["cutoff_f"],
            "full_cp_duration_s": r["full_duration_s"],
            "converged_cp_duration_s": r["converged_duration_s"],
            "cp_time_saving_factor": round(r["full_duration_s"] / max(r["converged_duration_s"], 1), 2),
            "peis_lf_saving_factor": round(r["cutoff_f"] / max(r["peis_lowest_f"], 1e-12), 2),
            "fit_full": {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                         for k, v in r["fit_full"].items()},
            "fit_optimized": {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                              for k, v in fo.items() if not isinstance(v, np.ndarray)},
            "sweep": [
                {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v)
                 for k, v in s.items() if not isinstance(v, np.ndarray)}
                for s in r["sweep"]
            ],
        })
    json_path = os.path.join(OUT_DIR, "full_vs_optimized_summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  {json_path}")


if __name__ == "__main__":
    main()
