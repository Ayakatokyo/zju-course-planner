"""Explicit real-DeepSeek check using only temporary, synthetic school data.

Requires --real-model-synthetic-only before reading configured model settings.
Only the DeepSeek model-directory/chat endpoints can be contacted. The receipt
contains synthetic answers and numeric diagnostics, never keys, headers, full
prompts, source records or complete provider reasoning. Temporary private run
events may contain bounded provider reasoning and are removed with the DB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from urllib.request import build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.model_client import ModelClient, NoRedirect, read_config
from course_assistant.server import Application
from validation.run_personal_ui_fixture import PersonalSyntheticSchool

RECEIPT = ROOT / "validation" / "chat_inspection_20261015_model.json"
QUESTIONS = [
    "请用本轮工具查询 SYNTHETIC-PE-REPEAT 的来源记录：有几条、各自成绩与名义学分原值是什么？保留重复，说明已读范围和未知状态。",
    "请再次用本轮工具核对同一门 SYNTHETIC-PE-REPEAT 的三条来源记录，比较名义学分原值；不能把来源成绩或名义学分当作已获学分，也不能判断毕业资格。请自然中文说明。",
]


class DeepSeekOnlyOpener:
    """Network guard independent of which application source/client is selected."""
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.calls = []

    def open(self, request, timeout):
        allowed = {("GET", ModelClient.MODELS_ENDPOINT), ("POST", ModelClient.ENDPOINT)}
        if (request.get_method(), request.full_url) not in allowed:
            raise AssertionError("validation_endpoint_not_allowed")
        self.calls.append({"operation": "models" if request.get_method() == "GET" else "chat", "timeout_s": timeout})
        return self.opener.open(request, timeout=timeout)


class CountingSyntheticSchool(PersonalSyntheticSchool):
    def __init__(self):
        super().__init__()
        self.request_count = 0

    def request(self, spec):
        self.request_count += 1
        return super().request(spec)


def safe_usage(value):
    return ModelClient._usage(value)


class AuditedSyntheticOnlyModel:
    def __init__(self, client, audit):
        self.client, self.audit = client, audit

    def status(self):
        return self.client.status()

    def with_options(self, model, thinking=False):
        return type(self)(self.client.with_options(model, thinking), self.audit)

    def list_models(self):
        started = time.monotonic()
        result = self.client.list_models()
        self.audit["directory_elapsed_ms"] = round((time.monotonic() - started) * 1000)
        self.audit["directory_ids"] = [item["id"] for item in result["models"]]
        return result

    def complete_with_metadata(self, messages, tools, *, observer=None):
        raw = json.dumps(messages, ensure_ascii=False)
        for forbidden in ("SYNTHETIC-SCHOOL-A", "SYNTHETIC-LOGIN-A", "SYNTHETIC-TRANSIENT-SECRET",
                          "SOURCE-", "OBS-", "PLAN-", "PERSONAL-REF-", "scope_key", "owner_student_id", "security_realm"):
            if forbidden in raw:
                raise AssertionError("validation_projection_not_synthetic_only")
        status = self.client.status()
        row = {"model": status["model"], "thinking": status["thinking"], "message_count": len(messages),
               "user_messages": sum(item.get("role") == "user" for item in messages),
               "prior_assistant_reasoning_messages": sum(item.get("role") == "assistant" and bool(item.get("reasoning_content")) for item in messages),
               "prior_assistant_reasoning_chars": sum(len(item.get("reasoning_content", "")) for item in messages if item.get("role") == "assistant")}
        self.audit["calls"].append(row)
        def observed(metadata):
            row.update(status=metadata["status"], elapsed_ms=metadata["elapsed_ms"], usage=safe_usage(metadata.get("usage")),
                       provider_reasoning_chars=len(metadata.get("provider_reasoning", "")))
            if metadata.get("code"):
                row["code"] = metadata["code"]
            if observer is not None:
                observer(metadata)
        message, metadata = self.client.complete_with_metadata(messages, tools, observer=observed)
        return message, metadata


def summarize_run(value):
    events = value["events"]
    model_ends = [row for row in events if row["kind"] == "model.end"]
    return {"status": value["run"]["status"], "model": value["run"]["model"], "thinking": value["run"]["thinking"],
            "duration_ms": value["run"]["duration_ms"], "event_count": len(events),
            "event_kinds": [row["kind"] for row in events],
            "provider_reasoning_chars": sum(len(row.get("reasoning_content", "")) for row in model_ends),
            "reasoning_is_execution_proof": value["reasoning_is_execution_proof"],
            "model_calls": [{"status": row.get("status"), "elapsed_ms": row.get("elapsed_ms"),
                             "usage": safe_usage(row.get("usage")), "reasoning_chars": len(row.get("reasoning_content", ""))}
                            for row in model_ends]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model-synthetic-only", action="store_true", required=True)
    parser.parse_args()
    started = time.monotonic()
    opener = DeepSeekOnlyOpener()
    base = ModelClient(read_config(ROOT / ".env"), opener=opener)
    if not base.status()["configured"]:
        parser.error("model_not_configured")
    configured = base.status()["model"]
    audit = {"calls": []}
    model = AuditedSyntheticOnlyModel(base, audit)
    receipt = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "status": "failed",
        "scope": "Real DeepSeek + exclusively synthetic temporary school data; not personal-source acceptance",
        "provider": "DeepSeek", "selected_model": configured, "thinking": True, "timeout_s": base.timeout,
        "real_school_network": False, "real_personal_data": False, "full_reasoning_saved": False,
        "directory_ids": [], "results": [], "checks": {}, "calls": audit["calls"]}
    schools = []
    def factory():
        client = CountingSyntheticSchool()
        schools.append(client)
        return client
    try:
        with tempfile.TemporaryDirectory(prefix="synthetic-chat-inspection-") as directory:
            app = Application(Path(directory).resolve() / "app.sqlite3", model=model, school_client_factory=factory)
            try:
                token, _ = app.school.login({"username": "SYNTHETIC-LOGIN-A", "password": "SYNTHETIC-TRANSIENT-SECRET"})
                context = app.auth.authenticate(token)
                app.school.fetch(context, {"operation": "academic", "target_term": "2026-2027-AW"})
                catalog = app.personal_chat.models(context, refresh=True)
                receipt["directory_ids"] = [item["id"] for item in catalog["models"]]
                receipt["directory_elapsed_ms"] = audit["directory_elapsed_ms"]
                receipt["checks"]["configured_model_in_actual_directory"] = configured in receipt["directory_ids"]
                if not receipt["checks"]["configured_model_in_actual_directory"]:
                    raise AssertionError("configured_model_not_in_actual_directory")
                app.personal_chat.configure({"model": configured, "thinking": True}, context)
                before_school = sum(client.request_count for client in schools)
                previous = None
                for index, question in enumerate(QUESTIONS, 1):
                    body = {"message": question, "request_id": str(uuid.uuid4()), "expected_turn": previous["turn_id"] if previous else 0,
                            "consent_personal": True, "data_use_version": "personal-retrieval-v1"}
                    if previous:
                        body["conversation_id"] = previous["conversation_id"]
                    result = app.personal_chat.turn(body, context)
                    row = {"turn": index, "question": question, "status": result["status"], "reply": result["reply"],
                           "code": result.get("code"), "model": result["model"], "thinking": result["thinking"],
                           "history_reset": result["history_reset"], "tools": [item["name"] for item in result["tool_results"]],
                           "citation_count": len(result["citations"]), "diagnostics": result["diagnostics"]}
                    if result.get("run_id"):
                        row["run"] = summarize_run(app.personal_chat.read_run({"run_id": result["run_id"]}, context))
                    receipt["results"].append(row)
                    print(json.dumps({key: row[key] for key in ("turn", "status", "code", "tools", "citation_count", "history_reset")}, ensure_ascii=False), flush=True)
                    receipt["checks"][f"turn_{index}_completed_with_current_tool_evidence"] = (
                        result["status"] == "completed" and result.get("model_used") is True and bool(result["tool_results"]) and bool(result["citations"]))
                    run = row.get("run", {})
                    receipt["checks"][f"turn_{index}_run_reasoning_usage_present"] = (
                        run.get("status") == "completed" and run.get("provider_reasoning_chars", 0) > 0
                        and bool(run.get("model_calls")) and all(call.get("usage", {}).get("total_tokens", 0) > 0 for call in run["model_calls"])
                        and "answer.validated" in run.get("event_kinds", []) and not result["diagnostics"]["incomplete"])
                    if previous:
                        receipt["checks"]["same_conversation_next_turn"] = result["conversation_id"] == previous["conversation_id"] and result["turn_id"] == previous["turn_id"] + 1
                        receipt["checks"]["second_turn_keeps_authorized_model_history"] = result["history_reset"] is False
                    previous = result
                    if result["status"] != "completed":
                        break
                second_calls = [call for call in audit["calls"] if call["user_messages"] >= 2]
                receipt["checks"]["second_turn_provider_received_prior_reasoning"] = bool(second_calls) and second_calls[0]["prior_assistant_reasoning_messages"] > 0
                receipt["checks"]["all_egress_uses_selected_thinking_model"] = bool(audit["calls"]) and all(call["model"] == configured and call["thinking"] is True for call in audit["calls"])
                receipt["checks"]["chat_did_not_fetch_school_sources"] = before_school == sum(client.request_count for client in schools)
                receipt["checks"]["two_turns_completed"] = len(receipt["results"]) == 2
                receipt["status"] = "passed" if all(receipt["checks"].values()) else "failed"
            finally:
                app.close()
    except Exception as error:
        code = getattr(error, "code", getattr(error, "reason", None))
        receipt["error_code"] = code if isinstance(code, str) and len(code) <= 100 else "synthetic_validation_failed"
        # Never print exception text: provider errors can include request material.
    receipt["provider_requests"] = opener.calls
    receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "checks": receipt["checks"], "error_code": receipt.get("error_code"),
                      "receipt": str(RECEIPT.relative_to(ROOT))}, ensure_ascii=False), flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
