"""Business contracts over real tools/SQLite; the model is a labeled test double."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from course_assistant.auth import AccessError
from course_assistant.chat import ChatFailure, ChatService
from course_assistant.importer import digest
from course_assistant.planner import revise_constraints, validate_plan
from course_assistant.recommendation_dialogue import apply_operations
from course_assistant.server import Application
from test_chat import FakeModel, call, answer


class RecommendationDialogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name) / 'recommend.sqlite3', mock_enabled=True, recommendation_demo=True)
        token, _ = self.app.auth.login_mock({'profile_id': 'demo-a'})
        self.context = self.app.auth.authenticate(token)
        token, _ = self.app.auth.login_mock({'profile_id': 'demo-b'})
        self.other = self.app.auth.authenticate(token)
        self.workspace = self.app.workspace(self.context)

    def turn(self, message, responses, previous=None):
        service = ChatService(self.app, FakeModel(responses))
        return service.turn({'message': message, 'request_id': str(uuid.uuid4()), 'consent_remote': True,
                             'expected_turn': previous['turn_id'] if previous else 0,
                             'conversation_id': previous['conversation_id'] if previous else None}, self.context)

    def initial(self):
        return self.turn('优先补合成专业选修6学分，偏向AI，尽量周五空闲，保留体育',
                         [call('get_my_context', {}), call('recommend_my_courses', {}, 'c2'), answer('feasible')])

    def action(self, response, option, **extra):
        return {'conversation_id': response['conversation_id'], 'expected_turn': response['turn_id'],
                'run_id': option['run_id'], 'option_id': option['option_id'], 'action_id': str(uuid.uuid4()), **extra}

    def test_goal_selects_different_courses_and_independently_revalidates_each_subset(self):
        response = self.initial()
        self.assertEqual(response['status'], 'completed')
        result = response['tool_results'][-1]['result']
        self.assertGreaterEqual(len(result['plans']), 2)
        self.assertGreaterEqual(len({frozenset(p['course_ids']) for p in result['plans']}), 2)
        self.assertEqual(self.app.workspace(self.context), self.workspace)
        for plan in result['plans']:
            validation = validate_plan(plan['section_ids'], self.workspace['demo']['offering_snapshot'],
                                       self.workspace['demo']['student'], plan['constraints'],
                                       self.workspace['demo']['academic_snapshot']['records'])
            self.assertEqual(validation['result'], 'pass')
            self.assertEqual(plan['metrics']['module_new_contribution'], 6)
            self.assertIn('DEMO-PE-01', plan['section_ids'])
            self.assertLess(len(plan['constraints']['requested_course_ids']), 4)

    def test_comparison_and_soft_to_hard_keep_protection_return_exactly_one(self):
        first = self.initial()
        compared = self.turn('比较A和B的课程与周五取舍',
                             [call('compare_my_options', {'option_ids': ['A', 'B']}), answer('compared')], first)
        self.assertEqual(compared['status'], 'completed')
        self.assertTrue(compared['tool_results'][0]['result']['differences'][1]['added_course_ids'])
        hard = self.turn('周五必须空闲，其他锁定保持', [call('revise_my_recommendation', {'operations': [
            {'op': 'set', 'field': 'friday_free', 'value': 'hard', 'evidence': '周五必须空闲'}]}), answer('feasible')], compared)
        result = hard['tool_results'][0]['result']
        self.assertEqual(hard['status'], 'completed')
        self.assertEqual(len(result['plans']), 1)
        self.assertEqual(set(result['plans'][0]['course_ids']), {'DEMO-DATA', 'DEMO-MODEL', 'DEMO:PE'})
        self.assertEqual(result['constraints']['preloaded_section_ids'], ['DEMO-PE-01'])

    def test_replace_does_not_silently_relax_hard_and_explicit_relaxation_works(self):
        first = self.initial()
        hard = self.turn('周五必须空闲', [call('revise_my_recommendation', {'operations': [
            {'op': 'set', 'field': 'friday_free', 'value': 'hard', 'evidence': '周五必须空闲'}]}), answer('feasible')], first)
        changed = self.turn('把A的模型方法换成AI实践', [call('revise_my_recommendation', {'option_id': 'A', 'operations': [
            {'op': 'replace', 'field': 'optional_course_ids', 'value': 'DEMO-MODEL', 'replacement': 'DEMO-AI',
             'evidence': '把A的模型方法换成AI实践'}]}), answer('scoped_infeasible')], hard)
        self.assertEqual(changed['status'], 'completed')
        self.assertEqual(changed['tool_results'][0]['result']['constraints']['friday_free'], 'hard')
        self.assertEqual(changed['tool_results'][0]['result']['plans'], [])
        relaxed = self.turn('周五可以有课，其他条件保留', [call('revise_my_recommendation', {'operations': [
            {'op': 'set', 'field': 'friday_free', 'value': 'off', 'evidence': '周五可以有课'}]}), answer('feasible')], changed)
        self.assertEqual(relaxed['status'], 'completed')
        self.assertIn('DEMO-AI', relaxed['tool_results'][0]['result']['plans'][0]['course_ids'])

    def test_ambiguous_negated_hypothetical_and_model_evidence_cannot_unlock(self):
        for message, evidence in [('如果周五可以有课，会怎样', '周五可以有课'),
                                  ('周五可以有课吗', '周五可以有课'),
                                  ('保留全部条件', '周五可以有课')]:
            with self.assertRaises(ChatFailure):
                apply_operations(self.workspace['demo'], self.workspace['constraints'], self.workspace['demo']['recommendation'],
                                 [{'op': 'set', 'field': 'friday_free', 'value': 'off', 'evidence': evidence}], message)
        for message in ('不要解除锁定DEMO-PE-01', '解除锁定DEMO-PE-01'):
            with self.assertRaises(ChatFailure):
                apply_operations(self.workspace['demo'], self.workspace['constraints'], self.workspace['demo']['recommendation'],
                                 [{'op': 'remove', 'field': 'locked_section_ids', 'value': 'DEMO-PE-01', 'evidence': message}], message)

    def test_save_export_idempotency_and_followup_do_not_promote_selected_candidates(self):
        first = self.initial()
        option = first['tool_results'][-1]['result']['plans'][1]
        command = self.action(first, option)
        saved = self.app.post('/api/chat/save-option', command, self.context)
        replay = self.app.post('/api/chat/save-option', command, self.context)
        self.assertEqual(replay['saved_id'], saved['saved_id'])
        self.assertEqual(len(self.app.store.saved(self.context)), 1)
        self.assertEqual(self.app.workspace(self.context)['constraints'], saved['constraints'])
        data, mime, _ = self.app.post('/api/chat/export-option', {k: v for k, v in command.items() if k != 'action_id'}, self.context)
        exported = json.loads(data)
        self.assertEqual(exported['saved_id'], saved['saved_id'])
        self.assertEqual(exported['plan']['section_ids'], option['section_ids'])
        followed = self.turn('继续按相同合成目标给出不同课程组合',
                             [call('recommend_my_courses', {}), answer('feasible')], first)
        self.assertFalse(followed['preview_reset'])
        result = followed['tool_results'][0]['result']
        self.assertGreaterEqual(len({frozenset(p['course_ids']) for p in result['plans']}), 2)
        reopened = self.initial()
        self.assertGreaterEqual(len({frozenset(p['course_ids']) for p in reopened['tool_results'][-1]['result']['plans']}), 2)

    def test_cross_owner_stale_turn_version_and_client_override_are_denied(self):
        first = self.initial()
        option = first['tool_results'][-1]['result']['plans'][0]
        command = self.action(first, option)
        with self.assertRaises((AccessError, ChatFailure)):
            self.app.post('/api/chat/save-option', command, self.other)
        with self.assertRaises((AccessError, ChatFailure)):
            self.app.post('/api/chat/save-option', {**command, 'expected_turn': 0}, self.context)
        with self.assertRaises((AccessError, ChatFailure)):
            self.app.post('/api/chat/save-option', {**command, 'constraints': option['constraints']}, self.context)
        newer = revise_constraints(self.workspace['constraints'], {'friday_free': 'hard'})
        self.app.store.set_workspace(self.context, self.workspace['demo'], newer)
        with self.assertRaises((AccessError, ChatFailure)):
            self.app.post('/api/chat/save-option', command, self.context)
        self.assertEqual(self.app.store.saved(self.context), [])

    def test_unknown_stays_pending_and_unreliable_calendar_refuses_ics(self):
        fixture = copy.deepcopy(self.app.fixture)
        fixture['offering_snapshot']['academic_calendar'] = None
        fixture['academic_calendar'] = None
        for section in fixture['offering_snapshot']['records']:
            for meeting in section.get('meetings', []):
                meeting['week_numbers'] = None
        self.app.fixture = fixture
        self.app.store.set_workspace(self.context, self.app.default_bundle(self.context), fixture['constraints'])
        result = self.turn('生成合成目标课表并明确未知', [call('recommend_my_courses', {}), answer('incomplete_data')])
        self.assertEqual(result['status'], 'completed')
        recommended = result['tool_results'][0]['result']
        self.assertEqual(recommended['plans'], [])
        self.assertTrue(recommended['pending_plans'])
        option = recommended['pending_plans'][0]
        with self.assertRaises(ValueError):
            self.app.post('/api/chat/export-option', self.action(result, option, format='ics'), self.context)

    def test_superseded_intent_and_old_option_cannot_be_saved(self):
        first = self.initial()
        old = first['tool_results'][-1]['result']['plans'][0]
        hard = self.turn('周五必须空闲', [call('revise_my_recommendation', {'operations': [
            {'op': 'set', 'field': 'friday_free', 'value': 'hard', 'evidence': '周五必须空闲'}]}), answer('feasible')], first)
        with self.assertRaises(ChatFailure):
            self.app.post('/api/chat/save-option', self.action(hard, old), self.context)

    def test_final_decision_cannot_use_old_feasible_after_course_replace(self):
        first = self.initial()
        response = self.turn('把模型方法换成AI实践，周五必须空闲', [
            call('recommend_my_courses', {}),
            call('revise_my_recommendation', {'operations': [
                {'op': 'replace', 'field': 'optional_course_ids', 'value': 'DEMO-MODEL', 'replacement': 'DEMO-AI', 'evidence': '把模型方法换成AI实践'},
                {'op': 'set', 'field': 'friday_free', 'value': 'hard', 'evidence': '周五必须空闲'}]}, 'c2'), answer('feasible')], first)
        self.assertEqual(response['status'], 'failed')
        self.assertEqual(response['code'], 'inconsistent_outcome')

    def test_direction_field_and_negative_words_are_verified_not_word_coincidence(self):
        bad = [
            ('不解除锁定DEMO-MODEL-01', {'op': 'remove', 'field': 'locked_section_ids', 'value': 'DEMO-MODEL-01', 'evidence': '解除锁定DEMO-MODEL-01'}),
            ('不要让周五可以有课', {'op': 'set', 'field': 'friday_free', 'value': 'off', 'evidence': '周五可以有课'}),
            ('不用取消周五空闲要求', {'op': 'set', 'field': 'friday_free', 'value': 'off', 'evidence': '取消周五空闲要求'}),
            ('为什么周五可以有课', {'op': 'set', 'field': 'friday_free', 'value': 'off', 'evidence': '周五可以有课'}),
            ('把AI实践换成模型方法', {'op': 'replace', 'field': 'optional_course_ids', 'value': 'DEMO-MODEL', 'replacement': 'DEMO-AI', 'evidence': '把AI实践换成模型方法'}),
            ('加入AI实践', {'op': 'add', 'field': 'excluded_course_ids', 'value': 'DEMO-AI', 'evidence': '加入AI实践'}),
            ('保留数据分析', {'op': 'add', 'field': 'excluded_course_ids', 'value': 'DEMO-DATA', 'evidence': '保留数据分析'}),
        ]
        for message, operation in bad:
            with self.subTest(message=message), self.assertRaises(ChatFailure):
                apply_operations(self.workspace['demo'], self.workspace['constraints'], self.workspace['demo']['recommendation'], [operation], message)

    def test_personal_unlock_cancel_exclusion_and_exclusion_replace_have_correct_meaning(self):
        constraints = {**self.workspace['constraints'], 'locked_section_ids': ['DEMO-PE-01', 'DEMO-MODEL-01'],
                       'excluded_section_ids': ['DEMO-AI-01']}
        changed, intent, _ = apply_operations(self.workspace['demo'], constraints, self.workspace['demo']['recommendation'], [
            {'op': 'remove', 'field': 'locked_section_ids', 'value': 'DEMO-MODEL-01', 'evidence': '解除锁定DEMO-MODEL-01'},
            {'op': 'remove', 'field': 'excluded_section_ids', 'value': 'DEMO-AI-01', 'evidence': '取消排除DEMO-AI-01'}],
            '解除锁定DEMO-MODEL-01，取消排除DEMO-AI-01')
        self.assertEqual(changed['locked_section_ids'], ['DEMO-PE-01'])
        self.assertEqual(changed['excluded_section_ids'], [])
        intent['excluded_course_ids'] = ['DEMO-MODEL']
        _, changed_intent, _ = apply_operations(self.workspace['demo'], changed, intent, [
            {'op': 'replace', 'field': 'excluded_course_ids', 'value': 'DEMO-MODEL', 'replacement': 'DEMO-AI',
             'evidence': '把排除的模型方法换成排除AI实践'}], '把排除的模型方法换成排除AI实践')
        self.assertEqual(changed_intent['excluded_course_ids'], ['DEMO-AI'])
        self.assertNotIn('DEMO-AI', changed_intent['required_course_ids'])

    def test_negated_goal_or_interest_patch_does_not_change_preview(self):
        first = self.initial()
        for message, patch in [('不要改成3学分', {'target_credits': 3}), ('不要取消AI兴趣', {'interest': 'off'})]:
            response = self.turn(message, [call('recommend_my_courses', {'intent_patch': patch})], first)
            self.assertEqual(response['status'], 'failed')
            self.assertEqual(response['code'], 'explicit_intent_required')
            first = response

    def test_rule_unknown_is_preserved_in_card_saved_result_and_export(self):
        self.app.fixture['rules'][0]['human_review_required'] = True
        rule = next(rule for rule in self.app.fixture['rules'] if rule['rule_id'] == 'DEMO-ELECTIVE')
        rule['human_review_required'] = True
        self.app.store.set_workspace(self.context, self.app.default_bundle(self.context), self.app.fixture['constraints'])
        response = self.turn('按合成目标推荐并显示规则未知', [call('recommend_my_courses', {}), answer('incomplete_data')])
        self.assertEqual(response['status'], 'completed')
        option = response['tool_results'][0]['result']['pending_plans'][0]
        self.assertEqual(option['validation']['result'], 'unknown')
        self.assertEqual(option['metrics']['contribution_state'], 'unknown')
        saved = self.app.post('/api/chat/save-option', self.action(response, option), self.context)
        self.assertEqual(saved['plan']['validation']['result'], 'unknown')
        self.assertTrue(any(c['code'] == 'module_target' and c['result'] == 'unknown' for c in saved['plan']['validation']['checks']))

    def test_same_course_class_replace_executes_and_keeps_all_source_protection(self):
        first = self.initial()
        second = self.turn('锁定DEMO-AI-01', [call('revise_my_recommendation', {'operations': [
            {'op': 'add', 'field': 'locked_section_ids', 'value': 'DEMO-AI-01', 'evidence': '锁定DEMO-AI-01'}]}), answer('feasible')], first)
        third = self.turn('把DEMO-AI-01换成DEMO-AI-02', [call('revise_my_recommendation', {'operations': [
            {'op': 'replace', 'field': 'locked_section_ids', 'value': 'DEMO-AI-01', 'replacement': 'DEMO-AI-02',
             'evidence': '把DEMO-AI-01换成DEMO-AI-02'}]}), answer('feasible')], second)
        self.assertEqual(third['status'], 'completed')
        for option in third['tool_results'][0]['result']['plans']:
            self.assertIn('DEMO-AI-02', option['section_ids'])
            self.assertNotIn('DEMO-AI-01', option['section_ids'])
            self.assertIn('DEMO-PE-01', option['section_ids'])

    def test_explicit_language_save_and_export_return_actual_receipts_without_resending_bytes(self):
        first = self.initial()
        second = self.turn('保存B', [call('act_on_my_option', {'action': 'save', 'option_id': 'B'}), answer('action_ready')], first)
        self.assertEqual(second['status'], 'completed')
        self.assertFalse(second['action_pending'])
        self.assertGreater(second['saved_action']['saved_id'], 0)
        self.assertEqual(second['versions'], self.app.versions(self.app.workspace(self.context)))
        third = self.turn('导出B为JSON', [call('act_on_my_option', {'action': 'export', 'option_id': 'B', 'format': 'json'}), answer('action_ready')], second)
        self.assertEqual(third['status'], 'completed')
        self.assertGreater(third['download']['bytes'], 0)
        row = self.app.store.chat_one(self.context, third['conversation_id'])
        self.assertNotIn('data_base64', json.dumps(row['state']['messages']))
        self.assertIn('download', row['state']['turns'][-1])

    def test_action_ready_interruption_resumes_same_save_request_exactly_once(self):
        first = self.initial()
        service = ChatService(self.app, FakeModel([call('act_on_my_option', {'action': 'save', 'option_id': 'B'}), answer('action_ready')]))
        request = {'message': '保存B', 'request_id': str(uuid.uuid4()), 'consent_remote': True,
                   'expected_turn': first['turn_id'], 'conversation_id': first['conversation_id']}
        with patch('course_assistant.recommendation_dialogue.finish_explicit_action', side_effect=RuntimeError('simulated process stop')):
            with self.assertRaises(RuntimeError):
                service.turn(request, self.context)
        response = service.turn(request, self.context)
        self.assertEqual(response['status'], 'completed')
        saved_id = response['saved_action']['saved_id']
        replayed = service.turn(request, self.context)
        self.assertEqual(replayed['saved_action']['saved_id'], saved_id)
        self.assertEqual(len(self.app.store.saved(self.context)), 1)

    def test_legacy_tool_condition_change_invalidates_prior_recommendation_reference(self):
        first = self.initial()
        old = first['tool_results'][-1]['result']['plans'][0]
        second = self.turn('周五必须空闲', [call('generate_my_plan', {'constraint_patch': {'friday_free': 'hard'}}), answer('feasible')], first)
        self.assertEqual(second['status'], 'completed')
        with self.assertRaises(ChatFailure):
            self.app.post('/api/chat/save-option', self.action(second, old), self.context)


if __name__ == '__main__':
    unittest.main()
