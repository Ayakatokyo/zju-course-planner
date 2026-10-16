#!/usr/bin/env python3
"""Run bounded interface acquisition through the official-browser connector.

The main application stays synthetic. This command never receives school
passwords, cookies or tickets; the connector uses the browser's own session.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_student_source_probe import (
    PrivateArgumentParser, ProbeCLIError, initialize_task, private_path,
    private_directory, read_private_json, output_private)
from course_assistant.student_sources import SourceTaskStore, StudentSourceError, digest, sanitize_payload
from course_assistant.student_source_parsers import parse_payload
from course_assistant.student_source_client import StudentSourceClient, SourceClientError, to_envelopes
from course_assistant.student_browser_bridge import BrowserBridgeTransport, BrowserBridgeServer, BrowserBridgeError
from course_assistant.student_plan_acquisition import OnDemandPlanService, StudentPlanAcquisitionError

PERSONAL_OPERATIONS = ("academic", "major_academic", "timetable", "exams")
STORED_FAILURES = frozenset({"awaiting_user_login", "source_session_expired", "source_subject_changed",
                           "source_unavailable", "permission_denied", "captcha_required", "schema_changed",
                           "user_confirmation_required"})


def source_profile(path):
    document = read_private_json(private_path(path))
    if (not isinstance(document, dict) or document.get("domain") != "profile"
            or document.get("source_origin") != "https://zdbk.zju.edu.cn"):
        raise ProbeCLIError("invalid_capture")
    payload, _ = sanitize_payload("profile", document.get("payload"))
    parsed = parse_payload("profile", payload, {})
    if parsed.get("status") == "schema_changed" or len(parsed.get("records", [])) != 1:
        raise ProbeCLIError("invalid_capture")
    return parsed["records"][0]


def sync_operation(client, store, attempt, operation, term, *, source_origin="https://zdbk.zju.edu.cn"):
    result = client.fetch(operation, target_term=term, force_refresh=True)
    if result["data_source"] == "live":
        with store.connect() as db:
            generation = store._authorize(db, attempt)["generation"]
        for envelope in to_envelopes(result, operation=operation, target_term=term,
                                     source_alias="BROWSER-HTTP-" + operation.upper().replace('_', '-'),
                                     source_origin=source_origin,
                                     generation=generation):
            store.ingest(attempt, envelope)
    else:
        domain = "academic" if operation == "major_academic" else operation
        failure = result["status"]
        store.fail_domain(attempt, domain, failure if failure in STORED_FAILURES else "source_unavailable")
    return {"operation": operation, "status": result["status"], "data_source": result["data_source"],
            "pages": len(result.get("pages", [])), "coverage": result.get("coverage"),
            "failure": result.get("failure")}


def plan_service(client, root, authorize):
    def capture(operation, **kwargs):
        result = client.fetch(operation, force_refresh=True, **kwargs)
        coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
        total, captured = coverage.get("reported_total"), coverage.get("captured_rows")
        if (operation == "plan_catalogue" and result.get("data_source") == "live"
                and result.get("pages") and result.get("failure") == "page_limit_exceeded"
                and type(total) is int and type(captured) is int and total > captured):
            # A bounded directory read cannot establish a unique candidate
            # while the source explicitly reports more scoped candidates.
            raise StudentPlanAcquisitionError("invalid_plan_catalogue")
        if result.get("failure") and result.get("data_source") == "live":
            raise StudentPlanAcquisitionError("schema_changed")
        if result["data_source"] != "live" or not result.get("pages"):
            raise StudentPlanAcquisitionError(result.get("failure") or result["status"])
        return result

    def catalogue(scope):
        result = capture("plan_catalogue", scope=scope)
        rows = []
        for page in result["pages"]:
            payload = page.get("payload") if isinstance(page, dict) else None
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise StudentPlanAcquisitionError("invalid_plan_catalogue")
            rows.extend(payload["items"])
        coverage = result.get("coverage")
        if not isinstance(coverage, dict):
            raise StudentPlanAcquisitionError("invalid_plan_catalogue")
        total = coverage.get("reported_total")
        if total is not None and (type(total) is not int or total != len(rows)):
            # A candidate cannot be unique while known scoped rows are omitted.
            raise StudentPlanAcquisitionError("invalid_plan_catalogue")
        # Unknown directory totals remain pending applicability review.
        return rows

    def document(ref):
        return capture("plan_document", plan_ref=ref)["pages"][0]["payload"]

    return OnDemandPlanService(root, authorize, catalogue, document)


def run(argv=None):
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="New or existing private batch directory")
    parser.add_argument("--term", required=True, help="Canonical source scope, e.g. 2026-2027-AW")
    parser.add_argument("--operations", default="academic")
    parser.add_argument("--plan-profile", help="Existing official profile observation in a private directory")
    parser.add_argument("--force-plan", action="store_true")
    parser.add_argument("--attempt", help="Resume an existing active task")
    parser.add_argument("--port", type=int, default=18976)
    parser.add_argument("--wait-seconds", type=int, default=60,
                        help="Operator pairing window, 1 to 300 seconds; HTTP request timeout remains 30 seconds")
    args = parser.parse_args(argv)
    operations = tuple(filter(None, args.operations.split(',')))
    if (any(operation not in PERSONAL_OPERATIONS for operation in operations)
            or len(set(operations)) != len(operations) or not 1 <= args.port <= 65535
            or not 1 <= args.wait_seconds <= 300 or not operations and not args.plan_profile):
        raise ProbeCLIError("invalid_arguments")
    if "academic" in operations and "major_academic" in operations:
        # They have distinct source semantics and need distinct three-page attempts.
        raise ProbeCLIError("invalid_arguments")
    from course_assistant.student_browser_bridge import validate_spec
    for operation in operations:
        validate_spec({"operation": operation, "target_term": args.term})
    batch = private_path(args.root)
    source_root, grant_path = batch / "source_task", batch / "source_grant.json"
    if not args.attempt:
        private_directory(batch)
        initialize_task(source_root, grant_path)
    store = SourceTaskStore(source_root, grant_path)
    attempt = args.attempt or store.create_attempt(args.term, "https://zdbk.zju.edu.cn")["attempt_id"]
    with store.connect() as db:
        row = store._authorize(db, attempt)
        if row["target_term"] != args.term:
            raise ProbeCLIError("source_scope_changed")
    profile = source_profile(args.plan_profile) if args.plan_profile else None
    allowed_operations = set(operations) | ({"plan_catalogue", "plan_document"} if profile else set())

    def authorize():
        with store.connect() as db:
            row = store._authorize(db, attempt)
            key = digest({"attempt": row["id"], "grant_digest": row["grant_digest"],
                          "operator": row["operator_ref"], "generation": row["generation"]})
        return {"scope_key": key, "target_term": args.term, "operations": sorted(allowed_operations)}

    transport = BrowserBridgeTransport(authorize)
    receipts = []
    with BrowserBridgeServer(transport, port=args.port) as server:
        print(json.dumps({"status": "awaiting_browser_connection", "broker_url": server.url,
                          "pairing_page": server.url + "/pair", "school_authentication": "not_configured"}), flush=True)
        if not transport.wait_connected(args.wait_seconds):
            print(json.dumps({"status": "browser_not_connected", "automatic_acquisition": "not_run"}), flush=True)
            return 2
        client = StudentSourceClient(transport, authorize, cache_seconds=0)
        for operation in operations:
            receipt = sync_operation(client, store, attempt, operation, args.term)
            receipts.append(receipt)
            print(json.dumps(receipt), flush=True)
            if receipt["status"] in {"source_session_expired", "source_subject_changed", "permission_denied",
                                      "awaiting_user_login", "captcha_required", "user_confirmation_required"}:
                break
        if profile and (not receipts or receipts[-1]["status"] not in {
                "source_session_expired", "source_subject_changed", "permission_denied",
                "awaiting_user_login", "captcha_required", "user_confirmation_required"}):
            def plan_authorize():
                result = authorize()
                if transport.session_epoch == "unpaired":
                    raise StudentPlanAcquisitionError("source_session_expired")
                return {**result, "scope_key": digest({"task": result["scope_key"],
                                                       "browser_epoch": transport.session_epoch})}
            service = plan_service(client, batch / "plans", plan_authorize)
            receipt = service.acquire(profile, force=args.force_plan)
            receipts.append({"operation": "plan", **receipt})
            print(json.dumps(receipts[-1]), flush=True)
        with store.connect() as db:
            store._authorize(db, attempt, allow_stopped=True)
        output_private(batch / ("sync_receipt_" + attempt + "_" + secrets.token_hex(6) + ".json"), json.dumps({
            "schema": "student-source-sync/1", "attempt_id": attempt,
            "security_realm": "restricted_personal_reference", "transport": "browser_http_connector",
            "receipts": receipts, "official_authentication": "not_configured",
            "student_plan_binding": "not_verified", "course_updates": "out_of_scope",
            "profile_current_session_verified": False if profile else None}, ensure_ascii=False, indent=2) + "\n")
    return 0 if receipts and all(receipt["status"] in {"complete_within_scope", "partial_acquisition", "acquired_pending_review"} for receipt in receipts) else 2


def main(argv=None):
    try:
        return run(argv)
    except (ProbeCLIError, StudentSourceError, SourceClientError, BrowserBridgeError, StudentPlanAcquisitionError) as error:
        print(json.dumps({"status": "stopped", "error": error.code}), flush=True)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "stopped", "error": "sync_unavailable"}), flush=True)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped", "error": "interrupted"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
