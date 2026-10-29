"""Synthetic identities and schemas exercise the opaque personal test facade."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.auth import AccessError, AuthManager
from course_assistant.personal_course_plans import (
    CURRENT_TERM, NAMES, PersonalCoursePlanService, course_plan_tool_schemas,
    project_course_plan_result, validate_course_plan_args,
    valid_computation_summary,
)
from course_assistant.school_accounts import SchoolAccountService
from course_assistant.storage import Store
from tests.test_current_term_recommendation import fixture, write_delivery
from tests.test_school_accounts import FakeSchoolClient, PROFILE_B


def sources(*, timetable=False, day=6, observation="OBS-" + "e" * 32):
    result = {"major_academic": {"status": "available", "verified": True},
              "curriculum": {"status": "available", "verified": True}}
    if timetable:
        result["timetable"] = {"status": "available", "verified": True, "observation_id": observation,
            "records": [{"field_states": {"meetings": "known"}, "meetings": [{"weekday": day, "periods": [7, 8],
                "field_states": {"weekday": "known", "periods": "known"}}]}]}
    return result


class PersonalCoursePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = write_delivery(self.temp.name, fixture())
        self.scope = {"scope_key": "SYNTHETIC-OWNER-GENERATION"}
        self.service = PersonalCoursePlanService(self.path, CURRENT_TERM, lambda: deepcopy(self.scope))
        self.turn = []

    def search(self, turn=None):
        return self.service.execute(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM}, turn_identity=self.turn if turn is None else turn)

    def test_initial_clarification_with_avoid_friday_reaches_solver_once(self):
        self.search()
        context = {"user_message": "从我培养方案的专业课中挑选候选，先看建议大三修的课，保留刚才的3门、至少6学分和尽量避开周五。仅做历史数据的条件测试。",
            "hard_preferences": [], "active_plan_ref": None}
        result = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 3, "max_credits": 12, "friday_free": "soft"},
            sources=sources(), turn_identity=self.turn, policy_context=context, enforce_policy=True)["result"]
        self.assertIsNone(result["reason"])
        self.assertTrue(result["options"])
        self.assertEqual(result["constraints"]["course_count"], 3)
        self.assertEqual(result["constraints"]["target_credits"], "6")
        self.assertIsNone(result["constraints"]["max_credits"])
        self.assertEqual(result["constraints"]["friday_free"], "soft")
        self.assertTrue(all(len(option["classes"]) == 3 for option in result["options"]))

    def test_human_course_count_reaches_solver_projection_inspect_and_revision(self):
        self.search()
        context = {"user_message": "选课程名含“Synthetic”的3门课程，至少6学分，周五尽量空", "hard_preferences": [], "active_plan_ref": None}
        first = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 7, "max_credits": 8, "friday_free": "soft"},
            sources=sources(), turn_identity=self.turn, policy_context=context, enforce_policy=True)
        plan = project_course_plan_result(first)["result"]
        self.assertEqual(plan["constraints"]["course_count"], 3)
        self.assertEqual(plan["constraints"]["target_credits"], "6")
        self.assertIsNone(plan["constraints"]["max_credits"])
        self.assertTrue(plan["options"])
        self.assertTrue(all(len(option["classes"]) == 3 for option in plan["options"]))
        context.update(active_plan_ref=plan["plan_ref"], user_message="只解释保存方案")
        inspected = self.service.execute(NAMES[3], {}, policy_context=context, enforce_policy=True)
        self.assertEqual(project_course_plan_result(inspected)["result"]["computation_summary"]["course_count"], 3)
        context["user_message"] = "锁定A第一门，其他条件都照旧。"
        locked_ref = plan["options"][0]["classes"][0]["class_ref"]
        locked = self.service.execute(NAMES[2], {"plan_ref": plan["plan_ref"], "expected_revision": 0,
            "action": "revise", "lock_class_refs": [locked_ref]}, sources=sources(), turn_identity=[],
            policy_context=context, enforce_policy=True)
        self.assertEqual(locked["result"]["locked_class_refs"], [locked_ref])
        context["user_message"] = "现在把周五改成必须空，候选范围、锁定、原来的3门课和至少6学分都保留。"
        revised = self.service.execute(NAMES[2], {"plan_ref": plan["plan_ref"], "expected_revision": 1,
            "action": "revise", "friday_free": "hard"}, sources=sources(), turn_identity=[],
            policy_context=context, enforce_policy=True)
        result = project_course_plan_result(revised)["result"]
        self.assertEqual(result["constraints"]["course_count"], 3)
        self.assertEqual(result["constraints"]["target_credits"], "6")
        self.assertEqual(result["constraints"]["friday_free"], "hard")
        self.assertEqual(result["locked_class_refs"], [locked_ref])
        self.assertEqual(result["data_version"], plan["data_version"])
        self.assertEqual(self.service._plans[plan["plan_ref"]]["codes"], ["SYN-A", "SYN-B", "SYN-C"])
        self.assertIsNone(result["constraints"]["max_credits"])
        self.assertEqual(result["options"], [])

    def test_colisted_code_projects_observation_and_admits_only_canonical_candidate(self):
        data = fixture()
        data["courses"][0]["definitions"][0]["fields"]["display_code"] = {"state": "known", "value": "SYN-A(99123450)"}
        self.path = write_delivery(self.temp.name, data)
        found = self.service.execute(NAMES[0], {"query": "99123450", "target_term": CURRENT_TERM}, turn_identity=self.turn)
        row = found["result"]["courses"][0]
        self.assertEqual(row["course_code"], "SYN-A")
        self.assertEqual(row["observed_code_links"][0]["verification_status"], "observed")
        legacy = deepcopy(found)
        legacy["result"]["courses"][0].pop("observed_code_links")
        self.assertEqual(project_course_plan_result(legacy), legacy)
        for change in ({"verification_status": "verified"}, {"stable_course_identity": True},
                       {"code": "/PRIVATE/PATH"}, {"code": "12345678901"}, {"code": "SYN-A"}):
            invalid = deepcopy(found)
            invalid["result"]["courses"][0]["observed_code_links"][0].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid_course_plan_result"):
                project_course_plan_result(invalid)
        args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
                "course_codes": ["99123450"], "target_credits": 3, "friday_free": "none"}
        refused = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn)
        self.assertEqual(refused["result"]["reason"], "course_search_required")
        args["course_codes"] = ["SYN-A"]
        actual = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn)
        self.assertTrue(actual["result"]["options"])

    def generate(self, *, kind="courses", source=None, extra=None):
        self.search()
        args = {"recommendation_kind": kind, "target_term": CURRENT_TERM, "course_codes": ["SYN-A", "SYN-B", "SYN-C"],
                "target_credits": 5, "friday_free": "soft", **(extra or {})}
        return self.service.execute(NAMES[1], args, sources=sources(timetable=kind == "timetable") if source is None else source,
                                    turn_identity=self.turn)

    def test_reference_sampling_rejects_numeric_runs_without_weakening_sensitive_guard(self):
        from course_assistant.personal_course_plans import _new_reference
        from course_assistant.personal_dialogue import _guard, PersonalDialogueError
        bad, safe = "123456789012345678" + "a" * 30, "b" * 48
        with patch("course_assistant.personal_course_plans.secrets.token_hex", side_effect=[bad, safe]) as rng:
            self.assertEqual(_new_reference("CLS-"), "CLS-" + safe)
            self.assertEqual(rng.call_count, 2)
            self.assertTrue(all(c.args == (24,) for c in rng.call_args_list))
        _guard("CLS-" + safe)
        with self.assertRaises(PersonalDialogueError):
            _guard("CLS-" + bad)
        with self.assertRaises(PersonalDialogueError):
            _guard("123456789012345678")
        with patch("course_assistant.personal_course_plans.secrets.token_hex", return_value=safe) as rng:
            self.assertEqual(_new_reference("PLN-"), "PLN-" + safe)
            self.assertEqual(rng.call_count, 1)

    def test_reference_exhaustion_is_bounded_and_keeps_new_plan_unpublished(self):
        with patch("course_assistant.personal_course_plans.secrets.token_hex", return_value="1" * 48) as rng:
            with self.assertRaisesRegex(RuntimeError, "^course_plan_reference_unavailable$"):
                self.generate()
        self.assertEqual(rng.call_count, 16)
        self.assertEqual(self.service._plans, {})

    def test_plan_and_class_references_survive_unchanged_egress_guard_after_resampling(self):
        from course_assistant.personal_dialogue import _guard
        bad = "123456789012345678" + "a" * 30
        tokens = [token for n in range(8) for token in (bad, "a" * 46 + f"{n:02x}")]
        with patch("course_assistant.personal_course_plans.secrets.token_hex", side_effect=tokens) as rng:
            result = self.generate()
        self.assertTrue(result["result"]["options"])
        self.assertEqual(rng.call_count, 8)  # one plan and three distinct classes
        _guard(json.dumps(result, ensure_ascii=False))
        project_course_plan_result(result)

    def test_reference_exhaustion_during_revision_preserves_owned_state(self):
        initial = self.generate(extra={"required_course_codes": ["SYN-A", "SYN-B"], "max_credits": 6})["result"]
        ref = initial["plan_ref"]
        before = deepcopy(self.service._plans)
        with patch("course_assistant.personal_course_plans.secrets.token_hex", return_value="1" * 48) as rng:
            with self.assertRaisesRegex(RuntimeError, "^course_plan_reference_unavailable$"):
                self.service.execute(NAMES[2], {"plan_ref": ref, "action": "revise", "expected_revision": 0,
                    "target_credits": 8, "max_credits": 10}, sources=sources())
        self.assertEqual(rng.call_count, 16)
        self.assertEqual(self.service._plans, before)

    def test_unresolved_draft_reference_uses_same_bounded_sampling(self):
        from course_assistant.personal_dialogue import _guard
        self.search()
        args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B"], "target_credits": 5, "friday_free": "none"}
        policy = {"user_message": "必须在紫金港上课", "hard_preferences": ["必须在紫金港上课"], "active_plan_ref": None}
        bad, safe = "1" * 48, "b" * 48
        with patch("course_assistant.personal_course_plans.secrets.token_hex", side_effect=[bad, safe]):
            result = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn, policy_context=policy)
        self.assertEqual(result["result"]["reason"], "hard_preferences_unresolved")
        self.assertEqual(result["result"]["plan_ref"], "PLN-" + safe)
        _guard(json.dumps(result, ensure_ascii=False))
        before = deepcopy(self.service._plans)
        with patch("course_assistant.personal_course_plans.secrets.token_hex", return_value=bad) as rng:
            with self.assertRaisesRegex(RuntimeError, "^course_plan_reference_unavailable$"):
                self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn, policy_context=policy)
        self.assertEqual(rng.call_count, 16)
        self.assertEqual(self.service._plans, before)

    def test_search_generate_compare_inspect_and_revise_use_opaque_refs_only(self):
        result = self.generate()["result"]
        self.assertTrue(result["options"])
        self.assertTrue(all(o["observed_slot_result"] == "pass" and o["full_semantics"] == "unverified" for o in result["options"]))
        compared = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "compare", "expected_revision": 0,
            "option_labels": ["A", "B"]}, sources=sources())["result"]
        self.assertEqual([o["label"] for o in compared["options"]], ["A", "B"])
        class_ref = result["options"][0]["classes"][0]["class_ref"]
        revised = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [class_ref], "friday_free": "hard"}, sources=sources())["result"]
        self.assertEqual(revised["revision"], 1)
        self.assertIn(class_ref, revised["locked_class_refs"])
        inspected = self.service.execute(NAMES[3], {})["result"]
        self.assertTrue(inspected["selection_hint"]["selection_only"])
        self.assertTrue(inspected["selection_hint"]["fresh_sources_required"])
        self.assertEqual(inspected["options"], [])
        text = json.dumps([result, compared, revised, inspected])
        for private in ("obs-SYN-", "section-SYN-", "observation_ids", "/Users/", str(self.path), "PRIVATE-", "weekday_period_text\": []"):
            self.assertNotIn(private, text)
        self.assertEqual(len(course_plan_tool_schemas()), 4)

    def test_inspect_explains_actual_minimum_and_soft_tradeoff_without_io_or_recalculation(self):
        data = fixture()
        data["courses"][0]["definitions"][0]["fields"]["credits"]["value"] = "4"
        write_delivery(self.temp.name, data)
        generated = self.generate(extra={"target_credits": 6})["result"]
        self.assertEqual([(o["total_credits"], o["soft_preference_evaluation"]["violation_count"])
                          for o in generated["options"][:2]], [("7", 0), ("6", 1)])
        ref = generated["options"][0]["classes"][0]["class_ref"]
        revised = self.service.execute(NAMES[2], {"plan_ref": generated["plan_ref"], "action": "revise",
            "expected_revision": 0, "lock_class_refs": [ref]}, sources=sources())["result"]
        before = deepcopy(self.service._plans)
        with patch("course_assistant.personal_course_plans.CurrentTermCourseDataTest.from_delivery", side_effect=AssertionError("no source read")), \
                patch("course_assistant.personal_course_plans.CurrentTermCourseDataTest.recommend", side_effect=AssertionError("no solver")):
            inspected = self.service.execute(NAMES[3], {}, policy_context=self.policy("为什么目标6是7，只解释不修改", revised["plan_ref"]))["result"]
        summary = inspected["computation_summary"]
        self.assertTrue(valid_computation_summary(summary))
        self.assertEqual(summary["credit_target"], {"minimum": "6", "maximum": None})
        self.assertEqual([(o["label"], o["excess_above_minimum"], o["soft_meeting_count"], o["locked_class_count"])
                          for o in summary["options"][:2]], [("A", "1", 0, 1), ("B", "0", 1, 1)])
        self.assertEqual(summary["scope"], {"input_course_count": 3, "returned_search_count": 3, "omitted_count": 0,
            "complete_within_input_scope": True, "limit_reached": False, "school_offerings_complete": False})
        self.assertFalse(summary["solver_executed_this_call"])
        self.assertFalse(summary["current_sources_read"])
        self.assertEqual(summary["ranking"]["criteria"][1:4], ["soft_meeting_count", "known_credits", "excess_above_minimum"])
        self.assertEqual(summary["rejections"], {"status": "not_recorded", "counts": []})
        self.assertEqual(summary["exact_target"], {"status": "not_recorded"})
        self.assertEqual(inspected["revision"], revised["revision"])
        self.assertEqual(inspected["data_version"], revised["data_version"])
        self.assertEqual(self.service._plans, before)
        self.assertTrue(all(v in {"missing", "not_required", "not_requested"} for v in inspected["source_preparation"].values()))
        blocked = self.service.execute(NAMES[3], {"plan_ref": generated["plan_ref"]},
            policy_context=self.policy("查看", None), enforce_policy=True)
        self.assertIsNone(blocked["result"]["computation_summary"])

    def test_inspect_old_computation_metadata_stays_unknown_and_search_omission_is_transparent(self):
        generated = self.generate(extra={"course_codes": ["SYN-A", "SYN-B"], "target_credits": 6})["result"]
        inspected = self.service.execute(NAMES[3], {})
        summary = inspected["result"]["computation_summary"]
        self.assertEqual(summary["scope"]["returned_search_count"], 3)
        self.assertEqual(summary["scope"]["omitted_count"], 1)
        state = self.service._plans[generated["plan_ref"]]
        state.pop("returned_search_codes")
        state.pop("ranking")
        for option in state["projection"]:
            option.pop("soft_preference_evaluation")
        historical = self.service.execute(NAMES[3], {})
        old = historical["result"]["computation_summary"]
        self.assertIsNone(old["scope"]["returned_search_count"])
        self.assertIsNone(old["scope"]["omitted_count"])
        self.assertEqual(old["ranking"], {"version": None, "criteria": []})
        self.assertIsNone(old["options"][0]["soft_meeting_count"])
        legacy = deepcopy(historical)
        legacy["result"].pop("computation_summary")
        self.assertEqual(project_course_plan_result(legacy), legacy)

    def test_computation_summary_projection_checks_origin_arithmetic_and_owned_inspect(self):
        generated = self.generate()
        inspected = self.service.execute(NAMES[3], {})
        for field, value in (("solver_executed_this_call", True), ("current_sources_read", True),
                             ("origin", "current_school_facts")):
            invalid = deepcopy(inspected)
            invalid["result"]["computation_summary"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "invalid_course_plan_result"):
                project_course_plan_result(invalid)
        invalid = deepcopy(inspected)
        invalid["result"]["computation_summary"]["options"][0]["excess_above_minimum"] = "99"
        with self.assertRaisesRegex(ValueError, "invalid_course_plan_result"):
            project_course_plan_result(invalid)
        invalid = deepcopy(generated)
        invalid["result"]["computation_summary"] = inspected["result"]["computation_summary"]
        with self.assertRaisesRegex(ValueError, "invalid_course_plan_result"):
            project_course_plan_result(invalid)

    def test_real_retention_followup_executes_lock_and_friday_without_changing_scope_or_credit_bounds(self):
        from course_assistant.course_plan_policy import authorize_changes
        data = fixture()
        data["courses"][1]["definitions"][0]["fields"]["name"]["value"] = "非结构化数据分析与应用"
        write_delivery(self.temp.name, data)
        self.service.execute(NAMES[0], {"query": "SYN-", "target_term": CURRENT_TERM}, turn_identity=self.turn)
        first = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 6, "friday_free": "soft"},
            sources=sources(), turn_identity=self.turn)["result"]
        class_ref = next(row["class_ref"] for row in first["options"][0]["classes"] if row["course_code"] == "SYN-B")
        before = deepcopy(self.service._plans[first["plan_ref"]])
        message = "锁定非结构化数据分析与应用，周五改为必须空，其他课程范围和学分条件都保留，仍不核本人课表和考试。"
        args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "friday_free": "hard", "lock_class_refs": [class_ref], "change_evidence": [
                {"field": "lock_class_refs", "quote": "锁定非结构化数据分析与应用", "class_refs": [class_ref]},
                {"field": "friday_free", "quote": "周五改为必须空"}]}
        result = self.service.execute(NAMES[2], args, sources=sources(),
            policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["constraints"]["friday_free"], "hard")
        self.assertEqual(result["constraints"]["target_credits"], "6")
        self.assertIsNone(result["constraints"]["max_credits"])
        self.assertEqual(result["locked_class_refs"], [class_ref])
        self.assertTrue(all(row["friday_meeting_count"] == 0 for row in result["options"]))
        after = self.service._plans[first["plan_ref"]]
        self.assertEqual(after["codes"], before["codes"])
        self.assertEqual(after["constraints"]["input_version_refs"], before["constraints"]["input_version_refs"])
        prior = {"friday_free": "soft", "target_credits": "6", "max_credits": None}
        for proposed in ({"target_credits": 3}, {"max_credits": 8}, {"unlock_class_refs": [class_ref]}):
            self.assertEqual(authorize_changes(message, proposed, prior_constraints=prior,
                class_metadata=before["class_metadata"])["status"], "needs_clarification")
        # A change_evidence entry alone cannot add the model's omitted lock arg.
        confirmation = "确认，仍是原来的三门课程，至少6学分、不设最高学分上限。请锁定非结构化数据分析与应用，把周五设为必须空，暂不核本人课表和考试。"
        proof = authorize_changes(confirmation, {"friday_free": "hard"}, prior_constraints=prior,
            class_metadata=before["class_metadata"], change_evidence=[{"field": "friday_free", "quote": "把周五设为必须空"}])
        self.assertEqual(proof["status"], "ready")
        self.assertNotIn("lock_class_refs", proof["authorized_patch"])
        self.assertEqual(authorize_changes("请把周五设置为必须空", {"friday_free": "hard"},
            prior_constraints=prior, class_metadata={})["status"], "ready")

    def test_generation_requires_current_turn_actual_search_admission(self):
        args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM, "course_codes": ["SYN-A"], "target_credits": 3, "friday_free": "none"}
        self.assertEqual(self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn)["result"]["reason"], "course_search_required")
        self.search()
        self.assertEqual(self.service.execute(NAMES[1], args, sources=sources(), turn_identity=[])["result"]["reason"], "course_search_required")
        self.assertEqual(self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-UNSEARCHED"]}, sources=sources(), turn_identity=self.turn)["result"]["reason"], "course_search_required")

    def initial_pool(self, codes, *, target=6, kind="courses", required=None):
        self.search()
        args = {"recommendation_kind": kind, "target_term": CURRENT_TERM, "course_codes": codes,
            "target_credits": target, "friday_free": "soft", "required_course_codes": required or []}
        message = f"至少{target}学分，周三第6到8节不能排课，尽量周五空闲"
        result = self.service.execute(NAMES[1], args, sources=sources(timetable=kind == "timetable"),
            turn_identity=self.turn, policy_context=self.policy(message))["result"]
        return result, args, message

    def test_same_turn_initial_no_option_can_expand_admitted_pool_once_and_recompute_all_conditions(self):
        first, args, message = self.initial_pool(["SYN-A"], kind="timetable")
        self.assertEqual(first["reason"], "no_observed_option_within_scope")
        self.assertTrue(first["search_status"]["complete_within_observed_scope"])
        self.assertEqual(first["actions"], ["search", "generate", "revise"])
        result = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"]},
            sources=sources(timetable=True), turn_identity=self.turn,
            policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["plan_ref"], first["plan_ref"])
        self.assertEqual(result["data_version"], first["data_version"])
        self.assertEqual(result["constraints"], first["constraints"])
        self.assertEqual(result["fixed_arrangement_count"], 1)
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["locked_class_refs"], [])
        state = self.service._plans[first["plan_ref"]]
        self.assertEqual(state["codes"], ["SYN-A", "SYN-B"])
        self.assertEqual(state["initial_pool_expansions"], 1)
        self.assertTrue(state["ever_had_options"])
        project_course_plan_result({"status": "available", "data_kind": "course_data_test", "result": result})

    def test_initial_pool_expansion_remains_bounded_after_another_computed_no_option(self):
        first, args, message = self.initial_pool(["SYN-A"], target=9)
        expanded = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(expanded["reason"], "no_observed_option_within_scope")
        self.assertEqual(expanded["revision"], 1)
        self.assertEqual(expanded["actions"], ["revise"])
        before = deepcopy(self.service._plans)
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B", "SYN-C"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)

    def test_initial_pool_expansion_never_crosses_turn_or_shrinks_replaces_or_drops_required_members(self):
        first, args, message = self.initial_pool(["SYN-A", "SYN-B"], target=9)
        before = deepcopy(self.service._plans)
        for codes in (["SYN-A"], ["SYN-B", "SYN-C"]):
            with self.subTest(codes=codes):
                denied = self.service.execute(NAMES[1], {**args, "course_codes": codes}, sources=sources(),
                    turn_identity=self.turn, policy_context=self.policy(message, first["plan_ref"]))["result"]
                self.assertEqual(denied["reason"], "locked_scope_requires_revision")
                self.assertEqual(self.service._plans, before)
        self.turn = []
        self.search()
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B", "SYN-C"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)
        required, args, message = self.initial_pool(["SYN-A"], required=["SYN-A"])
        before = deepcopy(self.service._plans)
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy(message, required["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)

    def test_initial_pool_expansion_cannot_reopen_a_plan_that_ever_had_options(self):
        first, args, _ = self.initial_pool(["SYN-A", "SYN-B"], target=3)
        self.assertTrue(first["options"])
        revised = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "target_credits": 9}, sources=sources(), policy_context=self.policy("目标改为9学分，其他条件保留", first["plan_ref"]))["result"]
        self.assertEqual(revised["reason"], "no_observed_option_within_scope")
        before = deepcopy(self.service._plans)
        denied = self.service.execute(NAMES[1], {**args, "target_credits": 9, "course_codes": ["SYN-A", "SYN-B", "SYN-C"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy("其他条件保留", first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)

    def test_initial_pool_expansion_rechecks_original_observations_even_with_same_dataset_label(self):
        first, args, message = self.initial_pool(["SYN-A"])
        before = deepcopy(self.service._plans)
        data = fixture()
        data["meetings"][0]["fields"]["periods"] = [3, 4]
        write_delivery(self.temp.name, data)
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"]}, sources=sources(),
            turn_identity=self.turn, policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)

    def test_initial_pool_expansion_requires_actual_admission_and_unchanged_data_and_policy_versions(self):
        self.service.execute(NAMES[0], {"query": "SYN-A", "target_term": CURRENT_TERM}, turn_identity=self.turn)
        args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM, "course_codes": ["SYN-A"],
            "target_credits": 6, "friday_free": "soft"}
        first = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("推荐6学分，尽量周五空闲"))["result"]
        before = deepcopy(self.service._plans)
        expanded = {**args, "course_codes": ["SYN-A", "SYN-B"]}
        denied = self.service.execute(NAMES[1], expanded, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("继续按原要求计算", first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "course_search_required")
        self.assertEqual(self.service._plans, before)
        self.search()
        with patch("course_assistant.current_term_recommendation.POLICY_VERSION", "SYN-CHANGED-POLICY"):
            denied = self.service.execute(NAMES[1], expanded, sources=sources(), turn_identity=self.turn,
                policy_context=self.policy("继续按原要求计算", first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)
        for name in ("manifest.json", "index.json"):
            path = Path(self.temp.name) / name
            value = json.loads(path.read_text())
            value["dataset_version"] = "SYN-DATA-v2"
            path.write_text(json.dumps(value))
        self.search()
        denied = self.service.execute(NAMES[1], expanded, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("继续按原要求计算", first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "locked_scope_requires_revision")
        self.assertEqual(self.service._plans, before)

    def test_initial_pool_expansion_never_invents_maximum_or_replaces_personal_fixed_arrangements(self):
        first, args, message = self.initial_pool(["SYN-A"], kind="timetable")
        before = deepcopy(self.service._plans)
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"], "max_credits": 6},
            sources=sources(timetable=True), turn_identity=self.turn,
            policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "change_evidence_required")
        self.assertEqual(self.service._plans, before)
        denied = self.service.execute(NAMES[1], {**args, "course_codes": ["SYN-A", "SYN-B"]},
            sources=sources(timetable=True, day=7), turn_identity=self.turn,
            policy_context=self.policy(message, first["plan_ref"]))["result"]
        self.assertEqual(denied["reason"], "personal_timetable_changed_requires_regeneration")
        self.assertEqual(self.service._plans, before)

    def test_sources_must_be_current_verified_and_unknown_arrangements_never_empty(self):
        self.assertEqual(self.generate(source={})["result"]["reason"], "source_preparation_required")
        self.assertEqual(self.generate(kind="timetable", source=sources())["result"]["reason"], "timetable_not_complete")
        bad = sources(timetable=True)
        bad["timetable"]["records"][0]["meetings"] = None
        self.assertEqual(self.generate(kind="timetable", source=bad)["result"]["reason"], "timetable_unparseable")
        bad["timetable"]["records"] = []
        good = self.generate(kind="timetable", source=bad)
        self.assertEqual(good["status"], "available")
        self.assertEqual(good["result"]["fixed_arrangement_count"], 0)

    def test_blocked_adjustment_retains_owned_lock_selection_without_old_fixed_facts(self):
        first = self.generate(kind="timetable")["result"]
        ref = next(row["class_ref"] for option in first["options"] for row in option["classes"]
                   if row["course_code"] == "SYN-A")
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise",
            "expected_revision": 0, "lock_class_refs": [ref]}, sources=sources(timetable=True),
            policy_context=self.policy("必须保留Synthetic SYN-A", first["plan_ref"]))["result"]
        self.assertEqual(locked["locked_class_refs"], [ref])
        self.assertEqual(locked["fixed_arrangement_count"], 1)
        failed_sources = sources(timetable=True)
        failed_sources["curriculum"] = {"status": "failed", "verified": False}
        malformed_slots = sources(timetable=True)
        malformed_slots["timetable"]["records"][0]["meetings"] = None
        args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1}
        for current_sources, reason in ((failed_sources, "source_preparation_required"),
                                        (sources(), "timetable_not_complete"),
                                        (malformed_slots, "timetable_unparseable")):
            with self.subTest(reason=reason), patch(
                    "course_assistant.personal_course_plans.CurrentTermCourseDataTest.from_delivery") as engine:
                blocked = self.service.execute(NAMES[2], args, sources=current_sources,
                    policy_context=self.policy("继续按原条件重算", first["plan_ref"]))
                engine.assert_not_called()
                result = blocked["result"]
                self.assertEqual((blocked["status"], result["reason"]), ("not_ready", reason))
                self.assertEqual((result["plan_ref"], result["revision"]), (first["plan_ref"], 1))
                self.assertEqual(result["locked_class_refs"], [ref])
                self.assertEqual(result["options"], [])
                self.assertIsNone(result["constraints"])
                self.assertIsNone(result["search_status"])
                self.assertEqual((result["fixed_arrangement_count"], result["user_busy_arrangement_count"]), (0, 0))
                self.assertNotIn("weekday_period_text", json.dumps(result))
        inspected = self.service.execute(NAMES[3], {}, policy_context=self.policy("查看原锁定", first["plan_ref"]))["result"]
        self.assertEqual(inspected["revision"], 1)
        self.assertEqual([row["class_ref"] for row in inspected["selection_hint"]["locked_classes"]], [ref])
        foreign = self.service.execute(NAMES[2], args, sources=failed_sources,
            policy_context=self.policy("继续", "PLN-" + "f" * 48))["result"]
        self.assertEqual(foreign["reason"], "stale_plan_reference")
        self.assertIsNone(foreign["plan_ref"])
        self.assertEqual(foreign["locked_class_refs"], [])

    def test_raw_slot_parser_does_not_override_conflicting_structured_fields(self):
        raw = sources(timetable=True)
        meeting = raw["timetable"]["records"][0]["meetings"][0]
        meeting.update(weekday=None, periods=None, time_raw="周六第7,8节{单周}", field_states={"weekday": "unknown", "periods": "unknown", "time_raw": "known"})
        self.assertEqual(self.generate(kind="timetable", source=raw)["status"], "available")
        meeting["field_states"]["weekday"] = "conflicting"
        self.assertEqual(self.generate(kind="timetable", source=raw)["result"]["reason"], "timetable_unparseable")

    def test_fresh_fixed_arrangements_rebind_new_receipts_but_changed_slots_reject_old_plan(self):
        result = self.generate(kind="timetable")["result"]
        args = {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0, "friday_free": "hard"}
        renewed = self.service.execute(NAMES[2], args, sources=sources(timetable=True, observation="OBS-" + "f" * 32))
        self.assertEqual(renewed["result"]["revision"], 1)
        changed = self.service.execute(NAMES[2], {**args, "expected_revision": 1}, sources=sources(timetable=True, day=4))
        self.assertEqual(changed["result"]["reason"], "personal_timetable_changed_requires_regeneration")
        self.assertEqual(changed["result"]["options"], [])
        self.assertNotIn("OBS-", json.dumps(renewed))

    def test_stale_revision_scope_and_foreign_refs_are_not_replayed(self):
        result = self.generate()["result"]
        args = {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0, "friday_free": "hard"}
        self.service.execute(NAMES[2], args, sources=sources())
        stale = self.service.execute(NAMES[2], args, sources=sources())
        self.assertEqual(stale["result"]["reason"], "stale_plan_revision")
        foreign = self.service.execute(NAMES[2], {**args, "plan_ref": "PLN-" + "a" * 48}, sources=sources())
        self.assertEqual(foreign["result"]["reason"], "stale_plan_reference")
        self.scope["scope_key"] = "SYNTHETIC-OTHER-GENERATION"
        with self.assertRaises(PermissionError): self.service.execute(NAMES[3], {})

    def test_overconstrained_locked_plan_can_be_inspected_unlocked_and_recomputed(self):
        result = self.generate()["result"]
        ref = next(c["class_ref"] for o in result["options"] for c in o["classes"] if c["course_code"] == "SYN-C")
        failed = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref], "friday_free": "hard"}, sources=sources())
        self.assertEqual(failed["result"]["options"], [])
        inspected = self.service.execute(NAMES[3], {"plan_ref": result["plan_ref"]})["result"]["selection_hint"]
        self.assertEqual(inspected["locked_classes"][0]["class_ref"], ref)
        recovered = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [ref]}, sources=sources())
        self.assertTrue(recovered["result"]["options"])
        self.assertEqual(recovered["result"]["locked_class_refs"], [])

    def test_foreign_class_ref_and_corrupted_dataset_do_not_update_plan_revision(self):
        result = self.generate()["result"]
        with self.assertRaises(ValueError):
            self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0,
                "lock_class_refs": ["CLS-" + "c" * 48]}, sources=sources())
        self.assertEqual(self.service.current_plan_hint()["revision"], 0)
        (Path(self.temp.name) / "meetings.json").write_text("[]")
        failed = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "compare", "expected_revision": 0,
            "option_labels": ["A"]}, sources=sources())
        self.assertEqual(failed["result"]["reason"], "course_data_unavailable")
        self.assertEqual(failed["result"]["options"], [])

    def test_strict_projection_and_arguments_reject_promotion_extra_fields_and_raw_ids(self):
        result = self.generate()
        for key, value in (("can_generate", True), ("source_path", "/Users/private"), ("observation_id", "OBS-" + "e" * 32)):
            changed = deepcopy(result)
            changed["result"][key] = value
            with self.assertRaises(ValueError): project_course_plan_result(changed)
        changed = deepcopy(result)
        changed["result"]["options"][0]["full_semantics"] = "verified"
        with self.assertRaises(ValueError): project_course_plan_result(changed)
        for args in ({"query": "x", "target_term": "2024-2025-AW"}, {"query": "x", "target_term": CURRENT_TERM, "owner": "someone"}):
            with self.assertRaises(ValueError): validate_course_plan_args(NAMES[0], args)

    def test_projection_malformed_json_types_have_one_controlled_error(self):
        result = self.generate()
        for key in result["result"]:
            changed = deepcopy(result)
            changed["result"][key] = {"malformed": True}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "invalid_course_plan_result"):
                project_course_plan_result(changed)

    def test_changed_dataset_version_cannot_reuse_previously_issued_selection_refs(self):
        result = self.generate()["result"]
        for filename in ("manifest.json", "index.json"):
            path = Path(self.temp.name) / filename
            doc = json.loads(path.read_text())
            doc["dataset_version"] = "SYN-DATA-v2"
            path.write_text(json.dumps(doc))
        changed = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "compare", "expected_revision": 0,
            "option_labels": ["A"]}, sources=sources())
        self.assertEqual(changed["result"]["reason"], "course_data_version_changed")
        self.assertEqual(changed["result"]["options"], [])

    def policy(self, message, active=None, *, hard=None, soft=None):
        return {"user_message": message, "active_plan_ref": active, "hard_preferences": hard or [], "soft_preferences": soft or []}

    def generate_with_policy(self, message, active=None, *, hard=None, soft=None):
        self.search()
        return self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "none"},
            sources=sources(), turn_identity=self.turn, policy_context=self.policy(message, active, hard=hard, soft=soft), enforce_policy=True)

    def test_initial_friday_argument_cannot_invent_or_escalate_a_human_preference(self):
        self.search()
        base = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5}
        for message, level in (("推荐五学分", "soft"), ("推荐五学分", "hard"),
                ("如果周五尽量空会怎样", "soft"),
                ("不是要求周五必须空", "hard"), ("原文说‘周五尽量空’", "soft")):
            with self.subTest(message=message, level=level):
                args = {**base, "friday_free": level}
                before = deepcopy(args)
                rejected = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn,
                    policy_context=self.policy(message, soft=["周五尽量空"]))["result"]
                self.assertEqual(rejected["reason"], "change_evidence_required")
                self.assertIsNone(rejected["plan_ref"])
                self.assertEqual(rejected["options"], [])
                self.assertEqual(args, before)
                self.assertEqual(self.service._plans, {})
        allowed = self.service.execute(NAMES[1], {**base, "friday_free": "soft"}, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("推荐五学分，周五尽量空"))["result"]
        self.assertTrue(allowed["options"])
        self.assertEqual(allowed["constraints"]["friday_free"], "soft")

    def test_initial_friday_uses_explicit_human_level_in_one_calculation(self):
        self.search()
        for phrase, expected in (("周五尽量空", "soft"), ("周五必须空", "hard"),
                ("周五不要有课", "hard"), ("周五还是别排", "hard")):
            for proposed in ("none", "soft", "hard"):
                with self.subTest(phrase=phrase, proposed=proposed):
                    args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
                        "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": proposed}
                    original = deepcopy(args)
                    result = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn,
                        policy_context=self.policy("推荐五学分，" + phrase))["result"]
                    self.assertTrue(result["options"])
                    self.assertIsNone(result["reason"])
                    self.assertEqual(result["constraints"]["friday_free"], expected)
                    self.assertEqual(args, original)

    def test_colloquial_pending_minimum_is_bound_even_when_model_omits_it(self):
        self.search()
        context = {**self.policy('先只看这两门怎么排'), 'pending_messages': [
            '帮我安排课表，周五不要有课', '就2026-2027秋冬，先帮我挑两门课，凑够6学分。']}
        result = self.service.execute(NAMES[1], {'recommendation_kind': 'courses', 'target_term': CURRENT_TERM,
            'course_codes': ['SYN-A', 'SYN-B', 'SYN-C'], 'target_credits': 3, 'friday_free': 'none'},
            sources=sources(), turn_identity=self.turn, policy_context=context)['result']
        self.assertIsNone(result['reason'])
        self.assertTrue(result['options'])
        self.assertEqual(result['constraints']['target_credits'], '6')
        self.assertEqual(result['constraints']['course_count'], 2)
        self.assertEqual(result['constraints']['friday_free'], 'hard')
        self.assertTrue(all({c['course_code'] for c in option['classes']} == {'SYN-A', 'SYN-B'} for option in result['options']))

    def test_colloquial_named_lock_keeps_plan_version_pool_and_conditions(self):
        first = self.generate_with_policy('选两门课，凑够6学分，周五不要有课')['result']
        chosen = first['options'][0]['classes'][0]
        context = self.policy('把A方案的' + chosen['course_name'] + '锁住，别的照旧。', first['plan_ref'])
        result = self.service.execute(NAMES[2], {'plan_ref': first['plan_ref'], 'action': 'revise',
            'expected_revision': first['revision'], 'lock_class_refs': [chosen['class_ref']]},
            sources=sources(), policy_context=context)['result']
        self.assertIsNone(result['reason'])
        self.assertEqual(result['plan_ref'], first['plan_ref'])
        self.assertEqual(result['data_version'], first['data_version'])
        self.assertEqual(result['revision'], first['revision'] + 1)
        self.assertEqual(result['constraints']['course_count'], 2)
        self.assertEqual(result['constraints']['target_credits'], '6')
        self.assertEqual(result['constraints']['friday_free'], 'hard')
        self.assertEqual(result['locked_class_refs'], [chosen['class_ref']])
        self.assertTrue(all(any(c['class_ref'] == chosen['class_ref'] and c['locked'] for c in option['classes']) for option in result['options']))

    def test_initial_friday_period_scope_is_not_silently_widened_to_a_whole_day(self):
        self.search()
        base = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5}
        for phrase, level in (("周五第6到8节尽量空", "soft"), ("周五第6到8节不能排课", "hard")):
            with self.subTest(phrase=phrase):
                result = self.service.execute(NAMES[1], {**base, "friday_free": level}, sources=sources(), turn_identity=self.turn,
                    policy_context=self.policy("推荐五学分，" + phrase))["result"]
                self.assertEqual(result["reason"], "change_evidence_required")
                self.assertIsNone(result["plan_ref"])
        correct = self.service.execute(NAMES[1], {**base, "friday_free": "none"}, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("推荐五学分，周五第6到8节尽量空"))["result"]
        self.assertTrue(correct["options"])
        self.assertEqual(correct["constraints"]["friday_free"], "none")
        self.assertEqual(correct["constraints"]["soft_free_slots"], [{"weekday": 5, "periods": [6, 7, 8]}])

    def test_initial_friday_compilation_uses_current_or_trusted_pending_human_scope(self):
        self.search()
        base = {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5}
        context = {**self.policy("就是第6到8节"), "pending_messages": ["推荐五学分，尽量周五空，周三下午必须空"]}
        resumed = self.service.execute(NAMES[1], {**base, "friday_free": "soft"}, sources=sources(), turn_identity=self.turn,
            policy_context=context)["result"]
        self.assertTrue(resumed["options"])
        self.assertEqual(resumed["constraints"]["friday_free"], "soft")
        self.assertEqual(resumed["user_busy_arrangement_count"], 1)
        hard = self.service.execute(NAMES[1], {**base, "friday_free": "none"}, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("推荐五学分，周五必须空"))["result"]
        self.assertTrue(hard["options"])
        self.assertEqual(hard["constraints"]["friday_free"], "hard")

    def test_pending_hard_constraint_resumes_same_draft_without_repeating_weekday(self):
        first = self.generate_with_policy("推荐五学分，周三下午必须空")
        self.assertEqual(first["status"], "not_ready")
        draft = first["result"]
        self.assertEqual(draft["reason"], "hard_preferences_unresolved")
        self.assertEqual(draft["options"], [])
        self.assertEqual(draft["revision"], 0)
        self.assertTrue(draft["plan_ref"].startswith("PLN-"))
        bad = self.generate_with_policy("不是第6到8节", draft["plan_ref"])
        self.assertEqual(bad["result"]["reason"], "hard_preferences_unresolved")
        self.assertEqual(bad["result"]["revision"], 0)
        result = self.generate_with_policy("就是第6到8节", draft["plan_ref"], hard=["周三第6到8节不能排课"])["result"]
        self.assertEqual(result["plan_ref"], draft["plan_ref"])
        self.assertEqual(result["revision"], 1)
        self.assertTrue(result["options"])
        self.assertEqual(result["policy_status"]["status"], "ready")
        self.assertEqual(result["fixed_arrangement_count"], 0)
        self.assertEqual(result["recommendation_kind"], "courses")
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertIn("周三下午必须空", result["policy_status"]["enforced_hard_preferences"][0])

    def test_lock_infeasibility_keeps_lock_until_actual_human_unlock_and_generation_cannot_bypass(self):
        first = self.generate_with_policy("推荐五学分，尽量周五空闲")["result"]
        ref = next(c["class_ref"] for o in first["options"] for c in o["classes"] if c["course_code"] == "SYN-C")
        args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
                "lock_class_refs": [ref], "friday_free": "hard"}
        blocked = self.service.execute(NAMES[2], args, sources=sources(), policy_context=self.policy(
            "锁定Synthetic SYN-C，把周五改为必须空", first["plan_ref"]))["result"]
        self.assertEqual(blocked["options"], [])
        self.assertEqual(blocked["locked_class_refs"], [ref])
        self.assertEqual(blocked["revision"], 1)
        unauthorized = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("不是让你解除Synthetic SYN-C的锁", first["plan_ref"]))
        self.assertEqual(unauthorized["result"]["reason"], "change_evidence_required")
        self.assertEqual(unauthorized["result"]["revision"], 1)
        bypass = self.generate_with_policy("再生成一次，其他条件保留", first["plan_ref"])
        self.assertEqual(bypass["result"]["reason"], "change_evidence_required")
        self.assertEqual(self.service.current_plan_hint()["revision"], 1)
        recovered = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("能不能帮我解除刚才那个锁？其他条件保留", first["plan_ref"]))["result"]
        self.assertEqual(recovered["revision"], 2)
        self.assertEqual(recovered["locked_class_refs"], [])
        self.assertEqual(recovered["constraints"]["friday_free"], "hard")
        self.assertTrue(recovered["options"])

    def test_soft_preferences_survive_revision_and_are_not_claimed_as_ranking(self):
        first = self.generate_with_policy("优先数据库，尽量周五空闲", soft=["优先数据库"])["result"]
        self.assertEqual(first["policy_status"]["unhandled_soft_preferences"], ["优先数据库"])
        revised = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "friday_free": "hard"}, sources=sources(), policy_context=self.policy("周五改为必须空，其他条件保留", first["plan_ref"]))["result"]
        self.assertEqual(revised["policy_status"]["unhandled_soft_preferences"], ["优先数据库"])
        self.assertEqual(revised["constraints"]["friday_free"], "hard")
        relaxed = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1,
            "friday_free": "none"}, sources=sources(), policy_context=self.policy("周五有课也可以，其他条件保留", first["plan_ref"],
                hard=["周五改为必须空"]))["result"]
        self.assertEqual(relaxed["constraints"]["friday_free"], "none")
        self.assertEqual(relaxed["policy_status"]["unhandled_soft_preferences"], ["优先数据库"])

    def test_human_soft_scope_reorders_same_pool_while_preserving_lock_fixed_required_and_version(self):
        data = fixture()
        data["meetings"][1]["fields"]["weekday"] = 3
        data["meetings"][2]["fields"]["weekday"] = 4
        data["courses"][2]["definitions"][0]["fields"]["credits"]["value"] = "3"
        write_delivery(self.temp.name, data)
        self.search()
        first = self.service.execute(NAMES[1], {"recommendation_kind": "timetable", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 6, "max_credits": 6,
            "friday_free": "hard", "required_course_codes": ["SYN-A"]}, sources=sources(timetable=True),
            turn_identity=self.turn, policy_context=self.policy("至少6学分，最多6学分，周五必须空"))["result"]
        self.assertEqual([row["course_code"] for row in first["options"][0]["classes"]], ["SYN-A", "SYN-B"])
        ref = next(row["class_ref"] for row in first["options"][0]["classes"] if row["course_code"] == "SYN-A")
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(timetable=True),
            policy_context=self.policy("锁定A第一门，其他条件保留", first["plan_ref"]))["result"]
        old_state = deepcopy(self.service._plans[first["plan_ref"]]["constraints"])
        changed = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1},
            sources=sources(timetable=True), policy_context=self.policy("周五保持不变，同时周三尽量空，之前的锁定和其他条件保留", first["plan_ref"],
                soft=["周三尽量空", "周日尽量空"]))["result"]
        self.assertEqual([row["course_code"] for row in changed["options"][0]["classes"]], ["SYN-A", "SYN-C"])
        self.assertEqual(changed["constraints"]["soft_free_slots"], [{"weekday": 3, "periods": None}])
        self.assertEqual([row["soft_preference_evaluation"]["violation_count"] for row in changed["options"]], [0, 1])
        self.assertEqual(changed["policy_status"]["unhandled_soft_preferences"], [])
        self.assertEqual(changed["locked_class_refs"], [ref])
        self.assertEqual(changed["fixed_arrangement_count"], 1)
        for field in ("input_version_refs", "locked_observation_ids", "fixed_meetings", "required_course_codes", "excluded_observation_ids", "friday_free"):
            self.assertEqual(self.service._plans[first["plan_ref"]]["constraints"][field], old_state[field])
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["data_version"], locked["data_version"])
        retained = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 2},
            sources=sources(timetable=True), policy_context=self.policy("再算一次，其他条件保留", first["plan_ref"]))["result"]
        self.assertEqual(retained["constraints"]["soft_free_slots"], changed["constraints"]["soft_free_slots"])
        self.assertEqual(retained["options"][0]["classes"], changed["options"][0]["classes"])

    def test_soft_evaluation_projection_is_paired_and_checked_while_old_results_stay_readable(self):
        result = self.generate()
        current = result["result"]
        self.assertEqual(current["constraints"]["soft_free_slots"], [{"weekday": 5, "periods": None}])
        self.assertEqual(current["options"][0]["soft_preference_evaluation"]["status"], "evaluated")
        old = deepcopy(result)
        old["result"]["constraints"].pop("soft_free_slots")
        for option in old["result"]["options"]:
            option.pop("soft_preference_evaluation")
        self.assertEqual(project_course_plan_result(old), old)
        for mutation in ("missing_evaluation", "missing_constraints", "wrong_scope", "wrong_total", "false_zero", "boolean_count"):
            bad = deepcopy(result)
            c, option = bad["result"]["constraints"], bad["result"]["options"][-1]
            if mutation == "missing_evaluation":
                option.pop("soft_preference_evaluation")
            elif mutation == "missing_constraints":
                c.pop("soft_free_slots")
            elif mutation == "wrong_scope":
                option["soft_preference_evaluation"]["scopes"][0]["weekday"] = 3
            elif mutation == "wrong_total":
                option["soft_preference_evaluation"]["violation_count"] += 1
            elif mutation == "boolean_count":
                option["soft_preference_evaluation"]["scopes"][0]["meeting_count"] = True
            else:
                option["soft_preference_evaluation"]["scopes"][0]["meeting_count"] = 0
                option["soft_preference_evaluation"]["violation_count"] = 0
                option["friday_meeting_count"] = 0
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "^invalid_course_plan_result$"):
                project_course_plan_result(bad)

    def test_additional_soft_weekday_recomputes_actual_order_without_changing_hard_or_lock(self):
        data = fixture()
        data["meetings"][1]["fields"]["weekday"] = 4
        data["meetings"][2]["fields"]["weekday"] = 2
        data["courses"][2]["definitions"][0]["fields"]["credits"]["value"] = "3"
        write_delivery(self.temp.name, data)
        self.search()
        first = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 6, "max_credits": 6,
            "friday_free": "none", "required_course_codes": ["SYN-A"]}, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("至少6学分，最多6学分，周五尽量空"))["result"]
        self.assertEqual([row["course_code"] for row in first["options"][0]["classes"]], ["SYN-A", "SYN-B"])
        ref = first["options"][0]["classes"][0]["class_ref"]
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref], "friday_free": "hard"}, sources=sources(),
            policy_context=self.policy("锁定A第一门，周五改为必须空", first["plan_ref"]))["result"]
        previous = deepcopy(self.service._plans[first["plan_ref"]]["constraints"])
        message = "周五必须空和已锁定课程都保持，周四也尽量空，按这个偏好重新比较课程组合。"
        result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": locked["revision"]},
            sources=sources(), policy_context=self.policy(message, first["plan_ref"], soft=["周四尽量空", "周五尽量空"]))["result"]
        self.assertEqual([row["course_code"] for row in result["options"][0]["classes"]], ["SYN-A", "SYN-C"])
        self.assertEqual(result["constraints"]["soft_free_slots"], [{"weekday": 4, "periods": None}])
        self.assertEqual([row["soft_preference_evaluation"]["violation_count"] for row in result["options"]], [0, 1])
        self.assertEqual(result["policy_status"]["unhandled_soft_preferences"], [])
        self.assertEqual(result["locked_class_refs"], [ref])
        self.assertEqual(result["revision"], 2)
        current = self.service._plans[first["plan_ref"]]["constraints"]
        for key in ("input_version_refs", "friday_free", "locked_observation_ids", "fixed_meetings", "required_course_codes", "excluded_observation_ids", "target_credits", "max_credits"):
            self.assertEqual(current[key], previous[key])

    def test_negated_soft_conjunction_cannot_change_owned_scope_or_existing_protection(self):
        first = self.generate_with_policy("推荐五学分，周五必须空，周三尽量空，周六第1节不能排课")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))["result"]
        previous = deepcopy(self.service._plans[first["plan_ref"]]["constraints"])
        revision = locked["revision"]
        for message in ("不要周三尽量空和周四尽量空", "不用让周三尽量空和周五尽量空", "周三尽量空和周四尽量空都不要"):
            with self.subTest(message=message):
                result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": revision},
                    sources=sources(), policy_context=self.policy(message, first["plan_ref"], soft=["周四尽量空", "周五尽量空"]))["result"]
                revision += 1
                self.assertEqual(result["revision"], revision)  # an actual requested recomputation
                self.assertTrue(result["options"])
                self.assertEqual(result["constraints"]["soft_free_slots"], [{"weekday": 3, "periods": None}])
                self.assertEqual(result["constraints"]["friday_free"], "hard")
                self.assertEqual(result["locked_class_refs"], [ref])
                self.assertIn(message, result["policy_status"]["unhandled_soft_preferences"])
                current = self.service._plans[first["plan_ref"]]["constraints"]
                for key in ("soft_free_slots", "friday_free", "locked_observation_ids", "required_course_codes", "excluded_observation_ids", "fixed_meetings", "input_version_refs"):
                    self.assertEqual(current[key], previous[key])

    def test_verified_old_to_new_relaxation_recomputes_unlock_and_preserves_busy_scope(self):
        first = self.generate_with_policy("推荐五学分，周五必须空，周三第6到8节不能排课")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))["result"]
        self.assertEqual(locked["locked_class_refs"], [ref])
        phrase = "把周五从必须空放宽为尽量空"
        message = "解除刚才那个锁，" + phrase + "，最高学分改为7学分，其他条件保留"
        args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [ref], "friday_free": "soft", "max_credits": 7,
            "change_evidence": [{"field": "unlock_class_refs", "quote": "解除刚才那个锁", "class_refs": [ref]},
                {"field": "friday_free", "quote": phrase}, {"field": "max_credits", "quote": "最高学分改为7学分"}]}
        result = self.service.execute(NAMES[2], args, sources=sources(), policy_context=self.policy(
            message, first["plan_ref"], soft=["尽量周五空闲"]))["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["plan_ref"], first["plan_ref"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["data_version"], first["data_version"])
        self.assertEqual(result["locked_class_refs"], [])
        self.assertEqual(result["constraints"]["friday_free"], "soft")
        self.assertEqual(result["constraints"]["max_credits"], "7")
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["fixed_arrangement_count"], 0)

    def test_old_to_new_relaxation_cannot_hide_unknown_or_commit_unlock_before_clarification(self):
        first = self.generate_with_policy("推荐五学分，周五必须空，周三第6到8节不能排课")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))
        phrase = "把周五从必须空放宽为尽量空"
        blocked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [ref], "friday_free": "soft", "max_credits": 7,
            "change_evidence": [{"field": "unlock_class_refs", "quote": "解除刚才那个锁", "class_refs": [ref]},
                {"field": "friday_free", "quote": phrase}, {"field": "max_credits", "quote": "最高学分改为7学分"}]},
            sources=sources(), policy_context=self.policy("解除刚才那个锁，" + phrase + "且必须同校区，最高学分改为7学分",
                first["plan_ref"], soft=["尽量周五空闲"]))["result"]
        self.assertEqual(blocked["reason"], "hard_preferences_unresolved")
        self.assertEqual(blocked["revision"], 1)
        self.assertEqual(blocked["locked_class_refs"], [ref])
        state = self.service._plans[first["plan_ref"]]
        self.assertEqual(state["constraints"]["friday_free"], "hard")
        self.assertEqual(state["data_version"], first["data_version"])

    def test_friday_evidence_cannot_borrow_another_weekday_or_mutate_owned_plan(self):
        first = self.generate_with_policy("推荐五学分，周五必须空，周三第6到8节不能排课")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))
        before = deepcopy(self.service._plans)
        for message, level in (("周五原条件保留且周三尽量空", "soft"),
                ("周五保持不变，同时周三尽量空", "soft"), ("周五保持不变，周三可以有课", "none"),
                ("保留周五尽量空的要求", "soft"), ("‘周五允许有课’", "none")):
            for explicit_quote in (False, True):
                with self.subTest(message=message, explicit_quote=explicit_quote):
                    args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1, "friday_free": level}
                    if explicit_quote:
                        args["change_evidence"] = [{"field": "friday_free", "quote": message}]
                    blocked = self.service.execute(NAMES[2], args, sources=sources(),
                        policy_context=self.policy(message, first["plan_ref"]))["result"]
                    self.assertEqual(blocked["reason"], "change_evidence_required")
                    self.assertEqual(blocked["revision"], 1)
                    self.assertEqual(blocked["locked_class_refs"], [ref])
                    self.assertEqual(self.service._plans, before)
        self.assertEqual(self.service.current_plan_hint()["plan_ref"], first["plan_ref"])
        self.assertEqual(self.service.current_plan_hint()["revision"], 1)

    def test_live_policy_context_is_required_and_conversation_pointer_cannot_reveal_other_plan(self):
        first = self.generate_with_policy("推荐五学分")["result"]
        missing = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "compare", "expected_revision": 0,
            "option_labels": ["A"]}, sources=sources(), enforce_policy=True)
        self.assertEqual(missing["result"]["reason"], "stale_plan_reference")
        for args in ({}, {"plan_ref": first["plan_ref"]}):
            inspected = self.service.execute(NAMES[3], args, policy_context=self.policy("看一下"), enforce_policy=True)
            self.assertIsNone(inspected["result"]["plan_ref"])
        self.assertIsNone(self.service.current_plan_hint(None, current_dialogue=True))
        self.search()
        missing = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A"], "target_credits": 3, "friday_free": "none"}, sources=sources(),
            turn_identity=self.turn, enforce_policy=True)
        self.assertEqual(missing["result"]["reason"], "policy_context_required")

    def test_courses_upgrade_to_timetable_recomputes_fresh_fixed_and_keeps_lock_policy_and_kind(self):
        first = self.generate_with_policy("推荐五学分，周三第6到8节不能排课")["result"]
        class_ref = next(row["class_ref"] for option in first["options"] for row in option["classes"] if row["course_code"] == "SYN-A")
        locked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [class_ref]}, sources=sources(), policy_context=self.policy("必须保留Synthetic SYN-A", first["plan_ref"]))["result"]
        self.assertEqual(locked["revision"], 1)
        fresh = sources(timetable=True, day=2)
        fresh["timetable"]["records"][0]["meetings"][0]["periods"] = [1, 2]
        self.search()
        args = {"recommendation_kind": "timetable", "target_term": CURRENT_TERM, "course_codes": ["SYN-A", "SYN-B", "SYN-C"],
                "target_credits": 5, "friday_free": "none"}
        upgraded = self.service.execute(NAMES[1], args, sources=fresh, turn_identity=self.turn,
            policy_context=self.policy("把这些排成课表，其他条件保留", first["plan_ref"]))["result"]
        self.assertTrue(upgraded["options"])
        self.assertEqual(upgraded["revision"], 2)
        self.assertEqual(upgraded["locked_class_refs"], [class_ref])
        self.assertEqual(upgraded["fixed_arrangement_count"], 1)
        self.assertEqual(upgraded["recommendation_kind"], "timetable")
        self.assertEqual(upgraded["user_busy_arrangement_count"], 1)
        self.assertTrue(all("SYN-B" not in [row["course_code"] for row in option["classes"]] for option in upgraded["options"]))
        self.assertEqual(self.service.required_sources(NAMES[1], {**args, "recommendation_kind": "courses"},
            active_plan_ref=first["plan_ref"]), "timetable")
        missing_fixed = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "compare", "expected_revision": 2,
            "option_labels": ["A"]}, sources=sources(), policy_context=self.policy("比较A", first["plan_ref"]))
        self.assertEqual(missing_fixed["result"]["reason"], "timetable_not_complete")
        self.search()
        downgrade = self.service.execute(NAMES[1], {**args, "recommendation_kind": "courses"}, sources=sources(), turn_identity=self.turn,
            policy_context=self.policy("重新生成课程组合", first["plan_ref"]))
        self.assertEqual(downgrade["result"]["reason"], "timetable_not_complete")

    def test_failed_timetable_upgrade_preserves_original_courses_plan_revision(self):
        first = self.generate_with_policy("推荐五学分")["result"]
        fresh = sources(timetable=True, day=1)
        fresh["timetable"]["records"][0]["meetings"][0]["periods"] = [1, 2]
        fresh["timetable"]["records"].append(deepcopy(fresh["timetable"]["records"][0]))
        fresh["timetable"]["records"][1]["meetings"][0]["weekday"] = 2
        self.search()
        result = self.service.execute(NAMES[1], {"recommendation_kind": "timetable", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "none"}, sources=fresh,
            turn_identity=self.turn, policy_context=self.policy("把这些排成课表", first["plan_ref"]))
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["result"]["revision"], 0)
        self.assertEqual(self.service.current_plan_hint()["revision"], 0)
        self.assertEqual(self.service.required_sources(NAMES[2], {"plan_ref": first["plan_ref"], "action": "compare", "expected_revision": 0,
            "option_labels": ["A"]}), "courses")

    def test_natural_negative_course_exclusion_is_executed_without_ignoring_other_hard_condition(self):
        first = self.generate_with_policy("推荐五学分")["result"]
        ref = next(row["class_ref"] for option in first["options"] for row in option["classes"] if row["course_code"] == "SYN-C")
        args = {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0, "exclude_class_refs": [ref]}
        result = self.service.execute(NAMES[2], args, sources=sources(), policy_context=self.policy(
            "不要选Synthetic SYN-C", first["plan_ref"], hard=["不要选Synthetic SYN-C"]))["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 1)
        self.assertTrue(all("SYN-C" not in [row["course_code"] for row in option["classes"]] for option in result["options"]))
        blocked = self.service.execute(NAMES[2], {**args, "expected_revision": 1}, sources=sources(), policy_context=self.policy(
            "不要选Synthetic SYN-C且周三下午必须空", first["plan_ref"], hard=["不要选Synthetic SYN-C且周三下午必须空"]))
        self.assertEqual(blocked["result"]["reason"], "hard_preferences_unresolved")
        self.assertEqual(blocked["result"]["options"], [])

    def test_server_pending_intent_resolves_periods_without_a_prior_plan_but_never_authorizes_unlock(self):
        self.search()
        context = {**self.policy("就是第6到8节", hard=["周三第6到8节不能排课"]),
                   "pending_messages": ["推荐五学分，周三下午必须空"]}
        result = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "none"}, sources=sources(),
            turn_identity=self.turn, policy_context=context)["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["policy_status"]["status"], "ready")
        class_ref = result["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [class_ref]}, sources=sources(), policy_context=self.policy("必须保留A第一门", result["plan_ref"]))
        denied = self.service.execute(NAMES[2], {"plan_ref": result["plan_ref"], "action": "revise", "expected_revision": 1,
            "unlock_class_refs": [class_ref]}, sources=sources(), policy_context={**self.policy("重新算一次", result["plan_ref"]),
                "pending_messages": ["解除刚才那个锁"]})
        self.assertEqual(denied["result"]["reason"], "change_evidence_required")
        self.assertEqual(denied["result"]["revision"], 1)

    def test_real_failure_protocol_shape_generates_after_pending_busy_synonym_resolution(self):
        first = self.generate_with_policy("推荐五学分，尽量周五空闲")["result"]
        self.search()
        result = self.service.execute(NAMES[1], {"recommendation_kind": "courses", "target_term": CURRENT_TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "soft"}, sources=sources(),
            turn_identity=self.turn, policy_context={**self.policy("就是第6到8节，请按这个禁排时间继续。", first["plan_ref"],
                hard=["周三第6-8节禁排"]), "pending_messages": ["加上周三下午必须空"]})
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["result"]["policy_status"]["unresolved"], [])
        self.assertEqual(result["result"]["user_busy_arrangement_count"], 1)
        self.assertTrue(result["result"]["options"])
        self.assertEqual(result["result"]["recommendation_kind"], "courses")
        inspected = self.service.execute(NAMES[3], {}, policy_context=self.policy("查看选择", first["plan_ref"]))
        self.assertEqual(inspected["result"]["recommendation_kind"], "courses")
        self.assertIsNone(self.search()["result"]["recommendation_kind"])
        for name in NAMES[1:3]:
            description = next(row["function"]["description"] for row in course_plan_tool_schemas() if row["function"]["name"] == name)
            self.assertIn("任意星期", description)
            self.assertIn("明确节次禁排", description)

    def test_pending_period_range_with_endpoint_units_recomputes_while_preserving_actual_lock(self):
        first = self.generate_with_policy("推荐五学分，尽量周五空闲")["result"]
        class_ref = next(row["class_ref"] for option in first["options"] for row in option["classes"] if row["course_code"] == "SYN-A")
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [class_ref]}, sources=sources(), policy_context=self.policy("锁定Synthetic SYN-A", first["plan_ref"]))
        self.search()
        args = {"recommendation_kind": "courses", "target_term": CURRENT_TERM, "course_codes": ["SYN-A", "SYN-B", "SYN-C"],
                "target_credits": 5, "friday_free": "soft"}
        context = {**self.policy("就是第6到8节，继续刚才的调整。", first["plan_ref"],
            hard=["周三第6节到第8节不能排课"]), "pending_messages": ["加上周三下午必须空"]}
        result = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn, policy_context=context)["result"]
        self.assertEqual(result["plan_ref"], first["plan_ref"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["locked_class_refs"], [class_ref])
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["policy_status"]["unresolved"], [])
        self.assertTrue(result["options"])
        self.assertTrue(all(any(row["class_ref"] == class_ref for row in option["classes"]) for option in result["options"]))
        changed = self.service.execute(NAMES[1], args, sources=sources(), turn_identity=self.turn,
            policy_context={**context, "hard_preferences": ["周三第6节到第9节不能排课"]})
        self.assertEqual(changed["result"]["reason"], "hard_preferences_unresolved")
        self.assertEqual(self.service.current_plan_hint()["revision"], 2)
        hint = self.service.execute(NAMES[3], {}, policy_context=self.policy("查看选择", first["plan_ref"]))["result"]["selection_hint"]
        self.assertEqual(hint["locked_classes"][0]["class_ref"], class_ref)

    def test_real_process_obligation_shape_computes_and_preserves_existing_lock(self):
        first = self.generate_with_policy("推荐五学分，必须实际计算，并说明未确认项。")["result"]
        self.assertTrue(first["options"])
        ref = first["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))
        result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1},
            sources=sources(), policy_context=self.policy("必须实际重算并说明来源和未知项", first["plan_ref"]))["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["locked_class_refs"], [ref])
        blocked = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 2},
            sources=sources(), policy_context=self.policy("必须实际重算且必须同校区", first["plan_ref"]))["result"]
        self.assertEqual(blocked["reason"], "hard_preferences_unresolved")
        self.assertEqual(blocked["revision"], 2)
        self.assertEqual(blocked["locked_class_refs"], [ref])

    def test_parallel_unsupported_hard_cannot_be_hidden_by_supported_preference_or_clear_a_lock(self):
        first = self.generate_with_policy("推荐五学分，尽量周五空闲")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))
        result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1},
            sources=sources(), policy_context=self.policy("周五必须空且同校区", first["plan_ref"]))["result"]
        self.assertEqual(result["reason"], "hard_preferences_unresolved")
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["locked_class_refs"], [ref])
        self.assertEqual(result["options"], [])
        self.assertTrue(any(r["text"] == "同校区" for r in result["policy_status"]["unresolved"]))

    def test_pending_period_supplement_keeps_known_lock_and_friday_from_shared_preserve_control(self):
        first = self.generate_with_policy("推荐五学分，周五必须空")["result"]
        ref = first["options"][0]["classes"][0]["class_ref"]
        self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0,
            "lock_class_refs": [ref]}, sources=sources(), policy_context=self.policy("锁定A第一门", first["plan_ref"]))
        request = "再加一个硬条件：周三下午不能排课，之前的锁定和周五必须空都保留。"
        waiting = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1},
            sources=sources(), policy_context=self.policy(request, first["plan_ref"]))["result"]
        self.assertEqual(waiting["reason"], "hard_preferences_unresolved")
        self.assertEqual(waiting["locked_class_refs"], [ref])
        self.assertEqual({row["code"] for row in waiting["policy_status"]["unresolved"]}, {"clock_period_mapping_unverified"})
        result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 1},
            sources=sources(), policy_context={**self.policy("就是第6到8节，继续刚才的调整。", first["plan_ref"],
                hard=["周三第6节到第8节不能排课", "之前的锁定"]), "pending_messages": [request]})["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["locked_class_refs"], [ref])
        self.assertEqual(result["constraints"]["friday_free"], "hard")
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["policy_status"]["unresolved"], [])
        self.assertTrue(all(any(row["class_ref"] == ref for row in option["classes"]) for option in result["options"]))
        unknown = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 2},
            sources=sources(), policy_context=self.policy("已有锁定和必须同校区都保留", first["plan_ref"]))["result"]
        self.assertEqual(unknown["reason"], "hard_preferences_unresolved")
        self.assertEqual(unknown["revision"], 2)
        self.assertEqual(unknown["locked_class_refs"], [ref])

    def test_pending_hard_slots_can_recompute_without_repeating_an_unchanged_preference(self):
        first = self.generate_with_policy("推荐五学分，尽量周五空闲")["result"]
        result = self.service.execute(NAMES[2], {"plan_ref": first["plan_ref"], "action": "revise", "expected_revision": 0},
            sources=sources(), policy_context={**self.policy("就是第6到8节，请继续。", first["plan_ref"],
                hard=["周三第6-8节禁排"]), "pending_messages": ["周三下午必须空"]})["result"]
        self.assertTrue(result["options"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["policy_status"]["unresolved"], [])
        self.assertEqual(result["user_busy_arrangement_count"], 1)
        self.assertEqual(result["constraints"]["friday_free"], "soft")

    def test_evicted_owned_pointer_cannot_generate_fresh_constraints_without_its_protections(self):
        first = self.generate_with_policy("推荐五学分，周五必须空")["result"]
        for _ in range(8):
            self.generate_with_policy("推荐五学分")
        self.assertNotIn(first["plan_ref"], self.service._plans)
        result = self.generate_with_policy("继续安排", first["plan_ref"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["result"]["reason"], "stale_plan_reference")
        self.assertEqual(result["result"]["options"], [])


class SchoolCoursePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        data = root / "data"
        data.mkdir()
        self.delivery = write_delivery(data, fixture())
        self.store = Store(root / "app.sqlite3")
        self.auth = AuthManager(self.store, mock_enabled=True)
        self.client = FakeSchoolClient()
        self.service = SchoolAccountService(self.auth, lambda: self.client, root=root / "private" / "school",
            course_data_test_delivery_index=str(self.delivery), course_data_test_term=CURRENT_TERM)
        self.addCleanup(self.service.close)
        token, _ = self.service.login({"username": "SYNTHETIC-LOGIN", "password": "SYNTHETIC-PASSWORD"})
        self.context = self.auth.authenticate(token)
        self.receipts = []
        self.active_ref = None
        self.policy_message = "推荐合成课程5学分，尽量周五空闲。"
        self.hard_preferences = []

    def live(self, name, args, receipts=None):
        result = self.service.personal_chat_live_tool(self.context, name, args, receipts=self.receipts if receipts is None else receipts,
            emit=lambda *_: None, authorize=lambda: self.auth.require_context(self.context), adopt=lambda _: None,
            policy_context={"user_message": self.policy_message, "hard_preferences": self.hard_preferences, "active_plan_ref": self.active_ref})
        if name in NAMES[1:3] and result.get("result", {}).get("plan_ref"):
            self.active_ref = result["result"]["plan_ref"]
        return result

    def prepare_major(self):
        return self.live("query_my_records", {"operation": "major_academic", "mode": "summary"})

    def curriculum(self):
        self.receipts.append({"operation": "curriculum", "target_term": None, "status": "acquired_pending_review", "ready": True,
            "data_source": "live", "source_alias": "PLAN-" + "a" * 24, "sha256": "b" * 64})
        return patch.object(self.service, "local_chat_status", return_value={"local": {
            "plan": {"status": "acquired_pending_review", "source_alias": "PLAN-" + "a" * 24, "sha256": "b" * 64},
            "evidence": {"ready": True}}})

    def args(self, kind="courses"):
        return {"recommendation_kind": kind, "target_term": CURRENT_TERM, "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "soft"}

    def test_explicit_verified_config_required_and_search_never_school_read(self):
        result = self.live("get_my_context", {})
        self.assertTrue(result["capabilities"]["course_data_test"])
        self.assertEqual(result["course_data_test"]["status"], "ready")
        before = len(self.client.request_calls)
        found = self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.assertEqual(len(found["result"]["courses"]), 3)
        self.assertEqual(len(self.client.request_calls), before)
        self.service.course_data_test_delivery_index = None
        self.assertEqual(self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})["result"]["reason"], "course_data_test_not_configured")
        self.assertFalse(self.live("get_my_context", {})["capabilities"]["course_data_test"])

    def test_generate_requires_actual_current_major_and_curriculum_without_extra_school_reads(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.assertEqual(self.live(NAMES[1], self.args())["result"]["reason"], "source_preparation_required")
        self.prepare_major()
        before = len(self.client.request_calls)
        with self.curriculum():
            generated = self.live(NAMES[1], self.args())
        self.assertEqual(generated["status"], "available")
        self.assertEqual(len(self.client.request_calls), before)
        self.assertTrue(generated["result"]["options"])
        self.assertFalse(self.live("get_my_context", {})["capabilities"]["real_recommendation"])

    def test_fake_observation_receipt_failed_read_and_other_turn_cannot_unlock_generation(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.receipts.append({"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "complete_within_scope",
            "ready": True, "data_source": "live", "observation_id": "OBS-" + "c" * 32})
        with self.curriculum():
            self.assertEqual(self.live(NAMES[1], self.args())["result"]["reason"], "source_preparation_required")
        self.receipts.clear()
        self.prepare_major()
        self.receipts.append({"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "failed", "ready": False, "data_source": "none"})
        with self.curriculum():
            self.assertEqual(self.live(NAMES[1], self.args())["result"]["reason"], "source_preparation_required")
        with self.curriculum():
            self.assertEqual(self.live(NAMES[1], self.args(), receipts=list(self.receipts))["result"]["reason"], "course_search_required")

    def test_timetable_requires_fresh_complete_own_observation_and_no_exam_requests(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.prepare_major()
        self.client.requests.append({"state": "business", "http_status": 200, "content_type": "application/json", "payload": {"kbList": [{
            "kcmc": "Synthetic protected course", "kcdm": "SYN-PROTECTED", "meetings": [{"xqj": 6, "jcs": [7, 8]}]}], "total": 1}})
        self.live("query_my_records", {"operation": "timetable", "target_term": CURRENT_TERM})
        before = len(self.client.request_calls)
        with self.curriculum():
            result = self.live(NAMES[1], self.args("timetable"))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["result"]["fixed_arrangement_count"], 1)
        self.assertEqual(len(self.client.request_calls), before)
        self.assertFalse(any(r["operation"] == "exams" for r in self.client.request_calls))

    def test_relogin_epoch_and_rejected_owner_clear_or_block_refs(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.prepare_major()
        with self.curriculum():
            result = self.live(NAMES[1], self.args())["result"]
        self.assertEqual(self.live(NAMES[3], {})["result"]["plan_ref"], result["plan_ref"])
        self.client.session_epoch += 1
        self.assertEqual(self.live(NAMES[3], {})["result"]["reason"], "stale_plan_reference")
        self.service.logout(self.context)
        with self.assertRaises(AccessError): self.live(NAMES[3], {"plan_ref": result["plan_ref"]})

    def test_mid_execution_authorization_failure_is_not_rewritten_as_argument_error(self):
        with patch("course_assistant.personal_course_plans.PersonalCoursePlanService.execute",
                   side_effect=AccessError("Synthetic denied", 403, "permission_denied")):
            with self.assertRaises(AccessError) as error:
                self.live(NAMES[3], {})
        self.assertEqual(error.exception.reason, "permission_denied")

    def test_names_only_catalog_configuration_cannot_enable_course_data_tests(self):
        self.service.course_data_test_delivery_index = None
        with patch.object(self.service.course_catalog, "describe", return_value={"status": "ready", "data_kind": "local_course_reference"}):
            context = self.live("get_my_context", {})
        self.assertTrue(context["capabilities"]["course_catalog_search"])
        self.assertFalse(context["capabilities"]["course_data_test"])

    def test_new_chat_context_does_not_default_to_another_chats_latest_plan(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.prepare_major()
        with self.curriculum():
            generated = self.live(NAMES[1], self.args())["result"]
        self.assertEqual(self.live("get_my_context", {})["current_course_plan"]["plan_ref"], generated["plan_ref"])
        self.active_ref = None
        self.assertIsNone(self.live("get_my_context", {})["current_course_plan"])
        self.assertIsNone(self.live(NAMES[3], {"plan_ref": generated["plan_ref"]})["result"]["plan_ref"])

    def test_exam_acquisition_receipts_are_reported_without_fetching_or_promoting_validation(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.prepare_major()
        for status, ready, expected in (("complete_within_scope", True, "available"), ("partial_acquisition", True, "partial"), ("failed", False, "failed")):
            self.receipts[:] = [r for r in self.receipts if r.get("operation") not in {"exams", "curriculum"}]
            self.receipts.append({"operation": "exams", "target_term": CURRENT_TERM, "status": status,
                "ready": ready, "data_source": "live" if ready else "none", "observation_id": "OBS-" + "d" * 32})
            before = len(self.client.request_calls)
            with self.curriculum():
                result = self.live(NAMES[1], self.args())["result"]
            self.assertEqual(result["source_preparation"]["exams"], expected)
            self.assertEqual(len(self.client.request_calls), before)
            self.assertEqual(result["validation_scope"], "observed_weekday_periods_conservative_overlap_policy")
            self.assertTrue(all(o["full_semantics"] == "unverified" for o in result["options"]))

    def test_other_authenticated_owner_cannot_resolve_or_update_private_plan_refs(self):
        self.live(NAMES[0], {"query": "Synthetic", "target_term": CURRENT_TERM})
        self.prepare_major()
        with self.curriculum():
            result = self.live(NAMES[1], self.args())["result"]
        self.client = FakeSchoolClient(profile=PROFILE_B)
        token, _ = self.service.login({"username": "SYNTHETIC-OTHER-LOGIN", "password": "SYNTHETIC-OTHER-PASSWORD"})
        self.context = self.auth.authenticate(token)
        private = self.live(NAMES[3], {"plan_ref": result["plan_ref"]})
        self.assertEqual(private["result"]["reason"], "stale_plan_reference")
        self.assertIsNone(private["result"]["plan_ref"])
        self.assertEqual(self.live("get_my_context", {})["current_course_plan"], None)


if __name__ == "__main__":
    unittest.main()
