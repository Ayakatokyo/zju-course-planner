"""Question-driven source preparation with synthetic school doubles only."""
from copy import deepcopy
import unittest

from course_assistant.personal_acquisition import (prepare_sources, source_requests,
    recommendation_source_requests, prepare_recommendation_sources)


class SyntheticSchool:
    def __init__(self, snapshots=(), plan_status="not_acquired"):
        self.local = {"observations": {"snapshots": list(snapshots)}, "plan": {"status": plan_status}}
        self.calls, self.plan_calls = [], []
        self.response = {"status": "partial_acquisition", "data_source": "live", "observation_id": "OBS-SYNTHETIC",
                         "checked_at": 123, "coverage": {"captured_rows": 3, "reported_total": 4, "has_more": True},
                         "records": [{"course_name_raw": "合成课程"}]}
        self.plan_response = {"status": "acquired_pending_review", "data_source": "live", "source_alias": "PLAN-SYNTHETIC"}
        self.error = None

    def local_chat_status(self, context):
        raise AssertionError("previous snapshots must not decide whether to fetch current sources")

    def fetch(self, context, body):
        self.calls.append(deepcopy(body))
        if self.error:
            raise self.error
        return deepcopy(self.response)

    def acquire_plan(self, context, *, force=False):
        self.plan_calls.append({"force": force})
        return deepcopy(self.plan_response)


class PersonalAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.school, self.events = SyntheticSchool(), []
        self.authorizations = 0

    def authorize(self):
        self.authorizations += 1

    def prepare(self, question, authorize=None):
        return prepare_sources(self.school, object(), question,
            authorize=authorize or self.authorize, emit=lambda kind, fields: self.events.append((kind, deepcopy(fields))))

    def test_generic_grade_question_forces_fresh_read_on_every_submission(self):
        for question in ("我的成绩怎么样？", "请看看我的成绩"):
            result = self.prepare(question)
            self.assertEqual(self.school.calls[-1], {"operation": "major_academic", "target_term": "ALL-HISTORY", "force_refresh": True})
            self.assertEqual(result[0], {"operation": "major_academic", "target_term": "ALL-HISTORY",
                "status": "partial_acquisition", "observation_id": "OBS-SYNTHETIC", "data_source": "live",
                "checked_at": 123, "coverage": self.school.response["coverage"], "ready": True})
        self.assertEqual(len(self.school.calls), 2)
        self.assertEqual(self.authorizations, 4)
        self.assertEqual([kind for kind, _ in self.events], ["tool.start", "tool.end"] * 2)
        self.assertEqual(self.events[-1][1]["tool_name"], "read_my_major_academic")

    def test_existing_partial_or_complete_snapshot_cannot_suppress_api_read(self):
        for status in ("partial_acquisition", "complete_within_scope", "source_unavailable"):
            self.school = SyntheticSchool([{"operation": "major_academic", "target_term": "ALL-HISTORY", "status": status}])
            self.prepare("数学分析的成绩是多少")
            self.assertEqual(len(self.school.calls), 1)
            self.assertTrue(self.school.calls[0]["force_refresh"])

    def test_explicit_year_scope_takes_precedence_over_major_keyword(self):
        self.prepare("查询2024—2025学年春夏主修成绩")
        self.assertEqual(self.school.calls, [{"operation": "academic", "target_term": "2024-2025-SS", "force_refresh": True}])
        self.prepare("再看2024-2025春夏主修成绩")
        self.assertEqual(len(self.school.calls), 2)

    def test_grade_and_curriculum_both_reacquire_on_each_relevant_question(self):
        for _ in range(2):
            receipts = self.prepare("我的成绩与培养要求分别是什么")
            self.assertEqual([r["operation"] for r in receipts], ["major_academic", "curriculum"])
            self.assertTrue(all(r["ready"] for r in receipts))
        self.assertEqual(len(self.school.calls), 2)
        self.assertEqual(self.school.plan_calls, [{"force": True}, {"force": True}])
        self.assertEqual(self.authorizations, 8)

    def test_existing_curriculum_is_reacquired_with_force(self):
        self.school = SyntheticSchool(plan_status="acquired_pending_review")
        receipts = self.prepare("培养方案中的专业选修要求")
        self.assertEqual(self.school.plan_calls, [{"force": True}])
        self.assertEqual(receipts[0]["source_alias"], "PLAN-SYNTHETIC")
        self.assertEqual(receipts[0]["data_source"], "live")
        self.assertIn("checked_at", receipts[0])

    def test_failed_or_cached_source_receipt_never_claims_ready(self):
        for response in ({"status": "source_unavailable", "records": []},
                         {**self.school.response, "status": "source_unavailable", "data_source": "historical_cache"},
                         {**self.school.response, "observation_id": None}):
            self.school.response = response
            receipt = self.prepare("我的成绩怎么样")[0]
            self.assertFalse(receipt["ready"])
            self.assertEqual(self.events[-1][1]["status"], "not_ready")
            for key in ("operation", "target_term", "status", "observation_id", "data_source", "checked_at", "coverage"):
                self.assertIn(key, receipt)

    def test_live_preparation_preserves_text_free_counts_but_failed_reads_have_no_diagnostics(self):
        self.school.response.update(operation="timetable", records=[
            {"course_name_raw": "PRIVATE-COURSE", "time_raw": "周一", "field_states": {"time_raw": "known"}},
            {"course_name_raw": "PRIVATE-COURSE", "field_states": {"time_raw": "unknown"}}],
            coverage={"captured_rows": 2, "reported_total": None, "complete_within_scope": False})
        receipt = self.prepare("2026-2027秋冬课表")[0]
        self.assertEqual(receipt["diagnostics"]["record_count"], 2)
        self.assertEqual(receipt["diagnostics"]["coverage"], "unknown")
        self.assertEqual(receipt["diagnostics"]["time_evidence_missing_count"], 1)
        self.assertNotIn("PRIVATE-COURSE", str(receipt))
        self.school.response.update(status="source_unavailable", data_source="historical_cache")
        self.assertNotIn("diagnostics", self.prepare("2026-2027秋冬课表")[0])

    def test_source_exception_is_a_safe_failed_receipt_and_does_not_stop_other_scope(self):
        self.school.error = OSError("SYNTHETIC-PRIVATE-VALUE")
        receipts = self.prepare("2024-2025学年的成绩")
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(r["status"] == "failed" and r["data_source"] == "none" and not r["ready"] for r in receipts))
        self.assertNotIn("SYNTHETIC-PRIVATE-VALUE", str(receipts) + str(self.events))

    def test_failed_curriculum_retains_failure_receipt(self):
        self.school.plan_response = {"status": "source_unavailable", "data_source": "historical_only", "source_alias": "PLAN-OLD"}
        receipt = self.prepare("培养方案是什么")[0]
        self.assertFalse(receipt["ready"])
        self.assertEqual(receipt["data_source"], "historical_only")
        self.assertEqual(receipt["status"], "source_unavailable")
        self.assertEqual(self.events[-1][1]["status"], "not_ready")

    def test_authorization_checked_before_fetch_and_after_source_result(self):
        def denied():
            raise PermissionError("SYNTHETIC-AUTH-REVOKED")
        with self.assertRaises(PermissionError):
            self.prepare("我的成绩怎么样", denied)
        self.assertEqual(self.school.calls, [])
        self.assertEqual(self.events, [])
        calls = 0
        def changed():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("SYNTHETIC-AUTH-REVOKED")
        with self.assertRaises(PermissionError):
            self.prepare("我的成绩怎么样", changed)
        self.assertEqual(len(self.school.calls), 1)
        endings = [fields for kind, fields in self.events if kind == "tool.end"]
        self.assertEqual(len(endings), 1)
        self.assertEqual(endings[0]["status"], "failed")
        self.assertNotIn("SYNTHETIC-AUTH-REVOKED", str(endings))

    def test_clarification_produces_blocking_receipt_without_source_io(self):
        receipt = self.prepare("我上学期成绩怎么样")[0]
        self.assertEqual((receipt["status"], receipt["target_term"], receipt["clarification"]),
                         ("needs_clarification", None, "academic_year_required"))
        self.assertFalse(receipt["ready"])
        self.assertEqual(self.school.calls, [])
        self.assertEqual(self.events, [])

    def test_different_domain_clauses_fetch_only_their_requested_terms(self):
        receipts = self.prepare("2023-2024秋冬成绩和2024-2025春夏课表")
        self.assertEqual(self.school.calls, [
            {"operation": "academic", "target_term": "2023-2024-AW", "force_refresh": True},
            {"operation": "timetable", "target_term": "2024-2025-SS", "force_refresh": True}])
        self.assertTrue(all(receipt["ready"] for receipt in receipts))
        self.assertEqual(len(self.events), 4)


class SourceRequestPlanningTests(unittest.TestCase):
    def test_generic_grades_and_specific_course_default_to_major_history(self):
        for question in ("我的成绩怎么样", "数学分析成绩多少", "查看主修成绩"):
            self.assertEqual(source_requests(question), [{"operation": "major_academic", "target_term": "ALL-HISTORY"}])

    def test_year_without_season_and_same_year_comparison_read_both_halves(self):
        expected = [{"operation": "academic", "target_term": f"2024-2025-{season}"} for season in ("AW", "SS")]
        for question in ("2024-2025学年成绩", "对比2024-2025秋冬和春夏成绩", "2024-2025第一学期和第二学期成绩"):
            self.assertEqual(source_requests(question), expected)

    def test_multiple_explicit_years_keep_their_own_seasons(self):
        self.assertEqual(source_requests("对比2023-2024秋冬和2024-2025春夏成绩"), [
            {"operation": "academic", "target_term": "2023-2024-AW"},
            {"operation": "academic", "target_term": "2024-2025-SS"}])
        self.assertEqual(source_requests("2023-2024和2024-2025秋冬成绩"), [
            {"operation": "academic", "target_term": "2023-2024-AW"},
            {"operation": "academic", "target_term": "2024-2025-AW"}])
        self.assertEqual(len(source_requests("2022-2023、2023-2024、2024-2025学年成绩")), 6)

    def test_different_domains_keep_clause_local_years_and_seasons(self):
        expected = [
            {"operation": "academic", "target_term": "2023-2024-AW"},
            {"operation": "timetable", "target_term": "2024-2025-SS"}]
        for question in ("2023-2024秋冬成绩和2024-2025春夏课表",
                         "成绩查2023-2024秋冬，课表查2024-2025春夏",
                         "2024-2025春夏课表，同时2023-2024秋冬成绩"):
            with self.subTest(question=question):
                self.assertEqual(source_requests(question), expected)
        self.assertEqual(source_requests("2023-2024秋冬成绩和2024-2025春夏课表以及2025-2026冬学期考试"), [
            *expected, {"operation": "exams", "target_term": "2025-2026-W"}])

    def test_shared_qualifiers_continue_to_apply_to_coordinated_domains(self):
        for question in ("2023-2024和2024-2025秋冬成绩与课表",
                         "成绩和课表比较2023-2024秋冬和2024-2025秋冬",
                         "2023-2024秋冬成绩与课表和2024-2025秋冬成绩与课表"):
            with self.subTest(question=question):
                self.assertEqual(source_requests(question), [
                    {"operation": operation, "target_term": f"{year}-AW"}
                    for operation in ("academic", "timetable")
                    for year in ("2023-2024", "2024-2025")])

    def test_unresolved_or_invalid_scope_in_one_domain_does_not_borrow_another(self):
        timetable = {"operation": "timetable", "target_term": "2024-2025-SS"}
        for question in ("上学期成绩和2024-2025春夏课表", "成绩看上学期，课表看2024-2025春夏"):
            with self.subTest(question=question):
                self.assertEqual(source_requests(question), [
                    {"operation": "academic", "target_term": None, "clarification": "academic_year_required"}, timetable])
        self.assertEqual(source_requests("2023-2025秋冬成绩和2024-2025春夏课表"), [
            {"operation": "academic", "target_term": None, "clarification": "invalid_academic_year"}, timetable])
        self.assertEqual(source_requests("2023-2024秋冬成绩和2024-2025培养方案"), [
            {"operation": "academic", "target_term": "2023-2024-AW"},
            {"operation": "curriculum", "target_term": None}])
        self.assertEqual(source_requests("我的成绩以及2024-2025春夏课表"), [
            {"operation": "major_academic", "target_term": "ALL-HISTORY"}, timetable])
        self.assertEqual(source_requests("2023-2024秋冬成绩和春夏课表"), [
            {"operation": "academic", "target_term": "2023-2024-AW"},
            {"operation": "timetable", "target_term": None, "clarification": "academic_year_required"}])

    def test_scope_limit_is_shared_by_all_clauses(self):
        self.assertEqual(source_requests("2022-2023、2023-2024、2024-2025学年成绩和2025-2026秋冬课表"), [
            {"operation": "academic", "target_term": None, "clarification": "source_scope_limit_exceeded"},
            {"operation": "timetable", "target_term": None, "clarification": "source_scope_limit_exceeded"}])

    def test_more_than_six_scopes_are_blocked_instead_of_silently_truncated(self):
        result = source_requests("2021-2022、2022-2023、2023-2024、2024-2025学年成绩")
        self.assertEqual(result, [{"operation": "academic", "target_term": None, "clarification": "source_scope_limit_exceeded"}])

    def test_relative_invalid_or_incomplete_dates_require_clarification(self):
        for question, code in (("上学期成绩", "academic_year_required"), ("今年成绩", "academic_year_required"),
                               ("秋冬成绩", "academic_year_required"), ("2023-2025成绩", "invalid_academic_year")):
            self.assertEqual(source_requests(question), [{"operation": "academic", "target_term": None, "clarification": code}])

    def test_timetable_requires_explicit_year_and_season(self):
        self.assertEqual(source_requests("2024-2025秋冬课表"), [{"operation": "timetable", "target_term": "2024-2025-AW"}])
        self.assertEqual(source_requests("2024-2025课表"), [{"operation": "timetable", "target_term": None, "clarification": "season_required"}])
        self.assertEqual(source_requests("我的课表是什么"), [{"operation": "timetable", "target_term": None, "clarification": "academic_year_required"}])

    def test_exams_split_exact_seasons_without_scanning_unspecified_year(self):
        self.assertEqual(source_requests("2024-2025秋冬考试安排"), [
            {"operation": "exams", "target_term": "2024-2025-A"}, {"operation": "exams", "target_term": "2024-2025-W"}])
        self.assertEqual(source_requests("2024-2025春夏考试安排"), [
            {"operation": "exams", "target_term": "2024-2025-S"}, {"operation": "exams", "target_term": "2024-2025-SU"}])
        self.assertEqual(source_requests("2024-2025冬学期考试"), [{"operation": "exams", "target_term": "2024-2025-W"}])
        self.assertEqual(source_requests("2024-2025考试"), [{"operation": "exams", "target_term": None, "clarification": "season_required"}])

    def test_identity_greetings_and_previous_run_diagnostics_do_not_fetch(self):
        for question in ("你是谁", "你好", "？", "你能做什么", "把刚刚成绩查询的执行记录返回给我，并说明是否执行过脚本", "说明上一轮培养查询的运行日志"):
            self.assertEqual(source_requests(question), [])


class RecommendationPreparationTests(unittest.TestCase):
    @staticmethod
    def task(**changes):
        return {"recommendation_kind": "courses", "target_term": "2026-2027-AW",
            "course_scope": {"kind": "topic", "queries": ["数据分析", "人工智能"]},
            "hard_preferences": [], **changes}

    def test_structured_goal_drives_minimum_sources_independent_of_question_keywords(self):
        scopes = recommendation_source_requests(self.task())
        self.assertEqual([(row["operation"], row["target_term"]) for row in scopes],
                         [("major_academic", "ALL-HISTORY"), ("curriculum", None)])
        self.assertEqual(scopes[0]["arguments"]["mode"], "summary")
        self.assertEqual(scopes[1]["arguments"], {"query": "数据分析", "limit": 3})
        self.assertNotIn("academic", [row["operation"] for row in scopes])
        self.assertNotIn("timetable", [row["operation"] for row in scopes])

    def test_timetable_reads_only_target_arrangements_and_opt_in_exam_scopes(self):
        task = self.task(recommendation_kind="timetable")
        scopes = recommendation_source_requests(task)
        self.assertEqual([row["operation"] for row in scopes], ["major_academic", "curriculum", "timetable"])
        self.assertEqual(scopes[-1]["target_term"], "2026-2027-AW")
        self.assertEqual(scopes[-1]["arguments"]["limit"], 15)
        for term, exams in (("2026-2027-AW", ["2026-2027-A", "2026-2027-W"]),
                            ("2026-2027-SS", ["2026-2027-S", "2026-2027-SU"])):
            scopes = recommendation_source_requests({**task, "target_term": term, "check_exam_conflicts": True})
            self.assertEqual([row["target_term"] for row in scopes if row["operation"] == "exams"], exams)

    def test_preferences_remain_intent_and_do_not_expand_the_source_profile(self):
        task = self.task(recommendation_kind="timetable")
        original = recommendation_source_requests(task)
        task.update(hard_preferences=["周五不排课"], soft_preferences=["尽量下午", "优先不影响考试"])
        self.assertEqual(recommendation_source_requests(task), original)
        self.assertNotIn("exams", [row["operation"] for row in original])

    def test_existing_success_and_failure_both_suppress_same_turn_retry(self):
        receipts = [{"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "failed"}]
        calls = []
        def execute(name, args):
            calls.append((name, deepcopy(args)))
            receipts.append({"operation": "curriculum", "target_term": None, "status": "acquired_pending_review"})
            return {"status": "pending_review", "matches": [{"text": "本轮培养片段"}]}
        prepared = prepare_recommendation_sources(self.task(), receipts=receipts, execute=execute)
        self.assertEqual(prepared, [{"name": "search_my_curriculum", "arguments": {"query": "数据分析", "limit": 3},
            "result": {"status": "pending_review", "matches": [{"text": "本轮培养片段"}]}}])
        self.assertEqual(prepare_recommendation_sources(self.task(), receipts=receipts, execute=execute), [])
        self.assertEqual(calls, [("search_my_curriculum", {"query": "数据分析", "limit": 3})])

    def test_returned_facts_are_copied_from_current_child_results_without_using_receipt_facts(self):
        receipts = [{"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "complete_within_scope",
            "records": [{"course_name_raw": "OLD-SNAPSHOT"}]}]
        child = {"status": "partial_acquisition", "records": [{"course_name_raw": "本轮课程"}],
            "coverage": {"captured_rows": 1, "reported_total": None}}
        def execute(name, args):
            operation = "curriculum" if name == "search_my_curriculum" else args["operation"]
            receipts.append({"operation": operation, "target_term": args.get("target_term"), "status": "partial_acquisition"})
            return child
        prepared = prepare_recommendation_sources(self.task(recommendation_kind="timetable"), receipts=receipts, execute=execute)
        self.assertEqual([row["name"] for row in prepared], ["search_my_curriculum", "query_my_records"])
        self.assertEqual(prepared[-1]["result"], child)
        self.assertNotIn("OLD-SNAPSHOT", str(prepared))
        child["records"][0]["course_name_raw"] = "MUTATED-LATER"
        self.assertEqual(prepared[-1]["result"]["records"][0]["course_name_raw"], "本轮课程")

    def test_failed_callback_is_safe_receipt_and_other_required_sources_continue(self):
        receipts, calls = [], []
        def execute(name, args):
            calls.append(name)
            if name == "query_my_records":
                raise OSError("NEVER-REVEAL-PRIVATE")
            receipts.append({"operation": "curriculum", "target_term": None, "status": "acquired_pending_review"})
            return {"status": "pending_review", "matches": []}
        prepared = prepare_recommendation_sources(self.task(), receipts=receipts, execute=execute)
        self.assertEqual(calls, ["query_my_records", "search_my_curriculum"])
        self.assertEqual(receipts[0]["status"], "failed")
        self.assertFalse(receipts[0]["ready"])
        self.assertEqual(receipts[0]["failure"], "source_unavailable")
        self.assertNotIn("NEVER-REVEAL", str(receipts))
        self.assertEqual(prepared, [{"name": "search_my_curriculum", "arguments": {"query": "数据分析", "limit": 3},
            "result": {"status": "pending_review", "matches": []}}])
        self.assertEqual(prepare_recommendation_sources(self.task(), receipts=receipts, execute=execute), [])
        self.assertEqual(len(calls), 2)

    def test_authorization_failure_stops_plan_without_creating_a_source_failure(self):
        receipts, calls = [], []
        def denied(name, args):
            calls.append(name)
            raise PermissionError("revoked")
        with self.assertRaises(PermissionError):
            prepare_recommendation_sources(self.task(), receipts=receipts, execute=denied)
        self.assertEqual(calls, ["query_my_records"])
        self.assertEqual(receipts, [])

    def test_invalid_scope_and_cumulative_source_budget_fail_before_any_execution(self):
        calls = []
        for task in (self.task(target_term=None), self.task(course_scope={"kind": "topic", "queries": []}),
                     self.task(hard_preferences=["Cookie: PRIVATE"])):
            with self.assertRaises(ValueError):
                prepare_recommendation_sources(task, receipts=[], execute=lambda *args: calls.append(args))
        existing = [{"operation": "academic", "target_term": f"{year}-{year+1}-AW"} for year in range(2018, 2024)]
        with self.assertRaisesRegex(ValueError, "^recommendation_source_limit_exceeded$"):
            prepare_recommendation_sources(self.task(), receipts=existing, execute=lambda *args: calls.append(args))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
