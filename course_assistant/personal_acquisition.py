"""Plan bounded read-only source preparation from a submitted user question.

The model cannot select an identity, URL or arbitrary source. Existing school
services still authorize each request and own transport, caching and coverage.
"""
from copy import deepcopy
import re
import time

from .chat_runs import tool_event_fields
from .recommendation_readiness import source_receipt_diagnostics


GRADE = re.compile(r"成绩|绩点|[Gg][Pp][Aa]|分数|平均分|挂科|修读记录")
PLAN = re.compile(r"培养|先修|课程要求|毕业要求|毕业.{0,8}学分|专业选修|专业必修|通识.{0,8}要求")
TIMETABLE = re.compile(r"课表|上课(?:时间|安排|地点)|课程表")
EXAMS = re.compile(r"考试|期中考|期末考|考场")
YEAR = re.compile(r"((?:19|20|21)\d{2})\s*[-—–~～至]\s*((?:19|20|21)\d{2})")
RELATIVE_TERM = re.compile(r"上学期|下学期|本学期|这学期|当前学期|上一学期|下一学期|去年|今年|明年|本学年|上学年|下学年")
PREVIOUS_RUN = re.compile(r"(?:上一轮|上轮|前一轮|刚刚|刚才|上次).{0,40}(?:运行|执行|调用|工具|脚本|日志|失败|错误)|(?:运行|执行|调用|工具|脚本).{0,20}(?:上一轮|上轮|前一轮|刚刚|刚才|上次)")
AUTH_FAILURES = frozenset({"permission_denied", "unauthenticated", "source_authorization_changed",
    "source_session_expired", "source_generation_changed", "source_subject_changed", "school_connection_expired",
    "personal_scope_changed", "personal_input_changed", "personal_model_changed", "grant_revoked", "grant_expired"})
MAX_REQUEST_SCOPES = 6
SOURCE_DOMAINS = ((GRADE, "academic"), (TIMETABLE, "timetable"), (EXAMS, "exams"), (PLAN, "curriculum"))
SCOPE_BOUNDARY = re.compile(r"以及|同时|另外|还有|并且|然后|[和与及、，,；;。！？\n]")


def _seasons(text, operation):
    if operation == "exams":
        result = [code for name, code in (("秋", "A"), ("冬", "W"), ("春", "S"), ("夏", "SU")) if name in text]
        if not result and re.search(r"第?[一1]学期|上学期", text):
            result = ["A", "W"]
        if not result and re.search(r"第?[二2]学期|下学期", text):
            result = ["S", "SU"]
        return result
    result = []
    if re.search(r"秋|冬|第?[一1]学期|上学期", text):
        result.append("AW")
    if re.search(r"春|夏|第?[二2]学期|下学期", text):
        result.append("SS")
    return result


def _source_question_parts(question):
    """Separate differently scoped domains without expanding coordinated dates.

    Shared leading/trailing qualifiers remain together. A different domain with
    its own time qualifier starts a separate clause; a clause without a year
    must still clarify rather than borrowing a year from the other domain.
    This is a bounded syntax aid, not a general natural-language scope parser.
    """
    def domains(text):
        return {operation for pattern, operation in SOURCE_DOMAINS if pattern.search(text)}

    def has_time(text):
        return bool(YEAR.search(text) or RELATIVE_TERM.search(text) or _seasons(text, "academic"))

    for boundary in SCOPE_BOUNDARY.finditer(question):
        left, right = question[:boundary.start()], question[boundary.end():]
        left_domains, right_domains = domains(left), domains(right)
        right_head = SCOPE_BOUNDARY.split(right, maxsplit=1)[0]
        if not left_domains or not right_domains or left_domains == right_domains or not has_time(right_head):
            continue
        first_domain = min(match.start() for pattern, _ in SOURCE_DOMAINS
                           if (match := pattern.search(right)) is not None)
        if not has_time(left) and not has_time(right[:first_domain]):
            # “成绩和课表比较2023-2024秋冬和2024-2025春夏”
            # has a common trailing scope, not a separate scope for each label.
            continue
        return _source_question_parts(left) + _source_question_parts(right)
    return [question]


def source_requests(question):
    """Return server-planned source scopes, with no I/O or clock assumptions.

    Normal items contain operation/target_term. An item with a ``clarification``
    code and null term is a blocked scope, never a fetch request. Curriculum is
    a separate single action with a null term. At most six data scopes and one
    curriculum action are planned; an oversized question is not silently cut.
    """
    if not isinstance(question, str):
        raise ValueError("invalid_question")
    if PREVIOUS_RUN.search(question) and not re.search(r"重新|重查|刷新|最新|再查", question):
        return []
    operations = [operation for pattern, operation in SOURCE_DOMAINS[:-1]
                  if pattern.search(question)]
    parts = _source_question_parts(question)
    requests = []
    for pattern, operation in SOURCE_DOMAINS[:-1]:
        for part in parts:
            if not pattern.search(part):
                continue
            for item in _domain_source_requests(part, operation):
                if item not in requests:
                    requests.append(item)
    if sum(item["target_term"] is not None for item in requests) > MAX_REQUEST_SCOPES:
        requests = [{"operation": operation, "target_term": None, "clarification": "source_scope_limit_exceeded"}
                    for operation in operations]
    if PLAN.search(question):
        requests.append({"operation": "curriculum", "target_term": None})
    return requests


def _domain_source_requests(question, operation):
    years = list(YEAR.finditer(question))
    if any(int(match[2]) != int(match[1]) + 1 for match in years):
        return [{"operation": operation, "target_term": None, "clarification": "invalid_academic_year"}]
    if not years:
        if operation == "academic" and not RELATIVE_TERM.search(question) and not _seasons(question, operation):
            return [{"operation": "major_academic", "target_term": "ALL-HISTORY"}]
        return [{"operation": operation, "target_term": None, "clarification": "academic_year_required"}]
    requests = []
    for index, match in enumerate(years):
        end = years[index + 1].start() if index + 1 < len(years) else len(question)
        segment = question[match.end():end]
        if index == 0:
            segment = question[:match.start()] + segment
        seasons = _seasons(segment, operation)
        if not seasons and index + 1 < len(years) and re.fullmatch(r"[\s、,，和与及或对比比较学年年度]*", question[match.end():end]):
            # A shared trailing season applies to coordinated years only.
            seasons = _seasons(question[years[-1].end():], operation)
        if not seasons:
            if operation == "academic":
                seasons = ["AW", "SS"]
            else:
                requests.append({"operation": operation, "target_term": None, "clarification": "season_required"})
                continue
        requests.extend({"operation": operation, "target_term": f"{match[1]}-{match[2]}-{season}"}
                        for season in seasons)
    return requests


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


def prepare_sources(school, context, question, *, authorize, emit):
    """Refresh each relevant source through its authorized school API.

    A stored observation is evidence of a previous read, never a freshness
    decision. Failed or clarification receipts survive so the caller can block
    fallback to a previous snapshot. Authorization failures still stop the turn.
    """
    results = []

    def read(name, action, ready, arguments):
        authorize()
        fields = tool_event_fields(name, arguments, initiated_by="service_acquisition")
        emit("tool.start", fields)
        started = time.monotonic()
        try:
            result = action()
            if not isinstance(result, dict):
                raise ValueError("invalid_source_result")
        except Exception as error:
            code = _error_code(error)
            emit("tool.end", {**fields, "status": "failed", "error_code": code,
                "elapsed_ms": round((time.monotonic() - started) * 1000)})
            authorize()
            if isinstance(error, PermissionError) or code in AUTH_FAILURES:
                raise
            return {"status": "failed", "data_source": "none", "failure": code}, False
        try:
            authorize()
        except Exception as error:
            emit("tool.end", {**fields, "status": "failed", "error_code": _error_code(error),
                "elapsed_ms": round((time.monotonic() - started) * 1000)})
            raise
        usable = ready(result)
        emit("tool.end", {**tool_event_fields(name, arguments, result, initiated_by="service_acquisition"),
            "status": "completed" if usable else "not_ready",
            "elapsed_ms": round((time.monotonic() - started) * 1000)})
        return result, usable

    for request in source_requests(question):
        operation, term = request["operation"], request["target_term"]
        if request.get("clarification"):
            results.append({**request, "status": "needs_clarification", "observation_id": None,
                "data_source": "none", "checked_at": None, "coverage": {}, "ready": False})
            continue
        if operation == "curriculum":
            result, ready = read("read_my_curriculum", lambda: school.acquire_plan(context, force=True),
                lambda result: result.get("status") == "acquired_pending_review" and result.get("data_source") == "live",
                {"operation": "curriculum"})
        else:
            result, ready = read("read_my_" + operation,
                lambda operation=operation, term=term: school.fetch(context,
                    {"operation": operation, "target_term": term, "force_refresh": True}),
                lambda result: result.get("status") in {"complete_within_scope", "partial_acquisition"}
                    and result.get("data_source") == "live" and isinstance(result.get("observation_id"), str),
                {"operation": operation, "target_term": term})
        receipt = {**request, "status": result.get("status", "not_ready"),
            "observation_id": result.get("observation_id"), "data_source": result.get("data_source", "none"),
            "checked_at": result.get("checked_at", time.time()),
            "coverage": deepcopy(result.get("coverage")) if isinstance(result.get("coverage"), dict) else {}, "ready": ready}
        for key in ("failure", "source_alias", "sha256"):
            if key in result:
                receipt[key] = result[key]
        if ready and (diagnostics := source_receipt_diagnostics(operation, result)) is not None:
            receipt["diagnostics"] = diagnostics
        results.append(receipt)
    return results
