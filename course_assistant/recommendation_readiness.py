"""Read-only recommendation preparation from an authorized current-turn context.

The caller supplies its own get_my_context result after authorization. This
projection performs no I/O and grants no authority: source availability proves
only that a source was read, not that its identities, rules or eligibility were
verified. No input to this initial slice enables real recommendation or export.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re


SCHEMA_VERSION = "recommendation-readiness-v1"
_TERM = re.compile(r"((?:19|20|21)\d{2})-((?:19|20|21)\d{2})-(AW|SS)\Z")
_OPERATIONS = frozenset({"academic", "major_academic", "timetable", "exams"})
_READABLE = frozenset({"complete_within_scope", "partial_acquisition"})


def _term(value):
    match = _TERM.fullmatch(value) if isinstance(value, str) else None
    return bool(match and int(match[2]) == int(match[1]) + 1)


def _registered_operations(context):
    values = context.get("api_operations")
    if not isinstance(values, list):
        return set()
    return {operation for value in values[:32]
            if isinstance(operation := value.get("operation") if isinstance(value, dict) else value, str)
            and operation in _OPERATIONS}


def _source_state(context, operation, terms):
    """Use only this turn's safe receipts, never operations/snapshot metadata."""
    if context.get("status") not in ("available", "ok"):
        return "missing"
    values = context.get("source_receipts")
    if not isinstance(values, list):
        return "missing"
    rows = [value for value in values[:32] if isinstance(value, dict)
            and value.get("operation") == operation
            and (value.get("target_term") is None or isinstance(value.get("target_term"), str))
            and value.get("target_term") in terms]
    if not rows:
        return "missing"
    if any(value.get("status") == "needs_clarification" for value in rows):
        return "needs_clarification"
    if any(value.get("status") in ("failed", "source_unavailable") for value in rows):
        return "failed"
    # Conflicting or cached receipts cannot be rescued by another positive row.
    if any(value.get("ready") is not True or value.get("data_source") != "live" for value in rows):
        return "missing"
    if operation == "curriculum":
        return "available" if all(value.get("status") == "acquired_pending_review" for value in rows) else "missing"
    if any(not isinstance(value.get("status"), str) or value["status"] not in _READABLE for value in rows):
        return "missing"
    if any(value.get("status") == "partial_acquisition" for value in rows):
        return "partial"
    return "available"


def assess_recommendation_readiness(context, *, recommendation_kind="courses", target_term=None):
    """Return bounded source preparation and still-unverified business inputs.

    ``context`` is a server-owned current-turn get_my_context result, never a
    model/client assertion. ``target_term`` is an explicit AW/SS term; this
    function does not infer relative terms. API registration means a read can
    be attempted, not that it will succeed. It does not assume an offering-pool
    endpoint exists or treat an existing personal timetable as a candidate pool.
    """
    if not isinstance(context, dict):
        raise ValueError("invalid_readiness_context")
    if not isinstance(recommendation_kind, str) or recommendation_kind not in {"courses", "timetable"}:
        raise ValueError("invalid_recommendation_kind")
    if target_term is not None and not _term(target_term):
        raise ValueError("invalid_readiness_target_term")
    capabilities = context.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    active = context.get("status") in ("available", "ok")
    registered = _registered_operations(context)
    live = active and capabilities.get("live_record_query") is True
    catalog = context.get("course_catalog")
    catalog = catalog if isinstance(catalog, dict) else {}
    catalog_ready = active and catalog.get("status") == "ready" and capabilities.get("course_catalog_search") is True
    sources = [{"id": "course_reference", "status": "available" if catalog_ready else "missing",
                "query_supported": catalog_ready, "tool": "search_course_catalog",
                "scope": "local_names_codes_only"}]

    def records(identifier, operation, terms, *, query_term=None):
        supported = live and operation in registered
        source = {"id": identifier, "status": _source_state(context, operation, terms) if query_term else "missing",
                  "query_supported": supported, "tool": "query_my_records", "operation": operation}
        if query_term is not None:
            source["target_term"] = query_term
        if query_term is None:
            source["requires"] = "explicit_target_term"
        sources.append(source)

    # Recommendation needs prior study facts. A target-term transcript (often
    # empty for a future term) is not a substitute for the major-source history,
    # which itself still does not prove the student's complete learning history.
    records("academic_records", "major_academic", {"ALL-HISTORY"}, query_term="ALL-HISTORY")
    sources[-1]["scope"] = "major_source_only_not_complete_learning_history"
    sources.append({"id": "curriculum", "status": _source_state(context, "curriculum", {None}),
                    "query_supported": active and capabilities.get("curriculum_local_search") is True
                        and capabilities.get("read_only_acquisition") is True,
                    "tool": "search_my_curriculum", "scope": "document_fragments_pending_review"})

    dependencies = [
        {"id": "identity_and_study_facts", "status": "required_dependency_unverified",
         "missing": ["stable_course_and_attempt_identity", "completion_recognition_and_repeat_policy"]},
        {"id": "curriculum_applicability_and_rules", "status": "required_dependency_unverified",
         "missing": ["personal_plan_applicability", "reviewed_versioned_rules_and_course_mapping"]},
        {"id": "current_candidate_pool", "status": "required_dependency_unverified",
         "missing": ["bounded_current_offerings", "personal_eligibility_and_fresh_capacity"]},
    ]
    if recommendation_kind == "timetable":
        records("existing_timetable", "timetable", {target_term}, query_term=target_term)
        seasons = ({"AW": ("A", "W"), "SS": ("S", "SU")}[target_term[-2:]] if target_term else ())
        exam_terms = {target_term[:-2] + season for season in seasons} if target_term else {None}
        # Exam source scopes are individual seasons. Both are needed for the
        # half-year source overview; one successful season is only partial.
        exam_states = [_source_state(context, "exams", {term}) for term in sorted(exam_terms) if term is not None]
        exam_status = ("available" if exam_states and all(value == "available" for value in exam_states)
                       else "failed" if "failed" in exam_states
                       else "partial" if any(value in {"available", "partial"} for value in exam_states)
                       else "missing")
        sources.append({"id": "exams", "status": exam_status,
                        "query_supported": live and "exams" in registered, "tool": "query_my_records",
                        "operation": "exams", "target_terms": sorted(term for term in exam_terms if term is not None),
                        "scope": "existing_personal_exams_only"})
        dependencies.extend([
            {"id": "section_time_and_calendar", "status": "required_dependency_unverified",
             "missing": ["stable_section_identity", "full_meetings_and_week_sets", "exam_intervals", "calendar_period_mapping"]},
            {"id": "protected_arrangements", "status": "required_dependency_unverified",
             "missing": ["verified_formal_enrollment_mapping", "explicit_locks_and_fixed_arrangements"]},
        ])
    if target_term is None:
        next_step = "先明确目标学年和秋冬/春夏，再核对限定课程或模块；不推测当前学期。"
    elif recommendation_kind == "courses":
        next_step = "可先查询已有主修记录、培养条款和课程名称代码；主修来源不代表完整修读历史，仍需核实适用规则及限定候选开课、资格。"
    else:
        next_step = "可先查询该学期本人课表和考试作为已有安排；它们不是全量候选，还需核实候选班次及时间校历。"
    return {"schema_version": SCHEMA_VERSION, "status": "not_ready", "gate": "real_recommendation_not_ready",
            "recommendation_kind": recommendation_kind, "target_term": target_term,
            "can_generate": False, "can_save_or_export": False,
            "sources": sources, "dependencies": dependencies,
            "delivery_dependency": "real_draft_freeze_and_export_contract_not_connected",
            "next_step": next_step}


def project_readiness_result(result, arguments):
    """Admit only this request's fixed readiness contract and safe source states.

    The service may replace each source's availability and query-support flag.
    Dependencies, action gates, scope, tool names and next-step text must remain
    byte-for-byte equivalent JSON to the pure assessment template. No source
    body, identity, path, arbitrary error or model-supplied field can pass here.
    """
    def invalid():
        raise ValueError("invalid_readiness_result")

    if (type(arguments) is not dict or set(arguments) - {"recommendation_kind", "target_term"}
            or "recommendation_kind" not in arguments
            or "target_term" in arguments and type(arguments["target_term"]) is not str):
        invalid()
    try:
        expected = assess_recommendation_readiness({}, **arguments)
    except (ValueError, TypeError):
        invalid()
    if (type(result) is not dict or set(result) != {"status", "data_kind", "check_executed", "readiness"}
            or result["status"] != "available" or result["data_kind"] != "personal_source"
            or result["check_executed"] is not True or type(result["readiness"]) is not dict):
        invalid()
    readiness = result["readiness"]
    sources = readiness.get("sources")
    if type(sources) is not list or len(sources) != len(expected["sources"]):
        invalid()
    statuses = {"available", "missing", "partial", "failed", "needs_clarification"}
    for source, target in zip(sources, expected["sources"]):
        if (type(source) is not dict or type(source.get("status")) is not str or source["status"] not in statuses
                or type(source.get("query_supported")) is not bool):
            invalid()
        target.update(status=source["status"], query_supported=source["query_supported"])
    try:
        actual_json = json.dumps(readiness, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        expected_json = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        invalid()
    if actual_json != expected_json:
        invalid()
    return deepcopy(expected)


def render_recommendation_readiness(readiness, sections):
    """Render selected preparation facts from the strict contract, as plain text."""
    if (not isinstance(sections, list) or not 1 <= len(sections) <= 3 or
            any(not isinstance(section, str) or section not in {"sources", "dependencies", "next_step"} for section in sections)
            or len(set(sections)) != len(sections)):
        raise ValueError("invalid_readiness_sections")
    if not isinstance(readiness, dict):
        raise ValueError("invalid_readiness_result")
    arguments = {"recommendation_kind": readiness.get("recommendation_kind")}
    if readiness.get("target_term") is not None:
        arguments["target_term"] = readiness["target_term"]
    checked = project_readiness_result({"status": "available", "data_kind": "personal_source",
        "check_executed": True, "readiness": readiness}, arguments)
    kind = "课程推荐准备" if checked["recommendation_kind"] == "courses" else "选课课表准备"
    term = checked["target_term"]
    if term is None:
        scope = "目标学期尚未明确"
    else:
        year, season = term.rsplit("-", 1)
        scope = year + "学年" + {"AW": "秋冬", "SS": "春夏"}[season] + "学期"
    lines = [f"{kind}（{scope}）：检查已完成，真实推荐尚未就绪，方案生成、保存和导出均未开放。"]
    if "sources" in sections:
        names = {"course_reference": "本地课程名称与代码", "academic_records": "主修成绩来源",
                 "curriculum": "培养资料", "existing_timetable": "已有本人课表", "exams": "已有本人考试安排"}
        states = {"available": "可用", "partial": "部分取得", "failed": "本轮读取未完成",
                  "missing": "本轮尚未取得", "needs_clarification": "查询范围需澄清"}
        sources = []
        for source in checked["sources"]:
            state = states[source["status"]]
            if source["status"] == "missing" and source["query_supported"]:
                state += "，可按明确范围查询"
            sources.append(names[source["id"]] + "：" + state)
        lines.append("来源准备：" + "；".join(sources) + "。")
        lines.append("主修来源不代表完整修读历史。")
        if checked["recommendation_kind"] == "timetable":
            lines.append("已有课表和考试仅用于核对已有安排，不代表可选班次清单。")
    if "dependencies" in sections:
        names = {
            "identity_and_study_facts": "课程与修读身份、完成认定及重复修读政策",
            "curriculum_applicability_and_rules": "本人培养方案适用、审核后的规则与课程映射",
            "current_candidate_pool": "限定开课候选、本人资格与最新余量",
            "section_time_and_calendar": "教学班身份、完整上课周次、考试时段与校历",
            "protected_arrangements": "正式中选映射、明确锁定及固定安排",
        }
        lines.append("仍需核实：" + "；".join(names[item["id"]] for item in checked["dependencies"]) + "。")
        lines.append("真实方案的保存与导出契约仍待接通。")
    if "next_step" in sections:
        lines.append("接下来：" + checked["next_step"])
    return "\n".join(lines)
