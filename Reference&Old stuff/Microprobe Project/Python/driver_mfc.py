# -*- coding: utf-8 -*-
"""
Aera MFC driver — HML DMFC digital protocol (COM5 / 9600 bps).

Hardware confirmed from HML DMFC utility tool v2.0:
  ID 007  S/N C090818032  100  sccm N2  Digital mode
  ID 008  S/N J080310023  1000 sccm N2  Digital mode

Aera HML digital protocol (RS-232 ASCII):
  Set setpoint  : "@<ID>S<xxxxx>\r"   (flow in integer steps, 0–64000)
  Read actual   : "@<ID>Q\r"          → "@<ID>+<actual> <setpt> <temp> ...\r\n"
  Read setpoint : "@<ID>R\r"          → "@<ID>+<setpt>\r\n"
  Open valve    : "@<ID>O\r"
  Close valve   : "@<ID>C\r"

Flow value encoding: 0–64000 spans 0–100% full scale.
  sccm → steps : int(sccm / FS_sccm * 64000)
  steps → sccm : steps / 64000 * FS_sccm

The Q (query) response fields (space-separated after address prefix):
  [0] actual flow (steps)
  [1] setpoint   (steps)
  [2] temperature (°C, signed)
  [3] status byte

NOTE: If the commands above produce no response, try the alternate short form:
  Set: "@<ID>S<xxxxx>\r"  →  some firmware expects zero-padded 5-digit int
  Query: "@<ID>Q\r"
  Use probe_protocol() below to test what the device responds to.
"""

import time
import serial
from config import COM_PORTS, SERIAL_BAUD, MFC_ADDRESS, MFC_FULL_SCALE_SCCM

_STEPS_MAX = 64000   # full-scale step count for Aera HML protocol


class AeraMFC:
    """
    Controls two Aera MFCs (channel A/B) on a shared RS-232 bus.

    Usage
    -----
    mfc = AeraMFC()
    mfc.connect()
    mfc.set_flow('A', 50.0)    # set Gas A (100 sccm MFC) to 50 sccm
    print(mfc.get_flow('A'))   # read actual flow in sccm
    mfc.set_flow('B', 500.0)   # set Gas B (1000 sccm MFC) to 500 sccm
    mfc.disconnect()
    """

    def __init__(self, port=None, baud=None, timeout=2):
        self.port    = port or COM_PORTS['mfc']
        self.baud    = baud or SERIAL_BAUD['mfc']
        self.timeout = timeout
        self.ser     = None

    # ── Connection ─────────────────────────────────────────────────────────
    def connect(self):
        self.ser = serial.Serial(
            self.port, self.baud,
            bytesize=8, parity='N', stopbits=1,
            timeout=self.timeout
        )
        time.sleep(0.2)
        print(f"[MFC] Connected on {self.port} @ {self.baud} bps")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("[MFC] Disconnected")

    # ── Low-level send/recv ────────────────────────────────────────────────
    def _send(self, channel: str, cmd: str):
        addr = MFC_ADDRESS[channel.upper()]
        msg  = f"@{addr:03d}{cmd}\r"
        self.ser.write(msg.encode('ascii'))
        time.sleep(0.05)

    def _query(self, channel: str, cmd: str) -> str:
        self.ser.reset_input_buffer()
        self._send(channel, cmd)
        resp = self.ser.readline().decode('ascii', errors='replace').strip()
        return resp

    def _strip_prefix(self, channel: str, resp: str) -> str:
        """Remove '@007' or '@008' prefix from response."""
        addr = MFC_ADDRESS[channel.upper()]
        prefix = f"@{addr:03d}"
        if resp.startswith(prefix):
            resp = resp[len(prefix):]
        return resp.strip()

    # ── Unit conversion ────────────────────────────────────────────────────
    def _sccm_to_steps(self, channel: str, sccm: float) -> int:
        fs = MFC_FULL_SCALE_SCCM[channel.upper()]
        return int(round(min(sccm, fs) / fs * _STEPS_MAX))

    def _steps_to_sccm(self, channel: str, steps: int) -> float:
        fs = MFC_FULL_SCALE_SCCM[channel.upper()]
        return steps / _STEPS_MAX * fs

    # ── Flow control ───────────────────────────────────────────────────────
    def set_flow(self, channel: str, sccm: float):
        """Set flow rate for channel ('A' or 'B') in sccm."""
        steps = self._sccm_to_steps(channel, sccm)
        self._send(channel, f"S{steps:05d}")
        print(f"[MFC] Ch{channel} → {sccm:.2f} sccm  (steps={steps})")

    def get_flow(self, channel: str) -> float:
        """Read actual flow rate for channel in sccm (from Q response field 0)."""
        resp = self._query(channel, "Q")
        data = self._strip_prefix(channel, resp)
        try:
            # Response: "+<actual> <setpt> <temp> ..."
            parts = data.lstrip('+').split()
            steps = int(parts[0])
            return self._steps_to_sccm(channel, steps)
        except (ValueError, IndexError):
            print(f"[MFC] Unexpected Q response: '{resp}'")
            return float('nan')

    def get_setpoint(self, channel: str) -> float:
        """Read current setpoint for channel in sccm (from Q response field 1)."""
        resp = self._query(channel, "Q")
        data = self._strip_prefix(channel, resp)
        try:
            parts = data.lstrip('+').split()
            steps = int(parts[1])
            return self._steps_to_sccm(channel, steps)
        except (ValueError, IndexError):
            print(f"[MFC] Unexpected Q response: '{resp}'")
            return float('nan')

    def close_valve(self, channel: str):
        """Fully close valve (zero flow)."""
        self._send(channel, "C")

    def open_valve(self, channel: str):
        """Open valve to current setpoint."""
        self._send(channel, "O")

    def set_flow_all(self, sccm_A: float, sccm_B: float):
        """Set both channels."""
        self.set_flow('A', sccm_A)
        self.set_flow('B', sccm_B)

    # ── Protocol probe (run once to verify commands) ───────────────────────
    def probe_protocol(self, channel: str):
        """
        Send test commands and print raw responses.
        Run this interactively first to verify the protocol is correct.
        """
        addr = MFC_ADDRESS[channel.upper()]
        print(f"\n[MFC] Probing channel {channel} (addr={addr}) ...")
        for cmd in ['Q', 'R', 'S00000', 'S32000']:
            msg = f"@{addr:03d}{cmd}\r"
            self.ser.reset_input_buffer()
            self.ser.write(msg.encode('ascii'))
            time.sleep(0.2)
            resp = self.ser.read(self.ser.in_waiting or 64)
            print(f"  sent: {msg!r}  →  recv: {resp!r}")
