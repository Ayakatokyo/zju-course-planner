import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from course_assistant.model_client import ModelClient, ModelError, NoRedirect, read_config, strict_json


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}


class Opener:
    def __init__(self, body=None, error=None, headers=None):
        self.body, self.error = body, error
        self.headers = headers or {}
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return Response(self.body, headers=self.headers)


def response(message=None, finish="stop"):
    return json.dumps({"choices": [{"finish_reason": finish, "message": message or {
        "role": "assistant", "content": "合成结果已返回", "reasoning_content": "private reasoning"}}]}).encode()


def stream_chunk(delta=None, finish=None, *, usage=None, identifier="synthetic-stream"):
    result = {"id": identifier, "object": "chat.completion.chunk", "choices": [
        {"index": 0, "delta": delta if delta is not None else {}, "finish_reason": finish}]}
    if usage is not None:
        result["usage"] = usage
    return result


def stream_response(*events, done=True, newline="\n"):
    values = [event if isinstance(event, str) else json.dumps(event, ensure_ascii=False) for event in events]
    if done:
        values.append("[DONE]")
    return (newline.join("data: " + value + newline for value in values) + newline).encode()


class FragmentedResponse(Response):
    def __init__(self, body, *, fragment_size=1):
        super().__init__(body)
        self.fragment_size, self.read_count = fragment_size, 0

    def read1(self, size=-1):
        self.read_count += 1
        return self.read(min(size, self.fragment_size))


class StreamOpener(Opener):
    def __init__(self, body, *, fragment_size=1):
        super().__init__(body)
        self.response = FragmentedResponse(body, fragment_size=fragment_size)

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response


class ModelClientTests(unittest.TestCase):
    def test_missing_configuration_never_calls_network(self):
        opener = Opener()
        client = ModelClient(opener=opener)
        self.assertFalse(client.status()["configured"])
        with self.assertRaisesRegex(ModelError, "未配置"):
            client.complete([], [])
        self.assertEqual(opener.calls, [])

    def test_exact_endpoint_limits_and_no_secret_in_status(self):
        opener = Opener(response())
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_MODEL": "chosen-model"}, opener=opener)
        result = client.complete([{"role": "user", "content": "测试"}], [])
        self.assertEqual(result, {"role": "assistant", "content": "合成结果已返回"})
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        payload = json.loads(request.data)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 1800)
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["model"], "chosen-model")
        self.assertNotIn("tools", payload)
        self.assertLessEqual(timeout, 30)
        self.assertNotIn("test-secret", json.dumps(client.status()))
        self.assertNotIn("test-secret", request.data.decode())

    def test_provider_errors_do_not_echo_response_or_retry(self):
        for error in (HTTPError(ModelClient.ENDPOINT, 401, "test-secret", {}, Response(b"test-secret")),
                      URLError("test-secret")):
            opener = Opener(error=error)
            with self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete([], [])
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertEqual(len(opener.calls), 1)

    def test_incomplete_malformed_or_duplicate_response_rejected(self):
        for body in (response(finish="length"), b'{"choices":[],"choices":[]}', b'{"choices":NaN}',
                     b"x" * 262145, response({"role": "assistant", "content": None}), b"[]",
                     b'{"choices":[null]}', b'{"choices":[{"finish_reason":"stop","message":null}]}'):
            with self.subTest(body_length=len(body)):
                with self.assertRaises(ModelError):
                    ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(body)).complete([], [])

    def test_tools_are_projected_and_duplicate_ids_rejected(self):
        call = {"id": "call_1", "type": "function", "function": {
            "name": "get_my_context", "arguments": "{}"}, "unrecognized": "drop"}
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
        result = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(response(message, "tool_calls"))).complete([], [])
        self.assertNotIn("unrecognized", result["tool_calls"][0])
        message["tool_calls"].append(call)
        with self.assertRaises(ModelError):
            ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(response(message, "tool_calls"))).complete([], [])

    def test_config_is_data_and_environment_has_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nDEEPSEEK_API_KEY="local-only"\nDEEPSEEK_MODEL=ignored\n'
                            'OTHER_SECRET=hidden\nCOURSE_ALLOW_REFERENCE_TEXT=true\n', encoding="utf-8")
            config = read_config(path, {"DEEPSEEK_MODEL": "selected"})
        self.assertEqual(config, {"DEEPSEEK_API_KEY": "local-only", "DEEPSEEK_MODEL": "selected",
                                  "COURSE_ALLOW_REFERENCE_TEXT": "true"})
        self.assertTrue(ModelClient(config).allow_reference_text)
        self.assertFalse(ModelClient().allow_reference_text)

    def test_redirect_is_rejected(self):
        with self.assertRaises(ModelError):
            NoRedirect().redirect_request(None, None, 302, None, None, "https://other.example")

    def test_strict_json_rejects_nonfinite_and_duplicate_keys(self):
        for raw in ('{"patch":{},"patch":{}}', '{"credits":NaN}', '{"credits":Infinity}'):
            with self.assertRaises(ValueError):
                strict_json(raw)

    def test_live_model_directory_uses_get_without_chat_and_projects_safe_fields(self):
        body = {"object": "list", "data": [{"id": "available-model", "object": "model", "owned_by": "deepseek",
            "name": "Available Model", "context_window": 1048576, "max_output_tokens": 393216,
            "input_modalities": ["text", "image"], "output_modalities": ["text"],
            "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high", "private": "test-secret"},
            "private": "test-secret", "permission": [{"secret": "NEVER-SHOW-PERMISSION"}]}], "private": "test-secret"}
        opener = Opener(json.dumps(body).encode())
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_MODEL": "configured-original"}, opener=opener)
        catalog = client.list_models()
        request, _ = opener.calls[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(catalog["selected_model"], "configured-original")
        self.assertEqual(catalog["models"][0]["effort"], {"supported_levels": ["low", "high", "max"], "default_level": "high"})
        self.assertEqual(catalog["models"][0]["context_window"], 1048576)
        self.assertNotIn("test-secret", json.dumps(catalog))
        self.assertNotIn("NEVER-SHOW", json.dumps(catalog))
        self.assertEqual(client.model, "configured-original")

    def test_directory_missing_configuration_malformed_ids_or_duplicates_never_guess_models(self):
        opener = Opener()
        with self.assertRaises(ModelError) as caught:
            ModelClient(opener=opener).list_models()
        self.assertEqual(caught.exception.code, "model_not_configured")
        self.assertEqual(opener.calls, [])
        for body in ({"object": "list", "data": [{"id": "duplicate"}, {"id": "duplicate"}]},
                     {"object": "list", "data": [{"id": "https://other.example/model"}]},
                     {"object": "list", "data": [{"id": "test-secret"}]},
                     {"object": "list", "data": None}, {"data": []}):
            with self.subTest(body=body), self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(json.dumps(body).encode())).list_models()
            self.assertEqual(caught.exception.code, "model_protocol_error")
        empty = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(b'{"object":"list","data":[]}')).list_models()
        self.assertEqual(empty["models"], [])

    def test_options_are_independent_snapshots_with_existing_key_transport_and_timeout(self):
        opener = Opener(response())
        original = ModelClient({"DEEPSEEK_API_KEY": "test-secret", "DEEPSEEK_MODEL": "configured-original",
            "COURSE_ALLOW_REFERENCE_TEXT": "true"}, opener=opener, timeout=12)
        selected = original.with_options("available-model", thinking=True)
        self.assertIsNot(selected, original)
        self.assertIs(selected._opener, original._opener)
        self.assertEqual((selected._key, selected.timeout, selected.allow_reference_text), (original._key, 12, True))
        self.assertEqual((original.model, original.thinking), ("configured-original", False))
        self.assertEqual((selected.model, selected.thinking), ("available-model", True))
        self.assertEqual(opener.calls, [])
        for model, thinking in (("https://other.example/model", False), ("", False), ("valid-model", "true"), (None, True)):
            with self.assertRaises(ModelError):
                original.with_options(model, thinking=thinking)

    def test_thinking_tool_roundtrip_retains_protocol_reasoning_without_metadata_in_messages(self):
        tool_call = {"id": "call_1", "type": "function", "function": {"name": "get_my_context", "arguments": "{}"}}
        body = {"id": "synthetic-completion", "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_content": "Public provider reasoning over synthetic context.",
            "tool_calls": [tool_call]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                "completion_tokens_details": {"reasoning_tokens": 12}}}
        opener = Opener(json.dumps(body).encode())
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", thinking=True)
        messages = [{"role": "user", "content": "SYNTHETIC QUESTION"}]
        assistant, metadata = client.complete_with_metadata(messages, [{"type": "function"}])
        self.assertEqual(assistant["reasoning_content"], body["choices"][0]["message"]["reasoning_content"])
        self.assertEqual(metadata["provider_reasoning"], assistant["reasoning_content"])
        self.assertEqual(metadata["usage"]["completion_tokens_details"]["reasoning_tokens"], 12)
        self.assertNotIn("usage", assistant)
        self.assertNotIn("elapsed_ms", assistant)
        opener.body = response({"role": "assistant", "content": "SYNTHETIC ANSWER", "reasoning_content": "Public final reasoning."})
        messages += [assistant, {"role": "tool", "tool_call_id": "call_1", "content": "SYNTHETIC TOOL RESULT"}]
        client.complete(messages, [{"type": "function"}])
        payload = json.loads(opener.calls[-1][0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(opener.calls[-1][1], 30)
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["messages"][1], assistant)
        self.assertNotIn("usage", payload["messages"][1])
        self.assertEqual(messages[0]["content"], "SYNTHETIC QUESTION")

    def test_opt_in_metadata_whitelists_usage_ids_and_never_prompt_or_tools(self):
        body = {"id": "synthetic-completion", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "SYNTHETIC ANSWER", "reasoning_content": "SHOULD-NOT-EXPOSE-WHILE-DISABLED"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "negative": -1,
                "prompt_cache_hit_tokens": True, "prompt_cache_miss_tokens": -1,
                "prompt_tokens_details": {"cached_tokens": 2, "secret": "test-secret"}, "raw_prompt": "NEVER-LOG-PROMPT"},
            "raw_headers": {"secret": "test-secret"}}
        opener = Opener(json.dumps(body).encode(), headers={"x-request-id": "synthetic-request", "secret": "test-secret"})
        seen = []
        message, metadata = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_with_metadata(
            [{"role": "user", "content": "NEVER-LOG-PROMPT"}], [{"PRIVATE-TOOL-DATA": "NEVER-LOG-TOOLS"}], observer=seen.append)
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(metadata["http_status"], 200)
        self.assertEqual(metadata["request_id"], "synthetic-request")
        self.assertEqual(metadata["completion_id"], "synthetic-completion")
        self.assertIsInstance(metadata["elapsed_ms"], int)
        self.assertEqual(metadata["usage"], {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                                          "prompt_tokens_details": {"cached_tokens": 2}})
        self.assertEqual(seen, [metadata])
        self.assertNotIn("reasoning_content", message)
        self.assertNotIn("provider_reasoning", metadata)
        for private in ("test-secret", "NEVER-LOG", "SHOULD-NOT-EXPOSE", "PRIVATE-TOOL-DATA"):
            self.assertNotIn(private, json.dumps(metadata))

    def test_observer_error_or_mutation_does_not_change_response_or_retry(self):
        opener = Opener(response())
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener)
        def broken(metadata):
            metadata.clear()
            raise RuntimeError("NEVER-ECHO-OBSERVER")
        message, metadata = client.complete_with_metadata([], [], observer=broken)
        self.assertEqual(message["content"], "合成结果已返回")
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(len(opener.calls), 1)

    def test_error_metadata_is_safe_once_and_does_not_read_raw_provider_error(self):
        class ForbiddenBody(Response):
            def read(self, *_):
                raise AssertionError("Must not read provider error body")
        error = HTTPError(ModelClient.ENDPOINT, 429, "test-secret NEVER-LOG-ERROR", {"x-request-id": "synthetic-request"}, ForbiddenBody(b"NEVER-LOG-ERROR"))
        opener, seen = Opener(error=error), []
        with self.assertRaises(ModelError) as caught:
            ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete([], [], observer=seen.append)
        self.assertEqual(caught.exception.code, "model_http_429")
        self.assertEqual(seen[0]["status"], "failed")
        self.assertEqual(seen[0]["http_status"], 429)
        self.assertEqual(seen[0]["request_id"], "synthetic-request")
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn("test-secret", str(caught.exception) + json.dumps(seen))
        self.assertNotIn("NEVER-LOG-ERROR", str(caught.exception) + json.dumps(seen))

    def test_invalid_or_key_echo_reasoning_and_secret_request_ids_fail_safely(self):
        for reasoning in ({"private": "test-secret"}, "provider echoed test-secret"):
            opener = Opener(response({"role": "assistant", "content": "answer", "reasoning_content": reasoning}),
                headers={"x-request-id": "test-secret"})
            seen = []
            with self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", thinking=True).complete([], [], observer=seen.append)
            self.assertEqual(caught.exception.code, "model_protocol_error")
            self.assertIsNone(seen[0]["request_id"])
            self.assertNotIn("test-secret", json.dumps(seen))

    def test_stream_request_uses_fixed_endpoint_and_merges_content_and_safe_usage(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
                 "prompt_tokens_details": {"cached_tokens": 2, "raw": "NEVER-LOG"}, "raw_prompt": "NEVER-LOG"}
        opener = Opener(stream_response(stream_chunk({"role": "assistant", "content": "合成"}),
            stream_chunk({"content": "答案", "reasoning_content": "HIDDEN-WHILE-DISABLED"}),
            stream_chunk({}, "stop", usage=usage)), headers={"x-request-id": "synthetic-request"})
        seen, progress = [], []
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener)
        message, metadata = client.complete_stream_with_metadata([{"role": "user", "content": "SYNTHETIC"}], [],
            observer=seen.append, on_reasoning=progress.append)
        self.assertEqual(message, {"role": "assistant", "content": "合成答案"})
        self.assertEqual(progress, [])
        self.assertEqual(metadata["usage"], {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
            "prompt_tokens_details": {"cached_tokens": 2}})
        self.assertEqual((metadata["status"], metadata["http_status"], metadata["request_id"]),
            ("completed", 200, "synthetic-request"))
        self.assertEqual(seen, [metadata])
        self.assertTrue(metadata["streaming"])
        request, timeout = opener.calls[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, ModelClient.ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Accept"), "text/event-stream")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertEqual((payload["max_tokens"], payload["temperature"]), (1800, 0))
        self.assertLessEqual(timeout, 30)
        for private in ("test-secret", "SYNTHETIC", "NEVER-LOG", "HIDDEN-WHILE"):
            self.assertNotIn(private, json.dumps(metadata))

    def test_stream_unicode_sse_framing_and_exact_reasoning_deltas_before_final_content(self):
        events = [stream_chunk({"role": "assistant", "reasoning_content": "先查合成"}),
                  stream_chunk({"reasoning_content": "资料。"}), stream_chunk({"content": '{"kind":"answer"}'}),
                  stream_chunk({}, "stop", usage={"completion_tokens_details": {"reasoning_tokens": 4}})]
        wire = b"\xef\xbb\xbf: keepalive\r\n\r\n" + stream_response(*events, newline="\r\n")
        opener, progress, reads = StreamOpener(wire, fragment_size=1), [], []
        def on_reasoning(delta):
            progress.append(delta)
            reads.append(opener.response.read_count)
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", True)
        message, metadata = client.complete_stream_with_metadata([], [], on_reasoning=on_reasoning)
        self.assertEqual(progress, ["先查合成", "资料。"])
        self.assertEqual(message["reasoning_content"], "先查合成资料。")
        self.assertEqual(metadata["provider_reasoning"], message["reasoning_content"])
        self.assertEqual(message["content"], '{"kind":"answer"}')
        self.assertLess(reads[0], reads[1])
        self.assertLess(reads[-1], opener.response.read_count)
        payload = json.loads(opener.calls[0][0].data)
        self.assertEqual((payload["max_tokens"], payload["reasoning_effort"]), (8192, "low"))
        self.assertNotIn("temperature", payload)
        self.assertEqual(metadata["usage"]["completion_tokens_details"], {"reasoning_tokens": 4})

    def test_stream_multiline_data_cr_framing_and_compatible_usage_only_chunk(self):
        first = json.dumps(stream_chunk({"role": "assistant", "content": "合成结果"}), ensure_ascii=False)
        first = "data: " + first.replace(', "choices"', ',\rdata: "choices"') + "\r\r"
        usage_only = {"id": "synthetic-stream", "object": "chat.completion.chunk", "choices": [],
                      "usage": {"total_tokens": 7}}
        wire = first.encode() + stream_response(stream_chunk({}, "stop"), usage_only, newline="\r")
        message, metadata = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=StreamOpener(wire)).complete_stream_with_metadata([], [])
        self.assertEqual(message["content"], "合成结果")
        self.assertEqual(metadata["usage"], {"total_tokens": 7})

    def test_stream_interleaved_tool_indexes_accumulate_id_name_and_arguments(self):
        events = [stream_chunk({"role": "assistant", "tool_calls": [
            {"index": 1, "id": "call_", "type": "function", "function": {"name": "query_", "arguments": '{"query":'}},
            {"index": 0, "id": "call_A", "type": "function", "function": {"name": "get_my_", "arguments": "{"}}]}),
            stream_chunk({"tool_calls": [
                {"index": 0, "function": {"name": "context", "arguments": "}"}},
                {"index": 1, "id": "B", "function": {"name": "my_records", "arguments": '"合成"}'}}]}),
            stream_chunk({}, "tool_calls")]
        opener = Opener(stream_response(*events))
        message, metadata = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_stream_with_metadata([], [{"type": "function"}])
        self.assertEqual(message, {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_A", "type": "function", "function": {"name": "get_my_context", "arguments": "{}"}},
            {"id": "call_B", "type": "function", "function": {"name": "query_my_records", "arguments": '{"query":"合成"}'}}]})
        self.assertEqual(metadata["usage"], {})
        self.assertNotIn("provider_reasoning", metadata)
        self.assertEqual(json.loads(opener.calls[0][0].data)["tools"], [{"type": "function"}])

    def test_stream_incomplete_or_malformed_streams_never_retry_or_accept_partial_answer(self):
        initial = stream_chunk({"role": "assistant", "content": "PARTIAL-SYNTHETIC"})
        malformed = [stream_response(initial, done=False), stream_response(initial),
            stream_response(initial, stream_chunk({}, "stop"), done=False),
            stream_response(initial, '{"choices":[],"choices":[]}'),
            stream_response(initial, '{"choices":NaN}'), stream_response(initial, "not json"),
            stream_response(initial, stream_chunk({}, "stop", identifier="changed-stream")),
            stream_response(initial, stream_chunk({"role": "user"}, "stop")),
            stream_response(initial, stream_chunk({"content": 4}, "stop")),
            stream_response(initial, stream_chunk({}, "stop"), stream_chunk({"content": "late"})),
            b"data: \xff\n\n", stream_response(stream_chunk({}, "stop"))]
        malformed += [stream_response(initial, stream_chunk({}, finish)) for finish in
                      ("length", "content_filter", "insufficient_system_resource", "aborted", "unknown")]
        for wire in malformed:
            with self.subTest(wire_length=len(wire)):
                opener, seen = Opener(wire), []
                with self.assertRaises(ModelError) as caught:
                    ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_stream_with_metadata([], [], observer=seen.append)
                self.assertIn(caught.exception.code, {"model_incomplete", "model_protocol_error"})
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0]["status"], "failed")
                self.assertNotIn("PARTIAL-SYNTHETIC", json.dumps(seen))

    def test_stream_invalid_tools_indexes_and_fragmented_json_are_rejected(self):
        good = {"index": 0, "id": "call_A", "type": "function", "function": {"name": "get_my_context", "arguments": "{}"}}
        invalid = [{**good, "index": True}, {**good, "index": -1}, {**good, "index": 8}, {**good, "index": 1},
                   {**good, "type": "other"}, {**good, "id": "bad/id"},
                   {**good, "function": {"name": "", "arguments": "{}"}},
                   {**good, "function": {"name": "get_my_context", "arguments": '{"a":1,"a":2}'}},
                   {**good, "function": {"name": "get_my_context", "arguments": '{"a":'}}]
        cases = [([item], "tool_calls") for item in invalid]
        cases += [([good, good], "tool_calls"), ([good, {**good, "index": 1}], "tool_calls"),
                  ([good], "stop"), ([], "tool_calls")]
        for calls, finish in cases:
            with self.subTest(calls=calls, finish=finish):
                opener = Opener(stream_response(stream_chunk({"role": "assistant", "content": "合成", "tool_calls": calls}),
                    stream_chunk({}, finish)))
                with self.assertRaises(ModelError) as caught:
                    ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_stream_with_metadata([], [])
                self.assertEqual(caught.exception.code, "model_protocol_error")
                self.assertEqual(len(opener.calls), 1)

    def test_stream_usage_types_counts_and_timing_are_validated(self):
        initial = stream_chunk({"role": "assistant", "content": "合成"})
        for usage in ([], "RAW-PROVIDER", {"total_tokens": -1}, {"total_tokens": True},
                      {"prompt_tokens_details": 4}, {"completion_tokens_details": {"reasoning_tokens": -1}}):
            with self.subTest(usage=usage), self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(stream_response(initial,
                    stream_chunk({}, "stop", usage=usage)))).complete_stream_with_metadata([], [])
            self.assertEqual(caught.exception.code, "model_protocol_error")
        for events in ([stream_chunk({"role": "assistant", "content": "合成"}, usage={"total_tokens": 1}), stream_chunk({}, "stop")],
                       [initial, stream_chunk({}, "stop", usage={"total_tokens": 1}),
                        {"id": "synthetic-stream", "choices": [], "usage": {"total_tokens": 1}}]):
            with self.assertRaises(ModelError):
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(stream_response(*events))).complete_stream_with_metadata([], [])

    def test_stream_wire_protocol_event_and_read_limits_are_independent(self):
        initial = stream_chunk({"role": "assistant", "content": "合成结果"})
        wire = stream_response(initial, stream_chunk({}, "stop"))
        for setting, limit, opener in (("STREAM_MAX_BYTES", len(wire) - 1, Opener(wire)),
            ("STREAM_MAX_PROTOCOL_BYTES", 2, Opener(wire)), ("STREAM_MAX_CHUNKS", 2, Opener(wire)),
            ("STREAM_MAX_READS", 2, StreamOpener(wire))):
            with self.subTest(setting=setting), patch.object(ModelClient, setting, limit):
                with self.assertRaises(ModelError) as caught:
                    ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_stream_with_metadata([], [])
                self.assertEqual(caught.exception.code, "model_response_limit")
                self.assertEqual(len(opener.calls), 1)
        # SSE framing can exceed the non-stream response limit while accepted
        # protocol text remains small; ordinary response limits stay unchanged.
        keepalive = b":" + b"x" * 270000 + b"\n\n"
        message, _ = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=Opener(keepalive + wire)).complete_stream_with_metadata([], [])
        self.assertEqual(message["content"], "合成结果")

    def test_stream_total_deadline_is_not_reset_by_keepalive(self):
        wire = b": keepalive\n\n" + stream_response(stream_chunk({"role": "assistant", "content": "合成"}, "stop"))
        opener, seen = Opener(wire), []
        with patch("course_assistant.model_client.time.monotonic", side_effect=[0, 0, 1, 31, 32]):
            with self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener, timeout=30).complete_stream_with_metadata([], [], observer=seen.append)
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(seen[0]["status"], "failed")

    def test_stream_remaining_socket_deadline_and_progress_time_are_in_total_budget(self):
        wire = stream_response(stream_chunk({"role": "assistant", "reasoning_content": "合成推理。"}),
            stream_chunk({"content": "合成答案"}, "stop"))
        opener = StreamOpener(wire, fragment_size=90)
        deadlines, now = [], [0]
        opener.response.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=deadlines.append)))
        with patch("course_assistant.model_client.time.monotonic", side_effect=lambda: now[0]):
            client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", True)
            def progress(_):
                now[0] = 31
            with self.assertRaises(ModelError) as caught:
                client.complete_stream_with_metadata([], [], on_reasoning=progress)
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertTrue(deadlines)
        self.assertTrue(all(0 < remaining <= 30 for remaining in deadlines))
        self.assertEqual(len(opener.calls), 1)

    def test_stream_http_failure_redirection_and_connection_error_remain_sanitized(self):
        class ForbiddenBody(Response):
            def read(self, *_):
                raise AssertionError("Must not read provider error body")
            read1 = read
        errors = [HTTPError(ModelClient.ENDPOINT, 429, "test-secret RAW-ERROR", {"x-request-id": "synthetic-request"},
                            ForbiddenBody(b"RAW-ERROR")), URLError("test-secret RAW-ERROR"),
                  ModelError("模型服务重定向被拒绝", "model_redirect_rejected")]
        for error in errors:
            opener, seen = Opener(error=error), []
            with self.subTest(error_type=type(error).__name__), self.assertRaises(ModelError) as caught:
                ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).complete_stream_with_metadata([], [], observer=seen.append)
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(len(seen), 1)
            self.assertNotIn("test-secret", str(caught.exception) + json.dumps(seen))
            self.assertNotIn("RAW-ERROR", str(caught.exception) + json.dumps(seen))

    def test_stream_transport_failure_after_real_reasoning_does_not_return_partial_result(self):
        class InterruptedResponse(Response):
            def read1(self, *_):
                if self.tell():
                    raise OSError("test-secret RAW-TRANSPORT-ERROR")
                return self.read()
        wire = stream_response(stream_chunk({"role": "assistant", "reasoning_content": "合成检查进行中。"}), done=False)
        opener, progress, seen = StreamOpener(wire), [], []
        opener.response = InterruptedResponse(wire)
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", True)
        with self.assertRaises(ModelError) as caught:
            client.complete_stream_with_metadata([], [], observer=seen.append, on_reasoning=progress.append)
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(progress, ["合成检查进行中。"])
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(seen[0]["status"], "failed")
        self.assertNotIn("provider_reasoning", seen[0])
        for private in ("test-secret", "RAW-TRANSPORT-ERROR"):
            self.assertNotIn(private, str(caught.exception) + json.dumps(seen))

    def test_stream_observers_cannot_change_request_and_split_credentials_never_reach_progress(self):
        wire = stream_response(stream_chunk({"role": "assistant", "reasoning_content": "正文te"}),
            stream_chunk({"reasoning_content": "stX"}), stream_chunk({"reasoning_content": "结尾t"}),
            stream_chunk({"content": "合成答案"}, "stop"))
        opener, progress = Opener(wire), []
        def broken_progress(delta):
            progress.append(delta)
            raise RuntimeError("NEVER-ECHO-PROGRESS")
        def broken_observer(value):
            value.clear()
            raise RuntimeError("NEVER-ECHO-OBSERVER")
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener).with_options("available-model", True)
        message, metadata = client.complete_stream_with_metadata([], [], observer=broken_observer, on_reasoning=broken_progress)
        self.assertEqual(progress, ["正文te", "stX", "结尾t"])
        self.assertEqual(message["reasoning_content"], "正文testX结尾t")
        self.assertEqual(metadata["status"], "completed")
        self.assertEqual(len(opener.calls), 1)
        progress, seen = [], []
        opener.body = stream_response(stream_chunk({"role": "assistant", "reasoning_content": "公开te"}),
            stream_chunk({"reasoning_content": "st-"}), stream_chunk({"reasoning_content": "secret"}),
            stream_chunk({"content": "合成答案"}, "stop"))
        with self.assertRaises(ModelError) as caught:
            client.complete_stream_with_metadata([], [], observer=seen.append, on_reasoning=progress.append)
        self.assertEqual(caught.exception.code, "model_protocol_error")
        self.assertEqual(progress, [])
        self.assertNotIn("test-secret", str(caught.exception) + json.dumps(seen))
        self.assertNotIn("provider_reasoning", seen[0])

    def test_stream_missing_configuration_invalid_callbacks_and_request_limits_do_not_call_network(self):
        opener = Opener()
        with self.assertRaises(ModelError) as caught:
            ModelClient(opener=opener).complete_stream_with_metadata([], [])
        self.assertEqual(caught.exception.code, "model_not_configured")
        client = ModelClient({"DEEPSEEK_API_KEY": "test-secret"}, opener=opener)
        for kwargs in ({"observer": "invalid"}, {"on_reasoning": "invalid"}):
            with self.assertRaises(ModelError) as caught:
                client.complete_stream_with_metadata([], [], **kwargs)
            self.assertEqual(caught.exception.code, "model_options_invalid")
        for messages, code in (([{"role": "user", "content": "x" * 160000}], "model_context_limit"),
                               ([{"content": float("nan")}], "model_request_invalid")):
            with self.assertRaises(ModelError) as caught:
                client.complete_stream_with_metadata(messages, [])
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(opener.calls, [])


if __name__ == "__main__":
    unittest.main()
