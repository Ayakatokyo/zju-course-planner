"""Private, generation-bound observations and local-only record queries.

The caller supplies already stripped SchoolAccountService results and a live
authorization callback. This module neither reads original source files nor
contacts a school or model. An observation is not an academic/enrollment fact.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from hashlib import sha256
from pathlib import Path
import base64
import fcntl
import hmac
import json
import math
import os
import re
import secrets
import stat
import threading
import unicodedata
import uuid


SCHEMA = "personal-observation/1"
OPERATIONS = frozenset({"academic", "major_academic", "timetable", "exams"})


def normalize_record_query(query, operation):
    """Normalize lookup text; only an anchored generic grades request is broad.

    Source values and course identities remain unchanged. A sentence containing
    a specific course, person or other condition is never converted to a scan.
    """
    query = unicodedata.normalize("NFKC", query).strip()
    compact = re.sub(r"\s+", "", query).strip("?。!！")
    if operation in {"academic", "major_academic"} and re.fullmatch(
            r"(?:请)?(?:帮我|帮忙)?(?:查询|查看|查一下|看一下|看看|查|看)?(?:一下)?"
            r"(?:我的|本人|我)?(?:学业)?成绩(?:总览|概况|情况|怎么样|如何)?", compact):
        return ""
    return query


def source_grade_summary(records, coverage):
    """Describe raw numeric grades in matching rows; never infer recognition.

    Decimal strings keep the transport independent of binary floating point.
    Means use ROUND_HALF_UP to two decimal places. Known text, annotations and
    numbers outside 0..100 are excluded, not converted into pass/fail or points.
    """
    numbers, non_numeric, unknown, completion_known = [], 0, 0, 0
    for record in records:
        states = record.get("field_states", {})
        completion = record.get("completion_status_raw")
        completion_missing = completion is None or isinstance(completion, str) and completion.strip().casefold() in {"", "unknown"}
        if isinstance(states, dict) and states.get("completion_status_raw") == "known" and not completion_missing:
            completion_known += 1
        value = record.get("grade_raw")
        if not isinstance(states, dict) or states.get("grade_raw") != "known" or value is None:
            unknown += 1
            continue
        raw = str(value).strip() if type(value) in (str, int, float) else ""
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
            non_numeric += 1
            continue
        try:
            grade = Decimal(raw)
        except InvalidOperation:
            non_numeric += 1
            continue
        if not grade.is_finite() or not 0 <= grade <= 100:
            non_numeric += 1
            continue
        numbers.append(grade)
    canonical = lambda value: format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")
    with localcontext() as context:
        context.prec = 40
        mean = (sum(numbers, Decimal(0)) / Decimal(len(numbers))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if numbers else None
    return {"matched_records": len(records), "numeric_grade_count": len(numbers),
        "non_numeric_grade_count": non_numeric, "unknown_grade_count": unknown,
        "completion_status_counts": {"known": completion_known, "unknown": len(records) - completion_known},
        "numeric_min": canonical(min(numbers)) if numbers else None,
        "numeric_max": canonical(max(numbers)) if numbers else None,
        "numeric_mean_unweighted": format(mean, ".2f") if mean is not None else None,
        "mean_decimal_places": 2, "basis": "known_pure_numeric_0_100_source_grades",
        "scope": "all_matching_rows_in_selected_snapshot", "coverage": deepcopy(coverage),
        "is_gpa": False, "earned_credits_verified": False, "academic_recognition_verified": False,
        "note": "仅对当前快照全部匹配行中的已知纯数字0至100来源成绩作简单不加权概况；非标准数字或文字成绩不猜。完成状态计数仅指来源字段已知性，不推断通过。不是GPA、绩点、已获学分或修读认定。"}
RESULT_FIELDS = frozenset({"status", "data_kind", "security_realm", "operation", "target_term",
    "data_source", "records", "issues", "row_failures", "coverage", "captured_at", "checked_at",
    "cache_captured_at", "failure", "source_alias", "source_field_diagnostics", "period_times",
    "period_times_source"})
QUERY_FIELDS = frozenset({"operation", "observation_id", "query", "academic_year", "limit", "cursor", "mode"})
HARD_STOPS = frozenset({"source_session_expired", "source_subject_changed", "permission_denied",
    "awaiting_user_login", "captcha_required", "user_confirmation_required"})
OBSERVATION_ID = re.compile(r"OBS-[a-f0-9]{32}\Z")
SOURCE_ALIAS = re.compile(r"SOURCE-[a-f0-9]{24}\Z")
MAX_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOTS = 2000
_CREDENTIAL_KEY = re.compile(r"(?:password|passwd|cookie|token|authorization|credential|csrf|secret|apikey|sessionid)\Z", re.I)


class PersonalObservationError(ValueError):
    """Credential-free error with a stable machine-readable code."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise PersonalObservationError(code)


def _json(value, code="invalid_observation_result"):
    """Keep JSON values exactly; never stringify unsupported Python objects."""
    remaining = 100000
    def check(node, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 24:
            _fail(code)
        if node is None or type(node) in (bool, int):
            return
        if type(node) is float:
            if not math.isfinite(node):
                _fail(code)
            return
        if type(node) is str:
            if len(node) > 100000 or "\x00" in node:
                _fail(code)
            return
        if type(node) is list:
            for item in node:
                check(item, depth + 1)
            return
        if type(node) is dict:
            for key, item in node.items():
                if type(key) is not str or _CREDENTIAL_KEY.fullmatch(re.sub(r"[^a-z0-9]", "", key.lower())):
                    _fail(code)
                check(item, depth + 1)
            return
        _fail(code)
    try:
        check(value)
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail(code)
    if len(raw) > MAX_BYTES:
        _fail(code)
    return raw


def _digest(value):
    return sha256(_json(value)).hexdigest()


def _timestamp(value):
    return value is None or type(value) is int and value >= 0 or type(value) is float and math.isfinite(value) and value >= 0


class PersonalObservationStore:
    """One immutable authorization generation, with owner/realm isolation.

    ``authorize()`` returns scope_key, owner_student_id and security_realm. Its
    scope_key must incorporate the current application and school generation.
    A new generation requires a new store; old references never grant access.
    """
    def __init__(self, root, authorize):
        if not callable(authorize):
            _fail("invalid_observation_request")
        self.authorize = authorize
        self._lock = threading.RLock()
        self._scope = self._authorization()
        self._scope_digest = _digest(self._scope)
        try:
            self.root = Path(root).expanduser().absolute()
            if self.root.resolve() != self.root:
                _fail("invalid_private_store")
            missing, parent = [], self.root
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for path in reversed(missing):
                path.mkdir(mode=0o700)
            self._directory(self.root)
            self.directory = self.root / self._scope_digest
            self.directory.mkdir(mode=0o700, exist_ok=True)
            self._directory(self.directory)
            self.lock_path = self.directory / "publication.lock"
            self.key_path = self.directory / "cursor.key"
            self._create(self.lock_path, b"", exists_ok=True)
            self._create(self.key_path, secrets.token_bytes(32), exists_ok=True)
            self._key = self._read(self.key_path, maximum=32)
            if len(self._key) != 32:
                _fail("invalid_private_store")
            self._authorization(self._scope)
        except (OSError, ValueError, TypeError) as error:
            if isinstance(error, PersonalObservationError):
                raise
            _fail("invalid_private_store")

    def _authorization(self, expected=None):
        try:
            receipt = self.authorize()
        except Exception:
            _fail("observation_expired")
        keys = ("scope_key", "owner_student_id", "security_realm")
        if (not isinstance(receipt, dict) or any(type(receipt.get(key)) is not str or
                not 0 < len(receipt[key]) <= 512 or "\x00" in receipt[key] for key in keys)):
            _fail("observation_expired")
        result = {key: receipt[key] for key in keys}
        if expected is not None and result != expected:
            _fail("observation_scope_changed")
        return result

    @staticmethod
    def _directory(path):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or path.resolve() != path:
            _fail("invalid_private_store")

    @staticmethod
    def _file_info(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            _fail("invalid_private_store")

    @staticmethod
    def _parent_fd(path):
        """Walk every directory with NOFOLLOW, including during file opens."""
        fd = os.open(path.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            for part in path.parent.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                                  getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except BaseException:
            os.close(fd)
            raise

    @classmethod
    def _read(cls, path, *, maximum=MAX_BYTES):
        parent = cls._parent_fd(path)
        try:
            fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        finally:
            os.close(parent)
        try:
            cls._file_info(fd)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                _fail("invalid_private_store")
            return raw
        finally:
            os.close(fd)

    @classmethod
    def _create(cls, path, raw, *, exists_ok=False):
        parent = cls._parent_fd(path)
        try:
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent)
        except FileExistsError:
            if not exists_ok:
                raise
            cls._read(path)
            return
        finally:
            os.close(parent)
        try:
            cls._file_info(fd)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _locked(self):
        with self._lock:
            self._authorization(self._scope)
            try:
                self._directory(self.root)
                self._directory(self.directory)
                parent = self._parent_fd(self.lock_path)
                try:
                    fd = os.open(self.lock_path.name, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
                finally:
                    os.close(parent)
                try:
                    self._file_info(fd)
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    self._authorization(self._scope)
                    yield
                finally:
                    os.close(fd)
            except OSError:
                _fail("invalid_private_store")

    def _sign(self, value):
        return hmac.new(self._key, _json(value), sha256).hexdigest()

    def _load(self, identifier):
        if type(identifier) is not str or not OBSERVATION_ID.fullmatch(identifier):
            _fail("inaccessible_observation")
        try:
            document = json.loads(self._read(self.directory / (identifier + ".json")))
        except FileNotFoundError:
            _fail("inaccessible_observation")
        except (ValueError, UnicodeError):
            _fail("invalid_private_store")
        if not isinstance(document, dict) or set(document) != {"snapshot", "signature"}:
            _fail("invalid_private_store")
        snapshot = document["snapshot"]
        if (not isinstance(snapshot, dict) or type(document["signature"]) is not str or
                not re.fullmatch(r"[a-f0-9]{64}", document["signature"]) or
                not hmac.compare_digest(document["signature"], self._sign(snapshot)) or
                snapshot.get("schema") != SCHEMA or snapshot.get("observation_id") != identifier or
                snapshot.get("scope_digest") != self._scope_digest or
                snapshot.get("owner_student_id") != self._scope["owner_student_id"] or
                snapshot.get("security_realm") != self._scope["security_realm"]):
            _fail("invalid_private_store")
        return snapshot

    def _all(self):
        paths = list(self.directory.iterdir())
        if len(paths) > MAX_SNAPSHOTS + 2:
            _fail("observation_store_limit")
        rows = []
        for path in paths:
            if path.name in {"publication.lock", "cursor.key"}:
                continue
            if path.suffix != ".json" or not OBSERVATION_ID.fullmatch(path.stem):
                _fail("invalid_private_store")
            rows.append(self._load(path.stem))
        return sorted(rows, key=lambda row: row["sequence"], reverse=True)

    def _result(self, result):
        if (not isinstance(result, dict) or set(result) - RESULT_FIELDS or
                not {"operation", "target_term", "records", "coverage", "data_source"} <= set(result) or
                type(result.get("operation")) is not str or result["operation"] not in OPERATIONS or
                type(result["records"]) is not list or len(result["records"]) > 2000 or
                any(type(row) is not dict for row in result["records"]) or type(result["coverage"]) is not dict or
                type(result["data_source"]) is not str or result["data_source"] not in {"live", "historical_cache", "none"}):
            _fail("invalid_observation_result")
        if type(result.get("status")) is str and result["status"] in HARD_STOPS:
            _fail("observation_expired")
        if result.get("data_kind", "personal_source") != "personal_source" or result.get("security_realm", self._scope["security_realm"]) != self._scope["security_realm"]:
            _fail("invalid_observation_result")
        term = result["target_term"]
        if result["operation"] == "major_academic":
            valid = term == "ALL-HISTORY"
        else:
            match = re.fullmatch(r"((?:19|20|21)[0-9]{2})-((?:19|20|21)[0-9]{2})-(AW|SS|A|W|S|SU)", term) if type(term) is str else None
            valid = bool(match and int(match[2]) == int(match[1]) + 1)
        if not valid or any(not _timestamp(result.get(key)) for key in ("captured_at", "checked_at", "cache_captured_at")):
            _fail("invalid_observation_result")
        if result.get("captured_at") is not None and result.get("checked_at") is not None and result["captured_at"] > result["checked_at"]:
            _fail("invalid_observation_result")
        alias = result.get("source_alias")
        if alias is not None and (type(alias) is not str or not SOURCE_ALIAS.fullmatch(alias)):
            _fail("invalid_observation_result")
        for key in ("issues", "row_failures", "source_field_diagnostics", "period_times"):
            if key in result and type(result[key]) is not list:
                _fail("invalid_observation_result")
        if "period_times_source" in result and type(result["period_times_source"]) is not dict:
            _fail("invalid_observation_result")
        for key in ("status", "failure"):
            if result.get(key) is not None and (type(result[key]) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", result[key])):
                _fail("invalid_observation_result")
        _json(result)
        return deepcopy(result)

    @staticmethod
    def _summary(snapshot):
        result = snapshot["result"]
        return {"observation_id": snapshot["observation_id"], "operation": result["operation"],
            "target_term": result["target_term"], "status": result.get("status"),
            "observation_status": snapshot["observation_status"], "data_kind": "personal_source", "local_only": True,
            "data_source": result["data_source"], "source_alias": result.get("source_alias"),
            "captured_at": result.get("captured_at"), "checked_at": result.get("checked_at"),
            "cache_captured_at": result.get("cache_captured_at"), "record_count": len(result["records"]),
            "coverage": deepcopy(result["coverage"]), "historical_only": snapshot["historical_only"],
            "linked_observation_id": snapshot["linked_observation_id"],
            "parser_version": snapshot["parser_version"], "mapping_version": snapshot["mapping_version"],
            "versions": {"observation_id": snapshot["observation_id"], "records_sha256": snapshot["records_sha256"],
                "snapshot_sha256": _digest(snapshot), "parser_version": snapshot["parser_version"],
                "mapping_version": snapshot["mapping_version"], "authorization_version": snapshot["scope_digest"]}}

    def register(self, result, *, parser_version, mapping_version):
        """Register a new read event; cached reads retain their capture time."""
        with self._locked():
            result = self._result(result)
            if any(type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) for value in (parser_version, mapping_version)):
                _fail("invalid_observation_result")
            rows = self._all()
            if len(rows) >= MAX_SNAPSHOTS:
                _fail("observation_store_limit")
            record_hash, linked = _digest(result["records"]), None
            status, historical = "unverified_historical", True
            if result["data_source"] == "live":
                reliable = (result.get("captured_at") is not None and result.get("checked_at") is not None and
                    result.get("source_alias") is not None and result.get("status") in {"complete_within_scope", "partial_acquisition"})
                status, historical = ("observed_live", False) if reliable else ("live_metadata_incomplete", True)
            elif result["data_source"] == "historical_cache":
                captured = result.get("captured_at")
                candidates = [row for row in rows if row["observation_status"] == "observed_live" and
                    row["parser_version"] == parser_version and row["mapping_version"] == mapping_version and
                    row["records_sha256"] == record_hash and row["result"]["operation"] == result["operation"] and
                    row["result"]["target_term"] == result["target_term"] and captured is not None and
                    captured == result.get("cache_captured_at") == row["result"].get("captured_at") and
                    row["result"]["coverage"] == result["coverage"] and
                    (result.get("source_alias") is None or row["result"].get("source_alias") == result["source_alias"])]
                if len(candidates) == 1:
                    linked, status = candidates[0]["observation_id"], "linked_historical_cache"
            elif result["data_source"] == "none":
                status = "source_unavailable"
            identifier = "OBS-" + uuid.uuid4().hex
            snapshot = {"schema": SCHEMA, "observation_id": identifier, "scope_digest": self._scope_digest,
                "owner_student_id": self._scope["owner_student_id"], "security_realm": self._scope["security_realm"],
                "sequence": (rows[0]["sequence"] + 1) if rows else 1, "parser_version": parser_version,
                "mapping_version": mapping_version, "records_sha256": record_hash, "result": result,
                "observation_status": status, "historical_only": historical, "linked_observation_id": linked}
            raw = _json({"snapshot": snapshot, "signature": self._sign(snapshot)})
            self._authorization(self._scope)
            path = self.directory / (identifier + ".json")
            self._create(path, raw)
            try:
                self._authorization(self._scope)
            except PersonalObservationError:
                path.unlink(missing_ok=True)
                raise
            return self._summary(snapshot)

    def describe(self):
        with self._locked():
            rows = self._all()
            summaries = [self._summary(row) for row in rows]
            latest = {}
            for summary in summaries:
                latest.setdefault(summary["operation"], summary)
            result = {"operations": sorted(latest), "snapshots": summaries, "latest_observations": latest,
                      "data_kind": "personal_source", "local_only": True}
            self._authorization(self._scope)
            return result

    def _cursor(self, payload):
        raw = _json(payload)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") + "." + self._sign(payload)

    def _decode_cursor(self, cursor):
        try:
            if type(cursor) is not str or len(cursor) > 4096:
                raise ValueError
            body, signature = cursor.split(".")
            payload = json.loads(base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True))
            if (not re.fullmatch(r"[a-f0-9]{64}", signature) or not isinstance(payload, dict) or
                    set(payload) != {"scope", "observation_id", "snapshot_sha256", "operation", "query", "academic_year", "limit", "offset"} or
                    not hmac.compare_digest(signature, self._sign(payload)) or payload["scope"] != self._scope_digest or
                    type(payload["offset"]) is not int or payload["offset"] < 1):
                raise ValueError
            return payload
        except (ValueError, TypeError, UnicodeError):
            _fail("invalid_observation_cursor")

    @staticmethod
    def _query_values(body):
        operation, query, year, limit = body.get("operation"), body.get("query", ""), body.get("academic_year"), body.get("limit", 20)
        mode = body.get("mode", "records")
        if (type(operation) is not str or operation not in OPERATIONS or type(query) is not str or len(query) > 200 or "\x00" in query or
                year is not None and (type(year) is not str or not re.fullmatch(r"(?:19|20|21)[0-9]{2}(?:-(?:19|20|21)[0-9]{2})?", year)) or
                type(limit) is not int or not 1 <= limit <= 20 or
                type(mode) is not str or mode not in {"records", "summary"}):
            _fail("invalid_observation_request")
        return operation, normalize_record_query(query, operation), year, limit, mode

    @staticmethod
    def _matches(record, query, year, target_term=None):
        if query:
            states = record.get("field_states", {})
            normalize = lambda value: re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()
            needle = normalize(query)
            code_query = bool(re.fullmatch(r"(?=.*\d)[a-z0-9][a-z0-9_-]{4,39}", needle))
            known = lambda key: isinstance(states, dict) and states.get(key) == "known" and type(record.get(key)) in (str, int)
            code_match = known("course_code_raw") and needle == normalize(record["course_code_raw"])
            name_match = not code_query and known("course_name_raw") and needle in normalize(record["course_name_raw"])
            if not (code_match or name_match):
                return False
        if year is not None:
            states = record.get("field_states", {})
            value = record.get("academic_year_raw")
            state = states.get("academic_year_raw", "unknown") if isinstance(states, dict) else "unknown"
            # A term-scoped observation can establish the query's year without
            # manufacturing a value in the source row. Conflicting/known row
            # values remain authoritative; all-history observations never fill
            # missing row years from the question.
            scoped_year = target_term.rsplit("-", 1)[0] if isinstance(target_term, str) and re.fullmatch(
                r"(?:19|20|21)[0-9]{2}-(?:19|20|21)[0-9]{2}-(?:AW|SS|A|W|S|SU)", target_term) else None
            if scoped_year is not None and year not in {scoped_year, scoped_year[:4]}:
                return False
            scope_matches = state == "unknown" and scoped_year is not None and year in {scoped_year, scoped_year[:4]}
            if not scope_matches and (state != "known" or type(value) not in (str, int) or str(value) != year):
                return False
        return True

    def query(self, body):
        if type(body) is not dict or set(body) - QUERY_FIELDS or "operation" not in body:
            _fail("invalid_observation_request")
        with self._locked():
            values = dict(body)
            cursor = self._decode_cursor(values["cursor"]) if "cursor" in values else None
            if cursor and values.get("mode", "records") != "records":
                _fail("invalid_observation_cursor")
            if cursor:
                for key in ("operation", "observation_id", "query", "academic_year", "limit"):
                    supplied = values.get(key)
                    if key == "query" and type(supplied) is str:
                        supplied = normalize_record_query(supplied, cursor["operation"])
                    if key in values and supplied != cursor[key]:
                        _fail("invalid_observation_cursor")
                    values[key] = cursor[key]
            operation, query, year, limit, mode = self._query_values(values)
            if "observation_id" in values:
                snapshot = self._load(values["observation_id"])
            else:
                snapshot = next((row for row in self._all() if row["result"]["operation"] == operation), None)
                if snapshot is None:
                    _fail("inaccessible_observation")
            if snapshot["result"]["operation"] != operation:
                _fail("inaccessible_observation")
            snapshot_hash = _digest(snapshot)
            if cursor and cursor["snapshot_sha256"] != snapshot_hash:
                _fail("invalid_observation_cursor")
            records = snapshot["result"]["records"]
            filtered = [record for record in records if self._matches(record, query, year, snapshot["result"]["target_term"])]
            offset = cursor["offset"] if cursor else 0
            if offset > len(filtered):
                _fail("invalid_observation_cursor")
            selected = deepcopy(filtered[offset:offset + limit]) if mode == "records" else []
            next_cursor = self._cursor({"scope": self._scope_digest, "observation_id": snapshot["observation_id"],
                "snapshot_sha256": snapshot_hash, "operation": operation, "query": query,
                "academic_year": year, "limit": limit, "offset": offset + limit}) if mode == "records" and offset + limit < len(filtered) else None
            result = {**self._summary(snapshot), "records": selected, "total_records": len(records),
                "matched_records": len(filtered), "returned_records": len(selected), "next_cursor": next_cursor,
                "query": query, "academic_year": year, "offset": offset, "mode": mode,
                "issues": deepcopy(snapshot["result"].get("issues")),
                "row_failures": deepcopy(snapshot["result"].get("row_failures")), "failure": snapshot["result"].get("failure")}
            if operation in {"academic", "major_academic"}:
                result["source_grade_summary"] = source_grade_summary(filtered, snapshot["result"]["coverage"])
            for key in ("period_times", "period_times_source"):
                if key in snapshot["result"]:
                    result[key] = deepcopy(snapshot["result"][key])
            self._authorization(self._scope)
            return result
