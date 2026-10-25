"""Synthetic audit regression cases; no school credentials or network."""
from copy import deepcopy
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from course_assistant.graduation_audit import project_rows
from course_assistant.student_source_parsers import parse_payload
from course_assistant.student_sources import sanitize_payload
from course_assistant.personal_dialogue import _record_source, _term
from tests import test_school_http as http_tests
from tests import test_school_accounts as account_tests

ROWS = [
    {"JDDM": "M1", "JDMC": "合成选修模块", "SFTG": "通过", "YQXF": "4", "YXXF": "4"},
    {"JDDM": "head_M1", "FJDDM": "M1", "JDMC": "课程名称", "KCDM": "课程代码"},
    {"JDDM": "C1", "FJDDM": "M1", "JDMC": "合成备选课", "KCDM": "SYN001", "KCXF": "2"},
    {"JDDM": "C2", "FJDDM": "M1", "JDMC": "合成在修课", "KCDM": "SYN002", "KCBZ": "在修"},
    {"JDDM": "C3", "FJDDM": "M1", "JDMC": "合成替代课", "KCDM": "SYN003", "SFTG": "被替代", "TDKC": "个人替换：合成课程"},
    {"JDDM": "C4", "FJDDM": "M1", "JDMC": "合成重复课", "KCDM": "SYN004", "SFTG": "通过", "CJ": "80"},
    {"JDDM": "C5", "FJDDM": "M1", "JDMC": "合成重复课", "KCDM": "SYN004", "SFTG": "通过", "CJ": "90"},
    {"JDDM": "M2", "JDMC": "合成总要求", "SFTG": "不通过", "YQXF": "150+8", "YXXF": "140+2"},
]

class GraduationAuditTests(unittest.TestCase):
    def test_tree_status_substitution_and_duplicate_attempts(self):
        payload = project_rows(ROWS)
        parsed = parse_payload('graduation_audit', sanitize_payload('graduation_audit', payload)[0])
        self.assertEqual(parsed['status'], 'parsed')
        rows = parsed['records']
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[1]['completion_status'], 'unknown')
        self.assertEqual(rows[2]['completion_status'], 'in_progress')
        self.assertEqual(rows[3]['completion_status'], 'replaced')
        self.assertEqual([r['grade_raw'] for r in rows if r['course_code_raw']=='SYN004'], ['80','90'])
        self.assertEqual(rows[-1]['earned_credits_raw'], '140+2')

    def test_invalid_or_truncated_tree_fails_closed(self):
        for rows in ([], ROWS + [ROWS[0]], [dict(ROWS[2], FJDDM='missing')],
                     [dict(ROWS[0], FJDDM='M1')], ROWS * 300):
            with self.assertRaises(ValueError):
                project_rows(rows)

    def test_protocol_is_only_read_query_and_all_history(self):
        harness = http_tests.SchoolHTTPTests()
        client = harness.client([http_tests.Response(ROWS)])
        self.assertEqual(harness.request('graduation_audit')['state'], 'route_not_confirmed')
        result = harness.request('graduation_audit', 'ALL-HISTORY')
        self.assertEqual(result['state'], 'business')
        request = harness.opener.requests[-1]
        self.assertEqual(urlsplit(request['url']).path, '/jwglxt/bysh/byshck_cxByshzsIndex.html')
        self.assertEqual(parse_qs(urlsplit(request['url']).query)['doType'], ['query'])
        self.assertEqual(parse_qs(request['body'].decode())['queryModel.showCount'], ['5000'])
        self.assertNotIn('xszsBysh', request['url'])
        self.assertTrue(_term('graduation_audit','ALL-HISTORY'))
        self.assertFalse(_term('graduation_audit','2026-2027-AW'))
        self.assertEqual(_record_source('graduation_audit',{'target_term':'ALL-HISTORY'}, {})['scope_kind'],'graduation_self_audit')

    def test_subject_mismatch_is_checked_before_projection(self):
        h = http_tests.SchoolHTTPTests()
        rows = deepcopy(ROWS)
        rows[0]['xh'] = 'SYNTHETIC-OTHER'
        h.client([http_tests.Response(rows)])
        self.assertEqual(h.request('graduation_audit','ALL-HISTORY')['state'],'source_subject_changed')

    def test_authenticated_live_query_reuses_same_turn_then_refreshes(self):
        h = account_tests.SchoolAccountTests()
        h.setUp()
        try:
            response = {'state':'business','http_status':200,'content_type':'application/json','payload':project_rows(ROWS)}
            _, context, client, _ = h.login(account_tests.FakeSchoolClient(requests=[deepcopy(response), deepcopy(response)]))
            receipts=[]
            args={'operation':'graduation_audit','mode':'summary'}
            result=h.live(context,'query_my_records',args,receipts)
            self.assertEqual(result['freshness'],'fetched_in_current_turn')
            from course_assistant.personal_dialogue import PersonalDialogueService
            _, remote = PersonalDialogueService._project('query_my_records',result,{'operation':'graduation_audit','mode':'summary','limit':15},'12345678',{},1,[0,0],set())
            self.assertIn('evidence_id',remote['graduation_summary'])
            self.assertEqual(result['graduation_summary']['course_record_count'],5)
            self.assertEqual(result['graduation_summary']['modules'][0]['completion_status_raw'],'通过')
            course=h.live(context,'query_my_records',{'operation':'graduation_audit','query':'SYN002'},receipts)
            self.assertEqual(course['records'][0]['study_note_raw'],'在修')
            self.assertEqual(len(client.request_calls),1)
            h.live(context,'query_my_records',args,[])
            self.assertEqual(len(client.request_calls),2)
        finally:
            h.doCleanups()

class EnrollmentPreviewTests(unittest.TestCase):
    def test_read_only_preview_contract_and_scope(self):
        from course_assistant.school_enrollment_preview import PAGE, COURSES, SECTIONS, CHOSEN
        html = ''.join(f'<input id="{key}" value="{value}">' for key,value in
            {'xn':'2026-2027','xq':'1','nj':'2023','zydm':'SYN','ylxs':'1'}.items())
        html += '<li class="list-group-item" data-xkkh="SYN-SECTION" data-kcdm="SYN001">已选上 / 合成教师</li>'
        h=http_tests.SchoolHTTPTests()
        h.client([http_tests.Response(html),http_tests.Response([{'kcdm':'SYN001','kcmc':'合成课','xkkh':'SYN-PARENT'}]),
                  http_tests.Response([{'kcdm':'SYN001','xkkh':'SYN-SECTION','sxbj':'1'}]),
                  http_tests.Response(json.dumps([{'xkkh':'SYN-SECTION','jsxm':'合成教师','rs':'1/20'}]), content_type='text/html')])
        result=h.http.enrollment_preview('SYN001')
        self.assertEqual(result['state'],'business')
        self.assertFalse(result['preview']['submission_enabled'])
        self.assertEqual(result['preview']['selected'][0]['kcdm'],'SYN001')
        paths=[urlsplit(r['url']).path for r in h.opener.requests[-4:]]
        self.assertEqual(paths,[PAGE,COURSES,CHOSEN,SECTIONS])
        self.assertEqual(parse_qs(h.opener.requests[-1]['body'].decode())['xkkh'], ['SYN-PARENT'])
        self.assertFalse(any('Bc' in path for path in paths))
        self.assertEqual(h.http.enrollment_preview('../arbitrary')['state'],'route_not_confirmed')

    def test_course_must_be_in_fresh_preview(self):
        html=''.join(f'<input id="{k}" value="{v}">' for k,v in {'xn':'2026-2027','xq':'1','nj':'2023','zydm':'SYN','ylxs':'1'}.items())
        h=http_tests.SchoolHTTPTests()
        h.client([http_tests.Response(html),http_tests.Response([]),http_tests.Response([])])
        result=h.http.enrollment_preview('UNKNOWN')
        self.assertEqual(result['state'],'route_not_confirmed')
        self.assertEqual(len(h.opener.script),0)
