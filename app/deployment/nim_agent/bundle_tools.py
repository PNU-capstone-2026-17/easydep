"""리소스 군(번들) 도구.

`research.md` 문제 2의 *"특정 리소스를 선택하는 경우 연계되는 다양한 리소스 군을
획득할 수 있어야"*에 답하는 자리다.

**graphkb와 무엇이 다른가**: 저쪽은 스키마 참조를 따라가 "가능한 것"을 전부 준다
(`EC2::Instance`에서 `KMS::ReplicaKey`까지). 이쪽은 **실제로 함께 쓰이는 것**을
등급과 빈도로 가른다.
"""

from __future__ import annotations

from agents import function_tool

from app.deployment.bundlekb import agent_api, dataset


def _is_named_bundle(query: str) -> bool:
    """이 질의가 **번들 이름**인가(`sg-default` 등).

    산문을 다시 파싱하지 않는다 — `find_bundle`이 `Bundle | None`을 돌려주므로
    구조로 판별한다. 오늘 저장소 전수 조사에서 "렌더링된 문장을 코드가 다시
    매칭하는 자리"가 4곳 나왔고 그중 하나는 번역만으로 조용히 0을 세고 있었다.
    같은 것을 새로 만들지 않는다.
    """
    return dataset.find_bundle(query) is not None


def _is_resource_type(query: str) -> bool:
    """이 질의가 **리소스 타입**인가. 접두어가 없어도 데이터가 아는 이름이면 참."""
    return bool(dataset.bundles_for(query) or dataset.companions_of(query))


# **도구 셋을 하나로 접었다(2026-07-25).** 셋의 차이는 "질의가 무엇이냐"뿐이었는데
# 그건 **도구가 판별할 수 있는 것**이지 모델에게 고르게 할 일이 아니다. 실제로
# SM2에서 모델이 검색 도구를 9번 연속 두드리며 헤맨 적이 있다.
@function_tool
def bundle_lookup(query: str) -> str:
    """What actually gets created together with a resource, in practice.

    Answers `research.md` problem 2: pick one resource, get the group of
    resources that comes with it. Pass whatever you have — the tool works out
    which kind of query it is:

    - **a resource type** (`core::vm`, `AWS::EC2::Instance`,
      `Microsoft.Compute/virtualMachines`, with or without the provider prefix)
      → the group that travels with it, in three tiers: always together / you
      must supply a value / optional attachment. **Do not flatten the tiers.**
      A "ratio at which they co-occurred in real templates" is a **corpus
      statistic** — never carry it over as if the cloud enforced it.
    - **a named group** (`sg-default`, `aws-apigateway-lambda`,
      `avm/res/compute/virtual-machine`) → that group, and any warning the
      source itself attached to it comes with it.
    - **anything else** → treated as a keyword search over the stored groups.
      An empty query lists the head of the catalogue.

    **The dependency relationships themselves** (what references what) are
    answered by `kb_creation_order` and `kb_describe_type`; this tool answers
    what is bundled with what in practice.

    Args:
        query: A resource type, a group name, or a keyword.
    """
    if _is_named_bundle(query):
        print(f"\n[bundle query] named group: {query!r}")
        return agent_api.describe_named_bundle(query)
    if _is_resource_type(query):
        print(f"\n[bundle query] resource group: {query!r}")
        return agent_api.resource_bundle(query)
    print(f"\n[bundle query] keyword search: {query!r}")
    return agent_api.list_bundles(query or None)


BUNDLE_TOOLS = [bundle_lookup]
