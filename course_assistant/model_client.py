"""Bounded DeepSeek transport. Secrets remain server-side; never echo provider errors."""
from __future__ import annotations

from copy import deepcopy
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelError(ValueError):
    def __init__(self, message, code="model_error", *, http_status=None, request_id=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.request_id = request_id


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复 JSON 字段")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("JSON 数值必须有限")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read_config(path=None, environ=None):
    """Read only named settings; .env is data, never sourced or executed as a shell."""
    keys = {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "COURSE_ALLOW_REFERENCE_TEXT"}
    result = {}
    if path and Path(path).is_file():
        file = Path(path)
        if file.stat().st_size > 16384:
            raise ModelError("模型配置文件过大", "model_config_invalid")
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if separator and key in keys:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                result[key] = value
    environment = os.environ if environ is None else environ
    result.update({key: environment[key] for key in keys if key in environment})
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A service redirect must never forward Authorization to another host.
        raise ModelError("模型服务重定向被拒绝", "model_redirect_rejected")


class ModelClient:
    ENDPOINT = "https://api.deepseek.com/chat/completions"
    MODELS_ENDPOINT = "https://api.deepseek.com/models"
    STREAM_MAX_BYTES = 4 * 1024 * 1024
    STREAM_MAX_PROTOCOL_BYTES = 262144
    STREAM_MAX_CHUNKS = 32768
    STREAM_MAX_READS = 32768

    def __init__(self, config=None, *, opener=None, timeout=30):
        config = config or {}
        self._key = config.get("DEEPSEEK_API_KEY", "").strip()
        self.model = config.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
        self.allow_reference_text = config.get("COURSE_ALLOW_REFERENCE_TEXT", "false").lower() == "true"
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", self.model):
            raise ModelError("模型名称配置无效", "model_config_invalid")
        if self._key and (len(self._key) > 512 or any(c.isspace() for c in self._key)):
            raise ModelError("模型 Key 配置格式无效", "model_config_invalid")
        self._opener = opener or build_opener(NoRedirect())
        self.timeout = timeout
        self.thinking = False

    def with_options(self, model, thinking=False):
        """Create a request-scoped client; callers validate model against list_models.

        No configuration file is read, no discovery/chat request is sent, and the
        configured client is not mutated. The existing transport is reused.
        """
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", model) or type(thinking) is not bool:
            raise ModelError("模型或思考选项无效", "model_options_invalid")
        client = ModelClient({"DEEPSEEK_API_KEY": self._key, "DEEPSEEK_MODEL": model,
            "COURSE_ALLOW_REFERENCE_TEXT": "true" if self.allow_reference_text else "false"},
            opener=self._opener, timeout=self.timeout)
        client.thinking = thinking
        return client

    def status(self):
        return {"configured": bool(self._key), "provider": "DeepSeek", "model": self.model,
                "remote": True, "thinking": self.thinking, "allow_reference_text": self.allow_reference_text,
                "reason": "" if self._key else "未配置 DEEPSEEK_API_KEY；请在本机 .env 填写并重启服务",
                "data_scope": "固定 synthetic 夹具与经同意的测试对话；真实学校数据未接入"}

    def _identifier(self, value):
        return value if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,150}", value)
                         and (not self._key or self._key not in value)) else None

    def _request_id(self, response):
        headers = getattr(response, "headers", None)
        return self._identifier(headers.get("x-request-id")) if hasattr(headers, "get") else None

    def _request_json(self, endpoint, *, data=None):
        if not self._key:
            raise ModelError("模型未配置", "model_not_configured")
        request = Request(endpoint, data=data, method="POST" if data is not None else "GET", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        http_status, request_id = None, None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = getattr(response, "status", None)
                http_status = status if type(status) is int and 100 <= status <= 599 else None
                request_id = self._request_id(response)
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ModelError("模型响应超出上限", "model_response_limit")
            body = strict_json(raw)
            if not isinstance(body, dict):
                raise ValueError("invalid response")
            return body, {"http_status": http_status, "request_id": request_id}
        except HTTPError as error:
            # Never read/echo an error body, request headers or provider error text.
            code, request_id = error.code, self._request_id(error)
            error.close()
            messages_by_code = {401: "模型鉴权失败，请检查本机 Key", 402: "模型账户余额不足",
                                429: "模型服务限流，请稍后重试"}
            raise ModelError(messages_by_code.get(code, "模型服务请求失败"), f"model_http_{code}",
                             http_status=code, request_id=request_id) from None
        except ModelError as error:
            error.http_status, error.request_id = http_status, request_id
            raise
        except (URLError, TimeoutError, OSError):
            raise ModelError("模型服务超时或不可连接", "model_unavailable") from None
        except Exception:
            raise ModelError("模型响应格式无效，本轮不采纳", "model_protocol_error",
                             http_status=http_status, request_id=request_id) from None

    def list_models(self):
        """Discover the live service-side directory; never include chat material.

        Only whitelisted display/capability values are returned. This method has
        no fallback that could present a locally guessed model as available.
        """
        body, transport = self._request_json(self.MODELS_ENDPOINT)
        try:
            data = body.get("data")
            if body.get("object") != "list" or not isinstance(data, list) or len(data) > 100:
                raise ValueError("invalid directory")
            models, seen = [], set()
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError("invalid model")
                identifier = self._identifier(item.get("id"))
                if identifier is None or len(identifier) > 100 or identifier in seen:
                    raise ValueError("invalid model id")
                seen.add(identifier)
                row = {"id": identifier}
                for key in ("name", "owned_by"):
                    value = item.get(key)
                    if (isinstance(value, str) and 0 < len(value) <= 150 and not any(ord(c) < 32 for c in value)
                            and (not self._key or self._key not in value)):
                        row[key] = value
                for key in ("context_window", "max_output_tokens"):
                    value = item.get(key)
                    if type(value) is int and 0 < value <= 100000000:
                        row[key] = value
                for key, allowed in (("input_modalities", {"text", "image"}), ("output_modalities", {"text"})):
                    values = item.get(key)
                    if isinstance(values, list) and len(values) <= len(allowed) and all(isinstance(v, str) and v in allowed for v in values):
                        row[key] = list(dict.fromkeys(values))
                effort = item.get("effort")
                levels = effort.get("supported_levels") if isinstance(effort, dict) else None
                if (isinstance(levels, list) and 0 < len(levels) <= 10
                        and all(isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,20}", v)
                                and (not self._key or self._key not in v) for v in levels)):
                    row["effort"] = {"supported_levels": list(dict.fromkeys(levels))}
                    if effort.get("default_level") in levels:
                        row["effort"]["default_level"] = effort["default_level"]
                models.append(row)
            return {"models": models, "provider": "DeepSeek", "configured": True,
                    "selected_model": self.model, **transport}
        except (TypeError, ValueError, RecursionError):
            raise ModelError("模型目录格式无效", "model_protocol_error", **transport) from None

    @staticmethod
    def _usage(value):
        if not isinstance(value, dict):
            return {}
        valid = lambda v: type(v) is int and 0 <= v <= 1000000000
        result = {key: item for key, item in value.items() if key in {
            "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"} and valid(item)}
        for key, allowed in (("prompt_tokens_details", {"cached_tokens"}), ("completion_tokens_details", {"reasoning_tokens"})):
            details = value.get(key)
            if isinstance(details, dict):
                result[key] = {name: count for name, count in details.items() if name in allowed and valid(count)}
        return result

    def _complete(self, messages, tools, metadata):
        if not self._key:
            raise ModelError("模型未配置", "model_not_configured")
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "max_tokens": 8192 if self.thinking else 1800,
                   "thinking": {"type": "enabled" if self.thinking else "disabled"}}
        if self.thinking:
            payload["reasoning_effort"] = "low"
        else:
            payload["temperature"] = 0
        if tools:
            payload["tools"] = tools
        try:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ModelError("本轮模型请求格式无效", "model_request_invalid") from None
        if len(data) > 160_000:
            raise ModelError("本轮上下文超出上限，请开始新对话", "model_context_limit")
        body, transport = self._request_json(self.ENDPOINT, data=data)
        metadata.update(transport, usage=self._usage(body.get("usage")), completion_id=self._identifier(body.get("id")))
        try:
            choices = body.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("invalid choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError("invalid choice")
            if choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ModelError("模型输出未完整结束，本轮不采纳", "model_incomplete")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            content = message.get("content")
            calls = message.get("tool_calls") or []
            if message.get("role") != "assistant" or not (content is None or isinstance(content, str)):
                raise ValueError("invalid message")
            if not isinstance(calls, list) or len(calls) > 8:
                raise ValueError("invalid tool calls")
            # Only explicitly enabled provider reasoning is a message protocol field.
            # Usage/transport metadata never becomes part of the assistant message.
            result = {"role": "assistant", "content": content}
            if self.thinking:
                reasoning = message.get("reasoning_content")
                if reasoning is not None:
                    if not isinstance(reasoning, str) or self._key in reasoning:
                        raise ValueError("invalid provider reasoning")
                    result["reasoning_content"] = reasoning
            if calls:
                result["tool_calls"] = []
                seen = set()
                for call in calls:
                    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                        raise ValueError("invalid function")
                    function = call["function"]
                    identifier, name, arguments = call["id"], function["name"], function["arguments"]
                    if (call.get("type") != "function" or not isinstance(identifier, str)
                            or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", identifier)
                            or identifier in seen or not isinstance(name, str) or len(name) > 80
                            or not isinstance(arguments, str) or len(arguments) > 8000):
                        raise ValueError("invalid function")
                    seen.add(identifier)
                    result["tool_calls"].append({"id": identifier, "type": "function",
                                                 "function": {"name": name, "arguments": arguments}})
            elif not content or not content.strip():
                raise ValueError("empty answer")
            if "reasoning_content" in result:
                metadata["provider_reasoning"] = result["reasoning_content"]
            return result
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, ModelError):
                raise
            raise ModelError("模型响应格式无效，本轮不采纳", "model_protocol_error") from None

    def complete_with_metadata(self, messages, tools, *, observer=None):
        """Return (protocol message, safe request metadata), without persistence.

        provider_reasoning is the provider's public field, not a complete account
        of internal inference. It may quote authorized source data: callers must
        treat it as personal material and must not place it in general logs.
        An optional per-call observer receives a copy, never prompts/tool data or
        raw errors. Observer failures cannot change or retry the model request.
        """
        if observer is not None and not callable(observer):
            raise ModelError("模型观察回调无效", "model_options_invalid")
        metadata = {"status": "failed", "provider": "DeepSeek", "model": self.model,
                    "thinking": self.thinking, "http_status": None, "request_id": None, "usage": {}}
        start = time.monotonic()
        try:
            result = self._complete(messages, tools, metadata)
            metadata["status"] = "completed"
            return result, metadata
        except ModelError as error:
            metadata["code"] = error.code
            if error.http_status is not None:
                metadata["http_status"] = error.http_status
            if error.request_id is not None:
                metadata["request_id"] = error.request_id
            raise
        finally:
            metadata["elapsed_ms"] = max(0, round((time.monotonic() - start) * 1000))
            if observer is not None:
                try:
                    observer(deepcopy(metadata))
                except Exception:
                    pass

    def _sse_data(self, response, deadline):
        """Yield bounded SSE data events, including when UTF-8/CRLF spans reads."""
        buffer, data, total, reads, events = b"", [], 0, 0, 0
        first_line, skip_lf = True, False
        read = getattr(response, "read1", response.read)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            # urllib's HTTPResponse owns this request's socket. Shorten each
            # blocking read to the total deadline rather than renewing timeout.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            part = read(min(4096, self.STREAM_MAX_BYTES - total + 1))
            reads += 1
            if time.monotonic() >= deadline:
                raise TimeoutError
            if not isinstance(part, bytes):
                raise ValueError("invalid stream bytes")
            if not part:
                return
            total += len(part)
            if total > self.STREAM_MAX_BYTES or reads > self.STREAM_MAX_READS:
                raise ModelError("模型响应超出上限", "model_response_limit")
            if skip_lf:
                if part.startswith(b"\n"):
                    part = part[1:]
                skip_lf = False
            buffer += part
            while True:
                boundary = re.search(br"[\r\n]", buffer)
                if boundary is None:
                    break
                index = boundary.start()
                line, delimiter, buffer = buffer[:index], buffer[index:index + 1], buffer[index + 1:]
                if delimiter == b"\r":
                    if buffer.startswith(b"\n"):
                        buffer = buffer[1:]
                    elif not buffer:
                        skip_lf = True
                if first_line:
                    line = line.removeprefix(b"\xef\xbb\xbf")
                    first_line = False
                if not line:
                    if data:
                        events += 1
                        if events > self.STREAM_MAX_CHUNKS:
                            raise ModelError("模型响应超出上限", "model_response_limit")
                        event, data = b"\n".join(data), []
                        yield event.decode("utf-8")
                    continue
                if line.startswith(b":"):
                    continue  # Provider keepalive; still counts toward bytes/deadline.
                field, separator, value = line.partition(b":")
                if field == b"data":
                    data.append(value[1:] if value.startswith(b" ") else value)

    def _stream_message(self, response, metadata, deadline, on_reasoning):
        content, reasoning, calls = "", "", {}
        content_seen, role_seen, finished, done = False, False, None, False
        completion_id, usage_seen, pending_reasoning = None, False, []
        protocol_bytes = 0

        def bounded(value):
            nonlocal protocol_bytes
            protocol_bytes += len(value.encode("utf-8"))
            if protocol_bytes > self.STREAM_MAX_PROTOCOL_BYTES:
                raise ModelError("模型响应超出上限", "model_response_limit")

        def progress(delta):
            if on_reasoning is not None:
                try:
                    on_reasoning(delta)
                except Exception:
                    pass  # Private diagnostics cannot fail or retry the request.

        for event in self._sse_data(response, deadline):
            if time.monotonic() >= deadline:
                raise TimeoutError
            if event == "[DONE]":
                done = True
                break
            body = strict_json(event)
            if not isinstance(body, dict) or body.get("object", "chat.completion.chunk") != "chat.completion.chunk":
                raise ValueError("invalid stream chunk")
            identifier = self._identifier(body.get("id"))
            if identifier is None or completion_id is not None and identifier != completion_id:
                raise ValueError("invalid stream identity")
            completion_id = identifier
            metadata["completion_id"] = identifier
            choices, usage = body.get("choices"), body.get("usage")
            if usage is not None:
                if not isinstance(usage, dict) or usage_seen:
                    raise ValueError("invalid stream usage")
                projected = self._usage(usage)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                    if key in usage and key not in projected:
                        raise ValueError("invalid token count")
                for key, count_key in (("prompt_tokens_details", "cached_tokens"), ("completion_tokens_details", "reasoning_tokens")):
                    if key in usage and (not isinstance(usage[key], dict) or count_key in usage[key] and count_key not in projected.get(key, {})):
                        raise ValueError("invalid token details")
                metadata["usage"], usage_seen = projected, True
            # Current DeepSeek puts usage on the terminal choice. Also admit a
            # compatible usage-only terminal chunk after an already ended choice.
            if choices == [] and finished is not None and usage is not None:
                continue
            if not isinstance(choices, list) or len(choices) != 1 or finished is not None:
                raise ValueError("invalid stream choices")
            choice = choices[0]
            if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
                raise ValueError("invalid stream choice index")
            delta, finish = choice.get("delta"), choice.get("finish_reason")
            if not isinstance(delta, dict) or "finish_reason" not in choice:
                raise ValueError("invalid stream delta")
            role = delta.get("role")
            if role is not None:
                if role != "assistant":
                    raise ValueError("invalid stream role")
                role_seen = True
            for key in ("content", "reasoning_content"):
                value = delta.get(key)
                if value is not None and not isinstance(value, str):
                    raise ValueError("invalid stream text")
                if value is not None:
                    bounded(value)
            if delta.get("content") is not None:
                content_seen = True
                content += delta["content"]
                if self._key in content:
                    raise ValueError("invalid stream content")
            if self.thinking and delta.get("reasoning_content"):
                value = delta["reasoning_content"]
                reasoning += value
                pending_reasoning.append(value)
                if self._key in reasoning:
                    raise ValueError("invalid provider reasoning")
                # Do not leak a credential split across deltas. Delay ambiguous
                # deltas, then emit each original delta unchanged once safe.
                ambiguous = any(reasoning.endswith(self._key[:size]) for size in range(1, len(self._key)))
                if not ambiguous:
                    for part in pending_reasoning:
                        progress(part)
                    pending_reasoning.clear()
            tool_deltas = delta.get("tool_calls")
            if tool_deltas is not None:
                if not isinstance(tool_deltas, list) or len(tool_deltas) > 8:
                    raise ValueError("invalid stream tools")
                indexes = set()
                for item in tool_deltas:
                    if not isinstance(item, dict) or type(item.get("index")) is not int or not 0 <= item["index"] < 8 or item["index"] in indexes:
                        raise ValueError("invalid stream tool index")
                    index = item["index"]
                    indexes.add(index)
                    call = calls.setdefault(index, {"id": "", "type": None, "function": {"name": "", "arguments": ""}})
                    if item.get("type") is not None:
                        if item["type"] != "function":
                            raise ValueError("invalid stream tool type")
                        call["type"] = "function"
                    if item.get("id") is not None:
                        if not isinstance(item["id"], str):
                            raise ValueError("invalid stream tool id")
                        bounded(item["id"])
                        call["id"] += item["id"]
                    function = item.get("function")
                    if function is not None:
                        if not isinstance(function, dict):
                            raise ValueError("invalid stream function")
                        for key, limit in (("name", 80), ("arguments", 8000)):
                            if function.get(key) is not None:
                                if not isinstance(function[key], str):
                                    raise ValueError("invalid stream function fragment")
                                bounded(function[key])
                                call["function"][key] += function[key]
                            if len(call["function"][key]) > limit or self._key in call["function"][key]:
                                raise ValueError("invalid stream function")
                    if len(call["id"]) > 150 or self._key in call["id"]:
                        raise ValueError("invalid stream tool id")
            if finish is not None:
                if finish not in {"stop", "tool_calls"}:
                    raise ModelError("模型输出未完整结束，本轮不采纳", "model_incomplete")
                finished = finish
            if usage is not None and finished is None:
                raise ValueError("premature stream usage")
        if not done or finished is None:
            raise ModelError("模型输出未完整结束，本轮不采纳", "model_incomplete")
        if not role_seen or (finished == "tool_calls") != bool(calls):
            raise ValueError("invalid stream completion")
        result = {"role": "assistant", "content": content if content_seen else None}
        if calls:
            if set(calls) != set(range(len(calls))):
                raise ValueError("incomplete stream tool indexes")
            seen = set()
            for call in calls.values():
                function = call["function"]
                if (call["type"] != "function" or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", call["id"])
                        or call["id"] in seen or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", function["name"])
                        or not isinstance(strict_json(function["arguments"]), dict)):
                    raise ValueError("invalid completed stream tool")
                seen.add(call["id"])
            result["tool_calls"] = [calls[index] for index in sorted(calls)]
        elif not content.strip():
            raise ValueError("empty stream answer")
        for part in pending_reasoning:
            progress(part)
        if time.monotonic() >= deadline:
            raise TimeoutError
        if self.thinking and reasoning:
            result["reasoning_content"] = reasoning
            metadata["provider_reasoning"] = reasoning
        return result

    def _complete_stream(self, messages, tools, metadata, on_reasoning, deadline):
        if not self._key:
            raise ModelError("模型未配置", "model_not_configured")
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, "max_tokens": 8192 if self.thinking else 1800,
                   "thinking": {"type": "enabled" if self.thinking else "disabled"}}
        if self.thinking:
            payload["reasoning_effort"] = "low"
        else:
            payload["temperature"] = 0
        if tools:
            payload["tools"] = tools
        try:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ModelError("本轮模型请求格式无效", "model_request_invalid") from None
        if len(data) > 160_000:
            raise ModelError("本轮上下文超出上限，请开始新对话", "model_context_limit")
        request = Request(self.ENDPOINT, data=data, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json", "Accept": "text/event-stream"})
        http_status, request_id = None, None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            with self._opener.open(request, timeout=remaining) as response:
                status = getattr(response, "status", None)
                http_status = status if type(status) is int and 100 <= status <= 599 else None
                request_id = self._request_id(response)
                metadata.update(http_status=http_status, request_id=request_id)
                return self._stream_message(response, metadata, deadline, on_reasoning)
        except HTTPError as error:
            code, request_id = error.code, self._request_id(error)
            error.close()
            messages_by_code = {401: "模型鉴权失败，请检查本机 Key", 402: "模型账户余额不足", 429: "模型服务限流，请稍后重试"}
            raise ModelError(messages_by_code.get(code, "模型服务请求失败"), f"model_http_{code}",
                             http_status=code, request_id=request_id) from None
        except ModelError as error:
            error.http_status, error.request_id = http_status, request_id
            raise
        except (URLError, TimeoutError, OSError):
            raise ModelError("模型服务超时或不可连接", "model_unavailable",
                             http_status=http_status, request_id=request_id) from None
        except Exception:
            raise ModelError("模型响应格式无效，本轮不采纳", "model_protocol_error",
                             http_status=http_status, request_id=request_id) from None

    def complete_stream_with_metadata(self, messages, tools, *, observer=None, on_reasoning=None):
        """Opt-in SSE transport; only real provider reasoning deltas are observed.

        on_reasoning receives each nonempty reasoning delta, never content/tool
        arguments. Partial deltas are private progress, not an accepted answer.
        A malformed/interrupted stream is never retried or returned as success.
        """
        if any(callback is not None and not callable(callback) for callback in (observer, on_reasoning)):
            raise ModelError("模型观察回调无效", "model_options_invalid")
        metadata = {"status": "failed", "provider": "DeepSeek", "model": self.model,
                    "thinking": self.thinking, "http_status": None, "request_id": None, "usage": {}, "streaming": True}
        start = time.monotonic()
        try:
            result = self._complete_stream(messages, tools, metadata, on_reasoning, start + self.timeout)
            metadata["status"] = "completed"
            return result, metadata
        except ModelError as error:
            metadata["code"] = error.code
            if error.http_status is not None:
                metadata["http_status"] = error.http_status
            if error.request_id is not None:
                metadata["request_id"] = error.request_id
            raise
        finally:
            metadata["elapsed_ms"] = max(0, round((time.monotonic() - start) * 1000))
            if observer is not None:
                try:
                    observer(deepcopy(metadata))
                except Exception:
                    pass

    def complete(self, messages, tools, *, observer=None):
        """Compatibility API: metadata is opt-in and never appended to messages."""
        return self.complete_with_metadata(messages, tools, observer=observer)[0]
