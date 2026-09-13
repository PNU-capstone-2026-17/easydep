"""Restore the report-case class diagram at its Workspace review gate.

The checkpoint bundle stores an immutable ArchitectureState rather than the
original database identifiers.  This utility creates an isolated app, saves the
completed requirements artifacts, persists the exact saved class model as
version 1, and opens a current class-diagram feedback checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.design.graphs.design_graph import (  # noqa: E402
    _result_payload,
    graph,
    session_status,
)
from app.design.schemas.architecture_state import ArchitectureState  # noqa: E402
from app.design.schemas.class_model import BCEModel  # noqa: E402
from app.design.services.class_diagram.plantuml import (  # noqa: E402
    generate_plantuml_from_bce_json,
)
from app.repositories import artifact_repository  # noqa: E402
from app.requirements.orchestration.service import persist_analysis  # noqa: E402
from app.workspace import repository  # noqa: E402
from app.workspace.actions import result_with_contract  # noqa: E402

DEFAULT_SOURCE = (
    ROOT
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "snapshots"
    / "class_diagram"
    / "state.json"
)
SOURCE_CLASS_MODEL = (
    ROOT
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "stages"
    / "05-usecase_diagram-to-class_diagram"
    / "output"
    / "class-model.json"
)
SOURCE_CLASS_PUML = SOURCE_CLASS_MODEL.with_name("class.puml")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return value


def _class_named(model: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in model.get("Classes") or []
        if isinstance(item, dict) and item.get("className") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one class named {name}.")
    return matches[0]


def _validate_source(source: dict[str, Any]) -> dict[str, Any]:
    model = source.get("extracted_bce_classes")
    puml = source.get("class_diagram_puml")
    if not isinstance(model, dict) or not model:
        raise ValueError("The source has no class model.")
    if not isinstance(puml, str) or not puml.strip():
        raise ValueError("The source has no class diagram PlantUML.")
    BCEModel.model_validate(model)

    staged_model = _load_object(SOURCE_CLASS_MODEL)
    if model != staged_model:
        raise ValueError("The snapshot class model differs from the report stage output.")
    staged_puml = SOURCE_CLASS_PUML.read_text(encoding="utf-8")
    if puml != staged_puml:
        raise ValueError("The snapshot PlantUML differs from the report stage output.")
    current_projection = generate_plantuml_from_bce_json(model)

    offering = _class_named(model, "CourseOffering")
    operation_names = [
        str(item.get("name") or "")
        for item in offering.get("operations") or []
        if isinstance(item, dict)
    ]
    if "incrementEnrolledCount" not in operation_names:
        raise ValueError("The source lacks incrementEnrolledCount().")
    if "decrementEnrolledCount" in operation_names:
        raise ValueError("The source already contains decrementEnrolledCount().")

    return {
        "class_count": len(model.get("Classes") or []),
        "course_offering_operation_names": operation_names,
        "class_diagram_syntax_valid": source.get("class_diagram_syntax_valid"),
        "class_diagram_check": source.get("class_diagram_check"),
        "saved_puml_matches_report_stage": True,
        "current_renderer_matches_saved_puml": current_projection == puml,
        "saved_puml_sha256": hashlib.sha256(puml.encode("utf-8")).hexdigest(),
        "current_projection_sha256": hashlib.sha256(
            current_projection.encode("utf-8")
        ).hexdigest(),
    }


def _requirements_payload(source: dict[str, Any]) -> dict[str, object]:
    return {
        "status": "completed",
        "requirements": source.get("refined_requirements") or [],
        "capability_contract": source.get("capability_contract") or {},
        "resource_intake": source.get("resource_intake") or {},
        "actors": source.get("actors") or [],
        "use_cases": source.get("use_cases") or [],
        "use_case_specs": source.get("use_case_specs") or [],
        "traceability": source.get("traceability") or {},
        "diagram": source.get("diagram") or source.get("usecase_diagram_puml") or "",
        "resource_spec": source.get("resource_spec") or {},
    }


def _checkpoint_state(source: dict[str, Any], app_id: str) -> ArchitectureState:
    allowed = set(ArchitectureState.__annotations__)
    restored = {key: value for key, value in source.items() if key in allowed}
    restored["app_id"] = app_id
    return cast(ArchitectureState, restored)


def _restore(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    from app.workspace.service import workspace_service

    app_id = artifact_repository.create_app(
        requirements_text="\n".join(str(item) for item in source["raw_requirements"]),
        resource_constraints_text=str(source.get("resource_constraints_text") or ""),
    )
    saved_requirements = persist_analysis(app_id, _requirements_payload(source))

    config = {"configurable": {"thread_id": app_id}}
    restored_config = graph.update_state(
        config,
        _checkpoint_state(source, app_id),
        as_node="gen_class_diagram",
    )
    waiting = cast(dict[str, object], graph.invoke(None, restored_config))
    payload = _result_payload(cast(dict[str, Any], waiting), app_id)
    if payload.get("status") != "need_feedback" or payload.get("stage") != "class_diagram":
        raise RuntimeError("The restored graph did not enter the class_diagram gate.")
    if session_status(app_id).get("stage") != "class_diagram":
        raise RuntimeError("The persisted design session is not at the class review gate.")

    expected_model = source["extracted_bce_classes"]
    stored_model = artifact_repository.get_version_content(app_id, "class_diagram", 1)
    if stored_model != expected_model:
        raise RuntimeError("The stored before class model differs from the source checkpoint.")

    command_id = str(uuid.uuid4())
    command = repository.create_command(
        command_id,
        app_id,
        "start_design",
        "design",
        {
            "checkpoint_import": {
                "source": source_path.relative_to(ROOT).as_posix(),
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "restored_gate": "class_diagram",
            }
        },
    )
    visible = workspace_service._design_result(payload)
    if visible.get("awaiting_input") is not True:
        raise RuntimeError("The restored Workspace command is not awaiting class review.")
    contracted = result_with_contract(
        {**command, "status": "AWAITING_INPUT"},
        visible,
    )
    command = repository.update_command(
        command_id,
        status="AWAITING_INPUT",
        result=contracted,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )

    return {
        "app_id": app_id,
        "thread_id": app_id,
        "review_command_id": command_id,
        "review_command": command,
        "saved_requirements_stages": saved_requirements,
        "artifact_versions": artifact_repository.load_state(app_id).get(
            "artifact_versions", {}
        ),
        "source": source_path.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source_path = args.source.resolve()
    source = _load_object(source_path)
    report: dict[str, Any] = {
        "source": source_path.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "validation": _validate_source(source),
        "applied": bool(args.apply),
    }
    if args.apply:
        try:
            report["restoration"] = _restore(source, source_path)
        finally:
            from app.workspace.service import workspace_service

            workspace_service.shutdown()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
