"""业务接入接口；所有外部写操作均为显式请求，六节点不会自动发送。"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.services.business_gateway import configured_gateway, Receipt
from app.services.workflow_feedback import archive
from app.services.raw_monitoring import StrictModel
from datetime import datetime, timezone

router = APIRouter(prefix="/api/business")


def bearer(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "需要身份提供方签发的凭据")
    return authorization[7:]


@router.get("/status")
def status():
    try: return {"services": configured_gateway().status(), "automatic_dispatch":False}
    except ValueError: return {"services":{}, "error":"业务配置不符合契约，请检查后端配置", "automatic_dispatch":False}


@router.get("/identity")
def identity(authorization: str | None = Header(default=None)):
    try: return configured_gateway().identity(bearer(authorization))
    except ValueError as exc: raise HTTPException(401, str(exc)) from exc


@router.get("/inventory")
def inventory(mine_id: str):
    try: return configured_gateway().inventory(mine_id)
    except ValueError as exc: raise HTTPException(503, str(exc)) from exc


class SendRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    recipient: str = Field(min_length=1, max_length=200)
    action_sequence: int | None = Field(default=None, ge=1)


class FieldVerification(StrictModel):
    plan_asset_id: str
    snapshot_id: str
    evidence_reference: str = Field(min_length=5, max_length=2000)
    personnel_qualified: bool
    equipment_ready: bool
    inventory_confirmed: bool
    site_conditions_confirmed: bool


@router.post("/runs/{run_id}/verify-plan")
def verify_plan(run_id: str, body: FieldVerification, authorization: str | None = Header(default=None)):
    """由身份提供方校验本矿审批权限；字段勾选作为实名现场声明入审计。"""
    try:
        user=configured_gateway().identity(bearer(authorization))
        parent=archive.get_run(run_id)
        snapshot=parent.get("actual_snapshot")
        if not snapshot or parent["result"]["status"] != "completed" or not snapshot.get("plan"):
            raise ValueError("仅支持已完成且选用结构化方案的实际数据任务")
        if snapshot["task"]["mine_id"] not in user["mine_ids"] or not {"review","work_order"}.issubset(user["permissions"]):
            raise ValueError("当前身份没有本矿方案核验与工单权限")
        if body.snapshot_id != snapshot["snapshot_id"] or body.plan_asset_id != snapshot["plan"]["asset_id"]:
            raise ValueError("方案或输入快照已变化，请重新查看当前任务")
        if parent["result"]["agents"]["resource_result"]["quantity_check"] != "sufficient":
            raise ValueError("资源未满足方案需求，请补充资源并重新运行")
        if not all([body.personnel_qualified,body.equipment_ready,body.inventory_confirmed,body.site_conditions_confirmed]):
            raise ValueError("人员资质、设备、现场库存及适用条件必须全部核验")
        approval={**body.model_dump(),"field_plan_verified":True,"run_id":run_id,"mine_id":snapshot["task"]["mine_id"],
                  "approved_by":user["user_id"],"verified_identity":user,"verified_at":datetime.now(timezone.utc).isoformat(),
                  "action_sequences":[a["sequence"] for a in snapshot["plan"]["parsed"]["actions"]]}
        saved=archive.field_approval(run_id,approval)
        if saved["approved_by"] != user["user_id"] or saved["evidence_reference"] != body.evidence_reference:
            raise ValueError("本任务已存在独立审批记录，不能覆盖；条件变化请重新运行")
        return saved
    except KeyError as exc: raise HTTPException(404,"运行不存在") from exc
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc


@router.get("/runs/{run_id}")
def run_business_state(run_id: str, authorization: str | None = Header(default=None)):
    try:
        gateway=configured_gateway(); user=gateway.identity(bearer(authorization))
        parent=archive.get_run(run_id)
        if not parent.get("input") or parent["input"]["mine_id"] not in user["mine_ids"]:
            raise ValueError("当前身份无该矿访问权限")
        snapshot=parent.get("actual_snapshot") or {}
        return {"approval":archive.field_approval(run_id),"requests":gateway.submissions_for_run(run_id),
                "snapshot_id":snapshot.get("snapshot_id"),"plan":snapshot.get("plan"),
                "quantity_check":parent["result"]["agents"].get("resource_result",{}).get("quantity_check")}
    except KeyError as exc: raise HTTPException(404,"运行不存在") from exc
    except ValueError as exc: raise HTTPException(403,str(exc)) from exc


@router.post("/runs/{run_id}/notify")
def notify(run_id: str, body: SendRequest, authorization: str | None = Header(default=None)):
    """只发送指定任务的核验建议，目标由当前操作者明确指定。"""
    token = bearer(authorization)
    try:
        parent = archive.get_run(run_id)
        if parent["result"]["status"] != "completed" or not parent["input"]:
            raise ValueError("仅支持已完成的自定义监测任务")
        decision = parent["result"]["agents"]["decision_result"]
        return configured_gateway().submit({"request_id":body.request_id,"run_id":run_id,
            "mine_id":parent["input"]["mine_id"],"recipient":body.recipient,"kind":"notification",
            "content":"监测核验建议（非生产指令）："+decision["summary"]}, token)
    except KeyError as exc: raise HTTPException(404,"运行或处置建议不存在") from exc
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc


@router.post("/runs/{run_id}/work-order")
def work_order(run_id: str, body: SendRequest, authorization: str | None = Header(default=None)):
    """生产工单只能使用服务端已核验方案；研究、演示和人工意见均不满足。"""
    token = bearer(authorization)
    try:
        parent = archive.get_run(run_id)
        approved = archive.field_approval(run_id)
        if not approved: raise ValueError("该运行没有现场核验并批准的方案，不能创建生产工单")
        action = next((a for a in parent["result"]["agents"]["decision_result"]["primary_plan"]["actions"] if a["sequence"]==body.action_sequence),None)
        if not action: raise ValueError("方案中不存在指定动作")
        return configured_gateway().submit({"request_id":body.request_id,"run_id":run_id,"mine_id":parent["input"]["mine_id"],
            "recipient":body.recipient,"kind":"work_order","content":action["action"],"action_sequence":body.action_sequence},token,approval=approved)
    except KeyError as exc: raise HTTPException(404,"运行不存在") from exc
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc


@router.get("/requests/{request_id}")
def request_status(request_id: str, authorization: str | None = Header(default=None)):
    try:
        gateway = configured_gateway()
        user = gateway.identity(bearer(authorization))
        record = gateway.get_submission(request_id)
        if record["mine_id"] not in user["mine_ids"]: raise ValueError("当前身份无该矿访问权限")
        return record
    except ValueError as exc: raise HTTPException(403,str(exc)) from exc


@router.post("/receipts")
def receipt(body: Receipt, authorization: str | None = Header(default=None)):
    try: return configured_gateway().receipt(body.model_dump(mode="json"), bearer(authorization))
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc
