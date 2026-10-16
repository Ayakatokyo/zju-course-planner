"""Plan bounded read-only source preparation from a submitted user question.

The model cannot select an identity, URL or arbitrary source. Existing school
services still authorize each request and own transport, caching and coverage.
"""
from copy import deepcopy
import re
import time

from .chat_runs import tool_event_fields


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
    operations = [operation for pattern, operation in ((GRADE, "academic"), (TIMETABLE, "timetable"), (EXAMS, "exams"))
                  if pattern.search(question)]
    years = list(YEAR.finditer(question))
    invalid_year = any(int(match[2]) != int(match[1]) + 1 for match in years)
    requests = []
    for operation in operations:
        if invalid_year:
            requests.append({"operation": operation, "target_term": None, "clarification": "invalid_academic_year"})
            continue
        if not years:
            if operation == "academic" and not RELATIVE_TERM.search(question) and not _seasons(question, operation):
                requests.append({"operation": "major_academic", "target_term": "ALL-HISTORY"})
            else:
                requests.append({"operation": operation, "target_term": None, "clarification": "academic_year_required"})
            continue
        for index, match in enumerate(years):
            end = years[index + 1].start() if index + 1 < len(years) else len(question)
            segment = question[match.end():end]
            if index == 0:
                segment = question[:match.start()] + segment
            seasons = _seasons(segment, operation)
            if not seasons and index + 1 < len(years) and re.fullmatch(r"[\s、,，和与及或对比比较学年年度]*", question[match.end():end]):
                # A shared trailing season in “2023-2024和2024-2025秋冬”
                # applies to the coordinated years, not to unrelated clauses.
                seasons = _seasons(question[years[-1].end():], operation)
            if not seasons:
                if operation == "academic":
                    seasons = ["AW", "SS"]
                else:
                    requests.append({"operation": operation, "target_term": None, "clarification": "season_required"})
                    continue
            for season in seasons:
                item = {"operation": operation, "target_term": f"{match[1]}-{match[2]}-{season}"}
                if item not in requests:
                    requests.append(item)
    if sum(item["target_term"] is not None for item in requests) > MAX_REQUEST_SCOPES:
        requests = [{"operation": operation, "target_term": None, "clarification": "source_scope_limit_exceeded"}
                    for operation in operations]
    if PLAN.search(question):
        requests.append({"operation": "curriculum", "target_term": None})
    return requests


def _error_code(error):
    code = getattr(error, "reason", getattr(error, "code", "source_unavailable"))
    return code if type(code) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) else "source_unavailable"


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
        results.append(receipt)
    return results
