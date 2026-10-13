"""Local curriculum references, isolated from mock students and model services.

Only the explicitly granted curriculum slice is scored. Real source paths live
in the caller's private bundle; public results contain opaque references.
Assistant checks are engineering evidence, never human relevance gold.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import base64
import hmac
import json
from pathlib import Path
import re
import secrets
import time

from .reference_retrieval import terms

SCHEMA = "local-reference/1"
CHUNKING_VERSION = "curriculum-table-slice/1"
SCOPE_VERSION = "ordinary-reference-deferred-classrooms/1"
COMPONENTS = ("courses", "course_observations", "sections", "section_observations",
              "meeting_snapshots", "meetings", "exams", "capacity_snapshots")
REVIEW_STATUS = "assistant_reviewed_pending_human"
PAGE_MARKER = re.compile(r"^=== SOURCE PAGE (\d+) ===\n", re.M)
COURSE_ROW = re.compile(r"^\s*([A-Z]{2,}\d{3,}[A-Z0-9]*)\s*(.+?)\s+(\d+(?:\.\d+)?)\s+")
DEFERRED = re.compile(r"第[二三四]课堂|[二三四]课堂")


class LocalReferenceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise LocalReferenceError(code, message)


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _bytes(path, expected=None):
    try:
        data = Path(path).read_bytes()
    except OSError:
        _fail("source_unavailable", "本机参考来源不可用")
    if expected is not None and sha256(data).hexdigest() != expected:
        _fail("stale_input", "参考来源摘要已改变，请建立新版本")
    return data


def _json(path, expected=None):
    data = _bytes(path, expected)
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        _fail("source_unavailable", "本机参考文件格式无效")


def _pages(raw):
    parts = PAGE_MARKER.split(raw)
    numbers = [int(parts[i]) for i in range(1, len(parts), 2)]
    if not numbers or numbers != list(range(1, len(numbers) + 1)):
        _fail("source_unavailable", "物理页标记不连续或重复")
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def _file_entry(manifest, name):
    row = manifest.get("files", {}).get(name)
    if isinstance(row, str):
        return name, row
    if isinstance(row, dict) and isinstance(row.get("path"), str) and isinstance(row.get("sha256"), str):
        return row["path"], row["sha256"]
    _fail("source_unavailable", "参考清单缺少允许的组件")


def _child(base, name):
    path = (Path(base) / name).resolve()
    if not path.is_relative_to(Path(base).resolve()):
        _fail("inaccessible_resource", "参考文件不在获准来源范围")
    return path


def _controls(delivery_index):
    delivery = _json(delivery_index)
    try:
        dataset_path = Path(delivery["dataset_manifest"]).resolve()
        requirements_path = Path(delivery["requirements_manifest"]).resolve()
        dataset, requirements = _json(dataset_path), _json(requirements_path)
        if dataset.get("security_realm") != "restricted_reference" or dataset.get("shared_projection_contains_personal_state") is not False:
            _fail("inaccessible_resource", "来源不是独立受限参考共享投影")
        if delivery.get("dataset_version") != dataset.get("dataset_version"):
            _fail("stale_input", "交付入口与数据版本不一致")
        return {"delivery": delivery, "dataset": dataset, "requirements": requirements,
                "dataset_path": dataset_path, "requirements_path": requirements_path,
                "dataset_manifest_sha256": sha256(_bytes(dataset_path)).hexdigest(),
                "requirements_manifest_sha256": sha256(_bytes(requirements_path)).hexdigest()}
    except (KeyError, TypeError):
        _fail("source_unavailable", "本机参考入口缺少必要清单")


def _selected_metadata(controls, blueprint_sha256=None):
    file_name, expected = _file_entry(controls["requirements"], "plans.json")
    records = _json(_child(controls["requirements_path"].parent, file_name), expected)
    blueprint = _json(controls["delivery"]["rag_blueprint"], blueprint_sha256)
    selected = blueprint.get("source_registry", {}).get("EVAL-A", {})
    matching = [record for record in records if str(record.get("plan_number")) == str(selected.get("plan_number"))]
    if len(matching) != 1 or matching[0].get("pdf_sha256") != selected.get("pdf_sha256"):
        _fail("stale_input", "参考别名与获准来源登记不一致")
    source_batch = Path(controls["requirements"]["source_batch"]).resolve()
    for field in ("pdf_path", "text_path"):
        if not Path(matching[0][field]).resolve().is_relative_to(source_batch):
            _fail("inaccessible_resource", "参考别名不属于获准来源批次")
    return matching[0]


def _grant(grant_path, controls, clock):
    value = _json(grant_path)
    if (not isinstance(value, dict) or value.get("security_realm") != "restricted_reference" or
            value.get("subject_kind") != "local_review" or value.get("purpose") != "local_reference_review" or
            value.get("plan_path") != "major" or value.get("target_term") != "2026-2027-1" or
            not isinstance(value.get("grant_id"), str) or not value["grant_id"] or
            value.get("owner_student_id") is not None or value.get("auth_mode") in {"mock", "school"}):
        _fail("inaccessible_resource", "需要独立本机参考用途授权")
    if value.get("status") != "active" or value.get("revoked") is not False:
        _fail("inaccessible_resource", "本机参考授权已撤销或未启用")
    try:
        expiry = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry.timestamp() <= clock():
            _fail("inaccessible_resource", "本机参考授权已到期")
    except (KeyError, ValueError, TypeError):
        _fail("inaccessible_resource", "本机参考授权有效期无效")
    aliases = value.get("source_aliases")
    if not isinstance(aliases, list) or aliases != ["EVAL-A"]:
        _fail("inaccessible_resource", "本首片只允许已指定的参考方案")
    review_id = value.get("assistant_review_id")
    if not isinstance(value.get("assistant_reviewed_pages", {}), dict) or not isinstance(value.get("allowed_pages", {}), dict):
        _fail("inaccessible_resource", "参考页范围格式无效")
    reviewed_pages = value.get("assistant_reviewed_pages", {}).get("EVAL-A", [])
    if ((review_id is not None and (not isinstance(review_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", review_id))) or
            not isinstance(reviewed_pages, list) or any(type(page) is not int or page <= 0 for page in reviewed_pages)):
        _fail("inaccessible_resource", "助手审阅标识或页范围格式无效")
    allowed_pages = value.get("allowed_pages", {}).get("EVAL-A")
    if allowed_pages is not None and (not isinstance(allowed_pages, list) or
                                     any(type(page) is not int or page <= 0 for page in allowed_pages)):
        _fail("inaccessible_resource", "允许的参考页范围格式无效")
    source_versions = value.get("allowed_source_versions", [value.get("allowed_source_digest")])
    if (controls["dataset"].get("dataset_version") not in value.get("allowed_dataset_versions", []) or
            controls["requirements"].get("source_digest") not in source_versions):
        _fail("stale_input", "当前来源版本未在本机授权白名单中")
    return value


def _span(page_number, page_text, start_line, end_line):
    lines = page_text.splitlines(keepends=True)
    if not 0 <= start_line < end_line <= len(lines):
        _fail("source_unavailable", "参考片段的物理页定位无效")
    start, end = sum(map(len, lines[:start_line])), sum(map(len, lines[:end_line]))
    return {"physical_page": page_number, "char_start": start, "char_end": end,
            "line_start": start_line + 1, "line_end": end_line,
            "page_text_sha256": sha256(page_text.encode()).hexdigest(),
            "text": page_text[start:end]}


def _make_chunks(source, pages, selector):
    """Build only locally reviewed ranges, never infer arbitrary PDF layout.

    All source-specific page/line selections belong to the private grant. A
    complete requirement, table headers and path note accompany course rows.
    """
    if not isinstance(selector, dict):
        _fail("source_unavailable", "需要本机核对后的章节与表格范围")
    def selected_span(value):
        if (not isinstance(value, dict) or set(value) != {"page", "line_start", "line_end"} or
                any(type(item) is not int for item in value.values()) or value["page"] not in pages):
            _fail("source_unavailable", "本机参考范围定位无效")
        return _span(value["page"], pages[value["page"]], value["line_start"] - 1, value["line_end"])
    try:
        title_span = selected_span(selector["title_span"])
        clause_span = selected_span(selector["requirement_span"])
        note_span = selected_span(selector["path_note_span"])
        header_spans = [selected_span(value) for value in selector["header_spans"]]
        row_spans = [selected_span(value) for value in selector["course_row_spans"]]
        count_spans = [selected_span(value) for value in selector["catalog_count_spans"]]
    except (KeyError, TypeError):
        _fail("source_unavailable", "本机参考范围缺少条款、表头、课程行或附注")
    matched = re.search(r"专业模块课程\s+(\d+(?:\.\d+)?)学分", clause_span["text"])
    if (not matched or "培养方案" not in title_span["text"] or "辅修" not in note_span["text"] or
            not header_spans or any(not span["text"].strip().startswith("课程号") for span in header_spans) or
            DEFERRED.search(note_span["text"]) or DEFERRED.search(clause_span["text"])):
        _fail("source_unavailable", "目标范围角色或普通/暂缓片段边界待核对")
    chunks = []
    def add(kind, title, spans, *, code=None, dependencies=(), scope="ordinary_course_reference"):
        text = "\n".join(span["text"] for span in spans)
        if kind in {"course_row", "table_header"} and DEFERRED.search(text):
            scope = "deferred_second_third_fourth_classroom"
        row = {"source_alias": "EVAL-A", "source_version": source["source_version"],
               "source_sha256": source["pdf_sha256"], "kind": kind, "title": title, "text": text,
               "text_sha256": sha256(text.encode()).hexdigest(), "spans": spans,
               "business_scope": scope, "plan_path": "major", "review_status": REVIEW_STATUS,
               "human_gold": False, "official_rule_enabled": False,
               "chunking_version": CHUNKING_VERSION, "scope_policy_version": SCOPE_VERSION,
               "required_context_ids": list(dependencies)}
        if code is not None:
            row["course_code"] = code
        row["chunk_id"] = "CHUNK-" + _digest(row)
        chunks.append(row)
        return row["chunk_id"]
    add("source_title", "方案标题", [title_span])
    clause_id = add("requirement", "专业模块完整要求", [clause_span])
    note_id = add("path_note", "星号与辅修路径说明", [note_span])
    header_ids = {}
    for span in header_spans:
        header_ids[(span["physical_page"], span["line_start"])] = add("table_header", "课程表表头", [span])
    course_rows = []
    for region in row_spans:
        page = region["physical_page"]
        lines = pages[page].splitlines()
        for position in range(region["line_start"] - 1, region["line_end"]):
            match = COURSE_ROW.match(lines[position])
            if not match:
                continue
            code, name, credits = match.groups()
            preceding = [(p, i) for p, i in header_ids if p < page or (p == page and i <= position)]
            if not preceding:
                _fail("source_unavailable", "课程行缺少已核对表头")
            header_id = header_ids[max(preceding)]
            chunk_id = add("course_row", code + " " + name, [_span(page, pages[page], position, position + 1)],
                           code=code, dependencies=(clause_id, header_id, note_id))
            course_rows.append({"course_code": code, "name_raw": name, "credits_raw": credits,
                                "evidence_chunk_id": chunk_id, "physical_page": page,
                                "relationship_status": REVIEW_STATUS, "required_context_ids": [clause_id, header_id, note_id]})
    catalog_counts = [int(match[1]) for span in count_spans for line in span["text"].splitlines()
                      if (match := re.match(r"^\s*\d+\).*?\s+(\d+)门\s*$", line))]
    if (not course_rows or len({row["course_code"] for row in course_rows}) != len(course_rows) or
            not catalog_counts or sum(catalog_counts) != len(course_rows)):
        _fail("source_unavailable", "模块目录数量与完整课程行不一致，需重新核对版式")
    admitted_pages = {span["physical_page"] for chunk in chunks for span in chunk["spans"]}
    return chunks, {"requirement_credits": matched[1], "catalog_course_count": len(course_rows),
                    "catalog_subgroup_counts": catalog_counts, "course_rows": course_rows,
                    "relationship_status": REVIEW_STATUS, "official_rule_enabled": False,
                    "personal_remaining_credits": None, "source_pages": sorted(admitted_pages)}


class LocalReferenceIndex:
    def __init__(self, delivery_index: Path, grant_path: Path, bundle_path: Path, *, clock=time.time):
        self.delivery_index, self.grant_path, self.bundle_path = map(Path, (delivery_index, grant_path, bundle_path))
        self.clock = clock
        blob = _bytes(self.bundle_path)
        self._bundle_sha256 = sha256(blob).hexdigest()
        self.bundle = _json(self.bundle_path, self._bundle_sha256)
        payload = {key: value for key, value in self.bundle.items() if key != "corpus_version"}
        if self.bundle.get("schema_version") != SCHEMA or self.bundle.get("corpus_version") != _digest(payload):
            _fail("stale_input", "本机参考索引完整性校验失败")
        try:
            self._signing_key = bytes.fromhex(self.bundle["signing_key"])
            if len(self._signing_key) != 32:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            _fail("stale_input", "本机引用签名配置无效")
        self.authorize()

    @classmethod
    def build(cls, delivery_index: Path, grant_path: Path, bundle_path: Path, *, clock=time.time):
        controls = _controls(delivery_index)
        grant = _grant(grant_path, controls, clock)
        requirements = controls["requirements"]
        source_batch = Path(requirements["source_batch"]).resolve()
        output = Path(bundle_path).resolve()
        if (output.exists() or any(output.is_relative_to(base) for base in
                                 (source_batch, controls["dataset_path"].parent, controls["requirements_path"].parent))):
            _fail("inaccessible_resource", "参考索引须另存新私有目录，不能覆盖源或已有版本")
        if "local-only" not in output.parts:
            _fail("inaccessible_resource", "参考索引只能写入本机私有目录")
        file_name, expected = _file_entry(requirements, "plans.json")
        metadata = _json(_child(controls["requirements_path"].parent, file_name), expected)
        if not isinstance(metadata, list):
            _fail("source_unavailable", "培养目录结构无效")
        blueprint = _json(controls["delivery"]["rag_blueprint"])
        selected = blueprint.get("source_registry", {}).get("EVAL-A", {})
        registry = []
        selected_pages = None
        for meta in metadata:
            pdf_path, text_path = Path(meta["pdf_path"]).resolve(), Path(meta["text_path"]).resolve()
            if not pdf_path.is_relative_to(source_batch) or not text_path.is_relative_to(source_batch):
                _fail("inaccessible_resource", "培养来源不在获准批次范围")
            conflicts = {item["page"] for item in meta.get("conflicting_cohort_mentions", [])}
            is_selected = str(meta["plan_number"]) == str(selected.get("plan_number"))
            if is_selected and meta["pdf_sha256"] != selected.get("pdf_sha256"):
                _fail("stale_input", "候选方案与已登记PDF不一致")
            pages = None
            if is_selected:
                _bytes(pdf_path, meta["pdf_sha256"])
                pages = _pages(_bytes(text_path, meta["text_sha256"]).decode("utf-8"))
                if len(pages) != meta["page_count"]:
                    _fail("stale_input", "培养页数与来源登记不一致")
            record = {"source_alias": "EVAL-A" if is_selected else "CATALOG-" + meta["pdf_sha256"][:20],
                      "source_version": requirements["source_digest"], "pdf_sha256": meta["pdf_sha256"],
                      "text_sha256": meta["text_sha256"], "pdf_path": str(pdf_path), "text_path": str(text_path),
                      "cohort": meta["cohort_from_directory"], "program_name": meta["major_name_raw"],
                      "page_count": meta["page_count"], "page_states": {str(page): "conflicting" if page in conflicts else "pending_human_review"
                                                                          for page in range(1, meta["page_count"] + 1)},
                      "hash_verification": "verified_this_build" if is_selected else "inventory_hash_recorded_not_rechecked",
                      "personal_applicability": "unknown", "official_effective_version": "unknown"}
            registry.append(record)
            if is_selected:
                if selected_pages is not None:
                    _fail("source_unavailable", "目标方案登记重复")
                selected_pages = pages
        if selected_pages is None:
            _fail("source_unavailable", "目标方案未在本机目录中登记")
        source = next(row for row in registry if row["source_alias"] == "EVAL-A")
        chunks, module = _make_chunks(source, selected_pages, grant.get("curriculum_selector"))
        module["assistant_visual_review_id"] = grant.get("assistant_review_id")
        module["assistant_visual_reviewed_pages"] = grant.get("assistant_reviewed_pages", {}).get("EVAL-A", [])
        bundle = {"schema_version": SCHEMA, "dataset_version": controls["dataset"]["dataset_version"],
                  "source_version": requirements["source_digest"],
                  "dataset_manifest_sha256": controls["dataset_manifest_sha256"],
                  "requirements_manifest_sha256": controls["requirements_manifest_sha256"],
                  "blueprint_sha256": sha256(_bytes(controls["delivery"]["rag_blueprint"])).hexdigest(),
                  "registry": registry, "chunks": chunks, "module": module,
                  "index_version": CHUNKING_VERSION, "scope_policy_version": SCOPE_VERSION,
                  "selector_sha256": _digest(grant["curriculum_selector"]), "signing_key": secrets.token_hex(32),
                  "human_gold_count": 0, "model_context_allowed": False, "cache_enabled": False}
        bundle["corpus_version"] = _digest(bundle)
        # Recheck live authorization immediately before committing the new file.
        current_controls = _controls(delivery_index)
        current_grant = _grant(grant_path, current_controls, clock)
        if (_digest(current_grant) != _digest(grant) or
                current_controls["dataset_manifest_sha256"] != controls["dataset_manifest_sha256"] or
                current_controls["requirements_manifest_sha256"] != controls["requirements_manifest_sha256"]):
            _fail("stale_input", "构建期间来源或授权版本已变化")
        _bytes(source["pdf_path"], source["pdf_sha256"])
        _bytes(source["text_path"], source["text_sha256"])
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        except OSError:
            _fail("source_unavailable", "无法另存本机参考索引")
        return output

    def _live(self):
        controls = _controls(self.delivery_index)
        grant = _grant(self.grant_path, controls, self.clock)
        _bytes(self.bundle_path, self._bundle_sha256)
        if (controls["dataset_manifest_sha256"] != self.bundle["dataset_manifest_sha256"] or
                controls["requirements_manifest_sha256"] != self.bundle["requirements_manifest_sha256"] or
                controls["dataset"]["dataset_version"] != self.bundle["dataset_version"] or
                controls["requirements"]["source_digest"] != self.bundle["source_version"] or
                _digest(grant.get("curriculum_selector")) != self.bundle["selector_sha256"]):
            _fail("stale_input", "本机参考索引与当前来源版本不一致")
        metadata = _selected_metadata(controls, self.bundle["blueprint_sha256"])
        selected = [row for row in self.bundle["registry"] if row["source_alias"] == "EVAL-A"]
        if len(selected) != 1:
            _fail("stale_input", "本机参考索引缺少唯一来源绑定")
        record = selected[0]
        conflicts = {item["page"] for item in metadata.get("conflicting_cohort_mentions", [])}
        expected_pages = {str(page): "conflicting" if page in conflicts else "pending_human_review"
                          for page in range(1, metadata["page_count"] + 1)}
        if (any(record[key] != metadata[key] for key in ("pdf_sha256", "text_sha256", "page_count")) or
                any(Path(record[key]).resolve() != Path(metadata[key]).resolve() for key in ("pdf_path", "text_path")) or
                record["page_states"] != expected_pages or record["cohort"] != metadata["cohort_from_directory"] or
                record["program_name"] != metadata["major_name_raw"]):
            _fail("stale_input", "本机参考索引的来源与获准目录不同")
        _bytes(record["pdf_path"], record["pdf_sha256"])
        pages = _pages(_bytes(record["text_path"], record["text_sha256"]).decode("utf-8"))
        authoritative = {**record, "source_version": controls["requirements"]["source_digest"]}
        chunks, module = _make_chunks(authoritative, pages, grant["curriculum_selector"])
        if (chunks != self.bundle["chunks"] or any(module[key] != self.bundle["module"].get(key) for key in module)):
            _fail("stale_input", "参考块或课程关系与获准范围不一致")
        return {"grant_version": _digest(grant), "grant": grant, "controls": controls}

    def authorize(self):
        live = self._live()
        return {"grant_version": live["grant_version"],
                "versions": {key: self.bundle[key] for key in ("dataset_version", "source_version", "corpus_version",
                                                                "index_version", "scope_policy_version")},
                "scope": {"source_aliases": ["EVAL-A"], "purpose": "local_reference_review", "plan_path": "major",
                          "target_term": live["grant"]["target_term"]}}

    @property
    def course_codes(self):
        live = self._live()
        by_id = {chunk["chunk_id"]: chunk for chunk in self.bundle["chunks"]}
        return [row["course_code"] for row in self.bundle["module"]["course_rows"]
                if all(self._allowed(by_id[key], live["grant"]) for key in
                       [row["evidence_chunk_id"], *row["required_context_ids"]])]

    def describe(self):
        live = self._live()
        module = {key: deepcopy(value) for key, value in self.bundle["module"].items() if key != "course_rows"}
        return {"status": "pending_human_review", "security_realm": "restricted_reference", "mode": "local_reference_only",
                "scope": {"source_aliases": ["EVAL-A"], "purpose": "local_reference_review", "plan_path": "major",
                          "target_term": live["grant"]["target_term"]},
                "versions": {key: self.bundle[key] for key in ("dataset_version", "source_version", "corpus_version",
                                                                "index_version", "scope_policy_version")} | {
                    "grant_version": live["grant_version"]},
                "source_count": len(self.bundle["registry"]), "admitted_source_count": 1,
                "chunk_count": len(self.bundle["chunks"]), "module": module,
                "human_gold_count": 0, "model_context_allowed": False, "cache_enabled": False,
                "warnings": ["助手核对不等于人工gold或官方政策审核。", "参考选择不建立本人方案绑定。"]}

    def shared_components(self):
        live = self._live()
        result = {}
        for name in COMPONENTS:
            file_name, expected = _file_entry(live["controls"]["dataset"], name)
            if file_name != name + ".json":
                _fail("inaccessible_resource", "共享组件不能改指个人或其他文件")
            result[name] = _json(_child(live["controls"]["dataset_path"].parent, file_name), expected)
        if self.authorize()["grant_version"] != live["grant_version"]:
            _fail("inaccessible_resource", "参考授权已变化")
        return result

    def _allowed(self, chunk, grant):
        if (chunk["source_alias"] not in grant["source_aliases"] or chunk["plan_path"] != grant["plan_path"] or
                chunk["business_scope"].startswith("deferred")):
            return False
        source = next(row for row in self.bundle["registry"] if row["source_alias"] == chunk["source_alias"])
        allowed = grant.get("allowed_pages", {}).get(chunk["source_alias"], self.bundle["module"]["source_pages"])
        return all(span["physical_page"] in allowed and source["page_states"].get(str(span["physical_page"])) != "conflicting"
                   for span in chunk["spans"])

    def _verify(self, chunk):
        source = next(row for row in self.bundle["registry"] if row["source_alias"] == chunk["source_alias"])
        pages = _pages(_bytes(source["text_path"], source["text_sha256"]).decode("utf-8"))
        if chunk["source_sha256"] != source["pdf_sha256"] or chunk["source_version"] != self.bundle["source_version"]:
            _fail("stale_input", "引用来源版本无效")
        texts = []
        for span in chunk["spans"]:
            page = pages.get(span["physical_page"])
            if (page is None or sha256(page.encode()).hexdigest() != span["page_text_sha256"] or
                    page[span["char_start"]:span["char_end"]] != span["text"]):
                _fail("stale_input", "引用片段与物理页不一致")
            texts.append(span["text"])
        if "\n".join(texts) != chunk["text"] or sha256(chunk["text"].encode()).hexdigest() != chunk["text_sha256"]:
            _fail("stale_input", "引用片段摘要不一致")

    def _view(self, chunk, live):
        by_id = {item["chunk_id"]: item for item in self.bundle["chunks"]}
        context = [by_id[key] for key in chunk["required_context_ids"]]
        if any(not self._allowed(item, live["grant"]) for item in [chunk, *context]):
            _fail("inaccessible_resource", "引用及必要上下文不在获准范围")
        for item in [chunk, *context]:
            self._verify(item)
        reference = self._reference(chunk, live)
        safe = lambda item: {key: deepcopy(item[key]) for key in
                             ("title", "text", "kind", "review_status", "business_scope", "text_sha256", "spans")}
        return {"reference": reference, **safe(chunk), "source_alias": chunk["source_alias"],
                "source_sha256": chunk["source_sha256"], "physical_pages": sorted({span["physical_page"]
                    for item in [chunk, *context] for span in item["spans"]}),
                "context": [safe(item) for item in context], "official_rule_enabled": False}

    def _reference(self, chunk, live):
        message = _digest({"chunk_id": chunk["chunk_id"], "corpus_version": self.bundle["corpus_version"],
                           "grant_version": live["grant_version"]}).encode()
        signature = hmac.new(self._signing_key, message, "sha256").digest()
        return "LOCAL-REF-" + base64.urlsafe_b64encode(signature).decode().rstrip("=")

    def search(self, query, *, mode="L1", limit=5):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 400 or mode not in {"L0", "L1"} or type(limit) is not int or not 1 <= limit <= 10:
            _fail("invalid_query", "查询、词法模式或数量无效")
        live = self._live()
        summary = self.describe()
        if DEFERRED.search(query):
            return {**summary, "status": "deferred_by_business_scope", "matches": [], "retrieval_mode": mode}
        tokens = terms(query)
        exact = {value.upper() for value in re.findall(r"[A-Za-z]{2,}\d{3,}[A-Za-z0-9]*", query)}
        ranked = []
        for chunk in self.bundle["chunks"]:
            if not self._allowed(chunk, live["grant"]):
                continue
            # Context eligibility precedes scoring, just as body eligibility does.
            by_id = {item["chunk_id"]: item for item in self.bundle["chunks"]}
            if any(not self._allowed(by_id[key], live["grant"]) for key in chunk["required_context_ids"]):
                continue
            code_match = bool(exact and chunk.get("course_code") in exact)
            if exact and not code_match:
                continue
            text = chunk["title"] + "\n" + chunk["text"]
            lexical = len(tokens & terms(text)) if mode == "L1" else sum(word in text.lower() for word in query.lower().split())
            score = (1000 if code_match else 0) + lexical
            if score:
                ranked.append((score, chunk["chunk_id"], chunk))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        matches = [{**self._view(chunk, live), "score": score} for score, _, chunk in ranked[:limit]]
        if self.authorize()["grant_version"] != live["grant_version"]:
            _fail("inaccessible_resource", "参考授权已变化")
        return {**summary, "status": "pending_human_review" if matches else "no_supported_answer_in_scope",
                "matches": matches, "retrieval_mode": mode,
                "answer_support_verified": False, "search_candidate_count": len(ranked)}

    def resolve(self, reference):
        live = self._live()
        if not isinstance(reference, str) or not re.fullmatch(r"LOCAL-REF-[A-Za-z0-9_-]{43}", reference):
            _fail("inaccessible_resource", "参考引用不可访问")
        chunk = next((row for row in self.bundle["chunks"] if self._allowed(row, live["grant"]) and
                      hmac.compare_digest(reference, self._reference(row, live))), None)
        if chunk is None:
            _fail("inaccessible_resource", "参考引用不可访问")
        result = self._view(chunk, live)
        result["reference"] = reference
        if self.authorize()["grant_version"] != live["grant_version"]:
            _fail("inaccessible_resource", "参考授权已变化")
        return result
