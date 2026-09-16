import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  approveWorkflow as approveWorkflowApi,
  cancelWorkflow as cancelWorkflowApi,
  fetchWorkflowResult,
  getWorkflowStatus,
  listSessionFiles,
  rejectWorkflow as rejectWorkflowApi,
  startWorkflow as startWorkflowApi,
  uploadSessionFiles
} from "../lib/api";
import { WS_BASE_URL } from "../lib/config";
import { createThreadId, getStoredThreadId, storeThreadId } from "../lib/thread";
import type {
  AgentNodeName,
  ApprovalRequest,
  ConnectionState,
  MonitorMessage,
  OutputFile,
  RiskLevel,
  SocketMessage,
  UploadedItem,
  WorkflowStartResponse,
  WorkflowStatus,
  WorkflowResultResponse
} from "../types";

const MAX_EVENTS = 120;

function extractString(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

export function useWorkflowSession() {
  const activeRunRef = useRef("");
  const terminalRunRef = useRef("");
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const heartbeatTimerRef = useRef<number | undefined>(undefined);
  const uploadedNameSetRef = useRef<Set<string>>(new Set());
  const [threadId, setThreadId] = useState(getStoredThreadId);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [events, setEvents] = useState<MonitorMessage[]>([]);
  const [files, setFiles] = useState<OutputFile[]>([]);
  const [sessionPath, setSessionPath] = useState("");
  const [result, setResult] = useState("");
  const [lastError, setLastError] = useState("");
  const [lastPongAt, setLastPongAt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedItems, setUploadedItems] = useState<UploadedItem[]>([]);

  // ── 六 Agent 工作流状态跟踪 ──
  const [workflowRunId, setWorkflowRunId] = useState<string>("");
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus>("completed");
  const [currentNode, setCurrentNode] = useState<AgentNodeName | "">("");
  const [riskLevel, setRiskLevel] = useState<RiskLevel | null>(null);
  const [approvalRequest, setApprovalRequest] = useState<ApprovalRequest | null>(null);
  const [approvalPending, setApprovalPending] = useState(false);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [resultData, setResultData] = useState<WorkflowResultResponse | null>(null);

  const clearSocketTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = undefined;
    }
  }, []);

  const resetSession = useCallback(() => {
    activeRunRef.current = "";
    terminalRunRef.current = "";
    const nextThreadId = createThreadId();
    storeThreadId(nextThreadId);
    setThreadId(nextThreadId);
    setEvents([]);
    setFiles([]);
    setSessionPath("");
    setResult("");
    setLastError("");
    setUploadedItems([]);
    uploadedNameSetRef.current.clear();
    setIsRunning(false);
    setIsCancelling(false);
    // 重置工作流状态
    setWorkflowRunId("");
    setWorkflowStatus("completed");
    setCurrentNode("");
    setRiskLevel(null);
    setApprovalRequest(null);
    setApprovalPending(false);
    setScenarioId("");
    setResultData(null);
  }, []);

  const refreshFiles = useCallback(async () => {
    if (!sessionPath) {
      return;
    }

    const response = await listSessionFiles(sessionPath);
    if (response.error) {
      throw new Error(response.error);
    }
    setFiles(response.files || []);
  }, [sessionPath]);

  useEffect(() => {
    let disposed = false;

    function connect() {
      clearSocketTimers();
      const hadSocket = Boolean(socketRef.current);
      socketRef.current?.close();
      setConnectionState(hadSocket ? "reconnecting" : "connecting");

      const socket = new WebSocket(`${WS_BASE_URL}/ws/${encodeURIComponent(threadId)}`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        setConnectionState("connected");
        setLastError("");
        heartbeatTimerRef.current = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 25000);
      };

      socket.onmessage = (event) => {
        if (socketRef.current !== socket) {
          return;
        }
        try {
          const payload = JSON.parse(event.data) as SocketMessage;
          if (payload.type === "pong") {
            setLastPongAt(new Date().toISOString());
            return;
          }

          if (payload.type !== "monitor_event") {
            return;
          }
          if (payload.workflow_run_id && activeRunRef.current && payload.workflow_run_id !== activeRunRef.current) return;

          setEvents((previous) => [...previous, payload].slice(-MAX_EVENTS));

          // ── 六 Agent 工作流事件解析 ──
          // 从 WS 事件驱动看板状态：当前节点高亮 / 风险等级 / 审批请求 / 工作流终态
          if (payload.event === "workflow_start") {
            // 新打开的同会话页面可跟随服务端当前运行，其后仍按run_id隔离事件。
            if (!activeRunRef.current) activeRunRef.current = String(payload.data?.run_id ?? payload.workflow_run_id ?? "");
            setWorkflowRunId(
              (previous) =>
                previous ||
                String(payload.data?.run_id ?? payload.workflow_run_id ?? "")
            );
            setWorkflowStatus("running");
            setScenarioId(String(payload.data?.scenario_id ?? ""));
            setCurrentNode("");
          }
          if (payload.event === "agent_start" || payload.event === "agent_failed") {
            const node = payload.data?.node;
            if (typeof node === "string") {
              setCurrentNode(node as AgentNodeName);
            }
          }
          if (payload.event === "alert_triggered") {
            const level = payload.data?.risk_level;
            if (typeof level === "string") {
              setRiskLevel(level as RiskLevel);
            }
          }
          if (payload.event === "approval_required") {
            setApprovalRequest(payload.data as unknown as ApprovalRequest);
            setWorkflowStatus("waiting_human");
          }
          if (payload.event === "workflow_complete") {
            terminalRunRef.current = String(payload.workflow_run_id ?? payload.data?.run_id ?? "");
            const finalStatus = String(payload.data?.final_status ?? "completed");
            setWorkflowStatus(finalStatus as WorkflowStatus);
            setIsRunning(false);
            setIsCancelling(false);
            setCurrentNode("");
            // 工作流完成后自动拉取完整结果，供 WorkflowResultPanel 渲染
            // 完成事件的运行 ID 由 monitor 统一放在顶层；兼容旧事件仍放在 data.run_id。
            const completedRunId = String(payload.data?.run_id ?? payload.workflow_run_id ?? "");
            if (completedRunId) {
              fetchWorkflowResult(completedRunId)
                .then((data) => {
                  if (activeRunRef.current !== completedRunId) return;
                  setResultData(data);
                  setWorkflowStatus(data.status as WorkflowStatus);
                  const perception = data.agents.perception_result;
                  if (perception) setRiskLevel(perception.risk_level as RiskLevel);
                  // 对话显示后端实际节点摘要，完整指标与建议仍可在结果面板核对。
                  setResult(Object.values(data.agents).filter(Boolean).map((agent) => agent!.summary).join("\n\n"));
                })
                .catch((err) => console.warn("[result] 拉取完整结果失败:", err));
            }
          }
          if (payload.event === "workflow_failed") {
            terminalRunRef.current = String(payload.workflow_run_id ?? "");
            setWorkflowStatus("failed");
            setIsRunning(false);
            setIsCancelling(false);
            setCurrentNode("");
          }

          if (payload.event === "session_created") {
            const path = extractString(payload.data, "path");
            if (path) {
              setSessionPath(path);
            }
          }

          if (payload.event === "task_result") {
            const finalResult = extractString(payload.data, "result");
            setResult(finalResult || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "task_cancelled") {
            setResult((previous) => previous || payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }

          if (payload.event === "error") {
            setLastError(payload.message);
            setIsRunning(false);
            setIsCancelling(false);
          }
        } catch (error) {
          setLastError(error instanceof Error ? error.message : "WebSocket 消息解析失败");
        }
      };

      socket.onerror = () => {
        if (!disposed && socketRef.current === socket) {
          setLastError("WebSocket 连接异常，请确认后端服务已启动");
        }
      };

      socket.onclose = () => {
        if (socketRef.current !== socket) {
          return;
        }
        clearSocketTimers();
        if (disposed) {
          setConnectionState("closed");
          return;
        }
        setConnectionState("reconnecting");
        reconnectTimerRef.current = window.setTimeout(connect, 2000);
      };
    }

    connect();

    return () => {
      disposed = true;
      clearSocketTimers();
      socketRef.current?.close();
    };
  }, [clearSocketTimers, threadId]);

  useEffect(() => {
    if (!sessionPath) {
      return;
    }

    refreshFiles().catch((error: unknown) => {
      setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
    });

    const timer = window.setInterval(() => {
      refreshFiles().catch((error: unknown) => {
        setLastError(error instanceof Error ? error.message : "文件列表刷新失败");
      });
    }, isRunning ? 2500 : 6000);

    return () => window.clearInterval(timer);
  }, [isRunning, refreshFiles, sessionPath]);

    // ── 六 Agent 工作流启动与人工审批 ──

  /** 取消当前六 Agent 工作流（工作流取消接口，非旧 /api/task 路径） */
  const cancelCurrentTask = useCallback(async () => {
    if (!workflowRunId) {
      throw new Error("当前没有运行中的工作流");
    }

    setIsCancelling(true);
    setLastError("");
    try {
      const response = await cancelWorkflowApi(workflowRunId, {
        operator: "矿总工程师"
      });
      if (response.status === "cancelled") {
        setIsRunning(false);
        setIsCancelling(false);
        setResult((previous) => previous || "任务已取消");
        setWorkflowStatus("cancelled");
      }
      return response;
    } catch (error) {
      setIsCancelling(false);
      throw error;
    }
  }, [workflowRunId]);

  /** 启动六 Agent 工作流（场景驱动，后台线程异步执行） */
  const startWorkflow = useCallback(
    async (scenarioId: string, query = "", runId?: string): Promise<WorkflowStartResponse> => {
      const requestedRunId = runId || `wf-${crypto.randomUUID()}`;
      activeRunRef.current = requestedRunId;
      terminalRunRef.current = "";
      setIsRunning(true);
      setIsCancelling(false);
      setEvents([]);
      setResult("");
      setLastError("");
      setCurrentNode("");
      setRiskLevel(null);
      setApprovalRequest(null);
      // 先清除上次结果，快速完成的 WS 事件不会再被请求返回覆盖为运行中。
      setResultData(null);
      setWorkflowRunId("");
      setWorkflowStatus("running");
      try {
        const response = await startWorkflowApi({
          scenario_id: scenarioId,
          run_id: requestedRunId,
          query: query || undefined,
          thread_id: threadId
        });

        setWorkflowRunId(response.run_id);
        setScenarioId(response.scenario_id || scenarioId);
        if (response.thread_id && response.thread_id !== threadId) {
          storeThreadId(response.thread_id);
          setThreadId(response.thread_id);
        }
        return response;
      } catch (error) {
        // 输入校验失败没有后台运行，应立即恢复输入而不是持续显示运行中。
        const text = error instanceof Error ? error.message : "工作流启动失败";
        setIsRunning(false);
        setWorkflowStatus("failed");
        setLastError(text);
        setResult(text);
        throw error;
      }
    },
    [threadId]
  );

  // 状态查询补偿断线丢失的事件；同一运行只查询，不重新提交。
  useEffect(() => {
    if (!workflowRunId || (!isRunning && resultData && workflowStatus === resultData.status)) return;
    let disposed = false;
    let timer: number | undefined;
    async function synchronize() {
      try {
        const snapshot = await getWorkflowStatus(workflowRunId);
        if (disposed || activeRunRef.current !== workflowRunId) return;
        const terminal = ["completed", "failed", "rejected", "cancelled"].includes(snapshot.status);
        // 完成事件可能先于较早发出的查询返回；旧的running快照不能覆盖终态。
        if (!terminal && terminalRunRef.current === workflowRunId) {
          timer = window.setTimeout(synchronize, 500);
          return;
        }
        setWorkflowStatus(snapshot.status);
        setApprovalRequest(snapshot.approval_request ?? null);
        setEvents(snapshot.events.map(event => ({ ...event, type: "monitor_event" as const,
          event: (event as unknown as { type: string }).type })));
        if (terminal) {
          terminalRunRef.current = workflowRunId;
          setIsRunning(false); setIsCancelling(false); setCurrentNode("");
          if (snapshot.error) setLastError(snapshot.error);
          if (snapshot.status === "completed") {
            const data = await fetchWorkflowResult(workflowRunId);
            if (disposed || activeRunRef.current !== workflowRunId) return;
            setResultData(data); setRiskLevel((data.agents.perception_result?.risk_level as RiskLevel | null) ?? null);
            setResult(Object.values(data.agents).filter(Boolean).map(agent => agent!.summary).join("\n\n"));
          }
          return;
        }
      } catch (error) {
        if (!disposed) setLastError(`运行状态同步失败：${error instanceof Error ? error.message : String(error)}`);
      }
      if (!disposed) timer = window.setTimeout(synchronize, 2500);
    }
    void synchronize();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [workflowRunId, connectionState, isRunning, resultData, workflowStatus]);

  /** 人工审批确认：恢复工作流继续下发处置指令 */
  const approveWorkflow = useCallback(
    async (note?: string, identityToken?: string) => {
      if (!workflowRunId) {
        throw new Error("当前没有运行中的工作流");
      }
      setApprovalPending(true);
      setLastError("");
      try {
        const response = await approveWorkflowApi(workflowRunId, {
          operator: "矿总工程师",
          note
        }, identityToken);
        setWorkflowStatus(previous => terminalRunRef.current === workflowRunId ? previous : "approved");
        return response;
      } finally {
        setApprovalPending(false);
      }
    },
    [workflowRunId]
  );

  /** 人工审批拒绝：恢复工作流并标记处置为 rejected 终止 */
  const rejectWorkflow = useCallback(
    async (note?: string) => {
      if (!workflowRunId) {
        throw new Error("当前没有运行中的工作流");
      }
      setApprovalPending(true);
      setLastError("");
      try {
        const response = await rejectWorkflowApi(workflowRunId, {
          operator: "矿总工程师",
          note
        });
        setWorkflowStatus("rejected");
        return response;
      } finally {
        setApprovalPending(false);
      }
    },
    [workflowRunId]
  );

  const uploadFiles = useCallback(
    async (items: UploadedItem[]) => {
      if (items.length === 0) {
        throw new Error("请选择要上传的文件");
      }

      const nextItems = items.filter((item) => !uploadedNameSetRef.current.has(item.name));

      if (nextItems.length === 0) {
        return {
          status: "uploaded",
          files: Array.from(uploadedNameSetRef.current)
        };
      }

      setIsUploading(true);
      setLastError("");
      try {
        const response = await uploadSessionFiles(
          nextItems.map((item) => item.raw),
          threadId
        );
        setUploadedItems((previous) => {
          const names = new Set(previous.map((item) => item.name));
          const next = [...previous];
          nextItems.forEach((item) => {
            if (!names.has(item.name)) {
              names.add(item.name);
              uploadedNameSetRef.current.add(item.name);
              next.push(item);
            }
          });
          return next;
        });
        return response;
      } finally {
        setIsUploading(false);
      }
    },
    [threadId]
  );

  const stats = useMemo(() => {
    const toolEvents = events.filter((event) => event.event === "tool_start").length;
    const assistantEvents = events.filter((event) => event.event === "assistant_call").length;
    const errorEvents = events.filter((event) => event.event === "error").length;

    return {
      toolEvents,
      assistantEvents,
      errorEvents,
      fileCount: files.length
    };
  }, [events, files.length]);

  return {
    connectionState,
    events,
    files,
    isCancelling,
    isRunning,
    isUploading,
    lastError,
    lastPongAt,
    refreshFiles,
    resetSession,
    result,
    sessionPath,
    stats,
    cancelCurrentTask,
    threadId,
    uploadFiles,
    uploadedItems,
    // 六 Agent 工作流状态与方法
    workflowRunId,
    workflowStatus,
    currentNode,
    riskLevel,
    approvalRequest,
    approvalPending,
    scenarioId,
    resultData,
    startWorkflow,
    approveWorkflow,
    rejectWorkflow
  };
}
