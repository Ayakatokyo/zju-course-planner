"""Run the localhost application with the persistent curriculum vector backend.

Requires the verified native Qdrant binary/config and local model artifacts.
App arguments are passed through, e.g. --enable-school --port 18875.
"""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def qdrant_healthy(timeout=2):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:6333/healthz', timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.argv = ['course_assistant.server', *sys.argv[1:]]
    if '--help' in sys.argv[1:] or '-h' in sys.argv[1:]:
        runpy.run_module('course_assistant.server', run_name='__main__')
        return
    settings = {
        'COURSE_SEMANTIC_ENCODER_MANIFEST': ROOT / 'private/semantic_runtime/manifest.json',
        'COURSE_SEMANTIC_PYTHON': ROOT / 'private/semantic_runtime/venv/bin/python',
        'COURSE_OCR_TESSDATA': ROOT / 'private/ocr_runtime/tessdata',
        'COURSE_CURRICULUM_CORPUS': ROOT / 'private/curriculum_vectors/catalogue.sqlite3',
    }
    for key, path in settings.items():
        if not path.exists():
            raise SystemExit('Missing local runtime: ' + key)
    os.environ.update({key: str(path) for key, path in settings.items()})
    os.environ.update(COURSE_QDRANT_URL='http://127.0.0.1:6333', COURSE_LOGIN_PREPARE='true')
    process = None
    try:
        if not qdrant_healthy():
            server = ROOT / 'private/qdrant_server'
            binary, config = server / 'bin/qdrant', server / 'config.yaml'
            if not binary.is_file() or not config.is_file():
                raise SystemExit('Install the pinned Qdrant server first; see the vector-store guide.')
            with (server / 'server.log').open('ab') as log:
                process = subprocess.Popen([str(binary), '--config-path', str(config)], cwd=server,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise SystemExit('Qdrant startup failed; inspect its private log.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SystemExit('Qdrant is not ready.')
                if qdrant_healthy(timeout=min(2, remaining)):
                    break
                time.sleep(min(.25, max(0, deadline - time.monotonic())))
        runpy.run_module('course_assistant.server', run_name='__main__')
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise


if __name__ == '__main__':
    main()
