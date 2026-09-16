# 实际数据接入：代码与页面验收记录

日期：2026-09-16。仅修改 agent1；未向真实矿方发送通知或生产工单。

## 通过的检查

- Python unittest：69 项通过。包括配置、解析、六节点、数据真实性边界、反馈与本机 HTTP 业务闭环。
- 前端 TypeScript / Vite 生产构建通过。构建仍有既存的大依赖包体积提示，不影响通过。
- PDF 正文、Word 段落和表格解析通过；损坏 PDF 被拒绝。
- 无效资源、跨矿资料、过期参数/库存、单位冲突及版本冲突被拒绝。
- Redis 实际检查点为 CoalMineRedisSaver；将单独验收进程指向不可用 Redis 后明确阻断，未回退内存。
- 本机 HTTP 提供方：可信身份 → 方案核验 → 工单 → 鉴权回执 → 后续观测，全部成功；重复工单仅发送一次，错误凭据与快照被拒绝。
- 全部原方案动作收到 executed 回执且观测可比时，反思完成率为 1，最大能量真实差值为 -4000 J，复核结束。

## 浏览器实际操作

使用本项目页面按用户路径分类上传 inventory、case、regulation、support 四份验收资料，保存配置，然后上传 task.json 点击运行。以下任务均有完整六节点和数量核算证据：

| 运行编号 | 输入 | 配置 | 等级 | 资源数量 |
| --- | --- | --- | --- | --- |
| wf-b7b7a8a0-b415-449e-bfd2-a4ecddaedcce | raw_events | v1 | yellow | sufficient |
| wf-6b03093f-a32c-4bb0-814e-cc8b9a45729e | raw_events | v2 | normal | sufficient |
| wf-c4812c2a-2fea-40e8-a3b4-2fa97f0cf82f | summary | v1 | yellow | sufficient |
| wf-3810fd50-48c9-42e5-8cb2-41254d5051db | summary | v1 | yellow | sufficient |
| wf-f4f10c2ec866 | summary（后续复核） | v1 | normal | sufficient |

- 同一原始输入在阈值 5000 J 的 v1 为 yellow；改为 7000 J 的 v2 为 normal，仍执行六节点。
- 中文摘要明确指定 v1 后使用旧版分级，未被最新 v2 覆盖。
- 摘要从页面提交后续 JSON，成功形成父子复核运行，比较真实指标。
- 四份资料页面显示“已用于 1 次运行”，后续可刷新次数。
- 黄色演示卡片也已实际点击运行，六节点完成；演示与实际数据路径独立。
- 结果区和实时轨迹区均为 overflow:auto；实测结果区内容高度超过容器仍可滚动。修复侧栏溢出后文档高度等于视口高度，main 顶部为 0，不再出现整页第二条滚动导致下部裁切。

## 修复范围

服务端配置与文件解析、不可变输入快照、四个实际计算/资料节点、六节点路由、实名方案核验、工单读取与提交、回执纳入反思、摘要与原始记录反馈、前端配置/追溯/业务操作、布局与说明同步。

## 复现与运行

按 [实际数据配置指南](actual-data-guide.md) 和根 README 启动并操作。测试输入位于 tests/fixtures/actual_ingestion，仅用于软件验收，不是现场参数。完整运行 JSON 已保存在本机 output/validation/actual-data（不提交 Git）。

当前启动地址：http://localhost:5173/；后端 http://localhost:8000/docs。Redis 容器 coal-mine-redis 使用 6380 端口。

本次没有矿方真实端点和授权测试对象，业务往返使用本机契约服务验证，不能据此宣称特定矿方生产环境已联调。真实上线按契约配置适配服务和凭据，并使用有效矿井资料进行验收。规则结果可追溯，不伪造概率置信度、现场库存或处置效果。
