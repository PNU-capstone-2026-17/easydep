import operator
from typing import Annotated, Any, TypedDict


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
    # TestingInput의 고정 snapshot이 있으면 dynamic node는 DB를 다시 조회하지 않는다.
    testing_input: dict[str, Any]
    # Testing 작업을 시작할 때 한 번 복원한 애플리케이션 폴더다. 모든 정적·동적
    # 검사는 이 폴더를 함께 사용하며 검사 도중 DB에서 파일을 다시 읽지 않는다.
    application_dir: str
    target_url: str # Target URL for dynamic testing, defaults to localhost:8080
    application_network: str | None
    # 사용자가 선택한 이전 수리 이력. 동적 테스트 생성기는 같은 실패와 후보를
    # 반복하지 않도록 이 값을 프롬프트 문맥으로만 사용한다.
    repair_history: dict[str, Any]
    # 구현 수리 뒤에는 이전 실패를 발견한 테스트 코드를 그대로 실행한다. 값이 없을 때만
    # 동적 테스트 노드가 NIM으로 새 후보를 만든다.
    fixed_test_code: str | None
    iac_expected: bool | None
    deployment_package_expected: bool | None

    # State
    current_node: str
    errors: Annotated[list[str], operator.add]

    # Reports from each verification node
    static_report: dict[str, Any] | None
    dynamic_functional_report: dict[str, Any] | None
    iac_report: dict[str, Any] | None


# 이름이 ``Test``로 시작하지만 pytest 수집 대상 클래스가 아니다.
TestingState.__test__ = False  # type: ignore[attr-defined]
