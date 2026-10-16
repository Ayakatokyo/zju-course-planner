"""Task-hint contract checks using invented conversations; no network or sources."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_tasks import (MAX_CONTEXT_BYTES, MAX_TASKS, MAX_SUPPLEMENTS,
    previous_task_context, validate_task_context, next_clarification_context,
    previous_clarification_context, validate_clarification_context)


def record(*, operation="academic", term="2023-2024-AW", query="", mode="summary", **patches):
    row = {"name": "query_my_records",
           "arguments": {"operation": operation, "query": query, "mode": mode},
           "result": {"status": "complete_within_scope", "operation": operation,
                      "target_term": term, "mode": mode, "data_source": "live"}}
    row.update(patches)
    return row


def state(*rows, status="completed"):
    return {"turns": [{"status": status, "tool_results": list(rows)}]}


class PersonalTaskTests(unittest.TestCase):
    @staticmethod
    def clarify(question="哪个学年？", basis="current_message"):
        return {"status": "completed", "reply_origin": "personal_model_clarification",
                "reply": question, "model_decision": {"kind": "clarify", "request_basis": basis}}

    def test_pending_clarification_keeps_actual_question_and_intent_only(self):
        value = next_clarification_context("春夏成绩概况", self.clarify(),
            task_context=previous_task_context(state(record())))
        self.assertEqual(value["request"], "春夏成绩概况")
        self.assertEqual(value["question"], "哪个学年？")
        self.assertFalse(value["facts_reusable"])
        turn = {**self.clarify(), "task_status": "awaiting_user", "pending_clarification": value,
                "tool_results": [{"records": "NEVER-CARRY"}]}
        copied = previous_clarification_context({"turns": [turn]})
        self.assertEqual(copied, value)
        copied["question"] = "changed"
        self.assertEqual(value["question"], "哪个学年？")
        self.assertNotIn("NEVER-CARRY", str(copied))

    def test_repeated_supplements_are_bounded_and_topic_change_replaces_request(self):
        pending = next_clarification_context("查课表", self.clarify())
        for index in range(MAX_SUPPLEMENTS):
            pending = next_clarification_context(f"补充{index}",
                self.clarify("还有哪个范围？", "pending_task"), previous=pending)
            self.assertEqual(len(pending["supplements"]), index + 1)
        self.assertIsNone(next_clarification_context("再补充", self.clarify(basis="pending_task"), previous=pending))
        replaced = next_clarification_context("换个问题，查考试", self.clarify("哪个学期？"), previous=pending)
        self.assertEqual(replaced["request"], "换个问题，查考试")
        self.assertEqual(replaced["supplements"], [])
        self.assertIsNone(replaced["previous_task"])

    def test_completion_failure_or_nonlatest_pending_cannot_resurrect_task(self):
        pending = next_clarification_context("查课表", self.clarify())
        for response in ({"status": "failed"}, {"status": "completed", "reply_origin": "personal_model_evidence"},
                         {**self.clarify(), "model_decision": {"kind": "general_chat"}}):
            self.assertIsNone(next_clarification_context("取消", response, previous=pending))
        earlier = {**self.clarify(), "task_status": "awaiting_user", "pending_clarification": pending}
        self.assertIsNone(previous_clarification_context({"turns": [earlier, {"status": "completed"}]}))
        self.assertIsNone(next_clarification_context("继续", self.clarify(basis="pending_task")))

    def test_pending_contract_rejects_facts_privilege_sensitive_and_oversized_trails(self):
        value = next_clarification_context("查课表", self.clarify())
        invalid = [[], {}, {**value, "records": []}, {**value, "authorization_granted": 0},
                   {**value, "facts_reusable": True}, {**value, "fresh_sources_required": 1},
                   {**value, "supplements": ["补充"] * (MAX_SUPPLEMENTS + 1)},
                   {**value, "question": "q" * 601}, {**value, "request": "https://example.invalid/private"},
                   {**value, "supplements": ["Cookie: PRIVATE"]}, {**value, "previous_task": {"records": []}}]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "^invalid_clarification_context$"):
                    validate_clarification_context(candidate)
        self.assertIsNone(next_clarification_context("密码是PRIVATE", self.clarify()))
        self.assertIsNone(next_clarification_context("查课表", self.clarify("请打开/Users/private")))

    def test_previous_actual_scope_and_mode_survive_without_facts(self):
        row = record()
        row["arguments"].update(page_token="PAGE-deadbeef-1", limit=15)
        row["result"].update(records=[{"grade_raw": "SYNTHETIC-PRIVATE-GRADE"}],
                             observation_id="OBS-SYNTHETIC-PRIVATE", next_page_token="SYNTHETIC-TOKEN",
                             source_alias="SOURCE-SYNTHETIC-PRIVATE", matched_records=100,
                             source_grade_summary={"numeric_mean_unweighted": "SYNTHETIC-PRIVATE"})
        value = previous_task_context(state(row))
        self.assertEqual(value["tasks"], [{"tool": "query_my_records", "operation": "academic",
                         "target_term": "2023-2024-AW", "query": "", "mode": "summary"}])
        self.assertFalse(value["authorization_granted"])
        self.assertFalse(value["facts_reusable"])
        self.assertTrue(value["fresh_sources_required"])
        self.assertNotIn("SYNTHETIC", json.dumps(value))
        self.assertNotIn("grade_raw", json.dumps(value))

    def test_generic_latest_turn_does_not_resurrect_old_scope(self):
        value = state(record())
        value["turns"].append({"status": "completed", "message": "换个话题", "tool_results": [
            {"name": "get_my_context", "arguments": {}, "result": {"status": "available"}}]})
        self.assertIsNone(previous_task_context(value))

    def test_failed_latest_uses_only_its_successful_queries(self):
        older = state(record(term="2022-2023-AW"))
        latest = state(record(term="2023-2024-SS"), status="failed")["turns"][0]
        latest["tool_results"].append(record(error=True))
        older["turns"].append(latest)
        value = previous_task_context(older)
        self.assertEqual(value["turn_status"], "failed")
        self.assertEqual([task["target_term"] for task in value["tasks"]], ["2023-2024-SS"])
        older["turns"][-1]["tool_results"] = []
        self.assertIsNone(previous_task_context(older))

    def test_last_turn_clarification_does_not_reuse_unexecuted_scope(self):
        row = record()
        row["result"].update(status="not_ready", query_executed=False)
        value = state(record())
        value["turns"].append(state(row)["turns"][0])
        self.assertIsNone(previous_task_context(value))

    def test_multiple_scopes_preserve_recent_unique_tasks_without_page_tokens(self):
        rows = [record(term=f"{year}-{year + 1}-AW") for year in range(2016, 2024)]
        repeated = deepcopy(rows[-1])
        repeated["arguments"]["page_token"] = "PAGE-deadbeef-2"
        rows.append(repeated)
        value = previous_task_context(state(*rows))
        self.assertEqual(len(value["tasks"]), MAX_TASKS)
        self.assertTrue(value["tasks_truncated"])
        self.assertEqual([task["target_term"] for task in value["tasks"]],
                         [f"{year}-{year + 1}-AW" for year in range(2018, 2024)])
        self.assertNotIn("page_token", json.dumps(value))

    def test_repeated_scope_with_different_query_or_mode_remains_separate(self):
        value = previous_task_context(state(record(), record(query="合成算法", mode="records"), record(mode="records")))
        self.assertEqual(len(value["tasks"]), 3)
        self.assertEqual(value["tasks"][1]["query"], "合成算法")

    def test_search_hints_keep_executed_query_only_including_zero_matches(self):
        curriculum = {"name": "search_my_curriculum", "arguments": {"query": "专业选修要求", "limit": 5},
                      "result": {"status": "pending_review", "matches": [{"text": "SYNTHETIC-PRIVATE"}]}}
        catalog = {"name": "search_course_catalog", "arguments": {"query": "合成算法", "limit": 10},
                   "result": {"status": "no_match_in_scope", "records": []}}
        value = previous_task_context(state(curriculum, catalog))
        self.assertEqual(value["tasks"], [{"tool": "search_my_curriculum", "query": "专业选修要求"},
                                        {"tool": "search_course_catalog", "query": "合成算法"}])
        self.assertNotIn("SYNTHETIC", json.dumps(value))

    def test_invalid_scope_operation_or_mismatched_execution_is_omitted(self):
        cases = [record(term="2023-2025-AW"), record(term="2023-2024-秋冬"),
                 record(operation="academic", term="ALL-HISTORY"),
                 record(operation="exams", term="2023-2024-AW"),
                 record(operation="timetable", term="2023-2024-A"), record(operation="unknown"),
                 record(mode="unknown")]
        mismatch = record()
        mismatch["arguments"]["target_term"] = "2023-2024-SS"
        cases.append(mismatch)
        mismatch = record()
        mismatch["arguments"]["operation"] = "timetable"
        cases.append(mismatch)
        mismatch = record()
        mismatch["arguments"]["mode"] = "records"
        cases.append(mismatch)
        for row in cases:
            with self.subTest(row=row):
                self.assertIsNone(previous_task_context(state(row)))

    def test_major_history_and_individual_exam_terms_are_valid(self):
        rows = [record(operation="major_academic", term="ALL-HISTORY")]
        rows.extend(record(operation="exams", term=f"2023-2024-{season}", mode="records")
                    for season in ("A", "W", "S", "SU"))
        value = previous_task_context(state(*rows))
        self.assertEqual(len(value["tasks"]), 5)
        self.assertFalse(value["tasks_truncated"])

    def test_query_sensitive_content_controls_and_oversized_strings_are_dropped(self):
        for query in ("Cookie: SYNTHETIC", "token=SYNTHETIC", "Bearer SYNTHETIC", "/Users/SYNTHETIC/file",
                      "https://example.invalid/private", "OBS-deadbeefdeadbeef", "PAGE-deadbeef-1",
                      "我的密码是SYNTHETIC", "学号2099123456", 'source_subject_id":"SYNTHETIC',
                      "ｐａｓｓｗｏｒｄ=SYNTHETIC", "合成\x00算法", "合成\n算法", "课" * 101,
                      "\ud800"):
            with self.subTest(query=repr(query)):
                self.assertIsNone(previous_task_context(state(record(query=query))))

    def test_malformed_inputs_are_ignored_without_copying_messages_or_diagnostics(self):
        for malformed in (None, [], {}, {"turns": None}, {"turns": [None]},
                          {"turns": [{"status": "running", "tool_results": [record()]}]},
                          {"turns": [{"status": "completed", "tool_results": {}}]}):
            with self.subTest(malformed=malformed):
                self.assertIsNone(previous_task_context(malformed))
        rows = [None, [], {"name": "get_my_run_diagnostics", "arguments": {},
                          "result": {"status": "available", "diagnostic": "SYNTHETIC-PRIVATE"}},
                record(result={"status": "unavailable"}), record(arguments=None),
                record(name=[]), record(result={"status": []}), record(operation=[]), record(mode=[])]
        self.assertIsNone(previous_task_context(state(*rows)))

    def test_projection_is_detached_and_bounded(self):
        rows = [{"name": "search_my_curriculum", "arguments": {"query": chr(0x1F300 + index) * 400},
                 "result": {"status": "pending_review"}} for index in range(12)]
        original = state(*rows)
        before = deepcopy(original)
        value = previous_task_context(original)
        self.assertEqual(original, before)
        self.assertLessEqual(len(value["tasks"]), MAX_TASKS)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()), MAX_CONTEXT_BYTES)
        value["tasks"][0]["query"] = "changed"
        self.assertEqual(original, before)

    def test_validator_accepts_none_and_returns_detached_strict_context(self):
        self.assertIsNone(validate_task_context(None))
        value = previous_task_context(state(record()))
        validated = validate_task_context(value)
        self.assertEqual(validated, value)
        self.assertIsNot(validated, value)
        self.assertIsNot(validated["tasks"], value["tasks"])
        validated["tasks"][0]["query"] = "changed"
        self.assertEqual(value["tasks"][0]["query"], "")

    def test_validator_rejects_extra_or_missing_fields_at_both_levels(self):
        value = previous_task_context(state(record()))
        invalid = []
        for key in value:
            changed = deepcopy(value)
            del changed[key]
            invalid.append(changed)
        for key in ("records", "citations", "model_output", "owner_student_id"):
            changed = deepcopy(value)
            changed[key] = "SYNTHETIC-PRIVATE"
            invalid.append(changed)
            changed = deepcopy(value)
            changed["tasks"][0][key] = "SYNTHETIC-PRIVATE"
            invalid.append(changed)
        for key in value["tasks"][0]:
            changed = deepcopy(value)
            del changed["tasks"][0][key]
            invalid.append(changed)
        self.assert_invalid_contexts(invalid)

    def test_validator_rejects_wrong_enums_types_privilege_flags_and_task_bounds(self):
        value = previous_task_context(state(record()))
        invalid = [[], "SYNTHETIC-PRIVATE", {}]
        for key, item in (("source", "other"), ("turn_status", []), ("turn_status", "running"),
                          ("authorization_granted", True), ("authorization_granted", 0),
                          ("facts_reusable", True), ("facts_reusable", 0),
                          ("fresh_sources_required", False), ("fresh_sources_required", 1),
                          ("tasks_truncated", 0), ("tasks", []), ("tasks", {}),
                          ("tasks", value["tasks"] * (MAX_TASKS + 1))):
            changed = deepcopy(value)
            changed[key] = item
            invalid.append(changed)
        for key, item in (("tool", []), ("tool", "get_my_run_diagnostics"), ("operation", []),
                          ("operation", "unknown"), ("target_term", []), ("target_term", "2023-2024-秋冬"),
                          ("mode", []), ("mode", "unknown"), ("query", []), ("query", "Cookie: SYNTHETIC")):
            changed = deepcopy(value)
            changed["tasks"][0][key] = item
            invalid.append(changed)
        duplicate = deepcopy(value)
        duplicate["tasks"] *= 2
        invalid.append(duplicate)
        self.assert_invalid_contexts(invalid)

    def test_validator_accepts_search_contract_and_rejects_extra_search_fields(self):
        for tool in ("search_my_curriculum", "search_course_catalog"):
            value = previous_task_context(state(record()))
            value["tasks"] = [{"tool": tool, "query": "合成课程要求"}]
            self.assertEqual(validate_task_context(value), value)
            maximum = 400 if tool == "search_my_curriculum" else 200
            for query in ("", "课" * (maximum + 1), "source_subject_id=SYNTHETIC"):
                changed = deepcopy(value)
                changed["tasks"][0]["query"] = query
                self.assert_invalid_contexts([changed])
            value["tasks"][0]["source_alias"] = "SYNTHETIC-PRIVATE"
            self.assert_invalid_contexts([value])

    def test_validator_rejects_utf8_budget_overflow_without_truncating_query(self):
        value = previous_task_context(state(record()))
        value["tasks"] = [{"tool": "search_my_curriculum", "query": chr(0x1F300 + index) * 400}
                          for index in range(MAX_TASKS)]
        self.assertGreater(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()), MAX_CONTEXT_BYTES)
        self.assert_invalid_contexts([value])

    def assert_invalid_contexts(self, contexts):
        for value in contexts:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "^invalid_task_context$"):
                    validate_task_context(value)


if __name__ == "__main__":
    unittest.main()
