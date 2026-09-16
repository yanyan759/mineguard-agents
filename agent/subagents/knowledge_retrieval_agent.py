"""知识检索 Agent 配置模块。

整合 RAGFlow、Tavily、MySQL 查询工具 + 合规检查工具。
"""

from app.agent.prompts import sub_agents_content
from app.tools.db_tools import execute_sql_query, get_table_data, list_sql_tables
from app.tools.ragflow_tools import create_ask_delete, get_assistant_list
from app.tools.tavily_tool import internet_search

knowledge_retrieval_agent = {
    "name": sub_agents_content["knowledge_retrieval"]["name"],
    "description": sub_agents_content["knowledge_retrieval"]["description"],
    "system_prompt": sub_agents_content["knowledge_retrieval"]["system_prompt"],
    "tools": [
        get_assistant_list,
        create_ask_delete,
        internet_search,
        list_sql_tables,
        get_table_data,
        execute_sql_query,
    ],
}
