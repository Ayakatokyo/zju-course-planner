"""Synthetic adapter contracts; no school requests or real fixtures in Git."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from course_source_adapters import Loader, PanelParser, capacity_packet, exam_packet, meeting_packet, segments


class SourceAdapterTests(unittest.TestCase):
    def test_week_annotation_semicolon_does_not_create_meeting(self):
        packet = meeting_packet("周二第3,4节<br>周四第1,2节{秋1-8周；冬1-8周}", "A<br>B")
        self.assertEqual(len(packet), 2)
        self.assertEqual(packet[1]["week_annotation_raw"], "秋1-8周；冬1-8周")
        self.assertEqual(packet[1]["periods"], [1, 2])
        self.assertIsNone(packet[1]["week_set"])
        self.assertEqual(packet[1]["place_raw"], "B")

    def test_unknown_schedule_is_not_known_empty(self):
        packet = meeting_packet("--", "--")
        self.assertEqual(len(packet), 1)
        self.assertEqual(packet[0]["time_raw"], "--")
        self.assertIsNone(packet[0]["weekday"])
        self.assertEqual(segments("周一第1节;周二第2节"), ["周一第1节", "周二第2节"])

    def test_location_misalignment_does_not_guess(self):
        packet = meeting_packet("周一第1节<br>周二第2节", "A")
        self.assertTrue(all(m["place_raw"] is None for m in packet))
        self.assertEqual(packet[0]["unaligned_places_raw"], ["A"])

    def test_capacity_zero_negative_and_hidden_sentinel(self):
        packet = capacity_packet("-2/0", "0~0", "-1", "-1")
        self.assertEqual((packet["remaining"], packet["capacity"]), (-2, 0))
        self.assertFalse(packet["hidden_counter_semantics_verified"])
        self.assertEqual(packet["pending_raw"], "0~0")

    def test_pending_exam_retains_original_and_timezone_unknown(self):
        self.assertEqual(exam_packet("待定")["status"], "explicit_pending")
        self.assertEqual(exam_packet("待定")["raw"], "待定")
        self.assertEqual(exam_packet("2027年1月2日(8:00-10:00)")["date"], "2027-01-02")
        self.assertIsNone(exam_packet("2027年1月2日(8:00-10:00)")["timezone"])
        self.assertIsNone(exam_packet("2027年2月30日(08:00-10:00)")["date"])

    def test_parser_retains_cell_line_breaks(self):
        parser = PanelParser()
        parser.feed('<div class="kc_head" data-xkcdm="X"><h3>X（1） 课 2 学分<span style="display:none">秘密</span></h3></div><tbody><tr id="tr_K" data-xkkh="K"><td class="sksj">周一第1节<br>周二第2节</td></tr></tbody>')
        self.assertEqual(parser.rows[0]["cells"][0]["text"], "周一第1节\n周二第2节")
        self.assertNotIn("秘密", "".join(parser.header))

    def test_source_path_escape_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = Loader(Path(tmp))
            with self.assertRaisesRegex(ValueError, "outside_project_local_only"):
                loader.read("../unrelated.json")

    def test_course_personal_flags_never_enter_shared_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = Loader(Path(tmp))
            source = loader.source("backend_full", "local-only/x.json", "hash", "/0", None)
            loader.raw_course({"kcdm": "X", "xkkh": "T(2026-2027-1)-X", "kcxx": "1~2.0~2.0-0.0", "kcxzzt": "1", "tsbj": "0", "xkzy": 0}, source, {"term": "2026-2027-1"}, "分类")
            record = loader.courses[0]
            self.assertEqual(record["fields"]["credits"], "2.0")
            self.assertNotIn("kcxx", record["fields"])
            self.assertEqual(record["personal"]["xkzy"], 0)

    def test_display_query_alias_requires_direct_action_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = Loader(Path(tmp))
            loader.backend_display_codes = {"SYNTHETIC_DISPLAY（SYNTHETIC_QUERY）": {"SYNTHETIC_QUERY"}}
            row = {"attributes": {"data-xkkh": "(2026-2027-1)-SYNTHETIC_QUERY-SYNTHETIC_TEACHER-1"}, "cells": [{"class": "xkkh", "text": "(2026-2027-1)-SYNTHETIC_QUERY-SYNTHETIC_TEACHER-1"}, {"class": "an", "text": "选课", "html": '<button data-kcdm="SYNTHETIC_QUERY">选课</button>'}]}
            obs = {"code": "SYNTHETIC_DISPLAY", "header": "SYNTHETIC_DISPLAY（SYNTHETIC_QUERY） 课程 2.0 学分课程类别：专业课开课学院：学院状态：未选", "source_scope": "zy_qb", "rows": [row], "observed_at": "2026-10-13T00:00:00Z"}
            loader.browser_course(obs, "local-only/x.json", "hash")
            self.assertEqual(loader.courses[0]["code"], "SYNTHETIC_QUERY")
            self.assertEqual(loader.sections[0]["code"], "SYNTHETIC_QUERY")
            self.assertEqual(loader.courses[0]["context"]["code_binding"]["dom_code"], "SYNTHETIC_DISPLAY")
            row["cells"][1]["html"] = '<button data-kcdm="WRONG">选课</button>'
            with self.assertRaisesRegex(ValueError, "alias_binding_not_verified"):
                loader.browser_course(obs, "local-only/x.json", "hash")


if __name__ == "__main__":
    unittest.main()
