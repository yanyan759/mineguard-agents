# 实际数据配置、运行与验收

本指南对应 `scenario_id=custom`。七个场景卡片属于演示路径；本路径不读取演示阈值、模拟库存或模拟风险下降。软件根据提交的记录和已选择配置计算，数据真实性、参数适用性与现场批准分别保留来源。

## 1. 启动依赖

先按根 README 安装 Python 依赖、前端依赖并启动 Docker Desktop。运行 `docker compose -f deploy/redis/docker-compose.yaml up -d`，将 `.env.example` 复制为 `.env`。保留 `REDIS_URL`，启动 `python -m uvicorn api.server:app --host 127.0.0.1 --port 8000`，前端运行 `npm.cmd run dev`。配置了 Redis 但无法连接时，工作流报错，不切换内存。

Docker 镜像由 Compose 下载，不需要把 Docker Desktop 或镜像文件放入源码。需要保留 Compose、依赖清单、环境变量模板和前端 lockfile；不要提交真实 `.env`、业务凭据或矿井资料库。

实际配置与资料默认存于 `output/mine_configuration.sqlite3`。可用环境变量 `MINE_CONFIGURATION_DB` 指向持久磁盘，部署容器时需挂载此目录。归档在 `output/workflow_archive.sqlite3`，工单账本在 `output/business_ledger.sqlite3`。备份这三份数据库及 Redis 数据卷，使用一个后端工作进程。

## 2. 标识、时间和版本

矿井 `mine_id`、巷道 `roadway_id`、测区 `area` 三项共同定位配置。不要把工作面名称和巷道编号混用。编号可用字母、数字、汉字、下划线、短横线。测区必须与输入完全一致。

所有配置与资料时间使用 ISO 8601，包含 `Z` 或 `+08:00`。配置有效期必须覆盖**整个监测窗口**，统计窗口分钟数必须相同。JSON 摘要建议提供 `window_end`；文字摘要可在末尾填写“配置版本：v1；观测截止：2026-09-16T01:00:00Z；数据来源：本矿监测系统。”。完整首行支持“矿井编号 / 巷道编号 / 任意测区名称：”。纯文字摘要未提供时间时使用服务端提交时间，并在证据中标为 `submission_time`，不能视为设备采样时间。

同测区同版本只允许相同内容重复保存。改规则或资料时保存新版本，例如 `v2`。默认读取最新保存版本，任务可用 `configuration_version` 指定已存版本。旧运行的输入、资料、库存、规则和结果独立冻结，不因新版本改变。

## 3. 台账格式

在配置框选“资源台账”，填写来源、版本和库存观测时间，再上传 JSON 或 CSV。

JSON 顶层为 `personnel`、`support_materials`、`equipment`，每项包括：

| 字段 | 约束及意义 |
| --- | --- |
| resource_id | 非空资源编号，同一类不得重复 |
| name | 与方案和库存接口使用的名称完全一致 |
| available | 核实的可用数量，有限且不小于零 |
| required | 未选结构化方案时，本次明确需求；选方案后按方案需求覆盖 |
| unit | 单位必须与方案相同；不自动把箱换算为根 |
| status | 可选记录，默认 available；设备可用性仍须现场核验 |

CSV 必须使用准确表头 `group,resource_id,name,available,required,unit`，group 使用上面的三个英文分类。一个配置只能选一份台账，防止重复叠加。空台账、负值、NaN、重复名称和格式错误拒绝入库。

数量优先规则：

1. `inventory_source=input`：任务自带 resources 优先；没有时读取选中的台账。台账时间不得晚于观测窗口结束，且不能超出配置的 `max_inventory_age_hours`。
2. 选中结构化方案：`requirements` 是本次需求全集，覆盖台账中的 required；每项必须在台账或接口中找到相同名称和单位。未返回可用量不能当作零库存。
3. `inventory_source=business/mysql`：在任务准入时查询对应接口，可用量覆盖用户填写值。需求从结构化方案或任务资源清单获得。查询失败、缺项、未来时间或库存过期会拒绝启动，不回退演示或浏览器数字。
4. 实际计算只冻结读取一次的库存；原始 query 与有效 task 都可追溯。用户核验过现场条件后，才能独立批准工单。

## 4. 案例、规程、支护方案

支持 JSON、UTF-8 TXT/Markdown、可提取文本的 PDF、DOCX，每份不超过 20 MB，提取正文不超过 500000 字符。PDF 扫描件需先 OCR 并人工校对，旧 `.doc`、图片及未定义 Excel/YAML 不会假报入库。资源数量请整理成资源模板。

知识 JSON 字段为 `text`（正文）、`terms`（可选检索词）、`actions`（只允许用于支护方案）、`requirements`。普通文件提取后的正文进入知识节点；不会自动猜测施工数量、工序或审批权限。

配置的三个标识明确限定资料范围。知识节点引用配置中选定的案例、规程和方案，显示原文和文件来源；关键词只解释匹配，不生成未经校准的“高相似度”。用户上传规程按用户资料标记，不自动冒充已经在线认证的正式法规。

结构化方案 JSON 的形状如下；尖括号占位内容及 null 需换成真实资料：

```json
{
  "text": "<本矿本测区方案正文及适用条件>",
  "terms": ["<相关指标或部位>"],
  "actions": [
    {
      "sequence": 1,
      "action": "<方案批准的具体动作>",
      "owner_role": "<责任岗位>",
      "deadline_minutes": null,
      "requires_approval": true,
      "acceptance_criteria": ["<验收依据>"]
    }
  ],
  "requirements": [
    {"group": "equipment", "name": "<与台账相同的名称>", "required": null, "unit": "<单位>"}
  ]
}
```

`deadline_minutes=null` 表示该资料未规定时限，系统不编造。`requirements.required` 必须填正数。动作序号和同类资源名称不能重复。上传成功后勾选资料，再在“选定适用方案”中明确选中它，最后保存配置。

页面“已上传、已校验、已入库”表示解析流程完成；“已用于 N 次运行”来自归档运行实际引用的证据编号。重新读取配置可以刷新。仅选择上传但未保存、被本次 resources 覆盖的台账，都不能误称本次已消费。

## 5. 分级配置

配置包含来源、版本、有效期、统计窗口、所选资料、库存来源、库存时效、复查周期、批准依据编号和 rules。

每条规则为 `{metric, operator, threshold, level}`：operator 为 `>=` 或 `<=`，level 为 blue/yellow/orange/red。命中多条取最高等级，无规则命中为 normal。空规则表示只统计，不定级。原始数据的分级仍依据矿方填写的规则，而非默认演示常数。

| metric | 实际含义 / 来源 |
| --- | --- |
| event_count | 逐条记录计数或摘要声明数量 |
| max_energy_j | J 单位最大能量；kJ 明确乘 1000 |
| frequency_per_hour | 原始记录数除窗口小时；摘要采用末次频次 |
| b_value | 摘要末次 b 值；原始记录需明确 energy_mle 方法和截断能量、至少 50 个样本 |
| missing_rate | 设备漏采/缺失比例，需要用户记录，不从事件多少推测 |
| frequency_increase | 摘要末次频次减起始频次 |
| b_drop | 摘要起始 b 值减末次 b 值 |
| high_energy_count | 摘要按自身标记口径提供的事件数量；不偷偷套用 50000 J |
| spatial_cluster_fraction | 摘要提供的空间集中比例，0～1 |

原始数据暂未实现历史窗口频次差、b 值下降量、高能标记和空间聚类的上述摘要口径；如果配置引用这些指标而原始输入无法给出，准入返回明确错误，不能把缺值当零。选择本次可计算指标，或通过完整监测摘要提交已核验指标。

规则计算有确定性证据，不输出概率风险评分或模型置信度。若需要模型概率，应使用经过验证的模型输出与校准报告；不能由表单任意百分比代替。复查时限来自配置或上传方案，未填写不会套用演示时限。

## 6. 提交和审计

所有实际任务经 `POST /api/workflow/start`，`scenario_id=custom`，query 为完整监测文本或序列化 JSON。JSON/CSV 文件先通过原始记录框上传校验，附上文件名进入同一任务。

`/api/configuration/prepare` 是公共预检入口；不启动、不发送通知。响应包含有效 task、assessment、snapshot。正式启动再次核验原始文件与反馈关联，并冻结本次快照。六节点不在执行途中读取另一版配置。

结果 `provenance` 包含 snapshot_id、配置版本/revision、输入、资料原始 SHA256、来源、有效期及库存证据；每个节点的 evidence 指向使用资料。前端“本次输入、配置版本与资料追溯”能查看这些内容。持久化旧结果不会随之后审批或配置修改被覆盖。

## 7. 从建议进入实际业务闭环

部署者按 [业务 HTTP 契约](business-http-contract.md) 配置接口地址、服务凭据和回执凭据。个人令牌由身份系统签发，仅保存在页面内存。不同供应商接口须实现契约适配，无法仅凭 URL 自动适配私有字段。

1. 六节点完成后，验证个人身份，并在业务框读取本次方案与执行状态。
2. 方案必须来自结构化上传且资源数量满足。填写现场核验依据，明确核验人员资质、设备状态、现场库存、适用性与现场条件。
3. 服务端检查个人凭据具备本矿 `review` 和 `work_order` 权限，快照和方案必须匹配。审批单独持久化，客户端 `approved=true` 无效。
4. 选择具体动作及业务对象，点击提交工单。后端从已归档方案生成正文；不能由请求伪造生产指令。相同请求编号不重复发送。
5. 业务提供方收到工单只表示 accepted；必须回调鉴权 `/api/business/receipts`，才显示 executed / delivered / failed。
6. 提交与原任务相同格式的原始记录或摘要 JSON，保持同矿、同测区、同来源、同窗口长度；摘要必须包含 window_end。提交这些后续观测，系统关联原运行、继承配置版本。原方案所有动作有 executed 回执且时间不晚于新观测，才能统计为全部执行完成。
7. 反思节点输出真实指标差值、执行完成率与回执。前后口径不同不会计算可比效果；指标变化不自动证明处置因果关系，不自动解除风险或批准复产。

初始分析还未执行措施，因此不能提前有处置后信息。完成上述业务与反馈步骤后，系统会消费新信息更新**关联复核运行**；原任务保持历史状态。

## 8. 可复现验收

在项目根目录运行：

```powershell
python -m unittest discover -s tests -p test_actual_pipeline.py
python -m unittest discover -s tests -p test_actual_business.py
python -m unittest discover -s tests -p test_*.py
cd frontend
npm.cmd run build
```

前两项分别验证配置/上传/六节点，以及本机真实 HTTP 往返：配置 → 六 Agent → 身份 → 工单 → 回执 → 后续观测。契约服务与验收资料只属于测试，不能称为已连接某个矿井的生产服务。

手动页面验收使用 `tests/fixtures/actual_ingestion/` 中清楚标记的测试文件，按本指南分别上传 inventory、case、regulation、support，再上传 task。测区为 ACCEPT-UI-0916 / RDW-ACCEPT / 浏览器验收测区，窗口 60 分钟，配置有效期需覆盖任务。仅用于测试的规则设置最大能量 ≥5000 J 为 yellow，复查周期 25 分钟。

应得到：60 条记录，最大 6000 J，总能量 183000 J，频次 60 次/h，资料三类均引用，方案动作与文件一致，资源数量满足。改阈值为 7000 并存新版本会变 normal 且仍执行六节点；改巡检仪可用量为 0 并上传新台账、保存新版本会出现真实缺口。旧版本结果不改变。

完成实际部署验收时，将测试资料替换为矿井原始资料、适用参数和已获授权的业务测试对象，并核对供应商回执记录。文件格式合规不代表内容自动真实，所有结论均可回溯到其输入来源。
