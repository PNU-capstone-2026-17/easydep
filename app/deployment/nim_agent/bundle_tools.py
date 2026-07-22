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
    """리소스 하나를 만들 때 **함께 다뤄야 하는 리소스 군**을 반환한다.

    "VM 만들려면 뭐가 더 필요해?", "이거 하나만 만들면 되나?" 같은 질문에 쓰세요.
    답은 세 층으로 옵니다 — 반드시 함께 만들어지는 것 / 값을 반드시 줘야 하는 것 /
    붙일 수 있는 것.

    **의존 관계 자체**(무엇이 무엇을 참조하나)는 `kb_creation_order`·
    `kb_type_detail`이 답합니다. 이 도구는 "실무에서 무엇과 묶여 다니나"입니다.

    Args:
        resource_type: 리소스 타입. 예: 'azure::Microsoft.Compute/virtualMachines',
            'core::vm', 'aws::AWS::Lambda::Function'.
    """
    print(f"\n[번들질의] 리소스 군: {resource_type!r}")
    return agent_api.resource_bundle(resource_type)


@function_tool
def bundle_describe(name: str) -> str:
    """이름 붙은 리소스 군 하나를 조회한다.

    `sg-default`, `aws-apigateway-lambda`, `avm/res/compute/virtual-machine` 처럼
    소스가 이름을 붙여 둔 묶음입니다. 원본이 단 경고가 있으면 함께 옵니다.

    Args:
        name: 리소스 군 이름.
    """
    print(f"\n[번들질의] 이름 조회: {name!r}")
    return agent_api.describe_named_bundle(name)


@function_tool
def bundle_search(keyword: str = "") -> str:
    """담긴 리소스 군을 키워드로 찾는다. 비우면 목록 앞부분.

    Args:
        keyword: 'web', 'lambda', 'kubernetes' 등.
    """
    print(f"\n[번들질의] 검색: {keyword!r}")
    return agent_api.list_bundles(keyword or None)


BUNDLE_TOOLS = [bundle_for_resource, bundle_describe, bundle_search]
