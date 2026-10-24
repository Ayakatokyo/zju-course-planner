"""Isolated synthetic account, protocol and HTTP security checks (no network model)."""
from copy import deepcopy
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import tempfile
import threading
import unittest

from course_assistant.auth import AccessError
from course_assistant.scenario_accounts import (COOKIE, ScenarioApplication, ScenarioAdapter,
    initialize_accounts, load_bundle, make_handler)
from scripts.build_curriculum_scenarios import build_student
from tests.test_curriculum_scenarios import fixture


class FakeModel:
    def __init__(self):
        self.seen = []
        self.thinking = True
        self.revoke = None
    def with_options(self, model, thinking):
        assert model == 'deepseek-flash'
        self.thinking = thinking
        return self
    def status(self):
        return {'configured': True, 'model': 'deepseek-flash', 'thinking': self.thinking}
    def complete(self, messages, tools):
        return self.complete_with_metadata(messages, tools)[0]
    def complete_with_metadata(self, messages, tools):
        self.seen.append(deepcopy(messages))
        if self.revoke:
            self.revoke()
        rows = [m for m in messages if m['role'] == 'tool']
        def call(name, args, num):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-'+str(num), 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}
        if not rows:
            result = call('query_my_records', {'operation': 'major_academic', 'mode': 'summary'}, 1)
        else:
            data = json.loads(rows[-1]['content'])
            result = call('finish_answer', {'kind': 'answer', 'text': '本模拟账号有'+str(data['matched_records'])+'条成绩记录。',
                'evidence_ids': [data['source_grade_summary']['evidence_id']]}, 2)
        return result, {'model': 'deepseek-flash', 'status': 'completed', 'http_status': 200}


class ScenarioAccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        plan = fixture()
        plan['cohort'] = 2026
        plan['minimum_graduation_credits_raw'] = '19.5+8'
        plan['modules'][0]['source_page'] = 1
        plan['unresolved_requirements'][0].update(reason='未模拟', source_page=1)
        self.students = [build_student(plan, year) for year in (1, 2, 3)]
        bundle = {'schema_version': 'curriculum-scenarios-v1', 'plans': [plan], 'students': self.students}
        self.path = self.root / 'scenarios.json'
        self.path.write_text(json.dumps(bundle, ensure_ascii=False))
        (self.root / 'manifest.json').write_text(json.dumps({'files': {'scenarios.json': sha256(self.path.read_bytes()).hexdigest()}}))
        self.accounts = self.root / 'accounts.json'
        initialize_accounts(self.accounts, {s['student_id']: s for s in self.students})
        self.password = (self.root / '测试账号.txt').read_text().splitlines()[1].split('：')[1]
        self.model = FakeModel()
        self.now = [1000.0]
        self.app = ScenarioApplication(self.path, self.accounts, self.model, clock=lambda: self.now[0])
    def login(self, username='stats1', previous=None):
        return self.app.login({'username': username, 'password': self.password}, previous)
    def test_verified_password_binding_and_no_plain_password_store(self):
        a, b = self.login(), self.login('stats2')
        self.assertEqual(self.app.view(a)['student']['study_year'], 1)
        self.assertEqual(self.app.view(b)['student']['study_year'], 2)
        self.assertNotIn(self.password, self.accounts.read_text())
        for body in ({'username': 'stats1', 'password': 'wrong'}, {'username': 'unknown', 'password': self.password},
                     {'username': 'stats1', 'password': self.password, 'student_id': self.students[1]['student_id']}):
            with self.assertRaises(AccessError): self.app.login(body)
    def test_login_rotation_revokes_previous_session(self):
        a = self.login(); b = self.login('stats2', a)
        with self.assertRaises(AccessError): self.app.authenticate(a)
        self.assertEqual(self.app.view(b)['turns'], [])
    def test_cookie_distinct_from_school(self):
        from course_assistant.auth import COOKIE_NAME
        self.assertNotEqual(COOKIE, COOKIE_NAME)
    def test_expired_session_rejected(self):
        token = self.login(); self.now[0] += 1801
        with self.assertRaises(AccessError): self.app.authenticate(token)
    def test_tampered_bundle_rejected_before_login(self):
        self.path.write_text(self.path.read_text()+' ')
        with self.assertRaises(ValueError): load_bundle(self.path)
    def test_account_cannot_bind_unknown_student(self):
        accounts=json.loads(self.accounts.read_text());accounts['stats1']['student_id']='REAL-OTHER'
        self.accounts.write_text(json.dumps(accounts))
        with self.assertRaises(ValueError): ScenarioApplication(self.path,self.accounts,self.model)
    def test_adapter_rejects_cross_student_scope(self):
        adapter = next(iter(self.app.adapters.values()))
        with self.assertRaises(AccessError): adapter.personal_chat_tool('SYN-OTHER', 'get_my_context', {})
    def test_stage_records_and_pagination_are_consistent(self):
        adapter = self.app.adapters[self.students[1]['student_id']]
        owner = adapter.student['student_id']
        a = adapter.personal_chat_tool(owner, 'query_my_records', {'operation':'major_academic','limit':2})
        b = adapter.personal_chat_tool(owner, 'query_my_records', {'operation':'major_academic','limit':2,'cursor':a['next_cursor']})
        self.assertEqual(a['matched_records'],3)
        self.assertEqual(a['source_record_indices']+b['source_record_indices'],[0,1,2])
        self.assertEqual(a['source_grade_summary']['matched_records'],3)
        self.assertIsNone(a['records'][0]['grade_points_raw'])
    def test_future_records_and_real_endpoints_not_available(self):
        adapter = next(iter(self.app.adapters.values()));owner=adapter.student['student_id']
        self.assertEqual(adapter.personal_chat_tool(owner,'query_my_records',{'operation':'timetable'})['status'],'not_ready')
        with self.assertRaises(AccessError): adapter.personal_chat_tool(owner,'generate_current_term_plan',{})
    def test_bound_search_preserves_unknown_requirements(self):
        adapter=next(iter(self.app.adapters.values()));owner=adapter.student['student_id']
        result=adapter.personal_chat_tool(owner,'search_my_curriculum',{'query':'未确认规则','limit':5})
        self.assertTrue(any('未模拟' in c['text'] for c in result['matches']))
        result=adapter.personal_chat_tool(owner,'search_my_curriculum',{'query':'下学期 春夏','limit':5})
        self.assertTrue(any('SYN1002M' in c['text'] for c in result['matches']))
    def test_same_dialogue_harness_and_synthetic_projection(self):
        a,b=self.login(),self.login('stats2')
        result=self.app.chat(a,{'message':'我的成绩概况'})
        self.assertEqual(result['status'],'completed')
        self.assertIn('1条',result['reply'])
        self.assertEqual(self.app.view(b)['turns'],[])
        payload=json.dumps(self.model.seen,ensure_ascii=False)
        self.assertIn('synthetic',payload)
        self.assertNotIn(self.password,payload)
        self.assertNotIn(a,payload)
        self.assertTrue(list((self.root/'runs').glob('*.json')))
    def test_client_cannot_select_other_student_or_inject_history(self):
        token=self.login()
        for field in ('student_id','history','conversation_id','auth_mode'):
            with self.assertRaises(AccessError):self.app.chat(token,{'message':'你好',field:'other'})
    def test_inflight_logout_prevents_answer_delivery(self):
        token=self.login()
        self.model.revoke=lambda:self.app.sessions.pop(token,None)
        with self.assertRaises(AccessError):self.app.chat(token,{'message':'我的成绩概况'})
        self.assertFalse(list((self.root/'runs').glob('*.json')))
    def test_parallel_same_session_rejected(self):
        token=self.login();session=self.app.authenticate(token);session['lock'].acquire()
        try:
            with self.assertRaises(AccessError):self.app.chat(token,{'message':'我的成绩'})
        finally:session['lock'].release()
    def test_shared_frontend_and_http_guards(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.app))
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            port=server.server_port
            def request(method,path,body=None,headers=None):
                c=HTTPConnection('127.0.0.1',port);c.request(method,path,json.dumps(body) if body is not None else None,headers or {})
                r=c.getresponse();data=r.read();h=dict(r.getheaders());status=r.status;c.close();return status,data,h
            status,html,_=request('GET','/')
            self.assertEqual(status,200);self.assertIn(b'/styles.css',html);self.assertIn(b'/scenario.js',html)
            _,css,_=request('GET','/styles.css')
            from course_assistant.scenario_accounts import ROOT
            self.assertEqual(css,(ROOT/'web/styles.css').read_bytes())
            self.assertEqual(request('GET','/api/session',headers={'Host':'evil.test'})[0],403)
            self.assertEqual(request('GET','/api/school/profile')[0],404)
            self.assertEqual(request('POST','/api/login',{'username':'stats1','password':self.password})[0],403)
            headers={'Origin':f'http://127.0.0.1:{port}'}
            status,data,h=request('POST','/api/login',{'username':'stats1','password':self.password},headers)
            self.assertEqual(status,200);self.assertIn('HttpOnly',h['Set-Cookie'])
            headers['Cookie']=h['Set-Cookie'].split(';')[0]
            self.assertEqual(request('POST','/api/chat',{'message':'hi'},headers)[0],403)
            headers['X-CSRF-Token']=json.loads(data)['csrf']
            self.assertEqual(request('POST','/api/logout',{},headers)[0],200)
            self.assertEqual(request('POST','/api/chat',{'message':'hi'},headers)[0],401)
        finally:
            server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main()
