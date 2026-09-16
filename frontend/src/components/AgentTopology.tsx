import { AlertOutlined, ArrowDownOutlined, ArrowRightOutlined, BookOutlined, NotificationOutlined, SolutionOutlined, SyncOutlined, TeamOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";
import { AGENT_NODE_META } from "../types";
import type { AgentNodeName, MonitorMessage } from "../types";

const NODE_FLOW: AgentNodeName[] = ["perception_warning", "knowledge_retrieval", "dispatch_decision", "resource_evaluation", "collaborative_control", "reflection_iteration"];
const NODE_ICONS: Record<AgentNodeName, ReactNode> = {
  perception_warning: <AlertOutlined aria-hidden />, knowledge_retrieval: <BookOutlined aria-hidden />, dispatch_decision: <SolutionOutlined aria-hidden />,
  resource_evaluation: <TeamOutlined aria-hidden />, collaborative_control: <NotificationOutlined aria-hidden />, reflection_iteration: <SyncOutlined aria-hidden />,
};
const NODE_DESCRIPTIONS: Record<AgentNodeName, string> = {
  perception_warning: "数据质量 → 风险评级", knowledge_retrieval: "规程 / 案例 / 支护方案", dispatch_decision: "分级处置方案",
  resource_evaluation: "人员 / 物资 / 设备核算", collaborative_control: "推送 / 工单 / 审批", reflection_iteration: "复盘 → 结束或回退",
};

interface AgentTopologyProps { currentNode?: AgentNodeName | ""; events?: MonitorMessage[]; }

function completedNodes(events: MonitorMessage[]): Set<AgentNodeName> {
  const completed = new Set<AgentNodeName>();
  for (const event of events) {
    if (event.event !== "agent_complete") continue;
    const node = event.data.node;
    if (typeof node === "string" && NODE_FLOW.includes(node as AgentNodeName)) completed.add(node as AgentNodeName);
  }
  return completed;
}

function rollbackLabel(events: MonitorMessage[]): string | null {
  const event = [...events].reverse().find((item) => item.event === "agent_rollback");
  if (!event) return null;
  const from = typeof event.data.from_agent === "string" ? event.data.from_agent : "反思迭代";
  const to = typeof event.data.to_agent === "string" ? event.data.to_agent : "上游节点";
  return `${from} ⇢ ${to}`;
}

export function AgentTopology({ currentNode = "", events = [] }: AgentTopologyProps) {
  const completed = completedNodes(events);
  const rollback = rollbackLabel(events);
  const hasEvents = events.length > 0;
  const actual = [...events].reverse().find(event=>event.event === "workflow_start")?.data?.scenario_id === "custom";
  return (
    <section className="console-panel topology-panel" aria-labelledby="topology-title">
      <div className="panel-heading"><div><span className="panel-kicker">SIX-AGENT WORKFLOW</span><h2 id="topology-title">六智能体处置链路</h2></div><span className="topology-legend"><i className="legend-line" aria-hidden /> 主流程 <i className="legend-line legend-line--dashed" aria-hidden /> 条件回退</span></div>
      <div className="agent-flow" aria-label="与后端状态机一致的六智能体流程">
        {NODE_FLOW.map((node, index) => {
          const meta = AGENT_NODE_META[node]; const active = currentNode === node; const done = completed.has(node);
          return <div className="agent-flow-item" key={node}>{index > 0 ? <div className="agent-flow-link" aria-hidden><ArrowRightOutlined /></div> : null}<div className={`agent-flow-node${active ? " agent-flow-node--active" : ""}${done ? " agent-flow-node--done" : ""}`} style={{ "--node-color": meta.color } as React.CSSProperties}><div className="agent-flow-node-icon">{NODE_ICONS[node]}</div><strong>{meta.short}</strong><p>{NODE_DESCRIPTIONS[node]}</p><small>{active ? "执行中" : done ? "已完成" : hasEvents ? "待执行" : "等待输入"}</small></div></div>;
        })}
      </div>
      <div className="topology-branches">
        {actual ? <>
          <div className="topology-branch"><span><b>实际数据</b> 按配置完成六节点计算，未触发分级也继续核对资料与资源</span></div>
          <div className="topology-branch"><span><b>资源存在缺口</b> 保留核算结果；补充资源或更换方案后提交新运行</span></div>
          <div className="topology-branch"><span><b>执行与复核</b> 业务框实名核验及派工 → 鉴权回执 → 后续观测复核运行</span></div>
        </> : <>
        <div className="topology-branch topology-branch--normal"><ArrowDownOutlined aria-hidden /><span><b>正常分支</b> 感知预警 → 结束</span></div>
        <div className="topology-branch topology-branch--resource"><ArrowDownOutlined aria-hidden /><span><b>资源不可行</b> 资源评估 ⇢ 调度决策重新编排</span></div>
        <div className="topology-branch topology-branch--reflection"><ArrowDownOutlined aria-hidden /><span><b>反思定向回退</b> 可返回感知、知识、调度、资源或管控节点{rollback ? ` · 最近：${rollback}` : ""}</span></div>
        </>}
      </div>
    </section>
  );
}
