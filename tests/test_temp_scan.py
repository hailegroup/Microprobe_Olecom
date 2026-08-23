# -*- coding: utf-8 -*-
"""Scan Watlow EZ-ZONE Modbus registers to find valid responses."""
import minimalmodbus, time

PORT = 'COM3'
ADDR = 1

inst = minimalmodbus.Instrument(PORT, ADDR)
inst.serial.baudrate = 9600
inst.serial.bytesize = 8
inst.serial.parity   = 'N'
inst.serial.stopbits = 1
inst.serial.timeout  = 0.5
inst.mode = minimalmodbus.MODE_RTU
inst.debug = False

print(f"Scanning FC03 (holding registers) on {PORT} addr={ADDR}...")
print("Looking for float values that look like temperature (10–1500°C range)\n")

hits = []
for reg in range(0, 600, 2):
    try:
        val = inst.read_float(reg, functioncode=3, number_of_registers=2)
        if 10 < val < 1500:
            print(f"  *** HIT FC03 reg={reg:4d}  value={val:.3f}  (looks like temperature!)")
            hits.append((3, reg, val))
        elif val != 0.0:
            print(f"  FC03 reg={reg:4d}  value={val:.3f}")
    except Exception:
        pass

print("\nScanning FC04 (input registers)...")
for reg in range(0, 600, 2):
    try:
        val = inst.read_float(reg, functioncode=4, number_of_registers=2)
        if 10 < val < 1500:
            print(f"  *** HIT FC04 reg={reg:4d}  value={val:.3f}  (looks like temperature!)")
            hits.append((4, reg, val))
        elif val != 0.0:
            print(f"  FC04 reg={reg:4d}  value={val:.3f}")
    except Exception:
        pass

if hits:
    print(f"\nFound {len(hits)} temperature-like register(s):")
    for fc, reg, val in hits:
        print(f"  FC{fc:02d} reg={reg}  →  {val:.3f}")
else:
    print("\nNo response — Watlow may be in Standard Bus mode, not Modbus RTU.")
    print("Need NI I/O Trace capture of COM3 while LabVIEW reads temperature.")

inst.serial.close()
