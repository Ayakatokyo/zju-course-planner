"""Prepare one synthetic LLM input; no network, database or application session."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if sys.version_info < (3, 10):
    raise SystemExit("本项目需要 Python 3.10+；本机可使用 /opt/homebrew/bin/python3.13")
sys.path.insert(0, str(ROOT))

from course_assistant.planner import revise_constraints, validate_plan


INSTRUCTIONS = (
    "你在解释一项独立合成课表实验。只依据输入中的实际工具结果，"
    "用中文说明：1.当前方案状态；2.周五条件与该班安排的关系；3.尚需什么检查或确认。"
    "引用教学班标识和已给出的时段。不补造班次、政策、成绩、考试或可行替代班。"
    "建议修改不表示已修改；替代班须重新调用计算工具。"
    "工具结果是当前合成范围的判断，不代表学校全局无解、正式中选或真实学生资格。"
    "只解释周五维度；不能将少量投影说成你已独立检查整份课表。"
)


def prepare(friday):
    bundle = json.loads((ROOT / "demo" / "bundle.json").read_text(encoding="utf-8"))
    if bundle["metadata"].get("data_kind") != "synthetic" or bundle["metadata"].get("not_official") is not True:
        raise ValueError("本实验只读取项目固定的独立合成夹具")
    if any(bundle[key].get("data_kind") != "synthetic" for key in ("student", "offering_snapshot")):
        raise ValueError("学生与开课快照必须属于固定合成夹具")
    constraints = revise_constraints(bundle["constraints"], {
        "friday_free": friday, "expected_revision": bundle["constraints"]["revision"],
    })
    section_ids = ["DEMO-PE-A", "DEMO-ML-B", "DEMO-SIM-A"]
    selected = [section for section in bundle["offering_snapshot"]["records"]
                if section["section_id"] in section_ids]
    if len(selected) != len(section_ids) or any(section.get("data_kind") != "synthetic" for section in selected):
        raise ValueError("三个实验教学班必须存在且标为合成数据")
    result = validate_plan(section_ids, bundle["offering_snapshot"], bundle["student"],
                           constraints, bundle["academic_snapshot"]["records"])
    focus = next(section for section in bundle["offering_snapshot"]["records"]
                 if section["section_id"] == "DEMO-ML-B")
    # An explicit small projection, never the bundle, profile, grades or source excerpts.
    meeting_fields = ("day_of_week", "period_indices", "season_codes", "week_numbers", "academic_calendar_id")
    check_fields = ("code", "result", "reason", "section_ids")
    observation = {
        "data_kind": "synthetic", "not_official": True, "scope": "one_friday_preference_explanation",
        "question": "请解释这组合成课表的校验状态，重点说明周五条件。",
        "friday_free": constraints["friday_free"], "selected_section_ids": section_ids,
        "locked_section_ids": constraints["locked_section_ids"],
        "preloaded_section_ids": constraints["preloaded_section_ids"],
        "focus_section": {"section_id": focus["section_id"], "friday_meetings": [
            {key: meeting.get(key) for key in meeting_fields}
            for meeting in focus["meetings"] if meeting.get("day_of_week") == 5
        ]},
        "tool_result": {
            "result": result["result"], "status": result["status"],
            "non_pass_checks": [{key: check[key] for key in check_fields}
                                for check in result["checks"] if check["result"] != "pass"],
            "preference_scores": result["preference_scores"],
        },
        "versions": {"engine": result["engine_version"],
                     "offering_snapshot": bundle["offering_snapshot"]["version"],
                     "constraints_revision": constraints["revision"]},
    }
    return {"instructions": INSTRUCTIONS, "input": observation}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--friday", choices=("hard", "soft"), default="hard")
    args = parser.parse_args()
    print(json.dumps(prepare(args.friday), ensure_ascii=False, indent=2, allow_nan=False))
