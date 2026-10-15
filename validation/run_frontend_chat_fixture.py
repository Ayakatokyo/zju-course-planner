"""Run the real frontend with a local scripted synthetic dialogue fixture.

No .env, school client, remote model, private material or persistent runtime
store is used. This is an explicit validation entry point, never the normal
application entry point. Tools, recommendation, history, save and export use
the existing Application contracts and synthetic recommendation bundle.

Start: python3 validation/run_frontend_chat_fixture.py --port 18877
Check without starting a server: python3 validation/run_frontend_chat_fixture.py --check
Login in the UI as either mock student. Fixed messages:
  生成合成推荐
  比较A和B
  周五必须空闲
  周五可以有课
  查询合成学业
  查询合成目录
  模拟失败
Use the recommendation card buttons to save/export. New conversation and
history use the real frontend controls. --case unknown shows pending plans;
--case locked_friday followed by 周五必须空闲 shows an infeasible result.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.chat import ChatService
from course_assistant.model_client import ModelError
from course_assistant.server import Application, make_handler

FIXTURE_LABEL = "合成界面夹具，未调用模型"
RECOMMEND_MESSAGES = {
    "生成合成推荐",
    "优先补合成专业选修6学分，偏向AI，尽量周五空闲，保留体育",
    "优先补专业选修6学分，偏向AI，尽量周五空闲，保留体育",
}


def tool_call(name, arguments, identifier):
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": identifier, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def final_answer(outcome, *, clarification=None):
    decision = {"kind": "clarify" if clarification else "answer",
                "outcome": "clarify" if clarification else outcome,
                "cited_chunk_ids": []}
    if clarification:
        decision["clarification"] = clarification
    return {"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)}


class LocalScriptedDialogue:
    """A stateless test double; fixed text selects real, bounded local tools."""

    allow_reference_text = False

    def status(self):
        return {"configured": True, "remote": False, "provider": FIXTURE_LABEL,
                "model": "synthetic-ui-script-only", "allow_reference_text": False,
                "reason": FIXTURE_LABEL, "data_scope": "固定合成推荐夹具"}

    def complete(self, messages, tools):
        # Only the current turn is examined; no shared response queue can mix owners.
        start = max(i for i, row in enumerate(messages) if row["role"] == "user")
        message = messages[start]["content"].strip()
        current = messages[start + 1:]
        completed = [row for row in current if row["role"] == "tool"]
        if not completed:
            return tool_call("get_my_context", {}, "fixture_context")
        if len(completed) == 1:
            if message in RECOMMEND_MESSAGES:
                return tool_call("recommend_my_courses", {}, "fixture_recommend")
            if message in {"比较A和B", "比较A和B的课程与周五取舍"}:
                return tool_call("compare_my_options", {"option_ids": ["A", "B"]},
                                 "fixture_compare")
            if message in {"周五必须空闲", "周五可以有课"}:
                value = "hard" if message == "周五必须空闲" else "off"
                return tool_call("revise_my_recommendation", {"operations": [{
                    "op": "set", "field": "friday_free", "value": value,
                    "evidence": message}]}, "fixture_revise")
            if message == "查询合成学业":
                return tool_call("audit_my_progress", {}, "fixture_audit")
            if message == "查询合成目录":
                return final_answer("context")
            if message == "模拟失败":
                raise ModelError(FIXTURE_LABEL + "：预设的界面失败状态", "fixture_failure")
            return final_answer("clarify", clarification="choose_action")
        last = completed[-1]
        name = next(call["function"]["name"] for row in current
                    for call in row.get("tool_calls", []) if call["id"] == last["tool_call_id"])
        result = json.loads(last["content"])
        outcome = "audit" if name == "audit_my_progress" else result["status"]
        return final_answer(outcome)


class FixtureChatService(ChatService):
    """Label only this fixture's delivered projections, including reloaded history."""

    @staticmethod
    def labeled_turn(turn):
        result = deepcopy(turn)
        result["reply"] = FIXTURE_LABEL + "。\n" + result.get("reply", "")
        # The frontend abbreviates ordinary feasible replies. This fixture-only
        # origin retains the visible provenance label without altering decisions.
        if result.get("reply_origin") in {"verified_tool_presentation", "verified_clarification_prompt"}:
            result["reply_origin"] = "synthetic_ui_fixture"
        return result

    def turn(self, body, context):
        return self.labeled_turn(super().turn(body, context))

    def history(self, body, context):
        result = deepcopy(super().history(body, context))
        result["turns"] = [self.labeled_turn(turn) for turn in result["turns"]]
        return result


def make_application(database, case="default"):
    # Explicit model prevents the regular entry point/config path from running.
    model = LocalScriptedDialogue()
    app = Application(database, mock_enabled=True, model=model,
                      recommendation_demo=True, recommendation_case=case,
                      school_enabled=False)
    app.chat = FixtureChatService(app, model)
    return app


def run_checks():
    """Exercise real business contracts in temporary stores, without a server."""
    from unittest.mock import patch

    def forbidden(*args, **kwargs):
        raise AssertionError("fixture attempted configuration/network/model access")

    with patch("course_assistant.server.read_config", forbidden), \
            patch("course_assistant.model_client.read_config", forbidden), \
            patch("course_assistant.model_client.ModelClient.complete", forbidden), \
            patch("urllib.request.OpenerDirector.open", forbidden), \
            tempfile.TemporaryDirectory(prefix="frontend-chat-check-") as directory:
        app = make_application(Path(directory) / "default.sqlite3")
        try:
            assert app.school is None
            token, _ = app.auth.login_mock({"profile_id": "demo-a"})
            owner = app.auth.authenticate(token)
            token, _ = app.auth.login_mock({"profile_id": "demo-b"})
            other = app.auth.authenticate(token)

            def ask(message, previous=None):
                response = app.post("/api/chat/turn", {"message": message,
                    "request_id": str(uuid.uuid4()), "consent_remote": False,
                    "conversation_id": previous["conversation_id"] if previous else None,
                    "expected_turn": previous["turn_id"] if previous else 0}, owner)
                assert FIXTURE_LABEL in response["reply"]
                return response

            first = ask("生成合成推荐")
            assert first["status"] == "completed"
            assert len(first["tool_results"][-1]["result"]["plans"]) >= 2
            compared = ask("比较A和B", first)
            assert compared["model_decision"]["outcome"] == "compared"
            hard = ask("周五必须空闲", compared)
            result = hard["tool_results"][-1]["result"]
            assert hard["status"] == "completed" and len(result["plans"]) == 1
            option = result["plans"][0]
            action = {"conversation_id": hard["conversation_id"],
                      "expected_turn": hard["turn_id"], "run_id": option["run_id"],
                      "option_id": option["option_id"], "action_id": str(uuid.uuid4())}
            data, media, suffix = app.post("/api/chat/export-option", {**action, "format": "json"}, owner)
            assert media.split(";", 1)[0] == "application/json" and suffix == "json" and data
            saved = app.post("/api/chat/save-option", action, owner)
            assert saved["saved_id"] > 0 and len(app.get("/api/saved", owner)) == 1
            assert app.get("/api/saved", other) == []
            history = app.post("/api/chat/history", {"conversation_id": hard["conversation_id"]}, owner)
            assert history["turn_id"] == 3 and history["option_receipts"]
            assert all(FIXTURE_LABEL in row["reply"] for row in history["turns"])
            assert all(row["reply_origin"] == "synthetic_ui_fixture" for row in history["turns"])
            audit = ask("查询合成学业")
            assert audit["status"] == "completed"
            assert len(app.get("/api/chat/conversations", owner)["conversations"]) == 2
            assert app.get("/api/chat/conversations", other)["conversations"] == []
            failure = ask("模拟失败", audit)
            assert failure["status"] == "failed" and failure["partial_tools_completed"]
            status = app.get("/api/chat/status", owner)
            assert status["remote"] is False and status["provider"] == FIXTURE_LABEL
            assert ask("未登记的夹具请求")["model_decision"]["kind"] == "clarify"
        finally:
            app.close()
        for case, expected in (("unknown", "incomplete_data"), ("locked_friday", "scoped_infeasible")):
            app = make_application(Path(directory) / (case + ".sqlite3"), case)
            try:
                token, _ = app.auth.login_mock({"profile_id": "demo-a"})
                owner = app.auth.authenticate(token)
                first = ask("生成合成推荐")
                response = first if case == "unknown" else ask("周五必须空闲", first)
                result = response["tool_results"][-1]["result"]
                assert response["status"] == "completed" and result["status"] == expected
                if case == "unknown":
                    assert result["pending_plans"] and not result["plans"]
            finally:
                app.close()
    print("PASS: local scripted dialogue, recommendation, comparison, revision, save/export, history, owner isolation, unknown/failure; no server started.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18877)
    parser.add_argument("--case", choices=("default", "unknown", "locked_friday"), default="default")
    parser.add_argument("--check", action="store_true", help="check contracts without starting a server")
    args = parser.parse_args()
    if args.check:
        run_checks()
        return
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    with tempfile.TemporaryDirectory(prefix="synthetic-frontend-chat-") as directory:
        app = make_application(Path(directory) / "app.sqlite3", args.case)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
            print(f"SYNTHETIC ONLY: http://127.0.0.1:{args.port}/", flush=True)
            print(FIXTURE_LABEL + "；临时SQLite；学校和远程模型均关闭。", flush=True)
            print("登录模拟学生 A/B，发送：生成合成推荐；比较A和B；周五必须空闲。", flush=True)
            print("使用方案卡保存/导出；新对话、历史、已保存沿用真实前端。", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            app.close()


if __name__ == "__main__":
    main()
