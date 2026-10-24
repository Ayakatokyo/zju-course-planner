"""Synthetic schema fixtures only; no personal rows or actual course identifiers."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from course_assistant.current_term_recommendation import (
    CurrentTermCourseDataTest, parse_week_annotation, search_delivery_courses,
)
from course_assistant.local_reference_observations import ReferenceObservationError, SHARED_COMPONENTS


def fixture():
    data = {key: [] for key in SHARED_COMPONENTS}
    for index, (code, credit, day) in enumerate((("SYN-A", "3", 1), ("SYN-B", "3", 2), ("SYN-C", "2", 5))):
        cid, sid, oid = "course-" + code, "section-" + code, "obs-" + code
        fields = {k: {"state": "known", "value": v} for k, v in (("name", "Synthetic " + code), ("credits", credit))}
        data["courses"].append({"code": code, "course_id": cid,
            "definitions": [{"term": "2026-2027-1", "fields": fields}], "stable_course_identity_verified": False})
        data["course_observations"].append({"id": "co-" + code, "course_id": cid, "term": "2026-2027-1"})
        data["sections"].append({"section_id": sid, "course_id": cid, "course_code": code,
            "term": "2026-2027-1", "observation_ids": [oid], "private_state": "PRIVATE-STATE"})
        data["section_observations"].append({"id": oid, "section_id": sid, "observed_at": "2026-10-13T06:00:00Z",
            "meeting_set_id": oid + "-set", "exam_id": oid + "-exam", "capacity_id": oid + "-cap",
            "fields": {"teacher_names": "Synthetic teacher", "term_part": "秋冬", "private_path": "/Users/PRIVATE"}})
        data["meeting_snapshots"].append({"id": oid + "-set", "section_id": sid, "observation_id": oid,
            "meeting_ids": [oid + "-meeting"], "status": "partial", "declared_state": None})
        data["meetings"].append({"id": oid + "-meeting", "section_id": sid, "observation_id": oid,
            "fields": {"weekday": day, "periods": [1, 2], "week_set": None, "calendar_version": None,
                       "time_raw": "Synthetic time", "place_raw": "Synthetic room", "week_annotation_raw": None}})
        data["exams"].append({"id": oid + "-exam", "section_id": sid, "observation_id": oid,
            "observed_at": "2026-10-13T06:00:00Z", "fields": {"date": None, "start": None, "end": None,
                "timezone": None, "status": "pending", "raw": "PRIVATE-EXAM-RAW"}})
        data["capacity_snapshots"].append({"id": oid + "-cap", "section_id": sid, "observation_id": oid,
            "observed_at": "2026-10-13T06:00:00Z", "fields": {"remaining": 5, "capacity": 30,
                "male_raw": "PRIVATE-COUNTER", "pending_raw": "PRIVATE-COUNTER"}})
    return data


def add_observation(data, code="SYN-A", *, when="2026-10-13T07:00:00Z", remaining=1):
    original = "obs-" + code
    oid = original + "-new"
    section = next(s for s in data["sections"] if s["course_code"] == code)
    section["observation_ids"].append(oid)
    for key, id_key in (("section_observations", "id"), ("meeting_snapshots", "id"),
                        ("meetings", "id"), ("exams", "id"), ("capacity_snapshots", "id")):
        row = deepcopy(next(r for r in data[key] if r.get("observation_id", r["id"]) == original))
        row["id"] = row["id"].replace(original, oid)
        if "observation_id" in row:
            row["observation_id"] = oid
        if "observed_at" in row:
            row["observed_at"] = when
        for field in ("meeting_set_id", "exam_id", "capacity_id"):
            if field in row:
                row[field] = row[field].replace(original, oid)
        if "meeting_ids" in row:
            row["meeting_ids"] = [v.replace(original, oid) for v in row["meeting_ids"]]
        if key == "capacity_snapshots":
            row["fields"]["remaining"] = remaining
        if key == "meetings":
            row["fields"]["weekday"] = 4
        data[key].append(row)
    return oid


def write_delivery(folder, data):
    root = Path(folder)
    files = {}
    for name, rows in data.items():
        raw = json.dumps(rows, ensure_ascii=False).encode()
        (root / (name + ".json")).write_bytes(raw)
        files[name] = {"path": name + ".json", "sha256": sha256(raw).hexdigest(), "records": len(rows)}
    manifest = {"dataset_version": "SYN-DATA-v1", "files": files, "security_realm": "restricted_reference",
                "shared_projection_contains_personal_state": False, "data_kind": "derived_real_reference_observations"}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "index.json").write_text(json.dumps({"dataset_version": "SYN-DATA-v1", "dataset_manifest": str(root / "manifest.json"),
                                                "requirements_manifest": "/DO-NOT-READ-PRIVATE.json"}))
    return root / "index.json"


class CurrentTermRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.enabled = True

    def service(self, **kwargs):
        return CurrentTermCourseDataTest(self.data, kwargs.pop("dataset_version", "SYN-DATA-v1"),
            current_term=kwargs.pop("current_term", "2026-2027-AW"), authorize=kwargs.pop("authorize", lambda: self.enabled),
            allowed_course_codes=kwargs.pop("allowed_course_codes", ["SYN-A", "SYN-B", "SYN-C"]), **kwargs)

    def test_requested_count_filters_credit_sufficient_subsets_and_survives_revision(self):
        engine = self.service()
        constraints = engine.new_constraints(6, friday_free="soft", course_count=3)
        result = engine.recommend(constraints)
        self.assertTrue(result["options"])
        self.assertTrue(all(len(option["course_codes"]) == 3 for option in result["options"]))
        two = engine.validate(["obs-SYN-A", "obs-SYN-B"], constraints)
        self.assertEqual(two["observed_slot_validation"]["result"], "fail")
        revised = engine.revise(constraints, {"expected_revision": 0, "friday_free": "hard"})
        self.assertEqual(revised["constraints"]["course_count"], 3)
        self.assertEqual(revised["options"], [])
        self.assertEqual(revised["constraints"]["target_credits"], "6")

    def test_exact_colisted_code_passes_both_search_layers_without_identity_promotion(self):
        from course_assistant.current_term_recommendation import search_delivery_courses
        self.data["courses"][0]["definitions"][0]["fields"]["display_code"] = {
            "state": "known", "value": "SYN-A（99123450）"}
        direct = self.service().search_courses("99123450")
        with tempfile.TemporaryDirectory() as folder:
            path = write_delivery(folder, self.data)
            delivery = search_delivery_courses(path, "99123450", current_term="2026-2027-AW", authorize=lambda: True)
            self.assertEqual(delivery["courses"], direct["courses"])
            self.assertEqual(delivery["matching_course_count"], 1)
            self.assertEqual(search_delivery_courses(path, "991234", current_term="2026-2027-AW", authorize=lambda: True)["courses"], [])
        row = direct["courses"][0]
        self.assertEqual(row["course_code"], "SYN-A")
        self.assertEqual(row["identity_scope"], "source_observed_exact_code_only")
        self.assertEqual(row["observed_code_links"], [{"code": "99123450", "relation_kind": "same_source_header",
            "verification_status": "observed", "source_field": "display_code"}])
        self.assertEqual(self.service().catalog._course("SYN-A")["official_course_identity"], "unknown")

    def test_colisted_search_keeps_distinct_courses_and_rejects_wrong_term_or_conflict(self):
        for row in self.data["courses"][:2]:
            row["definitions"][0]["fields"]["display_code"] = {"state": "known", "value": row["code"] + "（99123450）"}
        service = self.service()
        self.assertEqual([r["course_code"] for r in service.search_courses("99123450")["courses"]], ["SYN-A", "SYN-B"])
        self.assertEqual(service.search_courses("99123450", limit=1)["matching_course_count"], 2)
        self.data["courses"][0]["definitions"][0]["fields"]["display_code"]["state"] = "conflicting"
        self.data["courses"][1]["definitions"][0]["fields"].pop("display_code")
        self.data["courses"][1]["definitions"].append({"term": "2025-2026-1", "fields": {
            "display_code": {"state": "known", "value": "SYN-B（99123450）"}}})
        self.assertEqual(self.service().search_courses("99123450")["courses"], [])

    def test_real_computation_generates_distinct_observed_slot_plans_without_formal_promotion(self):
        service = self.service()
        result = service.recommend(service.new_constraints(5), limit=5)
        self.assertTrue(result["options"])
        self.assertGreater(len({tuple(o["course_codes"]) for o in result["options"]}), 1)
        for option in result["options"]:
            self.assertEqual(option["observed_slot_validation"]["result"], "pass")
            self.assertGreaterEqual(DecimalText(option["total_credits"]), 5)
            self.assertEqual(option["full_validation"]["result"], "unknown")
            self.assertFalse(option["observed_slot_validation"]["actual_date_conflict_verified"])
            self.assertGreater(option["observed_slot_validation"]["compared_meeting_pair_count"], 0)
            self.assertEqual(option["formal_recommendation_gate"], "real_recommendation_not_ready")
        self.assertFalse(result["can_save_or_export"])
        self.assertFalse(result["search"]["school_offerings_coverage_complete"])

    def test_conservative_overlap_rejected_even_with_explicit_disjoint_local_weeks(self):
        for i, raw in ((0, "秋1周"), (1, "秋2周")):
            self.data["meetings"][i]["fields"].update(weekday=1, week_annotation_raw=raw)
        service = self.service()
        constraints = service.new_constraints(6, required_course_codes=["SYN-A", "SYN-B"])
        result = service.recommend(constraints)
        self.assertFalse(result["options"])
        checked = service.validate(["obs-SYN-A", "obs-SYN-B"], constraints)
        conflicts = [c for c in checked["observed_slot_validation"]["checks"] if c["code"] == "possible_weekday_period_overlap"]
        self.assertEqual(conflicts[0]["meeting_refs"], ["obs-SYN-A-meeting", "obs-SYN-B-meeting"])
        self.assertFalse(conflicts[0]["actual_date_conflict_verified"])
        self.assertEqual(checked["sections"][0]["meetings"][0]["week_annotation"]["state"], "explicit_season_local_weeks")

    def test_missing_slots_not_inferred_from_term_part_and_fixed_unknown_retained(self):
        self.data["meetings"][0]["fields"]["weekday"] = None
        service = self.service()
        result = service.recommend(service.new_constraints(3))
        self.assertTrue(result["options"])
        self.assertFalse(any("SYN-A" in o["course_codes"] for o in result["options"]))
        fixed = [{"fixed_id": "synthetic-fixed", "weekday": None, "periods": None, "source_refs": ["synthetic-receipt"]}]
        checked = service.validate(["obs-SYN-B"], service.new_constraints(3, fixed_meetings=fixed))
        self.assertEqual(checked["observed_slot_validation"]["result"], "unknown")

    def test_fixed_arrangement_collision_and_hard_friday_are_deterministic_failures(self):
        service = self.service()
        fixed = [{"fixed_id": "fixed", "weekday": 2, "periods": [2, 3], "source_refs": ["fresh-timetable-receipt"]}]
        constraints = service.new_constraints(3, fixed_meetings=fixed)
        checked = service.validate(["obs-SYN-B"], constraints)
        self.assertEqual(checked["observed_slot_validation"]["result"], "fail")
        result = service.recommend(service.new_constraints(2, friday_free="hard"))
        self.assertTrue(result["options"])
        self.assertFalse(any("SYN-C" in o["course_codes"] for o in result["options"]))

    def test_soft_free_scope_changes_actual_order_with_same_pool_and_hard_constraints(self):
        self.data["meetings"][0]["fields"]["weekday"] = 3
        service = self.service()
        base = service.new_constraints(3, max_credits=3, friday_free="hard")
        initial = service.recommend(base, limit=5)
        self.assertEqual(initial["options"][0]["course_codes"], ["SYN-A"])
        result = service.revise(base, {"expected_revision": 0, "soft_free_slots": [{"weekday": 3, "periods": None}]}, limit=5)
        self.assertEqual(result["options"][0]["course_codes"], ["SYN-B"])
        self.assertEqual({tuple(row["course_codes"]) for row in result["options"]}, {tuple(row["course_codes"]) for row in initial["options"]})
        self.assertEqual(result["constraints"]["friday_free"], "hard")
        self.assertEqual(result["constraints"]["input_version_refs"], base["input_version_refs"])
        self.assertEqual([row["soft_preference_evaluation"]["violation_count"] for row in result["options"]], [0, 1])
        locked = service.recommend(service.new_constraints(3, max_credits=3, friday_free="hard",
            locked_observation_ids=["obs-SYN-A"], soft_free_slots=[{"weekday": 3, "periods": None}]))
        self.assertEqual(locked["options"][0]["course_codes"], ["SYN-A"])
        self.assertEqual(locked["options"][0]["soft_preference_evaluation"]["violation_count"], 1)

    def test_soft_weekdays_share_weight_and_one_meeting_counts_once(self):
        self.data["courses"][2]["definitions"][0]["fields"]["credits"]["value"] = "3"
        self.data["meetings"][0]["fields"]["weekday"] = 3
        extra = deepcopy(self.data["meetings"][0])
        extra["id"] += "-second"
        extra["fields"]["periods"] = [3, 4]
        self.data["meetings"].append(extra)
        self.data["meeting_snapshots"][0]["meeting_ids"].append(extra["id"])
        service = self.service()
        only_friday = service.recommend(service.new_constraints(3, max_credits=3, friday_free="soft",
            excluded_observation_ids=["obs-SYN-B"]), limit=5)
        self.assertEqual(only_friday["options"][0]["course_codes"], ["SYN-A"])
        both = service.recommend(service.new_constraints(3, max_credits=3, friday_free="soft",
            excluded_observation_ids=["obs-SYN-B"], soft_free_slots=[{"weekday": 3, "periods": None}, {"weekday": 5, "periods": [1, 2]}]), limit=5)
        # One Friday meeting loses to two Wednesday meetings only when each
        # weekday has the same weight; Friday retains no invented priority.
        self.assertEqual(both["options"][0]["course_codes"], ["SYN-C"])
        self.assertEqual([row["soft_preference_evaluation"]["violation_count"] for row in both["options"]], [1, 2])
        self.assertEqual(both["options"][0]["soft_preference_evaluation"]["scopes"], [
            {"weekday": 3, "periods": None, "meeting_count": 0}, {"weekday": 5, "periods": None, "meeting_count": 1}])
        scoped = service.validate(["obs-SYN-A"], service.new_constraints(3, friday_free="off",
            soft_free_slots=[{"weekday": 3, "periods": [2, 3]}]))
        self.assertEqual(scoped["soft_preference_evaluation"]["violation_count"], 2)
        for scopes in ([{"weekday": 3, "periods": None}, {"weekday": 3, "periods": [2]}],
                       [{"weekday": True, "periods": None}], [{"weekday": 4, "periods": [2, 2]}]):
            with self.subTest(scopes=scopes), self.assertRaises(ReferenceObservationError):
                service.new_constraints(3, soft_free_slots=scopes)

    def test_exam_conflict_uses_existing_planner_only_with_explicit_timezone(self):
        for exam in self.data["exams"][:2]:
            exam["fields"].update(date="2026-12-01", start="10:00", end="11:00", timezone="Asia/Shanghai")
        service = self.service()
        constraints = service.new_constraints(6, required_course_codes=["SYN-A", "SYN-B"])
        checked = service.validate(["obs-SYN-A", "obs-SYN-B"], constraints)
        self.assertEqual(checked["full_validation"]["result"], "fail")
        self.assertTrue(any(c["code"] == "exam_conflict" and c["result"] == "fail" for c in checked["full_validation"]["checks"]))
        self.assertFalse(service.recommend(constraints)["options"])
        for exam in self.data["exams"][:2]:
            exam["fields"]["timezone"] = None
        service = self.service()
        checked = service.validate(["obs-SYN-A", "obs-SYN-B"], service.new_constraints(6))
        self.assertEqual(checked["observed_slot_validation"]["result"], "pass")
        self.assertEqual(checked["full_validation"]["result"], "unknown")

    def test_latest_observation_kept_as_indivisible_bundle_no_old_positive_seat_fallback(self):
        add_observation(self.data, remaining=0)
        service = self.service()
        result = service.recommend(service.new_constraints(3))
        self.assertFalse(any("SYN-A" in o["course_codes"] for o in result["options"]))
        result = service.recommend(service.new_constraints(3, locked_observation_ids=["obs-SYN-A"]))
        self.assertTrue(result["options"])
        self.assertTrue(all("obs-SYN-A" in o["observation_ids"] for o in result["options"]))
        row = next(r for r in result["options"][0]["sections"] if r["observation_id"] == "obs-SYN-A")
        self.assertEqual(row["capacity"]["remaining"], 5)
        self.assertEqual(row["meetings"][0]["weekday"], 1)
        self.assertIn("historical_observations_not_current_offerings", result["limitations"])

    def test_unordered_observations_require_explicit_lock(self):
        add_observation(self.data, when=None)
        service = self.service()
        result = service.recommend(service.new_constraints(3, required_course_codes=["SYN-A"]))
        self.assertFalse(result["options"])
        self.assertTrue(any(d["code"] == "observation_order_unknown_requires_explicit_lock" for d in result["diagnostics"]))
        self.assertTrue(service.recommend(service.new_constraints(3, locked_observation_ids=["obs-SYN-A"]))["options"])

    def test_unknown_seats_not_zero_and_never_current_availability(self):
        self.data["capacity_snapshots"][0]["fields"]["remaining"] = None
        service = self.service()
        result = service.recommend(service.new_constraints(3, required_course_codes=["SYN-A"]))
        self.assertTrue(result["options"])
        checks = result["options"][0]["full_validation"]["checks"]
        self.assertTrue(any(c["code"] == "availability" and c["result"] == "unknown" for c in checks))

    def test_conflicting_credits_are_pending_and_missing_course_is_not_global_no_solution(self):
        self.data["courses"][0]["definitions"][0]["fields"]["credits"] = {"state": "conflict", "value": "3"}
        service = self.service(allowed_course_codes=["SYN-A", "SYN-MISSING"])
        result = service.recommend(service.new_constraints(3, required_course_codes=["SYN-A"]))
        self.assertTrue(result["options"])
        self.assertIsNone(result["options"][0]["total_credits"])
        self.assertEqual(result["options"][0]["observed_slot_validation"]["result"], "unknown")
        self.assertEqual(service.describe()["unobserved_course_count"], 1)
        result = service.recommend(service.new_constraints(3, required_course_codes=["SYN-MISSING"]))
        self.assertFalse(result["options"])
        self.assertFalse(result["search"]["school_offerings_coverage_complete"])

    def test_revocation_and_authorization_scope_changes_are_checked(self):
        service = self.service()
        constraints = service.new_constraints(3)
        self.enabled = False
        for action in (service.describe, lambda: service.recommend(constraints), lambda: service.search_courses("Synthetic")):
            with self.assertRaises(ReferenceObservationError): action()
        grant = {"grant_version": "SYN-GRANT-v1", "scope_version": "SYN-SCOPE-v1"}
        service = self.service(authorize=lambda: deepcopy(grant))
        grant["scope_version"] = "SYN-SCOPE-v2"
        with self.assertRaises(ReferenceObservationError): service.describe()

    def test_revise_preserves_locks_fixed_and_versions_then_revalidates(self):
        service = self.service()
        fixed = [{"fixed_id": "fixed", "weekday": 6, "periods": [7], "source_refs": ["fresh-receipt"]}]
        original = service.new_constraints(5, locked_observation_ids=["obs-SYN-A"], fixed_meetings=fixed)
        result = service.revise(original, {"expected_revision": 0, "locked_observation_ids": [], "fixed_meetings": [], "friday_free": "hard"})
        revised = result["constraints"]
        self.assertEqual(revised["revision"], 1)
        self.assertEqual(revised["locked_observation_ids"], ["obs-SYN-A"])
        self.assertEqual(revised["fixed_meetings"], fixed)
        self.assertEqual(revised["input_version_refs"], original["input_version_refs"])
        self.assertTrue(result["options"])
        self.assertTrue(all("obs-SYN-A" in o["observation_ids"] for o in result["options"]))
        unlocked = service.revise(revised, {"expected_revision": 1, "unlock_observation_ids": ["obs-SYN-A"],
                                         "excluded_observation_ids": ["obs-SYN-A"]})
        self.assertFalse(unlocked["constraints"]["locked_observation_ids"])
        self.assertTrue(all("obs-SYN-A" not in o["observation_ids"] for o in unlocked["options"]))
        with self.assertRaises(ValueError): service.revise(revised, {"expected_revision": 0})
        with self.assertRaises(ReferenceObservationError): service.revise(revised, {"current_term": "2027-2028-AW"})

    def test_compare_and_validation_reload_server_bundles_and_reject_stale_or_outside_refs(self):
        service = self.service()
        constraints = service.new_constraints(5)
        result = service.compare([["obs-SYN-A", "obs-SYN-C"], ["obs-SYN-B", "obs-SYN-C"]], constraints)
        self.assertEqual(len(result["options"]), 2)
        self.assertTrue(all(o["total_credits"] == "5" for o in result["options"]))
        with self.assertRaises(ReferenceObservationError): service.validate(["outside"], constraints)
        with self.assertRaises(ReferenceObservationError): self.service(dataset_version="SYN-DATA-v2").recommend(constraints)
        with self.assertRaises(ReferenceObservationError): service.new_constraints(3, required_course_codes=["OUTSIDE"])
        self.assertEqual(service.validate(["obs-SYN-A", "obs-SYN-A"], constraints)["observed_slot_validation"]["result"], "fail")

    def test_search_describe_and_results_do_not_project_private_state(self):
        service = self.service()
        found = service.search_courses("Synthetic", limit=2)
        self.assertEqual(len(found["courses"]), 2)
        self.assertTrue(found["truncated"])
        self.assertEqual(service.describe()["current_term"], "2026-2027-AW")
        text = json.dumps([found, service.describe(), service.recommend(service.new_constraints(3))])
        for value in ("PRIVATE-STATE", "PRIVATE-COUNTER", "PRIVATE-EXAM-RAW", "/Users/PRIVATE", "pending_raw", "male_raw"):
            self.assertNotIn(value, text)

    def test_current_term_is_unique_and_search_or_scope_bounded(self):
        for term in ("2026-2027-SS", "2026-2028-AW", None, 1):
            with self.subTest(term=term), self.assertRaises(ReferenceObservationError): self.service(current_term=term)
        with self.assertRaises(ReferenceObservationError): self.service(allowed_course_codes=[f"SYN-{i}" for i in range(25)])
        with self.assertRaises(ReferenceObservationError): self.service().search_courses("Synthetic", limit=11)
        result = self.service().recommend(self.service().new_constraints(3), node_limit=1)
        self.assertTrue(result["search"]["limit_reached"])
        self.assertFalse(result["search"]["complete_within_observed_scope"])

    def test_loader_checks_hashes_and_reads_only_shared_files_and_lexical_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_delivery(folder, self.data)
            result = search_delivery_courses(path, "Synthetic", current_term="2026-2027-AW", authorize=lambda: True, limit=2)
            self.assertEqual(result["matching_course_count"], 3)
            self.assertEqual(len(result["courses"]), 2)
            service = CurrentTermCourseDataTest.from_delivery(path, current_term="2026-2027-AW", authorize=lambda: True,
                                                            allowed_course_codes=["SYN-A"])
            self.assertEqual(service.describe()["observation_count"], 1)
            (Path(folder) / "meetings.json").write_text("[]")
            with self.assertRaises(ReferenceObservationError) as caught:
                CurrentTermCourseDataTest.from_delivery(path, current_term="2026-2027-AW", authorize=lambda: True,
                                                       allowed_course_codes=["SYN-A"])
            self.assertEqual(caught.exception.code, "delivery_hash_mismatch")

    def test_loader_rejects_private_file_redirection_and_personal_component_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_delivery(folder, self.data)
            manifest_path = Path(folder) / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["files"]["meetings"]["path"] = "restricted_personal_page_observations.json"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ReferenceObservationError) as caught:
                CurrentTermCourseDataTest.from_delivery(path, current_term="2026-2027-AW", authorize=lambda: True,
                                                       allowed_course_codes=["SYN-A"])
            self.assertEqual(caught.exception.code, "shared_components_only")
        self.data["restricted_personal_page_observations"] = [{"private": "DO-NOT-READ"}]
        with self.assertRaises(ReferenceObservationError): self.service()

    def test_raw_week_parity_and_explicit_sets_do_not_invent_calendar_dates(self):
        self.assertEqual(parse_week_annotation("秋1-2,4-8周；冬1-7周")["season_week_numbers"]["秋"], [1, 2, 4, 5, 6, 7, 8])
        self.assertEqual(parse_week_annotation("单周")["state"], "parity_only")
        self.assertIsNone(parse_week_annotation("双周")["season_week_numbers"])
        for value in (None, "秋0周", "秋4-2周", "第1到8周", {}, "秋1周；秋2周"):
            self.assertIsNone(parse_week_annotation(value)["season_week_numbers"])


def DecimalText(value):
    from decimal import Decimal
    return Decimal(value)


if __name__ == "__main__":
    unittest.main()
