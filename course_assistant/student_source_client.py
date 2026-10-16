"""Bounded private source reads over an independently owned transport.

No network or credentials are handled here. The transport owns its connection;
authorization and source observations never create a school AuthContext. All
returned pages are private business material, unsuitable for public logs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from queue import Empty, Queue
import re
import threading
import time

from .student_source_parsers import PARSER_VERSION, MAPPING_VERSION, parse_payload
from .student_sources import (CREDENTIAL_VALUE, SCHEMA, SUBJECT_FIELDS, StudentSourceError, sanitize_payload,
                             _observed_subjects, validate_business_field_diagnostics)

OPERATIONS = frozenset({"academic", "major_academic", "timetable", "exams", "plan_catalogue", "plan_document"})
MAX_SOURCE_PAGES = 50
DOMAINS = {"academic": "academic", "major_academic": "academic", "timetable": "timetable", "exams": "exams"}
HARD_STOPS = frozenset({"awaiting_user_login", "source_session_expired", "captcha_required",
                        "permission_denied", "source_subject_changed", "user_confirmation_required"})
FAILURES = HARD_STOPS | {"source_unavailable", "schema_changed", "route_not_confirmed"}
PLAN_SCOPE = frozenset({"cohort", "college_name", "major_name", "major_or_category_raw", "program", "study_path_raw"})
COHORT_PROVENANCE = frozenset({"unknown", "source_row", "confirmed_query_scope"})
PLAN_FIELDS = PLAN_SCOPE | {"items", "records", "total", "totalCount", "totalResult", "pageNo", "currentPage",
                           "pageSize", "showCount", "plan_ref", "source_alias", "version_sha256", "source_observation_kind",
                           "title", "cohort_provenance", "value", "raw_value", "state"} | SUBJECT_FIELDS
RESPONSE_FIELDS = frozenset({"state", "http_status", "payload", "content_type", "session_epoch", "source_subject_id",
                             "capture_method", "page_validation", "source_field_diagnostics"})


class SourceClientError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _hash(value):
    try:
        return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        raise SourceClientError("invalid_response") from None


def _text(value, limit=256):
    return (isinstance(value, str) and 0 < len(value) <= limit and
            not CREDENTIAL_VALUE.search(value) and not value.startswith(("http:", "https:", "file:", "/", "\\")))


def _integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    raise SourceClientError("invalid_pagination")


def _catalogue_projection(payload):
    def visit(value, depth=0):
        if depth > 10:
            raise SourceClientError("invalid_response")
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key not in PLAN_FIELDS:
                    continue
                if key == "title" and (not isinstance(child, str) or len(child) > 500):
                    raise SourceClientError("invalid_catalogue_title")
                if key == "cohort_provenance" and (not isinstance(child, str) or child not in COHORT_PROVENANCE):
                    raise SourceClientError("invalid_catalogue_provenance")
                result[key] = visit(child, depth + 1)
            return result
        if isinstance(value, list) and len(value) <= 1000:
            return [visit(child, depth + 1) for child in value]
        if (isinstance(value, str) and len(value) <= 10000 and not CREDENTIAL_VALUE.search(value)
                and not re.search(r"<[A-Za-z!/?][^>]*>", value)):
            return value
        if value is None or type(value) in (bool, int, float):
            _hash(value)
            return value
        raise SourceClientError("invalid_response")
    return visit(payload)


class StudentSourceClient:
    """Serialize bounded reads and cache stripped snapshots in memory only.

    ``authorize()`` must return a stable ``scope_key`` covering the operator,
    task and grant version. Optional ``operations`` and ``target_term`` narrow
    that scope. Exceptions from this callback propagate without a fallback.

    A transport must provide ``session_epoch`` and ``request(spec)``. An own
    timeout cannot cancel an arbitrary transport, so its late response is never
    published and no overlapping request starts while it is still running.
    """

    def __init__(self, transport, authorize, *, clock=time.time, timeout=30,
                 retries=1, maxpages=MAX_SOURCE_PAGES, cache_seconds=300, total_timeout=60):
        if (not callable(authorize) or not _finite(timeout) or not 0 < timeout <= 30 or
                type(retries) is not int or not 0 <= retries <= 1 or
                type(maxpages) is not int or not 1 <= maxpages <= MAX_SOURCE_PAGES or
                not _finite(total_timeout) or not 0 < total_timeout <= 120 or
                not _finite(cache_seconds) or not 0 <= cache_seconds <= 86400):
            raise SourceClientError("invalid_client_policy")
        self.transport, self.authorize, self.clock = transport, authorize, clock
        self.timeout, self.retries, self.maxpages, self.cache_seconds = timeout, retries, maxpages, cache_seconds
        self.total_timeout = total_timeout
        self._lock = threading.RLock()
        self._cache, self._subjects = {}, {}
        self._last_epoch, self._pending = None, None

    def _now(self):
        now = self.clock()
        if not _finite(now):
            raise SourceClientError("invalid_clock")
        return now

    def _auth(self, spec):
        try:
            grant = self.authorize()
            if not isinstance(grant, dict) or not _text(grant.get("scope_key")):
                raise SourceClientError("invalid_authorization_scope")
            if "operations" in grant and (not isinstance(grant["operations"], (list, tuple, set, frozenset)) or spec["operation"] not in grant["operations"]):
                raise SourceClientError("operation_not_authorized")
            if ("target_term" in grant and (spec["operation"] in DOMAINS or "target_term" in spec)
                    and spec.get("target_term") != grant["target_term"]):
                raise SourceClientError("term_not_authorized")
            return _hash(grant["scope_key"])
        except Exception:
            self._cache.clear()
            raise

    def _epoch(self):
        epoch = self.transport.session_epoch
        if not ((type(epoch) is int and epoch >= 0) or _text(epoch, 128)):
            self._cache.clear()
            raise SourceClientError("invalid_session_epoch")
        return epoch

    def _recheck(self, spec, scope_key, epoch):
        if self._auth(spec) != scope_key:
            self._cache.clear()
            raise SourceClientError("authorization_scope_changed")
        if self._epoch() != epoch:
            self._cache.clear()
            return False
        return True

    def _spec(self, operation, target_term, scope, plan_ref):
        if not isinstance(operation, str) or operation not in OPERATIONS:
            raise SourceClientError("unsupported_operation")
        if target_term is not None and (not isinstance(target_term, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", target_term)):
            raise SourceClientError("invalid_target_term")
        if operation in DOMAINS and (target_term is None or scope is not None or plan_ref is not None):
            raise SourceClientError("invalid_source_scope")
        if scope is not None:
            if not isinstance(scope, dict) or set(scope) - PLAN_SCOPE:
                raise SourceClientError("invalid_plan_scope")
            for key, value in scope.items():
                if value is None:
                    continue
                if key == "cohort":
                    if not 1900 <= _integer(value) <= 2200:
                        raise SourceClientError("invalid_plan_scope")
                elif not _text(value):
                    raise SourceClientError("invalid_plan_scope")
        if operation == "plan_catalogue" and (not scope or plan_ref is not None):
            raise SourceClientError("invalid_plan_scope")
        if operation == "plan_document" and (not isinstance(plan_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}", plan_ref)):
            raise SourceClientError("invalid_plan_reference")
        spec = {"operation": operation, "page": 1, "page_size": 20}
        if target_term is not None:
            spec["target_term"] = target_term
        if scope is not None:
            spec["scope"] = deepcopy(scope)
        if plan_ref is not None:
            spec["plan_ref"] = plan_ref
        return spec

    def _request(self, spec, *, timeout=None):
        if self._pending is not None and not self._pending.is_set():
            return None, "transport_still_running", False
        queue, done = Queue(maxsize=1), threading.Event()
        self._pending = done
        def work():
            try:
                outcome = (self.transport.request(deepcopy(spec)), None, False)
            except (TimeoutError, OSError):
                outcome = (None, "transport_unavailable", True)
            except Exception:
                outcome = (None, "transport_failed", False)
            done.set()
            queue.put(outcome)
        threading.Thread(target=work, daemon=True).start()
        try:
            return queue.get(timeout=self.timeout if timeout is None else timeout)
        except Empty:
            # A late challenge may be unavailable to the caller. Do not retain
            # a reusable cache while the connection's request is unresolved.
            self._cache.clear()
            return None, "request_timeout", False

    def _validate(self, response, spec, epoch, scope_key):
        if not isinstance(response, dict) or not isinstance(response.get("state"), str):
            raise SourceClientError("invalid_response")
        state = response["state"]
        if response.get("http_status") == 401:
            return None, "source_session_expired"
        if response.get("http_status") == 403:
            return None, "permission_denied"
        if state in HARD_STOPS:
            return None, state
        if set(response) - RESPONSE_FIELDS or state not in {"business"} | FAILURES:
            raise SourceClientError("invalid_response")
        if type(response.get("session_epoch")) is not type(epoch) or response.get("session_epoch") != epoch or self._epoch() != epoch:
            return None, "source_session_expired"
        if state != "business":
            return None, state
        dom = response.get("capture_method") == "browser_dom" or "page_validation" in response
        if dom:
            validation = response.get("page_validation")
            if (spec["operation"] != "plan_catalogue" or "http_status" not in response or response["http_status"] is not None or
                    response.get("capture_method") != "browser_dom" or response.get("content_type") != "dom_business_projection" or
                    not isinstance(validation, dict) or set(validation) != {"status", "domain"} or
                    validation.get("status") != "visible_business_page" or validation.get("domain") != "plan_catalogue"):
                raise SourceClientError("invalid_catalogue_capture")
        else:
            if "capture_method" in response and response["capture_method"] != "browser_http":
                raise SourceClientError("invalid_http_capture")
            if type(response.get("http_status")) is not int or response["http_status"] != 200:
                raise SourceClientError("business_http_status_unverified")
        content_type = response.get("content_type")
        if not isinstance(content_type, str):
            raise SourceClientError("invalid_content_type")
        mime = content_type.partition(";")[0].strip().lower()
        payload = response.get("payload")
        if spec["operation"] == "plan_document":
            if (mime != "application/pdf" or not isinstance(payload, bytes) or len(payload) > 10 * 1024 * 1024 or
                    not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF")):
                raise SourceClientError("invalid_plan_document")
            projected, rows, total, issues = payload, 1, 1, False
        else:
            if mime != ("dom_business_projection" if dom else "application/json") or not isinstance(payload, dict):
                raise SourceClientError("invalid_business_payload")
            domain = DOMAINS.get(spec["operation"])
            try:
                projected = sanitize_payload(domain, payload)[0] if domain else _catalogue_projection(payload)
            except StudentSourceError:
                raise SourceClientError("unsafe_business_projection") from None
            if len(json.dumps(projected, ensure_ascii=False).encode()) > 2 * 1024 * 1024:
                raise SourceClientError("business_page_too_large")
            containers = ("kbList", "records") if domain == "timetable" else ("items", "records")
            lists = [projected[key] for key in containers if key in projected]
            if not lists or not isinstance(lists[0], list) or any(value != lists[0] for value in lists[1:]):
                raise SourceClientError("invalid_record_container")
            rows = len(lists[0])
            if rows > spec["page_size"]:
                raise SourceClientError("page_size_exceeded")
            for key in ("pageNo", "currentPage"):
                if key in projected and _integer(projected[key]) != spec["page"]:
                    raise SourceClientError("page_number_mismatch")
            totals = [_integer(projected[key]) for key in ("total", "totalCount", "totalResult")
                      if key in projected and projected[key] is not None]
            if any(value < 0 for value in totals) or len(set(totals)) > 1:
                raise SourceClientError("total_count_conflicting")
            total = totals[0] if totals else None
            if domain:
                normalized = parse_payload(domain, projected)
                if normalized["status"] == "schema_changed":
                    raise SourceClientError("invalid_record_container")
                issues = bool(normalized["issues"] or normalized["row_failures"])
            else:
                issues = any(not isinstance(row, dict) or not row.get("plan_ref") for row in lists[0])
                for row in lists[0]:
                    if isinstance(row, dict) and row.get("plan_ref") is not None and (
                            not isinstance(row["plan_ref"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}", row["plan_ref"])):
                        raise SourceClientError("invalid_plan_reference")
        subject = response.get("source_subject_id")
        subjects = _observed_subjects(projected) if isinstance(projected, dict) else set()
        if subject is not None:
            if not _text(subject, 80):
                raise SourceClientError("invalid_source_subject")
            subjects.add(_hash(subject.strip()))
        anchor_key = (scope_key, epoch)
        previous = self._subjects.get(anchor_key)
        if len(subjects) > 1 or (previous and subjects and previous not in subjects):
            return None, "source_subject_changed"
        if subjects:
            self._subjects[anchor_key] = next(iter(subjects))
        safe = {"state": "business", "http_status": None if dom else 200, "content_type": mime,
                "session_epoch": epoch, "source_subject_id": subject, "payload": deepcopy(projected)}
        if "source_field_diagnostics" in response:
            try:
                safe["source_field_diagnostics"] = validate_business_field_diagnostics(
                    DOMAINS.get(spec["operation"]), response["source_field_diagnostics"])
                if safe["source_field_diagnostics"]["projection"]["row_count"] != rows:
                    raise SourceClientError("invalid_field_diagnostics")
            except StudentSourceError:
                raise SourceClientError("invalid_field_diagnostics") from None
        if dom:
            safe.update(capture_method="browser_dom", page_validation=deepcopy(response["page_validation"]))
        elif response.get("capture_method") == "browser_http":
            safe["capture_method"] = "browser_http"
        return {"response": safe, "payload": deepcopy(projected), "page_index": spec["page"],
                "rows": rows, "total_rows": total, "parse_issues": issues}, None

    def _result(self, status, pages, captured_at, *, source="none", failure=None, total=None, attempts=0):
        rows = sum(page["rows"] for page in pages)
        return {"status": status, "data_source": source, "pages": deepcopy(pages),
                "coverage": {"captured_rows": rows, "reported_total": total, "pages_captured": len(pages),
                             "complete_within_scope": status == "complete_within_scope",
                             "has_more": total > rows if total is not None else None},
                "captured_at": captured_at, "checked_at": self._now(), "cache_captured_at": None,
                "failure": failure, "request_attempts": attempts,
                "official_authentication": "not_configured"}

    def fetch(self, operation, target_term=None, force_refresh=False, scope=None, plan_ref=None):
        if type(force_refresh) is not bool:
            raise SourceClientError("invalid_refresh")
        spec = self._spec(operation, target_term, scope, plan_ref)
        with self._lock:
            scope_key, epoch = self._auth(spec), self._epoch()
            if self._last_epoch is not None and self._last_epoch != epoch:
                self._cache.clear()
                self._subjects.clear()
            self._last_epoch = epoch
            key = _hash({"grant": scope_key, "epoch": epoch, "request": spec,
                         "parser": PARSER_VERSION, "mapping": MAPPING_VERSION})
            cached = self._cache.get(key)
            now = self._now()
            if cached and not force_refresh and 0 <= now - cached["captured_at"] < self.cache_seconds:
                if not self._recheck(spec, scope_key, epoch):
                    return self._result("source_session_expired", [], None, failure="session_epoch_changed")
                result = deepcopy(cached)
                result.update(data_source="historical_cache", checked_at=self._now(), cache_captured_at=cached["captured_at"], request_attempts=0)
                return result
            pages, total, attempts, failure, state = [], None, 0, None, None
            deadline = time.monotonic() + self.total_timeout
            fingerprints = set()
            limit = 1 if operation.startswith("plan_") or operation == "timetable" else self.maxpages
            for page_number in range(1, limit + 1):
                request = {**spec, "page": page_number}
                for retry in range(self.retries + 1):
                    if not self._recheck(request, scope_key, epoch):
                        state, failure = "source_session_expired", "session_epoch_changed"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        state, failure = "source_unavailable", "request_budget_exceeded"
                        break
                    response, error, retryable = self._request(request, timeout=min(self.timeout, remaining))
                    attempts += 1
                    if not self._recheck(request, scope_key, epoch):
                        state, failure = "source_session_expired", "session_epoch_changed"
                        break
                    if time.monotonic() >= deadline:
                        state, failure = "source_unavailable", "request_budget_exceeded"
                        break
                    if error:
                        state, failure = "source_unavailable", error
                    else:
                        try:
                            page, state = self._validate(response, request, epoch, scope_key)
                            failure = state
                            retryable = state == "source_unavailable"
                        except SourceClientError as error:
                            state, failure, retryable = "schema_changed", error.code, False
                    if state != "source_unavailable" or not retryable or retry == self.retries:
                        break
                if state:
                    break
                payload = page["payload"]
                body = ([payload[key] for key in ("items", "records", "kbList") if key in payload]
                        if isinstance(payload, dict) else sha256(payload).hexdigest())
                fingerprint = _hash(body)
                if fingerprint in fingerprints:
                    state, failure = "schema_changed", "repeated_business_page"
                    break
                fingerprints.add(fingerprint)
                if page["total_rows"] is not None:
                    if total is not None and page["total_rows"] != total:
                        state, failure = "schema_changed", "total_count_changed"
                        break
                    total = page["total_rows"]
                pages.append(page)
                rows = sum(item["rows"] for item in pages)
                if total is not None and rows > total:
                    state, failure = "schema_changed", "captured_rows_exceed_total"
                    break
                if (total is not None and rows == total) or page["rows"] == 0 or (total is None and page["rows"] < 20):
                    break
            else:
                if total is not None and total > sum(item["rows"] for item in pages):
                    state, failure = "source_unavailable", "page_limit_exceeded"
            if not self._recheck(spec, scope_key, epoch):
                state, failure = "source_session_expired", "session_epoch_changed"
            if state in HARD_STOPS:
                self._cache.clear()
                return self._result(state, [], None, failure=failure, attempts=attempts)
            if state and not pages:
                if state == "source_unavailable" and cached and failure != "transport_failed":
                    result = deepcopy(cached)
                    result.update(status=state, data_source="historical_cache", failure=failure,
                                  checked_at=self._now(), cache_captured_at=cached["captured_at"], request_attempts=attempts)
                    return result
                self._cache.pop(key, None)
                return self._result(state, [], None, failure=failure, attempts=attempts)
            if state:
                self._cache.pop(key, None)
            complete = not state and total is not None and total == sum(item["rows"] for item in pages) and not any(item["parse_issues"] for item in pages)
            result = self._result("complete_within_scope" if complete else "partial_acquisition", pages, self._now(),
                                  source="live" if pages else "none", failure=failure, total=total, attempts=attempts)
            if complete:
                self._cache[key] = deepcopy(result)
            return result


def to_envelopes(result, *, operation, target_term, source_alias, source_origin,
                 generation, source_subject_ref=None):
    """Project live private pages into the existing store; never publish cache as live."""
    if operation not in DOMAINS or result.get("data_source") != "live":
        raise SourceClientError("live_personal_capture_required")
    envelopes = []
    pages = result.get("pages", [])
    for index, page in enumerate(pages):
        response = page["response"]
        coverage = result["coverage"]
        payload = deepcopy(page["payload"])
        # Preserve separately observed transport subject metadata in the
        # private business projection so store continuity survives restarts.
        if response.get("source_subject_id") is not None:
            payload["source_subject_id"] = response["source_subject_id"]
        envelopes.append({"schema": SCHEMA, "domain": DOMAINS[operation], "source_alias": source_alias,
                          "source_origin": source_origin, "generation": generation, "source_subject_ref": source_subject_ref,
                          "scope": {"target_term": target_term, "query_kind": operation},
                          "coverage": {"page_index": page["page_index"], "total_rows": page["total_rows"],
                                       "has_more": index < len(pages) - 1 or coverage.get("has_more") is True,
                                       "complete_within_scope": index == len(pages) - 1 and coverage["complete_within_scope"]},
                          "response": {"http_status": response["http_status"], "content_type": response["content_type"], "response_state": "business"},
                          "payload": payload})
    return envelopes
