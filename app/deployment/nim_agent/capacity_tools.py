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

    **인스턴스 타입이 특정 리전에서 쓸 수 있는지도 여기서 판정한다** —
    `cap_check_value('AWS::EC2::Instance', 'InstanceType', 'p5.48xlarge',
    context='Region=af-south-1')`. 실측에서 에이전트가 바로 이 질문에 도구를
    하나도 부르지 않고 "리전별 제공 여부는 조회되지 않는다"고 답했다. 그런
    말은 어디에도 없었지만, **된다는 말도 없었다.**

    신뢰도가 낮은(설명문에서 추출한) 제약은 값을 거부하는 근거로 쓰지 않고
    참고로만 알려준다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Volume', 'aws::AWS::Lambda::Function',
            'Microsoft.ContainerService/managedClusters'.
        property_name: 속성 이름. 예: 'Size', 'Timeout', 'EphemeralStorage/Size'.
        value: 넣으려는 값. 숫자면 숫자로 해석한다. 예: '100000', 'gp3'.
        context: 함께 정한 다른 속성. `'VolumeType=gp2'`, `'Region=af-south-1'` 처럼
            `이름=값`을 쉼표로 잇는다.
            **한도·허용값이 다른 것에 따라 달라지면 이걸 줘야 판정된다** — EBS 볼륨
            크기 상한은 gp2 16,384 / gp3 65,536 / standard 1,024 GiB로 제각각이고,
            인스턴스 타입 허용값은 리전 38곳마다 다르다.
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


#: **어느 타입이 "인스턴스 종류"를 고르는 자리인가.** 3줄짜리 사람 검수표다.
#: 규칙으로 유도하려 하면 짐작이 되고, 이건 짐작할 게 아니라 아는 사실이다.
_COMPUTE_TYPES = {
    "aws::AWS::EC2::Instance": "aws",
    "azure::Microsoft.Compute/virtualMachines": "azure",
    "gcp::ComputeInstance": "gcp",
}


def _perf_pointer(resource_type: str) -> str:
    """"이 타입의 하드웨어는 성능 축에 있다"는 한 줄. 아니면 빈 문자열.

    **축을 늘리는 것과 축에 닿게 하는 것은 다른 일이다.** 실측에서
    "g5g.xlarge에 어떤 GPU가 달렸어?"를 물었더니 모델이 용량 축을 뒤지다
    `AWS::EC2::Instance.Gpu`라는 없는 속성까지 조회하고 웹으로 나갔다.
    그때 우리는 GPU 571건을 쥐고 있었는데, **다른 축에** 있었다.

    같은 질문을 "몇 개, 어떤 모델이야?"로 물으면 성능 도구로 바로 갔다.
    문구 하나 차이로 갈리므로 **양쪽 문이 다 알아야 한다.**

    graphkb→capacitykb 때와 같이 도구 계층에 둔다 — 단방향 규약상 capacitykb는
    perfkb를 import할 수 없고, 양쪽을 다 보는 층은 여기뿐이다.
    """
    try:
        from capacitykb.agent_api import load_merged
        from capacitykb.query import resolve_type
        from perfkb.agent_api import hardware_summary

        capacity = load_merged()
        if capacity is None:
            return ""
        type_id = resolve_type(capacity, resource_type)
        provider = _COMPUTE_TYPES.get(type_id)
        if not provider:
            return ""
        summary = hardware_summary(provider)
    except Exception:
        return ""
    if not summary:
        return ""
    return (
        "\n  ※ CPU·GPU 모델처럼 **인스턴스 종류마다 다른 하드웨어**는 이 축에 없고 "
        f"성능 축에 있습니다({summary}).\n"
        f"     perf_instance_profile('{provider}', '<인스턴스 종류>') 로 보세요."
    )


@function_tool
def cap_property_limits(resource_type: str, property_name: str | None = None) -> str:
    """리소스 타입(또는 특정 속성)에 걸린 제약을 근거·신뢰도와 함께 반환한다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Volume'.
        property_name: 특정 속성만 볼 때 지정. 미지정이면 타입 전체.
    """
    print(f"\n[용량질의] 제약 조회: {resource_type}" + (f".{property_name}" if property_name else ""))
    return agent_api.property_limits(resource_type, property_name) + _perf_pointer(
        resource_type
    )


@function_tool
def cap_immutable_properties(resource_type: str) -> str:
    """변경하면 리소스가 삭제·재생성되는 속성들을 반환한다 (배포/변경 계획에 필수).

    Args:
        resource_type: 타입 이름. 예: 'AWS::EC2::Subnet'.
    """
    print(f"\n[용량질의] 불변 속성: {resource_type}")
    return agent_api.immutable(resource_type)


@function_tool
def cap_secret_properties(resource_type: str) -> str:
    """배포 때 넣지만 API로 다시 못 읽는 속성들(비밀번호·키·연결 문자열).

    인프라를 만들 때 이 값들은 잃어버리면 다시 조회할 수 없으니 Key Vault 등으로
    따로 관리하도록 안내하는 데 쓴다. 지금은 Azure만 이 표시를 제공한다 — 다른
    프로바이더에 "비밀값 없음"이라고 답하지 마세요(추적 안 함이지 없음이 아님).

    Args:
        resource_type: 타입 이름. 예: 'Microsoft.DBforMySQL/flexibleServers'.
    """
    print(f"\n[용량질의] 비밀값 속성: {resource_type}")
    return agent_api.secrets(resource_type)


@function_tool
def cap_allowed_values(resource_type: str, property_name: str) -> str:
    """속성의 허용값(enum)·패턴·기본값을 반환한다.

    리전마다 다른 허용값도 여기 있다 — `('AWS::EC2::Instance', 'InstanceType')`이면
    리전 38곳의 인스턴스 타입 목록이 조건과 함께 나온다. 특정 리전에서 되는지만
    알고 싶으면 `cap_check_value`에 `context='Region=...'`을 주는 쪽이 짧다.

    Args:
        resource_type: 타입 이름. 예: 'AWS::RDS::DBInstance', 'AWS::EC2::Instance'.
        property_name: 속성 이름. 예: 'StorageType', 'InstanceType'.
    """
    print(f"\n[용량질의] 허용값: {resource_type}.{property_name}")
    return agent_api.allowed_values(resource_type, property_name) + _perf_pointer(
        resource_type
    )


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


@function_tool
def cap_resolve_region(place: str) -> str:
    """사람이 쓴 지명을 **AWS** 리전 코드로 바꾼다. '서울' → 'ap-northeast-2'.

    **다른 도구에 리전을 넘기기 전에 먼저 이걸 부르세요.** 나머지 도구는 리전
    코드로 색인돼 있어서 '서울'을 그대로 넘기면 데이터가 있어도 못 찾습니다.

    **AWS 리전만 압니다.** 같은 도시에 다른 프로바이더의 리전도 있습니다
    (서울 = azure `koreasouth`, gcp `asia-northeast3`, tencent `ap-seoul`).
    사용자가 프로바이더를 안 밝혔으면 이 코드로 좁히지 말고 먼저 되물으세요 —
    좁히면 AWS만 본 답이 전체를 본 답처럼 보입니다.

    Args:
        place: 지명이나 리전 이름. 예: '서울', '도쿄', 'Seoul', 'ap-northeast-2'.
    """
    print(f"\n[용량질의] 리전 해석: {place!r}")
    return agent_api.region_lookup(place)


@function_tool
def cap_service_regions(service: str) -> str:
    """이 서비스의 엔드포인트가 어느 리전에 있는지 본다.

    **주의**: 목록에 없는 리전은 "못 쓴다"가 아니라 "이 데이터로는 모른다"입니다.
    없다고 단정해서 답하지 마세요.

    Args:
        service: CFN 타입(`AWS::EC2::Instance`) 또는 SDK 서비스 이름(`ec2`).
    """
    print(f"\n[용량질의] 서비스 리전: {service!r}")
    return agent_api.where_available(service)


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
    cap_secret_properties,
    cap_allowed_values,
    cap_service_quota,
    cap_resolve_region,
    cap_service_regions,
]
