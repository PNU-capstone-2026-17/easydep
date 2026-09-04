"""배포 템플릿 구조와 이름 전용 LLM 경계를 검증한다."""

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
from app.design.services.deployment_diagram.digest import workload_graph_structure_digest
from app.design.services.deployment_diagram.models import (
    DeploymentComponentLabels,
    WorkloadGraph,
)
from app.design.services.deployment_diagram.planner import (
    bind_runtime_contract,
    build_provider_resource_plan,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.service import (
    generate_workload_graph,
    revise_workload_graph,
)
from app.implementation.delivery.iac_renderer import render_open_tofu
from app.implementation.runtime.observations import observe_runtime_contract

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


def test_generate_workload_graph_uses_one_name_only_proposal() -> None:
    calls: list[tuple[list[dict[str, str]], type[BaseModel]]] = []

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        calls.append((messages, schema))
        return {"components": [{"id": "application", "name": "Order Service"}]}

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
    assert generated.workloads[0].id == "application"
    assert generated.workloads[0].name == "Order Service"
    assert generated.workloads[0].interfaces[0].exposure == "public"
    assert generated.connections == []
    assert generated.constraints == []
    assert len(calls) == 1
    messages, schema = calls[0]
    assert schema is DeploymentComponentLabels
    serialized = json.loads(messages[-1]["content"])
    assert serialized == {
        "components": [{"id": "application", "name": "Application"}],
        "context": {
            "useCaseSummary": "UC1: 고객이 주문 목록을 조회한다.",
        },
    }


def test_single_vm_file_database_contract_reaches_diagram_and_iac(
    tmp_path: Path,
) -> None:
    """설계 기본값, 실제 앱 관찰, IaC가 같은 실행 계약을 사용하는지 확인한다."""

    resource_spec = _resource_spec()
    api_spec = {"paths": {"/registrations": {"post": {}}}}
    erd_model = {"tables": [{"name": "registrations"}]}
    requirements = [
        {
            "id": "R1",
            "text": "An authenticated identity is required for protected operations.",
        }
    ]
    graph = generate_workload_graph(
        "A student registers for a course.",
        api_spec,
        refined_requirements=requirements,
        resource_spec=resource_spec,
        erd_model=erd_model,
        proposal_call=lambda _messages, _schema: {
            "components": [{"id": "application", "name": "Course Registration"}]
        },
    )
    workload = graph.workloads[0].model_dump()
    configuration = {
        item["name"]: item.get("value") for item in workload["configuration"]
    }
    assert workload["storage"][0]["mountPath"] == "/var/lib/easydep/data"
    assert configuration == {
        "SPRING_DATASOURCE_URL": (
            "jdbc:h2:file:/var/lib/easydep/data/easydep;"
            "MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_ON_EXIT=FALSE"
        ),
        "SPRING_DATASOURCE_USERNAME": "sa",
        "SPRING_DATASOURCE_PASSWORD": "",
        "SPRING_SECURITY_USER_NAME": "easydep",
        "SPRING_SECURITY_USER_PASSWORD": None,
    }

    bundle = build_deployment_diagram_bundle(
        graph.model_dump(),
        resource_spec,
        planning_inputs={
            "refined_requirements": requirements,
            "api_spec": api_spec,
            "erd_model": erd_model,
        },
    )
    assert bundle["status"] == "completed"

    application = tmp_path / "application"
    resources = application / "src/main/resources"
    resources.mkdir(parents=True)
    (resources / "application.yml").write_text(
        "server:\n"
        "  port: 8000\n"
        "spring:\n"
        "  datasource:\n"
        "    url: ${SPRING_DATASOURCE_URL}\n"
        "    username: ${SPRING_DATASOURCE_USERNAME}\n"
        "    password: ${SPRING_DATASOURCE_PASSWORD}\n"
        "  security:\n"
        "    user:\n"
        "      name: ${SPRING_SECURITY_USER_NAME}\n"
        "      password: ${SPRING_SECURITY_USER_PASSWORD}\n"
        "      roles: ${SPRING_SECURITY_USER_ROLES:USER}\n"
        "management:\n"
        "  endpoints:\n"
        "    web:\n"
        "      base-path: /\n"
        "      path-mapping:\n"
        "        health: healthz\n",
        encoding="utf-8",
    )
    (application / "Dockerfile").write_text("EXPOSE 8000\n", encoding="utf-8")
    observed = observe_runtime_contract(bundle, application)
    assert observed["workloads"][0]["mounts"] == [
        {"storageId": "workload-data", "mountPath": "/var/lib/easydep/data"}
    ]

    projection = bundle["projections"][0]
    binding = bind_runtime_contract(
        bundle["workloadGraph"], projection["deploymentPlan"], observed
    )
    assert binding["status"] == "bound"
    resource_plan = build_provider_resource_plan(
        binding["deploymentPlan"],
        binding["workloadGraph"],
        provider="aws",
        region="ap-northeast-2",
    )
    bound_bundle = {
        **bundle,
        "workloadGraph": binding["workloadGraph"],
        "projections": [
            {
                **projection,
                "deploymentPlan": binding["deploymentPlan"],
                "resourcePlan": resource_plan,
            }
        ],
    }
    diagram = deployment_bundle_runtime_puml(bound_bundle)
    rendered_iac = "\n".join(render_open_tofu(resource_plan).values())
    assert "[listen] http :8000" in diagram
    assert "[health] /healthz" in diagram
    assert "/var/lib/easydep/data" in diagram
    assert "SPRING_DATASOURCE_URL" in rendered_iac
    assert "jdbc:h2:file:/var/lib/easydep/data/easydep" in rendered_iac
    assert "SPRING_SECURITY_USER_PASSWORD" in rendered_iac
    assert "secret_reference_application_security_password" in rendered_iac
    assert "aws secretsmanager get-secret-value" in rendered_iac
    assert "/healthz" in rendered_iac


def test_explicit_contracts_choose_template_structure_before_llm() -> None:
    """LLM은 이미 정해진 두 workload의 이름만 받아야 한다."""

    observed: dict[str, Any] = {}

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        observed.update(json.loads(messages[-1]["content"]))
        assert schema is DeploymentComponentLabels
        return {
            "components": [
                {"id": "course-app", "name": "Course Registration"},
                {"id": "course-db", "name": "Course Database"},
            ]
        }

    contracts = [
        {
            "id": "app",
            "kind": "workloadContract",
            "value": {
                "workloadId": "course-app",
                "artifactKind": "generatedApplication",
                "interface": {"protocol": "http", "exposure": "public"},
                "replicaCount": 1,
            },
            "sourceRefs": ["requirement:R1"],
            "authority": "explicit",
            "status": "accepted",
        },
        {
            "id": "db",
            "kind": "workloadContract",
            "value": {
                "workloadId": "course-db",
                "artifactKind": "prebuiltImage",
                "image": "postgres:16",
                "engine": "postgresql",
                "deploymentMode": "container",
                "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
                "interface": {
                    "protocol": "tcp",
                    "exposure": "internal",
                    "port": 5432,
                },
            },
            "sourceRefs": ["requirement:R2"],
            "authority": "explicit",
            "status": "accepted",
        },
        {
            "id": "connection",
            "kind": "connectionContract",
            "value": {
                "sourceWorkloadRef": "course-app",
                "targetWorkloadRef": "course-db",
                "protocol": "tcp",
            },
            "sourceRefs": ["sequence:UC1"],
            "authority": "explicit",
            "status": "accepted",
        },
    ]
    graph = generate_workload_graph(
        "UC1: Student registers for a course.",
        {"paths": {"/registrations": {"post": {}}}},
        deployment_planning_facts=contracts,
        proposal_call=propose,
    )

    assert observed["components"] == [
        {"id": "course-app", "name": "course-app"},
        {"id": "course-db", "name": "course-db"},
    ]
    assert [item.name for item in graph.workloads] == [
        "Course Registration",
        "Course Database",
    ]
    assert [(item.sourceRef, item.targetRef) for item in graph.connections] == [
        ("course-app", "course-db")
    ]
    bundle = build_deployment_diagram_bundle(
        graph.model_dump(),
        _resource_spec(),
        planning_inputs={
            "api_spec": {"paths": {"/registrations": {"post": {}}}},
            "additional_planning_facts": contracts,
        },
    )
    assert bundle["status"] == "completed"
    assert "Course Registration" in deployment_bundle_runtime_puml(bundle)
    assert render_open_tofu(bundle["projections"][0]["resourcePlan"])


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


def test_accepted_capabilities_select_existing_storage_and_lb_templates() -> None:
    def propose(
        _messages: list[dict[str, str]], _schema: type[BaseModel]
    ) -> dict[str, Any]:
        return {"components": [{"id": "application", "name": "Student Portal"}]}

    graph = generate_workload_graph(
        "Students use the portal.",
        {"paths": {"/courses": {"get": {}}}},
        capability_contract={
            "capabilities": [
                {
                    "id": "durable-and-balanced",
                    "decision": "accepted",
                    "requirementIds": ["R1", "R2"],
                    "dependencyCapabilityIds": [
                        "persistent-block-storage",
                        "load-balanced-ingress",
                    ],
                }
            ]
        },
        proposal_call=propose,
    )

    assert graph.workloads[0].storage[0].mountPath == "/var/lib/easydep/data"
    assert [item.kind for item in graph.constraints] == ["managedReplacement"]
    bundle = build_deployment_diagram_bundle(graph.model_dump(), _resource_spec())
    compute = bundle["projections"][0]["deploymentPlan"]["computeUnits"][0]
    assert compute["kind"] == "managedVmGroup"


def test_revision_can_change_only_existing_component_names() -> None:
    current = WorkloadGraph.model_validate(_workload_graph_payload())
    before = current.model_dump()
    calls: list[type[BaseModel]] = []

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        assert messages
        prompt_input = json.loads(messages[-1]["content"])
        assert prompt_input == {
            "components": [{"id": "web", "name": "Web"}],
            "feedback": "web workload 이름을 명확히 한다.",
            "targetIds": ["web"],
        }
        calls.append(schema)
        assert schema is DeploymentComponentLabels
        return {
            "components": [
                {"id": "web", "name": "Order Web"},
                {"id": "invented", "name": "Ignored Component"},
            ]
        }

    revised = revise_workload_graph(
        current,
        "web workload 이름을 명확히 한다.",
        {"web"},
        proposal_call=propose,
    )

    assert calls == [DeploymentComponentLabels]
    assert isinstance(revised, WorkloadGraph)
    assert revised.workloads[0].name == "Order Web"
    assert revised.model_dump(exclude={"workloads": {0: {"name"}}}) == (
        current.model_dump(exclude={"workloads": {0: {"name"}}})
    )
    assert current.model_dump() == before
    assert workload_graph_structure_digest(revised.model_dump()) == (
        workload_graph_structure_digest(current.model_dump())
    )


def test_invalid_proposal_fails_after_one_service_call() -> None:
    calls = 0
    invalid = {
        "components": [
            {"id": "application", "name": "First"},
            {"id": "application", "name": "Duplicate"},
        ]
    }

    def propose(
        messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> dict[str, Any]:
        nonlocal calls
        del messages, schema
        calls += 1
        return invalid

    with pytest.raises(ValidationError, match="label ids must be unique"):
        generate_workload_graph(
            "UC1", {"paths": {"/orders": {"get": {}}}}, proposal_call=propose
        )

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
        "resource_spec": {},
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
