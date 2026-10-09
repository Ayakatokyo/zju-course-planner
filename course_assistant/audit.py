"""Deterministic, conservative academic accounting for the local MVP.

No expression from a document is executed. Only the explicitly synthetic demo
rule schema is supported; official candidates always remain unknown. Numeric
results are confirmed amounts within the supplied scope, never graduation
certification. The function does not mutate any input.
"""

from collections import defaultdict
from decimal import Decimal, localcontext, MAX_EMAX, MIN_EMIN
import json

from .importer import ImportErrorDetail, number


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
    """Reject malformed, negative or nonfinite values without inferring zero."""
    try:
        return number(value, "计算数值", nullable=True)
    except ImportErrorDetail:
        return None


def _add(left, right):
    # Decimal's default 28-place context can round an otherwise exact import.
    # The importer bounds digits/exponent; size the local context for this sum.
    exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
    precision = max(left.adjusted(), right.adjusted()) - exponent + 3
    with localcontext() as context:
        context.prec = max(28, precision)
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        return left + right


def _json_number(value):
    """Keep ordinary JSON numbers; preserve longer decimals as decimal text."""
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    converted = float(value)
    if Decimal(str(converted)) == value:
        return converted
    return str(value)


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
    provenance = {"record_id", "source_refs", "sources", "snapshot_id", "version", "imported_at", "data_kind",
                  "ledger", "tags", "recognition_tags", "requirement_id", "membership_id", "allocation_id",
                  "contribution_kind", "allocation_status", "policy_rule_version_id", *METRIC_FIELDS.values()}
    fact = {key: val for key, val in record.items() if key not in provenance}
    for key in (*METRIC_FIELDS.values(), "planned_credits", "grade_numeric"):
        if key in fact and _number(fact[key]) is not None:
            fact[key] = _json_number(_number(fact[key]))
    if fact.get("record_kind", "course" if fact.get("course_id") else None) == "course" and fact.get("recognition_status") in {"not_applicable", "recognized"}:
        fact["recognition_status"] = "confirmed_for_course"
    return json.dumps(fact, sort_keys=True, ensure_ascii=False)


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
            keys.append(("attempt", record.get("student_key"),
                         record.get("record_kind", "course" if record.get("course_id") else None), record["attempt_id"]))
        if _known(record.get("recognition_id")):
            keys.append(("recognition", record.get("student_key"), record["recognition_id"]))
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
        # Requirement/recognition coverage is separate from the credit fact.
        actual = [item for item in group if item.get("contribution_kind") not in {"condition_coverage", "report_only", "projection"}
                  and item.get("ledger") != "recognition"]
        row = dict((actual or group)[0])
        actual_ledgers = {item.get("ledger") for item in actual or group}
        if len(actual_ledgers) > 1:
            row["ledger"] = None
        row["_coverage_ledgers"] = {item.get("ledger") for item in group if item.get("ledger") in LEDGERS}
        row["_coverage_metrics"] = {}
        for ledger in row["_coverage_ledgers"]:
            allocated = [item for item in group if item.get("ledger") == ledger]
            row["_coverage_metrics"][ledger] = {
                metric: next(iter(values)) if len(values) == 1 else None
                for metric, field in METRIC_FIELDS.items()
                for values in [{_number(item.get(field)) for item in allocated}]
            }
        tag_sets = [_first(item, "tags", "recognition_tags") for item in group]
        if any(tags is not None and not isinstance(tags, list) for tags in tag_sets):
            row["tags"] = "unresolved_malformed_tags"
        else:
            raw_tags = [tag for tags in tag_sets if tags for tag in tags]
            if raw_tags:
                row["tags"] = raw_tags
        row["_record_ids"] = sorted({str(item.get("record_id", "missing")) for item in group})
        row["_duplicate_conflict"] = (len({_fingerprint(item) for item in group}) > 1 or
                                      any(len({_number(item.get(field)) for item in actual or group}) > 1
                                          for field in METRIC_FIELDS.values()))
        if row["_duplicate_conflict"]:
            # Different identifiers or allocations in one duplicate group cannot
            # be safely filtered out using the arbitrarily retained first row.
            for field in ("course_id", "plan_id", "student_key"):
                if len({item.get(field) for item in group}) > 1:
                    row[field] = None
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
    if kind == "course" and state not in {"failed", "not_taken", "replaced"}:
        if not _known(record.get("attempt_id")):
            issues.append("修读实例身份未知，不能由课程代码或成绩补造")
        if record.get("resolution_status") is not None and record["resolution_status"] != "verified":
            issues.append("修读实例尚未核验或存在冲突")
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
    if state in {"failed", "not_taken", "replaced"} or record.get("row_status") == "replaced" or recognition == "rejected":
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
        "attempt_id": record.get("attempt_id"), "recognition_id": record.get("recognition_id"),
        "fact_id": record.get("academic_fact_ref") or record.get("attempt_id") or record.get("recognition_id") or record.get("record_id"),
        "repeat_credit_policy_rule_id": record.get("repeat_credit_policy_rule_id"),
        "contribution_kind": record.get("contribution_kind", "actual_credit"),
        "coverage_ledgers": record.get("_coverage_ledgers", set()),
        "coverage_metrics": record.get("_coverage_metrics", {}),
        "status": status, "issues": issues, "ledger": ledger, "metrics": metrics,
        "tags": tags, "pending_tags": pending_tags, "planned_credits": planned,
        "source_refs": _unique_refs(record["_source_refs"] + (course.get("_source_refs", []) if course else [])),
    }


def _repeat_policy(group, index, rules, fields):
    course = index.get(group[0]["course_id"], {})
    policies = [course["repeat_credit_policy"]] if isinstance(course.get("repeat_credit_policy"), dict) else []
    refs = {entry["repeat_credit_policy_rule_id"] for entry in group if entry["repeat_credit_policy_rule_id"]}
    policies.extend(rule for rule in rules if rule.get("rule_kind") == "repeat_credit_policy"
                    and (rule.get("rule_id") in refs or group[0]["course_id"] in (rule.get("course_pool") or [])))
    if refs - {policy.get("rule_id") for policy in policies}:
        return None
    modes = set()
    for policy in policies:
        scope = policy.get("scope")
        if (policy.get("data_kind") != "synthetic" or policy.get("status") not in {"demo_active", "confirmed"}
                or not str(policy.get("rule_id", "")).startswith("DEMO-")
                or not str(policy.get("version", "")).startswith("DEMO-")
                or policy.get("plan_id") != fields["plan_id"] or not isinstance(scope, dict)
                or any(not _known(scope.get(key)) or scope[key] != fields[key] or _field_unconfirmed(scope, key)
                       for key in ("cohort", "program", "version"))
                or policy.get("human_review_required") is True or policy.get("enforce_automatically") is False
                or "candidate_expression" in policy or _has_conflict(policy)):
            continue
        if policy.get("mode") in {"per_attempt", "once_per_course"}:
            modes.add(policy["mode"])
    return next(iter(modes)) if len(modes) == 1 else None


def _resolve_attempts(entries, index, rules, student, fields):
    """Preserve attempts; only an explicit applicable policy resolves repeats."""
    groups = defaultdict(list)
    attempts = {entry["attempt_id"] for entry in entries
                if entry["record_kind"] == "course" and entry["attempt_id"]}
    for entry in entries:
        if entry["record_kind"] == "course" and entry["course_id"] and entry["status"] == "earned":
            groups[entry["course_id"]].append(entry)
        if entry["record_kind"] in {"recognition", "activity", "transfer_credit"} and entry["fact_id"] in attempts:
            entry["contribution_kind"] = "condition_coverage"
        if entry["contribution_kind"] in {"report_only", "projection"} and entry["status"] != "excluded":
            entry["status"] = "unknown"
            entry["issues"].append("来源报告或预计贡献不是独立已获分事实")
    for group in groups.values():
        if len(group) <= 1:
            continue
        mode = _repeat_policy(group, index, rules, fields)
        if mode == "per_attempt":
            continue
        if mode == "once_per_course" and all(not entry["issues"] for entry in group):
            if len({(entry["ledger"], tuple(entry["metrics"].items())) for entry in group}) == 1:
                for entry in group[1:]:
                    entry["credit_suppressed"] = True
                continue
        for entry in group:
            entry["issues"].append("同课程存在多次通过修读；可重复获分政策缺失、冲突或适用范围未确认")
    return entries


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
    if ledger is None and rule.get("metric") == "credits" and (entry["ledger"] == "recognition" or entry["contribution_kind"] == "condition_coverage"):
        # A generic total is base + second/third/fourth classrooms. Recognition
        # conditions must opt into their own ledger, not add a second credit copy.
        return False
    if ledger is not None:
        if entry["ledger"] not in LEDGERS:
            uncertain = True
        elif entry["ledger"] != ledger and ledger not in entry["coverage_ledgers"]:
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
    if rule.get("metric") == "course_count" and threshold is not None and threshold != threshold.to_integral_value():
        return None, "门数阈值不是整数"
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
    entries = _resolve_attempts([_normalize(row, student, index) for row in unique], index, rules, student, fields)
    ledger_totals = {key: Decimal(0) for key in LEDGERS}
    hours, points, planned = Decimal(0), Decimal(0), Decimal(0)
    measured_facts = {"hours": {}, "points": {}}
    measure_values = {"hours": defaultdict(set), "points": defaultdict(set)}
    for entry in entries:
        if entry["status"] == "earned" and not entry["issues"] and not entry.get("credit_suppressed"):
            for metric in measure_values:
                if entry["metrics"][metric] is not None:
                    measure_values[metric][entry["fact_id"]].add(entry["metrics"][metric])
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
            elif credits is not None and not entry.get("credit_suppressed") and entry["contribution_kind"] != "condition_coverage":
                ledger_totals[entry["ledger"]] = _add(ledger_totals[entry["ledger"]], credits)
            for metric in ("hours", "points"):
                value = entry["metrics"][metric]
                if value is None or entry.get("credit_suppressed"):
                    continue
                if len(measure_values[metric][entry["fact_id"]]) > 1:
                    reasons.append(f"同一事实的 {metric} 来源数值不同，待确认")
                    continue
                previous = measured_facts[metric].get(entry["fact_id"])
                if previous is not None:
                    continue
                measured_facts[metric][entry["fact_id"]] = value
                if metric == "hours":
                    hours = _add(hours, value)
                else:
                    points = _add(points, value)
        elif entry["status"] == "in_progress" and not reasons:
            planned_ids.update(entry["record_ids"])
            if entry["course_id"]:
                planned_courses.add(entry["course_id"])
            if entry["planned_credits"] is not None:
                planned = _add(planned, entry["planned_credits"])
            else:
                reasons.append("在修课程学分未知")
        if reasons or entry["status"] == "unknown":
            unknown_ids.update(entry["record_ids"])
            unknown_reasons.extend(reasons)
    extra_total = _add(_add(ledger_totals["second"], ledger_totals["third"]), ledger_totals["fourth"])
    ledger = {
        "earned_credits": _json_number(_add(ledger_totals["base"], extra_total)),
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
        if rule.get("rule_kind") == "repeat_credit_policy":
            continue
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
        counted_facts, counted_courses = {}, set()
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
            rule_ledger = rule.get("ledger")
            if rule_ledger is not None and rule_ledger != entry["ledger"]:
                value = entry["coverage_metrics"].get(rule_ledger, {}).get(rule["metric"], value)
            if value is None:
                reasons.append(f"相关记录的 {rule['metric']} 数值缺失或冲突")
                result["unknown_record_ids"].extend(entry["record_ids"])
                continue
            fact_key = entry["fact_id"]
            if rule["metric"] == "course_count":
                if entry["course_id"] in counted_courses:
                    value = Decimal(0)
                counted_courses.add(entry["course_id"])
            elif entry.get("credit_suppressed"):
                value = Decimal(0)
            elif fact_key in counted_facts:
                if counted_facts[fact_key] != value:
                    reasons.append("同一获分事实的条件贡献数值不一致，不能重复或任选累计")
                    result["unknown_record_ids"].extend(entry["record_ids"])
                    if counted_facts[fact_key] is not None:
                        actual = _add(actual, counted_facts[fact_key].copy_negate())
                    counted_facts[fact_key] = None
                value = Decimal(0)
            else:
                counted_facts[fact_key] = value
            actual = _add(actual, value)
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
            result["remaining"] = _json_number(max(Decimal(0), _add(threshold, actual.copy_negate())))
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
        "record_summary": {"input_count": len(records), "deduplicated_count": len(unique), "duplicate_count": duplicate_count, "excluded_record_ids": sorted(excluded_ids),
                           "attempt_ids": sorted({entry["attempt_id"] for entry in entries if entry["attempt_id"]}),
                           "accounted_fact_count": len(entries)},
        "warnings": ["仅为所提供范围内的学业规划核对，不代表校方毕业审核或正式资格认定"],
    }
