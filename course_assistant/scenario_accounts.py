"""Loopback-only curriculum scenarios: separate credentials, sessions and data.

Reuses the production dialogue protocol and AcademicHarness, with an offline
adapter. Never constructs a school client or opens the school account store.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import pbkdf2_hmac, sha256
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import hmac
import json
import os
import re
import secrets
import threading
import time

from .academic_harness import AcademicHarness
from .auth import AccessError
from .model_client import ModelClient, read_config
from .personal_dialogue import PersonalDialogueService
from .personal_observations import source_grade_summary, validate_grade_filter, matches_grade_filter, MAX_RECORD_TABLE_ROWS
from .semantic_retrieval import LocalSemanticRetriever
from .reference_retrieval import terms
from scripts.build_curriculum_scenarios import validate_student

ROOT = Path(__file__).resolve().parents[1]
COOKIE = 'curriculum_scenario_session'
REALM = 'curriculum_synthetic_accounts'


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, 'w', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        handle.write(value)


def load_bundle(path):
    raw = path.read_bytes()
    manifest = json.loads((path.parent / 'manifest.json').read_text())
    if sha256(raw).hexdigest() != manifest['files'][path.name]:
        raise ValueError('scenario bundle integrity mismatch')
    bundle = json.loads(raw)
    if bundle.get('schema_version') != 'curriculum-scenarios-v1':
        raise ValueError('unsupported scenario schema')
    plans = {p['plan_key']: p for p in bundle['plans']}
    students = {}
    for student in bundle['students']:
        validate_student(student, plans[student['plan_key']])
        if student['student_id'] in students or not student['student_id'].startswith('SYN-'):
            raise ValueError('invalid synthetic identity')
        students[student['student_id']] = student
    return students, plans


def initialize_accounts(path, students):
    if path.exists():
        raise ValueError('account file exists; refusing overwrite')
    password = 'Sim-' + secrets.token_urlsafe(9)
    accounts, lines = {}, ['仅用于本机模拟学生，绝非学校账号。', '共同测试密码：' + password, '']
    for student in students.values():
        username = ('business' if student['plan_key'] == 'BUSINESS' else 'stats') + str(student['study_year'])
        salt = secrets.token_bytes(16)
        accounts[username] = {'student_id': student['student_id'], 'salt': salt.hex(),
                             'password_hash': pbkdf2_hmac('sha256', password.encode(), salt, 210000).hex()}
        lines.append(username + ' — ' + student['display_name'])
    private_write(path, json.dumps(accounts, ensure_ascii=False, indent=2))
    private_write(path.with_name('测试账号.txt'), '\n'.join(lines) + '\n')


class ScenarioAdapter:
    def __init__(self, student, plan):
        self.student, self.plan = deepcopy(student), deepcopy(plan)
        self.version = digest({'student': student, 'plan': plan})
        self.chunks = self._chunks()
        self.retriever = LocalSemanticRetriever.from_environment()

    def _chunks(self):
        s, p = self.student, self.plan
        chunks = []
        def add(title, text, page=None, **metadata):
            chunks.append({'title': title, 'text': text, 'page': page,
                'source_alias': 'SCENARIO-' + p['pdf_sha256'][:16],
                'locator': {'chunk': len(chunks), 'student_version': self.version},
                'binding_status': 'synthetic_fixture', 'review_status': 'source_bound',
                'rules_status': 'partial_review', 'content_kind': 'scenario_derived', **metadata})
        add('模拟阶段概况 学分合计 年级 培养方案',
            f"{s['display_name']}；2026级；{s['checkpoint']}。本账号固定绑定{p['program_name']}培养方案。"
            f"模拟已完成{s['summary']['completed_records']}条，课程名义学分合计{s['summary']['completed_nominal_credits']}；"
            f"下学期春夏计划{s['summary']['next_term_courses']}门，名义学分{s['summary']['next_term_nominal_credits']}。"
            f"最低毕业学分原文{p['minimum_graduation_credits_raw']}。这些是部分明确课程轨迹，非学校认证/学分认定；"
            "未填体育/外语/通识等，不能据此计算完整毕业缺口、绩点或判断毕业。成绩65—95为合成值。")
        add('课表 排课 周五 空闲 冲突 考试 教学班 开课资料缺项',
            '本模拟场景只有培养课程及模拟成绩，没有教学班、星期、节次、周次、已有课表或考试时间。'
            '无法核算周五空闲和时间冲突；需要目标学期的教学班及完整时段、已有课表、相应考试安排。'
            '培养方案建议学期不是实际开课证明，不得补造排课结果。')
        labels = {'planned_next_term': '下学期 春夏 建议课程 计划未完成', 'future_courses': '后续年级 未来课程 未完成',
                  'academic_records': '模拟已完成课程', 'unselected_courses': '选修池 未选择课程'}
        for bucket, label in labels.items():
            for offset in range(0, len(s[bucket]), 4):
                rows = s[bucket][offset:offset + 4]
                text = '；'.join(f"{r['course_code']} {r['course_name']} {r['credits']}学分，{r['module']}，培养建议{r['suggested_term_raw']}，PDF第{r['source']['physical_page']}页" for r in rows)
                add(label, label + '（模拟取舍，不保证实际开课或可选）：' + text, rows[0]['source']['physical_page'],
                    content_kind='source_course_rows_with_scenario_selection',
                    course_names=[r['course_name'] for r in rows], course_codes=[r['course_code'] for r in rows])
        for m in s['named_module_progress']:
            add('模块进度 ' + m['name'], f"{m['name']}模块最低{m['minimum_credits']}学分；"
                f"模拟已完成名义学分{m['simulated_completed_nominal_credits']}；"
                f"该列名模块最低差额{m['remaining_named_module_minimum']}。仅模块数字差额，非毕业审核，不跨模块抵扣。", m['source_page'])
        for u in s['unresolved_requirements']:
            add('未确认要求 ' + u['name'], u['name'] + '：' + u['reason'], u['source_page'] if type(u['source_page']) is int else None)
        for assertion in p.get('source_assertions', []):
            add('培养方案原文', assertion['quote'], assertion['page'], content_kind='source_quote')
        # Full parsed module pool, kept separate from simulated choices/stages.
        for module in p['modules']:
            rows = [r for r in p['courses'] if r['module'] == module['name']]
            for start in range(0, len(rows), 4):
                group = rows[start:start + 4]
                text = '；'.join(f"{r['course_code']} {r['course_name']} {r['credits']}学分，培养建议{r['suggested_term_raw']}，PDF第{r['source']['physical_page']}页" for r in group)
                add(module['name'], text, group[0]['source']['physical_page'],
                    listing_module=module['name'], module_path=[module['name']],
                    course_row_count=len(group), content_kind='source_course_rows')
        return chunks

    def personal_chat_tool(self, context, name, args):
        if context != self.student['student_id']:
            raise AccessError('账号与学生绑定不一致', 403, 'permission_denied')
        base = {'status': 'available', 'data_kind': 'synthetic'}
        if name == 'get_my_context':
            s = self.student
            return {**base, 'profile': {'cohort': s['cohort'], 'major_name': s['display_name']},
                'operations': [{'operation': 'major_academic', 'target_term': 'ALL-HISTORY'}],
                'api_operations': [], 'capabilities': {'personal_record_query': True,
                'curriculum_available': True, 'curriculum_local_search': True, 'live_record_query': False,
                'course_catalog_search': False, 'course_data_test': False}}
        if name == 'query_my_records':
            predicate = validate_grade_filter(args['grade_filter'], args.get('operation')) if 'grade_filter' in args else None
            if args.get('operation') != 'major_academic':
                return {**base, 'status': 'not_ready', 'reason': 'no_observation_in_scope', 'records': []}
            records = []
            for index, r in enumerate(self.student['academic_records']):
                term = ''.join(r['assigned_term']['seasons'])
                row = {'course_code_raw': r['course_code'], 'course_name_raw': r['course_name'],
                    'grade_raw': r['grade'], 'grade_points_raw': None, 'nominal_credits': r['credits'],
                    'academic_year_raw': r['academic_year'], 'term_raw': term,
                    'completion_status_raw': '模拟已通过；未作学校认定'}
                row['field_states'] = {key: 'known' if value is not None else 'unknown' for key, value in row.items()}
                query = args.get('query', '').strip().casefold()
                if query and query not in (r['course_code'] + r['course_name']).casefold():
                    continue
                if args.get('academic_year') and args['academic_year'] != r['academic_year']:
                    continue
                if not matches_grade_filter(row, predicate):
                    continue
                records.append((index, row))
            offset, limit = int(args.get('cursor', 0)), args.get('limit', 15)
            if not 0 <= offset <= len(records) or not 1 <= limit <= 15:
                raise ValueError('invalid record cursor')
            mode = args.get('mode', 'records')
            if mode not in {'records', 'summary', 'table'} or mode == 'table' and 'cursor' in args:
                raise ValueError('invalid record mode')
            page_size = MAX_RECORD_TABLE_ROWS if mode == 'table' else limit
            selected = [] if mode == 'summary' else records[offset:offset + page_size]
            total = len(self.student['academic_records'])
            coverage = {'complete_within_scope': True, 'captured_rows': total, 'reported_total': total}
            return {**base, 'operation': 'major_academic', 'target_term': 'ALL-HISTORY',
                'records': [r for _, r in selected], 'source_record_indices': [i for i, _ in selected],
                'matched_records': len(records), 'total_records': total, 'returned_records': len(selected),
                'offset': offset, 'mode': args.get('mode', 'records'), 'coverage': coverage,
                **({'grade_filter': predicate} if predicate is not None else {}),
                'observation_id': self.version, 'source_alias': 'SYNTHETIC-ACADEMIC',
                'source_grade_summary': source_grade_summary([r for _, r in records], coverage),
                'next_cursor': str(offset + limit) if mode == 'records' and selected and offset + limit < len(records) else None}
        if name == 'search_my_curriculum':
            query = args['query'].strip().casefold()
            mode, offset, limit = args.get('mode', 'search'), args.get('offset', 0), args.get('limit', 5)
            if not isinstance(mode, str) or mode not in {'search', 'module_courses', 'module_context'} or type(offset) is not int or not 0 <= offset <= 2000 or type(limit) is not int or not 1 <= limit <= 5 or mode == 'search' and offset:
                raise ValueError('invalid curriculum query')
            if mode == 'module_context':
                # This mock fixture has reviewed excerpts, not the full PDF
                # dependency graph. Do not claim a complete context listing.
                return {**base, 'status': 'not_ready', 'matches': [],
                        'listing': {'status': 'full_context_unavailable_in_fixture', 'complete': False}}
            if mode == 'module_courses':
                modules = {c['listing_module'] for c in self.chunks if c.get('listing_module') and re.sub(r'\s+', '', c['listing_module']) in re.sub(r'\s+', '', query)}
                modules = {m for m in modules if not any(m != other and m in other for other in modules)}
                if len(modules) != 1:
                    names = sorted(modules or {c['listing_module'] for c in self.chunks if c.get('listing_module')})
                    return {**base, 'status': 'needs_clarification', 'matches': [],
                        'module_choices': [{'module_path': [m], 'request': {'query': m, 'mode': mode, 'offset': 0, 'limit': limit}} for m in names[:20]],
                        'module_choices_has_more': len(names) > 20,
                        'listing': {'status': 'named_unique_module_required', 'complete': False}}
                pool = [c for c in self.chunks if c.get('listing_module') in modules]
                if offset > len(pool):
                    raise ValueError('invalid curriculum offset')
                from .curriculum_facts import summarize_course_rows
                rows = [r for r in self.plan['courses'] if r['module'] in modules]
                return {**base, 'matches': deepcopy(pool[offset:offset + limit]), 'retrieval_mode': 'module_course_listing',
                    'course_summary': summarize_course_rows(rows),
                    'listing': {'status': 'parsed_module_rows', 'total_groups': len(pool), 'returned_groups': len(pool[offset:offset+limit]),
                    'total_course_rows': sum(c['course_row_count'] for c in pool),
                    'returned_course_rows': sum(c['course_row_count'] for c in pool[offset:offset+limit]),
                    'offset': offset, 'next_offset': offset + limit if offset + limit < len(pool) else None,
                    'has_more': offset + limit < len(pool), 'complete': offset == 0 and len(pool) <= limit},
                    'scope': {'binding_status': 'synthetic_fixture', 'rules_status': 'partial_review'}}
            query_terms = terms(query)
            def score(chunk):
                text = (chunk['title'] + chunk['text']).casefold()
                return len(query_terms & terms(text))
            candidates = [{'id': str(i), 'text': c['title'] + '\n' + c['text'], 'lexical_score': score(c)}
                          for i, c in enumerate(self.chunks) if not c.get('listing_module')]
            retrieval = self.retriever.rank(query, candidates, limit)
            selected = [int(r['id']) for r in retrieval['matches']]
            pinned = [i for i, c in enumerate(self.chunks) if any(n.casefold() in query for n in c.get('course_names', []))
                      or any(code.casefold() in query_terms for code in c.get('course_codes', []))]
            selected = (pinned + [i for i in selected if i not in pinned])[:limit]
            matches = [deepcopy(self.chunks[i]) for i in selected]
            return {**base, 'matches': matches, 'retrieval_mode': retrieval['retrieval_mode'],
                    'semantic_retrieval': {k: retrieval[k] for k in ('status', 'reason', 'dense_used', 'candidate_count', 'encoded_count', 'ranking_version')},
                    'scope': {'binding_status': 'synthetic_fixture', 'rules_status': 'partial_review'}}
        raise AccessError('未开放该模拟工具', 403, 'tool_not_allowed')


class ObservedFlash:
    def __init__(self, client, records):
        self.client, self.records = client, records
    def status(self):
        return self.client.status()
    def complete(self, messages, tools):
        result, metadata = self.client.complete_with_metadata(messages, tools)
        self.records.append({**{k: metadata[k] for k in ('status', 'http_status', 'model', 'usage', 'elapsed_ms') if k in metadata},
                             'response': deepcopy(result)})
        return result


class ScenarioApplication:
    def __init__(self, bundle, accounts_path, model=None, clock=time.time):
        self.students, self.plans = load_bundle(Path(bundle))
        self.accounts = json.loads(Path(accounts_path).read_text())
        for account in self.accounts.values():
            if account['student_id'] not in self.students:
                raise ValueError('unknown account binding')
        self.adapters = {key: ScenarioAdapter(student, self.plans[student['plan_key']]) for key, student in self.students.items()}
        self.model = model or ModelClient(read_config(ROOT / ".env")).with_options('deepseek-flash', True)
        self.clock, self.lock, self.sessions, self.attempts = clock, threading.RLock(), {}, []
        self.audit_root = Path(accounts_path).parent / 'runs'

    def authenticate(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if not session or self.clock() >= min(session['expires'], session['idle']):
                self.sessions.pop(token, None)
                raise AccessError('请登录模拟账号', 401, 'unauthenticated')
            session['idle'] = min(session['expires'], self.clock() + 1800)
            return session

    def login(self, body, previous=None):
        if set(body) != {'username', 'password'} or not all(isinstance(v, str) and 0 < len(v) <= 150 for v in body.values()):
            raise AccessError('请填写测试账号与密码', 400, 'invalid_login')
        with self.lock:
            now = self.clock()
            self.attempts = [t for t in self.attempts if now - t < 60]
            if len(self.attempts) >= 20:
                raise AccessError('尝试较多，请稍后重试', 429, 'rate_limited')
            self.attempts.append(now)
            account = self.accounts.get(body['username'])
            salt = bytes.fromhex(account['salt']) if account else b'0' * 16
            hashed = pbkdf2_hmac('sha256', body['password'].encode(), salt, 210000).hex()
            if not account or not hmac.compare_digest(hashed, account['password_hash']):
                raise AccessError('测试账号或密码不正确', 401, 'invalid_credentials')
            self.sessions.pop(previous, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {'student_id': account['student_id'], 'csrf': secrets.token_urlsafe(24),
                'expires': now + 28800, 'idle': now + 1800, 'turns': [], 'thinking': True, 'lock': threading.Lock()}
            return token

    def view(self, token):
        session = self.authenticate(token)
        s = self.students[session['student_id']]
        return {'authenticated': True, 'data_kind': 'synthetic', 'security_realm': REALM,
                'student': {k: s[k] for k in ('student_id', 'display_name', 'cohort', 'study_year', 'checkpoint', 'summary')},
                'csrf': session['csrf'], 'turns': deepcopy(session['turns']), 'model': {**self.model.status(), 'thinking': session['thinking']}}

    def chat(self, token, body):
        if set(body) != {'message'} or not isinstance(body['message'], str) or not 0 < len(body['message'].strip()) <= 2000:
            raise AccessError('消息需为1至2000字', 400, 'invalid_message')
        session = self.authenticate(token)
        if not session['lock'].acquire(blocking=False):
            raise AccessError('当前回答尚未结束', 409, 'busy')
        try:
            self.authenticate(token)
            if len(session['turns']) >= 20:
                raise AccessError('本段对话已达20轮，请新建对话', 409, 'turn_limit')
            adapter = self.adapters[session['student_id']]
            def authorize():
                current = self.authenticate(token)
                if current is not session:
                    raise AccessError('会话已变化', 403, 'permission_denied')
                return {'scope_key': digest([REALM, token, session['student_id'], adapter.version])}
            events, calls = [], []
            def emit(kind, fields):
                events.append({'kind': kind, **deepcopy(fields)})
            harness = AcademicHarness(adapter, session['student_id'], authorize=authorize,
                generation_authorize=authorize, adopt=lambda *a: None, emit=emit, user_message=body['message'])
            client = self.model.with_options('deepseek-flash', session['thinking'])
            model = ObservedFlash(client, calls)
            service = PersonalDialogueService(model, authorize, harness.execute)
            history = [row for turn in session['turns'][-6:] for row in (
                {'role': 'user', 'content': turn['message']}, {'role': 'assistant', 'content': turn['reply']})]
            result, _ = service.run(body['message'], data_use='synthetic_scenario', history=history, emit=emit)
            authorize()
            turn = {'message': body['message'], 'reply': result['reply'], 'status': result['status'],
                    'model_used': result['model_used'], 'model': 'deepseek-flash', 'code': result.get('code'),
                    'records': [c['record'] for c in result.get('citations', []) if c.get('kind') == 'record']}
            session['turns'].append(turn)
            run_id = secrets.token_hex(16)
            private_write(self.audit_root / (run_id + '.json'), json.dumps({'student_id': session['student_id'],
                'message': body['message'], 'result': result, 'events': events, 'model_calls': calls}, ensure_ascii=False, indent=2))
            return {**turn, 'data_kind': 'synthetic', 'run_id': run_id}
        finally:
            session['lock'].release()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, value, code=200, cookie=None, mime=None):
            raw = value if mime else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            for key, item in {'Content-Type': mime or 'application/json; charset=utf-8', 'Content-Length': str(len(raw)),
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
                self.send_header(key, item)
            if cookie is not None:
                self.send_header('Set-Cookie', f'{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            self.wfile.write(raw)
        def check_host(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                raise AccessError('不接受该来源', 403, 'invalid_host')
        def token(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                return cookie[COOKIE].value if COOKIE in cookie else None
            except Exception:
                return None
        def failure(self, error):
            self.respond({'error': str(error) if isinstance(error, AccessError) else '请求未完成，请重试',
                          'code': getattr(error, 'reason', 'invalid_request')}, getattr(error, 'status', 400))
        def do_GET(self):
            try:
                self.check_host()
                if self.path == '/api/session':
                    try:
                        self.respond(app.view(self.token()))
                    except AccessError:
                        self.respond({'authenticated': False})
                    return
                files = {'/': ('index.html', 'text/html; charset=utf-8'),
                         '/scenario.js': ('scenario.js', 'text/javascript; charset=utf-8'),
                         '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
                if self.path not in files:
                    raise AccessError('资源不存在', 404, 'not_found')
                name, mime = files[self.path]
                raw = (ROOT / 'web' / name).read_bytes()
                if name == 'index.html':
                    raw = raw.replace(b'/app.js', b'/scenario.js')
                self.respond(raw, mime=mime)
            except Exception as error:
                self.failure(error)
        def do_POST(self):
            try:
                self.check_host()
                if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
                    raise AccessError('不接受该来源', 403, 'invalid_origin')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16000:
                    raise AccessError('请求大小无效', 400, 'invalid_request')
                body = json.loads(self.rfile.read(length))
                if type(body) is not dict:
                    raise AccessError('请求格式无效', 400, 'invalid_request')
                if self.path == '/api/login':
                    token = app.login(body, self.token())
                    self.respond(app.view(token), cookie=token)
                    return
                session = app.authenticate(self.token())
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                    raise AccessError('会话校验失败，请刷新页面', 403, 'csrf_failed')
                if self.path == '/api/chat':
                    self.respond(app.chat(self.token(), body))
                elif self.path == '/api/settings' and set(body) == {'thinking'} and type(body['thinking']) is bool:
                    if not session['lock'].acquire(blocking=False):
                        raise AccessError('请等待本轮结束', 409, 'busy')
                    try:
                        session['thinking'] = body['thinking']
                    finally:
                        session['lock'].release()
                    self.respond(app.view(self.token()))
                elif self.path in {'/api/logout', '/api/new'} and not body:
                    if self.path == '/api/logout':
                        with app.lock:
                            app.sessions.pop(self.token(), None)
                        self.respond({'authenticated': False}, cookie='')
                    else:
                        if not session['lock'].acquire(blocking=False):
                            raise AccessError('请等待本轮结束', 409, 'busy')
                        try:
                            session['turns'].clear()
                        finally:
                            session['lock'].release()
                        self.respond(app.view(self.token()))
                else:
                    raise AccessError('资源不存在', 404, 'not_found')
            except Exception as error:
                self.failure(error)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--accounts', type=Path, required=True)
    parser.add_argument('--init-accounts', action='store_true')
    parser.add_argument('--port', type=int, default=18880)
    args = parser.parse_args()
    for path in (args.bundle, args.accounts):
        if not path.resolve().is_relative_to(ROOT / 'local-only'):
            parser.error('scenario files must be in local-only')
    if args.init_accounts:
        initialize_accounts(args.accounts, load_bundle(args.bundle)[0])
    app = ScenarioApplication(args.bundle, args.accounts)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    print(f'模拟账号 × 真实 DeepSeek Flash：http://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
