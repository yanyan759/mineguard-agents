"""
Markdown 转 PDF 工具模块

用于将 Agent 生成的 Markdown 方案和报表转换为 PDF，兼容煤矿领域模板。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from app.api.context import get_session_context


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "convert_md_to_pdf",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def convert_md_to_pdf(md_filename: str, pdf_filename: str | None = None) -> dict:
    """将 Markdown 文件转换为 PDF。

    调用前需确保 md_filename 已通过 generate_markdown 生成并存在于工作目录。
    生成后原 Markdown 文件保留，PDF 另行保存。

    :param md_filename: 源 Markdown 文件名
    :param pdf_filename: 目标 PDF 文件名（可选，默认同主文件名 .pdf）
    """
    if not md_filename:
        return _build_result("error", error="Markdown 文件名不能为空")

    try:
        session_dir = get_session_context()
    except Exception:
        session_dir = "."

    md_path = Path(session_dir) / Path(md_filename).name
    if not md_path.exists():
        return _build_result("error", error=f"源文件不存在: {md_filename}")

    if pdf_filename is None:
        pdf_filename = md_path.stem + ".pdf"
    pdf_path = Path(session_dir) / Path(pdf_filename).name

    try:
        from app.utils.word_converter import convert_markdown_to_pdf
        convert_markdown_to_pdf(str(md_path), str(pdf_path))
        return _build_result(
            "success",
            data={"source": str(md_path), "output": str(pdf_path),
                  "size_bytes": pdf_path.stat().st_size if pdf_path.exists() else 0},
        )
    except ImportError:
        # 可选依赖未安装时返回结构化提示
        md_content = md_path.read_text(encoding="utf-8")
        pdf_path.write_text(f"[PDF PLACEHOLDER]\n{md_content}", encoding="utf-8")
        return _build_result(
            "success",
            data={"source": str(md_path), "output": str(pdf_path),
                  "note": "PDF 转换库未安装，已生成占位文件。安装 ReportLab 后启用正式转换。"},
        )
    except Exception as e:
        return _build_result("error", error=str(e))
