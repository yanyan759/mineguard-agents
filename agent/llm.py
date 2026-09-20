"""
大模型初始化模块

负责从 .env 中读取模型配置，并创建项目统一复用的模型对象
工作流节点都从这里导入 model，避免在多个文件里重复加载环境变量
"""

import os

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model

# find_dotenv 会从当前目录向上查找 .env，适合脚本和 Web 服务从不同入口启动的场景
load_dotenv(find_dotenv())

# ── 无有效 API Key 时不创建模型对象（离线可导入） ──
# 背景：OpenAI 客户端在构造期即校验 API Key，若这里无条件 init_chat_model，
# 则缺少 key 会让所有 import 本模块的离线验收套件（validate_workflow /
# validate_gap_closure / validate_llm_nodes 等）在导入阶段直接崩溃，
# 导致"无外部密钥也能跑通离线套件"的验收场景无法成立。
# 处理：无 key 或未配置模型名 → model=None；
#   · llm_service / agent_subgraph 仅在 LLM_NODES=on 时真正使用 model，
#     且外层已有"异常回退确定性逻辑"的兜底（不会因 None 崩溃）；
#   · bootstrap 对 model=None 单独输出"未配置 key"提示，不误报连通冒烟；
#   · 配置了真实 key 的行为与改造前完全一致。
_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
_model_name = os.getenv("LLM_QWEN_MAX")
if _api_key and _model_name:
    # 使用 OpenAI 兼容协议接入模型；具体模型名由 .env 中的 LLM_QWEN_MAX 控制
    model = init_chat_model(
        model=_model_name,
        model_provider="openai",
    )
else:
    model = None
