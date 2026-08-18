"""Compatibility imports for the design-owned deployment topology model.

New code should import from ``app.design.services.deployment_diagram.topology``.
This module remains temporarily so implementation and older integrations can move
without a flag day.
"""

from app.design.services.deployment_diagram.topology import (  # noqa: F401
    COMPUTE_PROFILES,
    PROVIDERS,
    PUBLIC_INGRESS_MODES,
    WORKLOAD_LAYOUTS,
    TopologyFamily,
    derive_deployment_topology,
    enumerate_topology_families,
    family_errors,
    provider_projection_policy,
)

__all__ = [
    "COMPUTE_PROFILES",
    "PROVIDERS",
    "PUBLIC_INGRESS_MODES",
    "WORKLOAD_LAYOUTS",
    "TopologyFamily",
    "derive_deployment_topology",
    "enumerate_topology_families",
    "family_errors",
    "provider_projection_policy",
]
