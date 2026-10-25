"""Structured source dependencies through the authorized tool callback; no network."""
from copy import deepcopy
import unittest

from course_assistant.personal_acquisition import recommendation_source_requests, prepare_recommendation_sources


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
