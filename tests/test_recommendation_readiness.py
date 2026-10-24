"""Recommendation preparation over synthetic current-turn context only."""
from copy import deepcopy
import json
import unittest

from course_assistant.recommendation_readiness import (assess_recommendation_readiness, project_readiness_result,
    source_receipt_diagnostics, project_source_diagnostics)
from course_assistant.student_source_parsers import parse_payload


def receipt(operation="academic", term="2023-2024-AW", **changes):
    return {"operation": operation, "target_term": term, "status": "complete_within_scope",
            "ready": True, "data_source": "live", **changes}


def context(*receipts):
    return {"status": "available", "api_operations": [{"operation": operation} for operation in
            ("academic", "major_academic", "timetable", "exams")],
            "capabilities": {"live_record_query": True, "read_only_acquisition": True,
                             "curriculum_local_search": True, "course_catalog_search": True},
            "course_catalog": {"status": "ready"}, "source_receipts": list(receipts)}


def sources(value):
    return {source["id"]: source for source in value["sources"]}


def timetable_read():
    """Real school field shapes, with invented courses and no student identity."""
    payload = {"kbList": [{"kcmc": "合成课程一", "sksj": "周一第1-2节", "xqj": "1", "djj": "1", "skcd": "2"},
                          {"kcmc": "合成课程二", "xqj": "3", "djj": "3", "skcd": "2"},
                          {"kcmc": "合成课程三", "xqj": "5", "djj": "5", "skcd": "2"}]}
    parsed = parse_payload("timetable", payload, {"target_term": "2023-2024-AW"})
    return {"operation": "timetable", "status": "partial_acquisition", "data_source": "live",
            "records": parsed["records"], "row_failures": parsed["row_failures"],
            "coverage": {"captured_rows": 3, "reported_total": None, "complete_within_scope": False}}


class SourceReadDiagnosticTests(unittest.TestCase):
    def test_text_time_absence_does_not_erase_observed_structured_time(self):
        read = timetable_read()
        before = deepcopy(read)
        diagnostics = source_receipt_diagnostics("timetable", read)
        self.assertEqual(diagnostics, {"record_count": 3, "reported_total": "unknown", "coverage": "unknown",
            "row_failure_count": 0, "time_text_missing_count": 2, "structured_time_present_count": 3,
            "time_evidence_missing_count": 0})
        self.assertEqual(read, before)
        value = assess_recommendation_readiness(context(receipt("timetable", "2023-2024-AW",
            status="partial_acquisition", diagnostics=diagnostics)), recommendation_kind="timetable", target_term="2023-2024-AW")
        source = next(row for row in value["sources"] if row["id"] == "existing_timetable")
        self.assertEqual(source["diagnostics"], diagnostics)
        self.assertFalse(value["can_generate"])
        self.assertTrue(all(row["status"] == "required_dependency_unverified" for row in value["dependencies"]))

    def test_missing_time_evidence_is_independent_of_unknown_collection_total(self):
        read = timetable_read()
        read["records"][2]["meetings"] = None
        read["records"][2]["field_states"]["meetings"] = "unknown"
        diagnostics = source_receipt_diagnostics("timetable", read)
        self.assertEqual(diagnostics["time_evidence_missing_count"], 1)
        self.assertEqual(diagnostics["structured_time_present_count"], 2)
        self.assertEqual(diagnostics["reported_total"], "unknown")
        self.assertNotIn("missing_record_count", diagnostics)

    def test_diagnostics_are_text_free_and_old_or_failed_reads_do_not_pass(self):
        read = timetable_read()
        read.update(password="SYNTHETIC-PRIVATE-PASSWORD", source_alias="/Users/SYNTHETIC-PRIVATE-PATH")
        read["records"][0].update(owner="SYNTHETIC-PRIVATE-OWNER", raw_row={"cookie": "SYNTHETIC-PRIVATE-COOKIE"})
        self.assertNotIn("SYNTHETIC-PRIVATE", json.dumps(source_receipt_diagnostics("timetable", read)))
        for key, value in (("data_source", "historical_cache"), ("status", "failed"),
                           ("operation", "exams"), ("records", None)):
            with self.subTest(key=key):
                self.assertIsNone(source_receipt_diagnostics("timetable", {**read, key: value}))

    def test_private_text_invalid_counts_and_impossible_completeness_are_rejected(self):
        diagnostics = source_receipt_diagnostics("timetable", timetable_read())
        for patch in ({"private": "SYNTHETIC-PRIVATE"}, {"record_count": True}, {"record_count": -1},
                      {"record_count": 10001}, {"reported_total": None}, {"reported_total": []},
                      {"coverage": "complete_within_scope"}, {"time_text_missing_count": 4},
                      {"row_failure_count": 4}):
            with self.subTest(patch=patch), self.assertRaisesRegex(ValueError, "invalid_source_diagnostics"):
                project_source_diagnostics({**diagnostics, **patch}, "timetable")


class RecommendationReadinessTests(unittest.TestCase):
    def assess(self, value=None, **kwargs):
        return assess_recommendation_readiness(context() if value is None else value, **kwargs)

    def test_existing_reads_are_preparation_and_cannot_enable_real_generation(self):
        value = context(receipt("major_academic", "ALL-HISTORY"), receipt("curriculum", None, status="acquired_pending_review"))
        value["capabilities"]["real_recommendation"] = True
        value.update(plan_binding_status="official_record_verified", reviewed_rules=True,
                     stable_course_identity=True, eligibility=True)
        result = self.assess(value, target_term="2023-2024-AW")
        self.assertEqual([source["status"] for source in result["sources"]], ["available"] * 3)
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["gate"], "real_recommendation_not_ready")
        self.assertFalse(result["can_generate"])
        self.assertFalse(result["can_save_or_export"])
        self.assertTrue(all(item["status"] == "required_dependency_unverified" for item in result["dependencies"]))

    def test_registered_queries_are_not_current_source_evidence(self):
        result = self.assess(target_term="2023-2024-AW")
        selected = sources(result)
        self.assertEqual(selected["academic_records"]["status"], "missing")
        self.assertTrue(selected["academic_records"]["query_supported"])
        self.assertEqual(selected["curriculum"]["status"], "missing")
        self.assertTrue(selected["curriculum"]["query_supported"])
        self.assertNotIn("offering", [source["id"] for source in result["sources"]])

    def test_historical_and_failed_receipts_never_claim_current_availability(self):
        for patch, status in (({"data_source": "historical_cache"}, "missing"),
                              ({"ready": False}, "missing"), ({"status": "failed"}, "failed"),
                              ({"status": "needs_clarification"}, "needs_clarification")):
            with self.subTest(patch=patch):
                result = self.assess(context(receipt("major_academic", "ALL-HISTORY", **patch)), target_term="2023-2024-AW")
                self.assertEqual(sources(result)["academic_records"]["status"], status)
        combined = context(receipt("major_academic", "ALL-HISTORY"), receipt("major_academic", "ALL-HISTORY", status="failed", ready=False))
        self.assertEqual(sources(self.assess(combined, target_term="2023-2024-AW"))["academic_records"]["status"], "failed")

    def test_partial_source_stays_partial_and_does_not_close_business_dependencies(self):
        result = self.assess(context(receipt("major_academic", "ALL-HISTORY", status="partial_acquisition")), target_term="2023-2024-AW")
        self.assertEqual(sources(result)["academic_records"]["status"], "partial")
        self.assertTrue(all(item["status"] == "required_dependency_unverified" for item in result["dependencies"]))

    def test_target_term_grades_never_substitute_for_prior_study_source(self):
        for terms in ((receipt(),), (receipt(term="2023-2024-SS"),)):
            value = context(*terms)
            value["operations"] = [{"operation": "major_academic", "target_term": "ALL-HISTORY", "freshness": "fetched_in_current_turn"}]
            result = self.assess(value, target_term="2023-2024-AW")
            self.assertEqual(sources(result)["academic_records"]["status"], "missing")
            self.assertEqual(sources(result)["academic_records"]["target_term"], "ALL-HISTORY")
            self.assertEqual(sources(result)["academic_records"]["scope"], "major_source_only_not_complete_learning_history")
        value = context(receipt("major_academic", "ALL-HISTORY"))
        result = self.assess(value, target_term="2023-2024-AW")
        self.assertEqual(sources(result)["academic_records"]["status"], "available")
        self.assertEqual(sources(result)["academic_records"]["operation"], "major_academic")
        self.assertTrue(any(item["id"] == "identity_and_study_facts" and item["status"] == "required_dependency_unverified"
                            for item in result["dependencies"]))

    def test_no_target_never_infers_timetable_year_from_other_sources(self):
        result = self.assess(context(receipt(), receipt("timetable", None)), recommendation_kind="timetable")
        selected = sources(result)
        self.assertIsNone(result["target_term"])
        self.assertEqual(selected["academic_records"]["operation"], "major_academic")
        self.assertEqual(selected["existing_timetable"]["status"], "missing")
        self.assertEqual(selected["existing_timetable"]["requires"], "explicit_target_term")
        self.assertEqual(selected["exams"]["target_terms"], [])

    def test_timetable_has_additional_dependencies_and_personal_arrangements_are_not_candidates(self):
        value = context(receipt("timetable", "2023-2024-AW"), receipt("exams", "2023-2024-A"))
        course = self.assess(value, target_term="2023-2024-AW")
        timetable = self.assess(value, target_term="2023-2024-AW", recommendation_kind="timetable")
        self.assertNotIn("existing_timetable", sources(course))
        selected = sources(timetable)
        self.assertEqual(selected["existing_timetable"]["status"], "available")
        self.assertEqual(selected["exams"]["status"], "partial")
        self.assertEqual(selected["exams"]["target_terms"], ["2023-2024-A", "2023-2024-W"])
        dependencies = {item["id"]: item for item in timetable["dependencies"]}
        self.assertIn("section_time_and_calendar", dependencies)
        self.assertIn("protected_arrangements", dependencies)
        self.assertEqual(dependencies["current_candidate_pool"]["status"], "required_dependency_unverified")

    def test_spring_summer_exams_require_both_current_single_seasons(self):
        value = context(receipt("exams", "2023-2024-S"), receipt("exams", "2023-2024-SU"))
        selected = sources(self.assess(value, target_term="2023-2024-SS", recommendation_kind="timetable"))
        self.assertEqual(selected["exams"]["status"], "available")
        self.assertEqual(selected["exams"]["target_terms"], ["2023-2024-S", "2023-2024-SU"])

    def test_unavailable_context_and_unregistered_apis_do_not_promise_queries(self):
        value = context(receipt())
        value["status"] = "not_ready"
        self.assertTrue(all(source["status"] == "missing" and not source["query_supported"]
                            for source in self.assess(value, target_term="2023-2024-AW")["sources"]))
        value = context()
        value["api_operations"] = ["academic", {"operation": "unregistered_offering_pool"}]
        value["capabilities"]["read_only_acquisition"] = False
        selected = sources(self.assess(value, target_term="2023-2024-AW"))
        self.assertFalse(selected["academic_records"]["query_supported"])
        self.assertFalse(selected["curriculum"]["query_supported"])

    def test_output_omits_private_context_and_input_is_not_modified(self):
        value = context(receipt())
        value.update(owner_student_id="PRIVATE-OWNER", profile={"name": "PRIVATE-NAME"},
                     arbitrary="/Users/private/PRIVATE-PATH", source_alias="PRIVATE-SOURCE")
        value["source_receipts"][0].update(observation_id="PRIVATE-OBS", failure="PRIVATE-ERROR")
        value["course_catalog"].update(source_version="PRIVATE-VERSION", source_description="PRIVATE-DESCRIPTION")
        before = deepcopy(value)
        result = self.assess(value, target_term="2023-2024-AW")
        self.assertEqual(value, before)
        self.assertNotIn("PRIVATE-", json.dumps(result))

    def test_malformed_optional_context_is_ignored_without_promotion(self):
        value = {"status": {}, "capabilities": [], "api_operations": [{"operation": []}],
                 "course_catalog": [], "source_receipts": [receipt(target_term=[])]}
        result = self.assess(value)
        self.assertFalse(result["can_generate"])
        self.assertTrue(all(source["status"] == "missing" for source in result["sources"]))

    def test_invalid_explicit_request_is_rejected_without_relative_time_guessing(self):
        for value in (None, [], "context"):
            with self.subTest(context=value), self.assertRaisesRegex(ValueError, "invalid_readiness_context"):
                assess_recommendation_readiness(value)
        for term in ("上学期", "2023-2025-AW", "2023-2024-A", "2023-2024-秋冬", []):
            with self.subTest(term=term), self.assertRaisesRegex(ValueError, "invalid_readiness_target_term"):
                self.assess(target_term=term)
        for kind in ("all", [], None):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "invalid_recommendation_kind"):
                self.assess(recommendation_kind=kind)


class ReadinessProjectionTests(unittest.TestCase):
    def setUp(self):
        self.args = {"recommendation_kind": "timetable", "target_term": "2023-2024-AW"}
        self.wrapper = {"status": "available", "data_kind": "personal_source", "check_executed": True,
                        "readiness": assess_recommendation_readiness(context(receipt()), **self.args)}

    def assert_invalid(self, value, args=None):
        with self.assertRaisesRegex(ValueError, "^invalid_readiness_result$"):
            project_readiness_result(value, self.args if args is None else args)

    def test_projection_admits_safe_current_source_states_but_keeps_all_gates_closed(self):
        before = deepcopy(self.wrapper)
        projected = project_readiness_result(self.wrapper, self.args)
        self.assertEqual(projected, self.wrapper["readiness"])
        self.assertFalse(projected["can_generate"])
        self.assertFalse(projected["can_save_or_export"])
        self.assertEqual(projected["status"], "not_ready")
        projected["sources"][0]["status"] = "missing"
        self.assertEqual(self.wrapper, before)

    def test_optional_current_read_diagnostics_pass_but_private_or_wrong_source_fields_do_not(self):
        diagnostics = source_receipt_diagnostics("timetable", timetable_read())
        value = deepcopy(self.wrapper)
        selected = sources(value["readiness"])["existing_timetable"]
        selected.update(status="partial", diagnostics=deepcopy(diagnostics))
        self.assertEqual(sources(project_readiness_result(value, self.args))["existing_timetable"]["diagnostics"], diagnostics)
        selected["diagnostics"]["explanation"] = "SYNTHETIC-PRIVATE-TEXT"
        self.assert_invalid(value)
        selected["diagnostics"] = diagnostics
        selected["status"] = "failed"
        self.assert_invalid(value)
        selected.pop("diagnostics")
        value["readiness"]["sources"][0]["diagnostics"] = diagnostics
        self.assert_invalid(value)

    def test_only_source_availability_and_query_support_can_differ_from_template(self):
        value = deepcopy(self.wrapper)
        for source in value["readiness"]["sources"]:
            source.update(status="needs_clarification", query_supported=False)
        projected = project_readiness_result(value, self.args)
        self.assertTrue(all(source["status"] == "needs_clarification" for source in projected["sources"]))
        value["readiness"]["sources"][0]["tool"] = "arbitrary_terminal"
        self.assert_invalid(value)

    def test_extra_private_fields_at_any_layer_fail_without_passing_text(self):
        for location in ("wrapper", "readiness", "source", "dependency"):
            with self.subTest(location=location):
                value = deepcopy(self.wrapper)
                target = (value if location == "wrapper" else value["readiness"] if location == "readiness" else
                          value["readiness"]["sources"][0] if location == "source" else value["readiness"]["dependencies"][0])
                target["owner_student_id"] = "/Users/PRIVATE-OWNER"
                self.assert_invalid(value)

    def test_gates_fixed_fields_and_boolean_types_cannot_be_promoted_or_coerced(self):
        for key, replacement in (("can_generate", True), ("can_generate", 0), ("can_save_or_export", True),
                                 ("status", "ready"), ("gate", "enabled"), ("schema_version", "other"),
                                 ("next_step", "PRIVATE-TEXT"), ("delivery_dependency", "satisfied")):
            with self.subTest(key=key, replacement=replacement):
                value = deepcopy(self.wrapper)
                value["readiness"][key] = replacement
                self.assert_invalid(value)
        for key, replacement in (("status", "not_ready"), ("check_executed", 1), ("data_kind", "synthetic")):
            with self.subTest(key=key):
                value = deepcopy(self.wrapper)
                value[key] = replacement
                self.assert_invalid(value)

    def test_scope_kind_missing_dependencies_and_source_order_are_exact(self):
        for change in ("scope", "kind", "dependency", "source_order", "missing_source"):
            with self.subTest(change=change):
                value = deepcopy(self.wrapper)
                if change == "scope":
                    value["readiness"]["target_term"] = "2023-2024-SS"
                elif change == "kind":
                    value["readiness"]["recommendation_kind"] = "courses"
                elif change == "dependency":
                    value["readiness"]["dependencies"][0]["missing"].pop()
                elif change == "source_order":
                    value["readiness"]["sources"].reverse()
                else:
                    value["readiness"]["sources"].pop()
                self.assert_invalid(value)

    def test_invalid_source_states_and_query_flag_types_are_rejected(self):
        for key, replacement in (("status", "ready"), ("status", []), ("query_supported", 1),
                                 ("query_supported", "true")):
            with self.subTest(key=key, replacement=replacement):
                value = deepcopy(self.wrapper)
                value["readiness"]["sources"][0][key] = replacement
                self.assert_invalid(value)

    def test_requests_require_explicit_kind_and_allow_only_optional_canonical_term(self):
        for args in ({}, {"recommendation_kind": "courses", "target_term": None},
                     {"recommendation_kind": "courses", "target_term": "上学期"},
                     {"recommendation_kind": "courses", "owner_student_id": "PRIVATE"},
                     {"recommendation_kind": []}, {"recommendation_kind": "all"}):
            with self.subTest(args=args):
                self.assert_invalid(self.wrapper, args)
        args = {"recommendation_kind": "courses"}
        wrapper = {"status": "available", "data_kind": "personal_source", "check_executed": True,
                   "readiness": assess_recommendation_readiness({}, **args)}
        self.assertIsNone(project_readiness_result(wrapper, args)["target_term"])


if __name__ == "__main__":
    unittest.main()
