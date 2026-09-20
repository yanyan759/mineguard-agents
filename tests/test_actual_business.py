"""实际数据 API 到本机业务提供方再到回执复核；外部写操作仅作用于本机验收服务。"""
import json
import os
import time
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.api.server import app
from app.services.business_gateway import BusinessGateway, GatewayConfig
from app.services.workflow_feedback import RunArchive
from test_business_gateway import Provider


class ActualBusinessTests(unittest.TestCase):
    def test_configuration_upload_work_order_receipt_and_observation_roundtrip(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),Provider)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        Provider.writes=0;Provider.behavior="accept"
        with TemporaryDirectory() as folder:
            gateway=BusinessGateway(GatewayConfig(base_url=f"http://127.0.0.1:{server.server_port}",service_token="service",receipt_token="receipt"),Path(folder)/"ledger.sqlite3")
            archive=RunArchive(Path(folder)/"runs.sqlite3")
            with patch.dict(os.environ,{"MINE_CONFIGURATION_DB":str(Path(folder)/"config.sqlite3")}),patch("app.services.business_gateway.configured_gateway",return_value=gateway),patch("app.api.business_routes.configured_gateway",return_value=gateway),patch("app.services.workflow_feedback.archive",archive),patch("app.api.business_routes.archive",archive),patch("app.api.monitoring_routes.archive",archive),TestClient(app) as client:
                now=datetime.now(timezone.utc)
                scope=dict(mine_id="M1",roadway_id="RDW-ACCEPT",area="验收测区")
                def upload(category,body):
                    meta={**scope,"category":category,"version":"v1","title":category,"source":"本地契约验收资料","observed_at":(now-timedelta(hours=2)).isoformat()}
                    response=client.post("/api/configuration/ingest",data={"metadata":json.dumps(meta)},files={"file":(category+".json",json.dumps(body,ensure_ascii=False).encode())})
                    self.assertEqual(response.status_code,200,response.text);return response.json()["asset_id"]
                assets=[upload("case",{"text":"验收案例原文"}),upload("regulation",{"text":"验收规程原文"})]
                plan=upload("support",{"text":"验收方案","actions":[{"sequence":1,"action":"核对钻机状态","owner_role":"矿总工程师","deadline_minutes":20,"requires_approval":True}],"requirements":[{"group":"equipment","name":"钻机","required":1,"unit":"台"}]})
                assets.append(plan)
                response=client.post("/api/configuration",json={**scope,"version":"v1","source":"验收参数","valid_from":"2020-01-01T00:00:00Z","valid_to":"2100-01-01T00:00:00Z","window_minutes":60,"rules":[{"metric":"max_energy_j","operator":">=","threshold":5000,"level":"yellow"}],"assets":assets,"plan_asset_id":plan,"inventory_source":"business"})
                self.assertEqual(response.status_code,200,response.text)
                task={**scope,"input_type":"raw_events","source":"本地接口验收记录","method_id":"statistics-v1","window_start":(now-timedelta(hours=2)).isoformat(),"window_end":(now-timedelta(hours=1)).isoformat(),"events":[{"event_id":"a","timestamp":(now-timedelta(minutes=90)).isoformat(),"energy":6000,"energy_unit":"J"}],"missing_rate":0}
                def wait(run_id,target):
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        data=client.get(f"/api/workflow/{run_id}/status").json()
                        if data["status"]==target:return data
                        if data["status"]=="failed":self.fail(data["error"])
                        time.sleep(.03)
                    self.fail(f"运行未达到{target}")
                started=client.post("/api/workflow/start",json={"scenario_id":"custom","query":json.dumps(task)})
                self.assertEqual(started.status_code,200,started.text)
                run_id=started.json()["run_id"];wait(run_id,"waiting_human")
                headers={"Authorization":"Bearer test-user"}
                response=client.post(f"/api/workflow/{run_id}/approve",json={"operator":"不可作为可信身份"},headers=headers)
                self.assertEqual(response.status_code,200,response.text);status=wait(run_id,"completed")
                result=client.get(f"/api/workflow/{run_id}/result").json()
                self.assertEqual(len(result["agents"]),6)
                self.assertEqual(result["agents"]["resource_result"]["quantity_check"],"sufficient")
                self.assertEqual(result["provenance"]["task"]["resources"]["equipment"][0]["available"],2)
                verify={"plan_asset_id":plan,"snapshot_id":result["provenance"]["snapshot_id"],"evidence_reference":"LOCAL-FIELD-ACCEPTANCE-001","personnel_qualified":True,"equipment_ready":True,"inventory_confirmed":True,"site_conditions_confirmed":True}
                self.assertEqual(client.post(f"/api/business/runs/{run_id}/verify-plan",json=verify,headers={"Authorization":"Bearer wrong"}).status_code,409)
                self.assertEqual(client.post(f"/api/business/runs/{run_id}/verify-plan",json={**verify,"snapshot_id":"wrong"},headers=headers).status_code,409)
                self.assertEqual(client.post(f"/api/business/runs/{run_id}/verify-plan",json=verify,headers=headers).status_code,200)
                body={"request_id":"accept-work-order","recipient":"local-only","action_sequence":1}
                sent=client.post(f"/api/business/runs/{run_id}/work-order",json=body,headers=headers)
                self.assertEqual(sent.status_code,200,sent.text);self.assertEqual(sent.json()["status"],"accepted")
                client.post(f"/api/business/runs/{run_id}/work-order",json=body,headers=headers)
                self.assertEqual(Provider.writes,1)
                receipt={"receipt_id":"accept-receipt","request_id":body["request_id"],"provider_request_id":sent.json()["provider_request_id"],"mine_id":"M1","recipient":"local-only","kind":"work_order","action_sequence":1,"status":"executed","occurred_at":datetime.now(timezone.utc).isoformat()}
                self.assertEqual(client.post("/api/business/receipts",json=receipt,headers={"Authorization":"Bearer wrong"}).status_code,409)
                self.assertEqual(client.post("/api/business/receipts",json=receipt,headers={"Authorization":"Bearer receipt"}).status_code,200)
                new_start=datetime.now(timezone.utc)
                observation={**task,"window_start":new_start.isoformat(),"window_end":(new_start+timedelta(hours=1)).isoformat(),"events":[{"event_id":"b","timestamp":new_start.isoformat(),"energy":2000,"energy_unit":"J"}]}
                feedback=client.post(f"/api/monitoring/runs/{run_id}/feedback",json={"request_id":"accept-feedback","reported_by":"记录人","observation":observation,"actions":[]})
                self.assertEqual(feedback.status_code,200,feedback.text)
                child=feedback.json()
                started=client.post("/api/workflow/start",json={"scenario_id":"custom","run_id":child["child_run_id"],"query":json.dumps(child["task"])})
                self.assertEqual(started.status_code,200,started.text)
                wait(child["child_run_id"],"waiting_human")
                client.post(f"/api/workflow/{child['child_run_id']}/approve",json={"operator":"记录人"},headers=headers)
                wait(child["child_run_id"],"completed")
                reflection=client.get(f"/api/workflow/{child['child_run_id']}/result").json()["agents"]["reflection_result"]
                self.assertEqual(reflection["goal_completion_score"],1)
                self.assertEqual(reflection["return_decision"]["action"],"finish")
                self.assertEqual(reflection["evidence"][0]["value"]["changes"]["max_energy_j"]["delta"],-4000)
                persisted=archive.get_run(run_id)
                self.assertEqual(persisted["result"]["agents"]["perception_result"]["risk_level"],"yellow")
                self.assertTrue(all(a["used_in_runs"] for a in client.get("/api/configuration",params=scope).json()["assets"]))
                self.assertIn("reflection_iteration",status["phases"])
