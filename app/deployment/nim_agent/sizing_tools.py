"""사이징 도구.

**이 축의 답은 대부분 "모른다"여야 정상이다.** 담긴 것은 원본이 공식으로 적어 둔
변환 규칙뿐이고, "동시 사용자 N명 → vCPU M"은 소스가 없어 담지 않았다.

에이전트가 규모를 **직접 추론**하던 자리(KB 근거 0인 구간)를 좁히는 것이 목적이다.
"""

from __future__ import annotations

from agents import function_tool

from sizingkb import agent_api


# 예약 수를 0으로 두면 251대 자리에 256대가 됩니다.
@function_tool
def sizing_subnet_capacity(prefix_length: int, provider: str) -> str:
    """Compute **how many machines fit** in a subnet CIDR.

    Use this for questions like "how many in a /24?" or "how large should I make
    the subnet?". It computes `2^(32−prefix) − reserved IPs`, and for a provider
    whose reserved count is unknown it **does not compute and answers that it
    does not know**.

    Args:
        prefix_length: Prefix length. e.g. 24 (= /24).
        provider: 'aws' | 'azure' | 'gcp' | 'alibaba' | 'ibm', etc.
    """
    print(f"\n[사이징질의] 서브넷 용량: /{prefix_length} {provider}")
    return agent_api.subnet_capacity(prefix_length, provider)


@function_tool
def sizing_requirements(scope: str = "") -> str:
    """Look up minimum requirements and required counts.

    For questions like "what is the minimum for a K8s node?" or "how many
    subnets do I need for a cluster?". Empty gives every stored requirement.

    Args:
        scope: 'k8s-node' | 'aws' | 'azure', etc.
    """
    print(f"\n[사이징질의] 요구사항: {scope!r}")
    return agent_api.requirements(scope or None)


@function_tool
def sizing_reference_points(keyword: str = "") -> str:
    """Look up per-workload spec **reference points**.

    ⚠️ **These are examples, not correct answers.** It does not say "t3.small is
    the right web server"; it says "this source's web server example is
    t3.small". Do not recommend it as-is — pass it on as a reference point.

    Args:
        keyword: 'web' | 'llm' | 'gpu', etc.
    """
    print(f"\n[사이징질의] 참조점: {keyword!r}")
    return agent_api.reference_points(keyword or None)


@function_tool
def sizing_container_presets() -> str:
    """Return the CPU and memory of the container size presets (nano~2xlarge).

    The source states that these are "not for production", and that sentence
    comes with the result — **do not strip it.** These are container sizes, not
    instance sizes.
    """
    print("\n[사이징질의] 컨테이너 프리셋")
    return agent_api.container_presets()


SIZING_TOOLS = [
    sizing_subnet_capacity,
    sizing_requirements,
    sizing_reference_points,
    sizing_container_presets,
]
