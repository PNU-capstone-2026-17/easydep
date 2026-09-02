"""배포 planner 분리 전후의 결정론적 공개 계약을 검증한다."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.design.services.deployment_diagram import digest as split_digest
from app.design.services.deployment_diagram import normalization as split_normalization
from app.design.services.deployment_diagram import placement as split_placement
from app.design.services.deployment_diagram import planner as planner_facade
from app.design.services.deployment_diagram import planning_facts as split_planning_facts
from app.design.services.deployment_diagram import runtime_binding as split_runtime_binding
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.planner import (
    bind_runtime_contract,
    build_deployment_plan,
    build_provider_resource_plan,
    deployment_plan_structure_digest,
    extract_planning_facts,
    normalize_workload_graph,
    planning_context,
    resource_plan_structure_digest,
    workload_graph_structure_digest,
)

_ROOT = Path(__file__).resolve().parents[1]


def _candidate() -> dict[str, Any]:
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
                        "sourceRefs": ["api:GET /orders"],
                    }
                ],
                "storage": [],
                "configuration": [
                    {
                        "id": "state-url",
                        "name": "STATE_SERVICE_URL",
                        "kind": "endpointBinding",
                        "connectionRef": "web-to-state",
                        "projection": "url",
                        "sensitive": False,
                        "sourceRefs": ["sequence:web-to-state"],
                    },
                    {
                        "id": "feature-flag",
                        "name": "FEATURE_FLAG",
                        "kind": "value",
                        "sensitive": False,
                        "sourceRefs": ["requirement:R-FEATURE"],
                    },
                    {
                        "id": "api-token",
                        "name": "API_TOKEN",
                        "kind": "secretBinding",
                        "sensitive": True,
                        "sourceRefs": ["requirement:R-SECRET"],
                    },
                ],
                "resourceRequirements": {"minVCpu": 1, "minMemoryGiB": 2},
                "replicationSafety": "interchangeable",
                "sourceRefs": ["class:OrderControl"],
            },
            {
                "id": "state",
                "name": "State",
                "artifact": {"kind": "generatedApplication"},
                "interfaces": [
                    {
                        "id": "state-http",
                        "name": "State HTTP",
                        "protocol": "http",
                        "exposure": "internal",
                        "sourceRefs": ["sequence:web-to-state"],
                    }
                ],
                "storage": [
                    {
                        "id": "state-volume",
                        "persistence": "persistent",
                        "capacityGiB": 20,
                        "mountPath": "/srv/state",
                        "deletionPolicy": "retain",
                        "replicaSemantics": "singleAttachment",
                        "sourceRefs": ["requirement:R-DATA"],
                    }
                ],
                "configuration": [],
                "resourceRequirements": {"minVCpu": 1, "minMemoryGiB": 1},
                "replicationSafety": "singleton",
                "sourceRefs": ["class:StateControl"],
            },
        ],
        "externalDependencies": [],
        "connections": [
            {
                "id": "web-to-state",
                "sourceRef": "web",
                "targetRef": "state",
                "protocol": "http",
                "sourceInterfaceRef": "http",
                "targetInterfaceRef": "state-http",
                "sourceRefs": ["sequence:web-to-state"],
            }
        ],
        "constraints": [
            {
                "id": "web-replicas",
                "kind": "replicaCount",
                "workloadRefs": ["web"],
                "value": 2,
                "required": True,
                "sourceRefs": ["requirement:R-HA"],
            },
            {
                "id": "web-zone-spread",
                "kind": "zoneSpread",
                "workloadRefs": ["web"],
                "value": {"minimumZones": 2},
                "required": True,
                "sourceRefs": ["requirement:R-HA"],
            },
            {
                "id": "separate-state",
                "kind": "separate",
                "workloadRefs": ["web", "state"],
                "value": True,
                "required": True,
                "sourceRefs": ["requirement:R-ISOLATE"],
            },
        ],
        "derivations": [
            {
                "rule": "accepted-workload-contract",
                "decision": "Preserved explicit application and state workloads.",
                "sourceRefs": ["planningFact:workloads"],
            }
        ],
    }


def _planning_inputs() -> dict[str, Any]:
    return {
        "refined_requirements": {"requirements": [{"id": "R-HA"}]},
        "capability_contract": {
            "capabilities": [
                {
                    "id": "managed-replacement",
                    "decision": "accepted",
                    "necessity": "required",
                    "origin": "explicit",
                    "requirementIds": ["R-HA"],
                    "evidenceSpans": ["가용성을 위해 자동 복구한다."],
                    "dependencyCapabilityIds": [],
                    "typedConstraints": [
                        {
                            "id": "web-replicas",
                            "kind": "replicaCount",
                            "workloadRefs": ["web"],
                            "value": 2,
                        },
                        {
                            "id": "web-zone-spread",
                            "kind": "zoneSpread",
                            "workloadRefs": ["web"],
                            "value": {"minimumZones": 2},
                        },
                        {
                            "id": "managed-web",
                            "kind": "managedReplacement",
                            "workloadRefs": ["web"],
                            "value": True,
                        }
                    ],
                }
            ]
        },
        "resource_intake": {
            "provenance": [
                {
                    "field": "provider",
                    "value": "aws",
                }
            ]
        },
        "resource_spec": {
            "schemaVersion": "4",
            "workloads": ["vm"],
            "provider": "aws",
            "region": "ap-northeast-2",
            "candidateZones": [
                "ap-northeast-2a",
                "ap-northeast-2b",
                "ap-northeast-2c",
            ],
            "monthlyBudgetUSD": 400,
            "minVCpu": 1,
            "minMemoryGiB": 1,
        },
        "usecase_spec": {"use_cases": [{"id": "UC1"}]},
        "class_model": {"Classes": [{"className": "OrderControl"}]},
        "sequence_model": {"Diagrams": [{"use_case_id": "UC1"}]},
        "api_spec": {
            "paths": {
                "/orders": {
                    "get": {"operationId": "listOrders"},
                    "post": {"operationId": "createOrder"},
                }
            }
        },
        "erd_model": {"Classes": [{"className": "Order"}]},
        "artifact_versions": {
            "refinedRequirements": 3,
            "classModel": 4,
            "apiSpec": 2,
        },
    }


def _runtime_contracts() -> dict[str, Any]:
    return {
        "workloads": [
            {
                "workloadId": "web",
                "imageDigest": "sha256:web",
                "interfaces": [
                    {
                        "interfaceId": "http",
                        "exposure": "public",
                        "port": 8080,
                        "healthPath": "/health",
                    }
                ],
                "mounts": [],
                "configuration": [
                    {"name": "STATE_SERVICE_URL"},
                    {"name": "FEATURE_FLAG", "value": "enabled"},
                    {"name": "API_TOKEN", "secretRef": "secret/orders/api"},
                ],
            },
            {
                "workloadId": "state",
                "imageDigest": "sha256:state",
                "interfaces": [
                    {
                        "interfaceId": "state-http",
                        "exposure": "internal",
                        "port": 9090,
                        "healthPath": "/health",
                    }
                ],
                "mounts": [
                    {"storageId": "state-volume", "mountPath": "/srv/state"}
                ],
                "configuration": [],
            },
        ]
    }


def _invalid_candidate() -> dict[str, Any]:
    return {
        "schemaVersion": "unsupported",
        "workloads": [
            {
                "id": "broken",
                "name": "Broken",
                "artifact": {"kind": "generatedApplication"},
                "interfaces": [
                    {
                        "id": "mail",
                        "protocol": "smtp",
                        "exposure": "unknown",
                        "sourceRefs": [],
                    }
                ],
                "storage": [],
                "configuration": [],
                "resourceRequirements": {},
                "replicationSafety": "unknown",
                "sourceRefs": [],
            }
        ],
        "externalDependencies": [],
        "connections": [
            {
                "id": "dangling",
                "sourceRef": "broken",
                "targetRef": "missing",
                "protocol": "smtp",
                "sourceRefs": [],
            }
        ],
        "constraints": [],
        "derivations": [],
    }


def _pipeline_outputs() -> dict[str, Any]:
    inputs = _planning_inputs()
    facts = extract_planning_facts(**inputs)
    graph = normalize_workload_graph(_candidate(), planning_facts=facts)
    context = planning_context(inputs["resource_spec"])
    plan = build_deployment_plan(graph, context)
    resource_plan = build_provider_resource_plan(
        plan,
        graph,
        provider="aws",
        region="ap-northeast-2",
    )
    runtime_binding = bind_runtime_contract(graph, plan, _runtime_contracts())
    invalid_graph = normalize_workload_graph(_invalid_candidate())
    bundle = build_deployment_diagram_bundle(
        _candidate(),
        inputs["resource_spec"],
        planning_facts=facts,
    )
    return {
        "planningFacts": facts,
        "normalizedGraph": graph,
        "deploymentPlan": plan,
        "runtimeBinding": runtime_binding,
        "resourcePlan": resource_plan,
        "invalidGraph": invalid_graph,
        "bundle": bundle,
    }


def test_bundle_uses_the_same_normalized_graph_plan_and_resource_plan() -> None:
    outputs = _pipeline_outputs()
    bundle = outputs["bundle"]
    projection = bundle["projections"][0]

    assert bundle["planningFacts"] == outputs["planningFacts"]
    assert bundle["workloadGraph"] == outputs["normalizedGraph"]
    assert projection["deploymentPlan"] == outputs["deploymentPlan"]
    assert projection["resourcePlan"] == outputs["resourcePlan"]


def test_planner_facade_reexports_split_public_boundaries() -> None:
    assert planner_facade.extract_planning_facts is (
        split_planning_facts.extract_planning_facts
    )
    assert planner_facade.planning_context is split_planning_facts.planning_context
    assert planner_facade.planning_inputs_stale is (
        split_planning_facts.planning_inputs_stale
    )
    assert planner_facade.validate_workload_graph is (
        split_normalization.validate_workload_graph
    )
    assert planner_facade.normalize_workload_graph is (
        split_normalization.normalize_workload_graph
    )
    assert planner_facade.build_deployment_plan is split_placement.build_deployment_plan
    assert planner_facade.validate_deployment_plan is (
        split_placement.validate_deployment_plan
    )
    assert planner_facade.bind_runtime_contract is (
        split_runtime_binding.bind_runtime_contract
    )
    assert planner_facade.workload_graph_structure_digest is (
        split_digest.workload_graph_structure_digest
    )
    assert planner_facade.deployment_plan_structure_digest is (
        split_digest.deployment_plan_structure_digest
    )
    assert planner_facade.resource_plan_structure_digest is (
        split_digest.resource_plan_structure_digest
    )


def test_post_workload_modules_have_no_llm_or_outer_stage_imports() -> None:
    package = _ROOT / "app/design/services/deployment_diagram"
    module_names = (
        "planning_facts.py",
        "normalization.py",
        "placement.py",
        "runtime_binding.py",
        "digest.py",
        "planning_constants.py",
        "planning_primitives.py",
        "planner.py",
    )
    forbidden = (
        "app.design.graphs",
        "app.repositories",
        "app.requirements",
        "app.implementation",
    )
    violations: list[str] = []

    for module_name in module_names:
        path = package / module_name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: tuple[str, ...]
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported = (
                    module,
                    *(f"{module}.{alias.name}" for alias in node.names),
                )
            elif isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            else:
                continue
            violations.extend(
                f"{module_name}:{node.lineno}:{name}"
                for name in imported
                if name == "parse_structured"
                or ".structured" in name
                or name.startswith(("app.llm", "openai"))
                or name.startswith(forbidden)
            )

        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        calls.update(
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        )
        if "parse_structured" in calls:
            violations.append(f"{module_name}:parse_structured call")

    assert not violations, "forbidden post-workload dependencies: " + ", ".join(
        violations
    )
