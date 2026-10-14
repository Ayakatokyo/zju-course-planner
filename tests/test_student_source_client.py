"""Invented transports test authorization, provenance, bounded reads and cache isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
import unittest
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

from course_assistant.student_source_client import SourceClientError, StudentSourceClient, to_envelopes
from course_assistant.student_sources import business_field_diagnostics
from course_assistant.school_period_times import period_source


def business(rows=None, *, total=1, epoch=1, subject="SYNTHETIC-STUDENT-A", page=None):
    payload = {"items": rows if rows is not None else [{"kcmc": "SYNTHETIC-COURSE-A", "xf": "2"}], "total": total}
    if page is not None:
        payload["pageNo"] = page
    return {"state": "business", "http_status": 200, "content_type": "application/json; charset=utf-8",
            "session_epoch": epoch, "source_subject_id": subject, "payload": payload}


class ScriptTransport:
    def __init__(self, script):
        self.session_epoch = 1
        self.script, self.requests = list(script), []
        self.running, self.max_running = 0, 0

    def request(self, spec):
        self.requests.append(deepcopy(spec))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item(self, spec) if callable(item) else deepcopy(item)
        finally:
            self.running -= 1


class StudentSourceClientTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.scope = "SYNTHETIC-SCOPE-A"
        self.denied = False
        self.auth_calls = 0

    def authorize(self):
        self.auth_calls += 1
        if self.denied:
            raise PermissionError("SYNTHETIC-AUTH-DENIAL")
        return {"scope_key": self.scope}

    def client(self, script, **kwargs):
        self.transport = ScriptTransport(script)
        return StudentSourceClient(self.transport, self.authorize, clock=lambda: self.now, **kwargs)

    def fetch(self, client, **kwargs):
        return client.fetch("academic", "SYNTHETIC-TERM", **kwargs)

    def test_field_diagnostics_reject_unbounded_keys_values_and_wrong_row_counts(self):
        source = business([{"kcmc": "合成课", "sftg": None}])
        diagnostics = business_field_diagnostics("academic", source["payload"], source["payload"])
        invalid = []
        other_key = deepcopy(diagnostics)
        other_key["source"]["fields"]["SYNTHETIC-PRIVATE-KEY"] = {"present": 1, "empty": 0, "types": {"string": 1}}
        invalid.append(other_key)
        raw_value = deepcopy(diagnostics)
        raw_value["source"]["fields"]["sftg"]["value"] = "SYNTHETIC-PRIVATE-VALUE"
        invalid.append(raw_value)
        wrong_rows = deepcopy(diagnostics)
        wrong_rows["source"]["row_count"] = wrong_rows["projection"]["row_count"] = 2
        invalid.append(wrong_rows)
        for malformed in invalid:
            with self.subTest(malformed_keys=sorted(malformed)):
                client = self.client([{**source, "source_field_diagnostics": malformed}])
                result = self.fetch(client)
                self.assertEqual(result["status"], "schema_changed")
                self.assertEqual(result["failure"], "invalid_field_diagnostics")
                self.assertEqual(result["pages"], [])

    def test_cache_is_historical_detached_and_authorized_again(self):
        client = self.client([business()])
        live = self.fetch(client)
        self.assertEqual((live["status"], live["data_source"]), ("complete_within_scope", "live"))
        live["pages"][0]["payload"]["items"][0]["kcmc"] = "MUTATED"
        calls = self.auth_calls
        self.now += 10
        cached = self.fetch(client)
        self.assertEqual(cached["data_source"], "historical_cache")
        self.assertEqual(cached["captured_at"], 1000)
        self.assertEqual(cached["cache_captured_at"], 1000)
        self.assertGreater(self.auth_calls, calls)
        self.assertNotEqual(cached["pages"][0]["payload"]["items"][0]["kcmc"], "MUTATED")
        self.assertEqual(len(self.transport.requests), 1)

    def test_only_transient_failure_retries_once_and_labels_old_cache(self):
        client = self.client([business(), OSError("SYNTHETIC-SECRET"), OSError("SYNTHETIC-SECRET")])
        self.fetch(client)
        self.now += 10
        failed = self.fetch(client, force_refresh=True)
        self.assertEqual((failed["status"], failed["data_source"], failed["request_attempts"]),
                         ("source_unavailable", "historical_cache", 2))
        self.assertEqual(failed["captured_at"], 1000)
        self.assertNotIn("SYNTHETIC-SECRET", str(failed))

    def test_success_after_one_transient_failure_is_current(self):
        client = self.client([OSError("SYNTHETIC-SECRET"), business()])
        result = self.fetch(client)
        self.assertEqual((result["data_source"], result["request_attempts"]), ("live", 2))

    def test_unexpected_transport_exception_does_not_return_cached_body(self):
        client = self.client([business(), ValueError("SYNTHETIC-SECRET")])
        self.fetch(client)
        result = self.fetch(client, force_refresh=True)
        self.assertEqual((result["data_source"], result["pages"], result["request_attempts"]), ("none", [], 1))
        self.assertNotIn("SYNTHETIC-SECRET", str(result))

    def test_all_challenges_and_permissions_block_cache_and_do_not_retry(self):
        for state in ("awaiting_user_login", "source_session_expired", "captcha_required", "permission_denied",
                      "source_subject_changed", "user_confirmation_required"):
            with self.subTest(state=state):
                client = self.client([business(), {"state": state, "http_status": None,
                                                   "payload": "SYNTHETIC-SECRET-LOGIN"}, business()])
                self.fetch(client)
                result = self.fetch(client, force_refresh=True)
                self.assertEqual((result["status"], result["data_source"], result["pages"]), (state, "none", []))
                self.assertNotIn("SYNTHETIC-SECRET-LOGIN", str(result))
                self.assertEqual(len(self.transport.requests), 2)
                self.assertEqual(self.fetch(client)["data_source"], "live")

    def test_http_401_or_403_cannot_be_labeled_business(self):
        for code, state in ((401, "source_session_expired"), (403, "permission_denied")):
            with self.subTest(code=code):
                invalid = business()
                invalid["http_status"] = code
                client = self.client([business(), invalid])
                self.fetch(client)
                result = self.fetch(client, force_refresh=True)
                self.assertEqual((result["status"], result["pages"]), (state, []))

    def test_authorization_failure_on_cache_hit_propagates(self):
        client = self.client([business()])
        self.fetch(client)
        self.denied = True
        with self.assertRaises(PermissionError):
            self.fetch(client)
        self.assertEqual(len(self.transport.requests), 1)

    def test_revocation_during_request_prevents_any_publication(self):
        def revoke(transport, spec):
            self.denied = True
            return business()
        client = self.client([revoke])
        with self.assertRaises(PermissionError):
            self.fetch(client)
        self.assertFalse(client._cache)

    def test_scope_change_during_request_rejects_result(self):
        def switch(transport, spec):
            self.scope = "SYNTHETIC-SCOPE-B"
            return business()
        client = self.client([switch])
        with self.assertRaises(SourceClientError) as error:
            self.fetch(client)
        self.assertEqual(error.exception.code, "authorization_scope_changed")
        self.assertFalse(client._cache)

    def test_cache_key_covers_scope_term_operation_and_epoch(self):
        client = self.client([business(), business(), business(), business(), business(epoch=2)])
        self.fetch(client)
        self.scope = "SYNTHETIC-SCOPE-B"
        self.fetch(client)
        client.fetch("academic", "SYNTHETIC-OTHER-TERM")
        client.fetch("major_academic", "SYNTHETIC-OTHER-TERM")
        self.transport.session_epoch = 2
        client.fetch("major_academic", "SYNTHETIC-OTHER-TERM")
        self.assertEqual(len(self.transport.requests), 5)

    def test_epoch_change_in_flight_discards_old_response_and_cache(self):
        def switch(transport, spec):
            transport.session_epoch = 2
            return business()
        client = self.client([business(), switch])
        self.fetch(client)
        result = self.fetch(client, force_refresh=True)
        self.assertEqual((result["status"], result["pages"], result["data_source"]),
                         ("source_session_expired", [], "none"))

    def test_explicit_subject_changes_or_mixed_payloads_block_cached_body(self):
        for payload in (business(subject="SYNTHETIC-STUDENT-B"),
                        business([{ "student_number": "SYNTHETIC-STUDENT-B", "kcmc": "SYNTHETIC-COURSE"}])):
            with self.subTest(payload=payload):
                client = self.client([business(), payload])
                self.fetch(client)
                result = self.fetch(client, force_refresh=True)
                self.assertEqual((result["status"], result["pages"]), ("source_subject_changed", []))

    def test_unknown_subject_stays_unknown_and_never_creates_authentication(self):
        client = self.client([business(subject=None)])
        result = self.fetch(client)
        self.assertIsNone(result["pages"][0]["response"]["source_subject_id"])
        self.assertEqual(result["official_authentication"], "not_configured")
        self.assertNotIn("AuthContext", result)

    def test_boolean_epoch_is_not_equal_to_integer_connection_epoch(self):
        response = business()
        response["session_epoch"] = True
        client = self.client([response])
        result = self.fetch(client)
        self.assertEqual((result["status"], result["pages"]), ("source_session_expired", []))

    def test_pagination_uses_bounded_requests_and_keeps_short_page_total_evidence(self):
        client = self.client([business([{ "kcmc": f"SYNTHETIC-COURSE-{page}"}], total=3, page=page)
                              for page in range(1, 4)])
        result = self.fetch(client)
        self.assertEqual((result["status"], result["coverage"]["captured_rows"]), ("complete_within_scope", 3))
        self.assertEqual([spec["page"] for spec in self.transport.requests], [1, 2, 3])
        self.assertTrue(all(spec["page_size"] == 20 for spec in self.transport.requests))
        bounded = self.client([business([{ "kcmc": f"SYNTHETIC-COURSE-{page}"}], total=4, page=page)
                               for page in range(1, 4)])
        limited = self.fetch(bounded)
        self.assertEqual((limited["status"], len(limited["pages"])), ("partial_acquisition", 3))

    def test_repeated_page_or_changed_total_or_wrong_page_never_claims_complete(self):
        first = business(total=2, page=1)
        for second in (business(total=2, page=2), business([{ "kcmc": "SYNTHETIC-OTHER"}], total=3, page=2),
                       business([{ "kcmc": "SYNTHETIC-OTHER"}], total=2, page=1)):
            with self.subTest(second=second):
                client = self.client([first, second])
                result = self.fetch(client)
                self.assertEqual(result["status"], "partial_acquisition")
                self.assertFalse(result["coverage"]["complete_within_scope"])
                self.assertIsNotNone(result["failure"])
                self.assertEqual(len(result["pages"]), 1)

    def test_missing_total_and_malformed_rows_do_not_become_complete(self):
        response = business(total=1)
        del response["payload"]["total"]
        client = self.client([response])
        self.assertEqual(self.fetch(client)["status"], "partial_acquisition")
        client = self.client([business([{ "kcmc": "SYNTHETIC-COURSE", "xf": "invalid"}])])
        result = self.fetch(client)
        self.assertEqual((result["status"], result["coverage"]["captured_rows"]), ("partial_acquisition", 1))

    def test_unverified_http_wrong_type_headers_and_html_are_not_data(self):
        for change in ({"http_status": None}, {"http_status": True}, {"content_type": "text/html"},
                       {"payload": "<html>SYNTHETIC-SECRET</html>"}, {"headers": {"Cookie": "SYNTHETIC-SECRET"}}):
            with self.subTest(change=change):
                client = self.client([{**business(), **change}])
                result = self.fetch(client)
                self.assertEqual((result["status"], result["pages"]), ("schema_changed", []))
                self.assertNotIn("SYNTHETIC-SECRET", str(result))

    def test_credential_fields_are_stripped_and_credential_values_rejected(self):
        client = self.client([business([{ "kcmc": "SYNTHETIC-COURSE", "Cookie": "SYNTHETIC-SECRET"}])])
        self.assertNotIn("SYNTHETIC-SECRET", str(self.fetch(client)))
        client = self.client([business([{ "kcmc": "Cookie: sid=SYNTHETIC-SECRET"}])])
        self.assertEqual(self.fetch(client)["pages"], [])

    def test_concurrent_queries_serialize_and_share_existing_snapshot(self):
        client = self.client([business()])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.fetch(client), range(4)))
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.max_running, 1)
        self.assertEqual(sum(result["data_source"] == "live" for result in results), 1)

    def test_timeout_returns_promptly_blocks_overlapping_requests_and_ignores_late_body(self):
        release, entered = threading.Event(), threading.Event()
        def wait(transport, spec):
            entered.set()
            release.wait(2)
            return business()
        client = self.client([wait], timeout=0.02)
        start = time.monotonic()
        try:
            first = self.fetch(client)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(entered.is_set())
            second = self.fetch(client)
            self.assertEqual((first["failure"], second["failure"]), ("request_timeout", "transport_still_running"))
            self.assertEqual(len(self.transport.requests), 1)
            self.assertFalse(client._cache)
        finally:
            release.set()
            client._pending.wait(1)
        self.assertFalse(client._cache)

    def test_url_student_number_unsupported_course_refresh_and_invalid_scope_never_dispatch(self):
        client = self.client([])
        for call in (lambda: client.fetch("courses", "SYNTHETIC-TERM"),
                     lambda: client.fetch("academic", "https://invalid.example"),
                     lambda: client.fetch("plan_catalogue", scope={"student_number": "SYNTHETIC-OTHER"}),
                     lambda: client.fetch("plan_document", plan_ref="https://invalid.example/file.pdf")):
            with self.assertRaises(SourceClientError):
                call()
        self.assertEqual(self.transport.requests, [])

    def test_timeout_does_not_leave_reusable_cache_before_late_challenge(self):
        release = threading.Event()
        def late_challenge(transport, spec):
            release.wait(2)
            return {"state": "captcha_required", "session_epoch": 1, "http_status": None}
        client = self.client([business(), late_challenge], timeout=0.02)
        self.fetch(client)
        try:
            timed_out = self.fetch(client, force_refresh=True)
            self.assertEqual(timed_out["data_source"], "historical_cache")
            self.assertFalse(client._cache)
            self.assertEqual(self.fetch(client)["pages"], [])
        finally:
            release.set()
            client._pending.wait(1)

    def test_authorization_can_narrow_operations_and_term(self):
        transport = ScriptTransport([])
        client = StudentSourceClient(transport, lambda: {"scope_key": "SYNTHETIC-SCOPE", "operations": ["academic"], "target_term": "SYNTHETIC-TERM"})
        for operation, term in (("exams", "SYNTHETIC-TERM"), ("academic", "SYNTHETIC-OTHER")):
            with self.assertRaises(SourceClientError):
                client.fetch(operation, term)
        self.assertFalse(transport.requests)

    def test_plan_scope_is_not_mistaken_for_personal_term_authorization(self):
        transport = ScriptTransport([business([{ "plan_ref": "SYNTHETIC-PLAN"}])])
        client = StudentSourceClient(transport, lambda: {"scope_key": "SYNTHETIC-SCOPE", "target_term": "SYNTHETIC-TERM", "operations": ["plan_catalogue"]})
        self.assertEqual(client.fetch("plan_catalogue", scope={"cohort": 2030})["data_source"], "live")

    def test_catalogue_dom_guard_is_exact_and_only_catalogue_can_use_unknown_http(self):
        response = {**business([{ "plan_ref": "SYNTHETIC-PLAN", "major_name": "SYNTHETIC-MAJOR", "url": "SYNTHETIC-PRIVATE-LOCATION"}]),
                    "capture_method": "browser_dom", "http_status": None, "content_type": "dom_business_projection",
                    "page_validation": {"status": "visible_business_page", "domain": "plan_catalogue"}}
        client = self.client([response])
        result = client.fetch("plan_catalogue", scope={"cohort": 2030, "major_name": "SYNTHETIC-MAJOR"})
        self.assertEqual(result["data_source"], "live")
        self.assertIsNone(result["pages"][0]["response"]["http_status"])
        self.assertNotIn("SYNTHETIC-PRIVATE-LOCATION", str(result))
        for changed in ({"page_validation": {"status": "visible_business_page", "domain": "academic"}},
                        {"http_status": 200}, {"capture_method": "native_ax"}):
            client = self.client([{**response, **changed}])
            self.assertEqual(client.fetch("plan_catalogue", scope={"cohort": 2030})["pages"], [])
        client = self.client([response])
        self.assertEqual(self.fetch(client)["pages"], [])

    def test_catalogue_null_total_is_unknown_and_title_is_plain_business_text(self):
        response = business([{ "plan_ref": "SYNTHETIC-PLAN", "title": "SYNTHETIC-PLAN-TITLE"}], total=None)
        client = self.client([response])
        result = client.fetch("plan_catalogue", scope={"cohort": 2030})
        self.assertEqual((result["status"], result["data_source"], result["failure"]), ("partial_acquisition", "live", None))
        self.assertIsNone(result["coverage"]["reported_total"])
        self.assertEqual(result["pages"][0]["payload"]["items"][0]["title"], "SYNTHETIC-PLAN-TITLE")
        for title in ("<script>SYNTHETIC-SECRET</script>", {"title": "SYNTHETIC-TITLE"}, "Cookie: sid=SYNTHETIC-SECRET"):
            client = self.client([business([{ "plan_ref": "SYNTHETIC-PLAN", "title": title}])])
            self.assertEqual(client.fetch("plan_catalogue", scope={"cohort": 2030})["pages"], [])

    def test_candidate_cohort_provenance_is_preserved_but_not_accepted_as_query_scope(self):
        row = {"plan_ref": "SYNTHETIC-PLAN", "cohort": 2030, "cohort_provenance": "confirmed_query_scope"}
        client = self.client([business([row])])
        result = client.fetch("plan_catalogue", scope={"cohort": 2030})
        self.assertEqual(result["pages"][0]["payload"]["items"][0]["cohort_provenance"], "confirmed_query_scope")
        self.assertEqual(result["official_authentication"], "not_configured")
        with self.assertRaises(SourceClientError):
            client.fetch("plan_catalogue", scope={"cohort": 2030, "cohort_provenance": "confirmed_query_scope"})
        for value in (None, True, {"state": "known", "value": "confirmed_query_scope"}, "SYNTHETIC-PRIVATE-VALUE"):
            client = self.client([business([{**row, "cohort_provenance": value}])])
            self.assertEqual(client.fetch("plan_catalogue", scope={"cohort": 2030})["pages"], [])

    def test_plan_document_is_single_step_pdf_and_no_arbitrary_url_is_emitted(self):
        pdf = b"%PDF-1.4\nSYNTHETIC-DOCUMENT\n%%EOF"
        response = {"state": "business", "http_status": 200, "payload": pdf,
                    "content_type": "application/pdf", "session_epoch": 1, "source_subject_id": None}
        client = self.client([response])
        result = client.fetch("plan_document", plan_ref="SYNTHETIC-PLAN")
        self.assertEqual(result["pages"][0]["payload"], pdf)
        self.assertEqual(result["status"], "complete_within_scope")
        self.assertEqual(self.transport.requests, [{"operation": "plan_document", "page": 1, "page_size": 20, "plan_ref": "SYNTHETIC-PLAN"}])
        client = self.client([{**response, "payload": b"<html>SYNTHETIC-LOGIN</html>"}])
        self.assertEqual(client.fetch("plan_document", plan_ref="SYNTHETIC-PLAN")["pages"], [])

    def test_catalogue_cannot_hide_switched_subject_or_url_in_reference(self):
        client = self.client([business(), business([{ "plan_ref": "SYNTHETIC-PLAN", "student_number": "SYNTHETIC-STUDENT-B"}])])
        self.fetch(client)
        result = client.fetch("plan_catalogue", scope={"cohort": 2030})
        self.assertEqual((result["status"], result["pages"]), ("source_subject_changed", []))
        client = self.client([business([{ "plan_ref": "https://invalid.example/private.pdf"}])])
        self.assertEqual(client.fetch("plan_catalogue", scope={"cohort": 2030})["pages"], [])

    def test_live_envelopes_keep_actual_http_metadata_and_reject_historical_import(self):
        client = self.client([business()])
        live = self.fetch(client)
        options = {"operation": "academic", "target_term": "SYNTHETIC-TERM", "source_alias": "SOURCE-SYNTHETIC",
                   "source_origin": "https://synthetic.example.invalid", "generation": 1}
        envelopes = to_envelopes(live, **options)
        self.assertEqual(envelopes[0]["response"], {"http_status": 200, "content_type": "application/json", "response_state": "business"})
        self.assertNotIn("capture_method", envelopes[0]["response"])
        self.assertEqual(envelopes[0]["payload"]["source_subject_id"], "SYNTHETIC-STUDENT-A")
        with self.assertRaises(SourceClientError):
            to_envelopes(self.fetch(client), **options)

    def test_transport_subject_anchor_survives_store_restart_and_blocks_other_account(self):
        from course_assistant.student_sources import SourceTaskStore, StudentSourceError, REALM, PURPOSE, DOMAINS
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            grant = root / "grant.json"
            grant.write_text(json.dumps({"security_realm": REALM, "purpose": PURPOSE, "subject_kind": "local_review",
                "auth_mode": "local_operation", "owner_student_id": None, "data_kind": "synthetic", "domains": list(DOMAINS),
                "grant_id": "GRANT-SYNTHETIC", "operator_ref": "OP-SYNTHETIC", "revoked": False, "expires_at": 2000}))
            grant.chmod(0o600)
            store = SourceTaskStore(root / "store", grant, clock=lambda: self.now)
            task = store.create_attempt("SYNTHETIC-TERM", "https://synthetic.example.invalid")
            options = {"target_term": "SYNTHETIC-TERM", "source_alias": "SOURCE-SYNTHETIC", "source_origin": "https://synthetic.example.invalid", "generation": task["generation"]}
            first = self.fetch(self.client([business()]))
            store.ingest(task["attempt_id"], to_envelopes(first, operation="academic", **options)[0])
            restarted = SourceTaskStore(root / "store", grant, clock=lambda: self.now)
            second = self.client([business(subject="SYNTHETIC-STUDENT-B")]).fetch("exams", "SYNTHETIC-TERM")
            with self.assertRaises(StudentSourceError) as error:
                restarted.ingest(task["attempt_id"], to_envelopes(second, operation="exams", **options)[0])
            self.assertEqual(error.exception.code, "source_subject_changed")
            self.assertEqual(restarted.describe(task["attempt_id"])["official_authentication"], "not_configured")

    def test_browser_http_capture_marker_is_preserved_and_unknown_marker_rejected(self):
        client = self.client([{**business(), "capture_method": "browser_http"}])
        result = self.fetch(client)
        self.assertEqual(result["status"], "complete_within_scope")
        self.assertEqual(result["pages"][0]["response"]["capture_method"], "browser_http")
        for marker in ("browser_dom", "native_ax", "unrecognized"):
            client = self.client([{**business(), "capture_method": marker}])
            self.assertEqual(self.fetch(client)["pages"], [])

    def test_unpaged_timetable_does_not_request_a_duplicate_second_page(self):
        rows = [{"kcmc": "SYNTHETIC-COURSE-" + str(index)} for index in range(20)]
        response = {"state": "business", "http_status": 200, "content_type": "application/json",
                    "session_epoch": 1, "payload": {"kbList": rows}}
        client = self.client([response])
        result = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(result["coverage"]["captured_rows"], 20)
        self.assertIsNone(result["coverage"]["reported_total"])
        self.assertEqual(result["status"], "partial_acquisition")

    def test_period_clock_metadata_is_cached_detached_and_never_crosses_grant_or_epoch(self):
        clock = [{"period": 1, "start_time": "07:10", "end_time": "07:55"}]
        source = {"state": "business", "http_status": 200, "content_type": "application/json", "session_epoch": 1,
                  "payload": {"kbList": [{"kcmc": "SYNTHETIC-CLOCK-COURSE"}], "total": 1, "period_times": clock,
                              "period_times_source": period_source("known")}}
        other = deepcopy(source)
        other["payload"]["period_times"][0]["start_time"] = "07:20"
        client = self.client([source, other, {**deepcopy(other), "session_epoch": 2}])
        live = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual(live["pages"][0]["payload"]["period_times"], clock)
        live["pages"][0]["payload"]["period_times"][0]["start_time"] = "00:00"
        cached = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual(cached["data_source"], "historical_cache")
        self.assertEqual(cached["pages"][0]["payload"]["period_times"], clock)
        self.scope = "SYNTHETIC-DIFFERENT-SCOPE"
        different = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual(different["data_source"], "live")
        self.assertEqual(different["pages"][0]["payload"]["period_times"][0]["start_time"], "07:20")
        self.transport.session_epoch = 2
        self.assertEqual(client.fetch("timetable", "2026-2027-AW")["data_source"], "live")
        self.assertEqual(len(self.transport.requests), 3)
        self.denied = True
        with self.assertRaises(PermissionError):
            client.fetch("timetable", "2026-2027-AW")
        self.assertEqual(len(self.transport.requests), 3)

    def test_period_clock_metadata_cannot_bypass_row_bounds_or_fixed_projection(self):
        payload = {"kbList": [{"kcmc": "SYNTHETIC-CLOCK-COURSE"}] * 21, "period_times": [],
                   "period_times_source": period_source()}
        response = {"state": "business", "http_status": 200, "content_type": "application/json", "session_epoch": 1, "payload": payload}
        client = self.client([response])
        result = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual((result["status"], result["failure"], result["pages"]), ("schema_changed", "page_size_exceeded", []))
        payload["kbList"] = payload["kbList"][:1]
        payload["period_times_source"]["page_path"] = "https://foreign.invalid/"
        client = self.client([response])
        result = client.fetch("timetable", "2026-2027-AW")
        self.assertEqual((result["status"], result["failure"], result["pages"]), ("schema_changed", "unsafe_business_projection", []))

    def test_real_loopback_bridge_transport_client_roundtrip_only_stripped_synthetic_data(self):
        from course_assistant.student_browser_bridge import BrowserBridgeTransport, BrowserBridgeServer
        transport = BrowserBridgeTransport(self.authorize, timeout=1)
        server = BrowserBridgeServer(transport).start()
        self.addCleanup(server.close)
        origin, epoch = "chrome-extension://" + "a" * 32, "synthetic_epoch_0001"
        def post(path, payload, token=None):
            headers = {"Content-Type": "application/json", "Origin": origin}
            if token:
                headers["Authorization"] = "Bearer " + token
            request = Request(server.url + path, data=json.dumps(payload).encode(), headers=headers)
            with urlopen(request, timeout=2) as response:
                return json.loads(response.read())
        token = post("/pair", {"code": transport._pair_code, "session_epoch": epoch})["token"]
        client = StudentSourceClient(transport, self.authorize, timeout=2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result_future = pool.submit(client.fetch, "academic", "2026-2027-AW")
            job = None
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                job = post("/next", {"session_epoch": epoch}, token)["job"]
                if job:
                    break
                time.sleep(0.005)
            self.assertIsNotNone(job)
            response = business([{ "kcmc": "SYNTHETIC-COURSE", "password": "SYNTHETIC-SECRET"}], epoch=epoch)
            response["content_type"] = "application/json"
            post("/result", {"id": job["id"], "response": response}, token)
            result = result_future.result(timeout=2)
        self.assertEqual(result["status"], "complete_within_scope")
        self.assertEqual(result["pages"][0]["response"]["capture_method"], "browser_http")
        self.assertNotIn("SYNTHETIC-SECRET", str(result))
        self.assertNotIn(token, str(result))


if __name__ == "__main__":
    unittest.main()
