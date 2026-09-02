# -*- coding: utf-8 -*-
"""
Watlow EZ-ZONE PM temperature controller driver (pywatlow, Standard Bus).

The controller on this system is used in Standard Bus mode rather than Modbus RTU.
pywatlow has a known instance-handling bug in older releases, so the read/write
request builders are patched at import time.
"""

import threading
import time


def _apply_pywatlow_patch():
    import pywatlow.watlow as pw

    _orig_read = pw.Watlow._buildReadRequest

    def _patched_read(self, dataParam, instance):
        if not isinstance(instance, str) or not all(
            c in "0123456789abcdefABCDEF" for c in str(instance)
        ):
            instance = format(1, "02x")
        return _orig_read(self, dataParam, instance)

    pw.Watlow._buildReadRequest = _patched_read

    _orig_write = getattr(pw.Watlow, "_buildWriteRequest", None)
    if _orig_write is not None:

        def _patched_write(self, dataParam, value, *args):
            args = list(args)
            if args:
                instance = args[-1]
                if not isinstance(instance, str) or not all(
                    c in "0123456789abcdefABCDEF" for c in str(instance)
                ):
                    args[-1] = format(1, "02x")
            else:
                args.append(format(1, "02x"))
            return _orig_write(self, dataParam, value, *args)

        pw.Watlow._buildWriteRequest = _patched_write


try:
    from pywatlow.watlow import Watlow as _Watlow

    _apply_pywatlow_patch()
    _PYWATLOW_OK = True
except ImportError:
    _PYWATLOW_OK = False


from config import (
    COM_PORTS,
    SERIAL_BAUD,
    WATLOW_MODBUS_ADDR,
    WATLOW_PID_HIGH,
    WATLOW_PID_LOW,
    WATLOW_PID_SWITCH_TEMP,
    TEMP_SAFETY_SHUTDOWN_SETPOINT_C,
)


class WatlowController:
    """
    Control a Watlow EZ-ZONE PM via pywatlow.
    """

    def __init__(self, port=None, baud=None, addr=None):
        self.port = port or COM_PORTS["temp"]
        self.baud = baud or SERIAL_BAUD["temp"]
        self.addr = addr or WATLOW_MODBUS_ADDR
        self._w = None
        # self._w wraps a raw pyserial port (self._w.serial), read/written
        # from both the temperature-safety-monitor background thread
        # (_start_temperature_safety_monitor's poll loop, which runs for
        # the full duration of every automated run) and the run thread
        # itself (set_temperature/wait_stable during a temperature ramp) --
        # concurrently, with no synchronization until this lock. Matches
        # driver_motor.py::MDriveMotor's own _io_lock for the identical
        # shared-serial-port-across-threads hazard; unlike that driver,
        # this one didn't have it. A Win7 faulthandler crash dump caught
        # the safety-monitor thread faulting (0xc0000005) inside this
        # driver's own read path (via pywatlow/crcmod) while the run
        # thread was concurrently active -- consistent with, though not
        # proven to be caused by, this race.
        self._io_lock = threading.RLock()

    def connect(self):
        if not _PYWATLOW_OK:
            raise ImportError("Run: pip install pywatlow")
        with self._io_lock:
            self._w = _Watlow(port=self.port, address=self.addr)
        print(f"[Temp] Connected on {self.port}")

    def disconnect(self):
        with self._io_lock:
            if self._w:
                try:
                    self._w.serial.close()
                except Exception:
                    pass
                self._w = None
                print("[Temp] Disconnected")

    @staticmethod
    def _f_to_c(value_f: float) -> float:
        return (value_f - 32.0) * 5.0 / 9.0

    @staticmethod
    def _c_to_f(value_c: float) -> float:
        return value_c * 9.0 / 5.0 + 32.0

    @staticmethod
    def _extract_value(result, op_name: str) -> float:
        if not isinstance(result, dict):
            raise RuntimeError(f"[Temp] {op_name} returned unexpected payload: {result!r}")
        if result.get("error") is not None:
            raise RuntimeError(f"[Temp] {op_name} failed: {result['error']}")
        if result.get("data") is None:
            raise RuntimeError(f"[Temp] {op_name} returned no data")
        return float(result["data"])

    def get_ramp_rate(self) -> float:
        with self._io_lock:
            if not hasattr(self._w, "readParam"):
                raise RuntimeError("[Temp] pywatlow readParam API not found")
            result = self._w.readParam(7003, float)
        return self._extract_value(result, "read ramp rate")

    def set_ramp_rate(self, ramp_c_per_min: float):
        if ramp_c_per_min <= 0:
            raise ValueError("Ramp rate must be positive")
        with self._io_lock:
            if not hasattr(self._w, "writeParam"):
                raise RuntimeError("[Temp] pywatlow writeParam API not found")
            # Watlow EZ-ZONE PM manual indicates:
            #   7015 = ramp scale select, 57 -> minutes
            #   7003 = ramp rate value in display units per minute
            scale_result = self._w.writeParam(7015, 57, int)
            self._extract_value(scale_result, "write ramp scale")
            rate_result = self._w.writeParam(7003, float(ramp_c_per_min), float)
            self._extract_value(rate_result, "write ramp rate")
            readback = self.get_ramp_rate()
        print(f"[Temp] Ramp rate -> {ramp_c_per_min:.3f} C/min (readback {readback:.3f} C/min)")

    def get_temperature(self) -> float:
        with self._io_lock:
            result = self._w.read("actual value")
        return self._f_to_c(self._extract_value(result, "read temperature"))

    def get_setpoint(self) -> float:
        with self._io_lock:
            result = self._w.read("setpoint 1")
        return self._f_to_c(self._extract_value(result, "read setpoint"))

    def set_temperature(self, target_c: float):
        target_f = self._c_to_f(target_c)
        with self._io_lock:
            if hasattr(self._w, "write"):
                result = self._w.write(target_f)
            elif hasattr(self._w, "writeParam"):
                result = self._w.writeParam(7001, target_f, float)
            else:
                raise RuntimeError("[Temp] pywatlow write API not found")
            self._extract_value(result, "write setpoint")
            self._apply_pid(target_c)
            readback_c = self.get_setpoint()
        print(f"[Temp] Setpoint -> {target_c:.1f} C (readback {readback_c:.1f} C)")

    def safe_shutdown(self, target_c: float = TEMP_SAFETY_SHUTDOWN_SETPOINT_C):
        """Drive the furnace back to a conservative setpoint after a safety trip."""
        try:
            self.set_temperature(float(target_c))
        except Exception as exc:
            raise RuntimeError(f"[Temp] safe shutdown failed: {exc}") from exc
        print(f"[Temp] Safety shutdown -> {float(target_c):.1f} C")

    def _apply_pid(self, target_c: float):
        pid = (
            WATLOW_PID_LOW
            if target_c < WATLOW_PID_SWITCH_TEMP
            else WATLOW_PID_HIGH
        )
        print(f"[Temp] PID -> P={pid['P']} I={pid['I']} D={pid['D']}")

    def wait_stable(
        self,
        target_c: float,
        tol=2.0,
        stable_time=120,
        poll=10,
        timeout=7200,
    ):
        print(f"[Temp] Waiting for stability at {target_c:.1f} +/- {tol} C ...")
        t_start = time.time()
        t_stable_start = None

        while True:
            if time.time() - t_start > timeout:
                raise TimeoutError(f"[Temp] Stability timeout after {timeout}s")

            pv = self.get_temperature()
            in_band = abs(pv - target_c) <= tol

            if in_band:
                if t_stable_start is None:
                    t_stable_start = time.time()
                held = time.time() - t_stable_start
                print(f"  PV={pv:.1f}C stable for {held:.0f}/{stable_time}s")
                if held >= stable_time:
                    print(f"[Temp] Stable at {pv:.1f} C")
                    return
            else:
                t_stable_start = None
                print(f"  PV={pv:.1f}C (target {target_c:.1f} +/- {tol})")

            time.sleep(poll)
