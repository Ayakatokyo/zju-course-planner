"""Export a frozen planning result, never an enrollment instruction."""
import csv
import io
import json
from datetime import datetime, timezone
from .importer import ImportErrorDetail, digest
from .planner import _exam_interval, meeting_occurrences


def safe_cell(value):
    value = str(value if value is not None else "未知")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ImportErrorDetail("冻结结果包含不能导出的数值或字段") from error


def _missing(result):
    validation = result.get("plan", {}).get("validation", {})
    return [{"code": item.get("code"), "section_ids": item.get("section_ids", []),
             "result": item.get("result"), "reason": item.get("reason"),
             "source_refs": item.get("source_refs", []), "missing_field_paths": item.get("missing_field_paths", [])}
            for item in validation.get("checks", []) if item.get("result") != "pass"]


def export_result(result, format):
    _json(result)
    if format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode(), "application/json; charset=utf-8", "json"
    sections = result["plan"]["sections"]
    input_versions = result["plan"].get("validation", {}).get("input_version_refs", result.get("versions", {}))
    missing = _missing(result)
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["数据模式", "方案状态", "快照", "课程号", "课程", "教学班", "计划学分", "分学期", "星期", "周次", "节次", "地点", "验证结果", "冻结输入版本", "偏好版本", "来源", "待确认与冲突", "考试安排", "冻结偏好和锁定"])
        for section in sections:
            for meeting in section.get("meetings") or [{}]:
                meeting = meeting if isinstance(meeting, dict) else {}
                values = [result["data_kind"], "规划草案/非中选", result["versions"]["offering_snapshot"],
                          section["course_id"], section.get("course_name"), section["section_id"], section.get("credits"),
                          "/".join(meeting.get("season_codes") or []) or None, meeting.get("day_of_week"),
                          ",".join(map(str, meeting.get("week_numbers") or [])) or None,
                          ",".join(map(str, meeting.get("period_indices") or [])) or None, meeting.get("building_room"),
                          result["plan"]["validation"]["result"], _json(input_versions), result.get("constraints", {}).get("revision"),
                          _json(section.get("source_refs", []) + meeting.get("source_refs", [])), _json(missing),
                          _json({"state": section.get("exam_state", "unknown"), "exams": section.get("exams")}),
                          _json(result.get("constraints", {}))]
                writer.writerow([safe_cell(value) for value in values])
        return ("\ufeff" + stream.getvalue()).encode(), "text/csv; charset=utf-8", "csv"
    if format != "ics":
        raise ImportErrorDetail("导出格式仅支持 json、csv、ics")
    calendar = result["frozen_dataset"].get("academic_calendar", {})
    if (not isinstance(calendar, dict) or result.get("data_kind") != "synthetic" or calendar.get("data_kind") != "synthetic"
            or calendar.get("timezone") != "Asia/Shanghai"):
        raise ImportErrorDetail("缺少显式校历/节次时刻映射，不能导出日历")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Course Assistant//Synthetic Planner//ZH", "CALSCALE:GREGORIAN", "X-WR-CALNAME:synthetic 选课规划（非中选）", "X-WR-TIMEZONE:Asia/Shanghai"]
    def escaped(text):
        return str(text).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines.extend(["X-COURSE-INPUT-VERSIONS:" + escaped(_json(input_versions)),
                  "X-COURSE-OPEN-ISSUES:" + escaped(_json(missing)),
                  "X-COURSE-SCOPE:" + escaped("仅导出冻结输入中已知课时、固定安排和已知考试；待确认考试和缺失安排仍见问题清单，不构成完整正式课表"),
                  "X-COURSE-VALIDATION:" + escaped(result["plan"]["validation"]["result"])])
    def event(identifier, start, end, title, location, description, local=False):
        start_line = (f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}" if local else
                      "DTSTART:" + start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        end_line = (f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}" if local else
                    "DTEND:" + end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        lines.extend(["BEGIN:VEVENT", f"UID:{identifier}@synthetic.course-assistant.local", f"DTSTAMP:{stamp}",
                      start_line, end_line,
                      "SUMMARY:" + escaped(title), "LOCATION:" + escaped(location or "未知"),
                      "DESCRIPTION:" + escaped(description), "STATUS:TENTATIVE", "END:VEVENT"])
    for section in sections:
        if not section.get("meetings"):
            raise ImportErrorDetail("时段未知，不能导出日历")
        for meeting in section["meetings"]:
            occurrences = meeting_occurrences(meeting, calendar)
            if occurrences is None:
                raise ImportErrorDetail("上课时段、校历版本或节次日期映射不完整，不能导出日历")
            for item in occurrences:
                identifier = digest([section["section_id"], meeting.get("meeting_id"), item["date"], item["period_index"], input_versions])
                description = f"模拟规划，非正式中选。{section['section_id']}；冻结输入 {_json(input_versions)}；来源 {_json(section.get('source_refs', []) + meeting.get('source_refs', []))}；待确认 {_json(missing)}"
                # Preserve explicit native timezone representation for this draft.
                event(identifier, item["start"], item["end"], "[synthetic] " + section.get("course_name", section["course_id"]), meeting.get("building_room"), description, local=True)
        if section.get("exam_state") in {"known", "resolved"}:
            for exam in section.get("exams") or []:
                interval = _exam_interval(exam)
                if interval is None:
                    raise ImportErrorDetail("考试日期或时刻不完整，不能导出考试日历")
                identifier = digest([section["section_id"], exam.get("exam_id"), "exam", input_versions])
                event(identifier, interval[0], interval[1], "[synthetic 考试] " + section.get("course_name", section["course_id"]),
                      exam.get("location_raw"), f"模拟考试安排，非正式中选。来源 {_json(exam.get('source_refs', []))}；冻结输入 {_json(input_versions)}")
    for position, meeting in enumerate(result.get("constraints", {}).get("fixed_meetings", [])):
        occurrences = meeting_occurrences(meeting, calendar)
        if occurrences is None:
            raise ImportErrorDetail("固定安排的时段或校历映射不完整，不能导出日历")
        for item in occurrences:
            event(digest(["fixed", position, item["date"], item["period_index"], input_versions]),
                  item["start"], item["end"], "[synthetic 固定安排] " + meeting.get("title", "个人固定安排"),
                  meeting.get("building_room"), f"个人固定安排；冻结输入 {_json(input_versions)}；来源 {_json(meeting.get('source_refs', []))}", local=True)
    lines.append("END:VCALENDAR")
    # RFC5545 lines must fold at 75 UTF-8 octets; continuation begins with space.
    folded = []
    for line in lines:
        current = ""
        for char in line:
            if len((current + char).encode()) > 75:
                folded.append(current)
                current = " "
            current += char
        folded.append(current)
    return ("\r\n".join(folded) + "\r\n").encode(), "text/calendar; charset=utf-8", "ics"
