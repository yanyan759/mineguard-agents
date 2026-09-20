"""从当前会话的显式文件选择构造任务，文件保存与参与计算分别确认。"""
import hashlib
import json
import re
from pathlib import Path
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw, parse_csv_records


def session_file(root: Path, thread: str, name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", thread):
        raise ValueError("会话编号格式不合法")
    if not name or any(c in name for c in ('/', '\\', ':', '\x00')) or name in {".", ".."}:
        raise ValueError("仅能选择当前会话中的文件名")
    base = (root/f"session_{thread}").resolve()
    path = (base/name).resolve()
    if not path.is_relative_to(root.resolve()) or path.parent != base:
        raise ValueError("文件不属于当前会话")
    return path


def prepare_file(root: Path, thread: str, name: str, metadata: dict):
    path = session_file(root, thread, name)
    if not path.is_file(): raise ValueError("当前会话不存在所选文件")
    if path.stat().st_size > 20*1024*1024: raise ValueError("单次原始记录文件不得超过20MB")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    if path.suffix.lower() == ".csv":
        task = RawMonitoringTask.model_validate({**metadata, "events": parse_csv_records(text)})
    elif path.suffix.lower() == ".json":
        content = json.loads(text)
        if not isinstance(content, dict): raise ValueError("JSON必须包含监测元数据和events，不能仅传事件数组")
        if metadata: raise ValueError("JSON内已包含元数据，不接受另一份元数据覆盖")
        task = RawMonitoringTask.model_validate(content)
    else: raise ValueError("原始记录仅支持JSON或UTF-8 CSV")
    task.input_files = [name]
    return {"task": task.model_dump(mode="json"), "assessment": analyze_raw(task),
            "file": {"name": name, "sha256": hashlib.sha256(raw).hexdigest()}, "status": "validated"}


def real_sample(number: int):
    """历史标签不进入任务；只读取已核验样本中的事件发生前记录。"""
    if number not in range(1, 6): raise ValueError("样本编号应为1至5")
    folder = Path(__file__).resolve().parents[1]/"output/validation/elkcreek"
    samples = json.loads((folder/"replay_samples.json").read_text(encoding="utf-8"))
    info = samples[number-1]["input"]
    rows = json.loads((folder/f"event_{number}_input.json").read_text(encoding="utf-8"))
    data = {"input_type": "raw_events", "mine_id": "ELK-CREEK", "roadway_id": "STUDY-VOLUME", "area": "NIOSH study volume",
            "source": "NIOSH Elk Creek", "method_id": "elkcreek-hourly-q99-v1",
            "window_start": info["window_start_utc"], "window_end": info["prediction_time_utc"],
            "events": [{"event_id": f"{r['source_file']}:{r['source_row']}", "timestamp": r["time"],
                        "energy": r["energy_total"], "energy_unit": "J", "coordinates": [r["x"],r["y"],r["z"]]} for r in rows]}
    task = RawMonitoringTask.model_validate(data)
    return {"task": task.model_dump(mode="json"), "evaluation_only": samples[number-1]["evaluation_only"],
            "expected_research_alarm": samples[number-1]["computed_alarm"]}


def verify_input_files(root: Path, thread: str, task: RawMonitoringTask):
    """重新核对会话文件和实际事件；编辑后的任务不能冒称未修改的文件证据。"""
    if not task.input_files: return []
    if len(task.input_files) != 1: raise ValueError("当前一次任务仅支持一个原始记录文件")
    name = task.input_files[0]
    metadata = task.model_dump(mode="json", exclude={"events"}) if name.lower().endswith(".csv") else {}
    prepared = prepare_file(root, thread, name, metadata)
    original = RawMonitoringTask.model_validate(prepared["task"])
    keys = ("mine_id", "roadway_id", "area", "source", "window_start", "window_end", "events")
    if any(getattr(original, key) != getattr(task, key) for key in keys):
        raise ValueError("提交记录与原文件不一致；如需编辑为新任务，请清空input_files并注明新来源")
    return [{**prepared["file"], "verified_records":len(task.events), "metadata_source":"user_declared" if metadata else "uploaded_json"}]
