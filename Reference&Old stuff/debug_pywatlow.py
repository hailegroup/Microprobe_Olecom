# -*- coding: utf-8 -*-
import os, pywatlow
src = os.path.join(os.path.dirname(pywatlow.__file__), 'watlow.py')
print("Source:", src)
with open(src) as f:
    lines = f.readlines()
for i, line in enumerate(lines[125:160], start=126):
    print(f"{i:3d}: {line}", end='')
