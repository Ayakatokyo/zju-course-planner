"""Pure, conservative mappings for stripped personal business observations.

The caller validates the task grant, source response and business-field
whitelist before calling this module. No function reads a file, browser,
credential or network. Results are reference observations, never authenticated
student bindings, a verified academic ledger, or enrollment decisions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, time
from decimal import Decimal, InvalidOperation
import re


PARSER_VERSION = "student-source-parser-v2"
MAPPING_VERSION = "student-source-mapping-v2"
FIELD_STATES = frozenset({"known", "unknown", "not_provided", "not_applicable",
                          "pending_verification", "conflicting"})

PROFILE_FIELDS = {
    "source_subject_id": ("source_subject_id", "student_number", "xh", "xhm", "学号"),
    "name": ("name", "xm", "姓名"),
    "university": ("university", "university_name", "学校"),
    "college_name": ("college_name", "xymc", "college", "学院"),
    "major_name": ("major_name", "zymc", "major", "专业"),
    "major_code": ("major_code", "zydm", "专业代码"),
    "cohort": ("cohort", "entry_year", "enrollment_year", "rxnf", "入学年", "入学年份"),
    "admission_date_raw": ("admission_date_raw", "admission_date", "入学日期"),
    "current_grade_raw": ("current_grade_raw", "current_grade", "当前所在级"),
    "major_or_category_raw": ("major_or_category_raw", "类（专业）"),
    "program": ("program", "program_name", "项目", "培养项目"),
    "study_path_raw": ("study_path_raw", "study_path", "path", "修读路径", "培养路径"),
    "study_scope": ("study_scope", "修读范围"),
    "training_type": ("training_type", "培养类型"),
    "specialization_raw": ("specialization_raw", "specialization", "direction", "方向", "专业方向"),
}
ACADEMIC_FIELDS = {
    "source_selection_id_raw": ("xkkh", "source_selection_id_raw", "选课课号"),
    "course_code_raw": ("kcdm", "kch", "course_code_raw", "course_code", "课程代码", "课程号"),
    "course_name_raw": ("kcmc", "course_name_raw", "course_name", "课程名称"),
    "academic_year_raw": ("xnm", "academic_year_raw", "academic_year", "学年"),
    "term_raw": ("xqm", "term_raw", "term", "学期"),
    "nominal_credits": ("xf", "nominal_credits", "credits_in_source", "学分", "额定学分"),
    "grade_raw": ("cj", "grade_raw", "grade", "成绩"),
    "grade_points_raw": ("jd", "grade_points_raw", "grade_points", "绩点"),
    "completion_status_raw": ("completion_status_raw", "completion_status", "row_status", "sftg", "是否通过", "修读状态"),
    "source_attempt_id": ("source_attempt_id", "attempt_source_id", "修读实例号"),
    "recognition_status_raw": ("recognition_status_raw", "recognition_status", "认定状态"),
    "substitution_raw": ("substitution_raw", "substitution", "replacement", "替代课程", "替换关系"),
    "major_status_raw": ("major_status_raw", "is_major_raw", "major_status", "主修标记"),
    "record_kind": ("record_kind", "record_type", "记录类型"),
    "reported_earned_credits": ("earned_credits", "reported_earned_credits", "已获学分"),
}
TIMETABLE_FIELDS = {
    "source_selection_id_raw": ("xkkh", "source_selection_id_raw", "选课课号"),
    "source_section_id": ("jxb_id", "jxbh", "source_section_id", "教学班号"),
    "course_code_raw": ("kcdm", "kch", "course_code_raw", "course_code", "课程代码", "课程号"),
    "course_name_raw": ("kcmc", "course_name_raw", "course_name", "课程名称"),
    "academic_year_raw": ("xnm", "academic_year_raw", "academic_year", "学年"),
    "term_raw": ("xqm", "term_raw", "term", "学期"),
    "section_raw": ("kcb", "section_raw", "teaching_section_raw", "教学班原段"),
    "sfqd_raw": ("sfqd", "sfqd_raw", "是否确定"),
    "dsz_raw": ("dsz", "dsz_raw", "单双周"),
    "xxq_raw": ("xxq", "xxq_raw", "分学期"),
    "instructor_raw": ("xm", "jsxm", "instructor_raw", "teacher", "教师"),
    "time_raw": ("sksj", "time_raw", "上课时间"),
    "place_raw": ("cdmc", "place_raw", "地点", "上课地点"),
}
MEETING_FIELDS = {
    "time_raw": ("sksj", "time_raw", "上课时间"),
    "place_raw": ("cdmc", "place_raw", "地点", "上课地点"),
    "weekday": ("weekday", "xqj", "星期"),
    "periods": ("periods", "jcs", "节次"),
    "week_set": ("week_set", "weeks", "周集合"),
    "week_annotation_raw": ("zcd", "week_annotation_raw", "周次"),
    "sfqd_raw": ("sfqd", "sfqd_raw", "是否确定"),
    "dsz_raw": ("dsz", "dsz_raw", "单双周"),
    "xxq_raw": ("xxq", "xxq_raw", "分学期"),
    "start_period_raw": ("djj", "start_period_raw", "起始节"),
    "duration_raw": ("skcd", "duration_raw", "节数"),
}
EXAM_FIELDS = {
    "source_exam_id": ("source_exam_id", "ks_id", "考试号"),
    "source_selection_id_raw": ("xkkh", "source_selection_id_raw", "选课课号"),
    "source_section_id": ("jxb_id", "jxbh", "source_section_id", "教学班号"),
    "course_code_raw": ("kcdm", "kch", "course_code_raw", "course_code", "课程代码", "课程号"),
    "course_name_raw": ("kcmc", "course_name_raw", "course_name", "课程名称"),
    "academic_year_raw": ("xnm", "academic_year_raw", "academic_year", "学年"),
    "term_raw": ("xqm", "term_raw", "term", "学期"),
    "exam_time_raw": ("kssj", "exam_time_raw", "考试时间"),
    "final_exam_time_raw": ("final_exam_time_raw", "期末考试时间"),
    "midterm_exam_time_raw": ("midterm_exam_time_raw", "期中考试时间"),
    "final_exam_place_raw": ("final_exam_place_raw", "期末考试地点"),
    "midterm_exam_place_raw": ("midterm_exam_place_raw", "期中考试地点"),
    "exam_date": ("exam_date", "ksrq", "考试日期"),
    "start_time": ("start_time", "kssk", "开始时间"),
    "end_time": ("end_time", "ksjs", "结束时间"),
    "timezone": ("timezone", "时区"),
    "place_raw": ("ksdd", "cdmc", "place_raw", "考试地点"),
    "exam_status_raw": ("exam_status_raw", "exam_status", "考试状态"),
}
_CONTAINERS = {"items", "records", "kbList", "total", "totalCount", "totalResult",
               "pageNo", "pageSize", "currentPage", "showCount"}
_WRAPPER_FIELDS = {"value", "raw_value", "state"}
BUSINESS_FIELDS = {
    "profile": frozenset({a for aliases in PROFILE_FIELDS.values() for a in aliases} | _WRAPPER_FIELDS),
    "academic": frozenset({a for aliases in ACADEMIC_FIELDS.values() for a in aliases} | _CONTAINERS | _WRAPPER_FIELDS),
    "timetable": frozenset({a for aliases in (TIMETABLE_FIELDS | MEETING_FIELDS).values() for a in aliases}
                            | _CONTAINERS | _WRAPPER_FIELDS | {"meetings", "meeting_segments", "上课时段"}),
    "exams": frozenset({a for aliases in EXAM_FIELDS.values() for a in aliases} | _CONTAINERS | _WRAPPER_FIELDS),
}
_MISSING = object()
_UNKNOWN_TEXT = {"", "--", "—", "unknown", "待定", "未知", "未提供"}
_SCOPE_FIELDS = {"academic_year", "academic_year_raw", "term", "term_raw", "target_term",
                 "query_kind", "source_alias", "page_index", "coverage"}


def _unknown(value):
    return value is None or (isinstance(value, str) and value.strip() in _UNKNOWN_TEXT)


def _issue(code, row_index=None, field=None):
    result = {"code": code}
    if row_index is not None:
        result["source_row_index"] = row_index
    if field is not None:
        result["field_path"] = field
    return result


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid_decimal")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_decimal") from None
    if (not result.is_finite() or result < 0 or len(result.as_tuple().digits) > 1000
            or abs(result.as_tuple().exponent) > 1000):
        raise ValueError("invalid_decimal")
    return str(result)


def _text(value):
    if not isinstance(value, str):
        raise ValueError("invalid_text")
    return value.strip()


def _raw_value(value):
    return deepcopy(value)


def _cohort(value):
    # Only an explicit entry-year field is considered. An account ID is never
    # passed to this function, and school grade/term codes are not entry years.
    if type(value) is int and 1900 <= value <= 2200:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:19|20|21)\d{2}(?:年|级)?", value.strip()):
        return int(value.strip()[:4])
    raise ValueError("invalid_entry_year")


def _int_value(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        return int(value.strip())
    raise ValueError("invalid_integer")


def _weekday(value):
    result = _int_value(value)
    if not 1 <= result <= 7:
        raise ValueError("invalid_weekday")
    return result


def _positive_set(value):
    # No range/default expansion from school dsz/xxq/djj/skcd codes.
    if not isinstance(value, list):
        raise ValueError("invalid_integer_set")
    parsed = [_int_value(item) for item in value]
    if any(item < 1 for item in parsed) or len(set(parsed)) != len(parsed):
        raise ValueError("invalid_integer_set")
    return parsed


def _iso_date(value):
    result = _text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", result):
        raise ValueError("invalid_date")
    try:
        date.fromisoformat(result)
    except ValueError:
        raise ValueError("invalid_date") from None
    return result


def _local_time(value):
    result = _text(value)
    if not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", result):
        raise ValueError("invalid_time")
    try:
        time.fromisoformat(result)
    except ValueError:
        raise ValueError("invalid_time") from None
    return result


def _field(row, aliases, field, issues, index, converter=_text):
    assertions = []
    for alias in aliases:
        if alias not in row:
            continue
        raw = row[alias]
        state = None
        value = raw
        if isinstance(raw, dict) and set(raw) <= _WRAPPER_FIELDS and "state" in raw:
            state = raw["state"]
            value = raw.get("value", raw.get("raw_value"))
        if state is not None and (not isinstance(state, str) or state not in FIELD_STATES):
            issues.append(_issue("invalid_field_state", index, field))
            state, normalized = "unknown", None
        elif state is not None and state != "known":
            normalized = None
        elif _unknown(value):
            state, normalized = "unknown", None
        else:
            try:
                normalized = converter(value)
                state = state or "known"
            except (ValueError, TypeError) as error:
                # Converters only emit bounded constant codes; never stringify
                # an exception carrying personal source values.
                code = error.args[0] if error.args and error.args[0] in {
                    "invalid_decimal", "invalid_text", "invalid_entry_year", "invalid_integer",
                    "invalid_weekday", "invalid_integer_set", "invalid_date", "invalid_time"} else "invalid_field"
                issues.append(_issue(code, index, field))
                state, normalized = "unknown", None
        assertions.append({"source_field": alias, "raw_value": deepcopy(raw),
                           "value": normalized, "state": state})
    if not assertions:
        return None, "unknown", []
    known = [item["value"] for item in assertions if item["state"] == "known"]
    states = {item["state"] for item in assertions}
    if "conflicting" in states or (known and any(item != known[0] for item in known)) or len(states) > 1:
        issues.append(_issue("conflicting_aliases", index, field))
        return None, "conflicting", assertions
    return assertions[0]["value"], assertions[0]["state"], assertions


def _mapped(row, mapping, index, converters=None):
    result = {"field_states": {}, "field_assertions": {}, "issues": []}
    for field, aliases in mapping.items():
        value, state, assertions = _field(row, aliases, field, result["issues"], index,
                                          (converters or {}).get(field, _raw_value if field.endswith("_raw") else _text))
        result[field] = value
        result["field_states"][field] = state
        if assertions:
            result["field_assertions"][field] = assertions
    return result


def _profile(row, index):
    result = _mapped(row, PROFILE_FIELDS, index, {"cohort": _cohort})
    result["identity_binding_status"] = "unverified_source_observation"
    result["plan_binding_status"] = "unknown"
    admission_raw = result["admission_date_raw"]
    if result["field_states"]["admission_date_raw"] != "known":
        return result
    try:
        # The source explicitly labels this as admission date. Require the
        # complete ASCII representation; never use account/current-grade digits.
        if not isinstance(admission_raw, str) or not re.fullmatch(r"[0-9]{8}", admission_raw):
            raise ValueError
        admission = date(int(admission_raw[:4]), int(admission_raw[4:6]), int(admission_raw[6:8]))
    except ValueError:
        result["field_states"]["admission_date_raw"] = "unknown"
        for assertion in result["field_assertions"].get("admission_date_raw", []):
            assertion["state"], assertion["value"] = "unknown", None
        result["issues"].append(_issue("invalid_admission_date", index, "admission_date_raw"))
        return result
    derivation = {"rule": "year_from_complete_admission_date", "source_field_path": "admission_date_raw",
                  "source_fields": [item["source_field"] for item in result["field_assertions"]["admission_date_raw"]],
                  "source_row_index": index, "derived_value": admission.year}
    result["field_derivations"] = {"cohort": derivation}
    assertions = result["field_assertions"].setdefault("cohort", [])
    for source in result["field_assertions"]["admission_date_raw"]:
        assertions.append({"source_field": source["source_field"], "raw_value": deepcopy(source["raw_value"]),
                           "value": admission.year, "state": "known",
                           "derivation": "year_from_complete_admission_date", "source_field_path": "admission_date_raw"})
    if result["field_states"]["cohort"] == "known" and result["cohort"] != admission.year:
        result["cohort"] = None
        result["field_states"]["cohort"] = "conflicting"
        result["issues"].append(_issue("cohort_admission_date_conflict", index, "cohort"))
    elif result["field_states"]["cohort"] in {"unknown", "not_provided"}:
        result["cohort"] = admission.year
        result["field_states"]["cohort"] = "known"
    return result


def _scope_issues(record, scope, index):
    # Scope is a query restriction, not evidence that every returned row belongs
    # to that term. Only identically named explicit raw scope values compare.
    for field in ("academic_year_raw", "term_raw"):
        source = record.get(field)
        expected = scope.get(field, _MISSING)
        if expected is _MISSING:
            expected = scope.get(field.removesuffix("_raw"), _MISSING)
        if expected is not _MISSING and source is not None and str(source) != str(expected):
            record["issues"].append(_issue("row_outside_declared_scope", index, field))


def _academic(row, index):
    result = _mapped(row, ACADEMIC_FIELDS, index,
                     {"nominal_credits": _decimal, "reported_earned_credits": _decimal})
    raw_status = result["completion_status_raw"]
    statuses = {"passed": "passed", "通过": "passed", "failed": "failed", "未通过": "failed",
                "不通过": "failed", "in_progress": "in_progress", "在修": "in_progress",
                "replaced": "replaced", "被替代": "replaced", "pending_recognition": "pending_recognition",
                "待认定": "pending_recognition"}
    result["completion_status"] = statuses.get(raw_status, "unknown") if isinstance(raw_status, str) else "unknown"
    result["field_states"]["completion_status"] = "known" if isinstance(raw_status, str) and raw_status in statuses else "unknown"
    # A source report is kept separately. Even passed+xf cannot establish a
    # unique earned-credit fact without attempt/repeat/recognition review.
    result["earned_credits"] = None
    result["earned_credits_state"] = "unknown"
    result["field_states"]["earned_credits"] = "unknown"
    result["attempt_candidate_id"] = f"academic-row-{index}"
    result["attempt_resolution_status"] = "provisional"
    result["course_identity_status"] = "unresolved"
    grade = result["grade_raw"]
    result["grade_numeric"] = None
    result["field_states"]["grade_numeric"] = "unknown"
    if type(grade) in (int, float) or (isinstance(grade, str) and re.fullmatch(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)", grade)):
        try:
            result["grade_numeric"] = _decimal(grade)
            result["field_states"]["grade_numeric"] = "known"
        except ValueError:
            result["issues"].append(_issue("invalid_decimal", index, "grade_numeric"))
    return result


def _segments(value):
    # Semicolons inside week annotations do not create an extra meeting.
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    result, part, depth = [], [], 0
    for char in value:
        if char in "{（(【[":
            depth += 1
        if char in "}）)】]" and depth:
            depth -= 1
        if char in "\n;；" and not depth:
            if "".join(part).strip():
                result.append("".join(part).strip())
            part = []
        else:
            part.append(char)
    if "".join(part).strip():
        result.append("".join(part).strip())
    return result


def _timetable(row, index):
    result = _mapped(row, TIMETABLE_FIELDS, index)
    segments = [row[key] for key in ("meetings", "meeting_segments", "上课时段") if key in row]
    meeting_rows = segments[0] if segments else None
    if len(segments) > 1 and any(value != meeting_rows for value in segments):
        result["issues"].append(_issue("conflicting_meeting_containers", index, "meetings"))
    if meeting_rows is None:
        raw_time = result["time_raw"]
        if raw_time is not None and isinstance(raw_time, str):
            times = _segments(raw_time)
            places = _segments(result["place_raw"]) if isinstance(result["place_raw"], str) else []
            meeting_rows = [{"time_raw": item, "place_raw": places[pos] if len(places) == len(times) else None}
                            for pos, item in enumerate(times)]
            if places and len(places) != len(times):
                result["issues"].append(_issue("meeting_place_alignment_unknown", index, "meetings"))
        elif raw_time is not None:
            result["issues"].append(_issue("invalid_meeting_time_text", index, "time_raw"))
        elif any(alias in row for aliases in MEETING_FIELDS.values() for alias in aliases):
            meeting_rows = [{key: value for key, value in row.items()
                             if key in {alias for aliases in MEETING_FIELDS.values() for alias in aliases}}]
        else:
            meeting_rows = None
    result["meetings"] = None
    result["field_states"]["meetings"] = "unknown"
    if meeting_rows is not None:
        if not isinstance(meeting_rows, list):
            result["issues"].append(_issue("invalid_meeting_container", index, "meetings"))
        else:
            result["meetings"] = []
            result["field_states"]["meetings"] = "known"
            for segment_index, meeting in enumerate(meeting_rows):
                if isinstance(meeting, str):
                    meeting = {"time_raw": meeting}
                if not isinstance(meeting, dict):
                    result["meetings"].append({"source_segment_index": segment_index,
                                               "raw_segment": deepcopy(meeting), "parse_status": "failed"})
                    result["issues"].append(_issue("meeting_not_object", index, f"meetings.{segment_index}"))
                    continue
                normalized = _mapped(meeting, MEETING_FIELDS, index,
                                     {"weekday": _weekday, "periods": _positive_set, "week_set": _positive_set,
                                      "start_period_raw": lambda value: deepcopy(value),
                                      "duration_raw": lambda value: deepcopy(value)})
                normalized.update({"source_segment_index": segment_index, "raw_segment": deepcopy(meeting)})
                for key in ("sfqd_raw", "dsz_raw", "xxq_raw"):
                    if normalized[key] is not None:
                        normalized["field_states"][key] = "pending_verification"
                for issue in normalized["issues"]:
                    issue["field_path"] = f"meetings.{segment_index}.{issue['field_path']}"
                result["issues"].extend(normalized["issues"])
                normalized["formal_enrollment_status"] = "unknown"
                normalized["calendar_dates"] = None
                result["meetings"].append(normalized)
    for key in ("sfqd_raw", "dsz_raw", "xxq_raw"):
        # Raw school codes may be present, but the semantics have not been
        # verified. Preserve assertions without claiming confirmed enrollment.
        result["field_states"][key] = "pending_verification" if result[key] is not None else result["field_states"][key]
    result["formal_enrollment_status"] = "unknown"
    result["section_identity_status"] = "unresolved"
    result["calendar_dates"] = None
    return result


def _exams(row, index):
    result = _mapped(row, EXAM_FIELDS, index,
                     {"exam_date": _iso_date, "start_time": _local_time, "end_time": _local_time})
    for field in ("final_exam_time_raw", "midterm_exam_time_raw", "final_exam_place_raw", "midterm_exam_place_raw"):
        # These are independent source observations, not one combined exam.
        # Preserve an explicitly blank source string while its value remains
        # semantically unknown; a missing field still has value None.
        if result[field] is None and result["field_states"][field] == "unknown":
            for assertion in result["field_assertions"].get(field, []):
                raw = assertion["raw_value"]
                value = raw.get("value", raw.get("raw_value")) if isinstance(raw, dict) else raw
                if isinstance(value, str) and not value.strip():
                    result[field] = value
                    break
    result["section_identity_status"] = "unresolved"
    result["calendar_conversion_status"] = "unknown"
    if (result["start_time"] and result["end_time"]
            and time.fromisoformat(result["end_time"]) <= time.fromisoformat(result["start_time"])):
        result["issues"].append(_issue("exam_end_not_after_start", index, "end_time"))
        result["field_states"]["end_time"] = "conflicting"
    return result


def parse_payload(domain, payload, scope=None):
    """Map one stripped business page and retain every input row.

    ``scope`` is a caller-supplied restriction, never identity evidence. The
    source row ordinal is local to this page; the storage layer adds the
    immutable observation/page version when identifying candidates. Parse
    diagnostics contain codes/positions only, while private ``raw_row`` and
    field assertions retain values for authorized reconciliation.
    """
    if domain not in BUSINESS_FIELDS:
        raise ValueError("unsupported_student_source_domain")
    if scope is not None and not isinstance(scope, dict):
        raise ValueError("invalid_student_source_scope")
    scope = {key: deepcopy(value) for key, value in (scope or {}).items() if key in _SCOPE_FIELDS}
    result = {"domain": domain, "parser_version": PARSER_VERSION, "mapping_version": MAPPING_VERSION,
              "scope": scope, "records": [], "row_failures": [], "issues": [], "status": "parsed"}
    containers = ("kbList", "records") if domain == "timetable" else ("items", "records")
    if domain == "profile":
        rows = [payload] if isinstance(payload, dict) else None
    elif isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        supplied = [payload[key] for key in containers if key in payload]
        rows = supplied[0] if supplied else None
        if len(supplied) > 1 and any(item != rows for item in supplied):
            result["issues"].append(_issue("conflicting_record_containers"))
            result["status"] = "partial"
    else:
        rows = None
    if not isinstance(rows, list):
        rows = []
        result["status"] = "schema_changed"
        result["issues"].append(_issue("record_container_missing_or_invalid"))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            record = {"source_row_index": index, "raw_row": deepcopy(row),
                      "parse_status": "failed", "field_states": {}, "issues": [_issue("row_not_object", index)]}
        else:
            if domain == "profile":
                record = _profile(row, index)
            elif domain == "academic":
                record = _academic(row, index)
            elif domain == "timetable":
                record = _timetable(row, index)
            else:
                record = _exams(row, index)
            mapping = {"profile": PROFILE_FIELDS, "academic": ACADEMIC_FIELDS,
                       "timetable": TIMETABLE_FIELDS, "exams": EXAM_FIELDS}[domain]
            if not any(not _unknown(record[field]) and record[field] not in ([], {}) for field in mapping):
                # An object/container alone is no usable business evidence.
                # Keep the projected empty row for count/position reconciliation.
                # Explicit empty items=[] is handled separately and is valid.
                record["issues"].append(_issue("business_fields_missing", index))
            _scope_issues(record, scope, index)
            record.update({"source_row_index": index, "raw_row": deepcopy(row),
                           "parse_status": "partial" if record["issues"] else "parsed"})
        record["evidence_refs"] = [{"domain": domain, "source_row_index": index}]
        result["records"].append(record)
        if record["parse_status"] != "parsed":
            result["row_failures"].append({"source_row_index": index, "parse_status": record["parse_status"],
                                           "codes": list(dict.fromkeys(item["code"] for item in record["issues"]))})
            if result["status"] == "parsed":
                result["status"] = "partial"
    coverage = deepcopy(scope.get("coverage")) if isinstance(scope.get("coverage"), dict) else {}
    total = coverage.get("reported_total", _MISSING)
    if isinstance(payload, dict):
        totals = [payload[key] for key in ("total", "totalCount", "totalResult") if key in payload]
        if totals:
            total = totals[0]
            if any(value != total for value in totals):
                result["issues"].append(_issue("conflicting_total_counts"))
    normalized_total = None
    if total is not _MISSING:
        try:
            normalized_total = _int_value(total)
            if normalized_total < 0:
                raise ValueError("invalid_integer")
        except ValueError:
            result["issues"].append(_issue("invalid_total_count"))
    count = len(rows)
    reconciliation = "unknown" if normalized_total is None else "matched" if normalized_total == count else "partial" if normalized_total > count else "conflicting"
    if reconciliation == "conflicting":
        result["issues"].append(_issue("visible_count_exceeds_reported_total"))
    if result["issues"] and result["status"] == "parsed":
        result["status"] = "partial"
    coverage.update({"observed_row_count": count, "retained_row_count": len(result["records"]),
                     "parsed_row_count": sum(row["parse_status"] == "parsed" for row in result["records"]),
                     "partial_row_count": sum(row["parse_status"] == "partial" for row in result["records"]),
                     "failed_row_count": sum(row["parse_status"] == "failed" for row in result["records"]),
                     "reported_total": normalized_total, "count_reconciliation": reconciliation})
    # A large requested page size is never an end marker or completeness proof.
    coverage.setdefault("completeness", "unknown")
    if result["status"] != "parsed" or reconciliation in {"partial", "conflicting"}:
        coverage["completeness"] = "partial" if rows else "unknown"
    result["coverage"] = coverage
    result["data_available"] = result["status"] != "schema_changed"
    return result
