# -*- coding: utf-8 -*-
from pywatlow.watlow import Watlow

w = Watlow(port='COM3', address=1)
print("Reading temperature...")
result = w.read('actual value')
print("Result:", result)
