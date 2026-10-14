from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest

from course_assistant.student_source_provider import (
    DisabledSchoolProvider, ProviderContractError, SyntheticAuthReceipt,
    SyntheticAuthRequest, SyntheticReceiptValidator, SyntheticTrustedMapping,
)


class StudentSourceProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.mapping = SyntheticTrustedMapping(
            "SYNTHETIC-SUBJECT-A", "SYNTHETIC-STUDENT-A", "SYNTHETIC-MAPPING-v1")
        self.request = SyntheticAuthRequest("SYNTHETIC-REQUEST-A", 990, 1100, self.mapping.version)
        self.receipt = SyntheticAuthReceipt(
            "synthetic://school-provider", "SYNTHETIC-COURSE-ASSISTANT",
            self.request.request_ref, "SYNTHETIC-RECEIPT-A", self.mapping.subject, 995, 1090)
        self.validator = self.make_validator()

    def make_validator(self, mappings=None, **kwargs):
        return SyntheticReceiptValidator(
            issuer=self.receipt.issuer, audience=self.receipt.audience,
            trusted_mappings=[self.mapping] if mappings is None else mappings,
            clock=lambda: self.now, **kwargs)

    def denied(self, reason, receipt=None, request=None, validator=None):
        with self.assertRaises(ProviderContractError) as raised:
            (validator or self.validator).validate(receipt or self.receipt, request or self.request)
        self.assertEqual(raised.exception.reason, reason)
        self.assertEqual(str(raised.exception), reason)

    def test_provider_remains_disabled_before_and_after_fixture_pass(self):
        provider = DisabledSchoolProvider()
        before = provider.status()
        result = self.validator.validate(self.receipt, self.request)
        self.assertEqual(result["contract_status"], "synthetic_contract_pass")
        self.assertEqual(result["data_kind"], "synthetic")
        self.assertFalse(result["authenticated"])
        self.assertFalse(result["can_issue_auth_context"])
        self.assertEqual(result["binding_status"], "not_created")
        self.assertNotIn("student_id", result)
        self.assertEqual(provider.status(), before)
        self.assertFalse(before["enabled"])
        with self.assertRaises(ProviderContractError) as raised:
            provider.authenticate()
        self.assertEqual(raised.exception.reason, "official_provider_not_configured")

    def test_real_receipts_requests_mappings_and_policy_rejected(self):
        self.denied("synthetic_receipt_required", replace(self.receipt, synthetic=False))
        self.denied("synthetic_receipt_required", request=replace(self.request, synthetic=False))
        self.denied("synthetic_identifier_required", replace(self.receipt, subject="real-student"))
        for mapping in (replace(self.mapping, synthetic=False), replace(self.mapping, student_alias="real-student")):
            with self.subTest(mapping=mapping), self.assertRaises(ProviderContractError):
                self.make_validator([mapping])
        for issuer, audience in (("https://official.example", self.receipt.audience),
                                 (self.receipt.issuer, "real-client")):
            with self.subTest(issuer=issuer), self.assertRaises(ProviderContractError):
                SyntheticReceiptValidator(issuer=issuer, audience=audience, trusted_mappings=[])

    def test_exact_issuer_and_project_audience_required(self):
        self.denied("issuer_mismatch", replace(self.receipt, issuer="synthetic://other-provider"))
        self.denied("audience_mismatch", replace(self.receipt, audience="SYNTHETIC-OTHER-SERVICE"))

    def test_request_correlation_and_issue_after_request_required(self):
        self.denied("request_mismatch", replace(self.receipt, request_ref="SYNTHETIC-OTHER-REQUEST"))
        self.denied("receipt_outside_validity", replace(self.receipt, issued_at=989))

    def test_receipt_and_request_time_boundaries(self):
        for receipt in (replace(self.receipt, issued_at=1001), replace(self.receipt, expires_at=1000),
                        replace(self.receipt, expires_at=1200), replace(self.receipt, issued_at=900)):
            with self.subTest(receipt=receipt):
                self.denied("receipt_outside_validity", receipt)
        for request in (replace(self.request, created_at=1001), replace(self.request, expires_at=1000)):
            with self.subTest(request=request):
                self.denied("request_outside_validity", request=request)

    def test_nonfinite_boolean_and_invalid_policy_times_rejected(self):
        for value in (float("nan"), float("inf"), 10 ** 400, True, "995"):
            with self.subTest(value=value):
                self.denied("invalid_time", replace(self.receipt, issued_at=value))
        for value in (0, -1, float("nan"), True, 3601):
            with self.subTest(value=value), self.assertRaises(ProviderContractError):
                self.make_validator(max_age_seconds=value)

    def test_replay_rejects_same_receipt_and_new_receipt_for_used_request(self):
        self.validator.validate(self.receipt, self.request)
        self.denied("receipt_or_request_replayed")
        self.denied("receipt_or_request_replayed", replace(self.receipt, receipt_ref="SYNTHETIC-RECEIPT-B"))
        second_request = replace(self.request, request_ref="SYNTHETIC-REQUEST-B")
        self.denied("receipt_or_request_replayed",
                    replace(self.receipt, request_ref=second_request.request_ref), second_request)

    def test_concurrent_same_receipt_is_consumed_once(self):
        def check(_):
            try:
                return self.validator.validate(self.receipt, self.request)["contract_status"]
            except ProviderContractError as error:
                return error.reason
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(check, range(4)))
        self.assertEqual(results.count("synthetic_contract_pass"), 1)
        self.assertEqual(results.count("receipt_or_request_replayed"), 3)

    def test_mapping_must_be_unique_current_and_active(self):
        for mappings in ([], [self.mapping, self.mapping]):
            with self.subTest(mappings=mappings):
                self.denied("mapping_missing_or_conflicting", validator=self.make_validator(mappings))
        for mapping in (replace(self.mapping, status="pending"), replace(self.mapping, status="revoked"),
                        replace(self.mapping, status="verified"), replace(self.mapping, version="SYNTHETIC-MAPPING-v2")):
            with self.subTest(mapping=mapping):
                self.denied("mapping_unavailable", validator=self.make_validator([mapping]))

    def test_failed_check_does_not_consume_the_valid_fixture_request(self):
        self.denied("audience_mismatch", replace(self.receipt, audience="SYNTHETIC-WRONG"))
        self.assertEqual(self.validator.validate(self.receipt, self.request)["contract_status"],
                         "synthetic_contract_pass")

    def test_error_does_not_echo_upstream_values(self):
        untrusted_value = "untrusted-upstream-value"
        receipt = replace(self.receipt, issuer=untrusted_value)
        with self.assertRaises(ProviderContractError) as raised:
            self.validator.validate(receipt, self.request)
        self.assertNotIn(untrusted_value, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
