"""原始记录准备接口，返回可提交至同一个六Agent入口的规范任务。"""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.monitoring_files import prepare_file, real_sample
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw, profiles
from app.services.workflow_feedback import archive, validate_feedback

router = APIRouter(prefix="/api/monitoring")
ROOT = Path(__file__).resolve().parents[1]


class FilePreparation(BaseModel):
    thread_id: str
    filename: str
    metadata: dict = Field(default_factory=dict)


@router.get("/methods")
def methods():
    return {"methods": profiles()}


@router.post("/validate")
def validate(task: RawMonitoringTask):
    from app.services.mine_configuration import prepare_actual
    try:
        snapshot = prepare_actual(task)
        return {"task": task.model_dump(mode="json"), "assessment": snapshot["assessment"], "snapshot":snapshot,"status": "validated"}
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@router.post("/prepare")
def prepare(request: FilePreparation):
    try:
        result = prepare_file(ROOT/"updated", request.thread_id, request.filename, request.metadata)
        return {**result, **validate(RawMonitoringTask.model_validate(result["task"]))}
    except (ValueError, OSError) as exc: raise HTTPException(422, str(exc)) from exc


@router.get("/samples/{number}")
def sample(number: int):
    try: return real_sample(number)
    except (ValueError, OSError) as exc: raise HTTPException(422, str(exc)) from exc


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    reported_by: str = Field(min_length=1, max_length=200)
    actions: list[dict] = Field(default_factory=list)
    observation: dict
    note: str = ""


@router.post("/runs/{run_id}/feedback")
def feedback(run_id: str, request: FeedbackRequest):
    """持久记录反馈，返回唯一子运行任务；仍从标准六Agent入口启动。"""
    try:
        parent = archive.get_run(run_id)
        task = validate_feedback(parent, request.model_dump())
        record, created = archive.reserve_feedback(run_id, request.request_id, request.model_dump())
        task.parent_run_id = run_id
        task.feedback_id = record["feedback_id"]
        return {**record, "created": created, "task": task.model_dump(mode="json")}
    except KeyError as exc: raise HTTPException(404, "原始运行尚未保存或不存在") from exc
    except (ValueError, TypeError) as exc: raise HTTPException(422, str(exc)) from exc
