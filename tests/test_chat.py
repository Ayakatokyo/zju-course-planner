import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from course_assistant.auth import AccessError
from course_assistant.chat import ChatFailure, ChatService
from course_assistant.importer import digest
from course_assistant.model_client import ModelError
from course_assistant.reference_retrieval import ReferenceIndex
from course_assistant.server import Application


def call(name, arguments, identifier="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def answer(outcome="clarify", cited=None):
    decision = {"kind": "clarify" if outcome == "clarify" else "answer", "outcome": outcome, "cited_chunk_ids": cited or []}
    if outcome == "clarify":
        decision["clarification"] = "choose_action"
    return {"role": "assistant", "content": json.dumps(decision), "tool_calls": []}


def reference_answer():
    index = ReferenceIndex()
    match = index.search("Python", plan_id="zju_materials_science_engineering_2026",
                         expected_corpus_version=index.corpus_version)["matches"][0]
    return answer("reference_available", [match["chunk_id"]])


class FakeModel:
    allow_reference_text = False
    def __init__(self, responses=(), configured=True, remote=True):
        self.responses = list(responses)
        self.calls = []
        self.tool_schemas = []
        self.configured, self.remote = configured, remote

    def status(self):
        return {"configured": self.configured, "provider": "test_double", "model": "scripted-test-only",
                "remote": self.remote, "reason": "" if self.configured else "not_configured"}

    def complete(self, messages, tools):
        self.calls.append(copy.deepcopy(messages))
        self.tool_schemas.append(copy.deepcopy(tools))
        result = self.responses.pop(0)
        if callable(result):
            return result()
        if isinstance(result, Exception):
            raise result
        return result


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name) / "chat.sqlite3", mock_enabled=True)
        self.token, _ = self.app.auth.login_mock({"profile_id": "demo-a"})
        self.context = self.app.auth.authenticate(self.token)
        token, _ = self.app.auth.login_mock({"profile_id": "demo-b"})
        self.other = self.app.auth.authenticate(token)
        self.before = self.app.workspace(self.context)

    def request(self, message="尽量周五空闲，保留体育", previous=None):
        return {"request_id": str(uuid.uuid4()), "expected_turn": previous["turn_id"] if previous else 0,
                "conversation_id": previous["conversation_id"] if previous else None,
                "message": message, "consent_remote": True}

    def service(self, responses=(), **kwargs):
        model = FakeModel(responses, **kwargs)
        return ChatService(self.app, model), model

    def test_unconfigured_and_missing_consent_do_not_send_or_create_turn(self):
        for configured, consent, reason in ((False, True, "model_not_configured"), (True, False, "remote_consent_required")):
            service, model = self.service(configured=configured)
            request = self.request()
            request["consent_remote"] = consent
            with self.assertRaises(AccessError) as caught:
                service.turn(request, self.context)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(model.calls, [])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM owned_conversations").fetchone()[0], 0)

    def test_real_loop_protocol_runs_tools_and_keeps_workspace_unchanged(self):
        service, model = self.service([call("get_my_context", {}), call("generate_my_plan", {"constraint_patch": {"friday_free": "soft"}}, "call_2"), answer("feasible")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual([row["name"] for row in response["tool_results"]], ["get_my_context", "generate_my_plan"])
        generation = response["tool_results"][-1]["result"]
        self.assertEqual(generation["status"], "feasible")
        self.assertTrue(generation["preview_only"])
        self.assertTrue(all("DEMO-PE-A" in row["section_ids"] for row in generation["plans"]))
        self.assertEqual(self.app.workspace(self.context), self.before)
        self.assertEqual(len(model.calls), 3)
        sent = json.dumps(model.calls, ensure_ascii=False)
        self.assertNotIn(self.token, sent)
        self.assertNotIn(self.context.session_ref, sent)
        self.assertNotIn(self.context.student_id, sent)

    def test_multiturn_preview_inherits_lock_and_hard_condition(self):
        service, _ = self.service([call("generate_my_plan", {"constraint_patch": {"locked_section_ids": ["DEMO-ML-B"]}}), answer("feasible"),
                                  call("generate_my_plan", {"constraint_patch": {"friday_free": "hard"}}), answer("scoped_infeasible")])
        first = service.turn(self.request("保留体育，锁定周五的机器学习B班"), self.context)
        second = service.turn(self.request("周五必须空闲", first), self.context)
        result = second["tool_results"][0]["result"]
        self.assertEqual(result["status"], "scoped_infeasible")
        self.assertIn("DEMO-PE-A", result["constraints"]["locked_section_ids"])
        self.assertIn("DEMO-ML-B", result["constraints"]["locked_section_ids"])
        self.assertFalse(result["plans"])
        self.assertEqual(self.app.workspace(self.context), self.before)

    def test_no_model_unlock_or_hard_condition_relaxation(self):
        cases = [{"unlock_section_ids": ["DEMO-PE-A"]}, {"friday_free": "soft"}, {"max_credits": 20}]
        self.app.post("/api/generate", {"constraints": {"friday_free": "hard", "max_credits": 10}}, self.context)
        before = self.app.workspace(self.context)
        for patch in cases:
            service, _ = self.service([call("generate_my_plan", {"constraint_patch": patch})])
            result = service.turn(self.request(), self.context)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(self.app.workspace(self.context), before)

    def test_empty_lists_do_not_drop_targets_or_protected_members(self):
        service, _ = self.service([call("generate_my_plan", {"constraint_patch": {"requested_course_ids": [], "locked_section_ids": []}}), answer("feasible")])
        result = service.turn(self.request(), self.context)["tool_results"][0]["result"]
        self.assertEqual(result["constraints"]["requested_course_ids"], self.before["constraints"]["requested_course_ids"])
        self.assertIn("DEMO-PE-A", result["constraints"]["locked_section_ids"])
        self.assertIn("DEMO-PE-A", result["constraints"]["preloaded_section_ids"])

    def test_fixed_items_are_retained_without_transmitting_free_text(self):
        self.app.post("/api/generate", {"constraints": {"fixed_meetings": [{"day_of_week": 3, "note": "PRIVATE_SENTINEL"}]}}, self.context)
        service, model = self.service([call("get_my_context", {}), call("generate_my_plan", {"constraint_patch": {}}, "call_2"), answer("incomplete_data")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["tool_results"][-1]["result"]["constraints"]["fixed_meetings"][0]["note"], "PRIVATE_SENTINEL")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(model.calls))

    def test_query_audit_sections_and_reference_text_stays_local(self):
        service, model = self.service([call("audit_my_progress", {}),
                                      call("find_my_sections", {"course_ids": ["zju:MSE3123M"]}, "call_2"),
                                      call("search_plan_references", {"query": "Python"}, "call_3"), reference_answer()])
        response = service.turn(self.request("查询Python依据和我当前学业情况"), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["tool_results"][0]["result"]["ledger"]["base"], 8)
        self.assertTrue(response["citations"])
        self.assertTrue(all(row["enforce_automatically"] is False for row in response["citations"]))
        self.assertTrue(all("zju_materials_science_engineering_2026" in row["plan_ids"] for row in response["citations"]))
        tool_content = json.loads(model.calls[-1][-1]["content"])
        self.assertFalse(tool_content["reference_text_transmitted"])
        self.assertNotIn("text", tool_content["matches"][0])
        self.assertNotIn("title", tool_content["matches"][0])
        self.assertIn("text", response["citations"][0])

    def test_reference_text_requires_separate_configuration(self):
        service, model = self.service([call("search_plan_references", {"query": "Python"}), reference_answer()])
        model.allow_reference_text = True
        response = service.turn(self.request(), self.context)
        tool_content = json.loads(model.calls[-1][-1]["content"])
        self.assertTrue(tool_content["reference_text_transmitted"])
        self.assertIn("text", tool_content["matches"][0])
        self.assertEqual(response["status"], "completed")

    def test_revoking_reference_text_policy_does_not_resend_old_excerpts(self):
        service, model = self.service([call("search_plan_references", {"query": "Python"}), reference_answer(),
                                      call("get_my_context", {}), answer("context")])
        model.allow_reference_text = True
        first = service.turn(self.request(), self.context)
        self.assertIn("text", json.loads(model.calls[1][-1]["content"])["matches"][0])
        model.allow_reference_text = False
        second = service.turn(self.request("继续核对当前条件", first), self.context)
        self.assertTrue(second["history_reset"])
        self.assertEqual(len(model.calls[2]), 2)
        self.assertEqual(second["status"], "completed")

    def test_other_owner_cannot_read_or_continue_conversation(self):
        service, model = self.service([answer()])
        first = service.turn(self.request(), self.context)
        for action in (lambda: service.history({"conversation_id": first["conversation_id"]}, self.other),
                       lambda: service.turn(self.request("查询", first), self.other)):
            with self.assertRaises(AccessError) as caught:
                action()
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(model.calls), 1)

    def test_request_replay_and_revision_conflicts(self):
        service, model = self.service([answer()])
        request = self.request()
        first = service.turn(request, self.context)
        repeated = service.turn(request, self.context)
        self.assertTrue(repeated["replayed"])
        self.assertEqual(first["turn_id"], repeated["turn_id"])
        self.assertEqual(len(model.calls), 1)
        with self.assertRaises(AccessError) as caught:
            service.turn({**request, "message": "不同的消息"}, self.context)
        self.assertEqual(caught.exception.reason, "request_id_reused")
        with self.assertRaises(AccessError) as caught:
            service.turn({**self.request(previous=first), "expected_turn": 0}, self.context)
        self.assertEqual(caught.exception.reason, "turn_version_changed")

    def test_logout_during_model_call_stops_tools_and_results(self):
        def logout():
            self.app.auth.logout(self.context)
            return call("get_my_context", {})
        service, model = self.service([logout])
        with self.assertRaises(AccessError):
            service.turn(self.request(), self.context)
        self.assertEqual(len(model.calls), 1)
        with self.app.store.connect() as db:
            row = db.execute("SELECT status,response FROM owned_chat_requests").fetchone()
        self.assertEqual(row[0], "failed")
        self.assertEqual(json.loads(row[1])["tool_results"], [])

    def test_version_change_during_inference_discards_result(self):
        def update():
            self.app.post("/api/generate", {"constraints": {"friday_free": "hard"}}, self.context)
            return call("generate_my_plan", {"constraint_patch": {"friday_free": "soft"}})
        service, _ = self.service([update])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["code"], "workspace_changed")
        self.assertEqual(response["tool_results"], [])
        self.assertEqual(self.app.workspace(self.context)["constraints"]["friday_free"], "hard")

    def test_changed_workspace_invalidates_preview_and_old_model_history(self):
        service, model = self.service([call("generate_my_plan", {"constraint_patch": {"locked_section_ids": ["DEMO-ML-B"]}}), answer("feasible"),
                                      call("get_my_context", {}), answer("context")])
        first = service.turn(self.request(), self.context)
        self.app.post("/api/generate", {"constraints": {"friday_free": "hard"}}, self.context)
        second = service.turn(self.request("现在怎样", first), self.context)
        self.assertTrue(second["preview_reset"])
        self.assertNotIn("DEMO-ML-B", second["tool_results"][0]["result"]["constraints"]["locked_section_ids"])
        self.assertEqual(len(model.calls[2]), 2)
        self.assertTrue(service.history({"conversation_id": second["conversation_id"]}, self.context)["turns"])

    def test_arbitrary_import_not_admitted_to_model_even_if_marked_synthetic(self):
        demo = copy.deepcopy(self.before["demo"])
        demo["metadata"]["description"] = "PRIVATE_SENTINEL"
        self.app.post("/api/import", {"bundle": demo}, self.context)
        service, model = self.service([answer()])
        with self.assertRaises(AccessError) as caught:
            service.turn(self.request(), self.context)
        self.assertEqual(caught.exception.reason, "model_dataset_not_allowed")
        self.assertEqual(model.calls, [])

    def test_bad_json_duplicate_keys_nonfinite_and_extra_authority_fail_closed(self):
        bad = ['{"constraint_patch":{"friday_free":"soft","friday_free":"hard"}}',
               '{"constraint_patch":{"min_credits":NaN}}', '{"owner_student_id":"DEMO-STUDENT-002"}', '[]']
        for arguments in bad:
            response = call("generate_my_plan", {})
            response["tool_calls"][0]["function"]["arguments"] = arguments
            service, _ = self.service([response])
            result = service.turn(self.request(), self.context)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(self.app.workspace(self.context), self.before)

    def test_unknown_tool_course_and_duplicate_call_id_rejected(self):
        cases = [[call("read_arbitrary_file", {})], [call("find_my_sections", {"course_ids": ["other:course"]})],
                 [call("get_my_context", {}), call("get_my_context", {})]]
        for responses in cases:
            service, _ = self.service(responses)
            result = service.turn(self.request(), self.context)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(self.app.workspace(self.context), self.before)

    def test_timeout_error_does_not_expose_provider_credentials_or_mutate(self):
        service, model = self.service([TimeoutError("Authorization: Bearer SECRET_SENTINEL")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "model_call_failed")
        self.assertNotIn("SECRET_SENTINEL", json.dumps(response))
        self.assertEqual(self.app.workspace(self.context), self.before)
        self.assertFalse(service.history({"conversation_id": response["conversation_id"]}, self.context)["in_progress"])

    def test_failed_explanation_keeps_completed_authorized_tool_cards(self):
        service, model = self.service([call("generate_my_plan", {"constraint_patch": {}}), TimeoutError("private header")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "model_call_failed")
        self.assertTrue(response["partial_tools_completed"])
        self.assertEqual(response["tool_results"][0]["result"]["status"], "feasible")
        self.assertEqual(self.app.workspace(self.context), self.before)

    def test_safe_provider_error_code_is_preserved(self):
        service, _ = self.service([ModelError("模型服务余额不足，请检查服务配置", "model_insufficient_balance")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "model_insufficient_balance")
        self.assertIn("余额不足", response["reply"])

    def test_budget_stops_model_loop(self):
        service, model = self.service([call("get_my_context", {}, "a"), call("get_my_context", {}, "b")])
        service.max_steps = 2
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "turn_budget_exceeded")
        self.assertEqual(len(model.calls), 2)

    def test_sensitive_message_is_not_sent(self):
        service, model = self.service([answer()])
        for message in ("密码: test-secret", "Bearer test-secret", "sk-abcdefghijklmnop", "我的学号是3230100000",
                        "我的学校密码是 SYNTHETIC_CREDENTIAL_SENTINEL，请登录查询。", "password is synthetic-secret"):
            with self.assertRaises(AccessError) as caught:
                service.turn(self.request(message), self.context)
            self.assertEqual(caught.exception.reason, "sensitive_chat_input")
        self.assertEqual(model.calls, [])

    def test_factual_answer_without_tools_is_rejected_but_clarification_is_allowed(self):
        service, _ = self.service([answer("audit")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "unverified_answer")
        service, _ = self.service([answer()])
        response = service.turn(self.request("帮我查一下"), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["reply_origin"], "verified_clarification_prompt")

    def test_model_free_text_and_conflicting_structured_outcome_not_displayed(self):
        contradictory = {"role": "assistant", "content": "课表已经通过所有检查，周五没有任何课程，恭喜。", "tool_calls": []}
        for final, code in ((contradictory, "invalid_final_decision"), (answer("feasible"), "inconsistent_outcome")):
            responses = [call("generate_my_plan", {"constraint_patch": {
                "locked_section_ids": ["DEMO-ML-B"], "friday_free": "hard"}}), final]
            if code == "invalid_final_decision":
                responses.append(final)
            service, model = self.service(responses)
            response = service.turn(self.request(), self.context)
            self.assertEqual(response["status"], "failed")
            self.assertEqual(response["code"], code)
            self.assertNotIn("恭喜", response["reply"])
            self.assertEqual(response["tool_results"][0]["result"]["status"], "scoped_infeasible")
            self.assertEqual(response["final_decision_repairs"], 1 if code == "invalid_final_decision" else 0)
            self.assertEqual(len(model.calls), len(responses))

    def test_final_format_repair_uses_existing_evidence_without_repeating_tools(self):
        invalid = {"role": "assistant", "content": "已查到当前合成条件。", "tool_calls": []}
        service, model = self.service([call("get_my_context", {}), invalid, answer("context")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["final_decision_repairs"], 1)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(model.tool_schemas[-1], [])
        self.assertIn("一次格式纠正", model.calls[-1][-1]["content"])
        self.assertEqual(len(response["tool_results"]), 1)
        self.assertEqual(response["model_decision"]["source_tool_result_index"], 0)
        self.assertEqual(self.app.workspace(self.context), self.before)

    def test_final_format_repair_fails_after_second_invalid_response(self):
        invalid = {"role": "assistant", "content": '{"reply":"自由文字不是可核验决策"}', "tool_calls": []}
        service, model = self.service([call("get_my_context", {}), invalid, invalid, answer("context")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["code"], "invalid_final_decision")
        self.assertEqual(response["final_decision_repairs"], 1)
        self.assertEqual(len(model.calls), 3)
        self.assertTrue(response["partial_tools_completed"])

    def test_final_format_repair_cannot_call_tools_or_expand_step_budget(self):
        invalid = {"role": "assistant", "content": "格式错误", "tool_calls": []}
        service, model = self.service([call("get_my_context", {}), invalid,
                                      call("generate_my_plan", {"constraint_patch": {}}, "repeat")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "invalid_final_decision")
        self.assertEqual(response["final_decision_repairs"], 1)
        self.assertEqual(len(response["tool_results"]), 1)
        service, model = self.service([call("get_my_context", {}), invalid, answer("context")])
        service.max_steps = 2
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "turn_budget_exceeded")
        self.assertEqual(response["final_decision_repairs"], 0)
        self.assertEqual(len(model.calls), 2)

    def test_revoked_authorization_is_rechecked_before_format_repair(self):
        invalid = {"role": "assistant", "content": "格式错误", "tool_calls": []}
        service, model = self.service([call("get_my_context", {}), invalid, answer("context")])
        original_present = service._present_decision
        def revoke_after_invalid(*args):
            try:
                return original_present(*args)
            except ChatFailure as exc:
                if exc.code == "invalid_final_decision":
                    self.app.auth.logout(self.context)
                raise
        service._present_decision = revoke_after_invalid
        with self.assertRaises(AccessError):
            service.turn(self.request(), self.context)
        self.assertEqual(len(model.calls), 2)
        with self.app.store.connect() as db:
            response = json.loads(db.execute("SELECT response FROM owned_chat_requests").fetchone()[0])
        self.assertEqual(response["final_decision_repairs"], 0)
        self.assertEqual(response["tool_results"], [])

    def test_network_failure_during_format_repair_is_not_retried(self):
        invalid = {"role": "assistant", "content": "格式错误", "tool_calls": []}
        service, model = self.service([call("get_my_context", {}), invalid,
                                      ModelError("模型请求超时", "model_timeout"), answer("context")])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["code"], "model_timeout")
        self.assertEqual(response["final_decision_repairs"], 1)
        self.assertEqual(len(model.calls), 3)

    def test_generation_can_remain_primary_after_same_condition_validation(self):
        service, _ = self.service([
            call("generate_my_plan", {"constraint_patch": {"friday_free": "soft"}}, "generate"),
            call("validate_my_plan", {"section_ids": ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"]}, "validate"),
            answer("feasible"),
        ])
        response = service.turn(self.request("推荐两到三份课表并核对第一份"), self.context)
        self.assertEqual(response["status"], "completed")
        generation, validation = [item["result"] for item in response["tool_results"]]
        self.assertEqual(generation["status"], "feasible")
        self.assertEqual(validation["result"], "pass")
        self.assertNotEqual(generation["constraints"]["revision"], validation["constraints"]["revision"])
        self.assertEqual(response["model_decision"]["source_tool_result_index"], 0)
        self.assertEqual(response["model_decision"]["source_tool"], "generate_my_plan")
        self.assertIn("3 份", response["reply"])
        self.assertEqual(self.app.workspace(self.context), self.before)

    def test_old_feasible_generation_cannot_describe_changed_final_conditions(self):
        service, _ = self.service([
            call("generate_my_plan", {"constraint_patch": {"locked_section_ids": ["DEMO-ML-B"], "friday_free": "soft"}}, "generate"),
            call("validate_my_plan", {"section_ids": ["DEMO-PE-A", "DEMO-ML-B", "DEMO-SIM-A"],
                                      "constraint_patch": {"friday_free": "hard"}}, "validate"),
            answer("feasible"),
        ])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["code"], "inconsistent_outcome")
        generation, validation = [item["result"] for item in response["tool_results"]]
        self.assertEqual(generation["status"], "feasible")
        self.assertEqual(validation["result"], "fail")
        self.assertEqual(generation["constraints"]["friday_free"], "soft")
        self.assertEqual(validation["constraints"]["friday_free"], "hard")
        self.assertEqual(self.app.workspace(self.context), self.before)

    def test_latest_failed_validation_cannot_be_presented_as_pass(self):
        prior_pass = call("validate_my_plan", {"section_ids": ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"]}, "valid")
        failure = call("validate_my_plan", {"section_ids": ["DEMO-ML-A", "DEMO-SIM-A"]}, "invalid")
        for earlier in ([], [prior_pass]):
            service, _ = self.service(earlier + [failure, answer("pass")])
            response = service.turn(self.request(), self.context)
            self.assertEqual(response["status"], "failed")
            self.assertEqual(response["code"], "inconsistent_outcome")
            self.assertEqual(response["tool_results"][-1]["result"]["result"], "fail")
            self.assertEqual(self.app.workspace(self.context), self.before)

    def test_same_plan_later_conflicting_validation_blocks_generation_summary(self):
        service, _ = self.service([
            call("generate_my_plan", {"constraint_patch": {}}, "generate"),
            call("validate_my_plan", {"section_ids": ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"]}, "validate"),
            answer("feasible"),
        ])
        original_execute = service._execute
        def conflicting_result(name, *args):
            result, constraints = original_execute(name, *args)
            if name == "validate_my_plan":
                # Fault injection verifies rejection of inconsistent evidence;
                # the real deterministic engine passes this fixture schedule.
                result["result"] = "fail"
                result["plan"]["validation"]["result"] = "fail"
            return result, constraints
        service._execute = conflicting_result
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["code"], "inconsistent_outcome")
        self.assertEqual(response["tool_results"][0]["result"]["status"], "feasible")
        self.assertEqual(response["tool_results"][1]["result"]["result"], "fail")

    def test_other_failed_schedule_does_not_erase_generated_alternatives(self):
        service, _ = self.service([
            call("generate_my_plan", {"constraint_patch": {}}, "generate"),
            call("validate_my_plan", {"section_ids": ["DEMO-ML-A", "DEMO-SIM-A"]}, "validate"),
            answer("feasible"),
        ])
        response = service.turn(self.request(), self.context)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["model_decision"]["source_tool_result_index"], 0)
        self.assertEqual(response["tool_results"][1]["result"]["result"], "fail")
        self.assertIn("3 份", response["reply"])
        self.assertIn("未通过或待确认", response["reply"])

    def test_fabricated_or_previous_citation_cannot_be_used(self):
        for refs in (["REF-" + "a" * 64], []):
            service, _ = self.service([call("search_plan_references", {"query": "Python"}), answer("reference_available", refs)])
            response = service.turn(self.request(), self.context)
            self.assertEqual(response["code"], "unsupported_citation")

    def test_model_does_not_hold_application_lock_and_parallel_turn_is_rejected(self):
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            self.assertTrue(release.wait(3))
            return answer()
        service, _ = self.service([slow])
        request = self.request()
        output = []
        worker = threading.Thread(target=lambda: output.append(service.turn(request, self.context)))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(self.app.lock.acquire(timeout=1))
            self.app.lock.release()
            with self.assertRaises(AccessError) as caught:
                service.turn(request, self.context)
            self.assertEqual(caught.exception.reason, "turn_in_progress")
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output[0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
