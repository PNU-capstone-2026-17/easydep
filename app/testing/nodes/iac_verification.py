from app.db.models import TYPE_IAC_CODE
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.static_analysis import scan_stage


def iac_verification_node(state: TestingState) -> dict:
    """Static verification of the IaC sources the implementation agent stored.

    Scans the ``IAC_CODE`` snapshot (Terraform, Pulumi) with Trivy's
    misconfiguration rules.
    """
    return scan_stage(
        node="iac_verification",
        app_id=state.get("app_id"),
        artifact_type=TYPE_IAC_CODE,
        workspace_dir=state.get("iac_dir", ""),
        subject="IaC",
        report_key="iac_report",
    )
