# -*- coding: utf-8 -*-
import os, re
BASE = r"E:\codexcreate\煤矿资料收集"
ok = True
for cat in ["01_煤矿安全规程_顶板管理_冲击地压章节","02_煤矿顶板事故应急预案","03_冲击地压防治技术规范","04_煤矿支护技术规范GB系列","05_近五年煤矿顶板事故通报"]:
    lp = os.path.join(BASE, cat, "清单.md")
    txt = open(lp, encoding="utf-8").read()
    rows = re.findall(r"^\| (\d+) \|", txt, flags=re.M)
    print(cat[:2], "entries:", len(rows), "first-last:", rows[0] if rows else "-", rows[-1] if rows else "-")
    # referenced local files
    refs = re.findall(r"\| ([^|]+\.(?:html|pdf|txt|docx)) \|", txt)
    missing = []
    for f in refs:
        if not os.path.exists(os.path.join(BASE, cat, f)):
            missing.append(f)
    if missing:
        ok = False
        print("  MISSING FILES:", missing)
print("ALL OK" if ok else "ISSUES FOUND")
