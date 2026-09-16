# Neo4j 灾变知识图谱集成设计（阶段 8 / 二期）

> **状态**：✅ **已实施（2026-08-10 子步① / 2026-08-11 子步③）**——子步①（KG→LLM 方向：知识检索节点 YAML 匹配替换为配置驱动双轨，NEO4J_URI 配置→Cypher 图谱，未配置→YAML 回退）与子步③（LLM→KG 方向：反思节点 knowledge_candidates 经审核门人工审核后回写 Neo4j）均已交付，通过 `validate_neo4j_phase8.py` **10/10** 验收 + 双模式全量回归全绿；子步②（图嵌入注入）留后续按 §9 推进
> **日期**：2026-08-10
> **对应进度日志**：`personal_task_progress.md` — 「Neo4j 正式集成 | ✅ 已完成（2026-08-10 检索 / 2026-08-11 写回）」；「RAG 大模型双向耦合」
> **基线**：`agent1/agent_design.md` §8.2（Neo4j）、§8.4（UIE）、§9 阶段 8

---

## 0. 决策记录

| # | 决策 | 结论 | 理由 |
|:-:|------|------|------|
| 1 | 交付范围 | **仅设计文档**（本期）；代码/部署留二期 | 进度日志标记二期低优先级；遵守「禁止扩大需求」；Redis 子项为真实交付先例，二期按同形态推进 |
| 2 | 检索接入 | **配置驱动双轨**：`NEO4J_URI` 配置→图谱检索，未配置→YAML 回退 | 与 `REDIS_URL` 同构，开发环境零成本切换；保持六 Agent I/O 不变 |
| 3 | 图谱粒度 | **最小图谱**：`AccidentCase` / `Regulation` 两类主节点 + 枚举推导关系 | 契合「无感替换 YAML」；7 实体 × 7 关系细粒度拆分留二期 UIE 抽取 |
| 4 | 摄取源 | **YAML 权威**：从 `tests/fixtures/*.yaml` 建图 | 与 `seed_from_yaml.py` 模式一致；MySQL 表不动，避免双权威 |

---

## 1. 背景与目标

六 Agent 工作流的知识检索节点（`node_knowledge_retrieval`）当前是**纯确定性函数**：运行时从
`tests/fixtures/accident_cases.yaml` + `regulations.yaml` 读取结构化案例与规程，仅按风险等级过滤后
投影为 `KnowledgeRetrievalResult`。它从不调用 RAGFlow / MySQL。

二期目标（进度日志原文）：**将知识检索节点的硬编码 YAML 匹配替换为 Neo4j Cypher 查询**，通过知识图谱
向量嵌入注入垂域知识；**输入输出不变**（`PerceptionWarningResult` → `KnowledgeRetrievalResult`），
下游调度决策节点无感。

本设计文档即二期完整实现的蓝图，覆盖三个子步：

1. **KG→LLM 检索替换**（最小图谱 + 摄取 + Cypher 替换 YAML）——无前置依赖，先行；
2. **图嵌入注入**（向量相似度/检索增强）——① 完成后追加；
3. **LLM→KG 写回**（`knowledge_candidates` 人工审核 → UIE 抽取 → 写图）——依赖 UIE（独立二期子项）。

---

## 2. 现状与关键事实（探查结论）

### 2.1 知识检索节点现状（`agent/workflow_nodes.py:143-231`）

- 输入：`perception_result`（必填）；读取 `.risk_level`、`.warning_id`、`.causal_factors[].factor`
  （`factors` 被收集但**未参与匹配**）；`state["mine_context"]` 被读取但**未使用**（阶段 7 预留的地质筛选钩子）。
- 相似案例：遍历 YAML 案例，保留 `_risk_order(c_risk) >= _risk_order(risk_level)` 的条目，上限 5；
  `similarity` **硬编码** 0.7（同级）/ 0.4（更高级）。
- 规程：**全部** 10 条追加（`document/clause/requirement/mandatory/source`），无过滤。
- 支护方案：取**前 3 个案例（按文件序）中带 `actions` 的** → `scheme_id=f"scheme-{case_id}"`。
  （实现为 `for c in cases[:3]: if c.get("actions")` —— **先切片后过滤**，切到第 3 条即止，不再向后扫。）
- 覆盖语义：`len(similar)<3` → missing「高相似度历史案例不足」；无 `support_recs` → missing「支护方案推荐为空」；
  `status=PARTIAL`、`confidence=0.65`；否则 `SUCCESS`、`confidence=0.82`。
- 证据：`Evidence(source_type="fixture_yaml", description="accident_cases.yaml + regulations.yaml")`。

`_RISK_ORDER = {NORMAL:0, BLUE:1, YELLOW:2, ORANGE:3, RED:4}`（`workflow_nodes.py:638-642`）。

### 2.2 下游契约（不可变）

- `KnowledgeRetrievalResult(CommonAgentResult)`（`schemas/workflow.py:224-230`，`extra="forbid"`）：
  `warning_id, similar_cases[], regulations[], support_recommendations[], knowledge_conflicts[], missing_topics[]`，
  继承 `agent_name/status/summary/confidence[0,1]/evidence[]/warnings[]/errors[]/started_at/finished_at/duration_ms≥0/next_action`。
- 子类型：`SimilarCase{case_id, similarity[0,1], causes[], actions[], outcome?, source(必填)}`、
  `RegulationReference{document, clause?, requirement, mandatory=True, source}`、
  `SupportRecommendation{scheme_id, scheme_name, actions[], limitations[], source}`、
  `Evidence{source_type, source_id?, description, value?}`。
- **功能消费方**：`node_dispatch_decision`（`workflow_nodes.py:239-317`）**只读**
  `knowledge.similar_cases[*].actions`，扁平为 `ref_actions`，取 `ref_actions[:3]` 作为
  `resource_requirements.materials`。其余字段（similarity/causes/outcome/regulations/conflicts/missing）不读。
- **测试消费方**：`tests/validate_phase7.py` — `missing_knowledge` 场景断言知识检索
  `confidence ≤ 0.7`；知识检索节点 `agent_complete` 事件必须**存在**（`validate_phase7.py:337-343`
  仅断言事件存在，未断言 `evidence` 字段内容——`evidence` 溯源由本设计 §6.4 显式保证）。

> ⚠️ **无感约束的本质**：相似案例列表的**顺序**直接决定 `materials`（`ref_actions[:3]`）。
> 图谱检索必须保持与 YAML 迭代一致的全序，否则下游物资推荐变化。

### 2.3 数据源现状（`tests/fixtures/`）

- `accident_cases.yaml`（顶层 key `cases`，5 条）：`case_id, location, occurred_at, risk_level, causes[], precursors[], actions[], outcome, lessons[], source`。
  风险分布：red×2（case-2023-001, case-2021-008）、orange×1（case-2022-005）、yellow×2（case-2024-003, case-2023-007）。
- `regulations.yaml`（顶层 key `regulations`，10 条）：`regulation_id, document, clause, requirement, mandatory, applicable_conditions[], source_type, source`。
  `source_type` 枚举：`safety_regulation | rock_burst | support_standard | emergency_plan`；`mandatory`：8 true / 2 false（reg-004, reg-010）。
- 这些字段中 `precursors / lessons / location / applicable_conditions / source_type` 当前**未被节点读取**，是图谱可完整保留的素材。

**实证（2026-08-10 规则引擎实跑）**：`missing_knowledge` 场景（fixture 标注 orange）实际计算风险等级为 **RED**
（最大能量 180000J 等触发），red 下相似案例 `severity>=4` 仅 2 条 → 触发覆盖语义。设计验收必须基于**实际风险**，而非 fixture 标注值。

### 2.4 LLM→KG 预留现状（写回链路已由 §8 子步③补齐）

- `tools/knowledge_candidate_tool.py` 的 `stage_candidate` 仅返回内联 JSON（不落盘）；`review_status` 恒为 `pending_review`；
  候选为 `(entity)-[relation]->?`，**缺 object**，非完整 SPO 三元组。
- 实体类型枚举（docstring）：地质构造/支护设备/顶板灾变/监测指标/处置规程/岗位角色/巷道分区；
  关系类型枚举：诱发/支护/防控/监测/调度/上报/传导。
- 治理约束（`agent1/CLAUDE.md` + 基线 §8.4）：**知识候选必须经人工审核后才能进入正式知识库**；
  反思不得自动修改知识库。写回自动化依赖 UIE 微调（独立二期子项）。
- 上述缺口与治理约束已由 §8 子步③落地：审核门 `_review_gate` + 存储层 `_validate_write_entry` 双保险，
  仅 `approved + reviewed_by` 且含完整 SPO 的候选写回；反思派生候选仍缺 object，经人工审核或 UIE 补齐后进入写回。

### 2.5 基线文档预留（`agent_design.md`）

- §8.2：第一版暂不实现「实体/关系/图谱 Schema」「Cypher 查询工具」「图谱推理」；替代为 RAGFlow + MySQL/JSON +
  知识检索 Agent 统一输出。预留：**知识输出必须包含稳定 ID；来源不绑定 RAGFlow 专有结构；工具接口按「查询条件 → 结构化知识结果」设计**。
- §9 阶段 8：**保持 Agent 输入输出不变，通过替换工具适配层完成升级**。

---

## 3. 总体架构：配置驱动双轨

```
node_knowledge_retrieval
  └─ retrieve_knowledge(risk_level, warning_id, factors, mine_context)   ← 适配器 seam（唯一入口）
       ├─ 未配置 NEO4J_URI → YAML 后端（现有匹配逻辑原样迁入）   ← 默认回退 = 现状行为
       └─ 已配置 NEO4J_URI → Neo4j 后端（driver + 3 条 Cypher）   ← 二期新增
```

### 3.1 适配器 seam

新增 `agent1/agent/knowledge_sources.py`（`from app.agent.knowledge_sources` 经 `app/__init__.py` 的 `__path__` 扩展解析到该物理文件，`agent1/app/agent/` 目录不存在）：

```python
@dataclass
class RetrievalData:
    similar_cases: list[dict]          # [{case_id, similarity, causes, actions, outcome, source}]
    regulations: list[dict]            # [{document, clause, requirement, mandatory, source}]
    support_recommendations: list[dict]# [{scheme_id, scheme_name, actions, limitations, source}]
    missing_topics: list[str]
    source_type: str                   # "fixture_yaml" | "neo4j_kg"
    evidence_description: str

def retrieve_knowledge(
    *,
    risk_level: RiskLevel,
    warning_id: str,
    factors: list[str],
    mine_context: MineContext | None,
) -> RetrievalData:
    """按 NEO4J_URI 选择后端；返回数据层结果（不含 CommonAgentResult 包装）。"""
```

设计要点（对齐 codebase-design「深模块」）：
- **接口即 seam**：两个后端（YAML / Neo4j）共享同一小接口；节点只依赖 `retrieve_knowledge`，后端可独立替换。
- **数据层返回 plain 结构**，节点统一构造 Pydantic（`KnowledgeRetrievalResult` 校验集中一处）。
- **测试面**：`retrieve_knowledge` 即为测试面，两后端同一组断言。

### 3.2 三态策略（对齐 Redis 检查点）

| NEO4J_URI | Neo4j 可达 | 行为 |
|-----------|-----------|------|
| 未配置 | — | YAML 回退（开发模式，即现状） |
| 已配置 | ✅ | Cypher 图谱检索 |
| 已配置 | ❌ | **报错不静默降级**（对齐 `REDIS_URL` 配置但连不上 → `RuntimeError` 的先例） |

> 配置即视为「明确要求使用图谱」。连接/查询失败必须显式失败（异常上报），**不得**静默回退 YAML——
> 否则会掩盖数据源切换故障，且与 Redis 先例不一致。
>
> **失败表面（两处，缺一不可）**：
> ① **编译期探测**——`NEO4J_URI` 已配置时，`create_workflow()` 内即 `RETURN 1` 探测，
> 不可达抛 `RuntimeError`（与 `get_checkpointer()` 在 `create_workflow()` 编译时校验 Redis 完全同构，
> 见 §7.3）。坏配置在**任何**场景（含 normal 不触发知识检索的路径）创建工作流时即被捕获，不静默通过。
> ② **运行期故障表面**——探测通过后容器仍可能中途宕机。按六节点「返回 error-in-state、不抛异常」约定，
> `node_knowledge_retrieval` 捕获 Neo4j 驱动异常，返回 `status="failed" + errors[]` 的合法
> `KnowledgeRetrievalResult`（`status/errors` 均为 `CommonAgentResult` 字段，满足 `extra="forbid"`），
> 工作流不中断、错误进入 state，不产生 e2e 的 30s 盲等超时（见 §7.3）。

### 3.3 无感约束

图谱模式下 `node_knowledge_retrieval` 输出必须与 YAML 模式**结构契约逐字段一致**：
相似案例数量/顺序、similarity 取值、规程全量、支护方案前三项、覆盖→置信度/状态/missing_topics 内容。
任何偏差都会改变调度决策的 `materials` 或触发 e2e 断言失败。
`warnings` 的前缀提示语为人工可读文案，允许随数据源变化（见 §6.5），`missing_topics` 追加部分不变。

---

## 4. 图 Schema（最小图谱）

### 4.1 节点与属性

| Label | 数量 | 属性（全部来自 YAML 字段，含二期未用字段） |
|-------|:---:|------|
| `AccidentCase` | 5 | `case_id`（唯一）、`location`、`occurred_at`（ISO 字符串，保持原样）、`risk_level`（字符串枚举）、`causes: list[str]`、`precursors: list[str]`、`actions: list[str]`、`outcome`、`lessons: list[str]`、`source`、`seq: int`（YAML 文件序，保序键） |
| `Regulation` | 10 | `regulation_id`（唯一）、`document`、`clause`、`requirement`、`mandatory: bool`、`applicable_conditions: list[str]`、`source_type`、`source`、`seq: int` |
| `RiskLevel` | 5 | `level`（唯一，`normal|blue|yellow|orange|red`）、`severity: int`（0-4，对齐 `_RISK_ORDER`，供排序） |
| `SourceType` | 4 | `type`（唯一，`safety_regulation|rock_burst|support_standard|emergency_plan`） |

**为什么保留 `seq`**：YAML 迭代顺序决定相似案例列表与 `materials` 的全序。图谱节点无原生文件序，
seed 显式写入 `seq`（= YAML 索引），Cypher `ORDER BY seq` 复刻原序，保证「无感」。

### 4.2 关系（仅从枚举字段确定性推导，零抽取成本）

```cypher
(:AccidentCase)-[:HAS_RISK_LEVEL]->(:RiskLevel {level})
(:Regulation)-[:CLASSIFIED_AS]->(:SourceType {type})
```

> 关系全部由 `risk_level` / `source_type` 枚举字段直接推导，无 LLM/规则抽取。价值：
> ① 风险等级维度可用图谱遍历查询；② 为二期「图谱推理」保留拓扑；③ 检索 v1 仍以属性 + `RiskLevel.severity` 过滤为主。

### 4.3 约束与索引（Neo4j 5.x DDL）

```cypher
CREATE CONSTRAINT accident_case_id IF NOT EXISTS
  FOR (c:AccidentCase) REQUIRE c.case_id IS UNIQUE;
CREATE CONSTRAINT regulation_id IF NOT EXISTS
  FOR (r:Regulation) REQUIRE r.regulation_id IS UNIQUE;
CREATE CONSTRAINT risk_level_level IF NOT EXISTS
  FOR (l:RiskLevel) REQUIRE l.level IS UNIQUE;
CREATE CONSTRAINT source_type_type IF NOT EXISTS
  FOR (s:SourceType) REQUIRE s.type IS UNIQUE;
CREATE INDEX accident_case_risk IF NOT EXISTS
  FOR (c:AccidentCase) ON (c.risk_level);
CREATE INDEX accident_case_seq IF NOT EXISTS
  FOR (c:AccidentCase) ON (c.seq);
```

### 4.4 与二期全量图谱的演进

v1 最小图谱只落 `AccidentCase/Regulation/RiskLevel/SourceType`。细粒度实体（地质构造/支护设备/顶板灾变/监测指标/
处置规程/岗位角色/巷道分区）与 7 关系类型是**二期 UIE 抽取落地后的图谱形态**——届时 `AccidentCase` 可挂接到
`地质构造`、`监测指标` 等实体节点，`Regulation` 挂接到 `处置规程` 节点，候选 SPO 作为新节点/关系并入。
本设计不预建空 label，避免过度设计。

---

## 5. 种子摄取设计（YAML 权威源）

### 5.1 `deploy/neo4j/scripts/seed_from_yaml.py`

对齐 `deploy/mysql/scripts/seed_from_yaml.py` 的路径与命名约定：

- 输入：`tests/fixtures/accident_cases.yaml` + `tests/fixtures/regulations.yaml`（唯一权威源）。
- 连接：`NEO4J_URI`（env）或 `--uri` 参数；`GraphDatabase.driver(uri, auth=(user, password))`。
- 写入策略：**全部 MERGE（幂等）**，`case_id` / `regulation_id` / `level` / `type` 为自然键；
  `SET c.seq = <yaml 索引>`；关系 `MERGE ... ON CREATE` 防止重复边。
- 脚本签名（设计）：`seed_from_yaml.py [--uri bolt://localhost:7687] [--user neo4j] [--password agent1_dev] [--reset]`。

核心逻辑伪代码：

```python
# ① 预建全部 5 个 RiskLevel（必须含 severity）+ 4 个 SourceType。
#    仅按案例数据建会漏 normal/blue（fixture 只有 red/orange/yellow），
#    且漏 severity 会导致 Q1 `WHERE l.severity >= $curSeverity` 判空 → 0 行检索全断。
_RISK_ORDER = {"normal": 0, "blue": 1, "yellow": 2, "orange": 3, "red": 4}
for level, severity in _RISK_ORDER.items():
    tx.run("MERGE (l:RiskLevel {level: $level}) SET l.severity = $severity",
           level=level, severity=severity)
for st in ["safety_regulation", "rock_burst", "support_standard", "emergency_plan"]:
    tx.run("MERGE (s:SourceType {type: $type})", type=st)

for idx, c in enumerate(cases):                     # YAML 顺序写入 seq
    tx.run("""
      MERGE (c:AccidentCase {case_id: $case_id})
      SET  c += $props, c.seq = $seq
      """, case_id=c["case_id"], props={...}, seq=idx)
    tx.run("""
      MATCH (c:AccidentCase {case_id: $case_id})
      MATCH (l:RiskLevel {level: $level})           # ① 已预建，severity 就位
      MERGE (c)-[:HAS_RISK_LEVEL]->(l)
      """, case_id=..., level=...)
    # 同构处理 Regulation + CLASSIFIED_AS（SourceType ①已预建）
```

### 5.2 幂等与重建

- 重复执行安全（MERGE 语义），`seq` 幂等覆盖。
- `--reset`：`MATCH (n) DETACH DELETE n` 清空重建，供验收测试用例 2 使用。

### 5.3 摄取校验

- 节点计数：`AccidentCase=5`、`Regulation=10`、`RiskLevel=5`、`SourceType=4`。
- 关系计数：`HAS_RISK_LEVEL=5`、`CLASSIFIED_AS=10`（合计 15）。
- 抽查：`seq` 与 YAML 索引一致；`mandatory` 布尔正确（8 true / 2 false）。

---

## 6. 检索替换设计（KG→LLM）

### 6.1 Cypher 查询（v1，等价复刻 YAML 语义）

**Q1 相似案例**（复刻 `_risk_order(c_risk) >= _risk_order(current)` + 上限 5 + 0.7/0.4）：

```cypher
MATCH (c:AccidentCase)-[:HAS_RISK_LEVEL]->(l:RiskLevel)
WHERE l.severity >= $curSeverity
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       CASE WHEN l.level = $curLevel THEN 0.7 ELSE 0.4 END AS similarity
ORDER BY c.seq
LIMIT 5
```

**Q2 规程全量**（复刻"全部追加"）：

```cypher
MATCH (r:Regulation)
RETURN r.document AS document, r.clause AS clause, r.requirement AS requirement,
       r.mandatory AS mandatory, r.source AS source
ORDER BY r.seq
```

**Q3 支护方案前 3**（复刻 YAML `for c in cases[:3]: if c.get("actions")` —— **先按 seq 取前 3、再滤
actions**。刻意与 Q1 的「全量过滤后 LIMIT」不同：先切片可保证不向后扫到第 4/5 条，杜绝方案列表漂移）：

```cypher
MATCH (c:AccidentCase)
WITH c
ORDER BY c.seq
LIMIT 3
WHERE size(c.actions) > 0
RETURN c.case_id AS case_id, c.actions AS actions, c.source AS source
```

参数：`$curSeverity`（当前风险 `_RISK_ORDER` 值）、`$curLevel`（当前风险字符串）。

Python 侧仅做零逻辑变换：Q1 行 → `SimilarCase` 字段；Q2 行 → `RegulationReference` 字段；
Q3 行 → `SupportRecommendation(scheme_id=f"scheme-{case_id}", scheme_name=f"借鉴案例 {case_id} 处置方案",
limitations=["需根据当前地质条件调整"])`；然后计算覆盖语义。

### 6.2 契约等价性逐项对照

| 行为（YAML 现状） | 图谱实现 | 等价性 |
|------|----------|:---:|
| `severity >= current` 过滤 | `WHERE l.severity >= $curSeverity` | ✅ |
| 上限 5 | `LIMIT 5` | ✅ |
| 迭代顺序（影响 `materials` 全序） | `ORDER BY c.seq`（seed 保序） | ✅ |
| similarity 同级 0.7 / 高级 0.4 | `CASE WHEN l.level = $curLevel THEN 0.7 ELSE 0.4 END` | ✅ |
| 规程全量追加 | `MATCH (r:Regulation) RETURN ... ORDER BY r.seq` | ✅ |
| 支护方案 = 前 3 个案例中带 actions 的 | Q3 `WITH c ORDER BY c.seq LIMIT 3 WHERE size(c.actions)>0`（先切片后过滤，与节点 `cases[:3]`-再滤一致） | ✅ |
| `len(similar)<3 → missing` | Python 对 Q1 结果行数判 `<3` | ✅ |
| 无 support_recs → missing | Python 对 Q3 结果判空 | ✅ |
| `status/confidence = PARTIAL/0.65` 或 `SUCCESS/0.82` | Python 覆盖语义 | ✅ |
| `warnings=["当前为 fixture 模拟数据…"]+missing` | `source_type=="neo4j_kg"` 时文案调整为「当前为 Neo4j 图谱种子数据」 | ⚠️ 见 6.5 |
| evidence `source_type="fixture_yaml"` | `source_type="neo4j_kg"` | ⚠️ 允许差异（仅测试要求 evidence 存在） |

### 6.3 相似度与覆盖语义

- v1 图谱**沿用硬编码 0.7/0.4**（刻意无感）：图谱 v1 不改变匹配语义，仅换数据源。
  「图谱推理」价值不在 v1 释放，在子步 ②（向量/拓扑相似度）与 ③（写回）释放——这是「契约优先」的刻意取舍。
- 覆盖语义（`confidence≤0.7` / `status=partial` / `missing_topics` 非空）必须保持：
  - 实证：red 当前 → `severity>=4` 恰 2 条 → missing「高相似度历史案例不足」→ `confidence=0.65`。
  - 验收测试用例 5 以此断言。

### 6.4 证据溯源

- 图谱模式：`Evidence(source_type="neo4j_kg", source_id="coal_mine_kg",
  description=f"Neo4j 知识图谱：{N} 案例、{M} 规程、{K} 方案")`。
- 满足「知识结论必须带来源」：`SimilarCase.source` / `RegulationReference.source` 取自节点 `source` 属性。
- **基线 §8.2「稳定 ID」逐项核对**：
  - `case_id` ✅ **直接暴露**——`SimilarCase.case_id` 为 frozen 契约字段。
  - `regulation_id` ⚠️ **部分满足**——图谱节点有唯一约束（§4.3）且 Q2 RETURN 可带出，
    但 frozen 契约 `RegulationReference`（`schemas/workflow.py:208-213`，`extra="forbid"`）无此字段，
    禁止新增。故 regulation_id 只能内嵌于 `Evidence.description` 供溯源，**完整暴露留到契约可演进时**。
    **本设计将此项显式记录为已知取舍**，不夸大为「已满足」。

### 6.5 与 YAML 模式的文案差异

无感约束聚焦**结构契约与下游语义**；`warnings` 提示语为人工可读文案，允许随数据源变化：
- YAML：`"当前为 fixture 模拟数据，非真实知识库"`
- 图谱：`"当前为 Neo4j 图谱种子数据（YAML 权威摄取），非真实知识库"`
两者均保留「非真实知识库」警示语义，`missing_topics` 追加逻辑不变。

---

## 7. 部署与配置

### 7.1 `deploy/neo4j/docker-compose.yaml`（二期落地）

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    container_name: coal-mine-neo4j
    ports:
      - "7687:7687"          # Bolt（驱动连接）
      - "7474:7474"          # HTTP 浏览器
    environment:
      NEO4J_AUTH: "neo4j/agent1_dev"   # 密码风格对齐 Redis（agent1_dev）
      # v1 最小图谱无需 APOC/插件；子步②图嵌入用 Neo4j 5.13+ 原生向量索引（内建）
    volumes:
      - coal_mine_neo4j_data:/data
      - coal_mine_neo4j_logs:/logs
    restart: unless-stopped

volumes:
  coal_mine_neo4j_data:
  coal_mine_neo4j_logs:
```

### 7.2 `.env.example` 新增（对齐 `REDIS_URL` 注释风格）

```dotenv
# === Neo4j 知识图谱（阶段 8 / 二期：灾变知识图谱检索）===
# docker compose -f deploy/neo4j/docker-compose.yaml up -d 启动 Neo4j（7687 Bolt / 7474 HTTP）
# 未配置 NEO4J_URI 时知识检索回退 YAML fixture（开发模式，同 REDIS_URL 未配置回退 InMemorySaver）
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=agent1_dev
```

### 7.3 依赖与健康检查

- 新增 Python 依赖 `neo4j>=5.26`（agent1 依赖清单）。
- **编译期启动探测（对齐 Redis 先例）**：`NEO4J_URI` 配置时，`create_workflow()` 内调用
  `probe_neo4j()`（`RETURN 1`），不可达抛 `RuntimeError`——与 `get_checkpointer()` 在
  `create_workflow()` 编译时校验 Redis 完全同构（`checkpoint.py:26-44`、`workflow.py:122`）。
  **任何场景（含 normal 不触发知识检索）创建工作流即捕获坏配置**，不会静默通过。
- **运行期故障表面**：启动探测通过后容器仍可能中途宕机。按六节点「返回 error-in-state、不抛异常」
  约定（`workflow_nodes.py:147/246/341/428`），`node_knowledge_retrieval` 捕获 Neo4j 驱动异常，
  返回 `KnowledgeRetrievalResult(status="failed", errors=["Neo4j 查询失败: <异常>"], ...)`——
  `status/errors` 均为 `CommonAgentResult` 合法字段，满足 `extra="forbid"` 契约；工作流不中断、
  不静默回退 YAML，错误进入 state（`workflow_runner` 记录 `record.error`、下游节点可见
  `status=failed`），e2e 不会退化为每场景 30s 盲等超时。

---

## 8. LLM→KG 写回（已实施，子步③）

### 8.1 治理约束（不可绕过）

- `agent1/CLAUDE.md`：「反思建议不得自动修改阈值/模型/提示词/知识库」「知识结论必须带来源」。
- 基线 §8.4：「所有知识候选必须经人工审核后才能进入正式知识库」「不得自动作为正式知识入库」。
- 故写回必须显式经过**人工审核门**；自动写回依赖 UIE 抽取（独立二期子项），UIE 就绪前可用人工审核后的
  手工结构化候选先走通写回链路。

### 8.2 审核门状态机

```
LLM stage_candidate ──► pending_review ──审核──► approved ──► 写回 Neo4j（UIE 抽取完整 SPO 后）
                              │
                              └──► rejected（记录拒绝原因 + 审计轨迹，永不写回）
```

- `pending_review → approved`：审核人 + 审核意见必填；仅 `approved` 且经 UIE/人工补齐 object 后触发写回。
- 候选缺 object（非完整 SPO）的问题在此门解决：UIE 抽取补齐 object 实体，或人工审核界面补填。

### 8.3 候选字段 → 图 Schema 映射（已实施）

| 候选字段 | 图谱落地 |
|---------|---------|
| `entity` | 节点（subject），v1 以通用 `KnowledgeEntity` 节点承载，`(name, entity_type)` 为自然键（唯一约束）；二期细粒度 label 由 `entity_type` 决定 |
| `entity_type` | 节点 label（7 类：地质构造/支护设备/顶板灾变/监测指标/处置规程/岗位角色/巷道分区） |
| `relation` | 关系类型（7 类：诱发/支护/防控/监测/调度/上报/传导） |
| `object`（UIE/人工补齐） | 目标节点（object） |
| `evidence` / `source` / `confidence` | 关系属性（`evidence_text` / `source` / `confidence`） |
| `review_status` | 审核门状态（approved 才可见于图谱） |

### 8.4 写回接口签名（已实施，与 `stage_candidate` 同构）

```python
def write_knowledge_to_graph(entries: list[dict]) -> dict:
    """已审核（approved）且含完整 SPO 的知识条目写入 Neo4j。

    输入条目结构：{entity, entity_type, relation, object, object_type,
                   evidence, source, confidence, reviewed_by, reviewed_at}
    返回统一 {status, data, error, audit} 信封（对齐工具层约定）。
    """
```

> **已实施于 `knowledge_sources.py`（存储层）**。写回接口的数据结构与 `stage_candidate` 同构，
> 不改变阶段 1-7 冻结的 Agent 输入输出；工作流内接入点固定为「反思节点之后、END 之前」，
> ③ 已新增钩子（见 §8.5），不改变 §6 检索路径。写回白名单：`entity_type`/`object_type` 各 7 类、
> `relation` 7 类（见 §11 术语表）；动态 Cypher 关系类型仅允许白名单枚举内插（防注入）。
> 存储层 `_validate_write_entry` 与节点审核门双重保险：非 approved / 缺审核人 / 缺 SPO / 非法枚举一律拒写。

### 8.5 写回接入点（工作流内，③ 已实施）

- 进度日志固定写回位置：**反思节点之后、工作流结束之前**，不影响上游链路。
- 原现状缺口：`node_reflection_iteration` 硬编码 `knowledge_candidates=[]`，`stage_candidate` 钩子在
  六节点工作流内尚未被调用——③ 须同时引入「反思产出 → 候选池」钩子与「END 前」写回节点，
  二者只消费 `approved` 候选（见 §8.2 状态机）。此缺口已由本子步补齐。
- ③ 实现顺序：反思节点候选进入审核门 → 人工审核（或 UIE 抽取补齐 object）→ `write_knowledge_to_graph`
  于 END 前落图。②/③ 不改变 KG→LLM 只读检索路径。
- **③ 已实施（2026-08-11）**：`node_reflection_iteration` 通过 `_derive_knowledge_candidates` 从
  `perception.causal_factors` 派生 `pending_review` 候选（忠实呈现 §2.4 无 object 缺口，不擅自虚构 SPO）；
  路由 `_route_after_reflection` 的 `finish` → `knowledge_write_back` 节点 → END；
  审核门 `_review_gate` 仅放行 `approved + reviewed_by` 的完整 SPO 候选，其余 rejected（带原因）；
  合并状态：含 rejected → `partial`，written>0 → `success`，否则跟随存储层状态。

---

## 9. 二期子步与依赖排序

| 子步 | 内容 | 前置 | 交付物 |
|:---:|------|------|--------|
| ① | KG→LLM 检索替换 | 无（仅 Neo4j 容器） | `deploy/neo4j/` + seed + Schema + 检索接入 + `validate_neo4j_phase8.py` |
| ② | 图嵌入注入 | ① 完成后 | `AccidentCase` 向量属性 + 原生向量索引；similarity 由硬编码改为向量/拓扑相似度（保持 [0,1] 与覆盖语义）；可选启用 `mine_context` 地质筛选 |
| ③ | LLM→KG 写回 | ① 完成后（UIE 就绪前以人工结构化 SPO 候选先行） | ✅ **已完成（2026-08-11）**——审核门 + `write_knowledge_to_graph` 落图（`KnowledgeEntity` 通用节点 + 7/7 枚举白名单）；UIE 自动抽取补齐 object 为二期增强项，届时仅替换候选来源 |

> ①、③ 均已交付；②（图嵌入注入）仍待推进，嵌入模型选型（如 bge-m3 / DeepSeek embedding）留到
> ② 执行时确定，不在本设计锁定。③ 的 UIE 依赖已解除：以人工审核后的手工结构化候选先走通写回链路，
> UIE 就绪后仅替换候选生成来源，写回接口与审核门不变。

---

## 10. 验收方案（`tests/validate_neo4j_phase8.py`，已实施）

10 用例，对齐 `tests/validate_redis_phase8.py` 形态（`REDIS_URL` 默认注入 → 本脚本 `NEO4J_URI` 默认注入）：

| # | 用例 | 断言 |
|:-:|------|------|
| 1 | Neo4j 容器可达 | driver 连接 + `RETURN 1`；约束/索引存在 |
| 2 | 种子摄取幂等 | seed 两次后计数不变：`AccidentCase=5`、`Regulation=10`、`RiskLevel=5`（每条含 `severity`）、`SourceType=4`、关系=15；`--reset` 后重建一致；**图谱 case_id 集合 = YAML case_id 集合**（派生副本漂移探测，见 §12） |
| 3 | 配置驱动双轨 | 设 `NEO4J_URI` → 图谱后端；未设 → YAML 后端；**两模式 `similar_cases` 与 `support_recommendations` 全序一致**（case_id 序列、similarity、actions、scheme_id 序列） |
| 4 | 检索契约不变 | 图谱模式 `KnowledgeRetrievalResult`：`similar_cases[].actions` 非空、每项 `source` 非空、`similarity∈[0,1]`、`regulations=10`、red 当前 → `len(similar)=2`；Pydantic `extra=forbid` 校验通过 |
| 5 | 覆盖语义 | `missing_knowledge` 场景图谱模式 → 知识检索 `confidence ≤ 0.7`、`status=partial`、`missing_topics` 非空 |
| 6 | 全量回归 | `bootstrap` + `validate_workflow` 7 场景，NEO4J 模式子进程全过 |
| 7 | 失败表面 | 运行期：不可达 URI → 节点 `status=failed` + `errors[]`（不静默回退 YAML）；编译期：`probe_neo4j()` 不可达 → `RuntimeError`（fail-loudly，对齐 Redis 先例） |
| 8 | 写回审核门与图谱落图 | 混合候选（approved+reviewed_by / approved 缺审核人 / pending_review / rejected）→ 仅 approved 完整 SPO 写图：`written==1`、`skipped==1`、`rejected==2`（含「证据不足」「非完整 SPO」原因）；图谱验证 WB-TEST-1 三元组含 `review_status=approved`/`reviewed_by`/`confidence=0.6`；MERGE 幂等（二次写回计数不变）；未写实体不落图；测试数据清理 |
| 9 | 反思候选钩子 | `_derive_knowledge_candidates` 对 top-2 致灾因子派生候选：全部 `pending_review`、无 object（忠实 §2.4 缺口）、entity/source 非空 |
| 10 | 双轨写回 | 图谱模式非法枚举（entity_type/relation 不在白名单）→ `rejected` 不落图；YAML 模式（不设 NEO4J_URI）→ `status=skipped`，候选保留审核队列不落图 |

运行方式：`cd agent1 && python tests/validate_neo4j_phase8.py`（前置：Neo4j 容器 + seed 已执行）。
写回用例（8-10）还需容器内 `KnowledgeEntity` 约束自动创建（首次写回时幂等 `CREATE CONSTRAINT IF NOT EXISTS`）。

---

## 11. 领域术语表

> 本项目暂无 `CONTEXT.md`。本期「仅设计文档」，术语表并入本设计；二期实施时如出现跨模块术语冲突，
> 再按治理规则同步到 `CONTEXT.md`。

| 术语 | 定义 |
|------|------|
| 灾变知识图谱（CoalMineKnowledgeGraph） | 煤矿顶板灾变领域的结构化知识存储，二期以 Neo4j 实现，长期记忆层 |
| `AccidentCase` 事故案例节点 | 一条历史顶板事故案例（v1 取自 YAML，二期可接 UIE 抽取实体） |
| `Regulation` 规程节点 | 一条安全规程/标准/预案条款（`source_type` 分类） |
| `RiskLevel` 风险等级节点 | 五值枚举（normal/blue/yellow/orange/red），携带 `severity` 排序 |
| `SourceType` 来源类型节点 | 规程来源四分类（安全规程/冲击地压/支护标准/应急预案） |
| 知识候选（KnowledgeCandidate） | 反思 Agent 生成的待审知识条目（`stage_candidate`），`review_status=pending_review` |
| SPO 三元组 | subject-实体 - relation-关系 - object-实体；完整图谱写入的最小单元 |
| 审核门 | 人工审核 `pending_review→approved/rejected` 的强制关口，approved 才可入库 |
| 实体类型（7） | 地质构造 / 支护设备 / 顶板灾变 / 监测指标 / 处置规程 / 岗位角色 / 巷道分区 |
| 关系类型（7） | 诱发 / 支护 / 防控 / 监测 / 调度 / 上报 / 传导 |
| 无感替换 | 换数据源不换结构：`PerceptionWarningResult → KnowledgeRetrievalResult` 输入输出与下游语义完全不变 |

---

## 12. 风险与约束

| 风险/约束 | 说明 | 缓解 |
|-----------|------|------|
| **无感破坏** | 图谱检索顺序/字段与 YAML 不一致 → `materials`（`ref_actions[:3]`）变化 | `seq` 保序 + 逐项等价对照（§6.2）+ 验收用例 3/4 |
| **相似度无感 = 图谱推理价值延后** | v1 沿用 0.7/0.4，不体现拓扑/向量相似度 | 刻意取舍（契约优先）；推理价值在 ②③ 释放 |
| **覆盖语义漂移** | e2e 断言 `confidence≤0.7` 依赖 red→2 相似 | 基于**实际风险**（实证 red）而非 fixture 标注设计验收 |
| **配置静默降级** | `NEO4J_URI` 配置但连不上 → 掩盖故障 | 三态策略：报错不静默（对齐 Redis 先例） |
| **来源缺失** | 安全约束「知识结论必须带来源」 | `source` 取节点属性；`evidence.source_type="neo4j_kg"` |
| **派生副本漂移** | MySQL `accident_cases` 实为同一 YAML 的**派生物**（`seed_from_yaml.py` 由 YAML 生成），三份副本（YAML / MySQL / Neo4j）间无自动交叉校验；MERGE 不删除，YAML 删案例时留陈旧节点 | 摄取以 YAML 为唯一源；验收用例 2 增加「图谱 case_id 集合 = YAML case_id 集合」漂移探测；`--reset` 处理删案例；MySQL 表本期不动（其与 YAML 的既有漂移风险记录在案，非本子项引入） |
| **写回绕过审核** | LLM 自动改知识库 | 审核门 + 治理约束 + 基线 §8.4 |
| **候选非 SPO** | `stage_candidate` 缺 object | UIE/人工补齐后写回；写回接口显式要求完整三元组 |
| **mine_context 未用** | 地质筛选钩子当前为空 | 子步②可选启用；本期保持不参与（无感） |
| **不替换 RAGFlow** | RAGFlow 检索非结构化材料保留 | 回归离线降级路径不受影响；Neo4j 只替换结构化 YAML 匹配 |
| **Neo4j 镜像体积/内存** | 容器约 1GB+，需 JVM 内存 | 社区版默认堆配置；验收前置明确 |

---

## 13. 二期实施文件清单

**新建：**

| 文件 | 职责 |
|------|------|
| `agent1/deploy/neo4j/docker-compose.yaml` | Neo4j 容器（7687/7474，密码 `agent1_dev`） |
| `agent1/deploy/neo4j/scripts/seed_from_yaml.py` | YAML 权威源 → 图（MERGE 幂等，`seq` 保序，`--reset`） |
| `agent1/deploy/neo4j/scripts/schema.cql` | 约束/索引 DDL（或内嵌于 seed 脚本，二选一） |
| `agent1/agent/knowledge_sources.py` | 适配器 seam：`retrieve_knowledge` + YAML/Neo4j 后端 |
| `agent1/tests/validate_neo4j_phase8.py` | 6 用例验收（§10） |

**修改：**

| 文件 | 改动 |
|------|------|
| `agent1/agent/workflow_nodes.py` | `node_knowledge_retrieval` 内 `_load_yaml + 匹配段` 替换为 `retrieve_knowledge(...)`（YAML 逻辑迁入后端）；捕获 Neo4j 异常 → `status="failed" + errors[]`（§7.3 运行期失败表面） |
| `agent1/agent/workflow.py` | `create_workflow()` 内：`NEO4J_URI` 配置时 `probe_neo4j()` 编译期探测（对齐 `get_checkpointer()` 先例，§7.3） |
| `agent1/.env.example` | 新增 `NEO4J_URI/USER/PASSWORD`（§7.2） |
| `agent1` 依赖清单 | 新增 `neo4j>=5.26` |
| `personal_task_progress.md` | 二期完成时「Neo4j 正式集成」由 🟢 低（二期）升级为 ✅ |
| `agent1/CLAUDE.md` | 目录结构补 `deploy/neo4j/`；开发命令补 `validate_neo4j_phase8.py` |

**不动：** `code/`（只读基座）、`schemas/workflow.py`（契约冻结）、MySQL 表、RAGFlow 链路。

---

## 14. 附录：实施顺序建议（二期动工时）

1. `deploy/neo4j/` compose 起容器 → 验收用例 1 通过。
2. seed 脚本建 Schema + 摄取 → 验收用例 2 通过（幂等）。
3. `knowledge_sources.py` 适配器 + Cypher 后端 → 验收用例 3/4 通过（双轨无感）。
4. 覆盖语义 + 全量回归 → 验收用例 5/6 通过。
5. 更新进度日志与 CLAUDE.md，子步①交付。
6. 子步②（图嵌入）、③（写回）按 §9 依赖推进。
