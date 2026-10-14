"""Synthetic personal-source mappings; no school data or authentication."""

from copy import deepcopy
import json
import unittest

from course_assistant.student_source_parsers import BUSINESS_FIELDS, FIELD_STATES, MAPPING_VERSION, PARSER_VERSION, parse_payload


class StudentSourceParserTests(unittest.TestCase):
    def test_profile_never_infers_cohort_from_account(self):
        parsed = parse_payload("profile", {"xh": "SYNTHETIC-2023-ACCOUNT", "xm": "合成人物", "zymc": "合成专业"})
        record = parsed["records"][0]
        self.assertEqual(record["source_subject_id"], "SYNTHETIC-2023-ACCOUNT")
        self.assertEqual(record["major_name"], "合成专业")
        self.assertIsNone(record["cohort"])
        self.assertEqual(record["field_states"]["cohort"], "unknown")
        self.assertIsNone(record["program"])
        self.assertIsNone(record["study_path_raw"])
        self.assertEqual(record["identity_binding_status"], "unverified_source_observation")

    def test_profile_all_six_states_and_explicit_year(self):
        payload = {"rxnf": "2023级", "program": {"value": "合成项目", "state": "pending_verification"},
                   "study_path_raw": {"state": "not_provided"}, "training_type": {"state": "not_applicable"},
                   "major_name": {"value": ["合成甲", "合成乙"], "state": "conflicting"}}
        record = parse_payload("profile", payload)["records"][0]
        self.assertEqual(record["cohort"], 2023)
        self.assertEqual(record["field_states"]["program"], "pending_verification")
        self.assertEqual(record["field_states"]["study_path_raw"], "not_provided")
        self.assertEqual(record["field_states"]["training_type"], "not_applicable")
        self.assertEqual(record["field_states"]["major_name"], "conflicting")
        self.assertEqual(set(record["field_states"].values()), FIELD_STATES)
        self.assertIsNone(record["program"])
        self.assertEqual(record["raw_row"], payload)

    def test_conflicting_profile_aliases_keep_both_assertions(self):
        payload = {"major_name": "合成甲", "zymc": "合成乙", "entry_year": "2023"}
        parsed = parse_payload("profile", payload)
        record = parsed["records"][0]
        self.assertIsNone(record["major_name"])
        self.assertEqual(record["field_states"]["major_name"], "conflicting")
        self.assertEqual([item["raw_value"] for item in record["field_assertions"]["major_name"]], ["合成甲", "合成乙"])
        self.assertEqual(parsed["row_failures"][0]["source_row_index"], 0)

    def test_complete_admission_date_derives_cohort_with_source_evidence_only(self):
        payload = {"入学日期": "20220901", "当前所在级": "2024", "类（专业）": "合成类目", "xh": "SYNTHETIC-2025"}
        parsed = parse_payload("profile", payload)
        record = parsed["records"][0]
        self.assertEqual((PARSER_VERSION, MAPPING_VERSION), ("student-source-parser-v3", "student-source-mapping-v3"))
        self.assertEqual(record["cohort"], 2022)
        self.assertEqual(record["admission_date_raw"], "20220901")
        self.assertEqual(record["current_grade_raw"], "2024")
        self.assertEqual(record["major_or_category_raw"], "合成类目")
        self.assertTrue(all(record[field] is None for field in ("major_name", "program", "study_path_raw")))
        self.assertEqual(record["field_derivations"]["cohort"]["source_fields"], ["入学日期"])
        self.assertEqual(record["field_derivations"]["cohort"]["rule"], "year_from_complete_admission_date")
        self.assertEqual(record["field_assertions"]["cohort"][0]["raw_value"], "20220901")
        self.assertEqual(record["field_assertions"]["cohort"][0]["value"], 2022)
        self.assertEqual(record["raw_row"], payload)
        self.assertTrue({"入学日期", "当前所在级", "类（专业）"} <= BUSINESS_FIELDS["profile"])

    def test_admission_date_and_explicit_entry_year_conflict_retains_both_claims(self):
        payload = {"入学年份": "2023", "入学日期": "20220901", "当前所在级": "2024"}
        parsed = parse_payload("profile", payload)
        record = parsed["records"][0]
        self.assertIsNone(record["cohort"])
        self.assertEqual(record["field_states"]["cohort"], "conflicting")
        self.assertEqual(record["field_states"]["admission_date_raw"], "known")
        self.assertEqual([item["value"] for item in record["field_assertions"]["cohort"]], [2023, 2022])
        self.assertIn("cohort_admission_date_conflict", parsed["row_failures"][0]["codes"])
        self.assertEqual(record["raw_row"], payload)
        agreeing = parse_payload("profile", {"entry_year": "2022", "admission_date_raw": "20220901"})["records"][0]
        self.assertEqual(agreeing["cohort"], 2022)
        self.assertEqual(agreeing["field_states"]["cohort"], "known")

    def test_invalid_admission_dates_remain_unknown_without_grade_inference(self):
        for raw in ("20220229", "２０２２０９０１", "202209", "2022-09-01", "20220901 ", 20220901):
            with self.subTest(raw=raw):
                parsed = parse_payload("profile", {"入学日期": raw, "current_grade": "2024", "xh": "SYNTHETIC-2023"})
                record = parsed["records"][0]
                self.assertIsNone(record["cohort"])
                self.assertEqual(record["field_states"]["cohort"], "unknown")
                self.assertEqual(record["field_states"]["admission_date_raw"], "unknown")
                self.assertEqual(record["admission_date_raw"], raw)
                self.assertEqual(record["field_assertions"]["admission_date_raw"][0]["raw_value"], raw)
                self.assertIn("invalid_admission_date", parsed["row_failures"][0]["codes"])
                self.assertNotIn("field_derivations", record)

    def test_academic_same_code_and_selection_ids_remain_distinct_rows(self):
        rows = [{"xkkh": "SYNTHETIC-CHOICE", "kcdm": "PE-SYN", "kcmc": "合成体育", "xf": "1.00", "cj": "91", "jd": "4.0", "xnm": "2023", "xqm": "1"},
                {"xkkh": "SYNTHETIC-CHOICE", "kcdm": "PE-SYN", "kcmc": "合成体育", "xf": "1.00", "cj": "优秀", "jd": "4.5", "xnm": "2023", "xqm": "2"}]
        parsed = parse_payload("academic", {"items": rows})
        first, second = parsed["records"]
        self.assertNotEqual(first["attempt_candidate_id"], second["attempt_candidate_id"])
        self.assertEqual(first["course_code_raw"], second["course_code_raw"])
        self.assertEqual(first["nominal_credits"], "1.00")
        self.assertEqual(first["grade_numeric"], "91")
        self.assertIsNone(second["grade_numeric"])
        self.assertEqual(second["grade_raw"], "优秀")
        self.assertEqual(first["raw_row"], rows[0])
        self.assertTrue(all(item["completion_status"] == "unknown" and item["earned_credits"] is None for item in (first, second)))

    def test_xkkh_never_becomes_course_or_official_attempt_id(self):
        record = parse_payload("academic", {"items": [{"xkkh": "(2023-2024-1)-SYN-CODE-TEACHER-1", "kcmc": "合成课", "xf": "2"}]})["records"][0]
        self.assertIsNone(record["course_code_raw"])
        self.assertIsNone(record["source_attempt_id"])
        self.assertEqual(record["course_identity_status"], "unresolved")
        self.assertEqual(record["attempt_resolution_status"], "provisional")

    def test_passed_nominal_and_reported_earned_are_not_verified_credits(self):
        payload = {"items": [{"kcmc": "合成课", "xf": "2.25", "cj": "95", "completion_status": "passed", "earned_credits": "2.00"}]}
        record = parse_payload("academic", payload)["records"][0]
        self.assertEqual(record["completion_status"], "passed")
        self.assertEqual(record["nominal_credits"], "2.25")
        self.assertEqual(record["reported_earned_credits"], "2.00")
        self.assertIsNone(record["earned_credits"])
        self.assertEqual(record["earned_credits_state"], "unknown")

    def test_recognition_and_replacement_are_retained_without_extra_credits(self):
        rows = [{"kcmc": "合成认定", "record_kind": "recognition", "recognition_status": "reported_recognized", "xf": "1"},
                {"kcmc": "合成替换", "completion_status": "被替代", "substitution_raw": "合成替换关系", "xf": "3"}]
        first, second = parse_payload("academic", {"records": rows})["records"]
        self.assertEqual(first["record_kind"], "recognition")
        self.assertEqual(first["recognition_status_raw"], "reported_recognized")
        self.assertEqual(second["completion_status"], "replaced")
        self.assertEqual(second["substitution_raw"], "合成替换关系")
        self.assertIsNone(first["earned_credits"])
        self.assertIsNone(second["earned_credits"])

    def test_decimal_precision_invalid_credit_and_numeric_originals(self):
        rows = [{"xf": "0.123456789012345678901234567890123", "cj": 91, "jd": 4.5},
                {"xf": "NaN", "cj": "未出分"}, {"xf": True}, {"xf": "-1"}]
        parsed = parse_payload("academic", {"items": rows})
        self.assertEqual(parsed["records"][0]["nominal_credits"], rows[0]["xf"])
        self.assertEqual(parsed["records"][0]["grade_raw"], 91)
        self.assertEqual(parsed["records"][0]["grade_points_raw"], 4.5)
        self.assertEqual(len(parsed["records"]), 4)
        self.assertEqual(len(parsed["row_failures"]), 3)
        self.assertTrue(all(record["nominal_credits"] is None for record in parsed["records"][1:]))

    def test_scope_conflict_does_not_rewrite_row_or_use_target_term_as_school_code(self):
        scope = {"term_raw": "1", "target_term": "SYNTHETIC-AUTUMN", "academic_year_raw": "2023"}
        parsed = parse_payload("academic", {"items": [{"xnm": "2023", "xqm": "2", "kcmc": "合成课"}]}, scope)
        record = parsed["records"][0]
        self.assertEqual(record["term_raw"], "2")
        self.assertIn("row_outside_declared_scope", parsed["row_failures"][0]["codes"])
        only_target = parse_payload("academic", {"items": [{"xqm": "2"}]}, {"target_term": "SYNTHETIC-AUTUMN"})
        self.assertFalse(only_target["row_failures"])

    def test_timetable_preserves_every_explicit_segment_and_unknown_codes(self):
        row = {"kcmc": "合成课", "kcb": "合成教学班原段", "sfqd": 1, "dsz": 0, "xxq": "1",
               "meetings": [{"xqj": 2, "periods": [3, 4], "week_set": [1, 2, 3], "cdmc": "合成地点甲"},
                            {"xqj": "4", "djj": 1, "skcd": 2, "cdmc": "合成地点乙"}]}
        record = parse_payload("timetable", {"kbList": [row]})["records"][0]
        self.assertEqual(record["section_raw"], row["kcb"])
        self.assertEqual(record["sfqd_raw"], 1)
        self.assertEqual(record["dsz_raw"], 0)
        self.assertEqual(record["formal_enrollment_status"], "unknown")
        self.assertEqual(len(record["meetings"]), 2)
        self.assertEqual(record["meetings"][0]["periods"], [3, 4])
        self.assertIsNone(record["meetings"][1]["periods"])
        self.assertIsNone(record["meetings"][1]["week_set"])
        self.assertIsNone(record["calendar_dates"])
        self.assertEqual(record["raw_row"], row)

    def test_timetable_text_segments_do_not_split_week_annotation(self):
        row = {"sksj": "周二第3,4节<br>周四第1,2节{秋1-8周；冬1-8周}", "cdmc": "合成甲<br>合成乙"}
        meetings = parse_payload("timetable", {"records": [row]})["records"][0]["meetings"]
        self.assertEqual(len(meetings), 2)
        self.assertIn("秋1-8周；冬1-8周", meetings[1]["time_raw"])
        self.assertEqual(meetings[1]["place_raw"], "合成乙")
        self.assertIsNone(meetings[0]["week_set"])
        self.assertIsNone(meetings[0]["periods"])

    def test_missing_meeting_is_unknown_and_bad_segment_is_retained(self):
        parsed = parse_payload("timetable", {"kbList": [{"kcmc": "合成未知课"}, {"kcmc": "合成错格式课", "meetings": [{"xqj": 8}, None]}]})
        self.assertIsNone(parsed["records"][0]["meetings"])
        self.assertEqual(parsed["records"][1]["meetings"][1]["raw_segment"], None)
        self.assertEqual(parsed["records"][1]["meetings"][1]["parse_status"], "failed")
        self.assertEqual(parsed["records"][1]["meetings"][0]["field_states"]["weekday"], "unknown")
        self.assertEqual(parsed["row_failures"][0]["source_row_index"], 1)

    def test_exam_is_independent_and_no_date_or_timezone_inferred(self):
        row = {"kcmc": "合成课", "kssj": "第16周周二上午", "start_time": "08:00", "end_time": "10:00"}
        record = parse_payload("exams", {"items": [row]}, {"target_term": "SYNTHETIC-AUTUMN"})["records"][0]
        self.assertEqual(record["exam_time_raw"], row["kssj"])
        self.assertIsNone(record["exam_date"])
        self.assertIsNone(record["timezone"])
        self.assertEqual(record["calendar_conversion_status"], "unknown")
        self.assertNotIn("meetings", record)

    def test_explicit_exam_dates_validate_without_calendar_conversion(self):
        rows = [{"exam_date": "2027-01-02", "start_time": "08:00", "end_time": "10:00"},
                {"exam_date": "2027-02-30", "start_time": "25:00", "end_time": "10:00"},
                {"exam_date": "2027-01-02", "start_time": "10:00", "end_time": "08:00"}]
        parsed = parse_payload("exams", {"items": rows})
        self.assertEqual(parsed["records"][0]["exam_date"], "2027-01-02")
        self.assertIsNone(parsed["records"][1]["exam_date"])
        self.assertEqual(parsed["records"][2]["field_states"]["end_time"], "conflicting")
        self.assertEqual(len(parsed["row_failures"]), 2)

    def test_midterm_and_final_raw_arrangements_stay_separate_without_date_inference(self):
        row = {"选课课号": "SYNTHETIC-CHOICE", "课程名称": "合成课", "学期": "SYNTHETIC-TERM",
               "期末考试时间": "2027-01-02 08:00-10:00", "期中考试时间": "2026-11-02 14:00-16:00",
               "期末考试地点": "合成期末地点", "期中考试地点": "合成期中地点"}
        record = parse_payload("exams", {"items": [row]})["records"][0]
        self.assertEqual(record["final_exam_time_raw"], row["期末考试时间"])
        self.assertEqual(record["midterm_exam_time_raw"], row["期中考试时间"])
        self.assertEqual(record["final_exam_place_raw"], row["期末考试地点"])
        self.assertEqual(record["midterm_exam_place_raw"], row["期中考试地点"])
        self.assertTrue(all(record[field] is None for field in ("exam_time_raw", "exam_date", "start_time", "end_time", "place_raw")))
        self.assertEqual(record["raw_row"], row)
        self.assertEqual(record["field_assertions"]["midterm_exam_time_raw"][0]["source_field"], "期中考试时间")
        self.assertTrue({"期末考试时间", "期中考试时间", "期末考试地点", "期中考试地点"} <= BUSINESS_FIELDS["exams"])

    def test_blank_midterm_and_final_fields_preserve_empty_source_strings_as_unknown(self):
        row = {"选课课号": "SYNTHETIC-CHOICE", "课程名称": "合成课", "学期": "SYNTHETIC-TERM",
               "期末考试时间": "", "期中考试时间": "", "期末考试地点": "", "期中考试地点": ""}
        parsed = parse_payload("exams", {"items": [row]})
        record = parsed["records"][0]
        self.assertEqual(record["parse_status"], "parsed")
        self.assertEqual(parsed["coverage"]["retained_row_count"], 1)
        for field in ("final_exam_time_raw", "midterm_exam_time_raw", "final_exam_place_raw", "midterm_exam_place_raw"):
            self.assertEqual(record[field], "")
            self.assertEqual(record["field_states"][field], "unknown")
            self.assertEqual(record["field_assertions"][field][0]["raw_value"], "")
        self.assertTrue(all(record[field] is None for field in ("exam_time_raw", "exam_date", "start_time", "end_time")))
        self.assertEqual(record["raw_row"], row)

    def test_observed_api_midterm_final_aliases_keep_independent_raw_values(self):
        row = {"xkkh": "SYNTHETIC-CHOICE", "kcmc": "合成课", "xxq": "秋",
               "kssj": "", "jsmc": "", "qzkssj": "合成期中时间", "qzjsmc": "合成期中地点"}
        record = parse_payload("exams", {"items": [row]})["records"][0]
        self.assertEqual(record["term_raw"], "秋")
        self.assertEqual(record["final_exam_time_raw"], "")
        self.assertEqual(record["final_exam_place_raw"], "")
        self.assertEqual(record["midterm_exam_time_raw"], "合成期中时间")
        self.assertEqual(record["midterm_exam_place_raw"], "合成期中地点")
        self.assertIsNone(record["exam_date"])

    def test_malformed_rows_and_field_failures_reconcile_without_value_in_diagnostics(self):
        payload = {"items": [{"xf": "SYNTHETIC-PRIVATE-MALFORMED"}, "SYNTHETIC-PRIVATE-ROW", {"xf": "2.00"}], "total": 3}
        parsed = parse_payload("academic", payload)
        self.assertEqual([row["raw_row"] for row in parsed["records"]], payload["items"])
        self.assertEqual(parsed["coverage"]["retained_row_count"], 3)
        self.assertEqual(parsed["coverage"]["failed_row_count"], 1)
        self.assertEqual(parsed["coverage"]["partial_row_count"], 1)
        self.assertEqual(parsed["coverage"]["parsed_row_count"], 1)
        self.assertEqual([item["source_row_index"] for item in parsed["row_failures"]], [0, 1])
        diagnostics = json.dumps(parsed["row_failures"] + parsed["issues"], ensure_ascii=False)
        self.assertNotIn("SYNTHETIC-PRIVATE", diagnostics)

    def test_page_size_does_not_prove_complete_and_total_conflicts_are_visible(self):
        partial = parse_payload("academic", {"items": [{"xf": "1"}], "showCount": 5000, "total": 4})
        self.assertEqual(partial["coverage"]["count_reconciliation"], "partial")
        self.assertEqual(partial["coverage"]["completeness"], "partial")
        unknown = parse_payload("academic", {"items": [], "showCount": 5000})
        self.assertEqual(unknown["coverage"]["completeness"], "unknown")
        invalid = parse_payload("academic", {"items": [{"xf": "1"}, {"xf": "2"}], "total": 1})
        self.assertEqual(invalid["coverage"]["count_reconciliation"], "conflicting")
        self.assertIn("visible_count_exceeds_reported_total", [item["code"] for item in invalid["issues"]])

    def test_invalid_container_is_distinct_from_valid_empty_response(self):
        invalid = parse_payload("academic", {"items": None})
        empty = parse_payload("academic", {"items": [], "total": 0})
        self.assertEqual(invalid["status"], "schema_changed")
        self.assertFalse(invalid["data_available"])
        self.assertTrue(empty["data_available"])
        self.assertEqual(empty["coverage"]["count_reconciliation"], "matched")
        self.assertNotIn("earned_credits", empty)

    def test_empty_projection_rows_are_partial_and_all_original_positions_retained(self):
        profile = parse_payload("profile", {}, {"coverage": {"completeness": "complete_within_scope"}})
        exams = parse_payload("exams", {"items": [{}, {"exam_date": None}]}, {"coverage": {"completeness": "complete_within_scope"}})
        for parsed in (profile, exams):
            self.assertEqual(parsed["status"], "partial")
            self.assertEqual(parsed["coverage"]["completeness"], "partial")
            self.assertTrue(all("business_fields_missing" in item["codes"] for item in parsed["row_failures"]))
        self.assertEqual(profile["records"][0]["raw_row"], {})
        self.assertEqual([item["source_row_index"] for item in exams["records"]], [0, 1])
        empty = parse_payload("exams", {"items": [], "total": 0}, {"coverage": {"completeness": "complete_within_scope"}})
        self.assertEqual(empty["status"], "parsed")
        self.assertTrue(empty["data_available"])
        self.assertEqual(empty["coverage"]["completeness"], "complete_within_scope")

    def test_malformed_field_state_is_bounded_failure_and_same_exam_time_conflicts(self):
        profile = parse_payload("profile", {"major_name": {"value": "合成专业", "state": ["known"]}})
        self.assertEqual(profile["records"][0]["field_states"]["major_name"], "unknown")
        self.assertIn("invalid_field_state", profile["row_failures"][0]["codes"])
        exam = parse_payload("exams", {"items": [{"start_time": "08:00", "end_time": "08:00:00"}]})
        self.assertEqual(exam["records"][0]["field_states"]["end_time"], "conflicting")

    def test_purity_scope_filter_and_business_whitelists(self):
        payload = {"kbList": [{"kcmc": "合成课", "meetings": [{"weekday": 1, "periods": [1, 2]}]}]}
        scope = {"target_term": "SYNTHETIC-TERM", "coverage": {"completeness": "partial"}, "owner": "UNTRUSTED"}
        original_payload, original_scope = deepcopy(payload), deepcopy(scope)
        parsed = parse_payload("timetable", payload, scope)
        parsed["records"][0]["raw_row"]["meetings"][0]["weekday"] = 7
        self.assertEqual(payload, original_payload)
        self.assertEqual(scope, original_scope)
        self.assertNotIn("owner", parsed["scope"])
        self.assertTrue({"xh", "zymc", "入学年份"} <= BUSINESS_FIELDS["profile"])
        self.assertTrue({"xf", "cj", "jd", "xkkh"} <= BUSINESS_FIELDS["academic"])
        self.assertTrue({"kcb", "sfqd", "dsz", "xxq", "meetings"} <= BUSINESS_FIELDS["timetable"])
        self.assertFalse({"Cookie", "password", "token"} & set.union(*(set(fields) for fields in BUSINESS_FIELDS.values())))


if __name__ == "__main__":
    unittest.main()
