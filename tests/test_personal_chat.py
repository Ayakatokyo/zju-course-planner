"""Personal chat persistence coordination with synthetic local tools/models."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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
        return {"reply": "已查询当前本人范围的资料。"}


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

    def run(self, message, data_use="personal_retrieval", history=()):
        if self.app.lock._is_owned():
            raise AssertionError("model execution must not hold the app lock")
        self.authorize()
        result = self.execute("get_my_context", {})
        messages = deepcopy(list(history)) + [{"role": "user", "content": message}]
        reply = self.model.complete(messages, [])
        self.authorize()
        messages.append({"role": "assistant", "content": reply["reply"]})
        return {"status": "completed", "reply": reply["reply"],
                "tool_results": [{"name": "get_my_context", "arguments": {}, "result": result}],
                "citations": [], "reply_origin": "personal_model_evidence", "model_decision": {"kind": "answer", "evidence_ids": []},
                "partial_tools_completed": False}, messages


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

    def test_versions_change_clears_model_history_and_preserves_stale_local_turns(self):
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
        self.assertEqual(len(self.model.calls[2]), 1)
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
