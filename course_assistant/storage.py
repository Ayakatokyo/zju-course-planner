"""SQLite resources queried by owner and realm; ownerless legacy data stays quarantined."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .importer import digest


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owned_datasets (
                    realm TEXT NOT NULL, owner TEXT NOT NULL, digest TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(realm,owner,digest));
                CREATE TABLE IF NOT EXISTS workspaces (
                    realm TEXT NOT NULL, owner TEXT NOT NULL, dataset_digest TEXT NOT NULL,
                    constraints_body TEXT NOT NULL, PRIMARY KEY(realm,owner),
                    FOREIGN KEY(realm,owner,dataset_digest) REFERENCES owned_datasets(realm,owner,digest));
                CREATE TABLE IF NOT EXISTS owned_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, realm TEXT NOT NULL,
                    owner TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS plans_by_owner ON owned_plans(realm,owner,id);
                CREATE TABLE IF NOT EXISTS identity_bindings (
                    principal TEXT PRIMARY KEY, owner TEXT NOT NULL, realm TEXT NOT NULL,
                    version TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash TEXT PRIMARY KEY, principal TEXT NOT NULL,
                    binding_version TEXT NOT NULL, csrf_token TEXT NOT NULL,
                    issued REAL NOT NULL, last_activity REAL NOT NULL,
                    absolute_expires REAL NOT NULL, idle_expires REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                    principal TEXT, action TEXT NOT NULL, decision TEXT NOT NULL,
                    reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS owned_conversations (
                    id TEXT PRIMARY KEY, realm TEXT NOT NULL, owner TEXT NOT NULL,
                    turn INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                    pending_request TEXT, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS conversations_by_owner ON owned_conversations(realm,owner,id);
                CREATE TABLE IF NOT EXISTS owned_chat_requests (
                    realm TEXT NOT NULL, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL, response TEXT,
                    PRIMARY KEY(realm,owner,request_id),
                    FOREIGN KEY(conversation_id) REFERENCES owned_conversations(id));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, principal, action, decision, reason):
        with self.connect() as db:
            db.execute("INSERT INTO security_events(occurred_at,principal,action,decision,reason) VALUES(?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), principal, action, decision, reason))

    def import_dataset(self, context, body):
        key = digest(body)
        with self.connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO owned_datasets VALUES(?,?,?,?)",
                             (context.security_realm, context.student_id, key, encoded(body)))
        return {"dataset_sha256": key, "inserted": bool(cur.rowcount)}

    def workspace(self, context):
        with self.connect() as db:
            row = db.execute("SELECT d.body,w.constraints_body,d.digest FROM workspaces w JOIN owned_datasets d "
                             "ON d.realm=w.realm AND d.owner=w.owner AND d.digest=w.dataset_digest "
                             "WHERE w.realm=? AND w.owner=?", (context.security_realm, context.student_id)).fetchone()
        return {"demo": json.loads(row[0]), "constraints": json.loads(row[1]), "dataset_sha256": row[2]} if row else None

    def set_workspace(self, context, demo, constraints):
        imported = self.import_dataset(context, demo)
        with self.connect() as db:
            db.execute("INSERT INTO workspaces VALUES(?,?,?,?) ON CONFLICT(realm,owner) DO UPDATE SET "
                       "dataset_digest=excluded.dataset_digest,constraints_body=excluded.constraints_body",
                       (context.security_realm, context.student_id, imported["dataset_sha256"], encoded(constraints)))
        return imported

    def save(self, context, body):
        created = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cur = db.execute("INSERT INTO owned_plans(realm,owner,body,created_at) VALUES(?,?,?,?)",
                             (context.security_realm, context.student_id, encoded(body), created))
            identifier = cur.lastrowid
        return self._saved_row((identifier, encoded(body), created))

    @staticmethod
    def _saved_row(row):
        key, body, created = row
        return {**json.loads(body), "saved_id": key, "saved_revision": key,
                "created_at": created, "status": "saved_planning_only"}

    def saved(self, context):
        with self.connect() as db:
            rows = db.execute("SELECT id,body,created_at FROM owned_plans WHERE realm=? AND owner=? ORDER BY id DESC",
                              (context.security_realm, context.student_id)).fetchall()
        return [self._saved_row(row) for row in rows]

    def saved_one(self, context, identifier):
        with self.connect() as db:
            row = db.execute("SELECT id,body,created_at FROM owned_plans WHERE id=? AND realm=? AND owner=?",
                             (identifier, context.security_realm, context.student_id)).fetchone()
        return self._saved_row(row) if row else None

    def chat_one(self, context, identifier):
        with self.connect() as db:
            row = db.execute("SELECT turn,state,pending_request FROM owned_conversations "
                             "WHERE id=? AND realm=? AND owner=?",
                             (identifier, context.security_realm, context.student_id)).fetchone()
        return {"conversation_id": identifier, "turn_id": row[0], "state": json.loads(row[1]),
                "in_progress": row[2] is not None} if row else None

    def chat_begin(self, context, identifier, request_id, request_digest, expected_turn):
        """Reserve one turn atomically. IDs locate resources, never grant access."""
        from .auth import AccessError
        realm, owner = context.security_realm, context.student_id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT request_digest,conversation_id,status,response FROM owned_chat_requests "
                                  "WHERE realm=? AND owner=? AND request_id=?", (realm, owner, request_id)).fetchone()
            if previous:
                if previous[0] != request_digest:
                    raise AccessError("同一请求编号不能用于不同消息", 409, "request_id_reused")
                if previous[2] == "pending":
                    raise AccessError("该轮仍在执行，请等待完成", 409, "turn_in_progress")
                return {"replay": json.loads(previous[3])}
            if identifier is None:
                if expected_turn != 0:
                    raise AccessError("新对话应从第 0 轮开始", 409, "turn_version_changed")
                identifier = str(uuid.uuid4())
                state = {"messages": [], "turns": [], "preview_constraints": None, "workspace_versions": None}
                db.execute("INSERT INTO owned_conversations VALUES(?,?,?,?,?,?,?)",
                           (identifier, realm, owner, 0, encoded(state), None, datetime.now(timezone.utc).isoformat()))
                turn, pending = 0, None
            else:
                row = db.execute("SELECT turn,state,pending_request FROM owned_conversations "
                                 "WHERE id=? AND realm=? AND owner=?", (identifier, realm, owner)).fetchone()
                if row is None:
                    raise AccessError("资源不可访问", 404, "inaccessible_object")
                turn, state, pending = row[0], json.loads(row[1]), row[2]
            if pending:
                raise AccessError("该对话正在执行另一轮", 409, "turn_in_progress")
            if turn != expected_turn:
                raise AccessError("对话已更新，请重新载入", 409, "turn_version_changed")
            if turn >= 20:
                raise AccessError("该对话已达 20 轮上限，请新建对话", 409, "conversation_limit")
            db.execute("UPDATE owned_conversations SET pending_request=? WHERE id=? AND realm=? AND owner=?",
                       (request_id, identifier, realm, owner))
            db.execute("INSERT INTO owned_chat_requests VALUES(?,?,?,?,?,?,?)",
                       (realm, owner, request_id, request_digest, identifier, "pending", None))
        return {"conversation_id": identifier, "turn_id": turn + 1, "state": state}

    def chat_finish(self, context, identifier, request_id, turn_id, state, response):
        """Finish only the reserved turn; no workspace or frozen plan is changed."""
        from .auth import AccessError
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE owned_conversations SET turn=?,state=?,pending_request=NULL,updated_at=? "
                                "WHERE id=? AND realm=? AND owner=? AND pending_request=? AND turn=?",
                                (turn_id, encoded(state), datetime.now(timezone.utc).isoformat(), identifier,
                                 context.security_realm, context.student_id, request_id, turn_id - 1))
            if result.rowcount != 1:
                raise AccessError("对话版本已变化", 409, "turn_version_changed")
            db.execute("UPDATE owned_chat_requests SET status=?,response=? "
                       "WHERE realm=? AND owner=? AND request_id=? AND conversation_id=?",
                       ("complete" if response["status"] == "completed" else "failed", encoded(response),
                        context.security_realm, context.student_id, request_id, identifier))
