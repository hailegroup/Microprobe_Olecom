# -*- coding: utf-8 -*-
"""
IMS MDrive stepper motor driver (pyserial, party-line RS-485 over COM9).

MDrive uses a simple ASCII command set.
Each axis is addressed by a single character (e.g., 'X', 'Y', 'Z').
Commands are terminated with CR (\r).

Reference: MDrive Motion Control Programming Manual
"""

import time
import serial
from config import COM_PORTS, SERIAL_BAUD, STEPS_PER_MM


class MDriveMotor:
    """
    Controls one or more IMS MDrive axes on a shared RS-485 bus.

    Usage
    -----
    motor = MDriveMotor()
    motor.connect()
    motor.move_abs('X', 10.0)   # move X to 10.0 mm
    motor.move_rel('Z', -0.5)   # move Z by -0.5 mm
    motor.wait_done('X')
    motor.disconnect()
    """

    def __init__(self, port=None, baud=None, timeout=5):
        self.port    = port or COM_PORTS['motor']
        self.baud    = baud or SERIAL_BAUD['motor']
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
        print(f"[Motor] Connected on {self.port}")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("[Motor] Disconnected")

    # ── Low-level send/recv ────────────────────────────────────────────────
    def _send(self, axis: str, cmd: str):
        """Send a command to a specific axis (e.g., axis='X', cmd='MA 0')."""
        full_cmd = f"{axis}{cmd}\r"
        self.ser.write(full_cmd.encode('ascii'))
        time.sleep(0.05)

    def _query(self, axis: str, cmd: str) -> str:
        """Send command and read one line response."""
        self._send(axis, cmd)
        resp = self.ser.readline().decode('ascii', errors='replace').strip()
        return resp

    # ── Unit conversion ────────────────────────────────────────────────────
    def _mm_to_steps(self, axis: str, mm: float) -> int:
        return int(round(mm * STEPS_PER_MM[axis.upper()]))

    def _steps_to_mm(self, axis: str, steps: int) -> float:
        return steps / STEPS_PER_MM[axis.upper()]

    # ── Motion commands ────────────────────────────────────────────────────
    def move_abs(self, axis: str, pos_mm: float):
        """Move axis to absolute position in mm."""
        steps = self._mm_to_steps(axis, pos_mm)
        self._send(axis, f"MA {steps}")

    def move_rel(self, axis: str, delta_mm: float):
        """Move axis by a relative distance in mm."""
        steps = self._mm_to_steps(axis, delta_mm)
        self._send(axis, f"MR {steps}")

    def stop(self, axis: str):
        """Stop motion on axis."""
        self._send(axis, "SL 0")

    def home(self, axis: str):
        """Execute homing sequence on axis."""
        self._send(axis, "HM 1")

    # ── Position readback ──────────────────────────────────────────────────
    def get_position(self, axis: str) -> float:
        """Return current position in mm."""
        resp = self._query(axis, "PR P")
        try:
            steps = int(resp)
            return self._steps_to_mm(axis, steps)
        except ValueError:
            print(f"[Motor] Unexpected position response: '{resp}'")
            return float('nan')

    # ── Motion complete ────────────────────────────────────────────────────
    def is_moving(self, axis: str) -> bool:
        """Return True if axis is still in motion."""
        resp = self._query(axis, "PR MV")
        return resp.strip() == '1'

    def wait_done(self, axis: str, poll_interval=0.1, timeout_s=120):
        """Block until axis stops moving."""
        t0 = time.time()
        while self.is_moving(axis):
            if time.time() - t0 > timeout_s:
                raise TimeoutError(f"[Motor] Axis {axis} motion timeout ({timeout_s}s)")
            time.sleep(poll_interval)

    def move_abs_wait(self, axis: str, pos_mm: float, **kwargs):
        """Move to absolute position and wait for completion."""
        self.move_abs(axis, pos_mm)
        self.wait_done(axis, **kwargs)

    def move_rel_wait(self, axis: str, delta_mm: float, **kwargs):
        """Move by relative distance and wait for completion."""
        self.move_rel(axis, delta_mm)
        self.wait_done(axis, **kwargs)
