# -*- coding: utf-8 -*-
"""
Patch pywatlow's hex formatting bug, then test reading temperature.

The bug: somewhere in _buildReadRequest, hex() is called which returns
'0x1f' style strings (with '0x' prefix) instead of '1f' — causing
binascii.unhexlify to fail with 'Non-hexadecimal digit found'.
"""
import binascii, os, re

# ── Step 1: show what hexData looks like (intercept unhexlify) ─────────────
original_unhexlify = binascii.unhexlify

def debug_unhexlify(s):
    print(f"[DEBUG] unhexlify input: {repr(s)}")
    return original_unhexlify(s)

binascii.unhexlify = debug_unhexlify

try:
    from pywatlow.watlow import Watlow
    w = Watlow(port='COM3', address=1)
    w.read('actual value')
except binascii.Error as e:
    print(f"[ERROR] {e}")
    print()
    print("The hexData above is what needs to be fixed in pywatlow.")
except Exception as e:
    print(f"[OTHER ERROR] {type(e).__name__}: {e}")
finally:
    binascii.unhexlify = original_unhexlify

# ── Step 2: show the source lines around the bug ───────────────────────────
import pywatlow
src_path = os.path.join(os.path.dirname(pywatlow.__file__), 'watlow.py')
print(f"\n[SOURCE] {src_path}")
with open(src_path) as f:
    lines = f.readlines()
for i, line in enumerate(lines[130:150], start=131):
    print(f"  {i:3d}: {line}", end='')
