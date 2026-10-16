"""Loopback app with isolated synthetic workspaces and personal school clients."""
from __future__ import annotations
import argparse
import copy
import json
import mimetypes
import os
import re
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import __version__
from .auth import AccessError, AuthManager, COOKIE_NAME, PROFILES, SCHOOL_MODE
from .audit import audit_progress
from .exporter import export_result
from .importer import ROOT, SOURCE_ROOT, ImportErrorDetail, digest, load_json, read_sources, require, validate_demo
from .planner import generate_plans, revise_constraints, validate_plan
from .storage import Store
from .model_client import ModelClient, read_config, strict_json


class Application:
    def __init__(self, db_path=None, *, mock_enabled=False, clock=None, model=None, recommendation_demo=False, recommendation_case='default',
                 school_enabled=False, school_client_factory=None, school_root=None, personal_pdf_extractor=None):
        self.source = read_sources()
        self.school_enabled = bool(school_enabled or school_client_factory is not None)
        default_db = ROOT / ('private/school_app/planning.sqlite3' if self.school_enabled else 'runtime/planning.sqlite3')
        database = Path(db_path or default_db).expanduser().resolve()
        if self.school_enabled:
            resolved = database.resolve()
            if resolved.is_relative_to(ROOT) and not any(resolved.is_relative_to(ROOT / part) for part in ('private', 'local-only')):
                raise AccessError('学校账号运行库须位于 private、local-only 或项目外目录', 400, 'invalid_private_store')
            missing, parent = [], database.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
            os.chmod(database.parent, 0o700)
            if not database.exists():
                descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            os.chmod(database, 0o600)
        self.store = Store(database)
        options = {'clock': clock} if clock else {}
        self.auth = AuthManager(self.store, mock_enabled=mock_enabled, **options)
        self.school = None
        if self.school_enabled:
            from .school_accounts import SchoolAccountService
            if school_client_factory is None:
                from .school_http import SchoolHTTPClient
                school_client_factory = SchoolHTTPClient
            self.school = SchoolAccountService(self.auth, school_client_factory, root=school_root,
                                                personal_pdf_extractor=personal_pdf_extractor, **options)
        fixture_name = 'recommendation_bundle.json' if recommendation_demo else 'bundle.json'
        self.fixture = validate_demo(load_json(ROOT / 'demo' / fixture_name), self.source)
        require(recommendation_case in {'default', 'unknown', 'locked_friday'}, '未登记的合成推荐场景')
        require(recommendation_demo or recommendation_case == 'default', '推荐场景只适用于独立推荐夹具')
        if recommendation_case == 'unknown':
            self.fixture['academic_calendar'] = None
            self.fixture['offering_snapshot']['academic_calendar'] = None
            for section in self.fixture['offering_snapshot']['records']:
                for meeting in section.get('meetings', []):
                    meeting['week_numbers'] = None
        elif recommendation_case == 'locked_friday':
            for section in self.fixture['offering_snapshot']['records']:
                if section['section_id'] == 'DEMO-PE-01':
                    for meeting in section['meetings']:
                        meeting['day_of_week'] = 5
        if recommendation_demo:
            self.fixture['recommendation']['fixture_case'] = recommendation_case
        self.fixture = validate_demo(self.fixture, self.source)
        self.lock = threading.RLock()
        from .chat import ChatService
        self.chat = ChatService(self, model if model is not None else ModelClient())
        from .personal_chat import PersonalChatService
        self.personal_chat = PersonalChatService(self, self.chat.model)

    def auth_status(self, context=None):
        status = {'authenticated': False, 'mock_enabled': self.auth.mock_enabled,
                  'school_auth_status': 'not_configured', 'personal_data_source_status': 'not_connected',
                  'school_client_enabled': self.school_enabled,
                  'mock_profiles': [{'profile_id': key, **value} for key, value in PROFILES.items()] if self.auth.mock_enabled else []}
        if context:
            self.auth.require_context(context)
            status.update(authenticated=True, identity=context.identity(), csrf_token=self.auth.csrf(context))
            if context.auth_mode == SCHOOL_MODE:
                status['personal_data_source_status'] = self.school.status(context)['personal_data_source_status']
        return status

    def require_school(self):
        if self.school is None:
            raise AccessError('学校账号连接未开启', 503, 'school_auth_not_configured')
        return self.school

    def close(self):
        if self.school:
            self.school.close()

    def default_bundle(self, context):
        bundle = copy.deepcopy(self.fixture)
        old_owner = bundle['student']['student_key']
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return context.student_id if value == old_owner else value
        bundle = replace(bundle)
        # Synthetic profiles have independent snapshot identities, as well as SQL ownership.
        if context.principal_id == 'demo-b':
            def namespace(value):
                if isinstance(value, dict):
                    return {key: namespace(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [namespace(item) for item in value]
                if isinstance(value, str) and value.startswith(('DEMO-ACADEMIC-', 'DEMO-OFFERINGS-', 'DEMO-ENROLLMENT-')):
                    return 'DEMO-B-' + value[5:]
                return value
            bundle = namespace(bundle)
        return validate_demo(bundle, self.source)

    def workspace(self, context):
        self.auth.require_context(context)
        if context.auth_mode == SCHOOL_MODE:
            raise AccessError('本人教务数据尚未形成可执行推荐输入', 409, 'real_recommendation_not_ready')
        workspace = self.store.workspace(context)
        if workspace is None:
            bundle = self.default_bundle(context)
            self.store.set_workspace(context, bundle, bundle['constraints'])
            workspace = self.store.workspace(context)
        demo = workspace['demo']
        require(isinstance(demo, dict) and isinstance(demo.get('student'), dict), '活动输入结构无效')
        if demo['student'].get('student_key') != context.student_id:
            raise AccessError('资源不可访问', 404, 'nested_owner_mismatch')
        validate_demo(demo, self.source)
        require(workspace['dataset_sha256'] == digest(demo), '活动输入完整性校验失败')
        require(isinstance(workspace['constraints'], dict), '活动偏好结构无效')
        self.check_claims(workspace['constraints'], context)
        return workspace

    def versions(self, workspace):
        demo, constraints = workspace['demo'], workspace['constraints']
        return {'algorithm': __version__, 'dataset_sha256': digest(demo),
                'source_manifest_sha256': self.source['integrity']['manifest_sha256'],
                'plan_id': demo['student'].get('plan_id'), 'plan_version': demo['student'].get('plan_version'),
                'profile_version': demo['student'].get('profile_version'),
                'academic_snapshot': demo['academic_snapshot']['snapshot_id'],
                'offering_snapshot': demo['offering_snapshot']['snapshot_id'],
                'rule_version': sorted({r['version'] for r in demo['rules']}),
                'calendar_version': digest(demo.get('academic_calendar')),
                'constraints_revision': constraints.get('revision'), 'constraints_sha256': digest(constraints)}

    def check_claims(self, body, context):
        """Identity claims cannot alter scope; mismatches fail before any mutation."""
        if isinstance(body, dict):
            for key, value in body.items():
                normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
                if normalized in {'authmode', 'securityrealm', 'principalid', 'role', 'roles', 'rolegrants', 'authcontext', 'schoolauthenticated'}:
                    raise AccessError('不能由请求声明身份或权限', 403, 'client_authority_claim')
                if normalized in {'studentid', 'studentkey', 'ownerstudentid'} and value != context.student_id:
                    raise AccessError('资源不可访问', 404, 'owner_mismatch')
                if (normalized in {'studentnumber', 'verifiedstudentnumber', 'studentno', 'studentname', 'schoolaccount',
                                   'credential', 'credentials', 'passwordhash', 'authorization', 'session', 'sessionid', 'sessionref',
                                   'cookies', 'apikey', 'privatekey', 'schoolticket', 'authcode', 'secret',
                                   'filepath', 'localpath', 'privatepath', 'sourcepath'}
                        or normalized.endswith(('password', 'token', 'cookie', 'secret'))):
                    raise AccessError('该入口不接受身份凭据或自报学号', 400, 'credential_or_identifier_input')
                self.check_claims(value, context)
        elif isinstance(body, list):
            for value in body:
                self.check_claims(value, context)

    def audit(self, body, context=None):
        workspace = self.workspace(context)
        require(not any(key in body for key in ('student', 'records')), '核算只读取本人工作区，不接受身份或学业覆盖')
        if body.get('mode', 'demo') == 'source':
            return audit_progress({'plan_id': None, 'plan_binding_status': 'unknown'}, [], self.source['candidates'],
                                  self.source['courses'], record_completeness='unknown')
        demo = workspace['demo']
        return audit_progress(demo['student'], demo['academic_snapshot']['records'], demo['rules'], demo['courses'],
                              record_completeness=demo['academic_snapshot']['completeness'])

    def enrich_plan(self, plan, constraints, workspace):
        require(isinstance(plan, dict), '方案必须是 JSON 对象')
        demo = workspace['demo']
        lookup = {s['section_id']: s for s in demo['offering_snapshot']['records']}
        identifiers = plan.get('section_ids')
        require(isinstance(identifiers, list) and len(identifiers) <= 100 and all(isinstance(k, str) for k in identifiers), '方案班次不可访问')
        sections = [copy.deepcopy(lookup[k]) for k in identifiers if k in lookup]
        validation = validate_plan(identifiers, demo['offering_snapshot'], demo['student'], constraints,
                                   academic_records=demo['academic_snapshot']['records'])
        records = copy.deepcopy(demo['academic_snapshot']['records'])
        for section in sections:
            course = next(c for c in demo['courses'] if c['course_id'] == section['course_id'])
            records.append({'record_id': 'DEMO-PROJECTION-' + section['section_id'],
                            'attempt_id': 'DEMO-PROJECTION-' + section['section_id'],
                            'student_key': demo['student']['student_key'], 'plan_id': demo['student'].get('plan_id'),
                            'record_kind': 'course', 'course_id': section['course_id'], 'completion_status': 'passed',
                            'recognition_status': 'not_applicable', 'earned_credits': section.get('credits'),
                            'ledger': 'base', 'tags': course.get('tags', []), 'data_kind': 'synthetic',
                            'snapshot_id': 'DEMO-PROJECTION', 'source_refs': [{'snapshot_id': 'DEMO-PROJECTION'}]})
        projection = audit_progress(demo['student'], records, demo['rules'], demo['courses'],
                                    record_completeness=demo['academic_snapshot']['completeness'])
        # Ignore client-supplied validation, sections, provenance, and derived contributions.
        return {'section_ids': list(identifiers), 'sections': sections, 'validation': validation,
                'versions': self.versions(workspace), 'data_kind': 'synthetic', 'status': 'planning_draft',
                'plan_key': digest([identifiers, constraints, self.versions(workspace)]),
                'score': copy.deepcopy(validation.get('score', {})),
                'total_credits': validation.get('total_credits'),
                'expected_rule_contributions': projection['rules'],
                'projection_note': '预计贡献假设将来全部通过并获认定，不增加已获学分。'}

    def own_saved(self, context, identifier):
        if type(identifier) is not int or identifier <= 0:
            raise AccessError('资源不可访问', 404, 'inaccessible_object')
        saved = self.store.saved_one(context, identifier)
        if saved is None:
            raise AccessError('资源不可访问', 404, 'inaccessible_object')
        if saved.get('owner_student_id') != context.student_id or saved.get('security_realm') != context.security_realm:
            raise AccessError('资源不可访问', 404, 'nested_scope_mismatch')
        dataset = saved.get('frozen_dataset', {})
        if dataset.get('student', {}).get('student_key') != context.student_id:
            raise AccessError('资源不可访问', 404, 'nested_owner_mismatch')
        validate_demo(dataset, self.source)
        require(saved.get('versions', {}).get('dataset_sha256') == digest(dataset), '冻结输入完整性校验失败')
        return saved

    def frozen_result(self, body, context, workspace):
        if 'saved_id' in body:
            return self.own_saved(context, body['saved_id'])
        plan = body.get('plan', {})
        require(isinstance(plan, dict), '方案必须是 JSON 对象')
        require(plan.get('versions') == self.versions(workspace), '数据或偏好已更新，请重新校验后再保存/导出')
        constraints = workspace['constraints']
        if 'constraints' in body:
            require(body['constraints'] == constraints, '保存/导出须使用当前已校验条件')
        plan = self.enrich_plan(plan, constraints, workspace)
        return {'owner_student_id': context.student_id, 'security_realm': context.security_realm,
                'data_scope': 'synthetic', 'data_kind': 'synthetic', 'status': 'planning_draft',
                'not_official_enrollment': True, 'plan': plan, 'constraints': copy.deepcopy(constraints),
                'versions': self.versions(workspace), 'frozen_dataset': copy.deepcopy(workspace['demo'])}

    def get(self, path, context=None):
        parsed = urlparse(path)
        if parsed.path == '/api/health':
            require(not parsed.query, '接口不接受查询身份或资源范围')
            return {'status': 'ok', 'version': __version__, 'school_auth_status': 'not_configured',
                    'school_client_enabled': self.school_enabled}
        if context and context.auth_mode == SCHOOL_MODE:
            self.auth.require_context(context)
            require(not parsed.query or parsed.path in {'/api/chat/conversations', '/api/chat/runs'}, '接口不接受查询身份或资源范围')
            if path == '/api/bootstrap':
                return {'identity': context.identity(), 'school': self.require_school().status(context),
                        'source': {}, 'demo': {}, 'saved_plans': [], 'versions': {},
                        'chat': self.personal_chat.status(context)}
            if path == '/api/school/status':
                return self.require_school().status(context)
            if path == '/api/school/profile':
                return self.require_school().profile(context)
            if path == '/api/saved':
                return []
            if path == '/api/chat/status':
                return self.personal_chat.status(context)
            if path == '/api/chat/models':
                return self.personal_chat.models(context)
            if parsed.path == '/api/chat/runs':
                query = parse_qs(parsed.query, keep_blank_values=True)
                if (set(query) - {'limit'} or len(query.get('limit', ['20'])) != 1
                        or not re.fullmatch(r'(?:[1-9]|[1-4][0-9]|50)', query.get('limit', ['20'])[0])):
                    raise AccessError('运行列表只接受单个 1–50 的 limit 数量', 400, 'invalid_run_request')
                return self.personal_chat.recent_runs(context, limit=int(query.get('limit', ['20'])[0]))
            if parsed.path == '/api/chat/conversations':
                query = parse_qs(parsed.query, keep_blank_values=True)
                if (set(query) - {'limit'} or len(query.get('limit', ['50'])) != 1
                        or not re.fullmatch(r'[1-9][0-9]{0,2}', query.get('limit', ['50'])[0])):
                    raise AccessError('历史列表只接受单个 limit 数量', 400, 'invalid_chat_limit')
                return self.personal_chat.conversations(context, limit=int(query.get('limit', ['50'])[0]))
            raise AccessError('本人教务数据尚未形成可执行推荐输入', 409, 'real_recommendation_not_ready')
        if parsed.path == '/api/chat/conversations':
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (set(query) - {'limit'} or len(query.get('limit', ['50'])) != 1
                    or not re.fullmatch(r'[1-9][0-9]{0,2}', query.get('limit', ['50'])[0])):
                raise AccessError('历史列表只接受单个 limit 数量', 400, 'invalid_chat_limit')
            return self.chat.conversations(context, limit=int(query.get('limit', ['50'])[0]))
        require(not parsed.query, '接口不接受查询身份或资源范围')
        if path == '/api/chat/status':
            return self.chat.status(context)
        workspace = self.workspace(context)
        if path == '/api/bootstrap':
            demo = copy.deepcopy(workspace['demo'])
            demo['constraints'] = copy.deepcopy(workspace['constraints'])
            return {'source': self.source, 'demo': demo, 'saved_plans': [self.own_saved(context, row['saved_id']) for row in self.store.saved(context)],
                    'versions': self.versions(workspace), 'identity': context.identity()}
        if path == '/api/saved':
            return [self.own_saved(context, row['saved_id']) for row in self.store.saved(context)]
        raise AccessError('资源不可访问', 404, 'inaccessible_object')

    def post(self, path, body, context=None):
        if context and context.auth_mode == SCHOOL_MODE:
            self.auth.require_context(context)
            if path in {'/api/personal/records', '/api/evidence/prepare', '/api/evidence/search', '/api/evidence/resolve'}:
                return self.require_school().local_query(context, path, body)
            if path == '/api/chat/turn':
                return self.personal_chat.turn(body, context)
            if path == '/api/chat/history':
                return self.personal_chat.history(body, context)
            if path == '/api/chat/settings':
                return self.personal_chat.configure(body, context)
            if path == '/api/chat/models/refresh':
                require(body == {}, '模型目录刷新不接受额外参数')
                return self.personal_chat.models(context, refresh=True)
            if path == '/api/chat/run':
                return self.personal_chat.read_run(body, context)
            if path == '/api/chat/progress':
                return self.personal_chat.read_progress(body, context)
            if path == '/api/school/sync':
                return self.require_school().fetch(context, body)
            if path == '/api/school/captcha':
                return self.require_school().submit_captcha(context, body)
            if path == '/api/school/reauthenticate':
                return self.require_school().reauthenticate(context, body)
            if path == '/api/school/plan':
                require(set(body) <= {'force_refresh'} and type(body.get('force_refresh', False)) is bool, '方案请求格式无效')
                return self.require_school().acquire_plan(context, force=body.get('force_refresh', False))
            if path == '/api/school/plan-document':
                require(set(body) == {'source_alias'}, '仅接受本人方案引用')
                return self.require_school().read_plan_document(context, body['source_alias']), 'application/pdf', 'pdf'
            raise AccessError('本人教务数据尚未形成可执行推荐输入', 409, 'real_recommendation_not_ready')
        # The HTTP handler deliberately invokes these outside the global lock.
        # ChatService authorizes each short database/tool phase independently.
        if path == '/api/chat/turn':
            return self.chat.turn(body, context)
        if path == '/api/chat/history':
            return self.chat.history(body, context)
        if path in {'/api/chat/save-option', '/api/chat/export-option'}:
            from .recommendation_dialogue import option_action
            with self.lock:
                self.auth.require_context(context)
                self.check_claims(body, context)
                return option_action(self, body, context, export=path.endswith('export-option'))
        self.auth.require_context(context)
        self.check_claims(body, context)
        workspace = self.workspace(context)
        demo, old = workspace['demo'], workspace['constraints']
        if path == '/api/audit':
            return self.audit(body, context)
        if path in ('/api/import', '/api/reset-demo'):
            bundle = self.default_bundle(context) if path == '/api/reset-demo' else validate_demo(body.get('bundle'), self.source)
            require(bundle['student']['student_key'] == context.student_id, '数据必须属于当前 synthetic 学生')
            # Imports may update snapshots but cannot silently erase established locks.
            constraints = revise_constraints(old, bundle['constraints'])
            imported = self.store.set_workspace(context, bundle, constraints)
            workspace = {'demo': bundle, 'constraints': constraints}
            return {'result': 'pass', **imported, 'versions': self.versions(workspace),
                    'note': '仅更新本人 synthetic 工作区；锁定条件继承，历史冻结版本保留；重启恢复本人活动版本。'}
        if path in ('/api/generate', '/api/validate', '/api/revalidate'):
            if path == '/api/revalidate':
                saved = self.own_saved(context, body.get('saved_id'))
                # Keep current locks AND locks from the saved revision.
                inherited = {key: saved['constraints'].get(key, []) for key in ('locked_section_ids', 'preloaded_section_ids', 'fixed_meetings')}
                constraints = revise_constraints(old, inherited)
                workspace['constraints'] = constraints
                plan = self.enrich_plan(saved['plan'], constraints, workspace)
                plan['previous_saved_id'] = saved['saved_id']
                self.store.set_workspace(context, demo, constraints)
                return {'plan': plan, 'constraints': constraints, 'versions': self.versions(workspace),
                        'previous_versions': saved['versions'], 'data_kind': 'synthetic'}
            constraints = revise_constraints(old, body.get('constraints', {}))
            workspace['constraints'] = constraints
            if path == '/api/validate':
                result = validate_plan(body.get('section_ids', []), demo['offering_snapshot'], demo['student'], constraints,
                                       academic_records=demo['academic_snapshot']['records'])
                result['plan'] = self.enrich_plan({'section_ids': body.get('section_ids', [])}, constraints, workspace)
            else:
                result = generate_plans(demo['offering_snapshot'], demo['student'], constraints,
                                        academic_records=demo['academic_snapshot']['records'], max_nodes=10000, limit=3)
                for key in ('plans', 'pending_plans'):
                    result[key] = [self.enrich_plan(plan, constraints, workspace) for plan in result.get(key, [])]
            self.store.set_workspace(context, demo, constraints)
            return {**result, 'constraints': constraints, 'versions': self.versions(workspace), 'data_kind': 'synthetic'}
        if path == '/api/load':
            saved = self.own_saved(context, body.get('saved_id'))
            return {**saved, 'is_stale': saved['versions'] != self.versions(workspace),
                    'current_versions': self.versions(workspace)}
        if path == '/api/compare':
            from .planner import compare_plans
            if 'saved_ids' in body:
                require(isinstance(body['saved_ids'], list) and 2 <= len(body['saved_ids']) <= 3, '选择两至三个保存版本')
                saved = [self.own_saved(context, key) for key in body['saved_ids']]
                return {'mode': 'frozen', 'summaries': [{'saved_id': row['saved_id'], 'plan': row['plan'],
                         'constraints': row['constraints'], 'versions': row['versions']} for row in saved],
                        'different_input_versions': len({digest(row['versions']) for row in saved}) > 1,
                        'note': '分别使用各自冻结输入；不同版本不能视作同条件排名。'}
            plans = body.get('plans', [])
            require(isinstance(plans, list) and 2 <= len(plans) <= 3, '选择两至三个当前方案')
            for plan in plans:
                require(isinstance(plan, dict) and plan.get('versions') == self.versions(workspace), '比较前请按当前版本重新校验')
            comparison = compare_plans(plans, demo['offering_snapshot'], demo['student'], old,
                                       academic_records=demo['academic_snapshot']['records'])
            comparison['mode'] = 'current'
            return comparison
        if path == '/api/save':
            return self.store.save(context, self.frozen_result(body, context, workspace))
        if path == '/api/export':
            return export_result(self.frozen_result(body, context, workspace), body.get('format', 'json'))
        raise AccessError('资源不可访问', 404, 'inaccessible_object')


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, data, content_type, code=200, cookie=None, suffix=None, filename=None):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie is not None:
                self.send_header('Set-Cookie', cookie)
            if suffix:
                self.send_header('Content-Disposition', f'attachment; filename="{filename or "synthetic-plan." + suffix}"')
            self.end_headers()
            self.wfile.write(data)

        def response(self, value, code=200, cookie=None):
            self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                            'application/json; charset=utf-8', code, cookie)

        def token(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
            except Exception:
                return None
            item = cookie.get(COOKIE_NAME)
            return item.value if item else None

        def check_host(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                raise AccessError('不接受该来源', 403, 'invalid_host')

        def context(self):
            return app.auth.authenticate(self.token())

        def failure(self, error, action, context=None):
            if isinstance(error, AccessError):
                status, reason, message = error.status, error.reason, str(error)
            else:
                status, reason, message = 400, 'invalid_request', str(error) if isinstance(error, ValueError) else '请求内容无效'
            app.store.event(context.principal_id if context else None, action, 'deny', reason)
            self.response({'error': message, 'code': reason}, status)

        def do_GET(self):
            context = None
            try:
                self.check_host()
                parsed = urlparse(self.path)
                path = parsed.path
                require(not parsed.query or path in {'/api/chat/conversations', '/api/chat/runs'}, '接口不接受查询身份或资源范围')
                if path in {'/api/school/profile', '/api/school/captcha'}:
                    context = self.context()
                    service = app.require_school()
                    if context.auth_mode != SCHOOL_MODE:
                        raise AccessError('请先连接学校账号', 403, 'school_connection_required')
                    if path == '/api/school/captcha':
                        result = service.captcha(context)
                        app.auth.require_context(context)
                        self.send_bytes(result['image'], result['content_type'])
                    else:
                        result = service.profile(context)
                        app.auth.require_context(context)
                        self.response(result)
                    return
                if path in {'/api/bootstrap', '/api/chat/status', '/api/chat/conversations', '/api/chat/models', '/api/chat/runs'}:
                    context = self.context()
                    if context.auth_mode == SCHOOL_MODE:
                        result = app.get(self.path if parsed.query else path, context)
                        app.auth.require_context(context)
                        self.response(result)
                        return
                with app.lock:
                    if path == '/api/auth/session':
                        try:
                            context = self.context()
                        except AccessError:
                            context = None
                        self.response(app.auth_status(context))
                        return
                    if path.startswith('/api/'):
                        context = None if path == '/api/health' else self.context()
                        self.response(app.get(self.path if path == '/api/chat/conversations' else path, context))
                        return
                    if path.startswith('/sources/'):
                        context = self.context()
                        filename = path.rsplit('/', 1)[1]
                        allowed = {r['bundled_path'].rsplit('/', 1)[1] for r in app.source['source_aliases']}
                        if filename not in allowed:
                            raise AccessError('资源不可访问', 404, 'inaccessible_object')
                        file = SOURCE_ROOT / 'sources' / filename
                    else:
                        filename = 'index.html' if path == '/' else path.lstrip('/')
                        if filename not in {'index.html', 'app.js', 'styles.css'}:
                            raise AccessError('资源不可访问', 404, 'inaccessible_object')
                        file = ROOT / 'web' / filename
                    self.send_bytes(file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream')
            except (ValueError, OSError, TypeError) as error:
                self.failure(error, 'get', context)

        def do_POST(self):
            context = None
            try:
                self.check_host()
                parsed = urlparse(self.path)
                require(not parsed.query, '接口不接受查询身份或资源范围')
                path = parsed.path
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + self.headers.get('Host', '') or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise AccessError('不接受跨来源写请求', 403, 'cross_origin')
                require(self.headers.get('Content-Type', '').split(';', 1)[0] == 'application/json', '请使用 application/json')
                length = int(self.headers.get('Content-Length', 0))
                require(0 < length <= 2_000_000, '请求大小需在 1 字节至 2 MB 内')
                body = strict_json(self.rfile.read(length))
                require(isinstance(body, dict), '请求必须是 JSON 对象')
                if path == '/api/auth/school-login':
                    token, result = app.require_school().login(body, self.token())
                    cookie = f'{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict' if token else None
                    self.response(result, cookie=cookie)
                    return
                if path.startswith('/api/school/'):
                    context = self.context()
                    if context.auth_mode != SCHOOL_MODE:
                        raise AccessError('请先连接学校账号', 403, 'school_connection_required')
                    app.auth.check_csrf(context, self.headers.get('X-CSRF-Token'))
                    result = app.post(path, body, context)
                    app.auth.require_context(context)
                    if path == '/api/school/plan-document':
                        data, content_type, suffix = result
                        self.send_bytes(data, content_type, suffix=suffix, filename='cultivation-plan.pdf')
                    else:
                        self.response(result)
                    return
                if path in {'/api/personal/records', '/api/evidence/prepare', '/api/evidence/search', '/api/evidence/resolve'}:
                    context = self.context()
                    if context.auth_mode != SCHOOL_MODE:
                        raise AccessError('请先连接学校账号', 403, 'school_connection_required')
                    app.auth.check_csrf(context, self.headers.get('X-CSRF-Token'))
                    result = app.post(path, body, context)
                    app.auth.require_context(context)
                    self.response(result)
                    return
                with app.lock:
                    if path == '/api/auth/mock-login':
                        token, result = app.auth.login_mock(body, self.token())
                        if app.school:
                            app.school.discard_revoked_sessions()
                        self.response(result, cookie=f'{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict')
                        return
                    context = self.context()
                    app.auth.check_csrf(context, self.headers.get('X-CSRF-Token'))
                    if path == '/api/auth/logout':
                        if context.auth_mode == SCHOOL_MODE:
                            app.require_school().logout(context)
                        else:
                            app.auth.logout(context)
                        self.response({'authenticated': False}, cookie=f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict')
                        return
                    if not path.startswith('/api/chat/'):
                        result = app.post(path, body, context)
                        app.store.event(context.principal_id, path, 'allow', 'owner_scoped')
                if path.startswith('/api/chat/'):
                    result = app.post(path, body, context)
                    with app.lock:
                        app.auth.require_context(context)
                        app.store.event(context.principal_id, path, 'allow', 'owner_scoped')
                if path in {'/api/export', '/api/chat/export-option'}:
                    data, content_type, suffix = result
                    self.send_bytes(data, content_type, suffix=suffix)
                else:
                    self.response(result)
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                self.failure(error, 'post', context)

        def log_message(self, fmt, *args):
            # Do not write raw URLs, identifiers, cookies, or request bodies to launch output.
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description='浙大教务助手；可连接本人学校账号查询教务数据')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, help='本机运行数据库，不提交 Git')
    parser.add_argument('--enable-mock', action='store_true', help='显式启用本机 synthetic 身份，非学校登录')
    parser.add_argument('--enable-school', action='store_true', help='启用本机学校客户端；凭据仅临时交给学校验证，不落盘')
    parser.add_argument('--recommendation-demo', action='store_true', help='使用独立固定 synthetic 推荐夹具')
    parser.add_argument('--recommendation-case', choices=['default', 'unknown', 'locked_friday'], default='default', help='固定合成验证场景')
    parser.add_argument('--model-env-file', type=Path, default=ROOT / '.env',
                        help='本机模型配置文件（默认 .env），只读取约定配置，不执行文件内容')
    args = parser.parse_args()
    app = Application(args.db, mock_enabled=args.enable_mock, school_enabled=args.enable_school,
                      recommendation_demo=args.recommendation_demo, recommendation_case=args.recommendation_case,
                      model=ModelClient(read_config(args.model_env_file)))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    print(f'浙大教务助手 http://127.0.0.1:{server.server_port} · mock {"开启（synthetic）" if args.enable_mock else "关闭"} · 学校账号连接 {"开启" if args.enable_school else "关闭"}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
