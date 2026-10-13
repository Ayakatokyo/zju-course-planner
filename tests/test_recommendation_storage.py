"""Atomic recommendation saves on entirely synthetic, temporary SQLite data."""
import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from course_assistant.auth import AccessError, AuthContext
from course_assistant.importer import digest
from course_assistant.storage import Store, encoded


class RecommendationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "recommendation.sqlite3")
        self.context = AuthContext("synthetic-a", "synthetic-session-a", "SYNTHETIC-A", "test-realm", "test-v1")
        self.other = AuthContext("synthetic-b", "synthetic-session-b", "SYNTHETIC-B", "test-realm", "test-v1")
        self.dataset = {"student": {"student_key": self.context.student_id}, "data_kind": "synthetic",
                        "snapshot": "SYNTHETIC-OFFERINGS-v1"}
        self.constraints = {"revision": 1, "locked_section_ids": ["SYNTHETIC-PE"],
                            "requested_course_ids": ["SYNTHETIC-DATA"], "friday_free": "soft"}
        self.store.set_workspace(self.context, self.dataset, self.constraints)
        self.store.set_workspace(self.other, {**self.dataset, "student": {"student_key": self.other.student_id}},
                                 self.constraints)
        self.versions = self._versions(self.dataset, self.constraints)
        self.option_ref = {"run_id": "SYNTHETIC-RUN-1", "option_id": "SYNTHETIC-OPTION-A"}
        options = [{**self.option_ref, "section_ids": ["SYNTHETIC-AI", "SYNTHETIC-PE"],
                    "constraints": self.constraints, "versions": self.versions, "validation": {"result": "pass"}},
                   {"run_id": self.option_ref["run_id"], "option_id": "SYNTHETIC-OPTION-B",
                    "section_ids": ["SYNTHETIC-DATA", "SYNTHETIC-PE"], "versions": self.versions}]
        self.state = {"messages": [], "turns": [], "preview_constraints": copy.deepcopy(self.constraints),
                      "workspace_versions": copy.deepcopy(self.versions),
                      "recommendation_intent": {"intent_id": "SYNTHETIC-INTENT", "revision": 1},
                      "recommendation_runs": [
                          {"run_id": self.option_ref["run_id"], "intent_revision": 1,
                           "workspace_versions": copy.deepcopy(self.versions), "options": options},
                          {"run_id": "SYNTHETIC-OLD-RUN", "workspace_versions": copy.deepcopy(self.versions),
                           "options": [{"run_id": "SYNTHETIC-OLD-RUN", "option_id": "SYNTHETIC-OLD-OPTION",
                                        "section_ids": ["SYNTHETIC-PE"], "versions": self.versions}]}]}
        self.request_id = str(uuid.uuid4())
        reservation = self.store.chat_begin(self.context, None, self.request_id, "synthetic-request", 0)
        self.conversation_id = reservation["conversation_id"]
        self.ready_response = {"conversation_id": self.conversation_id, "turn_id": 1, "status": "completed",
                               "versions": self.versions, "reply": "SYNTHETIC action ready", "action_pending": True}
        self.state["turns"] = [{"message": "SYNTHETIC save A", **copy.deepcopy(self.ready_response)}]
        self.store.chat_finish(self.context, self.conversation_id, self.request_id, 1, self.state, self.ready_response)
        self.new_constraints = {**self.constraints, "revision": 2,
                                "requested_course_ids": ["SYNTHETIC-AI", "SYNTHETIC-PE"]}
        self.new_versions = self._versions(self.dataset, self.new_constraints)
        self.frozen = {"owner_student_id": self.context.student_id, "security_realm": self.context.security_realm,
                       "data_kind": "synthetic", "not_official_enrollment": True,
                       "frozen_dataset": self.dataset, "constraints": self.new_constraints, "versions": self.new_versions,
                       "plan": {"section_ids": options[0]["section_ids"], "versions": self.new_versions,
                                "validation": {"result": "pass"}}}
        self.arguments = {"context": self.context, "conversation_id": self.conversation_id, "expected_turn": 1,
                          "expected_workspace_versions": self.versions, "option_ref": self.option_ref,
                          "action_id": str(uuid.uuid4()), "frozen_body": self.frozen,
                          "new_constraints": self.new_constraints, "updated_state": self.state}

    @staticmethod
    def _versions(dataset, constraints):
        return {"algorithm": "synthetic-test-v1", "dataset_sha256": digest(dataset),
                "constraints_revision": constraints["revision"], "constraints_sha256": digest(constraints)}

    def save(self, **changes):
        return self.store.save_chat_option(**{**copy.deepcopy(self.arguments), **changes})

    def snapshot(self):
        with self.store.connect() as db:
            return {table: db.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()
                    for table in ("owned_datasets", "workspaces", "owned_plans", "owned_conversations",
                                  "owned_chat_requests", "owned_recommendation_actions")}

    def rejects_without_writes(self, reason, **changes):
        before = self.snapshot()
        with self.assertRaises(AccessError) as caught:
            self.save(**changes)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(self.snapshot(), before)

    def change_state(self, mutate):
        state = copy.deepcopy(self.state)
        mutate(state)
        with self.store.connect() as db:
            db.execute("UPDATE owned_conversations SET state=? WHERE id=?", (encoded(state), self.conversation_id))

    def final_request(self):
        return {"request_id": self.request_id, "response": copy.deepcopy(self.ready_response)}

    def amend(self, **changes):
        arguments = {"context": self.context, "conversation_id": self.conversation_id,
                     "request_id": self.request_id, "expected_turn": 1,
                     "response": {**copy.deepcopy(self.ready_response), "action_pending": False,
                                  "download": {"data_base64": "U1lOVEhFVElD", "format": "json"}}}
        return self.store.amend_chat_response(**{**arguments, **changes})

    def test_save_synchronizes_only_selected_option_and_preserves_turn(self):
        original_arguments = copy.deepcopy(self.arguments)
        response = self.save()
        saved = response["saved_plan"]
        self.assertFalse(response["replayed"])
        self.assertEqual(self.store.workspace(self.context)["constraints"], self.new_constraints)
        self.assertEqual(self.store.saved_one(self.context, saved["saved_id"]), saved)
        conversation = self.store.chat_one(self.context, self.conversation_id)
        self.assertEqual(conversation["turn_id"], 1)
        self.assertFalse(conversation["in_progress"])
        state = conversation["state"]
        self.assertEqual(state["workspace_versions"], self.new_versions)
        self.assertEqual(state["preview_constraints"], self.new_constraints)
        self.assertEqual(state["recommendation_intent"], self.state["recommendation_intent"])
        run, old_run = state["recommendation_runs"]
        chosen, sibling = run["options"]
        self.assertFalse(run["is_stale"])
        self.assertEqual(run["workspace_versions"], self.new_versions)
        self.assertFalse(chosen["is_stale"])
        self.assertEqual(chosen["validation"], self.frozen["plan"]["validation"])
        self.assertEqual(chosen["saved_id"], saved["saved_id"])
        self.assertEqual(chosen["versions"], self.new_versions)
        self.assertTrue(sibling["is_stale"])
        self.assertEqual(sibling["versions"], self.versions)
        self.assertTrue(old_run["is_stale"])
        self.assertTrue(old_run["options"][0]["is_stale"])
        self.assertEqual(self.arguments, original_arguments)

    def test_identical_action_replays_after_workspace_revision_changed(self):
        first = self.save()
        before = self.snapshot()
        replay = self.save()
        self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)
        receipt = self.store.recommendation_action(self.context, self.arguments["action_id"])
        self.assertEqual(receipt["response"], first)
        self.assertEqual(receipt["request"], {"conversation_id": self.conversation_id, "expected_turn": 1,
                                               "option_ref": self.option_ref})
        self.assertEqual(receipt["request_digest"], digest(receipt["request"]))
        self.assertIsNone(self.store.recommendation_action(self.other, self.arguments["action_id"]))

    def test_action_id_cannot_be_reused_for_different_command_or_commit(self):
        self.save()
        self.rejects_without_writes("action_id_reused", expected_turn=2)
        self.rejects_without_writes("action_id_reused", option_ref={**self.option_ref, "option_id": "SYNTHETIC-OPTION-B"})
        self.rejects_without_writes("action_id_reused", frozen_body={**self.frozen, "not_official_enrollment": False})

    def test_owner_and_realm_isolation(self):
        self.rejects_without_writes("inaccessible_object", context=self.other)
        another_realm = AuthContext("synthetic-a", "synthetic-session-a", self.context.student_id,
                                    "other-test-realm", "test-v1")
        self.rejects_without_writes("inaccessible_object", context=another_realm)
        response = self.save()
        self.assertIsNone(self.store.saved_one(self.other, response["saved_plan"]["saved_id"]))
        self.assertIsNone(self.store.recommendation_action(another_realm, response["action_id"]))

    def test_stale_turn_and_pending_chat_are_rejected(self):
        self.rejects_without_writes("turn_version_changed", expected_turn=0)
        self.store.chat_begin(self.context, self.conversation_id, str(uuid.uuid4()), "synthetic-next", 1)
        self.rejects_without_writes("turn_in_progress")

    def test_dataset_change_is_rejected_even_with_unchanged_preferences(self):
        self.store.set_workspace(self.context, {**self.dataset, "snapshot": "SYNTHETIC-OFFERINGS-v2"}, self.constraints)
        self.rejects_without_writes("workspace_changed")

    def test_preference_revision_and_same_revision_content_change_are_rejected(self):
        for constraints in ({**self.constraints, "revision": 2}, {**self.constraints, "friday_free": "hard"}):
            with self.subTest(constraints=constraints):
                self.store.set_workspace(self.context, self.dataset, constraints)
                self.rejects_without_writes("workspace_changed")

    def test_tampered_dataset_digest_is_rejected(self):
        with self.store.connect() as db:
            db.execute("UPDATE owned_datasets SET body=? WHERE realm=? AND owner=?",
                       ('{"data_kind":"synthetic","tampered":true}', self.context.security_realm, self.context.student_id))
        self.rejects_without_writes("workspace_changed")

    def test_missing_or_cross_run_option_reference_is_rejected(self):
        for option_ref in ({**self.option_ref, "option_id": "SYNTHETIC-MISSING"},
                           {"run_id": "SYNTHETIC-OLD-RUN", "option_id": self.option_ref["option_id"]},
                           {**self.option_ref, "run_id": "SYNTHETIC-MISSING-RUN"}):
            with self.subTest(option_ref=option_ref):
                self.rejects_without_writes("inaccessible_option", option_ref=option_ref)

    def test_stale_conversation_run_or_option_is_rejected(self):
        mutations = [lambda state: state.update(workspace_versions={}),
                     lambda state: state["recommendation_intent"].update(revision=2),
                     lambda state: state["recommendation_runs"][0].update(workspace_versions={}),
                     lambda state: state["recommendation_runs"][0].update(is_stale=True),
                     lambda state: state["recommendation_runs"][0]["options"][0].update(is_stale=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.change_state(mutation)
                self.rejects_without_writes("stale_recommendation")

    def test_frozen_scope_dataset_conditions_selection_and_versions_must_match(self):
        variants = [lambda body: body.update(owner_student_id=self.other.student_id),
                    lambda body: body.update(security_realm="other-test-realm"),
                    lambda body: body.update(data_kind="official"),
                    lambda body: body.update(not_official_enrollment=False),
                    lambda body: body.update(frozen_dataset={}),
                    lambda body: body.update(constraints=self.constraints),
                    lambda body: body["plan"].update(section_ids=["SYNTHETIC-DATA"]),
                    lambda body: body["versions"].update(constraints_sha256="synthetic-wrong-digest"),
                    lambda body: body["versions"].update(algorithm="synthetic-other-version"),
                    lambda body: body["plan"].update(versions={})]
        for mutate in variants:
            body = copy.deepcopy(self.frozen)
            mutate(body)
            with self.subTest(mutate=mutate):
                self.rejects_without_writes("invalid_frozen_recommendation", frozen_body=body)

    def test_missing_option_in_updated_state_does_not_write(self):
        state = copy.deepcopy(self.state)
        state["recommendation_runs"] = []
        self.rejects_without_writes("invalid_recommendation_state", updated_state=state)

    def test_duplicate_option_in_updated_state_does_not_write(self):
        state = copy.deepcopy(self.state)
        state["recommendation_runs"][0]["options"].append(copy.deepcopy(state["recommendation_runs"][0]["options"][0]))
        self.rejects_without_writes("invalid_recommendation_state", updated_state=state)

    def test_failure_after_plan_or_conversation_write_rolls_back_every_resource(self):
        for table, event in (("owned_conversations", "UPDATE OF state"),
                             ("owned_recommendation_actions", "INSERT")):
            with self.subTest(failure_at=table):
                with self.store.connect() as db:
                    db.execute("CREATE TRIGGER synthetic_failure BEFORE " + event + " ON " + table +
                               " BEGIN SELECT RAISE(ABORT, 'synthetic injected failure'); END")
                before = self.snapshot()
                with self.assertRaises(sqlite3.IntegrityError):
                    self.save()
                self.assertEqual(self.snapshot(), before)
                with self.store.connect() as db:
                    db.execute("DROP TRIGGER synthetic_failure")
        saved = self.save()["saved_plan"]
        self.assertEqual(saved["saved_id"], 1)
        self.assertEqual(len(self.store.saved(self.context)), 1)

    def test_language_save_updates_request_and_last_turn_at_new_baseline(self):
        final_request = self.final_request()
        receipt = self.save(final_chat_request=final_request)
        final = receipt["chat_response"]
        self.assertEqual(final["versions"], self.new_versions)
        self.assertEqual(final["saved_action"], receipt["saved_plan"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["reply_origin"], "verified_explicit_action")
        self.assertFalse(final["action_pending"])
        conversation = self.store.chat_one(self.context, self.conversation_id)
        self.assertEqual(conversation["state"]["workspace_versions"], final["versions"])
        self.assertEqual(conversation["state"]["turns"][-1], {"message": "SYNTHETIC save A", **final})
        replay = self.store.chat_begin(self.context, self.conversation_id, self.request_id, "synthetic-request", 0)
        self.assertEqual(replay["replay"], final)
        before = self.snapshot()
        self.assertEqual(self.save(final_chat_request=final_request), {**receipt, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_language_save_requires_request_owned_by_this_conversation(self):
        other_request = str(uuid.uuid4())
        other = self.store.chat_begin(self.other, None, other_request, "synthetic-other", 0)
        response = {**self.ready_response, "conversation_id": other["conversation_id"]}
        self.store.chat_finish(self.other, other["conversation_id"], other_request, 1,
                               {"turns": [response]}, response)
        self.rejects_without_writes("inaccessible_chat_request", final_chat_request={
            "request_id": other_request, "response": self.ready_response})
        same_owner_request = str(uuid.uuid4())
        same_owner = self.store.chat_begin(self.context, None, same_owner_request, "synthetic-other-conversation", 0)
        response = {**self.ready_response, "conversation_id": same_owner["conversation_id"]}
        self.store.chat_finish(self.context, same_owner["conversation_id"], same_owner_request, 1,
                               {"turns": [response]}, response)
        self.rejects_without_writes("inaccessible_chat_request", final_chat_request={
            "request_id": same_owner_request, "response": self.ready_response})

    def test_language_save_requires_completed_current_request_and_last_turn(self):
        for mutate in (lambda response: response.update(turn_id=0),
                       lambda response: response.update(conversation_id="SYNTHETIC-OTHER-CONVERSATION")):
            with self.subTest(mutate=mutate):
                prior = copy.deepcopy(self.ready_response)
                mutate(prior)
                with self.store.connect() as db:
                    db.execute("UPDATE owned_chat_requests SET response=? WHERE request_id=?", (encoded(prior), self.request_id))
                self.rejects_without_writes("turn_version_changed", final_chat_request=self.final_request())
        with self.store.connect() as db:
            db.execute("UPDATE owned_chat_requests SET response=?,status='failed' WHERE request_id=?",
                       (encoded(self.ready_response), self.request_id))
        self.rejects_without_writes("chat_request_not_complete", final_chat_request=self.final_request())
        with self.store.connect() as db:
            db.execute("UPDATE owned_chat_requests SET status='complete' WHERE request_id=?", (self.request_id,))
        self.change_state(lambda state: state["turns"][-1].update(turn_id=0))
        self.rejects_without_writes("turn_version_changed", final_chat_request=self.final_request())

    def test_language_save_request_failure_rolls_back_new_baseline_plan_and_receipt(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER synthetic_chat_failure BEFORE UPDATE OF response ON owned_chat_requests "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic request failure'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(final_chat_request=self.final_request())
        self.assertEqual(self.snapshot(), before)
        with self.store.connect() as db:
            db.execute("DROP TRIGGER synthetic_chat_failure")
            db.execute("CREATE TRIGGER synthetic_final_failure BEFORE INSERT ON owned_recommendation_actions "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic final failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(final_chat_request=self.final_request())
        self.assertEqual(self.snapshot(), before)

    def test_export_amendment_changes_only_receipts_and_keeps_download_out_of_model_history(self):
        workspace = self.store.workspace(self.context)
        messages = self.store.chat_one(self.context, self.conversation_id)["state"]["messages"]
        result = self.amend()
        self.assertEqual(self.store.workspace(self.context), workspace)
        self.assertEqual(self.store.saved(self.context), [])
        self.assertEqual(self.store.chat_one(self.context, self.conversation_id)["state"]["messages"], messages)
        replay = self.store.chat_begin(self.context, self.conversation_id, self.request_id, "synthetic-request", 0)
        self.assertEqual(replay["replay"], result)
        self.assertIn("download", self.store.chat_one(self.context, self.conversation_id)["state"]["turns"][-1])

    def test_failed_action_amendment_is_persisted_for_retry_without_plan_writes(self):
        result = self.amend(response={**self.ready_response, "status": "failed", "action_pending": False,
                                     "code": "synthetic_action_failure"})
        self.assertEqual(result["status"], "failed")
        with self.store.connect() as db:
            status = db.execute("SELECT status FROM owned_chat_requests WHERE request_id=?", (self.request_id,)).fetchone()[0]
        self.assertEqual(status, "failed")
        replay = self.store.chat_begin(self.context, self.conversation_id, self.request_id, "synthetic-request", 0)
        self.assertEqual(replay["replay"], result)
        self.assertEqual(self.store.saved(self.context), [])
        self.assertEqual(self.store.workspace(self.context)["constraints"], self.constraints)

    def test_amendment_rejects_other_owner_old_turn_pending_and_wrong_request(self):
        for changes, reason in (({"context": self.other}, "inaccessible_object"),
                                ({"request_id": str(uuid.uuid4())}, "inaccessible_chat_request"),
                                ({"expected_turn": 0, "response": {**self.ready_response, "turn_id": 0}}, "turn_version_changed")):
            with self.subTest(changes=changes):
                before = self.snapshot()
                with self.assertRaises(AccessError) as caught:
                    self.amend(**changes)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(self.snapshot(), before)
        self.store.chat_begin(self.context, self.conversation_id, str(uuid.uuid4()), "synthetic-next", 1)
        before = self.snapshot()
        with self.assertRaises(AccessError) as caught:
            self.amend()
        self.assertEqual(caught.exception.reason, "turn_in_progress")
        self.assertEqual(self.snapshot(), before)

    def test_amendment_failure_rolls_back_both_request_and_conversation(self):
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER synthetic_amendment_failure BEFORE UPDATE OF state ON owned_conversations "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic amendment failure'); END")
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.amend()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
