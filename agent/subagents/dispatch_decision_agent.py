"""调度决策 Agent 配置模块。

挂载 Markdown/PDF 文件工具 + 方案评分 + 合规检查工具。
"""

from app.agent.prompts import sub_agents_content
from app.tools.compliance_check_tool import check_compliance
from app.tools.markdown_tools import generate_markdown
from app.tools.pdf_tools import convert_md_to_pdf
from app.tools.plan_scoring_tool import score_plans

dispatch_decision_agent = {
    "name": sub_agents_content["dispatch_decision"]["name"],
    "description": sub_agents_content["dispatch_decision"]["description"],
    "system_prompt": sub_agents_content["dispatch_decision"]["system_prompt"],
    "tools": [
        generate_markdown,
        convert_md_to_pdf,
        score_plans,
        check_compliance,
    ],
}
