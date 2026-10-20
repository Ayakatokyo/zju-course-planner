"""Synthetic code-field fixtures; no school access or formal identity assertions."""
from copy import deepcopy
import unittest

from course_assistant.course_code_links import observed_code_links


TERM = "2099-2100-1"


def fixture(display="SYN1001M（99123450）", *, code="SYN1001M", state="known", term=TERM):
    return {"code": code, "course_name": "同名不构成身份依据", "private_locator": "/PRIVATE/SENTINEL",
            "stable_course_identity_verified": False, "definitions": [{"term": term, "fields": {
                "name": {"state": "known", "value": "同名不构成身份依据"},
                "display_code": {"state": state, "value": display,
                                 "assertions": [{"value": display, "observation_ids": ["PRIVATE-ID"]}]}}}]}


class CourseCodeLinkTests(unittest.TestCase):
    def test_direct_pair_returns_only_observed_relation_without_mutating_source(self):
        course = fixture()
        before = deepcopy(course)
        result = observed_code_links(course, TERM)
        self.assertEqual(result, [{"code": "99123450", "relation_kind": "same_source_header",
                                  "verification_status": "observed", "source_field": "display_code"}])
        self.assertEqual(course, before)
        result[0]["code"] = "CHANGED"
        self.assertEqual(observed_code_links(course, TERM)[0]["code"], "99123450")
        self.assertEqual(observed_code_links(fixture("SYN1001M(99123450)"), TERM)[0]["code"], "99123450")

    def test_numeric_primary_and_same_name_do_not_invent_a_pair(self):
        self.assertEqual(observed_code_links(fixture("99123450", code="99123450"), TERM), [])
        self.assertEqual(observed_code_links(fixture("99123450（99123450）", code="99123450"), TERM), [])
        course = fixture()
        del course["definitions"][0]["fields"]["display_code"]
        self.assertEqual(observed_code_links(course, TERM), [])

    def test_only_exact_source_term_is_used(self):
        course = fixture(term="2098-2099-1")
        course["definitions"].append(fixture()["definitions"][0])
        course["definitions"][0]["fields"]["display_code"]["value"] = "SYN1001M（99111110）"
        self.assertEqual(observed_code_links(course, TERM)[0]["code"], "99123450")
        self.assertEqual(observed_code_links(fixture(), "2099-2100-2"), [])
        for term in (None, "2099-2100-AW", "2099-2101-1", TERM + "\n"):
            with self.subTest(term=term):
                self.assertEqual(observed_code_links(course, term), [])

    def test_unknown_conflicting_and_inconsistent_known_assertions_stay_unlinked(self):
        for state in ("unknown", "conflicting", None):
            with self.subTest(state=state):
                self.assertEqual(observed_code_links(fixture(state=state), TERM), [])
        course = fixture()
        course["definitions"][0]["fields"]["display_code"]["assertions"].append(
            {"value": "SYN1001M（99111110）"})
        self.assertEqual(observed_code_links(course, TERM), [])

    def test_whole_field_pair_rejects_mismatch_ambiguity_and_untrusted_text(self):
        values = ("OTHER1001M（99123450）", "SYN1001M（SYN1001M）", "SYN1001M（syn1001m）", "SYN1001M（99123450）（99111110）",
                  "SYN1001M（99123450/99111110）", "SYN1001M（99123450)", " SYN1001M（99123450）",
                  "SYN1001M（99123450）\n", "SYN1001M（9912\x0050）", "SYN1001M（https://example.invalid）",
                  "SYN1001M（/Users/PRIVATE）", "SYN1001M（123456789012345678）", "SYN1001M（12345678901）",
                  "SYN1001M（TOKEN123456）")
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(observed_code_links(fixture(value), TERM), [])

    def test_duplicate_pair_deduplicates_but_disagreeing_definitions_do_not_choose(self):
        course = fixture()
        course["definitions"].append(deepcopy(course["definitions"][0]))
        self.assertEqual(len(observed_code_links(course, TERM)), 1)
        course["definitions"].append(fixture("SYN1001M（99111110）")["definitions"][0])
        self.assertEqual(observed_code_links(course, TERM), [])

    def test_invalid_course_shapes_do_not_raise_or_read_other_fields(self):
        for value in (None, [], {}, {"code": "123456789012345678", "definitions": []},
                      {"code": "SYN1001M", "definitions": None}):
            with self.subTest(value=value):
                self.assertEqual(observed_code_links(value, TERM), [])


if __name__ == "__main__":
    unittest.main()
