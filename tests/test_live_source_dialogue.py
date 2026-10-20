"""Synthetic protocol checks for live-source routing and bounded evidence.

No school endpoints, credentials, browser sessions or remote models are used.
The double returns invented values and lets tests inspect the exact model wire.
"""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_dialogue import PersonalDialogueService
from course_assistant.personal_observations import source_grade_summary
from tests.test_personal_dialogue import ScriptedModel, call, final


class LiveSourceDialogueTests(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.context = {"status": "available", "cohort": 2099, "major_name": "合成专业",
            "operations": [], "api_operations": ["academic", "major_academic", "timetable", "exams"],
            "capabilities": {"live_record_query": True, "curriculum_available": True, "local_course_catalog": True}}
        self.rows = [self.record(index) for index in range(40)]
        self.raw_cursor = "SYNTHETIC-SIGNED-CURSOR.NOT-FOR-PROVIDER.signature"
        self.record_handler = None
        self.curriculum = {"status": "pending_review", "scope": {"binding_status": "not_verified", "rules_status": "not_reviewed"},
            "matches": [{"title": "合成培养模块", "text": "合成专业模块要求至少6学分。", "page": 2,
                "review_status": "unreviewed", "model_context": [{"page": 2, "text": "合成专业模块，以下课程表属于该模块。", "context_role": "module_heading"}],
                "required_context": [], "reference": "PRIVATE-SYNTHETIC-CURRICULUM-REF"}]}

    @staticmethod
    def record(index):
        return {"course_name_raw": f"合成课程{index}", "course_code_raw": f"SYN{index:04d}M", "grade_raw": "80",
            "grade_points_raw": "3.7", "nominal_credits": "2", "academic_year_raw": "2099-2100",
            "term_raw": "1", "completion_status_raw": None,
            "field_states": {"course_name_raw": "known", "course_code_raw": "known", "grade_raw": "known",
                "grade_points_raw": "known", "nominal_credits": "known", "academic_year_raw": "known",
                "term_raw": "known", "completion_status_raw": "unknown"}}

    def result(self, args, *, offset=0, count=15):
        coverage = {"captured_rows": len(self.rows), "reported_total": len(self.rows), "has_more": False, "complete_within_scope": True}
        summary = source_grade_summary(self.rows, coverage)
        selected = [] if args.get("mode") == "summary" else deepcopy(self.rows[offset:offset + count])
        return {"status": "complete_within_scope", "operation": args["operation"], "target_term": args.get("target_term", "ALL-HISTORY"),
            "data_source": "live", "observation_id": "OBS-PRIVATE-SYNTHETIC", "source_alias": "SOURCE-PRIVATE-SYNTHETIC",
            "captured_at": 123, "checked_at": 124, "coverage": coverage, "records": selected,
            "total_records": len(self.rows), "matched_records": len(self.rows), "returned_records": len(selected),
            "offset": offset, "mode": args.get("mode", "records"), "source_grade_summary": summary,
            "next_cursor": self.raw_cursor if args.get("mode") != "summary" and offset + count < len(self.rows) else None}

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        if name == "query_my_records":
            return self.record_handler(args) if self.record_handler else self.result(args)
        if name == "search_my_curriculum":
            return deepcopy(self.curriculum)
        if name == "search_course_catalog":
            return {"status": "available", "data_kind": "local_course_reference", "matched_records": 1,
                "returned_records": 1, "has_more": False, "source_description": "合成本地课程名称与代码参考；不保证当期开课和本人可选。",
                "records": [{"course_name": "合成算法", "course_code": "SYN1001M", "source_version": "synthetic-catalog-v1"}]}
        raise AssertionError("unexpected synthetic tool")

    def run_protocol(self, responses, question="检查合成来源"):
        model = ScriptedModel(responses)
        service = PersonalDialogueService(model, lambda: {"scope_key": "SYNTHETIC-SCOPE", "versions": {"input": "v1"}}, self.execute)
        response, messages = service.run(question, data_use="personal_retrieval")
        return response, messages, model

    @staticmethod
    def last_tool(messages):
        return json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))

    @staticmethod
    def answer(text, proof):
        return final({"kind": "answer", "claims": [{"text": text, "evidence_ids": [proof]}]})

    @staticmethod
    def summary_answer(proof, sections=("counts", "numeric_grades")):
        return final({"kind": "answer", "claims": [{"kind": "source_summary", "evidence_id": proof,
                                                    "sections": list(sections)}]})

    def test_summary_mode_answers_with_full_aggregate_and_no_record_page(self):
        args = {"operation": "major_academic", "mode": "summary", "query": "", "limit": 1}
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(remote["records"], [])
            self.assertEqual(remote["returned_records"], 0)
            self.assertEqual(remote["source_grade_summary"]["matched_records"], 40)
            self.assertTrue(remote["retrieval"]["summary_only"])
            self.assertFalse(remote["retrieval"]["has_more"])
            self.assertNotIn("next_page_token", remote)
            return self.summary_answer(remote["source_grade_summary"]["evidence_id"])
        response, _, _ = self.run_protocol([call("query_my_records", args), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual([citation["kind"] for citation in response["citations"]], ["summary"])
        self.assertEqual(self.executed[-1], ("query_my_records", args))
        self.assertIn("本次匹配40条记录", response["reply"])
        self.assertIn("简单不加权均值为80.00", response["reply"])
        self.assertIn("不是GPA", response["reply"])

    def test_local_course_reference_is_citable_and_retains_its_distinct_status(self):
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(remote["data_kind"], "local_course_reference")
            course = remote["records"][0]
            self.assertFalse(course["current_offering_verified"])
            self.assertFalse(course["personal_eligibility_verified"])
            self.assertIn("current_offering_not_verified", remote["unknown"])
            return self.answer("本地课程参考包含合成算法SYN1001M；当期开课和本人资格尚未核验。", course["evidence_id"])
        response, messages, _ = self.run_protocol([call("search_course_catalog", {"query": "算法"}), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["citations"][0]["kind"], "course_reference")
        self.assertFalse(response["citations"][0]["course"]["current_offering_verified"])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "search_course_catalog"])
        self.assertNotIn("OBS-PRIVATE", json.dumps(messages))

    def test_records_then_curriculum_keeps_five_hits_in_reserved_envelope(self):
        for row in self.rows:
            row["course_name_raw"] += "合成扩展课程描述" * 23
        self.curriculum["matches"] = [{"title": f"合成模块{index}", "text": "合成条款与模块上下文。" * 50,
            "page": index + 1, "review_status": "unreviewed", "required_context": []} for index in range(5)]
        def page(args):
            return self.result(args, offset=15 if args.get("cursor") else 0)
        self.record_handler = page
        args = {"operation": "major_academic", "mode": "records", "query": "", "limit": 15}
        def following(messages, tools):
            remote = self.last_tool(messages)
            return call("query_my_records", {**args, "page_token": remote["next_page_token"]}, "second-page")
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(len(remote["matches"]), 5)
            self.assertEqual(remote["projection_omitted_matches"], 0)
            return self.answer("合成培养条款仍可核对，规则与本人适用尚待审核。", remote["matches"][0]["evidence_id"])
        response, messages, _ = self.run_protocol([call("query_my_records", args), following,
            call("search_my_curriculum", {"query": "模块", "limit": 5}, "curriculum"), respond])
        self.assertEqual(response["status"], "completed", response)
        record_tools = [result for result in response["tool_results"] if result["name"] == "query_my_records"]
        self.assertEqual(len(record_tools), 2)
        self.assertEqual(sum(len(tool["result"]["records"]) for tool in record_tools), 30)
        self.assertGreater(sum(len(json.dumps(tool["result"]["records"], ensure_ascii=False)) for tool in record_tools), 6000)
        self.assertEqual(len(self.last_tool(messages)["matches"]), 5)

    def test_opaque_next_page_really_resumes_and_raw_signed_cursor_never_leaves_service(self):
        args = {"operation": "major_academic", "mode": "records", "query": "", "limit": 15}
        self.record_handler = lambda request: self.result(request, offset=15 if request.get("cursor") else 0)
        def following(messages, tools):
            remote = self.last_tool(messages)
            self.assertRegex(remote["next_page_token"], r"^PAGE-[a-f0-9]{8}-1$")
            self.assertNotIn("next_cursor", remote)
            return call("query_my_records", {**args, "page_token": remote["next_page_token"]}, "page-two")
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(remote["records"][0]["course_name_raw"], "合成课程15")
            return self.answer("续取的合成记录来自下一页。", remote["records"][0]["evidence_id"])
        response, messages, model = self.run_protocol([call("query_my_records", args), following, respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(self.executed[-1], ("query_my_records", {**args, "cursor": self.raw_cursor}))
        self.assertEqual(response["citations"][0]["record_index"], 15)
        wire = json.dumps(messages, ensure_ascii=False) + json.dumps(model.calls, ensure_ascii=False)
        self.assertNotIn(self.raw_cursor, wire)
        self.assertNotIn("next_cursor", wire)
        self.assertNotIn("OBS-PRIVATE-SYNTHETIC", wire)

    def test_continuation_cannot_change_query_term_mode_or_limit(self):
        args = {"operation": "academic", "target_term": "2099-2100-AW", "mode": "records", "query": "", "limit": 15}
        for patch in ({"query": "另一个条件"}, {"target_term": "2099-2100-SS"}, {"limit": 10}):
            with self.subTest(patch=patch):
                self.executed = []
                def invalid(messages, tools):
                    return call("query_my_records", {**args, **patch, "page_token": self.last_tool(messages)["next_page_token"]}, "changed-page")
                response, messages, _ = self.run_protocol([call("query_my_records", args), invalid,
                    final({"kind": "not_ready", "reason": "insufficient_evidence"})])
                self.assertEqual(sum(name == "query_my_records" for name, _ in self.executed), 1)
                self.assertTrue(response["tool_results"][-1]["error"])
                self.assertEqual(response["tool_results"][-1]["result"]["status"], "unavailable")
                self.assertNotIn(self.raw_cursor, json.dumps(messages, ensure_ascii=False))

    def test_continuation_cannot_switch_to_summary_or_accept_raw_cursor_arguments(self):
        args = {"operation": "major_academic", "mode": "records", "query": "", "limit": 15}
        for change in ("summary", "raw_cursor"):
            with self.subTest(change=change):
                self.executed = []
                def invalid(messages, tools):
                    token = self.last_tool(messages)["next_page_token"]
                    patch = {"mode": "summary", "page_token": token} if change == "summary" else {"cursor": self.raw_cursor}
                    return call("query_my_records", {**args, **patch}, "invalid-continuation")
                response, _, _ = self.run_protocol([call("query_my_records", args), invalid])
                self.assertEqual(response["status"], "failed")
                self.assertEqual(response["code"], "invalid_tool_arguments")
                self.assertEqual(sum(name == "query_my_records" for name, _ in self.executed), 1)

    def test_forged_opaque_page_token_cannot_select_a_service_cursor(self):
        args = {"operation": "major_academic", "mode": "records", "query": "", "limit": 15,
                "page_token": "PAGE-1234abcd-999"}
        response, messages, _ = self.run_protocol([call("query_my_records", args),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])
        self.assertTrue(response["tool_results"][-1]["error"])
        self.assertNotIn(self.raw_cursor, json.dumps(messages, ensure_ascii=False))

    def test_explicit_unread_term_requires_both_live_query_and_api_operation_capability(self):
        args = {"operation": "academic", "target_term": "2098-2099-SS", "mode": "summary"}
        def respond(messages, tools):
            remote = self.last_tool(messages)
            return self.summary_answer(remote["source_grade_summary"]["evidence_id"], ("counts",))
        response, _, _ = self.run_protocol([call("query_my_records", args), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(self.executed[-1][1]["target_term"], "2098-2099-SS")
        self.assertIn("2098-2099学年春夏学期成绩来源", response["reply"])
        self.context["api_operations"] = []
        self.executed = []
        rejected, _, _ = self.run_protocol([call("query_my_records", args)])
        self.assertEqual(rejected["status"], "failed")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])
        self.context["api_operations"] = ["academic"]
        self.context["capabilities"]["live_record_query"] = False
        self.executed = []
        blocked, _, _ = self.run_protocol([call("query_my_records", args),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])
        self.assertEqual((blocked["status"], blocked["code"]), ("failed", "invalid_tool_arguments"))

    def test_legacy_context_cannot_invent_unread_term_from_cached_operation(self):
        self.context.pop("api_operations")
        self.context["capabilities"].pop("live_record_query")
        self.context["operations"] = [{"operation": "academic", "target_term": "2099-2100-AW", "coverage": {}}]
        response, _, _ = self.run_protocol([call("query_my_records", {"operation": "academic", "target_term": "2098-2099-SS"})])
        self.assertEqual(response["status"], "failed")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])

    def test_source_grade_points_survive_projection_without_becoming_official_gpa(self):
        self.rows = self.rows[:1]
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(remote["records"][0]["grade_points_raw"], "3.7")
            self.assertFalse(remote["source_grade_summary"]["is_gpa"])
            self.assertFalse(remote["source_grade_summary"]["academic_recognition_verified"])
            return self.answer("该合成记录来源绩点字段为3.7，不能据此认定正式GPA。", remote["records"][0]["evidence_id"])
        response, _, _ = self.run_protocol([call("query_my_records", {"operation": "major_academic", "mode": "records"}), respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(response["citations"][0]["record"]["grade_points_raw"], "3.7")
        self.assertIn("不能据此认定正式GPA", response["reply"])

    def test_projection_omission_stops_continuation_instead_of_skipping_unsent_rows(self):
        for row in self.rows:
            row["course_name_raw"] += "合" * 480
        self.record_handler = lambda request: self.result(request, offset=15 if request.get("cursor") else 0)
        args = {"operation": "major_academic", "mode": "records", "query": "", "limit": 15}
        def following(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(remote["projection_omitted_rows"], 0)
            return call("query_my_records", {**args, "page_token": remote["next_page_token"]}, "projection-page-two")
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertGreater(remote["projection_omitted_rows"], 0)
            self.assertTrue(remote["retrieval"]["has_more"])
            self.assertFalse(remote["retrieval"]["complete_within_loaded_snapshot"])
            self.assertNotIn("next_page_token", remote)
            self.assertNotIn("next_cursor", remote)
            return self.answer("当前投影还不完整，只能说明已返回的合成记录。", remote["records"][0]["evidence_id"])
        response, _, _ = self.run_protocol([call("query_my_records", args), following, respond])
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual(sum(name == "query_my_records" for name, _ in self.executed), 2)

    def test_wire_state_compaction_preserves_explicit_unknowns_and_full_local_proof(self):
        self.rows = self.rows[:1]
        def respond(messages, tools):
            remote = self.last_tool(messages)
            row = remote["records"][0]
            self.assertNotIn("grade_raw", row["field_states"])
            self.assertEqual(row["grade_raw"], "80")
            self.assertEqual(row["field_states"]["completion_status_raw"], "unknown")
            self.assertIsNone(row["completion_status_raw"])
            return self.answer("合成分数原值80，完成状态未知。", row["evidence_id"])
        response, _, _ = self.run_protocol([call("query_my_records", {"operation": "major_academic", "mode": "records"}), respond])
        self.assertEqual(response["status"], "completed", response)
        states = response["citations"][0]["record"]["field_states"]
        self.assertEqual(states["grade_raw"], "known")
        self.assertEqual(states["completion_status_raw"], "unknown")

    def test_old_history_evidence_id_cannot_be_used_as_current_turn_proof(self):
        old = "R-old-synthetic-turn-1"
        history = [{"role": "user", "content": "上轮合成查询"},
            {"role": "tool", "tool_call_id": "old", "content": json.dumps({"evidence_id": old, "grade_raw": "99"})}]
        model = ScriptedModel([self.answer("旧合成记录99。", old), self.answer("旧合成记录99。", old)])
        service = PersonalDialogueService(model, lambda: {"scope_key": "SYNTHETIC-SCOPE"}, self.execute)
        response, _ = service.run("继续核对", data_use="personal_retrieval", history=history)
        self.assertEqual((response["status"], response["code"]), ("failed", "unverified_personal_answer"))
        self.assertNotIn(old, [citation["evidence_id"] for citation in response["citations"]])

    def test_all_reserved_curriculum_hits_survive_six_prior_scope_queries(self):
        for row in self.rows:
            row["course_name_raw"] += "合成扩展课程描述" * 23
        self.curriculum["matches"] = [{"title": f"模块{index}", "text": "合成条款。" * 120,
            "page": index + 1, "required_context": [],
            "model_context": [{"page": index + 1, "text": "合成标题与必要附注。" * 20}]} for index in range(5)]
        def respond(messages, tools):
            remote = self.last_tool(messages)
            self.assertEqual(len(remote["matches"]), 5)
            self.assertEqual(remote["projection_omitted_matches"], 0)
            return self.answer("合成培养条款仍完整保留在其独立预算内，规则待审核。", remote["matches"][0]["evidence_id"])
        responses = [call("query_my_records", {"operation": "academic", "target_term": f"{year}-{year + 1}-AW", "mode": "records"},
                          f"scope-{year}") for year in range(2094, 2100)]
        response, _, _ = self.run_protocol(responses + [call("search_my_curriculum", {"query": "专业模块"}, "last-curriculum"), respond])
        self.assertEqual(response["status"], "completed", response)
        record_tools = [item for item in response["tool_results"] if item["name"] == "query_my_records"]
        self.assertEqual(len(record_tools), 6)
        self.assertTrue(any(item["result"]["projection_omitted_rows"] for item in record_tools))
        self.assertEqual(len(response["tool_results"][-1]["result"]["matches"]), 5)

    def test_prior_user_only_intents_are_closed_system_context_not_additional_user_tasks(self):
        previous = [{"role": "user", "content": "之前查询合成成绩"},
                    {"role": "user", "content": "之后查询合成培养要求"}]
        current = "现在只查询本地课程目录"
        model = ScriptedModel([final({"kind": "general_chat", "intent": "capabilities"})])
        service = PersonalDialogueService(model, lambda: {"scope_key": "SYNTHETIC-SCOPE"}, self.execute)
        response, persisted = service.run(current, data_use="personal_retrieval", history=previous)
        self.assertEqual(response["status"], "completed", response)
        wire = model.calls[0][0]
        self.assertEqual([row["content"] for row in wire if row["role"] == "user"], [current])
        system = next(row["content"] for row in wire if row["role"] == "system")
        self.assertIn("已结束历史意图", system)
        self.assertIn("不是待办", system)
        for item in previous:
            self.assertIn(item["content"], system)
        self.assertEqual([row["content"] for row in persisted if row["role"] == "user"], [current])
        self.assertEqual([name for name, _ in self.executed], ["get_my_context"])

    def test_catalog_only_turn_declares_only_context_catalog_and_optional_diagnostics(self):
        diagnostic = {"run_id": "RUN-" + "a" * 32, "status": "completed", "thinking": False,
                      "model": "synthetic-model", "events": []}
        model = ScriptedModel([call("search_course_catalog", {"query": "合成算法"}),
            lambda messages, tools: self.answer("本地课程目录有合成算法；是否当期开课仍未知。",
                                              self.last_tool(messages)["records"][0]["evidence_id"])])
        service = PersonalDialogueService(model, lambda: {"scope_key": "SYNTHETIC-SCOPE"}, self.execute)
        response, _ = service.run("本地课程目录里有合成算法吗", data_use="personal_retrieval", execution_context=diagnostic)
        self.assertEqual(response["status"], "completed", response)
        self.assertEqual({tool["function"]["name"] for tool in model.calls[0][1]},
                         {"get_my_context", "search_course_catalog", "get_my_run_diagnostics"})
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "search_course_catalog"])

    def test_catalog_only_turn_rejects_model_attempts_to_read_personal_sources(self):
        for name, args in (("query_my_records", {"operation": "major_academic", "mode": "summary"}),
                           ("search_my_curriculum", {"query": "专业选修"})):
            for repair, succeeds in ((final({"kind": "not_ready", "reason": "insufficient_evidence"}), True),
                                     (call(name, args), False)):
                with self.subTest(tool=name, repair_succeeds=succeeds):
                    self.executed = []
                    response, history, model = self.run_protocol([call(name, args), repair],
                        question="只查本地课程目录中的合成算法")
                    if succeeds:
                        self.assertEqual(response["status"], "completed")
                        self.assertEqual(response["model_decision"]["kind"], "not_ready")
                        self.assertEqual(response["citations"], [])
                    else:
                        self.assertEqual((response["status"], response["code"]), ("failed", "tool_not_allowed"))
                        self.assertEqual(history, [])
                    self.assertEqual(response["repair_attempts"], 1)
                    self.assertEqual(len(model.calls), 2)
                    self.assertEqual(model.calls[-1][1], [])
                    self.assertEqual([tool_name for tool_name, _ in self.executed], ["get_my_context"])
                    self.assertEqual([row["name"] for row in response["tool_results"]], ["get_my_context"])
                    self.assertTrue(all(c["kind"] == "context" for c in response["citations"]))
                    self.assertEqual({tool["function"]["name"] for tool in model.calls[0][1]},
                                     {"get_my_context", "search_course_catalog"})
                    self.assertEqual(response["validation_diagnostics"][0]["failure_detail"], "undeclared_tool_call")
                    retained = json.dumps([model.calls[-1][0], history, response["validation_diagnostics"]],
                        ensure_ascii=False)
                    self.assertNotIn("PRIVATE-SYNTHETIC-CURRICULUM-REF", retained)
                    self.assertNotIn("OBS-PRIVATE-SYNTHETIC", retained)
                    self.assertNotIn("合成课程0", retained)


if __name__ == "__main__":
    unittest.main()
