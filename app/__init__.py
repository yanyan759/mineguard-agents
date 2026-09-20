"""MineGuard Agents 的运行时 app 包入口。

通过扩展 ``__path__``，让从仓库根目录启动服务时仍能解析
``from app.xxx``，同时保持各业务模块按领域目录组织。
"""

from pathlib import Path

# agent1/ 根目录
_agent1_root = Path(__file__).resolve().parent.parent

# 扩展搜索路径：from app.schemas.workflow → agent1/schemas/workflow.py
_extra_path = str(_agent1_root)
if _extra_path not in __path__:
    __path__.append(_extra_path)
