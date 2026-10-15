"""Real DeepSeek cold-start check with exclusively synthetic school transport.

The explicit flag permits reading local model configuration, never printing it.
No source is prefetched: the first submitted grade question must prepare its
synthetic source through the service. Only bounded synthetic answers, counts,
states and durations enter the public receipt; provider reasoning stays in the
temporary private database/cache and is destroyed when this process exits.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.model_client import ModelClient, read_config
from course_assistant.server import Application
from validation.run_chat_inspection_check import DeepSeekOnlyOpener
from validation.run_personal_ui_fixture import PersonalSyntheticSchool

RECEIPT = ROOT / "validation" / "personal_retrieval_progress_20261015_model.json"
QUESTIONS = ("我的成绩怎么样", "你是谁")


def safe_code(error):
    value = getattr(error, "code", getattr(error, "reason", None))
    return value if isinstance(value, str) and value.isascii() and len(value) <= 100 else "synthetic_validation_failed"


class CountingSyntheticSchool(PersonalSyntheticSchool):
    def __init__(self):
        super().__init__()
        self.calls = []

    def request(self, spec):
        self.calls.append({key: spec[key] for key in ("operation", "target_term", "page") if key in spec})
        return super().request(spec)


class AuditedStreamingModel:
    """Forward only already projected synthetic messages; retain counts only."""
    def __init__(self, client, calls):
        self.client, self.calls = client, calls

    def status(self):
        return self.client.status()

    def with_options(self, model, thinking=False):
        return type(self)(self.client.with_options(model, thinking), self.calls)

    def complete_stream_with_metadata(self, messages, tools, *, observer=None, on_reasoning=None):
        raw = json.dumps(messages, ensure_ascii=False)
        for forbidden in ("SYNTHETIC-SCHOOL-A", "SYNTHETIC-LOGIN-A", "SYNTHETIC-TRANSIENT-SECRET",
                          "SOURCE-", "OBS-", "PLAN-", "PERSONAL-REF-", "scope_key", "owner_student_id", "security_realm"):
            if forbidden in raw:
                raise AssertionError("validation_projection_not_synthetic_only")
        row = {"model": self.status()["model"], "thinking": self.status()["thinking"],
               "message_count": len(messages), "streaming": True, "reasoning_delta_count": 0,
               "reasoning_delta_chars": 0, "projected_record_count": 0, "projected_summary_counts": []}
        for message in messages:
            if message.get("role") == "tool":
                value = json.loads(message["content"])
                row["projected_record_count"] += len(value.get("records", []))
                summary = value.get("source_grade_summary")
                if isinstance(summary, dict):
                    row["projected_summary_counts"].append({key: summary[key] for key in
                        ("matched_records", "numeric_grade_count", "non_numeric_grade_count", "unknown_grade_count") if key in summary})
        self.calls.append(row)
        def reasoning(delta):
            row["reasoning_delta_count"] += 1
            row["reasoning_delta_chars"] += len(delta)
            if on_reasoning is not None:
                on_reasoning(delta)
        def observed(metadata):
            row.update(status=metadata["status"], elapsed_ms=metadata["elapsed_ms"],
                       usage=ModelClient._usage(metadata.get("usage")),
                       provider_reasoning_chars=len(metadata.get("provider_reasoning", "")))
            if metadata.get("code"):
                row["code"] = metadata["code"]
            if observer is not None:
                observer(metadata)
        return self.client.complete_stream_with_metadata(messages, tools, observer=observed, on_reasoning=reasoning)


def run_turn(app, context, body):
    """Poll the actual authenticated progress reader while the real call runs."""
    started, samples, last_sequence = time.monotonic(), [], None
    live_reasoning, live_tools = False, False
    progress_errors = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.personal_chat.turn, body, context)
        while True:
            pending = not future.done()
            try:
                progress = app.personal_chat.read_progress({"request_id": body["request_id"]}, context)
                active = progress.get("active_reasoning") or {}
                kinds = list(dict.fromkeys(item["kind"] for item in progress.get("events", [])))
                chars = len(active.get("text", ""))
                live_reasoning = live_reasoning or bool(pending and chars)
                live_tools = live_tools or bool(pending and "tool.end" in kinds)
                sequence = progress.get("next_sequence")
                if sequence != last_sequence and len(samples) < 512:
                    samples.append({"elapsed_ms": round((time.monotonic() - started) * 1000),
                        "status": progress["status"], "terminal": progress["terminal"], "sequence": sequence,
                        "event_kinds": kinds, "active_reasoning_chars": chars, "turn_in_flight": pending})
                    last_sequence = sequence
            except Exception as error:
                progress_errors.append(safe_code(error))
            if not pending:
                break
            # This is a polling interval, never a model retry.
            time.sleep(0.05)
        result = future.result()
    return result, {"samples": samples, "observed_reasoning_while_running": live_reasoning,
                    "observed_tool_event_while_running": live_tools, "progress_error_codes": progress_errors,
                    "elapsed_ms": round((time.monotonic() - started) * 1000)}


def result_view(result):
    citations = result.get("citations", [])
    tools = result.get("tool_results", [])
    return {"status": result["status"], "code": result.get("code"), "reply": result["reply"],
        "model": result.get("model"), "thinking": result.get("thinking"), "history_reset": result.get("history_reset"),
        "reply_origin": result.get("reply_origin"), "decision_kind": result.get("model_decision", {}).get("kind"),
        "tools": [item["name"] for item in tools], "citation_count": len(citations),
        "citation_kinds": [item.get("kind") for item in citations],
        "summary_counts": [{key: item["source_grade_summary"][key] for key in
            ("matched_records", "numeric_grade_count", "non_numeric_grade_count", "unknown_grade_count")
            if key in item["source_grade_summary"]} for item in citations if isinstance(item.get("source_grade_summary"), dict)],
        "diagnostics": result.get("diagnostics")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model-synthetic-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=RECEIPT)
    args = parser.parse_args()
    started, calls, schools = time.monotonic(), [], []
    opener = DeepSeekOnlyOpener()
    base = ModelClient(read_config(ROOT / ".env"), opener=opener)
    if not base.status()["configured"]:
        parser.error("model_not_configured")
    receipt = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "status": "failed",
        "scope": "Real DeepSeek streaming + cold-start exclusively synthetic school transport; not personal-source acceptance",
        "provider": "DeepSeek", "model": base.status()["model"], "timeout_s": base.timeout,
        "real_school_network": False, "real_personal_data": False, "full_reasoning_saved": False,
        "prefetched_sources": False, "calls": calls, "results": [], "checks": {}}
    def factory():
        school = CountingSyntheticSchool()
        schools.append(school)
        return school
    try:
        # If application routing ever falls back to the real school client, fail
        # before constructing it; only the guarded DeepSeek opener may network.
        with patch("course_assistant.school_http.SchoolHTTPClient", side_effect=AssertionError("real_school_client_forbidden")):
            with tempfile.TemporaryDirectory(prefix="synthetic-cold-retrieval-progress-") as directory:
                root = Path(directory).resolve()
                app = Application(root / "app.sqlite3", model=AuditedStreamingModel(base, calls),
                    school_client_factory=factory, school_root=root / "private" / "school")
                try:
                    token, _ = app.school.login({"username": "SYNTHETIC-LOGIN-A", "password": "SYNTHETIC-TRANSIENT-SECRET"})
                    context = app.auth.authenticate(token)
                    initial = app.personal_chat.status(context)
                    receipt["checks"]["cold_start_has_no_observations"] = not initial["local"]["observations"]["snapshots"]
                    receipt["checks"]["cold_start_no_source_requests"] = not any(school.calls for school in schools)
                    receipt["checks"]["new_personal_deepseek_defaults_thinking_on"] = initial["thinking"] is True
                    if not all(receipt["checks"].values()):
                        raise AssertionError("cold_start_precondition_failed")
                    previous = None
                    for index, question in enumerate(QUESTIONS, 1):
                        body = {"message": question, "request_id": str(uuid.uuid4()),
                            "expected_turn": previous["turn_id"] if previous else 0,
                            "consent_personal": True, "data_use_version": "personal-retrieval-v1"}
                        if previous:
                            body["conversation_id"] = previous["conversation_id"]
                        result, progress = run_turn(app, context, body)
                        row = {"turn": index, "question": question, **result_view(result), "progress": progress}
                        receipt["results"].append(row)
                        print(json.dumps({"turn": index, "status": result["status"], "code": result.get("code"),
                            "tools": row["tools"], "citation_count": row["citation_count"],
                            "live_reasoning_seen": progress["observed_reasoning_while_running"],
                            "live_tool_seen": progress["observed_tool_event_while_running"]}, ensure_ascii=False), flush=True)
                        receipt["checks"][f"turn_{index}_completed"] = result["status"] == "completed"
                        receipt["checks"][f"turn_{index}_progress_reader_succeeded"] = not progress["progress_error_codes"]
                        if index == 1:
                            receipt["checks"]["grade_answer_has_summary_evidence"] = "summary" in row["citation_kinds"]
                            receipt["checks"]["grade_model_queried_current_records"] = "query_my_records" in row["tools"]
                            receipt["checks"]["real_reasoning_visible_while_running"] = progress["observed_reasoning_while_running"]
                            receipt["checks"]["real_tool_events_visible_while_running"] = progress["observed_tool_event_while_running"]
                        else:
                            receipt["checks"]["identity_uses_safe_general_chat"] = row["decision_kind"] == "general_chat" and not row["citation_count"]
                        previous = result
                        if result["status"] != "completed":
                            break
                    school_calls = [call for school in schools for call in school.calls]
                    receipt["synthetic_school_requests"] = school_calls
                    receipt["checks"]["grade_cold_start_read_major_all_history"] = bool(school_calls) and all(
                        call.get("operation") == "major_academic" and call.get("target_term") == "ALL-HISTORY" for call in school_calls)
                    receipt["checks"]["both_questions_ran_once_without_validation_retry"] = len(receipt["results"]) == 2
                    receipt["checks"]["all_model_calls_stream_selected_thinking_model"] = bool(calls) and all(
                        call["streaming"] and call["thinking"] and call["model"] == receipt["model"] for call in calls)
                    receipt["checks"]["summary_was_actually_projected_to_provider"] = any(call["projected_summary_counts"] for call in calls)
                    receipt["status"] = "passed" if all(receipt["checks"].values()) else "failed"
                finally:
                    app.close()
    except Exception as error:
        receipt["error_code"] = safe_code(error)
    receipt["provider_requests"] = opener.calls
    receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    output = args.output.expanduser().resolve()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "checks": receipt["checks"], "error_code": receipt.get("error_code"),
        "elapsed_ms": receipt["elapsed_ms"], "receipt": str(output)}, ensure_ascii=False), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
