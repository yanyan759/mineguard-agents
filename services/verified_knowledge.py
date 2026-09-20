"""检索可追溯的可选资料：默认使用小型发布 fixture，不捆绑真实语料。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from app.agent.knowledge_sources import RetrievalData

ROOT = Path(__file__).resolve().parents[1]
REGULATION_FILE = "tests/fixtures/verified_regulation_excerpt.txt"
REGULATION_URL = "https://www.gov.cn/gongbao/2025/issue_12326/202510/content_7043684.html"
# 与官方网页逐条核对的本地版本；内容变动后要求重新核验，不沿用旧认证。
VERIFIED_SHA256 = "95d2d7f4342172a146ed922dd21d95d751715a5535f318010ee6f4e82f637b8e"
VERIFIED_AT = "2026-09-12"


def retrieve_verified_knowledge(terms: list[str], root: Path = ROOT) -> RetrievalData:
    """条文按监测/阈值/效果核验主题引用；事故资料按实际命中词检索。"""
    result = RetrievalData(source_type="traceable_local_documents")
    path = root / REGULATION_FILE
    if not path.is_file():
        result.missing_topics.append("缺少已核验法规原文，未使用仿真条款替代")
    elif hashlib.sha256(path.read_bytes()).hexdigest() != VERIFIED_SHA256:
        result.missing_topics.append("法规本地版本已变化，需重新核验原文后引用")
    else:
        text = path.read_text(encoding="utf-8")
        for clause, applicability in (
            ("第三百三十五条", "冲击地压防治工作；预警临界指标须由现场资料与积累数据确定，本次矿井适用性待核验"),
            ("第三百四十二条", "冲击地压矿井的区域监测；不能仅凭本系统颜色判定矿井属于该类"),
            ("第三百五十四条", "冲击地压防治中的解危措施及效果检验；本次未提供处置后观测"),
        ):
            match = re.search(clause + r"\s*(.*?)(?=第[一二三四五六七八九十百]+条|$)", text, re.S)
            if not match:
                result.missing_topics.append(f"无法定位{clause}")
                continue
            quote = re.sub(r"第[一二三四五六七八九十]+节.*$", "", match[1], flags=re.S).strip()
            if clause == "第三百三十五条":
                quote = re.search(r"（四）.*?(?=（五）)", quote, re.S)[0]
                clause += "第（四）项"
            result.regulations.append({
                "document": "煤矿安全规程（应急管理部令第17号，2026-02-01施行）",
                "clause": clause, "requirement": quote, "source": "中国政府网·国务院公报",
                "source_url": REGULATION_URL, "applicability": applicability,
                "verified_at": VERIFIED_AT, "mandatory": True,
            })

    configured_cases = os.getenv("MINEGUARD_REAL_CASES_PATH", "").strip()
    cases_path = Path(configured_cases) if configured_cases else None
    if cases_path is not None and not cases_path.is_absolute():
        cases_path = root / cases_path
    if cases_path is not None and cases_path.is_file():
        try:
            raw = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
            rows = raw["cases"]
            # 仅匹配输入明确提到的领域词，不把事故严重程度当作当前预警相似度。
            keywords = [word for word in ("微震", "矿压", "顶板", "支护", "断层", "冲击地压", "离层", "采空区") if any(word in term for term in terms)]
            candidates = []
            for case in rows:
                content = " ".join([*case.get("causes", []), *case.get("precursors", [])])
                matches = [word for word in keywords if word in content]
                source_url = re.sub(r"\s+", "", case.get("metadata", {}).get("source_url", ""))
                parsed = urlparse(source_url)
                if not matches or parsed.scheme not in ("http", "https") or not (parsed.hostname or "").endswith(".gov.cn"):
                    continue
                candidates.append({
                    "case_id": case["case_id"], "similarity": None, "match_terms": matches,
                    "causes": case.get("causes", []), "actions": case.get("actions", []),
                    "outcome": case.get("outcome"), "source": case.get("source", ""),
                    "source_url": source_url, "source_status": "curated_summary_unverified",
                })
            result.similar_cases = sorted(candidates, key=lambda item: (-len(item["match_terms"]), item["case_id"]))[:3]
        except (KeyError, TypeError, yaml.YAMLError, UnicodeError) as exc:
            result.missing_topics.append(f"事故资料格式异常，未回退仿真数据：{type(exc).__name__}")
    if not result.similar_cases:
        result.missing_topics.append("未找到与输入关键词匹配且附官方来源的事故资料")
    result.missing_topics.append("缺少本矿地质条件、支护设计和经现场校准的阈值，不能确认具体施工方案适用性")
    result.evidence_description = "本地已核对法规摘录；事故资料为人工整理摘要，未逐条在线核验，不提供数值相似度"
    return result
