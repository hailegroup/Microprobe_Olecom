"""Read-only diagnostic: replay the REAL _select_pre_tail_plus_scout logic
against an already-completed olecom_pre_scout_ca_C01.mpr, to tell apart two
possible causes of a sparse/missing pre-hold in the saved
ca_for_fft_pre_plus_scout_only.txt:

  (a) a bug in _select_pre_tail_plus_scout's own selection logic (would
      reproduce right here, on this static, complete file), vs.
  (b) something specific to how/when the live run read the .mpr while it
      was still being written by EC-Lab (would NOT reproduce here, since
      this file is already complete).

Run this on the Win7 lab PC (where galvani is installed):

    python tools/diag_replay_pre_tail_selection.py "path/to/olecom_pre_scout_ca_C01.mpr" [pre_tail_s]

pre_tail_s defaults to 10.0 (the live pipeline's default).

Does not modify or move the .mpr; read-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def main() -> None:
    if len(sys.argv) not in (2, 3):
        print("usage: python tools/diag_replay_pre_tail_selection.py <path to .mpr> [pre_tail_s]", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    pre_tail_s = float(sys.argv[2]) if len(sys.argv) == 3 else 10.0

    from run_olecom_pre_scout_post_hybrid import _parse_mpr_dc, _select_pre_tail_plus_scout

    ca_pre_scout = _parse_mpr_dc(Path(path))
    print(f"_parse_mpr_dc returned shape: {ca_pre_scout.shape}")
    ns = ca_pre_scout[:, 3]
    t = ca_pre_scout[:, 0]
    for val in (0, 1, 2):
        mask = np.isclose(ns, val)
        if np.any(mask):
            t_val = t[mask]
            print(f"  Ns={val}: {int(mask.sum())} rows, time {t_val.min():.4f} to {t_val.max():.4f}")
        else:
            print(f"  Ns={val}: 0 rows")

    print(f"\nCalling _select_pre_tail_plus_scout(ca_pre_scout, pre_tail_s={pre_tail_s})...")
    out, trim_source = _select_pre_tail_plus_scout(ca_pre_scout, pre_tail_s=pre_tail_s)
    print(f"trim_source = {trim_source!r}")
    print(f"output shape: {out.shape}")
    if len(out):
        print(f"output time range (rebased): {out[:, 0].min():.4f} to {out[:, 0].max():.4f}")
        # How many of the output rows sit before the big value jump (i.e. the
        # step) vs after -- a crude proxy since output here is only [t, V, I].
        dv = np.abs(np.diff(out[:, 1]))
        if len(dv):
            step_idx = int(np.argmax(dv)) + 1
            print(f"largest voltage jump at output row {step_idx} (t={out[step_idx, 0]:.4f}), "
                  f"{step_idx} rows before it, {len(out) - step_idx} rows after")

    print("\nFirst 10 output rows (time, V, I):")
    for row in out[:10]:
        print(f"  t={row[0]:.6f}  V={row[1]:.6f}  I={row[2]:.6e}")


if __name__ == "__main__":
    main()
