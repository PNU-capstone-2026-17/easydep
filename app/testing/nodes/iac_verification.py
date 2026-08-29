from pathlib import Path

from app.testing.schemas.testing_state import TestingState
from app.testing.utils.static_analysis import scan_stage


def iac_verification_node(state: TestingState) -> dict:
    """같은 애플리케이션 폴더의 ``terraform`` 하위 폴더를 검사한다."""
    return scan_stage(
        node="iac_verification",
        directory=str(Path(state.get("application_dir", "")) / "terraform"),
        subject="IaC",
        report_key="iac_report",
    )
