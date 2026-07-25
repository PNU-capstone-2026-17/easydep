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
    """Return one instance spec's performance profile and **attached hardware**.

    Axes included: sustained CPU · generation (whether previous generation) ·
    clock · EBS · ACU · network bandwidth · NIC count · **GPU model/count · local
    SSD size** · CPU platform. When someone asks what is attached to a specific
    spec, or how much of it is attached, **this tool** answers it, not cap_* —
    cap_* holds allowed values of resource attributes, not the hardware attached
    to an instance.

    e.g. "is t3.medium okay under sustained load?", "which GPU is attached to
    g2-standard-8?", "how much local SSD does a2-ultragpu-1g have?", "what is the
    network bandwidth of Standard_D4s_v5?".

    Args:
        provider: 'aws' | 'azure' | 'gcp' (only these three are included for
            performance).
        spec_name: CSP spec name. e.g. 't3.medium', 'm5.large', 'Standard_D2s_v3'.
    """
    print(f"\n[perf query] profile: {provider} {spec_name}")
    return agent_api.instance_profile(provider, spec_name)


@function_tool
def perf_compare(provider: str, spec_names: list[str]) -> str:
    """Compare instance specs of the **same provider** side by side, axis by axis.

    It does not declare a winner — "faster" depends on the workload (CPU-bound vs
    IO-bound). **Comparison across providers (AWS vs Azure) is not possible**: the
    comparison axes (ACU, clock, etc.) are provider-specific, so there is no
    common baseline. If you get such a request, do not use this tool — answer that
    it cannot be done.

    Args:
        provider: 'aws' | 'azure' | 'gcp'. One provider at a time.
        spec_names: CSP spec names to compare (2 or more). e.g. ['m5.large',
            'm6i.large'].
    """
    print(f"\n[perf query] compare: {provider} {spec_names}")
    return agent_api.compare(provider, spec_names)


@function_tool
def perf_specs_by_ebs_baseline(min_mbps: float, limit: int = 10) -> str:
    """Find AWS specs whose sustained (baseline) EBS bandwidth meets a threshold.

    Trap avoidance: the commonly quoted "maximum bandwidth" is burst and is not
    sustained — this tool looks at baseline only. **AWS only** (Azure uses IOPS
    and GCP is separate, so those are different axes). Check price and size
    afterwards with cost_recommend_specs.

    Args:
        min_mbps: Required sustained EBS bandwidth (Mbps). e.g. 500 MB/s ≈ 4000
            Mbps.
        limit: Number of specs to return (default 10).
    """
    print(f"\n[perf query] EBS baseline >= {min_mbps} Mbps (AWS)")
    return agent_api.specs_meeting_ebs_baseline(min_mbps, limit)


PERF_TOOLS = [perf_instance_profile, perf_compare, perf_specs_by_ebs_baseline]
