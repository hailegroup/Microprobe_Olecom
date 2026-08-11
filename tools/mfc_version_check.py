# -*- coding: utf-8 -*-
"""Print the exact MFC driver/script versions used on the runner PC."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import config
import driver_mfc


def main():
    print("=== MFC runner version check ===")
    print(f"Project dir: {PROJECT_DIR}")
    print(f"Python: {sys.executable}")
    print(f"driver_mfc: {Path(driver_mfc.__file__).resolve()}")
    print(f"driver version: {getattr(driver_mfc, 'MFC_DRIVER_VERSION', 'unknown')}")
    print(f"MFC_WRITE_ENABLED: {getattr(config, 'MFC_WRITE_ENABLED', 'missing')}")
    print(f"MFC_SEND_DIGITAL_CONTROL_ON_WRITE: {getattr(config, 'MFC_SEND_DIGITAL_CONTROL_ON_WRITE', 'missing')}")
    print(f"MFC_SETPOINT_READBACK_TIMEOUT_S: {getattr(config, 'MFC_SETPOINT_READBACK_TIMEOUT_S', 'missing')}")
    print(f"MFC_SETPOINT_READBACK_POLL_S: {getattr(config, 'MFC_SETPOINT_READBACK_POLL_S', 'missing')}")
    print(f"MFC_ADDRESS: {getattr(config, 'MFC_ADDRESS', 'missing')}")
    print(f"MFC_FULL_SCALE_SCCM: {getattr(config, 'MFC_FULL_SCALE_SCCM', 'missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
