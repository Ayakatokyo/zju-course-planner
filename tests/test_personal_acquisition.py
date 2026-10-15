"""Question-driven source preparation with synthetic school doubles only."""
from copy import deepcopy
import unittest

from course_assistant.personal_acquisition import prepare_sources


class SyntheticSchool:
    def __init__(self, snapshots=(), plan_status="not_acquired"):
        self.local = {"observations": {"snapshots": list(snapshots)}, "plan": {"status": plan_status}}
        self.calls, self.plan_calls = [], 0
        self.response = {"status": "partial_acquisition", "records": [{"course_name_raw": "合成课程"}]}

    def local_chat_status(self, context):
        return {"local": deepcopy(self.local)}

    def fetch(self, context, body):
        self.calls.append(deepcopy(body))
        if self.response["records"]:
            self.local["observations"]["snapshots"].append({"operation": body["operation"], "target_term": body["target_term"]})
        return deepcopy(self.response)

    def acquire_plan(self, context):
        self.plan_calls += 1
        self.local["plan"]["status"] = "acquired_pending_review"
        return {"status": "acquired_pending_review"}


class PersonalAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.school, self.events = SyntheticSchool(), []
        self.authorizations = 0

    def authorize(self):
        self.authorizations += 1

    def prepare(self, question, authorize=None):
        return prepare_sources(self.school, object(), question,
            authorize=authorize or self.authorize, emit=lambda kind, fields: self.events.append((kind, deepcopy(fields))))

    def test_generic_grade_question_reads_major_history_once_and_reuses_observation(self):
        result = self.prepare("我的成绩怎么样？")
        self.assertEqual(self.school.calls, [{"operation": "major_academic", "target_term": "ALL-HISTORY"}])
        self.assertEqual(result, [{"operation": "major_academic", "status": "partial_acquisition"}])
        self.assertEqual(self.authorizations, 2)
        self.assertEqual([kind for kind, _ in self.events], ["tool.start", "tool.end"])
        self.assertEqual(self.events[-1][1]["tool_name"], "read_my_major_academic")
        self.assertEqual(self.prepare("请看看我的成绩"), [])
        self.assertEqual(len(self.school.calls), 1)

    def test_explicit_major_history_is_not_replaced_by_other_term_observations(self):
        self.school = SyntheticSchool([{"operation": "academic", "target_term": "2025-2026-AW"}])
        self.prepare("查看我的主修成绩")
        self.assertEqual(self.school.calls, [{"operation": "major_academic", "target_term": "ALL-HISTORY"}])

    def test_generic_overview_prepares_major_history_despite_existing_term_cache(self):
        self.school = SyntheticSchool([{"operation": "academic", "target_term": "2025-2026-AW"}])
        self.prepare("我的成绩怎么样")
        self.assertEqual(self.school.calls, [{"operation": "major_academic", "target_term": "ALL-HISTORY"}])
        self.assertEqual(self.prepare("再帮我看看成绩"), [])
        self.assertEqual(len(self.school.calls), 1)

    def test_identity_greetings_and_unscoped_schedule_never_fetch_sources(self):
        for question in ("你是谁", "你好", "？", "你能做什么", "我的课表是什么", "我什么时候考试"):
            self.assertEqual(self.prepare(question), [])
        self.assertEqual(self.school.calls, [])
        self.assertEqual(self.school.plan_calls, 0)
        self.assertEqual(self.events, [])

    def test_explicit_valid_term_uses_only_requested_academic_scope(self):
        self.prepare("查询2024—2025学年秋冬成绩")
        self.assertEqual(self.school.calls, [{"operation": "academic", "target_term": "2024-2025-AW"}])
        self.assertEqual(self.prepare("再看2024-2025秋冬成绩"), [])
        self.prepare("2024至2025春夏成绩")
        self.assertEqual(self.school.calls[-1], {"operation": "academic", "target_term": "2024-2025-SS"})

    def test_missing_grade_and_curriculum_are_the_only_two_preparation_actions(self):
        self.prepare("我的成绩与培养要求分别是什么")
        self.assertEqual(len(self.school.calls), 1)
        self.assertEqual(self.school.plan_calls, 1)
        self.assertEqual(self.prepare("再次查看成绩与培养要求"), [])
        self.assertEqual(self.school.plan_calls, 1)
        self.assertEqual(self.authorizations, 4)

    def test_existing_curriculum_does_not_reacquire_pdf(self):
        self.school = SyntheticSchool(plan_status="acquired_pending_review")
        self.assertEqual(self.prepare("培养方案中的专业选修要求"), [])
        self.assertEqual(self.school.plan_calls, 0)

    def test_empty_failed_source_is_not_reported_as_completed_or_registered(self):
        self.school.response = {"status": "source_unavailable", "records": []}
        result = self.prepare("我的成绩怎么样")
        self.assertEqual(result[0]["status"], "source_unavailable")
        self.assertEqual(self.events[-1][1]["status"], "not_ready")
        self.assertEqual(self.school.local["observations"]["snapshots"], [])

    def test_authorization_checked_before_fetch_and_after_source_result(self):
        def denied():
            raise PermissionError("SYNTHETIC-AUTH-REVOKED")
        with self.assertRaises(PermissionError):
            self.prepare("我的成绩怎么样", denied)
        self.assertEqual(self.school.calls, [])
        self.assertEqual(self.events, [])
        calls = 0
        def changed():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("SYNTHETIC-AUTH-REVOKED")
        with self.assertRaises(PermissionError):
            self.prepare("我的成绩怎么样", changed)
        self.assertEqual(len(self.school.calls), 1)
        endings = [fields for kind, fields in self.events if kind == "tool.end"]
        self.assertEqual(len(endings), 1)
        self.assertEqual(endings[0]["status"], "failed")
        self.assertNotIn("SYNTHETIC-AUTH-REVOKED", str(endings))


if __name__ == "__main__":
    unittest.main()
