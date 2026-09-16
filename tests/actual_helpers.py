"""验收专用配置；不写入用户矿井配置库，不作为现场参数。"""
import os
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from app.services.mine_configuration import ConfigurationStore, MineConfiguration
from app.services.monitoring_task import parse_monitoring_task


@contextmanager
def configured_summary(query):
    task=parse_monitoring_task(query)
    with TemporaryDirectory() as folder, patch.dict(os.environ,{"MINE_CONFIGURATION_DB":str(Path(folder)/"config.sqlite3")}):
        ConfigurationStore().save(MineConfiguration(mine_id=task.mine_id,roadway_id=task.roadway_id,area=task.area,
            version="acceptance-only",source="仅用于接口验收",valid_from="2020-01-01T00:00:00Z",valid_to="2100-01-01T00:00:00Z",
            window_minutes=task.window_minutes,recheck_minutes=25,
            rules=[dict(metric="max_energy_j",operator=">=",threshold=200000,level="red")]))
        yield
