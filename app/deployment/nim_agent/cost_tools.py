"""클라우드 인스턴스 스펙·가격(costkb) 질의 도구(@function_tool).

지식 차원 분담:
- `kb_*`(graphkb)    — 타입 간 의존성: 무엇이 무엇을 필요로 하나
- `cap_*`(capacitykb) — 용량·제약: 무엇이 허용되나 / 한도 / 바꿀 수 있나
- `cost_*`(이 파일)   — 스펙·가격: 무엇을 살 수 있고 얼마인가
- cb-tumblebug MCP    — 현재 상태·실행: 지금 무엇이 떠 있나 / 실제로 만들기

**MCP와의 관계**: MCP의 `recommend_vm_spec`은 `cost_recommend_specs`와 **같은 질문에
답하는 다른 소스**다(축이 다른 게 아니다). 이쪽은 서버·자격증명 없이 항상 동작하는
기준선이고, MCP가 연결돼 있으면 라이브 카탈로그라 더 정확하므로 그쪽이 우선이다.

**계획 게이트**: 카탈로그의 cloud_sizing은 "스펙 추천 → 비용 추정"으로 이어지는 다단계
작업이라, record_plan이 먼저 기록돼야 실행된다. 프롬프트로는 순서가 지켜지지 않고
(모델 3종 모두 실패) NIM은 tool_choice도 무시하므로, 도구가 직접 거부한다.
자세한 배경은 `nim_agent/session.py` 참고.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from costkb import agent_api
from costkb.agent_api import HOURS_PER_MONTH
from perfkb import agent_api as perf_api

from .session import SessionState


def _perf_annotate(spec: dict) -> str | None:
    """추천 후보에 성능 경고를 붙인다 — costkb×perfkb 조인 지점.

    여기(도구 계층)에서 조인하므로 두 KB는 서로 import하지 않는다. perfkb가 빌드되지
    않았거나 번들 스펙(id 없음)이면 조용히 None을 준다(fail-open).
    """
    return perf_api.recommend_warning(spec.get("id"))

_PLAN_REQUIRED = (
    "먼저 record_plan으로 계획을 기록하세요. 클라우드 리소스 산정은 구성요소별 사이징 → "
    "스펙 추천 → 비용 합산으로 이어지는 다단계 작업이라, 계획을 남긴 뒤에 실행합니다. "
    "record_plan을 호출한 다음 이 도구를 다시 부르세요."
)


def _needs_plan(ctx: RunContextWrapper[SessionState]) -> bool:
    """계획 게이트가 닫혀 있는지. 세션 상태가 없으면 게이트를 적용하지 않는다."""
    return isinstance(ctx.context, SessionState) and not ctx.context.has_plan


@function_tool
def cost_recommend_specs(
    ctx: RunContextWrapper[SessionState],
    vcpu_min: int = 2,
    mem_min_gib: float = 4,
    provider: str | None = None,
    region: str | None = None,
    sort_by: str = "cost",
    limit: int = 5,
    architecture: str = "x86_64",
) -> str:
    """요구사항을 만족하는 VM 스펙 후보와 시간당 단가를 추천한다(크레덴셜 불필요).

    성능 지식베이스(perfkb)가 빌드돼 있으면 후보에 성능 경고가 함께 붙습니다 —
    버스트 인스턴스나 구세대처럼 **가격만 보면 놓치는** 함정을 짚어줍니다.

    **record_plan으로 계획을 먼저 기록해야 실행됩니다.**

    Args:
        vcpu_min: 최소 vCPU 수.
        mem_min_gib: 최소 메모리(GiB).
        provider: 'aws' | 'azure' | 'gcp' | 'tencent' | 'alibaba' | 'ibm' | 'ncp' |
            'kt' | 'nhn' | 'openstack'. 미지정이면 전체 프로바이더.
        region: 리전 부분 문자열(예: 'us-east', 'ap-northeast'). 미지정이면 전체.
        sort_by: 'cost'(저렴한 순, 기본) | 'vcpu' | 'memory'.
        limit: 반환할 후보 수(기본 5).
        architecture: 'x86_64'(기본) | 'arm64' | 'any'. cb-tumblebug의 라이브 추천과
            같은 기본값이라, 값을 바꾸면 라이브 결과와 달라질 수 있습니다.
    """
    if _needs_plan(ctx):
        print("\n[스펙추천] 계획 없음 → 거부")
        return _PLAN_REQUIRED
    arch = None if architecture.lower() == "any" else architecture
    print(
        f"\n[스펙추천] vcpu>={vcpu_min}, mem>={mem_min_gib}GiB, "
        f"provider={provider or 'any'}, region={region or 'any'}, "
        f"arch={arch or 'any'}, sort={sort_by}"
    )
    return agent_api.recommend_specs(
        vcpu_min, mem_min_gib, provider, region, sort_by, limit,
        architecture=arch, annotate=_perf_annotate,
    )


@function_tool
def cost_estimate_monthly(
    ctx: RunContextWrapper[SessionState],
    hourly_usd: float,
    count: int = 1,
    hours_per_month: float = HOURS_PER_MONTH,
) -> str:
    """시간당 단가로 월 비용을 계산한다(노드 수·가동 시간 반영).

    **record_plan으로 계획을 먼저 기록해야 실행됩니다.**
    단가는 cost_recommend_specs가 준 값을 쓰세요(웹 검색 가격과 섞지 마세요).
    월 비용을 직접 암산하지 말고 반드시 이 도구로 계산하세요.

    Args:
        hourly_usd: 인스턴스 1대의 시간당 USD 단가.
        count: 인스턴스 대수(기본 1).
        hours_per_month: 월 가동 시간(기본 730 = 상시 가동).
    """
    if _needs_plan(ctx):
        print("\n[비용추정] 계획 없음 → 거부")
        return _PLAN_REQUIRED
    total = hourly_usd * hours_per_month * count
    print(
        f"\n[비용추정] ${hourly_usd}/h × {hours_per_month}h × {count}대 "
        f"= ${round(total, 2)}/월"
    )
    return agent_api.estimate_monthly_cost(hourly_usd, count, hours_per_month)


COST_TOOLS = [cost_recommend_specs, cost_estimate_monthly]
