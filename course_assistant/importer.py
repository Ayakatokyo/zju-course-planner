"""Read-only source ingestion and explicit fixture validation.

No expressions from a PDF, import, or model are evaluated as Python code.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "inputs" / "zju_course_agent_handoff_v1"


class ImportErrorDetail(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ImportErrorDetail(message)


def unique(rows, key, label):
    require(isinstance(rows, list), f"{label} 必须是数组")
    ids = [r.get(key) for r in rows if isinstance(r, dict)]
    require(len(ids) == len(rows) and all(isinstance(i, str) and i for i in ids),
            f"{label} 缺少有效 {key}")
    require(len(ids) == len(set(ids)), f"{label} {key} 重复")
    return set(ids)


def references(node):
    if isinstance(node, dict):
        if "file_name" in node and "sha256" in node and "pdf_pages" in node:
            yield node
        for value in node.values():
            yield from references(value)
    elif isinstance(node, list):
        for value in node:
            yield from references(value)


def read_sources(root=SOURCE_ROOT):
    root = Path(root)
    manifest = load_json(root / "MANIFEST.json")
    errors = []
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append(f"缺失或非法路径：{item['path']}")
            continue
        data = path.read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            errors.append(f"原资料字节/哈希不一致：{item['path']}")
    require(not errors, "；".join(errors))
    with ZipFile(root / "artifacts" / "选课助手数据样本_v1.zip") as archive:
        for name in archive.namelist():
            if not name.endswith("/"):
                path = root / "data" / Path(name).name
                require(path.is_file() and path.read_bytes() == archive.read(name),
                        f"展开文件与原 ZIP 不一致：{name}")
    names = {"plans": "plan_registry", "courses": "course_samples",
             "candidates": "requirement_candidates", "issues": "source_issues"}
    original = {key: load_json(root / "data" / f"{name}.json") for key, name in names.items()}
    output = {key: value["records"] for key, value in original.items()}
    keys = {"plans": "plan_id", "courses": "sample_id", "candidates": "rule_id", "issues": "issue_id"}
    expected = {"plans": 3, "courses": 19, "candidates": 23, "issues": 12}
    ids = {}
    for kind, rows in output.items():
        ids[kind] = unique(rows, keys[kind], kind)
        require(original[kind]["record_count"] == len(rows) == expected[kind], f"{kind} 数量不一致")
    unique(output["courses"], "course_id", "course identities")
    aliases = {r["original_file_name"]: r for r in manifest["source_aliases"]}
    for value in original.values():
        for ref in references(value):
            source = aliases.get(ref["file_name"])
            require(source and ref["sha256"] == source["sha256"], "来源文件映射或 SHA256 无效")
            require(isinstance(ref["pdf_pages"], list) and all(type(n) is int and
                    1 <= n <= source["pdf_page_count"] for n in ref["pdf_pages"]), "PDF 物理页码越界")
    for kind in ("courses", "candidates"):
        for row in output[kind]:
            require(row["plan_id"] in ids["plans"], f"{kind} 引用未知方案")
    for issue in output["issues"]:
        require(set(issue["plan_ids"]).issubset(ids["plans"]), "来源问题引用未知方案")
    for rule in output["candidates"]:
        require(rule["status"] == "candidate_pending_human_review" and
                rule["human_review_required"] is True and rule["enforce_automatically"] is False,
                "原候选规则状态发生变化，拒绝导入")
    for course in output["courses"]:
        require(isinstance(course["course_code"], str) and course["course_name"], "课程身份无效")
        number(course["credits"], "课程学分", nullable=True)
        require(all(course.get(key) is None for key in ("section_id", "offering_term", "meetings")),
                "培养方案课程不能作为开课事实")
    for plan in output["plans"]:
        require(sum(r["credits"] for r in plan["base_modules"]) == plan["base_credit_total_from_headings"],
                "主模块学分汇总错误")
        require(sum(r["credits"] for r in plan["extra_classrooms"]) == 8, "二三四课堂结构错误")
    dictionary = load_json(root / "data" / "import_data_dictionary.json")
    unique(dictionary["entities"], "entity_name", "导入字段字典")
    require(len(dictionary["entities"]) == dictionary["entity_count"] == 11, "导入实体计数错误")
    template = load_json(root / "data" / "offering_snapshot_template.json")
    require(template["records"] == [] and template["available_count"] == 0 and
            template["status"] == "awaiting_import", "真实教学班模板必须保持空")
    output.update({"metadata": {key: v["metadata"] for key, v in original.items()},
                   "counts": {**expected, "actual_offerings": 0, "import_entities": 11},
                   "empty_offering_snapshot": template, "dictionary": dictionary,
                   "source_aliases": manifest["source_aliases"],
                   "integrity": {"result": "pass", "files_checked": len(manifest["files"]),
                                 "expanded_zip_identical": True, "source_refs_valid": True,
                                 "manifest_sha256": hashlib.sha256((root / "MANIFEST.json").read_bytes()).hexdigest(),
                                 "note": "只验证交接资料完整性，不代表官方规则审核或学生适用性。"}})
    return output


def number(value, label, nullable=False):
    if value is None and nullable:
        return
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f"{label} 必须为非负有限数，未知请用 null")


def validate_meeting(meeting):
    require(isinstance(meeting, dict), "上课时段必须是对象")
    for key, high in (("day_of_week", 7),):
        value = meeting.get(key)
        require(value is None or type(value) is int and 1 <= value <= high, "星期必须为 1—7 或 null")
    for key in ("week_numbers", "period_indices"):
        values = meeting.get(key)
        require(values is None or isinstance(values, list) and
                all(type(x) is int and 1 <= x <= 60 for x in values) and len(values) == len(set(values)),
                f"{key} 必须为明确的正整数集合或 null")
    seasons = meeting.get("season_codes")
    require(seasons is None or isinstance(seasons, list) and all(isinstance(s, str) for s in seasons),
            "分学期需为字符串数组或 null")


def validate_demo(bundle, source=None):
    """First release imports only independent, explicitly synthetic bundles."""
    bundle = copy.deepcopy(bundle)
    require(isinstance(bundle, dict) and bundle.get("metadata", {}).get("data_kind") == "synthetic",
            "首版自定义规划数据必须显式标记 metadata.data_kind=synthetic")
    require(bundle["metadata"].get("version"), "数据包缺少版本")
    student = bundle.get("student", {})
    require(student.get("data_kind") == "synthetic" and student.get("student_key"), "学生须为匿名 synthetic 夹具")
    plans = {r["plan_id"] for r in (source or read_sources())["plans"]}
    require(student.get("plan_id") is None or student["plan_id"] in plans, "学生引用未知方案")
    courses = bundle.get("courses", [])
    course_ids = unique(courses, "course_id", "演示课程")
    for course in courses:
        number(course.get("credits"), "课程学分", nullable=True)
        require(course.get("course_name") and course.get("plan_id") in plans, "课程名称或方案引用缺失")
    rules = bundle.get("rules", [])
    unique(rules, "rule_id", "演示规则")
    for rule in rules:
        require(rule["rule_id"].startswith("DEMO-") and rule.get("data_kind") == "synthetic" and
                rule.get("status") == "demo_active" and rule.get("version"), "演示规则必须带 DEMO 编号和 synthetic 版本")
        require(rule.get("plan_id") in plans, "规则引用未知方案")
        require(rule.get("metric") in ("credits", "course_count", "hours", "points"), "不支持的计算单位")
        number(rule.get("threshold"), "规则阈值", nullable=True)
        require(set(rule.get("course_pool") or []).issubset(course_ids), "规则引用未知课程")
    academic = bundle.get("academic_snapshot", {})
    require(academic.get("data_kind") == "synthetic" and academic.get("snapshot_id"), "学业快照缺少 synthetic 标记或 ID")
    require(isinstance(academic.get("records"), list), "学业记录必须为数组")
    # Identical duplicates are allowed, but their treatment is visible in audit.
    for row in academic["records"]:
        require(row.get("record_id") and row.get("student_key") == student["student_key"] and
                row.get("snapshot_id") == academic["snapshot_id"] and row.get("data_kind") == "synthetic", "学业记录引用或 synthetic 标记无效")
        require(row.get("course_id") is None or row["course_id"] in course_ids, "学业记录引用未知课程")
        require(row.get("completion_status") in ("passed", "failed", "in_progress", "not_taken", "unknown"), "未知修读状态")
        require(row.get("recognition_status") in ("not_applicable", "pending", "recognized", "rejected", "unknown"), "未知认定状态")
        for key in ("earned_credits", "recognized_hours", "classroom_points"):
            number(row.get(key), key, nullable=True)
    snapshot = bundle.get("offering_snapshot", {})
    require(snapshot.get("data_kind") == "synthetic" and snapshot.get("snapshot_id") and
            snapshot.get("version"), "开课快照缺少 synthetic 标记或版本")
    sections = snapshot.get("records", [])
    section_ids = unique(sections, "section_id", "教学班")
    require(snapshot.get("available_count") == len(sections), "快照记录数不一致（该值不是余量）")
    require(snapshot.get("completeness") in ("complete_within_scope", "partial", "unknown", "awaiting_import"), "快照范围状态无效")
    for section in sections:
        require(section.get("data_kind") == "synthetic" and section.get("course_id") in course_ids,
                "模拟教学班须显式 synthetic 且引用已导入课程")
        require(section.get("snapshot_id") == snapshot["snapshot_id"], "教学班快照引用错误")
        number(section.get("credits"), "教学班学分", nullable=True)
        for key in ("capacity", "available_seats"):
            number(section.get(key), key, nullable=True)
        require(section.get("meetings") is None or isinstance(section["meetings"], list), "meetings 必须为数组或 null")
        for meeting in section.get("meetings") or []:
            validate_meeting(meeting)
    constraints = bundle.get("constraints", {})
    for key in ("locked_section_ids", "preloaded_section_ids", "excluded_section_ids"):
        require(isinstance(constraints.get(key, []), list) and set(constraints.get(key, [])).issubset(section_ids), f"{key} 引用未知教学班")
    require(set(constraints.get("requested_course_ids", [])).issubset(course_ids), "规划请求引用未知课程")
    require(constraints.get("friday_free", "off") in ("off", "soft", "hard"), "周五条件无效")
    for meeting in constraints.get("fixed_meetings", []):
        validate_meeting(meeting)
    for row in bundle.get("wish_items", []):
        require(row.get("data_kind") == "synthetic" and row.get("section_id") in section_ids, "志愿引用或标记无效")
    for group in bundle.get("wish_groups", []):
        require(group.get("data_kind") == "synthetic", "志愿组须标明 synthetic")
    require(snapshot.get("wish_items", []) == bundle.get("wish_items", []), "开课与志愿快照不一致")
    return bundle
