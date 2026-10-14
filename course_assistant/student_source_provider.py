"""Disabled school provider and synthetic-only verification contract tests.

This module performs no network, configuration, credential, or session I/O.  Its
fixture validator models checks a future approved protocol must implement; it
does not verify signatures/tickets or create a principal, binding, or AuthContext.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
import time


class ProviderContractError(ValueError):
    """A fixed reason code; upstream values never enter exception text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class DisabledSchoolProvider:
    """No supplied receipt or fixture can enable formal school authentication."""

    def status(self):
        return {
            "provider_kind": "school_official",
            "enabled": False,
            "school_auth_status": "not_configured",
            "can_issue_auth_context": False,
            "reason": "official_provider_not_configured",
            "required_conditions": [
                "approved_project_application",
                "verified_protocol_and_server_verification_material",
                "registered_project_callback",
                "expected_issuer_and_project_audience",
                "trusted_subject_to_student_mapping",
                "separate_personal_source_permission",
            ],
        }

    def authenticate(self):
        raise ProviderContractError("official_provider_not_configured")


@dataclass(frozen=True)
class SyntheticAuthRequest:
    request_ref: str
    created_at: float
    expires_at: float
    mapping_version: str
    synthetic: bool = True


@dataclass(frozen=True)
class SyntheticAuthReceipt:
    issuer: str
    audience: str
    request_ref: str
    receipt_ref: str
    subject: str
    issued_at: float
    expires_at: float
    synthetic: bool = True


@dataclass(frozen=True)
class SyntheticTrustedMapping:
    subject: str
    student_alias: str
    version: str
    status: str = "verified_synthetic_fixture"
    synthetic: bool = True


def _fixture_id(value):
    return isinstance(value, str) and re.fullmatch(r"SYNTHETIC-[A-Za-z0-9_.-]{1,100}", value) is not None


def _timestamp(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class SyntheticReceiptValidator:
    """In-memory fixture checks only; replay state is not production persistence.

    Expected issuer/audience and mappings are supplied by the test harness, not
    receipt claims. A successful check yields no owner/student ID or credentials.
    This deliberately has no hook into DisabledSchoolProvider or AuthManager.
    """

    def __init__(self, *, issuer, audience, trusted_mappings, clock=time.time,
                 max_age_seconds=120):
        if (not isinstance(issuer, str) or
                re.fullmatch(r"synthetic://[A-Za-z0-9_.-]{1,100}", issuer) is None or
                not _fixture_id(audience)):
            raise ProviderContractError("synthetic_policy_required")
        if not _timestamp(max_age_seconds) or not 0 < max_age_seconds <= 3600:
            raise ProviderContractError("synthetic_policy_invalid")
        mappings = tuple(trusted_mappings)
        for row in mappings:
            if (not isinstance(row, SyntheticTrustedMapping) or row.synthetic is not True or
                    not all(_fixture_id(value) for value in (row.subject, row.student_alias, row.version))):
                raise ProviderContractError("synthetic_mapping_required")
        self.issuer, self.audience = issuer, audience
        self.mappings, self.clock, self.max_age = mappings, clock, max_age_seconds
        self._receipts, self._requests, self._lock = set(), set(), threading.Lock()

    def validate(self, receipt, request):
        if (not isinstance(receipt, SyntheticAuthReceipt) or receipt.synthetic is not True or
                not isinstance(request, SyntheticAuthRequest) or request.synthetic is not True):
            raise ProviderContractError("synthetic_receipt_required")
        if not all(_fixture_id(value) for value in (
                receipt.request_ref, receipt.receipt_ref, receipt.subject,
                request.request_ref, request.mapping_version)):
            raise ProviderContractError("synthetic_identifier_required")
        if receipt.issuer != self.issuer:
            raise ProviderContractError("issuer_mismatch")
        if receipt.audience != self.audience:
            raise ProviderContractError("audience_mismatch")
        if receipt.request_ref != request.request_ref:
            raise ProviderContractError("request_mismatch")
        now = self.clock()
        if not all(_timestamp(value) for value in (
                now, request.created_at, request.expires_at, receipt.issued_at, receipt.expires_at)):
            raise ProviderContractError("invalid_time")
        if not request.created_at <= now < request.expires_at:
            raise ProviderContractError("request_outside_validity")
        if not (request.created_at <= receipt.issued_at <= now < receipt.expires_at and
                now - receipt.issued_at <= self.max_age and
                receipt.expires_at - receipt.issued_at <= self.max_age):
            raise ProviderContractError("receipt_outside_validity")
        mappings = [row for row in self.mappings if row.subject == receipt.subject]
        if len(mappings) != 1:
            raise ProviderContractError("mapping_missing_or_conflicting")
        mapping = mappings[0]
        if mapping.status != "verified_synthetic_fixture" or mapping.version != request.mapping_version:
            raise ProviderContractError("mapping_unavailable")
        receipt_key = (receipt.issuer, receipt.receipt_ref)
        with self._lock:
            if receipt_key in self._receipts or request.request_ref in self._requests:
                raise ProviderContractError("receipt_or_request_replayed")
            self._receipts.add(receipt_key)
            self._requests.add(request.request_ref)
        return {
            "contract_status": "synthetic_contract_pass",
            "data_kind": "synthetic",
            "school_auth_status": "not_configured",
            "authenticated": False,
            "binding_status": "not_created",
            "can_issue_auth_context": False,
        }
