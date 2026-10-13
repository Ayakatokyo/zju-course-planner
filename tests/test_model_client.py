import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from course_assistant.model_client import ModelClient, ModelError, NoRedirect, read_config, strict_json


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self, body=None, error=None):
        self.body, self.error = body, error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return Response(self.body)


def response(message=None, finish="stop"):
    return json.dumps({"choices": [{"finish_reason": finish, "message": message or {
        "role": "assistant", "content": "合成结果已返回", "reasoning_content": "private reasoning"}}]}).encode()


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


if __name__ == "__main__":
    unittest.main()
