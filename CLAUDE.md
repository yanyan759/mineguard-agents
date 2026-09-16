# MineGuard Agents 开发说明

本目录是煤矿顶板灾变监测、预警、决策与协同处置系统的唯一运行代码。系统由
六个业务 Agent 组成，使用 LangGraph StateGraph 编排，FastAPI 提供接口，React
前端提供操作台。`../code/` 仅是历史参考目录，不属于本项目运行路径。

## 开发规则

1. 所有改动限定在 `agent1/`，不要修改 `../code/`。
2. 数据契约、工作流节点和 API 变更必须同步更新测试与文档。
3. 新增逻辑使用中文注释，说明业务原因和数据来源。
4. 结果必须保留输入、配置版本、资料来源和运行编号，便于复核。
5. 高风险动作必须经过人工审批；任何模型或 Agent 都不能直接解除预警、停产、断电或撤人。

## 运行入口

```bash
cd agent1
python bootstrap.py
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

浏览器端位于 `frontend/`：

```bash
cd agent1/frontend
npm.cmd install
npm.cmd run dev
```

HTTP 入口是 `POST /api/workflow/start`，同步 CLI/测试入口是
`app.agent.workflow_entry.run_workflow()`。两者执行同一套工作流。

## 目录结构

```
agent1/
├── agent/                 # StateGraph、节点实现、结果提取和可选模型增强
│   ├── workflow.py        # 六节点图和条件回退
│   ├── workflow_nodes.py  # 六个业务节点
│   ├── workflow_entry.py  # CLI/测试同步入口
│   ├── workflow_state.py  # 节点共享状态
│   ├── prompts.py         # YAML 提示词加载
│   └── subagents/         # 六个 Agent 的职责和工具白名单
├── api/                   # FastAPI、WebSocket、配置、资料和业务接口
├── schemas/               # Pydantic 输入输出契约
├── services/              # 监测统计、配置、资料、资源和业务适配器
├── tools/                 # 知识、资源、合规、通知和回执工具
├── mcp_server/            # MCP 工具服务及客户端桥接
├── config/                # 风险方法和运行参数
├── prompt/prompts.yml     # 调度中心和六 Agent 的提示词
├── deploy/                # Redis、MySQL、Neo4j 和知识服务 Compose 配置
├── frontend/              # React + TypeScript 操作台
└── tests/                 # 契约、节点、API 和业务链路验收
```

## 六 Agent 流程

```
感知预警 → 知识检索 → 调度决策 → 资源评估 → 协同管控 → 反思迭代
```

感知节点只使用确定性监测统计和已选方法计算风险；其余节点可在
`LLM_NODES=on` 时使用配置的模型和 MCP 工具增强解释，失败会回退到确定性实现。
资源不足会返回调度决策重新评估，协同管控在高风险动作处产生人工审批中断，反思
节点根据后续观测决定结束或定向回退。

## 配置和数据

- `.env.example` 是配置模板，凭据只放在本地 `.env`。
- `REDIS_URL` 配置后启用 Redis 检查点和分布式锁；未配置时使用进程内检查点，仅适合开发。
- 矿井参数、资料分类和资源台账通过 `/api/configuration` 接口保存，启动任务时冻结版本快照。
- 自定义监测摘要和原始记录都走 `scenario_id=custom`，不会套用预设场景结果。
- 结果中的每条证据都携带来源 ID、配置版本或输入字段，前端展示解析、入库和本次使用状态。

## 测试

```bash
cd agent1
python -m unittest discover -s tests -p "test_*.py"
python tests/validate_fixtures.py
python tests/validate_tools.py
python tests/validate_workflow.py
python tests/validate_gap_closure.py
```

需要外部服务时再运行对应验收脚本：

```bash
python tests/validate_redis_phase8.py
python tests/validate_mysql_phase7.py
python tests/validate_neo4j_phase8.py
LLM_NODES=off python tests/validate_llm_nodes.py
LLM_NODES=on python tests/validate_llm_nodes.py
```

前端构建检查：

```bash
cd agent1/frontend
npm.cmd run build
```

## 修改工作流时的检查清单

提交前确认：

- 新字段已加入 `schemas/`，并在输入解析、节点消费、结果 API 和前端展示中贯通；
- 运行记录包含 `run_id`、`thread_id`、配置快照和资料证据；
- 资源、权限、通知、工单和回执接口的失败状态不会被伪装成成功；
- WebSocket 事件中的 Agent 名称与 `api/workflow_runner.py` 映射一致；
- `python -m unittest discover` 和 `npm.cmd run build` 均通过。
