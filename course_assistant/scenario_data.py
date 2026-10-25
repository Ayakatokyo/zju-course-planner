"""Authenticated synthetic data provider for the normal academic application.

No separate chat loop or web app: fixtures enter the same observation query and
PDF evidence services as school data. No method opens a school connection.
"""
from copy import deepcopy
from hashlib import pbkdf2_hmac, sha256
from pathlib import Path
import hmac
import json
import re
import threading
import time

from .auth import AccessError, SCENARIO_MODE, SCENARIO_REALM
from .personal_evidence import PersonalEvidenceService
from .personal_observations import PersonalObservationStore
from .scenario_accounts import load_bundle, digest


class ScenarioDataService:
    def __init__(self, auth, bundle, accounts, pdfs, root, *, clock=time.time, extractor=None):
        self.auth, self.clock, self.extractor = auth, clock, extractor
        self.students, self.plans = load_bundle(Path(bundle))
        self.accounts = json.loads(Path(accounts).read_text())
        self.pdfs = {key: Path(value).expanduser().resolve() for key, value in pdfs.items()}
        self.root = Path(root).resolve()
        self.sessions, self.services, self.attempts = {}, {}, {}
        self.lock = threading.RLock()
        for account in self.accounts.values():
            if account.get('student_id') not in self.students:
                raise ValueError('unknown synthetic account binding')
        for key, plan in self.plans.items():
            if key not in self.pdfs or sha256(self.pdfs[key].read_bytes()).hexdigest() != plan['pdf_sha256']:
                raise ValueError('synthetic curriculum PDF must match the reviewed source')
        self.version = digest({'students': self.students, 'plans': self.plans})
        auth.scenario_session_validator = self.session_valid

    def handles_username(self, username):
        # Reserved test names never fall through to school authentication.
        return isinstance(username, str) and (username in self.accounts or bool(re.fullmatch(r'(?:business|stats)\d+', username)))

    def session_valid(self, session_ref, owner):
        with self.lock:
            return self.sessions.get(session_ref) == owner

    def login(self, body, previous_token=None):
        if (set(body) != {'username', 'password'} or not isinstance(body['username'], str)
                or not isinstance(body['password'], str) or not 0 < len(body['password']) <= 256):
            raise AccessError('请输入账号和密码', 400, 'invalid_login')
        username, password = body['username'], body['password']
        with self.lock:
            now = self.clock()
            attempts = [t for t in self.attempts.get(username, []) if now - t < 60]
            if len(attempts) >= 8:
                raise AccessError('登录尝试过于频繁，请稍后再试', 429, 'login_rate_limited')
            attempts.append(now)
            self.attempts[username] = attempts
        account = self.accounts.get(username)
        salt = bytes.fromhex(account['salt']) if account else bytes(16)
        candidate = pbkdf2_hmac('sha256', password.encode(), salt, 210000).hex()
        if account is None or not hmac.compare_digest(candidate, account['password_hash']):
            raise AccessError('账号或密码不正确', 401, 'invalid_credentials')
        student = self.students[account['student_id']]
        def attach(context):
            with self.lock:
                self.sessions[context.session_ref] = student['student_id']
        token, result = self.auth.login_scenario(student['student_id'], student['display_name'], self.version,
            previous_token, attach=attach)
        result['profile'] = self.profile(self.auth.authenticate(token))['profile']
        return token, result

    def _authorize(self, context):
        self.auth.require_context(context)
        if (context.auth_mode, context.security_realm) != (SCENARIO_MODE, SCENARIO_REALM):
            raise AccessError('账号不可访问该来源', 403, 'permission_denied')
        return {'scope_key': digest([context.session_ref, context.student_id, self.version]),
                'owner_student_id': context.student_id, 'security_realm': SCENARIO_REALM}

    def personal_chat_identity_scope(self, context):
        return self._authorize(context)

    def personal_chat_scope(self, context):
        return {**self._authorize(context), 'versions': {'fixture': self.version}}

    def profile(self, context):
        self._authorize(context)
        s = self.students[context.student_id]
        return {'status': 'complete_within_scope', 'data_kind': 'synthetic',
            'data_source': 'synthetic_fixture', 'captured_at': self.clock(), 'profile': {
            'name': s['display_name'], 'cohort': s['cohort'], 'major_name': self.plans[s['plan_key']]['program_name'],
            'field_states': {'cohort': 'known', 'major_name': 'known', 'name': 'known'}}}

    def status(self, context):
        self._authorize(context)
        s = self.students[context.student_id]
        return {'status': 'ready', 'state': 'ready', 'data_kind': 'synthetic',
            'personal_data_source_status': 'synthetic_fixture', 'school_auth_status': 'not_school_authenticated',
            'query_context': {'cohort': s['cohort'], 'cohort_state': 'known',
                'available_academic_years': sorted({r['academic_year'] for r in s['academic_records']})}}

    def local_chat_status(self, context):
        self._authorize(context)
        return {'local': {'available': True}, 'data_kind': 'synthetic', 'capabilities': {
            'personal_record_query': True, 'curriculum_available': True, 'curriculum_local_search': True,
            'course_catalog_search': False, 'course_data_test': False, 'real_recommendation': False}}

    def _services(self, context):
        scope = self._authorize(context)
        with self.lock:
            if context.session_ref in self.services:
                return self.services[context.session_ref]
            student = self.students[context.student_id]
            plan = self.plans[student['plan_key']]
            authorize = lambda: self._authorize(context)
            observations = PersonalObservationStore(self.root / 'observations', authorize, data_kind='synthetic')
            rows, groups = [], {}
            for r in student['academic_records']:
                row = {'course_code_raw': r['course_code'], 'course_name_raw': r['course_name'],
                    'grade_raw': r['grade'], 'grade_points_raw': None, 'nominal_credits': r['credits'],
                    'academic_year_raw': r['academic_year'], 'term_raw': ''.join(r['assigned_term']['seasons']),
                    'completion_status_raw': '模拟已通过；未作学校认定'}
                row['field_states'] = {k: 'known' if v is not None else 'unknown' for k, v in row.items()}
                rows.append(row)
                seasons = set(r['assigned_term']['seasons'])
                # Short-term/ambiguous season rows stay in all-history only.
                semester = 'AW' if seasons and seasons <= {'秋', '冬'} else 'SS' if seasons and seasons <= {'春', '夏'} else None
                if semester:
                    groups.setdefault(('academic', r['academic_year'] + '-' + semester), []).append(row)
            groups[('major_academic', 'ALL-HISTORY')] = rows
            snapshots, results = {}, {}
            for key, records in groups.items():
                result = {'status': 'complete_within_scope', 'data_kind': 'synthetic', 'security_realm': SCENARIO_REALM,
                    'operation': key[0], 'target_term': key[1], 'data_source': 'synthetic_fixture',
                    'records': records, 'coverage': {'captured_rows': len(records), 'reported_total': len(records),
                    'complete_within_scope': True, 'has_more': False}, 'issues': [], 'row_failures': [],
                    'source_alias': 'SOURCE-' + self.version[:24], 'captured_at': self.clock(), 'checked_at': self.clock()}
                snapshots[key] = observations.register(result, parser_version='synthetic-source-v1', mapping_version='fixture-v1')['observation_id']
                results[key] = result
            path = self.pdfs[student['plan_key']]
            alias = 'PLAN-' + plan['pdf_sha256'][:24]
            def describe():
                authorize()
                if sha256(path.read_bytes()).hexdigest() != plan['pdf_sha256']:
                    raise AccessError('培养来源版本已变化', 409, 'personal_input_changed')
                return {'source_alias': alias, 'sha256': plan['pdf_sha256']}
            evidence = PersonalEvidenceService(self.root / 'evidence', authorize, lambda _: path.read_bytes(), describe, self.extractor, cohort=student['cohort'])
            value = {'observations': observations, 'snapshots': snapshots, 'results': results,
                     'evidence': evidence, 'alias': alias, 'path': path}
            self.services[context.session_ref] = value
            return value

    def prepare_curriculum(self, context):
        self._authorize(context)
        services = self._services(context)
        services['evidence'].prepare(services['alias'])
        return services['evidence'].prepare_vectors()

    def personal_chat_tool(self, context, name, args):
        self._authorize(context)
        if name == 'get_my_context':
            services = self._services(context)
            return {'status': 'ok', 'data_kind': 'synthetic', **self.profile(context)['profile'],
                'operations': [{'operation': op, 'target_term': term} for op, term in services['snapshots']],
                'capabilities': self.local_chat_status(context)['capabilities']}
        services = self._services(context)
        if name == 'query_my_records':
            if set(args) - {'operation', 'target_term', 'query', 'academic_year', 'limit', 'mode', 'cursor'}:
                raise AccessError('查询参数无效', 400, 'invalid_request')
            key = (args['operation'], args.get('target_term', 'ALL-HISTORY'))
            if key not in services['snapshots']:
                return {'status': 'not_ready', 'data_kind': 'synthetic', 'operation': args['operation'],
                        'reason': 'no_observation_in_scope', 'records': [], 'matched_records': None}
            query = {k: v for k, v in args.items() if k in {'operation', 'query', 'academic_year', 'limit', 'mode', 'cursor'}}
            query['observation_id'] = services['snapshots'][key]
            return services['observations'].query(query)
        if name == 'search_my_curriculum':
            services['evidence'].prepare(services['alias'])
            return {**services['evidence'].search(**args), 'data_kind': 'synthetic',
                    'source_content_kind': 'source_document'}
        raise AccessError('当前数据源未提供该工具所需资料', 409, 'tool_not_available')

    def fetch(self, context, body):
        if set(body) - {'operation', 'academic_year', 'season', 'target_term', 'force_refresh'}:
            raise AccessError('查询参数无效', 400, 'invalid_request')
        operation = body.get('operation')
        term = 'ALL-HISTORY' if operation == 'major_academic' else body.get('target_term', str(body.get('academic_year', '')) + '-' + str(body.get('season', '')))
        services = self._services(context)
        if (operation, term) not in services['results']:
            return {'status': 'not_ready', 'data_kind': 'synthetic', 'operation': operation,
                    'target_term': term, 'records': [], 'coverage': {'complete_within_scope': False}, 'reason': 'no_observation_in_scope'}
        return deepcopy(services['results'][(operation, term)])

    def acquire_plan(self, context, force=False):
        services = self._services(context)
        return {**services['evidence'].prepare(services['alias']), 'status': 'acquired_pending_review', 'data_kind': 'synthetic',
                'source_alias': services['alias'], 'binding_status': 'synthetic_fixture', 'rules_status': 'not_reviewed'}

    def read_plan_document(self, context, alias):
        services = self._services(context)
        if alias != services['alias']:
            raise AccessError('来源不可访问', 403, 'permission_denied')
        services['evidence'].describe()
        return services['path'].read_bytes()

    def local_query(self, context, path, body):
        if path == '/api/personal/records':
            return self.personal_chat_tool(context, 'query_my_records', body)
        services = self._services(context)
        if path == '/api/evidence/preview' and set(body) == {'reference', 'source_alias'}:
            return services['evidence'].preview(body['reference'], body['source_alias'])
        services['evidence'].prepare(services['alias'])
        if path == '/api/evidence/search':
            return services['evidence'].search(**body)
        if path == '/api/evidence/resolve' and set(body) == {'reference'}:
            return services['evidence'].resolve(body['reference'])
        if path == '/api/evidence/prepare' and set(body) == {'source_alias'} and body.get('source_alias') == services['alias']:
            return services['evidence'].prepare(services['alias'])
        raise AccessError('查询参数无效', 400, 'invalid_request')

    def close(self):
        with self.lock:
            self.sessions.clear()
            self.services.clear()

    def logout(self, context):
        self._authorize(context)
        self.auth.logout(context)
        with self.lock:
            self.sessions.pop(context.session_ref, None)
            self.services.pop(context.session_ref, None)

    def captcha(self, context):
        self._authorize(context)
        raise AccessError('该账号不使用学校验证码', 409, 'not_applicable')

    def submit_captcha(self, context, body):
        return self.captcha(context)

    def reauthenticate(self, context, body):
        self._authorize(context)
        raise AccessError('请退出后通过统一入口重新登录', 409, 'not_applicable')
