"""Bounded ephemeral stream state; authenticated chat service owns every read.

Only provider-visible reasoning is held here. Prompts, answers, tool arguments
and source rows never enter this cache. Durable events stay in ChatRunStore.
"""
from copy import deepcopy
import threading
import time

from .chat_runs import _reasoning, MAX_REASONING_EVENT


class ChatProgress:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.entries = {}

    def begin(self, key, *, run_id, model, thinking):
        with self.lock:
            now = self.clock()
            self.entries = {k: v for k, v in self.entries.items() if now - v["updated"] < 900}
            if len(self.entries) >= 256:
                finished = next((k for k, v in self.entries.items() if v["terminal"]), None)
                if finished is None:
                    return
                del self.entries[finished]
            self.entries[key] = {"run_id": run_id, "model": model, "thinking": thinking,
                "status": "running", "terminal": False, "next_sequence": 1,
                "updated": now, "call": 0, "raw": None, "truncated": False}

    def event(self, key, kind):
        with self.lock:
            value = self.entries.get(key)
            if value is None:
                return
            if kind == "model.start":
                value.update(call=value["call"] + 1, raw="", truncated=False)
            elif kind == "model.end":
                value["raw"] = None
            value.update(next_sequence=value["next_sequence"] + 1, updated=self.clock())

    def reasoning(self, key, delta):
        if type(delta) is not str:
            return
        with self.lock:
            value = self.entries.get(key)
            if value is None or value["terminal"] or value["raw"] is None:
                return
            # Hold a short trailing window so credentials split between token
            # chunks are redacted as a whole before anything is displayed.
            maximum = MAX_REASONING_EVENT + 96
            size = len(value["raw"]) + len(delta)
            value["raw"] = (value["raw"] + delta)[:maximum]
            value.update(truncated=value["truncated"] or size > maximum,
                         next_sequence=value["next_sequence"] + 1, updated=self.clock())

    def finish(self, key, status):
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                value.update(status=status, terminal=True, raw=None,
                             next_sequence=value["next_sequence"] + 1, updated=self.clock())

    def read(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value is None or self.clock() - value["updated"] >= 900:
                return None
            result = {k: deepcopy(v) for k, v in value.items()
                      if k not in {"raw", "updated", "call", "truncated"}}
            active = None
            if value["raw"]:
                visible = value["raw"][:-96] if len(value["raw"]) > 96 else ""
                text, truncated, redacted = _reasoning(visible, MAX_REASONING_EVENT)
                if text:
                    active = {"call": value["call"], "text": text,
                              "truncated": truncated or value["truncated"], "redacted": redacted}
            result["active_reasoning"] = active
            return result
