# 阶段 8 二期实施设计（图嵌入 / UIE 生产化 / MCP 生产化）

> **状态**：✅ **已确认实施（2026-08-16）**——用户确认范围=阶段 8 全部二期项；三项依赖已验证可用
> **基线**：`agent1/docs/neo4j_knowledge_graph_design.md`（子步①/③已交付，本设计补齐子步②）、`agent_design.md` §8.4（UIE）、`personal_task_progress.md`
> **对应进度日志**：`personal_task_progress.md` — 「Neo4j 子步② 图嵌入注入 / MCP 生产化 / UIE 生产化」

---

## 0. 决策记录（用户确认）

| # | 决策点 | 选择 | 理由 |
|:-:|--------|------|------|
| 1 | 二期范围 | 阶段 8 全部二期项（子步② + MCP + UIE） | 用户确认 |
| 2 | 子步② 嵌入方案 | **真嵌入模型（bge-m3）** | 语义质量高于确定性向量；Ollama 已装 bge-m3:567m（1024 维）实测可用；DeepSeek API 无 embedding 端点、RAGFlow 未运行 |
| 3 | UIE 范围 | **LLM 抽取 + 人工审核链** | 真微调缺 GPU/训练数据，DeepSeek V4 可做抽取；审核门（子步③已建）仍是唯一入库通道 |
| 4 | MCP 形态 | **独立 MCP Server（hybrid）** | 工作流内保持 LangGraph 直调，MCP 独立可发现；风险最低 |

## 1. 依赖验证结论

| 依赖 | 状态 | 证据 |
|------|------|------|
| Ollama bge-m3 | ✅ | `localhost:11434` 已启动，`bge-m3:567m` 已拉取，实测 1024 维嵌入 + 余弦 0.5912 |
| DeepSeek V4（chat） | ✅ | `OPENAI_BASE_URL=https://api.deepseek.com/v1` + `OPENAI_API_KEY` 已配（bootstrap 验证过连通） |
| mcp 包 | ✅ | `mcp 1.28.1`，`FastMCP` 可用 |
| Neo4j 容器 | ✅ | `coal-mine-neo4j` 运行中（5.26-community，原生向量索引需 5.13+） |
| Redis 容器 | ✅ | `coal-mine-redis` 运行中（6380） |
| sentence-transformers | ❌ 未装 | 不需要——走 Ollama HTTP，无需本地嵌入库 |

---

## 2. 子步② 图嵌入注入（Neo4j）

### 2.1 目标

Q1 硬编码相似度 `0.7/0.4` → bge-m3 向量余弦相似度；保持 `[0,1]` 与覆盖语义（`_compute_missing` 不变量）。

### 2.2 嵌入管线（`deploy/neo4j/scripts/embed_cases.py`）

- 读 YAML 权威源（`accident_cases.yaml`），案例文本 = `causes + actions + outcome` 拼接；
- 调 Ollama `/api/embed`（`EMBEDDING_BASE_URL`，默认 `http://localhost:11434`，模型 `bge-m3:567m`，1024 维）；
- MERGE 写 `AccidentCase.embedding` 属性（幂等：已有嵌入跳过；`--force` 重嵌）；
- 幂等建原生向量索引：
  `CREATE VECTOR INDEX accident_case_embedding IF NOT EXISTS FOR (c:AccidentCase) ON (c.embedding) OPTIONS {indexConfig: {vector.dimensions: 1024, vector.similarity_function: 'cosine'}}`；
- 与 `seed_from_yaml.py` 分离（schema+数据 与 嵌入增强 解耦，可独立重嵌）。

### 2.3 配置（`.env.example` 新增，注释风格对齐 NEO4J_URI）

```
# 图嵌入（子步②）：未配置则知识检索走 Q1 硬编码相似度（向后兼容）
EMBEDDING_PROVIDER=ollama
EMBEDDING_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=bge-m3:567m
EMBEDDING_DIM=1024
```

### 2.4 检索升级（`agent/knowledge_sources.py`）

三态双轨（在原有 YAML/Neo4j 双轨上扩展）：

| 模式 | 触发 | Q1 行为 |
|------|------|---------|
| YAML | `NEO4J_URI` 未设 | 原样（相似度 0.7/0.4） |
| Neo4j 传统 | `NEO4J_URI` 设、`EMBEDDING_PROVIDER` 未设 | Q1 原样（向后兼容，不破坏子步①不变量） |
| Neo4j 向量 | `NEO4J_URI` + `EMBEDDING_PROVIDER` 均设 | Q1' 向量路径 |

向量 Q1'（`_Q1_SIMILAR_VECTOR`）：
```
MATCH (c:AccidentCase)
CALL db.index.vector.queryNodes('accident_case_embedding', $topK, $queryEmbedding)
YIELD node AS c, score
WHERE EXISTS { MATCH (c)-[:HAS_RISK_LEVEL]->(l:RiskLevel) WHERE l.severity >= $curSeverity }
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       toFloat(score) AS similarity
ORDER BY similarity DESC
LIMIT $limit
```
- 查询文本 = 当前**致灾因子**（`factors` 首次真正使用，呼应 §2.1「收集但未使用」）；factors 为空则退化为案例文本「顶板灾变」通用查询；
- `similarity = cosine ∈ [0,1]`（bge-m3 余弦对文本非负，负值 clamp 到 0）；
- 覆盖语义保持：`_compute_missing` 依赖 `similar_count < 3` 与 `support_count == 0`，向量路径同样产生产物数 → 语义不变；
- 失败表面：Ollama 不可达 → `KnowledgeSourceError` → 节点 `status=failed`（不静默回退）。

### 2.5 验收变更（`tests/validate_neo4j_phase8.py`，10 → 12 用例）

子步② 引入向量路径后，验收**不删除**子步①不变量，而是分层保留：
- **用例 3「全序一致」保留**（传统 Neo4j 模式回归）：无嵌入配置时 Q1 走 `_neo4j_backend` 原样，仍与 YAML 结果全序一致（10 PASS 回归确认，向后兼容不变量不破坏）。
- **新增用例 11 `test_embedding_pipeline`**：嵌入幂等（二次 embed 计数不变）、向量索引存在、`AccidentCase` 均含 `embedding` 且维度=1024；
- **新增用例 12 `test_vector_similarity`**：向量模式新断言——`similar_cases` 全部 `severity >= 当前`（**结果集 ⊆ severity 过滤池**）、`similarity ∈ [0,1]`、`case_id` 集合非空且与 YAML 同池（弱化全序，保留「不静默扩大案例池」）。
- 全量验收：`validate_neo4j_phase8.py` **12 PASS / 0 FAIL**（图谱模式，含向量路径）。

---

## 3. UIE 生产化（LLM 抽取 + 人工审核链）

### 3.1 目标

真微调缺 GPU/训练数据 → 本期以 DeepSeek V4 chat 补齐 SPO 的 `object`，人工审核后经子步③ `write_knowledge_to_graph` 入库；微调接口预留（后续替换抽取后端）。

### 3.2 `tools/uie_extractor.py`（新建）

- `class EntityRelationExtractor`：构造注入 `base_url/key/model`（默认读 `OPENAI_BASE_URL/OPENAI_API_KEY/LLM_QWEN_MAX`）；
- `extract_object(candidate) -> dict`：输入 `{entity, relation}`（及可选 `evidence`），提示词限定输出 JSON `{object, object_type, confidence, evidence}`；object_type 限定 7 类枚举（复用 `_ENTITY_TYPES` 语义）；
- 失败处理：LLM 不可达/非 JSON → 抛 `UIEExtractionError`，调用方捕获后候选保持 `pending_review`（不污染审核队列）；
- 治理：抽取仅是工具，不自动写库、不自动改 `review_status`。

### 3.3 `tools/review_candidates.py`（新建，CLI 人工审核）

工作流（子步③审核门的入口端补齐）：
1. 读 `stage_candidate` 产出的 JSONL（`review_status=pending_review`，缺 object）；
2. 逐条展示 `entity/relation/evidence`；
3. 可选：`--auto-extract` 调 `EntityRelationExtractor` 预填 `object/object_type/confidence`；
4. 人工 `approve`/`reject` + 填 `reviewed_by`（必填）；approve 需 object 完整（可补填或经抽取）；
5. approved 且完整 SPO → 组 entries 调 `write_knowledge_to_graph` 入库；
6. 输出审核结果汇总（approved/rejected/skipped 计数 + 落图条数）。

### 3.4 验收

- `tests/validate_uie_phase8.py`：`EntityRelationExtractor` 结构校验（构造/异常类型）；`review_candidates` 核心逻辑（非交互部分）以注入的 JSONL 验证「approved+SPO→写回、rejected→不写回、缺审核人→拒写」；
- 端到端（图谱模式）：构造候选 → UIE 抽取补 object → 模拟审核 approved → `write_knowledge_to_graph` 落图 → 图谱可见 `review_status=approved`。

---

## 4. MCP 生产化（独立 FastMCP Server，hybrid）

### 4.1 目标

FastMCP（stdio）建独立 MCP Server，注册 `schemas/mcp_tools.py` 已冻结的 9 个工具 + 权限审批层；工作流内暂保持 LangGraph 直调（hybrid，风险最低）。

### 4.2 `mcp/server.py`（新建）

- `FastMCP("coal-mine")`；`@mcp.tool()` 逐一注册 9 工具；
- 入参/出参 Pydantic 复用 `MCP_TOOL_REGISTRY` 的 `input_schema/output_schema`（不重复定义契约）；
- handler 确定性实现（fixture/规则/DB 驱动，与现有 15 工具无同名冲突）：
  | 工具 | 实现来源 |
  |------|---------|
  | `query_sensor_history` | DB 只读（MySQL）或 fixture 模拟 |
  | `query_roof_risk_case` | `accident_cases.yaml` 过滤 |
  | `query_safety_regulation` | `regulations.yaml` 过滤 |
  | `query_available_workers` / `query_emergency_equipment` | fixture 资源台账 |
  | `query_disposal_result` | 工作流产物（fixture） |
  | `create_disposal_work_order` / `create_warning_record` | 变更类 → 需审批 |
  | `run_digital_twin_simulation` | 规则/模拟引擎 |

### 4.3 权限审批层（`mcp/` 内 `PermissionGate`）

- 读 `MCP_TOOL_REGISTRY.read_only`：只读工具直接放行；变更类工具（create_*）挂 `requires_approval=True`；
- `tools/call` 时变更工具先校验审批凭据（token/环境变量 `MCP_APPROVAL_TOKEN`），未审批返回 `needs_approval` 状态（不执行）；
- 注册与发现由 FastMCP 提供（stdio + `tools/list` + `tools/call`），可被任意 MCP 客户端发现调用。

### 4.4 验收（`tests/validate_mcp_phase8.py`）

- 9 工具可发现：`_HANDLERS` 9 handler ↔ `MCP_TOOL_REGISTRY` 一一对应（`tools/list` 含 9 个 `read_only`/`requires_approval` 元数据）；
- 只读工具直调：输入 Pydantic 校验 + 返回合法 `{status, data, error, audit}` 信封——`_mcp_result` 统一构造，`audit` 含 `{tool:"mcp", timestamp}`，`data` 为 output_schema `model_dump()`；用例 7 对全部 9 handler 合法输入做往返 Pydantic 校验；
- 变更工具：无审批 → `needs_approval`（不执行）；持 `MCP_APPROVAL_TOKEN` → 执行成功；
- 非法输入 → Pydantic `ValidationError` → `error` 信封。

---

## 5. 测试与回归

- 新增：`validate_neo4j_phase8.py` 用例 11/12、`validate_uie_phase8.py`、`validate_mcp_phase8.py`；
- 改写：`validate_neo4j_phase8.py` 用例 3（向量模式语义）；
- 全量回归（四依赖共存：Docker+Neo4j+Redis+Ollama）：
  - 图谱+嵌入模式：bootstrap + fixtures 5/5 + tools 41/41 + workflow 7 PASS/6 路径 + neo4j 12 PASS + redis 6/6 + uie + mcp；
  - YAML 模式：bootstrap + workflow 7 PASS/6 路径（无感）。

## 6. 交付物清单

| 子项 | 新建 | 修改 |
|------|------|------|
| 子步② | `deploy/neo4j/scripts/embed_cases.py` | `agent/knowledge_sources.py`、`tests/validate_neo4j_phase8.py`、`.env.example` |
| UIE | `tools/uie_extractor.py`、`tools/review_candidates.py`、`tests/validate_uie_phase8.py` | — |
| MCP | `mcp/server.py`、`mcp/permission.py`、`tests/validate_mcp_phase8.py` | `schemas/mcp_tools.py`（如需补注册元数据） |

## 7. 回滚

- 子步②：`EMBEDDING_PROVIDER` 注释掉 → 回 Q1 硬编码（子步①不变量恢复）；
- UIE：删除 `review_candidates.py`/`uie_extractor.py` 即可，写回链路（子步③）不受影响；
- MCP：`mcp/` 为独立目录，不接工作流，删除即回退。
