"""明确HTTP契约的业务适配器：可信身份、库存读取、幂等提交与鉴权回执。"""
from __future__ import annotations

import hmac
import json
import os
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import httpx
from pydantic import Field, model_validator
from app.services.raw_monitoring import StrictModel


class GatewayError(ValueError):
    pass


class GatewayConfig(StrictModel):
    base_url: str = ""
    service_token: str = Field(default="", repr=False)
    receipt_token: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=5, gt=0, le=30)

    @model_validator(mode="after")
    def safe_transport(self):
        if self.base_url:
            parts = urlsplit(self.base_url)
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("业务地址不能嵌入凭据、查询参数或片段")
            if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
                raise ValueError("外部业务地址必须使用HTTPS，本机契约测试可用HTTP")
        return self


class Identity(StrictModel):
    user_id: str = Field(min_length=1)
    roles: list[str]
    permissions: list[str]
    mine_ids: list[str]
    active: bool


class Inventory(StrictModel):
    mine_id: str
    source: str = Field(min_length=1)
    observed_at: datetime
    available_resources: dict[str, dict[str, float]]

    @model_validator(mode="after")
    def scoped_counts(self):
        if self.observed_at.tzinfo is None or self.observed_at > datetime.now(timezone.utc):
            raise ValueError("库存时间必须含时区且不得在未来")
        if set(self.available_resources) != {"personnel", "support_materials", "equipment"}:
            raise ValueError("库存必须分别声明人员、材料和设备")
        if any(not math.isfinite(v) or v < 0 for group in self.available_resources.values() for v in group.values()):
            raise ValueError("库存数量不得为负")
        return self


class Submission(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    run_id: str = Field(min_length=1)
    mine_id: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    kind: Literal["notification", "work_order"]
    content: str = Field(min_length=1, max_length=20000)
    action_sequence: int | None = Field(default=None, ge=1)


class Receipt(StrictModel):
    receipt_id: str = Field(min_length=1, max_length=100)
    request_id: str
    provider_request_id: str
    mine_id: str
    recipient: str
    kind: Literal["notification", "work_order"]
    action_sequence: int | None = None
    status: Literal["delivered", "executed", "failed"]
    occurred_at: datetime


class BusinessGateway:
    def __init__(self, config: GatewayConfig, path: Path):
        self.config, self.path = config, path

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, body TEXT NOT NULL, record TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        try:
            with conn: yield conn
        finally: conn.close()

    def _request(self, method, path, token, **kwargs):
        if not self.config.base_url or not token: raise GatewayError("业务服务未配置或缺少凭据")
        try:
            # 禁止跟随重定向或自动重试写操作，避免凭据外送和重复下发。
            with httpx.Client(timeout=self.config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
                return client.request(method, self.config.base_url.rstrip("/")+path, headers={"Authorization":f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise GatewayError("业务请求未确认完成（网络异常或超时）") from exc

    def status(self):
        keys = ("identity", "inventory", "notifications", "work_orders", "receipts")
        if not self.config.base_url or not self.config.service_token:
            return {key:{"status":"not_configured", "detail":"未配置业务地址或服务凭据"} for key in keys}
        try:
            response = self._request("GET", "/health", self.config.service_token)
            if response.status_code != 200: raise GatewayError("健康接口拒绝请求")
            capabilities = response.json()["capabilities"]
            result = {key:{"status":"connected" if capabilities.get(key) is True else "unavailable", "detail":"健康契约检查；具体身份、数据及现场业务仍需逐项联调"} for key in keys}
            if not self.config.receipt_token: result["receipts"] = {"status":"not_configured", "detail":"缺少回执鉴权凭据"}
            return result
        except (GatewayError, ValueError, KeyError, AttributeError):
            return {key:{"status":"failed", "detail":"健康接口失败或响应不符合契约"} for key in keys}

    def identity(self, user_token):
        response = self._request("GET", "/identity", user_token)
        if response.status_code != 200: raise GatewayError("身份提供方未验证当前用户")
        try: identity = Identity.model_validate(response.json())
        except ValueError as exc: raise GatewayError("身份响应不符合契约") from exc
        if not identity.active: raise GatewayError("用户已停用")
        return identity.model_dump()

    def inventory(self, mine_id):
        response = self._request("GET", "/inventory", self.config.service_token, params={"mine_id":mine_id})
        if response.status_code != 200: raise GatewayError("库存接口不可查询")
        try: snapshot = Inventory.model_validate(response.json())
        except ValueError as exc: raise GatewayError("库存响应不符合契约") from exc
        if snapshot.mine_id != mine_id: raise GatewayError("库存响应矿井不匹配")
        return snapshot.model_dump(mode="json")

    def get_submission(self, request_id):
        with self.connection() as conn:
            row = conn.execute("SELECT record FROM submissions WHERE id=?", (request_id,)).fetchone()
        if not row: raise GatewayError("业务请求不存在")
        return json.loads(row[0])

    def submissions_for_run(self, run_id):
        """只查询本地已鉴权落库的业务回执，不触发任何发送。"""
        with self.connection() as conn:
            rows=conn.execute("SELECT record FROM submissions WHERE json_extract(record,'$.run_id')=? ORDER BY rowid",(run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def submit(self, body, user_token, approval=None):
        """approval仅接收服务端已核验方案，API不得透传客户端的approved字段。"""
        task = Submission.model_validate(body)
        identity = self.identity(user_token)
        permission = "notify" if task.kind == "notification" else "work_order"
        if task.mine_id not in identity["mine_ids"] or permission not in identity["permissions"]:
            raise GatewayError("可信身份无本矿相应业务权限")
        if task.kind == "work_order":
            if not approval or not approval.get("field_plan_verified") or approval.get("run_id") != task.run_id or approval.get("mine_id") != task.mine_id or approval.get("approved_by") != identity["user_id"] or task.action_sequence not in approval.get("action_sequences", []):
                raise GatewayError("缺少服务端核验的现场方案与动作审批，工单未发送")
        canonical = json.dumps({**task.model_dump(), "requested_by":identity["user_id"]}, sort_keys=True, ensure_ascii=False)
        record = {**task.model_dump(), "requested_by":identity["user_id"], "status":"delivery_unknown", "provider_request_id":None, "created_at":datetime.now(timezone.utc).isoformat()}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body,record FROM submissions WHERE id=?", (task.request_id,)).fetchone()
            if row:
                if row[0] != canonical: raise GatewayError("请求编号已绑定不同内容或用户")
                return json.loads(row[1])
            # 先落库再发送：即使进程在响应前退出，同编号重试也只读取待核对记录。
            conn.execute("INSERT INTO submissions VALUES (?,?,?)", (task.request_id, canonical, json.dumps(record, ensure_ascii=False)))
        try:
            endpoint = "/notifications" if task.kind == "notification" else "/work-orders"
            response = self._request("POST", endpoint, self.config.service_token, json=task.model_dump())
            if response.status_code in {200,201,202}:
                data = response.json()
                if data.get("status") == "accepted" and isinstance(data.get("provider_request_id"), str) and data["provider_request_id"]:
                    record.update(status="accepted", provider_request_id=data["provider_request_id"])
            elif 400 <= response.status_code < 500 and response.status_code not in {408,409,429}:
                record["status"] = "rejected"
        except (GatewayError, ValueError, AttributeError):
            pass
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            latest = json.loads(conn.execute("SELECT record FROM submissions WHERE id=?",(task.request_id,)).fetchone()[0])
            if latest["status"] not in {"delivered", "executed", "failed"}:
                conn.execute("UPDATE submissions SET record=? WHERE id=?", (json.dumps(record, ensure_ascii=False),task.request_id))
            else: record = latest
        return record

    def receipt(self, body, token):
        if not self.config.receipt_token or not hmac.compare_digest(token or "", self.config.receipt_token):
            raise GatewayError("回执鉴权失败")
        receipt = Receipt.model_validate(body)
        canonical = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM submissions WHERE id=?", (receipt.request_id,)).fetchone()
            if not row: raise GatewayError("回执关联请求不存在")
            record = json.loads(row[0])
            for key in ("mine_id", "recipient", "kind", "action_sequence"):
                if record.get(key) != getattr(receipt,key): raise GatewayError("回执对象、矿井或动作不匹配")
            if not receipt.provider_request_id or (record["provider_request_id"] and receipt.provider_request_id != record["provider_request_id"]):
                raise GatewayError("回执外部请求编号不匹配")
            occurred = receipt.occurred_at
            if occurred.tzinfo is None or not datetime.fromisoformat(record["created_at"]) <= occurred <= datetime.now(timezone.utc):
                raise GatewayError("回执时间倒序、缺时区或在未来")
            if record.get("receipt") and occurred < datetime.fromisoformat(record["receipt"]["occurred_at"]):
                raise GatewayError("回执时间不得倒退")
            old = conn.execute("SELECT body FROM receipts WHERE id=?", (receipt.receipt_id,)).fetchone()
            if old:
                if old[0] != canonical: raise GatewayError("重复回执内容冲突")
                return record
            if receipt.kind == "notification" and receipt.status == "executed":
                raise GatewayError("通知送达不能写成动作已执行")
            allowed = {"delivery_unknown":{"delivered","executed","failed"}, "accepted":{"delivered","executed","failed"}, "delivered":{"executed","failed"} if receipt.kind=="work_order" else set()}
            if receipt.status not in allowed.get(record["status"], set()): raise GatewayError("回执状态回退或终态冲突")
            record.update(status=receipt.status, provider_request_id=receipt.provider_request_id, receipt=receipt.model_dump(mode="json"))
            conn.execute("INSERT INTO receipts VALUES (?,?)", (receipt.receipt_id,canonical))
            conn.execute("UPDATE submissions SET record=? WHERE id=?",(json.dumps(record,ensure_ascii=False),receipt.request_id))
        return record


def configured_gateway():
    # 凭据仅从后端环境读取，不放入页面、任务、日志或返回的运行证据。
    config = GatewayConfig(base_url=os.getenv("BUSINESS_BASE_URL", ""), service_token=os.getenv("BUSINESS_SERVICE_TOKEN", ""), receipt_token=os.getenv("BUSINESS_RECEIPT_TOKEN", ""), timeout_seconds=float(os.getenv("BUSINESS_TIMEOUT_SECONDS", "5")))
    return BusinessGateway(config, Path(__file__).resolve().parents[1]/"output/business_ledger.sqlite3")
