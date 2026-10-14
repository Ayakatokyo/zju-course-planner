"""Synthetic school transports exercise local-session ownership and failure paths."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest

from course_assistant.auth import AccessError, AuthManager, SCHOOL_MODE, SCHOOL_REALM
from course_assistant.school_accounts import SchoolAccountService
from course_assistant.storage import Store
from course_assistant.student_sources import digest


PROFILE_A = {"source_subject_id": "SYNTHETIC-A", "name": "合成学生 A", "cohort": 2023,
             "college_name": "SYNTHETIC-COLLEGE", "major_name": "SYNTHETIC-MAJOR"}
PROFILE_B = {**PROFILE_A, "source_subject_id": "SYNTHETIC-B", "name": "合成学生 B"}
PDF = b"%PDF-1.4\n" + b"SYNTHETIC-PDF-CONTENT\n" * 3 + b"%%EOF\n"


class FakeSchoolClient:
    def __init__(self, profile=None, *, login_state="authenticated", requests=None):
        self.profile = deepcopy(profile or PROFILE_A)
        self.login_state = login_state
        self.session_epoch = 1
        self.closed = False
        self.requests = list(requests or [])
        self.request_calls = []
        self.login_calls = 0
        self.captcha_submissions = 0

    def login(self, username, password):
        self.login_calls += 1
        if username == "SYNTHETIC-RAISE":
            raise ValueError(password)
        return {"state": self.login_state, "profile": deepcopy(self.profile), "session_epoch": self.session_epoch}

    def get_profile(self):
        return {"state": self.login_state, "profile": deepcopy(self.profile), "session_epoch": self.session_epoch}

    def request(self, spec):
        self.request_calls.append(deepcopy(spec))
        if self.requests:
            result = self.requests.pop(0)
            if callable(result):
                return result(self, spec)
            if isinstance(result, Exception):
                raise result
            return {"session_epoch": self.session_epoch, **deepcopy(result)}
        if spec["operation"] == "plan_document":
            return {"state": "business", "http_status": 200, "content_type": "application/pdf",
                    "session_epoch": self.session_epoch, "payload": PDF, "source_subject_id": self.profile["source_subject_id"]}
        if spec["operation"] == "plan_catalogue":
            rows = [{"plan_ref": "SYNTHETIC-PLAN", **spec["scope"], "cohort_provenance": "source_row"}]
        else:
            rows = [{"kcmc": self.profile["source_subject_id"] + "-COURSE", "kcdm": "SYNTHETIC-COURSE", "xf": "2"}]
        return {"state": "business", "http_status": 200, "content_type": "application/json",
                "session_epoch": self.session_epoch, "source_subject_id": self.profile["source_subject_id"],
                "payload": {"items": rows, "total": len(rows)}}

    def get_captcha(self):
        return {"state": "captcha_ready", "content_type": "image/png", "image": b"\x89PNG\r\n\x1a\nSYNTHETIC-CAPTCHA"}

    def submit_captcha(self, text):
        self.captcha_submissions += 1
        self.login_state = "authenticated"
        return {"state": "captcha_submitted"}

    def close(self):
        self.closed = True
        self.session_epoch += 1


class SchoolAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1000.0]
        self.store = Store(Path(self.temp.name) / "app.sqlite3")
        self.auth = AuthManager(self.store, mock_enabled=True, clock=lambda: self.now[0])
        self.clients = []
        self.service = SchoolAccountService(self.auth, lambda: self.clients.pop(0), root=Path(self.temp.name).resolve() / "private" / "school", clock=lambda: self.now[0])
        self.addCleanup(self.service.close)

    def login(self, client=None, previous=None):
        client = client or FakeSchoolClient()
        self.clients.append(client)
        token, result = self.service.login({"username": "SYNTHETIC-INPUT-NOT-OWNER", "password": "SYNTHETIC-PASSWORD"}, previous)
        return token, self.auth.authenticate(token), client, result

    def fetch(self, context, **kwargs):
        return self.service.fetch(context, {"operation": "academic", "target_term": "2026-2027-AW", **kwargs})

    def test_mock_disabled_can_authenticate_verified_personal_school_client(self):
        self.auth.mock_enabled = False
        token, context, _, result = self.login()
        self.assertEqual((context.auth_mode, context.security_realm, context.student_id), (SCHOOL_MODE, SCHOOL_REALM, "SYNTHETIC-A"))
        self.assertEqual(result["identity"]["name"], "合成学生 A")
        self.assertEqual(result["project_sso_status"], "not_configured")
        self.assertNotIn("SYNTHETIC-INPUT-NOT-OWNER", str(result))
        self.assertEqual(self.auth.authenticate(token), context)

    def test_failed_login_or_missing_school_subject_creates_no_app_session(self):
        for client in (FakeSchoolClient(login_state="source_session_expired"), FakeSchoolClient(profile={"name": "合成学生"})):
            self.clients.append(client)
            try:
                token, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
                self.assertIsNone(token)
                self.assertFalse(result["authenticated"])
            except AccessError as error:
                self.assertEqual(error.reason, "school_identity_unverified")
            self.assertTrue(client.closed)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0], 0)

    def test_mock_context_and_forged_school_context_cannot_fetch(self):
        _, context, _, _ = self.login()
        mock_token, _ = self.auth.login_mock({"profile_id": "demo-a"})
        for wrong in (self.auth.authenticate(mock_token), replace(context, student_id="SYNTHETIC-B"),
                      replace(context, auth_mode="school_official"), replace(context, profile_name="伪造名字")):
            with self.assertRaises(AccessError):
                self.fetch(wrong)

    def test_two_accounts_and_mock_have_separate_source_clients_and_private_roots(self):
        _, a, client_a, _ = self.login()
        _, b, client_b, _ = self.login(FakeSchoolClient(PROFILE_B))
        result_a, result_b = self.fetch(a), self.fetch(b)
        self.assertIn("SYNTHETIC-A-COURSE", str(result_a["records"]))
        self.assertNotIn("SYNTHETIC-B", str(result_a))
        self.assertIn("SYNTHETIC-B-COURSE", str(result_b["records"]))
        self.assertEqual((len(client_a.request_calls), len(client_b.request_calls)), (1, 1))
        owner_roots = [path for path in self.service.root.iterdir() if path.is_dir()]
        self.assertEqual(len(owner_roots), 2)

    def test_login_rotation_and_logout_close_school_cookies_and_revoke_app_session(self):
        token, old, client, _ = self.login()
        next_token, current, next_client, _ = self.login(previous=token)
        self.assertTrue(client.closed)
        with self.assertRaises(AccessError):
            self.auth.authenticate(token)
        self.service.logout(current)
        self.assertTrue(next_client.closed)
        with self.assertRaises(AccessError):
            self.auth.authenticate(next_token)
        with self.assertRaises(AccessError):
            self.fetch(old)

    def test_restart_does_not_restore_school_cookie_from_app_database(self):
        token, _, _, _ = self.login()
        restarted_auth = AuthManager(self.store, clock=lambda: self.now[0])
        restarted = SchoolAccountService(restarted_auth, FakeSchoolClient, root=self.service.root, clock=lambda: self.now[0])
        self.addCleanup(restarted.close)
        with self.assertRaises(AccessError) as error:
            restarted_auth.authenticate(token)
        self.assertEqual(error.exception.reason, "school_connection_expired")

    def test_source_rows_with_another_subject_are_blocked_before_any_publication(self):
        reply = {"state": "business", "http_status": 200, "content_type": "application/json",
                 "source_subject_id": "SYNTHETIC-B", "payload": {"items": [{"kcmc": "SYNTHETIC-PRIVATE-B"}], "total": 1}}
        _, context, client, _ = self.login(FakeSchoolClient(requests=[reply]))
        result = self.fetch(context)
        self.assertEqual((result["status"], result["records"]), ("source_subject_changed", []))
        self.assertTrue(client.closed)
        self.assertNotIn("SYNTHETIC-PRIVATE-B", str(result))
        self.assertNotIn("SYNTHETIC-PRIVATE-B", "".join(path.read_text() for path in self.service.root.rglob("*.json")))

    def test_same_owner_password_free_epoch_refresh_retries_once(self):
        def refresh(client, spec):
            client.session_epoch += 1
            return {"state": "source_session_expired", "session_epoch": client.session_epoch}
        _, context, client, _ = self.login(FakeSchoolClient(requests=[refresh]))
        result = self.fetch(context)
        self.assertEqual((result["status"], result["data_source"], len(client.request_calls)), ("complete_within_scope", "live", 2))
        self.assertEqual(client.login_calls, 1)

    def test_epoch_refresh_to_other_school_identity_does_not_retry_or_return_rows(self):
        def switch(client, spec):
            client.session_epoch += 1
            client.profile = deepcopy(PROFILE_B)
            return {"state": "source_session_expired", "session_epoch": client.session_epoch}
        _, context, client, _ = self.login(FakeSchoolClient(requests=[switch]))
        with self.assertRaises(AccessError) as error:
            self.fetch(context)
        self.assertEqual(error.exception.reason, "source_subject_changed")
        self.assertEqual(len(client.request_calls), 1)
        self.assertTrue(client.closed)

    def test_expired_school_session_has_no_cached_body_and_same_owner_relogin_retries(self):
        _, context, client, _ = self.login()
        self.fetch(context)
        client.requests.append({"state": "source_session_expired"})
        failed = self.fetch(context, force_refresh=True)
        self.assertEqual((failed["status"], failed["records"], failed["data_source"]), ("source_session_expired", [], "none"))
        next_client = FakeSchoolClient()
        self.clients.append(next_client)
        result = self.service.reauthenticate(context, {"username": "SYNTHETIC-FRESH-INPUT", "password": "SYNTHETIC-FRESH-PASSWORD"})
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["retry"]["data_source"], "live")
        self.assertTrue(client.closed)
        self.assertEqual(len(next_client.request_calls), 1)

    def test_relogin_cannot_switch_owner_and_leaves_no_other_user_body(self):
        _, context, original, _ = self.login()
        other = FakeSchoolClient(PROFILE_B)
        self.clients.append(other)
        with self.assertRaises(AccessError) as error:
            self.service.reauthenticate(context, {"username": "SYNTHETIC-B", "password": "SYNTHETIC-PASSWORD"})
        self.assertEqual(error.exception.reason, "source_subject_changed")
        self.assertTrue(other.closed)
        self.assertFalse(original.closed)
        self.assertEqual(self.auth.require_context(context).student_id, "SYNTHETIC-A")

    def test_profile_school_expiry_preserves_app_identity_for_reauthentication(self):
        token, context, original, _ = self.login()
        original.login_state = "source_session_expired"
        with self.assertRaises(AccessError) as error:
            self.service.profile(context)
        self.assertEqual((error.exception.reason, error.exception.status), ("source_session_expired", 409))
        self.assertEqual(self.auth.authenticate(token).student_id, "SYNTHETIC-A")
        next_client = FakeSchoolClient()
        self.clients.append(next_client)
        result = self.service.reauthenticate(context, {"username": "SYNTHETIC-FRESH-INPUT", "password": "SYNTHETIC-FRESH-PASSWORD"})
        self.assertTrue(result["authenticated"])
        self.assertEqual(self.service.profile(context)["profile"]["source_subject_id"], "SYNTHETIC-A")
        self.assertTrue(original.closed)

    def test_profile_application_expiry_remains_401_without_school_call(self):
        _, context, client, _ = self.login()
        self.now[0] += 1801
        with self.assertRaises(AccessError) as error:
            self.service.profile(context)
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(client.request_calls, [])

    def _blocked_reauthentication(self, context, client, revoke):
        entered, release = threading.Event(), threading.Event()
        original_login = client.login
        def blocked_login(username, password):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic test wait expired")
            return original_login(username, password)
        client.login = blocked_login
        self.clients.append(client)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.service.reauthenticate, context,
                                     {"username": "SYNTHETIC-FRESH-INPUT", "password": "SYNTHETIC-FRESH-PASSWORD"})
            try:
                self.assertTrue(entered.wait(2))
                revoke()
            finally:
                release.set()
            with self.assertRaises(AccessError) as error:
                future.result(timeout=3)
        return error.exception

    def test_logout_during_fresh_reauthentication_closes_authenticated_and_challenge_clients(self):
        for state in ("authenticated", "captcha_required"):
            with self.subTest(state=state):
                _, context, original, _ = self.login()
                fresh = FakeSchoolClient(login_state=state)
                error = self._blocked_reauthentication(context, fresh, lambda: self.service.logout(context))
                self.assertEqual(error.status, 401)
                self.assertTrue(original.closed)
                self.assertTrue(fresh.closed)
                self.assertNotIn(context.session_ref, self.service._sessions)
                self.assertEqual(self.service._login_challenges, {})

    def test_expiry_during_fresh_reauthentication_closes_authenticated_and_challenge_clients(self):
        for state in ("authenticated", "captcha_required"):
            with self.subTest(state=state):
                _, context, _, _ = self.login()
                fresh = FakeSchoolClient(login_state=state)
                def expire():
                    self.now[0] += 1801
                error = self._blocked_reauthentication(context, fresh, expire)
                self.assertEqual(error.status, 401)
                self.assertTrue(fresh.closed)
                self.assertEqual(self.service._login_challenges, {})
                self.assertNotEqual(self.service._sessions[context.session_ref].client, fresh)
                self.service.discard_revoked_sessions()

    def test_concurrent_reauthentication_keeps_newer_connection_and_closes_late_client(self):
        _, context, original, _ = self.login()
        late, current = FakeSchoolClient(), FakeSchoolClient()
        def replace_connection():
            self.clients.append(current)
            result = self.service.reauthenticate(context, {"username": "SYNTHETIC-FRESH-INPUT", "password": "SYNTHETIC-FRESH-PASSWORD"})
            self.assertTrue(result["authenticated"])
        error = self._blocked_reauthentication(context, late, replace_connection)
        self.assertEqual((error.reason, error.status), ("source_session_expired", 409))
        self.assertTrue(original.closed)
        self.assertTrue(late.closed)
        self.assertFalse(current.closed)
        self.assertIs(self.service._sessions[context.session_ref].client, current)
        self.assertEqual(self.auth.require_context(context).student_id, "SYNTHETIC-A")

    def test_query_captcha_is_session_bound_expires_and_retries_only_once(self):
        _, context, client, _ = self.login(FakeSchoolClient(requests=[{"state": "captcha_required"}]))
        self.assertEqual(self.fetch(context)["records"], [])
        captcha = self.service.captcha(context)
        with self.assertRaises(AccessError):
            self.service.submit_captcha(context, {"text": "1234", "challenge_id": "SYNTHETIC-WRONG"})
        result = self.service.submit_captcha(context, {"text": "1234", "challenge_id": captcha["challenge_id"]})
        self.assertEqual((result["data_source"], client.captcha_submissions, len(client.request_calls)), ("live", 1, 2))
        with self.assertRaises(AccessError):
            self.service.submit_captcha(context, {"text": "1234"})
        client.requests.append({"state": "captcha_required"})
        self.fetch(context, force_refresh=True)
        self.service.captcha(context)
        self.now[0] += 301
        with self.assertRaises(AccessError):
            self.service.submit_captcha(context, {"text": "1234"})

    def test_initial_login_captcha_stores_no_password_and_requires_fresh_credentials(self):
        client = FakeSchoolClient(login_state="captcha_required")
        self.clients.append(client)
        token, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-SECRET-FIRST"})
        self.assertIsNone(token)
        self.assertEqual(result["state"], "captcha_required")
        self.assertNotIn("SYNTHETIC-SECRET-FIRST", str(self.service.__dict__))
        next_token, logged = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-SECRET-FRESH",
                                                 "challenge_id": result["challenge_id"], "captcha_text": "1234"})
        self.assertTrue(logged["authenticated"])
        self.assertEqual(self.auth.authenticate(next_token).student_id, "SYNTHETIC-A")
        with self.assertRaises(AccessError):
            self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD",
                                "challenge_id": result["challenge_id"], "captcha_text": "1234"})

    def test_initial_login_captcha_accepts_png_jpeg_and_both_gif_signatures(self):
        for content_type, signature in (("image/png", b"\x89PNG\r\n\x1a\n"), ("image/jpeg", b"\xff\xd8\xff"),
                                        ("image/gif", b"GIF87a"), ("image/gif", b"GIF89a")):
            with self.subTest(content_type=content_type, signature=signature):
                client = FakeSchoolClient(login_state="captcha_required")
                client.get_captcha = lambda: {"state": "captcha_ready", "content_type": content_type,
                                              "image": signature + b"SYNTHETIC-CAPTCHA"}
                self.clients.append(client)
                token, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
                self.assertIsNone(token)
                self.assertEqual(result["captcha"]["content_type"], content_type)
                self.assertEqual(result["state"], "captcha_required")

    def test_query_captcha_accepts_png_jpeg_and_both_gif_signatures(self):
        for content_type, signature in (("image/png", b"\x89PNG\r\n\x1a\n"), ("image/jpeg", b"\xff\xd8\xff"),
                                        ("image/gif", b"GIF87a"), ("image/gif", b"GIF89a")):
            with self.subTest(content_type=content_type, signature=signature):
                _, context, client, _ = self.login(FakeSchoolClient(requests=[{"state": "captcha_required"}]))
                self.fetch(context)
                client.get_captcha = lambda: {"state": "captcha_ready", "content_type": content_type,
                                              "image": signature + b"SYNTHETIC-CAPTCHA"}
                captcha = self.service.captcha(context)
                self.assertEqual(captcha["content_type"], content_type)
                self.assertEqual(captcha["image"], signature + b"SYNTHETIC-CAPTCHA")

    def test_invalid_captcha_signatures_and_svg_are_rejected_for_login_and_query(self):
        for content_type, image in (("image/png", b"SYNTHETIC-INVALID"), ("image/jpeg", b"GIF89aSYNTHETIC"),
                                    ("image/gif", b"GIF88aSYNTHETIC"), ("image/svg+xml", b"<svg/>")):
            with self.subTest(content_type=content_type):
                invalid = {"state": "captcha_ready", "content_type": content_type, "image": image}
                client = FakeSchoolClient(login_state="captcha_required")
                client.get_captcha = lambda: deepcopy(invalid)
                self.clients.append(client)
                token, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
                self.assertIsNone(token)
                self.assertNotIn("captcha", result)
                self.assertTrue(client.closed)
                _, context, query_client, _ = self.login(FakeSchoolClient(requests=[{"state": "captcha_required"}]))
                self.fetch(context)
                query_client.get_captcha = lambda: deepcopy(invalid)
                with self.assertRaises(AccessError) as error:
                    self.service.captcha(context)
                self.assertEqual(error.exception.reason, "interactive_verification_required")

    def test_remote_errors_and_unknown_payload_fields_never_export_credentials(self):
        self.clients.append(FakeSchoolClient())
        with self.assertRaises(AccessError) as error:
            self.service.login({"username": "SYNTHETIC-RAISE", "password": "SYNTHETIC-SECRET-ERROR"})
        self.assertNotIn("SYNTHETIC-SECRET-ERROR", str(error.exception))
        _, context, client, result = self.login(FakeSchoolClient(profile={**PROFILE_A, "password": "SYNTHETIC-DROPPED-PASSWORD", "cookie": "SYNTHETIC-DROPPED-COOKIE"}))
        client.requests.append({"state": "business", "http_status": 200, "content_type": "application/json",
                                "payload": {"items": [{"kcmc": "SYNTHETIC-COURSE", "cookie": "SYNTHETIC-DROPPED-COOKIE"}], "total": 1}})
        fetched = self.fetch(context)
        private = "".join(path.read_text() for path in self.service.root.rglob("*.json"))
        with self.store.connect() as db:
            stored = str(db.execute("SELECT * FROM personal_school_identities").fetchall()) + str(db.execute("SELECT * FROM security_events").fetchall())
        for text in (str(result), str(fetched), private, stored):
            self.assertNotIn("SYNTHETIC-DROPPED-PASSWORD", text)
            self.assertNotIn("SYNTHETIC-DROPPED-COOKIE", text)

    def test_failed_login_diagnostics_and_audit_keep_only_protocol_allowlist(self):
        client = FakeSchoolClient()
        client.login = lambda username, password: {"state": "source_unavailable", "diagnostics": {
            "failure_step": "service_callback", "http_status": 502, "transport_error": "tls",
            "url": "https://SYNTHETIC-SECRET-URL", "cookie": "SYNTHETIC-SECRET-COOKIE",
            "password": password, "owner": username, "body": "SYNTHETIC-SECRET-BODY"},
            "profile": {"source_subject_id": "SYNTHETIC-SECRET-IDENTITY"}}
        self.clients.append(client)
        token, result = self.service.login({"username": "SYNTHETIC-SECRET-ACCOUNT", "password": "SYNTHETIC-SECRET-PASSWORD"})
        self.assertIsNone(token)
        self.assertEqual(result["diagnostics"], {"failure_step": "service_callback", "http_status": 502, "transport_error": "tls"})
        with self.store.connect() as db:
            events = db.execute("SELECT principal,action,decision,reason FROM security_events").fetchall()
        self.assertEqual(events, [("anonymous-school", "school_login", "deny",
                                  "code=source_unavailable|phase=service_callback|status=502|transport=tls")])
        self.assertTrue(client.closed)
        self.assertNotIn("SYNTHETIC-SECRET", str(result) + str(events))
        self.assertEqual(list(self.service.root.rglob("*.json")), [])

    def test_login_diagnostics_reject_unknown_values_and_non_integer_status(self):
        for diagnostics in ({"failure_step": "SYNTHETIC-SECRET-PHASE", "http_status": True, "transport_error": "SYNTHETIC-SECRET-ERROR"},
                            {"failure_step": ["cas_form"], "http_status": 1000, "transport_error": {"timeout": True}},
                            {"failure_step": None, "http_status": "403", "transport_error": None},
                            "SYNTHETIC-SECRET-DIAGNOSTICS"):
            with self.subTest(diagnostics=diagnostics):
                client = FakeSchoolClient()
                client.login = lambda username, password: {"state": "SYNTHETIC-SECRET-STATE", "diagnostics": diagnostics}
                self.clients.append(client)
                _, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
                self.assertEqual(result["state"], "source_unavailable")
                self.assertEqual(result["diagnostics"], {"failure_step": None, "http_status": None, "transport_error": None})
        with self.store.connect() as db:
            reasons = db.execute("SELECT reason FROM security_events").fetchall()
        self.assertEqual(reasons, [("code=source_unavailable|phase=unknown|status=none|transport=none",)] * 4)

    def test_login_diagnostics_preserve_three_digit_school_http_statuses(self):
        for status in (100, 599, 600, 901, 999):
            with self.subTest(status=status):
                client = FakeSchoolClient()
                client.login = lambda username, password: {"state": "source_unavailable", "diagnostics": {
                    "failure_step": "own_profile", "http_status": status, "transport_error": None}}
                self.clients.append(client)
                _, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
                self.assertEqual(result["diagnostics"]["http_status"], status)
                with self.store.connect() as db:
                    reason = db.execute("SELECT reason FROM security_events ORDER BY id DESC LIMIT 1").fetchone()[0]
                self.assertEqual(reason, f"code=source_unavailable|phase=own_profile|status={status}|transport=none")

    def test_login_menu_context_failure_has_literal_step_and_safe_audit(self):
        client = FakeSchoolClient()
        client.login = lambda username, password: {"state": "source_unavailable", "diagnostics": {
            "failure_step": "menu_context", "http_status": 911, "transport_error": None,
            "sessionUserKey": "SYNTHETIC-SECRET-MENU", "body": "SYNTHETIC-SECRET-BODY"}}
        self.clients.append(client)
        _, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
        self.assertEqual(result["diagnostics"], {"failure_step": "menu_context", "http_status": 911, "transport_error": None})
        with self.store.connect() as db:
            reason = db.execute("SELECT reason FROM security_events ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(reason, "code=source_unavailable|phase=menu_context|status=911|transport=none")
        self.assertNotIn("SYNTHETIC-SECRET", str(result) + reason)

    def test_raised_login_errors_audit_only_fixed_generic_failure(self):
        client = FakeSchoolClient()
        self.clients.append(client)
        with self.assertRaises(AccessError) as error:
            self.service.login({"username": "SYNTHETIC-RAISE", "password": "SYNTHETIC-SECRET-EXCEPTION"})
        self.assertEqual((error.exception.reason, error.exception.status), ("source_unavailable", 503))
        with self.store.connect() as db:
            events = db.execute("SELECT principal,action,decision,reason FROM security_events").fetchall()
        self.assertEqual(events, [("anonymous-school", "school_login", "deny",
                                  "code=source_unavailable|phase=unknown|status=none|transport=none")])
        self.assertNotIn("SYNTHETIC-SECRET-EXCEPTION", str(events) + str(error.exception))
        self.assertTrue(client.closed)

    def test_login_captcha_metadata_and_mock_login_do_not_gain_diagnostics(self):
        client = FakeSchoolClient(login_state="captcha_required")
        self.clients.append(client)
        _, challenge = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
        self.assertEqual(set(challenge), {"authenticated", "state", "challenge_id", "captcha"})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events").fetchone()[0], 0)
        token, mock = self.auth.login_mock({"profile_id": "demo-a"})
        self.assertEqual(self.auth.authenticate(token).auth_mode, "mock")
        self.assertNotIn("diagnostics", mock)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events WHERE action='school_login'").fetchone()[0], 0)

    def test_login_captcha_image_failure_preserves_only_its_safe_diagnostic_step(self):
        client = FakeSchoolClient(login_state="captcha_required")
        client.get_captcha = lambda: {"state": "source_unavailable", "diagnostics": {
            "failure_step": "login_captcha", "http_status": None, "transport_error": "timeout",
            "body": "SYNTHETIC-SECRET-IMAGE-BODY"}}
        self.clients.append(client)
        _, result = self.service.login({"username": "SYNTHETIC-A", "password": "SYNTHETIC-PASSWORD"})
        self.assertEqual(result["diagnostics"], {"failure_step": "login_captcha", "http_status": None, "transport_error": "timeout"})
        self.assertNotIn("captcha", result)
        self.assertTrue(client.closed)
        with self.store.connect() as db:
            reason = db.execute("SELECT reason FROM security_events").fetchone()[0]
        self.assertEqual(reason, "code=source_unavailable|phase=login_captcha|status=none|transport=timeout")
        self.assertNotIn("SYNTHETIC-SECRET", str(result) + reason)

    def test_sources_are_immutable_and_only_private_permissions(self):
        _, context, _, _ = self.login()
        first = self.fetch(context)
        files = sorted(self.service.root.rglob("*.json"))
        before = {path: path.read_bytes() for path in files}
        cached = self.fetch(context)
        self.assertEqual(cached["data_source"], "historical_cache")
        self.assertEqual(before, {path: path.read_bytes() for path in files})
        self.assertTrue(first["source_alias"].startswith("SOURCE-"))
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        for path in self.service.root.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_plan_is_on_demand_for_profile_cohort_and_owned_pdf(self):
        _, a, client, _ = self.login()
        _, b, _, _ = self.login(FakeSchoolClient(PROFILE_B))
        result = self.service.acquire_plan(a)
        self.assertEqual(result["status"], "acquired_pending_review")
        self.assertEqual(result["binding_status"], "not_verified")
        self.assertEqual(result["rules_status"], "not_reviewed")
        self.assertEqual(client.request_calls[0]["scope"]["cohort"], 2023)
        self.assertEqual(self.service.read_plan_document(a, result["source_alias"]), PDF)
        with self.assertRaises(AccessError):
            self.service.read_plan_document(b, result["source_alias"])
        cached = self.service.acquire_plan(a)
        self.assertEqual(cached["data_source"], "historical_cache")

    def test_plan_catalogue_known_incomplete_scope_does_not_download_pdf(self):
        catalogue = {"state": "business", "http_status": 200, "content_type": "application/json", "payload": {
            "items": [{"plan_ref": "SYNTHETIC-PLAN", **{key: PROFILE_A[key] for key in ("cohort", "college_name", "major_name")}}], "total": 2}}
        _, context, client, _ = self.login(FakeSchoolClient(requests=[catalogue]))
        result = self.service.acquire_plan(context)
        self.assertEqual(result["status"], "invalid_plan_catalogue")
        self.assertEqual(len(client.request_calls), 1)

    def test_invalid_requests_are_rejected_without_source_calls(self):
        _, context, client, _ = self.login()
        for body in ({"operation": "academic", "target_term": "2026-2028-AW"},
                     {"operation": "academic", "target_term": "2026-2027-AW", "owner": "SYNTHETIC-B"},
                     {"operation": "course_update", "target_term": "2026-2027-AW"},
                     {"operation": "academic", "target_term": "2026-2027-AW", "force_refresh": "yes"}):
            with self.assertRaises(AccessError):
                self.service.fetch(context, body)
        self.assertEqual(client.request_calls, [])

    def test_major_academic_requires_exact_whole_history_scope(self):
        _, context, client, _ = self.login()
        for body in ({"operation": "major_academic", "target_term": "2026-2027-AW"},
                     {"operation": "academic", "target_term": "ALL-HISTORY"},
                     {"operation": "timetable", "target_term": "ALL-HISTORY"},
                     {"operation": "exams", "target_term": "ALL-HISTORY"}):
            with self.assertRaises(AccessError) as error:
                self.service.fetch(context, body)
            self.assertEqual(error.exception.reason, "invalid_school_request")
        self.assertEqual(client.request_calls, [])
        result = self.service.fetch(context, {"operation": "major_academic", "target_term": "ALL-HISTORY"})
        self.assertEqual(result["target_term"], "ALL-HISTORY")
        self.assertEqual(result["data_source"], "live")
        self.assertEqual(client.request_calls[0]["operation"], "major_academic")
        self.assertEqual(client.request_calls[0]["target_term"], "ALL-HISTORY")

    def test_revocation_during_fetch_prevents_late_records_and_capture(self):
        _, context, client, _ = self.login()
        def revoke(current, spec):
            self.service.logout(context)
            return {"state": "business", "http_status": 200, "content_type": "application/json",
                    "session_epoch": current.session_epoch,
                    "payload": {"items": [{"kcmc": "SYNTHETIC-LATE-PRIVATE"}], "total": 1}}
        client.requests.append(revoke)
        with self.assertRaises(AccessError):
            self.fetch(context)
        self.assertNotIn("SYNTHETIC-LATE-PRIVATE", "".join(path.read_text() for path in self.service.root.rglob("*.json")))

    def test_permission_failure_blocks_previously_acquired_pdf(self):
        _, context, client, _ = self.login()
        acquired = self.service.acquire_plan(context)
        client.requests.append({"state": "permission_denied"})
        denied = self.fetch(context, force_refresh=True)
        self.assertEqual(denied["records"], [])
        with self.assertRaises(AccessError) as error:
            self.service.read_plan_document(context, acquired["source_alias"])
        self.assertEqual(error.exception.status, 404)

    def test_plan_failed_refresh_marks_historical_and_blocks_old_pdf_body(self):
        _, context, client, _ = self.login()
        acquired = self.service.acquire_plan(context)
        client.requests.extend([OSError("SYNTHETIC-SECRET-ERROR"), OSError("SYNTHETIC-SECRET-ERROR")])
        failed = self.service.acquire_plan(context, force=True)
        self.assertEqual((failed["status"], failed["data_source"]), ("source_unavailable", "historical_only"))
        self.assertNotIn("SYNTHETIC-SECRET-ERROR", str(failed))
        with self.assertRaises(AccessError):
            self.service.read_plan_document(context, acquired["source_alias"])

    def test_mock_switch_and_expiration_cleanup_release_clients_without_school_requests(self):
        token, _, client, _ = self.login()
        self.auth.login_mock({"profile_id": "demo-a"}, token)
        self.assertEqual(self.service.discard_revoked_sessions()["discarded_sessions"], 1)
        self.assertTrue(client.closed)
        self.assertEqual(client.request_calls, [])
        _, _, next_client, _ = self.login()
        self.now[0] += 1801
        self.assertEqual(self.service.discard_revoked_sessions()["discarded_sessions"], 1)
        self.assertTrue(next_client.closed)
        self.assertEqual(next_client.request_calls, [])


if __name__ == "__main__":
    unittest.main()
