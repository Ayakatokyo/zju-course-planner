"""Run the real, pinned Harness² engine against the isolated academic adapter.

Explicit --real-model and a local --model-factory are required for calls. The
factory returns the existing GPT-6-Luna/high client; no credentials enter receipts.
This script never applies a candidate to the running application.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.harness2_adapter import (AcademicAdapter, TextProposer, MODEL, UPSTREAM_REVISION,
    digest, dumps, save, snapshot)
from validation.harness2_cases import DEVELOPMENT, HELD_OUT, GRADES, AUDIT, TIMETABLE, CURRICULUM


def upstream(path):
    path = path.resolve()
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], text=True)
    if revision != UPSTREAM_REVISION or status:
        raise ValueError("Harness² must be the clean pinned upstream checkout")
    sys.path.insert(0, str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-factory", type=Path)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--phase", choices=("baseline", "optimize", "verify", "compare-runtime"), default="baseline")
    parser.add_argument("--baseline-ref", default="HEAD", help="compare-runtime: frozen dialogue source from this local Git revision")
    parser.add_argument("--candidate", default="c1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    upstream(args.upstream)
    from harness2.core.recursion import run_sequential
    from harness2.core.selection import run_selection, reflect_adoption
    from harness2.core.library import Library
    from harness2.core.spec import RunView
    from harness2.core.tree import read_tree

    baseline = snapshot()
    manifest = {"schema": "academic-harness2-v1", "upstream_revision": UPSTREAM_REVISION,
        "model": MODEL, "reasoning_effort": "high", "baseline": baseline,
        "sources_sha256": digest({"grades": GRADES, "audit": AUDIT, "timetable": TIMETABLE, "curriculum": CURRICULUM}),
        "cases_sha256": digest({"development": DEVELOPMENT, "heldout": HELD_OUT}),
        "implementation_sha256": {str(p.relative_to(ROOT)): digest(p.read_text()) for p in
            [ROOT / "validation/harness2_adapter.py", ROOT / "validation/harness2_cases.py",
             ROOT / "course_assistant/personal_dialogue.py", ROOT / "course_assistant/academic_harness.py"]}}
    baseline_source = None
    if args.phase == "compare-runtime":
        revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], cwd=ROOT, text=True).strip()
        changed = subprocess.check_output(["git", "diff", revision, "--name-only", "--", "course_assistant"], cwd=ROOT, text=True).splitlines()
        if set(changed) - {"course_assistant/personal_dialogue.py"}:
            raise ValueError("runtime comparison supports a dialogue-only change; other production modules must match")
        baseline_source = subprocess.check_output(["git", "show", revision + ":course_assistant/personal_dialogue.py"], cwd=ROOT, text=True)
        manifest.update(baseline_revision=revision, baseline_dialogue_sha256=digest(baseline_source))
    if args.dry_run:
        print(dumps({k: v for k, v in manifest.items() if k != "baseline"}))
        return 0
    if not args.real_model or args.model_factory is None:
        parser.error("model calls require --real-model and --model-factory")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old != manifest:
            raise ValueError("implementation, source, model or cases changed; use a new output directory")
    else:
        save(manifest_path, manifest)
    if args.phase in {"optimize", "compare-runtime"} and (output / "work").exists():
        raise ValueError("improvement already started; use a new output directory to preserve all candidate evidence")
    spec = importlib.util.spec_from_file_location("academic_experiment_model", args.model_factory.resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = AcademicAdapter(output, module.create_model, baseline)
    if args.phase == "compare-runtime":
        baseline_path = output / "baseline_dialogue.py"
        baseline_path.write_text(baseline_source, encoding="utf-8")
        old_spec = importlib.util.spec_from_file_location("course_assistant._comparison_baseline", baseline_path)
        old_dialogue = importlib.util.module_from_spec(old_spec)
        old_spec.loader.exec_module(old_dialogue)
        runs, checks = {}, {}
        for task in ("development", "heldout"):
            for label in ("base1", "compact"):
                run_id = task + "-" + label
                if label == "base1":
                    with patch("validation.harness2_adapter.dialogue", old_dialogue):
                        run_dir = adapter.execute(task, None, run_id)
                else:
                    run_dir = adapter.execute(task, None, run_id)
                checks[run_id] = adapter.score(run_dir, task)
                if task == "development":
                    runs[label] = RunView(label=label, run_id=run_id, run_dir=run_dir, ok=True, delivered=adapter.delivered(run_dir))
        save(output / "runtime_comparison.json", checks)
        selection = run_selection("development", 1, "seq", runs, adapter=adapter,
            prop=TextProposer(module.create_model, baseline, output / "improvement_calls"), work_dir=output / "work")
        print(dumps({"selected_runtime": selection.chosen_label, "selection_ok": selection.ok,
            "mechanical_results": {k: v["mechanical_passed"] for k, v in checks.items()}}))
        return 0
    base_dir = adapter.execute("development", None, "base1")
    base = RunView(label="base1", run_id="base1", run_dir=base_dir, ok=True, delivered=adapter.delivered(base_dir))
    if args.phase == "baseline":
        save(output / "baseline_checks.json", adapter.score(base_dir, "development"))
        return 0
    work = output / "work"
    if args.phase == "optimize":
        if (output / "recursion.json").exists():
            raise ValueError("recursion already recorded; inspect it or use verify, never overwrite evidence")
        proposer = TextProposer(module.create_model, baseline, output / "improvement_calls")
        result = run_sequential("development", 1, [base], adapter=adapter, prop=proposer,
            work_dir=work, run_id_of=lambda label: label, base_tree=baseline,
            propose_workers=1, rollout_workers=1)
        save(output / "recursion.json", result.to_dict())
        selection = run_selection("development", 1, "seq", result.rollouts, adapter=adapter,
            prop=proposer, work_dir=work)
        experience = reflect_adoption("development", selection, result.rollouts, adapter=adapter,
            prop=proposer, work_dir=work, decisions={s.label: s.notes for s in result.stages})
        selected = baseline if selection.chosen_is_base else result.trees.get(selection.chosen_label)
        if selected is not None:
            Library(output / "library", "academic", adapter.surface()).add("development", selected,
                why=selection.why, label=selection.chosen_label, run_id=selection.chosen_run_id, experience=experience)
        # Separate post-selection checks; their values never guide the improver.
        save(output / "development_checks.json", {label: adapter.score(run.run_dir, "development")
            for label, run in result.rollouts.items() if run.delivered})
        print(dumps({"selected": selection.chosen_label, "selection_ok": selection.ok,
            "stage_fallbacks": result.fell_back, "experience_retained": bool(experience)}))
    else:
        if not args.candidate.isalnum():
            raise ValueError("invalid candidate label")
        candidate_dir = work / "harnesses" / args.candidate
        adapter.validate_tree(read_tree(adapter.surface(), candidate_dir))
        before = adapter.execute("heldout", None, "heldout-base1")
        after = adapter.execute("heldout", candidate_dir, "heldout-" + args.candidate)
        save(output / ("heldout_checks_" + args.candidate + ".json"), {
            "base": adapter.score(before, "heldout"), args.candidate: adapter.score(after, "heldout")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
