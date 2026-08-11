# -*- coding: utf-8 -*-
"""Test Watlow EZ-ZONE via pywatlow (Standard Bus) with bug fix applied."""

# Apply monkey-patch before importing
import pywatlow.watlow as pw

_orig = pw.Watlow._buildReadRequest
def _patched(self, dataParam, instance):
    if not isinstance(instance, str) or not all(c in '0123456789abcdefABCDEF' for c in str(instance)):
        instance = format(1, '02x')
    return _orig(self, dataParam, instance)
pw.Watlow._buildReadRequest = _patched

from pywatlow.watlow import Watlow

w = Watlow(port='COM3', address=1)
print("Reading temperature (actual value)...")
try:
    result = w.read('actual value')
    print(f"  Result: {result}")
    raw_f = float(result['data'])
    temp_c = (raw_f - 32.0) * 5.0 / 9.0
    print(f"  Raw value = {raw_f:.2f}  →  {temp_c:.2f} °C  (converted from °F)")
except Exception as e:
    print(f"  ERROR: {e}")
