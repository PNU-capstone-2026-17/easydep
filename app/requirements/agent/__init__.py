"""요구사항 분석 에이전트 패키지.

기존 agent import가 요구사항 계약과 orchestration 공개 진입점을 찾을 수 있게 한다.
새 production 코드는 app.requirements.contracts와 app.requirements.orchestration을 직접 사용한다.
"""
from app.requirements.contracts.state import AgentState, RequirementItem


def __getattr__(name: str):
    """그래프 진입점만 지연 import해 stage registry의 초기화 순환을 피한다.

    Args:
        name: package에서 요청한 공개 attribute 이름이다.

    Returns:
        기존 graph module의 동일한 public callable이다.

    Notes:
        state contract는 즉시 사용할 수 있게 두고 graph build는 실제 진입점 접근 때만
        import한다. 반환 callable identity와 실행 동작은 바꾸지 않는다.
    """

    if name in {"build_graph", "resume_analysis", "start_analysis"}:
        from app.requirements.orchestration import graph

        return getattr(graph, name)
    raise AttributeError(name)

# 주의: 컴파일된 그래프 인스턴스(`graph`)는 package attribute와 submodule 이름이
# 충돌하므로 일부러 재노출하지 않는다.
# 필요하면 `app.requirements.orchestration.graph`의 공개 진입점을 직접 가져온다.
__all__ = [
    "AgentState",
    "RequirementItem",
    "build_graph",
    "resume_analysis",
    "start_analysis",
]
