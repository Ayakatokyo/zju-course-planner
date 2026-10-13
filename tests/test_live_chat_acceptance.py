"""Acceptance gate counterexamples; no .env, private transcript, or network dependency."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from course_assistant.chat import ChatService
from course_assistant.server import Application
from course_assistant.reference_retrieval import ReferenceIndex
from validation.run_live_chat import SCENARIOS, decision_evidence_checks, evaluate_records, evaluate_saved_run, save_run_result


def tool(name, arguments, identifier="test_call"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def answer(outcome, citations=()):
    return {"role": "assistant", "content": json.dumps({
        "kind": "answer", "outcome": outcome, "cited_chunk_ids": list(citations)})}


class ScriptedModel:
    allow_reference_text = False

    def __init__(self, responses):
        self.responses = responses

    def status(self):
        return {"configured": True, "remote": True, "provider": "test_double", "model": "test_only"}

    def complete(self, messages, tools):
        return self.responses.pop(0)


def recorded_fixture(conflict_tool="validate_my_plan", fixed=False, extra_validation=False):
    index = ReferenceIndex()
    citation = index.search("Python", plan_id="zju_materials_science_engineering_2026",
                            expected_corpus_version=index.corpus_version)["matches"][0]["chunk_id"]
    conflict_args = {"constraint_patch": {"locked_section_ids": ["DEMO-ML-B"], "friday_free": "hard"}}
    if conflict_tool == "validate_my_plan":
        conflict_args["section_ids"] = ["DEMO-PE-A", "DEMO-ML-B", "DEMO-SIM-A"]
    recommendation = [tool("generate_my_plan", {"constraint_patch": {"friday_free": "soft"}})]
    if extra_validation:
        recommendation.append(tool("validate_my_plan", {"section_ids": ["DEMO-PE-A", "DEMO-ML-A", "DEMO-SIM-A"]}, "test_validate"))
    model = ScriptedModel([
        tool("get_my_context", {}), answer("context"),
        *recommendation, answer("feasible"),
        tool(conflict_tool, conflict_args), answer("fail" if conflict_tool == "validate_my_plan" else "scoped_infeasible"),
        tool("search_plan_references", {"query": "Python"}), answer("reference_available", [citation]),
    ])
    with tempfile.TemporaryDirectory() as directory:
        app = Application(Path(directory) / "test.sqlite3", mock_enabled=True, model=model)
        token, _ = app.auth.login_mock({"profile_id": "demo-a"})
        context = app.auth.authenticate(token)
        if fixed:
            meeting = deepcopy(app.fixture["offering_snapshot"]["records"][0]["meetings"][0])
            meeting.update(meeting_id="SYNTHETIC-FIXED-TEST", day_of_week=7)
            app.post("/api/generate", {"constraints": {"fixed_meetings": [meeting]}}, context)
        before = app.workspace(context)
        versions = app.versions(before)
        previous, records, unchanged = None, [], []
        for message, _ in SCENARIOS:
            request = {"message": message, "request_id": str(uuid.uuid4()), "consent_remote": True,
                       "conversation_id": previous["conversation_id"] if previous else None,
                       "expected_turn": previous["turn_id"] if previous else 0}
            response = app.chat.turn(request, context)
            records.append({"request": request, "response": response})
            unchanged.append(app.workspace(context) == before)
            previous = response
    return records, {"initial_workspace": before, "initial_versions": versions, "workspace_checks": unchanged}


class LiveChatAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.options = recorded_fixture()

    def evaluate_mutation(self, mutate):
        records = deepcopy(self.records)
        mutate(records)
        return evaluate_records(records, **self.options)

    def test_validate_and_generate_are_both_valid_conflict_paths(self):
        self.assertTrue(evaluate_records(self.records, **self.options)["passed"])
        records, options = recorded_fixture("generate_my_plan")
        self.assertTrue(evaluate_records(records, **options)["passed"])

    def test_compatible_validation_does_not_hide_generation_evidence(self):
        records, options = recorded_fixture(extra_validation=True)
        response = records[1]["response"]
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["model_decision"]["source_tool_result_index"], 0)
        self.assertEqual(response["tool_results"][-1]["name"], "validate_my_plan")
        self.assertEqual(response["tool_results"][-1]["result"]["result"], "pass")
        result = evaluate_records(records, **options)
        self.assertTrue(result["passed"], result)

    def test_invalid_missing_or_mismatched_evidence_index_is_rejected(self):
        for value in (True, -1, 99, "0", None):
            result = self.evaluate_mutation(lambda rows: rows[1]["response"]["model_decision"].update(source_tool_result_index=value))
            self.assertFalse(result["passed"])
            self.assertIn("decision_evidence_index", result["cases"][1]["failed_checks"])
        result = self.evaluate_mutation(lambda rows: rows[1]["response"]["model_decision"].update(source_tool="validate_my_plan"))
        self.assertFalse(result["passed"])
        self.assertIn("decision_evidence_name", result["cases"][1]["failed_checks"])

    def test_selected_evidence_cannot_be_stale_or_overruled_by_same_plan_failure(self):
        records, _ = recorded_fixture(extra_validation=True)
        response = records[1]["response"]
        tools, decision = response["tool_results"], response["model_decision"]
        final_constraints = tools[-1]["result"]["constraints"]
        changed = deepcopy(final_constraints)
        changed["friday_free"] = "hard"
        self.assertFalse(decision_evidence_checks(tools, decision, changed)["decision_current_conditions"])
        altered = deepcopy(tools)
        altered[-1]["result"]["result"] = "fail"
        self.assertFalse(decision_evidence_checks(altered, decision, final_constraints)["decision_later_evidence_compatible"])
        altered = deepcopy(tools)
        altered.append(deepcopy(tools[0]))
        self.assertFalse(decision_evidence_checks(altered, decision, final_constraints)["decision_latest_same_tool"])
        bad_decision = {**decision, "effective_constraints_sha256": "wrong"}
        self.assertFalse(decision_evidence_checks(tools, bad_decision, final_constraints)["decision_conditions_digest"])

    def test_completed_with_expected_tool_is_not_enough(self):
        mutations = [
            lambda rows: rows[1]["response"]["tool_results"][0]["result"]["constraints"].update(friday_free="off"),
            lambda rows: rows[1]["response"]["tool_results"][0]["result"].update(plans=[]),
            lambda rows: rows[2]["response"]["tool_results"][0]["result"]["constraints"].update(locked_section_ids=["DEMO-PE-A"]),
            lambda rows: rows[2]["response"]["tool_results"][0]["result"].update(result="pass"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertFalse(self.evaluate_mutation(mutation)["passed"])

    def test_final_outcome_conversation_and_workspace_checks_are_required(self):
        mutations = [
            lambda rows: rows[2]["response"]["model_decision"].update(outcome="feasible"),
            lambda rows: rows[1]["response"].update(conversation_id=str(uuid.uuid4())),
            lambda rows: rows[1]["response"].update(turn_id=1),
            lambda rows: rows[1]["response"].update(history_reset=True),
            lambda rows: rows[1]["response"]["versions"].update(offering_snapshot="OTHER"),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertFalse(self.evaluate_mutation(mutation)["passed"])
        self.assertFalse(evaluate_records(self.records, **{**self.options, "workspace_checks": [True, False, True, True]})["passed"])

    def test_nonempty_fixed_items_have_real_evidence_and_cannot_disappear(self):
        records, options = recorded_fixture(fixed=True)
        result = evaluate_records(records, **options)
        self.assertTrue(result["passed"], result)
        self.assertTrue(result["coverage"]["nonempty_fixed_meetings"])
        records[2]["response"]["tool_results"][0]["result"]["constraints"]["fixed_meetings"] = []
        result = evaluate_records(records, **options)
        self.assertFalse(result["passed"])
        self.assertIn("calculation_1_fixed_meetings", result["cases"][2]["failed_checks"])

    def test_reference_permission_scope_source_and_relevance_are_checked(self):
        mutations = [
            lambda rows: rows[3]["response"]["tool_results"][0]["result"].update(reference_text_transmitted=True),
            lambda rows: rows[3]["response"]["tool_results"][0]["result"]["scope"].update(plan_id="zju_business_big_data_2026"),
            lambda rows: rows[3]["response"]["citations"][0].update(text="fabricated source text"),
            lambda rows: rows[3]["response"]["citations"][0].update(enforce_automatically=True),
            lambda rows: rows[3]["response"].update(citations=[]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertFalse(self.evaluate_mutation(mutation)["passed"])
        self.assertFalse(evaluate_records(self.records, **self.options, reference_text_allowed=True)["passed"])

    def test_missing_records_or_evidence_do_not_pass(self):
        self.assertFalse(evaluate_records(self.records[:3], **self.options)["passed"])
        self.assertFalse(evaluate_records([{}] * 4, **self.options)["passed"])

    def test_failed_turn_with_null_or_malformed_decision_is_a_failed_evaluation(self):
        for decision in (None, [], "invalid", 42, False):
            with self.subTest(decision=decision):
                records = deepcopy(self.records[:1])
                records[0]["response"].update(status="failed", code="invalid_final_decision",
                                               reply_origin="service_error", model_decision=decision)
                result = evaluate_records(records, **self.options)
                self.assertFalse(result["passed"])
                self.assertIn("decision_structure", result["cases"][0]["failed_checks"])

    def test_evaluator_exception_preserves_original_receipt_and_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            def broken(*args, **kwargs):
                self.assertEqual(json.loads((output / "synthetic_turns.json").read_text()), self.records)
                self.assertEqual(json.loads((output / "receipt.json").read_text())["evaluation_status"], "pending")
                raise RuntimeError("PRIVATE_ERROR_SENTINEL")
            with patch("validation.run_live_chat.evaluate_records", side_effect=broken):
                result = save_run_result(output, self.records, {"cases": [{"status": "failed"}]}, **self.options)
            self.assertFalse(result["business_acceptance"]["passed"])
            self.assertEqual(result["evaluation_status"], "failed")
            self.assertEqual(json.loads((output / "receipt.json").read_text())["cases"], [{"status": "failed"}])
            self.assertEqual(json.loads((output / "business_evaluation.json").read_text())["error_type"], "RuntimeError")
            self.assertNotIn("PRIVATE_ERROR_SENTINEL", (output / "receipt.json").read_text())

    def test_interrupted_evaluator_leaves_pending_base_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with patch("validation.run_live_chat.evaluate_records", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    save_run_result(output, self.records, {"cases": [{"status": "failed"}]}, **self.options)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["evaluation_status"], "pending")
            self.assertEqual(receipt["cases"], [{"status": "failed"}])
            self.assertEqual(json.loads((output / "synthetic_turns.json").read_text()), self.records)

    def test_reassessment_never_reads_config_or_calls_model_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "synthetic_turns.json").write_text(json.dumps(self.records), encoding="utf-8")
            receipt_path = run / "receipt.json"
            receipt_path.write_text(json.dumps({"occurred_at": "synthetic-test", "reference_text_allowed": False,
                                               "cases": [{"workspace_unchanged": True}] * 4}), encoding="utf-8")
            original = receipt_path.read_bytes()
            output = run / "evaluation.json"
            with patch("validation.run_live_chat.read_config", side_effect=AssertionError("must not read .env")), \
                 patch("course_assistant.model_client.ModelClient.complete", side_effect=AssertionError("must not call API")), \
                 redirect_stdout(io.StringIO()):
                self.assertTrue(evaluate_saved_run(run, output))
                with self.assertRaises(FileExistsError):
                    evaluate_saved_run(run, output)
            self.assertEqual(receipt_path.read_bytes(), original)
            self.assertFalse(json.loads(output.read_text())["real_model_called_during_evaluation"])


if __name__ == "__main__":
    unittest.main()
