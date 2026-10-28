"""Repeatable local status benchmark with synthetic records; no school/model I/O."""
from pathlib import Path
import argparse
import json
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_assistant.graduation_audit import parse_row
from course_assistant.personal_observations import PersonalObservationStore


def benchmark(snapshots=12, iterations=25):
    scope = {"scope_key": "synthetic-benchmark", "owner_student_id": "SYNTHETIC",
             "security_realm": "personal_school_client"}
    records = [parse_row({"JDDM": str(i), "JDMC": f"合成课程{i}", "ROOT": "合成模块",
        "KCDM": f"SYN{i:05d}", "KCXF": "2", "CJ": "80", "SFTG": "通过"}, i)
        for i in range(138)]
    with tempfile.TemporaryDirectory() as directory:
        store = PersonalObservationStore(Path(directory).resolve() / "observations", lambda: scope)
        for i in range(snapshots):
            store.register({"operation": "graduation_audit", "target_term": "ALL-HISTORY",
                "status": "complete_within_scope", "data_source": "live", "records": records,
                "coverage": {"captured_rows": 138, "reported_total": 138, "complete_within_scope": True},
                "captured_at": i, "checked_at": i, "source_alias": "SOURCE-" + "a" * 24},
                parser_version="synthetic-v1", mapping_version="synthetic-v1")
        started = time.perf_counter()
        expected = store.describe()
        cold_ms = (time.perf_counter() - started) * 1000
        durations = []
        for _ in range(iterations):
            started = time.perf_counter()
            actual = store.describe()
            durations.append((time.perf_counter() - started) * 1000)
            assert actual == expected
        return {"synthetic_only": True, "snapshots": snapshots, "rows_per_snapshot": 138,
                "iterations": iterations, "cold_ms": round(cold_ms, 3),
                "median_ms": round(statistics.median(durations), 3),
                "p95_ms": round(sorted(durations)[int((len(durations) - 1) * .95)], 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=int, default=12, choices=range(1, 129))
    parser.add_argument("--iterations", type=int, default=25, choices=range(3, 101))
    args = parser.parse_args()
    print(json.dumps(benchmark(args.snapshots, args.iterations), ensure_ascii=False))
