import { RISK_LEVEL_META } from "../types";
import type { RiskLevel } from "../types";

interface RiskBadgeProps {
  level: RiskLevel;
  score?: number;
}

/**
 * 风险等级徽章
 * 蓝/黄/橙/红四档预警可视化，随风险等级着色并显示评分。
 */
export function RiskBadge({ level, score }: RiskBadgeProps) {
  const meta = RISK_LEVEL_META[level] ?? RISK_LEVEL_META.normal;
  return (
    <span
      className={`risk-badge risk-badge--${level}`}
      style={{ borderColor: meta.hex, color: meta.hex }}
    >
      <i className="risk-badge-dot" style={{ background: meta.hex }} aria-hidden />
      <span>{meta.label}</span>
      {typeof score === "number" ? <em>评分 {score.toFixed(2)}</em> : null}
    </span>
  );
}
