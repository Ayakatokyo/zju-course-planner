"""Deterministic, conservative academic accounting for the local MVP.

No expression from a document is executed. Only the explicitly synthetic demo
rule schema is supported; official candidates always remain unknown. Numeric
results are confirmed amounts within the supplied scope, never graduation
certification. The function does not mutate any input.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json


KNOWN_BINDINGS = {"student_confirmed", "official_record_verified"}
COMPLETE = "complete_within_scope"
LEDGERS = {"base", "second", "third", "fourth", "recognition"}
METRIC_FIELDS = {
    "credits": "earned_credits",
    "hours": "recognized_hours",
    "points": "classroom_points",
}
UNKNOWN_VALUES = {None, "", "unknown", "pending", "unconfirmed"}


def _number(value):
    """Reject bool, negative, missing and nonfinite values without inferring 0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _json_number(value):
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _known(value):
    return value is not None and not (isinstance(value, str) and value in UNKNOWN_VALUES)


def _first(mapping, *names):
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _refs(value):
    refs = value.get("source_refs", value.get("sources", []))
    return list(refs) if isinstance(refs, list) else []


def _unique_refs(refs):
    unique, seen = [], set()
    for ref in refs:
        key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def _has_conflict(value, field=None):
    if value.get("source_conflict") is True or value.get("linked_issue_ids"):
        return True
    states = value.get("field_states", {})
    if not isinstance(states, dict):
        return False
    values = [states.get(field)] if field else states.values()
    return any(
        state in {"conflicting", "conflict"}
        if isinstance(state, str) else
        isinstance(state, dict) and state.get("state") in {"conflicting", "conflict"}
        for state in values
    )


def _field_unconfirmed(value, field):
    states = value.get("field_states", {})
    if not isinstance(states, dict) or field not in states:
        return False
    state = states[field]
    if isinstance(state, dict):
        state = state.get("state", state.get("status"))
    return state not in {"known", "confirmed", "recognized", "verified", "student_confirmed", "official_record_verified", "not_applicable"}


def _profile(student):
    fields = {
        "plan_id": student.get("plan_id"),
        "cohort": student.get("cohort"),
        "program": _first(student, "major_or_program", "program"),
        "version": _first(student, "plan_version", "version", "plan_version_label"),
    }
    reasons = []
    if student.get("plan_binding_status") not in KNOWN_BINDINGS and student.get("applicability_confirmed") is not True:
        reasons.append("学生尚未明确确认培养方案适用性")
    for field, value in fields.items():
        if not _known(value):
            reasons.append(f"适用信息缺失：{field}")
    if not _known(student.get("student_key")):
        reasons.append("缺少匿名学生键，无法核对记录归属")
    for aliases in (("major_or_program", "program"), ("plan_version", "version", "plan_version_label")):
        supplied = [student[key] for key in aliases if key in student and _known(student[key])]
        if supplied and any(value != supplied[0] for value in supplied):
            reasons.append(f"适用信息存在不一致：{'/'.join(aliases)}")
    states = student.get("profile_field_states", {})
    if isinstance(states, dict):
        for field in ("plan_id", "cohort", "major_or_program", "program", "plan_version", "version"):
            state = states.get(field)
            if isinstance(state, dict):
                state = state.get("state")
            if state is not None and state not in {"known", "confirmed", "student_confirmed", "official_record_verified"}:
                reasons.append(f"适用信息状态未确认：{field}")
    return fields, reasons


def _course_index(courses, plan_id):
    groups = defaultdict(list)
    for course in courses:
        if not isinstance(course, dict) or not _known(course.get("course_id")):
            continue
        memberships = set(course.get("plan_ids") or [])
        if _known(course.get("plan_id")):
            memberships.add(course["plan_id"])
        for member in course.get("plan_memberships") or []:
            if isinstance(member, dict) and _known(member.get("plan_id")):
                memberships.add(member["plan_id"])
        if plan_id in memberships:
            groups[course["course_id"]].append(course)
    index = {}
    for course_id, definitions in groups.items():
        course = dict(definitions[0])
        credits = {_number(row.get("credits")) for row in definitions}
        course["_definition_conflict"] = len(credits) > 1 or any(_has_conflict(row) for row in definitions)
        course["_source_refs"] = _unique_refs([ref for row in definitions for ref in _refs(row)])
        index[course_id] = course
    return index


def _fingerprint(record):
    # Source metadata can differ between repeated snapshots of the same fact.
    provenance = {"record_id", "source_refs", "sources", "snapshot_id", "version", "imported_at", "data_kind"}
    return json.dumps({key: val for key, val in record.items() if key not in provenance}, sort_keys=True, ensure_ascii=False)


def _deduplicate(records):
    """Group both record IDs and attempt IDs; preserve conflicting duplicates."""
    parent = list(range(len(records)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    seen = {}
    for index, record in enumerate(records):
        keys = []
        if _known(record.get("record_id")):
            keys.append(("record", record.get("student_key"), record["record_id"]))
        if _known(record.get("attempt_id")):
            keys.append(("attempt", record.get("student_key"), record.get("plan_id"), record.get("course_id"), record["attempt_id"]))
        for key in keys:
            if key in seen:
                parent[find(index)] = find(seen[key])
            else:
                seen[key] = index
    groups = defaultdict(list)
    for index, record in enumerate(records):
        groups[find(index)].append(record)
    unique, duplicate_count = [], 0
    for group in groups.values():
        duplicate_count += len(group) - 1
        row = dict(group[0])
        row["_record_ids"] = sorted({str(item.get("record_id", "missing")) for item in group})
        row["_duplicate_conflict"] = len({_fingerprint(item) for item in group}) > 1
        if row["_duplicate_conflict"]:
            # Different identifiers or allocations in one duplicate group cannot
            # be safely filtered out using the arbitrarily retained first row.
            for field in ("course_id", "ledger", "plan_id", "student_key"):
                if len({item.get(field) for item in group}) > 1:
                    row[field] = None
            if len({json.dumps(_first(item, "tags", "recognition_tags"), sort_keys=True) for item in group}) > 1:
                row["tags"] = None
        row["_source_refs"] = _unique_refs([ref for item in group for ref in _refs(item)])
        unique.append(row)
    return unique, duplicate_count


def _tags(record, course):
    raw = _first(record, "tags", "recognition_tags")
    # Explicit synthetic annotation may be used by demo fixtures, never guessed
    # from real course names, requirement paths or nearby words in documents.
    if raw is None and course and (course.get("data_kind") == "synthetic" or course.get("demo_annotation_kind") == "synthetic"):
        raw = course.get("tags")
    if raw is None:
        return None, set()
    if not isinstance(raw, list):
        return None, set()
    known, pending = set(), set()
    for item in raw:
        if isinstance(item, str):
            known.add(item)
        elif isinstance(item, dict):
            tag = _first(item, "tag", "tag_id", "name")
            if not _known(tag):
                continue
            if item.get("status", item.get("recognition_status")) in {"recognized", "confirmed", "known"}:
                known.add(tag)
            else:
                pending.add(tag)
    return known, pending


def _normalize(record, student, index):
    course_id = record.get("course_id")
    course = index.get(course_id)
    state = _first(record, "completion_status", "normalized_status", "status")
    kind = record.get("record_kind", "course" if course_id else None)
    recognition = record.get("recognition_status")
    issues = []
    excluded = False
    if _known(record.get("plan_id")) and record["plan_id"] != student.get("plan_id"):
        excluded = True
        issues.append("不同培养方案记录已隔离，不自动互抵")
    elif not _known(record.get("plan_id")):
        issues.append("学业记录的培养方案归属未知")
    if _known(record.get("student_key")) and record["student_key"] != student.get("student_key"):
        excluded = True
        issues.append("记录属于其他学生，未计入")
    elif not _known(record.get("student_key")):
        issues.append("记录的学生归属未知")
    if not _known(record.get("record_id")):
        issues.append("记录缺少 record_id")
    if not _known(record.get("snapshot_id")):
        issues.append("记录缺少学业快照版本，无法追溯")
    for field in ("plan_id", "student_key", "ledger", "completion_status", "recognition_status"):
        if _field_unconfirmed(record, field):
            issues.append(f"记录字段尚未确认：{field}")
    if record.get("_duplicate_conflict"):
        issues.append("同一记录或修读尝试存在相互冲突的重复数据")
    if _has_conflict(record):
        issues.append("记录存在来源差异或冲突，尚待核对")
    if kind not in {"course", "transfer_credit", "activity", "recognition"}:
        issues.append("record_kind 未知或不支持")
    if course_id and course is None and not excluded:
        issues.append("课程号未在适用方案的课程定义中精确匹配")
    if kind == "course" and not course_id:
        issues.append("课程记录缺少精确 course_id")
    if course and course.get("_definition_conflict"):
        issues.append("课程定义来源或学分存在冲突")
    ledger = record.get("ledger")
    if ledger not in LEDGERS:
        issues.append("学分/认定账本归属未知")
    if state in {"failed", "not_taken"} or recognition == "rejected":
        status = "excluded"
    elif state == "in_progress":
        status = "in_progress"
    elif state == "passed" and recognition in {"not_applicable", "recognized"}:
        if kind in {"recognition", "activity", "transfer_credit"} and recognition != "recognized":
            issues.append("非普通课程的正式认定未确认")
        status = "earned"
    else:
        status = "unknown"
        issues.append("通过或正式认定状态尚未确认")
    if excluded:
        status = "excluded"
    tags, pending_tags = _tags(record, course)
    metrics = {metric: _number(record.get(field)) for metric, field in METRIC_FIELDS.items()}
    for metric, field in METRIC_FIELDS.items():
        if _has_conflict(record, field) or _field_unconfirmed(record, field):
            metrics[metric] = None
    metrics["course_count"] = Decimal(1) if kind == "course" and course_id else Decimal(0)
    planned = _number(record.get("planned_credits"))
    if planned is None and course:
        planned = None if _field_unconfirmed(course, "credits") else _number(course.get("credits"))
    if _field_unconfirmed(record, "planned_credits"):
        planned = None
    return {
        "course_id": course_id, "record_kind": kind, "record_ids": record["_record_ids"],
        "status": status, "issues": issues, "ledger": ledger, "metrics": metrics,
        "tags": tags, "pending_tags": pending_tags, "planned_credits": planned,
        "source_refs": _unique_refs(record["_source_refs"] + (course.get("_source_refs", []) if course else [])),
    }


def _collapse_courses(entries):
    """A repeat pass of one course never grants a second copy of its credits."""
    groups, result = defaultdict(list), []
    for entry in entries:
        if entry["course_id"] and entry["record_kind"] == "course" and entry["status"] != "excluded":
            groups[entry["course_id"]].append(entry)
        else:
            result.append(entry)
    for group in groups.values():
        earned = [row for row in group if row["status"] == "earned"]
        bad = [row for row in group if row["issues"] or row["status"] == "unknown"]
        if earned:
            row = dict(earned[0])
            if bad:
                row["issues"] = list(dict.fromkeys(reason for item in bad for reason in item["issues"]))
                if not row["issues"]:
                    row["issues"] = ["同课存在未确认的重复认定"]
            if len({item["ledger"] for item in earned}) > 1:
                row["issues"] = row["issues"] + ["同课不同获分记录的账本归属不一致"]
            row["metrics"] = dict(row["metrics"])
            for metric in METRIC_FIELDS:
                values = {item["metrics"][metric] for item in earned}
                if len(values) > 1:
                    row["metrics"][metric] = None
            row["tags"] = set().union(*(item["tags"] or set() for item in earned))
            if any(item["tags"] is None for item in earned):
                row["tags"] = None
            row["pending_tags"] = set().union(*(item["pending_tags"] for item in earned))
        else:
            row = dict(group[0])
            if bad:
                row["status"] = "unknown"
                row["issues"] = list(dict.fromkeys(reason for item in bad for reason in item["issues"]))
            # Repeated registrations do not multiply planned course credits.
            if len({item["planned_credits"] for item in group}) > 1:
                row["planned_credits"] = None
        row["record_ids"] = sorted({rid for item in group for rid in item["record_ids"]})
        row["source_refs"] = _unique_refs([ref for item in group for ref in item["source_refs"]])
        result.append(row)
    return result


def _match(entry, rule):
    """False=no match, True=confirmed selection, None=selection unresolved."""
    pool = rule.get("course_pool")
    uncertain = False
    if pool is not None:
        if not isinstance(pool, list):
            return None
        if entry["course_id"] is None:
            if entry["record_kind"] in {"activity", "recognition"}:
                return False
            uncertain = True
        elif entry["course_id"] not in pool:
            return False
    ledger = rule.get("ledger")
    if ledger is None and rule.get("metric") == "credits" and entry["ledger"] == "recognition":
        # A generic total is base + second/third/fourth classrooms. Recognition
        # conditions must opt into their own ledger, not add a second credit copy.
        return False
    if ledger is not None:
        if entry["ledger"] not in LEDGERS:
            uncertain = True
        elif entry["ledger"] != ledger:
            return False
    tag = rule.get("tag")
    if tag is not None:
        if tag in entry["pending_tags"] or entry["tags"] is None:
            uncertain = True
        elif tag not in entry["tags"]:
            return False
    return None if uncertain else True


def _rule_blockers(rule, student, fields, profile_reasons):
    reasons = list(profile_reasons)
    if rule.get("data_kind") != "synthetic" or rule.get("status") != "demo_active":
        reasons.append("该条是未启用的来源候选或不受支持的规则；禁止自动执行")
    if rule.get("enforce_automatically") is False or rule.get("human_review_required") is True or "candidate_expression" in rule:
        reasons.append("规则要求人工审核或禁止自动执行")
    if not isinstance(rule.get("version"), str) or not rule["version"].startswith("DEMO-"):
        reasons.append("缺少独立的 DEMO 规则版本")
    if rule.get("plan_id") != fields["plan_id"]:
        reasons.append("规则属于其他方案，不自动跨方案抵扣")
    scope = rule.get("scope")
    if not isinstance(scope, dict):
        reasons.append("规则适用范围未明确")
    else:
        for key in ("cohort", "program", "version"):
            if not _known(scope.get(key)) or scope[key] != fields[key]:
                reasons.append(f"规则适用范围不一致或未知：{key}")
    if _has_conflict(rule):
        reasons.append("规则依赖来源问题或冲突，尚待核对")
    if rule.get("metric") not in {*METRIC_FIELDS, "course_count"}:
        reasons.append("不支持或未知的计算单位")
    if rule.get("ledger") is not None and rule["ledger"] not in LEDGERS:
        reasons.append("规则账本归属不支持")
    for field in rule.get("requires_fields") or []:
        if not _known(student.get(field)):
            reasons.append(f"规则依赖学生信息：{field}")
    return list(dict.fromkeys(reasons))


def _threshold(rule, student):
    conditional = rule.get("conditional_threshold")
    if conditional is not None:
        if not isinstance(conditional, dict) or not isinstance(conditional.get("values"), dict):
            return None, "条件阈值格式未确认"
        field = conditional.get("field")
        value = student.get(field) if isinstance(field, str) else None
        if not _known(value) or value not in conditional["values"]:
            return None, f"条件阈值依赖尚未确认的学生信息：{field}"
        threshold = _number(conditional["values"][value])
    else:
        threshold = _number(rule.get("threshold"))
    return threshold, None if threshold is not None else "阈值缺失或不是非负有限数值"


def audit_progress(student, records, rules, courses, record_completeness=COMPLETE):
    """Return JSON-ready progress with pass/fail/unknown and source evidence.

    A pass/fail is only a comparison against a synthetic rule in the supplied
    complete record scope. It is never official graduation or course eligibility
    certification. Missing numeric values do not inherit catalog course credits.
    """
    fields, profile_reasons = _profile(student)
    index = _course_index(courses, fields["plan_id"])
    unique, duplicate_count = _deduplicate(records)
    entries = _collapse_courses([_normalize(row, student, index) for row in unique])
    ledger_totals = {key: Decimal(0) for key in LEDGERS}
    hours, points, planned = Decimal(0), Decimal(0), Decimal(0)
    unknown_ids, unknown_reasons, planned_ids, planned_courses = set(), [], set(), set()
    excluded_ids = set()
    for entry in entries:
        if entry["status"] == "excluded":
            excluded_ids.update(entry["record_ids"])
            continue
        reasons = list(entry["issues"]) + profile_reasons
        if entry["status"] == "earned" and not reasons:
            credits = entry["metrics"]["credits"]
            if credits is None and entry["ledger"] != "recognition":
                reasons.append("实际已获学分缺失或冲突，不能由课程目录推断")
            elif credits is not None:
                ledger_totals[entry["ledger"]] += credits
            if entry["metrics"]["hours"] is not None:
                hours += entry["metrics"]["hours"]
            if entry["metrics"]["points"] is not None:
                points += entry["metrics"]["points"]
        elif entry["status"] == "in_progress" and not reasons:
            planned_ids.update(entry["record_ids"])
            if entry["course_id"]:
                planned_courses.add(entry["course_id"])
            if entry["planned_credits"] is not None:
                planned += entry["planned_credits"]
            else:
                reasons.append("在修课程学分未知")
        if reasons or entry["status"] == "unknown":
            unknown_ids.update(entry["record_ids"])
            unknown_reasons.extend(reasons)
    extra_total = sum((ledger_totals[key] for key in ("second", "third", "fourth")), Decimal(0))
    ledger = {
        "earned_credits": _json_number(ledger_totals["base"] + extra_total),
        "base": _json_number(ledger_totals["base"]),
        "extra_classrooms": {key: _json_number(ledger_totals[key]) for key in ("second", "third", "fourth")},
        "in_progress": {"credits": _json_number(planned), "course_ids": sorted(planned_courses), "record_ids": sorted(planned_ids)},
        "unknown": {"record_count": len(unknown_ids), "record_ids": sorted(unknown_ids), "reasons": list(dict.fromkeys(unknown_reasons))},
        "recognized_hours": _json_number(hours), "classroom_points": _json_number(points),
        "recognition_credits": _json_number(ledger_totals["recognition"]),
        "is_complete": not profile_reasons and not unknown_ids and record_completeness == COMPLETE,
        "known_amount_semantics": "confirmed_lower_bound_within_supplied_scope",
        "accounting_note": "基础与二三四课堂分账；认定学分不额外叠加，学时和记点独立；未知项不作已获学分。",
    }
    ledger["extra_classrooms"]["total"] = _json_number(extra_total)
    results = []
    for rule in rules:
        reasons = _rule_blockers(rule, student, fields, profile_reasons)
        threshold, threshold_reason = _threshold(rule, student)
        if threshold_reason:
            reasons.append(threshold_reason)
        result = {
            "rule_id": rule.get("rule_id"), "title": rule.get("title"), "plan_id": rule.get("plan_id"),
            "rule_version": rule.get("version"), "data_kind": rule.get("data_kind"),
            "metric": rule.get("metric"), "threshold": _json_number(threshold), "result": "unknown",
            "actual": None, "remaining": None, "reason": None,
            "source_refs": _refs(rule), "matched_course_ids": [], "matched_record_ids": [],
            "unknown_record_ids": [],
            "linked_issue_ids": list(rule.get("linked_issue_ids") or []),
        }
        # Candidate expressions and inactive rules never enter the calculator.
        if reasons:
            result["reason"] = "；".join(dict.fromkeys(reasons))
            results.append(result)
            continue
        actual = Decimal(0)
        for entry in entries:
            if entry["status"] in {"excluded", "in_progress"}:
                continue
            matched = _match(entry, rule)
            if matched is False:
                continue
            result["source_refs"].extend(entry["source_refs"])
            if matched is None:
                reasons.append("可能相关记录的标签或账本归属未知")
            if entry["issues"] or entry["status"] == "unknown":
                reasons.extend(entry["issues"] or ["相关记录状态未知"])
                result["unknown_record_ids"].extend(entry["record_ids"])
                continue
            if matched is None:
                result["unknown_record_ids"].extend(entry["record_ids"])
                continue
            value = entry["metrics"].get(rule["metric"])
            if value is None:
                reasons.append(f"相关记录的 {rule['metric']} 数值缺失或冲突")
                result["unknown_record_ids"].extend(entry["record_ids"])
                continue
            actual += value
            if entry["course_id"]:
                result["matched_course_ids"].append(entry["course_id"])
            result["matched_record_ids"].extend(entry["record_ids"])
            result["source_refs"].extend(entry["source_refs"])
        if record_completeness != COMPLETE:
            reasons.append("个人学业记录范围不完整，不能据此确认满足或缺口")
        result["actual"] = _json_number(actual)
        result["actual_semantics"] = "confirmed_lower_bound_within_supplied_scope"
        result["source_refs"] = _unique_refs(result["source_refs"])
        result["matched_course_ids"] = sorted(set(result["matched_course_ids"]))
        result["matched_record_ids"] = sorted(set(result["matched_record_ids"]))
        result["unknown_record_ids"] = sorted(set(result["unknown_record_ids"]))
        if reasons:
            result["reason"] = "；".join(dict.fromkeys(reasons))
        else:
            result["remaining"] = _json_number(max(Decimal(0), threshold - actual))
            result["result"] = "pass" if actual >= threshold else "fail"
            result["reason"] = "在所提供记录范围内满足演示阈值" if result["result"] == "pass" else "在所提供记录范围内尚未达到演示阈值；在修不计为已完成"
        results.append(result)
    return {
        "profile_status": "confirmed" if not profile_reasons else "unknown",
        "profile_reasons": profile_reasons, "plan_id": fields["plan_id"],
        "ledger": ledger, "rules": results,
        "versions": {
            "plan_version": fields["version"], "profile_version": student.get("profile_version"),
            "rule_versions": sorted({str(rule.get("version")) for rule in rules if rule.get("version") is not None}),
            "academic_snapshot_ids": sorted({str(row.get("snapshot_id")) for row in records if row.get("snapshot_id") is not None}),
            "record_completeness": record_completeness,
        },
        "record_summary": {"input_count": len(records), "deduplicated_count": len(unique), "duplicate_count": duplicate_count, "excluded_record_ids": sorted(excluded_ids)},
        "warnings": ["仅为所提供范围内的学业规划核对，不代表校方毕业审核或正式资格认定"],
    }
