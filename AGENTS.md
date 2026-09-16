# MineGuard Agents 开发指引

本仓库根目录是独立运行的煤矿顶板灾变六 Agent 项目。技术基线见 [agent_design.md](agent_design.md)，安装与配置见 [README.md](README.md)。

## 架构与入口

- 前端：React、TypeScript、Vite，源码位于 `frontend/src/`。
- API：FastAPI，启动入口 `python -m uvicorn api.server:app --host 0.0.0.0 --port 8000`。
- 编排：LangGraph StateGraph；定义位于 `agent/workflow.py`，节点位于 `agent/workflow_nodes.py`。
- 六个业务节点：感知预警、知识检索、调度决策、资源评估、协同管控、反思迭代。
- SQLite 保存配置、资料与归档；Redis 保存检查点与分布式锁；外部业务能力通过适配器接入。

## 修改约定

1. 新增逻辑使用中文注释；修改数据契约时同步检查输入、快照、节点、结果 API、前端和测试。
2. 实际数据不得回退到演示 fixture，不得伪造现场身份、库存、执行回执或观测效果。
3. 保留运行编号、配置版本、资料来源与证据；高风险动作遵循人工审批和业务授权流程。
4. 凭据只放在被忽略的本地 `.env`；发布 `.env.example` 和 Compose 配置。
5. 本项目可独立运行，不依赖上层工作区的其他源码目录。不要引入本机绝对路径作为运行前提。

## 验证

在仓库根目录运行：

```bash
python -m unittest discover -s tests -p "test_*.py"
python tests/validate_fixtures.py
python tests/validate_tools.py
```

在 `frontend/` 执行 `npm.cmd run build`（Windows）或 `npm run build`（macOS/Linux）。外部服务验收需要先按 README 启动 Redis 或相应可选服务。
