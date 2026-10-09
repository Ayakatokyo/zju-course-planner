"""Server-issued synthetic-only local sessions. School SSO is unconfigured."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import secrets
import time

COOKIE_NAME = "course_session"
REALM = "mock_development"
PROFILES = {
    "demo-a": {"student_id": "DEMO-STUDENT-001", "name": "模拟学生 A"},
    "demo-b": {"student_id": "DEMO-STUDENT-002", "name": "模拟学生 B"},
}


class AccessError(ValueError):
    def __init__(self, message="请先登录", status=401, reason="unauthenticated"):
        super().__init__(message)
        self.status, self.reason = status, reason


@dataclass(frozen=True)
class AuthContext:
    principal_id: str
    session_ref: str
    student_id: str
    security_realm: str
    binding_version: str
    auth_mode: str = "mock"

    def identity(self):
        return {"student_id": self.student_id, "name": PROFILES[self.principal_id]["name"],
                "auth_mode": self.auth_mode, "security_realm": self.security_realm,
                "data_kind": "synthetic", "personal_data_source_status": "not_connected"}


class AuthManager:
    def __init__(self, store, *, mock_enabled=False, clock=time.time, idle_seconds=1800, absolute_seconds=28800):
        self.store, self.mock_enabled, self.clock = store, mock_enabled, clock
        self.idle_seconds, self.absolute_seconds = idle_seconds, absolute_seconds
        if mock_enabled:
            with store.connect() as db:
                for principal, profile in PROFILES.items():
                    db.execute("INSERT OR IGNORE INTO identity_bindings VALUES(?,?,?,?,?)",
                               (principal, profile["student_id"], REALM, "DEMO-BINDING-v1", "verified"))

    @staticmethod
    def token_hash(token):
        return sha256(token.encode()).hexdigest()

    def login_mock(self, body, previous_token=None):
        if not self.mock_enabled:
            raise AccessError("mock 未开启；学校认证尚未配置", 403, "mock_disabled")
        if set(body) != {"profile_id"} or body["profile_id"] not in PROFILES:
            raise AccessError("仅支持服务端预置的 synthetic 夹具", 400, "invalid_mock_fixture")
        principal = body["profile_id"]
        with self.store.connect() as db:
            binding = db.execute("SELECT owner,realm,version,status FROM identity_bindings WHERE principal=?",
                                 (principal,)).fetchone()
            if not binding or binding[3] != "verified" or binding[1] != REALM or binding[0] != PROFILES[principal]["student_id"]:
                raise AccessError("身份绑定不可用", 403, "binding_unavailable")
            if previous_token:
                db.execute("UPDATE login_sessions SET revoked=1 WHERE token_hash=?", (self.token_hash(previous_token),))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            now = self.clock()
            db.execute("INSERT INTO login_sessions VALUES(?,?,?,?,?,?,?,?,0)",
                       (self.token_hash(token), principal, binding[2], csrf, now, now,
                        now + self.absolute_seconds, now + self.idle_seconds))
        context = self.authenticate(token)
        self.store.event(principal, "login", "allow", "synthetic_fixture")
        return token, {"authenticated": True, "identity": context.identity(), "csrf_token": csrf}

    def _session(self, session_ref):
        with self.store.connect() as db:
            row = db.execute("SELECT s.principal,s.binding_version,s.csrf_token,s.absolute_expires,s.idle_expires,s.revoked,"
                             "b.owner,b.realm,b.version,b.status FROM login_sessions s LEFT JOIN identity_bindings b "
                             "ON b.principal=s.principal WHERE s.token_hash=?", (session_ref,)).fetchone()
        now = self.clock()
        if not row or row[5] or now >= min(row[3], row[4]):
            raise AccessError()
        if not self.mock_enabled or row[7] != REALM or row[9] != "verified" or row[1] != row[8] or row[0] not in PROFILES or row[6] != PROFILES[row[0]]["student_id"]:
            raise AccessError("身份绑定不可用", 403, "binding_unavailable")
        return row

    def authenticate(self, token):
        if not isinstance(token, str) or not token or len(token) > 256:
            raise AccessError()
        session_ref = self.token_hash(token)
        row = self._session(session_ref)
        context = AuthContext(row[0], session_ref, row[6], row[7], row[8])
        self.require_context(context)
        return context

    def require_context(self, context):
        if not isinstance(context, AuthContext):
            raise AccessError()
        row = self._session(context.session_ref)
        if (context.principal_id, context.student_id, context.security_realm, context.binding_version, context.auth_mode) != (row[0], row[6], row[7], row[8], "mock"):
            raise AccessError("身份上下文不可用", 403, "context_mismatch")
        now = self.clock()
        with self.store.connect() as db:
            db.execute("UPDATE login_sessions SET last_activity=?,idle_expires=? WHERE token_hash=?",
                       (now, min(now + self.idle_seconds, row[3]), context.session_ref))
        return context

    def csrf(self, context):
        self.require_context(context)
        return self._session(context.session_ref)[2]

    def check_csrf(self, context, candidate):
        expected = self.csrf(context)
        if not isinstance(candidate, str) or not secrets.compare_digest(expected, candidate):
            raise AccessError("写请求校验失败", 403, "csrf_failed")

    def logout(self, context):
        self.require_context(context)
        with self.store.connect() as db:
            db.execute("UPDATE login_sessions SET revoked=1 WHERE token_hash=?", (context.session_ref,))
        self.store.event(context.principal_id, "logout", "allow", "session_revoked")
