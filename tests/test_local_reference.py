"""Synthetic engineering fixtures, not human relevance annotations."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.local_reference import COMPONENTS, LocalReferenceError, LocalReferenceIndex


class LocalReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "local-only"
        self.sources, self.dataset, self.requirements = [self.base / folder for folder in ("sources", "dataset", "requirements")]
        for folder in (self.sources, self.dataset, self.requirements):
            folder.mkdir(parents=True)
        self.delivery_path, self.grant_path = self.base / "delivery.json", self.base / "grant.json"
        self.bundle_path = self.base / "indexes" / "v1.json"
        self.pages = {
            1: "2099级虚构材料专业培养方案\n",
            2: "(2)专业模块课程 8学分\n可任选这些测试课程，不计算个人剩余学分。\n1)虚构分类 3门\n"
               "SYN9999M前节必修 2.0 2.0-0.0\n课程号 课程名称 学分 周学时\nSYN1001M虚构算法** 3.0 3.0-0.0\n",
            3: "课程号 课程名称 学分 周学时\nSYN1002M虚构劳动课程** 2.0 2.0-0.0\n",
            4: "SYN1003M虚构实验** 2.0 2.0-0.0\n课程号 实践教学环节\nSYN2001M首片外实践 2.0 2.0-0.0\n",
            5: "(1)第三课堂 2学分\nDEFERRED_SENTINEL\n辅修培养方案：\n*仅为辅修项目标记。\n**为辅修学位路径标记。\n",
        }
        text = "".join("=== SOURCE PAGE " + str(page) + " ===\n" + value for page, value in self.pages.items())
        self.text_path, self.pdf_path = self.sources / "fictional-a.txt", self.sources / "fictional-a.pdf"
        self.text_path.write_text(text, encoding="utf-8")
        self.pdf_path.write_bytes(b"%PDF-synthetic-fixture-only")
        other_text, other_pdf = self.sources / "fictional-b.txt", self.sources / "fictional-b.pdf"
        other_text.write_text("=== SOURCE PAGE 1 ===\nUNAUTHORIZED_PROGRAM_SENTINEL\n", encoding="utf-8")
        other_pdf.write_bytes(b"%PDF-second-synthetic-fixture")
        self.metadata = [
            {"plan_number": "FICTIONAL-A", "pdf_path": str(self.pdf_path), "text_path": str(self.text_path),
             "pdf_sha256": self.hash(self.pdf_path), "text_sha256": self.hash(self.text_path), "page_count": 5,
             "cohort_from_directory": "2099", "major_name_raw": "虚构材料", "conflicting_cohort_mentions": []},
            {"plan_number": "FICTIONAL-B", "pdf_path": str(other_pdf), "text_path": str(other_text),
             "pdf_sha256": self.hash(other_pdf), "text_sha256": self.hash(other_text), "page_count": 1,
             "cohort_from_directory": "2099", "major_name_raw": "虚构其他专业", "conflicting_cohort_mentions": [{"page": 1}]},
        ]
        self.write(self.requirements / "plans.json", self.metadata)
        self.requirements_manifest = {"source_batch": str(self.sources), "source_digest": "SYNTHETIC-SOURCE-v1",
                                      "files": {"plans.json": self.hash(self.requirements / "plans.json")}}
        self.write(self.requirements / "manifest.json", self.requirements_manifest)
        files = {}
        for name in COMPONENTS:
            rows = [{"code": "SYN1001M", "course_id": "SYNTHETIC-COURSE"}] if name == "courses" else []
            self.write(self.dataset / (name + ".json"), rows)
            files[name] = {"path": name + ".json", "sha256": self.hash(self.dataset / (name + ".json"))}
        (self.dataset / "restricted_personal_page_observations.json").write_bytes(b"PRIVATE_SENTINEL_NOT_JSON")
        self.dataset_manifest = {"dataset_version": "SYNTHETIC-DATASET-v1", "security_realm": "restricted_reference",
                                 "shared_projection_contains_personal_state": False, "files": files}
        self.write(self.dataset / "manifest.json", self.dataset_manifest)
        self.write(self.base / "blueprint.json", {"source_registry": {"EVAL-A": {
            "plan_number": "FICTIONAL-A", "pdf_sha256": self.hash(self.pdf_path)}}})
        self.delivery = {"dataset_version": self.dataset_manifest["dataset_version"],
                         "dataset_manifest": str(self.dataset / "manifest.json"),
                         "requirements_manifest": str(self.requirements / "manifest.json"),
                         "rag_blueprint": str(self.base / "blueprint.json")}
        self.write(self.delivery_path, self.delivery)
        self.grant = {"grant_id": "SYNTHETIC-LOCAL-GRANT", "security_realm": "restricted_reference",
                      "subject_kind": "local_review", "purpose": "local_reference_review", "status": "active",
                      "source_aliases": ["EVAL-A"], "plan_path": "major", "target_term": "2026-2027-1",
                      "allowed_dataset_versions": [self.dataset_manifest["dataset_version"]],
                      "allowed_source_versions": [self.requirements_manifest["source_digest"]],
                      "expires_at": "2099-01-01T00:00:00+00:00", "revoked": False,
                      "allowed_pages": {"EVAL-A": [1, 2, 3, 4, 5]},
                      "assistant_review_id": "synthetic-engineering-fixture-not-gold",
                      "assistant_reviewed_pages": {"EVAL-A": [1, 2, 3, 4, 5]},
                      "curriculum_selector": {
                          "title_span": {"page": 1, "line_start": 1, "line_end": 1},
                          "requirement_span": {"page": 2, "line_start": 1, "line_end": 2},
                          "path_note_span": {"page": 5, "line_start": 3, "line_end": 5},
                          "header_spans": [{"page": 2, "line_start": 5, "line_end": 5},
                                           {"page": 3, "line_start": 1, "line_end": 1}],
                          "course_row_spans": [{"page": 2, "line_start": 6, "line_end": 6},
                                               {"page": 3, "line_start": 2, "line_end": 2},
                                               {"page": 4, "line_start": 1, "line_end": 1}],
                          "catalog_count_spans": [{"page": 2, "line_start": 3, "line_end": 3}],
                      }}
        self.write(self.grant_path, self.grant)

    @staticmethod
    def hash(path):
        return sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def build(self):
        return LocalReferenceIndex.build(self.delivery_path, self.grant_path, self.bundle_path)

    def index(self):
        self.build()
        return LocalReferenceIndex(self.delivery_path, self.grant_path, self.bundle_path)

    def assert_error(self, code, action):
        with self.assertRaises(LocalReferenceError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(str(self.base), str(raised.exception))

    def test_registry_preserves_conflicts_and_catalog_but_only_selected_slice_is_indexed(self):
        index = self.index()
        summary = index.describe()
        self.assertEqual(summary["source_count"], 2)
        self.assertEqual(summary["admitted_source_count"], 1)
        self.assertEqual(summary["module"]["catalog_course_count"], 3)
        self.assertEqual(index.course_codes, ["SYN1001M", "SYN1002M", "SYN1003M"])
        self.assertEqual(index.bundle["registry"][1]["page_states"]["1"], "conflicting")
        self.assertEqual(summary["human_gold_count"], 0)
        self.assertFalse(summary["model_context_allowed"])
        self.assertNotIn("UNAUTHORIZED_PROGRAM_SENTINEL", json.dumps(index.bundle["chunks"]))
        self.assertNotIn(str(self.base), json.dumps(summary))

    def test_exact_code_both_modes_returns_complete_cross_page_context_and_roundtrips(self):
        index = self.index()
        for mode in ("L0", "L1"):
            with self.subTest(mode=mode):
                result = index.search("SYN1003M", mode=mode)
                self.assertEqual(result["status"], "pending_human_review")
                self.assertFalse(result["answer_support_verified"])
                self.assertEqual(len(result["matches"]), 1)
                match = result["matches"][0]
                self.assertEqual(match["physical_pages"], [2, 3, 4, 5])
                self.assertEqual({row["kind"] for row in match["context"]}, {"requirement", "table_header", "path_note"})
                resolved = index.resolve(match["reference"])
                self.assertEqual(resolved, {key: value for key, value in match.items() if key != "score"})
                self.assertNotIn("DEFERRED_SENTINEL", json.dumps(resolved))
                self.assertNotIn(str(self.base), json.dumps(resolved))
                self.assertEqual(resolved["review_status"], "assistant_reviewed_pending_human")

    def test_bigram_can_find_ordinary_labor_without_deferred_keyword_exclusion(self):
        index = self.index()
        result = index.search("虚构劳动")
        self.assertTrue(any("SYN1002M" in row["text"] for row in result["matches"]))
        self.assertEqual(index.search("第三课堂")['status'], "deferred_by_business_scope")
        self.assertEqual(index.search("第二课堂")['matches'], [])
        self.assertEqual(index.search("SYN2001M")['status'], "no_supported_answer_in_scope")
        self.assertEqual(index.search("SYN8888M")['matches'], [])

    def test_mock_personal_other_purpose_and_expiry_are_denied_before_source_build(self):
        for changes in ({"subject_kind": "mock"}, {"owner_student_id": "SYNTHETIC-OTHER"},
                        {"purpose": "student_recommendation"}, {"source_aliases": ["EVAL-B"]},
                        {"expires_at": "2000-01-01T00:00:00Z"}, {"expires_at": "2099-01-01"},
                        {"revoked": True}, {"status": "revoked"}):
            with self.subTest(changes=changes):
                self.write(self.grant_path, {**self.grant, **changes})
                self.assert_error("inaccessible_resource", self.build)
                self.assertFalse(self.bundle_path.exists())

    def test_live_revocation_expiry_and_scope_reduction_reject_old_reference(self):
        index = self.index()
        reference = index.search("SYN1001M")['matches'][0]['reference']
        self.write(self.grant_path, {**self.grant, "revoked": True})
        self.assert_error("inaccessible_resource", lambda: index.resolve(reference))
        self.assert_error("inaccessible_resource", lambda: index.search("SYN1001M"))
        self.write(self.grant_path, {**self.grant, "expires_at": "2000-01-01T00:00:00Z"})
        self.assert_error("inaccessible_resource", index.shared_components)
        self.write(self.grant_path, {**self.grant, "allowed_pages": {"EVAL-A": [1]}})
        self.assert_error("inaccessible_resource", lambda: index.resolve(reference))
        self.assertEqual(index.course_codes, [])
        self.assertEqual(index.search("SYN1001M")['matches'], [])

    def test_disallowed_body_and_required_context_are_filtered_before_scoring(self):
        index = self.index()
        self.write(self.grant_path, {**self.grant, "allowed_pages": {"EVAL-A": [1, 2, 3, 4]}})
        import course_assistant.local_reference as module
        original = module.terms
        seen = []
        def track(text):
            seen.append(text)
            return original(text)
        with patch.object(module, "terms", side_effect=track):
            self.assertTrue(all(row['kind'] != 'course_row' for row in index.search("虚构劳动")['matches']))
        self.assertFalse(any("SYN1002M" in text for text in seen))
        self.assertFalse(any("DEFERRED_SENTINEL" in text for text in seen))

    def test_forged_chunk_ids_references_and_nonstring_reference_are_rejected(self):
        index = self.index()
        for reference in (index.bundle["chunks"][0]["chunk_id"], "LOCAL-REF-forged", {"page": 2}, None):
            with self.subTest(reference=reference):
                self.assert_error("inaccessible_resource", lambda: index.resolve(reference))

    def test_changed_pdf_page_text_bundle_or_manifests_are_rejected(self):
        for target in ("pdf", "text", "bundle", "dataset_manifest", "requirements_manifest"):
            with self.subTest(target=target):
                # Restore the exact original bytes between independent corruptions.
                if not self.bundle_path.exists():
                    self.build()
                index = LocalReferenceIndex(self.delivery_path, self.grant_path, self.bundle_path)
                paths = {"pdf": self.pdf_path, "text": self.text_path, "bundle": self.bundle_path,
                         "dataset_manifest": self.dataset / "manifest.json",
                         "requirements_manifest": self.requirements / "manifest.json"}
                path = paths[target]
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                self.assert_error("stale_input", lambda: index.search("SYN1001M"))
                path.write_bytes(original)

    def test_shared_components_only_reads_whitelisted_files_and_checks_hash(self):
        index = self.index()
        components = index.shared_components()
        self.assertEqual(set(components), set(COMPONENTS))
        self.assertEqual(components["courses"][0]["code"], "SYN1001M")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(components))
        (self.dataset / "meetings.json").write_text("[] ", encoding="utf-8")
        self.assert_error("stale_input", index.shared_components)

    def test_changed_delivery_version_and_unapproved_source_version_are_denied(self):
        index = self.index()
        self.write(self.grant_path, {**self.grant, "allowed_source_versions": ["SYNTHETIC-UNAPPROVED"]})
        self.assert_error("stale_input", index.authorize)
        self.write(self.grant_path, self.grant)
        self.write(self.delivery_path, {**self.delivery, "dataset_version": "SYNTHETIC-NEW"})
        self.assert_error("stale_input", index.authorize)

    def test_mixed_deferred_note_bad_catalog_count_and_invalid_ranges_block_build(self):
        for modify in (lambda selector: selector["path_note_span"].update(line_start=1),
                       lambda selector: selector["course_row_spans"].pop(),
                       lambda selector: selector["title_span"].update(page=999)):
            with self.subTest(modify=modify):
                grant = deepcopy(self.grant)
                modify(grant["curriculum_selector"])
                self.write(self.grant_path, grant)
                self.assert_error("source_unavailable", self.build)
                self.assertFalse(self.bundle_path.exists())

    def test_source_rows_from_previous_table_and_practice_tail_are_not_admitted(self):
        index = self.index()
        self.assertNotIn("SYN9999M", index.course_codes)
        self.assertNotIn("SYN2001M", index.course_codes)
        self.assertEqual(index.search("SYN9999M")['matches'], [])

    def test_build_never_overwrites_source_existing_bundle_or_nonprivate_output(self):
        self.assert_error("inaccessible_resource", lambda: LocalReferenceIndex.build(
            self.delivery_path, self.grant_path, self.text_path))
        self.build()
        before = self.bundle_path.read_bytes()
        self.assert_error("inaccessible_resource", self.build)
        self.assertEqual(self.bundle_path.read_bytes(), before)
        self.assert_error("inaccessible_resource", lambda: LocalReferenceIndex.build(
            self.delivery_path, self.grant_path, Path(self.temp.name) / "public.json"))

    def test_conflicting_required_page_is_not_returned_as_valid_support(self):
        self.metadata[0]["conflicting_cohort_mentions"] = [{"page": 2}]
        self.write(self.requirements / "plans.json", self.metadata)
        self.requirements_manifest["files"]["plans.json"] = self.hash(self.requirements / "plans.json")
        self.write(self.requirements / "manifest.json", self.requirements_manifest)
        index = self.index()
        self.assertEqual(index.course_codes, [])
        self.assertEqual(index.search("SYN1001M")['matches'], [])

    def test_live_grant_is_checked_again_before_search_returns(self):
        index = self.index()
        original = index._view
        def revoke_after_candidate(chunk, live):
            result = original(chunk, live)
            self.write(self.grant_path, {**self.grant, "revoked": True})
            return result
        with patch.object(index, "_view", side_effect=revoke_after_candidate):
            self.assert_error("inaccessible_resource", lambda: index.search("SYN1001M"))

    def test_signed_reference_resolves_in_new_instance_and_rejects_modified_signature(self):
        first = self.index()
        reference = first.search("SYN1003M")['matches'][0]['reference']
        reopened = LocalReferenceIndex(self.delivery_path, self.grant_path, self.bundle_path)
        self.assertIn("SYN1003M", reopened.resolve(reference)['text'])
        replacement = 'A' if reference[-1] != 'A' else 'B'
        self.assert_error("inaccessible_resource", lambda: reopened.resolve(reference[:-1] + replacement))
        summary = reopened.describe()
        self.assertNotIn(first.bundle['signing_key'], json.dumps(summary))
        self.assertNotIn(str(self.base), json.dumps(reopened.authorize()))
        self.assertEqual(first.authorize(), reopened.authorize())

    def test_active_grant_changed_selector_requires_new_bundle(self):
        index = self.index()
        grant = deepcopy(self.grant)
        grant['curriculum_selector']['course_row_spans'].pop()
        self.write(self.grant_path, grant)
        self.assert_error('stale_input', lambda: index.course_codes)
        self.assert_error('stale_input', index.shared_components)

    def test_ungranted_documents_and_personal_files_are_never_read(self):
        original = Path.read_bytes
        forbidden = {'fictional-b.pdf', 'fictional-b.txt', 'restricted_personal_page_observations.json'}
        def guard(path):
            self.assertNotIn(path.name, forbidden)
            return original(path)
        with patch.object(Path, 'read_bytes', guard):
            index = self.index()
            index.search('SYN1001M')
            index.shared_components()
        self.assertEqual(index.bundle['registry'][1]['hash_verification'], 'inventory_hash_recorded_not_rechecked')

    def test_cold_self_consistent_bundle_cannot_replace_source_or_page_scope(self):
        self.build()
        original = self.bundle_path.read_bytes()
        from course_assistant.local_reference import _digest
        def replace_source(bundle):
            other = bundle['registry'][1]
            for key in ('pdf_path', 'text_path', 'pdf_sha256', 'text_sha256', 'page_count'):
                bundle['registry'][0][key] = other[key]
        for mutate in (replace_source,
                       lambda bundle: bundle['registry'][0]['page_states'].update({'2': 'conflicting'}),
                       lambda bundle: bundle['chunks'][0]['spans'][0].update(physical_page=3)):
            with self.subTest(mutate=mutate):
                bundle = json.loads(original)
                mutate(bundle)
                bundle['corpus_version'] = _digest({key: value for key, value in bundle.items() if key != 'corpus_version'})
                self.write(self.bundle_path, bundle)
                self.assert_error('stale_input', lambda: LocalReferenceIndex(self.delivery_path, self.grant_path, self.bundle_path))
        self.bundle_path.write_bytes(original)

    def test_mixed_activity_course_is_deferred_while_ordinary_labor_and_experiment_remain(self):
        self.pages[2] = self.pages[2].replace('虚构算法', '虚构第三课堂')
        text = ''.join('=== SOURCE PAGE ' + str(page) + ' ===\n' + value for page, value in self.pages.items())
        self.text_path.write_text(text, encoding='utf-8')
        self.metadata[0]['text_sha256'] = self.hash(self.text_path)
        self.write(self.requirements / 'plans.json', self.metadata)
        self.requirements_manifest['files']['plans.json'] = self.hash(self.requirements / 'plans.json')
        self.write(self.requirements / 'manifest.json', self.requirements_manifest)
        index = self.index()
        self.assertEqual(index.course_codes, ['SYN1002M', 'SYN1003M'])
        self.assertEqual(index.search('SYN1001M')['matches'], [])
        self.assertTrue(any('SYN1002M' in row['text'] for row in index.search('虚构劳动')['matches']))
        self.assertTrue(any('SYN1003M' in row['text'] for row in index.search('虚构实验')['matches']))

    def test_review_metadata_cannot_expose_private_paths_or_nonnumeric_pages(self):
        for changes in ({'assistant_review_id': str(self.base / 'private-note.md')},
                        {'assistant_reviewed_pages': {'EVAL-A': [str(self.text_path)]}},
                        {'allowed_pages': ['invalid']}):
            with self.subTest(changes=changes):
                self.write(self.grant_path, {**self.grant, **changes})
                self.assert_error('inaccessible_resource', self.build)
                self.assertFalse(self.bundle_path.exists())

    def test_grant_change_during_build_does_not_commit_old_scope(self):
        import course_assistant.local_reference as module
        original = module._make_chunks
        def shrink_after_chunks(*args):
            result = original(*args)
            self.write(self.grant_path, {**self.grant, 'allowed_pages': {'EVAL-A': [1]}})
            return result
        with patch.object(module, '_make_chunks', side_effect=shrink_after_chunks):
            self.assert_error('stale_input', self.build)
        self.assertFalse(self.bundle_path.exists())


if __name__ == "__main__":
    unittest.main()
