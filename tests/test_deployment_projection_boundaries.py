"""provider projection과 배포 renderer의 분리 전후 byte 계약을 검증한다."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.design import service as design_service
from app.design.services.deployment_diagram import (
    provider_plantuml as plantuml_facade,
)
from app.design.services.deployment_diagram import (
    provider_template as template_facade,
)
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    select_deployment_target,
)
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.planner import extract_planning_facts
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.provider_template import (
    validate_complete_provider_template,
)
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
from app.design.services.deployment_diagram.sizing import (
    apply_capacity_overrides,
    apply_compute_selections,
    compute_sizing_guidance,
)
from app.implementation.delivery.iac_renderer import render_open_tofu
from app.implementation.delivery.terraform import render_iac

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


def test_selected_target_drives_multi_projection_diagrams() -> None:
    spec = _resource_spec("aws")
    spec["deploymentTargets"] = [
        {"provider": provider, **target}
        for provider, target in list(_PROVIDERS.items())[:2]
    ]
    facts = extract_planning_facts(
        capability_contract=_capability_contract(),
        resource_spec=spec,
        api_spec={"paths": {"/orders": {"get": {}}}},
        erd_model={"Classes": [{"className": "Order"}]},
    )
    bundle = build_deployment_diagram_bundle(
        _candidate(), spec, planning_facts=facts
    )

    assert "Multiple provider alternatives" in deployment_bundle_runtime_puml(bundle)

    azure_target = bundle["projections"][1]["target"]
    selected = select_deployment_target(bundle, azure_target)
    runtime = deployment_bundle_runtime_puml(selected)
    provisioning = deployment_bundle_provisioning_puml(selected)

    assert "Microsoft Azure" in runtime
    assert "Microsoft Azure" in provisioning
    assert "Deployment target unresolved" not in runtime


def test_selected_resource_plan_has_one_canonical_iac_directory(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = _projection_outputs("aws")["bundle"]
    bundle_path = tmp_path / "deployment-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(
        "app.implementation.delivery.package._format_open_tofu", lambda _directory: None
    )
    monkeypatch.setattr(
        "app.implementation.delivery.terraform.check_deployment_package",
        lambda *_args, **_kwargs: {"gateStatus": "PASS", "issues": []},
    )

    report = render_iac(
        tmp_path / "run",
        SimpleNamespace(inputs={"deploymentBundle": bundle_path}),
    )

    assert report["deploymentPackage"] == "deployment"
    assert (tmp_path / "run/application/deployment/tofu/main.tf").is_file()
    assert not (tmp_path / "run/application/terraform").exists()


def test_compute_choices_reproject_without_private_constraint_kind() -> None:
    bundle = _projection_outputs("aws")["bundle"]
    projection = bundle["projections"][0]
    guidance = compute_sizing_guidance(
        projection["deploymentPlan"],
        provider="aws",
        region=_PROVIDERS["aws"]["region"],
        workload_graph=bundle["workloadGraph"],
        limit=1,
    )
    assert guidance["computeUnits"]
    assert all(unit["candidates"] for unit in guidance["computeUnits"])
    selections = [
        {
            "computeUnitId": unit["computeUnitId"],
            "sku": unit["candidates"][0]["sku"],
            "replicaCount": unit["minimumReplicaCount"],
            "replicationConfirmed": False,
        }
        for unit in guidance["computeUnits"]
    ]

    selected = apply_compute_selections(bundle, selections)

    WorkloadGraph.model_validate(selected["workloadGraph"])
    assert not any(
        item.get("kind") == "replicationConfirmation"
        for item in selected["workloadGraph"]["constraints"]
    )
    assert selected["sizing"]["status"] == "completed"


def test_target_zones_replace_primary_zone_context_including_empty_target() -> None:
    spec = _resource_spec("aws")
    spec["candidateZones"] = ["primary-zone-a", "primary-zone-b"]
    spec["selectedZones"] = ["primary-zone-a", "primary-zone-b"]
    spec["deploymentTargets"] = [
        {"provider": "aws", **_PROVIDERS["aws"]},
        {"provider": "azure", "region": "koreacentral", "zones": []},
    ]
    bundle = build_deployment_diagram_bundle(
        _candidate(),
        spec,
        planning_facts=extract_planning_facts(
            capability_contract=_capability_contract(), resource_spec=spec
        ),
    )
    projections = {
        item["provider"]: item for item in bundle["projections"]
    }

    aws = projections["aws"]
    assert aws["target"]["zones"] == _PROVIDERS["aws"]["zones"]
    assert aws["planningContext"]["candidateZones"] == _PROVIDERS["aws"]["zones"]
    assert aws["deploymentPlan"]["locationPlan"]["selectedZones"] == _PROVIDERS["aws"]["zones"]

    azure = projections["azure"]
    assert azure["target"]["zones"] == []
    assert azure["planningContext"]["candidateZones"][:2] == ["1", "2"]
    assert azure["deploymentPlan"]["locationPlan"]["selectedZones"] == ["1", "2"]
    assert azure["deploymentPlan"]["locationPlan"]["zonePolicy"] == "catalogBased"
    assert azure["status"] == "completed"


def test_sizing_selections_are_stored_and_retrieved_per_target(monkeypatch) -> None:
    spec = _resource_spec("aws")
    spec["deploymentTargets"] = [
        {"provider": provider, **target}
        for provider, target in list(_PROVIDERS.items())[:2]
    ]
    bundle = build_deployment_diagram_bundle(
        _candidate(),
        spec,
        planning_facts=extract_planning_facts(
            capability_contract=_capability_contract(), resource_spec=spec
        ),
    )
    aws_target = bundle["projections"][0]["target"]
    aws_projection = bundle["projections"][0]
    guidance = compute_sizing_guidance(
        aws_projection["deploymentPlan"],
        provider="aws",
        region=_PROVIDERS["aws"]["region"],
        workload_graph=bundle["workloadGraph"],
        limit=1,
    )
    selections = [
        {
            "computeUnitId": item["computeUnitId"],
            "sku": item["candidates"][0]["sku"],
            "replicaCount": item["minimumReplicaCount"],
            "replicationConfirmed": False,
        }
        for item in guidance["computeUnits"]
    ]

    updated = apply_compute_selections(
        bundle, selections, selected_target=aws_target["id"]
    )
    # Application returns a new bundle only after every compute selection is
    # accepted; the stored source is untouched until its caller persists it.
    assert all("sizing" not in item for item in bundle["projections"])
    aws_projection = next(
        item for item in updated["projections"] if item["target"] == aws_target
    )
    azure_target = updated["projections"][1]["target"]
    azure_projection = updated["projections"][1]
    assert aws_projection["sizing"]["selected"] == selections
    assert "sizing" not in azure_projection
    assert updated["sizing"]["target"] == aws_target

    monkeypatch.setattr(
        design_service,
        "_load_app",
        lambda _app_id: {"deployment_diagram_bundle": updated},
    )
    retrieved = design_service.deployment_sizing_session(
        "00000000-0000-4000-8000-000000000001", azure_target["id"]
    )
    assert retrieved["target"] == azure_target
    assert retrieved["selected"] == []
    selected_azure = select_deployment_target(updated, azure_target["id"])
    selected_azure_projection = next(
        item
        for item in selected_azure["projections"]
        if item["target"] == azure_target
    )
    assert retrieved["structureDigest"] == selected_azure_projection[
        "deploymentPlanStructureDigest"
    ]
    with pytest.raises(ValueError, match="preview changed"):
        design_service.apply_deployment_sizing_session(
            "00000000-0000-4000-8000-000000000001",
            azure_target["id"],
            selections,
            expected_structure_digest="stale-preview",
        )


def test_capacity_overrides_reprice_and_persist_without_changing_topology(monkeypatch) -> None:
    bundle = _projection_outputs("aws")["bundle"]
    projection = bundle["projections"][0]
    original_digest = projection["deploymentPlanStructureDigest"]
    baseline = compute_sizing_guidance(
        projection["deploymentPlan"],
        provider="aws",
        region=_PROVIDERS["aws"]["region"],
        workload_graph=bundle["workloadGraph"],
        limit=5,
    )
    first = baseline["computeUnits"][0]
    candidate = first["candidates"][0]
    overrides = [
        {
            "computeUnitId": first["computeUnitId"],
            "minVCpu": candidate["vCPU"],
            "minMemoryGiB": candidate["memoryGiB"],
        }
    ]
    capacity_plan, _ = apply_capacity_overrides(projection["deploymentPlan"], overrides)
    preview = compute_sizing_guidance(
        projection["deploymentPlan"],
        provider="aws",
        region=_PROVIDERS["aws"]["region"],
        workload_graph=bundle["workloadGraph"],
        capacity_overrides=overrides,
        limit=5,
    )
    selections = [
        {
            "computeUnitId": item["computeUnitId"],
            "sku": item["candidates"][0]["sku"],
            "replicaCount": item["minimumReplicaCount"],
            "replicationConfirmed": False,
        }
        for item in preview["computeUnits"]
    ]

    updated = apply_compute_selections(
        bundle,
        selections,
        capacity_overrides=overrides,
    )
    stored = updated["projections"][0]
    stored_compute = next(
        item
        for item in stored["deploymentPlan"]["computeUnits"]
        if item["id"] == first["computeUnitId"]
    )

    assert capacity_plan["structureDigest"] == original_digest
    assert stored["deploymentPlanStructureDigest"] == stored["deploymentPlan"]["structureDigest"]
    assert stored_compute["resourceRequirements"] == {
        "minVCpu": candidate["vCPU"],
        "minMemoryGiB": candidate["memoryGiB"],
    }
    assert stored["sizing"]["capacityOverrides"] == overrides
    assert bundle["projections"][0].get("sizing") is None

    monkeypatch.setattr(
        design_service,
        "_load_app",
        lambda _app_id: {"deployment_diagram_bundle": updated},
    )
    retrieved = design_service.deployment_sizing_session(
        "00000000-0000-4000-8000-000000000001", stored["target"]["id"]
    )
    retrieved_compute = next(
        item
        for item in retrieved["guidance"]["computeUnits"]
        if item["computeUnitId"] == first["computeUnitId"]
    )

    assert retrieved["capacityOverrides"] == overrides
    assert retrieved_compute["minimumRequirements"] == stored_compute["resourceRequirements"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ([{"computeUnitId": "unknown", "minVCpu": 1, "minMemoryGiB": 1}], "Unknown compute"),
        (
            [
                {"computeUnitId": "compute-app", "minVCpu": 1, "minMemoryGiB": 1},
                {"computeUnitId": "compute-app", "minVCpu": 2, "minMemoryGiB": 2},
            ],
            "only one capacity override",
        ),
        ([{"computeUnitId": "compute-app", "minVCpu": 0, "minMemoryGiB": 1}], "Invalid capacity"),
        ([{"computeUnitId": "compute-app", "minVCpu": 1}], "Invalid capacity"),
    ],
)
def test_capacity_overrides_reject_invalid_or_incomplete_values(overrides, message) -> None:
    plan = _projection_outputs("aws")["bundle"]["projections"][0]["deploymentPlan"]

    with pytest.raises(ValueError, match=message):
        apply_capacity_overrides(plan, overrides)


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
