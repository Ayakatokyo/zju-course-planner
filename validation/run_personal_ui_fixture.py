"""D1 UI on a disposable synthetic school transport and a valid test PDF."""
from __future__ import annotations

import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.server import Application, make_handler
from course_assistant.personal_chat import DATA_USE_VERSION
from validation.personal_fixture_data import synthetic_pdf
from validation.run_school_ui_fixture import NoOutboundModel, SyntheticSchool


class PersonalSyntheticSchool(SyntheticSchool):
    def request(self, spec):
        result = super().request(spec)
        if spec["operation"] == "plan_document" and result.get("state") == "business":
            result["payload"] = synthetic_pdf()
        return result


class ScriptedPersonalModel:
    """Explicit protocol fixture, never presented as a real model test."""
    stream_delay = 0.0
    def status(self):
        return {"configured": True, "remote": True, "provider": "Synthetic", "model": getattr(self, "model", "synthetic-only"),
                "thinking": getattr(self, "thinking", False)}

    def list_models(self):
        return {"models": [{"id": "synthetic-only", "name": "合成协议模型（非真实模型）"},
                           {"id": "synthetic-fast", "name": "合成切换模型（非真实模型）"}]}

    def with_options(self, model, thinking=False):
        other = ScriptedPersonalModel()
        other.model, other.thinking = model, thinking
        other.stream_delay = self.stream_delay
        return other

    def _complete_observed(self, messages, tools, observer=None):
        started = time.monotonic()
        message = self.complete(messages, tools)
        metadata = {"status": "completed", "model": self.status()["model"],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}
        if self.status()["thinking"]:
            chunks = ["合成协议推理：先读取服务端提供的当前本人范围。",
                      "随后只查询该范围内可用的成绩或培养资料。",
                      "这是带延迟的合成界面测试文本，并非实际模型推理。"]
            reasoning = "".join(chunks) + "\n".join(
                f"合成流式校验段 {index}：这是私有界面夹具生成的测试片段，只用于检查实时处理、来源展示和滚动位置，不是实际供应商的推理，也不代表本人学业结论。"
                for index in range(1, 9))
            message["reasoning_content"] = reasoning
            metadata["provider_reasoning"] = reasoning
        metadata["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if observer:
            observer(metadata)
        return message, metadata

    def complete_with_metadata(self, messages, tools, observer=None):
        return self._complete_observed(messages, tools, observer=observer)

    def complete_stream_with_metadata(self, messages, tools, *, observer=None, on_reasoning=None):
        """Emit actual fixture deltas slowly enough to observe HTTP progress.

        No transport is used. The deltas and final reasoning field are the
        same explicitly synthetic bytes, rather than fabricated UI stages.
        """
        started, observed = time.monotonic(), []
        # Preserve subclass overrides and their custom metadata; the fixture's
        # streaming adapter must not bypass existing counting/blocking doubles.
        message, metadata = self.complete_with_metadata(messages, tools, observer=observed.append)
        reasoning = message.get("reasoning_content") or metadata.get("provider_reasoning")
        if self.status()["thinking"] and isinstance(reasoning, str) and on_reasoning is not None:
            width = max(1, (len(reasoning) + 3) // 4)
            for offset in range(0, len(reasoning), width):
                if self.stream_delay:
                    time.sleep(self.stream_delay)
                on_reasoning(reasoning[offset:offset + width])
        if type(self).complete_with_metadata is ScriptedPersonalModel.complete_with_metadata:
            metadata["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if observer:
            for value in observed or [metadata]:
                observer(value)
        return message, metadata

    def complete(self, messages, tools):
        question = next(item["content"] for item in reversed(messages) if item["role"] == "user")
        current = []
        for item in reversed(messages):
            if item["role"] == "user":
                break
            if item["role"] == "tool":
                current.append(json.loads(item["content"]))
        def call(name, arguments):
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "synthetic_call_" + str(len(current)),
                "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
        normalized = "".join(question.split()).strip("?!？！。")
        if normalized in {"你是谁", "你是什么", "你是做什么的"}:
            return {"role": "assistant", "content": json.dumps({"kind": "general_chat", "intent": "identity"})}
        if normalized in {"你好", "hello", "hi"}:
            return {"role": "assistant", "content": json.dumps({"kind": "general_chat", "intent": "greeting"})}
        marker = "服务器已完成本轮本机范围检查（service_preflight get_my_context）：\n"
        context = {}
        for item in messages:
            if item.get("role") == "system" and marker in item.get("content", ""):
                context = json.loads(item["content"].split(marker, 1)[1])
                break
        if not current:
            if "SYNTHETIC-001" in question or "培养" in question:
                return call("search_my_curriculum", {"query": "SYNTHETIC-001", "limit": 5})
            available = [item for item in context.get("operations", []) if isinstance(item, dict)]
            scope = next((item for operation in ("major_academic", "academic") for item in available
                          if item.get("operation") == operation), None)
            if scope is None:
                return {"role": "assistant", "content": json.dumps({"kind": "not_ready", "reason": "insufficient_evidence"})}
            return call("query_my_records", {"operation": scope["operation"], "target_term": scope["target_term"],
                                              "query": "SYNTHETIC-PE-REPEAT" if "SYNTHETIC-PE-REPEAT" in question else "", "limit": 15})
        result = current[0]
        rows = result.get("matches") or result.get("records") or []
        summary_id = result.get("source_grade_summary", {}).get("evidence_id")
        ids = ([summary_id] if summary_id else []) + [row["evidence_id"] for row in rows if row.get("evidence_id")]
        decision = ({"kind": "answer", "claims": [{"text": "合成协议验证：以下片段仅来自当前已读范围，内容及适用仍待核验。",
                                                     "evidence_ids": ids[:3]}]}
                    if ids else {"kind": "not_ready", "reason": "insufficient_evidence"})
        return {"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)}


def install_synthetic_seed(app, turns):
    """Seed only a successful fixture login through the normal scoped services.

    This hook exists solely in this disposable validation executable. It adds
    no HTTP route and never runs in the normal application entry point.
    """
    if type(turns) is not int or not 0 <= turns <= 20:
        raise ValueError("synthetic seed count must be 0..20")
    if not isinstance(app.personal_chat.model, ScriptedPersonalModel):
        raise ValueError("synthetic seeding requires the explicit scripted model")
    original_login = app.school.login

    def seeded_login(body, previous_token=None):
        token, result = original_login(body, previous_token)
        if not turns or not token or result.get("authenticated") is not True:
            return token, result
        context = app.auth.authenticate(token)
        if context.student_id not in {"SYNTHETIC-SCHOOL-A", "SYNTHETIC-SCHOOL-B"}:
            raise ValueError("synthetic fixture owner required")
        app.school.fetch(context, {"operation": "academic", "target_term": "2025-2026-AW",
                                   "force_refresh": True})
        app.school.acquire_plan(context)
        directory = app.personal_chat.models(context, refresh=True)
        if not any(item.get("id") == "synthetic-only" for item in directory.get("models", [])):
            raise ValueError("synthetic model missing from fixture directory")
        app.personal_chat.configure({"model": "synthetic-only", "thinking": True}, context)
        previous = None
        for index in range(turns):
            topic = ("请查询培养资料中的 SYNTHETIC-001 及必要前提。" if index % 2 else
                     "请核对已读成绩中 SYNTHETIC-PE-REPEAT 的不同修读记录。")
            payload = {"message": f"第 {index + 1} 轮纯合成界面测试：{topic}",
                       "request_id": str(uuid.uuid4()), "expected_turn": index,
                       "consent_personal": True, "data_use_version": DATA_USE_VERSION}
            if previous:
                payload["conversation_id"] = previous["conversation_id"]
            previous = app.personal_chat.turn(payload, context)
            if previous.get("status") != "completed" or previous.get("turn_id") != index + 1:
                raise ValueError("synthetic seed conversation did not complete")
        print(f"SYNTHETIC ONLY: seeded {turns} turns for {context.student_id}; open its history.", flush=True)
        return token, result

    app.school.login = seeded_login


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18876)
    parser.add_argument("--scripted-model", action="store_true", help="explicit synthetic protocol fixture; no real model")
    parser.add_argument("--streaming", action="store_true",
                        help="explicit delayed synthetic reasoning deltas; requires --scripted-model")
    parser.add_argument("--seed-turns", type=int, default=None,
                        help="seed 0..20 turns after successful synthetic login; requires --scripted-model")
    args = parser.parse_args()
    if args.streaming and not args.scripted_model:
        parser.error("--streaming requires --scripted-model")
    if args.seed_turns is not None and (not args.scripted_model or not 0 <= args.seed_turns <= 20):
        parser.error("--seed-turns requires --scripted-model and a count from 0 to 20")
    with tempfile.TemporaryDirectory(prefix="synthetic-personal-d1-") as directory:
        root = Path(directory).resolve()
        model = ScriptedPersonalModel() if args.scripted_model else NoOutboundModel()
        if args.streaming:
            model.stream_delay = 0.3
        app = Application(root / "app.sqlite3", model=model,
                          school_client_factory=PersonalSyntheticSchool,
                          school_root=root / "private" / "school_accounts")
        if args.seed_turns is not None:
            install_synthetic_seed(app, args.seed_turns)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
        print(f"SYNTHETIC D1 ONLY: http://localhost:{args.port}/", flush=True)
        print("Use SYNTHETIC-LOGIN-A or SYNTHETIC-LOGIN-B and a synthetic password.", flush=True)
        print("No school or model network. PDF extraction uses local pdftotext.", flush=True)
        if args.streaming:
            print("Synthetic reasoning deltas: 0.3 seconds per fragment; no real provider stream.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.close()


if __name__ == "__main__":
    main()
