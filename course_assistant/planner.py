"""Deterministic planning against an explicitly identified offering snapshot.

These functions produce planning drafts, never official enrollment or earned
credits. A known constraint failure dominates unknown inputs; unknown checks
remain in pending_plans and never enter the validated plans list.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json


CONFIRMED_BINDINGS = {
    "confirmed", "synthetic_confirmed", "student_confirmed",
    "official_record_verified",
}
PROTECTED_FIELDS = ("locked_section_ids", "preloaded_section_ids", "fixed_meetings")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _unique(values):
    result, seen = [], set()
    for value in values:
        key = fingerprint(value)
        if key not in seen:
            result.append(deepcopy(value))
            seen.add(key)
    return result


def revise_constraints(old, patch):
    """Revise personal preferences without removing a protected condition.

    Removing a protected item requires a future explicit unlock workflow. A
    shorter patch is retained as an attempted removal in revision_warnings.
    Neither omitted keys nor [] silently discard an existing lock.
    """
    result = deepcopy(old)
    warnings = []
    for key, value in patch.items():
        if key in PROTECTED_FIELDS:
            if not isinstance(value, list):
                raise ValueError(f"{key} 必须为列表；既有条件不会被删除")
            original = old.get(key, [])
            patch_keys = {fingerprint(item) for item in value}
            removed = [item for item in original if fingerprint(item) not in patch_keys]
            result[key] = _unique(original + value)
            if removed:
                warnings.append({"field": key, "retained": deepcopy(removed),
                                 "reason": "既有锁定/预置/固定安排保留，需显式解锁流程才能删除"})
        elif key not in ("revision", "revision_warnings"):
            result[key] = deepcopy(value)
    if result.get("friday_free", "off") not in {"off", "soft", "hard"}:
        raise ValueError("friday_free 仅支持 off、soft、hard")
    result["revision"] = int(old.get("revision", 0)) + 1
    result["revision_warnings"] = warnings
    return result


def confirmed_enrollment(wish_items):
    """Extract only source-backed selected/preloaded schedule membership.

    Saving a draft, submitting a wish, rank, or tentative state is insufficient.
    The original source state, reviewed status mapping and snapshot are retained.
    Contradictory evidence for one section is left unknown.
    """
    grouped = {}
    for item in wish_items or []:
        if isinstance(item, dict) and item.get("section_id"):
            grouped.setdefault(item["section_id"], []).append(item)
    confirmed, pending = [], []
    for section_id, items in sorted(grouped.items()):
        proofs = [item for item in items if (
            item.get("official_enrollment_status") in {"selected", "preloaded"}
            and item.get("confirmed_schedule_membership") is True
            and item.get("status_mapping_verified") is True
            and bool(item.get("official_enrollment_status_raw"))
            and bool(item.get("status_snapshot_id"))
            and bool(item.get("source_refs"))
        )]
        contradictions = [item for item in items if (
            item.get("status_mapping_verified") is True
            and item.get("official_enrollment_status") in {"cancelled", "expired", "tentative"}
            and item.get("source_refs")
        )]
        if proofs and not contradictions:
            confirmed.append({"section_id": section_id,
                              "evidence": deepcopy(proofs),
                              "data_kind": proofs[0].get("data_kind", "unknown")})
        else:
            pending.append({"section_id": section_id, "result": "unknown",
                            "reason": "正式结果证据冲突" if proofs and contradictions else "没有经核实的正式课表证据",
                            "items": deepcopy(items)})
    return {"section_ids": [item["section_id"] for item in confirmed],
            "confirmed": confirmed, "pending": pending,
            "earned_credits": None,
            "note": "正式课表成员不等于已获学分；模拟证据不属于学校真实中选记录"}


def _provenance(snapshot, student=None, constraints=None, academic_records=None):
    result = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_version": snapshot.get("version"),
        "snapshot_fingerprint": fingerprint(snapshot),
        "data_kind": snapshot.get("data_kind", snapshot.get("metadata", {}).get("data_kind", "unknown")),
        "academic_year": snapshot.get("academic_year"),
        "term_code": snapshot.get("term_code"),
        "captured_at": snapshot.get("captured_at"),
        "completeness": snapshot.get("completeness", "unknown"),
        "filter_scope": deepcopy(snapshot.get("filter_scope")),
        "enrollment_guaranteed": False,
    }
    if student is not None:
        result.update({"student_profile_version": student.get("profile_version"),
                       "student_fingerprint": fingerprint(student),
                       "plan_id": student.get("plan_id"),
                       "plan_version": student.get("plan_version")})
    if constraints is not None:
        result.update({"constraints_revision": constraints.get("revision"),
                       "constraints_fingerprint": fingerprint(constraints)})
    if academic_records is not None:
        result["academic_records_fingerprint"] = fingerprint(academic_records)
        result["academic_snapshot_ids"] = sorted({r.get("snapshot_id") for r in academic_records
                                                  if isinstance(r, dict) and r.get("snapshot_id")})
    return result


def _index(snapshot):
    index, duplicates, malformed = {}, [], []
    records = snapshot.get("records")
    if not isinstance(records, list):
        return {}, [], ["records 不是列表"]
    for position, section in enumerate(records):
        if not isinstance(section, dict) or not section.get("section_id") or not section.get("course_id"):
            malformed.append(position)
            continue
        section_id = section["section_id"]
        if section_id in index:
            duplicates.append(section_id)
        else:
            index[section_id] = section
    return index, sorted(set(duplicates)), malformed


def find_sections(snapshot, course_ids):
    """Find precise course identifiers; never merge by a similar course name."""
    course_ids = _unique(course_ids)
    index, duplicates, malformed = _index(snapshot)
    groups = {course_id: [] for course_id in course_ids}
    for section in sorted(index.values(), key=lambda item: item["section_id"]):
        if section["course_id"] in groups:
            groups[section["course_id"]].append(deepcopy(section))
    return {"by_course": groups,
            "sections": [section for items in groups.values() for section in items],
            "missing_course_ids": [course_id for course_id, items in groups.items() if not items],
            "duplicate_section_ids": duplicates, "malformed_records": malformed,
            "provenance": _provenance(snapshot)}


def _check(code, result, reason, section_ids=None, **extra):
    return {"code": code, "result": result, "reason": reason,
            "section_ids": list(section_ids or []), **extra}


def _overall(checks):
    if any(item["result"] == "fail" for item in checks):
        return "fail"
    if any(item["result"] == "unknown" for item in checks):
        return "unknown"
    return "pass"


def _meeting_state(meeting):
    if not isinstance(meeting, dict) or meeting.get("schedule_state") != "known":
        return "unknown"
    day = meeting.get("day_of_week")
    if type(day) is not int or day not in range(1, 8):
        return "unknown"
    for field in ("period_indices", "week_numbers"):
        values = meeting.get(field)
        if not isinstance(values, list) or not values or any(type(v) is not int or v < 1 for v in values):
            return "unknown"
    seasons = meeting.get("season_codes")
    if not isinstance(seasons, list) or not seasons or any(not isinstance(v, str) or not v for v in seasons):
        return "unknown"
    if not meeting.get("academic_calendar_id"):
        return "unknown"
    return "known"


def _overlap(left, right, left_term, right_term):
    if left_term and right_term and left_term != right_term:
        return "pass", "教学时段属于不同目标学期", {}
    if not left_term or not right_term:
        return "unknown", "学期未知，不能计算时段交集", {}
    if _meeting_state(left) != "known" or _meeting_state(right) != "known":
        return "unknown", "时段、实际周集合或校历解释缺失", {}
    if left["academic_calendar_id"] != right["academic_calendar_id"]:
        return "unknown", "校历版本不同，不能直接比较局部周号和节次", {}
    seasons = sorted(set(left["season_codes"]) & set(right["season_codes"]))
    weeks = sorted(set(left["week_numbers"]) & set(right["week_numbers"]))
    periods = sorted(set(left["period_indices"]) & set(right["period_indices"]))
    if seasons and weeks and periods and left["day_of_week"] == right["day_of_week"]:
        return "fail", "同学期、同校历的分学期、实际周次、星期与节次重叠", {
            "season_codes": seasons, "week_numbers": weeks,
            "day_of_week": left["day_of_week"], "period_indices": periods,
            "academic_calendar_id": left["academic_calendar_id"],
        }
    return "pass", "已核实的时段没有实际交集", {}


def _freshness(snapshot, constraints):
    state = snapshot.get("freshness_state")
    if state != "current":
        return "unknown", "快照已过期，需刷新余量" if state == "stale" else "快照新鲜度尚未确认"
    if not snapshot.get("captured_at"):
        return "unknown", "缺采集时间，不能确认余量观察时间"
    if snapshot.get("expires_at"):
        try:
            expires = datetime.fromisoformat(snapshot["expires_at"].replace("Z", "+00:00"))
            as_of_value = constraints.get("as_of")
            as_of = (datetime.fromisoformat(as_of_value.replace("Z", "+00:00"))
                     if as_of_value else datetime.now(timezone.utc))
            if expires.tzinfo is None or as_of.tzinfo is None:
                return "unknown", "快照有效期缺少时区"
            if expires <= as_of:
                return "unknown", "快照有效期已过，需刷新余量"
        except (ValueError, TypeError):
            return "unknown", "快照有效期不能解析"
    return "pass", "基于明确标记为current的快照核对，推荐不保证占位"


def _prerequisites(section, student, records):
    prerequisites = section.get("prerequisite_course_ids")
    if not isinstance(prerequisites, list):
        return "unknown", "先修要求未知；没有显示先修不代表没有要求"
    if not prerequisites:
        return "pass", "当前快照显式标记无先修要求"
    details = []
    for course_id in prerequisites:
        matching = [r for r in records if isinstance(r, dict) and r.get("course_id") == course_id]
        by_id, conflicting_ids = {}, set()
        def record_fact_key(record):
            return fingerprint({
                "student_key": record.get("student_key"), "plan_id": record.get("plan_id"),
                "course_id": record.get("course_id"),
                "completion_status": record.get("completion_status", record.get("normalized_status", record.get("status", "unknown"))),
                "recognition_status": record.get("recognition_status", "unknown"),
                "attempt_id": record.get("attempt_id"),
            })
        for record in matching:
            key = record.get("record_id", record.get("academic_record_id"))
            if key and key in by_id and record_fact_key(record) != record_fact_key(by_id[key]):
                conflicting_ids.add(key)
            if key:
                by_id[key] = record
        eligible, unknown = [], bool(conflicting_ids)
        for record in matching:
            if record.get("record_id", record.get("academic_record_id")) in conflicting_ids:
                continue
            if record.get("student_key") and student.get("student_key") and record["student_key"] != student["student_key"]:
                continue
            if record.get("plan_id") and student.get("plan_id") and record["plan_id"] != student["plan_id"]:
                continue
            if not record.get("plan_id") or not record.get("student_key") or not student.get("student_key"):
                unknown = True
                continue
            status = record.get("completion_status", record.get("normalized_status", record.get("status", "unknown")))
            recognition = record.get("recognition_status", "unknown")
            if status == "passed" and recognition in {"recognized", "not_applicable"}:
                eligible.append(record)
            elif status == "unknown" or recognition not in {"recognized", "not_applicable", "rejected"}:
                unknown = True
        if eligible and not conflicting_ids:
            details.append(_check("prerequisite", "pass", f"{course_id} 有对应方案与学生的已通过证据"))
        elif unknown or student.get("academic_record_completeness") not in {"complete", "complete_within_scope"}:
            details.append(_check("prerequisite", "unknown", f"{course_id} 缺完整、无冲突的已通过/认定证据"))
        else:
            details.append(_check("prerequisite", "fail", f"{course_id} 在声明完整的学业记录范围内尚未通过"))
    return _overall(details), "；".join(item["reason"] for item in details)


def validate_plan(section_ids, snapshot, student, constraints, academic_records=None):
    """Fully validate a draft, keeping failures, unknowns and source versions."""
    academic_records = academic_records or []
    checks, conflicts = [], []
    index, duplicates, malformed = _index(snapshot)
    selected_ids = _unique(section_ids)
    if len(selected_ids) != len(section_ids):
        checks.append(_check("duplicate_selection", "fail", "方案中重复选择同一教学班"))
    if duplicates or malformed:
        checks.append(_check("snapshot_structure", "unknown", "教学班快照含重复标识或不完整记录",
                             duplicates, malformed_records=malformed))
    identity_known = (bool(student.get("plan_id")) and bool(student.get("plan_version"))
                      and student.get("cohort") is not None
                      and student.get("plan_binding_status") in CONFIRMED_BINDINGS)
    checks.append(_check("plan_binding", "pass" if identity_known else "unknown",
                         "使用已确认方案身份" if identity_known else "未确认适用培养方案，不能默认选择方案"))
    if not snapshot.get("snapshot_id") or not snapshot.get("version"):
        checks.append(_check("snapshot_identity", "unknown", "快照标识或版本缺失"))
    term = student.get("target_term")
    if not term or not snapshot.get("term_code"):
        checks.append(_check("snapshot_term", "unknown", "学生目标学期或快照学期未知"))
    elif term != snapshot["term_code"]:
        checks.append(_check("snapshot_term", "fail", "快照学期不属于学生目标学期"))
    else:
        checks.append(_check("snapshot_term", "pass", "快照对应学生明确的目标学期"))
    protected = _unique(constraints.get("locked_section_ids", []) + constraints.get("preloaded_section_ids", []))
    missing_protected = [item for item in protected if item not in selected_ids]
    checks.append(_check("protected_sections", "fail" if missing_protected else "pass",
                         "缺少必须保留的锁定/预置班" if missing_protected else "锁定与预置班全部保留", missing_protected))
    excluded = [item for item in selected_ids if item in constraints.get("excluded_section_ids", [])]
    if excluded:
        checks.append(_check("excluded_sections", "fail", "所选教学班同时被明确排除，不能静默解除锁定", excluded))
    selected = []
    for section_id in selected_ids:
        if section_id not in index:
            checks.append(_check("missing_section", "unknown", "所选班不在已导入快照中", [section_id]))
        else:
            selected.append(index[section_id])
    course_groups = {}
    for section in selected:
        course_groups.setdefault(section["course_id"], []).append(section["section_id"])
    for course_id, ids in course_groups.items():
        if len(ids) != 1:
            checks.append(_check("same_course_multiple_sections", "fail", f"{course_id} 选择了多个同时生效的班；备选志愿应分别比较", ids))
    required_courses = _unique(constraints.get("requested_course_ids", []) + [index[i]["course_id"] for i in protected if i in index])
    for course_id in required_courses:
        if course_id not in course_groups:
            checks.append(_check("required_course", "fail", f"方案缺少请求课程 {course_id}"))
    enrollment_items = [item for item in snapshot.get("wish_items", [])
                        if student.get("student_key") and item.get("student_key") == student.get("student_key")]
    enrolled = confirmed_enrollment(enrollment_items)
    enrolled_ids = set(enrolled["section_ids"])
    fresh_result, fresh_reason = _freshness(snapshot, constraints)
    total_credits, unknown_credits = 0, False
    meeting_items = []
    for section in selected:
        section_id = section["section_id"]
        ids = [section_id]
        section_term = section.get("term_code")
        if not section.get("academic_year") or not snapshot.get("academic_year"):
            checks.append(_check("section_academic_year", "unknown", "教学班或快照的学年未知", ids))
        elif section["academic_year"] != snapshot["academic_year"]:
            checks.append(_check("section_academic_year", "fail", "教学班学年与快照学年不一致", ids))
        if not section_term or not term:
            checks.append(_check("section_term", "unknown", "教学班学期或目标学期未知", ids))
        elif section_term != term:
            checks.append(_check("section_term", "fail", "教学班不属于目标学期", ids))
        else:
            checks.append(_check("section_term", "pass", "教学班属于目标学期", ids))
        plan_ids = section.get("plan_ids")
        if not identity_known or not isinstance(plan_ids, list) or not plan_ids:
            checks.append(_check("section_plan", "unknown", "班级归属或学生适用方案待确认", ids))
        elif plan_ids and student["plan_id"] not in plan_ids:
            checks.append(_check("section_plan", "fail", "教学班不在学生适用方案范围内", ids))
        else:
            checks.append(_check("section_plan", "pass", "班级方案范围精确匹配", ids))
        eligibility = section.get("eligibility")
        if not isinstance(eligibility, dict) or not identity_known:
            checks.append(_check("eligibility", "unknown", "选课资格限制或适用身份未知", ids))
        else:
            eligibility_checks = []
            for key, student_key in (("cohorts", "cohort"), ("plan_ids", "plan_id")):
                allowed = eligibility.get(key)
                if not isinstance(allowed, list) or student.get(student_key) is None:
                    eligibility_checks.append(_check("eligibility", "unknown", f"资格字段 {key} 未核实"))
                elif allowed and student[student_key] not in allowed:
                    eligibility_checks.append(_check("eligibility", "fail", f"不满足资格字段 {key}"))
            checks.append(_check("eligibility", _overall(eligibility_checks),
                                 "；".join(c["reason"] for c in eligibility_checks) or "显式资格范围匹配", ids))
        prereq_result, prereq_reason = _prerequisites(section, student, academic_records)
        checks.append(_check("prerequisites", prereq_result, prereq_reason, ids))
        seats = section.get("available_seats")
        if section_id in enrolled_ids:
            checks.append(_check("availability", "pass", "有正式课表证据，保留已有成员；满额不推翻已确认成员", ids))
        elif type(seats) not in {int, float} or seats < 0:
            checks.append(_check("availability", "unknown", "余量未知，不按0处理", ids))
        elif fresh_result != "pass":
            checks.append(_check("availability", "unknown", fresh_reason, ids, observed_available_seats=seats))
        elif seats == 0:
            checks.append(_check("availability", "fail", "当前快照余量为0，没有已有正式成员证据", ids))
        else:
            checks.append(_check("availability", "pass", fresh_reason, ids, observed_available_seats=seats))
        credits = section.get("credits")
        if type(credits) not in {int, float} or credits < 0:
            unknown_credits = True
            checks.append(_check("planned_credits", "unknown", "教学班学分未知", ids))
        else:
            total_credits += credits
        meetings = section.get("meetings")
        if not isinstance(meetings, list) or not meetings:
            checks.append(_check("schedule", "unknown", "缺少具体上课时段，建议学期不能替代时段", ids))
        else:
            for position, meeting in enumerate(meetings):
                state = _meeting_state(meeting)
                checks.append(_check("schedule", "pass" if state == "known" else "unknown",
                                     "具体时段、实际周集合及校历已声明" if state == "known" else "上课时段或校历解释待确认", ids,
                                     meeting_id=meeting.get("meeting_id", f"{section_id}#{position}") if isinstance(meeting, dict) else None))
                meeting_items.append((section_id, meeting, section_term))
    fixed = constraints.get("fixed_meetings", [])
    for position, meeting in enumerate(fixed):
        state = _meeting_state(meeting)
        checks.append(_check("fixed_schedule", "pass" if state == "known" else "unknown",
                             "固定安排已保留" if state == "known" else "固定安排缺少时段或校历解释", [], fixed_index=position))
        meeting_items.append((f"fixed:{position}", meeting, meeting.get("term_code", term) if isinstance(meeting, dict) else term))
    for left, right in combinations(meeting_items, 2):
        if left[0] == right[0]:
            continue
        result, reason, overlap = _overlap(left[1], right[1], left[2], right[2])
        check = _check("time_conflict", result, reason, [left[0], right[0]], overlap=overlap)
        checks.append(check)
        if result != "pass":
            conflicts.append(check)
    friday = constraints.get("friday_free", "off")
    friday_count = 0
    if friday not in {"off", "soft", "hard"}:
        checks.append(_check("preference", "unknown", "周五偏好强度无法识别"))
    for owner, meeting, meeting_term in meeting_items:
        if isinstance(meeting, dict) and meeting.get("day_of_week") == 5 and meeting_term == term:
            friday_count += 1
            if friday == "hard":
                checks.append(_check("friday_free", "fail", "与已确认的周五必须空闲条件冲突", [owner]))
    for field, relation, label in (("min_credits", "min", "最低"), ("max_credits", "max", "最高")):
        if constraints.get(field) is not None:
            threshold = constraints[field]
            if type(threshold) not in {int, float} or unknown_credits:
                checks.append(_check("credit_range", "unknown", f"{label}学分条件或方案学分未知"))
            else:
                satisfied = total_credits >= threshold if relation == "min" else total_credits <= threshold
                checks.append(_check("credit_range", "pass" if satisfied else "fail", f"规划学分 {total_credits}，个人{label}条件 {threshold}"))
    result = _overall(checks)
    for check in checks:
        check["source_refs"] = _unique([
            ref for section_id in check["section_ids"] if section_id in index
            for ref in index[section_id].get("source_refs", [])
        ])
    suggestions = []
    for check in checks:
        if check["result"] == "unknown":
            action = "补充或刷新相关证据，然后重新校验"
        elif check["result"] == "fail":
            action = "审查冲突班与个人条件；如需修改锁定/预置/固定安排，请显式确认解锁"
        else:
            continue
        suggestion = {"code": check["code"], "section_ids": check["section_ids"],
                      "reason": check["reason"], "action": action}
        if suggestion not in suggestions:
            suggestions.append(suggestion)
    return {"result": result, "status": "validated_draft" if result == "pass" else "needs_information" if result == "unknown" else "invalid_draft",
            "section_ids": selected_ids, "checks": checks, "conflicts": conflicts,
            "suggestions": suggestions, "total_credits": None if unknown_credits else total_credits,
            "planned_credits": None if unknown_credits else total_credits,
            "earned_credits": None,
            "preference_scores": {"friday_meeting_count": friday_count,
                                  "friday_penalty": friday_count if friday == "soft" else 0},
            "provenance": _provenance(snapshot, student, constraints, academic_records)}


def _scope_complete(snapshot, requested):
    scope = snapshot.get("filter_scope")
    return (snapshot.get("completeness") == "complete_within_scope"
            and isinstance(scope, dict) and isinstance(scope.get("course_ids"), list)
            and set(requested) <= set(scope["course_ids"]))


def generate_plans(snapshot, student, constraints, academic_records=None,
                   max_nodes=10000, limit=3):
    """Backtrack over finite options and fully revalidate every leaf.

    Rankings cover only this snapshot and declared search scope. A node cap
    reports search_limit even if some validated drafts have been found.
    """
    if type(max_nodes) is not int or max_nodes < 1 or type(limit) is not int or limit < 1:
        raise ValueError("max_nodes 与 limit 必须为正整数")
    academic_records = academic_records or []
    index, duplicates, malformed = _index(snapshot)
    protected = _unique(constraints.get("locked_section_ids", []) + constraints.get("preloaded_section_ids", []))
    requested = _unique(constraints.get("requested_course_ids", []) + [index[s]["course_id"] for s in protected if s in index])
    options, diagnostics = {}, []
    protected_by_course = {}
    for section_id in protected:
        if section_id in index:
            protected_by_course.setdefault(index[section_id]["course_id"], []).append(section_id)
    protected_constraints = {
        **constraints, "requested_course_ids": [index[s]["course_id"] for s in protected if s in index],
    }
    protected_constraints.pop("min_credits", None)
    protected_validation = validate_plan(protected, snapshot, student, protected_constraints, academic_records)
    protected_failures = [c for c in protected_validation["checks"] if c["result"] == "fail"]
    missing_protected = [s for s in protected if s not in index]
    for course_id in requested:
        choices = (protected_by_course.get(course_id) or
                   sorted(s["section_id"] for s in index.values() if s["course_id"] == course_id))
        allowed = []
        for section_id in choices:
            candidate_constraints = {**constraints, "requested_course_ids": [course_id],
                                     "locked_section_ids": [], "preloaded_section_ids": []}
            # An individual course is not expected to meet a whole-term minimum.
            candidate_constraints.pop("min_credits", None)
            candidate = validate_plan([section_id], snapshot, student, candidate_constraints, academic_records)
            failed = [c for c in candidate["checks"] if c["result"] == "fail"]
            if failed:
                diagnostics.extend(failed)
            else:
                allowed.append(section_id)
        options[course_id] = allowed
        if not choices:
            diagnostics.append(_check("missing_course_offerings", "unknown", f"当前导入范围缺少课程 {course_id} 的教学班"))
    if protected_failures:
        diagnostics.extend(protected_failures)
    if missing_protected:
        diagnostics.append(_check("missing_protected_section", "unknown", "必须保留的班尚未导入", missing_protected))
    complete_scope = _scope_complete(snapshot, requested)
    search = {"nodes_visited": 0, "max_nodes": max_nodes, "limit_reached": False,
              "complete_within_scope": complete_scope,
              "candidate_counts": {key: len(value) for key, value in options.items()},
              "requested_course_ids": requested}
    plans, pending = [], []
    # Fixed ordering makes the same snapshot/constraints produce the same drafts.
    ordered = sorted(requested, key=lambda key: (len(options[key]), key))

    def visit(position, chosen):
        if search["nodes_visited"] >= max_nodes:
            search["limit_reached"] = True
            return
        search["nodes_visited"] += 1
        if position == len(ordered):
            validation = validate_plan(chosen, snapshot, student, constraints, academic_records)
            if validation["result"] == "pass":
                plans.append(validation)
            elif validation["result"] == "unknown":
                pending.append(validation)
            else:
                diagnostics.extend(c for c in validation["checks"] if c["result"] == "fail")
            return
        course_id = ordered[position]
        for section_id in options[course_id]:
            # Prune only demonstrated overlaps. Unknown comparisons survive to
            # full validation and remain pending, never validated.
            section = index[section_id]
            known_conflicts = []
            for earlier_id in chosen:
                earlier = index[earlier_id]
                for left in section.get("meetings") or []:
                    for right in earlier.get("meetings") or []:
                        result, reason, overlap = _overlap(left, right, section.get("term_code"), earlier.get("term_code"))
                        if result == "fail":
                            known_conflicts.append(_check("time_conflict", "fail", reason, [earlier_id, section_id], overlap=overlap))
            if known_conflicts:
                diagnostics.extend(known_conflicts)
                continue
            visit(position + 1, chosen + [section_id])
            if search["limit_reached"]:
                return

    if not protected_failures and not missing_protected and all(options.values()):
        visit(0, [])
    plans.sort(key=lambda p: (p["preference_scores"]["friday_penalty"], tuple(sorted(p["section_ids"]))))
    pending.sort(key=lambda p: (p["preference_scores"]["friday_penalty"], tuple(sorted(p["section_ids"]))))
    diagnostics = _unique(diagnostics)
    unknown_inputs = (bool(duplicates or malformed or missing_protected)
                      or not complete_scope or any(c["result"] == "unknown" for c in diagnostics))
    if search["limit_reached"]:
        status, reason = "search_limit", "搜索达到节点上限；已有方案排序尚不保证覆盖完整搜索范围"
    elif plans:
        status, reason = "feasible", "已在当前快照范围找到并完整校验草案；不保证当前占位或官方中选"
    elif pending or unknown_inputs:
        status, reason = "incomplete_data", "当前导入范围或证据不足，没有可全部确认通过的方案"
    else:
        status, reason = "scoped_infeasible", "当前已声明完整的课程范围内，已核实条件不兼容；不代表全校或未来快照全局无解"
    return {"status": status, "reason": reason, "plans": plans[:limit],
            "pending_plans": pending[:limit], "diagnostics": diagnostics,
            "suggestions": [{"code": item["code"], "section_ids": item["section_ids"],
                             "reason": item["reason"],
                             "action": "补齐或刷新数据并重试" if item["result"] == "unknown" else "审查对应班与条件；锁定项保留，需显式确认解锁或修改要求"}
                            for item in diagnostics],
            "search": {**search, "validated_plan_count": len(plans),
                       "pending_plan_count": len(pending)},
            "constraints": deepcopy(constraints),
            "provenance": _provenance(snapshot, student, constraints, academic_records)}
