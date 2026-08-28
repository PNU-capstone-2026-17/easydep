"""Kubernetes와 IaC node가 함께 사용하는 Trivy 설정 오류 검사 stage다.

두 node는 산출물 종류만 다르고 같은 방식으로 설정 오류를 검사한다. 산출물 위치 선택,
Trivy 실행과 보고서 형식을 이 모듈에서 공통으로 처리한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.testing.schemas.testing_input import ArtifactSnapshotRef
from app.testing.utils.artifact_source import (
    ArtifactSnapshotMismatch,
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
    version_no: int | None = None,
    snapshot_ref: ArtifactSnapshotRef | None = None,
    expected_implementation_job_id: str | None = None,
    fixed_snapshot: bool = False,
) -> dict[str, Any]:
    """구현 agent가 저장한 snapshot을 우선하여 한 종류의 산출물을 검사한다.

    DB snapshot은 구현 agent가 게시했고 사용자가 나중에 조회할 수 있는 기준 결과다.
    ``workspace_dir``은 구현 결과가 DB에 저장되지 않은 실행을 위한 fallback이다. 보고서에
    실제로 검사한 위치를 기록해 오래된 workspace를 최신 산출물로 오인하지 않게 한다.
    """
    # 고정 입력에 이 산출물이 없다는 것은 Testing job을 만들 때 실제로 없었다는 뜻이다.
    # 이후 생긴 최신 버전이나 workspace 파일로 채우면 한 작업에 두 구현이 섞이므로 조회하지
    # 않고 UNAVAILABLE을 반환한다.
    if fixed_snapshot and snapshot_ref is None and version_no is None:
        unavailable = (
            f"고정된 테스트 입력에 {artifact_type} snapshot이 없습니다."
        )
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

    if app_id:
        try:
            with materialized_artifact(
                app_id,
                artifact_type,
                version_no=version_no,
                snapshot_ref=snapshot_ref,
                expected_implementation_job_id=expected_implementation_job_id,
            ) as (directory, provenance):
                report = _scan(directory, provenance, subject)
            return {
                "current_node": node,
                "errors": report["issues"],
                report_key: report,
            }
        except ArtifactSourceUnavailable as error:
            unavailable = str(error)
            if fixed_snapshot:
                return {
                    "current_node": node,
                    "errors": [unavailable],
                    report_key: {
                        "status": "UNAVAILABLE",
                        "issues": [],
                        "source": {
                            "source": "db",
                            "artifact_type": artifact_type,
                            "version_no": version_no,
                        },
                        "sourceError": "SNAPSHOT_UNAVAILABLE",
                        "message": unavailable,
                    },
                }
        except ArtifactSnapshotMismatch as error:
            # 버전이나 구현 작업 ID가 다르면 workspace fallback도 사용하면 안 된다. 잘못된
            # 파일을 검사하는 대신 provenance 오류를 그대로 실패 보고서에 남긴다.
            message = str(error)
            return {
                "current_node": node,
                "errors": [message],
                report_key: {
                    "status": "FAILED",
                    "issues": [message],
                    "source": {
                        "source": "db",
                        "artifact_type": artifact_type,
                        "version_no": version_no,
                    },
                    "sourceError": "SNAPSHOT_MISMATCH",
                    "message": f"{artifact_type} snapshot provenance가 일치하지 않습니다.",
                },
            }
        except ValueError as error:  # 저장 snapshot에 상위 경로로 나가는 안전하지 않은 경로가 있다.
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
        # 검사할 경로가 없다는 것은 "검사했고 문제가 없음"과 다르다. 설정 오류로도
        # 분류하지 않고 UNAVAILABLE로 표시해 검사를 실행하지 못했다는 사실을 남긴다.
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
