"""Generate the WorkloadGraph decision-coverage deployment corpus.

The cases exercise policy-derived placement instead of enumerating closed
topology families. The corpus is partitioned only by provider. Each case has
two deliberately separate views:

* runtime: placement plus request/data flow
* provisioning: prerequisite -> dependent creation relationships

ResourcePlan is the source for both diagrams and OpenTofu; PUML is not parsed
back into IaC. SVG is rendered with the exact PlantUML image digest used by the
application. ``--check`` renders into one system temporary directory and
byte-compares the result with the existing generated corpus.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.design.services.common.plantuml import PLANTUML_IMAGE  # noqa: E402
from app.design.services.deployment_diagram.bundle import (  # noqa: E402
    build_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.provider_plantuml import (  # noqa: E402
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)

OUTPUT_ROOT = ROOT / "docs" / "examples" / "deployment-diagrams"
TARGETS = {
    "aws": ("ap-northeast-2", ("ap-northeast-2a", "ap-northeast-2b")),
    "azure": ("koreacentral", ("1", "2")),
    "gcp": ("asia-northeast3", ("asia-northeast3-a", "asia-northeast3-b")),
}


def _workload(workload_id: str, *, public: bool, safety: str = "singleton") -> dict[str, Any]:
    return {
        "id": workload_id,
        "name": workload_id.title(),
        "artifact": {"kind": "generatedApplication"},
        "interfaces": [
            {
                "id": "http",
                "protocol": "http",
                "exposure": "public" if public else "internal",
                "sourceRefs": [f"api:{workload_id}"],
            }
        ],
        "storage": [],
        "configuration": [],
        "resourceRequirements": {},
        "replicationSafety": safety,
        "sourceRefs": [f"class:{workload_id}"],
    }


def _state_workload() -> dict[str, Any]:
    workload = _workload("state", public=False)
    workload["name"] = "State Service"
    workload["artifact"] = {
        "kind": "prebuiltImage",
        "image": "registry.example/state-service@sha256:" + "1" * 64,
        "engine": "explicit-state-service",
        "deploymentMode": "container",
        "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
    }
    workload["interfaces"] = [
        {
            "id": "service",
            "protocol": "http",
            "exposure": "internal",
            "sourceRefs": ["requirement:STATE-SERVICE"],
        }
    ]
    workload["storage"] = [
        {
            "id": "state-volume",
            "persistence": "persistent",
            "capacityGiB": 20,
            "mountPath": "/var/lib/easydep/state",
            "deletionPolicy": "retain",
            "replicaSemantics": "singleAttachment",
            "sourceRefs": ["requirement:STATE-DATA"],
        }
    ]
    workload["sourceRefs"] = ["requirement:STATE-SERVICE"]
    return workload


def _kebab(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", value).lower()


def semantic_case_id(
    *,
    compute_kind: str,
    compute_units: int,
    replicas: int,
    zones: int,
    workload_count: int,
    persistent_workload_count: int,
    colocate_relation_count: int,
    separate_relation_count: int,
    ingress_kind: str,
    secret_binding_count: int = 0,
    per_replica_storage_count: int = 0,
) -> str:
    """Return the corpus ID from normalized planning facts and relation constraints."""

    compute = _kebab(compute_kind)
    ingress = _kebab(ingress_kind)
    relations = "-".join(
        token
        for token in (
            f"colocate{colocate_relation_count}" if colocate_relation_count else "",
            f"separate{separate_relation_count}" if separate_relation_count else "",
        )
        if token
    ) or "none"
    dependencies = "-".join(
        token
        for token in (
            f"secret{secret_binding_count}" if secret_binding_count else "",
            (
                f"per-replica-storage{per_replica_storage_count}"
                if per_replica_storage_count
                else ""
            ),
        )
        if token
    )
    case_id = (
        f"{compute}-cu{compute_units}-r{replicas}-z{zones}"
        f"-w{workload_count}-pw{persistent_workload_count}"
        f".relations-{relations}.{ingress}"
    )
    return f"{case_id}.bindings-{dependencies}" if dependencies else case_id


def _expectation(
    *,
    compute_kind: str,
    replicas: int,
    zones: int,
    compute_units: int,
    workload_count: int,
    persistent_workload_count: int,
    colocate_relation_count: int = 0,
    separate_relation_count: int = 0,
    ingress_kind: str,
    secret_binding_count: int = 0,
    per_replica_storage_count: int = 0,
) -> tuple[str, dict[str, Any]]:
    expectation = {
        "computeKind": compute_kind,
        "replicaCount": replicas,
        "zoneCount": zones,
        "computeUnitCount": compute_units,
        "workloadCount": workload_count,
        "persistentWorkloadCount": persistent_workload_count,
        "colocateRelationCount": colocate_relation_count,
        "separateRelationCount": separate_relation_count,
        "ingressKind": ingress_kind,
        "secretBindingCount": secret_binding_count,
        "perReplicaStorageCount": per_replica_storage_count,
    }
    return semantic_case_id(
        compute_kind=compute_kind,
        compute_units=compute_units,
        replicas=replicas,
        zones=zones,
        workload_count=workload_count,
        persistent_workload_count=persistent_workload_count,
        colocate_relation_count=colocate_relation_count,
        separate_relation_count=separate_relation_count,
        ingress_kind=ingress_kind,
        secret_binding_count=secret_binding_count,
        per_replica_storage_count=per_replica_storage_count,
    ), expectation


CASE_EXPECTATIONS: dict[str, dict[str, Any]] = dict(
    [
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="directPublicIp",
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=2,
            persistent_workload_count=1,
            ingress_kind="directPublicIp",
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=1,
            separate_relation_count=1,
            ingress_kind="directPublicIp",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=1,
            zones=1,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=1,
            separate_relation_count=1,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=2,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=2,
            zones=1,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=1,
            separate_relation_count=1,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=2,
            zones=2,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=2,
            zones=2,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=1,
            separate_relation_count=1,
            ingress_kind="loadBalancer",
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="privateEgressOnly",
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=1,
            ingress_kind="directPublicIp",
            per_replica_storage_count=1,
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="directPublicIp",
            secret_binding_count=1,
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=1,
            workload_count=2,
            persistent_workload_count=0,
            ingress_kind="directPublicIp",
        ),
        _expectation(
            compute_kind="standaloneVm",
            replicas=1,
            zones=1,
            compute_units=2,
            workload_count=2,
            persistent_workload_count=0,
            separate_relation_count=1,
            ingress_kind="directPublicIp",
        ),
        _expectation(
            compute_kind="managedVmGroup",
            replicas=2,
            zones=1,
            compute_units=1,
            workload_count=1,
            persistent_workload_count=0,
            ingress_kind="privateEgressOnly",
        ),
    ]
)
DEPLOYMENT_CASES = tuple(CASE_EXPECTATIONS)


def _graph(case: str) -> dict[str, Any]:
    if case not in DEPLOYMENT_CASES:
        raise ValueError(f"Unknown deployment example: {case}")
    expectation = CASE_EXPECTATIONS[case]
    workload_count = int(expectation["workloadCount"])
    persistent_workload_count = int(expectation["persistentWorkloadCount"])
    workloads = [
        _workload(
            "web",
            public=expectation["ingressKind"] != "privateEgressOnly",
        )
    ]
    connections: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    if workload_count == 2 and persistent_workload_count == 0:
        workloads.append(_workload("worker", public=False))
    if persistent_workload_count == 1:
        workloads.append(_state_workload())
        workloads[0]["configuration"].append(
            {
                "id": "state-service-url",
                "name": "STATE_SERVICE_URL",
                "kind": "endpointBinding",
                "connectionRef": "web-to-state",
                "projection": "url",
                "sourceRefs": ["sequence:STATE-ACCESS"],
            }
        )
        connections.append(
            {
                "id": "web-to-state",
                "sourceRef": "web",
                "targetRef": "state",
                "targetInterfaceRef": "service",
                "protocol": "http",
                "sourceRefs": ["sequence:STATE-ACCESS"],
            }
        )
    if expectation["perReplicaStorageCount"]:
        state = next(item for item in workloads if item["id"] == "state")
        state["replicationSafety"] = "interchangeable"
        state["storage"][0]["replicaSemantics"] = "perReplica"
        constraints.append(
            {
                "id": "data-replicas",
                "kind": "replicaCount",
                "workloadRefs": ["state"],
                "value": 2,
                "sourceRefs": ["requirement:DATA-HA"],
            }
        )
    if expectation["secretBindingCount"]:
        workloads[0]["configuration"].append(
            {
                "id": "api-token",
                "name": "API_TOKEN",
                "kind": "secretBinding",
                "sensitive": True,
                "sourceRefs": ["requirement:SECRET"],
            }
        )
    related_workload = "state" if persistent_workload_count else "worker"
    if expectation["separateRelationCount"]:
        constraints.append(
            {
                "id": f"separate-web-{related_workload}",
                "kind": "separate",
                "workloadRefs": ["web", related_workload],
                "value": True,
                "sourceRefs": ["requirement:NFR-ISO"],
            }
        )
    if expectation["colocateRelationCount"]:
        constraints.append(
            {
                "id": f"colocate-web-{related_workload}",
                "kind": "colocate",
                "workloadRefs": ["web", related_workload],
                "value": True,
                "sourceRefs": ["requirement:NFR-COLOCATE"],
            }
        )
    if expectation["replicaCount"] > 1:
        workloads[0]["replicationSafety"] = "interchangeable"
        constraints.append(
            {
                "id": "replicas",
                "kind": "replicaCount",
                "workloadRefs": ["web"],
                "value": expectation["replicaCount"],
                "sourceRefs": ["requirement:NFR-REP"],
            }
        )
    if (
        expectation["computeKind"] == "managedVmGroup"
        and expectation["replicaCount"] == 1
    ):
        constraints.append(
            {
                "id": "replacement",
                "kind": "managedReplacement",
                "workloadRefs": ["web"],
                "value": True,
                "sourceRefs": ["requirement:NFR-REPLACE"],
            }
        )
    if expectation["zoneCount"] > 1:
        constraints.append(
            {
                "id": "zones",
                "kind": "zoneSpread",
                "workloadRefs": ["web"],
                "value": {"minimumZones": expectation["zoneCount"]},
                "sourceRefs": ["requirement:NFR-ZONE"],
            }
        )
    return {
        "schemaVersion": "easydep-workload-graph",
        "workloads": workloads,
        "externalDependencies": [],
        "connections": connections,
        "constraints": constraints,
        "derivations": [],
    }


def _resource_spec(provider: str) -> dict[str, Any]:
    region, available_zones = TARGETS[provider]
    return {
        "schemaVersion": "4",
        "workloads": ["vm"],
        "provider": provider,
        "region": region,
        "candidateZones": list(available_zones),
    }


def _relative_sources() -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for provider in TARGETS:
        for case in DEPLOYMENT_CASES:
            workload_graph = _graph(case)
            resource_spec = _resource_spec(provider)
            first = build_deployment_diagram_bundle(workload_graph, resource_spec)
            second = build_deployment_diagram_bundle(workload_graph, resource_spec)
            if first != second:
                raise RuntimeError(
                    f"Bundle generation is not deterministic: {provider}/{case}"
                )
            projection = first["projections"][0]
            if projection.get("status") != "completed":
                raise RuntimeError(f"Unresolved example {provider}/{case}: {projection}")
            provider_dir = Path(provider)
            outputs[provider_dir / f"{case}.runtime.puml"] = (
                deployment_bundle_runtime_puml(first).rstrip() + "\n"
            )
            outputs[provider_dir / f"{case}.provisioning.puml"] = (
                deployment_bundle_provisioning_puml(first).rstrip() + "\n"
            )
    if len(outputs) != len(TARGETS) * len(DEPLOYMENT_CASES) * 2:
        raise RuntimeError(f"Unexpected semantic view count: {len(outputs)}.")
    return outputs


def _write_sources(root: Path, sources: dict[Path, str]) -> None:
    for relative, source in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="\n")


def _render_svgs(root: Path, sources: dict[Path, str]) -> None:
    container_inputs = [f"/work/{path.as_posix()}" for path in sorted(sources)]
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{root.resolve()}:/work",
        PLANTUML_IMAGE,
        "-charset",
        "UTF-8",
        "-tsvg",
        *container_inputs,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"PlantUML SVG rendering failed:\n{detail}")
    missing = [
        relative.with_suffix(".svg")
        for relative in sources
        if not (root / relative.with_suffix(".svg")).is_file()
    ]
    if missing:
        raise RuntimeError(f"PlantUML did not create {len(missing)} SVG files: {missing}")


def _expected_files(sources: dict[Path, str]) -> set[Path]:
    return set(sources) | {path.with_suffix(".svg") for path in sources}


def _prune_stale_generated_files(root: Path, expected: set[Path]) -> None:
    if not root.exists():
        return
    unexpected = {path.relative_to(root) for path in root.rglob("*") if path.is_file()} - expected
    unmanaged = sorted(path for path in unexpected if path.suffix not in {".puml", ".svg"})
    if unmanaged:
        raise RuntimeError(f"Refusing to delete unmanaged corpus files: {unmanaged}")
    for relative in sorted(unexpected):
        (root / relative).unlink()


def generate() -> None:
    sources = _relative_sources()
    _prune_stale_generated_files(OUTPUT_ROOT, _expected_files(sources))
    _write_sources(OUTPUT_ROOT, sources)
    _render_svgs(OUTPUT_ROOT, sources)
    actual = {path.relative_to(OUTPUT_ROOT) for path in OUTPUT_ROOT.rglob("*") if path.is_file()}
    unexpected = actual - _expected_files(sources)
    if unexpected:
        raise RuntimeError(f"Unexpected files in example corpus: {sorted(unexpected)}")
    print(
        f"Generated {len(sources)} diagrams / {len(actual)} PUML+SVG files with {PLANTUML_IMAGE}."
    )


def check() -> None:
    sources = _relative_sources()
    expected = _expected_files(sources)
    checked_in = {
        path.relative_to(OUTPUT_ROOT) for path in OUTPUT_ROOT.rglob("*") if path.is_file()
    }
    if checked_in != expected:
        missing = sorted(expected - checked_in)
        extra = sorted(checked_in - expected)
        raise RuntimeError(f"Corpus file mismatch. Missing={missing}; extra={extra}")
    with tempfile.TemporaryDirectory(prefix="easydep-diagram-examples-") as temp_name:
        temporary_root = Path(temp_name)
        _write_sources(temporary_root, sources)
        _render_svgs(temporary_root, sources)
        mismatches = [
            relative
            for relative in sorted(expected)
            if (OUTPUT_ROOT / relative).read_bytes() != (temporary_root / relative).read_bytes()
        ]
    if mismatches:
        raise RuntimeError(f"Non-reproducible checked-in diagrams: {mismatches}")
    print(
        f"Verified {len(sources)} deterministic diagrams / {len(expected)} files "
        f"with {PLANTUML_IMAGE}."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in a temporary directory and byte-compare with checked-in files.",
    )
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        generate()


if __name__ == "__main__":
    main()
