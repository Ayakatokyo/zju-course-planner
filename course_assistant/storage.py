"""SQLite resources queried by owner and realm; ownerless legacy data stays quarantined."""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from .importer import digest


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def chat_title(value):
    """A bounded display label from this conversation's first user request."""
    if not isinstance(value, str):
        return "新对话"
    title = ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', value).split())
    return (title[:59] + '…') if len(title) > 60 else title or "新对话"


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
                CREATE TABLE IF NOT EXISTS owned_recommendation_actions (
                    realm TEXT NOT NULL, owner TEXT NOT NULL, action_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, payload_digest TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, request TEXT NOT NULL,
                    response TEXT NOT NULL, PRIMARY KEY(realm,owner,action_id),
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

    def recommendation_action(self, context, action_id):
        """Read an owned receipt before rebuilding a command after its commit."""
        with self.connect() as db:
            row = db.execute("SELECT request_digest,request,response FROM owned_recommendation_actions "
                             "WHERE realm=? AND owner=? AND action_id=?",
                             (context.security_realm, context.student_id, action_id)).fetchone()
        return {"request_digest": row[0], "request": json.loads(row[1]),
                "response": json.loads(row[2])} if row else None

    def save_chat_option(self, context, conversation_id, expected_turn,
                         expected_workspace_versions, option_ref, action_id,
                         frozen_body, new_constraints, updated_state, *, final_chat_request=None):
        """Commit a server-resolved preview, workspace and conversation together.

        All payloads here are prepared by the authorized server, never accepted
        as client facts. Re-read the owned source state under the write lock;
        the server's earlier checks alone do not prevent a concurrent update.
        """
        from .auth import AccessError
        realm, owner = context.security_realm, context.student_id
        if (type(expected_turn) is not int or expected_turn < 0 or
                not isinstance(option_ref, dict) or set(option_ref) != {"run_id", "option_id"} or
                any(not isinstance(value, str) or not value for value in option_ref.values()) or
                not isinstance(action_id, str) or not action_id or len(action_id) > 100 or
                not all(isinstance(value, dict) for value in
                        (expected_workspace_versions, frozen_body, new_constraints, updated_state))):
            raise AccessError("推荐保存参数无效", 400, "invalid_recommendation_action")
        if final_chat_request is not None and (
                not isinstance(final_chat_request, dict) or set(final_chat_request) != {"request_id", "response"} or
                not isinstance(final_chat_request["request_id"], str) or not final_chat_request["request_id"] or
                not isinstance(final_chat_request["response"], dict) or
                final_chat_request["response"].get("conversation_id") != conversation_id or
                type(final_chat_request["response"].get("turn_id")) is not int or
                final_chat_request["response"]["turn_id"] != expected_turn):
            raise AccessError("保存的对话回执参数无效", 400, "invalid_chat_amendment")
        request = {"conversation_id": conversation_id, "expected_turn": expected_turn,
                   "option_ref": deepcopy(option_ref)}
        request_digest = digest(request)
        payload_digest = digest({"workspace_versions": expected_workspace_versions,
                                 "frozen_body": frozen_body, "constraints": new_constraints,
                                 "state": updated_state, "final_chat_request": final_chat_request})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT request_digest,payload_digest,response FROM owned_recommendation_actions "
                                  "WHERE realm=? AND owner=? AND action_id=?", (realm, owner, action_id)).fetchone()
            if previous:
                if previous[:2] != (request_digest, payload_digest):
                    raise AccessError("同一动作编号不能用于不同保存请求", 409, "action_id_reused")
                return {**json.loads(previous[2]), "replayed": True}
            conversation = db.execute("SELECT turn,state,pending_request FROM owned_conversations "
                                      "WHERE id=? AND realm=? AND owner=?", (conversation_id, realm, owner)).fetchone()
            if conversation is None:
                raise AccessError("资源不可访问", 404, "inaccessible_object")
            if conversation[2] is not None:
                raise AccessError("该对话正在执行另一轮", 409, "turn_in_progress")
            if conversation[0] != expected_turn:
                raise AccessError("对话已更新，请重新载入", 409, "turn_version_changed")
            workspace = db.execute("SELECT d.body,w.constraints_body,d.digest FROM workspaces w "
                                   "JOIN owned_datasets d ON d.realm=w.realm AND d.owner=w.owner "
                                   "AND d.digest=w.dataset_digest WHERE w.realm=? AND w.owner=?", (realm, owner)).fetchone()
            if workspace is None:
                raise AccessError("资源不可访问", 404, "inaccessible_object")
            dataset, constraints = json.loads(workspace[0]), json.loads(workspace[1])
            actual = {"dataset_sha256": workspace[2], "constraints_revision": constraints.get("revision"),
                      "constraints_sha256": digest(constraints)}
            if (digest(dataset) != workspace[2] or
                    any(expected_workspace_versions.get(key) != value for key, value in actual.items())):
                raise AccessError("工作区已变化，请按当前数据重新计算", 409, "workspace_changed")
            state = json.loads(conversation[1])
            chat_request = None
            if final_chat_request is not None:
                chat_request = self._completed_chat_request(db, context, conversation_id,
                                                           final_chat_request["request_id"], expected_turn, state)
            runs = state.get("recommendation_runs", [])
            matching = [run for run in runs if isinstance(run, dict) and run.get("run_id") == option_ref["run_id"]]
            if len(matching) != 1:
                raise AccessError("推荐结果不可访问", 404, "inaccessible_option")
            run = matching[0]
            options = [option for option in run.get("options", []) if isinstance(option, dict)
                       and option.get("option_id") == option_ref["option_id"]
                       and option.get("run_id") == option_ref["run_id"]]
            if len(options) != 1:
                raise AccessError("推荐结果不可访问", 404, "inaccessible_option")
            option = options[0]
            if (state.get("workspace_versions") != expected_workspace_versions or
                    run.get("workspace_versions") != expected_workspace_versions or run.get("is_stale") or
                    option.get("is_stale") or run.get("intent_revision") !=
                    state.get("recommendation_intent", {}).get("revision")):
                raise AccessError("推荐结果已失效，请重新校验", 409, "stale_recommendation")
            versions = frozen_body.get("versions", {})
            plan = frozen_body.get("plan", {})
            revision_fields = {"constraints_revision", "constraints_sha256"}
            if (frozen_body.get("owner_student_id") != owner or frozen_body.get("security_realm") != realm or
                    frozen_body.get("data_kind") != "synthetic" or frozen_body.get("not_official_enrollment") is not True or
                    digest(frozen_body.get("frozen_dataset")) != workspace[2] or
                    frozen_body.get("constraints") != new_constraints or
                    not isinstance(plan, dict) or plan.get("section_ids") != option.get("section_ids") or
                    not isinstance(plan.get("section_ids"), list) or not plan["section_ids"] or
                    not isinstance(versions, dict) or versions.get("dataset_sha256") != workspace[2] or
                    {key: value for key, value in versions.items() if key not in revision_fields} !=
                    {key: value for key, value in expected_workspace_versions.items() if key not in revision_fields} or
                    versions.get("constraints_revision") != new_constraints.get("revision") or
                    versions.get("constraints_sha256") != digest(new_constraints) or
                    plan.get("versions") != versions or
                    type(new_constraints.get("revision")) is not int or
                    new_constraints["revision"] <= constraints.get("revision", 0)):
                raise AccessError("保存内容与服务端推荐结果不一致", 409, "invalid_frozen_recommendation")
            next_state = deepcopy(updated_state)
            # Only the chosen option is revalidated at the new baseline. Other
            # runs remain historical and cannot silently become current options.
            selected = []
            for next_run in next_state.get("recommendation_runs", []):
                next_run["is_stale"] = True
                for next_option in next_run.get("options", []):
                    next_option["is_stale"] = True
                    if (next_run.get("run_id") == option_ref["run_id"] and
                            next_option.get("option_id") == option_ref["option_id"] and
                            next_option.get("run_id") == option_ref["run_id"]):
                        selected.append((next_run, next_option))
            if len(selected) != 1:
                raise AccessError("保存状态缺少已选择的服务端结果", 409, "invalid_recommendation_state")
            selected_run, selected_option = selected[0]
            if final_chat_request is not None:
                last = next_state.get("turns", [])[-1:] or [None]
                if not isinstance(last[0], dict) or last[0].get("turn_id") != expected_turn:
                    raise AccessError("保存状态缺少当前对话轮", 409, "invalid_recommendation_state")
            created = datetime.now(timezone.utc).isoformat()
            changed = db.execute("UPDATE workspaces SET constraints_body=? WHERE realm=? AND owner=? "
                                 "AND dataset_digest=? AND constraints_body=?",
                                 (encoded(new_constraints), realm, owner, workspace[2], workspace[1]))
            if changed.rowcount != 1:
                raise AccessError("工作区已变化，请重新计算", 409, "workspace_changed")
            cur = db.execute("INSERT INTO owned_plans(realm,owner,body,created_at) VALUES(?,?,?,?)",
                             (realm, owner, encoded(frozen_body), created))
            saved = self._saved_row((cur.lastrowid, encoded(frozen_body), created))
            next_state.update(workspace_versions=deepcopy(versions), preview_constraints=deepcopy(new_constraints))
            selected_run.update(workspace_versions=deepcopy(versions), constraints=deepcopy(new_constraints), is_stale=False)
            selected_option.update(deepcopy(plan), option_id=option_ref["option_id"], run_id=option_ref["run_id"],
                                   versions=deepcopy(versions), constraints=deepcopy(new_constraints), is_stale=False,
                                   saved_id=saved["saved_id"], saved_versions=deepcopy(versions))
            response = {"saved_plan": saved, "action_id": action_id, "replayed": False}
            if final_chat_request is not None:
                final_response = {**deepcopy(final_chat_request["response"]), "status": "completed",
                                  "versions": deepcopy(versions), "saved_action": deepcopy(saved),
                                  "reply": "已按明确指令保存推荐方案；这是合成规划版本，不表示正式中选。",
                                  "reply_origin": "verified_explicit_action", "action_pending": False}
                next_state["turns"][-1].update(final_response)
                changed = db.execute("UPDATE owned_chat_requests SET status='complete',response=? "
                                     "WHERE realm=? AND owner=? AND request_id=? AND conversation_id=? "
                                     "AND status='complete' AND response=?",
                                     (encoded(final_response), realm, owner, final_chat_request["request_id"],
                                      conversation_id, chat_request))
                if changed.rowcount != 1:
                    raise AccessError("对话回执已变化", 409, "turn_version_changed")
                response["chat_response"] = final_response
            changed = db.execute("UPDATE owned_conversations SET state=?,updated_at=? WHERE id=? AND realm=? AND owner=? "
                                 "AND turn=? AND state=? AND pending_request IS NULL",
                                 (encoded(next_state), created, conversation_id, realm, owner, expected_turn, conversation[1]))
            if changed.rowcount != 1:
                raise AccessError("对话版本已变化", 409, "turn_version_changed")
            db.execute("INSERT INTO owned_recommendation_actions VALUES(?,?,?,?,?,?,?,?)",
                       (realm, owner, action_id, request_digest, payload_digest, conversation_id,
                        encoded(request), encoded(response)))
            return response

    @staticmethod
    def _completed_chat_request(db, context, conversation_id, request_id, expected_turn, state):
        """Authorize a receipt by its owned conversation and completed turn."""
        from .auth import AccessError
        row = db.execute("SELECT status,response FROM owned_chat_requests "
                         "WHERE realm=? AND owner=? AND request_id=? AND conversation_id=?",
                         (context.security_realm, context.student_id, request_id, conversation_id)).fetchone()
        if row is None:
            raise AccessError("对话请求不可访问", 404, "inaccessible_chat_request")
        if row[0] != "complete":
            raise AccessError("对话请求尚未正常完成", 409, "chat_request_not_complete")
        previous = json.loads(row[1])
        last = state.get("turns", [])[-1:] or [None]
        if (previous.get("conversation_id") != conversation_id or type(previous.get("turn_id")) is not int or
                previous["turn_id"] != expected_turn or not isinstance(last[0], dict) or
                last[0].get("conversation_id") != conversation_id or type(last[0].get("turn_id")) is not int or
                last[0]["turn_id"] != expected_turn):
            raise AccessError("回执不属于当前对话轮", 409, "turn_version_changed")
        return row[1]

    def amend_chat_response(self, context, conversation_id, request_id, expected_turn, response):
        """Amend an export/failure receipt without writing planning inputs."""
        from .auth import AccessError
        if (type(expected_turn) is not int or expected_turn < 0 or not isinstance(request_id, str) or not request_id or
                not isinstance(response, dict) or response.get("conversation_id") != conversation_id or
                type(response.get("turn_id")) is not int or response["turn_id"] != expected_turn or
                response.get("status") not in {"completed", "failed"}):
            raise AccessError("对话回执参数无效", 400, "invalid_chat_amendment")
        realm, owner = context.security_realm, context.student_id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT turn,state,pending_request FROM owned_conversations "
                             "WHERE id=? AND realm=? AND owner=?", (conversation_id, realm, owner)).fetchone()
            if row is None:
                raise AccessError("资源不可访问", 404, "inaccessible_object")
            if row[2] is not None:
                raise AccessError("该对话正在执行另一轮", 409, "turn_in_progress")
            if row[0] != expected_turn:
                raise AccessError("对话已更新", 409, "turn_version_changed")
            state = json.loads(row[1])
            previous = self._completed_chat_request(db, context, conversation_id, request_id, expected_turn, state)
            state["turns"][-1].update(deepcopy(response))
            changed = db.execute("UPDATE owned_chat_requests SET status=?,response=? WHERE realm=? AND owner=? "
                                 "AND request_id=? AND conversation_id=? AND status='complete' AND response=?",
                                 ("complete" if response["status"] == "completed" else "failed", encoded(response),
                                  realm, owner, request_id, conversation_id, previous))
            if changed.rowcount != 1:
                raise AccessError("对话回执已变化", 409, "turn_version_changed")
            changed = db.execute("UPDATE owned_conversations SET state=?,updated_at=? WHERE id=? AND realm=? AND owner=? "
                                 "AND turn=? AND state=? AND pending_request IS NULL",
                                 (encoded(state), datetime.now(timezone.utc).isoformat(), conversation_id,
                                  realm, owner, expected_turn, row[1]))
            if changed.rowcount != 1:
                raise AccessError("对话版本已变化", 409, "turn_version_changed")
            return deepcopy(response)

    def chat_list(self, context, limit=50):
        """Project only owned summary fields; never return entire tool history."""
        from .auth import AccessError
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AccessError("历史列表数量须为 1 至 100", 400, "invalid_chat_limit")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,turn,pending_request,updated_at,"
                "COALESCE(json_extract(state,'$.title'),json_extract(state,'$.turns[0].message')) "
                "FROM owned_conversations WHERE realm=? AND owner=? "
                "ORDER BY updated_at DESC,id DESC LIMIT ?",
                (context.security_realm, context.student_id, limit + 1)).fetchall()
        return {"conversations": [{"conversation_id": row[0], "title": chat_title(row[4]),
                                    "turn_id": row[1], "updated_at": row[3], "in_progress": row[2] is not None}
                                   for row in rows[:limit]],
                "limit": limit, "has_more": len(rows) > limit, "data_kind": "synthetic"}

    def chat_option_receipts(self, context, identifier, limit=100):
        """Read minimal completed save receipts in the same owner/realm domain."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT json_extract(response,'$.saved_plan.saved_id'),"
                "json_extract(request,'$.option_ref.option_id'),json_extract(request,'$.option_ref.run_id'),"
                "json_extract(response,'$.saved_plan.versions') FROM owned_recommendation_actions "
                "WHERE realm=? AND owner=? AND conversation_id=? ORDER BY rowid DESC LIMIT ?",
                (context.security_realm, context.student_id, identifier, limit + 1)).fetchall()
        receipts = {}
        for saved_id, option_id, run_id, versions in rows[:limit]:
            if type(saved_id) is int and saved_id > 0 and isinstance(option_id, str) and option_id not in receipts:
                receipts[option_id] = {"saved_id": saved_id, "option_id": option_id, "run_id": run_id,
                                       "versions": json.loads(versions) if isinstance(versions, str) else {}}
        return receipts, len(rows) > limit

    def chat_one(self, context, identifier):
        with self.connect() as db:
            row = db.execute("SELECT turn,state,pending_request,updated_at FROM owned_conversations "
                             "WHERE id=? AND realm=? AND owner=?",
                             (identifier, context.security_realm, context.student_id)).fetchone()
        return {"conversation_id": identifier, "turn_id": row[0], "state": json.loads(row[1]),
                "in_progress": row[2] is not None, "updated_at": row[3]} if row else None

    def chat_begin(self, context, identifier, request_id, request_digest, expected_turn, *, first_message=None):
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
            if "title" not in state:
                first = state.get("turns", [])[:1]
                state["title"] = chat_title(first[0].get("message") if first else first_message)
            db.execute("UPDATE owned_conversations SET pending_request=?,state=?,updated_at=? "
                       "WHERE id=? AND realm=? AND owner=?",
                       (request_id, encoded(state), datetime.now(timezone.utc).isoformat(), identifier, realm, owner))
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
            run_id = response.get("run_id")
            if (type(run_id) is str and re.fullmatch(r"RUN-[a-f0-9]{32}", run_id)
                    and type(response.get("personal_scope")) is str
                    and response["status"] in {"completed", "failed"}
                    and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_chat_runs'").fetchone()):
                # This is cleanup of this exact server-reserved request, not a
                # read or renewed authorization. Bind every owner/scope/request
                # field, and never let a run ID finalize a different request.
                db.execute("UPDATE personal_chat_runs SET status=?,error_code=?,updated_at=? "
                           "WHERE id=? AND realm=? AND owner=? AND scope_version=? "
                           "AND conversation_id=? AND request_id=? AND status='running'",
                           (response["status"], response.get("code") if response["status"] == "failed" else None,
                            datetime.now(timezone.utc).timestamp(), run_id, context.security_realm,
                            context.student_id, sha256(response["personal_scope"].encode("utf-8")).hexdigest(),
                            identifier, request_id))
