"""Bounded final-answer repair and private prior-run diagnostics; no network."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_dialogue import PersonalDialogueError, PersonalDialogueService, MAX_CLAIM_REFERENCES
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

    def run_model(self, responses, *, execution_context=None, max_steps=5, emit=None):
        model = ScriptedModel(responses)
        service = PersonalDialogueService(model, self.authorize, self.execute, max_steps=max_steps)
        result, history = service.run("合成测试请求", data_use="personal_retrieval",
            execution_context=execution_context, emit=emit or self.emit)
        return result, history, model

    @staticmethod
    def valid_record_answer(messages, tools):
        value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
        return final({"kind": "answer", "claims": [{"text": "合成课程的来源分数是88。", "evidence_ids": [value["records"][0]["evidence_id"]]}]})

    def test_one_format_repair_reuses_tool_results_and_preserves_clean_history(self):
        malformed = {"role": "assistant", "content": '```json\n{"kind":"answer"}\n```'}
        result, history, model = self.run_model([call("query_my_records", {"operation": "major_academic"}), malformed, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "invalid_json")
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(model.calls[-1][1], [])
        self.assertEqual(self.executed, [("get_my_context", {}), ("query_my_records", {"operation": "major_academic", "query": "", "limit": 15})])
        self.assertFalse(any(row["role"] == "system" or "```" in (row.get("content") or "") for row in history))
        self.assertEqual([kind for kind, _ in self.events], ["answer.rejected", "answer.repair", "answer.repair"])

    def test_repair_uses_current_evidence_and_does_not_accept_old_or_fabricated_ids(self):
        bad = final({"kind": "answer", "claims": [{"text": "合成课程成绩88。", "evidence_ids": ["old-id"]}]})
        result, _, _ = self.run_model([call("query_my_records", {"operation": "major_academic"}), bad, self.valid_record_answer])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "evidence_not_current")
        result, history, model = self.run_model([bad, bad, final({"kind": "general_chat", "intent": "identity"})])
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
        self.assertIn("claim_shape_invalid", outbound)
        self.assertIn("合成课程", outbound)
        self.assertEqual(len([name for name, _ in self.executed if name == "query_my_records"]), 1)

    def many_records(self, count):
        self.records.update(matched_records=count, records=[{"course_name_raw": f"合成课{index}", "grade_raw": "88",
            "field_states": {"course_name_raw": "known", "grade_raw": "known"}} for index in range(count)])

    def test_ten_course_list_retains_all_ten_current_record_references(self):
        self.many_records(10)
        def complete_list(messages, tools):
            rows=json.loads(messages[-1]["content"])["records"]
            return final({"kind":"answer", "claims":[{"text":"；".join(row["course_name_raw"]+"的来源成绩88" for row in rows),
                "evidence_ids":[row["evidence_id"] for row in rows]}]})
        result, _, _ = self.run_model([call("query_my_records", {"operation":"major_academic"}), complete_list])
        self.assertEqual(MAX_CLAIM_REFERENCES, 15)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(len(result["citations"]), 10)
        self.assertTrue(all(c["kind"] == "record" for c in result["citations"]))

    def test_sixteen_current_references_require_split_and_cannot_be_dropped_in_repair(self):
        self.many_records(15)
        reference_ids=[]
        def overflow(messages, tools):
            values=[json.loads(row["content"]) for row in messages if row["role"]=="tool"]
            reference_ids[:]=[values[0]["evidence_id"]]+[row["evidence_id"] for row in values[-1]["records"]]
            self.assertEqual(len(reference_ids),16)
            return final({"kind":"answer", "claims":[{"text":"当前范围和合成课程来源记录。", "evidence_ids":reference_ids}]})
        def repaired_drop(messages, tools):
            return final({"kind":"answer", "claims":[{"text":"当前范围和合成课程来源记录。", "evidence_ids":reference_ids[:15]}]})
        result, _, model=self.run_model([call("get_my_context"),call("query_my_records",{"operation":"major_academic"},"records"),overflow,repaired_drop])
        self.assertEqual(result["code"],"unverified_personal_answer")
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"],"evidence_list_invalid")
        self.assertEqual(result["validation_diagnostics"][-1]["failure_detail"],"repair_evidence_omitted")
        self.assertEqual(result["repair_attempts"],1)
        self.assertEqual(len(model.calls),4)
        self.assertEqual(len([name for name,_ in self.executed if name=="query_my_records"]),1)
        self.executed.clear()
        def repaired_split(messages, tools):
            return final({"kind":"answer", "claims":[{"text":"当前范围和部分合成来源记录。", "evidence_ids":reference_ids[:8]},
                {"text":"其余合成来源记录。", "evidence_ids":reference_ids[8:]}]})
        result, _, _=self.run_model([call("get_my_context"),call("query_my_records",{"operation":"major_academic"},"records"),overflow,repaired_split])
        self.assertEqual(result["status"],"completed")
        self.assertEqual(len(result["citations"]),16)
        self.assertEqual(result["repair_attempts"],1)

    def test_repair_has_one_extra_completion_even_at_original_step_limit(self):
        result, _, model = self.run_model([final({"kind": "unknown"}), final({"kind": "general_chat", "intent": "identity"})], max_steps=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["repair_attempts"], 1)

    def test_repair_cannot_execute_new_tools(self):
        result, _, model = self.run_model([final({"kind": "unknown"}), call("query_my_records", {"operation": "major_academic"})])
        self.assertEqual(result["code"], "model_protocol_error")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.executed, [("get_my_context", {})])

    def test_unsupported_or_sensitive_answers_do_not_get_format_repair(self):
        for text, code in (("已经正式中选。", "unsupported_personal_claim"), ("password=SYNTHETIC-SECRET", "sensitive_personal_content")):
            with self.subTest(code=code):
                self.executed.clear()
                result, _, model = self.run_model([final({"kind": "answer", "claims": [{"text": text, "evidence_ids": ["old"]}]})])
                self.assertEqual(result["code"], code)
                self.assertEqual(result["repair_attempts"], 0)
                self.assertEqual(len(model.calls), 1)

    def test_authorization_rechecked_before_repair_and_data_removed_on_revocation(self):
        def revoke(kind, fields):
            self.emit(kind, fields)
            if kind == "answer.repair" and fields.get("status") == "started":
                self.denied = True
        result, _, model = self.run_model([final({"kind": "unknown"}), final({"kind": "general_chat", "intent": "identity"})], emit=revoke)
        self.assertEqual(result["code"], "grant_revoked")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(result["validation_diagnostics"], [])

    def test_diagnostic_tool_is_local_bounded_and_citable_without_remote_run_id(self):
        def explain(messages, tools):
            value = json.loads(messages[-1]["content"])
            self.assertEqual(value["diagnostic"]["error_code"], "invalid_final_answer")
            self.assertFalse(value["diagnostic"]["thinking"])
            self.assertFalse(value["diagnostic"]["arbitrary_scripts_executed"])
            return final({"kind": "answer", "claims": [{"text": "上一轮已经查询记录，在最终答案格式校验时失败；没有执行任意终端脚本。", "evidence_ids": [value["evidence_id"]]}]})
        result, history, model = self.run_model([call("get_my_run_diagnostics"), explain], execution_context=self.previous)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertIn("get_my_run_diagnostics", {row["function"]["name"] for row in model.calls[0][1]})
        self.assertEqual(result["citations"][0]["kind"], "diagnostic")
        self.assertEqual(result["citations"][0]["diagnostic"]["run_id"], self.previous["run_id"])
        self.assertNotIn(self.previous["run_id"], json.dumps(history))
        self.assertEqual([kind for kind, _ in self.events], ["tool.start", "tool.end"])

    def test_no_prior_run_cannot_produce_diagnostic_evidence(self):
        result, history, model = self.run_model([call("get_my_run_diagnostics")])
        self.assertNotIn("get_my_run_diagnostics", {row["function"]["name"] for row in model.calls[0][1]})
        # The undeclared call is rejected before execution. A failed turn can
        # retain its genuine context proof, but must never create a prior-run
        # diagnostic proof or pretend that diagnostic execution happened.
        self.assertEqual((result["status"], result["code"]), ("failed", "tool_not_allowed"))
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertTrue(all(citation["kind"] == "context" for citation in result["citations"]))
        self.assertFalse(any("diagnostic" in citation for citation in result["citations"]))
        self.assertEqual([tool["name"] for tool in result["tool_results"]], ["get_my_context"])
        self.assertEqual((history, self.events), ([], []))
        self.assertEqual(len(model.calls), 1)

    def test_no_prior_run_can_safely_return_not_ready_without_fabricated_evidence(self):
        result, _, model = self.run_model([final({"kind": "not_ready", "reason": "insufficient_evidence"})])
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
        for value, detail in (([], "answer_not_object"), ({"kind": "answer", "claims": "NEVER-SEND"}, "claims_not_array"),
                ({"kind": "answer", "claims": []}, "claim_count_out_of_bounds"),
                ({"kind": "answer", "claims": [{"text": "NEVER-SEND", "evidence_ids": [], "extra": True}]}, "claim_shape_invalid"),
                ({"kind": "clarify", "question": "x" * 301}, "clarification_text_invalid")):
            with self.subTest(detail=detail):
                result, _, _ = self.run_model([final(value), final(value)])
                self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], detail)
                self.assertNotIn("NEVER-SEND", json.dumps(result["validation_diagnostics"]))


if __name__ == "__main__":
    unittest.main()
