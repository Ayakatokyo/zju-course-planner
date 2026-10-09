"""Synthetic ingestion checks before any per-user workspace is persisted."""
from copy import deepcopy
import unittest

from course_assistant.importer import ImportErrorDetail, load_json, read_sources, validate_demo


class ImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = read_sources()
        cls.demo = load_json("demo/bundle.json")

    def validate(self, mutate):
        bundle = deepcopy(self.demo)
        mutate(bundle)
        return validate_demo(bundle, self.source)

    def test_negative_remaining_seats_are_preserved_as_integers(self):
        result = self.validate(lambda b: b["offering_snapshot"]["records"][0].update(available_seats=-10))
        self.assertEqual(result["offering_snapshot"]["records"][0]["available_seats"], -10)

    def test_capacity_and_pending_counts_are_nonnegative_integers(self):
        for key, value in [("capacity", -1), ("capacity", 1.5), ("capacity", True),
                           ("available_seats", -1.5), ("available_seats", "-10"),
                           ("major_pending_count", -1), ("all_pending_count", 0.5)]:
            with self.subTest(key=key, value=value), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b["offering_snapshot"]["records"][0].update({key: value}))

    def test_decimal_text_is_preserved_and_nonfinite_forms_rejected(self):
        text = "0.123456789012345678901234567890123456789"
        result = self.validate(lambda b: b["academic_snapshot"]["records"][0].update(earned_credits=text))
        self.assertEqual(result["academic_snapshot"]["records"][0]["earned_credits"], text)
        for value in ["NaN", "Infinity", "1_000", True, "-0.1", "bad", float("inf")]:
            with self.subTest(value=value), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b["academic_snapshot"]["records"][0].update(earned_credits=value))

    def test_nested_real_data_kind_cannot_hide_under_synthetic_metadata(self):
        for location in ["academic_calendar", "constraints", "academic_snapshot"]:
            with self.subTest(location=location), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b[location].update(nested={"data_kind": "runtime_observation"}))

    def test_credentials_identity_and_permission_claims_are_rejected_recursively(self):
        for key in ["school_password", "access_token", "Cookie", "session_id", "student_number",
                    "student_id", "owner_student_id", "auth_mode", "roles", "source_path"]:
            with self.subTest(key=key), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b["constraints"].update(nested={key: "untrusted"}))

    def test_synthetic_student_requires_anonymous_key_and_matching_references(self):
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["student"].update(student_key="202300000001"))
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["wish_items"][1].update(student_key="DEMO-OTHER"))
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["student"].update(name="unverified real name"))

    def test_only_immutable_checked_public_course_definitions_are_allowed(self):
        result = validate_demo(deepcopy(self.demo), self.source)
        self.assertEqual(result["courses"][0]["data_kind"], "curriculum_course_sample_not_current_offering")
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["courses"][0].update(credits=999))
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["courses"][0].update(personal_record={"grade": 90}))

    def test_calendar_must_be_explicitly_synthetic(self):
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["academic_calendar"].pop("data_kind"))

    def test_planning_and_export_calendar_must_be_the_same_version(self):
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["academic_calendar"]["period_times"].update({"1": ["12:00", "12:45"]}))
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b.pop("academic_calendar"))

    def test_replaced_rows_and_unknown_attempts_can_be_preserved_for_audit(self):
        result = self.validate(lambda b: b["academic_snapshot"]["records"][0].update(
            completion_status="replaced", attempt_id=None, earned_credits=None))
        self.assertEqual(result["academic_snapshot"]["records"][0]["completion_status"], "replaced")

    def test_fractional_course_count_threshold_is_rejected(self):
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["rules"][0].update(metric="course_count", threshold="1.5"))

    def test_personal_file_references_and_bad_public_hashes_are_rejected(self):
        for reference in [{"file_name": "personal-student.xlsx"},
                          {"file_name": "document-2.pdf", "sha256": "forged"}]:
            with self.subTest(reference=reference), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b["constraints"].update(source_refs=[reference]))

    def test_malformed_records_are_validation_errors(self):
        for key in ["academic_snapshot", "offering_snapshot", "constraints"]:
            with self.subTest(key=key), self.assertRaises(ImportErrorDetail):
                self.validate(lambda b: b.update({key: []}))
        with self.assertRaises(ImportErrorDetail):
            self.validate(lambda b: b["academic_snapshot"]["records"].append("not a record"))


if __name__ == "__main__":
    unittest.main()
