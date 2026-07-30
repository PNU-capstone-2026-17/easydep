"""폐포(depkb.closure)의 구조적 불변식 — 검증 주장의 소비가 규율을 지키는가.

값의 옳음은 claims.json(과 그 실험들)이 진다. 여기서 지키는 것은 소비 층의
규율이다: 근거 없는 항목 금지 · 필수/선택의 겹침 금지 · CSP별 답의 차이 보존 ·
모르는 것 소비 거부.
"""

from __future__ import annotations

import pytest

from app.core.dependency import closure, describe


def test_azure_vm_needs_the_nic_chain() -> None:
    """azure VM 폐포 = network→subnet→nic 사슬. 순서는 필수 간선의 위상 정렬."""
    c = closure("vm", "azure")
    assert {i.id for i in c.required} == {"nic", "subnet", "network"}
    assert c.createOrder == ("network", "subnet", "nic", "vm")
    assert {a.id for a in c.attachable} == {"disk", "firewall", "publicIp"}
    assert not any(a.autoFilled for a in c.attachable), (
        "azure 선택 자원엔 서버 대체 실측이 없다 — autoFilled는 측정된 대체에만"
    )


def test_aws_vm_needs_nothing_and_that_is_a_measurement() -> None:
    """aws VM 폐포의 필수는 **공집합**이다 — 전부 서버 대체(기본 VPC·default
    SG·ENI 암묵·AMI 루트 볼륨). 버그처럼 보이는 것이 측정 결과다."""
    c = closure("vm", "aws")
    assert c.required == ()
    assert c.createOrder == ("vm",)
    auto = {a.id for a in c.attachable if a.autoFilled}
    assert auto == {"subnet", "firewall", "nic", "disk"}
    assert {a.id for a in c.attachable if not a.autoFilled} == {"sshKey"}, (
        "sshKey는 서버가 채워 주지 않는다 — 붙이려면 사람이 정한다"
    )


def test_gcp_vm_flips_the_modality_and_surfaces_the_condition() -> None:
    """gcp VM 폐포: disk·nic 필수(양상 반전의 소비측), nic→subnet은 조건부
    결정으로 사람에게 올라온다 — 지식이 아니라 배선이 문제였다는 그 자리에
    이제 조건이 실려 간다."""
    c = closure("vm", "gcp")
    assert {i.id for i in c.required} == {"nic", "disk"}
    conds = [d for d in c.decisions if d.kind == "conditional"]
    assert any(d.about == "nic→subnet" for d in conds)


def test_azure_lb_choice_reaches_the_human() -> None:
    """azure LB의 3항 선언 술어는 선택 결정으로 나온다 — 폐포가 대신 고르지
    않는다(어느 것이든 근거 없이 고르면 그건 우리 발명이다)."""
    c = closure("loadBalancer", "azure")
    assert any(d.kind == "choice" for d in c.decisions)


def test_every_required_item_carries_its_claims() -> None:
    """근거 없는 항목 금지 — 모든 필수 항목은 자기를 만든 간선을 들고 다닌다."""
    for csp in ("azure", "aws", "gcp"):
        c = closure("vm", csp)
        for item in c.required:
            assert item.because, f"{csp} {item.id}: 근거가 없다"


def test_required_and_attachable_never_overlap() -> None:
    """필수로 딸려온 것을 선택지로 다시 세지 않는다 — 겹치면 계획서가 같은
    자원을 두 번 말한다(실제로 azure subnet이 겹쳤다가 잡힌 자리)."""
    for csp in ("azure", "aws", "gcp"):
        for anchor in ("vm", "loadBalancer", "nic", "subnet"):
            c = closure(anchor, csp)
            overlap = {i.id for i in c.required} & {a.id for a in c.attachable}
            assert not overlap, f"{csp}/{anchor}: 겹침 {overlap}"


def test_delete_constraints_are_measured_pairs_only() -> None:
    """삭제 제약은 실측된 생명주기 주장만 싣는다 — 총순서를 지어내지 않는다."""
    c = closure("vm", "azure")
    assert ("nic", "subnet") in c.deleteBefore
    assert ("vm", "disk") in c.deleteBefore


def test_unknown_csp_and_anchor_fail_loudly() -> None:
    with pytest.raises(KeyError):
        closure("vm", "ncp")
    with pytest.raises(KeyError):
        closure("quantumComputer", "azure")


def test_describe_renders_for_every_known_cell() -> None:
    """describe는 아는 (앵커×CSP) 전부에서 예외 없이 문단을 낸다."""
    for csp in ("azure", "aws", "gcp"):
        for anchor in ("vm", "nic", "subnet", "network", "loadBalancer"):
            text = describe(anchor, csp)
            assert anchor in text and csp in text
