"""Run and inspect the requirements-to-design flow for sample applications."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.orchestration.graph import complete_design, start_workflow

DEFAULT_CONSTRAINTS = (
    "Deploy the Docker-based application to AWS ap-northeast-2 on Linux VMs. "
    "The monthly infrastructure budget is at most 100 USD. A single availability "
    "zone is acceptable for this evaluation."
)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def inspect_result(response: dict[str, Any]) -> dict[str, Any]:
    """Return reproducible structural checks without pretending to judge semantics."""
    result = response.get("result") or {}
    requirements = result.get("requirements_result") or {}
    design = result.get("design_result") or {}
    cloud = result.get("cloud_design_result") or {}
    artifacts = design.get("artifacts") or {}
    cloud_puml = str(cloud.get("deployment_diagram_puml") or "")
    logical_puml = str(cloud.get("logical_deployment_diagram_puml") or "")
    validation = design.get("validation") or {}
    checks = {
        "design_completed": bool(cloud)
        and response.get("stage") in {"implementation", "completed"},
        "requirements_completed": requirements.get("status") == "completed",
        "use_case_specs_present": bool(requirements.get("use_case_specs")),
        "class_diagram_present": bool(artifacts.get("class_diagram")),
        "sequence_diagram_present": bool(artifacts.get("sequence_diagram")),
        "api_spec_present": bool(artifacts.get("api_spec")),
        "logical_deployment_present": bool(logical_puml),
        "cloud_deployment_present": bool(cloud_puml),
        "cloud_deployment_delimited": (
            cloud_puml.startswith("@startuml") and cloud_puml.endswith("@enduml")
        ),
        "docker_vm_visible": all(
            token in cloud_puml
            for token in ("resource_vm", "Docker runtime", "Application container")
        ),
        "depkb_only": cloud.get("kb_used") == ["depkb"],
        "balanced_braces": cloud_puml.count("{") == cloud_puml.count("}"),
        "balanced_quotes": cloud_puml.count('"') % 2 == 0,
        "cloud_diagram_english_only": not bool(re.search(r"[^\x00-\x7f]", cloud_puml)),
    }
    unavailable = {
        name: data
        for name, data in validation.items()
        if "Unable to access jarfile" in str(data)
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "counts": {
            "requirements": len(requirements.get("requirements") or []),
            "actors": len(requirements.get("actors") or []),
            "use_cases": len(requirements.get("use_cases") or []),
            "use_case_specs": len(requirements.get("use_case_specs") or []),
        },
        "design_validation": validation,
        "validation_environment_unavailable": unavailable,
        "cloud_open_questions": cloud.get("open_questions") or [],
    }


def export_result(output_dir: Path, response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result") or {}
    design = result.get("design_result") or {}
    cloud = result.get("cloud_design_result") or {}
    artifacts = design.get("artifacts") or {}
    files = {
        "result.json": response,
        "requirements.json": result.get("requirements_result") or {},
        "design.json": design,
        "class.puml": artifacts.get("class_diagram") or "",
        "sequence.puml": artifacts.get("sequence_diagram") or "",
        "api.json": artifacts.get("api_spec") or {},
        "erd.puml": artifacts.get("erd") or "",
        "deployment-logical.puml": cloud.get("logical_deployment_diagram_puml") or "",
        "deployment-cloud.puml": cloud.get("deployment_diagram_puml") or "",
    }
    for name, value in files.items():
        _write(output_dir / name, value)
    inspection = inspect_result(response)
    _write(output_dir / "inspection.json", inspection)
    return inspection


def run_sample(path: Path, output_root: Path) -> dict[str, Any]:
    sample = json.loads(path.read_text(encoding="utf-8"))
    requirements = [str(item["text"]) for item in sample.get("classified") or []]
    supplied = str(sample.get("resource_constraints_text") or "").strip()
    constraints = supplied or DEFAULT_CONSTRAINTS
    started = start_workflow(
        requirements,
        resource_constraints_text=constraints,
        app_id=str(sample.get("name") or path.stem),
    )
    if started.get("stage") == "requirements" and started.get("status") != "completed":
        raise RuntimeError(
            f"Requirements input is still needed: {started.get('prompt')}"
        )
    completed = complete_design(str(started["run_id"]))
    output_dir = output_root / path.stem
    inspection = export_result(output_dir, completed)
    metadata = {
        "sample": path.name,
        "run_id": completed.get("run_id"),
        "constraint_source": "sample" if supplied else "evaluation_default",
        "constraints": constraints,
        "completed_at": datetime.now(UTC).isoformat(),
        "inspection": inspection,
    }
    _write(output_dir / "metadata.json", metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", nargs="*")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/evaluations/orchestration")
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    paths = (
        [args.input_dir / f"{name}.json" for name in args.samples]
        if args.samples
        else sorted(args.input_dir.glob("*.json"))
    )
    summary_path = args.output_dir / "summary.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if args.resume and summary_path.is_file()
        else []
    )
    by_sample = {str(item.get("sample")): item for item in summary}
    for path in paths:
        existing = by_sample.get(path.name) or {}
        if args.resume and existing.get("inspection", {}).get("passed") is True:
            continue
        try:
            item = run_sample(path, args.output_dir)
        except Exception as exc:
            item = {"sample": path.name, "error": f"{type(exc).__name__}: {exc}"}
            _write(args.output_dir / path.stem / "error.json", item)
        by_sample[path.name] = item
        summary = [by_sample[name] for name in sorted(by_sample)]
        _write(summary_path, summary)


if __name__ == "__main__":
    main()
