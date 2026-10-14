"""Loopback transport for a pinned official-browser connection.

The browser owns school cookies. This broker receives only stripped business
projections or one registered PDF. Pairing credentials exist only in memory;
they are local connection keys, never a school identity or AuthContext.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time

from .student_sources import sanitize_payload, StudentSourceError

OPERATIONS = frozenset({"academic", "major_academic", "timetable", "exams",
                        "plan_catalogue", "plan_document"})
STATES = frozenset({"business", "awaiting_user_login", "source_session_expired",
                    "captcha_required", "permission_denied", "source_unavailable",
                    "schema_changed", "route_not_confirmed", "user_confirmation_required",
                    "source_subject_changed"})
PLAN_FIELDS = frozenset({"plan_ref", "cohort", "college_name", "major_name",
                        "major_or_category_raw", "program", "study_path_raw",
                        "title", "source_observation_kind", "cohort_provenance"})
COHORT_PROVENANCE = frozenset({"unknown", "source_row", "confirmed_query_scope"})
SCOPE_FIELDS = PLAN_FIELDS - {"plan_ref", "title", "source_observation_kind", "cohort_provenance"}
EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")
EPOCH = re.compile(r"[A-Za-z0-9_-]{16,80}\Z")


class BrowserBridgeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_spec(spec):
    if (not isinstance(spec, dict) or set(spec) - {
            "operation", "target_term", "page", "page_size", "scope", "plan_ref"}
            or spec.get("operation") not in OPERATIONS):
        raise BrowserBridgeError("invalid_query")
    for key, upper in (("page", 3), ("page_size", 100)):
        value = spec.get(key, 1 if key == "page" else 20)
        if type(value) is not int or not 1 <= value <= upper:
            raise BrowserBridgeError("invalid_query")
    term = spec.get("target_term")
    if term is not None:
        matched = re.fullmatch(r"(20\d\d)-(20\d\d)-(AW|SS|A|W|S|SU)", term) if isinstance(term, str) else None
        if not matched or int(matched[2]) != int(matched[1]) + 1:
            raise BrowserBridgeError("invalid_query")
    if spec["operation"] in {"academic", "major_academic", "timetable", "exams"} and term is None:
        raise BrowserBridgeError("target_term_required")
    scope = spec.get("scope")
    if scope is not None:
        if not isinstance(scope, dict) or set(scope) - SCOPE_FIELDS:
            raise BrowserBridgeError("invalid_query")
        for key, value in scope.items():
            if value is None:
                continue
            if key == "cohort":
                if type(value) is not int or not 1900 <= value <= 2099:
                    raise BrowserBridgeError("invalid_query")
            elif not isinstance(value, str) or not 0 < len(value) <= 160:
                raise BrowserBridgeError("invalid_query")
        # Reuse the business string guard; no query can carry credential text.
        try:
            sanitize_payload("profile", scope)
        except StudentSourceError:
            raise BrowserBridgeError("invalid_query") from None
    if spec["operation"] == "plan_catalogue" and not scope:
        raise BrowserBridgeError("invalid_query")
    ref = spec.get("plan_ref")
    if ref is not None and (not isinstance(ref, str) or not re.fullmatch(r"PLAN-[A-Za-z0-9_-]{1,64}", ref)):
        raise BrowserBridgeError("invalid_query")
    if spec["operation"] == "plan_document" and ref is None:
        raise BrowserBridgeError("invalid_query")
    return deepcopy(spec)


def _project_response(response, operation, epoch):
    allowed = {"state", "http_status", "content_type", "session_epoch",
               "source_subject_id", "payload", "payload_base64", "capture_method", "page_validation"}
    if not isinstance(response, dict) or set(response) - allowed:
        raise BrowserBridgeError("invalid_response")
    if response.get("session_epoch") != epoch or response.get("state") not in STATES:
        raise BrowserBridgeError("source_session_expired")
    status = response.get("http_status")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise BrowserBridgeError("invalid_response")
    state = response["state"]
    if status == 401:
        state = "source_session_expired"
    elif status == 403:
        state = "permission_denied"
    media_type = response.get("content_type")
    if not isinstance(media_type, str) or media_type not in {
            "application/json", "application/pdf", "dom_business_projection", "text/html"}:
        media_type = None
    result = {"state": state, "http_status": status, "session_epoch": epoch,
              "content_type": media_type, "source_subject_id": None,
              "capture_method": "browser_http"}
    if state != "business":
        # Authentication HTML and all bodies on failure are discarded.
        return result
    native_catalogue = (operation == "plan_catalogue" and status is None
                        and response.get("capture_method") == "browser_dom"
                        and response.get("content_type") == "dom_business_projection"
                        and response.get("page_validation") == {
                            "status": "visible_business_page", "domain": "plan_catalogue"})
    if status != 200 and not native_catalogue:
        raise BrowserBridgeError("invalid_response")
    if native_catalogue:
        result["capture_method"] = "browser_dom"
        result["page_validation"] = deepcopy(response["page_validation"])
    elif "page_validation" in response:
        raise BrowserBridgeError("invalid_response")
    subject = response.get("source_subject_id")
    if subject is not None:
        try:
            safe, _ = sanitize_payload("profile", {"source_subject_id": subject})
        except StudentSourceError:
            raise BrowserBridgeError("invalid_response") from None
        if not isinstance(subject, str) or len(subject) > 160:
            raise BrowserBridgeError("invalid_response")
        result["source_subject_id"] = safe["source_subject_id"]
    if operation == "plan_document":
        if response.get("content_type") != "application/pdf" or "payload" in response:
            raise BrowserBridgeError("invalid_response")
        encoded = response.get("payload_base64")
        if not isinstance(encoded, str) or len(encoded) > 14 * 1024 * 1024:
            raise BrowserBridgeError("invalid_response")
        try:
            document = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise BrowserBridgeError("invalid_response") from None
        if len(document) > 10 * 1024 * 1024 or not document.startswith(b"%PDF-"):
            raise BrowserBridgeError("invalid_response")
        result["payload"] = document
        return result
    if (response.get("content_type") != "application/json" and not native_catalogue) or "payload_base64" in response:
        raise BrowserBridgeError("invalid_response")
    payload = response.get("payload")
    try:
        if operation == "plan_catalogue":
            if not isinstance(payload, dict) or set(payload) - {
                    "items", "pageNo", "currentPage", "pageSize", "showCount", "total", "totalCount", "totalResult"}:
                raise BrowserBridgeError("invalid_response")
            items = payload.get("items")
            if not isinstance(items, list) or len(items) > 100:
                raise BrowserBridgeError("invalid_response")
            cleaned = []
            for item in items:
                if not isinstance(item, dict) or set(item) - PLAN_FIELDS:
                    raise BrowserBridgeError("invalid_response")
                ref = item.get("plan_ref")
                validate_spec({"operation": "plan_document", "plan_ref": ref})
                profile = {k: v for k, v in item.items() if k in SCOPE_FIELDS}
                if any(value is not None and (type(value) not in (str, int)
                        or key != "cohort" and type(value) is not str)
                       for key, value in profile.items()):
                    raise BrowserBridgeError("invalid_response")
                if profile.get("cohort") is not None and (type(profile["cohort"]) is not int
                        or not 1900 <= profile["cohort"] <= 2099):
                    raise BrowserBridgeError("invalid_response")
                safe_profile, _ = sanitize_payload("profile", profile)
                title = item.get("title")
                if title is not None:
                    if not isinstance(title, str) or len(title) > 512:
                        raise BrowserBridgeError("invalid_response")
                    sanitize_payload("profile", {"major_name": title})
                if item.get("source_observation_kind") not in (None, "directory_observation"):
                    raise BrowserBridgeError("invalid_response")
                provenance = item.get("cohort_provenance", "unknown")
                if not isinstance(provenance, str) or provenance not in COHORT_PROVENANCE:
                    raise BrowserBridgeError("invalid_response")
                cleaned.append({**safe_profile, "plan_ref": ref, "title": title,
                                "cohort_provenance": provenance,
                                "source_observation_kind": "directory_observation"})
            counters = {}
            for key, value in payload.items():
                if key == "items":
                    continue
                if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
                    value = int(value)
                if value is not None and (type(value) is not int or not 0 <= value <= 100000):
                    raise BrowserBridgeError("invalid_response")
                counters[key] = value
            result["payload"] = {**counters, "items": cleaned}
        else:
            domain = "academic" if operation == "major_academic" else operation
            result["payload"], _ = sanitize_payload(domain, payload)
    except (StudentSourceError, TypeError, ValueError):
        raise BrowserBridgeError("invalid_response") from None
    return result


class BrowserBridgeTransport:
    """Serial jobs, bounded waits and a browser-pinned connection in memory."""
    def __init__(self, authorize, *, timeout=30, idle_timeout=20, clock=time.monotonic):
        self.authorize = authorize
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.clock = clock
        self._condition = threading.Condition()
        self._serial = threading.Lock()
        self._pair_code = secrets.token_urlsafe(24)
        self._token = None
        self._origin = None
        self._epoch = None
        self._paired_at = None
        self._paired_scope = None
        self._last_seen = None
        self._job = None
        self._response = None
        self._closed = False

    @property
    def session_epoch(self):
        with self._condition:
            if self._token:
                try:
                    self._connection()
                except BrowserBridgeError:
                    pass
            return self._epoch or "unpaired"

    def _auth(self):
        try:
            result = self.authorize()
        except Exception:
            with self._condition:
                self._clear()
            raise BrowserBridgeError("grant_denied") from None
        if not isinstance(result, dict) or not isinstance(result.get("scope_key"), str) or not result["scope_key"]:
            with self._condition:
                self._clear()
            raise BrowserBridgeError("grant_denied")
        with self._condition:
            if self._paired_scope is not None and result["scope_key"] != self._paired_scope:
                self._clear()
                raise BrowserBridgeError("source_scope_changed")
        return result["scope_key"]

    def _connection(self):
        if self._closed or not self._token:
            raise BrowserBridgeError("browser_not_connected")
        now = self.clock()
        if now - self._last_seen > self.idle_timeout or now - self._paired_at > 86400:
            self._clear()
            raise BrowserBridgeError("source_session_expired")

    def _clear(self):
        self._token = self._origin = self._epoch = None
        self._paired_scope = None
        self._pair_code = secrets.token_urlsafe(24)
        self._job = self._response = None
        self._condition.notify_all()

    def pair(self, code, epoch, origin):
        scope_key = self._auth()
        with self._condition:
            if (self._closed or self._token or not isinstance(code, str) or not code
                    or not secrets.compare_digest(code, self._pair_code)
                    or not isinstance(epoch, str) or not EPOCH.fullmatch(epoch)
                    or not EXTENSION_ORIGIN.fullmatch(origin)):
                raise BrowserBridgeError("pairing_denied")
            self._token = secrets.token_urlsafe(32)
            self._epoch, self._origin = epoch, origin
            self._paired_scope = scope_key
            self._paired_at = self._last_seen = self.clock()
            self._pair_code = ""
            self._condition.notify_all()
            return {"token": self._token, "session_epoch": self._epoch}

    def authenticate_connection(self, token, origin):
        self._auth()
        with self._condition:
            self._connection()
            if (origin != self._origin or not isinstance(token, str)
                    or not secrets.compare_digest(token, self._token)):
                raise BrowserBridgeError("connection_denied")
            self._last_seen = self.clock()

    def next_job(self, epoch):
        self._auth()
        with self._condition:
            self._connection()
            if epoch != self._epoch:
                raise BrowserBridgeError("source_session_expired")
            if not self._job or self._job["delivered"]:
                return {"job": None}
            self._job["delivered"] = True
            return {"job": {"id": self._job["id"], "spec": deepcopy(self._job["spec"])}}

    def heartbeat(self, epoch):
        self._auth()
        with self._condition:
            self._connection()
            if epoch != self._epoch:
                self._clear()
                raise BrowserBridgeError("source_session_expired")
            self._last_seen = self.clock()
            return {"alive": True}

    def disconnect(self, epoch):
        with self._condition:
            self._connection()
            if epoch != self._epoch:
                raise BrowserBridgeError("source_session_expired")
            self._clear()
            return {"disconnected": True}

    def submit(self, job_id, response):
        self._auth()
        with self._condition:
            self._connection()
            if not self._job or self._job["id"] != job_id or not self._job["delivered"] or self._response is not None:
                raise BrowserBridgeError("stale_result")
            if self.clock() >= self._job["deadline"]:
                self._job = None
                self._condition.notify_all()
                raise BrowserBridgeError("stale_result")
            clean = _project_response(response, self._job["spec"]["operation"], self._epoch)
            self._response = clean
            self._condition.notify_all()

    def request(self, spec):
        spec = validate_spec(spec)
        with self._serial:
            scope_key = self._auth()
            with self._condition:
                self._connection()
                epoch = self._epoch
                deadline = self.clock() + self.timeout
                self._job = {"id": secrets.token_hex(16), "spec": spec, "delivered": False,
                             "deadline": deadline}
                self._response = None
                while self._response is None:
                    remaining = deadline - self.clock()
                    if self._closed or self._epoch != epoch:
                        self._job = None
                        raise BrowserBridgeError("source_session_expired")
                    if remaining <= 0:
                        self._job = None
                        raise TimeoutError("source_unavailable")
                    self._condition.wait(min(remaining, .2))
                result = self._response
                self._job = self._response = None
                if self.clock() >= deadline:
                    raise TimeoutError("source_unavailable")
            if self._auth() != scope_key or self.session_epoch != epoch:
                raise BrowserBridgeError("source_scope_changed")
            return deepcopy(result)

    def wait_connected(self, timeout=60):
        deadline = self.clock() + timeout
        with self._condition:
            if self._token:
                try:
                    self._connection()
                except BrowserBridgeError:
                    pass
            while not self._token and not self._closed:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, .2))
            return bool(self._token)

    def describe(self):
        self.session_epoch
        with self._condition:
            return {"status": "connected" if self._token else "awaiting_browser_connection",
                    "pending_jobs": int(self._job is not None), "school_authentication": "not_configured"}

    def close(self):
        with self._condition:
            self._closed = True
            self._clear()


class BrowserBridgeServer:
    def __init__(self, transport, *, port=0):
        self.transport = transport
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _guard(self, *, extension=False):
                if self.headers.get("Host") != owner.host:
                    raise BrowserBridgeError("host_denied")
                origin = self.headers.get("Origin", "")
                if extension and not EXTENSION_ORIGIN.fullmatch(origin):
                    raise BrowserBridgeError("origin_denied")
                if not extension and origin and origin != owner.url:
                    raise BrowserBridgeError("origin_denied")
                return origin

            def _send(self, status, payload, *, content_type="application/json", origin=None):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                try:
                    self._guard()
                    if self.path == "/health":
                        self._send(200, owner.transport.describe())
                    elif self.path == "/pair":
                        code = html.escape(owner.transport._pair_code)
                        body = ("<!doctype html><meta charset=utf-8><title>学生来源本机连接</title>"
                                "<h1>连接官方教务会话</h1><p>在连接器中输入本机地址和本次连接码。"
                                "学校密码仍在官方页输入。</p><p>地址：" + html.escape(owner.url)
                                + "</p><p>连接码：<code>" + code + "</code></p>").encode()
                        self._send(200, body, content_type="text/html; charset=utf-8")
                    else:
                        self._send(404, {"error": "not_found"})
                except BrowserBridgeError as error:
                    self._send(403, {"error": error.code})

            def do_OPTIONS(self):
                try:
                    origin = self._guard(extension=True)
                    if self.path not in {"/pair", "/next", "/result", "/heartbeat", "/disconnect"}:
                        raise BrowserBridgeError("invalid_request")
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Methods", "POST")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except BrowserBridgeError as error:
                    self._send(403, {"error": error.code})

            def do_POST(self):
                origin = None
                try:
                    origin = self._guard(extension=True)
                    length = self.headers.get("Content-Length", "")
                    limit = 15 * 1024 * 1024 if self.path == "/result" else 4096
                    if not length.isdecimal() or not 0 < int(length) <= limit or self.headers.get("Transfer-Encoding"):
                        raise BrowserBridgeError("invalid_request")
                    payload = json.loads(self.rfile.read(int(length)))
                    if not isinstance(payload, dict):
                        raise BrowserBridgeError("invalid_request")
                    if self.path == "/pair" and set(payload) == {"code", "session_epoch"}:
                        result = owner.transport.pair(payload["code"], payload["session_epoch"], origin)
                    else:
                        header = self.headers.get("Authorization", "")
                        if not header.startswith("Bearer "):
                            raise BrowserBridgeError("connection_denied")
                        owner.transport.authenticate_connection(header[7:], origin)
                        if self.path == "/next" and set(payload) == {"session_epoch"}:
                            result = owner.transport.next_job(payload["session_epoch"])
                        elif self.path == "/heartbeat" and set(payload) == {"session_epoch"}:
                            result = owner.transport.heartbeat(payload["session_epoch"])
                        elif self.path == "/disconnect" and set(payload) == {"session_epoch"}:
                            result = owner.transport.disconnect(payload["session_epoch"])
                        elif self.path == "/result" and set(payload) == {"id", "response"}:
                            owner.transport.submit(payload["id"], payload["response"])
                            result = {"accepted": True}
                        else:
                            raise BrowserBridgeError("invalid_request")
                    self._send(200, result, origin=origin)
                except (BrowserBridgeError, StudentSourceError) as error:
                    self._send(403, {"error": error.code}, origin=origin)
                except (ValueError, UnicodeError, TypeError, KeyError):
                    self._send(400, {"error": "invalid_request"}, origin=origin)

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self.host = f"127.0.0.1:{self._server.server_port}"
        self.url = "http://" + self.host
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self):
        self.transport.close()
        if self._thread:
            self._server.shutdown()
            self._thread.join(timeout=2)
        self._server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
