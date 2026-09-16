-- 阶段 7：煤矿顶板灾变系统 5 张业务表 DDL
-- 设计目标：满足六 Agent 工具层的业务数据需求（知识检索案例、人员/物资/设备核算、工单下发）
-- 全部表使用 utf8mb4 字符集，与 Python 连接配置保持一致

-- 关键：docker-entrypoint 初始化时 mysql 客户端默认会话字符集是 latin1，
-- 若不显式 SET NAMES utf8mb4，UTF-8 中文会按 latin1 误解码入库（双重编码乱码）。
SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS coal_mine_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE coal_mine_db;

-- 1. 历史顶板事故案例表（知识检索 Agent 查询来源）
CREATE TABLE IF NOT EXISTS accident_cases (
  case_id       VARCHAR(32) PRIMARY KEY COMMENT '案例编号',
  location      VARCHAR(128) NOT NULL COMMENT '事故位置',
  occurred_at   DATETIME NOT NULL COMMENT '发生时间',
  risk_level    ENUM('normal','blue','yellow','orange','red') NOT NULL COMMENT '风险等级',
  causes        JSON NOT NULL COMMENT '致灾因素（JSON 数组）',
  precursors    JSON NOT NULL COMMENT '前兆特征（JSON 数组）',
  actions       JSON NOT NULL COMMENT '处置措施（JSON 数组）',
  outcome       TEXT COMMENT '处置结果',
  lessons       JSON COMMENT '事故教训（JSON 数组）',
  source        VARCHAR(128) COMMENT '案例来源',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间'
) ENGINE=InnoDB COMMENT='历史顶板事故案例';

-- 2. 人员排班表（资源评估 Agent 人员核查）
CREATE TABLE IF NOT EXISTS personnel (
  personnel_id  INT AUTO_INCREMENT PRIMARY KEY COMMENT '人员ID',
  name          VARCHAR(32) NOT NULL COMMENT '姓名',
  role          VARCHAR(32) NOT NULL COMMENT '岗位角色（支护工程师/监测值班员/操作工等）',
  mine_id       VARCHAR(32) NOT NULL COMMENT '矿井ID',
  shift         VARCHAR(16) COMMENT '班次（早/中/晚）',
  status        VARCHAR(16) DEFAULT 'on_duty' COMMENT '状态: on_duty/rest/training'
) ENGINE=InnoDB COMMENT='人员排班';

-- 3. 物资库存表（资源评估 Agent 物资核算）
CREATE TABLE IF NOT EXISTS materials_inventory (
  material_id   INT AUTO_INCREMENT PRIMARY KEY COMMENT '物资ID',
  name          VARCHAR(64) NOT NULL COMMENT '物资名称（锚索/钢带/注浆材料等）',
  quantity      INT NOT NULL DEFAULT 0 COMMENT '库存数量',
  unit          VARCHAR(16) COMMENT '计量单位（根/卷/袋/台）',
  location      VARCHAR(64) COMMENT '存放位置',
  status        VARCHAR(16) DEFAULT 'available' COMMENT '状态: available/shortage'
) ENGINE=InnoDB COMMENT='物资库存';

-- 4. 设备状态表（资源评估 Agent 设备可用性）
CREATE TABLE IF NOT EXISTS equipment (
  equipment_id  INT AUTO_INCREMENT PRIMARY KEY COMMENT '设备ID',
  name          VARCHAR(64) NOT NULL COMMENT '设备名称（微震监测仪/顶板离层仪等）',
  equipment_type VARCHAR(32) COMMENT '设备类型（监测/支护/救援）',
  location      VARCHAR(64) COMMENT '安装位置',
  status        VARCHAR(16) DEFAULT 'normal' COMMENT '状态: normal/warning/fault'
) ENGINE=InnoDB COMMENT='设备状态';

-- 5. 处置工单表（协同管控 Agent 工单下发与执行回执）
CREATE TABLE IF NOT EXISTS work_orders (
  work_order_id   INT AUTO_INCREMENT PRIMARY KEY COMMENT '工单ID',
  workflow_run_id VARCHAR(64) NOT NULL COMMENT '工作流运行ID',
  coordination_id VARCHAR(64) COMMENT '协同管控ID',
  action          TEXT NOT NULL COMMENT '处置动作描述',
  owner_role      VARCHAR(32) COMMENT '责任人角色',
  deadline_minutes INT COMMENT '完成时限（分钟）',
  status          VARCHAR(16) DEFAULT 'pending' COMMENT '状态: pending/dispatched/completed/failed',
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB COMMENT='处置工单';

-- 6. 角色权限矩阵表（用户权限落库，权限校验工具优先从本表读取）
--    permissions 存 JSON 数组，对齐 permission_check_tool 的 6 角色权限语义
CREATE TABLE IF NOT EXISTS role_permissions (
  role          VARCHAR(32) PRIMARY KEY COMMENT '角色名（矿总工程师/安全副矿长等）',
  permissions   JSON NOT NULL COMMENT '权限列表（JSON 数组）',
  updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB COMMENT='角色权限矩阵';
