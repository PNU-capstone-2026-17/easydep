"""유지보수성 리팩터링의 최종 공개 계약과 경계 방향을 검증한다."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from app.design.services.api_spec import ApiSpecModel, build_openapi_from_model
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.deployment_diagram.bundle import hydrate_deployment_diagram_bundle
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
)
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.design.services.sequence_diagram import SequenceCollection
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.implementation.domain.implementation_ir import (
    parse_components,
    parse_erd_entities,
    parse_openapi_operations,
)
from app.requirements.modeling.diagram import render_diagram
from app.requirements.schemas import AnalyzeResponse

_ROOT = Path(__file__).resolve().parents[1]
_STATE = (
    _ROOT
    / "evaluation/baselines/course-registration-cases/goldset/e1-aws"
    / "snapshots/deployment_diagram/state.json"
)
_SEQUENCE_PROJECTION_SHA256 = (
    "0453317ee427364c8ab2986a0d127e5d044a37bd060f033aee9009a74e5b29aa"
)
_IMPLEMENTATION_PROJECTION_SHA256 = {
    "components": "670d9fe64be207a142b7cd753ab92c7180127c9c6993910d90394a13590c0cb8",
    "operations": "4711d317fc274709e49856bc30bb9039b6dec6f197ca12a924d3fec29c326484",
    "entities": "7289e477df6f2ac50e22b444001089d8ce191c5314a5177886d6e01e8e76c944",
}


def _json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_frozen_contract_chain_reprojects_and_reaches_implementation() -> None:
    """한 current fixture의 requirements→implementation 외부 shape를 연속 검증한다."""
    state = json.loads(_STATE.read_text(encoding="utf-8"))

    response = AnalyzeResponse.model_validate(
        {
            "thread_id": "rollup",
            "phase": "diagram",
            "status": "completed",
            "requirements": state["classified"],
            "actors": state["actors"],
            "use_cases": state["use_cases"],
            "coverage": state["coverage"],
            "use_case_specs": state["use_case_specs"],
            "relationships": state["relationships"],
            "diagram": state["diagram"],
            "capability_contract": state["capability_contract"],
            "resource_spec": state["resource_spec"],
            "resource_intake": state["resource_intake"],
        }
    )
    assert response.model_dump(mode="json", exclude_none=True) == {
        "thread_id": "rollup",
        "phase": "diagram",
        "status": "completed",
        "requirements": [
            {**item, "source_refs": item.get("source_refs", [])}
            for item in state["classified"]
        ],
        "actors": state["actors"],
        "use_cases": state["use_cases"],
        "coverage": state["coverage"],
        "use_case_specs": state["use_case_specs"],
        "relationships": state["relationships"],
        "diagram": state["diagram"],
        "capability_contract": state["capability_contract"],
        "resource_spec": state["resource_spec"],
        "resource_intake": state["resource_intake"],
    }

    class_puml = generate_plantuml_from_bce_json(state["extracted_bce_classes"])
    assert class_puml == state["class_diagram_puml"]
    sequence = SequenceCollection.model_validate(state["sequence_diagram_model"])
    sequence_puml = generate_sequence_from_model(sequence.model_dump(mode="json"))
    assert sequence_puml.count("@startuml") == len(sequence.Diagrams)
    assert hashlib.sha256(sequence_puml.encode("utf-8")).hexdigest() == (
        _SEQUENCE_PROJECTION_SHA256
    )

    api_model = ApiSpecModel.model_validate(state["api_spec_model"])
    assert build_openapi_from_model(api_model) == state["api_spec"]
    assert generate_erd_from_bce_json(state["erd_bce_classes"]) == state["erd_puml"]

    workload = WorkloadGraph.model_validate(state["deployment_workload_graph"])
    assert workload.workloads
    hydrated = hydrate_deployment_diagram_bundle(state["deployment_diagram_bundle"])
    assert hydrated["deployment_workload_graph"] == state["deployment_workload_graph"]
    assert hydrated["deployment_plan"] == state["deployment_plan"]
    assert hydrated["deployment_resource_plan"] == state["deployment_resource_plan"]
    assert deployment_bundle_provisioning_puml(state["deployment_diagram_bundle"]) == (
        state["deployment_diagram_provisioning_puml"]
    )

    components = [asdict(item) for item in parse_components(class_puml)]
    operations = [
        asdict(item)
        for item in parse_openapi_operations(
            json.dumps(state["api_spec"], ensure_ascii=False)
        )
    ]
    entities = sorted(parse_erd_entities(state["erd_puml"]))
    assert _json_sha256(components) == _IMPLEMENTATION_PROJECTION_SHA256["components"]
    assert _json_sha256(operations) == _IMPLEMENTATION_PROJECTION_SHA256["operations"]
    assert _json_sha256(entities) == _IMPLEMENTATION_PROJECTION_SHA256["entities"]


def test_current_requirements_usecase_projection_is_byte_exact() -> None:
    """현재 stable-id 관계 계약을 결정론적 외부 PlantUML로 고정한다."""

    projected = render_diagram({
        "actors": [{
            "name": "Student",
            "description": "A registered student.",
            "parent_actor": None,
            "source_refs": ["FR1"],
        }],
        "use_cases": [{
            "id": "UC1",
            "name": "View course catalog",
            "primary_actor": "Student",
            "supporting_actors": [],
            "level": "user_goal",
            "goal": "Browse available courses.",
            "requirement_ids": ["FR1"],
            "nfr_ids": [],
        }],
        "relationships": {
            "associations": [{"actor": "Student", "use_case_id": "UC1"}],
            "includes": [],
            "extends": [],
            "generalizations": [],
            "derived_use_cases": [],
        },
    })

    assert projected == {
        "diagram": (
            "@startuml\n"
            "left to right direction\n"
            'actor "Student" as Student_2a164d54\n'
            "rectangle System {\n"
            '  usecase "View course catalog" as UC1_45c30a63\n'
            "}\n"
            "Student_2a164d54 --- UC1_45c30a63\n"
            "@enduml"
        ),
        "phase": "diagram",
    }


def test_frozen_checkpoint_json_excludes_process_local_cache_state() -> None:
    """disk에 저장한 current state에는 accepted-unit cache metadata가 없다."""

    state = json.loads(_STATE.read_text(encoding="utf-8"))

    def keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return [
                *(str(key) for key in value),
                *(nested for item in value.values() for nested in keys(item)),
            ]
        if isinstance(value, list):
            return [nested for item in value for nested in keys(item)]
        return []

    serialized_keys = keys(state)
    assert not any("acceptedunitcache" in key.replace("_", "").lower() for key in serialized_keys)
    assert "cacheVersionDigest" not in serialized_keys
    assert "_values" not in serialized_keys


def test_removed_namespaces_have_no_tracked_or_active_consumers() -> None:
    """삭제한 공용·요구사항 호환 경로가 다시 생기거나 import되지 않는다."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", "app/core"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []

    agent_dir = _ROOT / "app" / "requirements" / "agent"
    assert not agent_dir.exists() or list(agent_dir.rglob("*.py")) == []

    removed = (
        "app.requirements.agent",
        "app.requirements.api",
        "app.requirements.feedback",
        "app.requirements.runner",
        "app.requirements.session_store",
    )
    offenders: list[str] = []
    for path in sorted((_ROOT / "app").rglob("*.py")):
        relative = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [
                    node.module,
                    *(f"{node.module}.{alias.name}" for alias in node.names),
                ]
            offenders.extend(
                f"{relative}:{getattr(node, 'lineno', 0)}:{module}"
                for module in modules
                if module.startswith(removed)
            )
    assert offenders == []


def test_refactor_contract_tests_do_not_couple_to_prompts_or_private_helpers() -> None:
    """새 경계 테스트는 공개 seam만 사용하고 production 문구·private 구현을 읽지 않는다."""

    contract_tests = (
        "test_requirements_contract_boundaries.py",
        "test_llm_helpers.py",
        "test_requirements_resource_stage_boundaries.py",
        "test_requirements_modeling_stage_boundaries.py",
        "test_requirements_orchestration_boundaries.py",
        "test_api_spec_typed_boundaries.py",
        "test_erd_service_boundaries.py",
        "test_deployment_workload_boundary.py",
        "test_deployment_planner_boundaries.py",
        "test_deployment_projection_boundaries.py",
        "test_class_design_service.py",
    )
    violations: list[str] = []
    for filename in contract_tests:
        path = _ROOT / "tests" / filename
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                violations.extend(
                    f"{filename}:{node.lineno}: private import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
                violations.extend(
                    f"{filename}:{node.lineno}: prompt constant {alias.name}"
                    for alias in node.names
                    if "PROMPT" in alias.name.upper()
                )
            elif (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and not node.attr.startswith("__")
            ):
                violations.append(
                    f"{filename}:{node.lineno}: private attribute {node.attr}"
                )
            elif isinstance(node, ast.Name) and "PROMPT" in node.id.upper():
                violations.append(
                    f"{filename}:{node.lineno}: prompt name {node.id}"
                )

    assert violations == []


def test_rollup_policy_documents_current_checkpoint_and_context_ownership() -> None:
    text = (_ROOT / "docs/maintainability-rollup.md").read_text(encoding="utf-8")
    assert "d3caa86f49cc4c501cdfbdfd906f3b5f13b387b6" in text
    assert "현재 checkpoint schema" in text
    assert "과거 MySQL checkpoint shape" in text
    assert "migration" in text
    for context in (
        "cloudkb",
        "requirements",
        "design",
        "orchestration",
        "implementation",
        "metrics",
    ):
        assert f"`app.{context}`" in text
