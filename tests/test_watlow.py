# -*- coding: utf-8 -*-
"""
Watlow EZ-ZONE PM Modbus diagnostic.
Tries multiple serial configs + function codes + register ranges.
"""
from pymodbus.client.sync import ModbusSerialClient
import struct

PORT = 'COM3'
UNIT = 1

def f(r0, r1):
    return struct.unpack('>f', struct.pack('>HH', r0, r1))[0]

configs = [
    dict(baudrate=9600,  parity='N', stopbits=2),
    dict(baudrate=9600,  parity='E', stopbits=1),
    dict(baudrate=9600,  parity='N', stopbits=1),
    dict(baudrate=19200, parity='N', stopbits=2),
]

for cfg in configs:
    label = f"baud={cfg['baudrate']} P={cfg['parity']} S={cfg['stopbits']}"
    c = ModbusSerialClient(method='rtu', port=PORT, timeout=2, bytesize=8, **cfg)
    if not c.connect():
        print(f"[{label}] CONNECT FAILED"); continue

    hit = False
    # Try holding registers (FC03) and input registers (FC04)
    for reg in range(0, 400, 2):
        r = c.read_holding_registers(reg, count=2, unit=UNIT)
        if not r.isError():
            val = f(r.registers[0], r.registers[1])
            print(f"[{label}] FC03 reg={reg:4d} → {val:.2f}  *** HIT ***")
            hit = True
        r2 = c.read_input_registers(reg, count=2, unit=UNIT)
        if not r2.isError():
            val = f(r2.registers[0], r2.registers[1])
            print(f"[{label}] FC04 reg={reg:4d} → {val:.2f}  *** HIT ***")
            hit = True

    if not hit:
        print(f"[{label}] no response on any register 0-399")
    c.close()
