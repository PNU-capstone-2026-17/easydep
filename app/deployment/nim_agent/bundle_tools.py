"""리소스 군(번들) 도구.

`research.md` 문제 2의 *"특정 리소스를 선택하는 경우 연계되는 다양한 리소스 군을
획득할 수 있어야"*에 답하는 자리다.

**graphkb와 무엇이 다른가**: 저쪽은 스키마 참조를 따라가 "가능한 것"을 전부 준다
(`EC2::Instance`에서 `KMS::ReplicaKey`까지). 이쪽은 **실제로 함께 쓰이는 것**을
등급과 빈도로 가른다.
"""

from __future__ import annotations

from agents import function_tool

from bundlekb import agent_api


@function_tool
def bundle_for_resource(resource_type: str) -> str:
    """Return the **resource group** that must be handled together with one resource.

    Use this for questions like "what else do I need to create a VM?" or "is
    creating just this one enough?". The answer comes in three tiers — always
    together / you must supply a value / optional attachment.

    **The dependency relationships themselves** (what references what) are
    answered by `kb_creation_order` and `kb_type_detail`. This tool answers
    "what travels bundled with what in practice".

    Args:
        resource_type: Resource type. e.g.
            'azure::Microsoft.Compute/virtualMachines', 'core::vm',
            'aws::AWS::Lambda::Function'.
    """
    print(f"\n[번들질의] 리소스 군: {resource_type!r}")
    return agent_api.resource_bundle(resource_type)


@function_tool
def bundle_describe(name: str) -> str:
    """Look up one named resource group.

    These are bundles the source itself gave a name to, such as `sg-default`,
    `aws-apigateway-lambda`, or `avm/res/compute/virtual-machine`. If the
    original carries a warning, it comes with the result.

    Args:
        name: Resource group name.
    """
    print(f"\n[번들질의] 이름 조회: {name!r}")
    return agent_api.describe_named_bundle(name)


@function_tool
def bundle_search(keyword: str = "") -> str:
    """Search the stored resource groups by keyword. Empty gives the list head.

    Args:
        keyword: 'web', 'lambda', 'kubernetes', etc.
    """
    print(f"\n[번들질의] 검색: {keyword!r}")
    return agent_api.list_bundles(keyword or None)


BUNDLE_TOOLS = [bundle_for_resource, bundle_describe, bundle_search]
