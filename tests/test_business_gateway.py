"""本地HTTP契约测试：不向真实人员发送消息，不代表矿方联调。"""
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.business_gateway import BusinessGateway, GatewayConfig, GatewayError


class Provider(BaseHTTPRequestHandler):
    writes = 0
    behavior = "accept"
    def log_message(self, *args): pass
    def respond(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        try: self.wfile.write(raw)
        except OSError: pass
    def do_GET(self):
        if self.path == "/identity":
            if self.headers.get("Authorization") != "Bearer test-user": return self.respond(401, {})
            return self.respond(200, {"user_id":"operator1", "roles":["矿总工程师"], "permissions":["notify","work_order","review"], "mine_ids":["M1","ELK-CREEK"], "active":True})
        if self.headers.get("Authorization") != "Bearer service": return self.respond(401,{})
        if self.path.startswith("/inventory"):
            from datetime import datetime, timezone
            return self.respond(200,{"mine_id":"M1","source":"local-contract-test","observed_at":datetime.now(timezone.utc).isoformat(), "available_resources":{"personnel":{"支护工":3},"support_materials":{},"equipment":{"钻机":2}}})
        return self.respond(200, {"capabilities":{"identity":True,"inventory":True,"notifications":True,"receipts":True,"work_orders":True}})
    def do_POST(self):
        Provider.writes += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if Provider.behavior == "slow": time.sleep(.3)
        if Provider.behavior == "reject": return self.respond(422,{})
        return self.respond(202, {"provider_request_id": "remote-"+body["request_id"], "status":"accepted"})


class BusinessTests(unittest.TestCase):
    def test_workflow_reads_selected_inventory_without_user_count_fallback(self):
        import asyncio
        from unittest.mock import patch
        from app.api.workflow_runner import WorkflowRunner
        from test_raw_monitoring import sample
        task=sample();task.update(mine_id="M1",inventory_source="business", resources={"equipment":[{"resource_id":"drill","name":"钻机","required":1,"available":999,"unit":"台"}]})
        async def exercise():
            runner=WorkflowRunner()
            with patch("app.services.business_gateway.configured_gateway",return_value=self.gateway):
                record=await runner.start_async(scenario_id="custom",query=json.dumps(task))
                await record.task
            self.assertEqual(record.status,"completed",record.error)
            evidence=record.final_state["resource_result"].evidence[0].value
            self.assertEqual(evidence["comparison"]["equipment"][0]["available"],2)
            self.assertEqual(record.final_state["custom_task"]["resources"]["equipment"][0]["available"],2)
            self.assertEqual(json.loads(record.query)["resources"]["equipment"][0]["available"],999)
        asyncio.run(exercise())

    def test_http_api_verified_review_and_explicit_notification(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from app.api.server import app
        from app.services.monitoring_files import real_sample
        def wait(client, run_id, target):
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                snapshot=client.get(f"/api/workflow/{run_id}/status").json()
                if snapshot["status"]==target:return snapshot
                if snapshot["status"]=="failed":self.fail(snapshot)
                time.sleep(.05)
            self.fail("工作流未到达目标状态")
        with patch("app.services.business_gateway.configured_gateway",return_value=self.gateway), patch("app.api.business_routes.configured_gateway",return_value=self.gateway), TestClient(app) as client:
            started=client.post("/api/workflow/start",json={"scenario_id":"custom","query":json.dumps(real_sample(2)["task"])}).json()
            run_id=started["run_id"];wait(client,run_id,"waiting_human")
            rejected=client.post(f"/api/workflow/{run_id}/approve",json={"operator":"矿总工程师"},headers={"Authorization":"Bearer bad"})
            self.assertEqual(rejected.status_code,403)
            approved=client.post(f"/api/workflow/{run_id}/approve",json={"operator":"fake-client-name"},headers={"Authorization":"Bearer test-user"})
            self.assertEqual(approved.status_code,200,approved.text)
            wait(client,run_id,"completed")
            result=client.get(f"/api/workflow/{run_id}/result").json()
            self.assertEqual(result["approval_decision"]["approved_by"],"operator1")
            self.assertEqual(result["agents"]["control_result"]["dispatch_status"],"not_dispatched")
            body={"request_id":"api-local-test","recipient":"local-contract-target"}
            response=client.post(f"/api/business/runs/{run_id}/notify",json=body,headers={"Authorization":"Bearer test-user"})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["status"],"accepted")
            blocked=client.post(f"/api/business/runs/{run_id}/work-order",json={**body,"action_sequence":2},headers={"Authorization":"Bearer test-user"})
            self.assertEqual(blocked.status_code,409)
            self.assertEqual(Provider.writes,1)

    def test_mysql_duplicate_rows_are_counted_and_scope_is_required(self):
        from unittest.mock import patch
        from app.services.mysql_bridge import load_available_resources, scoped_inventory
        with patch("app.services.mysql_bridge._get_pool",return_value=object()), patch("app.services.mysql_bridge._execute",side_effect=[([],[("工人",),("工人",)]),([],[('锚杆',10),('锚杆',20)]),([],[('钻机',),('钻机',)])]):
            result = load_available_resources()
            self.assertEqual(result["equipment"]["钻机"],2)
            self.assertEqual(result["support_materials"]["锚杆"],30)
        with patch.dict("os.environ", {"MYSQL_SCOPE_MINE_ID":"OTHER"}):
            with self.assertRaises(ValueError): scoped_inventory("M1")

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1",0), Provider)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.worker.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join()
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Provider.writes = 0; Provider.behavior = "accept"
        self.gateway = BusinessGateway(GatewayConfig(base_url=f"http://127.0.0.1:{self.server.server_port}",service_token="service",receipt_token="receipt", timeout_seconds=.1), Path(self.temp.name)/"ledger.sqlite")
    def body(self):
        return {"request_id":"req1", "run_id":"run1", "mine_id":"M1", "recipient":"test-target", "kind":"notification", "content":"local test only"}
    def test_unconfigured_and_read_contract(self):
        missing = BusinessGateway(GatewayConfig(), Path(self.temp.name)/"missing.sqlite")
        self.assertEqual(missing.status()["inventory"]["status"], "not_configured")
        self.assertEqual(self.gateway.status()["inventory"]["status"], "connected")
        self.assertEqual(self.gateway.inventory("M1")["available_resources"]["equipment"]["钻机"],2)
        with self.assertRaises(GatewayError): self.gateway.identity("forged-user")
        with self.assertRaises(GatewayError): self.gateway.inventory("OTHER")
    def test_accepted_is_not_execution_and_duplicate_is_not_sent(self):
        one = self.gateway.submit(self.body(), "test-user")
        two = self.gateway.submit(self.body(), "test-user")
        self.assertEqual(one["status"], "accepted")
        self.assertEqual(two["status"], "accepted"); self.assertEqual(Provider.writes,1)
        with self.assertRaises(GatewayError): self.gateway.submit({**self.body(), "content":"changed"}, "test-user")
        with self.assertRaises(GatewayError): self.gateway.submit({**self.body(), "mine_id":"OTHER"}, "test-user")
    def test_timeout_remains_unknown_and_does_not_retry(self):
        Provider.behavior = "slow"
        one = self.gateway.submit(self.body(), "test-user")
        two = self.gateway.submit(self.body(), "test-user")
        self.assertEqual(one["status"], "delivery_unknown"); self.assertEqual(two["status"], "delivery_unknown")
        self.assertEqual(Provider.writes,1)
    def test_business_rejection_and_unauthorized_write(self):
        with self.assertRaises(GatewayError): self.gateway.submit(self.body(), "bad")
        self.assertEqual(Provider.writes,0)
        Provider.behavior = "reject"
        self.assertEqual(self.gateway.submit(self.body(), "test-user")["status"], "rejected")
    def test_authenticated_receipt_binding_and_state_regression(self):
        sent = self.gateway.submit(self.body(), "test-user")
        receipt = {"receipt_id":"r1", "request_id":"req1", "provider_request_id":sent["provider_request_id"],"mine_id":"M1", "recipient":"test-target","kind":"notification", "status":"delivered","occurred_at":sent["created_at"]}
        with self.assertRaises(GatewayError): self.gateway.receipt(receipt, "bad")
        with self.assertRaises(GatewayError): self.gateway.receipt({**receipt,"mine_id":"OTHER"}, "receipt")
        self.assertEqual(self.gateway.receipt(receipt,"receipt")["status"],"delivered")
        self.assertEqual(self.gateway.receipt(receipt,"receipt")["status"],"delivered")
        with self.assertRaises(GatewayError): self.gateway.receipt({**receipt,"status":"failed"},"receipt")
        with self.assertRaises(GatewayError): self.gateway.receipt({**receipt,"receipt_id":"r2","status":"executed"},"receipt")
    def test_work_order_requires_server_approval_and_action_receipt(self):
        body = {**self.body(),"kind":"work_order","action_sequence":2}
        with self.assertRaises(GatewayError): self.gateway.submit(body,"test-user")
        approved = {"run_id":"run1","mine_id":"M1","action_sequences":[2],"approved_by":"operator1","field_plan_verified":True}
        sent = self.gateway.submit(body,"test-user", approval=approved)
        receipt = {"receipt_id":"r1","request_id":"req1","provider_request_id":sent["provider_request_id"],"mine_id":"M1","recipient":"test-target","kind":"work_order","action_sequence":2,"status":"executed","occurred_at":sent["created_at"]}
        result = self.gateway.receipt(receipt,"receipt")
        self.assertEqual(result["status"],"executed")


if __name__ == "__main__": unittest.main()
