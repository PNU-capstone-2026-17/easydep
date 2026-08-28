import operator
from typing import Annotated, Any, TypedDict

from app.testing.schemas.testing_input import ArtifactSnapshotRef


class TestingState(TypedDict):
    """
    LangGraph state for the Testing Agent.
    """
    # Inputs
    run_id: str
    # 검사 대상 앱. 정적분석이 읽을 배포/IaC 스냅샷과 동적 검사가 읽을 기능
    # 요구사항이 모두 이 id로 DB에서 조회된다. LangGraph는 스키마에 없는 키를
    # 조용히 버리므로, 이 칸이 없으면 호출자가 넘겨도 노드에는 닿지 않는다.
    app_id: str
    # Testing job 시작 때 선택한 구현 작업과 파일 버전이다. ``fixed_artifacts``가
    # 참이면 이 목록에 없는 산출물을 DB 최신 버전이나 workspace에서 보충하지 않는다.
    implementation_job_id: str | None
    artifact_versions: dict[str, int]
    artifact_refs: dict[str, ArtifactSnapshotRef]
    fixed_artifacts: bool
    # DB에 저장된 스냅샷이 없을 때만 쓰는 작업공간 대체 경로.
    manifests_dir: str # e.g. "application/k8s"
    iac_dir: str # e.g. "application/terraform"
    target_url: str # Target URL for dynamic testing, defaults to localhost:8080
    # 사용자가 선택한 이전 수리 이력. 동적 테스트 생성기는 같은 실패와 후보를
    # 반복하지 않도록 이 값을 프롬프트 문맥으로만 사용한다.
    repair_history: dict[str, Any]

    # State
    current_node: str
    errors: Annotated[list[str], operator.add]

    # Reports from each verification node
    static_report: dict[str, Any] | None
    dynamic_functional_report: dict[str, Any] | None
    dynamic_nfr_report: dict[str, Any] | None
    iac_report: dict[str, Any] | None


# 이름이 ``Test``로 시작하지만 pytest 수집 대상 클래스가 아니다.
TestingState.__test__ = False  # type: ignore[attr-defined]
