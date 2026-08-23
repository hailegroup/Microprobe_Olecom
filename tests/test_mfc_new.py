# -*- coding: utf-8 -*-
"""Quick test for new Aera MFC driver."""
import serial, time

PORT = 'COM5'

def query(ser, addr_hex, cmd):
    ser.reset_input_buffer()
    msg = f"\x02{addr_hex}{cmd}\r".encode('ascii')
    ser.write(msg)
    time.sleep(0.1)
    resp = ser.readline().decode('ascii', errors='replace').strip()
    return resp

with serial.Serial(PORT, 9600, bytesize=8, parity='N', stopbits=1, timeout=1) as ser:
    time.sleep(0.2)
    for ch, addr in [('A', '07'), ('B', '08')]:
        fs   = query(ser, addr, 'RFK')
        flow = query(ser, addr, 'RFX')
        sp   = query(ser, addr, 'RFD')
        print(f"MFC {ch} (addr {addr}):  full_scale={fs}  actual={flow}  setpoint={sp}")
