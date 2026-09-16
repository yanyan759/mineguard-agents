# -*- coding: utf-8 -*-
import os
from pypdf import PdfReader
BASE = r"E:\codexcreate\煤矿资料收集\07_监测阈值标定_研究文献"
for f in ["基于b值的巷道冲击地压预警分析_高丁丁_矿业装备2021_全文.pdf", "千秋煤矿冲击地压综合预警技术研究_李学龙_中国矿大硕士2015_全文.pdf"]:
    p = os.path.join(BASE, f)
    r = PdfReader(p)
    total = 0
    pages_with_text = []
    for i, pg in enumerate(r.pages):
        t = pg.extract_text() or ""
        total += len(t)
        if len(t) > 20:
            pages_with_text.append(i+1)
    print(f, "| chars:", total, "| pages with text:", pages_with_text[:20], "... count:", len(pages_with_text))
