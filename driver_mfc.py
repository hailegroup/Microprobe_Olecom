# -*- coding: utf-8 -*-
"""
Aera FC-PA7800C MFC driver (pyserial, RS-485 over COM5).

Observed on the lab hardware:
  - Legacy '@007Q\\r' probing does not respond.
  - STX + hex-address reads do respond.
  - DMFC utility defaults to percent display.
  - Lab write/readback indicates RFX/RFD use raw 0-100 setting values,
    not sccm engineering units.

Read-only diagnostic results on the instrument PC:
  CH A: RFK -> N100.0,  RFX -> N0.02, RFD -> N0.0
  CH B: RFK -> N1000.0, RFX -> N0.09, RFD -> N0.0
"""

import threading
import time

import serial

from config import (
    COM_PORTS,
    SERIAL_BAUD,
    MFC_ADDRESS,
    MFC_FULL_SCALE_SCCM,
    MFC_WRITE_ENABLED,
    MFC_MIN_DIGITAL_SETTING_PERCENT,
    MFC_SETPOINT_TOLERANCE_PERCENT,
    MFC_SETPOINT_READBACK_TIMEOUT_S,
    MFC_SETPOINT_READBACK_POLL_S,
    MFC_SETPOINT_WRITE_RETRIES,
    MFC_SEND_DIGITAL_CONTROL_ON_WRITE,
)


MFC_DRIVER_VERSION = '20260616_sdc_fast_v12'


class AeraMFC:
    def __init__(self, port=None, baud=None, timeout=1):
        self.port = port or COM_PORTS['mfc']
        self.baud = baud or SERIAL_BAUD['mfc']
        self.timeout = timeout
        self.ser = None
        self._full_scale_cache = {}
        self._digital_control_sent = set()
        self._io_lock = threading.RLock()

    def connect(self):
        with self._io_lock:
            self.ser = serial.Serial(
                self.port,
                self.baud,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self.timeout,
            )
            time.sleep(0.2)
            print(f"[MFC] Connected on {self.port} @ {self.baud} bps")

    def disconnect(self):
        with self._io_lock:
            if self.ser and self.ser.is_open:
                self.ser.close()
                print("[MFC] Disconnected")

    def _hex_addr(self, channel: str) -> str:
        addr = MFC_ADDRESS[channel.upper()]
        return format(addr, '02x')

    def _write(self, channel: str, cmd: str):
        msg = f"\x02{self._hex_addr(channel)}{cmd}\r"
        self.ser.write(msg.encode('ascii'))
        self.ser.flush()
        time.sleep(0.015)

    def _read_response(self) -> str:
        data = self.ser.read_until(b'\r')
        return data.decode('ascii', errors='replace').strip()

    def _query(self, channel: str, cmd: str) -> str:
        with self._io_lock:
            self.ser.reset_input_buffer()
            self._write(channel, cmd)
            return self._read_response()

    def _write_check(self, channel: str, cmd: str):
        with self._io_lock:
            self._write(channel, cmd)
            resp = self._read_response()
            if 'OK' not in resp:
                raise RuntimeError(f"[MFC] Ch{channel} cmd={cmd!r} expected OK, got {resp!r}")
            return resp

    def _write_optional(self, channel: str, cmd: str):
        with self._io_lock:
            self._write(channel, cmd)
            resp = self._read_response()
            if 'OK' not in resp:
                print(f"[MFC] Ch{channel} optional cmd={cmd!r} got {resp!r}; continuing")
            return resp

    def _parse_value(self, channel: str, resp: str) -> float:
        if not resp:
            raise RuntimeError(f"[MFC] Ch{channel} no response")
        status = resp[0]
        # The first character is a status prefix. N is normal; E means the
        # value is still readable but an error latch is set (RER reports it).
        if status not in ('N', 'Z', 'E'):
            raise RuntimeError(f"[MFC] Ch{channel} status={status!r} resp={resp!r}")
        try:
            return float(resp[1:])
        except ValueError as exc:
            raise RuntimeError(f"[MFC] Ch{channel} unexpected response: {resp!r}") from exc

    def _get_full_scale_cached(self, channel: str) -> float:
        key = channel.upper()
        if key in self._full_scale_cache:
            return self._full_scale_cache[key]
        try:
            fs = self.get_full_scale(key)
            if fs > 0:
                self._full_scale_cache[key] = fs
                return fs
        except Exception:
            pass
        fs = MFC_FULL_SCALE_SCCM[key]
        self._full_scale_cache[key] = fs
        return fs

    def get_flow(self, channel: str) -> float:
        """Return raw DMFC flow output setting (0-100), not sccm."""
        return self.get_flow_setting(channel)

    def get_flow_setting(self, channel: str) -> float:
        resp = self._query(channel, 'RFX')
        return self._parse_value(channel, resp)

    def get_setpoint(self, channel: str) -> float:
        """Return raw DMFC setpoint setting (0-100), not sccm."""
        return self.get_setpoint_setting(channel)

    def get_setpoint_setting(self, channel: str) -> float:
        resp = self._query(channel, 'RFD')
        return self._parse_value(channel, resp)

    def setting_to_sccm(self, channel: str, setting: float) -> float:
        fs = self._get_full_scale_cached(channel)
        return float(setting) * fs / 100.0

    def sccm_to_setting(self, channel: str, sccm: float) -> float:
        fs = self._get_full_scale_cached(channel)
        if fs <= 0:
            raise RuntimeError(f"[MFC] Ch{channel} invalid full scale: {fs}")
        return float(sccm) * 100.0 / fs

    def _validate_setting(self, channel: str, setting: float) -> float:
        target = min(max(float(setting), 0.0), 100.0)
        min_nonzero = float(MFC_MIN_DIGITAL_SETTING_PERCENT)
        if 0.0 < target < min_nonzero:
            raise ValueError(
                f"[MFC] Ch{channel} requested setting={target:.2f} is below "
                f"the validated nonzero minimum {min_nonzero:.2f}. "
                "Use 0 or a larger setting. On the current lab MFCs, setting=2 "
                "has been the lowest validated nonzero target."
            )
        return target

    def set_setting(self, channel: str, setting: float):
        with self._io_lock:
            return self._set_setting_locked(channel, setting)

    def _set_setting_locked(self, channel: str, setting: float):
        if not MFC_WRITE_ENABLED:
            raise RuntimeError(
                "[MFC] Digital write commands are disabled in config.py because "
                "the Aera protocol still needs validation on the real hardware."
            )
        target = self._validate_setting(channel, setting)
        tol = float(MFC_SETPOINT_TOLERANCE_PERCENT)
        poll_s = max(float(MFC_SETPOINT_READBACK_POLL_S), 0.05)
        timeout_s = max(float(MFC_SETPOINT_READBACK_TIMEOUT_S), 0.0)
        retries = max(int(MFC_SETPOINT_WRITE_RETRIES), 0)
        last_readback = None
        channel = channel.upper()
        for attempt in range(retries + 1):
            self.clear_error(channel, required=False)
            if self._should_send_digital_control(channel):
                self.flow_mode(channel, required=True)
                self._digital_control_sent.add(channel)
            for cmd in self._setpoint_write_commands(target):
                self._write_check(channel, cmd)
                deadline = time.time() + timeout_s
                readback = self.get_setpoint_setting(channel)
                while abs(readback - target) > tol and time.time() < deadline:
                    time.sleep(poll_s)
                    readback = self.get_setpoint_setting(channel)
                last_readback = readback
                if abs(readback - target) <= tol:
                    print(
                        f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} target setting={target:.2f}, "
                        f"setpoint readback={readback:.2f} "
                        f"(approx {self.setting_to_sccm(channel, readback):.2f} sccm)"
                    )
                    return
                print(
                    f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} cmd={cmd!r} did not stick; "
                    f"target={target:.2f}, readback={readback:.2f}"
                )
            if attempt < retries:
                print(
                    f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} retrying setpoint write "
                    f"({attempt + 1}/{retries}); target={target:.2f}, readback={readback:.2f}"
                )
                time.sleep(0.5)
        raise RuntimeError(
            f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} setpoint write did not stick: "
            f"target setting={target:.2f}, readback={float(last_readback):.2f}. "
            f"Readback was polled for {timeout_s:.1f} s x {retries + 1} attempt(s). "
            "Do not enable unattended gas automation until this is fixed."
        )

    def set_flow(self, channel: str, setting: float):
        """Compatibility alias: value is now raw 0-100 DMFC setting."""
        self.set_setting(channel, setting)

    def set_setting_fast(self, channel: str, setting: float):
        """Send a raw setpoint command without blocking on readback verification.

        This is for manual GUI control, where the user wants DMFC-utility style
        responsiveness and can inspect RFD/RFX readbacks immediately.
        """
        with self._io_lock:
            if not MFC_WRITE_ENABLED:
                raise RuntimeError(
                    "[MFC] Digital write commands are disabled in config.py because "
                    "the Aera protocol still needs validation on the real hardware."
                )
            channel = channel.upper()
            target = self._validate_setting(channel, setting)
            self.clear_error(channel, required=False)
            if self._should_send_digital_control(channel):
                self.flow_mode(channel, required=True)
                self._digital_control_sent.add(channel)
            for cmd in self._setpoint_write_commands(target):
                self._write_check(channel, cmd)
            print(f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} fast target sent: setting={target:.2f}")

    def get_full_scale(self, channel: str) -> float:
        resp = self._query(channel, 'RFK')
        return self._parse_value(channel, resp)

    def close_valve(self, channel: str, required=True):
        if required:
            self._write_check(channel, 'SVC')
        else:
            self._write_optional(channel, 'SVC')

    def open_valve(self, channel: str, required=True):
        if required:
            self._write_check(channel, 'SVO')
        else:
            self._write_optional(channel, 'SVO')

    def flow_mode(self, channel: str, required=True):
        if required:
            self._write_check(channel, 'SRS')
        else:
            self._write_optional(channel, 'SRS')

    def clear_error(self, channel: str, required=True):
        if required:
            self._write_check(channel, 'SEC')
        else:
            self._write_optional(channel, 'SEC')

    def get_error_code(self, channel: str) -> float:
        resp = self._query(channel, 'RER')
        return self._parse_value(channel, resp)

    def set_flow_all(self, setting_A: float, setting_B: float):
        self.set_setting('A', setting_A)
        self.set_setting('B', setting_B)

    def _should_send_digital_control(self, channel: str) -> bool:
        policy = MFC_SEND_DIGITAL_CONTROL_ON_WRITE
        if isinstance(policy, str):
            normalized = policy.strip().lower()
            if normalized in ('once', 'first'):
                return channel.upper() not in self._digital_control_sent
            if normalized in ('always', 'true', 'yes', '1'):
                return True
            if normalized in ('never', 'false', 'no', '0', 'off'):
                return False
        return bool(policy)

    def _setpoint_write_commands(self, target: float):
        # On the Hitachi/Aera DMFCs in this lab, SFD replies OK but does not
        # latch the setpoint. SDC is the command that changes RFD immediately.
        return (f'SDC{float(target):.2f}',)

    def _fast_zero_setpoint(self, channel: str, target: float):
        tol = float(MFC_SETPOINT_TOLERANCE_PERCENT)
        for cmd in self._setpoint_write_commands(target):
            self._write_check(channel, cmd)
            time.sleep(0.15)
            readback = self.get_setpoint_setting(channel)
            print(
                f"[MFC {MFC_DRIVER_VERSION}] Ch{channel} zero cmd={cmd!r}, "
                f"RFD={readback:.2f}",
                flush=True,
            )
            if abs(readback - target) <= tol:
                return
