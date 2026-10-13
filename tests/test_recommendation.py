"""Engineering synthetic expectations, not a human-reviewed real-school gold."""
from copy import deepcopy
from itertools import combinations, product
from pathlib import Path
import unittest

from course_assistant.importer import load_json, validate_demo, digest
from course_assistant.audit import audit_progress
from course_assistant.planner import SearchBudget, generate_plans, validate_plan
from course_assistant.recommendation import (recommend, forecast_contribution, compare_recommendations,
                                             revalidate_option)

ROOT = Path(__file__).resolve().parents[1]


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.demo = load_json(ROOT / 'demo/recommendation_bundle.json')
        self.constraints = deepcopy(self.demo['constraints'])

    def run_recommend(self, **kwargs):
        return recommend(self.demo, self.constraints, **kwargs)

    def section(self, identifier):
        return next(s for s in self.demo['offering_snapshot']['records'] if s['section_id'] == identifier)

    def test_fixture_validation_and_all_course_descriptions_are_synthetic(self):
        self.assertEqual(validate_demo(self.demo), self.demo)
        self.assertTrue(all(c['data_kind'] == 'synthetic' and c['description_evidence']['data_kind'] == 'synthetic'
                            for c in self.demo['courses']))
        self.assertEqual(self.demo['academic_snapshot']['records'][1]['completion_status'], 'in_progress')
        self.assertEqual(self.demo['academic_snapshot']['records'][2]['completion_status'], 'unknown')

    def test_initial_real_course_differences_and_tradeoffs(self):
        before = digest(self.demo)
        result = self.run_recommend()
        self.assertEqual(result['status'], 'feasible')
        self.assertEqual(len(result['plans']), 3)
        first, second = result['plans'][:2]
        self.assertEqual(set(first['course_ids']), {'DEMO-AI', 'DEMO-DATA', 'DEMO:PE'})
        self.assertEqual(set(second['course_ids']), {'DEMO-DATA', 'DEMO-MODEL', 'DEMO:PE'})
        self.assertEqual(first['metrics']['interest_match_count'], 1)
        self.assertEqual(second['metrics']['friday_meeting_count'], 0)
        for plan in result['plans']:
            self.assertEqual(plan['metrics']['module_new_contribution'], 6)
            self.assertEqual(plan['total_credits'], 7)
            self.assertIn('DEMO-PE-01', plan['section_ids'])
            self.assertEqual(validate_plan(plan['section_ids'], self.demo['offering_snapshot'], self.demo['student'],
                                           plan['constraints'], self.demo['academic_snapshot']['records'])['result'], 'pass')
            self.assertEqual(set(plan['constraints']['requested_course_ids']), set(plan['course_ids']))
        self.assertEqual(digest(self.demo), before)
        self.assertEqual(result['constraints'], self.constraints)

    def test_soft_to_hard_friday_returns_exactly_one_without_changing_locks(self):
        self.constraints['friday_free'] = 'hard'
        result = self.run_recommend()
        self.assertEqual(result['status'], 'feasible')
        self.assertEqual(len(result['plans']), 1)
        self.assertEqual(set(result['plans'][0]['course_ids']), {'DEMO-DATA', 'DEMO-MODEL', 'DEMO:PE'})
        self.assertEqual(result['plans'][0]['constraints']['locked_section_ids'], ['DEMO-PE-01'])
        self.assertEqual(result['plans'][0]['constraints']['preloaded_section_ids'], ['DEMO-PE-01'])

    def test_protected_friday_conflict_is_scoped_infeasible(self):
        self.constraints['friday_free'] = 'hard'
        self.section('DEMO-PE-01')['meetings'][0]['day_of_week'] = 5
        result = self.run_recommend()
        self.assertEqual(result['status'], 'scoped_infeasible')
        self.assertEqual(result['plans'], [])
        self.assertTrue(any(d['code'] == 'friday_free' for d in result['diagnostics']))
        self.assertIn('DEMO-PE-01', result['constraints']['locked_section_ids'])

    def test_unknown_week_calendar_exam_and_prerequisite_do_not_become_pass(self):
        for field in ('week_numbers', 'calendar', 'exam', 'prerequisite'):
            with self.subTest(field=field):
                self.setUp()
                self.constraints['friday_free'] = 'hard'
                section = self.section('DEMO-DATA-01')
                if field == 'week_numbers': section['meetings'][0]['week_numbers'] = None
                if field == 'calendar': section['meetings'][0]['academic_calendar_id'] = None
                if field == 'exam': section['exam_state'], section['exams'] = 'unknown', None
                if field == 'prerequisite': section['prerequisite_course_ids'] = None
                result = self.run_recommend()
                self.assertEqual(result['plans'], [])
                self.assertTrue(result['pending_plans'])
                self.assertTrue(all(p['result'] == 'unknown' for p in result['pending_plans']))

    def test_partial_scope_and_missing_courses_do_not_claim_no_solution(self):
        self.constraints['friday_free'] = 'hard'
        self.demo['offering_snapshot']['records'] = [s for s in self.demo['offering_snapshot']['records']
                                                    if s['course_id'] != 'DEMO-DATA']
        self.assertEqual(self.run_recommend()['status'], 'incomplete_data')
        self.demo['offering_snapshot']['completeness'] = 'partial'
        self.assertEqual(self.run_recommend()['status'], 'incomplete_data')

    def test_global_budget_includes_prechecks_and_does_not_reset_for_subsets(self):
        result = self.run_recommend(max_nodes=12)
        self.assertEqual(result['status'], 'search_limit')
        self.assertEqual(result['search']['nodes_visited'], 12)
        self.assertIn('course_precheck', result['search']['phases'])
        self.assertIn('protected_precheck', result['search']['phases'])
        self.assertLess(result['search']['course_subsets_visited'], 8)
        self.assertFalse(result['search']['globally_optimal'])

    def test_candidate_retention_is_separate_from_search_exhaustion(self):
        result = self.run_recommend(max_candidates=1, max_diagnostics=1)
        self.assertEqual(result['status'], 'feasible')
        self.assertTrue(result['search']['candidate_retention_clipped'])
        self.assertFalse(result['search']['limit_reached'])
        self.assertLessEqual(len(result['plans']), 1)
        self.assertLessEqual(len(result['diagnostics']), 1)
        self.assertGreater(result['search']['validated_plan_count'], 1)

    def test_course_pool_clipping_is_explicit(self):
        result = self.run_recommend(max_pool_courses=1)
        self.assertEqual(result['status'], 'search_limit')
        self.assertTrue(result['search']['candidate_scope_clipped'])
        self.assertFalse(result['search']['globally_optimal'])

    def test_elapsed_budget(self):
        ticks = iter([0, 0, 1])
        budget = SearchBudget(20, max_seconds=0.5, clock=lambda: next(ticks))
        self.assertTrue(budget.spend('first'))
        self.assertFalse(budget.spend('second'))
        self.assertEqual(budget.report()['limit_reason'], 'elapsed_time')

    def test_leaf_sink_receives_candidates_before_legacy_top_three(self):
        old = load_json(ROOT / 'demo/bundle.json')
        leaves = []
        result = generate_plans(old['offering_snapshot'], old['student'], old['constraints'],
                                old['academic_snapshot']['records'], limit=1, leaf_sink=leaves.append)
        self.assertEqual(len(result['plans']), 1)
        self.assertEqual(len([p for p in leaves if p['result'] == 'pass']), 9)
        self.assertEqual(result['search']['validated_plan_count'], 9)

    def test_small_pool_matches_independent_slot_and_credit_enumeration(self):
        # Enumerate fixture slots directly; expected set does not call planner.
        sections = self.demo['offering_snapshot']['records']
        optional = self.demo['recommendation']['optional_course_ids']
        expected = set()
        for length in range(4):
            for subset in combinations(optional, length):
                choices = [[s for s in sections if s['course_id'] == cid] for cid in subset]
                for combination in product(*choices):
                    if sum(s['credits'] for s in combination) < 6:
                        continue
                    all_sections = [self.section('DEMO-PE-01'), *combination]
                    occupied = set()
                    collision = False
                    for section in all_sections:
                        for meeting in section['meetings']:
                            slots = {(season, week, meeting['day_of_week'], period)
                                     for season in meeting['season_codes'] for week in meeting['week_numbers']
                                     for period in meeting['period_indices']}
                            collision |= bool(slots & occupied)
                            occupied |= slots
                    if not collision:
                        expected.add(tuple(sorted(s['section_id'] for s in all_sections)))
        actual = self.run_recommend(limit=10)
        self.assertEqual({tuple(sorted(p['section_ids'])) for p in actual['plans']}, expected)
        self.assertEqual(len(expected), 7)

    def test_interest_cannot_be_inferred_from_course_name(self):
        ai = next(c for c in self.demo['courses'] if c['course_id'] == 'DEMO-AI')
        ai.pop('description_evidence')
        result = self.run_recommend()
        self.assertTrue(all(p['metrics']['interest_match_count'] == 0 for p in result['plans']))
        self.assertEqual(result['plans'][0]['metrics']['friday_meeting_count'], 0)

    def test_user_time_priority_changes_best_option(self):
        result = self.run_recommend(intent={'priority': ['goal', 'time', 'interest', 'change']})
        self.assertEqual(result['plans'][0]['metrics']['friday_meeting_count'], 0)

    def test_earned_module_course_is_not_counted_again(self):
        section = self.section('DEMO-AI-01')
        self.demo['academic_snapshot']['records'].append({'record_id': 'DEMO-AI-EARNED', 'attempt_id': 'DEMO-AI-ATTEMPT',
            'student_key': self.demo['student']['student_key'], 'plan_id': self.demo['student']['plan_id'],
            'record_kind': 'course', 'course_id': 'DEMO-AI', 'completion_status': 'passed',
            'recognition_status': 'not_applicable', 'earned_credits': 3, 'ledger': 'base', 'tags': ['module'],
            'data_kind': 'synthetic', 'snapshot_id': self.demo['academic_snapshot']['snapshot_id'], 'source_refs': section['source_refs']})
        before = digest(self.demo)
        projected = forecast_contribution(self.demo, [section], 'DEMO-ELECTIVE')
        self.assertEqual(projected['module_actual_before'], 3)
        self.assertEqual(projected['module_expected_after'], 3)
        self.assertEqual(projected['module_new_contribution'], 0)
        result = self.run_recommend()
        self.assertEqual(len(result['plans']), 1)
        self.assertNotIn('DEMO-AI', result['plans'][0]['course_ids'])
        self.assertEqual(result['plans'][0]['metrics']['module_new_contribution'], 6)
        self.assertEqual(digest(self.demo), before)

    def test_unknown_rule_is_not_a_reliable_target(self):
        self.demo['rules'][0]['status'] = 'candidate_pending_human_review'
        self.demo['rules'][0]['human_review_required'] = True
        self.assertEqual(self.run_recommend()['status'], 'incomplete_data')
        self.assertEqual(self.run_recommend()['plans'], [])

    def test_revalidation_keeps_module_review_unknown_inside_validation(self):
        option = self.run_recommend()['plans'][0]
        self.demo['rules'][0]['human_review_required'] = True
        validate_demo(self.demo)
        # Low-level class checks still pass; the necessary module rule does not.
        low = validate_plan(option['section_ids'], self.demo['offering_snapshot'], self.demo['student'],
                            option['constraints'], self.demo['academic_snapshot']['records'])
        self.assertEqual(low['result'], 'pass')
        current = revalidate_option(self.demo, option['constraints'], self.demo['recommendation'], option['section_ids'])
        self.assertEqual(current['result'], 'unknown')
        self.assertEqual(current['validation']['result'], 'unknown')
        self.assertEqual(current['metrics']['contribution_state'], 'unknown')
        self.assertTrue(any(c['code'] == 'module_target' and c['result'] == 'unknown'
                            for c in current['validation']['checks']))

    def test_revalidation_preserves_known_failure_over_unknown_module(self):
        option = self.run_recommend()['plans'][0]
        self.demo['rules'][0]['human_review_required'] = True
        self.section('DEMO-AI-01')['available_seats'] = 0
        current = revalidate_option(self.demo, option['constraints'], self.demo['recommendation'], option['section_ids'])
        self.assertEqual(current['validation']['result'], 'fail')

    def test_revalidation_checks_current_required_course_and_exclusion(self):
        option = self.run_recommend()['plans'][1]
        intent = {**self.demo['recommendation'], 'required_course_ids': ['DEMO-AI']}
        current = revalidate_option(self.demo, option['constraints'], intent, option['section_ids'])
        self.assertEqual(current['validation']['result'], 'fail')
        intent = {**self.demo['recommendation'], 'excluded_course_ids': ['DEMO-DATA']}
        current = revalidate_option(self.demo, option['constraints'], intent, option['section_ids'])
        self.assertEqual(current['validation']['result'], 'fail')

    def test_course_exclusion_does_not_remove_protected_member(self):
        result = self.run_recommend(intent={'excluded_course_ids': ['DEMO:PE']})
        self.assertEqual(result['plans'], [])
        self.assertTrue(any(d['code'] == 'excluded_required_course' for d in result['diagnostics']))
        self.assertIn('DEMO-PE-01', result['constraints']['locked_section_ids'])

    def test_cross_course_comparison_uses_each_options_selected_requests(self):
        first, second = self.run_recommend()['plans'][:2]
        compared = compare_recommendations([first, second], self.demo, self.constraints)
        self.assertEqual([p['result'] for p in compared['plans']], ['pass', 'pass'])
        for plan in compared['plans']:
            self.assertEqual(set(plan['constraints']['requested_course_ids']), set(plan['course_ids']))
        self.section('DEMO-AI-01')['available_seats'] = 0
        compared = compare_recommendations([first, second], self.demo, self.constraints)
        self.assertEqual([p['result'] for p in compared['plans']], ['fail', 'pass'])
        self.assertEqual(compared['ranking'][0], 1)

    def test_comparison_refreshes_contribution_interest_and_unknown_facts(self):
        first, second = self.run_recommend()['plans'][:2]
        self.section('DEMO-DATA-01')['credits'] = 2
        ai = next(c for c in self.demo['courses'] if c['course_id'] == 'DEMO-AI')
        ai['description_evidence']['verified'] = False
        compared = compare_recommendations([first, second], self.demo, self.constraints)
        self.assertEqual(compared['plans'][0]['metrics']['module_new_contribution'], 5)
        self.assertEqual(compared['plans'][0]['metrics']['interest_match_count'], 0)
        self.assertEqual([p['result'] for p in compared['plans']], ['fail', 'fail'])
        self.setUp()
        first, second = self.run_recommend()['plans'][:2]
        self.demo['offering_snapshot']['records'] = [s for s in self.demo['offering_snapshot']['records']
                                                    if s['section_id'] != 'DEMO-AI-01']
        compared = compare_recommendations([first, second], self.demo, self.constraints)
        self.assertEqual(compared['plans'][0]['result'], 'unknown')
        self.assertIsNone(compared['plans'][0]['metrics']['module_new_contribution'])

    def test_comparison_respects_new_course_exclusion(self):
        first, second = self.run_recommend()['plans'][:2]
        compared = compare_recommendations([first, second], self.demo, self.constraints,
                                            {'excluded_course_ids': ['DEMO-AI']})
        self.assertEqual([p['result'] for p in compared['plans']], ['fail', 'pass'])

    def test_bad_intent_and_non_synthetic_data_rejected(self):
        for intent in ({'optional_course_ids': ['missing']}, {'target_credits': 'NaN'},
                       {'priority': ['goal', 'interest', 'time', 'time']}):
            with self.subTest(intent=intent), self.assertRaises(ValueError):
                self.run_recommend(intent=intent)
        self.demo['metadata']['data_kind'] = 'real'
        with self.assertRaises(ValueError): self.run_recommend()


if __name__ == '__main__':
    unittest.main()
