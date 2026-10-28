"""SIWC boundary regression: synthetic keys/transports, no live accounts or schools."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import IncompleteRead
import io
import json
import socket
import ssl
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from course_assistant.auth import AccessError, SCHOOL_MODE, SCHOOL_REALM
from course_assistant.chatgpt_auth import AUTH, DIRECT, ChatGPTAccounts, jwt_library
from course_assistant.chatgpt_client import ChatGPTClient, ModelRouter
from course_assistant.chat_model_settings import ChatModelSettings
from course_assistant.model_client import ModelClient, ModelError


class Auth:
    valid = True
    def require_context(self, context):
        if not self.valid:
            raise AccessError("expired", 401, "expired")


class Response(io.BytesIO):
    status = 200
    headers = {"x-request-id": "synthetic-request"}


class Transport:
    def __init__(self):
        self.calls, self.replies = [], []
    def open(self, request, timeout):
        self.calls.append(request)
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response(result if isinstance(result, bytes) else json.dumps(result).encode())


def catalog():
    return {"models": [{"slug": "gpt-test", "display_name": "GPT Test", "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}], "default_reasoning_level": "high"},
                       {"slug": "hidden-test", "visibility": "hidden"},
                       {"slug": "unknown-test", "display_name": "Unknown", "visibility": "list"}]}


class ChatGPTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jwt = jwt_library()
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(cls.jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="synthetic-key", alg="RS256")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = SimpleNamespace(security_realm=SCHOOL_REALM, auth_mode=SCHOOL_MODE, student_id="synthetic-owner",
                                       session_ref="synthetic-session", binding_version=1)
        self.auth, self.transport = Auth(), Transport()
        self.service = ChatGPTAccounts(self.auth, Path(self.temp.name) / "credentials", opener=self.transport)
        self.owner = self.service.owner(self.context)

    def tearDown(self):
        with self.service.lock:
            for owner in list(self.service.pending):
                self.service._cancel(owner)
        self.temp.cleanup()

    def token(self, nonce, **claims):
        return self.jwt.encode({"iss": AUTH, "sub": "synthetic-sub", "aud": "oaiapp_test", "iat": int(time.time()),
                                "exp": int(time.time()) + 3600, "nonce": nonce, **claims}, self.key,
                               algorithm="RS256", headers={"kid": "synthetic-key"})

    def begin(self):
        result = self.service.begin(self.context)
        params = {k: v[0] for k, v in parse_qs(urlsplit(result["authorization_url"]).query).items()}
        return params

    def queue_tokens(self, nonce, **changes):
        body = {"access_token": "SYNTHETIC-ACCESS", "refresh_token": "SYNTHETIC-REFRESH", "token_type": "Bearer",
                "expires_in": 3600, "scope": DIRECT + " openid offline_access", "id_token": self.token(nonce), **changes}
        self.transport.replies.extend([body, {"issuer": AUTH, "jwks_uri": AUTH + "/jwks", "revocation_endpoint": AUTH + "/revoke"}, {"keys": [self.jwk]}])
        return body

    def connected(self):
        params = self.begin()
        self.queue_tokens(params["nonce"])
        self.service.finish(self.owner, {"state": params["state"], "client_id": "oaiapp_test", "code": "synthetic-code"})
        self.transport.replies.append(catalog())
        self.service.models(self.context, refresh=True)
        return params

    def test_registration_pkce_callback_and_private_storage(self):
        params = self.connected()
        self.assertEqual(params["client_id"], "dynamic_agent_client")
        self.assertEqual(params["agent_name_hint"], "浙大教务助手")
        self.assertEqual(urlsplit(params["redirect_uri"]).hostname, "127.0.0.1")
        exchange = parse_qs(self.transport.calls[0].data.decode())
        self.assertEqual(exchange["client_id"], ["oaiapp_test"])
        self.assertEqual(exchange["redirect_uri"], [params["redirect_uri"]])
        self.assertNotIn("client_secret", exchange)
        self.assertEqual((self.service.root / (self.owner + ".json")).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.service.root.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("SYNTHETIC", json.dumps(self.service.status(self.context)))
        second = self.begin()
        self.assertEqual(second["client_id"], "oaiapp_test")
        self.assertEqual(second["ext_agent_host_id"], params["ext_agent_host_id"])
        self.assertNotIn("agent_name_hint", second)
        self.assertNotIn("id_token_hint", second)
        self.assertNotEqual(second["state"], params["state"])

    def test_wrong_state_does_not_consume_attempt_and_code_is_one_use(self):
        params = self.begin()
        with self.assertRaises(ModelError):
            self.service.finish(self.owner, {"state": "wrong"})
        self.assertEqual(self.transport.calls, [])
        self.queue_tokens(params["nonce"])
        query = {"state": params["state"], "client_id": "oaiapp_test", "code": "synthetic-code"}
        self.service.finish(self.owner, query)
        with self.assertRaises(ModelError):
            self.service.finish(self.owner, query)
        self.assertEqual(len(self.transport.calls), 3)

    def test_denial_missing_client_and_school_logout_never_exchange(self):
        for query in ({"error": "access_denied"}, {"code": "synthetic-code"}, {"code": "synthetic-code", "client_id": "dynamic_agent_client"}):
            params = self.begin()
            with self.assertRaises(ModelError):
                self.service.finish(self.owner, {"state": params["state"], **query})
        params = self.begin()
        self.auth.valid = False
        with self.assertRaises(AccessError):
            self.service.finish(self.owner, {"state": params["state"], "code": "synthetic-code", "client_id": "oaiapp_test"})
        self.assertEqual(self.transport.calls, [])

    def test_jwt_nonce_audience_issuer_expiration_and_subject(self):
        for claims, nonce, subject in [({}, "other", None), ({"aud": "wrong"}, "nonce", None),
                ({"iss": "https://evil.invalid"}, "nonce", None), ({"exp": 1}, "nonce", None), ({}, "nonce", "other")]:
            self.service.discovery = {"issuer": AUTH, "jwks_uri": AUTH + "/jwks"}
            self.transport.replies.append({"keys": [self.jwk]})
            with self.assertRaises(ModelError) as error:
                self.service._identity(self.token("nonce", **claims), "oaiapp_test", nonce=nonce, subject=subject)
            self.assertEqual(error.exception.code, "chatgpt_identity_invalid")

    def test_missing_plan_scope_does_not_connect(self):
        params = self.begin()
        self.queue_tokens(params["nonce"], scope="openid email")
        with self.assertRaises(ModelError) as error:
            self.service.finish(self.owner, {"state": params["state"], "code": "synthetic", "client_id": "oaiapp_test"})
        self.assertEqual(error.exception.code, "chatgpt_scope_missing")
        self.assertFalse(self.service.status(self.context)["connected"])

    def test_refresh_serialized_rotated_persisted_and_omits_scope(self):
        self.connected()
        record = self.service._record(self.owner)
        record["expires_at"] = 1
        self.transport.replies.append({"access_token": "SYNTHETIC-ACCESS-2", "refresh_token": "SYNTHETIC-REFRESH-2",
                                       "expires_in": 3600, "token_type": "Bearer"})
        before = len(self.transport.calls)
        with ThreadPoolExecutor(4) as pool:
            values = list(pool.map(lambda _: self.service.access(self.context, record["revision"]), range(4)))
        self.assertEqual(values, ["SYNTHETIC-ACCESS-2"] * 4)
        self.assertEqual(len(self.transport.calls), before + 1)
        self.assertNotIn("scope", parse_qs(self.transport.calls[-1].data.decode()))
        saved = json.loads((self.service.root / (self.owner + ".json")).read_text())
        self.assertEqual(saved["refresh_token"], "SYNTHETIC-REFRESH-2")
        self.assertEqual(saved["revision"], record["revision"])

    def test_invalid_grant_marks_reconnect_without_leaking_body(self):
        self.connected()
        record = self.service._record(self.owner)
        record["expires_at"] = 1
        self.transport.replies.append(HTTPError(AUTH, 400, "secret", {}, io.BytesIO(b'{"error":"invalid_grant","secret":"do-not-show"}')))
        with self.assertRaises(ModelError) as error:
            self.service.access(self.context, record["revision"])
        self.assertEqual(error.exception.code, "chatgpt_reconnect_required")
        self.assertNotIn("do-not-show", str(error.exception))
        self.assertFalse(self.service.status(self.context)["connected"])

    def test_transient_refresh_preserves_credentials(self):
        self.connected()
        record = self.service._record(self.owner)
        record["expires_at"] = 1
        self.transport.replies.append(TimeoutError())
        with self.assertRaises(ModelError):
            self.service.access(self.context, record["revision"])
        self.assertEqual(self.service._record(self.owner)["refresh_token"], "SYNTHETIC-REFRESH")
        self.assertTrue(self.service.status(self.context)["connected"])

    def test_models_are_account_scoped_ordered_and_capabilities_not_invented(self):
        self.connected()
        rows = self.service.models(self.context)
        self.assertEqual([row["id"] for row in rows], ["chatgpt:gpt-test", "chatgpt:unknown-test"])
        self.assertEqual(rows[0]["effort"]["supported_levels"], ["low", "high"])
        self.assertEqual(rows[1]["effort"]["supported_levels"], [])
        other = deepcopy(self.context)
        other.student_id = "another-synthetic-owner"
        self.assertFalse(self.service.status(other)["connected"])
        self.assertEqual(self.service.models(other), [])
        router = ModelRouter(self.service, self.context, ModelClient())
        with self.assertRaises(ModelError):
            router.with_options(rows[0]["id"], True, "max")
        with self.assertRaises(ModelError):
            router.with_options(rows[0]["id"], False, "high")

    def test_disconnect_clears_local_and_invalidates_active_client_when_revoke_fails(self):
        self.connected()
        client = ChatGPTClient(self.service, self.context, self.service.models(self.context)[0])
        self.transport.replies.append(TimeoutError())
        result = self.service.disconnect(self.context)
        self.assertFalse(result["connected"])
        self.assertIn("未确认", result["error"])
        saved = json.loads((self.service.root / (self.owner + ".json")).read_text())
        self.assertNotIn("refresh_token", saved)
        self.assertFalse(client.status()["configured"])
        with self.assertRaises(ModelError) as error:
            self.service.access(self.context, client.revision)
        self.assertEqual(error.exception.code, "personal_model_changed")

    def client(self):
        self.connected()
        return ChatGPTClient(self.service, self.context, self.service.models(self.context)[0], reasoning_effort="high")

    @staticmethod
    def stream(output, **extra):
        response = {"status": "completed", "output": output, "usage": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}, **extra}
        return ("data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n").encode()

    def test_responses_tools_roundtrip_preserves_private_reasoning_and_ids(self):
        client = self.client()
        tools = [{"type": "function", "function": {"name": "query_my_records", "parameters": {"type": "object"}}}]
        reasoning = {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "synthetic-encrypted-only"}
        call = {"type": "function_call", "id": "fc_test", "namespace": "academic", "name": "query_my_records", "arguments": "{}", "call_id": "call_test"}
        self.transport.replies.append(self.stream([reasoning, call]))
        messages = [{"role": "system", "content": "synthetic instructions"}, {"role": "user", "content": "synthetic question"}]
        result, metadata = client.complete_with_metadata(messages, tools)
        self.assertEqual(metadata["usage"]["total_tokens"], 12)
        self.assertNotIn("encrypted", json.dumps([result, metadata]))
        messages += [result, {"role": "tool", "tool_call_id": "call_test", "content": "synthetic result"}]
        self.transport.replies.append(self.stream([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "synthetic final"}]}]))
        final = client.complete(messages, tools)
        self.assertEqual(final["content"], "synthetic final")
        payload = json.loads(self.transport.calls[-1].data)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertEqual(payload["input"][0]["role"], "developer")
        self.assertIn(reasoning, payload["input"])
        self.assertEqual(payload["input"][-1]["call_id"], "call_test")
        self.assertEqual(payload["tools"][0]["type"], "namespace")
        for unsupported in ("temperature", "max_output_tokens", "previous_response_id"):
            self.assertNotIn(unsupported, payload)

    def test_partial_delta_late_limit_or_missing_terminal_never_succeeds(self):
        client = self.client()
        delta = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        failures = [delta, delta + b'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n\n',
                    delta + b'data: {"type":"response.incomplete"}\n\n']
        for stream in failures:
            self.transport.replies.append(stream)
            observed = []
            with self.assertRaises(ModelError):
                client.complete([{"role": "user", "content": "synthetic"}], [], observer=observed.append)
            self.assertEqual(observed[0]["status"], "failed")
            self.assertNotIn("partial", json.dumps(observed))

    def test_transport_failures_keep_safe_categories_without_retry_or_error_text(self):
        client = self.client()
        secret = "SYNTHETIC-PRIVATE-URL-AND-TOKEN"
        failures = [(URLError(ssl.SSLCertVerificationError(secret)), "tls_certificate_error"),
                    (URLError(ssl.SSLError(secret)), "tls_error"),
                    (TimeoutError(secret), "timeout"),
                    (URLError(socket.gaierror(secret)), "dns_error"),
                    (ConnectionResetError(secret), "connection_error"),
                    (URLError(secret), "network_error")]
        for failure, category in failures:
            with self.subTest(category=category):
                observed, before = [], len(self.transport.calls)
                self.transport.replies.append(failure)
                with self.assertRaises(ModelError) as caught:
                    client.complete([{"role": "user", "content": "synthetic"}], [], observer=observed.append)
                self.assertEqual(len(self.transport.calls), before + 1)
                self.assertEqual(caught.exception.code, "model_unavailable")
                self.assertEqual(caught.exception.failure_detail, category)
                self.assertEqual(observed[0]["failure_detail"], category)
                self.assertEqual(observed[0]["status"], "failed")
                self.assertNotIn(secret, json.dumps(observed) + str(caught.exception))
        self.transport.replies.append(URLError(TimeoutError(secret)))
        with self.assertRaises(ModelError) as caught:
            self.service._json(AUTH + "/synthetic")
        self.assertEqual(caught.exception.code, "chatgpt_unavailable")
        self.assertEqual(caught.exception.failure_detail, "timeout")
        self.assertNotIn(secret, str(caught.exception))

    def test_connection_time_consumes_stream_deadline_and_incomplete_body_is_discarded(self):
        client = self.client()
        client.timeout = 30
        now, timeouts, observed = [0], [], []
        wire = self.stream([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "synthetic"}]}])
        class SlowResponse(Response):
            def read1(self, size=-1):
                now[0] = 31
                return super().read1(size)
        def open_slow(request, timeout):
            timeouts.append(timeout)
            now[0] = 20
            return SlowResponse(wire)
        with patch("course_assistant.chatgpt_client.time.monotonic", side_effect=lambda: now[0]), patch.object(self.transport, "open", side_effect=open_slow):
            with self.assertRaises(ModelError) as caught:
                client.complete([], [], observer=observed.append)
        self.assertEqual(caught.exception.code, "model_unavailable")
        self.assertEqual(observed[0]["failure_detail"], "timeout")
        self.assertEqual(timeouts, [30])
        self.assertEqual(len(observed), 1)
        class BrokenResponse(Response):
            def read1(self, size=-1):
                raise IncompleteRead(b"SYNTHETIC-PRIVATE-PARTIAL")
        observed.clear()
        with patch.object(self.transport, "open", return_value=BrokenResponse(b"")) as transport:
            with self.assertRaises(ModelError) as caught:
                client.complete([], [], observer=observed.append)
        self.assertEqual(caught.exception.code, "model_stream_incomplete")
        self.assertEqual(observed[0]["failure_detail"], "connection_error")
        self.assertEqual(transport.call_count, 1)
        self.assertNotIn("SYNTHETIC-PRIVATE-PARTIAL", json.dumps(observed) + str(caught.exception))

    def test_unregistered_tool_and_wrong_namespace_fail_before_execution(self):
        client = self.client()
        for kind in [{"type": "computer_call"}, {"type": "function_call", "namespace": "shell", "name": "exec", "call_id": "c", "arguments": "{}"}]:
            self.transport.replies.append(self.stream([kind]))
            with self.assertRaises(ModelError):
                client.complete([{"role": "user", "content": "synthetic"}], [])

    def test_effort_changes_revision_and_is_checked_against_catalog(self):
        base = ModelClient({"DEEPSEEK_API_KEY": "SYNTHETIC"})
        router = ModelRouter(self.service, self.context, base)
        settings = ChatModelSettings(self.auth, lambda: base, resolver=lambda _: router)
        settings.update(self.context, {"model": "deepseek-flash", "thinking": True, "reasoning_effort": "max"})
        client, status, revision = settings.snapshot(self.context)
        self.assertEqual(client.reasoning_effort, "max")
        self.assertEqual(revision, 1)
        with self.assertRaises(AccessError):
            settings.update(self.context, {"model": "deepseek-flash", "thinking": True, "reasoning_effort": "medium"})
        self.assertEqual(settings.snapshot(self.context)[2], 1)

    def test_deepseek_effort_on_both_transports_and_off_omits_it(self):
        for effort in ("low", "high", "max"):
            client = ModelClient({"DEEPSEEK_API_KEY": "SYNTHETIC"}, opener=self.transport).with_options("deepseek-flash", True, effort)
            self.transport.replies.append({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "synthetic"}}]})
            client.complete([], [])
            self.assertEqual(json.loads(self.transport.calls[-1].data)["reasoning_effort"], effort)
        client = client.with_options("deepseek-flash", False, "max")
        self.transport.replies.append({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "synthetic"}}]})
        client.complete([], [])
        self.assertNotIn("reasoning_effort", json.loads(self.transport.calls[-1].data))

    def test_loopback_callback_validates_host_and_consumes_denial(self):
        from http.client import HTTPConnection
        from urllib.parse import urlencode
        params = self.begin()
        address = urlsplit(params["redirect_uri"])
        connection = HTTPConnection(address.hostname, address.port, timeout=5)
        query = urlencode({"state": params["state"], "error": "access_denied"})
        connection.request("GET", "/auth/callback?" + query, headers={"Host": "evil.invalid"})
        result = connection.getresponse()
        self.assertEqual(result.status, 400)
        result.read()
        self.assertTrue(self.service.status(self.context)["pending"])
        connection.close()
        connection = HTTPConnection(address.hostname, address.port, timeout=5)
        connection.request("GET", "/auth/callback?" + query)
        result = connection.getresponse()
        self.assertEqual(result.status, 400)
        self.assertEqual(result.getheader("Referrer-Policy"), "no-referrer")
        result.read()
        connection.close()
        self.assertFalse(self.service.status(self.context)["pending"])
        self.assertEqual(self.transport.calls, [])
