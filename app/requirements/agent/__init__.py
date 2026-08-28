"""요구사항 분석 에이전트 패키지.

그래프 정의(state/llm/steps)를 모아 서빙 레이어(app.requirements.api)가 쓰는 공개 API를 노출한다.
멀티 단계 확장의 단일 진입점: build_graph()에 노드/엣지를 추가.
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
        from app.requirements.agent import graph

        return getattr(graph, name)
    raise AttributeError(name)

# 주의: 컴파일된 그래프 인스턴스(`graph`)는 일부러 재노출하지 않는다.
# 이름이 서브모듈 `app.requirements.agent.graph` 와 충돌해 `import app.requirements.agent.graph` 를 가리기 때문.
# 필요하면 `from app.requirements.agent.graph import graph` 로 직접 가져온다.
__all__ = [
    "AgentState",
    "RequirementItem",
    "build_graph",
    "resume_analysis",
    "start_analysis",
]
