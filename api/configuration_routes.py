"""资料上传、后端配置和实际任务预检接口。"""
import json
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.responses import Response
from app.services.mine_configuration import ConfigurationStore, MineConfiguration, AssetMetadata, Scope, prepare_actual
from app.services.monitoring_task import parse_monitoring_task

router = APIRouter(prefix="/api/configuration")


@router.get("/templates/{category}")
def template(category: str):
    """模板空数量不能通过校验，避免占位值被误当作真实库存或需求。"""
    if category == "resources":
        body={"personnel":[],"support_materials":[],"equipment":[{"resource_id":"请填写资源编号","name":"请填写资源名称","available":None,"required":None,"unit":"请填写单位"}]}
    elif category in ("case","regulation","support"):
        body={"text":"请替换为本测区资料正文","terms":[],"actions":[],"requirements":[]}
        if category == "support":
            body.update(actions=[{"sequence":1,"action":"请填写批准方案中的动作","owner_role":"请填写责任岗位","deadline_minutes":None,"requires_approval":True,"acceptance_criteria":["请填写验收依据"]}],
                        requirements=[{"group":"equipment","name":"请填写与台账一致的名称","required":None,"unit":"请填写单位"}])
    else: raise HTTPException(404,"模板类别不存在")
    return Response(json.dumps(body,ensure_ascii=False,indent=2),media_type="application/json",
                    headers={"Content-Disposition":f'attachment; filename="{category}-template.json"'})


@router.get("")
def read_configuration(mine_id: str, roadway_id: str, area: str):
    try:
        scope = Scope(mine_id=mine_id,roadway_id=roadway_id,area=area)
        return ConfigurationStore().listing(scope.model_dump())
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.post("")
def save_configuration(config: MineConfiguration):
    try:
        return ConfigurationStore().save(config)
    except FileExistsError as exc:
        raise HTTPException(409,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.post("/ingest")
async def ingest(metadata: str = Form(...), file: UploadFile = File(...)):
    try:
        meta = AssetMetadata.model_validate_json(metadata)
        raw = await file.read(20*1024*1024+1)
        return ConfigurationStore().ingest(file.filename or "unnamed",raw,meta)
    except (ValueError, UnicodeError, OSError) as exc:
        raise HTTPException(422,str(exc)) from exc
    except Exception as exc:
        # 解析器异常不能伪装为上传成功，正文损坏时需要用户重传。
        raise HTTPException(422,"资料解析失败，请核对文件格式或提供文字版") from exc


class Preparation(BaseModel):
    query: str


@router.post("/prepare")
def prepare(request: Preparation):
    try:
        snapshot = prepare_actual(parse_monitoring_task(request.query))
        return {"status":"validated","snapshot":snapshot,"task":snapshot["task"]}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
