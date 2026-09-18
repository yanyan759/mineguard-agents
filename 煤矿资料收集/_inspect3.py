# -*- coding: utf-8 -*-
import os, re
base = os.path.dirname(os.path.abspath(__file__))
def text_of(p):
    data = open(p, "rb").read()
    if data[:5] == b"%PDF-":
        return "[PDF] " + data[:8].decode("latin1")
    if data[:4] == b"PK\x03\x04":
        return "[DOCX/ZIP]"
    txt = None
    for enc in ("utf-8", "gbk"):
        try:
            txt = data.decode(enc); break
        except Exception: pass
    if txt is None:
        txt = data.decode("utf-8", "ignore")
    body = re.sub(r"<script.*?</script>", "", txt, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body)
    return body[:500]

checks = [
 r"01_煤矿安全规程_顶板管理_冲击地压章节\国务院公报2025年第28期_煤矿安全规程.html",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\2016版煤矿安全规程全文.pdf",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\2022年修改煤矿安全规程的决定_令第8号.html",
 r"01_煤矿安全规程_顶板管理_冲击地压章节\煤矿安全规程2025版新旧对照表.pdf",
 r"02_煤矿顶板事故应急预案\巴彦淖尔市煤矿生产安全事故应急预案2025年版.html",
 r"02_煤矿顶板事故应急预案\阳泉市煤矿生产安全事故应急预案.html",
 r"03_冲击地压防治技术规范\2025年征求防治煤矿冲击地压细则修订稿意见函_煤炭工业协会.html",
 r"03_冲击地压防治技术规范\2026年新版防治煤矿冲击地压细则印发_新闻.html",
 r"03_冲击地压防治技术规范\GB_T25217.2-2010_煤的冲击倾向性.html",
 r"04_煤矿支护技术规范GB系列\GB_T35056-2018_煤矿巷道锚杆支护技术规范.html",
]
for rel in checks:
    p = os.path.join(base, rel)
    print("="*20, rel)
    print(text_of(p)[:400])
