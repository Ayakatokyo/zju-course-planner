"""Scripted protocol over synthetic sources and the actual local test solver."""
from copy import deepcopy
import json
import tempfile
import unittest

from course_assistant.auth import AccessError
from course_assistant.personal_dialogue import PersonalDialogueService, PREPARE_TOOL
from course_assistant.personal_course_plans import PersonalCoursePlanService, _base
from course_assistant.recommendation_readiness import assess_recommendation_readiness
from tests.test_current_term_recommendation import fixture, write_delivery
from tests.test_personal_dialogue import ScriptedModel, call, final


TERM = "2026-2027-AW"
TASK = {"recommendation_kind": "courses", "target_term": TERM,
        "course_scope": {"kind": "topic", "queries": ["Synthetic"]},
        "hard_preferences": [], "soft_preferences": ["尽量周五空闲"]}


class RecommendationDialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.denied, self.turn, self.executed = False, [], []
        self.plan = PersonalCoursePlanService(write_delivery(self.temp.name, fixture()), TERM, self.authorize)
        self.context = {"status": "available", "operations": [], "source_receipts": [],
            "api_operations": ["major_academic", "timetable", "exams"],
            "capabilities": {"live_record_query": True, "read_only_acquisition": True,
                "curriculum_local_search": True, "recommendation_input_check": True, "course_data_test": True},
            "course_data_test": self.plan.describe()}
        self.sources = {key: {"status": "available", "verified": True} for key in ("major_academic", "curriculum")}

    def authorize(self):
        if self.denied:
            return {"scope_key": "changed", "versions": {"records": "changed"}}
        return {"scope_key": "SYNTHETIC-ONLY", "versions": {"records": "SYN-v1"}}

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        if name == "prepare_recommendation_inputs":
            self.context["source_receipts"] = [
                {"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "complete_within_scope", "ready": True, "data_source": "live"},
                {"operation": "curriculum", "status": "acquired_pending_review", "ready": True, "data_source": "live"}]
        if name in {"prepare_recommendation_inputs", "check_recommendation_inputs"}:
            check = {key: args[key] for key in ("recommendation_kind", "target_term") if key in args}
            return {"status": "available", "data_kind": "personal_source", "check_executed": True,
                    "readiness": assess_recommendation_readiness(self.context, **check)}
        return self.plan.execute(name, args, sources=self.sources, turn_identity=self.turn)

    def run_dialogue(self, responses, *, clarification_context=None):
        model = ScriptedModel(responses)
        response, messages = PersonalDialogueService(model, self.authorize, self.execute).run(
            "用本学期已有课程数据测试课程推荐", data_use="personal_retrieval",
            clarification_context=clarification_context)
        return response, messages, model

    @staticmethod
    def typed_last(messages, tools):
        evidence = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["evidence_id"]
        return final({'kind': 'answer', 'text': '本轮返回了课程条件测试资料，请按实际方案核对。', 'evidence_ids': ([evidence])})

    def test_complete_local_workflow_uses_prepared_sources_actual_search_and_solver(self):
        response, messages, model = self.run_dialogue([
            call("prepare_recommendation_inputs", TASK),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search"),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5, "friday_free": "soft"}, "generate"),
            self.typed_last])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["task_progress"], {"kind": "recommendation", "status": "limited_result",
            "scope": "course_data_test", "reason": "test_result"})
        options = response["recommendation_result"]["options"]
        self.assertTrue(options)
        self.assertTrue(all(option["observed_slot_result"] == "pass" for option in options))
        self.assertTrue(all(option["full_semantics"] == "unverified" for option in options))
        self.assertIn("条件测试", response["reply"])
        self.assertNotIn("observation_id", json.dumps(messages))
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs",
            "search_current_term_courses", "generate_current_term_plan"])
        tools = {row["function"]["name"] for row in model.calls[0][1]}
        self.assertTrue({"prepare_recommendation_inputs", "get_current_term_plan", "update_current_term_plan"} <= tools)

    def test_explain_saved_credit_tradeoff_answers_why_without_new_sources_or_solver(self):
        data = fixture()
        data["courses"][0]["definitions"][0]["fields"]["credits"]["value"] = "4"
        write_delivery(self.temp.name, data)
        self.plan.execute("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, turn_identity=self.turn)
        generated = self.plan.execute("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
            "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 6, "friday_free": "soft"},
            sources=self.sources, turn_identity=self.turn)
        self.assertEqual([row["total_credits"] for row in generated["result"]["options"][:2]], ["7", "6"])
        saved = deepcopy(self.plan._plans)

        def explain(messages, tools):
            evidence = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["evidence_id"]
            return final({'kind': 'answer', 'text': '不少于6学分是下限；A比下限多1学分。本轮没有重新选课或修改条件。', 'evidence_ids': ([evidence])})

        model = ScriptedModel([call("get_current_term_plan", {}), explain])
        response, _ = PersonalDialogueService(model, self.authorize, self.execute).run(
            "为什么目标6学分给了7学分？只解释已有计算，不改变条件或另找课程。", data_use="personal_retrieval")
        self.assertEqual(response["status"], "completed", response)
        self.assertIn("不少于6学分", response["reply"])
        self.assertIn("比下限多1学分", response["reply"])
        self.assertNotIn("见依据", response["reply"])
        self.assertIn("没有重新选课或修改条件", response["reply"])
        self.assertNotIn("软空闲时段占用", response["reply"])
        self.assertLess(len(response["reply"]), 240)
        self.assertEqual(response["repair_attempts"], 0)
        self.assertNotIn("已经完成课表推荐", response["reply"])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "get_current_term_plan"])
        self.assertEqual(self.plan._plans, saved)

    def timetable_prepare_receipt(self, state):
        original = self.execute
        def execute(name, args):
            result = original(name, args)
            if name == "prepare_recommendation_inputs":
                self.context["source_receipts"].append({"operation": "timetable", "target_term": TERM,
                    "status": {"partial": "partial_acquisition", "failed": "failed", "available": "complete_within_scope"}[state],
                    "ready": state != "failed", "data_source": "live"})
                result["readiness"] = assess_recommendation_readiness(self.context,
                    recommendation_kind=args["recommendation_kind"], target_term=args["target_term"])
            return result
        self.execute = execute

    @staticmethod
    def prepared_source_answer(messages, tools):
        assert {row["function"]["name"] for row in tools} <= {"query_my_records", "finish_answer"}
        value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
        return final({'kind': 'answer', 'text': '正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', 'evidence_ids': ([value['evidence_id']])})

    def test_actual_partial_timetable_prepare_ends_with_evidence_without_candidate_loop(self):
        self.timetable_prepare_receipt("partial")
        result, messages, model = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "recommendation_kind": "timetable"}), self.prepared_source_answer])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["task_progress"]["status"], "blocked")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs"])
        self.assertNotIn("本轮声明工具不能补齐", json.dumps(messages, ensure_ascii=False))

    def test_failed_timetable_prepare_returns_current_failure_not_old_facts(self):
        self.timetable_prepare_receipt("failed")
        result, _, _ = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "recommendation_kind": "timetable"}), self.prepared_source_answer])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["task_progress"]["status"], "blocked")
        source = next(row for row in result["citations"][0]["readiness"]["sources"] if row["id"] == "existing_timetable")
        self.assertEqual(source["status"], "failed")
        self.assertNotIn("recommendation_result", result)

    def test_prepared_source_final_mode_cannot_dispatch_a_new_search_and_shares_one_repair(self):
        self.timetable_prepare_receipt("partial")
        result, _, model = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "recommendation_kind": "timetable"}),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "ignored-search"),
            self.prepared_source_answer])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs"])
        self.assertEqual([row["function"]["name"] for row in model.calls[1][1]], ["query_my_records", "finish_answer"])
        self.assertEqual([tool["function"]["name"] for tool in model.calls[2][1]], ["finish_answer"])

    def test_available_timetable_keeps_tools_available(self):
        self.timetable_prepare_receipt("available")
        def search(messages, tools):
            self.assertTrue(tools)
            return call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search")
        result, _, _ = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "recommendation_kind": "timetable"}), search, self.typed_last])
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("search_current_term_courses", [name for name, _ in self.executed])

    def test_actual_unparseable_timetable_result_keeps_latest_t_evidence_in_final_mode(self):
        self.timetable_prepare_receipt("available")
        self.sources["timetable"] = {"status": "available", "verified": True,
            "records": [{"field_states": {"meetings": "unknown"}}]}
        def final_result(messages, tools):
            self.assertEqual([row["function"]["name"] for row in tools], ["query_my_records", "finish_answer"])
            return self.typed_last(messages, tools)
        result, _, _ = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "recommendation_kind": "timetable"}),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search"),
            call("generate_current_term_plan", {"recommendation_kind": "timetable", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B"], "target_credits": 5, "friday_free": "soft"}, "generate"), final_result])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["task_progress"]["status"], "blocked")
        self.assertEqual(result["recommendation_result"]["reason"], "timetable_unparseable")
        self.assertEqual(result["recommendation_result"]["options"], [])

    def test_source_stop_requires_current_executed_prepare_and_matching_proof(self):
        from course_assistant.personal_dialogue import _timetable_source_stop
        proof = {"kind": "recommendation_readiness", "readiness": {"recommendation_kind": "timetable",
            "target_term": TERM, "sources": [{"id": "existing_timetable", "status": "partial"}]}}
        row = {"name": "prepare_recommendation_inputs", "arguments": {"recommendation_kind": "timetable", "target_term": TERM},
            "result": {"check_executed": True, "evidence_id": "P-current"}}
        self.assertIsNotNone(_timetable_source_stop([row], {"P-current": proof}))
        for changed in ({**row, "error": True}, {**row, "name": "get_my_context"},
                {**row, "arguments": {"recommendation_kind": "courses", "target_term": TERM}},
                {**row, "result": {"check_executed": False, "evidence_id": "P-current"}},
                {**row, "arguments": {"recommendation_kind": "timetable", "target_term": "2025-2026-AW"}}):
            self.assertIsNone(_timetable_source_stop([changed], {"P-current": proof}))
        self.assertIsNone(_timetable_source_stop([row], {}))
        for state in ("missing", "not_requested", "needs_clarification", "available"):
            changed = deepcopy(proof)
            changed["readiness"]["sources"][0]["status"] = state
            self.assertIsNone(_timetable_source_stop([row], {"P-current": changed}))
        available = deepcopy(proof)
        available["readiness"]["sources"][0]["status"] = "available"
        blocked = {"name": "generate_current_term_plan", "arguments": {}, "result": {"evidence_id": "T-current"}}
        evidence = {"P-current": available, "T-current": {"kind": "course_data_plan", "course_data_plan": {
            "target_term": TERM, "reason": "timetable_not_complete", "source_preparation": {"timetable": "partial"}}}}
        self.assertIsNone(_timetable_source_stop([blocked, row], evidence))
        self.assertIsNotNone(_timetable_source_stop([row, blocked], evidence))

    def test_check_only_does_not_imply_preparation_or_recommendation_completion(self):
        def typed_check(messages, tools):
            evidence = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["evidence_id"]
            return final({'kind': 'answer', 'text': '正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', 'evidence_ids': ([evidence])})
        response, _, _ = self.run_dialogue([call("check_recommendation_inputs", {"recommendation_kind": "courses", "target_term": TERM}), typed_check])
        self.assertEqual(response["status"], "completed")
        self.assertNotIn("task_progress", response)
        self.assertNotIn("recommendation_result", response)
        self.assertEqual(len(self.executed), 2)

    def test_preparation_and_actual_condition_test_can_share_answer_without_denying_generation(self):
        def typed_plan_and_preparation(messages, tools):
            returned = [json.loads(row["content"]) for row in messages if row["role"] == "tool"]
            prepared = next(row for row in returned if "readiness" in row)
            return final({'kind': 'answer', 'text': '\n\n'.join(['本轮返回了课程条件测试资料，请按实际方案核对。', '正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。']), 'evidence_ids': ([returned[-1]['evidence_id']]) + ([prepared['evidence_id']])})

        response, _, _ = self.run_dialogue([
            call("prepare_recommendation_inputs", TASK),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search"),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B", "SYN-C"], "target_credits": 5,
                "friday_free": "soft"}, "generate"),
            typed_plan_and_preparation])

        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 0)
        self.assertEqual(response["task_progress"]["status"], "limited_result")
        self.assertTrue(response["recommendation_result"]["options"])
        self.assertEqual([proof["kind"] for proof in response["citations"]],
            ["course_data_plan", "recommendation_readiness"])
        readiness = response["citations"][1]["readiness"]
        self.assertFalse(readiness["can_generate"])
        self.assertFalse(readiness["can_save_or_export"])
        self.assertIn("条件测试", response["reply"])
        self.assertIn("正式推荐尚未就绪", response["reply"])
        self.assertNotIn("，方案生成、保存和导出均未开放", response["reply"])

    def test_prepare_not_declared_without_dynamic_source_capabilities(self):
        self.context["capabilities"].pop("live_record_query")
        response, _, model = self.run_dialogue([call("prepare_recommendation_inputs", TASK),
            call("prepare_recommendation_inputs", TASK)])
        self.assertEqual(response["code"], "tool_not_allowed")
        self.assertNotIn("prepare_recommendation_inputs", {row["function"]["name"] for row in model.calls[0][1]})
        self.assertEqual(len(self.executed), 1)

    def test_prepare_basis_schema_is_scoped_to_actual_pending_context_without_mutating_global(self):
        pending = {"source": "previous_turn_clarification", "request": "合成课程推荐，周三下午必须空",
            "supplements": [], "question": "请补充节次", "previous_task": None,
            "authorization_granted": False, "facts_reusable": False, "fresh_sources_required": True}
        for context, expected in ((None, ["current_message"]),
                (pending, ["current_message", "pending_task"]), (None, ["current_message"])):
            _, _, model = self.run_dialogue([final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})],
                clarification_context=context)
            declared = next(t for t in model.calls[0][1] if t["function"]["name"] == "prepare_recommendation_inputs")
            self.assertEqual(declared["function"]["parameters"]["properties"]["request_basis"]["enum"], expected)
        self.assertEqual(PREPARE_TOOL["function"]["parameters"]["properties"]["request_basis"]["enum"],
            ["current_message", "pending_task"])

    def test_missing_pending_basis_returns_actionable_diagnostic_before_source_dispatch(self):
        def correct_basis(messages, tools):
            diagnostic = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertEqual(diagnostic.pop("execution_budget")["remaining_tool_calls"], 8)
            self.assertIn("待补请求", diagnostic.pop("next_step"))
            self.assertEqual(diagnostic, {"status": "not_ready", "code": "clarification_context_missing",
                "unknown": ["no_evidence_returned"], "query_executed": False,
                "allowed_request_basis": ["current_message"], "pending_task_available": False})
            self.assertEqual([name for name, _ in self.executed], ["get_my_context"])
            return call("prepare_recommendation_inputs", {**TASK, "request_basis": "current_message"}, "corrected")

        def typed_preparation(messages, tools):
            proof = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["evidence_id"]
            return final({'kind': 'answer', 'text': '正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', 'evidence_ids': ([proof])})

        response, _, _ = self.run_dialogue([
            call("prepare_recommendation_inputs", {**TASK, "request_basis": "pending_task"}),
            correct_basis, typed_preparation])
        self.assertEqual(response["status"], "completed", response)
        rejected = response["tool_results"][1]
        self.assertTrue(rejected["error"])
        self.assertEqual(rejected["result"]["code"], "clarification_context_missing")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs"])
        self.assertEqual(self.executed[-1][1]["request_basis"], "current_message")
        self.assertEqual(response["task_progress"]["status"], "prepared")

    def test_callback_context_rejection_retains_only_controlled_recovery_fields(self):
        original = self.execute
        def rejected(name, args):
            if name == "prepare_recommendation_inputs":
                raise AccessError("NEVER-SEND-EXCEPTION-DETAIL", 409, "clarification_context_missing")
            return original(name, args)
        self.execute = rejected

        def inspect_diagnostic(messages, tools):
            diagnostic = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertEqual(diagnostic["code"], "clarification_context_missing")
            self.assertEqual(diagnostic["allowed_request_basis"], ["current_message"])
            self.assertFalse(diagnostic["query_executed"])
            self.assertNotIn("NEVER-SEND", json.dumps(messages))
            return final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})

        response, _, _ = self.run_dialogue([call("prepare_recommendation_inputs", TASK), inspect_diagnostic])
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["tool_results"][1]["result"]["code"], "clarification_context_missing")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])

    def test_unsafe_prepare_argument_is_rejected_before_acquisition(self):
        response, _, _ = self.run_dialogue([call("prepare_recommendation_inputs", {**TASK, "owner_student_id": "OTHER"})])
        self.assertEqual(response["code"], "invalid_tool_arguments")
        self.assertEqual(len(self.executed), 1)

    def test_answer_cannot_change_blocked_computation_to_feasible(self):
        r = _base("generate")
        r["reason"] = "source_preparation_required"
        def execute(name, args):
            if name == "get_my_context":
                return deepcopy(self.context)
            return {"status": "not_ready", "data_kind": "course_data_test", "result": r}
        def unsupported(messages, tools):
            ref = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["evidence_id"]
            return final({'kind': 'answer', 'text': "这是一个任意编写的结论。", 'evidence_ids': [ref]})
        model = ScriptedModel([call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
            "course_codes": ["SYN-A"], "target_credits": 3, "friday_free": "none"}), unsupported,
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        response, _ = PersonalDialogueService(model, self.authorize, execute).run("合成测试", data_use="personal_retrieval")
        self.assertEqual(response["repair_attempts"], 0)
        self.assertEqual(response["recommendation_result"]["reason"], "source_preparation_required")
        self.assertEqual(response["recommendation_result"]["options"], [])

    def test_answer_cannot_hide_actual_plan_result_behind_context_citation(self):
        def context_only(messages, tools):
            marker = "服务器已完成本轮本机范围检查（service_preflight get_my_context）：\n"
            context = json.loads(messages[0]["content"].split(marker)[1])
            return final({'kind': 'answer', 'text': "当前已有条件测试方案。", 'evidence_ids': [context["evidence_id"]]})
        response, _, _ = self.run_dialogue([
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B"], "target_credits": 3, "friday_free": "none"}, "generate"),
            context_only, self.typed_last])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 1)
        self.assertEqual(response["validation_diagnostics"][0]["failure_detail"], "latest_course_plan_evidence_required")
        self.assertTrue(response["recommendation_result"]["options"])
        self.assertEqual(response["task_progress"]["status"], "limited_result")

    def test_completed_plan_can_survive_undeclared_tool_rejection_as_typed_evidence(self):
        response, messages, model = self.run_dialogue([
            call("prepare_recommendation_inputs", TASK),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search"),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B"], "target_credits": 3, "friday_free": "none"}, "generate"),
            call("undeclared_formatter", {"query": "DISCARD-UNDECLARED-PAYLOAD"}, "unknown"), self.typed_last])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 1)
        self.assertTrue(response["recommendation_result"]["options"])
        self.assertEqual(response["task_progress"]["status"], "limited_result")
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs",
            "search_current_term_courses", "generate_current_term_plan"])
        self.assertNotIn("undeclared_formatter", json.dumps(messages))
        self.assertNotIn("DISCARD-UNDECLARED", json.dumps(model.calls[-1][0]))

    def test_corrected_declared_plan_arguments_reach_actual_solver_once(self):
        good = {"recommendation_kind": "courses", "target_term": TERM,
            "course_codes": ["SYN-A", "SYN-B"], "target_credits": 3, "friday_free": "none"}
        bad = {k: v for k, v in good.items() if k != "course_codes"}
        def corrected(messages, tools):
            self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs",
                "search_current_term_courses"])
            self.assertIn("required_argument_missing", messages[-1]["content"])
            self.assertTrue(tools)
            return call("generate_current_term_plan", good, "corrected-generate")
        response, _, _ = self.run_dialogue([call("prepare_recommendation_inputs", TASK),
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}, "search"),
            call("generate_current_term_plan", bad, "invalid-generate"), corrected, self.typed_last])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["argument_corrections"], 1)
        self.assertEqual(response["repair_attempts"], 0)
        self.assertTrue(response["recommendation_result"]["options"])
        self.assertEqual(sum(name == "generate_current_term_plan" for name, _ in self.executed), 1)
        self.assertEqual(self.executed[-1][1], good)

    def test_branch_schema_retry_preserves_actual_lock_hard_condition_and_plan_version(self):
        held = {}
        def lock(messages, tools):
            plan = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["result"]
            ref = plan["options"][0]["classes"][0]["class_ref"]
            return call("update_current_term_plan", {"plan_ref": plan["plan_ref"], "action": "revise",
                "expected_revision": plan["revision"], "lock_class_refs": [ref]}, "lock")
        def invalid_branch(messages, tools):
            plan = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["result"]
            held.update(plan=plan, state=deepcopy(self.plan._plans))
            return call("update_current_term_plan", {"plan_ref": plan["plan_ref"], "action": "revise",
                "expected_revision": plan["revision"], "option_labels": ["A"]}, "bad-branch")
        def corrected(messages, tools):
            self.assertEqual(self.plan._plans, held["state"])
            declaration = next(t for t in tools if t["function"]["name"] == "update_current_term_plan")
            self.assertIn("revise禁止", declaration["function"]["parameters"]["properties"]["option_labels"]["description"])
            self.assertIn("argument_value_invalid", messages[-1]["content"])
            return call("update_current_term_plan", {"plan_ref": held["plan"]["plan_ref"], "action": "revise",
                "expected_revision": held["plan"]["revision"]}, "corrected-branch")
        response, _, _ = self.run_dialogue([
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B"], "target_credits": 3, "friday_free": "hard"}, "generate"),
            lock, invalid_branch, corrected, self.typed_last])
        self.assertEqual(response["status"], "completed", response)
        result = response["recommendation_result"]
        self.assertEqual(response["argument_corrections"], 1)
        self.assertEqual(result["locked_class_refs"], held["plan"]["locked_class_refs"])
        self.assertEqual(result["constraints"]["friday_free"], "hard")
        self.assertEqual(result["plan_ref"], held["plan"]["plan_ref"])
        self.assertEqual(result["data_version"], held["plan"]["data_version"])
        self.assertTrue(all(o["friday_meeting_count"] == 0 for o in result["options"]))

    def test_undeclared_tool_repair_still_requires_latest_actual_plan_evidence(self):
        def omit_plan(messages, tools):
            marker = "服务器已完成本轮本机范围检查（service_preflight get_my_context）：\n"
            context = json.loads(messages[0]["content"].split(marker)[1])
            return final({'kind': 'answer', 'text': "当前有课程参考资料。", 'evidence_ids': [context["evidence_id"]]})
        response, messages, model = self.run_dialogue([
            call("search_current_term_courses", {"query": "Synthetic", "target_term": TERM}),
            call("generate_current_term_plan", {"recommendation_kind": "courses", "target_term": TERM,
                "course_codes": ["SYN-A", "SYN-B"], "target_credits": 3, "friday_free": "none"}, "generate"),
            call("undeclared_formatter", {}, "unknown"), omit_plan])
        self.assertEqual(response["code"], "unverified_personal_answer")
        self.assertEqual(response["repair_attempts"], 1)
        self.assertEqual(response["validation_diagnostics"][-1]["failure_detail"], "latest_course_plan_evidence_required")
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
        self.assertNotIn("recommendation_result", response)
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
