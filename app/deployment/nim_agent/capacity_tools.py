"""클라우드 리소스 용량·제약(capacitykb) 질의 도구(@function_tool).

지식 차원 분담:
- `kb_*`(graphkb)   — 타입 간 의존성: 무엇이 무엇을 필요로 하나
- `cap_*`(이 파일)  — 용량·제약: 무엇이 허용되나 / 한도 / 바꿀 수 있나
- `cost_*`(costkb) — 인스턴스 카탈로그·가격: 무엇을 살 수 있고 얼마인가
- cb-tumblebug MCP — 현재 상태·실행: 지금 무엇이 떠 있나 / 실제로 만들기

용량 한도는 LLM이 특히 자주 지어내는 영역이라(예: "EBS 최대 16TB"),
에이전트 instructions에서 반드시 이 도구를 쓰도록 지시한다.
"""

from __future__ import annotations

from agents import function_tool

from capacitykb import agent_api


@function_tool
def cap_check_value(
    resource_type: str,
    property_name: str,
    value: str,
    context: str | None = None,
) -> str:
    """리소스 속성에 넣으려는 값이 허용 범위인지 판정한다.

    신뢰도가 낮은(설명문에서 추출한) 제약은 값을 거부하는 근거로 쓰지 않고
    참고로만 알려준다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Volume', 'aws::AWS::Lambda::Function',
            'Microsoft.ContainerService/managedClusters'.
        property_name: 속성 이름. 예: 'Size', 'Timeout', 'EphemeralStorage/Size'.
        value: 넣으려는 값. 숫자면 숫자로 해석한다. 예: '100000', 'gp3'.
        context: 함께 정한 다른 속성. `'VolumeType=gp2'` 처럼 `이름=값`을 쉼표로 잇는다.
            **한도가 다른 속성에 따라 달라지면 이걸 줘야 판정된다** — EBS 볼륨 크기
            상한은 gp2 16,384 / gp3 65,536 / standard 1,024 GiB로 제각각이다.
            안 주면 "어느 조건에서 얼마인지"를 나열하고 무엇이 필요한지 알려준다.
    """
    parsed = _parse_context(context)
    shown = f" ({context})" if parsed else ""
    print(f"\n[용량질의] 값 판정: {resource_type}.{property_name} = {value!r}{shown}")
    return agent_api.check(resource_type, property_name, _coerce(value), context=parsed)


def _parse_context(text: str | None) -> dict | None:
    """`'VolumeType=gp2, Tier=Premium'` → `{"VolumeType": "gp2", "Tier": "Premium"}`.

    모델이 채우는 칸이라 형식을 느슨하게 받는다. 못 알아들은 조각은 버리되,
    하나도 못 읽으면 None을 돌려 "문맥 없음"과 같게 만든다 — 반쯤 읽은 문맥으로
    판정하면 어느 조건이 적용됐는지 아무도 모른다.
    """
    if not text:
        return None
    out: dict[str, str] = {}
    for chunk in text.replace(";", ",").split(","):
        name, sep, value = chunk.partition("=")
        if sep and name.strip() and value.strip():
            out[name.strip()] = value.strip()
    return out or None


@function_tool
def cap_property_limits(resource_type: str, property_name: str | None = None) -> str:
    """리소스 타입(또는 특정 속성)에 걸린 제약을 근거·신뢰도와 함께 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Volume'.
        property_name: 특정 속성만 볼 때 지정. 미지정이면 타입 전체.
    """
    print(f"\n[용량질의] 제약 조회: {resource_type}" + (f".{property_name}" if property_name else ""))
    return agent_api.property_limits(resource_type, property_name)


@function_tool
def cap_immutable_properties(resource_type: str) -> str:
    """변경하면 리소스가 삭제·재생성되는 속성들을 반환한다 (배포/변경 계획에 필수).

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Subnet'.
    """
    print(f"\n[용량질의] 불변 속성: {resource_type}")
    return agent_api.immutable(resource_type)


@function_tool
def cap_allowed_values(resource_type: str, property_name: str) -> str:
    """속성의 허용값(enum)·패턴·기본값을 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::RDS::DBInstance'.
        property_name: 속성 이름. 예: 'StorageType'.
    """
    print(f"\n[용량질의] 허용값: {resource_type}.{property_name}")
    return agent_api.allowed_values(resource_type, property_name)


@function_tool
def cap_service_quota(keyword: str) -> str:
    """계정/구독 단위 상한(서비스 쿼터)을 키워드로 찾는다.

    예: "vNet당 서브넷 몇 개까지?" → keyword='subnet'.
    현재 쿼터 데이터는 Azure만 수록돼 있다 (AWS/GCP는 공개 기계판독 소스가 없음).

    Args:
        keyword: 검색어. 예: 'subnet', 'virtual network', 'vCPU'.
    """
    print(f"\n[용량질의] 서비스 쿼터: {keyword!r}")
    return agent_api.service_quota(keyword)


def _coerce(raw: str) -> float | str:
    """LLM이 문자열로 넘긴 값을 가능하면 숫자로 바꾼다."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    return int(number) if number.is_integer() else number


CAPACITY_TOOLS = [
    cap_check_value,
    cap_property_limits,
    cap_immutable_properties,
    cap_allowed_values,
    cap_service_quota,
]
