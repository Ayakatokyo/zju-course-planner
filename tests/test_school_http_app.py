"""Real loopback app HTTP against an explicitly synthetic school transport."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from course_assistant.server import Application, make_handler
from course_assistant.student_sources import business_field_diagnostics


class SyntheticSchool:
    def __init__(self):
        self.session_epoch = 0
        self.owner = 'SYNTHETIC-SCHOOL-A'
        self.closed = False
        self.calls = []
        self.started, self.release = threading.Event(), threading.Event()
        self.block = False

    def login(self, username, password):
        self.owner = 'SYNTHETIC-SCHOOL-B' if username == 'SYNTHETIC-LOGIN-B' else 'SYNTHETIC-SCHOOL-A'
        self.session_epoch += 1
        return self.get_profile()

    def get_profile(self):
        return {'state': 'authenticated', 'profile': {'source_subject_id': self.owner,
                'name': '合成学校测试用户', 'admission_date_raw': '20230901',
                'college_name': '合成学院', 'major_name': '合成专业'}}

    def request(self, spec):
        self.calls.append(spec)
        self.started.set()
        if self.block:
            self.release.wait(3)
        if spec['operation'] == 'plan_document':
            return {'state': 'business', 'http_status': 200, 'content_type': 'application/pdf',
                    'session_epoch': self.session_epoch, 'source_subject_id': self.owner,
                    'payload': b'%PDF-1.4\n' + b'SYNTHETIC-PLAN-CONTENT\n' * 3 + b'%%EOF\n'}
        if spec['operation'] == 'plan_catalogue':
            return {'state': 'business', 'http_status': 200, 'content_type': 'application/json',
                    'session_epoch': self.session_epoch, 'source_subject_id': self.owner,
                    'payload': {'items': [{'plan_ref': 'SYNTHETIC-PLAN', **spec['scope'],
                                          'cohort_provenance': 'source_row'}], 'total': 1}}
        payload = {'kcmc': '合成接口课程', 'kch': 'SYNTHETIC-COURSE-1', 'xf': '1.5', 'cj': '90'}
        container = 'kbList' if spec['operation'] == 'timetable' else 'items'
        document = {container: [payload], 'total': 1, 'pageNo': 1}
        return {'state': 'business', 'http_status': 200, 'content_type': 'application/json',
                'session_epoch': self.session_epoch, 'source_subject_id': self.owner,
                'payload': document,
                'source_field_diagnostics': business_field_diagnostics(
                    'academic' if spec['operation'] == 'major_academic' else spec['operation'], document, document)}

    def close(self):
        self.closed = True
        self.session_epoch += 1


class SchoolAppHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clients = []
        def factory():
            client = SyntheticSchool()
            self.clients.append(client)
            return client
        self.app = Application(Path(self.temp.name).resolve() / 'app.sqlite3', mock_enabled=True,
                               school_client_factory=factory)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = None, None

    def tearDown(self):
        for client in self.clients:
            client.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, *, cookie=None, csrf=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if cookie or self.cookie:
            headers['Cookie'] = cookie or self.cookie
        if csrf and self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        if origin:
            headers['Origin'] = origin
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        raw, fields = response.read(), dict(response.getheaders())
        connection.close()
        result = json.loads(raw) if fields['Content-Type'].startswith('application/json') else raw
        return response.status, result, fields

    def login(self, username='SYNTHETIC-LOGIN-A'):
        status, result, fields = self.request('POST', '/api/auth/school-login',
            {'username': username, 'password': 'SYNTHETIC-TRANSIENT-SECRET'})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['authenticated'])
        self.cookie = fields['Set-Cookie'].split(';', 1)[0]
        self.csrf = result['csrf_token']
        return result

    def test_school_login_uses_returned_subject_and_bootstrap_has_no_mock_workspace(self):
        result = self.login()
        self.assertEqual(result['identity']['student_id'], 'SYNTHETIC-SCHOOL-A')
        self.assertEqual(result['identity']['auth_mode'], 'school_client')
        self.assertEqual(result['identity']['data_kind'], 'personal_source')
        self.assertNotIn('SYNTHETIC-LOGIN-A', str(result))
        self.app.default_bundle = lambda context: self.fail('school must not create mock bundle')
        status, boot, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(boot['demo'], {})
        self.assertEqual(boot['source'], {})
        self.assertFalse(boot['chat']['available'])
        self.assertEqual(boot['school']['query_context'], {'cohort': 2023, 'cohort_state': 'known'})
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0], 0)

    def test_real_routes_authorize_and_require_csrf_before_transport(self):
        self.login()
        body = {'operation': 'academic', 'target_term': '2026-2027-AW', 'force_refresh': True}
        self.assertEqual(self.request('POST', '/api/school/sync', body, csrf=False)[0], 403)
        self.assertEqual(self.clients[-1].calls, [])
        status, result, _ = self.request('POST', '/api/school/sync', body)
        self.assertEqual(status, 200)
        self.assertEqual(result['source_field_diagnostics'][0]['source']['row_count'], 1)
        self.assertEqual(result['source_field_diagnostics'][0]['domain'], 'academic')
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['data_source'], 'live')
        self.assertEqual(result['data_kind'], 'personal_source')
        self.assertEqual(self.request('POST', '/api/school/sync', {**body, 'student_id': 'SYNTHETIC-OTHER'})[0], 400)
        self.assertEqual(self.request('GET', '/api/school/profile?student_id=SYNTHETIC-OTHER')[0], 400)

    def test_mock_cannot_query_school_or_create_transport(self):
        status, body, headers = self.request('POST', '/api/auth/mock-login', {'profile_id': 'demo-a'})
        self.assertEqual(status, 200)
        self.cookie, self.csrf = headers['Set-Cookie'].split(';', 1)[0], body['csrf_token']
        self.assertEqual(self.request('GET', '/api/school/profile')[0], 403)
        self.assertEqual(self.request('POST', '/api/school/sync', {'operation': 'academic', 'target_term': '2026-2027-AW'})[0], 403)
        self.assertEqual(self.clients, [])

    def test_major_history_scope_survives_http_service_and_transport_contract(self):
        self.login()
        body = {'operation': 'major_academic', 'target_term': 'ALL-HISTORY', 'force_refresh': True}
        status, result, _ = self.request('POST', '/api/school/sync', body)
        self.assertEqual(status, 200, result)
        self.assertEqual((result['operation'], result['target_term'], result['data_source']),
                         ('major_academic', 'ALL-HISTORY', 'live'))
        self.assertEqual(self.clients[-1].calls[-1]['target_term'], 'ALL-HISTORY')
        self.assertEqual(self.request('POST', '/api/school/sync',
                                     {**body, 'target_term': '2026-2027-AW'})[0], 400)

    def test_plan_uses_authenticated_profile_and_pdf_is_owner_scoped(self):
        self.login()
        status, plan, _ = self.request('POST', '/api/school/plan', {})
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan['status'], 'acquired_pending_review')
        alias = plan['source_alias']
        catalogue = next(call for call in self.clients[-1].calls if call['operation'] == 'plan_catalogue')
        self.assertEqual(catalogue['scope']['cohort'], 2023)
        self.assertEqual(catalogue['scope']['major_name'], '合成专业')
        status, pdf, headers = self.request('POST', '/api/school/plan-document', {'source_alias': alias})
        self.assertEqual(status, 200)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.login('SYNTHETIC-LOGIN-B')
        self.assertEqual(self.request('POST', '/api/school/plan-document', {'source_alias': alias})[0], 404)

    def test_school_login_rejects_cross_origin_before_credentials_reach_driver(self):
        status, _, _ = self.request('POST', '/api/auth/school-login',
                                  {'username': 'SYNTHETIC-LOGIN-A', 'password': 'SYNTHETIC-TRANSIENT-SECRET'},
                                  origin='https://synthetic-untrusted.invalid')
        self.assertEqual(status, 403)
        self.assertEqual(self.clients, [])

    def test_mock_switch_and_logout_destroy_school_session(self):
        self.login()
        old_cookie, old_client = self.cookie, self.clients[-1]
        status, _, _ = self.request('POST', '/api/auth/mock-login', {'profile_id': 'demo-a'})
        self.assertEqual(status, 200)
        self.assertTrue(old_client.closed)
        self.assertEqual(self.request('GET', '/api/school/profile', cookie=old_cookie)[0], 401)
        self.login('SYNTHETIC-LOGIN-B')
        old_cookie, old_client = self.cookie, self.clients[-1]
        self.assertEqual(self.request('POST', '/api/auth/logout', {})[0], 200)
        self.assertTrue(old_client.closed)
        self.assertEqual(self.request('GET', '/api/school/profile', cookie=old_cookie)[0], 401)

    def test_school_data_cannot_enter_synthetic_model_or_recommendation(self):
        self.login()
        self.app.chat.turn = lambda *args: self.fail('personal data cannot call remote model')
        for path, body in [('/api/chat/turn', {'message': 'SYNTHETIC-PRIVATE-TEXT'}),
                           ('/api/import', {'bundle': {}}), ('/api/generate', {}), ('/api/save', {})]:
            self.assertEqual(self.request('POST', path, body)[0], 409)

    def test_logout_not_blocked_by_query_and_late_data_is_denied(self):
        self.login()
        client = self.clients[-1]
        client.block = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            query = pool.submit(self.request, 'POST', '/api/school/sync',
                                {'operation': 'academic', 'target_term': '2026-2027-AW'})
            self.assertTrue(client.started.wait(1))
            start = time.monotonic()
            self.assertEqual(self.request('POST', '/api/auth/logout', {})[0], 200)
            self.assertLess(time.monotonic() - start, 0.5)
            client.release.set()
            status, result, _ = query.result(timeout=2)
        self.assertEqual(status, 401)
        self.assertNotIn('records', result)

    def test_credentials_absent_from_app_response_database_and_private_business_files(self):
        result = self.login()
        self.assertNotIn('SYNTHETIC-TRANSIENT-SECRET', str(result))
        self.request('POST', '/api/school/sync', {'operation': 'academic', 'target_term': '2026-2027-AW'})
        for file in Path(self.temp.name).rglob('*'):
            if file.is_file():
                self.assertNotIn(b'SYNTHETIC-TRANSIENT-SECRET', file.read_bytes())


if __name__ == '__main__':
    unittest.main()
