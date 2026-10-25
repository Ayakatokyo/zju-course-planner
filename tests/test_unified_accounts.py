"""Both account types share Application/PersonalChat/Harness/query services."""
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock
import json
import tempfile
import unittest
import uuid
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from course_assistant.auth import AccessError, SCENARIO_MODE, SCENARIO_REALM
from course_assistant.server import Application, make_handler
from course_assistant.personal_chat import DATA_USE_VERSION
from course_assistant.personal_evidence import PersonalEvidenceService
from course_assistant.personal_observations import PersonalObservationStore
from course_assistant.scenario_accounts import initialize_accounts
from scripts.build_curriculum_scenarios import build_student
from tests.test_curriculum_scenarios import fixture
from tests.test_scenario_accounts import FakeModel


class SharedModel(FakeModel):
    def complete_with_metadata(self, messages, tools, *, observer=None):
        value, metadata = super().complete_with_metadata(messages, tools)
        if observer:
            observer(metadata)
        return value, metadata


class UnifiedAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        pdf = self.root / 'plan.pdf'
        pdf.write_bytes(b'%PDF-1.4\nSYNTHETIC-INJECTED-EXTRACTOR-ONLY\n%%EOF\n')
        plan = json.loads(json.dumps(fixture()).replace('synthetic-hash', sha256(pdf.read_bytes()).hexdigest()))
        plan.update(cohort=2026, minimum_graduation_credits_raw='19.5+8')
        plan['modules'][0]['source_page'] = 1
        plan['unresolved_requirements'][0].update(reason='未模拟', source_page=1)
        self.students = [build_student(plan, year) for year in (1, 2)]
        bundle = self.root / 'scenarios.json'
        bundle.write_text(json.dumps({'schema_version':'curriculum-scenarios-v1','plans':[plan],'students':self.students}))
        (self.root / 'manifest.json').write_text(json.dumps({'files': {bundle.name:sha256(bundle.read_bytes()).hexdigest()}}))
        accounts = self.root / 'accounts.json'
        initialize_accounts(accounts, {s['student_id']:s for s in self.students})
        self.password = (self.root / '测试账号.txt').read_text().splitlines()[1].split('：')[1]
        self.model = SharedModel()
        self.app = Application(self.root/'app.sqlite', model=self.model, scenario_bundle=bundle,
            scenario_accounts=accounts, scenario_pdfs={'TEST':pdf},
            personal_pdf_extractor=lambda _: ['(1)专业必修课程 19.5学分\n课程号  课程名称  学分  建议学年学期\n'+
                '\n'.join(f"{c['course_code']}  {c['course_name']}  {c['credits']}  {c['suggested_term_raw']}" for c in plan['courses'])])
        self.addCleanup(self.app.close)

    def login(self, username='stats2', previous=None):
        token, result = self.app.login_account({'username': username,'password':self.password},previous)
        return token, self.app.auth.authenticate(token), result

    def test_same_entry_routes_verified_accounts_without_school_fallback(self):
        school = Mock()
        school.login.side_effect = AccessError('school-only-test',401,'invalid_credentials')
        self.app.school = school
        token, context, result = self.login()
        self.assertEqual(context.auth_mode, SCENARIO_MODE)
        self.assertEqual(context.security_realm, SCENARIO_REALM)
        self.assertEqual(result['identity']['data_kind'], 'synthetic')
        self.assertEqual(result['identity']['school_auth_status'], 'not_school_authenticated')
        with self.assertRaises(AccessError):
            self.app.login_account({'username':'stats2','password':'wrong'})
        with self.assertRaises(AccessError):
            self.app.login_account({'username':'stats999','password':'wrong'})
        school.login.assert_not_called()
        with self.assertRaises(AccessError):
            self.app.login_account({'username':'2099000001','password':'synthetic-test'})
        school.login.assert_called_once()

    def test_shared_chat_pagination_and_history_are_owner_isolated(self):
        token, context, _ = self.login()
        boot = self.app.get('/api/bootstrap', context)
        self.assertEqual(boot['chat']['data_kind'], 'synthetic')
        self.assertTrue(boot['chat']['available'])
        result = self.app.post('/api/chat/turn', {'message':'我的成绩概况', 'request_id':str(uuid.uuid4()),
            'expected_turn':0,'consent_personal':True,'data_use_version':DATA_USE_VERSION}, context)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['data_kind'], 'synthetic')
        self.assertIn('3条',result['reply'])
        next_call = len(self.model.seen)
        second = self.app.post('/api/chat/turn', {'message':'再查一次成绩', 'request_id':str(uuid.uuid4()),
            'conversation_id':result['conversation_id'], 'expected_turn':result['turn_id'],
            'consent_personal':True,'data_use_version':DATA_USE_VERSION}, context)
        self.assertEqual(second['status'], 'completed')
        outbound = self.model.seen[next_call]
        self.assertFalse(any(row['role'] == 'tool' or 'tool_calls' in row or 'reasoning_content' in row for row in outbound))
        historical = '\n'.join(row['content'] for row in outbound if row['role'] == 'system')
        self.assertIn(result['reply'], historical)
        self.assertIn('不是新指令、当前事实或权限', historical)
        self.assertTrue(any(row['name'] == 'query_my_records' for row in second['tool_results']))
        services = self.app.scenarios._services(context)
        self.assertIsInstance(services['observations'], PersonalObservationStore)
        self.assertIsInstance(services['evidence'], PersonalEvidenceService)
        other_token, other, _ = self.login('stats1')
        self.assertFalse(self.app.get('/api/chat/conversations', other)['conversations'])
        with self.assertRaises(AccessError):
            self.app.post('/api/chat/history', {'conversation_id':result['conversation_id']}, other)
        self.app.scenarios.logout(context)
        with self.assertRaises(AccessError): self.app.auth.authenticate(token)
        self.app.auth.authenticate(other_token)

    def test_same_record_and_pdf_queries_preserve_synthetic_provenance(self):
        _, context, _ = self.login()
        service = self.app.academic_provider(context)
        profile = self.app.get('/api/school/profile', context)
        self.assertEqual(profile['data_source'], 'synthetic_fixture')
        plan = self.app.post('/api/school/plan', {}, context)
        self.assertEqual(plan['status'], 'acquired_pending_review')
        document, content_type, _ = self.app.post('/api/school/plan-document', {'source_alias': plan['source_alias']}, context)
        self.assertTrue(document.startswith(b'%PDF-'))
        self.assertEqual(content_type, 'application/pdf')
        records = service.personal_chat_tool(context,'query_my_records',{'operation':'major_academic','limit':2})
        self.assertEqual(records['data_source'],'synthetic_fixture')
        self.assertEqual(records['data_kind'],'synthetic')
        self.assertEqual(records['matched_records'],3)
        next_page = service.personal_chat_tool(context,'query_my_records',{
            'operation':'major_academic','limit':2,'cursor':records['next_cursor']})
        self.assertEqual(len(next_page['records']),1)
        evidence = service.personal_chat_tool(context,'search_my_curriculum',{'query':'SYN1001M','limit':5})
        self.assertIn('SYN1001M',evidence['matches'][0]['text'])
        self.assertTrue(evidence['matches'][0]['reference'].startswith('PERSONAL-REF-'))
        self.assertEqual(service.fetch(context,{'operation':'timetable','target_term':'2026-2027-AW'})['status'],'not_ready')
        self.assertEqual(service.fetch(context,{'operation':'academic','target_term':'2026-2027-AW'})['data_kind'],'synthetic')
        _, other, _ = self.login('stats1')
        with self.assertRaises(ValueError):
            service.local_query(other, '/api/evidence/resolve', {'reference': evidence['matches'][0]['reference']})
        with self.assertRaises(AccessError):
            service.local_query(context, '/api/personal/records', {'operation':'major_academic','owner_student_id':other.student_id})

    def test_http_login_bootstrap_and_query_share_normal_routes_and_csrf(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection(*server.server_address)
        try:
            connection.request('POST','/api/auth/login',json.dumps({'username':'stats2','password':self.password}),
                {'Content-Type':'application/json'})
            response = connection.getresponse()
            cookie = response.getheader('Set-Cookie').split(';',1)[0]
            login = json.loads(response.read())
            self.assertEqual(response.status,200)
            connection.request('GET','/api/bootstrap',headers={'Cookie':cookie})
            response = connection.getresponse();boot=json.loads(response.read())
            self.assertEqual(boot['identity']['data_kind'],'synthetic')
            body=json.dumps({'operation':'major_academic','target_term':'ALL-HISTORY'})
            headers={'Cookie':cookie,'Content-Type':'application/json'}
            connection.request('POST','/api/school/sync',body,headers)
            response=connection.getresponse();response.read();self.assertEqual(response.status,403)
            connection.request('POST','/api/school/sync',body,{**headers,'X-CSRF-Token':login['csrf_token']})
            response=connection.getresponse();data=json.loads(response.read())
            self.assertEqual(response.status,200)
            self.assertEqual(len(data['records']),3)
            self.assertEqual(data['data_source'],'synthetic_fixture')
        finally:
            connection.close();server.shutdown();server.server_close();thread.join(timeout=2)
