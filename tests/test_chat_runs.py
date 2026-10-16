"""Private run-event storage using only synthetic contexts and reasoning."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
import uuid

from course_assistant.auth import AuthContext
from course_assistant.chat_runs import (ChatRunError, ChatRunStore, MAX_EVENTS,
                                       MAX_REASONING_EVENT, MAX_REASONING_RUN, tool_event_fields)
from course_assistant.storage import Store


class ChatRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.store = Store(self.root / "synthetic.sqlite3")
        self.store.path.chmod(0o600)
        self.context = AuthContext("SYNTHETIC-P-A", "SYNTHETIC-SESSION-A", "SYNTHETIC-A",
                                   "personal_school_client", "v1", "school_client")
        self.other = AuthContext("SYNTHETIC-P-B", "SYNTHETIC-SESSION-B", "SYNTHETIC-B",
                                 "personal_school_client", "v1", "school_client")
        self.scope, self.active, self.auth_calls = "SYNTHETIC-SCOPE-1", True, 0
        self.logs = ChatRunStore(self.store, self.authorize)
        self.model = {"provider": "SyntheticProvider", "model": "synthetic-model-1", "remote": True, "thinking": True}

    def authorize(self, context):
        self.auth_calls += 1
        if not self.active:
            raise ValueError("Cookie: SYNTHETIC-MUST-NOT-LEAK")
        return {"scope_version": self.scope}

    def begin(self, **changes):
        return self.logs.begin(self.context, request_id=changes.get("request_id", str(uuid.uuid4())),
            conversation_id=changes.get("conversation_id"), model_status=changes.get("model_status", self.model),
            scope_version=changes.get("scope_version", self.scope))

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(ChatRunError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_run_event_lifecycle_summary_and_explicit_reasoning_detail(self):
        run = self.begin(conversation_id=str(uuid.uuid4()))
        self.logs.event(self.context, run, "model.start", {"step": 1, "status": "started"})
        brief = self.logs.event(self.context, run, "model.end", {"elapsed_ms": 12.5,
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                      "completion_tokens_details": {"reasoning_tokens": 2}},
            "request_id": "synthetic-request-01", "completion_id": "chatcmpl-synthetic_01", "http_status": 200,
            "reasoning_content": "供应商可见推理：先检索已读记录。"})
        self.assertNotIn("reasoning_content", brief["fields"])
        self.logs.event(self.context, run, "tool.start", {"tool_name": "query_my_records"})
        self.logs.event(self.context, run, "tool.end", {"tool_name": "query_my_records", "record_count": 3})
        self.logs.event(self.context, run, "answer.validated", {"citation_count": 2, "status": "accepted"})
        done = self.logs.finish(self.context, run, "completed")
        self.assertTrue(done["thinking"])
        self.assertEqual(done["event_count"], 5)
        self.assertEqual(done, self.logs.finish(self.context, run, "completed"))
        self.assertNotIn("reasoning_content", str(self.logs.list(self.context, self.scope)))
        default = self.logs.read(self.context, run, self.scope)
        self.assertNotIn("reasoning_content", default["events"][1]["fields"])
        details = self.logs.read(self.context, run, self.scope, include_reasoning=True)
        self.assertIn("供应商可见推理", details["events"][1]["fields"]["reasoning_content"])
        self.assertEqual(details["reasoning_kind"], "provider_visible_reasoning")
        self.assertFalse(details["reasoning_is_execution_proof"])
        self.assertEqual(details["events"][1]["fields"]["http_status"], 200)

    def test_tool_rejection_is_stored_as_validation_not_a_dispatched_tool(self):
        run = self.begin()
        fields = {"tool_name": "query_my_records",
            "error_code": "invalid_tool_arguments", "failure_detail": "argument_schema_mismatch",
            "attempt": 1, "count": 1, "status": "retrying"}
        self.assert_code("invalid_run_event", self.logs.event, self.context, run, "tool.rejected",
                         {**fields, "arguments": "NEVER-STORE"})
        self.logs.event(self.context, run, "tool.rejected", fields)
        self.logs.finish(self.context, run, "completed")
        detail = self.logs.read(self.context, run, self.scope)
        self.assertEqual(detail["events"][0]["kind"], "tool.rejected")
        self.assertEqual(detail["events"][0]["fields"]["status"], "retrying")
        self.assertNotIn("NEVER-STORE", str(detail))

    def test_begin_only_keeps_model_projection_and_is_idempotent(self):
        request = str(uuid.uuid4())
        model = {**self.model, "api_key": "SYNTHETIC-DO-NOT-PERSIST", "headers": {"Cookie": "SYNTHETIC"}}
        first = self.begin(request_id=request, model_status=model)
        self.assertEqual(first, self.begin(request_id=request, model_status=model))
        self.assertNotIn("DO-NOT-PERSIST", str(self.logs.read(self.context, first, self.scope, include_reasoning=True)))
        self.assert_code("chat_run_request_reused", self.begin, request_id=request, model_status={**model, "thinking": False})

    def test_owner_realm_and_authorization_generation_are_separate(self):
        run = self.begin()
        self.assertEqual(self.logs.list(self.other, self.scope)["runs"], [])
        self.assert_code("inaccessible_chat_run", self.logs.read, self.other, run, self.scope)
        realm = AuthContext(self.context.principal_id, self.context.session_ref, self.context.student_id, "mock_development", "v1")
        self.assert_code("inaccessible_chat_run", self.logs.read, realm, run, self.scope)
        old = self.scope
        self.scope = "SYNTHETIC-SCOPE-2"
        self.assertEqual(self.logs.list(self.context, self.scope)["runs"], [])
        self.assert_code("chat_run_scope_changed", self.logs.read, self.context, run, old)
        self.assert_code("inaccessible_chat_run", self.logs.read, self.context, run, self.scope)
        self.assert_code("inaccessible_chat_run", self.logs.event, self.context, run, "model.start", {})

    def test_authentication_checked_before_and_after_return_without_error_leaks(self):
        run = self.begin()
        self.active = False
        self.assert_code("chat_run_authorization_expired", self.logs.list, self.context, self.scope)
        self.active = True
        calls = 0
        def expire_after_read(context):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("SYNTHETIC-MUST-NOT-LEAK")
            return self.scope
        logs = ChatRunStore(self.store, expire_after_read)
        self.assert_code("chat_run_authorization_expired", logs.read, self.context, run, self.scope, include_reasoning=True)

    def test_callbacks_can_update_same_sqlite_without_transaction_deadlock(self):
        def database_auth(context):
            self.store.event(context.principal_id, "synthetic-auth", "allow", "synthetic-test")
            return self.scope
        logs = ChatRunStore(self.store, database_auth)
        run = logs.begin(self.context, request_id=str(uuid.uuid4()), model_status=self.model, scope_version=self.scope)
        logs.event(self.context, run, "model.start", {})
        logs.finish(self.context, run, "completed")
        self.assertEqual(logs.read(self.context, run, self.scope)["run"]["status"], "completed")

    def test_prompt_arguments_rows_headers_and_paths_are_not_event_fields(self):
        run = self.begin()
        for field in ("prompt", "messages", "tool_args", "arguments", "raw_rows", "raw_row", "cookie", "headers", "path", "error_message"):
            with self.subTest(field=field):
                self.assert_code("invalid_run_event", self.logs.event, self.context, run, "model.end", {field: "SYNTHETIC"})
        self.assertEqual(self.logs.read(self.context, run, self.scope)["events"], [])

    def test_actual_tool_summary_keeps_scope_and_coverage_without_query_text(self):
        run = self.begin()
        fields = tool_event_fields("query_my_records", {"operation": "major_academic", "target_term": "ALL-HISTORY",
            "query": "NEVER-SEND-COURSE-NAME"}, {"records": [{}] * 15, "matched_records": 60, "returned_records": 15,
            "coverage": {"captured_rows": 60, "reported_total": 104, "has_more": True, "complete_within_scope": False}})
        self.logs.event(self.context, run, "tool.end", fields)
        self.logs.event(self.context, run, "answer.rejected", {"error_code": "invalid_final_answer",
            "failure_detail": "answer_shape_invalid", "attempt": 0, "evidence_count": 14})
        self.logs.event(self.context, run, "answer.repair", {"status": "started", "attempt": 1})
        detail = self.logs.read(self.context, run, self.scope)
        self.assertNotIn("NEVER-SEND", str(detail))
        self.assertEqual(detail["events"][0]["fields"]["reported_total"], 104)
        self.assertEqual(detail["events"][0]["fields"]["query_mode"], "course_keyword")
        self.assertFalse(detail["events"][0]["fields"]["complete_within_scope"])
        self.assertEqual(detail["events"][1]["fields"]["failure_detail"], "answer_shape_invalid")

    def test_scope_diagnostic_fields_reject_free_text_and_wrong_types(self):
        run = self.begin()
        for fields in ({"operation": []}, {"operation": "owner_student_id"}, {"target_term": "/private/source"},
                       {"target_term": "2099-2101-AW"}, {"query_mode": {"query": "raw"}}, {"query_mode": "raw"},
                       {"initiated_by": []}, {"initiated_by": "user-script"}, {"has_more": 1},
                       {"complete_within_scope": None}, {"reported_total": -1}):
            self.assert_code("invalid_run_event", self.logs.event, self.context, run, "tool.end", fields)
        self.assert_code("invalid_run_event", self.logs.event, self.context, run, "answer.rejected",
                         {"failure_detail": "raw answer text with spaces"})

    def test_reasoning_redacts_credentials_paths_and_structured_request_bodies(self):
        run = self.begin()
        text = "思路\nCookie: SYNTHETIC-COOKIE\nkey sk-abcdefghijklmnop\n/Users/synthetic/private.pdf https://example.test/?token=synthetic"
        self.logs.event(self.context, run, "model.end", {"reasoning_content": text})
        self.logs.event(self.context, run, "model.end", {"reasoning_content": '{"messages": [{"content":"SYNTHETIC-RAW-PROMPT"}]}'})
        detail = self.logs.read(self.context, run, self.scope, include_reasoning=True)
        raw = str(detail)
        for forbidden in ("SYNTHETIC-COOKIE", "sk-abcdefghijklmnop", "/Users/", "https://", "SYNTHETIC-RAW-PROMPT"):
            self.assertNotIn(forbidden, raw)
        self.assertTrue(detail["events"][0]["fields"]["reasoning_redacted"])

    def test_reasoning_single_event_and_run_limits_are_enforced(self):
        run = self.begin()
        for _ in range(6):
            self.logs.event(self.context, run, "model.end", {"reasoning_content": "合成推理" * 2000})
        details = self.logs.read(self.context, run, self.scope, include_reasoning=True)
        texts = [event["fields"]["reasoning_content"] for event in details["events"]]
        self.assertTrue(all(len(text) <= MAX_REASONING_EVENT for text in texts))
        self.assertEqual(sum(map(len, texts)), MAX_REASONING_RUN)
        self.assertEqual(details["run"]["reasoning_chars"], MAX_REASONING_RUN)
        self.assertEqual(texts[-1], "")

    def test_strict_counts_usage_http_ids_and_reasoning_types(self):
        run = self.begin()
        for fields in ({"elapsed_ms": float("nan")}, {"elapsed_ms": 10 ** 1000}, {"count": True},
                       {"usage": {"prompt_tokens": True}}, {"usage": {"headers": {}}},
                       {"http_status": True}, {"http_status": 600}, {"request_id": "value\nCookie:bad"},
                       {"completion_id": "https://example.test"}, {"reasoning_content": {"text": "bad"}}):
            self.assert_code("invalid_run_event", self.logs.event, self.context, run, "model.end", fields)
        self.assert_code("invalid_run_event", self.logs.event, self.context, run, "tool.start", {"reasoning_content": "bad"})
        self.assert_code("invalid_run_event", self.logs.event, self.context, run, "error", {})

    def test_error_codes_are_controlled_and_final_runs_reject_new_events(self):
        run = self.begin()
        self.logs.event(self.context, run, "error", {"stage": "model", "error_code": "model_http_401"})
        result = self.logs.finish(self.context, run, "failed", "model_http_401")
        self.assertEqual(result["error_code"], "model_http_401")
        self.assert_code("chat_run_finished", self.logs.event, self.context, run, "model.start", {})
        self.assert_code("chat_run_finished", self.logs.finish, self.context, run, "completed")
        self.assert_code("invalid_run_event", self.logs.finish, self.context, run, "failed", "/private/secret")

    def test_limit_and_concurrent_sequences_are_bounded_and_unique(self):
        run = self.begin()
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: self.logs.event(self.context, run, "model.start", {}), range(12)))
        self.assertEqual(sorted(value["sequence"] for value in values), list(range(1, 13)))
        with self.store.connect() as db:
            db.execute("UPDATE personal_chat_runs SET event_count=? WHERE id=?", (MAX_EVENTS, run))
        self.assert_code("chat_run_event_limit", self.logs.event, self.context, run, "model.start", {})
        self.assertEqual(self.logs.finish(self.context, run, "completed")["status"], "completed")

    def test_listing_is_limited_and_has_no_event_bodies(self):
        for _ in range(3):
            self.begin()
        listed = self.logs.list(self.context, self.scope, limit=2)
        self.assertEqual(len(listed["runs"]), 2)
        self.assertTrue(listed["has_more"])
        for limit in (True, 0, 51):
            self.assert_code("invalid_run_request", self.logs.list, self.context, self.scope, limit)
        self.assert_code("inaccessible_chat_run", self.logs.read, self.context, "../private", self.scope)

    def test_private_permissions_and_symlink_rejection(self):
        self.store.path.chmod(0o644)
        self.assert_code("private_chat_run_store_required", self.logs.list, self.context, self.scope)
        self.store.path.chmod(0o600)
        original = self.root / "original.sqlite3"
        self.store.path.rename(original)
        self.store.path.symlink_to(original)
        self.assert_code("private_chat_run_store_required", ChatRunStore, self.store)

    def test_old_table_migrates_thinking_default_without_losing_runs(self):
        run = self.begin(model_status={**self.model, "thinking": False})
        with self.store.connect() as db:
            # Recreate precisely the first logger schema, before Thinking was
            # a dedicated column, without relying on DROP COLUMN support.
            original_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='personal_chat_runs'").fetchone()[0]
            columns = ",".join(row[1] for row in db.execute("PRAGMA table_info(personal_chat_runs)") if row[1] != "thinking")
            db.execute("DROP TABLE personal_chat_run_events")
            db.execute("ALTER TABLE personal_chat_runs RENAME TO runs_before_migration")
            db.execute(original_sql.replace("thinking INTEGER NOT NULL DEFAULT 0,", ""))
            db.execute(f"INSERT INTO personal_chat_runs({columns}) SELECT {columns} FROM runs_before_migration")
            db.execute("DROP TABLE runs_before_migration")
        migrated = ChatRunStore(self.store, self.authorize)
        result = migrated.read(self.context, run, self.scope)
        self.assertFalse(result["run"]["thinking"])
        with self.store.connect() as db:
            self.assertIn("thinking", {row[1] for row in db.execute("PRAGMA table_info(personal_chat_runs)")})


if __name__ == "__main__":
    unittest.main()
