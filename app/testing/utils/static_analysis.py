"""복원된 애플리케이션 폴더를 Trivy로 검사한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.testing.utils.docker_trivy import run_trivy_scan


def _scan(directory: Path, subject: str) -> dict[str, Any]:
    try:
        issues = run_trivy_scan(str(directory.resolve()))
    except Exception as error:
        message = f"Trivy 실행을 시작하지 못했습니다: {error}"
        return {
            "status": "UNAVAILABLE",
            "gateStatus": "INCONCLUSIVE",
            "issues": [message],
            "source": {"source": "application", "directory": str(directory)},
            "message": message,
        }
    if issues is None:
        message = "Trivy가 결과를 반환하지 않았습니다."
        return {
            "status": "UNAVAILABLE",
            "gateStatus": "INCONCLUSIVE",
            "issues": [message],
            "source": {"source": "application", "directory": str(directory)},
            "message": message,
        }
    # The legacy helper returns strings. A tool-startup failure is not an application
    # misconfiguration and must not become a FAIL or a pass.
    unavailable = any(
        any(token in str(issue).lower() for token in ("실행 실패", "not found", "no such file", "timed out"))
        for issue in issues
    )
    return {
        "status": "UNAVAILABLE" if unavailable else "FAILED" if issues else "PASSED",
        "gateStatus": "INCONCLUSIVE" if unavailable else "FAIL" if issues else "PASS",
        "issues": issues,
        "source": {"source": "application", "directory": str(directory)},
        "message": (
            f"Trivy could not complete the {subject} scan."
            if unavailable
            else f"Found {len(issues)} {subject} misconfigurations via Trivy."
        ),
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
                "gateStatus": "INCONCLUSIVE",
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
