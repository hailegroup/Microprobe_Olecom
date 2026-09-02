from __future__ import annotations

"""
Directly test the "reconnecting after dropping a live COM reference spawns
a competing EClab.exe process" hypothesis, instead of inferring it from
circumstantial process counts.

First version of this script kept two OleComController instances (A and B)
alive *simultaneously* and had B connect while A was still referenced. That
came back negative: B connecting reused A's already-running process (COM's
class-factory reuse), no second EClab.exe appeared, and B.LoadSettings
succeeded fine. But that isn't what the real (now-fixed) bug did.

The real _recover_olecom_after_error did `self.bl.ctrl = None` -- it
DROPPED AND RELEASED the only reference to the working connection, THEN
reconnected. Releasing the last COM reference to an object before creating
a new one is a materially different scenario from holding two references
open at once (class-factory reuse may specifically depend on an existing
live reference to route back to). This version reproduces that exact
sequence:
  1. Controller A connects (stands in for "a real measurement in
     progress").
  2. A's reference is explicitly released (matches self.bl.ctrl = None +
     garbage collection) -- NOT just left alive/unused.
  3. Controller B is created fresh afterward and attempts
     ConnectDevice/SelectDevice/SelectChannel + LoadSettings on the SAME
     device/channel.

If a second EClab.exe now appears and/or B's LoadSettings fails the same
way real runs did, that's direct confirmation of the hypothesis in its
actual (drop-then-reconnect) form.

Never calls RunChannel -- LoadSettings alone is enough to test this, and
nothing actually starts running on the hardware.

SAFETY: only run this when the instrument is idle. This deliberately
recreates the exact conflicted state that caused real run failures -- do
not run it during a real sample measurement.

Usage (on the Win7 instrument machine, EC-Lab closed beforehand so the
first controller creates a fresh, predictable instance):
    python tools/probe_olecom_duplicate_process_conflict.py --channel 1
"""

import argparse
import gc
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_olecom_pre_scout_post_hybrid import OleComController, _write_peis_mps  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1, help="User-visible 1-based channel.")
    parser.add_argument("--device", type=int, default=0)
    args = parser.parse_args()

    out_dir = PROJECT_DIR / "results" / "probe_olecom_duplicate_process_conflict"
    mps_path = out_dir / "duplicate_conflict_test_peis.mps"
    _write_peis_mps(
        mps_path,
        bias_v=0.0,
        f_high_hz=1000.0,
        f_low_hz=100.0,
        points_per_decade=6,
        amplitude_mv=10.0,
        bandwidth=8,
        n_average=1,
    )

    print("--- Controller A: connecting (stands in for a real measurement already running) ---")
    ctrl_a = OleComController(
        ip=args.ip, channel=args.channel, device=args.device,
        create_if_missing=True, trust_test_connection=True,
    )
    ctrl_a.connect()
    print(f"A connected. A._connected()={ctrl_a._connected()}")
    input("Confirm in Task Manager that exactly one EClab.exe is running, then press Enter...")

    print(
        "\n--- Releasing A's COM reference (mirrors the old "
        "_recover_olecom_after_error's `self.bl.ctrl = None`) ---"
    )
    ctrl_a.obj = None
    del ctrl_a
    gc.collect()
    time.sleep(1.0)
    print("A's reference released.")
    input(
        "Check Task Manager -- EClab.exe itself likely didn't close (releasing our "
        "COM reference doesn't close the app), then press Enter to create B fresh..."
    )

    print("\n--- Controller B: connecting fresh, after A's reference was dropped ---")
    ctrl_b = OleComController(
        ip=args.ip, channel=args.channel, device=args.device,
        create_if_missing=True, trust_test_connection=True,
    )
    ctrl_b.connect()
    print(f"B connected. B._connected()={ctrl_b._connected()}")
    input(
        "Check Task Manager now -- did a SECOND EClab.exe appear? Check EC-Lab "
        "window(s) for blank parameters / a safety-limits prompt, then press Enter "
        "to attempt LoadSettings on B..."
    )

    print("\n--- Attempting LoadSettings on B ---")
    t0 = time.perf_counter()
    try:
        load_code = ctrl_b.obj.LoadSettings(ctrl_b.device, ctrl_b.com_channel, str(mps_path))
        elapsed = time.perf_counter() - t0
        print(f"B.LoadSettings returned {load_code!r} in {elapsed:.1f}s")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"B.LoadSettings raised after {elapsed:.1f}s: {exc!r}")

    answer = input(
        "\nCheck EC-Lab now -- blank parameters / safety-limits prompt observed? (y/n): "
    ).strip().lower()
    print(f"dialog_observed={answer.startswith('y')}")

    print("\nDone. Leaving B's connection open (no disconnect) for further inspection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
