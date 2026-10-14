"""Task isolation and conservative publication tests with invented source data."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.student_sources import (
    DOMAINS, PURPOSE, REALM, SCHEMA, SourceTaskStore, StudentSourceError,
    match_plan, response_state, sanitize_payload,
)


class StudentSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 100.0
        self.grant = {"grant_id": "GRANT-test", "operator_ref": "REVIEW-test", "security_realm": REALM,
                      "purpose": PURPOSE, "subject_kind": "local_review", "auth_mode": "local_operation",
                      "owner_student_id": None, "data_kind": "synthetic", "domains": list(DOMAINS),
                      "revoked": False, "expires_at": 1000}
        self.grant_path = self.root / "grant.json"
        self.write_grant()
        self.store = SourceTaskStore(self.root / "task", self.grant_path, clock=lambda: self.now)
        self.attempt = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid", "SOURCE-one")

    def write_grant(self):
        self.grant_path.write_text(json.dumps(self.grant), encoding="utf-8")
        os.chmod(self.grant_path, 0o600)

    def envelope(self, domain="academic", payload=None):
        return {"schema": SCHEMA, "domain": domain, "source_alias": "SOURCE-academic",
                "source_origin": "https://synthetic.example.invalid", "generation": self.attempt["generation"],
                "source_subject_ref": "SOURCE-one", "scope": {"target_term": "TEST-TERM", "query_kind": "visible_single_term"},
                "coverage": {"page_index": 1, "total_rows": 1, "has_more": False, "complete_within_scope": True},
                "response": {"http_status": 200, "content_type": "application/json", "response_state": "business"},
                "payload": payload if payload is not None else {"items": [{"xkkh": "TEST-PE-1", "kcmc": "合成体育", "xf": "1.5", "cj": "90", "jd": "4.5"}]}}

    def ingest(self, envelope=None):
        return self.store.ingest(self.attempt["attempt_id"], envelope or self.envelope())

    def assert_code(self, code, function, *args):
        with self.assertRaises(StudentSourceError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def test_transport_classifier_does_not_treat_200_html_as_business(self):
        for metadata, expected in [
            ({"http_status": 200, "content_type": "text/html", "response_state": "login_html"}, "awaiting_user_login"),
            ({"http_status": 302, "response_state": "redirect_login"}, "awaiting_user_login"),
            ({"http_status": 401}, "source_session_expired"),
            ({"http_status": 403}, "permission_denied"),
            ({"http_status": 200, "content_type": "application/json", "response_state": "empty"}, "schema_changed"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(response_state(metadata), expected)

    def test_native_visible_business_projection_keeps_http_and_identity_unknown(self):
        self.attempt = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid")
        env = self.envelope(domain="profile", payload={"学院": "合成学院"})
        env["source_subject_ref"] = None
        env["response"] = {
            "capture_method": "native_ax", "http_status": None,
            "content_type": "dom_business_projection", "response_state": "business",
            "page_validation": {"status": "visible_business_page", "domain": "profile"},
        }
        self.assertEqual(self.ingest(env)["rows"], 1)
        snapshot = self.store.private_snapshot(self.attempt["attempt_id"], "profile")
        self.assertEqual(snapshot["envelope"]["response"], env["response"])
        readiness = self.store.readiness(self.attempt["attempt_id"])
        self.assertEqual(readiness["source_identity"], "unknown")
        self.assertEqual(readiness["official_authentication"], "not_configured")
        self.assertEqual(readiness["student_plan_binding"], "not_verified")
        self.assertEqual(readiness["recommendation_input"], "input_not_ready_for_recommendation")
        self.assertNotIn("owner_student_id", readiness)

    def test_native_capture_requires_exact_domain_guard_without_relaxing_http(self):
        native = {
            "capture_method": "native_ax", "http_status": None,
            "content_type": "dom_business_projection", "response_state": "business",
            "page_validation": {"status": "visible_business_page", "domain": "academic"},
        }
        cases = []
        for key in native:
            metadata = deepcopy(native)
            metadata.pop(key)
            cases.append(("missing_" + key, metadata))
        for key, value in (("capture_method", "browser_dom"), ("http_status", 200),
                           ("content_type", "application/json"), ("response_state", "empty"),
                           ("page_validation", None), ("page_validation", []),
                           ("page_validation", {"status": "visible_business_page", "domain": "profile"}),
                           ("page_validation", {"status": "unverified", "domain": "academic"}),
                           ("page_validation", {"status": "visible_business_page", "domain": "academic", "raw": "TEST-SECRET"}),
                           ("headers", {"Cookie": "TEST-SECRET"})):
            metadata = deepcopy(native)
            metadata[key] = value
            cases.append(("invalid_" + key, metadata))
        for label, metadata in cases:
            with self.subTest(label=label):
                env = self.envelope()
                env["response"] = metadata
                self.assert_code("invalid_response_metadata", self.ingest, env)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["observations"], 0)
        self.assertNotIn(b"TEST-SECRET", self.store.path.read_bytes())
        self.assert_code("invalid_response_metadata", response_state, native)
        self.assertEqual(response_state({"http_status": None, "content_type": "application/json", "response_state": "business"}), "source_unavailable")
        self.assertEqual(response_state({"http_status": 200, "content_type": "application/json", "response_state": "business"}), "business")

    def test_native_capture_cannot_override_login_challenge_or_failure_states(self):
        for state, status, expected in (
                ("login_html", None, "awaiting_user_login"),
                ("redirect_login", None, "awaiting_user_login"),
                ("captcha", None, "captcha_required"),
                ("auth_expired", None, "source_session_expired"),
                ("permission_denied", None, "permission_denied"),
                ("business", 401, "source_session_expired"),
                ("business", 403, "permission_denied"),
                ("transport_failure", None, "source_unavailable")):
            with self.subTest(state=state, status=status):
                self.attempt = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid", "SOURCE-one")
                env = self.envelope(payload="<html>TEST-SECRET</html>")
                env["response"] = {
                    "capture_method": "native_ax", "http_status": status,
                    "content_type": "dom_business_projection", "response_state": state,
                }
                self.assertEqual(self.ingest(env)["status"], expected)
                self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["observations"], 0)
                self.assertNotIn(b"TEST-SECRET", self.store.path.read_bytes())

    def test_login_body_is_never_persisted_and_task_stops(self):
        env = self.envelope()
        env["response"] = {"http_status": 200, "content_type": "text/html", "response_state": "login_html"}
        env["payload"] = "<html>TEST-SECRET</html>"
        self.assertEqual(self.ingest(env)["status"], "awaiting_user_login")
        self.assert_code("awaiting_user_login", self.ingest)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["status"], "awaiting_user_login")
        self.assertNotIn(b"TEST-SECRET", self.store.path.read_bytes())

    def test_credential_projection_is_immutable_and_raw_bytes_are_absent(self):
        env = self.envelope()
        env["payload"]["items"][0]["password"] = "TEST-SECRET"
        env["payload"]["items"][0]["nested_auth"] = {"cookie": "TEST-SECRET"}
        result = self.ingest(env)
        self.assertEqual(result["stripped_field_count"], 2)
        snapshot = self.store.private_snapshot(self.attempt["attempt_id"], "academic")
        self.assertEqual(snapshot["envelope"]["digest_basis"], "credential_stripped_business_projection")
        self.assertNotIn(b"TEST-SECRET", self.store.path.read_bytes())
        self.assertEqual(self.ingest(env)["observation_id"], result["observation_id"])
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["observations"], 1)

    def test_tickets_in_business_values_are_rejected_without_echo(self):
        self.assert_code("unsafe_business_value", sanitize_payload, "academic", {"items": [{"kcmc": "https://synthetic.example.invalid/?ticket=TEST-SECRET"}]})

    def test_response_metadata_cannot_carry_headers(self):
        env = self.envelope()
        env["response"]["headers"] = {"Cookie": "TEST-SECRET"}
        self.assert_code("invalid_response_metadata", self.ingest, env)
        self.assertNotIn(b"TEST-SECRET", self.store.path.read_bytes())

    def test_school_or_mock_owner_cannot_grant_access(self):
        for field, value in (("owner_student_id", "TEST-STUDENT"), ("auth_mode", "mock"), ("security_realm", "restricted_reference")):
            with self.subTest(field=field):
                saved = deepcopy(self.grant)
                self.grant[field] = value
                self.write_grant()
                self.assert_code("grant_denied", self.ingest)
                self.grant = saved
                self.write_grant()

    def test_external_grant_changes_invalidate_existing_task(self):
        self.grant["operator_ref"] = "REVIEW-other"
        self.write_grant()
        self.assert_code("stale_grant", self.ingest)

    def test_revocation_and_expiry_are_checked_on_every_read(self):
        self.ingest()
        self.now = 1000
        self.assert_code("grant_expired", self.store.private_snapshot, self.attempt["attempt_id"], "academic")
        self.now = 100
        self.store.revoke()
        self.assert_code("grant_revoked", self.store.private_snapshot, self.attempt["attempt_id"], "academic")

    def test_account_switch_stops_task_and_does_not_mix_observations(self):
        self.ingest()
        env = self.envelope()
        env["source_subject_ref"] = "SOURCE-other"
        self.assert_code("source_subject_changed", self.ingest, env)
        self.assert_code("source_subject_changed", self.store.private_snapshot, self.attempt["attempt_id"], "academic")
        desc = self.store.describe(self.attempt["attempt_id"])
        self.assertEqual(desc["domains"]["academic"]["observations"], 1)

    def test_new_generation_invalidates_old_references(self):
        self.ingest()
        self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid", "SOURCE-one")
        self.assert_code("source_generation_changed", self.store.private_snapshot, self.attempt["attempt_id"], "academic")
        self.assertEqual(self.store.readiness(self.attempt["attempt_id"])["profile"], "unknown")

    def test_unknown_source_subject_does_not_become_authenticated(self):
        unknown = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid")
        desc = self.store.readiness(unknown["attempt_id"])
        self.assertEqual(desc["source_identity"], "unknown")
        self.assertEqual(desc["official_authentication"], "not_configured")
        self.assertEqual(desc["recommendation_input"], "input_not_ready_for_recommendation")

    def test_subject_unknown_still_allows_independent_business_observation(self):
        self.attempt = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid")
        env = self.envelope()
        env["source_subject_ref"] = None
        self.assertEqual(self.ingest(env)["rows"], 1)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["source_identity"], "unknown")

    def test_pagination_three_page_budget_and_coverage_reconciliation(self):
        env = self.envelope()
        env["coverage"].update(total_rows=4, has_more=True, complete_within_scope=False)
        self.assertEqual(self.ingest(env)["status"], "partial_acquisition")
        for page in (2, 3):
            env["coverage"]["page_index"] = page
            env["payload"]["items"][0]["xkkh"] = f"TEST-PE-{page}"
            self.assertEqual(self.ingest(env)["rows"], page)
        env["coverage"]["page_index"] = 4
        self.assert_code("page_budget_exceeded", self.ingest, env)
        snapshot = self.store.private_snapshot(self.attempt["attempt_id"], "academic")
        self.assertEqual(snapshot["snapshot"]["coverage"]["captured_rows"], 3)

    def test_same_query_continuation_can_be_complete_only_with_total_evidence(self):
        env = self.envelope()
        env["coverage"].update(total_rows=2, has_more=True, complete_within_scope=False)
        self.ingest(env)
        env["coverage"].update(page_index=2, has_more=False, complete_within_scope=True)
        env["payload"]["items"][0]["xkkh"] = "TEST-PE-2"
        result = self.ingest(env)
        self.assertEqual((result["rows"], result["status"]), (2, "complete_within_scope"))
        self.assertIsNone(self.store.private_snapshot(self.attempt["attempt_id"], "academic")["snapshot"]["records"][0]["earned_credits"])

    def test_changed_term_origin_or_query_cannot_join_snapshot(self):
        for field, replacement, expected in (("source_origin", "https://invalid.example", "source_origin_denied"),
                                              ("scope", {"target_term": "OTHER"}, "source_scope_changed")):
            env = self.envelope()
            env[field] = replacement
            self.assert_code(expected, self.ingest, env)

    def test_malformed_rows_preserved_in_reconciliation(self):
        env = self.envelope(payload={"items": [{"kcmc": "TEST-COURSE", "xf": "not-a-number"}, None]})
        env["coverage"]["total_rows"] = 2
        result = self.ingest(env)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["status"], "partial_acquisition")
        self.assertGreater(result["parse_failures"], 0)

    def test_failed_refresh_retains_history_but_cannot_replay_as_current(self):
        self.ingest()
        self.store.fail_domain(self.attempt["attempt_id"], "academic", "source_unavailable")
        snap = self.store.private_snapshot(self.attempt["attempt_id"], "academic")
        self.assertTrue(snap["historical_only"])
        self.assertEqual(self.store.readiness(self.attempt["attempt_id"])["academic"], "historical_only")
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["status"], "source_unavailable")
        self.assert_code("new_attempt_required", self.ingest)

    def test_database_write_failure_rolls_back_observation_and_pointer(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_pointer BEFORE INSERT ON active BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.ingest()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["status"], "not_acquired")

    def test_revoke_during_normalization_prevents_publication(self):
        from course_assistant import student_sources
        original = student_sources.parse_payload
        def revoke(*args):
            self.grant["revoked"] = True
            self.write_grant()
            return original(*args)
        with patch.object(student_sources, "parse_payload", revoke):
            self.assert_code("grant_revoked", self.ingest)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)

    def test_private_file_permissions_and_unconfigured_readiness(self):
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        readiness = self.store.readiness(self.attempt["attempt_id"])
        self.assertEqual(readiness["academic"], "not_acquired")
        self.assertEqual(readiness["calendar"], "unknown")

    def test_changed_totals_and_body_total_cannot_claim_complete(self):
        env = self.envelope()
        env["payload"]["total"] = 99
        self.assertEqual(self.ingest(env)["status"], "partial_acquisition")
        self.attempt = self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid", "SOURCE-one")
        env = self.envelope()
        env["coverage"].update(total_rows=99, has_more=True, complete_within_scope=False)
        self.ingest(env)
        env["coverage"].update(page_index=2, total_rows=2, has_more=False, complete_within_scope=True)
        self.assertEqual(self.ingest(env)["status"], "partial_acquisition")
        snapshot = self.store.private_snapshot(self.attempt["attempt_id"], "academic")["snapshot"]
        self.assertEqual(len({row["attempt_candidate_id"] for row in snapshot["records"]}), 2)
        self.assertEqual([row["source_page_index"] for row in snapshot["records"]], [1, 2])
        self.assertEqual(len(snapshot["coverage_evidence"]), 2)

    def test_malformed_new_page_marks_previous_snapshot_historical(self):
        env = self.envelope()
        env["coverage"].update(total_rows=2, has_more=True, complete_within_scope=False)
        self.ingest(env)
        env["coverage"].update(page_index=2)
        env["payload"] = {"wrong_container": [{"kcmc": "TEST"}]}
        self.assert_code("schema_changed", self.ingest, env)
        snapshot = self.store.private_snapshot(self.attempt["attempt_id"], "academic")
        self.assertTrue(snapshot["historical_only"])
        self.assertEqual(snapshot["current_status"], "schema_changed")

    def test_credential_headers_cannot_hide_in_business_fields(self):
        for value in ("Cookie: sid=TEST-SECRET", "Authorization: TEST-SECRET", "password=TEST-SECRET"):
            with self.subTest(value=value):
                self.assert_code("unsafe_business_value", sanitize_payload, "academic", {"items": [{"kcmc": value}]})

    def test_plan_version_cannot_leak_unvalidated_text_to_receipt(self):
        profile = {"cohort": 2023, "major_name": "TEST", "program": "TEST", "study_path_raw": "TEST"}
        self.assert_code("invalid_plan_catalogue", match_plan, profile, [{**profile, "source_alias": "PLAN-A", "version_sha256": "TEST-SECRET"}])

    def test_business_subject_switch_is_detected_before_projection_publication(self):
        env = self.envelope(domain="profile", payload={"xh": "SYNTHETIC-STUDENT-ONE", "zymc": "合成专业"})
        self.ingest(env)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["source_identity"], "observed_pending_verification")
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["status"], "browser_source_observed")
        env = self.envelope(payload={"items": [{"xh": "SYNTHETIC-STUDENT-TWO", "kcmc": "合成课程"}]})
        self.assert_code("source_subject_changed", self.ingest, env)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["observations"], 0)

    def test_alias_without_observed_subject_never_becomes_source_identity(self):
        self.ingest()
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["source_identity"], "unknown")

    def test_old_generation_readiness_marks_prior_success_historical(self):
        self.ingest()
        self.store.create_attempt("TEST-TERM", "https://synthetic.example.invalid")
        description = self.store.readiness(self.attempt["attempt_id"])
        self.assertEqual(description["status"], "source_generation_changed")
        self.assertTrue(description["domains"]["academic"]["historical_only"])
        self.assertEqual(description["academic"], "historical_only")

    def test_mixed_subjects_within_one_page_stop_before_any_publication(self):
        env = self.envelope(payload={"items": [{"xh": "SYNTHETIC-A", "kcmc": "TEST-ONE"}, {"xh": "SYNTHETIC-B", "kcmc": "TEST-TWO"}]})
        self.assert_code("source_subject_changed", self.ingest, env)
        self.assertEqual(self.store.describe(self.attempt["attempt_id"])["domains"]["academic"]["observations"], 0)

    def test_wrapped_observed_subject_cannot_bypass_continuity(self):
        env = self.envelope(domain="profile", payload={"student_number": "SYNTHETIC-A", "major": "TEST"})
        self.ingest(env)
        env = self.envelope(payload={"items": [{"student_number": {"state": "known", "raw_value": "SYNTHETIC-B"}, "kcmc": "TEST"}]})
        self.assert_code("source_subject_changed", self.ingest, env)


class PlanMatchTest(unittest.TestCase):
    def test_directory_category_candidate_does_not_complete_applicability(self):
        profile = {"cohort": 2022, "major_or_category_raw": "合成类专业", "college_name": "合成学院"}
        row = {"cohort": 2022, "major_name": "合成类专业", "college_name": "合成学院", "source_alias": "PLAN-A", "source_observation_kind": "directory_observation"}
        result = match_plan(profile, [row])
        self.assertEqual(result["status"], "candidate_pending_review")
        self.assertEqual(result["binding_status"], "not_verified")
        self.assertEqual(result["mapping_status"], "category_to_major_not_verified")
        self.assertIn("program", result["missing_fields"])
        self.assertIn("major_name", result["missing_fields"])
        self.assertEqual(match_plan(profile, [{**row, "cohort": 2026}])["status"], "profile_fields_missing")

    def test_partial_profile_requires_explicit_directory_observation(self):
        profile = {"cohort": 2022, "major_or_category_raw": "合成类专业", "college_name": "合成学院"}
        row = {"cohort": 2022, "major_name": "合成类专业", "college_name": "合成学院", "source_alias": "PLAN-A"}
        self.assertEqual(match_plan(profile, [row])["candidates"], [])

    def test_different_cohort_never_uses_2026_by_default(self):
        profile = {"cohort": 2023, "major_name": "合成专业", "program": "合成项目", "study_path_raw": "主修"}
        row = {**profile, "cohort": 2026, "source_alias": "PLAN-A", "version_sha256": "0" * 64}
        self.assertEqual(match_plan(profile, [row])["status"], "plan_version_missing")
        row["cohort"] = 2023
        result = match_plan(profile, [row])
        self.assertEqual(result["status"], "candidate_pending_review")
        self.assertEqual(result["binding_status"], "not_verified")
        self.assertEqual(match_plan(profile, [row, {**row, "source_alias": "PLAN-B"}])["status"], "ambiguous_plan")

    def test_missing_profile_and_near_name_do_not_establish_applicability(self):
        self.assertEqual(match_plan({"cohort": 2023}, [])["status"], "profile_fields_missing")
        profile = {"cohort": 2023, "major_name": "合成专业", "program": "合成项目", "study_path_raw": "主修"}
        self.assertEqual(match_plan(profile, [{**profile, "major_name": "近似合成专业", "source_alias": "PLAN-A"}])["status"], "plan_version_missing")


if __name__ == "__main__":
    unittest.main()
