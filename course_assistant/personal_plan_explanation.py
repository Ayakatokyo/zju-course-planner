"""Explain an owned, recorded computation without reading or recomputing facts."""
from __future__ import annotations

from itertools import combinations
from .planner import _add, _number


def plan_reasoning_facts(plan, searches=()):
    """Join already projected facts for explanation, without ranking relevance.

    Inspect uses only the owned saved calculation. Search exclusions are joined
    only from this turn's projected results with the same data version/term.
    Periods remain observations: neither meeting counts nor period numbers are
    elapsed hours, half days, current availability or personal free time.
    """
    saved = plan.get("action") == "inspect"
    options = ((plan.get("computation_summary") or {}).get("options", []) if saved
               else plan.get("options", []))
    if not options or plan.get("reason"):
        return None
    constraints = plan.get("constraints") or {}
    credit_target = ((plan.get("computation_summary") or {}).get("credit_target", {}) if saved else
                     {"minimum": constraints.get("target_credits"), "maximum": constraints.get("max_credits")})
    minimum = _number(credit_target.get("minimum"))
    courses, rows, classes_by_option = {}, [], {}
    for option in options:
        classes = option.get("classes")
        by_code = {row["course_code"]: row for row in classes} if classes is not None else None
        classes_by_option[option["label"]] = by_code
        for row in classes or []:
            courses[row["course_code"]] = row["course_name"]
        total = _number(option["total_credits"])
        rows.append({"label": option["label"], "total_credits": option["total_credits"],
            "excess_above_minimum": format(_add(total, minimum.copy_negate()), "f")
                if total is not None and minimum is not None else None,
            "friday_meeting_count": option["friday_meeting_count"],
            "course_codes": list(by_code) if by_code is not None else None,
            "friday_observed_slots": [{"course_code": row["course_code"], "slot": slot}
                for row in classes for slot in row["weekday_period_text"] if slot.startswith("周五")]
                if classes is not None else None})
    differences = []
    for left, right in combinations(options, 2):
        a, b = classes_by_option[left["label"]], classes_by_option[right["label"]]
        if a is None or b is None:
            continue
        differences.append({"left": left["label"], "right": right["label"],
            "shared_course_codes": sorted(a.keys() & b.keys()),
            "left_only_course_codes": sorted(a.keys() - b.keys()),
            "right_only_course_codes": sorted(b.keys() - a.keys()),
            "left_only_course_names": [a[code]["course_name"] for code in sorted(a.keys() - b.keys())],
            "right_only_course_names": [b[code]["course_name"] for code in sorted(b.keys() - a.keys())],
            "changed_class_course_codes": sorted(code for code in a.keys() & b.keys()
                if a[code]["class_ref"] != b[code]["class_ref"])})
    totals = [_number(option['total_credits']) for option in options]
    minimum_returned = min(totals) if all(value is not None for value in totals) else None
    shared = (sorted(set.intersection(*(set(value) for value in classes_by_option.values())))
              if all(value is not None for value in classes_by_option.values()) else None)
    blocked = {}
    reason_labels = {
        "excluded_or_unknown_weekday_period": "星期或节次资料不完整",
        "historical_nonpositive_capacity": "历史余量非正",
        "observation_order_unknown_requires_explicit_lock": "观察先后顺序未知",
        "simultaneous_observations_require_explicit_lock": "同时存在多份观察，尚未指定采用哪份",
    }
    if not saved:
        for reference, search in searches:
            if (search.get("action") != "search" or search.get("data_version") != plan.get("data_version")
                    or search.get("target_term") != plan.get("target_term")):
                continue
            for row in search.get("courses", []):
                summary = row.get("observation_summary")
                if summary and summary["usable_section_count"] == 0:
                    blocked[row["course_code"]] = {"course_code": row["course_code"],
                        "evidence_id": reference, "reasons": [reason_labels[reason] for reason in summary["blocking_reasons"]]}
    return {"origin": "owned_last_computation" if saved else "current_tool_result",
        "scope": "historical_course_observations_only", "course_names": courses, "options": rows,
        "credit_target": credit_target,
        "returned_option_summary": {
            "option_count": len(options), "shared_course_codes": shared,
            "friday_free_labels": [row['label'] for row in options if row['friday_meeting_count'] == 0],
            "friday_occupied_labels": [row['label'] for row in options if row['friday_meeting_count'] > 0],
            "lowest_credit_labels": [row['label'] for row in options
                if minimum_returned is not None and _number(row['total_credits']) == minimum_returned],
            "scope": "returned_options_only"},
        "pairwise_changes": differences, "candidate_screening": list(blocked.values())[:10],
        "candidate_screening_omitted": max(0, len(blocked) - 10),
        "duration_mapping_verified": False, "domain_relevance_ranked": False,
        "interpretation": {
            "credits": "minimum是最低要求，excess_above_minimum是方案超过最低要求的学分；不能据返回方案断言恰好达到最低值的组合不存在。",
            "comparison": "pairwise_changes只比较本次返回方案；未返回的组合和候选不是已排除或不可行。",
            "time": "只能描述weekday_period_text中的星期与节次，没有节次到时刻的映射；不称上午、下午、晚段、半天或课时。",
            "relevance": "选择理由限于实际课程名称、已引用培养原文和条件差异；课程内容、难度、已修完成或课程等价未知。"}}
