"""Offline boundaries for the development-only Harness² text surface."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from course_assistant import personal_dialogue as dialogue
from validation.harness2_adapter import AcademicAdapter, TextProposer, apply_text_edits, snapshot, use_candidate, validate_candidate
from validation.harness2_cases import FrozenSchool, TERM, mechanical_checks


class Harness2AdapterTests(unittest.TestCase):
    def test_existing_proposal_receipt_is_not_overwritten_or_regenerated(self):
        class ProposerError(RuntimeError):
            def __init__(self, message, **kwargs):
                super().__init__(message)
                self.attempts = kwargs['attempts']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / 'inv1.a1.json'
            receipt.write_text('{"original":"failed attempt"}')
            original = receipt.read_bytes()
            factory = Mock(side_effect=AssertionError('must not call the model again'))
            proposer = TextProposer(factory, snapshot(), root)
            module = SimpleNamespace(Reply=None, ProposerError=ProposerError)
            with patch.dict('sys.modules', {'harness2.core.proposer': module}):
                with self.assertRaises(ProposerError) as caught:
                    proposer.call('retry', root / 'missing-context', 'inv1.a1')
            self.assertEqual(caught.exception.attempts, 0)
            self.assertEqual(receipt.read_bytes(), original)
            factory.assert_not_called()

    def test_failed_execution_has_no_deliverable_files(self):
        adapter = object.__new__(AcademicAdapter)
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing'
            self.assertFalse(adapter.delivered(missing))
            self.assertEqual(adapter.deliverable_files(missing), {})

    def test_only_existing_text_surfaces_are_editable(self):
        base = snapshot()
        for name in ("../systemprompt.md", "guardrails.md", "scripts/run.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                apply_text_edits(base, [{"path": name, "old": "x", "new": "y"}])
        bad = deepcopy(base)
        descriptions = json.loads(bad["tool_descriptions/tool_guidance.md"])
        descriptions["run_shell"] = "not allowed"
        bad["tool_descriptions/tool_guidance.md"] = json.dumps(descriptions)
        with self.assertRaises(ValueError):
            validate_candidate(bad, base)

    def test_candidate_is_process_local_and_schemas_cannot_change(self):
        base, before = snapshot(), deepcopy(dialogue.TOOLS)
        candidate = apply_text_edits(base, [{"path": "systemprompt.md", "old": "使用中文", "new": "用中文"}])
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with use_candidate(candidate, base):
                self.assertEqual(dialogue.SYSTEM, candidate["systemprompt.md"])
                self.assertEqual(dialogue.TOOLS, before)
                raise RuntimeError("test failure")
        self.assertEqual(snapshot(), base)
        self.assertEqual(dialogue.TOOLS, before)

    def test_ambiguous_or_missing_replacements_are_rejected(self):
        for old in ("不存在的原文", "本轮"):
            with self.subTest(old=old), self.assertRaises(ValueError):
                apply_text_edits(snapshot(), [{"path": "systemprompt.md", "old": old, "new": "changed"}])

    def test_fixed_sources_preserve_duplicate_names_unknowns_and_filter_scope(self):
        school = FrozenSchool()
        result = school.execute("query_my_records", {"operation": "academic", "target_term": TERM, "mode": "table", "query": "数据分析"})
        self.assertEqual(len(result["records"]), 2)
        self.assertIsNone(result["records"][1]["grade_raw"])
        result = school.execute("query_my_records", {"operation": "academic", "target_term": TERM, "mode": "table",
            "grade_filter": {"kind": "numeric", "op": "gte", "value": 90}})
        self.assertEqual(result["source_record_indices"], [0, 4])
        local, remote = dialogue.PersonalDialogueService._project("query_my_records", result,
            {"operation": "academic", "target_term": TERM, "mode": "table", "query": "", "limit": 15,
             "grade_filter": {"kind": "numeric", "op": "gte", "value": 90}}, "synthetic", {}, 0, [0, 0, {}], set())
        self.assertEqual(remote["record_table"]["displayed_records"], 2)
        audit = school.execute("query_my_records", {"operation": "graduation_audit", "query": "实践教学环节"})
        self.assertEqual(audit["target_term"], "ALL-HISTORY")
        self.assertEqual(len(audit["records"]), 3)
        with self.assertRaises(ValueError):
            school.execute("query_my_records", {"operation": "academic", "target_term": "2025-2026-SS"})

    def test_completed_without_requested_delivery_is_not_a_pass(self):
        checks = mechanical_checks({"id": "D01"}, {"status": "completed", "citations": []}, [])
        self.assertTrue(checks["completed"])
        self.assertFalse(checks["table_rows"])
        self.assertFalse(checks["requested_outcome"])


if __name__ == "__main__":
    unittest.main()
