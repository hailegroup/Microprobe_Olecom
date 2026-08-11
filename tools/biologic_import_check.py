# -*- coding: utf-8 -*-
"""Diagnose BioLogic/easy-biologic imports on the runner PC."""

from __future__ import annotations

from pathlib import Path
import importlib
import os
import platform
import sys
import traceback

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


MODULES = [
    "numpy",
    "driver_biologic",
    "easy_biologic",
    "easy_biologic.base_programs",
    "easy_biologic.lib.ec_find",
    "easy_biologic.lib.ec_lib",
    "easy_biologic.lib.data_parser",
]


def main():
    print("=== BioLogic import check ===")
    print(f"Project dir: {PROJECT_DIR}")
    print(f"Python: {sys.executable}")
    print(f"Python version: {sys.version}")
    print(f"Architecture: {platform.architecture()[0]}")
    print(f"PATH head: {os.environ.get('PATH', '')[:500]}")
    print()
    ok = True
    for name in MODULES:
        print(f"--- import {name} ---")
        try:
            mod = importlib.import_module(name)
            print(f"OK: {getattr(mod, '__file__', '<built-in>')}")
        except Exception:
            ok = False
            traceback.print_exc()
        print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
