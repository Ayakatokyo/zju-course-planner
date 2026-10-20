"""Owner-bound, opaque references for explicitly enabled course-data tests.

Only the school service supplies current-turn source validation. This facade
does local file reads and deterministic computation; it has no school transport,
model, enrollment, persistence or export capability.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import re
import secrets

from .current_term_recommendation import (CurrentTermCourseDataTest, search_delivery_courses, _load_delivery,
                                         parse_week_annotation, RANKING_VERSION, RANKING_CRITERIA)
from .local_reference_observations import ReferenceObservationError, _text
from .planner import _add, _number, fingerprint
from .course_plan_policy import compile_hard_constraints, authorize_changes
from .course_code_links import valid_observed_code_links


CURRENT_TERM = "2026-2027-AW"
SCHEMA_VERSION = "personal-course-data-test/1"
MODE = "current_term_course_data_test"
REFERENCE_MODE = "historical_course_observations"
VALIDATION_SCOPE = "observed_weekday_periods_conservative_overlap_policy"
LIMITATIONS = ["仅使用获准本学期历史课程观察，不代表当前仍开课、可选或有余量。",
    "仅核对已观察星期和节次；完整周次、校历、考试时间及资格未验证。",
    "培养适用、先修、学分认定与跨来源稳定身份未验证；不执行选退课、保存或导出。"]
NAMES = ("search_current_term_courses", "generate_current_term_plan", "update_current_term_plan", "get_current_term_plan")
_REFS = {"plan": re.compile(r"PLN-[a-f0-9]{48}\Z"), "class": re.compile(r"CLS-[a-f0-9]{48}\Z")}
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")
_STATES = {"available", "partial", "missing", "failed", "not_required", "not_requested", "unparseable", "changed"}
_REASONS = {None, "course_data_test_not_configured", "source_preparation_required", "timetable_not_complete",
    "timetable_unparseable", "personal_timetable_changed_requires_regeneration", "course_data_unavailable",
    "course_data_version_changed", "course_search_required", "no_observed_option_within_scope", "stale_plan_reference", "stale_plan_revision"}
_REASONS |= {"hard_preferences_unresolved", "change_evidence_required", "policy_context_required", "locked_scope_requires_revision"}


def _invalid():
    raise ValueError("invalid_course_plan_arguments")


def _new_reference(prefix):
    """Keep random opaque IDs compatible with unchanged sensitive-text checks.

    Rejection sampling retains the reference format and almost all random
    entropy. Never alter a candidate or fall back to a predictable identifier.
    """
    for _ in range(16):
        token = secrets.token_hex(24)
        if isinstance(token, str) and re.fullmatch(r"[a-f0-9]{48}", token) and not re.search(r"\d{18}", token):
            return prefix + token
    raise RuntimeError("course_plan_reference_unavailable")


def _strings(value, *, limit, pattern):
    return (type(value) is list and len(value) <= limit and len(set(v for v in value if type(v) is str)) == len(value)
            and all(type(v) is str and pattern.fullmatch(v) for v in value))


def course_plan_tool_schemas():
    """Fresh strict JSON schemas; no identity, path or observation arguments."""
    string = lambda **extra: {"type": "string", **extra}
    decimal = {"type": "number", "exclusiveMinimum": 0, "maximum": 100}
    codes = {"type": "array", "minItems": 1, "maxItems": 12, "uniqueItems": True,
             "items": string(pattern="^[A-Za-z0-9_.:-]{1,80}$")}
    refs = {"type": "array", "maxItems": 12, "uniqueItems": True, "items": string(pattern="^CLS-[a-f0-9]{48}$")}
    evidence = {"type": "array", "maxItems": 6, "items": {"type": "object", "additionalProperties": False,
        "properties": {"field": string(enum=["friday_free", "target_credits", "max_credits", "unlock_class_refs", "lock_class_refs", "exclude_class_refs"]),
            "quote": string(minLength=1, maxLength=200), "class_refs": refs}, "required": ["field", "quote"]}}
    schemas = [
        {"name": NAMES[0], "description": "按名称或精确代码检索已授权本学期课程观察；支持来源标题明确并列的另一代码，返回observed关系，不证明课程等价或培养适用。仅为历史课程数据测试候选。",
         "properties": {"query": string(minLength=1, maxLength=100), "target_term": string(enum=[CURRENT_TERM]),
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["query", "target_term"]},
        {"name": NAMES[1], "description": "在明确课程池内实算本学期课程数据测试组合；须先准备本轮主修、培养，排课另需本人完整课表。自动执行prepare_recommendation_inputs已核用户hard_preferences中任意星期的明确节次禁排，无需额外时间参数；下午等未明确节次须先澄清。courses仅课程组合，timetable另避开本人课表。本轮初次计算尚无任何方案且无必选、锁定、排除时，可同版本保留原条件，用本轮已检索的严格更大课程池追加重算一次；其他范围变更仍受保护。",
         "properties": {"recommendation_kind": string(enum=["courses", "timetable"]), "target_term": string(enum=[CURRENT_TERM]),
             "course_codes": codes, "target_credits": decimal, "max_credits": decimal,
             "friday_free": string(enum=["none", "soft", "hard"]),
             "required_course_codes": {**codes, "minItems": 0}, "change_evidence": evidence},
         "required": ["recommendation_kind", "target_term", "course_codes", "target_credits", "friday_free"]},
        {"name": NAMES[2], "description": "比较当前版本方案，或显式锁定/解锁/排除班次并重新计算；不保存、不选课。自动编译并继承prepare_recommendation_inputs已核hard_preferences中任意星期的明确节次禁排，无需额外时间参数；未明确节次先澄清。保持已有锁、本人课表固定项和数据版本。",
         "properties": {"plan_ref": string(pattern="^PLN-[a-f0-9]{48}$"), "action": string(enum=["compare", "revise"]),
             "expected_revision": {"type": "integer", "minimum": 0},
             "option_labels": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True, "items": string(enum=["A", "B", "C"])},
             "lock_class_refs": refs, "unlock_class_refs": refs, "exclude_class_refs": refs,
             "friday_free": string(enum=["none", "soft", "hard"]), "target_credits": decimal, "max_credits": decimal,
             "change_evidence": evidence},
         "required": ["plan_ref", "action", "expected_revision"]},
        {"name": NAMES[3], "description": "查看当前连接的课程数据测试选择引用；仅为历史方案对象元数据，更新仍需本轮本人来源。",
         "properties": {"plan_ref": string(pattern="^PLN-[a-f0-9]{48}$"),
             "option_labels": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True, "items": string(enum=["A", "B", "C"])}},
         "required": []},
    ]
    return [{"type": "function", "function": {"name": row["name"], "description": row["description"],
             "parameters": {"type": "object", "properties": deepcopy(row["properties"]),
                            "required": row["required"], "additionalProperties": False}}} for row in schemas]


def validate_course_plan_args(name, args):
    if name not in NAMES or type(args) is not dict:
        _invalid()
    schemas = {row["function"]["name"]: row["function"]["parameters"] for row in course_plan_tool_schemas()}
    schema = schemas[name]
    if not set(schema["required"]) <= set(args) or set(args) - set(schema["properties"]):
        _invalid()
    if name in NAMES[:2] and args["target_term"] != CURRENT_TERM:
        _invalid()
    for key in ("target_credits", "max_credits"):
        if key in args and (type(args[key]) not in {int, float} or (value := _number(args[key])) is None or not 0 < value <= 100):
            _invalid()
    if "friday_free" in args and (type(args["friday_free"]) is not str or args["friday_free"] not in {"none", "soft", "hard"}):
        _invalid()
    if "change_evidence" in args:
        values = args["change_evidence"]
        if type(values) is not list or len(values) > 6:
            _invalid()
        for row in values:
            if (type(row) is not dict or not {"field", "quote"} <= set(row) or set(row) - {"field", "quote", "class_refs"}
                    or type(row["field"]) is not str or row["field"] not in {"friday_free", "target_credits", "max_credits", "unlock_class_refs", "lock_class_refs", "exclude_class_refs"}
                    or type(row["quote"]) is not str or not 1 <= len(row["quote"]) <= 200
                    or "class_refs" in row and not _strings(row["class_refs"], limit=12, pattern=_REFS["class"])):
                _invalid()
    if name == NAMES[0]:
        if (type(args["query"]) is not str or not args["query"].strip() or len(args["query"]) > 100
                or "\x00" in args["query"] or type(args.get("limit", 10)) is not int or not 1 <= args.get("limit", 10) <= 10):
            _invalid()
    elif name == NAMES[1]:
        if (type(args["recommendation_kind"]) is not str or args["recommendation_kind"] not in {"courses", "timetable"}
                or not _strings(args["course_codes"], limit=12, pattern=_CODE) or not args["course_codes"]
                or not _strings(args.get("required_course_codes", []), limit=12, pattern=_CODE)
                or not set(args.get("required_course_codes", [])) <= set(args["course_codes"])
                or args.get("max_credits", args["target_credits"]) < args["target_credits"]):
            _invalid()
    elif name == NAMES[2]:
        if (type(args["plan_ref"]) is not str or not _REFS["plan"].fullmatch(args["plan_ref"])
                or type(args["action"]) is not str or args["action"] not in {"compare", "revise"}
                or type(args["expected_revision"]) is not int or args["expected_revision"] < 0):
            _invalid()
        for key in ("lock_class_refs", "unlock_class_refs", "exclude_class_refs"):
            if key in args and not _strings(args[key], limit=12, pattern=_REFS["class"]):
                _invalid()
        if args["action"] == "compare":
            labels = args.get("option_labels")
            if (set(args) != {"plan_ref", "action", "expected_revision", "option_labels"}
                    or not _strings(labels, limit=3, pattern=re.compile(r"[ABC]\Z")) or not labels):
                _invalid()
        elif "option_labels" in args:
            _invalid()
    else:
        if "plan_ref" in args and (type(args["plan_ref"]) is not str or not _REFS["plan"].fullmatch(args["plan_ref"])):
            _invalid()
        if "option_labels" in args and (not _strings(args["option_labels"], limit=3, pattern=re.compile(r"[ABC]\Z")) or not args["option_labels"]):
            _invalid()
    return deepcopy(args)


def _base(action, sources=None):
    return {"schema_version": SCHEMA_VERSION, "action": action, "mode": MODE, "target_term": CURRENT_TERM,
            "recommendation_kind": None,
            "data_version": None, "reference_mode": REFERENCE_MODE, "revision": None, "plan_ref": None,
            "source_preparation": {key: (sources or {}).get(key, {}).get("status", default) for key, default in
                (("major_academic", "missing"), ("curriculum", "missing"), ("timetable", "not_required"), ("exams", "not_requested"))},
            "search": None, "courses": [], "options": [], "locked_class_refs": [], "fixed_arrangement_count": 0,
            "constraints": None, "validation_scope": VALIDATION_SCOPE, "limitations": list(LIMITATIONS),
            "actions": [], "reason": None, "search_status": None, "selection_hint": None,
            "policy_status": None, "user_busy_arrangement_count": 0, "computation_summary": None}


class PersonalCoursePlanService:
    def __init__(self, delivery_index, target_term, authorize):
        self.delivery_index, self.target_term, self.authorize = delivery_index, target_term, authorize
        self._scope = fingerprint(authorize())
        self._plans = {}
        self._search_admission = None
        self._turn_sequence = 0

    def _check(self):
        if self.target_term != CURRENT_TERM:
            raise ValueError("course_data_test_not_configured")
        approved = self.authorize()
        if approved is False or fingerprint(approved) != self._scope:
            raise PermissionError("course_plan_scope_changed")
        return approved

    def describe(self):
        self._check()
        try:
            components, _ = _load_delivery(self.delivery_index, self._check)
            ready = any(type(row) is dict and row.get("term") == "2026-2027-1" for row in components["sections"])
        except (ReferenceObservationError, OSError):
            ready = False
        self._check()
        return {"status": "ready" if ready else "unavailable", "target_term": CURRENT_TERM, "reference_mode": REFERENCE_MODE}

    def current_plan_hint(self, active_plan_ref=None, *, current_dialogue=False):
        self._check()
        state = self._plans.get(active_plan_ref) if current_dialogue else next(reversed(self._plans.values()), None)
        return ({"plan_ref": state["plan_ref"], "revision": state["constraints"]["revision"],
                 "option_labels": list(state["options"]), "selection_only": True, "fresh_sources_required": True}
                if state else None)

    def _wrap(self, result, status="available"):
        self._check()
        return project_course_plan_result({"status": status, "data_kind": "course_data_test", "result": result})

    def _missing(self, action, sources, reason, *, state=None, policy=None):
        result = _base(action, sources)
        result.update(reason=reason, actions=["prepare_sources"] if reason in {"source_preparation_required", "timetable_not_complete", "timetable_unparseable"} else ["search"])
        if state:
            # These are owned selection references, not refreshed timetable
            # facts. A blocked attempt must not look as if it cleared locks.
            locked = set(state["constraints"]["locked_observation_ids"])
            result.update(plan_ref=state["plan_ref"], revision=state["constraints"]["revision"], recommendation_kind=state["kind"],
                locked_class_refs=[ref for ref, observation in state["classes"].items() if observation in locked])
        if policy is not None:
            result["policy_status"] = self._policy_status(policy)
            result["actions"] = ["generate"] if reason == "hard_preferences_unresolved" else ["revise"]
        return self._wrap(result, "not_ready")

    @staticmethod
    def _policy_status(policy):
        return {"status": policy["status"], "unresolved": deepcopy(policy.get("unresolved", [])),
                "enforced_hard_preferences": list(policy.get("enforced_hard_preferences", [])),
                "unhandled_soft_preferences": list(policy.get("unhandled_soft_preferences", []))}

    def _remember(self, state):
        if len(self._plans) >= 8 and state["plan_ref"] not in self._plans:
            self._plans.pop(next(iter(self._plans)))
        self._plans[state["plan_ref"]] = state

    def _origin_metadata(self, turn_identity):
        admission = self._search_admission
        return {"origin_turn_sequence": admission["turn_sequence"] if admission and admission["turn"] is turn_identity else None,
                "ever_had_options": False, "initial_pool_expansions": 0}

    def _initial_pool_expansion_open(self, state, turn_identity, kind):
        admission = self._search_admission
        return bool(state and admission and turn_identity is not None and admission["turn"] is turn_identity
            and state.get("origin_turn_sequence") == admission["turn_sequence"]
            and state.get("ever_had_options") is False and state.get("initial_pool_expansions") == 0
            and not state["options"] and state["kind"] == kind
            and state.get("search", {}).get("complete_within_observed_scope") is True
            and not any(state["constraints"][key] for key in
                        ("locked_observation_ids", "excluded_observation_ids", "required_course_codes")))

    def _can_expand_initial_pool(self, state, args, engine, turn_identity, kind):
        if (not self._initial_pool_expansion_open(state, turn_identity, kind)
                or not set(state["codes"]) < set(args["course_codes"])
                or args.get("required_course_codes")
                or self._search_admission["version"] != state["data_version"]
                or any(engine.version_refs[key] != state["constraints"]["input_version_refs"][key]
                       for key in ("dataset_version", "current_term", "policy_version"))):
            return False
        try:
            original = CurrentTermCourseDataTest.from_delivery(self.delivery_index, current_term=CURRENT_TERM,
                authorize=self._check, allowed_course_codes=state["codes"])
        except (ReferenceObservationError, OSError):
            return False
        # Verify both the remembered original scope and its exact observations
        # inside the newly loaded superset. A stable dataset label alone is not
        # enough when a local delivery has changed between reads.
        original_subset = {key: row for key, row in engine._pool.items() if row["course_code"] in set(state["codes"])}
        return original.version_refs == state["constraints"]["input_version_refs"] and original._pool == original_subset

    @staticmethod
    def _fixed(sources):
        source = sources.get("timetable", {})
        if source.get("status") != "available" or source.get("verified") is not True or type(source.get("records")) is not list:
            return None
        fixed = []
        for row_index, row in enumerate(source["records"]):
            if (type(row) is not dict or row.get("field_states", {}).get("meetings") != "known"
                    or type(row.get("meetings")) is not list or not row["meetings"]):
                return None
            if any(type(issue) is dict and issue.get("code") in {"conflicting_meeting_containers", "invalid_meeting_container", "meeting_not_object", "invalid_meeting_time_text"}
                   for issue in row.get("issues", [])):
                return None
            for segment, meeting in enumerate(row["meetings"]):
                if type(meeting) is not dict:
                    return None
                states = meeting.get("field_states", {})
                if any(type(issue) is dict and str(issue.get("field_path", "")).rsplit(".", 1)[-1] in {"weekday", "periods"}
                       for issue in meeting.get("issues", [])):
                    return None
                day, periods = meeting.get("weekday"), meeting.get("periods")
                if states.get("weekday") != "known" or states.get("periods") != "known":
                    # A complete, unambiguous source string is an independent
                    # projection, never a fallback over conflicting fields.
                    if (any(states.get(k) not in {"known", "unknown", None} for k in ("weekday", "periods"))
                            or states.get("time_raw") != "known" or type(meeting.get("time_raw")) is not str):
                        return None
                    match = re.fullmatch(r"(?:周|星期)([一二三四五六日天1-7])第?([0-9]+(?:[,，][0-9]+)*|[0-9]+-[0-9]+)节(?:\{([^{}]+)\})?", meeting["time_raw"])
                    if not match:
                        return None
                    if match[3] and parse_week_annotation(match[3])["state"] not in {"parity_only", "explicit_season_local_weeks"}:
                        return None
                    parsed_day = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}.get(match[1])
                    parsed_day = parsed_day if parsed_day is not None else int(match[1])
                    if "-" in match[2]:
                        start, end = map(int, match[2].split("-"))
                        if not 1 <= start <= end <= 60:
                            return None
                        parsed_periods = list(range(start, end + 1))
                    else:
                        parsed_periods = list(map(int, re.split("[,，]", match[2])))
                    if states.get("weekday") == "known" and day != parsed_day or states.get("periods") == "known" and periods != parsed_periods:
                        return None
                    day, periods = parsed_day, parsed_periods
                if (type(day) is not int or not 1 <= day <= 7 or type(periods) is not list or not periods
                        or any(type(p) is not int or not 1 <= p <= 60 for p in periods)):
                    return None
                fixed.append({"fixed_id": f"personal-{row_index}-{segment}", "weekday": day,
                              "periods": sorted(set(periods)), "source_refs": [source["observation_id"]]})
                if len(fixed) > 40:
                    return None
        return fixed

    def required_sources(self, name, args, *, active_plan_ref=None):
        """Server helper for the fresh receipt gate, without exposing plan state."""
        args = validate_course_plan_args(name, args)
        self._check()
        if name in (NAMES[0], NAMES[3]):
            return None
        if name == NAMES[1]:
            state = self._plans.get(active_plan_ref)
            return "timetable" if state and state["kind"] == "timetable" else args["recommendation_kind"]
        state = self._plans.get(args["plan_ref"])
        return state["kind"] if state else None

    @staticmethod
    def _computation_summary(state, labels):
        """Explain owned computation metadata without refreshing or recomputing.

        Missing historical records stay unknown. Search counts describe returned
        local search rows, not a reviewed recommendation scope or school coverage.
        """
        constraints, search = state["constraints"], state["search"]
        if type(search.get("nodes_visited")) is not int:
            # A clarification placeholder has constraints but has never run
            # the solver; it is not a previous computation to explain.
            return None
        minimum = _number(constraints["target_credits"])
        returned = state.get("returned_search_codes")
        scope_codes = set(state["codes"])
        options = []
        for row in state["projection"]:
            if row["label"] not in labels:
                continue
            total = _number(row["total_credits"])
            evaluation = row.get("soft_preference_evaluation")
            options.append({"label": row["label"], "total_credits": row["total_credits"],
                "excess_above_minimum": format(max(Decimal(0), _add(total, minimum.copy_negate())), "f") if total is not None else None,
                "soft_meeting_count": evaluation["violation_count"] if evaluation else None,
                "friday_meeting_count": row["friday_meeting_count"],
                "locked_class_count": sum(item["locked"] for item in row["classes"])})
        return {"origin": "owned_last_computation", "solver_executed_this_call": False,
            "current_sources_read": False,
            "credit_target": {"minimum": constraints["target_credits"], "maximum": constraints["max_credits"]},
            "scope": {"input_course_count": len(scope_codes),
                "returned_search_count": len(returned) if returned is not None else None,
                "omitted_count": len(set(returned) - scope_codes) if returned is not None else None,
                "complete_within_input_scope": search["complete_within_observed_scope"],
                "limit_reached": search["limit_reached"], "school_offerings_complete": False},
            "options": options, "ranking": deepcopy(state.get("ranking") or {"version": None, "criteria": []}),
            "rejections": {"status": "not_recorded", "counts": []},
            "exact_target": {"status": "not_recorded"}}

    def execute(self, name, args, *, sources=None, turn_identity=None, policy_context=None, enforce_policy=False):
        args = validate_course_plan_args(name, args)
        self._check()
        sources = sources if type(sources) is dict else {}
        if policy_context is not None and (type(policy_context) is not dict or not {"user_message", "hard_preferences", "active_plan_ref"} <= set(policy_context)
                or set(policy_context) - {"user_message", "hard_preferences", "active_plan_ref", "soft_preferences", "pending_messages"}
                or type(policy_context["user_message"]) is not str or len(policy_context["user_message"]) > 8000
                or type(policy_context["hard_preferences"]) is not list or len(policy_context["hard_preferences"]) > 6
                or any(type(p) is not str or not 1 <= len(p) <= 200 for p in policy_context["hard_preferences"])
                or type(policy_context.get("soft_preferences", [])) is not list or len(policy_context.get("soft_preferences", [])) > 6
                or any(type(p) is not str or not 1 <= len(p) <= 200 for p in policy_context.get("soft_preferences", []))
                or type(policy_context.get("pending_messages", [])) is not list or len(policy_context.get("pending_messages", [])) > 4
                or any(type(p) is not str or not 1 <= len(p) <= 8000 for p in policy_context.get("pending_messages", []))
                or sum(len(p.encode("utf-8")) for p in policy_context.get("pending_messages", [])) > 12000
                or policy_context["active_plan_ref"] is not None and (type(policy_context["active_plan_ref"]) is not str or not _REFS["plan"].fullmatch(policy_context["active_plan_ref"]))):
            _invalid()
        policy_on = enforce_policy or policy_context is not None
        active_ref = policy_context["active_plan_ref"] if policy_context is not None else None
        action = "search" if name == NAMES[0] else "generate" if name == NAMES[1] else "inspect" if name == NAMES[3] else args["action"]
        if name == NAMES[3]:
            state = (self._plans.get(active_ref) if policy_on else
                     self._plans.get(args["plan_ref"]) if "plan_ref" in args else next(reversed(self._plans.values()), None))
            if policy_on and "plan_ref" in args and args["plan_ref"] != active_ref:
                state = None
            if state is None:
                return self._missing(action, sources, "stale_plan_reference")
            labels = args.get("option_labels", list(state["options"]))
            if any(label not in state["options"] for label in labels):
                _invalid()
            hint_options, seen, omitted = [], set(), []
            for label in labels:
                option = next(o for o in state["projection"] if o["label"] == label)
                refs = {r["class_ref"] for r in option["classes"]}
                if len(seen | refs) > 24:
                    omitted.append(label)
                    continue
                seen |= refs
                hint_options.append({"label": label, "classes": [{k: r[k] for k in ("class_ref", "course_code", "course_name", "locked")} for r in option["classes"]]})
            result = _base(action)
            locked = set(state["constraints"]["locked_observation_ids"])
            locked_classes = [{**row, "locked": True} for ref, row in state["class_metadata"].items() if state["classes"][ref] in locked]
            result.update(plan_ref=state["plan_ref"], revision=state["constraints"]["revision"], data_version=state["data_version"], recommendation_kind=state["kind"],
                selection_hint={"plan_ref": state["plan_ref"], "revision": state["constraints"]["revision"],
                    "option_labels": list(state["options"]), "options": hint_options, "omitted_option_labels": omitted,
                    "locked_classes": locked_classes, "selection_only": True, "fresh_sources_required": True}, actions=["compare", "revise"])
            if state.get("policy"):
                result["policy_status"] = self._policy_status(state["policy"])
            result["computation_summary"] = self._computation_summary(state, labels)
            return self._wrap(result)
        state = (self._plans.get(args["plan_ref"]) if name == NAMES[2] else self._plans.get(active_ref) if policy_on and name == NAMES[1] else None)
        if name == NAMES[1] and policy_on and active_ref is not None and state is None:
            # A bounded in-memory cache may have evicted the owned plan. An
            # expired pointer is never permission to drop its locks/policy.
            return self._missing(action, sources, "stale_plan_reference")
        if name == NAMES[2] and policy_on and args["plan_ref"] != active_ref:
            state = None
        if name == NAMES[2] and state is None:
            return self._missing(action, sources, "stale_plan_reference")
        if name == NAMES[2] and state and args["expected_revision"] != state["constraints"]["revision"]:
            return self._missing(action, sources, "stale_plan_revision", state=state)
        if name == NAMES[0]:
            try:
                found = search_delivery_courses(self.delivery_index, args["query"], current_term=CURRENT_TERM,
                                                authorize=self._check, limit=args.get("limit", 10))
            except (ReferenceObservationError, OSError):
                return self._missing(action, sources, "course_data_unavailable")
            result = _base(action)
            result.update(data_version=found["dataset_version"], search={"query": args["query"],
                "matching_course_count": found["matching_course_count"], "truncated": found["truncated"]},
                courses=[{"course_code": r["course_code"], "course_name": r["course_name"], "credits": r["nominal_credits"],
                          "observed_section_count": r["section_count"], "observation_count": r["observation_count"],
                          "observed_code_links": r["observed_code_links"]} for r in found["courses"]], actions=["generate"])
            if turn_identity is not None:
                previous = self._search_admission
                same_turn = previous and previous["turn"] is turn_identity and previous["version"] == found["dataset_version"]
                admitted = set(previous["codes"]) if same_turn else set()
                if not same_turn:
                    self._turn_sequence += 1
                self._search_admission = {"turn": turn_identity, "version": found["dataset_version"],
                                          "codes": admitted | {r["course_code"] for r in found["courses"]},
                                          "turn_sequence": previous["turn_sequence"] if same_turn else self._turn_sequence}
            return self._wrap(result)
        # Prepare changes off the owned state: failed reference allocation must
        # not leave partial class metadata, a new revision, or changed locks.
        state = deepcopy(state)
        if policy_on and policy_context is None:
            return self._missing(action, sources, "policy_context_required", state=state)
        if name == NAMES[1]:
            admitted = self._search_admission
            if (turn_identity is None or not admitted or admitted["turn"] is not turn_identity
                    or not set(args["course_codes"]) <= admitted["codes"]):
                return self._missing(action, sources, "course_search_required")
        kind = args["recommendation_kind"] if name == NAMES[1] else state["kind"]
        if state and state["kind"] == "timetable":
            kind = "timetable"
        upgrading = state is not None and state["kind"] == "courses" and kind == "timetable"
        if any(sources.get(key, {}).get("verified") is not True or sources.get(key, {}).get("status") not in {"available", "partial"}
               for key in ("major_academic", "curriculum")):
            return self._missing(action, sources, "source_preparation_required", state=state)
        fixed = self._fixed(sources) if kind == "timetable" else []
        if fixed is None:
            return self._missing(action, sources, "timetable_unparseable" if sources.get("timetable", {}).get("status") == "available" else "timetable_not_complete", state=state)
        codes = args["course_codes"] if name == NAMES[1] else state["codes"]
        try:
            engine = CurrentTermCourseDataTest.from_delivery(self.delivery_index, current_term=CURRENT_TERM,
                authorize=self._check, allowed_course_codes=codes)
        except (ReferenceObservationError, OSError):
            return self._missing(action, sources, "course_data_unavailable", state=state)
        policy = compile_hard_constraints("", [])
        if policy_on:
            overrides = {}
            consumed_expressions = []
            if state:
                proposed = {key: args[key] for key in ("friday_free", "target_credits", "max_credits", "lock_class_refs", "unlock_class_refs", "exclude_class_refs") if key in args}
                metadata = deepcopy(state["class_metadata"])
                locked = set(state["constraints"]["locked_observation_ids"])
                for ref, row in metadata.items():
                    row["locked"] = state["classes"][ref] in locked
                    row["option_positions"] = {label: ids.index(state["classes"][ref]) + 1 for label, ids in state["options"].items() if state["classes"][ref] in ids}
                prior = {key: state["constraints"][key] for key in ("target_credits", "max_credits", "friday_free")}
                prior["friday_free"] = "none" if prior["friday_free"] == "off" else prior["friday_free"]
                change = authorize_changes(policy_context["user_message"], proposed, prior_constraints=prior,
                    class_metadata=metadata, change_evidence=args.get("change_evidence"))
                if change["status"] != "ready":
                    return self._missing(action, sources, "change_evidence_required", state=state, policy=change)
                overrides = {field: proposed[field] for field in change["verified_changes"] if field in {"friday_free", "target_credits", "max_credits"}}
                consumed_expressions = [expression for proof in change["verified_changes"].values() for expression in proof.get("consumed_expressions", [])]
            inherited_policy = state.get("policy") if state else None
            for pending_message in policy_context.get("pending_messages", []):
                inherited_policy = compile_hard_constraints(pending_message, [], inherited_policy=inherited_policy)
            policy = compile_hard_constraints(policy_context["user_message"], policy_context["hard_preferences"],
                inherited_policy=inherited_policy, overrides=overrides,
                soft_preferences=policy_context.get("soft_preferences", []), consumed_expressions=consumed_expressions)
            if state is None and args["friday_free"] != "none":
                # The required tool field is not authority to invent a first
                # preference. A verified explicit period scope is also not
                # permission to widen it into an all-day Friday preference.
                requested = args["friday_free"]
                human_messages = [policy_context["user_message"], *policy_context.get("pending_messages", [])]
                verified = policy["engine_patch"].get("friday_free") == requested and any(
                    authorize_changes(message, {"friday_free": requested}, prior_constraints={"friday_free": "none"},
                        class_metadata={})["status"] == "ready" for message in human_messages)
                if not verified:
                    rejected = {"status": "needs_clarification", "unresolved": [{"text": "首次周五全天条件需要当前本人明确要求",
                        "code": "current_user_change_evidence_required"}]}
                    return self._missing(action, sources, "change_evidence_required", policy=rejected)
            base_goal = args.get("target_credits", state["constraints"]["target_credits"] if state else None)
            base_maximum = args.get("max_credits", state["constraints"]["max_credits"] if state else None)
            goal = max(_number(base_goal) or Decimal(0), _number(policy["engine_patch"].get("target_credits")) or Decimal(0))
            maximums = [n for n in (_number(base_maximum), _number(policy["engine_patch"].get("max_credits"))) if n is not None]
            if maximums and goal > min(maximums):
                policy.update(status="needs_clarification", unresolved=policy["unresolved"] + [{"text": "目标学分与最高学分冲突", "code": "conflicting_credit_bounds"}])
        if policy["status"] != "ready":
            if state is None:
                constraints = engine.new_constraints(args["target_credits"], friday_free="off" if args["friday_free"] == "none" else args["friday_free"],
                    required_course_codes=args.get("required_course_codes", []), max_credits=args.get("max_credits"), fixed_meetings=fixed)
                state = {"plan_ref": _new_reference("PLN-"), "kind": kind, "codes": list(codes), "classes": {}, "class_metadata": {},
                    "options": {}, "projection": [], "constraints": constraints, "data_version": engine.dataset_version,
                    "search": {"limit_reached": False, "complete_within_observed_scope": False}, "personal_fixed": deepcopy(fixed)}
                state.update(self._origin_metadata(turn_identity))
            state["policy"] = policy
            self._remember(state)
            return self._missing(action, sources, "hard_preferences_unresolved", state=state, policy=policy)
        personal_fixed = deepcopy(fixed)
        fixed = fixed + policy["blocked_slots"]
        if len(fixed) > 40:
            policy.update(status="needs_clarification", unresolved=[{"text": "固定安排与硬条件超出当前计算范围", "code": "hard_condition_budget_exceeded"}])
            return self._missing(action, sources, "hard_preferences_unresolved", state=state, policy=policy)
        pool_expanded = False
        if state:
            if state["constraints"]["input_version_refs"] != engine.version_refs:
                if name != NAMES[1] or not self._can_expand_initial_pool(state, args, engine, turn_identity, kind):
                    return self._missing(action, sources, "locked_scope_requires_revision" if name == NAMES[1] else "course_data_version_changed", state=state)
                pool_expanded = True
            signature = lambda rows: sorted((r["weekday"], tuple(r["periods"])) for r in rows)
            if (name != NAMES[1] or pool_expanded) and signature(state.get("personal_fixed", state["constraints"]["fixed_meetings"])) != signature(personal_fixed):
                return self._missing(action, sources, "personal_timetable_changed_requires_regeneration", state=state)
            constraints = deepcopy(state["constraints"])
            if pool_expanded:
                # This is the sole narrow scope transition. The old scope was
                # revalidated above; the regular engine still validates every
                # inherited condition and performs a real revision below.
                constraints["input_version_refs"] = deepcopy(engine.version_refs)
            constraints["fixed_meetings"] = fixed
            if policy_on:
                constraints["soft_free_slots"] = deepcopy(policy["soft_free_slots"])
            for field, value in policy["engine_patch"].items():
                if field == "target_credits":
                    constraints[field] = format(max(_number(constraints[field]), _number(value)), "f")
                elif field == "max_credits":
                    constraints[field] = value if constraints[field] is None else format(min(_number(constraints[field]), _number(value)), "f")
                else:
                    constraints[field] = value
            if action == "compare":
                if any(label not in state["options"] for label in args["option_labels"]):
                    _invalid()
                output = engine.compare([state["options"][label] for label in args["option_labels"]], constraints)
                options = output["options"]
                labels = args["option_labels"]
                search = state["search"]
            else:
                patch = {key: deepcopy(args[key]) for key in ("target_credits", "max_credits", "expected_revision") if key in args}
                if policy_on:
                    patch["soft_free_slots"] = deepcopy(policy["soft_free_slots"])
                if "friday_free" in args:
                    patch["friday_free"] = "off" if args["friday_free"] == "none" else args["friday_free"]
                for field, value in policy["engine_patch"].items():
                    if field == "target_credits":
                        patch[field] = format(max(_number(patch.get(field, constraints[field])), _number(value)), "f")
                    elif field == "max_credits":
                        patch[field] = value if patch.get(field, constraints[field]) is None else format(min(_number(patch.get(field, constraints[field])), _number(value)), "f")
                    else:
                        patch[field] = value
                for external, internal in (("lock_class_refs", "locked_observation_ids"), ("unlock_class_refs", "unlock_observation_ids"), ("exclude_class_refs", "excluded_observation_ids")):
                    if external in args:
                        if any(ref not in state["classes"] for ref in args[external]):
                            _invalid()
                        values = [state["classes"][ref] for ref in args[external]]
                        patch[internal] = sorted(set(values + (constraints[internal] if internal in {"locked_observation_ids", "excluded_observation_ids"} else [])))
                        if internal == "locked_observation_ids" and len(patch[internal]) > 12:
                            _invalid()
                if name == NAMES[1]:
                    patch["expected_revision"] = constraints["revision"]
                    patch["required_course_codes"] = sorted(set(constraints["required_course_codes"]) | set(args.get("required_course_codes", [])))
                output = engine.revise(constraints, patch, limit=3)
                options, constraints, search = output["options"], output["constraints"], output["search"]
                labels = list("ABC"[:len(options)])
        else:
            if engine.dataset_version != self._search_admission["version"]:
                return self._missing(action, sources, "course_data_version_changed")
            credit_target = max(_number(args["target_credits"]), _number(policy["engine_patch"].get("target_credits")) or Decimal(0))
            maximum = args.get("max_credits")
            if "max_credits" in policy["engine_patch"]:
                maximum = policy["engine_patch"]["max_credits"] if maximum is None else min(_number(maximum), _number(policy["engine_patch"]["max_credits"]))
            constraints = engine.new_constraints(credit_target, friday_free=policy["engine_patch"].get("friday_free", "off" if args["friday_free"] == "none" else args["friday_free"]),
                required_course_codes=args.get("required_course_codes", []), max_credits=maximum, fixed_meetings=fixed,
                soft_free_slots=policy["soft_free_slots"])
            output = engine.recommend(constraints, limit=3)
            options, search = output["options"], output["search"]
            labels = list("ABC"[:len(options)])
            state = {"plan_ref": _new_reference("PLN-"), "kind": kind, "codes": list(codes), "classes": {}, "class_metadata": {}, "options": {}}
            state.update(self._origin_metadata(turn_identity))
        projected = []
        inverse = {oid: ref for ref, oid in state["classes"].items()}
        for label, option in zip(labels, options):
            classes = []
            for row in option["sections"]:
                oid = row["observation_id"]
                if oid not in inverse:
                    ref = _new_reference("CLS-")
                    inverse[oid] = ref
                    state["classes"][ref] = oid
                classes.append({"class_ref": inverse[oid], "course_code": row["course_code"], "course_name": row["course_name"],
                    "credits": row["credits"], "weekday_period_text": ["周" + "一二三四五六日"[m["weekday"] - 1] + "第" + "、".join(map(str, m["periods"])) + "节" for m in row["meetings"]],
                    "locked": oid in constraints["locked_observation_ids"]})
                state["class_metadata"][inverse[oid]] = {key: classes[-1][key] for key in ("class_ref", "course_code", "course_name")}
            projected.append({"label": label, "total_credits": option["total_credits"],
                "observed_slot_result": option["observed_slot_validation"]["result"], "full_semantics": "unverified", "classes": classes,
                "compared_meeting_pair_count": option["observed_slot_validation"]["compared_meeting_pair_count"],
                "friday_meeting_count": option["friday_meeting_count"],
                "soft_preference_evaluation": deepcopy(option["soft_preference_evaluation"])})
        self._check()
        if upgrading and not options:
            return self._missing(action, sources, "no_observed_option_within_scope", state=state, policy=policy if policy_on else None)
        if action != "compare":
            if pool_expanded:
                state.update(codes=list(codes), initial_pool_expansions=state["initial_pool_expansions"] + 1)
            state.update(kind=kind, constraints=constraints, options={label: option["observation_ids"] for label, option in zip(labels, options)},
                         search=search, projection=deepcopy(projected), data_version=engine.dataset_version,
                         ranking=deepcopy(output.get("ranking")),
                         policy=policy, personal_fixed=personal_fixed,
                         ever_had_options=state.get("ever_had_options", True) or bool(options))
            if name == NAMES[1]:
                state["returned_search_codes"] = sorted(self._search_admission["codes"])
            self._remember(state)
        result = _base(action, sources)
        result.update(data_version=engine.dataset_version, revision=constraints["revision"], plan_ref=state["plan_ref"], options=projected, recommendation_kind=kind,
            locked_class_refs=[inverse[oid] for oid in constraints["locked_observation_ids"] if oid in inverse], fixed_arrangement_count=len(personal_fixed),
            user_busy_arrangement_count=len(policy["blocked_slots"]), policy_status=self._policy_status(policy) if policy_on else None,
            constraints={"target_credits": constraints["target_credits"], "max_credits": constraints["max_credits"],
                "friday_free": "none" if constraints["friday_free"] == "off" else constraints["friday_free"],
                "required_course_codes": constraints["required_course_codes"],
                "soft_free_slots": sorted(([row for row in constraints.get("soft_free_slots", []) if row["weekday"] != 5]
                    + [{"weekday": 5, "periods": None}]) if constraints["friday_free"] == "soft"
                    else deepcopy(constraints.get("soft_free_slots", [])), key=lambda row: row["weekday"])},
            actions=["compare", "revise"] if projected else ["search", "generate", "revise"]
                if self._initial_pool_expansion_open(state, turn_identity, kind) else ["revise"],
            reason=None if projected else "no_observed_option_within_scope",
            search_status={"limit_reached": search["limit_reached"], "complete_within_observed_scope": search["complete_within_observed_scope"], "school_offerings_coverage_complete": False})
        return self._wrap(result, "available" if projected else "not_ready")


def _valid_soft_scopes(scopes, *, counts=False):
    fields = {"weekday", "periods", "meeting_count"} if counts else {"weekday", "periods"}
    return (type(scopes) is list and len(scopes) <= 7 and all(type(row) is dict and set(row) == fields
        and type(row["weekday"]) is int and 1 <= row["weekday"] <= 7
        and (row["periods"] is None or type(row["periods"]) is list and 1 <= len(row["periods"]) <= 60
            and all(type(n) is int and 1 <= n <= 60 for n in row["periods"])
            and row["periods"] == sorted(set(row["periods"])))
        and (not counts or type(row["meeting_count"]) is int and 0 <= row["meeting_count"] <= 800)
        for row in scopes) and [row["weekday"] for row in scopes] == sorted({row["weekday"] for row in scopes}))


def valid_computation_summary(summary):
    """Strict metadata contract; it carries no current-source or causal claim."""
    fields = {"origin", "solver_executed_this_call", "current_sources_read", "credit_target", "scope",
              "options", "ranking", "rejections", "exact_target"}
    credit = lambda v: type(v) is str and len(v) <= 32 and (n := _number(v)) is not None and n <= 1200
    count = lambda v, maximum=1000000: type(v) is int and 0 <= v <= maximum
    if (type(summary) is not dict or set(summary) != fields or summary["origin"] != "owned_last_computation"
            or summary["solver_executed_this_call"] is not False or summary["current_sources_read"] is not False):
        return False
    target, scope, ranking = summary["credit_target"], summary["scope"], summary["ranking"]
    if (type(target) is not dict or set(target) != {"minimum", "maximum"} or not credit(target["minimum"])
            or _number(target["minimum"]) <= 0 or target["maximum"] is not None and
            (not credit(target["maximum"]) or _number(target["maximum"]) < _number(target["minimum"]))):
        return False
    if (type(scope) is not dict or set(scope) != {"input_course_count", "returned_search_count", "omitted_count",
            "complete_within_input_scope", "limit_reached", "school_offerings_complete"}
            or not count(scope["input_course_count"], 12) or not scope["input_course_count"]
            or any(type(scope[k]) is not bool for k in ("complete_within_input_scope", "limit_reached"))
            or scope["complete_within_input_scope"] and scope["limit_reached"]
            or scope["school_offerings_complete"] is not False):
        return False
    returned, omitted = scope["returned_search_count"], scope["omitted_count"]
    if ((returned is None) != (omitted is None) or returned is not None and
            (not count(returned) or not count(omitted) or returned - scope["input_course_count"] != omitted)):
        return False
    if (type(ranking) is not dict or set(ranking) != {"version", "criteria"}
            or ranking not in ({"version": None, "criteria": []},
                               {"version": RANKING_VERSION, "criteria": RANKING_CRITERIA})
            or summary["rejections"] != {"status": "not_recorded", "counts": []}
            or summary["exact_target"] != {"status": "not_recorded"}):
        return False
    if type(summary["options"]) is not list or len(summary["options"]) > 3:
        return False
    labels = set()
    for row in summary["options"]:
        if (type(row) is not dict or set(row) != {"label", "total_credits", "excess_above_minimum",
                "soft_meeting_count", "friday_meeting_count", "locked_class_count"}
                or type(row["label"]) is not str or row["label"] not in {"A", "B", "C"} or row["label"] in labels
                or row["total_credits"] is not None and not credit(row["total_credits"])
                or row["excess_above_minimum"] is not None and not credit(row["excess_above_minimum"])
                or (row["total_credits"] is None) != (row["excess_above_minimum"] is None)
                or row["soft_meeting_count"] is not None and not count(row["soft_meeting_count"], 10000)
                or not count(row["friday_meeting_count"], 10000) or not count(row["locked_class_count"], 12)):
            return False
        if row["total_credits"] is not None and _number(row["excess_above_minimum"]) != max(
                Decimal(0), _add(_number(row["total_credits"]), _number(target["minimum"]).copy_negate())):
            return False
        labels.add(row["label"])
    return True


def _project_course_plan_result(payload):
    """Reject extra fields and unsafe promotion before provider/UI projection."""
    def bad():
        raise ValueError("invalid_course_plan_result")
    if (type(payload) is not dict or set(payload) != {"status", "data_kind", "result"}
            or payload["status"] not in {"available", "not_ready"} or payload["data_kind"] != "course_data_test"):
        bad()
    r = payload["result"]
    template = _base("search")
    if (type(r) is not dict or not set(template) - {"computation_summary"} <= set(r)
            or set(r) - set(template)):
        bad()
    for key in ("schema_version", "mode", "target_term", "reference_mode", "validation_scope", "limitations"):
        if r[key] != template[key]:
            bad()
    if type(r["action"]) is not str or r["action"] not in {"search", "generate", "compare", "revise", "inspect"} or r["reason"] not in _REASONS:
        bad()
    if r["recommendation_kind"] is not None and (type(r["recommendation_kind"]) is not str or r["recommendation_kind"] not in {"courses", "timetable"}):
        bad()
    if r["data_version"] is not None and (type(r["data_version"]) is not str or not _CODE.fullmatch(r["data_version"])):
        bad()
    if r["plan_ref"] is not None and (type(r["plan_ref"]) is not str or not _REFS["plan"].fullmatch(r["plan_ref"])):
        bad()
    if r["revision"] is not None and (type(r["revision"]) is not int or r["revision"] < 0):
        bad()
    if (type(r["source_preparation"]) is not dict or set(r["source_preparation"]) != set(template["source_preparation"])
            or any(type(v) is not str or v not in _STATES for v in r["source_preparation"].values())
            or not _strings(r["locked_class_refs"], limit=12, pattern=_REFS["class"])
            or type(r["fixed_arrangement_count"]) is not int or not 0 <= r["fixed_arrangement_count"] <= 40
            or type(r["user_busy_arrangement_count"]) is not int or not 0 <= r["user_busy_arrangement_count"] <= 20
            or type(r["actions"]) is not list or len(r["actions"]) > 3 or any(v not in {"search", "generate", "compare", "revise", "prepare_sources"} for v in r["actions"])):
        bad()
    safe_text = lambda v: v is None or type(v) is str and len(v) <= 200 and _text(v) == v
    credit = lambda v: v is None or type(v) is str and len(v) <= 32 and _number(v) is not None and _number(v) <= 1200
    if r["policy_status"] is not None:
        policy = r["policy_status"]
        codes = {"multiple_time_scopes_require_review", "clock_period_mapping_unverified", "invalid_period_scope",
            "unsupported_hard_condition", "unsupported_positive_schedule_requirement", "hard_condition_evidence_required",
            "conflicting_credit_bounds", "hard_condition_budget_exceeded", "current_user_change_evidence_required", "non_asserted_hard_condition"}
        if (type(policy) is not dict or set(policy) != {"status", "unresolved", "enforced_hard_preferences", "unhandled_soft_preferences"}
                or policy["status"] not in {"ready", "needs_clarification"} or type(policy["unresolved"]) is not list
                or len(policy["unresolved"]) > 6 or type(policy["enforced_hard_preferences"]) is not list
                or len(policy["enforced_hard_preferences"]) > 6 or any(not safe_text(t) or t is None for t in policy["enforced_hard_preferences"])
                or type(policy["unhandled_soft_preferences"]) is not list or len(policy["unhandled_soft_preferences"]) > 6
                or any(not safe_text(t) or t is None for t in policy["unhandled_soft_preferences"])):
            bad()
        for item in policy["unresolved"]:
            if type(item) is not dict or set(item) != {"text", "code"} or item["code"] not in codes or not safe_text(item["text"]) or item["text"] is None:
                bad()
        if policy["status"] == "needs_clarification" and r["options"]:
            bad()
    if type(r["courses"]) is not list or len(r["courses"]) > 10:
        bad()
    for row in r["courses"]:
        required_course = {"course_code", "course_name", "credits", "observed_section_count", "observation_count"}
        if (type(row) is not dict or not required_course <= set(row) or set(row) - required_course - {"observed_code_links"}
                or type(row["course_code"]) is not str or not _CODE.fullmatch(row["course_code"]) or not safe_text(row["course_name"])
                or not credit(row["credits"]) or any(type(row[k]) is not int or row[k] < 0 for k in ("observed_section_count", "observation_count"))):
            bad()
        if "observed_code_links" in row and not valid_observed_code_links(row["observed_code_links"], row["course_code"]):
            bad()
    if r["search"] is not None and (type(r["search"]) is not dict or set(r["search"]) != {"query", "matching_course_count", "truncated"}
            or not safe_text(r["search"]["query"]) or type(r["search"]["matching_course_count"]) is not int or r["search"]["matching_course_count"] < 0 or type(r["search"]["truncated"]) is not bool):
        bad()
    if r["constraints"] is not None:
        c = r["constraints"]
        if (type(c) is not dict or not {"target_credits", "max_credits", "friday_free", "required_course_codes"} <= set(c)
                or set(c) - {"target_credits", "max_credits", "friday_free", "required_course_codes", "soft_free_slots"}
                or not credit(c["target_credits"]) or c["target_credits"] is None or not credit(c["max_credits"])
                or c["friday_free"] not in {"none", "soft", "hard"} or not _strings(c["required_course_codes"], limit=12, pattern=_CODE)):
            bad()
        if "soft_free_slots" in c and not _valid_soft_scopes(c["soft_free_slots"]):
            bad()
        if "soft_free_slots" in c and c["friday_free"] == "soft" and {"weekday": 5, "periods": None} not in c["soft_free_slots"]:
            bad()
    if r["search_status"] is not None and (type(r["search_status"]) is not dict or set(r["search_status"]) != {"limit_reached", "complete_within_observed_scope", "school_offerings_coverage_complete"}
            or any(type(v) is not bool for v in r["search_status"].values()) or r["search_status"]["school_offerings_coverage_complete"] is not False):
        bad()
    if r["selection_hint"] is not None:
        h = r["selection_hint"]
        if (type(h) is not dict or set(h) != {"plan_ref", "revision", "option_labels", "options", "omitted_option_labels", "locked_classes", "selection_only", "fresh_sources_required"}
                or h["plan_ref"] != r["plan_ref"] or h["revision"] != r["revision"] or h["selection_only"] is not True
                or h["fresh_sources_required"] is not True or r["action"] != "inspect"
                or not _strings(h["option_labels"], limit=3, pattern=re.compile(r"[ABC]\Z"))
                or not _strings(h["omitted_option_labels"], limit=3, pattern=re.compile(r"[ABC]\Z"))
                or type(h["locked_classes"]) is not list or len(h["locked_classes"]) > 12
                or type(h["options"]) is not list or len(h["options"]) > 3):
            bad()
        seen = set()
        for option in h["options"] + [{"label": None, "classes": h["locked_classes"]}]:
            if type(option) is not dict or set(option) != {"label", "classes"} or option["label"] is not None and option["label"] not in h["option_labels"] or type(option["classes"]) is not list or len(option["classes"]) > 12:
                bad()
            for row in option["classes"]:
                if (type(row) is not dict or set(row) != {"class_ref", "course_code", "course_name", "locked"}
                        or type(row["class_ref"]) is not str or not _REFS["class"].fullmatch(row["class_ref"])
                        or type(row["course_code"]) is not str or not _CODE.fullmatch(row["course_code"])
                        or not safe_text(row["course_name"]) or type(row["locked"]) is not bool):
                    bad()
                seen.add(row["class_ref"])
        if len(seen) > 24:
            bad()
    if type(r["options"]) is not list or len(r["options"]) > 3:
        bad()
    if (r["plan_ref"] is None) != (r["revision"] is None):
        bad()
    if r["options"] and (payload["status"] != "available" or r["plan_ref"] is None or r["data_version"] is None
                         or r["constraints"] is None or r["search_status"] is None or r["reason"] is not None):
        bad()
    if r["action"] == "inspect" and (r["options"] or r["courses"] or r["search"] is not None or r["constraints"] is not None):
        bad()
    if r["action"] != "inspect" and r["selection_hint"] is not None:
        bad()
    if r.get("computation_summary") is not None:
        summary = r["computation_summary"]
        if (r["action"] != "inspect" or payload["status"] != "available" or r["selection_hint"] is None
                or r["data_version"] is None or not valid_computation_summary(summary)
                or any(row["label"] not in r["selection_hint"]["option_labels"] for row in summary["options"])):
            bad()
        hint_options = {row["label"]: row for row in r["selection_hint"]["options"]}
        for row in summary["options"]:
            if row["label"] in hint_options and row["locked_class_count"] != sum(
                    item["locked"] for item in hint_options[row["label"]]["classes"]):
                bad()
    labels = set()
    class_definitions = {}
    for option in r["options"]:
        if (type(option) is not dict or not {"label", "total_credits", "observed_slot_result", "full_semantics", "classes", "compared_meeting_pair_count", "friday_meeting_count"} <= set(option)
                or set(option) - {"label", "total_credits", "observed_slot_result", "full_semantics", "classes", "compared_meeting_pair_count", "friday_meeting_count", "soft_preference_evaluation"}
                or option["label"] not in {"A", "B", "C"} or option["label"] in labels or not credit(option["total_credits"])
                or option["observed_slot_result"] not in {"pass", "unknown", "fail"} or option["full_semantics"] != "unverified"
                or any(type(option[k]) is not int or option[k] < 0 for k in ("compared_meeting_pair_count", "friday_meeting_count"))
                or type(option["classes"]) is not list or not 1 <= len(option["classes"]) <= 12):
            bad()
        modern = r["constraints"] is not None and "soft_free_slots" in r["constraints"]
        if ("soft_preference_evaluation" in option) != modern:
            bad()
        if modern:
            evaluation = option["soft_preference_evaluation"]
            if (type(evaluation) is not dict or set(evaluation) != {"status", "method", "violation_count", "scopes"}
                    or evaluation["status"] != "evaluated" or evaluation["method"] != "observed_meeting_overlap_count"
                    or type(evaluation["violation_count"]) is not int or evaluation["violation_count"] < 0
                    or not _valid_soft_scopes(evaluation["scopes"], counts=True)
                    or [{key: row[key] for key in ("weekday", "periods")} for row in evaluation["scopes"]] != r["constraints"]["soft_free_slots"]
                    or sum(row["meeting_count"] for row in evaluation["scopes"]) != evaluation["violation_count"]):
                bad()
            if any(row["weekday"] == 5 and row["periods"] is None and row["meeting_count"] != option["friday_meeting_count"]
                   for row in evaluation["scopes"]):
                bad()
        labels.add(option["label"])
        option_refs = set()
        for row in option["classes"]:
            if (type(row) is not dict or set(row) != {"class_ref", "course_code", "course_name", "credits", "weekday_period_text", "locked"}
                    or type(row["class_ref"]) is not str or not _REFS["class"].fullmatch(row["class_ref"])
                    or type(row["course_code"]) is not str or not _CODE.fullmatch(row["course_code"])
                    or not safe_text(row["course_name"]) or not credit(row["credits"]) or type(row["locked"]) is not bool
                    or type(row["weekday_period_text"]) is not list or not 1 <= len(row["weekday_period_text"]) <= 60
                    or any(type(t) is not str or not re.fullmatch(r"周[一二三四五六日]第[0-9]+(?:、[0-9]+)*节", t) for t in row["weekday_period_text"])):
                bad()
            if row["class_ref"] in option_refs or row["locked"] != (row["class_ref"] in r["locked_class_refs"]):
                bad()
            if row["class_ref"] in class_definitions and class_definitions[row["class_ref"]] != row:
                bad()
            option_refs.add(row["class_ref"])
            class_definitions[row["class_ref"]] = row
        if not set(r["locked_class_refs"]) <= option_refs:
            bad()
        if modern:
            for scope in option["soft_preference_evaluation"]["scopes"]:
                observed = 0
                for row in option["classes"]:
                    for text in row["weekday_period_text"]:
                        match = re.fullmatch(r"周([一二三四五六日])第([0-9]+(?:、[0-9]+)*)节", text)
                        if ("一二三四五六日".index(match[1]) + 1 == scope["weekday"] and
                                (scope["periods"] is None or set(map(int, match[2].split("、"))) & set(scope["periods"]))):
                            observed += 1
                if not observed <= scope["meeting_count"] <= observed + r["fixed_arrangement_count"] + r["user_busy_arrangement_count"]:
                    bad()
    return deepcopy(payload)


def project_course_plan_result(payload):
    """Return the full exact wrapper or one controlled validation error."""
    try:
        checked = _project_course_plan_result(payload)
        json.dumps(checked, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return checked
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        raise ValueError("invalid_course_plan_result") from None
