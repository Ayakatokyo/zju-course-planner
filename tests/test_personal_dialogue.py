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
    return final({'kind': 'answer', 'text': text, 'evidence_ids': [proof]})


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
    def test_source_row_position_is_local_only_and_not_filtered_offset(self):
        self.records["source_record_indices"] = [17]
        response, messages, model = self.run_service([
            call("query_my_records", {"operation": "academic", "query": "算法"}), answer_from_last_tool])
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["citations"][0]["source_record_index"], 17)
        self.assertEqual(response["citations"][0]["record_index"], 0)
        self.assertNotIn("source_record_ind", json.dumps(messages))
        self.assertNotIn("source_record_ind", json.dumps(model.calls))

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
        self.assertNotIn("[1]", result["reply"])
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
        self.assertEqual({row["function"]["name"] for row in model.calls[0][1]}, {"get_my_context", "query_my_records", "search_my_curriculum", "search_course_catalog", "lookup_academic_terms", "finish_answer"})

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
            return final({'kind': 'answer', 'text': '\n\n'.join(["成绩来源原值88，来源学分不等于已获学分。", "不能判断毕业资格，也未用于推荐。"]), 'evidence_ids': ([record_id, context_id]) + ([context_id, record_id])})
        result, _, _ = self.run_service([call("get_my_context"), call("query_my_records", {"operation": "academic"}, "r"), combined])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["reply"].endswith("不能判断毕业资格，也未用于推荐。"))
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
        invalid = final({'kind': 'answer', 'text': "合成课程成绩为100。", 'evidence_ids': ["R-old"]})
        result, _, _ = self.run_service([invalid, invalid])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "unverified_personal_answer")
        self.assertNotIn("100", result["reply"])

    def test_only_validated_personal_evidence_text_becomes_visible_analysis(self):
        result, _, _ = self.run_service([call("search_my_curriculum", {"query": "模块"}), answer_from_last_tool])
        self.assertEqual(result["model_decision"]["answer_protocol"], "text-v1")
        self.assertTrue(result["citations"])
        context, _, _ = self.run_service([call("get_my_context"), answer_from_last_tool])
        self.assertEqual(context["status"], "completed")
        self.assertNotIn("evidence_analysis", context.get("model_decision") or {})
        invalid = final({'kind': 'answer', 'text': "合成建议", 'evidence_ids': ["E-old"]})
        rejected, _, _ = self.run_service([invalid, invalid])
        self.assertNotIn("evidence_analysis", rejected.get("model_decision") or {})

    def test_not_ready_tool_cannot_mint_proof_even_if_callback_has_nonempty_records(self):
        self.records["status"] = "not_ready"
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["tool_results"][-1]["result"]["records"], [])
        self.assertNotIn("合成算法", json.dumps(messages, ensure_ascii=False))

    def test_old_history_evidence_cannot_support_current_turn_or_collide_with_new_aliases(self):
        service, model = self.service([call("query_my_records", {"operation": "academic"}), answer_from_last_tool])
        first, history = service.run("查询算法", data_use="personal_retrieval")
        old = first["citations"][0]["evidence_id"]
        invalid = final({'kind': 'answer', 'text': "历史来源成绩为88。", 'evidence_ids': [old]})
        model.responses = [call("query_my_records", {"operation": "academic"}), invalid, invalid]
        second, next_history = service.run("再核对一次", data_use="personal_retrieval", history=history)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["code"], "unverified_personal_answer")
        self.assertNotEqual(second["citations"][0]["evidence_id"], old)
        self.assertEqual(next_history, [])

    def test_unknown_tool_identity_parameters_and_profile_operation_never_execute(self):
        for name, args in (("fetch_school", {}), ("query_my_records", {"operation": "profile"}),
                ("get_my_context", {"owner": "other"}), ("search_my_curriculum", {"query": "模块", "path": "/tmp/source"}),
                ("query_my_records", {"operation": "academic", "student_id": "other"})):
            result, _, _ = self.run_service([call(name, args), call(name, args)])
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
            result, _, _ = self.run_service([call("query_my_records", args), call("query_my_records", args)])
            self.assertEqual(result["code"], "invalid_tool_arguments")
        result, _, _ = self.run_service([call("search_my_curriculum", {"query": "模块", "limit": 6}),
            call("search_my_curriculum", {"query": "模块", "limit": 6})])
        self.assertEqual(result["code"], "invalid_tool_arguments")
        self.assertTrue(all(name == "get_my_context" for name, _ in self.executed))

    def test_graduation_schedule_and_action_success_claims_are_not_available(self):
        for text in ("已满足毕业资格。", "已获得3学分。", "这套课表完全无冲突。", "当前还差3学分。", "已经正式中选。",
                     "已获3学分。", "保证无冲突。",
                     "不能只看原始分数，但你已满足毕业条件。", "无法保证有余量，但这套课表可选。",
                     "不保证当前可选，但保证之后可选。", "不保证当前可选，但课表完全无冲突。",
                     "资料不足以说明课程内容，但你已满足毕业要求。",
                     "并非不足以证明已经满足毕业资格。", "不是不足以认定还差3学分。",
                     "不能说不足以证明已经满足毕业要求。",
                     "不是不保证可选，是已经确认可选。"):
            def unsafe(messages, tools, value=text):
                result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                return final({'kind': 'answer', 'text': value, 'evidence_ids': [result["records"][0]["evidence_id"]]})
            result, _, _ = self.run_service([call("query_my_records", {"operation": "academic"}), unsafe])
            self.assertEqual(result["code"], "unsupported_personal_claim")
            self.assertNotIn(text, result["reply"])

    def test_audit_credits_require_current_attributed_module_and_exact_value(self):
        proof = {"kind": "record", "operation": "graduation_audit", "tool_index": 0,
                 "record": {"node_kind": "module", "module_name_raw": "合成实践模块",
                            "earned_credits_raw": "6", "field_states": {"earned_credits_raw": "known"}}}
        text = "合成实践模块：学校自审不通过，已获6.0学分；这不是最终毕业认定。"
        def check(value, proofs=None, refs=None):
            return PersonalDialogueService._final(json.dumps({"kind": "answer", "text": value,
                "evidence_ids": ["R-test"] if refs is None else refs}), {"R-test": proof} if proofs is None else proofs)
        self.assertEqual(check(text)[0], text)
        summary = {"kind": "summary", "operation": "graduation_audit", "tool_index": 0,
                   "graduation_summary": {"modules": [{"course_name_raw": "合成实践模块", "earned_credits_raw": "6"}]}}
        self.assertEqual(check(text, {"R-test": summary})[0], text)
        for value in (text.replace("6.0", "9"), text.replace("合成实践模块", "另一模块"),
                      text.replace("学校自审", "当前"), text + "已满足毕业资格。", text + "还差3学分。"):
            with self.assertRaises(PersonalDialogueError) as error:
                check(value)
            self.assertEqual(error.exception.code, "unsupported_personal_claim")
        for record in ({**proof["record"], "earned_credits_raw": None},
                       {**proof["record"], "earned_credits_raw": "6+2"},
                       {**proof["record"], "field_states": {"earned_credits_raw": "unknown"}}):
            with self.assertRaises(PersonalDialogueError):
                check(text, {"R-test": {**proof, "record": record}})
        with self.assertRaises(PersonalDialogueError):
            check(text, {"R-test": {**proof, "operation": "academic"}})
        with self.assertRaises(PersonalDialogueError):
            check(text, refs=["R-stale"])
        with self.assertRaises(PersonalDialogueError):
            check(text, refs=[])

    def test_real_model_style_limits_and_source_caveats_are_not_positive_capability_claims(self):
        disclaimers = ["来源学分不等于已获学分。", "不能判断毕业资格，也未用于推荐。",
            "两者不足以直接认定你个人还差3学分，也不能据此认定已满足毕业要求。",
            "这些资料不足以证明已经满足毕业资格。",
            "当前记录不能证明你已获得3学分。", "没有证据证明已满足毕业要求。",
            "目前课表尚未就绪，不能保证完全无冲突。", "本轮只解释来源记录，不能据此推断还差多少学分。",
            "方案只基于本学期历史课程观察，不保证当前开课、可选或有余量。",
            "也不保证正式中选。", "不确保完全无冲突。",
            "不能确认已保存或已导出。", "尚未完成毕业审核。",
            "这是该文件片段中的标称学分，不是已获学分。",
            "同时提示观察到的成绩不构成已获学分或认定的证明。",
            "本轮可以说明来源成绩与已获学分的区别，毕业资格仍待审核。",
            "该资料提到课表推荐和已获学分这两个话题。",
            "本机培养资料中，SYNTHETIC-001 标注为“Nominal credits 3”（名义学分3），并非“来源学分”或已获学分；该片段未给出“来源学分”这一字段。"]
        for text in disclaimers:
            def limited(messages, tools, value=text):
                result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                return final({'kind': 'answer', 'text': value, 'evidence_ids': [result["records"][0]["evidence_id"]]})
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
            matches = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))["matches"]
            return final({'kind': 'answer', 'text': '\n\n'.join([text for index, text in enumerate(texts)]), 'evidence_ids': (sum([[matches[index]['evidence_id']] for index, text in enumerate(texts)], []))})
        result, _, _ = self.run_service([call("search_my_curriculum", {"query": "SYNTHETIC-001"}), response])
        self.assertEqual(result["status"], "completed", result.get("code"))
        self.assertEqual(len(result["citations"]), 4)
        self.assertIn("不是已获学分", result["reply"])

    def test_not_ready_and_minimal_clarification_are_explicit_model_decisions(self):
        for value in ({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []},
                      {'kind': "clarify", 'text': "要核对哪个已读取学期？"}):
            result, _, model = self.run_service([final(value)])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(result["model_decision"]["kind"], value["kind"])
            self.assertEqual(result["citations"], [])
        result, _, _ = self.run_service([final({'kind': "clarify", 'text': "请提供学校密码"})])
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
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
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


    def test_generic_grades_tool_query_is_normalized_without_widening_course_questions(self):
        for query, expected in (("我的成绩怎么样", ""), (" 请查看我的成绩？ ", ""),
                                ("我的高等数学成绩怎么样", "我的高等数学成绩怎么样")):
            self.executed.clear()
            result, _, _ = self.run_service([call("query_my_records", {"operation": "academic", "query": query}), answer_from_last_tool])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.executed[-1][1]["query"], expected)

    def test_record_paging_and_projection_omissions_are_explicit_without_private_cursor(self):
        self.records["records"] = [{"course_name_raw": "合成课程" * 100, "grade_raw": str(index),
                                     "academic_year_raw": "来源年份长文本" * 80, "term_raw": "来源学期长文本" * 80,
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
            remote = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            summary = remote["source_grade_summary"]
            self.assertEqual(summary["matched_records"], 22)
            self.assertEqual(summary["numeric_mean_unweighted"], "64.55")
            self.assertEqual(summary["completion_status_counts"], {"known":0,"unknown":22})
            self.assertFalse(summary["coverage"]["complete_within_scope"])
            self.assertFalse(remote["retrieval"]["complete_within_loaded_snapshot"])
            self.assertEqual(summary["record_source"]["operation"], "academic")
            self.assertEqual(summary["record_source"]["label"], "2099-2100学年秋冬学期成绩来源")
            return final({'kind': 'answer', 'text': '当前来源共22条记录，简单均值64.55，不是GPA；不能证明完整历史。', 'evidence_ids': ([summary['evidence_id']])})
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic", "query": "我的成绩怎么样"}), summary_answer])
        self.assertEqual(result["status"], "completed", result.get("code"))
        citation = result["citations"][0]
        self.assertEqual(citation["kind"], "summary")
        self.assertEqual(citation["title"], "来源成绩概况")
        self.assertEqual(citation["source_grade_summary"]["matched_records"], 22)
        self.assertEqual(citation["source_grade_summary"]["completion_status_counts"], {"known":0,"unknown":22})
        self.assertNotIn("[1]", result["reply"])
        self.assertEqual(citation["record_source"]["label"], "2099-2100学年秋冬学期成绩来源")
        self.assertIn("64.55", result["reply"])
        self.assertNotIn("主修", result["reply"])
        self.assertNotIn("NEVER-SEND", json.dumps(messages))

    def test_invalid_summary_cannot_mint_evidence_or_echo_untrusted_fields(self):
        self.records["source_grade_summary"] = {"matched_records": 1, "numeric_grade_count": 0,
            "non_numeric_grade_count": 0, "unknown_grade_count": 0, "raw_prompt": "NEVER-SEND"}
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("NEVER-SEND", json.dumps(messages))
        self.assertFalse(any(item["kind"] == "summary" for item in result["citations"]))

    def test_completion_status_summary_must_have_exact_known_unknown_counts(self):
        for counts in ({"known":True,"unknown":0},{"known":-1,"unknown":2},{"known":0,"unknown":0},
                {"known":1},{"known":1,"unknown":0,"raw_prompt":"NEVER-SEND"},None):
            with self.subTest(counts=counts):
                self.records["source_grade_summary"]=source_grade_summary(self.records["records"],self.records["coverage"])
                self.records["source_grade_summary"]["completion_status_counts"]=counts
                result,messages,_=self.run_service([call("query_my_records",{"operation":"academic"}),
                    final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
                self.assertTrue(result["tool_results"][-1]["error"])
                self.assertNotIn("NEVER-SEND",json.dumps(messages))
                self.assertFalse(any(c["kind"]=="summary" for c in result["citations"]))

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
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})], execute_tool=broken)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("NEVER-ECHO", json.dumps(result) + json.dumps(messages))

    def test_sensitive_known_field_rejects_whole_projection_and_rolls_back_proofs(self):
        self.records["records"].append({"course_name_raw": "密码是SYNTHETIC-SECRET"})
        result, messages, _ = self.run_service([call("query_my_records", {"operation": "academic"}),
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["citations"], [])
        self.assertTrue(result["tool_results"][-1]["error"])
        self.assertNotIn("SYNTHETIC-SECRET", json.dumps(messages))

    def test_long_evidence_is_omitted_whole_with_explicit_boundary_not_silently_cut(self):
        self.evidence["matches"][0]["text"] = "合成完整条件" * 500
        result, messages, _ = self.run_service([call("search_my_curriculum", {"query": "条件"}),
            final({'kind': 'not_ready', 'text': '现有资料不足以支持该结论，还需核对相应来源与适用条件。', 'evidence_ids': []})])
        tool = json.loads(next(row["content"] for row in messages if row["role"] == "tool"))
        self.assertEqual(tool["matches"], [])
        self.assertEqual(tool["projection_omitted_matches"], 1)
        self.assertEqual(result["citations"], [])

    def test_curriculum_ocr_and_listing_coverage_are_projected_without_coordinates(self):
        item = self.evidence['matches'][0]
        item.update(extraction_method='local_ocr', text_quality='ocr_needs_review', source_boxes=[{'bbox': [1, 2, 3, 4]}])
        self.evidence.update(retrieval_mode='module_course_listing', listing={'status': 'parsed_module_rows',
            'offset': 0, 'total_groups': 1, 'returned_groups': 1, 'next_offset': None, 'has_more': False, 'complete': True})
        self.evidence['coverage']['pages_needing_review'] = [2]
        proofs = {}
        local, remote = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '专业选修', 'limit': 5, 'mode': 'module_courses'}, 'test', proofs, 0, [0, 0, {}], set())
        self.assertEqual(remote['matches'][0]['text_quality'], 'ocr_needs_review')
        self.assertEqual(remote['extraction_quality']['pages_needing_review'], [2])
        self.assertNotIn('source_boxes', remote['matches'][0])
        self.assertTrue(local['matches'][0]['source_boxes'])
        _, limited = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '专业选修', 'limit': 5, 'mode': 'module_courses'}, 'other', {}, 0, [0, 0, {'E': 12000}], set())
        self.assertFalse(limited['listing']['complete'])
        self.assertFalse(limited['listing']['projection_complete'])

    def test_curriculum_counts_reflect_only_transmitted_rows_and_navigation_is_actionable(self):
        from course_assistant.curriculum_facts import summarize_course_rows
        self.evidence['course_summary'] = summarize_course_rows([
            {'credits': '2.0', 'suggested_term_raw': '一(夏)'}, {'credits': None}])
        self.evidence['matches'][0]['course_row_count'] = 4
        self.evidence['listing'] = {'status': 'parsed_module_rows', 'total_groups': 6,
            'total_course_rows': 22, 'returned_groups': 1, 'returned_course_rows': 4}
        self.evidence['module_choices'] = [{'module_path': ['专业选修课程'], 'request': {
            'query': '专业选修课程', 'mode': 'module_courses', 'limit': 5, 'offset': 0}}]
        args = {'query': '专业选修', 'mode': 'module_courses', 'limit': 5}
        _, wire = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            args, 'test', {}, 0, [0, 0, {}], set())
        self.assertEqual(wire['listing']['total_course_rows'], 22)
        self.assertEqual(wire['course_summary']['known_nominal_credits_sum'], '2.0')
        self.assertEqual(wire['course_summary']['unknown_credit_rows'], 1)
        self.assertEqual(wire['matches'][0]['course_row_count'], 4)
        self.assertEqual(wire['module_choices'][0]['request']['mode'], 'module_courses')
        _, omitted = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            args, 'test', {}, 0, [0, 0, {'E': 12000}], set())
        self.assertEqual(omitted['listing']['returned_course_rows'], 0)
        self.assertEqual(omitted['listing']['returned_groups'], 0)
        self.assertEqual(omitted['listing']['total_course_rows'], 22)

    def test_shared_context_is_sent_once_with_resolvable_batch_local_references(self):
        original = self.evidence['matches'][0]
        original.update(source_alias='PLAN-SYNTHETIC', model_context_complete=True,
            model_context=[{'text': '注：仅限合成模块，不得跨模块替代。', 'page': 2, 'context_role': 'module_note'}])
        other = deepcopy(original)
        other['text'] = 'SYN1002M 合成第二课 3.0'
        other['reference'] = 'OTHER-SYNTHETIC-REFERENCE'
        self.evidence['matches'] = [original, other]
        local, remote = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '合成模块', 'limit': 5}, 'ctx', {}, 0, [0, 0, {}], set())
        first, second = remote['matches']
        self.assertEqual(second['model_context'][0], {'context_source_evidence_id': first['evidence_id'], 'context_index': 0})
        self.assertIn('不得跨模块', first['model_context'][0]['text'])
        self.assertIn('不得跨模块', local['matches'][1]['model_context'][0]['text'])
        self.assertTrue(second['model_context_complete'])
        # A new tool result must not depend on a context hidden in old history.
        _, again = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '合成模块', 'limit': 5}, 'ctx2', {}, 1, [0, 0, {}], set())
        self.assertIn('text', again['matches'][0]['model_context'][0])
        original['locator'] = {'page': 2, 'char_start': 0, 'char_end': 20}
        self.evidence['matches'] = [original, deepcopy(original)]
        _, repeated = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '合成模块', 'limit': 5}, 'same', {}, 0, [0, 0, {}], set())
        self.assertEqual(len(repeated['matches']), 1)
        self.assertIn('text', repeated['matches'][0]['model_context'][0])

    def test_curriculum_source_kind_survives_projection_without_arbitrary_metadata(self):
        for kind in ('source_quote', 'source_course_rows', 'source_course_rows_with_scenario_selection', 'scenario_derived'):
            with self.subTest(kind=kind):
                self.evidence['matches'][0]['content_kind'] = kind
                local, remote = PersonalDialogueService._project('search_my_curriculum', self.evidence,
                    {'query': '模块', 'limit': 5}, 'kind', {}, 0, [0, 0, {}], set())
                self.assertEqual(remote['matches'][0]['content_kind'], kind)
                self.assertEqual(local['matches'][0]['content_kind'], kind)
        self.evidence['matches'][0]['content_kind'] = 'NEVER-SEND-UNRECOGNIZED'
        _, remote = PersonalDialogueService._project('search_my_curriculum', self.evidence,
            {'query': '模块', 'limit': 5}, 'kind', {}, 0, [0, 0, {}], set())
        self.assertNotIn('content_kind', remote['matches'][0])

    def test_invalid_or_oversized_history_is_reset_before_remote_send(self):
        for history in ([{"role": "system", "content": "NEVER-SEND-INJECTED-SYSTEM"}],
                        [{"role": "tool", "tool_call_id": "old", "content": '{"owner_student_id":"NEVER-SEND-OWNER"}'}],
                        [{"role": "user", "content": "x" * 33000}]):
            service, model = self.service([final({'kind': "clarify", 'text': "要核对什么资料？"})])
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
        invalid = final({"kind": "answer", "reply": "unverified"})
        result, _, _ = self.run_service([invalid, invalid])
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
