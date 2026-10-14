"""Synthetic integration across the transport client, private task and plan store."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from course_assistant.student_sources import SourceTaskStore, SCHEMA
from course_assistant.student_source_client import StudentSourceClient
from scripts.run_student_source_probe import initialize_task, ROOT
from scripts.run_student_source_sync import source_profile, sync_operation, plan_service, main


class SyntheticTransport:
    session_epoch = 'synthetic_session_0001'
    def __init__(self):
        self.calls = []
        self.state = 'business'
    def request(self, spec):
        self.calls.append(spec)
        common = {'state': self.state, 'http_status': 200, 'session_epoch': self.session_epoch,
                  'content_type': 'application/json', 'capture_method': 'browser_http', 'source_subject_id': 'SYNTHETIC-SUBJECT'}
        if self.state != 'business':
            return common
        if spec['operation'] == 'plan_document':
            return {**common, 'content_type': 'application/pdf', 'payload': b'%PDF-1.4\nSynthetic test only, no student data\n%%EOF'}
        if spec['operation'] == 'plan_catalogue':
            return {**common, 'payload': {'items': [{'plan_ref': 'PLAN-synthetic', 'cohort': 2023,
                       'college_name': '合成学院', 'major_name': '合成专业', 'program': None,
                       'study_path_raw': None, 'title': '合成2023级方案'}], 'total': 1, 'pageNo': 1}}
        return {**common, 'payload': {'items': [{'kch': 'SYNTH-1', 'kcmc': '合成课程', 'xf': '1.50', 'cj': '90'}],
                                      'total': 1, 'pageNo': 1}}


class StudentSourceSyncTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'local-only').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic_source_sync_', dir=ROOT / 'local-only')
        self.addCleanup(self.temporary.cleanup)
        self.batch = Path(self.temporary.name)
        self.source_root, self.grant = self.batch / 'source_task', self.batch / 'source_grant.json'
        initialize_task(self.source_root, self.grant, synthetic=True)
        self.store = SourceTaskStore(self.source_root, self.grant)
        self.term = '2026-2027-AW'
        self.origin = 'https://synthetic.example.invalid'
        self.attempt = self.store.create_attempt(self.term, self.origin)['attempt_id']
        self.transport = SyntheticTransport()
        self.authorize = lambda: {'scope_key': 'synthetic-local-grant', 'target_term': self.term}
        self.client = StudentSourceClient(self.transport, self.authorize)

    def test_live_academic_moves_through_existing_parser_and_keeps_subject_anchor(self):
        receipt = sync_operation(self.client, self.store, self.attempt, 'academic', self.term, source_origin=self.origin)
        self.assertEqual(receipt['status'], 'complete_within_scope')
        self.assertNotIn('payload', receipt)
        snapshot = self.store.private_snapshot(self.attempt, 'academic')
        self.assertEqual(len(snapshot['snapshot']['records']), 1)
        self.assertEqual(snapshot['snapshot']['records'][0]['nominal_credits'], '1.50')
        self.assertEqual(self.store.describe(self.attempt)['status'], 'browser_source_observed')

    def test_failed_connector_does_not_replace_source_with_empty_success(self):
        self.transport.state = 'route_not_confirmed'
        receipt = sync_operation(self.client, self.store, self.attempt, 'academic', self.term, source_origin=self.origin)
        self.assertEqual(receipt['status'], 'route_not_confirmed')
        self.assertIsNone(self.store.private_snapshot(self.attempt, 'academic'))
        self.assertEqual(self.store.describe(self.attempt)['domains']['academic']['status'], 'source_unavailable')

    def test_plan_callbacks_acquire_2023_pdf_and_reuse_authorized_version(self):
        service = plan_service(self.client, self.batch / 'plans', self.authorize)
        profile = {'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'}
        first = service.acquire(profile)
        calls = len(self.transport.calls)
        cached = service.acquire(profile)
        self.assertEqual(first['status'], 'acquired_pending_review')
        self.assertEqual(first['binding_status'], 'not_verified')
        self.assertEqual(cached['data_source'], 'historical_cache')
        self.assertEqual(len(self.transport.calls), calls)
        self.assertTrue(service.read_document(first['source_alias']).startswith(b'%PDF-'))

    def test_existing_official_profile_only_supplies_observed_scope(self):
        path = self.batch / 'profile.json'
        body = {'schema': SCHEMA, 'domain': 'profile', 'source_origin': 'https://zdbk.zju.edu.cn',
                'payload': {'admission_date_raw': '20230901', 'college_name': '合成学院',
                            'major_or_category_raw': '合成专业', 'current_grade_raw': '2026'}}
        path.write_text(json.dumps(body))
        os.chmod(path, 0o600)
        observed = source_profile(path)
        self.assertEqual(observed['cohort'], 2023)
        self.assertIsNone(observed['major_name'])

    def test_category_candidate_with_different_explicit_major_is_rejected(self):
        service = plan_service(self.client, self.batch / 'plans', self.authorize)
        result = service.acquire({'cohort': 2023, 'college_name': '合成学院',
                                  'major_or_category_raw': '另一合成类别'})
        self.assertEqual(result['status'], 'source_scope_conflict')
        self.assertFalse(any(call['operation'] == 'plan_document' for call in self.transport.calls))

    def test_new_personal_pledge_stops_task_and_retains_specific_status(self):
        self.transport.state = 'user_confirmation_required'
        receipt = sync_operation(self.client, self.store, self.attempt, 'academic', self.term, source_origin=self.origin)
        self.assertEqual(receipt['status'], 'user_confirmation_required')
        self.assertEqual(self.store.describe(self.attempt)['status'], 'user_confirmation_required')

    def test_explicit_catalogue_count_conflict_blocks_document_download(self):
        request = self.transport.request
        def conflicting(spec):
            result = request(spec)
            if spec['operation'] == 'plan_catalogue':
                result['payload']['total'] = 0
            return result
        self.transport.request = conflicting
        service = plan_service(self.client, self.batch / 'plans', self.authorize)
        result = service.acquire({'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'})
        self.assertEqual(result['status'], 'schema_changed')
        self.assertFalse(any(call['operation'] == 'plan_document' for call in self.transport.calls))

    def test_known_omitted_catalogue_candidate_blocks_document_download(self):
        request = self.transport.request
        def incomplete(spec):
            result = request(spec)
            if spec['operation'] == 'plan_catalogue':
                result['payload']['total'] = 2
            return result
        self.transport.request = incomplete
        service = plan_service(self.client, self.batch / 'plans', self.authorize)
        result = service.acquire({'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'})
        self.assertEqual(result['status'], 'invalid_plan_catalogue')
        self.assertEqual(result['version_count'], 0)
        self.assertEqual(result['source_aliases'], [])
        self.assertFalse(any(call['operation'] == 'plan_document' for call in self.transport.calls))

    def test_unknown_dom_catalogue_total_keeps_pending_review(self):
        request = self.transport.request
        def directory(spec):
            result = request(spec)
            if spec['operation'] == 'plan_catalogue':
                result.update(http_status=None, content_type='dom_business_projection',
                              capture_method='browser_dom',
                              page_validation={'status': 'visible_business_page', 'domain': 'plan_catalogue'})
                result['payload']['total'] = None
            return result
        self.transport.request = directory
        service = plan_service(self.client, self.batch / 'plans', self.authorize)
        result = service.acquire({'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'})
        self.assertEqual(result['status'], 'acquired_pending_review')
        self.assertEqual(result['binding_status'], 'not_verified')
        self.assertTrue(any(call['operation'] == 'plan_document' for call in self.transport.calls))

    def test_all_captured_catalogue_pages_contribute_to_candidate_ambiguity(self):
        calls = []
        class CapturedPagesClient:
            def fetch(inner, operation, **kwargs):
                calls.append(operation)
                return {'data_source': 'live', 'failure': None,
                        'coverage': {'reported_total': 2},
                        'pages': [{'payload': {'items': [{'plan_ref': 'PLAN-synthetic-' + str(index),
                                   'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'}]}}
                                  for index in (1, 2)]}
        service = plan_service(CapturedPagesClient(), self.batch / 'plans', self.authorize)
        result = service.acquire({'cohort': 2023, 'college_name': '合成学院', 'major_name': '合成专业'})
        self.assertEqual(result['status'], 'ambiguous_plan')
        self.assertEqual(result['candidate_count'], 2)
        self.assertNotIn('plan_document', calls)

    def test_cli_rejects_course_updates_and_credential_arguments_without_echo(self):
        for extra in (['--operations', 'course_update'], ['--password', 'SYNTHETIC_PASSWORD']):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--root', str(self.batch / 'new'), '--term', self.term, *extra])
            self.assertEqual(code, 2)
            self.assertNotIn('SYNTHETIC_PASSWORD', output.getvalue())
            self.assertFalse((self.batch / 'new').exists())


if __name__ == '__main__':
    unittest.main()
