import { useRef, useState } from "react";
import { Alert, Button, Select } from "antd";
import { API_BASE_URL } from "../lib/config";
import { uploadSessionFiles } from "../lib/api";

export async function monitoringRequest(path: string, body?: unknown) {
  const response = await fetch(`${API_BASE_URL}${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : Array.isArray(data.detail)
    ? data.detail.map((item: {loc?: Array<string | number>; msg?: string}) => `${item.loc?.join(".") || "输入"}：${item.msg || "格式错误"}`).join("；")
    : "输入校验请求失败");
  return data;
}

type Props = {
  threadId: string;
  filenames: string[];
  busy: boolean;
  onStart: (task: Record<string, unknown>) => Promise<void>;
};

/** 选择、校验、提交使用同一个任务对象，原始记录进入现有六Agent会话。 */
export function MonitoringWorkbench({ threadId, filenames, busy, onStart }: Props) {
  const panel = useRef<HTMLDetailsElement>(null);
  const [text, setText] = useState("");
  const [metadata, setMetadata] = useState("");
  const [filename, setFilename] = useState<string>();
  const [localNames, setLocalNames] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);

  async function operation(action: () => Promise<void>) {
    setPending(true); setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setPending(false); }
  }
  async function prepare(name: string) {
    const meta = name.toLowerCase().endsWith(".csv") ? JSON.parse(metadata || "{}") : {};
    const result = await monitoringRequest("/api/monitoring/prepare", { thread_id: threadId, filename: name, metadata: meta });
    setText(JSON.stringify(result.task, null, 2)); setPreview(result.assessment);
    setStatus(`已校验并选为本次输入：${name}。点击下方按钮启动六Agent。`);
  }
  return <details className="monitoring-workbench" ref={panel}>
    <summary>原始监测记录 · 上传、校验并交给六 Agent</summary>
    <p>JSON需包含矿井、测区、来源、窗口和events；CSV需同时填写元数据。b值、缺失率等字段可在后续补充。</p>
    <div className="monitoring-actions">
      <label>上传JSON/CSV <input aria-label="上传原始记录" type="file" accept=".json,.csv" disabled={pending || busy} onChange={e => {
        const file = e.target.files?.[0]; if (!file) return;
        void operation(async () => {
          await uploadSessionFiles([file], threadId); setFilename(file.name);
          setLocalNames(names => Array.from(new Set([...names, file.name])));
          setStatus(`已上传：${file.name}，尚未参与计算`);
          if (file.name.toLowerCase().endsWith(".json")) await prepare(file.name);
        }); e.target.value = "";
      }} /></label>
      <Select aria-label="选择当前会话文件" placeholder="选择当前会话文件" value={filename} onChange={setFilename}
        style={{ minWidth: 200, maxWidth: "100%" }} options={Array.from(new Set([...filenames, ...localNames])).map(name => ({ value: name, label: name }))} />
      <Button disabled={!filename || pending || busy} onClick={() => void operation(() => prepare(filename!))}>读取并校验所选文件</Button>
    </div>
    <details><summary>CSV元数据（JSON，不含events）</summary>
      <textarea aria-label="CSV元数据" value={metadata} onChange={e => setMetadata(e.target.value)} rows={5}
        placeholder={'{"input_type":"raw_events","mine_id":"实际矿井编号","roadway_id":"实际巷道编号","area":"实际测区","source":"监测系统名称","method_id":"statistics-v1","window_start":"带时区开始时间","window_end":"带时区结束时间"}'} />
      <p>CSV列：event_id,timestamp,energy,energy_unit，可选x,y,z。能量单位支持J/kJ。</p>
    </details>
    <div className="monitoring-actions">
      <Button disabled={pending || busy} onClick={() => void operation(async () => {
        const data = await monitoringRequest("/api/monitoring/samples/1");
        setText(JSON.stringify(data.task, null, 2)); setPreview(null); setStatus("已载入NIOSH真实历史记录，尚未启动；历史标签不进入输入。");
      })}>载入真实样本1</Button>
      <Button disabled={pending || busy} onClick={() => void operation(async () => {
        const data = await monitoringRequest("/api/monitoring/samples/2");
        setText(JSON.stringify(data.task, null, 2)); setPreview(null); setStatus("已载入NIOSH研究报警样本2，提交后进入六Agent研判流程。");
      })}>载入研究报警样本2</Button>
      <Button disabled={!text || pending || busy} onClick={() => {
        try { const task = JSON.parse(text); task.method_id = "statistics-v1"; setText(JSON.stringify(task, null, 2)); setPreview(null); }
        catch { setError("请先提供合法JSON"); }
      }}>切换为只统计、不定级</Button>
    </div>
    <textarea aria-label="原始监测任务JSON" value={text} onChange={e => { setText(e.target.value); setPreview(null); setStatus("输入已编辑，提交前将重新校验"); }} rows={8} placeholder="粘贴带元数据的原始任务JSON，或上传文件/载入真实样本" />
    {status && <p role="status">{status}</p>}
    {error && <Alert type="error" showIcon message={error} />}
    {preview && <pre className="monitoring-preview">{JSON.stringify(preview, null, 2)}</pre>}
    <Button type="primary" loading={pending} disabled={busy || !text.trim()} onClick={() => void operation(async () => {
      const task = JSON.parse(text) as Record<string, unknown>;
      const data = await monitoringRequest("/api/monitoring/validate", task);
      setPreview(data.assessment);
      await onStart(data.task); setStatus("原始记录已提交到当前会话的六Agent工作流，请查看轨迹和结果。");
      if (panel.current) panel.current.open = false;
    })}>校验并送入六 Agent</Button>
  </details>;
}
