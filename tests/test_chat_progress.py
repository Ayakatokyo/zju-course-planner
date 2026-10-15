"""Bounded ephemeral progress-cache tests; all text and owners are synthetic."""
import json
import unittest

from course_assistant.chat_progress import ChatProgress


class ChatProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.progress = ChatProgress(clock=lambda: self.now)
        self.key = ("personal_school_client", "SYNTHETIC-A", "session-a", "binding-1", "scope-1", "request-1")

    def begin(self, key=None):
        self.progress.begin(key or self.key, run_id="RUN-SYNTHETIC", model="synthetic-model", thinking=True)

    def test_owner_realm_session_binding_and_scope_keys_are_independent(self):
        self.begin()
        self.progress.event(self.key, "model.start")
        self.progress.reasoning(self.key, "合成公开推理。" * 40)
        self.assertIsNotNone(self.progress.read(self.key)["active_reasoning"])
        for index in range(len(self.key)):
            other = list(self.key)
            other[index] += "-other"
            self.assertIsNone(self.progress.read(tuple(other)))
        view = self.progress.read(self.key)
        view["active_reasoning"]["text"] = "changed by reader"
        self.assertNotEqual(self.progress.read(self.key)["active_reasoning"]["text"], "changed by reader")

    def test_lifecycle_uses_only_actual_active_reasoning_and_monotonic_sequence(self):
        self.begin()
        start = self.progress.read(self.key)
        self.assertEqual((start["status"], start["terminal"], start["next_sequence"]), ("running", False, 1))
        self.progress.reasoning(self.key, "ignored before model.start")
        self.assertIsNone(self.progress.read(self.key)["active_reasoning"])
        self.progress.event(self.key, "model.start")
        self.progress.reasoning(self.key, "短片段先保留在脱敏尾窗")
        self.assertIsNone(self.progress.read(self.key)["active_reasoning"])
        self.progress.reasoning(self.key, "合成推理" * 60)
        active = self.progress.read(self.key)
        self.assertGreater(active["next_sequence"], start["next_sequence"])
        self.assertEqual(active["active_reasoning"]["call"], 1)
        self.progress.event(self.key, "model.end")
        self.assertIsNone(self.progress.read(self.key)["active_reasoning"])
        self.progress.finish(self.key, "completed")
        final = self.progress.read(self.key)
        self.assertTrue(final["terminal"])
        self.assertEqual(final["status"], "completed")
        self.progress.reasoning(self.key, "late " * 100)
        self.assertEqual(self.progress.read(self.key), final)

    def test_credentials_split_across_chunks_are_redacted_before_display(self):
        for chunks, secret in ((["pass", "word=SYNTHETIC-SECRET\n"], "SYNTHETIC-SECRET"),
                               (["Bearer SYN", "THETIC-ACCESS-TOKEN\n"], "SYNTHETIC-ACCESS-TOKEN"),
                               (["sk-synt", "heticlongcredential123\n"], "syntheticlongcredential123")):
            self.begin()
            self.progress.event(self.key, "model.start")
            for chunk in ["合成前言。" * 30] + chunks + ["\n安全后文。" * 40]:
                self.progress.reasoning(self.key, chunk)
                self.assertNotIn(secret, json.dumps(self.progress.read(self.key)))
            active = self.progress.read(self.key)["active_reasoning"]
            self.assertTrue(active["redacted"])
            self.assertIn("已隐藏", active["text"])

    def test_structured_requests_paths_and_oversize_reasoning_never_escape_cache_view(self):
        self.begin()
        self.progress.event(self.key, "model.start")
        self.progress.reasoning(self.key, '{"messages":[{"content":"SYNTHETIC-PRIVATE-ROW"}]}' + "x" * 300)
        self.assertNotIn("SYNTHETIC-PRIVATE-ROW", json.dumps(self.progress.read(self.key)))
        self.progress.event(self.key, "model.start")
        self.progress.reasoning(self.key, "/Users/synthetic/private.pdf\n" + "合成安全文本。" * 2000)
        active = self.progress.read(self.key)["active_reasoning"]
        self.assertTrue(active["truncated"])
        self.assertLessEqual(len(active["text"]), 4096)
        self.assertNotIn("/Users/", active["text"])
        self.assertNotIn('"raw"', json.dumps(self.progress.read(self.key)))

    def test_unfinished_private_key_block_is_hidden_during_stream(self):
        self.begin()
        self.progress.event(self.key, "model.start")
        self.progress.reasoning(self.key, "合成公开说明。" * 20)
        self.progress.reasoning(self.key, "-----BEGIN PRIVATE KEY-----\nSYNTHETIC-SECRET-KEY-BODY" + "a" * 160)
        active = self.progress.read(self.key)["active_reasoning"]
        self.assertTrue(active["redacted"])
        self.assertNotIn("SYNTHETIC-SECRET-KEY-BODY", active["text"])
        self.assertIn("凭据已隐藏", active["text"])

    def test_ttl_boundary_is_900_seconds_and_new_begin_prunes_stale_entries(self):
        self.begin()
        self.now = 899.999
        self.assertIsNotNone(self.progress.read(self.key))
        self.now = 900
        self.assertIsNone(self.progress.read(self.key))
        second = (*self.key[:-1], "request-2")
        self.begin(second)
        self.assertNotIn(self.key, self.progress.entries)
        self.assertIsNotNone(self.progress.read(second))

    def test_capacity_is_bounded_and_only_finished_entries_are_evicted(self):
        keys = [(*self.key[:-1], f"request-{index}") for index in range(257)]
        for key in keys[:256]:
            self.begin(key)
        self.begin(keys[-1])
        self.assertEqual(len(self.progress.entries), 256)
        self.assertIsNone(self.progress.read(keys[-1]))
        self.progress.finish(keys[0], "completed")
        self.begin(keys[-1])
        self.assertEqual(len(self.progress.entries), 256)
        self.assertIsNone(self.progress.read(keys[0]))
        self.assertIsNotNone(self.progress.read(keys[-1]))

    def test_invalid_deltas_and_unknown_keys_do_not_create_progress(self):
        self.begin()
        self.progress.event(self.key, "model.start")
        before = self.progress.read(self.key)
        for value in (None, 3, {"private": "synthetic"}):
            self.progress.reasoning(self.key, value)
        self.assertEqual(self.progress.read(self.key), before)
        self.progress.event("unknown", "model.start")
        self.progress.reasoning("unknown", "synthetic")
        self.progress.finish("unknown", "completed")
        self.assertIsNone(self.progress.read("unknown"))


if __name__ == "__main__":
    unittest.main()
