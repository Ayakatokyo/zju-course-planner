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
from .course_catalog import CourseCatalogService
from .recommendation_readiness import project_source_diagnostics, source_receipt_diagnostics


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
    course_plans: object = None
    course_plan_epoch: object = None
    course_plan_config: object = None
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
    def __init__(self, auth, client_factory, *, root=None, clock=time.time, personal_pdf_extractor=None,
                 course_data_test_delivery_index=None, course_data_test_term=None):
        if not callable(client_factory):
            _error("invalid_school_request")
        self.auth, self.client_factory, self.clock = auth, client_factory, clock
        self.personal_pdf_extractor = personal_pdf_extractor
        self.course_catalog = CourseCatalogService()
        self.course_data_test_delivery_index = (course_data_test_delivery_index if course_data_test_delivery_index is not None
                                               else os.environ.get("COURSE_DATA_TEST_DELIVERY_INDEX"))
        self.course_data_test_term = (course_data_test_term if course_data_test_term is not None
                                     else os.environ.get("COURSE_DATA_TEST_TERM"))
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
            for page_number, page in enumerate(result.get("pages", []), 1):
                payload = page["payload"]
                parsed = parse_payload(DOMAINS[request["operation"]], payload, {"target_term": request["target_term"]})
                if request["operation"] == "timetable":
                    period_metadata = {key: parsed[key] for key in ("period_times", "period_times_source")}
                for record in parsed["records"]:
                    # Parser row numbers are page-local. This is a position in
                    # this read, never a stable school attempt identity.
                    global_index = len(records) + 1
                    record["source_page"] = page_number
                    record["source_global_row_index"] = global_index
                    if "attempt_candidate_id" in record:
                        record["attempt_candidate_id"] = f"academic-page-{page_number}-row-{record.get('source_row_index')}-global-{global_index}"
                    for reference in record.get("evidence_refs", []):
                        reference.update(source_page=page_number, source_global_row_index=global_index)
                    records.append(record)
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
        # Older events remain immutable audit evidence. Only the newest event
        # per actual query scope is part of the current input version.
        latest = {}
        for item in status["observations"]["snapshots"]:
            latest.setdefault((item["operation"], item["target_term"]), item)
        versions = {"observations": [{"observation_id": item["observation_id"], "versions": item["versions"]}
                    for item in latest.values()],
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

    @staticmethod
    def _live_receipt_ready(receipt):
        if not isinstance(receipt, dict) or receipt.get("ready") is not True or receipt.get("data_source") != "live":
            return False
        if receipt.get("operation") == "curriculum":
            return (receipt.get("status") == "acquired_pending_review"
                    and isinstance(receipt.get("source_alias"), str)
                    and re.fullmatch(r"PLAN-[a-f0-9]{24}", receipt["source_alias"]) is not None
                    and isinstance(receipt.get("sha256"), str)
                    and re.fullmatch(r"[a-f0-9]{64}", receipt["sha256"]) is not None)
        return (receipt.get("status") in {"complete_within_scope", "partial_acquisition"}
                and isinstance(receipt.get("observation_id"), str)
                and re.fullmatch(r"OBS-[a-f0-9]{32}", receipt["observation_id"]) is not None)

    def _course_plan_service(self, context, connection):
        from .personal_course_plans import CURRENT_TERM, PersonalCoursePlanService
        if not self.course_data_test_delivery_index or self.course_data_test_term != CURRENT_TERM:
            connection.course_plans, connection.course_plan_epoch, connection.course_plan_config = None, None, None
            return None
        epoch = connection.client.session_epoch
        self._authorize_local(context, connection, epoch)
        configured = digest({"delivery_index": str(self.course_data_test_delivery_index), "term": self.course_data_test_term})
        if connection.course_plans is None or connection.course_plan_epoch != epoch or connection.course_plan_config != configured:
            connection.course_plans = PersonalCoursePlanService(self.course_data_test_delivery_index, CURRENT_TERM,
                lambda: self._authorize_local(context, connection, epoch))
            connection.course_plan_epoch = epoch
            connection.course_plan_config = configured
        return connection.course_plans

    def _course_plan_sources(self, context, connection, receipts, kind):
        """Read only exact observations already acquired in this server turn."""
        sources = {"major_academic": {"status": "missing", "verified": False},
                   "curriculum": {"status": "missing", "verified": False},
                   "timetable": {"status": "not_required" if kind != "timetable" else "missing", "verified": False},
                   "exams": {"status": "not_requested", "verified": False}}
        def selected(operation, term):
            matching = [r for r in receipts if r.get("operation") == operation and r.get("target_term") == term]
            if not matching:
                return None
            # A failed/conflicting current receipt is not rescued by an older
            # positive receipt. No school request or historical fallback here.
            if any(not self._live_receipt_ready(r) for r in matching):
                sources[operation]["status"] = "failed"
                return None
            return matching[-1]
        def query(operation, term, *, all_records=False):
            receipt = selected(operation, term)
            if receipt is None:
                return
            records, cursor, count = [], None, 0
            while True:
                body = {"operation": operation, "observation_id": receipt["observation_id"], "limit": 20,
                        "mode": "records" if all_records else "summary"}
                if cursor:
                    body["cursor"] = cursor
                try:
                    result = self.local_query(context, "/api/personal/records", body)
                except AccessError as error:
                    from .personal_acquisition import AUTH_FAILURES
                    if error.status in {401, 403} or getattr(error, "reason", None) in AUTH_FAILURES:
                        raise
                    sources[operation]["status"] = "failed"
                    return
                if (result.get("observation_id") != receipt["observation_id"] or result.get("operation") != operation
                        or result.get("target_term") != term or result.get("data_source") != "live"
                        or result.get("historical_only") is not False or result.get("observation_status") != "observed_live"):
                    sources[operation]["status"] = "failed"
                    return
                if all_records:
                    if (result.get("status") != "complete_within_scope"
                            or result.get("coverage", {}).get("complete_within_scope") is not True
                            or result.get("row_failures")
                            or type(result.get("records")) is not list):
                        sources[operation]["status"] = "partial"
                        return
                    records.extend(result["records"])
                    count += 1
                    cursor = result.get("next_cursor")
                    if len(records) > 200 or count > 10:
                        sources[operation]["status"] = "partial"
                        return
                    if cursor:
                        continue
                    if result.get("matched_records") != len(records) or result.get("total_records") != len(records):
                        sources[operation]["status"] = "partial"
                        return
                sources[operation] = {"status": "partial" if result.get("status") == "partial_acquisition" else "available",
                    "verified": True, "observation_id": receipt["observation_id"], "records": records}
                return
        query("major_academic", "ALL-HISTORY")
        curriculum = selected("curriculum", None)
        if curriculum:
            local = self.local_chat_status(context)["local"]
            plan = local["plan"]
            if (plan.get("status") == "acquired_pending_review" and plan.get("source_alias") == curriculum["source_alias"]
                    and plan.get("sha256") == curriculum["sha256"] and local["evidence"].get("ready") is True):
                sources["curriculum"] = {"status": "available", "verified": True}
            else:
                sources["curriculum"]["status"] = "failed"
        if kind == "timetable":
            query("timetable", "2026-2027-AW", all_records=True)
        # This is only acquisition status from this turn's server receipts.
        # The bounded plan validator still checks weekday/period observations;
        # it neither fetches exams here nor claims full exam conflict coverage.
        exam_receipts = [r for r in receipts if r.get("operation") == "exams" and r.get("target_term") == "2026-2027-AW"]
        if exam_receipts:
            if any(not self._live_receipt_ready(r) for r in exam_receipts):
                sources["exams"]["status"] = "failed"
            else:
                sources["exams"]["status"] = "partial" if any(r.get("status") == "partial_acquisition" for r in exam_receipts) else "available"
        self._authorize_local(context, connection, connection.client.session_epoch)
        return sources

    def personal_chat_live_tool(self, context, name, args, *, receipts, emit, authorize, adopt, before=None, policy_context=None):
        """Use a fresh receipt from this turn, never a latest historical row.

        ``receipts`` belongs to the server's current turn and is not accepted
        from model arguments. Newly requested scopes are force-read once and
        adopted before querying their exact observation. A failed receipt blocks
        same-turn retries and historical fallback. Only static course names and
        codes use the explicitly configured local catalog.
        """
        from .chat_runs import tool_event_fields
        from .personal_acquisition import AUTH_FAILURES
        if (not isinstance(args, dict) or not isinstance(receipts, list)
                or not all(callable(fn) for fn in (emit, authorize, adopt))):
            _error("invalid_school_request")
        connection = self._connection(context)
        authorize()

        def unavailable(operation, term=None, reason="no_fresh_source_in_turn"):
            return {"status": "not_ready", "operation": operation, "target_term": term,
                    "data_kind": "personal_source", "data_source": "none", "records": [], "matches": [],
                    "coverage": {}, "matched_records": None, "returned_records": 0,
                    "query_executed": False, "reason": reason, "answer_support_verified": False}

        def code(error):
            value = getattr(error, "reason", getattr(error, "code", "source_unavailable"))
            return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value) else "source_unavailable"

        def read(operation, term):
            event_name = "read_my_" + operation
            arguments = {"operation": operation, **({"target_term": term} if term is not None else {})}
            fields = tool_event_fields(event_name, arguments, initiated_by="service_acquisition")
            started = time.monotonic()
            emit("tool.start", fields)
            try:
                result = (self.acquire_plan(context, force=True) if operation == "curriculum" else
                          self.fetch(context, {"operation": operation, "target_term": term, "force_refresh": True}))
                if not isinstance(result, dict):
                    raise ValueError("invalid_source_result")
            except Exception as error:
                failure = code(error)
                receipt = {**arguments, "target_term": term, "status": "failed", "data_source": "none",
                    "observation_id": None, "checked_at": self.clock(), "coverage": {}, "ready": False, "failure": failure}
                receipts.append(receipt)
                emit("tool.end", {**fields, "status": "failed", "error_code": failure,
                    "elapsed_ms": round((time.monotonic() - started) * 1000)})
                adopt(True)
                authorize()
                if isinstance(error, PermissionError) or failure in AUTH_FAILURES:
                    raise
                return receipt
            receipt = {**arguments, "target_term": term, "status": result.get("status", "not_ready"),
                "observation_id": result.get("observation_id"), "data_source": result.get("data_source", "none"),
                "checked_at": result.get("checked_at", self.clock()),
                "coverage": deepcopy(result.get("coverage")) if isinstance(result.get("coverage"), dict) else {},
                "ready": True}
            for key in ("failure", "source_alias", "sha256"):
                if key in result:
                    receipt[key] = result[key]
            if operation != "curriculum" and (result.get("operation") != operation or result.get("target_term") != term):
                receipt["ready"] = False
            receipt["ready"] = self._live_receipt_ready(receipt)
            if receipt["ready"] and (diagnostics := source_receipt_diagnostics(operation, result)) is not None:
                receipt["diagnostics"] = diagnostics
            receipts.append(receipt)
            # Source additions change the input digest. The caller adopts that
            # version while this same connection lock still serializes reads.
            try:
                adopt(True)
                authorize()
            except Exception as error:
                emit("tool.end", {**fields, "status": "failed", "error_code": code(error),
                    "elapsed_ms": round((time.monotonic() - started) * 1000)})
                raise
            emit("tool.end", {**tool_event_fields(event_name, arguments, result, initiated_by="service_acquisition"),
                "status": "completed" if receipt["ready"] else "not_ready",
                "elapsed_ms": round((time.monotonic() - started) * 1000)})
            return receipt

        def public_receipt(receipt):
            item = {**{key: deepcopy(receipt[key]) for key in
                    ("operation", "target_term", "status", "data_source", "clarification", "failure") if key in receipt},
                    "ready": self._live_receipt_ready(receipt)}
            if item["ready"] and "diagnostics" in receipt:
                try:
                    item["diagnostics"] = project_source_diagnostics(receipt["diagnostics"], receipt.get("operation"))
                except ValueError:
                    pass
            return item

        if name == "search_course_catalog":
            if set(args) - {"query", "limit"} or "query" not in args:
                _error("invalid_school_request")
            result = self.course_catalog.search(**args)
            authorize()
            return result
        with connection.lock:
            if before is not None:
                before()
            self._authorize(context, connection)
            if name == "get_my_context":
                if args:
                    _error("invalid_school_request")
                profile = parse_payload("profile", connection.profile)["records"][0]
                operations = []
                for receipt in receipts:
                    if receipt.get("operation") in DOMAINS and self._live_receipt_ready(receipt):
                        item = {"operation": receipt["operation"], "target_term": receipt.get("target_term"),
                            "coverage": deepcopy(receipt.get("coverage", {})), "checked_at": receipt.get("checked_at"),
                            "freshness": "fetched_in_current_turn"}
                        if item not in operations:
                            operations.append(item)
                curriculum = any(receipt.get("operation") == "curriculum" and self._live_receipt_ready(receipt) for receipt in receipts)
                catalog = self.course_catalog.describe()
                plans = self._course_plan_service(context, connection)
                course_data_test = (plans.describe() if plans else {"status": "not_configured", "target_term": None,
                    "reference_mode": "historical_course_observations"})
                result = {"status": "available", "data_kind": "personal_source", "cohort": profile.get("cohort"),
                    "major_name": profile.get("major_name"), "operations": operations,
                    "api_operations": [{"operation": operation, "target_term_required": operation != "major_academic"}
                                       for operation in ("academic", "major_academic", "timetable", "exams")],
                    "capabilities": {"curriculum_available": curriculum, "personal_record_query": True, "live_record_query": True,
                        "curriculum_local_search": True, "read_only_acquisition": True,
                        "course_catalog_search": catalog.get("status") == "ready",
                        "recommendation_input_check": True,
                        "course_data_test": course_data_test["status"] == "ready",
                        "real_recommendation": False},
                    "course_catalog": catalog,
                    "course_data_test": course_data_test,
                    "current_course_plan": plans.current_plan_hint(policy_context.get("active_plan_ref") if isinstance(policy_context, dict) else None,
                        current_dialogue=True) if plans else None,
                    "source_receipts": [public_receipt(receipt) for receipt in receipts],
                    "note": "教务事实须由本轮实际接口读取回执支持；旧本机观察不作为本轮最新资料。课程目录仅为本地静态参考。"}
            elif name in {"search_current_term_courses", "generate_current_term_plan", "update_current_term_plan", "get_current_term_plan"}:
                from .personal_course_plans import validate_course_plan_args, _base
                try:
                    validate_course_plan_args(name, args)
                except (ValueError, TypeError):
                    _error("invalid_school_request")
                plans = self._course_plan_service(context, connection)
                if plans is None:
                    action = "search" if name == "search_current_term_courses" else "inspect" if name == "get_current_term_plan" else args.get("action", "generate")
                    result = {"status": "not_ready", "data_kind": "course_data_test",
                              "result": {**_base(action), "reason": "course_data_test_not_configured", "actions": ["search"]}}
                else:
                    kind = plans.required_sources(name, args,
                        active_plan_ref=policy_context.get("active_plan_ref") if isinstance(policy_context, dict) else None)
                    sources = self._course_plan_sources(context, connection, receipts, kind) if kind else {}
                    try:
                        result = plans.execute(name, args, sources=sources, turn_identity=receipts,
                            policy_context=policy_context, enforce_policy=True)
                    except AccessError:
                        raise
                    except (ValueError, TypeError):
                        _error("invalid_school_request")
            elif name == "check_recommendation_inputs":
                if (set(args) - {"recommendation_kind", "target_term"} or "recommendation_kind" not in args
                        or "target_term" in args and not isinstance(args["target_term"], str)):
                    _error("invalid_school_request")
                from .recommendation_readiness import assess_recommendation_readiness
                try:
                    # Validate before reading even the configured local catalog.
                    assess_recommendation_readiness({}, **args)
                except ValueError:
                    _error("invalid_school_request")
                current = self.personal_chat_live_tool(context, "get_my_context", {},
                    receipts=receipts, emit=emit, authorize=authorize, adopt=adopt, before=before, policy_context=policy_context)
                result = {"status": "available", "data_kind": "personal_source", "check_executed": True,
                    "readiness": assess_recommendation_readiness(current, **args)}
            elif name == "query_my_records":
                if set(args) - {"operation", "target_term", "query", "academic_year", "limit", "cursor", "mode"} or args.get("operation") not in DOMAINS:
                    _error("invalid_school_request")
                operation = args["operation"]
                term = args.get("target_term")
                candidates = [receipt for receipt in receipts if receipt.get("operation") == operation]
                if term is None and operation == "major_academic":
                    term = "ALL-HISTORY"
                elif term is None:
                    scopes = {receipt.get("target_term") for receipt in candidates if isinstance(receipt.get("target_term"), str)}
                    if len(scopes) != 1 or any(receipt.get("status") == "needs_clarification" for receipt in candidates):
                        return unavailable(operation, reason="target_term_required")
                    term = next(iter(scopes))
                self._request_body({"operation": operation, "target_term": term, "force_refresh": True})
                matching = [receipt for receipt in candidates if receipt.get("target_term") == term]
                # A lexical preflight can lack a scope that the model resolves
                # from the actual pending clarification. Explicit canonical
                # arguments still require a fresh authorized read; a null or
                # ambiguous scope above cannot use a historical observation.
                receipt = matching[-1] if matching else read(operation, term)
                if not self._live_receipt_ready(receipt):
                    return unavailable(operation, term, "fresh_source_read_failed")
                request = {key: deepcopy(value) for key, value in args.items()
                           if key in {"operation", "query", "academic_year", "limit", "cursor", "mode"}}
                request["observation_id"] = receipt["observation_id"]
                result = self.local_query(context, "/api/personal/records", request)
                if result.get("observation_id") != receipt["observation_id"] or result.get("target_term") != term:
                    return unavailable(operation, term, "fresh_receipt_mismatch")
                result["freshness"] = "fetched_in_current_turn"
            elif name == "search_my_curriculum":
                if set(args) - {"query", "limit"} or "query" not in args:
                    _error("invalid_school_request")
                matching = [receipt for receipt in receipts if receipt.get("operation") == "curriculum"]
                receipt = matching[-1] if matching else read("curriculum", None)
                if not self._live_receipt_ready(receipt):
                    return unavailable("curriculum", reason="fresh_curriculum_read_failed")
                local = self.local_chat_status(context)["local"]
                if (local["plan"].get("status") != "acquired_pending_review"
                        or local["plan"].get("source_alias") != receipt["source_alias"]
                        or local["plan"].get("sha256") != receipt["sha256"]):
                    return unavailable("curriculum", reason="fresh_curriculum_receipt_mismatch")
                if not local["evidence"].get("ready"):
                    self.local_query(context, "/api/evidence/prepare", {"source_alias": receipt["source_alias"]})
                    adopt(True)
                result = self.local_query(context, "/api/evidence/search", args)
                result["freshness"] = "fetched_in_current_turn"
            else:
                _error("invalid_school_request")
            self._authorize(context, connection)
            authorize()
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
            if (operation == "plan_catalogue" and result.get("pages")
                    and result.get("failure") == "page_limit_exceeded"):
                raise StudentPlanAcquisitionError("invalid_plan_catalogue")
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
