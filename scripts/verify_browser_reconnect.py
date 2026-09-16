"""补验真实WebSocket重连、审批面板滚动及研判记录；不下发现场指令。"""
import json
import time
import requests
import verify_browser_monitoring as b

b.setup()
b.click("载入研究报警样本2")
b.until("document.querySelector('[aria-label=\"原始监测任务JSON\"]').value.length>100")


def check_waiting(run_id):
    count=b.ev("window.__runs.length")
    prototype=b.call("Runtime.evaluate",{"expression":"WebSocket.prototype"})["result"]["objectId"]
    objects=b.call("Runtime.queryObjects",{"prototypeObjectId":prototype})["objects"]["objectId"]
    b.call("Runtime.callFunctionOn",{"objectId":objects,"functionDeclaration":"function(){window.__sockets=this.filter(s=>s.url.includes(':8000/ws/')&&s.readyState===1);}","returnByValue":True})
    assert b.ev("window.__sockets.length")>0
    b.ev("const OriginalSocket=window.WebSocket;window.WebSocket=class extends OriginalSocket{constructor(...args){super(...args);window.__sockets.push(this)}}")
    sockets=b.ev("window.__sockets.length")
    b.ev("window.__sockets.at(-1).close()")
    b.until(f"window.__sockets.length>{sockets} && window.__sockets.at(-1).readyState===1")
    assert b.ev("window.__runs.length")==count
    assert requests.get(f"http://127.0.0.1:8000/api/workflow/{run_id}/status").json()["status"]=="waiting_human"
    dimensions=[]
    for width,height in [(1366,768),(754,424)]:
        b.call("Emulation.setDeviceMetricsOverride",{"width":width,"height":height,"deviceScaleFactor":1,"mobile":False})
        time.sleep(.3)
        b.ev("document.querySelector('.approval-panel').scrollIntoView({block:'center',behavior:'instant'})")
        info=b.ev("(()=>{let e=document.querySelector('.approval-panel-body');e.scrollTop=100;return {height:e.clientHeight,scroll:e.scrollHeight,moved:e.scrollTop>0,overflow:getComputedStyle(e).overflowY}})()")
        assert info["overflow"]=="auto" and info["height"]>0
        if width==754:assert info["moved"],info
        dimensions.append({"viewport":[width,height],"body":info})
        b.screenshot(f"approval-scroll-{width}x{height}")
    (b.OUT/"approval-scroll-validation.json").write_text(json.dumps(dimensions,indent=2),encoding="utf-8")
    b.call("Emulation.setDeviceMetricsOverride",{"width":1366,"height":768,"deviceScaleFactor":1,"mobile":False})
    print("WebSocket重连未重新启动任务；审批面板宽窄屏滚动通过",flush=True)


result=b.run("reconnect-review",approval=True,during_approval=check_waiting)
assert result["approval_decision"]["decision"]=="approved"
assert result["approval_decision"]["verified_identity"] is None
assert result["agents"]["control_result"]["dispatch_status"]=="not_dispatched"
b.click("业务接入状态 · 身份、库存、通知与回执")
b.until("document.querySelector('.business-connections').textContent.includes('未配置')")
assert not b.ev("window.__errors"), b.ev("window.__errors")
print("人工意见已展示；无身份凭据不冒充授权；业务服务未配置状态真实",flush=True)
