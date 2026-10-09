"""A loopback-only local web app using the Python standard library."""
from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .audit import audit_progress
from .exporter import export_result
from .importer import ROOT, SOURCE_ROOT, ImportErrorDetail, digest, load_json, read_sources, require, validate_demo
from .planner import generate_plans, revise_constraints, validate_plan
from .storage import Store


class Application:
    def __init__(self, db_path=None):
        self.source = read_sources()
        self.store = Store(db_path or ROOT / "runtime" / "planning.sqlite3")
        self.store.import_dataset("source", self.source)
        self.demo = validate_demo(load_json(ROOT / "demo" / "bundle.json"), self.source)
        self.store.import_dataset("synthetic", self.demo)
        self.constraints = copy.deepcopy(self.demo["constraints"])
        self.lock = threading.RLock()

    def versions(self):
        return {"algorithm": __version__, "dataset_sha256": digest(self.demo),
                "source_manifest_sha256": self.source["integrity"]["manifest_sha256"],
                "plan_id": self.demo["student"].get("plan_id"),
                "plan_version": self.demo["student"].get("plan_version"),
                "profile_version": self.demo["student"].get("profile_version"),
                "academic_snapshot": self.demo["academic_snapshot"]["snapshot_id"],
                "offering_snapshot": self.demo["offering_snapshot"]["snapshot_id"],
                "rule_version": sorted({r["version"] for r in self.demo["rules"]})}

    def audit(self, body):
        if body.get("mode", "demo") == "source":
            return audit_progress(body.get("student", {"plan_id": None, "plan_binding_status": "unknown"}),
                                  body.get("records", []), self.source["candidates"], self.source["courses"],
                                  record_completeness="unknown")
        return audit_progress(self.demo["student"], self.demo["academic_snapshot"]["records"],
                              self.demo["rules"], self.demo["courses"],
                              record_completeness=self.demo["academic_snapshot"]["completeness"])

    def enrich_plan(self, plan, constraints):
        lookup = {s["section_id"]: s for s in self.demo["offering_snapshot"]["records"]}
        sections = [lookup[k] for k in plan["section_ids"]]
        validation = validate_plan(plan["section_ids"], self.demo["offering_snapshot"],
                                   self.demo["student"], constraints,
                                   academic_records=self.demo["academic_snapshot"]["records"])
        # Prospective pass records exist only inside this explanation object. They
        # never enter the student's earned-credit ledger or stored dataset.
        records = copy.deepcopy(self.demo["academic_snapshot"]["records"])
        for section in sections:
            course = next(c for c in self.demo["courses"] if c["course_id"] == section["course_id"])
            records.append({"record_id": "DEMO-PROJECTION-" + section["section_id"],
                            "attempt_id": "DEMO-PROJECTION-" + section["section_id"],
                            "student_key": self.demo["student"]["student_key"], "plan_id": self.demo["student"].get("plan_id"),
                            "record_kind": "course", "course_id": section["course_id"], "completion_status": "passed",
                            "recognition_status": "not_applicable", "earned_credits": section.get("credits"),
                            "ledger": "base", "tags": course.get("tags", []), "data_kind": "synthetic",
                            "snapshot_id": "DEMO-PROJECTION", "source_refs": [{"snapshot_id": "DEMO-PROJECTION"}]})
        projection = audit_progress(self.demo["student"], records, self.demo["rules"], self.demo["courses"],
                                    record_completeness=self.demo["academic_snapshot"]["completeness"])
        return {**plan, "sections": sections, "validation": validation, "versions": self.versions(),
                "data_kind": "synthetic", "status": "planning_draft",
                "plan_key": digest([plan["section_ids"], constraints, self.versions()]),
                "expected_rule_contributions": projection["rules"],
                "projection_note": "预计贡献仅在这些课程将来全部通过并获认定的假设下计算，不增加已获学分。"}

    def frozen_result(self, body):
        if body.get("saved_id"):
            match = next((row for row in self.store.saved() if row["saved_id"] == body["saved_id"]), None)
            require(match is not None, "保存版本不存在")
            return match
        plan = body.get("plan", {})
        require(plan.get("versions", {}).get("dataset_sha256") == digest(self.demo), "数据已更新，请重新生成后再保存/导出")
        constraints = revise_constraints(self.constraints, body.get("constraints", {}))
        plan = self.enrich_plan(plan, constraints)
        return {"data_kind": "synthetic", "status": "planning_draft", "not_official_enrollment": True,
                "plan": plan, "constraints": constraints, "versions": self.versions(),
                "frozen_dataset": copy.deepcopy(self.demo)}

    def get(self, path):
        if path == "/api/bootstrap":
            demo = copy.deepcopy(self.demo)
            demo["constraints"] = copy.deepcopy(self.constraints)
            return {"source": self.source, "demo": demo, "saved_plans": self.store.saved(),
                    "versions": self.versions()}
        if path == "/api/saved":
            return self.store.saved()
        if path == "/api/health":
            return {"status": "ok", "version": __version__, "data_kind": "synthetic", "actual_offerings": 0}
        raise ImportErrorDetail("未知 API")

    def post(self, path, body):
        if path == "/api/audit":
            return self.audit(body)
        if path == "/api/import":
            demo = validate_demo(body.get("bundle"), self.source)
            imported = self.store.import_dataset("synthetic", demo)
            self.demo = demo
            self.constraints = copy.deepcopy(demo["constraints"])
            return {"result": "pass", **imported, "versions": self.versions(),
                    "note": "旧方案保留原快照；导入不修改 inputs。活动演示数据重启后回到默认夹具。"}
        if path in ("/api/generate", "/api/validate"):
            constraints = revise_constraints(self.constraints, body.get("constraints", {}))
            if path == "/api/validate":
                return validate_plan(body.get("section_ids", []), self.demo["offering_snapshot"],
                                     self.demo["student"], constraints,
                                     academic_records=self.demo["academic_snapshot"]["records"])
            self.constraints = constraints
            result = generate_plans(self.demo["offering_snapshot"], self.demo["student"], constraints,
                                    academic_records=self.demo["academic_snapshot"]["records"],
                                    max_nodes=10000, limit=3)
            result["plans"] = [self.enrich_plan(plan, constraints) for plan in result.get("plans", [])]
            result["pending_plans"] = [self.enrich_plan(plan, constraints) for plan in result.get("pending_plans", [])]
            return {**result, "constraints": constraints, "versions": self.versions(), "data_kind": "synthetic"}
        if path == "/api/save":
            return self.store.save(self.frozen_result(body))
        if path == "/api/export":
            return export_result(self.frozen_result(body), body.get("format", "json"))
        raise ImportErrorDetail("未知 API")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def response(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/"):
                    with app.lock:
                        self.response(app.get(path))
                    return
                if path.startswith("/sources/"):
                    filename = path.rsplit("/", 1)[1]
                    allowed = {r["bundled_path"].rsplit("/", 1)[1] for r in app.source["source_aliases"]}
                    require(filename in allowed, "来源文件不存在")
                    file = SOURCE_ROOT / "sources" / filename
                else:
                    filename = "index.html" if path == "/" else path.lstrip("/")
                    require(filename in {"index.html", "app.js", "styles.css"}, "文件不存在")
                    file = ROOT / "web" / filename
                data = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, OSError) as error:
                self.response({"error": str(error)}, 404)

        def do_POST(self):
            try:
                # Local forms cannot be invoked by an unrelated browser origin.
                origin = self.headers.get("Origin")
                require(not origin or urlparse(origin).netloc == self.headers.get("Host"), "不接受跨来源写请求")
                require(self.headers.get("Content-Type", "").split(";", 1)[0] == "application/json", "请使用 application/json")
                length = int(self.headers.get("Content-Length", 0))
                require(0 < length <= 2_000_000, "请求 JSON 大小需在 1 字节至 2 MB 内")
                body = json.loads(self.rfile.read(length))
                require(isinstance(body, dict), "请求必须是 JSON 对象")
                path = urlparse(self.path).path
                with app.lock:
                    result = app.post(path, body)
                if path == "/api/export":
                    data, content_type, suffix = result
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Disposition", f'attachment; filename="synthetic-plan.{suffix}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.response(result)
            except (ValueError, KeyError, TypeError) as error:
                self.response({"error": str(error)}, 400)

        def log_message(self, fmt, *args):
            # Keep launch output useful without logging uploaded personal bodies.
            super().log_message(fmt, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="本地选课规划/显式模拟演示")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app = Application()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"选课助手 http://127.0.0.1:{args.port} · synthetic 演示 · Ctrl+C 停止", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
