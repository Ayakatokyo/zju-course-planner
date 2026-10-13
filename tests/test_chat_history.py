"""Owned history projections and reload/continue; all models are test doubles."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from course_assistant.auth import AccessError, AuthContext
from course_assistant.model_client import ModelError
from course_assistant.server import Application, make_handler
from course_assistant.storage import encoded


def call(name, args=None, identifier='history-tool'):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args or {})}}]}


def answer(outcome='context'):
    return {'role': 'assistant', 'content': json.dumps({'kind': 'answer', 'outcome': outcome, 'cited_chunk_ids': []}),
            'tool_calls': []}


class ScriptedModel:
    allow_reference_text = False
    def __init__(self, responses=()):
        self.responses, self.calls = list(responses), []

    def status(self):
        return {'configured': True, 'remote': True, 'provider': 'history-test-double', 'model': 'scripted-only'}

    def complete(self, messages, tools):
        self.calls.append(deepcopy(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'history.sqlite3'
        self.model = ScriptedModel()
        self.app = Application(self.db, mock_enabled=True, recommendation_demo=True, model=self.model)
        token, _ = self.app.auth.login_mock({'profile_id': 'demo-a'})
        self.context = self.app.auth.authenticate(token)
        token, _ = self.app.auth.login_mock({'profile_id': 'demo-b'})
        self.other = self.app.auth.authenticate(token)
        self.app.workspace(self.context)

    def turn(self, message='查询合成需求', responses=None, previous=None):
        self.model.responses = list(responses or [call('get_my_context'), answer()])
        return self.app.post('/api/chat/turn', {'message': message, 'request_id': str(uuid.uuid4()),
            'expected_turn': previous['turn_id'] if previous else 0,
            'conversation_id': previous['conversation_id'] if previous else None, 'consent_remote': True}, self.context)

    def history(self, identifier, context=None):
        return self.app.post('/api/chat/history', {'conversation_id': identifier}, context or self.context)

    def business_snapshot(self):
        with self.app.store.connect() as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in ('owned_datasets', 'workspaces', 'owned_plans', 'owned_conversations',
                                  'owned_chat_requests', 'owned_recommendation_actions')}

    def insert_conversation(self, *, context=None, state=None, updated_at=None, pending=None, turn=0):
        context = context or self.context
        identifier = str(uuid.uuid4())
        state = state or {'messages': [], 'turns': [], 'preview_constraints': None, 'workspace_versions': None}
        with self.app.store.connect() as db:
            db.execute('INSERT INTO owned_conversations VALUES(?,?,?,?,?,?,?)',
                       (identifier, context.security_realm, context.student_id, turn, encoded(state), pending,
                        updated_at or datetime.now(timezone.utc).isoformat()))
        return identifier

    def test_empty_history_list_is_authenticated_and_bounded(self):
        result = self.app.get('/api/chat/conversations', self.context)
        self.assertEqual(result, {'conversations': [], 'limit': 50, 'has_more': False, 'data_kind': 'synthetic'})
        with self.assertRaises(AccessError): self.app.get('/api/chat/conversations')

    def test_lists_only_trusted_owner_and_realm_and_never_tool_results(self):
        own = self.turn('保留体育并查询合成课程')
        other_id = self.insert_conversation(context=self.other, state={'title': 'OTHER PRIVATE CONTENT', 'turns': []})
        realm = AuthContext(self.context.principal_id, self.context.session_ref, self.context.student_id,
                            'other-test-realm', self.context.binding_version)
        other_realm_id = self.insert_conversation(context=realm, state={'title': 'OTHER REALM CONTENT', 'turns': []})
        result = self.app.get('/api/chat/conversations?limit=100', self.context)
        self.assertEqual([row['conversation_id'] for row in result['conversations']], [own['conversation_id']])
        self.assertEqual(set(result['conversations'][0]), {'conversation_id', 'title', 'turn_id', 'updated_at', 'in_progress'})
        self.assertNotIn('tool_results', json.dumps(result))
        self.assertNotIn('OTHER', json.dumps(result))
        self.assertNotIn(other_id, json.dumps(result))
        self.assertNotIn(other_realm_id, json.dumps(result))
        self.assertEqual(self.app.store.chat_list(realm)['conversations'][0]['conversation_id'], other_realm_id)
        with self.assertRaises(AccessError): self.app.chat.conversations(realm)

    def test_limit_sorting_and_truncation_use_latest_activity(self):
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        ids = [self.insert_conversation(updated_at=(start + timedelta(minutes=i)).isoformat()) for i in range(5)]
        result = self.app.get('/api/chat/conversations?limit=3', self.context)
        self.assertEqual([row['conversation_id'] for row in result['conversations']], list(reversed(ids[-3:])))
        self.assertEqual(result['limit'], 3)
        self.assertTrue(result['has_more'])
        self.assertFalse(self.app.get('/api/chat/conversations?limit=5', self.context)['has_more'])
        reservation = self.app.store.chat_begin(self.context, ids[0], str(uuid.uuid4()), 'synthetic-pending', 0,
                                                first_message='这一轮正在准备')
        latest = self.app.get('/api/chat/conversations?limit=1', self.context)['conversations'][0]
        self.assertEqual(latest['conversation_id'], ids[0])
        self.assertTrue(latest['in_progress'])

    def test_limit_is_the_only_query_parameter_and_must_be_one_integer(self):
        for query in ('limit=0', 'limit=101', 'limit=-1', 'limit=1.5', 'limit=true', 'limit=',
                      'limit=1&limit=2', 'owner=DEMO-STUDENT-002', 'limit=3&realm=other', 'limit=03'):
            with self.subTest(query=query), self.assertRaises(AccessError):
                self.app.get('/api/chat/conversations?' + query, self.context)
        with self.assertRaises(AccessError): self.app.chat.conversations(self.context, limit=True)

    def test_first_user_title_is_short_and_preserved_across_followups(self):
        original = '优先补合成专业选修，偏向AI，保留体育。' * 5
        first = self.turn(original)
        title = self.app.get('/api/chat/conversations', self.context)['conversations'][0]['title']
        self.assertLessEqual(len(title), 60)
        self.assertTrue(title.endswith('…'))
        self.turn('现在周五必须空闲', previous=first)
        self.assertEqual(self.app.get('/api/chat/conversations', self.context)['conversations'][0]['title'], title)
        self.assertEqual(self.history(first['conversation_id'])['title'], title)

    def test_legacy_title_falls_back_to_first_turn_without_schema_rewrite(self):
        identifier = self.insert_conversation(state={'turns': [{'message': '  旧需求\n  保留体育  ', 'status': 'failed'}],
                                                      'workspace_versions': None}, turn=1)
        result = self.app.get('/api/chat/conversations', self.context)
        self.assertEqual(result['conversations'][0]['title'], '旧需求 保留体育')
        self.assertEqual(self.history(identifier)['title'], '旧需求 保留体育')

    def test_reload_failed_and_empty_and_in_progress_conversations(self):
        failed = self.turn('查询合成需求但模型临时失败', [ModelError('测试替身超时', 'provider_timeout')])
        loaded = self.history(failed['conversation_id'])
        self.assertEqual(loaded['turn_id'], 1)
        self.assertFalse(loaded['in_progress'])
        self.assertEqual(loaded['turns'][0]['status'], 'failed')
        empty = self.insert_conversation()
        self.assertEqual(self.history(empty)['turns'], [])
        reserved = self.app.store.chat_begin(self.context, None, str(uuid.uuid4()), 'synthetic-pending', 0,
                                            first_message='正在查询合成课表')
        current = self.history(reserved['conversation_id'])
        self.assertTrue(current['in_progress'])
        self.assertEqual(current['turn_id'], 0)
        self.assertEqual(current['title'], '正在查询合成课表')
        self.assertEqual(current['turns'], [])
        with self.assertRaises(AccessError) as caught:
            self.turn('继续查询', previous=current)
        self.assertEqual(caught.exception.reason, 'turn_in_progress')

    def test_unknown_or_other_owned_id_has_same_inaccessible_response(self):
        first = self.turn()
        errors = []
        for identifier, context in ((first['conversation_id'], self.other), (str(uuid.uuid4()), self.context)):
            with self.assertRaises(AccessError) as caught: self.history(identifier, context)
            errors.append((caught.exception.status, caught.exception.reason, str(caught.exception)))
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[0][:2], (404, 'inaccessible_object'))

    def test_reload_after_process_restart_continues_the_same_history(self):
        first = self.turn('优先保留合成体育')
        renewed_model = ScriptedModel([call('get_my_context'), answer()])
        renewed = Application(self.db, mock_enabled=True, recommendation_demo=True, model=renewed_model)
        token, _ = renewed.auth.login_mock({'profile_id': 'demo-a'})
        context = renewed.auth.authenticate(token)
        loaded = renewed.post('/api/chat/history', {'conversation_id': first['conversation_id']}, context)
        self.assertEqual(loaded['turn_id'], 1)
        second = renewed.post('/api/chat/turn', {'conversation_id': loaded['conversation_id'],
            'expected_turn': loaded['turn_id'], 'request_id': str(uuid.uuid4()),
            'message': '继续查询同一组条件', 'consent_remote': True}, context)
        self.assertEqual(second['conversation_id'], first['conversation_id'])
        self.assertEqual(second['turn_id'], 2)
        self.assertTrue(any(m.get('content') == '优先保留合成体育' for m in renewed_model.calls[0]))
        self.assertEqual(len(renewed.get('/api/chat/conversations', context)['conversations']), 1)
        with self.assertRaises(AccessError):
            renewed.post('/api/chat/turn', {'conversation_id': loaded['conversation_id'],
                'expected_turn': loaded['turn_id'], 'request_id': str(uuid.uuid4()),
                'message': '旧页面重复继续', 'consent_remote': True}, context)

    def test_loading_does_not_execute_or_redownload_completed_actions(self):
        first = self.turn('推荐合成课程', [call('recommend_my_courses'), answer('feasible')])
        option = first['tool_results'][0]['result']['plans'][1]
        command = {'conversation_id': first['conversation_id'], 'expected_turn': first['turn_id'],
                   'run_id': option['run_id'], 'option_id': option['option_id'], 'action_id': str(uuid.uuid4())}
        saved = self.app.post('/api/chat/save-option', command, self.context)
        # A completed language-export receipt is data on reload, never a command.
        with self.app.store.connect() as db:
            state = json.loads(db.execute('SELECT state FROM owned_conversations WHERE id=?',
                                          (first['conversation_id'],)).fetchone()[0])
            state['turns'][-1].update(reply_origin='verified_explicit_action',
                                    saved_action={'saved_id': saved['saved_id']},
                                    download={'data_base64': 'e30=', 'content_type': 'application/json',
                                              'filename': 'synthetic.json', 'bytes': 2}, action_pending=False)
            db.execute('UPDATE owned_conversations SET state=? WHERE id=?', (encoded(state), first['conversation_id']))
        before = self.business_snapshot()
        calls = len(self.model.calls)
        with patch('course_assistant.recommendation_dialogue.finish_explicit_action', side_effect=AssertionError('must not execute')):
            loaded = self.history(first['conversation_id'])
            loaded_again = self.history(first['conversation_id'])
        self.assertEqual(loaded, loaded_again)
        self.assertEqual(loaded['option_receipts'][option['option_id']],
                         {'saved_id': saved['saved_id'], 'option_id': option['option_id'],
                          'run_id': option['run_id'], 'versions': saved['versions']})
        self.assertEqual(loaded['turns'][-1]['download']['bytes'], 2)
        self.assertFalse(loaded['action_pending'])
        self.assertEqual(len(self.model.calls), calls)
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual(len(self.app.store.saved(self.context)), 1)
        self.assertNotIn('frozen_dataset', json.dumps(loaded['option_receipts']))
        self.assertEqual(self.app.store.chat_option_receipts(self.other, first['conversation_id'])[0], {})

    def test_history_exposes_interrupted_action_pending_without_resuming_it(self):
        identifier = self.insert_conversation(state={'turns': [{'message': '保存B', 'action_pending': True}],
                                                      'workspace_versions': None}, turn=1)
        before = self.business_snapshot()
        with patch('course_assistant.recommendation_dialogue.finish_explicit_action', side_effect=AssertionError('must not execute')):
            loaded = self.history(identifier)
        self.assertTrue(loaded['action_pending'])
        self.assertFalse(loaded['in_progress'])
        self.assertEqual(self.business_snapshot(), before)

    def test_workspace_update_keeps_history_but_marks_preview_stale(self):
        first = self.turn()
        workspace = self.app.workspace(self.context)
        self.app.store.set_workspace(self.context, workspace['demo'], {**workspace['constraints'], 'revision': 99})
        loaded = self.history(first['conversation_id'])
        self.assertTrue(loaded['is_stale'])
        self.assertEqual(loaded['turns'][0]['message'], '查询合成需求')
        second = self.turn('按当前条件继续查询', previous=loaded)
        self.assertTrue(second['preview_reset'])
        self.assertEqual(second['conversation_id'], first['conversation_id'])

    def test_http_get_list_and_post_load_enforce_session_query_and_csrf(self):
        first = self.turn()
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def stop():
            server.shutdown(); server.server_close(); worker.join(2)
        self.addCleanup(stop)
        token, status = self.app.auth.login_mock({'profile_id': 'demo-a'})
        def request(method, path, body=None, auth=True, csrf=True):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['Cookie'] = 'course_session=' + token
                if csrf: headers['X-CSRF-Token'] = status['csrf_token']
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        self.assertEqual(request('GET', '/api/chat/conversations?limit=100', auth=False)[0], 401)
        code, listed = request('GET', '/api/chat/conversations?limit=100')
        self.assertEqual(code, 200)
        self.assertEqual(listed['conversations'][0]['conversation_id'], first['conversation_id'])
        self.assertEqual(request('GET', '/api/chat/conversations?owner=DEMO-STUDENT-002')[0], 400)
        self.assertEqual(request('GET', '/api/chat/status?limit=100')[0], 400)
        self.assertEqual(request('POST', '/api/chat/history', {'conversation_id': first['conversation_id']}, csrf=False)[0], 403)
        code, loaded = request('POST', '/api/chat/history', {'conversation_id': first['conversation_id']})
        self.assertEqual(code, 200)
        self.assertEqual(loaded['turn_id'], 1)


if __name__ == '__main__':
    unittest.main()
