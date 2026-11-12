from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch
from course_assistant.curriculum_preparation import CurriculumPreparation

class Auth:
    revoked=False
    def require_context(self, context):
        if self.revoked:raise ValueError('revoked')

class PreparationTests(unittest.TestCase):
    def wait(self,jobs,context,expected):
        end=time.monotonic()+2
        while time.monotonic()<end:
            if jobs.status(context)['status']==expected:return
            time.sleep(.005)
        self.fail(jobs.status(context))

    def test_nonblocking_deduplicated_and_session_isolated(self):
        started,release=threading.Event(),threading.Event()
        context=SimpleNamespace(session_ref='one');auth=Auth();calls=[]
        def prepare(ctx):
            calls.append(ctx.session_ref);started.set();release.wait(2)
            return {'status':'ready','vectors':10,'unsafe':'must not publish'}
        jobs=CurriculumPreparation(auth,lambda _:SimpleNamespace(prepare_curriculum=prepare),capacity=1)
        try:
            self.assertEqual(jobs.enqueue(context)['status'],'queued')
            self.assertTrue(started.wait(1))
            self.assertEqual(jobs.enqueue(context)['status'],'preparing')
            self.assertEqual(jobs.status(SimpleNamespace(session_ref='two'))['status'],'not_started')
            self.assertEqual(jobs.enqueue(SimpleNamespace(session_ref='two'))['status'],'busy')
            release.set();self.wait(jobs,context,'ready')
            self.assertEqual(calls,['one']);self.assertNotIn('unsafe',jobs.status(context))
        finally:release.set();jobs.close()

    def test_revocation_during_job_never_publishes_ready(self):
        entered,release=threading.Event(),threading.Event();auth=Auth()
        ctx=SimpleNamespace(session_ref='one')
        def prepare(_):entered.set();release.wait(2);return {'status':'ready'}
        jobs=CurriculumPreparation(auth,lambda _:SimpleNamespace(prepare_curriculum=prepare))
        try:
            jobs.enqueue(ctx);self.assertTrue(entered.wait(1));auth.revoked=True;release.set()
            with self.assertRaises(ValueError):jobs.status(ctx)
            end=time.monotonic()+2
            while time.monotonic()<end:
                with jobs.lock:state=jobs.jobs['one']['status']
                if state=='failed':break
                time.sleep(.005)
            self.assertEqual(state,'failed')
        finally:release.set();jobs.close()

    def test_submit_failure_returns_safe_status_and_releases_capacity(self):
        calls = []
        def prepare(context):
            calls.append(context.session_ref)
            return {'status': 'ready'}
        jobs = CurriculumPreparation(Auth(), lambda _: SimpleNamespace(prepare_curriculum=prepare), capacity=1)
        failed = SimpleNamespace(session_ref='failed')
        recovered = SimpleNamespace(session_ref='recovered')
        try:
            with patch.object(jobs.pool, 'submit', side_effect=RuntimeError('synthetic private failure')):
                result = jobs.enqueue(failed)
            self.assertEqual(result, {'status': 'failed', 'reason': 'preparation_unavailable'})
            self.assertEqual(jobs.status(failed), result)
            self.assertEqual(jobs.enqueue(recovered)['status'], 'queued')
            self.wait(jobs, recovered, 'ready')
            self.assertEqual(calls, ['recovered'])
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_worker_start_failure_cannot_run_abandoned_job_after_recovery(self):
        calls = []
        def prepare(context):
            calls.append(context.session_ref)
            return {'status': 'ready'}
        jobs = CurriculumPreparation(Auth(), lambda _: SimpleNamespace(prepare_curriculum=prepare), capacity=1)
        failed = SimpleNamespace(session_ref='failed')
        recovered = SimpleNamespace(session_ref='recovered')
        try:
            with patch('threading.Thread.start', side_effect=RuntimeError('synthetic worker start failure')):
                self.assertEqual(jobs.enqueue(failed)['status'], 'failed')
            self.assertEqual(jobs.enqueue(recovered)['status'], 'queued')
            self.wait(jobs, recovered, 'ready')
            self.assertEqual(jobs.status(failed)['status'], 'failed')
            self.assertEqual(calls, ['recovered'])
        finally:
            jobs.close()
            jobs.pool.shutdown(wait=True)

    def test_close_cancels_queued_jobs_and_releases_each_slot_once(self):
        entered = threading.Barrier(3)
        release = threading.Event()
        calls = []
        def prepare(context):
            calls.append(context.session_ref)
            entered.wait(timeout=2)
            release.wait(3)
            return {'status': 'ready'}
        jobs = CurriculumPreparation(Auth(), lambda _: SimpleNamespace(prepare_curriculum=prepare))
        contexts = [SimpleNamespace(session_ref=str(index)) for index in range(4)]
        try:
            for context in contexts[:2]:
                self.assertEqual(jobs.enqueue(context)['status'], 'queued')
            entered.wait(timeout=2)
            for context in contexts[2:]:
                self.assertEqual(jobs.enqueue(context)['status'], 'queued')
            jobs.close()
            jobs.close()
            self.assertFalse(release.is_set())
            for context in contexts:
                self.assertEqual(jobs.status(context), {'status': 'cancelled', 'reason': 'preparation_stopped'})
            self.assertEqual(jobs.enqueue(SimpleNamespace(session_ref='new')),
                             {'status': 'cancelled', 'reason': 'preparation_stopped'})
            release.set()
            jobs.pool.shutdown(wait=True)
            self.assertCountEqual(calls, ['0', '1'])
            for context in contexts:
                self.assertEqual(jobs.status(context)['status'], 'cancelled')
            for _ in contexts:
                self.assertTrue(jobs.slots.acquire(blocking=False))
            self.assertFalse(jobs.slots.acquire(blocking=False))
        finally:
            release.set()
            jobs.close()
            jobs.pool.shutdown(wait=True)
