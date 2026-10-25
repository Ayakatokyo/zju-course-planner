from types import SimpleNamespace
import threading
import time
import unittest
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
