import {
  AlertOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileSearchOutlined,
  SyncOutlined,
  ToolOutlined,
  UndoOutlined
} from "@ant-design/icons";
import { Empty, Tag } from "antd";
import { AGENT_NODE_META, RISK_LEVEL_META } from "../types";
import type { AgentNodeName, MonitorMessage, RiskLevel } from "../types";

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "--:--:--";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}

/** 六 Agent 事件图标，传入 Agent 主题色 */
function EventIcon({ event, color }: { event: string; color?: string }) {
  if (event === "agent_start") {
    return <BranchesOutlined style={{ color }} aria-hidden />;
  }
  if (event === "agent_complete") {
    return <CheckCircleOutlined style={{ color }} aria-hidden />;
  }
  if (event === "agent_failed") {
    return <CloseCircleOutlined style={{ color }} aria-hidden />;
  }
  if (event === "agent_rollback") {
    return <UndoOutlined style={{ color }} aria-hidden />;
  }
  if (event === "approval_required" || event === "alert_triggered") {
    return <AlertOutlined style={{ color }} aria-hidden />;
  }
  if (event === "execution_feedback") {
    return <SyncOutlined style={{ color }} aria-hidden />;
  }
  if (event === "workflow_start") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "workflow_complete") {
    return <CheckCircleOutlined aria-hidden />;
  }
  if (event === "workflow_failed") {
    return <CloseCircleOutlined aria-hidden />;
  }
  if (event === "assistant_call") {
    return <BranchesOutlined aria-hidden />;
  }
  if (event === "tool_start") {
    return <ToolOutlined aria-hidden />;
  }
  if (event === "session_created") {
    return <FileSearchOutlined aria-hidden />;
  }
  return <ClockCircleOutlined aria-hidden />;
}

/** 从事件 data 提取节点名与对应 Agent 主题色 */
function resolveAgent(event: MonitorMessage): { node?: AgentNodeName; color?: string } {
  const node = event.data?.node as AgentNodeName | undefined;
  if (node && AGENT_NODE_META[node]) {
    return { node, color: AGENT_NODE_META[node].color };
  }
  return { node, color: undefined };
}

interface EventStreamProps {
  events: MonitorMessage[];
}

/**
 * 六 Agent 实时执行轨迹
 * 节点级事件流：按 Agent 着色/图标区分，展示 agent_name、workflow_run_id、
 * 风险等级、置信度、证据来源与执行耗时。
 */
export function EventStream({ events }: EventStreamProps) {
  return (
    <section className="console-panel event-panel" aria-labelledby="event-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">LIVE TRACE</span>
          <h2 id="event-title">六 Agent 实时轨迹</h2>
        </div>
        <span className="event-count">{events.length}</span>
      </div>

      {events.length === 0 ? (
        <div className="empty-console">
          <Empty
            description="等待 WebSocket 推送六 Agent 执行事件"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <ol className="event-stream">
          {events.map((event, index) => {
            const { node, color } = resolveAgent(event);
            const risk = event.data?.risk_level as RiskLevel | undefined;
            const confidence = event.data?.confidence;
            const durationMs = event.data?.duration_ms;
            const evidence = event.data?.evidence as
              | Array<{ source_type?: string; description?: string }>
              | undefined;
            return (
              <li
                className={`event-row event-row--${event.event}`}
                key={`${event.timestamp}-${index}`}
              >
                <div className="event-icon" style={{ background: color ?? undefined }}>
                  <EventIcon event={event.event} color={color} />
                </div>
                <div className="event-body">
                  <div className="event-meta">
                    <span className="event-agent" style={{ color }}>
                      {event.agent_name ||
                        (node ? AGENT_NODE_META[node].name : event.event)}
                    </span>
                    <time dateTime={event.timestamp}>{formatTime(event.timestamp)}</time>
                  </div>
                  <p>{event.message}</p>

                  {/* 置信度 / 耗时 / 风险标签 / 证据来源 */}
                  <div className="event-chips">
                    {risk && RISK_LEVEL_META[risk] ? (
                      <Tag color={RISK_LEVEL_META[risk].color}>
                        {RISK_LEVEL_META[risk].label}
                      </Tag>
                    ) : null}
                    {node && event.data?.evaluation_basis ? (
                      <span className="event-chip">{event.data.evaluation_basis === "simulation" ? "演示流程" : typeof confidence === "number" && event.data.evaluation_basis === "calibrated" ? `置信度 ${confidence.toFixed(2)}` : event.data.evaluation_basis === "actual_snapshot" ? "实际输入计算 / 资料引用" : "记录与证据核验"}</span>
                    ) : null}
                    {typeof durationMs === "number" ? (
                      <span className="event-chip">耗时 {durationMs}ms</span>
                    ) : null}
                  </div>

                  {Array.isArray(evidence) && evidence.length > 0 ? (
                    <div className="event-evidence">
                      <span>证据来源：</span>
                      {evidence.map((item, itemIndex) => (
                        <Tag key={`${item.source_type}-${itemIndex}`} bordered={false}>
                          {item.source_type}
                          {item.description ? ` · ${item.description}` : ""}
                        </Tag>
                      ))}
                    </div>
                  ) : null}

                  {event.workflow_run_id ? (
                    <code className="event-run">run: {event.workflow_run_id}</code>
                  ) : null}

                  {Object.keys(event.data).length > 0 ? (
                    <pre>{JSON.stringify(event.data, null, 2)}</pre>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
