"""Database-backed inputs for the testing agent's static analysis.

Static analysis has to read what the implementation agent actually produced,
not whatever happens to be left over in a workspace directory.  The
implementation worker stores every generated file tree as an immutable
artifact snapshot (``artifact_repository.save_file_snapshot``), so the
deployment manifests (``DEPLOYMENT_FILE``) and the IaC sources (``IAC_CODE``)
are already in the database keyed by app id.

Trivy scans a directory, not a database, so a snapshot is materialised into a
throwaway directory that lives exactly as long as one scan.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from app.repositories.artifact_repository import AppNotFound, load_file_snapshot


class ArtifactSourceUnavailable(Exception):
    """The app has no stored snapshot of the requested artifact type."""


def _safe_relative(file_path: str) -> Path:
    """Reject anything that would write outside the scan directory.

    ``save_file_snapshot`` already normalises paths on the way in, but this
    materialises database rows onto a real filesystem, so it does not take
    that on trust.
    """
    candidate = PurePosixPath(file_path.replace("\\", "/"))
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError(f"Unsafe artifact file path: {file_path}")
    return Path(*candidate.parts)


def materialize_artifact(
    app_id: str, artifact_type: str, destination: Path
) -> dict[str, Any]:
    """Write the current stored snapshot of one artifact type into a directory."""
    try:
        snapshot = load_file_snapshot(app_id, artifact_type)
    except AppNotFound as error:
        raise ArtifactSourceUnavailable(f"Unknown app id: {app_id}") from error
    if not snapshot or not snapshot.get("files"):
        raise ArtifactSourceUnavailable(
            f"No stored {artifact_type} snapshot exists for app {app_id}."
        )

    written: list[str] = []
    for file_path, item in sorted(snapshot["files"].items()):
        target = destination / _safe_relative(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
        written.append(file_path)

    return {
        "source": "db",
        "artifact_type": artifact_type,
        "version_no": snapshot.get("version_no"),
        "implementation_job_id": (snapshot.get("metadata") or {}).get(
            "implementation_job_id"
        ),
        "file_count": len(written),
        "files": written,
    }


@contextmanager
def materialized_artifact(
    app_id: str, artifact_type: str
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield ``(directory, provenance)`` for one stored snapshot, then delete it."""
    with tempfile.TemporaryDirectory(prefix="easydep-testing-scan-") as temporary:
        directory = Path(temporary)
        provenance = materialize_artifact(app_id, artifact_type, directory)
        yield directory, provenance
