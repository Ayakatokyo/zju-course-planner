"""Synthetic race and intent checks for live-source coordination; no network."""
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

from course_assistant.auth import AccessError, AuthContext, AuthManager, SCHOOL_MODE, SCHOOL_REALM
from course_assistant.personal_chat import DATA_USE_VERSION, PersonalChatService
from course_assistant.school_accounts import SchoolAccountService
from course_assistant.storage import Store
from course_assistant.student_sources import digest
from tests.test_school_accounts import FakeSchoolClient
from tests.test_personal_chat import FakeAuth, FakeModel, FakeSchool


class LiveSourceConnectionRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "synthetic.sqlite3")
        self.auth = AuthManager(self.store, mock_enabled=True)
        self.client = FakeSchoolClient()
        self.school = SchoolAccountService(self.auth, lambda: self.client,
            root=Path(self.temp.name).resolve() / "private" / "school")
        self.addCleanup(self.school.close)
        self.token, _ = self.school.login({"username": "SYNTHETIC-LOGIN", "password": "SYNTHETIC-PASSWORD"})
        self.context = self.auth.authenticate(self.token)

    def test_external_publication_between_outer_check_and_lock_is_not_adopted(self):
        baseline = digest(self.school.personal_chat_scope(self.context)["versions"])
        connection = self.school._connection(self.context)
        entered, receipts, adopts, events = threading.Event(), [], [], []
        def generation_authorize():
            self.auth.require_context(self.context)
            entered.set()
        def strict_before():
            self.assertTrue(connection.lock._is_owned())
            self.auth.require_context(self.context)
            current = digest(self.school.personal_chat_scope(self.context)["versions"])
            if current != baseline:
                raise AccessError("合成外部版本变化", 409, "personal_input_changed")
        def request():
            return self.school.personal_chat_live_tool(self.context, "query_my_records",
                {"operation": "academic", "target_term": "2025-2026-AW"}, receipts=receipts,
                emit=lambda kind, fields: events.append((kind, deepcopy(fields))),
                authorize=generation_authorize, adopt=lambda changed: adopts.append(changed), before=strict_before)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with connection.lock:
                future = pool.submit(request)
                self.assertTrue(entered.wait(2))
                external = self.school.fetch(self.context,
                    {"operation": "academic", "target_term": "2024-2025-SS", "force_refresh": True})
                self.assertEqual(external["data_source"], "live")
                self.assertNotEqual(digest(self.school.personal_chat_scope(self.context)["versions"]), baseline)
            with self.assertRaises(AccessError) as caught:
                future.result(timeout=2)
        self.assertEqual(caught.exception.reason, "personal_input_changed")
        self.assertEqual(len(self.client.request_calls), 1)
        self.assertEqual(self.client.request_calls[0]["target_term"], "2024-2025-SS")
        self.assertEqual((receipts, adopts, events), ([], [], []))


class LiveIntentCoordinatorTests(unittest.TestCase):
    def test_live_second_turn_sends_displayed_dialogue_without_raw_tools_or_reasoning(self):
        class LiveSchool(FakeSchool):
            def personal_chat_live_tool(inner, context, name, args, *, receipts, emit, authorize, adopt, before=None):
                self.assertTrue(callable(before), "the coordinator must pass its strict version check")
                before()
                authorize()
                return inner.personal_chat_tool(context, name, args)
        class RecordingDialogue:
            def __init__(inner, model, authorize, execute):
                inner.model, inner.authorize, inner.execute = model, authorize, execute
            def run(inner, message, *, history=(), **kwargs):
                inner.authorize()
                tool = inner.execute("get_my_context", {})
                messages = deepcopy(list(history)) + [{"role": "user", "content": message}]
                inner.model.complete(messages, [])
                # Old fact/tool/reasoning payloads are genuinely persisted in
                # this controlled first-turn fixture, not inferred from a count.
                messages += [{"role": "assistant", "content": "OLD-SYNTHETIC-FACT-99", "reasoning_content": "OLD-SYNTHETIC-REASONING"},
                             {"role": "tool", "tool_call_id": "old-synthetic", "content": "OLD-SYNTHETIC-TOOL-BODY"}]
                return {"status": "completed", "reply": "已查合成记录", "tool_results": [{"name": "get_my_context", "arguments": {}, "result": tool}],
                    "citations": [], "model_decision": {"kind": "answer", "evidence_ids": []},
                    "reply_origin": "personal_model_evidence", "partial_tools_completed": False}, messages
        with tempfile.TemporaryDirectory() as temporary:
            app = SimpleNamespace(auth=FakeAuth(), school=LiveSchool(), lock=threading.RLock(),
                                  store=Store(Path(temporary) / "synthetic.sqlite3"))
            model = FakeModel()
            service = PersonalChatService(app, model)
            context = AuthContext("SYNTHETIC-P", "SYNTHETIC-SESSION", "SYNTHETIC-A", SCHOOL_REALM, "SYNTHETIC-BINDING", SCHOOL_MODE)
            def body(message, previous=None):
                result = {"message": message, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                    "consent_personal": True, "data_use_version": DATA_USE_VERSION}
                if previous:
                    result.update(conversation_id=previous["conversation_id"], expected_turn=previous["turn_id"])
                return result
            with patch.object(service, "_dialogue", side_effect=lambda authorize, execute: RecordingDialogue(model, authorize, execute)):
                first = service.turn(body("合成数学分析成绩是多少"), context)
                stored = app.store.chat_one(context, first["conversation_id"])["state"]
                self.assertIn("OLD-SYNTHETIC-TOOL-BODY", json.dumps(stored["messages"]))
                second = service.turn(body("第二门呢？", first), context)
            self.assertEqual(second["status"], "completed")
            self.assertEqual([row["content"] for row in model.calls[1] if row["role"] == "user"],
                             ["合成数学分析成绩是多少", "第二门呢？"])
            self.assertEqual([row["content"] for row in model.calls[1] if row["role"] == "assistant"], ["已查合成记录"])
            self.assertNotIn("OLD-SYNTHETIC", json.dumps(model.calls[1]))
            self.assertTrue(second["history_reset"])


if __name__ == "__main__":
    unittest.main()
