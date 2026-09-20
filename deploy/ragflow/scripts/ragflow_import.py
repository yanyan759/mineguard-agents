"""RAGFlow 知识库导入与助手创建脚本（REST API）。

流程：建 3 个知识库（dataset）→ 上传 prepare_real_docs.py 归类的可选资料
（docs/real/<知识库名>/，pdf/html/txt/doc/docx）→ 触发解析 → 轮询解析完成
→ 创建 3 个绑定知识库的检索助手。

助手名称/知识库名与 tools/ragflow_tools.py 的 _COAL_MINE_ASSISTANTS 映射一致：
    coal_mine_safety_regulations / coal_mine_accident_cases / coal_mine_support_schemes

前置：agent1/.env 配置 RAGFLOW_API_URL 与 RAGFLOW_API_KEY；RAGFlow 界面配置
Ollama 提供方（bge-m3 embedding + deepseek-r1:7b 或 DeepSeek API LLM），
并把注册后的模型名填入 RAGFLOW_EMBEDDING_MODEL / RAGFLOW_LLM_MODEL。
未配置时进入 offline 模式：校验文档目录结构 + 打印待执行计划（dry-run 校验）。

⚠️ RAGFlow 各版本 REST 端点可能有差异（/api/v1/datasets 等），
实际部署后如接口变更请按 RAGFlow 官方文档微调本脚本。

用法：
    cd agent1
    python deploy/ragflow/scripts/ragflow_import.py            # 离线校验
    python deploy/ragflow/scripts/ragflow_import.py --execute  # 连接真实 RAGFlow 导入
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

from dotenv import load_dotenv

# 路径约定：scripts/ → ragflow/ → deploy/ → agent1/
AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# 加载 agent1/.env（RAGFLOW_API_URL / RAGFLOW_API_KEY / RAGFLOW_EMBEDDING_MODEL / RAGFLOW_LLM_MODEL）
load_dotenv(AGENT1_ROOT / ".env")
# 真实资料已由 prepare_real_docs.py 归类到 docs/real/<知识库名>/（pdf/html/txt/doc/docx 混合）
DOCS_DIR = Path(os.getenv(
    "MINEGUARD_CORPUS_DIR",
    str(AGENT1_ROOT / "deploy" / "ragflow" / "docs" / "real"),
))


def _documents(folder: str) -> list[Path]:
    """返回实际资料文件，跳过目录说明和占位文件。"""
    target = DOCS_DIR / folder
    if not target.exists():
        return []
    return sorted(
        f for f in target.iterdir()
        if f.is_file() and f.name not in {"README.md", ".gitkeep"}
    )

# 知识库名 → real/ 下的归并目录（与 tools/ragflow_tools.py 助手映射对齐）
DATASETS = {
    "coal_mine_safety_regulations": "coal_mine_safety_regulations",
    "coal_mine_accident_cases": "coal_mine_accident_cases",
    "coal_mine_support_schemes": "coal_mine_support_schemes",
}

# 助手定义（名称/描述与 tools/ragflow_tools.py 一致，便于工具按名匹配）
ASSISTANTS = [
    {
        "name": "煤矿安全规程检索助手",
        "description": "检索《煤矿安全规程》《冲击地压防治技术规范》等法规标准条款",
        "dataset": "coal_mine_safety_regulations",
        "system_instruction": "你是煤矿安全规程检索助手，只依据知识库条款回答，必须标注条款出处。",
    },
    {
        "name": "顶板事故案例检索助手",
        "description": "检索历史顶板事故案例（位置/诱因/前兆/处置/后果）",
        "dataset": "coal_mine_accident_cases",
        "system_instruction": "你是顶板事故案例检索助手，检索时返回案例来源与处置措施。",
    },
    {
        "name": "支护方案检索助手",
        "description": "检索历史支护方案和专家研判记录",
        "dataset": "coal_mine_support_schemes",
        "system_instruction": "你是支护方案检索助手，检索时说明方案适用条件与来源。",
    },
]

_RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY")
_RAGFLOW_API_URL = (os.getenv("RAGFLOW_API_URL") or "http://localhost:9380").rstrip("/")
# embedding 模型名：v0.26.4 要求 <模型名>@<提供方> 格式（Ollama 提供方已配置 bge-m3:567m）
_RAGFLOW_EMBEDDING_MODEL = os.getenv("RAGFLOW_EMBEDDING_MODEL", "bge-m3:567m@Ollama")
# 助手绑定的对话 LLM 模型名：同上格式（Ollama deepseek-r1:7b）
_RAGFLOW_LLM_MODEL = os.getenv("RAGFLOW_LLM_MODEL", "deepseek-r1:7b@Ollama")
_TIMEOUT = 30


def _headers() -> dict:
    """构造 RAGFlow REST 鉴权头。"""
    if not _RAGFLOW_API_KEY:
        raise RuntimeError("未配置 RAGFLOW_API_KEY（agent1/.env）")
    return {"Authorization": f"Bearer {_RAGFLOW_API_KEY}"}


def _validate_docs_tree() -> None:
    """校验文档目录是否就绪（离线也可运行）。"""
    print("[校验] 文档目录结构：")
    for dataset, folder in DATASETS.items():
        target = DOCS_DIR / folder
        files = _documents(folder)
        print(f"  {dataset} ← {folder}/（{len(files)} 篇）")
        if not files:
            print("    [提示] 无文档，请先运行 prepare_real_docs.py")
    print(f"[校验] 完成 → {DOCS_DIR}")


def _create_dataset(name: str) -> str:
    """创建知识库并返回 dataset_id（已存在则复用）。"""
    # 先查重：同名 dataset 直接复用，避免重复创建
    resp = requests.get(f"{_RAGFLOW_API_URL}/api/v1/datasets", headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    for item in resp.json().get("data", []):
        if item.get("name") == name:
            print(f"  [复用] dataset {name} = {item['id']}")
            return item["id"]

    body = {
        "name": name,
        "permission": "me",
        "chunk_method": "naive",
        "embedding_model": _RAGFLOW_EMBEDDING_MODEL,
    }
    resp = requests.post(f"{_RAGFLOW_API_URL}/api/v1/datasets", json=body, headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    dataset_id = resp.json()["data"]["id"]
    print(f"  [创建] dataset {name} = {dataset_id}")
    return dataset_id


# 扩展名 → RAGFlow 可识别的 MIME 类型
_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _content_type(filename: Path) -> str:
    """按扩展名返回 MIME 类型，未知类型用二进制流。"""
    return _CONTENT_TYPES.get(filename.suffix.lower(), "application/octet-stream")


def _upload_docs(dataset_id: str, folder: str) -> list[str]:
    """上传分类目录下全部真实资料（pdf/html/txt/doc/docx），返回 document_id 列表。

    v0.26.4 上传响应 data 为数组；按文件名去重保证幂等（重复运行不产生重复文档）。
    """
    doc_ids: list[str] = []
    # 先取已有文档名，跳过已上传文件（支持断点续传/重复运行）
    existing = set()
    try:
        r = requests.get(
            f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents",
            # v0.26.4 限制 page_size ≤ 100，超限返回 code:100 + data:null
            params={"page": 1, "page_size": 100}, headers=_headers(), timeout=_TIMEOUT,
        )
        r.raise_for_status()
        # data 可能为 null（接口错误/超限），用 or {} 兜底避免 None.get 崩溃
        existing = {d.get("name") for d in (r.json().get("data") or {}).get("docs", [])}
    except Exception as e:
        print(f"  [警告] 读取已有文档列表失败（继续上传）：{e}")

    target = DOCS_DIR / folder
    for file in _documents(folder):
        if file.name in existing:
            print(f"  [跳过] {file.name}（已存在）")
            continue
        with file.open("rb") as f:
            resp = requests.post(
                f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents",
                headers=_headers(),
                files={"file": (file.name, f, _content_type(file))},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            doc_ids.append(resp.json()["data"][0]["id"])
        print(f"  [上传] {file.name}")
    return doc_ids


def _parse_docs(dataset_id: str, doc_ids: list[str]) -> None:
    """触发文档解析并确认进入解析队列（v0.26.4 用 /documents/parse + 列表确认）。

    解析是 RAGFlow 服务端后台任务（CPU 密集型，耗时长），本脚本只负责触发
    并确认文档进入 RUNNING/DONE 状态；全量完成由外部核对（GET /documents）。
    无新增文档时（断点续传/重复运行）不重复触发，直接返回。
    """
    if not doc_ids:
        print("  [解析] 无新增文档（已在解析中或已完成），跳过触发")
        return
    resp = requests.post(
        f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents/parse",
        json={"document_ids": doc_ids},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    # 短轮询确认已启动（最多 30s），未确认仅警告不阻塞主流程
    for _ in range(15):
        r = requests.get(
            f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents",
            # v0.26.4 限制 page_size ≤ 100，超限返回 code:100 + data:null
            params={"page": 1, "page_size": 100},
            headers=_headers(), timeout=_TIMEOUT,
        )
        r.raise_for_status()
        docs = {d["id"]: d for d in (r.json().get("data") or {}).get("docs", [])}
        started = sum(1 for doc_id in doc_ids if docs.get(doc_id, {}).get("run") in ("RUNNING", "DONE"))
        if started:
            print(f"  [解析] 已触发，{started}/{len(doc_ids)} 文档进入解析队列")
            return
        time.sleep(2)
    print("  [警告] 解析未确认启动，请在 RAGFlow 界面检查状态")


def _create_assistant(assistant: dict, dataset_id: str) -> None:
    """创建绑定指定知识库的对话助手（v0.26.4 Chat API），已存在则跳过。

    注意：v0.26.4 已废弃 /agents/create_assistant 端点，改用 /api/v1/chats。
    """
    resp = requests.get(f"{_RAGFLOW_API_URL}/api/v1/chats", headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    # v0.26.4：GET /chats 返回 data.chats 列表（不是 data 裸列表），null 时兜底空列表
    for item in resp.json().get("data", {}).get("chats", []) or []:
        if item.get("name") == assistant["name"]:
            print(f"  [复用] assistant {assistant['name']}")
            return

    body = {
        "name": assistant["name"],
        "description": assistant["description"],
        "dataset_ids": [dataset_id],
        "llm_id": _RAGFLOW_LLM_MODEL,
        "prompt_config": {"system": assistant["system_instruction"]},
    }
    resp = requests.post(
        f"{_RAGFLOW_API_URL}/api/v1/chats",
        json=body, headers=_headers(), timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    print(f"  [创建] assistant {assistant['name']}")


def _chat_exists(name: str) -> bool:
    """按名称检查对话助手是否已存在。"""
    resp = requests.get(f"{_RAGFLOW_API_URL}/api/v1/chats", headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    return any(c.get("name") == name for c in resp.json().get("data", {}).get("chats", []) or [])


def _dataset_has_parsed_doc(dataset_id: str) -> bool:
    """检查知识库是否已有解析完成的文档（RAGFlow 建助手的前置条件）。"""
    r = requests.get(
        f"{_RAGFLOW_API_URL}/api/v1/datasets/{dataset_id}/documents",
        params={"page": 1, "page_size": 100},
        headers=_headers(), timeout=_TIMEOUT,
    )
    r.raise_for_status()
    docs = r.json().get("data", {}).get("docs", []) or []
    return any((d.get("progress") or 0) >= 1.0 for d in docs)


def ensure_assistants(max_wait_seconds: int = 6 * 3600) -> int:
    """轮询等待各知识库有解析文档后创建缺失助手。

    RAGFlow 创建 chat 助手要求知识库已有解析完成的文档（code=102 校验），
    而解析是后台 CPU 任务（耗时长）。本函数每 30s 检查一次：知识库有
    解析完成文档且助手缺失时补建；全部就绪返回 0，超时返回 1。
    """
    deadline = time.time() + max_wait_seconds
    done_names: set[str] = set()
    while time.time() < deadline:
        for assistant in ASSISTANTS:
            if assistant["name"] in done_names:
                continue
            if _chat_exists(assistant["name"]):
                print(f"  [就绪] assistant {assistant['name']} 已存在")
                done_names.add(assistant["name"])
                continue
            dataset_id = _resolve_dataset_id(assistant["dataset"])
            if not _dataset_has_parsed_doc(dataset_id):
                print(f"  [等待] {assistant['name']}：知识库 {assistant['dataset']} 尚无解析完成文档")
                continue
            _create_assistant(assistant, dataset_id)
            done_names.add(assistant["name"])
        if len(done_names) == len(ASSISTANTS):
            print("[OK] 3 个检索助手全部就绪")
            return 0
        time.sleep(30)
    missing = [a["name"] for a in ASSISTANTS if a["name"] not in done_names]
    print(f"[错误] 超时仍未就绪：{', '.join(missing)}（请在 RAGFlow 界面检查解析状态）")
    return 1


def main() -> int:
    """执行导入流程；--execute 才真正连接 RAGFlow。"""
    parser = argparse.ArgumentParser(description="RAGFlow 知识库导入与助手创建")
    parser.add_argument("--execute", action="store_true", help="连接真实 RAGFlow 执行导入（默认仅离线校验）")
    parser.add_argument(
        "--ensure-assistants", action="store_true",
        help="仅确保 3 个助手存在（等待知识库解析完成后补建缺失助手，长跑模式）",
    )
    args = parser.parse_args()

    if args.ensure_assistants:
        print("=" * 60)
        print("  确保 3 个检索助手就绪（等待解析完成）")
        print("=" * 60)
        return ensure_assistants()

    print("=" * 60)
    print("  RAGFlow 知识库导入")
    print("=" * 60)

    if not args.execute:
        # 离线模式：校验文档树并打印执行计划
        _validate_docs_tree()
        print()
        print("[离线校验通过] 未指定 --execute，未连接 RAGFlow。")
        print("部署 RAGFlow 并配置 .env 后运行：")
        print("    python deploy/ragflow/scripts/ragflow_import.py --execute")
        print("执行计划：创建 3 个知识库 + 上传文档 + 解析 + 创建 3 个助手")
        return 0

    # 在线执行模式
    try:
        if not _RAGFLOW_API_KEY:
            print("[错误] 未配置 RAGFLOW_API_KEY，请在 agent1/.env 填写后重试")
            return 1

        for dataset, folder in DATASETS.items():
            print(f"[知识库] {dataset}")
            dataset_id = _create_dataset(dataset)
            doc_ids = _upload_docs(dataset_id, folder)
            _parse_docs(dataset_id, doc_ids)

        for assistant in ASSISTANTS:
            dataset_id = _resolve_dataset_id(assistant["dataset"])
            _create_assistant(assistant, dataset_id)

        print()
        print("[OK] RAGFlow 导入完成：3 知识库 + 3 助手")
        return 0
    except Exception as e:
        print(f"[错误] 导入失败：{e}")
        return 1


def _resolve_dataset_id(name: str) -> str:
    """按名称解析 dataset_id（与 _create_dataset 的查重逻辑一致）。"""
    resp = requests.get(f"{_RAGFLOW_API_URL}/api/v1/datasets", headers=_headers(), timeout=_TIMEOUT)
    resp.raise_for_status()
    for item in resp.json().get("data", []):
        if item.get("name") == name:
            return item["id"]
    raise RuntimeError(f"知识库 {name} 不存在，请先创建")


if __name__ == "__main__":
    sys.exit(main())
