# -*- coding: utf-8 -*-
"""
Interactive MFC write smoke test.

This is intentionally separate from the normal GUI safety gate. It temporarily
enables driver_mfc writes only inside this Python process after the user types a
confirmation phrase. Use it to validate that A/B raw settings can be written and
read back on the Windows 7 runner before setting MFC_WRITE_ENABLED=True in
config.py for unattended automation.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import driver_mfc
import config
from driver_mfc import AeraMFC


def _prompt_float(label: str, default: float) -> float:
    raw = input(f"{label} target raw setting 0-100 [{default:g}]: ").strip()
    if not raw:
        return float(default)
    return float(raw)


def _print_readback(mfc: AeraMFC, label: str):
    print(f"\n--- {label} readback ---")
    for ch in ("A", "B"):
        fs = mfc.get_full_scale(ch)
        flow = mfc.get_flow(ch)
        sp = mfc.get_setpoint(ch)
        print(
            f"Ch {ch}: full-scale={fs:g} sccm, "
            f"flow-setting={flow:g}, setpoint-setting={sp:g}, "
            f"approx-flow={mfc.setting_to_sccm(ch, flow):g} sccm"
        )


def main():
    parser = argparse.ArgumentParser(description="Interactive Aera MFC write smoke test")
    parser.add_argument("--a", type=float, default=None, help="Gas A raw setting 0-100")
    parser.add_argument("--b", type=float, default=None, help="Gas B raw setting 0-100")
    parser.add_argument(
        "--channels",
        default="A,B",
        help="Comma-separated channels to write: A, B, or A,B. Readback still shows both.",
    )
    parser.add_argument("--wait", type=float, default=2.0, help="Seconds to wait before readback")
    parser.add_argument(
        "--digital-control",
        action="store_true",
        help="Send the driver's digital-control command before SFD writes",
    )
    args = parser.parse_args()

    print("=== MFC WRITE smoke test ===")
    print("This WILL send setpoint commands to the MFC.")
    print("Use only after the read-only diagnostic confirms COM port/address mapping.")
    print("Close the vendor MFC utility first so the COM port is free.")
    print(f"Script: {Path(__file__).resolve()}")
    print(f"Driver: {Path(driver_mfc.__file__).resolve()}")
    print(f"Driver version: {getattr(driver_mfc, 'MFC_DRIVER_VERSION', 'unknown')}")
    print(
        "Readback polling: "
        f"timeout={getattr(config, 'MFC_SETPOINT_READBACK_TIMEOUT_S', 'missing')} s, "
        f"interval={getattr(config, 'MFC_SETPOINT_READBACK_POLL_S', 'missing')} s"
    )
    print()

    channels = {
        token.strip().upper()
        for token in str(args.channels).replace(";", ",").split(",")
        if token.strip()
    }
    if not channels or not channels.issubset({"A", "B"}):
        raise SystemExit("--channels must be A, B, or A,B")

    target_a = args.a if args.a is not None else (_prompt_float("Gas A", 5.0) if "A" in channels else None)
    target_b = args.b if args.b is not None else (_prompt_float("Gas B", 5.0) if "B" in channels else None)

    print()
    print(
        "Requested raw settings: "
        + ", ".join(
            part for part in (
                f"A={target_a:g}" if "A" in channels else None,
                f"B={target_b:g}" if "B" in channels else None,
            )
            if part is not None
        )
    )
    confirm = input("Type WRITE to send these setpoints, or anything else to abort: ").strip()
    if confirm != "WRITE":
        print("Aborted. No setpoint commands were sent.")
        return 2

    # Enable writes only inside this smoke-test process. This does not edit config.py.
    driver_mfc.MFC_WRITE_ENABLED = True
    driver_mfc.MFC_SEND_DIGITAL_CONTROL_ON_WRITE = bool(args.digital_control)

    mfc = AeraMFC()
    try:
        mfc.connect()
        _print_readback(mfc, "Before write")
        print("\nSending raw settings...")
        if "A" in channels:
            mfc.set_setting("A", target_a)
        if "B" in channels:
            mfc.set_setting("B", target_b)
        if args.wait > 0:
            print(f"Waiting {args.wait:g} s...")
            time.sleep(args.wait)
        _print_readback(mfc, "After write")
    finally:
        try:
            mfc.disconnect()
        except Exception:
            pass

    print("\nIf after-write setpoint-settings match the requested values, MFC writing is validated.")
    print("Then you can set MFC_WRITE_ENABLED=True in config.py for GUI automation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
