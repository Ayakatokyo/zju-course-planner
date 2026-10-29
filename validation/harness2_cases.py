"""Frozen synthetic sources for Harness²; no school connection or personal files.

The development prompts are visible to the improver. Check functions and held-out
prompts are never copied to its context. Passing checks is not semantic acceptance.
"""
from copy import deepcopy

from course_assistant.graduation_audit import summary as audit_summary
from course_assistant.personal_observations import matches_grade_filter, source_grade_summary

TERM = "2026-2027-AW"
MODULE = "实践教学环节"


def row(**values):
    return {**values, "field_states": {k: "unknown" if v is None else "known" for k, v in values.items()}}


GRADES = [row(course_name_raw=name, course_code_raw=code, grade_raw=grade, nominal_credits=credits)
    for name, code, grade, credits in [
        ("数据分析", "SYN101", "92", "3"), ("数据分析", "SYN102", None, "2"),
        ("管理基础", "SYN103", "88", "2"), ("项目实践", "SYN104", "优秀", "1"),
        ("统计方法", "SYN105", "95", "4")]]
AUDIT = [
    row(node_id="module-practice", node_kind="module", course_name_raw=MODULE,
        required_credits_raw="9", earned_credits_raw="6", completion_status_raw="不通过"),
    row(node_id="practice-1", parent_node_id="module-practice", node_kind="course",
        course_name_raw="项目实践", course_code_raw="SYN201", nominal_credits="2",
        completion_status_raw="通过", study_note_raw=None),
    row(node_id="practice-2", parent_node_id="module-practice", node_kind="course",
        course_name_raw="综合设计", course_code_raw="SYN202", nominal_credits="4",
        completion_status_raw=None, study_note_raw="在修")]
TIMETABLE = [row(course_name_raw="统计方法", course_code_raw="SYN105",
    time_raw="周一第1-2节", place_raw="合成教学楼101"),
    row(course_name_raw="项目实践", course_code_raw="SYN201", time_raw=None, place_raw=None)]
CURRICULUM = "实践教学环节：要求9学分。项目实践，SYN201，2学分，建议二短；综合设计，SYN202，4学分，建议四秋冬。此片段列出的两门课程不代表完整模块。"

# Ordinary tasks, not answer keys. Held-out prompts are kept out of source files.
DEVELOPMENT = [
    {"id": "D01", "question": f"列出我{TERM}的全部成绩，保留同名记录和未知值，并说明数字均分是不是GPA。"},
    {"id": "D02", "question": f"只看{TERM}里90分及以上的成绩，全部列出并统计名义学分。"},
    {"id": "D03", "question": "学校自审中实践教学环节的课程，哪些通过、哪些在修？模块已获和要求学分分别是多少？"},
    {"id": "D04", "question": "对照培养原文和学校自审，列出实践教学环节各门课程的原文学分、建议学期和当前状态。"},
    {"id": "D05", "question": f"核对我{TERM}的课表：周五能否确认没课？区分字段缺项和漏读记录。"},
    {"id": "D06", "question": "只解释含义，不查本人记录：要求学分减去已获学分，可以直接叫正式欠修学分吗？",
        "history": [{"role": "user", "content": "学校自审中实践教学环节要求多少、已获多少？"},
                    {"role": "assistant", "content": "学校自审中，实践教学环节要求9学分，已获6学分。"}]},
]
HELD_OUT = [
    {"id": "H01", "question": f"我{TERM}里叫数据分析的记录有几条？它们同名但代码不同，能说明我重修了吗？"},
    {"id": "H02", "question": "我最近一次考试是什么时候？先不要猜学期。"},
    {"id": "H03", "question": f"把{TERM}中成绩未知的记录全部列出来；未知是不是没通过？"},
    {"id": "H04", "question": "不查个人资料，只解释培养方案和学校自审各能说明什么，能否保证毕业。"},
]


class FrozenSchool:
    """Bounded source provider: mirrors query semantics, never performs network I/O."""
    def __init__(self):
        self.calls = []

    def personal_chat_tool(self, context, name, args):
        return self.execute(name, args)

    def execute(self, name, args):
        self.calls.append({"name": name, "arguments": deepcopy(args)})
        if name == "get_my_context":
            return {"status": "ok", "cohort": 2026, "major_name": "合成信息管理专业",
                "operations": [{"operation": op, "target_term": term} for op, term in
                    [("academic", TERM), ("major_academic", "ALL-HISTORY"),
                     ("graduation_audit", "ALL-HISTORY"), ("timetable", TERM)]],
                "api_operations": ["academic", "major_academic", "graduation_audit", "timetable", "exams"],
                "capabilities": {"curriculum_available": True, "personal_record_query": True,
                    "live_record_query": True, "read_only_acquisition": True}}
        if name == "lookup_academic_terms":
            return {"status": "ok"}  # The production projector supplies the public terms.
        if name == "query_my_records":
            op, term = args["operation"], args.get("target_term")
            expected = "ALL-HISTORY" if op in {"major_academic", "graduation_audit"} else TERM
            if term is None and op in {"major_academic", "graduation_audit"}:
                term = "ALL-HISTORY"  # Same default as SchoolAccountService's live adapter.
            if term != expected or op == "exams" or args.get("page_token"):
                raise ValueError("outside_frozen_source_scope")
            source = AUDIT if op == "graduation_audit" else TIMETABLE if op == "timetable" else GRADES
            query = args.get("query", "")
            indexed = [(i, r) for i, r in enumerate(source) if not query or query in
                str(r.get("course_name_raw", "")) or query in str(r.get("course_code_raw", "")) or
                (op == "graduation_audit" and query == MODULE)]
            if args.get("grade_filter"):
                indexed = [(i, r) for i, r in indexed if matches_grade_filter(r, args["grade_filter"])]
            matched = [r for _, r in indexed]
            coverage = {"captured_rows": len(source), "reported_total": None if op == "timetable" else len(source),
                "has_more": False, "complete_within_scope": op != "timetable"}
            result = {"status": "ok", "operation": op, "target_term": term, "data_kind": "synthetic",
                "data_source": "frozen_synthetic", "source_alias": "SOURCE-FROZEN", "observation_id": "OBS-FROZEN-" + op,
                "coverage": coverage, "mode": args.get("mode", "records"),
                "matched_records": len(matched), "total_records": len(source), "query": query}
            if args.get("grade_filter"):
                result["grade_filter"] = deepcopy(args["grade_filter"])
            shown = [] if args.get("mode") == "summary" else indexed[:args.get("limit", 15)]
            if args.get("mode") == "table":
                shown = indexed
            result.update(records=deepcopy([r for _, r in shown]), source_record_indices=[i for i, _ in shown],
                returned_records=len(shown), has_more=len(shown) < len(indexed) if shown else False)
            if op in {"academic", "major_academic"}:
                result["source_grade_summary"] = source_grade_summary(matched, coverage)
            if op == "graduation_audit":
                result["graduation_summary"] = audit_summary(matched)
            return result
        if name == "search_my_curriculum":
            return {"status": "pending_review", "data_kind": "synthetic", "relevance": "named_module",
                "scope": {"binding_status": "not_verified", "rules_status": "not_reviewed"},
                "matches": [{"reference": "PERSONAL-REF-" + "A" * 43, "page": 5,
                    "title": MODULE, "text": CURRICULUM,
                    "review_status": "unreviewed", "binding_status": "not_verified", "rules_status": "not_reviewed"}]}
        raise ValueError("tool_outside_frozen_scope")


def mechanical_checks(case, response, calls):
    """Necessary checks only; final prose is separately read against source facts."""
    checks = {"completed": response.get("status") == "completed",
        "requested_outcome": (response.get("model_decision") or {}).get("kind") == ("clarify" if case["id"] == "H02" else "answer")}
    queries = [c["arguments"] for c in calls if c["name"] == "query_my_records"]
    tables = [p["record_table"] for p in response.get("citations", []) if p.get("record_table")]
    case_id = case["id"]
    if case_id in {"D01", "D02", "H03"}:
        expected = {"D01": 5, "D02": 2, "H03": 1}[case_id]
        checks["table_rows"] = sum(map(len, tables)) == expected
    if case_id == "D02":
        checks["filter"] = any(q.get("grade_filter") == {"kind": "numeric", "op": "gte", "value": 90} for q in queries)
    if case_id == "D03":
        checks["audit_records"] = any(q["operation"] == "graduation_audit" and q.get("mode", "records") == "records" for q in queries)
        checks["audit_cited"] = any(p.get("operation") == "graduation_audit" and p.get("kind") == "record" for p in response.get("citations", []))
    if case_id == "D04":
        checks["both_sources"] = any(q["operation"] == "graduation_audit" for q in queries) and any(c["name"] == "search_my_curriculum" for c in calls)
        checks["both_sources_cited"] = any(p.get("operation") == "graduation_audit" for p in response.get("citations", [])) and any(p.get("kind") == "curriculum" for p in response.get("citations", []))
    if case_id == "D05":
        checks["current_timetable"] = any(q["operation"] == "timetable" and q.get("target_term") == TERM for q in queries)
        checks["timetable_cited"] = any(p.get("operation") == "timetable" for p in response.get("citations", []))
    if case_id in {"D06", "H02", "H04"}:
        checks["no_personal_reads"] = not queries and not any(c["name"] == "search_my_curriculum" for c in calls)
    if case_id == "D06":
        checks["no_old_numbers"] = not any(n in response.get("reply", "") for n in ("9", "6", "3", "九", "六", "三"))
    if case_id == "H02":
        checks["clarification"] = (response.get("model_decision") or {}).get("kind") == "clarify"
    return checks
