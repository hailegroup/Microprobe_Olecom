# -*- coding: utf-8 -*-
"""
Read-only diagnostic for the Aera MFC serial protocol.

This script does not send any setpoint or valve-changing commands.
It only probes the controller using two candidate protocol families:

1. Legacy ASCII style: @007Q\r, @008Q\r, ...
2. STX + hex-address style used in the current driver.

Use this while the vendor DMFC utility is closed so COM5 is free.
"""

import time

import serial

from config import COM_PORTS, SERIAL_BAUD


PORT = COM_PORTS["mfc"]
BAUD = SERIAL_BAUD["mfc"]


def _read_all(ser, wait_s=0.25):
    time.sleep(wait_s)
    chunks = []
    while True:
        n = ser.in_waiting
        if n <= 0:
            break
        chunks.append(ser.read(n))
        time.sleep(0.02)
    return b"".join(chunks)


def _query_plain(ser, payload: bytes, wait_s=0.25):
    ser.reset_input_buffer()
    ser.write(payload)
    ser.flush()
    return _read_all(ser, wait_s=wait_s)


def run():
    print(f"Opening {PORT} @ {BAUD} bps")
    with serial.Serial(
        PORT,
        BAUD,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=0.3,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    ) as ser:
        time.sleep(0.2)

        print("\n=== Legacy @-protocol probes ===")
        for cmd in [
            b"@007Q\r",
            b"@008Q\r",
            b"@007R\r",
            b"@008R\r",
            b"@007?\r",
            b"@008?\r",
        ]:
            resp = _query_plain(ser, cmd, wait_s=0.35)
            print(f"{cmd!r} -> {resp!r}")

        print("\n=== STX protocol read-only probes ===")
        for cmd in [
            b"\x0207RFK\r",
            b"\x0207RFX\r",
            b"\x0207RFD\r",
            b"\x0208RFK\r",
            b"\x0208RFX\r",
            b"\x0208RFD\r",
        ]:
            resp = _query_plain(ser, cmd, wait_s=0.20)
            print(f"{cmd!r} -> {resp!r}")


if __name__ == "__main__":
    run()
