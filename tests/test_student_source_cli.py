"""The CLI is local-only; every fixture here is deliberately synthetic."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from course_assistant.student_sources import SourceTaskStore, StudentSourceError
from scripts import run_student_source_probe as cli


class StudentSourceCLITests(unittest.TestCase):
    def setUp(self):
        (cli.ROOT / "local-only").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="student-cli-test-", dir=cli.ROOT / "local-only")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.grant = self.base / "task", self.base / "grant.json"
        self.sentinel = "SYNTHETIC_PRIVATE_SENTINEL"

    def args(self, *args, output=None):
        values = ["--root", str(self.root), "--grant", str(self.grant)]
        if output is not None:
            values.extend(["--output", str(output)])
        return values + list(args)

    def process(self, *args, output=None, expected=0):
        result = subprocess.run(
            [sys.executable, str(cli.ROOT / "scripts" / "run_student_source_probe.py"),
             *self.args(*args, output=output)],
            capture_output=True, text=True, timeout=10, cwd=cli.ROOT)
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn(self.sentinel, result.stdout + result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        return result, json.loads(result.stdout if expected == 0 else result.stderr)

    def in_process(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def initialize_start(self):
        self.process("init", "--synthetic")
        _, start = self.process("start", "--term", "2026-2027-1", "--origin",
                                "https://synthetic.example.invalid")
        return start

    def envelope(self, start):
        return {
            "schema": "student-acquisition/1", "domain": "academic",
            "source_alias": "SOURCE-A", "source_origin": "https://synthetic.example.invalid",
            "generation": start["generation"], "source_subject_ref": None,
            "scope": {"target_term": "2026-2027-1", "academic_year_raw": "2026",
                      "term_raw": "1", "query_kind": "synthetic_grade_slice"},
            "coverage": {"page_index": 1, "total_rows": 2, "has_more": False,
                         "complete_within_scope": True},
            "response": {"http_status": 200, "content_type": "application/json",
                         "response_state": "business"},
            "payload": {"items": [
                {"xkkh": "SYNTHETIC-SELECTION-A", "kcdm": "SYNTHETIC-PE",
                 "kcmc": self.sentinel, "xf": "1.0", "cj": "95", "jd": "4.5"},
                {"xkkh": "SYNTHETIC-SELECTION-B", "kcdm": "SYNTHETIC-PE",
                 "kcmc": self.sentinel, "xf": "1.0", "cj": "85", "jd": "3.5"},
            ], "password": "SYNTHETIC_CREDENTIAL_SENTINEL"},
        }

    def write_input(self, value, name="observation.json"):
        path = self.base / name
        cli.output_private(path, json.dumps(value, ensure_ascii=False))
        return path

    def test_init_default_has_no_student_owner_and_private_permissions(self):
        _, result = self.process("init")
        self.assertEqual(result["data_kind"], "personal_source_observation")
        self.assertEqual(result["official_authentication"], "not_configured")
        grant = json.loads(self.grant.read_text())
        self.assertIsNone(grant["owner_student_id"])
        self.assertEqual(grant["subject_kind"], "local_review")
        self.assertRegex(grant["operator_ref"], r"^LOCAL-[a-f0-9]{24}$")
        self.assertNotIn("student_id", grant)
        self.assertNotIn("source_subject_ref", grant)
        self.assertNotIn("source_alias", grant)
        for path in (self.root, self.grant, self.root / "student_source.sqlite3"):
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_init_does_not_overwrite_existing_root_or_grant(self):
        self.process("init")
        original = self.grant.read_bytes()
        _, failure = self.process("init", expected=2)
        self.assertEqual(failure["code"], "task_already_exists")
        self.assertEqual(self.grant.read_bytes(), original)
        self.root = self.base / "second-task"
        self.process("init", expected=2)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.grant.read_bytes(), original)

    def test_multiple_processes_ingest_report_readiness_and_revoke(self):
        start = self.initialize_start()
        self.assertEqual(start["status"], "source_subject_unknown")
        attempt = start["attempt_id"]
        source = self.write_input(self.envelope(start))
        _, receipt = self.process("ingest", "--attempt", attempt, "--input", str(source))
        self.assertEqual(receipt["rows"], 2)
        self.assertEqual(receipt["stripped_field_count"], 1)
        _, duplicate = self.process("ingest", "--attempt", attempt, "--input", str(source))
        self.assertEqual(duplicate["status"], "idempotent")
        _, description = self.process("describe", "--attempt", attempt)
        self.assertEqual(description["domains"]["academic"]["observations"], 1)
        self.assertEqual(description["domains"]["profile"]["status"], "not_acquired")
        _, readiness = self.process("readiness", "--attempt", attempt)
        self.assertEqual(readiness["student_plan_binding"], "not_verified")
        self.assertEqual(readiness["recommendation_input"], "input_not_ready_for_recommendation")
        report_path = self.base / "report.html"
        _, report = self.process("report", "--attempt", attempt, output=report_path)
        self.assertTrue(report["report_created"])
        body = report_path.read_text()
        self.assertIn(self.sentinel, body)
        self.assertNotIn("SYNTHETIC_CREDENTIAL_SENTINEL", body)
        self.assertIn("不构成本项目学校认证", body)
        self.assertIn("尚不可用于本人推荐", body)
        self.assertEqual(report_path.stat().st_mode & 0o077, 0)
        self.process("revoke")
        _, denied = self.process("describe", "--attempt", attempt, expected=2)
        self.assertEqual(denied["code"], "grant_revoked")
        self.process("start", "--term", "2026-2027-1", "--origin",
                     "https://synthetic.example.invalid", expected=2)

    def test_failure_retains_old_observation_as_historical_private_report(self):
        start = self.initialize_start()
        source = self.write_input(self.envelope(start))
        self.process("ingest", "--attempt", start["attempt_id"], "--input", str(source))
        _, failure = self.process("failure", "--attempt", start["attempt_id"],
                                  "--domain", "academic", "--status", "source_unavailable")
        self.assertEqual(failure["status"], "source_unavailable")
        report = self.base / "failed-report.html"
        self.process("report", "--attempt", start["attempt_id"], output=report)
        body = html.unescape(report.read_text())
        self.assertIn('"historical_only": true', body)
        self.assertIn('"current_status": "source_unavailable"', body)

    def test_stopped_attempt_reports_fixed_failure_without_old_personal_body(self):
        self.process("init", "--synthetic")
        for status in sorted(cli.REPORT_NO_BODY_STATES - {"source_generation_changed"}):
            with self.subTest(status=status):
                _, start = self.process("start", "--term", "2026-2027-1", "--origin",
                                        "https://synthetic.example.invalid")
                source = self.write_input(self.envelope(start), name=status + ".json")
                self.process("ingest", "--attempt", start["attempt_id"], "--input", str(source))
                self.process("failure", "--attempt", start["attempt_id"], "--domain", "academic",
                             "--status", status)
                path = self.base / (status + ".html")
                _, receipt = self.process("report", "--attempt", start["attempt_id"], output=path)
                self.assertTrue(receipt["report_created"])
                self.assertEqual(receipt["report_content"], "status_only")
                body = path.read_text()
                self.assertIn(status, body)
                self.assertNotIn(self.sentinel, body)
                self.assertNotIn("SYNTHETIC-SELECTION-A", body)
                self.assertIn("本轮未取得可审阅正文", body)

    def test_previous_generation_failure_report_contains_no_old_personal_body(self):
        start = self.initialize_start()
        source = self.write_input(self.envelope(start))
        self.process("ingest", "--attempt", start["attempt_id"], "--input", str(source))
        self.process("start", "--term", "2026-2027-1", "--origin", "https://synthetic.example.invalid")
        path = self.base / "previous-generation.html"
        self.process("report", "--attempt", start["attempt_id"], output=path)
        self.assertNotIn(self.sentinel, path.read_text())
        self.assertNotIn("SYNTHETIC-SELECTION-A", path.read_text())
        self.assertIn("source_generation_changed", path.read_text())

    def test_report_does_not_suppress_grant_expiry_revocation_or_other_errors(self):
        start = self.initialize_start()
        path = self.base / "denied-report.html"
        for reason in ("grant_expired", "grant_revoked", "stale_grant", "invalid_domain"):
            with self.subTest(reason=reason), patch.object(SourceTaskStore, "private_snapshot",
                                                          side_effect=StudentSourceError(reason)):
                code, stdout, stderr = self.in_process(self.args("report", "--attempt", start["attempt_id"],
                                                                output=path))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr)["code"], reason)
                self.assertFalse(path.exists())

    def test_report_html_escapes_personal_text_without_active_remote_resources(self):
        text = '</pre><script src="https://example.invalid/secret">alert(1)</script>'
        result = cli.private_report_html({}, {}, {"profile": {"text": text}})
        self.assertIn("&lt;/pre&gt;&lt;script", result)
        self.assertNotIn('<script src="', result)
        self.assertNotIn("<iframe", result)
        self.assertIn("default-src 'none'", result)
        self.assertIn("本轮未取得可审阅正文", result)

    def test_private_path_rejects_public_paths_and_symlink_escapes(self):
        forbidden = cli.ROOT / "validation" / "SYNTHETIC_PRIVATE_SENTINEL.json"
        with self.assertRaises(cli.ProbeCLIError) as raised:
            cli.private_path(forbidden)
        self.assertEqual(raised.exception.code, "invalid_private_path")
        with tempfile.TemporaryDirectory(prefix="student-cli-outside-") as outside:
            link = self.base / "escape"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(cli.ProbeCLIError):
                cli.private_path(link / "report.html")
        private_root = cli.ROOT / "private"
        self.assertEqual(cli.private_path(private_root / "SYNTHETIC-local-task"),
                         (private_root / "SYNTHETIC-local-task").resolve())

    def test_all_supplied_cli_paths_are_validated_before_task_io(self):
        for extra in (("ingest", "--attempt", "TASK-SYNTHETIC", "--input",
                       str(cli.ROOT / "README.md")),
                      ("readiness", "--attempt", "TASK-SYNTHETIC", "--catalogue",
                       str(cli.ROOT / "README.md"))):
            with self.subTest(extra=extra), patch.object(SourceTaskStore, "__init__") as init:
                code, stdout, stderr = self.in_process(self.args(*extra))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr)["code"], "invalid_private_path")
                init.assert_not_called()
        with patch.object(SourceTaskStore, "__init__") as init:
            code, _, stderr = self.in_process(self.args("report", "--attempt", "TASK-SYNTHETIC",
                                                        output=cli.ROOT / "README.md"))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stderr)["code"], "invalid_private_path")
            init.assert_not_called()

    def test_private_output_exclusive_does_not_overwrite_existing_file(self):
        path = self.base / "report.html"
        cli.output_private(path, "original")
        with self.assertRaises(cli.ProbeCLIError) as raised:
            cli.output_private(path, "replacement")
        self.assertEqual(raised.exception.code, "output_unavailable")
        self.assertEqual(path.read_text(), "original")

    def test_output_creates_every_nested_directory_with_private_permissions(self):
        path = self.base / "new-report-batch" / "nested" / "reports" / "report.html"
        previous_umask = os.umask(0o022)
        try:
            cli.output_private(path, "synthetic report")
        finally:
            os.umask(previous_umask)
        for directory in (path.parent, path.parent.parent, path.parent.parent.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_init_creates_each_missing_task_and_grant_parent_with_mode_0700(self):
        self.root = self.base / "new-task-batch" / "nested" / "task"
        self.grant = self.base / "new-grant-batch" / "nested" / "grant.json"
        self.process("init", "--synthetic")
        for directory in (self.root, self.root.parent, self.root.parent.parent,
                          self.grant.parent, self.grant.parent.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.grant.stat().st_mode & 0o777, 0o600)

    def test_report_refuses_existing_wide_parent_without_changing_files_or_permissions(self):
        start = self.initialize_start()
        parent = self.base / "existing-wide-report-directory"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        existing = parent / "original.txt"
        existing.write_text("SYNTHETIC-original")
        existing.chmod(0o644)
        path = parent / "report.html"
        _, failure = self.process("report", "--attempt", start["attempt_id"], output=path, expected=2)
        self.assertEqual(failure["code"], "private_permissions_required")
        self.assertFalse(path.exists())
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(existing.stat().st_mode & 0o777, 0o644)
        self.assertEqual(existing.read_text(), "SYNTHETIC-original")

    def test_init_refuses_existing_wide_grant_parent_and_rolls_back_new_task(self):
        parent = self.base / "existing-wide-grant-directory"
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        existing = parent / "original.txt"
        existing.write_text("SYNTHETIC-original")
        self.grant = parent / "grant.json"
        _, failure = self.process("init", "--synthetic", expected=2)
        self.assertEqual(failure["code"], "private_permissions_required")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.grant.exists())
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(existing.read_text(), "SYNTHETIC-original")

    def test_existing_grant_in_wide_parent_is_denied_without_permission_repair(self):
        self.grant = self.base / "grant-holder" / "grant.json"
        start = self.initialize_start()
        original = self.grant.read_bytes()
        self.grant.parent.chmod(0o755)
        _, failure = self.process("describe", "--attempt", start["attempt_id"], expected=2)
        self.assertEqual(failure["code"], "private_permissions_required")
        self.assertEqual(self.grant.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.grant.read_bytes(), original)

    def test_shared_material_roots_are_never_chmodded_for_nested_or_direct_outputs(self):
        with tempfile.TemporaryDirectory(prefix="student-cli-scope-fixture-") as temporary:
            fixture_root = Path(temporary)
            for name in ("local-only", "private"):
                shared = fixture_root / name
                shared.mkdir(mode=0o755)
                shared.chmod(0o755)
                with self.subTest(name=name), patch.object(cli, "ROOT", fixture_root):
                    nested = shared / "new-private-batch" / "report.html"
                    cli.output_private(nested, "synthetic report")
                    self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)
                    with self.assertRaises(cli.ProbeCLIError) as raised:
                        cli.output_private(shared / "direct.html", "synthetic report")
                    self.assertEqual(raised.exception.code, "private_permissions_required")
                    self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
                    self.assertFalse((shared / "direct.html").exists())

    def test_malformed_json_and_argument_errors_have_fixed_codes_no_values(self):
        start = self.initialize_start()
        path = self.base / "bad.json"
        cli.output_private(path, self.sentinel)
        _, failure = self.process("ingest", "--attempt", start["attempt_id"], "--input", str(path), expected=2)
        self.assertEqual(failure["code"], "input_unavailable")
        code, stdout, stderr = self.in_process(self.args("failure", "--attempt", self.sentinel,
                                                       "--domain", self.sentinel, "--status", self.sentinel))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn(self.sentinel, stderr)
        self.assertEqual(json.loads(stderr)["code"], "invalid_arguments")

    def test_unexpected_exception_text_and_unrecognised_codes_are_not_printed(self):
        start = self.initialize_start()
        for error in (RuntimeError(self.sentinel), StudentSourceError(self.sentinel)):
            with self.subTest(error=type(error).__name__), patch.object(SourceTaskStore, "describe", side_effect=error):
                code, stdout, stderr = self.in_process(self.args("describe", "--attempt", start["attempt_id"]))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(self.sentinel, stderr)
                self.assertEqual(json.loads(stderr)["code"], "probe_failed")

    def test_report_refuses_changed_or_revoked_scope_before_or_after_write(self):
        start = self.initialize_start()
        store = SourceTaskStore(self.root, self.grant)
        description = store.describe(start["attempt_id"])
        report_path = self.base / "scope-change-report.html"
        for changed_on in (3, 4):
            calls = 0

            def changed_description(*args):
                nonlocal calls
                calls += 1
                if calls == changed_on:
                    raise StudentSourceError("grant_revoked")
                return description

            # Report calls describe first, once inside readiness, before write,
            # then after write. Both revocation points must leave no report.
            with self.subTest(changed_on=changed_on), patch.object(SourceTaskStore, "describe", side_effect=changed_description):
                code, stdout, stderr = self.in_process(self.args("report", "--attempt", start["attempt_id"],
                                                                output=report_path))
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr)["code"], "grant_revoked")
                self.assertFalse(report_path.exists())

    def test_init_rolls_back_new_grant_and_task_when_database_setup_fails(self):
        with patch.object(SourceTaskStore, "__init__", side_effect=OSError(self.sentinel)):
            code, stdout, stderr = self.in_process(self.args("init", "--synthetic"))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn(self.sentinel, stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.grant.exists())


if __name__ == "__main__":
    unittest.main()
