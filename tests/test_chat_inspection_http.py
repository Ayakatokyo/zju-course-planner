"""Run inspection/model settings over real loopback HTTP, with synthetic doubles.

No real provider/school request, configuration file or personal source is used.
The imported helper module is intentionally not a TestCase import: its tests
must not be discovered a second time from this module.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
import uuid

import test_personal_http as helper_module
from course_assistant.chat_runs import ChatRunError
from validation.run_personal_ui_fixture import ScriptedPersonalModel


class SyntheticInspectionModel(ScriptedPersonalModel):
    """Actual tool protocol without inference; options share observation state."""
    def __init__(self, model="synthetic-fast", thinking=False, shared=None):
        self.model, self.thinking = model, thinking
        self.shared = shared if shared is not None else {
            "catalog_calls": 0, "calls": [], "block_once": False,
            "started": threading.Event(), "release": threading.Event(), "lock": threading.Lock()}
        self._key = "SYNTHETIC-NEVER-LOG-MODEL-KEY"

    def status(self):
        return {"configured": True, "remote": True, "provider": "Synthetic",
                "model": self.model, "thinking": self.thinking,
                "reason": "Synthetic protocol fixture, no remote inference"}

    def list_models(self):
        self.shared["catalog_calls"] += 1
        return {"models": [{"id": "synthetic-fast", "name": "Synthetic Fast"},
                            {"id": "synthetic-deep", "name": "Synthetic Deep"}]}

    def with_options(self, model, thinking=False):
        return type(self)(model, thinking, self.shared)

    def complete_with_metadata(self, messages, tools, *, observer=None):
        with self.shared["lock"]:
            self.shared["calls"].append({"model": self.model, "thinking": self.thinking,
                "messages": deepcopy(messages), "tools": deepcopy(tools)})
            number = len(self.shared["calls"])
            block = self.shared["block_once"]
            self.shared["block_once"] = False
        if block:
            self.shared["started"].set()
            if not self.shared["release"].wait(6):
                raise AssertionError("Synthetic blocked provider was not released")
        message = super().complete(messages, tools)
        metadata = {"status": "completed", "model": self.model, "http_status": 200,
            "elapsed_ms": 2, "request_id": f"synthetic-request-{number}",
            "completion_id": f"synthetic-completion-{number}",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                      "completion_tokens_details": {"reasoning_tokens": 8 if self.thinking else 0}}}
        if self.thinking:
            reasoning = f"Public synthetic provider reasoning, step {number}."
            message["reasoning_content"] = reasoning
            metadata["provider_reasoning"] = reasoning
        if observer is not None:
            observer(deepcopy(metadata))
        return message, metadata


class ChatInspectionHTTPTests(unittest.TestCase):
    request = helper_module.PersonalHTTPTests.request
    login = helper_module.PersonalHTTPTests.login
    sync = helper_module.PersonalHTTPTests.sync

    def setUp(self):
        helper_module.PersonalHTTPTests.setUp(self)
        self.model = SyntheticInspectionModel()
        self.app.personal_chat.model = self.model

    def tearDown(self):
        self.model.shared["release"].set()
        helper_module.PersonalHTTPTests.tearDown(self)

    @staticmethod
    def body(message="SYNTHETIC-NEVER-LOG-QUESTION 查询重复来源记录", *, previous=None):
        result = {"message": message, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                  "consent_personal": True, "data_use_version": "personal-retrieval-v1"}
        if previous:
            result.update(conversation_id=previous["conversation_id"], expected_turn=previous["turn_id"])
        return result

    def refresh(self):
        code, result, _ = self.request("POST", "/api/chat/models/refresh", {})
        self.assertEqual(code, 200, result)
        return result

    def select(self, model="synthetic-deep", thinking=True):
        code, result, _ = self.request("POST", "/api/chat/settings", {"model": model, "thinking": thinking})
        self.assertEqual(code, 200, result)
        return result

    def turn(self, *, previous=None):
        code, result, _ = self.request("POST", "/api/chat/turn", self.body(previous=previous))
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "completed", result)
        return result

    def test_get_catalog_is_local_refresh_requires_csrf_and_selection_uses_directory(self):
        self.login()
        for _ in range(2):
            code, catalog, _ = self.request("GET", "/api/chat/models")
            self.assertEqual(code, 200, catalog)
            self.assertEqual(catalog["selected_model"], "synthetic-fast")
        self.assertEqual(self.model.shared["catalog_calls"], 0)
        self.assertEqual(self.model.shared["calls"], [])
        self.assertEqual(self.clients[0].calls, [])
        for limit in ("0", "51", "99", "1&limit=2"):
            code, denied, _ = self.request("GET", "/api/chat/runs?limit=" + limit)
            self.assertEqual(code, 400, denied)
            self.assertEqual(denied["code"], "invalid_run_request")
        self.assertEqual(self.request("POST", "/api/chat/models/refresh", {}, csrf=False)[0], 403)
        self.assertEqual(self.model.shared["catalog_calls"], 0)
        catalog = self.refresh()
        self.assertEqual({item["id"] for item in catalog["models"]}, {"synthetic-fast", "synthetic-deep"})
        self.assertEqual(self.model.shared["catalog_calls"], 1)
        self.assertEqual(self.request("GET", "/api/chat/models")[0], 200)
        self.assertEqual(self.model.shared["catalog_calls"], 1)
        for options in ({"model": "invented-model", "thinking": True},
                        {"model": "synthetic-deep", "thinking": "true"},
                        {"model": "synthetic-deep", "thinking": True, "owner": "other"}):
            code, denied, _ = self.request("POST", "/api/chat/settings", options)
            self.assertEqual(code, 400, denied)
        self.assertEqual(self.request("POST", "/api/chat/settings", {"model": "synthetic-deep", "thinking": True}, csrf=False)[0], 403)
        selected = self.select()
        self.assertEqual((selected["model"], selected["thinking"]), ("synthetic-deep", True))
        self.assertEqual(self.model.shared["calls"], [])
        self.assertEqual(self.clients[0].calls, [])

    def test_model_settings_are_session_owner_scoped_and_do_not_mutate_base(self):
        self.login()
        self.refresh()
        self.select("synthetic-deep", True)
        first_cookie, first_csrf = self.cookie, self.csrf
        # Independent browser session: a login carrying A's cookie intentionally
        # revokes A and would not test concurrent per-user settings isolation.
        self.cookie = self.csrf = None
        self.login("SYNTHETIC-LOGIN-B")
        code, second, _ = self.request("GET", "/api/chat/models")
        self.assertEqual(code, 200, second)
        self.assertEqual((second["selected_model"], second["thinking"]), ("synthetic-fast", False))
        self.select("synthetic-fast", False)
        self.cookie, self.csrf = first_cookie, first_csrf
        code, first, _ = self.request("GET", "/api/chat/models")
        self.assertEqual(code, 200, first)
        self.assertEqual((first["selected_model"], first["thinking"]), ("synthetic-deep", True))
        self.assertEqual((self.model.model, self.model.thinking), ("synthetic-fast", False))
        self.assertEqual(self.model.shared["calls"], [])

    def test_thinking_tool_rounds_history_and_safe_run_inspection(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.refresh()
        self.select()
        before = len(self.clients[0].calls)
        result = self.turn()
        self.assertEqual((result["model"], result["thinking"]), ("synthetic-deep", True))
        self.assertEqual(result["diagnostics"], {"available": True, "incomplete": False})
        self.assertRegex(result["run_id"], r"^RUN-[a-f0-9]{32}$")
        calls = self.model.shared["calls"]
        # Service preflight supplies context before the model's first request;
        # only the record-tool selection and evidence answer need inference.
        self.assertEqual(len(calls), 2)
        for index, call in enumerate(calls):
            self.assertEqual((call["model"], call["thinking"]), (result["model"], result["thinking"]))
            assistant = [row for row in call["messages"] if row["role"] == "assistant"]
            self.assertEqual(len(assistant), index)
            self.assertTrue(all("reasoning_content" in row for row in assistant))
            self.assertTrue(all("usage" not in row and "elapsed_ms" not in row for row in call["messages"]))
        first_call_count = len(calls)
        continued = self.turn(previous=result)
        first_next_turn = calls[first_call_count]["messages"]
        self.assertEqual(len(self.clients[0].calls), before + 8)
        code, listing, _ = self.request("GET", "/api/chat/runs?limit=20")
        self.assertEqual(code, 200, listing)
        self.assertEqual({run["run_id"] for run in listing["runs"]}, {result["run_id"], continued["run_id"]})
        self.assertNotIn("reasoning_content", json.dumps(listing))
        self.assertEqual(self.request("POST", "/api/chat/run", {"run_id": result["run_id"]}, csrf=False)[0], 403)
        code, inspected, _ = self.request("POST", "/api/chat/run", {"run_id": result["run_id"]})
        self.assertEqual(code, 200, inspected)
        self.assertEqual(inspected["run"]["status"], "completed")
        self.assertFalse(inspected["reasoning_is_execution_proof"])
        events = inspected["events"]
        self.assertEqual([row["sequence"] for row in events], list(range(1, len(events) + 1)))
        self.assertEqual({row["kind"] for row in events}, {"model.start", "model.end", "tool.start", "tool.end", "answer.validated"})
        model_ends = [row for row in events if row["kind"] == "model.end"]
        self.assertEqual(len(model_ends), 2)
        self.assertTrue(all(row["usage"]["total_tokens"] == 30 for row in model_ends))
        self.assertTrue(all(row["request_id"].startswith("synthetic-request-") for row in model_ends))
        self.assertTrue(all(row["reasoning_content"].startswith("Public synthetic") for row in model_ends))
        self.assertEqual([row["tool_name"] for row in events if row["kind"] == "tool.end"], ["get_my_context", "read_my_major_academic", "query_my_records"])
        self.assertEqual(events[-1]["kind"], "answer.validated")
        self.assertEqual(events[-1]["citation_count"], len(result["citations"]))
        serialized = json.dumps(inspected, ensure_ascii=False)
        for private in (self.model._key, "SYNTHETIC-NEVER-LOG-QUESTION", "SYNTHETIC-PE-REPEAT",
                        "source_alias", "observation_id", "tool_args", "messages", "password"):
            self.assertNotIn(private, serialized)
        self.assertEqual(self.app.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.app.store.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertTrue(continued["history_reset"])
        self.assertFalse(any(row["role"] in {"assistant", "tool"} for row in first_next_turn))
        self.assertEqual(len([row for row in first_next_turn if row["role"] == "user"]), 1)
        self.assertTrue(any("已结束历史意图" in row.get("content", "")
                            for row in first_next_turn if row["role"] == "system"))

    def test_changing_selected_model_clears_model_context_but_preserves_ui_history(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.refresh()
        first = self.turn()
        previous_call_count = len(self.model.shared["calls"])
        self.select("synthetic-deep", True)
        second = self.turn(previous=first)
        self.assertTrue(second["history_reset"])
        initial_new_model = self.model.shared["calls"][previous_call_count]
        self.assertEqual(initial_new_model["model"], "synthetic-deep")
        self.assertEqual([row["role"] for row in initial_new_model["messages"]], ["system", "user"])
        code, history, _ = self.request("POST", "/api/chat/history", {"conversation_id": second["conversation_id"]})
        self.assertEqual(code, 200, history)
        self.assertEqual(len(history["turns"]), 2)
        self.assertTrue(history["turns"][0]["is_stale"])

    def test_scope_generation_change_hides_previous_run_and_summary(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        result = self.turn()
        self.assertEqual(self.request("GET", "/api/chat/runs?limit=20")[1]["runs"][0]["run_id"], result["run_id"])
        self.clients[0].session_epoch += 1
        code, listing, _ = self.request("GET", "/api/chat/runs?limit=20")
        self.assertEqual(code, 200, listing)
        self.assertEqual(listing["runs"], [])
        code, denied, _ = self.request("POST", "/api/chat/run", {"run_id": result["run_id"]})
        self.assertEqual(code, 404, denied)
        self.assertNotIn("events", denied)

    def test_event_storage_failure_does_not_change_chat_or_retry_model(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.assertIsNotNone(self.app.personal_chat.runs)
        with patch.object(self.app.personal_chat.runs, "event", side_effect=ChatRunError("chat_run_store_unavailable")):
            result = self.turn()
        self.assertEqual(result["diagnostics"], {"available": True, "incomplete": True})
        self.assertTrue(result["reply"])
        self.assertTrue(result["citations"])
        self.assertEqual(len(self.model.shared["calls"]), 2)
        code, inspected, _ = self.request("POST", "/api/chat/run", {"run_id": result["run_id"]})
        self.assertEqual(code, 200, inspected)
        self.assertEqual(inspected["run"]["status"], "completed")
        self.assertEqual(inspected["events"], [])

    def test_midflight_model_switch_rejects_old_response_without_blocking_settings(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.refresh()
        self.model.shared["block_once"] = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/chat/turn", self.body())
            try:
                self.assertTrue(self.model.shared["started"].wait(2))
                changed = pool.submit(self.request, "POST", "/api/chat/settings", {"model": "synthetic-deep", "thinking": True})
                code, setting, _ = changed.result(timeout=2)
                self.assertEqual(code, 200, setting)
            finally:
                self.model.shared["release"].set()
            code, denied, _ = pending.result(timeout=4)
        self.assertEqual(code, 409, denied)
        self.assertEqual(denied["code"], "personal_model_changed")
        self.assertNotIn("tool_results", denied)
        self.assertNotIn("citations", denied)
        self.assertEqual(len(self.model.shared["calls"]), 1)
        self.assertEqual(self.model.model, "synthetic-fast")

    def test_model_aba_revision_rejects_obsolete_snapshot_before_provider_egress(self):
        self.login()
        self.sync("academic", "2026-2027-AW")
        self.refresh()
        settings = self.app.personal_chat.settings
        original = settings.snapshot
        count, switched = 0, False
        def aba(context):
            nonlocal count, switched
            captured = original(context)
            count += 1
            if count == 2 and not switched:
                # The caller still receives the original A snapshot. A second
                # request changes the session A -> B -> A during that window.
                switched = True
                settings.update(context, {"model": "synthetic-deep", "thinking": True})
                settings.update(context, {"model": "synthetic-fast", "thinking": False})
            return captured
        with patch.object(settings, "snapshot", side_effect=aba):
            code, denied, _ = self.request("POST", "/api/chat/turn", self.body())
        self.assertTrue(switched, "Synthetic ABA window must actually execute")
        self.assertEqual(code, 409, denied)
        self.assertEqual(denied["code"], "personal_model_changed")
        self.assertNotIn("tool_results", denied)
        self.assertEqual(self.model.shared["calls"], [], "Obsolete model snapshots must not send a question")
        code, final, _ = self.request("GET", "/api/chat/models")
        self.assertEqual(code, 200, final)
        self.assertEqual((final["selected_model"], final["thinking"]), ("synthetic-fast", False))


if __name__ == "__main__":
    unittest.main()
