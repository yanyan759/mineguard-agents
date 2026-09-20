"""反思迭代 Agent 配置模块。

挂载文件读取 + Markdown + 知识候选暂存 + 执行回执查询工具。
"""

from app.agent.prompts import sub_agents_content
from app.tools.execution_feedback_tool import query_feedback
from app.tools.knowledge_candidate_tool import stage_candidate
from app.tools.markdown_tools import generate_markdown
from app.tools.upload_file_read_tool import read_file_content

reflection_iteration_agent = {
    "name": sub_agents_content["reflection_iteration"]["name"],
    "description": sub_agents_content["reflection_iteration"]["description"],
    "system_prompt": sub_agents_content["reflection_iteration"]["system_prompt"],
    "tools": [
        read_file_content,
        generate_markdown,
        query_feedback,
        stage_candidate,
    ],
}
