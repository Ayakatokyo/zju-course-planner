"""Observed code pairs from shared definitions, without identity or rule promotion."""
from __future__ import annotations

import re


_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}\Z")
_TERM = re.compile(r"((?:19|20|21)\d{2})-((?:19|20|21)\d{2})-[12]\Z")
_LONG_DIGITS = re.compile(r"\d{11,}")
_PRIVATE_WORDS = re.compile(r"password|cookie|token|authorization|session", re.I)


def _code(value):
    return (isinstance(value, str) and bool(_CODE.fullmatch(value))
            and not _LONG_DIGITS.search(value) and not _PRIVATE_WORDS.search(value)
            and (not value.isdecimal() or 6 <= len(value) <= 10))


def _paired_code(display, primary):
    if not isinstance(display, str) or len(display) > 124:
        return None
    for left, right in (("（", "）"), ("(", ")")):
        prefix = primary + left
        if display.startswith(prefix) and display.endswith(right):
            related = display[len(prefix):-1]
            if _code(related) and related.casefold() != primary.casefold():
                return related
    return None


def valid_observed_code_links(value, primary):
    """Validate the bounded public observation, including legacy empty lists."""
    return (type(primary) is str and type(value) is list and len(value) <= 1 and all(
        type(row) is dict and set(row) == {"code", "relation_kind", "verification_status", "source_field"}
        and _code(row["code"]) and row["code"].casefold() != primary.casefold()
        and row["relation_kind"] == "same_source_header" and row["verification_status"] == "observed"
        and row["source_field"] == "display_code" for row in value))


def observed_code_links(course_row, source_term):
    """Return consistent same-source display pairs for one explicit source term.

    A parenthesized code is an observation, not a verified old code, stable
    course identity, equivalence or personal curriculum/credit recognition.
    """
    term = _TERM.fullmatch(source_term) if isinstance(source_term, str) else None
    if (term is None or int(term[2]) != int(term[1]) + 1
            or not isinstance(course_row, dict) or not _code(course_row.get("code"))):
        return []
    primary = course_row["code"]
    definitions = course_row.get("definitions")
    if not isinstance(definitions, list) or not 1 <= len(definitions) <= 100:
        return []
    matches = [row for row in definitions if isinstance(row, dict) and row.get("term") == source_term]
    if not matches:
        return []
    related_codes = set()
    for row in matches:
        fields = row.get("fields")
        display = fields.get("display_code") if isinstance(fields, dict) else None
        if not isinstance(display, dict) or display.get("state") != "known":
            return []
        value = display.get("value")
        related = _paired_code(value, primary)
        if related is None:
            return []
        assertions = display.get("assertions", [])
        if (not isinstance(assertions, list) or len(assertions) > 100
                or any(not isinstance(assertion, dict) or assertion.get("value") != value
                       for assertion in assertions)):
            return []
        related_codes.add(related)
    if len(related_codes) != 1:
        return []
    return [{"code": related_codes.pop(), "relation_kind": "same_source_header",
             "verification_status": "observed", "source_field": "display_code"}]
