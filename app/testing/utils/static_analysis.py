"""One Trivy misconfiguration stage, shared by the K8s and IaC nodes.

Both nodes ask the same question of a different artifact type, so the source
resolution, the scan and the report shape live here once.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.testing.utils.artifact_source import (
    ArtifactSourceUnavailable,
    materialized_artifact,
)
from app.testing.utils.docker_trivy import run_trivy_scan


def _scan(directory: Path, provenance: dict[str, Any], subject: str) -> dict[str, Any]:
    issues = run_trivy_scan(str(directory.resolve()))
    return {
        "status": "FAILED" if issues else "PASSED",
        "issues": issues,
        "source": provenance,
        "message": f"Found {len(issues)} {subject} misconfigurations via Trivy.",
    }


def scan_stage(
    *,
    node: str,
    app_id: str | None,
    artifact_type: str,
    workspace_dir: str,
    subject: str,
    report_key: str,
) -> dict[str, Any]:
    """Scan one artifact type, preferring the snapshot the implementation agent stored.

    The database is the source of truth: it is what the implementation agent
    published and what the user can inspect afterwards.  ``workspace_dir`` is
    the fallback for runs whose implementation output was never persisted (the
    legacy orchestration path writes a workspace but no snapshot), and the
    report always records which one was scanned so a stale workspace cannot
    masquerade as a verified artifact.
    """
    if app_id:
        try:
            with materialized_artifact(app_id, artifact_type) as (directory, provenance):
                report = _scan(directory, provenance, subject)
            return {
                "current_node": node,
                "errors": report["issues"],
                report_key: report,
            }
        except ArtifactSourceUnavailable as error:
            unavailable = str(error)
        except ValueError as error:  # An unsafe path in the stored snapshot.
            return {
                "current_node": node,
                "errors": [str(error)],
                report_key: {
                    "status": "FAILED",
                    "issues": [str(error)],
                    "source": {"source": "db", "artifact_type": artifact_type},
                    "message": f"Stored {artifact_type} snapshot is not scannable.",
                },
            }
    else:
        unavailable = f"No app_id was supplied, so no stored {artifact_type} snapshot could be read."

    if not workspace_dir or not os.path.exists(workspace_dir):
        # Nothing was scanned. That is not the same as "scanned and clean", and
        # it is not a misconfiguration either — say which it is.
        return {
            "current_node": node,
            "errors": [unavailable],
            report_key: {
                "status": "UNAVAILABLE",
                "issues": [],
                "source": {"source": "none", "artifact_type": artifact_type},
                "message": unavailable,
            },
        }

    report = _scan(
        Path(workspace_dir),
        {
            "source": "workspace",
            "artifact_type": artifact_type,
            "directory": workspace_dir,
            "reason": unavailable,
        },
        subject,
    )
    return {"current_node": node, "errors": report["issues"], report_key: report}
