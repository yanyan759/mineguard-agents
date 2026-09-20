"""持久化运行快照与人工反馈，保留原结果并建立父子复核链。"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw
from app.services.monitoring_task import parse_monitoring_task
from datetime import timedelta


class RunArchive:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS feedback (parent TEXT, request TEXT, body TEXT, record TEXT, PRIMARY KEY(parent, request))")
        conn.execute("CREATE TABLE IF NOT EXISTS field_approvals (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save_run(self, run_id, body):
        # 终态结果只存一次，复核必须产生新run，不能覆盖历史结论。
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO runs VALUES (?,?)", (run_id, json.dumps(body, ensure_ascii=False)))

    def get_run(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row: raise KeyError(run_id)
        return json.loads(row[0])

    def field_approval(self, run_id, body=None):
        """审批作为独立审计记录，不覆盖初始六节点结果。"""
        with self.connect() as conn:
            if body is not None:
                conn.execute("INSERT OR IGNORE INTO field_approvals VALUES (?,?)",(run_id,json.dumps(body,ensure_ascii=False)))
            row=conn.execute("SELECT body FROM field_approvals WHERE run_id=?",(run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def reserve_feedback(self, parent, request_id, body):
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT body,record FROM feedback WHERE parent=? AND request=?", (parent, request_id)).fetchone()
            if row:
                if row[0] != canonical: raise ValueError("相同请求标识的反馈内容冲突")
                return json.loads(row[1]), False
            record = {"feedback_id": str(uuid.uuid4()), "parent_run_id": parent, "child_run_id": f"wf-{uuid.uuid4().hex[:12]}",
                      "status": "recorded", "source": "human_report_unverified", "created_at": datetime.now(timezone.utc).isoformat()}
            conn.execute("INSERT INTO feedback VALUES (?,?,?,?)", (parent, request_id, canonical, json.dumps(record)))
            return record, True

    def get_feedback(self, feedback_id):
        with self.connect() as conn:
            row = conn.execute("SELECT record,body FROM feedback WHERE json_extract(record,'$.feedback_id')=?", (feedback_id,)).fetchone()
        if not row: raise KeyError(feedback_id)
        return json.loads(row[0]), json.loads(row[1])


archive = RunArchive(Path(__file__).resolve().parents[1]/"output/workflow_archive.sqlite3")


def observation_window(task):
    if getattr(task,"window_end",None) is None:
        raise ValueError("反馈摘要必须提供带时区的 window_end")
    return (task.window_start if isinstance(task,RawMonitoringTask) else task.window_end-timedelta(minutes=task.window_minutes),task.window_end)


def validate_feedback(parent, request):
    """人工报告必须对应原动作、同一测区和之后的监测窗口。"""
    if parent["result"]["status"] != "completed": raise ValueError("原任务尚未完成")
    original = parse_monitoring_task(json.dumps(parent["input"],ensure_ascii=False))
    new = parse_monitoring_task(json.dumps(request["observation"],ensure_ascii=False))
    if new.configuration_version is None:
        new.configuration_version = original.configuration_version
    if any(getattr(original, key) != getattr(new, key) for key in ("mine_id", "roadway_id", "area")):
        raise ValueError("反馈矿井、巷道或测区与原运行不一致")
    _,original_end=observation_window(original)
    new_start,_=observation_window(new)
    if new_start < original_end: raise ValueError("新观测窗口不得早于原监测窗口结束")
    actions = parent["result"]["agents"].get("decision_result", {}).get("primary_plan", {}).get("actions", [])
    ids = {a["sequence"] for a in actions}
    seen = set()
    for action in request.get("actions", []):
        if not isinstance(action, dict) or not {"sequence", "status", "occurred_at"}.issubset(action):
            raise ValueError("动作报告须包含sequence、status、occurred_at")
        if not isinstance(action["sequence"], int) or not isinstance(action["occurred_at"], str):
            raise ValueError("动作序号应为整数，发生时间应为带时区文本")
        if action["sequence"] not in ids or action["sequence"] in seen: raise ValueError("反馈动作不存在或重复")
        seen.add(action["sequence"])
        if action["status"] not in {"completed", "failed", "not_executed"}: raise ValueError("执行状态无效")
        occurred = datetime.fromisoformat(action["occurred_at"].replace("Z", "+00:00"))
        if occurred.tzinfo is None or not original_end <= occurred <= new_start:
            raise ValueError("执行时间必须在原窗口之后、新观测之前，并包含时区")
    if not request.get("reported_by", "").strip(): raise ValueError("请填写反馈记录人；记录不等同身份核验")
    return new


def compare_observations(parent, new, assessment=None):
    original = parse_monitoring_task(json.dumps(parent["input"],ensure_ascii=False))
    before = parent["result"]["agents"]["perception_result"]["assessment"]
    if assessment is not None:
        after=assessment
    elif isinstance(new,RawMonitoringTask):
        after=analyze_raw(new)
    else:
        from app.services.mine_configuration import prepare_actual
        after=prepare_actual(new)["assessment"]
    reasons = []
    for key in ("mine_id", "roadway_id", "area", "source", "method_id", "b_method"):
        if getattr(original, key, None) != getattr(new, key, None): reasons.append(f"{key}不一致")
    if type(original) is not type(new): reasons.append("原始记录与摘要统计口径不同")
    original_start,original_end=observation_window(original)
    new_start,new_end=observation_window(new)
    if original_end-original_start != new_end-new_start: reasons.append("统计窗口长度不一致")
    if before.get("time_source") == "submission_time" or after.get("time_source") == "submission_time":
        reasons.append("摘要未提供实测窗口截止时间")
    if original.missing_rate != new.missing_rate: reasons.append("设备漏采/缺失率口径不一致")
    if before["profile"] != after["profile"]: reasons.append("参数快照不一致")
    if before["applicability"] != "applicable" or after["applicability"] != "applicable": reasons.append("方法不适用")
    changes = {}
    if not reasons:
        for key in ("event_count", "max_energy_j", "total_energy_j", "frequency_per_hour", "b_value"):
            left, right = before["metrics"].get(key), after["metrics"].get(key)
            if left is not None and right is not None: changes[key] = {"before": left, "after": right, "delta": right-left}
    return {"comparable": not reasons, "reasons": reasons, "changes": changes, "causal_effect_verified": False,
            "caveats": ["仅比较目录观测；设备在线率及现场效果未验证", "人工报告不等同可信执行回执", "指标下降不能自动解除风险或批准复工"]}
