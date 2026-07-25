"""사이징 도구.

**이 축의 답은 대부분 "모른다"여야 정상이다.** 담긴 것은 원본이 공식으로 적어 둔
변환 규칙뿐이고, "동시 사용자 N명 → vCPU M"은 소스가 없어 담지 않았다.

에이전트가 규모를 **직접 추론**하던 자리(KB 근거 0인 구간)를 좁히는 것이 목적이다.
"""

from __future__ import annotations

from agents import function_tool

from app.deployment.sizingkb import agent_api


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
    print(f"\n[sizing query] subnet capacity: /{prefix_length} {provider}")
    return agent_api.subnet_capacity(prefix_length, provider)


# **도구 셋을 하나로 접었다(2026-07-25).** 셋 다 "이 워크로드에 무엇이 필요한가"에
# 답하는데 모델이 매번 갈라야 했다. 컨테이너 프리셋은 scope와 무관하게 270 tok으로
# 고정이라 **scope를 밝힌 질문에는 붙이지 않는다** — K8s 최소사양을 물었는데
# 프리셋 7종이 딸려오면 그건 답이 아니라 소음이다.
@function_tool
def sizing_rules(scope: str = "") -> str:
    """Sizing rules this source states — minimums, required counts, and the
    per-workload examples.

    **Most answers on this axis should be "we do not know", and that is
    correct.** Only conversion rules the source wrote down are stored; "N
    concurrent users → M vCPU" has no source and is deliberately absent. Do not
    fill that gap yourself.

    - **`scope` given** → the minimums and required counts for it (e.g.
      `'k8s-node'` for the node floor, `'aws'` for that provider's rules), plus
      any workload reference points matching it.

    **"How many X do I need?" is answered here**, not by the dependency or
    limits axes — e.g. "how many subnets does a Kubernetes cluster need?"
    (`scope='aws'` → `requiredSubnetCount: 2`). A schema says whether a field is
    required; it does not say **how many**, and answering from the schema alone
    produces "optional, one", which is wrong for a cluster.
    - **`scope` omitted** → every stored rule, plus the container size presets
      (nano~2xlarge).

    ⚠️ Reference points are **examples, not correct answers.** The source does
    not say "t3.small is the right web server"; it says "this source's web
    server example is t3.small". Pass them on as reference points; do not
    recommend them as-is. The presets likewise carry the source's own "not for
    production" sentence — **do not strip it**, and note they are container
    sizes, not instance sizes.

    Args:
        scope: 'k8s-node' | 'aws' | 'azure' | 'web' | 'llm' | 'gpu', etc.
            Omit to see everything.
    """
    print(f"\n[sizing query] rules: {scope!r}")
    parts = [
        agent_api.requirements(scope or None),
        agent_api.reference_points(scope or None),
    ]
    if not scope:
        parts.append(agent_api.container_presets())
    return "\n\n".join(parts)


SIZING_TOOLS = [
    sizing_subnet_capacity,
    sizing_rules,
]
