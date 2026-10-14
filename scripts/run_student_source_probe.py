#!/usr/bin/env python3
"""Inspect stripped student-source observations in a restricted local task.

No browser credentials, upstream requests, model calls, or main-app workspace
are handled by this command. Public output contains only task receipts.
"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOMAINS = ("profile", "academic", "timetable", "exams")
FAILURE_STATUSES = (
    "awaiting_user_login", "source_session_expired", "source_subject_changed",
    "source_unavailable", "permission_denied", "captcha_required",
    "schema_changed", "incomplete_capture", "user_confirmation_required",
)
REPORT_NO_BODY_STATES = frozenset((
    "awaiting_user_login", "source_session_expired", "source_subject_changed",
    "permission_denied", "captcha_required", "source_generation_changed",
    "user_confirmation_required",
))
ERROR_CODES = frozenset((
    "invalid_arguments", "inaccessible_resource", "invalid_private_path",
    "input_unavailable", "output_unavailable", "stale_input", "invalid_scope",
    "source_unavailable", "source_subject_changed", "source_subject_unknown",
    "source_session_expired", "permission_denied", "captcha_required",
    "schema_changed", "incomplete_capture", "grant_revoked", "grant_expired",
    "invalid_grant", "invalid_envelope", "invalid_attempt", "invalid_domain",
    "invalid_term", "invalid_origin", "task_already_exists", "probe_failed",
    "report_scope_changed", "interrupted",
    "capture_limit_exceeded", "coverage_not_open", "grant_denied", "grant_unavailable",
    "invalid_alias", "invalid_capture", "invalid_coverage", "invalid_failure_state",
    "invalid_plan_catalogue", "invalid_response_metadata", "new_attempt_required",
    "page_budget_exceeded", "private_permissions_required", "source_generation_changed",
    "source_origin_denied", "source_scope_changed", "stale_grant", "target_term_required",
    "task_not_found", "unsafe_business_value",
    "user_confirmation_required",
))


class ProbeCLIError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal messages echo rejected arguments, including paths.
        raise ProbeCLIError("invalid_arguments")


def private_path(value, *, allow_root=False):
    """Require a resolved path beneath this project's ignored material roots."""
    try:
        path = Path(value).expanduser().resolve()
        allowed = ((ROOT / "local-only").resolve(), (ROOT / "private").resolve())
        if not any((allow_root or path != base) and path.is_relative_to(base) for base in allowed):
            raise ProbeCLIError("invalid_private_path")
        return path
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        if isinstance(error, ProbeCLIError):
            raise
        raise ProbeCLIError("invalid_private_path") from None


def private_directory(value, *, exclusive=False, create=True):
    """Create missing private directories one at a time, each with mode 0700.

    Existing directories are never chmodded. Only the direct material directory
    must be private; the shared local-only/private roots can remain unchanged.
    """
    path = private_path(value, allow_root=True)
    if exclusive and path.exists():
        raise ProbeCLIError("task_already_exists")
    missing, current = [], path
    while not current.exists():
        if not create:
            raise ProbeCLIError("output_unavailable")
        missing.append(current)
        current = current.parent
    try:
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                if exclusive and directory == path:
                    raise ProbeCLIError("task_already_exists") from None
            if not directory.is_dir():
                raise ProbeCLIError("output_unavailable")
            if directory.stat().st_mode & 0o077:
                raise ProbeCLIError("private_permissions_required")
        if not path.is_dir():
            raise ProbeCLIError("output_unavailable")
        if path.stat().st_mode & 0o077:
            raise ProbeCLIError("private_permissions_required")
    except OSError:
        raise ProbeCLIError("output_unavailable") from None
    return path


def read_private_json(path):
    path = private_path(path)
    try:
        # A small read budget prevents accidental input of unbounded exports.
        with path.open("rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ProbeCLIError("input_unavailable") from None


def output_private(path, text):
    """Create one new private file; existing outputs are never overwritten."""
    path = private_path(path)
    private_directory(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise ProbeCLIError("output_unavailable") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
    except (OSError, UnicodeError):
        # A failed report must not leave a partially published file.
        try:
            path.unlink()
        except OSError:
            pass
        raise ProbeCLIError("output_unavailable") from None


def initialize_task(root_path, grant_path, *, synthetic=False, clock=time.time):
    """Create a new task/grant pair, with no student owner or source identity."""
    from course_assistant.student_sources import SourceTaskStore

    root, grant_path = private_path(root_path), private_path(grant_path)
    if root == grant_path or root.exists() or grant_path.exists():
        raise ProbeCLIError("task_already_exists")
    grant = {
        "security_realm": "restricted_personal_reference",
        "purpose": "local_student_source_review", "subject_kind": "local_review",
        "auth_mode": "local_operation", "owner_student_id": None,
        "data_kind": "synthetic" if synthetic else "personal_source_observation",
        "domains": list(DOMAINS), "grant_id": "GRANT-" + secrets.token_hex(12),
        "operator_ref": "LOCAL-" + secrets.token_hex(12), "revoked": False,
        "expires_at": clock() + 24 * 60 * 60,
    }
    root_created = grant_created = False
    try:
        private_directory(root, exclusive=True)
        root_created = True
        output_private(grant_path, json.dumps(grant, ensure_ascii=False, indent=2) + "\n")
        grant_created = True
        SourceTaskStore(root, grant_path, clock=clock)
    except Exception:
        # Roll back only files created by this initialization, never prior inputs.
        if grant_created:
            grant_path.unlink(missing_ok=True)
        if root_created:
            for name in ("student_source.sqlite3", "student_source.sqlite3-journal",
                         "student_source.sqlite3-wal", "student_source.sqlite3-shm"):
                (root / name).unlink(missing_ok=True)
            try:
                root.rmdir()
            except OSError:
                pass
        raise
    return {"status": "local_task_initialized", "security_realm": grant["security_realm"],
            "data_kind": grant["data_kind"], "official_authentication": "not_configured",
            "student_plan_binding": "not_verified"}


def private_report_html(description, readiness, snapshots):
    """Render only escaped, already-stripped private snapshots, without assets."""
    labels = {
        "profile": "本人资料", "academic": "成绩与主修观察",
        "timetable": "目标学期课表观察", "exams": "考试观察",
    }

    def block(value):
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        return "<pre>" + html.escape(text, quote=True) + "</pre>"

    sections = ["<section><h2>本机任务与来源范围</h2>" + block(description) + "</section>",
                "<section><h2>输入就绪度与缺口</h2>" + block(readiness) + "</section>"]
    for domain in DOMAINS:
        value = snapshots.get(domain)
        body = ("<p>本轮未取得可审阅正文；不能据此认定该域为空。</p>"
                if value is None else block(value))
        sections.append("<section><h2>" + labels[domain] + "</h2>" + body + "</section>")
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>学生来源本机私有报告</title><style>
body{margin:0;background:#fff;color:#252638;font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{max-width:1000px;margin:auto;padding:32px 20px}h1{font-size:26px}h2{font-size:20px}
section{margin:28px 0;border-top:1px solid #ddd;padding-top:20px}
.status{padding:14px;background:#fff4dd;border-radius:6px}pre{font:13px/1.65 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
</style></head><body><main><h1>学生来源本机私有报告</h1>
<p class="status">独立受限本机审阅。来源观察不构成本项目学校认证，未形成可信本人绑定，尚不可用于本人推荐。</p>
<p>逐域保留原观察、覆盖范围、解析结果与失败；未知和未取得数据不能计为零学分、没有课程或正式中选。方案仅为候选，需核对实际年级和路径。可靠校历未取得时不推算日期或生成真实 ICS。</p>
""" + "".join(sections) + "</main></body></html>\n"


def _parser():
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--grant", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--synthetic", action="store_true")
    start = commands.add_parser("start")
    start.add_argument("--term", required=True)
    start.add_argument("--origin", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--attempt", required=True)
    ingest.add_argument("--input", required=True, type=Path)
    describe = commands.add_parser("describe")
    describe.add_argument("--attempt", required=True)
    for name in ("readiness", "report"):
        command = commands.add_parser(name)
        command.add_argument("--attempt", required=True)
        command.add_argument("--catalogue", type=Path)
    failure = commands.add_parser("failure")
    failure.add_argument("--attempt", required=True)
    failure.add_argument("--domain", required=True, choices=DOMAINS)
    failure.add_argument("--status", required=True, choices=FAILURE_STATUSES)
    commands.add_parser("revoke")
    return parser


def _run(argv):
    args = _parser().parse_args(argv)
    root, grant = private_path(args.root), private_path(args.grant)
    output = private_path(args.output) if args.output is not None else None
    if args.command == "report" and output is None:
        raise ProbeCLIError("invalid_arguments")
    if args.command != "report" and output is not None:
        raise ProbeCLIError("invalid_arguments")
    # Validate every supplied path before loading observations or invoking a task.
    input_path = private_path(args.input) if hasattr(args, "input") else None
    catalogue_path = private_path(args.catalogue) if getattr(args, "catalogue", None) else None
    from course_assistant.student_sources import SourceTaskStore, StudentSourceError

    if args.command == "init":
        result = initialize_task(root, grant, synthetic=args.synthetic)
    else:
        private_directory(grant.parent, create=False)
        store = SourceTaskStore(root, grant)
        if args.command == "start":
            result = store.create_attempt(args.term, args.origin)
        elif args.command == "ingest":
            result = store.ingest(args.attempt, read_private_json(input_path))
        elif args.command == "describe":
            result = store.describe(args.attempt)
        elif args.command == "failure":
            result = store.fail_domain(args.attempt, args.domain, args.status)
        elif args.command == "revoke":
            result = store.revoke()
        else:
            catalogue = read_private_json(catalogue_path) if catalogue_path is not None else None
            if args.command == "report":
                description = store.describe(args.attempt)
                result = store.readiness(args.attempt, catalogue=catalogue)
                snapshots = {}
                blocked_reasons = set()
                for domain in DOMAINS:
                    try:
                        snapshots[domain] = store.private_snapshot(args.attempt, domain)
                    except StudentSourceError as error:
                        if error.code not in REPORT_NO_BODY_STATES:
                            raise
                        snapshots[domain] = None
                        blocked_reasons.add(error.code)
                if blocked_reasons:
                    snapshots = dict.fromkeys(DOMAINS)
                report_description = {**description, "private_body_access": {
                    "status": "blocked" if blocked_reasons else "available_within_task",
                    "reasons": sorted(blocked_reasons),
                }}
                rendered = private_report_html(report_description, result, snapshots)
                if store.describe(args.attempt) != description:
                    raise ProbeCLIError("report_scope_changed")
                output_private(output, rendered)
                try:
                    if store.describe(args.attempt) != description:
                        raise ProbeCLIError("report_scope_changed")
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
                result = {"status": "restricted_personal_reference", "report_created": True,
                          "school_auth_status": "not_configured", "recommendation_ready": False,
                          "report_content": "status_only" if not any(snapshots.values()) else "private_observations"}
            else:
                result = store.readiness(args.attempt, catalogue=catalogue)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


def main(argv=None):
    try:
        return _run(argv)
    except KeyboardInterrupt:
        code, exit_code = "interrupted", 130
    except Exception as error:
        candidate = getattr(error, "code", getattr(error, "reason", None))
        code = candidate if isinstance(candidate, str) and candidate in ERROR_CODES else "probe_failed"
        exit_code = 2
    print(json.dumps({"status": "student_source_probe_failed", "code": code}), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
