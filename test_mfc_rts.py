# -*- coding: utf-8 -*-
"""
MFC RS-485 test with precise RTS direction-control timing.

CS-485 converter uses RTS for TX/RX switching:
  RTS high  → TX mode (converter drives RS-485 bus)
  RTS low   → RX mode (converter listens on RS-485 bus)

At 9600 bps: 1 byte = 10 bits = 1.04 ms
"@007Q\r" = 6 bytes → needs ~6.25 ms to transmit fully before releasing RTS.
We add a margin and use ser.flush() to confirm all bytes are in the UART FIFO.

Run with the HML DMFC utility CLOSED (COM5 must be free).
"""

import serial
import time

PORT  = 'COM5'
BAUD  = 9600

# Transmission delay: bytes * 10 bits / baud * safety_factor
def tx_delay(n_bytes, baud=9600, factor=2.0):
    return n_bytes * 10 / baud * factor

def query_rts(ser, cmd_bytes, rx_wait=0.3):
    """Send cmd_bytes with RTS-controlled direction, return raw response bytes."""
    ser.reset_input_buffer()
    ser.rts = True                          # assert RTS → TX mode
    time.sleep(0.005)                       # let converter switch (~3 ms)
    ser.write(cmd_bytes)
    ser.flush()                             # wait until OS UART buffer empty
    time.sleep(tx_delay(len(cmd_bytes)))    # wait for physical transmission
    ser.rts = False                         # deassert RTS → RX mode
    time.sleep(rx_wait)                     # wait for MFC response
    return ser.read(ser.in_waiting or 1)

def run_test():
    print(f"Opening {PORT} at {BAUD} bps ...")
    with serial.Serial(
        PORT, BAUD,
        bytesize=8, parity='N', stopbits=1,
        timeout=0.5,
        rtscts=False, dsrdtr=False,
        xonxoff=False
    ) as ser:
        ser.rts = False
        ser.dtr = False
        time.sleep(0.2)
        print(f"  port open, RTS=False, DTR=False\n")

        # Try all reasonable command formats for addresses 7 and 8
        test_cmds = []
        for addr in [7, 8, 1, 2]:
            for fmt in [
                f'@{addr:03d}Q\r',
                f'@{addr:02d}Q\r',
                f'@{addr}Q\r',
                f'@{addr:03d}R\r',
            ]:
                test_cmds.append(fmt)

        print("=== RTS-controlled query scan ===")
        found = []
        for cmd_str in test_cmds:
            cmd = cmd_str.encode('ascii')
            resp = query_rts(ser, cmd, rx_wait=0.4)
            if resp:
                print(f"  HIT  {cmd!r}  →  {resp!r}")
                found.append((cmd_str, resp))
            else:
                print(f"  miss {cmd!r}")

        if not found:
            print("\n--- No response. Trying with DTR instead of RTS ---")
            # Some converters use DTR for direction control
            for cmd_str in ['@007Q\r', '@007R\r', '@008Q\r']:
                cmd = cmd_str.encode('ascii')
                ser.reset_input_buffer()
                ser.dtr = True
                time.sleep(0.005)
                ser.write(cmd)
                ser.flush()
                time.sleep(tx_delay(len(cmd)))
                ser.dtr = False
                time.sleep(0.4)
                resp = ser.read(ser.in_waiting or 1)
                if resp:
                    print(f"  HIT (DTR)  {cmd!r}  →  {resp!r}")
                else:
                    print(f"  miss (DTR) {cmd!r}")

        if not found:
            print("\n--- Trying without any RTS/DTR (auto-switching converter) ---")
            for cmd_str in ['@007Q\r', '@007R\r', '@008Q\r', '@001Q\r']:
                cmd = cmd_str.encode('ascii')
                ser.reset_input_buffer()
                ser.rts = False
                ser.dtr = False
                ser.write(cmd)
                time.sleep(0.5)
                resp = ser.read(ser.in_waiting or 1)
                if resp:
                    print(f"  HIT (no RTS)  {cmd!r}  →  {resp!r}")
                else:
                    print(f"  miss (no RTS) {cmd!r}")

    print("\nDone.")

if __name__ == '__main__':
    run_test()
