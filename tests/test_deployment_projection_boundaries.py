"""provider projection과 배포 renderer의 분리 전후 byte 계약을 검증한다."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.design.services.deployment_diagram import (
    provider_plantuml as plantuml_facade,
)
from app.design.services.deployment_diagram import (
    provider_template as template_facade,
)
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.planner import extract_planning_facts
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.provider_template import validate_complete_provider_template
from app.design.services.deployment_diagram.provider_template_generation import (
    build_complete_provider_template,
)
from app.design.services.deployment_diagram.provider_template_generation import (
    provider_template_structure_digest as split_provider_template_structure_digest,
)
from app.design.services.deployment_diagram.provider_template_validation import (
    validate_complete_provider_template as validate_generated_provider_template,
)
from app.design.services.deployment_diagram.provisioning_renderer import (
    render_provisioning_dependencies,
)
from app.design.services.deployment_diagram.runtime_renderer import (
    render_runtime_deployment,
)
from app.implementation.delivery.iac_renderer import render_open_tofu

_ROOT = Path(__file__).resolve().parents[1]
_PROVIDERS: dict[str, dict[str, Any]] = {
    "aws": {
        "region": "ap-northeast-2",
        "zones": ["ap-northeast-2a", "ap-northeast-2b"],
    },
    "azure": {"region": "koreacentral", "zones": ["1", "2"]},
    "gcp": {
        "region": "asia-northeast3",
        "zones": ["asia-northeast3-a", "asia-northeast3-b"],
    },
}


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
                "decision": "Preserved explicit workloads.",
                "sourceRefs": ["planningFact:workloads"],
            }
        ],
    }


def _capability_contract() -> dict[str, Any]:
    return {
        "capabilities": [
            {
                "id": "availability",
                "decision": "accepted",
                "necessity": "required",
                "origin": "explicit",
                "requirementIds": ["R-HA"],
                "evidenceSpans": ["두 zone에서 자동 복구한다."],
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
                    },
                ],
            }
        ]
    }


def _resource_spec(provider: str) -> dict[str, Any]:
    target = _PROVIDERS[provider]
    return {
        "schemaVersion": "4",
        "workloads": ["vm"],
        "provider": provider,
        "region": target["region"],
        "candidateZones": target["zones"],
        "selectedZones": target["zones"],
        "monthlyBudgetUSD": 500,
        "minVCpu": 1,
        "minMemoryGiB": 1,
    }


def _projection_outputs(provider: str) -> dict[str, Any]:
    spec = _resource_spec(provider)
    facts = extract_planning_facts(
        capability_contract=_capability_contract(),
        resource_spec=spec,
        api_spec={"paths": {"/orders": {"get": {}}}},
        erd_model={"Classes": [{"className": "Order"}]},
    )
    bundle = build_deployment_diagram_bundle(
        _candidate(),
        spec,
        planning_facts=facts,
    )
    projection = bundle["projections"][0]
    resource_plan = projection["resourcePlan"]
    validate_complete_provider_template(resource_plan)
    runtime_puml = deployment_bundle_runtime_puml(bundle)
    provisioning_puml = deployment_bundle_provisioning_puml(bundle)
    iac_files = render_open_tofu(resource_plan)
    return {
        "bundle": bundle,
        "resourcePlan": resource_plan,
        "runtimePuml": runtime_puml,
        "provisioningPuml": provisioning_puml,
        "iacFiles": iac_files,
    }


def test_split_projection_public_boundaries_match_compatibility_facades() -> None:
    assert template_facade.build_complete_provider_template is (
        build_complete_provider_template
    )
    assert template_facade.provider_template_structure_digest is (
        split_provider_template_structure_digest
    )
    assert template_facade.validate_complete_provider_template is (
        validate_generated_provider_template
    )
    assert plantuml_facade.render_runtime_deployment is render_runtime_deployment
    assert plantuml_facade.render_provisioning_dependencies is (
        render_provisioning_dependencies
    )

    for provider, target in _PROVIDERS.items():
        outputs = _projection_outputs(provider)
        bundle = outputs["bundle"]
        projection = bundle["projections"][0]
        generated = build_complete_provider_template(
            projection["deploymentPlan"],
            bundle["workloadGraph"],
            provider=provider,
            region=target["region"],
        )

        validate_generated_provider_template(generated)
        assert generated == outputs["resourcePlan"]
        assert render_runtime_deployment(bundle) == outputs["runtimePuml"]
        assert render_provisioning_dependencies(bundle) == outputs[
            "provisioningPuml"
        ]


def test_projection_modules_have_no_llm_or_outer_stage_imports() -> None:
    module_paths = (
        (
            _ROOT
            / "app/design/services/deployment_diagram/provider_template_generation.py",
            True,
        ),
        (
            _ROOT
            / "app/design/services/deployment_diagram/provider_template_validation.py",
            True,
        ),
        (
            _ROOT / "app/design/services/deployment_diagram/runtime_renderer.py",
            True,
        ),
        (
            _ROOT / "app/design/services/deployment_diagram/provisioning_renderer.py",
            True,
        ),
        (
            _ROOT / "app/design/services/deployment_diagram/renderer_support.py",
            True,
        ),
        (
            _ROOT / "app/design/services/deployment_diagram/provider_template.py",
            True,
        ),
        (
            _ROOT / "app/design/services/deployment_diagram/provider_plantuml.py",
            True,
        ),
        (_ROOT / "app/implementation/delivery/iac_renderer.py", False),
    )
    forbidden = (
        "app.design.graphs",
        "app.repositories",
        "app.requirements",
    )
    violations: list[str] = []

    for path, forbid_implementation in module_paths:
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
                f"{path.name}:{node.lineno}:{name}"
                for name in imported
                if ".structured" in name
                or ".prompts" in name
                or name.startswith(("app.llm", "openai"))
                or name.startswith(forbidden)
                or (
                    forbid_implementation
                    and name.startswith("app.implementation")
                )
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
            violations.append(f"{path.name}:parse_structured call")

    assert not violations, "forbidden projection dependencies: " + ", ".join(
        violations
    )
