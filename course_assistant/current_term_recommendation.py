"""Bounded scheduling tests using explicitly authorized current-term observations.

This is a course-data test, not the formal personal recommendation capability.
It never acquires school data, assumes eligibility, or promotes historical seats
to current availability. Candidate identities are local observation identities.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import re

from .local_reference_observations import (
    ObservationCatalog, ReferenceObservationError, SHARED_COMPONENTS, _identifier,
)
from .planner import SearchBudget, _add, _number, fingerprint, revise_constraints, validate_plan
from .course_code_links import observed_code_links


POLICY_VERSION = "current-term-course-data-test/1"
RANKING_VERSION = "current-term-observed-ranking/1"
RANKING_CRITERIA = ["observed_slot_result", "soft_meeting_count", "known_credits",
                    "excess_above_minimum", "course_count", "stable_observation_order"]
LIMITATIONS = ["historical_observations_not_current_offerings", "source_identity_not_cross_source_identity",
               "complete_weeks_and_calendar_unverified", "eligibility_and_prerequisites_unverified",
               "curriculum_applicability_and_credit_recognition_unverified", "current_capacity_unverified",
               "personal_schedule_completeness_not_asserted", "official_enrollment_not_performed"]
_ALIASES = {"target_credits": "min_credits", "required_course_codes": "requested_course_ids",
            "locked_observation_ids": "locked_section_ids", "excluded_observation_ids": "excluded_section_ids",
            "unlock_observation_ids": "unlock_section_ids"}


def _error(code, message):
    raise ReferenceObservationError(code, message)


def _source_term(current_term):
    match = re.fullmatch(r"(20[0-9]{2})-(20[0-9]{2})-AW", current_term) if isinstance(current_term, str) else None
    if not match or int(match[2]) != int(match[1]) + 1:
        _error("current_term_required", "仅接受配置明确的本学年秋冬学期，不能推测或换到其他学期")
    return f"{match[1]}-{match[2]}-1"


def parse_week_annotation(value):
    """Parse only explicit season-local sets; parity never supplies week bounds."""
    if isinstance(value, str) and value in {"单周", "双周"}:
        return {"state": "parity_only", "parity": "odd" if value == "单周" else "even",
                "season_week_numbers": None, "calendar_verified": False}
    if not isinstance(value, str) or not value or len(value) > 200:
        return {"state": "unknown", "season_week_numbers": None, "calendar_verified": False}
    result = {}
    for clause in value.split("；"):
        match = re.fullmatch(r"([秋冬春夏])([0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*)周", clause)
        if not match or match[1] in result:
            return {"state": "unparsed", "season_week_numbers": None, "calendar_verified": False}
        weeks = set()
        for span in match[2].split(","):
            bounds = list(map(int, span.split("-")))
            start, end = bounds[0], bounds[-1]
            if not 1 <= start <= end <= 60:
                return {"state": "unparsed", "season_week_numbers": None, "calendar_verified": False}
            weeks.update(range(start, end + 1))
        result[match[1]] = sorted(weeks)
    return {"state": "explicit_season_local_weeks", "season_week_numbers": result,
            "calendar_verified": False}


def _slot_known(meeting):
    return (isinstance(meeting, dict) and type(meeting.get("weekday")) is int
            and 1 <= meeting["weekday"] <= 7 and isinstance(meeting.get("periods"), list)
            and bool(meeting["periods"]) and all(type(n) is int and 1 <= n <= 60 for n in meeting["periods"]))


def _load_delivery(path, authorize):
    if not callable(authorize):
        _error("authorization_required", "课程数据测试需要当前本人用途授权")
    try:
        approved = authorize()
    except Exception:
        _error("authorization_required", "课程数据测试用途授权不可用")
    if approved is False:
        _error("authorization_required", "课程数据测试需要当前本人用途授权")
    try:
        index = json.loads(Path(path).read_text(encoding="utf-8"))
        manifest_path = Path(index["dataset_manifest"])
        if not manifest_path.is_absolute():
            manifest_path = Path(path).parent / manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (index["dataset_version"] != manifest["dataset_version"]
                or manifest.get("security_realm") != "restricted_reference"
                or manifest.get("shared_projection_contains_personal_state") is not False
                or manifest.get("data_kind") != "derived_real_reference_observations"):
            _error("invalid_delivery", "交付版本或共享数据边界不匹配")
        components = {}
        for name in sorted(SHARED_COMPONENTS):
            entry = manifest["files"][name]
            if entry["path"] != name + ".json":
                _error("shared_components_only", "共享组件文件不能改指个人状态或其他材料")
            child = manifest_path.parent / entry["path"]
            if child.resolve().parent != manifest_path.parent.resolve():
                _error("shared_components_only", "共享组件文件不能离开当前交付目录")
            raw = child.read_bytes()
            if sha256(raw).hexdigest() != entry["sha256"]:
                _error("delivery_hash_mismatch", "共享组件内容已变化，请核对配置版本")
            components[name] = json.loads(raw)
            if not isinstance(components[name], list) or len(components[name]) != entry["records"]:
                _error("delivery_count_mismatch", "共享组件记录数量与交付清单不符")
    except ReferenceObservationError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        _error("delivery_unavailable", "显式配置的课程数据测试交付入口不可用")
    return components, index["dataset_version"]


def _matches_course_query(code, names, links, query):
    # Co-listed codes are exact lookups, never substring or semantic identity.
    text = query.strip().casefold()
    return (any(link["code"].casefold() == text for link in links)
            or all(token in " ".join([code] + [n for n in names if isinstance(n, str)]).casefold()
                   for token in text.split()))


def _course_query_priority(code, links, query):
    text = query.strip().casefold()
    return (0 if code.casefold() == text else 1 if any(link["code"].casefold() == text for link in links) else 2, code)


def search_delivery_courses(path, query, *, current_term, authorize, limit=10):
    """Lexical search of current-term course names/codes, with section coverage.

    This local file read shares the explicit test-purpose authorization; it
    reads no personal states or curriculum and does not infer topic relevance.
    """
    source_term = _source_term(current_term)
    if (not isinstance(query, str) or not query.strip() or len(query) > 100
            or type(limit) is not int or not 1 <= limit <= 10):
        _error("invalid_course_search", "课程检索须为非空名称/代码片段，每次至多10项")
    components, version = _load_delivery(path, authorize)
    sections = {s["section_id"]: s.get("course_code") for s in components["sections"]
                if isinstance(s, dict) and _identifier(s.get("section_id")) and s.get("term") == source_term}
    counts = {}
    for row in components["section_observations"]:
        code = sections.get(row.get("section_id")) if isinstance(row, dict) else None
        if code:
            counts[code] = counts.get(code, 0) + 1
    matching = []
    code_links = {}
    for row in components["courses"]:
        code = row.get("code") if isinstance(row, dict) else None
        if not _identifier(code) or code not in counts:
            continue
        names = [d.get("fields", {}).get("name", {}).get("value", "") for d in row.get("definitions", [])
                 if isinstance(d, dict) and d.get("term") == source_term]
        code_links[code] = observed_code_links(row, source_term)
        if _matches_course_query(code, names, code_links[code], query):
            matching.append(code)
    matching = sorted(set(matching), key=lambda code: _course_query_priority(code, code_links[code], query))
    chosen = matching[:limit]
    if chosen:
        test = CurrentTermCourseDataTest(components, version, current_term=current_term,
                                         authorize=authorize, allowed_course_codes=chosen)
        return test.search_courses(query, limit=limit) | {"matching_course_count": len(matching), "truncated": len(matching) > limit}
    if authorize() is False:
        _error("authorization_required", "课程数据测试用途授权已失效")
    return {"status": "available", "current_term": current_term, "courses": [], "matching_course_count": 0,
            "truncated": False, "dataset_version": version, "coverage": "observed_current_term_course_data_only"}


class CurrentTermCourseDataTest:
    """An authorization-bound, immutable pool; all inputs and revisions are explicit."""

    def __init__(self, components, dataset_version, *, current_term, authorize, allowed_course_codes):
        source_term = _source_term(current_term)
        if (not isinstance(allowed_course_codes, list) or any(not _identifier(c) for c in allowed_course_codes)
                or len(set(allowed_course_codes)) > 24):
            _error("bounded_course_scope_required", "课程数据测试须显式限定至多24个精确课程代码")
        self.current_term, self.source_term = current_term, source_term
        self.catalog = ObservationCatalog(components, dataset_version, authorize, allowed_course_codes, term=self.source_term)
        self.dataset_version = dataset_version
        self._pool = {}
        for code in self.catalog.allowed_course_codes:
            cursor = None
            while True:
                page = self.catalog.lookup(code, limit=100, cursor=cursor)
                for row in page["observations"]:
                    for meeting in row["meetings"]:
                        meeting["week_annotation"] = parse_week_annotation(meeting.get("week_annotation_raw"))
                    self._pool[row["observation_id"]] = row
                cursor = page["pagination"]["next_cursor"]
                if cursor is None:
                    break
        self.version_refs = {"dataset_version": dataset_version, "current_term": current_term,
                             "scope_fingerprint": self.catalog.scope_fingerprint,
                             "observation_fingerprint": fingerprint(self._pool), "policy_version": POLICY_VERSION}

    @classmethod
    def from_delivery(cls, path, *, current_term, authorize, allowed_course_codes):
        """Read only the eight shared files from an explicitly injected index."""
        _source_term(current_term)
        components, version = _load_delivery(path, authorize)
        return cls(components, version, current_term=current_term,
                   authorize=authorize, allowed_course_codes=allowed_course_codes)

    def search_courses(self, query, *, limit=10):
        self.catalog._authorize()
        if (not isinstance(query, str) or not query.strip() or len(query) > 100
                or type(limit) is not int or not 1 <= limit <= 10):
            _error("invalid_course_search", "课程检索须为非空名称/代码片段，每次至多10项")
        results = []
        for code in self.catalog.allowed_course_codes:
            course = self.catalog._course(code)
            rows = [r for r in self._pool.values() if r["course_code"] == code]
            links = observed_code_links(self.catalog._courses.get(code), self.source_term)
            if rows and _matches_course_query(code, [course["course_name"]], links, query):
                results.append({"course_code": code, "course_name": course["course_name"],
                    "nominal_credits": course["credits"], "observation_count": len(rows),
                    "section_count": len({r["section_id"] for r in rows}),
                    "observed_code_links": links,
                    "identity_scope": "source_observed_exact_code_only"})
        results.sort(key=lambda row: _course_query_priority(row["course_code"], row["observed_code_links"], query))
        self.catalog._authorize()
        return {"status": "available", "current_term": self.current_term, "dataset_version": self.dataset_version,
                "courses": results[:limit], "matching_course_count": len(results), "truncated": len(results) > limit,
                "coverage": "explicit_code_pool_only_not_all_school_offerings"}

    def describe(self):
        self.catalog._authorize()
        codes = {row["course_code"] for row in self._pool.values()}
        dates = sorted({row["observed_at"][:10] for row in self._pool.values()
                        if self.catalog._observed_time(row)[0] != float("-inf")})
        return {"status": "available", "mode": "current_term_course_data_test", "current_term": self.current_term,
                "source_term": self.source_term, "version_refs": deepcopy(self.version_refs),
                "authorized_course_count": len(self.catalog.allowed_course_codes), "observed_course_count": len(codes),
                "unobserved_course_count": len(set(self.catalog.allowed_course_codes) - codes),
                "section_count": len({row["section_id"] for row in self._pool.values()}),
                "observation_count": len(self._pool), "observed_dates": dates,
                "known_slot_observation_count": sum(bool(r["meetings"]) and all(_slot_known(m) for m in r["meetings"])
                                                      for r in self._pool.values()),
                "coverage": "explicit_code_pool_only_not_all_school_offerings",
                "formal_recommendation_gate": "real_recommendation_not_ready", "limitations": list(LIMITATIONS)}

    def new_constraints(self, target_credits, *, friday_free="soft", required_course_codes=None,
                        locked_observation_ids=None, excluded_observation_ids=None, fixed_meetings=None, max_credits=None,
                        soft_free_slots=None):
        result = {"revision": 0, "input_version_refs": deepcopy(self.version_refs), "target_credits": target_credits,
                  "max_credits": max_credits, "friday_free": friday_free,
                  "required_course_codes": list(required_course_codes or []),
                  "locked_observation_ids": list(locked_observation_ids or []),
                  "excluded_observation_ids": list(excluded_observation_ids or []),
                  "fixed_meetings": deepcopy(fixed_meetings or []), "soft_free_slots": deepcopy(soft_free_slots or [])}
        return self._constraints(result)

    def _constraints(self, value):
        self.catalog._authorize()
        required = {"revision", "input_version_refs", "target_credits", "max_credits", "friday_free",
                    "required_course_codes", "locked_observation_ids", "excluded_observation_ids", "fixed_meetings"}
        history = {"previous_revision", "previous_constraints_fingerprint", "validation_required",
                   "revision_actions", "revision_warnings"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - history - {"soft_free_slots"}:
            _error("invalid_test_constraints", "课程数据测试条件必须使用完整的已声明字段")
        result = deepcopy(value)
        if value["input_version_refs"] != self.version_refs:
            _error("stale_test_constraints", "课程数据、范围或策略已变化，不能沿用旧条件")
        if type(value["revision"]) is not int or value["revision"] < 0:
            _error("invalid_test_constraints", "条件版本必须为非负整数")
        target, maximum = _number(value["target_credits"]), _number(value["max_credits"])
        if target is None or target <= 0 or (value["max_credits"] is not None and (maximum is None or maximum < target)):
            _error("invalid_test_constraints", "测试目标须为正学分，最高学分不得小于目标")
        result["target_credits"] = format(target, "f")
        result["max_credits"] = format(maximum, "f") if maximum is not None else None
        if not isinstance(value["friday_free"], str) or value["friday_free"] not in {"off", "soft", "hard"}:
            _error("invalid_test_constraints", "周五条件须明确为off、soft或hard")
        soft = value.get("soft_free_slots", [])
        if (type(soft) is not list or len(soft) > 7 or any(type(row) is not dict
                or set(row) != {"weekday", "periods"} or type(row["weekday"]) is not int or not 1 <= row["weekday"] <= 7
                or row["periods"] is not None and (type(row["periods"]) is not list or not row["periods"]
                    or len(row["periods"]) > 60 or any(type(n) is not int or not 1 <= n <= 60 for n in row["periods"])
                    or row["periods"] != sorted(set(row["periods"]))) for row in soft)
                or len({row["weekday"] for row in soft}) != len(soft)):
            _error("invalid_test_constraints", "软空闲条件须为至多七个不重复星期的明确全天或节次范围")
        result["soft_free_slots"] = sorted(deepcopy(soft), key=lambda row: row["weekday"])
        for key, allowed in (("required_course_codes", self.catalog.allowed_course_codes),
                             ("locked_observation_ids", self._pool), ("excluded_observation_ids", self._pool)):
            values = value[key]
            if not isinstance(values, list) or any(not _identifier(v) or v not in allowed for v in values):
                _error("outside_test_scope", "必选、锁定或排除项超出当前课程数据范围")
            result[key] = sorted(set(values))
        if not isinstance(value["fixed_meetings"], list) or len(value["fixed_meetings"]) > 40:
            _error("invalid_fixed_meetings", "固定安排须为至多40个显式时段")
        seen = set()
        for meeting in result["fixed_meetings"]:
            if (not isinstance(meeting, dict) or set(meeting) != {"fixed_id", "weekday", "periods", "source_refs"}
                    or not _identifier(meeting["fixed_id"]) or meeting["fixed_id"] in seen
                    or not isinstance(meeting["source_refs"], list) or not meeting["source_refs"]
                    or any(not _identifier(v) for v in meeting["source_refs"])
                    or (meeting["weekday"] is not None and (type(meeting["weekday"]) is not int or not 1 <= meeting["weekday"] <= 7))
                    or (meeting["periods"] is not None and (not isinstance(meeting["periods"], list)
                        or not meeting["periods"] or any(type(n) is not int or not 1 <= n <= 60 for n in meeting["periods"])))):
                _error("invalid_fixed_meetings", "固定安排须保留明确标识、来源与已知/未知星期节次")
            seen.add(meeting["fixed_id"])
        return result

    def _option(self, ids, constraints):
        rows = [deepcopy(self._pool[oid]) for oid in ids]
        checks, slots = [], []
        def check(code, result, refs, **extra):
            checks.append({"code": code, "result": result, "observation_ids": refs, **extra})
        if len(ids) != len(set(ids)):
            check("duplicate_observation", "fail", ids)
        codes = [r["course_code"] for r in rows]
        if len(codes) != len(set(codes)):
            check("same_course_multiple_observations", "fail", ids)
        for oid in constraints["locked_observation_ids"]:
            check("locked_observation", "pass" if oid in ids else "fail", [oid])
        for code in constraints["required_course_codes"]:
            check("required_course", "pass" if code in codes else "fail", [], course_code=code)
        total, known = Decimal(0), True
        records = []
        for row in rows:
            oid = row["observation_id"]
            check("excluded_observation", "fail" if oid in constraints["excluded_observation_ids"] else "pass", [oid])
            credit = _number(row["credits"])
            known &= credit is not None
            if credit is not None:
                total = _add(total, credit)
            complete = bool(row["meetings"]) and all(_slot_known(m) for m in row["meetings"])
            check("observed_weekday_period_complete", "pass" if complete else "unknown", [oid])
            for meeting in row["meetings"]:
                slots.append((oid, meeting, [meeting["meeting_id"]]))
            seats = row["capacity"]["remaining"]
            check("historical_capacity", "fail" if seats is not None and seats <= 0 else "unknown", [oid],
                  observed_remaining=seats, capacity_id=row["capacity"]["capacity_id"], current_availability_verified=False)
            exam = row["exam"]
            records.append({"section_id": oid, "course_id": row["course_code"], "academic_year": self.current_term[:9],
                "term_code": self.current_term, "credits": row["credits"], "available_seats": seats,
                "source_refs": [oid, row["meeting_set_id"], row["capacity"]["capacity_id"]],
                "meetings": [{"meeting_id": m["meeting_id"], "day_of_week": m["weekday"], "period_indices": m["periods"],
                              "week_numbers": m["week_set"], "academic_calendar_id": m["calendar_version"],
                              "season_codes": None, "schedule_state": "partial_reference", "source_refs": [m["meeting_id"]]}
                             for m in row["meetings"]],
                "exams": [{"exam_id": exam["exam_id"], "exam_date": exam["date"], "start_time_local": exam["start"],
                           "end_time_local": exam["end"], "timezone": exam["timezone"], "source_refs": [exam["exam_id"]]}],
                "exam_state": "known"})
        for fixed in constraints["fixed_meetings"]:
            check("fixed_weekday_period_complete", "pass" if _slot_known(fixed) else "unknown", [], fixed_id=fixed["fixed_id"])
            slots.append(("fixed:" + fixed["fixed_id"], fixed, fixed["source_refs"]))
        compared_pairs = 0
        for (left, lm, lr), (right, rm, rr) in combinations(slots, 2):
            if left == right or not _slot_known(lm) or not _slot_known(rm):
                continue
            compared_pairs += 1
            periods = sorted(set(lm["periods"]) & set(rm["periods"]))
            if lm["weekday"] == rm["weekday"] and periods:
                check("possible_weekday_period_overlap", "fail", [v for v in (left, right) if not v.startswith("fixed:")],
                      parties=[left, right], meeting_refs=lr + rr, weekday=lm["weekday"], periods=periods,
                      policy="conservative_possible_week_overlap_rejected", actual_date_conflict_verified=False)
        friday = sum(_slot_known(m) and m["weekday"] == 5 for _, m, _ in slots)
        if constraints["friday_free"] == "hard" and friday:
            check("hard_friday", "fail", ids)
        target, maximum = _number(constraints["target_credits"]), _number(constraints["max_credits"])
        check("observed_credit_target", "unknown" if not known else "pass" if total >= target and (maximum is None or total <= maximum) else "fail", ids)
        snapshot = {"snapshot_id": "course-data-test-" + fingerprint(self.version_refs), "version": self.dataset_version,
                    "data_kind": "derived_real_reference_observations", "academic_year": self.current_term[:9],
                    "term_code": self.current_term, "freshness_state": "stale", "completeness": "partial",
                    "records": records, "source_refs": sorted(ids), "academic_calendar": None}
        engine_constraints = {"revision": constraints["revision"], "input_version_refs": self.version_refs,
                              "requested_course_ids": constraints["required_course_codes"], "locked_section_ids": constraints["locked_observation_ids"],
                              "excluded_section_ids": constraints["excluded_observation_ids"], "friday_free": constraints["friday_free"],
                              "min_credits": constraints["target_credits"], "max_credits": constraints["max_credits"],
                              "fixed_meetings": [{"day_of_week": m["weekday"], "period_indices": m["periods"],
                                "schedule_state": "partial_reference", "source_refs": m["source_refs"]} for m in constraints["fixed_meetings"]]}
        validation = validate_plan(ids, snapshot, {"target_term": self.current_term}, engine_constraints)
        # Historical capacity unknowns stay in full validation; only explicit
        # nonpositive observations reject this bounded course-data scenario.
        projection_checks = [c for c in checks if c["code"] != "historical_capacity" or c["result"] == "fail"]
        projected = "fail" if any(c["result"] == "fail" for c in projection_checks) or validation["result"] == "fail" else (
                    "unknown" if any(c["result"] == "unknown" for c in projection_checks) else "pass")
        soft_scopes = deepcopy(constraints["soft_free_slots"])
        if constraints["friday_free"] == "soft":
            soft_scopes = [row for row in soft_scopes if row["weekday"] != 5] + [{"weekday": 5, "periods": None}]
        soft_scopes.sort(key=lambda row: row["weekday"])
        soft_counts, hit_meetings = [], set()
        for scope in soft_scopes:
            hits = {index for index, (_, meeting, _) in enumerate(slots) if _slot_known(meeting)
                    and meeting["weekday"] == scope["weekday"]
                    and (scope["periods"] is None or set(meeting["periods"]) & set(scope["periods"]))}
            hit_meetings.update(hits)
            soft_counts.append({**scope, "meeting_count": len(hits)})
        soft_evaluation = {"status": "evaluated", "method": "observed_meeting_overlap_count",
                           "violation_count": len(hit_meetings), "scopes": soft_counts}
        return {"option_id": fingerprint([self.version_refs, constraints, ids]), "observation_ids": list(ids),
                "course_codes": codes, "total_credits": format(total, "f") if known else None,
                "known_credit_lower_bound": format(total, "f"), "sections": rows,
                "observed_slot_validation": {"result": projected, "checks": checks,
                    "scope": "observed_weekday_periods_conservative_overlap_policy", "compared_meeting_pair_count": compared_pairs,
                    "actual_date_conflict_verified": False},
                "full_validation": validation, "friday_meeting_count": friday, "soft_preference_evaluation": soft_evaluation,
                "limitations": list(LIMITATIONS), "formal_recommendation_gate": "real_recommendation_not_ready",
                "status": "course_data_test_option" if projected == "pass" else "needs_information" if projected == "unknown" else "rejected_test_option",
                "provenance": {"version_refs": deepcopy(self.version_refs), "constraints_fingerprint": fingerprint(constraints),
                    "identity_scope": "indivisible_source_observation_only", "official_enrollment": False}}

    def validate(self, observation_ids, constraints):
        constraints = self._constraints(constraints)
        if (not isinstance(observation_ids, list) or not observation_ids or len(observation_ids) > 24
                or any(not isinstance(oid, str) or oid not in self._pool for oid in observation_ids)):
            _error("outside_test_scope", "方案须引用当前范围内至多24个原观察标识")
        result = self._option(observation_ids, constraints)
        self.catalog._authorize()
        return result

    def recommend(self, constraints, *, limit=3, node_limit=10000):
        constraints = self._constraints(constraints)
        if type(limit) is not int or not 1 <= limit <= 5:
            _error("invalid_result_limit", "测试方案数须为1至5")
        budget = SearchBudget(node_limit, max_candidates=64, max_diagnostics=40, max_seconds=10)
        diagnostics, grouped = [], {}
        def diagnose(code, oid=None):
            if len(diagnostics) < budget.max_diagnostics:
                diagnostics.append({"code": code, "observation_id": oid})
            else:
                budget.diagnostics_clipped = True
        for row in self._pool.values():
            if not budget.spend("observation_precheck"):
                break
            grouped.setdefault(row["section_id"], []).append(row)
        choices = {}
        locked = set(constraints["locked_observation_ids"])
        locked_codes = {self._pool[oid]["course_code"] for oid in locked}
        for rows in grouped.values():
            demanded = [r for r in rows if r["observation_id"] in locked]
            times = [self.catalog._observed_time(r)[0] for r in rows]
            if demanded:
                selected = demanded
            elif float("-inf") in times:
                # Unknown order cannot justify replacing one observation with
                # another. Such a section needs explicit source selection.
                diagnose("observation_order_unknown_requires_explicit_lock")
                continue
            else:
                latest = max(times)
                selected = [r for r, timestamp in zip(rows, times) if timestamp == latest]
                if len(selected) != 1:
                    diagnose("simultaneous_observations_require_explicit_lock")
                    continue
            for row in selected:
                oid = row["observation_id"]
                if row["course_code"] in locked_codes and oid not in locked:
                    continue
                if (oid in constraints["excluded_observation_ids"] or not row["meetings"]
                        or not all(_slot_known(m) for m in row["meetings"])):
                    diagnose("excluded_or_unknown_weekday_period", oid)
                    continue
                seats = row["capacity"]["remaining"]
                if seats is not None and seats <= 0:
                    diagnose("historical_nonpositive_capacity", oid)
                    continue
                if constraints["friday_free"] == "hard" and any(m["weekday"] == 5 for m in row["meetings"]):
                    diagnose("hard_friday", oid)
                    continue
                choices.setdefault(row["course_code"], []).append(oid)
        required = set(constraints["required_course_codes"]) | locked_codes
        codes = sorted(choices, key=lambda code: (code not in required, code))
        retained, rejected = [], 0
        def rank(option):
            return (option["observed_slot_validation"]["result"] != "pass",
                    option["soft_preference_evaluation"]["violation_count"],
                    _number(option["total_credits"]) is None,
                    max(Decimal(0), _add(_number(option["total_credits"]) or Decimal(0), _number(constraints["target_credits"]).copy_negate())),
                    len(option["course_codes"]), tuple(option["observation_ids"]))
        def visit(position, ids):
            nonlocal rejected
            if not budget.spend("course_section_subset_search"):
                return
            if budget.nodes_visited % 128 == 0:
                self.catalog._authorize()
            if position == len(codes):
                if not ids:
                    return
                option = self._option(ids, constraints)
                if option["observed_slot_validation"]["result"] == "fail":
                    rejected += 1
                    for item in option["observed_slot_validation"]["checks"]:
                        if item["result"] == "fail":
                            diagnose(item["code"])
                    for item in option["full_validation"]["checks"]:
                        if item["result"] == "fail":
                            diagnose(item["code"])
                    return
                retained.append(option)
                if len(retained) > budget.max_candidates:
                    retained.sort(key=rank)
                    del retained[budget.max_candidates:]
                    budget.candidate_retention_clipped = True
                return
            if codes[position] not in required:
                visit(position + 1, ids)
            for oid in sorted(choices[codes[position]]):
                if budget.limit_reached:
                    break
                visit(position + 1, ids + [oid])
        if required - set(choices):
            diagnose("required_or_locked_course_has_no_usable_observation")
        elif not budget.limit_reached:
            visit(0, [])
        retained.sort(key=rank)
        options = retained[:limit]
        self.catalog._authorize()
        return {"status": "course_data_test_generated" if options else "no_test_option_within_checked_scope",
                "mode": "current_term_course_data_test", "current_term": self.current_term,
                "options": options, "constraints": constraints, "version_refs": deepcopy(self.version_refs),
                "ranking": {"version": RANKING_VERSION, "criteria": list(RANKING_CRITERIA)},
                "search": {**budget.report(), "candidate_course_count": len(choices), "rejected_leaf_count": rejected,
                           "complete_within_observed_scope": not budget.limit_reached,
                           "school_offerings_coverage_complete": False, "globally_optimal": False},
                "diagnostics": diagnostics, "limitations": list(LIMITATIONS),
                "formal_recommendation_gate": "real_recommendation_not_ready", "can_save_or_export": False,
                "note": "按已有星期/节次观察验证；完整周次、校历、当前开课资格未验证。仅限本学期课程数据测试。"}

    def compare(self, observation_id_sets, constraints):
        if not isinstance(observation_id_sets, list) or not 1 <= len(observation_id_sets) <= 5:
            _error("invalid_compare", "每次比较须为1至5套当前观察标识列表")
        return {"status": "course_data_test_comparison", "options": [self.validate(ids, constraints) for ids in observation_id_sets],
                "version_refs": deepcopy(self.version_refs), "formal_recommendation_gate": "real_recommendation_not_ready"}

    def revise(self, constraints, patch, *, limit=3, node_limit=10000):
        old = self._constraints(constraints)
        allowed = {"target_credits", "max_credits", "friday_free", "required_course_codes", "locked_observation_ids",
                   "excluded_observation_ids", "fixed_meetings", "unlock_observation_ids", "expected_revision", "soft_free_slots"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            _error("invalid_revision", "仅可修改声明的测试偏好；不能替换数据或学期")
        if "unlock_observation_ids" in patch and (not isinstance(patch["unlock_observation_ids"], list)
                or any(not isinstance(oid, str) or oid not in self._pool for oid in patch["unlock_observation_ids"])):
            _error("outside_test_scope", "解锁项须来自当前课程数据范围")
        internal = {_ALIASES.get(k, k): deepcopy(v) for k, v in old.items()}
        updated = revise_constraints(internal, {_ALIASES.get(k, k): deepcopy(v) for k, v in patch.items()})
        reverse = {v: k for k, v in _ALIASES.items()}
        updated = {reverse.get(k, k): v for k, v in updated.items()}
        # Generic planner adds these protected fields even when absent.
        updated.pop("preloaded_section_ids", None)
        result = self._constraints(updated)
        return self.recommend(result, limit=limit, node_limit=node_limit)
