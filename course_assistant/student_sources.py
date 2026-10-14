"""Isolated local student-source review tasks. No network or application identity.

The grant permits local observation review, never school authentication. Only
credential-stripped business projections are persisted, in an independent store.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from .student_source_parsers import BUSINESS_FIELDS, PARSER_VERSION, MAPPING_VERSION, parse_payload

REALM = "restricted_personal_reference"
SCHEMA = "student-acquisition/1"
PURPOSE = "local_student_source_review"
DOMAINS = ("profile", "academic", "timetable", "exams")
STOP_STATES = {"source_session_expired", "source_subject_changed", "permission_denied",
               "captcha_required", "awaiting_user_login", "user_confirmation_required"}
FAIL_STATES = STOP_STATES | {"source_unavailable", "schema_changed", "incomplete_capture"}
ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
CREDENTIAL_VALUE = re.compile(r"(?i)(?:bearer\s+|(?:^|[?&;\s])(?:ticket|token|password|passwd|jsessionid|sessionid|cookie)\s*=|(?:cookie|set-cookie|authorization)\s*:|-----BEGIN .*PRIVATE KEY)")
SUBJECT_FIELDS = {"source_subject_id", "student_number", "xh", "xhm", "学号"}


class StudentSourceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def fail(code):
    raise StudentSourceError(code)


def digest(value):
    try:
        return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        fail("invalid_capture")


def _alias(value):
    if not isinstance(value, str) or not ALIAS.fullmatch(value):
        fail("invalid_alias")
    return value


def _origin(value, synthetic=False):
    allowed = "https://synthetic.example.invalid" if synthetic else "https://zdbk.zju.edu.cn"
    if value != allowed or urlsplit(value).query or urlsplit(value).fragment:
        fail("source_origin_denied")
    return value


def sanitize_payload(domain, payload):
    """Allow only business field names; never retain raw HTML/headers/hidden fields.

    Unknown keys are counted without echoing their names or values. The digest
    represents this projection, not original HTTP bytes. Callers must never pass
    login HTML, credentials, request headers, cookies or a HAR to this function.
    """
    if domain not in DOMAINS or not isinstance(payload, dict):
        fail("schema_changed")
    allowed = set(BUSINESS_FIELDS[domain]) | SUBJECT_FIELDS
    removed = 0

    def visit(value, depth=0):
        nonlocal removed
        if depth > 12:
            fail("invalid_capture")
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key not in allowed:
                    removed += 1
                    continue
                result[key] = visit(child, depth + 1)
            return result
        if isinstance(value, list):
            if len(value) > 10000:
                fail("capture_limit_exceeded")
            return [visit(child, depth + 1) for child in value]
        if isinstance(value, str):
            if len(value) > 10000 or CREDENTIAL_VALUE.search(value) or "<html" in value.lower() or "<input" in value.lower():
                fail("unsafe_business_value")
            return value
        if value is None or isinstance(value, (bool, int, float)):
            digest(value)
            return value
        fail("invalid_capture")

    return visit(payload), removed


def _observed_subjects(payload):
    """Continuity checks on observed identifiers; these never assign an owner."""
    observed = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in SUBJECT_FIELDS:
                    if isinstance(child, dict):
                        state = child.get("state", "known")
                        child = child.get("value", child.get("raw_value")) if isinstance(state, str) and state in {"known", "pending_verification"} else None
                    if child is not None and type(child) in (str, int) and str(child).strip() not in {"", "--", "unknown", "未知"}:
                        if len(str(child)) > 80:
                            fail("invalid_capture")
                        observed.add(digest(str(child).strip()))
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return observed


def response_state(metadata, *, expected_domain=None):
    """Classify bounded capture claims; they never prove school authentication."""
    native_fields = {"http_status", "content_type", "response_state", "capture_method", "page_validation"}
    if not isinstance(metadata, dict) or set(metadata) - native_fields:
        fail("invalid_response_metadata")
    status, state = metadata.get("http_status"), metadata.get("response_state")
    if state in {"login_html", "redirect_login"}:
        return "awaiting_user_login"
    if state == "captcha":
        return "captcha_required"
    if state == "user_confirmation_required":
        return "user_confirmation_required"
    if status == 401 or state == "auth_expired":
        return "source_session_expired"
    if status == 403 or state == "permission_denied":
        return "permission_denied"
    if state == "transport_failure":
        return "source_unavailable"
    if "capture_method" in metadata or "page_validation" in metadata:
        validation = metadata.get("page_validation")
        if (set(metadata) != native_fields or metadata.get("capture_method") != "native_ax" or
                status is not None or state != "business" or metadata.get("content_type") != "dom_business_projection" or
                not isinstance(validation, dict) or set(validation) != {"status", "domain"} or
                validation.get("status") != "visible_business_page" or expected_domain not in DOMAINS or
                validation.get("domain") != expected_domain):
            fail("invalid_response_metadata")
        return "business"
    if status != 200:
        return "source_unavailable"
    if state != "business" or metadata.get("content_type") not in {"application/json", "dom_business_projection"}:
        return "schema_changed"
    return "business"


class SourceTaskStore:
    def __init__(self, root_path, grant_path, *, clock=time.time):
        self.root, self.grant_path, self.clock = Path(root_path).resolve(), Path(grant_path).resolve(), clock
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            fail("private_permissions_required")
        self.path = self.root / "student_source.sqlite3"
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        if self.path.stat().st_mode & 0o077:
            fail("private_permissions_required")
        self._grant()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, grant_digest TEXT NOT NULL,
                    operator_ref TEXT NOT NULL, data_kind TEXT NOT NULL, target_term TEXT NOT NULL,
                    origin TEXT NOT NULL, generation INTEGER NOT NULL, subject_ref TEXT,
                    status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, domain TEXT NOT NULL,
                    page_index INTEGER NOT NULL, identity_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, normalized TEXT NOT NULL, envelope TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS active(
                    attempt_id TEXT NOT NULL, domain TEXT NOT NULL, observation_id TEXT NOT NULL,
                    PRIMARY KEY(attempt_id,domain));
                CREATE TABLE IF NOT EXISTS domain_states(
                    attempt_id TEXT NOT NULL, domain TEXT NOT NULL, status TEXT NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(attempt_id,domain));
                CREATE TABLE IF NOT EXISTS controls(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _grant(self):
        try:
            mode = self.grant_path.stat().st_mode
            grant = json.loads(self.grant_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            fail("grant_unavailable")
        if mode & 0o077:
            fail("private_permissions_required")
        if (not isinstance(grant, dict) or grant.get("security_realm") != REALM or
                grant.get("purpose") != PURPOSE or grant.get("subject_kind") != "local_review" or
                grant.get("auth_mode") != "local_operation" or grant.get("owner_student_id") is not None or
                grant.get("data_kind") not in {"synthetic", "personal_source_observation"} or
                grant.get("domains") != list(DOMAINS)):
            fail("grant_denied")
        _alias(grant.get("grant_id"))
        _alias(grant.get("operator_ref"))
        if grant.get("revoked") is not False:
            fail("grant_revoked")
        expiry = grant.get("expires_at")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or self.clock() >= expiry:
            fail("grant_expired")
        return grant

    def _authorize(self, db, attempt_id, *, allow_stopped=False):
        grant = self._grant()
        if db.execute("SELECT value FROM controls WHERE key='revoked'").fetchone():
            fail("grant_revoked")
        row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if not row:
            fail("task_not_found")
        if (row["grant_id"], row["grant_digest"], row["operator_ref"], row["data_kind"]) != (
                grant["grant_id"], digest(grant), grant["operator_ref"], grant["data_kind"]):
            fail("stale_grant")
        current = db.execute("SELECT value FROM controls WHERE key='active_generation'").fetchone()
        if not allow_stopped and current and str(row["generation"]) != current[0]:
            fail("source_generation_changed")
        if not allow_stopped and row["status"] in STOP_STATES:
            fail(row["status"])
        return row

    def create_attempt(self, target_term, source_origin, source_subject_ref=None):
        grant = self._grant()
        if not isinstance(target_term, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", target_term):
            fail("target_term_required")
        _origin(source_origin, grant["data_kind"] == "synthetic")
        if source_subject_ref is not None:
            _alias(source_subject_ref)
        attempt_id = "TASK-" + secrets.token_hex(12)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM controls WHERE key='revoked'").fetchone():
                fail("grant_revoked")
            generation = db.execute("SELECT COALESCE(MAX(generation),0)+1 FROM attempts").fetchone()[0]
            if digest(self._grant()) != digest(grant):
                fail("stale_grant")
            db.execute("INSERT INTO attempts(id,grant_id,grant_digest,operator_ref,data_kind,target_term,origin,generation,subject_ref,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (attempt_id, grant["grant_id"], digest(grant), grant["operator_ref"], grant["data_kind"],
                        target_term, source_origin, generation, source_subject_ref, "source_subject_unknown" if source_subject_ref is None else "browser_source_observed", self.clock()))
            db.execute("INSERT OR REPLACE INTO controls(key,value) VALUES('active_generation',?)", (str(generation),))
        return {"attempt_id": attempt_id, "generation": generation, "status": "source_subject_unknown" if source_subject_ref is None else "browser_source_observed"}

    def fail_domain(self, attempt_id, domain, status):
        if domain not in DOMAINS or status not in FAIL_STATES:
            fail("invalid_failure_state")
        with self.connect() as db:
            self._authorize(db, attempt_id, allow_stopped=True)
            db.execute("INSERT OR REPLACE INTO domain_states(attempt_id,domain,status,updated) VALUES(?,?,?,?)", (attempt_id, domain, status, self.clock()))
            if status in STOP_STATES:
                db.execute("UPDATE attempts SET status=? WHERE id=?", (status, attempt_id))
        return {"attempt_id": attempt_id, "domain": domain, "status": status}

    def ingest(self, attempt_id, envelope):
        try:
            return self._ingest(attempt_id, envelope)
        except StudentSourceError as error:
            if error.code in {"schema_changed", "unsafe_business_value", "invalid_capture", "invalid_coverage", "capture_limit_exceeded"}:
                domain = envelope.get("domain") if isinstance(envelope, dict) else None
                if domain in DOMAINS:
                    try:
                        self.fail_domain(attempt_id, domain, "schema_changed" if error.code == "schema_changed" else "incomplete_capture")
                    except StudentSourceError:
                        pass
            raise

    def _ingest(self, attempt_id, envelope):
        if not isinstance(envelope, dict) or set(envelope) - {"schema", "domain", "source_alias", "source_origin", "generation", "source_subject_ref", "scope", "coverage", "response", "payload"}:
            fail("invalid_envelope")
        domain = envelope.get("domain")
        if envelope.get("schema") != SCHEMA or domain not in DOMAINS:
            fail("invalid_envelope")
        _alias(envelope.get("source_alias"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            attempt = self._authorize(db, attempt_id)
            prior_failure = db.execute("SELECT status FROM domain_states WHERE attempt_id=? AND domain=?", (attempt_id, domain)).fetchone()
            if prior_failure and prior_failure[0] in FAIL_STATES:
                fail("new_attempt_required")
            if envelope.get("generation") != attempt["generation"]:
                fail("source_generation_changed")
            if envelope.get("source_subject_ref") != attempt["subject_ref"]:
                db.execute("UPDATE attempts SET status='source_subject_changed' WHERE id=?", (attempt_id,))
                db.commit()
                fail("source_subject_changed")
            if envelope.get("source_origin") != attempt["origin"]:
                fail("source_origin_denied")
            state = response_state(envelope.get("response"), expected_domain=domain)
            if state != "business":
                db.execute("INSERT OR REPLACE INTO domain_states(attempt_id,domain,status,updated) VALUES(?,?,?,?)", (attempt_id, domain, state, self.clock()))
                if state in STOP_STATES:
                    db.execute("UPDATE attempts SET status=? WHERE id=?", (state, attempt_id))
                db.commit()
                return {"attempt_id": attempt_id, "domain": domain, "status": state, "rows": 0}
            scope, coverage = envelope.get("scope"), envelope.get("coverage")
            if (not isinstance(scope, dict) or set(scope) - {"target_term", "academic_year_raw", "term_raw", "query_kind"} or scope.get("target_term") != attempt["target_term"]):
                fail("source_scope_changed")
            if (not isinstance(coverage, dict) or set(coverage) - {"page_index", "total_rows", "has_more", "complete_within_scope"}):
                fail("invalid_coverage")
            page = coverage.get("page_index")
            if type(page) is not int or not 1 <= page <= 3:
                fail("page_budget_exceeded")
            for key in ("has_more", "complete_within_scope"):
                if coverage.get(key) is not None and type(coverage[key]) is not bool:
                    fail("invalid_coverage")
            total = coverage.get("total_rows")
            if total is not None and (type(total) is not int or total < 0):
                fail("invalid_coverage")
            for value in scope.values():
                if not isinstance(value, str) or len(value) > 80 or CREDENTIAL_VALUE.search(value):
                    fail("invalid_scope")
            payload, stripped = sanitize_payload(domain, envelope.get("payload"))
            subjects = _observed_subjects(payload)
            previous_subject = db.execute("SELECT value FROM controls WHERE key=?", ("subject:" + attempt_id,)).fetchone()
            if len(subjects) > 1 or (previous_subject and subjects and previous_subject[0] not in subjects):
                db.execute("UPDATE attempts SET status='source_subject_changed' WHERE id=?", (attempt_id,))
                db.commit()
                fail("source_subject_changed")
            if subjects and not previous_subject:
                db.execute("INSERT INTO controls(key,value) VALUES(?,?)", ("subject:" + attempt_id, next(iter(subjects))))
            if subjects:
                db.execute("UPDATE attempts SET status='browser_source_observed' WHERE id=? AND status='source_subject_unknown'", (attempt_id,))
            normalized = parse_payload(domain, payload, {**scope, "coverage": coverage, "source_alias": envelope["source_alias"], "page_index": page})
            if normalized.get("status") == "schema_changed" or normalized.get("data_available") is False:
                fail("schema_changed")
            safe_envelope = {key: deepcopy(envelope[key]) for key in ("schema", "domain", "source_alias", "source_origin", "generation", "source_subject_ref", "scope", "coverage", "response")}
            safe_envelope.update({"security_realm": REALM, "data_kind": attempt["data_kind"],
                                  "projection_sha256": digest(payload), "digest_basis": "credential_stripped_business_projection",
                                  "stripped_field_count": stripped, "parser_version": PARSER_VERSION, "mapping_version": MAPPING_VERSION})
            identity_key = digest({"attempt": attempt_id, "grant": attempt["grant_digest"], "envelope": safe_envelope, "payload": payload})
            old = db.execute("SELECT id,normalized FROM observations WHERE identity_key=?", (identity_key,)).fetchone()
            if old:
                return {"attempt_id": attempt_id, "domain": domain, "status": "idempotent", "observation_id": old["id"], "rows": len(json.loads(old["normalized"])["records"])}
            obs_id = "OBS-" + secrets.token_hex(12)
            for record in normalized.get("records", []):
                record.update({"source_observation_ref": obs_id, "source_page_index": page})
                record["evidence_refs"] = [{"observation_id": obs_id, "page_index": page, "source_row_index": record.get("source_row_index")}]
                if "attempt_candidate_id" in record:
                    record["attempt_candidate_id"] = f"{obs_id}:row:{record.get('source_row_index')}"
            for failure in normalized.get("row_failures", []):
                failure.update({"source_observation_ref": obs_id, "source_page_index": page})
            parser_coverage = deepcopy(normalized.get("coverage", {}))
            normalized["coverage_evidence"] = [{"page_index": page, "envelope": deepcopy(coverage), "parser": parser_coverage}]
            latest = db.execute("SELECT o.* FROM active a JOIN observations o ON o.id=a.observation_id WHERE a.attempt_id=? AND a.domain=?", (attempt_id, domain)).fetchone()
            if page != (latest["page_index"] + 1 if latest else 1):
                fail("new_attempt_required")
            if latest:
                prior = json.loads(latest["envelope"])
                if prior["scope"] != scope or prior["source_alias"] != envelope["source_alias"]:
                    fail("source_scope_changed")
                if prior["coverage"].get("has_more") is not True:
                    fail("coverage_not_open")
                previous = json.loads(latest["normalized"])
                normalized["records"] = previous["records"] + normalized["records"]
                normalized["row_failures"] = previous["row_failures"] + normalized["row_failures"]
                normalized["issues"] = previous["issues"] + normalized["issues"]
                normalized["coverage_evidence"] = previous["coverage_evidence"] + normalized["coverage_evidence"]
            row_count = len(normalized.get("records", []))
            totals = {item["envelope"].get("total_rows") for item in normalized["coverage_evidence"] if item["envelope"].get("total_rows") is not None}
            totals.update(item["parser"].get("reported_total") for item in normalized["coverage_evidence"] if item["parser"].get("reported_total") is not None)
            conflict = len(totals) > 1
            if conflict:
                normalized["issues"].append({"code": "coverage_totals_conflicting"})
            complete = (coverage.get("complete_within_scope") is True and coverage.get("has_more") is False and total == row_count
                        and not normalized.get("row_failures") and not normalized.get("issues") and not conflict)
            status = "complete_within_scope" if complete else "partial_acquisition"
            normalized["coverage"] = {**coverage, "captured_rows": row_count, "status": status}
            db.execute("INSERT INTO observations(id,attempt_id,domain,page_index,identity_key,payload,normalized,envelope,created) VALUES(?,?,?,?,?,?,?,?,?)",
                       (obs_id, attempt_id, domain, page, identity_key, json.dumps(payload, ensure_ascii=False), json.dumps(normalized, ensure_ascii=False), json.dumps(safe_envelope, ensure_ascii=False), self.clock()))
            self._authorize(db, attempt_id)
            db.execute("INSERT OR REPLACE INTO active(attempt_id,domain,observation_id) VALUES(?,?,?)", (attempt_id, domain, obs_id))
            db.execute("INSERT OR REPLACE INTO domain_states(attempt_id,domain,status,updated) VALUES(?,?,?,?)", (attempt_id, domain, status, self.clock()))
            return {"attempt_id": attempt_id, "domain": domain, "observation_id": obs_id, "status": status,
                    "rows": row_count, "parse_failures": len(normalized.get("row_failures", [])), "projection_sha256": digest(payload), "stripped_field_count": stripped}

    def private_snapshot(self, attempt_id, domain):
        if domain not in DOMAINS:
            fail("invalid_domain")
        with self.connect() as db:
            self._authorize(db, attempt_id)
            state = db.execute("SELECT status FROM domain_states WHERE attempt_id=? AND domain=?", (attempt_id, domain)).fetchone()
            row = db.execute("SELECT o.normalized,o.envelope FROM active a JOIN observations o ON o.id=a.observation_id WHERE a.attempt_id=? AND a.domain=?", (attempt_id, domain)).fetchone()
            if not row:
                return None
            return {"snapshot": json.loads(row[0]), "envelope": json.loads(row[1]), "current_status": state[0], "historical_only": state[0] in FAIL_STATES}

    def describe(self, attempt_id):
        with self.connect() as db:
            attempt = self._authorize(db, attempt_id, allow_stopped=True)
            states = {row[0]: {"status": row[1], "updated_at": row[2]} for row in db.execute("SELECT domain,status,updated FROM domain_states WHERE attempt_id=?", (attempt_id,))}
            counts = {row[0]: row[1] for row in db.execute("SELECT domain,COUNT(*) FROM observations WHERE attempt_id=? GROUP BY domain", (attempt_id,))}
            observed_subject = db.execute("SELECT value FROM controls WHERE key=?", ("subject:" + attempt_id,)).fetchone()
            current_generation = db.execute("SELECT value FROM controls WHERE key='active_generation'").fetchone()
            old_generation = current_generation and str(attempt["generation"]) != current_generation[0]
        status = "source_generation_changed" if old_generation else attempt["status"]
        blocked = bool(old_generation) or status in STOP_STATES
        domain_summary = {}
        for domain in DOMAINS:
            item = {**states.get(domain, {"status": "not_acquired"}), "observations": counts.get(domain, 0)}
            item["historical_only"] = blocked or item["status"] in FAIL_STATES
            domain_summary[domain] = item
        return {"attempt_id": attempt_id, "security_realm": REALM, "data_kind": attempt["data_kind"], "status": status,
                "source_identity": "observed_pending_verification" if observed_subject else "unknown",
                "official_authentication": "not_configured", "domains": domain_summary,
                "limits": {"pages_per_domain": 3, "request_timeout_seconds": 30, "transient_retries": 1, "serial": True},
                "implemented_enforcement": {"page_budget": True, "serialized_transaction_publication": True},
                "automatic_network": False, "timeout_retry": "browser_policy_not_automatic_transport"}

    def readiness(self, attempt_id, catalogue=None):
        description = self.describe(attempt_id)
        try:
            profile = self.private_snapshot(attempt_id, "profile")
        except StudentSourceError as error:
            if error.code not in STOP_STATES | {"source_generation_changed"}:
                raise
            profile = None
        records = profile["snapshot"]["records"] if profile and not profile["historical_only"] else []
        match = match_plan(records[0] if records else {}, catalogue or [])
        return {**description, "plan_match": match,
                "profile": "observed_pending_verification" if records else "unknown",
                "academic": "historical_only" if description["domains"]["academic"]["historical_only"] else description["domains"]["academic"]["status"],
                "arrangements": "historical_only" if description["domains"]["timetable"]["historical_only"] else description["domains"]["timetable"]["status"],
                "rules": "not_reviewed", "calendar": "unknown", "student_plan_binding": "not_verified",
                "recommendation_input": "input_not_ready_for_recommendation",
                "missing": ["official_authentication", "trusted_student_binding", "reviewed_rules", "reliable_calendar", "verified_source_mapping"]}

    def revoke(self):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO controls(key,value) VALUES('revoked','true')")
        return {"status": "grant_revoked", "security_realm": REALM}


def match_plan(profile, catalogue):
    """Exact source fields yield review candidates only, never a verified binding."""
    keys = ("cohort", "major_name", "program", "study_path_raw")
    missing = [key for key in keys if profile.get(key) in (None, "", "--")]
    if missing:
        # A directory result can be a review candidate while the person's
        # category, project and path still lack verified applicability mapping.
        category = profile.get("major_or_category_raw")
        candidates = []
        if profile.get("cohort") is not None and isinstance(category, str) and category:
            for row in catalogue:
                if not isinstance(row, dict):
                    fail("invalid_plan_catalogue")
                if (row.get("source_observation_kind") == "directory_observation" and
                        str(row.get("cohort")) == str(profile["cohort"]) and row.get("major_name") == category and
                        row.get("college_name") == profile.get("college_name") and row.get("college_name")):
                    version = row.get("version_sha256")
                    if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version)):
                        fail("invalid_plan_catalogue")
                    candidates.append({"source_alias": _alias(row.get("source_alias")), "version_sha256": version,
                                       "review_status": "directory_candidate_pending_applicability_review"})
        if candidates:
            return {"status": "candidate_pending_review" if len(candidates) == 1 else "ambiguous_plan",
                    "candidates": candidates, "missing_fields": missing,
                    "matched_fields": ["cohort", "college_name", "major_or_category_raw"],
                    "mapping_status": "category_to_major_not_verified", "binding_status": "not_verified"}
        return {"status": "profile_fields_missing", "candidates": [], "missing_fields": missing, "binding_status": "not_verified"}
    candidates = []
    for row in catalogue:
        if not isinstance(row, dict):
            fail("invalid_plan_catalogue")
        if all(str(row.get(key)) == str(profile[key]) for key in keys):
            version = row.get("version_sha256")
            if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version)):
                fail("invalid_plan_catalogue")
            candidates.append({"source_alias": _alias(row.get("source_alias")), "version_sha256": version, "review_status": "candidate_pending_review"})
    return {"status": "plan_version_missing" if not candidates else "candidate_pending_review" if len(candidates) == 1 else "ambiguous_plan",
            "candidates": candidates, "matched_fields": list(keys), "binding_status": "not_verified"}
