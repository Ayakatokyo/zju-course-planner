"""Candidate context and grounded explanation contracts over synthetic sources."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.current_term_recommendation import CurrentTermCourseDataTest
from course_assistant.personal_course_plans import PersonalCoursePlanService, project_course_plan_result
from course_assistant.personal_dialogue import PersonalDialogueService, PersonalDialogueError, EVIDENCE_LIMITS, COURSE_SEARCH_EVIDENCE_LIMIT
from course_assistant.personal_plan_explanation import plan_reasoning_facts
from tests.test_current_term_recommendation import fixture, add_observation, write_delivery
from tests.test_personal_course_plans import sources
from tests.test_personal_dialogue import ScriptedModel, call, final

TERM = '2026-2027-AW'


class CandidateReasoningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = fixture()
        self.turn = []

    def engine(self):
        return CurrentTermCourseDataTest(self.data, 'SYN-v1', current_term=TERM,
            allowed_course_codes=['SYN-A', 'SYN-B', 'SYN-C'], authorize=lambda: True)

    def service(self):
        return PersonalCoursePlanService(write_delivery(self.folder.name, self.data), TERM,
            lambda: {'scope_key': 'synthetic-only'})

    def test_search_exposes_same_latest_observation_exclusions_as_solver(self):
        add_observation(self.data, remaining=0)  # Earlier positive capacity must not win.
        self.data['meetings'][1]['fields']['periods'] = None
        engine = self.engine()
        result = {r['course_code']: r['observation_summary'] for r in engine.search_courses('Synthetic')['courses']}
        self.assertEqual(result['SYN-A']['usable_section_count'], 0)
        self.assertEqual(result['SYN-A']['blocking_reasons'], ['historical_nonpositive_capacity'])
        self.assertEqual(result['SYN-B']['blocking_reasons'], ['excluded_or_unknown_weekday_period'])
        self.assertEqual(result['SYN-C']['usable_section_count'], 1)
        self.assertEqual(result['SYN-C']['friday_free_section_count'], 0)
        self.assertEqual(result['SYN-C']['weekday_period_text'], ['周五第1、2节'])
        plan = engine.recommend(engine.new_constraints(2, friday_free='off'))
        self.assertTrue(plan['options'])
        self.assertTrue(all(o['course_codes'] == ['SYN-C'] for o in plan['options']))
        self.assertTrue(all(v['current_availability_verified'] is False for v in result.values()))

    def test_unordered_observations_do_not_invent_a_usable_latest_section(self):
        add_observation(self.data, when='2026-10-13T06:00:00Z')
        summary = self.engine().search_courses('SYN-A')['courses'][0]['observation_summary']
        self.assertEqual(summary['usable_section_count'], 0)
        self.assertEqual(summary['blocking_reasons'], ['simultaneous_observations_require_explicit_lock'])
        self.assertEqual(summary['weekday_period_text'], [])

    def test_model_receives_only_bounded_candidate_context_and_explains_actual_result(self):
        service = self.service()
        def execute(name, args):
            if name == 'get_my_context':
                return {'status': 'available', 'operations': [], 'api_operations': [],
                    'capabilities': {'course_data_test': True}, 'course_data_test': service.describe()}
            return service.execute(name, args, sources=sources(), turn_identity=self.turn)
        def choose(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            rows = result['result']['courses']
            self.assertEqual(rows[0]['observation_summary']['friday_free_section_count'], 1)
            self.assertNotIn('PRIVATE-', json.dumps(result))
            self.assertNotIn('observation_id', json.dumps(result))
            return call('generate_current_term_plan', {'recommendation_kind': 'courses', 'target_term': TERM,
                'course_codes': ['SYN-A', 'SYN-B', 'SYN-C'], 'target_credits': 6, 'friday_free': 'soft'}, 'generate')
        def explain(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertNotIn('analysis_guidance', result)
            self.assertEqual(result['reasoning_facts']['options'][0]['friday_observed_slots'], [])
            self.assertFalse(result['reasoning_facts']['domain_relevance_ranked'])
            return final({'kind': 'answer', 'text': '优先方案A：两门课合计6学分，已有节次不占周五。它更贴近学分目标；多加一门仅作备选。', 'evidence_ids': ([result['evidence_id']]) + ([])})
        model = ScriptedModel([call('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}), choose, explain])
        response, _ = PersonalDialogueService(model, lambda: {'scope_key': 'synthetic-only'}, execute).run(
            '给出至少6学分的课程组合，尽量周五空闲，并解释取舍。', data_use='personal_retrieval')
        self.assertEqual(response['status'], 'completed', response)
        self.assertEqual(response['repair_attempts'], 0)
        self.assertIn('优先方案A', response['reply'])
        self.assertEqual(response['recommendation_result']['options'][0]['total_credits'], '6')
        self.assertEqual(response['recommendation_result']['options'][0]['friday_meeting_count'], 0)
        self.assertEqual(len(model.calls), 3)

    def proofs(self):
        service = self.service()
        found = service.execute('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}, turn_identity=self.turn)
        generated = service.execute('generate_current_term_plan', {'recommendation_kind': 'courses', 'target_term': TERM,
            'course_codes': ['SYN-A', 'SYN-B'], 'target_credits': 6, 'friday_free': 'soft'},
            sources=sources(), turn_identity=self.turn)
        proofs, budget = {}, [0, 0, {}]
        _, search = PersonalDialogueService._project('search_current_term_courses', found, {}, 'synthetic', proofs, 0, budget, set())
        _, plan = PersonalDialogueService._project('generate_current_term_plan', generated, {}, 'synthetic', proofs, 1, budget, set())
        return proofs, search['evidence_id'], plan['evidence_id']

    def test_projection_rejects_promoted_availability_and_inconsistent_counts(self):
        result = self.service().execute('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}, turn_identity=self.turn)
        self.assertEqual(project_course_plan_result(result), result)
        for patch in ({'current_availability_verified': True}, {'friday_free_section_count': 2}, {'blocking_reasons': ['arbitrary']}, {'private_path': '/private/not-for-model'}):
            bad = deepcopy(result)
            bad['result']['courses'][0]['observation_summary'].update(patch)
            with self.assertRaises(ValueError):
                project_course_plan_result(bad)

    def test_explanation_preserves_opaque_result_and_validates_extra_evidence(self):
        proofs, search, plan = self.proofs()
        original = deepcopy(proofs)
        content = {'kind': 'answer', 'text': '优先A，两门课程已达到目标；这是已有观察上的组合判断。', 'evidence_ids': ([plan]) + ([search])}
        reply, decision, citations = PersonalDialogueService._final(json.dumps(content), proofs)
        self.assertIn('优先A', reply)
        self.assertEqual(proofs, original)
        self.assertEqual({c['evidence_id'] for c in citations}, {search, plan})
        self.assertEqual(set(decision['evidence_ids']), {search, plan})
        for ref in ['nonexistent', 'old-plan']:
            bad_proofs = deepcopy(proofs)
            bad_proofs['summary-proof'] = {'kind': 'summary'}
            bad_proofs['old-plan'] = {**deepcopy(proofs[plan]), 'superseded': True}
            content['evidence_ids'] = [plan, ref]
            with self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._final(json.dumps(content), bad_proofs)

    def test_explanation_cannot_promote_candidate_search_or_official_outcomes(self):
        proofs, search, plan = self.proofs()
        for ref, text in [(plan, '保证正式可选'), (plan, '已完成选课')]:
            content = {'kind': 'answer', 'text': text, 'evidence_ids': ([ref]) + ([])}
            with self.assertRaises(PersonalDialogueError):
                PersonalDialogueService._final(json.dumps(content), proofs)

    def test_repeated_current_curriculum_span_reuses_budget_but_changed_source_does_not(self):
        item = {'source_alias': 'SYNTHETIC-PLAN-v1', 'locator': {'physical_page': 1, 'char_start': 0, 'char_end': 8},
            'text': '合成课程为选修。', 'page': 1, 'required_context': [{'page': 1, 'text': '合成课程模块'}]}
        proofs, budget = {}, [0, 0, {}]
        def project(row):
            return PersonalDialogueService._project('search_my_curriculum',
                {'status': 'pending_review', 'matches': [row]}, {'limit': 1}, 'synthetic', proofs, 1, budget, set())
        first_local, first_remote = project(item)
        before = deepcopy(budget)
        _, repeated = project(item)
        self.assertEqual(repeated['matches'], first_remote['matches'])
        self.assertEqual(budget, before)
        self.assertEqual(len(proofs), 1)
        self.assertEqual(first_local['matches'][0]['required_context'], item['required_context'])
        budget[2]['E'] = EVIDENCE_LIMITS['E']
        _, repeated_full = project(item)
        self.assertEqual(repeated_full['matches'], first_remote['matches'])
        for patch in ({'source_alias': 'SYNTHETIC-PLAN-v2'}, {'required_context': [{'page': 1, 'text': '不同合成模块'}]}):
            local, remote = project({**item, **patch})
            self.assertEqual(remote['matches'], [])
            self.assertEqual(local['projection_omitted_matches'], 1)
            self.assertEqual(remote['projection_omitted_matches'], 1)
            self.assertIn('上下文', remote['projection_note'])

    def test_remaining_budget_reaches_model_and_exhaustion_allows_final_answer(self):
        service = self.service()
        def execute(name, args):
            if name == 'get_my_context':
                return {'status': 'available', 'operations': [], 'api_operations': [],
                    'capabilities': {'course_data_test': True}, 'course_data_test': service.describe()}
            return service.execute(name, args, turn_identity=self.turn)
        def conclude(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertEqual(result['execution_budget']['remaining_tool_calls'], 0)
            self.assertEqual([tool["function"]["name"] for tool in tools], ["finish_answer"])
            return final({'kind': 'answer', 'text': '本轮返回了课程条件测试资料，请按实际方案核对。', 'evidence_ids': ([result['evidence_id']])})
        model = ScriptedModel([call('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}), conclude])
        response, messages = PersonalDialogueService(model, lambda: {'scope_key': 'synthetic-only'}, execute,
            max_tools=2).run('查找Synthetic课程', data_use='personal_retrieval')
        self.assertEqual(response['status'], 'completed')
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(response['tool_results']), 2)

    def test_candidate_expansion_cannot_consume_reserved_calculation_evidence(self):
        service = self.service()
        found = service.execute('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}, turn_identity=self.turn)
        generated = service.execute('generate_current_term_plan', {'recommendation_kind': 'courses', 'target_term': TERM,
            'course_codes': ['SYN-A', 'SYN-B'], 'target_credits': 6, 'friday_free': 'soft'},
            sources=sources(), turn_identity=self.turn)
        proofs, budget = {}, [COURSE_SEARCH_EVIDENCE_LIMIT, 0, {'T': COURSE_SEARCH_EVIDENCE_LIMIT}]
        _, remote = PersonalDialogueService._project('search_current_term_courses', found, {}, 'synthetic', proofs, 0, budget, set())
        self.assertEqual(remote['reason'], 'course_search_budget_reserved_for_calculation')
        self.assertNotIn('result', remote)
        self.assertEqual(budget[2]['T'], COURSE_SEARCH_EVIDENCE_LIMIT)
        _, calculated = PersonalDialogueService._project('generate_current_term_plan', generated, {}, 'synthetic', proofs, 1, budget, set())
        self.assertIn('evidence_id', calculated)
        self.assertTrue(calculated['result']['options'])

    def test_explicit_user_credit_bounds_override_model_initial_tightening(self):
        for model_min, model_max in ((7, 7.5), (3, 10)):
            service = self.service()
            service.execute('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}, turn_identity=self.turn)
            result = service.execute('generate_current_term_plan', {'recommendation_kind': 'courses', 'target_term': TERM,
                'course_codes': ['SYN-A', 'SYN-B', 'SYN-C'], 'target_credits': model_min, 'max_credits': model_max, 'friday_free': 'soft'},
                sources=sources(), turn_identity=self.turn,
                policy_context={'user_message': '给一个至少6学分、最多8学分的组合，尽量周五空闲。',
                    'hard_preferences': [], 'active_plan_ref': None})
            self.assertEqual(result['status'], 'available', result)
            self.assertEqual(result['result']['constraints']['target_credits'], '6')
            self.assertEqual(result['result']['constraints']['max_credits'], '8')
            self.assertTrue(any(o['total_credits'] == '6' for o in result['result']['options']))

    def comparative_plan(self):
        self.data['courses'][0]['definitions'][0]['fields']['credits']['value'] = '4'
        service = self.service()
        found = service.execute('search_current_term_courses', {'query': 'Synthetic', 'target_term': TERM}, turn_identity=self.turn)
        generated = service.execute('generate_current_term_plan', {'recommendation_kind': 'courses', 'target_term': TERM,
            'course_codes': ['SYN-A', 'SYN-B', 'SYN-C'], 'target_credits': 6, 'friday_free': 'soft'},
            sources=sources(), turn_identity=self.turn)
        return service, found, generated

    def test_pairwise_facts_separate_shared_courses_from_actual_replacements(self):
        _, _, generated = self.comparative_plan()
        original = deepcopy(generated)
        facts = plan_reasoning_facts(generated['result'])
        pair = facts['pairwise_changes'][0]
        self.assertEqual(pair, {'left': 'A', 'right': 'B', 'shared_course_codes': ['SYN-A'],
            'left_only_course_codes': ['SYN-B'], 'right_only_course_codes': ['SYN-C'],
            'left_only_course_names': ['Synthetic SYN-B'], 'right_only_course_names': ['Synthetic SYN-C'],
            'changed_class_course_codes': []})
        self.assertEqual(facts['options'][1]['friday_observed_slots'], [{'course_code': 'SYN-C', 'slot': '周五第1、2节'}])
        self.assertFalse(facts['duration_mapping_verified'])
        self.assertEqual(facts['credit_target'], {'minimum': '6', 'maximum': None})
        self.assertEqual(facts['options'][0]['excess_above_minimum'], '1')
        self.assertIn('未返回', facts['interpretation']['comparison'])
        aggregate = facts['returned_option_summary']
        self.assertEqual(aggregate['friday_free_labels'], ['A'])
        self.assertIn('B', aggregate['friday_occupied_labels'])
        self.assertEqual(aggregate['scope'], 'returned_options_only')
        self.assertEqual(generated, original)
        # Same course in a different class is a class change, not a new course.
        generated['result']['options'][1]['classes'][0]['class_ref'] = 'CLS-' + 'c' * 44
        pair = plan_reasoning_facts(generated['result'])['pairwise_changes'][0]
        self.assertEqual(pair['changed_class_course_codes'], ['SYN-A'])

    def test_candidate_screening_keeps_per_course_causes_and_source_version(self):
        _, found, generated = self.comparative_plan()
        search = found['result']
        for row, reason in zip(search['courses'], ['excluded_or_unknown_weekday_period', 'historical_nonpositive_capacity']):
            row['observation_summary'].update(usable_section_count=0, friday_free_section_count=0, blocking_reasons=[reason])
        facts = plan_reasoning_facts(generated['result'], [('T-search', search)])
        self.assertEqual([r['reasons'] for r in facts['candidate_screening']], [['星期或节次资料不完整'], ['历史余量非正']])
        for field, value in [('data_version', 'different-version'), ('target_term', 'different-term')]:
            stale = {**search, field: value}
            self.assertEqual(plan_reasoning_facts(generated['result'], [('T-old', stale)])['candidate_screening'], [])

    def test_specific_followup_uses_saved_classes_without_reading_sources_or_solving(self):
        service, _, generated = self.comparative_plan()
        before = deepcopy(service._plans)
        def execute(name, args):
            if name == 'get_my_context':
                return {'status': 'available', 'operations': [], 'api_operations': [],
                    'capabilities': {'course_data_test': True}, 'course_data_test': {'status': 'ready', 'target_term': TERM}}
            self.assertEqual(name, 'get_current_term_plan')
            return service.execute(name, args)
        def explain(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            facts = result['reasoning_facts']
            self.assertEqual(facts['origin'], 'owned_last_computation')
            self.assertEqual(facts['options'][1]['friday_observed_slots'][0]['slot'], '周五第1、2节')
            self.assertEqual(result['result']['computation_summary']['credit_target']['minimum'], '6')
            self.assertEqual(result['answer_context']['current_conditions']['target_credits'], '6')
            self.assertIn('finish_answer', result['answer_context']['delivery'])
            self.assertIn('半天', result['reasoning_facts']['interpretation']['time'])
            return final({'kind': 'answer', 'text': 'B比A换掉了Synthetic B，加入Synthetic C；保存的观察是周五第1、2节，不能换算成半天。目标仍是至少6学分。', 'evidence_ids': ([result['evidence_id']]) + ([])})
        model = ScriptedModel([call('get_current_term_plan', {}), explain])
        with patch.object(CurrentTermCourseDataTest, 'from_delivery', side_effect=AssertionError('no source read')), \
                patch.object(CurrentTermCourseDataTest, 'recommend', side_effect=AssertionError('no solver')):
            response, _ = PersonalDialogueService(model, lambda: {'scope_key': 'synthetic-only'}, execute).run(
                '只比较A和B，周五到底占哪几节？不要重算。', data_use='personal_retrieval')
        self.assertEqual(response['status'], 'completed', response)
        self.assertEqual(response['repair_attempts'], 0)
        self.assertIn('周五第1、2节', response['reply'])
        self.assertTrue(response['reply'].startswith('B比A'))
        self.assertEqual(service._plans, before)

    def test_saved_detail_validation_and_legacy_unknown_do_not_invent_free_time(self):
        service, _, _ = self.comparative_plan()
        inspected = service.execute('get_current_term_plan', {})
        for field, value in [('weekday_period_text', ['半天']), ('course_code', 'SYN-FOREIGN'), ('private_path', '/private/source')]:
            bad = deepcopy(inspected)
            bad['result']['computation_summary']['options'][0]['classes'][0][field] = value
            with self.assertRaises(ValueError):
                project_course_plan_result(bad)
        legacy = deepcopy(inspected)
        for row in legacy['result']['computation_summary']['options']:
            row.pop('classes')
        self.assertEqual(project_course_plan_result(legacy), legacy)
        facts = plan_reasoning_facts(legacy['result'])
        self.assertIsNone(facts['options'][0]['friday_observed_slots'])
        self.assertEqual(facts['pairwise_changes'], [])
        self.assertIsNone(facts['returned_option_summary']['shared_course_codes'])

    def test_comparison_context_shares_budget_and_never_displaces_actual_plan(self):
        _, _, generated = self.comparative_plan()
        from course_assistant.personal_dialogue import _json
        size = len(_json({'result': generated['result']}).encode('utf-8'))
        used = EVIDENCE_LIMITS['T'] - size - 1
        budget, proofs = [used, 0, {'T': used}], {}
        _, remote = PersonalDialogueService._project('generate_current_term_plan', generated, {}, 'synthetic', proofs, 0, budget, set())
        self.assertIn('result', remote)
        self.assertNotIn('reasoning_facts', remote)
        self.assertIn('reasoning_facts_omitted', remote)
        self.assertLessEqual(budget[2]['T'], EVIDENCE_LIMITS['T'])

    def test_saved_analysis_still_rejects_outdated_and_foreign_evidence(self):
        service, _, _ = self.comparative_plan()
        inspected = service.execute('get_current_term_plan', {})
        proofs = {}
        _, remote = PersonalDialogueService._project('get_current_term_plan', inspected, {}, 'synthetic', proofs, 0, [0, 0, {}], set())
        answer = {'kind': 'answer', 'text': '这里只比较保存的方案。', 'evidence_ids': [remote['evidence_id']]}
        content = json.dumps(answer)
        self.assertIn('这里只比较保存', PersonalDialogueService._final(content, proofs)[0])
        proofs[remote['evidence_id']]['superseded'] = True
        with self.assertRaises(PersonalDialogueError):
            PersonalDialogueService._final(content, proofs)
        proofs[remote['evidence_id']]['superseded'] = False
        answer['evidence_ids'].append('R-foreign')
        with self.assertRaises(PersonalDialogueError):
            PersonalDialogueService._final(json.dumps(answer), proofs)

    def test_model_controls_prose_order_without_dropping_supporting_data(self):
        proofs, search, plan = self.proofs()
        proofs['R-synthetic'] = {'kind': 'record', 'evidence_id': 'R-synthetic', 'tool_index': 0}
        content = {'kind': 'answer', 'text': '\n\n'.join(['合成成绩资料说明。', '首选A，优先满足原学分与空闲条件。']), 'evidence_ids': (['R-synthetic']) + ([plan]) + ([search])}
        reply, decision, citations = PersonalDialogueService._final(json.dumps(content), proofs)
        self.assertEqual(reply, content['text'])
        self.assertIn('合成成绩资料说明。', reply)
        self.assertEqual(citations[0]['evidence_id'], 'R-synthetic')

    def test_revise_context_retires_old_payload_without_rewriting_saved_tool_results(self):
        for deliberately_stale in (False, True):
            with self.subTest(deliberately_stale=deliberately_stale):
                service, _, _ = self.comparative_plan()
                executed, refs = [], {}

                def execute(name, args):
                    executed.append(name)
                    if name == 'get_my_context':
                        return {'status': 'available', 'operations': [], 'api_operations': [],
                            'capabilities': {'course_data_test': True}}
                    return service.execute(name, args, sources=sources(), turn_identity=self.turn)

                def revise(messages, tools):
                    old = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
                    refs['old'] = old['evidence_id']
                    plan = old['result']
                    return call('update_current_term_plan', {'plan_ref': plan['plan_ref'],
                        'expected_revision': plan['revision'], 'action': 'revise', 'friday_free': 'hard'}, 'revise')

                def answer(messages, tools):
                    old, new = [json.loads(row['content']) for row in messages if row['role'] == 'tool']
                    self.assertEqual(old['citation_status'], 'superseded')
                    self.assertNotIn('result', old)
                    self.assertNotIn('reasoning_facts', old)
                    self.assertNotIn('evidence_id', old)
                    self.assertIn(new['evidence_id'], old['current_evidence_ids'])
                    self.assertEqual(new['result']['revision'], 1)
                    refs['new'] = new['evidence_id']
                    selected = [refs['old'], refs['new']] if deliberately_stale else [refs['new']]
                    return call('finish_answer', {'kind': 'answer', 'text': '已按原学分条件重新计算，周五为硬条件。',
                        'evidence_ids': selected}, 'answer')

                def repair(messages, tools):
                    self.assertEqual([t['function']['name'] for t in tools], ['finish_answer'])
                    return call('finish_answer', {'kind': 'answer', 'text': '已按原学分条件重新计算，周五为硬条件。',
                        'evidence_ids': [refs['new']]}, 'repair')

                steps = [call('get_current_term_plan', {}), revise, answer] + ([repair] if deliberately_stale else [])
                response, history = PersonalDialogueService(ScriptedModel(steps),
                    lambda: {'scope_key': 'synthetic-only'}, execute).run(
                        '保留原条件，把周五设为必须空。', data_use='personal_retrieval')
                self.assertEqual(response['status'], 'completed', response)
                self.assertEqual(response['repair_attempts'], int(deliberately_stale))
                self.assertEqual(response['model_decision']['evidence_ids'], [refs['new']])
                self.assertEqual(executed, ['get_my_context', 'get_current_term_plan', 'update_current_term_plan'])
                self.assertEqual(response['tool_results'][1]['result']['result']['revision'], 0)
                raw_old = next(json.loads(row['content']) for row in history if row['role'] == 'tool')
                self.assertEqual(raw_old['result']['revision'], 0)
                if deliberately_stale:
                    self.assertIn('course_plan_revision_outdated', str(response['validation_diagnostics']))


if __name__ == '__main__':
    unittest.main()
