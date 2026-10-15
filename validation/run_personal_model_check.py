"""Real model smoke test using exclusively synthetic school data.

No real school client is imported or contacted. The explicit flag is required
before using the already configured model. Output contains only synthetic
answers and bounded egress metadata, never keys or provider request headers.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from course_assistant.model_client import ModelClient, ModelError, read_config
from course_assistant.server import Application
from validation.run_personal_ui_fixture import PersonalSyntheticSchool


class AuditedSyntheticModel:
    def __init__(self):
        self.client = ModelClient(read_config(ROOT / ".env"))
        self.egress = []

    def status(self):
        return self.client.status()

    def complete(self, messages, tools):
        raw = json.dumps(messages, ensure_ascii=False)
        for value in ("SYNTHETIC-SCHOOL-A", "SYNTHETIC-LOGIN-A", "SYNTHETIC-TRANSIENT-SECRET", "SOURCE-", "OBS-", "PLAN-", "PEV-", "scope_key", "owner_student_id", "security_realm"):
            if value in raw:
                raise AssertionError("outbound projection included a prohibited fixture marker")
        tool_fields = []
        for item in messages:
            if item["role"] == "tool":
                value = json.loads(item["content"])
                tool_fields.append({"top_level_fields": sorted(value),
                    "record_fields": sorted({key for row in value.get("records", []) for key in row}),
                    "match_fields": sorted({key for row in value.get("matches", []) for key in row})})
        self.egress.append({"characters": len(raw), "messages_sha256": sha256(raw.encode()).hexdigest(),
                            "tool_fields": tool_fields})
        try:
            response = self.client.complete(messages, tools)
        except ModelError as error:
            self.egress[-1]["error_code"] = error.code
            raise
        self.egress[-1]["synthetic_assistant_content"] = response.get("content")
        self.egress[-1]["requested_tools"] = [item["function"]["name"] for item in response.get("tool_calls", [])]
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model-synthetic-only", action="store_true", required=True)
    args = parser.parse_args()
    model = AuditedSyntheticModel()
    if not model.status().get("configured"):
        parser.error("model is not configured")
    questions = ["在已读取的学期成绩中，SYNTHETIC-PE-REPEAT 有几条来源记录？保留重复记录，并说明覆盖范围。",
                 "这份培养资料中 SYNTHETIC-001 的来源学分是什么？有没有不计入某模块的说明？请引用页码并说明适用审核状态。"]
    with tempfile.TemporaryDirectory(prefix="synthetic-personal-model-") as directory:
        app = Application(Path(directory).resolve() / "app.sqlite3", model=model, school_client_factory=PersonalSyntheticSchool)
        try:
            token, _ = app.school.login({"username": "SYNTHETIC-LOGIN-A", "password": "SYNTHETIC-TRANSIENT-SECRET"})
            context = app.auth.authenticate(token)
            app.school.fetch(context, {"operation": "academic", "target_term": "2026-2027-AW"})
            app.school.acquire_plan(context)
            results = []
            for question in questions:
                result = app.personal_chat.turn({"message": question, "request_id": str(uuid.uuid4()), "expected_turn": 0,
                    "consent_personal": True, "data_use_version": "personal-retrieval-v1"}, context)
                results.append({"question": question, "status": result["status"], "reply": result["reply"],
                    "code": result.get("code"), "tools": [item["name"] for item in result["tool_results"]],
                    "citations": len(result["citations"]), "reply_origin": result.get("reply_origin")})
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            receipt = {"scope": "real model plus exclusively synthetic local school transport; not personal-source acceptance",
                       "model": {key: model.status()[key] for key in ("provider", "model")},
                       "results": results, "egress": model.egress,
                       "real_school_network": False, "real_personal_data": False}
            path = ROOT / "validation" / "personal_agent_20261015_model.json"
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            return 0 if all(item["status"] == "completed" for item in results) else 1
        finally:
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
