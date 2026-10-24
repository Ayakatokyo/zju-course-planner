"""Real personal orchestration and dialogue over invented school/model transports.

These are protocol journey tests, not real school or supplier acceptance. No
production service is patched, no user session is read, and no network is used.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
import uuid

from course_assistant.model_client import ModelError
from course_assistant.personal_chat import DATA_USE_VERSION
from course_assistant.server import Application
from tests.test_personal_dialogue import ScriptedModel, call, final
from tests.test_school_accounts import FakeSchoolClient


class TaskJourneySchool(FakeSchoolClient):
    def request(self, spec):
        if spec["operation"] != "timetable":
            return super().request(spec)
        self.request_calls.append(deepcopy(spec))
        old = spec["target_term"] == "2022-2023-AW"
        row = {"kcmc": "OLD-SYNTHETIC-TIMETABLE-FACT" if old else "本轮合成课表课程",
               "kch": "SYNTHETIC-OLD" if old else "SYNTHETIC-CURRENT",
               "sksj": "春 周一第1,2节{1-8周}", "skdd": "合成教室"}
        return {"state": "business", "http_status": 200, "content_type": "application/json",
                "session_epoch": self.session_epoch, "source_subject_id": self.profile["source_subject_id"],
                "payload": {"kbList": [row], "total": 1}}


class PersonalTaskJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.school = TaskJourneySchool()
        self.model = ScriptedModel([])
        root = Path(self.temporary.name).resolve()
        self.app = Application(root / "synthetic.sqlite3", model=self.model,
                               school_client_factory=lambda: self.school,
                               school_root=root / "private" / "school")
        self.addCleanup(self.app.close)
        token, receipt = self.app.school.login({"username": "SYNTHETIC-LOGIN", "password": "SYNTHETIC-TRANSIENT"})
        self.assertTrue(receipt["authenticated"])
        self.context = self.app.auth.authenticate(token)

    def turn(self, message, previous=None):
        body = {"message": message, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                "consent_personal": True, "data_use_version": DATA_USE_VERSION}
        if previous is not None:
            body.update(conversation_id=previous["conversation_id"], expected_turn=previous["turn_id"])
        return self.app.personal_chat.turn(body, self.context)

    def stored(self, response):
        return self.app.store.chat_one(self.context, response["conversation_id"])["state"]["turns"][-1]

    @staticmethod
    def pending_in_wire(messages):
        for row in messages:
            if row["role"] == "system":
                marker = re.search(r"上一轮待补请求 previous_clarification[^\n]*\n", row["content"])
                if marker:
                    return json.JSONDecoder().raw_decode(row["content"][marker.end():])[0]
        return None

    def assert_pending_wire(self, messages, *, request, supplements, question):
        pending = self.pending_in_wire(messages)
        self.assertIsNotNone(pending)
        self.assertEqual((pending["request"], pending["supplements"], pending["question"]),
                         (request, supplements, question))
        self.assertFalse(pending["authorization_granted"])
        self.assertFalse(pending["facts_reusable"])
        self.assertTrue(pending["fresh_sources_required"])
        self.assertIsNone(pending["previous_task"])

    def test_three_turn_clarification_queries_fresh_timetable_and_clears_pending(self):
        old = self.app.school.fetch(self.context, {"operation": "timetable", "target_term": "2022-2023-AW", "force_refresh": True})
        self.assertEqual(old["data_source"], "live")

        def ask_year(messages, tools):
            self.assertIsNone(self.pending_in_wire(messages))
            return final({'kind': "clarify", 'text': "需要查询哪个学年？", 'request_basis': "current_message"})

        def ask_season(messages, tools):
            self.assert_pending_wire(messages, request="查一下课表", supplements=[], question="需要查询哪个学年？")
            self.assertEqual([row["content"] for row in messages if row["role"] == "user"], ["2024-2025"])
            return final({'kind': "clarify", 'text': "查询秋冬还是春夏？", 'request_basis': "pending_task"})

        def query_fresh(messages, tools):
            self.assert_pending_wire(messages, request="查一下课表", supplements=["2024-2025"], question="查询秋冬还是春夏？")
            self.assertEqual([row["content"] for row in messages if row["role"] == "user"], ["春夏"])
            return call("query_my_records", {"operation": "timetable", "target_term": "2024-2025-SS",
                                             "mode": "records", "query": "", "limit": 5})

        def answer_current(messages, tools):
            result = json.loads(next(row["content"] for row in reversed(messages) if row["role"] == "tool"))
            self.assertEqual((result["operation"], result["target_term"]), ("timetable", "2024-2025-SS"))
            record = result["records"][0]
            self.assertEqual(record["course_name_raw"], "本轮合成课表课程")
            return final({'kind': 'answer', 'text': "本轮春夏课表记录包含本轮合成课表课程。", 'evidence_ids': [record["evidence_id"]]})

        self.model.responses.extend([ask_year, ask_season, query_fresh, answer_current])
        first = self.turn("查一下课表")
        self.assertEqual((first["status"], first["task_status"]), ("completed", "awaiting_user"))
        self.assertEqual(len(self.school.request_calls), 1, "clarification must not read the old/default scope")
        second = self.turn("2024-2025", first)
        self.assertEqual((second["status"], second["task_status"]), ("completed", "awaiting_user"))
        self.assertEqual(second["model_decision"]["request_basis"], "pending_task")
        self.assertEqual(self.stored(second)["pending_clarification"]["supplements"], ["2024-2025"])
        self.assertEqual(len(self.school.request_calls), 1)
        third = self.turn("春夏", second)
        self.assertEqual((third["status"], third["task_status"]), ("completed", "completed"), third)
        self.assertIsNone(self.stored(third)["pending_clarification"])
        self.assertEqual([(row["operation"], row["target_term"]) for row in self.school.request_calls],
                         [("timetable", "2022-2023-AW"), ("timetable", "2024-2025-SS")])
        query = next(item for item in third["tool_results"] if item["name"] == "query_my_records")
        self.assertEqual(query["result"]["data_source"], "live")
        self.assertEqual(third["citations"][0]["target_term"], "2024-2025-SS")
        self.assertNotEqual(third["citations"][0]["observation_id"], old["observation_id"])
        wire = json.dumps(self.model.calls, ensure_ascii=False)
        self.assertNotIn("OLD-SYNTHETIC-TIMETABLE-FACT", wire)
        self.assertNotIn(old["observation_id"], wire)
        self.assertNotIn("SYNTHETIC-TRANSIENT", wire)
        self.assertEqual(len(self.model.calls), 4)

    def test_topic_switch_ends_pending_and_later_fragment_does_not_resurrect_it(self):
        def changed_topic(messages, tools):
            self.assert_pending_wire(messages, request="查一下课表", supplements=[], question="需要查询哪个学年？")
            return final({'kind': 'answer', 'text': '你好，我可以根据已开放的教务资料回答问题。', 'evidence_ids': []})

        def fresh_clarification(messages, tools):
            self.assertIsNone(self.pending_in_wire(messages))
            return final({'kind': "clarify", 'text': "需要核对哪类信息？", 'request_basis': "current_message"})

        self.model.responses.extend([final({'kind': "clarify", 'text': "需要查询哪个学年？"}),
                                     changed_topic, fresh_clarification])
        first = self.turn("查一下课表")
        second = self.turn("换个话题，介绍下你的能力", first)
        self.assertEqual(second["task_status"], "completed")
        self.assertIsNone(self.stored(second)["pending_clarification"])
        third = self.turn("春夏", second)
        self.assertEqual(third["task_status"], "awaiting_user")
        self.assertEqual(self.stored(third)["pending_clarification"]["request"], "春夏")
        self.assertEqual(self.school.request_calls, [])

    def test_failed_supplement_ends_pending_and_later_fragment_starts_a_new_request(self):
        def new_request(messages, tools):
            self.assertIsNone(self.pending_in_wire(messages))
            return final({'kind': "clarify", 'text': "需要核对哪类信息？", 'request_basis': "current_message"})

        self.model.responses.extend([final({'kind': "clarify", 'text': "需要查询哪个学年？"}),
                                     ModelError("model_timeout", "合成模型超时"), new_request])
        first = self.turn("查一下课表")
        second = self.turn("2024-2025", first)
        self.assertEqual((second["status"], second["task_status"]), ("failed", "failed"))
        self.assertIsNone(self.stored(second)["pending_clarification"])
        third = self.turn("春夏", second)
        self.assertEqual(third["task_status"], "awaiting_user")
        self.assertEqual(self.stored(third)["pending_clarification"]["request"], "春夏")
        self.assertEqual(self.school.request_calls, [])


if __name__ == "__main__":
    unittest.main()
