# -*- coding: utf-8 -*-
import os, re, html as htmllib

BASE = os.path.dirname(os.path.abspath(__file__))

# 1) cleanup
removals = [
 r"03_冲击地压防治技术规范\征求防治煤矿冲击地压细则修订稿意见函.html",
 r"03_冲击地压防治技术规范\征求防治煤矿冲击地压细则修订稿意见函_内蒙古局_复检.html",
 r"05_近五年煤矿顶板事故通报\2025-08-24襄矿西故县煤业顶板事故调查报告.pdf",
]
for rel in removals:
    p = os.path.join(BASE, rel)
    if os.path.exists(p):
        os.remove(p)
        print("removed", rel)

# 2) extract 第九章 from 2025 规程 full text (国务院公报 html)
src = os.path.join(BASE, r"01_煤矿安全规程_顶板管理_冲击地压章节\国务院公报2025年第28期_煤矿安全规程.html")
txt = open(src, encoding="utf-8", errors="ignore").read()
body = re.sub(r"<script.*?</script>", "", txt, flags=re.S)
body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
body = re.sub(r"<[^>]+>", "", body)
body = htmllib.unescape(body)
i = body.find("第九章")
j = body.find("第十章", i + 1 if i >= 0 else 0)
print("chapter idx", i, j)
if i >= 0 and j > i:
    chapter = body[i:j].strip()
    header = "【节选】《煤矿安全规程》(应急管理部令第17号,2025年7月24日公布,自2026年2月1日起施行) 第九章 冲击地压防治\n来源:国务院公报2025年第28期 https://www.gov.cn/gongbao/2025/issue_12326/202510/content_7043684.html\n" + "="*60 + "\n\n"
    out = os.path.join(BASE, "03_冲击地压防治技术规范", "2025版煤矿安全规程_第九章冲击地压防治_节选.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + chapter)
    print("saved", out, len(chapter), "chars")

# 3) find pdf links in 2018 and 2026 细则 notice pages
for rel, name in [
    (r"03_冲击地压防治技术规范\2018年防治煤矿冲击地压细则_印发通知.html", "2018"),
    (r"03_冲击地压防治技术规范\2026年防治煤矿冲击地压细则_印发通知.html", "2026"),
]:
    p = os.path.join(BASE, rel)
    t = open(p, encoding="utf-8", errors="ignore").read()
    links = re.findall(r'href=["\']([^"\']+?\.(?:pdf|doc|docx))["\']', t, flags=re.I)
    print(name, "pdf links:", links[:10])
