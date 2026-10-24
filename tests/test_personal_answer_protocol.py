"""Unified model prose, with source/plan checks kept separate from phrasing."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_dialogue import PersonalDialogueService, PersonalDialogueError, SYSTEM, _current_evidence_catalog
from tests import test_personal_dialogue as helpers

call, final = helpers.call, helpers.final


class PersonalAnswerProtocolTests(unittest.TestCase):
    def test_summary_and_record_display_context_keep_distinct_meaning(self):
        from course_assistant.personal_dialogue import _model_context
        for mode in ("summary", "records"):
            with self.subTest(mode=mode):
                value = {"operation": "major_academic", "record_source": {"scope_kind": "major_history"},
                         "mode": mode, "records": [], "matched_records": 12}
                messages = [{"role": "tool", "content": json.dumps(value)}]
                before = deepcopy(messages)
                projected = json.loads(_model_context(messages, {})[0]["content"])
                self.assertEqual(messages, before)
                self.assertEqual(projected["matched_records"], 12)
                guide = projected["answer_context"]
                if mode == "summary":
                    self.assertIn("没有请求明细", guide["record_detail"])
                    self.assertNotIn("presentation", guide)
                else:
                    self.assertNotIn("record_detail", guide)

    def answer(self, text, refs=(), kind="answer", **extra):
        return json.dumps({"kind": kind, "text": text, "evidence_ids": list(refs), **extra}, ensure_ascii=False)

    def test_one_prose_contract_mixes_all_current_source_types_without_templates(self):
        proofs = {key: {"evidence_id": key, "kind": kind, "tool_index": index, "text": "合成来源"}
                  for index, (key, kind) in enumerate([
                      ("R-current", "record"), ("S-current", "summary"),
                      ("E-current", "curriculum"), ("P-current", "recommendation_readiness")])}
        before = deepcopy(proofs)
        body = "两份来源支持这一方向；统计是来源分数的简单均值，培养正式适用仍未知。"
        reply, decision, citations = PersonalDialogueService._final(self.answer(body, proofs), proofs)
        self.assertEqual(reply, body)
        self.assertEqual(decision["answer_protocol"], "text-v1")
        self.assertEqual([row["evidence_id"] for row in citations], list(proofs))
        self.assertEqual(proofs, before)
        self.assertNotIn("evidence_analysis", decision)

    def test_plain_greeting_is_model_text_not_service_enum(self):
        reply, _, citations = PersonalDialogueService._final(self.answer("晚上好，想先了解哪方面？"), {})
        self.assertEqual(reply, "晚上好，想先了解哪方面？")
        self.assertEqual(citations, [])

    def test_clarification_and_blocked_use_same_text_and_references(self):
        _, decision, _ = PersonalDialogueService._final(
            self.answer("想看哪一学期？", kind="clarify", request_basis="pending_task"), {}, pending_clarification=True)
        self.assertEqual(decision["request_basis"], "pending_task")
        proofs = {"P-current": {"kind": "recommendation_readiness", "tool_index": 1}}
        reply, decision, _ = PersonalDialogueService._final(
            self.answer("这次课表来源读取不完整，暂不能可靠避开已有安排。", proofs, kind="not_ready"), proofs)
        self.assertEqual(decision["kind"], "not_ready")
        self.assertIn("读取不完整", reply)

    def test_old_answer_languages_are_not_active_fallbacks(self):
        for value in [{"kind": "answer", "claims": []}, {"kind": "general_chat", "intent": "greeting"},
                      {"kind": "answer", "text": "文本", "analysis": {}}, {"kind": "not_ready", "reason": "insufficient_evidence"}]:
            with self.subTest(value=value), self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._final(json.dumps(value), {})
        self.assertNotIn('"claims":', SYSTEM)
        self.assertNotIn('"sections":', SYSTEM)

    def test_unknown_or_missing_fact_references_do_not_pass(self):
        proofs = {"R-current": {"kind": "record", "tool_index": 1}}
        for refs in (["R-previous-turn"], []):
            with self.subTest(refs=refs), self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._final(self.answer("合成来源成绩为88。", refs), proofs)

    def test_current_plan_explanation_keeps_canonical_values_and_rejects_stale_revision(self):
        proofs = {"T-current": {"kind": "course_data_plan", "tool_index": 1,
            "course_data_plan": {"plan_ref": "PLN-synthetic", "revision": 1, "action": "inspect",
                "computation_summary": {"credit_target": {"minimum": "6"}, "options": [{"label": "A", "total_credits": "7"}]}}}}
        before = deepcopy(proofs)
        body = "至少6学分是下限，保存的A为7学分；这次仅解释，没有重算。"
        reply, _, citations = PersonalDialogueService._final(self.answer(body, proofs), proofs)
        self.assertEqual(reply, body)
        self.assertEqual(citations[0]["course_data_plan"], before["T-current"]["course_data_plan"])
        proofs["T-new"] = {"kind": "course_data_plan", "tool_index": 2,
            "course_data_plan": {"plan_ref": "PLN-synthetic", "revision": 2, "action": "revise"}}
        with self.assertRaises(PersonalDialogueError):
            PersonalDialogueService._final(self.answer(body, ["T-current"]), proofs)

    def test_sensitive_content_and_formal_actions_still_rejected(self):
        for body in ["密码：synthetic-secret", "已完成选课", "保证正式可选", "你已满足毕业要求"]:
            with self.subTest(body=body), self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._final(self.answer(body), {})

    def test_real_loop_returns_model_wording_and_only_actual_records(self):
        fixtures = helpers.PersonalDialogueTests("runTest")
        fixtures.setUp()
        body = "本次取得的合成算法来源成绩为88，修读完成状态仍未知。"
        def respond(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertNotIn("answer_claim", result)
            self.assertIn("有分数不能证明已修完成", result["answer_context"]["meaning"])
            self.assertIn("finish_answer", result["answer_context"]["delivery"])
            return final({"kind": "answer", "text": body, "evidence_ids": [result["records"][0]["evidence_id"]]})
        response, _, model = fixtures.run_service([
            call("query_my_records", {"operation": "academic", "target_term": "2099-2100-AW"}), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["reply"], body)
        self.assertEqual(response["citations"][0]["record"]["grade_raw"], "88")
        self.assertNotIn("answer_context", response["tool_results"][-1]["result"])
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(response["repair_attempts"], 0)

    def test_repair_reuses_sources_and_only_explains_the_single_final_shape(self):
        fixtures = helpers.PersonalDialogueTests("runTest")
        fixtures.setUp()
        def repaired(messages, tools):
            self.assertEqual([tool["function"]["name"] for tool in tools], ["finish_answer"])
            guide = messages[-1]["content"]
            self.assertNotIn('"claims":', guide)
            self.assertNotIn("NEVER-REPLAY-DRAFT", json.dumps(messages))
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertIn(result["records"][0]["evidence_id"], guide)
            self.assertIn("不能写‘已修’", guide)
            self.assertIn("不按前缀或序号猜测", guide)
            return final({"kind": "answer", "text": "本次合成来源成绩为88。", "evidence_ids": [result["records"][0]["evidence_id"]]})
        response, _, _ = fixtures.run_service([
            call("query_my_records", {"operation": "academic", "target_term": "2099-2100-AW"}),
            final({"kind": "answer", "claims": [{"text": "NEVER-REPLAY-DRAFT"}]}), repaired])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 1)
        self.assertEqual(sum(name == "query_my_records" for name, _ in fixtures.executed), 1)

    def test_repair_reference_catalog_excludes_superseded_plans_and_keeps_source_types(self):
        proofs = {
            "S-synthetic-1": {"kind": "summary", "tool_index": 0},
            "R-synthetic-2": {"kind": "record", "tool_index": 1},
            "T-old": {"kind": "course_data_plan", "tool_index": 2,
                "course_data_plan": {"plan_ref": "synthetic", "revision": 0, "action": "generate"}},
            "T-new": {"kind": "course_data_plan", "tool_index": 3,
                "course_data_plan": {"plan_ref": "synthetic", "revision": 1, "action": "revise"}}}
        before = deepcopy(proofs)
        self.assertEqual(_current_evidence_catalog(proofs), {
            "summary": ["S-synthetic-1"], "record": ["R-synthetic-2"], "course_data_plan": ["T-new"]})
        self.assertEqual(proofs, before)
        with self.assertRaises(PersonalDialogueError):
            PersonalDialogueService._final(self.answer("合成来源。", ["R-synthetic-1"]), proofs)

    def test_final_tool_delivers_prose_without_dispatching_or_extra_completion(self):
        fixtures = helpers.PersonalDialogueTests("runTest")
        fixtures.setUp()
        def respond(messages, tools):
            self.assertIn("finish_answer", [tool["function"]["name"] for tool in tools])
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            return call("finish_answer", {"kind": "answer", "text": "合成来源成绩为88，完成状态未知。",
                        "evidence_ids": [result["records"][0]["evidence_id"]]}, "final")
        response, history, model = fixtures.run_service([
            call("query_my_records", {"operation": "academic", "target_term": "2099-2100-AW"}), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 0)
        self.assertEqual(len(model.calls), 2)
        self.assertFalse(any(name == "finish_answer" for name, _ in fixtures.executed))
        self.assertFalse(history[-1].get("tool_calls"))

    def test_final_tool_does_not_allow_business_calls_in_same_batch(self):
        fixtures = helpers.PersonalDialogueTests("runTest")
        fixtures.setUp()
        combined = call("finish_answer", {"kind": "answer", "text": "你好。", "evidence_ids": []}, "final")
        combined["tool_calls"] += call("query_my_records", {"operation": "academic"}, "query")["tool_calls"]
        response, _, _ = fixtures.run_service([combined,
            call("finish_answer", {"kind": "answer", "text": "你好。", "evidence_ids": []}, "repaired")])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 1)
        self.assertFalse(any(name == "query_my_records" for name, _ in fixtures.executed))

    def test_final_tool_still_rejects_foreign_references_and_sensitive_claims(self):
        for body, refs in [("合成成绩为88。", ["R-foreign"]), ("已完成选课。", [])]:
            with self.subTest(body=body):
                fixtures = helpers.PersonalDialogueTests("runTest")
                fixtures.setUp()
                response, _, _ = fixtures.run_service([
                    call("finish_answer", {"kind": "answer", "text": body, "evidence_ids": refs}, "first"),
                    call("finish_answer", {"kind": "answer", "text": body, "evidence_ids": refs}, "second")])
                self.assertEqual(response["status"], "failed")


if __name__ == "__main__":
    unittest.main()
