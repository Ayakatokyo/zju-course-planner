"""Synthetic semantic regressions for the independent offline merger."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from scripts.deduplicate_courses import build_dataset, publish_local, section_identity, summarize_fields


def course(oid="c1", code="TEST101", **fields):
    return {"id": oid, "code": code, "term": "2026-2027-1", "fields": {"name": "合成课", "credits": "2.0", **fields},
            "source": {"route": "synthetic_test", "observed_at": "2026-10-13T01:00:00Z"},
            "context": {"classification": "synthetic"}, "classification": "synthetic", "personal": {"selected": "0"}}


def section(oid="s1", code="TEST101", tail="001-1", **patch):
    return {"id": oid, "code": code, "term": "2026-2027-1", "source_key": f"(2026-2027-1)-{code}-{tail}",
            "fields": {"teacher_names": "合成教师", "international": False},
            "meetings": [{"time_raw": "周一第1,2节", "place_raw": "合成地点", "week_set": None}],
            "exam": {"raw": "待定", "status": "pending", "date": None},
            "capacity": {"raw": "0/20", "remaining": 0, "capacity": 20}, "personal": {"selected": "0"},
            "source": {"route": "synthetic_test", "observed_at": "2026-10-13T01:00:00Z"}, **patch}


class DedupSemantics(unittest.TestCase):
    def test_unknown_does_not_overwrite_zero_false_or_empty_collection(self):
        result = summarize_fields([{"id": "a", "fields": {"zero": 0, "flag": False, "empty": []}},
                                   {"id": "b", "fields": {"zero": None, "flag": "--", "empty": None}}], ["zero", "flag", "empty"])
        self.assertEqual([result[x]["value"] for x in ["zero", "flag", "empty"]], [0, False, []])
        self.assertTrue(all(v["state"] == "known" for v in result.values()))

    def test_conflicting_values_remain_unresolved_with_all_sources(self):
        d = build_dataset({"course_observations": [course(), course("c2", name="合成异名")], "section_observations": []})
        name = d["courses"][0]["definitions"][0]["fields"]["name"]
        self.assertEqual(name["state"], "conflicting")
        self.assertIsNone(name["value"])
        self.assertEqual(len(name["assertions"]), 2)

    def test_decimal_compatibility_is_exact(self):
        d = build_dataset({"course_observations": [course(), course("c2", credits=2)], "section_observations": []})
        credit = d["courses"][0]["definitions"][0]["fields"]["credits"]
        self.assertEqual(credit["state"], "known")
        self.assertEqual(credit["value"], "2")

    def test_same_name_different_code_never_merges(self):
        d = build_dataset({"course_observations": [course(), course("c2", "TEST102")], "section_observations": []})
        self.assertEqual(len(d["courses"]), 2)

    def test_different_class_and_term_remain_separate(self):
        s2 = section("s2", tail="001-2")
        s3 = section("s3", term="2027-2028-1", source_key="(2027-2028-1)-TEST101-001-1")
        d = build_dataset({"course_observations": [course()], "section_observations": [section(), s2, s3]})
        self.assertEqual(len(d["sections"]), 3)

    def test_multiple_meetings_and_time_dependent_snapshots_are_not_stitched(self):
        s1 = section()
        s1["meetings"].append({"time_raw": "周三第5节", "place_raw": None, "week_set": None})
        s2 = section("s2", meetings=[{"time_raw": "周二第3节", "place_raw": "别处", "week_set": None}],
                     exam={"raw": "2027-01-01", "status": "known_local", "date": "2027-01-01"},
                     capacity={"raw": "-1/20", "remaining": -1, "capacity": 20},
                     source={"route": "synthetic_test", "observed_at": "2026-10-13T02:00:00Z"})
        d = build_dataset({"course_observations": [course()], "section_observations": [s1, s2]})
        self.assertEqual(len(d["sections"]), 1)
        self.assertEqual(len(d["meetings"]), 3)
        self.assertEqual(len(d["sections"][0]["meeting_variants"]), 2)
        self.assertEqual([x["fields"]["remaining"] for x in d["capacity_snapshots"]], [0, -1])
        self.assertTrue(d["reconciliation"]["checks"]["dynamic_values_preserved_without_cross_time_stitching"])

    def test_unknown_identity_never_matches_another_unknown(self):
        a, b = section(source_key=None), section("s2", source_key=None)
        self.assertNotEqual(section_identity(a), section_identity(b))

    def test_verified_empty_meetings_are_separate_from_unknown(self):
        d = build_dataset({"course_observations": [course()], "section_observations": [
            section(meetings=[], meeting_state="known_empty"), section("s2", meetings=None)]})
        self.assertEqual([x["status"] for x in d["meeting_snapshots"]], ["known_empty", "unknown_not_confirmed_empty"])
        self.assertEqual(len(d["sections"][0]["meeting_variants"]), 2)

    def test_parent_binding_conflicts_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_key_course_conflict"):
            section_identity(section(source_key="(2026-2027-1)-TEST999-001-1"))
        with self.assertRaisesRegex(ValueError, "source_key_term_conflict"):
            section_identity(section(term="2025-2026-1"))

    def test_personal_fields_excluded_from_course_and_section_projection(self):
        d = build_dataset({"course_observations": [course()], "section_observations": [section()]})
        for key in ("courses", "course_observations", "sections", "section_observations"):
            self.assertNotIn("selected", str(d[key]))
        self.assertIn("selected", str(d["restricted_personal_page_observations"]))

    def test_scope_exclusion_preserves_observation_and_source(self):
        excluded = course("excluded", "TEST999")
        excluded["excluded_reason"] = "user_excluded_scope"
        d = build_dataset({"course_observations": [course(), excluded], "section_observations": []})
        self.assertEqual(len(d["courses"]), 1)
        self.assertEqual(d["excluded_observations"], [excluded])
        self.assertEqual(len(d["restricted_source_registry"]), 2)

    def test_adapter_cannot_leak_personal_flag_or_raw_credit_encoding(self):
        with self.assertRaisesRegex(ValueError, "personal_field_in_shared_projection"):
            build_dataset({"course_observations": [course(kcxx="1~2~2-0")], "section_observations": []})

    def test_partial_context_does_not_claim_equivalence_to_known_filter(self):
        a, b = course(), course("c2")
        b["context"] = {"classification": "synthetic", "plan": "known_synthetic_plan"}
        d = build_dataset({"course_observations": [a, b], "section_observations": []})
        self.assertEqual(len(d["classification_memberships"]), 2)

    def test_duplicate_observation_id_must_have_identical_content(self):
        a, b = course(), course(name="changed")
        with self.assertRaisesRegex(ValueError, "conflicting_observation_id"):
            build_dataset({"course_observations": [a, b], "section_observations": []})

    def test_idempotent_files_and_input_order_invariance(self):
        inputs = {"course_observations": [course(), course("c2")], "section_observations": [section(), section("s2")]}
        with tempfile.TemporaryDirectory() as tmp:
            first, m1 = publish_local(inputs, Path(tmp))
            before = {p.name: p.read_bytes() for p in first.iterdir()}
            reverse = deepcopy(inputs)
            reverse["course_observations"].reverse()
            reverse["section_observations"].reverse()
            second, m2 = publish_local(reverse, Path(tmp))
            self.assertEqual(first, second)
            self.assertEqual(m1, m2)
            self.assertEqual(before, {p.name: p.read_bytes() for p in second.iterdir()})


if __name__ == "__main__":
    unittest.main()
