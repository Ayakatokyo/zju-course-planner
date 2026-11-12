"""Local launcher lifecycle checks without external services or personal data."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from course_assistant import server
from scripts import run_vector_app as launcher


class VectorLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ('semantic_runtime/manifest.json', 'semantic_runtime/venv/bin/python',
                     'curriculum_vectors/catalogue.sqlite3', 'qdrant_server/bin/qdrant', 'qdrant_server/config.yaml'):
            path = self.root / 'private' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        (self.root / 'private/ocr_runtime/tessdata').mkdir(parents=True)
        for replacement in (patch.object(launcher, 'ROOT', self.root), patch.object(launcher.os, 'chdir'),
                            patch.dict(os.environ, {'COURSE_DATA_TEST_TERM': '2026-2027-AW'}, clear=True),
                            patch.object(sys, 'path', list(sys.path)),
                            patch.object(sys, 'argv', ['run_vector_app.py', '--enable-school', '--port', '18875'])):
            replacement.start()
            self.addCleanup(replacement.stop)
        for attribute, replacement in (
                ('healthy', patch.object(launcher, 'qdrant_healthy', return_value=True)),
                ('launch', patch.object(launcher.subprocess, 'Popen')),
                ('run', patch.object(launcher.runpy, 'run_module'))):
            setattr(self, attribute, replacement.start())
            self.addCleanup(replacement.stop)
        self.process = self.launch.return_value
        self.process.poll.return_value = None

    def test_help_requires_no_runtime_and_starts_no_service(self):
        for flag in ('--help', '-h'):
            with self.subTest(flag=flag), patch.object(launcher, 'ROOT', self.root / 'missing'), \
                    patch.object(sys, 'argv', ['run_vector_app.py', flag]):
                launcher.main()
                self.assertEqual(sys.argv, ['course_assistant.server', flag])
        self.healthy.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(self.run.call_count, 2)

    def test_missing_runtime_is_reported_before_service_start(self):
        (self.root / 'private/semantic_runtime/manifest.json').unlink()
        with self.assertRaisesRegex(SystemExit, 'COURSE_SEMANTIC_ENCODER_MANIFEST'):
            launcher.main()
        self.healthy.assert_not_called()
        self.launch.assert_not_called()
        self.run.assert_not_called()

    def test_reuses_existing_service_and_preserves_application_arguments(self):
        launcher.main()
        self.launch.assert_not_called()
        self.assertEqual(sys.argv, ['course_assistant.server', '--enable-school', '--port', '18875'])
        self.assertEqual(os.environ['COURSE_DATA_TEST_TERM'], '2026-2027-AW')
        self.assertEqual(os.environ['COURSE_QDRANT_URL'], 'http://127.0.0.1:6333')
        self.assertEqual(os.environ['COURSE_LOGIN_PREPARE'], 'true')
        self.assertEqual(os.environ['COURSE_CURRICULUM_CORPUS'],
                         str(self.root / 'private/curriculum_vectors/catalogue.sqlite3'))
        self.run.assert_called_once_with('course_assistant.server', run_name='__main__')

    def test_application_failure_preserves_preexisting_service(self):
        self.run.side_effect = OSError('SYNTHETIC-PORT-IN-USE')
        with self.assertRaisesRegex(OSError, 'SYNTHETIC-PORT-IN-USE'):
            launcher.main()
        self.launch.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_normal_application_exit_retains_started_persistent_service(self):
        self.healthy.side_effect = [False, True]
        launcher.main()
        self.launch.assert_called_once()
        self.process.terminate.assert_not_called()
        self.assertTrue(self.launch.call_args.kwargs['start_new_session'])
        self.assertLessEqual(self.healthy.call_args.kwargs['timeout'], 2)

    def test_application_failure_reaps_only_its_started_service(self):
        self.healthy.side_effect = [False, True]
        self.run.side_effect = OSError('SYNTHETIC-PORT-IN-USE')
        self.process.wait.side_effect = [subprocess.TimeoutExpired('SYNTHETIC-QDRANT', 5), 0]
        with self.assertRaisesRegex(OSError, 'SYNTHETIC-PORT-IN-USE'):
            launcher.main()
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)

    def test_startup_deadline_reaps_owned_process_without_running_application(self):
        self.healthy.return_value = False
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(SystemExit, 'not ready'):
                launcher.main()
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=5)
        self.run.assert_not_called()

    def test_early_process_exit_does_not_start_application(self):
        self.healthy.return_value = False
        self.process.poll.return_value = 1
        with self.assertRaisesRegex(SystemExit, 'startup failed'):
            launcher.main()
        self.run.assert_not_called()
        self.process.terminate.assert_not_called()


class ApplicationStartupTests(unittest.TestCase):
    def test_port_bind_failure_closes_initialized_application(self):
        app = Mock()
        with patch.object(sys, 'argv', ['course_assistant.server', '--port', '18875']), \
                patch.object(server, 'Application', return_value=app), \
                patch.object(server, 'read_config', return_value={}), \
                patch.object(server, 'ModelClient'), \
                patch.object(server, 'ThreadingHTTPServer', side_effect=OSError('SYNTHETIC-PORT-IN-USE')):
            with self.assertRaisesRegex(OSError, 'SYNTHETIC-PORT-IN-USE'):
                server.main()
        app.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
