"""RAGFlow 全量文档重解析与配置脚本（REST API）。

背景：v0.26.4 naive 切分器默认 layout_recognize=DeepDOC（视觉版面识别 + OCR），
每 10 页窗口耗时 ~173s；且 RAPTOR / GraphRAG 默认开启，依赖 LLM（Ollama CPU 推理）
进一步拖慢。经调研确认（rag/app/naive.py 源码）：
  - layout_recognize 仅影响 PDF；html/txt/doc/docx 走纯文本路径
  - 合法安全值仅 "DeepDOC" / "Plain Text" / ""；"naive" / "false" 会解析时报错
  - "Plain Text" = pdfium 文本层提取，跳过 OCR/TSR/DLR，快 ~30-50 倍；扫描版 PDF 无文本层会得 0 chunk
  - 执行器使用【文档自身】parser_config（上传时的快照），改数据集配置不会传播到已上传文档
  - 重解析（POST /documents/parse）会强制重置状态、清空旧 chunk、全量重建
  - 参数类型：auto_keywords/auto_questions 用整数 0/1；raptor.use_raptor / graphrag.use_graphrag 用布尔

优化配置：layout_recognize="Plain Text" + 关闭 RAPTOR/GraphRAG/自动关键词/自动问题。

用法（cd agent1）：
    python deploy/ragflow/scripts/ragflow_reparse.py --apply     # PATCH 全部文档配置 + 重触发解析
    python deploy/ragflow/scripts/ragflow_reparse.py --audit     # 轮询至全部终态，报告 FAIL/0-chunk 文档
    python deploy/ragflow/scripts/ragflow_reparse.py --flip-back # 将 FAIL/0-chunk 文档翻回 DeepDOC 并重解析（扫描版兜底）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# 路径约定：scripts/ → ragflow/ → deploy/ → agent1/（绝对路径加载 .env，避免 cwd 影响）
AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(AGENT1_ROOT / ".env")

_RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY")
_RAGFLOW_API_URL = (os.getenv("RAGFLOW_API_URL") or "http://localhost:9380").rstrip("/")
_TIMEOUT = 30

# 3 个知识库名 → id（与 ragflow_import.py 一致）
DATASETS = {
    "coal_mine_safety_regulations": "7a456002930c11f1b7aed3f791ceee6e",
    "coal_mine_accident_cases": "aa92bc94930e11f1b7aed3f791ceee6e",
    "coal_mine_support_schemes": "b2ab3dd4930e11f1b7aed3f791ceee6e",
}

# 优化后的解析配置（v0.26.4 参数类型：auto_* 整数，raptor/graphrag 布尔）
OPTIMIZED_CFG = {
    "layout_recognize": "Plain Text",   # pdfium 文本层提取，跳过 DeepDOC OCR/TSR/DLR
    "chunk_token_num": 512,
    "auto_keywords": 0,                 # 关闭 LLM 自动关键词（省 LLM 调用）
    "auto_questions": 0,                # 关闭 LLM 自动问题
    "raptor": {"use_raptor": False},    # 关闭 RAPTOR（LLM 摘要，耗时）
    "graphrag": {"use_graphrag": False},  # 关闭 GraphRAG（LLM 实体抽取，耗时）
}
# 扫描版 PDF 兜底配置：保持文本层缺失也能 OCR 提取，但同样关闭 RAPTOR/GraphRAG
DEEPDOC_CFG = {
    "layout_recognize": "DeepDOC",
    "chunk_token_num": 512,
    "auto_keywords": 0,
    "auto_questions": 0,
    "raptor": {"use_raptor": False},
    "graphrag": {"use_graphrag": False},
}


def _headers() -> dict:
    if not _RAGFLOW_API_KEY:
        raise RuntimeError("未配置 RAGFLOW_API_KEY（agent1/.env）")
    return {"Authorization": f"Bearer {_RAGFLOW_API_KEY}", "Content-Type": "application/json"}


def _list_docs(dataset_id: str) -> list[dict]:
    """分页拉取知识库全部文档（v0.26.4 page_size ≤ 100）。"""
    docs: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents",
            params={"page": page, "page_size": 100}, headers=_headers(), timeout=_TIMEOUT,
        )
        r.raise_for_status()
        batch = (r.json().get("data") or {}).get("docs", [])
        docs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return docs


def _patch_doc(dataset_id: str, doc_id: str, cfg: dict) -> bool:
    """PATCH 单个文档 parser_config；返回是否成功（code=0）。"""
    r = requests.patch(
        f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents/{doc_id}",
        json={"parser_config": cfg}, headers=_headers(), timeout=_TIMEOUT,
    )
    return r.status_code == 200 and r.json().get("code") == 0


def _trigger_parse(dataset_id: str, doc_ids: list[str]) -> None:
    """重触发解析（强制重置 + 清空旧 chunk + 全量重建，无需先取消）。"""
    r = requests.post(
        f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents/parse",
        json={"document_ids": doc_ids}, headers=_headers(), timeout=_TIMEOUT,
    )
    r.raise_for_status()
    print(f"  [触发] {dataset_id}: {len(doc_ids)} 份进入解析队列")


def cmd_apply() -> int:
    """PATCH 全部文档为优化配置 + 重触发解析。"""
    if not _RAGFLOW_API_KEY:
        print("[错误] 未配置 RAGFLOW_API_KEY")
        return 1
    for name, dataset_id in DATASETS.items():
        docs = _list_docs(dataset_id)
        print(f"[知识库] {name}（{dataset_id[:8]}…）：{len(docs)} 份")
        ok = fail = 0
        for d in docs:
            if _patch_doc(dataset_id, d["id"], OPTIMIZED_CFG):
                ok += 1
            else:
                fail += 1
        print(f"  [PATCH] 成功 {ok} / 失败 {fail}")
        if ok:
            _trigger_parse(dataset_id, [d["id"] for d in docs])
    print("[完成] 全部文档已应用优化配置并重触发解析")
    return 0


def _chunk_count(dataset_id: str, doc_id: str) -> int:
    """查单个文档的 chunk 总数（列表接口不返回 chunk_num，需逐个查）。"""
    try:
        r = requests.get(
            f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks",
            params={"page": 1, "page_size": 1}, headers=_headers(), timeout=_TIMEOUT,
        )
        data = (r.json().get("data") or {}) if r.status_code == 200 else {}
        return int(data.get("total") or len(data.get("chunks", [])))
    except Exception:
        return -1


def cmd_audit(max_wait_seconds: int = 3600) -> int:
    """轮询至全部文档终态（DONE=3 / FAIL=4），报告 FAIL 与 0-chunk 文档。

    Plain Text 对扫描版 PDF 会得 0 chunk（文本层缺失），需同样翻回 DeepDOC。
    """
    deadline = time.time() + max_wait_seconds
    pending_names = set(DATASETS)
    all_issues: list[dict] = []
    while pending_names and time.time() < deadline:
        for name, dataset_id in DATASETS.items():
            if name not in pending_names:
                continue
            docs = _list_docs(dataset_id)
            # 注意：v0.26.4 API 的 run 字段返回字符串（UNSTART/RUNNING/CANCEL/DONE/FAIL）
            unsettled = [d for d in docs if d.get("run") not in ("DONE", "FAIL")]
            if unsettled:
                print(f"  [进行中] {name}: {len(unsettled)}/{len(docs)} 未终态")
                continue
            pending_names.discard(name)
            fails = [d for d in docs if d.get("run") == "FAIL"]
            dones = [d for d in docs if d.get("run") == "DONE"]
            # 对 DONE 文档逐个查 chunk 数，识别扫描版（0 chunk）
            zeros = []
            for d in dones:
                n = _chunk_count(dataset_id, d["id"])
                if n == 0:
                    zeros.append(d)
            print(f"  [终态] {name}: DONE={len(dones)} FAIL={len(fails)} 0-chunk={len(zeros)}")
            for d in fails:
                all_issues.append({"name": name, "doc": d["name"], "reason": "FAIL"})
            for d in zeros:
                all_issues.append({"name": name, "doc": d["name"], "reason": "0-chunk（扫描版?）"})
        if not pending_names:
            break
        if pending_names:
            time.sleep(20)
    for d in all_issues:
        print(f"  [问题] {d['name']} / {d['doc']}: {d['reason']}")
    print(f"[审计] 共 {len(all_issues)} 个问题文档，待 --flip-back 兜底")
    return 0 if not pending_names else 1


def cmd_flip_back() -> int:
    """将 FAIL 或 0-chunk 文档翻回 DeepDOC（关 RAPTOR/GraphRAG）并重解析。"""
    total_patched = 0
    for name, dataset_id in DATASETS.items():
        docs = _list_docs(dataset_id)
        targets: list[dict] = []
        for d in docs:
            if d.get("run") == "FAIL":
                targets.append(d)
            elif d.get("run") == "DONE" and _chunk_count(dataset_id, d["id"]) == 0:
                targets.append(d)
        for d in targets:
            if _patch_doc(dataset_id, d["id"], DEEPDOC_CFG):
                total_patched += 1
        if targets:
            _trigger_parse(dataset_id, [d["id"] for d in targets])
            print(f"  [翻回] {name}: {len(targets)} 份 → DeepDOC 重解析")
    print(f"[完成] 共翻回 {total_patched} 份（扫描版 PDF 用 DeepDOC OCR 兜底）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGFlow 全量文档重解析（优化配置）")
    parser.add_argument("--apply", action="store_true", help="PATCH 全部文档优化配置 + 重触发解析")
    parser.add_argument("--audit", action="store_true", help="轮询至全部终态并报告问题文档")
    parser.add_argument("--flip-back", action="store_true", help="FAIL/0-chunk 文档翻回 DeepDOC 重解析")
    args = parser.parse_args()
    if args.apply:
        return cmd_apply()
    if args.audit:
        return cmd_audit()
    if args.flip_back:
        return cmd_flip_back()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
