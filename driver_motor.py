# -*- coding: utf-8 -*-
"""
IMS MDrive 23 Plus stepper motor driver (pyserial, party-line RS-485 over COM9).

Protocol confirmed from:
  - NI I/O Trace of LabVIEW program
  - XY_table/motor_tools.py (toomanycats/XY_table on GitHub)

Party-line RS-485: each axis has a 1-char address (X, Y, Z).
Commands prefixed with axis letter, terminated with CR.
Motor echoes every command back before sending the response.

Key commands (axis prefix omitted for clarity):
  MA {steps}   absolute move
  MR {steps}   relative move
  SL 0         stop
  HM 1         home
  PR P         read position in steps  (also PR CL works)
  PR MP        1 = moving to position, 0 = stopped
  PR SN        serial number
"""

import re
import time
import threading
import serial
from config import COM_PORTS, SERIAL_BAUD, STEPS_PER_MM, STAGE_SAFE_MOVE


class UnsafeStageMoveError(RuntimeError):
    """Raised when a requested stage move cannot be executed safely."""


class MDriveMotor:
    """
    Controls IMS MDrive axes on a shared RS-485 bus.

    Usage
    -----
    motor = MDriveMotor()
    motor.connect()
    motor.move_abs('X', 10.0)
    motor.wait_done('X')
    print(motor.get_position('X'))
    motor.disconnect()
    """

    def __init__(self, port=None, baud=None, timeout=0.5):
        self.port    = port or COM_PORTS['motor']
        self.baud    = baud or SERIAL_BAUD['motor']
        self.timeout = timeout
        self.ser     = None
        self._io_lock = threading.RLock()

    # ── Connection ─────────────────────────────────────────────────────────
    def connect(self):
        with self._io_lock:
            self.disconnect()
            last_exc = None
            for attempt in range(1, 4):
                try:
                    self.ser = serial.Serial(
                        self.port, self.baud,
                        bytesize=8, parity='N', stopbits=1,
                        timeout=self.timeout,
                        write_timeout=self.timeout,
                    )
                    time.sleep(0.2)
                    try:
                        self.ser.reset_input_buffer()
                        self.ser.reset_output_buffer()
                    except Exception:
                        pass
                    print(f"[Motor] Connected on {self.port}")
                    return
                except Exception as exc:
                    last_exc = exc
                    self.ser = None
                    time.sleep(0.5 * attempt)
            raise RuntimeError(
                f"[Motor] could not open {self.port}: {last_exc}. "
                "Close any other GUI/LabVIEW/terminal using this COM port, "
                "or unplug/replug the USB-serial cable if Windows did not release it."
            )

    def disconnect(self):
        with self._io_lock:
            ser = self.ser
            self.ser = None
            if ser and ser.is_open:
                try:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                except Exception:
                    pass
                try:
                    ser.close()
                finally:
                    time.sleep(0.2)
                    print("[Motor] Disconnected")

    # ── Low-level send/recv ────────────────────────────────────────────────
    def _send(self, axis: str, cmd: str):
        """Send command to axis (e.g. axis='X', cmd='MA 1000')."""
        with self._io_lock:
            if self.ser is None or not self.ser.is_open:
                raise RuntimeError("[Motor] serial port is not connected")
            self.ser.reset_input_buffer()
            msg = f"{axis}{cmd}\r\n".encode('ascii')
            self.ser.write(msg)
            self.ser.flush()
            time.sleep(0.1)

    def _query(self, axis: str, cmd: str) -> str:
        """Send command, skip echo line(s), return the value line."""
        with self._io_lock:
            self._send(axis, cmd)
            echo = f"{axis}{cmd}".lower().strip()
            lines = self.ser.readlines()
            for line in lines:
                text = line.decode('ascii', errors='replace').strip()
                if text and text.lower() != echo and text != '>':
                    return text
            return ''

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
        """Move axis by relative distance in mm."""
        steps = self._mm_to_steps(axis, delta_mm)
        self._send(axis, f"MR {steps}")

    def stop(self, axis: str):
        self._send(axis, "SL 0")

    def home(self, axis: str):
        self._send(axis, "HM 1")

    # ── Position readback ──────────────────────────────────────────────────
    def get_position(self, axis: str) -> float:
        """Return current position in mm."""
        resp = self._query(axis, "PR P")
        try:
            # Strip any non-numeric prefix, accept negative values
            m = re.search(r'-?\d+', resp)
            if m:
                return self._steps_to_mm(axis, int(m.group()))
        except Exception:
            pass
        print(f"[Motor] Unexpected position response: '{resp}'")
        return float('nan')

    # ── Motion complete ────────────────────────────────────────────────────
    def is_moving(self, axis: str) -> bool:
        """Return True if axis is still moving (uses PR MP flag)."""
        resp = self._query(axis, "PR MP")
        return resp.strip() == '1'

    def wait_done(self, axis: str, poll_interval=0.2, timeout_s=120):
        """Block until axis stops moving."""
        t0 = time.time()
        while self.is_moving(axis):
            if time.time() - t0 > timeout_s:
                raise TimeoutError(f"[Motor] Axis {axis} timeout ({timeout_s}s)")
            time.sleep(poll_interval)

    def move_abs_wait(self, axis: str, pos_mm: float, **kwargs):
        self.move_abs(axis, pos_mm)
        self.wait_done(axis, **kwargs)

    def move_rel_wait(self, axis: str, delta_mm: float, **kwargs):
        self.move_rel(axis, delta_mm)
        self.wait_done(axis, **kwargs)

    @staticmethod
    def _is_finite_number(value):
        try:
            return value is not None and float(value) == float(value)
        except Exception:
            return False

    @classmethod
    def plan_safe_xyz_move(
        cls,
        current_positions,
        target_positions,
        *,
        tol_mm=0.01,
        safe_move=None,
    ):
        """
        Return an ordered list of (axis, target_mm) moves.

        When safe move is enabled and XY changes are requested, the planner will:
          1. lift Z to a travel-safe clearance,
          2. move X/Y,
          3. move back to the target Z.
        """
        safe_cfg = dict(STAGE_SAFE_MOVE)
        if safe_move:
            safe_cfg.update(safe_move)

        current = {
            axis: current_positions.get(axis)
            for axis in ("X", "Y", "Z")
        }
        target = {
            axis: target_positions.get(axis)
            for axis in ("X", "Y", "Z")
        }

        def changed(axis):
            if not cls._is_finite_number(target.get(axis)):
                return False
            if not cls._is_finite_number(current.get(axis)):
                return True
            return abs(float(current[axis]) - float(target[axis])) > tol_mm

        x_changed = changed("X")
        y_changed = changed("Y")
        z_changed = changed("Z")
        moves = []

        if not safe_cfg.get("enabled", False):
            for axis in ("X", "Y", "Z"):
                if changed(axis):
                    moves.append((axis, float(target[axis])))
            return moves

        xy_changed = x_changed or y_changed
        current_z = current.get("Z")
        target_z = target.get("Z") if cls._is_finite_number(target.get("Z")) else current_z
        clearance_z = safe_cfg.get("clearance_z_mm")
        if cls._is_finite_number(clearance_z):
            clearance_z = float(clearance_z)
        elif xy_changed and cls._is_finite_number(current_z) and cls._is_finite_number(target_z):
            lift_delta = abs(float(safe_cfg.get("lift_delta_mm", 0.0) or 0.0))
            if bool(safe_cfg.get("positive_z_is_up", True)):
                clearance_z = max(float(current_z), float(target_z)) + lift_delta
            else:
                clearance_z = min(float(current_z), float(target_z)) - lift_delta
        elif xy_changed:
            raise UnsafeStageMoveError(
                "Safe XY travel requires either an absolute clearance_z_mm or a readable current Z position."
            )

        if xy_changed and cls._is_finite_number(clearance_z):
            if (not cls._is_finite_number(current_z)) or abs(float(current_z) - float(clearance_z)) > tol_mm:
                moves.append(("Z", float(clearance_z)))

        for axis in ("X", "Y"):
            if changed(axis):
                moves.append((axis, float(target[axis])))

        if cls._is_finite_number(target.get("Z")):
            reference_z = clearance_z if cls._is_finite_number(clearance_z) and xy_changed else current_z
            if (not cls._is_finite_number(reference_z)) or abs(float(reference_z) - float(target["Z"])) > tol_mm:
                moves.append(("Z", float(target["Z"])))

        return moves

    @staticmethod
    def _log_planned_move(log_fn, axis, prev, target):
        if log_fn is None:
            return
        if prev is None or prev != prev:
            log_fn(f"  Moving {axis}: -> {target:.3f} mm")
        else:
            log_fn(f"  Moving {axis}: {prev:.3f} -> {target:.3f} mm")

    def move_xyz_safe(
        self,
        *,
        x_mm=None,
        y_mm=None,
        z_mm=None,
        tol_mm=0.01,
        safe_move=None,
        current_positions=None,
        log_fn=None,
    ):
        if current_positions is None:
            current_positions = {}
            for axis, target in (("X", x_mm), ("Y", y_mm), ("Z", z_mm)):
                if target is None:
                    current_positions[axis] = None
                    continue
                current_positions[axis] = self.get_position(axis)
        else:
            current_positions = dict(current_positions)

        target_positions = {"X": x_mm, "Y": y_mm, "Z": z_mm}
        moves = self.plan_safe_xyz_move(
            current_positions,
            target_positions,
            tol_mm=tol_mm,
            safe_move=safe_move,
        )
        settle_s = float(dict(STAGE_SAFE_MOVE, **(safe_move or {})).get("settle_s", 0.0) or 0.0)
        for axis, target in moves:
            self._log_planned_move(log_fn, axis, current_positions.get(axis), target)
            self.move_abs_wait(axis, target)
            if settle_s > 0:
                time.sleep(settle_s)
            current_positions[axis] = target
        return moves
