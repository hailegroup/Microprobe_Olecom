"""Read-only diagnostic: inspect what galvani actually extracts from a linked
OLE-COM pre+scout CA .mpr file.

Run this on the Win7 lab PC (where `galvani` is installed) against the raw
.mpr, e.g.:

    python tools/diag_inspect_ca_mpr.py "path/to/olecom_pre_scout_ca_C01.mpr"

Purpose: `_parse_mpr_dc` (tools/run_olecom_pre_scout_post_hybrid.py) reads
this file via galvani.BioLogic.MPRfile and relies on an "Ns" column to tell
pre-hold (Ns=0) rows apart from scout (Ns=1) rows. The derived
ca_for_fft_pre_plus_scout_only.txt for at least one real run showed only a
handful of Ns=0 rows despite a 120s pre-hold that EC-Lab itself displays as
densely recorded -- this script prints exactly what galvani returns (column
names, total row count, per-Ns row counts, and per-Ns time ranges) so we can
tell whether the parse itself is under-reading the file, or whether "Ns"
doesn't mean what _parse_mpr_dc assumes it means for this file.

Does not modify or move the .mpr; read-only.
"""
from __future__ import annotations

import sys

import numpy as np


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python tools/diag_inspect_ca_mpr.py <path to .mpr>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    from galvani import BioLogic

    mpr = BioLogic.MPRfile(path)
    arr = mpr.data
    names = arr.dtype.names or ()
    print(f"File: {path}")
    print(f"Total rows in mpr.data: {len(arr)}")
    print(f"Available columns ({len(names)}): {list(names)}")
    print()

    if "time/s" not in names:
        print("No 'time/s' column found -- cannot continue.")
        return
    t = np.asarray(arr["time/s"], dtype=float)
    print(f"time/s range: {t.min():.4f} to {t.max():.4f} (n={len(t)})")

    if "Ewe/V" in names:
        v = np.asarray(arr["Ewe/V"], dtype=float)
        print(f"Ewe/V range: {v.min():.6f} to {v.max():.6f}")

    if "Ns" not in names:
        print("\nNo 'Ns' column found in this file -- _parse_mpr_dc falls back to "
              "an all-zero Ns array, which would make _select_pre_tail_plus_scout "
              "treat the ENTIRE recording as 'pre' (Ns==0) and find no scout rows, "
              "triggering its ns_missing_pre_or_scout_raw_fallback path. That would "
              "explain data loss if pre_tail_s selection then only keeps a small "
              "tail window.")
        return

    ns = np.asarray(arr["Ns"], dtype=float)
    unique_ns, counts = np.unique(ns, return_counts=True)
    print(f"\nUnique Ns values and row counts:")
    for val, count in zip(unique_ns, counts):
        mask = np.isclose(ns, val)
        t_ns = t[mask]
        print(
            f"  Ns={val:g}: {count} rows, time range "
            f"{t_ns.min():.4f} to {t_ns.max():.4f} s "
            f"(span={t_ns.max() - t_ns.min():.4f}s)"
        )

    print("\nFirst 10 rows (time, Ewe/V, Ns):")
    for idx in range(min(10, len(t))):
        ewe = arr["Ewe/V"][idx] if "Ewe/V" in names else float("nan")
        print(f"  t={t[idx]:.6f}  Ewe={ewe:.6f}  Ns={ns[idx]:g}")

    # Highlight anything that would explain sparse Ns==0 rows specifically:
    pre_mask = np.isclose(ns, 0)
    pre_t = t[pre_mask]
    if len(pre_t) > 1:
        dt_values = np.diff(np.sort(pre_t))
        dt_values = dt_values[np.isfinite(dt_values) & (dt_values > 0)]
        if len(dt_values):
            print(
                f"\nNs==0 median dt: {np.median(dt_values):.4f}s "
                f"(min={dt_values.min():.4f}s, max={dt_values.max():.4f}s) "
                f"over {len(pre_t)} points spanning {pre_t.max() - pre_t.min():.2f}s"
            )


if __name__ == "__main__":
    main()
