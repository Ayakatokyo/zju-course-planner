"""Explain an owned, recorded computation without reading or recomputing facts."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


EXPLANATION_SECTIONS = ("credit_target", "observed_tradeoff", "scope_limits", "unknowns")
RANKING_CRITERIA = ["observed_slot_result", "soft_meeting_count", "known_credits",
                    "excess_above_minimum", "course_count", "stable_observation_order"]


def _decimal(value):
    if not isinstance(value, str) or len(value) > 64 or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0:
        return None
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def _count(value):
    return str(value) if type(value) is int and value >= 0 else "未记录"


def computation_explanation(summary, sections=None):
    """Render only fixed, validated summary fields; never invent a rejected option.

    The caller checks the strict course-plan projection and current proof. This
    renderer has no school client, solver, model, private file or mutable state.
    """
    if not isinstance(summary, dict) or summary.get("origin") != "owned_last_computation":
        raise ValueError("computation_summary_unavailable")
    sections = list(EXPLANATION_SECTIONS) if sections is None else sections
    if (not isinstance(sections, list) or not 1 <= len(sections) <= len(EXPLANATION_SECTIONS)
            or any(type(value) is not str or value not in EXPLANATION_SECTIONS for value in sections)
            or len(set(sections)) != len(sections)):
        raise ValueError("invalid_computation_sections")
    lines = []
    credit = summary.get("credit_target") or {}
    minimum, maximum = _decimal(credit.get("minimum")), _decimal(credit.get("maximum"))
    if "credit_target" in sections:
        if minimum is not None:
            lines.append(f"目标是「不少于{minimum}学分」" +
                         (f"，上限{maximum}学分。" if maximum is not None else "，没有设置上限，不要求恰好达到下限。"))
        else:
            lines.append("原计算未记录学分下限，不能补推目标。")
    if "observed_tradeoff" in sections:
        for option in summary.get("options", []):
            label = option.get("label")
            if label not in {"A", "B", "C"}:
                continue
            total, excess = _decimal(option.get("total_credits")), _decimal(option.get("excess_above_minimum"))
            text = f"方案{label}为{total if total is not None else '未记录'}学分"
            if excess is not None:
                text += f"，比下限多{excess}学分"
            lines.append(text + "。")
        exact = (summary.get("exact_target") or {}).get("status")
        lines.append({
            "seen_feasible": "原搜索也记录了恰好达到下限的组合；排序还会考虑时段和软偏好。",
            "not_found_in_complete_examined_scope": "原计算池内未找到恰好达到下限的组合；结论不覆盖学校全部课程。",
            "search_incomplete": "原搜索未完成，不能排除恰好达到下限的其他组合。",
        }.get(exact, "已保存记录不足以判断是否存在恰好达到下限的其他组合。"))
    if "scope_limits" in sections and (summary.get("scope") or {}).get("limit_reached") is True:
        lines.append("原搜索达到预算上限，结果只覆盖已检查部分。")
    if "unknowns" in sections:
        lines.append("这是历史课程条件参考，正式适用仍待核验。")
    lines.append("本次只解释已有结果；计算细节见依据。")
    return "\n\n".join(lines)
