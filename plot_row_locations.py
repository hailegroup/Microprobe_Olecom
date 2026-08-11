# -*- coding: utf-8 -*-
"""
Map measurement rows to their physical stage locations.

Each row folder carries a ``measurement_record.json`` with ``row_index``,
``label`` and ``row_settings`` (X_mm, Y_mm, Z_mm, Temperature_C, V_dc, ...).
The row folder name encodes the same values (``..._E1_X2p4_Y13p2_Z-1p9_...``)
and is used as a fallback when the JSON is missing.

Outputs (written next to the selected folder, or to --out):
  * row_locations.csv  - one line per row: row, label, condition, X/Y/Z, E, T, V
  * row_locations_map.png - scatter of the unique electrode locations (X vs Y),
    each annotated with its electrode id and how many rows were measured there.

Usage:
    python plot_row_locations.py <folder> [--out DIR] [--show]
If <folder> is omitted a folder picker opens.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _p2f(token):
    """'2p4'->2.4, '-1p9'->-1.9, '+0p21'->0.21, '500'->500.0."""
    if token is None:
        return None
    t = str(token).replace("p", ".").lstrip("+")
    try:
        return float(t)
    except ValueError:
        return None


def parse_folder_name(name):
    """Fallback: pull X/Y/Z/E/T/V out of a row folder name."""
    out = {}
    for key, pat in (("X", r"_X(-?\+?[0-9p]+)"), ("Y", r"_Y(-?\+?[0-9p]+)"),
                     ("Z", r"_Z(-?\+?[0-9p]+)"), ("T", r"_T([0-9]+)"),
                     ("V", r"_V([+-]?[0-9p]+)")):
        m = re.search(pat, name)
        if m:
            out[key] = _p2f(m.group(1))
    m = re.search(r"_E([0-9]+)", name)
    if m:
        out["E"] = int(m.group(1))
    m = re.match(r"(row[0-9]+)", name)
    if m:
        out["row_label"] = m.group(1)
    return out


def _row_from_record(rec_path, root):
    sample_dir = rec_path.parent
    info = {"folder": sample_dir.name}
    try:
        info["condition"] = str(sample_dir.parent.relative_to(root))
    except ValueError:
        info["condition"] = sample_dir.parent.name

    data = {}
    try:
        d = json.loads(rec_path.read_text(encoding="utf-8", errors="ignore"))
        rs = d.get("row_settings", {}) or {}
        data["row"] = d.get("row_index")
        data["label"] = d.get("label")
        data["X"] = _p2f(rs.get("X_mm"))
        data["Y"] = _p2f(rs.get("Y_mm"))
        data["Z"] = _p2f(rs.get("Z_mm"))
        data["T"] = _p2f(rs.get("Temperature_C"))
        data["V"] = _p2f(rs.get("V_dc"))
        lab = str(rs.get("Label", d.get("label", "")))
        m = re.search(r"_E([0-9]+)", lab)
        data["E"] = int(m.group(1)) if m else None
    except Exception:
        pass

    # Fill any gaps from the folder name.
    fb = parse_folder_name(sample_dir.name)
    for k in ("X", "Y", "Z", "T", "V", "E"):
        if data.get(k) is None and fb.get(k) is not None:
            data[k] = fb[k]
    if not data.get("row_label"):
        rl = fb.get("row_label")
        if rl:
            info["row_label"] = rl
    info.update(data)
    return info


def collect_locations(root):
    root = Path(root)
    recs = sorted(root.rglob("measurement_record.json"))
    rows = [_row_from_record(r, root) for r in recs]
    if not rows:  # no JSON at all: parse folder names of any origin_export parents
        seen = set()
        for oe in root.rglob("origin_export"):
            d = oe.parent
            if d in seen:
                continue
            seen.add(d)
            info = {"folder": d.name}
            try:
                info["condition"] = str(d.parent.relative_to(root))
            except ValueError:
                info["condition"] = d.parent.name
            info.update(parse_folder_name(d.name))
            rows.append(info)
    return pd.DataFrame(rows)


def plot_locations(df, out_png, title):
    have_xy = df.dropna(subset=["X", "Y"])
    if have_xy.empty:
        return None
    # Unique electrode locations with a measurement count.
    grp = (have_xy.groupby(["X", "Y"])
           .agg(n=("folder", "size"),
                E=("E", lambda s: sorted(set(int(v) for v in s.dropna()))))
           .reset_index())

    fig, ax = plt.subplots(figsize=(6.5, 7.5), layout="constrained")
    e_for_color = [ (e[0] if isinstance(e, list) and e else 0) for e in grp["E"] ]
    sc = ax.scatter(grp["X"], grp["Y"], c=e_for_color, s=140, cmap="plasma",
                    edgecolor="black", zorder=3)
    for _, r in grp.iterrows():
        elabel = "E" + "/".join(str(e) for e in r["E"]) if r["E"] else "?"
        ax.annotate(f"{elabel}\n(n={r['n']})", (r["X"], r["Y"]),
                    textcoords="offset points", xytext=(8, 0),
                    va="center", fontsize=8)
    ax.set_xlabel("Stage X (mm)")
    ax.set_ylabel("Stage Y (mm)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.margins(0.25)
    if len(set(e_for_color)) > 1:
        fig.colorbar(sc, ax=ax, label="Electrode #", shrink=0.7)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)
    return out_png


def _pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw()
        p = filedialog.askdirectory(title="Select a condition folder")
        root.destroy()
        return p or None
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map measurement rows to stage locations.")
    ap.add_argument("folder", nargs="?", help="Condition/top folder (picker if omitted)")
    ap.add_argument("--out", default=None, help="Output dir (default: <folder>)")
    ap.add_argument("--show", action="store_true", help="Open the map after building")
    args = ap.parse_args(argv)

    folder = args.folder or _pick_folder()
    if not folder:
        print("No folder selected.")
        return 2
    root = Path(folder)
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    out_dir = Path(args.out) if args.out else root
    out_dir.mkdir(parents=True, exist_ok=True)

    df = collect_locations(root)
    if df.empty:
        print("No measurement_record.json or row folders found.")
        return 0

    cols = [c for c in ["row", "row_label", "label", "condition", "E",
                        "X", "Y", "Z", "T", "V", "folder"] if c in df.columns]
    df = df[cols].sort_values([c for c in ["condition", "row"] if c in cols])
    csv_path = out_dir / "row_locations.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    n_xy = df.dropna(subset=["X", "Y"]).shape[0] if {"X", "Y"} <= set(df.columns) else 0
    uniq = df.dropna(subset=["X", "Y"]).drop_duplicates(["X", "Y"]).shape[0] if n_xy else 0
    print(f"{len(df)} rows | {n_xy} with X/Y | {uniq} unique locations")
    print(f"Table: {csv_path}")

    png_path = out_dir / "row_locations_map.png"
    made = plot_locations(df, png_path, f"Electrode locations - {root.name}")
    if made:
        print(f"Map:   {png_path}")
        if args.show:
            try:
                import os
                os.startfile(str(png_path))  # Windows
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
