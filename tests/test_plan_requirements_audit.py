"""Semantic safety checks on synthetic text; no real student or school calls."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_plan_requirements import build_inventory, canonical, digest, parse_pages, resolve_output, scan_plan, source_plan, write_inventory


def meta():
    return {"plan_number": "SYNTHETIC_A", "plan_ref": "SYNTHETIC-PLAN-A", "cohort_from_directory": "2026",
            "major_name_raw": "独立合成方案", "pdf_sha256": "synthetic_pdf", "text_sha256": "synthetic_text"}


def synthetic_batch(root, text, accounts=None):
    for folder in ["normalized", "pdfs", "pdf_text", "objects"]:
        (root / folder).mkdir()
    pdf = b"synthetic non-rendered PDF fixture"
    pdf_hash = digest(pdf)
    (root / "pdfs/SYNTHETIC_A.pdf").write_bytes(pdf)
    (root / "objects" / f"{pdf_hash}.pdf").write_bytes(pdf)
    (root / "pdf_text/SYNTHETIC_A.txt").write_text(text)
    row = {"pyfaxx_id": "SYNTHETIC_SOURCE_KEY", "jxjhh": "SYNTHETIC_A", **(accounts or {})}
    directory = canonical({"items": [row]})
    directory_hash = digest(directory)
    (root / "objects" / f"{directory_hash}.json").write_bytes(directory)
    candidate = {"plan_number": "SYNTHETIC_A", "source_plan_key": "SYNTHETIC_SOURCE_KEY", "cohort": "2026",
                 "major_name": "独立合成方案", "college_name": "合成学院", "study_years_raw": "4", "degree_raw": "合成学位",
                 "minimum_graduation_credits_raw": "100+8", "notes_raw": None, "pdf_status": "success",
                 "pdf": {"named_file": "pdfs/SYNTHETIC_A.pdf", "object": f"objects/{pdf_hash}.pdf", "sha256": pdf_hash, "receipt_id": "synthetic-pdf-receipt"},
                 "source": {"sha256": directory_hash, "receipt_id": "synthetic-directory-receipt", "row_index": 0}}
    (root / "normalized/plan_candidates.json").write_bytes(canonical([candidate]))
    (root / "normalized/plan_manifest.json").write_bytes(canonical({"directory_records": 1}))
    return candidate


class PlanRequirementInventoryTests(unittest.TestCase):
    def test_reject_missing_duplicate_or_unordered_physical_pages(self):
        for text in ["=== SOURCE PAGE 2 ===\nX", "=== SOURCE PAGE 1 ===\nA\n=== SOURCE PAGE 1 ===\nB", "=== SOURCE PAGE 1 ===\nA\n=== SOURCE PAGE 3 ===\nB"]:
            with self.assertRaises(ValueError):
                parse_pages(text)

    def test_deferral_does_not_exclude_interleaved_normal_courses(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n六、第二课堂 +4学分\n课程号 课程名称\nLAB1001M劳动实践 2.0\n二课堂累计记点≥4\n七、第三课堂 +2学分\n参加社会实践1周。\n八、第四课堂 +2学分\n参加境外交流4周。\n")
        records, _ = scan_plan(meta(), pages)
        course = next(r for r in records if r["category"] == "ordinary_course_safety")
        second = next(r for r in records if r["category"] == "second_classroom")
        self.assertEqual(course["scope_status"], "retained_for_review")
        self.assertTrue(second["course_table_interleaving"])
        self.assertIn("累计记点≥4", second["source_spans"][0]["raw_text"])
        self.assertTrue(all(r["enforce_automatically"] is False for r in records))

    def test_zero_recognition_header_and_generic_body_are_both_preserved(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n五、其他必修环节(认定型学分)\n(1)美育类 0学分\n国际学生原则上不作认定要求，但鼓励修读。\n要求修读2学分美育类课程。\n(2)劳育类\n要求学生修读32学时劳动教育类课程。\n")
        records, _ = scan_plan(meta(), pages)
        arts = next(r for r in records if r["category"] == "arts_recognition")
        self.assertIn("0学分", arts["source_spans"][0]["raw_text"])
        self.assertIn("2学分", arts["source_spans"][0]["raw_text"])
        self.assertEqual(arts["scope_status"], "retained_for_review")
        self.assertIn("international_student_recognition_exception_with_generic_body", arts["interpretation_flags"])
        labor = next(r for r in records if r["category"] == "labor_recognition")
        self.assertEqual(labor["measurements_mentions"][0]["unit_raw"], "学时")
        self.assertIsNone(labor["measurements_mentions"][0]["comparator"])

    def test_cross_page_clause_replays_physical_page_spans(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n六、第二课堂 +4学分\n开始条文\n=== SOURCE PAGE 2 ===\n续页条文\n七、第三课堂 +2学分\n三课堂正文\n")
        records, _ = scan_plan(meta(), pages)
        second = next(r for r in records if r["category"] == "second_classroom")
        self.assertEqual([s["page"] for s in second["source_spans"]], [1, 2])
        self.assertEqual(second["source_spans"][1]["raw_text"], "续页条文")

    def test_school_name_inside_requirement_is_not_a_watermark_boundary(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n为保证教学质量，进入下一阶段须满足：\n1.完成论文。\n2.达到浙江大学研究生英语免修条件。\n3.前四年海外交流≥1次。\n\n")
        records, _ = scan_plan(meta(), pages)
        gate = next(r for r in records if r["category"] == "undergraduate_transition_gate")
        self.assertIn("海外交流≥1次", gate["source_spans"][0]["raw_text"])

    def test_wrapped_law_transition_and_unknown_gpa_do_not_gain_threshold(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n本项目进入\n专业硕士阶段的要求如下：论文良好及以上。\n\n统计专业课平均绩点未达到要求则退出项目。\n")
        records, _ = scan_plan(meta(), pages)
        self.assertIn("undergraduate_transition_gate", {r["category"] for r in records})
        gpa = next(r for r in records if r["category"] == "dual_degree_progression")
        self.assertIn("gpa_threshold_not_given", gpa["interpretation_flags"])
        self.assertEqual(gpa["measurements_mentions"], [])

    def test_graduate_degree_gate_is_distinct_from_user_deferral(self):
        pages = parse_pages("=== SOURCE PAGE 1 ===\n博士阶段培养方案\n毕业和授予学位标准\n1.通过学位论文答辩。\n2.创新成果达到本学科要求。\n浙江大学  Zhejiang University\n")
        records, _ = scan_plan(meta(), pages)
        gate = next(r for r in records if r["category"] == "graduate_stage_gates")
        self.assertEqual(gate["degree_stage"], "graduate")
        self.assertEqual(gate["scope_status"], "retained_for_review")
        self.assertFalse(gate["enforce_automatically"])

    def test_directory_zero_unknown_and_missing_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = synthetic_batch(root, "=== SOURCE PAGE 1 ===\n2026级独立合成方案\n", {"dektxf": 0, "dektxf3": None})
            result, _ = source_plan(root, candidate)
            accounts = {a["category"]: a for a in result["directory_credit_accounts"]}
            self.assertEqual(accounts["second_classroom"]["raw_value"], 0)
            self.assertEqual(accounts["second_classroom"]["field_state"], "known")
            self.assertEqual(accounts["third_classroom"]["field_state"], "unknown")
            self.assertEqual(accounts["fourth_classroom"]["field_state"], "not_provided")

    def test_conflicting_cohort_page_is_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = synthetic_batch(root, "=== SOURCE PAGE 1 ===\n2026级合成培养方案\n=== SOURCE PAGE 2 ===\n2025级合成课程修读导图\n")
            result, pages = source_plan(root, candidate)
            self.assertEqual(result["conflicting_cohort_mentions"][0]["page"], 2)
            self.assertIn("2025级", pages[2])
            self.assertFalse(result["graduation_total_modified"])

    def test_inventory_reproducible_and_inputs_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic_batch(root, "=== SOURCE PAGE 1 ===\n2026级合成方案\n六、第二课堂 +4学分\n要求活动认定。\n", {"dektxf": "4"})
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            repo = Path(__file__).resolve().parents[1]
            a = build_inventory(root, repo)
            b = build_inventory(root, repo)
            self.assertEqual(canonical(a), canonical(b))
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_real_output_path_cannot_escape_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for requested in ["docs/private.json", "local-only", "local-only/../../outside"]:
                with self.assertRaises(ValueError):
                    resolve_output(repo, requested)
            self.assertEqual(resolve_output(repo, "local-only/audit"), (repo / "local-only/audit").resolve())
            (repo / "local-only").mkdir()
            (repo / "local-only/escape").symlink_to(repo / "docs", target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_output(repo, "local-only/escape/output")

    def test_content_addressed_runs_are_idempotent_private_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fixture"
            root.mkdir()
            synthetic_batch(root, "=== SOURCE PAGE 1 ===\n2026级合成方案\n六、第二课堂 +4学分\n", {"dektxf": "4"})
            payload = build_inventory(root, Path(__file__).resolve().parents[1])
            output = Path(tmp) / "local-only/results"
            a, hash_a = write_inventory(payload, root, output, "synthetic_source_sha")
            before = {p.name: p.read_bytes() for p in a.iterdir()}
            b, hash_b = write_inventory(payload, root, output, "synthetic_source_sha")
            self.assertEqual((a, hash_a), (b, hash_b))
            self.assertEqual(before, {p.name: p.read_bytes() for p in b.iterdir()})
            self.assertEqual(a.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in a.iterdir()))
            (a / "summary.json").write_bytes(b"historical different derived content")
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                write_inventory(payload, root, output, "synthetic_source_sha")
            self.assertEqual((a / "summary.json").read_bytes(), b"historical different derived content")


if __name__ == "__main__":
    unittest.main()
