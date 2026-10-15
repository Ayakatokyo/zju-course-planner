"""Synthetic observation-store checks; no private school sources or network."""
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.personal_observations import PersonalObservationStore, PersonalObservationError


class PersonalObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "private" / "observations"
        self.receipt = {"scope_key": "SYNTHETIC-SESSION-A-1", "owner_student_id": "SYNTHETIC-A",
                        "security_realm": "personal_school_client"}
        self.active = True
        self.store = PersonalObservationStore(self.root, self.authorize)

    def authorize(self):
        if not self.active:
            raise ValueError("Cookie: SYNTHETIC-MUST-NOT-LEAK")
        return deepcopy(self.receipt)

    @staticmethod
    def record(index=0, **patches):
        result = {"course_name_raw": "合成体育", "course_code_raw": "SYNTHETIC-PE",
                  "academic_year_raw": "2023-2024", "grade_raw": 70 + index,
                  "earned_credits": None, "earned_credits_state": "unknown",
                  "attempt_candidate_id": f"academic-row-{index}", "attempt_resolution_status": "provisional",
                  "course_identity_status": "unresolved",
                  "field_states": {"course_name_raw": "known", "course_code_raw": "known",
                                   "academic_year_raw": "known", "earned_credits": "unknown"}}
        result.update(patches)
        return result

    def result(self, *, count=3, **patches):
        result = {"status": "complete_within_scope", "data_kind": "personal_source",
                  "security_realm": "personal_school_client", "operation": "major_academic",
                  "target_term": "ALL-HISTORY", "data_source": "live",
                  "records": [self.record(index) for index in range(count)], "issues": [], "row_failures": [],
                  "coverage": {"captured_rows": count, "reported_total": count, "pages_captured": 1,
                               "complete_within_scope": True, "has_more": False},
                  "captured_at": 100.25, "checked_at": 101.25, "cache_captured_at": None,
                  "failure": None, "source_alias": "SOURCE-" + "a" * 24,
                  "source_field_diagnostics": []}
        result.update(patches)
        return result

    def register(self, result=None, **versions):
        return self.store.register(result or self.result(), parser_version=versions.get("parser_version", "parser-v5"),
                                   mapping_version=versions.get("mapping_version", "mapping-v5"))

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PersonalObservationError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_live_reads_have_distinct_event_ids_even_for_identical_payload_and_time(self):
        first, second = self.register(), self.register()
        self.assertNotEqual(first["observation_id"], second["observation_id"])
        self.assertEqual(first["versions"]["records_sha256"], second["versions"]["records_sha256"])
        self.assertEqual(second["observation_status"], "observed_live")
        self.assertEqual(len(self.store.describe()["snapshots"]), 2)

    def test_records_keep_repeated_attempts_unknowns_and_safe_literal_text(self):
        source = self.result()
        source["records"][1]["course_name_raw"] = '<script>SYNTHETIC-LITERAL</script>'
        original = deepcopy(source)
        registered = self.register(source)
        source["records"].clear()
        view = self.store.query({"operation": "major_academic", "observation_id": registered["observation_id"]})
        self.assertEqual(view["records"], original["records"])
        self.assertEqual(view["total_records"], 3)
        self.assertIsNone(view["records"][0]["earned_credits"])
        self.assertEqual(view["records"][0]["attempt_resolution_status"], "provisional")
        self.assertTrue(view["local_only"])
        self.assertEqual(view["data_kind"], "personal_source")
        view["records"][0]["grade_raw"] = "CHANGED"
        self.assertEqual(self.store.query({"operation": "major_academic"})["records"], original["records"])

    def test_directory_files_and_reopen_are_private(self):
        first = self.register()
        for path in (self.root, self.store.directory):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for path in self.store.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        reopened = PersonalObservationStore(self.root, self.authorize)
        view = reopened.query({"operation": "major_academic", "observation_id": first["observation_id"]})
        self.assertEqual(view["captured_at"], 100.25)
        self.assertEqual(view["record_count"], 3)

    def test_cached_read_links_only_one_proven_original_capture(self):
        original = self.register()
        cached = self.result(data_source="historical_cache", source_alias=None,
                             cache_captured_at=100.25, checked_at=200)
        result = self.register(cached)
        self.assertEqual(result["observation_status"], "linked_historical_cache")
        self.assertEqual(result["linked_observation_id"], original["observation_id"])
        self.assertTrue(result["historical_only"])
        self.assertEqual(result["captured_at"], 100.25)
        self.assertEqual(result["checked_at"], 200)

    def test_unproven_ambiguous_or_mismatched_cache_stays_historical(self):
        cached = self.result(data_source="historical_cache", cache_captured_at=100.25)
        result = self.register(cached)
        self.assertEqual(result["observation_status"], "unverified_historical")
        self.register()
        self.register()
        result = self.register(cached)
        self.assertIsNone(result["linked_observation_id"])
        for field, value in (("cache_captured_at", 99), ("source_alias", "SOURCE-" + "b" * 24),
                             ("records", [self.record(7)])):
            with self.subTest(field=field):
                result = self.register({**cached, field: value})
                self.assertIsNone(result["linked_observation_id"])

    def test_cache_versions_and_coverage_must_match(self):
        self.register()
        cached = self.result(data_source="historical_cache", cache_captured_at=100.25)
        result = self.register(cached, parser_version="parser-v6")
        self.assertIsNone(result["linked_observation_id"])
        changed = deepcopy(cached)
        changed["coverage"]["has_more"] = True
        self.assertIsNone(self.register(changed)["linked_observation_id"])

    def test_missing_metadata_is_not_recovered_from_file_times_or_row_count(self):
        source = self.result()
        for field in ("captured_at", "checked_at", "cache_captured_at", "source_alias"):
            source.pop(field)
        result = self.register(source)
        os.utime(self.store.directory / (result["observation_id"] + ".json"), (99999, 99999))
        view = self.store.query({"operation": "major_academic"})
        self.assertEqual(view["observation_status"], "live_metadata_incomplete")
        self.assertTrue(view["historical_only"])
        self.assertIsNone(view["captured_at"])
        self.assertEqual(view["coverage"], source["coverage"])

    def test_course_filter_uses_known_name_code_and_does_not_search_grades(self):
        source = self.result()
        source["records"][1]["field_states"]["course_code_raw"] = "conflicting"
        source["records"][1]["field_states"]["course_name_raw"] = "unknown"
        source["records"][2]["course_name_raw"] = "合成数据分析"
        source["records"][2]["course_code_raw"] = "SYNTHETIC-DATA"
        self.register(source)
        self.assertEqual(self.store.query({"operation": "major_academic", "query": "体育"})["matched_records"], 1)
        self.assertEqual(self.store.query({"operation": "major_academic", "query": "synthetic-data"})["matched_records"], 1)
        self.assertEqual(self.store.query({"operation": "major_academic", "query": "70"})["matched_records"], 0)

    def test_lookup_normalizes_spaces_width_and_requires_complete_course_codes(self):
        source = self.result()
        source["records"] = [self.record(0, course_name_raw="大 学 英 语（甲）", course_code_raw="061B0010"),
            self.record(1, course_name_raw="其他合成课程", course_code_raw="061B00100"),
            self.record(2, course_name_raw="大学英语（甲）", course_code_raw="061B0010")]
        self.register(source)
        code = self.store.query({"operation": "major_academic", "query": " ０６１ｂ００１０ "})
        self.assertEqual(code["matched_records"], 2)
        self.assertEqual([row["course_code_raw"] for row in code["records"]], ["061B0010", "061B0010"])
        names = self.store.query({"operation": "major_academic", "query": " 大学英语(甲) "})
        self.assertEqual(names["matched_records"], 2)
        self.assertEqual(names["records"][0]["course_name_raw"], "大 学 英 语（甲）")

    def test_generic_grades_question_is_overview_but_specific_sentences_are_not_widened(self):
        self.register()
        for query in ("我的成绩怎么样", "请查看我的成绩？", "成绩总览", " 我的成绩如何 "):
            view = self.store.query({"operation": "major_academic", "query": query})
            self.assertEqual(view["query"], "")
            self.assertEqual(view["matched_records"], 3)
        for query in ("我的合成体育成绩怎么样", "成绩低于80", "删除所有成绩"):
            view = self.store.query({"operation": "major_academic", "query": query})
            self.assertNotEqual(view["query"], "")
            self.assertEqual(view["matched_records"], 0)

    def test_normalized_query_continuation_stays_pinned_to_original_snapshot(self):
        original = self.register(self.result(count=23))
        first = self.store.query({"operation": "major_academic", "query": " 体育 ", "limit": 20})
        self.register(self.result(count=1))
        following = self.store.query({"operation": "major_academic", "query": " 体育 ", "cursor": first["next_cursor"]})
        self.assertEqual(following["observation_id"], original["observation_id"])
        self.assertEqual(following["returned_records"], 3)

    def test_source_grade_summary_uses_all_matching_rows_not_first_page(self):
        source = self.result(count=22)
        source["coverage"].update(reported_total=30, complete_within_scope=False, has_more=True)
        for index, record in enumerate(source["records"]):
            record["grade_raw"] = "90" if index < 15 else "10"
            record["field_states"]["grade_raw"] = "known"
        self.register(source)
        first = self.store.query({"operation": "major_academic", "limit": 15})
        summary = first["source_grade_summary"]
        self.assertEqual(summary["matched_records"], 22)
        self.assertEqual(summary["numeric_grade_count"], 22)
        self.assertEqual(summary["numeric_mean_unweighted"], "64.55")
        self.assertEqual((summary["numeric_min"], summary["numeric_max"]), ("10", "90"))
        self.assertFalse(summary["coverage"]["complete_within_scope"])
        self.assertFalse(summary["is_gpa"])
        self.assertFalse(summary["earned_credits_verified"])
        self.assertEqual(first["returned_records"], 15)
        following = self.store.query({"operation": "major_academic", "cursor": first["next_cursor"]})
        self.assertEqual(following["source_grade_summary"], summary)

    def test_summary_never_guesses_annotations_out_of_range_or_unknown_grade_states(self):
        grades = [("0", "known"), (100, "known"), ("82.5", "known"), ("85(补考)", "known"),
                  ("及格", "known"), ("101", "known"), ("1e2", "known"), (None, "known"),
                  ("80", "unknown"), ("90", "conflicting")]
        source = self.result(count=len(grades))
        for row, (grade, state) in zip(source["records"], grades):
            row["grade_raw"], row["field_states"]["grade_raw"] = grade, state
        self.register(source)
        summary = self.store.query({"operation": "major_academic"})["source_grade_summary"]
        self.assertEqual((summary["numeric_grade_count"], summary["non_numeric_grade_count"], summary["unknown_grade_count"]), (3, 4, 3))
        self.assertEqual((summary["numeric_min"], summary["numeric_max"], summary["numeric_mean_unweighted"]), ("0", "100", "60.83"))
        self.assertIn("不是GPA", summary["note"])
        empty = self.store.query({"operation": "major_academic", "query": "不存在的合成课程"})["source_grade_summary"]
        self.assertEqual(empty["matched_records"], 0)
        self.assertIsNone(empty["numeric_mean_unweighted"])

    def test_summary_respects_name_year_filters_and_is_not_emitted_for_timetable(self):
        source = self.result(count=3)
        for index, row in enumerate(source["records"]):
            row["field_states"]["grade_raw"] = "known"
            row["grade_raw"] = str(30 + index * 30)
        source["records"][1]["academic_year_raw"] = "2024-2025"
        source["records"][2]["course_name_raw"] = "合成其他课"
        self.register(source)
        summary = self.store.query({"operation": "major_academic", "query": "体育", "academic_year": "2023-2024"})["source_grade_summary"]
        self.assertEqual(summary["matched_records"], 1)
        self.assertEqual(summary["numeric_mean_unweighted"], "30.00")
        self.register(self.result(operation="timetable", target_term="2023-2024-AW"))
        self.assertNotIn("source_grade_summary", self.store.query({"operation": "timetable"}))

    def test_year_filter_uses_only_known_source_value_never_query_term(self):
        source = self.result()
        source["records"][1]["field_states"]["academic_year_raw"] = "unknown"
        source["records"][2]["academic_year_raw"] = 2024
        self.register(source)
        view = self.store.query({"operation": "major_academic", "academic_year": "2023-2024"})
        self.assertEqual(view["matched_records"], 1)
        self.assertEqual(self.store.query({"operation": "major_academic", "academic_year": "2024"})["matched_records"], 1)
        self.assertEqual(self.store.query({"operation": "major_academic", "academic_year": "2024-2025"})["matched_records"], 0)

    def test_local_pagination_keeps_original_snapshot_after_new_read(self):
        first = self.register(self.result(count=23))
        page = self.store.query({"operation": "major_academic", "query": "体育", "limit": 20})
        self.assertEqual(page["returned_records"], 20)
        self.assertEqual(page["matched_records"], 23)
        self.assertEqual(page["coverage"]["captured_rows"], 23)
        self.register(self.result(count=2, captured_at=200, checked_at=201))
        following = self.store.query({"operation": "major_academic", "cursor": page["next_cursor"]})
        self.assertEqual(following["observation_id"], first["observation_id"])
        self.assertEqual(following["returned_records"], 3)
        self.assertEqual(following["offset"], 20)
        self.assertIsNone(following["next_cursor"])

    def test_cursor_tampering_and_filter_or_operation_changes_are_rejected(self):
        self.register(self.result(count=3))
        page = self.store.query({"operation": "major_academic", "limit": 1})
        cursor = page["next_cursor"]
        for body in ({"cursor": cursor + "x"}, {"cursor": 5}, {"cursor": cursor, "query": "另一个条件"},
                     {"cursor": cursor, "academic_year": "2024"}, {"cursor": cursor, "limit": 2},
                     {"cursor": cursor, "operation": "academic"}):
            with self.subTest(body=body):
                self.assert_code("invalid_observation_cursor", self.store.query, {"operation": "major_academic", **body})

    def test_cross_term_queries_never_merge_snapshots(self):
        first = self.register(self.result(operation="academic", target_term="2023-2024-AW"))
        second = self.register(self.result(operation="academic", target_term="2024-2025-SS", count=1))
        latest = self.store.query({"operation": "academic"})
        self.assertEqual(latest["observation_id"], second["observation_id"])
        self.assertEqual(latest["total_records"], 1)
        older = self.store.query({"operation": "academic", "observation_id": first["observation_id"]})
        self.assertEqual(older["target_term"], "2023-2024-AW")
        self.assertEqual(older["total_records"], 3)
        self.assert_code("inaccessible_observation", self.store.query,
                         {"operation": "exams", "observation_id": first["observation_id"]})

    def test_authorization_change_invalidates_instance_refs_and_cursors(self):
        registered = self.register()
        page = self.store.query({"operation": "major_academic", "limit": 1})
        self.receipt["scope_key"] = "SYNTHETIC-SESSION-A-2"
        self.assert_code("observation_scope_changed", self.store.describe)
        self.assert_code("observation_scope_changed", self.store.query, {"operation": "major_academic"})
        replacement = PersonalObservationStore(self.root, self.authorize)
        self.assertEqual(replacement.describe()["snapshots"], [])
        self.assert_code("inaccessible_observation", replacement.query,
                         {"operation": "major_academic", "observation_id": registered["observation_id"]})
        self.assert_code("invalid_observation_cursor", replacement.query,
                         {"operation": "major_academic", "cursor": page["next_cursor"]})

    def test_owner_and_realm_are_not_selected_by_request_or_same_scope_key(self):
        registered = self.register()
        for field in ("owner_student_id", "security_realm"):
            receipt = self.authorize()
            receipt[field] = "SYNTHETIC-OTHER"
            other = PersonalObservationStore(self.root, lambda: receipt)
            self.assertEqual(other.describe()["operations"], [])
            self.assert_code("inaccessible_observation", other.query,
                             {"operation": "major_academic", "observation_id": registered["observation_id"]})
        for field in ("owner", "owner_student_id", "security_realm", "path", "target_term"):
            self.assert_code("invalid_observation_request", self.store.query,
                             {"operation": "major_academic", field: "SYNTHETIC"})

    def test_expiration_never_echoes_callback_exception(self):
        self.active = False
        self.assert_code("observation_expired", self.store.describe)
        self.assert_code("observation_expired", self.register)

    def test_authorization_rechecked_after_write_and_unpublished_on_change(self):
        create = self.store._create
        def change_after_create(path, raw):
            create(path, raw)
            self.receipt["scope_key"] = "SYNTHETIC-NEW-GENERATION"
        with patch.object(self.store, "_create", side_effect=change_after_create):
            self.assert_code("observation_scope_changed", self.register)
        self.assertEqual(list(self.store.directory.glob("OBS-*.json")), [])

    def test_authorization_rechecked_before_returning_private_query(self):
        self.register()
        load = self.store._load
        def expire_after_load(identifier):
            value = load(identifier)
            self.active = False
            return value
        with patch.object(self.store, "_load", side_effect=expire_after_load):
            self.assert_code("observation_expired", self.store.query, {"operation": "major_academic"})

    def test_unsafe_python_values_and_malformed_results_are_not_stringified(self):
        bad = [Decimal("1.5"), {1: "non-string key"}, {"cookie": "SYNTHETIC"}, b"bytes", {1, 2}, float("nan")]
        for value in bad:
            with self.subTest(value=type(value).__name__):
                source = self.result()
                source["records"][0]["unknown_raw"] = value
                self.assert_code("invalid_observation_result", self.register, source)
        for source in (self.result(operation={}), self.result(data_source=[]), self.result(status=[]),
                       self.result(captured_at=True), self.result(checked_at=90), self.result(source_alias="/private/path"),
                       self.result(target_term="2023-2025-AW", operation="academic"), self.result(owner_student_id="OTHER")):
            self.assert_code("invalid_observation_result", self.register, source)

    def test_invalid_query_and_limits_have_stable_errors(self):
        self.register()
        for body in ({}, {"operation": {}}, {"operation": "major_academic", "limit": True},
                     {"operation": "major_academic", "limit": 21}, {"operation": "major_academic", "limit": 0},
                     {"operation": "major_academic", "query": []}, {"operation": "major_academic", "academic_year": 2023}):
            self.assert_code("invalid_observation_request", self.store.query, body)
        self.assert_code("inaccessible_observation", self.store.query,
                         {"operation": "major_academic", "observation_id": "../../anything"})

    def test_session_failure_is_not_registered_as_a_personal_snapshot(self):
        self.assert_code("observation_expired", self.register, self.result(status="source_subject_changed"))
        self.assertEqual(self.store.describe()["snapshots"], [])

    def test_source_failure_is_explicit_not_silently_replaced_by_old_live(self):
        self.register()
        failure = self.register(self.result(status="source_unavailable", data_source="none", records=[], source_alias=None,
                                           captured_at=None, cache_captured_at=None, failure="transport_failed"))
        view = self.store.query({"operation": "major_academic"})
        self.assertEqual(view["observation_id"], failure["observation_id"])
        self.assertEqual(view["records"], [])
        self.assertEqual(view["failure"], "transport_failed")
        self.assertTrue(view["historical_only"])

    def test_symlink_directory_and_snapshot_are_rejected(self):
        registered = self.register()
        path = self.store.directory / (registered["observation_id"] + ".json")
        real = path.with_suffix(".backup")
        path.rename(real)
        path.symlink_to(real)
        self.assert_code("invalid_private_store", self.store.query,
                         {"operation": "major_academic", "observation_id": registered["observation_id"]})
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_code("invalid_private_store", PersonalObservationStore, alias, self.authorize)

    def test_changed_permissions_or_snapshot_content_are_rejected(self):
        registered = self.register()
        path = self.store.directory / (registered["observation_id"] + ".json")
        path.chmod(0o644)
        self.assert_code("invalid_private_store", self.store.query,
                         {"operation": "major_academic", "observation_id": registered["observation_id"]})
        path.chmod(0o600)
        document = json.loads(path.read_bytes())
        document["snapshot"]["result"]["records"][0]["grade_raw"] = 100
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assert_code("invalid_private_store", self.store.query,
                         {"operation": "major_academic", "observation_id": registered["observation_id"]})

    def test_timetable_metadata_is_preserved_without_making_calendar_facts(self):
        source = self.result(operation="timetable", target_term="2026-2027-AW",
                             period_times=[{"period": 1, "start_time": "08:00", "end_time": "08:45"}],
                             period_times_source={"state": "known", "page_path": "SYNTHETIC-TIMETABLE"})
        source["records"][0].update(formal_enrollment_status="unknown", calendar_dates=None,
                                    section_identity_status="unresolved")
        self.register(source)
        view = self.store.query({"operation": "timetable"})
        self.assertEqual(view["period_times"], source["period_times"])
        self.assertIsNone(view["records"][0]["calendar_dates"])
        self.assertEqual(view["records"][0]["formal_enrollment_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
