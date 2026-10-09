"""P0 domain acceptance: independent synthetic cases and original candidate guard."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from course_assistant.audit import audit_progress


ROOT = Path(__file__).resolve().parents[1]


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.student = {
            "student_key": "DEMO-STUDENT", "plan_id": "DEMO-PLAN-A", "cohort": 2026,
            "major_or_program": "演示专业", "plan_version": "DEMO-PLAN-v1",
            "plan_binding_status": "student_confirmed", "language_level": "L2",
        }
        self.courses = [
            {"course_id": "DEMO:PY-A", "course_name": "Python程序设计", "plan_id": "DEMO-PLAN-A", "credits": 3, "data_kind": "synthetic", "tags": ["core", "boya"]},
            {"course_id": "DEMO:PY-B", "course_name": "Python程序设计", "plan_id": "DEMO-PLAN-B", "credits": 4, "data_kind": "synthetic", "tags": ["core"]},
            {"course_id": "DEMO:MOD", "course_name": "模块课程", "plan_id": "DEMO-PLAN-A", "credits": 2, "data_kind": "synthetic", "tags": ["module"]},
        ]
        self.record = {
            "record_id": "DEMO-REC", "student_key": "DEMO-STUDENT", "plan_id": "DEMO-PLAN-A",
            "course_id": "DEMO:PY-A", "record_kind": "course", "completion_status": "passed",
            "recognition_status": "not_applicable", "earned_credits": 3, "ledger": "base",
            "attempt_id": "DEMO-ATTEMPT-1", "snapshot_id": "DEMO-ACADEMIC-v1",
            "source_refs": [{"snapshot_id": "DEMO-ACADEMIC-v1"}],
        }
        self.rule = {
            "rule_id": "DEMO-R01", "title": "演示要求", "plan_id": "DEMO-PLAN-A",
            "data_kind": "synthetic", "version": "DEMO-RULES-v1", "status": "demo_active",
            "metric": "credits", "threshold": 3, "ledger": "base",
            "scope": {"cohort": 2026, "program": "演示专业", "version": "DEMO-PLAN-v1"},
            "source_refs": [{"snapshot_id": "DEMO-RULES-v1"}],
        }

    def audit(self, records=None, rules=None, student=None, courses=None, completeness="complete_within_scope"):
        return audit_progress(
            self.student if student is None else student,
            [self.record] if records is None else records,
            [self.rule] if rules is None else rules,
            self.courses if courses is None else courses,
            completeness,
        )

    def test_01_plan_and_near_name_are_isolated(self):
        foreign = deepcopy(self.record)
        foreign.update(record_id="DEMO-FOREIGN", attempt_id="DEMO-FOREIGN-ATTEMPT", plan_id="DEMO-PLAN-B", course_id="DEMO:PY-B", earned_credits=4)
        result = self.audit(records=[foreign])
        self.assertEqual(result["ledger"]["earned_credits"], 0)
        self.assertEqual(result["rules"][0]["result"], "fail")
        self.assertEqual(result["rules"][0]["remaining"], 3)

    def test_01_unknown_binding_never_defaults_to_current_plan(self):
        self.student["plan_binding_status"] = "unknown"
        result = self.audit()
        self.assertEqual(result["profile_status"], "unknown")
        self.assertEqual(result["rules"][0]["result"], "unknown")
        self.assertEqual(result["ledger"]["earned_credits"], 0)

    def test_01_cohort_program_and_plan_version_must_match(self):
        for field, wrong in [("cohort", 2023), ("program", "另一专业"), ("version", "old")]:
            with self.subTest(field=field):
                rule = deepcopy(self.rule)
                rule["scope"][field] = wrong
                self.assertEqual(self.audit(rules=[rule])["rules"][0]["result"], "unknown")

    def test_01_missing_profile_version_is_unknown(self):
        del self.student["plan_version"]
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")

    def test_01_inconsistent_profile_aliases_are_unknown(self):
        self.student["program"] = "矛盾的专业"
        self.assertEqual(self.audit()["profile_status"], "unknown")
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")

    def test_01_missing_record_plan_assignment_is_unknown(self):
        del self.record["plan_id"]
        result = self.audit()
        self.assertEqual(result["rules"][0]["result"], "unknown")
        self.assertEqual(result["ledger"]["earned_credits"], 0)

    def test_02_failed_and_in_progress_do_not_earn_credits(self):
        for status in ["failed", "in_progress"]:
            with self.subTest(status=status):
                record = deepcopy(self.record)
                record["completion_status"] = status
                record["earned_credits"] = None
                result = self.audit(records=[record])
                self.assertEqual(result["ledger"]["earned_credits"], 0)
                self.assertEqual(result["rules"][0]["result"], "fail")
                self.assertEqual(result["ledger"]["in_progress"]["credits"], 3 if status == "in_progress" else 0)

    def test_02_failed_then_retake_pass_and_repeat_import_is_one_credit(self):
        failed = deepcopy(self.record)
        failed.update(record_id="DEMO-FAILED", attempt_id="DEMO-OLD", completion_status="failed", earned_credits=0)
        result = self.audit(records=[failed, self.record, deepcopy(self.record)])
        self.assertEqual(result["ledger"]["earned_credits"], 3)
        self.assertEqual(result["record_summary"]["duplicate_count"], 1)
        self.assertEqual(result["rules"][0]["result"], "pass")

    def test_02_different_record_id_same_attempt_is_idempotent(self):
        other = deepcopy(self.record)
        other.update(record_id="DEMO-NEW-IMPORT", snapshot_id="DEMO-ACADEMIC-v2", source_refs=[{"snapshot_id": "DEMO-ACADEMIC-v2"}])
        result = self.audit(records=[self.record, other])
        self.assertEqual(result["ledger"]["earned_credits"], 3)
        self.assertEqual(result["record_summary"]["duplicate_count"], 1)
        self.assertEqual(len(result["rules"][0]["matched_record_ids"]), 2)

    def test_02_repeated_passes_of_same_course_do_not_multiply_credits(self):
        second_pass = deepcopy(self.record)
        second_pass.update(record_id="DEMO-REPEAT-PASS", attempt_id="DEMO-ATTEMPT-2")
        result = self.audit(records=[self.record, second_pass])
        self.assertEqual(result["ledger"]["earned_credits"], 3)
        self.assertEqual(result["rules"][0]["actual"], 3)

    def test_02_conflicting_duplicate_is_unknown(self):
        conflicting = deepcopy(self.record)
        conflicting["completion_status"] = "failed"
        conflicting["earned_credits"] = 0
        result = self.audit(records=[self.record, conflicting])
        self.assertEqual(result["ledger"]["earned_credits"], 0)
        self.assertEqual(result["rules"][0]["result"], "unknown")
        self.assertIsNone(result["rules"][0]["remaining"])

    def test_02_conflicting_duplicate_with_different_course_remains_unknown(self):
        conflicting = deepcopy(self.record)
        conflicting["course_id"] = "DEMO:MOD"
        rule = deepcopy(self.rule)
        rule["course_pool"] = ["DEMO:MOD"]
        self.assertEqual(self.audit(records=[self.record, conflicting], rules=[rule])["rules"][0]["result"], "unknown")

    def test_02_pending_recognition_is_not_completed(self):
        self.record["recognition_status"] = "pending"
        result = self.audit()
        self.assertEqual(result["ledger"]["earned_credits"], 0)
        self.assertEqual(result["rules"][0]["result"], "unknown")

    def test_02_missing_earned_credit_not_filled_from_catalog(self):
        self.record["earned_credits"] = None
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")
        self.rule["metric"] = "course_count"
        self.rule["threshold"] = 1
        self.assertEqual(self.audit()["rules"][0]["result"], "pass")

    def test_02_partial_record_scope_never_claims_final_gap(self):
        result = self.audit(completeness="partial")
        self.assertEqual(result["rules"][0]["result"], "unknown")
        self.assertEqual(result["rules"][0]["actual"], 3)
        self.assertIsNone(result["rules"][0]["remaining"])
        self.assertFalse(result["ledger"]["is_complete"])

    def test_02_numeric_value_with_unknown_field_state_is_unknown(self):
        self.record["field_states"] = {"earned_credits": {"state": "pending_verification"}}
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")
        self.assertEqual(self.audit()["ledger"]["earned_credits"], 0)

    def test_02_snapshot_version_missing_is_unknown(self):
        del self.record["snapshot_id"]
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")

    def test_03_one_course_covers_two_tag_counts_without_repeated_credits(self):
        rules = []
        for tag in ["core", "boya"]:
            rule = deepcopy(self.rule)
            rule.update(rule_id="DEMO-" + tag, metric="course_count", threshold=1, tag=tag)
            rules.append(rule)
        result = self.audit(rules=rules)
        self.assertEqual([rule["result"] for rule in result["rules"]], ["pass", "pass"])
        self.assertEqual(result["ledger"]["earned_credits"], 3)

    def test_03_hours_points_and_recognition_not_added_to_credits(self):
        labor = deepcopy(self.record)
        labor.update(record_id="DEMO-LABOR", attempt_id="DEMO-LABOR", course_id=None, record_kind="recognition", recognition_status="recognized", earned_credits=2, recognized_hours=32, ledger="recognition", tags=["labor"])
        activity = deepcopy(self.record)
        activity.update(record_id="DEMO-ACTIVITY", attempt_id="DEMO-ACTIVITY", course_id=None, record_kind="activity", recognition_status="recognized", earned_credits=4, classroom_points=9, ledger="second", tags=["activity"])
        hours = deepcopy(self.rule)
        hours.update(rule_id="DEMO-HOURS", metric="hours", threshold=32, ledger="recognition", tag="labor")
        points = deepcopy(self.rule)
        points.update(rule_id="DEMO-POINTS", metric="points", threshold=8, ledger="second")
        result = self.audit(records=[self.record, labor, activity], rules=[hours, points])
        self.assertEqual(result["ledger"]["base"], 3)
        self.assertEqual(result["ledger"]["extra_classrooms"]["second"], 4)
        self.assertEqual(result["ledger"]["earned_credits"], 7)
        self.assertEqual(result["ledger"]["recognized_hours"], 32)
        self.assertEqual(result["ledger"]["classroom_points"], 9)
        self.assertEqual([rule["result"] for rule in result["rules"]], ["pass", "pass"])

    def test_03_missing_ledger_not_assumed_base(self):
        del self.record["ledger"]
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")
        self.assertEqual(self.audit()["ledger"]["earned_credits"], 0)

    def test_03_generic_credit_total_excludes_recognition_copy(self):
        recognition = deepcopy(self.record)
        recognition.update(record_id="DEMO-RECOGNITION", attempt_id="DEMO-RECOGNITION", course_id=None, record_kind="recognition", recognition_status="recognized", earned_credits=2, ledger="recognition")
        self.rule.pop("ledger")
        self.rule["threshold"] = 5
        result = self.audit(records=[self.record, recognition])
        self.assertEqual((result["rules"][0]["actual"], result["rules"][0]["remaining"]), (3, 2))
        self.assertEqual(result["ledger"]["recognition_credits"], 2)

    def test_03_unverified_tag_does_not_count_as_confirmed(self):
        self.record["recognition_tags"] = [{"tag": "core", "status": "pending"}]
        self.rule.update(metric="course_count", threshold=1, tag="core")
        result = self.audit()
        self.assertEqual(result["rules"][0]["result"], "unknown")
        self.assertEqual(result["ledger"]["earned_credits"], 3)

    def test_03_real_course_path_not_inferred_as_recognition_tag(self):
        self.courses[0].update(data_kind="curriculum_course_sample_not_current_offering", requirement_path=["core"])
        self.rule.update(metric="course_count", threshold=1, tag="core")
        self.assertEqual(self.audit()["rules"][0]["result"], "unknown")

    def test_05_all_original_candidates_are_guarded(self):
        path = ROOT / "inputs/zju_course_agent_handoff_v1/data/requirement_candidates.json"
        rules = json.loads(path.read_text(encoding="utf-8"))["records"]
        original = deepcopy(rules)
        result = self.audit(rules=rules)
        self.assertEqual(len(result["rules"]), 23)
        self.assertTrue(all(rule["result"] == "unknown" and rule["actual"] is None for rule in result["rules"]))
        self.assertEqual(rules, original)
        self.assertTrue(all(rule["enforce_automatically"] is False for rule in rules))

    def test_05_review_guard_cannot_be_bypassed_by_demo_label(self):
        for blocker in [{"enforce_automatically": False}, {"human_review_required": True}, {"candidate_expression": {"minimum_credits": 3}}]:
            with self.subTest(blocker=blocker):
                rule = deepcopy(self.rule)
                rule.update(blocker)
                self.assertEqual(self.audit(rules=[rule])["rules"][0]["result"], "unknown")

    def test_05_source_issue_and_unknown_language_stay_unknown(self):
        issue = deepcopy(self.rule)
        issue["linked_issue_ids"] = ["DEMO-SRC-15-16"]
        language = deepcopy(self.rule)
        language.update(conditional_threshold={"field": "language_level", "values": {"L1": 6, "L2": 4.5, "L3": 3}})
        self.student["language_level"] = None
        result = self.audit(rules=[issue, language])
        self.assertEqual([rule["result"] for rule in result["rules"]], ["unknown", "unknown"])
        self.assertEqual(result["rules"][0]["linked_issue_ids"], ["DEMO-SRC-15-16"])

    def test_05_conditional_threshold_uses_explicit_level(self):
        self.rule["conditional_threshold"] = {"field": "language_level", "values": {"L1": 6, "L2": 4.5, "L3": 3}}
        result = self.audit()["rules"][0]
        self.assertEqual((result["result"], result["threshold"], result["remaining"]), ("fail", 4.5, 1.5))

    def test_05_invalid_numeric_values_remain_unknown(self):
        for value in [None, -1, True, "NaN", "Infinity", "not a number"]:
            with self.subTest(value=value):
                record = deepcopy(self.record)
                record["earned_credits"] = value
                self.assertEqual(self.audit(records=[record])["rules"][0]["result"], "unknown")

    def test_sources_and_versions_are_retained_and_inputs_unchanged(self):
        before = deepcopy((self.student, self.record, self.rule, self.courses))
        result = self.audit()
        self.assertEqual(result["versions"]["rule_versions"], ["DEMO-RULES-v1"])
        self.assertEqual(result["versions"]["academic_snapshot_ids"], ["DEMO-ACADEMIC-v1"])
        self.assertEqual(len(result["rules"][0]["source_refs"]), 2)
        self.assertEqual(before, (self.student, self.record, self.rule, self.courses))
        json.dumps(result, allow_nan=False)

    def test_demo_bundle_has_manually_expected_accounting(self):
        bundle = json.loads((ROOT / "demo/bundle.json").read_text(encoding="utf-8"))
        result = audit_progress(bundle["student"], bundle["academic_snapshot"]["records"], bundle["rules"], bundle["courses"], bundle["academic_snapshot"]["completeness"])
        self.assertEqual(result["ledger"]["base"], 8)
        self.assertEqual(result["ledger"]["earned_credits"], 12)
        self.assertEqual(result["ledger"]["in_progress"]["credits"], 3)
        by_id = {rule["rule_id"]: rule for rule in result["rules"]}
        self.assertEqual((by_id["DEMO-MODULE"]["actual"], by_id["DEMO-MODULE"]["remaining"]), (3, 5))
        self.assertEqual(by_id["DEMO-LABOUR"]["result"], "unknown")
        self.assertTrue(by_id["DEMO-LABOUR"]["unknown_record_ids"])
        self.assertEqual(by_id["DEMO-SECOND-POINTS"]["actual"], 5)


if __name__ == "__main__":
    unittest.main()
