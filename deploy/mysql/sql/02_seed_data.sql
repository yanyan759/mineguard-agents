-- 阶段 7：MySQL 种子数据（由 deploy/mysql/scripts/seed_from_yaml.py 生成）
-- ⚠️ 事故案例为仿真构造（同 fixtures/accident_cases.yaml）；人员/物资/设备为模拟排班数据
-- 关键：初始化时 mysql 客户端默认会话字符集是 latin1，必须 SET NAMES utf8mb4
-- 否则 UTF-8 中文会按 latin1 误解码入库（双重编码乱码）。
SET NAMES utf8mb4;
USE coal_mine_db;


-- 1. 事故案例（从 fixture YAML 派生，5 条）

INSERT IGNORE INTO accident_cases
  (case_id, location, occurred_at, risk_level, causes, precursors,   actions, outcome, lessons, source)
VALUES ('case-2023-001', 'XX煤矿西一采区1203工作面', '2023-03-15 14:30:00', 'red', '["顶板砂岩含水层疏干导致岩层强度降低", "超前支承压力叠加构造应力", "支护强度不足"]', '["微震事件频次从 5 次/小时突增至 18 次/小时", "b 值从 1.3 持续下降至 0.6", "支架工作阻力超出额定值 15%", "顶板离层仪位移量单日增 30mm"]', '["立即撤出危险区域人员", "加密支护（锚索 + 钢带）", "顶板注浆加固", "调整推进速度从 4m/d 降至 2m/d"]', '支护加固后风险解除，无人员伤亡。事后复盘确认注浆区域覆盖不足是主因。', '["b 值下降是重要前兆指标，应提前 20 分钟预警", "含水层区域应预先注浆，不可仅依赖被动支护", "支架阻力超限时不能简单卸载，需综合诊断顶板状态"]', '煤矿顶板事故案例汇编');

INSERT IGNORE INTO accident_cases
  (case_id, location, occurred_at, risk_level, causes, precursors,   actions, outcome, lessons, source)
VALUES ('case-2022-005', 'YY煤矿北二采区2205工作面', '2022-08-20 09:15:00', 'orange', '["邻近断层活化", "微震活动沿断层面集中分布", "未及时加密支护"]', '["微震事件沿断层走向呈条带分布", "事件能量逐次递增（5000→15000→45000→80000J）", "断层附近钻孔应力计读数异常升高"]', '["工作面停产", "补打锚索加密支护", "施工卸压钻孔"]', '停产 3 天后恢复，未发生冒顶。卸压钻孔有效释放了断层附近的集中应力。', '["微震事件沿构造呈条带状分布是断层活化的重要特征", "能量逐次递增模式应触发快速响应，不应等待完整预测窗口", "卸压钻孔在断层活化情况下比加密支护更有效"]', '冲击地压典型事故案例选编');

INSERT IGNORE INTO accident_cases
  (case_id, location, occurred_at, risk_level, causes, precursors,   actions, outcome, lessons, source)
VALUES ('case-2024-003', 'ZZ煤矿东三采区3301工作面', '2024-01-10 22:45:00', 'yellow', '["采空区悬顶面积过大", "周期来压与地质构造叠加"]', '["液压支架阻力周期性升高但峰值稳定", "微震事件频次小幅升高（3→8 次/小时）", "b 值维持在 1.1-1.2 正常范围"]', '["加强矿压观测频次", "调整放顶煤工艺参数", "提前准备备用支护材料"]', '顺利通过周期来压，未发生事故。属于常规操作，方案有效。', '["支架阻力周期性波动是正常的来压特征，需与异常升高区分", "b 值稳定是重要安全信号，可降低不必要的应急响应", "提前准备备用材料和平稳过渡是低成本有效处置"]', '煤矿矿压观测年报');

INSERT IGNORE INTO accident_cases
  (case_id, location, occurred_at, risk_level, causes, precursors,   actions, outcome, lessons, source)
VALUES ('case-2021-008', 'WW煤矿西翼采区1502工作面', '2021-11-05 03:20:00', 'red', '["喀斯特溶洞塌陷引发连锁微震", "顶板岩层整体失稳", "前期微震信号被误判为正常矿压"]', '["微震源深度突然从 -350m 跳变到 -280m（溶洞层位）", "b 值从 1.4 急降 0.25（1 小时内）", "事件频次跳跃式增加（3→15→30 次/小时）", "多台支架同时报警压力异常"]', '["紧急全矿撤人", "启动矿级应急预案", "地表钻孔探查溶洞发育范围"]', '人员成功撤离，但工作面部分坍塌，停产 45 天。事后分析为喀斯特溶洞顶板突然塌陷。', '["震源深度突变是喀斯特地区特有的高危前兆", "b 值急降（>0.2/h）应直接触发最高级别告警", "多传感器同时异常时不应等待人工研判，应自动启动撤人流程"]', '煤矿重大险情案例汇编');

INSERT IGNORE INTO accident_cases
  (case_id, location, occurred_at, risk_level, causes, precursors,   actions, outcome, lessons, source)
VALUES ('case-2023-007', 'VV煤矿中采区1608工作面', '2023-06-22 16:00:00', 'yellow', '["巷道掘进面揭露小断层", "局部应力集中"]', '["钻孔应力计局部升高", "微震事件集中在掘进面附近（<10m）", "能量较小（均<5000J）"]', '["暂停掘进 4 小时观察", "补打锚杆 20 根", "探测断层产状和延伸方向"]', '确认断层规模较小，加密支护后恢复正常掘进。', '["掘进面附近微震集中是局部应力释放，不等于大范围失稳", "小断层探测应列入掘进标准流程"]', '巷道掘进安全经验汇编');

-- 2. 人员排班（7 行）

INSERT IGNORE INTO personnel (name, role, mine_id, shift, status)
VALUES
  ('王工', '支护工程师', 'MINE-001', '早班', 'on_duty'),
  ('李师傅', '支护工程师', 'MINE-001', '中班', 'on_duty'),
  ('张值班', '监测值班员', 'MINE-001', '早班', 'on_duty'),
  ('赵调度', '调度室主任', 'MINE-001', '常白班', 'on_duty'),
  ('钱安全', '安全副矿长', 'MINE-001', '常白班', 'on_duty'),
  ('孙矿总', '矿总工程师', 'MINE-001', '常白班', 'on_duty'),
  ('周操作', '操作工', 'MINE-001', '晚班', 'on_duty');

-- 3. 物资库存（8 行）

INSERT IGNORE INTO materials_inventory (name, quantity, unit, location, status)
VALUES
  ('锚索', '120', '根', '西翼库房', 'available'),
  ('钢带', '80', '卷', '西翼库房', 'available'),
  ('锚杆', '200', '根', '西翼库房', 'available'),
  ('注浆材料', '300', '袋', '东翼库房', 'available'),
  ('卸压钻机', '2', '台', '机电车间', 'available'),
  ('备用支护材料', '50', '套', '材料棚', 'available'),
  ('单体液压支柱', '40', '根', '东翼库房', 'available'),
  ('木垛料', '100', '根', '材料棚', 'shortage');

-- 4. 设备状态（6 行）

INSERT IGNORE INTO equipment (name, equipment_type, location, status)
VALUES
  ('微震监测仪', '监测', '1203工作面', 'normal'),
  ('顶板离层仪', '监测', '1203工作面', 'normal'),
  ('钻孔应力计', '监测', '1203运输巷', 'normal'),
  ('液压支架监测系统', '监测', '1203工作面', 'warning'),
  ('锚索张拉机', '支护', '机电车间', 'normal'),
  ('井下应急广播', '救援', '西一采区', 'normal');

-- 5. 处置工单（空表，由协同管控 Agent 运行产生）

-- （无种子数据，工单随工作流执行写入）

-- 6. 角色权限（7 行，config/permission_matrix.py 权威源，逐条带条款出处）

INSERT IGNORE INTO role_permissions (role, permissions)
VALUES
  ('矿长', '["approve_plan", "initiate_emergency", "report_status"]'),
  ('矿总工程师', '["approve_plan"]'),
  ('安全副矿长', '["escalate_alert", "initiate_emergency"]'),
  ('调度室主任', '["initiate_emergency", "dispatch_work_order"]'),
  ('支护工程师', '["execute_plan", "report_status"]'),
  ('监测值班员', '["view_warning", "acknowledge_alert"]'),
  ('操作工', '["report_status", "view_warning"]');
