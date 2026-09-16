import { useRef, useState } from "react";
import { Alert, Button, Input } from "antd";
import { monitoringRequest } from "./MonitoringWorkbench";

/** 新观测复用现有六Agent入口；人工报告与已验证执行回执分别标识。 */
export function ObservationFeedback({ runId, busy, onStart }: {
  runId: string; busy: boolean; onStart: (task: Record<string, unknown>, runId?: string) => Promise<void>;
}) {
  const [observation, setObservation] = useState("");
  const [reportedBy, setReportedBy] = useState("");
  const [actions, setActions] = useState("[]");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [pending, setPending] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  function edited() { requestId.current = crypto.randomUUID(); }
  return <details className="monitoring-workbench observation-feedback">
    <summary>补交执行反馈与新观测 · 再次运行六 Agent</summary>
    <p>关联原任务 {runId}。支持原始记录或监测摘要 JSON，需与原任务使用同一格式、矿井、测区和来源；摘要必须提供 window_end。新窗口应晚于原记录。</p>
    <Input aria-label="反馈记录人" placeholder="反馈记录人" value={reportedBy} onChange={e => { setReportedBy(e.target.value); edited(); }} />
    <textarea aria-label="后续原始监测JSON" rows={7} value={observation} placeholder="粘贴后续真实观测任务JSON（与原始记录入口相同格式）"
      onChange={e => { setObservation(e.target.value); edited(); }} />
    <details><summary>动作执行报告（没有回执时保留空数组）</summary>
      <p>每项填写sequence、status（completed/failed/not_executed）、occurred_at（带时区）。sequence对应原处置建议中的动作序号。</p>
      <textarea aria-label="动作执行报告" rows={3} value={actions} onChange={e => { setActions(e.target.value); edited(); }} />
    </details>
    {error && <Alert type="error" message={error} showIcon />}
    {saved && <p role="status">{saved}</p>}
    <Button type="primary" disabled={busy || !observation.trim() || !reportedBy.trim()} loading={pending} onClick={async () => {
      setPending(true); setError("");
      try {
        const data = await monitoringRequest(`/api/monitoring/runs/${encodeURIComponent(runId)}/feedback`, {
          request_id: requestId.current, reported_by: reportedBy, actions: JSON.parse(actions), observation: JSON.parse(observation)
        });
        setSaved(`反馈已保存：${data.feedback_id}；关联复核运行 ${data.child_run_id}。启动失败时可用同一内容重试。`);
        await onStart(data.task, data.child_run_id);
      } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
      finally { setPending(false); }
    }}>保存反馈并启动六 Agent 复核</Button>
  </details>;
}
