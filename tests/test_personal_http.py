"""D1 authorization, provenance and cancellation through the real HTTP handler."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from course_assistant.server import Application, make_handler
from validation.personal_fixture_data import synthetic_pdf
from validation.run_school_ui_fixture import NoOutboundModel
from validation.run_personal_ui_fixture import PersonalSyntheticSchool


class ObservedSyntheticSchool(PersonalSyntheticSchool):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.expired = False
        self.pdf_version = "1"

    def request(self, spec):
        self.calls.append(spec)
        if self.expired:
            return {"state": "source_session_expired", "session_epoch": self.session_epoch}
        result = super().request(spec)
        if spec["operation"] == "plan_document" and result.get("state") == "business":
            result["payload"] = synthetic_pdf(self.pdf_version)
        return result


class PersonalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clients = []
        def factory():
            client = ObservedSyntheticSchool()
            self.clients.append(client)
            return client
        self.app = Application(Path(self.temp.name).resolve() / "app.sqlite3", mock_enabled=True,
            model=NoOutboundModel(), school_client_factory=factory,
            personal_pdf_extractor=lambda body: ["Synthetic table SYNTHETIC-001 nominal credits 3. See page 2.",
                                                 "SYNTHETIC-001 does not count toward seminar. Unreviewed."])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = self.csrf = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, *, csrf=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if origin:
            headers["Origin"] = origin
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        result, fields = json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return response.status, result, fields

    def login(self, username="SYNTHETIC-LOGIN-A"):
        code, result, fields = self.request("POST", "/api/auth/school-login",
            {"username": username, "password": "SYNTHETIC-TRANSIENT"})
        self.assertEqual(code, 200, result)
        self.cookie = fields["Set-Cookie"].split(";", 1)[0]
        self.csrf = result["csrf_token"]

    def sync(self, operation="major_academic", term="ALL-HISTORY", force=True):
        code, result, _ = self.request("POST", "/api/school/sync",
            {"operation": operation, "target_term": term, "force_refresh": force})
        self.assertEqual(code, 200, result)
        return result

    def prepare(self):
        code, plan, _ = self.request("POST", "/api/school/plan", {})
        self.assertEqual(code, 200, plan)
        code, result, _ = self.request("POST", "/api/evidence/prepare", {"source_alias": plan["source_alias"]})
        self.assertEqual(code, 200, result)
        self.assertTrue(result["ready"])
        return plan

    def test_status_is_local_only_and_does_not_fetch_school_or_model(self):
        self.login()
        for path in ("/api/bootstrap", "/api/chat/status"):
            code, result, _ = self.request("GET", path)
            self.assertEqual(code, 200, result)
            status = result["chat"] if path.endswith("bootstrap") else result
            self.assertTrue(status["remote"])
            self.assertFalse(status["available"])
            self.assertTrue(status["local"]["available"])
            self.assertFalse(status["local"]["evidence"]["ready"])
            self.assertEqual(status["local"]["observations"]["snapshots"], [])
        self.assertEqual(self.clients[0].calls, [])

    def test_registered_scope_is_partial_duplicates_and_unknown_credits_survive_query(self):
        self.login()
        context = self.app.auth.authenticate(self.cookie.split("=", 1)[1])
        # Deliberately exercise the partial-acquisition path. The normal
        # default now reads all four pages of this 75-row source fixture.
        self.app.school._connection(context).source.maxpages = 3
        source = self.sync()
        self.assertEqual(len(source["records"]), 60)
        self.assertEqual(source["status"], "partial_acquisition")
        self.assertEqual(source["coverage"]["reported_total"], 75)
        self.assertEqual(source["coverage"]["pages_captured"], 3)
        self.assertTrue(source["coverage"]["has_more"])
        self.assertFalse(source["coverage"]["complete_within_scope"])
        code, result, _ = self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "query": "SYNTHETIC-PE-REPEAT", "limit": 20})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["observation_id"], source["observation_id"])
        self.assertEqual(len(result["records"]), 3)
        self.assertTrue(all(row["earned_credits"] is None for row in result["records"]))
        self.assertEqual(result["coverage"], source["coverage"])
        self.assertNotEqual(result["status"], "verified_complete")
        self.assertEqual(result["data_kind"], "personal_source")

    def test_default_acquisition_reads_complete_75_rows_and_summary_not_first_pages(self):
        self.login()
        source = self.sync()
        self.assertEqual(len(source["records"]), 75)
        self.assertEqual(source["status"], "complete_within_scope")
        self.assertEqual(source["coverage"], {"captured_rows": 75, "reported_total": 75,
            "pages_captured": 1, "complete_within_scope": True, "has_more": False})
        self.assertEqual([call["page"] for call in self.clients[0].calls], [1])
        self.assertEqual([call["page_size"] for call in self.clients[0].calls], [100])
        code, summary, _ = self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "observation_id": source["observation_id"], "mode": "summary"})
        self.assertEqual(code, 200, summary)
        self.assertEqual(summary["records"], [])
        self.assertEqual(summary["source_grade_summary"]["matched_records"], 75)
        self.assertEqual(summary["source_grade_summary"]["numeric_grade_count"], 60)
        self.assertEqual(summary["source_grade_summary"]["non_numeric_grade_count"], 15)
        self.assertEqual(summary["coverage"], source["coverage"])
        body = {"operation": "major_academic", "observation_id": source["observation_id"], "limit": 20}
        records, sizes = [], []
        while True:
            code, page, _ = self.request("POST", "/api/personal/records", body)
            self.assertEqual(code, 200, page)
            records.extend(page["records"])
            sizes.append(len(page["records"]))
            self.assertEqual(page["observation_id"], source["observation_id"])
            if not page["next_cursor"]:
                break
            body["cursor"] = page["next_cursor"]
        self.assertEqual(sizes, [20, 20, 20, 15])
        self.assertEqual(len({row["attempt_candidate_id"] for row in records}), 75)
        self.assertTrue(all(row["earned_credits"] is None for row in records))

    def test_pagination_is_pinned_to_observation_and_filter(self):
        self.login()
        first = self.sync()
        code, page, _ = self.request("POST", "/api/personal/records", {"operation": "major_academic", "limit": 20})
        self.assertEqual(code, 200, page)
        self.assertEqual(len(page["records"]), 20)
        self.sync()
        code, next_page, _ = self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "cursor": page["next_cursor"]})
        self.assertEqual(code, 200, next_page)
        self.assertEqual(next_page["observation_id"], first["observation_id"])
        self.assertNotEqual(next_page["records"], page["records"])
        self.assertEqual(self.request("POST", "/api/personal/records", {"operation": "major_academic",
            "query": "different", "cursor": page["next_cursor"]})[0], 409)

    def test_cached_read_keeps_original_capture_and_linked_observation(self):
        self.login()
        live = self.sync("timetable", "2026-2027-AW")
        cached = self.sync("timetable", "2026-2027-AW", force=False)
        self.assertEqual(cached["data_source"], "historical_cache")
        self.assertNotEqual(cached["observation_id"], live["observation_id"])
        code, result, _ = self.request("POST", "/api/personal/records",
            {"operation": "timetable", "observation_id": cached["observation_id"]})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["captured_at"], live["captured_at"])
        self.assertEqual(result["linked_observation_id"], live["observation_id"])

    def test_exact_document_search_resolves_page_and_exclusion_context_without_network(self):
        self.login()
        self.prepare()
        before = len(self.clients[0].calls)
        code, search, _ = self.request("POST", "/api/evidence/search", {"query": "SYNTHETIC-001", "limit": 5})
        self.assertEqual(code, 200, search)
        self.assertFalse(search["answer_support_verified"])
        self.assertTrue(search["matches"])
        code, resolved, _ = self.request("POST", "/api/evidence/resolve", {"reference": search["matches"][0]["reference"]})
        self.assertEqual(code, 200, resolved)
        self.assertIn("does not count", json.dumps(resolved))
        self.assertEqual(len(self.clients[0].calls), before)
        self.assertFalse(self.request("GET", "/api/chat/status")[1]["available"])
        self.assertEqual(self.request("POST", "/api/generate", {})[0], 409)

    @unittest.skipUnless(shutil.which("pdftotext"), "local pdftotext is required for the real extraction path")
    def test_valid_pdf_uses_default_local_extractor_and_preserves_two_physical_pages(self):
        self.app.school.personal_pdf_extractor = None
        self.login()
        self.prepare()
        code, result, _ = self.request("POST", "/api/evidence/search", {"query": "SYNTHETIC-001"})
        self.assertEqual(code, 200, result)
        self.assertEqual({row["page"] for row in result["matches"]}, {1, 2})

    def test_new_pdf_invalidates_previous_index_and_citations(self):
        self.login()
        self.prepare()
        hit = self.request("POST", "/api/evidence/search", {"query": "SYNTHETIC-001"})[1]["matches"][0]
        self.clients[0].pdf_version = "2"
        code, plan, _ = self.request("POST", "/api/school/plan", {"force_refresh": True})
        self.assertEqual(code, 200, plan)
        self.assertFalse(self.request("GET", "/api/chat/status")[1]["local"]["evidence"]["ready"])
        self.assertEqual(self.request("POST", "/api/evidence/resolve", {"reference": hit["reference"]})[0], 409)
        self.assertEqual(self.request("POST", "/api/evidence/prepare", {"source_alias": plan["source_alias"]})[0], 200)
        self.assertEqual(self.request("POST", "/api/evidence/resolve", {"reference": hit["reference"]})[0], 409)

    def test_cross_owner_and_same_owner_reauthentication_cannot_reuse_references(self):
        self.login()
        source = self.sync()
        self.prepare()
        reference = self.request("POST", "/api/evidence/search", {"query": "SYNTHETIC-001"})[1]["matches"][0]["reference"]
        self.assertEqual(self.request("POST", "/api/school/reauthenticate",
            {"username": "SYNTHETIC-LOGIN-A", "password": "SYNTHETIC-TRANSIENT"})[0], 200)
        self.assertEqual(self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "observation_id": source["observation_id"]})[0], 409)
        self.assertEqual(self.request("POST", "/api/evidence/resolve", {"reference": reference})[0], 409)
        self.login("SYNTHETIC-LOGIN-B")
        self.assertEqual(self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "observation_id": source["observation_id"]})[0], 409)

    def test_school_session_failure_disables_local_queries_even_when_cached_records_exist(self):
        self.login()
        self.sync()
        self.clients[0].expired = True
        failed = self.sync()
        self.assertEqual(failed["status"], "source_session_expired")
        code, status, _ = self.request("GET", "/api/chat/status")
        self.assertEqual(code, 200, status)
        self.assertFalse(status["local"]["available"])
        self.assertEqual(self.request("POST", "/api/personal/records", {"operation": "major_academic"})[0], 409)

    def test_http_epoch_change_starts_fresh_local_registry(self):
        self.login()
        source = self.sync()
        self.clients[0].session_epoch += 1
        code, status, _ = self.request("GET", "/api/chat/status")
        self.assertEqual(code, 200, status)
        self.assertEqual(status["local"]["observations"]["snapshots"], [])
        self.assertEqual(self.request("POST", "/api/personal/records",
            {"operation": "major_academic", "observation_id": source["observation_id"]})[0], 409)

    def test_failed_local_registration_preserves_successful_school_read(self):
        from course_assistant.personal_observations import PersonalObservationError, PersonalObservationStore
        self.login()
        with patch.object(PersonalObservationStore, "register", side_effect=PersonalObservationError("invalid_private_store")):
            result = self.sync()
        self.assertEqual(len(result["records"]), 75)
        self.assertEqual(result["coverage"]["captured_rows"], 75)
        self.assertFalse(result["coverage"]["has_more"])
        self.assertNotIn("observation_id", result)
        self.assertEqual(result["observation_registration"], {"status": "failed", "code": "invalid_private_store"})
        self.assertTrue(self.clients[0].calls)

    def test_local_constructor_io_and_permissions_failure_do_not_break_school_read(self):
        from course_assistant.personal_evidence import PersonalEvidenceError
        self.login()
        for error in (OSError("synthetic private path must not leak"), PersonalEvidenceError("private_permissions_required")):
            with self.subTest(kind=type(error).__name__), patch.object(self.app.school, "_personal_services", side_effect=error):
                result = self.sync()
                self.assertEqual(len(result["records"]), 75)
                self.assertEqual(result["coverage"]["pages_captured"], 1)
                self.assertFalse(result["coverage"]["has_more"])
                self.assertEqual(result["observation_registration"]["status"], "failed")
                self.assertNotIn("synthetic private path", json.dumps(result))

    def test_coordinator_rechecks_epoch_after_store_returns(self):
        from course_assistant.personal_observations import PersonalObservationStore
        self.login()
        self.sync()
        query = PersonalObservationStore.query
        def race(store, body):
            result = query(store, body)
            self.clients[0].session_epoch += 1
            return result
        with patch.object(PersonalObservationStore, "query", race):
            code, result, _ = self.request("POST", "/api/personal/records", {"operation": "major_academic"})
        self.assertEqual(code, 409, result)
        self.assertNotIn("records", result)

    def test_csrf_mock_and_arbitrary_query_inputs_are_rejected(self):
        self.login()
        self.assertEqual(self.request("POST", "/api/evidence/prepare", {"source_alias": "x"}, csrf=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/personal/records", {"operation": "major_academic"}, origin="http://example.com")[0], 403)
        self.assertEqual(self.clients[0].calls, [])
        for extra in ({"sql": "SELECT * FROM x"}, {"owner_student_id": "other"}, {"path": "/private/x"}):
            self.assertEqual(self.request("POST", "/api/personal/records", {"operation": "major_academic", **extra})[0], 409)
        code, login, headers = self.request("POST", "/api/auth/mock-login", {"profile_id": "demo-a"})
        self.assertEqual(code, 200, login)
        self.cookie, self.csrf = headers["Set-Cookie"].split(";", 1)[0], login["csrf_token"]
        self.assertEqual(self.request("POST", "/api/personal/records", {"operation": "major_academic"})[0], 403)

    def test_logout_revokes_inflight_extraction_without_waiting_for_it(self):
        started, release = threading.Event(), threading.Event()
        def blocked_extractor(body):
            started.set()
            release.wait(6)
            return ["SYNTHETIC-001"]
        self.app.school.personal_pdf_extractor = blocked_extractor
        self.login()
        code, plan, _ = self.request("POST", "/api/school/plan", {})
        self.assertEqual(code, 200, plan)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.request, "POST", "/api/evidence/prepare", {"source_alias": plan["source_alias"]})
            try:
                self.assertTrue(started.wait(2))
                logout = pool.submit(self.request, "POST", "/api/auth/logout", {})
                self.assertEqual(logout.result(timeout=2)[0], 200)
            finally:
                release.set()
            self.assertIn(pending.result(timeout=3)[0], (401, 409))
        self.assertEqual(self.request("GET", "/api/chat/status")[0], 401)


if __name__ == "__main__":
    unittest.main()
