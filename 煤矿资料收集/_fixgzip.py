# -*- coding: utf-8 -*-
import os, gzip
base = os.path.dirname(os.path.abspath(__file__))
fixed, failed = [], []
for cat in os.listdir(base):
    d = os.path.join(base, cat)
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        if fn.startswith("_"):
            continue
        p = os.path.join(d, fn)
        try:
            data = open(p, "rb").read()
        except Exception as e:
            failed.append((p, str(e))); continue
        if data[:2] == b"\x1f\x8b":
            try:
                plain = gzip.decompress(data)
                with open(p, "wb") as f:
                    f.write(plain)
                fixed.append((fn, len(data), len(plain)))
            except Exception as e:
                failed.append((p, str(e)))
print("FIXED", len(fixed))
for f in fixed:
    print(" ", f[0], f[1], "->", f[2])
print("FAILED", len(failed))
for f in failed:
    print(" ", f)
