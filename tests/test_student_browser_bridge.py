import base64
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from course_assistant.student_browser_bridge import (
    BrowserBridgeError, BrowserBridgeServer, BrowserBridgeTransport,
    _project_response, validate_spec)


ORIGIN = "chrome-extension://" + "a" * 32
EPOCH = "synthetic_epoch_0001"
SPEC = {"operation": "academic", "target_term": "2026-2027-AW", "page": 1, "page_size": 20}


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"scope_key": "synthetic-grant"}
        self.transport = BrowserBridgeTransport(lambda: self.grant, timeout=.3)
        self.server = BrowserBridgeServer(self.transport).start()
        self.addCleanup(self.server.close)

    def send(self, path, payload=None, *, origin=ORIGIN, token=None, host=None):
        headers = {"Origin": origin} if origin else {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if host:
            headers["Host"] = host
        request = Request(self.server.url + path,
                          data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=2) as response:
                body = response.read()
                return response.status, json.loads(body) if response.headers['Content-Type'] == 'application/json' else body
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def pair(self):
        status, data = self.send('/pair', {'code': self.transport._pair_code, 'session_epoch': EPOCH})
        self.assertEqual(status, 200)
        return data['token']

    def wait_job(self, token):
        for _ in range(50):
            status, data = self.send('/next', {'session_epoch': EPOCH}, token=token)
            self.assertEqual(status, 200)
            if data['job']:
                return data['job']
            time.sleep(.005)
        self.fail('No job delivered')

    def response(self, **changes):
        return {"state": "business", "http_status": 200, "content_type": "application/json",
                "session_epoch": EPOCH, "payload": {"items": [{"kch": "SYNTH-1"}], "total": 1}, **changes}

    def test_loopback_paired_roundtrip_only_business_projection(self):
        token = self.pair()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.transport.request, SPEC)
            job = self.wait_job(token)
            self.assertEqual(job['spec'], SPEC)
            response = self.response(payload={'items': [{'kch': 'SYNTH-1', 'password': 'test-only'}], 'total': 1})
            self.assertEqual(self.send('/result', {'id': job['id'], 'response': response}, token=token)[0], 200)
            result = future.result(timeout=2)
            self.assertNotIn('password', result['payload']['items'][0])

    def test_pairing_wrong_code_origin_replay_and_health_do_not_expose_key(self):
        self.assertEqual(self.send('/pair', {'code': 'wrong', 'session_epoch': EPOCH})[0], 403)
        self.assertEqual(self.send('/pair', {'code': self.transport._pair_code, 'session_epoch': EPOCH}, origin='https://example.invalid')[0], 403)
        token = self.pair()
        self.assertEqual(self.send('/pair', {'code': '', 'session_epoch': EPOCH})[0], 403)
        _, health = self.send('/health', origin=None)
        self.assertNotIn(token, json.dumps(health))
        self.assertNotIn(EPOCH, json.dumps(health))

    def test_consumed_pairing_code_cannot_be_reused_after_scope_change(self):
        token = self.pair()
        self.grant = {'scope_key': 'other-grant'}
        self.assertEqual(self.send('/next', {'session_epoch': EPOCH}, token=token)[0], 403)
        self.assertEqual(self.send('/pair', {'code': '', 'session_epoch': EPOCH})[0], 403)
        self.assertTrue(self.transport._pair_code)

    def test_wrong_host_origin_and_connection_token_rejected(self):
        token = self.pair()
        payload = {'session_epoch': EPOCH}
        self.assertEqual(self.send('/next', payload, token=token, host='localhost')[0], 403)
        self.assertEqual(self.send('/next', payload, token=token, origin='https://evil.invalid')[0], 403)
        self.assertEqual(self.send('/next', payload, token='wrong')[0], 403)
        self.assertEqual(self.send('/next', {'session_epoch': 'other_epoch_000000'}, token=token)[0], 403)

    def test_timeout_cancels_job_and_late_result_is_not_published(self):
        token = self.pair()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.transport.request, SPEC)
            job = self.wait_job(token)
            with self.assertRaises(TimeoutError):
                future.result(timeout=2)
            self.assertEqual(self.send('/result', {'id': job['id'], 'response': self.response()}, token=token)[1]['error'], 'stale_result')

    def test_grant_change_in_flight_rejects_returned_body(self):
        token = self.pair()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.transport.request, SPEC)
            job = self.wait_job(token)
            self.grant = {'scope_key': 'another-owner'}
            self.assertEqual(self.send('/result', {'id': job['id'], 'response': self.response()}, token=token)[0], 403)
            with self.assertRaises(BrowserBridgeError) as caught:
                future.result(timeout=2)
            self.assertIn(caught.exception.code, {'source_scope_changed', 'source_session_expired'})

    def test_auth_failure_payload_headers_and_unknown_status_do_not_cross(self):
        result = _project_response(self.response(state='awaiting_user_login', payload='<password-form>'), 'academic', EPOCH)
        self.assertNotIn('payload', result)
        result = _project_response(self.response(state='source_session_expired', http_status=401,
                                  content_type={'Cookie': 'synthetic'}), 'academic', EPOCH)
        self.assertIsNone(result['content_type'])
        result = _project_response(self.response(http_status=403), 'academic', EPOCH)
        self.assertEqual(result['state'], 'permission_denied')
        for response in (self.response(headers={'Cookie': 'test'}), self.response(http_status=None), self.response(session_epoch='changed_epoch_0000')):
            with self.assertRaises(BrowserBridgeError):
                _project_response(response, 'academic', EPOCH)

    def test_pdf_only_registered_operation_and_bounded_binary(self):
        document = b'%PDF-1.4\nsynthetic\n%%EOF'
        response = self.response(content_type='application/pdf', payload_base64=base64.b64encode(document).decode())
        response.pop('payload')
        self.assertEqual(_project_response(response, 'plan_document', EPOCH)['payload'], document)
        response['payload_base64'] = base64.b64encode(b'<html>login</html>').decode()
        with self.assertRaises(BrowserBridgeError):
            _project_response(response, 'plan_document', EPOCH)

    def test_catalogue_dom_keeps_unknown_http_only_with_precise_guard(self):
        response = self.response(http_status=None, content_type='dom_business_projection',
                                 capture_method='browser_dom',
                                 page_validation={'status': 'visible_business_page', 'domain': 'plan_catalogue'},
                                 payload={'items': [{'plan_ref': 'PLAN-synthetic', 'cohort': 2023}], 'total': None})
        result = _project_response(response, 'plan_catalogue', EPOCH)
        self.assertIsNone(result['http_status'])
        self.assertEqual(result['capture_method'], 'browser_dom')
        response['page_validation']['domain'] = 'academic'
        with self.assertRaises(BrowserBridgeError):
            _project_response(response, 'plan_catalogue', EPOCH)

    def test_catalogue_nested_secrets_and_invalid_counters_are_rejected(self):
        for payload in ({'items': [{'plan_ref': 'PLAN-test', 'major_name': {'password': 'synthetic'}}]},
                        {'items': [], 'total': {'Cookie': 'synthetic'}}):
            with self.assertRaises(BrowserBridgeError):
                _project_response(self.response(payload=payload), 'plan_catalogue', EPOCH)

    def test_catalogue_cohort_provenance_is_scalar_and_never_a_query_permission(self):
        row = {'plan_ref': 'PLAN-synthetic', 'cohort': 2030, 'cohort_provenance': 'confirmed_query_scope'}
        result = _project_response(self.response(payload={'items': [row]}), 'plan_catalogue', EPOCH)
        self.assertEqual(result['payload']['items'][0]['cohort_provenance'], 'confirmed_query_scope')
        legacy = _project_response(self.response(payload={'items': [{'plan_ref': 'PLAN-synthetic'}]}), 'plan_catalogue', EPOCH)
        self.assertEqual(legacy['payload']['items'][0]['cohort_provenance'], 'unknown')
        with self.assertRaises(BrowserBridgeError):
            validate_spec({'operation': 'plan_catalogue', 'scope': {'cohort': 2030, 'cohort_provenance': 'confirmed_query_scope'}})
        for value in (None, True, {'value': 'confirmed_query_scope'}, 'SYNTHETIC-PRIVATE-VALUE'):
            with self.subTest(value=value), self.assertRaises(BrowserBridgeError):
                _project_response(self.response(payload={'items': [{**row, 'cohort_provenance': value}]}), 'plan_catalogue', EPOCH)

    def test_catalogue_known_paging_metadata_is_numeric_and_bounded(self):
        payload = {'items': [{'plan_ref': 'PLAN-synthetic'}], 'currentPage': '1',
                   'pageSize': 20, 'showCount': '20', 'totalResult': '1'}
        result = _project_response(self.response(payload=payload), 'plan_catalogue', EPOCH)
        self.assertEqual({key: result['payload'][key] for key in ('currentPage', 'pageSize', 'showCount', 'totalResult')},
                         {'currentPage': 1, 'pageSize': 20, 'showCount': 20, 'totalResult': 1})
        for value in ({'Cookie': 'SYNTHETIC-SECRET'}, True, 100001, -1):
            with self.subTest(value=value), self.assertRaises(BrowserBridgeError):
                _project_response(self.response(payload={**payload, 'pageSize': value}), 'plan_catalogue', EPOCH)

    def test_response_submitted_after_deadline_is_rejected_before_waiter_wakes(self):
        now = [0.0]
        self.transport.clock = lambda: now[0]
        token = self.pair()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.transport.request, SPEC)
            job = self.wait_job(token)
            with self.transport._condition:
                now[0] = 1
                with self.assertRaises(BrowserBridgeError) as caught:
                    self.transport.submit(job['id'], self.response())
                self.assertEqual(caught.exception.code, 'stale_result')
            with self.assertRaises(TimeoutError):
                future.result(timeout=2)

    def test_queries_cannot_supply_password_user_url_or_course_update(self):
        bad = [dict(SPEC, url='https://example.invalid'), dict(SPEC, xh='SYNTHETIC'),
               dict(SPEC, operation='course_update'), dict(SPEC, page=4), dict(SPEC, target_term='2026-2028-AW'),
               {'operation': 'plan_document', 'plan_ref': 'https://example.invalid/plan.pdf'}]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaises(BrowserBridgeError):
                validate_spec(spec)

    def test_grant_revocation_stops_paired_connection_without_logging_reason(self):
        token = self.pair()
        def revoked():
            raise ValueError('private reason must not leave callback')
        self.transport.authorize = revoked
        code, data = self.send('/next', {'session_epoch': EPOCH}, token=token)
        self.assertEqual((code, data), (403, {'error': 'grant_denied'}))
        self.assertEqual(self.transport.describe()['status'], 'awaiting_browser_connection')

    def test_invalid_grant_receipt_cancels_connection(self):
        token = self.pair()
        self.grant = {}
        self.assertEqual(self.send('/next', {'session_epoch': EPOCH}, token=token)[0], 403)
        self.assertEqual(self.transport.describe()['status'], 'awaiting_browser_connection')

    def test_disconnect_and_missing_heartbeat_invalidate_epoch(self):
        token = self.pair()
        self.assertEqual(self.send('/heartbeat', {'session_epoch': EPOCH}, token=token), (200, {'alive': True}))
        self.assertEqual(self.send('/disconnect', {'session_epoch': EPOCH}, token=token), (200, {'disconnected': True}))
        self.assertEqual(self.transport.session_epoch, 'unpaired')
        now = [0.0]
        self.transport.clock = lambda: now[0]
        self.pair()
        now[0] = 21
        self.assertFalse(self.transport.wait_connected(timeout=0))
        self.assertEqual(self.transport.session_epoch, 'unpaired')


if __name__ == '__main__':
    unittest.main()
