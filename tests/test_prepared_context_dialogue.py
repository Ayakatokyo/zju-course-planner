"""Bounded prepared facts and blocked-source diagnosis; invented data only."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_acquisition import recommendation_source_requests
from course_assistant.personal_dialogue import PersonalDialogueService
from course_assistant.personal_observations import source_grade_summary
from course_assistant.recommendation_readiness import assess_recommendation_readiness, source_receipt_diagnostics
from tests.test_personal_dialogue import ScriptedModel, call, final
from tests.test_recommendation_readiness import timetable_read


TERM = "2026-2027-AW"
TASK = {"recommendation_kind": "timetable", "target_term": TERM,
        "course_scope": {"kind": "topic", "queries": ["合成课程"]},
        "hard_preferences": [], "soft_preferences": []}


class PreparedContextDialogueTests(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.change_wrapper = None
        self.context = {"status": "available", "operations": [], "source_receipts": [],
            "api_operations": ["major_academic", "timetable", "exams"],
            "capabilities": {"live_record_query": True, "read_only_acquisition": True,
                "curriculum_local_search": True, "recommendation_input_check": True,
                "course_catalog_search": True}, "course_catalog": {"status": "ready"}}
        rows = [{"course_name_raw": "合成已修课程", "grade_raw": "80", "nominal_credits": "2",
                 "field_states": {"grade_raw": "known", "nominal_credits": "known"}}]
        coverage = {"captured_rows": 1, "reported_total": 1, "complete_within_scope": True, "has_more": False}
        self.major = {"status": "complete_within_scope", "operation": "major_academic", "target_term": "ALL-HISTORY",
            "data_source": "live", "coverage": coverage, "mode": "summary", "records": [],
            "matched_records": 1, "total_records": 1, "returned_records": 0,
            "source_grade_summary": source_grade_summary(rows, coverage),
            "source_alias": "PRIVATE-RAW-SOURCE", "observation_id": "PRIVATE-RAW-OBS"}
        self.curriculum = {"status": "pending_review", "scope": {"binding_status": "not_verified", "rules_status": "not_reviewed"},
            "matches": [{"title": "合成培养模块", "text": "合成培养模块要求见课程表，适用与完整规则待核实。",
                "page": 1, "reference": "PRIVATE-RAW-REFERENCE", "source_path": "/Users/PRIVATE-RAW-PATH"}],
            "raw_pdf": "PRIVATE-RAW-PDF", "password": "PRIVATE-RAW-PASSWORD"}
        self.timetable = timetable_read()
        self.timetable.update(target_term=TERM, matched_records=3, total_records=3, returned_records=3,
            observation_id="PRIVATE-RAW-OBS", source_alias="PRIVATE-RAW-SOURCE", mode="records")
        self.timetable["records"][0].update(password="PRIVATE-RAW-PASSWORD", owner_student_id="PRIVATE-RAW-OWNER",
            raw_row={"cookie": "PRIVATE-RAW-COOKIE"})
        self.receipts = [{"operation": "major_academic", "target_term": "ALL-HISTORY", "status": "complete_within_scope",
                          "ready": True, "data_source": "live"},
                         {"operation": "curriculum", "target_term": None, "status": "acquired_pending_review",
                          "ready": True, "data_source": "live"},
                         {"operation": "timetable", "target_term": TERM, "status": "partial_acquisition",
                          "ready": True, "data_source": "live",
                          "diagnostics": source_receipt_diagnostics("timetable", self.timetable)}]

    def wrapper(self, args):
        current = {**self.context, "source_receipts": deepcopy(self.receipts)}
        children = []
        for request in recommendation_source_requests(args):
            result = {"major_academic": self.major, "curriculum": self.curriculum,
                      "timetable": self.timetable}[request["operation"]]
            children.append({"name": request["tool"], "arguments": request["arguments"], "result": deepcopy(result)})
        value = {"status": "available", "data_kind": "personal_source", "check_executed": True,
            "readiness": assess_recommendation_readiness(current, recommendation_kind=args["recommendation_kind"],
                target_term=args["target_term"]), "prepared_sources": children}
        if self.change_wrapper:
            self.change_wrapper(value)
        return value

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        if name == "prepare_recommendation_inputs":
            return self.wrapper(args)
        if name == "query_my_records":
            return deepcopy(self.timetable)
        raise AssertionError("unexpected synthetic dispatch")

    def run_dialogue(self, responses, **kwargs):
        model = ScriptedModel(responses)
        response, messages = PersonalDialogueService(model,
            lambda: {"scope_key": "SYNTHETIC-SCOPE", "versions": {"input": "SYNTHETIC-v1"}}, self.execute).run(
                "核对合成排课所需资料", data_use="personal_retrieval", **kwargs)
        return response, messages, model

    @staticmethod
    def tool_values(messages):
        return [json.loads(row["content"]) for row in messages if row["role"] == "tool"]

    @staticmethod
    def context_value(messages):
        return json.loads(messages[0]["content"].rsplit("service_preflight get_my_context）：\n", 1)[1])

    @classmethod
    def prepared_answer(cls, messages, tools):
        prepared = next(row for row in reversed(cls.tool_values(messages)) if "prepared_sources" in row)
        return final({'kind': 'answer', 'text': '正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', 'evidence_ids': ([prepared['evidence_id']])})

    def test_prepare_exposes_current_bounded_facts_with_usable_citations_and_no_raw_private_fields(self):
        def answer(messages, tools):
            prepared = self.tool_values(messages)[-1]
            children = prepared["prepared_sources"]
            self.assertEqual([row["name"] for row in children], ["query_my_records", "search_my_curriculum", "query_my_records"])
            self.assertEqual(children[0]["result"]["records"], [])
            summary = children[0]["result"]["source_grade_summary"]
            self.assertEqual(summary["matched_records"], 1)
            self.assertEqual(len(children[2]["result"]["records"]), 3)
            self.assertFalse(prepared["readiness"]["can_generate"])
            self.assertEqual(prepared["readiness"]["sources"][3]["status"], "partial")
            return final({'kind': 'answer', 'text': '\n\n'.join(['正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', '本次匹配4条记录，纯数字成绩2条，成绩未知1条；简单均值85.00，不是GPA。', '合成培养片段仍需核对适用与完整规则。', '已取得本轮课表记录。']), 'evidence_ids': ([prepared['evidence_id']]) + ([summary['evidence_id']]) + ([children[1]['result']['matches'][0]['evidence_id']]) + ([children[2]['result']['records'][0]['evidence_id']])})
        response, messages, model = self.run_dialogue([call("prepare_recommendation_inputs", TASK), answer])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["task_progress"]["status"], "blocked")
        self.assertEqual({row["kind"] for row in response["citations"]}, {"recommendation_readiness", "summary", "curriculum", "record"})
        self.assertNotIn("PRIVATE-RAW", json.dumps(model.calls))
        self.assertNotIn("/Users/", json.dumps(model.calls))
        self.assertIn("接口未声明总数", response["reply"])
        self.assertIn("文本缺失不等于没有课时", response["reply"])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs"])

    def test_arbitrary_or_cross_scope_children_fail_and_earlier_child_proofs_are_rolled_back(self):
        for kind in ("arbitrary", "cross_scope", "result_scope", "duplicate"):
            with self.subTest(kind=kind):
                self.executed = []
                def change(value):
                    if kind == "arbitrary":
                        value["prepared_sources"][1]["name"] = "arbitrary_terminal"
                    elif kind == "cross_scope":
                        value["prepared_sources"][2]["arguments"]["target_term"] = "2026-2027-SS"
                    elif kind == "result_scope":
                        value["prepared_sources"][2]["result"]["target_term"] = "2026-2027-SS"
                    else:
                        value["prepared_sources"][1] = deepcopy(value["prepared_sources"][0])
                self.change_wrapper = change
                def stale_claim(messages, tools):
                    values = self.tool_values(messages)
                    self.assertEqual(values[-1]["status"], "unavailable")
                    self.assertEqual(values[-1]["code"], "invalid_tool_result")
                    context_id = self.context_value(messages)["evidence_id"]
                    stale = "S" + context_id[1:].rsplit("-", 1)[0] + "-3"
                    return final({'kind': 'answer', 'text': "此引用必须被回滚。", 'evidence_ids': [stale]})
                def context_answer(messages, tools):
                    reference = self.context_value(messages)["evidence_id"]
                    return final({'kind': 'answer', 'text': "本轮仅确认了可查询范围。", 'evidence_ids': [reference]})
                response, _, model = self.run_dialogue([call("prepare_recommendation_inputs", TASK), stale_claim, context_answer])
                self.assertEqual(response["status"], "completed", response)
                self.assertEqual(response["validation_diagnostics"][0]["failure_detail"], "evidence_not_current")
                self.assertEqual([row["kind"] for row in response["citations"]], ["context"])
                self.assertNotIn("PRIVATE-RAW", json.dumps(model.calls))

    def test_partial_stop_allows_only_same_term_timetable_diagnosis(self):
        def finish(messages, tools):
            prepared = next(row for row in self.tool_values(messages) if "prepared_sources" in row)
            latest = self.tool_values(messages)[-1]
            self.assertEqual({row["function"]["name"] for row in tools}, {"query_my_records", "finish_answer"})
            return final({'kind': 'answer', 'text': '\n\n'.join(['正式推荐尚未就绪；接口未声明总数，文本缺失不等于没有课时。', '已核对本轮取得的一条课表记录。']), 'evidence_ids': ([prepared['evidence_id']]) + ([latest['records'][0]['evidence_id']])})
        response, _, _ = self.run_dialogue([call("prepare_recommendation_inputs", TASK),
            call("query_my_records", {"operation": "timetable", "target_term": TERM, "limit": 1}, "diagnose"), finish])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs", "query_my_records"])
        self.assertEqual(response["task_progress"]["status"], "blocked")

    def test_partial_stop_rejects_candidates_and_other_term_before_dispatch(self):
        for name, args in (("search_course_catalog", {"query": "合成"}),
                           ("query_my_records", {"operation": "timetable", "target_term": "2026-2027-SS"}),
                           ("query_my_records", {"operation": "exams", "target_term": "2026-2027-A"})):
            with self.subTest(name=name, args=args):
                self.executed = []
                response, _, _ = self.run_dialogue([call("prepare_recommendation_inputs", TASK),
                    call(name, args, "disallowed"), self.prepared_answer])
                self.assertEqual(response["status"], "completed", response)
                self.assertEqual(response["repair_attempts"], 1)
                self.assertEqual([name for name, _ in self.executed], ["get_my_context", "prepare_recommendation_inputs"])

    def test_partial_stop_bounds_diagnosis_to_two_calls(self):
        query = {"operation": "timetable", "target_term": TERM, "limit": 1}
        response, _, model = self.run_dialogue([call("prepare_recommendation_inputs", TASK),
            call("query_my_records", query, "first"), call("query_my_records", query, "second"),
            call("query_my_records", query, "third-denied"), self.prepared_answer])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["repair_attempts"], 1)
        self.assertEqual([name for name, _ in self.executed],
                         ["get_my_context", "prepare_recommendation_inputs", "query_my_records", "query_my_records"])
        self.assertEqual([tool["function"]["name"] for tool in model.calls[-2][1]], ["finish_answer"])

    def test_previous_preparation_is_historical_and_needs_no_school_read(self):
        readiness = self.wrapper(TASK)["readiness"]
        diagnostic = {"run_id": "RUN-" + "a" * 32, "status": "completed", "thinking": True,
            "model": "scripted-only", "events": [], "previous_preparation": {
                "source": "previous_turn_preparation", "historical_only": True, "fresh_sources_required": True,
                "readiness": readiness}}
        def answer(messages, tools):
            value = self.tool_values(messages)[-1]["previous_preparation"]
            self.assertTrue(value["historical_only"])
            return final({'kind': 'answer', 'text': '上一轮准备检查中，接口未声明总数；本轮未重新读取教务资料。', 'evidence_ids': ([value['evidence_id']])})
        response, _, _ = self.run_dialogue([call("get_my_run_diagnostics", {}), answer], execution_context=diagnostic)
        self.assertEqual(response["status"], "completed", response)
        self.assertIn("上一轮准备检查", response["reply"])
        self.assertIn("本轮未重新读取教务资料", response["reply"])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])


if __name__ == "__main__":
    unittest.main()
