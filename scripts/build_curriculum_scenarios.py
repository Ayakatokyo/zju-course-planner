"""Build isolated, source-bound synthetic student trajectories from reviewed PDFs.

This offline tool neither opens the application database nor calls school/model
APIs. The review spec and generated curriculum-derived data belong in local-only.
It is deliberately strict about this reviewed table layout, not a general PDF
or official curriculum-rule parser.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CODE = re.compile(r"[A-Z]{2,5}[0-9]{3,5}[A-Z]*[0-9]?")
TERM = re.compile(r"([一二三四])\(([秋冬春夏短]+)\)")
SEASONS = "秋冬春夏短"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("invalid nonnegative decimal")
    return result


def total(rows):
    return str(sum((number(r["credits"]) for r in rows), Decimal(0)))


def schedule(raw):
    """Preserve alternatives; selecting one is explicitly a scenario choice."""
    choices = []
    for part in raw.split("/"):
        match = TERM.fullmatch(part)
        if not match or list(match[2]) != sorted(set(match[2]), key=SEASONS.index):
            raise ValueError("unsupported suggested term: " + raw)
        choices.append({"study_year": "一二三四".index(match[1]) + 1,
                        "seasons": list(match[2])})
    return choices


def extract_rows(xml, pages, pdf_hash):
    """Read aligned 9pt course rows, excluding large rotated watermarks."""
    document = ET.fromstring(xml)
    all_pages = document.findall(".//{*}page")
    rows = []
    for page_number in pages:
        page = all_pages[page_number - 1]
        words = [{"x": float(w.get("xMin")), "y": float(w.get("yMin")),
                  "right": float(w.get("xMax")), "bottom": float(w.get("yMax")),
                  "text": w.text or ""} for w in page.findall("{*}word")
                 if abs(float(w.get("yMax")) - float(w.get("yMin")) - 9) < .15]
        for word in sorted(words, key=lambda w: (w["y"], w["x"])):
            if not CODE.fullmatch(word["text"]):
                continue
            line = sorted([w for w in words if abs(w["y"] - word["y"]) < .2
                           and w["x"] >= word["x"]], key=lambda w: w["x"])
            cells = [w["text"] for w in line]
            if len(cells) != 6 or not re.fullmatch(r"\d+\.\d+", cells[2]):
                raise ValueError(f"review required: PDF page {page_number}, {word['text']}")
            if any(r["course_code"] == cells[0] for r in rows):
                raise ValueError("duplicate code requires explicit review: " + cells[0])
            rows.append({"course_code": cells[0], "course_name": cells[1],
                         "credits": str(number(cells[2])), "weekly_hours_raw": cells[3],
                         "total_hours_raw": cells[4], "suggested_term_raw": cells[5],
                         "suggested_term_choices": schedule(cells[5]),
                         "source": {"pdf_sha256": pdf_hash, "physical_page": page_number,
                                    "bbox": [word["x"], word["y"], line[-1]["right"], line[-1]["bottom"]],
                                    "cells": cells, "row_sha256": sha(json.dumps(cells, ensure_ascii=False).encode())}})
    return rows


def reviewed_plan(spec, base):
    pdf = (base / spec["pdf"]).resolve()
    digest = sha(pdf.read_bytes())
    if digest != spec["pdf_sha256"]:
        raise ValueError("source PDF changed; review is required")
    xml = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], check=True,
                         capture_output=True).stdout
    rows = extract_rows(xml, spec["catalog_pages"], digest)
    if len(rows) != spec["expected_catalog_rows"]:
        raise ValueError("catalog row coverage changed")
    by_code = {r["course_code"]: r for r in rows}
    assigned = set()
    modules = []
    for group in spec["modules"]:
        codes = group["course_codes"]
        if len(set(codes)) != len(codes) or not set(codes) <= by_code.keys() or assigned & set(codes):
            raise ValueError("invalid or overlapping module membership")
        assigned.update(codes)
        selected = codes if group["selection"] == "all" else group["selection"]
        if not isinstance(selected, list) or len(set(selected)) != len(selected) or not set(selected) <= set(codes):
            raise ValueError("selected course outside reviewed module")
        sum_selected = number(total([by_code[c] for c in selected]))
        minimum = number(group["minimum_credits"])
        if sum_selected < minimum:
            raise ValueError("full synthetic path does not meet named module minimum")
        if group["selection"] == "all" and sum_selected != minimum:
            raise ValueError("mandatory course sum differs from reviewed module heading")
        for code in codes:
            by_code[code].update(module=group["name"], selected=code in selected,
                                 selection_reason="reviewed_required_list" if group["selection"] == "all" else "synthetic_choice_within_source_pool")
        modules.append({k: v for k, v in group.items() if k not in {"course_codes", "selection"}} |
                       {"selected_codes": selected, "planned_path_credits": str(sum_selected)})
    if assigned != set(by_code):
        raise ValueError("every catalog row must have a reviewed module")
    # Identity and each rule quote must occur verbatim in the source's small-font
    # text after whitespace removal. Quotes are retained alongside page locators.
    source_pages = ET.fromstring(xml).findall(".//{*}page")
    for assertion in spec["source_assertions"]:
        page = source_pages[assertion["page"] - 1]
        words = [w for w in page.findall("{*}word")
                 if float(w.get("yMax")) - float(w.get("yMin")) <= 15]
        text = "".join(w.text or "" for w in sorted(words, key=lambda w: (round(float(w.get('yMin')), 1), float(w.get('xMin')))))
        if re.sub(r"\s+", "", assertion["quote"]) not in re.sub(r"\s+", "", text):
            raise ValueError("reviewed source quote not found: " + assertion["quote"])
    return {"plan_key": spec["plan_key"], "program_name": spec["program_name"],
            "cohort": spec["cohort"], "pdf_sha256": digest,
            "minimum_graduation_credits_raw": spec["minimum_graduation_credits_raw"],
            "catalog_pages": spec["catalog_pages"], "courses": rows, "modules": modules,
            "unresolved_requirements": spec["unresolved_requirements"],
            "source_assertions": spec["source_assertions"],
            "source_status": "reviewed_local_2026_pdf_not_current_official_verification",
            "prerequisite_status": "not_verified_no_prerequisites_invented"}


def build_student(plan, year):
    if type(year) is not int or year not in (1, 2, 3):
        raise ValueError("supported study stages: 1, 2, 3")
    student_id = f"SYN-{plan['plan_key']}-Y{year}"
    records, next_term, future, unselected = [], [], [], []
    for row in plan["courses"]:
        course = json.loads(json.dumps(row))
        if not row["selected"]:
            unselected.append(course)
            continue
        chosen = row["suggested_term_choices"][0]
        assigned_year = chosen["study_year"]
        end = SEASONS.index(chosen["seasons"][-1])
        course["assigned_term"] = chosen
        course["term_choice_kind"] = "synthetic_first_source_alternative"
        if assigned_year < year or (assigned_year == year and end <= 1):
            # Stable across stages; generated marks are never attributed to PDFs.
            mark = 65 + int(sha((plan['plan_key'] + row['course_code']).encode())[:8], 16) % 31
            academic_start = plan["cohort"] + assigned_year - 1
            course.update(attempt_id=f"{student_id}-{row['course_code']}-1", status="simulated_passed",
                          grade=str(mark), grade_kind="synthetic_generated_65_to_95",
                          grade_points=None, recognition_status="not_officially_recognized",
                          academic_year=f"{academic_start}-{academic_start+1}")
            records.append(course)
        elif assigned_year == year and set(chosen["seasons"]) <= set("春夏"):
            course.update(status="planned_not_enrolled", grade=None)
            next_term.append(course)
        else:
            course.update(status="future_not_enrolled", grade=None)
            future.append(course)
    progress = []
    for module in plan["modules"]:
        done = [r for r in records if r["module"] == module["name"]]
        credits = number(total(done))
        progress.append({**module, "simulated_completed_nominal_credits": str(credits),
                         "remaining_named_module_minimum": str(max(Decimal(0), number(module["minimum_credits"]) - credits)),
                         "scope": "named_module_only_not_graduation_audit"})
    result = {"schema_version": "curriculum-scenario-v1", "data_kind": "synthetic_student_with_source_bound_curriculum",
              "student_id": student_id, "display_name": f"模拟{plan['program_name']}·大{'一二三'[year-1]}",
              "plan_key": plan["plan_key"], "cohort": plan["cohort"], "study_year": year,
              "checkpoint": "该学年秋冬结束，春夏选课前（模拟时间，不是当前真实年级）",
              "profile_binding": "explicit_fixture_binding_not_school_authentication",
              "pdf_sha256": plan["pdf_sha256"], "academic_records": records,
              "planned_next_term": next_term, "future_courses": future, "unselected_courses": unselected,
              "named_module_progress": progress,
              "summary": {"completed_records": len(records), "completed_nominal_credits": total(records),
                          "next_term_courses": len(next_term), "next_term_nominal_credits": total(next_term)},
              "unresolved_requirements": plan["unresolved_requirements"],
              "prerequisite_status": plan["prerequisite_status"],
              "offerings": [], "timetable": None, "exams": None,
              "graduation_eligible": None, "overall_remaining_credits": None,
              "test_questions": ["概括这个模拟学生已完成和下阶段计划的课程，不能把计划当已修。",
                                 "依据对应培养方案，春夏有哪些课程值得优先核对？哪些条件还不能确认？",
                                 "哪些模块还缺学分？请把已命名模块差额与尚未落实的通识、认定要求分开。"],
              "expected_guards": ["不跨方案借用课程号或学分", "不把未来课程计入已完成", "不把模拟成绩称为学校实测",
                                  "不以名义学分认定毕业", "未提供班次、余量、节次，不生成真实可选课表", "未核实先修与认定，不保证可选资格"]}
    validate_student(result, plan)
    return result


def validate_student(student, plan):
    if student["plan_key"] != plan["plan_key"] or student["pdf_sha256"] != plan["pdf_sha256"] or student["cohort"] != plan["cohort"]:
        raise ValueError("cross-plan binding")
    source = {r["course_code"]: r for r in plan["courses"]}
    seen = Counter()
    for bucket in ("academic_records", "planned_next_term", "future_courses", "unselected_courses"):
        for row in student[bucket]:
            seen[row["course_code"]] += 1
            original = source[row["course_code"]]
            for field in ("course_name", "credits", "suggested_term_raw", "source", "module", "selected"):
                if row[field] != original[field]:
                    raise ValueError("source-bound course changed: " + field)
            if bucket != "unselected_courses":
                term = row["assigned_term"]
                if not row["selected"] or term not in original["suggested_term_choices"]:
                    raise ValueError("invalid selected course or assigned term")
                done = term["study_year"] < student["study_year"] or (
                    term["study_year"] == student["study_year"] and SEASONS.index(term["seasons"][-1]) <= 1)
                next_term = term["study_year"] == student["study_year"] and set(term["seasons"]) <= set("春夏")
                expected = "academic_records" if done else "planned_next_term" if next_term else "future_courses"
                if bucket != expected:
                    raise ValueError("course assigned to incorrect stage")
            elif row["selected"]:
                raise ValueError("selected course placed in unselected pool")
            if bucket == "academic_records":
                term = row["assigned_term"]
                if term not in original["suggested_term_choices"] or term["study_year"] > student["study_year"] or (
                    term["study_year"] == student["study_year"] and SEASONS.index(term["seasons"][-1]) > 1):
                    raise ValueError("future course marked completed")
                if row["status"] != "simulated_passed" or not 65 <= int(row["grade"]) <= 95:
                    raise ValueError("invalid synthetic outcome")
                if not row["selected"]:
                    raise ValueError("unselected course marked completed")
            elif row.get("grade") is not None:
                raise ValueError("uncompleted course has grade")
    if set(seen) != set(source) or any(n != 1 for n in seen.values()):
        raise ValueError("catalog rows lost or duplicated")
    if total(student["academic_records"]) != student["summary"]["completed_nominal_credits"]:
        raise ValueError("incorrect credit total")
    if student["summary"]["completed_records"] != len(student["academic_records"]) or (
        student["summary"]["next_term_courses"] != len(student["planned_next_term"])) or (
        student["summary"]["next_term_nominal_credits"] != total(student["planned_next_term"])):
        raise ValueError("incorrect stage summary")
    if student["offerings"] or student["timetable"] is not None or student["exams"] is not None:
        raise ValueError("unsupported offering or timetable assertion")
    if student["graduation_eligible"] is not None or student["overall_remaining_credits"] is not None:
        raise ValueError("unsupported graduation assertion")
    if student["unresolved_requirements"] != plan["unresolved_requirements"] or student["prerequisite_status"] != plan["prerequisite_status"]:
        raise ValueError("unresolved requirements must be preserved")


def review_html(bundle):
    payload = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>培养方案模拟学生核对</title><style>body{font:16px/1.65 system-ui;margin:32px auto;padding:0 20px;max-width:1200px;color:#24314a;background:#f6f8fc}h1{font-size:28px}section{background:white;padding:22px;margin:20px 0;border:1px solid #dde3ee;border-radius:12px}select{font:inherit;max-width:100%;padding:10px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px;text-align:left;border-bottom:1px solid #e5e9f1}th{background:#f0f4fc}.scroll{overflow:auto}.muted{color:#596780}summary{cursor:pointer;font-weight:600}code{font-size:13px}li{margin:5px 0}</style>
<h1>培养方案模拟学生核对</h1><p>仅供测试 · 2026级方案 · 各学年秋冬结束，春夏选课前</p><p class="muted">课程号、名称、学分和建议学期来自对应培养方案。学生、成绩、修读进度和选修取舍均为合成；不是学校记录或毕业审核。保留未落实要求，不编造教学班、考试和先修资格。</p>
<label for="student">选择模拟学生</label> <select id="student"></select><main id="view"></main>
<script id="data" type="application/json">""" + payload + """</script><script>
const data=JSON.parse(document.querySelector('#data').textContent),select=document.querySelector('#student');
const esc=v=>String(v??'未确认').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
for(const s of data.students){const o=document.createElement('option');o.value=s.student_id;o.textContent=s.display_name;select.append(o)}
function table(rows){return '<div class="scroll"><table><thead><tr><th>课程</th><th>模块</th><th>学分</th><th>建议学期</th><th>模拟成绩</th><th>原PDF页</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+esc(r.course_name)+'<br><code>'+esc(r.course_code)+'</code></td><td>'+esc(r.module)+'</td><td>'+esc(r.credits)+'</td><td>'+esc(r.suggested_term_raw)+'</td><td>'+esc(r.grade??'未生成')+'</td><td>'+esc(r.source.physical_page)+'</td></tr>').join('')+'</tbody></table></div>'}
function render(){const s=data.students.find(s=>s.student_id===select.value),p=data.plans.find(p=>p.plan_key===s.plan_key);document.querySelector('#view').innerHTML='<section><h2>'+esc(s.display_name)+'</h2><p>'+esc(s.checkpoint)+'</p><p>已完成 '+s.summary.completed_records+' 条 · 名义学分 '+s.summary.completed_nominal_credits+'；春夏计划 '+s.summary.next_term_courses+' 门 · '+s.summary.next_term_nominal_credits+' 学分。</p><p class="muted">仅统计本样本中明确选择的课程；不是完整学生画像或学校认定学分。</p><p><a download href="'+esc(s.student_id)+'.json">下载当前学生 JSON</a> · <a download href="scenarios.json">下载全部数据</a></p><details><summary>培养方案来源</summary><p>2026级 '+esc(p.program_name)+' · 原最低毕业学分 '+esc(p.minimum_graduation_credits_raw)+'</p><code>PDF SHA256: '+esc(p.pdf_sha256)+'</code></details></section><section><h2>春夏待核对课程（未选课、未完成）</h2>'+table(s.planned_next_term)+'</section><section><details><summary>模拟已完成记录</summary>'+table(s.academic_records)+'</details></section><section><details><summary>后续学年及短学期课程</summary>'+table(s.future_courses)+'</details></section><section><h2>仍需落实的要求</h2><ul>'+s.unresolved_requirements.map(r=>'<li>'+esc(r.name)+'：'+esc(r.reason)+'（原PDF第'+r.source_page+'页）</li>').join('')+'</ul><p>先修要求：未核实，不依据课名或建议顺序自行创造先修规则。毕业资格、完整剩余学分和真实开课均未判定。</p></section><section><h2>可用于对话测试</h2><ol>'+s.test_questions.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ol></section>'}
select.addEventListener('change',render);render();</script></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / "local-only"):
        parser.error("outputs must be isolated under project local-only/")
    if out.exists():
        parser.error("output exists; use a new version directory")
    spec = json.loads(args.review.read_text())
    plans = [reviewed_plan(p, args.review.resolve().parent) for p in spec["plans"]]
    students = [build_student(p, year) for p in plans for year in (1, 2, 3)]
    bundle = {"schema_version": "curriculum-scenarios-v1", "kind": "synthetic_offline_only",
              "review_sha256": sha(args.review.read_bytes()), "plans": plans, "students": students}
    out.mkdir(parents=True, mode=0o700)
    outputs = {"scenarios.json": json.dumps(bundle, ensure_ascii=False, indent=2), "index.html": review_html(bundle)}
    for student in students:
        outputs[student["student_id"] + ".json"] = json.dumps(student, ensure_ascii=False, indent=2)
    for name, content in outputs.items():
        path = out / name
        path.write_text(content + "\n")
        path.chmod(0o600)
    manifest = {"files": {name: sha((out/name).read_bytes()) for name in outputs},
                "programs": len(plans), "students": len(students),
                "source_course_rows": sum(len(p["courses"]) for p in plans),
                "school_or_model_calls": 0, "formal_applicability_verified": False}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (out / "manifest.json").chmod(0o600)
    print(json.dumps({k:v for k,v in manifest.items() if k != "files"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
