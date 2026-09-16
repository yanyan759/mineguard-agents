// ═══════════════════════════════════════════════════════════════════════
// 基础连接状态
// ═══════════════════════════════════════════════════════════════════════

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "closed";

// ═══════════════════════════════════════════════════════════════════════
// 煤矿顶板灾变六 Agent 领域类型
// ═══════════════════════════════════════════════════════════════════════

/** 风险等级（与后端 schemas/workflow.py RiskLevel 对齐） */
export type RiskLevel = "normal" | "blue" | "yellow" | "orange" | "red";

export interface RiskLevelMeta {
  label: string;
  /** antd Badge 状态色 */
  color: string;
  /** 十六进制主色（图表/节点发光） */
  hex: string;
}

export const RISK_LEVEL_META: Record<RiskLevel, RiskLevelMeta> = {
  normal: { label: "正常", color: "default", hex: "#8f9aa7" },
  blue: { label: "蓝色预警", color: "blue", hex: "#3b82f6" },
  yellow: { label: "黄色预警", color: "gold", hex: "#facc15" },
  orange: { label: "橙色预警", color: "orange", hex: "#fb923c" },
  red: { label: "红色预警", color: "red", hex: "#ff4d4f" }
};

/** 六 Agent 节点名（与后端 workflow 节点名对齐） */
export type AgentNodeName =
  | "perception_warning"
  | "knowledge_retrieval"
  | "dispatch_decision"
  | "resource_evaluation"
  | "collaborative_control"
  | "reflection_iteration";

export const AGENT_NODE_META: Record<AgentNodeName, { name: string; short: string; color: string }> = {
  perception_warning: { name: "感知预警 Agent", short: "感知预警", color: "#ff5c7a" },
  knowledge_retrieval: { name: "知识检索 Agent", short: "知识检索", color: "#7c8cff" },
  dispatch_decision: { name: "调度决策 Agent", short: "调度决策", color: "#20d6ff" },
  resource_evaluation: { name: "资源评估 Agent", short: "资源评估", color: "#5dff9f" },
  collaborative_control: { name: "协同管控 Agent", short: "协同管控", color: "#ffc857" },
  reflection_iteration: { name: "反思迭代 Agent", short: "反思迭代", color: "#c084fc" }
};

/** Agent 执行状态（与后端 AgentStatus 对齐） */
export type AgentStatus = "success" | "partial" | "failed" | "waiting_human";

/** 工作流整体状态（与后端 workflow_runner.WorkflowRunRecord.status 对齐） */
export type WorkflowStatus =
  | "running"
  | "waiting_human"
  | "approved"
  | "rejected"
  | "completed"
  | "failed"
  | "cancelled";

export const WORKFLOW_STATUS_META: Record<WorkflowStatus, { label: string; color: string }> = {
  running: { label: "执行中", color: "processing" },
  waiting_human: { label: "待人工审批", color: "warning" },
  approved: { label: "已审批", color: "success" },
  rejected: { label: "审批拒绝", color: "error" },
  completed: { label: "已完成", color: "success" },
  failed: { label: "失败", color: "error" },
  cancelled: { label: "已取消", color: "default" }
};

/** 六 Agent 工作流事件名 */
export type AgentEventName =
  | "agent_start"
  | "agent_complete"
  | "agent_failed"
  | "agent_rollback"
  | "approval_required"
  | "alert_triggered"
  | "execution_feedback"
  | "workflow_start"
  | "workflow_complete"
  | "workflow_failed";

// ═══════════════════════════════════════════════════════════════════════
// 监控事件（原有事件 + 六 Agent 事件）
// ═══════════════════════════════════════════════════════════════════════

export type MonitorEventName =
  | "session_created"
  | "tool_start"
  | "assistant_call"
  | "task_result"
  | "task_cancelled"
  | "error"
  | AgentEventName
  | string;

export interface MonitorMessage {
  type: "monitor_event";
  event: MonitorEventName;
  message: string;
  data: Record<string, unknown>;
  timestamp: string;
  /** 六 Agent 事件统一携带 run 级追踪字段 */
  workflow_run_id?: string;
  agent_name?: string;
}

export interface PongMessage {
  type: "pong";
  message: string;
}

export type SocketMessage = MonitorMessage | PongMessage;

// ═══════════════════════════════════════════════════════════════════════
// 工作流接口类型（/api/workflow/*）
// ═══════════════════════════════════════════════════════════════════════

export interface WorkflowStartRequest {
  scenario_id: string;
  query?: string;
  thread_id?: string;
  run_id?: string;
  mine_id?: string;
  roadway_id?: string;
  operator?: { user_id?: string; role?: string; permissions?: string[] };
}

export interface WorkflowStartResponse {
  status: string;
  run_id: string;
  thread_id: string;
  scenario_id: string;
}

export interface ApprovalActionRequest {
  operator?: string;
  note?: string;
}

export interface ApprovalResponse {
  status: string;
  run_id: string;
}

/** /api/workflow/{run_id}/cancel 返回 */
export interface CancelWorkflowResponse {
  status: string;
  run_id: string;
}

/** 人工审批请求（协同管控节点 interrupt 携带） */
export interface ApprovalRequest {
  notice?: string;
  type?: string;
  coordination_id: string;
  warning_id?: string | null;
  required_role: string;
  risk_level?: string;
  dispatched_actions?: string[];
}

/** 单个 Agent 结果摘要（status 接口 results 字段） */
export interface AgentResultSummary {
  status: string;
  summary: string;
  confidence: number | null;
  next_action: string;
}

/** /api/workflow/{run_id}/status 返回 */
export interface WorkflowStatusResponse {
  run_id: string;
  thread_id: string;
  scenario_id: string;
  query?: string | null;
  status: WorkflowStatus;
  phases: string[];
  results: Record<string, AgentResultSummary>;
  approval_request?: ApprovalRequest | null;
  approval_decision?: Record<string, unknown> | null;
  events: MonitorMessage[];
  error?: string | null;
  started_at: string;
  finished_at?: string | null;
}

/** 场景元数据（前端示例卡片） */
export interface ScenarioMeta {
  id: string;
  title: string;
  description: string;
  risk: RiskLevel;
  input: string;
  context: string[];
  branch: string;
}

/** 7 套演示 fixture 场景 */
export const WORKFLOW_SCENARIOS: ScenarioMeta[] = [
  { id: "normal", title: "常规监测", description: "监测指标正常，无需进入处置链", risk: "normal", input: "MINE-001 / RDW-001 / 工作面-1：近 30 分钟微震频次 1.2 次/h，最大能量 2,800 J，b 值 1.18，数据缺失率 1%。请求判定当前顶板风险并给出复核周期。", context: ["频次 1.2 次/h", "最大能量 2,800 J", "b 值 1.18 · 缺失率 1%"], branch: "感知预警 → 结束" },
  { id: "yellow", title: "黄色预警", description: "微震频次与能量持续升高，资源充足时下发常规支护", risk: "yellow", input: "MINE-001 / RDW-002 / 工作面-2：5 条微震事件在 30 分钟内由 6.5 次/h 升至 8.0 次/h，最大能量 18,000 J，b 值 0.92，数据缺失率 5%。现有支护工 10/6、工程师 3/2、锚杆 500/200、钻机 4/2。请生成黄色预警处置建议并核算资源。", context: ["频次 6.5 → 8.0 次/h", "最大能量 18,000 J · b 值 0.92", "支护工 10/6 · 锚杆 500/200"], branch: "感知 → 知识 → 调度 → 资源 → 管控 → 反思" },
  { id: "red", title: "红色预警", description: "高能量事件密集聚集，快速告警并在下发前请求人工审批", risk: "red", input: "MINE-001 / RDW-003 / 工作面-3：8 条高能量事件密集聚集，频次峰值 22 次/h，最大能量 300,000 J，b 值由 0.60 降至 0.35，数据缺失率 10%。应急队 18/15、液压支柱 80/50、钻机 6/5。请执行红色预警快速处置并标记需审批动作。", context: ["频次峰值 22 次/h", "最大能量 300,000 J", "b 值 0.60 → 0.35 · 快速告警"], branch: "感知 → 知识 → 调度 → 资源 → 管控（审批）" },
  { id: "missing_knowledge", title: "案例资料待上传", description: "待上传相似案例，进入专家会商流程", risk: "orange", input: "MINE-001 / RDW-004 / 工作面-4：微震频次 12 次/h，最大能量 90,000 J，b 值 0.68，数据缺失率 8%。请上传同构顶板案例并提交专家会商意见。", context: ["频次 12 次/h", "最大能量 90,000 J · b 值 0.68", "案例资料待上传 · 缺失率 8%"], branch: "感知 → 知识 → 调度" },
  { id: "resource_insufficient", title: "资源待补充", description: "关键材料与钻机缺口，回到调度决策重排方案", risk: "orange", input: "MINE-001 / RDW-005 / 巷道-5：6 条事件使频次由 10 次/h 升至 15 次/h，最大能量 70,000 J，b 值 0.72，数据缺失率 15%。工字钢仅 15/100 根、钻机 1/3 台。请识别资源阻断并返回调度节点调整处置计划。", context: ["频次 10 → 15 次/h", "工字钢 15/100 根", "钻机 1/3 台 · 缺失率 15%"], branch: "资源评估 → 调度决策" },
  { id: "reflection_rollback", title: "风险未降", description: "执行反馈显示风险未下降，反思结果回退调度决策", risk: "orange", input: "MINE-001 / RDW-006 / 工作面-6：5 条事件中最大能量 160,000 J，频次峰值 20 次/h，b 值 0.55 降至 0.42，数据缺失率 12%。首轮支护执行后风险评分未下降，请让反思节点回退并重新调度。", context: ["频次峰值 20 次/h", "最大能量 160,000 J", "风险评分未下降 · 快速告警"], branch: "反思迭代 ⇢ 调度决策" },
  { id: "max_iterations", title: "循环上限", description: "多轮调整仍未满足目标，停止自动迭代并请求人工", risk: "red", input: "MINE-001 / RDW-007 / 工作面-7：连续两轮处置后风险评分仍高于 0.8，频次 19 次/h，最大能量 210,000 J，b 值 0.40。请在达到最大迭代次数后停止自动循环并提交人工决策。", context: ["风险评分 > 0.8", "频次 19 次/h · 最大能量 210,000 J", "达到最大迭代次数"], branch: "反思迭代（上限） → 人工决策" }
];

// ═══════════════════════════════════════════════════════════════════════
// 原有 REST 接口类型（保留）
// ═══════════════════════════════════════════════════════════════════════

export interface UploadResponse {
  status: "uploaded" | string;
  files: string[];
}

export interface OutputFile {
  name: string;
  type: "file" | string;
  path: string;
  size: number;
  mtime: number;
}

export interface FileListResponse {
  files?: OutputFile[];
  error?: string;
}

export interface UploadedItem {
  uid: string;
  name: string;
  size: number;
  raw: File;
}

// ═══════════════════════════════════════════════════════════════════════
// 六 Agent 完整结果类型（/api/workflow/{run_id}/result）
// ═══════════════════════════════════════════════════════════════════════

/** 六 Agent 单个结果的公共字段（每个 Agent 除公共字段外还有专属业务字段） */
export interface AgentFullResult {
  status: string;
  summary: string;
  confidence: number | null;
  evaluation_basis?: string;
  evidence?: Array<{ source_type?: string; description?: string; value?: Record<string, unknown> }>;
  warnings?: string[];
  errors?: string[];
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  next_action?: string;
  warning_id?: string;
}

/** 感知预警 Agent 完整结果 */
export interface PerceptionResult extends AgentFullResult {
  risk_level: string | null;
  risk_score: number | null;
  assessment?: { method_id: string; summary: string; applicability: string; reasons: string[]; metrics: Record<string, unknown>; profile: Record<string, unknown> | null; alarm: boolean | null; source: string; window_start: string; window_end: string; input_files: string[] };
  model_mode: string;
  affected_area?: { mine_id: string; roadway_id: string; locations: string[] };
  causal_factors?: Array<{ factor: string; importance: number; trend: string; evidence: string[] }>;
  anomalies?: string[];
  triggered_rules?: string[];
}

/** 知识检索 Agent 完整结果 */
export interface RegulationReference {
  document?: string;
  clause?: string;
  requirement?: string;
  mandatory?: boolean;
  source?: string;
  source_url?: string;
  applicability?: string;
  verified_at?: string;
}

export interface KnowledgeResult extends AgentFullResult {
  similar_cases?: Array<{
    case_id: string;
    similarity: number | null;
    match_terms?: string[];
    source_url?: string;
    source_status?: string;
    causes?: string[];
    actions?: string[];
    outcome?: string;
    source?: string;
  }>;
  regulations?: Array<RegulationReference | string>;
  support_recommendations?: Array<string | {scheme_id:string;scheme_name:string;source:string;actions:string[];expert_review:string;applicable_conditions:string[]}>;
  knowledge_conflicts?: Array<string | {
    source_a?: string;
    source_b?: string;
    resolution?: string;
  }>;
  missing_topics?: string[];
}

/** 调度决策 Agent 完整结果 */
export interface DecisionResult extends AgentFullResult {
  decision_level?: string;
  decision_id?: string;
  primary_plan?: {
    plan_id?: string;
    name?: string;
    actions?: Array<{
      sequence: number;
      action: string;
      owner_role?: string;
      deadline_minutes?: number;
      requires_approval?: boolean;
    }>;
  };
  alternative_plans?: Array<{ plan_id?: string; name?: string; risk?: string }>;
  prohibited_actions?: string[];
  human_approval_required?: boolean;
  resource_requirements?: {
    personnel?: string[];
    materials?: string[];
    equipment?: string[];
    communication?: string[];
  };
}

/** 资源评估 Agent 完整结果 */
export interface ResourceResult extends AgentFullResult {
  feasibility?: string;
  feasibility_score?: number | null;
  quantity_check?: "sufficient" | "insufficient" | "not_provided";
  execution_readiness?: "unverified" | "verified";
  resource_gaps?: Array<{
    resource_type?: string;
    resource_name: string;
    required: number;
    available: number;
    unit?: string;
    reason?: string;
  }>;
  blocking_issues?: Array<{ description?: string } | string>;
  adjustment_suggestions?: string[];
  redesign_required?: boolean;
}

/** 协同管控 Agent 完整结果 */
export interface ControlResult extends AgentFullResult {
  coordination_id?: string;
  permission_check?: { passed?: boolean | null; required_role?: string; actual_role?: string | null; denied_actions?: string[] };
  notification_results?: Array<{ recipient: string; channel: string; status: string; sent_at?: string | null }>;
  dispatch_status?: string;
  dispatched_actions?: string[];
  report_artifacts?: string[];
  human_action_required?: boolean;
}

/** 反思迭代 Agent 完整结果 */
export interface ReflectionResult extends AgentFullResult {
  iteration_count?: number;
  goal_completion_score?: number | null;
  risk_change?: { before?: number; after?: number | null; trend?: string };
  knowledge_candidates?: Array<{
    entity?: string;
    entity_type?: string;
    relation?: string;
    evidence?: string;
    source?: string;
    confidence?: number;
    review_status?: string;
  }>;
  return_decision?: { action?: string; target_agent?: string; reason?: string };
  human_review_required?: boolean;
  optimization_suggestions?: Array<{ type?: string; description?: string }>;
}

/** 完整工作流结果响应（GET /api/workflow/{run_id}/result） */
export interface WorkflowResultResponse {
  provenance?: {snapshot_id:string;execution_mode:string;configuration?:{version:string;revision:string}|null;documents:Array<{asset_id:string;title:string;sha256:string;version:string}>;task:Record<string,unknown>} | null;
  run_id: string;
  scenario_id: string;
  query?: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  agents: {
    perception_result?: PerceptionResult;
    knowledge_result?: KnowledgeResult;
    decision_result?: DecisionResult;
    resource_result?: ResourceResult;
    control_result?: ControlResult;
    reflection_result?: ReflectionResult;
  };
  approval_decision?: { decision?: string; approved_by?: string; note?: string; decided_at?: string; verified_identity?: {user_id:string;roles:string[]} | null };
  knowledge_writeback?: { status?: string; data?: { written?: number; skipped?: unknown[]; rejected?: unknown[] } };
  iteration_count: number;
  max_iterations: number;
}
