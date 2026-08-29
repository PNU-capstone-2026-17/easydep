"""복원된 애플리케이션 폴더를 Trivy로 검사한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.testing.utils.docker_trivy import run_trivy_scan


def _scan(directory: Path, subject: str) -> dict[str, Any]:
    issues = run_trivy_scan(str(directory.resolve()))
    return {
        "status": "FAILED" if issues else "PASSED",
        "issues": issues,
        "source": {"source": "application", "directory": str(directory)},
        "message": f"Found {len(issues)} {subject} misconfigurations via Trivy.",
    }


def scan_stage(
    *,
    node: str,
    directory: str,
    subject: str,
    report_key: str,
) -> dict[str, Any]:
    """복원된 폴더가 있으면 검사하고, 없으면 미실행 사실을 보고한다."""
    target = Path(directory) if directory else None
    if target is None or not target.exists():
        message = f"검사할 {subject} 폴더가 없습니다: {directory or '(empty)'}"
        return {
            "current_node": node,
            "errors": [message],
            report_key: {
                "status": "UNAVAILABLE",
                "issues": [],
                "source": {"source": "none", "directory": directory},
                "message": message,
            },
        }

    report = _scan(target, subject)
    return {
        "current_node": node,
        "errors": report["issues"],
        report_key: report,
    }
