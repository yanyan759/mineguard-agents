import { useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Input, Select } from "antd";
import { API_BASE_URL } from "../lib/config";

const names: Record<string, string> = { identity:"身份服务", inventory:"库存服务", notifications:"通知通道", work_orders:"工单通道", receipts:"回执通道" };
const statuses: Record<string, string> = { not_configured:"未配置", connected:"健康接口可连接 · 待现场联调", failed:"连接失败", unavailable:"服务未提供", accepted:"对方已接收，尚无送达/执行回执", delivery_unknown:"发送结果未知，请核对对方记录；相同请求不会重发", delivered:"通知已送达", executed:"收到动作执行回执", rejected:"对方拒绝", recorded:"已记录" };

/** 接入状态与显式发送独立展示；凭据只保存在当前组件内存。 */
export function BusinessConnections({ runId }: { runId?: string }) {
  const [services, setServices] = useState<Record<string,{status:string;detail:string}>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");
  const [identity, setIdentity] = useState("");
  const [recipient, setRecipient] = useState("");
  const [sent, setSent] = useState<{request_id:string;status:string;run_id:string} | null>(null);
  const [business,setBusiness]=useState<{snapshot_id:string;quantity_check:string;plan?:{asset_id:string;title:string;parsed:{actions:{sequence:number;action:string}[]}};approval?:{approved_by:string;verified_at:string};requests:{request_id:string;status:string;kind:string;action_sequence?:number}[]} | null>(null);
  const [checks,setChecks]=useState({personnel_qualified:false,equipment_ready:false,inventory_confirmed:false,site_conditions_confirmed:false});
  const [reference,setReference]=useState("");
  const [sequence,setSequence]=useState<number>();
  useEffect(()=>{setBusiness(null);setSent(null);setSequence(undefined);setReference("");setChecks({personnel_qualified:false,equipment_ready:false,inventory_confirmed:false,site_conditions_confirmed:false});},[runId]);
  const request = useRef({key:"",id:crypto.randomUUID()});
  async function api(path: string, body?: unknown) {
    const response = await fetch(API_BASE_URL+path, {method:body === undefined ? "GET":"POST",
      headers:{"Content-Type":"application/json", ...(token ? {Authorization:`Bearer ${token}`} : {})}, body:body===undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(typeof data.detail === "string" ? data.detail : data.error || "业务接口返回错误");
    return data;
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true);setError("");
    try { await action(); } catch(error) {setError(error instanceof Error ? error.message:String(error));}
    finally {setBusy(false);}
  }
  async function refresh() { const data = await api("/api/business/status"); setServices(data.services); }
  return <details className="monitoring-workbench business-connections" onToggle={event => {
    if(event.currentTarget.open && !Object.keys(services).length && !busy) void perform(refresh);
  }}>
    <summary>业务接入状态 · 身份、库存、通知与回执</summary>
    <p>接口可连接不代表现场数据已经核验。六 Agent 生成建议后，外部通知须单独提交；本页面不会自动下发生产指令。</p>
    <Button disabled={busy} onClick={() => void perform(refresh)}>检查接入状态</Button>
    {Object.entries(services).map(([key,value]) => <p key={key}><b>{names[key] || key}：</b>{statuses[value.status] || value.status}<br/><small>{value.detail}</small></p>)}
    <details><summary>已接入业务服务：验证个人身份与发送核验建议</summary>
      <p>填写身份提供方签发的个人凭据，仅用于当前操作，不保存到浏览器。</p>
      <Input.Password aria-label="业务个人凭据" autoComplete="off" value={token} onChange={e => {setToken(e.target.value);setIdentity("");}} />
      <Button disabled={busy || !token} onClick={() => void perform(async () => {
        const user = await api("/api/business/identity"); setIdentity(`已验证用户 ${user.user_id}，岗位 ${user.roles.join("、")}，矿井 ${user.mine_ids.join("、")}`);
      })}>验证身份</Button>
      {identity && <p>{identity}</p>}
      <p>当前已完成任务：{runId || "尚无可发送的自定义结果"}。只发送该任务的核验建议摘要。</p>
      <Input aria-label="通知目标" placeholder="业务系统中的准确接收目标编号" value={recipient} onChange={e => setRecipient(e.target.value)} />
      <Button disabled={busy || !token || !recipient || !runId || services.notifications?.status !== "connected"} onClick={() => void perform(async () => {
        const key = `${runId}:${recipient}`;
        if(request.current.key !== key) request.current = {key,id:crypto.randomUUID()};
        setSent(await api(`/api/business/runs/${encodeURIComponent(runId!)}/notify`,{request_id:request.current.id,recipient}));
      })}>发送当前核验建议到指定目标</Button>
      {sent && <><p>任务 {sent.run_id} · 请求 {sent.request_id}<br/>{statuses[sent.status] || sent.status}</p>
        <Button disabled={busy || !token} onClick={() => void perform(async () => setSent(await api(`/api/business/requests/${encodeURIComponent(sent.request_id)}`)))}>查询送达回执</Button></>}
    </details>
    <details><summary>现场方案核验、工单与执行回执</summary>
      <p>使用上方个人凭据。现场核验独立保存，不覆盖原始结果。完整方案、数量满足及本矿权限均通过后，可提交指定动作。</p>
      <Button disabled={busy||!token||!runId} onClick={()=>void perform(async()=>setBusiness(await api(`/api/business/runs/${encodeURIComponent(runId!)}`)))}>读取本次方案与执行状态</Button>
      {business&&<>
        <p>{business.plan?.title||"本任务未选择结构化方案"} · 资源数量：{business.quantity_check==="sufficient"?"已满足方案需求":business.quantity_check==="insufficient"?"存在实际缺口":"待配置"}</p>
        {business.plan&&<>
          <Input aria-label="现场核验依据" placeholder="现场核验记录 / 审批文件编号或资料地址" value={reference} onChange={e=>setReference(e.target.value)}/>
          {Object.entries({personnel_qualified:"已核验人员资质",equipment_ready:"已核验设备状态",inventory_confirmed:"已核验现场库存",site_conditions_confirmed:"已核验方案适用性与现场条件"}).map(([key,label])=><p key={key}><Checkbox checked={checks[key as keyof typeof checks]} onChange={e=>setChecks({...checks,[key]:e.target.checked})}>{label}</Checkbox></p>)}
          <Button disabled={busy||!token||!runId||reference.trim().length<5||!Object.values(checks).every(Boolean)||business.quantity_check!=="sufficient"} onClick={()=>void perform(async()=>{
            await api(`/api/business/runs/${encodeURIComponent(runId!)}/verify-plan`,{...checks,evidence_reference:reference,plan_asset_id:business.plan!.asset_id,snapshot_id:business.snapshot_id});
            setBusiness(await api(`/api/business/runs/${encodeURIComponent(runId!)}`));
          })}>实名核验并批准本次方案</Button>
          {business.approval&&<p role="status">现场条件已核验 · 批准人 {business.approval.approved_by} · {business.approval.verified_at}</p>}
          <Select aria-label="工单动作" placeholder="选择要提交的动作" value={sequence} onChange={setSequence} options={business.plan.parsed.actions.map(a=>({value:a.sequence,label:`${a.sequence}. ${a.action}`}))}/>
          <p>工单目标使用上方“通知目标”的对象编号：{recipient||"尚未填写"}</p>
          <Button disabled={busy||!business.approval||!token||!recipient||!sequence||!runId} onClick={()=>void perform(async()=>{
            const key=`order:${runId}:${recipient}:${sequence}`;if(request.current.key!==key)request.current={key,id:crypto.randomUUID()};
            setSent(await api(`/api/business/runs/${encodeURIComponent(runId!)}/work-order`,{request_id:request.current.id,recipient,action_sequence:sequence}));
            setBusiness(await api(`/api/business/runs/${encodeURIComponent(runId!)}`));
          })}>提交选定动作工单</Button>
        </>}
        {business.requests.map(r=><p key={r.request_id}>{r.kind==="work_order"?`工单动作 ${r.action_sequence}`:"通知"} · {r.request_id}<br/>{statuses[r.status]||r.status}</p>)}
      </>}
    </details>
    {error && <Alert type="error" message={error} showIcon />}
  </details>;
}
