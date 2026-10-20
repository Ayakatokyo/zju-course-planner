"""Personal chat persistence coordination with synthetic local tools/models."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from course_assistant.auth import AccessError, AuthContext, SCHOOL_MODE, SCHOOL_REALM
from course_assistant.model_client import ModelError
from course_assistant.personal_chat import DATA_USE_VERSION, PersonalChatService
from course_assistant.storage import Store
from course_assistant.personal_tasks import PREPARE_RECOMMENDATION_TOOL
from course_assistant.recommendation_readiness import assess_recommendation_readiness


class FakeModel:
    def __init__(self):
        self.configured, self.model, self.calls, self.callback = True, "synthetic-model-1", [], None

    def status(self):
        return {"configured": self.configured, "provider": "synthetic-provider", "model": self.model,
                "remote": True, "data_scope": "THIS MUST BE OVERRIDDEN"}

    def complete(self, messages, tools):
        self.calls.append(deepcopy(messages))
        if self.callback:
            self.callback()
        return {"reply": getattr(self, "response_reply", "已查询当前本人范围的资料。")}


class FakeAuth:
    def __init__(self):
        self.active = True

    def require_context(self, context):
        if not self.active or not isinstance(context, AuthContext):
            raise AccessError()


class FakeSchool:
    def __init__(self):
        self.epoch, self.version, self.calls = 1, "SYNTHETIC-V1", []

    def personal_chat_scope(self, context):
        return {"scope_key": f"{context.session_ref}:{self.epoch}",
                "versions": {"snapshot": self.version, "source_alias": "SYNTHETIC-PRIVATE-ALIAS"}}

    def local_chat_status(self, context):
        return {"configured": False, "available": False, "remote": False,
                "local": {"available": True}, "capabilities": {"local_records": True, "real_recommendation": True}}

    def personal_chat_tool(self, context, name, args):
        self.calls.append((context.student_id, name, deepcopy(args)))
        return {"status": "ok", "records": [{"course_name_raw": "SYNTHETIC-LOCAL-ONLY", "earned_credits": None}]}


class FakeDialogue:
    def __init__(self, app, model, authorize, execute):
        self.app, self.model, self.authorize, self.execute = app, model, authorize, execute

    def run(self, message, data_use="personal_retrieval", history=(), execution_context=None, task_context=None,
            clarification_context=None, emit=None):
        self.app.received_execution_context = deepcopy(execution_context)
        self.app.received_task_context = deepcopy(task_context)
        self.app.received_clarification_context = deepcopy(clarification_context)
        if self.app.lock._is_owned():
            raise AssertionError("model execution must not hold the app lock")
        self.authorize()
        result = self.execute("get_my_context", {})
        tool_results = [{"name": "get_my_context", "arguments": {}, "result": result}]
        for name, args in getattr(self.app, "dialogue_tool_calls", []):
            tool_results.append({"name": name, "arguments": deepcopy(args), "result": self.execute(name, args)})
        messages = deepcopy(list(history)) + [{"role": "user", "content": message}]
        reply = self.model.complete(messages, [])
        self.authorize()
        messages.append({"role": "assistant", "content": reply["reply"]})
        response = {"status": "completed", "reply": reply["reply"],
                "tool_results": deepcopy(getattr(self.app, "dialogue_tool_results", tool_results)),
                "citations": [], "reply_origin": getattr(self.app, "reply_origin", "personal_model_evidence"),
                "model_decision": deepcopy(getattr(self.app, "model_decision", {"kind": "answer", "evidence_ids": []})),
                "partial_tools_completed": False}
        if hasattr(self.app, "dialogue_task_progress"):
            response["task_progress"] = deepcopy(self.app.dialogue_task_progress)
        return response, messages


class LiveSyntheticSchool(FakeSchool):
    """Exercise service orchestration, with invented receipts and no network."""
    def __init__(self):
        super().__init__()
        self.reads = []
        self.fail_operation = None
        self.lexical_preparations = []
        self.policy_calls = []

    def prepare_personal_chat(self, context, question, **kwargs):
        self.lexical_preparations.append(question)
        raise AssertionError("live task selection must precede source acquisition")

    def personal_chat_live_tool(self, context, name, args, *, receipts, emit, authorize, adopt, before, policy_context=None):
        before()
        self.policy_calls.append((name, deepcopy(policy_context)))
        self.calls.append((context.student_id, name, deepcopy(args)))
        current = {"status": "available", "data_kind": "personal_source", "source_receipts": deepcopy(receipts),
            "api_operations": ["academic", "major_academic", "timetable", "exams"],
            "capabilities": {"live_record_query": True, "curriculum_local_search": True,
                "read_only_acquisition": True, "course_catalog_search": True, "recommendation_input_check": True},
            "course_catalog": {"status": "ready"}}
        if name == "get_my_context":
            return current
        if name == "check_recommendation_inputs":
            return {"status": "available", "data_kind": "personal_source", "check_executed": True,
                    "readiness": assess_recommendation_readiness(current, **args)}
        operation = "curriculum" if name == "search_my_curriculum" else args["operation"]
        term = args.get("target_term")
        if not any(row.get("operation") == operation and row.get("target_term") == term for row in receipts):
            self.reads.append((operation, term))
            failed = self.fail_operation == operation
            receipts.append({"operation": operation, "target_term": term,
                "status": "failed" if failed else "acquired_pending_review" if operation == "curriculum" else "complete_within_scope",
                "ready": not failed, "data_source": "none" if failed else "live"})
            self.version = "SYNTHETIC-V" + str(len(self.reads) + 1)
            adopt(True)
        authorize()
        return {"status": "not_ready" if self.fail_operation == operation else "pending_review" if operation == "curriculum" else "complete_within_scope",
            "operation": operation, "target_term": term, "mode": args.get("mode", "records"),
            "records": [] if self.fail_operation == operation else [{"course_name_raw": "本轮合成课程"}]}


class PersonalChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = SimpleNamespace(auth=FakeAuth(), school=FakeSchool(), lock=threading.RLock(),
                                   store=Store(Path(self.temp.name) / "synthetic.sqlite3"))
        self.model = FakeModel()
        self.service = PersonalChatService(self.app, self.model)
        self.context = AuthContext("SYNTHETIC-P-A", "SYNTHETIC-SESSION-A", "SYNTHETIC-A", SCHOOL_REALM,
                                   "SYNTHETIC-BINDING-1", SCHOOL_MODE)
        self.other = AuthContext("SYNTHETIC-P-B", "SYNTHETIC-SESSION-B", "SYNTHETIC-B", SCHOOL_REALM,
                                 "SYNTHETIC-BINDING-1", SCHOOL_MODE)
        self.factory = patch.object(self.service, "_dialogue", side_effect=lambda authorize, execute:
                                    FakeDialogue(self.app, self.model, authorize, execute))
        self.factory.start()
        self.addCleanup(self.factory.stop)

    @staticmethod
    def body(message="查询这份培养资料", previous=None, **changes):
        value = {"message": message, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                 "consent_personal": True, "data_use_version": DATA_USE_VERSION}
        if previous:
            value.update(conversation_id=previous["conversation_id"], expected_turn=previous["turn_id"])
        value.update(changes)
        return value

    def turn(self, previous=None, **changes):
        return self.service.turn(self.body(previous=previous, **changes), self.context)

    def assert_reason(self, reason, callback, *args, **kwargs):
        with self.assertRaises(AccessError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)

    def enable_private_runs(self):
        from course_assistant.chat_runs import ChatRunStore
        self.app.store.path = self.app.store.path.resolve()
        self.app.store.path.chmod(0o600)
        self.app.store.path.parent.chmod(0o700)
        self.service.runs = ChatRunStore(self.app.store, authorize=self.service._run_authorize)

    def test_status_uses_actual_model_and_separate_personal_capabilities(self):
        status = self.service.status(self.context)
        self.assertTrue(status["configured"])
        self.assertTrue(status["available"])
        self.assertTrue(status["remote"])
        self.assertTrue(status["capabilities"]["model_dialogue"])
        self.assertFalse(status["capabilities"]["real_recommendation"])
        self.assertEqual(status["data_kind"], "personal_source")
        self.assertNotIn("SYNTHETIC-PRIVATE-ALIAS", str(status["versions"]))
        self.assertNotIn("SYNTHETIC-SESSION-A", str(status))
        self.assertNotEqual(status["data_scope"], "THIS MUST BE OVERRIDDEN")
        self.assertEqual(status["personal_data_use"]["version"], DATA_USE_VERSION)
        self.assertIn("教务记录", status["personal_data_use"]["summary"])

    def test_blocked_school_status_keeps_actual_model_configuration_without_raising(self):
        blocked = {"local": {"available": False, "reason": "source_session_expired"},
                   "capabilities": {"local_records": False, "local_evidence": False}}
        with patch.object(self.app.school, "local_chat_status", return_value=blocked), \
                patch.object(self.app.school, "personal_chat_scope", side_effect=AssertionError("blocked scope must not be read")):
            status = self.service.status(self.context)
        self.assertTrue(status["configured"])
        self.assertFalse(status["available"])
        self.assertFalse(status["local"]["available"])
        self.assertFalse(status["capabilities"]["model_dialogue"])

    def test_school_scope_callbacks_never_run_under_global_app_lock(self):
        scope = self.app.school.personal_chat_scope
        def checked(context):
            self.assertFalse(self.app.lock._is_owned())
            return scope(context)
        with patch.object(self.app.school, "personal_chat_scope", side_effect=checked):
            self.service.status(self.context)
            body = self.body()
            first = self.service.turn(body, self.context)
            self.service.turn(body, self.context)
            self.service.history({"conversation_id": first["conversation_id"]}, self.context)
            self.service.conversations(self.context)

    def test_turn_history_and_list_preserve_existing_frontend_shapes(self):
        result = self.turn()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["turn_id"], 1)
        self.assertEqual(result["data_kind"], "personal_source")
        history = self.service.history({"conversation_id": result["conversation_id"]}, self.context)
        self.assertEqual(history["turns"][0]["reply"], result["reply"])
        self.assertEqual(history["option_receipts"], {})
        self.assertFalse(history["in_progress"])
        self.assertFalse(history["is_stale"])
        listing = self.service.conversations(self.context, limit=1)
        self.assertEqual(listing["conversations"][0]["conversation_id"], result["conversation_id"])
        self.assertEqual(listing["conversations"][0]["title"], "查询这份培养资料")

    def test_previous_run_is_bound_to_conversation_without_questions_or_reasoning(self):
        self.enable_private_runs()
        first = self.turn(message="SYNTHETIC-QUESTION-MUST-NOT-BE-IN-DIAGNOSTIC")
        self.assertIsNotNone(first["run_id"])
        self.assertIsNone(self.app.received_execution_context)
        second = self.turn(previous=first, message="解释刚刚的运行记录")
        value = self.app.received_execution_context
        self.assertEqual(second["status"], "completed")
        self.assertEqual(value["run_id"], first["run_id"])
        self.assertEqual(value["status"], "completed")
        for forbidden in ("SYNTHETIC-QUESTION", "reasoning_content", "usage", "request_id", "scope_key", "owner"):
            self.assertNotIn(forbidden, str(value))
        self.turn(message="新对话不能取到另一个对话运行")
        self.assertIsNone(self.app.received_execution_context)

    def test_failed_provider_history_stays_empty_but_actual_failure_is_available(self):
        self.enable_private_runs()
        def fail():
            raise ModelError("模型服务不可用", "model_unavailable")
        self.model.callback = fail
        first = self.turn()
        self.assertEqual(first["status"], "failed")
        self.model.callback = None
        second = self.turn(previous=first, message="为什么刚刚失败")
        value = self.app.received_execution_context
        self.assertEqual(second["status"], "completed")
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_code"], "model_unavailable")
        self.assertTrue(any(event["fields"].get("error_code") == "model_unavailable" for event in value["events"]))
        self.assertEqual(len(self.model.calls[-1]), 1)

    def test_diagnostic_run_from_other_conversation_is_not_adopted(self):
        self.enable_private_runs()
        first, other = self.turn(), self.turn()
        scope, _, _ = self.service._current(self.context)
        state = {"turns": [{"run_id": first["run_id"]}]}
        self.assertIsNone(self.service._execution_context(self.context, state, scope, other["conversation_id"]))
        self.app.school.epoch += 1
        new_scope, _, _ = self.service._current(self.context)
        self.assertIsNone(self.service._execution_context(self.context, state, new_scope, first["conversation_id"]))

    def test_latest_preparation_without_run_id_is_not_attached_to_an_older_run(self):
        from tests.test_personal_tasks import recommendation_row
        self.enable_private_runs()
        first = self.turn()
        scope, _, _ = self.service._current(self.context)
        state = self.app.store.chat_one(self.context, first["conversation_id"])["state"]
        state["turns"].append({"status": "completed", "task_status": "blocked",
            "tool_results": [recommendation_row()]})
        diagnostic = self.service._execution_context(self.context, state, scope, first["conversation_id"])
        self.assertEqual(diagnostic["run_id"], first["run_id"])
        self.assertNotIn("previous_preparation", diagnostic)

    def test_previous_blocked_preparation_explains_counts_without_new_source_reads(self):
        from tests.test_personal_dialogue import ScriptedModel, call, final
        from course_assistant.recommendation_readiness import source_receipt_diagnostics

        class PartialTimetableSchool(LiveSyntheticSchool):
            def personal_chat_live_tool(self, context, name, args, *, receipts, emit, authorize, adopt, before, policy_context=None):
                result = super().personal_chat_live_tool(context, name, args, receipts=receipts, emit=emit,
                    authorize=authorize, adopt=adopt, before=before, policy_context=policy_context)
                if name == "query_my_records" and args.get("operation") == "timetable":
                    receipt = next(row for row in reversed(receipts) if row["operation"] == "timetable")
                    result.update(status="partial_acquisition", data_source="live",
                        coverage={"captured_rows": 3, "reported_total": None, "complete_within_scope": False},
                        records=[{"course_name_raw": "合成课程甲", "time_raw": "周一第1节",
                            "field_states": {"course_name_raw": "known", "time_raw": "known"}},
                            {"course_name_raw": "合成课程乙", "field_states": {"course_name_raw": "known", "time_raw": "unknown"}},
                            {"course_name_raw": "合成课程丙", "field_states": {"course_name_raw": "known", "time_raw": "unknown"}}])
                    receipt.update(status="partial_acquisition",
                        diagnostics=source_receipt_diagnostics("timetable", result))
                return result

        self.app.school = PartialTimetableSchool()
        def preparation_answer(messages, tools):
            value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            return final({"kind": "answer", "claims": [{"kind": "recommendation_readiness",
                "evidence_id": value["evidence_id"], "sections": ["next_step"]}]})

        def request_previous(messages, tools):
            return call("get_my_run_diagnostics", {})

        def historical_answer(messages, tools):
            value = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            previous = value["previous_preparation"]
            self.assertTrue(previous["historical_only"])
            self.assertTrue(previous["fresh_sources_required"])
            return final({"kind": "answer", "claims": [{"kind": "recommendation_readiness",
                "evidence_id": previous["evidence_id"], "sections": ["next_step"]}]})

        self.factory.stop()
        model = ScriptedModel([
            call(PREPARE_RECOMMENDATION_TOOL, self.recommendation_task()), preparation_answer,
            request_previous, historical_answer])
        model.status = lambda: {"configured": True, "remote": True,
            "provider": "SyntheticProvider", "model": "scripted-only"}
        self.service = PersonalChatService(self.app, model)
        self.enable_private_runs()
        first = self.turn(message="按限定范围准备课表组合")
        self.assertEqual(first["status"], "completed", first)
        self.assertEqual(first["task_status"], "blocked")
        self.assertEqual(self.app.school.reads, [("major_academic", "ALL-HISTORY"),
            ("curriculum", None), ("timetable", "2026-2027-AW")])
        reads = deepcopy(self.app.school.reads)
        second = self.turn(first, message="解释上一轮具体为什么不能继续")
        self.assertEqual(second["status"], "completed", second)
        self.assertEqual(self.app.school.reads, reads)
        self.assertEqual([row["name"] for row in second["tool_results"]], ["get_my_context", "get_my_run_diagnostics"])
        self.assertEqual(second["tool_results"][0]["result"]["source_receipts"], [])
        self.assertIn("上一轮准备检查的记录，本轮未重新读取教务资料", second["reply"])
        self.assertIn("取得3条记录", second["reply"])
        self.assertIn("接口未声明总数，覆盖无法确认", second["reply"])
        self.assertIn("2条仍有课时缺少文本或结构化时间证据", second["reply"])
        self.assertTrue(any(row.get("historical_only") is True for row in second["citations"]))

    def test_corrupt_or_unavailable_diagnostics_do_not_break_the_next_task(self):
        self.enable_private_runs()
        first = self.turn()
        for patch_kwargs in ({"side_effect": ValueError("SYNTHETIC-CORRUPT-LOG")}, {"return_value": {}},
                             {"return_value": {"run": {"conversation_id": first["conversation_id"], "status": "completed"}}}):
            with self.subTest(patch_kwargs=patch_kwargs), patch.object(self.service.runs, "read", **patch_kwargs):
                result = self.turn(previous=first, message="继续正常提问")
                self.assertEqual(result["status"], "completed")
                self.assertIsNone(self.app.received_execution_context)
                first = result

    def test_explicit_policy_and_consent_required_before_any_reservation(self):
        for changes in ({"consent_personal": False}, {"consent_personal": 1}, {"data_use_version": "old"},
                        {"consent_remote": True}, {"owner_student_id": self.context.student_id}):
            expected = "invalid_chat_request" if "consent_remote" in changes or "owner_student_id" in changes else "personal_data_consent_required"
            self.assert_reason(expected, self.service.turn, self.body(**changes), self.context)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM owned_conversations").fetchone()[0], 0)
        self.assertEqual(self.model.calls, [])

    def test_request_ids_message_bounds_and_credentials_are_checked(self):
        for changes in ({"request_id": "not-a-uuid"}, {"expected_turn": True}, {"message": " "},
                        {"message": "x" * 2001}, {"conversation_id": "../private"}):
            self.assert_reason("invalid_chat_request", self.service.turn, self.body(**changes), self.context)
        for text in ("密码是SYNTHETIC", "Cookie: SYNTHETIC", "sk-abcdefghijklmnop", "学号1234567890"):
            self.assert_reason("sensitive_chat_input", self.service.turn, self.body(message=text), self.context)
        self.assertEqual(self.model.calls, [])

    def test_unconfigured_model_never_reserves_or_calls_tools(self):
        self.model.configured = False
        self.assertFalse(self.service.status(self.context)["available"])
        self.assert_reason("model_not_configured", self.service.turn, self.body(), self.context)
        self.assertEqual(self.app.school.calls, [])
        self.assertEqual(self.service.conversations(self.context)["conversations"], [])

    def test_current_scope_replay_is_idempotent_and_never_reexecutes_model(self):
        body = self.body()
        first = self.service.turn(body, self.context)
        again = self.service.turn(body, self.context)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["conversation_id"], first["conversation_id"])
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.app.school.calls), 1)

    def test_old_epoch_conversations_titles_references_and_request_replays_are_denied(self):
        body = self.body()
        first = self.service.turn(body, self.context)
        self.app.school.epoch += 1
        self.assertEqual(self.service.conversations(self.context)["conversations"], [])
        self.assert_reason("inaccessible_object", self.service.history, {"conversation_id": first["conversation_id"]}, self.context)
        self.assert_reason("inaccessible_object", self.turn, first)
        self.assert_reason("request_id_reused", self.service.turn, body, self.context)
        self.assertEqual(len(self.model.calls), 1)

    def test_owner_realm_and_session_isolation(self):
        first = self.turn()
        self.assertEqual(self.service.conversations(self.other)["conversations"], [])
        self.assert_reason("inaccessible_object", self.service.history, {"conversation_id": first["conversation_id"]}, self.other)
        session = AuthContext(self.context.principal_id, "SYNTHETIC-SESSION-NEW", self.context.student_id,
                              SCHOOL_REALM, self.context.binding_version, SCHOOL_MODE)
        self.assert_reason("inaccessible_object", self.service.history, {"conversation_id": first["conversation_id"]}, session)
        mock = AuthContext("demo-a", "SYNTHETIC-MOCK", "SYNTHETIC-A", "mock_development", "v1", "mock")
        self.assert_reason("school_connection_required", self.service.status, mock)

    def test_versions_change_keeps_intent_without_old_facts_and_preserves_stale_local_turns(self):
        first = self.turn()
        second = self.turn(first, message="继续查询这门课")
        self.assertEqual(len(self.model.calls[1]), 3)
        self.assertFalse(second["history_reset"])
        self.app.school.version = "SYNTHETIC-V2"
        history = self.service.history({"conversation_id": first["conversation_id"]}, self.context)
        self.assertTrue(history["is_stale"])
        self.assertTrue(all(turn["is_stale"] for turn in history["turns"]))
        third = self.turn(second, message="查询当前资料")
        self.assertTrue(third["history_reset"])
        self.assertEqual(len(self.model.calls[2]), 3)
        self.assertTrue(all(row["role"] == "user" for row in self.model.calls[2]))
        self.assertNotIn("SYNTHETIC-LOCAL-ONLY", str(self.model.calls))

    def test_stale_input_replay_is_denied_without_a_model_call(self):
        body = self.body()
        self.service.turn(body, self.context)
        self.app.school.version = "SYNTHETIC-V2"
        self.assert_reason("personal_input_changed", self.service.turn, body, self.context)
        self.assertEqual(len(self.model.calls), 1)

    def test_provider_change_between_turns_resets_outbound_history(self):
        first = self.turn()
        self.model.model = "synthetic-model-2"
        second = self.turn(first)
        self.assertTrue(second["history_reset"])
        self.assertEqual(len(self.model.calls[1]), 1)

    def test_task_scope_survives_fresh_sources_but_not_model_change(self):
        self.app.dialogue_tool_results = [{"name": "query_my_records",
            "arguments": {"operation": "academic", "mode": "summary", "query": ""},
            "result": {"status": "complete_within_scope", "operation": "academic",
                "target_term": "2023-2024-AW", "mode": "summary", "records": [{"grade_raw": "NEVER-CARRY-FACT"}],
                "observation_id": "NEVER-CARRY-OBS", "next_page_token": "NEVER-CARRY-PAGE"}}]
        first = self.turn(message="请概括2023-2024秋冬成绩")
        self.app.school.version = "SYNTHETIC-FRESH-V2"
        second = self.turn(first, message="那春夏呢？")
        hint = self.app.received_task_context
        self.assertEqual(hint["tasks"], [{"tool": "query_my_records", "operation": "academic",
            "target_term": "2023-2024-AW", "mode": "summary", "query": ""}])
        self.assertNotIn("NEVER-CARRY", str(hint))
        self.assertFalse(hint["facts_reusable"])
        self.model.model = "synthetic-model-2"
        self.turn(second, message="再查询")
        self.assertIsNone(self.app.received_task_context)

    @staticmethod
    def recommendation_task(**changes):
        return {"recommendation_kind": "timetable", "target_term": "2026-2027-AW",
            "course_scope": {"kind": "topic", "queries": ["数据分析"]},
            "hard_preferences": ["周五不排课"], "soft_preferences": [], "check_exam_conflicts": False,
            "request_basis": "current_message", **changes}

    def test_recommendation_prepare_executes_minimum_sources_once_and_rechecks_current_receipts(self):
        self.app.school = LiveSyntheticSchool()
        task = self.recommendation_task()
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL, task)] * 2
        result = self.turn(message="帮我准备限定范围的推荐")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.app.school.reads,
            [("major_academic", "ALL-HISTORY"), ("curriculum", None), ("timetable", "2026-2027-AW")])
        readiness = result["tool_results"][-1]["result"]["readiness"]
        self.assertEqual({source["id"]: source["status"] for source in readiness["sources"]},
            {"course_reference": "available", "academic_records": "available", "curriculum": "available",
             "existing_timetable": "available", "exams": "missing"})
        self.assertFalse(readiness["can_generate"])
        prepared = result["tool_results"][1]["result"]["prepared_sources"]
        self.assertEqual([row["name"] for row in prepared], ["query_my_records", "search_my_curriculum", "query_my_records"])
        self.assertEqual([row["result"]["operation"] for row in prepared], ["major_academic", "curriculum", "timetable"])
        self.assertEqual(prepared[-1]["arguments"]["limit"], 15)
        self.assertEqual(prepared[-1]["result"]["records"], [{"course_name_raw": "本轮合成课程"}])
        self.assertEqual(result["tool_results"][-1]["result"]["prepared_sources"], [])
        self.app.dialogue_tool_calls = []
        following = self.turn(result, message="那春夏呢")
        hints = self.app.received_task_context["tasks"]
        self.assertEqual(hints, [{"tool": PREPARE_RECOMMENDATION_TOOL, **task}])
        self.assertFalse(self.app.received_task_context["facts_reusable"])
        self.model.model = "synthetic-model-changed"
        self.turn(following, message="继续")
        self.assertIsNone(self.app.received_task_context)

    def test_check_only_does_not_prepare_or_upgrade_to_recommendation(self):
        self.app.school = LiveSyntheticSchool()
        args = {"recommendation_kind": "courses", "target_term": "2026-2027-AW"}
        self.app.dialogue_tool_calls = [("check_recommendation_inputs", args)]
        first = self.turn(message="只检查2026-2027秋冬课表推荐输入，不读取成绩或培养资料")
        self.assertEqual(first["task_status"], "completed")
        self.assertNotIn("task_progress", first)
        self.assertEqual(self.app.school.reads, [])
        self.assertEqual(self.app.school.lexical_preparations, [])
        self.app.dialogue_tool_calls = []
        self.turn(first, message="换成春夏呢")
        self.assertEqual(self.app.received_task_context["tasks"], [{"tool": "check_recommendation_inputs", **args}])
        self.assertEqual(self.app.school.reads, [])

    def test_trusted_recommendation_progress_is_partial_or_blocked_and_saved_in_history(self):
        for progress, expected in (({"kind": "recommendation", "status": "prepared", "scope": "formal", "reason": "source_prepared"}, "partial"),
                ({"kind": "recommendation", "status": "limited_result", "scope": "course_data_test", "reason": "test_result"}, "partial"),
                ({"kind": "recommendation", "status": "blocked", "scope": "formal", "reason": "formal_dependencies_unverified"}, "blocked"),
                ({"kind": "recommendation", "status": "blocked", "scope": "course_data_test", "reason": "source_unavailable"}, "blocked")):
            with self.subTest(progress=progress):
                self.app.dialogue_task_progress = progress
                result = self.turn(message="限定课程任务")
                self.assertEqual(result["task_status"], expected)
                self.assertEqual(result["task_progress"], progress)
                stored = self.app.store.chat_one(self.context, result["conversation_id"])["state"]["turns"][-1]
                self.assertEqual(stored["task_status"], expected)
                self.assertEqual(stored["task_progress"], progress)
                history = self.service.history({"conversation_id": result["conversation_id"]}, self.context)
                self.assertEqual(history["turns"][-1]["task_progress"], progress)
                intent = self.service._intent_history({"turns": [stored]})
                self.assertEqual(intent, [{"role": "user", "content": "限定课程任务"}])

    def test_invalid_or_formal_completed_progress_is_rejected_without_persisted_result(self):
        for progress in ({"kind": "recommendation", "status": "completed", "scope": "formal", "reason": "test_result"},
                {"kind": "recommendation", "status": "limited_result", "scope": "formal", "reason": "test_result"},
                {"kind": "recommendation", "status": "limited_result", "scope": "course_data_test", "reason": "Cookie: PRIVATE"}):
            with self.subTest(progress=progress):
                self.app.dialogue_task_progress = progress
                result = self.turn(message="限定课程任务")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["task_status"], "failed")
                self.assertNotIn("task_progress", result)
                self.assertEqual(result["tool_results"], [])

    def test_client_cannot_submit_task_progress_as_a_service_result(self):
        with self.assertRaises(AccessError):
            self.service.turn(self.body(task_progress={"kind": "recommendation", "status": "completed",
                "scope": "formal", "reason": "test_result"}), self.context)
        self.assertEqual(self.model.calls, [])

    def test_clarification_cannot_also_claim_a_recommendation_result(self):
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify", "request_basis": "current_message"}
        self.app.dialogue_task_progress = {"kind": "recommendation", "status": "limited_result",
            "scope": "course_data_test", "reason": "test_result"}
        result = self.turn(message="推荐")
        self.assertEqual(result["task_status"], "failed")
        self.assertNotIn("task_progress", result)

    def test_live_clarification_has_no_lexical_read_and_regular_query_still_reads_fresh_scope(self):
        self.app.school = LiveSyntheticSchool()
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify", "request_basis": "current_message"}
        first = self.turn(message="帮我根据课表和成绩推荐一下")
        self.assertEqual(first["task_status"], "awaiting_user")
        self.assertEqual(self.app.school.reads, [])
        self.assertEqual(self.app.school.lexical_preparations, [])
        self.app.reply_origin = "personal_model_evidence"
        self.app.model_decision = {"kind": "answer", "evidence_ids": []}
        self.app.dialogue_tool_calls = [("query_my_records", {"operation": "academic",
            "target_term": "2026-2027-AW", "query": "", "mode": "summary"})]
        self.turn(first, message="换题，概括2026-2027秋冬成绩")
        self.assertEqual(self.app.school.reads, [("academic", "2026-2027-AW")])

    def test_recommendation_failed_source_is_not_retried_or_replaced_by_old_facts(self):
        self.app.school = LiveSyntheticSchool()
        self.app.school.fail_operation = "major_academic"
        task = self.recommendation_task(recommendation_kind="courses")
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL, task)] * 2
        result = self.turn(message="准备限定兴趣的课程参考")
        self.assertEqual(self.app.school.reads, [("major_academic", "ALL-HISTORY"), ("curriculum", None)])
        readiness = result["tool_results"][-1]["result"]["readiness"]
        self.assertEqual(next(source for source in readiness["sources"] if source["id"] == "academic_records")["status"], "failed")
        self.assertFalse(readiness["can_generate"])
        prepared = result["tool_results"][1]["result"]["prepared_sources"]
        failed = next(row["result"] for row in prepared if row["result"]["operation"] == "major_academic")
        self.assertEqual(failed["status"], "not_ready")
        self.assertEqual(failed["records"], [])
        self.assertEqual(result["tool_results"][-1]["result"]["prepared_sources"], [])

    def test_invalid_recommendation_scope_stops_before_any_read(self):
        self.app.school = LiveSyntheticSchool()
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL,
            self.recommendation_task(course_scope={"kind": "topic", "queries": []}))]
        result = self.turn(message="推荐")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.app.school.reads, [])
        self.assertEqual(result["tool_results"], [])

    def test_pending_prepare_without_server_clarification_is_rejected_before_reading(self):
        self.app.school = LiveSyntheticSchool()
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL,
            self.recommendation_task(request_basis="pending_task"))]
        with self.assertRaisesRegex(AccessError, "当前没有可继续的待补请求") as rejected:
            self.turn(message="就是第6到8节")
        self.assertEqual(rejected.exception.reason, "clarification_context_missing")
        self.assertEqual(self.app.school.reads, [])
        self.assertTrue(all(not policy["pending_messages"] for _, policy in self.app.school.policy_calls))

    def test_real_dialogue_can_recover_missing_pending_basis_before_actual_source_preparation(self):
        from tests.test_personal_dialogue import ScriptedModel, call, final

        self.app.school = LiveSyntheticSchool()
        task = self.recommendation_task(recommendation_kind="courses", hard_preferences=[])
        def correct_basis(messages, tools):
            diagnostic = json.loads(messages[-1]["content"])
            self.assertEqual(diagnostic["code"], "clarification_context_missing")
            self.assertFalse(diagnostic["query_executed"])
            self.assertEqual(diagnostic["allowed_request_basis"], ["current_message"])
            self.assertEqual(self.app.school.reads, [])
            return call(PREPARE_RECOMMENDATION_TOOL, task, "current-basis")

        def typed_preparation(messages, tools):
            evidence = json.loads(messages[-1]["content"])["evidence_id"]
            return final({"kind": "answer", "claims": [{"kind": "recommendation_readiness",
                "evidence_id": evidence, "sections": ["sources"]}]})

        model = ScriptedModel([call(PREPARE_RECOMMENDATION_TOOL, {**task, "request_basis": "pending_task"}),
            correct_basis, typed_preparation])
        self.factory.stop()
        self.service = PersonalChatService(self.app, model)
        result = self.turn(message="按当前限定范围准备合成课程推荐资料")
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["task_status"], "partial")
        self.assertEqual(self.app.school.reads, [("major_academic", "ALL-HISTORY"), ("curriculum", None)])
        rejected = result["tool_results"][1]
        self.assertTrue(rejected["error"])
        self.assertEqual(rejected["result"]["code"], "clarification_context_missing")
        check_policy = next(policy for name, policy in self.app.school.policy_calls
            if name == "check_recommendation_inputs")
        self.assertEqual(check_policy["pending_messages"], [])

    def test_message_protocol_detail_reaches_one_controlled_error_event(self):
        from tests.test_personal_dialogue import ScriptedModel, call
        model = ScriptedModel([{"role": "assistant", "content": None, "tool_calls": [
            call("get_my_context", identifier=f"call-{i}")["tool_calls"][0] for i in range(4)]}])
        model.status = lambda: {"configured": True, "remote": True, "provider": "SyntheticProvider", "model": "scripted-only"}
        self.factory.stop()
        self.app.store.path = self.app.store.path.resolve()
        self.app.store.path.chmod(0o600)
        self.service = PersonalChatService(self.app, model)
        with patch.object(self.service.runs, "event", wraps=self.service.runs.event) as recorded:
            result = self.turn(message="合成协议诊断")
        self.assertEqual(result["code"], "model_protocol_error")
        self.assertEqual(result["validation_diagnostics"][0]["failure_detail"], "tool_batch_count_exceeded")
        errors = [entry.args[-1] for entry in recorded.call_args_list if entry.args[2] == "error"]
        self.assertEqual(errors, [{"error_code": "model_protocol_error", "stage": "answer",
            "failure_detail": "tool_batch_count_exceeded"}])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual([name for _, name, _ in self.app.school.calls], ["get_my_context"])

    def test_arbitrary_protocol_exception_text_cannot_become_error_detail(self):
        from tests.test_personal_dialogue import ScriptedModel
        from course_assistant.personal_dialogue import PersonalDialogueError
        error = PersonalDialogueError("model_protocol_error")
        error.validation_diagnostic = {"error_code": "model_protocol_error", "failure_detail": "UNTRUSTED-RAW-DETAIL"}
        self.factory.stop()
        self.app.store.path = self.app.store.path.resolve()
        self.app.store.path.chmod(0o600)
        model = ScriptedModel([error])
        model.status = lambda: {"configured": True, "remote": True, "provider": "SyntheticProvider", "model": "scripted-only"}
        self.service = PersonalChatService(self.app, model)
        with patch.object(self.service.runs, "event", wraps=self.service.runs.event) as recorded:
            result = self.turn(message="合成协议诊断")
        errors = [entry.args[-1] for entry in recorded.call_args_list if entry.args[2] == "error"]
        self.assertEqual(errors, [{"error_code": "model_protocol_error", "stage": "answer"}])
        self.assertEqual(result["validation_diagnostics"], [])
        self.assertNotIn("UNTRUSTED", json.dumps(errors))

    def test_pending_prepare_passes_only_server_user_intent_in_internal_policy(self):
        self.app.school = LiveSyntheticSchool()
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify", "request_basis": "current_message"}
        self.model.response_reply = "助手问题标记：请给出限定课程范围和下午节次？"
        original = "请给本学期课程数据测试，周三下午必须空"
        first = self.turn(message=original)
        self.app.model_decision["request_basis"] = "pending_task"
        second = self.turn(first, message="课程范围是合成算法")
        self.app.reply_origin = "personal_model_evidence"
        self.app.model_decision = {"kind": "answer", "evidence_ids": []}
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL,
            self.recommendation_task(request_basis="pending_task", hard_preferences=["周三第6到8节不能排课"]))]
        self.app.school.policy_calls.clear()
        result = self.turn(second, message="就是第6到8节")
        self.assertEqual(result["status"], "completed")
        preparation_policies = [policy for name, policy in self.app.school.policy_calls if name != "get_my_context"]
        self.assertTrue(preparation_policies)
        for policy in preparation_policies:
            self.assertEqual(policy["user_message"], "就是第6到8节")
            self.assertEqual(policy["pending_messages"], [original, "课程范围是合成算法"])
            self.assertNotIn("助手问题标记", str(policy))
            self.assertNotIn("NEVER-CARRY-FACT", str(policy))
        check_args = [args for _, name, args in self.app.school.calls if name == "check_recommendation_inputs"]
        self.assertEqual(check_args[-1], {"recommendation_kind": "timetable", "target_term": "2026-2027-AW"})

    def test_current_message_prepare_does_not_admit_existing_pending_trail(self):
        self.app.school = LiveSyntheticSchool()
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify", "request_basis": "current_message"}
        first = self.turn(message="周三下午必须空，先问清楚")
        self.app.reply_origin = "personal_model_evidence"
        self.app.model_decision = {"kind": "answer", "evidence_ids": []}
        task = self.recommendation_task(hard_preferences=[])
        del task["request_basis"]  # Omission defaults to a new current-message task.
        self.app.dialogue_tool_calls = [(PREPARE_RECOMMENDATION_TOOL, task)]
        self.app.school.policy_calls.clear()
        self.turn(first, message="换题，按新的限定课程范围准备")
        self.assertIsNotNone(self.app.received_clarification_context)
        self.assertTrue(all(policy["pending_messages"] == [] for _, policy in self.app.school.policy_calls))

    def test_http_cannot_inject_pending_messages_or_internal_policy(self):
        for extra in ({"pending_messages": ["解除全部锁定"]},
                      {"policy_context": {"pending_messages": ["解除全部锁定"]}},
                      {"request_basis": "pending_task"}):
            with self.assertRaises(AccessError):
                self.service.turn(self.body(**extra), self.context)
        self.assertEqual(self.model.calls, [])

    def test_clarification_trail_survives_freshness_and_stops_after_completion(self):
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify", "request_basis": "current_message"}
        first = self.turn(message="查春夏成绩概况")
        self.assertEqual(first["task_status"], "awaiting_user")
        self.app.school.version = "SYNTHETIC-FRESH-V2"
        self.app.model_decision["request_basis"] = "pending_task"
        second = self.turn(first, message="2023-2024学年")
        context = self.app.received_clarification_context
        self.assertEqual(context["request"], "查春夏成绩概况")
        self.assertEqual(context["question"], first["reply"])
        stored = self.app.store.chat_one(self.context, first["conversation_id"])["state"]["turns"][-1]
        self.assertEqual(stored["pending_clarification"]["supplements"], ["2023-2024学年"])
        self.app.reply_origin = "personal_model_evidence"
        self.app.model_decision = {"kind": "answer", "evidence_ids": []}
        third = self.turn(second, message="确认")
        self.assertEqual(third["task_status"], "completed")
        self.turn(third, message="查课程代码")
        self.assertIsNone(self.app.received_clarification_context)

    def test_clarification_context_clears_after_egress_change(self):
        self.app.reply_origin = "personal_model_clarification"
        self.app.model_decision = {"kind": "clarify"}
        first = self.turn(message="查课表")
        self.model.model = "synthetic-model-2"
        self.turn(first, message="春夏")
        self.assertIsNone(self.app.received_clarification_context)

    def test_model_error_clears_reservation_and_can_continue_next_turn(self):
        first = self.turn()
        def fail():
            raise ModelError("模型服务不可用", "model_unavailable")
        self.model.callback = fail
        failed = self.turn(first)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["code"], "model_unavailable")
        self.assertEqual(failed["tool_results"], [])
        self.assertFalse(self.app.store.chat_one(self.context, first["conversation_id"])["in_progress"])
        self.model.callback = None
        final = self.turn(failed)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["turn_id"], 3)

    def test_unexpected_exception_text_is_never_persisted_or_echoed(self):
        def fail():
            raise RuntimeError("Cookie: SYNTHETIC-MUST-NOT-LEAK /private/path")
        self.model.callback = fail
        result = self.turn()
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("MUST-NOT-LEAK", str(result))
        self.assertNotIn("MUST-NOT-LEAK", str(self.service.history({"conversation_id": result["conversation_id"]}, self.context)))

    def test_midturn_input_model_and_scope_changes_discard_private_results_and_finish(self):
        for kind in ("input", "model", "scope"):
            with self.subTest(kind=kind):
                previous = self.turn()
                if kind == "input":
                    change = lambda: setattr(self.app.school, "version", self.app.school.version + "X")
                    reason = "personal_input_changed"
                elif kind == "model":
                    change = lambda: setattr(self.model, "model", self.model.model + "X")
                    reason = "personal_model_changed"
                else:
                    change = lambda: setattr(self.app.school, "epoch", self.app.school.epoch + 1)
                    reason = "personal_scope_changed"
                self.model.callback = change
                self.assert_reason(reason, self.turn, previous)
                self.model.callback = None
                row = self.app.store.chat_one(self.context, previous["conversation_id"])
                self.assertFalse(row["in_progress"])
                self.assertEqual(row["state"]["turns"][-1]["tool_results"], [])
                self.assertEqual(row["state"]["messages"], [])

    def test_logout_during_inference_is_not_blocked_and_returns_no_private_payload(self):
        first = self.turn()
        started, release = threading.Event(), threading.Event()
        def wait():
            started.set()
            release.wait(3)
        self.model.callback = wait
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.turn, first)
            self.assertTrue(started.wait(1))
            acquired = self.app.lock.acquire(timeout=0.5)
            self.assertTrue(acquired)
            if acquired:
                self.app.auth.active = False
                self.app.lock.release()
            release.set()
            with self.assertRaises(AccessError):
                future.result(timeout=2)
        row = self.app.store.chat_one(self.context, first["conversation_id"])
        self.assertFalse(row["in_progress"])
        self.assertEqual(row["state"]["turns"][-1]["tool_results"], [])
        self.assertEqual(row["state"]["turns"][-1]["citations"], [])

    def test_parallel_turn_is_rejected_without_holding_inference_lock(self):
        first = self.turn()
        started, release = threading.Event(), threading.Event()
        self.model.callback = lambda: (started.set(), release.wait(3))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.turn, first)
            self.assertTrue(started.wait(1))
            try:
                self.assert_reason("turn_in_progress", self.turn, first)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=2)["status"], "completed")

    def test_history_and_list_schema_reject_additional_scope_and_bad_limit(self):
        first = self.turn()
        self.assert_reason("invalid_chat_request", self.service.history,
                           {"conversation_id": first["conversation_id"], "owner": "other"}, self.context)
        for limit in (True, 0, 101, "5"):
            self.assert_reason("invalid_chat_limit", self.service.conversations, self.context, limit)


if __name__ == "__main__":
    unittest.main()
