"""사이징 도구.

**이 축의 답은 대부분 "모른다"여야 정상이다.** 담긴 것은 원본이 공식으로 적어 둔
변환 규칙뿐이고, "동시 사용자 N명 → vCPU M"은 소스가 없어 담지 않았다.

에이전트가 규모를 **직접 추론**하던 자리(KB 근거 0인 구간)를 좁히는 것이 목적이다.
"""

from __future__ import annotations

from agents import function_tool

from sizingkb import agent_api


@function_tool
def sizing_subnet_capacity(prefix_length: int, provider: str) -> str:
    """서브넷 CIDR에 **몇 대나 띄울 수 있는지** 계산한다.

    "/24면 몇 대?", "서브넷 얼마나 크게 잡아야 해?" 같은 질문에 쓰세요.
    `2^(32−prefix) − 예약IP`로 계산하며, 예약 수를 모르는 프로바이더면
    **계산하지 않고 모른다고 답합니다** — 0으로 두면 251대 자리에 256대가 됩니다.

    Args:
        prefix_length: 프리픽스 길이. 예: 24 (= /24).
        provider: 'aws' | 'azure' | 'gcp' | 'alibaba' | 'ibm' 등.
    """
    print(f"\n[사이징질의] 서브넷 용량: /{prefix_length} {provider}")
    return agent_api.subnet_capacity(prefix_length, provider)


@function_tool
def sizing_requirements(scope: str = "") -> str:
    """최소치·개수 요구를 조회한다.

    "K8s 노드는 얼마부터 돼?", "클러스터 만들려면 서브넷 몇 개?" 같은 질문용입니다.
    비우면 담긴 요구 전체.

    Args:
        scope: 'k8s-node' | 'aws' | 'azure' 등.
    """
    print(f"\n[사이징질의] 요구사항: {scope!r}")
    return agent_api.requirements(scope or None)


@function_tool
def sizing_reference_points(keyword: str = "") -> str:
    """워크로드별 스펙 **참조점**을 조회한다.

    ⚠️ **정답이 아니라 예시입니다.** "웹서버는 t3.small이 맞다"가 아니라 "이 소스의
    웹서버 예시가 t3.small"입니다. 그대로 권하지 말고 참조점으로 전하세요.

    Args:
        keyword: 'web' | 'llm' | 'gpu' 등.
    """
    print(f"\n[사이징질의] 참조점: {keyword!r}")
    return agent_api.reference_points(keyword or None)


@function_tool
def sizing_container_presets() -> str:
    """컨테이너 규모 프리셋(nano~2xlarge)의 CPU·메모리를 반환한다.

    원본이 "프로덕션용이 아니다"라고 적어 두었고 그 문장이 함께 옵니다 —
    **떼지 마세요.** 컨테이너 규모이지 인스턴스 규모가 아닙니다.
    """
    print("\n[사이징질의] 컨테이너 프리셋")
    return agent_api.container_presets()


SIZING_TOOLS = [
    sizing_subnet_capacity,
    sizing_requirements,
    sizing_reference_points,
    sizing_container_presets,
]
