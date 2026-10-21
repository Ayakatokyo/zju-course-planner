"""Real application/HTTP coordination, with synthetic transports and no network.

The HTTP helper is imported as a module to avoid rediscovering its TestCase.
Only loopback HTTP is used; no environment file or private source is read.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import unittest
import uuid

import test_personal_http as helper_module
from course_assistant.model_client import ModelClient
from validation.run_personal_ui_fixture import ScriptedPersonalModel


class NeverProvider:
    def __init__(self):
        self.calls = 0

    def open(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("A provider request is forbidden in this test")


class SyntheticProgressModel(ScriptedPersonalModel):
    """Protocol fixture: request all-snapshot summary, with actual local deltas."""
    def __init__(self, model="synthetic-only", thinking=False, shared=None):
        self.model, self.thinking = model, thinking
        self.shared = shared if shared is not None else {
            "calls": [], "block": False, "started": threading.Event(), "release": threading.Event()}

    def with_options(self, model, thinking=False):
        return type(self)(model, thinking, self.shared)

    def complete(self, messages, tools):
        self.shared["calls"].append(deepcopy(messages))
        message = super().complete(messages, tools)
        # This fixture asks a genuine overview rather than the parent fixture's
        # repeated-course example. The application must normalize the full user
        # question safely and summarize every matching row, not its first page.
        for call in message.get("tool_calls", []):
            if call["function"]["name"] == "query_my_records":
                arguments = json.loads(call["function"]["arguments"])
                arguments["query"] = "我的成绩怎么样？"
                call["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)
        return message

    def complete_stream_with_metadata(self, messages, tools, *, observer=None, on_reasoning=None):
        message, metadata = self.complete_with_metadata(messages, tools)
        if self.thinking and on_reasoning is not None:
            on_reasoning("这是合成协议测试的公开推理增量，不是实际模型推理。" * 20)
            if self.shared["block"]:
                self.shared["block"] = False
                self.shared["started"].set()
                if not self.shared["release"].wait(6):
                    raise AssertionError("Synthetic provider was not released")
        if observer:
            observer(metadata)
        return message, metadata


class BlockingSyntheticSchool(helper_module.ObservedSyntheticSchool):
    def __init__(self):
        super().__init__()
        self.started, self.release = threading.Event(), threading.Event()

    def request(self, spec):
        if spec["operation"] == "major_academic":
            self.started.set()
            if not self.release.wait(6):
                raise AssertionError("Synthetic school was not released")
        return super().request(spec)


class RecoveringSyntheticModel(SyntheticProgressModel):
    def complete(self, messages, tools):
        message = super().complete(messages, tools)
        if message.get("content") and json.loads(message["content"]).get("kind") == "answer" and not self.shared.get("malformed_sent"):
            self.shared["malformed_sent"] = True
            return {"role": "assistant", "content": "```json\n" + message["content"] + "\n```"}
        return message


class PersonalRetrievalProgressHTTPTests(unittest.TestCase):
    request = helper_module.PersonalHTTPTests.request
    login = helper_module.PersonalHTTPTests.login

    def setUp(self):
        helper_module.PersonalHTTPTests.setUp(self)
        self.model = SyntheticProgressModel()
        self.app.personal_chat.model = self.model

    def tearDown(self):
        self.model.shared["release"].set()
        for client in self.clients:
            if hasattr(client, "release"):
                client.release.set()
        helper_module.PersonalHTTPTests.tearDown(self)

    @staticmethod
    def body(question="我的成绩怎么样", previous=None):
        body = {"message": question, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                "consent_personal": True, "data_use_version": "personal-retrieval-v1"}
        if previous:
            body.update(conversation_id=previous["conversation_id"], expected_turn=previous["turn_id"])
        return body

    def progress(self, request_id):
        code, result, _ = self.request("POST", "/api/chat/progress", {"request_id": request_id})
        self.assertEqual(code, 200, result)
        return result

    def thinking_on(self):
        code, result, _ = self.request("POST", "/api/chat/settings", {"model": "synthetic-only", "thinking": True})
        self.assertEqual(code, 200, result)

    def test_real_school_service_defaults_new_deepseek_session_on_and_preserves_explicit_off(self):
        transport = NeverProvider()
        base = ModelClient({"DEEPSEEK_API_KEY": "SYNTHETIC-NO-NETWORK", "DEEPSEEK_MODEL": "deepseek-test"}, opener=transport)
        self.app.personal_chat.model = base
        self.login()
        status = self.request("GET", "/api/chat/status")[1]
        self.assertTrue(status["thinking"])
        self.assertEqual(status["provider"], "DeepSeek")
        self.assertFalse(base.status()["thinking"])
        code, options, _ = self.request("POST", "/api/chat/settings", {"model": "deepseek-test", "thinking": False})
        self.assertEqual(code, 200, options)
        self.assertFalse(self.request("GET", "/api/chat/status")[1]["thinking"])
        first_cookie, first_csrf = self.cookie, self.csrf
        self.cookie = self.csrf = None
        self.login("SYNTHETIC-LOGIN-B")
        self.assertTrue(self.request("GET", "/api/chat/status")[1]["thinking"])
        self.cookie, self.csrf = first_cookie, first_csrf
        self.assertFalse(self.request("GET", "/api/chat/status")[1]["thinking"])
        self.assertEqual(transport.calls, 0)
        self.assertTrue(all(not client.calls for client in self.clients))

    def test_cold_grade_chat_acquires_once_and_summary_uses_entire_loaded_snapshot(self):
        self.login()
        self.assertEqual(self.clients[0].calls, [])
        body = self.body()
        code, result, _ = self.request("POST", "/api/chat/turn", body)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed", result)
        sources = self.clients[0].calls
        self.assertTrue(sources)
        self.assertEqual({item["operation"] for item in sources}, {"major_academic"})
        self.assertTrue(all(item["target_term"] == "ALL-HISTORY" for item in sources))
        tools = result["tool_results"]
        self.assertEqual(tools[0]["initiated_by"], "service_preflight")
        query = next(tool for tool in tools if tool["name"] == "query_my_records")
        self.assertEqual(query["arguments"]["query"], "")
        self.assertEqual(query["result"]["source_grade_summary"]["matched_records"], 75)
        self.assertEqual(query["result"]["source_grade_summary"]["numeric_grade_count"], 60)
        self.assertEqual(query["result"]["coverage"]["captured_rows"], 75)
        self.assertFalse(query["result"]["coverage"]["has_more"])
        self.assertEqual([item["page"] for item in sources], [1])
        self.assertGreater(query["result"]["retrieval"]["returned_records"], 0)
        self.assertLessEqual(query["result"]["retrieval"]["returned_records"], 15)
        self.assertTrue(query["result"]["retrieval"]["has_more"])
        self.assertTrue(any(item["kind"] == "summary" for item in result["citations"]))
        completed = self.progress(body["request_id"])
        self.assertEqual((completed["status"], completed["terminal"]), ("completed", True))
        read_starts = [item for item in completed["events"] if item["kind"] == "tool.start" and item.get("tool_name") == "read_my_major_academic"]
        self.assertEqual(len(read_starts), 1)
        before = len(sources)
        repeated_body = self.body(previous=result)
        code, repeated, _ = self.request("POST", "/api/chat/turn", repeated_body)
        self.assertEqual(code, 200, repeated)
        self.assertEqual(repeated["status"], "completed", repeated)
        self.assertTrue(repeated["history_reset"])
        self.assertEqual(len(sources), before + 1)
        self.assertEqual([item["page"] for item in sources[before:]], [1])
        latest_initial = self.model.shared["calls"][-2]
        self.assertFalse(any(item["role"] == "tool" for item in latest_initial))
        self.assertEqual([item["content"] for item in latest_initial if item["role"] == "user"], [repeated_body["message"]])
        system = next(item["content"] for item in latest_initial if item["role"] == "system")
        self.assertIn("近期历史问答", system)
        self.assertIn("旧引用不可复用", system)
        self.assertIn(body["message"], system)

    def test_one_http_turn_repairs_format_with_actual_persisted_events_without_reacquiring(self):
        self.model = RecoveringSyntheticModel()
        self.app.personal_chat.model = self.model
        self.login()
        body = self.body()
        code, result, _ = self.request("POST", "/api/chat/turn", body)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["repair_attempts"], 1)
        self.assertFalse(result["diagnostics"]["incomplete"])
        self.assertEqual(len(self.model.shared["calls"]), 3)
        code, detail, _ = self.request("POST", "/api/chat/run", {"run_id": result["run_id"]})
        self.assertEqual(code, 200, detail)
        events = detail["events"]
        self.assertEqual(len([event for event in events if event["kind"] == "tool.start" and event.get("tool_name") == "read_my_major_academic"]), 1)
        self.assertEqual(len([event for event in events if event["kind"] == "tool.start" and event.get("tool_name") == "query_my_records"]), 1)
        rejected = next(event for event in events if event["kind"] == "answer.rejected")
        self.assertEqual(rejected["failure_detail"], "invalid_json")
        self.assertEqual([event["status"] for event in events if event["kind"] == "answer.repair"], ["started", "completed"])
        source = next(event for event in events if event["kind"] == "tool.end" and event.get("tool_name") == "read_my_major_academic")
        self.assertEqual(source["initiated_by"], "service_acquisition")
        self.assertEqual(source["captured_rows"], 75)
        self.assertEqual(source["reported_total"], 75)
        self.assertFalse(source["has_more"])
        self.assertTrue(source["complete_within_scope"])
        self.assertEqual([item["page"] for item in self.clients[0].calls], [1])

    def test_identity_chat_calls_model_without_acquiring_school_sources(self):
        self.login()
        code, result, _ = self.request("POST", "/api/chat/turn", self.body("你是谁"))
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["reply_origin"], "model_selected_service_guidance")
        self.assertEqual(self.clients[0].calls, [])
        self.assertEqual(len(self.model.shared["calls"]), 1)

    def test_progress_polling_does_not_wait_for_school_connection_read_lock(self):
        def factory():
            client = BlockingSyntheticSchool()
            self.clients.append(client)
            return client
        self.app.school.client_factory = factory
        self.login()
        client, body = self.clients[0], self.body()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/chat/turn", body)
            try:
                self.assertTrue(client.started.wait(2))
                polling = pool.submit(self.progress, body["request_id"])
                progress = polling.result(timeout=2)
                self.assertEqual(progress["status"], "running")
                self.assertFalse(progress["terminal"])
                self.assertIsNone(progress["active_reasoning"])
                self.assertTrue(any(item.get("tool_name") == "read_my_major_academic" and item["kind"] == "tool.start" for item in progress["events"]))
                # The first model call now selects the exact live record
                # query; school I/O no longer precedes task selection. The
                # source lock must still leave progress polling responsive,
                # with no second generation while that query is blocked.
                self.assertEqual(len(self.model.shared["calls"]), 1)
                self.assertTrue(any(item["kind"] == "model.end" for item in progress["events"]))
            finally:
                client.release.set()
            code, result, _ = pending.result(timeout=3)
            self.assertEqual(code, 200, result)
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(len(self.model.shared["calls"]), 2)

    def test_active_reasoning_is_owner_and_generation_scoped(self):
        self.login()
        self.thinking_on()
        self.model.shared["block"] = True
        body = self.body("你是谁")
        first_cookie, first_csrf = self.cookie, self.csrf
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/chat/turn", body)
            try:
                self.assertTrue(self.model.shared["started"].wait(2))
                active = self.progress(body["request_id"])
                self.assertTrue(active["thinking"])
                self.assertIn("合成协议", active["active_reasoning"]["text"])
                self.cookie = self.csrf = None
                self.login("SYNTHETIC-LOGIN-B")
                foreign = self.progress(body["request_id"])
                self.assertEqual(foreign["status"], "not_started")
                self.assertEqual(foreign["events"], [])
                self.assertIsNone(foreign["active_reasoning"])
                self.cookie, self.csrf = first_cookie, first_csrf
                self.clients[0].session_epoch += 1
                changed = self.progress(body["request_id"])
                self.assertEqual(changed["status"], "not_started")
                self.assertIsNone(changed["active_reasoning"])
                self.assertEqual(changed["events"], [])
            finally:
                self.cookie, self.csrf = first_cookie, first_csrf
                self.model.shared["release"].set()
            self.assertEqual(pending.result(timeout=3)[0], 409)

    def test_logout_revokes_old_progress_and_late_model_result(self):
        self.login()
        self.thinking_on()
        self.model.shared["block"] = True
        body = self.body("你是谁")
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/chat/turn", body)
            try:
                self.assertTrue(self.model.shared["started"].wait(2))
                self.assertIsNotNone(self.progress(body["request_id"])["active_reasoning"])
                logout = pool.submit(self.request, "POST", "/api/auth/logout", {})
                self.assertEqual(logout.result(timeout=2)[0], 200)
                code, denied, _ = self.request("POST", "/api/chat/progress", {"request_id": body["request_id"]})
                self.assertEqual(code, 401, denied)
            finally:
                self.model.shared["release"].set()
            self.assertIn(pending.result(timeout=3)[0], (401, 409))
        self.cookie = self.csrf = None
        self.login()
        fresh = self.progress(body["request_id"])
        self.assertEqual(fresh["status"], "not_started")
        self.assertEqual(fresh["events"], [])
        self.assertIsNone(fresh["active_reasoning"])


if __name__ == "__main__":
    unittest.main()
