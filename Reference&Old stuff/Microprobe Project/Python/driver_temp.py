# -*- coding: utf-8 -*-
"""
Watlow EZ-ZONE temperature controller driver (pymodbus, Modbus RTU over COM3).

Register map (EZ-ZONE PM, Modbus RTU):
  - Setpoint read/write : 7219 (float, 2 registers, big-endian)
  - Process value (PV)  : 7037 (float, 2 registers)
  - PID P gain          : 7057 (float, 2 registers)
  - PID I (reset)       : 7059 (float, 2 registers)
  - PID D (rate)        : 7061 (float, 2 registers)

All floats are IEEE-754 32-bit stored as two 16-bit holding registers.
Address offset: Watlow uses 1-based display; pymodbus uses 0-based → subtract 1.
"""

import time
import struct
import serial
from pymodbus.client import ModbusSerialClient
from config import COM_PORTS, SERIAL_BAUD, WATLOW_MODBUS_ADDR, WATLOW_PID_LOW, WATLOW_PID_HIGH, WATLOW_PID_SWITCH_TEMP


def _regs_to_float(r0: int, r1: int) -> float:
    """Convert two 16-bit registers (big-endian word order) to IEEE-754 float."""
    raw = struct.pack('>HH', r0, r1)
    return struct.unpack('>f', raw)[0]


def _float_to_regs(val: float):
    """Convert float to two 16-bit registers (big-endian word order)."""
    raw = struct.pack('>f', val)
    r0, r1 = struct.unpack('>HH', raw)
    return r0, r1


# Watlow EZ-ZONE register addresses (0-based, i.e., display addr - 1)
_REG = {
    'pv':        7037 - 1,   # process value (read-only)
    'setpoint':  7219 - 1,   # setpoint (read/write)
    'pid_P':     7057 - 1,   # proportional band
    'pid_I':     7059 - 1,   # integral (reset)
    'pid_D':     7061 - 1,   # derivative (rate)
}


class WatlowController:
    """
    Controls a Watlow EZ-ZONE temperature controller via Modbus RTU.

    Usage
    -----
    tc = WatlowController()
    tc.connect()
    tc.set_temperature(600.0)       # °C
    print(tc.get_temperature())     # actual PV
    tc.wait_stable(600.0, tol=2.0)
    tc.disconnect()
    """

    def __init__(self, port=None, baud=None, addr=None):
        self.port = port or COM_PORTS['temp']
        self.baud = baud or SERIAL_BAUD['temp']
        self.addr = addr or WATLOW_MODBUS_ADDR
        self.client = None

    # ── Connection ─────────────────────────────────────────────────────────
    def connect(self):
        self.client = ModbusSerialClient(
            port=self.port, baudrate=self.baud,
            bytesize=8, parity='N', stopbits=1,
            timeout=2
        )
        if not self.client.connect():
            raise ConnectionError(f"[Temp] Could not connect on {self.port}")
        print(f"[Temp] Connected on {self.port}")

    def disconnect(self):
        if self.client:
            self.client.close()
            print("[Temp] Disconnected")

    # ── Read/write float registers ─────────────────────────────────────────
    def _read_float(self, reg_addr: int) -> float:
        result = self.client.read_holding_registers(reg_addr, count=2, slave=self.addr)
        if result.isError():
            raise IOError(f"[Temp] Modbus read error at reg {reg_addr}")
        return _regs_to_float(result.registers[0], result.registers[1])

    def _write_float(self, reg_addr: int, value: float):
        r0, r1 = _float_to_regs(value)
        result = self.client.write_registers(reg_addr, [r0, r1], slave=self.addr)
        if result.isError():
            raise IOError(f"[Temp] Modbus write error at reg {reg_addr}")

    # ── Temperature control ────────────────────────────────────────────────
    def get_temperature(self) -> float:
        """Read current process value (°C)."""
        return self._read_float(_REG['pv'])

    def get_setpoint(self) -> float:
        """Read current setpoint (°C)."""
        return self._read_float(_REG['setpoint'])

    def set_temperature(self, target_c: float):
        """Write setpoint and auto-switch PID gains."""
        self._write_float(_REG['setpoint'], target_c)
        self._apply_pid(target_c)
        print(f"[Temp] Setpoint → {target_c:.1f} °C")

    def _apply_pid(self, target_c: float):
        pid = WATLOW_PID_LOW if target_c < WATLOW_PID_SWITCH_TEMP else WATLOW_PID_HIGH
        self._write_float(_REG['pid_P'], pid['P'])
        self._write_float(_REG['pid_I'], pid['I'])
        self._write_float(_REG['pid_D'], pid['D'])
        print(f"[Temp] PID → P={pid['P']} I={pid['I']} D={pid['D']}")

    # ── Stability wait ─────────────────────────────────────────────────────
    def wait_stable(self, target_c: float, tol=2.0, stable_time=120, poll=10, timeout=7200):
        """
        Block until PV is within ±tol °C of target for `stable_time` seconds.
        Polls every `poll` seconds. Raises TimeoutError after `timeout` seconds.
        """
        print(f"[Temp] Waiting for stability at {target_c:.1f} ±{tol} °C ...")
        t_start = time.time()
        t_stable_start = None

        while True:
            elapsed = time.time() - t_start
            if elapsed > timeout:
                raise TimeoutError(f"[Temp] Stability timeout after {timeout}s")

            pv = self.get_temperature()
            in_band = abs(pv - target_c) <= tol

            if in_band:
                if t_stable_start is None:
                    t_stable_start = time.time()
                held = time.time() - t_stable_start
                print(f"  PV={pv:.1f}°C  stable for {held:.0f}/{stable_time}s")
                if held >= stable_time:
                    print(f"[Temp] Stable at {pv:.1f} °C")
                    return
            else:
                t_stable_start = None
                print(f"  PV={pv:.1f}°C  (target {target_c:.1f} ±{tol})")

            time.sleep(poll)
