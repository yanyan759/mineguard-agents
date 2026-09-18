# -*- coding: utf-8 -*-
"""真实事故案例 → fixtures 真实数据域生成脚本。

从 事故案例_真实报告FINAL.yaml（21 份真实，含官方 URL 元数据）生成
agent1/tests/fixtures/real_accident_cases.yaml（真实数据域权威源）。

设计要点：
- 与仿真 accident_cases.yaml（5 份）完全分离，case_id 前缀 real-* 与仿真 case-* 不冲突
- 保留 metadata.source_url/local_file/published_at（真实数据凭证，仿真 YAML 无此字段）
- 供 Neo4j seed --real / embed_cases --real / knowledge_sources REAL_CASES=on 消费
"""
from pathlib import Path
from collections import Counter
import sys

import yaml

# 根据脚本位置定位发布包，保证在不同操作系统和目录下均可复现。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MY = PROJECT_ROOT / "真实数据投喂/01_事故案例整理"
SRC = MY / "事故案例_真实报告FINAL.yaml"
OUT = PROJECT_ROOT / "tests/fixtures/real_accident_cases.yaml"

REQUIRED = {"case_id", "location", "occurred_at", "risk_level", "causes",
            "precursors", "actions", "outcome", "lessons", "source", "metadata"}


def main() -> int:
    raw = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    cases = raw["cases"]
    print("FINAL 案例数:", len(cases))

    # 字段完整性校验
    for c in cases:
        miss = REQUIRED - set(c)
        assert not miss, f"{c['case_id']} 缺 {miss}"
        assert c["metadata"].get("source_url"), f"{c['case_id']} 缺 source_url"
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids)), "case_id 重复"
    assert all(i.startswith("real-") for i in ids), "真实案例 case_id 应 real-* 前缀"

    payload = {
        "# 说明": (
            "真实事故案例库（21 份）—— 逐字解析自国家矿山安全监察局及地方局官方事故调查报告，"
            "metadata.source_url/local_file/published_at 为官方来源凭证。"
            "与 tests/fixtures/accident_cases.yaml（5 份仿真）分离，构成真实数据域；"
            "由 seed_from_yaml.py --real / embed_cases.py --real / knowledge_sources.py REAL_CASES=on 消费。"
            "真实性：synthetic_but_anchored=false。"
        ),
        "cases": cases,
    }
    OUT.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    print("已写入:", OUT)
    print("等级分布:", dict(Counter(c["risk_level"] for c in cases)))
    print("年份分布:", dict(sorted(Counter(c["case_id"][5:9] for c in cases).items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
