"""Frozen exports preserve unknowns and never invent a teaching calendar."""

from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import unittest

from course_assistant.exporter import export_result
from course_assistant.importer import ImportErrorDetail, digest
from course_assistant.planner import validate_plan


ROOT = Path(__file__).resolve().parents[1]


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((ROOT / "demo" / "bundle.json").read_text(encoding="utf-8"))
        self.ids = ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"]

    def frozen(self):
        snapshot = self.bundle["offering_snapshot"]
        validation = validate_plan(self.ids, snapshot, self.bundle["student"],
                                   self.bundle["constraints"], self.bundle["academic_snapshot"]["records"])
        lookup = {item["section_id"]: item for item in snapshot["records"]}
        return {"data_kind": "synthetic", "not_official_enrollment": True,
                "plan": {**validation, "validation": validation,
                         "sections": [deepcopy(lookup[item]) for item in self.ids]},
                "constraints": deepcopy(self.bundle["constraints"]),
                "versions": {"offering_snapshot": snapshot["version"], "rule_version": "DEMO-RULES-v1",
                             "dataset_sha256": digest(self.bundle)},
                "frozen_dataset": deepcopy(self.bundle)}

    def test_json_freezes_exact_inputs_and_export_does_not_mutate_them(self):
        result = self.frozen()
        before = deepcopy(result)
        data, content_type, suffix = export_result(result, "json")
        self.assertEqual(json.loads(data), before)
        self.assertEqual(result, before)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(suffix, "json")

    def test_csv_preserves_sources_versions_unknowns_and_escapes_formulas(self):
        section = self.bundle["offering_snapshot"]["records"][1]
        section["course_name"] = "=HYPERLINK(fake)"
        section["exam_state"] = "unknown"
        result = self.frozen()
        data, _, _ = export_result(result, "csv")
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        row = next(item for item in rows if item["教学班"] == "DEMO-ML-A")
        self.assertEqual(row["课程"], "'=HYPERLINK(fake)")
        self.assertEqual(row["数据模式"], "synthetic")
        self.assertEqual(row["偏好版本"], "1")
        self.assertEqual(json.loads(row["冻结输入版本"])["snapshot_version"], result["versions"]["offering_snapshot"])
        self.assertTrue(json.loads(row["来源"]))
        self.assertTrue(any(item["code"] == "exam_schedule" for item in json.loads(row["待确认与冲突"])))
        self.assertEqual(json.loads(row["考试安排"])["state"], "unknown")

    def test_ics_uses_only_explicit_dates_and_keeps_unknown_exam_warning(self):
        self.bundle["offering_snapshot"]["records"][1]["exam_state"] = "unknown"
        result = self.frozen()
        data, _, suffix = export_result(result, "ics")
        unfolded = data.replace(b"\r\n ", b"").decode()
        self.assertEqual(suffix, "ics")
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20261130T080000", unfolded)
        self.assertIn("STATUS:TENTATIVE", unfolded)
        self.assertIn("X-COURSE-INPUT-VERSIONS:", unfolded)
        self.assertIn("exam_schedule", unfolded)
        self.assertIn("X-COURSE-VALIDATION:unknown", unfolded)
        self.assertTrue(all(len(line) <= 75 for line in data.split(b"\r\n")))

    def test_ics_rejects_missing_or_invalid_calendar_instead_of_inventing_dates(self):
        original = self.frozen()
        for mutation in ("missing_period", "not_monday", "clock_invalid", "calendar_missing", "calendar_null", "real_meeting"):
            result = deepcopy(original)
            calendar = result["frozen_dataset"]["academic_calendar"]
            if mutation == "missing_period":
                calendar["period_times"] = {}
            elif mutation == "not_monday":
                calendar["season_first_mondays"]["冬"] = "2026-12-01"
            elif mutation == "clock_invalid":
                calendar["period_times"]["7"] = ["16:35", "15:00"]
            elif mutation == "calendar_missing":
                result["frozen_dataset"].pop("academic_calendar")
            elif mutation == "calendar_null":
                result["frozen_dataset"]["academic_calendar"] = None
            else:
                result["plan"]["sections"][0]["meetings"][0]["data_kind"] = "reference_sample"
            with self.subTest(mutation=mutation), self.assertRaises(ImportErrorDetail):
                export_result(result, "ics")

    def test_ics_exports_known_exams_and_fixed_meetings_with_no_line_injection(self):
        section = self.bundle["offering_snapshot"]["records"][1]
        section.update(exam_state="known", exams=[{
            "exam_id": "DEMO-EXAM", "exam_date": "2026-12-01", "start_time_local": "18:00",
            "end_time_local": "19:00", "timezone": "Asia/Shanghai", "data_kind": "synthetic",
            "location_raw": "演示考场\r\nBEGIN:EVIL", "source_refs": [{"snapshot_id": "DEMO-EXAM-v1"}]}])
        meeting = deepcopy(self.bundle["offering_snapshot"]["records"][0]["meetings"][0])
        meeting.update(meeting_id="DEMO-FIXED", title="读书安排", day_of_week=7)
        self.bundle["constraints"]["fixed_meetings"] = [meeting]
        data, _, _ = export_result(self.frozen(), "ics")
        unfolded = data.replace(b"\r\n ", b"").decode()
        self.assertIn("[synthetic 考试]", unfolded)
        self.assertIn("DTSTART:20261201T100000Z", unfolded)
        self.assertIn("[synthetic 固定安排] 读书安排", unfolded)
        self.assertNotIn("\r\nBEGIN:EVIL", unfolded)
        self.assertIn("\\nBEGIN:EVIL", unfolded)

    def test_nonfinite_frozen_values_are_not_exported_as_valid_json_or_csv(self):
        result = self.frozen()
        result["frozen_dataset"]["offering_snapshot"]["records"][0]["credits"] = float("nan")
        for format in ("json", "csv", "ics"):
            with self.subTest(format=format), self.assertRaises(ImportErrorDetail):
                export_result(result, format)


if __name__ == "__main__":
    unittest.main()
