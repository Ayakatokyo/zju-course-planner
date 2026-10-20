"""Bounded, session-scoped drafts kept only in this server process's memory.

Text becomes unavailable after thirty minutes. Any draft access purges all
expired text; there is no background erasure thread. Revision tombstones remain
until close so delayed writes cannot resurrect cleared or expired content.
This service does not read school data, create chats, persist text or call a
model. Its caller supplies only a trusted local identity key.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
import uuid

from .auth import AccessError


class PersonalDraftService:
    def __init__(self, sensitive, *, clock=time.time, ttl_seconds=1800, capacity=128):
        self.sensitive, self.clock = sensitive, clock
        self.ttl_seconds, self.capacity = ttl_seconds, capacity
        self._lock = threading.RLock()
        self._entries = {}
        self._closed = False

    def close(self):
        with self._lock:
            self._entries.clear()
            self._closed = True

    def _require_open(self):
        if self._closed:
            raise AccessError("草稿服务已结束", 503, "personal_draft_unavailable")

    @staticmethod
    def _key(key):
        if type(key) is not tuple or len(key) != 5 or any(type(value) is not str or not value or len(value) > 512 for value in key):
            raise AccessError("草稿身份范围不可用", 409, "personal_scope_unavailable")

    def validate(self, body):
        if (type(body) is not dict or set(body) != {"text", "conversation_id", "expected_revision"}
                or type(body["text"]) is not str or len(body["text"]) > 2000 or "\x00" in body["text"]
                or type(body["expected_revision"]) is not int or body["expected_revision"] < 0):
            raise AccessError("草稿字段或版本无效", 400, "invalid_personal_draft")
        identifier = body["conversation_id"]
        if identifier is not None:
            try:
                if type(identifier) is not str or str(uuid.UUID(identifier)) != identifier:
                    raise ValueError
            except (ValueError, AttributeError):
                raise AccessError("草稿对话编号无效", 400, "invalid_personal_draft") from None
        try:
            body["text"].encode("utf-8")
        except UnicodeError:
            raise AccessError("草稿内容格式无效", 400, "invalid_personal_draft") from None
        if self.sensitive.search(body["text"]):
            raise AccessError("请移除密码、凭据或真实学号后重试", 400, "sensitive_chat_input")

    @staticmethod
    def _empty(revision=0):
        return {"text": "", "conversation_id": None, "revision": revision, "expires_at": None}

    def _expire(self, key, now):
        row = self._entries.get(key)
        if row and row["expires"] is not None and now >= row["expires"]:
            row = {"value": self._empty(row["value"]["revision"] + 1), "expires": None}
            self._entries[key] = row
        return row

    def _expire_all(self, now):
        # At most 128 scopes. Old authorization generations need not be read
        # again for their expired text to be cleared; keep their revisions.
        for key in self._entries:
            self._expire(key, now)

    def read(self, key, authorize):
        self._key(key)
        authorize()
        with self._lock:
            authorize()
            self._require_open()
            self._expire_all(self.clock())
            row = self._entries.get(key)
            if row is None:
                if len(self._entries) >= self.capacity:
                    raise AccessError("当前草稿会话容量已满", 503, "personal_draft_capacity")
                # Establish revision 0 before an autosave is in flight, so a
                # subsequent accepted send can invalidate that delayed write.
                row = {"value": self._empty(), "expires": None}
                self._entries[key] = row
            result = deepcopy(row["value"])
            authorize()
            return result

    def write(self, key, body, authorize):
        self._key(key)
        self.validate(body)
        authorize()
        with self._lock:
            authorize()
            self._require_open()
            now = self.clock()
            self._expire_all(now)
            row = self._entries.get(key)
            revision = row["value"]["revision"] if row else 0
            if body["expected_revision"] != revision:
                raise AccessError("草稿已在其他页面更新，请重新读取", 409, "personal_draft_revision_conflict")
            if row is None and len(self._entries) >= self.capacity:
                # A live session's cleared/expired revision is still needed.
                # Never evict it and accidentally admit an old revision 0.
                raise AccessError("当前草稿会话容量已满", 503, "personal_draft_capacity")
            expires = now + self.ttl_seconds if body["text"] else None
            value = ({"text": body["text"], "conversation_id": body["conversation_id"], "revision": revision + 1,
                      "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
                     if expires is not None else self._empty(revision + 1))
            authorize()
            self._entries[key] = {"value": value, "expires": expires}
            return deepcopy(value)

    def consume(self, key, conversation_id, message):
        """Clear only the draft matching a request the chat store accepted.

        This internal operation receives the already verified reservation's
        scope; it does not expose text or acquire any additional authority.
        A different message or conversation retains its text and expiry, but
        every existing entry advances its revision to fence delayed saves.
        """
        self._key(key)
        with self._lock:
            if self._closed:
                return False
            self._expire_all(self.clock())
            row = self._entries.get(key)
            if not row:
                return False
            matched = (bool(row["value"]["text"]) and row["value"]["conversation_id"] == conversation_id
                       and row["value"]["text"].strip() == message.strip())
            value = self._empty(row["value"]["revision"] + 1) if matched else {**row["value"], "revision": row["value"]["revision"] + 1}
            self._entries[key] = {"value": value, "expires": None if matched else row["expires"]}
            return matched
