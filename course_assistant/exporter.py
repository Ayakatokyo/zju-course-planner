"""Export a frozen planning result, never an enrollment instruction."""
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from .importer import ImportErrorDetail, digest


def safe_cell(value):
    value = str(value if value is not None else "未知")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_result(result, format):
    if format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8", "json"
    sections = result["plan"]["sections"]
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["数据模式", "方案状态", "快照", "课程号", "课程", "教学班", "计划学分", "分学期", "星期", "周次", "节次", "地点", "验证结果"])
        for section in sections:
            for meeting in section.get("meetings") or [{}]:
                writer.writerow([safe_cell(x) for x in [result["data_kind"], "规划草案/非中选", result["versions"]["offering_snapshot"], section["course_id"], section.get("course_name"), section["section_id"], section.get("credits"), "/".join(meeting.get("season_codes") or []) or None, meeting.get("day_of_week"), ",".join(map(str, meeting.get("week_numbers") or [])) or None, ",".join(map(str, meeting.get("period_indices") or [])) or None, meeting.get("building_room"), result["plan"]["validation"]["result"]]])
        return ("\ufeff" + stream.getvalue()).encode(), "text/csv; charset=utf-8", "csv"
    if format != "ics":
        raise ImportErrorDetail("导出格式仅支持 json、csv、ics")
    calendar = result["frozen_dataset"].get("academic_calendar", {})
    if calendar.get("data_kind") != "synthetic" or calendar.get("timezone") != "Asia/Shanghai":
        raise ImportErrorDetail("缺少显式校历/节次时刻映射，不能导出日历")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Course Assistant//Synthetic Planner//ZH", "CALSCALE:GREGORIAN", "X-WR-CALNAME:synthetic 选课规划（非中选）", "X-WR-TIMEZONE:Asia/Shanghai"]
    def escaped(text):
        return str(text).replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for section in sections:
        if not section.get("meetings"):
            raise ImportErrorDetail("时段未知，不能导出日历")
        for meeting in section["meetings"]:
            if meeting.get("academic_calendar_id") != calendar.get("calendar_id"):
                raise ImportErrorDetail("校历版本不一致，不能导出日历")
            for key in ("day_of_week", "week_numbers", "period_indices", "season_codes"):
                if not meeting.get(key):
                    raise ImportErrorDetail(f"缺少 {key}，不能导出日历")
            for season in meeting["season_codes"]:
                monday = calendar.get("season_first_mondays", {}).get(season)
                if not monday:
                    raise ImportErrorDetail("缺少分学期日期映射")
                for week in meeting["week_numbers"]:
                    day = date.fromisoformat(monday) + timedelta(weeks=week-1, days=meeting["day_of_week"]-1)
                    for period in meeting["period_indices"]:
                        times = calendar.get("period_times", {}).get(str(period))
                        if not times:
                            raise ImportErrorDetail("缺少节次时刻映射")
                        identifier = digest([section["section_id"], meeting.get("meeting_id"), season, week, period, result["versions"]])
                        lines.extend(["BEGIN:VEVENT", f"UID:{identifier}@synthetic.course-assistant.local", f"DTSTAMP:{stamp}", f"DTSTART;TZID=Asia/Shanghai:{day.strftime('%Y%m%d')}T{times[0].replace(':','')}00", f"DTEND;TZID=Asia/Shanghai:{day.strftime('%Y%m%d')}T{times[1].replace(':','')}00", "SUMMARY:" + escaped("[synthetic] " + section.get("course_name", section["course_id"])), "LOCATION:" + escaped(meeting.get("building_room", "未知")), "DESCRIPTION:" + escaped(f"模拟规划，非正式中选。{section['section_id']}；快照 {result['versions']['offering_snapshot']}；规则 {result['versions']['rule_version']}"), "STATUS:TENTATIVE", "END:VEVENT"])
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
