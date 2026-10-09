"""Loopback-only synthetic app; every personal request uses a live server context."""
from __future__ import annotations
import argparse
import copy
import json
import mimetypes
import re
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .auth import AccessError, AuthManager, COOKIE_NAME, PROFILES
from .audit import audit_progress
from .exporter import export_result
from .importer import ROOT, SOURCE_ROOT, ImportErrorDetail, digest, load_json, read_sources, require, validate_demo
from .planner import generate_plans, revise_constraints, validate_plan
from .storage import Store


class Application:
    def __init__(self, db_path=None, *, mock_enabled=False, clock=None):
        self.source = read_sources()
        self.store = Store(db_path or ROOT / 'runtime' / 'planning.sqlite3')
        options = {'clock': clock} if clock else {}
        self.auth = AuthManager(self.store, mock_enabled=mock_enabled, **options)
        self.fixture = validate_demo(load_json(ROOT / 'demo' / 'bundle.json'), self.source)
        self.lock = threading.RLock()

    def auth_status(self, context=None):
        status = {'authenticated': False, 'mock_enabled': self.auth.mock_enabled,
                  'school_auth_status': 'not_configured', 'personal_data_source_status': 'not_connected',
                  'mock_profiles': [{'profile_id': key, **value} for key, value in PROFILES.items()] if self.auth.mock_enabled else []}
        if context:
            self.auth.require_context(context)
            status.update(authenticated=True, identity=context.identity(), csrf_token=self.auth.csrf(context))
        return status

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
        if path == '/api/health':
            return {'status': 'ok', 'version': __version__, 'school_auth_status': 'not_configured'}
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
        def send_bytes(self, data, content_type, code=200, cookie=None, suffix=None):
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
                self.send_header('Content-Disposition', f'attachment; filename="synthetic-plan.{suffix}"')
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
                require(not parsed.query, '接口不接受查询身份或资源范围')
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
                        self.response(app.get(path, context))
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
                def reject_constant(value):
                    raise ImportErrorDetail('JSON 数值必须有限')
                body = json.loads(self.rfile.read(length), parse_constant=reject_constant)
                require(isinstance(body, dict), '请求必须是 JSON 对象')
                with app.lock:
                    if path == '/api/auth/mock-login':
                        token, result = app.auth.login_mock(body, self.token())
                        self.response(result, cookie=f'{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict')
                        return
                    if path == '/api/auth/school-login':
                        raise AccessError('学校认证协议和应用接入尚未配置', 503, 'school_auth_not_configured')
                    context = self.context()
                    app.auth.check_csrf(context, self.headers.get('X-CSRF-Token'))
                    if path == '/api/auth/logout':
                        app.auth.logout(context)
                        self.response({'authenticated': False}, cookie=f'{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict')
                        return
                    result = app.post(path, body, context)
                    app.store.event(context.principal_id, path, 'allow', 'owner_scoped')
                if path == '/api/export':
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
    parser = argparse.ArgumentParser(description='本地选课规划；学校认证和真实数据源未接入')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, help='本机运行数据库，不提交 Git')
    parser.add_argument('--enable-mock', action='store_true', help='显式启用本机 synthetic 身份，非学校登录')
    args = parser.parse_args()
    app = Application(args.db, mock_enabled=args.enable_mock)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    print(f'选课助手 http://127.0.0.1:{server.server_port} · mock {"开启（synthetic）" if args.enable_mock else "关闭"} · 学校认证未配置', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
