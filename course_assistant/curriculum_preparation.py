"""Bounded login preparation; authenticated sessions own their job status."""
from concurrent.futures import ThreadPoolExecutor
import threading


class CurriculumPreparation:
    def __init__(self, auth, provider, *, capacity=4):
        self.auth, self.provider = auth, provider
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='curriculum')
        self.slots = threading.BoundedSemaphore(capacity)
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False

    def enqueue(self, context):
        self.auth.require_context(context)
        with self.lock:
            if self.closed:
                return {'status': 'cancelled', 'reason': 'preparation_stopped'}
            if context.session_ref in self.jobs:
                return dict(self.jobs[context.session_ref])
            if not self.slots.acquire(blocking=False):
                return {'status': 'busy', 'reason': 'preparation_queue_full'}
            for key in list(self.jobs):
                if len(self.jobs) < 64:
                    break
                if self.jobs[key]['status'] not in {'queued', 'preparing'}:
                    self.jobs.pop(key)
            job = {'status': 'queued'}
            self.jobs[context.session_ref] = job
            try:
                future = self.pool.submit(self._run, context, job)
            except Exception:
                self.slots.release()
                self.jobs[context.session_ref] = {'status': 'failed', 'reason': 'preparation_unavailable'}
                return dict(self.jobs[context.session_ref])
            # Completion also covers queued futures cancelled during shutdown.
            future.add_done_callback(lambda _: self.slots.release())
            return {'status': 'queued'}

    def _run(self, context, job):
        try:
            self.auth.require_context(context)
            with self.lock:
                # A failed worker start can leave its submitted work in the pool.
                if self.closed or self.jobs.get(context.session_ref) is not job:
                    return
                self.jobs[context.session_ref] = {'status': 'preparing'}
            result = self.provider(context).prepare_curriculum(context)
            self.auth.require_context(context)
            # Never expose exception messages, paths or source records as status.
            safe = {key: result[key] for key in ('status', 'vectors', 'encoded_count', 'persistent_cache_hits',
                    'missing_vectors', 'write_allowed', 'vector_backend') if key in result}
            with self.lock:
                if not self.closed:
                    self.jobs[context.session_ref] = safe
        except Exception:
            with self.lock:
                if not self.closed:
                    self.jobs[context.session_ref] = {'status': 'failed', 'reason': 'preparation_unavailable'}

    def status(self, context):
        self.auth.require_context(context)
        with self.lock:
            return dict(self.jobs.get(context.session_ref, {'status': 'not_started'}))

    def close(self):
        with self.lock:
            self.closed = True
            for key, job in self.jobs.items():
                if job['status'] in {'queued', 'preparing'}:
                    self.jobs[key] = {'status': 'cancelled', 'reason': 'preparation_stopped'}
        self.pool.shutdown(wait=False, cancel_futures=True)
