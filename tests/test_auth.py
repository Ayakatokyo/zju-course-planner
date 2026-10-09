import copy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from course_assistant.auth import AccessError, COOKIE_NAME
from course_assistant.importer import digest, ImportErrorDetail
from course_assistant.server import Application, make_handler


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'auth.sqlite3'
        self.now = [1000.0]
        self.app = Application(self.path, mock_enabled=True, clock=lambda: self.now[0])
        self.token_a, _ = self.app.auth.login_mock({'profile_id': 'demo-a'})
        self.token_b, _ = self.app.auth.login_mock({'profile_id': 'demo-b'})
        self.a, self.b = self.app.auth.authenticate(self.token_a), self.app.auth.authenticate(self.token_b)

    def plan(self, context):
        result = self.app.post('/api/generate', {}, context)
        return self.app.post('/api/save', {'plan': result['plans'][0], 'constraints': result['constraints']}, context)

    def test_default_mock_disabled_and_no_arbitrary_student_login(self):
        app = Application(Path(self.temp.name) / 'disabled.sqlite3')
        self.assertFalse(app.auth_status()['mock_enabled'])
        with self.assertRaises(AccessError):
            app.auth.login_mock({'profile_id': 'demo-a'})
        for body in ({'student_number': '123'}, {'profile_id': 'demo-a', 'password': 'fixture-secret'},
                     {'profile_id': 'unknown'}, {'profile_id': 'demo-a', 'auth_mode': 'school_official'}):
            with self.assertRaises(AccessError):
                self.app.auth.login_mock(body)

    def test_sensitive_apis_require_live_server_context(self):
        for path in ('/api/bootstrap', '/api/saved'):
            with self.assertRaises(AccessError):
                self.app.get(path)
        with self.assertRaises(AccessError):
            self.app.post('/api/generate', {})
        with self.assertRaises(AccessError):
            self.app.auth.authenticate('forged')
        with self.assertRaises(AccessError):
            self.app.get('/api/bootstrap', replace(self.a, student_id=self.b.student_id))
        with self.assertRaises(AccessError):
            self.app.get('/api/bootstrap', replace(self.a, auth_mode='school_official'))

    def test_session_rotation_logout_and_expiry(self):
        new_token, _ = self.app.auth.login_mock({'profile_id': 'demo-a'}, self.token_a)
        self.assertNotEqual(new_token, self.token_a)
        with self.assertRaises(AccessError):
            self.app.auth.authenticate(self.token_a)
        new_context = self.app.auth.authenticate(new_token)
        self.app.auth.logout(new_context)
        with self.assertRaises(AccessError):
            self.app.get('/api/bootstrap', new_context)
        self.now[0] += 1801
        with self.assertRaises(AccessError):
            self.app.auth.authenticate(self.token_b)

    def test_absolute_expiry_even_with_activity(self):
        for _ in range(15):
            self.now[0] += 1799
            self.app.auth.authenticate(self.token_a)
        self.now[0] = 1000 + 28800
        with self.assertRaises(AccessError):
            self.app.auth.authenticate(self.token_a)

    def test_binding_revoke_conflict_version_change_rejects_old_session(self):
        for status, version in (('revoked', 'DEMO-BINDING-v1'), ('conflicting', 'DEMO-BINDING-v1'), ('verified', 'v2')):
            with self.app.store.connect() as db:
                db.execute('UPDATE identity_bindings SET status=?,version=? WHERE principal=?', (status, version, 'demo-a'))
            with self.assertRaises(AccessError):
                self.app.get('/api/bootstrap', self.a)

    def test_identity_claims_rejected_before_mutation(self):
        before = self.app.workspace(self.a)
        for body in ({'student_id': self.b.student_id}, {'student_number': '123'}, {'auth_mode': 'school_official'},
                     {'constraints': {'owner_student_id': self.b.student_id}}, {'role': 'admin'}):
            with self.assertRaises(AccessError):
                self.app.post('/api/generate', body, self.a)
        self.assertEqual(self.app.workspace(self.a), before)
        with self.assertRaises(ImportErrorDetail):
            self.app.post('/api/audit', {'mode': 'source', 'student': {'student_key': self.a.student_id}, 'records': []}, self.a)

    def test_nested_camelcase_credentials_never_enter_workspace_or_export(self):
        before = self.app.workspace(self.a)
        for key in ('schoolPassword', 'Access_Token', 'Cookie', 'sessionRef', 'studentNumber', 'privatePath', 'apiKey', 'cookies', 'secret', 'clientSecret'):
            with self.subTest(key=key), self.assertRaises(AccessError):
                self.app.post('/api/generate', {'constraints': {key: 'fixture-secret'}}, self.a)
        self.assertEqual(self.app.workspace(self.a), before)

    def test_active_workspace_checks_nested_owner_scope_and_integrity(self):
        original = self.app.workspace(self.a)['demo']
        for mutation in ('owner', 'scope', 'digest'):
            changed = copy.deepcopy(original)
            if mutation == 'owner':
                changed['student']['student_key'] = self.b.student_id
            elif mutation == 'scope':
                changed['metadata']['data_kind'] = 'runtime_observation'
            else:
                changed['metadata']['version'] += '-corrupted'
            with self.app.store.connect() as db:
                db.execute('UPDATE owned_datasets SET body=? WHERE owner=?', (json.dumps(changed), self.a.student_id))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.app.get('/api/bootstrap', self.a)
            with self.app.store.connect() as db:
                db.execute('UPDATE owned_datasets SET body=? WHERE owner=?', (json.dumps(original), self.a.student_id))

    def test_guessed_object_ids_read_export_save_revalidate_compare_same_response(self):
        saved_b = self.plan(self.b)
        for key in (saved_b['saved_id'], 999999):
            for path in ('/api/load', '/api/save', '/api/export', '/api/revalidate'):
                with self.assertRaises(AccessError) as raised:
                    self.app.post(path, {'saved_id': key}, self.a)
                self.assertEqual((raised.exception.status, str(raised.exception)), (404, '资源不可访问'))
            with self.assertRaises(AccessError):
                self.app.post('/api/compare', {'saved_ids': [key, key]}, self.a)
        self.assertEqual(self.app.get('/api/saved', self.a), [])

    def test_nested_frozen_owner_checked_even_if_outer_owner_matches(self):
        saved = self.plan(self.a)
        with self.app.store.connect() as db:
            row = db.execute('SELECT body FROM owned_plans WHERE id=?', (saved['saved_id'],)).fetchone()
            value = json.loads(row[0]); value['frozen_dataset']['student']['student_key'] = self.b.student_id
            db.execute('UPDATE owned_plans SET body=? WHERE id=?', (json.dumps(value), saved['saved_id']))
        with self.assertRaises(AccessError):
            self.app.post('/api/export', {'saved_id': saved['saved_id']}, self.a)

    def test_import_requires_current_synthetic_owner_and_rejects_real_mode(self):
        before = self.app.workspace(self.a)
        other = self.app.workspace(self.b)['demo']
        with self.assertRaises(AccessError):
            self.app.post('/api/import', {'bundle': other}, self.a)
        wrong = copy.deepcopy(before['demo']); wrong['metadata']['data_kind'] = 'runtime_observation'
        with self.assertRaises(ImportErrorDetail):
            self.app.post('/api/import', {'bundle': wrong}, self.a)
        self.assertEqual(self.app.workspace(self.a), before)

    def test_concurrent_workspaces_versions_and_saved_results_are_isolated(self):
        def work(context, friday):
            bundle = self.app.workspace(context)['demo']
            bundle['metadata']['version'] += '-updated-' + context.principal_id
            with self.app.lock:
                self.app.post('/api/import', {'bundle': bundle}, context)
                generated = self.app.post('/api/generate', {'constraints': {'friday_free': friday}}, context)
                saved = self.app.post('/api/save', {'plan': generated['plans'][0], 'constraints': generated['constraints']}, context)
                exported = self.app.post('/api/export', {'saved_id': saved['saved_id']}, context)
            return json.loads(exported[0])
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(work, self.a, 'soft'), pool.submit(work, self.b, 'off')]
            results = [future.result() for future in futures]
        self.assertEqual([row['owner_student_id'] for row in results], [self.a.student_id, self.b.student_id])
        self.assertEqual([row['constraints']['friday_free'] for row in results], ['soft', 'off'])
        for context in (self.a, self.b):
            saved = self.app.get('/api/saved', context)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]['frozen_dataset']['student']['student_key'], context.student_id)

    def test_active_workspace_survives_restart_and_legacy_stays_quarantined(self):
        bundle = self.app.workspace(self.a)['demo']; bundle['metadata']['version'] = 'DEMO-PERSIST-v2'
        self.app.post('/api/import', {'bundle': bundle}, self.a)
        with self.app.store.connect() as db:
            db.execute('CREATE TABLE saved_plans(id INTEGER PRIMARY KEY,body TEXT,created_at TEXT)')
            db.execute('INSERT INTO saved_plans VALUES(1,?,?)', ('{"legacy_private":true}', 'old'))
        restarted = Application(self.path, mock_enabled=True, clock=lambda: self.now[0])
        context = restarted.auth.authenticate(self.token_a)
        self.assertEqual(restarted.workspace(context)['demo']['metadata']['version'], 'DEMO-PERSIST-v2')
        self.assertEqual(restarted.get('/api/saved', context), [])
        with restarted.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM saved_plans').fetchone()[0], 1)

    def test_history_revalidation_keeps_current_hard_preferences_and_all_locks(self):
        saved = self.plan(self.a)
        self.app.post('/api/generate', {'constraints': {'locked_section_ids': ['DEMO-ML-B'], 'friday_free': 'hard'}}, self.a)
        result = self.app.post('/api/revalidate', {'saved_id': saved['saved_id']}, self.a)
        self.assertEqual(result['constraints']['friday_free'], 'hard')
        self.assertIn('DEMO-ML-B', result['constraints']['locked_section_ids'])
        self.assertIn('DEMO-PE-A', result['constraints']['locked_section_ids'])
        self.assertEqual(result['plan']['validation']['result'], 'fail')
        history = self.app.post('/api/load', {'saved_id': saved['saved_id']}, self.a)
        self.assertEqual(history['constraints']['friday_free'], 'soft')
        self.assertTrue(history['is_stale'])

    def test_stale_preference_revision_cannot_overwrite_newer_conditions(self):
        revision = self.app.workspace(self.a)['constraints']['revision']
        result = self.app.post('/api/generate', {'constraints': {'friday_free': 'hard', 'expected_revision': revision}}, self.a)
        before = self.app.workspace(self.a)
        with self.assertRaises(ValueError):
            self.app.post('/api/generate', {'constraints': {'friday_free': 'off', 'expected_revision': revision}}, self.a)
        self.assertEqual(self.app.workspace(self.a), before)
        self.assertEqual(result['constraints']['friday_free'], 'hard')

    def test_no_session_credentials_in_business_export_or_security_log(self):
        saved = self.plan(self.a)
        exported = self.app.post('/api/export', {'saved_id': saved['saved_id']}, self.a)[0].decode()
        self.assertNotIn(self.token_a, exported)
        self.assertNotIn(self.app.auth.csrf(self.a), exported)
        with self.app.store.connect() as db:
            logs = json.dumps(db.execute('SELECT * FROM security_events').fetchall())
            stored = db.execute('SELECT token_hash FROM login_sessions').fetchall()
        self.assertNotIn(self.token_a, logs)
        self.assertNotIn(self.token_a, str(stored))


class HTTPAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.app = Application(Path(self.temp.name) / 'http.sqlite3', mock_enabled=True)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(conn.close)
        full = {'Content-Type': 'application/json', **(headers or {})}
        conn.request(method, path, json.dumps(body) if body is not None else None, full)
        response = conn.getresponse(); raw = response.read()
        return response.status, dict(response.getheaders()), json.loads(raw) if raw else None

    def login(self):
        code, headers, value = self.request('POST', '/api/auth/mock-login', {'profile_id': 'demo-a'})
        self.assertEqual(code, 200)
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        return {'Cookie': cookie.split(';')[0], 'X-CSRF-Token': value['csrf_token']}

    def test_public_minimal_health_and_anonymous_personal_denial(self):
        self.assertEqual(self.request('GET', '/api/health')[0], 200)
        for path in ('/api/bootstrap', '/api/saved', '/sources/materials_2026.pdf'):
            self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(self.request('POST', '/api/generate', {})[0], 401)

    def test_csrf_origin_host_logout_and_refresh(self):
        headers = self.login()
        self.assertEqual(self.request('GET', '/api/bootstrap', headers=headers)[0], 200)
        refreshed = self.request('GET', '/api/auth/session', headers=headers)[2]
        self.assertEqual(refreshed['csrf_token'], headers['X-CSRF-Token'])
        before = digest(self.app.workspace(self.app.auth.authenticate(headers['Cookie'].split('=',1)[1])))
        bad = {'Cookie': headers['Cookie']}
        self.assertEqual(self.request('POST', '/api/generate', {}, bad)[0], 403)
        self.assertEqual(self.request('POST', '/api/generate', {}, {**headers, 'Origin': 'https://attacker.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/api/bootstrap', headers={**headers, 'Host': 'attacker.invalid'})[0], 403)
        self.assertEqual(digest(self.app.workspace(self.app.auth.authenticate(headers['Cookie'].split('=',1)[1]))), before)
        self.assertEqual(self.request('POST', '/api/auth/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('GET', '/api/bootstrap', headers=headers)[0], 401)

    def test_school_unconfigured_query_scope_and_private_source_denied(self):
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/auth/school-login', {}, headers)[0], 503)
        value = self.request('GET', '/api/auth/session', headers=headers)[2]
        self.assertEqual(value['identity']['auth_mode'], 'mock')
        self.assertEqual(value['personal_data_source_status'], 'not_connected')
        self.assertEqual(self.request('GET', '/api/bootstrap?student_id=DEMO-STUDENT-002', headers=headers)[0], 400)
        self.assertEqual(self.request('GET', '/sources/EXCEL-REFERENCE-001', headers=headers)[0], 404)
        self.assertEqual(self.request('POST', '/api/public-rule-write', {}, headers)[0], 404)
        self.assertEqual(self.request('GET', '/private/anything', headers=headers)[0], 404)

    def test_null_plan_and_calendar_return_errors_without_disconnect_or_save(self):
        headers = self.login()
        for path in ('/api/save', '/api/export'):
            code, _, value = self.request('POST', path, {'plan': None}, headers)
            self.assertEqual(code, 400)
            self.assertIn('JSON 对象', value['error'])
        self.assertEqual(self.request('GET', '/api/saved', headers=headers)[2], [])
        context = self.app.auth.authenticate(headers['Cookie'].split('=', 1)[1])
        bundle = self.app.workspace(context)['demo']
        bundle['academic_calendar'] = None
        bundle['offering_snapshot']['academic_calendar'] = None
        self.assertEqual(self.request('POST', '/api/import', {'bundle': bundle}, headers)[0], 200)
        validated = self.request('POST', '/api/validate', {'section_ids': ['DEMO-PE-A']}, headers)[2]
        code, _, value = self.request('POST', '/api/export', {'plan': validated['plan'], 'format': 'ics'}, headers)
        self.assertEqual(code, 400)
        self.assertIn('校历', value['error'])
        self.assertEqual(self.request('POST', '/api/export', {'plan': validated['plan'], 'format': 'json'}, headers)[0], 200)
