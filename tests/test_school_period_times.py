"""Synthetic HTML only: no real school page, person, or default clock schedule."""
from copy import deepcopy
import unittest

from course_assistant.school_period_times import (PERIOD_PAGE_LIMIT, TIMETABLE_PAGE,
                                                parse_period_table, period_source, validate_period_metadata)
from course_assistant.student_sources import StudentSourceError, sanitize_payload
from course_assistant.student_source_parsers import parse_payload


HEADER = '<tr><th colspan="2">时间</th><th>星期一</th><th>星期二</th></tr>'
CLOCK_TABLE = '<table>' + HEADER + '<tr><td><i>1</i></td><td>07:10<br>—<br>07:55</td><td>合成课程 考试 13:00—14:00</td></tr></table>'
TIMES = [{"period": 1, "start_time": "07:10", "end_time": "07:55"}]


class PeriodTimesTests(unittest.TestCase):
    def test_standalone_clock_pair_is_separate_from_course_and_exam_text(self):
        times, source, subjects = parse_period_table(CLOCK_TABLE)
        self.assertEqual(times, TIMES)
        self.assertEqual(source, {"state": "known", "page_path": TIMETABLE_PAGE})
        self.assertEqual(subjects, set())
        self.assertNotIn("合成课程", str((times, source)))

    def test_headerless_course_only_scripts_inputs_and_nested_tables_do_not_supply_clocks(self):
        cases = [CLOCK_TABLE.replace(HEADER, ''),
                 '<table>' + HEADER + '<tr><td>1</td><td>合成课 考试13:00—14:00</td></tr></table>',
                 '<script>' + CLOCK_TABLE + '</script>',
                 '<table>' + HEADER + '<tr><td>1</td><td><input value="07:10—07:55"></td></tr></table>',
                 '<table>' + HEADER + '<tr><td>1</td><td><table><tr><td>07:10—07:55</td></tr></table></td></tr></table>',
                 CLOCK_TABLE.replace('07:10', '25:00'),
                 CLOCK_TABLE.replace('<i>1</i>', 'unknown')]
        for html in cases:
            with self.subTest(html=html[:30]):
                self.assertEqual(parse_period_table(html)[:2], ([], period_source()))

    def test_ambiguous_duplicate_out_of_order_and_unbounded_html_are_rejected(self):
        cases = [CLOCK_TABLE.replace('</table>', '<tr><td>1</td><td>07:20—08:00</td></tr></table>'),
                 CLOCK_TABLE.replace('colspan="2"', 'colspan="3"').replace('<i>1</i>', '<i>1</i></td><td>2'),
                 CLOCK_TABLE.replace('07:10', '09:00'),
                 CLOCK_TABLE.replace('</table>', ''),
                 'x' * (PERIOD_PAGE_LIMIT + 1)]
        for html in cases:
            with self.subTest(size=len(html)):
                with self.assertRaises(ValueError):
                    parse_period_table(html)

    def test_weekday_course_or_exam_cells_cannot_supply_a_missing_clock(self):
        for header in (HEADER, '<tr><th>时间</th><th>星期一</th><th>星期二</th></tr>'):
            html = '<table>' + header + '<tr><td>1</td><td></td><td>13:00—14:00</td></tr></table>'
            self.assertEqual(parse_period_table(html)[:2], ([], period_source()))
        html = '<table>' + HEADER + '<tr><td>1</td><td>07:10—07:55</td><td>13:00—14:00</td></tr></table>'
        self.assertEqual(parse_period_table(html)[:2], (TIMES, period_source("known")))

    def test_time_header_colspan_and_morning_rowspan_locate_clock_columns(self):
        html = ('<table><tr><th rowspan="2" colspan="3">时间</th><th colspan="2">星期一</th><th colspan="2">星期二</th></tr>'
                '<tr><th>单</th><th>双</th><th>单</th><th>双</th></tr>'
                '<tr><td rowspan="2">上午</td><td>1</td><td>07:10—07:55</td><td rowspan="2" colspan="2">13:00—14:00</td><td></td><td></td></tr>'
                '<tr><td>2</td><td>08:10—08:55</td><td></td><td></td></tr></table>')
        expected = [*TIMES, {"period": 2, "start_time": "08:10", "end_time": "08:55"}]
        self.assertEqual(parse_period_table(html)[:2], (expected, period_source("known")))

    def test_unknown_overlapping_or_conflicting_header_spans_do_not_guess_columns(self):
        cases = [CLOCK_TABLE.replace('colspan="2"', 'colspan="unknown"'),
                 CLOCK_TABLE.replace('colspan="2"', 'colspan="0"'),
                 '<table>' + HEADER + HEADER.replace('colspan="2"', 'colspan="3"') +
                 '<tr><td>1</td><td>07:10—07:55</td></tr></table>',
                 '<table>' + HEADER + '<tr><td></td><td rowspan="2">07:10—07:55</td></tr>' +
                 '<tr><td colspan="2">1</td><td>13:00—14:00</td></tr></table>']
        for html in cases:
            self.assertEqual(parse_period_table(html)[:2], ([], period_source()))

    def test_explicit_period_and_time_headers_are_usable_without_spanning_header(self):
        html = CLOCK_TABLE.replace('<th colspan="2">时间</th>', '<th>节次</th><th>时间</th>')
        self.assertEqual(parse_period_table(html)[:2], (TIMES, period_source("known")))

    def test_fixed_metadata_is_whitelisted_only_at_timetable_payload_root(self):
        payload = {"kbList": [{"kcmc": "合成课", "period_times": deepcopy(TIMES)}],
                   "period_times": TIMES, "period_times_source": period_source("known"), "unknown": "synthetic"}
        safe, removed = sanitize_payload("timetable", payload)
        self.assertEqual(safe["period_times"], TIMES)
        self.assertNotIn("period_times", safe["kbList"][0])
        self.assertEqual(removed, 2)
        parsed = parse_payload("timetable", safe)
        self.assertEqual(parsed["period_times"], TIMES)
        self.assertEqual(parsed["period_times_source"], period_source("known"))
        self.assertNotIn("period_times", sanitize_payload("academic", payload)[0])

    def test_invalid_metadata_drops_no_unvalidated_clock_into_normalized_result(self):
        bad = deepcopy(TIMES)
        bad[0]["raw_html"] = "SYNTHETIC-PRIVATE"
        payload = {"kbList": [], "period_times": bad, "period_times_source": period_source("known")}
        with self.assertRaises(StudentSourceError):
            sanitize_payload("timetable", payload)
        parsed = parse_payload("timetable", payload)
        self.assertEqual(parsed["period_times"], [])
        self.assertEqual(parsed["period_times_source"]["state"], "schema_changed")
        self.assertEqual(parsed["issues"][0]["code"], "invalid_period_source")
        for source in ({"state": "known", "page_path": 'https://foreign.invalid/'},
                       {**period_source("known"), "cookie": "SYNTHETIC-COOKIE"},
                       period_source("not_available")):
            with self.assertRaises(ValueError):
                validate_period_metadata(TIMES, source)
