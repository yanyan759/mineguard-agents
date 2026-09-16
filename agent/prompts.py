"""
六 Agent 提示词配置加载模块

负责读取 app/prompt/prompts.yml 中的调度中心和六个业务 Agent 配置，供
LangGraph 节点及可选 LLM 子图统一使用。
"""

from pathlib import Path
from typing import Any

import yaml


def load_yaml(file_path: Path) -> dict[str, Any]:
    """
    加载 YAML 配置文件。

    :param file_path: YAML 文件路径
    :return: YAML 解析后的字典
    """
    with open(file_path, "r", encoding="utf-8") as f:
        # safe_load 只按数据解析 YAML，避免 yaml.load 可能触发的对象构造风险
        return yaml.safe_load(f)


# 当前文件位于 app/agent/prompts.py，parents[1] 即 app（agent1）目录
app_root_path = Path(__file__).parents[1]
yaml_file_path = app_root_path / "prompt" / "prompts.yml"

prompt_yaml_content = load_yaml(yaml_file_path)

# 工作流调度中心提示词配置
orchestrator_content = prompt_yaml_content["orchestrator"]

# 六个业务 Agent 配置集合（预期 6 个 key）
sub_agents_content = prompt_yaml_content["sub_agents"]

# 启动时校验 YAML 包含全部六 Agent 配置
_expected_agent_keys = {
    "perception_warning",
    "knowledge_retrieval",
    "dispatch_decision",
    "resource_evaluation",
    "collaborative_control",
    "reflection_iteration",
}
_actual_keys = set(sub_agents_content.keys())
if _actual_keys != _expected_agent_keys:
    missing = _expected_agent_keys - _actual_keys
    extra = _actual_keys - _expected_agent_keys
    raise RuntimeError(
        f"prompts.yml sub_agents 配置与预期不符："
        f"缺失={missing or '无'}，多余={extra or '无'}"
    )
