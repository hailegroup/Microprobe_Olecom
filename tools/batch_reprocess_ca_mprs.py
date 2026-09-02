"""Batch-regenerate ca_for_fft_pre_plus_scout_only.txt from the raw
olecom_pre_scout_ca_C*.mpr files across a whole results tree.

Why this exists: _wait_for_point_count_stable used to trust EC-Lab's own
live point-count query alone before reading a .mpr for FFT analysis. That
query can report "done" before the on-disk file is actually fully
flushed, so on some runs the very sparse-but-real .mpr row data was
present all along, but the SAVED ca_for_fft_pre_plus_scout_only.txt was
built from a snapshot that only had a fraction of it -- mostly the
pre-hold, since it was written first and buffered longest. The .mpr
itself was never touched by that bug (confirmed on a real run: EC-Lab
and a fresh galvani parse both show the full, dense pre-hold). This tool
re-reads each row's raw .mpr straight from disk (now safely, since the
run is long finished and nothing is still buffering) and regenerates a
correct .txt from it, using the exact same selection logic
(_select_pre_tail_plus_scout) the live pipeline uses, with each row's own
originally-configured pre_tail_s (recorded in that row's summary.json).

Only touches the raw pre+scout CA file the CA-FFT tuning GUI
(tools/ca_fft_tuning_gui.py) actually reads (summary["ca_fft_raw_txt"] /
ca_for_fft_pre_plus_scout_only.txt). Does not touch the post-hold .mpr,
the already-saved fft_ready/smoothed trace, or any of the full-arc/Nyquist
PNGs -- rerun the tuning GUI (or the normal deferred postprocess) against
a row after reprocessing it here to regenerate those from the corrected
raw data.

Run this on the Win7 lab PC (where galvani is installed):

    python tools/batch_reprocess_ca_mprs.py "path/to/results/root" [--dry-run] [--pre-tail-s 10.0]

Any row folder is found by locating olecom_pre_scout_ca_C*.mpr anywhere
under the given root (recursively) -- point it at a single row folder or
an entire multi-row run's top-level results directory.

Existing ca_for_fft_pre_plus_scout_only.txt files are backed up (once,
non-destructively -- a pre-existing backup is never overwritten) to
ca_for_fft_pre_plus_scout_only.txt.pre_reprocess_backup before being
replaced.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

BACKUP_SUFFIX = ".pre_reprocess_backup"


def _load_summary(row_dir: Path) -> dict:
    path = row_dir / "summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def reprocess_row(
    mpr_path: Path,
    *,
    default_pre_tail_s: float,
    dry_run: bool,
) -> dict:
    """Regenerate one row's ca_for_fft_pre_plus_scout_only.txt from its raw .mpr.

    Returns a small report dict; raises on unrecoverable per-row errors
    (caller decides whether to keep going).
    """
    from run_olecom_pre_scout_post_hybrid import _parse_mpr_dc, _save_ca_txt, _select_pre_tail_plus_scout

    row_dir = mpr_path.parent
    summary = _load_summary(row_dir)
    pre_tail_s = float(summary.get("ca_fft_pre_tail_s", default_pre_tail_s))

    txt_path = row_dir / "ca_for_fft_pre_plus_scout_only.txt"
    old_count = None
    if txt_path.exists():
        try:
            with txt_path.open("r", encoding="latin1") as fh:
                old_count = sum(1 for _ in fh) - 1  # minus header
        except Exception:
            old_count = None

    ca_pre_scout = _parse_mpr_dc(mpr_path)
    new_ca, trim_source = _select_pre_tail_plus_scout(ca_pre_scout, pre_tail_s=pre_tail_s)
    new_count = len(new_ca)

    report = {
        "row_dir": str(row_dir),
        "mpr_path": str(mpr_path),
        "pre_tail_s": pre_tail_s,
        "trim_source": trim_source,
        "old_txt_points": old_count,
        "new_txt_points": new_count,
        "changed": (old_count is None) or (new_count != old_count),
        "dry_run": dry_run,
    }

    if dry_run:
        return report

    if txt_path.exists():
        backup_path = row_dir / (txt_path.name + BACKUP_SUFFIX)
        if not backup_path.exists():
            shutil.copy2(txt_path, backup_path)
            report["backup_written"] = str(backup_path)
        else:
            report["backup_written"] = None  # already had one from a prior run

    _save_ca_txt(txt_path, new_ca)
    report["txt_written"] = str(txt_path)

    if (row_dir / "summary.json").exists():
        summary["ca_fft_raw_reprocessed"] = True
        summary["ca_fft_raw_reprocessed_trim_source"] = trim_source
        summary["ca_fft_raw_reprocessed_old_points"] = old_count
        summary["ca_fft_raw_reprocessed_new_points"] = new_count
        (row_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return report


def find_pre_scout_mprs(root: Path) -> list[Path]:
    return sorted(root.rglob("olecom_pre_scout_ca_C*.mpr"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate ca_for_fft_pre_plus_scout_only.txt from raw pre+scout CA .mpr files."
    )
    parser.add_argument("root", help="Results root (or single row folder) to search recursively for .mpr files.")
    parser.add_argument(
        "--pre-tail-s", type=float, default=10.0,
        help="Fallback pre_tail_s if a row's summary.json doesn't record ca_fft_pre_tail_s (default: 10.0).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing anything.")
    args = parser.parse_args()

    root = Path(args.root)
    mprs = find_pre_scout_mprs(root)
    if not mprs:
        print(f"No olecom_pre_scout_ca_C*.mpr files found under {root}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(mprs)} pre+scout CA .mpr file(s) under {root}")
    changed = 0
    errors = 0
    for mpr_path in mprs:
        try:
            report = reprocess_row(mpr_path, default_pre_tail_s=args.pre_tail_s, dry_run=args.dry_run)
        except Exception as exc:
            errors += 1
            print(f"[ERROR] {mpr_path}: {exc}", file=sys.stderr)
            continue
        marker = "CHANGED" if report["changed"] else "unchanged"
        print(
            f"[{marker}] {report['row_dir']}: {report['old_txt_points']} -> "
            f"{report['new_txt_points']} points (pre_tail_s={report['pre_tail_s']:g}, "
            f"trim_source={report['trim_source']})"
        )
        if report["changed"]:
            changed += 1

    verb = "would change" if args.dry_run else "changed"
    print(f"\nDone: {changed}/{len(mprs)} rows {verb}, {errors} error(s).")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
