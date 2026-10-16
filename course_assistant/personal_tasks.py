"""Bounded task hints from one caller-authorized conversation turn.

This pure projection retains executed query intent and normalized scope only.
It never reads sources, authorizes a request, or makes old facts current. The
caller must verify conversation ownership and discard hints after a connection,
model or egress-policy change before sending them to a model.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import unicodedata


MAX_TASKS = 6
MAX_TOOL_RESULTS = 64
MAX_CONTEXT_BYTES = 10000
MAX_CLARIFICATION_BYTES = 12000
MAX_SUPPLEMENTS = 3
OPERATIONS = frozenset({"academic", "major_academic", "timetable", "exams"})
RECORD_SUCCESS = frozenset({"complete_within_scope", "partial_acquisition"})
SEARCH_SUCCESS = {
    "search_course_catalog": frozenset({"ok", "no_match_in_scope"}),
    "search_my_curriculum": frozenset({"pending_review", "no_supported_answer_in_scope"}),
}
_TERM = re.compile(r"((?:19|20|21)\d{2})-((?:19|20|21)\d{2})-(AW|SS|A|W|S|SU)\Z")
_PRIVATE = re.compile(
    r"(?i)(?:https?://|file://|/(?:Users|private|home|var|tmp)/|"
    r"(?:password|passwd|cookie|set-cookie|token|ticket|authorization|credential|"
    r"csrf|secret|api[_ -]?key|sessionid|jsessionid|密码|口令)\s*(?:[:=：]|是|为|\bis\b)\s*\S+|"
    r"(?:owner_student_id|source_subject_id|security_realm)\"?\s*[:=]|"
    r"(?:学号|账号|身份证号?)\s*(?:[:=：]|是|为)?\s*[a-z0-9]{5,}|"
    r"(?<!\d)\d{17}[0-9x](?!\d)|"
    r"\b(?:Bearer|Basic)\s+|\bsk[-_][a-z0-9_-]{12,}|"
    r"\b(?:OBS|PAGE|PERSONAL-REF|SOURCE|TASK|PLAN)-[a-z0-9_-]{6,})")


def _query(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or
            not empty and not value.strip()):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if (any(unicodedata.category(char).startswith("C") for char in normalized)
            or _PRIVATE.search(normalized)):
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    return value


def _term(operation, value):
    if operation == "major_academic":
        return value == "ALL-HISTORY"
    match = _TERM.fullmatch(value) if isinstance(value, str) else None
    return bool(match and int(match[2]) == int(match[1]) + 1 and
                match[3] in ({"A", "W", "S", "SU"} if operation == "exams" else {"AW", "SS"}))


def _task(row):
    if not isinstance(row, dict) or row.get("error"):
        return None
    name, arguments, result = row.get("name"), row.get("arguments"), row.get("result")
    if (not isinstance(name, str) or not isinstance(arguments, dict) or not isinstance(result, dict)
            or result.get("query_executed") is False):
        return None
    if name == "query_my_records":
        if not isinstance(result.get("status"), str) or result["status"] not in RECORD_SUCCESS:
            return None
        operation = result.get("operation")
        term = result.get("target_term")
        mode = result.get("mode", arguments.get("mode", "records"))
        query = _query(arguments.get("query", ""), 100, empty=True)
        if (not isinstance(operation, str) or operation not in OPERATIONS or arguments.get("operation") != operation
                or not _term(operation, term) or query is None or not isinstance(mode, str) or mode not in {"records", "summary"}
                or "target_term" in arguments and arguments["target_term"] != term
                or "mode" in arguments and arguments["mode"] != mode):
            return None
        return {"tool": name, "operation": operation, "target_term": term, "query": query, "mode": mode}
    if name in SEARCH_SUCCESS and isinstance(result.get("status"), str) and result["status"] in SEARCH_SUCCESS[name]:
        query = _query(arguments.get("query"), 400 if name == "search_my_curriculum" else 200)
        if query is not None:
            return {"tool": name, "query": query}
    return None


def validate_task_context(value):
    """Validate the exact hint contract before it enters a model request.

    ``None`` means there is no preceding executable task. Invalid contexts
    fail with one safe error code; accepting arbitrary prior result fields
    here would bypass the producer's intent-only projection.
    """
    if value is None:
        return None

    def invalid():
        raise ValueError("invalid_task_context")

    if (type(value) is not dict or set(value) != {
            "source", "turn_status", "tasks", "authorization_granted",
            "facts_reusable", "fresh_sources_required", "tasks_truncated"}
            or value["source"] != "previous_turn_executed_tools"
            or type(value["turn_status"]) is not str or value["turn_status"] not in {"completed", "failed"}
            or value["authorization_granted"] is not False or value["facts_reusable"] is not False
            or value["fresh_sources_required"] is not True or type(value["tasks_truncated"]) is not bool
            or type(value["tasks"]) is not list or not 1 <= len(value["tasks"]) <= MAX_TASKS):
        invalid()
    seen = set()
    for task in value["tasks"]:
        if type(task) is not dict or type(task.get("tool")) is not str:
            invalid()
        tool = task["tool"]
        if tool == "query_my_records":
            if (set(task) != {"tool", "operation", "target_term", "query", "mode"}
                    or type(task["operation"]) is not str or task["operation"] not in OPERATIONS
                    or type(task["target_term"]) is not str or not _term(task["operation"], task["target_term"])
                    or type(task["mode"]) is not str or task["mode"] not in {"summary", "records"}
                    or type(task["query"]) is not str or _query(task["query"], 100, empty=True) is None):
                invalid()
        elif tool in SEARCH_SUCCESS:
            maximum = 400 if tool == "search_my_curriculum" else 200
            if (set(task) != {"tool", "query"} or type(task["query"]) is not str
                    or _query(task["query"], maximum) is None):
                invalid()
        else:
            invalid()
        identity = json.dumps(task, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if identity in seen:
            invalid()
        seen.add(identity)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_CONTEXT_BYTES:
        invalid()
    return deepcopy(value)


def previous_task_context(state):
    """Return a safe ``previous_task`` value, or ``None`` for no usable hint.

    Only the latest turn is inspected: a new topic, clarification without an
    executed query, or failure before tool success must not resurrect an older
    task. A failed latest turn can retain its successfully executed query intent
    while explicitly remaining failed. Pagination repeats collapse; at most six
    recent unique tasks survive. No result body, source ID, token, citation,
    count, grade, provider output or user message is copied.
    """
    turns = state.get("turns") if isinstance(state, dict) else None
    if not isinstance(turns, list) or not turns or not isinstance(turns[-1], dict):
        return None
    turn = turns[-1]
    status, rows = turn.get("status"), turn.get("tool_results")
    if not isinstance(status, str) or status not in {"completed", "failed"} or not isinstance(rows, list):
        return None
    tasks, seen = [], set()
    truncated = len(rows) > MAX_TOOL_RESULTS
    for row in reversed(rows[-MAX_TOOL_RESULTS:]):
        task = _task(row)
        if task is None:
            continue
        key = json.dumps(task, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        if len(tasks) >= MAX_TASKS:
            truncated = True
            continue
        tasks.append(task)
    if not tasks:
        return None
    value = {"source": "previous_turn_executed_tools", "turn_status": status,
             "tasks": list(reversed(tasks)), "authorization_granted": False,
             "facts_reusable": False, "fresh_sources_required": True,
             "tasks_truncated": truncated}
    while len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_CONTEXT_BYTES:
        value["tasks"].pop(0)
        value["tasks_truncated"] = True
    return validate_task_context(value) if value["tasks"] else None


def validate_clarification_context(value):
    """Validate a bounded request/clarification trail, never prior tool facts.

    Request text is user intent, and question is the actual validated assistant
    clarification. Both remain untrusted data. The caller still checks owner,
    connection and egress versions before using this context.
    """
    if value is None:
        return None
    def invalid():
        raise ValueError("invalid_clarification_context")
    if (type(value) is not dict or set(value) != {
            "source", "request", "supplements", "question", "previous_task",
            "authorization_granted", "facts_reusable", "fresh_sources_required"}
            or value["source"] != "previous_turn_clarification"
            or _query(value["request"], 2000) is None
            or _query(value["question"], 600) is None
            or type(value["supplements"]) is not list or len(value["supplements"]) > MAX_SUPPLEMENTS
            or any(_query(item, 2000) is None for item in value["supplements"])
            or value["authorization_granted"] is not False or value["facts_reusable"] is not False
            or value["fresh_sources_required"] is not True):
        invalid()
    try:
        validate_task_context(value["previous_task"])
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        invalid()
    if size > MAX_CLARIFICATION_BYTES:
        invalid()
    return deepcopy(value)


def previous_clarification_context(state):
    """Return only the latest turn's validated pending request, fail closed."""
    turns = state.get("turns") if isinstance(state, dict) else None
    if not isinstance(turns, list) or not turns or not isinstance(turns[-1], dict):
        return None
    turn = turns[-1]
    if (turn.get("status") != "completed" or turn.get("task_status") != "awaiting_user"
            or turn.get("reply_origin") != "personal_model_clarification"):
        return None
    try:
        return validate_clarification_context(turn.get("pending_clarification"))
    except ValueError:
        return None


def next_clarification_context(message, response, *, previous=None, task_context=None):
    """Retain a validated question only when the model actually asks for input.

    The model explicitly distinguishes continuing a pending task from starting
    a new request. A completion, failure or topic change never revives an older
    task. Exhausted or sensitive trails are omitted, not silently truncated.
    """
    if (not isinstance(response, dict) or response.get("status") != "completed"
            or response.get("reply_origin") != "personal_model_clarification"):
        return None
    decision = response.get("model_decision")
    if not isinstance(decision, dict) or decision.get("kind") != "clarify":
        return None
    basis = decision.get("request_basis", "current_message")
    try:
        previous = validate_clarification_context(previous)
        task_context = validate_task_context(task_context)
        if basis == "pending_task":
            if previous is None:
                return None
            request, supplements, task = previous["request"], previous["supplements"] + [message], previous["previous_task"]
        elif basis == "current_message":
            request, supplements, task = message, [], task_context
        else:
            return None
        return validate_clarification_context({"source": "previous_turn_clarification",
            "request": request, "supplements": supplements, "question": response.get("reply"),
            "previous_task": task, "authorization_granted": False, "facts_reusable": False,
            "fresh_sources_required": True})
    except ValueError:
        return None
