import {
  AlertOutlined,
  ApiOutlined,
  BookOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  NotificationOutlined,
  SolutionOutlined,
  SyncOutlined,
  TeamOutlined
} from "@ant-design/icons";
import { Alert, App as AntApp, Button } from "antd";
import { useEffect, useRef, useState } from "react";
import { AgentTopology } from "./components/AgentTopology";
import { ApprovalPanel } from "./components/ApprovalPanel";
import { ChatComposer } from "./components/ChatComposer";
import { MonitoringWorkbench } from "./components/MonitoringWorkbench";
import { ObservationFeedback } from "./components/ObservationFeedback";
import { BusinessConnections } from "./components/BusinessConnections";
import { MineConfiguration } from "./components/MineConfiguration";
import { ConversationThread } from "./components/ConversationThread";
import { EventStream } from "./components/EventStream";
import { RiskBadge } from "./components/RiskBadge";
import type { ChatTurn } from "./components/ConversationThread";
import { WorkflowResultPanel } from "./components/WorkflowResultPanel";
import { useWorkflowSession } from "./hooks/useWorkflowSession";
import { API_BASE_URL, WS_BASE_URL } from "./lib/config";
import {
  AGENT_NODE_META,
  WORKFLOW_SCENARIOS,
  WORKFLOW_STATUS_META
} from "./types";
import type { AgentNodeName, ConnectionState, UploadedItem } from "./types";

function connectionLabel(state: ConnectionState): string {
  const labels: Record<ConnectionState, string> = {
    connecting: "连接中",
    connected: "已连接",
    reconnecting: "重连中",
    closed: "已关闭"
  };
  return labels[state];
}

function createTurn(content: string): ChatTurn {
  return {
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`,
    content,
    events: [],
    files: [],
    isRunning: true,
    result: "",
    timestamp: new Date().toISOString()
  };
}

/** 侧边栏六 Agent 列表（含图标） */
const SIDEBAR_AGENTS: Array<{ key: AgentNodeName; icon: React.ReactNode }> = [
  { key: "perception_warning", icon: <AlertOutlined aria-hidden /> },
  { key: "knowledge_retrieval", icon: <BookOutlined aria-hidden /> },
  { key: "dispatch_decision", icon: <SolutionOutlined aria-hidden /> },
  { key: "resource_evaluation", icon: <TeamOutlined aria-hidden /> },
  { key: "collaborative_control", icon: <NotificationOutlined aria-hidden /> },
  { key: "reflection_iteration", icon: <SyncOutlined aria-hidden /> }
];

export default function App() {
  const { message } = AntApp.useApp();
  const [query, setQuery] = useState("");
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [feedbackParentId, setFeedbackParentId] = useState<string | null>(null);
  const streamRef = useRef<HTMLElement | null>(null);
  const session = useWorkflowSession();
  const { resultData } = session;

  useEffect(() => {
    // 复核启动失败时保留原任务的反馈编辑区，避免已保存反馈失去重试入口。
    if (resultData) setFeedbackParentId(resultData.agents.perception_result?.assessment ? resultData.run_id : null);
  }, [resultData]);

  useEffect(() => {
    setTurns((previous) => {
      if (previous.length === 0) {
        return previous;
      }

      const latestTurn = previous[previous.length - 1];
      const nextLatestTurn = {
        ...latestTurn,
        events: session.events,
        files: session.files,
        isRunning: session.isRunning,
        result: session.result
      };

      return [...previous.slice(0, -1), nextLatestTurn];
    });
  }, [session.events, session.files, session.isRunning, session.result]);

  useEffect(() => {
    const streamNode = streamRef.current;
    if (!streamNode) {
      return;
    }

    window.requestAnimationFrame(() => {
      streamNode.scrollTo({
        top: streamNode.scrollHeight,
        behavior: "smooth"
      });
    });
  }, [turns]);

  /** 失败时回填 turn 状态 */
  function markTurnFailed(turnId: string, error: unknown) {
    setTurns((previous) =>
      previous.map((turn) =>
        turn.id === turnId
          ? {
              ...turn,
              isRunning: false,
              result: error instanceof Error ? error.message : "工作流启动失败"
            }
          : turn
      )
    );
  }

  /** 场景卡片启动六 Agent 工作流 */
  async function handleStartScenario(scenarioId: string) {
    const scenario = WORKFLOW_SCENARIOS.find((item) => item.id === scenarioId);
    const content = scenario
      ? `启动场景：${scenario.title}\n${scenario.input}`
      : `启动场景：${scenarioId}`;
    const nextTurn = createTurn(content);
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.startWorkflow(scenarioId, scenario?.input ?? "");
      message.success("六 Agent 工作流已启动，执行轨迹实时展示");
    } catch (error) {
      markTurnFailed(nextTurn.id, error);
      message.error(error instanceof Error ? error.message : "工作流启动失败");
    }
  }

  /** 自定义提交进入摘要解析入口，由真实指标决定风险与后续分支。 */
  async function handleSubmit() {
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      message.warning("请输入监测任务，或点击场景卡片启动工作流");
      return;
    }

    const nextTurn = createTurn(cleanQuery);
    setTurns((previous) => [...previous, nextTurn]);
    setQuery("");

    try {
      await session.startWorkflow("custom", cleanQuery);
      message.success("工作流已启动，执行过程会显示在看板上");
    } catch (error) {
      // 校验失败时保留原文，用户可补充指标后重新提交。
      setQuery(cleanQuery);
      markTurnFailed(nextTurn.id, error);
      message.error(error instanceof Error ? error.message : "工作流启动失败");
    }
  }

  async function handleCancel() {
    try {
      const response = await session.cancelCurrentTask();
      message.info(
        response.status === "cancelling" ? "取消请求已发送，正在等待当前调用结束" : "任务已取消"
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  async function handleUpload(items: UploadedItem[]) {
    try {
      const response = await session.uploadFiles(items);
      setStagedItems([]);
      message.success(`已上传 ${response.files.length} 个文件`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "上传失败");
    }
  }

  function handleNewSession() {
    session.resetSession();
    setTurns([]);
    setQuery("");
    setStagedItems([]);
    setFeedbackParentId(null);
  }

  /** 人工审批：批准执行 */
  async function handleApprove(note?: string, identityToken?: string) {
    try {
      await session.approveWorkflow(note, identityToken);
      message.success(session.approvalRequest?.notice ? "已记录同意意见，继续汇总评估；未下发现场指令" : "审批结果已提交");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "审批失败");
    }
  }

  /** 人工审批：拒绝 */
  async function handleReject(note?: string) {
    try {
      await session.rejectWorkflow(note);
      message.info("已拒绝，工作流将终止");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "审批失败");
    }
  }

  const online = session.connectionState === "connected";
  const statusMeta = WORKFLOW_STATUS_META[session.workflowStatus];
  const waitingApproval =
    session.workflowStatus === "waiting_human" && session.approvalRequest !== null;

  useEffect(() => {
    const target = waitingApproval ? ".approval-panel" : resultData ? ".workflow-result-panel" : null;
    if (!target) return;
    const frame = window.requestAnimationFrame(() => document.querySelector(target)?.scrollIntoView({block:"start",behavior:"instant"}));
    return () => window.cancelAnimationFrame(frame);
  }, [waitingApproval, resultData?.run_id]);

  return (
    <div className="chat-app-shell min-h-dvh">
      <aside className="chat-sidebar" aria-label="会话信息">
        <div className="sidebar-brand">
          <span className="panel-kicker">COAL MINE AGENTS</span>
          <h1>顶板灾变预警</h1>
          <p>六智能体协同处置决策台</p>
        </div>

        <Button className="new-chat-button" block onClick={handleNewSession}>
          新建会话
        </Button>

        <div className="sidebar-section">
          <span className="sidebar-label">THREAD</span>
          <strong className="thread-id" title={session.threadId}>
            {session.threadId.slice(0, 8)}
          </strong>
        </div>

        <div className="sidebar-status-list">
          <div
            className={`sidebar-status ${online ? "sidebar-status--online" : "sidebar-status--warn"}`}
          >
            <ApiOutlined aria-hidden />
            <span>WebSocket</span>
            <strong>{connectionLabel(session.connectionState)}</strong>
          </div>
          <div className="sidebar-status">
            <BranchesOutlined aria-hidden />
            <span>工作流</span>
            <strong>{statusMeta.label}</strong>
          </div>
          {session.riskLevel ? (
            <div className="sidebar-status">
              <AlertOutlined aria-hidden />
              <span>风险等级</span>
              <RiskBadge level={session.riskLevel} />
            </div>
          ) : null}
          <div
            className={session.stats.errorEvents > 0 ? "sidebar-status sidebar-status--error" : "sidebar-status"}
          >
            <CloseCircleOutlined aria-hidden />
            <span>异常</span>
            <strong>{session.stats.errorEvents}</strong>
          </div>
        </div>

        <div className="sidebar-section">
          <span className="sidebar-label">AGENTS</span>
          <ul className="agent-mini-list">
            {SIDEBAR_AGENTS.map((agent) => (
              <li
                className={session.currentNode === agent.key ? "agent-mini--active" : ""}
                key={agent.key}
              >
                {agent.icon}
                {AGENT_NODE_META[agent.key].name}
              </li>
            ))}
          </ul>
        </div>

        <div className="sidebar-section sidebar-endpoints">
          <span className="sidebar-label">ENDPOINTS</span>
          <code>{API_BASE_URL}</code>
          <code>{WS_BASE_URL}</code>
        </div>
      </aside>

      <main className="chat-main">
        <header className="chat-topbar">
          <div>
            <span className="panel-kicker">WORKSPACE</span>
            <h2>顶板灾变处置看板</h2>
          </div>
          <div className="topbar-right">
            {session.riskLevel ? <RiskBadge level={session.riskLevel} /> : null}
            <div className={`run-indicator ${session.isRunning ? "run-indicator--live" : ""}`}>
              {session.isRunning ? <BranchesOutlined aria-hidden /> : <CheckCircleOutlined aria-hidden />}
              {session.isRunning ? "处置中" : "待命"}
            </div>
          </div>
        </header>

        {session.lastError ? (
          <Alert className="chat-alert" message={session.lastError} showIcon type="error" />
        ) : null}

        {/* 六 Agent 处置链路拓扑（当前节点高亮） */}
        <AgentTopology currentNode={session.currentNode} events={session.events} />

        {/* 高风险动作人工审批 */}
        {waitingApproval && session.approvalRequest ? (
          <ApprovalPanel
            pending={session.approvalPending}
            onApprove={handleApprove}
            onReject={handleReject}
            request={session.approvalRequest}
          />
        ) : null}

        {/* 工作流完成后自动展开完整结构化处置结果 */}
        {session.workflowStatus === "completed" && resultData ? (
          <WorkflowResultPanel result={resultData} />
        ) : null}
        {feedbackParentId ?
          <ObservationFeedback key={feedbackParentId} runId={feedbackParentId} busy={session.isRunning} onStart={async (task, runId) => {
            const turn = createTurn(`关联原任务 ${String(task.parent_run_id)} 的后续观测复核`);
            setTurns(previous => [...previous, turn]);
            try { await session.startWorkflow("custom", JSON.stringify(task), runId); }
            catch(error) { markTurnFailed(turn.id,error); throw error; }
          }} /> : null}

        {/* 主区：对话 + 六 Agent 实时轨迹 双栏 */}
        <div className="workspace-grid">
          <section className="chat-stream-panel" ref={streamRef}>
            <ConversationThread onStartScenario={handleStartScenario} turns={turns} />
          </section>
          <EventStream events={session.events} />
        </div>

        <MonitoringWorkbench key={session.threadId} threadId={session.threadId} busy={session.isRunning}
          filenames={session.uploadedItems.map(item => item.name)} onStart={async task => {
            const nextTurn = createTurn(`原始监测记录：${String(task.mine_id)} / ${String(task.area)}，方法 ${String(task.method_id)}`);
            setTurns(previous => [...previous, nextTurn]);
            try { await session.startWorkflow("custom", JSON.stringify(task)); }
            catch (error) { markTurnFailed(nextTurn.id, error); throw error; }
          }} />
        <BusinessConnections runId={session.workflowStatus === "completed" && resultData?.scenario_id === "custom" ? resultData.run_id : undefined} />
        <MineConfiguration threadId={session.threadId} busy={session.isRunning} />
        <ChatComposer
          isCancelling={session.isCancelling}
          isRunning={session.isRunning}
          isUploading={session.isUploading}
          onCancel={handleCancel}
          onNewSession={handleNewSession}
          onQueryChange={setQuery}
          onStagedItemsChange={setStagedItems}
          onSubmit={handleSubmit}
          onUpload={handleUpload}
          query={query}
          stagedItems={stagedItems}
          uploadedItems={session.uploadedItems}
        />
      </main>
    </div>
  );
}
