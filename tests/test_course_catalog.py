"""Local catalog engineering fixtures; no school access or relevance gold."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.course_catalog import CourseCatalogError, CourseCatalogService, ENVIRONMENT_KEY


class CourseCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        self.courses_path = self.dataset / "courses.json"
        self.manifest_path = self.dataset / "manifest.json"
        self.delivery_path = self.root / "delivery.json"
        self.rows = [self.course("SYN1001M", "虚构数据分析"), self.course("SYN1002M", "虚构数据结构"),
                     self.course("SYN2001M", "虚构英语")]
        self.manifest = {"schema_version": "course-offline-dedup/1", "dataset_version": "a" * 64,
            "data_kind": "derived_real_reference_observations", "security_realm": "restricted_reference",
            "shared_projection_contains_personal_state": False, "counts": {"courses": len(self.rows)},
            "files": {"courses": {"path": "courses.json", "records": len(self.rows), "sha256": ""},
                "restricted_personal_page_observations": {"path": "DO_NOT_READ_PRIVATE_SENTINEL", "sha256": "bad"}}}
        self.delivery = {"date": "2099-01-01", "dataset_version": "a" * 64,
            "dataset_manifest": str(self.manifest_path), "requirements_manifest": "DO_NOT_READ_PDF_SENTINEL",
            "private_source_path": "PRIVATE_DELIVERY_SENTINEL"}
        self.publish()

    @staticmethod
    def course(code, name):
        # Shape copied from the existing real deduplicated projection, with
        # invented course facts and privacy sentinels in discarded fields.
        return {"code": code, "code_namespace": "zju_undergraduate_selection_primary_code",
            "course_id": "PRIVATE_COURSE_ID_SENTINEL", "stable_course_identity_verified": False,
            "observation_ids": ["PRIVATE_OBSERVATION_ID_SENTINEL"], "definitions": [{
                "definition_id": "PRIVATE_DEFINITION_SENTINEL", "term": "2099-2100-1",
                "projection_kind": "field_evidence_union_not_observed_snapshot",
                "observation_ids": ["PRIVATE_OBSERVATION_ID_SENTINEL"], "fields": {
                    "name": {"state": "known", "value": name,
                        "assertions": [{"value": name, "observation_ids": ["PRIVATE_OBSERVATION_ID_SENTINEL"]}],
                        "unknown_observation_ids": []},
                    "credits": {"state": "known", "value": "DO_NOT_TRANSMIT_CREDITS_SENTINEL"},
                    "college": {"state": "known", "value": "DO_NOT_TRANSMIT_COLLEGE_SENTINEL"},
                    "owner_student_id": {"value": "PRIVATE_OWNER_SENTINEL"}}}]}

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def publish(self):
        self.write(self.courses_path, self.rows)
        self.manifest["counts"]["courses"] = len(self.rows)
        self.manifest["files"]["courses"]["records"] = len(self.rows)
        self.manifest["files"]["courses"]["sha256"] = sha256(self.courses_path.read_bytes()).hexdigest()
        self.write(self.manifest_path, self.manifest)
        self.write(self.delivery_path, self.delivery)

    def service(self):
        return CourseCatalogService(self.delivery_path)

    def assert_safe(self, result):
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("SENTINEL", serialized)
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)
        forbidden = {"owner_student_id", "observation_ids", "course_id", "definition_id", "dataset_manifest"}
        self.assertFalse(forbidden & set(keys(result)))

    def test_unconfigured_is_not_ready_with_no_synthetic_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            service = CourseCatalogService()
        for result in (service.describe(), service.search("数据")):
            self.assertEqual(result["status"], "not_ready")
            self.assertEqual(result["reason"], "course_catalog_not_configured")
            self.assertEqual(result["records"], [])
            self.assert_safe(result)

    def test_environment_is_explicit_configuration_and_argument_overrides_it(self):
        with patch.dict(os.environ, {ENVIRONMENT_KEY: str(self.delivery_path)}):
            self.assertEqual(CourseCatalogService().describe()["status"], "ready")
            self.assertEqual(CourseCatalogService("").describe()["status"], "not_ready")
            self.assertEqual(CourseCatalogService(self.root / "missing").describe()["status"], "not_ready")

    def test_describe_minimizes_fields_and_does_not_open_other_components(self):
        result = self.service().describe()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["data_kind"], "local_course_reference")
        self.assertEqual(result["coverage"]["available_courses"], 3)
        self.assertEqual(result["coverage"]["batch_date"], "2099-01-01")
        self.assertIsNone(result["coverage"]["observed_at"])
        self.assertFalse(result["coverage"]["complete_for_school"])
        self.assertFalse(result["coverage"]["current_offering_verified"])
        self.assertRegex(result["source_version"], r"^CATALOG-[a-f0-9]{64}$")
        self.assert_safe(result)

    def test_exact_code_and_code_inside_question_are_case_insensitive(self):
        for query in ("syn1001m", "ＳＹＮ１００１Ｍ", "请介绍SYN1001M这门课程"):
            result = self.service().search(query)
            self.assertEqual([row["course_code"] for row in result["records"]], ["SYN1001M"])
            self.assert_safe(result)

    def test_name_keywords_return_bounded_reference_scope(self):
        result = self.service().search("数据", limit=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["matched_records"], 2)
        self.assertEqual(result["returned_records"], 1)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["records"][0]["course_name"], "虚构数据分析")
        multi = self.service().search("虚构 分析")
        self.assertEqual([row["course_code"] for row in multi["records"]], ["SYN1001M"])
        self.assert_safe(result)

    def test_no_match_does_not_claim_absence_in_school(self):
        result = self.service().search("目录外虚构课程")
        self.assertEqual(result["status"], "no_match_in_scope")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["matched_records"], 0)
        self.assertFalse(result["coverage"]["complete_for_school"])

    def test_record_exposes_no_internal_ids_or_unneeded_source_fields(self):
        result = self.service().search("SYN1001M")
        row = result["records"][0]
        self.assertEqual(set(row), {"course_code", "course_name", "course_name_variants", "fields", "coverage", "source_version"})
        self.assertEqual(row["fields"], {"course_code": "known", "course_name": "known"})
        self.assertFalse(row["coverage"]["stable_course_identity_verified"])
        self.assertFalse(row["coverage"]["personal_eligibility_verified"])
        self.assert_safe(result)

    def test_conflicting_names_preserve_alternatives_without_choosing_fact(self):
        second = deepcopy(self.rows[0]["definitions"][0])
        second["fields"]["name"]["value"] = "虚构算法分析"
        self.rows[0]["definitions"].append(second)
        self.publish()
        result = self.service().search("算法")
        row = result["records"][0]
        self.assertIsNone(row["course_name"])
        self.assertEqual(row["fields"]["course_name"], "conflicting")
        self.assertEqual(set(row["course_name_variants"]), {"虚构数据分析", "虚构算法分析"})
        self.assert_safe(result)

    def test_unknown_name_remains_unknown_but_exact_code_is_findable(self):
        self.rows[0]["definitions"][0]["fields"]["name"] = {"state": "unknown", "value": None}
        self.publish()
        row = self.service().search("SYN1001M")["records"][0]
        self.assertIsNone(row["course_name"])
        self.assertEqual(row["fields"]["course_name"], "unknown")

    def test_courses_tampering_is_detected_on_next_call(self):
        service = self.service()
        self.assertEqual(service.describe()["status"], "ready")
        self.courses_path.write_text("[]", encoding="utf-8")
        for result in (service.describe(), service.search("数据")):
            self.assertEqual(result["status"], "not_ready")
            self.assertEqual(result["reason"], "course_catalog_integrity_failed")
            self.assert_safe(result)

    def test_manifest_version_hash_and_count_contracts_fail_closed(self):
        for mutation in ("version", "hash", "count", "realm", "personal"):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.manifest)
                if mutation == "version":
                    self.manifest["dataset_version"] = "b" * 64
                elif mutation == "hash":
                    self.manifest["files"]["courses"]["sha256"] = "b" * 64
                elif mutation == "count":
                    self.manifest["files"]["courses"]["records"] = 4
                elif mutation == "realm":
                    self.manifest["security_realm"] = "school_personal"
                else:
                    self.manifest["shared_projection_contains_personal_state"] = True
                self.write(self.manifest_path, self.manifest)
                result = self.service().search("数据")
                self.assertEqual(result["status"], "not_ready")
                self.assert_safe(result)
                self.manifest = original
        self.publish()

    def test_optional_manifest_digest_is_honored(self):
        self.delivery["dataset_manifest_sha256"] = sha256(self.manifest_path.read_bytes()).hexdigest()
        self.write(self.delivery_path, self.delivery)
        service = self.service()
        self.assertEqual(service.describe()["status"], "ready")
        self.manifest["untrusted_extra"] = "changed"
        self.write(self.manifest_path, self.manifest)
        self.assertEqual(service.describe()["reason"], "course_catalog_integrity_failed")

    def test_relative_delivery_manifest_supported_but_component_escape_rejected(self):
        self.delivery["dataset_manifest"] = "dataset/manifest.json"
        self.write(self.delivery_path, self.delivery)
        self.assertEqual(self.service().describe()["status"], "ready")
        self.manifest["files"]["courses"]["path"] = "../outside.json"
        self.write(self.manifest_path, self.manifest)
        self.assertEqual(self.service().describe()["status"], "not_ready")

    def test_changed_projection_gets_new_opaque_source_version(self):
        before = self.service().describe()["source_version"]
        self.rows[0]["definitions"][0]["fields"]["name"]["value"] = "虚构新版分析"
        self.publish()
        after = self.service().describe()["source_version"]
        self.assertNotEqual(before, after)
        self.assert_safe(self.service().search("新版"))

    def test_component_symlink_cannot_escape_configured_dataset(self):
        outside = self.root / "outside.json"
        outside.write_bytes(self.courses_path.read_bytes())
        self.courses_path.unlink()
        self.courses_path.symlink_to(outside)
        result = self.service().describe()
        self.assertEqual(result["status"], "not_ready")
        self.assert_safe(result)

    def test_excessive_name_variants_do_not_create_unbounded_remote_rows(self):
        first = self.rows[0]["definitions"][0]
        self.rows[0]["definitions"] = []
        for index in range(11):
            variant = deepcopy(first)
            variant["fields"]["name"]["value"] = "虚构名称" + str(index)
            self.rows[0]["definitions"].append(variant)
        self.publish()
        self.assertEqual(self.service().search("SYN1001M")["status"], "not_ready")

    def test_invalid_query_and_limit_cannot_become_path_configuration(self):
        for query, limit in (("", 5), ("x" * 201, 5), ("数据\n", 5), (None, 5), ("数据", 0),
                             ("数据", 11), ("数据", True), ("数据", "5")):
            with self.subTest(query=query, limit=limit):
                with self.assertRaises(CourseCatalogError) as raised:
                    self.service().search(query, limit)
                self.assertEqual(raised.exception.code, "invalid_course_catalog_query")
        result = self.service().search(str(self.root / "private.json"))
        self.assertEqual(result["records"], [])
        self.assert_safe(result)

    def test_malformed_config_or_projection_has_safe_failure(self):
        self.assertEqual(CourseCatalogService({"path": str(self.root)}).describe()["status"], "not_ready")
        for value in ({"bad": "shape"}, [self.course("../escape", "虚构课程")],
                      [self.course("SYN1M", "/Users/PRIVATE_NAME_SENTINEL")]):
            if isinstance(value, list):
                self.rows = value
                self.publish()
            else:
                self.write(self.courses_path, value)
                self.manifest["files"]["courses"]["sha256"] = sha256(self.courses_path.read_bytes()).hexdigest()
                self.write(self.manifest_path, self.manifest)
            result = self.service().search("数据")
            self.assertEqual(result["status"], "not_ready")
            self.assert_safe(result)


if __name__ == "__main__":
    unittest.main()
