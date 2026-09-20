import { API_BASE_URL } from "./config";
import type {
  ApprovalActionRequest,
  ApprovalResponse,
  FileListResponse,
  UploadResponse,
  WorkflowStartRequest,
  WorkflowStartResponse,
  WorkflowStatusResponse,
  CancelWorkflowResponse,
  WorkflowResultResponse
} from "../types";

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new Error(message);
  }

  return payload as T;
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("thread_id", threadId);
  files.forEach((file) => formData.append("files", file));

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  });
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"));
  url.searchParams.set("path", path);
  return requestJson<FileListResponse>(url);
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"));
  url.searchParams.set("path", path);
  return url.toString();
}

// ═══════════════════════════════════════════════════════════════════════
// 六 Agent 工作流接口
// ═══════════════════════════════════════════════════════════════════════

/** 启动工作流：custom 解析 query，其余 ID 选择显式预设场景。 */
export async function startWorkflow(
  request: WorkflowStartRequest
): Promise<WorkflowStartResponse> {
  return requestJson<WorkflowStartResponse>(apiUrl("/api/workflow/start"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(request)
  });
}

/** 人工审批确认：恢复工作流继续下发处置指令 */
export async function approveWorkflow(
  runId: string,
  action: ApprovalActionRequest = {},
  identityToken?: string
): Promise<ApprovalResponse> {
  return requestJson<ApprovalResponse>(
    apiUrl(`/api/workflow/${encodeURIComponent(runId)}/approve`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(identityToken ? { Authorization: `Bearer ${identityToken}` } : {})
      },
      body: JSON.stringify(action)
    }
  );
}

/** 人工审批拒绝：恢复工作流并标记处置为 rejected 终止 */
export async function rejectWorkflow(
  runId: string,
  action: ApprovalActionRequest = {}
): Promise<ApprovalResponse> {
  return requestJson<ApprovalResponse>(
    apiUrl(`/api/workflow/${encodeURIComponent(runId)}/reject`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(action)
    }
  );
}

/** 取消一次六 Agent 工作流运行（非终态可取消） */
export async function cancelWorkflow(
  runId: string,
  action: ApprovalActionRequest = {}
): Promise<CancelWorkflowResponse> {
  return requestJson<CancelWorkflowResponse>(
    apiUrl(`/api/workflow/${encodeURIComponent(runId)}/cancel`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(action)
    }
  );
}

/** 查询六 Agent 工作流运行状态 */
export async function getWorkflowStatus(runId: string): Promise<WorkflowStatusResponse> {
  return requestJson<WorkflowStatusResponse>(
    apiUrl(`/api/workflow/${encodeURIComponent(runId)}/status`)
  );
}

// ═══════════════════════════════════════════════════════════════════════
// 完整结果查询接口
// ═══════════════════════════════════════════════════════════════════════

/** 查询六 Agent 工作流完整结果（Redis checkpoint 恢复） */
export async function fetchWorkflowResult(runId: string): Promise<WorkflowResultResponse> {
  return requestJson<WorkflowResultResponse>(
    apiUrl(`/api/workflow/${encodeURIComponent(runId)}/result`)
  );
}
