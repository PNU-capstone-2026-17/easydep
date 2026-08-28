"""배포 WorkloadGraph의 typed LLM 경계와 하류 호환 계약을 검증한다."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.design.graphs import subgraphs as design_subgraphs
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.service import (
    generate_workload_graph,
    revise_workload_graph,
)
from app.implementation.delivery.iac_renderer import render_open_tofu

_ROOT = Path(__file__).resolve().parents[1]


def _workload_graph_payload() -> dict[str, Any]:
    return {
        "schemaVersion": "easydep-workload-graph",
        "workloads": [
            {
                "id": "web",
                "name": "Web",
                "artifact": {"kind": "generatedApplication"},
                "interfaces": [
                    {
                        "id": "http",
                        "name": "HTTP",
                        "protocol": "http",
                        "exposure": "public",
                        "port": 8080,
                        "sourceRefs": ["api:GET /orders"],
                    }
                ],
                "storage": [],
                "configuration": [],
                "resourceRequirements": {},
                "replicationSafety": "singleton",
                "sourceRefs": ["class:OrderControl"],
            }
        ],
        "externalDependencies": [],
        "connections": [],
        "constraints": [],
        "derivations": [],
    }


def _resource_spec() -> dict[str, Any]:
    return {
        "schemaVersion": "4",
        "workloads": ["vm"],
        "provider": "aws",
        "region": "ap-northeast-2",
    }


def test_generate_workload_graph_uses_one_structured_proposal() -> None:
    calls: list[tuple[list[dict[str, str]], type[BaseModel]]] = []

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        calls.append((messages, schema))
        return _workload_graph_payload()

    generated = generate_workload_graph(
        "UC1: 고객이 주문 목록을 조회한다.",
        {"openapi": "3.1.0", "paths": {"/orders": {}}},
        refined_requirements=[{"id": "R1"}],
        capability_contract={"capabilities": []},
        resource_intake={"provider": "aws"},
        class_model={"Classes": []},
        sequence_model={"Diagrams": []},
        erd_model={"Classes": []},
        deployment_planning_facts=[{"id": "fact-1"}],
        proposal_call=propose,
    )

    assert isinstance(generated, WorkloadGraph)
    assert generated.model_dump() == WorkloadGraph.model_validate(
        _workload_graph_payload()
    ).model_dump()
    assert len(calls) == 1
    messages, schema = calls[0]
    assert schema is WorkloadGraph
    assert schema.__name__ == "WorkloadGraphProposal"
    serialized = json.loads(messages[-1]["content"])
    assert serialized == {
        "refinedRequirements": [{"id": "R1"}],
        "capabilityContract": {"capabilities": []},
        "resourceIntake": {"provider": "aws"},
        "useCaseSpecification": "UC1: 고객이 주문 목록을 조회한다.",
        "apiSpec": {"openapi": "3.1.0", "paths": {"/orders": {}}},
        "deploymentPlanningFacts": [{"id": "fact-1"}],
        "classModel": {"Classes": []},
        "sequenceModel": {"Diagrams": []},
        "erdModel": {"Classes": []},
    }


def test_empty_generation_and_feedback_do_not_call_llm() -> None:
    calls = 0

    def unexpected(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        nonlocal calls
        del messages, schema
        calls += 1
        raise AssertionError("빈 workload 입력은 structured LLM을 호출하면 안 된다")

    empty = generate_workload_graph("", {}, proposal_call=unexpected)
    current = WorkloadGraph.model_validate(_workload_graph_payload())
    unchanged = revise_workload_graph(current, "", proposal_call=unexpected)

    assert empty == WorkloadGraph()
    assert unchanged is current
    assert calls == 0


def test_revision_is_one_typed_proposal_without_service_repair_loop() -> None:
    current = WorkloadGraph.model_validate(_workload_graph_payload())
    before = current.model_dump()
    calls: list[type[BaseModel]] = []

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        assert messages
        calls.append(schema)
        revised = json.loads(json.dumps(before))
        revised["workloads"][0]["name"] = "Order Web"
        return revised

    revised = revise_workload_graph(
        current,
        "web workload 이름을 명확히 한다.",
        "현재 설계 산출물",
        {"web"},
        proposal_call=propose,
    )

    assert calls == [WorkloadGraph]
    assert isinstance(revised, WorkloadGraph)
    assert revised.workloads[0].name == "Order Web"
    assert current.model_dump() == before


def test_invalid_proposal_fails_after_one_service_call() -> None:
    calls = 0
    invalid = _workload_graph_payload()
    invalid["externalDependencies"] = [
        {
            "id": "web",
            "name": "Duplicate",
            "interfaces": [],
            "sourceRefs": ["requirement:R2"],
        }
    ]

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        nonlocal calls
        del messages, schema
        calls += 1
        return invalid

    with pytest.raises(ValidationError, match="globally unique"):
        generate_workload_graph("UC1", {}, proposal_call=propose)

    assert calls == 1


def test_checkpoint_json_round_trip_preserves_workload_graph_shape() -> None:
    accepted = WorkloadGraph.model_validate(_workload_graph_payload())
    stored = accepted.model_dump()
    restored = WorkloadGraph.model_validate(json.loads(json.dumps(stored)))

    assert restored.model_dump() == stored
    assert set(stored) == {
        "schemaVersion",
        "workloads",
        "externalDependencies",
        "connections",
        "constraints",
        "derivations",
    }
    assert set(stored["workloads"][0]) == {
        "id",
        "name",
        "artifact",
        "interfaces",
        "storage",
        "configuration",
        "resourceRequirements",
        "replicationSafety",
        "sourceRefs",
    }


def test_public_graph_spec_validates_and_dumps_typed_workload_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted = WorkloadGraph.model_validate(_workload_graph_payload())
    captured_generation: dict[str, Any] = {}
    captured_revision: list[tuple[WorkloadGraph, str, set[str]]] = []

    def generate(
        scenario_text: str,
        api_spec: dict[str, Any],
        **inputs: Any,
    ) -> WorkloadGraph:
        captured_generation.update(
            scenario_text=scenario_text,
            api_spec=api_spec,
            inputs=inputs,
        )
        return accepted

    def revise(
        current: WorkloadGraph,
        feedback: str,
        _context_text: str,
        targets: set[str],
    ) -> WorkloadGraph:
        captured_revision.append((current, feedback, targets))
        return current

    state: ArchitectureState = {
        "usecase_spec": {"use_cases": [{"id": "UC1", "name": "주문 조회"}]},
        "api_spec": {"openapi": "3.1.0", "paths": {}},
        "refined_requirements": {"requirements": [{"id": "R1"}]},
        "capability_contract": {"capabilities": []},
        "resource_intake": {"provider": "aws"},
        "extracted_bce_classes": {"Classes": []},
        "sequence_diagram_model": {"Diagrams": []},
        "erd_bce_classes": {"Classes": []},
        "deployment_planning_facts": [{"id": "fact-1"}],
    }
    monkeypatch.setattr(design_subgraphs, "extract_deployment_model", generate)
    monkeypatch.setattr(design_subgraphs, "revise_deployment_model", revise)

    stored = design_subgraphs.DEPLOYMENT_DIAGRAM_SPEC.extract(state)
    revised = design_subgraphs.DEPLOYMENT_DIAGRAM_SPEC.revise(
        stored,
        "web workload를 유지한다.",
        state,
        {"web"},
    )

    assert stored == accepted.model_dump()
    assert revised == stored
    assert captured_generation["scenario_text"]
    assert captured_generation["api_spec"] == state["api_spec"]
    assert captured_generation["inputs"] == {
        "refined_requirements": state["refined_requirements"],
        "capability_contract": state["capability_contract"],
        "resource_intake": state["resource_intake"],
        "class_model": state["extracted_bce_classes"],
        "sequence_model": state["sequence_diagram_model"],
        "erd_model": state["erd_bce_classes"],
        "deployment_planning_facts": state["deployment_planning_facts"],
    }
    assert len(captured_revision) == 1
    current, feedback, targets = captured_revision[0]
    assert isinstance(current, WorkloadGraph)
    assert current.model_dump() == stored
    assert (feedback, targets) == ("web workload를 유지한다.", {"web"})


def test_public_graph_spec_rejects_invalid_raw_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _workload_graph_payload()
    invalid["externalDependencies"] = [
        {
            "id": "web",
            "name": "Duplicate",
            "interfaces": [],
            "sourceRefs": ["requirement:R2"],
        }
    ]

    def generate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return invalid

    monkeypatch.setattr(design_subgraphs, "extract_deployment_model", generate)

    with pytest.raises(ValidationError, match="globally unique"):
        design_subgraphs.DEPLOYMENT_DIAGRAM_SPEC.extract(
            {"usecase_spec": {"use_cases": [{"id": "UC1"}]}}
        )


def test_typed_boundary_preserves_bundle_puml_and_iac_outputs() -> None:
    # 기존 parse_structured도 Pydantic schema의 model_dump를 반환했다. 선택 필드의
    # None/default까지 포함한 이 값이 분리 전 저장·planner 입구 shape다.
    legacy_candidate = WorkloadGraph.model_validate(
        _workload_graph_payload()
    ).model_dump()
    accepted = WorkloadGraph.model_validate(legacy_candidate)

    legacy_bundle = build_deployment_diagram_bundle(
        legacy_candidate, _resource_spec()
    )
    typed_bundle = build_deployment_diagram_bundle(
        accepted.model_dump(), _resource_spec()
    )
    hydrated = hydrate_deployment_diagram_bundle(typed_bundle)

    assert typed_bundle == legacy_bundle
    assert set(hydrated) == {
        "deployment_diagram_bundle",
        "deployment_diagram_model",
        "deployment_workload_graph",
        "deployment_plan",
        "deployment_resource_plan",
    }
    assert hydrated["deployment_diagram_model"] == typed_bundle["workloadGraph"]
    assert hydrated["deployment_workload_graph"] == typed_bundle["workloadGraph"]
    assert deployment_bundle_runtime_puml(typed_bundle) == (
        deployment_bundle_runtime_puml(legacy_bundle)
    )
    assert deployment_bundle_provisioning_puml(typed_bundle) == (
        deployment_bundle_provisioning_puml(legacy_bundle)
    )
    typed_resource_plan = typed_bundle["projections"][0]["resourcePlan"]
    legacy_resource_plan = legacy_bundle["projections"][0]["resourcePlan"]
    assert render_open_tofu(typed_resource_plan) == render_open_tofu(
        legacy_resource_plan
    )


def test_workload_service_does_not_import_outer_stage_state() -> None:
    forbidden = (
        "app.design.graphs",
        "app.repositories",
        "app.requirements",
        "app.implementation",
    )
    modules = (
        "models.py",
        "prompts.py",
        "service.py",
        "extractor.py",
        "reviser.py",
    )
    violations: list[str] = []
    package = _ROOT / "app/design/services/deployment_diagram"

    for name in modules:
        path = package / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...]
            if isinstance(node, ast.ImportFrom):
                imported = (node.module or "",)
            elif isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            else:
                continue
            violations.extend(
                f"{name}:{node.lineno}:{module}"
                for module in imported
                if module.startswith(forbidden)
            )

    assert not violations, "forbidden workload service imports: " + ", ".join(
        violations
    )


def test_compatibility_facades_use_only_public_service_seams() -> None:
    package = _ROOT / "app/design/services/deployment_diagram"
    violations: list[str] = []

    for name in ("extractor.py", "reviser.py"):
        path = package / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module == "app.design.services.common.structured":
                violations.append(f"{name}:{node.lineno}:structured")
            if module == "app.design.services.deployment_diagram.service":
                violations.extend(
                    f"{name}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )

    assert not violations, "compatibility facade bypasses public service: " + ", ".join(
        violations
    )
