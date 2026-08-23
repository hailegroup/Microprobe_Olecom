"""
Flatten every row folder's full_arc_summary.json (fitted equivalent-circuit
results, written by tools/make_olecom_full_arc_from_run.py) under a results
tree into one combined CSV.

Joins each row's rrqrq_components fit (Rs, and per-RQ-element R/Q/alpha/
f_char/resistance-fraction) with that electrode's condition metadata --
temperature, gas flow rates, bias, electrode number, diameter -- pulled
from the curated sibling JSON gui.py::_curate_row_result_artifacts writes
one directory up from each row folder. Also computes, per RQ element, the
CPE-equivalent capacitance (Brug formula: C = Q^(1/n) * R^((1-n)/n)), the
"reaction resistance" (sum of RQ-element resistances, excluding Rs), and
area-normalized versions (resistance * area in cm^2 -> ohm*cm^2;
capacitance / area in cm^2 -> F/cm^2). Also carries each fitted resistance's
1-sigma standard error (*_err_ohm columns, from the fit's linearized
Jacobian/covariance -- see Analysis_Convert_CP_to_EIS/EIS_Fitting.py's
param_std_errors) through the same area normalization; capacitance itself
has no propagated error since that would need the R/Q covariance term, not
just each parameter's own variance.

Usage:

    python tools/extract_full_arc_results_to_csv.py <results_root> [-o output.csv]

results_root is searched recursively, so it can point at a single run's
top-level folder or a parent directory containing several runs. The output
CSV defaults to full_arc_results.csv written directly inside results_root
(pass -o to write it somewhere else instead).

Self-contained -- no comtypes/galvani dependency, runs on any machine.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

ROW_FOLDER_RE = re.compile(r"^row\d+_(.+)_\d{8}_\d{6}$")
ELECTRODE_RE = re.compile(r"(?:^|_)E(\d+)(?:_|$)")
RQ_R_KEY_RE = re.compile(r"^RQ(\d+)_R_ohm$")

# Folder-name fallback parsing (used only when no curated sibling JSON is
# found), inverting gui.py::_label_number's encoding: decimal point -> 'p',
# trailing zeros stripped, sign kept for voltage.
FALLBACK_T_RE = re.compile(r"(?:^|_)T(-?\d+p?\d*)")
FALLBACK_GA_RE = re.compile(r"_GA(-?\d+p?\d*)")
FALLBACK_GB_RE = re.compile(r"_GB(-?\d+p?\d*)")
FALLBACK_V_RE = re.compile(r"_V([+-]\d+p?\d*)")

COLUMNS = [
    "row_folder", "label", "electrode_number",
    "temperature_C", "gas_a_setting", "gas_b_setting", "bias_V_dc",
    "electrode_diameter_um", "electrode_area_cm2",
    "fit_model", "fit_score", "fit_cost",
    "fit_condition_number", "fit_errors_ill_conditioned", "fit_relative_error", "full_arc_variant",
    "mc_n_iterations", "mc_n_success", "mc_relative_error", "mc_n_freq_bins",
    "Rs_ohm", "Rs_ohm_cm2", "Rs_err_ohm", "Rs_err_ohm_cm2", "Rs_mc_err_ohm", "Rs_mc_err_ohm_cm2",
    "RQ1_R_ohm", "RQ1_R_ohm_cm2", "RQ1_R_err_ohm", "RQ1_R_err_ohm_cm2",
    "RQ1_R_mc_err_ohm", "RQ1_R_mc_err_ohm_cm2",
    "RQ1_Q_F_s_alpha_minus_1", "RQ1_Q_err_F_s_alpha_minus_1", "RQ1_Q_mc_err_F_s_alpha_minus_1",
    "RQ1_alpha", "RQ1_alpha_err", "RQ1_alpha_mc_err",
    "RQ1_fchar_Hz", "RQ1_resistance_fraction", "RQ1_C_F", "RQ1_C_F_cm2",
    "RQ1_C_mc_err_F", "RQ1_C_mc_err_F_cm2",
    "RQ2_R_ohm", "RQ2_R_ohm_cm2", "RQ2_R_err_ohm", "RQ2_R_err_ohm_cm2",
    "RQ2_R_mc_err_ohm", "RQ2_R_mc_err_ohm_cm2",
    "RQ2_Q_F_s_alpha_minus_1", "RQ2_Q_err_F_s_alpha_minus_1", "RQ2_Q_mc_err_F_s_alpha_minus_1",
    "RQ2_alpha", "RQ2_alpha_err", "RQ2_alpha_mc_err",
    "RQ2_fchar_Hz", "RQ2_resistance_fraction", "RQ2_C_F", "RQ2_C_F_cm2",
    "RQ2_C_mc_err_F", "RQ2_C_mc_err_F_cm2",
    "R1+R2_ohm", "R1+R2_ohm_cm2", "R1+R2_err_ohm", "R1+R2_err_ohm_cm2",
    "R1+R2_mc_err_ohm", "R1+R2_mc_err_ohm_cm2",
]

_curated_cache: dict[Path, list[dict]] = {}


def _decode_label_number(text: str) -> float:
    return float(text.replace("p", "."))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _curated_jsons_in(parent: Path) -> list[dict]:
    cached = _curated_cache.get(parent)
    if cached is not None:
        return cached
    found = []
    for candidate in parent.glob("*.json"):
        data = _load_json(candidate)
        if data:
            found.append(data)
    _curated_cache[parent] = found
    return found


def _find_curated_json(row_dir: Path, label: str) -> dict | None:
    for data in _curated_jsons_in(row_dir.parent):
        candidate_label = data.get("label") or (data.get("row_settings") or {}).get("Label")
        if candidate_label == label:
            return data
    return None


def _condition_from_folder_name(label: str) -> dict:
    out = {"temperature_C": None, "gas_a_setting": None, "gas_b_setting": None, "bias_V_dc": None}
    m = FALLBACK_T_RE.search(label)
    if m:
        out["temperature_C"] = _decode_label_number(m.group(1))
    m = FALLBACK_GA_RE.search(label)
    if m:
        out["gas_a_setting"] = _decode_label_number(m.group(1))
    m = FALLBACK_GB_RE.search(label)
    if m:
        out["gas_b_setting"] = _decode_label_number(m.group(1))
    m = FALLBACK_V_RE.search(label)
    if m:
        out["bias_V_dc"] = _decode_label_number(m.group(1))
    return out


def _cpe_capacitance(q, r, alpha):
    """Brug formula: C = Q^(1/n) * R^((1-n)/n).

    Well-behaved as alpha -> 1 (the ideal-capacitor limit): (1-n)/n -> 0,
    so R^0 = 1 and C -> Q exactly, no singularity.
    """
    if q is None or r is None or alpha is None:
        return None
    try:
        q = float(q)
        r = float(r)
        alpha = float(alpha)
    except (TypeError, ValueError):
        return None
    if alpha == 0 or not (math.isfinite(q) and math.isfinite(r) and math.isfinite(alpha)):
        return None
    if q < 0 or r < 0:
        return None
    try:
        return (q ** (1.0 / alpha)) * (r ** ((1.0 - alpha) / alpha))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def _electrode_area_cm2(diameter_um):
    if diameter_um is None:
        return None
    try:
        diameter_um = float(diameter_um)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(diameter_um) or diameter_um <= 0:
        return None
    radius_cm = (diameter_um * 1e-4) / 2.0
    return math.pi * radius_cm ** 2


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _generic_rq_components(fit: dict) -> dict | None:
    """Fallback per-model RQ-element breakdown built directly from the raw
    fit-result dict's R{i}/Q{i}/a{i} keys -- the naming convention shared by
    RQRQ/RRQRQ/RQRQRQ (Analysis_Convert_CP_to_EIS/EIS_Fitting.py's
    FIT_RESULT_KEYS) and by the single-arc RRQ fit
    (make_olecom_full_arc_from_run.py::_fit_rrq). Used whenever fit_model !=
    "RRQRQ", since full_arc_summary.json's own rrqrq_components is only
    populated for that one model. Keys are named RQ1_*/RQ2_*/... (1-indexed,
    however many elements the model actually has) matching
    rrqrq_components' convention exactly, so the rest of extract_row's
    capacitance/area-normalization logic needs no per-model special-casing.

    Also picks up "<key> Monte Carlo error" fields (monte_carlo_param_errors,
    make_olecom_full_arc_from_run.py) into "RQ{i}_*_mc_err_*" the same way it
    does for the Jacobian-based "<key> error" fields -- raw per-parameter MC
    error only, since monte_carlo_param_errors' RRQRQ-only component rollup
    (fchar/resistance_fraction/capacitance re-derived per bootstrap sample)
    has no generic equivalent here.
    """
    if not fit:
        return None
    elements = []
    idx = 0
    while True:
        r = fit.get(f"R{idx} (Ohm)")
        q = fit.get(f"Q{idx} (F·s^(a-1))")
        a = fit.get(f"a{idx}")
        # A model with fewer than 3 RQ branches (RQRQ, RRQ) still carries
        # R2/Q2/a2 in the dict as NaN, not absent -- _result_from_model_params
        # pre-fills every FIT_RESULT_KEYS slot with np.nan and only overwrites
        # the branches the model actually has. `is None` alone doesn't catch
        # that, so a phantom all-NaN branch used to slip through and poison
        # downstream sums (e.g. R1+R2_ohm) to NaN.
        if r is None or q is None or a is None:
            break
        r_f, q_f, a_f = _as_float(r), _as_float(q), _as_float(a)
        if r_f is None or q_f is None or a_f is None:
            break
        if not (math.isfinite(r_f) and math.isfinite(q_f) and math.isfinite(a_f)):
            break
        elements.append(
            (
                r, q, a, fit.get(f"RQ{idx} fchar (Hz)"),
                fit.get(f"R{idx} (Ohm) error"), fit.get(f"Q{idx} (F·s^(a-1)) error"), fit.get(f"a{idx} error"),
                fit.get(f"R{idx} (Ohm) Monte Carlo error"),
                fit.get(f"Q{idx} (F·s^(a-1)) Monte Carlo error"),
                fit.get(f"a{idx} Monte Carlo error"),
            )
        )
        idx += 1
    if not elements:
        return None

    out = {"interpretation": "generic per-model RQ element breakdown (non-RRQRQ fit_model)"}
    rs = fit.get("Rs (Ohm)")
    if rs is not None:
        out["Rs_ohm"] = _as_float(rs)
        rs_err = fit.get("Rs (Ohm) error")
        if rs_err is not None:
            out["Rs_err_ohm"] = _as_float(rs_err)
        rs_mc_err = fit.get("Rs (Ohm) Monte Carlo error")
        if rs_mc_err is not None:
            out["Rs_mc_err_ohm"] = _as_float(rs_mc_err)

    total_r = 0.0
    have_total = False
    # Independent-sum propagation for the combined error, same caveat as
    # make_olecom_full_arc_from_run.py::_rrqrq_component_summary -- cross-
    # branch covariance isn't tracked past the diagonal.
    total_r_err_sq = 0.0
    have_total_err = True
    total_r_mc_err_sq = 0.0
    have_total_mc_err = True
    for pos, (r, q, a, fchar, r_err, q_err, a_err, r_mc_err, q_mc_err, a_mc_err) in enumerate(elements, start=1):
        r_val = _as_float(r)
        out[f"RQ{pos}_R_ohm"] = r_val
        out[f"RQ{pos}_Q_F_s_alpha_minus_1"] = _as_float(q)
        out[f"RQ{pos}_alpha"] = _as_float(a)
        out[f"RQ{pos}_fchar_Hz"] = _as_float(fchar)
        r_err_val = _as_float(r_err)
        out[f"RQ{pos}_R_err_ohm"] = r_err_val
        out[f"RQ{pos}_Q_err_F_s_alpha_minus_1"] = _as_float(q_err)
        out[f"RQ{pos}_alpha_err"] = _as_float(a_err)
        r_mc_err_val = _as_float(r_mc_err)
        out[f"RQ{pos}_R_mc_err_ohm"] = r_mc_err_val
        out[f"RQ{pos}_Q_mc_err_F_s_alpha_minus_1"] = _as_float(q_mc_err)
        out[f"RQ{pos}_alpha_mc_err"] = _as_float(a_mc_err)
        if r_val is not None:
            total_r += r_val
            have_total = True
        if r_err_val is not None:
            total_r_err_sq += r_err_val ** 2
        else:
            have_total_err = False
        if r_mc_err_val is not None:
            total_r_mc_err_sq += r_mc_err_val ** 2
        else:
            have_total_mc_err = False
    if have_total:
        for pos in range(1, len(elements) + 1):
            r_val = out.get(f"RQ{pos}_R_ohm")
            out[f"RQ{pos}_resistance_fraction"] = (r_val / total_r) if (r_val is not None and total_r) else None
        out["R1+R2_ohm"] = total_r
        if have_total_err:
            out["R1+R2_err_ohm"] = total_r_err_sq ** 0.5
        if have_total_mc_err:
            out["R1+R2_mc_err_ohm"] = total_r_mc_err_sq ** 0.5
    return out


def extract_row(full_arc_summary_path: Path, warnings: list) -> dict:
    row_dir = full_arc_summary_path.parent
    row_folder = row_dir.name
    m = ROW_FOLDER_RE.match(row_folder)
    label = m.group(1) if m else row_folder
    m_e = ELECTRODE_RE.search(label)
    electrode_number = int(m_e.group(1)) if m_e else None
    if electrode_number is None:
        warnings.append(f"{row_folder}: could not parse electrode number from label {label!r}")

    diameter_um = None
    curated = _find_curated_json(row_dir, label)
    if curated is not None:
        row_settings = curated.get("row_settings") or {}
        temperature_C = _as_float(row_settings.get("Temperature_C"))
        gas_a_setting = _as_float(row_settings.get("GasA_setting"))
        gas_b_setting = _as_float(row_settings.get("GasB_setting"))
        bias_V_dc = _as_float(row_settings.get("V_dc"))
        diameter_um = curated.get("electrode_diameter_um")
    else:
        warnings.append(
            f"{row_folder}: no matching curated JSON found in {row_dir.parent}; "
            "falling back to folder-name parsing, diameter unavailable"
        )
        cond = _condition_from_folder_name(label)
        temperature_C = cond["temperature_C"]
        gas_a_setting = cond["gas_a_setting"]
        gas_b_setting = cond["gas_b_setting"]
        bias_V_dc = cond["bias_V_dc"]

    area_cm2 = _electrode_area_cm2(diameter_um)

    summary = _load_json(full_arc_summary_path)
    rrqrq = summary.get("rrqrq_components") or _generic_rq_components(summary.get("fit") or {})

    row = {
        "row_folder": row_folder,
        "label": label,
        "electrode_number": electrode_number,
        "temperature_C": temperature_C,
        "gas_a_setting": gas_a_setting,
        "gas_b_setting": gas_b_setting,
        "bias_V_dc": bias_V_dc,
        "electrode_diameter_um": diameter_um,
        "electrode_area_cm2": area_cm2,
        "fit_model": summary.get("fit_model"),
        "fit_score": summary.get("fit_score"),
        "fit_cost": summary.get("fit_cost"),
        "fit_condition_number": summary.get("fit_condition_number"),
        "fit_errors_ill_conditioned": summary.get("fit_errors_ill_conditioned"),
        # Blank (not defaulted to True) for JSON files written before this
        # option existed -- they were always fit with relative weighting
        # since no alternative was available, but left honestly blank
        # rather than assumed, since this field records what's actually on
        # disk, not what's historically inferable.
        "fit_relative_error": summary.get("fit_relative_error"),
        "full_arc_variant": summary.get("recommended_full_arc_variant"),
        "mc_n_iterations": summary.get("mc_n_iterations"),
        "mc_n_success": summary.get("mc_n_success"),
        "mc_relative_error": summary.get("mc_relative_error"),
        "mc_n_freq_bins": summary.get("mc_n_freq_bins"),
    }
    for col in COLUMNS:
        row.setdefault(col, None)

    if not rrqrq:
        warnings.append(
            f"{row_folder}: no fit component breakdown available "
            f"(fit_model={summary.get('fit_model')!r}, fit may have failed); "
            "RQ-derived columns left blank"
        )
        return row

    def _scaled(value):
        if value is None or area_cm2 is None:
            return None
        v = _as_float(value)
        return v * area_cm2 if v is not None else None

    row["Rs_ohm"] = rrqrq.get("Rs_ohm")
    row["Rs_ohm_cm2"] = _scaled(row["Rs_ohm"])
    row["Rs_err_ohm"] = rrqrq.get("Rs_err_ohm")
    row["Rs_err_ohm_cm2"] = _scaled(row["Rs_err_ohm"])
    row["Rs_mc_err_ohm"] = rrqrq.get("Rs_mc_err_ohm")
    row["Rs_mc_err_ohm_cm2"] = _scaled(row["Rs_mc_err_ohm"])

    rq_positions = sorted({int(m.group(1)) for k in rrqrq if (m := RQ_R_KEY_RE.match(k))})
    for i in rq_positions:
        r_key, q_key, a_key, f_key, frac_key = (
            f"RQ{i}_R_ohm", f"RQ{i}_Q_F_s_alpha_minus_1", f"RQ{i}_alpha",
            f"RQ{i}_fchar_Hz", f"RQ{i}_resistance_fraction",
        )
        r_err_key, q_err_key, a_err_key = (
            f"RQ{i}_R_err_ohm", f"RQ{i}_Q_err_F_s_alpha_minus_1", f"RQ{i}_alpha_err",
        )
        r_mc_err_key, q_mc_err_key, a_mc_err_key = (
            f"RQ{i}_R_mc_err_ohm", f"RQ{i}_Q_mc_err_F_s_alpha_minus_1", f"RQ{i}_alpha_mc_err",
        )
        r_val = rrqrq.get(r_key)
        q_val = rrqrq.get(q_key)
        a_val = rrqrq.get(a_key)
        row[r_key] = r_val
        row[f"RQ{i}_R_ohm_cm2"] = _scaled(r_val)
        row[q_key] = q_val
        row[a_key] = a_val
        row[f_key] = rrqrq.get(f_key)
        row[frac_key] = rrqrq.get(frac_key)
        row[r_err_key] = rrqrq.get(r_err_key)
        row[f"RQ{i}_R_err_ohm_cm2"] = _scaled(row[r_err_key])
        row[q_err_key] = rrqrq.get(q_err_key)
        row[a_err_key] = rrqrq.get(a_err_key)
        row[r_mc_err_key] = rrqrq.get(r_mc_err_key)
        row[f"RQ{i}_R_mc_err_ohm_cm2"] = _scaled(row[r_mc_err_key])
        row[q_mc_err_key] = rrqrq.get(q_mc_err_key)
        row[a_mc_err_key] = rrqrq.get(a_mc_err_key)

        c_val = _cpe_capacitance(q_val, r_val, a_val)
        if c_val is None and q_val is not None and r_val is not None and a_val is not None:
            warnings.append(
                f"{row_folder}: could not compute RQ{i} capacitance from "
                f"Q={q_val}, R={r_val}, alpha={a_val}"
            )
        row[f"RQ{i}_C_F"] = c_val
        row[f"RQ{i}_C_F_cm2"] = (c_val / area_cm2) if (c_val is not None and area_cm2) else None

        # Unlike Rs/RQ_R (which scale as ohm*cm2, multiplied by area),
        # capacitance density is F/cm2 -- divided by area, same convention
        # as RQ{i}_C_F_cm2 above. This is the one place MC has a genuine
        # correctness edge over the Jacobian error: capacitance is a
        # nonlinear function of R, Q, and alpha together, and
        # monte_carlo_param_errors re-derives it fresh per bootstrap sample
        # rather than needing a propagation-of-errors formula through their
        # covariance -- which is why no Jacobian-based "*_err_F" column
        # exists for capacitance at all, only this MC one.
        c_mc_err_key = f"RQ{i}_C_mc_err_F"
        c_mc_err_val = rrqrq.get(c_mc_err_key)
        row[c_mc_err_key] = c_mc_err_val
        row[f"RQ{i}_C_mc_err_F_cm2"] = (
            (_as_float(c_mc_err_val) / area_cm2) if (c_mc_err_val is not None and area_cm2) else None
        )

    # Sum of every RQ element's own resistance (RQ1_R_ohm + RQ2_R_ohm [+ ...]),
    # excluding Rs -- computed fresh from the individual RQ{i}_R_ohm values
    # rather than trusting a separately-stored total, so this reads correctly
    # regardless of whether the source full_arc_summary.json is old enough to
    # still use the legacy "polarization_R_total_ohm" key name.
    r1_plus_r2 = 0.0
    found_any = False
    for key, value in rrqrq.items():
        if RQ_R_KEY_RE.match(key) and value is not None:
            v = _as_float(value)
            if v is not None:
                r1_plus_r2 += v
                found_any = True
    row["R1+R2_ohm"] = r1_plus_r2 if found_any else None
    row["R1+R2_ohm_cm2"] = _scaled(row["R1+R2_ohm"])

    # Independent-sum propagation (sqrt of sum of squares), same caveat as
    # _rrqrq_component_summary: only requires every RQ{i}_R_err_ohm present,
    # not a pre-stored total, so this also works for legacy JSON.
    r1_plus_r2_err_sq = 0.0
    found_all_err = found_any
    for i in rq_positions:
        err_val = _as_float(row.get(f"RQ{i}_R_err_ohm"))
        if err_val is None:
            found_all_err = False
            break
        r1_plus_r2_err_sq += err_val ** 2
    row["R1+R2_err_ohm"] = (r1_plus_r2_err_sq ** 0.5) if found_all_err else None
    row["R1+R2_err_ohm_cm2"] = _scaled(row["R1+R2_err_ohm"])

    # Unlike R1+R2_err_ohm above, this is read directly rather than summed
    # from the individual RQ{i}_R_mc_err_ohm values: monte_carlo_param_errors
    # already computes it from the actual joint bootstrap distribution (the
    # std of R1+R2 re-derived per refit), which correctly reflects any
    # correlation between the branches -- an independent sum-of-squares would
    # throw that away.
    row["R1+R2_mc_err_ohm"] = rrqrq.get("R1+R2_mc_err_ohm")
    row["R1+R2_mc_err_ohm_cm2"] = _scaled(row["R1+R2_mc_err_ohm"])

    return row


def extract_all(results_root: Path) -> tuple[pd.DataFrame, list[str]]:
    _curated_cache.clear()  # per-invocation cache only; avoid stale cross-call reuse
    summary_paths = sorted(results_root.rglob("full_arc_summary.json"))
    warnings: list[str] = []
    rows = [extract_row(p, warnings) for p in summary_paths]
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_root", help="Run folder or parent directory to search recursively.")
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output CSV path. Defaults to full_arc_results.csv inside results_root.",
    )
    args = parser.parse_args()

    root = Path(args.results_root)
    if not root.exists():
        print(f"results_root does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else (root / "full_arc_results.csv")

    df, warnings = extract_all(root)
    if df.empty:
        print(f"No full_arc_summary.json files found under {root}", file=sys.stderr)
        sys.exit(1)

    df.to_csv(output_path, index=False)

    n_total = len(df)
    n_curated = int(df["electrode_diameter_um"].notna().sum())
    n_rrqrq = int(df["Rs_ohm"].notna().sum())
    fit_model_counts = df["fit_model"].value_counts(dropna=False).to_dict()

    print(f"Wrote {n_total} row(s) to {output_path}")
    print(f"  {n_curated}/{n_total} matched a curated JSON (condition metadata + diameter)")
    print(f"  {n_rrqrq}/{n_total} had rrqrq_components populated")
    print(f"  fit_model breakdown: {fit_model_counts}")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
