"""Actual personal HTTP routes and the model tool loop on synthetic sources."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
import uuid

import test_personal_http as helper_module
from validation.run_personal_ui_fixture import ScriptedPersonalModel


class CountingModel(ScriptedPersonalModel):
    def __init__(self):
        self.calls = 0
        self.block = False
        self.started, self.release = threading.Event(), threading.Event()
        self._observer = self

    def with_options(self, model, thinking=False):
        other = CountingModel()
        other.model, other.thinking = model, thinking
        # Request snapshots retain this test's counters and synchronization.
        other._observer = self._observer
        return other

    def complete(self, messages, tools):
        observer = self._observer
        observer.calls += 1
        observer.started.set()
        if observer.block:
            observer.release.wait(5)
        return super().complete(messages, tools)


class PersonalChatHTTPTests(unittest.TestCase):
    request = helper_module.PersonalHTTPTests.request
    login = helper_module.PersonalHTTPTests.login
    sync = helper_module.PersonalHTTPTests.sync
    prepare = helper_module.PersonalHTTPTests.prepare

    def setUp(self):
        helper_module.PersonalHTTPTests.setUp(self)
        self.model = CountingModel()
        self.app.personal_chat.model = self.model

    def tearDown(self):
        self.model.release.set()
        helper_module.PersonalHTTPTests.tearDown(self)

    @staticmethod
    def body(question="查询 SYNTHETIC-PE-REPEAT 的来源记录"):
        return {"message": question, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                "consent_personal": True, "data_use_version": "personal-retrieval-v1"}

    def test_direct_chat_selects_live_tools_and_history_with_one_source_read(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        before = len(self.clients[0].calls)
        body = self.body()
        code, reply, _ = self.request("POST", "/api/chat/turn", body)
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply["status"], "completed", reply)
        self.assertEqual([tool["name"] for tool in reply["tool_results"]], ["get_my_context", "query_my_records"])
        self.assertEqual(len(reply["tool_results"][-1]["result"]["records"]), 3)
        self.assertEqual(len(self.clients[0].calls), before + 1)
        code, history, _ = self.request("POST", "/api/chat/history", {"conversation_id": reply["conversation_id"]})
        self.assertEqual(code, 200, history)
        self.assertEqual(history["turns"][0]["reply"], reply["reply"])
        code, listing, _ = self.request("GET", "/api/chat/conversations?limit=1")
        self.assertEqual(code, 200, listing)
        self.assertEqual(listing["limit"], 1)
        self.assertEqual(listing["conversations"][0]["conversation_id"], reply["conversation_id"])
        calls = self.model.calls
        replay = self.request("POST", "/api/chat/turn", body)[1]
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.model.calls, calls)

    def test_personal_consent_is_required_before_any_model_call(self):
        self.login()
        body = self.body()
        for changes in ({"consent_personal": False}, {"data_use_version": "synthetic"}, {"consent_remote": True}):
            code, result, _ = self.request("POST", "/api/chat/turn", {**body, **changes})
            self.assertEqual(code, 400, result)
        self.assertEqual(self.model.calls, 0)

    def test_first_turn_is_visible_while_running_and_recovered_without_another_model_call(self):
        self.login()
        self.model.block = True
        body = self.body('你好')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.request, 'POST', '/api/chat/turn', body)
            try:
                self.assertTrue(self.model.started.wait(2))
                code, listing, headers = self.request('GET', '/api/chat/conversations')
                self.assertEqual(code, 200, listing)
                self.assertEqual(len(listing['conversations']), 1, listing)
                row = listing['conversations'][0]
                self.assertEqual((row['title'], row['turn_id'], row['in_progress']), ('你好', 0, True))
                self.assertEqual(headers['Cache-Control'], 'no-store')
                identifier = row['conversation_id']
                for _ in range(2):
                    code, history, _ = self.request('POST', '/api/chat/history', {'conversation_id': identifier})
                    self.assertEqual(code, 200, history)
                    self.assertTrue(history['in_progress'])
                    self.assertEqual(history['turns'], [])
                duplicate = self.request('POST', '/api/chat/turn', body)
                self.assertEqual(duplicate[0], 409, duplicate[1])
                competing = self.request('POST', '/api/chat/turn', {
                    **self.body('另一条问题'), 'conversation_id': identifier})
                self.assertEqual(competing[0], 409, competing[1])
                self.assertEqual(self.model.calls, 1)
                self.assertEqual(self.clients[0].calls, [])
            finally:
                self.model.release.set()
            code, reply, _ = pending.result(timeout=3)
        self.assertEqual(code, 200, reply)
        self.assertEqual((reply['conversation_id'], reply['turn_id']), (identifier, 1))
        code, history, _ = self.request('POST', '/api/chat/history', {'conversation_id': identifier})
        self.assertEqual(code, 200, history)
        self.assertFalse(history['in_progress'])
        self.assertEqual(history['turns'][0]['reply'], reply['reply'])
        self.assertTrue(self.request('POST', '/api/chat/turn', body)[1]['replayed'])
        self.assertEqual(self.model.calls, 1)

    def test_pending_first_turn_is_hidden_after_reauthentication_or_owner_change(self):
        for change in ('reauthentication', 'owner'):
            with self.subTest(change=change):
                self.login()
                self.model.block = True
                self.model.started.clear()
                self.model.release.clear()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.request, 'POST', '/api/chat/turn', self.body('你好'))
                    try:
                        self.assertTrue(self.model.started.wait(2))
                        listing = self.request('GET', '/api/chat/conversations')[1]
                        self.assertEqual(len(listing['conversations']), 1, listing)
                        identifier = listing['conversations'][0]['conversation_id']
                        if change == 'reauthentication':
                            code, result, _ = self.request('POST', '/api/school/reauthenticate', {
                                'username': 'SYNTHETIC-LOGIN-A', 'password': 'SYNTHETIC-TRANSIENT'})
                            self.assertEqual(code, 200, result)
                        else:
                            self.login('SYNTHETIC-LOGIN-B')
                        self.assertEqual(self.request('GET', '/api/chat/conversations')[1]['conversations'], [])
                        code, result, _ = self.request('POST', '/api/chat/history', {'conversation_id': identifier})
                        self.assertEqual(code, 404, result)
                    finally:
                        self.model.release.set()
                    code, result, _ = pending.result(timeout=3)
                self.assertIn(code, (401, 409), result)
                self.assertNotIn('tool_results', result)
                with self.app.store.connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM owned_conversations WHERE pending_request IS NOT NULL').fetchone()[0], 0)

    def test_title_search_finds_older_owned_conversation_without_new_source_or_model_calls(self):
        self.login()
        code, reply, _ = self.request("POST", "/api/chat/turn", self.body())
        self.assertEqual(code, 200, reply)
        with self.app.store.connect() as db:
            realm, owner, raw = db.execute("SELECT realm,owner,state FROM owned_conversations WHERE id=?",
                                           (reply['conversation_id'],)).fetchone()
            state = json.loads(raw)
            summary = {key: state[key] for key in ('personal_scope', 'personal_service')}
            summary.update(title='SYNTHETIC RECENT', turns=[])
            now = datetime.now(timezone.utc)
            db.executemany('INSERT INTO owned_conversations VALUES(?,?,?,?,?,?,?)',
                [(str(uuid.uuid4()), realm, owner, 0, json.dumps(summary), None,
                  (now + timedelta(seconds=i + 1)).isoformat()) for i in range(105)])
        calls = (self.model.calls, len(self.clients[0].calls))
        listing = self.request('GET', '/api/chat/conversations?limit=100')[1]
        self.assertNotIn(reply['conversation_id'], [row['conversation_id'] for row in listing['conversations']])
        code, result, headers = self.request('GET', '/api/chat/conversations?query=synthetic-pe-repeat')
        self.assertEqual(code, 200, result)
        self.assertEqual([row['conversation_id'] for row in result['conversations']], [reply['conversation_id']])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual((self.model.calls, len(self.clients[0].calls)), calls)
        code, _, _ = self.request('POST', '/api/school/reauthenticate',
            {'username': 'SYNTHETIC-LOGIN-A', 'password': 'SYNTHETIC-TRANSIENT'})
        self.assertEqual(code, 200)
        self.assertEqual(self.request('GET', '/api/chat/conversations?query=synthetic-pe-repeat')[1]['conversations'], [])

    def test_curriculum_is_prepared_automatically_by_tool_and_no_manual_scope_is_required(self):
        self.login()
        code, _, _ = self.request("POST", "/api/school/plan", {})
        self.assertEqual(code, 200)
        before = len(self.clients[0].calls)
        code, result, _ = self.request("POST", "/api/chat/turn", self.body("培养资料 SYNTHETIC-001 是什么？"))
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["tool_results"][-1]["name"], "search_my_curriculum")
        self.assertTrue(result["citations"])
        self.assertEqual(len(self.clients[0].calls), before + 2)

    def test_reauthentication_hides_old_titles_history_and_request_replay(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        body = self.body()
        reply = self.request("POST", "/api/chat/turn", body)[1]
        self.assertEqual(self.request("POST", "/api/school/reauthenticate",
            {"username": "SYNTHETIC-LOGIN-A", "password": "SYNTHETIC-TRANSIENT"})[0], 200)
        self.assertEqual(self.request("GET", "/api/chat/conversations?limit=100")[1]["conversations"], [])
        self.assertEqual(self.request("POST", "/api/chat/history", {"conversation_id": reply["conversation_id"]})[0], 404)
        self.assertNotEqual(self.request("POST", "/api/chat/turn", body)[0], 200)

    def test_logout_is_not_blocked_by_personal_model_and_late_results_are_discarded(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.model.block = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/chat/turn", self.body())
            try:
                self.assertTrue(self.model.started.wait(2))
                logout = pool.submit(self.request, "POST", "/api/auth/logout", {})
                self.assertEqual(logout.result(timeout=2)[0], 200)
            finally:
                self.model.release.set()
            code, result, _ = pending.result(timeout=3)
        self.assertEqual(code, 401, result)
        self.assertNotIn("tool_results", result)


if __name__ == "__main__":
    unittest.main()
