"""Pure synthetic structures mirroring shared schema; no real text or codes."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from copy import deepcopy
from decimal import Decimal
from itertools import combinations
import json
from pathlib import Path
import unittest

from course_assistant.local_reference_observations import ObservationCatalog, ReferenceObservationError


def fixture():
    components = {name: [] for name in ('courses', 'course_observations', 'sections', 'section_observations',
                   'meeting_snapshots', 'meetings', 'exams', 'capacity_snapshots')}
    for code, amount, slots in [('SYN-A', '3.0', [(1, [1, 2]), (5, [1, 2])]),
                                 ('SYN-B', '3.0', [(2, [3, 4])]), ('SYN-C', '2.0', [(3, [3, 4])])]:
        cid, sid, oid = 'course-' + code, 'section-' + code, 'obs-' + code
        fields = {key: {'state': 'known', 'value': value, 'assertions': [{'value': value, 'observation_ids': [cid + '-src']}],
                        'unknown_observation_ids': []} for key, value in [('name', 'Synthetic ' + code), ('credits', amount)]}
        components['courses'].append({'course_id': cid, 'code': code, 'code_namespace': 'synthetic-structure-only',
            'definitions': [{'definition_id': cid + '-definition', 'term': '2026-2027-1', 'fields': fields,
                             'observation_ids': [cid + '-src']}], 'observation_ids': [cid + '-src'], 'stable_course_identity_verified': False})
        components['course_observations'].append({'id': cid + '-src', 'course_id': cid, 'term': '2026-2027-1',
                                                  'fields': {'credits': amount, 'name': 'Synthetic ' + code}})
        components['sections'].append({'section_id': sid, 'course_id': cid, 'course_code': code, 'term': '2026-2027-1',
            'source_section_key': 'synthetic-key', 'fields': {'teacher_names': {'state': 'known', 'value': 'MUST-NOT-USE-MERGED'}},
            'observation_ids': [oid], 'meeting_variants': [], 'stable_across_refreshes_verified': False})
        components['section_observations'].append({'id': oid, 'section_id': sid, 'observed_at': '2026-10-14T10:00:00+08:00',
            'fields': {'teacher_names': 'Synthetic Teacher ' + code, 'term_part': 'Synthetic autumn/winter',
                       'personal_state': 'PRIVATE-STATE-SENTINEL', 'private_path': '/Users/PRIVATE-PATH-SENTINEL'},
            'meeting_set_id': oid + '-set', 'exam_id': oid + '-exam', 'capacity_id': oid + '-cap'})
        mids = []
        for index, (day, periods) in enumerate(slots):
            mid = oid + '-meeting-' + str(index); mids.append(mid)
            components['meetings'].append({'id': mid, 'section_id': sid, 'observation_id': oid, 'index': index,
                'fields': {'weekday': day, 'periods': periods, 'week_set': None, 'calendar_version': None,
                           'time_raw': 'synthetic time', 'place_raw': 'synthetic room', 'private_path': '/Users/PRIVATE-PATH-SENTINEL'}})
        components['meeting_snapshots'].append({'id': oid + '-set', 'section_id': sid, 'observation_id': oid,
                                                'meeting_ids': mids, 'status': 'partial', 'declared_state': None})
        components['exams'].append({'id': oid + '-exam', 'section_id': sid, 'observation_id': oid,
                                    'observed_at': '2026-10-14T10:00:00+08:00',
                                    'fields': {'date': None, 'start': None, 'end': None, 'timezone': None, 'status': 'explicit_pending',
                                               'raw': 'private text should not be projected'}})
        components['capacity_snapshots'].append({'id': oid + '-cap', 'section_id': sid, 'observation_id': oid,
            'observed_at': '2026-10-14T10:00:00+08:00', 'fields': {'capacity': 20, 'remaining': 10,
            'pending_raw': {'all': 'PRIVATE-COUNTER-SENTINEL'}, 'male_raw': 'private male count', 'female_raw': 'private female count',
            'raw': 'private counters', 'hidden_counter_semantics_verified': False}})
    return components


def add_observation(components, code='SYN-A'):
    original = next(o for o in components['section_observations'] if o['id'] == 'obs-' + code)
    oid = original['id'] + '-new'
    new = {**deepcopy(original), 'id': oid, 'observed_at': '2026-10-14T11:00:00+08:00',
           'meeting_set_id': oid + '-set', 'exam_id': oid + '-exam', 'capacity_id': oid + '-cap'}
    new['fields']['teacher_names'] = 'Synthetic NEW teacher'
    components['section_observations'].append(new)
    section = next(s for s in components['sections'] if s['section_id'] == original['section_id'])
    section['observation_ids'].append(oid)
    mid = oid + '-meeting'
    components['meeting_snapshots'].append({'id': oid + '-set', 'section_id': section['section_id'], 'observation_id': oid,
                                            'meeting_ids': [mid], 'status': 'partial', 'declared_state': None})
    components['meetings'].append({'id': mid, 'section_id': section['section_id'], 'observation_id': oid, 'index': 0,
                                   'fields': {'weekday': 4, 'periods': [7, 8], 'week_set': None, 'calendar_version': None,
                                              'time_raw': 'synthetic new time', 'place_raw': 'synthetic new room'}})
    components['exams'].append({'id': oid + '-exam', 'section_id': section['section_id'], 'observation_id': oid,
                               'observed_at': new['observed_at'], 'fields': {'date': '2026-12-01', 'start': '10:00',
                               'end': '11:00', 'timezone': None, 'status': 'synthetic_known_local_time'}})
    components['capacity_snapshots'].append({'id': oid + '-cap', 'section_id': section['section_id'], 'observation_id': oid,
                                            'observed_at': new['observed_at'], 'fields': {'capacity': 30, 'remaining': 2}})


class LocalReferenceObservationTests(unittest.TestCase):
    def setUp(self):
        self.components = fixture()
        self.enabled = True

    def catalog(self, **kwargs):
        return ObservationCatalog(self.components, kwargs.pop('dataset_version', 'SYN-DATA-v1'),
                                  kwargs.pop('authorize', lambda: self.enabled),
                                  kwargs.pop('allowed_course_codes', ['SYN-A', 'SYN-B', 'SYN-C']), **kwargs)

    def test_exact_pool_mapping_no_similar_name_or_case_fallback(self):
        catalog = self.catalog(allowed_course_codes=['SYN-A'])
        result = catalog.lookup('SYN-A')
        self.assertEqual(result['mapping_status'], 'exact_code_observed_reference')
        self.assertEqual(result['course']['credits'], '3.0')
        for code in ('syn-a', 'SYN-B', ' SYN-A', 'same synthetic name'):
            with self.subTest(code=code), self.assertRaises(ReferenceObservationError): catalog.lookup(code)
        self.assertEqual(result['result'], 'unknown')
        self.assertFalse(result['personal_data_used'])

    def test_full_pagination_is_bound_to_code_version_term_and_pool(self):
        add_observation(self.components)
        catalog = self.catalog()
        first = catalog.lookup('SYN-A', limit=1)
        second = catalog.lookup('SYN-A', limit=1, cursor=first['pagination']['next_cursor'])
        self.assertEqual(first['pagination']['total'], 2)
        self.assertTrue(first['pagination']['has_more'])
        self.assertFalse(second['pagination']['has_more'])
        self.assertEqual({o['observation_id'] for r in (first, second) for o in r['observations']}, {'obs-SYN-A', 'obs-SYN-A-new'})
        for another, code in ((self.catalog(dataset_version='SYN-DATA-v2'), 'SYN-A'),
                               (self.catalog(allowed_course_codes=['SYN-A']), 'SYN-A'), (catalog, 'SYN-B')):
            with self.assertRaises(ReferenceObservationError):
                another.lookup(code, cursor=first['pagination']['next_cursor'])
        with self.assertRaises(ReferenceObservationError): catalog.lookup('SYN-A', cursor='not-json')
        cursor = json.loads(urlsafe_b64decode(first['pagination']['next_cursor']))
        cursor['term'] = 'synthetic-other-term'
        with self.assertRaises(ReferenceObservationError):
            catalog.lookup('SYN-A', cursor=urlsafe_b64encode(json.dumps(cursor).encode()).decode())

    def test_latest_bundle_preserves_own_time_exam_capacity_and_all_fragments(self):
        add_observation(self.components)
        rows = self.catalog().lookup('SYN-A')['observations']
        original = next(o for o in rows if o['observation_id'] == 'obs-SYN-A')
        latest = next(o for o in rows if o['observation_id'] == 'obs-SYN-A-new')
        self.assertEqual(len(original['meetings']), 2)
        self.assertEqual(original['capacity']['remaining'], 10)
        self.assertEqual(original['exam']['date'], None)
        self.assertEqual(latest['teacher_names'], 'Synthetic NEW teacher')
        self.assertEqual(latest['capacity']['remaining'], 2)
        self.assertEqual(latest['exam']['date'], '2026-12-01')
        self.assertEqual([m['weekday'] for m in latest['meetings']], [4])
        self.assertNotIn('MUST-NOT-USE-MERGED', json.dumps(rows))

    def test_wrong_parent_mapping_fails_closed(self):
        self.components['sections'][0]['course_id'] = 'course-SYN-B'
        with self.assertRaises(ReferenceObservationError) as caught: self.catalog()
        self.assertEqual(caught.exception.code, 'invalid_course_mapping')

    def test_ambiguous_course_parent_identity_is_not_accepted(self):
        self.components['courses'][1]['course_id'] = self.components['courses'][0]['course_id']
        with self.assertRaises(ReferenceObservationError) as caught: self.catalog()
        self.assertEqual(caught.exception.code, 'ambiguous_course_identity')

    def test_unknown_or_naive_observation_time_never_claims_latest(self):
        add_observation(self.components)
        original = self.components['section_observations'][0]
        original['observed_at'] = '2026-10-14T14:00:00'
        for name in ('exams', 'capacity_snapshots'):
            next(r for r in self.components[name] if r['observation_id'] == original['id'])['observed_at'] = original['observed_at']
        result = self.catalog().conditional_recommend(target_credits=5, limit=10)
        selected = next(s for o in result['options'] for s in o['sections'] if s['course_code'] == 'SYN-A')
        self.assertEqual(selected['observation_selection'], 'deterministic_bundle_with_unordered_observations')
        self.assertIn('observation_time_order_unknown', selected['unknowns'])

    def test_cross_observation_bundle_or_time_is_never_stitched(self):
        add_observation(self.components)
        self.components['section_observations'][0]['capacity_id'] = 'obs-SYN-A-new-cap'
        with self.assertRaises(ReferenceObservationError) as caught: self.catalog().lookup('SYN-A')
        self.assertEqual(caught.exception.code, 'observation_bundle_mismatch')
        self.setUp()
        self.components['capacity_snapshots'][0]['observed_at'] = '2026-10-14T12:00:00+08:00'
        with self.assertRaises(ReferenceObservationError): self.catalog().lookup('SYN-A')

    def test_missing_observation_or_meeting_cannot_be_called_complete(self):
        self.components['section_observations'].pop()
        with self.assertRaises(ReferenceObservationError): self.catalog()
        self.setUp()
        self.components['meeting_snapshots'][0]['meeting_ids'].pop()
        with self.assertRaises(ReferenceObservationError) as caught: self.catalog().lookup('SYN-A')
        self.assertEqual(caught.exception.code, 'incomplete_meeting_set')

    def test_revocation_checks_before_queries_and_before_return(self):
        catalog = self.catalog()
        self.enabled = False
        for action in (lambda: catalog.lookup('SYN-A'), lambda: catalog.conditional_recommend(target_credits=5)):
            with self.assertRaises(ReferenceObservationError): action()
        count = [0]
        def live():
            count[0] += 1
            return count[0] < 4
        self.enabled = True
        catalog = self.catalog(authorize=live)
        with self.assertRaises(ReferenceObservationError): catalog.lookup('SYN-A')

    def test_active_grant_scope_or_version_change_invalidates_built_catalog(self):
        grant = {'active': True, 'scope_version': 'synthetic-scope-v1', 'allowed_codes': ['SYN-A', 'SYN-B', 'SYN-C']}
        catalog = self.catalog(authorize=lambda: deepcopy(grant))
        self.assertTrue(catalog.lookup('SYN-A')['observations'])
        grant['allowed_codes'] = ['SYN-B']
        for action in (lambda: catalog.lookup('SYN-A'), lambda: catalog.conditional_recommend(target_credits=5)):
            with self.assertRaises(ReferenceObservationError) as caught: action()
            self.assertEqual(caught.exception.code, 'reference_scope_changed')

    def test_live_registry_authorizer_with_private_path_controls_is_supported(self):
        state = {'grant_version': 'synthetic-grant-v1', 'grant': {'active': True},
                 'versions': {'corpus_version': 'synthetic-corpus-v1'},
                 'controls': {'dataset_path': Path('synthetic-private/dataset.json'),
                              'requirements_path': Path('synthetic-private/requirements.json')}}
        catalog = self.catalog(authorize=lambda: deepcopy(state))
        result = catalog.lookup('SYN-A')
        self.assertTrue(result['observations'])
        self.assertNotIn('synthetic-private', json.dumps(result))
        state['grant_version'] = 'synthetic-grant-v2'
        with self.assertRaises(ReferenceObservationError) as caught: catalog.lookup('SYN-A')
        self.assertEqual(caught.exception.code, 'reference_scope_changed')
        state['grant_version'] = 'synthetic-grant-v1'
        state['versions']['corpus_version'] = 'synthetic-corpus-v2'
        with self.assertRaises(ReferenceObservationError): catalog.lookup('SYN-A')

    def test_personal_components_and_hidden_counters_or_paths_never_project(self):
        catalog = self.catalog()
        text = json.dumps(catalog.lookup('SYN-A')) + json.dumps(catalog.conditional_recommend(target_credits=5))
        for sentinel in ('PRIVATE-STATE-SENTINEL', 'PRIVATE-COUNTER-SENTINEL', 'PRIVATE-PATH-SENTINEL', 'pending_raw', 'male_raw', 'female_raw'):
            self.assertNotIn(sentinel, text)
        self.components['restricted_personal_page_observations'] = []
        with self.assertRaises(ReferenceObservationError): self.catalog()

    def test_private_absolute_paths_and_session_urls_are_not_text_evidence(self):
        for value in ('/tmp/private-file.json', '/private/var/cache/personal', '/Volumes/private/file.xlsx',
                      'https://example.invalid?session=private-cookie', 'C:\\Users\\private\\file', '../private/export'):
            with self.subTest(value=value):
                self.components['meetings'][0]['fields']['time_raw'] = value
                result = self.catalog().lookup('SYN-A')
                self.assertIsNone(result['observations'][0]['meetings'][0]['time_raw'])
                self.assertNotIn(value, json.dumps(result))

    def test_unknown_week_calendar_eligibility_exam_and_no_real_academics_are_explicit(self):
        result = self.catalog().conditional_recommend(target_credits=5)
        self.assertGreaterEqual(len(result['options']), 2)
        self.assertEqual(result['assumptions']['target_new_credits'], '5')
        self.assertEqual(result['assumptions']['academic_progress'], 'not_provided')
        for option in result['options']:
            self.assertEqual(option['result'], 'unknown')
            self.assertEqual(option['status'], 'conditional_reference')
            self.assertIn('student_eligibility_unknown', option['unknowns'])
            self.assertIn('exam_time_or_timezone_unknown', option['unknowns'])
            self.assertIn('week_set_unknown', option['unknowns'])
        self.assertNotIn('pass', json.dumps(result))

    def test_known_slot_overlap_with_unknown_weeks_is_possible_not_fail_or_clear(self):
        self.components['meetings'][-1]['fields']['weekday'] = 2
        result = self.catalog().conditional_recommend(target_credits=5, limit=10)
        pair = next(o for o in result['options'] if o['course_codes'] == ['SYN-B', 'SYN-C'])
        self.assertTrue(pair['possible_conflicts'])
        self.assertTrue(all(c['result'] == 'unknown' for c in pair['possible_conflicts']))
        self.assertEqual(pair['result'], 'unknown')

    def test_hard_friday_excludes_known_friday_soft_uses_observed_weekdays(self):
        soft = self.catalog().conditional_recommend(target_credits=5, friday_free='soft')
        self.assertEqual(soft['options'][0]['course_codes'], ['SYN-B', 'SYN-C'])
        self.assertEqual(soft['options'][0]['metrics']['friday_meeting_count'], 0)
        hard = self.catalog().conditional_recommend(target_credits=5, friday_free='hard')
        self.assertEqual(len(hard['options']), 1)
        self.assertNotIn('SYN-A', hard['options'][0]['course_codes'])
        self.assertTrue(any(d['code'] == 'hard_friday' for d in hard['diagnostics']))

    def test_latest_negative_capacity_does_not_use_older_positive_observation(self):
        add_observation(self.components)
        self.components['capacity_snapshots'][-1]['fields']['remaining'] = -2
        result = self.catalog().conditional_recommend(target_credits=5, limit=10)
        self.assertTrue(all('SYN-A' not in o['course_codes'] for o in result['options']))
        self.assertTrue(any(d['code'] == 'observed_capacity_unavailable' for d in result['diagnostics']))
        self.assertEqual(result['search']['observed_course_count'], 3)
        self.assertEqual(result['search']['candidate_course_count'], 2)
        self.assertEqual(result['search']['unobserved_course_codes'], [])
        self.assertEqual(result['search']['filtered_observed_course_codes'], ['SYN-A'])
        self.assertTrue(result['search']['observation_precheck_complete'])

    def test_credit_conflict_or_unknown_is_not_replaced_from_section_fields(self):
        self.components['courses'][0]['definitions'][0]['fields']['credits']['state'] = 'conflicting'
        self.components['sections'][0]['fields']['credits'] = {'state': 'known', 'value': '99'}
        self.assertIsNone(self.catalog().lookup('SYN-A')['course']['credits'])
        result = self.catalog().conditional_recommend(target_credits=5, limit=10)
        unknown = next(o for o in result['options'] if 'SYN-A' in o['course_codes'])
        self.assertEqual(unknown['credits_state'], 'unknown')
        self.assertIsNone(unknown['total_credits'])
        self.assertIn('scenario_credit_target_not_confirmed', unknown['unknowns'])

    def test_unobserved_code_is_unknown_and_search_does_not_claim_global_no_solution(self):
        catalog = self.catalog(allowed_course_codes=['SYN-A', 'SYN-MISSING'])
        missing = catalog.lookup('SYN-MISSING')
        self.assertEqual(missing['observations'], [])
        self.assertEqual(missing['mapping_status'], 'not_observed_in_authorized_pool')
        result = catalog.conditional_recommend(target_credits=8, friday_free='hard')
        self.assertEqual(result['options'], [])
        self.assertEqual(result['result'], 'unknown')
        self.assertIn('SYN-MISSING', result['search']['unobserved_course_codes'])
        self.assertFalse(result['search']['source_course_coverage_complete'])

    def test_node_limit_is_global_across_observation_checks_and_search(self):
        result = self.catalog().conditional_recommend(target_credits=5, node_limit=4)
        self.assertEqual(result['search']['nodes_visited'], 4)
        self.assertTrue(result['search']['limit_reached'])
        self.assertIn('observation_precheck', result['search']['phases'])
        self.assertFalse(result['search']['globally_optimal'])
        self.assertFalse(result['search']['complete_within_observed_scope'])

    def test_small_pool_matches_independent_credit_subsets(self):
        result = self.catalog().conditional_recommend(target_credits=5, limit=10)
        amounts = {'SYN-A': Decimal('3'), 'SYN-B': Decimal('3'), 'SYN-C': Decimal('2')}
        expected = {tuple(group) for size in range(1, 4) for group in combinations(sorted(amounts), size)
                    if sum((amounts[c] for c in group), Decimal(0)) >= Decimal('5')}
        self.assertEqual({tuple(o['course_codes']) for o in result['options']}, expected)
        self.assertEqual(len(expected), 4)

    def test_decimal_totals_are_exact_and_default_goal_is_only_assumption(self):
        for course, amount in zip(self.components['courses'], ('0.1', '0.2', '0.3')):
            course['definitions'][0]['fields']['credits']['value'] = amount
        result = self.catalog().conditional_recommend(target_credits='0.3', limit=10)
        pair = next(o for o in result['options'] if o['course_codes'] == ['SYN-A', 'SYN-B'])
        self.assertEqual(Decimal(pair['total_credits']), Decimal('0.3'))
        self.assertNotEqual(result['assumptions']['requirement_completion'], 'pass')


if __name__ == '__main__':
    unittest.main()
