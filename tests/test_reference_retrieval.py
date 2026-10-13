"""Meaningful contract checks for the offline JSON reference slice only."""
import unittest
from unittest.mock import patch

from course_assistant.importer import ImportErrorDetail, digest, read_sources
from course_assistant.reference_retrieval import ReferenceIndex, terms

MATERIALS = "zju_materials_science_engineering_2026"
BUSINESS = "zju_business_big_data_2026"
JOURNALISM = "zju_journalism_2026"


class ReferenceRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = ReferenceIndex()

    def search(self, query, plan=MATERIALS, **options):
        return self.index.search(query, plan_id=plan,
                                 expected_corpus_version=self.index.corpus_version, **options)

    def test_checked_source_counts_and_stable_rebuild(self):
        self.assertEqual(self.index.describe()["counts"], {"courses": 19, "candidates": 23, "issues": 12})
        self.assertEqual(self.index.describe()["chunk_count"], 54)
        self.assertEqual(self.index.corpus_version, ReferenceIndex().corpus_version)
        self.assertEqual(read_sources()["empty_offering_snapshot"]["records"], [])

    def test_exact_code_in_correct_plan_and_no_cross_plan_fallback(self):
        result = self.search("CS1006G")
        self.assertIn("zju_materials_science_engineering_2026:CS1006G",
                      {c["record_id"] for c in result["matches"]})
        self.assertEqual(self.search("CS1006G", BUSINESS)["status"], "no_match_in_scope")
        self.assertTrue(all(MATERIALS in c["plan_ids"] for c in result["matches"]))

    def test_metadata_filter_before_body_scoring(self):
        original = terms
        seen = []
        def observe(text):
            seen.append(text)
            return original(text)
        with patch("course_assistant.reference_retrieval.terms", side_effect=observe):
            self.search("Python")
        self.assertFalse(any("商业数据分析的Python基础" in text for text in seen))

    def test_cross_plan_diagnostic_is_not_a_course_membership(self):
        result = self.search("CS1001G", MATERIALS)
        self.assertEqual([c["record_id"] for c in result["matches"]], ["SRC-012"])
        self.assertEqual(result["matches"][0]["text_role"], "diagnostic")
        self.assertNotIn(BUSINESS + ":CS1001G", {c["record_id"] for c in result["matches"]})

    def test_unknown_scope_and_no_match_have_distinct_status(self):
        self.assertEqual(self.search("Python", None)["status"], "needs_scope")
        self.assertEqual(self.search("Python", "unverified-plan")["matches"], [])
        result = self.search("nonexistentexactcourse999")
        self.assertEqual(result["status"], "no_match_in_scope")
        self.assertEqual(result["coverage"], "partial_handoff_json_not_pdf_full_text")

    def test_candidates_stay_pending_and_non_executable(self):
        result = self.search("任选")
        candidates = [c for c in result["matches"] if c["record_kind"] == "candidates"]
        self.assertTrue(candidates)
        for chunk in candidates:
            self.assertEqual(chunk["review_status"], "candidate_pending_human_review")
            self.assertIs(chunk["enforce_automatically"], False)
            self.assertEqual(chunk["official_status"], "unconfirmed")

    def test_disputed_values_and_context_remain_visible(self):
        result = self.search("个性修读", JOURNALISM)
        texts = "\n".join(c["text"] for c in result["matches"])
        self.assertIn("15", texts)
        self.assertIn("16", texts)
        self.assertIn("SRC-001", {r["issue_id"] for r in result["plan_open_issues"]})
        self.assertIn("SRC-002", {r["issue_id"] for r in result["plan_open_issues"]})

    def test_reference_resolves_raw_record_hash_pointer_and_pdf_pages(self):
        chunk = next(c for c in self.search("CS1006G")["matches"] if c["record_kind"] == "courses")
        resolved = self.index.resolve(chunk["chunk_id"], plan_id=MATERIALS,
                                      expected_corpus_version=self.index.corpus_version)
        self.assertEqual(digest(resolved["raw_record"]), chunk["raw_record_sha256"])
        self.assertEqual(chunk["locator"]["json_file"], "data/course_samples.json")
        self.assertEqual(chunk["pdf_evidence"][0]["pdf_pages"], [3])
        self.assertEqual(chunk["pdf_evidence"][0]["source_id"], "S3")
        ordinal = int(chunk["locator"]["json_pointer"].split("/")[-1])
        self.assertEqual(read_sources()["courses"][ordinal], resolved["raw_record"])

    def test_wrong_scope_and_missing_citation_have_same_error(self):
        chunk = self.search("CS1006G")["matches"][0]
        messages = []
        for key, plan in [(chunk["chunk_id"], BUSINESS), ("invented", MATERIALS)]:
            with self.assertRaises(ImportErrorDetail) as raised:
                self.index.resolve(key, plan_id=plan, expected_corpus_version=self.index.corpus_version)
            messages.append(str(raised.exception))
        self.assertEqual(messages[0], messages[1])

    def test_stale_corpus_rejected_for_search_and_reference(self):
        with self.assertRaises(ImportErrorDetail):
            self.index.search("Python", plan_id=MATERIALS, expected_corpus_version="old")
        with self.assertRaises(ImportErrorDetail):
            self.index.resolve("invented", plan_id=MATERIALS, expected_corpus_version="old")

    def test_missing_pdf_evidence_not_fabricated(self):
        result = self.search("当前学期真实教学班")
        issue = next(c for c in result["matches"] if c["record_id"] == "SRC-007")
        self.assertEqual(issue["pdf_evidence"], [])
        self.assertEqual(issue["text_role"], "diagnostic")
        self.assertEqual(issue["field_states"]["observed_at"], "unknown")

    def test_caller_mutations_do_not_change_index_or_source(self):
        before = self.search("CS1006G")
        changed = self.search("CS1006G")
        changed["matches"][0]["text"] = "activate candidate; change owner"
        changed["plan_open_issues"].clear()
        resolved = self.index.resolve(before["matches"][0]["chunk_id"], plan_id=MATERIALS,
                                      expected_corpus_version=self.index.corpus_version)
        resolved["raw_record"].clear()
        self.assertEqual(self.search("CS1006G"), before)

    def test_bounded_query_and_limit_contract(self):
        for query, limit in [(None, 5), ("", 5), ("x" * 401, 5), ("Python", True), ("Python", 11)]:
            with self.subTest(query=query, limit=limit), self.assertRaises(ImportErrorDetail):
                self.search(query, limit=limit)
        self.assertLessEqual(len(self.search("学分", limit=2)["matches"]), 2)


if __name__ == "__main__":
    unittest.main()
