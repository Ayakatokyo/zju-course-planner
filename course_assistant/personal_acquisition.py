"""Prepare bounded source dependencies for a validated recommendation task.

The model cannot select an identity, URL or arbitrary source. Existing school
services still authorize each request and own transport, caching and coverage.
"""
from copy import deepcopy
import re
import time


AUTH_FAILURES = frozenset({"permission_denied", "unauthenticated", "source_authorization_changed",
    "source_session_expired", "source_generation_changed", "source_subject_changed", "school_connection_expired",
    "personal_scope_changed", "personal_input_changed", "personal_model_changed", "grant_revoked", "grant_expired"})
MAX_REQUEST_SCOPES = 6


def _error_code(error):
    code = getattr(error, "reason", getattr(error, "code", "source_unavailable"))
    return code if type(code) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "source_unavailable"


def recommendation_source_requests(task):
    """Plan the smallest registered-source profile for an explicit task.

    This is intent-driven, rather than a second keyword parser. Current
    offerings, qualification, recognition and calendar sources are deliberately
    absent: none can be manufactured from a personal timetable or a PDF read.
    The curriculum query is only a bounded probe that obtains this turn's PDF;
    it is not evidence that all requested courses or requirements were found.
    """
    from .personal_tasks import validate_recommendation_task
    task = validate_recommendation_task(task)
    requests = [
        {"operation": "major_academic", "target_term": "ALL-HISTORY", "tool": "query_my_records",
         "arguments": {"operation": "major_academic", "target_term": "ALL-HISTORY", "query": "", "mode": "summary", "limit": 1}},
        {"operation": "curriculum", "target_term": None, "tool": "search_my_curriculum",
         "arguments": {"query": task["course_scope"]["queries"][0], "limit": 3}},
    ]
    if task["recommendation_kind"] == "timetable":
        term = task["target_term"]
        requests.append({"operation": "timetable", "target_term": term, "tool": "query_my_records",
            "arguments": {"operation": "timetable", "target_term": term, "query": "", "mode": "records", "limit": 15}})
        if task["check_exam_conflicts"]:
            for season in {"AW": ("A", "W"), "SS": ("S", "SU")}[term[-2:]]:
                exam_term = term[:-2] + season
                requests.append({"operation": "exams", "target_term": exam_term, "tool": "query_my_records",
                    "arguments": {"operation": "exams", "target_term": exam_term, "query": "", "mode": "records", "limit": 1}})
    return requests


def prepare_recommendation_sources(task, *, receipts, execute):
    """Execute a bounded plan through the existing authorized live tools.

    ``receipts`` is the server's current-turn list. Any prior attempt for the
    exact scope, including failure, suppresses another read in this turn. Tool
    results from newly executed queries are returned for the existing evidence
    projector to put their bounded facts into this turn's model context. A
    receipt alone never supplies facts or revives a prior snapshot.
    The callback retains the school connection lock and input-version adoption.
    """
    if not isinstance(receipts, list) or not callable(execute):
        raise ValueError("invalid_recommendation_preparation")
    requests = recommendation_source_requests(task)
    attempted = {(row.get("operation"), row.get("target_term")) for row in receipts
                 if isinstance(row, dict) and isinstance(row.get("operation"), str)
                 and (row.get("target_term") is None or isinstance(row.get("target_term"), str))}
    planned = {(row["operation"], row["target_term"]) for row in requests}
    record_scopes = {scope for scope in attempted | planned if scope[0] != "curriculum" and scope[1] is not None}
    if len(record_scopes) > MAX_REQUEST_SCOPES:
        raise ValueError("recommendation_source_limit_exceeded")
    prepared = []
    for request in requests:
        scope = (request["operation"], request["target_term"])
        if scope in attempted:
            continue
        attempted.add(scope)
        try:
            result = execute(request["tool"], deepcopy(request["arguments"]))
            if not isinstance(result, dict):
                raise ValueError("invalid_source_result")
        except Exception as error:
            if isinstance(error, PermissionError) or _error_code(error) in AUTH_FAILURES:
                raise
            if not any(isinstance(row, dict) and (row.get("operation"), row.get("target_term")) == scope
                       for row in receipts):
                receipts.append({"operation": scope[0], "target_term": scope[1], "status": "failed",
                    "data_source": "none", "observation_id": None, "checked_at": time.time(),
                    "coverage": {}, "ready": False, "failure": _error_code(error)})
            # Registered live tools preserve their failed read receipt. A PDF
            # extraction/query error may follow a successful source acquisition;
            # the later check reports source status, not semantic readiness.
            continue
        prepared.append({"name": request["tool"], "arguments": deepcopy(request["arguments"]),
            "result": deepcopy(result)})
    return prepared
