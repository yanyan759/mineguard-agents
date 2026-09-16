"""演示场景数据源加载器。

从测试目录加载 7 套固定场景，供六 Agent 工作流离线验证使用。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.workflow import (
    PerceptionWarningInput,
    ResourceSnapshot,
    SimulationCase,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

_FIXTURE_FILES = {
    "normal": "scenario_001_normal.json",
    "yellow": "scenario_002_yellow.json",
    "red": "scenario_003_red.json",
    "missing_knowledge": "scenario_004_missing_knowledge.json",
    "resource_insufficient": "scenario_005_resource_insufficient.json",
    "reflection_rollback": "scenario_006_reflection_rollback.json",
    "max_iterations": "scenario_007_max_iterations.json",
}


class FixtureDataSource:
    """加载 7 套固定场景作为演示数据源。

    用法：
        loader = FixtureDataSource()
        for scenario_id in loader.list_scenarios():
            inp = loader.load_scenario(scenario_id)
            snapshot = loader.get_resource_snapshot(scenario_id)
    """

    def __init__(self, fixtures_dir: Path | None = None):
        self._dir = fixtures_dir or FIXTURES_DIR
        self._cache: dict[str, SimulationCase] = {}

    def list_scenarios(self) -> list[str]:
        """返回所有可用的 fixture 场景 ID 列表。"""
        return list(_FIXTURE_FILES.keys())

    def scenario_label(self, scenario_id: str) -> str:
        """返回场景描述。"""
        case = self._load(scenario_id)
        return case.description

    def load_scenario(self, scenario_id: str) -> PerceptionWarningInput:
        """加载指定场景的感知预警输入数据。"""
        case = self._load(scenario_id)
        return case.perception_input

    def get_expected_result(self, scenario_id: str):
        """返回指定场景的预期感知预警结果。"""
        case = self._load(scenario_id)
        return case.expected_warning

    def get_resource_snapshot(
        self, scenario_id: str
    ) -> ResourceSnapshot | None:
        """返回指定场景的资源快照（部分场景可能无资源数据）。"""
        case = self._load(scenario_id)
        return case.resource_snapshot

    def _load(self, scenario_id: str) -> SimulationCase:
        """懒加载 + 缓存 fixture JSON 为 Pydantic 对象。"""
        if scenario_id in self._cache:
            return self._cache[scenario_id]

        filename = _FIXTURE_FILES.get(scenario_id)
        if filename is None:
            raise KeyError(
                f"未知场景 '{scenario_id}'，可用：{list(_FIXTURE_FILES.keys())}"
            )

        filepath = self._dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Fixture 文件不存在: {filepath}")

        raw = json.loads(filepath.read_text(encoding="utf-8"))
        case = SimulationCase(**raw)
        self._cache[scenario_id] = case
        return case
