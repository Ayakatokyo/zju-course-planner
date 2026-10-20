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
            text = f"上次计算按不少于{minimum}学分处理目标"
            text += f"、最多{maximum}学分。" if maximum is not None else "，没有设置学分上限；这不等于必须恰好达到目标学分。"
            lines.append(text)
        else:
            lines.append("上次计算的学分下限未记录，不能补推当时的目标口径。")
    lines.append("这一步只解释已保存的计算记录，不新增教务读取、不改条件或重新求解。")
    if "observed_tradeoff" in sections:
        for option in summary.get("options", []):
            label = option.get("label")
            if label not in {"A", "B", "C"}:
                continue
            total, excess = _decimal(option.get("total_credits")), _decimal(option.get("excess_above_minimum"))
            text = f"方案{label}的来源学分为{total if total is not None else '未记录'}"
            if excess is not None:
                text += f"，比下限多{excess}学分"
            text += (f"；软空闲时段占用{_count(option.get('soft_meeting_count'))}段，"
                     f"周五占用{_count(option.get('friday_meeting_count'))}段，"
                     f"保留锁定{_count(option.get('locked_class_count'))}项。")
            lines.append(text)
        ranking = summary.get("ranking") or {}
        if ranking.get("version") == "current-term-observed-ranking/1" and ranking.get("criteria") == RANKING_CRITERIA:
            lines.append("当时先优先观察节次检查通过的方案，再减少软空闲时段占用，然后在已知学分方案中减少超出下限的学分；仍相同时再比较课程数量和固定的观察顺序。因此，空闲偏好满足得更好的方案可能比学分更接近目标的方案排前。")
        else:
            lines.append("该次计算没有保存完整排序依据，不能事后补写某套排在前面的具体原因。")
        exact = (summary.get("exact_target") or {}).get("status")
        if exact == "seen_feasible":
            lines.append("该次搜索记录了恰好达到学分下限的条件测试组合；它不一定排在首位。")
        elif exact == "not_found_in_complete_examined_scope":
            lines.append("该次已检查的输入范围内未记录到恰好达到下限的组合；这不是学校全部课程范围的结论。")
        elif exact == "search_incomplete":
            lines.append("该次搜索未完成，不能断言不存在恰好达到目标学分的组合。")
        else:
            lines.append("是否存在其他恰好达到目标学分的组合及其具体取舍未记录，不能只凭已保存方案断言不存在。")
    if "scope_limits" in sections:
        scope = summary.get("scope") or {}
        text = f"这次解释对应的计算输入池为{_count(scope.get('input_course_count'))}门课程"
        if type(scope.get("returned_search_count")) is int:
            text += f"；当时检索返回{_count(scope['returned_search_count'])}门"
        if type(scope.get("omitted_count")) is int and scope["omitted_count"]:
            text += f"，其中{_count(scope['omitted_count'])}门未进入计算池，未据此认定它们不适用"
        lines.append(text + "。")
        if scope.get("limit_reached") is True:
            lines.append("当时搜索达到预算上限，结论只限已经检查的部分。")
        elif scope.get("complete_within_input_scope") is True:
            lines.append("当时完成了输入池内的有界搜索；输入池仍不代表完整候选或学校全部开课。")
        else:
            lines.append("该次输入池内的搜索完整性未确认，不能据此排除其他组合。")
    if "unknowns" in sections:
        lines.append("这里只解释历史课程观察上的条件测试；当前开课、可选资格、培养适用、完整周次、校历和考试仍未核实。若需要恰好达到目标学分，可明确提出相同的学分上限后再计算；本次尚未作该修改。")
    return "\n\n".join(lines)
