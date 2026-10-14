"""Run the real loopback application against a synthetic school transport.

This explicit validation entry point never imports SchoolHTTPClient, reads real
private materials, or sends model/network requests. It is not a production
entry point and is never loaded by ``python -m course_assistant.server``.
All displayed identities, courses, marks, places and documents are synthetic.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.model_client import ModelClient
from course_assistant.server import Application, make_handler
from course_assistant.student_source_client import DOMAINS
from course_assistant.student_sources import business_field_diagnostics, sanitize_payload


class NoOutboundModel(ModelClient):
    """A visibly unconfigured model which cannot send a request."""

    def __init__(self):
        super().__init__(config={})

    def complete(self, *args, **kwargs):
        raise AssertionError("synthetic school UI fixture must not call a model")


def academic_rows(operation, target_term):
    """75 source rows; normal bounded acquisition reads only 3 x 20."""
    year = target_term[:9] if operation == "academic" else None
    term = target_term.rsplit("-", 1)[-1]
    rows = []
    for index in range(75):
        start = 2023 + index % 4
        row = {
            "source_attempt_id": f"SYNTHETIC-ATTEMPT-{index + 1:03d}",
            "kch": "SYNTHETIC-PE-REPEAT" if index in {0, 1, 2} else f"SYNTHETIC-COURSE-{index + 1:03d}",
            "kcmc": "合成体育重复修读" if index in {0, 1, 2} else f"合成课程 {index + 1:02d}",
            "xnm": year or f"{start}-{start + 1}",
            "xqm": "秋冬" if term == "AW" else "春夏" if term == "SS" else ("秋冬", "春夏")[index % 2],
            "xf": ("1", "1.5", "2", "3")[index % 4],
            "cj": str(70 + index % 29),
            "major_status_raw": "主修",
        }
        if index % 10 == 3:
            row.update(cj="45", completion_status_raw="failed")
        elif index % 10 == 4:
            row.update(cj="在修", completion_status_raw="in_progress")
        elif index % 10 == 5:
            row.update(cj="90", completion_status_raw="passed")
        elif index % 10 == 6:
            row.update(cj="待认定", completion_status_raw="pending_recognition")
        elif index % 10 == 7:
            row.update(completion_status_raw="unknown")
        # Some rows have marks and no source completion assertion by design.
        if index == 8:
            row["kcmc"] = '<img src="SYNTHETIC-NO-RESOURCE" onerror="globalThis.SYNTHETIC_XSS=true">合成恶意文本'
        rows.append(row)
    return rows


def timetable_rows(target_term):
    year, season = target_term[:9], target_term.rsplit("-", 1)[-1]
    rows = []

    def add(number, name, time_raw, *, meetings=None, place="合成教学楼 101", **fields):
        row = {"kch": f"SYNTHETIC-TT-{number:02d}", "kcmc": name,
               "xkkh": f"({year}-{1 if season == 'AW' else 2})-SYNTHETIC-TT-{number:02d}",
               "jxb_id": f"SYNTHETIC-SECTION-{number:02d}", "xnm": year,
               "xqm": season, "xm": f"合成教师 {number}", "sksj": time_raw,
               "sfqd": "1", **fields}
        if place is not None:
            row["skdd"] = place
        if meetings is not None:
            row["meetings"] = meetings
        rows.append(row)

    def meeting(day, periods, weeks, raw, place="合成教学楼 101", **fields):
        return {"weekday": day, "periods": periods, "week_set": weeks,
                "time_raw": raw, "place_raw": place, **fields}

    add(1, "合成周一基础课", "周一第1-2节{1-8周}",
        meetings=[meeting(1, [1, 2], list(range(1, 9)), "周一第1-2节{1-8周}")], xxq="秋")
    add(2, "合成周一重叠课", "周一第2-3节{3-6周}",
        meetings=[meeting(1, [2, 3], [3, 4, 5, 6], "周一第2-3节{3-6周}", "合成教学楼 202")],
        place="合成教学楼 202", xxq="秋")
    add(3, "合成多时段课程", "周二第3-4节{1-8周}<br>周四第5-6节{1-8周}",
        meetings=[meeting(2, [3, 4], list(range(1, 9)), "周二第3-4节{1-8周}", "合成实验室 301"),
                  meeting(4, [5, 6], list(range(1, 9)), "周四第5-6节{1-8周}", "合成实验室 302")],
        place="合成实验室 301<br>合成实验室 302", xxq="秋")
    add(4, "合成转义换行课", "周三第7-8节{1-8周}&lt;br&gt;周五第9-10节{1-8周}",
        place="合成教室 401&lt;br&gt;合成教室 402", xxq="秋")
    add(5, "合成冬学期课", "周二第1-2节{1-8周}",
        meetings=[meeting(2, [1, 2], list(range(1, 9)), "周二第1-2节{1-8周}", "合成教学楼 501", xxq="冬")],
        place="合成教学楼 501", xxq="冬")
    add(6, "合成奇数周课程", "周四第1-2节{1-15周(单)}",
        meetings=[meeting(4, [1, 2], list(range(1, 16, 2)), "周四第1-2节{1-15周(单)}", "合成教学楼 601")],
        place="合成教学楼 601", xxq="秋冬", dsz="1")
    add(7, "合成未定星期课", "星期待定 第5-6节{1-8周}",
        meetings=[meeting(None, [5, 6], list(range(1, 9)), "星期待定 第5-6节{1-8周}")])
    add(8, "合成未定节次课", "周三 节次待定{1-8周}",
        meetings=[meeting(3, None, list(range(1, 9)), "周三 节次待定{1-8周}")])
    add(9, "合成缺地点课", "周五第1-2节{1-8周}", place=None,
        meetings=[meeting(5, [1, 2], list(range(1, 9)), "周五第1-2节{1-8周}", None)])
    add(10, "合成来源代码课", "周六第3-4节{1-8周}", sfqd="0", xxq="1", dsz="0",
        meetings=[meeting(6, [3, 4], list(range(1, 9)), "周六第3-4节{1-8周}", sfqd="0", xxq="1")])
    add(11, "合成未定时间 <script>globalThis.SYNTHETIC_XSS=true</script>", "待定",
        place="合成地点待定", sfqd="1", xxq="unknown")
    return rows


class SyntheticSchool:
    """Only documented synthetic accounts; no real transport exists here."""

    def __init__(self):
        self.session_epoch = 0
        self.owner = None
        self.closed = False

    def login(self, username, password):
        if username not in {"SYNTHETIC-LOGIN-A", "SYNTHETIC-LOGIN-B"} or not password:
            return {"state": "invalid_credentials"}
        self.owner = "SYNTHETIC-SCHOOL-" + username[-1]
        self.session_epoch += 1
        return self.get_profile()

    def get_profile(self):
        return {"state": "authenticated", "profile": {
            "source_subject_id": self.owner, "name": "合成学校测试用户 " + self.owner[-1],
            "admission_date_raw": "20230901", "college_name": "合成学院",
            "major_name": "合成专业", "university": "合成测试学校",
            "major_or_category_raw": "合成专业", "study_path_raw": "合成主修路径"}}

    def request(self, spec):
        if self.closed or self.owner is None:
            return {"state": "source_session_expired", "session_epoch": self.session_epoch}
        operation = spec["operation"]
        response = {"state": "business", "http_status": 200,
                    "content_type": "application/json", "session_epoch": self.session_epoch,
                    "source_subject_id": self.owner}
        if operation == "plan_document":
            head, end = b"%PDF-1.4\n% SYNTHETIC UI FIXTURE ONLY\n", b"\n%%EOF\n"
            pdf = head + b" " * (84 - len(head) - len(end)) + end
            assert len(pdf) == 84
            return {**response, "content_type": "application/pdf", "payload": pdf}
        if operation == "plan_catalogue":
            payload = {"items": [{"plan_ref": "SYNTHETIC-PLAN-2023", **deepcopy(spec["scope"]),
                                  "cohort_provenance": "source_row"}], "total": 1}
        elif operation in {"academic", "major_academic"}:
            rows = academic_rows(operation, spec["target_term"])
            offset, size = (spec["page"] - 1) * spec["page_size"], spec["page_size"]
            payload = {"items": rows[offset:offset + size], "total": 75, "pageNo": spec["page"]}
        elif operation == "timetable":
            def clock(minutes):
                return f"{minutes // 60:02d}:{minutes % 60:02d}"
            payload = {"kbList": timetable_rows(spec["target_term"]), "total": 11, "pageNo": 1,
                       "period_times": [{"period": i + 1, "start_time": clock(420 + i * 55),
                                         "end_time": clock(465 + i * 55)} for i in range(13)],
                       "period_times_source": {"state": "known", "page_path": "/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html"}}
        elif operation == "exams":
            payload = {"items": [
                {"kch": "SYNTHETIC-TT-01", "kcmc": "合成期中期末考试课", "xnm": spec["target_term"][:9],
                 "xkkh": f"({spec['target_term'][:9]}-{1 if spec['target_term'].rsplit('-', 1)[-1] in {'A','W'} else 2})-SYNTHETIC-TT-01",
                 "jxb_id": "SYNTHETIC-SECTION-01",
                 # Both source-season queries intentionally return this exact
                 # observation, to exercise conservative cross-season dedup.
                 "xxq": "秋冬" if spec["target_term"].rsplit("-", 1)[-1] in {"A", "W"} else "春夏",
                 "kssj": "2026-11-18 09:00-11:00",
                 "jsmc": "合成考场 101", "qzkssj": "2026-10-25 14:00-16:00", "qzjsmc": "合成考场 102"},
                {"kch": "SYNTHETIC-TT-09", "kcmc": "合成日期空值考试课",
                 "xkkh": f"({spec['target_term'][:9]}-{1 if spec['target_term'].rsplit('-', 1)[-1] in {'A','W'} else 2})-SYNTHETIC-TT-09",
                 "jxb_id": "SYNTHETIC-SECTION-09", "xxq": spec['target_term'].rsplit('-',1)[-1],
                 "kssj": "", "jsmc": "", "qzkssj": "", "qzjsmc": "", "ksrq": ""},
            ], "total": 2, "pageNo": 1}
        else:
            return {"state": "route_not_confirmed", "session_epoch": self.session_epoch}
        if operation in DOMAINS:
            projection, _ = sanitize_payload(DOMAINS[operation], payload)
            return {**response, "payload": projection,
                    "source_field_diagnostics": business_field_diagnostics(DOMAINS[operation], payload, projection)}
        return {**response, "payload": payload}

    def close(self):
        self.closed = True
        self.session_epoch += 1
        self.owner = None


class SyntheticClockReferenceSchool(SyntheticSchool):
    """Exercise published clock references with no source clock response."""

    def request(self, spec):
        response = super().request(spec)
        if spec["operation"] != "timetable" or response.get("state") != "business":
            return response
        payload = response["payload"]
        payload["period_times"] = []
        payload["period_times_source"] = {"state": "not_available", "page_path":
                                          "/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html"}
        for row in payload["kbList"]:
            if row.get("skdd"):
                row["skdd"] = "合成紫金港 " + row["skdd"]
            for meeting in row.get("meetings", []):
                if meeting.get("place_raw"):
                    meeting["place_raw"] = "合成紫金港 " + meeting["place_raw"]
            if row["kch"] == "SYNTHETIC-TT-06":
                row["sksj"] = "周四第11-13节{1-15周(单)}"
                row["meetings"][0].update(periods=[11, 12, 13], time_raw=row["sksj"])
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18876)
    parser.add_argument("--clock-reference", action="store_true",
                        help="use synthetic main-campus places and no source clocks")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with tempfile.TemporaryDirectory(prefix="synthetic-school-ui-") as directory:
        root = Path(directory).resolve()
        app = Application(root / "app.sqlite3", mock_enabled=False,
                          model=NoOutboundModel(), school_client_factory=(SyntheticClockReferenceSchool
                              if args.clock_reference else SyntheticSchool),
                          school_root=root / "private" / "school_accounts")
        try:
            server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
            print(f"SYNTHETIC ONLY: http://127.0.0.1:{args.port}/", flush=True)
            print("Accounts: SYNTHETIC-LOGIN-A / SYNTHETIC-LOGIN-B; use any synthetic password.", flush=True)
            print("Temporary private store; mock disabled; school and model networks absent.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            app.close()


if __name__ == "__main__":
    main()
