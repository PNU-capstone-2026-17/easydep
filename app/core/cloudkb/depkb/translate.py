"""Translate a Docker workload intent into VM dependency anchors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Translation:
    anchors: tuple[str, ...]
    rationale: tuple[tuple[str, str], ...] = ()
    open_questions: tuple[str, ...] = ()
    unmeasured: tuple[str, ...] = ()
    ignored: tuple[tuple[str, str], ...] = ()


def translate(deployment_intent: dict) -> Translation:
    """Read only topology decisions already present in a Docker deployment intent.

    Every workload needs a VM. Persistence and external-entry anchors are added only
    when the intent explicitly states them; this function does not infer VM size or count.
    """
    workloads = deployment_intent.get("workloads") or []
    if not workloads:
        return Translation(
            anchors=(),
            open_questions=(
                "The deployment intent has no Docker workloads to place on a VM.",
            ),
        )

    anchors: dict[str, str] = {
        "vm": "A Docker workload requires a VM host in the selected research scope."
    }
    questions: list[str] = []
    ignored: dict[str, str] = {}

    for workload in workloads:
        name = str(workload.get("name") or "unnamed workload")
        capabilities = workload.get("capabilities") or {}
        persistent = bool(
            workload.get("persistent")
            or workload.get("persistentVolume")
            or capabilities.get("persistence")
        )
        external = bool(
            workload.get("external")
            or workload.get("ingress")
            or capabilities.get("ingress")
        )
        high_availability = bool(
            workload.get("highAvailability")
            or capabilities.get("highAvailability")
        )
        if persistent:
            anchors.setdefault("disk", f"`{name}` explicitly requires persistent data.")
        if high_availability:
            anchors.setdefault(
                "loadBalancer",
                f"`{name}` explicitly requires a high-availability external entry point.",
            )
        elif external:
            anchors.setdefault(
                "publicIp", f"`{name}` explicitly requires an external entry point."
            )

        for key in ("replicas", "cpu", "memory", "instanceType"):
            if key in workload:
                ignored.setdefault(
                    key,
                    "Capacity and concrete VM selection are handled after design.",
                )
        kind = workload.get("kind")
        if kind in {"Deployment", "StatefulSet", "DaemonSet", "CronJob"}:
            questions.append(
                f"`{name}` uses Kubernetes kind `{kind}`, which is outside the "
                "Docker-on-VM scope; provide a Docker workload description instead."
            )

    return Translation(
        anchors=tuple(sorted(anchors)),
        rationale=tuple(sorted(anchors.items())),
        open_questions=tuple(questions),
        ignored=tuple(sorted(ignored.items())),
    )
