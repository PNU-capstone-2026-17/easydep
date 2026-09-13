"""Restore a saved requirements-stage result at the current use-case review gate.

The checkpoint E2E gold set stores artifact state, not the original MySQL rows or
LangGraph checkpoint identifiers.  This utility gives that state fresh database
identifiers and creates a new, current LangGraph feedback checkpoint without
rewriting any field in the saved source JSON.

It is intentionally limited to the use-case review gate.  The resulting app can
receive ordinary feedback through ``POST /api/workspace/apps/{app_id}/commands``.
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

from app.repositories import artifact_repository  # noqa: E402
from app.requirements.contracts.state import AgentState  # noqa: E402
from app.requirements.modeling.use_cases import check_coverage  # noqa: E402
from app.requirements.orchestration import persistence  # noqa: E402
from app.requirements.orchestration.graph import build_graph, result_payload  # noqa: E402
from app.requirements.orchestration.service import persist_analysis  # noqa: E402
from app.requirements.schemas import Actor, UseCase  # noqa: E402
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
    / "use_cases"
    / "state.json"
)


def _load_source(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("The source state must be a JSON object.")
    return value


def _validate_source(source: dict[str, Any]) -> dict[str, Any]:
    raw_requirements = source.get("raw_requirements")
    classified = source.get("classified")
    actors = source.get("actors")
    use_cases = source.get("use_cases")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("The source has no raw_requirements list.")
    if not isinstance(classified, list) or not classified:
        raise ValueError("The source has no classified requirements list.")
    if not isinstance(actors, list) or not actors:
        raise ValueError("The source has no actors list.")
    if not isinstance(use_cases, list) or not use_cases:
        raise ValueError("The source has no use_cases list.")

    requirement_ids = [str(item.get("id") or "") for item in classified]
    use_case_ids = [str(item.get("id") or "") for item in use_cases]
    if any(not value for value in requirement_ids) or len(set(requirement_ids)) != len(
        requirement_ids
    ):
        raise ValueError("The classified requirement IDs are blank or duplicated.")
    if any(not value for value in use_case_ids) or len(set(use_case_ids)) != len(
        use_case_ids
    ):
        raise ValueError("The use-case IDs are blank or duplicated.")

    for actor in actors:
        Actor.model_validate(actor)
    for use_case in use_cases:
        UseCase.model_validate(use_case)

    uc10 = [item for item in use_cases if item.get("id") == "UC10"]
    if len(uc10) != 1:
        raise ValueError("The source must contain exactly one UC10.")

    current_coverage = check_coverage(cast(AgentState, source))["coverage"]
    return {
        "raw_requirement_count": len(raw_requirements),
        "classified_requirement_count": len(classified),
        "actor_count": len(actors),
        "use_case_count": len(use_cases),
        "use_case_ids": use_case_ids,
        "uc10": uc10[0],
        "stored_coverage_matches_current": current_coverage == source.get("coverage"),
        "current_coverage": current_coverage,
    }


def _checkpoint_state(source: dict[str, Any]) -> AgentState:
    allowed = set(AgentState.__annotations__)
    return cast(AgentState, {key: value for key, value in source.items() if key in allowed})


def _restore(source: dict[str, Any], source_path: Path) -> dict[str, Any]:
    # The Workspace service imports the implementation runtime. Keep that heavy
    # dependency out of validation-only runs.
    from app.workspace.service import workspace_service

    app_id = artifact_repository.create_app(
        requirements_text="\n".join(str(item) for item in source["raw_requirements"]),
        resource_constraints_text=str(source.get("resource_constraints_text") or ""),
    )
    persistence.remember_session_mode(app_id, True)
    graph = build_graph(feedback_gates=True, persistent=True)
    config = {"configurable": {"thread_id": app_id}}
    restored_config = graph.update_state(
        config,
        _checkpoint_state(source),
        as_node="model_use_cases",
    )
    waiting = cast(dict[str, object], graph.invoke(None, restored_config))
    payload = result_payload(waiting, app_id)
    if payload.get("status") != "need_feedback" or payload.get("phase") != "use_cases":
        raise RuntimeError("The restored graph did not enter the use_cases feedback gate.")
    if payload.get("use_cases") != source.get("use_cases"):
        raise RuntimeError("The restored use cases differ from the source checkpoint.")

    payload["saved_stages"] = persist_analysis(app_id, payload)
    expected_artifact = {
        "actors": source["actors"],
        "use_cases": source["use_cases"],
        "use_case_specs": [],
        "traceability": source["traceability"],
    }
    stored_artifact = artifact_repository.get_version_content(app_id, "usecase_spec", 1)
    if stored_artifact != expected_artifact:
        raise RuntimeError("The stored before artifact differs from the source checkpoint.")

    command_id = str(uuid.uuid4())
    command = repository.create_command(
        command_id,
        app_id,
        "message",
        "requirements",
        {
            "checkpoint_import": {
                "source": source_path.relative_to(ROOT).as_posix(),
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                "restored_gate": "use_cases",
            }
        },
    )
    visible = workspace_service._requirements_result(cast(dict[str, Any], payload))
    if visible.pop("awaiting_input", False) is not True:
        raise RuntimeError("The restored Workspace command is not awaiting review.")
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

    artifact_versions = artifact_repository.load_state(app_id).get("artifact_versions", {})
    return {
        "app_id": app_id,
        "thread_id": app_id,
        "review_command_id": command_id,
        "review_command": command,
        "saved_stages": payload["saved_stages"],
        "artifact_versions": artifact_versions,
        "source": source_path.relative_to(ROOT).as_posix(),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create the isolated app, artifact versions, and feedback checkpoint.",
    )
    args = parser.parse_args()
    source_path = args.source.resolve()
    source = _load_source(source_path)
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
