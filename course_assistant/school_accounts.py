"""Personal school HTTP connections owned by live local application sessions.

Passwords are transient call arguments. School cookies, pending captcha state
and connections remain in memory. Only stripped business projections and plan
PDFs enter the owner-scoped local private store.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import base64
import json
import os
import re
import secrets
import stat
import threading
import time

from .auth import AccessError, SCHOOL_MODE, SCHOOL_REALM
from .student_source_client import DOMAINS, StudentSourceClient, SourceClientError
from .student_source_parsers import PARSER_VERSION, MAPPING_VERSION, parse_payload
from .student_sources import CREDENTIAL_VALUE, _observed_subjects, digest, sanitize_payload
from .school_period_times import period_source
from .student_plan_acquisition import OnDemandPlanService, StudentPlanAcquisitionError


LOGIN_FAILURE_STATES = frozenset({"source_session_expired", "interactive_verification_required", "route_not_confirmed",
                                 "source_unavailable", "schema_changed", "permission_denied", "invalid_credentials",
                                 "identity_not_observed", "school_identity_unverified"})
LOGIN_FAILURE_STEPS = frozenset({"cas_form", "cas_captcha_status", "cas_pubkey", "cas_submit", "service_ticket",
                                "service_callback", "menu_context", "own_profile", "login_captcha"})


def _error(reason, status=400):
    messages = {"school_identity_unverified": "学校本人身份未确认", "source_subject_changed": "学校账号发生变化，请重新登录",
                "school_connection_expired": "学校连接已结束，请重新登录", "source_session_expired": "学校会话已失效，请重新登录",
                "captcha_required": "请填写学校验证码", "interactive_verification_required": "学校要求完成交互验证后重新登录",
                "invalid_school_request": "学校数据请求格式无效", "invalid_login_challenge": "登录验证已失效，请重新登录",
                "source_unavailable": "学校服务暂时不可用", "schema_changed": "学校响应结构尚未适配",
                "route_not_confirmed": "学校接口尚未确认", "permission_denied": "学校接口拒绝访问"}
    raise AccessError(messages.get(reason, "学校数据暂时不可用"), status, reason)


def _private_directory(path):
    path = Path(path).expanduser().absolute()
    if (path.resolve() != path or not ({"private", "local-only"} & set(path.parts[2:]))):
        _error("invalid_private_store", 503)
    missing, parent = [], path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        _error("invalid_private_store", 503)
    return path


def _safe_profile(result):
    if not isinstance(result, dict) or result.get("state") != "authenticated" or not isinstance(result.get("profile"), dict):
        _error("school_identity_unverified", 403)
    try:
        projection, _ = sanitize_payload("profile", result["profile"])
        parsed = parse_payload("profile", projection)
        profile = parsed["records"][0] if len(parsed.get("records", [])) == 1 else {}
        subject = profile.get("source_subject_id")
        if (not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", subject)
                or profile.get("field_states", {}).get("source_subject_id") != "known"
                or len(_observed_subjects(projection)) != 1):
            _error("school_identity_unverified", 403)
        return projection, profile
    except AccessError:
        raise
    except Exception:
        _error("school_identity_unverified", 403)


@dataclass
class _Connection:
    client: object
    owner: str
    profile: dict
    generation: str = field(default_factory=lambda: secrets.token_hex(16))
    state: str = "connected"
    source: object = None
    plan: object = None
    personal: object = None
    personal_epoch: object = None
    pending: dict | None = None
    captcha_id: str | None = None
    captcha_expires: float = 0
    lock: object = field(default_factory=threading.RLock)


class _BoundTransport:
    def __init__(self, connection):
        self.connection = connection

    @property
    def session_epoch(self):
        return digest({"connection": self.connection.generation, "epoch": self.connection.client.session_epoch})

    def request(self, spec):
        response = self.connection.client.request(spec)
        if not isinstance(response, dict):
            return response
        response = deepcopy(response)
        if response.get("session_epoch") != self.connection.client.session_epoch:
            return {"state": "source_session_expired", "session_epoch": self.session_epoch}
        if response.get("state") == "business":
            subjects = _observed_subjects(response["payload"]) if isinstance(response.get("payload"), dict) else set()
            explicit = response.get("source_subject_id")
            if explicit is not None:
                subjects.add(digest(str(explicit).strip()))
            if subjects and subjects != {digest(self.connection.owner)}:
                return {"state": "source_subject_changed", "session_epoch": self.session_epoch}
        # The app connection epoch also changes when a new client replaces an
        # old one, even if the new HTTP client starts its own counter at zero.
        response["session_epoch"] = self.session_epoch
        return response


class SchoolAccountService:
    def __init__(self, auth, client_factory, *, root=None, clock=time.time, personal_pdf_extractor=None):
        if not callable(client_factory):
            _error("invalid_school_request")
        self.auth, self.client_factory, self.clock = auth, client_factory, clock
        self.personal_pdf_extractor = personal_pdf_extractor
        self.root = _private_directory(root or auth.store.path.parent / "private" / "school_accounts")
        if auth.store.path.is_symlink():
            _error("invalid_private_store", 503)
        os.chmod(auth.store.path, 0o600)
        self._sessions, self._login_challenges = {}, {}
        self._lock = threading.RLock()
        auth.school_session_validator = self._session_exists

    def _session_exists(self, session_ref, owner):
        with self._lock:
            connection = self._sessions.get(session_ref)
            return connection is not None and connection.owner == owner

    def _connection(self, context):
        self.auth.require_context(context)
        if context.auth_mode != SCHOOL_MODE or context.security_realm != SCHOOL_REALM:
            _error("school_connection_required", 403)
        with self._lock:
            connection = self._sessions.get(context.session_ref)
        if connection is None or connection.owner != context.student_id:
            _error("school_connection_expired", 401)
        return connection

    def _authorize(self, context, connection):
        if self._connection(context) is not connection:
            _error("source_session_expired", 409)
        return {"scope_key": digest({"owner": connection.owner, "realm": SCHOOL_REALM,
                                      "app_session": context.session_ref, "connection": connection.generation})}

    def _build(self, context, connection):
        authorize = lambda: self._authorize(context, connection)
        connection.source = StudentSourceClient(_BoundTransport(connection), authorize, clock=self.clock)

    @staticmethod
    def _close_client(client):
        try:
            client.close()
        except Exception:
            pass

    @staticmethod
    def _credentials(body):
        if (not isinstance(body, dict) or set(body) - {"username", "password", "challenge_id", "captcha_text"}
                or not isinstance(body.get("username"), str) or not 1 <= len(body["username"]) <= 100
                or not isinstance(body.get("password"), str) or not 1 <= len(body["password"]) <= 1024):
            _error("invalid_school_request")
        return body["username"], body["password"]

    @staticmethod
    def _login_diagnostics(result):
        source = result.get("diagnostics") if isinstance(result, dict) else None
        source = source if isinstance(source, dict) else {}
        step, status, transport = (source.get(key) for key in ("failure_step", "http_status", "transport_error"))
        return {"failure_step": step if isinstance(step, str) and step in LOGIN_FAILURE_STEPS else None,
                "http_status": status if type(status) is int and 100 <= status <= 999 else None,
                "transport_error": transport if isinstance(transport, str) and transport in {"timeout", "tls", "network"} else None}

    def _record_login_failure(self, code, result=None):
        code = code if isinstance(code, str) and code in LOGIN_FAILURE_STATES else "source_unavailable"
        diagnostics = self._login_diagnostics(result)
        reason = "|".join(("code=" + code, "phase=" + (diagnostics["failure_step"] or "unknown"),
                           "status=" + (str(diagnostics["http_status"]) if diagnostics["http_status"] is not None else "none"),
                           "transport=" + (diagnostics["transport_error"] or "none")))
        self.auth.store.event("anonymous-school", "school_login", "deny", reason)
        return diagnostics

    def _login(self, body):
        username, password = self._credentials(body)
        with self._lock:
            expired = [key for key, (_, expiry) in self._login_challenges.items() if self.clock() >= expiry]
            for key in expired:
                self._close_client(self._login_challenges.pop(key)[0])
            if len(self._login_challenges) >= 16:
                self._record_login_failure("source_unavailable")
                _error("source_unavailable", 503)
        challenge_id = body.get("challenge_id")
        if challenge_id is None:
            if "captcha_text" in body:
                _error("invalid_login_challenge")
            try:
                client = self.client_factory()
            except Exception:
                self._record_login_failure("source_unavailable")
                _error("source_unavailable", 503)
        else:
            if not isinstance(challenge_id, str):
                _error("invalid_login_challenge")
            with self._lock:
                challenge = self._login_challenges.pop(challenge_id, None)
            if challenge is None or self.clock() >= challenge[1]:
                if challenge:
                    self._close_client(challenge[0])
                _error("invalid_login_challenge")
            client = challenge[0]
            text = body.get("captcha_text")
            if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9]{1,16}", text):
                self._close_client(client)
                _error("invalid_school_request")
            try:
                submitted = client.submit_captcha(text)
                if not isinstance(submitted, dict) or submitted.get("state") != "captcha_submitted":
                    self._close_client(client)
                    self._record_login_failure("interactive_verification_required")
                    _error("interactive_verification_required")
            except AccessError:
                raise
            except Exception:
                self._close_client(client)
                self._record_login_failure("source_unavailable")
                _error("source_unavailable", 503)
        try:
            result = client.login(username, password)
        except Exception:
            self._close_client(client)
            self._record_login_failure("source_unavailable")
            _error("source_unavailable", 503)
        finally:
            # No callback, pending record or service attribute retains them.
            username = password = None
        if not isinstance(result, dict):
            self._close_client(client)
            self._record_login_failure("schema_changed")
            _error("schema_changed", 502)
        if result.get("state") == "authenticated":
            try:
                projection, profile = _safe_profile(result)
            except Exception:
                self._close_client(client)
                self._record_login_failure("school_identity_unverified", {"diagnostics": {"failure_step": "own_profile"}})
                raise
            return client, projection, profile, None
        state = result.get("state")
        diagnostic_result = result
        if isinstance(state, str) and state in {"captcha_required", "interactive_verification_required"}:
            try:
                captcha_result = client.get_captcha()
                captcha = self._captcha_image(captcha_result)
                if captcha is None and isinstance(captcha_result, dict) and isinstance(captcha_result.get("diagnostics"), dict):
                    diagnostic_result = captcha_result
            except Exception:
                captcha = None
            if captcha:
                nonce = secrets.token_urlsafe(24)
                with self._lock:
                    self._login_challenges[nonce] = (client, self.clock() + 300)
                return None, None, None, {"authenticated": False, "state": "captcha_required", "challenge_id": nonce,
                                          "captcha": {"content_type": captcha["content_type"],
                                                      "data_base64": base64.b64encode(captcha["image"]).decode("ascii")}}
        self._close_client(client)
        state = state if isinstance(state, str) and state in LOGIN_FAILURE_STATES else "source_unavailable"
        diagnostics = self._record_login_failure(state, diagnostic_result)
        return None, None, None, {"authenticated": False, "state": state, "school_auth_status": "not_connected",
                                  "diagnostics": diagnostics}

    def login(self, body, previous_token=None):
        client, projection, profile, failure = self._login(body)
        if failure is not None:
            return None, failure
        connection = _Connection(client, profile["source_subject_id"], projection)
        previous_ref = self.auth.token_hash(previous_token) if isinstance(previous_token, str) and previous_token else None
        def attach(context):
            with self._lock:
                self._sessions[context.session_ref] = connection
            self._build(context, connection)
        try:
            token, result = self.auth.login_school({"source_subject_id": connection.owner, "name": profile.get("name") or ""},
                                                  previous_token, attach=attach)
            context = self.auth.authenticate(token)
            self._capture(context, "profile", None, [projection])
        except Exception:
            self._close_client(client)
            with self._lock:
                for ref, item in list(self._sessions.items()):
                    if item is connection:
                        self._sessions.pop(ref)
            raise
        if previous_ref:
            with self._lock:
                previous = self._sessions.pop(previous_ref, None)
            if previous:
                self._close_client(previous.client)
        return token, {**result, "profile": profile, "personal_data_source_status": "connected"}

    def status(self, context):
        connection = self._connection(context)
        profile = parse_payload("profile", connection.profile)["records"][0]
        cohort_state = profile["field_states"]["cohort"]
        return {"school_client_enabled": True, "school_auth_status": "personal_client_authenticated" if connection.state == "connected" else "personal_client_session_unavailable",
                "project_sso_status": "not_configured", "personal_data_source_status": connection.state,
                "data_kind": "personal_source", "security_realm": SCHOOL_REALM,
                "operations": ["profile", "academic", "major_academic", "timetable", "exams", "plan"],
                "query_context": {"cohort": profile["cohort"] if cohort_state == "known" else None,
                                  "cohort_state": cohort_state},
                "recommendation_status": "input_not_ready_for_recommendation"}

    def _capture(self, context, operation, term, payloads):
        connection = self._connection(context)
        self._authorize(context, connection)
        owner_path = _private_directory(self.root / digest({"realm": SCHOOL_REALM, "owner": connection.owner}))
        directory = _private_directory(owner_path / "sources")
        document = {"schema": "personal-school-source/1", "operation": operation, "target_term": term,
                    "source_origin": "https://zdbk.zju.edu.cn", "parser_version": PARSER_VERSION,
                    "owner_student_id": connection.owner, "payloads": payloads}
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        content_hash = sha256(raw).hexdigest()
        path = directory / (content_hash + ".json")
        self._authorize(context, connection)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
        except FileExistsError:
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or path.read_bytes() != raw:
                _error("invalid_private_store", 503)
        except OSError:
            _error("invalid_private_store", 503)
        self._authorize(context, connection)
        return "SOURCE-" + content_hash[:24]

    def profile(self, context):
        connection = self._connection(context)
        with connection.lock:
            try:
                result = connection.client.get_profile()
                if not isinstance(result, dict) or result.get("state") != "authenticated":
                    state = result.get("state") if isinstance(result, dict) else "schema_changed"
                    connection.state = state if state in {"source_session_expired", "captcha_required", "permission_denied"} else "source_unavailable"
                    _error(connection.state, 409 if connection.state == "source_session_expired" else 403)
                projection, profile = _safe_profile(result)
            except AccessError:
                raise
            except Exception:
                _error("source_unavailable", 503)
            self._authorize(context, connection)
            if profile["source_subject_id"] != connection.owner:
                connection.state = "source_subject_changed"
                connection.pending = None
                self._close_client(connection.client)
                _error("source_subject_changed", 403)
            connection.profile = projection
            alias = self._capture(context, "profile", None, [projection])
            return {"status": "complete_within_scope", "data_kind": "personal_source", "data_source": "live",
                    "profile": profile, "source_alias": alias, "captured_at": self.clock()}

    @staticmethod
    def _request_body(body):
        if (not isinstance(body, dict) or set(body) - {"operation", "target_term", "force_refresh"}
                or body.get("operation") not in DOMAINS or type(body.get("force_refresh", False)) is not bool
                or not isinstance(body.get("target_term"), str)):
            _error("invalid_school_request")
        if body["operation"] == "major_academic":
            valid_term = body["target_term"] == "ALL-HISTORY"
        else:
            valid_term = bool(re.fullmatch(r"(?:19|20|21)[0-9]{2}-(?:19|20|21)[0-9]{2}-(?:AW|SS|A|W|S|SU)", body["target_term"]))
            if valid_term:
                year_a, year_b, _ = body["target_term"].split("-")
                valid_term = int(year_b) == int(year_a) + 1
        if not valid_term:
            _error("invalid_school_request")
        return {"operation": body["operation"], "target_term": body["target_term"],
                "force_refresh": body.get("force_refresh", False)}

    def fetch(self, context, body):
        request = self._request_body(body)
        connection = self._connection(context)
        with connection.lock:
            before = connection.client.session_epoch
            try:
                result = connection.source.fetch(**request)
            except AccessError:
                raise
            except Exception:
                _error("source_unavailable", 503)
            # A password-free HTTP-session refresh may advance its epoch. Trust
            # the new one only after checking the authenticated profile again.
            if result["status"] == "source_session_expired" and connection.client.session_epoch != before:
                projection, profile = _safe_profile(connection.client.get_profile())
                if profile["source_subject_id"] != connection.owner:
                    connection.state = "source_subject_changed"
                    connection.pending = None
                    self._close_client(connection.client)
                    _error("source_subject_changed", 403)
                self._authorize(context, connection)
                connection.profile = projection
                self._build(context, connection)
                result = connection.source.fetch(**{**request, "force_refresh": True})
            self._authorize(context, connection)
            state = result["status"]
            connection.state = "connected" if result["data_source"] == "live" else state
            connection.pending = deepcopy(request) if state in {"captcha_required", "source_session_expired", "awaiting_user_login"} else None
            if state == "source_subject_changed":
                self._close_client(connection.client)
            records, issues, failures = [], [], []
            period_metadata = {"period_times": [], "period_times_source": period_source()}
            payloads = []
            for page in result.get("pages", []):
                payload = page["payload"]
                parsed = parse_payload(DOMAINS[request["operation"]], payload, {"target_term": request["target_term"]})
                if request["operation"] == "timetable":
                    period_metadata = {key: parsed[key] for key in ("period_times", "period_times_source")}
                records.extend(parsed["records"])
                issues.extend(parsed["issues"])
                failures.extend(parsed["row_failures"])
                payloads.append(payload)
            alias = self._capture(context, request["operation"], request["target_term"], payloads) if payloads and result["data_source"] == "live" else None
            public = {key: value for key, value in {
                "status": state, "data_kind": "personal_source", "security_realm": SCHOOL_REALM,
                "operation": request["operation"], "target_term": request["target_term"], "data_source": result["data_source"],
                "records": records, "issues": issues, "row_failures": failures, "coverage": result["coverage"],
                **(period_metadata if request["operation"] == "timetable" else {}),
                "source_field_diagnostics": [page["response"]["source_field_diagnostics"]
                                             for page in result.get("pages", [])
                                             if "source_field_diagnostics" in page.get("response", {})],
                "captured_at": result["captured_at"], "checked_at": result["checked_at"],
                "cache_captured_at": result["cache_captured_at"], "failure": result["failure"], "source_alias": alias}.items()}
            # Register a separate observation, rather than treating the content
            # hash of a stripped source projection as a capture timestamp.
            if result.get("pages") and connection.state not in self._LOCAL_BLOCKED:
                from .personal_observations import PersonalObservationError
                from .personal_evidence import PersonalEvidenceError
                epoch = connection.client.session_epoch
                try:
                    store, _ = self._personal_services(context, connection)
                    observation = store.register(public, parser_version=PARSER_VERSION, mapping_version=MAPPING_VERSION)
                    public["observation_id"] = observation["observation_id"]
                except (PersonalObservationError, PersonalEvidenceError, AccessError) as error:
                    self._authorize_local(context, connection, epoch)
                    code = getattr(error, "code", getattr(error, "reason", None))
                    if code not in {"invalid_private_store", "private_store_unavailable", "invalid_private_path",
                                    "private_permissions_required", "observation_store_limit", "invalid_observation_result"}:
                        raise
                    # The school read remains usable if a new local registry
                    # cannot be written. It must never acquire a fake receipt.
                    public["observation_registration"] = {"status": "failed", "code": code}
                except OSError:
                    self._authorize_local(context, connection, epoch)
                    public["observation_registration"] = {"status": "failed", "code": "private_store_unavailable"}
                self._authorize_local(context, connection, epoch)
            return public

    _LOCAL_BLOCKED = frozenset({"source_subject_changed", "permission_denied", "source_session_expired",
                                "awaiting_user_login", "captcha_required"})

    def _authorize_local(self, context, connection, epoch):
        scope = self._authorize(context, connection)
        if connection.state in self._LOCAL_BLOCKED or connection.client.session_epoch != epoch:
            _error("source_session_expired", 409)
        return scope

    @staticmethod
    def _local_error(error):
        code = getattr(error, "code", "local_query_unavailable")
        # Module errors carry only controlled codes. Never send exception text,
        # private paths, PDF parser output or an extracted document in errors.
        raise AccessError("本机资料查询暂不可用，请检查来源状态后重试", 409, code)

    def _personal_services(self, context, connection):
        from .personal_observations import PersonalObservationStore
        from .personal_evidence import PersonalEvidenceService
        self._authorize(context, connection)
        epoch = connection.client.session_epoch
        if connection.state in self._LOCAL_BLOCKED:
            _error(connection.state, 409)
        if connection.personal is not None and connection.personal_epoch == epoch:
            return connection.personal
        def authorize():
            scope = self._authorize_local(context, connection, epoch)
            return {"scope_key": digest({"connection": scope["scope_key"], "http_epoch": epoch}),
                    "owner_student_id": connection.owner, "security_realm": SCHOOL_REALM}
        scope = authorize()
        owner_path = _private_directory(self.root / digest({"realm": SCHOOL_REALM, "owner": connection.owner}))
        # A new authenticated generation gets a separate registry. Same bytes
        # under a new session cannot revive an old observation or citation.
        directory = _private_directory(owner_path / "personal" / digest(scope))
        def describe_document():
            authorize()
            profile = parse_payload("profile", connection.profile)["records"][0]
            result = self._plan_service(context, connection).describe(profile)
            authorize()
            if result.get("status") != "acquired_pending_review":
                raise StudentPlanAcquisitionError("source_unavailable")
            return result
        store = PersonalObservationStore(directory / "observations", authorize)
        evidence = PersonalEvidenceService(directory / "evidence", authorize,
            lambda alias: self.read_plan_document(context, alias), describe_document,
            extractor=self.personal_pdf_extractor)
        connection.personal, connection.personal_epoch = (store, evidence), epoch
        return connection.personal

    def local_chat_status(self, context):
        connection = self._connection(context)
        epoch = connection.client.session_epoch
        base = {"configured": False, "available": False, "remote": False,
                "reason": "real_recommendation_not_ready"}
        if connection.state in self._LOCAL_BLOCKED:
            local = {"available": False, "reason": connection.state,
                     "observations": {"snapshots": []}, "evidence": {"ready": False, "status": "source_unavailable"},
                     "plan": {"status": "source_unavailable"}}
        else:
            from .personal_observations import PersonalObservationError
            from .personal_evidence import PersonalEvidenceError
            try:
                with connection.lock:
                    store, evidence = self._personal_services(context, connection)
                    plan_service = self._plan_service(context, connection)
                    profile = parse_payload("profile", connection.profile)["records"][0]
                plan = plan_service.describe(profile)
                evidence_status = (evidence.describe() if plan.get("status") == "acquired_pending_review" else
                    {"ready": False, "status": "not_prepared", "reason": plan.get("status", "plan_missing")})
                local = {"available": True, "observations": store.describe(), "evidence": evidence_status, "plan": plan}
            except (PersonalObservationError, PersonalEvidenceError, StudentPlanAcquisitionError, OSError) as error:
                self._local_error(error)
            self._authorize_local(context, connection, epoch)
        self._authorize(context, connection)
        if connection.client.session_epoch != epoch:
            _error("source_session_expired", 409)
        return {**base, "local": local, "capabilities": {"local_records": local["available"],
                "local_evidence": local["available"] and local["evidence"]["ready"],
                "model_dialogue": False, "real_recommendation": False}}

    def local_query(self, context, path, body):
        from .personal_observations import PersonalObservationError
        from .personal_evidence import PersonalEvidenceError
        connection = self._connection(context)
        # Only obtain the services under the short connection lock. Extraction
        # runs without the global app lock, so logout can revoke a pending job.
        try:
            with connection.lock:
                store, evidence = self._personal_services(context, connection)
                epoch = connection.client.session_epoch
            if path == "/api/personal/records":
                result = store.query(body)
            elif path == "/api/evidence/prepare" and set(body) == {"source_alias"}:
                result = evidence.prepare(body["source_alias"])
            elif (path == "/api/evidence/search" and set(body) <= {"query", "limit"} and "query" in body
                  and type(body.get("limit", 5)) is int and 1 <= body.get("limit", 5) <= 5):
                result = evidence.search(body["query"], limit=body.get("limit", 5))
            elif path == "/api/evidence/resolve" and set(body) == {"reference"}:
                result = evidence.resolve(body["reference"])
            else:
                _error("invalid_school_request")
            if path.startswith("/api/evidence/"):
                current = evidence.describe()
                if not current["ready"] or current["versions"] != result.get("versions"):
                    raise PersonalEvidenceError("stale_input")
        except (PersonalObservationError, PersonalEvidenceError, StudentPlanAcquisitionError, OSError) as error:
            self._local_error(error)
        self._authorize_local(context, connection, epoch)
        return result

    def personal_chat_scope(self, context):
        """Server-only generation and input versions; no model identity claim."""
        connection = self._connection(context)
        epoch = connection.client.session_epoch
        scope = self._authorize_local(context, connection, epoch)
        status = self.local_chat_status(context)["local"]
        plan = status["plan"]
        versions = {"observations": [{"observation_id": item["observation_id"], "versions": item["versions"]}
                    for item in status["observations"]["snapshots"]],
                    "plan": {key: plan.get(key) for key in ("source_alias", "sha256", "status", "mapping_status", "binding_status", "rules_status")},
                    "profile": digest(connection.profile),
                    "parser": PARSER_VERSION, "mapping": MAPPING_VERSION}
        self._authorize_local(context, connection, epoch)
        return {"scope_key": digest({"connection": scope["scope_key"], "http_epoch": epoch}), "versions": versions}

    def personal_chat_identity_scope(self, context):
        """Authorization generation only, so progress need not wait for I/O."""
        connection = self._connection(context)
        epoch = connection.client.session_epoch
        scope = self._authorize_local(context, connection, epoch)
        return {"scope_key": digest({"connection": scope["scope_key"], "http_epoch": epoch})}

    def prepare_personal_chat(self, context, question, *, before, authorize, emit, adopt):
        from .personal_acquisition import prepare_sources
        connection = self._connection(context)
        # Serialize this question's source additions with other reads. Logout
        # revokes the AuthManager session without waiting for this lock.
        with connection.lock:
            before()
            result = prepare_sources(self, context, question, authorize=authorize, emit=emit)
            adopt(bool(result))
            return result

    def personal_chat_tool(self, context, name, args):
        """Internal tools locate resources through the authenticated connection.

        The model never supplies an owner, source path, arbitrary URL or SQL.
        Preparing an already permitted PDF is local; tools do not acquire new
        school data or perform enrollment actions.
        """
        connection = self._connection(context)
        epoch = connection.client.session_epoch
        self._authorize_local(context, connection, epoch)
        if name == "get_my_context":
            local = self.local_chat_status(context)["local"]
            profile = parse_payload("profile", connection.profile)["records"][0]
            result = {"status": "available", "data_kind": "personal_source",
                      "cohort": profile.get("cohort"), "major_name": profile.get("major_name"),
                      "operations": [{"operation": item["operation"], "target_term": item["target_term"],
                                      "coverage": item["coverage"], "captured_at": item["captured_at"]}
                                     for item in local["observations"]["snapshots"][:20]],
                      "capabilities": {"curriculum_available": local["plan"].get("status") == "acquired_pending_review",
                                       "read_only_acquisition": True,
                                       "real_recommendation": False},
                      "note": "按当前登录本人范围查询；服务可按已提交问题准备必要的本人只读来源。来源是观察记录，培养适用及规则待审核。"}
        elif name == "query_my_records":
            request = {key: value for key, value in args.items() if key in {"operation", "query", "academic_year", "limit"}}
            local = self.local_chat_status(context)["local"]
            snapshots = [item for item in local["observations"]["snapshots"] if item["operation"] == request.get("operation")]
            if args.get("target_term") is not None:
                snapshots = [item for item in snapshots if item["target_term"] == args["target_term"]]
            if not snapshots:
                result = {"status": "not_ready", "operation": request.get("operation"), "records": [],
                          "coverage": {}, "reason": "no_observation_in_scope", "data_kind": "personal_source"}
            else:
                request["observation_id"] = snapshots[0]["observation_id"]
                result = self.local_query(context, "/api/personal/records", request)
        elif name == "search_my_curriculum":
            local = self.local_chat_status(context)["local"]
            if local["plan"].get("status") != "acquired_pending_review":
                result = {"status": "not_ready", "matches": [], "reason": "curriculum_not_acquired",
                          "data_kind": "personal_source", "answer_support_verified": False}
            else:
                if not local["evidence"].get("ready"):
                    self.local_query(context, "/api/evidence/prepare", {"source_alias": local["plan"]["source_alias"]})
                result = self.local_query(context, "/api/evidence/search", args)
        else:
            _error("invalid_school_request")
        self._authorize_local(context, connection, epoch)
        return result

    @staticmethod
    def _captcha_image(result):
        if (not isinstance(result, dict) or result.get("state") != "captcha_ready"
                or result.get("content_type") not in {"image/png", "image/jpeg", "image/gif"}
                or not isinstance(result.get("image"), bytes) or not 0 < len(result["image"]) <= 2 * 1024 * 1024):
            return None
        signature = {"image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",),
                     "image/gif": (b"GIF87a", b"GIF89a")}[result["content_type"]]
        if not result["image"].startswith(signature):
            return None
        return {"state": "captcha_ready", "content_type": result["content_type"], "image": result["image"]}

    def captcha(self, context):
        connection = self._connection(context)
        with connection.lock:
            if connection.state != "captcha_required" or connection.pending is None:
                _error("captcha_not_pending")
            try:
                image = self._captcha_image(connection.client.get_captcha())
            except Exception:
                image = None
            self._authorize(context, connection)
            if image is None:
                _error("interactive_verification_required")
            connection.captcha_id = secrets.token_urlsafe(24)
            connection.captcha_expires = self.clock() + 300
            return {**image, "challenge_id": connection.captcha_id}

    def submit_captcha(self, context, body):
        connection = self._connection(context)
        if (not isinstance(body, dict) or set(body) - {"text", "challenge_id"}
                or not isinstance(body.get("text"), str) or not re.fullmatch(r"[A-Za-z0-9]{1,16}", body["text"])):
            _error("invalid_school_request")
        with connection.lock:
            if (connection.state != "captcha_required" or connection.pending is None or connection.captcha_id is None
                    or self.clock() >= connection.captcha_expires or
                    "challenge_id" in body and body["challenge_id"] != connection.captcha_id):
                _error("invalid_login_challenge")
            connection.captcha_id = None
            try:
                result = connection.client.submit_captcha(body["text"])
            except Exception:
                _error("source_unavailable", 503)
            self._authorize(context, connection)
            if not isinstance(result, dict) or result.get("state") != "captcha_submitted":
                return {"state": "captcha_required", "status": "captcha_required", "records": []}
            pending = deepcopy(connection.pending)
            connection.pending = None
            return self.fetch(context, {**pending, "force_refresh": True})

    def reauthenticate(self, context, body):
        old = self._connection(context)
        client, failure, installed, retained_challenge = None, None, False, False
        try:
            client, projection, profile, failure = self._login(body)
            with old.lock:
                # Publication and logout use the same lock: logout must close
                # whichever client is current when it revokes the app session.
                with self._lock:
                    self._authorize(context, old)
                    if failure is not None:
                        retained_challenge = True
                        return failure
                    if profile["source_subject_id"] != old.owner:
                        _error("source_subject_changed", 403)
                    connection = _Connection(client, old.owner, projection)
                    pending = deepcopy(old.pending)
                    self._build(context, connection)
                    self._sessions[context.session_ref] = connection
                    installed = True
                self._close_client(old.client)
            if pending is not None:
                return {"authenticated": True, "state": "authenticated", "retry": self.fetch(context, {**pending, "force_refresh": True})}
            return {"authenticated": True, "state": "authenticated", "personal_data_source_status": "connected"}
        finally:
            if not installed:
                if client is not None:
                    self._close_client(client)
                if failure is not None and not retained_challenge:
                    with self._lock:
                        challenge = self._login_challenges.pop(failure.get("challenge_id"), None)
                    if challenge is not None:
                        self._close_client(challenge[0])

    def _plan_service(self, context, connection):
        if connection.plan is not None:
            return connection.plan
        def capture(operation, **kwargs):
            result = connection.source.fetch(operation, force_refresh=True, **kwargs)
            if result.get("failure") or result.get("data_source") != "live" or not result.get("pages"):
                if result.get("status") in {"source_subject_changed", "permission_denied", "source_session_expired",
                                            "awaiting_user_login", "captcha_required"}:
                    connection.state = result["status"]
                raise StudentPlanAcquisitionError(result.get("failure") or "source_unavailable")
            return result
        def catalogue(scope):
            result = capture("plan_catalogue", scope=scope)
            rows = [row for page in result["pages"] for row in page["payload"].get("items", [])]
            total = result["coverage"]["reported_total"]
            if total is not None and total != len(rows):
                raise StudentPlanAcquisitionError("invalid_plan_catalogue")
            return rows
        def document(ref):
            return capture("plan_document", plan_ref=ref)["pages"][0]["payload"]
        def authorize_plan():
            scope = self._authorize(context, connection)
            if connection.state in {"source_subject_changed", "permission_denied", "source_session_expired",
                                     "awaiting_user_login", "captcha_required"}:
                raise StudentPlanAcquisitionError(connection.state)
            return scope
        owner_path = _private_directory(self.root / digest({"realm": SCHOOL_REALM, "owner": connection.owner}))
        connection.plan = OnDemandPlanService(owner_path / "plans", authorize_plan, catalogue, document, clock=self.clock)
        return connection.plan

    def acquire_plan(self, context, *, force=False):
        if type(force) is not bool:
            _error("invalid_school_request")
        connection = self._connection(context)
        with connection.lock:
            profile = self.profile(context)["profile"]
            self._authorize(context, connection)
            try:
                result = self._plan_service(context, connection).acquire(profile, force=force)
            except StudentPlanAcquisitionError as error:
                _error(error.code, 409)
            self._authorize(context, connection)
            return {**result, "data_kind": "personal_source", "security_realm": SCHOOL_REALM,
                    "recommendation_status": "input_not_ready_for_recommendation"}

    def read_plan_document(self, context, source_alias):
        connection = self._connection(context)
        with connection.lock:
            try:
                body = self._plan_service(context, connection).read_document(source_alias)
            except StudentPlanAcquisitionError:
                _error("inaccessible_plan_document", 404)
            self._authorize(context, connection)
            return body

    def logout(self, context):
        with self._lock:
            connection = self._connection(context)
            self.auth.logout(context)
            self._sessions.pop(context.session_ref, None)
        self._close_client(connection.client)

    def discard_revoked_sessions(self):
        """Release at most 64 revoked/expired clients without a school request."""
        with self._lock:
            refs = list(self._sessions)[:64]
        live = set()
        if refs:
            placeholders = ",".join("?" for _ in refs)
            with self.auth.store.connect() as db:
                rows = db.execute("SELECT s.token_hash FROM login_sessions s JOIN identity_bindings b "
                                  "ON b.principal=s.principal WHERE s.token_hash IN (" + placeholders + ") "
                                  "AND s.revoked=0 AND s.absolute_expires>? AND s.idle_expires>? "
                                  "AND s.binding_version=b.version AND b.realm=? AND b.status=?",
                                  (*refs, self.clock(), self.clock(), SCHOOL_REALM, "verified_personal_client")).fetchall()
            live = {row[0] for row in rows}
        with self._lock:
            discarded = [self._sessions.pop(ref) for ref in refs if ref not in live and ref in self._sessions]
            expired = [nonce for nonce, (_, expiry) in self._login_challenges.items() if self.clock() >= expiry]
            challenge_clients = [self._login_challenges.pop(nonce)[0] for nonce in expired]
        for connection in discarded:
            self._close_client(connection.client)
        for client in challenge_clients:
            self._close_client(client)
        return {"discarded_sessions": len(discarded), "discarded_challenges": len(challenge_clients)}

    def close(self):
        with self._lock:
            clients = [item.client for item in self._sessions.values()] + [item[0] for item in self._login_challenges.values()]
            self._sessions.clear()
            self._login_challenges.clear()
        for client in clients:
            self._close_client(client)
