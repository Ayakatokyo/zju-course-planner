"""Real loopback HTTP tests, with a clearly labeled model test double."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from course_assistant.server import Application, make_handler


class WaitingModel:
    allow_reference_text = False

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()

    def status(self):
        return {"configured": True, "remote": True, "provider": "TEST DOUBLE", "model": "test-only"}

    def complete(self, messages, tools):
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("test deadline")
        return {"role": "assistant", "content": "仅测试替身"}


class ChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = WaitingModel()
        self.app = Application(Path(self.tmp.name) / "test.sqlite3", mock_enabled=True, model=self.model)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        status, body, headers = self.request("POST", "/api/auth/mock-login", {"profile_id": "demo-a"})
        self.assertEqual(status, 200)
        self.cookie = dict(headers)["Set-Cookie"].split(";", 1)[0]
        self.csrf = body["csrf_token"]

    def tearDown(self):
        self.model.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, *, auth=False, csrf=True, raw=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Cookie"] = self.cookie
            if csrf:
                headers["X-CSRF-Token"] = self.csrf
        connection.request(method, path, raw if raw is not None else json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), response.getheaders()
        connection.close()
        return result

    def body(self):
        return {"message": "查询我的合成课表", "request_id": str(uuid.uuid4()),
                "expected_turn": 0, "consent_remote": True}

    def test_chat_auth_csrf_and_duplicate_json_gate(self):
        self.assertEqual(self.request("GET", "/api/chat/status")[0], 401)
        self.assertEqual(self.request("POST", "/api/chat/turn", self.body(), auth=True, csrf=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/chat/turn", auth=True, raw='{"message":"a","message":"b"}')[0], 400)
        self.assertFalse(self.model.started.is_set())

    def test_logout_is_not_blocked_by_model_and_late_response_is_denied(self):
        collected = []
        thread = threading.Thread(target=lambda: collected.append(
            self.request("POST", "/api/chat/turn", self.body(), auth=True)))
        thread.start()
        self.assertTrue(self.model.started.wait(2), "model was not reached")
        try:
            # This must succeed while the model is still waiting on the event.
            status, _, _ = self.request("POST", "/api/auth/logout", {}, auth=True)
            self.assertEqual(status, 200)
        finally:
            self.model.release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(collected[0][0], 401)
        self.assertNotIn("tool_results", collected[0][1])


if __name__ == "__main__":
    unittest.main()
