"""Acquire one scoped curriculum PDF into an isolated, local private store.

Callbacks own upstream transport. This module handles neither school credentials
nor application identity, and never parses or enables executable requirements.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time


SCHEMA = "student-plan-acquisition/1"
REALM = "restricted_personal_reference"
SCOPE_FIELDS = ("cohort", "college_name", "major_name", "major_or_category_raw", "program", "study_path_raw")
CANDIDATE_FIELDS = ("plan_ref", *SCOPE_FIELDS, "title", "cohort_provenance")
COHORT_PROVENANCE = frozenset({"unknown", "source_row", "confirmed_query_scope"})
UNKNOWN = (None, "", "--", "unknown", "未知")
OPAQUE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
ALIAS = re.compile(r"PLAN-[a-f0-9]{24}\Z")
UNSAFE = re.compile(r"(?i)(?:<html|<input|bearer\s+|(?:^|[?&;\s])(?:ticket|token|password|passwd|jsessionid|sessionid|cookie)\s*=|(?:cookie|set-cookie|authorization)\s*:)")
AUTH_ERRORS = frozenset(("grant_revoked", "grant_expired", "permission_denied", "grant_denied", "source_session_expired", "source_subject_changed", "source_generation_changed", "awaiting_user_login", "captcha_required", "user_confirmation_required", "source_authorization_changed"))
SOURCE_ERRORS = frozenset(("source_unavailable", "route_not_confirmed", "schema_changed", "invalid_pdf", "document_limit_exceeded", "source_scope_conflict", "invalid_plan_catalogue", "ambiguous_plan", "plan_missing", "stale_input"))


class StudentPlanAcquisitionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise StudentPlanAcquisitionError(code)


def _digest(value):
    try:
        return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError):
        _fail("schema_changed")


def _value(value):
    if isinstance(value, dict):
        if value.get("state") == "conflicting":
            _fail("source_scope_conflict")
        if value.get("state") not in ("known", "pending_verification"):
            return None
        value = value.get("value", value.get("raw_value"))
    if isinstance(value, str):
        value = value.strip()
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail("schema_changed")
    if value in UNKNOWN:
        return None
    if type(value) not in (str, int) or isinstance(value, str) and (
            len(value) > 512 or "\x00" in value or UNSAFE.search(value)):
        _fail("schema_changed")
    return value


def scope_from_profile(profile):
    """Use explicit source fields; current grade/account never imply cohort."""
    if not isinstance(profile, dict):
        _fail("schema_changed")
    states = profile.get("field_states", {})
    if not isinstance(states, dict):
        _fail("schema_changed")
    scope = {}
    for key in SCOPE_FIELDS:
        if states.get(key) == "conflicting":
            _fail("source_scope_conflict")
        scope[key] = None if states.get(key) in ("unknown", "not_applicable") else _value(profile.get(key))
    if states.get("admission_date_raw") == "conflicting":
        _fail("source_scope_conflict")
    date = None if states.get("admission_date_raw") in ("unknown", "not_applicable") else _value(profile.get("admission_date_raw"))
    if isinstance(date, str) and re.fullmatch(r"[0-9]{8}", date):
        try:
            admission_year = datetime.strptime(date, "%Y%m%d").year
        except ValueError:
            admission_year = None
        if admission_year is not None:
            if scope["cohort"] is not None and str(scope["cohort"]) != str(admission_year):
                _fail("source_scope_conflict")
            scope["cohort"] = admission_year
    if scope["cohort"] is not None:
        if not re.fullmatch(r"[0-9]{4}", str(scope["cohort"])):
            _fail("schema_changed")
        scope["cohort"] = int(scope["cohort"])
    return scope


class OnDemandPlanService:
    """Scoped acquisition with immutable PDFs and live authorized cache access.

    ``authorize()`` must supply a stable, opaque ``scope_key`` incorporating
    owner, grant and session generation. Returning a key is an operator contract,
    not verification of a school login or trusted student-plan binding.
    """
    def __init__(self, root, authorize, fetch_catalogue, fetch_document, *,
                 max_pdf_bytes=10 * 1024 * 1024, clock=time.time):
        if not all(callable(callback) for callback in (authorize, fetch_catalogue, fetch_document)):
            _fail("invalid_arguments")
        if type(max_pdf_bytes) is not int or max_pdf_bytes < 32:
            _fail("invalid_arguments")
        self.authorize = authorize
        self.fetch_catalogue = fetch_catalogue
        self.fetch_document = fetch_document
        self.max_pdf_bytes, self.clock = max_pdf_bytes, clock
        try:
            raw = Path(root).expanduser().absolute()
            # /private on macOS is a system prefix, not a project material root.
            if "local-only" not in raw.parts and "private" not in raw.parts[2:]:
                _fail("invalid_private_path")
            if raw.resolve() != raw:
                _fail("invalid_private_path")
            missing, parent = [], raw
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
            self.root = raw
            self._directory(self.root)
            for name in ("objects", "versions", "scopes"):
                path = self.root / name
                path.mkdir(mode=0o700, exist_ok=True)
                self._directory(path)
            self.lock_path = self.root / "publication.lock"
            if not self.lock_path.exists():
                self._write_new(self.lock_path, b"")
            self._file(self.lock_path)
        except OSError:
            _fail("private_store_unavailable")

    @staticmethod
    def _directory(path):
        if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            _fail("private_permissions_required")

    @staticmethod
    def _file(path):
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            _fail("private_permissions_required")

    def _check_store(self):
        self._directory(self.root)
        for name in ("objects", "versions", "scopes"):
            self._directory(self.root / name)
        self._file(self.lock_path)

    @contextmanager
    def _locked(self):
        self._check_store()
        fd = os.open(self.lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _authorize(self, expected=None):
        try:
            receipt = self.authorize()
        except Exception as error:
            code = getattr(error, "code", "permission_denied")
            _fail(code if code in AUTH_ERRORS else "permission_denied")
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("scope_key"), str)
                or not receipt["scope_key"] or len(receipt["scope_key"]) > 512):
            _fail("permission_denied")
        key = _digest(receipt["scope_key"])
        if expected is not None and key != expected:
            _fail("source_authorization_changed")
        return key

    @staticmethod
    def _write_new(path, body):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _load(self, cache_key):
        path = self.root / "scopes" / (cache_key + ".json")
        if not path.exists():
            return {"schema": SCHEMA, "versions": [], "active_alias": None, "failure_status": None}
        self._file(path)
        try:
            state = json.loads(path.read_bytes())
            if (not isinstance(state, dict) or state.get("schema") != SCHEMA
                    or not isinstance(state.get("versions"), list)
                    or any(not isinstance(alias, str) or not ALIAS.fullmatch(alias) for alias in state["versions"])
                    or len(set(state["versions"])) != len(state["versions"])
                    or state.get("active_alias") not in [None, *state["versions"]]
                    or state.get("failure_status") not in {None, *SOURCE_ERRORS}
                    or type(state.get("candidate_count", 0)) is not int
                    or not 0 <= state.get("candidate_count", 0) <= 100):
                _fail("schema_changed")
            return state
        except (ValueError, UnicodeError, OSError):
            _fail("private_store_unavailable")

    def _publish_state(self, cache_key, state, auth_key):
        self._authorize(auth_key)
        path = self.root / "scopes" / (cache_key + ".json")
        if path.exists():
            self._file(path)
        temporary = path.with_name(".pending-" + secrets.token_hex(12))
        try:
            self._write_new(temporary, json.dumps(state, sort_keys=True, ensure_ascii=False,
                                                  allow_nan=False).encode())
            self._authorize(auth_key)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _metadata(self, alias, *, expected_auth=None):
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
            _fail("invalid_alias")
        path = self.root / "versions" / (alias + ".json")
        self._file(path)
        try:
            metadata = json.loads(path.read_bytes())
        except (ValueError, UnicodeError, OSError):
            _fail("private_store_unavailable")
        if (not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA
                or metadata.get("source_alias") != alias
                or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("sha256")))
                or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("cache_key")))
                or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("authorization_scope")))
                or not isinstance(metadata.get("missing_fields"), list)
                or any(key not in SCOPE_FIELDS for key in metadata["missing_fields"])
                or metadata.get("mapping_status") not in ("category_to_major_not_verified", "applicability_pending_review")):
            _fail("schema_changed")
        candidate = metadata.get("candidate")
        provenance = candidate.get("cohort_provenance", "unknown") if isinstance(candidate, dict) else None
        if not isinstance(provenance, str) or provenance not in COHORT_PROVENANCE:
            _fail("schema_changed")
        if expected_auth is not None and metadata.get("authorization_scope") != expected_auth:
            _fail("permission_denied")
        path = self.root / "objects" / (metadata["sha256"] + ".pdf")
        self._file(path)
        body = path.read_bytes()
        if sha256(body).hexdigest() != metadata["sha256"]:
            _fail("stale_input")
        return metadata, body

    def _summary(self, state, *, data_source, auth_key):
        result = {"status": state.get("failure_status") or "plan_missing",
                  "security_realm": REALM, "data_source": data_source,
                  "binding_status": "not_verified", "rules_status": "not_reviewed",
                  "version_count": len(state["versions"]), "candidate_count": state.get("candidate_count", 0),
                  "source_aliases": list(state["versions"]), "sha256": None,
                  "candidate_cohort_provenance": "unknown",
                  "historical_only": bool(state.get("failure_status") and state["versions"]), "missing_fields": []}
        if state.get("active_alias"):
            metadata, _ = self._metadata(state["active_alias"], expected_auth=auth_key)
            result.update({"source_alias": metadata["source_alias"], "sha256": metadata["sha256"],
                           "missing_fields": metadata["missing_fields"],
                           "candidate_cohort_provenance": metadata["candidate"].get("cohort_provenance", "unknown"),
                           "mapping_status": metadata["mapping_status"]})
            if not state.get("failure_status"):
                result["status"] = "acquired_pending_review"
        return result

    @staticmethod
    def _candidate(row, scope):
        if not isinstance(row, dict):
            _fail("invalid_plan_catalogue")
        provenance = row.get("cohort_provenance", "unknown")
        if not isinstance(provenance, str) or provenance not in COHORT_PROVENANCE:
            _fail("invalid_plan_catalogue")
        try:
            candidate = {key: _value(row.get(key)) for key in CANDIDATE_FIELDS}
        except StudentPlanAcquisitionError as error:
            _fail("invalid_plan_catalogue" if error.code == "schema_changed" else error.code)
        candidate["cohort_provenance"] = provenance
        if not isinstance(candidate["plan_ref"], str) or not OPAQUE_REF.fullmatch(candidate["plan_ref"]):
            _fail("invalid_plan_catalogue")
        for key in SCOPE_FIELDS:
            if scope[key] is not None and candidate[key] is not None and str(scope[key]) != str(candidate[key]):
                _fail("source_scope_conflict")
        # A category label may locate a same-name directory candidate, but a
        # different explicit major requires additional applicability evidence.
        if (scope["major_name"] is None and scope["major_or_category_raw"] is not None
                and candidate["major_or_category_raw"] is None and candidate["major_name"] is not None
                and scope["major_or_category_raw"] != candidate["major_name"]):
            _fail("source_scope_conflict")
        return candidate

    def _pdf(self, response):
        if isinstance(response, dict):
            if (set(response) != {"content_type", "body"} or not isinstance(response["content_type"], str)
                    or response["content_type"].split(";", 1)[0].strip().lower() != "application/pdf"):
                _fail("invalid_pdf")
            response = response["body"]
        if not isinstance(response, bytes) or len(response) < 32:
            _fail("invalid_pdf")
        if len(response) > self.max_pdf_bytes:
            _fail("document_limit_exceeded")
        if (not re.match(rb"%PDF-[12]\.[0-9](?:\r|\n)", response)
                or not response[-1024:].rstrip().endswith(b"%%EOF")):
            _fail("invalid_pdf")
        return response

    def describe(self, profile):
        auth_key = self._authorize()
        scope = scope_from_profile(profile)
        cache_key = _digest({"scope": scope, "authorization_scope": auth_key})
        with self._locked():
            state = self._load(cache_key)
            result = self._summary(state, data_source="historical_only" if state.get("failure_status") else "historical_cache", auth_key=auth_key)
        self._authorize(auth_key)
        return result

    def acquire(self, profile, *, force=False):
        if type(force) is not bool:
            _fail("invalid_arguments")
        auth_key = self._authorize()
        scope = scope_from_profile(profile)
        missing = [key for key in ("cohort", "college_name") if scope[key] is None]
        if scope["major_name"] is None and scope["major_or_category_raw"] is None:
            missing.append("major_name_or_category")
        if missing:
            self._authorize(auth_key)
            return {"status": "profile_fields_missing", "data_source": "none", "security_realm": REALM,
                    "missing_fields": missing, "candidate_count": 0, "version_count": 0,
                    "source_aliases": [], "sha256": None, "binding_status": "not_verified", "rules_status": "not_reviewed"}
        cache_key = _digest({"scope": scope, "authorization_scope": auth_key})
        with self._locked():
            previous = self._load(cache_key)
            expected = _digest(previous)
            if previous["active_alias"] and not force:
                result = self._summary(previous, data_source="historical_only" if previous["failure_status"] else "historical_cache", auth_key=auth_key)
                self._authorize(auth_key)
                return result
        candidate_count = 0
        try:
            self._authorize(auth_key)
            rows = self.fetch_catalogue(deepcopy(scope))
            self._authorize(auth_key)
            if not isinstance(rows, list) or len(rows) > 100:
                _fail("invalid_plan_catalogue")
            candidates = [self._candidate(row, scope) for row in rows]
            candidate_count = len(candidates)
            if candidate_count != 1:
                _fail("plan_missing" if candidate_count == 0 else "ambiguous_plan")
            candidate = candidates[0]
            self._authorize(auth_key)
            body = self._pdf(self.fetch_document(candidate["plan_ref"]))
            self._authorize(auth_key)
            content_hash = sha256(body).hexdigest()
            with self._locked():
                state = self._load(cache_key)
                if _digest(state) != expected:
                    _fail("stale_input")
                alias = "PLAN-" + _digest({"cache_key": cache_key, "candidate": candidate, "sha256": content_hash})[:24]
                metadata = {"schema": SCHEMA, "security_realm": REALM, "source_alias": alias,
                            "authorization_scope": auth_key, "cache_key": cache_key,
                            "scope": scope, "candidate": candidate, "sha256": content_hash,
                            "observed_at": self.clock(), "status": "acquired_pending_review",
                            "binding_status": "not_verified", "rules_status": "not_reviewed",
                            "missing_fields": [key for key in SCOPE_FIELDS if candidate[key] is None or scope[key] is None],
                            "mapping_status": "category_to_major_not_verified" if scope["major_name"] is None else "applicability_pending_review"}
                self._authorize(auth_key)
                object_path = self.root / "objects" / (content_hash + ".pdf")
                if object_path.exists():
                    self._file(object_path)
                    if object_path.read_bytes() != body:
                        _fail("stale_input")
                else:
                    self._write_new(object_path, body)
                metadata_path = self.root / "versions" / (alias + ".json")
                if not metadata_path.exists():
                    self._write_new(metadata_path, json.dumps(metadata, sort_keys=True, ensure_ascii=False,
                                                              allow_nan=False).encode())
                else:
                    saved, _ = self._metadata(alias)
                    if any(saved.get(key) != metadata[key] for key in metadata if key != "observed_at"):
                        _fail("stale_input")
                self._authorize(auth_key)
                state.update({"scope": scope, "authorization_scope": auth_key, "active_alias": alias,
                              "candidate_count": 1, "failure_status": None, "last_checked_at": self.clock()})
                if alias not in state["versions"]:
                    state["versions"].append(alias)
                self._publish_state(cache_key, state, auth_key)
                result = self._summary(state, data_source="live", auth_key=auth_key)
            self._authorize(auth_key)
            return result
        except Exception as error:
            code = getattr(error, "code", "source_unavailable")
            if code in AUTH_ERRORS:
                _fail(code)
            code = code if code in SOURCE_ERRORS else "source_unavailable"
            self._authorize(auth_key)
            with self._locked():
                state = self._load(cache_key)
                if _digest(state) != expected:
                    _fail("stale_input")
                state.update({"scope": scope, "authorization_scope": auth_key,
                              "candidate_count": candidate_count, "failure_status": code,
                              "last_checked_at": self.clock()})
                self._publish_state(cache_key, state, auth_key)
                result = self._summary(state, data_source="historical_only" if state["versions"] else "none", auth_key=auth_key)
            self._authorize(auth_key)
            return result

    def read_document(self, source_alias):
        """Read only an active permitted version; failed refreshes block bodies."""
        auth_key = self._authorize()
        with self._locked():
            metadata, body = self._metadata(source_alias, expected_auth=auth_key)
            state = self._load(metadata["cache_key"])
            if state.get("active_alias") != source_alias:
                _fail("historical_document_requires_review")
            if state.get("failure_status"):
                _fail(state["failure_status"])
        self._authorize(auth_key)
        return body
