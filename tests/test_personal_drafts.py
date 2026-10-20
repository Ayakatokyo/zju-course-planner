"""Synthetic-only draft isolation, expiry, concurrency and accepted-send tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch

from course_assistant.auth import AccessError
from course_assistant.model_client import ModelError
from course_assistant.personal_chat import SENSITIVE
from course_assistant.personal_drafts import PersonalDraftService
from course_assistant.server import Application
from tests import test_personal_chat as chat_fixture


class PersonalDraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.store = PersonalDraftService(SENSITIVE, clock=lambda: self.now)
        self.key = ("synthetic-realm", "synthetic-owner", "synthetic-session", "synthetic-binding", "synthetic-local-scope")
        self.authorize = lambda: True

    @staticmethod
    def body(text="未发送的合成问题", revision=0, conversation_id=None):
        return {"text": text, "conversation_id": conversation_id, "expected_revision": revision}

    def assert_code(self, code, callback, *args):
        with self.assertRaises(AccessError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.reason, code)
        return caught.exception

    def test_compare_and_swap_allows_only_one_concurrent_page_write(self):
        self.assertEqual(self.store.read(self.key, self.authorize)["revision"], 0)
        gate = threading.Barrier(2)
        def save(text):
            gate.wait(2)
            try:
                return self.store.write(self.key, self.body(text), self.authorize)
            except AccessError as error:
                return error.reason
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["合成页面甲", "合成页面乙"]))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertIn("personal_draft_revision_conflict", results)
        self.assertEqual(self.store.read(self.key, self.authorize)["revision"], 1)

    def test_expiry_and_clear_retain_monotonic_tombstones_against_stale_writes(self):
        saved = self.store.write(self.key, self.body(), self.authorize)
        self.assertEqual(saved["expires_at"], "1970-01-01T00:46:40Z")
        self.now = 2800.0
        self.assert_code("personal_draft_revision_conflict", self.store.write, self.key, self.body("过期旧写", 1), self.authorize)
        expired = self.store.read(self.key, self.authorize)
        self.assertEqual(expired, {"text": "", "conversation_id": None, "revision": 2, "expires_at": None})
        self.assertEqual(self.store.read(self.key, self.authorize), expired)
        cleared = self.store.write(self.key, self.body("", 2), self.authorize)
        self.assertEqual(cleared["revision"], 3)
        self.now += 9999
        self.assertEqual(self.store.read(self.key, self.authorize), cleared)
        self.assert_code("personal_draft_revision_conflict", self.store.write, self.key, self.body("迟到旧写", 0), self.authorize)

    def test_any_draft_access_clears_other_expired_text_without_resetting_its_revision(self):
        other = (*self.key[:-1], "other-scope")
        absent = (*self.key[:-1], "absent-scope")
        for operation in ("read", "write", "consume"):
            with self.subTest(operation=operation):
                self.now = 1000.0
                store = PersonalDraftService(SENSITIVE, clock=lambda: self.now)
                store.write(self.key, self.body("旧连接草稿"), self.authorize)
                self.now = 2000.0
                store.write(other, self.body("当前连接草稿"), self.authorize)
                self.now = 3000.0
                if operation == "read":
                    store.read(other, self.authorize)
                elif operation == "write":
                    store.write(other, self.body("当前新草稿", 1), self.authorize)
                else:
                    self.assertFalse(store.consume(absent, None, "普通已接纳问题"))
                # Inspect before ever re-reading the old key: cleanup was
                # caused by activity in a different scope, not that re-read.
                self.assertEqual(store._entries[self.key]["value"], {"text": "", "conversation_id": None, "revision": 2, "expires_at": None})
                self.assertIsNone(store._entries[self.key]["expires"])
                self.assertEqual(store.read(other, self.authorize)["text"], "当前新草稿" if operation == "write" else "当前连接草稿")
                self.assert_code("personal_draft_revision_conflict", store.write, self.key, self.body("迟到旧写", 1), self.authorize)
                self.assertEqual(store.read(self.key, self.authorize)["revision"], 2)

    def test_close_clears_all_content_and_cannot_reopen_with_an_old_write(self):
        self.store.write(self.key, self.body(), self.authorize)
        self.store.close()
        self.store.close()
        self.assertEqual(self.store._entries, {})
        self.assert_code("personal_draft_unavailable", self.store.read, self.key, self.authorize)
        self.assert_code("personal_draft_unavailable", self.store.write, self.key, self.body("关闭后迟到写"), self.authorize)
        self.assertFalse(self.store.consume(self.key, None, "关闭后已接纳问题"))
        self.assertEqual(self.store._entries, {})

    def test_each_trusted_identity_dimension_isolated_without_eviction_of_tombstones(self):
        self.store.write(self.key, self.body(), self.authorize)
        for index in range(5):
            key = list(self.key)
            key[index] += "-other"
            self.assertEqual(self.store.read(tuple(key), self.authorize)["text"], "")
        limited = PersonalDraftService(SENSITIVE, capacity=2)
        limited.write(self.key, self.body(""), self.authorize)
        other = (*self.key[:-1], "other-scope")
        limited.read(other, self.authorize)
        third = (*self.key[:-1], "third-scope")
        error = self.assert_code("personal_draft_capacity", limited.read, third, self.authorize)
        self.assertEqual(error.status, 503)
        self.assertEqual(limited.read(self.key, self.authorize)["revision"], 1)
        self.assert_code("personal_draft_revision_conflict", limited.write, self.key, self.body("迟到旧写"), self.authorize)

    def test_strict_input_and_authorization_reject_without_saving_text(self):
        invalid = [{}, self.body(revision=True), self.body(revision=-1), self.body(text="x" * 2001),
            self.body(text="bad\x00text"), self.body(text="\ud800"), self.body(conversation_id="not-a-uuid"),
            {**self.body(), "student_id": "untrusted"}]
        for body in invalid:
            with self.subTest(body_keys=list(body)):
                self.assert_code("invalid_personal_draft", self.store.write, self.key, body, self.authorize)
        self.assert_code("sensitive_chat_input", self.store.write, self.key, self.body("password=synthetic-secret"), self.authorize)
        def denied():
            raise AccessError()
        self.assert_code("unauthenticated", self.store.write, self.key, self.body(), denied)
        self.assertEqual(self.store._entries, {})
        active = [True]
        def authorize():
            if not active[0]:
                raise AccessError("范围已经变化", 409, "personal_scope_changed")
        def revoked_before_commit():
            active[0] = False
            return self.now
        self.store.clock = revoked_before_commit
        self.assert_code("personal_scope_changed", self.store.write, self.key, self.body(), authorize)
        self.assertEqual(self.store._entries, {})

    def test_accepted_send_fences_late_saves_and_preserves_a_newer_different_draft(self):
        self.store.read(self.key, self.authorize)
        self.assertFalse(self.store.consume(self.key, None, "已接纳的问题"))
        self.assert_code("personal_draft_revision_conflict", self.store.write, self.key, self.body("已接纳的问题", 0), self.authorize)
        newer = self.store.write(self.key, self.body("更晚输入的不同问题", 1), self.authorize)
        self.assertFalse(self.store.consume(self.key, None, "已接纳的问题"))
        after = self.store.read(self.key, self.authorize)
        self.assertEqual(after, {**newer, "revision": 3})
        identifier = str(uuid.uuid4())
        self.store.write(self.key, self.body("  同一问题  ", 3, identifier), self.authorize)
        self.assertFalse(self.store.consume(self.key, None, "同一问题"))
        self.assertTrue(self.store.consume(self.key, identifier, "同一问题"))
        self.assertEqual(self.store.read(self.key, self.authorize), {"text": "", "conversation_id": None, "revision": 6, "expires_at": None})
        empty = PersonalDraftService(SENSITIVE, capacity=0)
        self.assertFalse(empty.consume(self.key, None, "普通对话不分配草稿"))
        self.assertEqual(empty._entries, {})


class PersonalDraftIntegrationTests(unittest.TestCase):
    body = staticmethod(chat_fixture.PersonalChatTests.body)
    turn = chat_fixture.PersonalChatTests.turn
    assert_reason = chat_fixture.PersonalChatTests.assert_reason

    def setUp(self):
        chat_fixture.PersonalChatTests.setUp(self)
        self.app.school.personal_chat_identity_scope = lambda context: {
            "scope_key": f"{context.session_ref}:{self.app.school.epoch}"}

    def save(self, text="未发送的合成问题", revision=0, conversation_id=None):
        return self.service.write_draft(PersonalDraftStoreTests.body(text, revision, conversation_id), self.context)["draft"]

    def test_application_close_also_closes_the_in_memory_draft_service(self):
        app = Application(Path(self.temp.name) / "draft-close.sqlite3", mock_enabled=True, model=self.model)
        drafts = app.personal_chat.drafts
        key = ("synthetic-realm", "synthetic-owner", "synthetic-session", "synthetic-binding", "synthetic-local-scope")
        drafts.write(key, PersonalDraftStoreTests.body(), lambda: True)
        app.close()
        self.assertEqual(drafts._entries, {})
        with self.assertRaises(AccessError) as caught:
            drafts.read(key, lambda: True)
        self.assertEqual(caught.exception.reason, "personal_draft_unavailable")

    def test_draft_requires_only_local_identity_and_creates_no_chat_run_or_model_request(self):
        self.model.configured = False
        with patch.object(self.service, "_current", side_effect=AssertionError("draft must not bind model/source versions")), \
                patch.object(self.app.store, "chat_begin", side_effect=AssertionError("draft must not create a chat")):
            first = self.service.read_draft(self.context)
            saved = self.save()
            self.assertEqual(first["data_kind"], "personal_source")
            self.assertEqual(saved["revision"], 1)
            self.assertEqual(self.service.read_draft(self.context)["draft"], saved)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.app.school.calls, [])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM owned_conversations").fetchone()[0], 0)
        self.assertEqual(self.service.read_draft(self.other)["draft"]["text"], "")

    def test_conversation_id_is_owned_current_scope_and_session_reauthentication_isolated(self):
        conversation = self.turn()
        self.save(conversation_id=conversation["conversation_id"])
        self.assert_reason("inaccessible_object", self.service.write_draft,
            PersonalDraftStoreTests.body(conversation_id=conversation["conversation_id"]), self.other)
        changed_session = replace(self.context, session_ref="SYNTHETIC-NEW-SESSION")
        self.assertEqual(self.service.read_draft(changed_session)["draft"]["text"], "")
        self.assert_reason("inaccessible_object", self.service.write_draft,
            PersonalDraftStoreTests.body(conversation_id=conversation["conversation_id"]), changed_session)
        self.app.school.epoch += 1
        self.assertEqual(self.service.read_draft(self.context)["draft"]["text"], "")
        self.assert_reason("inaccessible_object", self.service.write_draft,
            PersonalDraftStoreTests.body(conversation_id=conversation["conversation_id"]), self.context)
        self.app.auth.active = False
        self.assert_reason("unauthenticated", self.service.read_draft, self.context)

    def test_rejected_send_keeps_draft_but_accepted_model_failure_consumes_it(self):
        saved = self.save("  查询这份培养资料  ")
        self.model.configured = False
        self.assert_reason("model_not_configured", self.turn)
        self.assertEqual(self.service.read_draft(self.context)["draft"], saved)
        self.model.configured = True
        self.model.callback = lambda: (_ for _ in ()).throw(ModelError("合成模型失败", "synthetic_model_failure"))
        response = self.turn()
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["code"], "synthetic_model_failure")
        self.assertEqual(self.service.read_draft(self.context)["draft"], {"text": "", "conversation_id": None, "revision": 2, "expires_at": None})

    def test_actual_chat_reservation_rejects_late_autosave_and_replay_does_not_consume_again(self):
        self.service.read_draft(self.context)  # establishes revision 0 for the page
        observed = []
        def late_save():
            try:
                self.save("查询这份培养资料", 0)
            except AccessError as error:
                observed.append(error.reason)
        self.model.callback = late_save
        body = self.body()
        reply = self.service.turn(body, self.context)
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(observed, ["personal_draft_revision_conflict"])
        self.assertEqual(self.service.read_draft(self.context)["draft"]["revision"], 1)
        newer = self.save("不同的下一条草稿", 1)
        replay = self.service.turn(body, self.context)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.service.read_draft(self.context)["draft"], newer)


if __name__ == "__main__":
    unittest.main()
