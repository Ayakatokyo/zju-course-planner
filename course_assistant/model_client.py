"""Bounded DeepSeek transport. Secrets remain server-side; never echo provider errors."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ModelError(ValueError):
    def __init__(self, message, code="model_error"):
        super().__init__(message)
        self.code = code


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

    def status(self):
        return {"configured": bool(self._key), "provider": "DeepSeek", "model": self.model,
                "remote": True, "allow_reference_text": self.allow_reference_text,
                "reason": "" if self._key else "未配置 DEEPSEEK_API_KEY；请在本机 .env 填写并重启服务",
                "data_scope": "固定 synthetic 夹具与经同意的测试对话；真实学校数据未接入"}

    def complete(self, messages, tools):
        if not self._key:
            raise ModelError("模型未配置", "model_not_configured")
        payload = {"model": self.model, "messages": messages,
                   "stream": False, "max_tokens": 1800, "temperature": 0,
                   "thinking": {"type": "disabled"}}
        if tools:
            payload["tools"] = tools
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > 160_000:
            raise ModelError("本轮上下文超出上限，请开始新对话", "model_context_limit")
        request = Request(self.ENDPOINT, data=data, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ModelError("模型响应超出上限", "model_response_limit")
            body = strict_json(raw)
            if not isinstance(body, dict):
                raise ValueError("invalid response")
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
            # Never forward internal reasoning, provider metadata, or unrecognized fields.
            result = {"role": "assistant", "content": content}
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
            return result
        except HTTPError as error:
            # Do not read/echo provider body: it can contain submitted material or credentials.
            error.close()
            code = error.code
            messages_by_code = {401: "模型鉴权失败，请检查本机 Key", 402: "模型账户余额不足",
                                429: "模型服务限流，请稍后重试"}
            raise ModelError(messages_by_code.get(code, "模型服务请求失败"), f"model_http_{code}") from None
        except (URLError, TimeoutError, OSError):
            raise ModelError("模型服务超时或不可连接", "model_unavailable") from None
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            if isinstance(error, ModelError):
                raise
            raise ModelError("模型响应格式无效，本轮不采纳", "model_protocol_error") from None
