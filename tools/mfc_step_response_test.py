# -*- coding: utf-8 -*-
"""
Interactive Aera MFC step-response logger.

This separates two questions that look identical in the GUI:
  1. Did the DMFC setpoint register (RFD) change immediately?
  2. Did the measured output (RFX) ramp slowly after the setpoint changed?

Run this on the instrument PC with the vendor DMFC utility closed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import serial

from config import COM_PORTS, SERIAL_BAUD, MFC_ADDRESS, MFC_FULL_SCALE_SCCM


def _hex_addr(channel: str) -> str:
    return format(MFC_ADDRESS[channel.upper()], "02x")


def _send(ser, channel: str, cmd: str, wait_s: float = 0.08) -> str:
    msg = ("\x02%s%s\r" % (_hex_addr(channel), cmd)).encode("ascii")
    ser.reset_input_buffer()
    ser.write(msg)
    ser.flush()
    time.sleep(wait_s)
    return ser.readline().decode("ascii", errors="replace").strip()


def _parse(resp: str):
    if not resp or resp[0] not in ("N", "Z", "E"):
        return None
    try:
        return float(resp[1:])
    except Exception:
        return None


def _query(ser, channel: str, cmd: str):
    return _parse(_send(ser, channel, cmd))


def _readback(ser, channel: str):
    return _query(ser, channel, "RFD"), _query(ser, channel, "RFX")


def main():
    parser = argparse.ArgumentParser(description="Log MFC setpoint/output after an SDC write")
    parser.add_argument("--channel", choices=["A", "B", "a", "b"], default=None)
    parser.add_argument("--target", type=float, default=None, help="Raw DMFC setting, 0-100")
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument(
        "--digital-control",
        choices=["never", "once"],
        default="once",
        help="Whether to send SRS before SFD in this test",
    )
    args = parser.parse_args()

    channel = (args.channel or input("Channel [A/B] [B]: ").strip() or "B").upper()
    if channel not in ("A", "B"):
        raise SystemExit("Channel must be A or B")
    target = args.target
    if target is None:
        raw = input("Target raw setting 0-100 [5]: ").strip()
        target = float(raw or "5")
    target = min(max(float(target), 0.0), 100.0)

    print("=== MFC STEP RESPONSE TEST ===")
    print("This WILL send one setpoint command and then log RFD/RFX over time.")
    print("Close the vendor DMFC utility first so the COM port is free.")
    print("If RFD jumps immediately but RFX is slow, the DMFC output is ramping/filtering.")
    print("If RFD itself is slow or unchanged, our command sequence differs from the vendor utility.")
    print()
    print("Channel=%s, target setting=%.2f, digital-control=%s" % (channel, target, args.digital_control))
    confirm = input("Type STEP to run, or anything else to abort: ").strip()
    if confirm != "STEP":
        print("Aborted.")
        return 2

    fs = float(MFC_FULL_SCALE_SCCM.get(channel, 100.0))
    with serial.Serial(
        COM_PORTS["mfc"],
        SERIAL_BAUD["mfc"],
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=0.5,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    ) as ser:
        time.sleep(0.2)
        before_sp, before_flow = _readback(ser, channel)
        print("Before: RFD=%.3f, RFX=%.3f, approx-flow=%.3f sccm" % (
            before_sp if before_sp is not None else float("nan"),
            before_flow if before_flow is not None else float("nan"),
            (before_flow or 0.0) * fs / 100.0,
        ))
        # SEC clears the DMFC error latch. Lab validation showed that invalid
        # candidate commands can leave RER=01 and prefix reads with E.
        resp = _send(ser, channel, "SEC")
        print("SEC -> %r" % resp)
        if args.digital_control == "once":
            resp = _send(ser, channel, "SRS")
            print("SRS -> %r" % resp)
        resp = _send(ser, channel, "SDC%.2f" % target)
        print("SDC%.2f -> %r" % (target, resp))
        print()
        print("time_s,RFD_setting,RFX_output_setting,approx_output_sccm")

        t0 = time.time()
        end = t0 + max(float(args.duration), 0.0)
        interval = max(float(args.interval), 0.05)
        while True:
            now = time.time()
            sp, flow = _readback(ser, channel)
            approx = (flow or 0.0) * fs / 100.0
            print("%.2f,%s,%s,%.4f" % (
                now - t0,
                "" if sp is None else "%.4f" % sp,
                "" if flow is None else "%.4f" % flow,
                approx,
            ))
            if now >= end:
                break
            time.sleep(interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
