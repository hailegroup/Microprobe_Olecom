# -*- coding: utf-8 -*-
"""
Hardware configuration for the microprobe scanning system.
Edit this file to match your physical setup.
"""

# ── Serial ports ───────────────────────────────────────────────────────────
COM_PORTS = {
    'motor':    'COM9',    # IMS MDrive stepper motors
    'temp':     'COM3',    # Watlow EZ-ZONE temperature controller (Modbus RTU)
    'mfc':      'COM5',    # Aera mass flow controllers (addresses 7 and 8)
}

SERIAL_BAUD = {
    'motor': 9600,
    'temp':  9600,
    'mfc':   9600,
}

# ── BioLogic potentiostat ──────────────────────────────────────────────────
BIOLOGIC_IP   = '192.109.209.127'
BIOLOGIC_PORT = 5000   # default easy-biologic port

# ── Motor calibration (steps per mm) ──────────────────────────────────────
STEPS_PER_MM = {
    'X': 40314.96,
    'Y': 48377.95,
    'Z': 48377.95,
}

# ── Watlow EZ-ZONE ────────────────────────────────────────────────────────
WATLOW_MODBUS_ADDR = 1   # default slave address
WATLOW_PID_LOW  = {'P': 40,  'I': 108, 'D': 18}   # below 400 °C
WATLOW_PID_HIGH = {'P': 24,  'I': 12,  'D': 2}    # above  400 °C
WATLOW_PID_SWITCH_TEMP = 400.0   # °C

# ── Aera MFC ──────────────────────────────────────────────────────────────
MFC_ADDRESS = {
    'A': 7,   # Gas A
    'B': 8,   # Gas B
}
MFC_FULL_SCALE_SCCM = {
    'A': 100.0,    # ID 007, S/N C090818032
    'B': 1000.0,   # ID 008, S/N J080310023
}
