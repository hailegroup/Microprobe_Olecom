# -*- coding: utf-8 -*-
"""
Interactive Aera MFC write-command variant tester.

Use this only after read-only diagnostics pass. It tries several plausible
raw setpoint-write command formats and reports whether RFD changes. This is meant
to identify the exact command format before enabling unattended GUI gas control.
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

from config import COM_PORTS, SERIAL_BAUD, MFC_ADDRESS


def _hex_addr(channel: str) -> str:
    return format(MFC_ADDRESS[channel.upper()], "02x")


def _send_raw(ser, channel: str, cmd: str, wait_s=0.12) -> str:
    msg = f"\x02{_hex_addr(channel)}{cmd}\r".encode("ascii")
    ser.reset_input_buffer()
    ser.write(msg)
    ser.flush()
    time.sleep(wait_s)
    return ser.readline().decode("ascii", errors="replace").strip()


def _parse_value(resp: str):
    if not resp:
        return None
    if resp[0] not in ("N", "Z", "E"):
        return None
    try:
        return float(resp[1:])
    except Exception:
        return None


def _query_value(ser, channel: str, cmd: str):
    return _parse_value(_send_raw(ser, channel, cmd))


def _readback(ser, channel: str):
    return {
        "fs": _query_value(ser, channel, "RFK"),
        "flow": _query_value(ser, channel, "RFX"),
        "sp": _query_value(ser, channel, "RFD"),
    }


def _candidate_commands(target_setting: float):
    target = float(target_setting)
    values = []
    for value in (target,):
        values.extend([
            f"{value:g}",
            f"{value:.1f}",
            f"{value:.2f}",
            f"{value:05.2f}",
            f"{value:06.2f}",
            f"{value:07.2f}",
            f"+{value:.2f}",
            f"+{value:06.2f}",
        ])

    # Preserve order while dropping duplicates.
    deduped_values = []
    for value in values:
        if value not in deduped_values:
            deduped_values.append(value)

    commands = []
    # Lab validation on 2026-06-16: SFD replies OK on the Hitachi/Aera DMFCs
    # but does not latch RFD; SDC is the effective setpoint command.
    prefixes = ["SDC", "SDC ", "SDC=", "SDC+", "SFD", "SFD ", "SFD=", "SFD+"]
    for value in deduped_values:
        for prefix in prefixes:
            if prefix.endswith("+") and value.startswith("+"):
                commands.append(prefix[:-1] + value)
            else:
                commands.append(prefix + value)

    # A few integer-scaled encodings sometimes used by older controllers.
    commands.extend([
        f"SDC{int(round(target)):d}",
        f"SDC{int(round(target * 10)):d}",
        f"SDC{int(round(target * 100)):d}",
        f"SFD{int(round(target)):d}",
        f"SFD{int(round(target * 10)):d}",
        f"SFD{int(round(target * 100)):d}",
    ])

    deduped = []
    for cmd in commands:
        if cmd not in deduped:
            deduped.append(cmd)
    return deduped


def main():
    parser = argparse.ArgumentParser(description="Interactive MFC setpoint command variant tester")
    parser.add_argument("--channel", default="A", choices=["A", "B", "a", "b"], help="Channel to test first")
    parser.add_argument("--target", type=float, default=5.0, help="Raw setpoint setting target 0-100")
    parser.add_argument("--settle", type=float, default=0.25, help="Wait after each write before RFD readback")
    parser.add_argument("--digital-control", action="store_true", help="Send SRS before testing variants")
    parser.add_argument("--open-valve", action="store_true", help="Also send SRS and SVO before variants")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of candidate commands; 0 means all")
    args = parser.parse_args()

    channel = args.channel.upper()
    print("=== MFC WRITE VARIANT TEST ===")
    print("This WILL send candidate setpoint commands to one MFC channel.")
    print("Start with a small raw setting, for example 5. Values below ~2 may not act/read reliably.")
    print("If any variant changes RFD, the script will try to reset that channel to 0 using the same format.")
    if args.digital_control:
        print("WARNING: --digital-control will send SRS before testing.")
    if args.open_valve:
        print("WARNING: --open-valve will send SRS and SVO before testing.")
    print()
    print(f"Channel={channel}, target setting={args.target:g}")
    confirm = input("Type VARIANT to run, or anything else to abort: ").strip()
    if confirm != "VARIANT":
        print("Aborted.")
        return 2
    if 0.0 < float(args.target) < 2.0:
        print("Target setting is below the observed reliable nonzero minimum of ~2. Use 0 or >=2.")
        return 2

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
        before = _readback(ser, channel)
        print(
            f"Before: full-scale={before['fs']} sccm, "
            f"flow-setting={before['flow']}, setpoint-setting={before['sp']}"
        )

        # Clear stale error latch before variant probing; invalid candidate
        # commands can leave RER=01 and prefix later reads with E.
        resp = _send_raw(ser, channel, "SEC")
        print(f"pre-cmd 'SEC' -> {resp!r}")

        if args.digital_control or args.open_valve:
            pre_cmds = ["SRS"]
            if args.open_valve:
                pre_cmds.append("SVO")
            for cmd in pre_cmds:
                resp = _send_raw(ser, channel, cmd)
                print(f"pre-cmd {cmd!r} -> {resp!r}")

        commands = _candidate_commands(args.target)
        if args.limit and args.limit > 0:
            commands = commands[:args.limit]

        baseline_sp = before["sp"]
        success = None
        for idx, cmd in enumerate(commands, start=1):
            resp = _send_raw(ser, channel, cmd)
            time.sleep(max(args.settle, 0.0))
            after = _readback(ser, channel)
            delta = None if baseline_sp is None or after["sp"] is None else after["sp"] - baseline_sp
            print(
                f"{idx:03d}. {cmd!r} -> {resp!r}; "
                f"RFD-setting={after['sp']} flow-setting={after['flow']} delta={delta}"
            )
            if after["sp"] is not None and abs(after["sp"] - args.target) <= max(0.1, abs(args.target) * 0.05):
                success = cmd
                print(f"SUCCESS: {cmd!r} produced requested setpoint setting.")
                break
            if after["sp"] is not None and baseline_sp is not None and abs(after["sp"] - baseline_sp) > 0.1:
                success = cmd
                print(f"PARTIAL SUCCESS: {cmd!r} changed RFD from baseline.")
                break

        if success:
            reset = input("Type RESET to try resetting this channel to 0 with the same command style: ").strip()
            if reset == "RESET":
                reset_cmd = success.replace(str(args.target), "0")
                # More robust reset for the common SFD{value} family.
                if success.startswith("SFD"):
                    sep = ""
                    if success.startswith("SFD "):
                        sep = " "
                    elif success.startswith("SFD="):
                        sep = "="
                    elif success.startswith("SFD+"):
                        sep = "+"
                    reset_cmd = "SFD" + sep + "0"
                resp = _send_raw(ser, channel, reset_cmd)
                time.sleep(max(args.settle, 0.0))
                final = _readback(ser, channel)
                print(
                    f"reset {reset_cmd!r} -> {resp!r}; "
                    f"RFD-setting={final['sp']} flow-setting={final['flow']}"
                )
        else:
            print("No candidate changed RFD. Do NOT enable MFC_WRITE_ENABLED yet.")
            print("Next likely checks: remote/digital setpoint mode, valve/mode commands, or vendor utility protocol capture.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
