"""Explicitly configured, read-only local course-name/code reference service.

Only the shared ``courses`` projection is read. Personal observation registries,
curriculum PDFs, candidate sections and synthetic fixtures are never fallbacks.
Callers configure the delivery file on the server; model tool arguments cannot
select files. Public results deliberately omit local paths and source IDs.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata


ENVIRONMENT_KEY = "COURSE_CATALOG_DELIVERY_INDEX"
SCHEMA = "course-offline-dedup/1"
DATA_KIND = "local_course_reference"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_COURSES_BYTES = 32 * 1024 * 1024
MAX_COURSES = 50000
MAX_NAME_VARIANTS = 10
HASH = re.compile(r"[a-f0-9]{64}\Z")
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}\Z")
TERM = re.compile(r"((?:19|20|21)\d{2})-((?:19|20|21)\d{2})-[12]\Z")
PRIVATE_TEXT = re.compile(
    r"(?i)(?:/Users/|/private/|/var/|/home/|file://|https?://|"
    r"\b(?:password|cookie|token|api[_ -]?key|owner_student_id)\s*[:=]|"
    r"\bsk[-_][a-z0-9_-]{12,})")
SOURCE_DESCRIPTION = "本机离线采集课程名称与代码参考；不是当前开课清单或本人可选课程认定。"
UNKNOWN = ("stable_course_identity_not_verified", "current_offering_not_verified",
           "personal_eligibility_not_verified", "observed_at_not_provided")


class CourseCatalogError(ValueError):
    """Safe machine-readable errors; never include a configured local path."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise CourseCatalogError(code)


def _json(path, maximum, expected=None):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(maximum + 1)
    except (OSError, ValueError):
        _fail("course_catalog_source_unavailable")
    if len(raw) > maximum:
        _fail("course_catalog_source_invalid")
    if expected is not None and sha256(raw).hexdigest() != expected:
        _fail("course_catalog_integrity_failed")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        _fail("course_catalog_source_invalid")
    return value, raw


def _safe_text(value, maximum=180):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or PRIVATE_TEXT.search(value)):
        _fail("course_catalog_source_invalid")
    return value.strip()


def _normal(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class CourseCatalogService:
    """Bounded server-side access to one explicitly selected delivery version."""

    def __init__(self, delivery_index=None):
        configured = os.environ.get(ENVIRONMENT_KEY) if delivery_index is None else delivery_index
        self._path = None
        self._configuration_error = False
        if configured is not None and configured != "":
            if not isinstance(configured, (str, os.PathLike)):
                self._configuration_error = True
            else:
                try:
                    self._path = Path(configured).expanduser().absolute()
                except (OSError, ValueError, TypeError):
                    self._configuration_error = True

    @staticmethod
    def _unavailable(reason):
        return {"status": "not_ready", "data_kind": DATA_KIND, "type": DATA_KIND,
                "reason": reason, "source_description": SOURCE_DESCRIPTION,
                "records": [], "unknown": list(UNKNOWN)}

    def _load(self):
        if self._configuration_error:
            _fail("course_catalog_configuration_invalid")
        if self._path is None:
            _fail("course_catalog_not_configured")
        delivery, _ = _json(self._path, MAX_METADATA_BYTES)
        if (not isinstance(delivery, dict) or not isinstance(delivery.get("dataset_manifest"), str)
                or not delivery["dataset_manifest"].strip()
                or not isinstance(delivery.get("dataset_version"), str)
                or not HASH.fullmatch(delivery["dataset_version"])):
            _fail("course_catalog_source_invalid")
        manifest_path = Path(delivery["dataset_manifest"])
        if not manifest_path.is_absolute():
            manifest_path = self._path.parent / manifest_path
        manifest_path = manifest_path.resolve()
        expected_manifest = delivery.get("dataset_manifest_sha256")
        if expected_manifest is not None and (not isinstance(expected_manifest, str) or not HASH.fullmatch(expected_manifest)):
            _fail("course_catalog_source_invalid")
        manifest, manifest_raw = _json(manifest_path, MAX_METADATA_BYTES, expected_manifest)
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA
                or manifest.get("security_realm") != "restricted_reference"
                or manifest.get("data_kind") != "derived_real_reference_observations"
                or manifest.get("shared_projection_contains_personal_state") is not False):
            _fail("course_catalog_source_invalid")
        if manifest.get("dataset_version") != delivery["dataset_version"]:
            _fail("course_catalog_integrity_failed")
        files = manifest.get("files")
        entry = files.get("courses") if isinstance(files, dict) else None
        counts = manifest.get("counts")
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str) or not HASH.fullmatch(entry["sha256"])
                or type(entry.get("records")) is not int or not 0 <= entry["records"] <= MAX_COURSES
                or not isinstance(counts, dict) or type(counts.get("courses")) is not int
                or counts["courses"] != entry["records"]):
            _fail("course_catalog_source_invalid")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            _fail("course_catalog_source_invalid")
        course_path = (manifest_path.parent / relative).resolve()
        if not course_path.is_relative_to(manifest_path.parent):
            _fail("course_catalog_source_invalid")
        rows, courses_raw = _json(course_path, MAX_COURSES_BYTES, entry["sha256"])
        if not isinstance(rows, list) or len(rows) != entry["records"]:
            _fail("course_catalog_integrity_failed")
        batch_date = None
        try:
            if isinstance(delivery.get("date"), str):
                batch_date = date.fromisoformat(delivery["date"]).isoformat()
        except ValueError:
            pass
        source_version = "CATALOG-" + sha256(manifest_raw + b"\0" + courses_raw).hexdigest()
        projected, seen = [], set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("code"), str)
                    or not CODE.fullmatch(row["code"])
                    or row.get("code_namespace") != "zju_undergraduate_selection_primary_code"
                    or not isinstance(row.get("definitions"), list) or len(row["definitions"]) > 100):
                _fail("course_catalog_source_invalid")
            code = row["code"]
            if code.casefold() in seen:
                _fail("course_catalog_source_invalid")
            seen.add(code.casefold())
            names, terms = set(), set()
            for definition in row["definitions"]:
                if not isinstance(definition, dict) or not isinstance(definition.get("fields"), dict):
                    _fail("course_catalog_source_invalid")
                field = definition["fields"].get("name")
                if not isinstance(field, dict) or field.get("state") not in {"known", "unknown", "conflicting"}:
                    _fail("course_catalog_source_invalid")
                if field["state"] == "known":
                    names.add(_safe_text(field.get("value")))
                elif field["state"] == "conflicting":
                    assertions = field.get("assertions", [])
                    if not isinstance(assertions, list) or len(assertions) > 100:
                        _fail("course_catalog_source_invalid")
                    for assertion in assertions:
                        if not isinstance(assertion, dict):
                            _fail("course_catalog_source_invalid")
                        names.add(_safe_text(assertion.get("value")))
                term = definition.get("term")
                match = TERM.fullmatch(term) if isinstance(term, str) else None
                if match and int(match[2]) == int(match[1]) + 1:
                    terms.add(term)
                if len(names) > MAX_NAME_VARIANTS:
                    _fail("course_catalog_source_invalid")
            ordered_names = sorted(names)
            projected.append({"course_code": code, "course_name": ordered_names[0] if len(names) == 1 else None,
                "course_name_variants": ordered_names,
                "fields": {"course_code": "known", "course_name": "known" if len(names) == 1 else "conflicting" if names else "unknown"},
                "coverage": {"target_terms": sorted(terms), "batch_date": batch_date, "observed_at": None,
                    "observed_at_status": "unknown", "stable_course_identity_verified": False,
                    "current_offering_verified": False, "personal_eligibility_verified": False},
                "source_version": source_version})
        summary = {"status": "ready", "data_kind": DATA_KIND, "type": DATA_KIND,
            "source_version": source_version, "source_description": SOURCE_DESCRIPTION,
            "coverage": {"available_courses": len(projected), "scope": "offline_observed_courses_only",
                "complete_for_school": False, "batch_date": batch_date, "observed_at": None,
                "observed_at_status": "unknown", "current_offering_verified": False,
                "personal_eligibility_verified": False}, "unknown": list(UNKNOWN)}
        return summary, projected

    def describe(self):
        try:
            summary, _ = self._load()
            return summary
        except CourseCatalogError as error:
            return self._unavailable(error.code)
        except (OSError, ValueError, TypeError, RecursionError):
            return self._unavailable("course_catalog_source_invalid")

    def search(self, query, limit=5):
        if (not isinstance(query, str) or not 0 < len(query.strip()) <= 200
                or any(ord(char) < 32 or ord(char) == 127 for char in query)
                or type(limit) is not int or not 1 <= limit <= 10):
            _fail("invalid_course_catalog_query")
        try:
            summary, rows = self._load()
        except CourseCatalogError as error:
            return self._unavailable(error.code)
        except (OSError, ValueError, TypeError, RecursionError):
            return self._unavailable("course_catalog_source_invalid")
        normalized = _normal(query)
        keywords = [item for item in re.split(r"[\s,，、;；]+", normalized) if item]
        code_tokens = set(re.findall(r"[a-z0-9][a-z0-9_.-]*", normalized))
        ranked = []
        for row in rows:
            code, names = _normal(row["course_code"]), [_normal(name) for name in row["course_name_variants"]]
            exact_code = normalized == code or code in code_tokens
            partial_code = bool(CODE.fullmatch(normalized)) and len(normalized) >= 2 and normalized in code
            name_match = bool(keywords) and any(all(keyword in name for keyword in keywords) for name in names)
            if exact_code or partial_code or name_match:
                rank = 0 if exact_code else 1 if any(normalized == name for name in names) else 2 if name_match else 3
                ranked.append((rank, code, row))
        ranked.sort(key=lambda item: item[:2])
        selected = [deepcopy(row) for _, _, row in ranked[:limit]]
        return {**summary, "status": "ok" if selected else "no_match_in_scope", "records": selected,
            "matched_records": len(ranked), "returned_records": len(selected), "has_more": len(ranked) > len(selected),
            "retrieval_mode": "exact_code_or_name_keywords", "query_scope": "configured_local_course_catalog"}
