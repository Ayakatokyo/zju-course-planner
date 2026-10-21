"""Common dialogue journeys over invented data; no network or school access."""
import json
import unittest

from course_assistant.personal_chat import PersonalChatService
from course_assistant.personal_dialogue import PersonalDialogueService, _course_plan_claim
from tests import test_live_source_dialogue as live_fixtures
from tests.test_personal_dialogue import ScriptedModel, call, final


class PersonalConversationTests(unittest.TestCase):
    def setUp(self):
        self.sources = live_fixtures.LiveSourceDialogueTests()
        self.sources.setUp()

    def run_dialogue(self, question, responses, history=()):
        model = ScriptedModel(responses)
        service = PersonalDialogueService(model, lambda: {"scope_key": "synthetic-only"}, self.sources.execute)
        response, _ = service.run(question, history=history, data_use="personal_retrieval")
        return response, model

    @staticmethod
    def explain(messages, tools):
        result = json.loads(messages[-1]["content"])
        return final({"kind": "answer", "claims": [{"text": item["text"], "evidence_ids": [item["evidence_id"]]}
                                                  for item in result["entries"]]})

    def test_explanation_keeps_previous_answer_but_never_replays_school_facts(self):
        state = {"turns": [{"message": "查成绩概况", "reply": "合成测试：成绩未知3条。",
                            "tool_results": [{"secret": "RAW-NOT-TRANSMITTED"}],
                            "reasoning_content": "THINKING-NOT-TRANSMITTED"}]}
        history = PersonalChatService._conversation_history(state)
        response, model = self.run_dialogue("这里的未知是什么意思？", [
            call("lookup_academic_terms", {"topics": ["unknown_grade"]}), self.explain], history)
        self.assertEqual(response["status"], "completed")
        self.assertIn("不等于0分", response["reply"])
        self.assertEqual([name for name, _ in self.sources.executed], ["get_my_context"])
        wire = json.dumps(model.calls[0][0], ensure_ascii=False)
        self.assertIn("合成测试：成绩未知3条", wire)
        self.assertIn("历史数据", wire)
        self.assertNotIn("RAW-NOT-TRANSMITTED", wire)
        self.assertNotIn("THINKING-NOT-TRANSMITTED", wire)
        self.assertEqual(response["citations"][0]["kind"], "academic_term")

    def test_irrelevant_capabilities_answer_gets_one_tool_enabled_correction(self):
        response, model = self.run_dialogue("未知成绩是什么意思", [
            final({"kind": "general_chat", "intent": "capabilities"}),
            call("lookup_academic_terms", {"topics": ["unknown_grade", "completion_status"]}), self.explain])
        self.assertEqual(response["status"], "completed")
        self.assertEqual(len(model.calls), 3)
        self.assertIn("lookup_academic_terms", [t["function"]["name"] for t in model.calls[1][1]])
        self.assertNotIn("我可以查询你的成绩", response["reply"])
        self.assertEqual(len(self.sources.executed), 1)

    def test_repeated_irrelevant_answer_is_failed_not_completed(self):
        wrong = final({"kind": "general_chat", "intent": "capabilities"})
        response, model = self.run_dialogue("未知成绩是什么意思", [wrong, wrong])
        self.assertEqual(response["status"], "failed")
        self.assertEqual(len(model.calls), 2)

    def test_all_terms_cannot_be_finished_with_major_history_summary(self):
        response, model = self.run_dialogue("每个学期的成绩都需要", [
            call("query_my_records", {"operation": "major_academic", "mode": "summary"}),
            final({"kind": "clarify", "question": "需要从哪个学年学期查到哪个学年学期？"})])
        self.assertEqual(response["model_decision"]["kind"], "clarify")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual([name for name, _ in self.sources.executed], ["get_my_context"])
        self.assertFalse(response["tool_results"][-1]["result"]["query_executed"])

    def test_history_is_bounded_in_pairs_and_retains_no_tools(self):
        state = {"turns": [{"message": "旧问" + str(i), "reply": "答" * 3001, "tool_results": [{"raw": "NEVER"}]}
                           for i in range(12)]}
        history = PersonalChatService._conversation_history(state)
        self.assertLessEqual(len(json.dumps(history, ensure_ascii=False).encode()), 24000)
        self.assertEqual([row["role"] for row in history], ["user", "assistant"] * (len(history) // 2))
        self.assertTrue(all(len(row["content"]) <= 3000 for row in history))
        self.assertNotIn("NEVER", json.dumps(history))

    def test_unknown_term_topic_cannot_be_injected_as_evidence(self):
        response, _ = self.run_dialogue("解释成绩", [call("lookup_academic_terms", {"topics": ["unknown_grade", "invented_policy"]})])
        self.assertEqual(response["status"], "failed")
        self.assertFalse(any(item["kind"] == "academic_term" for item in response["citations"]))

    def test_search_evidence_after_calculation_does_not_request_generation_again(self):
        proofs = {"search": {"kind": "course_data_plan", "tool_index": 1,
            "course_data_plan": {"action": "search", "target_term": "2026-2027-AW", "courses": []}}}
        claim = {"kind": "course_data_plan", "evidence_id": "search"}
        self.assertIn("可以继续", _course_plan_claim(claim, proofs)[0])
        proofs["generated"] = {"kind": "course_data_plan", "tool_index": 2,
            "course_data_plan": {"action": "generate", "target_term": "2026-2027-AW"}}
        self.assertNotIn("可以继续", _course_plan_claim(claim, proofs)[0])
