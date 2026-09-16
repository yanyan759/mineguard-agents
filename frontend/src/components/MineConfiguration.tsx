import { useState } from "react";
import { Alert, Button, Checkbox, ConfigProvider, Input, InputNumber, Select } from "antd";
import { API_BASE_URL } from "../lib/config";
import { monitoringRequest } from "./MonitoringWorkbench";

type Rule = {metric:string;operator:string;threshold:number;level:string};
type Asset = {asset_id:string;title:string;category:string;version:string;sha256:string;used_in_runs:string[];parsed:{actions?:unknown[];requirements?:unknown[]}};
type Configuration = {mine_id:string;roadway_id:string;area:string;version:string;source:string;valid_from:string;valid_to:string;window_minutes:number;rules:Rule[];assets:string[];inventory_source:string;max_inventory_age_hours:number;recheck_minutes:number|null;approval_reference:string;plan_asset_id:string|null};
const initial:Configuration = {mine_id:"",roadway_id:"",area:"",version:"v1",source:"",valid_from:"",valid_to:"",window_minutes:60,rules:[],assets:[],inventory_source:"input",max_inventory_age_hours:24,recheck_minutes:null,approval_reference:"",plan_asset_id:null};
const categories:Record<string,string> = {resources:"资源台账",case:"历史案例",regulation:"规程",support:"支护 / 处置方案"};
const metrics:Record<string,string> = {max_energy_j:"最大能量（J）",event_count:"事件数",frequency_per_hour:"频次（次/h）",b_value:"b 值",missing_rate:"缺失率（0～1）",frequency_increase:"频次差",b_drop:"b 值下降量",high_energy_count:"高能事件数",spatial_cluster_fraction:"空间集中比例（0～1）"};

/** 后端持久化的测区配置；解析成功才列为可选资料，运行用量由服务端记录。 */
export function MineConfiguration({busy}:{threadId:string;busy?:boolean}) {
  const [config,setConfig]=useState<Configuration>(initial);
  const [assets,setAssets]=useState<Asset[]>([]);
  const [category,setCategory]=useState("resources");
  const [source,setSource]=useState("");
  const [version,setVersion]=useState("v1");
  const [observedAt,setObservedAt]=useState("");
  const [pending,setPending]=useState(false);
  const [error,setError]=useState("");
  const [status,setStatus]=useState("");
  const [preview,setPreview]=useState("");
  const [result,setResult]=useState("");
  const scope={mine_id:config.mine_id,roadway_id:config.roadway_id,area:config.area};
  const disabled=busy||pending;
  function update<K extends keyof Configuration>(key:K,value:Configuration[K]) {setConfig(old=>({...old,[key]:value}));setStatus("配置已编辑，请保存新版本后使用");setResult("");}
  async function perform(action:()=>Promise<void>) {setPending(true);setError("");try{await action();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setPending(false);}}
  async function load() {
    const data=await monitoringRequest("/api/configuration?"+new URLSearchParams(scope));setAssets(data.assets);
    if(data.configuration){const {revision,...saved}=data.configuration;void revision;setConfig(saved);setStatus(`已读取服务端版本 ${saved.version}`);}else setStatus("此测区尚未保存配置，请填写后保存");
  }
  async function upload(file:File) {
    const form=new FormData();form.set("metadata",JSON.stringify({...scope,category,title:file.name,version,source,observed_at:observedAt}));form.set("file",file);
    const response=await fetch(API_BASE_URL+"/api/configuration/ingest",{method:"POST",body:form});const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail));
    setAssets(old=>[{...data,used_in_runs:[]},...old.filter(a=>a.asset_id!==data.asset_id)]);
    setConfig(old=>({...old,assets:Array.from(new Set([...old.assets,data.asset_id]))}));
    setStatus(`${file.name}：已上传 → 已校验 → 已入库。保存配置版本后，本次选定资料才会用于后续任务。`);
  }
  return <details className="monitoring-workbench business-connections">
    <summary>矿方资料与参数配置 · 阈值、案例、支护与资源</summary>
    <ConfigProvider componentDisabled={disabled}>
    <p>按矿井、巷道和测区保存到后端。实际任务自动使用匹配测区的最新配置；任务 JSON 可用 configuration_version 指定旧版本。已启动任务保留原快照。</p>
    <div className="config-grid">{(["mine_id","roadway_id","area"] as const).map((key,i)=><label key={key}>{["矿井编号","巷道编号","测区 / 工作面"][i]}<Input aria-label={`配置${key}`} value={config[key]} disabled={disabled} onChange={e=>{setConfig({...initial,...scope,[key]:e.target.value});setAssets([]);setStatus("测区已切换，请读取对应配置或新建");setResult("");}}/></label>)}</div>
    <Button disabled={disabled||!Object.values(scope).every(Boolean)} onClick={()=>void perform(load)}>读取本测区配置与资料</Button>
    <h4>1. 上传并校验资料</h4>
    <p>台账使用 JSON / CSV；案例、规程和方案支持 JSON、TXT、MD、文字 PDF、DOCX。用于动作决策与资源核算的方案需包含 actions 和 requirements。扫描件请先 OCR 校对。</p>
    <div className="config-grid">
      <label>资料类别<Select aria-label="资料类别" value={category} onChange={setCategory} options={Object.entries(categories).map(([value,label])=>({value,label}))}/></label>
      <label>资料版本<Input aria-label="资料版本" value={version} onChange={e=>setVersion(e.target.value)}/></label>
      <label>资料来源 / 批准编号<Input aria-label="资料来源" value={source} onChange={e=>setSource(e.target.value)}/></label>
      <label>资料 / 库存时间（带时区）<Input aria-label="资料时间" value={observedAt} placeholder="2026-09-16T08:00:00+08:00" onChange={e=>setObservedAt(e.target.value)}/></label>
    </div>
    <p><a href={`${API_BASE_URL}/api/configuration/templates/resources`}>下载资源模板</a> · <a href={`${API_BASE_URL}/api/configuration/templates/support`}>下载方案模板</a> · <a href={`${API_BASE_URL}/api/configuration/templates/case`}>下载知识资料模板</a></p>
    <label className="upload-dropzone">上传并解析所选类别<input aria-label="上传矿方资料" type="file" accept={category==="resources"?".json,.csv":".json,.txt,.md,.pdf,.docx"} disabled={disabled||!source||!observedAt||!Object.values(scope).every(Boolean)} onChange={e=>{const file=e.target.files?.[0];if(file)void perform(()=>upload(file));e.target.value="";}}/></label>
    <div className="configuration-assets">{assets.map(asset=><div key={asset.asset_id}>
      <Checkbox checked={config.assets.includes(asset.asset_id)} onChange={e=>update("assets",e.target.checked?[...config.assets,asset.asset_id]:config.assets.filter(id=>id!==asset.asset_id))}>{categories[asset.category]} · {asset.title} · {asset.version}</Checkbox>
      <small>已上传 · 已校验 · 已入库 · {asset.used_in_runs.length?`已用于 ${asset.used_in_runs.length} 次运行`:"尚未用于运行"}</small>
      <details><summary>校验与使用记录</summary><p>SHA256：{asset.sha256}</p><p>任务：{asset.used_in_runs.join("、")||"配置保存并运行后记录"}</p></details>
    </div>)}</div>
    <h4>2. 参数版本与适用范围</h4>
    <div className="config-grid">
      {(["version","source","valid_from","valid_to","approval_reference"] as const).map((key,i)=><label key={key}>{["配置版本","参数来源","有效期开始（带时区）","有效期结束（带时区）","批准依据编号"][i]}<Input aria-label={`参数${key}`} value={config[key]} onChange={e=>update(key,e.target.value)}/></label>)}
      <label>统计窗口（分钟）<InputNumber aria-label="配置统计窗口" min={1} value={config.window_minutes} onChange={v=>update("window_minutes",v??60)}/></label>
      <label>库存允许时效（小时）<InputNumber aria-label="库存时效" min={0.1} value={config.max_inventory_age_hours} onChange={v=>update("max_inventory_age_hours",v??24)}/></label>
      <label>复查周期（分钟，可留空）<InputNumber aria-label="配置复查周期" min={1} value={config.recheck_minutes} onChange={v=>update("recheck_minutes",v)}/></label>
      <label>库存来源<Select aria-label="配置库存来源" value={config.inventory_source} onChange={v=>update("inventory_source",v)} options={[{value:"input",label:"上传台账 / 本次输入"},{value:"business",label:"业务库存接口"},{value:"mysql",label:"已配置 MySQL 库存"}]}/></label>
      <label>选定适用方案<Select aria-label="选定执行方案" allowClear value={config.plan_asset_id} onChange={v=>update("plan_asset_id",v??null)} options={assets.filter(a=>a.category==="support"&&config.assets.includes(a.asset_id)&&a.parsed.actions?.length&&a.parsed.requirements?.length).map(a=>({value:a.asset_id,label:a.title}))}/></label>
    </div>
    <p>按命中最高等级计算，规则留空仅统计、不定级。参数必须适用本测区；确定性规则不产生概率置信度。</p>
    {config.rules.map((rule,index)=><div className="config-rule" key={index}>
      <Select aria-label={`规则${index+1}指标`} value={rule.metric} options={Object.entries(metrics).map(([value,label])=>({value,label}))} onChange={v=>update("rules",config.rules.map((r,i)=>i===index?{...r,metric:v}:r))}/>
      <Select aria-label={`规则${index+1}比较`} value={rule.operator} options={[{value:">=",label:"≥"},{value:"<=",label:"≤"}]} onChange={v=>update("rules",config.rules.map((r,i)=>i===index?{...r,operator:v}:r))}/>
      <InputNumber aria-label={`规则${index+1}阈值`} value={rule.threshold} min={0} onChange={v=>update("rules",config.rules.map((r,i)=>i===index?{...r,threshold:v??0}:r))}/>
      <Select aria-label={`规则${index+1}等级`} value={rule.level} options={[{value:"blue",label:"蓝"},{value:"yellow",label:"黄"},{value:"orange",label:"橙"},{value:"red",label:"红"}]} onChange={v=>update("rules",config.rules.map((r,i)=>i===index?{...r,level:v}:r))}/>
      <Button onClick={()=>update("rules",config.rules.filter((_,i)=>i!==index))}>移除</Button>
    </div>)}
    <Button disabled={disabled} onClick={()=>update("rules",[...config.rules,{metric:"max_energy_j",operator:">=",threshold:0,level:"yellow"}])}>添加规则（填写矿方值）</Button>
    <Button type="primary" disabled={disabled} onClick={()=>void perform(async()=>{const saved=await monitoringRequest("/api/configuration",config);setStatus(`服务端已保存 ${saved.version}，下次实际任务按测区读取此版本。修改内容请使用新版本号。`);})}>保存配置到后端</Button>
    <details><summary>3. 预检实际任务将使用什么</summary>
      <Input.TextArea aria-label="配置预检输入" value={preview} rows={5} onChange={e=>setPreview(e.target.value)} placeholder="实际监测 JSON 或完整摘要"/>
      <Button disabled={disabled||!preview.trim()} onClick={()=>void perform(async()=>{const data=await monitoringRequest("/api/configuration/prepare",{query:preview});setResult(JSON.stringify({snapshot_id:data.snapshot.snapshot_id,version:data.snapshot.configuration?.version,assessment:data.snapshot.assessment,resources:data.task.resources,plan:data.snapshot.plan?.title},null,2));})}>核验参数、资料与资源</Button>
      {result&&<pre className="monitoring-preview">{result}</pre>}
    </details>
    {status&&<p role="status">{status}</p>}{error&&<Alert type="error" showIcon message={error}/>}
    </ConfigProvider>
  </details>;
}
