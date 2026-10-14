"""Synthetic on-demand source acquisition, not official authentication tests."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from course_assistant.student_plan_acquisition import (
    OnDemandPlanService, StudentPlanAcquisitionError, scope_from_profile,
)


PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\nstartxref\n0\n%%EOF\n"


class StudentPlanAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "local-only" / "plan-task"
        self.auth_key = "SYNTHETIC-OWNER-A-GRANT-1-EPOCH-1"
        self.auth_error = None
        self.auth_calls = 0
        self.catalogue_calls, self.document_calls = [], []
        self.profile = {"cohort": 2023, "college_name": "虚构学院", "major_name": "虚构专业",
                        "program": "虚构项目", "study_path_raw": "主修"}
        self.candidates = [{"plan_ref": "SYNTHETIC-PLAN-2023", **self.profile, "title": "2023级虚构方案"}]
        self.document = PDF
        self.fetch_error = None
        self.service = self.create_service()

    def authorize(self):
        self.auth_calls += 1
        if self.auth_error:
            raise StudentPlanAcquisitionError(self.auth_error)
        return {"scope_key": self.auth_key, "credential": "IGNORED-NEVER-PERSISTED"}

    def catalogue(self, scope):
        self.catalogue_calls.append(deepcopy(scope))
        if self.fetch_error:
            raise StudentPlanAcquisitionError(self.fetch_error)
        return deepcopy(self.candidates)

    def fetch_document(self, ref):
        self.document_calls.append(ref)
        return self.document

    def create_service(self, **kwargs):
        return OnDemandPlanService(self.root, self.authorize, self.catalogue, self.fetch_document, **kwargs)

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(StudentPlanAcquisitionError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_targeted_non_2026_acquisition_is_unreviewed_not_binding(self):
        result = self.service.acquire(self.profile)
        self.assertEqual(self.catalogue_calls, [dict(self.profile, major_or_category_raw=None)])
        self.assertEqual(self.document_calls, ["SYNTHETIC-PLAN-2023"])
        self.assertEqual(result["status"], "acquired_pending_review")
        self.assertEqual(result["data_source"], "live")
        self.assertEqual(result["binding_status"], "not_verified")
        self.assertEqual(result["rules_status"], "not_reviewed")
        self.assertEqual(result["sha256"], sha256(PDF).hexdigest())
        self.assertEqual(self.service.read_document(result["source_alias"]), PDF)
        self.assertEqual(len(list((self.root / "objects").glob("*.pdf"))), 1)

    def test_query_derived_candidate_cohort_preserves_provenance_in_immutable_metadata(self):
        self.candidates[0]["cohort_provenance"] = "confirmed_query_scope"
        first = self.service.acquire(self.profile)
        metadata_path = self.root / "versions" / (first["source_alias"] + ".json")
        original = metadata_path.read_bytes()
        metadata = json.loads(original)
        self.assertEqual(metadata["candidate"]["cohort_provenance"], "confirmed_query_scope")
        self.assertEqual(first["candidate_cohort_provenance"], "confirmed_query_scope")
        self.assertEqual(first["binding_status"], "not_verified")
        cached = self.service.acquire(self.profile)
        self.assertEqual(cached["candidate_cohort_provenance"], "confirmed_query_scope")
        self.assertEqual(metadata_path.read_bytes(), original)

    def test_legacy_candidates_default_unknown_and_bad_provenance_blocks_download(self):
        legacy = self.service.acquire(self.profile)
        self.assertEqual(legacy["candidate_cohort_provenance"], "unknown")
        for value in (None, True, {"value": "confirmed_query_scope"}, "SYNTHETIC-PRIVATE-VALUE"):
            self.candidates[0]["cohort_provenance"] = value
            downloads = len(self.document_calls)
            result = self.service.acquire(self.profile, force=True)
            self.assertEqual(result["status"], "invalid_plan_catalogue")
            self.assertEqual(len(self.document_calls), downloads)

    def test_cohort_can_come_only_from_explicit_admission_date(self):
        profile = {"admission_date_raw": "20240901", "current_grade_raw": "2026"}
        self.assertEqual(scope_from_profile(profile)["cohort"], 2024)
        self.assertIsNone(scope_from_profile({"current_grade_raw": "2026", "student_number": "20230000"})["cohort"])
        self.assertIsNone(scope_from_profile({"admission_date_raw": "20230230"})["cohort"])
        self.assertIsNone(scope_from_profile({"admission_date_raw": "２０２３０９０１"})["cohort"])
        self.assertIsNone(scope_from_profile({"admission_date_raw": "20240901", "field_states": {"admission_date_raw": "unknown"}})["cohort"])
        self.assert_code("source_scope_conflict", scope_from_profile, {"cohort": 2023, "admission_date_raw": "20240901"})

    def test_missing_cohort_never_defaults_or_queries_entire_directory(self):
        profile = deepcopy(self.profile)
        del profile["cohort"]
        result = self.service.acquire(profile)
        self.assertEqual(result["status"], "profile_fields_missing")
        self.assertIn("cohort", result["missing_fields"])
        self.assertEqual(self.catalogue_calls, [])

    def test_missing_college_or_profession_prevents_query(self):
        for key in ("college_name", "major_name"):
            with self.subTest(key=key):
                profile = deepcopy(self.profile)
                del profile[key]
                self.assertEqual(self.service.acquire(profile)["status"], "profile_fields_missing")
        self.assertEqual(self.catalogue_calls, [])

    def test_category_retained_and_never_promoted_to_verified_major(self):
        profile = {"cohort": 2023, "college_name": "虚构学院", "major_or_category_raw": "虚构类"}
        self.candidates = [{"plan_ref": "SYNTHETIC-CATEGORY", **profile, "title": "虚构类培养目录"}]
        result = self.service.acquire(profile)
        self.assertIsNone(self.catalogue_calls[0]["major_name"])
        self.assertEqual(self.catalogue_calls[0]["major_or_category_raw"], "虚构类")
        self.assertEqual(result["mapping_status"], "category_to_major_not_verified")
        self.assertIn("major_name", result["missing_fields"])
        self.assertIn("program", result["missing_fields"])
        self.assertIn("study_path_raw", result["missing_fields"])
        self.assertEqual(result["binding_status"], "not_verified")

    def test_missing_candidate_applicability_fields_stay_missing(self):
        self.candidates = [{"plan_ref": "SYNTHETIC-MISSING", "cohort": 2023}]
        result = self.service.acquire(self.profile)
        self.assertEqual(result["status"], "acquired_pending_review")
        self.assertIn("major_name", result["missing_fields"])
        self.assertIn("program", result["missing_fields"])
        self.assertEqual(result["binding_status"], "not_verified")

    def test_different_cohorts_and_paths_have_separate_cache_keys(self):
        original = self.service.acquire(self.profile)
        for key, value in (("cohort", 2024), ("program", "虚构双学位项目"), ("study_path_raw", "辅修")):
            with self.subTest(key=key):
                profile = dict(self.profile, **{key: value})
                self.candidates = [{"plan_ref": "SYNTHETIC-OTHER", **profile}]
                result = self.service.acquire(profile)
                self.assertEqual(result["data_source"], "live")
                self.assertNotEqual(result["source_alias"], original["source_alias"])
        self.assertEqual(len(self.catalogue_calls), 4)

    def test_zero_or_multiple_candidates_never_download(self):
        for rows, status in (([], "plan_missing"), (self.candidates * 2, "ambiguous_plan")):
            with self.subTest(status=status):
                self.candidates = rows
                result = self.service.acquire(self.profile, force=True)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["candidate_count"], len(rows))
                self.assertEqual(result["data_source"], "none")
        self.assertEqual(self.document_calls, [])

    def test_explicit_candidate_scope_conflict_blocks_download(self):
        for key, value in (("cohort", 2026), ("college_name", "其他学院"), ("major_name", "其他专业"),
                           ("program", "其他项目"), ("study_path_raw", "辅修")):
            with self.subTest(key=key):
                self.candidates = [{"plan_ref": "SYNTHETIC-WRONG", **self.profile, key: value}]
                self.assertEqual(self.service.acquire(self.profile, force=True)["status"], "source_scope_conflict")
        self.assertEqual(self.document_calls, [])

    def test_conflicting_profile_scope_does_not_contact_callback(self):
        self.assert_code("source_scope_conflict", self.service.acquire,
                         dict(self.profile, field_states={"major_name": "conflicting"}))
        self.assertEqual(self.catalogue_calls, [])

    def test_cache_returns_historical_source_and_rechecks_authorization(self):
        result = self.service.acquire(self.profile)
        calls = self.auth_calls
        cached = self.service.acquire(self.profile)
        self.assertGreaterEqual(self.auth_calls - calls, 2)
        self.assertEqual(cached["data_source"], "historical_cache")
        self.assertEqual(cached["source_alias"], result["source_alias"])
        self.assertEqual(len(self.document_calls), 1)
        self.assertEqual(self.service.describe(self.profile)["data_source"], "historical_cache")

    def test_force_same_content_does_not_create_duplicate_version(self):
        first = self.service.acquire(self.profile)
        refreshed = self.service.acquire(self.profile, force=True)
        self.assertEqual(first["source_alias"], refreshed["source_alias"])
        self.assertEqual(refreshed["version_count"], 1)
        self.assertEqual(refreshed["data_source"], "live")
        self.assertEqual(len(self.document_calls), 2)

    def test_force_changed_content_keeps_immutable_old_version(self):
        first = self.service.acquire(self.profile)
        old_path = self.root / "objects" / (first["sha256"] + ".pdf")
        self.document = PDF.replace(b"1 0 obj", b"2 0 obj")
        second = self.service.acquire(self.profile, force=True)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(second["version_count"], 2)
        self.assertEqual(old_path.read_bytes(), PDF)
        self.assert_code("historical_document_requires_review", self.service.read_document, first["source_alias"])

    def test_failed_force_marks_old_data_history_and_blocks_body(self):
        first = self.service.acquire(self.profile)
        self.fetch_error = "source_unavailable"
        result = self.service.acquire(self.profile, force=True)
        self.assertEqual(result["status"], "source_unavailable")
        self.assertEqual(result["data_source"], "historical_only")
        self.assertTrue(result["historical_only"])
        self.assertEqual(result["sha256"], first["sha256"])
        self.assert_code("source_unavailable", self.service.read_document, first["source_alias"])
        self.fetch_error = None
        self.assertEqual(self.service.acquire(self.profile)["data_source"], "historical_only")
        self.assertEqual(self.service.acquire(self.profile, force=True)["status"], "acquired_pending_review")

    def test_revocation_expiry_and_logout_prevent_cached_return_or_body(self):
        first = self.service.acquire(self.profile)
        for code in ("grant_revoked", "grant_expired", "source_session_expired", "awaiting_user_login", "permission_denied"):
            with self.subTest(code=code):
                self.auth_error = code
                self.assert_code(code, self.service.acquire, self.profile)
                self.assert_code(code, self.service.describe, self.profile)
                self.assert_code(code, self.service.read_document, first["source_alias"])
        self.assertEqual(len(self.document_calls), 1)

    def test_changed_owner_grant_or_epoch_cannot_reuse_other_authority_cache(self):
        first = self.service.acquire(self.profile)
        self.auth_key = "SYNTHETIC-OWNER-B-GRANT-2-EPOCH-3"
        self.assertEqual(self.service.describe(self.profile)["version_count"], 0)
        self.assert_code("permission_denied", self.service.read_document, first["source_alias"])
        second = self.service.acquire(self.profile)
        self.assertNotEqual(second["source_alias"], first["source_alias"])
        self.assertEqual(len(self.document_calls), 2)

    def test_authority_change_during_catalogue_prevents_document_and_publication(self):
        def switch_scope(scope):
            self.auth_key = "SYNTHETIC-CHANGED"
            return self.candidates
        self.service.fetch_catalogue = switch_scope
        self.assert_code("source_authorization_changed", self.service.acquire, self.profile)
        self.assertEqual(self.document_calls, [])
        self.assertEqual(list((self.root / "versions").glob("*.json")), [])

    def test_authority_change_during_download_prevents_publication(self):
        def switch_scope(ref):
            self.auth_key = "SYNTHETIC-CHANGED"
            return PDF
        self.service.fetch_document = switch_scope
        self.assert_code("source_authorization_changed", self.service.acquire, self.profile)
        self.assertEqual(list((self.root / "objects").glob("*.pdf")), [])

    def test_pdf_transport_shape_content_type_signature_and_eof_are_guarded(self):
        for response in (b"<html>login</html>" * 4, b"%PDF-1.7\n" + b"x" * 100,
                         b"fake " + PDF, {"body": PDF, "content_type": "text/html"},
                         {"body": PDF, "content_type": None}, {"body": PDF, "content_type": "application/pdf", "headers": {}},
                         {"body": "not-bytes", "content_type": "application/pdf"}):
            with self.subTest(response_type=type(response).__name__):
                self.document = response
                self.assertEqual(self.service.acquire(self.profile, force=True)["status"], "invalid_pdf")
        self.assertEqual(list((self.root / "objects").glob("*.pdf")), [])
        self.document = {"body": PDF, "content_type": "application/pdf; charset=binary"}
        self.assertEqual(self.service.acquire(self.profile, force=True)["status"], "acquired_pending_review")

    def test_oversized_pdf_is_not_written(self):
        self.service.max_pdf_bytes = 32
        result = self.service.acquire(self.profile)
        self.assertEqual(result["status"], "document_limit_exceeded")
        self.assertEqual(list((self.root / "objects").glob("*.pdf")), [])

    def test_public_results_contain_no_profile_locator_or_auth_receipt_values(self):
        result = self.service.acquire(self.profile)
        public = json.dumps({"acquire": result, "describe": self.service.describe(self.profile)}, ensure_ascii=False)
        for value in ("虚构学院", "虚构专业", "虚构项目", "主修", self.auth_key,
                      "SYNTHETIC-PLAN-2023", str(self.root), "IGNORED-NEVER-PERSISTED"):
            self.assertNotIn(value, public)
        persisted = b"".join(path.read_bytes() for path in self.root.rglob("*.json"))
        self.assertNotIn(self.auth_key.encode(), persisted)
        self.assertNotIn(b"IGNORED-NEVER-PERSISTED", persisted)
        metadata = json.loads((self.root / "versions" / (result["source_alias"] + ".json")).read_bytes())
        self.assertEqual(metadata["scope"]["cohort"], 2023)
        self.assertEqual(metadata["candidate"]["major_name"], "虚构专业")

    def test_callback_exceptions_do_not_leak_secrets_in_failure(self):
        def bad_callback(scope):
            raise RuntimeError("password=FICTIONAL-SECRET url=/private/hidden")
        self.service.fetch_catalogue = bad_callback
        result = self.service.acquire(self.profile)
        self.assertEqual(result["status"], "source_unavailable")
        self.assertNotIn("FICTIONAL-SECRET", json.dumps(result))
        self.assertNotIn(b"FICTIONAL-SECRET", b"".join(path.read_bytes() for path in self.root.rglob("*.json")))

    def test_document_reference_cannot_be_url_or_path(self):
        for ref in ("https://example.invalid/doc", "../doc", "/tmp/doc", "id?ticket=SECRET"):
            with self.subTest(ref=ref):
                self.candidates = [{**self.profile, "plan_ref": ref}]
                self.assertEqual(self.service.acquire(self.profile, force=True)["status"], "invalid_plan_catalogue")
        self.assertEqual(self.document_calls, [])

    def test_permissions_are_700_directories_and_600_material_files(self):
        self.service.acquire(self.profile)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for path in self.root.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_public_root_and_wide_existing_root_are_rejected_without_chmod(self):
        public = Path(self.temp.name).resolve() / "docs" / "cache"
        self.assert_code("invalid_private_path", OnDemandPlanService,
                         public, self.authorize, self.catalogue, self.fetch_document)
        self.assertFalse(public.exists())
        os.chmod(self.root, 0o755)
        self.assert_code("private_permissions_required", self.create_service)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)

    def test_permissions_widened_after_construction_block_cache(self):
        self.service.acquire(self.profile)
        os.chmod(self.root / "objects", 0o755)
        self.assert_code("private_permissions_required", self.service.describe, self.profile)

    def test_symlink_private_root_is_rejected(self):
        alias = self.root.parent / "symlink-task"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_code("invalid_private_path", OnDemandPlanService,
                         alias, self.authorize, self.catalogue, self.fetch_document)

    def test_invalid_authorization_never_queries(self):
        self.service.authorize = lambda: {"owner": "SYNTHETIC-A"}
        self.assert_code("permission_denied", self.service.acquire, self.profile)
        self.assertEqual(self.catalogue_calls, [])

    def test_tampered_immutable_pdf_blocks_cache_and_private_read(self):
        first = self.service.acquire(self.profile)
        (self.root / "objects" / (first["sha256"] + ".pdf")).write_bytes(PDF + b"tamper")
        self.assert_code("stale_input", self.service.describe, self.profile)
        self.assert_code("stale_input", self.service.read_document, first["source_alias"])

    def test_corrupted_cache_cannot_turn_public_aliases_into_private_text(self):
        self.service.acquire(self.profile)
        state_path = next((self.root / "scopes").glob("*.json"))
        state = json.loads(state_path.read_bytes())
        state["versions"].append("PRIVATE-TEXT-SENTINEL")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assert_code("private_store_unavailable", self.service.describe, self.profile)


if __name__ == "__main__":
    unittest.main()
