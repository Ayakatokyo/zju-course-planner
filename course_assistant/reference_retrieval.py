"""Offline, read-only reference slice. Not a personal/RAG/Agent runtime tool.

Only the manifest-checked handoff JSON is admitted. An explicit plan is a
developer reference selection, not a verified student binding or permission.
No models, network, persistence, cache or active planning workspace are used.
"""
from __future__ import annotations

import argparse
import copy
from hashlib import sha256
import json
import re
import unicodedata

from .importer import SOURCE_ROOT, digest, read_sources, require

MAPPING_VERSION = "reference-json-v1"
RETRIEVAL_VERSION = "keyword-latin-han-bigram-v1"


def terms(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    words = set(re.findall(r"[a-z0-9]+", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            words.add(run)
    return words


class ReferenceIndex:
    """Immutable projection rebuilt from checked source; no official efficacy."""

    def __init__(self, root=SOURCE_ROOT):
        source = read_sources(root)
        self._plans = {p["plan_id"]: p for p in source["plans"]}
        aliases = {p["original_file_name"]: p for p in source["source_aliases"]}
        self._chunks = {}
        self._records = {}
        self._issues = source["issues"]
        filenames = {"courses": "course_samples", "candidates": "requirement_candidates",
                     "issues": "source_issues"}
        id_keys = {"courses": "sample_id", "candidates": "rule_id", "issues": "issue_id"}
        for kind in filenames:
            for ordinal, row in enumerate(source[kind]):
                plans = row["plan_ids"] if kind == "issues" else [row["plan_id"]]
                refs = row["evidence"] if kind == "issues" else row["sources"]
                pdf_evidence = [{"source_id": aliases[r["file_name"]]["source_id"],
                                 "sha256": r["sha256"], "pdf_pages": r["pdf_pages"]}
                                for r in refs]
                if kind == "courses":
                    title = row["course_code"] + " " + row["course_name"]
                    text = "\n".join(r.get("excerpt", "") for r in refs)
                    role = "curriculum_course_reference"
                elif kind == "candidates":
                    title, text, role = row["title"], "\n".join(row["source_quotes"]), "candidate_basis"
                else:
                    title = row["title"]
                    text = "\n".join(r.get("excerpt", "") for r in refs)
                    # Missing-source issues cite the JSON diagnosis, not a made-up PDF.
                    text = text or row["impact"]
                    role = "diagnostic"
                locator = {"json_file": f"data/{filenames[kind]}.json",
                           "json_pointer": f"/records/{ordinal}"}
                chunk = {
                    "record_id": row[id_keys[kind]], "record_kind": kind,
                    "plan_ids": plans, "cohorts": [self._plans[p]["cohort"] for p in plans],
                    "title": title, "text": text, "text_role": role,
                    "text_sha256": sha256(text.encode("utf-8")).hexdigest(), "raw_record_sha256": digest(row),
                    "locator": locator, "pdf_evidence": pdf_evidence,
                    "locator_precision": "json_record_and_source_pdf_page_set",
                    "source_manifest_sha256": source["integrity"]["manifest_sha256"],
                    "mapping_version": MAPPING_VERSION, "data_scope": "shared",
                    "usage_scope": "authorized_local_review_only", "owner_student_id": None,
                    "security_realm": "restricted_reference", "data_kind": "observed_sample",
                    "official_status": "unconfirmed", "observed_at": None,
                    "review_status": row.get("status", "source_listing_reference_only"),
                    "enforce_automatically": False, "completeness": "partial",
                    "field_states": {"observed_at": "unknown", "personal_applicability": "unknown",
                                     "official_effective_version": "pending_verification"},
                }
                key = "REF-" + digest(chunk)
                chunk["chunk_id"] = key
                self._chunks[key] = chunk
                self._records[key] = copy.deepcopy(row)
        self._version = digest({"mapping": MAPPING_VERSION, "chunks": self._chunks})

    @property
    def corpus_version(self):
        return self._version

    def _check_version(self, expected):
        require(expected == self._version, "参考语料版本变化，请重新取得语料版本")

    def describe(self):
        return {"corpus_version": self._version, "mapping_version": MAPPING_VERSION,
                "retrieval_version": RETRIEVAL_VERSION, "chunk_count": len(self._chunks),
                "counts": {kind: sum(c["record_kind"] == kind for c in self._chunks.values())
                           for kind in ("courses", "candidates", "issues")},
                "plan_ids": sorted(self._plans), "mode": "offline_reference_only",
                "coverage": "partial_handoff_json_not_pdf_full_text",
                "personal_data_indexed": False, "model_configured": False, "cache_enabled": False}

    def search(self, query, *, plan_id, expected_corpus_version, limit=5):
        self._check_version(expected_corpus_version)
        require(isinstance(query, str) and 0 < len(query.strip()) <= 400, "查询需为 1—400 字符")
        require(type(limit) is int and 1 <= limit <= 10, "limit 需为 1—10 整数")
        require(plan_id is None or isinstance(plan_id, str), "参考方案需为字符串或 null")
        result = {**self.describe(), "scope": {"plan_id": plan_id, "purpose": "reference_review"},
                  "matches": [], "plan_open_issues": [],
                  "warnings": ["参考选择不是本人适用绑定；候选不执行；不是当前开课或学业事实。"]}
        if plan_id not in self._plans:
            return {**result, "status": "needs_scope"}
        query_terms = terms(query)
        ranked = []
        for chunk in self._chunks.values():
            # Metadata eligibility precedes text tokenization and scoring.
            if plan_id not in chunk["plan_ids"]:
                continue
            score = len(query_terms & terms(chunk["title"] + "\n" + chunk["text"]))
            if score:
                ranked.append((score, chunk["chunk_id"]))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        result["matches"] = [{**copy.deepcopy(self._chunks[key]), "score": score}
                             for score, key in ranked[:limit]]
        result["plan_open_issues"] = [
            {"issue_id": r["issue_id"], "title": r["title"], "status": r["status"]}
            for r in self._issues if plan_id in r["plan_ids"]]
        result["status"] = "ok" if ranked else "no_match_in_scope"
        return result

    def resolve(self, chunk_id, *, plan_id, expected_corpus_version):
        self._check_version(expected_corpus_version)
        require(isinstance(chunk_id, str), "引用需为字符串")
        require(plan_id is None or isinstance(plan_id, str), "参考方案需为字符串或 null")
        chunk = self._chunks.get(chunk_id)
        require(chunk is not None and plan_id in chunk["plan_ids"], "参考引用不可用")
        require(digest(self._records[chunk_id]) == chunk["raw_record_sha256"], "参考记录摘要不一致")
        return {"chunk": copy.deepcopy(chunk), "raw_record": copy.deepcopy(self._records[chunk_id]),
                "corpus_version": self._version, "note": "只回查本机参考记录，不证明官方生效或个人适用。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="明确选择参考方案，不绑定学生身份")
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    index = ReferenceIndex()
    result = index.search(args.query, plan_id=args.plan, expected_corpus_version=index.corpus_version)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
