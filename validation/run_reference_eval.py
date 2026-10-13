"""Fixed local reference questions; no model, personal data or school requests."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from course_assistant.reference_retrieval import ReferenceIndex

M = "zju_materials_science_engineering_2026"
J = "zju_journalism_2026"
B = "zju_business_big_data_2026"
# Expected records manually identified in the checked JSON; not a real-user benchmark.
CASES = [
    ("R01", M, "Python", "ok", {M + ":CS1006G"}),
    ("R02", M, "专业模块课程", "ok", {"MSE26-R02"}),
    # SRC-012 legitimately compares near-name codes across plans. A diagnostic
    # mention must not be promoted to a course membership in the selected plan.
    ("R03", M, "CS1001G", "ok", {"SRC-012"}),
    ("R04", J, "CS1243G", "ok", {"SRC-002"}),
    ("R05", J, "个性修读", "ok", {"JOUR26-R04", "SRC-001"}),
    ("R06", B, "统计学 绩点", "ok", {"BDA26-R01", "SRC-004"}),
    ("R07", None, "Python", "needs_scope", set()),
    ("R08", M, "nonexistentexactcourse999", "no_match_in_scope", set()),
]


def main():
    index = ReferenceIndex()
    rows = []
    for case_id, plan, query, status, expected in CASES:
        result = index.search(query, plan_id=plan, expected_corpus_version=index.corpus_version, limit=5)
        actual = [c["record_id"] for c in result["matches"]]
        valid_references = True
        for chunk in result["matches"]:
            resolved = index.resolve(chunk["chunk_id"], plan_id=plan,
                                     expected_corpus_version=index.corpus_version)
            valid_references &= (resolved["chunk"]["text_sha256"] == sha256(chunk["text"].encode()).hexdigest())
        no_foreign_course = all(plan in c["plan_ids"] for c in result["matches"])
        if case_id == "R03":
            no_foreign_course &= all(c["record_kind"] == "issues" for c in result["matches"])
        ok = result["status"] == status and expected.issubset(actual) and valid_references and no_foreign_course
        rows.append({"case_id": case_id, "plan_id": plan, "query": query,
                     "expected_status": status, "expected_records_at_5": sorted(expected),
                     "actual_status": result["status"], "actual_records_at_5": actual,
                     "references_rechecked": valid_references,
                     "scope_and_diagnostic_role_checked": no_foreign_course, "passed": ok})
    paths = [ROOT / "course_assistant/reference_retrieval.py", Path(__file__),
             ROOT / "tests/test_reference_retrieval.py"]
    report = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "git_base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "corpus": index.describe(), "top_k": 5, "cases": rows,
              "passed": sum(r["passed"] for r in rows), "total": len(rows),
              "source_sha256": {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths},
              "scope": "8 developer reference fixtures only; no real user/model/personal permission evaluation"}
    output = ROOT / "validation/reference_eval_20261009.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": len(rows), "cases": rows}, ensure_ascii=False))
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
