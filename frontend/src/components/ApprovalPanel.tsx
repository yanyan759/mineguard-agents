import { AlertOutlined, CheckOutlined, CloseOutlined } from "@ant-design/icons";
import { Button, Tag } from "antd";
import { useState } from "react";
import { RISK_LEVEL_META } from "../types";
import type { ApprovalRequest, RiskLevel } from "../types";

interface ApprovalPanelProps {
  request: ApprovalRequest;
  pending: boolean;
  onApprove: (note?: string, identityToken?: string) => Promise<void>;
  onReject: (note?: string) => Promise<void>;
}

/**
 * 人工审批面板
 * 高风险动作（红警撤人等）经协同管控节点触发审批暂停后，在此展示审批请求
 * 并允许操作者批准/拒绝，交互结果通过 /api/workflow/{run_id}/approve|reject 恢复工作流。
 */
export function ApprovalPanel({ request, pending, onApprove, onReject }: ApprovalPanelProps) {
  const [note, setNote] = useState("");
  const [identityToken, setIdentityToken] = useState("");
  const risk = (request.risk_level as RiskLevel) ?? "red";
  const riskMeta = RISK_LEVEL_META[risk] ?? { color: "gold", label: "研究报警 · 现场未定级" };

  return (
    <section className="approval-panel" role="dialog" aria-label="高风险动作人工审批">
      <div className="approval-panel-head">
        <AlertOutlined aria-hidden />
        <div>
          <span className="panel-kicker">HUMAN APPROVAL</span>
          <h3>{request.notice ? "高风险建议待人工研判" : "高风险动作待人工审批"}</h3>
        </div>
        <Tag color={riskMeta.color}>{riskMeta.label}</Tag>
      </div>

      <div className="approval-panel-body">
        {request.notice ? <p className="approval-meta">{request.notice}</p> : null}
        <p className="approval-meta">
          所需研判岗位：
          <strong>{request.required_role}</strong>
          {request.coordination_id ? ` · 协同单 ${request.coordination_id}` : ""}
        </p>

        {request.dispatched_actions && request.dispatched_actions.length > 0 ? (
          <div className="approval-actions">
            <span className="approval-actions-label">待审批处置动作：</span>
            <ol>
              {request.dispatched_actions.map((action, index) => (
                <li key={`${action}-${index}`}>{action}</li>
              ))}
            </ol>
          </div>
        ) : null}

        <input
          className="approval-note"
          aria-label="审批意见"
          disabled={pending}
          onChange={(event) => setNote(event.target.value)}
          placeholder="审批意见（可选）"
          value={note}
        />

        <div className="approval-actions-row">
          <Button
            danger
            icon={<CheckOutlined />}
            loading={pending}
            onClick={() => void onApprove(note || undefined, identityToken || undefined)}
            type="primary"
          >
            {request.notice ? "记录同意意见" : "批准执行"}
          </Button>
          <Button
            icon={<CloseOutlined />}
            loading={pending}
            onClick={() => void onReject(note || undefined)}
          >
            拒绝
          </Button>
        </div>
        {request.notice && <details><summary>可选：验证研判人身份</summary>
          <p>已接入身份提供方时填写个人凭据，由后端核验矿井、岗位和研判权限；留空仅记录人工意见。</p>
          <input className="approval-note" type="password" autoComplete="off" aria-label="研判人身份凭据" value={identityToken} onChange={event => setIdentityToken(event.target.value)} />
        </details>}
      </div>
    </section>
  );
}
