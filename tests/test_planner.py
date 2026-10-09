"""Acceptance checks against explicitly synthetic, hand-reviewed schedules."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from course_assistant.planner import (
    compare_plans, confirmed_enrollment, find_sections, generate_plans, revise_constraints,
    validate_plan,
)


ROOT = Path(__file__).resolve().parents[1]


class PlannerTests(unittest.TestCase):
    def setUp(self):
        bundle = json.loads((ROOT / "demo" / "bundle.json").read_text(encoding="utf-8"))
        self.snapshot = deepcopy(bundle["offering_snapshot"])
        self.student = deepcopy(bundle["student"])
        self.constraints = deepcopy(bundle["constraints"])
        self.records = deepcopy(bundle["academic_snapshot"]["records"])

    def section(self, section_id):
        return next(r for r in self.snapshot["records"] if r["section_id"] == section_id)

    def validate(self, section_ids=None):
        return validate_plan(section_ids or ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"],
                             self.snapshot, self.student, self.constraints, self.records)

    def generate(self, **kwargs):
        return generate_plans(self.snapshot, self.student, self.constraints, self.records, **kwargs)

    def check(self, result, code, expected):
        self.assertTrue(any(c["code"] == code and c["result"] == expected for c in result["checks"]), result)

    def test_three_ranked_plans_preserve_preloaded_and_versions(self):
        result = self.generate()
        self.assertEqual(result["status"], "feasible")
        self.assertEqual(len(result["plans"]), 3)
        self.assertEqual(result["search"]["validated_plan_count"], 9)
        for plan in result["plans"]:
            self.assertEqual(plan["result"], "pass")
            self.assertIn("DEMO-PE-A", plan["section_ids"])
            self.assertEqual(plan["total_credits"], 6)
            self.assertIsNone(plan["earned_credits"])
            self.assertEqual(plan["preference_scores"]["friday_penalty"], 0)
            self.assertEqual(plan["provenance"]["data_kind"], "synthetic")
            self.assertEqual(plan["provenance"]["snapshot_version"], "DEMO-OFFERINGS-v1")
        self.assertFalse(result["provenance"]["enrollment_guaranteed"])

    def test_find_sections_uses_identifiers_and_reports_scope(self):
        result = find_sections(self.snapshot, ["zju:MSE3123M", "missing"])
        self.assertEqual(len(result["by_course"]["zju:MSE3123M"]), 3)
        self.assertEqual(result["missing_course_ids"], ["missing"])
        self.assertEqual(result["provenance"]["filter_scope"], self.snapshot["filter_scope"])

    def test_odd_and_even_actual_weeks_do_not_overlap(self):
        left = self.section("DEMO-ML-A")["meetings"][1]
        right = self.section("DEMO-SIM-A")["meetings"][1]
        right.update(day_of_week=3, period_indices=[3, 4], week_numbers=[2, 4, 6, 8])
        self.assertEqual(self.validate()["result"], "pass")
        right["week_numbers"] = [1, 2]
        result = self.validate()
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["conflicts"][0]["overlap"]["week_numbers"], [1])

    def test_disjoint_seasons_do_not_overlap_and_multiple_meetings_are_checked(self):
        # ML-C冬 Tue3/4 and SIM-A秋 Tue3/4 have disjoint seasons.
        self.assertEqual(self.validate(["DEMO-PE-A", "DEMO-ML-C", "DEMO-SIM-A"])["result"], "pass")
        self.section("DEMO-SIM-A")["meetings"][0]["season_codes"] = ["冬"]
        self.assertEqual(self.validate(["DEMO-PE-A", "DEMO-ML-C", "DEMO-SIM-A"])["result"], "fail")

    def test_different_calendar_week_numbers_remain_unknown(self):
        self.section("DEMO-SIM-A")["meetings"][0]["academic_calendar_id"] = "OTHER-CALENDAR"
        result = self.validate()
        self.assertEqual(result["result"], "unknown")
        self.check(result, "time_conflict", "unknown")

    def test_missing_meetings_does_not_pass(self):
        self.section("DEMO-ML-A")["meetings"] = []
        result = self.validate()
        self.assertEqual(result["result"], "unknown")
        self.check(result, "schedule", "unknown")

    def test_missing_prerequisites_eligibility_and_identity_are_unknown(self):
        self.section("DEMO-ML-A")["prerequisite_course_ids"] = None
        self.section("DEMO-ML-A")["eligibility"] = None
        self.student["plan_binding_status"] = "unknown"
        result = self.validate()
        self.assertEqual(result["result"], "unknown")
        for code in ("plan_binding", "prerequisites", "eligibility"):
            self.check(result, code, "unknown")

    def test_prerequisite_respects_student_plan_recognition_and_conflicts(self):
        self.section("DEMO-ML-A")["prerequisite_course_ids"] = ["zju:CS1006G"]
        # A repeated export's new provenance is not contradictory completion.
        self.records[-1]["snapshot_id"] = "DEMO-REIMPORT-v2"
        self.records[-1]["source_refs"] = [{"snapshot_id": "DEMO-REIMPORT-v2"}]
        self.assertEqual(self.validate()["result"], "pass")
        for record in self.records:
            if record.get("completion_status") == "passed" and record.get("course_id") == "zju:CS1006G":
                record["plan_id"] = "other-plan"
        self.check(self.validate(), "prerequisites", "unknown")
        self.student["academic_record_completeness"] = "complete_within_scope"
        self.check(self.validate(), "prerequisites", "fail")
        for record in self.records:
            if record.get("completion_status") == "passed" and record.get("course_id") == "zju:CS1006G":
                record["plan_id"] = self.student["plan_id"]
                record["recognition_status"] = "pending"
        self.check(self.validate(), "prerequisites", "unknown")
        for record in self.records:
            if record.get("completion_status") == "passed" and record.get("course_id") == "zju:CS1006G":
                record["recognition_status"] = "not_applicable"
        self.records[-1]["completion_status"] = "failed"
        self.check(self.validate(), "prerequisites", "unknown")

    def test_unknown_zero_and_stale_seats_are_distinct(self):
        section = self.section("DEMO-ML-A")
        section["available_seats"] = None
        self.check(self.validate(), "availability", "unknown")
        section["available_seats"] = 0
        self.check(self.validate(), "availability", "fail")
        self.snapshot["freshness_state"] = "stale"
        result = self.validate()
        self.assertEqual(result["result"], "unknown")
        self.check(result, "availability", "unknown")

    def test_expiration_is_reproducible_with_as_of(self):
        self.snapshot["expires_at"] = "2026-10-10T00:00:00+08:00"
        self.constraints["as_of"] = "2026-10-09T12:00:00+08:00"
        self.assertEqual(self.validate()["result"], "pass")
        self.constraints["as_of"] = "2026-10-11T12:00:00+08:00"
        self.assertEqual(self.validate()["result"], "unknown")

    def test_full_preloaded_requires_formal_evidence_for_same_student(self):
        self.section("DEMO-PE-A")["available_seats"] = 0
        self.assertEqual(self.validate()["result"], "pass")
        self.snapshot["wish_items"][0]["status_mapping_verified"] = False
        self.check(self.validate(), "availability", "fail")
        self.snapshot["wish_items"][0]["status_mapping_verified"] = True
        self.snapshot["wish_items"][0]["student_key"] = "other-student"
        self.check(self.validate(), "availability", "fail")

    def test_wish_alternatives_and_saved_drafts_are_not_formal_members(self):
        result = confirmed_enrollment(self.snapshot["wish_items"])
        self.assertEqual(result["section_ids"], ["DEMO-PE-A"])
        self.assertEqual(len(result["pending"]), 2)
        wish = deepcopy(self.snapshot["wish_items"][0])
        wish.update(section_id="DEMO-ML-A", official_enrollment_status="tentative",
                    confirmed_schedule_membership=False, is_submitted=True)
        self.assertEqual(confirmed_enrollment([wish])["section_ids"], [])
        wishes = [deepcopy(self.snapshot["wish_items"][0])]
        cancelled = deepcopy(wishes[0])
        cancelled["official_enrollment_status"] = "cancelled"
        wishes.append(cancelled)
        self.assertEqual(confirmed_enrollment(wishes)["section_ids"], [])

    def test_upgrade_to_hard_friday_preserves_lock_and_revalidates(self):
        self.constraints["locked_section_ids"].append("DEMO-ML-B")
        self.assertEqual(self.generate()["status"], "feasible")
        original = deepcopy(self.constraints)
        self.constraints = revise_constraints(self.constraints, {
            "friday_free": "hard", "locked_section_ids": [], "preloaded_section_ids": [],
        })
        self.assertEqual(self.constraints["locked_section_ids"], original["locked_section_ids"])
        self.assertEqual(self.constraints["preloaded_section_ids"], original["preloaded_section_ids"])
        self.assertEqual(len(self.constraints["revision_warnings"]), 2)
        result = self.generate()
        self.assertEqual(result["status"], "scoped_infeasible")
        self.assertTrue(any(c["code"] == "friday_free" for c in result["diagnostics"]))
        self.assertIn("DEMO-ML-B", result["constraints"]["locked_section_ids"])
        self.assertEqual(original["revision"], 1)

    def test_fixed_meetings_cannot_be_silently_removed(self):
        meeting = deepcopy(self.section("DEMO-ML-A")["meetings"][0])
        self.constraints["fixed_meetings"] = [meeting]
        self.constraints = revise_constraints(self.constraints, {"fixed_meetings": []})
        self.assertEqual(self.constraints["fixed_meetings"], [meeting])
        self.assertEqual(self.validate()["result"], "fail")
        self.check(self.validate(), "time_conflict", "fail")

    def test_full_plan_minimum_does_not_reject_protected_subset(self):
        self.constraints["min_credits"] = 6
        self.assertEqual(self.generate()["status"], "feasible")
        self.constraints["min_credits"] = 7
        self.assertEqual(self.generate()["status"], "scoped_infeasible")

    def test_unknown_candidates_only_enter_pending_plans(self):
        for section in self.snapshot["records"]:
            if section["section_id"] != "DEMO-PE-A":
                section["available_seats"] = None
        result = self.generate()
        self.assertEqual(result["status"], "incomplete_data")
        self.assertEqual(result["plans"], [])
        self.assertEqual(len(result["pending_plans"]), 3)
        self.assertTrue(all(p["result"] == "unknown" for p in result["pending_plans"]))

    def test_incomplete_scope_and_search_limit_do_not_claim_global_infeasibility(self):
        for section in self.snapshot["records"]:
            if section["section_id"] != "DEMO-PE-A":
                section["available_seats"] = 0
        self.assertEqual(self.generate()["status"], "scoped_infeasible")
        self.snapshot["completeness"] = "partial"
        self.assertEqual(self.generate()["status"], "incomplete_data")
        self.setUp()
        limited = self.generate(max_nodes=1)
        self.assertEqual(limited["status"], "search_limit")
        self.assertTrue(limited["search"]["limit_reached"])
        self.assertEqual(limited["search"]["nodes_visited"], 1)

    def test_missing_requested_or_locked_sections_are_incomplete_data(self):
        self.constraints["requested_course_ids"].append("not-imported")
        self.assertEqual(self.generate()["status"], "incomplete_data")
        self.setUp()
        self.constraints["locked_section_ids"].append("MISSING-LOCK")
        result = self.generate()
        self.assertEqual(result["status"], "incomplete_data")
        self.assertIn("MISSING-LOCK", result["constraints"]["locked_section_ids"])

    def test_plan_isolation_duplicate_classes_and_term_mismatch(self):
        self.section("DEMO-ML-A")["plan_ids"] = ["other-plan"]
        self.check(self.validate(), "section_plan", "fail")
        self.section("DEMO-ML-A")["plan_ids"] = []
        self.check(self.validate(), "section_plan", "unknown")
        self.setUp()
        self.check(self.validate(["DEMO-PE-A", "DEMO-ML-A", "DEMO-ML-B", "DEMO-SIM-A"]),
                   "same_course_multiple_sections", "fail")
        self.section("DEMO-SIM-A")["term_code"] = "other-term"
        self.check(self.validate(), "section_term", "fail")

    def test_duplicate_snapshot_identifiers_and_data_changes_are_visible(self):
        before = self.validate()["provenance"]["snapshot_fingerprint"]
        self.snapshot["records"].append(deepcopy(self.snapshot["records"][0]))
        result = self.validate()
        self.check(result, "snapshot_structure", "unknown")
        self.assertNotEqual(before, result["provenance"]["snapshot_fingerprint"])

    def test_major_and_curriculum_version_are_explicit_eligibility(self):
        eligibility = self.section("DEMO-ML-A")["eligibility"]
        eligibility["major_codes"] = ["DEMO-OTHER-MAJOR"]
        self.check(self.validate(), "eligibility", "fail")
        eligibility["major_codes"] = [self.student["major_code"]]
        eligibility["plan_versions"] = ["other-version"]
        self.check(self.validate(), "eligibility", "fail")
        eligibility.pop("plan_versions")
        self.check(self.validate(), "eligibility", "unknown")
        self.student.pop("major_code")
        self.check(self.validate(), "plan_binding", "unknown")

    def test_decimal_strings_sum_exactly_and_nonfinite_values_remain_unknown(self):
        for identifier, amount in (("DEMO-PE-A", "0.1"), ("DEMO-ML-A", "0.2"), ("DEMO-SIM-A", "0")):
            self.section(identifier)["credits"] = amount
        self.constraints.update(min_credits="0.3", max_credits="0.3")
        result = self.validate()
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["total_credits_exact"], "0.3")
        for invalid in (float("nan"), float("inf"), "-Infinity", "NaN", True):
            self.section("DEMO-ML-A")["credits"] = invalid
            result = self.validate()
            self.assertIsNone(result["total_credits"])
            self.check(result, "planned_credits", "unknown")
            json.dumps(result, allow_nan=False)
        self.constraints["min_credits"] = "Infinity"
        self.check(self.validate(), "credit_range", "unknown")
        with self.assertRaises(ValueError):
            revise_constraints(self.constraints, {})

    def test_declared_optional_and_unsupported_eligibility_never_silently_pass(self):
        eligibility = self.section("DEMO-ML-A")["eligibility"]
        eligibility["language_levels"] = ["DEMO-ADVANCED"]
        self.check(self.validate(), "eligibility", "unknown")
        self.student["language_level"] = "DEMO-BEGINNER"
        self.check(self.validate(), "eligibility", "fail")
        self.student["language_level"] = "DEMO-ADVANCED"
        self.assertEqual(self.validate()["result"], "pass")
        eligibility["unimplemented_gpa_limit"] = "3.5"
        self.check(self.validate(), "eligibility", "unknown")

    def test_prerequisite_from_explicit_other_plan_version_remains_unconfirmed(self):
        self.section("DEMO-ML-A")["prerequisite_course_ids"] = ["zju:CS1006G"]
        for record in self.records:
            if record.get("course_id") == "zju:CS1006G":
                record["plan_version"] = "other-version"
        self.check(self.validate(), "prerequisites", "unknown")

    def test_negative_seat_count_is_known_unavailable_and_fraction_is_unknown(self):
        self.section("DEMO-ML-A")["available_seats"] = -4
        self.check(self.validate(), "availability", "fail")
        self.section("DEMO-ML-A")["available_seats"] = 0.5
        self.check(self.validate(), "availability", "unknown")
        self.section("DEMO-ML-A")["available_seats"] = float("inf")
        result = self.validate()
        self.check(result, "availability", "unknown")
        json.dumps(result, allow_nan=False)

    def test_long_decimal_total_is_exact_without_context_rounding_or_float_loss(self):
        self.section("DEMO-PE-A")["credits"] = "0.123456789012345678901234567891"
        self.section("DEMO-ML-A")["credits"] = "0.000000000000000000000000000009"
        self.section("DEMO-SIM-A")["credits"] = "0"
        self.constraints.update(min_credits="0.123456789012345678901234567900",
                                max_credits="0.123456789012345678901234567900")
        result = self.validate()
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["total_credits_exact"], "0.123456789012345678901234567900")
        self.assertEqual(result["total_credits"], "0.123456789012345678901234567900")
        self.section("DEMO-ML-A")["credits"] = "1e1000"
        result = self.validate()
        self.assertIsInstance(result["total_credits"], str)
        json.dumps(result, allow_nan=False)

    def exam(self, identifier, day="2026-12-01", start="18:00", end="19:00"):
        return {"exam_id": identifier, "exam_date": day, "start_time_local": start,
                "end_time_local": end, "timezone": "Asia/Shanghai",
                "data_kind": "synthetic", "source_refs": [{"snapshot_id": "DEMO-EXAM-v1"}]}

    def test_missing_exam_information_never_means_no_exam(self):
        section = self.section("DEMO-ML-A")
        section.pop("exam_state")
        section.pop("exams")
        result = self.validate()
        self.assertEqual(result["result"], "unknown")
        self.check(result, "exam_schedule", "unknown")
        section.update(exam_state="none", exams=[self.exam("EXAM-A")])
        self.check(self.validate(), "exam_schedule", "unknown")
        section.update(exam_state="known", exams=[self.exam("EXAM-A")])
        section["exams"][0].pop("timezone")
        self.check(self.validate(), "exam_schedule", "unknown")

    def test_exam_intersections_use_dates_clocks_and_half_open_intervals(self):
        left, right = self.section("DEMO-ML-A"), self.section("DEMO-SIM-A")
        left.update(exam_state="known", exams=[self.exam("EXAM-ML")])
        right.update(exam_state="known", exams=[self.exam("EXAM-SIM", start="18:30", end="19:30")])
        result = self.validate()
        self.check(result, "exam_conflict", "fail")
        right["exams"][0]["start_time_local"] = "19:00"
        self.assertEqual(self.validate()["result"], "pass")
        right["exams"][0]["exam_date"] = "2026-12-02"
        self.assertEqual(self.validate()["result"], "pass")

    def test_exam_against_class_requires_calendar_and_reports_actual_overlap(self):
        section = self.section("DEMO-SIM-A")
        section.update(exam_state="known", exams=[self.exam("EXAM-SIM", day="2026-11-30", start="08:30", end="09:00")])
        result = self.validate()
        self.check(result, "exam_class_conflict", "fail")
        conflicts = [item for item in result["conflicts"] if item["code"] == "exam_class_conflict"]
        self.assertIn("DEMO-ML-A", conflicts[0]["section_ids"])
        section["exams"][0]["start_time_local"] = "18:00"
        section["exams"][0]["end_time_local"] = "19:00"
        self.assertEqual(self.validate()["result"], "pass")
        self.snapshot.pop("academic_calendar")
        self.check(self.validate(), "exam_class_conflict", "unknown")

    def test_explicit_unlock_only_removes_personal_lock_and_versions_are_preserved(self):
        self.constraints["locked_section_ids"].append("DEMO-ML-B")
        self.constraints["input_version_refs"] = {"snapshot": "DEMO-OFFERINGS-v1"}
        revised = revise_constraints(self.constraints, {"unlock_section_ids": ["DEMO-ML-B", "DEMO-PE-A"],
                                                        "expected_revision": 1, "friday_free": "hard"})
        self.assertEqual(revised["locked_section_ids"], [])
        self.assertEqual(revised["preloaded_section_ids"], ["DEMO-PE-A"])
        self.assertEqual(revised["input_version_refs"], self.constraints["input_version_refs"])
        self.assertEqual(revised["previous_revision"], 1)
        self.assertTrue(revised["validation_required"])
        self.constraints = revised
        self.assertEqual(self.generate()["status"], "feasible")
        self.check(self.validate(["DEMO-ML-A", "DEMO-SIM-A"]), "protected_sections", "fail")
        with self.assertRaises(ValueError):
            revise_constraints(revised, {"expected_revision": 1})
        with self.assertRaises(ValueError):
            revise_constraints(revised, {"input_version_refs": {"snapshot": "fake"}})

    def test_formal_members_are_preserved_without_client_lock(self):
        self.constraints.update(locked_section_ids=[], preloaded_section_ids=[])
        generated = self.generate()
        self.assertTrue(all("DEMO-PE-A" in item["section_ids"] for item in generated["plans"]))
        self.check(self.validate(["DEMO-ML-A", "DEMO-SIM-A"]), "protected_sections", "fail")
        self.snapshot["wish_items"][0]["student_key"] = "DEMO-OTHER-STUDENT"
        self.assertEqual(self.validate(["DEMO-ML-A", "DEMO-SIM-A"])["result"], "pass")

    def test_comparison_recomputes_stale_results_and_handles_missing_classes(self):
        old = self.validate()
        self.section("DEMO-ML-A")["available_seats"] = 0
        compared = compare_plans([old, ["DEMO-PE-A", "DEMO-ML-B", "DEMO-SIM-A"]],
                                 self.snapshot, self.student, self.constraints, self.records)
        self.assertEqual(compared["plans"][0]["previous_result"], "pass")
        self.assertEqual(compared["plans"][0]["result"], "fail")
        self.assertEqual(compared["ranking"], [1, 0])
        self.assertEqual(compared["differences"][1]["added_section_ids"], ["DEMO-ML-B"])
        self.assertTrue(compared["plans"][0]["changed_input_versions"])
        self.snapshot["records"] = [item for item in self.snapshot["records"] if item["section_id"] != "DEMO-ML-A"]
        compared = compare_plans([old, ["DEMO-PE-A", "DEMO-ML-B", "DEMO-SIM-A"]],
                                 self.snapshot, self.student, self.constraints, self.records)
        self.check(compared["plans"][0], "missing_section", "unknown")
        self.assertEqual(compared["plans"][0]["result"], "unknown")
        self.assertIsNone(compared["plans"][0]["planned_credits"])


if __name__ == "__main__":
    unittest.main()
