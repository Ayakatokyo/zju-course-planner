"""Responses/SSE adapter for the existing bounded academic Harness."""
from copy import deepcopy
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .chatgpt_auth import RESOURCE
from .model_client import ModelClient, ModelError, strict_json


class ModelRouter:
    def __init__(self, accounts, context, base):
        self.accounts, self.context, self.base = accounts, context, base

    def status(self):
        return self.base.status()

    def local_catalog(self):
        status = self.base.status()
        row = {"id": status["model"], "name": status["model"], "provider": status.get("provider")}
        for key in ("effort", "thinking_toggle"):
            if key in status:
                row[key] = status[key]
        return {"models": [row] + self.accounts.models(self.context), "source": "configured", "status": "ready"}

    def with_options(self, model, thinking=False, reasoning_effort=None):
        if model.startswith("chatgpt:"):
            rows = self.accounts.models(self.context)
            row = next((row for row in rows if row["id"] == model), None)
            # A disconnected selection remains visibly unavailable, never silently routes to DeepSeek.
            if row is None:
                row = {"id": model, "effort": {"supported_levels": [], "default_level": "default"}}
            return ChatGPTClient(self.accounts, self.context, row, thinking=thinking, reasoning_effort=reasoning_effort)
        method = getattr(self.base, "with_options", None)
        if not callable(method):
            return self.base
        options = {"model": model, "thinking": thinking}
        if reasoning_effort is not None:
            options["reasoning_effort"] = reasoning_effort
        return method(**options)

    def list_models(self):
        warnings, models = [], []
        try:
            method = getattr(self.base, "list_models", None)
            models = method()["models"] if callable(method) else self.local_catalog()["models"][:1]
        except ModelError:
            models = self.local_catalog()["models"][:1]
            warnings.append("DeepSeek 目录暂不可用，保留当前配置。")
        if self.accounts.status(self.context)["connected"]:
            try:
                models += self.accounts.models(self.context, refresh=True)
            except ModelError as error:
                warnings.append(str(error))
                models += self.accounts.models(self.context)
        return {"models": models, "warnings": warnings}


class ChatGPTClient(ModelClient):
    """Request-scoped; encrypted reasoning never leaves this adapter or enters logs."""
    def __init__(self, accounts, context, row, *, thinking=True, reasoning_effort=None):
        self.accounts, self.context = accounts, context
        self.model, self.row = row["id"], deepcopy(row)
        self.revision = accounts.status(context)["connection_revision"]
        self.reasoning_effort = reasoning_effort or row["effort"]["default_level"]
        levels = row["effort"]["supported_levels"]
        if type(thinking) is not bool or not isinstance(self.reasoning_effort, str):
            raise ModelError("思考选项无效", "model_options_invalid")
        if levels and self.reasoning_effort not in levels:
            raise ModelError("该 GPT 模型不支持所选思考强度", "model_effort_invalid")
        if not levels and self.reasoning_effort != "default" and accounts.status(context)["connected"]:
            raise ModelError("模型目录尚未提供该思考强度，请刷新目录", "model_effort_invalid")
        if not thinking and "none" not in levels and accounts.status(context)["connected"]:
            raise ModelError("该模型不支持关闭思考，请选择思考强度", "model_thinking_required")
        self.thinking = thinking and self.reasoning_effort != "none"
        self._outputs = {}
        self.timeout = 90

    def status(self):
        connection = self.accounts.status(self.context)
        configured = connection["connected"] and connection["connection_revision"] == self.revision
        return {"configured": configured, "provider": "ChatGPT", "model": self.model, "remote": True,
                "thinking": self.thinking, "reasoning_effort": self.reasoning_effort,
                "effort": self.row["effort"], "thinking_toggle": self.row.get("thinking_toggle", False),
                "connection_revision": connection["connection_revision"],
                "reason": "" if configured else "请连接 ChatGPT 并授权使用订阅额度"}

    def _input(self, messages):
        result = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                result.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
            elif role == "assistant" and message.get("tool_calls"):
                key = tuple(call["id"] for call in message["tool_calls"])
                if key not in self._outputs:
                    raise ModelError("GPT 工具上下文不完整", "model_protocol_error")
                result.extend(deepcopy(self._outputs[key]))
            elif role in {"system", "developer", "user", "assistant"} and message.get("content"):
                result.append({"role": "developer" if role == "system" else role, "content": message["content"]})
        return result

    def _output(self, response, allowed):
        output = response.get("output")
        if response.get("status") != "completed" or not isinstance(output, list) or len(output) > 100:
            raise ModelError("GPT 回复未完整完成", "model_protocol_error")
        texts, calls, seen = [], [], set()
        for item in output:
            if not isinstance(item, dict):
                raise ValueError()
            kind = item.get("type")
            if kind == "function_call":
                name, call_id, arguments = item.get("name"), item.get("call_id"), item.get("arguments")
                if (name not in allowed or item.get("namespace") != "academic"
                        or not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", call_id)
                        or call_id in seen or not isinstance(arguments, str) or len(arguments) > 4000):
                    raise ModelError("GPT 工具调用格式无效", "model_protocol_error")
                seen.add(call_id)
                calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}})
            elif kind == "message":
                if item.get("role") != "assistant" or item.get("status", "completed") != "completed":
                    raise ValueError()
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                        texts.append(content["text"])
                    else:
                        raise ModelError("GPT 未提供可核验的文本答复", "model_response_refused")
            elif kind != "reasoning":
                raise ModelError("GPT 返回了未授权的工具类型", "model_protocol_error")
        text = "".join(texts)
        if len(text) > 12000 or len(calls) > 3 or not (text.strip() or calls):
            raise ModelError("GPT 回复超出约定范围或为空", "model_protocol_error")
        if calls:
            self._outputs[tuple(call["id"] for call in calls)] = deepcopy(output)
        result = {"role": "assistant", "content": text or None}
        if calls:
            result["tool_calls"] = calls
        return result

    @staticmethod
    def _failure(event):
        error = event.get("error") or (event.get("response") or {}).get("error") or {}
        code = error.get("code") if isinstance(error, dict) else None
        if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}:
            return ModelError("ChatGPT 订阅共享额度暂不可用，请在 ChatGPT 中核对后重试", code)
        return ModelError("GPT 回复未完整完成，请重试", "model_response_incomplete")

    def complete_stream_with_metadata(self, messages, tools, *, observer=None, on_reasoning=None):
        metadata = {"status": "failed", "provider": "ChatGPT", "model": self.model, "thinking": self.thinking,
                    "reasoning_effort": self.reasoning_effort, "http_status": None, "usage": {}}
        started = time.monotonic()
        try:
            token = self.accounts.access(self.context, self.revision)
            functions = [{"type": "function", "name": tool["function"]["name"],
                          "description": tool["function"].get("description", ""),
                          "parameters": tool["function"]["parameters"], "strict": False} for tool in tools]
            allowed = {tool["name"] for tool in functions}
            payload = {"model": self.model.removeprefix("chatgpt:"), "input": self._input(messages),
                       "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}
            if functions:
                payload["tools"] = [{"type": "namespace", "name": "academic",
                                     "description": "本人教务查询与方案核验工具。由应用逐次验证并执行。", "tools": functions}]
                payload["parallel_tool_calls"] = False
            effort = self.reasoning_effort if self.thinking else "none"
            if effort != "default":
                payload["reasoning"] = {"effort": effort}
            raw = json.dumps(payload, ensure_ascii=False).encode()
            if len(raw) > 2 * 1024 * 1024:
                raise ModelError("GPT 请求上下文过大", "model_request_limit")
            request = Request(RESOURCE + "/responses", data=raw, headers={"Authorization": "Bearer " + token,
                              "Content-Type": "application/json", "Accept": "text/event-stream"})
            completed = None
            with self.accounts.opener.open(request, timeout=self.timeout) as response:
                metadata["http_status"] = response.status
                identifier = response.headers.get("x-request-id")
                if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,150}", identifier):
                    metadata["request_id"] = identifier
                for raw_event in self._sse_data(response, time.monotonic() + self.timeout):
                    if raw_event == "[DONE]":
                        break
                    event = strict_json(raw_event)
                    kind = event.get("type")
                    if kind in {"error", "response.failed", "response.incomplete"}:
                        raise self._failure(event)
                    if kind == "response.completed":
                        completed = event.get("response")
                        break
            if not isinstance(completed, dict):
                raise ModelError("GPT 流中断，未收到完成事件", "model_stream_incomplete")
            result = self._output(completed, allowed)
            # Revalidate connection and academic session after the network wait.
            self.accounts.owner(self.context)
            if self.accounts.status(self.context)["connection_revision"] != self.revision:
                raise ModelError("ChatGPT 连接已变化，请重新发送", "personal_model_changed")
            usage = completed.get("usage", {})
            if isinstance(usage, dict):
                metadata["usage"] = self._usage({"prompt_tokens": usage.get("input_tokens"),
                    "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens"),
                    "completion_tokens_details": usage.get("output_tokens_details", {}),
                    "prompt_tokens_details": usage.get("input_tokens_details", {})})
            metadata["status"] = "completed"
            return result, metadata
        except HTTPError as error:
            status = error.code
            error.close()
            metadata.update(http_status=status, code="model_http_" + str(status))
            raise ModelError("ChatGPT 请求失败，请检查连接或订阅额度", metadata["code"], http_status=status) from None
        except ModelError as error:
            metadata["code"] = error.code
            raise
        except (URLError, TimeoutError, OSError):
            metadata["code"] = "model_unavailable"
            raise ModelError("ChatGPT 服务超时或不可连接", "model_unavailable") from None
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            metadata["code"] = "model_protocol_error"
            raise ModelError("GPT 返回格式无效，本轮不采纳", "model_protocol_error") from None
        finally:
            metadata["elapsed_ms"] = max(0, round((time.monotonic() - started) * 1000))
            if observer:
                try:
                    observer(deepcopy(metadata))
                except Exception:
                    pass

    complete_with_metadata = complete_stream_with_metadata

    def complete(self, messages, tools, *, observer=None):
        return self.complete_stream_with_metadata(messages, tools, observer=observer)[0]
