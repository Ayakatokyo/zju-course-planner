"""SQLite resources queried by owner and realm; ownerless legacy data stays quarantined."""
from __future__ import annotations
import json
import sqlite3
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
