# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _net import fetch
BASE = os.path.dirname(os.path.abspath(__file__))
u = "https://www.7-zip.org/a/7zr.exe"
try:
    data, ctype = fetch(u, timeout=120)
    print("len", len(data), ctype, data[:2])
    if len(data) > 300000:
        open(os.path.join(BASE, "_7zr.exe"), "wb").write(data)
        print("7ZR SAVED")
except Exception as e:
    print("FAIL", str(e)[:200])
print("DONE")
