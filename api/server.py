"""
FastAPI 接口层

负责承接六 Agent 工作流的任务启动、人工审批、文件上传/下载、输出文件列表查询和
WebSocket 长连接。HTTP 接口只做轻量调度，真正的工作流执行放到后台任务中；
执行进度和最终结果由 monitor 按 thread_id 推送给前端。
"""

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import uvicorn
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Header,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.monitor import manager
from app.api.workflow_runner import runner
from app.api.business_routes import router as business_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    服务生命周期入口。

    启动时绑定当前事件循环到 WebSocket 管理器，确保后台 Agent 任务可以把
    monitor 事件投递回 FastAPI 所在的 loop。
    """
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")
    yield


# 当前文件位于 app/api/server.py，运行时目录统一收敛到 app 目录
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent

app = FastAPI(
    title="MineGuard Agents API",
    description="煤矿顶板灾变六智能体监测、研判与协同处置接口",
    lifespan=lifespan,
)
from app.api.monitoring_routes import router as monitoring_router
app.include_router(monitoring_router)
app.include_router(business_router)
from app.api.configuration_routes import router as configuration_router
app.include_router(configuration_router)

# output 保存每个会话最终工作区，前端只允许从这里浏览和下载生成文件
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

# updated 暂存用户上传文件
updated_dir = project_root / "updated"
updated_dir.mkdir(exist_ok=True)

# 教学项目通常前后端分别本地启动，这里放开跨域以便 Vite 页面直接调用 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WorkflowStartRequest(BaseModel):
    """启动六 Agent 工作流的请求体。

    scenario_id=custom 时从 query 解析监测摘要，其余 ID 显式选择预设场景。
    """

    scenario_id: str = "yellow"
    query: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    mine_id: str | None = None
    roadway_id: str | None = None
    operator: dict | None = None  # {user_id, role, permissions}


class ApprovalRequest(BaseModel):
    """人工审批请求体。

    operator: 执行审批的操作者（姓名/角色）
    note: 审批意见
    """

    operator: str | None = None
    note: str | None = None


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    """
    文件上传接口 (File Upload)。

    目标：
    1. 接收用户上传的一个或多个文件。
    2. 保存到 `updated/session_{thread_id}` 目录。
    3. 供 Agent 在后续任务中读取和分析。

    Args:
        files (List[UploadFile]): 文件对象列表。
        thread_id (str): 关联的任务会话 ID。
    """
    # 上传文件先按会话隔离保存，避免不同任务读取到彼此的附件
    from app.services.monitoring_files import session_file
    try:
        file_paths = [session_file(updated_dir, thread_id, file.filename or "") for file in files]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    target_dir = updated_dir / f"session_{thread_id}"
    if any(file.size is not None and file.size > 20*1024*1024 for file in files):
        raise HTTPException(413, "单个上传文件不得超过20MB")
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for file, file_path in zip(files, file_paths):
        # 直接复制文件流，避免大文件一次性读入内存
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    """
    文件下载接口 (File Download)。

    目标：
    1. 根据绝对路径下载文件。
    2. 严格的安全检查，防止越权访问。

    Args:
        path (str): 文件的绝对路径 (通常从 list_files 接口获取)。
    """
    try:
        # resolve 后再做 is_relative_to，防止 `../` 之类的路径穿越到 output 之外
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            return {"error": "拒绝访问: 只能下载输出目录下的文件"}
    except Exception:
        return {"error": "无效的路径参数"}

    if not abs_path.exists():
        return {"error": "文件不存在"}

    # FileResponse 会以流式响应返回文件内容，并让浏览器使用原文件名下载
    return FileResponse(abs_path, filename=abs_path.name)


@app.get("/api/files")
async def list_files(path: str):
    """
    文件列表查询接口 (File Explorer)。

    目标：
    1. 列出指定目录下的所有生成文件。
    2. 提供文件元数据（大小、修改时间、下载所需路径）。
    3. 严格的安全检查，防止路径遍历攻击。

    Args:
        path (str): 目标目录的绝对路径 (必须在 output 目录下)。
    """
    print(f"[DEBUG] 请求文件列表: {path}")

    try:
        # 和下载接口保持同一条安全边界：前端只能查看 output 目录内部内容
        abs_path = Path(path).resolve()
        output_abs = output_dir.resolve()

        if not abs_path.is_relative_to(output_abs):
            print(f"[ERROR] 拒绝访问: {abs_path} 不在 {output_abs} 目录下")
            return {"error": "拒绝访问: 只能访问输出目录下的文件"}

    except Exception as e:
        print(f"[ERROR] 路径解析失败: {e}")
        return {"error": f"路径无效: {e}"}

    if not abs_path.exists():
        return {"error": "目录不存在"}

    files = []
    try:
        # 递归返回文件元数据，前端据此渲染文件列表并发起下载请求
        for file_path in abs_path.rglob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "type": "file",
                        "path": str(file_path),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )

    except Exception as e:
        print(f"[ERROR] 遍历文件失败: {e}")
        return {"error": str(e)}

    # 最新生成的文件排在前面，方便用户优先看到本次任务产物
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] 找到 {len(files)} 个文件")
    return {"files": files}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """
    WebSocket 实时通讯核心接口 (Real-time Communication)。

    连接建立后，ConnectionManager 会用 thread_id 保存 WebSocket。monitor 后续
    发送事件时只需要按 thread_id 查找连接，就能把进度推给对应页面。循环中的
    receive_text 用于接收前端心跳，避免连接空闲断开。
    """
    print(f"会话向我们发起了请求，要求建立连接：{thread_id} 对应：{websocket}")

    # 连接建立后立即按 thread_id 注册，monitor 后续才能把事件定向推给当前页面
    await manager.connect(websocket, thread_id)

    try:
        while True:
            # 前端通常发送 ping 心跳；服务端回复 pong，顺便维持连接活跃
            data = await websocket.receive_text()
            await websocket.send_json(
                {"type": "pong", "message": f"服务端已收到: {data}"}
            )

    except WebSocketDisconnect:
        # 只移除当前 WebSocket 实例，避免旧连接断开时误删同 thread_id 的新连接
        manager.disconnect(websocket, thread_id)
        print(f"[WebSocket] 客户端已断开: {thread_id}")

    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
        manager.disconnect(websocket, thread_id)


# ═══════════════════════════════════════════════════════════════════════════
# 六 Agent 工作流接口
# 工作流在后台线程中由 WorkflowRunner 驱动，事件通过 /ws/{thread_id} 推送
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/api/workflow/start")
async def start_workflow(request: WorkflowStartRequest):
    """启动六 Agent 工作流。

    以演示场景或实际快照（scenario_id/query）作为输入，async 驱动六 Agent 工作流；
    各阶段事件（agent_start/agent_complete/approval_required 等）自动携带
    workflow_run_id 通过 /ws/{thread_id} 推送给前端。

    :return: {status, run_id, thread_id, scenario_id}
    """
    from app.agent.fixture_loader import FixtureDataSource

    valid_scenarios = [*FixtureDataSource().list_scenarios(), "custom"]
    if request.scenario_id not in valid_scenarios:
        raise HTTPException(
            status_code=400,
            detail=f"未知场景 '{request.scenario_id}'，可用: {valid_scenarios}",
        )

    actual_snapshot = None
    # 在创建后台任务之前反馈缺字段/格式错误，禁止校验失败后套用预设数据。
    if request.scenario_id == "custom":
        from app.services.monitoring_task import parse_monitoring_task
        try:
            task = parse_monitoring_task(request.query)
            if getattr(task, "input_type", None) == "raw_events" and task.input_files:
                from app.services.monitoring_files import verify_input_files
                verify_input_files(updated_dir, request.thread_id or request.run_id or "", task)
            if getattr(task, "parent_run_id", None) or getattr(task, "feedback_id", None):
                from app.services.workflow_feedback import archive, validate_feedback
                try:
                    record, feedback_body = archive.get_feedback(task.feedback_id)
                    expected = validate_feedback(archive.get_run(record["parent_run_id"]), feedback_body)
                    expected.parent_run_id = record["parent_run_id"]
                    expected.feedback_id = record["feedback_id"]
                    if expected.model_dump(mode="json") != task.model_dump(mode="json") or request.run_id != record["child_run_id"]:
                        raise ValueError("复核任务与已登记反馈不一致")
                except KeyError as exc:
                    raise ValueError("反馈关联不存在，请先登记反馈") from exc
            if (request.mine_id and request.mine_id != task.mine_id) or (request.roadway_id and request.roadway_id != task.roadway_id):
                raise ValueError("请求中的矿井/巷道编号与监测文字不一致")
            from app.services.mine_configuration import prepare_actual
            import asyncio
            actual_snapshot = await asyncio.to_thread(prepare_actual, task)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 异步驱动工作流，保证 Agent 子图和 MCP 客户端运行在受控事件循环中。
    try:
        record = await runner.start_async(
            scenario_id=request.scenario_id, run_id=request.run_id,
            thread_id=request.thread_id, mine_id=request.mine_id,
            roadway_id=request.roadway_id, operator=request.operator, query=request.query,
            actual_snapshot=actual_snapshot,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "started" if record.status == "running" else record.status,
        "workflow_status": record.status,
        "run_id": record.run_id,
        "thread_id": record.thread_id,
        "scenario_id": record.scenario_id,
    }


@app.post("/api/workflow/{run_id}/approve")
async def approve_workflow(run_id: str, request: ApprovalRequest, authorization: str | None = Header(default=None)):
    """人工审批确认：恢复工作流继续下发处置指令。

    仅当工作流处于 waiting_human（高风险动作待审批）时可审批。
    """
    try:
        verified_identity = None
        if authorization:
            from app.api.business_routes import bearer
            from app.services.business_gateway import configured_gateway
            from app.services.monitoring_task import parse_monitoring_task
            snapshot = runner.get_status(run_id)
            if snapshot["scenario_id"] != "custom":
                raise HTTPException(409, "演示场景仅记录演示意见，不申请真实身份授权")
            task = parse_monitoring_task(snapshot["query"])
            try:
                verified_identity = await asyncio.to_thread(configured_gateway().identity, bearer(authorization))
                role = (snapshot.get("approval_request") or {}).get("required_role")
                if task.mine_id not in verified_identity["mine_ids"] or "review" not in verified_identity["permissions"] or role not in verified_identity["roles"]:
                    raise ValueError("可信身份不具备本矿所需研判岗位权限")
            except ValueError as exc:
                raise HTTPException(403, str(exc)) from exc
        result = await runner.approve(
            run_id, approved_by=verified_identity["user_id"] if verified_identity else request.operator,
            note=request.note, verified_identity=verified_identity,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {run_id}")
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"status": "approved", "run_id": run_id}


@app.post("/api/workflow/{run_id}/reject")
async def reject_workflow(run_id: str, request: ApprovalRequest):
    """人工审批拒绝：恢复工作流并标记处置为 rejected 终止。"""
    try:
        result = await runner.reject(
            run_id, rejected_by=request.operator, note=request.note
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {run_id}")
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"status": "rejected", "run_id": run_id}


@app.post("/api/workflow/{run_id}/cancel")
async def cancel_workflow(run_id: str, request: ApprovalRequest):
    """取消一次工作流运行。

    仅非终态（running/waiting_human）可取消；waiting_human 注入取消决策终止，
    running 置取消标记在节点边界退出。返回取消后的最终状态。
    """
    try:
        result = await runner.cancel(
            run_id, cancelled_by=request.operator, note=request.note
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {run_id}")
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"status": "cancelled", "run_id": run_id}


@app.get("/api/workflow/{run_id}/status")
async def workflow_status(run_id: str):
    """查询六 Agent 工作流运行状态。

    返回：运行状态、已执行节点轨迹、各 Agent 结果摘要、审批请求/结果、事件列表。
    """
    try:
        return runner.get_status(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"工作流不存在: {run_id}")


@app.get("/api/workflow/{run_id}/result")
async def workflow_result(run_id: str):
    """查询六 Agent 工作流完整结果（Redis checkpoint 恢复）。

    返回六个 Agent 的完整结构化输出（status/summary/confidence + 全量业务字段），
    供前端 WorkflowResultPanel 渲染处置结果详情。
    """
    try:
        return await runner.get_result(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"结果不可用: {run_id}")


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
