# 业务HTTP接入契约

本接口适配器已通过本地HTTP服务测试。没有矿方协议、账号和测试对象时，不代表矿方系统已接通。六节点分析不会自动发送通知或生产指令，需由业务界面显式操作。

## 后端配置

参见`.env.example`末尾的`BUSINESS_BASE_URL`、`BUSINESS_SERVICE_TOKEN`、`BUSINESS_RECEIPT_TOKEN`及`BUSINESS_TIMEOUT_SECONDS`。外部地址必须HTTPS；仅本机HTTP可用于契约测试。凭据不存入任务、运行记录或前端本地存储。修改环境配置后重启后端。

## 提供方需实现

| HTTP接口 | 鉴权 | 必需返回 |
| --- | --- | --- |
| GET `/health` | 后端服务Bearer凭据 | `capabilities`对象，分别声明identity、inventory、notifications、work_orders、receipts布尔值 |
| GET `/identity` | 操作者个人Bearer凭据 | `user_id`、`roles`数组、`permissions`数组、`mine_ids`数组、`active`布尔值 |
| GET `/inventory?mine_id=...` | 后端服务凭据 | `mine_id`、`source`、带时区的`observed_at`、`available_resources` |
| POST `/notifications` | 后端服务凭据 | 200/201/202且`status="accepted"`及非空`provider_request_id` |
| POST `/work-orders` | 后端服务凭据 | 同上；接收不等于动作执行 |

身份权限约定：`review`允许研判记录，另核验岗位必须匹配工作流的`required_role`且矿井在授权范围内；`notify`允许发送核验建议；`work_order`还必须有服务端已核验的现场方案与对应动作审批。客户端自报operator/role不用于可信授权。

库存必须包含`personnel`、`support_materials`、`equipment`三个对象，其成员为精确资源名称到非负有限可用数量的映射。不匹配矿井、缺项、未来时间或网络异常会报错。观测时间与来源随结果保留，现场时效、人员资质和设备状态尚需核验。

实际摘要、原始记录或测区配置可选择 `inventory_source="business"`。需求来自选定结构化方案的 requirements，或用户资源清单。准入时查询并冻结可用量，原始 query 保留，effective task 使用接口数字；资源节点消费该快照，不在执行时重复查询。查询失败、过期或资源缺项拒绝启动，不回退输入值或 fixture。数量单位须与配置方案一致，提供方应在适配层完成单位映射。

也支持显式`inventory_source="mysql"`。旧表无矿井字段，必须在后端声明`MYSQL_SCOPE_MINE_ID`且与任务匹配；原表无库存观测时间，因此实际任务需由台账维护者设置 `MYSQL_INVENTORY_OBSERVED_AT`，格式为带时区的真实更新时间。未设置或过期将拒绝运行，不把查询时间冒充库存时间。同名多行材料/设备累计计数。

## 提交、幂等与回执

外部提交体：`request_id`、`run_id`、`mine_id`、`recipient`、`kind`（notification/work_order）、`content`，工单另有`action_sequence`。接收方也须按`request_id`幂等，不应把网络重试当成新工单。

平台先把请求及可信用户编号保存到`output/business_ledger.sqlite3`，再发送一次HTTP请求。相同编号与相同内容只返回原记录，内容/用户冲突拒绝。超时、5xx、无法判定的响应保留`delivery_unknown`，不自动重发；明确业务拒绝为`rejected`。`accepted`只表示接收方接收。

回执由提供方POST至本平台`/api/business/receipts`，使用独立回执Bearer凭据，字段为：

```json
{
  "receipt_id": "唯一回执编号",
  "request_id": "原请求编号",
  "provider_request_id": "提供方请求编号",
  "mine_id": "原矿井编号",
  "recipient": "原接收目标",
  "kind": "notification",
  "status": "delivered",
  "occurred_at": "2026-09-12T15:00:00Z"
}
```

工单回执须增加原`action_sequence`。状态允许delivered、executed、failed；通知禁止executed。回执必须匹配矿井、对象、种类、动作、外部请求编号，时间不能倒序或在未来。重复同回执可查询，冲突重复/终态回退被拒绝。身份鉴权只证明提供方提交，仍需现场业务验收，不能自动推导风险解除。

## 平台入口

页面“业务接入状态”检查健康契约；展开后可验证个人身份、明确选择目标并发送当前已完成自定义任务的核验建议、查询回执。没有服务配置时发送按钮禁用。本次验证没有点击真实外部发送按钮。

平台API：GET `/api/business/status`、`/identity`、`/inventory`、`/requests/{id}`；POST `/runs/{run_id}/notify`、`/runs/{run_id}/work-order`、`/receipts`。上述相对路径均接在`/api/business`后。通知正文由已归档的原任务建议生成，不能从请求伪造任意生产指令。

## 实名现场方案核验

实际数据配置选择含 actions、requirements 的支护方案，六节点完成且 quantity_check=sufficient 后，可 POST `/api/business/runs/{run_id}/verify-plan`，使用个人 Bearer 凭据。

请求字段：plan_asset_id、snapshot_id、evidence_reference（至少 5 字符的现场核验记录编号/来源），以及 personnel_qualified、equipment_ready、inventory_confirmed、site_conditions_confirmed 四项布尔声明。声明必须全部成立。服务端从已归档输入验证方案和快照；身份提供方必须授予本矿 review 和 work_order 权限。审核人不是客户端自报姓名。

审批保存在运行归档的 field_approvals 表，不覆盖分析结果。同一运行审批不允许改写审批人或依据。现场条件变化需新运行、新审批。提交工单时还会再次核验身份、矿井、批准人及动作序号；不能传 approved=true 绕过。

GET `/api/business/runs/{run_id}` 返回当前方案、输入快照编号、数量状态、独立审批与本地最新业务回执，需本矿个人凭据。页面可在此完成核验、指定动作派工与查询。

回执进入新观测复核：反思节点查询原任务的工单账本，只统计鉴权 executed 且发生时间不晚于新观测的动作。全部动作完成且观测口径一致时结束本次复核；不自动认定风险解除。

外部供应商须按此契约实现身份、库存、通知与工单字段映射。代码的本机契约测试证明适配流程工作，不冒充具体矿方真实端点联调。

已实现的持久化用于单后端工作进程和本机SQLite。多实例分布式派工需使用共享业务账本、共同幂等协议另行验收。运行中审批恢复仍受原检查点后端能力限制，终态查询与反馈独立持久化。

验证命令：`python -m unittest discover -s tests -p test_business_gateway.py`。专项测试包含真实本机HTTP往返、六节点资源读取、可信审批、显式通知、失败/超时、幂等及回执；不使用真实人员或设备作为测试目标。
