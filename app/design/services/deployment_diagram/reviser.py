"""Apply user feedback to WorkloadGraph only; all plans are regenerated."""

from __future__ import annotations

from typing import Any

from app.design.services.common.structured import parse_structured, revision_messages
from app.design.services.deployment_diagram.extractor import DeploymentModel

DEPLOYMENT_REVISION_SYSTEM_PROMPT = """
You edit an existing WorkloadGraph candidate. Apply the user's feedback and
return the complete graph.

- Change only WorkloadGraph. Never add or edit VMs, VM groups, cloud disks, load
  balancers, public IPs, NAT, firewall resources, or any CSP resource.
- Keep every workload, external dependency, connection, interface, storage item,
  configuration item, and typed constraint grounded with sourceRefs.
- Never create a database workload, engine, image, or storage from an ERD alone.
- Actors and browsers are not workloads.
- Do not guess exposure, protocol, port, retention, replica safety, data execution
  mode, or prebuilt image details. Keep ambiguity for deterministic validation.
- Every connection endpoint and referenced interface must exist in the full graph.
- Every generated source workload connection has exactly one endpointBinding
  configuration. Choose a stable id, a unique UPPER_SNAKE_CASE environment name,
  and a url/host/port projection; never guess the endpoint value.
- A generated application's persistent storage has a design-selected absolute
  POSIX mountPath. Secret bindings have environment names but never secret values.

Return the full graph according to the response schema. Include no markdown or
prose outside schema fields.
"""


def revise_deployment_model(
    current_model: dict[str, Any],
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
) -> dict[str, Any]:
    if not current_model or not feedback:
        return current_model or {}
    return parse_structured(
        revision_messages(
            DEPLOYMENT_REVISION_SYSTEM_PROMPT,
            "Design Artifacts",
            context_text,
            "Current WorkloadGraph",
            current_model,
            feedback,
            targets,
        ),
        DeploymentModel,
    )
