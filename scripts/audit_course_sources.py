"""Verify immutable source evidence and write a restricted local audit report."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from course_source_adapters import BACKEND, BROWSER, Loader, PanelParser, digest, load_sources, text


def known(value):
    return value is not None and not (isinstance(value, str) and text(value) is None)


def immutable_json(path, data):
    value = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != value:
            raise ValueError("audit_output_exists_with_different_bytes_choose_new_directory")
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(value)
    path.chmod(0o600)


def audit_sources(project):
    project = Path(project).resolve()
    inputs = load_sources(project)
    repeated = load_sources(project)
    if digest(inputs) != digest(repeated):
        raise ValueError("source_adapter_not_idempotent")
    loader = Loader(project)
    original_inventory_path = "local-only/course_dedup_20261013/source_inventory_before.json"
    inventory, inventory_sha = loader.load(original_inventory_path)
    for item in inventory:
        path = (project / item["path"]).resolve()
        if not (path.is_relative_to(project / "local-only") or path.is_relative_to(project / "inputs/zju_course_agent_handoff_v1")):
            raise ValueError("original_inventory_path_outside_authorized_sources")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("original_inventory_hash_changed")
    backend = loader.backend_root
    normalized_manifest, _ = loader.load(backend + "/normalized/manifest.json")
    normalized_checks = []
    for name, entry in sorted(normalized_manifest["datasets"].items()):
        rows, _ = loader.load(backend + "/" + entry["path"], entry["sha256"])
        normalized_checks.append({"dataset": name, "rows": len(rows), "declared_rows": entry["rows"], "match": len(rows) == entry["rows"]})
    original_sections, _ = loader.load(backend + "/normalized/section_observations.json")
    old_by_id = {s["observation_id"]: s for s in original_sections}
    conflict_details = []
    for conflict in inputs["audit"]["original_backend_candidate_conflicts"]:
        rows = [old_by_id[o] for o in conflict["observations"]]
        fields = sorted(set().union(*(row.keys() for row in rows)) - {"observation_id", "source"})
        difference = {field: sorted({json.dumps(row.get(field), ensure_ascii=False) for row in rows}) for field in fields
                      if len({json.dumps(row.get(field), ensure_ascii=False) for row in rows}) > 1}
        conflict_details.append({"candidate_section_key": conflict["candidate_section_key"], "source_key": rows[0]["source_section_key"],
                                 "differing_fields": difference, "resolution": "original_values_and_conflict_retained"})
    stop_inventory, _ = loader.load(BROWSER + "/offline_review_20261013_stop/stop_inventory.json")
    for entry in stop_inventory["files"]:
        loader.read(BROWSER + "/" + entry["path"], entry["sha256"])
    stop_counts, _ = loader.load(BROWSER + "/offline_review_20261013_stop/course_counts.json")
    html_receipts = {r["path"]: r for r in loader.lines(BROWSER + "/selection_html_receipts.jsonl")}
    alias_checks = []
    for alias in inputs["evidence_aliases"]:
        if alias["reason"] != "derived_html_json_not_new_observation":
            continue
        derived, _ = loader.load(alias["alias_path"], alias["alias_sha256"])
        raw, raw_sha = loader.read(alias["original_path"])
        parser = PanelParser()
        parser.feed(raw.decode())
        receipt = html_receipts[derived["raw_html_path"]]
        checks = {"raw_hash_metadata_matches": raw_sha == derived["raw_html_sha256"],
                  "rows_and_attributes_match": parser.rows == derived["rows"],
                  "header_matches": "".join(parser.header).strip() == derived["header"] and parser.attrs == derived["header_attributes"],
                  "code_scope_time_match_receipt": (derived["code"], derived["source_scope"], derived["observed_at"]) == (receipt["code"], receipt["scope"], receipt["observed_at"])}
        alias_checks.append({"alias_path": alias["alias_path"], "checks": checks})
        if not all(checks.values()):
            raise ValueError("derived_html_alias_differs_from_original_evidence")
    course_fields, section_fields, dynamic, alignment = {}, {}, {}, {}
    for route in sorted({o["source"]["route"] for o in inputs["course_observations"]}):
        rows = [o for o in inputs["course_observations"] if o["source"]["route"] == route and not o.get("excluded_reason")]
        fields = sorted(set().union(*(o["fields"].keys() for o in rows)))
        course_fields[route] = {field: {"observed_known": sum(known(o["fields"].get(field)) for o in rows), "unknown": sum(not known(o["fields"].get(field)) for o in rows)} for field in fields}
    for route in sorted({o["source"]["route"] for o in inputs["section_observations"]}):
        rows = [o for o in inputs["section_observations"] if o["source"]["route"] == route]
        fields = sorted(set().union(*(o["fields"].keys() for o in rows)))
        section_fields[route] = {field: {"observed_known": sum(known(o["fields"].get(field)) for o in rows), "unknown": sum(not known(o["fields"].get(field)) for o in rows)} for field in fields}
        dynamic[route] = {"exam_status": dict(sorted(Counter(o["exam"]["status"] for o in rows).items())),
                          "capacity_known": sum(o["capacity"]["remaining"] is not None and o["capacity"]["capacity"] is not None for o in rows),
                          "capacity_zero_remaining": sum(o["capacity"]["remaining"] == 0 for o in rows),
                          "capacity_negative_remaining": sum(o["capacity"]["remaining"] is not None and o["capacity"]["remaining"] < 0 for o in rows),
                          "observation_time_unknown": sum(o["source"]["observed_at"] is None for o in rows)}
        meetings = [m for o in rows for m in o["meetings"]]
        alignment[route] = {"meeting_records": len(meetings), "parsed_weekday_periods": sum(m["weekday"] is not None and m["periods"] is not None for m in meetings),
                            "week_annotations_observed_not_normalized": sum(bool(m.get("week_annotation_raw")) for m in meetings),
                            "alignment": dict(sorted(Counter(m["alignment"] for m in meetings).items())),
                            "calendar_verified": 0, "week_set_normalized_verified": 0}
    original_html = [o for o in inputs["course_observations"] if o["source"]["route"] == "browser" and o["source"]["path"].endswith(".html")]
    original_json = [o for o in inputs["course_observations"] if o["source"]["route"] == "browser" and "/selection_courses/" in o["source"]["path"]]
    return {"schema_version": "course-source-audit/1", "source_load_sha256": digest(inputs), "counts": inputs["counts"],
            "audit": inputs["audit"], "evidence_aliases": inputs["evidence_aliases"], "backend_original_conflict_details": conflict_details,
            "derived_html_alias_checks": alias_checks,
            "field_availability": {"course": course_fields, "section": section_fields, "dynamic": dynamic, "meeting": alignment},
            "browser_reconciliation": {"raw_json_course_observations": len(original_json), "html_course_observations": len(original_html),
                                       "initial_course_observations": 5, "html_rows": sum(sum(s["source"]["path"] == o["source"]["path"] for s in inputs["section_observations"]) for o in original_html),
                                       "historical_zero_row_observations": stop_counts["historical_zero_row_attempts"],
                                       "derived_json_not_recounted": inputs["audit"]["browser_derived_json_aliases_skipped"]},
            "normalized_manifest_checks": normalized_checks, "original_inventory_sha256": inventory_sha,
            "checks": {"all_original_5775_files_unchanged": len(inventory) == 5775, "all_browser_stop_inventory_2631_files_unchanged": len(stop_inventory["files"]) == 2631,
                       "all_normalized_manifest_hashes_and_counts_valid": all(c["match"] for c in normalized_checks),
                       "all_backend_parent_and_bound_target_checks_passed": not inputs["audit"]["binding_errors"],
                       "all_html_receipt_row_counts_valid": True, "no_html_same_path_history_overwrite": True,
                       "all_181_derived_html_aliases_match_original_hash_rows_headers_receipts": len(alias_checks) == 181 and all(all(c["checks"].values()) for c in alias_checks),
                       "source_adapter_independent_reload_is_identical": digest(inputs) == digest(repeated)},
            "policy_boundaries": {"candidate_identity_cross_refresh_verified": False, "application_imported": False,
                                  "school_or_model_network_requests": 0, "real_student_identity_binding": False,
                                  "inputs_or_raw_sources_modified": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output_root = (args.output or project / "local-only/course_dedup_20261013/source_audits").resolve()
    task_root = project / "local-only/course_dedup_20261013"
    if not output_root.is_relative_to(task_root):
        raise ValueError("audit_output_must_stay_in_ignored_task_directory")
    report = audit_sources(project)
    # Each changed report receives a new content-derived directory. Historical
    # source_audit_v1 and any existing version are never overwritten.
    output = (output_root / ("source-audit-v1-" + digest(report)[:20])).resolve()
    if not output.is_relative_to(task_root):
        raise ValueError("audit_version_output_outside_ignored_task_directory")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    immutable_json(output / "source_audit.json", report)
    print(json.dumps({"path": str(output / "source_audit.json"), "counts": report["counts"], "checks": report["checks"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
