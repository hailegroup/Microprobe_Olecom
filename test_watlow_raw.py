# -*- coding: utf-8 -*-
"""Raw Modbus RTU test for Watlow EZ-ZONE PM using pyserial (no pymodbus)."""
import serial, struct, time

PORT = 'COM3'

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def read_regs(ser, unit, reg, count):
    msg = struct.pack('>BBHH', unit, 0x03, reg, count)
    msg += struct.pack('<H', crc16(msg))
    ser.reset_input_buffer()
    ser.write(msg)
    time.sleep(0.3)
    resp = ser.read(ser.in_waiting or 9)
    return msg, resp

configs = [
    dict(parity='N', stopbits=1),
    dict(parity='N', stopbits=2),
    dict(parity='E', stopbits=1),
]

test_regs = [0, 100, 360, 1000, 2000, 7036]

for cfg in configs:
    label = f"parity={cfg['parity']} stop={cfg['stopbits']}"
    try:
        ser = serial.Serial(PORT, 9600, bytesize=8, timeout=1, **cfg)
    except Exception as e:
        print(f"[{label}] open failed: {e}"); continue

    time.sleep(0.1)
    any_hit = False
    for reg in test_regs:
        for unit in [1, 2]:
            req, resp = read_regs(ser, unit, reg, 2)
            if resp:
                print(f"[{label}] unit={unit} reg={reg:5d}  req={req.hex()}  resp={resp.hex()}  *** HIT ***")
                any_hit = True
    if not any_hit:
        print(f"[{label}] no response on any register")
    ser.close()
