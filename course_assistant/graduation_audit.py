"""Read-only graduation self-audit tree. School findings remain advisory."""
from html import unescape
import re

RAW_FIELDS = frozenset({"JDDM", "FJDDM", "JDMC", "KCDM", "KCXF", "CJ", "SFTG", "KCBZ", "TDKC", "XBX", "ZYFX", "YQXF", "YXXF", "YQMS", "YXMS", "ROOT", "JDJB", "GJDSFTG", "SFRDLB", "RDXKCXF"})
FIELDS = {"node_id": "JDDM", "parent_node_id": "FJDDM", "course_name_raw": "JDMC", "course_code_raw": "KCDM", "nominal_credits": "KCXF", "grade_raw": "CJ", "completion_status_raw": "SFTG", "study_note_raw": "KCBZ", "substitution_raw": "TDKC", "requirement_type_raw": "XBX", "specialization_raw": "ZYFX", "required_credits_raw": "YQXF", "earned_credits_raw": "YXXF", "required_courses_raw": "YQMS", "completed_courses_raw": "YXMS", "module_name_raw": "ROOT", "root_status_raw": "GJDSFTG", "recognition_only_raw": "SFRDLB", "nonduplicated_recognition_credits_raw": "RDXKCXF"}


def project_rows(payload):
    # The official tree endpoint returns a single array, not a paginated grid.
    if not isinstance(payload, list) or not payload or len(payload) > 2000:
        raise ValueError("invalid_audit_tree")
    ids = set()
    result = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("invalid_audit_row")
        row = {k: v for k, v in raw.items() if k in RAW_FIELDS}
        node = row.get("JDDM")
        if not isinstance(node, str) or not node or node in ids or not isinstance(row.get("JDMC"), str):
            raise ValueError("invalid_audit_node")
        ids.add(node)
        if node.startswith("head_"):
            continue
        for key, value in row.items():
            if value is not None and (type(value) not in (str, int, float) or len(str(value)) > 2000):
                raise ValueError("invalid_audit_value")
            if isinstance(value, str):
                row[key] = unescape(re.sub(r"<[^>]*>", " ", value)).strip()
        result.append(row)
    nodes = {r["JDDM"]: r for r in result}
    for row in result:
        parent = row.get("FJDDM")
        seen = {row["JDDM"]}
        while parent:
            if parent not in nodes or parent in seen:
                raise ValueError("invalid_audit_parent")
            seen.add(parent)
            parent = nodes[parent].get("FJDDM")
    if not any(not r.get("KCDM") for r in result):
        raise ValueError("audit_modules_missing")
    return {"items": result, "total": len(result), "currentPage": 1}


def parse_row(row, index):
    result = {key: str(row[raw]).strip() if row.get(raw) is not None else None for key, raw in FIELDS.items()}
    result["node_kind"] = "course" if result["course_code_raw"] else "module"
    status, note = result["completion_status_raw"], result["study_note_raw"]
    result["completion_status"] = {"通过": "passed", "不通过": "failed", "被替代": "replaced"}.get(status, "in_progress" if note == "在修" else "unknown")
    # Missing status is not evidence that a course is compulsory or uncompleted.
    result["field_states"] = {k: "known" if v not in (None, "") else "unknown" for k, v in result.items()}
    result["issues"] = []
    return result


def summary(records):
    modules = [{k: row[k] for k in ("node_id", "parent_node_id", "course_name_raw", "completion_status_raw", "required_credits_raw", "earned_credits_raw", "required_courses_raw", "completed_courses_raw", "nonduplicated_recognition_credits_raw") if row.get(k) not in (None, "")} for row in records if row.get("node_kind") == "module"]
    return {"modules": modules, "course_record_count": sum(row.get("node_kind") == "course" for row in records),
        "note": "学校毕业资格自审结果仅供参考；空状态不是必修欠课；已达标模块仍可能列出未修备选课；被替代不算待修；复合学分原值不相加，各层级学分不重复累计。"}
