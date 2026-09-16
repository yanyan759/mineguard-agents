# 软件缺口补齐实施记录

用户已确认设计：`docs/superpowers/specs/2026-09-12-software-gap-closure.md`。当前会话连续实施，无需重复确认。仅修改agent1/；工作区无Git仓库。

## 任务与文件

- [x] 数据计算：`services/raw_monitoring.py`定义RawMonitoringTask和严格事件校验、UTC窗口、单位换算、b值方法声明、方法适用性；服务端`config/assessment_profiles/`固定参数版本。`tests/test_raw_monitoring.py`先验证未来事件、重复ID、单位冲突、缺失、不适用和独立手算值，再实现。
- [x] 六节点：`agent/raw_assessment.py`及既有workflow_nodes/state/runner接入原始任务；模型允许未评估风险，未知不走正常结束分支。节点开始事件在真实调用前产生，任务ID避免重复驱动。
- [x] 上传与页面：`api/monitoring_routes.py`做会话内输入文件选择、验证及真实样本入口；`frontend/src/components/MonitoringWorkbench.tsx`提供JSON/CSV元数据、选择、启动与结果依据。复用原有工作流会话，不创建第二套聊天状态。
- [x] 反馈：`services/workflow_feedback.py`保存不可覆盖的反馈与父子运行关系；API绑定已有结果与动作、限制时序/范围/方法、请求幂等；页面补交记录并启动复核，反思比较真实指标，不能归因或自动解除风险。
- [x] 业务：`services/business_gateway.py`接入明确契约的HTTP身份/资源/通知/回执，配置缺失真实返回；超时无自动重复写、持久请求标识去重、回执不越级。已有MySQL读取作为可查询来源而非假库存兜底。真实外部系统未提供时仅本地HTTP契约测试，不实际对外通知。
- [x] 回归：专项测试、自定义输入和既有七场景、前端构建；浏览器实际提交原始JSON/CSV、缺项/错单位、研究样本、后续观测与滚动。报告各项已实现/已测试/外部待联调状态。

固定决策：统计方法不输出五色等级；研究阈值只输出研究报警。原始数据缺b值/缺失率可进入计算，未知保留null。CSV元数据必须独立明确；上传使用会话内文件名，不接受跨会话或任意磁盘路径。研究参数不自动转为现场有效。

验收命令：`python -m unittest discover -s tests -p test_raw_monitoring.py`；补充反馈/HTTP契约测试后执行对应文件；`python tests/validate_workflow.py`；`npm.cmd run build`。实际测试输出在交付时记录。

验收已完成：25项输入/上传测试、6项反馈、9项证据、9项本机业务契约，七场景和六条路径、38项API检查、前端构建及真实浏览器上传/复核/重连/滚动均通过。记录见`../../software-gap-verification-2026-09-12.md`。真实矿方协议映射、现场批准方案与现场效度仍未具备，不归入本地测试通过。
