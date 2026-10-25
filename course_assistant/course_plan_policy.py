"""Pure user-intent policy for bounded course-data scheduling tests.

Model-selected references are not change authorization. Unknown hard conditions
remain blocking; clock words are never translated into guessed period numbers.
This module reads no source, changes no account and runs no planner.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import re

from .planner import _number


VERSION = "course-plan-policy/3"
_DAY = re.compile(r"(?:周|星期)([一二三四五六日天1-7])")
_PERIOD_TOKEN = r"第?\s*[0-9]+\s*节?"
_PERIOD_CONNECTOR = r"(?:到|至|[-—–~～、,，]|和|及)"
_PERIOD_START = r"(?:第\s*[0-9]+|(?<![0-9.\-—–~～第])[0-9]+)\s*节?"
_PERIOD = re.compile(_PERIOD_START + r"(?:\s*" + _PERIOD_CONNECTOR + r"\s*" + _PERIOD_TOKEN + r")*")
_HARD = re.compile(r"必须|务必|一定|不得|不能|不许|不要(?:排|选|安排)|别(?:给我)?(?:排|选|安排)|只能|不接受|绝不|硬(?:性|条件)|至少|最多|最低|最高|不少于|不超过|上限|下限|正好|恰好|刚好")
_SPACE = re.compile(r"\s+")
_SOFT = re.compile(r"尽量|最好|优先|不强求|不一定|不必|不用非得|没必要")
_SOFT_ADDITIVE = r"(?:也|同样(?:地)?|另外|此外|还)"
_REPORTED = re.compile(r"如果|假如|假设|要是|比如|例如|示例|引用|原文|(?:老师|资料|他|她|你)(?:说|建议)|会怎样|会有什么|有什么影响|为什么|怎么样|是否已经|有没有(?:被)?(?:取消|解除)")
_NEGATED_CHANGE = re.compile(r"(?:不要|别|不能|不得|不许|不想|不希望)(?:给我|帮我|去|再|把.{0,20})?(?:解除|解锁|取消|放宽|收紧|调整|变更|降低|调低|去掉|改|设为|设置为|解开|锁定|固定住|保留|排除|禁用)")
_DENIED_INTENT = re.compile(r"不是|并非|(?:没有|没|未曾|从未)(?:说|要求|希望|打算|同意|授权|让|想)|不(?:需要|打算|准备|希望|想要|想|愿意|同意).*(?:解除|解锁|取消|放宽|收紧|调整|变更|改|设为|设置为|锁定|保留|排除)|不接受|不行")
_NUMBER = r"(?:[0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十百]+)"
_COURSE_COUNT = re.compile(r"(?:选择?|挑选?|安排|推荐|给我|改成|改为|调整为|正好|恰好|刚好|必须|只要|就要)(" + _NUMBER + r"|两)门")
_COURSE_COUNT_LITERAL = re.compile(r"(?:" + _NUMBER + r"|两)门(?:课程|课)?\Z")
_PROCESS_ITEM = r"(?:来源|依据|证据|引用|未知项|未知部分|未确认项|未确认部分|未确认内容|未核实项|不确定项|不确定性|限制|边界|哪些(?:尚未|还未|没有|未)(?:确认|核实))"
_PROCESS_ITEMS = _PROCESS_ITEM + r"(?:(?:以及|和|及|与|、)" + _PROCESS_ITEM + r")*"
_PROCESS_OBLIGATION = re.compile(
    r"(?:请你?|麻烦|必须|务必|一定|需要|应当|应该|要|先|再|最后|本轮|本次|这次|实际|真正|确实|如实)*"
    r"(?:"
    r"(?:重新|再次|再)?(?:执行)?(?:计算|重算|实算|校验|核算)"
    r"|(?:调用|使用|通过|用)(?:已声明的?|已有的?|现有的?|支持的?|可用的?|相关的?|相应的?|真实的?|确定性)?工具(?:进行|执行|来|做)?(?:实际|真正)?(?:重新计算|计算|重算|校验|核对)?"
    r"|(?:说明|解释|标明|列出|注明|交代|展示|给出)(?:实际|真实|本轮|已有)?" + _PROCESS_ITEMS +
    r"|把" + _PROCESS_ITEMS + r"(?:说明白|讲清楚|说明清楚)"
    # A complete evidence-interpretation obligation is not a schedule rule.
    # Keep this anchored so neighboring time/credit/campus clauses survive.
    r"|(?:不能|不得|不要|不应)(?:把|将)(?:未见|未知|未查到|没有查到|未返回)(?:的)?成绩"
    r"(?:当作|当成|视为|认定为|判断为)(?:没学过|未学过|没修过|未修过|没有修过|未修读)"
    r")(?:一下|一遍|一次)?[。.!！]?\Z")
_PRESERVE_REFERENCE = (r"(?:(?:其他|其余|其它|剩余|原有|原来|之前|此前|已有|既有|原先|原)(?:的)?(?:条件|要求|偏好|约束)"
    r"|(?:(?:其他|其余|其它|剩余|原有|原来|之前|此前|已有|既有|原先|上轮|先前|刚才|当前|原)(?:的)?)?"
    r"(?:课程范围|课程池|学分条件|学分要求|目标学分|最低学分|最高学分|学分上限|学分下限)"
    r"|(?:之前|此前|原有|已有|既有|原来|原先|上轮|先前|刚才|当前|原)(?:的)?(?:锁定(?:项|条件|班次)?|锁|固定项|固定安排|固定课程)"
    r"|锁定(?:项|条件|班次)?|固定项|固定安排|既有锁定|已锁定的?(?:班次|课程|项)?|已固定的?(?:安排|课程))")
_PRESERVE_NOUN = re.compile(_PRESERVE_REFERENCE + r"\Z")
_PRESERVE_SUFFIX = r"(?:都|均|全部|继续|仍然)?(?:保留|不变|不动|照旧|保持(?:不变|原样)?|维持(?:不变|原样)?)"
_PRESERVE_END = re.compile(_PRESERVE_SUFFIX + r"[。.!！]?\Z")
_PRESERVE_CONDITIONS = re.compile(r"(?:请|必须|务必|继续|仍然)?(?:" + _PRESERVE_REFERENCE + _PRESERVE_SUFFIX
    + r"|(?:保留|沿用|保持|维持)" + _PRESERVE_REFERENCE + r"(?:不变|原样)?)[。.!！]?\Z")
_FRIDAY = r"(?:周五|星期五|周5|星期5)"
_EMPTY = r"(?:空闲|没课|无课|留空|空)"
_FRIDAY_PREDICATES = {
    "hard": r"(?:(?:必须|务必|一定)(?:保持|留出|留)?" + _EMPTY + r"|(?:不能|不得|不许)(?:有课|上课|排课)|(?:不要|别)(?:给我)?(?:排课|安排课))",
    "soft": r"(?:尽量|最好|优先|不强求|不一定|不必|不用非得|没必要)(?:保持|留出|留)?" + _EMPTY,
    "none": r"(?:(?:可以|允许|接受)(?:有课|上课|排课)|(?:有课|上课|排课)(?:也可以|也行|能接受|没关系|可以))",
}
_LEVEL_CHANGE = r"(?:(?:改|调整|放宽|收紧|变|降)(?:成|为|到)|设(?:置)?为)"


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 200 and "\x00" not in value


def _norm(value):
    return _SPACE.sub("", value).casefold()


def _strong_hard(text):
    # A soft modal has priority over the embedded positive word 一定/必须.
    masked = re.sub(r"不一定|不必|不用非得|没必要|不强求", "", text)
    return re.search(r"必须|务必|一定|不得|不能|不许|只能|硬|至少|最多|最低|最高|不少于|不超过|上限|下限|正好|恰好|刚好", masked) is not None


def _asserted_request(clause):
    """Check assertion scope before interpreting a target or operation.

    Denial/report/conditional frames dominate contained action words. Polite
    action questions are requests; questions about consequences are not.
    """
    masked = re.sub(r"能不能|可不可以", "可以", clause)
    if _REPORTED.search(masked) or _DENIED_INTENT.search(masked) or _NEGATED_CHANGE.search(masked):
        return False
    if re.search(r"[?？吗]", clause) and not re.search(r"(?:帮我|帮忙|麻烦|请|能否|能不能|可以|我想|我要|我希望).*(?:解除|解锁|取消|解开|放宽|改|调|设为|设置为|锁定|保留|排除|留空|空闲)", clause):
        return False
    return True


def _number_text(value):
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    if re.fullmatch(r"[一二三四五六七八九十百]+", value):
        if value == "一百" or value == "百":
            value = "100"
        elif "十" in value and value.count("十") == 1:
            left, right = value.split("十")
            if (left and left not in digits) or (right and right not in digits):
                return None
            value = str((digits[left] if left else 1) * 10 + (digits[right] if right else 0))
        elif value in digits:
            value = str(digits[value])
        else:
            return None
    number = _number(value)
    return format(number, "f") if number is not None and 0 < number <= 100 else None


def _clauses(message, *, preserve_conjunctions=False):
    # A comma within an explicit period list is not a task-clause separator,
    # including forms whose individual items each carry 第/节.
    period_commas = {index for match in _PERIOD.finditer(message) if "节" in match[0]
                     for index in range(match.start(), match.end()) if message[index] in ",，"}
    # Keep a question mark in its own clause so assertion checks still see it.
    separator = r"(?<![0-9])[，,]|[，,](?![0-9])|[。；;\n]|(?<=[?？])"
    if not preserve_conjunctions:
        separator += r"|(?:然后|同时|并且)|(?<!一)并(?=把|将|请|解除|取消|周|星期)"
    parts, start = [], 0
    for match in re.finditer(separator, message):
        if match.start() in period_commas:
            continue
        parts.append(message[start:match.start()].strip())
        start = match.end()
    parts.append(message[start:].strip())
    return [part for part in parts if part]


def _requirement_parts(text):
    """Preserve every bounded conjunct and its inherited hard/soft modal."""
    if _PROCESS_OBLIGATION.fullmatch(_norm(text)):
        return [(text, False)]
    def split(pattern, value):
        period_spans = [(match.start(), match.end()) for match in _PERIOD.finditer(value) if "节" in match[0]]
        pieces, start = [], 0
        for match in re.finditer(pattern, value):
            if any(begin <= match.start() < end for begin, end in period_spans):
                continue
            pieces.append(value[start:match.start()].strip())
            start = match.end()
        return pieces + [value[start:].strip()]
    parts = []
    for part in split(r"并且|而且|同时|然后|但是|不过|且|并|但", text):
        # 来源和未知项 are one complete explanation obligation; period
        # lists likewise retain their own conjunctions.
        parts.extend([part] if _PROCESS_OBLIGATION.fullmatch(_norm(part)) else split(r"以及|和|及|与|、", part))
    result, soft = [], False
    for part in parts:
        if not part:
            continue
        if _SOFT.search(part) and not _strong_hard(part):
            soft = True
        elif _HARD.search(part) or _strong_hard(part):
            soft = False
        result.append((part, soft))
    return result


def _period_scope(text):
    """Read one complete range or pure list; never return a prefix match."""
    normalized = _norm(text)
    matches = [match for match in _PERIOD.finditer(normalized) if "节" in match[0]]
    if not matches:
        return None, "invalid_period_scope" if "节" in normalized or re.search(r"第[0-9]", normalized) else None
    if len(matches) != 1:
        return None, "multiple_time_scopes_require_review"
    match = matches[0]
    raw = match[0]
    ranges = re.findall(r"到|至|[-—–~～]", raw)
    lists = re.findall(r"[、,，]|和|及", raw)
    # A second range, a union of ranges, or an unfinished endpoint needs
    # clarification rather than a guessed union or first-period fallback.
    if len(ranges) > 1 or ranges and lists:
        return None, "multiple_time_scopes_require_review"
    before, after = normalized[:match.start()], normalized[match.end():]
    if (not raw.endswith("节") or re.search(r"(?:第[0-9]+|到|至|[-—–~～、])$", before)
            or re.match(r"(?:到|至|[-—–~～、]|以后|之后|以前|之前|起|开始|往后)", after)
            or re.match(r"[，,](?:第?[0-9]|第|到|至)", after)):
        return None, "invalid_period_scope"
    values = [int(value) for value in re.findall(r"[0-9]+", raw)]
    if any(not 1 <= value <= 60 for value in values):
        return None, "invalid_period_scope"
    if ranges:
        if len(values) != 2 or values[0] > values[1]:
            return None, "invalid_period_scope"
        values = list(range(values[0], values[1] + 1))
    return {"raw": raw, "periods": sorted(set(values))}, None


def _course_count_records(text):
    normalized = _norm(text)
    if re.search(r"(?:至少|最多|不少于|不超过)(?:选)?" + _NUMBER + r"门", normalized):
        return [{"text": text, "kind": "unresolved", "code": "course_count_range_requires_review"}]
    records = []
    for match in _COURSE_COUNT.finditer(normalized):
        value = _number_text(match[1].replace("两", "二"))
        number = _number(value) if value is not None else None
        if number is None or number != int(number) or not 1 <= number <= 12:
            records.append({"text": text, "kind": "unresolved", "code": "unsupported_course_count"})
        else:
            records.append({"text": text, "kind": "course_count", "value": int(number)})
    return records


def _parse_hard(text):
    normalized = _norm(text)
    if _PROCESS_OBLIGATION.fullmatch(normalized):
        # The caller still has to execute declared tools and report their
        # actual outcomes. This is no promise of a solution or new capability.
        return [{"text": text, "kind": "boundary", "value": "already_enforced"}]
    if _PRESERVE_CONDITIONS.fullmatch(normalized):
        return [{"text": text, "kind": "boundary", "value": "already_enforced"}]
    if _SOFT.search(text) and not _strong_hard(text):
        return []
    parts = _requirement_parts(text)
    if len(parts) > 1:
        shared_controls = set()
        for index, (part, _) in enumerate(parts):
            if _PRESERVE_END.search(_norm(part)):
                shared_controls.update(previous for previous in range(index)
                    if _PRESERVE_NOUN.fullmatch(_norm(parts[previous][0])))
            if (_PRESERVE_CONDITIONS.fullmatch(_norm(part))
                    and re.match(r"(?:请|必须|务必|继续|仍然)?(?:保留|沿用|保持|维持)", _norm(part))):
                shared_controls.update(following for following in range(index + 1, len(parts))
                    if _PRESERVE_NOUN.fullmatch(_norm(parts[following][0])))
        return [record for index, (part, soft) in enumerate(parts) if not soft for record in
            ([{"text": part, "kind": "boundary", "value": "already_enforced"}] if index in shared_controls else _parse_hard(part))]
    if re.search(r"不(?:自动|执行|进行)?(?:选退课|选课操作|退课操作|保存|导出|发布|扩采)|不伪造|不补(?:造|演示)", normalized):
        return [{"text": text, "kind": "boundary", "value": "already_enforced"}]
    records = _course_count_records(text)
    preserved_count = re.fullmatch(r"(?:保留|沿用|保持|维持)(?:刚才的?|原来的?|原有的?|现有的?)?(" + _NUMBER + r"|两)门(?:课程|课)?", normalized)
    if not records and preserved_count:
        # Restating a count is a constraint, not permission to change an
        # inherited count. Keep both so conflicting preservation is rejected.
        records = [{**record, "text": text} for record in _course_count_records("选" + preserved_count[1] + "门")]
    if not records and _COURSE_COUNT_LITERAL.fullmatch(normalized):
        # A conjunct such as '课程范围、3门课和至少6学分都保留'
        # restates the count. Retain inherited requirements so a different
        # number conflicts instead of silently changing the saved count.
        records = [{**record, "text": text} for record in _course_count_records("选" + normalized)]
    for prefix, kind in ((r"至少|最低|不少于|下限", "target_credits"), (r"最多|最高|不超过|上限", "max_credits"),
                         (r"正好|恰好|刚好", "exact_credits")):
        match = re.search(r"(?:" + prefix + r")(?:学分)?(?:改为|调整为|设为|为|是|到)?(" + _NUMBER + r")(?:个)?学分", normalized)
        if match and (number := _number_text(match[1])) is not None:
            for field in (("target_credits", "max_credits") if kind == "exact_credits" else (kind,)):
                records.append({"text": text, "kind": field, "value": number})
    days = list(_DAY.finditer(normalized))
    if days:
        if len(days) > 1:
            return [{"text": text, "kind": "unresolved", "code": "multiple_time_scopes_require_review"}]
        day = {c: i + 1 for i, c in enumerate("一二三四五六日")}.get(days[0][1])
        day = day if day is not None else 7 if days[0][1] == "天" else int(days[0][1])
        period, period_error = _period_scope(normalized)
        if period_error:
            return [{"text": text, "kind": "unresolved", "code": period_error}]
        if period:
            if not re.search(r"空|禁排|不排(?!除)|避开|留给|有实验|有安排|别.*(?:排|安排|有课)|不(?:要|能|许|得).*(?:排|有课|上课|安排)", normalized):
                return [{"text": text, "kind": "unresolved", "code": "unsupported_positive_schedule_requirement"}]
            records.append({"text": text, "kind": "busy", "value": {"weekday": day, "periods": period["periods"]}})
        elif re.search(r"上午|下午|晚上|早八|早上|中午|[0-9一二三四五六七八九十]+点|[0-9]+[:：][0-9]+", normalized):
            return [{"text": text, "kind": "unresolved", "code": "clock_period_mapping_unverified", "weekday": day}]
        elif re.search(r"空|没课|无课|不(?:要|能|许|得).*(?:上课|有课|排课|安排课)|别.*(?:排课|安排课)|整天", normalized):
            if day == 5:
                records.append({"text": text, "kind": "friday_free", "value": "hard"})
            else:
                # All possible period identifiers in the bounded engine, not
                # a claim that the school has sixty periods in one day.
                records.append({"text": text, "kind": "busy", "value": {"weekday": day, "periods": list(range(1, 61))}})
    elif re.search(r"禁排|不排(?!除)|留空", normalized):
        period, period_error = _period_scope(normalized)
        unresolved = {"text": text, "kind": "unresolved", "code": period_error or "unsupported_hard_condition"}
        if period is not None:
            residual = normalized.replace(period["raw"], "")
            residual = re.sub(r"(?:必须|务必|一定)?(?:禁排|不排(?:课)?|留空)", "", residual)
            if not _HARD.search(residual):
                # Intent metadata only: periods without a weekday cannot
                # become an executable block until that weekday is supplied.
                unresolved["periods_without_weekday"] = period["periods"]
        return records + [unresolved]
    return records or [{"text": text, "kind": "unresolved", "code": "unsupported_hard_condition"}]


def _soft_scope(text):
    """Read a complete, asserted soft free-time request, never a clock guess."""
    if not _text(text) or not _asserted_request(text) or re.search(r'[“"‘][^”"’]*[”"’]', text):
        return None
    normalized = _norm(text)
    days = list(_DAY.finditer(normalized))
    if len(days) != 1 or not _SOFT.search(normalized) or _strong_hard(normalized):
        return None
    if re.search(r"上午|下午|晚上|早八|早上|中午|[0-9一二三四五六七八九十]+点|[0-9]+[:：][0-9]+", normalized):
        return None
    period, error = _period_scope(normalized)
    if error:
        return None
    residual = normalized.replace(days[0][0], "", 1)
    if period:
        residual = residual.replace(period["raw"], "", 1)
    # A neighboring campus, positive class, or cancellation condition cannot
    # disappear just because it contains an empty-time word somewhere.
    if not re.fullmatch(r"(?:" + _SOFT_ADDITIVE + r"|请|麻烦|帮我|我希望|希望|让|使|的|改为|改成|调整为|设为|"
            r"尽量|最好|优先|不强求|不一定|不必|不用非得|没必要|保持|留出|留|整天|全天|都)*"
            r"(?:空闲|没课|无课|留空|空|避开|不要排课|别排课|不排课|不要有课|不上课)(?:一下|一点)?[。.!！]?", residual):
        return None
    weekday = {c: i + 1 for i, c in enumerate("一二三四五六日")}.get(days[0][1])
    weekday = weekday if weekday is not None else 7 if days[0][1] == "天" else int(days[0][1])
    return {"weekday": weekday, "periods": period["periods"] if period else None}


def _negated_soft_scope(clause):
    """Keep a denial of soft intent scoped over its complete conjunction.

    A negative request before the weekday, or a shared negative suffix, denies
    the wish itself. 不要排课 after a soft weekday remains a positive request
    for free time and is deliberately not matched by either frame.
    """
    text = _norm(clause)
    negative = r"(?:不要|不用|无需|不必|不需要|不想要|不想|不希望|不要求|别)"
    prefix = (negative + r"(?:" + _SOFT_ADDITIVE + r"|再|继续|给我|帮我|让|使|要求|安排|保持|保留|维持|把|将|去|要|的)*"
        r"(?:(?:尽量|最好|优先)(?:让|使)?)?" + _DAY.pattern)
    suffix = r"(?:都|均|全部)?" + negative + r"(?:了|吧)?[。.!！?？]?\Z"
    return _DAY.search(text) is not None and bool(re.search(prefix, text) or re.search(suffix, text))


def _soft_requirements(user_message, soft_preferences, inherited_policy, overrides):
    """Only current human assertions can add, remove, or replace soft scopes.

    The model's list may point to an unsupported wish in the human text, but it
    is not authority to create or report a preference. Canonical scopes and
    unhandled source text are derived from the human message or verified policy.
    """
    records = deepcopy((inherited_policy or {}).get("soft_time_preferences", []))
    # Migrate only the old policy's already-executed Friday preference; an
    # unhandled text entry is never authority to execute a new soft scope.
    if not records:
        records = [{"text": value, **scope} for value in (inherited_policy or {}).get("friday_soft_preferences", [])
                   if (scope := _soft_scope(value)) is not None]
    if "friday_free" in overrides and overrides["friday_free"] != "soft":
        records = [row for row in records if row["weekday"] != 5 or row["periods"] is not None]
    # Older unhandled lists could contain unverified model suggestions. Only
    # source-anchored entries are eligible to become current inherited wishes;
    # historical result projections themselves are never rewritten here.
    inherited_unhandled = list((inherited_policy or {}).get("unhandled_soft_source_texts", []))
    parts, rejected = [], []
    for clause in _clauses(user_message, preserve_conjunctions=True):
        if not any(is_soft or _SOFT.search(part) for part, is_soft in _requirement_parts(clause)):
            continue
        # Assertion scope belongs to the original clause. Splitting a denial,
        # hypothesis or quote at 和/且 must not turn its tail into a new wish.
        if not _asserted_request(clause) or _negated_soft_scope(clause) or re.search(r'[“"‘][^”"’]*[”"’]', clause):
            rejected.append(clause)
            continue
        for group in _clauses(clause):
            requirements = _requirement_parts(group)
            # A shared operation over multiple time scopes cannot safely be
            # represented by independently adding each bare tail. Retain the
            # whole request as unhandled instead of partially changing it.
            if (len(requirements) > 1 and len(_DAY.findall(group)) > 1 and re.search(
                    r"取消|删除|去掉|不再要求|不用再要求|改为|改成|调整为|换成|换为|保留|不变|不动|照旧|沿用|维持|保持原样", group)):
                rejected.append(group)
                continue
            parts.extend(part for part, is_soft in requirements if is_soft or _SOFT.search(part))
    handled, removed_friday = [], False
    for part in parts:
        if _PRESERVE_CONDITIONS.fullmatch(_norm(part)) or _PROCESS_OBLIGATION.fullmatch(_norm(part)):
            continue
        if not _asserted_request(part) or re.search(r'[“"‘][^”"’]*[”"’]', part):
            rejected.append(part)
            continue
        # Strict old-to-new scope changes require the old verified soft scope.
        # An ambiguous reference or hypothetical cannot remove anything.
        transition = re.fullmatch(r"(?:请|麻烦|帮我)?(?:把|将)?(.+?)(?:改为|改成|调整为|换成|换为)(.+)", part.strip())
        if transition:
            old_scope, new_scope = _soft_scope(transition[1]), _soft_scope(transition[2])
            if old_scope is not None and new_scope is not None and any(
                    all(row[key] == old_scope[key] for key in ("weekday", "periods")) for row in records):
                records = [row for row in records if not all(row[key] == old_scope[key] for key in ("weekday", "periods"))]
                records.append({"text": part, **new_scope})
                removed_friday |= old_scope == {"weekday": 5, "periods": None} and new_scope != old_scope
                handled.append(part)
                continue
        cancellation = re.fullmatch(r"(?:请|麻烦|帮我)?(?:取消|删除|去掉|不再要求|不用再要求)(?:原来|之前|原有|的)*(.+)", part.strip())
        scope = _soft_scope(cancellation[1]) if cancellation else None
        if scope is not None and any(all(row[key] == scope[key] for key in ("weekday", "periods")) for row in records):
            records = [row for row in records if not all(row[key] == scope[key] for key in ("weekday", "periods"))]
            removed_friday |= scope == {"weekday": 5, "periods": None}
            handled.append(part)
            continue
        preserve = re.search(r"保留|不变|不动|照旧|沿用|维持|保持原样", part)
        if preserve:
            scope = _soft_scope(re.sub(r"(?:请)?(?:保留|继续保留|不变|不动|照旧|沿用|维持|保持不变|保持原样)", "", part))
            if scope is not None and any(all(row[key] == scope[key] for key in ("weekday", "periods")) for row in records):
                handled.append(part)
                continue
        scope = _soft_scope(part)
        if scope is not None and not preserve:
            if not any(all(row[key] == scope[key] for key in ("weekday", "periods")) for row in records):
                records.append({"text": part, **scope})
            handled.append(part)
        else:
            rejected.append(part)
    # A verified Friday level transition may include its old hard endpoint,
    # so it is handled by authorize_changes rather than the soft-only parser.
    if overrides.get("friday_free") == "soft" and not any(row["weekday"] == 5 and row["periods"] is None for row in records):
        records.append({"text": "周五尽量空闲", "weekday": 5, "periods": None})
    unhandled = [value for value in inherited_unhandled if not ((scope := _soft_scope(value)) is not None
        and any(all(row[key] == scope[key] for key in ("weekday", "periods")) for row in records))] + rejected
    for preference in soft_preferences:
        scope = _soft_scope(preference)
        if (preference in handled or scope is not None and any(
                all(row[key] == scope[key] for key in ("weekday", "periods")) for row in records)):
            continue
        if _PRESERVE_CONDITIONS.fullmatch(_norm(preference)) or _PROCESS_OBLIGATION.fullmatch(_norm(preference)):
            continue
        # An unrecognized but explicit human wish can still be displayed as
        # unsupported. A stale or invented model suggestion cannot. Retain an
        # already captured original clause instead of its shortened paraphrase.
        if any(_norm(preference) in _norm(value) for value in unhandled):
            continue
        if any(preference in clause and _asserted_request(clause) and not _negated_soft_scope(clause)
                and not _strong_hard(preference) and not re.search(r'[“"‘][^”"’]*[”"’]', clause)
                for clause in _clauses(user_message, preserve_conjunctions=True)):
            unhandled.append(preference)
    # Merge same-day scopes as a union so overlapping requests do not gain
    # arbitrary weight and a whole-day preference dominates its subranges.
    by_day = {}
    for row in records:
        day, periods = row["weekday"], row["periods"]
        if day not in by_day:
            by_day[day] = deepcopy(periods)
        elif by_day[day] is not None:
            by_day[day] = None if periods is None else sorted(set(by_day[day]) | set(periods))
    scopes = [{"weekday": day, "periods": by_day[day]} for day in sorted(by_day)]
    # Persist a bounded canonical policy, rather than an unbounded history of
    # equivalent soft clauses. Its text is a display label, not a new quote.
    canonical = [{"text": "周" + "一二三四五六日"[row["weekday"] - 1]
        + ("" if row["periods"] is None else "第" + "、".join(map(str, row["periods"])) + "节") + "尽量空闲", **row}
        for row in scopes]
    return canonical, scopes, list(dict.fromkeys(value[:200] for value in unhandled if _text(value[:200])))[:6], removed_friday


def compile_hard_constraints(user_message, hard_preferences, *, inherited_policy=None, overrides=None, soft_preferences=None,
                             consumed_expressions=None):
    if soft_preferences is None:
        soft_preferences = []
    if (type(user_message) is not str or len(user_message) > 8000 or type(hard_preferences) is not list
            or len(hard_preferences) > 6 or any(not _text(p) for p in hard_preferences)
            or type(soft_preferences) is not list or len(soft_preferences) > 6
            or any(not _text(p) for p in soft_preferences)):
        raise ValueError("invalid_course_plan_policy")
    inherited = deepcopy((inherited_policy or {}).get("requirements", []))
    overrides = overrides or {}
    consumed_expressions = consumed_expressions or []
    if (type(consumed_expressions) is not list or len(consumed_expressions) > 36
            or any(not _text(value) or value not in user_message for value in consumed_expressions)):
        raise ValueError("invalid_course_plan_policy")
    def remaining(text):
        for expression in sorted(consumed_expressions, key=len, reverse=True):
            text = text.replace(expression, "")
        return text.strip()
    inherited = [r for r in inherited if r["kind"] not in overrides]
    extracted = [remaining(clause) for clause in _clauses(user_message, preserve_conjunctions=True)]
    extracted = [clause for clause in extracted if (_HARD.search(clause)
                 or _PERIOD.search(clause) and re.search(r"禁排|不排(?!除)|留空", clause)
                 or (_PRESERVE_END.search(_norm(clause)) or re.match(r"(?:请|必须|务必|继续|仍然)?(?:保留|沿用|保持|维持)", _norm(clause)))
                    and re.search(r"锁|固定|条件|要求|偏好|约束", clause))
                 and not (_SOFT.search(clause) and not _strong_hard(clause))
                 ]
    current = [r for clause in extracted for r in (_parse_hard(clause) if _asserted_request(clause) else
               [{"text": clause, "kind": "unresolved", "code": "non_asserted_hard_condition"}])]
    # A concrete request to select N courses is a count constraint even without
    # the word 'must'. Quoted, hypothetical and negated changes are not requests.
    counts = [record for clause in _clauses(user_message, preserve_conjunctions=True)
              if _asserted_request(clause) and not _SOFT.search(clause)
              for record in _course_count_records(clause)]
    current.extend(counts)
    if any(record["kind"] == "course_count" for record in counts):
        inherited = [record for record in inherited if record["kind"] != "course_count"]
    records = inherited + current
    for preference in hard_preferences:
        original_preference = preference
        preference = remaining(preference)
        if not preference:
            continue
        if _SOFT.search(preference) and not _strong_hard(preference):
            continue
        parsed = _parse_hard(preference)
        if inherited_policy is not None and _PRESERVE_NOUN.fullmatch(_norm(preference)):
            # A reference to an existing control proposes no new value.
            # Inheritance already preserves it; do not require prose history.
            continue
        if preference not in user_message and all(record["kind"] in overrides for record in parsed):
            continue
        if original_preference in user_message:
            records.extend(parsed if _asserted_request(preference) else [{"text": preference, "kind": "unresolved", "code": "non_asserted_hard_condition"}])
        elif any(preference == r["text"] for r in inherited):
            continue
        elif all(r["kind"] != "unresolved" and any(r["kind"] == source["kind"] and r.get("value") == source.get("value") for source in current + inherited) for r in parsed):
            # Model paraphrases may only repeat a constraint independently
            # derived from the actual human message.
            continue
        else:
            records.append({"text": preference, "kind": "unresolved", "code": "hard_condition_evidence_required"})
    # A natural follow-up containing only explicit periods may resolve one
    # inherited clock phrase without repeating its weekday or a fixed syntax.
    pending = [r for r in records if r["kind"] == "unresolved" and r.get("code") == "clock_period_mapping_unverified"]
    period, period_error = _period_scope(user_message)
    if (len({(r["text"], r.get("weekday")) for r in pending}) == 1 and period is not None and period_error is None
            and not _DAY.search(user_message) and _asserted_request(user_message)
            and not (_SOFT.search(user_message) and not _strong_hard(user_message))):
        source = pending[0]
        resolved = _parse_hard("周" + "一二三四五六日"[source["weekday"] - 1] + period["raw"] + "不能排课")
        if resolved[0]["kind"] == "busy":
            records = [r for r in records if r not in pending]
            records = [r for r in records if not (r["kind"] == "unresolved" and r.get("code") == "hard_condition_evidence_required"
                and any(p["kind"] == "busy" and p["value"] == resolved[0]["value"] for p in _parse_hard(r["text"])))]
            records = [r for r in records if not (r["kind"] == "unresolved"
                and r.get("periods_without_weekday") == resolved[0]["value"]["periods"])]
            records.append({**resolved[0], "text": source["text"] + "；" + user_message.strip()})
    # Conversely, an explicit weekday can complete one previously unscoped
    # period request. Do not borrow a weekday from an unrelated old sentence.
    unscoped = [r for r in records if r["kind"] == "unresolved" and "periods_without_weekday" in r]
    current_days = list(_DAY.finditer(user_message))
    if (len({tuple(r["periods_without_weekday"]) for r in unscoped}) == 1 and len(current_days) == 1
            and _asserted_request(user_message) and not (_SOFT.search(user_message) and not _strong_hard(user_message))
            and period_error is None and (period is None or period["periods"] == unscoped[0]["periods_without_weekday"])):
        raw = "第" + "、".join(map(str, unscoped[0]["periods_without_weekday"])) + "节"
        resolved = _parse_hard(current_days[0][0] + raw + "不能排课")[0]
        if resolved["kind"] == "busy":
            records = [r for r in records if r not in unscoped]
            records = [r for r in records if not (r["kind"] == "unresolved" and r.get("code") == "hard_condition_evidence_required"
                and any(p["kind"] == "busy" and p["value"] == resolved["value"] for p in _parse_hard(r["text"])))]
            records.append({**resolved, "text": unscoped[0]["text"] + "；" + user_message.strip()})
    unique = []
    for record in records:
        # Preservation and execution instructions are already enforced by the
        # service. They are not persistent scheduling constraints or budget items.
        if record["kind"] != "boundary" and record not in unique:
            unique.append(record)
    # Verified changes remove only their exact old-to-new expression. Any
    # separate, asserted request for the opposite level is still a conflict.
    friday_override = overrides.get("friday_free")
    remaining_current = [remaining(clause) for clause in _clauses(user_message, preserve_conjunctions=True)]
    if (friday_override is not None and (friday_override != "hard" and any(r["kind"] == "friday_free" for r in current)
            or friday_override != "soft" and any(_asserted_request(clause) and re.search(
                _FRIDAY + r"(?:仍然|依然|还是|仍)?" + _FRIDAY_PREDICATES["soft"], _norm(clause))
                for clause in remaining_current))):
        unique.append({"text": "本轮周五条件修改与另一明确要求冲突", "kind": "unresolved", "code": "unsupported_hard_condition"})
    patch, busy, unresolved = {}, [], []
    for record in unique:
        kind = record["kind"]
        if kind == "unresolved":
            unresolved.append({"text": record["text"][:200], "code": record["code"]})
        elif kind == "busy":
            value = record["value"]
            identity = sha256(repr((value["weekday"], value["periods"])).encode()).hexdigest()[:24]
            item = {"fixed_id": "user-busy-" + identity, **deepcopy(value), "source_refs": ["USERBUSY-" + identity]}
            if item not in busy:
                busy.append(item)
        elif kind in {"target_credits", "max_credits"}:
            value = _number(record["value"])
            old = _number(patch.get(kind))
            patch[kind] = record["value"] if old is None else format(max(old, value) if kind == "target_credits" else min(old, value), "f")
        elif kind == "friday_free":
            patch[kind] = "hard"
        elif kind == "course_count":
            if kind in patch and patch[kind] != record["value"]:
                unresolved.append({"text": "课程门数要求互相矛盾", "code": "conflicting_course_count"})
            patch[kind] = record["value"]
    if "target_credits" in patch and "max_credits" in patch and _number(patch["target_credits"]) > _number(patch["max_credits"]):
        unresolved.append({"text": "学分上下限互相矛盾", "code": "conflicting_credit_bounds"})
    if len(busy) > 20 or len(unique) > 20:
        unresolved.append({"text": "硬条件数量超出当前受控范围", "code": "hard_condition_budget_exceeded"})
    soft_records, soft_scopes, unhandled_soft, removed_friday = _soft_requirements(user_message, soft_preferences or [], inherited_policy, overrides)
    friday_soft = [row["text"] for row in soft_records if row["weekday"] == 5 and row["periods"] is None]
    if friday_soft and patch.get("friday_free") != "hard":
        patch["friday_free"] = "soft"
    elif removed_friday and patch.get("friday_free") != "hard":
        patch["friday_free"] = "off"
    return {"version": VERSION, "status": "needs_clarification" if unresolved else "ready", "engine_patch": patch,
            "blocked_slots": busy, "unresolved": unresolved[:6], "requirements": unique,
            "unhandled_soft_preferences": unhandled_soft,
            "unhandled_soft_source_texts": list(unhandled_soft),
            "soft_time_preferences": soft_records, "soft_free_slots": soft_scopes,
            "friday_soft_preferences": list(dict.fromkeys(friday_soft))[:6],
            "enforced_hard_preferences": list(dict.fromkeys(r["text"][:200] for r in unique if r["kind"] != "unresolved"))[:6]}


def _evidence_clauses(message, quote):
    if not _text(quote) or quote not in message:
        return []
    clauses = [clause for clause in _clauses(message) if quote in clause or clause in quote]
    if not clauses:
        return []
    for clause in clauses:
        if (not _asserted_request(clause)
                or re.search(r"[“\"‘][^”\"’]*(?:取消|解锁|解除|放宽|收紧|改成|改为|调整|设为|设置为)[^”\"’]*[”\"’]", clause)):
            return []
    return clauses + [quote] if len(clauses) > 1 else clauses


def _target_matches(text, refs, metadata, *, unlocking=False):
    normalized = _norm(text)
    locked = {ref for ref, row in metadata.items() if row.get("locked") is True}
    if unlocking and re.search(r"全部|所有|都(?:解|取消|不要)", normalized):
        return set(refs) == locked and bool(locked)
    if unlocking and len(locked) == 1 and set(refs) == locked and re.search(r"(?:刚才|刚刚|那个|这(?:个|一)|之前|原来|我加的).*(?:锁|锁定)|(?:解除|解开|取消|解锁)(?:我)?(?:的)?锁(?:定)?", normalized):
        return True
    # Match complete known aliases first. A short course name contained in a
    # longer named course is not a second user-selected target. Identical names
    # on different classes remain ambiguous and still need an option position.
    aliases = {}
    for ref, row in metadata.items():
        if unlocking and row.get("locked") is not True:
            continue
        for alias in (row.get("course_code"), row.get("course_name")):
            if type(alias) is str and alias:
                aliases.setdefault(_norm(alias), set()).add(ref)
    named = set()
    if aliases:
        pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
        for match in re.finditer(pattern, normalized):
            named.update(aliases[match[0]])
    for ref in refs:
        row = metadata.get(ref, {})
        direct = ref in named
        positioned = any(re.search(r"(?:方案)?" + re.escape(label.casefold()) + r"(?:中|里|的)?(?:第?(?:" + str(position) + "|" + "一二三四五六七八九十"[position-1] + r")(?:门|个)|首(?:门|个))", normalized)
                         for label, position in row.get("option_positions", {}).items() if 1 <= position <= 10)
        if not direct and not positioned:
            return False
        if direct and len(named) > 1:
            # Naming one course cannot silently choose one of its ambiguous
            # classes. An option position remains an explicit disambiguator.
            if not positioned:
                return False
    return bool(refs)


def _consumed_class_expressions(clause, field, refs, metadata):
    """Only the verified action and its actual target are already handled.

    Never consume a complete clause: it can also carry another hard time or
    academic condition which still requires independent compilation.
    """
    verb = {"unlock_class_refs": r"解除|解锁|解开|取消",
            "lock_class_refs": r"锁定|保留|固定住",
            "exclude_class_refs": r"排除|不选|不要(?:排|选|安排)|别(?:给我)?(?:排|选|安排)"}[field]
    expressions = []
    if field == "unlock_class_refs" and len(refs) == 1 and {ref for ref, row in metadata.items() if row.get("locked") is True} == set(refs):
        # This single owned lock has already passed target authorization.
        # Consume its bounded pronoun expression, including polite framing,
        # rather than ignoring the whole sentence and adjacent hard requests.
        pattern = (r"(?:能不能|可不可以|能否|可以)?(?:请|帮我|帮忙|麻烦)?(?:解除|解锁|解开|取消)"
            r"(?:我)?(?:刚才|刚刚|之前|原来)?(?:加的)?(?:的)?(?:那个|这个|这一个)?锁(?:定)?")
        expressions.extend(match[0] for match in re.finditer(pattern, clause))
    for ref in refs:
        row = metadata[ref]
        qualifiers = [r"(?:方案" + re.escape(label) + "|" + re.escape(label) + r"方案)(?:里的?|中的?|的)?"
                      for label in row.get("option_positions", {})]
        qualifier = "(?:" + "|".join(qualifiers) + ")?" if qualifiers else ""
        targets = [qualifier + re.escape(value) for value in (row.get("course_code"), row.get("course_name")) if type(value) is str and value]
        targets += [r"(?:方案)?" + re.escape(label) + r"(?:中|里|的)?(?:第?(?:" + str(position) + "|" + "一二三四五六七八九十"[position-1] + r")(?:门|个)|首(?:门|个))"
                    for label, position in row.get("option_positions", {}).items() if 1 <= position <= 10]
        for target in targets:
            # Match a small action phrase, stopping at the verified target.
            for pattern in (r"(?:必须|务必|一定)?(?:" + verb + r")(?:我)?(?:刚才|刚刚)?(?:的)?(?:这门|课程|班次)?\s*" + target,
                            r"(?:必须|务必|一定)?(?:把|将)?\s*" + target + r"(?:的)?(?:班次|课程|锁)?(?:必须|务必|一定)?(?:" + verb + r")"):
                expressions.extend(match[0] for match in re.finditer(pattern, clause, flags=re.IGNORECASE))
    return list(dict.fromkeys(expressions))


def _consumed_friday_expressions(clause, level, prior_level):
    """Consume a verified local change, never its neighboring requirements.

    Old and new endpoints must both be supported Friday levels; an unknown
    endpoint or mismatched prior level remains visible to the hard compiler.
    Character offsets preserve the exact original user evidence, including
    whitespace, rather than manufacturing a normalized quote.
    """
    if level not in _FRIDAY_PREDICATES or prior_level not in _FRIDAY_PREDICATES:
        return []
    offsets = [index for index, char in enumerate(clause) if not char.isspace()]
    normalized = _SPACE.sub("", clause)
    old, new = _FRIDAY_PREDICATES[prior_level], _FRIDAY_PREDICATES[level]
    pattern = (r"(?:把|将)?" + _FRIDAY + r"(?:的)?(?:要求|条件)?(?:从|由|原来的?|原先的?)?"
        + old + r"(?:的)?(?:要求|条件)?" + _LEVEL_CHANGE + r"(?:" + _FRIDAY + r")?" + new)
    patterns = [pattern]
    if level == "none":
        patterns.append(r"(?:取消|不再要求)" + _FRIDAY + old + r"(?:的)?(?:要求|条件)?")
    expressions = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            expression = clause[offsets[match.start()]:offsets[match.end() - 1] + 1]
            if _text(expression):
                expressions.append(expression)
    return list(dict.fromkeys(expressions))


def _friday_change_requested(clause, level):
    """Require a local Friday predicate, including in a multi-clause quote.

    A shared quote is evidence location, not permission to borrow another
    weekday's preference. Unknown ellipsis stays unverified rather than
    inferring that a predicate applies to an earlier Friday mention.
    """
    if level not in _FRIDAY_PREDICATES:
        return False
    # Mask before splitting conjunctions: quoted subclauses are not current
    # instructions, and replacing them with a marker cannot join endpoints.
    unquoted = re.sub(r"[“\"‘][^”\"’]*[”\"’]", "⟦引用⟧", clause)
    for sentence in _clauses(unquoted, preserve_conjunctions=True):
        for part, _ in _requirement_parts(sentence):
            text = _norm(part)
            days = list(_DAY.finditer(text))
            if not days or any(day[1] not in {"五", "5"} for day in days) or not _asserted_request(part):
                continue
            if (re.search(r"保留|不变|不动|照旧|沿用|维持|保持原样", text)
                    and not re.search(_LEVEL_CHANGE + r"|取消|不再要求", text)):
                continue
            if any(_consumed_friday_expressions(part, level, old) for old in _FRIDAY_PREDICATES):
                return True
            prefix = _FRIDAY + r"(?:的)?(?:要求|条件)?(?:仍然|依然|还是|仍)?(?:" + _LEVEL_CHANGE + r")?"
            patterns = [prefix + _FRIDAY_PREDICATES[level]]
            if level == "soft":
                if (_soft_scope(part) == {"weekday": 5, "periods": None}
                        and re.search(r"(?:尽量|最好|优先)避开" + _FRIDAY, text)):
                    return True
                patterns.append(r"(?:尽量|最好|优先)(?:让|使)?" + _FRIDAY + r"(?:保持|留出|留)?" + _EMPTY)
                patterns.append(prefix + r"(?:尽量|最好|优先)(?:不要|别)(?:有课|上课|排课|安排课)")
            elif level == "hard":
                patterns.append(r"(?:必须|务必|一定)(?:让|使)?" + _FRIDAY + r"(?:保持|留出|留)?" + _EMPTY)
            elif level == "none":
                patterns.append(r"(?:取消|不再要求)" + _FRIDAY + r"(?:的)?" + _EMPTY + r"(?:的)?(?:要求|条件)?")
            if any(re.search(pattern, text) for pattern in patterns):
                return True
    return False


def authorize_changes(user_message, proposed_patch, *, prior_constraints, class_metadata, change_evidence=None):
    if type(user_message) is not str or type(proposed_patch) is not dict or type(prior_constraints) is not dict or type(class_metadata) is not dict:
        raise ValueError("invalid_course_plan_policy")
    evidence = change_evidence or []
    if type(evidence) is not list or len(evidence) > 6:
        raise ValueError("invalid_course_plan_policy")
    verified, unresolved = {}, []
    for field, value in proposed_patch.items():
        if field not in {"friday_free", "target_credits", "max_credits", "lock_class_refs", "unlock_class_refs", "exclude_class_refs"}:
            raise ValueError("invalid_course_plan_policy")
        if (field in prior_constraints and (value == prior_constraints[field] or field in {"target_credits", "max_credits"}
                and _number(value) is not None and _number(value) == _number(prior_constraints[field]))) or type(value) is list and not value:
            continue
        rows = [row for row in evidence if type(row) is dict and row.get("field") == field]
        quotes = [row.get("quote") for row in rows] if rows else _clauses(user_message) + ([user_message] if len(user_message) <= 200 else [])
        authorized = False
        for quote in quotes:
            clauses = _evidence_clauses(user_message, quote)
            for clause in clauses:
                text = _norm(clause)
                if field == "friday_free":
                    authorized = _friday_change_requested(clause, value)
                elif field in {"target_credits", "max_credits"}:
                    numbers = [_number_text(match[1]) for match in re.finditer(r"(" + _NUMBER + r")(?:个)?学分", text)]
                    bare = re.fullmatch(r"(" + _NUMBER + r")(?:个)?(?:学分)?[。.!！]?", text)
                    if bare:
                        numbers.append(_number_text(bare[1]))
                    authorized = _number(value) is not None and any(_number(n) == _number(value) for n in numbers if n is not None)
                    authorized &= bool(bare or re.search(r"改|调|按|目标|至少|最多|最低|最高|上限|下限|推荐|算|就行|就好|即可", text))
                    if field == "max_credits":
                        authorized &= re.search(r"最高|最多|上限|不超过", text) is not None
                else:
                    verbs = {"unlock_class_refs": r"解除|解锁|解开|取消.*锁|锁.*取消|不(?:用再|再|必).*(?:锁|保留)",
                             "lock_class_refs": r"锁定|保留|固定住", "exclude_class_refs": r"排除|不选|不要(?:排|选|安排)|别(?:排|选|安排)"}
                    if rows and any("class_refs" in row and set(row["class_refs"]) != set(value) for row in rows):
                        continue
                    authorized = (type(value) is list and all(ref in class_metadata for ref in value)
                        and re.search(verbs[field], text) is not None and _target_matches(clause, value, class_metadata, unlocking=field == "unlock_class_refs"))
                if authorized:
                    verified[field] = {"quote": quote, "operation_verified": True}
                    if field in {"unlock_class_refs", "lock_class_refs", "exclude_class_refs"}:
                        verified[field]["consumed_expressions"] = _consumed_class_expressions(clause, field, value, class_metadata)
                    elif field == "friday_free":
                        verified[field]["consumed_expressions"] = _consumed_friday_expressions(clause, value, prior_constraints.get(field))
                    break
            if authorized:
                break
        if not authorized:
            unresolved.append({"text": {"unlock_class_refs": "解除个人锁定", "lock_class_refs": "新增个人锁定", "exclude_class_refs": "排除班次",
                "friday_free": "修改周五条件", "target_credits": "修改目标学分", "max_credits": "修改最高学分"}[field], "code": "current_user_change_evidence_required"})
    return {"status": "needs_clarification" if unresolved else "ready", "authorized_patch": deepcopy(proposed_patch) if not unresolved else {},
            "unresolved": unresolved, "verified_changes": verified}
