"""Record current local tests and owning source identity; no real school calls."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import unittest
from collections import Counter
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from course_assistant import __version__
from course_assistant.importer import read_sources


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-prefix', help='Save a separate round, using a filename prefix such as agent_dialog_20261009')
    args = parser.parse_args()
    if args.record_prefix and not re.fullmatch(r'[A-Za-z0-9_-]+', args.record_prefix):
        parser.error('--record-prefix must contain only letters, digits, underscores or hyphens')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    counts = Counter(identifier.split('.')[0] for identifier in test_ids(suite))
    output = ROOT / 'validation' / (f'{args.record_prefix}_tests.txt' if args.record_prefix else 'current_tests_20261009.txt')
    with output.open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    node = shutil.which('node')
    syntax = subprocess.run([node, '--check', str(ROOT / 'web' / 'app.js')], capture_output=True, text=True) if node else None
    whitespace = subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True, text=True)
    original = subprocess.run(['git', 'diff', '--exit-code', '--', 'inputs'], cwd=ROOT, capture_output=True, text=True)
    files = sorted([p for folder in ('course_assistant', 'web', 'tests', 'demo')
                    for p in (ROOT / folder).rglob('*')
                    if p.is_file() and p.suffix in {'.py', '.js', '.css', '.html', '.json'}])
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    report = {'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'application_version': __version__,
              'python_version': platform.python_version(),
              'git_base': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'test_result': {'ran': result.testsRun, 'passed': result.testsRun - len(result.skipped) - len(result.errors) - len(result.failures),
                              'skipped': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors), 'by_module': dict(counts)},
              'node_version': subprocess.check_output([node, '--version'], text=True).strip() if node else None,
              'web_syntax': 'pass' if syntax and syntax.returncode == 0 else 'not_run' if not syntax else 'fail',
              'git_diff_check': {'returncode': whitespace.returncode, 'output': whitespace.stdout + whitespace.stderr},
              'inputs_git_unchanged': original.returncode == 0, 'source_integrity': read_sources()['integrity'],
              'source_sha256': hashes,
              'scope': 'local synthetic tests; official authentication, real data, deployment and full product acceptance remain incomplete'}
    report_path = ROOT / 'validation' / (f'{args.record_prefix}_validation.json' if args.record_prefix else 'development_validation_20261009.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('application_version', 'test_result', 'web_syntax', 'inputs_git_unchanged', 'git_diff_check')}, ensure_ascii=False))
    return 0 if result.wasSuccessful() and (not syntax or syntax.returncode == 0) and whitespace.returncode == 0 and original.returncode == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
