"""真实煤矿资料 → 3 类知识库文档归类脚本。

把 `agent1/煤矿资料收集/` 的真实资料（PDF/HTML/TXT/DOC/DOCX）按
`docs/ragflow_knowledge_mapping.md` 的分类映射，复制到
`deploy/ragflow/docs/real/<知识库>/` 目录，供 RAGFlow 批量导入。

分类规则（用户 2026-08-08 确认「保持 3 知识库归并」）：
    coal_mine_safety_regulations ← 01 规程 + 03 防冲规范 + 08 监测制度
    coal_mine_accident_cases     ← 05 事故通报 + 06 前兆论文（排除导航/题录页）
    coal_mine_support_schemes    ← 02 预案 + 04 支护规范

排除：07 阈值文献（用于替换 config 参数）、CAJ、导航/题录页、脚本/CSV。

用法：
    cd agent1
    python deploy/ragflow/scripts/prepare_real_docs.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# 路径约定：scripts/ → ragflow/ → deploy/ → agent1/
AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_ROOT = Path(
    os.getenv("MINEGUARD_SOURCE_CORPUS_DIR", str(AGENT1_ROOT / "煤矿资料收集"))
)
OUT_ROOT = AGENT1_ROOT / "deploy" / "ragflow" / "docs" / "real"

# 知识库名 → 归并的源目录名
DATASET_MAP = {
    "coal_mine_safety_regulations": [
        "01_煤矿安全规程_顶板管理_冲击地压章节",
        "03_冲击地压防治技术规范",
        "08_监测制度_专家经验",
    ],
    "coal_mine_accident_cases": [
        "05_近五年煤矿顶板事故通报",
        "06_事故前兆数据_学术论文",
    ],
    "coal_mine_support_schemes": [
        "02_煤矿顶板事故应急预案",
        "04_煤矿支护技术规范GB系列",
    ],
}

# 明确排除的源文件（导航页/题录页/非知识内容）
EXCLUDE_SUBSTRINGS = [
    "煤矿安全_期刊页_NSTL",      # 06 期刊导航页，无正文
    "王自强_中州煤炭2016_知网题录页",  # 06 题录页
]

# 允许复制到知识库的扩展名（CAJ 不支持，ZIP 是数据集，py 是脚本）
ALLOWED_SUFFIXES = {".pdf", ".html", ".txt", ".doc", ".docx"}


def main() -> None:
    """按映射复制可选资料；源码发布包没有资料时只保留空目录说明。"""
    # 先清空旧输出，避免残留文件影响导入
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)

    summary: list[str] = []
    for dataset, src_dirs in DATASET_MAP.items():
        target = OUT_ROOT / dataset
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.md").write_text(
            "此目录用于可选资料导入。源码仓库不内置真实语料；请按 docs/external-data.md 配置外部资料目录。\n",
            encoding="utf-8",
        )
        copied = 0
        for src_dir in src_dirs:
            src_path = SRC_ROOT / src_dir
            if not src_path.exists():
                print(f"  [警告] 源目录不存在：{src_dir}")
                continue
            for file in sorted(src_path.iterdir()):
                if not file.is_file():
                    continue
                # 排除清单.md 索引文件
                if file.name in {"清单.md"}:
                    continue
                # 排除导航/题录页
                if any(ex in file.name for ex in EXCLUDE_SUBSTRINGS):
                    continue
                # 排除不支持的格式
                if file.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                shutil.copy2(file, target / file.name)
                copied += 1
        summary.append(f"  {dataset} ← {len(src_dirs)} 目录 → {copied} 份")
        print(summary[-1])

    print(f"\n[OK] 真实资料归类完成 → {OUT_ROOT}")
    for line in summary:
        print(line)
    print("\n下一步：按部署方案选择执行 ragflow_import.py 或 Dify 导入脚本")


if __name__ == "__main__":
    main()
