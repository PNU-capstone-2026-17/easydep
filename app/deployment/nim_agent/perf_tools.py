"""클라우드 인스턴스 성능 특성(perfkb) 질의 도구(@function_tool).

지식 차원 분담:
- `kb_*`(graphkb)    — 타입 간 의존성: 무엇이 무엇을 필요로 하나
- `cap_*`(capacitykb) — 용량·제약: 무엇이 허용되나 / 한도 / 바꿀 수 있나
- `cost_*`(costkb)   — 스펙·가격: 무엇을 살 수 있고 얼마인가
- `perf_*`(이 파일)  — 성능 특성: 그게 실제로 얼마나 빠른가

**cost_*와의 관계**: `cost_recommend_specs`는 추천 결과에 이미 성능 경고(버스트·구세대)를
자동으로 붙인다(도구 계층 조인). perf_* 도구는 사용자가 **특정 스펙을 콕 집어** 프로파일을
보거나 둘을 비교하려 할 때 쓴다.

**정직한 한계 — 프로바이더 간 비교는 불가능**: ACU는 Azure에만, 클럭·EBS는 AWS에만 있다.
"AWS m5.large vs Azure D2s_v3 중 뭐가 빨라?"는 답할 수 없다 — perf_compare가 단일 provider만
받는 것이 그 한계를 구조적으로 강제한다. 값이 없는 건 '느리다'가 아니라 '모른다'다.
"""

from __future__ import annotations

from agents import function_tool

from perfkb import agent_api


@function_tool
def perf_instance_profile(provider: str, spec_name: str) -> str:
    """한 인스턴스 스펙의 성능 프로파일을 반환한다(상시 CPU·세대·클럭·EBS·ACU 등).

    가격만으로는 안 보이는 특성을 확인할 때 쓴다 — 예: "t3.medium 상시 부하에 괜찮아?",
    "m5.large 최신 세대야?", "이 인스턴스 네트워크 대역폭 얼마야?".

    Args:
        provider: 'aws' | 'azure' | 'gcp' (성능은 이 셋만 수록).
        spec_name: CSP 스펙명. 예: 't3.medium', 'm5.large', 'Standard_D2s_v3'.
    """
    print(f"\n[성능질의] 프로파일: {provider} {spec_name}")
    return agent_api.instance_profile(provider, spec_name)


@function_tool
def perf_compare(provider: str, spec_names: list[str]) -> str:
    """**같은 프로바이더**의 인스턴스 스펙들을 성능 축별로 나란히 비교한다.

    승자를 단정하지 않는다 — '더 빠르다'는 워크로드(CPU/IO 바운드)에 따라 다르기 때문이다.
    **프로바이더 간 비교(AWS vs Azure)는 할 수 없다**: 비교 축(ACU/클럭 등)이 프로바이더
    전용이라 공통 기준이 없다. 그런 요청을 받으면 이 도구를 쓰지 말고 불가능하다고 답하세요.

    Args:
        provider: 'aws' | 'azure' | 'gcp'. 한 번에 한 프로바이더만.
        spec_names: 비교할 CSP 스펙명 목록(2개 이상). 예: ['m5.large', 'm6i.large'].
    """
    print(f"\n[성능질의] 비교: {provider} {spec_names}")
    return agent_api.compare(provider, spec_names)


@function_tool
def perf_specs_by_ebs_baseline(min_mbps: float, limit: int = 10) -> str:
    """지속(baseline) EBS 대역폭이 기준 이상인 AWS 스펙을 찾는다.

    함정 방지: 흔히 보는 "최대 대역폭"은 버스트라 지속되지 않는다 — 이 도구는 baseline만
    본다. **AWS 전용**(Azure는 IOPS, GCP는 별도라 축이 다르다). 가격·크기는 이후
    cost_recommend_specs로 확인하세요.

    Args:
        min_mbps: 필요한 지속 EBS 대역폭(Mbps). 예: 500 MB/s ≈ 4000 Mbps.
        limit: 반환할 스펙 수(기본 10).
    """
    print(f"\n[성능질의] EBS baseline >= {min_mbps} Mbps (AWS)")
    return agent_api.specs_meeting_ebs_baseline(min_mbps, limit)


PERF_TOOLS = [perf_instance_profile, perf_compare, perf_specs_by_ebs_baseline]
