"""Task-hint contract checks using invented conversations; no network or sources."""
from copy import deepcopy
import json
import unittest

from course_assistant.personal_tasks import (MAX_CONTEXT_BYTES, MAX_TASKS, MAX_SUPPLEMENTS,
    previous_task_context, validate_task_context, next_clarification_context,
    previous_clarification_context, validate_clarification_context, validate_recommendation_task,
    PREPARE_RECOMMENDATION_TOOL, validate_task_progress, previous_preparation_context,
    validate_previous_preparation_context)
from course_assistant.recommendation_readiness import assess_recommendation_readiness


def record(*, operation="academic", term="2023-2024-AW", query="", mode="summary", **patches):
    row = {"name": "query_my_records",
           "arguments": {"operation": operation, "query": query, "mode": mode},
           "result": {"status": "complete_within_scope", "operation": operation,
                      "target_term": term, "mode": mode, "data_source": "live"}}
    row.update(patches)
    return row


def state(*rows, status="completed"):
    return {"turns": [{"status": status, "tool_results": list(rows)}]}


def recommendation_task(**changes):
    return {"recommendation_kind": "timetable", "target_term": "2026-2027-AW",
            "course_scope": {"kind": "topic", "queries": ["数据分析"]},
            "hard_preferences": ["周五不排课"], "soft_preferences": [], "check_exam_conflicts": False,
            "request_basis": "current_message", **changes}


def recommendation_row(name=PREPARE_RECOMMENDATION_TOOL, arguments=None):
    arguments = recommendation_task() if arguments is None else arguments
    check = {key: arguments[key] for key in ("recommendation_kind", "target_term") if key in arguments}
    return {"name": name, "arguments": deepcopy(arguments), "result": {"status": "available",
        "data_kind": "personal_source", "check_executed": True,
        "readiness": assess_recommendation_readiness({}, **check)}}


class PreviousPreparationContextTests(unittest.TestCase):
    @staticmethod
    def blocked(*rows, **changes):
        return {"turns": [{"status": "completed", "task_status": "blocked", "tool_results": list(rows), **changes}]}

    def test_previous_blocker_keeps_only_strict_historical_check_and_not_source_bodies(self):
        row = recommendation_row()
        row["result"].update(evidence_id="PRIVATE-EVIDENCE", prepared_sources=[{"result": {"records": ["PRIVATE-RECORD"]}}])
        row["result"]["readiness"]["sources"][3].update(status="partial", diagnostics={
            "record_count": 3, "reported_total": "unknown", "coverage": "unknown", "row_failure_count": 0,
            "time_text_missing_count": 2, "structured_time_present_count": 0, "time_evidence_missing_count": 2})
        source = self.blocked(row, user_message="PRIVATE-QUESTION", owner_student_id="PRIVATE-OWNER")
        value = previous_preparation_context(source)
        self.assertEqual(set(value), {"source", "historical_only", "fresh_sources_required", "readiness"})
        self.assertTrue(value["historical_only"])
        self.assertTrue(value["fresh_sources_required"])
        self.assertEqual(value["readiness"]["sources"][3]["diagnostics"]["time_evidence_missing_count"], 2)
        self.assertNotIn("PRIVATE-", json.dumps(value))
        value["readiness"]["sources"][3]["diagnostics"]["record_count"] = 0
        self.assertEqual(row["result"]["readiness"]["sources"][3]["diagnostics"]["record_count"], 3)

    def test_latest_turn_only_and_latest_valid_check_are_used(self):
        older = recommendation_row()
        check = recommendation_row("check_recommendation_inputs", {"recommendation_kind": "courses"})
        bad = deepcopy(older)
        bad["result"]["readiness"]["can_generate"] = True
        value = previous_preparation_context(self.blocked(older, check, bad))
        self.assertEqual(value["readiness"]["recommendation_kind"], "courses")
        self.assertIsNone(value["readiness"]["target_term"])
        latest = self.blocked()["turns"][0]
        self.assertIsNone(previous_preparation_context({"turns": [self.blocked(older)["turns"][0], latest]}))

    def test_only_terminal_blocked_turns_provide_history_including_legacy_readiness(self):
        row = recommendation_row()
        for status in ("completed", "failed"):
            value = previous_preparation_context(self.blocked(row, status=status))
            self.assertEqual(value["readiness"], row["result"]["readiness"])
        progress = {"kind": "recommendation", "status": "blocked", "scope": "formal", "reason": "source_unavailable"}
        self.assertIsNotNone(previous_preparation_context(self.blocked(row, task_status="failed", task_progress=progress)))
        for changes in ({"status": "running"}, {"status": "cancelled"}, {"task_status": "partial"},
                        {"task_status": "completed", "task_progress": {"status": "blocked"}}):
            self.assertIsNone(previous_preparation_context(self.blocked(row, **changes)))

    def test_failed_or_inconsistent_tools_cannot_supply_previous_readiness(self):
        for changes in ({"error": True}, {"arguments": {"recommendation_kind": "courses"}},
                        {"name": "query_my_records"}):
            row = recommendation_row()
            row.update(changes)
            self.assertIsNone(previous_preparation_context(self.blocked(row)))
        row = recommendation_row()
        row["result"]["query_executed"] = False
        self.assertIsNone(previous_preparation_context(self.blocked(row)))
        self.assertIsNone(previous_preparation_context({"turns": "PRIVATE-CORRUPT"}))

    def test_validator_rejects_raw_fields_current_source_claims_and_changed_gates(self):
        value = previous_preparation_context(self.blocked(recommendation_row()))
        invalid = [None, {}, {**value, "historical_only": False}, {**value, "fresh_sources_required": False},
            {**value, "source": "current_turn"}, {**value, "records": ["PRIVATE-RECORD"]}]
        for field, replacement in (("can_generate", True), ("next_step", "PRIVATE-TEXT"),
                                   ("owner_student_id", "PRIVATE-OWNER")):
            altered = deepcopy(value)
            altered["readiness"][field] = replacement
            invalid.append(altered)
        for altered in invalid:
            with self.assertRaisesRegex(ValueError, "^invalid_previous_preparation_context$"):
                validate_previous_preparation_context(altered)
        self.assertEqual(validate_previous_preparation_context(value), value)


class PersonalTaskTests(unittest.TestCase):
    def test_table_followup_keeps_scope_and_filter_without_local_rows(self):
        row = record(operation="major_academic", term="ALL-HISTORY", query="合成关键词", mode="table")
        predicate = {"kind": "unknown"}
        row["arguments"]["grade_filter"] = predicate
        row["result"].update(grade_filter=predicate, record_table=[{"record": "PRIVATE-ROW"}])
        value = previous_task_context(state(row))
        self.assertEqual(validate_task_context(value), value)
        self.assertEqual(value["tasks"][0]["mode"], "table")
        self.assertEqual(value["tasks"][0]["grade_filter"], predicate)
        self.assertNotIn("PRIVATE-ROW", json.dumps(value))

    def test_grade_predicate_survives_intent_projection_without_source_facts(self):
        row = record(operation="major_academic", term="ALL-HISTORY", query="合成关键词", mode="records")
        predicate = {"kind": "numeric", "op": "gte", "value": 80}
        row["arguments"]["grade_filter"] = deepcopy(predicate)
        row["result"].update(grade_filter=deepcopy(predicate), records=[{"grade_raw": "PRIVATE"}])
        value = previous_task_context(state(row))
        self.assertEqual(value["tasks"][0]["grade_filter"], predicate)
        self.assertEqual(validate_task_context(value), value)
        self.assertNotIn("PRIVATE", json.dumps(value))
        for bad in ({"kind": "unknown"}, None, {"kind": "numeric", "value": "PRIVATE"}):
            altered = deepcopy(row)
            altered["result"]["grade_filter"] = bad
            self.assertIsNone(previous_task_context(state(altered)))
        for bad in (None, {"kind": "numeric", "op": "gte", "value": 101}):
            altered = deepcopy(value)
            altered["tasks"][0]["grade_filter"] = bad
            with self.assertRaisesRegex(ValueError, "invalid_task_context"):
                validate_task_context(altered)

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

    def test_exact_failed_retry_restores_only_immediately_preceding_clarification_intent(self):
        pending = next_clarification_context("帮我安排课表，周五不要有课", self.clarify("安排哪些课程？"))
        scope = {"personal_scope": "synthetic-owner", "versions": {"egress_digest": "synthetic-egress"}}
        earlier = {**self.clarify(), **scope, "task_status": "awaiting_user", "pending_clarification": pending}
        message = "挑三门管理类的课，凑够6学分。"
        failed = {**scope, "status": "failed", "task_status": "failed", "code": "model_unavailable",
                  "reply_origin": "service_error", "message": message, "pending_clarification": None,
                  "tool_results": [{"records": "FAILED-FACT-NEVER-REUSE"}]}
        trail = {"turns": [earlier, failed]}
        restored = previous_clarification_context(trail, retry_message=message)
        self.assertEqual(restored, pending)
        self.assertNotIn("FAILED-FACT", str(restored))
        self.assertIsNone(previous_clarification_context(trail))
        self.assertIsNone(previous_clarification_context(trail, retry_message="换成两门"))
        for patch in ({"code": "personal_input_changed"}, {"status": "completed"},
                      {"personal_scope": "other-owner"}, {"versions": {"egress_digest": "other-egress"}}):
            with self.subTest(patch=patch):
                self.assertIsNone(previous_clarification_context(
                    {"turns": [earlier, {**failed, **patch}]}, retry_message=message))
        self.assertIsNone(previous_clarification_context(
            {"turns": [earlier, {"status": "completed"}, failed]}, retry_message=message))

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

    def test_recommendation_task_keeps_goal_scope_and_explicit_preferences_without_readiness_facts(self):
        row = recommendation_row()
        row["result"].update(evidence_id="P-NEVER-CARRY", source_alias="SOURCE-NEVER-CARRY")
        row["result"]["private_records"] = [{"grade_raw": "NEVER-CARRY"}]
        value = previous_task_context(state(row))
        self.assertEqual(value["tasks"], [{"tool": PREPARE_RECOMMENDATION_TOOL, **recommendation_task()}])
        self.assertNotIn("NEVER-CARRY", json.dumps(value))
        self.assertNotIn("readiness", json.dumps(value))
        self.assertFalse(value["authorization_granted"])
        self.assertTrue(value["fresh_sources_required"])

    def test_check_only_task_stays_distinct_and_can_leave_term_unresolved(self):
        for args in ({"recommendation_kind": "courses"},
                     {"recommendation_kind": "timetable", "target_term": "2026-2027-AW"}):
            hint = previous_task_context(state(recommendation_row("check_recommendation_inputs", args)))
            self.assertEqual(hint["tasks"], [{"tool": "check_recommendation_inputs",
                "recommendation_kind": args["recommendation_kind"], "target_term": args.get("target_term")}])
            self.assertNotIn("course_scope", hint["tasks"][0])

    def test_recheck_does_not_replace_prepared_intent_and_late_queries_cannot_erase_it(self):
        prepared = recommendation_row()
        check = recommendation_row("check_recommendation_inputs",
            {"recommendation_kind": "timetable", "target_term": "2026-2027-AW"})
        rows = [prepared, check] + [record(query=f"合成课程{index}") for index in range(MAX_TASKS)]
        value = previous_task_context(state(*rows))
        self.assertEqual(len(value["tasks"]), MAX_TASKS)
        self.assertTrue(value["tasks_truncated"])
        self.assertIn({"tool": PREPARE_RECOMMENDATION_TOOL, **recommendation_task()}, value["tasks"])
        self.assertNotIn("check_recommendation_inputs", [task["tool"] for task in value["tasks"]])

    def test_recommendation_execution_must_match_actual_kind_term_and_fixed_gates(self):
        for key, changed in (("target_term", "2025-2026-AW"), ("recommendation_kind", "courses"),
                             ("can_generate", True), ("can_save_or_export", True),
                             ("status", "ready"), ("owner_student_id", "NEVER-CARRY")):
            row = recommendation_row()
            row["result"]["readiness"][key] = changed
            self.assertIsNone(previous_task_context(state(row)))
        failed = recommendation_row()
        failed["error"] = True
        self.assertIsNone(previous_task_context(state(failed)))

    def test_recommendation_validator_requires_explicit_bounded_scope_and_safe_intent(self):
        original = recommendation_task()
        minimal = deepcopy(original)
        del minimal["check_exam_conflicts"]
        del minimal["soft_preferences"]
        del minimal["request_basis"]
        self.assertEqual(validate_recommendation_task(minimal), original)
        for kind in ("courses", "curriculum_module", "topic"):
            value = recommendation_task(course_scope={"kind": kind, "queries": ["合成范围"]})
            detached = validate_recommendation_task(value)
            detached["course_scope"]["queries"].append("changed")
            self.assertEqual(value["course_scope"]["queries"], ["合成范围"])
        invalid = [None, {}, recommendation_task(target_term=None), recommendation_task(target_term="本学期"),
            recommendation_task(target_term="2026-2028-AW"), recommendation_task(check_exam_conflicts=1),
            recommendation_task(recommendation_kind="courses", check_exam_conflicts=True),
            recommendation_task(course_scope={"kind": "topic", "queries": []}),
            recommendation_task(course_scope={"kind": "topic", "queries": ["范围"] * 2}),
            recommendation_task(course_scope={"kind": "topic", "queries": [str(i) for i in range(7)]}),
            recommendation_task(course_scope={"kind": "topic", "queries": ["Cookie: NEVER-CARRY"]}),
            recommendation_task(hard_preferences=["/Users/NEVER-CARRY"]),
            recommendation_task(hard_preferences=["同一条件"] * 2),
            recommendation_task(hard_preferences=["条件"] * 7),
            recommendation_task(soft_preferences=["Cookie: NEVER-CARRY"]),
            recommendation_task(soft_preferences=["偏好"] * 7),
            recommendation_task(soft_preferences=["尽量上午"] * 2),
            recommendation_task(soft_preferences=["周五不排课"]),
            recommendation_task(request_basis="previous_turn"), recommendation_task(request_basis=True),
            recommendation_task(pending_messages=["不能由模型附带上一轮原话"]),
            recommendation_task(authorization_granted=True), recommendation_task(records=[])]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "^invalid_recommendation_task$"):
                    validate_recommendation_task(value)

    def test_recommendation_pending_basis_is_preserved_as_intent_and_never_embeds_history(self):
        args = recommendation_task(request_basis="pending_task")
        hint = previous_task_context(state(recommendation_row(arguments=args)))
        self.assertEqual(hint["tasks"][0]["request_basis"], "pending_task")
        self.assertNotIn("pending_messages", hint["tasks"][0])
        self.assertFalse(hint["facts_reusable"])

    def test_soft_preferences_remain_distinct_intent_and_never_become_hard(self):
        args = recommendation_task(hard_preferences=[], soft_preferences=["尽量下午上课", "优先数据分析"])
        value = previous_task_context(state(recommendation_row(arguments=args)))
        self.assertEqual(value["tasks"][0]["hard_preferences"], [])
        self.assertEqual(value["tasks"][0]["soft_preferences"], args["soft_preferences"])
        self.assertFalse(value["facts_reusable"])

    def test_task_progress_has_fixed_states_reasons_and_no_formal_completion(self):
        self.assertIsNone(validate_task_progress(None))
        for status, scope, reason in (("prepared", "formal", "source_prepared"),
                ("limited_result", "course_data_test", "test_result"),
                ("blocked", "formal", "formal_dependencies_unverified"),
                ("blocked", "course_data_test", "source_unavailable")):
            value = {"kind": "recommendation", "status": status, "scope": scope, "reason": reason}
            copied = validate_task_progress(value)
            self.assertEqual(copied, value)
            self.assertIsNot(copied, value)
        original = {"kind": "recommendation", "status": "limited_result", "scope": "course_data_test", "reason": "test_result"}
        invalid = [[], {}, {**original, "status": "completed"}, {**original, "scope": "formal"},
            {**original, "reason": "source_prepared"}, {**original, "reason": "Cookie: PRIVATE"},
            {**original, "kind": "answer"}, {**original, "can_generate": True},
            {**original, "reason": []}]
        for value in invalid:
            with self.assertRaisesRegex(ValueError, "^invalid_task_progress$"):
                validate_task_progress(value)

    def test_recommendation_context_rejects_extra_facts_or_noncanonical_normalized_intent(self):
        value = previous_task_context(state(recommendation_row()))
        invalid = []
        for key, item in (("records", []), ("locked_section_ids", ["NEVER-CARRY"]),
                          ("can_generate", True), ("target_term", "2026-2027-A")):
            copied = deepcopy(value)
            copied["tasks"][0][key] = item
            invalid.append(copied)
        copied = deepcopy(value)
        del copied["tasks"][0]["check_exam_conflicts"]
        invalid.append(copied)
        self.assert_invalid_contexts(invalid)

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
