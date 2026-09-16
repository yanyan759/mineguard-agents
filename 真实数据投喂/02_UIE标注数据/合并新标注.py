# -*- coding: utf-8 -*-
"""UIE 标注合并脚本：把新案例（担水沟/红阳三矿/新安/羊泉）的 35 条补充标注并入现有 422 条。

- 校验每条 object 逐字出现在 text（真实性原则）
- 校验 entity_type / object_type / relation 枚举合法、confidence ∈ [0,1]
- 以 (text, entity, relation, object) 为键去重（新标注内部 + 与已有 CSV）
- 输出统一 8 列 CSV（无 BOM 列名）
"""
from pathlib import Path
import csv
import json
import sys

BASE = Path(r"e:/编程系统/precode")
CSV_PATH = BASE / "任务成果" / "02_UIE标注数据" / "标注_合并_2026-08-17.csv"
NEW_JSON = Path(r"C:/Users/HONOR/AppData/Local/Temp/claude/e-------precode/ffaf5a8d-6968-4c19-9235-6d1ed6279fd4/tasks/uie_35rows.json")
OUT_PATH = CSV_PATH  # 覆盖写回（内容增加）

ENTITY_TYPES = {"地质构造", "支护设备", "顶板灾变", "监测指标", "处置规程", "岗位角色", "巷道分区"}
RELATIONS = {"诱发", "支护", "防控", "监测", "调度", "上报", "传导"}


def read_existing() -> list[dict]:
    """读现有合并 CSV（兼容 BOM 列名）。"""
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            norm = {}
            for k, v in r.items():
                key = k.lstrip("\ufeff").strip()
                norm[key] = (v or "").strip()
            rows.append(norm)
    return rows


def validate(row: dict, idx: int) -> str | None:
    """返回错误信息；合法返回 None。"""
    if not row.get("text"):
        return f"第{idx}行缺 text"
    if row.get("object") and row["object"] not in row["text"]:
        return f"第{idx}行 object 未逐字出现在 text: {row['object']!r}"
    if row.get("object_type") and row["object_type"] not in ENTITY_TYPES:
        return f"第{idx}行 object_type 越界: {row['object_type']}"
    if row.get("entity_type") and row["entity_type"] not in ENTITY_TYPES:
        return f"第{idx}行 entity_type 越界: {row['entity_type']}"
    if row.get("relation") and row["relation"] not in RELATIONS:
        return f"第{idx}行 relation 越界: {row['relation']}"
    if row.get("confidence"):
        try:
            conf = float(row["confidence"])
        except ValueError:
            return f"第{idx}行 confidence 非数值: {row['confidence']}"
        if not 0 <= conf <= 1:
            return f"第{idx}行 confidence 越界: {conf}"
    return None


def main() -> int:
    existing = read_existing()
    new_rows = json.loads(NEW_JSON.read_text(encoding="utf-8"))
    print(f"现有: {len(existing)} 条；新标注: {len(new_rows)} 条")

    # 新标注规范化 + 校验
    cleaned = []
    for idx, r in enumerate(new_rows):
        # confidence 可能是数值，统一转字符串；其余字段 strip
        row = {}
        for k, v in r.items():
            row[k] = str(v).strip() if not isinstance(v, float) else str(v)
        row["confidence"] = str(row.get("confidence", "")).strip()
        err = validate(row, idx)
        if err:
            print(f"  [丢弃] {err}")
            continue
        cleaned.append(row)
    print(f"新标注校验通过: {len(cleaned)} 条")

    # 去重：以 (text, entity, relation, object) 为键；新标注内部 + 与已有
    seen = {(r.get("text"), r.get("entity"), r.get("relation"), r.get("object"))
            for r in existing}
    merged = list(existing)
    dup_inside = 0
    for r in cleaned:
        key = (r.get("text"), r.get("entity"), r.get("relation"), r.get("object"))
        if key in seen:
            dup_inside += 1
            continue
        seen.add(key)
        merged.append(r)
    print(f"重复丢弃: {dup_inside} 条（已有/新标注内部）")

    # 写回（无 BOM 列名）
    cols = ["text", "entity", "entity_type", "relation", "object",
            "object_type", "confidence", "source"]
    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(merged)
    print(f"合并写回: {OUT_PATH} → {len(merged)} 条")

    from collections import Counter
    rel = Counter(r.get("relation", "") for r in merged)
    print("关系分布:", dict(rel))
    print("传导:", rel.get("传导", 0), "(目标 ≥10)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
