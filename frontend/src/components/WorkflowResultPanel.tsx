import { memo, useState } from "react";
import { DownOutlined, RightOutlined } from "@ant-design/icons";
import type {
  WorkflowResultResponse,
  AgentFullResult,
  PerceptionResult,
  KnowledgeResult,
  DecisionResult,
  ResourceResult,
  ControlResult,
  ReflectionResult
} from "../types";

interface WorkflowResultPanelProps {
  result: WorkflowResultResponse;
}

/** 没有经校准的算法依据时不展示固定数值；0也不能替代“待输入”。 */
function confidenceLabel(data?: AgentFullResult | null): string {
  if (data?.evaluation_basis === "actual_snapshot") return "不适用（确定性计算 / 资料引用）";
  return data?.evaluation_basis === "calibrated" && data.confidence != null ? String(data.confidence) : "待输入";
}

function SourceLink({ url }: { url?: string }) {
  return url && /^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noreferrer">查看官方来源</a> : null;
}

/** Agent 结果卡片配置（标题、序号——对应执行链顺序） */
const AGENT_CARDS: Array<{
  key: keyof WorkflowResultResponse["agents"];
  label: string;
  idx: string;
}> = [
  { key: "perception_result", label: "感知预警", idx: "AGENT 01" },
  { key: "knowledge_result", label: "知识检索", idx: "AGENT 02" },
  { key: "decision_result", label: "调度决策", idx: "AGENT 03" },
  { key: "resource_result", label: "资源评估", idx: "AGENT 04" },
  { key: "control_result", label: "协同管控", idx: "AGENT 05" },
  { key: "reflection_result", label: "反思迭代", idx: "AGENT 06" }
];

/** Agent 状态 → 徽章样式映射 */
function statusStyle(status: string): { bg: string; color: string; label: string } {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    success: { bg: "rgba(93,255,159,0.12)", color: "var(--green)", label: "SUCCESS" },
    partial: { bg: "rgba(255,200,87,0.12)", color: "var(--amber)", label: "PARTIAL" },
    failed: { bg: "rgba(255,92,122,0.12)", color: "var(--red)", label: "FAILED" },
    waiting_human: { bg: "rgba(255,200,87,0.12)", color: "var(--amber)", label: "WAITING" }
  };
  return map[status] || map.success;
}

/** 单条 标签-值 行 */
function KvRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        padding: "6px 0",
        borderBottom: "1px solid var(--line)",
        fontSize: 13
      }}
    >
      <span
        style={{
          width: 120,
          color: "var(--muted)",
          fontFamily: "monospace",
          fontSize: 12,
          flexShrink: 0
        }}
      >
        {label}
      </span>
      <span style={{ flex: 1, wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

/** 数据表格（min-width 保证移动端横向滚动，页面主体不横向滚动） */
function DataTable({
  headers,
  rows
}: {
  headers: string[];
  rows: React.ReactNode[][];
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 12.5,
          fontFamily: "inherit"
        }}
      >
        <thead>
          <tr>
            {headers.map((h) => (
              <th
                key={h}
                style={{
                  textAlign: "left",
                  padding: "8px 10px",
                  borderBottom: "1px solid var(--line)",
                  color: "var(--muted)",
                  fontSize: 11,
                  fontWeight: 500,
                  whiteSpace: "nowrap"
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td
                  key={j}
                  style={{
                    padding: "8px 10px",
                    borderBottom: "1px solid rgba(113,247,255,0.1)"
                  }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 分区小标题 */
function SubLabel({ children, color = "var(--cyan)" }: { children: React.ReactNode; color?: string }) {
  return (
    <div
      style={{
        fontSize: 11,
        color,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        margin: "12px 0 8px",
        fontFamily: "monospace"
      }}
    >
      {children}
    </div>
  );
}

/** 感知预警内容 */
function PerceptionContent({ data }: { data: PerceptionResult }) {
  const input = data.evidence?.find((item) => item.source_type === "user_monitoring_summary")?.value;
  // 原样展示实际参与评分的字段，缺失观测显示未提供，便于与用户输入逐项核对。
  const inputLabels: Array<[string, string]> = [
    ["mine_id", "矿井"], ["roadway_id", "巷道"], ["area", "工作面"],
    ["window_minutes", "窗口（分钟）"], ["event_count", "事件数"],
    ["max_energy_j", "最大能量（J）"], ["frequency_start", "起始频次（次/h）"],
    ["frequency_end", "末次频次（次/h）"], ["b_start", "起始 b 值"], ["b_end", "末次 b 值"],
    ["missing_rate", "缺失率"], ["high_energy_count", "高能事件数"],
    ["spatial_cluster_fraction", "空间集中比例"],
  ];
  return (
    <>
      {data.assessment && <>
        <SubLabel>原始记录计算依据</SubLabel>
        <KvRow label="方法 / 适用性" value={`${data.assessment.method_id} / ${data.assessment.applicability === "applicable" ? "计算口径适用" : "不适用"}`} />
        <KvRow label="方法报警" value={data.assessment.alarm == null ? "仅统计，未选择分级规则" : data.assessment.alarm ? "触发" : "未触发"} />
        <KvRow label="实际数据来源" value={data.assessment.source} />
        <KvRow label="监测窗口" value={`${data.assessment.window_start} → ${data.assessment.window_end}`} />
        {Object.entries(data.assessment.metrics).map(([key, value]) => <KvRow key={key} label={key} value={value == null ? "待输入" : typeof value === "object" ? JSON.stringify(value) : String(value)} />)}
        {data.assessment.reasons.map(reason => <p key={reason}>{reason}</p>)}
        <details><summary>参数来源与核验状态</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(data.assessment.profile, null, 2)}</pre></details>
      </>}
      {input ? <>
        <SubLabel>实际参与评分的输入</SubLabel>
        {inputLabels.map(([key, label]) => <KvRow key={key} label={label} value={
          input[key] == null ? "未提供" : key === "missing_rate" || key === "spatial_cluster_fraction"
            ? `${(Number(input[key]) * 100).toFixed(1)}%` : String(input[key])
        } />)}
      </> : null}
      <SubLabel>风险评级</SubLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "0 16px" }}>
        <KvRow label="risk_level" value={<span style={{ color: data.risk_level === "yellow" ? "var(--amber)" : "var(--green)" }}>{data.risk_level}</span>} />
        <KvRow label="risk_score" value={data.risk_score} />
        <KvRow label="置信度" value={confidenceLabel(data)} />
        <KvRow label="model_mode" value={data.model_mode} />
      </div>
      {data.anomalies && data.anomalies.length > 0 ? (
        <>
          <SubLabel>异常项</SubLabel>
          <ul style={{ paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, margin: 0 }}>
            {data.anomalies.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      ) : null}
      {data.causal_factors && data.causal_factors.length > 0 ? (
        <>
          <SubLabel>致灾因子</SubLabel>
          <DataTable
            headers={["因子", "趋势", "权重"]}
            rows={data.causal_factors.map((f) => [
              f.factor,
              <span key="trend" style={{ color: f.trend === "rising" ? "var(--red)" : f.trend === "falling" ? "var(--green)" : "var(--muted)" }}>
                {f.trend === "unknown" ? "待输入" : f.trend}
              </span>,
              f.importance
            ])}
          />
        </>
      ) : null}
    </>
  );
}

/** 知识检索内容 */
function KnowledgeContent({ data }: { data: KnowledgeResult }) {
  const traceable = data.evaluation_basis === "traceable_documents";
  const simulated = data.evidence?.some((item) => item.source_type === "fixture_yaml");
  return (
    <>
      {simulated ? <p style={{ color: "var(--amber)" }}>案例资料</p> : null}
      {data.support_recommendations?.length ? <><SubLabel>已引用支护与处置方案</SubLabel>{data.support_recommendations.map((s,i)=><details key={i}><summary>{typeof s === "string" ? s : s.scheme_name}</summary>{typeof s !== "string" && <><p>来源：{s.source} · 适用测区：{s.applicable_conditions.join("、")}</p><p style={{whiteSpace:"pre-wrap"}}>{s.expert_review}</p>{s.actions.map((a,j)=><p key={j}>{j+1}. {a}</p>)}</>}</details>)}</> : null}
      {data.similar_cases && data.similar_cases.length > 0 ? (
        <>
          <SubLabel>{traceable ? "相关事故资料" : "案例参考"}</SubLabel>
          <DataTable
            headers={["案例", "匹配依据", "致因摘要", "历史事故结果", "来源"]}
            rows={data.similar_cases.map((c) => [
              c.case_id,
              <span key="sim" style={{ fontFamily: "monospace" }}>
                {c.match_terms?.join("、") || (c.source_status === "user_scoped_document" ? "测区配置选定" : "待输入")}
              </span>,
              (c.causes || []).join("；") || "—",
              c.outcome || "—",
              <span key="source">{c.source || "—"}<br /><SourceLink url={c.source_url} />{c.source_status === "curated_summary_unverified" ? <p>本地整理摘要，未逐条在线核验</p> : null}</span>
            ])}
          />
        </>
      ) : null}
      {data.knowledge_conflicts && data.knowledge_conflicts.length > 0 ? (
        <>
          <SubLabel color="var(--amber)">知识冲突</SubLabel>
          <DataTable
            headers={["冲突方 A", "冲突方 B", "仲裁依据"]}
            rows={data.knowledge_conflicts.map((k) => [
              typeof k === "string" ? k : k.source_a || "—",
              typeof k === "string" ? "—" : k.source_b || "—",
              typeof k === "string" ? "—" : k.resolution || "—"
            ])}
          />
        </>
      ) : null}
      {data.regulations && data.regulations.length > 0 ? (
        <>
          <SubLabel>{traceable ? "法规原文与适用条件" : "法规参考"}（{data.regulations.length}）</SubLabel>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {data.regulations.map((r, i) => (
              <span
                key={i}
                style={{
                  background: "var(--surface)",
                  border: "1px solid var(--line)",
                  borderRadius: 6,
                  padding: "3px 10px",
                  fontSize: 12,
                  color: "var(--muted)"
                }}
              >
                {typeof r === "string"
                  ? r
                  : `${r.document || r.source || "规程"}${r.clause ? ` · ${r.clause}` : ""}${r.requirement ? `：${r.requirement}` : ""}`}
                {typeof r !== "string" ? <><br /><SourceLink url={r.source_url} />{r.verified_at ? <span> · 原文核对：{r.verified_at}</span> : null}{r.applicability ? <p>适用条件：{r.applicability}</p> : null}</> : null}
              </span>
            ))}
          </div>
        </>
      ) : null}
    </>
  );
}

/** 调度决策内容 */
function DecisionContent({ data }: { data: DecisionResult }) {
  return (
    <>
      {data.evaluation_basis === "rule_based_draft" ? <p>规则生成的初步建议，时限为演示复查周期，责任岗位与现场适用性待确认。</p> : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        {data.decision_id ? (
          <span
            style={{
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: 6,
              padding: "4px 10px",
              fontSize: 12,
              color: "var(--cyan)",
              fontFamily: "monospace"
            }}
          >
            {data.decision_id}
          </span>
        ) : null}
        {data.decision_level ? (
          <span
            style={{
              background: "rgba(255,200,87,0.08)",
              border: "1px solid rgba(255,200,87,0.35)",
              borderRadius: 6,
              padding: "4px 10px",
              fontSize: 12,
              color: "var(--amber)"
            }}
          >
            {data.decision_level}
          </span>
        ) : null}
        {data.human_approval_required ? (
          <span
            style={{
              background: "rgba(255,92,122,0.08)",
              border: "1px solid rgba(255,92,122,0.35)",
              borderRadius: 6,
              padding: "4px 10px",
              fontSize: 12,
              color: "var(--red)"
            }}
          >
            需人工审批
          </span>
        ) : null}
      </div>
      {data.primary_plan && data.primary_plan.actions && data.primary_plan.actions.length > 0 ? (
        <>
          <SubLabel>处置方案动作</SubLabel>
          <DataTable
            headers={["序", "动作", "责任人", "时限(min)", "需审批"]}
            rows={data.primary_plan.actions.map((a) => [
              a.sequence,
              a.action,
              a.owner_role || "—",
              a.deadline_minutes ?? "—",
              a.requires_approval ? "是" : "否"
            ])}
          />
        </>
      ) : null}
      {data.prohibited_actions && data.prohibited_actions.length > 0 ? (
        <>
          <SubLabel color="var(--red)">禁止动作</SubLabel>
          <ul style={{ paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, margin: 0 }}>
            {data.prohibited_actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

/** 资源评估内容（failed 时红色高亮） */
function ResourceContent({ data }: { data: ResourceResult }) {
  const infeasible = data.feasibility === "infeasible";
  const inventory = data.evidence?.find(item => item.source_type === "business_inventory" || item.source_type === "mysql_inventory")?.value as {inventory?:{source:string;observed_at?:string;queried_at?:string;available_resources:Record<string,Record<string,number>>};error?:string} | undefined;
  return (
    <>
      {inventory && <><SubLabel>所选库存来源</SubLabel>
        {inventory.error ? <p>{inventory.error}；未用预设库存替代。</p> : <>
          <KvRow label="来源" value={inventory.inventory?.source || "—"} />
          <KvRow label="库存观测时间" value={inventory.inventory?.observed_at || "未提供"} />
          <DataTable headers={["类别", "资源", "接口返回可用数量"]} rows={Object.entries(inventory.inventory?.available_resources || {}).flatMap(([group,items]) => Object.entries(items).map(([name,count]) => [group,name,count]))} />
        </>}
      </>}
      {data.quantity_check ? <>
        <SubLabel>数量核算与执行条件</SubLabel>
        <KvRow label="所列数量" value={{ sufficient: "满足用户所列需求", insufficient: "存在数量缺口", not_provided: "未提供" }[data.quantity_check]} />
        <KvRow label="整体执行条件" value={data.execution_readiness === "verified" ? "已核验" : "未核验"} />
      </> : null}
      <SubLabel>可行性</SubLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "0 16px" }}>
        <KvRow
          label="feasibility"
          value={
            <span style={{ color: infeasible ? "var(--red)" : "var(--green)" }}>
              {data.feasibility || "—"}
            </span>
          }
        />
        <KvRow label="可行性评分" value={data.feasibility_score ?? "待输入"} />
        <KvRow label="缺口数" value={(data.resource_gaps?.length ?? 0)} />
        <KvRow label="阻塞数" value={data.blocking_issues?.length ?? 0} />
      </div>
      {data.resource_gaps && data.resource_gaps.length > 0 ? (
        <>
          <SubLabel color="var(--red)">资源缺口</SubLabel>
          <DataTable
            headers={["类型", "资源", "需", "有", "原因"]}
            rows={data.resource_gaps.map((g) => [
              g.resource_type || "—",
              g.resource_name,
              g.required,
              <span key="avail" style={{ color: g.available === 0 ? "var(--red)" : "var(--amber)" }}>
                {g.available}
              </span>,
              g.reason || "—"
            ])}
          />
        </>
      ) : null}
      {data.blocking_issues && data.blocking_issues.length > 0 ? (
        <>
          <SubLabel color="var(--red)">阻塞问题</SubLabel>
          <ul style={{ paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, margin: 0 }}>
            {data.blocking_issues.map((issue, i) => (
              <li key={i}>{typeof issue === "string" ? issue : issue?.description || "—"}</li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

/** 协同管控内容 */
function ControlContent({ data }: { data: ControlResult }) {
  return (
    <>
      {data.permission_check ? (
        <>
          <SubLabel>权限检查</SubLabel>
          <KvRow
            label="执行权限核验"
            value={
              data.permission_check.passed == null ? data.permission_check.actual_role ? "研判人身份已核验；执行授权未确认" : "未核验（缺少可信身份）" : data.permission_check.passed ? (
                <span style={{ color: "var(--green)" }}>✓ true</span>
              ) : (
                <span style={{ color: "var(--red)" }}>✗ false</span>
              )
            }
          />
          <KvRow label="所需岗位" value={data.permission_check.required_role || "—"} />
          <KvRow label="已核验身份" value={data.permission_check.actual_role || "未核验"} />
          {data.permission_check.denied_actions && data.permission_check.denied_actions.length > 0 ? (
            <KvRow label="denied_actions" value={data.permission_check.denied_actions.join(", ")} />
          ) : null}
        </>
      ) : null}
      <KvRow label="下发状态" value={data.dispatch_status === "not_dispatched" ? "未下发" : data.dispatch_status || "未核验"} />
      {data.notification_results?.length ? <DataTable headers={["拟通知岗位", "通道", "发送状态"]} rows={data.notification_results.map((item) => [item.recipient, item.channel, item.status === "sent" && item.sent_at ? "已发送" : item.status === "failed" ? "发送失败" : "未发送"])} /> : null}
      {data.dispatched_actions && data.dispatched_actions.length > 0 ? (
        <>
          <SubLabel>下发动作</SubLabel>
          <ul style={{ paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, margin: 0 }}>
            {data.dispatched_actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      ) : null}
      {data.report_artifacts && data.report_artifacts.length > 0 ? (
        <>
          <SubLabel>产出物</SubLabel>
          <ul style={{ paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, margin: 0 }}>
            {data.report_artifacts.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

/** 反思迭代内容 */
function ReflectionContent({ data }: { data: ReflectionResult }) {
  const comparison = data.evidence?.find(item => item.source_type === "post_observation_comparison")?.value as {
    comparable: boolean; reasons: string[]; changes: Record<string, { before: number; after: number; delta: number }>;
    feedback?: { parent_run_id: string; reported_by: string; actions: Array<{ sequence: number; status: string; occurred_at: string }> };
  } | undefined;
  const metricNames: Record<string, string> = { event_count: "事件数", max_energy_j: "最大能量（J）", total_energy_j: "总能量（J）", frequency_per_hour: "频次（次/h）", b_value: "b值" };
  return (
    <>
      {comparison && <>
        <SubLabel>关联观测复核</SubLabel>
        <KvRow label="原任务" value={comparison.feedback?.parent_run_id ?? "—"} />
        <KvRow label="比较条件" value={comparison.comparable ? "口径一致，可比较目录指标" : comparison.reasons.join("；")} />
        {comparison.comparable && <DataTable headers={["指标", "原观测", "新观测", "净变化"]}
          rows={Object.entries(comparison.changes).map(([key, values]) => [metricNames[key] || key, ...[values.before, values.after, values.delta].map(v => Number(v.toPrecision(8)))])} />}
        <KvRow label="人工报告人" value={comparison.feedback?.reported_by ?? "—"} />
        {comparison.feedback?.actions.length ? <DataTable headers={["原动作序号", "人工报告状态", "发生时间"]}
          rows={comparison.feedback.actions.map(action => [action.sequence, action.status, action.occurred_at])} /> : <p>未提交动作执行报告，处置效果尚未验证。</p>}
        <p>人工报告未经身份及执行核验；指标变化不能据此归因于处置，也不能自动解除风险。</p>
      </>}
      <SubLabel>复盘指标</SubLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "0 16px" }}>
        <KvRow label="iterations" value={`${data.iteration_count ?? 0} 轮`} />
        <KvRow label="目标完成度" value={data.goal_completion_score ?? "待输入"} />
        <KvRow
          label="风险变化"
          value={
            data.risk_change
              ? data.risk_change.trend === "unknown" ? (comparison ? "已比较观测指标，现场风险变化仍未验证" : "待输入处置后监测数据，等待现场确认") : `${data.risk_change.before} → ${data.risk_change.after} (${data.risk_change.trend})`
              : "—"
          }
        />
        <KvRow
          label="人工复核"
          value={
            data.human_review_required ? (
              <span style={{ color: "var(--amber)" }}>required</span>
            ) : (
              "not required"
            )
          }
        />
      </div>
      {data.knowledge_candidates && data.knowledge_candidates.length > 0 ? (
        <>
          <SubLabel color="var(--amber)">知识候选（{data.knowledge_candidates.length}）</SubLabel>
          <DataTable
            headers={["实体", "类型", "关系", "证据", "置信", "审核"]}
            rows={data.knowledge_candidates.map((c) => [
              c.entity || "—",
              c.entity_type || "—",
              c.relation || "—",
              c.evidence || "—",
              c.confidence ?? "—",
              <span
                key="review"
                style={{
                  padding: "2px 8px",
                  borderRadius: 5,
                  fontSize: 11,
                  background:
                    c.review_status === "pending" ? "rgba(255,200,87,0.1)" : "var(--surface)",
                  color: c.review_status === "pending" ? "var(--amber)" : "var(--muted)",
                  border: `1px solid ${c.review_status === "pending" ? "rgba(255,200,87,0.4)" : "var(--line)"}`
                }}
              >
                {c.review_status || "—"}
              </span>
            ])}
          />
        </>
      ) : null}
      {data.return_decision ? (
        <>
          <SubLabel color={data.return_decision.action === "finish" ? "var(--green)" : "var(--amber)"}>
            迭代决策
          </SubLabel>
          <div
            style={{
              background: "var(--surface)",
              border: "1px solid var(--line)",
              borderRadius: 8,
              padding: "10px 14px",
              fontSize: 12.5
            }}
          >
            <b>{data.return_decision.action}</b>
            {data.return_decision.target_agent ? ` → ${data.return_decision.target_agent}` : ""}
            {data.return_decision.reason ? (
              <span style={{ color: "var(--muted)" }}>（{data.return_decision.reason}）</span>
            ) : (
              ""
            )}
          </div>
        </>
      ) : null}
    </>
  );
}

/** 根据 Agent 类型分发到对应内容组件 */
function ContentSwitcher({ data }: { data: AgentFullResult }) {
  if ("risk_level" in data) return <PerceptionContent data={data as PerceptionResult} />;
  if ("similar_cases" in data) return <KnowledgeContent data={data as KnowledgeResult} />;
  if ("primary_plan" in data || "prohibited_actions" in data)
    return <DecisionContent data={data as DecisionResult} />;
  if ("feasibility" in data || "resource_gaps" in data)
    return <ResourceContent data={data as ResourceResult} />;
  if ("permission_check" in data || "coordination_id" in data)
    return <ControlContent data={data as ControlResult} />;
  if ("knowledge_candidates" in data || "return_decision" in data)
    return <ReflectionContent data={data as ReflectionResult} />;
  return null;
}

/** 单个 Agent 结果卡片（可折叠，默认展开） */
const AgentResultCard = memo(function AgentResultCard({
  idx,
  label,
  data
}: {
  idx: string;
  label: string;
  data: AgentFullResult;
}) {
  const [open, setOpen] = useState(true);
  const st = statusStyle(data.status);
  const isFailed = data.status === "failed";

  return (
    <div
      style={{
        background: "var(--surface)",
        border: `1px solid ${isFailed ? "rgba(255,92,122,0.5)" : "var(--line)"}`,
        borderLeft: isFailed ? "3px solid var(--red)" : undefined,
        borderRadius: 12,
        marginBottom: 12,
        overflow: "hidden"
      }}
    >
      <button
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "14px 18px",
          background: "none",
          border: "none",
          borderBottom: open ? "1px solid var(--line)" : "none",
          color: "var(--text)",
          cursor: "pointer",
          textAlign: "left",
          fontFamily: "inherit"
        }}
      >
        <span style={{ fontFamily: "monospace", fontSize: 11, color: "var(--cyan)", letterSpacing: "0.1em" }}>
          {idx}
        </span>
        <span style={{ fontWeight: 700, fontSize: 15, flex: 1 }}>{label}</span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            padding: "4px 10px",
            borderRadius: 6,
            fontFamily: "monospace",
            background: st.bg,
            color: st.color
          }}
        >
          {st.label}
        </span>
        <span style={{ fontFamily: "monospace", fontSize: 12, color: "var(--muted)" }}>
          置信度{confidenceLabel(data)}
        </span>
        {open ? (
          <DownOutlined style={{ color: "var(--muted)", fontSize: 12 }} />
        ) : (
          <RightOutlined style={{ color: "var(--muted)", fontSize: 12 }} />
        )}
      </button>
      {open ? (
        <div style={{ padding: "16px 18px" }}>
          <p style={{ fontSize: 13.5, margin: "0 0 14px", lineHeight: 1.6 }}>{data.summary}</p>
          <ContentSwitcher data={data} />
          {data.evidence?.some((item) => item.source_type === "official_regulation") ? <>
            <SubLabel>建议依据</SubLabel>
            <ul>{data.evidence.filter((item) => item.source_type === "official_regulation").map((item, index) => <li key={index}>{item.description} <SourceLink url={typeof item.value?.source_url === "string" ? item.value.source_url : undefined} /></li>)}</ul>
          </> : null}
          {data.warnings?.filter((warning) => warning !== "高相似度历史案例不足").length ? <>
            <SubLabel color="var(--amber)">数据说明与待核验项</SubLabel>
            <ul>{data.warnings.filter((warning) => warning !== "高相似度历史案例不足").map((warning, index) => <li key={index}>{warning === "支护方案推荐为空" ? "支护方案资料待上传" : warning}</li>)}</ul>
          </> : null}
        </div>
      ) : null}
    </div>
  );
});

/** 六 Agent 结果展示主面板 */
export const WorkflowResultPanel = memo(function WorkflowResultPanel({
  result
}: WorkflowResultPanelProps) {
  const perception = result.agents.perception_result;
  const decision = result.agents.decision_result;
  const resources = result.agents.resource_result;
  const approval = result.approval_decision;
  const riskLabel = perception?.risk_level || "待输入";
  const riskTone = riskLabel === "red" || riskLabel === "orange" ? "critical" : riskLabel === "yellow" ? "warning" : "stable";
  const plan = decision?.primary_plan;
  const nextAction = plan?.actions?.[0]?.action || decision?.summary || (riskLabel === "normal" ? "本次评分未触发后续处置链路，继续监测并核验数据" : "等待调度决策结果");
  const feasibility = resources?.quantity_check === "insufficient" ? "存在实际数量缺口" : resources?.quantity_check === "sufficient" ? "数量满足 · 可进行现场核验" : resources?.feasibility === "feasible" ? "资源可行" : "资源待配置";
  const needsHuman = decision?.human_approval_required || result.agents.control_result?.human_action_required;
  const custom = result.scenario_id === "custom";
  const approvalLabel = approval?.decision === "approved" ? custom ? "已记录同意意见，尚未授权执行" : "已审批" : approval?.decision === "rejected" ? "已记录拒绝意见" : custom && needsHuman ? "待现场核验" : needsHuman ? "待人工核验 / 审批" : "无需审批";
  const riskName: Record<string, string> = { normal: "正常", blue: "蓝色预警", yellow: "黄色预警", orange: "橙色预警", red: "红色预警" };
  return (
    <section
      className="workflow-result-panel"
      style={{
        background: "rgba(10,15,24,0.96)",
        border: "1px solid var(--line)",
        borderRadius: 14,
        padding: "20px 22px",
        margin: "16px 0"
      }}
    >
      {/* 标题栏 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18, flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: "monospace",
            fontSize: 12,
            letterSpacing: "0.1em",
            color: "var(--cyan)",
            padding: "4px 10px",
            border: "1px solid var(--line)",
            borderRadius: 6,
            background: "var(--surface)"
          }}
        >
          WORKFLOW RESULT
        </span>
        <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
          六 Agent 处置结果
          <span
            style={{
              marginLeft: 10,
              fontSize: 12,
              fontWeight: 400,
              color: "var(--muted)"
            }}
          >
            {result.scenario_id} · {result.iteration_count}/{result.max_iterations} 迭代
          </span>
        </h3>
      </div>

      {/* 调度员首屏结论：先给出状态与下一步，再展开六 Agent 证据。 */}
      <div className={`decision-summary decision-summary--${riskTone}`}>
        <div className="decision-summary__headline">
          <div>
            <span className="decision-summary__eyebrow">{custom ? "实际数据 · 按本次参数与资料计算" : "预设场景演示结果"}</span>
            <h4>当前初评：{riskName[riskLabel] || riskLabel}</h4>
            <p>{perception?.summary || "感知结果尚未返回，暂不能形成最终风险判断。"}</p>
          </div>
          <div className="decision-summary__score">
            <span>风险评分</span>
            <strong>{perception?.risk_score ?? "—"}</strong>
            <small>置信度{confidenceLabel(perception)}</small>
          </div>
        </div>
        <div className="decision-summary__grid">
          <div><span>建议与依据</span><strong>{plan?.name || plan?.plan_id || "本次未生成处置方案"}</strong><p>{nextAction}</p></div>
          <div><span>执行条件</span><strong>{feasibility}</strong><p>{resources?.blocking_issues?.[0] ? (typeof resources.blocking_issues[0] === "string" ? resources.blocking_issues[0] : resources.blocking_issues[0].description) : "未发现阻塞项"}</p></div>
          <div><span>研判与执行状态</span><strong>{approvalLabel}</strong><p>{custom ? "本处为分析完成时快照；在业务接入框核验方案、提交工单并查询最新回执" : needsHuman ? "待现场确认" : "模拟执行流程"}</p></div>
        </div>
      </div>

      {/* 六 Agent 结果卡片 */}
      {result.provenance&&<details className="result-provenance"><summary>本次输入、配置版本与资料追溯</summary>
        <p>运行 {result.run_id} · 配置 {result.provenance.configuration?.version||"未选分级配置"}</p>
        <p>输入快照 SHA256：{result.provenance.snapshot_id}</p>
        {result.provenance.documents.map(a=><p key={a.asset_id}>{a.title} · {a.version}<br/>SHA256：{a.sha256}</p>)}
        <pre className="monitoring-preview">{JSON.stringify(result.provenance.task,null,2)}</pre>
      </details>}
      {AGENT_CARDS.map(({ key, label, idx }) => {
        const data = result.agents[key];
        if (!data) return null;
        return <AgentResultCard key={key} idx={idx} label={label} data={data} />;
      })}

      {/* 审批决策摘要 */}
      {result.approval_decision ? (
        <div
          style={{
            marginTop: 12,
            background: "var(--surface)",
            border: "1px solid var(--line)",
            borderRadius: 10,
            padding: "12px 16px",
            fontSize: 13
          }}
        >
          <b style={{ fontFamily: "monospace", fontSize: 12, color: "var(--muted)", marginRight: 10 }}>
            审批决策
          </b>
          <span
            style={{
              fontWeight: 600,
              color:
                result.approval_decision.decision === "approved" ? "var(--green)" : "var(--red)"
            }}
          >
            {result.approval_decision.decision}
          </span>
          {result.approval_decision.approved_by
            ? ` · ${result.approval_decision.approved_by}`
            : ""}
          {result.approval_decision.note ? (
            <span style={{ color: "var(--muted)" }}> — {result.approval_decision.note}</span>
          ) : (
            ""
          )}
        </div>
      ) : null}

      {/* 知识写回摘要 */}
      {result.knowledge_writeback ? (
        <div
          style={{
            marginTop: 8,
            background: "var(--surface)",
            border: "1px solid var(--line)",
            borderRadius: 10,
            padding: "12px 16px",
            fontSize: 13
          }}
        >
          <b style={{ fontFamily: "monospace", fontSize: 12, color: "var(--muted)", marginRight: 10 }}>
            知识写回
          </b>
          <span
            style={{
              fontWeight: 600,
              color:
                result.knowledge_writeback.status === "skipped"
                  ? "var(--amber)"
                  : "var(--green)"
            }}
          >
            {result.knowledge_writeback.status}
          </span>
          {result.knowledge_writeback.data?.written !== undefined ? (
            <span> · written={result.knowledge_writeback.data.written}</span>
          ) : (
            ""
          )}
          {result.knowledge_writeback.data?.skipped &&
          result.knowledge_writeback.data.skipped.length > 0 ? (
            <span> · skipped={result.knowledge_writeback.data.skipped.length}（待人工审核）</span>
          ) : (
            ""
          )}
        </div>
      ) : null}
    </section>
  );
});
