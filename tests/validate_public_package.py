"""公开仓库发布包离线验收。

这个脚本检查的是当前 Git 文件集，而不是本机忽略目录。它用于保证公开仓库
不携带原始语料、凭据或构建产物，同时保留默认演示链路所需的最小 fixture。
真实资料验收由 validate_real_data.py 在部署方提供外部路径后单独执行。

运行：
    cd agent1
    python tests/validate_public_package.py
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAX_TRACKED_BYTES = 10 * 1024 * 1024
FORBIDDEN_SUFFIXES = {".pdf", ".doc", ".docx", ".zip", ".7z", ".rar", ".html", ".htm"}
FORBIDDEN_NAMES = {
    ".env",
    "real_accident_cases.yaml",
    "事故案例_真实报告FINAL.yaml",
    "事故案例_真实报告V1.yaml",
    "codex_URL映射.json",
    "标注示例_真实报告10条.csv",
    "标注示例_真实报告10条.jsonl",
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    files = tracked_files()
    print(f"[检查] Git 跟踪文件：{len(files)} 个")

    forbidden = [
        path for path in files
        if (Path(path).suffix.lower() in FORBIDDEN_SUFFIXES and path != "frontend/index.html")
        or Path(path).name in FORBIDDEN_NAMES
        or Path(path).name.startswith(".env") and Path(path).name != ".env.example"
    ]
    if forbidden:
        fail("公开包包含不应跟踪的文件：" + ", ".join(forbidden[:10]))

    oversized = []
    for relative in files:
        path = ROOT / relative
        if path.is_file() and path.stat().st_size > MAX_TRACKED_BYTES:
            oversized.append(f"{relative} ({path.stat().st_size} bytes)")
    if oversized:
        fail("公开包存在超过 10 MB 的跟踪文件：" + ", ".join(oversized))

    required = [
        "tests/fixtures/accident_cases.yaml",
        "tests/fixtures/regulations.yaml",
        "tests/fixtures/support_plans.yaml",
        "tests/fixtures/verified_regulation_excerpt.txt",
        "deploy/ragflow/docs/real/coal_mine_accident_cases/README.md",
        "deploy/ragflow/docs/real/coal_mine_safety_regulations/README.md",
        "deploy/ragflow/docs/real/coal_mine_support_schemes/README.md",
        ".env.example",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        fail("公开包缺少默认运行所需文件：" + ", ".join(missing))

    real_root = ROOT / "deploy" / "ragflow" / "docs" / "real"
    unexpected_real = [
        path.relative_to(real_root).as_posix()
        for path in real_root.rglob("*")
        if path.is_file() and path.name not in {"README.md", ".gitkeep"}
    ]
    if unexpected_real:
        fail("RAGFlow 真实资料目录包含非占位文件：" + ", ".join(unexpected_real))

    # 默认资料索引应为空；外部目录由 MINEGUARD_CORPUS_DIR 显式启用。
    sys.path.insert(0, str(ROOT))
    knowledge_sources = importlib.import_module("app.agent.knowledge_sources")
    if knowledge_sources._index_real_docs():
        fail("默认真实资料索引非空，请将资料移到仓库外并配置 MINEGUARD_CORPUS_DIR")

    importlib.import_module("api.server")
    print("[通过] 无原始语料、凭据或大文件；fixture、占位目录和 API 导入均就绪")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, subprocess.CalledProcessError) as exc:
        print(f"[失败] {exc}")
        raise SystemExit(1)
