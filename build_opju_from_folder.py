# -*- coding: utf-8 -*-
"""
Build OriginLab .opju projects from an EXISTING analysis output folder.

This is the fast path: it reuses the ``origin_export`` CSVs that
``analysis_gui.py`` / ``recover_and_export`` already wrote, so it does NOT
re-run the CP->EIS analysis. For every sample folder that contains an
``origin_export/`` subfolder it builds a native Origin project with editable
Nyquist and Bode graphs (Reference PEIS, Recovered CP->EIS, and — when a
``fit_result.json`` is present — the RQRQRQ fit curve).

Usage
-----
    python build_opju_from_folder.py <folder> [--layout electrode|master|per-row]

``<folder>`` is a whole dataset folder (e.g. "20260628 Gradient LSCF 500").

Layouts
-------
* ``electrode`` (default): ONE .opju **per electrode**, where an electrode is a
  unique (X, Y, Z) location on the film (parsed from the row-folder names, e.g.
  ``..._X2p4_Y13p2_Z-1p9_...``). You are asked for a generic dataset name (e.g.
  "LSCF 500") and each file is named ``<name> electrode <N>.opju``; an optional
  note plus the electrode coordinates are written to the project's Notes window.
  Inside each .opju there is a Project Explorer folder per gas condition (folder
  names like ``[1] 0.2 pO2``), and within it a workbook per bias point named for
  the voltage token (``V+0p21``) with the full source-folder name in the
  workbook's Comments — carrying the same sheets and 7 graphs as ``master``.
* ``master``: ONE .opju for the whole dataset (workbook per row + 7 graphs per
  condition folder).
* ``per-row``: one .opju per sample, mirrored into a folder tree.

Requires OriginLab Origin/OriginPro 2021b+ installed locally (see
export_originpro.py). Discovery/assembly work without Origin; only the final
.opju write needs it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
ANALYSIS_DIR = BASE_DIR / "Analysis_Convert_CP_to_EIS"

_CSV_SUFFIXES = ("_reference_peis", "_recovered_eis", "_raw_dc")

# --- Row-folder name parsing (per-electrode layout) -------------------------
# A row folder looks like:
#   row001_ADAPT_T500_GA85p65_GB0_E1_X2p4_Y13p2_Z-1p9_V+0p21_20260628_205826
# The electrode is identified by its (X, Y, Z) coordinates on the film, and the
# measurement point within an electrode by its bias voltage token (V+0p21).
_COORD_RE = re.compile(r"(X-?[0-9p]+_Y-?[0-9p]+_Z-?[0-9p]+)", re.IGNORECASE)
_VOLT_RE = re.compile(r"_(V[+_-][0-9p]+)(?=_|$)", re.IGNORECASE)
_ROW_RE = re.compile(r"(row\d+)", re.IGNORECASE)


def _parse_coords(folder_name):
    """Return the raw coordinate token (e.g. 'X2p4_Y13p2_Z-1p9') or None."""
    m = _COORD_RE.search(str(folder_name))
    return m.group(1) if m else None


def _coords_readable(token):
    """'X2p4_Y13p2_Z-1p9' -> 'X=2.4, Y=13.2, Z=-1.9' ('p' is the decimal point)."""
    out = []
    for part in str(token).split("_"):
        if not part:
            continue
        axis, value = part[0], part[1:].replace("p", ".")
        out.append(f"{axis}={value}")
    return ", ".join(out)


def _parse_voltage(folder_name):
    """Return the normalised bias token (e.g. 'V+0p21') or None.

    The reanalysis folders use an underscore for the sign ('V_0p21'); that is a
    positive bias, so it is normalised to '+'.
    """
    m = _VOLT_RE.search(str(folder_name))
    if not m:
        return None
    tok = m.group(1)
    sign = "+" if tok[1] == "_" else tok[1]
    return f"V{sign}{tok[2:]}"


def _row_token(folder_name):
    """Short 'row001' fallback label for a row folder."""
    m = _ROW_RE.match(str(folder_name))
    return m.group(1) if m else str(folder_name).split("_")[0]


def _gas_sort_key(name):
    """Order gas-condition folders naturally: [1], [2], ... [7-1], [7-2], then
    anything without a leading bracket index (e.g. 'reanalysis') last."""
    m = re.match(r"\[(\d+)(?:-(\d+))?\]", str(name))
    if m:
        return (0, int(m.group(1)), int(m.group(2) or 0), str(name).lower())
    return (1, 0, 0, str(name).lower())


def _safe_filename(name):
    """Sanitise a string for use as a Windows filename (no extension)."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip()
    return cleaned or "dataset"


def _read_eis_csv(path, name):
    df = pd.read_csv(path)
    return {
        "name": name,
        "freq": df["freq_Hz"].to_numpy(dtype=float),
        "re": df["ReZ_Ohm"].to_numpy(dtype=float),
        "im": df["ImZ_Ohm"].to_numpy(dtype=float),  # conventional Im(Z)
    }


# --- Production (deployed OLECOM) origin_export format ----------------------
# Columns (BOM-prefixed): measured_peis.csv -> freq_Hz, ReZ_ohm, negImZ_ohm
#                         full_arc.csv      -> freq_Hz, ReZ_ohm, ImZ_ohm, ...
#                         nyquist.csv       -> ReZ_ohm, negImZ_ohm, freq_Hz
#                         ca_fft_input.csv  -> time_s, voltage_V, current_A
def _read_prod_csv(path):
    return pd.read_csv(path, encoding="utf-8-sig").apply(pd.to_numeric, errors="coerce").dropna()


def _prod_reference(path):
    a = _read_prod_csv(path).iloc[:, :3].to_numpy(dtype=float)  # freq, ReZ, negImZ
    return {"name": "Reference PEIS", "freq": a[:, 0], "re": a[:, 1], "im": -a[:, 2]}


def _prod_recovered_full_arc(path):
    a = _read_prod_csv(path).iloc[:, :3].to_numpy(dtype=float)  # freq, ReZ, ImZ
    return {"name": "Recovered (CP->EIS)", "freq": a[:, 0], "re": a[:, 1], "im": a[:, 2]}


def _prod_recovered_nyquist(path):
    a = _read_prod_csv(path).iloc[:, :3].to_numpy(dtype=float)  # ReZ, negImZ, freq
    return {"name": "Recovered (CP->EIS)", "freq": a[:, 2], "re": a[:, 0], "im": -a[:, 1]}


def _prod_cp(path):
    return _read_prod_csv(path).iloc[:, :3].to_numpy(dtype=float)  # time, V, I


def _collect_production(oe, sample_dir):
    """Assemble datasets from a production origin_export folder, or None if it
    isn't one. Returns (sample_name, datasets, cp_data)."""
    meas = next(iter(oe.glob("*_measured_peis.csv")), None)
    arc = next(iter(oe.glob("*_full_arc.csv")), None)
    nyq = next(iter(oe.glob("*_nyquist.csv")), None)
    cafft = next(iter(oe.glob("*_ca_fft_input.csv")), None)
    if not any((meas, arc, nyq, cafft)):
        return None

    datasets = []
    if meas is not None:
        datasets.append(_prod_reference(meas))
    if arc is not None:
        datasets.append(_prod_recovered_full_arc(arc))
    elif nyq is not None:
        datasets.append(_prod_recovered_nyquist(nyq))
    if not datasets:
        return None

    cp_data = _prod_cp(cafft) if cafft is not None else None
    return sample_dir.name, datasets, cp_data


# --- Grouped master layout: per-row 3-sheet workbooks, grouped by condition ---
def _csv_col(df, name, idx):
    if name in df.columns:
        return df[name].to_numpy(dtype=float)
    return df.iloc[:, idx].to_numpy(dtype=float)


def _eis_frame(reZ, negImZ, freq):
    """Sheet columns A..E: Re_Z, -Im_Z, frequency, |Z|, Phase.

    The complex impedance is Re_Z + j*(-(-Im_Z)) = Re_Z + j*ImZ where ImZ = -negImZ,
    so |Z| = sqrt(Re_Z^2 + negImZ^2) and Phase = atan2(-negImZ, Re_Z).
    """
    reZ = np.asarray(reZ, dtype=float)
    negImZ = np.asarray(negImZ, dtype=float)
    freq = np.asarray(freq, dtype=float)
    absZ = np.sqrt(reZ ** 2 + negImZ ** 2)
    phase = np.degrees(np.arctan2(-negImZ, reZ))
    return pd.DataFrame({
        "Re_Z": reZ, "-Im_Z": negImZ, "frequency": freq, "|Z|": absZ, "Phase": phase,
    })


def _recovered_only_mask(rec_freq, meas_freq, rtol=1e-3):
    """Boolean mask of recovered-arc points that are NOT measured PEIS points.

    full_arc.csv merges FFT-recovered (low freq) with the measured PEIS
    (high freq). A recovered point is treated as "measured" (and dropped) when
    its frequency is within rtol of any measured-PEIS frequency.
    """
    rec = np.asarray(rec_freq, dtype=float)
    meas = np.asarray(meas_freq, dtype=float)
    if rec.size == 0 or meas.size == 0:
        return np.ones(rec.size, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.abs(rec[:, None] - meas[None, :]) / np.abs(rec[:, None])
    nearest = np.nanmin(rel, axis=1)
    return nearest >= rtol


def _read_txt(path):
    """Read a headered numeric text file (tab- or space-separated)."""
    return np.genfromtxt(str(path), skip_header=1)


def _rrqrq_curve_from_summary(sample_dir, measured, recovered):
    """Smooth RRQRQ fit curve (Rs + RQ1 + RQ2) from full_arc_summary_fallback_rrqrq.json.

    The RRQRQ is the fallback fit that succeeded when the primary RRCRC failed.
    """
    sample_dir = Path(sample_dir)
    j = sample_dir / "full_arc_summary_fallback_rrqrq.json"
    if not j.exists():
        j = sample_dir / "full_arc_summary.json"
    if not j.exists():
        return None
    try:
        d = json.loads(j.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    comp = d.get("rrqrq_components") or {}
    try:
        rs = float(comp["Rs_ohm"])
        r0 = float(comp["RQ1_R_ohm"]); q0 = float(comp["RQ1_Q_F_s_alpha_minus_1"]); a0 = float(comp["RQ1_alpha"])
        r1 = float(comp["RQ2_R_ohm"]); q1 = float(comp["RQ2_Q_F_s_alpha_minus_1"]); a1 = float(comp["RQ2_alpha"])
    except Exception:
        return None
    freqs = [df["frequency"].to_numpy(dtype=float) for df in (measured, recovered) if df is not None]
    if not freqs:
        return None
    allf = np.concatenate(freqs)
    allf = allf[np.isfinite(allf) & (allf > 0)]
    if allf.size == 0:
        return None
    grid = np.logspace(np.log10(allf.min()), np.log10(allf.max()), 250)
    w = 2.0 * np.pi * grid

    def _zrq(r, q, a):
        return r / (1.0 + r * q * (1j * w) ** a)

    z = rs + _zrq(r0, q0, a0) + _zrq(r1, q1, a1)
    if not np.all(np.isfinite(z)):
        return None
    return _eis_frame(np.real(z), -np.imag(z), grid)   # -Im(Z) column


def _load_scout_and_fit(sample_dir, row):
    """Attach the scout FFT-ready CA (dashed overlay) and the RRQRQ fit curve
    (dashed overlay) to a row, read from the raw files in the row folder."""
    sample_dir = Path(sample_dir)
    caf = sample_dir / "ca_for_fft_pre_plus_scout_fft_ready.txt"
    if caf.exists():
        try:
            d = _read_txt(caf)
            if d.ndim == 2 and d.shape[1] >= 3:
                row["ca_fft_ready"] = pd.DataFrame(
                    {"time": d[:, 0], "voltage": d[:, 1], "current": d[:, 2]})
        except Exception:
            pass
    row["fit"] = _rrqrq_curve_from_summary(sample_dir, row.get("measured"), row.get("recovered"))
    return row


def prepare_row(oe):
    """Build the sheet DataFrames for one row from its origin_export folder."""
    oe = Path(oe)
    row = {"name": oe.parent.name, "measured": None, "ca": None, "recovered": None}

    meas = next(iter(oe.glob("*_measured_peis.csv")), None)
    if meas is not None:  # freq_Hz, ReZ_ohm, negImZ_ohm
        d = pd.read_csv(meas, encoding="utf-8-sig")
        row["measured"] = _eis_frame(
            _csv_col(d, "ReZ_ohm", 1), _csv_col(d, "negImZ_ohm", 2), _csv_col(d, "freq_Hz", 0))

    ca = next(iter(oe.glob("*_ca_fft_input.csv")), None)
    if ca is not None:  # time_s, voltage_V, current_A
        d = pd.read_csv(ca, encoding="utf-8-sig")
        row["ca"] = pd.DataFrame({
            "time": _csv_col(d, "time_s", 0),
            "voltage": _csv_col(d, "voltage_V", 1),
            "current": _csv_col(d, "current_A", 2),
        })

    arc = next(iter(oe.glob("*_full_arc.csv")), None)
    if arc is not None:  # freq_Hz, ReZ_ohm, ImZ_ohm, negImZ_ohm, absZ_ohm, phase_deg
        d = pd.read_csv(arc, encoding="utf-8-sig")
        r_reZ = _csv_col(d, "ReZ_ohm", 1)
        r_negImZ = _csv_col(d, "negImZ_ohm", 3)
        r_freq = _csv_col(d, "freq_Hz", 0)
        # Drop the measured-PEIS points so the sheet holds only recovered (FFT) data.
        if row["measured"] is not None:
            keep = _recovered_only_mask(r_freq, row["measured"]["frequency"].to_numpy(dtype=float))
            r_reZ, r_negImZ, r_freq = r_reZ[keep], r_negImZ[keep], r_freq[keep]
        row["recovered"] = _eis_frame(r_reZ, r_negImZ, r_freq)

    _load_scout_and_fit(oe.parent, row)   # scout dashed + RRQRQ fit dashed from raw files
    return row


def prepare_row_raw(sample_dir):
    """Build the sheet DataFrames for a row folder that has NO origin_export,
    reading the raw .txt files directly."""
    sample_dir = Path(sample_dir)
    row = {"name": sample_dir.name, "measured": None, "ca": None, "recovered": None}

    mp = sample_dir / "measured_peis.txt"
    if mp.exists():  # freq_Hz, ReZ_ohm, negImZ_ohm
        try:
            d = _read_txt(mp)
            if d.ndim == 2 and d.shape[1] >= 3:
                row["measured"] = _eis_frame(d[:, 1], d[:, 2], d[:, 0])
        except Exception:
            pass

    ca = sample_dir / "ca_for_fft_pre_plus_scout_only.txt"
    if ca.exists():  # time/s V/V I/A
        try:
            d = _read_txt(ca)
            if d.ndim == 2 and d.shape[1] >= 3:
                row["ca"] = pd.DataFrame({"time": d[:, 0], "voltage": d[:, 1], "current": d[:, 2]})
        except Exception:
            pass

    arc = (next(iter(sample_dir.glob("full_arc_*_recommended.txt")), None)
           or next(iter(sample_dir.glob("full_arc_*_fallback_rrqrq.txt")), None))
    if arc is not None:  # freq_Hz, ReZ_ohm, ImZ_ohm, negImZ_ohm
        try:
            d = _read_txt(arc)
            if d.ndim == 2 and d.shape[1] >= 4:
                reZ, negImZ, freq = d[:, 1], d[:, 3], d[:, 0]
                if row["measured"] is not None:
                    keep = _recovered_only_mask(freq, row["measured"]["frequency"].to_numpy(dtype=float))
                    reZ, negImZ, freq = reZ[keep], negImZ[keep], freq[keep]
                row["recovered"] = _eis_frame(reZ, negImZ, freq)
        except Exception:
            pass

    _load_scout_and_fit(sample_dir, row)
    return row


def collect_groups(root):
    """Group rows by condition folder. Uses origin_export CSVs where present,
    and falls back to the raw .txt files for row folders without origin_export.

    Returns [{folder_parts, cond_name, rows:[...]}] sorted by folder.
    """
    root = Path(root)
    by_cond = {}          # cond_dir -> list of (kind, source_path, sample_dir)
    seen_dirs = set()

    # 1) origin_export folders (production CSVs)
    for oe in sorted(p for p in root.rglob("origin_export") if p.is_dir()):
        if not (any(oe.glob("*_measured_peis.csv"))
                or any(oe.glob("*_ca_fft_input.csv"))
                or any(oe.glob("*_full_arc.csv"))):
            continue
        sample_dir = oe.parent
        by_cond.setdefault(sample_dir.parent, []).append(("csv", oe, sample_dir))
        seen_dirs.add(sample_dir)

    # 2) raw-txt row folders that have no origin_export
    for mp in sorted(root.rglob("measured_peis.txt")):
        sample_dir = mp.parent
        if sample_dir in seen_dirs:
            continue
        by_cond.setdefault(sample_dir.parent, []).append(("raw", sample_dir, sample_dir))
        seen_dirs.add(sample_dir)

    groups = []
    for cond in sorted(by_cond, key=str):
        try:
            parts = [p for p in cond.relative_to(root).parts if p not in ("", ".")]
        except ValueError:
            parts = [cond.name]
        entries = sorted(by_cond[cond], key=lambda e: e[2].name)
        rows = [prepare_row(src) if kind == "csv" else prepare_row_raw(src)
                for kind, src, _sd in entries]
        groups.append({"folder_parts": parts, "cond_name": cond.name, "rows": rows})
    return groups


def _iter_row_sources(root):
    """Yield (kind, source_path, sample_dir) for every row folder under root, in
    a stable path order. 'csv' uses the origin_export CSVs; 'raw' falls back to
    the raw .txt files for row folders without an origin_export."""
    root = Path(root)
    seen = set()
    for oe in sorted((p for p in root.rglob("origin_export") if p.is_dir()),
                     key=lambda p: str(p).lower()):
        if not (any(oe.glob("*_measured_peis.csv"))
                or any(oe.glob("*_ca_fft_input.csv"))
                or any(oe.glob("*_full_arc.csv"))):
            continue
        sample_dir = oe.parent
        seen.add(sample_dir)
        yield "csv", oe, sample_dir
    for mp in sorted(root.rglob("measured_peis.txt"), key=lambda p: str(p).lower()):
        sample_dir = mp.parent
        if sample_dir in seen:
            continue
        seen.add(sample_dir)
        yield "raw", sample_dir, sample_dir


def collect_electrodes(root):
    """Organise the dataset by electrode for the per-electrode layout.

    Every electrode is identified by its unique (X, Y, Z) coordinates on the
    film. Within an electrode the rows are grouped by gas-condition folder
    (the row folder's parent, e.g. '[1] 0.2 pO2'); within a gas condition each
    row is one bias point, labelled by its voltage token (e.g. 'V+0p21').

    Returns a list of electrode dicts ordered by first appearance::

        {"number": 1, "coords": "X2p4_Y13p2_Z-1p9",
         "gas": {"[1] 0.2 pO2": [row, ...], ...}}
    """
    root = Path(root)
    electrodes = {}   # coord token -> electrode dict
    order = []        # coord tokens in first-appearance order

    for kind, src, sample_dir in _iter_row_sources(root):
        folder = sample_dir.name
        coord = _parse_coords(folder)
        if coord is None:
            print(f"  ! skipping (no X/Y/Z coordinates in name): {folder}")
            continue
        if coord not in electrodes:
            order.append(coord)
            electrodes[coord] = {"number": len(order), "coords": coord, "gas": {}}
            print(f"  + electrode {electrodes[coord]['number']}: "
                  f"{_coords_readable(coord)}")

        gas_name = sample_dir.parent.name
        volt = _parse_voltage(folder) or _row_token(folder)
        print(f"    reading electrode {electrodes[coord]['number']} / "
              f"{gas_name} / {volt}  <- {folder}")

        row = prepare_row(src) if kind == "csv" else prepare_row_raw(src)
        row["label"] = volt
        electrodes[coord]["gas"].setdefault(gas_name, []).append(row)

    return [electrodes[c] for c in order]


def _fit_curve_dataset(sample_dir, datasets):
    """Rebuild the smooth RQRQRQ fit curve from fit_result.json, if available."""
    fit_json = Path(sample_dir) / "fit_result.json"
    if not fit_json.exists() or not datasets:
        return None
    try:
        if str(ANALYSIS_DIR) not in sys.path:
            sys.path.insert(0, str(ANALYSIS_DIR))
        import EIS_Fitting as EISFIT  # type: ignore

        fit = json.loads(fit_json.read_text(encoding="utf-8"))
        all_f = np.concatenate([np.asarray(d["freq"], dtype=float) for d in datasets])
        all_f = all_f[np.isfinite(all_f) & (all_f > 0)]
        if not all_f.size:
            return None
        grid = np.logspace(np.log10(all_f.min()), np.log10(all_f.max()), 200)
        z = np.asarray(EISFIT.Z_from_fit_result(fit, grid))
        if z.size and np.all(np.isfinite(z)):
            return {"name": "RQRQRQ fit", "freq": grid, "re": np.real(z), "im": np.imag(z)}
    except Exception as exc:  # pragma: no cover - depends on host analysis deps
        print(f"    (fit curve skipped: {exc})")
    return None


def _sample_name_from_anchor(anchor, fallback):
    stem = anchor.stem
    for suffix in _CSV_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return fallback


def _opju_destination(root, sample_dir, sample_name, output_root, in_place):
    """Decide where a sample's .opju goes.

    in_place  -> next to the origin_export folder (inside sample_dir).
    otherwise -> mirror the nested structure under output_root, recreating every
                 intermediate folder (e.g. [7-3].../[1...]/row001...).
    """
    if in_place:
        return sample_dir / f"{sample_name}.opju"
    try:
        rel = sample_dir.relative_to(root)
    except ValueError:
        rel = Path(sample_dir.name)
    return Path(output_root) / rel / f"{sample_name}.opju"


def collect_jobs(root, output_root=None, in_place=False):
    """Return a list of {sample_name, datasets, cp_data, opju_path} to build.

    Recurses to any depth, so a top-level condition folder (e.g. "LSCF 500")
    that nests ``[7-3].../[1...]/row001.../origin_export`` is fully covered.
    """
    root = Path(root)
    if output_root is None:
        output_root = root / "opju_export"
    output_root = Path(output_root)

    if (root / "origin_export").is_dir():
        oe_dirs = [root / "origin_export"]
    else:
        oe_dirs = sorted({p for p in root.rglob("origin_export") if p.is_dir()})

    jobs = []
    for oe in oe_dirs:
        sample_dir = oe.parent

        # Production (deployed OLECOM) format takes priority.
        prod = _collect_production(oe, sample_dir)
        if prod is not None:
            sample_name, datasets, cp_data = prod
        else:
            # Repo export_origin_friendly.py format.
            ref = next(iter(oe.glob("*_reference_peis.csv")), None)
            rec = next(iter(oe.glob("*_recovered_eis.csv")), None)
            dc = next(iter(oe.glob("*_raw_dc.csv")), None)

            anchor = ref or rec or dc
            if anchor is None:
                continue
            sample_name = _sample_name_from_anchor(anchor, sample_dir.name)

            datasets = []
            if ref is not None:
                datasets.append(_read_eis_csv(ref, "Reference PEIS"))
            if rec is not None:
                datasets.append(_read_eis_csv(rec, "Recovered (CP->EIS)"))
            if not datasets:
                continue
            fit_ds = _fit_curve_dataset(sample_dir, datasets)
            if fit_ds:
                datasets.append(fit_ds)

            cp_data = None
            if dc is not None:
                d = pd.read_csv(dc)
                cp_data = np.column_stack([
                    d["time_s"].to_numpy(dtype=float),
                    d["voltage_V"].to_numpy(dtype=float),
                    d["current_A"].to_numpy(dtype=float),
                ])

        try:
            rel_parent = sample_dir.parent.relative_to(root)
        except ValueError:
            rel_parent = Path(sample_dir.parent.name)
        folder_parts = [p for p in rel_parent.parts if p not in ("", ".")]

        jobs.append({
            "sample_name": sample_name,
            "datasets": datasets,
            "cp_data": cp_data,
            "folder_parts": folder_parts,  # hierarchy for the master-layout Project Explorer
            "opju_path": _opju_destination(
                root, sample_dir, sample_name, output_root, in_place),
        })
    return jobs


def _pick_folder():
    """Pop a folder picker so the user can 'select' a condition folder."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askdirectory(title="Select a condition folder (e.g. LSCF 500)")
        root.destroy()
        return path or None
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build Origin .opju projects from an existing analysis output folder.")
    parser.add_argument("folder", nargs="?",
                        help="Condition folder (e.g. LSCF 500), a reanalysis root, or a "
                             "single sample folder. If omitted, a folder picker opens.")
    parser.add_argument("--layout", choices=["electrode", "per-row", "master"],
                        default="electrode",
                        help="'electrode' (default): ONE .opju per electrode (unique X/Y/Z "
                             "location); inside each, a folder per gas condition, with a "
                             "voltage-named workbook (sheets + graphs) per bias point. "
                             "'master': ONE .opju for the whole dataset, a workbook per row + "
                             "7 graphs per condition folder. 'per-row': one .opju per sample.")
    parser.add_argument("--output-root", default=None,
                        help="electrode: folder for the per-electrode .opju files "
                             "(default: <folder>/opju_by_electrode). per-row: where the "
                             "mirrored .opju tree is written (default: <folder>/opju_export). "
                             "master: folder for the single .opju (default: <folder>).")
    parser.add_argument("--dataset-name", default=None,
                        help="electrode only: base name for the .opju files, e.g. 'LSCF 500'. "
                             "Each file becomes '<name> electrode <N>.opju'. If omitted, you "
                             "are prompted (a guess from the folder name is offered).")
    parser.add_argument("--note", default=None,
                        help="electrode only: optional note added to every .opju's Notes "
                             "window (alongside the electrode coordinates). If omitted, you "
                             "are prompted; leave blank to skip.")
    parser.add_argument("--in-place", action="store_true",
                        help="per-row only: write each .opju next to its origin_export "
                             "instead of mirroring into an output tree.")
    parser.add_argument("--show", action="store_true",
                        help="Show the Origin window while building (default: hidden)")
    args = parser.parse_args(argv)

    folder = args.folder or _pick_folder()
    if not folder:
        print("No folder selected.")
        return 2
    root = Path(folder)
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    from export_originpro import originpro_status, OriginExporter

    ok, version, message = originpro_status()
    if not ok:
        print(f"\nCannot build .opju — {message}")
        return 3

    if args.layout == "electrode":
        return _build_by_electrode(root, args, version, OriginExporter)

    if args.layout == "master":
        return _build_master_grouped(root, args, version, OriginExporter)

    output_root = Path(args.output_root) if args.output_root else (root / "opju_export")
    jobs = collect_jobs(root, output_root=output_root, in_place=args.in_place)
    print(f"Found {len(jobs)} sample folder(s) with origin_export data under {root}")
    if not jobs:
        print("Nothing to build. Run the analysis GUI first to produce origin_export CSVs.")
        return 0
    return _build_per_row(jobs, args, version, OriginExporter)


def _build_per_row(jobs, args, version, OriginExporter):
    if not args.in_place:
        print(f"Mirrored .opju tree (one per sample) will be written under the output root.")
    for j in jobs:
        print(f"  - {j['sample_name']}: {', '.join(d['name'] for d in j['datasets'])}")

    print(f"\nStarting Origin (originpro {version}) …")
    exporter = OriginExporter(show=args.show)
    written = 0
    try:
        for j in jobs:
            try:
                exporter.export_sample(
                    j["opju_path"], j["sample_name"], j["datasets"], j["cp_data"])
                print(f"  wrote {j['opju_path']}")
                written += 1
            except Exception as exc:
                print(f"  FAILED {j['sample_name']}: {exc}")
    finally:
        exporter.close()

    print(f"\nDone: {written}/{len(jobs)} Origin project(s) written.")
    return 0


def _suggest_dataset_name(root):
    """Best-effort dataset name guess from the folder, e.g. 'LSCF 500'."""
    m = re.search(r"([A-Za-z]{2,})\s*(\d{3,4})", Path(root).name)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    return Path(root).name


def _prompt(question, default=None):
    """Prompt on the console; return the answer or the default. Non-interactive
    environments (no stdin) fall back to the default silently."""
    try:
        suffix = f" [{default}]" if default else ""
        ans = input(f"{question}{suffix}: ").strip()
    except (EOFError, OSError):
        return default
    return ans or default


def _build_by_electrode(root, args, version, OriginExporter):
    """One .opju per electrode (unique X/Y/Z). Inside each: a Project Explorer
    folder per gas condition, holding a voltage-named workbook (sheets + the 7
    graphs) per bias point, plus a Notes window with the electrode coordinates
    and any user note."""
    print(f"Scanning {root} for row folders …")
    electrodes = collect_electrodes(root)
    if not electrodes:
        print("No electrode data found (no row folders with X/Y/Z coordinates and "
              "origin_export / raw .txt data).")
        return 0

    dataset_name = args.dataset_name or _prompt(
        "Dataset name (e.g. LSCF 500)", _suggest_dataset_name(root))
    dataset_name = (dataset_name or _suggest_dataset_name(root)).strip()

    user_note = args.note
    if user_note is None:
        user_note = _prompt("Optional note to add to every .opju (blank to skip)", "")
    user_note = (user_note or "").strip()

    dest_dir = Path(args.output_root) if args.output_root else (root / "opju_by_electrode")
    dest_dir.mkdir(parents=True, exist_ok=True)

    n_gas = sum(len(e["gas"]) for e in electrodes)
    n_rows = sum(len(rows) for e in electrodes for rows in e["gas"].values())
    print(f"\nPer-electrode layout -> {dest_dir}")
    print(f"  {len(electrodes)} electrode(s), {n_gas} gas-condition folder(s), "
          f"{n_rows} bias point(s) total.")
    print(f"  Each electrode: 1 .opju named '{dataset_name} electrode <N>'; a folder per "
          f"gas condition; a voltage-named workbook (5 sheets) + 7 graphs per bias point.")
    print(f"  This is heavy over COM; keep Origin closed while it runs.")

    print(f"\nStarting Origin (originpro {version}) …")
    exporter = OriginExporter(show=args.show)
    written = 0
    try:
        for el in electrodes:
            num = el["number"]
            readable = _coords_readable(el["coords"])
            fname = _safe_filename(f"{dataset_name} electrode {num}") + ".opju"
            out_path = dest_dir / fname
            print(f"\n=== Electrode {num}  ({readable}) -> {fname} ===")
            try:
                exporter.begin_project()
                note_lines = [
                    f"Dataset: {dataset_name}",
                    f"Electrode {num}",
                    f"Coordinates: {readable}  [{el['coords']}]",
                ]
                if user_note:
                    note_lines += ["", user_note]
                exporter.add_project_note("\n".join(note_lines))

                for gas_name in sorted(el["gas"], key=_gas_sort_key):
                    rows = el["gas"][gas_name]
                    try:
                        exporter.add_group([gas_name], rows)
                        print(f"    built {gas_name}  ({len(rows)} bias point(s))")
                    except Exception as exc:
                        print(f"    FAILED {gas_name}: {exc}")

                exporter.save_project(out_path)
                print(f"  saved {out_path}")
                written += 1
            except Exception as exc:
                print(f"  FAILED electrode {num}: {exc}")
    finally:
        exporter.close()

    print(f"\nDone: {written}/{len(electrodes)} electrode project(s) written to {dest_dir}.")
    return 0


def _build_master_grouped(root, args, version, OriginExporter):
    groups = collect_groups(root)
    n_rows = sum(len(g["rows"]) for g in groups)
    if not groups:
        print("No production origin_export folders (measured_peis/ca_fft_input/full_arc) found.")
        return 0

    dest_dir = Path(args.output_root) if args.output_root else root
    # Keep spaces/brackets in the filename; only strip Windows-invalid characters.
    fname = re.sub(r'[\\/:*?"<>|]+', "_", root.name).strip() or "dataset"
    master_path = dest_dir / f"{fname}.opju"

    print(f"Master (grouped) layout -> {master_path}")
    print(f"  {len(groups)} condition folder(s), {n_rows} rows total.")
    print(f"  Each condition folder: 1 workbook/row (3 sheets: Measured PEIS, Measured CA, "
          f"Recovered EIS) + 7 graphs.")
    print(f"  This is heavy over COM; keep Origin closed while it runs.")

    print(f"\nStarting Origin (originpro {version}) …")
    exporter = OriginExporter(show=args.show)
    done = 0
    try:
        exporter.begin_project()
        for g in groups:
            label = "/".join(g["folder_parts"]) or g["cond_name"]
            try:
                exporter.add_group(g["folder_parts"], g["rows"])
                done += 1
                print(f"  built {label}  ({len(g['rows'])} rows)")
            except Exception as exc:
                print(f"  FAILED {label}: {exc}")
        exporter.save_project(master_path)
        print(f"\nSaved master project: {master_path}")
    finally:
        exporter.close()

    print(f"Done: {done}/{len(groups)} condition folder(s) built into {master_path.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
