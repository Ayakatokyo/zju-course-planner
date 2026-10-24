import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.importer import ImportErrorDetail, digest, read_sources, validate_demo
from course_assistant.server import Application


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name) / "test.sqlite3", mock_enabled=True)
        token, _ = self.app.auth.login_mock({"profile_id": "demo-a"})
        self.context = self.app.auth.authenticate(token)
        self.demo = self.app.workspace(self.context)["demo"]

    def post(self, path, body):
        return self.app.post(path, body, self.context)

    def audit(self, body):
        return self.app.audit(body, self.context)

    def generation(self):
        return self.post("/api/generate", {"constraints": {"friday_free": "soft"}})

    def test_school_only_start_does_not_load_demo_sources_fixture_or_chat(self):
        from tests.test_school_http_app import SyntheticSchool
        from tests.test_personal_dialogue import ScriptedModel
        model = ScriptedModel([])
        with patch("course_assistant.server.read_sources", side_effect=AssertionError("demo source read")), \
                patch("course_assistant.server.load_json", side_effect=AssertionError("demo fixture read")), \
                patch("course_assistant.chat.ChatService", side_effect=AssertionError("demo chat initialized")):
            app = Application(Path(self.temp.name) / "school-only.sqlite3", model=model,
                school_client_factory=SyntheticSchool, school_root=Path(self.temp.name).resolve() / "private" / "school")
            self.addCleanup(app.close)
            self.assertIs(app.personal_chat.model, model)
            self.assertIsNone(app.chat)
            self.assertIsNone(app.fixture)
            self.assertIsNone(app.source)
            self.assertTrue(app.get("/api/health")["school_client_enabled"])
            self.assertFalse(app.auth.mock_enabled)
        self.assertIsNotNone(self.app.chat)
        self.assertIsNotNone(self.app.fixture)
        self.assertIs(self.app.personal_chat.model, self.app.chat.model)

    def test_source_integrity_and_counts(self):
        source = read_sources()
        self.assertEqual(source["counts"], {"plans": 3, "courses": 19, "candidates": 23, "issues": 12,
                                           "actual_offerings": 0, "import_entities": 11})
        self.assertEqual(source["integrity"]["files_checked"], 17)
        self.assertEqual(source["empty_offering_snapshot"]["records"], [])
        result = self.audit({"mode": "source"})
        self.assertTrue(all(rule["result"] == "unknown" for rule in result["rules"]))

    def test_import_is_idempotent_and_rejects_unmarked_simulation(self):
        result = self.app.store.import_dataset(self.context, self.demo)
        self.assertFalse(result["inserted"])
        demo = copy.deepcopy(self.demo)
        demo["metadata"]["data_kind"] = "official"
        with self.assertRaises(ImportErrorDetail):
            self.post("/api/import", {"bundle": demo})

    def test_bad_reference_and_nonfinite_credit_rejected(self):
        for field, value in (("course_id", "DEMO:missing"), ("credits", float("nan")), ("capacity", -1)):
            demo = copy.deepcopy(self.demo)
            demo["offering_snapshot"]["records"][0][field] = value
            with self.assertRaises(ImportErrorDetail):
                validate_demo(demo, self.app.source)

    def test_generate_save_and_export_freeze_data(self):
        before = digest(self.demo)
        result = self.generation()
        self.assertEqual(len(result["plans"]), 3)
        for plan in result["plans"]:
            self.assertEqual(plan["validation"]["result"], "pass")
            self.assertIn("DEMO-PE-A", plan["section_ids"])
        plan = result["plans"][0]
        projected = next(r for r in plan["expected_rule_contributions"] if r["rule_id"] == "DEMO-MODULE")
        self.assertEqual(projected["actual"], 8)
        self.assertEqual(self.audit({})["ledger"]["base"], 8)
        saved = self.post("/api/save", {"plan": plan, "constraints": result["constraints"]})
        self.assertEqual(saved["status"], "saved_planning_only")
        self.assertTrue(saved["not_official_enrollment"])
        exported, _, _ = self.post("/api/export", {"saved_id": saved["saved_id"], "format": "json"})
        frozen = json.loads(exported)
        self.assertEqual(frozen["versions"]["dataset_sha256"], before)
        self.assertEqual(digest(frozen["frozen_dataset"]), before)
        self.assertEqual(digest(self.demo), before)

    def test_stale_result_is_not_saved_as_current(self):
        result = self.generation()
        plan = result["plans"][0]
        saved = self.post("/api/save", {"plan": plan, "constraints": result["constraints"]})
        refreshed = copy.deepcopy(self.demo)
        refreshed["metadata"]["version"] = "DEMO-DATA-v2"
        snapshot = refreshed["offering_snapshot"]
        snapshot["version"] = "DEMO-OFFERINGS-v2"
        snapshot["records"][1]["available_seats"] = 0
        self.post("/api/import", {"bundle": refreshed})
        with self.assertRaises(ImportErrorDetail):
            self.post("/api/save", {"plan": plan, "constraints": result["constraints"]})
        history = self.post("/api/export", {"saved_id": saved["saved_id"], "format": "json"})
        self.assertEqual(json.loads(history[0])["versions"]["dataset_sha256"], saved["versions"]["dataset_sha256"])
        new = self.generation()
        self.assertTrue(all("DEMO-ML-A" not in p["section_ids"] for p in new["plans"]))

    def test_server_keeps_locks_on_preference_upgrade(self):
        self.post("/api/generate", {"constraints": {"locked_section_ids": ["DEMO-ML-B"]}})
        result = self.post("/api/generate", {"constraints": {"friday_free": "hard", "locked_section_ids": []}})
        self.assertIn("DEMO-ML-B", result["constraints"]["locked_section_ids"])
        self.assertIn("DEMO-PE-A", result["constraints"]["locked_section_ids"])
        self.assertFalse(result["plans"])
        self.assertTrue(result["diagnostics"])

    def test_csv_and_ics_keep_synthetic_and_calendar_scope(self):
        result = self.generation()
        body = {"plan": result["plans"][0], "constraints": result["constraints"]}
        data, _, _ = self.post("/api/export", {**body, "format": "csv"})
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertTrue(all(row[0] == "synthetic" and row[1] == "规划草案/非中选" for row in rows[1:]))
        data, _, _ = self.post("/api/export", {**body, "format": "ics"})
        self.assertIn(b"STATUS:TENTATIVE", data)
        self.assertIn(b"DTSTART;TZID=Asia/Shanghai:20261130", data)
        self.assertTrue(all(len(line) <= 75 for line in data.split(b"\r\n")))
        self.demo["academic_calendar"]["period_times"] = {}
        self.demo["offering_snapshot"]["academic_calendar"] = self.demo["academic_calendar"]
        self.post("/api/import", {"bundle": self.demo})
        # The old plan is stale because the underlying calendar changed.
        with self.assertRaises(ImportErrorDetail):
            self.post("/api/export", {**body, "format": "ics"})


if __name__ == "__main__":
    unittest.main()
