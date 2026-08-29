from app.testing.schemas.testing_state import TestingState
from app.testing.utils.static_analysis import scan_stage


def static_verification_node(state: TestingState) -> dict:
    """복원한 애플리케이션 전체에서 배포 설정 문제를 찾는다."""
    return scan_stage(
        node="static_verification",
        directory=state.get("application_dir", ""),
        subject="deployment file",
        report_key="static_report",
    )
