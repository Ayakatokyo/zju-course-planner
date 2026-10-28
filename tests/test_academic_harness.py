"""Synthetic integration: real harness, dialogue, projection and plan calculator.

Only the school transport and model are doubles. No school or provider network.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from course_assistant.academic_harness import AcademicHarness, resource_catalog
from course_assistant.auth import AccessError
from course_assistant.personal_course_plans import PersonalCoursePlanService, NAMES, CURRENT_TERM
from course_assistant.personal_dialogue import PersonalDialogueService, PersonalDialogueError
from tests.test_current_term_recommendation import fixture, write_delivery
from tests.test_personal_course_plans import sources
from tests.test_personal_dialogue import ScriptedModel, call, final
from tests import test_prepared_context_dialogue as prepared_fixtures


class SchoolDouble:
    def __init__(self, delivery):
        self.plans = PersonalCoursePlanService(delivery, CURRENT_TERM, lambda: {"scope_key": "SYNTHETIC"})
        facts = prepared_fixtures.PreparedContextDialogueTests()
        facts.setUp()
        self.context = deepcopy(facts.context)
        self.context["capabilities"]["course_data_test"] = True
        self.context["course_data_test"] = {"status": "ready", "target_term": CURRENT_TERM,
            "reference_mode": "historical_course_observations"}
        self.facts = {"major_academic": facts.major, "curriculum": facts.curriculum, "timetable": facts.timetable}
        self.calls, self.failures = [], set()
        self.after_read = lambda: None

    def personal_chat_tool_requirements(self, context, name, args, *, policy_context, receipts, before):
        before()
        return self.plans.dependency_task(name, args, active_plan_ref=policy_context["active_plan_ref"], turn_identity=receipts)

    def personal_chat_live_tool(self, context, name, args, *, receipts, emit, authorize, adopt, before, policy_context):
        before()
        self.calls.append((name, deepcopy(args)))
        if name == "get_my_context":
            result = deepcopy(self.context)
            hint = self.plans.current_plan_hint(policy_context["active_plan_ref"], current_dialogue=True)
            if hint:
                result["current_course_plan"] = hint
            return result
        if name in NAMES:
            current = sources()
            for operation in ("major_academic", "curriculum"):
                receipt = next((r for r in receipts if r["operation"] == operation), {})
                if not receipt.get("ready"):
                    current[operation] = {"status": "failed", "verified": False}
            if any(r["operation"] == "timetable" for r in receipts):
                current["timetable"] = {"status": "partial", "verified": True}
            return self.plans.execute(name, args, sources=current, turn_identity=receipts, policy_context=policy_context)
        operation = "curriculum" if name == "search_my_curriculum" else args["operation"]
        if operation in self.failures:
            raise ValueError("synthetic_source_failed")
        receipts.append({"operation": operation, "target_term": None if operation == "curriculum" else args["target_term"],
            "status": "partial_acquisition" if operation == "timetable" else "complete_within_scope",
            "ready": True, "data_source": "live"})
        self.after_read()
        return deepcopy(self.facts[operation])


class AcademicHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.school = SchoolDouble(write_delivery(Path(self.temp.name), fixture()))
        self.events, self.revoked = [], False

    def authorize(self):
        if self.revoked:
            raise AccessError("Synthetic authorization revoked", 403, "grant_revoked")
        return {"scope_key": "SYNTHETIC", "versions": {"input": "v1"}}

    def harness(self, message="推荐合成课程5学分", active=None, clarification=None):
        return AcademicHarness(self.school, None, authorize=self.authorize,
            generation_authorize=self.authorize, adopt=lambda _: None,
            emit=lambda event, fields: self.events.append((event, deepcopy(fields))),
            user_message=message, active_plan_ref=active, clarification_context=clarification)

    def test_managed_generation_continues_only_explicit_trusted_clarification(self):
        from course_assistant.personal_tasks import next_clarification_context
        request = "生成2门课、至少5学分、尽量周五空闲的条件方案。"
        pending = next_clarification_context(request, {"status": "completed",
            "reply_origin": "personal_model_clarification", "reply": "请选择课程范围。",
            "model_decision": {"kind": "clarify", "request_basis": "current_message"}})
        runtime = self.harness("只在合成课程里选，其他条件保留。", clarification=pending)
        args = {**self.args(), "friday_free": "soft", "request_basis": "pending_task"}
        result, _ = self.dialogue(runtime, [call(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM}),
            call(NAMES[1], args, "compute"), self.answer], runtime.policy_context["user_message"])
        plan = result["recommendation_result"]
        self.assertTrue(plan["options"], plan)
        self.assertEqual(plan["constraints"]["course_count"], 2)
        self.assertEqual(plan["constraints"]["target_credits"], "5")
        self.assertEqual(plan["constraints"]["friday_free"], "soft")
        self.assertEqual(runtime.policy_context["pending_messages"], [request])
        self.assertEqual(len([n for n, _ in self.school.calls if n == "query_my_records"]), 1)
        # A new request with a pending clarification must not inherit its goals.
        changed = self.harness("换个需求，推荐合成课程5学分。", clarification=pending)
        changed.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        new_plan = changed.execute(NAMES[1], {**self.args(), "request_basis": "current_message"})["result"]
        self.assertEqual(changed.policy_context["pending_messages"], [])
        self.assertEqual(new_plan["constraints"]["friday_free"], "none")
        self.assertIsNone(new_plan["constraints"].get("course_count"))
        # A model claim cannot create a pending human request or read sources.
        missing = self.harness()
        before = len(self.school.calls)
        with self.assertRaises(AccessError) as caught:
            missing.execute(NAMES[1], args)
        self.assertEqual(caught.exception.reason, "clarification_context_missing")
        self.assertEqual(len(self.school.calls), before)
        from course_assistant.personal_course_plans import validate_course_plan_args
        for basis in (None, True, "all_history", []):
            with self.subTest(basis=basis), self.assertRaises(ValueError):
                validate_course_plan_args(NAMES[1], {**self.args(), "request_basis": basis})

    @staticmethod
    def args(kind="courses"):
        return {"recommendation_kind": kind, "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "none"}

    @staticmethod
    def answer(messages, tools):
        last = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
        return final({'kind': 'answer', 'text': '本轮返回了课程条件测试资料，请按实际方案核对。', 'evidence_ids': ([last['evidence_id']])})

    def dialogue(self, harness, responses, message):
        model = ScriptedModel(responses)
        result, _ = PersonalDialogueService(model, self.authorize, harness.execute).run(message, data_use="personal_retrieval")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["repair_attempts"], 0)
        self.assertNotIn("PRIVATE-RAW", json.dumps(model.calls))
        return result, model

    def test_three_turn_generation_revision_and_explanation_without_prepare(self):
        first = self.harness()
        result, first_model = self.dialogue(first, [call(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM}),
            call(NAMES[1], self.args(), "compute"), self.answer], "推荐合成课程5学分")
        declared = {tool["function"]["name"] for tool in first_model.calls[0][1]}
        self.assertNotIn("prepare_recommendation_inputs", declared)
        self.assertIn("check_recommendation_inputs", declared)
        plan = result["recommendation_result"]
        self.assertTrue(plan["options"])
        self.assertEqual([n for n, _ in self.school.calls], ["get_my_context", NAMES[0], "query_my_records", "search_my_curriculum", NAMES[1]])
        prepared = result["tool_results"][-1]["result"]["prepared_sources"]
        self.assertTrue(prepared[0]["result"]["source_grade_summary"]["evidence_id"].startswith("S"))
        self.assertTrue(prepared[1]["result"]["matches"][0]["evidence_id"].startswith("E"))
        ref = plan["plan_ref"]
        message = "周五必须空闲，其他条件保持"
        second = self.harness(message, ref)
        revised, _ = self.dialogue(second, [call(NAMES[3], {"plan_ref": ref}),
            call(NAMES[2], {"plan_ref": ref, "action": "revise", "expected_revision": 0,
                "friday_free": "hard"}, "revise"), self.answer], message)
        self.assertEqual(revised["recommendation_result"]["revision"], 1)
        self.assertEqual(revised["recommendation_result"]["constraints"]["friday_free"], "hard")
        self.assertEqual(len([n for n, _ in self.school.calls if n == "query_my_records"]), 2)
        before = len(self.school.calls)
        third = self.harness("解释已有方案", ref)
        explained, _ = self.dialogue(third, [call(NAMES[3], {"plan_ref": ref}), self.answer], "解释已有方案")
        self.assertEqual([n for n, _ in self.school.calls[before:]], ["get_my_context", NAMES[3]])
        self.assertEqual(explained["tool_results"][-1]["result"]["result"]["revision"], 1)
        self.assertEqual(third.receipts, [])

    def test_previously_queried_scope_is_reused_only_within_this_turn(self):
        runtime = self.harness()
        runtime.execute("query_my_records", {"operation": "major_academic", "target_term": "ALL-HISTORY", "mode": "summary"})
        runtime.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        result = runtime.execute(NAMES[1], self.args())
        self.assertEqual([c["name"] for c in result["prepared_sources"]], ["search_my_curriculum"])
        self.assertEqual(len([n for n, _ in self.school.calls if n == "query_my_records"]), 1)
        self.assertEqual(self.harness().receipts, [])

    def test_source_failure_is_not_retried_or_replaced_by_old_facts(self):
        runtime = self.harness()
        self.school.failures.add("major_academic")
        runtime.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        for _ in range(2):
            result = runtime.execute(NAMES[1], self.args())
            self.assertEqual(result["status"], "not_ready")
            self.assertEqual(result["result"]["reason"], "source_preparation_required")
            self.assertEqual(result["result"]["options"], [])
        self.assertEqual(len([n for n, _ in self.school.calls if n == "query_my_records"]), 1)
        self.assertEqual(self.school.plans.current_plan_hint(), None)

    def test_revocation_during_source_read_aborts_before_other_sources_or_compute(self):
        runtime = self.harness()
        runtime.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.school.after_read = lambda: setattr(self, "revoked", True)
        with self.assertRaises(AccessError):
            runtime.execute(NAMES[1], self.args())
        self.assertEqual([n for n, _ in self.school.calls], [NAMES[0], "query_my_records"])

    def test_unsearched_or_foreign_plan_never_acquires_sources(self):
        runtime = self.harness()
        self.assertEqual(runtime.execute(NAMES[1], self.args())["result"]["reason"], "course_search_required")
        try:
            runtime.execute(NAMES[2], {"plan_ref": "PLN-" + "a" * 48, "action": "revise", "expected_revision": 0})
        except ValueError:
            pass
        self.assertFalse(any(n in {"query_my_records", "search_my_curriculum"} for n, _ in self.school.calls))

    def test_partial_timetable_stops_after_one_automatic_acquisition(self):
        runtime = self.harness()
        result, model = self.dialogue(runtime, [call(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM}),
            call(NAMES[1], self.args("timetable"), "compute"), self.answer], "用合成课程排5学分的课表")
        self.assertEqual(result["recommendation_result"]["reason"], "timetable_not_complete")
        self.assertEqual(result["task_progress"]["status"], "blocked")
        self.assertEqual([t["function"]["name"] for t in model.calls[-1][1]], ["query_my_records", "finish_answer"])

    def test_auto_child_projection_rejects_extra_or_cross_scope_payload(self):
        runtime = self.harness()
        runtime.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        result = runtime.execute(NAMES[1], self.args())
        for alter in ("child", "scope", "tool"):
            wrong = deepcopy(result)
            if alter == "child":
                wrong["prepared_sources"][0]["owner"] = "SYNTHETIC-OTHER"
            elif alter == "scope":
                wrong["prepared_sources"][0]["arguments"]["target_term"] = "2025-2026-AW"
            else:
                wrong["dependency_task"]["target_term"] = "2025-2026-AW"
            with self.subTest(alter=alter), self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._project(NAMES[1], wrong, self.args(), "abc", {}, 1, [0, 0, {}], set())

    def test_owned_plan_uses_canonical_policy_instead_of_old_preparation_prose(self):
        previous = {"tasks": [{"tool": "prepare_recommendation_inputs", "hard_preferences": ["周五必须空闲"],
            "soft_preferences": ["尽量周四空闲"]}]}
        harness = AcademicHarness(self.school, None, authorize=self.authorize,
            generation_authorize=self.authorize, adopt=lambda _: None, emit=lambda *_: None,
            user_message="解释已有方案", active_plan_ref="PLN-" + "a" * 48, task_context=previous)
        self.assertEqual(harness.policy_context["hard_preferences"], [])
        self.assertEqual(harness.policy_context["soft_preferences"], [])

    def test_catalog_describes_available_routes_without_identity_or_old_facts(self):
        context = {**self.school.context, "password": "PRIVATE-RAW", "old_records": ["PRIVATE-RAW"]}
        catalog = resource_catalog(context)
        self.assertNotIn("PRIVATE-RAW", json.dumps(catalog))
        self.assertFalse(catalog["compute_dependencies_managed"])
        runtime = self.harness()
        self.assertTrue(resource_catalog(runtime.execute("get_my_context", {}))["compute_dependencies_managed"])
        self.assertEqual(self.school.calls[-1][0], "get_my_context")


if __name__ == "__main__":
    unittest.main()
