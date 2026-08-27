from app.db.models import TYPE_DEPLOYMENT_FILE
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.static_analysis import scan_stage


def static_verification_node(state: TestingState) -> dict:
    """Static verification of the deployment files the implementation agent stored.

    Scans the ``DEPLOYMENT_FILE`` snapshot — Docker and deployment support
    files — with Trivy's misconfiguration rules. Kubernetes manifests are not
    part of the current implementation release contract.
    """
    return scan_stage(
        node="static_verification",
        app_id=state.get("app_id"),
        artifact_type=TYPE_DEPLOYMENT_FILE,
        workspace_dir=state.get("manifests_dir", ""),
        subject="deployment file",
        report_key="static_report",
    )
