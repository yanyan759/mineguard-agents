"""RAGFlow 知识库文档构建脚本。

把本地案例与规程资料转成
RAGFlow 可导入的 markdown 文档，按三个知识库分类输出：

    safety_regulations/  — 安全规程条款（煤矿安全规程/冲击地压防治/应急预案）
    accident_cases/      — 历史顶板事故案例（每案例一篇）
    support_schemes/     — 支护处置方案（从案例 actions 提炼）

输出目录：deploy/ragflow/docs/<分类>/*.md
本脚本离线可运行（仅依赖 fixture YAML），不依赖 RAGFlow 服务。

用法：
    cd agent1
    python deploy/ragflow/scripts/build_documents.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 路径约定：scripts/ → ragflow/ → deploy/ → agent1/
AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES_DIR = AGENT1_ROOT / "tests" / "fixtures"
OUT_DIR = AGENT1_ROOT / "deploy" / "ragflow" / "docs"


def _render_case(c: dict) -> str:
    """渲染单篇事故案例 markdown 文档。"""
    lines = [
        f"# 事故案例：{c['case_id']}",
        "",
        f"- **事故位置**：{c.get('location', '')}",
        f"- **发生时间**：{c.get('occurred_at', '')}",
        f"- **风险等级**：{c.get('risk_level', '')}",
        f"- **案例来源**：{c.get('source', '')}",
        "",
        "## 致灾因素",
    ]
    lines += [f"- {x}" for x in c.get("causes", [])]
    lines += ["", "## 前兆特征"]
    lines += [f"- {x}" for x in c.get("precursors", [])]
    lines += ["", "## 处置措施"]
    lines += [f"- {x}" for x in c.get("actions", [])]
    lines += ["", "## 处置结果", "", c.get("outcome", ""), "", "## 事故教训"]
    lines += [f"- {x}" for x in c.get("lessons", [])]
    return "\n".join(lines) + "\n"


def _render_regulation(r: dict) -> str:
    """渲染单条规程条款 markdown 文档。"""
    lines = [
        f"# {r.get('document', '')} · {r.get('clause', '')}",
        "",
        f"- **强制性**：{'是' if r.get('mandatory', False) else '否'}",
        f"- **来源**：{r.get('source', '')}",
        f"- **条款类型**：{r.get('source_type', '')}",
        "",
        "## 条文要求",
        "",
        r.get("requirement", ""),
        "",
        "## 适用条件",
    ]
    lines += [f"- {x}" for x in r.get("applicable_conditions", [])]
    return "\n".join(lines) + "\n"


def _render_scheme(c: dict) -> str:
    """从案例 actions 提炼支护处置方案 markdown 文档。"""
    lines = [
        f"# 支护处置方案（借鉴案例 {c['case_id']}）",
        "",
        f"- **适用场景**：{c.get('risk_level', '')} 风险（{c.get('location', '')}）",
        f"- **案例来源**：{c.get('source', '')}",
        "",
        "## 处置动作",
    ]
    lines += [f"{i+1}. {x}" for i, x in enumerate(c.get("actions", []))]
    lines += ["", "## 注意事项", ""]
    lines += [f"- {x}" for x in c.get("lessons", [])]
    return "\n".join(lines) + "\n"


def main() -> None:
    """读取 fixture YAML 并写出三分类 markdown 文档。"""
    cases = yaml.safe_load(
        (FIXTURES_DIR / "accident_cases.yaml").read_text(encoding="utf-8")
    ).get("cases", [])
    regs = yaml.safe_load(
        (FIXTURES_DIR / "regulations.yaml").read_text(encoding="utf-8")
    ).get("regulations", [])

    plans = [
        ("accident_cases", "事故案例", _render_case, cases, "case_id"),
        ("safety_regulations", "安全规程", _render_regulation, regs, "regulation_id"),
        ("support_schemes", "支护方案", _render_scheme, cases, "case_id"),
    ]

    written = 0
    for folder, label, render, items, key_field in plans:
        target = OUT_DIR / folder
        target.mkdir(parents=True, exist_ok=True)
        for item in items:
            file = target / f"{item[key_field]}.md"
            file.write_text(render(item), encoding="utf-8")
            written += 1
        print(f"[{label}] {target} → {len(items)} 篇")

    print(f"[OK] 共生成 {written} 篇 markdown 文档 → {OUT_DIR}")


if __name__ == "__main__":
    main()
