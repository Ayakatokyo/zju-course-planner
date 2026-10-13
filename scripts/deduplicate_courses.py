"""Reproducible offline course evidence merge; never accesses school/model services.

Source adapters retain raw evidence. This module resolves only compatible known
static assertions and keeps time-dependent records as indivisible observations.
The resulting restricted-reference data is not an application import bundle.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

VERSION = "course-offline-dedup/1"
NAMESPACE = "zju_undergraduate_selection_primary_code"
UNKNOWN = {"", "--", "undefined", "null", "待定", "待确认"}
COURSE_FIELDS = ("name", "display_code", "credits", "hours", "college", "prerequisite_hint", "term_part")
SECTION_FIELDS = ("teacher_names", "term_part", "hours", "instruction_mode", "teaching_form", "target", "international", "textbook")
PERSONAL_FIELD_NAMES = {"kcxx", "course_info_raw", "raw_credit_encoding", "kcxzzt", "tsbj", "xkzy", "sfxz",
                        "page_selected_raw", "selection_status_raw", "page_status_raw", "page_pending_counts_raw",
                        "wish_raw", "student_id", "owner_student_id", "personal"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def known(value):
    # Zero, False and explicit empty collections are values, never missing.
    return value is not None and not (isinstance(value, str) and value.strip() in UNKNOWN)


def decimal_text(value):
    if not known(value) or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
        return format(d.normalize(), "f") if d.is_finite() else None
    except InvalidOperation:
        return None


def field_value(name, value):
    if isinstance(value, dict) and "state" in value and "value" in value:
        value = value["value"] if value["state"] == "known" else None
    if name == "credits":
        return decimal_text(value)
    return value


def summarize_fields(observations, names):
    """Compatible evidence union; conflicting known assertions stay unresolved."""
    result = {}
    for name in sorted(set(names)):
        values = {}
        missing = []
        for o in observations:
            value = field_value(name, o.get("fields", {}).get(name))
            if not known(value):
                missing.append(o["id"])
                continue
            key = canonical(value)
            values.setdefault(key, {"value": value, "observation_ids": []})["observation_ids"].append(o["id"])
        assertions = [values[k] for k in sorted(values)]
        for a in assertions:
            a["observation_ids"] = sorted(a["observation_ids"])
        state = "unknown" if not assertions else "known" if len(assertions) == 1 else "conflicting"
        result[name] = {"state": state, "value": assertions[0]["value"] if state == "known" else None,
                        "assertions": assertions, "unknown_observation_ids": sorted(missing)}
    return result


def section_identity(o):
    code, term, key = o.get("code"), o.get("term"), o.get("source_key")
    if not code:
        raise ValueError("section_without_bound_course")
    if key:
        match = re.match(r"^\((\d{4}-\d{4}-\d+)\)-", key)
        if match:
            key_term = match[1]
            if term is not None and term != key_term:
                raise ValueError("source_key_term_conflict")
            term = key_term
            if not key.startswith(f"({key_term})-{code}-"):
                raise ValueError("source_key_course_conflict")
    # Unknown identity/context never operates as a wildcard across observations.
    return (NAMESPACE, code, term or "unknown:" + o["id"], key or "unknown:" + o["id"])


def unique_observations(rows):
    result = {}
    for row in rows:
        if not row.get("id"):
            raise ValueError("missing_observation_id")
        if row["id"] in result and result[row["id"]] != row:
            raise ValueError("conflicting_observation_id")
        result[row["id"]] = row
    return [result[k] for k in sorted(result)]


def check_shared_fields(value):
    if isinstance(value, dict):
        if PERSONAL_FIELD_NAMES.intersection(value):
            raise ValueError("personal_field_in_shared_projection")
        for child in value.values():
            check_shared_fields(child)
    elif isinstance(value, list):
        for child in value:
            check_shared_fields(child)


def build_dataset(inputs):
    course_obs = unique_observations(inputs["course_observations"])
    section_obs = unique_observations(inputs["section_observations"])
    business = [o for o in course_obs if not o.get("excluded_reason")]
    excluded = [o for o in course_obs if o.get("excluded_reason")]
    for o in business:
        check_shared_fields(o["fields"])
        check_shared_fields(o.get("scoped_fields", {}))
    for o in section_obs:
        check_shared_fields(o["fields"])
    by_code, by_section = defaultdict(list), defaultdict(list)
    for o in business:
        if not o.get("code"):
            raise ValueError("course_without_code")
        by_code[o["code"]].append(o)
    for o in section_obs:
        if o.get("excluded_reason"):
            raise ValueError("excluded_section_requires_separate_adapter_partition")
        by_section[section_identity(o)].append(o)
        if o["code"] not in by_code:
            raise ValueError("orphan_section_course")
    courses, sections, conflicts, unknowns = [], [], [], []
    course_ids = {code: digest([NAMESPACE, code]) for code in by_code}

    def issues(entity_id, summaries):
        for field, info in summaries.items():
            if info["state"] == "conflicting":
                conflicts.append({"entity_id": entity_id, "field": field,
                                  "assertions": info["assertions"], "resolution": "preserve_pending_review"})
            if info["unknown_observation_ids"]:
                unknowns.append({"entity_id": entity_id, "field": field,
                                 "observation_ids": info["unknown_observation_ids"],
                                 "state": info["state"], "reason": "see_original_field_and_source_adapter_state"})

    for code, rows in sorted(by_code.items()):
        # Keep definition terms separate; entity summaries are evidence aids,
        # not a claim that all fields coexisted in an official snapshot.
        definitions = []
        terms = defaultdict(list)
        for o in rows:
            terms[o.get("term") or "unknown:" + o["id"]].append(o)
        for term, observations in sorted(terms.items()):
            names = set(COURSE_FIELDS).union(*(o.get("fields", {}).keys() for o in observations))
            summaries = summarize_fields(observations, names)
            definition_id = digest([course_ids[code], term])
            issues(definition_id, summaries)
            definitions.append({"definition_id": definition_id, "term": term,
                                "fields": summaries, "observation_ids": sorted(o["id"] for o in observations),
                                "projection_kind": "field_evidence_union_not_observed_snapshot"})
        courses.append({"course_id": course_ids[code], "code": code, "code_namespace": NAMESPACE,
                        "stable_course_identity_verified": False,
                        "definitions": definitions, "observation_ids": sorted(o["id"] for o in rows)})

    meetings, meeting_sets, exams, capacities, personals = [], [], [], [], []
    section_public_obs, source_registry, course_public_obs = [], [], []
    membership_groups = defaultdict(list)
    for o in course_obs:
        source_registry.append({"observation_id": o["id"], "source": o["source"],
                                "context": o.get("context"), "context_id": digest(o.get("context")),
                                "type": "course", "excluded_reason": o.get("excluded_reason")})
        personal = o.get("personal", {})
        if personal:
            personals.append({"id": digest([o["id"], "personal"]), "observation_id": o["id"],
                              "scope": "course_header", "fields": personal,
                              "application_verified_identity_binding": False, "owner_student_id": None,
                              "formal_enrollment": None, "earned_credit": None})
        if o.get("excluded_reason"):
            continue
        course_public_obs.append({"id": o["id"], "course_id": course_ids[o["code"]], "term": o.get("term"),
                                  "fields": o["fields"], "scoped_fields": o.get("scoped_fields", {})})
        # Context is part of the key. Partial browser context cannot overwrite
        # verified backend filter context or imply applicability to a student.
        context = o.get("context") or {"unknown_context_observation": o["id"]}
        identity = [course_ids[o["code"]], o.get("term"), o.get("classification"), context]
        membership_groups[canonical(identity)].append(o)
    memberships = []
    for key, rows in sorted(membership_groups.items()):
        o = rows[0]
        memberships.append({"membership_id": digest(json.loads(key)), "course_id": course_ids[o["code"]],
                            "term": o.get("term"), "classification": o.get("classification"),
                            "context_ref": digest(o.get("context")), "observation_ids": sorted(r["id"] for r in rows),
                            "plan_requiredness": None, "student_applicability_verified": False})
    section_id_by_obs = {}
    for identity, rows in sorted(by_section.items()):
        sid = digest(identity)
        names = set(SECTION_FIELDS).union(*(o.get("fields", {}).keys() for o in rows))
        fields = summarize_fields(rows, names)
        issues(sid, fields)
        schedule_variants = defaultdict(list)
        for o in rows:
            section_id_by_obs[o["id"]] = sid
            # Keep the entire timetable observation together, including unknown
            # empty text. Do not create a merged timetable from multiple times.
            packet = o.get("meetings")
            schedule_variants[canonical({"meetings": packet, "declared_state": o.get("meeting_state")})].append(o["id"])
            source_registry.append({"observation_id": o["id"], "type": "section", "source": o["source"],
                                    "context": o.get("context"), "context_id": digest(o.get("context"))})
            mids = []
            for index, meeting in enumerate(packet or []):
                mid = digest([o["id"], "meeting", index])
                mids.append(mid)
                meetings.append({"id": mid, "section_id": sid, "observation_id": o["id"], "index": index,
                                 "fields": meeting, "calculator_ready": False})
            meeting_status = "known_empty" if packet == [] and o.get("meeting_state") == "known_empty" else "partial" if packet else "unknown_not_confirmed_empty"
            meeting_sets.append({"id": digest([o["id"], "meeting_set"]), "section_id": sid,
                                 "observation_id": o["id"], "meeting_ids": mids,
                                 "declared_state": o.get("meeting_state"), "status": meeting_status})
            observed_at = o["source"].get("observed_at")
            for kind, values, bucket in (("exam", o.get("exam"), exams), ("capacity", o.get("capacity"), capacities)):
                bucket.append({"id": digest([o["id"], kind]), "section_id": sid, "observation_id": o["id"],
                               "observed_at": observed_at, "fields": values})
            personals.append({"id": digest([o["id"], "personal"]), "section_id": sid,
                              "observation_id": o["id"], "scope": "section_row", "fields": o.get("personal"),
                              "application_verified_identity_binding": False, "owner_student_id": None,
                              "formal_enrollment": None, "earned_credit": None})
            section_public_obs.append({"id": o["id"], "section_id": sid, "observed_at": observed_at,
                                       "fields": o["fields"], "meeting_set_id": digest([o["id"], "meeting_set"]),
                                       "exam_id": digest([o["id"], "exam"]), "capacity_id": digest([o["id"], "capacity"])})
        variants = [{**json.loads(key), "observation_ids": sorted(ids)} for key, ids in sorted(schedule_variants.items())]
        if len(variants) > 1:
            conflicts.append({"entity_id": sid, "field": "meeting_set", "variants": variants,
                              "resolution": "retain_observation_versions_not_union"})
        sections.append({"section_id": sid, "course_id": course_ids[identity[1]], "course_code": identity[1],
                         "term": identity[2], "source_section_key": identity[3],
                         "identity_kind": "candidate_exact_source_key_within_observed_term",
                         "stable_across_refreshes_verified": False, "fields": fields,
                         "observation_ids": sorted(o["id"] for o in rows),
                         "meeting_variants": variants, "calculator_ready": False})

    route_courses = Counter(o["source"]["route"] for o in business)
    route_sections = Counter(o["source"]["route"] for o in section_obs)
    counts = {"course_observations": len(business), "excluded_course_observations": len(excluded),
              "courses": len(courses), "classification_relations": len(memberships), "section_observations": len(section_obs),
              "candidate_sections": len(sections), "meeting_fragments": len(meetings), "meeting_snapshots": len(meeting_sets),
              "exam_observations": len(exams), "capacity_observations": len(capacities), "personal_observations": len(personals),
              "conflict_fields_or_schedule_versions": len(conflicts), "static_unknown_field_groups": len(unknowns),
              "course_observations_by_route": dict(sorted(route_courses.items())),
              "section_observations_by_route": dict(sorted(route_sections.items()))}
    field_completeness = {}
    for name, rows in (("course", business), ("section", section_obs)):
        names = COURSE_FIELDS if name == "course" else SECTION_FIELDS
        field_completeness[name] = {f: {"known_observations": sum(known(field_value(f, o["fields"].get(f))) for o in rows),
                                           "total_observations": len(rows)} for f in names}
    field_completeness["dynamic"] = {
        "meeting_fragments_with_place": sum(known(m["fields"].get("place_raw")) for m in meetings),
        "meeting_fragments_with_week_set": sum(m["fields"].get("week_set") is not None for m in meetings),
        "exam_statuses": dict(Counter((e["fields"] or {}).get("status", "unknown") for e in exams)),
        "capacity_known_remaining": sum(known((c["fields"] or {}).get("remaining")) for c in capacities),
        "capacity_zero_remaining": sum((c["fields"] or {}).get("remaining") == 0 for c in capacities),
        "capacity_negative_remaining": sum(isinstance((c["fields"] or {}).get("remaining"), int) and c["fields"]["remaining"] < 0 for c in capacities),
        "unknown_observation_time": sum(o["source"].get("observed_at") is None for o in section_obs)}
    expected_fragments = sum(len(o.get("meetings") or []) for o in section_obs)
    checks = {
        "all_course_observations_mapped_or_explicitly_excluded": {o["id"] for o in course_obs} == {o["id"] for o in course_public_obs + excluded},
        "all_section_observations_mapped": {o["id"] for o in section_obs} == set(section_id_by_obs),
        "every_section_observation_has_indivisible_dynamic_records": len(exams) == len(capacities) == len(meeting_sets) == len(section_obs),
        "all_meeting_fragments_preserved": expected_fragments == len(meetings),
        "all_nonduplicate_section_identity_tuples_preserved": len({section_identity(o) for o in section_obs}) == len(sections),
        "all_nonduplicate_course_codes_preserved": {o["code"] for o in business} == {c["code"] for c in courses},
        "all_observation_sources_recoverable": len(source_registry) == len(course_obs) + len(section_obs),
    }
    # Verify each dynamic packet by content rather than just checking counts.
    exams_by_id = {e["observation_id"]: e["fields"] for e in exams}
    caps_by_id = {c["observation_id"]: c["fields"] for c in capacities}
    meetings_by_id = defaultdict(list)
    for m in meetings:
        meetings_by_id[m["observation_id"]].append((m["index"], m["fields"]))
    checks["dynamic_values_preserved_without_cross_time_stitching"] = all(
        exams_by_id[o["id"]] == o.get("exam") and caps_by_id[o["id"]] == o.get("capacity") and
        [f for _, f in sorted(meetings_by_id[o["id"]])] == (o.get("meetings") or []) for o in section_obs)
    if not all(checks.values()):
        raise ValueError("lossless_merge_check_failed")
    return {"courses": courses, "course_observations": course_public_obs, "classification_memberships": memberships,
            "sections": sections, "section_observations": section_public_obs, "meetings": meetings,
            "meeting_snapshots": meeting_sets, "exams": exams, "capacity_snapshots": capacities,
            "restricted_personal_page_observations": personals, "restricted_source_registry": source_registry,
            "excluded_observations": excluded, "conflicts": conflicts, "unknowns": unknowns,
            "field_completeness": field_completeness, "reconciliation": {"counts": counts, "checks": checks,
                "source_adapter_audit": inputs.get("audit"), "evidence_aliases": inputs.get("evidence_aliases", [])}}


def immutable_write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("immutable_output_conflict")
        return
    with path.open("xb") as f:
        f.write(raw)
    path.chmod(0o600)


def publish_local(inputs, output_root):
    data = build_dataset(inputs)
    implementation_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    adapter = Path(__file__).with_name("course_source_adapters.py")
    adapter_sha = hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter.exists() else None
    version_hash = digest({"version": VERSION, "implementation_sha256": implementation_sha,
                           "adapter_implementation_sha256": adapter_sha, "data": data})
    destination = output_root / ("v1-" + version_hash[:20])
    files = {}
    for name, value in sorted(data.items()):
        raw = (canonical(value) + "\n").encode()
        immutable_write(destination / (name + ".json"), raw)
        files[name] = {"path": name + ".json", "sha256": hashlib.sha256(raw).hexdigest(),
                       "records": len(value) if isinstance(value, list) else None}
    manifest = {"schema_version": VERSION, "dataset_version": version_hash, "implementation_sha256": implementation_sha,
                "adapter_implementation_sha256": adapter_sha,
                "data_kind": "derived_real_reference_observations", "security_realm": "restricted_reference",
                "shared_projection_contains_personal_state": False,
                "anonymous_or_production_sharing_authorized": False, "application_imported": False,
                "calculator_ready": False, "external_model_sent": False, "source_identity_binding_verified": False,
                "scope": "observed_autumn_winter_samples_user_exclusions_retained_separately", "files": files,
                "counts": data["reconciliation"]["counts"], "checks": data["reconciliation"]["checks"]}
    immutable_write(destination / "manifest.json", (canonical(manifest) + "\n").encode())
    return destination, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from course_source_adapters import load_sources
    project = args.project.resolve()
    output = (args.output or project / "local-only/course_dedup_20261013/datasets").resolve()
    if project / "local-only" not in output.parents:
        raise ValueError("real_derived_output_must_stay_in_local_only")
    inputs = load_sources(project)
    destination, manifest = publish_local(inputs, output)
    # Real paths are returned only for the local command; public docs use an evidence ID.
    print(json.dumps({"path": str(destination), "dataset_version": manifest["dataset_version"],
                      "counts": manifest["counts"], "checks": manifest["checks"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
