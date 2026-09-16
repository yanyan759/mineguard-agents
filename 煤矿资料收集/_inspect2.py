# -*- coding: utf-8 -*-
import os, re
base = r"E:\codexcreate\煤矿资料收集"
for rel in [r"03_冲击地压防治技术规范\征求防治煤矿冲击地压细则修订稿意见函.html",
            r"03_冲击地压防治技术规范\征求防治煤矿冲击地压细则修订稿意见函_内蒙古局_复检.html"]:
    p = os.path.join(base, rel)
    txt = open(p, encoding="utf-8", errors="ignore").read()
    body = re.sub(r"<script.*?</script>", "", txt, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body)
    print("="*20, rel, "len", len(body))
    print(body[:600])
