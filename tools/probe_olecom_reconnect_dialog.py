from __future__ import annotations

"""
Isolate whether repeatedly reconnecting the OLE-COM device (ConnectDeviceByIP
/ ConnectDevice / SelectDevice / SelectChannel, in OleComController.connect())
is what triggers EC-Lab's "parameters blank / reconfirm safety limits" prompt
-- the leading hypothesis for why LoadSettings keeps failing identically even
after gui.py's OLE-COM recovery path (_recover_olecom_after_error) drops the
stale Python-side proxy and forces a fresh reconnect. If that prompt is a
modal dialog, it would block EC-Lab's UI thread and explain why every
LoadSettings retry after a reconnect fails the same way: the problem isn't a
stale handle anymore, it's a human-input-blocked EC-Lab.

Deliberately never calls LoadSettings/RunChannel -- this isolates connect()
alone, independent of any actual technique file.

Run this directly on the Win7 instrument machine, with EC-Lab open/visible
and the potentiostat physically connected, so you can watch what EC-Lab does
after each forced reconnect:

    python tools/probe_olecom_reconnect_dialog.py --cycles 3
"""

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_olecom_pre_scout_post_hybrid import OleComController  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default="192.109.209.128")
    parser.add_argument("--channel", type=int, default=1, help="User-visible 1-based channel.")
    parser.add_argument("--cycles", type=int, default=3, help="Number of forced reconnects to run.")
    parser.add_argument("--create-if-missing", action="store_true")
    args = parser.parse_args()

    ctrl = OleComController(
        ip=args.ip,
        channel=int(args.channel),
        create_if_missing=bool(args.create_if_missing),
        trust_test_connection=True,
    )

    print("Connecting for the first time (normal startup path)...")
    ctrl.connect()
    print("Connected.\n")

    observed_on = []
    for i in range(1, int(args.cycles) + 1):
        print(f"--- Cycle {i}/{args.cycles} ---")
        input(
            "Look at EC-Lab now (note whether parameters/fields look normal), "
            "then press Enter to force a reconnect..."
        )

        t0 = time.perf_counter()
        try:
            ctrl.connect()  # exact same call gui.py's recovery path ends up making
            elapsed = time.perf_counter() - t0
            print(f"connect() succeeded in {elapsed:.1f}s")
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            print(f"connect() FAILED after {elapsed:.1f}s: {exc!r}")

        answer = input(
            "Check EC-Lab now -- are the parameter fields blank, or is there a "
            "safety-limits / reset prompt on screen? (y/n): "
        ).strip().lower()
        hit = answer.startswith("y")
        if hit:
            observed_on.append(i)
        print(f"  -> recorded: dialog_observed={hit}\n")

    print("=== Summary ===")
    print(f"Cycles run: {args.cycles}")
    print(f"Dialog observed after cycles: {observed_on or 'none'}")
    print("Leaving the connection open (no disconnect) so you can inspect EC-Lab further.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
