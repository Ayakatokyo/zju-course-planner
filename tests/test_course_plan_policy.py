"""No personal facts or calls: hard-condition coverage and human change scope."""
import unittest

from course_assistant.course_plan_policy import compile_hard_constraints, authorize_changes


class CoursePlanPolicyTests(unittest.TestCase):
    def metadata(self):
        return {"class-one": {"course_code": "SYN-ONE", "course_name": "合成一", "locked": True, "option_positions": {"A": 1}},
                "class-two": {"course_code": "SYN-TWO", "course_name": "合成二", "locked": False, "option_positions": {"A": 2}}}

    def authorize(self, text, patch, evidence=None):
        return authorize_changes(text, patch, prior_constraints={"friday_free": "hard", "target_credits": "6", "max_credits": None},
                                 class_metadata=self.metadata(), change_evidence=evidence)

    def test_omitted_hard_preference_still_blocks_unknown_clock_words(self):
        result = compile_hard_constraints("推荐课程，周三下午必须空，尽量周五空闲。", [])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["unresolved"][0]["code"], "clock_period_mapping_unverified")
        self.assertEqual(result["blocked_slots"], [])

    def test_conversational_period_supplement_uses_only_unique_inherited_weekday(self):
        first = compile_hard_constraints("周三下午必须空", ["周三下午必须空"])
        resumed = compile_hard_constraints("就是第6到8节", ["周三第6到8节不能排课"], inherited_policy=first)
        self.assertEqual(resumed["status"], "ready")
        self.assertEqual(resumed["blocked_slots"][0]["weekday"], 3)
        self.assertEqual(resumed["blocked_slots"][0]["periods"], [6, 7, 8])
        waiting = compile_hard_constraints("再算一次", [], inherited_policy=first)
        self.assertEqual(waiting["status"], "needs_clarification")

    def test_explicit_periods_and_colloquial_whole_day_empty_are_executable(self):
        slots = compile_hard_constraints("周三第6,7,8节不能排课", [])
        self.assertEqual(slots["blocked_slots"][0]["periods"], [6, 7, 8])
        for phrase in ("把周五改为必须空", "周五一定留空", "星期五不能有课"):
            with self.subTest(phrase=phrase):
                self.assertEqual(compile_hard_constraints(phrase, [phrase])["engine_patch"]["friday_free"], "hard")

    def test_unsupported_hard_never_becomes_ready_and_paraphrase_needs_source(self):
        self.assertEqual(compile_hard_constraints("推荐课程", ["必须同校区"])["status"], "needs_clarification")
        self.assertEqual(compile_hard_constraints("课程必须都在同一校区", [])["status"], "needs_clarification")
        self.assertEqual(compile_hard_constraints("周三第6节必须有课", [])["status"], "needs_clarification")

    def test_credit_bounds_are_preserved_and_conflicts_block(self):
        result = compile_hard_constraints("至少六学分，最多八学分", ["至少六学分", "最多八学分"])
        self.assertEqual(result["engine_patch"], {"target_credits": "6", "max_credits": "8"})
        self.assertEqual(compile_hard_constraints("至少八学分，最多六学分", [])["status"], "needs_clarification")
        conflict = compile_hard_constraints("必须至少八学分，最多六学分", ["最多六学分"])
        self.assertEqual(conflict["status"], "needs_clarification")

    def test_soft_preferences_are_nonblocking_and_retained_without_claimed_ranking(self):
        result = compile_hard_constraints("尽量周三下午空，优先数据库，尽量周五空闲", [], soft_preferences=["优先数据库"])
        self.assertEqual(result["status"], "ready")
        self.assertIn("尽量周三下午空", result["unhandled_soft_preferences"])
        self.assertIn("优先数据库", result["unhandled_soft_preferences"])
        self.assertNotIn("尽量周五空闲", result["unhandled_soft_preferences"])
        self.assertEqual(compile_hard_constraints("其他条件保留", [], inherited_policy=result)["unhandled_soft_preferences"], result["unhandled_soft_preferences"])

    def test_model_refs_without_human_unlock_evidence_do_not_authorize(self):
        self.assertEqual(self.authorize("重新算一次", {"unlock_class_refs": ["class-one"]})["status"], "needs_clarification")
        self.assertEqual(self.authorize("重新算一次", {"unlock_class_refs": ["class-one"]},
            [{"field": "unlock_class_refs", "quote": "解除刚才的锁"}])["status"], "needs_clarification")

    def test_natural_and_polite_unlock_requests_are_allowed_without_fixed_sentence(self):
        for phrase in ("解除我刚刚加的那个锁", "可以帮我解开刚才那个锁吗？", "把合成一的锁取消", "合成一不用再保留锁定了"):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.authorize(phrase, {"unlock_class_refs": ["class-one"]})["status"], "ready")

    def test_negative_hypothetical_reported_and_consequence_questions_do_not_authorize(self):
        for phrase in ("不要解除合成一的锁", "如果无解就解锁合成一", "老师说‘解锁合成一’", "解除合成一的锁会有什么影响？"):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.authorize(phrase, {"unlock_class_refs": ["class-one"]})["status"], "needs_clarification")

    def test_denied_intent_does_not_authorize_any_controlled_change(self):
        cases = [
            ("我没有说取消周五必须空的要求", {"friday_free": "none"}),
            ("不是让你解除合成一的锁", {"unlock_class_refs": ["class-one"]}),
            ("不需要解除合成一的锁", {"unlock_class_refs": ["class-one"]}),
            ("我没说目标改成3学分", {"target_credits": 3}),
            ("3学分是我不接受的", {"target_credits": 3}),
            ("不要锁定合成一", {"lock_class_refs": ["class-one"]}),
            ("别排除合成一", {"exclude_class_refs": ["class-one"]}),
            ("我不是要把周五改成尽量空闲", {"friday_free": "soft"}),
            ("最高学分不是10学分", {"max_credits": 10}),
        ]
        for phrase, patch in cases:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.authorize(phrase, patch)["status"], "needs_clarification")

    def test_polite_question_and_independent_positive_clause_still_authorize(self):
        self.assertEqual(self.authorize("能不能帮我解除合成一的锁？", {"unlock_class_refs": ["class-one"]})["status"], "ready")
        self.assertEqual(self.authorize("不要解除合成一，锁定A第二门", {"lock_class_refs": ["class-two"]})["status"], "ready")
        self.assertEqual(self.authorize("不要解除合成一，锁定A第二门", {"unlock_class_refs": ["class-one"]})["status"], "needs_clarification")

    def test_nonasserted_hard_conditions_and_period_supplements_never_create_busy_slots(self):
        for phrase in ("如果周三第6到8节不能排课，会怎样", "我不是说周三第6到8节不能排课"):
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [])
                self.assertEqual(result["blocked_slots"], [])
                self.assertEqual(result["status"], "needs_clarification")
        first = compile_hard_constraints("周三下午必须空", [])
        for phrase in ("如果改成第6到8节会怎样", "不是第6到8节"):
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [], inherited_policy=first)
                self.assertEqual(result["blocked_slots"], [])
                self.assertEqual(result["status"], "needs_clarification")
                self.assertEqual(result["unresolved"][0]["code"], "clock_period_mapping_unverified")

    def test_soft_modality_has_priority_over_embedded_hard_word(self):
        result = compile_hard_constraints("周五不一定空", [])
        self.assertEqual(result["status"], "ready")
        self.assertNotEqual(result["engine_patch"].get("friday_free"), "hard")
        self.assertEqual(result["blocked_slots"], [])

    def test_verified_class_action_consumes_only_its_exact_target_expression(self):
        cases = (("不要选合成二", {"exclude_class_refs": ["class-two"]}),
                 ("必须保留A第一门", {"lock_class_refs": ["class-one"]}))
        for phrase, patch in cases:
            with self.subTest(phrase=phrase):
                proof = self.authorize(phrase, patch)
                self.assertEqual(proof["status"], "ready")
                consumed = [text for row in proof["verified_changes"].values() for text in row["consumed_expressions"]]
                self.assertTrue(consumed)
                compiled = compile_hard_constraints(phrase, [phrase], consumed_expressions=consumed)
                self.assertEqual(compiled["status"], "ready")
                for suffix in ("且周三下午必须空", "且课程必须同校区"):
                    message = phrase + suffix
                    proof = self.authorize(message, patch)
                    consumed = [text for row in proof["verified_changes"].values() for text in row["consumed_expressions"]]
                    compiled = compile_hard_constraints(message, [message], consumed_expressions=consumed)
                    self.assertEqual(compiled["status"], "needs_clarification")
                    self.assertEqual(compiled["blocked_slots"], [])
        with self.assertRaises(ValueError):
            compile_hard_constraints("必须同校区", [], consumed_expressions=["必须保留合成一"])

    def test_normalized_busy_predicates_match_supported_values_without_inventing_scope(self):
        first = compile_hard_constraints("周三第6到8节不能排课", [])
        for predicate in ("禁排", "不排", "留空"):
            preference = "周三第6-8节" + predicate
            with self.subTest(predicate=predicate):
                raw = compile_hard_constraints(preference, [])
                self.assertEqual(raw["status"], "ready")
                self.assertEqual(raw["blocked_slots"][0]["periods"], [6, 7, 8])
                normalized = compile_hard_constraints("其他条件保持", [preference], inherited_policy=first)
                self.assertEqual(normalized["status"], "ready")
                self.assertEqual(len(normalized["blocked_slots"]), 1)
        different = compile_hard_constraints("其他条件保持", ["周三第6-9节禁排"], inherited_policy=first)
        self.assertEqual(different["status"], "needs_clarification")
        hypothetical = compile_hard_constraints("如果周三第6-8节禁排，会怎样", [])
        self.assertEqual(hypothetical["blocked_slots"], [])

    def test_real_failure_protocol_period_supplement_matches_normalized_tool_preference(self):
        pending = compile_hard_constraints("加上周三下午必须空", [])
        result = compile_hard_constraints("就是第6到8节，请按这个禁排时间继续。", ["周三第6-8节禁排"], inherited_policy=pending)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["blocked_slots"][0]["weekday"], 3)
        self.assertEqual(result["blocked_slots"][0]["periods"], [6, 7, 8])

    def test_period_ranges_accept_endpoint_markers_and_lists_as_one_complete_scope(self):
        expressions = ("第6到8节", "第6到第8节", "第6节到第8节", "6节至8节", "第6节—第8节", "第6节-第8节",
                       "第6、7、8节", "第6节、第7节、第8节", "第6节，第7节，第8节", "第6节和第7节及第8节", "第6节, 第7节, 第8节")
        for expression in expressions:
            with self.subTest(expression=expression):
                result = compile_hard_constraints("周三" + expression + "不能排课", [])
                self.assertEqual(result["status"], "ready")
                self.assertEqual(len(result["blocked_slots"]), 1)
                self.assertEqual(result["blocked_slots"][0]["periods"], [6, 7, 8])

    def test_period_parser_never_falls_back_to_a_prefix_for_invalid_or_ambiguous_scope(self):
        invalid = ("第8节到第6节", "第0节到第8节", "第6节到第61节", "第-1节", "第1.5节",
                   "第6节到第8节到第10节", "第1-2节、第6-8节", "第6到8节和第10节", "第6节第8节",
                   "第6第8节", "第6节到", "第6节到未知节", "第6节到第8", "第6节以后")
        for expression in invalid:
            with self.subTest(expression=expression):
                result = compile_hard_constraints("周三" + expression + "不能排课", [])
                self.assertEqual(result["status"], "needs_clarification")
                self.assertEqual(result["blocked_slots"], [])
                pending = compile_hard_constraints("周三下午必须空", [])
                resumed = compile_hard_constraints("就是" + expression, [], inherited_policy=pending)
                self.assertEqual(resumed["status"], "needs_clarification")
                self.assertEqual(resumed["blocked_slots"], [])

    def test_real_failure_repeated_endpoint_units_match_only_the_actual_supplement_range(self):
        pending = compile_hard_constraints("加上周三下午必须空", [])
        message = "就是第6到8节，继续刚才的调整。"
        result = compile_hard_constraints(message, ["周三第6节到第8节不能排课"], inherited_policy=pending)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["blocked_slots"][0]["periods"], [6, 7, 8])
        for preference in ("周三第6节到第9节不能排课", "周三第6节不能排课", "周三第6节、第8节不能排课"):
            with self.subTest(preference=preference):
                rejected = compile_hard_constraints(message, [preference], inherited_policy=pending)
                self.assertEqual(rejected["status"], "needs_clarification")
                self.assertTrue(any(row["code"] == "hard_condition_evidence_required" for row in rejected["unresolved"]))
                self.assertEqual(rejected["blocked_slots"][0]["periods"], [6, 7, 8])

    def test_raw_period_busy_without_weekday_or_model_preference_requires_clarification(self):
        for predicate in ("禁排", "不排", "留空"):
            with self.subTest(predicate=predicate):
                result = compile_hard_constraints("第6节到第8节" + predicate, [])
                self.assertEqual(result["status"], "needs_clarification")
                self.assertEqual(result["blocked_slots"], [])
                self.assertEqual(result["unresolved"][0]["code"], "unsupported_hard_condition")
                explicit = compile_hard_constraints("周四第6节到第8节" + predicate, [])
                self.assertEqual(explicit["status"], "ready")
                self.assertEqual(explicit["blocked_slots"][0]["weekday"], 4)
        missing = compile_hard_constraints("第6节到第8节禁排", [])
        resumed = compile_hard_constraints("就是星期四", ["周四第6-8节禁排"], inherited_policy=missing)
        self.assertEqual(resumed["status"], "ready")
        self.assertEqual(resumed["blocked_slots"][0]["weekday"], 4)
        for text in ("如果改成星期四会怎样", "不是星期四"):
            self.assertEqual(compile_hard_constraints(text, [], inherited_policy=missing)["blocked_slots"], [])

    def test_unscoped_busy_request_uses_only_unique_pending_weekday_without_consuming_other_hard(self):
        pending = compile_hard_constraints("周三下午必须空", [])
        resumed = compile_hard_constraints("第6节到第8节禁排", [], inherited_policy=pending)
        self.assertEqual(resumed["status"], "ready")
        self.assertEqual(resumed["blocked_slots"][0]["weekday"], 3)
        self.assertEqual(resumed["blocked_slots"][0]["periods"], [6, 7, 8])
        other = compile_hard_constraints("周四第6节到第8节禁排", [], inherited_policy=pending)
        self.assertEqual(other["status"], "needs_clarification")
        self.assertEqual([r["weekday"] for r in other["blocked_slots"]], [4])
        blocked = compile_hard_constraints("第6到8节禁排且必须同校区", [], inherited_policy=pending)
        self.assertEqual(blocked["status"], "needs_clarification")
        self.assertTrue(any(r["code"] == "unsupported_hard_condition" for r in blocked["unresolved"]))

    def test_process_obligations_are_execution_contracts_not_schedule_constraints(self):
        phrases = ("必须实际计算，并说明未确认项。", "务必调用已有工具重算", "一定要重新计算并说明来源和未知项",
                   "必须把来源和限制讲清楚", "请务必如实说明哪些未确认", "必须实际调用工具计算")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [])
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["blocked_slots"], [])
                self.assertEqual(result["engine_patch"], {})
        with_time = compile_hard_constraints("必须实际计算并周三第6节到第8节留空", [])
        self.assertEqual(with_time["status"], "ready")
        self.assertEqual(with_time["blocked_slots"][0]["periods"], [6, 7, 8])
        with_credit = compile_hard_constraints("必须实际计算且最多八学分", [])
        self.assertEqual(with_credit["engine_patch"], {"max_credits": "8"})

    def test_process_clause_does_not_consume_business_hard_conditions_or_create_capabilities(self):
        phrases = ("必须实际计算且课程必须同校区", "必须调用已有工具重算且最多8学分且必须正式可选",
                   "必须实际计算出三套方案", "必须保证有解", "必须正式可选", "必须调用终端脚本计算", "必须调用学校选课工具")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [])
                self.assertEqual(result["status"], "needs_clarification")
                self.assertTrue(result["unresolved"])
        combined = compile_hard_constraints("必须实际计算且周三第6到8节留空且最多8学分且必须同校区", [])
        self.assertEqual(combined["status"], "needs_clarification")
        self.assertEqual(combined["blocked_slots"][0]["periods"], [6, 7, 8])
        self.assertEqual(combined["engine_patch"], {"max_credits": "8"})
        self.assertTrue(any(r["code"] == "unsupported_hard_condition" for r in combined["unresolved"]))
        for phrase in ("我不是说必须实际计算", "如果必须实际计算会怎样", "我没说必须说明来源"):
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [])
                self.assertEqual(result["status"], "needs_clarification")
                self.assertEqual(result["enforced_hard_preferences"], [])

    def test_parallel_hard_requirements_preserve_explicit_and_inherited_unknown_conjuncts(self):
        for connector in ("且", "并", "并且", "而且", "同时", "和", "以及", "、"):
            for supported, field, expected in (("周五必须空", "friday_free", "hard"), ("至少6学分", "target_credits", "6")):
                for unsupported in ("必须同校区", "同校区"):
                    phrase = supported + connector + unsupported
                    with self.subTest(phrase=phrase):
                        result = compile_hard_constraints(phrase, [])
                        self.assertEqual(result["status"], "needs_clarification")
                        self.assertEqual(result["engine_patch"][field], expected)
                        self.assertTrue(any(r["code"] == "unsupported_hard_condition" for r in result["unresolved"]))
        multiple = compile_hard_constraints("至少6学分且周三第6到8节禁排且必须正式可选", [])
        self.assertEqual(multiple["status"], "needs_clarification")
        self.assertEqual(multiple["engine_patch"]["target_credits"], "6")
        self.assertEqual(multiple["blocked_slots"][0]["periods"], [6, 7, 8])

    def test_parallel_soft_modals_and_preserve_controls_do_not_become_hard_requirements(self):
        result = compile_hard_constraints("周五必须空且周三尽量空闲且同校区", [])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["engine_patch"], {"friday_free": "hard"})
        self.assertEqual(result["blocked_slots"], [])
        self.assertEqual(result["unhandled_soft_preferences"], ["同校区"])
        self.assertEqual(result["soft_free_slots"], [{"weekday": 3, "periods": None}])
        transition = compile_hard_constraints("至少6学分且最好同校区且必须最多8学分", [])
        self.assertEqual(transition["status"], "ready")
        self.assertEqual(transition["engine_patch"], {"target_credits": "6", "max_credits": "8"})
        self.assertEqual(transition["unhandled_soft_preferences"], ["最好同校区"])
        for control in ("其他条件保留", "其余要求保持不变", "原有偏好不变"):
            preserved = compile_hard_constraints("周五必须空且" + control, [], inherited_policy=result)
            self.assertEqual(preserved["status"], "ready")
            self.assertEqual(preserved["engine_patch"], {"friday_free": "hard"})
            self.assertEqual(preserved["unhandled_soft_preferences"], result["unhandled_soft_preferences"])
        hard_reset = compile_hard_constraints("周三尽量空闲且必须同校区", [])
        self.assertEqual(hard_reset["status"], "needs_clarification")
        self.assertEqual(hard_reset["soft_free_slots"], [{"weekday": 3, "periods": None}])

    def test_soft_free_scopes_cover_all_weekdays_and_explicit_periods_without_clock_guesses(self):
        for day, name in enumerate("一二三四五六日", 1):
            with self.subTest(day=day):
                result = compile_hard_constraints("请尽量让周" + name + "空闲", [])
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["soft_free_slots"], [{"weekday": day, "periods": None}])
                self.assertEqual(result["unhandled_soft_preferences"], [])
        result = compile_hard_constraints("周二第6到8节尽量空，周四第2、4节最好没课，周三下午尽量空", [])
        self.assertEqual(result["soft_free_slots"], [{"weekday": 2, "periods": [6, 7, 8]}, {"weekday": 4, "periods": [2, 4]}])
        self.assertEqual(result["unhandled_soft_preferences"], ["周三下午尽量空"])
        self.assertEqual(result["blocked_slots"], [])

    def test_positive_additive_discourse_markers_compile_only_the_explicit_soft_scope(self):
        for message, day, periods in (("周四也尽量空", 4, None), ("星期二同样最好没课", 2, None),
                ("同样地周日第3到4节也尽量空", 7, [3, 4]), ("另外周一也希望尽量空闲", 1, None),
                ("此外周六尽量空", 6, None), ("还请周三尽量不要排课", 3, None)):
            with self.subTest(message=message):
                result = compile_hard_constraints(message, [], soft_preferences=["周" + "一二三四五六日"[day - 1] + "尽量空"])
                self.assertEqual(result["soft_free_slots"], [{"weekday": day, "periods": periods}])
                self.assertEqual(result["status"], "ready")
                self.assertNotIn(message, result["unhandled_soft_preferences"])
        # A reference to "also" supplies no missing weekday or clock mapping.
        for message in ("也尽量空", "周四下午也尽量空", "周四也尽量同校区"):
            with self.subTest(message=message):
                self.assertEqual(compile_hard_constraints(message, [])["soft_free_slots"], [])

    def test_additive_markers_do_not_override_denial_report_cancellation_or_preservation(self):
        previous = compile_hard_constraints("周三尽量空，周五必须空，周二第1节不能排课", [])
        for message in ("如果周四也尽量空会怎样", "不是要求周四同样尽量空", "老师说‘周四也尽量空’",
                "不用同样让周三尽量空和周四也尽量空", "不要也让周三尽量空和周五也尽量空",
                "周三同样尽量空和周四也尽量空都不要", "取消周三也尽量空和周四也尽量空",
                "周四也尽量空保持不变"):
            with self.subTest(message=message):
                result = compile_hard_constraints(message, [], inherited_policy=previous)
                self.assertEqual(result["soft_free_slots"], previous["soft_free_slots"])
                self.assertEqual(result["blocked_slots"], previous["blocked_slots"])
                self.assertEqual(result["engine_patch"]["friday_free"], "hard")
        removed = compile_hard_constraints("取消周三也尽量空", [], inherited_policy=previous)
        self.assertEqual(removed["soft_free_slots"], [])
        self.assertEqual(removed["engine_patch"]["friday_free"], "hard")
        injected = compile_hard_constraints("其他条件保持", [], inherited_policy=previous, soft_preferences=["周四也尽量空"])
        self.assertEqual(injected["soft_free_slots"], previous["soft_free_slots"])
        self.assertNotIn("周四也尽量空", injected["unhandled_soft_preferences"])

    def test_soft_scope_normalization_does_not_duplicate_overlapping_or_whole_day_requests(self):
        result = compile_hard_constraints("周二第2到4节尽量空，周二第3到6节最好空，周四尽量空，周四第1节尽量空", [])
        self.assertEqual(result["soft_free_slots"], [{"weekday": 2, "periods": [2, 3, 4, 5, 6]}, {"weekday": 4, "periods": None}])
        self.assertEqual(len(result["soft_time_preferences"]), 2)
        retained = compile_hard_constraints("周四尽量空保持不变，其他条件保留", [], inherited_policy=result)
        self.assertEqual(retained["soft_free_slots"], result["soft_free_slots"])
        self.assertEqual(retained["unhandled_soft_preferences"], [])

    def test_soft_scope_changes_require_asserted_human_evidence_and_preserve_hard_constraints(self):
        first = compile_hard_constraints("周三尽量空，周五必须空，周二第1节不能排课", [])
        for phrase in ("其他条件保留", "如果把周三尽量空改为周四尽量空会怎样", "不是让你取消周三尽量空", "老师说‘周四尽量空’"):
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [], inherited_policy=first, soft_preferences=["周日尽量空"])
                self.assertEqual(result["soft_free_slots"], first["soft_free_slots"])
                self.assertEqual(result["blocked_slots"], first["blocked_slots"])
                self.assertEqual(result["engine_patch"]["friday_free"], "hard")
        moved = compile_hard_constraints("把周三尽量空改为周四尽量空，其他条件保留", [], inherited_policy=first)
        self.assertEqual(moved["soft_free_slots"], [{"weekday": 4, "periods": None}])
        self.assertEqual(moved["engine_patch"]["friday_free"], "hard")
        removed = compile_hard_constraints("取消周四尽量空，其他条件保留", [], inherited_policy=moved)
        self.assertEqual(removed["soft_free_slots"], [])
        self.assertEqual(removed["blocked_slots"], first["blocked_slots"])
        injected = compile_hard_constraints("重新计算", [], soft_preferences=["周三尽量空", "周五尽量空"])
        self.assertEqual(injected["soft_free_slots"], [])
        self.assertEqual(injected["engine_patch"], {})
        self.assertEqual(injected["unhandled_soft_preferences"], [])

    def test_model_only_soft_items_are_neither_executed_nor_reported_as_human_wishes(self):
        first = compile_hard_constraints("周五尽量空", [], soft_preferences=["周五尽量空"])
        hard = compile_hard_constraints("周五改为必须空", [], inherited_policy=first, overrides={"friday_free": "hard"})
        current = compile_hard_constraints("周五必须空和已锁定课程都保持，周四也尽量空", [], inherited_policy=hard,
            soft_preferences=["周四尽量空", "周五尽量空", "周日尽量空", "最好同校区"])
        self.assertEqual(current["engine_patch"]["friday_free"], "hard")
        self.assertEqual(current["soft_free_slots"], [{"weekday": 4, "periods": None}])
        self.assertEqual(current["unhandled_soft_preferences"], [])
        self.assertEqual(current["unhandled_soft_source_texts"], [])
        legacy = {"unhandled_soft_preferences": ["周五尽量空", "最好同校区"]}
        self.assertEqual(compile_hard_constraints("继续", [], inherited_policy=legacy)["unhandled_soft_preferences"], [])

    def test_actual_unsupported_raw_wishes_survive_current_pending_and_verified_inheritance(self):
        # The production facade compiles each trusted pending user message
        # before the current one. The model list is only a final hint list.
        pending = compile_hard_constraints("周二下午尽量空，最好同校区", [])
        current = compile_hard_constraints("周四也尽量空，希望上午课少一点", [], inherited_policy=pending,
            soft_preferences=["周四尽量空", "希望上午课少一点", "周五尽量空"])
        self.assertEqual(current["soft_free_slots"], [{"weekday": 4, "periods": None}])
        self.assertEqual(current["unhandled_soft_preferences"], ["周二下午尽量空", "最好同校区", "希望上午课少一点"])
        retained = compile_hard_constraints("其他条件保留", [], inherited_policy=current, soft_preferences=["周五尽量空"])
        self.assertEqual(retained["unhandled_soft_preferences"], current["unhandled_soft_preferences"])
        self.assertEqual(retained["soft_free_slots"], current["soft_free_slots"])

    def test_shared_soft_operations_never_turn_a_conjoined_tail_into_a_new_preference(self):
        previous = compile_hard_constraints("周三尽量空，周五必须空，周二第1节不能排课", [])
        for message in ("取消周三尽量空和周四尽量空", "不是让你取消周三尽量空和周四尽量空",
                "不要取消周三尽量空和周四尽量空", "如果取消周三尽量空和周四尽量空会怎样",
                "老师说‘取消周三尽量空和周四尽量空’", "保留周三尽量空和周四尽量空",
                "周三尽量空和周四尽量空都保留", "把周三尽量空和周四尽量空改为周六尽量空"):
            with self.subTest(message=message):
                result = compile_hard_constraints(message, [], inherited_policy=previous, soft_preferences=["周四尽量空"])
                self.assertEqual(result["soft_free_slots"], previous["soft_free_slots"])
                self.assertEqual(result["blocked_slots"], previous["blocked_slots"])
                self.assertEqual(result["engine_patch"]["friday_free"], "hard")
                self.assertEqual(result["status"], "ready")
                self.assertIn(message, result["unhandled_soft_preferences"])
        added = compile_hard_constraints("周三尽量空和周四尽量空", [], inherited_policy=previous)
        self.assertEqual(added["soft_free_slots"], [{"weekday": 3, "periods": None}, {"weekday": 4, "periods": None}])
        cancelled = compile_hard_constraints("取消周三尽量空，周四尽量空", [], inherited_policy=previous)
        self.assertEqual(cancelled["soft_free_slots"], [{"weekday": 4, "periods": None}])
        parallel = compile_hard_constraints("周五保持不变同时周四尽量空", [], inherited_policy=previous)
        self.assertEqual(parallel["soft_free_slots"], added["soft_free_slots"])
        denied_then_added = compile_hard_constraints("不是让你取消周三尽量空和周四尽量空，周六尽量空", [], inherited_policy=previous)
        self.assertEqual(denied_then_added["soft_free_slots"], [{"weekday": 3, "periods": None}, {"weekday": 6, "periods": None}])

    def test_negative_soft_intent_prefix_and_suffix_preserve_the_whole_group(self):
        previous = compile_hard_constraints("周三尽量空，周五必须空，周二第1节不能排课", [])
        messages = ("不要周三尽量空和周四尽量空", "不用让周三尽量空和周四尽量空",
            "无需周三尽量空和周四尽量空", "周三尽量空和周四尽量空都不要", "周三尽量空和周四尽量空都不用",
            "不要周三尽量空和周五尽量空", "不用尽量让星期二空和星期六空")
        for message in messages:
            for inherited in (None, previous):
                with self.subTest(message=message, inherited=bool(inherited)):
                    result = compile_hard_constraints(message, [], inherited_policy=inherited, soft_preferences=["周五尽量空"])
                    self.assertEqual(result["soft_free_slots"], previous["soft_free_slots"] if inherited else [])
                    self.assertEqual(result["blocked_slots"], previous["blocked_slots"] if inherited else [])
                    self.assertEqual(result["engine_patch"], previous["engine_patch"] if inherited else {})
                    self.assertIn(message, result["unhandled_soft_preferences"])
        positive = compile_hard_constraints("周五尽量不要排课和周四尽量不要排课", [])
        self.assertEqual(positive["soft_free_slots"], [{"weekday": 4, "periods": None}, {"weekday": 5, "periods": None}])
        self.assertEqual(positive["unhandled_soft_preferences"], [])
        self.assertEqual(positive["engine_patch"], {"friday_free": "soft"})

    def test_existing_selection_control_references_share_preservation_without_becoming_new_hard(self):
        previous = compile_hard_constraints("周五必须空", [])
        phrases = ("之前的锁定和周五必须空都保留", "已有锁定与固定项均不变", "原有固定安排和既有锁定保持原样",
                   "保留已有的锁和固定项", "已锁定的班次与其他条件继续保留", "必须保留之前的锁定")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = compile_hard_constraints(phrase, [phrase], inherited_policy=previous)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(result["engine_patch"]["friday_free"], "hard")
                self.assertEqual(result["blocked_slots"], [])
        pending = compile_hard_constraints("再加一个硬条件：周三下午不能排课，之前的锁定和周五必须空都保留。", [], inherited_policy=previous)
        result = compile_hard_constraints("就是第6到8节，继续刚才的调整。", ["周三第6节到第8节不能排课", "之前的锁定"], inherited_policy=pending)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["engine_patch"]["friday_free"], "hard")
        self.assertEqual(result["blocked_slots"][0]["weekday"], 3)
        self.assertEqual(result["blocked_slots"][0]["periods"], [6, 7, 8])

    def test_preserve_control_never_consumes_unknown_business_or_new_ambiguous_lock_targets(self):
        phrases = ("之前的锁定和必须同校区都保留", "保留原有锁定和新的那门课", "已有锁定和新增锁定都保留",
                   "必须锁定那门课程且其他条件保留", "之前的锁定和周五必须空都保留且必须正式可选")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                for preferences in ([], [phrase]):
                    result = compile_hard_constraints(phrase, preferences)
                    self.assertEqual(result["status"], "needs_clarification")
                    self.assertTrue(result["unresolved"])
        credits = compile_hard_constraints("已有锁定和最多8学分都保留", [])
        self.assertEqual(credits["engine_patch"]["max_credits"], "8")
        self.assertEqual(compile_hard_constraints("之前的锁定", ["之前的锁定"])["status"], "needs_clarification")
        self.assertEqual(self.authorize("已有锁定和其他条件都保留", {"unlock_class_refs": ["class-one"]})["status"], "needs_clarification")

    def test_option_position_and_exact_target_scope_are_checked(self):
        self.assertEqual(self.authorize("锁定A第一门", {"lock_class_refs": ["class-one"]})["status"], "ready")
        self.assertEqual(self.authorize("锁定A第一门", {"lock_class_refs": ["class-two"]})["status"], "needs_clarification")

    def test_relaxation_direction_must_match_human_words(self):
        self.assertEqual(self.authorize("把周五改成尽量空闲", {"friday_free": "soft"})["status"], "ready")
        self.assertEqual(self.authorize("周五有课也可以", {"friday_free": "none"})["status"], "ready")
        self.assertEqual(self.authorize("周五还是必须空", {"friday_free": "none"})["status"], "needs_clarification")

    def test_verified_level_transitions_consume_only_the_old_to_new_expression(self):
        prior = compile_hard_constraints("周五必须空，周三第6到8节不能排课", [])
        for phrase, old, new in (
            ("把周五从必须空放宽为尽量空", "hard", "soft"),
            ("星期五由不能有课调整为最好空闲", "hard", "soft"),
            ("将周五原来的必须空改成可以有课", "hard", "none"),
            ("周五从尽量空收紧为必须空", "soft", "hard"),
            ("周五由允许有课改成务必留空", "none", "hard"),
            ("把 周五 从 必须空 放宽到 尽量空闲", "hard", "soft"),
            ("取消周五必须空的要求", "hard", "none"),
        ):
            with self.subTest(phrase=phrase):
                message = phrase + "且周三第6到8节不能排课，其他条件保留"
                proof = authorize_changes(message, {"friday_free": new}, prior_constraints={"friday_free": old}, class_metadata={})
                self.assertEqual(proof["status"], "ready")
                consumed = proof["verified_changes"]["friday_free"]["consumed_expressions"]
                self.assertEqual(consumed, [phrase])
                result = compile_hard_constraints(message, ["周三第6-8节禁排"], inherited_policy=prior,
                    overrides={"friday_free": new}, consumed_expressions=consumed)
                self.assertEqual(result["status"], "ready")
                self.assertNotEqual(result["engine_patch"].get("friday_free"), "hard")
                self.assertEqual([(row["weekday"], row["periods"]) for row in result["blocked_slots"]], [(3, [6, 7, 8])])

    def test_verified_transition_cannot_consume_neighboring_unknown_or_opposite_conditions(self):
        phrase = "把周五从必须空放宽为尽量空"
        prior = compile_hard_constraints("周五必须空", [])
        for suffix in ("且必须同校区", "并且必须正式可选", "但周五仍必须空", "和周三下午必须空"):
            with self.subTest(suffix=suffix):
                message = phrase + suffix
                proof = self.authorize(message, {"friday_free": "soft"})
                consumed = proof["verified_changes"]["friday_free"]["consumed_expressions"]
                self.assertEqual(consumed, [phrase])
                result = compile_hard_constraints(message, [], inherited_policy=prior,
                    overrides={"friday_free": "soft"}, consumed_expressions=consumed)
                self.assertEqual(result["status"], "needs_clarification")
        message = "把周五从尽量空收紧为必须空，但周五仍尽量空"
        proof = authorize_changes(message, {"friday_free": "hard"}, prior_constraints={"friday_free": "soft"}, class_metadata={})
        result = compile_hard_constraints(message, [], overrides={"friday_free": "hard"},
            consumed_expressions=proof["verified_changes"]["friday_free"]["consumed_expressions"])
        self.assertEqual(result["status"], "needs_clarification")

    def test_transition_consumption_requires_asserted_current_evidence_and_matching_prior(self):
        phrase = "把周五从必须空改为尽量空"
        for message in ("如果" + phrase + "会怎样", "我没有说" + phrase, "不是要" + phrase,
                        "不要" + phrase, "老师说“" + phrase + "”", "“" + phrase + "”", phrase + "？"):
            with self.subTest(message=message):
                self.assertEqual(self.authorize(message, {"friday_free": "soft"})["status"], "needs_clarification")
        proof = authorize_changes(phrase, {"friday_free": "soft"}, prior_constraints={"friday_free": "none"}, class_metadata={})
        self.assertEqual(proof["verified_changes"]["friday_free"]["consumed_expressions"], [])
        result = compile_hard_constraints(phrase, [], overrides={"friday_free": "soft"})
        self.assertEqual(result["status"], "needs_clarification")

    def test_verified_polite_unlock_consumes_only_its_owned_target_before_followup(self):
        phrase = "能不能帮我解除刚才那个锁？"
        for suffix, expected in (("其他条件保留", "ready"), ("必须同校区", "needs_clarification")):
            message = phrase + suffix
            proof = self.authorize(message, {"unlock_class_refs": ["class-one"]})
            self.assertEqual(proof["status"], "ready")
            consumed = proof["verified_changes"]["unlock_class_refs"]["consumed_expressions"]
            self.assertEqual(consumed, [phrase[:-1]])
            result = compile_hard_constraints(message, [], consumed_expressions=consumed)
            self.assertEqual(result["status"], expected)
        self.assertEqual(self.authorize("重新计算", {"target_credits": 6})["status"], "ready")
        self.assertEqual(self.authorize("重新计算", {"target_credits": 3})["status"], "needs_clarification")

    def test_friday_change_cannot_borrow_another_weekdays_predicate_or_preserve_instruction(self):
        for message, level in (
            ("周五原条件保留且周三尽量空", "soft"),
            ("周五保持不变，同时周三尽量空", "soft"),
            ("周五原条件保留，周三可以有课", "none"),
            ("周五保持不变，取消周三必须空的要求", "none"),
            ("尽量周三空，周五保持不变", "soft"),
            ("保留周五尽量空的要求", "soft"),
            ("维持周五可以有课的条件", "none"),
            ("周五和周三尽量空", "soft"),
            ("“周五尽量空”", "soft"),
            ("“周五尽量空且周三空”", "soft"),
            ("‘周五允许有课’", "none"),
            ("不需要调整周五尽量空", "soft"),
        ):
            for evidence in (None, [{"field": "friday_free", "quote": message}]):
                with self.subTest(message=message, supplied_quote=evidence is not None):
                    result = self.authorize(message, {"friday_free": level}, evidence)
                    self.assertEqual(result["status"], "needs_clarification")
                    self.assertEqual(result["authorized_patch"], {})
                    self.assertEqual(result["verified_changes"], {})

    def test_friday_local_predicate_and_transition_remain_usable_with_other_weekdays(self):
        for message, level in (
            ("周三尽量空且把周五改成尽量空闲", "soft"),
            ("周五从必须空放宽为尽量空，同时周三第6到8节不能排课", "soft"),
            ("周三必须空，周五允许有课", "none"),
            ("取消周五必须空的要求，但周三第6节不能排课", "none"),
            ("请尽量让周五空，其他条件保留", "soft"),
            ("周五尽量不要排课，其他条件保留", "soft"),
        ):
            with self.subTest(message=message):
                self.assertEqual(self.authorize(message, {"friday_free": level},
                    [{"field": "friday_free", "quote": message}])["status"], "ready")


if __name__ == "__main__":
    unittest.main()
