"""检查公开仓库是否仍符合轻量可复现发布边界。

只检查 Git index 中已经跟踪的文件，避免把本机的大型资料、运行产物或依赖目录
误认为公开仓库内容。该脚本不读取或上传文件，只输出违规路径并返回退出码。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


MAX_TRACKED_FILE_BYTES = 100 * 1024 * 1024
FORBIDDEN_PARTS = {
    ".chrome-verify",
    ".scratch",
    ".venv",
    "node_modules",
    "output",
    "updated",
}
SECRET_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
RAW_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".html",
    ".htm",
    ".pdf",
    ".txt",
    ".zip",
}
RAW_DATA_ROOTS = ("煤矿资料收集/", "真实数据投喂/", "deploy/ragflow/docs/real/")
TEMPLATE_MARKERS = ("模板", "template", "example", "sample")


def tracked_files(repo_root: Path) -> list[Path]:
    """读取 Git index 中的路径；不扫描未跟踪的本机文件。"""
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    names = completed.stdout.decode("utf-8").split("\0")
    return [repo_root / name for name in names if name]


def violations(repo_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        relative = path.relative_to(repo_root).as_posix()
        parts = set(Path(relative).parts)
        if parts & FORBIDDEN_PARTS:
            errors.append(f"forbidden generated/dependency path: {relative}")
        if Path(relative).name in SECRET_NAMES:
            errors.append(f"credential file: {relative}")
        if not path.exists():
            errors.append(f"tracked file missing from checkout: {relative}")
            continue
        size = path.stat().st_size
        if size > MAX_TRACKED_FILE_BYTES:
            errors.append(f"file exceeds 100 MiB: {relative} ({size} bytes)")
        if relative.startswith(RAW_DATA_ROOTS) and path.suffix.lower() in RAW_EXTENSIONS:
            # README、模板和索引可以留在仓库，原始语料应通过来源或 Release 分发。
            name = path.name.lower()
            if path.name.lower() != "readme.md" and not any(
                marker.lower() in name for marker in TEMPLATE_MARKERS
            ):
                errors.append(f"raw corpus path: {relative}")
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        files = tracked_files(repo_root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"PUBLIC_PACKAGE_AUDIT=error\n{exc}", file=sys.stderr)
        return 2
    errors = violations(repo_root, files)
    print(f"PUBLIC_PACKAGE_TRACKED_FILES={len(files)}")
    print(f"PUBLIC_PACKAGE_MAX_FILE_BYTES={MAX_TRACKED_FILE_BYTES}")
    if errors:
        print("PUBLIC_PACKAGE_AUDIT=failed")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PUBLIC_PACKAGE_AUDIT=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
