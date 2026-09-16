"""通过本机Chrome真实页面验证上传、六节点、审批、复核及滚动；不调用外部通知。"""
import base64
import json
import sys
import time
from pathlib import Path
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/validation/browser-monitoring"
OUT.mkdir(parents=True, exist_ok=True)
page = next(x for x in requests.get("http://127.0.0.1:9222/json").json() if x.get("id") == "DAC294EB6A23E30C361AEA95475BFA38")
ws = websocket.create_connection(page["webSocketDebuggerUrl"], origin="http://localhost:9222", timeout=30)
seq = 0


def call(method, params=None):
    global seq
    seq += 1
    ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        data = json.loads(ws.recv())
        if data.get("id") == seq:
            if "error" in data: raise RuntimeError(data["error"])
            return data.get("result", {})


def ev(expression):
    result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
    if "exceptionDetails" in result: raise RuntimeError(result["exceptionDetails"])
    return result.get("result", {}).get("value")


def until(expression, timeout=30):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = ev(expression)
        if value: return value
        time.sleep(.2)
    raise AssertionError(f"页面等待超时: {expression}")


def click(text):
    point=ev(f"(()=>{{let e=[...document.querySelectorAll('button,summary')].find(e=>e.textContent.trim()==={json.dumps(text)}||e.getAttribute('aria-label')==={json.dumps(text)});if(!e||e.disabled)throw Error('按钮不可用');for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!(e.tagName==='SUMMARY'&&p===e.parentElement))p.open=true;e.scrollIntoView({{block:'center',behavior:'instant'}});let r=e.getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()")
    call("Input.dispatchMouseEvent",{"type":"mousePressed","button":"left","clickCount":1,**point})
    call("Input.dispatchMouseEvent",{"type":"mouseReleased","button":"left","clickCount":1,**point})


def fill(label, value):
    ev(f"(()=>{{let e=document.querySelector('[aria-label={json.dumps(label)}]');if(!e)throw Error('输入不存在');for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,{json.dumps(value)});e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")


def run(label, approval=False, button="校验并送入六 Agent", during_approval=None):
    previous = ev("window.__runs.length")
    click(button)
    item = until(f"window.__runs.length>{previous} && window.__runs.at(-1)")
    run_id = item["response"]["run_id"]
    if approval:
        until("!!document.querySelector('.approval-panel')")
        if during_approval: during_approval(run_id)
        ev("document.querySelector('.approval-panel').scrollIntoView({block:'center',behavior:'instant'})")
        time.sleep(.4)
        screenshot(label+"-approval")
        click("记录同意意见")
    until("document.querySelector('.workflow-result-panel') && document.querySelector('.workflow-result-panel').textContent.includes("+json.dumps(run_id)+")")
    status = requests.get(f"http://127.0.0.1:8000/api/workflow/{run_id}/status").json()
    result = requests.get(f"http://127.0.0.1:8000/api/workflow/{run_id}/result").json()
    assert status["status"] == "completed", status
    expected = ["perception_warning", "knowledge_retrieval", "dispatch_decision", "resource_evaluation", "collaborative_control", "reflection_iteration"]
    assert all(n in status["phases"] for n in expected), status["phases"]
    for node in expected:
        starts = [i for i,e in enumerate(status["events"]) if e["type"] == "agent_start" and e["data"].get("node")==node]
        ends = [i for i,e in enumerate(status["events"]) if e["type"] == "agent_complete" and e["data"].get("node")==node]
        assert starts and ends and min(starts)<max(ends), (node, starts, ends)
    (OUT/f"{label}.json").write_text(json.dumps({"request": item, "status": status, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(label, run_id, status["status"], status["phases"], flush=True)
    return result


def screenshot(name):
    image = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    (OUT/f"{name}.png").write_bytes(base64.b64decode(image["data"]))


def setup():
    call("Emulation.setDeviceMetricsOverride", {"width":1366,"height":768,"deviceScaleFactor":1,"mobile":False})
    call("Page.reload")
    time.sleep(1)
    until("!!document.querySelector('.monitoring-workbench')")
    ev("window.__runs=[];window.__feedback=[];window.__errors=[];window.addEventListener('error',e=>window.__errors.push(e.message));const f=window.fetch;window.fetch=async(...args)=>{if(window.__failNextStart&&String(args[0]).endsWith('/api/workflow/start')){window.__failNextStart=false;throw Error('browser-test: simulated request failure');}let r=await f(...args);if(String(args[0]).endsWith('/api/workflow/start'))window.__runs.push({body:JSON.parse(args[1].body),response:await r.clone().json()});if(String(args[0]).endsWith('/feedback'))window.__feedback.push(await r.clone().json());if(String(args[0]).endsWith('/status')&&(await r.clone().json()).status==='running')await new Promise(resolve=>setTimeout(resolve,800));return r;}")
    click("原始监测记录 · 上传、校验并交给六 Agent")


def upload(path):
    ev("for(let p=document.querySelector('input[aria-label=\"上传原始记录\"]');p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true")
    document = call("DOM.getDocument")
    node = call("DOM.querySelector", {"nodeId":document["root"]["nodeId"],"selector":"input[aria-label='上传原始记录']"})
    call("DOM.setFileInputFiles", {"nodeId":node["nodeId"],"files":[str(path.resolve())]})


def extended():
    import copy
    import csv
    import pandas as pd
    # 使用已核验的真实文件，通过页面文件控件上传；接口仍从浏览器发出。
    task = requests.get("http://127.0.0.1:8000/api/monitoring/samples/1").json()["task"]
    path = OUT/"real-monitoring.json"; path.write_text(json.dumps(task), encoding="utf-8")
    upload(path)
    until("document.body.innerText.includes('已校验并选为本次输入：real-monitoring.json')")
    uploaded_json = run("uploaded-json")
    assert uploaded_json["agents"]["perception_result"]["assessment"]["file_evidence"][0]["verified_records"]==6
    csv_path = OUT/"real-monitoring.csv"
    with csv_path.open("w",newline="",encoding="utf-8") as f:
        writer = csv.writer(f);writer.writerow(["event_id","timestamp","energy","energy_unit","x","y","z"])
        for event in task["events"]: writer.writerow([event["event_id"],event["timestamp"],event["energy"],event["energy_unit"],*event["coordinates"]])
    click("CSV元数据（JSON，不含events）")
    fill("CSV元数据",json.dumps({k:v for k,v in task.items() if k!="events"}))
    upload(csv_path)
    until("document.body.innerText.includes('已上传：real-monitoring.csv')")
    click("读取并校验所选文件")
    until("document.body.innerText.includes('已校验并选为本次输入：real-monitoring.csv')")
    uploaded_csv = run("uploaded-csv")
    assert uploaded_csv["agents"]["perception_result"]["assessment"]["metrics"] == uploaded_json["agents"]["perception_result"]["assessment"]["metrics"]
    # 从相同原始目录取之后一小时，原始记录不移时间、不改能量。
    events = pd.read_parquet(ROOT/"output/validation/elkcreek/filtered_events.parquet")
    start = pd.Timestamp(task["window_end"])+pd.Timedelta(hours=1); end=start+pd.Timedelta(hours=1)
    rows = events.loc[(events.time>=start)&(events.time<end)]
    later = copy.deepcopy(task);later.update(window_start=start.isoformat(),window_end=end.isoformat(),input_files=[])
    later["events"] = [{"event_id":f"{r.source_file}:{r.source_row}","timestamp":r.time.isoformat(),"energy":r.energy_total,"energy_unit":"J","coordinates":[r.x,r.y,r.z]} for r in rows.itertuples()]
    (OUT/"real-later-observation.json").write_text(json.dumps(later,ensure_ascii=False,indent=2),encoding="utf-8")
    click("补交执行反馈与新观测 · 再次运行六 Agent")
    fill("反馈记录人","浏览器核验：历史目录复核，无现场执行")
    fill("后续原始监测JSON",json.dumps(later))
    ev("window.__failNextStart=true")
    click("保存反馈并启动六 Agent 复核")
    until("document.querySelector('.observation-feedback')?.textContent.includes('browser-test: simulated request failure')")
    assert ev("document.querySelector('[aria-label=\"后续原始监测JSON\"]').value")==json.dumps(later)
    assessment=requests.post("http://127.0.0.1:8000/api/monitoring/validate",json=later).json()["assessment"]
    child=run("later-observation",approval=assessment["alarm"] is True,button="保存反馈并启动六 Agent 复核")
    saved=ev("window.__feedback")
    assert saved[-1]["child_run_id"]==saved[-2]["child_run_id"] and saved[-1]["created"] is False
    comparison=child["agents"]["reflection_result"]["evidence"][0]["value"]
    assert comparison["comparable"] and comparison["feedback"]["parent_run_id"]==uploaded_csv["run_id"]
    assert ev("document.querySelector('.workflow-result-panel').textContent.includes('关联观测复核')")
    parent=requests.get(f"http://127.0.0.1:8000/api/workflow/{uploaded_csv['run_id']}/result").json()
    assert parent==uploaded_csv, "原结果被复核覆盖"
    # 非法单位、窗口外记录、重复ID：保留输入，不能创建后台运行。
    for label,change in [("invalid-unit",lambda d:d["events"][0].update(energy_unit="MJ?")),("future-event",lambda d:d["events"][0].update(timestamp=d["window_end"])),("duplicate-id",lambda d:d["events"][1].update(event_id=d["events"][0]["event_id"]))]:
        data=copy.deepcopy(task);change(data); text=json.dumps(data)
        fill("原始监测任务JSON",text); count=ev("window.__runs.length")
        click("校验并送入六 Agent")
        until("!!document.querySelector('.monitoring-workbench .ant-alert-error')")
        assert ev("window.__runs.length")==count
        assert ev("document.querySelector('[aria-label=\"原始监测任务JSON\"]').value")==text
        print(label,"rejected, input preserved",flush=True)
    # 与演示场景不同的自定义摘要，使用原有对话输入框和发送按钮。
    summary="MINE-003 / RDW-027 / 工作面-27：过去45分钟监测到4条微震事件，频次由3.2次/h升至6.4次/h，最大能量12500 J，b值由1.28降至1.19，数据缺失率3%。高能事件0条，空间集中比例35%。支护工7/5人、工程师1/1人、锚杆260/140根、液压支柱36/24根、钻机2/1台。"
    fill("监测任务",summary)
    custom=run("custom-summary",button="发送任务")
    assert custom["agents"]["perception_result"]["risk_score"]==.195
    # 恢复原始数据任务后，核验桌面/窄屏每个长面板实际可滚动。
    fill("原始监测任务JSON",json.dumps(task))
    run("final-raw")
    dimensions=[]
    for width,height in [(1366,768),(754,424)]:
        call("Emulation.setDeviceMetricsOverride",{"width":width,"height":height,"deviceScaleFactor":1,"mobile":False})
        time.sleep(.3)
        info=ev("(()=>{let out={};for(let s of ['.chat-main','.chat-stream-panel','.event-stream','.workflow-result-panel']){let e=document.querySelector(s);let before=e.scrollTop;e.scrollTop=40;let r=e.getBoundingClientRect();out[s]={height:e.clientHeight,scroll:e.scrollHeight,moved:e.scrollTop>0,overflow:getComputedStyle(e).overflowY,bounds:[r.x,r.y,r.width,r.height]};e.scrollTop=before;}out.documentOverflow=document.documentElement.scrollWidth>innerWidth;return out;})()")
        assert not info["documentOverflow"], info
        for key in ['.chat-main','.chat-stream-panel','.event-stream','.workflow-result-panel']:
            assert info[key]["height"]>0 and info[key]["moved"], (key,info)
        dimensions.append({"viewport":[width,height],"panels":info})
        ev("document.querySelector('.workflow-result-panel').scrollIntoView({block:'center'})")
        screenshot(f"scroll-{width}x{height}")
    (OUT/"scroll-validation.json").write_text(json.dumps(dimensions,ensure_ascii=False,indent=2),encoding="utf-8")
    call("Emulation.setDeviceMetricsOverride",{"width":1366,"height":768,"deviceScaleFactor":1,"mobile":False})
    assert not ev("window.__errors"), ev("window.__errors")
    print("uploads, feedback, custom input, scroll verified",flush=True)


if __name__ == "__main__":
    setup()
    click("载入真实样本1")
    until("document.querySelector('[aria-label=\"原始监测任务JSON\"]').value.length>100")
    first = run("sample1")
    assessment = first["agents"]["perception_result"]["assessment"]
    assert assessment["metrics"]["event_count"] == 6 and assessment["alarm"] is False
    assert first["agents"]["perception_result"]["risk_level"] is None
    click("载入研究报警样本2")
    until("JSON.parse(document.querySelector('[aria-label=\"原始监测任务JSON\"]').value).events.length===30")
    second = run("sample2", approval=True)
    assert second["agents"]["perception_result"]["assessment"]["alarm"] is True
    ev("document.querySelector('.workflow-result-panel').scrollIntoView({block:'start'})")
    screenshot("sample2-result")
    print("browser_errors", ev("window.__errors"), flush=True)
    extended()
