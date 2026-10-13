"""Explicit real-model smoke run on fixed synthetic data; transcripts stay local-only."""
from pathlib import Path
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from course_assistant.importer import digest
from course_assistant.model_client import ModelClient, read_config
from course_assistant.planner import generate_plans, validate_plan
from course_assistant.reference_retrieval import ReferenceIndex
from course_assistant.server import Application


SCENARIOS = [
    ("查询我的合成课程目录和现在保留的安排。", "get_my_context"),
    ("保留体育，尽量周五空闲，生成两到三个合成课表。", "generate_my_plan"),
    ("继续保留体育，另锁定 DEMO-ML-B，并要求周五必须空闲。请重新校验，不要放宽条件。", "generate_my_plan"),
    ("帮我查找当前参考培养方案中 Python 的相关依据，说明是否已审核。", "search_plan_references"),
]


def decision_evidence_checks(tools, decision, final_constraints):
    """Independently verify a server-issued evidence index and later observations."""
    if not isinstance(decision, dict):
        return {"decision_structure": False}
    index = decision.get("source_tool_result_index")
    if type(index) is not int or not 0 <= index < len(tools):
        return {"decision_evidence_index": False}
    selected = tools[index]
    name, result = selected["name"], selected["result"]
    outcome = {"get_my_context": "context", "audit_my_progress": "audit", "find_my_sections": "sections"}.get(name)
    if name == "generate_my_plan":
        outcome = result["status"]
    elif name == "validate_my_plan":
        outcome = result["result"]
    elif name == "search_plan_references":
        outcome = "reference_available" if result["status"] == "ok" else result["status"]
    checks = {"decision_evidence_index": True,
              "decision_evidence_name": decision.get("source_tool") == name,
              "decision_matches_tool": decision.get("kind") == "answer" and outcome is not None and decision.get("outcome") == outcome,
              "decision_latest_same_tool": all(tool["name"] != name for tool in tools[index + 1:])}
    if name not in {"generate_my_plan", "validate_my_plan"}:
        return checks
    bookkeeping = {"revision", "previous_revision", "previous_constraints_fingerprint",
                   "revision_warnings", "revision_actions", "validation_required"}
    def business_digest(value):
        return digest({key: item for key, item in value.items() if key not in bookkeeping})
    effective = business_digest(result["constraints"])
    checks["decision_current_conditions"] = effective == business_digest(final_constraints)
    checks["decision_conditions_digest"] = decision.get("effective_constraints_sha256") == effective
    compatible = True
    for later in tools[index + 1:]:
        other = later["result"]
        if later["name"] not in {"generate_my_plan", "validate_my_plan"} or business_digest(other["constraints"]) != effective:
            continue
        if name == "generate_my_plan" and later["name"] == "validate_my_plan":
            same_plan = any(set(plan["section_ids"]) == set(other["section_ids"]) for plan in result.get("plans", []))
            if outcome == "feasible" and same_plan and other["result"] != "pass":
                compatible = False
            if outcome == "scoped_infeasible" and other["result"] == "pass":
                compatible = False
        elif name == "validate_my_plan" and later["name"] == "generate_my_plan":
            if outcome == "pass" and other["status"] == "scoped_infeasible":
                compatible = False
    checks["decision_later_evidence_compatible"] = compatible
    return checks


def evaluate_records(records, *, initial_workspace, initial_versions, workspace_checks,
                     reference_text_allowed=False, reference_index=None):
    """Offline business acceptance; this function never reads configuration or calls a model.

    workspace_checks must come from the live observer, not from response claims.
    Recomputing validates recorded calculation evidence; it does not turn an
    offline replay into a fresh model run or browser/school acceptance.
    """
    reference_index = reference_index or ReferenceIndex()
    demo = initial_workspace["demo"]
    prior_constraints = deepcopy(initial_workspace["constraints"])
    plan_id = demo["student"]["plan_id"]
    cases, previous, request_ids = [], None, set()
    for number, row in enumerate(records, 1):
        checks = {}
        def check(name, value):
            checks[name] = bool(value)
        try:
            request, response = row["request"], row["response"]
            tools = response["tool_results"]
            check("completed", response["status"] == "completed")
            check("synthetic_preview", response.get("data_kind") == "synthetic" and response.get("preview_only") is True)
            check("verified_reply", response.get("reply_origin") == "verified_tool_presentation" and
                  isinstance(response.get("reply"), str) and bool(response["reply"].strip()))
            check("test_message", number <= len(SCENARIOS) and request["message"] == SCENARIOS[number - 1][0])
            check("consent", request.get("consent_remote") is True)
            check("unique_request", request["request_id"] not in request_ids)
            request_ids.add(request["request_id"])
            check("same_conversation", request.get("conversation_id") == (previous["conversation_id"] if previous else None)
                  and (previous is None or response["conversation_id"] == previous["conversation_id"]))
            check("ordered_turn", type(response["turn_id"]) is int and response["turn_id"] == number and
                  request["expected_turn"] == number - 1)
            check("history_retained", number == 1 or response.get("history_reset") is False)
            check("workspace_versions", response["versions"] == initial_versions)
            check("workspace_unchanged", number <= len(workspace_checks) and workspace_checks[number - 1] is True)
            check("tools_executed", isinstance(tools, list) and bool(tools))
            decision = response["model_decision"]
            calculations = [tool for tool in tools if tool["name"] in {"generate_my_plan", "validate_my_plan"}]
            final_constraints = calculations[-1]["result"]["constraints"] if calculations else prior_constraints
            checks.update(decision_evidence_checks(tools, decision, final_constraints))
            citations = response["citations"]
            check("selected_citations", isinstance(decision, dict) and
                  decision.get("cited_chunk_ids") == [item["chunk_id"] for item in citations])
            for position, tool in enumerate(calculations):
                calculated, constraints = tool["result"], tool["result"]["constraints"]
                prefix = f"calculation_{position + 1}_"
                for field in ("locked_section_ids", "preloaded_section_ids", "requested_course_ids", "excluded_section_ids"):
                    check(prefix + field, set(prior_constraints.get(field, [])) <= set(constraints.get(field, [])))
                check(prefix + "fixed_meetings", all(item in constraints.get("fixed_meetings", []) for item in prior_constraints.get("fixed_meetings", [])))
                check(prefix + "revision_chain", constraints.get("previous_revision") == prior_constraints["revision"] and
                      constraints.get("revision") == prior_constraints["revision"] + 1 and
                      constraints.get("previous_constraints_fingerprint") == digest(prior_constraints))
                check(prefix + "versions", calculated.get("workspace_versions") == initial_versions)
                check(prefix + "preview", calculated.get("preview_only") is True and calculated.get("data_kind") == "synthetic")
                if tool["name"] == "generate_my_plan":
                    recomputed = generate_plans(demo["offering_snapshot"], demo["student"], constraints,
                                               demo["academic_snapshot"]["records"], max_nodes=10000, limit=3)
                    check(prefix + "deterministic_status", calculated["status"] == recomputed["status"])
                    for category in ("plans", "pending_plans"):
                        plans = calculated.get(category, [])
                        check(prefix + category + "_members", [plan["section_ids"] for plan in plans] ==
                              [plan["section_ids"] for plan in recomputed[category]])
                        check(prefix + category + "_validations", all(plan["validation"] == validate_plan(
                            plan["section_ids"], demo["offering_snapshot"], demo["student"], constraints,
                            demo["academic_snapshot"]["records"]) for plan in plans))
                else:
                    recomputed = validate_plan(calculated["section_ids"], demo["offering_snapshot"], demo["student"], constraints,
                                               demo["academic_snapshot"]["records"])
                    check(prefix + "deterministic_validation", all(calculated.get(key) == value for key, value in recomputed.items()))
                prior_constraints = deepcopy(constraints)
            if number == 1:
                contexts = [tool["result"] for tool in tools if tool["name"] == "get_my_context"]
                check("context_query", bool(contexts) and contexts[-1].get("plan_id") == plan_id)
                if contexts:
                    current = contexts[-1]
                    check("context_catalog", {item["course_id"] for item in current["courses"]} == {item["course_id"] for item in demo["courses"]})
                    check("context_locks", current["constraints"]["locked_section_ids"] == prior_constraints["locked_section_ids"])
            elif number == 2:
                generated = [tool["result"] for tool in calculations if tool["name"] == "generate_my_plan"]
                check("recommendation_query", bool(generated))
                if generated:
                    current = generated[-1]
                    check("soft_friday", current["constraints"].get("friday_free") == "soft")
                    check("two_or_three_valid_plans", current["status"] == "feasible" and 2 <= len(current["plans"]) <= 3 and
                          all(plan["validation"]["result"] == "pass" and "DEMO-PE-A" in plan["section_ids"] for plan in current["plans"]))
            elif number == 3:
                check("recalculation_query", bool(calculations))
                if calculations:
                    current = calculations[-1]
                    result = current["result"]
                    check("both_locks_retained", {"DEMO-PE-A", "DEMO-ML-B"} <= set(result["constraints"]["locked_section_ids"]))
                    check("hard_friday", result["constraints"].get("friday_free") == "hard")
                    check("conflict_not_passed", (current["name"] == "generate_my_plan" and result["status"] == "scoped_infeasible" and not result["plans"]) or
                          (current["name"] == "validate_my_plan" and result["result"] == "fail" and
                           {"DEMO-PE-A", "DEMO-ML-B"} <= set(result["section_ids"]) and
                           any(item["code"] == "friday_free" and item["result"] == "fail" for item in result["checks"])))
            elif number == 4:
                searches = [tool["result"] for tool in tools if tool["name"] == "search_plan_references"]
                check("reference_query", bool(searches) and bool(citations))
                check("reference_egress_closed", reference_text_allowed is False and
                      all(result.get("reference_text_transmitted") is False for result in searches))
                check("reference_scope", all(result["scope"]["plan_id"] == plan_id for result in searches))
                matches = {item["chunk_id"]: item for result in searches for item in result["matches"]}
                check("references_are_retrieved", all(item["chunk_id"] in matches and item == matches[item["chunk_id"]] for item in citations))
                check("python_evidence_present", any(item.get("record_id") in {plan_id + ":CS1006G", "MSE26-R01"} and
                      "python" in (item.get("title", "") + item.get("text", "")).lower() for item in citations))
                for position, citation in enumerate(citations):
                    original = reference_index.resolve(citation["chunk_id"], plan_id=plan_id,
                                                       expected_corpus_version=reference_index.corpus_version)["chunk"]
                    check(f"citation_{position + 1}_source", all(citation.get(key) == value for key, value in original.items()))
                    check(f"citation_{position + 1}_inactive", citation.get("enforce_automatically") is False and citation.get("official_status") == "unconfirmed" and
                          (citation.get("record_kind") != "candidates" or citation.get("review_status") == "candidate_pending_human_review"))
                check("partial_coverage_disclosed", all(result.get("coverage") == "partial_handoff_json_not_pdf_full_text" and bool(result.get("warnings")) for result in searches))
            else:
                check("known_scenario", False)
            previous = response
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            check("complete_evidence_structure", False)
        cases.append({"case": number, "passed": bool(checks) and all(checks.values()), "checks": checks,
                      "failed_checks": [key for key, value in checks.items() if not value]})
    return {"evaluation_version": "business-results-v3.1", "evaluation_kind": "offline_record_evaluation",
            "passed": len(records) == len(SCENARIOS) and len(workspace_checks) == len(SCENARIOS) and all(item["passed"] for item in cases),
            "cases": cases, "baseline_versions": initial_versions,
            "coverage": {"nonempty_fixed_meetings": bool(initial_workspace["constraints"].get("fixed_meetings")),
                         "browser": "separate", "school": "not_run", "reference_semantic_answer": "not_tested_text_egress_closed",
                         "unsupported_reference_question": "not_in_these_four_scenarios"}}


def evaluate_saved_run(directory, output=None):
    """Re-evaluate an existing run without overwriting it or reading .env."""
    directory = Path(directory)
    records_path, receipt_path = directory / "synthetic_turns.json", directory / "receipt.json"
    records, receipt = json.loads(records_path.read_text()), json.loads(receipt_path.read_text())
    with tempfile.TemporaryDirectory() as temporary:
        app = Application(Path(temporary) / "evaluation.sqlite3", mock_enabled=True)
        token, _ = app.auth.login_mock({"profile_id": "demo-a"})
        context = app.auth.authenticate(token)
        workspace = app.workspace(context)
        result = evaluate_records(records, initial_workspace=workspace, initial_versions=app.versions(workspace),
                                  workspace_checks=[item.get("workspace_unchanged") for item in receipt["cases"]],
                                  reference_text_allowed=receipt.get("reference_text_allowed"))
    result.update(evaluated_at=datetime.now(timezone.utc).isoformat(), real_model_called_during_evaluation=False,
                  original_run=receipt.get("occurred_at"), original_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                  original_records_sha256=hashlib.sha256(records_path.read_bytes()).hexdigest(),
                  evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  original_source_sha256=receipt.get("source_sha256"),
                  recomputation_source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                               ("course_assistant/planner.py", "course_assistant/reference_retrieval.py", "course_assistant/importer.py")})
    output = Path(output) if output else directory / ("evaluation_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"evaluation": str(output), "passed": result["passed"],
                      "failed_checks": [case["failed_checks"] for case in result["cases"]]}, ensure_ascii=False))
    return result["passed"]


def save_run_result(output, records, receipt, **evaluation_options):
    """Persist original observations before evaluating them; never lose a failed run.

    The pending base receipt also survives an interrupted or broken evaluator.
    Only after a separate evaluation file exists is this new run's receipt
    replaced atomically with its completed evaluation status.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    def write_new(path, value):
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    write_new(output / "synthetic_turns.json", records)
    result = {**receipt, "evaluation_status": "pending", "business_acceptance": None,
              "business_evaluation_file": "business_evaluation.json"}
    receipt_path = output / "receipt.json"
    write_new(receipt_path, result)
    try:
        evaluation = evaluate_records(records, **evaluation_options)
        result["evaluation_status"] = "completed"
    except Exception as error:
        # Error messages may contain submitted data. Keep only the error class.
        evaluation = {"passed": False, "evaluation_kind": "offline_record_evaluation",
                      "status": "evaluator_error", "error_type": type(error).__name__}
        result["evaluation_status"] = "failed"
    write_new(output / "business_evaluation.json", evaluation)
    result["business_acceptance"] = evaluation
    temporary_receipt = output / "receipt.completed.tmp"
    write_new(temporary_receipt, result)
    temporary_receipt.replace(receipt_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--evaluate-run", type=Path, help="只离线复评该目录中的既有合成回执，不读取模型配置、不调用模型")
    parser.add_argument("--evaluation-output", type=Path, help="离线评价的新文件；已存在时拒绝覆盖")
    args = parser.parse_args()
    if args.evaluate_run:
        if not evaluate_saved_run(args.evaluate_run, args.evaluation_output):
            raise SystemExit(1)
        return
    if args.evaluation_output:
        parser.error("--evaluation-output 只用于 --evaluate-run")
    model = ModelClient(read_config(args.model_env_file))
    if not model.status()["configured"]:
        parser.exit(2, "尚未配置模型；没有发出请求。请在本机 .env 设置 DEEPSEEK_API_KEY。\n")
    if model.allow_reference_text:
        parser.exit(2, "本组验收要求参考原文外传关闭；未发出请求，请使用 COURSE_ALLOW_REFERENCE_TEXT=false。\n")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    records, receipts = [], []
    with tempfile.TemporaryDirectory() as directory:
        app = Application(Path(directory) / "smoke.sqlite3", mock_enabled=True, model=model)
        token, _ = app.auth.login_mock({"profile_id": "demo-a"})
        context = app.auth.authenticate(token)
        initial_workspace = app.workspace(context)
        initial_versions = app.versions(initial_workspace)
        before = digest(initial_workspace)
        previous = None
        for index, (message, expected_tool) in enumerate(SCENARIOS):
            body = {"message": message, "request_id": str(uuid.uuid4()), "consent_remote": True,
                    "conversation_id": previous["conversation_id"] if previous else None,
                    "expected_turn": previous["turn_id"] if previous else 0}
            response = app.chat.turn(body, context)
            records.append({"request": body, "response": response})
            tool_names = [row["name"] for row in response["tool_results"]]
            acceptable_tools = {expected_tool, "validate_my_plan"} if index == 2 else {expected_tool}
            record = {"case": index + 1, "status": response["status"], "tools": tool_names,
                      "acceptable_tools": sorted(acceptable_tools),
                      "expected_tool_called": bool(acceptable_tools.intersection(tool_names)),
                      "workspace_unchanged": before == digest(app.workspace(context)),
                      "result_digest": digest(response), "code": response.get("code"),
                      "generation_statuses": [row["result"].get("status") for row in response["tool_results"]
                                              if row["name"] == "generate_my_plan"]}
            receipts.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            previous = response
            if response["status"] != "completed":
                break
    output = ROOT / "local-only" / "chat_acceptance" / run_id
    result = {"occurred_at": run_id, "provider": model.status()["provider"], "model": model.model,
              "reference_text_allowed": model.allow_reference_text, "real_model_run_attempted": True,
              "school_acceptance": "not_run", "browser_acceptance": "separate", "cases": receipts,
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / "course_assistant").glob("*.py"))}}
    result = save_run_result(output, records, result, initial_workspace=initial_workspace,
                             initial_versions=initial_versions,
                             workspace_checks=[item["workspace_unchanged"] for item in receipts],
                             reference_text_allowed=model.allow_reference_text)
    print("本机测试回执：" + str(output / "receipt.json"), flush=True)
    success = result["business_acceptance"]["passed"]
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
