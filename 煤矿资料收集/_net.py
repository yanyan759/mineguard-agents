# -*- coding: utf-8 -*-
"""Shared fetch helper for 煤矿资料收集 downloads (gzip-aware)."""
import ssl, gzip, zlib, io
import urllib.request

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def fetch(url, timeout=90, referer=None, headers_extra=None):
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Accept-Encoding": "gzip, deflate"}
    if referer:
        headers["Referer"] = referer
    if headers_extra:
        headers.update(headers_extra)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "")
        enc = (r.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            data = gzip.decompress(data)
        elif "deflate" in enc:
            data = zlib.decompress(data)
        return data, ctype

def decode(data):
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", "ignore")
