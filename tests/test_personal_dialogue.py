"""Personal dialogue protocol exercised with synthetic facts and labeled doubles."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from course_assistant.model_client import ModelError
from course_assistant.personal_dialogue import PersonalDialogueError, PersonalDialogueService
from course_assistant.personal_observations import source_grade_summary


def call(name, arguments=None, identifier="synthetic-call"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}]}


def final(value):
    return {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)}


def answer_from_last_tool(messages, tools):
    result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
    if result.get("records"):
        proof = result["records"][0]["evidence_id"]
        text = "本次已读来源中，合成算法的成绩原值为88；这仅是该记录的来源值。"
    elif result.get("matches"):
        proof = result["matches"][0]["evidence_id"]
        text = "这份取得文件的片段写明专业模块至少6学分，仍需核对上下文及本人适用。"
    else:
        proof = result["evidence_id"]
        text = "本机已读范围包含合成专业的成绩记录。"
    return final({"kind": "answer", "claims": [{"text": text, "evidence_ids": [proof]}]})


class ScriptedModel:
    """No remote model: deterministic protocol responses over synthetic tool data."""
    def __init__(self, responses, configured=True):
        self.responses, self.calls, self.configured = list(responses), [], configured

    def status(self):
        return {"configured": self.configured, "remote": True,
                "provider": "SYNTHETIC TEST DOUBLE - NO MODEL", "model": "scripted-only"}

    def complete(self, messages, tools):
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value(messages, tools) if callable(value) else value


class PersonalDialogueTests(unittest.TestCase):
    def setUp(self):
        self.scope = "SYNTHETIC-OWNER-A:personal_school_client:session-1:generation-1"
        self.versions = {"records": "synthetic-v1", "curriculum": "synthetic-v1"}
        self.denied = False
        self.executed = []
        self.context = {"status": "ok", "cohort": 2099, "major_name": "合成专业",
            "name": "NEVER-SEND-STUDENT-NAME", "account": "NEVER-SEND-ACCOUNT",
            "owner_student_id": "NEVER-SEND-OWNER",
            "operations": [{"operation": "academic", "target_term": "2099-2100-AW",
                "coverage": {"captured_rows": 60, "reported_total": 75, "has_more": True}},
                {"operation": "major_academic", "target_term": "ALL-HISTORY", "coverage": {"captured_rows": 60}}],
            "capabilities": {"curriculum_available": True, "real_recommendation": False}}
        self.records = {"operation": "academic", "status": "partial_acquisition", "target_term": "2099-2100-AW",
            "data_source": "live",
            "source_alias": "PLAN-NEVER-SEND-SOURCE", "observation_id": "OBS-NEVER-SEND-SNAPSHOT",
            "coverage": {"captured_rows": 60, "reported_total": 75, "has_more": True, "complete_within_scope": False},
            "returned_records": 1, "matched_records": 2, "total_records": 60,
            "records": [{"course_name_raw": "合成算法", "course_code_raw": "SYN1001M", "grade_raw": "88",
                "nominal_credits": "3", "completion_status_raw": None,
                "field_states": {"completion_status_raw": "unknown"}, "source_subject_id": "NEVER-SEND-ID",
                "password": "NEVER-SEND-KEY", "profile": {"name": "NEVER-SEND-FULL-PROFILE"}}]}
        self.evidence = {"status": "pending_review", "coverage": {"physical_pages": 4},
            "scope": {"binding_status": "not_verified", "rules_status": "not_reviewed", "owner_student_id": "NEVER-SEND-OWNER"},
            "matches": [{"reference": "PERSONAL-REF-" + "A" * 43, "source_alias": "PLAN-NEVER-SEND-SOURCE",
                "title": "合成培养模块", "text": "合成培养方案专业模块至少6学分。", "page": 2,
                "review_status": "unreviewed", "locator": {"physical_page": 2},
                "required_context": [{"page": 1, "text": "NEVER-SEND-WHOLE-PDF-PAGE"}]}]}

    def authorize(self):
        if self.denied:
            raise PersonalDialogueError("grant_revoked")
        return {"scope_key": self.scope, "versions": deepcopy(self.versions)}

    def execute(self, name, args):
        self.executed.append((name, deepcopy(args)))
        if name == "get_my_context":
            return deepcopy(self.context)
        if name == "query_my_records":
            return deepcopy(self.records)
        return deepcopy(self.evidence)

    def service(self, responses=(), **kwargs):
        model = ScriptedModel(responses, kwargs.pop("configured", True))
        return PersonalDialogueService(model, kwargs.pop("authorize", self.authorize),
            kwargs.pop("execute_tool", self.execute), **kwargs), model

    def run_service(self, responses, **kwargs):
        service, model = self.service(responses, **kwargs)
        result, messages = service.run("查询本人合成来源记录", data_use="personal_retrieval")
        return result, messages, model

    def test_real_complete_protocol_uses_tools_and_current_record_evidence(self):
        result, messages, model = self.run_service([
            call("get_my_context"), call("query_my_records", {"operation": "academic", "target_term": "2099-2100-AW", "query": "算法"}, "records"), answer_from_last_tool])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data_kind"], "personal_source")
        self.assertTrue(result["model_used"])
        self.assertEqual(len(model.calls), 3)
        self.assertIn("88", result["reply"])
        self.assertIn("[1]", result["reply"])
        self.assertEqual(result["citations"][0]["kind"], "record")
        self.assertEqual(result["citations"][0]["record"]["grade_raw"], "88")
        self.assertEqual(result["citations"][0]["operation"], "academic")
        self.assertEqual(result["citations"][0]["data_source"], "live")
        self.assertEqual(result["citations"][0]["text"], "")
        self.assertEqual(result["citations"][0]["citation_number"], 1)
        self.assertNotIn(result["citations"][0]["evidence_id"], result["reply"])
        self.assertEqual(result["tool_results"][-1]["result"]["coverage"]["captured_rows"], 60)
        self.assertEqual(result["tool_results"][-1]["result"]["data_source"], "live")
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("NEVER-SEND", serialized)
        self.assertNotIn(self.scope, serialized)
        self.assertNotIn("source_alias", serialized)
        self.assertEqual({row["function"]["name"] for row in model.calls[0][1]}, {"get_my_context", "query_my_records", "search_my_curriculum"})

    def test_evidence_sends_related_paragraph_not_private_references_or_whole_pages(self):
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "专业模块", "limit": 5}), answer_from_last_tool])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["citations"][0]["page"], 2)
        self.assertEqual(result["citations"][0]["reference"], self.evidence["matches"][0]["reference"])
        self.assertEqual(result["citations"][0]["required_context"], self.evidence["matches"][0]["required_context"])
        self.assertEqual(result["citations"][0]["binding_status"], "not_verified")
        self.assertEqual(result["citations"][0]["rules_status"], "not_reviewed")
        self.assertEqual(result["citations"][0]["locator"], {"physical_page": 2})
        self.assertEqual(result["tool_results"][-1]["result"]["matches"][0]["required_context"], self.evidence["matches"][0]["required_context"])
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertIn("至少6学分", serialized)
        self.assertIn("required_context_not_transmitted", serialized)
        self.assertNotIn("PERSONAL-REF", serialized)
        self.assertNotIn("NEVER-SEND", serialized)
        self.assertIn("unreviewed", serialized)

    def test_numeric_display_citations_keep_actual_current_ids_only_in_internal_decision(self):
        def combined(messages, tools):
            results = [json.loads(row["content"]) for row in messages if row["role"] == "tool"]
            context_id, record_id = results[0]["evidence_id"], results[1]["records"][0]["evidence_id"]
            return final({"kind": "answer", "claims": [
                {"text": "成绩来源原值88，来源学分不等于已获学分。", "evidence_ids": [record_id, context_id]},
                {"text": "不能判断毕业资格，也未用于推荐。", "evidence_ids": [context_id, record_id]}]})
        result, _, _ = self.run_service([call("get_my_context"), call("query_my_records", {"operation": "academic"}, "r"), combined])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["reply"].endswith("[2] [1]"))
        self.assertIn("[1] [2]", result["reply"])
        self.assertIn("来源学分不等于已获学分", result["reply"])
        self.assertEqual([item["label"] for item in result["citations"]], ["[1]", "[2]"])
        self.assertEqual([item["kind"] for item in result["citations"]], ["record", "context"])
        self.assertEqual(result["citations"][1]["context"]["major_name"], "合成专业")
        self.assertEqual(result["citations"][1]["text"], "")
        self.assertEqual(result["model_decision"]["evidence_ids"], [item["evidence_id"] for item in result["citations"]])
        for item in result["citations"]:
            self.assertNotIn(item["evidence_id"], result["reply"])

    def test_local_page_context_retained_whole_until_explicit_local_budget(self):
        page = "合成必要上下文，包含表头和附注。" * 300
        self.evidence["matches"][0]["required_context"] = [{"page": 2, "text": page, "review_status": "unreviewed"}]
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "模块"}), answer_from_last_tool])
        proof = result["citations"][0]
        self.assertEqual(proof["required_context"][0]["text"], page)
        self.assertFalse(proof["context_omitted"])
        self.assertNotIn(page, json.dumps(messages, ensure_ascii=False))
        self.evidence["matches"][0]["required_context"][0]["text"] = page * 20
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "模块"}), answer_from_last_tool])
        proof = result["citations"][0]
        self.assertTrue(proof["context_omitted"])
        self.assertEqual(proof["required_context"], [])
        self.assertEqual(proof["context_omitted_reason"], "local_context_budget")
        self.assertTrue(result["tool_results"][-1]["result"]["matches"][0]["context_omitted"])

    def test_each_turn_requires_exact_explicit_data_use_before_model_or_tools(self):
        service, model = self.service([])
        for consent in (None, False, True, "synthetic", "personal_retrieval "):
            with self.assertRaises(PersonalDialogueError) as caught:
                service.run("查询合成记录", data_use=consent)
            self.assertEqual(caught.exception.code, "personal_data_use_required")
        self.assertEqual(model.calls, [])
        self.assertEqual(self.executed, [])

    def test_unconfigured_and_sensitive_input_never_send(self):
        service, model = self.service([], configured=False)
        with self.assertRaises(PersonalDialogueError) as caught:
            service.run("查询合成资料", data_use="personal_retrieval")
        self.assertEqual(caught.exception.code, "model_not_configured")
        for message in ("密码是SYNTHETIC-SECRET", "Bearer SYNTHETIC-SECRET", "学号：2099123456", "/Users/synthetic/private.pdf"):
            with self.assertRaises(PersonalDialogueError):
                service.run(message, data_use="personal_retrieval")
        self.assertEqual(model.calls, [])

    def test_no_current_tool_evidence_rejects_free_factual_answer(self):
        result, _, _ = self.run_service([final({"kind": "answer", "claims": [{"text": "合成课程成绩为100。", "evidence_ids": ["R-old"]}]})])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "unverified_personal_answer")
        self.assertNotIn("100", result["reply"])

    def test_not_ready_tool_cannot_mint_proof_even_if_callback_has_nonempty_records(self):
        self.records["status"] = "not_ready"
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["tool_results"][-1]["result"]["records"], [])
        self.assertNotIn("合成算法", json.dumps(messages, ensure_ascii=False))

    def test_old_history_evidence_cannot_support_current_turn_or_collide_with_new_aliases(self):
        service, model = self.service([call("query_my_records", {"operation": "academic"}), answer_from_last_tool])
        first, history = service.run("查询算法", data_use="personal_retrieval")
        old = first["citations"][0]["evidence_id"]
        model.responses = [call("query_my_records", {"operation": "academic"}),
            final({"kind": "answer", "claims": [{"text": "历史来源成绩为88。", "evidence_ids": [old]}]})]
        second, next_history = service.run("再核对一次", data_use="personal_retrieval", history=history)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["code"], "unverified_personal_answer")
        self.assertNotEqual(second["citations"][0]["evidence_id"], old)
        self.assertEqual(next_history, [])

    def test_unknown_tool_identity_parameters_and_profile_operation_never_execute(self):
        for name, args in (("fetch_school", {}), ("query_my_records", {"operation": "profile"}),
                ("get_my_context", {"owner": "other"}), ("search_my_curriculum", {"query": "模块", "path": "/tmp/source"}),
                ("query_my_records", {"operation": "academic", "student_id": "other"})):
            result, _, _ = self.run_service([call(name, args)])
            self.assertEqual(result["status"], "failed")
        self.assertTrue(all(name == "get_my_context" for name, _ in self.executed))

    def test_target_term_must_come_from_current_context_not_arbitrary_or_historical_scope(self):
        result, _, _ = self.run_service([call("query_my_records", {"operation": "academic", "target_term": "2099-2100-AW"}), answer_from_last_tool])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.executed[0], ("get_my_context", {}))
        self.executed.clear()
        result, _, _ = self.run_service([call("get_my_context"), call("query_my_records", {"operation": "academic", "target_term": "2098-2099-AW"}, "r2")])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        self.assertEqual(len(self.executed), 1)

    def test_year_and_tool_result_limits_are_strict(self):
        for args in ({"operation": "academic", "academic_year": "2099-2099"},
                     {"operation": "academic", "limit": 16}, {"operation": "academic", "limit": True}):
            result, _, _ = self.run_service([call("query_my_records", args)])
            self.assertEqual(result["code"], "invalid_tool_arguments")
        result, _, _ = self.run_service([call("search_my_curriculum", {"query": "模块", "limit": 6})])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        self.assertTrue(all(name == "get_my_context" for name, _ in self.executed))

    def test_graduation_schedule_and_action_success_claims_are_not_available(self):
        for text in ("已满足毕业资格。", "已获得3学分。", "这套课表完全无冲突。", "当前还差3学分。", "已经正式中选。", "已保存课表。",
                     "已获3学分。", "已为你生成推荐课表。", "保证无冲突。",
                     "不能只看原始分数，但你已满足毕业条件。", "无法保证有余量，但这套课表可选。"):
            def unsafe(messages, tools, value=text):
                result = json.loads(messages[-1]["content"])
                return final({"kind": "answer", "claims": [{"text": value, "evidence_ids": [result["records"][0]["evidence_id"]]}]})
            result, _, _ = self.run_service([call("query_my_records", {"operation": "academic"}), unsafe])
            self.assertEqual(result["code"], "unsupported_personal_claim")
            self.assertNotIn(text, result["reply"])

    def test_real_model_style_limits_and_source_caveats_are_not_positive_capability_claims(self):
        disclaimers = ["来源学分不等于已获学分。", "不能判断毕业资格，也未用于推荐。",
            "当前记录不能证明你已获得3学分。", "没有证据证明已满足毕业要求。",
            "目前课表尚未就绪，不能保证完全无冲突。", "本轮只解释来源记录，不能据此推断还差多少学分。",
            "不能确认已保存或已导出。", "尚未完成毕业审核。",
            "这是该文件片段中的标称学分，不是已获学分。",
            "同时提示观察到的成绩不构成已获学分或认定的证明。",
            "本轮可以说明来源成绩与已获学分的区别，毕业资格仍待审核。",
            "该资料提到课表推荐和已获学分这两个话题。",
            "本机培养资料中，SYNTHETIC-001 标注为“Nominal credits 3”（名义学分3），并非“来源学分”或已获学分；该片段未给出“来源学分”这一字段。"]
        for text in disclaimers:
            def limited(messages, tools, value=text):
                result = json.loads(messages[-1]["content"])
                return final({"kind": "answer", "claims": [{"text": value, "evidence_ids": [result["records"][0]["evidence_id"]]}]})
            result, _, _ = self.run_service([call("query_my_records", {"operation": "academic"}), limited])
            self.assertEqual(result["status"], "completed", (text, result.get("code")))
            self.assertIn(text, result["reply"])

    def test_long_form_synthetic_curriculum_reply_from_real_smoke_keeps_source_caveats(self):
        # Copied wording only from the pure-synthetic remote smoke, never personal data.
        texts = [
            "培养资料第1页的模块表中，SYNTHETIC-001 列在 Synthetic AI 模块下，标称学分（Nominal credits）为 3；这是该文件片段中的标称学分，不是已获学分。",
            "第1页另有一条说明：SYNTHETIC-001 需要先修 SYNTHETIC-000，并指向第2页的排除与适用性说明。",
            "第2页明确写明 SYNTHETIC-001 不计入 synthetic seminar 模块，且该页仅描述虚构队列 SYNTHETIC-2023；同时提示观察到的成绩不构成已获学分或认定的证明。",
            "适用审核状态：这些片段均为 unreviewed / rules_status 为 not_reviewed，检索整体状态为 pending_review，且 binding_status 为 not_verified、personal_applicability_not_inferred，因此不能据此认定该规则正式适用于本人。"]
        self.evidence["matches"] = [{**self.evidence["matches"][0], "text": text, "page": 1 if index < 2 else 2}
            for index, text in enumerate(texts)]
        def response(messages, tools):
            matches = json.loads(messages[-1]["content"])["matches"]
            return final({"kind": "answer", "claims": [{"text": text, "evidence_ids": [matches[index]["evidence_id"]]}
                for index, text in enumerate(texts)]})
        result, _, _ = self.run_service([call("search_my_curriculum", {"query": "SYNTHETIC-001"}), response])
        self.assertEqual(result["status"], "completed", result.get("code"))
        self.assertEqual(len(result["citations"]), 4)
        self.assertIn("[4]", result["reply"])
        self.assertIn("不是已获学分", result["reply"])

    def test_not_ready_and_minimal_clarification_are_explicit_model_decisions(self):
        for value in ({"kind": "not_ready", "reason": "graduation_not_ready"},
                      {"kind": "clarify", "question": "要核对哪个已读取学期？"}):
            result, _, model = self.run_service([final(value)])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(result["model_decision"]["kind"], value["kind"])
            self.assertEqual(result["citations"], [])
        result, _, _ = self.run_service([final({"kind": "clarify", "question": "请提供学校密码"})])
        self.assertEqual(result["code"], "unsupported_personal_claim")

    def test_server_preflight_precedes_model_and_repeated_context_uses_current_cache(self):
        self.context["capabilities"]["read_only_acquisition"] = True
        def first(messages, tools):
            self.assertEqual(self.executed, [("get_my_context", {})])
            self.assertIn("service_preflight get_my_context", messages[0]["content"])
            self.assertIn('"read_only_acquisition":true', messages[0]["content"])
            self.assertNotIn("NEVER-SEND", messages[0]["content"])
            return call("get_my_context")
        result, messages, _ = self.run_service([first, call("query_my_records", {"operation": "academic"}, "records"), answer_from_last_tool])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([name for name, _ in self.executed], ["get_my_context", "query_my_records"])
        self.assertEqual([row["name"] for row in result["tool_results"]], ["get_my_context", "query_my_records"])
        self.assertEqual(result["tool_results"][0]["initiated_by"], "service_preflight")
        self.assertEqual(result["tool_results"][1]["initiated_by"], "model")
        self.assertNotIn("service_preflight get_my_context", json.dumps(messages, ensure_ascii=False))

    def test_empty_preflight_scope_is_not_converted_to_empty_student_achievement(self):
        self.context["operations"] = []
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic", "query": "我的成绩怎么样"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.executed, [("get_my_context", {})])
        self.assertEqual(result["tool_results"][-1]["arguments"]["query"], "")
        remote = json.loads(next(row["content"] for row in messages if row["role"] == "tool"))
        self.assertEqual(remote["status"], "not_ready")
        self.assertEqual(remote["reason"], "no_observation_in_scope")
        self.assertFalse(remote["query_executed"])
        self.assertIsNone(remote["retrieval"]["matched_records"])
        self.assertIsNone(remote["retrieval"]["complete_within_loaded_snapshot"])
        self.assertEqual(remote["records"], [])
        self.assertEqual(result["citations"], [])
        self.assertNotIn("没有成绩", result["reply"])

    def test_model_selects_finite_general_guidance_without_uncited_personal_text(self):
        self.context["operations"] = []
        for intent in ("identity", "greeting", "capabilities", "clarify_input"):
            result, messages, model = self.run_service([final({"kind": "general_chat", "intent": intent})])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(model.calls), 1)
            self.assertTrue(result["model_used"])
            self.assertEqual(result["reply_origin"], "model_selected_service_guidance")
            self.assertEqual(result["model_decision"]["reply_text_origin"], "service_guidance")
            self.assertEqual(result["citations"], [])
        for value in ({"kind": "general_chat", "intent": "identity", "text": "你的成绩100。"},
                      {"kind": "general_chat", "intent": "your_grade_is_100"},
                      {"kind": "general_chat", "intent": "greeting", "claims": []}):
            result, _, _ = self.run_service([final(value)])
            self.assertEqual(result["code"], "invalid_final_answer")
            self.assertNotIn("100", result["reply"])

    def test_generic_grades_tool_query_is_normalized_without_widening_course_questions(self):
        for query, expected in (("我的成绩怎么样", ""), (" 请查看我的成绩？ ", ""),
                                ("我的高等数学成绩怎么样", "我的高等数学成绩怎么样")):
            self.executed.clear()
            result, _, _ = self.run_service([call("query_my_records", {"operation": "academic", "query": query}), answer_from_last_tool])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.executed[-1][1]["query"], expected)

    def test_record_paging_and_projection_omissions_are_explicit_without_private_cursor(self):
        self.records["records"] = [{"course_name_raw": "合成课程" * 100, "grade_raw": str(index),
                                     "field_states": {"course_name_raw": "known", "grade_raw": "known"}}
                                    for index in range(15)]
        self.records.update(matched_records=30, returned_records=15, next_cursor="NEVER-SEND-PRIVATE-CURSOR")
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}), answer_from_last_tool])
        self.assertEqual(result["status"], "completed")
        remote = json.loads(next(row["content"] for row in messages if row["role"] == "tool"))
        self.assertTrue(remote["retrieval"]["has_more"])
        self.assertFalse(remote["retrieval"]["complete_within_loaded_snapshot"])
        self.assertLess(len(remote["records"]), 15)
        self.assertEqual(remote["returned_records"], len(remote["records"]))
        self.assertGreater(remote["projection_omitted_rows"], 0)
        self.assertIn("record_query_or_projection_incomplete", remote["unknown"])
        self.assertNotIn("NEVER-SEND-PRIVATE-CURSOR", json.dumps(messages))

    def test_summary_has_its_own_citation_and_covers_all_filtered_source_rows(self):
        all_rows = [{"course_name_raw": "合成体育", "grade_raw": "90" if index < 15 else "10",
                     "field_states": {"grade_raw": "known"}} for index in range(22)]
        self.records.update(records=all_rows[:15], matched_records=22, returned_records=15,
            next_cursor="NEVER-SEND-CURSOR", source_grade_summary=source_grade_summary(all_rows, self.records["coverage"]))
        def summary_answer(messages, tools):
            remote = json.loads(messages[-1]["content"])
            summary = remote["source_grade_summary"]
            self.assertEqual(summary["matched_records"], 22)
            self.assertEqual(summary["numeric_mean_unweighted"], "64.55")
            self.assertFalse(summary["coverage"]["complete_within_scope"])
            self.assertFalse(remote["retrieval"]["complete_within_loaded_snapshot"])
            return final({"kind": "answer", "claims": [{"text": "当前部分来源范围内共匹配22条纯数字成绩，简单不加权均值64.55；这不是绩点，也不能证明已获学分。",
                "evidence_ids": [summary["evidence_id"]]}]})
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic", "query": "我的成绩怎么样"}), summary_answer])
        self.assertEqual(result["status"], "completed", result.get("code"))
        citation = result["citations"][0]
        self.assertEqual(citation["kind"], "summary")
        self.assertEqual(citation["title"], "来源成绩概况")
        self.assertEqual(citation["source_grade_summary"]["matched_records"], 22)
        self.assertIn("[1]", result["reply"])
        self.assertNotIn("NEVER-SEND", json.dumps(messages))

    def test_invalid_summary_cannot_mint_evidence_or_echo_untrusted_fields(self):
        self.records["source_grade_summary"] = {"matched_records": 1, "numeric_grade_count": 0,
            "non_numeric_grade_count": 0, "unknown_grade_count": 0, "raw_prompt": "NEVER-SEND"}
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("NEVER-SEND", json.dumps(messages))
        self.assertFalse(any(item["kind"] == "summary" for item in result["citations"]))

    def test_generation_change_after_model_or_tool_clears_all_personal_results(self):
        def changed(messages, tools):
            self.scope += ":new-generation"
            return call("query_my_records", {"operation": "academic"})
        result, messages, _ = self.run_service([changed])
        self.assertEqual(result["code"], "source_authorization_changed")
        self.assertEqual((result["tool_results"], result["citations"], messages), ([], [], []))
        self.assertEqual(self.executed, [("get_my_context", {})])
        def changed_tool(name, args):
            self.scope += ":another-generation"
            return self.records
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"})], execute_tool=changed_tool)
        self.assertEqual(result["code"], "source_authorization_changed")
        self.assertEqual((result["tool_results"], messages), ([], []))

    def test_versions_or_final_authorization_change_also_prevents_publication(self):
        service, _ = self.service([call("query_my_records", {"operation": "academic"}), answer_from_last_tool])
        original = service._final
        def changed(*args):
            result = original(*args)
            self.versions["records"] = "synthetic-v2"
            return result
        with patch.object(service, "_final", side_effect=changed):
            result, messages = service.run("核对记录", data_use="personal_retrieval")
        self.assertEqual(result["code"], "source_authorization_changed")
        self.assertEqual((result["tool_results"], result["citations"], messages), ([], [], []))

    def test_model_failure_keeps_separate_safe_local_results_but_not_failed_context(self):
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            ModelError("NEVER-ECHO-MODEL-SECRET", "model_unavailable")])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["partial_tools_completed"])
        self.assertTrue(result["tool_results"])
        self.assertNotIn("NEVER-ECHO", json.dumps(result))
        self.assertEqual(messages, [])

    def test_tool_failure_does_not_echo_raw_exception_and_can_produce_not_ready(self):
        def broken(name, args):
            raise ValueError("NEVER-ECHO-PDF-PATH-OR-COOKIE")
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "模块"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})], execute_tool=broken)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("NEVER-ECHO", json.dumps(result) + json.dumps(messages))

    def test_sensitive_known_field_rejects_whole_projection_and_rolls_back_proofs(self):
        self.records["records"].append({"course_name_raw": "密码是SYNTHETIC-SECRET"})
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["citations"], [])
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("SYNTHETIC-SECRET", json.dumps(messages))

    def test_long_evidence_is_omitted_whole_with_explicit_boundary_not_silently_cut(self):
        self.evidence["matches"][0]["text"] = "合成完整条件" * 500
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "条件"}),
            final({"kind": "not_ready", "reason": "insufficient_evidence"})])
        tool = json.loads(next(row["content"] for row in messages if row["role"] == "tool"))
        self.assertEqual(tool["matches"], [])
        self.assertEqual(tool["projection_omitted_matches"], 1)
        self.assertEqual(result["citations"], [])

    def test_invalid_or_oversized_history_is_reset_before_remote_send(self):
        for history in ([{"role": "system", "content": "NEVER-SEND-INJECTED-SYSTEM"}],
                        [{"role": "tool", "tool_call_id": "old", "content": '{"owner_student_id":"NEVER-SEND-OWNER"}'}],
                        [{"role": "user", "content": "x" * 33000}]):
            service, model = self.service([final({"kind": "clarify", "question": "要核对什么资料？"})])
            result, _ = service.run("核对资料", data_use="personal_retrieval", history=history)
            self.assertTrue(result["history_reset"])
            self.assertEqual(len(model.calls[0][0]), 2)

    def test_duplicate_json_unknown_final_and_repeated_tool_ids_fail_closed(self):
        malformed = call("query_my_records", {"operation": "academic"})
        malformed["tool_calls"][0]["function"]["arguments"] = '{"operation":"academic","operation":"profile"}'
        result, _, _ = self.run_service([malformed])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        result, _, _ = self.run_service([call("get_my_context"), call("get_my_context")])
        self.assertEqual(result["code"], "model_protocol_error")
        result, _, _ = self.run_service([final({"kind": "answer", "reply": "unverified"})])
        self.assertEqual(result["code"], "invalid_final_answer")

    def test_tool_and_turn_budgets_are_bounded(self):
        result, _, model = self.run_service([call("get_my_context")], max_steps=1)
        self.assertEqual(result["code"], "turn_budget_exceeded")
        self.assertEqual(len(model.calls), 1)
        three = {"role": "assistant", "content": None, "tool_calls": [
            call("get_my_context", identifier=f"call-{index}")["tool_calls"][0] for index in range(3)]}
        self.executed.clear()
        result, _, _ = self.run_service([three], max_tools=2)
        self.assertEqual(result["code"], "turn_budget_exceeded")
        self.assertEqual(self.executed, [("get_my_context", {})])


if __name__ == "__main__":
    unittest.main()
