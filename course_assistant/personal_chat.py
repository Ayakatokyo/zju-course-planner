"""Owner/realm and authorization-generation coordination for personal chat.

The dialogue loop receives only the server's local tool callback. This layer
owns consent, persistence, replay and input/model-version checks, not retrieval
or school transport. Synthetic chat and its validators remain separate.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import threading
import time
import uuid

from .auth import AccessError, SCHOOL_MODE, SCHOOL_REALM
from .importer import digest
from .model_client import ModelError
from .storage import chat_title
from .chat_model_settings import ChatModelSettings
from .chat_runs import ChatRunStore, ChatRunError, tool_event_fields
from .chat_progress import ChatProgress
from .personal_tasks import (previous_task_context, previous_clarification_context,
                             next_clarification_context)


DATA_USE_VERSION = "personal-retrieval-v1"
SERVICE = "personal-retrieval-chat-v1"
TOOLS_VERSION = "personal-question-tools-v8-typed-readiness"
DATA_USE_SUMMARY = "本轮问题及检索到的相关教务记录、培养资料片段会发送给当前模型；不发送账号凭据。"
SENSITIVE = re.compile(r"(?i)(sk[-_][a-z0-9_-]{12,}|bearer\s+\S+|(?:password|passwd|cookie|token|api[_ -]?key|密码|口令)\s*(?:[:=：]|是|为|\bis\b)\s*\S+|(?:密码|口令)\s+\S+|(?<!\d)\d{10}(?!\d))")


def _deny(code, message, status=409):
    raise AccessError(message, status, code)


def _json_check(value, limit=800000):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _deny("invalid_personal_chat_state", "本轮对话结果格式无效")
    if len(raw) > limit:
        _deny("personal_chat_context_limit", "本轮内容超出上限，请缩小问题或开始新对话")


class _ObservedModel:
    """One immutable selected client and safe per-request diagnostics."""
    def __init__(self, client, emit, authorize, reasoning=None):
        self.client, self.emit, self.authorize, self.reasoning = client, emit, authorize, reasoning
        self.step = 0

    def status(self):
        return self.client.status()

    def complete(self, messages, tools):
        self.authorize()
        status, started = self.status(), time.monotonic()
        self.step += 1
        self.emit("model.start", {"model": status.get("model"), "step": self.step,
                                   "input_chars": len(json.dumps(messages, ensure_ascii=False))})
        def observed(metadata):
            values = {key: metadata[key] for key in ("model", "status", "elapsed_ms", "usage", "http_status", "request_id", "completion_id")
                      if metadata.get(key) is not None}
            values["step"] = self.step
            if metadata.get("code"):
                values["error_code"] = metadata["code"]
            if type(metadata.get("provider_reasoning")) is str:
                values["reasoning_content"] = metadata["provider_reasoning"]
            self.emit("model.end", values)
        stream = getattr(self.client, "complete_stream_with_metadata", None)
        method = getattr(self.client, "complete_with_metadata", None)
        if callable(stream):
            message, _ = stream(messages, tools, observer=observed, on_reasoning=self.reasoning)
        elif callable(method):
            message, _ = method(messages, tools, observer=observed)
        else:
            try:
                message = self.client.complete(messages, tools)
                observed({"status": "completed", "elapsed_ms": round((time.monotonic()-started)*1000),
                          "provider_reasoning": message.get("reasoning_content")})
            except Exception as error:
                observed({"status": "failed", "elapsed_ms": round((time.monotonic()-started)*1000),
                          "code": getattr(error, "code", "model_call_failed")})
                raise
        self.authorize()
        return message


class PersonalChatService:
    def __init__(self, app, model):
        self.app, self.model = app, model
        self.settings = ChatModelSettings(app.auth, lambda: self.model)
        self._active_model = threading.local()
        self.progress = ChatProgress()
        try:
            self.runs = ChatRunStore(app.store, authorize=self._run_authorize)
        except ChatRunError:
            self.runs = None

    def _run_authorize(self, context):
        return self._identity_scope(context)

    def _identity_scope(self, context):
        self.app.auth.require_context(context)
        method = getattr(self.app.school, "personal_chat_identity_scope", None)
        if not callable(method):
            return self._current(context)[0]
        receipt = method(context)
        return digest({"scope_key": receipt["scope_key"], "owner": context.student_id,
                       "realm": context.security_realm, "binding": context.binding_version})

    @staticmethod
    def _progress_key(context, scope, request_id):
        return (context.security_realm, context.student_id, context.session_ref,
                context.binding_version, scope, request_id)

    def read_progress(self, body, context):
        if (type(body) is not dict or set(body) - {"request_id", "after_sequence"}
                or "request_id" not in body or type(body.get("after_sequence", 0)) is not int
                or not 0 <= body.get("after_sequence", 0) <= 1000000000):
            _deny("invalid_progress_request", "进度请求字段无效", 400)
        request_id = self._uuid(body["request_id"])
        scope = self._identity_scope(context)
        value = self.progress.read(self._progress_key(context, scope, request_id))
        base = {"request_id": request_id, "events": [], "active_reasoning": None,
                "reasoning_notice": "DeepSeek实际返回的可见推理；不是完整内部思维或答案正确性证明。"}
        if value is None:
            return {**base, "status": "not_started", "next_sequence": 0, "terminal": False,
                    "available": self.runs is not None}
        events = []
        if value.get("run_id") and self.runs is not None:
            try:
                detail = self.runs.read(context, value["run_id"], scope, include_reasoning=True)
                events = [{**event["fields"], "sequence": event["sequence"], "kind": event["kind"],
                           "at": event["created_at"], "code": event["fields"].get("error_code")}
                          for event in detail["events"]]
            except ChatRunError:
                pass  # Diagnostics must never trigger a second model request.
        if self._identity_scope(context) != scope:
            _deny("personal_scope_changed", "本人授权代次已变化", 409)
        return {**base, **value, "events": events, "available": self.runs is not None}

    @staticmethod
    def _uuid(value):
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            _deny("invalid_chat_request", "请求或对话编号必须为标准 UUID", 400)
        return value

    def _request(self, body):
        required = {"message", "request_id", "expected_turn", "consent_personal", "data_use_version"}
        if type(body) is not dict or not required <= set(body) or set(body) - (required | {"conversation_id"}):
            _deny("invalid_chat_request", "个人对话请求字段无效", 400)
        message = body["message"]
        if type(message) is not str or not 0 < len(message.strip()) <= 2000 or len(message) > 2000 or "\x00" in message:
            _deny("invalid_chat_request", "消息需为 1 至 2000 字符", 400)
        if SENSITIVE.search(message):
            _deny("sensitive_chat_input", "请移除密码、凭据或真实学号后重试", 400)
        self._uuid(body["request_id"])
        if body.get("conversation_id") is not None:
            self._uuid(body["conversation_id"])
        if type(body["expected_turn"]) is not int or body["expected_turn"] < 0:
            _deny("invalid_chat_request", "对话版本需为非负整数", 400)
        if body["consent_personal"] is not True or body["data_use_version"] != DATA_USE_VERSION:
            _deny("personal_data_consent_required", "请先确认本轮问题及限定检索内容的模型使用范围", 400)

    def _current(self, context, *, expected_scope=None, expected_versions=None, expected_egress=None, model_snapshot=None):
        self.app.auth.require_context(context)
        if context.auth_mode != SCHOOL_MODE or context.security_realm != SCHOOL_REALM:
            _deny("school_connection_required", "请先连接学校本人账号", 403)
        receipt = self.app.school.personal_chat_scope(context)
        if (type(receipt) is not dict or type(receipt.get("scope_key")) is not str or
                not 0 < len(receipt["scope_key"]) <= 512 or type(receipt.get("versions")) is not dict):
            _deny("personal_scope_unavailable", "本人对话授权范围暂不可用")
        _json_check(receipt["versions"], 200000)
        scope = digest({"scope_key": receipt["scope_key"], "owner": context.student_id,
                        "realm": context.security_realm, "binding": context.binding_version})
        _, model_status, _ = model_snapshot or self.settings.snapshot(context)
        if type(model_status) is not dict:
            _deny("model_not_configured", "模型尚未配置", 503)
        egress = {key: model_status.get(key) for key in ("configured", "provider", "model", "remote", "thinking", "settings_revision")}
        egress.update(data_use_version=DATA_USE_VERSION, tools_version=TOOLS_VERSION)
        _json_check(egress, 10000)
        versions = {"personal_scope": scope, "input_digest": digest(receipt["versions"]),
                    "egress_digest": digest(egress), "data_use_version": DATA_USE_VERSION,
                    "tools_version": TOOLS_VERSION}
        if expected_scope is not None and scope != expected_scope:
            _deny("personal_scope_changed", "本人授权代次已变化，请开始新对话")
        if expected_versions is not None and versions["input_digest"] != expected_versions["input_digest"]:
            _deny("personal_input_changed", "本人资料版本已变化，请重新发送以检索当前依据")
        if expected_egress is not None and versions["egress_digest"] != expected_egress:
            _deny("personal_model_changed", "模型或数据使用范围已变化，请重新确认后发送")
        return scope, versions, model_status

    @staticmethod
    def _owned_state(row, scope):
        if row is None or row["state"].get("personal_service") != SERVICE or row["state"].get("personal_scope") != scope:
            _deny("inaccessible_object", "资源不可访问", 404)
        return row["state"]

    @staticmethod
    def _intent_history(state):
        """Carry user intent across fresh data versions, never old fact/tool payloads."""
        rows = [{"role": "user", "content": turn["message"]} for turn in state.get("turns", [])[-8:]
                if isinstance(turn, dict) and isinstance(turn.get("message"), str) and 0 < len(turn["message"]) <= 2000]
        while rows and len(json.dumps(rows, ensure_ascii=False).encode()) > 12000:
            rows.pop(0)
        return rows

    def status(self, context):
        self.app.auth.require_context(context)
        if context.auth_mode != SCHOOL_MODE or context.security_realm != SCHOOL_REALM:
            _deny("school_connection_required", "请先连接学校本人账号", 403)
        model_status = self.settings.client(context).status()
        local = self.app.school.local_chat_status(context)
        scope, versions = None, {}
        ready = local.get("local", {}).get("available") is True
        if ready:
            try:
                scope, versions, model_status = self._current(context)
                self._current(context, expected_scope=scope, expected_versions=versions,
                              expected_egress=versions["egress_digest"])
            except AccessError as error:
                if error.reason not in {"source_session_expired", "source_subject_changed", "permission_denied",
                                         "awaiting_user_login", "captcha_required"}:
                    raise
                ready = False
                local["local"] = {"available": False, "reason": error.reason,
                    "observations": {"snapshots": []}, "evidence": {"ready": False}}
        self.app.auth.require_context(context)
        capabilities = {**local.get("capabilities", {}), "model_dialogue": ready and model_status.get("configured") is True,
                        "real_recommendation": False}
        return {**local, **model_status, "configured": model_status.get("configured") is True,
                "available": ready and model_status.get("configured") is True,
                "data_kind": "personal_source", "preview_only": True, "versions": versions,
                "personal_scope": scope, "data_use_version": DATA_USE_VERSION, "max_turns": 20,
                "personal_data_use": {"version": DATA_USE_VERSION, "summary": DATA_USE_SUMMARY},
                "data_scope": "本轮问题及必要本人检索内容；按问题只读获取教务来源，不执行选课",
                "capabilities": capabilities,
                "diagnostics_available": self.runs is not None,
                "reason": model_status.get("reason", "") if not model_status.get("configured") else ""}

    def models(self, context, *, refresh=False):
        return self.settings.refresh(context) if refresh else self.settings.catalog(context)

    def configure(self, body, context):
        self.settings.update(context, body)
        return self.status(context)

    @staticmethod
    def _run_view(value):
        result = deepcopy(value)
        result["started_at"] = result["created_at"]
        endpoint = time.time() if result["status"] == "running" else result["updated_at"]
        result["duration_ms"] = max(0, round((endpoint - result["created_at"]) * 1000))
        if result["status"] != "running":
            result["finished_at"] = result["updated_at"]
        return result

    def recent_runs(self, context, limit=20):
        scope, _, _ = self._current(context)
        if self.runs is None:
            return {"runs": [], "has_more": False, "available": False}
        try:
            result = self.runs.list(context, scope, limit=limit)
        except ChatRunError:
            raise AccessError("运行记录暂不可用", 503, "chat_run_store_unavailable") from None
        return {**result, "runs": [self._run_view(item) for item in result["runs"]], "available": True}

    def read_run(self, body, context):
        if type(body) is not dict or set(body) != {"run_id"}:
            _deny("invalid_run_request", "仅接受运行编号", 400)
        scope, _, _ = self._current(context)
        if self.runs is None:
            _deny("chat_run_store_unavailable", "运行记录暂不可用", 503)
        try:
            result = self.runs.read(context, body["run_id"], scope, include_reasoning=True)
        except ChatRunError as error:
            _deny(error.code, "该运行记录不可访问", 404)
        return {**result, "run": self._run_view(result["run"]),
                "events": [{**event["fields"], "sequence": event["sequence"], "kind": event["kind"],
                            "code": event["fields"].get("error_code"), "at": event["created_at"]}
                           for event in result["events"]]}

    def conversations(self, context, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            _deny("invalid_chat_limit", "历史列表数量须为 1 至 100", 400)
        scope, versions, _ = self._current(context)
        with self.app.lock:
            self.app.auth.require_context(context)
            with self.app.store.connect() as db:
                rows = db.execute("SELECT id,turn,pending_request,updated_at,json_extract(state,'$.title') "
                    "FROM owned_conversations WHERE realm=? AND owner=? "
                    "AND json_extract(state,'$.personal_scope')=? AND json_extract(state,'$.personal_service')=? "
                    "ORDER BY updated_at DESC,id DESC LIMIT ?",
                    (context.security_realm, context.student_id, scope, SERVICE, limit + 1)).fetchall()
            result = {"conversations": [{"conversation_id": row[0], "turn_id": row[1], "in_progress": row[2] is not None,
                    "updated_at": row[3], "title": chat_title(row[4])} for row in rows[:limit]],
                    "limit": limit, "has_more": len(rows) > limit, "data_kind": "personal_source",
                    "personal_scope": scope, "versions": versions}
        self._current(context, expected_scope=scope, expected_versions=versions,
                      expected_egress=versions["egress_digest"])
        return result

    def history(self, body, context):
        if type(body) is not dict or set(body) != {"conversation_id"}:
            _deny("invalid_chat_request", "历史查询只接受对话编号", 400)
        identifier = self._uuid(body["conversation_id"])
        scope, versions, _ = self._current(context)
        with self.app.lock:
            self.app.auth.require_context(context)
            row = self.app.store.chat_one(context, identifier)
            state = self._owned_state(row, scope)
            turns = deepcopy(state.get("turns", []))
            for turn in turns:
                turn["is_stale"] = turn.get("versions") != versions
            result = {"conversation_id": identifier, "turn_id": row["turn_id"], "turns": turns,
                    "in_progress": row["in_progress"], "data_kind": "personal_source", "preview_only": True,
                    "is_stale": state.get("workspace_versions") != versions, "personal_scope": scope,
                    "versions": versions, "title": chat_title(state.get("title")), "updated_at": row["updated_at"],
                    "action_pending": False, "option_receipts": {}, "option_receipts_has_more": False}
        self._current(context, expected_scope=scope, expected_versions=versions,
                      expected_egress=versions["egress_digest"])
        return result

    def _dialogue(self, authorize, execute_tool):
        from .personal_dialogue import PersonalDialogueService
        return PersonalDialogueService(getattr(self._active_model, "client", self.model), authorize, execute_tool)

    def _execution_context(self, context, state, scope, identifier):
        try:
            return self._read_execution_context(context, state, scope, identifier)
        except Exception:
            # Run logs are optional diagnostics. Authentication/input versions
            # are checked by the main turn before and after model execution.
            return None

    def _read_execution_context(self, context, state, scope, identifier):
        """Only this authenticated conversation's previous recorded run.

        Failed provider messages stay excluded from model history. This separate
        projection lets the next turn explain what actually failed without
        sending previous questions, source bodies or provider reasoning again.
        """
        if self.runs is None:
            return None
        previous = next((turn for turn in reversed(state.get("turns", [])) if turn.get("run_id")), None)
        if previous is None:
            return None
        try:
            detail = self.runs.read(context, previous["run_id"], scope, include_reasoning=False)
        except ChatRunError:
            return None
        run = detail["run"]
        if run.get("conversation_id") != identifier or run.get("status") not in {"completed", "failed", "cancelled", "aborted"}:
            return None
        allowed = {"step", "attempt", "count", "tool_count", "citation_count", "record_count", "match_count",
                   "claim_count", "evidence_count", "unknown_count", "input_chars", "output_chars", "elapsed_ms",
                   "error_code", "failure_detail", "stage", "tool_name", "status", "http_status", "operation",
                   "target_term", "query_mode", "initiated_by", "captured_rows", "reported_total", "matched_records",
                   "returned_records", "has_more", "complete_within_scope"}
        value = {"run_id": run["run_id"], "status": run["status"], "error_code": run.get("error_code"),
                "thinking": run["thinking"], "model": run.get("model"),
                "events": [{"kind": event["kind"], "fields": {key: value for key, value in event["fields"].items() if key in allowed}}
                           for event in detail["events"][-64:]]}
        from .personal_dialogue import _execution_context, PersonalDialogueError
        try:
            return _execution_context(value)
        except PersonalDialogueError:
            return None  # An unavailable diagnostic must not break the next task.

    @staticmethod
    def _failure(error):
        if isinstance(error, AccessError):
            code = error.reason
        else:
            code = getattr(error, "code", "personal_dialogue_failed")
        if type(code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", code):
            code = "personal_dialogue_failed"
        message = {"personal_input_changed": "本人资料版本已变化，本轮结果已失效，请重新发送。",
                   "personal_model_changed": "模型或数据使用范围已变化，请重新确认后发送。",
                   "source_unavailable": "学校来源暂不可用，本轮未取得所需资料，请稍后重试。",
                   "source_session_expired": "学校会话已失效，请重新登录后提问。",
                   "captcha_required": "学校需要验证码，请在我的教务完成验证后重试。"}.get(code,
                   "本轮对话未完成，请稍后重试。")
        if isinstance(error, ModelError):
            message = str(error)
        return {"status": "failed", "reply": message, "code": code, "tool_results": [], "citations": [],
                "data_kind": "personal_source", "preview_only": True, "reply_origin": "service_error",
                "partial_tools_completed": False, "model_decision": None}

    def turn(self, body, context):
        self._request(body)
        snapshot = self.settings.snapshot(context)
        scope, versions, model_status = self._current(context, model_snapshot=snapshot)
        if model_status.get("configured") is not True:
            _deny("model_not_configured", "模型尚未配置，没有发送本轮消息", 503)
        with self.app.lock:
            self.app.auth.require_context(context)
            if body.get("conversation_id") is not None:
                self._owned_state(self.app.store.chat_one(context, body["conversation_id"]), scope)
            reservation = self.app.store.chat_begin(context, body.get("conversation_id"), body["request_id"],
                digest({"body": body, "personal_scope": scope, "egress_digest": versions["egress_digest"]}),
                body["expected_turn"], first_message=body["message"])
            if "replay" in reservation:
                replay = reservation["replay"]
                if replay.get("personal_scope") != scope or replay.get("data_kind") != "personal_source":
                    _deny("inaccessible_object", "资源不可访问", 404)
                if replay.get("versions") != versions:
                    _deny("personal_input_changed", "旧请求资料或模型版本已变化，请创建新一轮")
            else:
                identifier, turn_id, state = reservation["conversation_id"], reservation["turn_id"], reservation["state"]
                reset = state.get("workspace_versions") != versions
                state.update(personal_service=SERVICE, personal_scope=scope)
                same_egress = (state.get("workspace_versions") or {}).get("egress_digest") == versions["egress_digest"]
                task_context = previous_task_context(state) if same_egress else None
                clarification_context = previous_clarification_context(state) if same_egress else None
                history = self._intent_history(state) if reset and same_egress else [] if reset else deepcopy(state.get("messages", []))
                if callable(getattr(self.app.school, "personal_chat_live_tool", None)):
                    history = self._intent_history(state) if same_egress else []
                    reset = reset or bool(state.get("messages"))

        if "replay" in reservation:
            self._current(context, expected_scope=scope, expected_versions=versions,
                          expected_egress=versions["egress_digest"])
            return {**replay, "replayed": True}

        def authorize():
            self._current(context, expected_scope=scope, expected_versions=versions,
                          expected_egress=versions["egress_digest"])
            # Input changes made by this turn are adopted under the connection
            # lock. Authority/model changes remain fatal; old input versions
            # still fail _current unless explicitly adopted by our own read.
            return {"scope_key": scope, "versions": {"egress_digest": versions["egress_digest"]}}

        run_id, log_errors = None, []
        if self.runs is not None:
            try:
                run_id = self.runs.begin(context, identifier, body["request_id"], model_status, scope)
            except ChatRunError:
                log_errors.append(True)

        def emit(kind, fields):
            if run_id is not None:
                try:
                    self.runs.event(context, run_id, kind, fields)
                except ChatRunError:
                    log_errors.append(True)
            self.progress.event(progress_key, kind)

        progress_key = self._progress_key(context, scope, body["request_id"])
        self.progress.begin(progress_key, run_id=run_id, model=model_status.get("model"),
                            thinking=model_status.get("thinking") is True)

        source_receipts = []

        def execute_tool(name, args):
            authorize()
            initiated_by = "service_preflight" if name == "get_my_context" else "model"
            emit("tool.start", tool_event_fields(name, args, initiated_by=initiated_by))
            started = time.monotonic()
            try:
                live_tool = getattr(self.app.school, "personal_chat_live_tool", None)
                result = (live_tool(context, name, args, receipts=source_receipts, emit=emit,
                                   authorize=generation_authorize, adopt=adopt, before=authorize)
                          if callable(live_tool) else self.app.school.personal_chat_tool(context, name, args))
            except Exception as error:
                emit("tool.end", {"tool_name": name, "status": "failed", "error_code": getattr(error, "code", getattr(error, "reason", "tool_unavailable")),
                                   "elapsed_ms": round((time.monotonic()-started)*1000)})
                raise
            authorize()
            emit("tool.end", {**tool_event_fields(name, args, result, initiated_by=initiated_by),
                              "status": "not_ready" if result.get("status") in {"not_ready", "unavailable"} else "completed",
                              "elapsed_ms": round((time.monotonic()-started)*1000)})
            return result

        response, messages, fatal = None, [], None
        try:
            def generation_authorize():
                self._current(context, expected_scope=scope, expected_egress=versions["egress_digest"])

            def adopt(changed):
                nonlocal versions, reset, history
                if not changed:
                    authorize()
                    return
                _, current, _ = self._current(context, expected_scope=scope,
                    expected_egress=versions["egress_digest"])
                if current != versions:
                    keep_intent = versions["egress_digest"] == current["egress_digest"]
                    versions, reset = current, True
                    history = self._intent_history(state) if keep_intent else []

            preparation = getattr(self.app.school, "prepare_personal_chat", None)
            if callable(preparation):
                prepared = preparation(context, body["message"], before=authorize, authorize=generation_authorize,
                                       emit=emit, adopt=adopt)
                if isinstance(prepared, list):
                    source_receipts.extend(prepared)
            # Neither inference nor PDF extraction holds the global app lock.
            self._active_model.client = _ObservedModel(snapshot[0], emit, authorize,
                lambda delta: self.progress.reasoning(progress_key, delta))
            execution_context = self._execution_context(context, state, scope, identifier)
            response, messages = self._dialogue(authorize, execute_tool).run(
                body["message"], data_use="personal_retrieval", history=history,
                execution_context=execution_context, task_context=task_context,
                clarification_context=clarification_context, emit=emit)
            if (type(response) is not dict or response.get("status") not in {"completed", "failed"} or
                    type(response.get("reply")) is not str or type(response.get("tool_results")) is not list or
                    type(response.get("citations")) is not list or type(messages) is not list):
                _deny("invalid_personal_chat_state", "本轮对话结果格式无效")
            _json_check({"response": response, "messages": messages})
            authorize()
        except Exception as error:
            fatal = error
            response, messages = self._failure(error), []
        finally:
            if hasattr(self._active_model, "client"):
                del self._active_model.client

        try:
            authorize()
        except Exception as error:
            fatal, response, messages = error, self._failure(error), []
        if response["status"] == "completed":
            emit("answer.validated", {"status": "completed", "tool_count": len(response["tool_results"]),
                                      "citation_count": len(response["citations"])})
        else:
            emit("error", {"error_code": response.get("code", "personal_dialogue_failed"), "stage": "answer"})
        if run_id is not None:
            try:
                self.runs.finish(context, run_id, response["status"], error_code=response.get("code") if response["status"] != "completed" else None)
            except ChatRunError:
                log_errors.append(True)
        with self.app.lock:
            try:
                self.app.auth.require_context(context)
            except AccessError as error:
                fatal, response, messages = error, self._failure(error), []
            # Failed authentication/input validation discards all current tool
            # data. A local sanitized turn still releases the reservation.
            if fatal is not None:
                response = self._failure(fatal)
            pending = next_clarification_context(body["message"], response,
                previous=clarification_context, task_context=task_context)
            decision = response.get("model_decision")
            decision = decision if isinstance(decision, dict) else {}
            response["task_status"] = ("failed" if response["status"] == "failed" else
                "awaiting_user" if pending else "needs_clarification" if decision.get("kind") == "clarify" else
                "blocked" if decision.get("kind") == "not_ready" else "completed")
            response.update(conversation_id=identifier, turn_id=turn_id, personal_scope=scope,
                            versions=deepcopy(versions), data_kind="personal_source", preview_only=True,
                            history_reset=reset or response.get("history_reset", False),
                            run_id=run_id, model=model_status.get("model"), thinking=model_status.get("thinking") is True,
                            diagnostics={"available": run_id is not None, "incomplete": bool(log_errors)})
            state.update(messages=deepcopy(messages) if response["status"] == "completed" else [],
                         workspace_versions=deepcopy(versions), personal_service=SERVICE, personal_scope=scope)
            for previous in state.get("turns", []):
                previous["is_stale"] = previous.get("versions") != versions
            state.setdefault("turns", []).append({"message": body["message"], **deepcopy(response),
                                                  "pending_clarification": pending})
            try:
                self.app.store.chat_finish(context, identifier, body["request_id"], turn_id, state, response)
            finally:
                self.progress.finish(progress_key, response["status"])
        if isinstance(fatal, AccessError):
            raise fatal
        self._current(context, expected_scope=scope, expected_versions=versions,
                      expected_egress=versions["egress_digest"])
        return response
