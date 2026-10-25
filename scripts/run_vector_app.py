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

ROOT=Path(__file__).resolve().parents[1]

def main():
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    server=ROOT/'private/qdrant_server'
    def healthy():
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:6333/healthz',timeout=2) as response:
                return response.status==200
        except OSError:return False
    if not healthy():
        binary=server/'bin/qdrant';config=server/'config.yaml'
        if not binary.is_file() or not config.is_file():
            raise SystemExit('Install the pinned Qdrant server first; see the vector-store guide.')
        with (server/'server.log').open('ab') as log:
            process=subprocess.Popen([str(binary),'--config-path',str(config)],cwd=server,
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(40):
            if healthy():break
            if process.poll() is not None:raise SystemExit('Qdrant startup failed; inspect its private log.')
            time.sleep(.25)
        else:raise SystemExit('Qdrant is not ready.')
    settings={
        'COURSE_SEMANTIC_ENCODER_MANIFEST':ROOT/'private/semantic_runtime/manifest.json',
        'COURSE_SEMANTIC_PYTHON':ROOT/'private/semantic_runtime/venv/bin/python',
        'COURSE_OCR_TESSDATA':ROOT/'private/ocr_runtime/tessdata',
        'COURSE_CURRICULUM_CORPUS':ROOT/'private/curriculum_vectors/catalogue.sqlite3',
    }
    for key,path in settings.items():
        if not path.exists():raise SystemExit('Missing local runtime: '+key)
        os.environ[key]=str(path)
    os.environ.update(COURSE_QDRANT_URL='http://127.0.0.1:6333',COURSE_LOGIN_PREPARE='true')
    sys.argv=['course_assistant.server',*sys.argv[1:]]
    runpy.run_module('course_assistant.server',run_name='__main__')

if __name__=='__main__':main()
