"""연계 리소스 군 판정 — 자원 하나를 고르면 무엇이 딸려오나.

과제 문제 ②에 대한 답이라 값이 흔들리면 답이 흔들린다. 여기서 지키는 것은 **절차가
내는 구분**이다: 만들 것 / 고를 것 / 자동으로 채워지는 것 / **사람이 정해야 하는 것**.
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.graphkb.tumblebug_closure import closure, describe


def test_a_vm_brings_five_and_leaves_one_decision() -> None:
    """VM 하나를 요청하면 다섯이 따라오고, 사람이 정할 것은 하나다.

    이 셋(vNet·sshKey·securityGroup)이 증거 다섯 층에서 동시에 관측되는 자리이고,
    **동시에 cb-tumblebug이 알아서 만들어 주는 자리**이기도 하다. 딸려오는 것을 세는
    것만으로는 "그래서 내가 뭘 정해야 하나"에 답하지 못한다 — 그 구분이 요점이다.
    """
    c = closure("node", "aws")
    assert {x.id for x in c.created} == {
        "infra", "vNet", "subnet", "securityGroup", "sshKey"
    }
    assert set(c.chosen) == {"spec", "image"}, "스펙·이미지는 만드는 것이 아니라 고르는 것"
    assert {x.id for x in c.decisions} == {"infra"}


def test_kubernetes_on_aws_needs_two_subnets() -> None:
    """aws에서만 서브넷이 둘이다 — 값이 우리 추정이 아니라 자산 표에서 온다."""
    assert next(x for x in closure("k8sCluster", "aws").created
                if x.id == "subnet").count == 2
    assert next(x for x in closure("k8sCluster", "azure").created
                if x.id == "subnet").count == 1


def test_the_same_resource_differs_by_provider() -> None:
    """`sqlDb`는 aws에서 vNet+서브넷 둘을 끌고 오고 azure에서는 아무것도 안 끌고 온다.

    벤더 중립 판정만 봤으면 둘 다 빈손이었다(cb-tumblebug 스키마에 `required`가
    없다). CSP 조건표가 중립 판정을 덮기 때문에 aws 쪽이 산다 — 이 테스트가 그
    덮어쓰기를 지킨다.
    """
    on_aws = closure("sqlDb", "aws")
    assert {x.id for x in on_aws.created} == {"vNet", "subnet"}
    assert next(x for x in on_aws.created if x.id == "subnet").count == 2

    assert not closure("sqlDb", "azure").created, (
        "azure의 sqlDb는 resourceGroup만 요구한다 — 네트워크를 끌고 오지 않는다"
    )


def test_azure_vpn_pulls_in_its_gateway_subnet() -> None:
    """azure의 VPN만 서브넷을 끌고 온다(전용 GatewaySubnet).

    코드 스키마에는 없고 `assets/networkinfo.yaml`에만 있는 사실이라, 자산 데이터를
    증거층으로 세우지 않았으면 이 간선이 폐포에서 통째로 빠졌다.
    """
    assert "subnet" in {x.id for x in closure("vpn", "azure").created}
    assert "subnet" not in {x.id for x in closure("vpn", "aws").created}


def test_object_storage_waits_for_nothing() -> None:
    """빈 결과도 결과다 — 아무것도 안 기다리고 만들 수 있는 유일한 실물 자원."""
    c = closure("objectStorage", "aws")
    assert not c.created and not c.chosen
    assert "nothing comes with it" in describe("objectStorage", "aws")


def test_provider_conditionality_is_visible_in_the_reason(data_free=None) -> None:
    """왜 딸려왔는지가 CSP에 따라 달라진다.

    aws에서는 securityGroup이 vNet을 요구하지만 azure에서는 아니다(`some CSPs …
    don't bind SG to VPC`). 근거 목록에 그 차이가 그대로 나타나야, 나중에
    "왜 이게 필요하지"를 되짚을 수 있다.
    """
    on_aws = next(x for x in closure("node", "aws").created if x.id == "vNet")
    on_azure = next(x for x in closure("node", "azure").created if x.id == "vNet")
    assert "securityGroup" in on_aws.because
    assert "securityGroup" not in on_azure.because


def test_unknown_resource_says_so() -> None:
    with pytest.raises(KeyError):
        closure("relationalDatabase", "aws")
