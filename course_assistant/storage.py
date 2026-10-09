"""Versioned SQLite storage. Inputs and imported JSON bodies are immutable."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .importer import digest


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    digest TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    body TEXT NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS entities (
                    dataset_digest TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(dataset_digest,kind,entity_id),
                    FOREIGN KEY(dataset_digest) REFERENCES datasets(digest));
                CREATE TABLE IF NOT EXISTS saved_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_dataset(self, kind, body):
        key = digest(body)
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?,?)", (
                key, kind, json.dumps(body, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
            inserted = bool(cursor.rowcount)
            sets = {"plans": "plan_id", "courses": "course_id", "candidates": "rule_id", "issues": "issue_id", "rules": "rule_id"}
            for entity_kind, id_field in sets.items():
                for row in body.get(entity_kind, []):
                    db.execute("INSERT OR IGNORE INTO entities VALUES(?,?,?,?)", (
                        key, entity_kind, row[id_field], json.dumps(row, ensure_ascii=False)))
            for snapshot_kind in ("academic_snapshot", "offering_snapshot"):
                snapshot = body.get(snapshot_kind)
                if snapshot:
                    db.execute("INSERT OR IGNORE INTO entities VALUES(?,?,?,?)", (
                        key, snapshot_kind, snapshot["snapshot_id"], json.dumps(snapshot, ensure_ascii=False)))
        return {"dataset_sha256": key, "inserted": inserted}

    def save(self, body):
        created = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cur = db.execute("INSERT INTO saved_plans(body,created_at) VALUES(?,?)", (json.dumps(body, ensure_ascii=False), created))
            identifier = cur.lastrowid
        return {**body, "saved_id": identifier, "saved_revision": identifier,
                "created_at": created, "status": "saved_planning_only"}

    def saved(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,body,created_at FROM saved_plans ORDER BY id DESC").fetchall()
        return [{**json.loads(body), "saved_id": key, "saved_revision": key, "created_at": created,
                 "status": "saved_planning_only"} for key, body, created in rows]
