# -*- coding: utf-8 -*-
import os, gzip
base = r"E:\codexcreate\煤矿资料收集"
files = [
 r"01_煤矿安全规程_顶板管理_冲击地压章节\2025版煤矿安全规程_全文_应急管理部.html",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\应急管理部令第17号_公布令.html",
 r"03_冲击地压防治技术规范\2026年防治煤矿冲击地压细则_印发通知.html",
 r"03_冲击地压防治技术规范\征求防治煤矿冲击地压细则修订稿意见函.html",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\3项煤矿顶板管理规定_印发通知.html",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\四川出台指导意见加强煤矿顶板管理.html",
]
for rel in files:
    p = os.path.join(base, rel)
    data = open(p, "rb").read()
    print("="*30, rel, len(data))
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
            print("gzip decompressed ->", len(data))
        except Exception as e:
            print("gzip err", e)
    txt = None
    for enc in ("utf-8", "gbk"):
        try:
            txt = data.decode(enc)
            print("encoding:", enc)
            break
        except Exception:
            continue
    if txt:
        print(txt[:400].replace("\n", " "))
