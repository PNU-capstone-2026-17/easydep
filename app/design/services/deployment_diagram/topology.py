"""Design-owned finite Docker-on-VM deployment topology families.

This module describes concrete placement and ingress structure.  It deliberately
does not infer or claim high availability.  Exact replica and zone names are
attributes of a family, not additional families.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Literal

Provider = Literal["aws", "azure", "gcp"]
ComputeProfile = Literal[
    "standaloneOne",
    "managedGroupOne",
    "managedGroupManySingleZone",
    "managedGroupManyMultiZone",
]
WorkloadLayout = Literal["primaryOnly", "colocatedPersistent", "isolatedPersistent"]
PublicIngress = Literal["direct", "loadBalanced"]

PROVIDERS: tuple[Provider, ...] = ("aws", "azure", "gcp")
COMPUTE_PROFILES: tuple[ComputeProfile, ...] = (
    "standaloneOne",
    "managedGroupOne",
    "managedGroupManySingleZone",
    "managedGroupManyMultiZone",
)
WORKLOAD_LAYOUTS: tuple[WorkloadLayout, ...] = (
    "primaryOnly",
    "colocatedPersistent",
    "isolatedPersistent",
)
PUBLIC_INGRESS_MODES: tuple[PublicIngress, ...] = ("direct", "loadBalanced")

_NATIVE_GROUPS = {
    "aws": "autoscaling-group",
    "azure": "virtual-machine-scale-set",
    "gcp": "regional-managed-instance-group",
}


@dataclass(frozen=True)
class TopologyFamily:
    compute_profile: ComputeProfile
    workload_layout: WorkloadLayout
    public_ingress: PublicIngress
    provider: Provider | None = None

    @property
    def id(self) -> str:
        provider = f"{self.provider}." if self.provider else ""
        return f"{provider}{self.compute_profile}.{self.workload_layout}.{self.public_ingress}"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "provider": self.provider,
            "computeProfile": self.compute_profile,
            "workloadLayout": self.workload_layout,
            "publicIngress": self.public_ingress,
        }


def family_errors(family: TopologyFamily) -> list[str]:
    """Return hard structural errors for a proposed family."""
    errors: list[str] = []
    grouped = family.compute_profile != "standaloneOne"
    if not grouped and family.public_ingress == "loadBalanced":
        errors.append("standalone-load-balanced-ingress-is-unsupported")
    if grouped and family.public_ingress == "direct":
        errors.append("managed-group-direct-ingress-is-unsupported")
    if grouped and family.workload_layout == "colocatedPersistent":
        errors.append("managed-group-colocated-persistent-workload-is-unsupported")
    if family.provider is not None and family.provider not in PROVIDERS:
        errors.append("unsupported-provider")
    return errors


def enumerate_topology_families(*, include_providers: bool = False) -> list[TopologyFamily]:
    """Enumerate the 9 logical or 27 provider-labelled supported families."""
    providers: tuple[Provider | None, ...] = PROVIDERS if include_providers else (None,)
    candidates = (
        TopologyFamily(compute, layout, ingress, provider)
        for provider, compute, layout, ingress in product(
            providers,
            COMPUTE_PROFILES,
            WORKLOAD_LAYOUTS,
            PUBLIC_INGRESS_MODES,
        )
    )
    return [family for family in candidates if not family_errors(family)]


def _node_state_mode(node: dict[str, Any]) -> str:
    explicit = str(node.get("stateMode") or node.get("state_mode") or "").strip()
    if explicit:
        return explicit
    # Compatibility for stored logical models created before stateMode existed.
    if str(node.get("kind") or "").strip().lower() == "database":
        return "persistent"
    return "none"


def logical_persistent_workload_present(logical_model: dict[str, Any] | None) -> bool:
    model = logical_model or {}
    return any(
        _node_state_mode(node) == "persistent"
        for node in (model.get("Nodes") or model.get("nodes") or [])
        if isinstance(node, dict)
    )


def derive_deployment_topology(
    *,
    provider: str,
    resource_spec: dict[str, Any] | None = None,
    logical_deployment_model: dict[str, Any] | None = None,
    persistent_storage_required: bool = False,
) -> dict[str, Any]:
    """Normalize one selected topology and expose validation issues.

    Missing values use the least surprising compatibility defaults: one
    standalone VM, direct public ingress, and a dedicated PostgreSQL VM when an
    explicit persistent workload already exists. These defaults preserve current
    deployments while keeping the finite family bounded.
    """
    spec = resource_spec or {}
    normalized_provider = str(provider or "").strip().lower()
    compute_profile = str(spec.get("computeProfile") or "standaloneOne")
    selected_zones = [
        str(zone).strip() for zone in spec.get("selectedZones") or [] if str(zone).strip()
    ]
    persistent_workload_present = logical_persistent_workload_present(logical_deployment_model)
    requested_persistent_placement = str(
        spec.get("persistentWorkloadPlacement") or "separateCompute"
    )
    selected_ingress_zones = [
        str(zone).strip()
        for zone in spec.get("ingressZones") or selected_zones
        if str(zone).strip()
    ]

    issues: list[dict[str, str]] = []
    if len(selected_zones) != len(set(selected_zones)):
        issues.append(
            {
                "field": "selectedZones",
                "reason": "Selected zones must be distinct.",
                "classification": "invalid",
            }
        )
    if persistent_storage_required and not persistent_workload_present:
        issues.append(
            {
                "field": "applicationPersistence",
                "reason": (
                    "Writable primary-workload persistence is outside v1; an explicit "
                    "persistent workload must own the disk."
                ),
                "classification": "unsupported",
            }
        )
    if compute_profile not in COMPUTE_PROFILES:
        issues.append(
            {
                "field": "computeProfile",
                "reason": f"Unsupported compute profile: {compute_profile}",
                "classification": "invalid",
            }
        )
        compute_profile = "standaloneOne"
    grouped = compute_profile != "standaloneOne"
    workload_layout = (
        "primaryOnly"
        if not persistent_workload_present
        else "colocatedPersistent"
        if requested_persistent_placement == "colocate"
        else "isolatedPersistent"
    )
    public_ingress = str(spec.get("publicIngress") or ("loadBalanced" if grouped else "direct"))
    many = compute_profile in {
        "managedGroupManySingleZone",
        "managedGroupManyMultiZone",
    }
    spread = compute_profile == "managedGroupManyMultiZone"
    replica_count = spec.get("replicaCount")
    if isinstance(replica_count, bool) or not isinstance(replica_count, int):
        replica_count = 2 if many else 1
    if many and replica_count < 2:
        issues.append(
            {
                "field": "replicaCount",
                "reason": "A many-replica profile requires replicaCount >= 2.",
                "classification": "invalid",
            }
        )
    if not many and replica_count != 1:
        issues.append(
            {
                "field": "replicaCount",
                "reason": "A one-replica profile requires replicaCount = 1.",
                "classification": "invalid",
            }
        )
    if spread and len(set(selected_zones)) < 2:
        issues.append(
            {
                "field": "selectedZones",
                "reason": "A multi-zone spread profile requires at least two zones.",
                "classification": "needsInput",
            }
        )
    if not spread and len(selected_zones) > 1:
        issues.append(
            {
                "field": "selectedZones",
                "reason": "A single-zone profile may select at most one occupied zone.",
                "classification": "invalid",
            }
        )
    if requested_persistent_placement not in {"colocate", "separateCompute"}:
        issues.append(
            {
                "field": "persistentWorkloadPlacement",
                "reason": ("persistentWorkloadPlacement must be colocate or separateCompute."),
                "classification": "invalid",
            }
        )
        requested_persistent_placement = "separateCompute"
        workload_layout = "isolatedPersistent" if persistent_workload_present else "primaryOnly"
    if grouped and workload_layout == "colocatedPersistent":
        issues.append(
            {
                "field": "persistentWorkloadPlacement",
                "reason": "Persistent workloads cannot share a managed compute group in v1.",
                "classification": "unsupported",
            }
        )
    if public_ingress not in PUBLIC_INGRESS_MODES:
        issues.append(
            {
                "field": "publicIngress",
                "reason": f"Unsupported public ingress: {public_ingress}",
                "classification": "invalid",
            }
        )
        public_ingress = "loadBalanced" if grouped else "direct"
    if grouped and public_ingress == "direct":
        issues.append(
            {
                "field": "publicIngress",
                "reason": "Managed groups require one stable load-balanced endpoint.",
                "classification": "unsupported",
            }
        )
    if not grouped and public_ingress == "loadBalanced":
        issues.append(
            {
                "field": "publicIngress",
                "reason": "A standalone VM uses its reserved public address directly.",
                "classification": "unsupported",
            }
        )
    if many and spec.get("applicationStateless") is not True:
        issues.append(
            {
                "field": "applicationStateless",
                "reason": (
                    "Multiple replicas require evidence that local session, uploads, "
                    "singleton schedulers, and writable state are absent or externalized."
                ),
                "classification": "needsInput",
            }
        )

    topology = {
        "schemaVersion": "easydep-deployment-topology/v1",
        "provider": normalized_provider or None,
        "computeProfile": compute_profile,
        "computeManagement": "managedGroup" if grouped else "standalone",
        "replicaClass": "many" if many else "one",
        "replicaCount": replica_count,
        "zoneLayout": "multiZoneSpread" if spread else "singleZone",
        "selectedZones": selected_zones,
        "selectedIngressZones": selected_ingress_zones,
        "workloadLayout": workload_layout,
        "publicIngress": public_ingress,
        "publicEndpoint": {
            "required": True,
            "protocol": "http",
            "mode": public_ingress,
        },
        "persistentWorkloadPolicy": {
            "required": persistent_workload_present,
            "instanceCount": 1 if persistent_workload_present else 0,
            "publiclyReachable": False,
            "separatePersistentDisk": persistent_workload_present,
            "secretPolicy": ("runtimeContract" if persistent_workload_present else "notApplicable"),
        },
        "egressPolicy": {
            "mode": (
                "instancePublicAddress"
                if public_ingress == "direct" and workload_layout != "isolatedPersistent"
                else "hybridPublicAddressAndManagedNat"
                if public_ingress == "direct" and workload_layout == "isolatedPersistent"
                else "managedNat"
            ),
            "requiredFor": ["applicationImagePull"]
            + (["persistentWorkloadImagePull"] if persistent_workload_present else []),
        },
        "registryPolicy": {
            "mode": "providerNativePrivateRegistry",
            "provisionedBy": "userExecutedGeneratedIaC",
            "imageReference": "immutableDigest",
        },
        "bootImagePolicy": {
            "mode": "providerDefaultLinuxImage",
            "resolvedAt": "userExecutedTerraformPlan",
            "recordResolvedImageId": True,
        },
        "workloadEndpointPolicy": {
            "mode": (
                "fixedPrivateAddress"
                if workload_layout == "isolatedPersistent"
                else "containerLocal"
                if workload_layout == "colocatedPersistent"
                else "notApplicable"
            ),
            "applicationImageRebuildRequiredOnStateReplacement": False,
        },
        "secretPolicy": {
            "mode": (
                "callerManagedProviderSecretReference"
                if persistent_workload_present
                else "notApplicable"
            ),
            "credentialCollectionByEasyDep": False,
            "valueStoredByEasyDep": False,
            "requiredKeys": (
                ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
                if persistent_workload_present
                else []
            ),
        },
        "ownershipPolicy": "createDeploymentResources",
        "persistentStorageDeletionPolicy": "retainPersistentDisk",
        "autoscaling": False,
        "availabilityClaim": "none",
        "issues": issues,
    }
    topology["familyId"] = TopologyFamily(
        compute_profile=compute_profile,  # type: ignore[arg-type]
        workload_layout=workload_layout,  # type: ignore[arg-type]
        public_ingress=public_ingress,  # type: ignore[arg-type]
        provider=(normalized_provider if normalized_provider in PROVIDERS else None),  # type: ignore[arg-type]
    ).id
    return topology


def provider_projection_policy(topology: dict[str, Any]) -> dict[str, Any]:
    """Translate DeploymentTopology/v1 into provider projector inputs.

    This internal adapter carries placement, count, ingress, and backend-health
    requirements only; callers must not interpret it as an availability or SLA claim.
    """
    provider = str(topology.get("provider") or "")
    grouped = topology.get("computeManagement") == "managedGroup"
    spread = topology.get("zoneLayout") == "multiZoneSpread"
    replicas = int(topology.get("replicaCount") or 1)
    load_balanced = topology.get("publicIngress") == "loadBalanced"
    aws_multi_zone_ingress = provider == "aws" and load_balanced and spread
    return {
        "schemaVersion": "easydep-provider-projection-policy/v1",
        "source": "deployment-topology",
        "reason": "explicit-topology-family",
        "provider": provider or None,
        "mode": "managedGroup" if grouped else "standalone",
        "nativeComputeGroup": _NATIVE_GROUPS.get(provider) if grouped else None,
        "minimumInstances": replicas,
        "minimumZones": 2 if spread else 1,
        "minimumIngressZones": 2 if aws_multi_zone_ingress else 1,
        "minimumSubnets": 2 if provider == "aws" and spread else 1,
        "minimumIngressSubnets": 2 if aws_multi_zone_ingress else 1,
        "selectedIngressZones": list(topology.get("selectedIngressZones") or []),
        "zoneSpreadRequired": spread,
        "loadBalancerRequired": load_balanced,
        "backendHealthRequired": load_balanced,
        "sourceRefs": [],
    }
