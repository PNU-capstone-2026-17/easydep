from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def write_release_manifest(
    run_root: Path,
    *,
    workflow: dict[str, object],
    audit: dict[str, object],
    verification: dict[str, object],
    conformance: dict[str, object],
    traceability: dict[str, object],
    deployment: dict[str, object] | None,
    iac: dict[str, object] | None,
    container_smoke: dict[str, object],
) -> dict[str, object]:
    checks = {
        "workflow": workflow.get("status") == "COMPLETE",
        "completionAudit": audit.get("status") == "COMPLETE",
        "backendVerification": verification.get("status") == "SUCCEEDED",
        "sourceDesignConformance": conformance.get("status") == "PASSED",
        "traceability": traceability.get("summary", {}).get("missing") == 0,
        "deployment": deployment is None
        or (
            deployment.get("validation", {}).get("status")
            in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}
            and deployment.get("sourceConformance", {}).get("status")
            in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}
        ),
        "iac": iac is None
        or (
            iac.get("sourceConformance", {}).get("status") == "SUCCEEDED"
            and iac.get("terraformValidation", {}).get("status") == "SUCCEEDED"
        ),
        "containerRuntime": container_smoke.get("status")
        in {"SUCCEEDED", "NOT_APPLICABLE"},
    }
    frontend = verification.get("frontendVerification")
    checks["frontendVerification"] = frontend is None or (
        isinstance(frontend, dict) and frontend.get("exitCode") == 0
    )
    failed = sorted(name for name, passed in checks.items() if not passed)
    manifest = {
        "schemaVersion": "easydep-release-manifest/v1alpha1",
        "runId": run_root.name,
        "status": "RELEASABLE" if not failed else "BLOCKED",
        "createdAt": datetime.now(UTC).isoformat(),
        "checks": checks,
        "failedChecks": failed,
        "evidence": {
            "workflow": "reports/workflow-state.json",
            "completionAudit": "reports/implementation-completion-audit.json",
            "verification": "reports/final-verification.json",
            "sourceDesignConformance": "reports/source-design-conformance.json",
            "traceability": "reports/rtm-traceability-map.json",
            "containerRuntime": "reports/container-runtime-smoke.json",
        },
    }
    target = run_root / "reports" / "release-manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
