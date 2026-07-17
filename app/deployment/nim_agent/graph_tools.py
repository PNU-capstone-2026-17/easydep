"""클라우드 리소스 타입 의존성 그래프(graphkb) 질의 도구(@function_tool).

역할 분담: 리소스 "타입" 간의 정적 지식(생성 순서, 삭제 영향, 벤더 간 동치)은
이 도구가, 용량 한도는 `cap_*`(capacitykb), 스펙·가격은 `cost_*`(costkb),
배포된 인스턴스의 동적 상태(목록/상태/IP)와 실행은 CB-Tumblebug MCP가 담당한다.

이 도구들은 output/*.json 그래프 산출물을 읽는다. 산출물이 없으면
빌드 안내 메시지를 반환하므로 에이전트가 그대로 사용자에게 전달할 수 있다.
"""

from __future__ import annotations

from agents import function_tool

from graphkb import agent_api


@function_tool
def kb_creation_order(resource_type: str, required_only: bool = False) -> str:
    """클라우드 리소스 타입을 만들기 위해 먼저 만들어야 하는 타입들을 순서대로 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'vm', 'core::vNet', 'AWS::EC2::Subnet',
            'Microsoft.Network/virtualNetworks', 'ComputeInstance'.
        required_only: True면 생성에 필수(required)인 의존만 계산한다.
    """
    print(f"\n[그래프질의] 선행 체인: {resource_type!r} (required_only={required_only})")
    return agent_api.creation_order(resource_type, required_only=required_only)


@function_tool
def kb_deletion_impact(resource_type: str) -> str:
    """클라우드 리소스 타입을 삭제하면 영향받는(그 타입에 의존하는) 타입들을 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'vNet', 'AWS::EC2::VPC', 'ComputeNetwork'.
    """
    print(f"\n[그래프질의] 삭제 영향: {resource_type!r}")
    return agent_api.deletion_impact(resource_type)


@function_tool
def kb_equivalent_types(resource_type: str) -> str:
    """다른 클라우드(AWS/Azure/GCP)나 중립(코어) 레이어에서 같은 것을 가리키는 타입을 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'vNet', 'AWS::EC2::VPC', 'ComputeNetwork'.
    """
    print(f"\n[그래프질의] 동치 타입: {resource_type!r}")
    return agent_api.equivalent_types(resource_type)


@function_tool
def kb_describe_type(resource_type: str) -> str:
    """리소스 타입의 레이어/프로바이더/출처와, 의존 엣지 상세(참조 필드·필수 여부·신뢰도)를 반환한다.

    Args:
        resource_type: 타입 이름.
    """
    print(f"\n[그래프질의] 타입 상세: {resource_type!r}")
    return agent_api.describe_type(resource_type)


@function_tool
def kb_search_types(keyword: str, provider: str | None = None, limit: int = 20) -> str:
    """키워드로 리소스 타입을 검색한다 (부분 문자열, 대소문자 무시).

    Args:
        keyword: 검색어. 예: 'subnet', 'loadbalancer', 'firewall'.
        provider: 'common' | 'aws' | 'azure' | 'gcp'. 미지정이면 전체.
        limit: 반환할 최대 개수(기본 20).
    """
    print(f"\n[그래프질의] 타입 검색: {keyword!r} (provider={provider or 'any'})")
    return agent_api.search_types(keyword, provider=provider, limit=limit)


@function_tool
def kb_rank_types(
    by: str = "dependencies",
    provider: str | None = None,
    limit: int = 10,
    required_only: bool = False,
) -> str:
    """의존 관계가 가장 많은 리소스 타입 순위를 한 번에 반환한다 (전체 집계).

    "가장 의존성이 큰 타입", "가장 많이 참조되는 타입" 같은 **전체 대상 질문**에
    쓴다. 타입을 하나씩 조회하지 말고 이 도구를 한 번 호출하면 된다.

    Args:
        by: 'dependencies'면 그 타입이 의존하는 타입 수(만들려면 필요한 것),
            'dependents'면 그 타입에 의존하는 타입 수(삭제 시 영향받는 것).
        provider: 'common' | 'aws' | 'azure' | 'gcp'. 미지정이면 전체.
        limit: 상위 몇 개를 볼지(기본 10).
        required_only: True면 필수 의존만 센다.
    """
    print(
        f"\n[그래프질의] 타입 순위: by={by}, provider={provider or 'any'}, limit={limit}"
    )
    return agent_api.rank_types(
        by, provider=provider, limit=limit, required_only=required_only
    )


GRAPHKB_TOOLS = [
    kb_creation_order,
    kb_deletion_impact,
    kb_equivalent_types,
    kb_describe_type,
    kb_search_types,
    kb_rank_types,
]
