# -*- coding: utf-8 -*-
"""
Hardware configuration for the microprobe scanning system.
Edit this file to match your physical setup.
"""

# ── Serial ports ───────────────────────────────────────────────────────────
COM_PORTS = {
    'motor':    'COM9',    # IMS MDrive stepper motors
    'temp':     'COM4',    # Watlow EZ-ZONE temperature controller (Standard Bus via pywatlow)
    'mfc':      'COM5',    # Aera mass flow controllers (addresses 7 and 8)
}

SERIAL_BAUD = {
    'motor': 9600,
    'temp':  9600,   # Watlow EZ-ZONE: 9600, 8N1
    'mfc':   9600,
}

# ── BioLogic potentiostat ──────────────────────────────────────────────────
BIOLOGIC_IP   = '192.109.209.128'
BIOLOGIC_PORT = 5000   # default easy-biologic port

# BioLogic control backend for the Win7 runner.
#
# IMPORTANT:
#   OLE-COM and easy-biologic should not be mixed in the same instrument session.
#   After easy-biologic talks to the potentiostat, EC-Lab/OLE-COM may require an
#   instrument power-cycle before it can reconnect cleanly.  Keep the production
#   GUI on OLE-COM so Connect, OCV contact checks, quick runs, and full-auto all
#   use the same EC-Lab-controlled path.
#
# Supported values:
#   'olecom'        : Win7 production mode; requires EC-Lab OLE-COM/comtypes.
#   'easybiologic'  : legacy/debug mode only.
BIOLOGIC_BACKEND = 'olecom'
BIOLOGIC_ALLOW_EASY_FALLBACK = False
BIOLOGIC_OLECOM_DEVICE_INDEX = 0
# Win7/EC-Lab can show a visible EC-Lab window without registering an active
# OLE-COM object in the Running Object Table.  The GUI Connect button may create
# one persistent OLE-COM session, but measurement scripts should still avoid
# creating extra hidden EC-Lab instances unless explicitly requested.
BIOLOGIC_OLECOM_CREATE_ECLAB_IF_MISSING = True
BIOLOGIC_OLECOM_TRUST_TEST_CONNECTION = True
BIOLOGIC_OLECOM_DISCONNECT_DEVICE_ON_GUI_DISCONNECT = False

# Default OLE-COM hybrid settings used by GUI quick/full-auto wrappers.
BIOLOGIC_OLECOM_BANDWIDTH = 4
BIOLOGIC_OLECOM_PEIS_POINTS_PER_DECADE = 10
BIOLOGIC_OLECOM_PEIS_DEEP_LIMIT_HZ = 0.1
BIOLOGIC_OLECOM_PEIS_OVERLAP_LIMIT_HZ = 0.5
BIOLOGIC_OLECOM_PRE_MAX_S = 300.0
BIOLOGIC_OLECOM_MIN_FFT_DURATION_S = 100.0
BIOLOGIC_OLECOM_SCOUT_MAX_S = 300.0
BIOLOGIC_OLECOM_SCOUT_TAIL_WINDOW_S = 20.0
BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SHIFT_LIMIT = 0.03
BIOLOGIC_OLECOM_SCOUT_TAIL_REL_SLOPE_LIMIT = 0.03

# ── Motor calibration (steps per mm) ──────────────────────────────────────
STEPS_PER_MM = {
    'X': 40314.96,
    'Y': 48377.95,
    'Z': 48377.95,
}

# Stage-travel safety policy used when moving between sample locations.
# Keep disabled until the real Z sign convention / clearance height is confirmed.
STAGE_SAFE_MOVE = {
    'enabled': True,
    'clearance_z_mm': None,      # absolute safe Z for XY travel; overrides relative lift when set
    'lift_delta_mm': 0.5,        # relative lift if clearance_z_mm is not configured
    'positive_z_is_up': False,   # False: increasing Z moves the tip down / closer to sample
    'settle_s': 0.1,
}

# ── Watlow EZ-ZONE ────────────────────────────────────────────────────────
WATLOW_MODBUS_ADDR = 1   # default slave address
WATLOW_PID_LOW  = {'P': 40,  'I': 108, 'D': 18}   # below 400 °C
WATLOW_PID_HIGH = {'P': 24,  'I': 12,  'D': 2}    # above  400 °C
WATLOW_PID_SWITCH_TEMP = 400.0   # °C
TEMP_SAFETY_ENABLED = True
TEMP_SAFETY_ACTIVE_TARGET_C = 150.0
TEMP_SAFETY_MIN_VALID_C = 50.0
TEMP_SAFETY_MAX_DROP_C = 80.0
TEMP_SAFETY_POLL_S = 5.0
TEMP_SAFETY_SHUTDOWN_SETPOINT_C = 25.0

# ── Aera MFC ──────────────────────────────────────────────────────────────
MFC_ADDRESS = {
    'A': 7,   # Gas A
    'B': 8,   # Gas B
}
MFC_FULL_SCALE_SCCM = {
    'A': 100.0,    # ID 007, S/N C090818032
    'B': 1000.0,   # ID 008, S/N J080310023
}

# Safety gate for digital write commands to the Aera MFCs.
# Keep disabled until the command protocol is validated on the real hardware.
MFC_WRITE_ENABLED = True

# The DMFC utility manual says flow setpoint/output display defaults to percent.
# Lab readback also indicates the write setting is a raw 0-100 value, not sccm.
# GUI/CSV conditions therefore use raw DMFC setting values (0-100).
MFC_SETPOINT_COMMAND_UNIT = 'percent'
MFC_MIN_DIGITAL_SETTING_PERCENT = 2.0
MFC_SETPOINT_TOLERANCE_PERCENT = 0.25
MFC_SETPOINT_READBACK_TIMEOUT_S = 4.0
MFC_SETPOINT_READBACK_POLL_S = 0.5
MFC_SETPOINT_WRITE_RETRIES = 1

# Some DMFCs require switching from analog to digital control before serial
# setpoints take effect. Leave disabled until the exact command behavior is
# validated on the old instrument PC, because digital mode can override analog
# flow control from the external system.
# Digital control switch policy before setpoint writes:
#   'once'   : send SRS only once per channel/connection, then only update SFD
#   'always' : send SRS before every SFD write
#   False    : never send SRS automatically
# Re-sending SRS every manual gas edit can make the hardware behave unlike the
# DMFC utility, which normally stays in digital/manual mode and just changes the
# target setpoint.
MFC_SEND_DIGITAL_CONTROL_ON_WRITE = 'once'
