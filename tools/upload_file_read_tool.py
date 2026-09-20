"""
文件读取工具模块

支持监测数据文件格式：
- CSV 微震数据 / JSON 监测数据 / YAML 规程案例 / 通用文本
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from app.api.context import get_session_context


def _build_result(status: str, data: Any = None, error: str | None = None,
                  filename: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "read_file_content", "filename": filename,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def read_file_content(filename: str) -> dict:
    """读取工作目录中的文件内容。

    支持格式：.txt/.md(原文) .csv(表格) .json(结构化) .yaml/.yml(规程/案例)
    :param filename: 文件名（仅允许会话工作目录内的文件）
    """
    if not filename or not filename.strip():
        return _build_result("rejected", error="文件名不能为空")

    try:
        session_dir = get_session_context()
    except Exception:
        session_dir = "."
    file_path = Path(session_dir) / Path(filename).name

    if not file_path.exists():
        return _build_result("error", error=f"文件不存在: {filename}", filename=filename)

    try:
        suffix = file_path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
                content = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            except ImportError:
                content = file_path.read_text(encoding="utf-8")
            data = {"format": "yaml", "content": content}
        elif suffix == ".csv":
            lines = [l.strip() for l in file_path.read_text(encoding="utf-8").split("\n") if l.strip()]
            headers = [h.strip() for h in lines[0].split(",")] if lines else []
            rows = [[c.strip() for c in l.split(",")] for l in lines[1:101]]
            data = {"format": "csv", "headers": headers, "rows": rows, "row_count": len(rows)}
        elif suffix == ".json":
            data = {"format": "json", "content": json.loads(file_path.read_text(encoding="utf-8"))}
        else:
            data = {"format": "text", "content": file_path.read_text(encoding="utf-8")}
        return _build_result("success", data=data, filename=filename)
    except Exception as e:
        return _build_result("error", error=str(e), filename=filename)
