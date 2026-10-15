"""Plan bounded read-only source preparation from a submitted user question.

The model cannot select an identity, URL or arbitrary source. Existing school
services still authorize each request and own transport, caching and coverage.
"""
import re
import time


GRADE = re.compile(r"成绩|绩点|[Gg][Pp][Aa]|分数|平均分|挂科|修读记录")
PLAN = re.compile(r"培养|先修|课程要求|毕业要求|毕业.{0,8}学分|专业选修|专业必修|通识.{0,8}要求")


def _explicit_term(question):
    match = re.search(r"((?:19|20|21)\d{2})\s*[-—–~～至]\s*((?:19|20|21)\d{2})", question)
    if not match or int(match[2]) != int(match[1]) + 1:
        return None
    season = "AW" if re.search(r"秋|冬", question) else "SS" if re.search(r"春|夏", question) else None
    return f"{match[1]}-{match[2]}-{season}" if season else None


def prepare_sources(school, context, question, *, authorize, emit):
    """Acquire only a question's missing grade/curriculum source, at most two.

    A generic grade overview uses the school's all-history major-grade entry;
    its actual coverage remains authoritative and is never called all grades.
    Other unscoped timetable/exam questions are left for clarification.
    """
    local = school.local_chat_status(context).get("local", {})
    snapshots = local.get("observations", {}).get("snapshots", [])
    snapshots = snapshots if isinstance(snapshots, list) else []
    requests = []
    if GRADE.search(question):
        term = _explicit_term(question)
        operation = "major_academic" if "主修" in question or not term else "academic"
        target = "ALL-HISTORY" if operation == "major_academic" else term
        wanted = [item for item in snapshots if item.get("operation") == operation and item.get("target_term") == target]
        if not wanted:
            requests.append((operation, target))
    need_plan = bool(PLAN.search(question)) and local.get("plan", {}).get("status") != "acquired_pending_review"
    results = []

    def read(name, action, ready):
        authorize()
        emit("tool.start", {"tool_name": name})
        started = time.monotonic()
        try:
            result = action()
            authorize()
        except Exception as error:
            code = getattr(error, "reason", getattr(error, "code", "source_unavailable"))
            if type(code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
                code = "source_unavailable"
            emit("tool.end", {"tool_name": name, "status": "failed", "error_code": code,
                "elapsed_ms": round((time.monotonic() - started) * 1000)})
            raise
        emit("tool.end", {"tool_name": name, "status": "completed" if ready(result) else "not_ready",
            "record_count": len(result.get("records", [])),
            "elapsed_ms": round((time.monotonic() - started) * 1000)})
        return result

    for operation, term in requests:
        name = "read_my_" + operation
        # Authorization-generation and model checks happen before adoption of
        # this service-initiated input change. Caller refreshes input versions.
        result = read(name, lambda: school.fetch(context, {"operation": operation, "target_term": term}),
            lambda result: result.get("status") in {"complete_within_scope", "partial_acquisition"}
                or bool(result.get("records")))
        results.append({"operation": operation, "status": result.get("status")})
    if need_plan:
        result = read("read_my_curriculum", lambda: school.acquire_plan(context),
                      lambda result: result.get("status") == "acquired_pending_review")
        results.append({"operation": "curriculum", "status": result.get("status")})
    return results
