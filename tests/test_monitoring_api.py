"""文件输入归属、JSON/CSV一致性和会话路径限制。"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.monitoring_files import prepare_file, session_file
from test_raw_monitoring import sample


class FileTests(unittest.TestCase):
    def test_modified_records_cannot_claim_uploaded_file_as_evidence(self):
        from app.services.monitoring_files import verify_input_files
        from app.services.raw_monitoring import RawMonitoringTask
        with TemporaryDirectory() as folder:
            root = Path(folder); path = session_file(root, "test", "data.json")
            path.parent.mkdir(); path.write_text(json.dumps(sample()), encoding="utf-8")
            task = RawMonitoringTask.model_validate(prepare_file(root,"test","data.json",{})["task"])
            self.assertEqual(len(verify_input_files(root,"test",task)),1)
            task.events[0].energy = 100
            with self.assertRaises(ValueError): verify_input_files(root,"test",task)

    def test_uploaded_json_is_actual_task_input(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); p = session_file(root, "test-thread", "data.json")
            p.parent.mkdir(); p.write_text(json.dumps(sample()), encoding="utf-8")
            result = prepare_file(root, "test-thread", "data.json", {})
            self.assertEqual(result["assessment"]["metrics"]["max_energy_j"], 3000)
            self.assertEqual(result["task"]["input_files"], ["data.json"])

    def test_traversal_and_cross_session_paths_rejected(self):
        with TemporaryDirectory() as folder:
            for thread, name in [("../other", "data.json"), ("mine", "../other/data.json"), ("mine", "E:\\secrets.json")]:
                with self.assertRaises(ValueError): session_file(Path(folder), thread, name)

    def test_csv_requires_metadata_and_preserves_units(self):
        with TemporaryDirectory() as folder:
            root = Path(folder); p = session_file(root, "test", "data.csv")
            p.parent.mkdir(); p.write_text("event_id,timestamp,energy,energy_unit\na,2020-01-01T00:20:00Z,4,kJ\n", encoding="utf-8")
            with self.assertRaises(ValueError): prepare_file(root, "test", "data.csv", {})
            meta = sample(); meta.pop("events")
            self.assertEqual(prepare_file(root, "test", "data.csv", meta)["assessment"]["metrics"]["max_energy_j"], 4000)


if __name__ == "__main__": unittest.main()
