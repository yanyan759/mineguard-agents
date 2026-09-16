"""资源评估 Agent 配置模块。

挂载 MySQL 只读查询工具 + 资源核算工具。
"""

from app.agent.prompts import sub_agents_content
from app.tools.db_tools import execute_sql_query, get_table_data, list_sql_tables
from app.tools.resource_accounting_tool import calculate_gaps

resource_evaluation_agent = {
    "name": sub_agents_content["resource_evaluation"]["name"],
    "description": sub_agents_content["resource_evaluation"]["description"],
    "system_prompt": sub_agents_content["resource_evaluation"]["system_prompt"],
    "tools": [
        list_sql_tables,
        get_table_data,
        execute_sql_query,
        calculate_gaps,
    ],
}
