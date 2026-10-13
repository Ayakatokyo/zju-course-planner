#!/usr/bin/env python3
"""Offline, read-only curriculum requirement inventory; Python standard library only.

Source pages are existing pdftotext extracts. The inventory is review evidence,
not executable graduation rules, student applicability, or a PDF semantic audit.
Only second/third/fourth classroom requirements are deferred by the user.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata


VERSION = "plan-requirement-inventory/1.0"
DEFAULT_BATCH = "local-only/school_backend_full_20261013"
DEFAULT_OUTPUT = "local-only/course_dedup_20261013/requirements_v1"
PAGE_MARKER = re.compile(r"^=== SOURCE PAGE (\d+) ===\n", re.M)
WATERMARK = "浙江大学  Zhejiang University"
COURSE_ROW = re.compile(r"^\s*(?:[A-Z]{2,}\d{3,}[A-Z0-9]*|\d{7,}|\*{4,})")
MAIN_HEADING = re.compile(r"^\s*[一二三四五六七八九十]+、")
SUB_HEADING = re.compile(r"^\s*\([1-9]\)")
DEFER_HEADING = re.compile(r"^\s*[一二三四五六七八九十]+、第([二三四])课堂")
RECOGNITION_HEADING = re.compile(r"^\s*\(([1-4])\)(美育类|劳育类|创新创业类|心理健康类)")
GENERAL_END = re.compile(r"^\s*(?:专业核心课程|专业核心实践|全英文课程|推荐学制|授予学位|学科专业类别|培养说明)")
TABLE_START = re.compile(r"^\s*(?:课程号|类别.*(?:课程号|项目序号)|[一二三四五六七八九十]+学年|培养方案修读指导性计划|辅修培养方案)")


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("\u200b", "")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: object) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def category(name, units, relation, recommendation, kind="recognition_or_gate", scope="retained_for_review"):
    return {"name_raw": name, "units": units, "requirement_kind": kind,
            "relation_to_ordinary_courses": relation, "recommendation": recommendation,
            "scope_status": scope, "enforce_automatically": False}


CATEGORIES = {
    "second_classroom": category("第二课堂", ["学分", "记点"], "独立活动账户；普通课程修读不能自行代替活动认定。", "本轮暂缓推荐、核算和普通课程问答，保留账户、原文与出处，不减少官方毕业总要求。", "separate_credit_account", "deferred_by_user"),
    "third_classroom": category("第三课堂", ["学分", "记点", "周"], "社会实践等独立账户；与第二课堂分账，剩余记点替换还可能有附加条件。", "本轮暂缓；保留原文、适用方案和活动认定路径。", "separate_credit_account", "deferred_by_user"),
    "fourth_classroom": category("第四课堂", ["学分", "周", "门", "记点"], "国际化学习交流账户，可能与认可课程、第三课堂剩余记点联合完成；不能简化为必须出国。", "本轮暂缓；保留所有替代路径，不从总要求减分。", "separate_credit_account", "deferred_by_user"),
    "arts_recognition": category("美育类", ["学分"], "部分通识或专业课可覆盖认定条件；是否认可依官方清单及个人认定，不再重复加实际学分。", "建议纳入带引用问答；核算待适用清单和本人认定，不自动排除美育课程。"),
    "labor_recognition": category("劳育类／劳动教育类", ["学时", "学分"], "来源通常要求32学时；普通实践课不因名称含劳动就自动获得全部认定学时。", "建议纳入；学时与课程学分分开，等待官方清单/个人认定。标题0学分与正文要求并存时保留例外。"),
    "innovation_recognition": category("创新创业类", ["学分"], "可由认定课程覆盖，不因名字包含创新或创业就完成，也不额外叠加课程学分。", "建议纳入带引用问答，规则执行待清单与审核。"),
    "mental_health_recognition": category("心理健康类", ["学分"], "课程覆盖与条件认定分开；同一合格课程可能同时覆盖不同条件。", "建议纳入，保留国际学生例外；不自动增加基础毕业学分。"),
    "language_requirements": category("外语类", ["学分", "层次", "门"], "外语课程仍是普通课程，但要求由能力层次、起点、项目决定；存在不同学分梯度和固定要求。", "建议纳入方案问答；层次/起点未知则拒绝个人结论，不统一套L3或全校梯度。", "course_requirement_with_applicability"),
    "writing_course_gate": category("大学写作修读门数", ["门", "课程学分"], "部分方案在博雅技艺类要求中另指定大学写作课程；这是普通课程门数条件，不是独立课堂积分。", "建议纳入方案问答；保留该类课程和交叉覆盖关系，不另增学分。", "course_count_requirement"),
    "agricultural_reading_course_gate": category("耕读相关通识课程", ["门", "课程学分"], "部分涉农方案要求至少1门耕读相关通识课；不能因为劳动相近而暂缓该普通课程要求。", "建议纳入，具体认定清单待核对，不按名称直接认定。", "course_count_requirement"),
    "cross_college_course_gate": category("跨专业／其他学院课程门数", ["门", "学分"], "个性修读有其他学院专业基础或专业课程至少1门等条件；不是新增活动账户，也不等于用户已排除的跨类采集入口。", "建议纳入方案依据；学院/课程类别/是否在本方案内须有来源。", "course_count_requirement"),
    "course_recognition_overlap": category("实验技能训练学分同时认定劳动教育", ["学分", "学时"], "部分贯通项目明确特定实验技能训练课程学分可同时认定劳动教育；课程与认定覆盖保留两条关系，不重复增加实际学分。", "建议纳入来源限定的认定问答；不能推广到其他实验/实习，劳育学时换算未给仍未知。"),
    "ai_english_course_count_gate": category("人工智能／全英文课程最低门数", ["门", "课程学分"], "个别方案要求人工智能课和全英文课分别至少2门；不是第四课堂替代条件。", "建议保留为本方案普通课程条件，按官方课程标签确认，不自动排除。", "course_count_requirement"),
    "progression_path_course_gate": category("保研学生的指定进阶模块", ["路径", "课程学分"], "个别方案对本校保研学生指定进阶模块；个人升学路径尚不明时不能统一适用。", "建议纳入路径问答，个人路径未知保持unknown，核算待政策审核。", "course_requirement_with_applicability"),
    "graduate_course_transfer": category("本研衔接课程替换／免修", ["课程学分", "免修"], "贯通项目描述已修研究生学位课或选修课后续可做替换或免修；不能将两阶段学分机械重复加总。", "建议按阶段和项目保留为依据；待正式认定及对应研究生方案，不自动替换。", "stage_specific_recognition"),
    "swimming_exemption": category("游泳必修与考核免修", ["考核", "学年", "学分"], "游泳为普通体育课程；考核免修仍要求修读其他体育专项课程，不能免去总体育要求。", "建议纳入；免修依据与替代课分别记录，等待适用修读办法与本人考核事实。"),
    "sport_skill_certificate": category("浙江大学体育技能中级／高级证书", ["证书", "课程序列"], "完成水上运动、形体舞蹈或素质拓展ⅠⅡ／ⅠⅡⅢⅣ可获证；来源未说证书是额外毕业门槛。", "建议仅信息展示，不将可获得证书改成必须获证；体育课程保留。", "optional_certificate"),
    "international_student_exemption": category("国际学生思政／军体修读和免修", ["学分", "免修"], "按明确国际学生方案改变普通思政、军体课程要求；不推广到其他学生。", "建议纳入适用过滤与例外问答，保留原则上等限定，不猜本人身份。", "course_requirement_with_applicability"),
    "micro_minor": category("微辅修", ["学分", "证书", "门"], "有跨专业课程模块与证书；多份方案要求不足部分由指定课程补足，门槛不同，非统一额外账户。", "建议作为路径问答纳入；本人未选路径则不强加，不能将证书学分与基础课程重复累计。", "optional_study_path"),
    "minor_program_degree": category("辅修专业／辅修学位", ["学分", "证书", "论文"], "另一路径的课程/星号标记与论文要求；不与主修要求或微辅修合并。", "建议按路径单列，首版主修推荐不自动执行；保留所有正常课程。", "optional_study_path"),
    "research_result_credit_recognition": category("科研训练系列课程的成果学分申请", ["学分", "作者序位", "成果"], "论文成果按作者序位申请课程学分，未发表同等成果另有材料要求；属于课程认定，非二三四课堂账户。", "建议纳入专门认定依据问答，待学校认定；不因科研/实践词语排除课程。"),
    "undergraduate_transition_gate": category("本科后转段条件", ["学位", "成绩等级", "分", "学分", "次", "资格"], "医学/法学项目除本科课程完成还列论文等级、英语、交流或推免等转段要求；不等同所有本科生毕业门槛。", "建议按项目和本科转段阶段纳入依据问答，阈值缺失保持unknown，不自动执行。", "progression_gate"),
    "dual_degree_progression": category("双学位项目绩点与退出条件", ["绩点", "论文", "学分"], "商务双学位有交叉论文和统计相关专业课平均绩点要求；正文未给具体绩点阈值。", "建议纳入并明确阈值未知，禁止模型补数或自行判定退出。", "progression_gate"),
    "honors_certificate": category("竺可桢荣誉证书评估", ["评估", "证书"], "混合班/人文社科实验班来源表述为达到评估标准可申请；不能改写成全体学生强制毕业证书。", "建议仅说明申请及评估依据缺口；医学转段中提到荣誉证书的条件按该项目单列。", "optional_certificate"),
    "medical_comprehensive_assessment": category("医学综合课程Ⅰ／Ⅱ／Ⅲ及本科毕业考试", ["考核", "学分"], "综合课程/本科毕业考试可本身列课程号学分；普通课程记录和额外考核事实不能混为一条。", "建议按医学方案纳入；保留这些课程，考核状态另取可信本人来源。", "course_linked_assessment"),
    "professional_qualification_outcome": category("能够通过国家执业医师考试", ["能力目标"], "口腔方案在知识/能力毕业要求中出现；文字是培养能力目标，不能据此确认毕业前必须持有执照。", "建议保留为能力目标问答，待配套政策确认正式资格门槛。", "qualitative_outcome"),
    "graduate_stage_gates": category("研究生阶段毕业和授予学位标准", ["考核", "学位论文", "创新成果", "学分"], "部分本科贯通PDF附有博士/研究生阶段标准；不能并入本科第一课堂/本科学位审核。", "建议登记并加degree_stage=graduate过滤；本科首版不自动执行或排除其课表课程。", "stage_specific_gate"),
    "competency_outcomes": category("毕业知识、能力、素质及人格要求", ["非量化目标"], "正文包含教育目标和能力结果；多数没有个人可执行阈值，不能等同额外学分或合格证明。", "建议仅作依据问答，只有明确门槛及配套政策核验后才派生可执行规则。", "qualitative_outcome"),
    "ordinary_course_safety": category("含体测、劳动、实践、科研、实验、实习、毕业设计等词的课程表行", ["课程学分", "学时"], "具有课程号的正常课程/教学环节，不因关键词与独立认定或活动类似而删除。", "所有原行保留；本盘点只给防误排除证据，不创建排除清单。", "ordinary_course_preservation"),
}


def parse_pages(text: str) -> dict[int, str]:
    parts = PAGE_MARKER.split(text)
    if parts[0].strip() or len(parts) < 3:
        raise ValueError("Unexpected source-page extract format")
    numbers = [int(parts[i]) for i in range(1, len(parts), 2)]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("Missing, repeated, or unordered physical pages")
    pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}
    return pages


def flatten(pages):
    return [(page, line_no, line) for page, text in pages.items()
            for line_no, line in enumerate(text.splitlines(), 1)]


def body_end(lines, start, mode="paragraph"):
    """Stop by actual headings; table interleaving stays visibly evidenced."""
    for i in range(start + 1, len(lines)):
        line = norm(lines[i][2])
        if mode == "classroom" and (MAIN_HEADING.match(line) or re.match(r"^\s*(?:培养方案修读指导性计划|辅修培养方案|(?:硕士|博士|研究生)阶段培养方案)", line)):
            return i
        if mode == "recognition" and (RECOGNITION_HEADING.match(line) or MAIN_HEADING.match(line) or "培养方案修读指导性计划" in line):
            return i
        if mode == "course_requirement" and (SUB_HEADING.match(line) or MAIN_HEADING.match(line) or "通识选修课程" in line and re.match(r"\s*\d", line)):
            return i
        if mode == "competency" and GENERAL_END.match(line):
            return i
        if mode == "graduate_gate" and (TABLE_START.match(line) or "Zhejiang University" in line):
            return i
        if mode == "paragraph" and (not line.strip() or TABLE_START.match(line) or "Zhejiang University" in line or COURSE_ROW.match(line)):
            return i
    return len(lines)


def make_record(meta, lines, start, end, key, reason):
    chosen = lines[start:end]
    if not chosen:
        raise ValueError("Empty evidence span")
    spans = []
    for page in dict.fromkeys(p for p, _, _ in chosen):
        page_lines = [(n, t) for p, n, t in chosen if p == page]
        spans.append({"page": page, "line_start": page_lines[0][0], "line_end": page_lines[-1][0],
                      "raw_text": "\n".join(t for _, t in page_lines)})
    raw = "\n".join(t for _, _, t in chosen)
    prose = " ".join(norm(t).strip() for _, _, t in chosen
                     if t.strip() and "Zhejiang University" not in t and not COURSE_ROW.match(norm(t)) and not TABLE_START.match(norm(t)))
    location = f"{meta['plan_number']}:{meta['text_sha256']}:{key}:{chosen[0][0]}:{chosen[0][1]}:{chosen[-1][0]}:{chosen[-1][1]}"
    evid = "REQ-" + digest(location.encode())[:16]
    measurements = [{"raw_value": m.group(0), "value_text": m.group(1), "unit_raw": m.group(2),
                     "comparator": None, "interpretation": "mention_only_not_executable_threshold"}
                    for m in re.finditer(r"([+≥≤<>]?\s*\d+(?:\.\d+)?(?:\s*[-~～至]\s*\d+(?:\.\d+)?)?)\s*(学分|学时|记点|门|次|周|年)", prose)]
    return {"evidence_id": evid, "category": key, "plan_ref": meta["plan_ref"], "plan_number": meta["plan_number"],
            "cohort_from_directory": meta["cohort_from_directory"], "major_name_raw": meta["major_name_raw"],
            "pdf_sha256": meta["pdf_sha256"], "text_sha256": meta["text_sha256"], "source_spans": spans,
            "scope_status": CATEGORIES[key]["scope_status"], "enforce_automatically": False,
            "student_applicability_verified": False, "degree_stage": "graduate" if key == "graduate_stage_gates" else "source_context_pending_review",
            "detection_reason": reason, "measurements_mentions": measurements,
            "course_table_interleaving": any(COURSE_ROW.match(norm(t)) for _, _, t in chosen),
            "interpretation_flags": (["international_student_recognition_exception_with_generic_body"] if "原则上不作认定要求" in prose else [])
                + (["gpa_threshold_not_given"] if key == "dual_degree_progression" else [])
                + (["qualitative_not_confirmed_eligibility_gate"] if key in {"competency_outcomes", "professional_qualification_outcome"} else []),
            "raw_text_sha256": digest(raw.encode()), "review_status": "candidate_from_source_text_pending_policy_review"}


PARAGRAPH_PATTERNS = [
    ("writing_course_gate", r"《大学写作》", "方案明确写作课程修读条件，保留普通课程"),
    ("agricultural_reading_course_gate", r"耕读相关通识课程", "涉农方案的课程门数条件"),
    ("cross_college_course_gate", r"跨专业课程.*至少|至少修读\s*1\s*门由其他学院", "普通课程跨学院覆盖条件，不是跨类采集入口"),
    ("course_recognition_overlap", r"学分可同时认定为劳动教育课程学分", "明确课程到劳动教育的并行认定条文"),
    ("ai_english_course_count_gate", r"人工智能课程中至少修读|全英文课程中至少修读", "方案普通课程标签与门数"),
    ("progression_path_course_gate", r"本校保研学生必须修读", "升学路径限定的模块要求"),
    ("graduate_course_transfer", r"已修读的研究生.*(?:课程替换|免修)", "本研衔接课程认定限定阶段"),
    ("swimming_exemption", r"申请考核免修游", "游泳必修的考核免修及补修其他体育课程"),
    ("sport_skill_certificate", r"体育技能中级证书", "体育课程序列对应可获得证书"),
    ("international_student_exemption", r"国际学生.*(?:免修|至少修读)", "国际学生专门课程例外"),
    ("micro_minor", r"微辅修", "跨专业路径或微辅修课程/证书条款"),
    ("minor_program_degree", r"辅修(?:专业|学位)", "辅修路径单列"),
    ("research_result_credit_recognition", r"发表CCF|申请获得科研实践课程", "科研成果对应第一课堂课程学分申请"),
    ("undergraduate_transition_gate", r"为保证教学质量|专业硕士阶段的要求", "本科后转段专门条件"),
    ("dual_degree_progression", r"平均绩点", "双学位保留资格，阈值未给"),
    ("honors_certificate", r"达到评估标准.*申请获得", "荣誉证书可申请评估"),
    ("medical_comprehensive_assessment", r"(?:还需参加医学综合|医学综合考试|本科毕业考试)", "课程相连的医学综合/毕业考核"),
    ("professional_qualification_outcome", r"能够通过国家执业医师考试", "能力结果，非确认资格门槛"),
]
BROAD_PATTERN = re.compile(r"认定|免修|记点|合格|达标|考核|测试|绩点|证书|资格|至少|不少于|不低于|不得|必读|应.*修|要求.*修|必须|论文.*(?:良好|80分)|海外交流.*次")


def scan_plan(meta, pages):
    lines = flatten(pages)
    records, claimed = [], set()

    def add(start, end, key, reason):
        record = make_record(meta, lines, start, end, key, reason)
        if record["evidence_id"] not in {r["evidence_id"] for r in records}:
            records.append(record)
            claimed.update(range(start, end))

    for i, (_, _, raw) in enumerate(lines):
        text = norm(raw)
        classroom = DEFER_HEADING.match(text)
        recognition = RECOGNITION_HEADING.match(text)
        if classroom:
            key = {"二": "second_classroom", "三": "third_classroom", "四": "fourth_classroom"}[classroom.group(1)]
            add(i, body_end(lines, i, "classroom"), key, "独立主标题与对应活动条文；只标记业务范围暂缓")
        elif recognition:
            key = {"1": "arts_recognition", "2": "labor_recognition", "3": "innovation_recognition", "4": "mental_health_recognition"}[recognition.group(1)]
            add(i, body_end(lines, i, "recognition"), key, "其他必修环节中的认定子标题与正文")
        elif re.match(r"^\s*\([1-9]\)外语类", text):
            add(i, body_end(lines, i, "course_requirement"), "language_requirements", "外语模块原始标题与要求，不仅搜索分层关键词")
        elif re.match(r"^\s*毕业和授予学位标准", text):
            add(i, body_end(lines, i, "graduate_gate"), "graduate_stage_gates", "贯通PDF明确研究生阶段标准，保留阶段隔离")
        elif re.match(r"^\s*毕业要求\s*$", text):
            add(i, body_end(lines, i, "competency"), "competency_outcomes", "毕业要求段落；量化门槛另立记录，通用能力不强制执行")
        if COURSE_ROW.match(text) and re.search(r"体测|劳动|实践|科研|实验|实习|毕业|读书报告", text):
            add(i, i + 1, "ordinary_course_safety", "有课程号的原课程行，明确保留而非关键词排除")

    # Paragraph anchors may begin after a line wrap; include the preceding part of
    # the same paragraph but never a different heading, table, or source page.
    for i, (page, _, raw) in enumerate(lines):
        text = norm(raw)
        if COURSE_ROW.match(text):
            continue
        for key, pattern, reason in PARAGRAPH_PATTERNS:
            if re.search(pattern, text):
                start = i
                while start > 0 and lines[start - 1][0] == page:
                    prev = norm(lines[start - 1][2])
                    if not prev.strip() or COURSE_ROW.match(prev) or TABLE_START.match(prev) or MAIN_HEADING.match(prev) or SUB_HEADING.match(prev) or "Zhejiang University" in prev:
                        break
                    start -= 1
                add(start, body_end(lines, i), key, reason)

    # Diagrams and suggested activity tables repeat classroom names separately
    # from the main clauses. Preserve those mentions without claiming a new
    # threshold or applying the deferral to their whole page/mixed context.
    for i, (_, _, raw) in enumerate(lines):
        if i in claimed or COURSE_ROW.match(norm(raw)):
            continue
        for name, key in [("二", "second_classroom"), ("三", "third_classroom"), ("四", "fourth_classroom")]:
            if re.search(rf"(?:第)?{name}课堂", norm(raw)):
                add(i, i + 1, key, "图示/指导计划中的重复名称；仅此名称范围暂缓，不排除整个页或同段普通课程")

    candidates = []
    for i, (page, line_no, raw) in enumerate(lines):
        text = norm(raw)
        if BROAD_PATTERN.search(text) and i not in claimed and "Zhejiang University" not in raw:
            kind = "ordinary_course_row_preserved" if COURSE_ROW.match(text) else (
                "activity_table_mention_pending_context" if re.match(r"\s*(?:二|三|四)课堂", text) or "记点" in text else "unclassified_context_pending_review")
            candidates.append({"plan_ref": meta["plan_ref"], "page": page, "line": line_no, "raw_text": raw,
                               "context_raw": "\n".join(t for p, _, t in lines[max(0, i - 1):i + 2] if p == page),
                               "candidate_kind": kind, "scope_status": "retained_for_review", "enforce_automatically": False})
    return records, candidates


def source_plan(batch, candidate):
    number = candidate["plan_number"]
    pdf = batch / candidate["pdf"]["named_file"]
    obj = batch / candidate["pdf"]["object"]
    text_file = batch / "pdf_text" / f"{number}.txt"
    pdf_hash = digest(pdf.read_bytes())
    if pdf_hash != candidate["pdf"]["sha256"] or digest(obj.read_bytes()) != pdf_hash:
        raise ValueError(f"PDF source integrity mismatch: {number}")
    raw_text = text_file.read_text()
    pages = parse_pages(raw_text)
    directory_object = batch / "objects" / (candidate["source"]["sha256"] + ".json")
    directory_blob = directory_object.read_bytes()
    if digest(directory_blob) != candidate["source"]["sha256"]:
        raise ValueError("Directory response object integrity mismatch")
    rows = json.loads(directory_blob)["items"]
    matches = [(index, row) for index, row in enumerate(rows)
               if str(row.get("pyfaxx_id")) == str(candidate["source_plan_key"])
               and str(row.get("jxjhh")) == str(number)]
    if len(matches) != 1:
        raise ValueError("Directory row must bind by its own plan key and number")
    row_index, directory_row = matches[0]
    account_fields = [("second_classroom", "dektxf", "dektbz"), ("third_classroom", "dektxf3", "d3ktbz"), ("fourth_classroom", "dektxf4", "dsktbz")]
    accounts = []
    for key, field, clause in account_fields:
        value = directory_row.get(field)
        accounts.append({"category": key, "source_field": field, "raw_value": value,
                         "field_present": field in directory_row,
                         "field_state": "not_provided" if field not in directory_row else ("unknown" if value is None or value == "" else "known"),
                         "unit_raw": "学分", "unit_mapping_status": "field_label_and_pdf_heading_mapping",
                         "scope_status": "deferred_by_user", "enforce_automatically": False,
                         "clause_source_field": clause, "clause_raw": directory_row.get(clause),
                         "clause_field_present": clause in directory_row})
    titles = [{"page": page, "line": line_no, "raw_text": line,
               "cohort_mentions": re.findall(r"(20\d{2})\s*级", norm(line))}
              for page, page_text in pages.items() for line_no, line in enumerate(page_text.splitlines(), 1)
              if re.search(r"20\d{2}\s*级", norm(line))]
    mismatch = [v for v in titles if any(c != str(candidate["cohort"]) for c in v["cohort_mentions"])]
    meta = {"plan_number": number, "plan_ref": "PLAN-" + digest(f"{number}:{pdf_hash}".encode())[:16],
            "source_plan_key": candidate["source_plan_key"], "cohort_from_directory": candidate["cohort"],
            "major_name_raw": candidate["major_name"], "college_name_raw": candidate["college_name"],
            "study_years_raw": candidate["study_years_raw"], "degree_raw": candidate["degree_raw"],
            "directory_minimum_graduation_credits_raw": candidate["minimum_graduation_credits_raw"],
            "directory_notes_raw": candidate["notes_raw"], "pdf_sha256": pdf_hash, "text_sha256": digest(raw_text.encode()),
            "page_count": len(pages), "pdf_path": str(pdf.resolve()), "text_path": str(text_file.resolve()),
            "source_receipt_id": candidate["pdf"]["receipt_id"], "directory_receipt_id": candidate["source"]["receipt_id"],
            "directory_object_path": str(directory_object.resolve()), "directory_object_sha256": candidate["source"]["sha256"],
            "directory_actual_row_index": row_index, "directory_candidate_row_index": candidate["source"]["row_index"],
            "directory_credit_accounts": accounts,
            "directory_related_credit_fields_raw": {field: {"field_present": field in directory_row, "raw_value": directory_row.get(field)}
                                                    for field in ["wfxxf", "fxxf", "exwxf", "kcdlxfyq", "zdbyxf"]},
            "title_and_cohort_mentions": titles, "conflicting_cohort_mentions": mismatch,
            "student_applicability_verified": False, "policy_review_complete": False,
            "pdf_minimum_graduation_lines": [{"page": page, "raw_text": line} for page, text in pages.items()
                                             for line in text.splitlines() if "最低毕业学分" in norm(line)],
            "graduation_total_modified": False}
    return meta, pages


def build_inventory(batch: Path, repo: Path):
    manifest_path = batch / "normalized" / "plan_manifest.json"
    candidates_path = batch / "normalized" / "plan_candidates.json"
    manifest = json.loads(manifest_path.read_text())
    candidates = json.loads(candidates_path.read_text())
    if len(candidates) != manifest["directory_records"] or len({c["plan_number"] for c in candidates}) != len(candidates):
        raise ValueError("Plan manifest reconciliation failed")
    plans, records, review = [], [], []
    for candidate in sorted(candidates, key=lambda c: c["plan_number"]):
        if candidate["pdf_status"] != "success":
            raise ValueError("Inventory requires persisted successful PDFs")
        meta, pages = source_plan(batch, candidate)
        observations, unclassified = scan_plan(meta, pages)
        plans.append(meta)
        records.extend(observations)
        review.extend(unclassified)

    categories = []
    for key, definition in CATEGORIES.items():
        relevant = [r for r in records if r["category"] == key]
        refs = sorted({r["plan_ref"] for r in relevant})
        exact_names = sorted({r["source_spans"][0]["raw_text"].splitlines()[0].strip() for r in relevant})
        examples = sorted(relevant, key=lambda r: (r["course_table_interleaving"], r["plan_number"], r["source_spans"][0]["page"]))[:5]
        categories.append({"category": key, **definition, "plan_coverage_count": len(refs), "observation_count": len(relevant),
                           "applicable_plan_candidates": refs, "source_name_variants": exact_names,
                           "example_evidence_ids": [r["evidence_id"] for r in examples]})
    field_reference_files = [repo / "docs/数据结构与字段字典_v1.md", repo / "inputs/zju_course_agent_handoff_v1/data/import_data_dictionary.json",
                             repo / "inputs/zju_course_agent_handoff_v1/data/plan_registry.json", repo / "inputs/zju_course_agent_handoff_v1/data/requirement_candidates.json",
                             repo / "inputs/zju_course_agent_handoff_v1/data/source_issues.json"]
    sources = [{"path": str(p.resolve()), "sha256": digest(p.read_bytes()), "role": "field_or_historical_candidate_reference_not_new_observation"}
               for p in field_reference_files]
    assertions = {"manifest_plan_count_matches": len(plans) == manifest["directory_records"],
                  "all_pdf_named_and_object_sha256_match_manifest": True, "all_source_pages_contiguous": True,
                  "source_span_replay_exact": True, "every_evidence_id_unique": len({r["evidence_id"] for r in records}) == len(records),
                  "only_second_third_fourth_deferred": all(r["scope_status"] != "deferred_by_user" or r["category"] in {"second_classroom", "third_classroom", "fourth_classroom"} for r in records),
                  "no_executable_rules_created": all(r["enforce_automatically"] is False for r in records),
                  "graduation_totals_unmodified": all(p["graduation_total_modified"] is False for p in plans),
                  "no_source_mutation_network_or_model_call": True}
    for p in plans:
        pages = parse_pages(Path(p["text_path"]).read_text())
        for record in [r for r in records if r["plan_ref"] == p["plan_ref"]]:
            for span in record["source_spans"]:
                replay = "\n".join(pages[span["page"]].splitlines()[span["line_start"] - 1:span["line_end"]])
                if replay != span["raw_text"]:
                    raise ValueError("Source span replay failed")
        if digest(Path(p["pdf_path"]).read_bytes()) != p["pdf_sha256"] or digest(Path(p["text_path"]).read_bytes()) != p["text_sha256"]:
            raise ValueError("Source changed while inventory was being built")
    assertions["source_pdf_and_text_hashes_unchanged_after_scan"] = True
    for p in plans:
        if digest(Path(p["directory_object_path"]).read_bytes()) != p["directory_object_sha256"]:
            raise ValueError("Directory source changed while scanning")
    assertions["all_directory_rows_bound_by_own_plan_identity_and_hash"] = True
    directory_accounts = [{"plan_ref": p["plan_ref"], "plan_number": p["plan_number"], "cohort_from_directory": p["cohort_from_directory"],
                           "major_name_raw": p["major_name_raw"], "source_object_sha256": p["directory_object_sha256"],
                           "source_receipt_id": p["directory_receipt_id"], "source_row_index": p["directory_actual_row_index"],
                           "source_plan_key": p["source_plan_key"], **account}
                          for p in plans for account in p["directory_credit_accounts"]]
    if not all(assertions.values()):
        raise ValueError("Inventory invariants failed")
    summary = {"schema_version": VERSION, "source_batch_manifest_sha256": digest(manifest_path.read_bytes()),
               "source_plan_candidates_sha256": digest(candidates_path.read_bytes()), "scanned_plans": len(plans),
               "scanned_pages": sum(p["page_count"] for p in plans), "source_pdf_integrity_verified": len(plans),
               "classified_observations": len(records), "broad_review_candidates": len(review),
               "broad_review_candidate_kinds": dict(Counter(r["candidate_kind"] for r in review)),
               "plans_with_conflicting_cohort_mentions": sum(bool(p["conflicting_cohort_mentions"]) for p in plans),
               "categories": [{k: c[k] for k in ("category", "name_raw", "plan_coverage_count", "observation_count", "scope_status")} for c in categories],
               "validation": assertions, "official_policy_review_complete": False, "visual_review_all_pages": False,
               "student_applicability_verified": False, "field_references": sources,
               "directory_account_fields": {key: {"plan_coverage_count": len([a for a in directory_accounts if a["category"] == key and a["field_present"]]),
                                                    "raw_value_distribution": dict(Counter(str(a["raw_value"]) for a in directory_accounts if a["category"] == key)),
                                                    "field_state_distribution": dict(Counter(a["field_state"] for a in directory_accounts if a["category"] == key))}
                                            for key in ["second_classroom", "third_classroom", "fourth_classroom"]},
               "interpretation": "Coverage counts indicate observed text in plan candidates, not verified personal applicability. Numeric mentions are not activated thresholds. Unmatched text is unknown, not proof of absence."}
    issues = [{"issue_id": "REQ-COHORT-" + digest(f"{p['plan_ref']}:{mention['page']}:{mention['raw_text']}".encode())[:12],
               "issue_kind": "directory_vs_pdf_page_cohort_conflict", "plan_ref": p["plan_ref"], "plan_number": p["plan_number"],
               "directory_cohort": p["cohort_from_directory"], "pdf_page": mention["page"], "pdf_cohort_mentions": mention["cohort_mentions"],
               "raw_text": mention["raw_text"], "pdf_sha256": p["pdf_sha256"],
               "decision": "retain_original_quarantine_problem_page_pending_review_not_rewrite_cohort"}
              for p in plans for mention in p["conflicting_cohort_mentions"]]
    issues += [{"issue_id": "REQ-EXCEPTION-" + r["evidence_id"].removeprefix("REQ-"),
                "issue_kind": "recognition_zero_header_exception_and_generic_body", "evidence_id": r["evidence_id"],
                "plan_ref": r["plan_ref"], "category": r["category"],
                "decision": "retain_both_do_not_enforce_generic_threshold_without_applicability_review"}
               for r in records if "international_student_recognition_exception_with_generic_body" in r["interpretation_flags"]]
    return {"summary.json": summary, "plans.json": plans, "requirements.json": records, "issues.json": issues,
            "categories.json": categories, "review_candidates.json": review, "directory_accounts.json": directory_accounts}


def render_report(payload):
    summary, cats, plans, records = (payload[k] for k in ("summary.json", "categories.json", "plans.json", "requirements.json"))
    by_evidence = {r["evidence_id"]: r for r in records}
    lines = ["# 培养方案特殊要求本机盘点 v1", "", f"规则扫描覆盖 {summary['scanned_plans']} 份、{summary['scanned_pages']} 页；命名PDF及对象哈希 {summary['source_pdf_integrity_verified']} 份一致。",
             "本报告含真实方案定位，只保留本机忽略目录。数字是文本出现覆盖，不是本人适用性或官方政策审核。", "",
             "第二、第三、第四课堂仅标记 deferred_by_user；原件、课程表行、毕业总要求不改。其他类别为建议，未自动排除。", ""]
    lines += ["目录三账户字段逐条另存 directory_accounts.json，显式0与unknown分别保留。PDF标题覆盖和目录字段覆盖分开：", ""]
    for key, stats in summary["directory_account_fields"].items():
        lines += [f"- {CATEGORIES[key]['name_raw']}目录字段覆盖 {stats['plan_coverage_count']} 份，原值分布 {stats['raw_value_distribution']}，状态分布 {stats['field_state_distribution']}。"]
    lines += [""]
    for cat in cats:
        lines += [f"## {cat['name_raw']}：{cat['plan_coverage_count']} 份 / {cat['observation_count']} 条证据", "",
                  "单位：" + "、".join(cat["units"]) + "；范围：" + cat["scope_status"], cat["relation_to_ordinary_courses"], cat["recommendation"], ""]
        for evid in cat["example_evidence_ids"][:3]:
            r = by_evidence[evid]
            pages = ",".join(str(s["page"]) for s in r["source_spans"])
            snippet = " ".join(s["raw_text"].replace("\n", " ") for s in r["source_spans"])[:900]
            lines += [f"- {evid}；{r['plan_number']} {r['major_name_raw']}；PDF物理页 {pages}：{snippet}"]
        lines += [""]
    lines += ["## 待核对与限制", "", f"广义触发词未归类候选 {summary['broad_review_candidates']} 条，按原行/上下文保存在 review_candidates.json；不是新增已确认要求。",
              "提取文本可混排课程表、跨页段落和图示。course_table_interleaving=true 的证据须按PDF核对，不能按整个页/段自动过滤课程。",
              "本轮页文本扫描全部覆盖，代表页视觉核验另列 visual_review.json；未逐页视觉审阅全部PDF，未审核激活原23条规则。",
              "目录与PDF中出现不同年级的页单独隔离；不按目录静默改写正文年级。", ""]
    for p in plans:
        for mention in p["conflicting_cohort_mentions"]:
            lines += [f"- {p['plan_ref']} {p['plan_number']} {p['major_name_raw']}：目录 {p['cohort_from_directory']}，PDF第{mention['page']}页 {mention['raw_text'].strip()}。"]
    return "\n".join(lines) + "\n"


def resolve_output(repo: Path, requested: str) -> Path:
    allowed = (repo / "local-only").resolve()
    output = (repo / requested).resolve()
    if output == allowed or allowed not in output.parents:
        raise ValueError("Real requirement artifacts must be inside this repository's local-only/ directory")
    return output


def write_inventory(payload, batch: Path, output: Path, script_sha256: str):
    """Immutable content-addressed runs; same inputs reuse identical bytes."""
    blobs = {name: canonical(data) for name, data in payload.items()}
    blobs["report.md"] = render_report(payload).encode()
    hashes = {name: digest(blob) for name, blob in sorted(blobs.items())}
    manifest = {"schema_version": VERSION, "source_batch": str(batch), "source_code_sha256": script_sha256, "files": hashes,
                "source_digest": digest(canonical([{k: p[k] for k in ("plan_number", "pdf_sha256", "text_sha256", "directory_object_sha256")} for p in payload["plans.json"]]))}
    version = "v1-" + digest(canonical(manifest))[:20]
    target = output / version
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    os.chmod(target, 0o700)
    blobs["manifest.json"] = canonical({**manifest, "version_directory": version})
    for name, blob in blobs.items():
        path = target / name
        if path.exists():
            if path.read_bytes() != blob:
                raise ValueError(f"Refusing to overwrite different historical derived bytes: {path.name}")
            continue
        with path.open("xb") as handle:
            handle.write(blob)
        os.chmod(path, 0o600)
    return target, digest(blobs["manifest.json"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default=DEFAULT_BATCH)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    batch = (repo / args.batch).resolve()
    if not (batch / "normalized/plan_manifest.json").is_file():
        batches = sorted(p for p in batch.glob("backend-*") if (p / "normalized/plan_manifest.json").is_file())
        if len(batches) != 1:
            parser.error("Select one persisted source batch explicitly with --batch")
        batch = batches[0]
    try:
        output = resolve_output(repo, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    if batch == output or batch in output.parents:
        parser.error("Derived output must not be inside the source batch")
    # Source and output can be elsewhere for fixture verification; real artifacts
    # must stay local and ignored rather than entering tracked documentation.
    payload = build_inventory(batch, repo)
    target, manifest_hash = write_inventory(payload, batch, output, digest(Path(__file__).read_bytes()))
    print(json.dumps({"output": str(target), "scanned_plans": payload["summary.json"]["scanned_plans"],
                      "scanned_pages": payload["summary.json"]["scanned_pages"], "manifest_sha256": manifest_hash,
                      "validation": payload["summary.json"]["validation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
