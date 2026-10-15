"""Bounded private run events, never complete prompts or source records.

The application must catch ChatRunError so diagnostics cannot break chat.
``authorize(context)`` may return a current scope_version string (or a dict
containing it). With no callback the trusted caller owns authorization before
and after every operation; resource IDs alone never constitute authorization.
Provider-visible reasoning is untrusted, potentially personal text. It is
redacted, bounded and omitted from default summaries, not an execution proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import math
import re
import sqlite3
import stat
import time
import uuid

from .auth import AuthContext


KINDS = frozenset({"model.start", "model.end", "tool.start", "tool.end", "answer.validated", "error"})
FINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "aborted"})
COUNTS = frozenset({"step", "attempt", "count", "tool_count", "citation_count", "record_count",
                    "match_count", "claim_count", "evidence_count", "unknown_count", "input_chars", "output_chars"})
USAGE = frozenset({"prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens",
                   "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "input_tokens", "output_tokens"})
COMMON_FIELDS = COUNTS | {"elapsed_ms", "status", "error_code"}
EVENT_FIELDS = {
    "model.start": COMMON_FIELDS | {"provider", "model"},
    "model.end": COMMON_FIELDS | {"provider", "model", "usage", "reasoning_content", "request_id", "completion_id", "http_status"},
    "tool.start": COMMON_FIELDS | {"tool_name"},
    "tool.end": COMMON_FIELDS | {"tool_name"},
    "answer.validated": COMMON_FIELDS,
    "error": COMMON_FIELDS | {"stage"},
}
MAX_EVENTS = 256
MAX_REASONING_EVENT = 4096
MAX_REASONING_RUN = 16384
RUN_ID = re.compile(r"RUN-[a-f0-9]{32}\Z")
CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}\Z")


class ChatRunError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise ChatRunError(code)


def _scope(value):
    if type(value) is not str or not 0 < len(value) <= 512 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        _fail("invalid_run_scope")
    return sha256(value.encode("utf-8")).hexdigest()


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        _fail("invalid_run_request")
    return value


def _name(value):
    if (type(value) is not str or not NAME.fullmatch(value) or value.startswith(("/", "file:", "http:", "https:")) or
            re.match(r"[A-Za-z]:[/\\]", value) or re.search(r"(?i)\bsk[-_][A-Za-z0-9_-]{8,}", value)):
        _fail("invalid_run_event")
    return value


def _code(value):
    if type(value) is not str or not CODE.fullmatch(value):
        _fail("invalid_run_event")
    return value


def _usage(value):
    if type(value) is not dict or set(value) - (USAGE | {"completion_tokens_details", "prompt_tokens_details"}):
        _fail("invalid_run_event")
    result = {}
    for key, count in value.items():
        if key in {"completion_tokens_details", "prompt_tokens_details"}:
            allowed = {"reasoning_tokens"} if key == "completion_tokens_details" else {"cached_tokens"}
            if type(count) is not dict or set(count) - allowed:
                _fail("invalid_run_event")
            result[key] = _usage(count)
        elif type(count) is not int or not 0 <= count <= 100000000:
            _fail("invalid_run_event")
        else:
            result[key] = count
    return result


def _reasoning(value, remaining):
    if type(value) is not str or len(value) > 200000:
        _fail("invalid_run_event")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail("invalid_run_event")
    original = value
    # A reasoning field is not a permitted container for structured requests.
    if re.search(r'"(?:messages|prompt|tool_args|arguments|raw_rows|raw_row|payload|headers)"\s*:', value, re.I):
        value = "[结构化请求或记录内容已隐藏]"
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", "[凭据已隐藏]", value, flags=re.S)
    value = re.sub(r"(?i)\bsk[-_][A-Za-z0-9_-]{8,}|\bbearer\s+\S+", "[凭据已隐藏]", value)
    value = re.sub(r"(?im)(?:password|passwd|cookie|set-cookie|token|api[_ -]?key|csrf|authorization|密码|口令)\s*(?:[:=：]|是|为)\s*[^\r\n]+", "[凭据已隐藏]", value)
    value = re.sub(r"(?:file://|https?://)[^\s<>\"']+|/(?:Users|private|var|home|tmp)/[^\s<>\"']+|[A-Za-z]:\\[^\s<>\"']+", "[路径或链接已隐藏]", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    limit = min(MAX_REASONING_EVENT, remaining)
    return value[:limit], len(value) > limit, value != original


class ChatRunStore:
    def __init__(self, store, authorize=None, *, clock=time.time):
        if not callable(getattr(store, "connect", None)) or authorize is not None and not callable(authorize) or not callable(clock):
            _fail("invalid_run_request")
        self.store, self.authorize, self.clock = store, authorize, clock
        self._private()
        try:
            with self.store.connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS personal_chat_runs (
                        id TEXT PRIMARY KEY,realm TEXT NOT NULL,owner TEXT NOT NULL,scope_version TEXT NOT NULL,
                        conversation_id TEXT,request_id TEXT NOT NULL,provider TEXT,model TEXT,remote INTEGER,
                        thinking INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,error_code TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,
                        event_count INTEGER NOT NULL DEFAULT 0,reasoning_chars INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(realm,owner,scope_version,request_id));
                    CREATE INDEX IF NOT EXISTS chat_runs_by_scope ON personal_chat_runs(realm,owner,scope_version,created_at);
                    CREATE TABLE IF NOT EXISTS personal_chat_run_events (
                        run_id TEXT NOT NULL,sequence INTEGER NOT NULL,kind TEXT NOT NULL,created_at REAL NOT NULL,
                        fields TEXT NOT NULL,PRIMARY KEY(run_id,sequence),
                        FOREIGN KEY(run_id) REFERENCES personal_chat_runs(id));
                """)
                if "thinking" not in {row[1] for row in db.execute("PRAGMA table_info(personal_chat_runs)")}:
                    db.execute("ALTER TABLE personal_chat_runs ADD COLUMN thinking INTEGER NOT NULL DEFAULT 0")
        except (sqlite3.Error, OSError):
            _fail("chat_run_store_unavailable")

    def _private(self):
        try:
            path = self.store.path.expanduser().absolute()
            info, parent = path.lstat(), path.parent.lstat()
            if (path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISDIR(parent.st_mode) or
                    stat.S_IMODE(parent.st_mode) != 0o700):
                _fail("private_chat_run_store_required")
        except OSError:
            _fail("chat_run_store_unavailable")

    def _now(self):
        try:
            value = self.clock()
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError
            return value
        except Exception:
            _fail("chat_run_clock_unavailable")

    def _auth(self, context, expected=None):
        if not isinstance(context, AuthContext) or any(type(value) is not str or not value for value in (context.student_id, context.security_realm, context.session_ref)):
            _fail("chat_run_authorization_expired")
        if self.authorize is None:
            return expected
        try:
            value = self.authorize(context)
        except Exception:
            _fail("chat_run_authorization_expired")
        if type(value) is dict:
            value = value.get("scope_version")
        current = _scope(value)
        if expected is not None and current != expected:
            _fail("chat_run_scope_changed")
        return current

    @contextmanager
    def _db(self, context, expected=None, *, write=False):
        current = self._auth(context, expected)
        self._private()
        try:
            with self.store.connect() as db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db, current
            # AuthManager touches the same SQLite database. Reauthorizing while
            # holding this write transaction would deadlock its session update.
            # A post-commit failure returns no payload; old-scope rows stay
            # private and cannot be read under a replacement authorization.
            self._auth(context, current)
            self._private()
        except (sqlite3.Error, OSError):
            _fail("chat_run_store_unavailable")

    @staticmethod
    def _row(db, context, run_id):
        if type(run_id) is not str or not RUN_ID.fullmatch(run_id):
            _fail("inaccessible_chat_run")
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM personal_chat_runs WHERE id=? AND realm=? AND owner=?",
                         (run_id, context.security_realm, context.student_id)).fetchone()
        if row is None:
            _fail("inaccessible_chat_run")
        return dict(row)

    @staticmethod
    def _summary(row):
        return {key: row[key] for key in ("id", "scope_version", "conversation_id", "request_id", "provider", "model",
                "remote", "status", "error_code", "created_at", "updated_at", "event_count", "reasoning_chars")} | {
                "run_id": row["id"], "data_kind": "personal_source", "local_only": True,
                "thinking": bool(row["thinking"]),
                "reasoning_kind": "provider_visible_reasoning", "reasoning_is_execution_proof": False}

    def begin(self, context, conversation_id=None, request_id=None, model_status=None, scope_version=None):
        scope = _scope(scope_version)
        request_id = _uuid(request_id)
        if conversation_id is not None:
            _uuid(conversation_id)
        if type(model_status) is not dict:
            _fail("invalid_run_request")
        provider = _name(model_status["provider"]) if model_status.get("provider") is not None else None
        model = _name(model_status["model"]) if model_status.get("model") is not None else None
        remote = model_status.get("remote")
        thinking = model_status.get("thinking", False)
        if remote is not None and type(remote) is not bool or type(thinking) is not bool:
            _fail("invalid_run_request")
        with self._db(context, scope, write=True) as (db, _):
            db.row_factory = sqlite3.Row
            old = db.execute("SELECT * FROM personal_chat_runs WHERE realm=? AND owner=? AND scope_version=? AND request_id=?",
                (context.security_realm, context.student_id, scope, request_id)).fetchone()
            if old is not None:
                if (old["conversation_id"], old["provider"], old["model"], old["remote"], bool(old["thinking"])) != (conversation_id, provider, model, remote, thinking):
                    _fail("chat_run_request_reused")
                return old["id"]
            run_id, now = "RUN-" + uuid.uuid4().hex, self._now()
            db.execute("INSERT INTO personal_chat_runs(id,realm,owner,scope_version,conversation_id,request_id,provider,model,remote,thinking,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'running',?,?)",
                (run_id, context.security_realm, context.student_id, scope, conversation_id, request_id, provider, model, remote, thinking, now, now))
            return run_id

    def _fields(self, kind, fields, remaining):
        if type(kind) is not str or kind not in KINDS or type(fields) is not dict or set(fields) - EVENT_FIELDS[kind]:
            _fail("invalid_run_event")
        result = {}
        for key, value in fields.items():
            if key in COUNTS:
                if type(value) is not int or not 0 <= value <= 100000000:
                    _fail("invalid_run_event")
                result[key] = value
            elif key == "elapsed_ms":
                if type(value) not in (int, float) or not 0 <= value <= 86400000 or not math.isfinite(value):
                    _fail("invalid_run_event")
                result[key] = value
            elif key in {"provider", "model", "tool_name"}:
                result[key] = _name(value)
            elif key == "error_code":
                result[key] = _code(value)
            elif key in {"request_id", "completion_id"}:
                if (type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) or
                        re.match(r"(?i)sk[-_][A-Za-z0-9_-]{8,}", value)):
                    _fail("invalid_run_event")
                result[key] = value
            elif key == "http_status":
                if type(value) is not int or not 100 <= value <= 599:
                    _fail("invalid_run_event")
                result[key] = value
            elif key == "usage":
                result[key] = _usage(value)
            elif key == "status":
                if type(value) is not str or value not in {"started", "running", "completed", "failed", "cancelled", "accepted", "rejected", "ok", "unknown", "not_ready", "timeout", "skipped"}:
                    _fail("invalid_run_event")
                result[key] = value
            elif key == "stage":
                if type(value) is not str or value not in {"model", "tool", "answer", "authorization", "storage", "run"}:
                    _fail("invalid_run_event")
                result[key] = value
            elif key == "reasoning_content":
                text, truncated, redacted = _reasoning(value, remaining)
                result.update(reasoning_content=text, reasoning_chars=len(text), reasoning_truncated=truncated,
                              reasoning_redacted=redacted, reasoning_source="provider_visible_reasoning")
        if kind == "error" and "error_code" not in result:
            _fail("invalid_run_event")
        return result

    def event(self, context, run_id, kind, fields):
        with self._db(context, write=True) as (db, current):
            row = self._row(db, context, run_id)
            if current is not None and current != row["scope_version"]:
                _fail("inaccessible_chat_run")
            if row["status"] != "running":
                _fail("chat_run_finished")
            if row["event_count"] >= MAX_EVENTS:
                _fail("chat_run_event_limit")
            values = self._fields(kind, fields, MAX_REASONING_RUN - row["reasoning_chars"])
            sequence, now = row["event_count"] + 1, self._now()
            db.execute("INSERT INTO personal_chat_run_events VALUES(?,?,?,?,?)",
                       (run_id, sequence, kind, now, json.dumps(values, ensure_ascii=False, allow_nan=False)))
            db.execute("UPDATE personal_chat_runs SET event_count=?,reasoning_chars=reasoning_chars+?,updated_at=? WHERE id=?",
                       (sequence, values.get("reasoning_chars", 0), now, run_id))
            return {"sequence": sequence, "kind": kind, "created_at": now,
                    "fields": {key: value for key, value in values.items() if key != "reasoning_content"}}

    def finish(self, context, run_id, status, error_code=None):
        if type(status) is not str or status not in FINAL_STATUSES or status == "completed" and error_code is not None:
            _fail("invalid_run_event")
        if error_code is not None:
            _code(error_code)
        with self._db(context, write=True) as (db, current):
            row = self._row(db, context, run_id)
            if current is not None and current != row["scope_version"]:
                _fail("inaccessible_chat_run")
            if row["status"] != "running" and (row["status"], row["error_code"]) != (status, error_code):
                _fail("chat_run_finished")
            if row["status"] != "running":
                return self._summary(row)
            now = self._now()
            db.execute("UPDATE personal_chat_runs SET status=?,error_code=?,updated_at=? WHERE id=?", (status, error_code, now, run_id))
            row.update(status=status, error_code=error_code, updated_at=now)
            return self._summary(row)

    def list(self, context, scope_version, limit=20):
        scope = _scope(scope_version)
        if type(limit) is not int or not 1 <= limit <= 50:
            _fail("invalid_run_request")
        with self._db(context, scope) as (db, _):
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM personal_chat_runs WHERE realm=? AND owner=? AND scope_version=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (context.security_realm, context.student_id, scope, limit + 1)).fetchall()
            return {"runs": [self._summary(dict(row)) for row in rows[:limit]], "has_more": len(rows) > limit,
                    "scope_version": scope, "local_only": True}

    def read(self, context, run_id, scope_version, *, include_reasoning=False):
        scope = _scope(scope_version)
        if type(include_reasoning) is not bool:
            _fail("invalid_run_request")
        with self._db(context, scope) as (db, _):
            row = self._row(db, context, run_id)
            if row["scope_version"] != scope:
                _fail("inaccessible_chat_run")
            events = []
            for event in db.execute("SELECT sequence,kind,created_at,fields FROM personal_chat_run_events WHERE run_id=? ORDER BY sequence LIMIT ?", (run_id, MAX_EVENTS)):
                fields = json.loads(event["fields"])
                if not include_reasoning:
                    fields.pop("reasoning_content", None)
                events.append({"sequence": event["sequence"], "kind": event["kind"], "created_at": event["created_at"], "fields": fields})
            return {"run": self._summary(row), "events": events, "reasoning_included": include_reasoning,
                    "reasoning_kind": "provider_visible_reasoning", "reasoning_is_execution_proof": False,
                    "local_only": True}
