"""Real loopback draft routes with synthetic authentication and no model calls."""
import unittest

from tests import test_personal_http as http_fixture


class PersonalDraftHTTPTests(unittest.TestCase):
    request = http_fixture.PersonalHTTPTests.request
    login = http_fixture.PersonalHTTPTests.login

    def setUp(self):
        http_fixture.PersonalHTTPTests.setUp(self)

    def tearDown(self):
        http_fixture.PersonalHTTPTests.tearDown(self)

    @staticmethod
    def body(text="尚未发送的合成问题", revision=0):
        return {"text": text, "conversation_id": None, "expected_revision": revision}

    def test_authenticated_school_only_routes_reuse_csrf_origin_and_strict_schema_without_acquisition(self):
        self.assertEqual(self.request("GET", "/api/chat/draft")[0], 401)
        self.login()
        calls = len(self.clients[0].calls)
        code, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(code, 200, bootstrap)
        self.assertEqual(bootstrap["drafts"], {"available": True, "storage": "server_session_memory", "max_chars": 2000, "ttl_seconds": 1800})
        self.assertNotIn("drafts", bootstrap["chat"])
        code, first, _ = self.request("GET", "/api/chat/draft")
        self.assertEqual(code, 200, first)
        self.assertEqual(first, {"data_kind": "personal_source", "draft": {"text": "", "conversation_id": None, "revision": 0, "expires_at": None}})
        self.assertEqual(self.request("POST", "/api/chat/draft", self.body(), csrf=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/chat/draft", self.body(), origin="https://example.invalid")[0], 403)
        self.assertEqual(self.request("GET", "/api/chat/draft?student_id=untrusted")[0], 400)
        code, saved, _ = self.request("POST", "/api/chat/draft", self.body())
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["draft"]["revision"], 1)
        self.assertEqual(self.request("GET", "/api/chat/draft")[1], saved)
        code, conflict, _ = self.request("POST", "/api/chat/draft", self.body())
        self.assertEqual((code, conflict["code"]), (409, "personal_draft_revision_conflict"))
        for body in ({**self.body(revision=1), "scope": "untrusted"}, self.body("password=synthetic-secret", 1)):
            self.assertEqual(self.request("POST", "/api/chat/draft", body)[0], 400)
        self.assertEqual(len(self.clients[0].calls), calls)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM owned_conversations").fetchone()[0], 0)
        code, mocked, fields = self.request("POST", "/api/auth/mock-login", {"profile_id": "demo-a"})
        self.assertEqual(code, 200)
        self.cookie, self.csrf = fields["Set-Cookie"].split(";", 1)[0], mocked["csrf_token"]
        self.assertEqual(self.request("GET", "/api/chat/draft")[0], 403)
        self.assertEqual(self.request("POST", "/api/chat/draft", self.body())[0], 403)

    def test_http_expiry_keeps_revision_and_revoked_session_cannot_restore_text(self):
        self.login()
        now = [1000.0]
        self.app.personal_chat.drafts.clock = lambda: now[0]
        self.assertEqual(self.request("POST", "/api/chat/draft", self.body())[0], 200)
        now[0] += 1800
        code, rejected, _ = self.request("POST", "/api/chat/draft", self.body("旧页面迟到写", 1))
        self.assertEqual((code, rejected["code"]), (409, "personal_draft_revision_conflict"))
        draft = self.request("GET", "/api/chat/draft")[1]["draft"]
        self.assertEqual((draft["text"], draft["revision"], draft["expires_at"]), ("", 2, None))
        self.assertEqual(self.request("POST", "/api/auth/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/chat/draft")[0], 401)
        self.assertEqual(self.request("POST", "/api/chat/draft", self.body("不应写入", 2))[0], 401)


if __name__ == "__main__":
    unittest.main()
