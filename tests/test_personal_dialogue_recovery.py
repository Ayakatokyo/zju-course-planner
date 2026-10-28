"""Bounded final-answer repair and private prior-run diagnostics; no network."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_dialogue import PersonalDialogueError, PersonalDialogueService, MAX_ANSWER_REFERENCES, EVIDENCE_LIMITS, MAX_EGRESS_CHARS
from course_assistant.personal_observations import source_grade_summary
from course_assistant.recommendation_readiness import assess_recommendation_readiness
from tests.test_personal_dialogue import ScriptedModel, call, final


class PersonalDialogueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.executed, self.events, self.denied = [], [], False
        self.context = {"status": "available", "operations": [{"operation": "major_academic", "target_term": "ALL-HISTORY"}], "capabilities": {}}
        self.records = {"status": "partial_acquisition", "operation": "major_academic",
            "matched_records": 1, "records": [{"course_name_raw": "合成课程", "grade_raw": "88",
                "field_states": {"grade_raw": "known", "course_name_raw": "known"}}]}
        self.previous = {"run_id": "RUN-" + "1" * 32, "status": "failed", "error_code": "invalid_final_answer",
            "thinking": False, "model": "synthetic-model", "events": [
                {"kind": "tool.end", "fields": {"tool_name": "query_my_records", "status": "completed", "operation": "major_academic",
                    "target_term": "ALL-HISTORY", "query_mode": "overview", "initiated_by": "model", "returned_records": 15,
                    "captured_rows": 60, "reported_total": 104, "has_more": True, "complete_within_scope": False}},
                {"kind": "error", "fields": {"error_code": "invalid_final_answer", "stage": "answer"}}]}

    def authorize(self):
        if self.denied:
            raise PersonalDialogueError("grant_revoked")
        return {"scope_key": "synthetic-current-generation"}

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        return deepcopy(self.records)

    def emit(self, kind, fields):
        self.events.append((kind, deepcopy(fields)))

    def run_model(self, responses, *, execution_context=None, task_context=None, clarification_context=None, max_steps=5, max_tools=10, emit=None):
        model = ScriptedModel(responses)
        service = PersonalDialogueService(model, self.authorize, self.execute, max_steps=max_steps, max_tools=max_tools)
        result, history = service.run("合成测试请求", data_use="personal_retrieval",
            execution_context=execution_context, task_context=task_context, clarification_context=clarification_context, emit=emit or self.emit)
        return result, history, model

    @staticmethod
    def valid_record_answer(messages, tools):
        value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
        return final({'kind': 'answer', 'text': "合成课程的来源分数是88。", 'evidence_ids': [value["records"][0]["evidence_id"]]})

    def audit_answer(self, text):
        self.context["operations"] = [{"operation": "graduation_audit", "target_term": "ALL-HISTORY"}]
        self.records.update(operation="graduation_audit", target_term="ALL-HISTORY", records=[{
            "node_kind": "module", "course_name_raw": "合成实践模块", "earned_credits_raw": "6",
            "required_credits_raw": "9", "field_states": {"earned_credits_raw": "known"}}])
        def answer(messages, tools):
            value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            return final({"kind": "answer", "text": text, "evidence_ids": [value["records"][0]["evidence_id"]]})
        return answer

    def test_audit_wording_can_recover_once_without_reacquiring_or_replaying_rejected_draft(self):
        rejected = "合成实践模块。学校自审中该模块已获6/要求9学分。"
        valid = "学校自审中，合成实践模块已获6学分，要求9学分。"
        result, history, model = self.run_model([
            call("query_my_records", {"operation": "graduation_audit"}),
            self.audit_answer(rejected), self.audit_answer(valid)])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["reply"], valid)
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
        messages, tools = model.calls[-1]
        self.assertEqual([tool["function"]["name"] for tool in tools], ["finish_answer"])
        self.assertNotIn(rejected, json.dumps(messages, ensure_ascii=False))
        self.assertNotIn(rejected, json.dumps(history, ensure_ascii=False))
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "source_claim_not_supported")

    def test_audit_repair_still_rejects_wrong_values_and_stops_after_one_attempt(self):
        bad = self.audit_answer("学校自审中，合成实践模块已获9学分，要求9学分。")
        result, _, model = self.run_model([
            call("query_my_records", {"operation": "graduation_audit"}), bad, bad])
        self.assertEqual(result["code"], "unsupported_personal_claim")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(model.calls), 3)

    def test_audit_repair_cannot_execute_another_tool(self):
        bad = self.audit_answer("合成实践模块。学校自审已获6/9学分。")
        result, _, _ = self.run_model([
            call("query_my_records", {"operation": "graduation_audit"}), bad,
            call("query_my_records", {"operation": "graduation_audit"}, identifier="extra-query")])
        self.assertEqual(result["status"], "failed")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])

    def test_curriculum_only_repair_does_not_reuse_historical_personal_credits(self):
        self.records = {"status": "pending_review", "coverage": {"physical_pages": 1},
            "matches": [{"reference": "PERSONAL-REF-" + "A" * 43, "source_alias": "SYNTHETIC-PLAN",
                "title": "合成实践模块", "text": "合成实践模块要求9学分。", "page": 1,
                "review_status": "unreviewed", "locator": {"physical_page": 1}}]}
        def answer(text):
            def respond(messages, tools):
                value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                return final({"kind": "answer", "text": text, "evidence_ids": [value["matches"][0]["evidence_id"]]})
            return respond
        valid = "培养原文中合成实践模块要求9学分；当前未查询自审，不能确认个人认定学分。"
        result, _, _ = self.run_model([
            call("search_my_curriculum", {"query": "合成实践模块"}),
            answer("学校自审中，合成实践模块已获6学分。"), answer(valid)])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["reply"], valid)
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "search_my_curriculum"])

    def set_summary(self, operation="academic", term="2023-2024-SS", *, complete=True):
        self.context["operations"] = [{"operation": operation, "target_term": term}]
        rows = [{"course_name_raw": "合成课程", "grade_raw": grade,
                 "completion_status_raw": None,
                 "field_states": {"grade_raw": "known" if grade is not None else "unknown",
                                  "course_name_raw": "known", "completion_status_raw": "unknown"}}
                for grade in ("80", "90", None, "通过")]
        coverage = {"captured_rows": 4, "reported_total": 4 if complete else 8, "complete_within_scope": complete}
        self.records.update(operation=operation, target_term=term, mode="summary", records=[],
            matched_records=len(rows), coverage=coverage, source_grade_summary=source_grade_summary(rows, coverage))

    @staticmethod
    def summary_answer(messages, tools):
        value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
        summary = value["source_grade_summary"]
        return final({"kind": "answer", "text": value["record_source"]["label"] + "：本次匹配4条记录，纯数字成绩2条，成绩未知1条，简单均值85.00。",
            "evidence_ids": [summary["evidence_id"]]})

    def test_summary_scope_and_numbers_render_from_executed_source_not_model_wording(self):
        for operation, term, label in (("academic", "2023-2024-SS", "2023-2024学年春夏学期成绩来源"),
                                       ("academic", "2023-2024-AW", "2023-2024学年秋冬学期成绩来源"),
                                       ("major_academic", "ALL-HISTORY", "本次取得的主修成绩来源")):
            with self.subTest(operation=operation, term=term):
                self.executed.clear()
                self.set_summary(operation, term)
                result, _, _ = self.run_model([call("query_my_records", {"operation": operation, "target_term": term, "mode": "summary"}),
                                              self.summary_answer])
                self.assertEqual(result["status"], "completed", result.get("code"))
                self.assertIn(label, result["reply"])
                self.assertIn("本次匹配4条记录", result["reply"])
                self.assertIn("纯数字成绩2条", result["reply"])
                self.assertIn("成绩未知1条", result["reply"])
                self.assertIn("85.00", result["reply"])
                self.assertEqual(result["citations"][0]["record_source"]["target_term"], term)
                self.assertFalse(result["citations"][0]["record_source"]["full_student_history_verified"])
                if operation == "academic":
                    self.assertNotIn("主修", result["reply"])

    def test_summary_prose_uses_current_reference_without_template_repair(self):
        self.set_summary()
        result, _, model = self.run_model([call("query_my_records", {"operation": "academic", "target_term": "2023-2024-SS", "mode": "summary"}), self.summary_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
        value=json.loads(next(row["content"] for row in reversed(model.calls[-1][0]) if row["role"] == "tool"))
        self.assertEqual(value["source_grade_summary"]["numeric_mean_unweighted"], "85.00")
        self.assertNotIn("available_sections", value["source_grade_summary"])

    def test_record_source_descriptor_covers_timetable_and_exam_without_changing_record_claims(self):
        for operation, term, expected in (("timetable", "2023-2024-SS", "2023-2024学年春夏学期课表来源"),
                                           ("exams", "2023-2024-W", "2023-2024学年冬学期考试来源")):
            with self.subTest(operation=operation):
                self.context["operations"] = [{"operation": operation, "target_term": term}]
                self.records.update(operation=operation, target_term=term)
                def answer(messages, tools):
                    value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                    self.assertEqual(value["record_source"]["label"], expected)
                    return final({'kind': 'answer', 'text': "这是本次取得的合成课程安排记录。", 'evidence_ids': [value["records"][0]["evidence_id"]]})
                result, _, _ = self.run_model([call("query_my_records", {"operation": operation, "target_term": term}), answer])
                self.assertEqual(result["status"], "completed", result.get("code"))
                self.assertEqual(result["citations"][0]["record_source"]["label"], expected)

    def test_returned_term_mismatch_cannot_mint_source_evidence(self):
        self.set_summary()
        self.records["target_term"] = "2023-2024-AW"
        result, _, _ = self.run_model([call("query_my_records", {"operation": "academic", "target_term": "2023-2024-SS"}),
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertEqual(result["tool_results"][-1]["result"]["code"], "invalid_tool_result")
        self.assertEqual(result["citations"], [])


    def test_partial_summary_retains_coverage_for_model_reasoning(self):
        self.set_summary(complete=False)
        result, _, model = self.run_model([call("query_my_records", {"operation": "academic", "target_term": "2023-2024-SS"}), self.summary_answer])
        self.assertEqual(result["status"], "completed")
        value=json.loads(next(row["content"] for row in reversed(model.calls[-1][0]) if row["role"] == "tool"))
        self.assertFalse(value["coverage"]["complete_within_scope"])
        self.assertFalse(result["citations"][0]["record_source"]["full_student_history_verified"])


    def test_two_term_summaries_and_record_detail_keep_independent_sources(self):
        proofs, budget, refs = {}, [0, 0, {}], []
        for index, term in enumerate(("2023-2024-AW", "2023-2024-SS")):
            self.set_summary(term=term)
            self.records["records"] = [{"course_name_raw": "合成算法", "grade_raw": "80", "field_states": {"grade_raw": "known"}}]
            _, remote = PersonalDialogueService._project("query_my_records", self.records,
                {"operation": "academic", "target_term": term, "query": "", "mode": "records", "limit": 15}, "12345678", proofs, index, budget, set())
            refs.append(remote["source_grade_summary"]["evidence_id"])
            if index == 1: refs.append(remote["records"][0]["evidence_id"])
        _, decision, citations = PersonalDialogueService._final(json.dumps({"kind": "answer", "text": "两个学期分别保留来源，合成算法为80。", "evidence_ids": refs}), proofs)
        self.assertEqual(len(decision["evidence_ids"]), 3)
        self.assertEqual([item["record_source"]["target_term"] for item in citations], ["2023-2024-AW", "2023-2024-SS", "2023-2024-SS"])

    def test_summary_no_numeric_values_and_omitted_completion_field_stay_unknown(self):
        self.set_summary()
        source = self.records["source_grade_summary"]
        source.update(numeric_grade_count=0, non_numeric_grade_count=1, unknown_grade_count=3,
                      numeric_min=None, numeric_max=None, numeric_mean_unweighted=None)
        del source["completion_status_counts"]
        _, remote = PersonalDialogueService._project("query_my_records", self.records,
            {"operation": "academic", "target_term": "2023-2024-SS", "limit": 15}, "12345678", {}, 0, [0, 0, {}], set())
        self.assertIsNone(remote["source_grade_summary"]["numeric_mean_unweighted"])
        self.assertNotIn("completion_status_counts", remote["source_grade_summary"])

    def test_source_descriptor_is_derived_and_cannot_be_overwritten_by_tool_copy(self):
        self.set_summary()
        self.records["record_source"] = {"label": "UNTRUSTED-SOURCE-LABEL"}
        self.records["source_grade_summary"]["record_source"] = {"label": "UNTRUSTED-SUMMARY-LABEL"}
        result, _, model = self.run_model([call("query_my_records", {"operation": "academic", "target_term": "2023-2024-SS"}), self.summary_answer])
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("UNTRUSTED", result["reply"])
        self.assertNotIn("UNTRUSTED", json.dumps(model.calls[-1][0], ensure_ascii=False))

    def test_clarification_data_is_bounded_system_context_and_basis_requires_pending(self):
        pending = {"source": "previous_turn_clarification", "request": "查我的课表", "supplements": [],
            "question": "要查哪个学年和学期？", "previous_task": None,
            "authorization_granted": False, "facts_reusable": False, "fresh_sources_required": True}
        answer = final({'kind': "clarify", 'text': "春夏还是秋冬？", 'request_basis': "pending_task"})
        result, _, model = self.run_model([answer], clarification_context=pending)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_decision"]["request_basis"], "pending_task")
        self.assertIn("previous_clarification", model.calls[0][0][0]["content"])
        self.assertEqual(sum(row["role"] == "user" for row in model.calls[0][0]), 1)
        result, _, _ = self.run_model([answer, final({'kind': "clarify", 'text': "要查哪个学年？"})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "clarification_context_missing")
        self.assertEqual(result["model_decision"]["request_basis"], "current_message")

    def test_pending_clarification_requires_explicit_basis_before_updating_task(self):
        pending = {"source": "previous_turn_clarification", "request": "查我的春夏成绩", "supplements": [],
            "question": "要查哪个学年？", "previous_task": None,
            "authorization_granted": False, "facts_reusable": False, "fresh_sources_required": True}
        result, _, _ = self.run_model([
            final({'kind': "clarify", 'text': "还需要明确具体范围吗？"}),
            final({'kind': "clarify", 'text': "要查该学年春夏的哪些课程？", 'request_basis': "pending_task"})],
            clarification_context=pending)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "clarification_basis_required")
        self.assertEqual(result["model_decision"]["request_basis"], "pending_task")

    def test_term_encoding_rejection_corrects_once_without_dispatching_or_echoing_proposal(self):
        bad = call("query_my_records", {"operation": "major_academic", "target_term": "全部历史"})
        good = call("query_my_records", {"operation": "major_academic", "target_term": "ALL-HISTORY"}, "corrected")
        result, history, model = self.run_model([bad, good, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["argument_corrections"], 1)
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
        self.assertNotIn("全部历史", json.dumps(model.calls[1][0], ensure_ascii=False))
        self.assertFalse(any(row["role"] == "system" for row in history))
        self.assertEqual(self.events[0], ("tool.rejected", {"tool_name": "query_my_records", "count": 1,
            "error_code": "invalid_tool_arguments", "failure_detail": "argument_schema_mismatch", "attempt": 1, "status": "retrying"}))

    def test_previous_task_is_a_bounded_system_hint_and_never_old_evidence(self):
        task = {"source": "previous_turn_executed_tools", "turn_status": "completed",
            "tasks": [{"tool": "query_my_records", "operation": "academic", "target_term": "2023-2024-AW", "query": "", "mode": "summary"}],
            "authorization_granted": False, "facts_reusable": False, "fresh_sources_required": True, "tasks_truncated": False}
        result, _, model = self.run_model([final({'kind': "clarify", 'text': "要看同学年的春夏成绩吗？"})], task_context=task)
        self.assertEqual(result["status"], "completed")
        self.assertIn('"target_term":"2023-2024-AW"', model.calls[0][0][0]["content"])
        self.assertEqual(sum(row["role"] == "user" for row in model.calls[0][0]), 1)
        self.assertEqual(self.executed, [("get_my_context", {})])
        with self.assertRaises(PersonalDialogueError) as caught:
            self.run_model([], task_context={**task, "records": [{"grade_raw": 88}]})
        self.assertEqual(caught.exception.code, "invalid_task_context")

    def test_second_malformed_term_stops_without_executing_records(self):
        result, history, model = self.run_model([
            call("query_my_records", {"operation": "major_academic", "target_term": "全部"}, "first"),
            call("query_my_records", {"operation": "major_academic", "target_term": "全部"}, "second")])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertEqual(history, [])
        self.assertEqual(self.events[-1][1]["status"], "failed")

    def test_declared_argument_shapes_retry_once_with_only_safe_schema_feedback(self):
        cases = [({}, "required_argument_missing"), ({"operation": []}, "argument_type_invalid"),
            ({"operation": "major_academic", "unrecognized_parameter": "REJECTED-PARAMETER-TEXT"}, "unexpected_argument"),
            ({"operation": "major_academic", "limit": 16}, "argument_value_invalid")]
        for arguments, expected in cases:
            with self.subTest(issue=expected):
                self.executed.clear()
                self.events.clear()
                result, history, model = self.run_model([call("query_my_records", arguments),
                    call("query_my_records", {"operation": "major_academic"}, "corrected"), self.valid_record_answer])
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual((result["argument_corrections"], result["repair_attempts"]), (1, 0))
                self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
                diagnostic = result["validation_diagnostics"][0]
                self.assertEqual((diagnostic["tool_name"], diagnostic["failure_detail"]), ("query_my_records", expected))
                feedback = model.calls[1][0][-1]["content"]
                self.assertIn('"tool_schemas"', feedback)
                self.assertIn(expected, feedback)
                self.assertTrue(model.calls[1][1])
                retained = json.dumps([model.calls[1][0], history, result["validation_diagnostics"]], ensure_ascii=False)
                self.assertNotIn("unrecognized_parameter", retained)
                self.assertNotIn("REJECTED-PARAMETER-TEXT", retained)

    def test_generic_argument_rejection_discards_entire_mixed_batch(self):
        batch = call("query_my_records", {"operation": "major_academic"}, "valid")
        batch["tool_calls"] += call("search_my_curriculum", {"query": []}, "invalid")["tool_calls"]
        def clarify(messages, tools):
            self.assertEqual(self.executed, [("get_my_context", {})])
            return final({'kind': "clarify", 'text': "请确认这次要查询的范围？"})
        result, _, _ = self.run_model([batch, clarify])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["argument_corrections"], 1)
        self.assertEqual(result["validation_diagnostics"][0]["count"], 2)
        self.assertEqual(self.executed, [("get_my_context", {})])

    def test_recovery_rejects_malformed_tail_after_an_early_recoverable_error(self):
        # The first error stops normal parsing; recovery must still validate
        # the entire batch, including IDs/structure of later calls.
        proposals = [call("query_my_records", {"operation": "major_academic", "target_term": "全部"}),
            call("search_my_curriculum", {"query": []}), call("unavailable_helper")]
        for proposal in proposals:
            for malformed in ("duplicate_id", "function_shape"):
                with self.subTest(tool=proposal["tool_calls"][0]["function"]["name"], tail=malformed):
                    self.executed.clear()
                    batch = deepcopy(proposal)
                    tail = call("get_my_context", identifier="tail")["tool_calls"][0]
                    if malformed == "duplicate_id":
                        tail["id"] = batch["tool_calls"][0]["id"]
                    else:
                        tail["function"] = {"UNTRUSTED-FIELD": "UNTRUSTED-VALUE"}
                    batch["tool_calls"].append(tail)
                    original = deepcopy(batch)
                    result, history, model = self.run_model([batch])
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual((result["argument_corrections"], result["repair_attempts"]), (0, 0))
                    self.assertEqual(self.executed, [("get_my_context", {})])
                    self.assertEqual(len(model.calls), 1)
                    self.assertEqual(history, [])
                    self.assertEqual(batch, original)
                    self.assertNotIn("UNTRUSTED", json.dumps(result["validation_diagnostics"]))

    def test_generic_and_term_corrections_share_one_retry_and_original_budgets(self):
        bad = call("query_my_records", {"operation": "major_academic", "limit": 16}, "invalid")
        term = call("query_my_records", {"operation": "major_academic", "target_term": "全部"}, "bad-term")
        for responses in ([bad, bad], [bad, term], [term, bad]):
            self.executed.clear()
            result, history, model = self.run_model(responses)
            self.assertEqual(result["code"], "invalid_tool_arguments")
            self.assertEqual(result["argument_corrections"], 1)
            self.assertEqual(len(model.calls), 2)
            self.assertEqual(self.executed, [("get_my_context", {})])
            self.assertEqual(history, [])
        for limits in ({"max_tools": 1}, {"max_steps": 1}):
            self.executed.clear()
            result, _, model = self.run_model([bad], **limits)
            self.assertEqual(result["code"], "turn_budget_exceeded")
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(self.executed, [("get_my_context", {})])

    def test_argument_recovery_never_admits_identity_paths_cursor_or_sensitive_values(self):
        for extra in ({"owner": "OTHER"}, {"owner_id": "OTHER"}, {"path": "local"},
                {"source_path": "local"}, {"cursor": "opaque"}, {"authorization": "grant"},
                {"unknown": {"student_id": "OTHER"}}, {"user_id": "OTHER"},
                {"subject_id": "OTHER"}, {"student_name": "OTHER"}):
            self.executed.clear()
            result, _, model = self.run_model([call("query_my_records", {"operation": "major_academic", **extra})])
            self.assertEqual(result["code"], "invalid_tool_arguments")
            self.assertEqual(result["argument_corrections"], 0)
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(self.executed, [("get_my_context", {})])
        result, _, model = self.run_model([call("search_my_curriculum", {"query": "cookie=SYNTHETIC-SECRET"})])
        self.assertEqual(result["code"], "sensitive_personal_content")
        self.assertEqual(result["argument_corrections"], 0)
        self.assertEqual(len(model.calls), 1)

    def test_message_protocol_failures_project_fixed_categories_without_raw_message(self):
        prototype = call("get_my_context")
        def changed_call(**changes):
            value = deepcopy(prototype)
            value["tool_calls"][0].update(changes)
            return value
        duplicate = deepcopy(prototype)
        duplicate["tool_calls"] *= 2
        batch = {"role": "assistant", "content": None, "tool_calls": [
            call("get_my_context", identifier=f"call-{i}")["tool_calls"][0] for i in range(4)]}
        cases = [([], "message_type_invalid"), ({"role": "UNTRUSTED-ROLE"}, "message_role_invalid"),
            ({"role": "assistant", "reasoning_content": {"secret": "UNTRUSTED-REASONING"}}, "reasoning_type_invalid"),
            ({"role": "assistant", "reasoning_content": "x" * 64001}, "reasoning_length_exceeded"),
            ({"role": "assistant", "content": {"secret": "UNTRUSTED-CONTENT"}}, "content_type_invalid"),
            ({"role": "assistant", "content": "x" * 12001}, "content_length_exceeded"),
            ({"role": "assistant", "tool_calls": {"UNTRUSTED-TOOL": "UNTRUSTED-ARGS"}}, "tool_batch_type_invalid"),
            (batch, "tool_batch_count_exceeded"), ({"role": "assistant", "content": " "}, "message_empty"),
            ({"role": "assistant", "tool_calls": [{"UNTRUSTED-FIELD": "UNTRUSTED-CALL"}]}, "tool_call_shape_invalid"),
            (changed_call(type="UNTRUSTED-TYPE"), "tool_call_type_invalid"),
            (changed_call(id={"UNTRUSTED-ID": "UNTRUSTED-VALUE"}), "tool_call_id_type_invalid"),
            (changed_call(id="UNTRUSTED-ID!"), "tool_call_id_format_invalid"),
            (duplicate, "tool_call_id_duplicate")]
        for raw, expected in cases:
            with self.subTest(detail=expected):
                self.executed.clear()
                result, history, model = self.run_model([raw])
                self.assertEqual(result["code"], "model_protocol_error")
                self.assertEqual((result["repair_attempts"], result["argument_corrections"]), (0, 0))
                self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], expected)
                self.assertLessEqual(set(result["validation_diagnostics"][0]), {"error_code", "failure_detail", "count"})
                self.assertNotIn("UNTRUSTED", json.dumps(result["validation_diagnostics"]))
                self.assertEqual(self.executed, [("get_my_context", {})])
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(history, [])

    def test_message_failure_counts_are_bounded_and_prompt_keeps_batch_limit(self):
        raw = {"role": "assistant", "content": "UNTRUSTED" * 20000}
        result, _, model = self.run_model([raw])
        self.assertEqual(result["validation_diagnostics"], [{"error_code": "model_protocol_error",
            "failure_detail": "content_length_exceeded", "count": 100000}])
        self.assertIn("每次最多3个相互独立的调用", model.calls[0][0][0]["content"])
        self.assertIn("依赖前一步结果的调用等待结果", model.calls[0][0][0]["content"])

    def test_term_recovery_does_not_relax_identity_paging_or_current_scope_validation(self):
        for extra in ({"student_id": "other"}, {"page_token": "PAGE-12345678-1"}):
            with self.subTest(extra=extra):
                self.executed.clear()
                result, _, model = self.run_model([call("query_my_records", {
                    "operation": "major_academic", "target_term": "全部", **extra})])
                self.assertEqual(result["code"], "invalid_tool_arguments")
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(self.executed, [("get_my_context", {})])
        result, _, model = self.run_model([call("query_my_records", {
            "operation": "academic", "target_term": "2098-2099-SS"})])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        self.assertEqual(len(model.calls), 1)

    def test_rejected_batch_dispatches_neither_valid_nor_invalid_members(self):
        batch = call("query_my_records", {"operation": "major_academic"}, "valid")
        batch["tool_calls"].extend(call("query_my_records", {
            "operation": "major_academic", "target_term": "全部"}, "invalid")["tool_calls"])
        result, _, _ = self.run_model([batch, final({'kind': "clarify", 'text': "要查看哪个范围？"})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertEqual(self.events[0][1]["count"], 2)

    def test_corrected_arguments_still_require_current_authorization(self):
        def revoke(messages, tools):
            self.denied = True
            return call("query_my_records", {"operation": "major_academic"}, "corrected")
        result, history, _ = self.run_model([call("query_my_records", {
            "operation": "major_academic", "target_term": "全部"}), revoke])
        self.assertEqual(result["code"], "grant_revoked")
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(history, [])

    def test_schema_correction_consumes_existing_step_budget(self):
        result, _, model = self.run_model([call("query_my_records", {
            "operation": "major_academic", "target_term": "全部"})], max_steps=1)
        self.assertEqual(result["code"], "turn_budget_exceeded")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.executed, [("get_my_context", {})])

    def test_schema_correction_consumes_existing_tool_budget(self):
        model = ScriptedModel([call("query_my_records", {"operation": "major_academic", "target_term": "全部"}),
            call("query_my_records", {"operation": "major_academic"}, "corrected")])
        service = PersonalDialogueService(model, self.authorize, self.execute, max_tools=2)
        result, _ = service.run("合成查询", data_use="personal_retrieval", emit=self.emit)
        self.assertEqual(result["code"], "turn_budget_exceeded")
        self.assertEqual(self.executed, [("get_my_context", {})])

    def test_one_format_repair_reuses_tool_results_and_preserves_clean_history(self):
        malformed = {"role": "assistant", "content": '```json\n{"kind":"answer"}\n```'}
        result, history, model = self.run_model([call("query_my_records", {"operation": "major_academic"}), malformed, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "invalid_json")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
        self.assertEqual(self.executed, [("get_my_context", {}), ("query_my_records", {"operation": "major_academic", "query": "", "limit": 15})])
        self.assertFalse(any(row["role"] == "system" or "```" in (row.get("content") or "") for row in history))
        self.assertEqual([kind for kind, _ in self.events], ["answer.rejected", "answer.repair", "answer.repair"])

    def test_repair_uses_current_evidence_and_does_not_accept_old_or_fabricated_ids(self):
        bad = final({'kind': 'answer', 'text': "合成课程成绩88。", 'evidence_ids': ["old-id"]})
        result, _, _ = self.run_model([call("query_my_records", {"operation": "major_academic"}), bad, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "evidence_not_current")
        result, history, model = self.run_model([bad, bad, final({'kind': 'answer', 'text': '你好，我可以根据已开放的教务资料回答问题。', 'evidence_ids': []})])
        self.assertEqual(result["code"], "unverified_personal_answer")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(result["validation_diagnostics"]), 2)
        self.assertEqual(history, [])

    def test_repair_request_excludes_rejected_draft_and_its_reasoning(self):
        rejected = final({"kind": "answer", "claims": [{"text": "UNTRUSTED-REJECTED-DRAFT：已满足毕业要求。",
            "evidence_ids": [], "unexpected": True}]})
        rejected["reasoning_content"] = "UNTRUSTED-REJECTED-REASONING"
        result, _, model = self.run_model([call("query_my_records", {"operation": "major_academic"}),
            rejected, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        outbound = json.dumps(model.calls[-1][0], ensure_ascii=False)
        self.assertNotIn("UNTRUSTED-REJECTED", outbound)
        self.assertNotIn("已满足毕业要求", outbound)
        self.assertIn("answer_shape_invalid", outbound)
        self.assertIn("合成课程", outbound)
        self.assertEqual(len([name for name, _ in self.executed if name == "query_my_records"]), 1)

    def many_records(self, count):
        self.records.update(matched_records=count, records=[{"course_name_raw": f"合成课{index}", "grade_raw": "88",
            "field_states": {"course_name_raw": "known", "grade_raw": "known"}} for index in range(count)])

    def test_ten_course_list_retains_all_ten_current_record_references(self):
        self.many_records(10)
        def complete_list(messages, tools):
            rows=json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["records"]
            return final({'kind': 'answer', 'text': "；".join(row["course_name_raw"]+"的来源成绩88" for row in rows), 'evidence_ids': [row["evidence_id"] for row in rows]})
        result, _, _ = self.run_model([call("query_my_records", {"operation":"major_academic"}), complete_list])
        self.assertGreaterEqual(MAX_ANSWER_REFERENCES, 16)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(len(result["citations"]), 10)
        self.assertTrue(all(c["kind"] == "record" for c in result["citations"]))

    def test_sixteen_current_references_need_no_claim_splitting(self):
        self.many_records(15)
        def answer(messages, tools):
            values=[json.loads(row["content"]) for row in messages if row["role"]=="tool"]
            refs=[values[0]["evidence_id"]]+[row["evidence_id"] for row in values[-1]["records"]]
            return final({"kind":"answer", "text":"当前范围与15条合成来源记录。", "evidence_ids":refs})
        result, _, _ = self.run_model([call("get_my_context"), call("query_my_records", {"operation":"major_academic"}, "records"), answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["citations"]), 16)
        self.assertEqual(result["repair_attempts"], 0)

    def test_repair_has_one_extra_completion_even_at_original_step_limit(self):
        result, _, model = self.run_model([final({"kind": "unknown"}), final({'kind': 'answer', 'text': '你好，我可以根据已开放的教务资料回答问题。', 'evidence_ids': []})], max_steps=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["repair_attempts"], 1)

    def test_repair_cannot_execute_new_tools(self):
        result, _, model = self.run_model([final({"kind": "unknown"}), call("query_my_records", {"operation": "major_academic"})])
        self.assertEqual(result["code"], "model_protocol_error")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.executed, [("get_my_context", {})])

    def test_undeclared_tool_batch_is_discarded_and_repair_has_no_source_tools(self):
        proposed = call("query_my_records", {"operation": "major_academic"}, "allowed-but-not-run")
        proposed["tool_calls"] += call("unavailable_helper", {"query": "REJECTED-BATCH-MARKER"}, "rejected-id")["tool_calls"]
        proposed["content"] = "REJECTED-CONTENT-MARKER"
        proposed["reasoning_content"] = "REJECTED-REASONING-MARKER"
        result, history, model = self.run_model([proposed,
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})], max_steps=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_decision"]["kind"], "not_ready")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertEqual([row["name"] for row in result["tool_results"]], ["get_my_context"])
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "undeclared_tool_call")
        retained = json.dumps([model.calls[-1][0], history, result["validation_diagnostics"]])
        self.assertNotIn("unavailable_helper", retained)
        self.assertNotIn("REJECTED-", retained)
        self.assertNotIn("rejected-id", retained)

    def test_undeclared_tool_repair_reuses_current_record_evidence_without_new_reads(self):
        result, _, model = self.run_model([
            call("query_my_records", {"operation": "major_academic"}, "source"),
            call("another_namespace.finalize", {"query": "DISCARD-ME"}, "unknown"), self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
        self.assertEqual(result["citations"][0]["kind"], "record")

    def test_undeclared_tool_repair_is_shared_once_and_cannot_request_another_tool(self):
        for second, expected in ((call("unavailable_helper"), "tool_not_allowed"),
                (call("query_my_records", {"operation": "major_academic"}), "model_protocol_error"),
                (final({"kind": "answer", "claims": []}), "invalid_final_answer")):
            with self.subTest(expected=expected):
                self.executed.clear()
                result, history, model = self.run_model([call("unavailable_helper"), second])
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["repair_attempts"], 1)
                self.assertEqual(len(model.calls), 2)
                self.assertEqual([tool["function"]["name"] for tool in model.calls[-1][1]], ["finish_answer"])
                self.assertEqual(self.executed, [("get_my_context", {})])
                self.assertEqual(history, [])

    def test_sensitive_unknown_tool_arguments_and_revocation_do_not_get_repair(self):
        for args in ({"password": "SYNTHETIC-SECRET"}, {"query": "cookie=SYNTHETIC-SECRET"},
                {"owner_student_id": "OTHER"}):
            with self.subTest(args=args):
                result, _, model = self.run_model([call("unavailable_helper", args)])
                self.assertEqual(result["code"], "sensitive_personal_content")
                self.assertEqual(result["repair_attempts"], 0)
                self.assertEqual(len(model.calls), 1)
        def revoke(messages, tools):
            self.denied = True
            return call("unavailable_helper")
        result, _, model = self.run_model([revoke])
        self.assertEqual(result["code"], "grant_revoked")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(len(model.calls), 1)

    def test_unsupported_or_sensitive_answers_do_not_get_format_repair(self):
        for text, code in (("已经正式中选。", "unsupported_personal_claim"), ("password=SYNTHETIC-SECRET", "sensitive_personal_content")):
            with self.subTest(code=code):
                self.executed.clear()
                result, _, model = self.run_model([final({'kind': 'answer', 'text': text, 'evidence_ids': ["old"]})])
                self.assertEqual(result["code"], code)
                self.assertEqual(result["repair_attempts"], 0)
                self.assertEqual(len(model.calls), 1)

    def test_authorization_rechecked_before_repair_and_data_removed_on_revocation(self):
        def revoke(kind, fields):
            self.emit(kind, fields)
            if kind == "answer.repair" and fields.get("status") == "started":
                self.denied = True
        result, _, model = self.run_model([final({"kind": "unknown"}), final({'kind': 'answer', 'text': '你好，我可以根据已开放的教务资料回答问题。', 'evidence_ids': []})], emit=revoke)
        self.assertEqual(result["code"], "grant_revoked")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(result["validation_diagnostics"], [])

    def test_diagnostic_tool_is_local_bounded_and_citable_without_remote_run_id(self):
        def explain(messages, tools):
            value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertEqual(value["diagnostic"]["error_code"], "invalid_final_answer")
            self.assertFalse(value["diagnostic"]["thinking"])
            self.assertFalse(value["diagnostic"]["arbitrary_scripts_executed"])
            return final({'kind': 'answer', 'text': "上一轮已经查询记录，在最终答案格式校验时失败；没有执行任意终端脚本。", 'evidence_ids': [value["evidence_id"]]})
        result, history, model = self.run_model([call("get_my_run_diagnostics"), explain], execution_context=self.previous)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertIn("get_my_run_diagnostics", {row["function"]["name"] for row in model.calls[0][1]})
        self.assertEqual(result["citations"][0]["kind"], "diagnostic")
        self.assertEqual(result["citations"][0]["diagnostic"]["run_id"], self.previous["run_id"])
        self.assertNotIn(self.previous["run_id"], json.dumps(history))
        self.assertEqual([kind for kind, _ in self.events], ["tool.start", "tool.end"])

    def test_diagnostics_admit_registered_timetable_exam_reads_but_not_arbitrary_read_names(self):
        for name, operation, term in (("read_my_timetable", "timetable", "2023-2024-AW"),
                                      ("read_my_exams", "exams", "2023-2024-A")):
            with self.subTest(name=name):
                previous = deepcopy(self.previous)
                previous["events"][0]["fields"].update(tool_name=name, operation=operation, target_term=term)
                def explain(messages, tools):
                    value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                    self.assertEqual(value["diagnostic"]["events"][0]["fields"]["tool_name"], name)
                    return final({'kind': 'answer', 'text': "上一轮只读取得相应安排，随后答复格式检查未完成。", 'evidence_ids': [value["evidence_id"]]})
                result, _, _ = self.run_model([call("get_my_run_diagnostics"), explain], execution_context=previous)
                self.assertEqual(result["status"], "completed", result.get("code"))
        previous["events"][0]["fields"]["tool_name"] = "read_my_arbitrary_endpoint"
        with self.assertRaises(PersonalDialogueError) as caught:
            self.run_model([], execution_context=previous)
        self.assertEqual(caught.exception.code, "invalid_execution_context")

    def test_no_prior_run_cannot_produce_diagnostic_evidence(self):
        result, history, model = self.run_model([call("get_my_run_diagnostics"), call("get_my_run_diagnostics")])
        self.assertNotIn("get_my_run_diagnostics", {row["function"]["name"] for row in model.calls[0][1]})
        # The undeclared call is rejected before execution. A failed turn can
        # retain its genuine context proof, but must never create a prior-run
        # diagnostic proof or pretend that diagnostic execution happened.
        self.assertEqual((result["status"], result["code"]), ("failed", "tool_not_allowed"))
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertTrue(all(citation["kind"] == "context" for citation in result["citations"]))
        self.assertFalse(any("diagnostic" in citation for citation in result["citations"]))
        self.assertEqual([tool["name"] for tool in result["tool_results"]], ["get_my_context"])
        self.assertEqual(history, [])
        self.assertFalse(any(kind in {"tool.start", "tool.end"} for kind, _ in self.events))
        self.assertEqual(len(model.calls), 2)

    def test_no_prior_run_can_safely_return_not_ready_without_fabricated_evidence(self):
        result, _, model = self.run_model([final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_decision"]["kind"], "not_ready")
        self.assertEqual(result["citations"], [])
        self.assertNotIn("get_my_run_diagnostics", {row["function"]["name"] for row in model.calls[0][1]})

    def test_untrusted_diagnostic_text_or_identity_fields_never_reach_model(self):
        for field, value in (("reasoning_content", "NEVER-SEND"), ("query", "NEVER-SEND"), ("owner", "NEVER-SEND"), ("usage", {"prompt_tokens": 3})):
            bad = deepcopy(self.previous)
            bad["events"][0]["fields"][field] = value
            model = ScriptedModel([])
            service = PersonalDialogueService(model, self.authorize, self.execute)
            with self.assertRaises(PersonalDialogueError) as caught:
                service.run("合成请求", data_use="personal_retrieval", execution_context=bad)
            self.assertEqual(caught.exception.code, "invalid_execution_context")
            self.assertEqual(model.calls, [])

    def test_fine_grained_validation_metadata_contains_no_raw_answer(self):
        for value, detail in (([], "answer_not_object"), ({"kind": "answer", "claims": "NEVER-SEND"}, "answer_shape_invalid"),
                ({"kind": "answer", "text": ""}, "answer_text_invalid"),
                ({"kind": "answer", "text": "NEVER-SEND", "extra": True}, "answer_shape_invalid"),
                ({"kind": "clarify", "text": "x" * 6001}, "answer_text_invalid")):
            with self.subTest(detail=detail):
                result, _, _ = self.run_model([final(value), final(value)])
                self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], detail)
                self.assertNotIn("NEVER-SEND", json.dumps(result["validation_diagnostics"]))


class ReadinessDialogueTests(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.context = {"status": "available", "operations": [],
            "api_operations": ["academic", "major_academic", "timetable", "exams"],
            "capabilities": {"recommendation_input_check": True, "live_record_query": True,
                "course_catalog_search": True, "read_only_acquisition": True, "curriculum_local_search": True},
            "course_catalog": {"status": "ready"}, "source_receipts": []}
        self.arguments = {"recommendation_kind": "timetable", "target_term": "2023-2024-AW"}
        self.override = None

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        if name == "check_recommendation_inputs":
            return deepcopy(self.override) if self.override is not None else self.wrapper(args)
        raise AssertionError("readiness_must_not_acquire_other_sources")

    def wrapper(self, args):
        return {"status": "available", "data_kind": "personal_source", "check_executed": True,
                "readiness": assess_recommendation_readiness(self.context, **args)}

    def run_model(self, responses):
        model = ScriptedModel(responses)
        service = PersonalDialogueService(model, lambda: {"scope_key": "SYNTHETIC-READINESS"}, self.execute)
        response, history = service.run("先检查2023-2024秋冬的选课课表推荐输入。", data_use="personal_retrieval")
        return response, history, model

    @staticmethod
    def readiness_answer(messages, tools):
        value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
        return final({'kind': 'answer', 'text': '2023-2024学年秋冬学期的正式推荐尚未就绪。正式方案生成、保存和导出尚未开放。', 'evidence_ids': ([value['evidence_id']])})

    def test_supported_readiness_tool_yields_bounded_proof_without_generation_or_other_reads(self):
        result, _, model = self.run_model([call("check_recommendation_inputs", self.arguments), self.readiness_answer])
        self.assertEqual(result["status"], "completed", result.get("code"))
        declared = {tool["function"]["name"] for tool in model.calls[0][1]}
        self.assertIn("check_recommendation_inputs", declared)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "check_recommendation_inputs"])
        proof = result["citations"][0]
        self.assertEqual(proof["kind"], "recommendation_readiness")
        self.assertEqual(proof["readiness"]["status"], "not_ready")
        self.assertFalse(proof["readiness"]["can_generate"])
        self.assertFalse(proof["readiness"]["can_save_or_export"])
        self.assertEqual(result["tool_results"][-1]["result"]["status"], "available")
        self.assertIn("正式推荐尚未就绪", result["reply"])
        self.assertIn("2023-2024学年秋冬学期", result["reply"])
        self.assertIn("正式方案生成、保存和导出尚未开放", result["reply"])
        self.assertNotIn("来源准备：", result["reply"])
        self.assertNotIn("仍需核实：", result["reply"])
        remote = json.loads(next(row["content"] for row in reversed(model.calls[-1][0]) if row["role"] == "tool"))
        self.assertEqual(set(remote["readiness"]), set(proof["readiness"]))
        self.assertIn("已有安排不是可选班次清单", remote["readiness"]["next_step"])
        self.assertIn("考试仅在明确要求核对时读取", remote["readiness"]["next_step"])

    def test_missing_or_non_boolean_capability_never_declares_or_executes_readiness(self):
        for capability in (False, 1, None):
            with self.subTest(capability=capability):
                self.executed.clear()
                self.context["capabilities"]["recommendation_input_check"] = capability
                result, _, model = self.run_model([call("check_recommendation_inputs", self.arguments),
                    call("check_recommendation_inputs", self.arguments)])
                self.assertEqual(result["code"], "tool_not_allowed")
                self.assertNotIn("check_recommendation_inputs", {tool["function"]["name"] for tool in model.calls[0][1]})
                self.assertEqual(self.executed, [("get_my_context", {})])

    def test_invalid_identity_scope_or_kind_arguments_are_not_executed(self):
        for arguments in ({}, {"recommendation_kind": "all"}, {"recommendation_kind": "courses", "target_term": None},
                          {"recommendation_kind": "courses", "target_term": "2023-2024-A"},
                          {"recommendation_kind": "courses", "owner_student_id": "SYNTHETIC-OTHER"}):
            with self.subTest(arguments=arguments):
                self.executed.clear()
                result, _, model = self.run_model([call("check_recommendation_inputs", arguments),
                    call("check_recommendation_inputs", arguments)])
                self.assertEqual(result["code"], "invalid_tool_arguments")
                self.assertEqual(len(model.calls), 1 if "owner_student_id" in arguments else 2)
                self.assertEqual(self.executed, [("get_my_context", {})])

    def test_unsupported_generation_and_private_tool_fields_never_mint_readiness_proof(self):
        for injection in ("private", "generation"):
            with self.subTest(injection=injection):
                self.executed.clear()
                self.override = self.wrapper(self.arguments)
                if injection == "private":
                    self.override["readiness"]["sources"][0]["owner_student_id"] = "PRIVATE-OWNER"
                else:
                    self.override["readiness"]["can_generate"] = True
                result, _, model = self.run_model([call("check_recommendation_inputs", self.arguments),
                    final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["tool_results"][-1]["error"])
                self.assertEqual(result["tool_results"][-1]["result"]["code"], "invalid_tool_result")
                self.assertFalse(any(proof["kind"] == "recommendation_readiness" for proof in result["citations"]))
                self.assertNotIn("PRIVATE-OWNER", json.dumps(model.calls))

    def test_readiness_has_independent_utf8_budget_and_no_unevidenced_remote_fallback(self):
        wrapper, args = self.wrapper(self.arguments), self.arguments
        proofs, occupied = {}, {kind: limit for kind, limit in EVIDENCE_LIMITS.items() if kind != "P"}
        budget = [MAX_EGRESS_CHARS - EVIDENCE_LIMITS["P"], 0, occupied]
        _, remote = PersonalDialogueService._project("check_recommendation_inputs", wrapper, args,
            "12345678", proofs, 0, budget, set())
        self.assertIn("evidence_id", remote)
        self.assertEqual(proofs[remote["evidence_id"]]["kind"], "recommendation_readiness")
        self.assertLessEqual(budget[2]["P"], 6000)
        _, exhausted = PersonalDialogueService._project("check_recommendation_inputs", wrapper, args,
            "12345678", proofs, 1, [6000, 0, {"P": 6000}], set())
        self.assertEqual(exhausted["status"], "not_ready")
        self.assertNotIn("evidence_id", exhausted)
        self.assertNotIn("readiness", exhausted)

    def test_readiness_answer_cannot_create_a_computed_plan(self):
        result, _, _ = self.run_model([call("check_recommendation_inputs", self.arguments), self.readiness_answer])
        self.assertIsNone(result.get("recommendation_result"))
        self.assertFalse(result["citations"][0]["readiness"]["can_generate"])

    def test_readiness_prose_needs_no_source_specific_format_repair(self):
        result, _, model = self.run_model([call("check_recommendation_inputs", self.arguments), self.readiness_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "check_recommendation_inputs"])





if __name__ == "__main__":
    unittest.main()
