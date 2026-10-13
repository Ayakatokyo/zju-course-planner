"""Local delivery QA: immutable input bytes, output hashes and repeat execution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from deduplicate_courses import canonical, immutable_write, publish_local
from course_source_adapters import load_sources


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(project, inventory_path):
    inventory = json.loads(inventory_path.read_text())
    changed = [r["path"] for r in inventory if not (project / r["path"]).is_file()
               or sha(project / r["path"]) != r["sha256"]]
    if changed:
        raise ValueError("original_input_inventory_changed; inspect local inventory")
    inputs = load_sources(project)
    output = project / "local-only/course_dedup_20261013/datasets"
    first, manifest = publish_local(inputs, output)
    first_hashes = {p.name: sha(p) for p in first.iterdir() if p.is_file() and p.name != "delivery_validation.json"}
    second, repeated = publish_local(load_sources(project), output)
    repeated_hashes = {p.name: sha(p) for p in second.iterdir() if p.is_file() and p.name != "delivery_validation.json"}
    file_hashes_valid = all(sha(first / r["path"]) == r["sha256"] for r in manifest["files"].values())
    idempotent = first == second and manifest == repeated and first_hashes == repeated_hashes
    registry = json.loads((first / "restricted_source_registry.json").read_text())
    # Locators refer to original source files; registry itself stays restricted.
    source_files_valid = all((project / r["source"]["path"]).is_file()
                             and sha(project / r["source"]["path"]) == r["source"]["sha256"] for r in registry)
    final_input_check = all((project / r["path"]).is_file() and sha(project / r["path"]) == r["sha256"] for r in inventory)
    result = {"schema_version": "course-dedup-validation/1", "dataset_version": manifest["dataset_version"],
              "counts": manifest["counts"], "original_files_checked": len(inventory),
              "original_inventory_sha256": sha(inventory_path), "output_file_count": len(first_hashes),
              "checks": {**manifest["checks"], "original_bytes_unchanged_before_and_after": final_input_check,
                         "output_hashes_valid": file_hashes_valid, "all_source_files_and_hashes_valid": source_files_valid,
                         "second_independent_source_load_is_byte_idempotent": idempotent},
              "code_sha256": {p.name: sha(p) for p in [Path(__file__), Path(__file__).with_name("deduplicate_courses.py"),
                                                        Path(__file__).with_name("course_source_adapters.py")]},
              "page_acceptance_run": False, "real_business_acceptance_run": False,
              "application_full_regressions_run": False, "model_or_embedding_called": False,
              "committed": False, "pushed": False, "deployed": False}
    if not all(result["checks"].values()):
        raise ValueError("delivery_validation_failed")
    immutable_write(first / "delivery_validation.json", (canonical(result) + "\n").encode())
    return first, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    path = args.inventory or project / "local-only/course_dedup_20261013/source_inventory_before.json"
    first, result = validate(project, path)
    print(json.dumps({"path": str(first), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
