# 煤矿顶板灾变监测六 Agent 技术设计

版本：v2.0  
适用代码：`agent1/`（以当前代码和 API 为准）  
更新日期：2026-09-16

## 1. 文档定位

本文是当前系统的技术基线，说明真实数据如何进入六 Agent 工作流、各节点如何产生结果、结果如何追溯以及哪些动作必须经过人工和业务系统确认。文档描述的是 `agent1` 当前实现，不是早期原型或外部平台的设计。

系统面向煤矿顶板灾变相关的监测摘要、原始事件、案例、规程、支护方案、资源台账和执行反馈。它输出风险判断、处置建议、资源核算、协同状态和复核结果，供值班、调度和安全管理人员研判。任何软件结果都不能替代矿方批准的规程、现场检查和法定审批。

## 2. 设计原则与边界

1. **实际数据优先**：实际模式只读取本次提交的数据、适用的参数版本、已选入配置的资料和冻结的资源快照，不读取七个演示场景、演示库存或模拟风险下降。
2. **规则与解释分离**：风险等级、阈值命中、权限、资源数量和状态转换由确定性代码或业务适配器约束；自然语言模型（如启用）只能补充解释和结构化提取，不能改写规则定级。
3. **证据先于结论**：每个节点保存输入快照、资料哈希、规则/方法版本、计算结果和时间。没有证据的字段显示“待输入”或“不适用”，不伪造概率、相似度或执行效果。
4. **建议与执行分离**：工作流可以生成建议；通知、工单、停产、撤人、设备控制等外部动作必须由有权限人员显式提交，并由业务服务返回可验证回执。
5. **不可变运行**：任务启动时冻结配置、资料、库存和评估结果。之后修改配置不会改变已经完成的运行；需要新版本时提交新运行。

系统不内置矿方最终阈值、训练好的预测权重或外部业务账号。没有适用且已批准的配置时，系统仍可计算统计量和数据质量，但不会自动宣称现场五色风险等级或方案可执行。

## 3. 总体架构

```text
React + Vite 前端
  ├─ 场景演示 / 实际数据输入 / 资料配置 / 业务接入 / 结果与轨迹
  └─ REST + WebSocket
        ↓
FastAPI API
  ├─ configuration_routes：配置、资料、预检、模板
  ├─ workflow_runner：启动、状态、审批、取消、恢复
  ├─ business_routes：身份、现场核验、通知、工单、回执
  └─ monitoring / upload：监测数据和附件
        ↓
LangGraph StateGraph
  感知预警 → 知识检索 → 调度决策 → 资源评估 → 协同管控 → 反思迭代
        ↓                         ↘ 人工审批/现场核验
SQLite 配置与归档、Redis Checkpoint、可选外部业务适配器
```

技术栈：Python 3.12+、FastAPI、Pydantic v2、LangGraph、Redis Stack、SQLite、httpx；前端使用 React 19、Vite 7、TypeScript、Ant Design。资料解析使用本地 JSON/CSV/TXT/Markdown/PDF/DOCX 适配器。Redis 用于检查点和分布式锁；SQLite 保存配置版本、资料索引、运行快照、反馈和业务账本。

## 4. 两种运行模式

### 4.1 演示模式

前端七个场景卡片通过 `FixtureDataSource` 读取 `tests/fixtures` 中的固定输入，用于展示流程、审批中断、资源不足和回退路由。它们不是实时监测，也不代表矿方阈值。演示结果不得写入实际资料索引或业务系统。

### 4.2 实际数据模式

用户在前端提交摘要或原始事件 JSON，并选择矿井、巷道、测区、参数版本和资料版本。`prepare_actual()` 完成作用域校验、字段校验、规则预检、资料选择、库存读取和快照生成；只有预检成功的快照才能启动六节点工作流。实际节点读取 `WorkflowState.actual_snapshot`，不会回退到 fixture。

## 5. 核心数据契约

### 5.1 运行上下文

`WorkflowContext` 包含 `workflow_run_id`、`trace_id`、`thread_id`、`mine_id`、`roadway_id`、操作者、开始时间、迭代次数和最大迭代次数。所有事件、结果和审计记录使用运行编号关联。

### 5.2 实际任务

摘要任务至少包含：

```json
{
  "mine_id": "MINE-021",
  "roadway_id": "RDW-021",
  "area": "工作面-21",
  "observed_at": "2026-09-16T10:00:00+08:00",
  "window_minutes": 60,
  "event_count": 20,
  "max_energy_j": 82000,
  "frequency_per_hour": 20,
  "b_value": 0.72,
  "missing_rate": 0.01,
  "high_energy_count": 2,
  "spatial_cluster_fraction": 0.65,
  "resources": {
    "personnel": [],
    "support_materials": [],
    "equipment": []
  }
}
```

原始事件任务还需提供事件 ID、时间、能量、坐标和采样元数据。服务端会校验单位、时区、时间窗口、重复事件、缺失率以及高能事件口径（当前默认 `>=50000 J`）。总事件数、最大能量和高能数量必须彼此一致，否则返回 422 并指出字段矛盾。

### 5.3 统一节点结果

六个结果模型均包含 Agent 名称、状态、摘要、`evidence`、`warnings`、`errors`、开始/结束时间和 `next_action`。实际规则计算的 `risk_score`、置信度和因子趋势只有在方法与数据支持时才填值；没有校准依据时使用 `null`，前端显示“待输入/不适用”。

证据项至少包含来源类型、来源 ID、描述和可审计值。资料证据带文件哈希、版本、来源、矿井/巷道/测区和观测时间。

## 6. 六 Agent 职责与输出

### 6.1 感知预警

输入是冻结的监测任务和适用参数。节点执行数据质量检查、统计计算和阈值规则，输出风险等级（仅在配置允许时）、命中规则、异常项、致灾因子、数据完整性、复查周期和快速告警标记。规则结果的证据包括观测值、阈值、参数版本和计算方法。

### 6.2 知识检索

节点按矿井、巷道、测区和配置选中的资料筛选案例、规程和支护方案。关键词命中用于解释匹配，不冒充经过校准的相似度概率。输出每份资料的资产 ID、版本、哈希、来源、适用范围和本次是否使用；缺少某类资料时显示“待上传并选入配置”。

### 6.3 调度决策

节点融合预警、资料和可选的结构化处置方案，生成动作序列、责任角色、时限、审批要求、禁止动作和资源需求。若没有结构化方案，至少生成“复查本次观测指标并记录现场检查结果”的可追溯建议，并明确完整资源需求待配置。决策理由引用上游证据，不由自由文本覆盖规则。

### 6.4 资源评估

节点将任务中的资源需求与冻结库存逐项比较，输出 `quantity_check`（`sufficient`、`insufficient`、`not_provided`）、缺口、阻塞项和执行准备状态。数量满足只表示账面核算通过；`execution_readiness` 在实名现场核验前保持 `unverified`。

### 6.5 协同管控

节点执行权限预检查、生成通知/工单建议并维护待审批、已核验、已提交、执行中和完成状态。工作流不会自动向生产系统发送高风险动作。通知和工单只能通过独立业务接口显式提交。

### 6.6 反思迭代

节点读取执行回执和后续观测，按相同方法和可比窗口计算前后变化，输出完成度、风险变化、失败动作和改进建议。缺少后续观测时状态为 `wait_for_data`；缺少可信回执时不能把用户手填完成当成执行事实。知识候选默认 `pending_review`，必须人工审核后才能写回。

## 7. 工作流状态机与路由

正常路由为：

```text
perception_warning
 → knowledge_retrieval
 → dispatch_decision
 → resource_evaluation
 → collaborative_control
 → reflection_iteration
 → knowledge_write_back → END
```

条件路由包括：

- 数据预检失败：工作流不启动，API 返回字段级错误。
- 资源不足：反思节点可回到调度决策，要求调整动作或资源。
- 高风险动作：协同管控进入 `waiting_human`，由批准/拒绝/取消接口恢复。
- 现场方案未核验：禁止创建生产工单。
- 缺少后续观测：反思返回 `wait_for_data`，提交 child run 后继续复核。
- 达到最大迭代次数：强制结束并标记人工复核，防止无限循环。

节点级事件通过 WebSocket 推送，事件包含阶段、状态、摘要和运行编号；前端可以滚动查看完整轨迹。

## 8. 配置和版本管理

`MineConfiguration` 按矿井、巷道、测区隔离，包含版本、来源、有效期、统计窗口、复查周期、规则列表、资料资产 ID、资源资产 ID 和方案资产 ID。规则支持 `>=`/`<=` 及蓝黄橙红等级；版本不可变，更新必须创建新版本。启动时检查观测时间是否落在有效期内，并将命中的配置完整复制到 `actual_snapshot`。

前端配置页支持：保存版本、上传资料、解析状态查看、选择资料、配置预检和模板下载。资料状态依次为“已上传 → 已解析 → 已校验 → 已入库 → 已选入配置 → 本次已使用”。普通会话附件不会自动成为知识资料。

## 9. 资料、库存与证据生命周期

资料解析器支持资源 JSON/CSV，以及案例、规程、支护方案的 JSON、TXT、Markdown、可提取文本的 PDF 和 DOCX。系统记录 SHA-256、来源、版本、作用域、上传人和时间；解析失败不入库。资源台账必须声明人员、支护材料和设备三组，并进行非负数量及时区校验。

实际运行前冻结：任务 JSON、参数版本、资料版本及哈希、方案、库存来源与观测时间、输入文件证据。结果中的每条引用都能回到该快照。外部库存接口异常时不会伪造“充足”，而是阻止进入可执行状态。

## 10. 业务系统接入与人工门禁

业务适配器使用 `BUSINESS_BASE_URL`、服务令牌和回执令牌。外部地址要求 HTTPS，本机契约测试允许 localhost HTTP。健康契约声明身份、库存、通知、工单和回执能力。

1. 身份接口返回用户、角色、权限和矿井范围；后端每次业务操作重新校验。
2. 现场方案核验要求同矿井权限、方案/快照 ID 一致、人员资质、设备、库存和现场条件全部确认，并保存实名证据。
3. 通知是显式操作，幂等键冲突时拒绝不同内容。
4. 工单只能引用服务端已核验方案和动作，客户端不能用 `approved` 字段绕过门禁。
5. 回执必须关联请求、矿井、动作和提供方请求编号，时间不能倒序或晚于当前时间；通知送达不能伪装成动作执行。

## 11. API 契约

主要接口如下，运行后的 `/docs` 是字段最终来源：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET/POST | `/api/configuration` | 读取或保存配置版本 |
| POST | `/api/configuration/ingest` | 上传、解析和入库资料 |
| POST | `/api/configuration/prepare` | 实际任务预检并生成快照 |
| POST | `/api/workflow/start` | 启动演示或实际工作流 |
| GET | `/api/workflow/{run_id}/status` | 状态、轨迹和摘要 |
| GET | `/api/workflow/{run_id}/result` | 完整六节点结果和 provenance |
| POST | `/api/workflow/{run_id}/approve` | 恢复人工批准的中断 |
| POST | `/api/workflow/{run_id}/reject` | 拒绝并结束中断 |
| POST | `/api/workflow/{run_id}/cancel` | 取消运行 |
| POST | `/api/monitoring/runs/{run_id}/feedback` | 提交后续观测并创建复核 |
| GET | `/api/business/status` | 查看业务能力健康状态 |
| POST | `/api/business/runs/{run_id}/verify-plan` | 实名现场方案核验 |
| POST | `/api/business/runs/{run_id}/notify` | 显式发送通知 |
| POST | `/api/business/runs/{run_id}/work-order` | 显式提交工单 |
| POST | `/api/business/receipts` | 接收鉴权回执 |
| WS | `/ws/{thread_id}` | 实时节点事件 |

实际任务的 `query` 是序列化后的 JSON 字符串，不是嵌套对象。API 返回的 `run_id` 用于后续查询，不要重复启动同一编号绑定不同输入。

## 12. 前端页面结构

页面包括场景演示、实际监测输入、矿井配置、资料上传、业务接入、六 Agent 实时轨迹、六 Agent 结果、审批与后续观测。结果、轨迹、审批和对话区域均为独立滚动容器，输入框不会覆盖结果。资料和配置卡片显示解析、校验、入库、选用和本次使用状态。

## 13. 部署与运行依赖

Redis 必须先启动：

```powershell
cd <项目目录>
docker compose -f deploy/redis/docker-compose.yaml up -d
$env:REDIS_URL = "redis://:agent1_dev@localhost:6380/0"
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

前端：

```powershell
cd <项目目录>/frontend
npm.cmd install
npm.cmd run dev -- --host 0.0.0.0
```

Compose 文件和环境变量模板应提交到源码仓库；Docker Desktop、镜像缓存、Redis 数据卷、`.env` 凭据、SQLite 运行账本、`node_modules` 和 `dist` 不提交。外部业务服务、身份、库存和回执需部署符合 `docs/business-http-contract.md` 的适配器并配置真实凭据。

## 14. 安全、审计与故障处理

凭据只从后端环境读取，不进入页面、任务、日志或证据。文件按会话和矿井作用域访问；SQLite 写入使用事务；外部写操作禁止自动重试和跟随重定向。Redis 不可用时严格失败，不静默切换内存检查点。

常见故障包括：Redis 管道不存在（启动 Docker Desktop）、8000/5173 端口占用（复用或更换端口）、PowerShell 禁止脚本（使用 `npm.cmd` 和 Python 可执行文件）、422 数据契约错误（按字段错误修正）、WebSocket 断开（检查后端和代理）。运行状态 `running` 只表示任务存在；`waiting_human` 表示等待审批，不是断连。

## 15. 测试与验收标准

必须通过：

```powershell
python -m unittest discover -s tests -p "test_*.py"
cd frontend
npm.cmd run build
```

实际数据验收至少证明：

1. 上传资料后状态从解析到本次使用可追溯，知识节点引用文件哈希。
2. 同一输入和同一配置版本可复现结果；改参数版本会影响新运行，旧运行保持不变。
3. 修改资源数量会改变 `quantity_check` 和缺口，而不是固定显示充足。
4. 高能事件数量、最大能量、单位和窗口矛盾会被拒绝。
5. 现场核验前不能创建生产工单；无回执不能宣称执行完成。
6. 后续观测按可比窗口计算真实变化，缺数据时明确等待输入。
7. 每个最终字段都能追溯到输入、规则、资料、库存、业务回执或人工核验。

自动化测试验证软件契约；矿方生产验收仍需使用批准的参数、真实接口、现场数据和安全管理流程完成联调。数量满足、风险下降或流程完成均不自动等价于安全批准或复工许可。

## 16. 可维护性约定

扩展监测字段阅读 `services/monitoring_task.py`、`services/raw_monitoring.py` 和 `schemas/workflow.py`；扩展路由阅读 `agent/workflow.py`、`agent/workflow_state.py` 和 `agent/workflow_nodes.py`；扩展实际数据阅读 `agent/actual_assessment.py`、`services/mine_configuration.py`；扩展业务接入阅读 `services/business_gateway.py` 和 `api/business_routes.py`。任何新增字段都必须贯穿提交、校验、快照、节点消费、结果契约、前端展示和测试，不能只保存在浏览器状态。
