"""요구사항 분석 에이전트 패키지.

그래프 정의(state/llm/steps)를 모아 서빙 레이어(app.main)가 쓰는 공개 API를 노출한다.
멀티 단계 확장의 단일 진입점: build_graph()에 노드/엣지를 추가.
"""
from app.agent.graph import build_graph, resume_analysis, start_analysis
from app.agent.state import AgentState, RequirementItem

# 주의: 컴파일된 그래프 인스턴스(`graph`)는 일부러 재노출하지 않는다.
# 이름이 서브모듈 `app.agent.graph` 와 충돌해 `import app.agent.graph` 를 가리기 때문.
# 필요하면 `from app.agent.graph import graph` 로 직접 가져온다.
__all__ = [
    "build_graph",
    "start_analysis",
    "resume_analysis",
    "AgentState",
    "RequirementItem",
]
