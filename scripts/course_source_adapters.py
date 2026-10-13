"""Read immutable local school evidence without network or import side effects.

All locators and raw page state returned by this module are restricted local
evidence. Only ``fields`` are eligible for the merge's shared projection.
"""
from __future__ import annotations

from collections import Counter
import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re

VERSION = "course-source-adapters/1"
BACKEND = "local-only/school_backend_full_20261013"
BROWSER = "local-only/school_bulk_20261013"
PROBE = "local-only/school_backend_probe_20261013"
MISSING = {"", "--", "undefined", "null", "待定", "未知"}
CLASSIFICATIONS = {"xk_1": "本类(专业)", "xk_b": "通识必修课程/全部课程", "xk_n": "通识选修课程/全部课程", "xk_8": "体育课程", "foundation": "专业基础课程", "zy_qb": "专业课程/所有类（专业）"}


class PanelParser(HTMLParser):
    """Pure DOM panel parser; copied here instead of executing source scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.header, self.rows, self.headers, self.attrs = [], [], [], {}
        self.h3 = self.in_body = False
        self.skip_span = self.span_depth = 0
        self.row = self.cell = self.th = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cell is not None and tag != "td":
            self.cell["html"] += self.get_starttag_text()
        if tag == "div" and "kc_head" in a.get("class", "").split():
            self.attrs = {k: v for k, v in a.items() if k.startswith("data-")}
        if tag == "h3":
            self.h3 = True
        if self.h3 and tag == "span":
            self.span_depth += 1
            if "display:none" in a.get("style", "").replace(" ", ""):
                self.skip_span = self.span_depth
        if tag == "tbody":
            self.in_body = True
        if tag == "tr" and self.in_body:
            self.row = {"id": a.get("id"), "class": a.get("class", ""),
                        "attributes": {k: v for k, v in a.items() if k.startswith("data-")}, "cells": []}
        if tag == "td" and self.row is not None:
            self.cell = {"class": a.get("class", ""), "text": "", "html": "",
                         "visible": "display:none" not in a.get("style", "").replace(" ", "")}
        if tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"
        if tag == "th":
            self.th = {"text": "", "class": a.get("class", "")}

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.row["cells"].append(self.cell)
            self.cell = None
        elif self.cell is not None:
            self.cell["html"] += "</" + tag + ">"
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        if tag == "tbody":
            self.in_body = False
        if tag == "h3":
            self.h3 = False
        if tag == "span" and self.h3:
            if self.span_depth == self.skip_span:
                self.skip_span = 0
            self.span_depth -= 1
        if tag == "th" and self.th is not None:
            self.headers.append(self.th)
            self.th = None

    def handle_data(self, data):
        if self.h3 and not self.skip_span:
            self.header.append(data)
        if self.cell is not None:
            self.cell["text"] += data
            self.cell["html"] += data
        if self.th is not None:
            self.th["text"] += data

    def handle_entityref(self, name):
        self.handle_data(html.unescape("&" + name + ";"))

    def handle_charref(self, name):
        self.handle_data(html.unescape("&#" + name + ";"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def raw_text(value):
    if value is None:
        return None
    value = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.I)
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


def text(value):
    value = raw_text(value)
    return None if value is None or value in MISSING else value


def segments(value):
    # A week annotation can contain semicolons: {秋1-8周；冬1-8周}.
    # Only top-level separators split meetings, never annotation content.
    result, current, depth = [], [], 0
    for char in raw_text(value) or "":
        if char in "{[(（【":
            depth += 1
        elif char in "}])）】":
            depth = max(0, depth - 1)
        if char in "\r\n" or (char in ";；" and depth == 0):
            part = "".join(current).strip()
            if text(part):
                result.append(part)
            current = []
        else:
            current.append(char)
    part = "".join(current).strip()
    if text(part):
        result.append(part)
    return result


def key_term(value):
    m = re.match(r"^T?\((\d{4}-\d{4}-\d+)\)-", str(value or ""))
    return m[1] if m else None


def meeting_packet(time_value, place_value):
    times, places = segments(time_value), segments(place_value)
    if not times:
        return [{"time_raw": raw_text(time_value), "place_raw": raw_text(place_value), "weekday": None,
                 "periods": None, "week_set": None, "calendar_version": None, "alignment": "unknown"}]
    result = []
    for i, t in enumerate(times):
        m = re.fullmatch(r"周([一二三四五六日天])第([0-9,，、]+)节(?:\{(.*?)\})?", t)
        aligned = len(times) == len(places)
        result.append({"time_raw": t, "place_raw": places[i] if aligned else None,
                       "weekday": "一二三四五六日".index(m[1].replace("天", "日")) + 1 if m else None,
                       "periods": [int(v) for v in re.split(r"[,，、]", m[2])] if m else None,
                       "week_set": None, "calendar_version": None,
                       "week_annotation_raw": m[3] if m else None,
                       "week_annotation_state": "observed_not_normalized" if m and m[3] else "not_observed",
                       "alignment": "positional_source_order" if aligned else "unverified_segment_alignment",
                       "unaligned_places_raw": [] if aligned else places})
    return result


def exam_packet(value):
    raw = raw_text(value)
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日\((\d{1,2}:\d{2})-(\d{1,2}:\d{2})\)", raw or "")
    date = start = end = None
    if m:
        try:
            date = datetime.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            start, end = m[4].zfill(5), m[5].zfill(5)
            datetime.time.fromisoformat(start)
            datetime.time.fromisoformat(end)
        except ValueError:
            date = start = end = None
    return {"raw": raw, "status": "observed_local_datetime_timezone_unverified" if date else
            "explicit_pending" if raw == "待定" else "unknown_or_unparsed", "date": date,
            "start": start, "end": end, "timezone": None}


def capacity_packet(value, pending=None, male=None, female=None):
    raw = raw_text(value)
    m = re.fullmatch(r"(-?\d+)\s*/\s*(\d+)", raw or "")
    return {"raw": raw, "remaining": int(m[1]) if m else None, "capacity": int(m[2]) if m else None,
            "pending_raw": pending, "male_raw": male, "female_raw": female,
            "hidden_counter_semantics_verified": False}


class Loader:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.evidence = {}
        self.courses, self.sections, self.aliases = [], [], []
        self.backend_display_codes = {}
        self.audit = {"adapter_version": VERSION, "binding_errors": [], "source_filter_template_code_mismatches": 0}

    @property
    def backend_root(self):
        """Resolve the one persisted local batch without publishing its identity."""
        root = self.project / BACKEND
        if (root / "receipts.jsonl").is_file():
            return BACKEND
        batches = sorted(p for p in root.glob("backend-*")
                         if (p / "receipts.jsonl").is_file()
                         and (p / "normalized/manifest.json").is_file())
        if len(batches) != 1:
            raise ValueError("exactly_one_local_backend_batch_required")
        return str(batches[0].relative_to(self.project))

    def read(self, path, expected=None):
        p = (self.project / path).resolve()
        if not p.is_relative_to(self.project / "local-only"):
            raise ValueError("source_path_outside_project_local_only")
        data = p.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if expected and sha != expected:
            raise ValueError("source_hash_mismatch:" + str(path))
        self.evidence[str(p.relative_to(self.project))] = {"path": str(p.relative_to(self.project)), "sha256": sha, "bytes": len(data)}
        return data, sha

    def load(self, path, expected=None):
        data, sha = self.read(path, expected)
        return json.loads(data), sha

    def lines(self, path):
        data, _ = self.read(path)
        return [json.loads(line) for line in data.splitlines() if line.strip()]

    def source(self, route, path, sha, pointer, observed_at, receipt_id=None):
        return {"route": route, "path": str(path), "sha256": sha, "row_pointer": pointer,
                "observed_at": observed_at, "receipt_id": receipt_id, "adapter_version": VERSION}

    def raw_course(self, row, source, context, classification, excluded=None):
        info = str(row.get("kcxx") or "").split("~")
        fields = {"name": text(row.get("kcmc")), "display_code": text(row.get("xskcdm")),
                  "credits": info[1] if len(info) == 3 else None, "hours": text(info[2]) if len(info) == 3 else None,
                  "college": text(row.get("kkxy")), "prerequisite_hint": None, "term_part": text(row.get("xxq"))}
        if source["route"] == "backend_full" and not excluded and fields["display_code"]:
            self.backend_display_codes.setdefault(fields["display_code"], set()).add(str(row["kcdm"]))
        self.courses.append({"id": digest(["course", source]), "code": str(row["kcdm"]),
                             "term": key_term(row.get("xkkh")) or context.get("term"), "classification": classification,
                             "fields": fields, "scoped_fields": {"category": text(row.get("kclb")), "nature": text(row.get("kcxz"))},
                             "source": source, "context": context, "excluded_reason": excluded,
                             "personal": {k: row[k] for k in ("kcxx", "kcxzzt", "tsbj", "xkzy") if k in row}})

    def raw_section(self, row, source, context, code):
        fields = {"teacher_names": text(row.get("jsxm")), "teacher_source_ids": text(row.get("jszgh")),
                  "term_part": text(row.get("vxxq") or row.get("xxq")), "hours": text(row.get("zxs")),
                  "instruction_mode": text(row.get("jxfs")), "teaching_form": text(row.get("skxs")),
                  "target": text(row.get("mxdx")), "international": text(row.get("gjhkc")), "textbook": text(row.get("jcmc"))}
        key = text(row.get("xkkh"))
        term = key_term(key) or context.get("term")
        if key and not key.startswith(f"({term})-{code}-"):
            raise ValueError("section_course_key_conflict")
        # sksj preserves explicit <br> grouping; vsksj is its semicolon projection.
        meetings = meeting_packet(row.get("sksj") or row.get("vsksj"), row.get("skdd"))
        self.sections.append({"id": digest(["section", source]), "code": code, "term": term, "source_key": key,
                              "fields": fields, "meetings": meetings,
                              "exam": exam_packet(row.get("vkssj") or row.get("kssj")),
                              "capacity": capacity_packet(row.get("rs"), row.get("yxrs"), row.get("brs"), row.get("grs")),
                              "personal": {k: row[k] for k in ("sfxz", "xkzy", "yxrs", "brs", "grs", "brl", "grl") if k in row},
                              "source": source, "context": context})

    def backend(self):
        base = self.backend_root
        receipts = self.lines(base + "/receipts.jsonl")
        by_id = {r["receipt_id"]: r for r in receipts}
        scans, _ = self.load(base + "/scans.json")
        allowed = {s["scope"] for s in scans.values()}
        self.audit["backend_receipts_by_kind_outcome"] = dict(Counter(r["kind"] + ":" + r["outcome"] for r in receipts))
        objects = {}
        for r in receipts:
            if r["kind"] not in {"courses", "sections"} or r["outcome"] not in {"success", "empty_unverified"}:
                continue
            path = base + "/" + r["object"]
            if path not in objects:
                objects[path] = self.load(path, r["sha256"])[0]
            rows = objects[path]
            if not isinstance(rows, list) or len(rows) != r["records"]:
                raise ValueError("source_receipt_row_count_mismatch")
            filters = r["source"]["filters"]
            if r["kind"] == "courses":
                excluded = None if r["scope"] in allowed else "user_excluded_partial_cross_category_collection"
                context = {"source_scope": r["scope"], "filters": filters,
                           "term": f'{filters["xn"]}-{filters["xq"]}', "context_completeness": "verified_source_filters"}
                for i, row in enumerate(rows):
                    self.raw_course(row, self.source("backend_full", path, r["sha256"], f"/{i}", r["observed_at"], r["receipt_id"]), context, r["source"].get("label"), excluded)
            else:
                parent, bound = r.get("parent"), r.get("target", {}).get("bound_course")
                if not parent or not bound or r["target"].get("parent") != parent:
                    raise ValueError("missing_bound_parent")
                pr = by_id[parent["receipt_id"]]
                ppath = base + "/" + pr["object"]
                prow = objects.get(ppath)
                if prow is None:
                    prow = self.load(ppath, pr["sha256"])[0]
                actual = prow[parent["row_index"]]
                if actual["kcdm"] != parent["course_code"] or actual["xkkh"] != parent["query_key"] or bound != {"kcdm": actual["kcdm"], "xkkh": actual["xkkh"]} or parent["full_filters"] != pr["source"]["filters"] or parent["scope"] != pr["scope"]:
                    raise ValueError("bound_parent_response_mismatch")
                if filters.get("kcdm") != bound["kcdm"] or filters.get("xkkh") != bound["xkkh"]:
                    self.audit["source_filter_template_code_mismatches"] += 1
                context = {"source_scope": parent["scope"], "filters": parent["full_filters"], "parent": parent,
                           "bound_course": bound, "term": f'{filters["xn"]}-{filters["xq"]}', "context_completeness": "verified_bound_parent"}
                for i, row in enumerate(rows):
                    self.raw_section(row, self.source("backend_full", path, r["sha256"], f"/{i}", r["observed_at"], r["receipt_id"]), context, parent["course_code"])
        conflicts, _ = self.load(base + "/normalized/conflicts.json")
        self.audit["original_backend_candidate_conflicts"] = conflicts

    def browser_course(self, obs, path, sha, pointer="", route="browser", extra_context=None, text_span=None, row_pointers=None):
        dom_code, header = str(obs["code"]), raw_text(obs["header"]) or ""
        display = re.search(re.escape(dom_code) + r"(?:（[^）]*）)?", header)
        code = dom_code
        code_binding = None
        candidates = self.backend_display_codes.get(display[0] if display else "", set())
        if len(candidates) == 1 and dom_code not in candidates:
            candidate = next(iter(candidates))
            # Identity changes require exact source evidence. Never use names,
            # teachers or similarity. The backend kcdm binds this exact display
            # code; every expanded DOM row also names it in key/action code.
            for row in obs.get("rows", []):
                key = row.get("attributes", {}).get("data-xkkh") or next((c["text"] for c in row["cells"] if c.get("class") == "xkkh"), None)
                action_codes = re.findall(r'data-kcdm=[\"\x27]([^\"\x27]+)', " ".join(c.get("html", "") for c in row["cells"]))
                if not key or not str(key).startswith(f"({key_term(key)})-{candidate}-") or action_codes != [candidate]:
                    raise ValueError("display_query_code_alias_binding_not_verified")
            code = candidate
            code_binding = {"dom_code": dom_code, "query_code": code, "display_code": display[0],
                            "evidence": "exact_backend_display_binding_and_dom_section_key_action_code"}
        row_terms = {key_term(row.get("attributes", {}).get("data-xkkh") or next((c["text"] for c in row["cells"] if c.get("class") == "xkkh"), None)) for row in obs.get("rows", [])}
        row_terms.discard(None)
        if len(row_terms) > 1:
            raise ValueError("browser_course_multiple_terms")
        explicit_term = f'{obs["academic_year_raw"]}-1' if obs.get("academic_year_raw") and obs.get("term_raw") in {"秋、冬", "秋冬"} else None
        term = next(iter(row_terms), explicit_term) or (extra_context or {}).get("term")
        scope = obs.get("source_scope")
        context = {"source_scope": scope, "academic_year_raw": obs.get("academic_year_raw"), "term_raw": obs.get("term_raw"),
                   "term": term, "context_completeness": "observed_dom_partial_filters", **(extra_context or {})}
        if code_binding:
            context["code_binding"] = code_binding
            self.aliases.append({"source_path": path, "source_sha256": sha, **code_binding,
                                 "reason": "observed_display_code_to_query_code_alias_not_new_course"})
        source = self.source(route, path, sha, pointer, obs.get("observed_at"))
        if text_span is not None:
            source["text_span"] = text_span
        credit = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*学分", header)
        name = re.search(re.escape(display[0] if display else dom_code) + r"\s*(.*?)\s*([0-9]+(?:\.[0-9]+)?)\s*学分", header)
        category = re.search(r"课程类别\s*：\s*(.*?)\s*开课学院", header)
        college = re.search(r"开课学院\s*：\s*(.*?)(?:\s*状态\s*：|\s*展开|$)", header)
        status = re.search(r"状态\s*：\s*([^\r\n]*?)(?:\s*展开|$)", header)
        self.courses.append({"id": digest(["course", source]), "code": code, "term": term, "classification": CLASSIFICATIONS.get(scope, scope),
                             "fields": {"name": text(name[1]) if name else None, "display_code": display[0] if display else None,
                                        "credits": credit[1] if credit else None, "hours": None, "college": text(college[1]) if college else None,
                                        "prerequisite_hint": "有预修要求" if "有预修要求" in header else None, "term_part": None},
                             "scoped_fields": {"category": text(category[1]) if category else None, "nature": None},
                             "source": source, "context": context, "excluded_reason": None,
                             "personal": {"page_status_raw": text(status[1]) if status else None, "header_attributes_raw": obs.get("header_attributes", {})}})
        for i, row in enumerate(obs.get("rows", [])):
            cells = {c["class"]: raw_text(c["text"]) for c in row["cells"]}
            attr = row.get("attributes", {})
            key = attr.get("data-xkkh") or cells.get("xkkh")
            if not key and row.get("id", "").startswith("tr_"):
                key = row["id"][3:]
            teacher_ids = re.findall(r"showTeacherInfo\([^)]*,'([^']+)'\)", " ".join(c.get("html", "") for c in row["cells"]))
            row_pointer = row_pointers[i] if row_pointers else pointer + f"/rows/{i}"
            src = self.source(route, path, sha, row_pointer, obs.get("observed_at"))
            self.sections.append({"id": digest(["section", src]), "code": code, "term": key_term(key) or term, "source_key": key,
                                  "fields": {"teacher_names": text(cells.get("jsxm")), "teacher_source_ids": teacher_ids[0] if teacher_ids else None,
                                             "term_part": text(cells.get("xxq")), "hours": text(attr.get("data-zxs")),
                                             "instruction_mode": text(cells.get("jxfs")), "teaching_form": text(cells.get("skxs")),
                                             "target": text(cells.get("mxdx")), "international": text(cells.get("gjhkc")), "textbook": None},
                                  "meetings": meeting_packet(cells.get("sksj"), cells.get("skdd")), "exam": exam_packet(cells.get("kssj")),
                                  "capacity": capacity_packet(cells.get("rsxx"), {"major": cells.get("bxdd"), "all": cells.get("sydd")}, cells.get("brsxx"), cells.get("grsxx")),
                                  "personal": {"row_class_raw": row.get("class"), "action_raw": cells.get("an"),
                                               "page_selected_raw": attr.get("data-sfxz"), "major_pending_raw": cells.get("bxdd"), "all_pending_raw": cells.get("sydd")},
                                  "source": src, "context": context})

    def browser(self):
        skipped = 0
        for p in sorted((self.project / BROWSER / "selection_courses").glob("*.json")):
            path = str(p.relative_to(self.project))
            obs, sha = self.load(path)
            if obs.get("raw_html_path"):
                skipped += 1
                self.aliases.append({"alias_path": path, "alias_sha256": sha, "original_path": BROWSER + "/" + obs["raw_html_path"], "reason": "derived_html_json_not_new_observation"})
                continue
            self.browser_course(obs, path, sha)
        initial_path = BROWSER + "/selection_raw/category_xk_1.json"
        initial, sha = self.load(initial_path)
        for i, c in enumerate(initial["courses"]):
            self.browser_course({**c, "source_scope": "xk_1", "observed_at": initial["observed_at"]}, initial_path, sha, f"/courses/{i}")
        receipts = self.lines(BROWSER + "/selection_html_receipts.jsonl")
        paths = Counter(r["path"] for r in receipts)
        if any(n > 1 for n in paths.values()):
            raise ValueError("multiple_html_receipts_same_path_require_history_review")
        for r in receipts:
            path = BROWSER + "/" + r["path"]
            raw, sha = self.read(path)
            parser = PanelParser()
            parser.feed(raw.decode())
            if len(parser.rows) != r["rows"]:
                raise ValueError("html_receipt_rows_mismatch")
            self.browser_course({"code": r["code"], "header": "".join(parser.header), "header_attributes": parser.attrs,
                                 "rows": parser.rows, "observed_at": r["observed_at"], "source_scope": r["scope"],
                                 "academic_year_raw": r["academic_year_raw"], "term_raw": r["term_raw"]}, path, sha)
        self.audit["browser_derived_json_aliases_skipped"] = skipped
        self.audit["browser_html_receipts"] = len(receipts)
        self.audit["browser_multiple_receipts_same_path"] = 0

    def probe(self):
        journal = self.lines(PROBE + "/journal.jsonl")
        course_entry = next(r for r in journal if r["kind"] == "courses")
        section_entry = next(r for r in journal if r["kind"] == "sections")
        context = {"source_scope": course_entry["scope"], "filters": course_entry["source"]["filters"], "term": "2026-2027-1", "context_completeness": "probe_observed_filters"}
        for r in journal:
            if r["kind"] not in {"courses", "sections"}:
                continue
            path = PROBE + "/" + r["object"]
            rows, sha = self.load(path, r["sha256"])
            for i, row in enumerate(rows):
                src = self.source("backend_probe", path, sha, f"/{i}", r["observedAt"], r["key"])
                if r["kind"] == "courses":
                    self.raw_course(row, src, context, "本类(专业)")
                else:
                    self.raw_section(row, src, context, r["source"]["filters"]["kcdm"])
        network, _ = self.load(PROBE + "/qa/network_observation.json")
        times = {r["evidence_ref"]: r["observed_at"] for r in network["records"]}
        for kind, name, time_ref in [("courses", "raw/courses_ui.json", "courses_ui"), ("sections", "raw/sections_ui.json", "sections_ui"), ("sections", "raw/sections_replay.json", None)]:
            path = PROBE + "/" + name
            rows, sha = self.load(path)
            for i, row in enumerate(rows):
                src = self.source("probe_ui" if time_ref else "probe_manual_replay", path, sha, f"/{i}", times.get(time_ref))
                if kind == "courses":
                    self.raw_course(row, src, context, "本类(专业)")
                else:
                    self.raw_section(row, src, context, section_entry["source"]["filters"]["kcdm"])
        path = PROBE + "/raw/course_headers_dom.json"
        headers, sha = self.load(path)
        for i, h in enumerate(headers["headers"]):
            self.browser_course({"code": h["course_code"], "header": h["text"], "source_scope": "xk_1", "rows": [], "observed_at": headers["observed_at"]}, path, sha, f"/headers/{i}", "probe_dom_header", context)
        path = PROBE + "/raw/selection_course_dom.json"
        dom, sha = self.load(path)
        # Separate header observations are extracted from this independently timed
        # DOM snapshot; its course block exists even when a class is not expanded.
        chunks = re.split(r"(?=^(?:【有预修要求】\s*)?[A-Z][A-Z0-9]*（)", dom["content"], flags=re.M)
        observed_codes = []
        for i, chunk in enumerate(chunks):
            match = re.match(r"(?:【有预修要求】\s*)?([A-Z][A-Z0-9]*)（", chunk)
            if not match:
                continue
            code = match[1]
            observed_codes.append(code)
            header = chunk.split("展开关闭", 1)[0]
            indices = [n for n, row in enumerate(dom["rows"]) if any(c.get("class") == "xkkh" and str(c.get("text", "")).startswith(f"(2026-2027-1)-{code}-") for c in row["cells"])]
            rows = [dom["rows"][n] for n in indices]
            start = dom["content"].find(chunk)
            self.browser_course({"code": code, "header": header, "source_scope": "xk_1", "rows": rows, "observed_at": dom["observed_at"]}, path, sha, "/content", "probe_dom", context,
                                text_span=[start, start + len(header)], row_pointers=[f"/rows/{n}" for n in indices])
        self.audit["probe_dom_course_blocks"] = len(observed_codes)
        self.audit["probe_manual_observation_time"] = "unknown_retained_not_inferred_from_mtime"

    def result(self):
        self.courses.sort(key=lambda r: r["id"])
        self.sections.sort(key=lambda r: r["id"])
        backend = [r for r in self.sections if r["source"]["route"] == "backend_full"]
        browser = [r for r in self.sections if r["source"]["route"] == "browser"]
        backend_keys, browser_keys = {r["source_key"] for r in backend}, {r["source_key"] for r in browser}
        self.audit["source_files"] = [self.evidence[k] for k in sorted(self.evidence)]
        self.audit["browser_only_candidate_source_keys"] = sorted(browser_keys - backend_keys)
        self.audit["backend_only_candidate_source_key_count"] = len(backend_keys - browser_keys)
        self.audit["backend_time_segment_distribution"] = dict(sorted(Counter(sum(text(m["time_raw"]) is not None for m in r["meetings"]) for r in backend).items()))
        self.audit["backend_meeting_records_including_unknown"] = sum(len(r["meetings"]) for r in backend)
        counts = {"course_observations_by_route": dict(sorted(Counter(r["source"]["route"] for r in self.courses).items())),
                  "section_observations_by_route": dict(sorted(Counter(r["source"]["route"] for r in self.sections).items())),
                  "course_observations": len(self.courses), "section_observations": len(self.sections),
                  "excluded_course_observations": sum(bool(r["excluded_reason"]) for r in self.courses),
                  "business_course_code_groups": len({r["code"] for r in self.courses if not r["excluded_reason"]}),
                  "candidate_source_section_key_groups": len({(r["code"], r["term"], r["source_key"]) for r in self.sections})}
        return {"course_observations": self.courses, "section_observations": self.sections,
                "evidence_aliases": sorted(self.aliases, key=lambda r: r.get("alias_path", r.get("source_path", ""))), "counts": counts, "audit": self.audit}


def load_sources(project: Path):
    loader = Loader(project)
    loader.backend()
    loader.browser()
    loader.probe()
    return loader.result()
