"""协同管控 Agent 配置模块。

挂载 Markdown/PDF 文件工具 + 权限校验 + 通知工具。
"""

from app.agent.prompts import sub_agents_content
from app.tools.markdown_tools import generate_markdown
from app.tools.notification_tool import resolve_recipients
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.permission_check_tool import check_permission

collaborative_control_agent = {
    "name": sub_agents_content["collaborative_control"]["name"],
    "description": sub_agents_content["collaborative_control"]["description"],
    "system_prompt": sub_agents_content["collaborative_control"]["system_prompt"],
    "tools": [
        generate_markdown,
        convert_md_to_pdf,
        check_permission,
        resolve_recipients,
    ],
}
